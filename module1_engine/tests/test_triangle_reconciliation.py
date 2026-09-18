"""The triangle on the page must be the triangle in the workbook.

Two implementations of the paid triangle exist and both are load-bearing:

* ``engine.calculate_incremental_triangle`` + ``calculate_cumulative_triangle`` build the
  ``Paid Claims Triangle`` sheet, whose Selected LDF row is what books;
* ``triangles.build_triangle`` builds the Triangle view, at any grain.

Nothing held them together until this file. An actuary who selects a factor on the page and
expects it to mean the same thing in the workbook is relying on an equality no test asserted.

They agree on every cell the workbook populates. They differ in one bounded, understood way,
pinned below: where a slice's ``Amount`` is entirely NaN the engine's ``+=`` propagates NaN into
the cell, while ``build_triangle`` coerces the amount to 0.0. The counts are asserted exactly, so
a *new* divergence fails here rather than surfacing as a page that disagrees with a booked number.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.grain import QUARTERLY
from module1_engine.triangles import build_triangle

CLAIMS_PAID = (
    Path(__file__).resolve().parents[2] / "benchmarks" / "fixtures" / "summary_ref" / "claims_paid"
)
START, END = pd.Timestamp("2016-01-01"), pd.Timestamp("2017-12-31")
SLICE_KEYS = ["RESERVINGCLASS", "HEADOFDAMAGE", "RI_TREATY_TYPE"]

pytestmark = pytest.mark.skipif(not CLAIMS_PAID.is_dir(), reason="reference fixture not available")


@pytest.fixture(scope="module")
def paid():
    from module1_engine.engine import import_data

    return import_data(str(CLAIMS_PAID), "AMOUNTPAID", is_os=False)


def _workbook_cumulative(slice_df) -> np.ndarray:
    from module1_engine.engine import (
        calculate_cumulative_triangle,
        calculate_incremental_triangle,
    )

    frame = calculate_cumulative_triangle(
        calculate_incremental_triangle(slice_df, START, END)
    )
    return frame.iloc[:, 1:].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)


def _page_cumulative(slice_df) -> np.ndarray:
    triangle = build_triangle(slice_df, grain=QUARTERLY, start=START, end=END)
    return np.array(triangle.to_dict()["cumulative"], dtype=float)


def _compare(paid):
    for keys, group in paid.groupby(SLICE_KEYS):
        yield keys, group, _workbook_cumulative(group), _page_cumulative(group)


def test_the_page_and_the_workbook_agree_wherever_the_workbook_has_a_number(paid):
    """The equality the actuary is relying on."""
    checked = 0
    for keys, _group, wb, pg in _compare(paid):
        assert wb.shape == pg.shape, keys
        populated = ~np.isnan(wb)
        assert np.allclose(wb[populated], pg[populated], atol=0.005), keys
        checked += int(populated.sum())
    assert checked > 500, "the comparison must actually cover cells"


def test_the_only_divergence_is_the_all_nan_amount_slices(paid):
    """Bounded and understood: the engine's `+=` propagates a NaN amount into the cell; the
    diagnostic coerces it to 0.0. It happens only where a slice carries no usable amount at
    all, so no populated cell is at stake."""
    diverging = {}
    for keys, group, wb, pg in _compare(paid):
        mismatched = np.isnan(wb) & ~np.isnan(pg)
        # The page must never be emptier than the workbook — that would hide booked money.
        assert not (~np.isnan(wb) & np.isnan(pg)).any(), keys
        if mismatched.any():
            assert np.all(pg[mismatched] == 0.0), keys
            assert group["Amount"].isna().all(), (
                f"{keys}: divergence outside an all-NaN slice"
            )
            diverging[keys] = int(mismatched.sum())

    # Every one is a Salvage head of damage: `AMOUNTPAID` is empty on recovery rows.
    assert all(hod == "Salvage" for _, hod, _ in diverging), diverging
    assert sum(diverging.values()) == 91
    assert len(diverging) == 7


def test_the_recovery_substitution_never_fires_on_this_book(paid):
    """Why those slices are empty at all, recorded so the cause is not rediscovered.

    `import_data` substitutes `AMOUNTRECOVERED` for `AMOUNTPAID` only when
    `POLICYCLASS == 'Motor'`. This book spells it 'Motor Insurance', so the substitution never
    fires and 483 salvage rows with a fully populated `AMOUNTRECOVERED` contribute nothing.
    Asserted, not fixed: changing it moves booked numbers and is the client's call.
    """
    salvage = paid[(paid.HEADOFDAMAGE == "Salvage") & (paid.RI_TREATY_TYPE == "GROSS")]
    motor = salvage[salvage.RESERVINGCLASS == "Motor Insurance"]
    assert len(motor) == 483
    assert motor["Amount"].isna().all()
    assert motor["AMOUNTRECOVERED"].notna().all()
    assert set(motor["POLICYCLASS"].unique()) == {"Motor Insurance"}


# ---------------------------------------------------------------------------
# The reported basis: where the page deliberately parts company with the sheet
# ---------------------------------------------------------------------------

QUARTERS = [f"{y}Q{q}" for y in range(2021, 2025) for q in range(1, 5)]
WINDOW = (pd.Timestamp("2021-01-01"), pd.Timestamp("2024-12-31"))


def _synthetic_paid():
    return pd.DataFrame([
        {
            "LOSSDATE": pd.Period(q, freq="Q").start_time,
            "PAYMENTDATE": pd.Period(q, freq="Q").start_time,
            "Amount": 1000.0,
        }
        for q in QUARTERS
    ])


def _synthetic_os(valuations):
    """A full open-claim inventory at each listed valuation date."""
    return pd.DataFrame([
        {
            "LOSSDATE": pd.Period(q, freq="Q").start_time,
            "As at": pd.Timestamp(v),
            "Amount": 500.0,
        }
        for v in valuations
        for q in QUARTERS
        if pd.Period(q, freq="Q") <= pd.Period(pd.Timestamp(v), freq="Q")
    ])


ANNUAL_VALUATIONS = ["2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31"]


def _workbook_reported(paid_df, os_df):
    """The sheet's construction: cumulative paid plus the outstanding triangle, with no notion
    of which cells a valuation actually covered."""
    from module1_engine.engine import (
        calculate_cumulative_triangle,
        calculate_incremental_triangle,
    )

    start, end = WINDOW
    cumulative = calculate_cumulative_triangle(
        calculate_incremental_triangle(paid_df, start, end)
    )
    outstanding = calculate_incremental_triangle(os_df, start, end, is_os=True)
    reported = cumulative.copy()
    reported.iloc[:, 1:] = reported.iloc[:, 1:] + outstanding.iloc[:, 1:]
    return reported.iloc[:, 1:].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)


def test_the_two_agree_on_every_cell_a_valuation_covers():
    """Where the data supports the cell, the page and the sheet compute the same incurred."""
    from module1_engine.triangles import BASIS_REPORTED

    paid_df, os_df = _synthetic_paid(), _synthetic_os(ANNUAL_VALUATIONS)
    start, end = WINDOW
    page = build_triangle(
        paid_df, grain=QUARTERLY, start=start, end=end,
        basis=BASIS_REPORTED, os_frame=os_df,
    )
    wb = _workbook_reported(paid_df, os_df)
    pg = np.array(page.to_dict()["cumulative"], dtype=float)

    covered = set(page.coverage.covered_cells)
    assert covered, "the fixture must actually cover some cells"
    for i, j in covered:
        assert wb[i][j] == pytest.approx(pg[i][j]), (i, j)


def test_the_sheet_fills_uncovered_cells_with_paid_alone_and_the_page_refuses_to():
    """The sawtooth, pinned.

    With annual valuations against a quarterly grain, three cells in four have no outstanding
    to add. The sheet leaves those as cumulative paid, so the column alternates between
    "paid + case reserves" and "paid alone" — and its age-to-age factors then measure where the
    valuation dates fall rather than how claims develop. The page leaves them null instead,
    which is why it can form no factor at all. That difference is the entire point of the
    coverage model, so it is asserted rather than assumed.
    """
    from module1_engine.triangles import BASIS_REPORTED

    paid_df, os_df = _synthetic_paid(), _synthetic_os(ANNUAL_VALUATIONS)
    start, end = WINDOW
    page = build_triangle(
        paid_df, grain=QUARTERLY, start=start, end=end,
        basis=BASIS_REPORTED, os_frame=os_df,
    )
    wb = _workbook_reported(paid_df, os_df)
    pg = np.array(page.to_dict()["cumulative"], dtype=float)

    covered = set(page.coverage.covered_cells)
    n = len(page.accident_labels)
    uncovered_upper = [
        (i, j) for i in range(n) for j in range(n - i) if (i, j) not in covered
    ]
    assert len(uncovered_upper) == 96  # 136 upper-triangle cells, 40 of them valued

    for i, j in uncovered_upper:
        assert np.isnan(pg[i][j]), (i, j)
        # The sheet shows a number there — cumulative paid, with no case reserve added.
        assert not np.isnan(wb[i][j]), (i, j)

    # And the consequence: the sheet yields factors from that sawtooth; the page yields none.
    page_factors = np.isfinite(np.array(page.to_dict()["age_to_age"], dtype=float)).sum()
    assert page_factors == 0
    assert page.coverage.supports_factors is False


def test_quarter_end_valuations_remove_the_divergence_entirely():
    """The fix is the client's data, not our arithmetic: value the extract at every quarter end
    and the two constructions agree cell for cell."""
    from module1_engine.triangles import BASIS_REPORTED, COVERAGE_INVENTORY

    quarterly_valuations = [pd.Period(q, freq="Q").end_time for q in QUARTERS]
    paid_df, os_df = _synthetic_paid(), _synthetic_os(quarterly_valuations)
    start, end = WINDOW
    page = build_triangle(
        paid_df, grain=QUARTERLY, start=start, end=end,
        basis=BASIS_REPORTED, os_frame=os_df,
    )
    assert page.coverage.shape == COVERAGE_INVENTORY

    wb = _workbook_reported(paid_df, os_df)
    pg = np.array(page.to_dict()["cumulative"], dtype=float)
    populated = ~np.isnan(wb)
    assert np.allclose(wb[populated], pg[populated], atol=0.005)
    assert np.isfinite(np.array(page.to_dict()["age_to_age"], dtype=float)).sum() > 0
