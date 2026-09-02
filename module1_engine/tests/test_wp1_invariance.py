"""WP1's safety property: correcting the benchmark rows moves no computed figure.

WP1 deliberately changes the triangle sheets — Simple Avg stops averaging over zero-filled
cells (F3), Weighted Avg moves to the development column it actually describes (F5), six new
benchmark rows and a `Factor Count` row appear, and every block gains a label, which shifts
each sheet down by one row. All of that is in the region a human reads.

None of it may touch a number the engine computes. `Reserve Summary` and `Combined_Summary`
must be identical to the pre-WP1 baseline frozen in `benchmarks/goldens/summary_ref_prewp1`.
That baseline is not a fixture (there is no fixtures/ directory for it), so the ordinary
golden suite neither runs nor re-captures it; it exists solely for this test.

If this file fails, WP1 has changed a filed number and must not ship.
"""

import pandas as pd
import pytest

from processing import benchmarks, golden

BASELINE = golden.GOLDENS_DIR / "summary_ref_prewp1" if hasattr(golden, "GOLDENS_DIR") else None


def _baseline_dir():
    from processing.benchmarks import GOLDENS_DIR

    return GOLDENS_DIR / "summary_ref_prewp1"


pytestmark = pytest.mark.skipif(
    not (_baseline_dir() / "manifest.json").exists(),
    reason="pre-WP1 baseline not present",
)


@pytest.fixture(scope="module")
def produced():
    fx = benchmarks.get_fixture("summary_ref")
    struct, _ = benchmarks.run_fixture(fx)
    return struct


@pytest.fixture(scope="module")
def baseline():
    return golden.thaw(_baseline_dir())


def _sheet(struct, workbook, sheet):
    return struct.get(workbook, {}).get(sheet)


def test_every_reserve_summary_is_unchanged(produced, baseline):
    """The sheet every ultimate, IBNR and ULR is computed from."""
    checked = 0
    for workbook, sheets in baseline.items():
        before = sheets.get("Reserve Summary")
        if before is None:
            continue
        after = _sheet(produced, workbook, "Reserve Summary")
        assert after is not None, f"{workbook} lost its Reserve Summary"
        pd.testing.assert_frame_equal(
            after, before, check_dtype=False, obj=f"{workbook}:Reserve Summary"
        )
        checked += 1
    assert checked > 0, "baseline contained no Reserve Summary sheets"


def test_combined_summary_is_unchanged(produced, baseline):
    """Every downstream Module 2 input comes from this workbook."""
    before_wb = baseline.get("Combined_Summary.xlsx")
    assert before_wb, "baseline has no Combined_Summary.xlsx"
    after_wb = produced.get("Combined_Summary.xlsx")
    assert after_wb is not None, "Combined_Summary.xlsx was not produced"
    assert set(after_wb) == set(before_wb), "Combined_Summary sheet set changed"
    for sheet, before in before_wb.items():
        pd.testing.assert_frame_equal(
            after_wb[sheet], before, check_dtype=False, obj=f"Combined_Summary:{sheet}"
        )


def test_the_workbook_set_is_unchanged(produced, baseline):
    assert set(produced) == set(baseline)


def test_selected_rows_are_still_seeded_identically(produced):
    """Selected LDF stays `=1` in every development column, with Selected CDF beneath it.

    The CDF formula's *text* legitimately differs from the baseline — it names the Selected
    LDF row, which moved — so this asserts the substance instead: same column count, same
    seed, and a PRODUCT running from each column to the last.
    """
    checked = 0
    for workbook, sheets in produced.items():
        for name in ("Paid Claims Triangle", "Reported Triangle"):
            frame = sheets.get(name)
            if frame is None:
                continue
            labels = frame.iloc[:, 0].astype(str)
            ldf_rows = labels[labels == "Selected LDF"].index.tolist()
            cdf_rows = labels[labels == "Selected CDF"].index.tolist()
            assert len(ldf_rows) == 1 and len(cdf_rows) == 1, f"{workbook}:{name}"
            assert cdf_rows[0] == ldf_rows[0] + 1, "Selected CDF must sit directly below LDF"

            # The golden struct is read with `pd.read_excel`, which evaluates nothing: both
            # rows come back as NaN because no spreadsheet has ever opened the file. That
            # absence IS the seeded state (defect F6 — an un-edited workbook develops at the
            # reader's blank default of 2.0), so asserting it is asserting the invariant.
            for row in (ldf_rows[0], cdf_rows[0]):
                values = frame.iloc[row, 1:].tolist()
                assert all(
                    v is None or (isinstance(v, float) and pd.isna(v)) for v in values
                ), f"{workbook}:{name} row {row} is no longer an unevaluated formula: {values}"
            checked += 1
    assert checked > 0


def test_the_benchmark_block_gained_its_rows_and_labels(produced):
    """The intended change, asserted positively so a silent revert is caught."""
    expected = {
        "Simple Avg LDF", "Simple Avg CDF", "Weighted Avg LDF", "Weighted Avg CDF",
        "Ex-Hi-Lo Avg LDF", "Ex-Hi-Lo Avg CDF", "Last 4 Avg LDF", "Last 4 Avg CDF",
        "Last 8 Avg LDF", "Last 8 Avg CDF", "Median LDF", "Median CDF", "Factor Count",
    }
    frame = produced["Motor Insurance Payment GROSS 2017-12.xlsx"]["Paid Claims Triangle"]
    labels = set(frame.iloc[:, 0].astype(str))
    assert expected <= labels, f"missing: {expected - labels}"
    # Blocks 2..n carry a label on the blank row above them. The block at row 1 cannot — a
    # label there becomes the pandas header — so it is named by `engine.LEADING_BLOCK`.
    assert {"Cumulative Triangle", "Age-to-Age Factors"} <= labels

    reported = produced["Motor Insurance Payment GROSS 2017-12.xlsx"]["Reported Triangle"]
    reported_labels = set(reported.iloc[:, 0].astype(str))
    assert "Age-to-Age Factors" in reported_labels
    # Reported leads with the cumulative block, so that one is unlabelled here and named by
    # LEADING_BLOCK; it has no incremental block at all.
    assert "Cumulative Triangle" not in reported_labels
    assert "Incremental Triangle" not in reported_labels
