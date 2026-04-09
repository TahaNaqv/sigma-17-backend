import pandas as pd
from openpyxl import Workbook

from module2_engine.engine import _apply_selected_ulr, _read_required_sheet, _require_columns


def _uw_summary_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "RESERVINGCLASS": "Motor",
                "UWY": "2024",
                "Ult LR": 0.6,
                "RA %": 0.1,
                "Comm Ratio": 0.2,
                "Exp Ratio": 0.1,
            },
            {
                "RESERVINGCLASS": "Property",
                "UWY": "2023",
                "Ult LR": 0.5,
                "RA %": 0.05,
                "Comm Ratio": 0.15,
                "Exp Ratio": 0.05,
            },
        ]
    )


def test_apply_selected_ulr_defaults_to_ult_lr_when_no_payload():
    df = _apply_selected_ulr(_uw_summary_df(), None)
    assert float(df.loc[0, "Selected ULR"]) == 0.6
    assert float(df.loc[1, "Selected ULR"]) == 0.5


def test_apply_selected_ulr_overrides_matching_row_only():
    payload = [{"reserving_class": "Motor", "uwy": "2024", "selected_ulr": 0.7}]
    df = _apply_selected_ulr(_uw_summary_df(), payload)
    assert float(df.loc[0, "Selected ULR"]) == 0.7
    assert float(df.loc[1, "Selected ULR"]) == 0.5
    # Combined Ratio = Selected ULR * (1 + RA%) + Comm Ratio + Exp Ratio
    assert round(float(df.loc[0, "Combined Ratio"]), 6) == round(0.7 * 1.1 + 0.2 + 0.1, 6)


def test_read_required_sheet_raises_clear_error_for_missing_sheet():
    wb = Workbook()
    ws = wb.active
    ws.title = "Existing"
    ws.append(["a"])
    ws.append([1])
    import io

    buf = io.BytesIO()
    wb.save(buf)
    raw = buf.getvalue()
    try:
        _read_required_sheet(raw, "MissingSheet")
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "Required sheet 'MissingSheet' is missing." in str(exc)


def test_require_columns_raises_clear_error():
    df = pd.DataFrame({"a": [1], "b": [2]})
    try:
        _require_columns(df, "Sample", ["a", "c"])
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "Sheet 'Sample' is missing required columns: c." in str(exc)
