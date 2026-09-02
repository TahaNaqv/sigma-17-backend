"""Input pre-flight for Module 1 reserving runs (WP0).

The engine drives its reserve loop from the **premium** frame and matches claims by exact
string equality on ``RESERVINGCLASS``. Nothing raises when a value fails to match:

* a claims class absent from premium has every one of its rows **silently discarded**;
* a premium class absent from the paid file produces a workbook with a **zero paid triangle**,
  which develops to a reserve built on outstanding alone.

On the reference book that is the whole of ``Health`` — the premium file spells it ``Health
Insurance`` — **3,044 paid rows worth 35,503,674**, in the largest class of the book. The run
completes, the workbook looks plausible, and the number is wrong. That is the failure this
module exists to make impossible.

Pure functions over dataframes: no Django, no I/O, no engine import. The same report is
served by the pre-submit endpoint and enforced by the Celery task, so what a user is shown
before a run is exactly what gates it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import pandas as pd

from core.normalize import canonical_key, suggest_matches

CLASS_COLUMN = "RESERVINGCLASS"

SEVERITY_OK = "ok"
SEVERITY_WARN = "warn"
SEVERITY_ERROR = "error"
_SEVERITY_ORDER = {SEVERITY_OK: 0, SEVERITY_WARN: 1, SEVERITY_ERROR: 2}

#: Heads of damage whose paid amount is a recovery rather than a payment. Mirrors the
#: substitution `import_data` performs, so `dropped_amount` reports the value the ENGINE would
#: have consumed rather than a raw column sum.
RECOVERY_HEADS = frozenset(
    {
        "TP - Morror Recovery",
        "TP - Insurance Recovery(RASEED)",
        "TP - Right of Recovery",
        "Right of Recovery",
        "Salvage",
        "Recovery",
        "OD - Salvage / Scrap",
        "OD – Right of Recovery",
        "OD - Salvage / Client",
        "OD - Reversal of Total Loss",
        "Subrogation",
    }
)


@dataclass(frozen=True)
class PreflightMessage:
    code: str
    severity: str
    text: str
    #: Machine-readable payload for the UI (class names, counts, suggestions).
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "text": self.text,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class PreflightReport:
    severity: str
    messages: list[PreflightMessage]
    #: class -> {"premium": n, "paid": n, "os": n}, for the side-by-side reconciliation table.
    row_counts: dict[str, dict[str, int]]
    dropped_row_count: int
    dropped_amount: float
    suggestions: list[dict[str, Any]]

    @property
    def blocking(self) -> bool:
        return self.severity == SEVERITY_ERROR

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "messages": [m.as_dict() for m in self.messages],
            "row_counts": self.row_counts,
            "dropped_row_count": self.dropped_row_count,
            "dropped_amount": self.dropped_amount,
            "suggestions": self.suggestions,
        }


def _classes(frame: pd.DataFrame | None) -> dict[str, str]:
    """canonical key -> the original spelling, for one frame."""
    if frame is None or CLASS_COLUMN not in getattr(frame, "columns", []):
        return {}
    out: dict[str, str] = {}
    for value in frame[CLASS_COLUMN].dropna().astype(str).unique():
        key = canonical_key(value)
        if key:
            out.setdefault(key, value)
    return out


def _engine_amount(frame: pd.DataFrame, *, is_os: bool) -> pd.Series:
    """The amount the engine would consume for these rows.

    `import_data` substitutes AMOUNTRECOVERED for AMOUNTPAID on Motor recovery heads, so a raw
    AMOUNTPAID sum would misstate what is actually being discarded.
    """
    column = "AMOUNTOUTSTANDING" if is_os else "AMOUNTPAID"
    if column not in frame.columns:
        return pd.Series([0.0] * len(frame), index=frame.index, dtype=float)
    amount = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    if is_os or "AMOUNTRECOVERED" not in frame.columns:
        return amount
    if "POLICYCLASS" not in frame.columns or "HEADOFDAMAGE" not in frame.columns:
        return amount
    recovered = pd.to_numeric(frame["AMOUNTRECOVERED"], errors="coerce").fillna(0.0)
    substitute = frame["POLICYCLASS"].astype(str).eq("Motor") & frame["HEADOFDAMAGE"].astype(
        str
    ).isin(RECOVERY_HEADS)
    return amount.where(~substitute, recovered)


def _rows_for(frame: pd.DataFrame | None, keys: Iterable[str]) -> pd.DataFrame:
    keyset = set(keys)
    if frame is None or CLASS_COLUMN not in getattr(frame, "columns", []) or not keyset:
        return pd.DataFrame()
    mask = frame[CLASS_COLUMN].astype(str).map(canonical_key).isin(keyset)
    return frame[mask]


def _count_by_class(frame: pd.DataFrame | None) -> dict[str, int]:
    if frame is None or CLASS_COLUMN not in getattr(frame, "columns", []):
        return {}
    counts = frame[CLASS_COLUMN].dropna().astype(str).map(canonical_key).value_counts()
    return {str(k): int(v) for k, v in counts.items()}


def build_preflight_report(
    premium: pd.DataFrame | None,
    claims_paid: pd.DataFrame | None,
    claims_os: pd.DataFrame | None,
) -> PreflightReport:
    """Reconcile reserving classes across the three inputs.

    Severities are chosen so the gate stays trustworthy:

    * **error** — rows will be discarded, or a class will develop with no paid claims. Both
      produce a plausible workbook containing a wrong number.
    * **warn** — a class has premium and no claims at all. Legitimate for a class with no
      experience (``D&O`` on the reference book), so it must never block: an error here would
      train users to run in permissive mode and the gate would stop meaning anything.
    """
    premium_classes = _classes(premium)
    paid_classes = _classes(claims_paid)
    os_classes = _classes(claims_os)

    premium_keys = set(premium_classes)
    paid_keys = set(paid_classes)
    os_keys = set(os_classes)

    premium_counts = _count_by_class(premium)
    paid_counts = _count_by_class(claims_paid)
    os_counts = _count_by_class(claims_os)

    row_counts: dict[str, dict[str, int]] = {}
    for key in sorted(premium_keys | paid_keys | os_keys):
        label = premium_classes.get(key) or paid_classes.get(key) or os_classes.get(key) or key
        row_counts[label] = {
            "premium": premium_counts.get(key, 0),
            "paid": paid_counts.get(key, 0),
            "os": os_counts.get(key, 0),
        }

    messages: list[PreflightMessage] = []
    suggestions: list[dict[str, Any]] = []

    # --- rows that will be discarded entirely -------------------------------------------
    dropped_paid_keys = paid_keys - premium_keys
    dropped_os_keys = os_keys - premium_keys
    dropped_paid = _rows_for(claims_paid, dropped_paid_keys)
    dropped_os = _rows_for(claims_os, dropped_os_keys)
    dropped_row_count = len(dropped_paid) + len(dropped_os)
    dropped_amount = float(
        (_engine_amount(dropped_paid, is_os=False).sum() if len(dropped_paid) else 0.0)
        + (_engine_amount(dropped_os, is_os=True).sum() if len(dropped_os) else 0.0)
    )

    for key in sorted(dropped_paid_keys | dropped_os_keys):
        label = paid_classes.get(key) or os_classes.get(key) or key
        matches = suggest_matches(label, sorted(premium_classes.values()))
        for m in matches:
            suggestions.append(
                {"alias": m.value, "canonical": m.candidate, "score": m.score, "basis": m.basis}
            )
        rows = int(paid_counts.get(key, 0) + os_counts.get(key, 0))
        hint = (
            f" Did you mean '{matches[0].candidate}'? ({matches[0].basis})"
            if matches
            else " No similar class exists in the premium file."
        )
        messages.append(
            PreflightMessage(
                code="class_not_in_premium",
                severity=SEVERITY_ERROR,
                text=(
                    f"'{label}' appears in the claims data but not in the premium data, so all "
                    f"{rows:,} of its rows would be silently discarded.{hint}"
                ),
                detail={
                    "class": label,
                    "rows": rows,
                    "suggestions": [
                        {"canonical": m.candidate, "score": m.score, "basis": m.basis}
                        for m in matches
                    ],
                },
            )
        )

    # --- premium classes that would develop with no paid claims -------------------------
    for key in sorted(premium_keys - paid_keys):
        label = premium_classes[key]
        has_os = key in os_keys
        if not has_os:
            # No claims at all — legitimate for a class with no experience.
            messages.append(
                PreflightMessage(
                    code="class_without_claims",
                    severity=SEVERITY_WARN,
                    text=(
                        f"'{label}' has premium but no claims of any kind. Its workbooks will "
                        f"contain no claims data. This is expected for a class with no "
                        f"experience yet."
                    ),
                    detail={"class": label, "premium_rows": premium_counts.get(key, 0)},
                )
            )
            continue
        matches = suggest_matches(label, sorted(paid_classes.values()))
        for m in matches:
            suggestions.append(
                {"alias": m.candidate, "canonical": m.value, "score": m.score, "basis": m.basis}
            )
        hint = (
            f" The paid data contains '{matches[0].candidate}', which looks like the same class "
            f"({matches[0].basis})."
            if matches
            else ""
        )
        messages.append(
            PreflightMessage(
                code="class_without_paid_claims",
                severity=SEVERITY_ERROR,
                text=(
                    f"'{label}' has premium and outstanding claims but no paid claims. Its paid "
                    f"triangle will be empty and its reserve will develop from outstanding "
                    f"alone.{hint}"
                ),
                detail={
                    "class": label,
                    "os_rows": os_counts.get(key, 0),
                    "suggestions": [
                        {"canonical": m.candidate, "score": m.score, "basis": m.basis}
                        for m in matches
                    ],
                },
            )
        )

    # --- premium classes with paid but no outstanding ------------------------------------
    for key in sorted(premium_keys & paid_keys - os_keys):
        label = premium_classes[key]
        messages.append(
            PreflightMessage(
                code="class_without_os_claims",
                severity=SEVERITY_WARN,
                text=(
                    f"'{label}' has no outstanding claims. Its reported triangle will equal its "
                    f"paid triangle."
                ),
                detail={"class": label},
            )
        )

    # --- missing inputs entirely ----------------------------------------------------------
    for label, frame in (("premium", premium), ("claims paid", claims_paid), ("claims outstanding", claims_os)):
        if frame is None or len(frame) == 0:
            messages.append(
                PreflightMessage(
                    code="empty_input",
                    severity=SEVERITY_ERROR,
                    text=(
                        f"The {label} data is empty. Every triangle would be produced as zeros "
                        f"with no other indication that anything was wrong."
                    ),
                    detail={"input": label},
                )
            )

    if dropped_row_count:
        messages.insert(
            0,
            PreflightMessage(
                code="rows_discarded",
                severity=SEVERITY_ERROR,
                text=(
                    f"{dropped_row_count:,} claim rows worth {dropped_amount:,.0f} would be "
                    f"discarded because their reserving class does not appear in the premium "
                    f"data."
                ),
                detail={"rows": dropped_row_count, "amount": dropped_amount},
            ),
        )

    severity = SEVERITY_OK
    for message in messages:
        if _SEVERITY_ORDER[message.severity] > _SEVERITY_ORDER[severity]:
            severity = message.severity

    # De-duplicate suggestions, strongest first.
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for s in sorted(suggestions, key=lambda s: -s["score"]):
        pair = (s["alias"], s["canonical"])
        if pair not in seen:
            seen.add(pair)
            unique.append(s)

    return PreflightReport(
        severity=severity,
        messages=messages,
        row_counts=row_counts,
        dropped_row_count=dropped_row_count,
        dropped_amount=dropped_amount,
        suggestions=unique,
    )
