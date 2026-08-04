"""Schema-as-code for the IFRS 17 note disclosure — ``Gross_Note`` / ``RI_Note`` / ``IS`` / ``BS``.

These four sheets are a *re-presentation* of the Gross/RI movement tables: every cell is a
literal, a reference into a movement line, a reference into another note, or a sum within
the note itself. No new measurement, no new data source (plan §1).

Two layers, deliberately separated:

* ``notes_source.json`` — the client's four sheets extracted **verbatim, defects included**
  (regenerate with ``scripts/extract_client_disclosure.py --write``).
* ``DEVIATIONS`` below — the curated corrections, each carrying its defect id, the client's
  literal cell, our resolution and the evidence. Nothing diverges from the signed source
  without an entry here, and every entry is one line to reverse.

References are resolved from Excel rows to **line ids** at load time, so a future
renumbering of either the movement template or a note fails loudly at import instead of
silently resolving to zero — the failure mode that hid E1–E3 for a release.

Pure stdlib (no Django / pandas).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .schema import SCHEMA

_SOURCE_PATH = Path(__file__).with_name("notes_source.json")

#: Bumped independently of the movement SCHEMA_VERSION so a note-only revision does not
#: invalidate movement-schema-keyed consumers (plan §12.1).
NOTES_SCHEMA_VERSION = "2026.07"

STATUS_ASSUMED = "assumed"
STATUS_CONFIRMED = "client_confirmed"


@dataclass(frozen=True)
class Deviation:
    """One documented departure from the client's literal cell."""

    id: str  # defect id from the plan (D1 … D8)
    note: str
    row: int  # row in the client's note sheet
    columns: dict[str, dict]  # column key -> replacement source (notes_source.json shape)
    client_cell: str  # what the client's file literally contains
    resolution: str  # what we do instead
    evidence: str  # why — the tie-out or structural argument
    status: str = STATUS_ASSUMED


#: The deviation ledger (plan §8). Ordered by defect id.
DEVIATIONS: tuple[Deviation, ...] = (
    Deviation(
        id="D7",
        note="Gross_Note",
        row=13,
        columns={
            bucket: {"kind": "movement",
                     "terms": [{"sheet": "Gross", "bucket": bucket, "row": 26, "factor": -1.0}]}
            for bucket in ("LRC_excl_LC", "Loss_Component", "LIC_excl_RA", "Risk_Adjustment")
        },
        client_cell="=Gross!C26 … =Gross!I26 (unflipped)",
        resolution="negated: insurance revenue releases the LRC, so it reduces the liability",
        evidence=(
            "IS!C8 = SUM(C5:C7) is labelled 'Insurance service result' while C6 (expenses) is "
            "positive and C22/C27 are labelled 'loss' while positive — the statement is "
            "expense-positive, so revenue must enter negative for C8 to be a result at all. "
            "Matches the IFRS 17 disclosure form, and fits the client's own closing balance at "
            "3.9% against 7.4% unflipped (plan §5.1)."
        ),
    ),
    Deviation(
        id="D4",
        note="Gross_Note",
        row=17,
        columns={"Loss_Component": {
            "kind": "movement",
            "terms": [{"sheet": "Gross", "bucket": "Loss_Component", "row": 42}],
        }},
        client_cell="=Gross!E31 (the whole 'Insurance service expenses' subtotal)",
        resolution="=Gross!E42 — the onerous-contract line the label describes",
        evidence=(
            "Note rows 16-19 are a partition of Gross row 31; citing row 31 from inside its own "
            "decomposition is circular. Numerically identical today only because row 42 is the "
            "sole Loss Component contributor."
        ),
    ),
    Deviation(
        id="D3",
        note="Gross_Note",
        row=18,
        columns={
            "LIC_excl_RA": {"kind": "movement",
                            "terms": [{"sheet": "Gross", "bucket": "LIC_excl_RA", "row": 47}]},
            "Risk_Adjustment": {"kind": "movement",
                                "terms": [{"sheet": "Gross", "bucket": "Risk_Adjustment", "row": 47}]},
        },
        client_cell="=Gross!G45 / =Gross!I45 (a Loss Component amortisation row)",
        resolution="=Gross!G47 / =Gross!I47 — 'Past Service: Changes to liabilities for incurred claims'",
        evidence=(
            "The label reads 'changes that relate to past service - adjustments to the LIC'. "
            "Repointing makes Gross_Note!F20 equal Gross!J31 exactly: 501,110,496.06 (plan §3.1)."
        ),
    ),
    Deviation(
        id="D1",
        note="Gross_Note",
        row=26,
        columns={
            bucket: {"kind": "movement", "terms": [
                {"sheet": "Gross", "bucket": bucket, "row": 67},
                {"sheet": "Gross", "bucket": bucket, "row": 68},
            ]}
            for bucket in ("LIC_excl_RA", "Risk_Adjustment")
        },
        client_cell="the literal text 'Gross!G67+Gross!G67' — no leading '=', same cell twice",
        resolution="=Gross!G67+Gross!G68 — claims paid + directly attributable expenses paid",
        evidence=(
            "The row label is 'Claims AND OTHER DIRECTLY ATTRIBUTABLE EXPENSES paid', and this is "
            "what makes the note's cash section reconstruct Gross!J71 exactly (plan §4.1)."
        ),
    ),
    Deviation(
        id="D1b",
        note="Gross_Note",
        row=27,
        columns={"LRC_excl_LC": {"kind": "movement", "terms": [
            {"sheet": "Gross", "bucket": "LRC_excl_LC", "row": 69},
            {"sheet": "Gross", "bucket": "LRC_excl_LC", "row": 70},
        ]}},
        client_cell="=Gross!C69 only",
        resolution="=Gross!C69+Gross!C70 — acquisition cash flows plus 'Other Cash Flows'",
        evidence=(
            "Gross row 40 ('Other Acquisition Cash Flows') and row 70 ('Other Cash Flows') are "
            "exactly equal and opposite in the client's Total column (±49,658,854.56684359), so "
            "row 70 is the cash leg of the acquisition line. Without it the note silently drops "
            "49.7m of cash flow and no longer ties to Gross!J71."
        ),
    ),
    Deviation(
        id="D5",
        note="Gross_Note",
        row=32,
        columns={"Total": {"kind": "row_total", "columns": [
            "LRC_excl_LC", "Loss_Component", "LIC_excl_RA", "Risk_Adjustment",
        ]}},
        client_cell="=F12+F23+F29 — three blank spacer rows, so permanently 0",
        resolution="=SUM(B32:E32) — the row's own buckets",
        evidence="Every other Total cell in both notes is the SUM of its own row.",
    ),
    Deviation(
        id="D4",
        note="RI_Note",
        row=17,
        columns={"Loss_Recovery_Component": {
            "kind": "movement",
            "terms": [{"sheet": "RI", "bucket": "Loss_Recovery_Component", "row": 33}],
        }},
        client_cell="=RI!F27 (the whole 'Amounts Recoverable from Reinsurance' subtotal)",
        resolution="=RI!F33 — 'Future Service: LRC for new onerous contracts and reversal'",
        evidence="Mirror of the Gross D4: note rows 16-18 are a partition of RI row 27.",
    ),
    Deviation(
        id="D2",
        note="RI_Note",
        row=25,
        columns={"Assets_Remaining_Coverage": {"kind": "movement", "terms": [
            {"sheet": "RI", "bucket": "Assets_Remaining_Coverage", "row": 57},
            {"sheet": "RI", "bucket": "Assets_Remaining_Coverage", "row": 59},
            {"sheet": "RI", "bucket": "Assets_Remaining_Coverage", "row": 61},
        ]}},
        client_cell="the literal text 'RI!D57+RI!D59' — no leading '='",
        resolution="=RI!D57+RI!D59+RI!D61 — premium paid + fixed commission + other cash flows",
        evidence=(
            "Completeness: with row 61 the RI cash rows reconstruct RI!K62 exactly. Row 61 is a "
            "tier-M line (0 until an override fills it), so this is a no-op today and structurally "
            "correct thereafter."
        ),
    ),
    Deviation(
        id="D2b",
        note="RI_Note",
        row=26,
        columns={"Amounts_Recoverable_IC": {"kind": "movement", "terms": [
            {"sheet": "RI", "bucket": "Amounts_Recoverable_IC", "row": 58},
            {"sheet": "RI", "bucket": "Amounts_Recoverable_IC", "row": 60},
        ]}},
        client_cell="=RI!H58 only",
        resolution="=RI!H58+RI!H60 — claims received + profit/sliding-scale commission received",
        evidence="Same completeness tie-out to RI!K62; row 60 is tier-M, so a no-op today.",
    ),
    *(
        Deviation(
            id="D6",
            note="IS",
            row=row,
            columns={"Total": {"kind": "const", "value": 0.0}},
            client_cell=f"{label}: no value cell at all",
            resolution="explicit 0",
            evidence=(
                "An IFRS 9 / general-ledger item with no source anywhere in the Module 2 "
                "pipeline. Excel already coerces the blank to 0 inside the sums that consume it "
                "(IS!C13, IS!C27), so this makes the file's own behaviour explicit and auditable."
            ),
        )
        for row, label in ((11, "Expected credit loss on financial assets"),
                           (20, "IFRS9 Adjustments"),
                           (24, "Zakat expense"))
    ),
)


@dataclass(frozen=True)
class MovementTerm:
    """One addend reading a movement-sheet line."""

    sheet: str  # "Gross" | "RI"
    line: str  # movement schema line id
    bucket: str  # movement bucket, or "Total"
    factor: float = 1.0


@dataclass(frozen=True)
class NoteRef:
    """A reference into another note table."""

    note: str
    line: str
    column: str


@dataclass(frozen=True)
class ColumnSource:
    """How one (line, column) cell of a note gets its value."""

    kind: str  # movement | note | sum | row_total | const | dash
    terms: tuple[MovementTerm, ...] = ()
    ref: NoteRef | None = None
    lines: tuple[str, ...] = ()  # sum: note line ids in this note
    columns: tuple[str, ...] = ()  # row_total: which columns to add
    value: float = 0.0
    client_literal: str | None = None  # set when the client's cell was unparsed text


@dataclass(frozen=True)
class NoteLine:
    id: str
    row: int  # provenance: row in the client's note sheet
    label: str
    kind: str  # section | input | subtotal
    columns: dict[str, ColumnSource] = field(default_factory=dict)

    @property
    def is_value_line(self) -> bool:
        return self.kind != "section"


@dataclass(frozen=True)
class NoteSheet:
    name: str
    title: str
    columns: tuple[str, ...]
    value_columns: tuple[str, ...]  # measurement columns (excludes "Total")
    source_sheet: str | None  # the movement sheet it re-presents, if any
    lines: tuple[NoteLine, ...]

    def line(self, line_id: str) -> NoteLine:
        for ln in self.lines:
            if ln.id == line_id:
                return ln
        raise KeyError(f"{self.name}: no line {line_id!r}")


@dataclass(frozen=True)
class NotesSchema:
    version: str
    sheets: dict[str, NoteSheet]


def _movement_line_id(sheet: str, row: int) -> str | None:
    for ln in SCHEMA.sheets[sheet].lines:
        if ln.row == row:
            return ln.id
    return None


def _build_source(raw: dict, *, row_ids: dict[int, str], note_row_ids: dict[str, dict[int, str]]):
    kind = raw["kind"]
    literal = raw.get("literal_text")
    if kind == "movement":
        terms = tuple(
            MovementTerm(
                sheet=t["sheet"],
                line=_movement_line_id(t["sheet"], t["row"]) or f"<row {t['row']}>",
                bucket=t["bucket"],
                factor=float(t.get("factor", 1.0)),
            )
            for t in raw["terms"]
        )
        return ColumnSource(kind=kind, terms=terms, client_literal=literal)
    if kind == "note":
        ref = raw["ref"]
        target_rows = note_row_ids.get(ref["note"], {})
        return ColumnSource(
            kind=kind,
            ref=NoteRef(note=ref["note"],
                        line=target_rows.get(ref["row"], f"<row {ref['row']}>"),
                        column=ref["column"]),
            client_literal=literal,
        )
    if kind == "sum":
        return ColumnSource(
            kind=kind,
            lines=tuple(row_ids.get(r, f"<row {r}>") for r in raw["rows"]),
            client_literal=literal,
        )
    if kind == "row_total":
        return ColumnSource(kind=kind, columns=tuple(raw["columns"]), client_literal=literal)
    if kind == "const":
        return ColumnSource(kind=kind, value=float(raw["value"]), client_literal=literal)
    if kind == "dash":
        return ColumnSource(kind=kind, client_literal=literal)
    return ColumnSource(kind="unparsed", client_literal=literal or raw.get("detail"))


@lru_cache(maxsize=1)
def _load() -> NotesSchema:
    raw = json.loads(_SOURCE_PATH.read_text(encoding="utf-8"))

    # Apply the deviation ledger to the raw source before resolving references.
    by_note: dict[str, dict[int, dict]] = {}
    for name, sh in raw["sheets"].items():
        by_note[name] = {ln["row"]: ln for ln in sh["lines"]}
    for dev in DEVIATIONS:
        line = by_note.get(dev.note, {}).get(dev.row)
        if line is None:
            continue  # surfaced by validate_notes_schema()
        line["sources"].update({col: dict(src) for col, src in dev.columns.items()})
        if line["kind"] == "section" and line["sources"]:
            line["kind"] = "input"  # D6: a value line whose cell the client omitted

    note_row_ids = {
        name: {ln["row"]: ln["id"] for ln in sh["lines"]} for name, sh in raw["sheets"].items()
    }

    sheets: dict[str, NoteSheet] = {}
    for name, sh in raw["sheets"].items():
        row_ids = note_row_ids[name]
        lines = tuple(
            NoteLine(
                id=ln["id"],
                row=ln["row"],
                label=ln["label"],
                kind=ln["kind"],
                columns={
                    col: _build_source(src, row_ids=row_ids, note_row_ids=note_row_ids)
                    for col, src in ln["sources"].items()
                },
            )
            for ln in sh["lines"]
        )
        columns = tuple(sh["columns"])
        sheets[name] = NoteSheet(
            name=name,
            title=sh["title"],
            columns=columns,
            value_columns=tuple(c for c in columns if c != "Total"),
            source_sheet=sh["source_sheet"],
            lines=lines,
        )
    return NotesSchema(version=NOTES_SCHEMA_VERSION, sheets=sheets)


NOTES_SCHEMA: NotesSchema = _load()


def get_note(name: str) -> NoteSheet:
    return NOTES_SCHEMA.sheets[name]


def deviations(status: str | None = None) -> tuple[Deviation, ...]:
    """The ledger, optionally filtered by status. ``assumed`` entries are surfaced on the
    job's warnings so the sign-off banner can show what is pending confirmation."""
    if status is None:
        return DEVIATIONS
    return tuple(d for d in DEVIATIONS if d.status == status)


def validate_notes_schema() -> list[str]:
    """Structural integrity check. Returns a list of problems ([] == valid).

    Asserts: unique line ids; every movement term resolves to a real movement line and a
    declared bucket; every note reference resolves to a real note/line/column; every sum
    references real lines of the same note; row_total columns are declared; no cycles in
    intra-note sums; nothing left unparsed; and every deviation targets a real line.
    """
    problems: list[str] = []

    for dev in DEVIATIONS:
        sheet = NOTES_SCHEMA.sheets.get(dev.note)
        if sheet is None:
            problems.append(f"deviation {dev.id}: unknown note {dev.note!r}")
            continue
        if not any(ln.row == dev.row for ln in sheet.lines):
            problems.append(f"deviation {dev.id}: {dev.note} has no row {dev.row}")
        for col in dev.columns:
            if col not in sheet.columns:
                problems.append(f"deviation {dev.id}: {dev.note} has no column {col!r}")

    for name, sheet in NOTES_SCHEMA.sheets.items():
        ids = [ln.id for ln in sheet.lines]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            problems.append(f"{name}: duplicate line ids {sorted(dupes)}")
        id_set = set(ids)

        for ln in sheet.lines:
            for col, src in ln.columns.items():
                where = f"{name}.{ln.id}.{col}"
                if col not in sheet.columns:
                    problems.append(f"{where}: undeclared column")
                if src.kind == "unparsed":
                    problems.append(f"{where}: unparsed client cell {src.client_literal!r}")
                for term in src.terms:
                    msheet = SCHEMA.sheets.get(term.sheet)
                    if msheet is None:
                        problems.append(f"{where}: unknown movement sheet {term.sheet!r}")
                        continue
                    if term.line not in {m.id for m in msheet.lines}:
                        problems.append(f"{where}: movement line {term.line!r} does not exist")
                    if term.bucket not in msheet.buckets:
                        problems.append(f"{where}: {term.sheet} has no bucket {term.bucket!r}")
                if src.ref is not None:
                    target = NOTES_SCHEMA.sheets.get(src.ref.note)
                    if target is None:
                        problems.append(f"{where}: unknown note {src.ref.note!r}")
                    elif src.ref.line not in {t.id for t in target.lines}:
                        problems.append(f"{where}: note line {src.ref.line!r} does not exist")
                    elif src.ref.column not in target.columns:
                        problems.append(f"{where}: {src.ref.note} has no column {src.ref.column!r}")
                for lid in src.lines:
                    if lid not in id_set:
                        problems.append(f"{where}: sums missing line {lid!r}")
                for c in src.columns:
                    if c not in sheet.value_columns:
                        problems.append(f"{where}: row_total over undeclared column {c!r}")

        problems.extend(_cycles(sheet))

    return problems


def _cycles(sheet: NoteSheet) -> list[str]:
    """Detect cycles in a note's intra-sheet sums (a self-referential subtotal would make
    the renderer's evaluation order undefined)."""
    graph = {
        ln.id: {lid for src in ln.columns.values() for lid in src.lines} for ln in sheet.lines
    }
    problems: list[str] = []
    WHITE, GREY, BLACK = 0, 1, 2
    colour = dict.fromkeys(graph, WHITE)

    def visit(node: str, path: list[str]) -> None:
        colour[node] = GREY
        for nxt in graph.get(node, ()):  # missing ids already reported
            if colour.get(nxt) == GREY:
                problems.append(f"{sheet.name}: cyclic sum {' -> '.join(path + [nxt])}")
            elif colour.get(nxt) == WHITE:
                visit(nxt, path + [nxt])
        colour[node] = BLACK

    for node in graph:
        if colour[node] == WHITE:
            visit(node, [node])
    return problems
