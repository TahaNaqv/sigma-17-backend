"""What an outstanding-claims extract can support, before any triangle is built.

These tests are deliberately free of the reference fixture: the shapes they pin are
properties of extract *structure*, and they must fail loudly on a machine that has no
client data. The one fixture-backed test is marked and skips cleanly.

The distinction under test throughout: a cell inside a valued diagonal with no outstanding
is a genuine zero; a cell outside every valued diagonal is unknown. Conflating them is what
produced the sawtooth described in `docs/TRIANGLE_REPORTED_AND_EP_PLAN.md` §1B.
"""

from pathlib import Path

import pandas as pd
import pytest

from core.grain import MONTHLY, QUARTERLY, YEARLY
from module1_engine.triangles import (
    COVERAGE_DIAGONAL,
    COVERAGE_INVENTORY,
    COVERAGE_NONE,
    COVERAGE_SINGLE,
    COVERAGE_SPARSE,
    valuation_coverage,
)

START, END = "2021-01-01", "2024-12-31"


def _os(rows):
    return pd.DataFrame(
        [{"LOSSDATE": loss, "As at": as_at, "AMOUNTOUTSTANDING": amt} for loss, as_at, amt in rows]
    )


def _inventory(valuations, accident_quarters):
    """Every listed valuation reports every accident quarter up to it — a real inventory."""
    rows = []
    for v in valuations:
        for a in accident_quarters:
            if pd.Period(a, freq="Q") <= pd.Period(v, freq="Q"):
                rows.append((pd.Period(a, freq="Q").start_time, pd.Timestamp(v), 1000.0))
    return _os(rows)


QUARTERS = [f"{y}Q{q}" for y in range(2021, 2025) for q in range(1, 5)]


def test_no_data_and_no_as_at_column_are_both_none():
    assert valuation_coverage(None, grain=QUARTERLY).shape == COVERAGE_NONE
    frame = pd.DataFrame({"LOSSDATE": ["2021-01-01"], "AMOUNTOUTSTANDING": [1.0]})
    assert valuation_coverage(frame, grain=QUARTERLY).shape == COVERAGE_NONE


def test_a_valuation_at_every_period_is_an_inventory():
    frame = _inventory([f"{q[:4]}-{int(q[-1])*3:02d}-28" for q in QUARTERS], QUARTERS)
    c = valuation_coverage(frame, grain=QUARTERLY, start=START, end=END)
    assert c.shape == COVERAGE_INVENTORY
    assert c.coverage_ratio == 1.0
    assert c.supports_factors is True


def test_annual_valuations_against_a_quarterly_grain_are_sparse():
    """The client's production shape. Four year-end valuations, sixteen quarters."""
    frame = _inventory(["2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31"], QUARTERS)
    c = valuation_coverage(frame, grain=QUARTERLY, start=START, end=END)
    assert c.shape == COVERAGE_SPARSE
    # Diagonals 3, 7, 11, 15 carry 4 + 8 + 12 + 16 = 40 of the 136 upper-triangle cells.
    assert len(c.covered_cells) == 40
    assert round(c.coverage_ratio, 3) == round(40 / 136, 3)


def test_sparse_coverage_admits_no_factor_at_all():
    """The whole point. Valued diagonals four apart share no adjacent pair, so there is no
    age-to-age factor to form — which is the correct answer, not a gap to fill."""
    frame = _inventory(["2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31"], QUARTERS)
    c = valuation_coverage(frame, grain=QUARTERLY, start=START, end=END)
    assert c.supports_factors is False
    covered = set(c.covered_cells)
    assert not any((i, j + 1) in covered for (i, j) in covered)


def test_the_same_extract_is_an_inventory_at_yearly():
    """Coverage is a property of (extract, grain) — the grain the data supports is found,
    not configured. This is what unlocks the yearly reported triangle today."""
    frame = _inventory(["2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31"], QUARTERS)
    c = valuation_coverage(frame, grain=YEARLY, start=START, end=END)
    assert c.shape == COVERAGE_INVENTORY
    assert c.supports_factors is True


def test_current_period_only_extract_is_diagonal():
    """Each valuation lists only the claims that occurred in that same period, so no claim is
    ever seen twice and nothing develops. This is the shape the per-valuation accident spans
    exist to detect — and detecting it is what makes the "a valuation is complete" assumption
    below safe, because an extract that is not an inventory never reaches this branch."""
    rows = [
        (pd.Period(q, freq="Q").start_time, pd.Period(q, freq="Q").end_time, 1000.0)
        for q in QUARTERS
    ]
    c = valuation_coverage(_os(rows), grain=QUARTERLY, start=START, end=END)
    assert c.shape == COVERAGE_DIAGONAL
    assert c.usable is False
    assert c.supports_factors is False


def test_one_valuation_is_a_position_not_a_triangle():
    frame = _inventory(["2024-12-31"], QUARTERS)
    c = valuation_coverage(frame, grain=QUARTERLY, start=START, end=END)
    assert c.shape == COVERAGE_SINGLE
    assert c.supports_factors is False


def test_a_supplied_valuation_is_taken_as_a_complete_inventory():
    """The assumption, stated: a valuation date covers every accident period up to it, and a
    cohort absent from it had nothing outstanding.

    The alternative — requiring each valuation to list a cohort at least as old as the cell —
    was tried and rejected. In a healthy inventory the oldest open cohort drifts forward as old
    claims close, so that rule progressively nulled the oldest cells of a perfectly good
    triangle, and it could not distinguish "closed" from "not supplied" in any case. The guard
    against an extract that genuinely never reaches back is the `diagonal` classification,
    which refuses the basis outright."""
    frame = _inventory(["2024-12-31"], ["2023Q1", "2023Q2", "2023Q3", "2023Q4", "2024Q1"])
    c = valuation_coverage(frame, grain=QUARTERLY, start=START, end=END)
    assert c.covers(0, 15)   # accident 2021-Q1 at the 2024-Q4 valuation: reported, and nil
    assert c.covers(8, 7)
    # One valuation is still a position rather than a triangle, so nothing develops from it.
    assert c.shape == COVERAGE_SINGLE
    assert c.supports_factors is False


def test_coverage_is_reported_in_the_extract_s_own_terms():
    frame = _inventory(["2021-12-31", "2024-12-31"], QUARTERS)
    c = valuation_coverage(frame, grain=QUARTERLY, start=START, end=END)
    assert c.valuation_periods == ["2021-Q4", "2024-Q4"]
    assert c.accident_span["2021-Q4"] == ("2021-Q1", "2021-Q4")
    assert c.to_dict()["shape"] == COVERAGE_SPARSE


def test_monthly_grain_needs_month_end_valuations():
    frame = _inventory(["2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31"], QUARTERS)
    c = valuation_coverage(frame, grain=MONTHLY, start=START, end=END)
    assert c.shape == COVERAGE_SPARSE
    assert c.supports_factors is False


CLAIMS_OS = Path(__file__).resolve().parents[2] / "benchmarks" / "fixtures" / "summary_ref" / "claims_os"


@pytest.mark.skipif(not CLAIMS_OS.is_dir(), reason="reference fixture not available")
def test_the_reference_extract_is_diagonal_only():
    from module1_engine.engine import import_data

    frame = import_data(str(CLAIMS_OS), "AMOUNTOUTSTANDING", is_os=True)
    c = valuation_coverage(frame, grain=QUARTERLY, start="2016-01-01", end="2017-12-31")
    assert c.shape == COVERAGE_DIAGONAL
    assert c.valuation_periods == ["2017-Q1", "2017-Q2", "2017-Q3", "2017-Q4"]
