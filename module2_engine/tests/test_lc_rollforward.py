"""LC roll-forward (client revision 2026-10-05): the IFRS Summary CK:DD block, its
movement-disclosure links, and the LC / RI columns of the BOP-EOP reconciliations.

Three layers:
  1. ``engine.build_lc_movement`` / ``engine._read_lc_bop`` — the new-vs-change split
     and its input contract (pure, synthetic frames).
  2. The movement disclosure consumes the block so the Loss Component and Loss
     Recovery Component columns roll forward with zero residual.
  3. The reconciliation tables tie to the movement sheets' own opening/closing
     balances — the control that the two presentations of one balance agree.
"""

import io
import types

import numpy as np
import pandas as pd
import pytest

from module2_engine import engine as E
from module2_engine.movement import compute as C
from module2_engine.movement import mapping as M

AP = 2024  # accounting period


def _lc(rows):
    """An allocate "LC" sheet: rows of (class, uwy, LC Discounted_CY, Loss Recovery)."""
    out = []
    for rc, uwy, lc_cy, lorc in rows:
        rec = {"RESERVINGCLASS": rc, "UWY": uwy}
        rec.update({m: 0.0 for m in E.LC_MEASURES})
        rec["LC Discounted_CY"] = lc_cy
        rec["Loss Recovery Component"] = lorc
        out.append(rec)
    return pd.DataFrame(out)


def _keys(pairs):
    return pd.DataFrame(pairs, columns=["RESERVINGCLASS", "UWY"])


# ── 1. build_lc_movement ────────────────────────────────────────────────────


def test_prior_cohort_books_change_current_cohort_books_new():
    keys = _keys([("A", 2023), ("A", 2024)])
    curr = _lc([("A", 2023, 20_000.0, 2_000.0), ("A", 2024, 119_000.0, 11_900.0)])
    prev = _lc([("A", 2023, 50_000.0, 5_000.0)])
    out, warnings = E.build_lc_movement(curr, prev, keys, AP)
    old, new = out.iloc[0], out.iloc[1]
    # The client's worked example (Gross N6:R8): BOP 50,000 -> EOP 20,000 is a change.
    assert old["Gross_LC_New"] == 0.0 and old["Gross_LC_Change"] == -30_000.0
    assert old["RI_LC_New"] == 0.0 and old["RI_LC_Change"] == -3_000.0
    # ...and the current cohort's 119,000 is all new.
    assert new["Gross_LC_New"] == 119_000.0 and new["Gross_LC_Change"] == 0.0
    assert new["RI_LC_New"] == 11_900.0 and new["RI_LC_Change"] == 0.0
    assert warnings == []


def test_cohort_after_accounting_period_is_new_business():
    """Deviation LC-1: UWY > accounting period is booked as new, so it still rolls."""
    keys = _keys([("A", 2025)])
    out, _ = E.build_lc_movement(_lc([("A", 2025, 700.0, 70.0)]), None, keys, AP)
    assert out.iloc[0]["Gross_LC_New"] == 700.0
    assert out.iloc[0]["RI_LC_New"] == 70.0


def test_opening_plus_new_plus_change_equals_closing_for_every_row():
    rng = np.random.default_rng(7)
    pairs = [(c, u) for c in ("A", "B", "C") for u in range(2018, 2026)]
    keys = _keys(pairs)
    curr = _lc([(c, u, *rng.uniform(0, 1e6, 2)) for c, u in pairs])
    # current-cohort and later rows have no opening balance
    prev = _lc([(c, u, *rng.uniform(0, 1e6, 2)) for c, u in pairs if u < AP])
    out, _ = E.build_lc_movement(curr, prev, keys, AP)
    for measure, new, change in E.LC_MOVEMENT_SPLITS:
        lhs = out[f"{measure}_prev"] + out[new] + out[change]
        np.testing.assert_allclose(lhs, out[f"{measure}_curr"], rtol=0, atol=1e-6)


def test_output_follows_key_order_and_carries_every_column():
    keys = _keys([("B", 2020), ("A", 2019), ("A", 2018)])
    curr = _lc([("A", 2018, 1.0, 0.0), ("A", 2019, 2.0, 0.0), ("B", 2020, 3.0, 0.0)])
    out, _ = E.build_lc_movement(curr, None, keys, AP)
    assert list(out.columns) == ["RESERVINGCLASS", "UWY", *E.lc_movement_columns()]
    assert list(out["LC Discounted_CY_curr"]) == [3.0, 2.0, 1.0]


def test_missing_lc_bop_means_zero_opening_and_a_warning():
    keys = _keys([("A", 2023)])
    out, warnings = E.build_lc_movement(_lc([("A", 2023, 10.0, 1.0)]), None, keys, AP)
    assert out.iloc[0]["LC Discounted_CY_prev"] == 0.0
    assert out.iloc[0]["Gross_LC_Change"] == 10.0  # whole balance reads as movement
    assert len(warnings) == 1 and "LC_BOP" in warnings[0]


def test_duplicate_class_uwy_in_lc_bop_is_rejected():
    prev = _lc([("A", 2023, 1.0, 0.0), ("A", 2023, 2.0, 0.0)])
    with pytest.raises(ValueError, match="more than one row"):
        E.build_lc_movement(_lc([("A", 2023, 1.0, 0.0)]), prev, _keys([("A", 2023)]), AP)


def test_lc_bop_rows_outside_current_period_are_reported():
    prev = _lc([("A", 2023, 1.0, 0.0), ("GONE", 2019, 5.0, 0.0)])
    out, warnings = E.build_lc_movement(
        _lc([("A", 2023, 1.0, 0.0)]), prev, _keys([("A", 2023)]), AP
    )
    assert len(out) == 1
    assert any("GONE/2019" in w for w in warnings)


def _book(**sheets) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for name, frame in sheets.items():
            frame.to_excel(w, index=False, sheet_name=name)
    return buf.getvalue()


def test_read_lc_bop_is_optional():
    assert E._read_lc_bop(_book(LIC_BOP=pd.DataFrame({"x": [1]}))) is None


def test_read_lc_bop_requires_every_lc_column():
    partial = _lc([("A", 2023, 1.0, 0.0)]).drop(columns=["PAA_LRC"])
    with pytest.raises(ValueError, match="PAA_LRC"):
        E._read_lc_bop(_book(LC_BOP=partial))


def test_read_lc_bop_accepts_a_prior_lc_sheet_verbatim():
    lc = _lc([("A", 2023, 1.0, 0.5)])
    got = E._read_lc_bop(_book(LC_BOP=lc))
    assert list(got.columns) == list(lc.columns)


# ── 2. movement disclosure consumes the block ───────────────────────────────


def _movement_frames(pairs, curr, prev, extra=None):
    """An IFRS Summary carrying only the LC block (+ ``extra`` columns)."""
    ifrs, warnings = E.build_lc_movement(curr, prev, _keys(pairs), AP)
    for col, val in (extra or {}).items():
        ifrs[col] = val
    return types.SimpleNamespace(
        ifrs_summary_df=ifrs, allocate_sheets={"LC": curr}, warnings=tuple(warnings)
    )


def test_loss_component_and_recovery_columns_roll_forward_with_zero_residual():
    pairs = [("A", 2022), ("A", 2023), ("A", 2024), ("A", 2025)]
    curr = _lc([("A", 2022, 0.0, 0.0), ("A", 2023, 20_000.0, 2_000.0),
                ("A", 2024, 119_000.0, 11_900.0), ("A", 2025, 900.0, 90.0)])
    prev = _lc([("A", 2022, 4_000.0, 400.0), ("A", 2023, 50_000.0, 5_000.0)])
    result = C.build_sama_movement(_movement_frames(pairs, curr, prev))
    for pair in result.pairs:
        g, ri = pair.sheets["Gross"], pair.sheets["RI"]
        assert g.residual["Loss_Component"] == pytest.approx(0.0, abs=1e-9), pair.uwy
        assert ri.residual["Loss_Recovery_Component"] == pytest.approx(0.0, abs=1e-9), pair.uwy


def test_movement_lines_carry_the_client_links():
    pairs = [("A", 2023), ("A", 2024)]
    curr = _lc([("A", 2023, 20_000.0, 2_000.0), ("A", 2024, 119_000.0, 11_900.0)])
    prev = _lc([("A", 2023, 50_000.0, 5_000.0)])
    result = C.build_sama_movement(_movement_frames(pairs, curr, prev))
    old = {p.uwy: p for p in result.pairs}[2023]
    new = {p.uwy: p for p in result.pairs}[2024]
    g_old = old.sheets["Gross"].line_values
    assert g_old["other_methodology_diff"]["Loss_Component"] == 50_000.0  # row 25 opening
    # row 45 carries "-" but the change is stored signed: a release stays negative.
    assert g_old["reversal_amortization_of_losses_following_an_assumed_patttern"][
        "Loss_Component"] == -30_000.0
    assert new.sheets["Gross"].line_values["lossess_on_new_onerous_contracts"][
        "Loss_Component"] == 119_000.0
    ri_old = old.sheets["RI"].line_values
    assert ri_old["other_methodology_diff"]["Loss_Recovery_Component"] == 5_000.0
    assert ri_old["reversal_amortization_of_lrc_following_an_assumed_patttern"][
        "Loss_Recovery_Component"] == -3_000.0
    assert new.sheets["RI"].line_values[
        "loss_recovery_component_for_new_underlying_onerous_contracts"][
        "Loss_Recovery_Component"] == 11_900.0


def test_ri_premium_payable_opening_reads_the_payable_balance():
    """RI D6 correction: opening premium payable is RI_Payable_prev, not the period's
    RI Premium Paid cash flow (which stays on the cash-flow row 57)."""
    pairs = [("A", 2023)]
    curr = _lc([("A", 2023, 0.0, 0.0)])
    frames = _movement_frames(pairs, curr, None, extra={
        "RI_Payable_prev": 40.0, "RI_Payable_curr": 60.0, "RI Premium Paid": 999.0})
    ri = C.build_sama_movement(frames).pairs[0].sheets["RI"]
    assert ri.line_values["premium_payable"]["Assets_Remaining_Coverage"] == -40.0


def test_ri_opening_risk_adjustment_uses_opening_ra_os():
    """RI J20 correction: RA (OS)_prev, not RA (OS)_curr."""
    pairs = [("A", 2023)]
    frames = _movement_frames(pairs, _lc([("A", 2023, 0.0, 0.0)]), None, extra={
        "RI - RA (OS)_prev": 3.0, "RI - RA (OS)_curr": 300.0,
        "RI - RA (IBNR)_prev": 4.0, "RI - RA (IBNR)_curr": 400.0})
    ri = C.build_sama_movement(frames).pairs[0].sheets["RI"]
    assert ri.line_values["other_methodology_diff"]["Risk_Adjustment"] == 7.0
    assert ri.opening["Risk_Adjustment"] == 7.0


def test_engine_warnings_reach_the_movement_result():
    frames = _movement_frames([("A", 2023)], _lc([("A", 2023, 1.0, 0.0)]), None)
    result = C.build_sama_movement(frames)
    assert any("LC_BOP" in w for w in result.warnings)


def test_retired_override_value_is_ignored_with_a_warning():
    frames = _movement_frames([("A", 2023)], _lc([("A", 2023, 0.0, 0.0)]), None)
    ovr = pd.DataFrame([{"RESERVINGCLASS": "A", "UWY": 2023,
                         "ri_loss_recovery_new_onerous": 500.0}])
    result = C.build_sama_movement(frames, overrides=ovr)
    ri = result.pairs[0].sheets["RI"]
    assert ri.line_values["loss_recovery_component_for_new_underlying_onerous_contracts"][
        "Loss_Recovery_Component"] == 0.0
    assert any("ri_loss_recovery_new_onerous" in w for w in result.warnings)


def test_retired_override_keys_are_no_longer_mapped():
    live = set(M.override_keys("RI"))
    assert live.isdisjoint(M.RETIRED_OVERRIDE_KEYS)
    assert M.validate_mapping() == []


# ── 3. reconciliation tables tie to the movement sheets ─────────────────────


def _full_frame():
    """Two classes × two cohorts with every LRC/LIC build-up column populated."""
    rng = np.random.default_rng(11)
    pairs = [("A", 2023), ("A", 2024), ("B", 2023), ("B", 2024)]
    curr = _lc([(c, u, *rng.uniform(1, 1e5, 2)) for c, u in pairs])
    prev = _lc([(c, u, *rng.uniform(1, 1e5, 2)) for c, u in pairs if u < AP])
    cols = {}
    for s in ("prev", "curr"):
        for c in (*E.LRC_COMPONENTS[s], *E.lic_columns(s), *E.ri_lrc_components(s),
                  *E.ri_lic_components(s)):
            cols[c] = rng.uniform(1, 1e5, len(pairs))
    frames = _movement_frames(pairs, curr, prev)
    ifrs = frames.ifrs_summary_df
    for c, v in cols.items():
        ifrs[c] = v
    # RI ULAE has no line on the RI movement sheet; it is zero in the client's data.
    ifrs["RI - ULAE_prev"] = ifrs["RI - ULAE_curr"] = 0.0
    return frames


@pytest.mark.parametrize("suffix,attr", [("prev", "opening"), ("curr", "closing_independent")])
def test_reconciliation_ties_to_movement_balances(suffix, attr):
    frames = _full_frame()
    ifrs = frames.ifrs_summary_df
    lrc = E.create_lrc_reconciliation(ifrs, suffix).set_index("RESERVINGCLASS")
    lic = E.create_lic_reconciliation(ifrs, suffix).set_index("RESERVINGCLASS")
    result = C.build_sama_movement(frames)
    for rc in ("A", "B"):
        sheets = [p.sheets for p in result.pairs if p.reserving_class == rc]

        def total(sheet, bucket):
            return sum(getattr(s[sheet], attr)[bucket] for s in sheets)

        assert lrc.loc[rc, "Loss Component"] == pytest.approx(total("Gross", "Loss_Component"))
        assert lrc.loc[rc, "RI LRC"] == pytest.approx(total("RI", "Assets_Remaining_Coverage"))
        assert lrc.loc[rc, "Loss Recovery Component"] == pytest.approx(
            total("RI", "Loss_Recovery_Component"))
        assert lic.loc[rc, "RI LIC"] == pytest.approx(
            total("RI", "Amounts_Recoverable_IC") + total("RI", "Risk_Adjustment"))


def test_reconciliation_keeps_the_gross_columns_first_and_unchanged():
    ifrs = _full_frame().ifrs_summary_df
    base = E.create_lrc_table(ifrs, E.LRC_COMPONENTS["prev"])
    full = E.create_lrc_reconciliation(ifrs, "prev")
    pd.testing.assert_frame_equal(full[list(base.columns)], base)
    base_lic = E.create_lic_table(ifrs, "curr")
    full_lic = E.create_lic_reconciliation(ifrs, "curr")
    pd.testing.assert_frame_equal(full_lic[list(base_lic.columns)], base_lic)
    assert list(full_lic.columns)[len(base_lic.columns):][-1] == "RI LIC"
