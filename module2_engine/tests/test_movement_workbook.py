"""Render smoke test for the movement workbook — CI-safe (synthetic frames, no
desktop files). Asserts the workbook generates, opens, and has the expected
one-sheet-per-class structure with opening/closing rows."""

import io
import types

import pandas as pd

from module2_engine.movement.compute import build_sama_movement
from module2_engine.movement.workbook import render_sama_workbook


def _frames():
    rows = [
        {"RESERVINGCLASS": "MOTOR", "UWY": 2023,
         "Gross UPR_prev": 100.0, "Gross UPR_curr": 120.0,
         "DAC_prev": 10.0, "DAC_curr": 12.0,
         "GROSS - Outstanding_prev": 200.0, "GROSS - Outstanding_curr": 210.0,
         "Premium Received": 40.0, "Claims Paid": -30.0},
        {"RESERVINGCLASS": "PROPERTY", "UWY": 2022,
         "Gross UPR_prev": 50.0, "Gross UPR_curr": 55.0,
         "GROSS - Outstanding_prev": 80.0, "GROSS - Outstanding_curr": 85.0},
    ]
    ifrs = pd.DataFrame(rows)
    lc = pd.DataFrame([
        {"RESERVINGCLASS": r["RESERVINGCLASS"], "UWY": r["UWY"],
         "LC Discounted_PY": 0.0, "LC Discounted_CY": 0.0, "Loss Recovery Component": 0.0}
        for r in rows
    ])
    return types.SimpleNamespace(ifrs_summary_df=ifrs, allocate_sheets={"LC": lc})


def test_workbook_renders_one_sheet_per_class():
    res = build_sama_movement(_frames())
    xlsx = render_sama_workbook(res, reporting_date="31/12/2024")
    assert isinstance(xlsx, bytes) and len(xlsx) > 0
    xl = pd.ExcelFile(io.BytesIO(xlsx))
    assert set(xl.sheet_names) == {"MOTOR", "PROPERTY"}


def test_workbook_contains_opening_closing_and_reporting_date():
    res = build_sama_movement(_frames())
    xlsx = render_sama_workbook(res, reporting_date="31/12/2024")
    text = pd.ExcelFile(io.BytesIO(xlsx)).parse("MOTOR", header=None).astype(str).to_numpy().tolist()
    flat = " ".join(c for row in text for c in row)
    assert "as at 01/01" in flat  # opening label unchanged
    assert "31/12/2024" in flat   # dynamic closing date applied (plan §3b)
    assert "Reconciliation residual" in flat


def test_render_works_without_xlsxwriter():
    """Guard: the disclosure workbook must render with only openpyxl — xlsxwriter is
    an optional dep absent in production (this exact gap shipped a prod crash once)."""
    import builtins

    real_import = builtins.__import__

    def blocked(name, *a, **k):
        if name == "xlsxwriter" or name.startswith("xlsxwriter."):
            raise ModuleNotFoundError("No module named 'xlsxwriter'")
        return real_import(name, *a, **k)

    res = build_sama_movement(_frames())
    builtins.__import__ = blocked
    try:
        xlsx = render_sama_workbook(res, reporting_date="31/12/2024")
    finally:
        builtins.__import__ = real_import
    assert isinstance(xlsx, bytes) and len(xlsx) > 0


def test_long_class_names_are_truncated_safely():
    rows = [{"RESERVINGCLASS": "MOTOR COMPULSORY (NON-AGGREGATORS ONLY)", "UWY": 2023,
             "Gross UPR_prev": 1.0, "Gross UPR_curr": 1.0}]
    ifrs = pd.DataFrame(rows)
    lc = pd.DataFrame([{"RESERVINGCLASS": rows[0]["RESERVINGCLASS"], "UWY": 2023,
                        "LC Discounted_PY": 0.0, "LC Discounted_CY": 0.0, "Loss Recovery Component": 0.0}])
    frames = types.SimpleNamespace(ifrs_summary_df=ifrs, allocate_sheets={"LC": lc})
    xlsx = render_sama_workbook(build_sama_movement(frames))
    name = pd.ExcelFile(io.BytesIO(xlsx)).sheet_names[0]
    assert len(name) <= 31
