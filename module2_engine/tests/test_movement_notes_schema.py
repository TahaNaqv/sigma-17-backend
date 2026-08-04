"""Structural integrity of the IFRS 17 note schema (Gross_Note / RI_Note / IS / BS).

The notes are a re-presentation of the movement sheets, so almost every failure mode here
is a *reference* failure: a row that no longer exists, a bucket that was renamed, a
correction that silently stopped applying. These tests pin the references by line id and
pin each documented deviation to the evidence that justifies it.

No actuarial assertions — that is sign-off (plan §11).
"""

import pytest

from module2_engine.movement.notes_schema import (
    DEVIATIONS,
    NOTES_SCHEMA,
    STATUS_ASSUMED,
    STATUS_CONFIRMED,
    deviations,
    validate_notes_schema,
)
from module2_engine.movement.schema import SCHEMA


def _line(note: str, row: int):
    return next(ln for ln in NOTES_SCHEMA.sheets[note].lines if ln.row == row)


def _movement_rows(note: str, row: int, column: str) -> set[int]:
    """The movement rows a note cell reads, resolved back through the schema.

    Keyed by (sheet, line id) — several ids exist on *both* sheets (``other_cash_flows``
    is Gross r70 and RI r61; ``past_service_changes_to_liabilities…`` is Gross r47 and RI
    r38), so a sheet-blind lookup silently resolves to the wrong row.
    """
    src = _line(note, row).columns[column]
    by_id = {
        (name, ln.id): ln.row for name, sheet in SCHEMA.sheets.items() for ln in sheet.lines
    }
    return {by_id[(t.sheet, t.line)] for t in src.terms}


def test_notes_schema_is_structurally_valid():
    assert validate_notes_schema() == []


def test_all_four_note_sheets_present_with_expected_shape():
    assert set(NOTES_SCHEMA.sheets) == {"Gross_Note", "RI_Note", "IS", "BS"}
    for name, source in (("Gross_Note", "Gross"), ("RI_Note", "RI")):
        sheet = NOTES_SCHEMA.sheets[name]
        assert sheet.source_sheet == source
        assert sheet.columns[-1] == "Total"
        assert len(sheet.value_columns) == 4
        assert sheet.value_columns == SCHEMA.sheets[source].value_buckets
    for name in ("IS", "BS"):
        assert NOTES_SCHEMA.sheets[name].columns == ("Total",)
        assert NOTES_SCHEMA.sheets[name].source_sheet is None


def test_every_note_line_resolves_to_real_movement_lines():
    """No reference may dangle — the failure mode that hid E1-E3 for a release."""
    valid = {name: {ln.id for ln in sheet.lines} for name, sheet in SCHEMA.sheets.items()}
    for sheet in NOTES_SCHEMA.sheets.values():
        for ln in sheet.lines:
            for src in ln.columns.values():
                for term in src.terms:
                    assert term.line in valid[term.sheet], f"{sheet.name}.{ln.id} -> {term.line}"


def test_no_unparsed_client_cells_survive():
    """The client's three unparsed text cells (missing '=') must all be corrected; nothing
    may reach the renderer as raw text."""
    for sheet in NOTES_SCHEMA.sheets.values():
        for ln in sheet.lines:
            for col, src in ln.columns.items():
                assert src.kind != "unparsed", f"{sheet.name}.{ln.id}.{col}"
                assert src.client_literal is None, (
                    f"{sheet.name}.{ln.id}.{col} still carries the client's literal "
                    f"{src.client_literal!r} — it needs a deviation"
                )


# ── the deviation ledger ─────────────────────────────────────────────────────

def test_every_deviation_targets_a_real_line_and_is_documented():
    assert DEVIATIONS, "the ledger must not be empty"
    for dev in DEVIATIONS:
        sheet = NOTES_SCHEMA.sheets[dev.note]
        assert any(ln.row == dev.row for ln in sheet.lines), f"{dev.id}: no row {dev.row}"
        assert dev.columns, f"{dev.id}: replaces nothing"
        assert dev.client_cell and dev.resolution and dev.evidence, f"{dev.id}: undocumented"
        assert dev.status in (STATUS_ASSUMED, STATUS_CONFIRMED)


def test_ledger_is_fully_pending_client_confirmation():
    """Every correction is ours until the client's actuary acknowledges it. When one is
    confirmed, flip its status and this count moves — deliberately visible."""
    assert len(deviations(STATUS_ASSUMED)) == len(DEVIATIONS)
    assert deviations(STATUS_CONFIRMED) == ()


def test_d7_insurance_revenue_enters_negative():
    """Revenue releases the LRC, so it reduces the liability (plan §11 Q1)."""
    revenue = _line("Gross_Note", 13)
    for column in NOTES_SCHEMA.sheets["Gross_Note"].value_columns:
        terms = revenue.columns[column].terms
        assert len(terms) == 1
        assert terms[0].line == "insurance_revenue"
        assert terms[0].factor == -1.0


def test_d3_past_service_points_at_the_lic_row_not_the_loss_component_row():
    """Repointing row 18 is what makes Gross_Note!F20 tie to Gross!J31 exactly."""
    for column in ("LIC_excl_RA", "Risk_Adjustment"):
        assert _movement_rows("Gross_Note", 18, column) == {47}


def test_d4_onerous_lines_point_at_the_line_not_its_enclosing_subtotal():
    """Note rows 16-19 partition the movement's service-expense subtotal; citing that
    subtotal from inside its own decomposition is circular."""
    assert _movement_rows("Gross_Note", 17, "Loss_Component") == {42}
    assert _movement_rows("RI_Note", 17, "Loss_Recovery_Component") == {33}


def test_d5_closing_assets_total_sums_its_own_row():
    src = _line("Gross_Note", 32).columns["Total"]
    assert src.kind == "row_total"
    assert set(src.columns) == set(NOTES_SCHEMA.sheets["Gross_Note"].value_columns)


def test_d6_general_ledger_lines_are_explicit_zeros():
    """The client left these cells empty while summing over them; Excel coerced them to 0."""
    for row in (11, 20, 24):
        line = _line("IS", row)
        assert line.kind == "input", f"IS r{row} must be a value line, not a section"
        assert line.columns["Total"].kind == "const"
        assert line.columns["Total"].value == 0.0


# ── the cash-flow completeness tie-out (plan §4.1) ───────────────────────────

def _movement_cashflow_rows(sheet_name: str) -> set[int]:
    """Input rows below the movement sheet's 'Cash flows' section header."""
    sheet = SCHEMA.sheets[sheet_name]
    start = next(ln.row for ln in sheet.lines
                 if ln.kind == "section" and "cash flow" in ln.label.lower())
    return {ln.row for ln in sheet.lines if ln.kind == "input" and ln.row > start}


@pytest.mark.parametrize(
    "note, rows, source",
    [("Gross_Note", (25, 26, 27), "Gross"), ("RI_Note", (25, 26), "RI")],
)
def test_note_cash_section_covers_every_movement_cash_line(note, rows, source):
    """The note's cash block must reconstruct the movement sheet's total cash flows — no
    line silently dropped. This is what fixes D1/D2 and forces the 'Other Cash Flows'
    fold-in (without it the Gross note loses 49.7m)."""
    covered: set[int] = set()
    for row in rows:
        for column in NOTES_SCHEMA.sheets[note].value_columns:
            src = _line(note, row).columns.get(column)
            if src is not None and src.terms:
                covered |= _movement_rows(note, row, column)
    assert covered == _movement_cashflow_rows(source)


# ── IS / BS wiring ───────────────────────────────────────────────────────────

def test_is_and_bs_read_the_note_totals():
    assert _line("BS", 4).columns["Total"].ref.note == "Gross_Note"
    assert _line("BS", 4).columns["Total"].ref.line == "closing_balance_net"
    assert _line("BS", 5).columns["Total"].ref.line == "closing_balance_net"
    assert _line("BS", 5).columns["Total"].ref.note == "RI_Note"

    assert _line("IS", 5).columns["Total"].ref.line == "insurance_revenue"
    assert _line("IS", 6).columns["Total"].ref.line == "insurance_service_expenses_2"
    assert _line("IS", 7).columns["Total"].ref.line == "total_changes_in_the_statement_of_income"
    for row in (5, 6, 7):
        assert _line("IS", row).columns["Total"].ref.column == "Total"


def test_is_finance_lines_read_the_movement_total_column():
    """The client cross-checks finance against Gross!J57 / RI!K48 rather than the notes."""
    assert _movement_rows("IS", 14, "Total") == {57}
    assert _movement_rows("IS", 15, "Total") == {48}
    assert _line("IS", 14).columns["Total"].terms[0].bucket == "Total"
