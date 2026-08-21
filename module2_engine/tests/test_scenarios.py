"""WP4 — sensitivity shocks.

The propagation table in ``test_propagation_map_matches_specification`` IS the
acceptance specification for this feature (docs/SENSITIVITY_TESTING_PLAN.md 1.3).
It was derived by measurement against the client reference book; if it changes,
the model's behaviour has changed and that must be a deliberate, reviewed decision.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from module2_engine.engine import _apply_selected_ulr, _compute_allocate_frames
from module2_engine.scenarios import (
    TOTAL,
    ScenarioShock,
    build_comparison,
    build_tornado,
    canonical_class,
    extract_measures,
    run_sensitivity,
)

FIXTURES = Path(__file__).resolve().parents[2] / "benchmarks" / "fixtures"
ALLOCATE_REF = FIXTURES / "m2_allocate_ref" / "Combined_Summary.xlsx"

pytestmark = pytest.mark.skipif(
    not ALLOCATE_REF.is_file(), reason="reference fixture not available"
)


@pytest.fixture(scope="module")
def combined_bytes() -> bytes:
    return ALLOCATE_REF.read_bytes()


@pytest.fixture(scope="module")
def base_frames(combined_bytes):
    sheets, _ = _compute_allocate_frames(combined_bytes, None)
    return sheets


def _total(sheets, sheet: str, column: str) -> float:
    return float(pd.to_numeric(sheets[sheet][column], errors="coerce").sum())


# ---------------------------------------------------------------------------
# Unit semantics — the levers are NOT interchangeable
# ---------------------------------------------------------------------------


def test_ra_shock_is_relative_not_absolute():
    df = pd.DataFrame({"RESERVINGCLASS": ["A"], "GROSS/RI": ["GROSS"], "RA %": [0.0463]})
    out = ScenarioShock("RA +10%", "ra", 0.10).apply_ra(df)
    assert out["RA %"].iloc[0] == pytest.approx(0.050930, abs=1e-9)
    # An absolute reading would give 0.1463 — three times the loading.
    assert out["RA %"].iloc[0] != pytest.approx(0.1463, abs=1e-6)


def test_discount_shock_is_absolute_basis_points_on_cy_only():
    df = pd.DataFrame({"Time Period": [0, 1], "CY Discount": [0.0608, 0.0561],
                       "PY Discount": [0.0579, 0.0509]})
    out = ScenarioShock("Disc +5bp", "discount", 5).apply_discount(df)
    assert out["CY Discount"].tolist() == pytest.approx([0.0613, 0.0566], abs=1e-12)
    # PY is the prior period's locked-in basis and must never move.
    assert out["PY Discount"].tolist() == pytest.approx([0.0579, 0.0509], abs=1e-12)


def test_ulr_shock_is_absolute_percentage_points():
    assert ScenarioShock("ULR +5pp", "ulr", 0.05).ulr_delta() == pytest.approx(0.05)
    assert ScenarioShock("RA", "ra", 0.05).ulr_delta() is None


def test_unknown_lever_and_non_finite_magnitude_are_rejected():
    with pytest.raises(ValueError):
        ScenarioShock("bad", "inflation", 0.1)
    with pytest.raises(ValueError):
        ScenarioShock("bad", "ra", float("nan"))


# ---------------------------------------------------------------------------
# The ULR subtlety: shock the COLUMN, not the input list
# ---------------------------------------------------------------------------


def _uw_summary() -> pd.DataFrame:
    return pd.DataFrame([
        {"RESERVINGCLASS": "Motor", "UWY": "2024", "Ult LR": 0.60,
         "RA %": 0.10, "Comm Ratio": 0.20, "Exp Ratio": 0.10},
        {"RESERVINGCLASS": "Property", "UWY": "2023", "Ult LR": 0.50,
         "RA %": 0.05, "Comm Ratio": 0.15, "Exp Ratio": 0.05},
    ])


def test_ulr_shock_applies_when_no_selections_were_made():
    """Regression: shocking selected_ulr_rows would be a silent no-op here."""
    out = _apply_selected_ulr(_uw_summary(), None, ulr_shock=0.05)
    assert out["Selected ULR"].tolist() == pytest.approx([0.65, 0.55])


def test_ulr_shock_applies_on_top_of_explicit_selections():
    rows = [{"reserving_class": "Motor", "uwy": "2024", "selected_ulr": 0.70}]
    out = _apply_selected_ulr(_uw_summary(), rows, ulr_shock=0.05)
    assert out["Selected ULR"].tolist() == pytest.approx([0.75, 0.55])


def test_ulr_shock_is_clipped_at_zero():
    out = _apply_selected_ulr(_uw_summary(), None, ulr_shock=-5.0)
    assert out["Selected ULR"].tolist() == pytest.approx([0.0, 0.0])


def test_ulr_shock_respects_class_scope():
    out = _apply_selected_ulr(_uw_summary(), None, ulr_shock=0.05, ulr_scope=("motor",))
    assert out["Selected ULR"].tolist() == pytest.approx([0.65, 0.50])


def test_no_shock_leaves_selected_ulr_untouched():
    out = _apply_selected_ulr(_uw_summary(), None)
    assert out["Selected ULR"].tolist() == pytest.approx([0.60, 0.50])


# ---------------------------------------------------------------------------
# Propagation map — the acceptance specification
# ---------------------------------------------------------------------------

#: (sheet, column) -> (ra+10%, disc+5bp, ulr+5pp) expected RELATIVE deltas.
#: ``None`` means "structurally does not respond" and is asserted as exactly zero.
PROPAGATION = {
    ("MainSheet", "IBNR"): (None, None, None),
    ("MainSheet", "ULAE"): (None, None, None),
    ("MainSheet", "RA (OS)"): (0.100000000, None, None),
    ("MainSheet", "RA (IBNR)"): (0.100000000, None, None),
    ("MainSheet", "Future CF"): (None, None, None),
    ("MainSheet", "Discounting Impact"): (None, -0.007847322, None),
    ("MainSheet", "Change in Discounting Impact"): (None, -0.151076539, None),
    ("LC", "PAA_LRC"): (None, None, None),
    ("LC", "GMM LRC_Undiscounted"): (0.002799839, None, 0.067621560),
    ("LC", "GMM LRC_Discounted_CY"): (0.002795845, -0.000389109, 0.067648963),
    ("LC", "GMM LRC_Discounted_PY"): (0.002796151, None, 0.067646894),
    ("LC", "LC Discounted_CY"): (0.068253752, -0.006697818, 1.450768879),
    ("LC", "Loss Recovery Component"): (0.068253752, -0.006697818, 1.450768879),
}


def test_propagation_map_matches_specification(combined_bytes, base_frames):
    shocked = {
        "ra": _compute_allocate_frames(
            combined_bytes, None, shock=ScenarioShock("RA +10%", "ra", 0.10))[0],
        "discount": _compute_allocate_frames(
            combined_bytes, None, shock=ScenarioShock("Disc +5bp", "discount", 5))[0],
        "ulr": _compute_allocate_frames(
            combined_bytes, None, shock=ScenarioShock("ULR +5pp", "ulr", 0.05))[0],
    }
    failures = []
    for (sheet, column), expected in PROPAGATION.items():
        base = _total(base_frames, sheet, column)
        for lever, want in zip(("ra", "discount", "ulr"), expected):
            got = _total(shocked[lever], sheet, column)
            delta = got - base
            if want is None:
                if abs(delta) > 1e-6:
                    failures.append(f"{sheet}.{column} [{lever}] moved {delta} (expected none)")
            else:
                rel = delta / abs(base)
                if rel != pytest.approx(want, rel=1e-6):
                    failures.append(
                        f"{sheet}.{column} [{lever}] {rel:.6f} != {want:.6f}"
                    )
    assert not failures, "\n".join(failures)


def test_ra_shock_moves_ra_balances_by_exactly_the_relative_magnitude(
    combined_bytes, base_frames
):
    for mag in (-0.25, -0.10, 0.10, 0.25):
        sheets, _ = _compute_allocate_frames(
            combined_bytes, None, shock=ScenarioShock("x", "ra", mag))
        for col in ("RA (OS)", "RA (IBNR)"):
            base = _total(base_frames, "MainSheet", col)
            got = _total(sheets, "MainSheet", col)
            assert (got - base) / base == pytest.approx(mag, rel=1e-9)


def test_discount_shock_leaves_py_discounted_lrc_untouched(combined_bytes, base_frames):
    """The CY-only guarantee — the feature's core correctness property."""
    for mag in (-25, -5, 5, 25):
        sheets, _ = _compute_allocate_frames(
            combined_bytes, None, shock=ScenarioShock("x", "discount", mag))
        assert _total(sheets, "LC", "GMM LRC_Discounted_PY") == pytest.approx(
            _total(base_frames, "LC", "GMM LRC_Discounted_PY"), rel=1e-12
        )


def test_ra_shock_moves_both_balances_and_combined_ratio(combined_bytes, base_frames):
    """RA is double-counted by design: explicit balances AND the combined ratio."""
    sheets, _ = _compute_allocate_frames(
        combined_bytes, None, shock=ScenarioShock("x", "ra", 0.10))
    assert _total(sheets, "MainSheet", "RA (OS)") > _total(base_frames, "MainSheet", "RA (OS)")
    assert _total(sheets, "Loss Ratio", "Combined Ratio") > _total(
        base_frames, "Loss Ratio", "Combined Ratio")


def test_ult_lr_does_not_move_under_a_ulr_shock(combined_bytes, base_frames):
    """Ult LR is derived from IBNR; only the SELECTED ULR is a judgement input."""
    sheets, _ = _compute_allocate_frames(
        combined_bytes, None, shock=ScenarioShock("x", "ulr", 0.05))
    assert _total(sheets, "Loss Ratio", "Ult LR") == pytest.approx(
        _total(base_frames, "Loss Ratio", "Ult LR"), rel=1e-12)


# ---------------------------------------------------------------------------
# No-op and scoping guarantees
# ---------------------------------------------------------------------------


def test_shock_none_is_value_identical_to_no_shock(combined_bytes, base_frames):
    sheets, _ = _compute_allocate_frames(combined_bytes, None, shock=None)
    assert [k for k in base_frames if not base_frames[k].equals(sheets[k])] == []


def test_zero_magnitude_scenario_equals_base(combined_bytes, base_frames):
    for lever in ("ra", "discount", "ulr"):
        sheets, _ = _compute_allocate_frames(
            combined_bytes, None, shock=ScenarioShock("zero", lever, 0.0))
        assert [k for k in base_frames if not base_frames[k].equals(sheets[k])] == []


def test_class_scoped_shock_leaves_other_classes_untouched(combined_bytes, base_frames):
    classes = sorted(base_frames["MainSheet"]["RESERVINGCLASS"].astype(str).unique())
    target = classes[0]
    sheets, _ = _compute_allocate_frames(
        combined_bytes, None, shock=ScenarioShock("x", "ra", 0.10, (target,)))
    base_ms, new_ms = base_frames["MainSheet"], sheets["MainSheet"]
    for rc in classes:
        b = float(base_ms[base_ms.RESERVINGCLASS == rc]["RA (OS)"].sum())
        g = float(new_ms[new_ms.RESERVINGCLASS == rc]["RA (OS)"].sum())
        if rc == target:
            assert (g - b) / b == pytest.approx(0.10, rel=1e-9)
        else:
            assert g == pytest.approx(b, rel=1e-12)


def test_class_scope_matching_is_case_and_whitespace_insensitive(combined_bytes, base_frames):
    target = sorted(base_frames["MainSheet"]["RESERVINGCLASS"].astype(str).unique())[0]
    messy = f"  {target.lower()}  "
    sheets, _ = _compute_allocate_frames(
        combined_bytes, None, shock=ScenarioShock("x", "ra", 0.10, (messy,)))
    ms = sheets["MainSheet"]
    b = float(base_frames["MainSheet"].query("RESERVINGCLASS == @target")["RA (OS)"].sum())
    g = float(ms.query("RESERVINGCLASS == @target")["RA (OS)"].sum())
    assert (g - b) / b == pytest.approx(0.10, rel=1e-9)


def test_canonical_class_collapses_case_and_whitespace():
    assert canonical_class("  Motor   Insurance ") == canonical_class("motor insurance")
    assert canonical_class(None) == ""


# ---------------------------------------------------------------------------
# Measures, comparison, tornado
# ---------------------------------------------------------------------------


def test_ratio_measures_use_gep_weighted_mean_not_a_sum(combined_bytes):
    result = run_sensitivity(combined_bytes, [ScenarioShock("ULR +5pp", "ulr", 0.05)])
    base = result.base["selected_ulr"][TOTAL]
    shocked = result.per_scenario[0][1]["selected_ulr"][TOTAL]
    assert 0.0 < base < 3.0, "a weighted mean, not a sum across classes"
    assert shocked - base == pytest.approx(0.05, abs=1e-9)


def test_comparison_marks_structural_zeros_and_omits_percent_on_zero_base(combined_bytes):
    result = run_sensitivity(combined_bytes, [ScenarioShock("RA +10%", "ra", 0.10)])
    frame = result.comparison()
    ibnr = frame[frame.measure_key == "ibnr"].iloc[0]
    assert not bool(ibnr.responds) and ibnr.abs_delta == pytest.approx(0.0)
    ra = frame[frame.measure_key == "ra_os"].iloc[0]
    assert bool(ra.responds) and ra.pct_delta == pytest.approx(0.10, rel=1e-6)


def test_tornado_ranks_by_absolute_not_percent(combined_bytes):
    """Loss Component moves +145% on a tiny base; GMM LRC moves ~7% on a huge one.
    Ranking on percent would put the wrong measure at the top of the book's risk."""
    result = run_sensitivity(combined_bytes, [
        ScenarioShock("ULR +5pp", "ulr", 0.05),
        ScenarioShock("RA +10%", "ra", 0.10),
    ])
    t = result.tornado()
    order = list(t.measure_key)
    assert order.index("gmm_lrc_undisc") < order.index("lc_cy")
    assert t.max_abs_delta.is_monotonic_decreasing


def test_run_sensitivity_warns_on_a_scenario_that_moves_nothing(combined_bytes):
    result = run_sensitivity(combined_bytes, [ScenarioShock("noop", "ra", 0.0)])
    assert any("moved no measure" in w for w in result.warnings)


def test_run_sensitivity_warns_on_a_scope_class_absent_from_the_data(combined_bytes):
    result = run_sensitivity(
        combined_bytes, [ScenarioShock("x", "ra", 0.10, ("Nonexistent Class",))])
    assert any("not present" in w for w in result.warnings)


def test_process_scope_requires_its_extra_inputs(combined_bytes):
    with pytest.raises(ValueError, match="Process-scope"):
        run_sensitivity(combined_bytes, [ScenarioShock("x", "ra", 0.10)], scope="process")


def test_unknown_scope_is_rejected(combined_bytes):
    with pytest.raises(ValueError, match="Unknown scope"):
        run_sensitivity(combined_bytes, [ScenarioShock("x", "ra", 0.10)], scope="nope")


# ---------------------------------------------------------------------------
# Process scope — LIC / LRC, and the BOP invariance
# ---------------------------------------------------------------------------

PROCESS_REF = FIXTURES / "m2_process_ref"

process_ref = pytest.mark.skipif(
    not (PROCESS_REF / "Combined_Summary.xlsx").is_file(),
    reason="process reference fixture not available",
)


@pytest.fixture(scope="module")
def process_inputs():
    return (
        (PROCESS_REF / "Combined_Summary.xlsx").read_bytes(),
        (PROCESS_REF / "Previous_period.xlsx").read_bytes(),
        (PROCESS_REF / "Expense-CF.xlsx").read_bytes(),
    )


@process_ref
def test_process_scope_exposes_lic_and_lrc_measures(process_inputs):
    cs, pp, ex = process_inputs
    result = run_sensitivity(
        cs, [ScenarioShock("RA +10%", "ra", 0.10)],
        scope="process", previous_period_bytes=pp, expense_cf_bytes=ex,
        accounting_period=2024,
    )
    for key in ("lic_bop", "lic_eop", "lrc_bop", "lrc_eop"):
        assert key in result.base, f"{key} missing from process-scope measures"


@process_ref
def test_bop_measures_never_move_under_any_shock(process_inputs):
    """BOP is prior-period given data. If a shock moves it, the model is wrong."""
    cs, pp, ex = process_inputs
    result = run_sensitivity(
        cs,
        [ScenarioShock("RA +10%", "ra", 0.10),
         ScenarioShock("Disc +5bp", "discount", 5),
         ScenarioShock("ULR +5pp", "ulr", 0.05)],
        scope="process", previous_period_bytes=pp, expense_cf_bytes=ex,
        accounting_period=2024,
    )
    for _, values in result.per_scenario:
        for key in ("lic_bop", "lrc_bop"):
            assert values[key][TOTAL] == pytest.approx(
                result.base[key][TOTAL], rel=1e-12
            ), f"{key} moved under a shock"


@process_ref
def test_lic_eop_responds_to_ra_and_discount_but_not_to_ulr(process_inputs):
    """LIC is the claims liability: RA and discounting reach it, loss ratio does not
    (ULR drives the LRC/loss-component path)."""
    cs, pp, ex = process_inputs
    result = run_sensitivity(
        cs,
        [ScenarioShock("RA +10%", "ra", 0.10),
         ScenarioShock("Disc +5bp", "discount", 5),
         ScenarioShock("ULR +5pp", "ulr", 0.05)],
        scope="process", previous_period_bytes=pp, expense_cf_bytes=ex,
        accounting_period=2024,
    )
    base = result.base["lic_eop"][TOTAL]
    deltas = {s.lever: v["lic_eop"][TOTAL] - base for s, v in result.per_scenario}
    assert deltas["ra"] > 0
    assert deltas["discount"] < 0
    assert deltas["ulr"] == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Workbook
# ---------------------------------------------------------------------------


def test_workbook_renders_every_expected_sheet(combined_bytes):
    import io
    from module2_engine.workbook_sensitivity import render_sensitivity_workbook

    result = run_sensitivity(combined_bytes, [
        ScenarioShock("RA +10%", "ra", 0.10),
        ScenarioShock("Disc +5bp", "discount", 5),
    ])
    xlsx = render_sensitivity_workbook(result)
    names = pd.ExcelFile(io.BytesIO(xlsx)).sheet_names
    assert names == [
        "Scenario Definitions", "Levels", "Comparison — Absolute",
        "Comparison — Percent", "Tornado", "By Class",
    ]


def test_workbook_renders_structural_zeros_as_a_dash(combined_bytes):
    import io
    from module2_engine.workbook_sensitivity import render_sensitivity_workbook

    result = run_sensitivity(combined_bytes, [ScenarioShock("RA +10%", "ra", 0.10)])
    xlsx = render_sensitivity_workbook(result)
    frame = pd.read_excel(io.BytesIO(xlsx), "Comparison — Absolute", skiprows=4)
    ibnr = frame[frame["Measure"] == "IBNR"].iloc[0]
    # A measure the lever cannot reach must be visibly distinct from a tiny move.
    assert str(ibnr["RA +10%"]).strip() == "-"
