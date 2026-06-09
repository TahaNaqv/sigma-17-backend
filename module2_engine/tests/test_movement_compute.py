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
