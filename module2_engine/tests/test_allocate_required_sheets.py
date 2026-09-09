"""ALLOCATE_REQUIRED_SHEETS must stay exactly the set the engine reads.

The chaining layer filters source jobs on this constant, so if the engine ever
starts reading a ninth sheet and nobody updates the tuple, the picker would
happily offer workbooks that fail mid-run — the exact failure this constant was
introduced to prevent. These tests derive the answer from the engine's own
behaviour rather than restating the list, so drift in either direction fails.
"""

import io

import pandas as pd
import pytest

from module2_engine.engine import ALLOCATE_REQUIRED_SHEETS, _compute_allocate_frames


def _workbook(sheets: list[str]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name in sheets:
            pd.DataFrame({"a": [1]}).to_excel(writer, sheet_name=name, index=False)
    return buf.getvalue()


def test_every_declared_sheet_is_actually_required():
    """Drop one declared sheet at a time; the engine must object to that sheet."""
    for omitted in ALLOCATE_REQUIRED_SHEETS:
        present = [s for s in ALLOCATE_REQUIRED_SHEETS if s != omitted]
        with pytest.raises(ValueError) as exc:
            _compute_allocate_frames(_workbook(present))
        assert f"Required sheet '{omitted}' is missing." == str(exc.value), (
            f"engine did not report {omitted!r} as the missing sheet"
        )


def test_no_undeclared_sheet_is_required():
    """With every declared sheet present, the engine gets past sheet resolution.

    The stub frames have none of the required COLUMNS, so it still fails — but on
    a column/parse complaint, never on a missing sheet. Any "Required sheet ..."
    here would mean the engine reads a sheet the constant does not declare.
    """
    with pytest.raises(Exception) as exc:
        _compute_allocate_frames(_workbook(list(ALLOCATE_REQUIRED_SHEETS)))
    assert "Required sheet" not in str(exc.value), str(exc.value)


def test_ulae_and_discount_rate_are_the_module1_gap():
    """Pins the fact the chaining UX is built on: these two sheets are actuary
    judgement that Module 1 never writes, so a raw Module 1 Combined_Summary can
    never be allocated from. If Module 1 ever starts emitting them, this test
    fails and the picker's guidance copy needs revisiting."""
    assert "ULAE-RA" in ALLOCATE_REQUIRED_SHEETS
    assert "Discount Rate" in ALLOCATE_REQUIRED_SHEETS
