"""WP2 — UPR earning-method registry.

``test_registry_reproduces_all_three_historic_blocks`` IS the acceptance specification
(docs/UPR_METHOD_SELECTION_PLAN.md §1.3). It freezes the measurement that justified
shipping the registry with a pro-rata default: if it ever fails, the default has stopped
being bit-identical and that must be a deliberate, reviewed decision.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from module1_engine.engine import preprocess_data, preprocess_dates
from module1_engine.upr_methods import (
    EIGHTHS,
    FLAT_PERCENTAGE,
    FULL_PREMIUM_IN_PERIOD,
    METHOD_KEYS,
    METHODS,
    PRO_RATA_DAILY,
    SUM_OF_DIGITS,
    TWENTY_FOURTHS,
    UNGATED_METHODS,
    UprPolicy,
    UprRule,
    normalize_token,
    token_matches,
    unearned_fraction,
)

PREMIUM_DIR = Path(__file__).resolve().parents[2] / "benchmarks" / "fixtures" / "summary_ref" / "premium"

CAR = ["Contractors All Risks", "Erection All Risks"]


def _synthetic() -> pd.DataFrame:
    """Four policies with known geometry, so every formula can be hand-checked."""
    df = pd.DataFrame([
        # issued and incepting 2024-01-01, annual
        {"RESERVINGCLASS": "MOTOR", "PRODUCTTYPE": "PRIVATE CAR",
         "ISSUEDATE": "2024-01-01", "RiskStartDate": "2024-01-01",
         "RiskEndDate": "2024-12-31", "PREMIUMAMOUNT": 1000.0},
        # same, but issued a quarter later
        {"RESERVINGCLASS": "MOTOR", "PRODUCTTYPE": "PRIVATE CAR",
         "ISSUEDATE": "2024-04-01", "RiskStartDate": "2024-04-01",
         "RiskEndDate": "2025-03-31", "PREMIUMAMOUNT": 1000.0},
        # already expired at a 2024-06-30 valuation
        {"RESERVINGCLASS": "MARINE", "PRODUCTTYPE": "MARINE CARGO",
         "ISSUEDATE": "2023-01-01", "RiskStartDate": "2023-01-01",
         "RiskEndDate": "2023-12-31", "PREMIUMAMOUNT": 500.0},
        # same-day policy — Duration 1 after the +1, guards the divide-by-zero path
        {"RESERVINGCLASS": "ENGINEERING", "PRODUCTTYPE": "ERECTION ALL RISKS",
         "ISSUEDATE": "2024-06-30", "RiskStartDate": "2024-06-30",
         "RiskEndDate": "2024-06-30", "PREMIUMAMOUNT": 100.0},
    ])
    for c in ("ISSUEDATE", "RiskStartDate", "RiskEndDate"):
        df[c] = pd.to_datetime(df[c])
    df["Duration"] = (df["RiskEndDate"] - df["RiskStartDate"]).dt.days + 1
    return df


# ---------------------------------------------------------------------------
# The acceptance specification
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not PREMIUM_DIR.is_dir(), reason="reference fixture not available")
def test_registry_reproduces_all_three_historic_blocks():
    """§1.3 — the measurement that justified the default, frozen as a test.

    The historic code carried three copies of the same np.select, already drifted in two
    ways (marine class spelling; a 91-day vs calendar-quarter window). All three must be
    reproduced exactly by the default policy.
    """
    df = preprocess_data(str(PREMIUM_DIR))
    df["PREMIUMAMOUNT"] = pd.to_numeric(df["PREMIUMAMOUNT"], errors="coerce")
    preprocess_dates(df)
    df["Duration"] = pd.to_numeric(
        (df["RiskEndDate"] - df["RiskStartDate"]).dt.days + 1, errors="coerce"
    )
    ds = np.maximum(df["Duration"], 1)

    def historic(date, marine, window):
        prev = (date - pd.DateOffset(months=3)) if window == "m3" else (date - pd.Timedelta(days=91))
        cond = [
            (df["POLICYCLASS"] != marine) & (df["ISSUEDATE"] <= date) & (df["PRODUCTTYPE"].isin(CAR)),
            (df["POLICYCLASS"] != marine) & (df["ISSUEDATE"] <= date) & (~df["PRODUCTTYPE"].isin(CAR)),
            (df["POLICYCLASS"] == marine) & (df["ISSUEDATE"] <= date) & (df["ISSUEDATE"] > prev),
        ]
        ch = [
            1 - ((np.minimum(np.maximum((date - df["RiskStartDate"]).dt.days + 1, 0), df["Duration"]) ** 2) / (ds ** 2)),
            np.maximum(0, np.minimum(df["Duration"], (df["RiskEndDate"] - date).dt.days)) / ds,
            1,
        ]
        return np.select(cond, ch, default=0)

    worst = 0.0
    for date in pd.date_range("2016-03-31", "2019-12-31", freq="QE"):
        produced = unearned_fraction(df, date, UprPolicy())
        for marine, window in (("Marine cargo", "m3"), ("Marine Cargo", "m3"), ("Marine cargo", "d91")):
            worst = max(worst, float(np.abs(historic(date, marine, window) - produced).max()))
    assert worst == 0.0, f"default policy is no longer bit-identical (max diff {worst:.3e})"


@pytest.mark.skipif(not PREMIUM_DIR.is_dir(), reason="reference fixture not available")
def test_policy_none_equals_the_explicit_default():
    df = preprocess_data(str(PREMIUM_DIR))
    preprocess_dates(df)
    df["Duration"] = pd.to_numeric(
        (df["RiskEndDate"] - df["RiskStartDate"]).dt.days + 1, errors="coerce"
    )
    date = pd.Timestamp("2017-12-31")
    a = unearned_fraction(df, date, None)
    b = unearned_fraction(df, date, UprPolicy())
    c = unearned_fraction(df, date, UprPolicy.from_dicts([{"method": PRO_RATA_DAILY}]))
    assert np.abs(a - b).max() == 0.0
    assert np.abs(a - c).max() == 0.0


# ---------------------------------------------------------------------------
# Eligibility is a separate gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", METHOD_KEYS)
def test_a_policy_not_yet_issued_earns_nothing_under_every_method(method):
    """The historic `np.select(default=0)` gate, made explicit. Folding eligibility into
    each method would grant UPR to unissued policies — the §1.3 design correction."""
    df = _synthetic()
    date = pd.Timestamp("2023-06-30")  # before every ISSUEDATE except the expired one
    policy = UprPolicy.from_dicts([{"method": method, "params": {"percent": 0.5}}])
    frac = unearned_fraction(df, date, policy)
    not_yet = (df["ISSUEDATE"] > date).to_numpy()
    assert np.abs(frac[not_yet]).max() == 0.0


# ---------------------------------------------------------------------------
# Per-method behaviour
# ---------------------------------------------------------------------------


def test_pro_rata_is_linear_in_remaining_days():
    df = _synthetic().iloc[[0]]
    p = UprPolicy.from_dicts([{"method": PRO_RATA_DAILY}])
    # 2024-01-01..2024-12-31 = 366 days; at 2024-06-30 there are 184 days left.
    assert unearned_fraction(df, pd.Timestamp("2024-06-30"), p)[0] == pytest.approx(184 / 366, abs=1e-12)
    assert unearned_fraction(df, pd.Timestamp("2024-12-31"), p)[0] == pytest.approx(0.0)


def test_sum_of_digits_is_quadratic_and_reaches_zero_at_expiry():
    df = _synthetic().iloc[[0]]
    p = UprPolicy.from_dicts([{"method": SUM_OF_DIGITS}])
    mid = unearned_fraction(df, pd.Timestamp("2024-06-30"), p)[0]
    elapsed = 182  # (2024-06-30 - 2024-01-01).days + 1
    assert mid == pytest.approx(1 - (elapsed ** 2) / (366 ** 2), abs=1e-12)
    assert unearned_fraction(df, pd.Timestamp("2024-12-31"), p)[0] == pytest.approx(0.0)


@pytest.mark.parametrize("method", [PRO_RATA_DAILY, SUM_OF_DIGITS])
def test_duration_based_methods_self_gate_on_expiry(method):
    """§1.4 — these reach zero of their own accord; the term-based ones do not."""
    df = _synthetic().iloc[[2]]  # expired 2023-12-31
    p = UprPolicy.from_dicts([{"method": method}])
    assert unearned_fraction(df, pd.Timestamp("2024-06-30"), p)[0] == pytest.approx(0.0)


def test_eighths_does_not_self_gate_on_expiry():
    """The reason upr_guard exists. An expired policy still earns 1/8ths weight."""
    df = _synthetic().iloc[[2]]
    p = UprPolicy.from_dicts([{"method": EIGHTHS}])
    # Issued 2023-01-01, valued 2023-06-30 -> 1 quarter elapsed -> (7-2)/8
    assert unearned_fraction(df, pd.Timestamp("2023-06-30"), p)[0] == pytest.approx(5 / 8)


def test_eighths_steps_down_by_two_eighths_a_quarter():
    df = _synthetic().iloc[[0]]  # issued 2024-01-01
    p = UprPolicy.from_dicts([{"method": EIGHTHS}])
    expected = [7 / 8, 5 / 8, 3 / 8, 1 / 8, 0.0]
    for i, date in enumerate(pd.date_range("2024-03-31", periods=5, freq="QE")):
        assert unearned_fraction(df, date, p)[0] == pytest.approx(expected[i]), date


def test_twenty_fourths_steps_down_by_two_twenty_fourths_a_month():
    df = _synthetic().iloc[[0]]
    p = UprPolicy.from_dicts([{"method": TWENTY_FOURTHS}])
    assert unearned_fraction(df, pd.Timestamp("2024-01-31"), p)[0] == pytest.approx(23 / 24)
    assert unearned_fraction(df, pd.Timestamp("2024-12-31"), p)[0] == pytest.approx(1 / 24)
    assert unearned_fraction(df, pd.Timestamp("2025-01-31"), p)[0] == pytest.approx(0.0)


def test_full_premium_in_period_uses_a_calendar_quarter_not_91_days():
    """§1.1 — the historic run-off loop used Timedelta(days=91), which disagrees with a
    calendar quarter in three quarters out of four. The calendar reading wins."""
    df = _synthetic().iloc[[1]]  # issued 2024-04-01
    p = UprPolicy.from_dicts([{"method": FULL_PREMIUM_IN_PERIOD}])
    # 2024-06-30 minus 3 calendar months is 2024-03-30, so 2024-04-01 is inside.
    assert unearned_fraction(df, pd.Timestamp("2024-06-30"), p)[0] == pytest.approx(1.0)
    # A quarter later it has fallen out of the window.
    assert unearned_fraction(df, pd.Timestamp("2024-09-30"), p)[0] == pytest.approx(0.0)


def test_flat_percentage_is_constant_for_eligible_rows():
    df = _synthetic()
    p = UprPolicy.from_dicts([{"method": FLAT_PERCENTAGE, "params": {"percent": 0.4}}])
    frac = unearned_fraction(df, pd.Timestamp("2024-06-30"), p)
    eligible = (df["ISSUEDATE"] <= pd.Timestamp("2024-06-30")).to_numpy()
    assert np.allclose(frac[eligible], 0.4)


def test_same_day_policy_does_not_divide_by_zero():
    df = _synthetic().iloc[[3]]  # RiskStart == RiskEnd
    for method in (PRO_RATA_DAILY, SUM_OF_DIGITS):
        p = UprPolicy.from_dicts([{"method": method}])
        value = unearned_fraction(df, pd.Timestamp("2024-06-30"), p)[0]
        assert np.isfinite(value)


@pytest.mark.parametrize("method", METHOD_KEYS)
def test_malformed_dates_cannot_escape_the_zero_to_one_range(method):
    """Regression for a latent defect inherited from the historic formula: a row with
    RiskEndDate before RiskStartDate drove `sum_of_digits` to about -132,000, which would
    have flowed straight into UPR. No reference row is malformed, so no golden caught it."""
    df = _synthetic().iloc[[0]].copy()
    df["RiskEndDate"] = pd.Timestamp("2023-01-01")   # before RiskStartDate
    df["Duration"] = (df["RiskEndDate"] - df["RiskStartDate"]).dt.days + 1
    p = UprPolicy.from_dicts([{"method": method, "params": {"percent": 0.5}}])
    value = unearned_fraction(df, pd.Timestamp("2024-06-30"), p)[0]
    assert 0.0 <= value <= 1.0, f"{method} produced {value}"


def test_nan_duration_yields_zero_not_nan():
    df = _synthetic().iloc[[0]].copy()
    df["Duration"] = np.nan
    p = UprPolicy.from_dicts([{"method": PRO_RATA_DAILY}])
    assert unearned_fraction(df, pd.Timestamp("2024-06-30"), p)[0] == 0.0


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_a_product_rule_beats_a_class_rule_beats_the_catch_all():
    policy = UprPolicy.from_dicts([
        {"method": PRO_RATA_DAILY},
        {"method": SUM_OF_DIGITS, "reserving_class": "ENGINEERING"},
        {"method": EIGHTHS, "reserving_class": "ENGINEERING",
         "product_type": "erection", "match_mode": "contains"},
    ])
    assert policy.resolve("MOTOR", "PRIVATE CAR").method == PRO_RATA_DAILY
    assert policy.resolve("ENGINEERING", "BOILER").method == SUM_OF_DIGITS
    assert policy.resolve("ENGINEERING", "ERECTION ALL RISKS").method == EIGHTHS


def test_contains_matching_catches_the_real_product_strings():
    """The literal-match failure that made the historic branches dead: the code looked for
    'Contractors All Risks' while the data holds \"CONTRACTORS'ALL RISK\"."""
    policy = UprPolicy.from_dicts([
        {"method": PRO_RATA_DAILY},
        {"method": SUM_OF_DIGITS, "product_type": "all risk", "match_mode": "contains"},
    ])
    for product in ("CONTRACTORS'ALL RISK", "Contractors All Risks", "contractors all risk"):
        assert policy.resolve("ENGINEERING", product).method == SUM_OF_DIGITS


def test_matching_is_case_and_punctuation_insensitive():
    assert normalize_token("  CONTRACTORS'ALL   RISK ") == "contractors all risk"
    assert token_matches("marine cargo export", "marine cargo", "contains")
    assert token_matches("marine cargo export", "marine", "prefix")
    assert not token_matches("marine cargo export", "cargo", "prefix")


def test_an_unmatched_class_falls_back_to_pro_rata():
    policy = UprPolicy.from_dicts([{"method": EIGHTHS, "reserving_class": "MOTOR"}])
    assert policy.resolve("SOMETHING ELSE", "").method == PRO_RATA_DAILY


def test_priority_breaks_a_specificity_tie():
    policy = UprPolicy.from_dicts([
        {"method": PRO_RATA_DAILY, "reserving_class": "MOTOR", "priority": 0},
        {"method": SUM_OF_DIGITS, "reserving_class": "MOTOR", "priority": 5},
    ])
    assert policy.resolve("MOTOR", "").method == SUM_OF_DIGITS


def test_an_unknown_method_is_rejected_at_construction():
    with pytest.raises(ValueError, match="Unknown UPR method"):
        UprRule(method="wishful_thinking")


def test_an_unknown_match_mode_is_rejected():
    with pytest.raises(ValueError, match="Unknown match mode"):
        UprRule(method=PRO_RATA_DAILY, match_mode="fuzzy")


def test_ungated_methods_are_exactly_the_term_based_ones():
    assert set(UNGATED_METHODS) == {EIGHTHS, TWENTY_FOURTHS}
    assert all(METHODS[k].self_gates_on_expiry for k in METHOD_KEYS if k not in UNGATED_METHODS)
