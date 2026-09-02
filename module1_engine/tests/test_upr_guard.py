"""WP2 — book-suitability guard for term-based UPR methods.

`eighths` / `twenty_fourths` weight by issue date alone and never consult the risk period.
On the client reference book they produce -243% and -429% UPR. The guard exists so they
cannot be selected by accident on a book like that.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from module1_engine.engine import preprocess_data, preprocess_dates
from module1_engine.upr_guard import BLOCK, OK, WARN, evaluate_book, evaluate_policy
from module1_engine.upr_methods import EIGHTHS, PRO_RATA_DAILY, SUM_OF_DIGITS, UprPolicy

PREMIUM_DIR = Path(__file__).resolve().parents[2] / "benchmarks" / "fixtures" / "summary_ref" / "premium"


@pytest.fixture(scope="module")
def reference_book():
    if not PREMIUM_DIR.is_dir():
        pytest.skip("reference fixture not available")
    df = preprocess_data(str(PREMIUM_DIR))
    df["PREMIUMAMOUNT"] = pd.to_numeric(df["PREMIUMAMOUNT"], errors="coerce")
    preprocess_dates(df)
    df["Duration"] = pd.to_numeric(
        (df["RiskEndDate"] - df["RiskStartDate"]).dt.days + 1, errors="coerce"
    )
    return df


def _clean_annual_book(n=200) -> pd.DataFrame:
    start = pd.Timestamp("2024-01-01")
    df = pd.DataFrame({
        "RESERVINGCLASS": ["MOTOR"] * n,
        "PRODUCTTYPE": ["PRIVATE CAR"] * n,
        "ISSUEDATE": [start + pd.Timedelta(days=i) for i in range(n)],
        "RiskStartDate": [start + pd.Timedelta(days=i) for i in range(n)],
        "RiskEndDate": [start + pd.Timedelta(days=i + 364) for i in range(n)],
        "PREMIUMAMOUNT": [1000.0] * n,
    })
    if n:
        df["Duration"] = (df["RiskEndDate"] - df["RiskStartDate"]).dt.days + 1
    else:
        # An empty frame has object-dtype date columns; give Duration the right dtype
        # so the fixture itself does not blow up before the guard is reached.
        df["Duration"] = pd.Series(dtype=float)
    return df


# ---------------------------------------------------------------------------
# The reference book must fail
# ---------------------------------------------------------------------------


def test_the_reference_book_blocks_eighths_on_negative_premium(reference_book):
    report = evaluate_book(reference_book, EIGHTHS, at_date=pd.Timestamp("2017-12-31"))
    assert report.blocked
    check = next(c for c in report.checks if c.key == "negative_premium")
    assert check.level == BLOCK
    # 699 of 14,791 rows carry negative premium — the measured cause of the -243%.
    assert check.value == pytest.approx(699 / 14791, rel=1e-3)
    assert "negative premium" in check.detail


def test_the_reference_book_warns_on_expired_policies(reference_book):
    report = evaluate_book(reference_book, EIGHTHS, at_date=pd.Timestamp("2017-12-31"))
    check = next(c for c in report.checks if c.key == "expired")
    assert check.level == WARN


def test_the_term_check_passes_because_the_book_LOOKS_suitable(reference_book):
    """The trap: 92.8% of policies run an annual term, so the homogeneity check passes.
    A method that looks applicable and is not is the dangerous case."""
    report = evaluate_book(reference_book, EIGHTHS, at_date=pd.Timestamp("2017-12-31"))
    check = next(c for c in report.checks if c.key == "term_homogeneity")
    assert check.level == OK


# ---------------------------------------------------------------------------
# Self-gating methods are never guarded
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", [PRO_RATA_DAILY, SUM_OF_DIGITS])
def test_duration_based_methods_are_not_guarded(reference_book, method):
    report = evaluate_book(reference_book, method, at_date=pd.Timestamp("2017-12-31"))
    assert report.level == OK
    assert report.checks == []


# ---------------------------------------------------------------------------
# A genuinely suitable book must pass
# ---------------------------------------------------------------------------


def test_a_clean_annual_book_passes():
    report = evaluate_book(
        _clean_annual_book(), EIGHTHS, at_date=pd.Timestamp("2024-06-30")
    )
    assert not report.blocked
    assert report.level in (OK, WARN)


def test_a_mixed_term_book_is_blocked():
    df = _clean_annual_book(100)
    # Half the book becomes 30-day cover — the homogeneity assumption fails.
    df.loc[:49, "RiskEndDate"] = df.loc[:49, "RiskStartDate"] + pd.Timedelta(days=29)
    df["Duration"] = (df["RiskEndDate"] - df["RiskStartDate"]).dt.days + 1
    report = evaluate_book(df, EIGHTHS, at_date=pd.Timestamp("2024-06-30"))
    assert report.blocked
    assert next(c for c in report.checks if c.key == "term_homogeneity").level == BLOCK


def test_a_small_share_of_negative_premium_is_tolerated():
    df = _clean_annual_book(1000)
    df.loc[:4, "PREMIUMAMOUNT"] = -1000.0     # 0.5%, under the 1% threshold
    report = evaluate_book(df, EIGHTHS, at_date=pd.Timestamp("2024-06-30"))
    assert next(c for c in report.checks if c.key == "negative_premium").level == OK


# ---------------------------------------------------------------------------
# Policy-level evaluation
# ---------------------------------------------------------------------------


def test_a_method_confined_to_a_clean_class_is_judged_on_that_class_only(reference_book):
    """A rule scoped to one class must not be blocked by the rest of the book."""
    policy = UprPolicy.from_dicts([
        {"method": PRO_RATA_DAILY},
        {"method": EIGHTHS, "reserving_class": "D&O"},
    ])
    reports = evaluate_policy(reference_book, policy, at_date=pd.Timestamp("2017-12-31"))
    assert len(reports) == 1
    assert reports[0].rows_examined < len(reference_book)


def test_a_policy_using_only_self_gating_methods_produces_no_reports(reference_book):
    policy = UprPolicy.from_dicts([
        {"method": PRO_RATA_DAILY},
        {"method": SUM_OF_DIGITS, "reserving_class": "Engineering Insurance"},
    ])
    assert evaluate_policy(reference_book, policy, at_date=pd.Timestamp("2017-12-31")) == []


def test_an_empty_book_does_not_crash_the_guard():
    empty = _clean_annual_book(0)
    report = evaluate_book(empty, EIGHTHS, at_date=pd.Timestamp("2024-06-30"))
    assert report.level == OK
