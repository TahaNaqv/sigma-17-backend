"""IBNR Summary is located by shape, not by hardcoded column index.

Module 1 appends `RESERVINGCLASS, Payment/Recovery, GROSS/RI` after a reserve
block whose width varies (a `CDF` column was added; a large-claim add-back run
writes two extra base columns), so the reader must not assume the pre-CDF
21-column layout. It also must not assume the book contains salvage rows.
"""

import pandas as pd
import pytest

from module2_engine.engine import _ibnr_pivot, _ibnr_summary_frames

# The pre-CDF layout the historic positional reader ([0, 18, 19, 20]) was written for.
_LEGACY_HEADERS = [
    "Accident_Period", "EP", "Paid Claims", "OS Claims", "Reported Claims", "Reported LR",
    "Implied LR", "Paid CDF", "Reported CDF", "Paid CL Ultimate", "Reported CL Ultimate",
    "ELR Ultimate", "Paid BF Ultimate", "Reported BF Ultimate", "Selected Method",
    "Ultimate Claims", "IBNR", "ULR",
    "RESERVINGCLASS", "Payment/Recovery", "GROSS/RI",
]


def _row(*, paid_cdf, ibnr, klass, head, gross_ri, extra=()):
    base = ["2024-Q1", 0, 0, 0, 0, 0, None, paid_cdf, 0, 0, 0, 0, 0, 0, "Paid CL", 0, ibnr, 0]
    return base + list(extra) + [klass, head, gross_ri]


def _frame(headers, rows):
    return pd.DataFrame(rows, columns=headers)


def test_legacy_21_column_layout_resolves_to_the_historic_positions():
    df = _frame(_LEGACY_HEADERS, [_row(paid_cdf=2.0, ibnr=100, klass="Motor", head="Payment", gross_ri="GROSS")])
    names, values = _ibnr_summary_frames(df)
    assert list(names.columns) == ["Accident_Period", "RESERVINGCLASS", "Payment/Recovery", "GROSS/RI"]
    assert names.iloc[0].tolist() == ["2024-Q1", "Motor", "Payment", "GROSS"]
    assert values.iloc[0].tolist() == [2.0, 100]


def test_cdf_column_shifts_the_metadata_without_misreading_it():
    """The regression: Module 1 grew a `CDF` column, pushing the metadata to 19/20/21."""
    headers = _LEGACY_HEADERS[:18] + ["CDF"] + _LEGACY_HEADERS[18:]
    df = _frame(headers, [_row(paid_cdf=2.0, ibnr=100, klass="Motor", head="Payment", gross_ri="GROSS", extra=[2.0])])
    names, values = _ibnr_summary_frames(df)
    assert names.iloc[0].tolist() == ["2024-Q1", "Motor", "Payment", "GROSS"]
    assert values.iloc[0].tolist() == [2.0, 100]


def test_mislabelled_header_row_is_read_by_position_not_by_name():
    """Some Module 1 builds drop the `CDF` label and repeat `GROSS/RI`, leaving
    every metadata header one column left of the data it names. The columns
    themselves are still in the documented order, so the tail wins."""
    headers = _LEGACY_HEADERS[:18] + ["RESERVINGCLASS", "Payment/Recovery", "GROSS/RI", "GROSS/RI.1"]
    df = _frame(headers, [_row(paid_cdf=2.0, ibnr=100, klass="Motor", head="Payment", gross_ri="GROSS", extra=[2.0])])
    names, _ = _ibnr_summary_frames(df)
    assert names.iloc[0].tolist() == ["2024-Q1", "Motor", "Payment", "GROSS"]


def test_missing_measure_column_names_the_sheet_and_the_column():
    df = _frame(_LEGACY_HEADERS, [_row(paid_cdf=2.0, ibnr=100, klass="Motor", head="Payment", gross_ri="GROSS")])
    with pytest.raises(ValueError, match=r"'IBNR Summary' is missing required columns: IBNR"):
        _ibnr_summary_frames(df.drop(columns=["IBNR"]))


def _with_heads(heads):
    """One IBNR row per head of damage, all on the same (period, class, GROSS/RI)."""
    rows = [
        _row(paid_cdf=2.0, ibnr=10 * i, klass="Motor", head=head, gross_ri="GROSS")
        for i, head in enumerate(heads, start=1)
    ]
    return _frame(_LEGACY_HEADERS, rows)


def test_book_without_salvage_rows_still_yields_both_value_columns():
    """A `Payment`-only book used to raise a bare pandas length mismatch, which
    the task surfaced as the unactionable "check workbook formats" message."""
    piv = _ibnr_pivot(_with_heads(["Payment"]))
    assert list(piv.columns) == [
        "Accident_Period", "RESERVINGCLASS", "GROSS/RI", "Payment", "S&S", "Paid CDF",
    ]
    assert piv.loc[0, "Payment"] == 10
    assert piv.loc[0, "S&S"] == 0


def test_salvage_and_subrogation_both_fold_into_the_ss_column():
    piv = _ibnr_pivot(_with_heads(["Payment", "Salvage", "Subrogation"]))
    assert piv.loc[0, "Payment"] == 10
    assert piv.loc[0, "S&S"] == 20 + 30
    assert piv.loc[0, "Paid CDF"] == 2.0


def test_unrecognised_head_of_damage_is_named_rather_than_silently_dropped():
    with pytest.raises(ValueError, match=r"unrecognised 'Payment/Recovery' values: Windscreen"):
        _ibnr_pivot(_with_heads(["Payment", "Windscreen"]))
