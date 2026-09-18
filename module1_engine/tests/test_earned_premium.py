"""Earned premium at any grain, reconciled to the figure that is actually booked.

The whole trust argument for the monthly and yearly numbers is one equality: at quarterly, this
must reproduce `Reserve Summary.EP` to the cent. No workbook contains a monthly or yearly EP, so
nothing else can check them — if the quarterly figures agree and the grains partition the same
premium, the finer ones are the same calculation at a different resolution.
"""

from pathlib import Path

import pandas as pd
import pytest

from core.grain import MONTHLY, QUARTERLY, YEARLY
from module1_engine.earned_premium import earned_premium_by_period

PREMIUM = (
    Path(__file__).resolve().parents[2] / "benchmarks" / "fixtures" / "summary_ref" / "premium"
)
BOP, EOP = pd.Timestamp("2016-01-01"), pd.Timestamp("2017-12-31")

pytestmark = pytest.mark.skipif(not PREMIUM.is_dir(), reason="reference fixture not available")


@pytest.fixture(scope="module")
def premium():
    from module1_engine.engine import preprocess_data

    return preprocess_data(str(PREMIUM))


@pytest.fixture(scope="module")
def booked(premium):
    """`Reserve Summary.EP` as the engine builds it: the GEP_ columns, summed over UWY."""
    from module1_engine.engine import calculate_upr, summarize_upr_by_reserving_class

    df = calculate_upr(premium.copy(), bop=BOP, eop=EOP, upr_policy=None)
    _, additional, _, _ = summarize_upr_by_reserving_class(
        df, bop=BOP, eop=EOP, upr_policy=None
    )
    gep = [c for c in additional.columns if c.startswith("GEP_")]
    return (
        additional.melt(
            id_vars=["RESERVINGCLASS"], value_vars=gep, var_name="period", value_name="ep"
        )
        .assign(period=lambda d: d.period.str.replace("GEP_", "", regex=False))
        .groupby(["RESERVINGCLASS", "period"])["ep"]
        .sum()
    )


def test_quarterly_reproduces_the_booked_ep_to_the_cent(premium, booked):
    mine = earned_premium_by_period(premium, grain=QUARTERLY, bop=BOP, eop=EOP)
    gross = mine[mine.RI_TREATY_TYPE == "GROSS"].set_index(["RESERVINGCLASS", "period"])["ep"]
    joined = pd.concat([booked.rename("booked"), gross.rename("mine")], axis=1).dropna()

    # Guard against a vacuous pass: the comparison must have real money in it.
    assert len(joined) == 64
    assert int((joined.booked.abs() > 1).sum()) == 27
    assert joined.booked.sum() == pytest.approx(203_750_563.55, abs=0.01)

    assert (joined.mine - joined.booked).abs().max() < 0.005


def test_the_three_grains_partition_the_same_premium(premium):
    """Monthly, quarterly and yearly are one calculation at three resolutions, so over the same
    window they must total identically. A drift here means a period boundary is wrong."""
    totals = {}
    for grain in (MONTHLY, QUARTERLY, YEARLY):
        frame = earned_premium_by_period(premium, grain=grain, bop=BOP, eop=EOP)
        totals[grain.key] = frame[frame.RI_TREATY_TYPE == "GROSS"].ep.sum()
    assert totals["monthly"] == pytest.approx(totals["quarterly"], abs=0.01)
    assert totals["yearly"] == pytest.approx(totals["quarterly"], abs=0.01)
    assert totals["quarterly"] == pytest.approx(203_750_563.55, abs=0.01)


def test_each_grain_reports_the_periods_the_window_contains(premium):
    for grain, expected in ((MONTHLY, 24), (QUARTERLY, 8), (YEARLY, 2)):
        frame = earned_premium_by_period(premium, grain=grain, bop=BOP, eop=EOP)
        assert frame["period"].nunique() == expected, grain.key


def test_periods_come_back_in_chronological_order(premium):
    frame = earned_premium_by_period(premium, grain=QUARTERLY, bop=BOP, eop=EOP)
    one_class = frame[
        (frame.RESERVINGCLASS == frame.RESERVINGCLASS.iloc[0])
        & (frame.RI_TREATY_TYPE == "GROSS")
    ]
    assert list(one_class["period"]) == [
        f"{y}-Q{q}" for y in (2016, 2017) for q in (1, 2, 3, 4)
    ]


def test_gross_and_ri_are_reported_separately(premium):
    frame = earned_premium_by_period(premium, grain=QUARTERLY, bop=BOP, eop=EOP)
    assert set(frame.RI_TREATY_TYPE.unique()) == {"GROSS", "RI"}
    # The two are different books of business, not a split of one total.
    gross = frame[frame.RI_TREATY_TYPE == "GROSS"].ep.sum()
    ri = frame[frame.RI_TREATY_TYPE == "RI"].ep.sum()
    assert gross != pytest.approx(ri)


def test_an_empty_frame_yields_an_empty_result_rather_than_raising():
    out = earned_premium_by_period(pd.DataFrame(), grain=QUARTERLY, bop=BOP, eop=EOP)
    assert list(out.columns) == ["RESERVINGCLASS", "RI_TREATY_TYPE", "period", "ep"]
    assert out.empty


def _booked_ep(premium, *, bop, eop):
    """`Reserve Summary.EP` for a run booked over `bop`..`eop`, summed over UWY."""
    from module1_engine.engine import calculate_upr, summarize_upr_by_reserving_class

    df = calculate_upr(premium.copy(), bop=bop, eop=eop, upr_policy=None)
    _, additional, _, _ = summarize_upr_by_reserving_class(
        df, bop=bop, eop=eop, upr_policy=None
    )
    gep = [c for c in additional.columns if c.startswith("GEP_")]
    return (
        additional.melt(
            id_vars=["RESERVINGCLASS"], value_vars=gep, var_name="period", value_name="ep"
        )
        .assign(period=lambda d: d.period.str.replace("GEP_", "", regex=False))
        .groupby("period")["ep"]
        .sum()
    )


def test_the_page_window_and_the_booking_window_agree_where_they_overlap(premium):
    """WP8.8, the earned-premium half.

    The page computes EP over the EXPERIENCE period, because it sits beside a triangle drawn
    on that axis. The workbook's `EP` column spans `bop`..`eop` and zero-fills outside it. On a
    run whose experience period is wider than its booking period — production job `3cb6aba9`
    is exactly that, 2021-2024 experience against a 2024 booking — the two therefore differ at
    the edges, and the difference is a window, not a disagreement about the arithmetic.

    Asserted as a relationship rather than equality: overlapping periods must match to the
    cent, and the extra periods must be the page's alone.
    """
    narrow_bop, narrow_eop = pd.Timestamp("2017-01-01"), pd.Timestamp("2017-12-31")
    booked = _booked_ep(premium, bop=narrow_bop, eop=narrow_eop)

    page = earned_premium_by_period(premium, grain=QUARTERLY, bop=BOP, eop=EOP)
    page_gross = page[page.RI_TREATY_TYPE == "GROSS"].groupby("period")["ep"].sum()

    booked_periods = set(booked.index)
    assert booked_periods == {"2017-Q1", "2017-Q2", "2017-Q3", "2017-Q4"}
    # The page covers the whole experience period.
    assert set(page_gross.index) == {f"{y}-Q{q}" for y in (2016, 2017) for q in (1, 2, 3, 4)}

    # Where both compute a figure, they compute the same figure — including the first booked
    # quarter, whose opening UPR is the day before `bop` either way.
    for period in sorted(booked_periods):
        assert page_gross[period] == pytest.approx(booked[period], abs=0.01), period

    # On THIS book the extra periods happen to be empty — the reference premium file, like its
    # claims file, is effectively a 2017 book — so the divergence here is structural rather
    # than material. It is material wherever premium is earned outside the booking window,
    # which the synthetic case below pins without depending on the fixture's shape.
    outside = page_gross[[p for p in page_gross.index if p.startswith("2016")]]
    assert (outside == 0).all()


def test_the_windows_diverge_materially_when_premium_is_earned_outside_the_booking_period():
    """Production job `3cb6aba9` books 2024 against a 2021-2024 experience period, so most of
    its earned premium falls outside the booked window. Built synthetically so the property is
    pinned regardless of what any fixture happens to contain."""
    premium = pd.DataFrame([
        {
            "POLICYNUMBER": f"P{i}",
            "POLICYSTARTDATE": pd.Timestamp(f"{year}-01-01"),
            "POLICYENDDATE": pd.Timestamp(f"{year}-12-31"),
            "RiskStartDate": pd.Timestamp(f"{year}-01-01"),
            "RiskEndDate": pd.Timestamp(f"{year}-12-31"),
            "ISSUEDATE": pd.Timestamp(f"{year}-01-01"),
            "RESERVINGCLASS": "Motor",
            "RI_TREATY_TYPE": "GROSS",
            "PREMIUMAMOUNT": 1_000_000.0,
            "COMMISSIONAMOUNT": 0.0,
        }
        for i, year in enumerate((2016, 2017))
    ])
    page = earned_premium_by_period(premium, grain=QUARTERLY, bop=BOP, eop=EOP)
    gross = page[page.RI_TREATY_TYPE == "GROSS"].groupby("period")["ep"].sum()

    earned_2016 = gross[[p for p in gross.index if p.startswith("2016")]].sum()
    earned_2017 = gross[[p for p in gross.index if p.startswith("2017")]].sum()
    # A run booking only 2017 would report the first of these as zero; the page reports both,
    # because both sit on the triangle it is drawn beside.
    assert earned_2016 == pytest.approx(1_000_000.0, abs=1.0)
    assert earned_2017 == pytest.approx(1_000_000.0, abs=1.0)
