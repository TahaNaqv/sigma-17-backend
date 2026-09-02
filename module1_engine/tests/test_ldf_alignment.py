"""Defect F5: the `Weighted Avg LDF` row's development-column alignment.

The engine used to write the weighted link ratio for `dev i-1 -> dev i` at development column
`i`, while the age-to-age block, `Simple Avg` and `Selected LDF` all put the `dev j -> dev j+1`
factor at column `j`. One column of drift, in the only benchmark row that was usable — because
F3 had collapsed the other one — so copying it into `Selected LDF` overstated the total Paid CL
ultimate by +178% on the reference book.

The alignment is not a matter of taste. `Selected CDF[j] = PRODUCT(LDF[j] : last)`, and
`cdf_for_row` hands the CDF at development column `m` to the cohort whose maturity is `m`; for
that CDF to mean "develop from `m` to ultimate", `LDF[m]` must be the `m -> m+1` factor.

These tests fail if anything reintroduces the shift.
"""

import numpy as np
import pandas as pd
import pytest

from module1_engine.averages import BASIS_VOLUME_WEIGHTED, age_to_age_matrix, ldf_for_basis
from module1_engine.engine import (
    calculate_age_to_age_factors,
    cdf_for_row,
    selected_cdf_from_ldf,
    selected_cdf_row_to_series,
)
from module1_engine.triangles import volume_weighted_ldf

CUM = pd.DataFrame({
    "Accident Period": ["2016-Q1", "2016-Q2", "2016-Q3", "2016-Q4"],
    0: [440404.0, 1199143.0, 166090.0, 507141.0],
    1: [3589719.0, 2221195.0, 2847926.0, np.nan],
    2: [3639994.0, 2550170.0, np.nan, np.nan],
    3: [3823919.0, np.nan, np.nan, np.nan],
})


def _numeric(df):
    return df.iloc[:, 1:].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)


def test_weighted_and_simple_describe_the_same_development_step():
    """The regression, stated as the property that was violated.

    Both rows at column `j` must be summarising the same age-to-age column `j`, so the
    weighted value has to lie between that column's min and max.
    """
    a2a = age_to_age_matrix(_numeric(CUM))
    weighted = ldf_for_basis(_numeric(CUM), BASIS_VOLUME_WEIGHTED)
    for j in range(a2a.shape[1]):
        column = a2a[:, j]
        column = column[np.isfinite(column)]
        if column.size == 0 or weighted[j] is None:
            continue
        assert column.min() - 1e-9 <= weighted[j] <= column.max() + 1e-9, (
            f"column {j}: weighted {weighted[j]} is outside the factors it averages "
            f"[{column.min()}, {column.max()}] — the row is misaligned"
        )


def test_the_engine_row_matches_the_wp6_reference_implementation():
    """One implementation, repository-wide. The engine used to carry a second, shifted copy."""
    reference = volume_weighted_ldf(pd.DataFrame(_numeric(CUM)))
    produced = ldf_for_basis(_numeric(CUM), BASIS_VOLUME_WEIGHTED)
    for j, ref in enumerate(reference):
        if np.isfinite(ref):
            assert produced[j] == pytest.approx(float(ref)), f"column {j}"


def test_the_age_to_age_frame_puts_the_j_to_j_plus_one_factor_at_column_j():
    a2a = calculate_age_to_age_factors(CUM)
    assert a2a.iloc[0, 1] == pytest.approx(3589719.0 / 440404.0)   # dev 0 -> 1 at column 0
    assert a2a.iloc[0, 2] == pytest.approx(3639994.0 / 3589719.0)  # dev 1 -> 2 at column 1


def test_a_shifted_vector_would_change_the_reserve_and_is_therefore_detectable():
    """Quantifies why this matters rather than merely asserting equality.

    Shifting the LDF vector one column right multiplies every CDF by that column's own factor,
    so the ultimate moves materially. If a future change reintroduces the shift, this is the
    size of the error it would cause.
    """
    aligned = [v if v is not None else 1.0 for v in ldf_for_basis(_numeric(CUM), BASIS_VOLUME_WEIGHTED)]
    shifted = [1.0] + aligned[:-1]
    paid = [3823919.0, 2550170.0, 2847926.0, 507141.0]

    def total(ldf):
        series = selected_cdf_row_to_series(list(selected_cdf_from_ldf(ldf)))
        return sum(paid[i] * cdf_for_row(series, i) for i in range(len(paid)))

    assert total(shifted) > total(aligned) * 1.5, (
        "the shift must remain large enough to be worth guarding against"
    )
