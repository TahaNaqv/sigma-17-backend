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

#: Triangle bases. Paid develops payments; reported develops paid plus the case reserve
#: standing at each valuation, so it is only as fine as the valuation dates allow.
BASIS_PAID = "paid"
BASIS_REPORTED = "reported"

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
    #: "paid" or "reported". The paid basis is the historic behaviour and its output is
    #: unchanged; nothing about it is conditional on this field.
    basis: str = BASIS_PAID
    #: Present on the reported basis only — what the outstanding extract could support.
    coverage: "ValuationCoverage | None" = None

    def to_dict(self) -> dict[str, Any]:
        def grid(frame: pd.DataFrame) -> list[list[float | None]]:
            return [
                [None if pd.isna(v) else float(v) for v in row]
                for row in frame.to_numpy()
            ]

        return {
            "grain": self.grain,
            "basis": self.basis,
            "coverage": self.coverage.to_dict() if self.coverage else None,
            "accident_labels": self.accident_labels,
            "dev_periods": self.dev_periods,
            "incremental": grid(self.incremental),
            "cumulative": grid(self.cumulative),
            "age_to_age": grid(self.age_to_age),
            "counts": [[int(v) for v in row] for row in self.counts.to_numpy()],
            "credibility": self.credibility.to_dict(),
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# Valuation coverage — what an outstanding-claims extract can actually support
# ---------------------------------------------------------------------------

#: Extract shapes, ordered from most to least capable.
COVERAGE_INVENTORY = "inventory"
COVERAGE_SPARSE = "sparse"
COVERAGE_DIAGONAL = "diagonal"
COVERAGE_SINGLE = "single"
COVERAGE_NONE = "none"


@dataclass(frozen=True)
class ValuationCoverage:
    """Which cells of a reported triangle are backed by an actual valuation.

    An outstanding balance is only ever known **at a valuation date**, so each distinct
    ``As at`` in the extract supplies exactly one *diagonal* of the incurred triangle. The
    grain of the triangle is therefore limited by the frequency of the extract, not by us —
    a file valued at year-ends can support a yearly reported triangle and nothing finer.

    The distinction this type exists to preserve:

    * a cell **inside** a valued diagonal with no outstanding is a genuine **zero** — every
      claim for that cohort had closed by that date;
    * a cell **outside** every valued diagonal is **unknown**, and must stay null.

    Collapsing the two is what produces a sawtooth triangle whose development factors measure
    where the valuation dates fall rather than how claims develop. Measured on the client's
    production extract: four annual valuations against a quarterly grain leave 40 of 136
    upper-triangle cells valued (29%), and the resulting factors are meaningless.
    """

    grain: str
    #: Valuation periods present in the extract, at this grain, chronological.
    valuation_periods: list[str]
    #: Per valuation period, the (oldest, newest) accident period actually observed in it.
    #: An extract that only ever reports the current period's own claims shows (p, p) here,
    #: which is how :data:`COVERAGE_DIAGONAL` is detected.
    accident_span: dict[str, tuple[str, str]]
    shape: str
    #: ``(accident index, development index)`` pairs backed by a valuation.
    covered_cells: list[tuple[int, int]]
    #: Covered cells as a share of the upper triangle.
    coverage_ratio: float
    warnings: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        """Whether a reported triangle should be produced at this grain at all.

        **Inventory only.** An earlier version also accepted :data:`COVERAGE_SPARSE` and
        rendered the triangle with the unvalued cells left null. The client's rule is
        narrower, and it is the right one: *"if OS is available at each frequency, there will
        be a reporting triangle, else not — at quarterly dates available it can show quarterly,
        but also show monthly and there may be some diagonal blank ... and that will be
        misleading, or we can say not sufficient information provided."*

        A triangle drawn with three cells in four missing does not read as "insufficient data";
        it reads as a triangle. Saying so in words is harder to misread than showing it and
        hoping the gaps speak for themselves.
        """
        return self.shape == COVERAGE_INVENTORY

    @property
    def supports_factors(self) -> bool:
        """Whether age-to-age factors can be formed.

        A factor needs **both** of its cells valued. Under :data:`COVERAGE_SPARSE` the valued
        diagonals are further apart than one development period, so no adjacent pair exists
        and no factor can be formed — which is the correct answer, not a limitation to work
        around.
        """
        if not self.usable:
            return False
        covered = set(self.covered_cells)
        return any((i, j + 1) in covered for (i, j) in covered)

    def covers(self, accident_index: int, dev_index: int) -> bool:
        return (accident_index, dev_index) in set(self.covered_cells)

    def to_dict(self) -> dict[str, Any]:
        return {
            "grain": self.grain,
            "valuation_periods": list(self.valuation_periods),
            "accident_span": {k: list(v) for k, v in self.accident_span.items()},
            "shape": self.shape,
            "covered_cells": [list(c) for c in self.covered_cells],
            "coverage_ratio": self.coverage_ratio,
            "usable": self.usable,
            "supports_factors": self.supports_factors,
            "warnings": list(self.warnings),
        }


def valuation_coverage(
    os_frame: pd.DataFrame | None,
    *,
    grain: PeriodGrain = DEFAULT_GRAIN,
    start=None,
    end=None,
    as_at_column: str = "As at",
    accident_column: str = "LOSSDATE",
) -> ValuationCoverage:
    """Classify what ``os_frame`` can support at ``grain``.

    Runs before any triangle is built, so the caller can refuse, degrade or proceed with a
    reason rather than discovering the problem in the factors.
    """
    warnings: list[str] = []

    def _empty(shape: str, note: str) -> ValuationCoverage:
        warnings.append(note)
        return ValuationCoverage(
            grain=grain.key, valuation_periods=[], accident_span={}, shape=shape,
            covered_cells=[], coverage_ratio=0.0, warnings=warnings,
        )

    if os_frame is None or os_frame.empty:
        return _empty(COVERAGE_NONE, "No outstanding-claims data was supplied.")
    if as_at_column not in os_frame.columns:
        return _empty(
            COVERAGE_NONE,
            f"The outstanding extract carries no {as_at_column!r} column, so no valuation "
            f"date can be established.",
        )

    work = os_frame.copy()
    for col in (as_at_column, accident_column):
        work[col] = pd.to_datetime(work[col], errors="coerce")
    work = work.dropna(subset=[as_at_column, accident_column])
    if work.empty:
        return _empty(COVERAGE_NONE, "No outstanding row carries both a valuation and a loss date.")

    val = work[as_at_column].dt.to_period(grain.period_alias)
    acc = work[accident_column].dt.to_period(grain.period_alias)

    if start is not None and end is not None:
        axis = grain.period_range(start, end)
    else:
        axis = pd.period_range(acc.min(), acc.max(), freq=grain.period_alias)
    n = len(axis)
    if n == 0:
        return _empty(COVERAGE_NONE, "The experience period contains no accident periods.")

    spans: dict[str, tuple[str, str]] = {}
    oldest_by_valuation: dict[pd.Period, pd.Period] = {}
    for period, group in acc.groupby(val):
        spans[grain.label_for(period)] = (
            grain.label_for(group.min()), grain.label_for(group.max())
        )
        oldest_by_valuation[period] = group.min()
    valuations = sorted(oldest_by_valuation)

    # A cell is valued when its maturity date is one of the valuations. Nothing more: a
    # supplied valuation is taken as a COMPLETE inventory at that date, so a cohort absent
    # from it had nothing outstanding — its claims had closed.
    #
    # An earlier rule also required the valuation to list an accident period at least as old
    # as the cohort, meaning to catch an extract that never reaches back. It misread the
    # healthy case instead: in a real inventory the oldest open cohort drifts forward as old
    # claims close, so that rule progressively nulled the oldest cells of a perfectly good
    # triangle and could not tell "closed" from "not supplied" anyway. The spans are still
    # computed — they are what identifies a diagonal-only extract below, which is the shape
    # that genuinely cannot develop, and that check is where this concern belongs.
    covered: list[tuple[int, int]] = []
    for i, accident_period in enumerate(axis):
        for j in range(n - i):
            if (accident_period + j) in oldest_by_valuation:
                covered.append((i, j))

    upper = n * (n + 1) // 2
    ratio = len(covered) / upper if upper else 0.0

    valued_diagonals = [d for d in range(n) if axis[d] in oldest_by_valuation]
    diagonal_only = all(
        span[0] == span[1] == label for label, span in spans.items()
    )

    if len(valuations) == 1:
        shape = COVERAGE_SINGLE
        warnings.append(
            f"One valuation date only ({grain.label_for(valuations[0])}). This gives the "
            f"incurred position at that date, not a development triangle."
        )
    elif diagonal_only:
        shape = COVERAGE_DIAGONAL
        warnings.append(
            "Every valuation reports only the claims that occurred in that same period, so "
            "no claim's outstanding is ever observed twice and no development can be "
            "measured. A reported triangle needs the full open-claim inventory at each "
            "valuation date."
        )
    elif len(valued_diagonals) >= n:
        shape = COVERAGE_INVENTORY
    else:
        shape = COVERAGE_SPARSE
        warnings.append(
            f"Not sufficient information for a {grain.label.lower()} reported triangle: "
            f"outstanding is valued at only {len(valued_diagonals)} of {n} development "
            f"periods ({ratio:.0%} of cells)."
        )

    return ValuationCoverage(
        grain=grain.key,
        valuation_periods=[grain.label_for(p) for p in valuations],
        accident_span=spans,
        shape=shape,
        covered_cells=covered,
        coverage_ratio=ratio,
        warnings=warnings,
    )


def _score(counts: pd.DataFrame) -> Credibility:
    n_acc, n_dev = counts.shape
    values = counts.to_numpy()
    # Score the triangle that is actually shown. `build_triangle` masks everything below the
    # leading diagonal to NaN — a payment recorded after the valuation date lands there — so
    # counting those rows here would describe a denser, higher-volume triangle than the one on
    # screen, and can put "cells populated" above 100%.
    if n_acc and n_dev:
        observed = (np.arange(n_acc)[:, None] + np.arange(n_dev)[None, :]) < n_acc
        values = np.where(observed, values, 0)
    upper = sum(1 for i in range(n_acc) for j in range(n_dev) if i + j < n_acc)
    non_empty = int((values > 0).sum())
    claims = int(values.sum())
    populated = values[values > 0]
    median = float(np.median(populated)) if populated.size else 0.0

    # The sparsest development column, measured against the cells that column CAN hold —
    # column j has only n_acc - j observable cells. Ranking on the raw count instead always
    # named the far-right corner, where one or two cells is the shape of a triangle rather
    # than a fact about the data, and said nothing about the columns that carry the factors.
    sparsest = None
    if n_dev and n_acc:
        col_non_empty = (values > 0).sum(axis=0)
        best = None
        for j in range(n_dev):
            observable = n_acc - j
            if observable <= 0:
                continue
            ratio = int(col_non_empty[j]) / observable
            if best is None or ratio < best[0]:
                best = (ratio, j, observable)
        if best is not None:
            _, idx, observable = best
            sparsest = {
                "index": idx,
                "non_empty": int(col_non_empty[idx]),
                "observable": int(observable),
            }

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


def _outstanding_by_cell(
    os_frame: pd.DataFrame | None,
    *,
    axis,
    columns: list[int],
    grain: PeriodGrain,
    accident_column: str,
    as_at_column: str,
    amount_column: str,
) -> list[list[float]]:
    """Outstanding per (accident index, development index), as a plain grid of floats.

    ``dev = valuation period − accident period``: each valuation date supplies one diagonal.
    Amounts that fail to parse become 0.0 rather than NaN — a row we cannot read must not
    silently empty a cell that other rows populate. Whether the cell is *reportable* at all is
    :class:`ValuationCoverage`'s decision, not this function's.
    """
    n = len(axis)
    grid = [[0.0] * len(columns) for _ in range(n)]
    if os_frame is None or os_frame.empty:
        return grid
    if as_at_column not in os_frame.columns or accident_column not in os_frame.columns:
        return grid

    work = os_frame.copy()
    for col in (accident_column, as_at_column):
        work[col] = pd.to_datetime(work[col], errors="coerce")
    work = work.dropna(subset=[accident_column, as_at_column])
    if work.empty:
        return grid

    accident = work[accident_column].dt.to_period(grain.period_alias)
    valuation = work[as_at_column].dt.to_period(grain.period_alias)
    amounts = pd.to_numeric(work.get(amount_column), errors="coerce").fillna(0.0)

    position = {period: i for i, period in enumerate(axis)}
    dev = (valuation - accident).apply(lambda x: x.n)
    for period, development, amount in zip(accident, dev, amounts):
        i = position.get(period)
        if i is None or development < 0 or development >= len(columns):
            continue
        grid[i][development] += float(amount)
    return grid


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
    basis: str = BASIS_PAID,
    os_frame: pd.DataFrame | None = None,
    as_at_column: str = "As at",
    os_amount_column: str = "Amount",
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
            warnings=warnings + [
                "No payments fall inside the experience period."
                if basis == BASIS_REPORTED
                # On the reported basis the wording matters: outstanding may well exist for
                # this slice, and "no claims" would wrongly suggest otherwise. The triangle
                # is still empty, because a reported triangle develops paid plus case
                # reserves and there is no paid side to develop.
                else "No claims fall inside the experience period."
            ],
            basis=basis,
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

    coverage = None
    if basis == BASIS_REPORTED:
        coverage = valuation_coverage(
            os_frame, grain=grain, start=start, end=end,
            as_at_column=as_at_column, accident_column=accident_column,
        )
        warnings.extend(coverage.warnings)
        # Outstanding is a BALANCE at a valuation date, not a flow, so it is added to the
        # cumulative paid at that maturity rather than accumulated across development.
        outstanding = _outstanding_by_cell(
            os_frame, axis=axis, columns=columns, grain=grain,
            accident_column=accident_column, as_at_column=as_at_column,
            amount_column=os_amount_column,
        )
        covered = set(coverage.covered_cells)
        for i in range(n):
            for j in range(n - i):
                if (i, j) in covered:
                    cumulative.iat[i, j] = cumulative.iat[i, j] + outstanding[i][j]
                else:
                    # Unknown, not nil. A cell outside every valued diagonal was never
                    # reported, and treating it as paid-only is what produces a sawtooth
                    # whose factors measure the valuation calendar instead of development.
                    cumulative.iat[i, j] = np.nan

    age_to_age = pd.DataFrame(np.nan, index=cumulative.index, columns=columns[:-1] or [0])
    for j in range(cumulative.shape[1] - 1):
        cur = cumulative.iloc[:, j]
        nxt = cumulative.iloc[:, j + 1]
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = nxt / cur.replace(0, np.nan)
        age_to_age.iloc[:, j] = ratio

    return TriangleSet(
        grain=grain.key,
        basis=basis,
        coverage=coverage,
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
