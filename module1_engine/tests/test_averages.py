"""The average-basis primitives (WP1 / requirement 7).

The reference fixture is Motor Insurance Paid GROSS, whose dev-0 factors are wildly dispersed
(1.85, 8.15, 17.15) — the exact case the client is asking about when they ask to remove the
high and the low.
"""

import numpy as np
import pytest

from module1_engine.averages import (
    BASIS_ALL,
    BASIS_EX_HI_LO,
    BASIS_LAST_4,
    BASIS_LAST_8,
    BASIS_MEDIAN,
    BASIS_VOLUME_WEIGHTED,
    age_to_age_matrix,
    benchmark_rows,
    cdf_from_ldf_row,
    column_counts,
    ldf_for_basis,
    mask_for_basis,
    reduce_masked,
    volume_weighted,
)

#: Motor Insurance Payment GROSS, cumulative paid, first four development columns.
MOTOR = np.array([
    [440404.0, 3589719.0, 3639994.0, 3823919.0],
    [1199143.0, 2221195.0, 2550170.0, np.nan],
    [166090.0, 2847926.0, np.nan, np.nan],
    [507141.0, np.nan, np.nan, np.nan],
])


# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------


def test_column_j_holds_the_j_to_j_plus_one_factor():
    a2a = age_to_age_matrix(MOTOR)
    assert a2a[0, 0] == pytest.approx(3589719.0 / 440404.0)
    assert a2a[0, 1] == pytest.approx(3639994.0 / 3589719.0)


def test_the_final_column_is_always_blank():
    """There is no factor beyond the last observed development."""
    a2a = age_to_age_matrix(MOTOR)
    assert np.isnan(a2a[:, -1]).all()


def test_undefined_cells_are_nan_not_zero():
    """Defect F3. A zero here is what collapsed Simple Avg and its CDF."""
    a2a = age_to_age_matrix(MOTOR)
    assert np.isnan(a2a[3, 0])           # no dev-1 cell to divide into
    assert not (a2a[np.isfinite(a2a)] == 0).any()


def test_a_zero_denominator_is_undefined_but_a_zero_numerator_is_data():
    """Cumulative paid can fall — the Motor recovery substitution puts AMOUNTRECOVERED into
    `Amount` for recovery heads — so a genuine 0.0 factor is real, not a gap."""
    cum = np.array([[0.0, 500.0, 500.0], [500.0, 0.0, np.nan], [100.0, 200.0, np.nan]])
    a2a = age_to_age_matrix(cum)
    assert np.isnan(a2a[0, 0])           # 500 / 0 -> undefined
    assert a2a[1, 0] == 0.0              # 0 / 500 -> a real zero factor
    assert a2a[2, 0] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# Bases
# ---------------------------------------------------------------------------


def test_all_is_the_plain_column_mean():
    ldf = ldf_for_basis(MOTOR, BASIS_ALL)
    assert ldf[0] == pytest.approx(np.mean([8.150968, 1.852319, 17.146884]), rel=1e-5)


def test_ex_hi_lo_drops_one_high_and_one_low():
    ldf = ldf_for_basis(MOTOR, BASIS_EX_HI_LO)
    # of 8.15 / 1.85 / 17.15, only 8.15 survives
    assert ldf[0] == pytest.approx(8.150968, rel=1e-5)


def test_ex_hi_lo_falls_back_to_all_below_three_valid_cells():
    """Dropping the extremes of two cells would leave nothing at all."""
    a2a = age_to_age_matrix(MOTOR)
    assert column_counts(a2a)[1] == 2
    assert ldf_for_basis(MOTOR, BASIS_EX_HI_LO)[1] == pytest.approx(
        ldf_for_basis(MOTOR, BASIS_ALL)[1]
    )


def test_ex_hi_lo_equals_median_up_to_four_cells_and_diverges_only_at_five():
    """Pinned deliberately, and it is stronger than the plan assumed.

    Drop the highest and lowest of n sorted values and take the mean of the rest:

        n = 3  ->  v2                    == median
        n = 4  ->  (v2 + v3) / 2         == median (an even median IS that mean)
        n = 5  ->  (v2 + v3 + v4) / 3    != v3

    So the two bases are indistinguishable until a development column has **five** valid
    factors. On the reference book no column exceeds three, and even a fourth would not
    separate them. Offering both as named choices is only honest beside the factor count.
    """
    def _column(values):
        rows = len(values) + 1
        cum = np.full((rows, 3), np.nan)
        cum[: len(values), 0] = 100.0
        cum[: len(values), 1] = [100.0 * v for v in values]
        return cum

    for values in ([1.0, 2.0, 5.0], [1.0, 2.0, 4.0, 10.0]):
        cum = _column(values)
        assert column_counts(age_to_age_matrix(cum))[0] == len(values)
        assert ldf_for_basis(cum, BASIS_EX_HI_LO)[0] == pytest.approx(
            ldf_for_basis(cum, BASIS_MEDIAN)[0]
        ), f"must coincide at n={len(values)}"

    five = _column([1.0, 2.0, 4.0, 8.0, 100.0])
    assert column_counts(age_to_age_matrix(five))[0] == 5
    assert ldf_for_basis(five, BASIS_EX_HI_LO)[0] == pytest.approx((2 + 4 + 8) / 3)
    assert ldf_for_basis(five, BASIS_MEDIAN)[0] == pytest.approx(4.0)
    assert ldf_for_basis(five, BASIS_EX_HI_LO)[0] != pytest.approx(
        ldf_for_basis(five, BASIS_MEDIAN)[0]
    )


def test_ex_hi_lo_drops_one_occurrence_of_a_tie_not_every_tied_cell():
    cum = np.array([
        [100.0, 200.0, np.nan],
        [100.0, 200.0, np.nan],
        [100.0, 200.0, np.nan],
        [100.0, 900.0, np.nan],
    ])
    a2a = age_to_age_matrix(cum)
    mask = mask_for_basis(a2a, BASIS_EX_HI_LO)
    assert mask[:, 0].sum() == 2, "a tie must not empty the column"


def test_last_n_keeps_the_most_recent_VALID_rows():
    """Taking the last four rows of a column whose last three are undefined would average one
    factor and report it as four."""
    cum = np.array([
        [100.0, 200.0, np.nan],
        [100.0, 300.0, np.nan],
        [100.0, 400.0, np.nan],
        [100.0, 500.0, np.nan],
        [100.0, 600.0, np.nan],
        [0.0, 0.0, np.nan],       # undefined: zero denominator
    ])
    a2a = age_to_age_matrix(cum)
    mask = mask_for_basis(a2a, BASIS_LAST_4)
    kept = np.flatnonzero(np.isfinite(a2a[:, 0]) & ~mask[:, 0])
    assert kept.tolist() == [1, 2, 3, 4]


def test_last_4_and_last_8_are_inert_when_a_column_has_fewer_factors():
    """The measured reality of the reference book: no column exceeds three valid factors, so
    both 'last N' bases return the simple average exactly."""
    for basis in (BASIS_LAST_4, BASIS_LAST_8):
        assert ldf_for_basis(MOTOR, basis) == ldf_for_basis(MOTOR, BASIS_ALL)


def test_a_column_with_no_survivor_yields_none():
    """Never 0 (collapses the CDF) and never 1.0 (silently means 'no development')."""
    a2a = age_to_age_matrix(MOTOR)
    assert reduce_masked(a2a, np.ones(a2a.shape, dtype=bool)) == [None] * a2a.shape[1]
    assert ldf_for_basis(MOTOR, BASIS_ALL)[-1] is None


def test_volume_weighted_honours_the_mask_on_both_sides_of_the_ratio():
    a2a = age_to_age_matrix(MOTOR)
    mask = np.zeros(a2a.shape, dtype=bool)
    mask[2, 0] = True                     # drop the 17.15 outlier row
    got = volume_weighted(MOTOR, mask)[0]
    expected = (3589719.0 + 2221195.0) / (440404.0 + 1199143.0)
    assert got == pytest.approx(expected)


def test_volume_weighted_matches_the_wp6_reference_implementation():
    """`triangles.volume_weighted_ldf` is the same quantity; the engine used to carry a second,
    misaligned copy (defect F5). They must agree column for column."""
    import pandas as pd

    from module1_engine.triangles import volume_weighted_ldf

    reference = volume_weighted_ldf(pd.DataFrame(MOTOR))
    mine = volume_weighted(MOTOR)
    for j, ref in enumerate(reference):
        if np.isfinite(ref):
            assert mine[j] == pytest.approx(float(ref)), f"column {j}"


def test_custom_exclusions_union_onto_a_named_basis():
    a2a = age_to_age_matrix(MOTOR)
    mask = mask_for_basis(a2a, BASIS_EX_HI_LO, extra_excluded=[(0, 1)])
    assert mask[0, 1]
    assert mask[2, 0], "the basis' own exclusions must survive"


def test_out_of_range_custom_exclusions_are_ignored():
    """A stored selection may outlive a re-shaped triangle."""
    a2a = age_to_age_matrix(MOTOR)
    mask = mask_for_basis(a2a, BASIS_ALL, extra_excluded=[(99, 0), (0, 99), ("x", 1)])
    assert not mask.any()


# ---------------------------------------------------------------------------
# CDF and the written block
# ---------------------------------------------------------------------------


def test_cdf_is_the_suffix_product_with_blanks_as_one():
    assert cdf_from_ldf_row([2.0, 3.0, None]) == pytest.approx([6.0, 3.0, 1.0])


def test_benchmark_rows_pair_every_ldf_with_its_cdf_and_end_with_the_count():
    rows = benchmark_rows(MOTOR)
    labels = [label for label, _ in rows]
    assert labels[-1] == "Factor Count"
    assert labels[:4] == [
        "Simple Avg LDF", "Simple Avg CDF", "Weighted Avg LDF", "Weighted Avg CDF",
    ]
    assert dict(rows)["Factor Count"] == [3, 2, 1, 0]
    for label, values in rows:
        assert len(values) == MOTOR.shape[1], label
