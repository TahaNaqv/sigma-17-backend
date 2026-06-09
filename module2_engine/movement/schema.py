"""Schema-as-code for the IFRS 17 movement-analysis disclosure.

The structure (rows, indent levels, sign indicators, bucket columns, and the
subtotal groupings that are recoverable from the source workbook) is the single
source of truth shared by the engine (projection target) and the frontend (render
source, via the generated ``schema.ts``). It is loaded from the committed
``schema_source.json`` — a faithful, normalized extraction of the SAMA
``template.xlsx`` (regenerate with ``scripts/extract_movement_schema_from_xlsx.py``
only when SAMA revises the template) — plus a small curated override layer below.

Pure stdlib (no Django / pandas) so it imports anywhere and is trivially testable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_SOURCE_PATH = Path(__file__).with_name("schema_source.json")

# Curated corrections layered over the auto-extraction. The Gross sheet in the
# source workbook was flattened to static values, so a handful of aggregate rows
# lost the formulas that mark them as subtotals (the parallel RI rows, which keep
# their formulas, use slightly different labels so the cross-reference misses them).
# Keyed by (sheet, line_id) -> kind. Reviewed against the SAMA template.
_KIND_OVERRIDES: dict[tuple[str, str], str] = {
    ("Gross", "insurance_acquisition_cash_flows_on_new_contracts_amortization_of_insurance_acquisition_cash_flows"): "subtotal",
    ("Gross", "future_service_losses_on_onerous_contracts_and_reversals_of_those_losses"): "subtotal",
    ("Gross", "reversal_of_losses_on_existing_onerous_contracts"): "subtotal",
    ("Gross", "insurance_finance_expenses_income"): "subtotal",
    # "Cash flows" stays a section header (not a subtotal).
}

#: Lines whose subtotal *composition* is not fully recoverable from the flattened
#: Gross workbook and must be defined explicitly in the engine (compute.py) with
#: actuarial sign-off. Surfaced here so callers can detect "needs definition".
SUBTOTALS_NEEDING_EXPLICIT_FORMULA: frozenset[str] = frozenset(
    line_id for (sheet, line_id) in _KIND_OVERRIDES
)


@dataclass(frozen=True)
class Line:
    """One disclosure row."""

    id: str
    row: int  # original Excel row (stable ordering + provenance)
    label: str
    level: int  # indent depth (0 = top level)
    kind: str  # opening | input | subtotal | closing | section
    signs: dict[str, str] = field(default_factory=dict)  # bucket -> + | - | +/- | -/+
    formulas: dict[str, dict] | None = None  # bucket -> {excel, refs:[...]} (subtotals)

    @property
    def is_value_line(self) -> bool:
        """True for lines that carry a number per bucket (not pure section headers)."""
        return self.kind in {"opening", "input", "subtotal", "closing"}


@dataclass(frozen=True)
class Sheet:
    name: str
    buckets: tuple[str, ...]  # all columns incl. Total
    value_buckets: tuple[str, ...]  # the 4 measurement buckets (excl. Total)
    lines: tuple[Line, ...]

    def line(self, line_id: str) -> Line:
        for ln in self.lines:
            if ln.id == line_id:
                return ln
        raise KeyError(f"{self.name}: no line {line_id!r}")


@dataclass(frozen=True)
class MovementSchema:
    version: str
    sheets: dict[str, Sheet]


@lru_cache(maxsize=1)
def _load() -> MovementSchema:
    raw = json.loads(_SOURCE_PATH.read_text(encoding="utf-8"))
    sheets: dict[str, Sheet] = {}
    for name, sh in raw["sheets"].items():
        lines = []
        for ln in sh["lines"]:
            kind = _KIND_OVERRIDES.get((name, ln["id"]), ln["kind"])
            lines.append(
                Line(
                    id=ln["id"],
                    row=ln["row"],
                    label=ln["label"],
                    level=ln.get("level", 0),
                    kind=kind,
                    signs=dict(ln.get("signs") or {}),
                    formulas=ln.get("formulas"),
                )
            )
        sheets[name] = Sheet(
            name=name,
            buckets=tuple(sh["buckets"]),
            value_buckets=tuple(sh["value_buckets"]),
            lines=tuple(lines),
        )
    return MovementSchema(version=raw["schema_version"], sheets=sheets)


SCHEMA: MovementSchema = _load()
SCHEMA_VERSION: str = SCHEMA.version


def get_sheet(name: str) -> Sheet:
    return SCHEMA.sheets[name]


def iter_lines():
    """Yield (sheet_name, Line) across both sheets."""
    for name, sheet in SCHEMA.sheets.items():
        for ln in sheet.lines:
            yield name, ln


def validate_schema() -> list[str]:
    """Structural integrity check. Returns a list of problems ([] == valid).

    Asserts: unique ids per sheet; signs only on input/opening lines and only on
    declared buckets; every subtotal formula reference points to an existing line
    and a declared bucket. Does NOT assert actuarial correctness (sign-off, §1).
    """
    problems: list[str] = []
    for name, sheet in SCHEMA.sheets.items():
        ids = [ln.id for ln in sheet.lines]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            problems.append(f"{name}: duplicate line ids {sorted(dupes)}")
        valid_buckets = set(sheet.buckets)
        id_set = set(ids)
        for ln in sheet.lines:
            for bucket in ln.signs:
                if bucket not in valid_buckets:
                    problems.append(f"{name}.{ln.id}: sign on unknown bucket {bucket!r}")
            for bucket, frm in (ln.formulas or {}).items():
                if bucket not in valid_buckets:
                    problems.append(f"{name}.{ln.id}: formula on unknown bucket {bucket!r}")
                for ref in frm.get("refs", []):
                    targets = ref.get("lines", []) or ([ref["line"]] if ref.get("line") else [])
                    for t in targets:
                        if t not in id_set:
                            problems.append(
                                f"{name}.{ln.id}: formula refs unknown line {t!r}"
                            )
    return problems
