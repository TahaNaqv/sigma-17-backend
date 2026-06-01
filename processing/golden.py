"""Golden-output capture & comparison for the actuarial engines.

The optimisation work (docs/PERFORMANCE_OPTIMIZATION_PLAN.md) requires that
optimised engines produce **bit-identical** results to the current code. This
module freezes a known-good output ("golden") and diffs a fresh run against it
at the *value* level, so we compare numbers — not the byte layout of an .xlsx,
which legitimately changes between writer versions.

An engine output is normalised to a nested structure::

    {workbook_filename: {sheet_name: DataFrame}}

Module 1 entry points write a *directory* of workbooks; Module 2 entry points
return workbook *bytes*. Both normalise into the structure above.

Goldens are persisted as pickled DataFrames (exact float64 preservation — CSV
round-trips through text and can lose the last ULP) plus a ``manifest.json``
describing the structure. Pickle is acceptable here because goldens are an
internal, same-environment artefact, regenerated with ``capture_golden``.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.testing import assert_frame_equal

# A normalised engine output: filename -> sheet -> frame.
OutputStruct = dict[str, dict[str, pd.DataFrame]]


# --------------------------------------------------------------------------- #
# Normalisation: engine output -> {filename: {sheet: DataFrame}}
# --------------------------------------------------------------------------- #
def read_workbook(source: Any, name: str) -> dict[str, pd.DataFrame]:
    """Read every sheet of one workbook (path or bytes) into ``{sheet: df}``."""
    buf = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source
    with pd.ExcelFile(buf, engine="openpyxl") as xls:
        return {
            sheet: pd.read_excel(xls, sheet_name=sheet, engine="openpyxl")
            for sheet in xls.sheet_names
        }


def normalize_output_dir(directory: str | Path) -> OutputStruct:
    """Normalise a directory of ``.xlsx`` files (Module 1 style output)."""
    directory = Path(directory)
    out: OutputStruct = {}
    for path in sorted(directory.glob("*.xlsx")):
        if path.name.startswith("~$"):  # transient Excel lock files
            continue
        out[path.name] = read_workbook(path, path.name)
    return out


def normalize_bytes(name: str, data: bytes) -> OutputStruct:
    """Normalise a single in-memory workbook (Module 2 style output)."""
    return {name: read_workbook(data, name)}


# --------------------------------------------------------------------------- #
# Freeze / thaw
# --------------------------------------------------------------------------- #
def freeze(struct: OutputStruct, golden_dir: str | Path) -> None:
    """Persist a normalised output as the golden set under ``golden_dir``."""
    golden_dir = Path(golden_dir)
    if golden_dir.exists():
        for p in golden_dir.rglob("*"):
            if p.is_file():
                p.unlink()
    golden_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, list[str]] = {}
    for fi, (filename, sheets) in enumerate(struct.items()):
        manifest[filename] = list(sheets.keys())
        for si, (sheet, frame) in enumerate(sheets.items()):
            frame.to_pickle(golden_dir / f"{fi:04d}_{si:04d}.pkl")
    (golden_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))


def thaw(golden_dir: str | Path) -> OutputStruct:
    """Load a previously frozen golden set."""
    golden_dir = Path(golden_dir)
    manifest = json.loads((golden_dir / "manifest.json").read_text())
    out: OutputStruct = {}
    for fi, (filename, sheet_names) in enumerate(manifest.items()):
        out[filename] = {}
        for si, sheet in enumerate(sheet_names):
            out[filename][sheet] = pd.read_pickle(golden_dir / f"{fi:04d}_{si:04d}.pkl")
    return out


# --------------------------------------------------------------------------- #
# Diff
# --------------------------------------------------------------------------- #
def diff_struct(
    actual: OutputStruct,
    golden: OutputStruct,
    *,
    atol: float = 0.0,
    rtol: float = 0.0,
) -> list[str]:
    """Return a list of human-readable differences (empty == identical).

    With the default ``atol=rtol=0`` and exact dtype matching this enforces
    bit-identical values. Pass a small ``atol`` only with sign-off when a
    summation-order change is provably the sole source of divergence (Risk R1).
    """
    diffs: list[str] = []

    a_files, g_files = set(actual), set(golden)
    for missing in sorted(g_files - a_files):
        diffs.append(f"missing workbook: {missing!r}")
    for extra in sorted(a_files - g_files):
        diffs.append(f"unexpected workbook: {extra!r}")

    for filename in sorted(a_files & g_files):
        a_sheets, g_sheets = actual[filename], golden[filename]
        for missing in sorted(set(g_sheets) - set(a_sheets)):
            diffs.append(f"{filename}: missing sheet {missing!r}")
        for extra in sorted(set(a_sheets) - set(g_sheets)):
            diffs.append(f"{filename}: unexpected sheet {extra!r}")
        for sheet in sorted(set(a_sheets) & set(g_sheets)):
            try:
                assert_frame_equal(
                    a_sheets[sheet],
                    g_sheets[sheet],
                    check_exact=(atol == 0.0 and rtol == 0.0),
                    atol=atol,
                    rtol=rtol,
                    check_dtype=True,
                )
            except AssertionError as exc:
                first = str(exc).strip().splitlines()[0]
                diffs.append(f"{filename}::{sheet}: {first}")
    return diffs
