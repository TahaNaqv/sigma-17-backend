"""UPR earning-method registry and per-line-of-business policy resolution.

Replaces three copy-pasted, string-matched, unreachable branches in ``calculate_upr``
(docs/UPR_METHOD_SELECTION_PLAN.md §1.1-1.2) with a named registry the actuary controls.

Two design rules carry the whole module:

**1. Eligibility is separate from earning.** The historic code expressed eligibility —
"the policy must have been issued by the valuation date" — implicitly, via
``np.select(..., default=0)``: every condition carried ``ISSUEDATE <= date`` and a row
matching none of them fell to zero. A registry that folds that test into each method
cannot reproduce today's output and would grant UPR to policies not yet issued. So
``unearned_fraction`` applies eligibility once, around whatever method resolved.

That separation is what makes the default provably safe: a pro-rata-everywhere policy
reproduces all three historic blocks across 14,791 rows x 12 valuation dates with
``max |diff| = 0.000e+00`` (plan §1.3).

**2. Matching is normalised, never literal.** Exact-literal matching is the direct cause
of the dead branches: the code looked for ``"Contractors All Risks"`` while the data holds
``"CONTRACTORS'ALL RISK"``, and tested ``POLICYCLASS == "Marine cargo"`` while marine lives
in ``PRODUCTTYPE``. Every comparison here goes through :func:`normalize_token`.

Term-based methods (``eighths``, ``twenty_fourths``) are a special hazard: they weight by
issue date alone and ignore the risk period entirely, so they do NOT self-gate on expiry
the way the duration-based methods do. On the client reference book they produce -243% and
-429% respectively. See :mod:`module1_engine.upr_guard`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# Normalised matching
# --------------------------------------------------------------------------- #

_PUNCT = re.compile(r"[^a-z0-9 ]+")


def normalize_token(value: Any) -> str:
    """Casefold, strip punctuation, collapse whitespace.

    ``"CONTRACTORS'ALL RISK"`` and ``"Contractors All Risks"`` do NOT collapse to the same
    token (one is singular), which is why rules support ``contains`` / ``prefix`` matching
    rather than relying on normalisation alone.
    """
    if value is None:
        return ""
    text = str(value).strip().casefold()
    text = _PUNCT.sub(" ", text)
    return " ".join(text.split())


MATCH_EXACT = "exact"
MATCH_CONTAINS = "contains"
MATCH_PREFIX = "prefix"
MATCH_MODES = (MATCH_EXACT, MATCH_CONTAINS, MATCH_PREFIX)


def token_matches(haystack: str, needle: str, mode: str) -> bool:
    if not needle:
        return True
    if mode == MATCH_CONTAINS:
        return needle in haystack
    if mode == MATCH_PREFIX:
        return haystack.startswith(needle)
    return haystack == needle


# --------------------------------------------------------------------------- #
# The methods
# --------------------------------------------------------------------------- #

PRO_RATA_DAILY = "pro_rata_daily"
SUM_OF_DIGITS = "sum_of_digits"
FULL_PREMIUM_IN_PERIOD = "full_premium_in_period"
EIGHTHS = "eighths"
TWENTY_FOURTHS = "twenty_fourths"
FLAT_PERCENTAGE = "flat_percentage"


def _duration_safe(df: pd.DataFrame) -> pd.Series:
    """Denominator that never divides by zero. Same-day policies have Duration 0."""
    return np.maximum(df["Duration"], 1)


def _pro_rata_daily(df: pd.DataFrame, at_date: pd.Timestamp, params: Mapping) -> pd.Series:
    """365ths / time apportionment. Self-gates on expiry: once RiskEnd < at_date the
    numerator clamps to 0."""
    remaining = (df["RiskEndDate"] - at_date).dt.days
    return np.maximum(0, np.minimum(df["Duration"], remaining)) / _duration_safe(df)


def _sum_of_digits(df: pd.DataFrame, at_date: pd.Timestamp, params: Mapping) -> pd.Series:
    """Increasing-risk basis (contractors'/erection all risks). Also self-gates: once
    elapsed reaches Duration the expression is 1 - 1 = 0."""
    elapsed = (at_date - df["RiskStartDate"]).dt.days + 1
    capped = np.minimum(np.maximum(elapsed, 0), df["Duration"])
    return 1 - ((capped ** 2) / (_duration_safe(df) ** 2))


def _full_premium_in_period(
    df: pd.DataFrame, at_date: pd.Timestamp, params: Mapping
) -> pd.Series:
    """Marine-cargo style: the whole premium is unearned while the policy is inside the
    lookback window, nothing after.

    ``lookback_months`` defaults to 3 — a calendar quarter. The historic code disagreed
    with itself here (``DateOffset(months=3)`` in two blocks, ``Timedelta(days=91)`` in the
    third, which differ in three quarters out of four); the calendar reading wins.
    """
    months = int(params.get("lookback_months", 3) or 3)
    window_start = at_date - pd.DateOffset(months=months)
    return (df["ISSUEDATE"] > window_start).astype(float)


def _periods_since_issue(df: pd.DataFrame, at_date: pd.Timestamp, freq: str) -> pd.Series:
    issued = df["ISSUEDATE"].dt.to_period(freq)
    at = pd.Period(at_date, freq=freq)
    return (at - issued).apply(lambda x: x.n if x is not pd.NaT else np.nan).clip(lower=0)


def _eighths(df: pd.DataFrame, at_date: pd.Timestamp, params: Mapping) -> pd.Series:
    """1/8ths — assumes uniform issuance through a quarter and a fixed term.

    Weights by ISSUE QUARTER only; the risk period is not consulted, so this does NOT
    self-gate on expiry. Guarded by upr_guard.
    """
    k = _periods_since_issue(df, at_date, "Q")
    return np.clip((7 - 2 * k) / 8, 0.0, 1.0)


def _twenty_fourths(df: pd.DataFrame, at_date: pd.Timestamp, params: Mapping) -> pd.Series:
    """1/24ths — the monthly analogue of :func:`_eighths`, with the same hazard."""
    m = _periods_since_issue(df, at_date, "M")
    return np.clip((23 - 2 * m) / 24, 0.0, 1.0)


def _flat_percentage(df: pd.DataFrame, at_date: pd.Timestamp, params: Mapping) -> pd.Series:
    """A constant unearned fraction, for treaty or regulatory bases that prescribe one."""
    pct = float(params.get("percent", 0.0) or 0.0)
    return pd.Series(pct, index=df.index, dtype=float)


@dataclass(frozen=True)
class UprMethod:
    key: str
    label: str
    description: str
    fn: Callable[[pd.DataFrame, pd.Timestamp, Mapping], pd.Series]
    #: True when the formula reaches zero of its own accord once the policy expires.
    #: False means the method weights by issue date alone and needs a suitability guard.
    self_gates_on_expiry: bool
    params_schema: tuple[str, ...] = ()


METHODS: dict[str, UprMethod] = {
    m.key: m
    for m in (
        UprMethod(
            PRO_RATA_DAILY, "Pro-rata (365ths)",
            "Straight time apportionment over the risk period.",
            _pro_rata_daily, True,
        ),
        UprMethod(
            SUM_OF_DIGITS, "Sum of digits (increasing risk)",
            "Risk-attaching basis for contractors' / erection all-risks business, where "
            "exposure rises through the policy term.",
            _sum_of_digits, True,
        ),
        UprMethod(
            FULL_PREMIUM_IN_PERIOD, "Full premium in period",
            "The whole premium is unearned while the policy sits inside the lookback "
            "window; nothing after. Marine cargo convention.",
            _full_premium_in_period, True, ("lookback_months",),
        ),
        UprMethod(
            EIGHTHS, "Eighths (1/8ths)",
            "Assumes uniform issuance through each quarter and a fixed term. Ignores the "
            "risk period, so it is only valid on a homogeneous book.",
            _eighths, False, ("term_months",),
        ),
        UprMethod(
            TWENTY_FOURTHS, "Twenty-fourths (1/24ths)",
            "Monthly analogue of eighths, with the same homogeneity requirement.",
            _twenty_fourths, False, ("term_months",),
        ),
        UprMethod(
            FLAT_PERCENTAGE, "Flat percentage",
            "A constant unearned fraction, for treaty or regulatory bases that prescribe one.",
            _flat_percentage, True, ("percent",),
        ),
    )
}

METHOD_KEYS: tuple[str, ...] = tuple(METHODS)
#: Methods that weight by issue date alone and therefore need a book-suitability check.
UNGATED_METHODS: tuple[str, ...] = tuple(
    k for k, m in METHODS.items() if not m.self_gates_on_expiry
)


# --------------------------------------------------------------------------- #
# Policy resolution
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class UprRule:
    """One rule. Blank ``reserving_class`` matches every class; blank ``product_type``
    makes it the class-level default."""

    method: str
    reserving_class: str = ""
    product_type: str = ""
    match_mode: str = MATCH_EXACT
    params: Mapping[str, Any] = field(default_factory=dict)
    priority: int = 0

    def __post_init__(self) -> None:
        if self.method not in METHODS:
            raise ValueError(
                f"Unknown UPR method {self.method!r}; expected one of {METHOD_KEYS}."
            )
        if self.match_mode not in MATCH_MODES:
            raise ValueError(
                f"Unknown match mode {self.match_mode!r}; expected one of {MATCH_MODES}."
            )

    @property
    def specificity(self) -> int:
        """Higher wins. A product rule beats a class rule beats the catch-all."""
        return (2 if self.product_type else 0) + (1 if self.reserving_class else 0)


#: The shipped default. Proven bit-identical to the historic behaviour (plan §1.3).
DEFAULT_RULES: tuple[UprRule, ...] = (UprRule(method=PRO_RATA_DAILY),)


@dataclass(frozen=True)
class UprPolicy:
    rules: tuple[UprRule, ...] = DEFAULT_RULES

    @classmethod
    def from_dicts(cls, raw: Iterable[Mapping[str, Any]] | None) -> "UprPolicy":
        """Build from plain dicts. No Django types cross the engine boundary."""
        if not raw:
            return cls()
        rules = tuple(
            UprRule(
                method=str(r["method"]),
                reserving_class=str(r.get("reserving_class") or ""),
                product_type=str(r.get("product_type") or ""),
                match_mode=str(r.get("match_mode") or MATCH_EXACT),
                params=dict(r.get("params") or {}),
                priority=int(r.get("priority") or 0),
            )
            for r in raw
        )
        return cls(rules=rules or DEFAULT_RULES)

    def resolve(self, reserving_class: Any, product_type: Any) -> UprRule:
        """Most specific matching rule; falls back to pro-rata when nothing matches."""
        rc = normalize_token(reserving_class)
        pt = normalize_token(product_type)
        best: UprRule | None = None
        for rule in self.rules:
            if rule.reserving_class and not token_matches(
                rc, normalize_token(rule.reserving_class), rule.match_mode
            ):
                continue
            if rule.product_type and not token_matches(
                pt, normalize_token(rule.product_type), rule.match_mode
            ):
                continue
            if best is None or (rule.specificity, rule.priority) > (
                best.specificity, best.priority
            ):
                best = rule
        return best or UprRule(method=PRO_RATA_DAILY)

    @property
    def is_default(self) -> bool:
        return self.rules == DEFAULT_RULES

    def methods_used(self) -> set[str]:
        return {r.method for r in self.rules}


def _row_assignments(
    df: pd.DataFrame, policy: UprPolicy
) -> tuple[np.ndarray, list[UprRule]]:
    """Per-row rule assignment as ``(indices, rules)``.

    Indices rather than rule objects because a rule carries a ``params`` mapping and is
    therefore unhashable; grouping on integers also keeps the hot path cheap. Resolution
    runs once per distinct ``(RESERVINGCLASS, PRODUCTTYPE)`` pair — the reference book has
    ~14,800 rows across a few dozen pairs.
    """
    products = (
        df["PRODUCTTYPE"] if "PRODUCTTYPE" in df.columns
        else pd.Series("", index=df.index)
    )
    rules: list[UprRule] = []
    seen: dict[int, int] = {}
    cache: dict[tuple[str, str], int] = {}
    indices = np.empty(len(df), dtype=int)

    for i, (rc, pt) in enumerate(zip(df["RESERVINGCLASS"], products)):
        ck = (normalize_token(rc), normalize_token(pt))
        idx = cache.get(ck)
        if idx is None:
            rule = policy.resolve(rc, pt)
            idx = seen.get(id(rule))
            if idx is None:
                idx = len(rules)
                rules.append(rule)
                seen[id(rule)] = idx
            cache[ck] = idx
        indices[i] = idx
    return indices, rules


def unearned_fraction(
    df: pd.DataFrame,
    at_date: pd.Timestamp,
    policy: UprPolicy | None = None,
) -> np.ndarray:
    """Unearned fraction in [0, 1] per row at ``at_date``.

    Eligibility and earning are applied separately (see module docstring): a policy issued
    after ``at_date`` contributes nothing regardless of method, reproducing the historic
    ``np.select(..., default=0)`` behaviour exactly.
    """
    policy = policy or UprPolicy()
    eligible = (df["ISSUEDATE"] <= at_date).to_numpy()

    indices, rules = _row_assignments(df, policy)
    frac = np.zeros(len(df), dtype=float)
    for i, rule in enumerate(rules):
        mask = indices == i
        if not mask.any():
            continue
        subset = df[mask]
        values = METHODS[rule.method].fn(subset, at_date, rule.params)
        frac[mask] = np.asarray(values, dtype=float)

    # An unearned fraction outside [0, 1] is meaningless. On well-formed data every
    # method already lands inside it, so this clamp is a no-op on the reference book
    # (asserted by the bit-identity test) — but a row with RiskEndDate before
    # RiskStartDate drives `sum_of_digits` to roughly -132,000, which would flow
    # straight into UPR as a vast negative. That is a latent defect inherited from the
    # historic formula; it never fired because no reference row is malformed, so no
    # golden could catch it. Clamping is the cheapest place to make bad input harmless.
    frac = np.clip(np.nan_to_num(frac, nan=0.0, posinf=0.0, neginf=0.0), 0.0, 1.0)
    return np.where(eligible, frac, 0.0)
