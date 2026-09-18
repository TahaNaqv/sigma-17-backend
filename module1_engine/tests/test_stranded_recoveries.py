"""Recovery rows the amount substitution will not reach.

`import_data` substitutes `AMOUNTRECOVERED` for `AMOUNTPAID` only where `POLICYCLASS` is exactly
"Motor". The 2016-2017 reference book spells that column "Motor Insurance", so 483 salvage rows
with a fully populated `AMOUNTRECOVERED` contribute nothing to any triangle — and nothing told
anybody. The client's current extract spells it "Motor", so the same rows ARE picked up today:
the behaviour turns on a spelling, and `class_aliases` normalises reserving class but nothing
normalises this one.

Detected from the data rather than the spelling, and reported rather than corrected — whether
recoveries belong in the paid triangle is an actuarial decision that moves booked numbers.
"""

from pathlib import Path

import pandas as pd
import pytest

from processing.services.preflight import _stranded_recoveries

CLAIMS_PAID = (
    Path(__file__).resolve().parents[2] / "benchmarks" / "fixtures" / "summary_ref" / "claims_paid"
)


def _rows(policy_class, head="Salvage", paid=None, recovered=5000.0, n=1):
    return pd.DataFrame([
        {
            "POLICYCLASS": policy_class,
            "HEADOFDAMAGE": head,
            "AMOUNTPAID": paid,
            "AMOUNTRECOVERED": recovered,
        }
    ] * n)


def test_a_recovery_row_the_substitution_will_miss_is_reported():
    found = _stranded_recoveries(_rows("Motor Insurance", n=3))
    assert found is not None
    assert found["rows"] == 3
    assert found["policy_classes"] == {"Motor Insurance"}
    assert found["heads"] == {"Salvage"}


def test_the_spelling_the_substitution_expects_is_not_reported():
    assert _stranded_recoveries(_rows("Motor")) is None


def test_a_recovery_row_that_also_has_a_paid_amount_is_not_stranded():
    """It contributes its paid amount, so nothing is lost and nothing needs saying."""
    assert _stranded_recoveries(_rows("Motor Insurance", paid=1000.0)) is None


def test_an_ordinary_payment_is_not_a_recovery():
    assert _stranded_recoveries(_rows("Motor Insurance", head="Payment")) is None


def test_a_row_with_no_recovered_amount_is_not_stranded():
    assert _stranded_recoveries(_rows("Motor Insurance", recovered=None)) is None


def test_a_zero_recovery_is_not_worth_reporting():
    """It contributes nothing either way, so flagging it is noise. On the reference book 1,633
    of the 1,645 structurally-matching rows are exactly this."""
    assert _stranded_recoveries(_rows("Motor Insurance", recovered=0.0)) is None


def test_missing_columns_are_tolerated_rather_than_raising():
    assert _stranded_recoveries(pd.DataFrame({"POLICYCLASS": ["Motor"]})) is None
    assert _stranded_recoveries(None) is None
    assert _stranded_recoveries(pd.DataFrame()) is None


@pytest.mark.skipif(not CLAIMS_PAID.is_dir(), reason="reference fixture not available")
def test_the_reference_book_strands_twelve_recovery_rows_worth_real_money():
    """Pinned against the real file.

    1,645 rows match the structural pattern, but 1,633 of them carry a recovered amount of
    zero and lose nothing. Twelve carry money — 1,743,541 of it — spread across several
    policy classes. Note that NO row in this book spells POLICYCLASS 'Motor', so the
    substitution never fires anywhere in it.
    """
    import pandas as pd

    frames = [pd.read_excel(f) for f in sorted(CLAIMS_PAID.glob("*.xlsx"))]
    found = _stranded_recoveries(pd.concat(frames, ignore_index=True))
    assert found is not None
    assert found["rows"] == 12
    assert found["amount"] == pytest.approx(1_743_541, abs=1)
    assert found["heads"] == {"Salvage"}
