"""WP6 — grain-parameterised triangles, credibility, and the cross-grain bridge.

``test_monthly_ldfs_do_not_compose_into_quarterly_ldfs`` is a NEGATIVE test: it asserts an
invalid feature has not been reintroduced. An earlier design proposed deriving quarterly
LDFs as the product of three monthly LDFs; measured on this data it is wrong by +409%.
Anyone reasoning from first principles will be tempted to add it back.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.grain import MONTHLY, QUARTERLY, YEARLY, get_grain
from module1_engine.engine import import_data
from module1_engine.triangles import (
    CREDIBILITY_UNUSABLE,
    build_triangle,
    cdf_from_ldf,
    implied_cdf_from_finer_grain,
    volume_weighted_ldf,
)

CLAIMS_DIR = Path(__file__).resolve().parents[2] / "benchmarks" / "fixtures" / "summary_ref" / "claims_paid"
START, END = "2016-01-01", "2017-12-31"

pytestmark = pytest.mark.skipif(not CLAIMS_DIR.is_dir(), reason="reference fixture not available")


@pytest.fixture(scope="module")
def paid():
    return import_data(str(CLAIMS_DIR), "AMOUNTPAID", is_os=False)


@pytest.fixture(scope="module")
def triangles(paid):
    return {
        "monthly": build_triangle(paid, grain=MONTHLY, start=START, end=END),
        "quarterly": build_triangle(paid, grain=QUARTERLY, start=START, end=END),
        "yearly": build_triangle(paid, grain=YEARLY, start=START, end=END),
    }


# ---------------------------------------------------------------------------
# Conservation
# ---------------------------------------------------------------------------


def test_every_grain_carries_the_same_money(triangles):
    totals = {
        k: float(t.incremental.to_numpy().sum()) for k, t in triangles.items()
    }
    assert totals["quarterly"] == pytest.approx(107_488_826, rel=1e-9)
    assert totals["monthly"] == pytest.approx(totals["quarterly"], rel=1e-12)
    assert totals["yearly"] == pytest.approx(totals["quarterly"], rel=1e-12)


def test_grain_shapes_match_the_experience_window(triangles):
    # 24-month window -> 24 months / 8 quarters / 2 years.
    assert len(triangles["monthly"].accident_labels) == 24
    assert len(triangles["quarterly"].accident_labels) == 8
    assert len(triangles["yearly"].accident_labels) == 2


def test_each_quarterly_cohort_is_exactly_three_monthly_cohorts(triangles):
    parents = [
        QUARTERLY.label_for(MONTHLY.parse(m).to_timestamp())
        for m in triangles["monthly"].accident_labels
    ]
    counts = pd.Series(parents).value_counts()
    assert set(counts.unique()) == {3}
    assert set(counts.index) == set(triangles["quarterly"].accident_labels)


def test_the_unobserved_lower_triangle_is_nan_not_zero(triangles):
    """A fabricated zero would silently corrupt every age-to-age factor."""
    cum = triangles["quarterly"].cumulative
    n = len(cum)
    # Row 0 is the OLDEST cohort and is fully observed; the newest has only development 0.
    assert not pd.isna(cum.iloc[0, n - 1])
    assert pd.isna(cum.iloc[n - 1, 1])
    assert not pd.isna(cum.iloc[n - 1, 0])


# ---------------------------------------------------------------------------
# The invalid bridge must stay absent
# ---------------------------------------------------------------------------


def test_monthly_ldfs_do_not_compose_into_quarterly_ldfs(triangles):
    """NEGATIVE TEST — the composition is invalid; this proves it, so nobody re-adds it.

    A quarterly accident period aggregates three monthly cohorts at different maturities,
    so a quarterly link ratio is not the product of three monthly link ratios.
    """
    lq = volume_weighted_ldf(triangles["quarterly"].cumulative)
    lm = volume_weighted_ldf(triangles["monthly"].cumulative)
    composed = np.prod(lm[0:3])
    assert lq[0] == pytest.approx(3.457375, rel=1e-4)
    assert composed == pytest.approx(17.597184, rel=1e-4)
    # More than four times out: not a rounding difference, a structural one.
    assert abs(composed - lq[0]) / lq[0] > 4.0


def test_no_ldf_composition_helper_is_exported():
    import module1_engine.triangles as module

    assert not [n for n in dir(module) if "compose" in n.lower()]


# ---------------------------------------------------------------------------
# The valid bridge
# ---------------------------------------------------------------------------


def test_implied_cdf_reproduces_the_measured_values(triangles):
    result = implied_cdf_from_finer_grain(
        triangles["monthly"], triangles["quarterly"],
        fine_grain=MONTHLY, coarse_grain=QUARTERLY,
    )
    assert sum(result.ultimate) == pytest.approx(1_069_489_457, rel=1e-6)
    tail = dict(zip(result.labels, result.implied_cdf))["2017-Q4"]
    assert tail == pytest.approx(69.8101, rel=1e-4)


def test_the_implied_ultimate_differs_materially_from_the_quarterly_one(triangles):
    """+92% on this book — the reason the credibility gate exists at all."""
    result = implied_cdf_from_finer_grain(
        triangles["monthly"], triangles["quarterly"],
        fine_grain=MONTHLY, coarse_grain=QUARTERLY,
    )
    qcdf = cdf_from_ldf(volume_weighted_ldf(triangles["quarterly"].cumulative))
    cum = triangles["quarterly"].cumulative
    n = len(cum)
    quarterly_ultimate = sum(
        float(cum.iloc[i, min(n - 1 - i, cum.shape[1] - 1)]) * qcdf[min(n - 1 - i, len(qcdf) - 1)]
        for i in range(n)
    )
    assert sum(result.ultimate) / quarterly_ultimate == pytest.approx(1.92, rel=0.02)


def test_implied_cdf_labels_align_with_the_coarse_triangle(triangles):
    result = implied_cdf_from_finer_grain(
        triangles["monthly"], triangles["quarterly"],
        fine_grain=MONTHLY, coarse_grain=QUARTERLY,
    )
    assert result.labels == triangles["quarterly"].accident_labels


# ---------------------------------------------------------------------------
# Credibility
# ---------------------------------------------------------------------------


def test_credibility_is_scored_on_density_not_raw_cell_count(triangles):
    """Regression: an earlier gate on `non_empty < 30` scored the quarterly triangle
    (26 cells, median 146 claims) BELOW the monthly one (158 cells, median 24) — backwards,
    because a coarser grain has fewer cells by construction."""
    q = triangles["quarterly"].credibility
    m = triangles["monthly"].credibility
    assert q.median_claims_per_cell > m.median_claims_per_cell
    assert q.fill_ratio > m.fill_ratio
    assert q.level == "high"
    assert m.level == "medium"


def test_a_sparse_class_is_unusable_at_every_grain(paid):
    thin = paid[(paid.RESERVINGCLASS == "Banker's Blanket") & (paid.RI_TREATY_TYPE == "GROSS")]
    for grain in (MONTHLY, QUARTERLY):
        credibility = build_triangle(thin, grain=grain, start=START, end=END).credibility
        assert credibility.level == CREDIBILITY_UNUSABLE


def test_an_unusable_triangle_refuses_to_imply_factors_even_when_overridden(paid):
    thin = paid[(paid.RESERVINGCLASS == "Banker's Blanket") & (paid.RI_TREATY_TYPE == "GROSS")]
    fine = build_triangle(thin, grain=MONTHLY, start=START, end=END)
    coarse = build_triangle(thin, grain=QUARTERLY, start=START, end=END)
    with pytest.raises(ValueError, match="too sparse"):
        implied_cdf_from_finer_grain(
            fine, coarse, fine_grain=MONTHLY, coarse_grain=QUARTERLY,
            allow_low_credibility=True,
        )


def test_a_low_credibility_triangle_requires_explicit_confirmation(paid):
    thin = paid[paid.RESERVINGCLASS == "Fire and property damage"]
    fine = build_triangle(thin, grain=MONTHLY, start=START, end=END)
    coarse = build_triangle(thin, grain=QUARTERLY, start=START, end=END)
    if fine.credibility.level != "low":
        pytest.skip("fixture no longer scores low")
    with pytest.raises(ValueError, match="thin"):
        implied_cdf_from_finer_grain(fine, coarse, fine_grain=MONTHLY, coarse_grain=QUARTERLY)
    result = implied_cdf_from_finer_grain(
        fine, coarse, fine_grain=MONTHLY, coarse_grain=QUARTERLY,
        allow_low_credibility=True,
    )
    assert result.labels


# ---------------------------------------------------------------------------
# Exclusions and edges
# ---------------------------------------------------------------------------


def test_claim_exclusions_apply_identically_at_every_grain():
    """Read the raw file rather than `import_data`, which drops CLAIMNUMBER — the gap WP5
    exists to close. The triangle service is ready for the exclusions ahead of it."""
    raw = pd.concat(
        [pd.read_excel(f) for f in sorted(CLAIMS_DIR.glob("*.xlsx"))], ignore_index=True
    )
    raw = raw.rename(columns={"AMOUNTPAID": "Amount"})
    top = (
        raw[(raw.RI_TREATY_TYPE == "GROSS") & (raw.HEADOFDAMAGE == "Payment")]
        .groupby("CLAIMNUMBER")["Amount"].sum().nlargest(5).index.tolist()
    )
    totals = {}
    for name, grain in (("monthly", MONTHLY), ("quarterly", QUARTERLY), ("yearly", YEARLY)):
        t = build_triangle(raw, grain=grain, start=START, end=END, excluded_claims=top)
        totals[name] = float(t.incremental.to_numpy().sum())
    assert totals["monthly"] == pytest.approx(totals["quarterly"], rel=1e-9)
    assert totals["yearly"] == pytest.approx(totals["quarterly"], rel=1e-9)
    full = build_triangle(raw, grain=QUARTERLY, start=START, end=END)
    assert totals["quarterly"] < float(full.incremental.to_numpy().sum())


def test_import_data_now_carries_claim_identity(paid):
    """WP5 plumbed CLAIMNUMBER and REPORTEDDATE through `import_data`; before that, claim
    identity did not exist anywhere in the system."""
    assert "CLAIMNUMBER" in paid.columns
    assert paid["CLAIMNUMBER"].isna().sum() == 0


def test_exclusions_on_data_without_claim_numbers_warn_rather_than_fail(paid):
    """A frame from another source may still lack the column; degrade, never raise."""
    result = build_triangle(
        paid.drop(columns=["CLAIMNUMBER"]),
        grain=QUARTERLY, start=START, end=END, excluded_claims=["ANY"],
    )
    assert any("no CLAIMNUMBER" in w for w in result.warnings)
    assert result.accident_labels


def test_exclusions_now_work_end_to_end_from_import_data(paid):
    """The path the large-claims feature actually uses."""
    top = (
        paid[(paid.RI_TREATY_TYPE == "GROSS") & (paid.HEADOFDAMAGE == "Payment")]
        .groupby("CLAIMNUMBER")["Amount"].sum().nlargest(10).index.tolist()
    )
    full = build_triangle(paid, grain=QUARTERLY, start=START, end=END)
    less = build_triangle(
        paid, grain=QUARTERLY, start=START, end=END, excluded_claims=top
    )
    assert float(less.incremental.to_numpy().sum()) < float(full.incremental.to_numpy().sum())
    assert not any("no CLAIMNUMBER" in w for w in less.warnings)


def test_an_empty_frame_produces_an_empty_triangle_not_a_crash(paid):
    result = build_triangle(paid.iloc[0:0], grain=MONTHLY, start=START, end=END)
    assert result.accident_labels == []
    assert result.credibility.level == CREDIBILITY_UNUSABLE
    assert any("No claims" in w for w in result.warnings)


def test_get_grain_rejects_an_unknown_key():
    with pytest.raises(ValueError, match="Unknown period grain"):
        get_grain("fortnightly")
    assert get_grain(None) is QUARTERLY
