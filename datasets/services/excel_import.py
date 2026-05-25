"""Parse an uploaded .xlsx into a Dataset + typed rows.

Used by:
- POST /api/datasets/import-excel/ (user-driven import)
- Phase 2.5: the upload-side of existing job endpoints when we wire
  auto-Dataset creation for backwards-compat.

Design: we trust the row tables' Django field validators to enforce
types (DecimalField rejects garbage, DateField rejects bad dates) by
running each row through its serializer. The import is atomic — a
single bad row rolls the whole batch back so the user never ends up
with a half-loaded dataset.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from io import BytesIO
from typing import Any

import pandas as pd
from django.db import transaction

from ..models import ROW_MODEL_FOR_KIND, Dataset
from ..serializers import ROW_SERIALIZER_FOR_KIND
from .columns import (
    DB_TO_EXCEL_FOR_KIND,
    REQUIRED_FIELDS_FOR_KIND,
    excel_to_db_for_kind,
)


class ExcelImportError(Exception):
    """Raised when an uploaded Excel file can't be ingested."""


def _coerce_cell(value: Any) -> Any:
    """Normalize a single pandas cell to something the serializer accepts.

    pandas reads blank cells as NaN; the Django serializer wants None.
    Datetime objects get reduced to plain dates because all our row
    fields are DateField, not DateTimeField. NaN floats are normalized
    too — the serializer rejects NaN even on optional numeric fields.
    """
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    return value


def _read_workbook_rows(file_bytes: bytes, kind: str) -> list[dict]:
    """Read xlsx bytes and return a list of dicts keyed by DB field name.

    Reads only the first sheet — same as the engine. Unknown columns are
    silently dropped; required columns missing → ExcelImportError.
    """
    excel_to_db = excel_to_db_for_kind(kind)
    db_to_excel = DB_TO_EXCEL_FOR_KIND[kind]
    try:
        df = pd.read_excel(BytesIO(file_bytes), engine="openpyxl")
    except Exception as exc:
        raise ExcelImportError(f"Could not parse Excel file: {exc}") from exc

    # Track which expected columns weren't found; bail if the file is
    # missing a column we consider required.
    present_excel = set(df.columns)
    missing_required = []
    required_db = REQUIRED_FIELDS_FOR_KIND.get(kind, ())
    for db_field in required_db:
        excel_col = db_to_excel[db_field]
        if excel_col not in present_excel:
            missing_required.append(excel_col)
    if missing_required:
        raise ExcelImportError(
            "Excel file is missing required columns: " + ", ".join(missing_required)
        )

    rows: list[dict] = []
    for record in df.to_dict(orient="records"):
        row: dict = {}
        for excel_col, raw_value in record.items():
            db_field = excel_to_db.get(excel_col)
            if db_field is None:
                # Unknown column — skip silently; many real-world sheets
                # carry extra audit columns.
                continue
            row[db_field] = _coerce_cell(raw_value)
        rows.append(row)
    return rows


@transaction.atomic
def import_excel_to_dataset(
    *,
    organization,
    kind: str,
    name: str,
    file_bytes: bytes,
    description: str = "",
    created_by=None,
) -> Dataset:
    """Create a new Dataset from xlsx bytes. Atomic per call."""
    if kind not in DB_TO_EXCEL_FOR_KIND:
        raise ExcelImportError(f"Excel import is not supported for kind '{kind}'.")

    raw_rows = _read_workbook_rows(file_bytes, kind)

    dataset = Dataset.objects.create(
        organization=organization,
        kind=kind,
        name=name,
        description=description,
        source=Dataset.Source.EXCEL_IMPORT,
        created_by=created_by,
    )

    model = ROW_MODEL_FOR_KIND[kind]
    serializer_cls = ROW_SERIALIZER_FOR_KIND[kind]

    # Validate every row first so we never half-insert.
    validated = []
    errors = []
    for offset, raw in enumerate(raw_rows):
        payload = dict(raw)
        payload["row_index"] = offset
        ser = serializer_cls(data=payload)
        if not ser.is_valid():
            errors.append({"row": offset, "errors": ser.errors})
            continue
        validated.append(ser.validated_data)

    if errors:
        # transaction.atomic will roll the Dataset row back too.
        raise ExcelImportError(
            {
                "detail": "One or more Excel rows failed validation.",
                "rows": errors,
            }
        )

    instances = [model(dataset=dataset, **data) for data in validated]
    model.objects.bulk_create(instances, batch_size=2000)
    dataset.refresh_row_count()
    return dataset
