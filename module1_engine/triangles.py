"""Grain-parameterised development triangles (requirement 5).

A **diagnostic** service: it reads the same claim data the reserving pipeline reads and
builds triangles at monthly, quarterly or yearly grain, but it writes nothing into the
reserve workbooks. Booking stays quarterly — see ``core.grain``.

Two findings from verification shape this module, and both are load-bearing.

**1. Quarterly LDFs cannot be composed from monthly LDFs.**
An earlier design proposed ``quarterly_LDF[k] = prod(monthly_LDF[3k : 3k+3])``. Measured on
the reference claims it is wrong by **+408.98%** at development 0. The cause is structural: a
quarterly accident period aggregates three monthly cohorts *at different maturities* — the
January cohort has three months of development by the end of Q1, February two, March one — so
a quarterly link ratio is not the product of three monthly link ratios at any offset. No
function here exposes that composition, and a negative test asserts it stays absent.

**2. The valid bridge runs through ultimates.**
Each quarterly cohort is exactly three monthly cohorts and the two triangles carry identical
totals, so monthly experience can be projected per monthly cohort, summed within the quarter,
and expressed as an **implied quarterly CDF** — the object the engine already consumes via
its ``Selected CDF`` row. That is exact.

**But it needs a credibility gate.** On the reference book the monthly route produces a 92%
higher ultimate driven by a tail CDF of 69.8 against 25.5 — sparsity, not signal. Median
claims per cell falls from 146 (quarterly) to 24 (monthly), and at the reserving grain four
of fourteen class/treaty triangles hold fewer than ten non-empty monthly cells. Every
triangle therefore reports its own credibility, and :func:`implied_cdf_from_finer_grain`
refuses below the floor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np
import pandas as pd

from core.grain import DEFAULT_GRAIN, PeriodGrain

# Credibility thresholds. Properties of the data, evaluated per triangle rather than
# assumed from the grain — a high-volume class can support monthly where the book as a
# whole cannot.
#
# Scored on DENSITY and VOLUME, never on raw cell count. An earlier version gated on
# `non_empty < 30`, which scored the reference book's quarterly triangle (26 cells, median
# 146 claims each) as *less* credible than its monthly one (158 cells, median 24) — exactly
# backwards, because a coarser grain has fewer cells by construction. Only a genuinely tiny
# triangle is unusable regardless of grain.
UNUSABLE_MAX_CELLS = 10
HIGH_MIN_MEDIAN_CLAIMS = 50
HIGH_MIN_FILL_RATIO = 0.60
MEDIUM_MIN_MEDIAN_CLAIMS = 15
MEDIUM_MIN_FILL_RATIO = 0.40

CREDIBILITY_UNUSABLE = "unusable"
CREDIBILITY_LOW = "low"
CREDIBILITY_MEDIUM = "medium"
CREDIBILITY_HIGH = "high"


@dataclass
class Credibility:
    accident_periods: int
    dev_periods: int
    cells_in_upper_triangle: int
    non_empty_cells: int
    claims: int
    median_claims_per_cell: float
    sparsest_dev_column: dict[str, int] | None
    fill_ratio: float
    level: str

    @property
    def usable(self) -> bool:
        return self.level != CREDIBILITY_UNUSABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "accident_periods": self.accident_periods,
            "dev_periods": self.dev_periods,
            "cells_in_upper_triangle": self.cells_in_upper_triangle,
            "non_empty_cells": self.non_empty_cells,
            "claims": self.claims,
            "median_claims_per_cell": self.median_claims_per_cell,
            "sparsest_dev_column": self.sparsest_dev_column,
            "fill_ratio": self.fill_ratio,
            "level": self.level,
            "usable": self.usable,
        }


@dataclass
class TriangleSet:
    grain: str
    accident_labels: list[str]
    dev_periods: list[int]
    incremental: pd.DataFrame
    cumulative: pd.DataFrame
    age_to_age: pd.DataFrame
    counts: pd.DataFrame
    credibility: Credibility
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def grid(frame: pd.DataFrame) -> list[list[float | None]]:
            return [
                [None if pd.isna(v) else float(v) for v in row]
                for row in frame.to_numpy()
            ]

        return {
            "grain": self.grain,
            "accident_labels": self.accident_labels,
            "dev_periods": self.dev_periods,
            "incremental": grid(self.incremental),
            "cumulative": grid(self.cumulative),
            "age_to_age": grid(self.age_to_age),
            "counts": [[int(v) for v in row] for row in self.counts.to_numpy()],
            "credibility": self.credibility.to_dict(),
            "warnings": list(self.warnings),
        }


def _score(counts: pd.DataFrame) -> Credibility:
    n_acc, n_dev = counts.shape
    values = counts.to_numpy()
    upper = sum(1 for i in range(n_acc) for j in range(n_dev) if i + j < n_acc)
    non_empty = int((values > 0).sum())
    claims = int(values.sum())
    populated = values[values > 0]
    median = float(np.median(populated)) if populated.size else 0.0

    sparsest = None
    if n_dev:
        col_counts = (values > 0).sum(axis=0)
        idx = int(np.argmin(col_counts))
        sparsest = {"index": idx, "non_empty": int(col_counts[idx])}

    fill = (non_empty / upper) if upper else 0.0
    if non_empty < UNUSABLE_MAX_CELLS:
        level = CREDIBILITY_UNUSABLE
    elif median >= HIGH_MIN_MEDIAN_CLAIMS and fill >= HIGH_MIN_FILL_RATIO:
        level = CREDIBILITY_HIGH
    elif median >= MEDIUM_MIN_MEDIAN_CLAIMS and fill >= MEDIUM_MIN_FILL_RATIO:
        level = CREDIBILITY_MEDIUM
    else:
        level = CREDIBILITY_LOW

    return Credibility(
        accident_periods=n_acc,
        dev_periods=n_dev,
        cells_in_upper_triangle=upper,
        non_empty_cells=non_empty,
        claims=claims,
        median_claims_per_cell=median,
        sparsest_dev_column=sparsest,
        fill_ratio=fill,
        level=level,
    )


def build_triangle(
    df: pd.DataFrame,
    *,
    grain: PeriodGrain = DEFAULT_GRAIN,
    start=None,
    end=None,
    amount_column: str = "Amount",
    accident_column: str = "LOSSDATE",
    development_column: str = "PAYMENTDATE",
    excluded_claims: Iterable[str] | None = None,
) -> TriangleSet:
    """Incremental / cumulative / age-to-age triangles at ``grain``.

    ``excluded_claims`` drops rows by ``CLAIMNUMBER`` before aggregating, so the WP5
    large-claim exclusions apply identically at every grain.
    """
    warnings: list[str] = []
    work = df.copy()

    if excluded_claims:
        excluded = {str(c) for c in excluded_claims}
        if "CLAIMNUMBER" in work.columns:
            before = len(work)
            work = work[~work["CLAIMNUMBER"].astype(str).isin(excluded)]
            warnings.append(f"Excluded {before - len(work):,} rows for {len(excluded)} claims.")
        else:
            warnings.append(
                "Claim exclusions were supplied but the data carries no CLAIMNUMBER column."
            )

    for col in (accident_column, development_column):
        work[col] = pd.to_datetime(work[col], errors="coerce")
    work = work.dropna(subset=[accident_column, development_column])
    if start is not None:
        work = work[work[accident_column] >= pd.Timestamp(start)]
    if end is not None:
        work = work[work[accident_column] <= pd.Timestamp(end)]

    if work.empty:
        empty = pd.DataFrame()
        return TriangleSet(
            grain=grain.key, accident_labels=[], dev_periods=[],
            incremental=empty, cumulative=empty, age_to_age=empty, counts=empty,
            credibility=_score(pd.DataFrame(np.zeros((0, 0)))),
            warnings=warnings + ["No claims fall inside the experience period."],
        )

    accident = work[accident_column].dt.to_period(grain.period_alias)
    development = work[development_column].dt.to_period(grain.period_alias)
    dev_index = (development - accident).apply(lambda x: x.n)

    negative = int((dev_index < 0).sum())
    if negative:
        warnings.append(
            f"{negative:,} rows develop before their accident period and were dropped."
        )
    keep = dev_index >= 0
    work, accident, dev_index = work[keep], accident[keep], dev_index[keep]

    # A full period axis, so the triangle is not silently ragged where a period had no claims.
    if start is not None and end is not None:
        axis = grain.period_range(start, end)
    else:
        axis = pd.period_range(accident.min(), accident.max(), freq=grain.period_alias)
    if len(axis) and accident.min() < axis[0]:
        warnings.append(
            f"Experience starts at {grain.label_for(accident.min())}, before the requested "
            f"{grain.label_for(axis[0])}."
        )

    n = len(axis)
    columns = list(range(n))
    amounts = pd.to_numeric(work[amount_column], errors="coerce").fillna(0.0)

    incremental = (
        pd.DataFrame({"acc": accident, "dev": dev_index, "amt": amounts})
        .pivot_table(index="acc", columns="dev", values="amt", aggfunc="sum", fill_value=0.0)
        .reindex(index=axis, columns=columns, fill_value=0.0)
    )
    counts = (
        pd.DataFrame({"acc": accident, "dev": dev_index})
        .assign(one=1)
        .pivot_table(index="acc", columns="dev", values="one", aggfunc="sum", fill_value=0)
        .reindex(index=axis, columns=columns, fill_value=0)
        .astype(int)
    )

    # Only the upper triangle is observed; the rest is future and stays NaN rather than 0,
    # so age-to-age factors are never computed against a fabricated zero.
    cumulative = incremental.cumsum(axis=1).astype(float)
    for i in range(n):
        cumulative.iloc[i, n - i:] = np.nan

    age_to_age = pd.DataFrame(np.nan, index=cumulative.index, columns=columns[:-1] or [0])
    for j in range(cumulative.shape[1] - 1):
        cur = cumulative.iloc[:, j]
        nxt = cumulative.iloc[:, j + 1]
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = nxt / cur.replace(0, np.nan)
        age_to_age.iloc[:, j] = ratio

    return TriangleSet(
        grain=grain.key,
        accident_labels=grain.labels(axis),
        dev_periods=columns,
        incremental=incremental,
        cumulative=cumulative,
        age_to_age=age_to_age,
        counts=counts,
        credibility=_score(counts),
        warnings=warnings,
    )


def volume_weighted_ldf(cumulative: pd.DataFrame) -> np.ndarray:
    """Volume-weighted link ratios over the observed (upper) part of the triangle."""
    n = len(cumulative)
    out = np.full(max(cumulative.shape[1] - 1, 0), np.nan)
    for j in range(len(out)):
        rows = n - j - 1
        if rows <= 0:
            continue
        num = cumulative.iloc[:rows, j + 1].sum(skipna=True)
        den = cumulative.iloc[:rows, j].sum(skipna=True)
        if den:
            out[j] = num / den
    return out


def cdf_from_ldf(ldf: np.ndarray) -> np.ndarray:
    """Reverse-cumulative product; blanks treated as 1.0 (no further development)."""
    clean = np.where(np.isfinite(ldf), ldf, 1.0)
    out = np.ones(len(clean) + 1)
    for i in range(len(clean) - 1, -1, -1):
        out[i] = out[i + 1] * clean[i]
    return out


@dataclass
class ImpliedCdfResult:
    """Coarse-grain CDFs implied by finer-grain development."""

    labels: list[str]
    implied_cdf: list[float | None]
    paid_to_date: list[float]
    ultimate: list[float]
    credibility: Credibility
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "labels": self.labels,
            "implied_cdf": self.implied_cdf,
            "paid_to_date": self.paid_to_date,
            "ultimate": self.ultimate,
            "credibility": self.credibility.to_dict(),
            "warnings": list(self.warnings),
        }


def implied_cdf_from_finer_grain(
    fine: TriangleSet,
    coarse: TriangleSet,
    *,
    fine_grain: PeriodGrain,
    coarse_grain: PeriodGrain,
    allow_low_credibility: bool = False,
) -> ImpliedCdfResult:
    """Coarse-grain CDFs implied by developing each fine cohort and summing the ultimates.

    This is the ONLY supported bridge between grains. Composing link ratios across grains is
    invalid (see the module docstring); this route is exact because each coarse cohort is an
    exact union of fine cohorts.

    Refuses on an ``unusable`` fine triangle, and on ``low`` unless the caller explicitly
    accepts it — on the reference book the monthly route lifts the ultimate 92% on the
    strength of a 69.8 tail CDF, which is sparsity rather than signal.
    """
    warnings = list(fine.warnings)
    if fine.credibility.level == CREDIBILITY_UNUSABLE:
        raise ValueError(
            f"The {fine.grain} triangle has only {fine.credibility.non_empty_cells} "
            f"non-empty cells from {fine.credibility.claims} claims — too sparse to imply "
            f"development factors."
        )
    if fine.credibility.level == CREDIBILITY_LOW and not allow_low_credibility:
        raise ValueError(
            f"The {fine.grain} triangle is thin ({fine.credibility.non_empty_cells} non-empty "
            f"cells, median {fine.credibility.median_claims_per_cell:.0f} claims per cell). "
            f"Confirm explicitly to derive factors from it."
        )

    fine_cdf = cdf_from_ldf(volume_weighted_ldf(fine.cumulative))
    n_fine = len(fine.cumulative)

    ultimate_by_coarse: dict[str, float] = {}
    for i, label in enumerate(fine.accident_labels):
        maturity = min(n_fine - 1 - i, fine.cumulative.shape[1] - 1)
        if maturity < 0:
            continue
        paid = fine.cumulative.iloc[i, maturity]
        if pd.isna(paid):
            continue
        factor = fine_cdf[maturity] if maturity < len(fine_cdf) else 1.0
        parent = coarse_grain.label_for(fine_grain.parse(label).to_timestamp())
        ultimate_by_coarse[parent] = ultimate_by_coarse.get(parent, 0.0) + float(paid) * factor

    n_coarse = len(coarse.cumulative)
    labels, cdfs, paids, ults = [], [], [], []
    for i, label in enumerate(coarse.accident_labels):
        maturity = min(n_coarse - 1 - i, coarse.cumulative.shape[1] - 1)
        paid = coarse.cumulative.iloc[i, maturity] if maturity >= 0 else np.nan
        paid = 0.0 if pd.isna(paid) else float(paid)
        ult = ultimate_by_coarse.get(label, 0.0)
        labels.append(label)
        paids.append(paid)
        ults.append(ult)
        cdfs.append((ult / paid) if abs(paid) > 1e-9 else None)

    missing = set(ultimate_by_coarse) - set(labels)
    if missing:
        warnings.append(
            f"{len(missing)} fine-grain cohorts fall outside the coarse axis and were ignored."
        )
    return ImpliedCdfResult(
        labels=labels,
        implied_cdf=cdfs,
        paid_to_date=paids,
        ultimate=ults,
        credibility=fine.credibility,
        warnings=warnings,
    )
