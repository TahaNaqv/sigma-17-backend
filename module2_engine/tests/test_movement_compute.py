"""Tests for the movement projection (compute.py).

Two layers: (1) the safe expression resolver, whose longest-match tokenizer must
handle column names containing spaces/dashes; (2) the roll-forward identities on a
synthetic IFRS-Summary frame. These assert the *machinery*, not actuarial values
(that is sign-off, plan §1)."""

import types

import pandas as pd

from module2_engine.movement import compute as C


# ── resolver ────────────────────────────────────────────────────────────────

def _resolve(expr, row, p="prev", big_p="PY"):
    missing: set[str] = set()
    return C._resolve(expr, row, set(row), p=p, big_p=big_p, missing=missing), missing


def test_resolver_single_column_with_spaces():
    row = {"Gross UPR_prev": 100.0, "Gross UPR_curr": 120.0}
    val, missing = _resolve("Gross UPR_{p}", row, p="prev")
    assert val == 100.0 and missing == set()


def test_resolver_longest_match_disambiguates_dashes():
    # "GROSS - Outstanding_prev" is ONE column, not GROSS minus Outstanding_prev.
    row = {"GROSS - Outstanding_prev": 200.0, "GROSS - S&S_prev": 5.0}
    val, missing = _resolve("GROSS - Outstanding_{p} + GROSS - S&S_{p}", row, p="prev")
    assert val == 205.0 and missing == set()


def test_resolver_negated_difference():
    row = {"Gross UPR_curr": 120.0, "Gross UPR_prev": 100.0}
    val, _ = _resolve("-(Gross UPR_curr - Gross UPR_prev)", row)
    assert val == -20.0


def test_resolver_max_min_functions():
    row = {"LC Discounted_CY": 30.0, "LC Discounted_PY": 50.0}
    hi, _ = _resolve("max(LC Discounted_CY - LC Discounted_PY, 0)", row, big_p="PY")
    lo, _ = _resolve("min(LC Discounted_CY - LC Discounted_PY, 0)", row)
    assert hi == 0.0 and lo == -20.0


def test_resolver_missing_column_is_zero_and_recorded():
    val, missing = _resolve("Nonexistent Col_{p}", {"x": 1.0}, p="prev")
    assert val == 0.0 and "Nonexistent Col_prev" in missing


# ── roll-forward identities ─────────────────────────────────────────────────

def _frames(rows):
    ifrs = pd.DataFrame(rows)
    lc = pd.DataFrame(
        [
            {"RESERVINGCLASS": r["RESERVINGCLASS"], "UWY": r["UWY"],
             "LC Discounted_PY": 0.0, "LC Discounted_CY": 0.0, "Loss Recovery Component": 0.0}
            for r in rows
        ]
    )
    return types.SimpleNamespace(ifrs_summary_df=ifrs, allocate_sheets={"LC": lc})


def _base_row(rc="TEST", uwy=2023):
    # minimal columns the Gross opening build-up + a couple of movements read
    return {
        "RESERVINGCLASS": rc, "UWY": uwy,
        "Gross UPR_prev": 100.0, "Gross UPR_curr": 120.0,
        "DAC_prev": 10.0, "DAC_curr": 12.0,
        "Comm_Payable_prev": 5.0, "Comm_Payable_curr": 6.0,
        "GROSS - Outstanding_prev": 200.0, "GROSS - Outstanding_curr": 210.0,
        "GROSS - ULAE_prev": 7.0, "GROSS - ULAE_curr": 8.0,
        "GROSS - Discounting Impact_prev": -3.0, "GROSS - Discounting Impact_curr": -4.0,
        "GROSS - SS_prev": 1.0, "GROSS - SS_curr": 1.0,
        "GROSS - S&S_prev": 2.0, "GROSS - S&S_curr": 2.0,
        "GROSS - Payment_prev": 9.0, "GROSS - Payment_curr": 9.0,
        "Claim_Pay_prev": 4.0, "Claim_Pay_curr": 4.0,
        "GROSS - RA (OS)_prev": 6.0, "GROSS - RA (OS)_curr": 6.5,
        "GROSS - RA (IBNR)_prev": 3.0, "GROSS - RA (IBNR)_curr": 3.5,
        "GWP": 50.0, "Commission Expense": 8.0,
        "Premium Received": 40.0, "Claims Paid": -30.0, "Other Cash Flows": -1.0,
    }


def test_one_pair_per_present_class_uwy():
    res = C.build_sama_movement(_frames([_base_row("A", 2022), _base_row("B", 2023)]))
    assert len(res.pairs) == 2
    assert {(p.reserving_class, p.uwy) for p in res.pairs} == {("A", 2022), ("B", 2023)}


def test_rollforward_and_residual_identities_hold():
    res = C.build_sama_movement(_frames([_base_row()]))
    g = res.pairs[0].sheets["Gross"]
    for b in g.opening:
        # closing_rollforward == opening + ΔPnL − cashflows, by construction
        # residual == closing_independent − closing_rollforward
        assert g.residual[b] == g.closing_independent[b] - g.closing_rollforward[b]
    # opening LIC bucket is a non-trivial sum of the build-up lines
    assert g.opening["LIC_excl_RA"] != 0.0


def test_both_sheets_emitted_per_pair():
    res = C.build_sama_movement(_frames([_base_row()]))
    assert set(res.pairs[0].sheets) == {"Gross", "RI"}


def test_cy_py_paid_read_from_combined_summary_columns():
    """The client mapping sources the incurred/past-service paid lines from the
    Combined-Summary 'Gross CY Paid'/'Gross PY Paid' columns (present in IFRS Summary)
    directly — no derived CY/PY side-frame needed."""
    row = _base_row()
    row["Gross CY Paid"] = 100.0
    row["Gross PY Paid"] = 50.0
    g = C.build_sama_movement(_frames([row])).pairs[0].sheets["Gross"]
    # both lines carry sign '+' -> positive magnitude, in the LIC excl RA bucket
    assert g.line_values["incurred_in_cy_paid_in_cy"]["LIC_excl_RA"] == 100.0
    assert g.line_values["paid_in_cy"]["LIC_excl_RA"] == 50.0


def test_dual_bucket_line_populates_ra_bucket():
    """A line with sources in two buckets (Incurred CY OS -> LIC excl RA + Risk
    Adjustment) now fills BOTH — the old single-bucket router dropped the RA half."""
    row = _base_row()
    row["GROSS - CY O/S"] = 80.0
    row["GROSS - CY RA (OS)"] = 7.0
    g = C.build_sama_movement(_frames([row])).pairs[0].sheets["Gross"]
    lv = g.line_values["incurred_in_cy_os_at_end_cy"]
    assert lv["LIC_excl_RA"] == 80.0
    assert lv["Risk_Adjustment"] == 7.0


def test_sign_applied_from_mapping():
    """DAC carries sign '-': its opening LRC contribution is the negated magnitude."""
    row = _base_row()  # DAC_prev = 10.0
    g = C.build_sama_movement(_frames([row])).pairs[0].sheets["Gross"]
    assert g.line_values["dac"]["LRC_excl_LC"] == -10.0


def test_reconciliation_report_shape_and_counts():
    res = C.build_sama_movement(_frames([_base_row("A", 2022), _base_row("B", 2023)]))
    rep = C.reconciliation_report(res)
    assert rep["pairs"] == 2
    assert rep["cells_checked"] == 2 * 2 * 4  # pairs × sheets × value buckets
    assert isinstance(rep["ties_out"], bool)
    assert rep["breaches"] == len(rep["top_breaches"]) or rep["breaches"] > len(rep["top_breaches"])
    assert set(rep["tolerance"]) == {"abs", "rel"}


def test_reconciliation_flags_a_break():
    """An opening balance with no offsetting movement/closing must breach the tie-out."""
    row = _base_row()
    res = C.build_sama_movement(_frames([row]))
    # force a break: closing_independent moved far from the roll-forward closing
    res.pairs[0].sheets["Gross"].residual["LIC_excl_RA"] = 10_000_000.0
    rep = C.reconciliation_report(res)
    assert rep["ties_out"] is False and rep["breaches"] >= 1


def test_override_frame_fills_ri_manual_line():
    """A tier-O line resolves from the MovementOverride frame (via __ovr__<key>),
    not the default 0."""
    ovr = pd.DataFrame([{"RESERVINGCLASS": "TEST", "UWY": 2023,
                         "ri_loss_recovery_new_onerous": 500.0}])
    ri = C.build_sama_movement(_frames([_base_row()]), overrides=ovr).pairs[0].sheets["RI"]
    lid = "loss_recovery_component_for_new_underlying_onerous_contracts"
    assert ri.line_values[lid]["Loss_Recovery_Component"] == 500.0
    # without the override the same line is 0
    ri0 = C.build_sama_movement(_frames([_base_row()])).pairs[0].sheets["RI"]
    assert ri0.line_values[lid]["Loss_Recovery_Component"] == 0.0
