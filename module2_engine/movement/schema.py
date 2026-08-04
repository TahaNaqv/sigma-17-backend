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
import re
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

#: Aggregate rows the client flattened to static values, losing their formulas. Every
#: relation below was reconstructed from the SAMA structure and then **verified against
#: the client's own Total column**, which is the only column in their file carrying real
#: numbers (their per-bucket cells are broken references to the IFRS Summary header row).
#: Each evaluates to the client's figure with a 0.00 residual — see the note-disclosure
#: plan §3. Column letters are cosmetic; the evaluator resolves purely by row number.
#:
#: Row 32 includes row 38: the client's own row-31 formula is
#: ``=SUM(C32+C42+C47+C53+C54)`` — it never adds row 38 — yet J31 ties only when the
#: acquisition amount is inside row 32. Row 47 takes row 52 (ULAE) as a *sibling* of row
#: 48, not a child of it, and does **not** take row 53, which row 31 already adds
#: directly; the previous reconstruction double-counted it, and row 53 carries the routed
#: reconciliation residual, so the double count was live rather than theoretical.
RECONSTRUCTED_FORMULAS: dict[str, dict[int, str]] = {
    "Gross": {
        32: "=SUM(C33:C37)+C38",  # incurred claims + acquisition CF (client's J32 ties)
        38: "=SUM(C39:C41)",      # commission + other acquisition + change in DAC
        42: "=C43+C44",           # new onerous losses + reversals
        44: "=SUM(C45:C46)",      # amortisation + assumption change
        47: "=C48+C52",           # change in ultimate + change in ULAE
        48: "=SUM(C49:C51)",      # paid + ΔOS + ΔIBNR (ULAE is row 52, alongside)
        54: "=C55",               # investment components = change in profit commission
        57: "=SUM(C58:C59)",      # finance expense: P&L + OCI
        61: "=SUM(C62:C63)",      # other movements = Item 1 + Item 2
        # Closing = opening + balance movement + cash flows. NOT ``C6+C64+C71``: row 64
        # ("Total changes in the statement of profit or loss and OCI") is a **P&L**
        # aggregate — it carries row 56 = revenue − expenses — and a P&L total is not a
        # balance movement. Insurance revenue *releases* the LRC, so it enters negative
        # here while row 64 adds it. See ROLLFORWARD_NEGATED_BLOCK below.
        72: "=C6+(C31-C26+C57+C60+C61)+C71",
    },
}

#: Per sheet, the subtotal whose input block enters the **balance** roll-forward negated.
#:
#: The SAMA sheets present revenue and ceded-premium allocation with a P&L sign (positive),
#: but in a balance roll-forward both *reduce* the balance: insurance revenue releases the
#: LRC, and the reinsurance premium allocation consumes the asset for remaining coverage.
#:
#: The client encoded exactly this on the RI sheet — the one whose formulas they never
#: flattened: ``D63 = D4+(D55−D62)`` reaches its closing through ``D47 = D27−D21``, which
#: negates the allocation block. The Gross equivalent (row 72) was flattened to a static,
#: so the same treatment had to be reconstructed, and the reconstruction inherited row 64's
#: P&L sign instead. Tested against the client's own independent closing: the negated form
#: fits at 3.9%, the P&L-signed form at 7.4% (plan §5.1/§5.3).
ROLLFORWARD_NEGATED_BLOCK: dict[str, str] = {
    "Gross": "insurance_revenue",
    "RI": "amounts_allocated_to_reinsurance",
}


def negated_rollforward_rows(sheet_name: str) -> set[int]:
    """Excel rows of the input lines that enter the balance roll-forward negated."""
    sheet = SCHEMA.sheets.get(sheet_name)
    block = ROLLFORWARD_NEGATED_BLOCK.get(sheet_name)
    if sheet is None or block is None:
        return set()
    line = next((ln for ln in sheet.lines if ln.id == block), None)
    if line is None or not line.formulas:
        return set()
    excel = next(iter(line.formulas.values()))["excel"]
    return referenced_rows(excel)

#: Direction the cash-flow block enters the closing roll-forward, per sheet.
#:
#: The two sheets sign cash flows in mirror image, because one tracks a **liability** and
#: the other an **asset**, and the client's sign column describes the direction of the
#: *cash*, not of the balance:
#:
#:   Gross  premium *received* `+` → positive, and premium received **increases** the
#:          liability; claims *paid* `-` → negative, and paying claims **decreases** it.
#:          So the signed cash total is already a liability movement: **add** it.
#:   RI     premium *paid* `-` → negative, but paying premium **increases** the asset;
#:          claims *received* `+` → positive, but receiving cash **decreases** it.
#:          So the signed cash total is the negated asset movement: **subtract** it.
#:
#: RI's own closing formula (``RI!D63 = D4+(D55-D62)``) subtracts, and was previously
#: transplanted onto Gross, whose row 72 the client had flattened to a static. The client
#: has since written the Gross convention down themselves, in ``Gross_Note!F31 =
#: F11+F22+F28`` — opening plus changes **plus** cash flows.
CLOSING_CASHFLOW_SIGN: dict[str, float] = {"Gross": 1.0, "RI": -1.0}


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

#: Version of the extracted SAMA template structure (rows, labels, buckets, signs).
TEMPLATE_VERSION: str = SCHEMA.version

#: Revision of the *curated* layer that sits on top of that extraction — the kind
#: overrides, the reconstructed formulas, and the roll-forward conventions below. Bump it
#: whenever they change the numbers, so a consumer can tell a corrected artifact from an
#: earlier one by version alone; the template extraction itself is untouched by such a
#: change, which is why this is separate from TEMPLATE_VERSION.
#:
#: r2 — reconstructed the four Gross subtotals that had no formula and rendered 0 (rows
#:      38/42/44/57), folded row 38 into row 32, moved ULAE out of "Change in Ultimate",
#:      and corrected the closing roll-forward: cash flows are added for Gross (a
#:      liability) and the revenue / ceded-allocation block is negated because a P&L total
#:      is not a balance movement. All of these change reported figures.
CURATED_REVISION: str = "r2"

SCHEMA_VERSION: str = f"{TEMPLATE_VERSION}+{CURATED_REVISION}"


def get_sheet(name: str) -> Sheet:
    return SCHEMA.sheets[name]


def iter_lines():
    """Yield (sheet_name, Line) across both sheets."""
    for name, sheet in SCHEMA.sheets.items():
        for ln in sheet.lines:
            yield name, ln


_ROW_REF = re.compile(r"[A-Z]{1,3}(\d+)")


def referenced_rows(excel: str) -> set[int]:
    """Row numbers a subtotal formula reads, expanding ``X7:X25`` ranges."""
    expr = excel.lstrip("=")
    rows: set[int] = set()

    def _range(m):
        rows.update(range(int(m.group(1)), int(m.group(2)) + 1))
        return " "

    expr = re.sub(r"[A-Z]{1,3}(\d+):[A-Z]{1,3}(\d+)", _range, expr)
    rows.update(int(m) for m in _ROW_REF.findall(expr))
    return rows


def validate_schema() -> list[str]:
    """Structural integrity check. Returns a list of problems ([] == valid).

    Asserts: unique ids per sheet; signs only on input/opening lines and only on
    declared buckets; every subtotal formula reference points to an existing line
    and a declared bucket; and every reconstructed formula targets a real aggregate row
    and reads only real rows. Does NOT assert actuarial correctness (sign-off, §1).
    """
    problems: list[str] = []
    for sheet_name, formulas in RECONSTRUCTED_FORMULAS.items():
        sheet = SCHEMA.sheets.get(sheet_name)
        if sheet is None:
            problems.append(f"reconstructed formulas for unknown sheet {sheet_name!r}")
            continue
        rows = {ln.row: ln for ln in sheet.lines}
        for row, excel in formulas.items():
            target = rows.get(row)
            if target is None:
                problems.append(f"{sheet_name}: reconstructed formula for missing row {row}")
                continue
            if target.kind == "input":
                problems.append(
                    f"{sheet_name} r{row} ({target.id}): reconstructed formula on an input line"
                )
            for ref in sorted(referenced_rows(excel)):
                if ref not in rows:
                    problems.append(f"{sheet_name} r{row}: formula refs missing row {ref}")
                elif ref == row:
                    problems.append(f"{sheet_name} r{row}: formula references itself")
    for sheet_name in SCHEMA.sheets:
        if sheet_name not in CLOSING_CASHFLOW_SIGN:
            problems.append(f"{sheet_name}: no closing cash-flow sign declared")
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
