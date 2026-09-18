"""Earned premium per accident period, at any grain, on the basis the booking already uses.

Two definitions of earned premium exist in this codebase and only one of them is booked.

**Booked** — the UPR-movement basis, built per period inside
``engine.summarize_upr_by_reserving_class`` and written to ``Reserve Summary.EP``:

    EP(p) = GWP(p) − UPR(end of p) + UPR(end of p−1)

where ``GWP(p)`` is premium on policies *issued* in ``p`` and
``UPR(d) = unearned_fraction(df, d, policy) × PREMIUMAMOUNT``. It therefore depends on the run's
UPR method policy, which is why this function takes one rather than assuming ``pro_rata_daily``.
That EP drives `ELR Ultimate`, both BF ultimates, `ULR` and `Reported LR`.

**Unbooked** — there used to be a second one: ``engine.calculate_quarterly_premium``, a
pro-rata-by-risk-days figure computed inside a timed stage and then discarded, whose only
would-be consumer (``export_upr_summary_to_excel``) had no callers either. Both were deleted
alongside this module. Had the triangle view reached for the nearest-looking helper it would have
put a second, different earned premium in front of an actuary reconciling to the workbook.

So this module lifts the booked definition out unchanged and parameterises the one thing the
engine hard-codes: the grain. The correctness argument is a single equality — at quarterly this
must reproduce ``Reserve Summary.EP`` to the cent for every class and treaty. Nothing else can
check the monthly and yearly figures, because no workbook contains them.

Grouping note: the engine groups by ``(RESERVINGCLASS, UWY)`` and the Reserve Summary later sums
those over UWY. EP is a linear combination of sums, so grouping by class directly and skipping
the UWY split is arithmetically identical — asserted by the reconciliation test rather than
assumed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.grain import DEFAULT_GRAIN, PeriodGrain
from module1_engine.upr_methods import UprPolicy, unearned_fraction

GROSS = "GROSS"
RI = "RI"


def _prepared(premium_df: pd.DataFrame) -> pd.DataFrame:
    """The frame shaped as `calculate_upr` leaves it, which is what `unearned_fraction` reads.

    Deliberately the same three steps in the same order — date coercion, then
    `preprocess_dates`, then `Duration` — because the methods behind `unearned_fraction` read
    all of them and a difference here would show up as an EP that disagrees with the workbook.
    """
    from module1_engine.engine import preprocess_dates

    df = premium_df.copy()
    for col in ("ISSUEDATE", "RiskStartDate", "RiskEndDate", "POLICYSTARTDATE", "POLICYENDDATE"):
        if col in df.columns and df[col].dtype == "object":
            df[col] = pd.to_datetime(df[col], errors="coerce")
    df["PREMIUMAMOUNT"] = pd.to_numeric(df["PREMIUMAMOUNT"], errors="coerce")
    if "COMMISSIONAMOUNT" in df.columns:
        df["COMMISSIONAMOUNT"] = pd.to_numeric(df["COMMISSIONAMOUNT"], errors="coerce")
    preprocess_dates(df)
    # `calculate_upr` adds this before any method runs; `sum_of_digits` reads it.
    df["Duration"] = pd.to_numeric(
        (df["RiskEndDate"] - df["RiskStartDate"]).dt.days + 1, errors="coerce"
    )
    return df


def earned_premium_by_period(
    premium_df: pd.DataFrame,
    *,
    grain: PeriodGrain = DEFAULT_GRAIN,
    bop,
    eop,
    upr_policy: UprPolicy | None = None,
) -> pd.DataFrame:
    """Long-form earned premium: one row per (class, treaty, period).

    ``bop``/``eop`` bound the periods reported, exactly as they bound the engine's loop. The
    period before ``bop`` is evaluated too — it supplies the opening UPR that the first
    period's movement is measured against — but is not itself reported.
    """
    if premium_df is None or premium_df.empty:
        return pd.DataFrame(columns=["RESERVINGCLASS", "RI_TREATY_TYPE", "period", "ep"])

    df = _prepared(premium_df)
    bop_ts, eop_ts = pd.Timestamp(bop), pd.Timestamp(eop)

    # The engine seeds its loop with the day before BOP so the first reported period has an
    # opening UPR to subtract from. Same here, and the seed period is dropped at the end.
    opening = bop_ts - pd.Timedelta(days=1)
    period_ends = [opening, *grain.date_range(bop_ts, eop_ts)]

    issue_period = df["ISSUEDATE"].dt.to_period(grain.period_alias).map(grain.label_for)
    gross = df["RI_TREATY_TYPE"] == GROSS
    ri = df["RI_TREATY_TYPE"] == RI
    classes = df["RESERVINGCLASS"]

    rows: list[dict] = []
    previous: dict[tuple[str, str], float] | None = None

    for date in period_ends:
        label = grain.label_for(date)
        upr = pd.Series(
            unearned_fraction(df, date, upr_policy) * df["PREMIUMAMOUNT"].fillna(0.0),
            index=df.index,
        )
        written = df["PREMIUMAMOUNT"].where(issue_period == label, other=np.nan)

        closing: dict[tuple[str, str], float] = {}
        for treaty, mask in ((GROSS, gross), (RI, ri)):
            by_class_upr = upr.where(mask).groupby(classes).sum()
            by_class_gwp = written.where(mask).groupby(classes).sum()
            for reserving_class in by_class_upr.index:
                key = (str(reserving_class), treaty)
                closing[key] = float(by_class_upr.get(reserving_class, 0.0))
                if previous is None:
                    continue  # the seed period reports nothing
                rows.append({
                    "RESERVINGCLASS": key[0],
                    "RI_TREATY_TYPE": treaty,
                    "period": label,
                    "ep": float(by_class_gwp.get(reserving_class, 0.0))
                    - closing[key]
                    + previous.get(key, 0.0),
                })
        previous = closing

    out = pd.DataFrame(rows, columns=["RESERVINGCLASS", "RI_TREATY_TYPE", "period", "ep"])
    return out.sort_values(
        ["RESERVINGCLASS", "RI_TREATY_TYPE", "period"], key=_period_sort_key(grain)
    ).reset_index(drop=True)


def _period_sort_key(grain: PeriodGrain):
    """Chronological ordering for the period column; identity for the others."""
    def key(column: pd.Series):
        if column.name != "period":
            return column
        return column.map(grain.sort_key)
    return key
