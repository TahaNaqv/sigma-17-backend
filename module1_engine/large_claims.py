"""Large-claim identification and exclusion (requirement 6).

Two ranking rules here are correctness requirements, not refinements, and a naive
implementation gets both wrong (docs/LARGE_CLAIMS_EXCLUSION_PLAN.md §1.3):

**Paid must be slice-scoped.** The claims file carries one row per
``(claim x head of damage x treaty x transaction)``. A naive ``groupby(CLAIMNUMBER).sum()``
therefore adds GROSS to RI and nets Payment against Salvage, which is not a meaningful
ordering. Scoped to GROSS/Payment the top ten claims are 22.3% of gross paid; the
unscoped figure of 29.9% quoted early in this project came from exactly that error.

**Outstanding must use the latest as-at.** The OS file carries one row per
``(claim x as-at x treaty)``, one to four snapshots per claim. Summing across them
multiply-counts a single reserve: on the reference book the naive sum overstates the largest
claim **6.5x** (38,994,200 against 3,999,200) and **returns a different claim first**. A naive
implementation would not merely be imprecise, it would list the wrong claims.

Exclusion is then applied through one of three treatments. The default is
``exclude_and_add_back``, corrected after measurement: the intuitive-sounding
``exclude_from_ldf_only`` applies attritional factors to a base that still contains the large
claims, double-counts their development, and *raises* the reserve 13.3% — the largest move of
the three and the opposite of what a user removing claims expects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# Treatment modes
# --------------------------------------------------------------------------- #

#: Attritional triangle developed with attritional factors; the excluded claims re-enter at
#: their known incurred (paid + case). The only mode whose factors and base describe the same
#: population. Measured -2.7%.
MODE_ADD_BACK = "exclude_and_add_back"
#: Factors from the attritional triangle applied to a base that still holds the large
#: claims. Internally inconsistent — measured +13.3%. Available, never default.
MODE_LDF_ONLY = "exclude_from_ldf_only"
#: Gone from base and factors alike. Understates the ultimate by dropping real cost.
MODE_ENTIRELY = "exclude_entirely"

MODES = (MODE_ADD_BACK, MODE_LDF_ONLY, MODE_ENTIRELY)
DEFAULT_MODE = MODE_ADD_BACK

MODE_LABELS: dict[str, str] = {
    MODE_ADD_BACK: "Exclude from development, add actual cost back",
    MODE_LDF_ONLY: "Exclude from factor selection only",
    MODE_ENTIRELY: "Exclude entirely",
}

#: Plain-language consequence, shown next to each mode. Measured on the reference book.
MODE_NOTES: dict[str, str] = {
    MODE_ADD_BACK: (
        "Develops the attritional book with attritional factors, then adds the excluded "
        "claims back at their known incurred — paid plus case reserve — so they carry no "
        "IBNR of their own. Factors and base describe the same population. Measured -2.7% "
        "on the reference book."
    ),
    MODE_LDF_ONLY: (
        "Takes factors from a triangle without the large claims but leaves those claims in "
        "the reserve base. This double-counts their development and can RAISE the reserve "
        "substantially — measured +13.3% on the reference book, with one accident quarter "
        "up 36.9%."
    ),
    MODE_ENTIRELY: (
        "Removes the claims from the factors and the base. Understates the ultimate by "
        "dropping cost that was genuinely incurred — measured -8.2% on the reference book."
    ),
}

# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #

SELECT_TOP_N = "top_n"
SELECT_THRESHOLD = "threshold"
SELECTION_KINDS = (SELECT_TOP_N, SELECT_THRESHOLD)

DEFAULT_TOP_N = 10
#: The slice a claim is ranked within. Summing outside it is the §1.3 error.
DEFAULT_TREATY = "GROSS"
DEFAULT_HEAD_OF_DAMAGE = "Payment"


@dataclass
class LargeClaim:
    claim_number: str
    reserving_class: str
    paid: float
    outstanding: float
    #: Paid + outstanding at the latest as-at. The figure a user ranks on.
    incurred: float
    share_of_class_paid: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_number": self.claim_number,
            "reserving_class": self.reserving_class,
            "paid": self.paid,
            "outstanding": self.outstanding,
            "incurred": self.incurred,
            "share_of_class_paid": self.share_of_class_paid,
        }


@dataclass
class LargeClaimReport:
    claims: list[LargeClaim]
    slice_treaty: str
    slice_head_of_damage: str
    selection: dict[str, Any]
    per_class: bool
    total_paid_in_slice: float
    warnings: list[str] = field(default_factory=list)

    @property
    def claim_numbers(self) -> list[str]:
        return [c.claim_number for c in self.claims]

    @property
    def concentration(self) -> float | None:
        """Share of slice paid held by the selected claims — the number that tells an
        actuary whether exclusion is warranted at all."""
        if not self.total_paid_in_slice:
            return None
        return sum(c.paid for c in self.claims) / self.total_paid_in_slice

    def to_dict(self) -> dict[str, Any]:
        return {
            "claims": [c.to_dict() for c in self.claims],
            "slice": {
                "treaty": self.slice_treaty,
                "head_of_damage": self.slice_head_of_damage,
            },
            "selection": dict(self.selection),
            "per_class": self.per_class,
            "total_paid_in_slice": self.total_paid_in_slice,
            "concentration": self.concentration,
            "warnings": list(self.warnings),
        }


def _has_claim_numbers(df: pd.DataFrame) -> bool:
    return df is not None and not df.empty and "CLAIMNUMBER" in df.columns


def paid_by_claim(
    paid: pd.DataFrame,
    *,
    treaty: str = DEFAULT_TREATY,
    head_of_damage: str | None = DEFAULT_HEAD_OF_DAMAGE,
    amount_column: str = "Amount",
) -> pd.DataFrame:
    """Paid per claim within an explicit slice.

    Uses the engine's derived ``Amount`` (which already handles the Motor recovery
    substitution) so the ranking agrees with the triangles it is used to adjust.
    """
    if not _has_claim_numbers(paid):
        return pd.DataFrame(columns=["CLAIMNUMBER", "RESERVINGCLASS", "paid"])
    work = paid
    if treaty:
        work = work[work["RI_TREATY_TYPE"].astype(str).str.upper() == treaty.upper()]
    if head_of_damage:
        work = work[work["HEADOFDAMAGE"].astype(str) == head_of_damage]
    if work.empty:
        return pd.DataFrame(columns=["CLAIMNUMBER", "RESERVINGCLASS", "paid"])
    amounts = pd.to_numeric(work[amount_column], errors="coerce").fillna(0.0)
    return (
        pd.DataFrame({
            "CLAIMNUMBER": work["CLAIMNUMBER"].astype(str),
            "RESERVINGCLASS": work["RESERVINGCLASS"].astype(str),
            "paid": amounts,
        })
        .groupby(["CLAIMNUMBER", "RESERVINGCLASS"], as_index=False)["paid"].sum()
    )


def outstanding_by_claim(
    os_data: pd.DataFrame,
    *,
    treaty: str = DEFAULT_TREATY,
    amount_column: str = "Amount",
) -> pd.DataFrame:
    """Outstanding per claim **at its latest as-at**.

    Never a sum across snapshots: that multiply-counts one reserve and, measured on the
    reference book, both overstates the largest claim 6.5x and reorders the ranking.
    """
    if not _has_claim_numbers(os_data) or "As at" not in os_data.columns:
        return pd.DataFrame(columns=["CLAIMNUMBER", "RESERVINGCLASS", "outstanding"])
    work = os_data
    if treaty:
        work = work[work["RI_TREATY_TYPE"].astype(str).str.upper() == treaty.upper()]
    if work.empty:
        return pd.DataFrame(columns=["CLAIMNUMBER", "RESERVINGCLASS", "outstanding"])

    work = work.assign(_asat=pd.to_datetime(work["As at"], errors="coerce"))
    latest = work.groupby("CLAIMNUMBER")["_asat"].transform("max")
    work = work[work["_asat"] == latest]

    amounts = pd.to_numeric(work[amount_column], errors="coerce").fillna(0.0)
    return (
        pd.DataFrame({
            "CLAIMNUMBER": work["CLAIMNUMBER"].astype(str),
            "RESERVINGCLASS": work["RESERVINGCLASS"].astype(str),
            "outstanding": amounts,
        })
        .groupby(["CLAIMNUMBER", "RESERVINGCLASS"], as_index=False)["outstanding"].sum()
    )


def rank_claims(
    paid: pd.DataFrame,
    os_data: pd.DataFrame | None = None,
    *,
    treaty: str = DEFAULT_TREATY,
    head_of_damage: str | None = DEFAULT_HEAD_OF_DAMAGE,
    kind: str = SELECT_TOP_N,
    top_n: int = DEFAULT_TOP_N,
    threshold: float | None = None,
    per_class: bool = True,
    rank_on: str = "incurred",
) -> LargeClaimReport:
    """Rank claims within a slice and select the large ones.

    ``per_class`` ranks inside each reserving class, which is the default because factor
    selection is per class — a book-wide top ten would pick every claim from the largest
    class and none from the others.
    """
    if kind not in SELECTION_KINDS:
        raise ValueError(f"Unknown selection kind {kind!r}; expected one of {SELECTION_KINDS}.")

    warnings: list[str] = []
    if not _has_claim_numbers(paid):
        return LargeClaimReport(
            claims=[], slice_treaty=treaty, slice_head_of_damage=head_of_damage or "",
            selection={"kind": kind, "top_n": top_n, "threshold": threshold},
            per_class=per_class, total_paid_in_slice=0.0,
            warnings=["This data carries no claim numbers, so claims cannot be identified."],
        )

    by_paid = paid_by_claim(paid, treaty=treaty, head_of_damage=head_of_damage)
    by_os = (
        outstanding_by_claim(os_data, treaty=treaty)
        if os_data is not None
        else pd.DataFrame(columns=["CLAIMNUMBER", "RESERVINGCLASS", "outstanding"])
    )
    if os_data is not None and by_os.empty:
        warnings.append("No outstanding data matched this slice; ranking uses paid only.")

    merged = by_paid.merge(by_os, on=["CLAIMNUMBER", "RESERVINGCLASS"], how="outer")
    merged["paid"] = merged["paid"].fillna(0.0)
    merged["outstanding"] = merged["outstanding"].fillna(0.0)
    merged["incurred"] = merged["paid"] + merged["outstanding"]
    if merged.empty:
        return LargeClaimReport(
            claims=[], slice_treaty=treaty, slice_head_of_damage=head_of_damage or "",
            selection={"kind": kind, "top_n": top_n, "threshold": threshold},
            per_class=per_class, total_paid_in_slice=0.0,
            warnings=warnings + ["No claims fall inside the selected slice."],
        )

    if rank_on not in ("incurred", "paid", "outstanding"):
        raise ValueError(f"Unknown rank_on {rank_on!r}.")

    # "Largest" is by magnitude: a large recovery is as material as a large payment.
    merged["_rank"] = merged[rank_on].abs()

    if kind == SELECT_THRESHOLD:
        if threshold is None:
            raise ValueError("Threshold selection requires a threshold.")
        selected = merged[merged["_rank"] >= float(threshold)]
    elif per_class:
        selected = (
            merged.sort_values("_rank", ascending=False)
            .groupby("RESERVINGCLASS", group_keys=False)
            .head(int(top_n))
        )
    else:
        selected = merged.nlargest(int(top_n), "_rank")

    class_totals = by_paid.groupby("RESERVINGCLASS")["paid"].sum().to_dict()
    claims = [
        LargeClaim(
            claim_number=r.CLAIMNUMBER,
            reserving_class=r.RESERVINGCLASS,
            paid=float(r.paid),
            outstanding=float(r.outstanding),
            incurred=float(r.incurred),
            share_of_class_paid=(
                float(r.paid) / class_totals[r.RESERVINGCLASS]
                if class_totals.get(r.RESERVINGCLASS) else None
            ),
        )
        for r in selected.sort_values("_rank", ascending=False).itertuples()
    ]
    return LargeClaimReport(
        claims=claims,
        slice_treaty=treaty,
        slice_head_of_damage=head_of_damage or "",
        selection={"kind": kind, "top_n": top_n, "threshold": threshold, "rank_on": rank_on},
        per_class=per_class,
        total_paid_in_slice=float(by_paid["paid"].sum()),
        warnings=warnings,
    )


# --------------------------------------------------------------------------- #
# Applying an exclusion
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ExclusionPlan:
    """How an exclusion should reach the two independent paths of the reserve loop.

    The engine builds the Reserve Summary base (paid / OS per accident period) and the
    triangles from separate expressions over the same data, which is what makes the three
    modes expressible at all:

    ========================  ==================  =============  ==========
    mode                      triangles filtered  base filtered  add-back
    ========================  ==================  =============  ==========
    exclude_and_add_back      yes                 no             yes
    exclude_from_ldf_only     yes                 no             no
    exclude_entirely          yes                 yes            no
    ========================  ==================  =============  ==========

    ``exclude_and_add_back`` leaves the Reserve Summary base **unfiltered** on purpose: the
    Paid/OS/Reported columns must still tie to the ledger, and BF ultimates take their known
    component straight from them. The split happens inside the chain-ladder ultimate, which
    develops ``base - large`` with the attritional factor and then adds the large claims'
    known incurred (paid + case) back at face value. A large claim therefore carries no IBNR
    — its case reserve is taken as its ultimate — which is the standard treatment and the
    reason its development must not be run through an attritional factor.
    """

    claim_numbers: frozenset[str]
    mode: str = DEFAULT_MODE

    @classmethod
    def build(cls, claim_numbers: Iterable[str], mode: str = DEFAULT_MODE) -> "ExclusionPlan":
        if mode not in MODES:
            raise ValueError(f"Unknown exclusion mode {mode!r}; expected one of {MODES}.")
        return cls(frozenset(str(c) for c in claim_numbers), mode)

    @property
    def active(self) -> bool:
        return bool(self.claim_numbers)

    @property
    def filters_triangles(self) -> bool:
        return self.active

    @property
    def filters_base(self) -> bool:
        """Only ``exclude_entirely`` drops the claims from the Reserve Summary base.

        Add-back keeps the base whole and nets the large claims out inside the ultimate
        instead (see :meth:`large_amounts`), so the summary still reconciles to the ledger.
        """
        return self.active and self.mode == MODE_ENTIRELY

    @property
    def adds_back(self) -> bool:
        return self.active and self.mode == MODE_ADD_BACK

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        """Drop the excluded claims from ``df``. A frame without claim numbers is returned
        untouched — the caller warns rather than silently reserving on the wrong basis."""
        if not self.active or not _has_claim_numbers(df):
            return df
        return df[~df["CLAIMNUMBER"].astype(str).isin(self.claim_numbers)]

    def excluded_rows(self, df: pd.DataFrame) -> pd.DataFrame:
        if not self.active or not _has_claim_numbers(df):
            return df.iloc[0:0]
        return df[df["CLAIMNUMBER"].astype(str).isin(self.claim_numbers)]

    def period_totals(
        self,
        df: pd.DataFrame,
        periods: Sequence[object],
        *,
        period_column: str = "Accident_Period",
        amount_column: str = "Amount",
    ) -> list[float]:
        """Excluded amount per accident period, aligned to ``periods``.

        Returns zeros — never a shorter list — when nothing matches, so a stale selection
        against re-uploaded files degrades to "no add-back" rather than misaligning the
        Reserve Summary. :func:`match_report` is what tells the caller that happened.
        """
        rows = self.excluded_rows(df)
        if rows.empty or period_column not in rows.columns:
            return [0.0] * len(periods)
        totals = rows.groupby(period_column)[amount_column].sum()
        return [float(totals.get(p, 0.0)) for p in periods]

    def match_report(self, df: pd.DataFrame) -> dict:
        """How many of the requested claim numbers were actually found in ``df``.

        An exclusion that matches nothing produces output identical to no exclusion at all.
        That is the one failure mode of this feature a user cannot see, so it is measured
        and surfaced rather than left to inference.
        """
        if not self.active:
            return {"requested": 0, "matched": 0, "unmatched": []}
        if not _has_claim_numbers(df):
            return {
                "requested": len(self.claim_numbers),
                "matched": 0,
                "unmatched": sorted(self.claim_numbers),
            }
        present = set(df["CLAIMNUMBER"].astype(str))
        matched = self.claim_numbers & present
        return {
            "requested": len(self.claim_numbers),
            "matched": len(matched),
            "unmatched": sorted(self.claim_numbers - present),
        }
