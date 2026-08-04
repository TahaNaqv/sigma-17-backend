"""Regression net for the reconstructed Gross aggregates and the closing roll-forward.

The client flattened several Gross rows to static values, destroying their formulas. We
reconstruct them in ``schema.RECONSTRUCTED_FORMULAS``. Because the flattening also
destroyed the evidence, the reconstruction was verified against the client's ``Total``
column — the only column in their workbook carrying real numbers — and those verified
relations are pinned here so a future edit cannot quietly break them.

The figures below are transcribed from `Module2_Final_Output.xlsx` sheet `Gross`,
column J. They are *structural* evidence only: that column comes from a different run
than the workbook's own IFRS Summary, so it pins the shape of each subtotal, never the
value of any engine output.
"""

import types

import pandas as pd
import pytest

from module2_engine.movement.compute import build_sama_movement
from module2_engine.movement.schema import (
    CLOSING_CASHFLOW_SIGN,
    RECONSTRUCTED_FORMULAS,
    ROLLFORWARD_NEGATED_BLOCK,
    SCHEMA,
    negated_rollforward_rows,
    referenced_rows,
    validate_schema,
)
from module2_engine.movement.workbook import _row_values, build_json_companion

# Gross!J<row> in the client's disclosure file.
CLIENT_GROSS_TOTAL: dict[int, float] = {
    6: 526143684.4591513,
    26: 508233604.91225433,
    27: 548942885.6812903, 28: -27120384.564964015, 29: -13588896.204071943, 30: 0.0,
    31: 501110496.06243366,
    32: 548201472.2379875,
    33: 264001322.31000012, 34: 54649695.143046945, 35: 86280041.62446433,
    36: 7103181.981351051, 37: 28322877.825000003,
    38: 107844353.35412507,
    39: 57920442.39399939, 40: 49658854.56684359, 41: 265056.3932820899,
    42: 3503396.9974000826,
    43: 14235825.876524292, 44: -10732428.87912421,
    45: -10732428.87912421, 46: 0.0,
    47: -50594373.17295393,
    48: -38431543.69343014,
    49: 193309932.86999995, 50: -49581687.125829205, 51: -182159789.43760085,
    52: -12162829.47952379, 53: 0.0, 54: 0.0, 55: 0.0,
    57: 7853583.018769694, 58: 7853583.018769694, 59: 0.0,
    61: 0.0, 62: 0.0, 63: 0.0,
    66: 511850453.79807115, 67: -482428479.810558, 68: -28322877.825000003,
    69: -57732378.75613608, 70: -49658854.56684359,
}

TOL = 0.5  # currency units; the client's column is rounded in places


def _evaluate(excel: str) -> float:
    """Evaluate a reconstructed formula against the client's Total column."""
    expr = excel.lstrip("=").replace("SUM", "")
    import re

    expr = re.sub(
        r"[A-Z]{1,3}(\d+):[A-Z]{1,3}(\d+)",
        lambda m: "(" + "+".join(
            f"CLIENT[{r}]" for r in range(int(m.group(1)), int(m.group(2)) + 1)
        ) + ")",
        expr,
    )
    expr = re.sub(r"[A-Z]{1,3}(\d+)", r"CLIENT[\1]", expr)
    return eval(expr, {"CLIENT": CLIENT_GROSS_TOTAL})  # noqa: S307 — fixed literal input


@pytest.mark.parametrize("row", sorted(r for r in RECONSTRUCTED_FORMULAS["Gross"] if r != 72))
def test_reconstructed_subtotal_matches_the_client_total_column(row):
    """Each reconstructed Gross subtotal reproduces the client's own figure."""
    expected = CLIENT_GROSS_TOTAL[row]
    actual = _evaluate(RECONSTRUCTED_FORMULAS["Gross"][row])
    assert abs(actual - expected) < TOL, (
        f"row {row}: {RECONSTRUCTED_FORMULAS['Gross'][row]} = {actual:,.2f}, "
        f"client Total column says {expected:,.2f}"
    )


def test_row_32_includes_the_acquisition_row():
    """Row 32 must absorb row 38: the client's row-31 formula never adds row 38, yet J31
    ties only when the acquisition amount sits inside row 32."""
    assert 38 in referenced_rows(RECONSTRUCTED_FORMULAS["Gross"][32])
    row31 = SCHEMA.sheets["Gross"].line("insurance_service_expenses")
    assert 38 not in referenced_rows(next(iter(row31.formulas.values()))["excel"])


def test_past_service_takes_ulae_and_not_the_methodology_diff():
    """Row 47 = change in ultimate + ULAE. Row 53 belongs to row 31 alone — counting it
    in both double-counts the routed reconciliation residual."""
    refs47 = referenced_rows(RECONSTRUCTED_FORMULAS["Gross"][47])
    assert refs47 == {48, 52}
    assert referenced_rows(RECONSTRUCTED_FORMULAS["Gross"][48]) == {49, 50, 51}
    row31 = SCHEMA.sheets["Gross"].line("insurance_service_expenses")
    assert 53 in referenced_rows(next(iter(row31.formulas.values()))["excel"])


def test_gross_closing_adds_cash_flows_ri_subtracts():
    """The mirror-image convention (plan §5.1): Gross tracks a liability, RI an asset."""
    assert CLOSING_CASHFLOW_SIGN == {"Gross": 1.0, "RI": -1.0}


def test_gross_closing_uses_the_balance_movement_not_the_pnl_total():
    """Row 64 is a P&L aggregate (it carries row 56 = revenue − expenses); a balance
    roll-forward must not consume it (plan §5.3)."""
    formula = RECONSTRUCTED_FORMULAS["Gross"][72]
    assert 64 not in referenced_rows(formula), f"row 72 must not read row 64: {formula}"
    assert referenced_rows(formula) == {6, 26, 31, 57, 60, 61, 71}
    assert "C31-C26" in formula, "revenue must be subtracted, expenses added"


def test_schema_validation_covers_the_reconstructed_formulas():
    assert validate_schema() == []


def _frames(**cols):
    row = {"RESERVINGCLASS": "TEST", "UWY": 2023, **cols}
    ifrs = pd.DataFrame([row])
    lc = pd.DataFrame([{"RESERVINGCLASS": "TEST", "UWY": 2023,
                        "LC Discounted_PY": 0.0, "LC Discounted_CY": 0.0,
                        "Loss Recovery Component": 0.0}])
    return types.SimpleNamespace(ifrs_summary_df=ifrs, allocate_sheets={"LC": lc})


def test_previously_zero_aggregates_now_carry_their_children():
    """E1-E3: rows 38/42/44/57 had no formula at all and rendered 0 regardless of input."""
    res = build_sama_movement(_frames(**{
        "Commission Expense": 7.0,                                  # row 39
        "GROSS - Insurance Finance (Income)/Expense": 5.0,          # row 58
    }))
    sres = res.pairs[0].sheets["Gross"]
    vals = _row_values(SCHEMA.sheets["Gross"], sres)

    assert vals[39]["LRC_excl_LC"] == 7.0
    assert vals[38]["LRC_excl_LC"] == 7.0, "acquisition CF subtotal must sum its children"
    assert vals[32]["LRC_excl_LC"] == 7.0, "row 32 absorbs the acquisition row"
    assert vals[31]["LRC_excl_LC"] == 7.0, "insurance service expenses must see it"

    assert vals[58]["LIC_excl_RA"] == 5.0
    assert vals[57]["LIC_excl_RA"] == 5.0, "finance subtotal must sum P&L + OCI"


def test_closing_rollforward_adds_gross_cash_flows():
    """E7: a Gross cash inflow raises the closing liability; the old convention lowered it."""
    res = build_sama_movement(_frames(**{"Premium Received": 40.0}))
    sres = res.pairs[0].sheets["Gross"]
    cash = sres.line_values["premium_received"]["LRC_excl_LC"]
    assert cash == 40.0
    assert sres.closing_rollforward["LRC_excl_LC"] == pytest.approx(
        sres.opening["LRC_excl_LC"] + 40.0
    )


def test_cash_and_revenue_agree_across_both_closing_paths():
    """The workbook's row-72 formula and compute's ``closing_rollforward`` are separate
    code paths. After E7 they agree on the cash-flow contribution — which is what E7
    fixed — and they already agreed on revenue."""
    for label, cols in (
        ("cash", {"Gross UPR_prev": 100.0, "Premium Received": 40.0}),
        ("revenue", {"Gross UPR_prev": 100.0, "GWP": 90.0}),
    ):
        sres = build_sama_movement(_frames(**cols)).pairs[0].sheets["Gross"]
        vals = _row_values(SCHEMA.sheets["Gross"], sres)
        for bucket in SCHEMA.sheets["Gross"].value_buckets:
            assert vals[72][bucket] == pytest.approx(
                sres.closing_rollforward[bucket]
            ), f"{label}/{bucket}"


def test_e8_rendered_closing_and_computed_rollforward_agree():
    """E8, resolved: the two closing paths must now agree exactly.

    Previously they differed by twice the expense block — ``closing_rollforward`` summed
    the P&L input lines (expenses positive, revenue positive) while the sheet reached its
    closing through row 64, which carries row 56 = revenue − expenses. Neither was a
    *balance* movement. Both now use one: revenue negated, expenses positive.
    """
    sres = build_sama_movement(_frames(**{
        "Gross UPR_prev": 100.0, "GWP": 90.0,
        "Commission Expense": 7.0, "Premium Received": 40.0,
        "GROSS - Insurance Finance (Income)/Expense": 5.0,
    })).pairs[0].sheets["Gross"]
    vals = _row_values(SCHEMA.sheets["Gross"], sres)

    assert vals[31]["LRC_excl_LC"] == 7.0  # expenses present
    assert vals[26]["LRC_excl_LC"] != 0.0  # revenue present — the term that used to differ
    for bucket in SCHEMA.sheets["Gross"].value_buckets:
        assert vals[72][bucket] == pytest.approx(sres.closing_rollforward[bucket]), bucket


def test_balance_movement_negates_revenue_not_expenses():
    """The direction that makes it a balance roll-forward: revenue releases the LRC."""
    sres = build_sama_movement(_frames(**{"Gross UPR_prev": 100.0, "GWP": 90.0})).pairs[0].sheets["Gross"]
    vals = _row_values(SCHEMA.sheets["Gross"], sres)
    revenue = vals[26]["LRC_excl_LC"]
    assert revenue > 0, "the sheet still presents revenue with its P&L sign"
    # ...but the balance closes *lower* by that revenue.
    assert sres.closing_rollforward["LRC_excl_LC"] == pytest.approx(
        sres.opening["LRC_excl_LC"] - revenue
    )


def test_negated_block_is_declared_for_both_sheets():
    """Gross negates insurance revenue; RI negates the ceded-premium allocation — which
    the client's own un-flattened RI formula (D47 = D27 − D21) already encodes."""
    assert set(ROLLFORWARD_NEGATED_BLOCK) == {"Gross", "RI"}
    assert negated_rollforward_rows("Gross") == {27, 28, 29, 30}  # the Insurance revenue block
    assert negated_rollforward_rows("RI") == {22, 23, 24, 25, 26}  # Amounts Allocated to RI


def test_json_companion_reports_the_repaired_aggregates():
    """The structured feed downstream consumers read must carry the same fix."""
    res = build_sama_movement(_frames(**{"Commission Expense": 7.0}))
    comp = build_json_companion(res, levels=("cohort",))
    lines = {ln["id"]: ln for ln in comp["views"][0]["sheets"]["Gross"]["lines"]}
    assert lines["insurance_service_expenses"]["total"] == 7.0
