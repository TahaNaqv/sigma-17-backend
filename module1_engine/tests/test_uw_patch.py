"""Tests for UW / ULAE / Discount patching of Combined_Summary.xlsx."""

import io

import pandas as pd
import pytest

from module1_engine.uw_patch import (
    SHEET_DISCOUNT_RATE,
    SHEET_ULAE_RA,
    apply_uw_parameters_to_combined_summary,
    build_default_payload_template_from_workbook,
)


def _minimal_combined_summary_bytes() -> bytes:
    uw = pd.DataFrame(
        {
            "RESERVINGCLASS": ["Motor", "Property"],
            "UWY": [2022, 2022],
            "GWP": [100.0, 200.0],
            "UPR": [10.0, 20.0],
            "GEP": [90.0, 180.0],
            "Commission Expense": [5.0, 10.0],
            "Paid Claims": [30.0, 40.0],
            "OS": [10.0, 15.0],
            "Incurred Claims": [40.0, 55.0],
            "Paid LR": [0.33, 0.22],
            "Inc LR": [0.44, 0.31],
            "Comm Ratio": [0.05, 0.05],
            "Exp Ratio": [None, None],
            "RI %": [None, None],
        }
    )
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        uw.to_excel(writer, sheet_name="UW Summary", index=False)
        pd.DataFrame({"Combined": [1]}).to_excel(writer, sheet_name="Combined Summary", index=False)
    return buf.getvalue()


def test_build_default_payload_template():
    raw = _minimal_combined_summary_bytes()
    tpl = build_default_payload_template_from_workbook(raw)
    assert len(tpl["exp_ratio"]) == 2
    classes = {e["reserving_class"] for e in tpl["exp_ratio"]}
    assert classes == {"Motor", "Property"}
    assert len(tpl["ulae_ra"]) == 4
    assert len(tpl["discount"]) >= 5


def test_apply_uw_parameters_writes_sheets():
    raw = _minimal_combined_summary_bytes()
    payload = {
        "exp_ratio": [
            {"reserving_class": "Motor", "uwy": "2022", "exp_ratio": 0.15, "ri_percent": 0.25},
            {"reserving_class": "Property", "uwy": "2022", "exp_ratio": 0.2, "ri_percent": 0.3},
        ],
        "ulae_ra": [
            {"reserving_class": "Motor", "gross_ri": "GROSS", "ulae_percent": 1.1, "ra_percent": 2.2},
        ],
        "discount": [
            {"time_period": "0-1 Year", "cy_discount": 0.05, "py_discount": 0.04},
        ],
    }
    out = apply_uw_parameters_to_combined_summary(raw, payload)
    xl = pd.ExcelFile(io.BytesIO(out), engine="openpyxl")
    uw = pd.read_excel(xl, sheet_name="UW Summary")
    motor = uw[uw["RESERVINGCLASS"] == "Motor"].iloc[0]
    assert float(motor["Exp Ratio"]) == pytest.approx(0.15)
    assert float(motor["RI %"]) == pytest.approx(0.25)

    ulae = pd.read_excel(xl, sheet_name=SHEET_ULAE_RA)
    assert "RESERVINGCLASS" in ulae.columns
    assert len(ulae) >= 1

    disc = pd.read_excel(xl, sheet_name=SHEET_DISCOUNT_RATE)
    assert "Time Period" in disc.columns
    assert any(disc["Time Period"].astype(str).str.contains("0-1"))
