"""Reserve-workbook helpers for the Excel-free Update Reserve flow.

A Summary job's output ZIP contains:
- `Combined_Summary.xlsx`
- One reserve workbook per (reserving_class, head_of_damage, ri_type, eop),
  named `{reserving_class} {head_of_damage} {ri_type} {YYYY-MM}.xlsx`. Each
  workbook has three sheets: `Paid Claims Triangle`, `Reported Triangle`,
  and `Reserve Summary`. The triangle sheets contain a "Selected LDF" row
  followed by a "Selected CDF" row whose cells are PRODUCT formulas.

The Update Reserve job consumes the **Selected CDF** values (the engine
reads them with `data_only=True`). So the user-facing edit surface in
the UI is the Selected CDF row — we read it, let the user override
values, and write the overrides back as plain numbers (replacing the
PRODUCT formulas). The engine then sees the user's values directly.

This module is the single place that knows how to:
- list reserve workbooks in a source Summary job's ZIP
- read the current Selected CDF row for each triangle sheet
- apply a set of user-provided overrides + write the modified workbooks
  into the task's staging directory
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from pathlib import Path

from openpyxl import load_workbook

from processing.models import Module1Job
from processing.services.source_resolver import (
    ARTIFACT_COMBINED_SUMMARY,
    read_artifact_bytes,
)

logger = logging.getLogger(__name__)


TRIANGLE_SHEET_NAMES = ("Paid Claims Triangle", "Reported Triangle")
SELECTED_CDF_LABEL = "Selected CDF"


# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------


def _parse_workbook_name(basename: str) -> dict | None:
    """Return {reserving_class, head_of_damage, ri_type, eop_label} or None.

    Mirrors the engine's parsing at module1_engine/engine.py: the last
    three whitespace-separated parts are eop, ri_type, head_of_damage;
    everything before them is the reserving_class (which may itself
    contain spaces).
    """
    if not basename.lower().endswith(".xlsx"):
        return None
    stem = basename[:-len(".xlsx")]
    parts = re.split(r"\s+", stem.strip())
    if len(parts) < 4:
        return None
    eop_label = parts[-1]
    ri_type = parts[-2].upper()
    head_of_damage = parts[-3]
    reserving_class = " ".join(parts[:-3])
    return {
        "filename": basename,
        "reserving_class": reserving_class,
        "head_of_damage": head_of_damage,
        "ri_type": ri_type,
        "eop_label": eop_label,
    }


def list_reserve_workbooks(source_job: Module1Job) -> list[dict]:
    """List the parseable reserve workbooks in `source_job.output_zip`.

    Excludes `Combined_Summary.xlsx` and any entries whose names don't
    match the engine's `{class} {hod} {ri} {eop}.xlsx` shape.
    """
    if source_job is None:
        raise ValueError("Source job is missing.")
    if not source_job.output_zip:
        return []
    try:
        with source_job.output_zip.open("rb") as f:
            raw = f.read()
    except FileNotFoundError as exc:
        raise ValueError("Source job output is missing on disk.") from exc

    out: list[dict] = []
    with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
        for name in zf.namelist():
            if name == ARTIFACT_COMBINED_SUMMARY:
                continue
            if not name.lower().endswith(".xlsx"):
                continue
            parsed = _parse_workbook_name(name)
            if parsed is not None:
                out.append(parsed)
    return out


# ---------------------------------------------------------------------------
# CDF reading
# ---------------------------------------------------------------------------


def _find_selected_cdf_row(ws) -> int | None:
    """Same primitive the engine uses — first cell in column A equal to
    "Selected CDF" wins."""
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=1, max_col=1):
        for cell in row:
            if cell.value == SELECTED_CDF_LABEL:
                return cell.row
    return None


def _read_triangle_cdf(wb_data_only, wb_formulas, sheet_name: str) -> dict:
    """Pull the column labels (from row 1) and the current CDF values
    (from the Selected CDF row) for one triangle sheet.

    Returns:
      {
        "sheet": sheet_name,
        "cdf_row": <int|null>,                # row number in the sheet
        "column_labels": [str, ...],          # headers above each value
        "values": [float|None, ...],          # current Selected CDF values
      }
    """
    if sheet_name not in wb_formulas.sheetnames:
        return {"sheet": sheet_name, "cdf_row": None, "column_labels": [], "values": []}
    ws = wb_formulas[sheet_name]
    ws_data = wb_data_only[sheet_name]
    row = _find_selected_cdf_row(ws)
    if row is None:
        return {"sheet": sheet_name, "cdf_row": None, "column_labels": [], "values": []}

    # The engine iterates columns 2..max_column inclusive (column 1 is the
    # "Selected CDF" label). We do the same.
    max_col = ws.max_column
    column_labels: list[str] = []
    values: list[float | None] = []
    for col_idx in range(2, max_col + 1):
        header_value = ws.cell(row=1, column=col_idx).value
        column_labels.append("" if header_value is None else str(header_value))
        v = ws_data.cell(row=row, column=col_idx).value
        if v is None:
            values.append(None)
        else:
            try:
                values.append(float(v))
            except (TypeError, ValueError):
                values.append(None)
    return {
        "sheet": sheet_name,
        "cdf_row": row,
        "column_labels": column_labels,
        "values": values,
    }


def read_workbook_cdfs(source_job: Module1Job, filename: str) -> dict:
    """Read the Selected CDF row for both triangle sheets of one workbook.

    Returns:
      {
        "filename": str,
        "reserving_class", "head_of_damage", "ri_type", "eop_label": ...,
        "triangles": [ {sheet, cdf_row, column_labels, values}, ... ],
      }
    Raises ValueError if the workbook isn't found or can't be parsed.
    """
    parsed = _parse_workbook_name(filename)
    if parsed is None:
        raise ValueError(f"Workbook name '{filename}' is not parseable.")

    raw = read_artifact_bytes(source_job=source_job, artifact=filename)

    # Two loads: one for formulas (sheet structure / cell label lookup),
    # one for values (the cached formula results the engine consumes).
    wb_formulas = load_workbook(io.BytesIO(raw), data_only=False)
    wb_data_only = load_workbook(io.BytesIO(raw), data_only=True)

    triangles = [
        _read_triangle_cdf(wb_data_only, wb_formulas, name)
        for name in TRIANGLE_SHEET_NAMES
    ]

    return {**parsed, "triangles": triangles}


# ---------------------------------------------------------------------------
# Override application
# ---------------------------------------------------------------------------


def _apply_overrides_to_bytes(
    raw_bytes: bytes,
    overrides: dict[str, list[float | None]],
) -> bytes:
    """Apply per-sheet overrides to the Selected CDF row of an xlsx and
    return the modified bytes.

    `overrides` is keyed by sheet name; the value is the new CDF row
    (positional, starting at column 2). Missing sheets / shorter arrays
    leave their cells unchanged.
    """
    wb = load_workbook(io.BytesIO(raw_bytes), data_only=False)
    for sheet_name, new_values in overrides.items():
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        cdf_row = _find_selected_cdf_row(ws)
        if cdf_row is None:
            continue
        for offset, value in enumerate(new_values):
            col_idx = 2 + offset  # column 1 = label cell
            # Overwriting with a numeric value drops any formula that was
            # there. None leaves the cell empty (still an override).
            ws.cell(row=cdf_row, column=col_idx, value=value)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def write_workbooks_with_overrides(
    *,
    source_job: Module1Job,
    overrides_by_filename: dict[str, dict[str, list[float | None]]],
    dest_folder: Path,
) -> list[str]:
    """For each reserve workbook in `source_job`'s output ZIP, copy it
    into `dest_folder`. If the workbook's filename appears in
    `overrides_by_filename`, apply the per-sheet CDF overrides first.

    Returns the list of basenames written into `dest_folder`. Workbooks
    not in the overrides dict are written verbatim (still copied so the
    engine sees them).
    """
    if source_job is None:
        raise ValueError("Source job is missing.")
    if not source_job.output_zip:
        raise ValueError("Source job has no downloadable output.")

    dest_folder.mkdir(parents=True, exist_ok=True)

    try:
        with source_job.output_zip.open("rb") as f:
            raw_zip = f.read()
    except FileNotFoundError as exc:
        raise ValueError("Source job output is missing on disk.") from exc

    written: list[str] = []
    with zipfile.ZipFile(io.BytesIO(raw_zip), "r") as zf:
        for name in zf.namelist():
            if name == ARTIFACT_COMBINED_SUMMARY:
                # The Update Reserve task handles Combined_Summary separately
                # via the existing chained-source code path; skip it here.
                continue
            if not name.lower().endswith(".xlsx"):
                continue
            if _parse_workbook_name(name) is None:
                continue
            file_bytes = zf.read(name)
            if name in overrides_by_filename:
                file_bytes = _apply_overrides_to_bytes(
                    file_bytes, overrides_by_filename[name]
                )
            (dest_folder / name).write_bytes(file_bytes)
            written.append(name)
    return written
