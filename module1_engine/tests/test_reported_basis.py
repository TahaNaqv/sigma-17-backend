"""The reported (incurred) triangle, and the cells it must refuse to fill.

Outstanding is a balance at a valuation date, so each valuation supplies one diagonal. The rule
this file pins is the one that keeps the triangle honest:

    a cell inside a valued diagonal with no outstanding is a genuine zero — every claim closed;
    a cell outside every valued diagonal is unknown and stays null;
    an age-to-age factor requires BOTH of its cells to be valued.

Treating an unvalued cell as paid-only is what produced the sawtooth described in
`docs/TRIANGLE_REPORTED_AND_EP_PLAN.md` §1B — a triangle whose factors measure where the client's
valuation dates fall rather than how claims develop.
"""

import numpy as np
import pandas as pd
import pytest

from core.grain import MONTHLY, QUARTERLY, YEARLY
from module1_engine.triangles import (
    BASIS_PAID,
    BASIS_REPORTED,
    COVERAGE_INVENTORY,
    COVERAGE_SPARSE,
    build_triangle,
)

START, END = "2021-01-01", "2024-12-31"
QUARTERS = [f"{y}Q{q}" for y in range(2021, 2025) for q in range(1, 5)]


def _paid(amount=1000.0):
    """One payment in every accident quarter, at development 0."""
    return pd.DataFrame([
        {
            "LOSSDATE": pd.Period(q, freq="Q").start_time,
            "PAYMENTDATE": pd.Period(q, freq="Q").start_time,
            "Amount": amount,
        }
        for q in QUARTERS
    ])


def _os(valuations, amount=500.0):
    """A full inventory: every valuation lists every accident quarter up to it."""
    return pd.DataFrame([
        {
            "LOSSDATE": pd.Period(q, freq="Q").start_time,
            "As at": pd.Timestamp(v),
            "Amount": amount,
        }
        for v in valuations
        for q in QUARTERS
        if pd.Period(q, freq="Q") <= pd.Period(pd.Timestamp(v), freq="Q")
    ])


ANNUAL = ["2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31"]
QUARTERLY_VALUATIONS = [pd.Period(q, freq="Q").end_time for q in QUARTERS]


def _grid(triangle, block="cumulative"):
    return np.array(triangle.to_dict()[block], dtype=float)


def test_the_paid_basis_is_untouched_by_the_new_parameter():
    plain = build_triangle(_paid(), grain=QUARTERLY, start=START, end=END)
    explicit = build_triangle(_paid(), grain=QUARTERLY, start=START, end=END, basis=BASIS_PAID)
    assert plain.basis == BASIS_PAID
    assert plain.coverage is None
    np.testing.assert_array_equal(_grid(plain), _grid(explicit))


def test_outstanding_is_added_as_a_balance_not_accumulated():
    """A case reserve standing at a valuation is a level, so it is added to the cumulative paid
    at that maturity. Accumulating it across development would count the same reserve twice."""
    triangle = build_triangle(
        _paid(), grain=QUARTERLY, start=START, end=END,
        basis=BASIS_REPORTED, os_frame=_os(QUARTERLY_VALUATIONS),
    )
    grid = _grid(triangle)
    # Accident 2021-Q1: paid 1000 at dev 0 and flat after; 500 of outstanding at each valuation.
    assert grid[0][0] == pytest.approx(1500.0)
    assert grid[0][1] == pytest.approx(1500.0)
    assert grid[0][2] == pytest.approx(1500.0)


def test_quarterly_valuations_give_a_full_inventory_and_real_factors():
    triangle = build_triangle(
        _paid(), grain=QUARTERLY, start=START, end=END,
        basis=BASIS_REPORTED, os_frame=_os(QUARTERLY_VALUATIONS),
    )
    assert triangle.coverage.shape == COVERAGE_INVENTORY
    assert triangle.coverage.supports_factors is True
    assert np.isfinite(_grid(triangle, "age_to_age")).sum() > 0


def test_annual_valuations_at_a_quarterly_grain_leave_uncovered_cells_null():
    """The client's production shape: four year-end valuations against sixteen quarters."""
    triangle = build_triangle(
        _paid(), grain=QUARTERLY, start=START, end=END,
        basis=BASIS_REPORTED, os_frame=_os(ANNUAL),
    )
    assert triangle.coverage.shape == COVERAGE_SPARSE
    grid = _grid(triangle)
    # 40 of the 136 upper-triangle cells are valued; the rest are unknown, not nil.
    assert int((~np.isnan(grid)).sum()) == 40
    covered = set(triangle.coverage.covered_cells)
    for i in range(16):
        for j in range(16 - i):
            assert np.isnan(grid[i][j]) != ((i, j) in covered)


def test_no_factor_spans_an_uncovered_cell():
    """The safety property. With valued diagonals four apart there is no adjacent pair, so the
    honest answer is no factors at all — not a sawtooth."""
    triangle = build_triangle(
        _paid(), grain=QUARTERLY, start=START, end=END,
        basis=BASIS_REPORTED, os_frame=_os(ANNUAL),
    )
    assert triangle.coverage.supports_factors is False
    assert np.isfinite(_grid(triangle, "age_to_age")).sum() == 0


def test_the_same_extract_is_usable_at_yearly():
    """Coverage is a property of (extract, grain), so the yearly view stays available on data
    that cannot support the booking grain."""
    triangle = build_triangle(
        _paid(), grain=YEARLY, start=START, end=END,
        basis=BASIS_REPORTED, os_frame=_os(ANNUAL),
    )
    assert triangle.coverage.shape == COVERAGE_INVENTORY
    assert int((~np.isnan(_grid(triangle))).sum()) == 10
    assert np.isfinite(_grid(triangle, "age_to_age")).sum() == 6


def test_monthly_needs_month_end_valuations():
    triangle = build_triangle(
        _paid(), grain=MONTHLY, start=START, end=END,
        basis=BASIS_REPORTED, os_frame=_os(ANNUAL),
    )
    assert triangle.coverage.shape == COVERAGE_SPARSE
    assert triangle.coverage.supports_factors is False


def test_a_closed_claim_inside_a_valued_diagonal_reads_zero_not_null():
    """The distinction the whole coverage model exists for: nothing outstanding at a valuation
    that DID report the cohort means the claims closed, which is data, not absence."""
    os_frame = _os(QUARTERLY_VALUATIONS)
    # Drop accident 2021-Q1 from the final valuation: reported, and nil.
    keep = ~(
        (os_frame["LOSSDATE"] == pd.Period("2021Q1", freq="Q").start_time)
        & (os_frame["As at"] == QUARTERLY_VALUATIONS[-1])
    )
    triangle = build_triangle(
        _paid(), grain=QUARTERLY, start=START, end=END,
        basis=BASIS_REPORTED, os_frame=os_frame[keep],
    )
    grid = _grid(triangle)
    assert not np.isnan(grid[0][15])
    assert grid[0][15] == pytest.approx(1000.0)  # paid only, no case reserve left


def test_missing_outstanding_data_refuses_rather_than_reporting_paid_as_reported():
    triangle = build_triangle(
        _paid(), grain=QUARTERLY, start=START, end=END,
        basis=BASIS_REPORTED, os_frame=None,
    )
    assert triangle.coverage.shape == "none"
    assert triangle.coverage.usable is False
    assert np.isnan(_grid(triangle)).all()


def test_the_warning_says_not_enough_information_rather_than_describing_gaps():
    """The client's own framing, and the right one: *"there may be some diagonal blank ... and
    that will be misleading, or we can say not sufficient information provided."* A triangle
    with three cells in four missing does not read as insufficient data — it reads as a
    triangle."""
    triangle = build_triangle(
        _paid(), grain=QUARTERLY, start=START, end=END,
        basis=BASIS_REPORTED, os_frame=_os(ANNUAL),
    )
    joined = " ".join(triangle.warnings)
    assert "Not sufficient information for a quarterly reported triangle" in joined
    assert "4 of 16 development periods" in joined


def test_sparse_coverage_is_refused_rather_than_drawn_with_gaps():
    """`usable` is inventory only: outstanding must be valued at every development period of
    the grain being asked for, or there is no reported triangle at that grain."""
    for grain in (MONTHLY, QUARTERLY):
        triangle = build_triangle(
            _paid(), grain=grain, start=START, end=END,
            basis=BASIS_REPORTED, os_frame=_os(ANNUAL),
        )
        assert triangle.coverage.shape == COVERAGE_SPARSE, grain.key
        assert triangle.coverage.usable is False, grain.key

    # The same extract carries a yearly triangle perfectly well.
    yearly = build_triangle(
        _paid(), grain=YEARLY, start=START, end=END,
        basis=BASIS_REPORTED, os_frame=_os(ANNUAL),
    )
    assert yearly.coverage.usable is True
