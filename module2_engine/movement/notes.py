"""Evaluate the IFRS 17 note disclosure for one grain view.

``build_notes`` is a pure function of the movement ``SheetResult``s a view already
carries. It reads them through ``compute.line_totals`` — the *same* resolution the Gross/RI
sheets are rendered from — so a note line and the movement line it cites cannot disagree
by construction. No frames, no mapping, no I/O.

Every note line is a linear combination of movement line values, so the notes are additive
across (class, UWY) pairs exactly as ``sum_sheet_results`` already assumes: the entity note
equals the sum of the class notes, for free and without special-casing.

Two conventions worth stating, because they are easy to get wrong:

* ``"-"`` cells (the structurally-absent RI liability rows) evaluate to ``None`` and render
  as a dash, but contribute **0** to any sum that includes them — which is what Excel does
  with the client's literal ``-`` text.
* Movement lines are addressed by ``(sheet, line id)``, never by id alone. Several ids
  exist on both sheets (``other_cash_flows`` is Gross r70 *and* RI r61), so an id-only
  lookup silently reads the wrong row.

Pure stdlib; no Django / pandas.
"""

from __future__ import annotations

from dataclasses import dataclass

from .compute import SheetResult, aggregated_views, line_totals
from .notes_schema import NOTES_SCHEMA, NoteSheet
from .schema import SCHEMA


def _num(v) -> float:
    """Dash / missing -> 0.0, matching Excel's treatment of the client's ``-`` cells."""
    if v is None:
        return 0.0
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if f == f and f not in (float("inf"), float("-inf")) else 0.0


@dataclass(frozen=True)
class NoteLineValue:
    id: str
    row: int  # provenance: row in the client's note sheet
    label: str
    kind: str  # section | input | subtotal
    values: dict[str, float | None]  # column -> value; None is a rendered "-"

    @property
    def total(self) -> float:
        return _num(self.values.get("Total"))


@dataclass(frozen=True)
class NoteTable:
    name: str
    title: str
    columns: tuple[str, ...]
    lines: tuple[NoteLineValue, ...]

    def line(self, line_id: str) -> NoteLineValue:
        for ln in self.lines:
            if ln.id == line_id:
                return ln
        raise KeyError(f"{self.name}: no line {line_id!r}")

    def value(self, line_id: str, column: str = "Total") -> float:
        return _num(self.line(line_id).values.get(column))


def build_notes(view: dict) -> dict[str, NoteTable]:
    """Evaluate all four note tables for one grain view.

    ``view`` is an entry from ``compute.aggregated_views`` — ``{"sheets": {name:
    SheetResult}, …}``. A movement sheet absent from the view resolves to zeros rather
    than raising, so a cohort with no reinsurance still produces a well-formed RI note.
    """
    movement: dict[str, dict[str, dict[str, float]]] = {}
    for name, sres in (view.get("sheets") or {}).items():
        if name in SCHEMA.sheets and isinstance(sres, SheetResult):
            movement[name] = line_totals(SCHEMA.sheets[name], sres)

    memo: dict[tuple[str, str, str], float | None] = {}
    active: set[tuple[str, str, str]] = set()

    def cell(note: str, line_id: str, column: str) -> float | None:
        key = (note, line_id, column)
        if key in memo:
            return memo[key]
        if key in active:
            # validate_notes_schema() rejects cycles; this keeps a malformed schema from
            # blowing the stack in production rather than returning a wrong number.
            return 0.0
        active.add(key)
        try:
            value = _resolve(note, line_id, column)
        finally:
            active.discard(key)
        memo[key] = value
        return value

    def _resolve(note: str, line_id: str, column: str) -> float | None:
        sheet: NoteSheet = NOTES_SCHEMA.sheets[note]
        src = sheet.line(line_id).columns.get(column)
        if src is None:
            return 0.0  # the client left this cell blank
        if src.kind == "dash":
            return None
        if src.kind == "const":
            return src.value
        if src.kind == "movement":
            return sum(
                term.factor * movement.get(term.sheet, {}).get(term.line, {}).get(term.bucket, 0.0)
                for term in src.terms
            )
        if src.kind == "note":
            ref = src.ref
            return _num(cell(ref.note, ref.line, ref.column))
        if src.kind == "sum":
            return sum(_num(cell(note, lid, column)) for lid in src.lines)
        if src.kind == "row_total":
            return sum(_num(cell(note, line_id, col)) for col in src.columns)
        return 0.0

    tables: dict[str, NoteTable] = {}
    for name, sheet in NOTES_SCHEMA.sheets.items():
        lines = tuple(
            NoteLineValue(
                id=ln.id,
                row=ln.row,
                label=ln.label,
                kind=ln.kind,
                values={col: cell(name, ln.id, col) for col in sheet.columns}
                if ln.is_value_line
                else {},
            )
            for ln in sheet.lines
        )
        tables[name] = NoteTable(
            name=name, title=sheet.title, columns=sheet.columns, lines=lines
        )
    return tables


# ── tie-out controls (plan §6.5) ─────────────────────────────────────────────
# The notes restate the movement sheets, so they must agree with them. These controls
# assert that agreement at runtime — a rendered note that has silently drifted from the
# sheet it presents is worse than no note at all.
#
# C3 is deliberately NOT a pass/fail: the note omits several movement lines (FX, other
# movements, investment components), so a gap between the note's closing and the
# movement's roll-forward closing is *expected*. It is reported so it stays visible
# instead of being absorbed into a plug.

#: (control id, note, note line id, movement sheet, movement Excel row, factor, label)
_CONTROLS: tuple[tuple[str, str, str, str, int, float, str], ...] = (
    ("C1", "Gross_Note", "insurance_service_expenses_2", "Gross", 31, 1.0,
     "Gross note service expenses == movement Insurance service expenses"),
    ("C2a", "Gross_Note", "insurance_revenue", "Gross", 26, -1.0,
     "Gross note revenue == negated movement Insurance revenue"),
    ("C2b", "Gross_Note", "finance_expense_from_insurance_contracts", "Gross", 57, 1.0,
     "Gross note finance == movement Insurance finance expenses/income"),
    ("C2c", "Gross_Note", "total_cash_inflows_outflows", "Gross", 71, 1.0,
     "Gross note cash flows == movement Total Cash Flows"),
    ("C2d", "RI_Note", "amounts_recoverable_from_reinsurers_net", "RI", 27, 1.0,
     "RI note amounts recoverable == movement Amounts Recoverable from Reinsurance"),
    ("C2e", "RI_Note", "allocation_of_reinsurance_premium", "RI", 21, 1.0,
     "RI note premium allocation == movement Amounts Allocated to Reinsurance"),
    ("C2f", "RI_Note", "total_cash_inflows_outflows", "RI", 62, 1.0,
     "RI note cash flows == movement Total Cash Flows"),
)

DEFAULT_TOL_ABS = 1.0
DEFAULT_TOL_REL = 1e-4


@dataclass(frozen=True)
class ControlResult:
    id: str
    label: str
    view: str
    note_value: float
    movement_value: float
    passed: bool

    @property
    def delta(self) -> float:
        return self.note_value - self.movement_value


def note_controls(
    view: dict,
    tables: dict[str, NoteTable] | None = None,
    *,
    tol_abs: float = DEFAULT_TOL_ABS,
    tol_rel: float = DEFAULT_TOL_REL,
) -> list[ControlResult]:
    """Evaluate every note-vs-movement control for one view.

    A control breaches when the difference exceeds **both** an absolute floor and a
    relative fraction of the compared magnitude — the same two-sided tolerance the
    roll-forward reconciliation uses, so tiny balances don't false-positive and large ones
    aren't masked. Float equality would flap: these are sums over dozens of pairs.
    """
    tables = tables if tables is not None else build_notes(view)
    movement: dict[str, dict[str, dict[str, float]]] = {}
    for name, sres in (view.get("sheets") or {}).items():
        if name in SCHEMA.sheets and isinstance(sres, SheetResult):
            movement[name] = line_totals(SCHEMA.sheets[name], sres)

    out: list[ControlResult] = []
    label = str(view.get("label") or "")
    for cid, note, line_id, sheet, row, factor, description in _CONTROLS:
        table = tables.get(note)
        if table is None or sheet not in movement:
            continue
        schema_line = next((ln for ln in SCHEMA.sheets[sheet].lines if ln.row == row), None)
        if schema_line is None:
            continue
        note_value = table.value(line_id)
        movement_value = factor * movement[sheet].get(schema_line.id, {}).get("Total", 0.0)
        delta = abs(note_value - movement_value)
        scale = max(abs(note_value), abs(movement_value), 1.0)
        out.append(
            ControlResult(
                id=cid, label=description, view=label,
                note_value=note_value, movement_value=movement_value,
                passed=not (delta > tol_abs and delta > tol_rel * scale),
            )
        )

    # C4 — internal coherence of IS/BS against the note closings (entity grain only).
    bs, is_ = tables.get("BS"), tables.get("IS")
    if bs is not None and "Gross_Note" in tables:
        for cid, line_id, note in (
            ("C4a", "insurance_contract_liabilities", "Gross_Note"),
            ("C4b", "reinsurance_contract_assets", "RI_Note"),
        ):
            if note not in tables:
                continue
            a, b = bs.value(line_id), tables[note].value("closing_balance_net")
            out.append(ControlResult(
                id=cid, label=f"BS {line_id.replace('_', ' ')} == {note} closing",
                view=label, note_value=a, movement_value=b,
                passed=abs(a - b) <= max(tol_abs, tol_rel * max(abs(a), abs(b), 1.0)),
            ))
    if is_ is not None:
        result = is_.value("insurance_service_result")
        parts = (is_.value("insurance_revenue") + is_.value("insurance_service_expenses")
                 + is_.value("net_expenses_from_reinsurance_contracts"))
        out.append(ControlResult(
            id="C4c", label="IS insurance service result == revenue + expenses + RI",
            view=label, note_value=result, movement_value=parts,
            passed=abs(result - parts) <= max(tol_abs, tol_rel * max(abs(result), 1.0)),
        ))
    return out


def closing_gap(view: dict, tables: dict[str, NoteTable] | None = None) -> dict[str, float]:
    """C3 — the note's closing against the movement's roll-forward closing, per sheet.

    **Not** a pass/fail. The note omits movement lines (effect of exchange rates, other
    movements, investment components), so a gap is expected; it is reported so a reader
    can see its size rather than discover it later.
    """
    tables = tables if tables is not None else build_notes(view)
    gaps: dict[str, float] = {}
    for note, sheet in (("Gross_Note", "Gross"), ("RI_Note", "RI")):
        sres = (view.get("sheets") or {}).get(sheet)
        if note not in tables or not isinstance(sres, SheetResult):
            continue
        gaps[sheet] = round(
            tables[note].value("closing_balance_net") - sum(sres.closing_rollforward.values()), 2
        )
    return gaps


def notes_report(result, *, levels: tuple[str, ...] = ("entity", "class", "cohort")) -> dict:
    """Machine-readable control report for ``movement_warnings``.

    Takes a ``MovementResult`` — symmetric with ``reconciliation_report`` — so callers
    (and their test seams) have a single collaborator to patch rather than two.
    """
    from .notes_schema import STATUS_ASSUMED, deviations  # local: avoids a cycle at import

    results: list[ControlResult] = []
    entity_gap: dict[str, float] = {}
    for view in aggregated_views(result, levels=levels):
        tables = build_notes(view)
        results.extend(note_controls(view, tables))
        if view.get("level") == "entity":
            entity_gap = closing_gap(view, tables)

    breaches = [r for r in results if not r.passed]
    breaches.sort(key=lambda r: -abs(r.delta))
    return {
        "controls_checked": len(results),
        "breaches": len(breaches),
        "ties_out": not breaches,
        "top_breaches": [
            {"id": r.id, "label": r.label, "view": r.view,
             "note": round(r.note_value, 2), "movement": round(r.movement_value, 2),
             "delta": round(r.delta, 2)}
            for r in breaches[:25]
        ],
        # Expected, not a failure — see closing_gap().
        "closing_gap_vs_rollforward": entity_gap,
        "deviations_assumed": len(deviations(STATUS_ASSUMED)),
    }


def sum_note_tables(tables: list[dict[str, NoteTable]]) -> dict[str, NoteTable]:
    """Additively combine per-view note tables — used only to *assert* additivity in tests
    and controls. Production builds each grain's notes from that grain's SheetResults,
    which is equivalent because every note line is linear in the movement values."""
    if not tables:
        return {}
    out: dict[str, NoteTable] = {}
    for name in tables[0]:
        first = tables[0][name]
        lines = []
        for i, proto in enumerate(first.lines):
            values: dict[str, float | None] = {}
            for col in proto.values:
                if all(t[name].lines[i].values.get(col) is None for t in tables):
                    values[col] = None  # a dash everywhere stays a dash
                else:
                    values[col] = sum(_num(t[name].lines[i].values.get(col)) for t in tables)
            lines.append(
                NoteLineValue(id=proto.id, row=proto.row, label=proto.label,
                              kind=proto.kind, values=values)
            )
        out[name] = NoteTable(
            name=name, title=first.title, columns=first.columns, lines=tuple(lines)
        )
    return out
