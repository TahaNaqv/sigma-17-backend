"""Evaluation of the IFRS 17 note disclosure — ``notes.build_notes``.

Pins the tie-outs that make the notes trustworthy: they must agree with the movement
sheet they re-present, they must aggregate additively across grains, and the client's
``-`` cells must behave the way Excel behaves.
"""

import types

import pandas as pd
import pytest

from module2_engine.movement.compute import aggregated_views, build_sama_movement, line_totals
from module2_engine.movement.notes import (
    build_notes,
    closing_gap,
    note_controls,
    notes_report,
    sum_note_tables,
)
from module2_engine.movement.notes_schema import DEVIATIONS, NOTES_SCHEMA
from module2_engine.movement.schema import SCHEMA

TOL = 1e-6


def _frames(rows):
    ifrs = pd.DataFrame(rows)
    lc = pd.DataFrame([
        {"RESERVINGCLASS": r["RESERVINGCLASS"], "UWY": r["UWY"],
         "LC Discounted_PY": 0.0, "LC Discounted_CY": 0.0, "Loss Recovery Component": 0.0}
        for r in rows
    ])
    return types.SimpleNamespace(ifrs_summary_df=ifrs, allocate_sheets={"LC": lc})


def _row(rc="MOTOR", uwy=2023, **over):
    base = {
        "RESERVINGCLASS": rc, "UWY": uwy,
        "Gross UPR_prev": 100.0, "Gross UPR_curr": 130.0,
        "GROSS - Outstanding_prev": 200.0, "GROSS - Outstanding_curr": 210.0,
        "GWP": 90.0, "Commission Expense": 7.0,
        "Premium Received": 40.0, "Claims Paid": 30.0,
        "GROSS - Insurance Finance (Income)/Expense": 5.0,
        "GROSS - CY O/S": 12.0, "GROSS - CY RA (OS)": 3.0,
    }
    base.update(over)
    return base


def _views(rows, levels=("entity", "class", "cohort")):
    return aggregated_views(build_sama_movement(_frames(rows)), levels=levels)


def _entity(rows=None):
    return _views(rows or [_row()], levels=("entity",))[0]


def _movement_total(view, sheet: str, row: int) -> float:
    """The movement sheet's Total for an Excel row, resolved the same way the sheet is."""
    line = next(ln for ln in SCHEMA.sheets[sheet].lines if ln.row == row)
    return line_totals(SCHEMA.sheets[sheet], view["sheets"][sheet])[line.id]["Total"]


def test_build_notes_returns_all_four_tables():
    notes = build_notes(_entity())
    assert set(notes) == {"Gross_Note", "RI_Note", "IS", "BS"}
    for name, table in notes.items():
        assert table.columns == NOTES_SCHEMA.sheets[name].columns
        assert len(table.lines) == len(NOTES_SCHEMA.sheets[name].lines)


# ── agreement with the movement sheet (plan §6.5 controls C1/C2) ─────────────

def test_c1_note_service_expenses_ties_to_the_movement_subtotal():
    """The decisive tie-out (plan §3.1): with row 18 pointed at the past-service row, the
    note's Insurance service expenses equals Gross row 31 exactly."""
    view = _entity()
    note = build_notes(view)["Gross_Note"]
    assert note.value("insurance_service_expenses_2") == pytest.approx(
        _movement_total(view, "Gross", 31), abs=TOL
    )


def test_c2_revenue_is_the_negated_movement_revenue_and_finance_matches():
    view = _entity()
    note = build_notes(view)["Gross_Note"]
    assert note.value("insurance_revenue") == pytest.approx(
        -_movement_total(view, "Gross", 26), abs=TOL
    )
    assert note.value("finance_expense_from_insurance_contracts") == pytest.approx(
        _movement_total(view, "Gross", 57), abs=TOL
    )


def test_note_cash_block_ties_to_the_movement_total_cash_flows():
    """The §4.1 completeness tie-out, evaluated rather than merely declared."""
    view = _entity()
    note = build_notes(view)["Gross_Note"]
    assert note.value("total_cash_inflows_outflows") == pytest.approx(
        _movement_total(view, "Gross", 71), abs=TOL
    )


def test_note_closing_is_opening_plus_changes_plus_cash():
    note = build_notes(_entity())["Gross_Note"]
    expected = (
        note.value("opening_balance_net")
        + note.value("total_changes_in_the_statement_of_income")
        + note.value("total_cash_inflows_outflows")
    )
    assert note.value("closing_balance_net") == pytest.approx(expected, abs=TOL)


def test_row_totals_equal_the_sum_of_their_buckets():
    note = build_notes(_entity())["Gross_Note"]
    buckets = NOTES_SCHEMA.sheets["Gross_Note"].value_columns
    for line in note.lines:
        if not line.values or line.values.get("Total") is None:
            continue
        assert line.values["Total"] == pytest.approx(
            sum(v for b in buckets if (v := line.values.get(b)) is not None), abs=TOL
        ), line.id


# ── additivity across grains ─────────────────────────────────────────────────

def test_entity_notes_equal_the_sum_of_the_class_notes():
    """Every note line is linear in the movement values, so aggregation is free — this
    is what lets the same tables be produced at entity, class and cohort grain."""
    rows = [_row("MOTOR", 2023), _row("PROPERTY", 2022, GWP=50.0, **{"Commission Expense": 3.0})]
    views = _views(rows)
    entity = build_notes(next(v for v in views if v["level"] == "entity"))
    classes = [build_notes(v) for v in views if v["level"] == "class"]
    summed = sum_note_tables(classes)

    for name, table in entity.items():
        for line in table.lines:
            for col, value in line.values.items():
                other = summed[name].line(line.id).values[col]
                if value is None:
                    assert other is None, f"{name}.{line.id}.{col}"
                else:
                    assert value == pytest.approx(other, abs=1e-6), f"{name}.{line.id}.{col}"


def test_all_constant_sources_are_zero():
    """Additivity holds only because every hard-coded note cell is 0 — a non-zero constant
    would be counted once at entity level and again in each class."""
    for sheet in NOTES_SCHEMA.sheets.values():
        for line in sheet.lines:
            for col, src in line.columns.items():
                if src.kind == "const":
                    assert src.value == 0.0, f"{sheet.name}.{line.id}.{col}"


# ── the client's "-" cells ───────────────────────────────────────────────────

def test_dash_cells_render_as_none_but_count_as_zero_in_sums():
    note = build_notes(_entity())["RI_Note"]
    liabilities = note.line("reinsurance_contract_liabilities_opening")
    assert all(v is None for v in liabilities.values.values())
    # the net line sums assets + the dashed liabilities, and must equal the assets alone
    assert note.value("opening_balance_net") == pytest.approx(
        note.value("reinsurance_contract_assets_opening"), abs=TOL
    )


# ── IS / BS wiring ───────────────────────────────────────────────────────────

def test_is_service_result_and_bs_balances_follow_the_notes():
    notes = build_notes(_entity())
    is_, bs = notes["IS"], notes["BS"]
    assert is_.value("insurance_service_result") == pytest.approx(
        is_.value("insurance_revenue")
        + is_.value("insurance_service_expenses")
        + is_.value("net_expenses_from_reinsurance_contracts"),
        abs=TOL,
    )
    assert bs.value("insurance_contract_liabilities") == pytest.approx(
        notes["Gross_Note"].value("closing_balance_net"), abs=TOL
    )
    assert bs.value("reinsurance_contract_assets") == pytest.approx(
        notes["RI_Note"].value("closing_balance_net"), abs=TOL
    )


# ── degenerate inputs (plan §6.4) ────────────────────────────────────────────

def test_view_without_an_ri_sheet_produces_a_zeroed_note_not_a_crash():
    view = _entity()
    view["sheets"].pop("RI")
    notes = build_notes(view)
    assert notes["RI_Note"].value("closing_balance_net") == 0.0
    assert notes["BS"].value("reinsurance_contract_assets") == 0.0
    # the Gross side is unaffected
    assert notes["Gross_Note"].value("closing_balance_net") != 0.0


def test_view_with_no_sheets_at_all_is_all_zeros():
    notes = build_notes({"sheets": {}})
    for table in notes.values():
        for line in table.lines:
            for value in line.values.values():
                assert value is None or value == 0.0


# ── runtime controls (plan §6.5) ─────────────────────────────────────────────

def test_controls_pass_on_a_well_formed_view():
    results = note_controls(_entity())
    assert results, "controls must actually run"
    failed = [r.id for r in results if not r.passed]
    assert failed == [], f"unexpected control breaches: {failed}"
    assert {"C1", "C2a", "C2b", "C2c", "C4a", "C4b", "C4c"} <= {r.id for r in results}


def test_controls_detect_a_drifted_note():
    """Corrupt the movement value a control compares against and the control must fail —
    otherwise the control is decorative."""
    view = _entity()
    sres = view["sheets"]["Gross"]
    sres.line_values["commission_on_written_premium"] = {"LRC_excl_LC": 10_000_000.0}
    tables = build_notes(view)
    # Rebuild the movement side from a *stale* view so the two genuinely disagree.
    stale = _entity()
    c1 = next(r for r in note_controls(stale, tables) if r.id == "C1")
    assert not c1.passed
    assert abs(c1.delta) > 1.0


def test_notes_report_shape_and_deviation_count():
    report = notes_report(build_sama_movement(_frames([_row()])))
    assert report["controls_checked"] > 0
    assert report["breaches"] == 0 and report["ties_out"] is True
    assert report["deviations_assumed"] == len(DEVIATIONS)
    assert set(report["closing_gap_vs_rollforward"]) == {"Gross", "RI"}


def test_closing_gap_is_reported_not_absorbed():
    """C3 is a reported reconciling item, never a pass/fail.

    After E8 both sides use the same balance-movement convention, so the gap is now only
    the movement lines the note genuinely omits (effect of exchange rates, other
    movements). With none of those present it collapses to zero — before E8 this same
    fixture showed a gap of twice the revenue.
    """
    view = _entity()
    gap = closing_gap(view)
    assert set(gap) == {"Gross", "RI"}
    assert gap["Gross"] == pytest.approx(0.0, abs=0.01)
    assert gap["RI"] == pytest.approx(0.0, abs=0.01)


def test_closing_gap_reports_lines_the_note_omits():
    """A movement line outside the note's structure must show up in C3 rather than vanish:
    'Effect of movements in exchange rates' has no note row."""
    view = _entity()
    view["sheets"]["Gross"].line_values["effect_of_movements_in_exchange_rates"] = {
        "LRC_excl_LC": 250.0
    }
    # rebuild the roll-forward the same way compute would, with the extra line included
    view["sheets"]["Gross"].closing_rollforward["LRC_excl_LC"] += 250.0
    assert closing_gap(view)["Gross"] == pytest.approx(-250.0, abs=0.01)
