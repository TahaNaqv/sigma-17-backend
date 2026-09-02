"""Book-suitability guard for term-based UPR methods.

``eighths`` and ``twenty_fourths`` weight premium by ISSUE DATE alone and never consult the
risk period. That makes them valid only on a homogeneous book of in-force policies with a
uniform term — and catastrophic otherwise.

Measured on the client reference book (docs/UPR_METHOD_SELECTION_PLAN.md §1.5):

===================  ==============  ==========
method               UPR at EOP      vs today
===================  ==============  ==========
pro_rata_daily        224,367,539         --
eighths              -321,788,936     -243.4%
twenty_fourths       -738,254,910     -429.0%
===================  ==============  ==========

The cause is not expiry — adding an in-force gate moves it by 0.1pp. It is that the book
carries **699 rows (4.7%) of negative premium totalling -3.16bn** (cancellations and
mid-term endorsements) which pro-rata correctly weights at ~0.109 because their risk period
has run off, and which eighths weights at ~0.426 because it only looks at when they were
issued.

The trap is that the book looks suitable: 92.8% of policies run an annual term. A method
that appears applicable and is not is more dangerous than one that obviously is not, so
selecting a term-based method runs these checks and blocks on the ones that matter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from module1_engine.upr_methods import UNGATED_METHODS

#: Above this share of negative-premium rows a term-based method is unusable.
MAX_NEGATIVE_PREMIUM_SHARE = 0.01
#: Above this share of rows outside the declared term the homogeneity assumption fails.
MAX_OFF_TERM_SHARE = 0.10
#: Above this share of already-expired rows the result is misleading but not fatal.
WARN_EXPIRED_SHARE = 0.05
#: Tolerance around the declared term, as a fraction.
TERM_TOLERANCE = 0.10

BLOCK = "block"
WARN = "warn"
OK = "ok"


@dataclass
class GuardCheck:
    key: str
    label: str
    level: str
    detail: str
    value: float | None = None
    threshold: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "level": self.level,
            "detail": self.detail,
            "value": self.value,
            "threshold": self.threshold,
        }


@dataclass
class GuardReport:
    method: str
    rows_examined: int
    checks: list[GuardCheck] = field(default_factory=list)

    @property
    def level(self) -> str:
        levels = {c.level for c in self.checks}
        if BLOCK in levels:
            return BLOCK
        if WARN in levels:
            return WARN
        return OK

    @property
    def blocked(self) -> bool:
        return self.level == BLOCK

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "rows_examined": self.rows_examined,
            "level": self.level,
            "blocked": self.blocked,
            "checks": [c.to_dict() for c in self.checks],
        }


def _share(mask: np.ndarray) -> float:
    return float(mask.mean()) if mask.size else 0.0


def evaluate_book(
    df: pd.DataFrame,
    method: str,
    *,
    at_date: pd.Timestamp | None = None,
    term_months: int = 12,
) -> GuardReport:
    """Assess whether ``df`` is a suitable book for a term-based ``method``.

    Returns a report even for self-gating methods (level ``ok``, no checks) so callers can
    treat every method uniformly.
    """
    report = GuardReport(method=method, rows_examined=int(len(df)))
    if method not in UNGATED_METHODS or df.empty:
        return report

    premium = pd.to_numeric(df.get("PREMIUMAMOUNT"), errors="coerce").fillna(0.0).to_numpy()
    duration = pd.to_numeric(df.get("Duration"), errors="coerce").to_numpy(dtype=float)

    # --- negative premium ---------------------------------------------------
    neg = premium < 0
    neg_share = _share(neg)
    neg_total = float(premium[neg].sum()) if neg.any() else 0.0
    report.checks.append(
        GuardCheck(
            key="negative_premium",
            label="Negative-premium rows",
            level=BLOCK if neg_share > MAX_NEGATIVE_PREMIUM_SHARE else OK,
            detail=(
                f"{int(neg.sum()):,} of {len(df):,} rows ({neg_share:.1%}) carry negative "
                f"premium totalling {neg_total:,.0f}. This method weights by issue date "
                f"alone, so cancellations and mid-term endorsements whose risk period has "
                f"run off are given full weight."
            ),
            value=neg_share,
            threshold=MAX_NEGATIVE_PREMIUM_SHARE,
        )
    )

    # --- term homogeneity ---------------------------------------------------
    expected_days = term_months * 30.4375
    lo, hi = expected_days * (1 - TERM_TOLERANCE), expected_days * (1 + TERM_TOLERANCE)
    known = ~np.isnan(duration)
    off = known & ((duration < lo) | (duration > hi))
    off_share = _share(off)
    report.checks.append(
        GuardCheck(
            key="term_homogeneity",
            label=f"Policies outside a {term_months}-month term",
            level=BLOCK if off_share > MAX_OFF_TERM_SHARE else OK,
            detail=(
                f"{int(off.sum()):,} of {len(df):,} rows ({off_share:.1%}) run a term "
                f"outside {lo:.0f}-{hi:.0f} days. This method assumes a uniform term."
            ),
            value=off_share,
            threshold=MAX_OFF_TERM_SHARE,
        )
    )

    # --- already expired ----------------------------------------------------
    if at_date is not None and "RiskEndDate" in df.columns:
        expired = (df["RiskEndDate"] < at_date).fillna(False).to_numpy()
        exp_share = _share(expired)
        report.checks.append(
            GuardCheck(
                key="expired",
                label="Policies already expired at the valuation date",
                level=WARN if exp_share > WARN_EXPIRED_SHARE else OK,
                detail=(
                    f"{int(expired.sum()):,} of {len(df):,} rows ({exp_share:.1%}) had "
                    f"expired by {pd.Timestamp(at_date).date()}. This method does not "
                    f"reach zero on expiry of its own accord."
                ),
                value=exp_share,
                threshold=WARN_EXPIRED_SHARE,
            )
        )

    return report


def evaluate_policy(
    df: pd.DataFrame,
    policy,
    *,
    at_date: pd.Timestamp | None = None,
) -> list[GuardReport]:
    """Run the guard for every term-based method the policy uses, against the rows that
    method would actually apply to — a method confined to one clean class must not be
    blocked by the rest of the book."""
    from module1_engine.upr_methods import normalize_token

    reports: list[GuardReport] = []
    for rule in policy.rules:
        if rule.method not in UNGATED_METHODS:
            continue
        mask = np.ones(len(df), dtype=bool)
        if rule.reserving_class:
            from module1_engine.upr_methods import token_matches

            needle = normalize_token(rule.reserving_class)
            mask &= df["RESERVINGCLASS"].map(
                lambda v: token_matches(normalize_token(v), needle, rule.match_mode)
            ).to_numpy()
        if rule.product_type and "PRODUCTTYPE" in df.columns:
            from module1_engine.upr_methods import token_matches

            needle = normalize_token(rule.product_type)
            mask &= df["PRODUCTTYPE"].map(
                lambda v: token_matches(normalize_token(v), needle, rule.match_mode)
            ).to_numpy()
        reports.append(
            evaluate_book(
                df[mask],
                rule.method,
                at_date=at_date,
                term_months=int(rule.params.get("term_months", 12) or 12),
            )
        )
    return reports
