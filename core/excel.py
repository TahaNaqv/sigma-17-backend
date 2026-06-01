"""Centralised Excel engine selection for the actuarial engines.

The compute hot paths are vectorised (see docs/PERFORMANCE_OPTIMIZATION_PLAN.md);
what remains is Excel I/O. We use faster backends where they are correctness-safe:

* **Reads** → ``python-calamine`` (Rust, several times faster than openpyxl).
* **Writes** → ``XlsxWriter`` (faster than openpyxl for fresh workbooks).

Both are chosen here, once, with graceful fallback to openpyxl if the optional
dependency is missing, and an env override for incident response:

    SIGMA_EXCEL_READ_ENGINE=openpyxl   # force-revert reads
    SIGMA_EXCEL_WRITE_ENGINE=openpyxl  # force-revert writes

IMPORTANT: ``XlsxWriter`` can only create new files (it cannot edit an existing
workbook or append a second table to a sheet that already exists). Code paths
that load-and-modify a workbook, or write two tables to one sheet via
``startrow``, must keep using openpyxl explicitly — do not route them through
``WRITE_ENGINE``.
"""

from __future__ import annotations

import os


def _resolve_read_engine() -> str:
    override = os.environ.get("SIGMA_EXCEL_READ_ENGINE", "").strip()
    if override:
        return override
    try:
        import python_calamine  # noqa: F401
        return "calamine"
    except ImportError:
        return "openpyxl"


def _resolve_write_engine() -> str:
    override = os.environ.get("SIGMA_EXCEL_WRITE_ENGINE", "").strip()
    if override:
        return override
    try:
        import xlsxwriter  # noqa: F401
        return "xlsxwriter"
    except ImportError:
        return "openpyxl"


# Resolved once at import. Cheap, and engines don't change mid-process.
READ_ENGINE: str = _resolve_read_engine()
WRITE_ENGINE: str = _resolve_write_engine()
