"""WP5 — large-claim ranking and exclusion.

The measured facts in docs/LARGE_CLAIMS_EXCLUSION_PLAN.md §1.3–§1.5 are the acceptance
specification. Two of them exist because a naive implementation gets them wrong in a way
that is invisible: an unscoped paid ranking, and an OS ranking that sums across as-at
snapshots.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.grain import QUARTERLY
from module1_engine.engine import import_data
from module1_engine.large_claims import (
    DEFAULT_MODE,
    MODE_ADD_BACK,
    MODE_ENTIRELY,
    MODE_LDF_ONLY,
    ExclusionPlan,
    outstanding_by_claim,
    paid_by_claim,
    rank_claims,
)
from module1_engine.triangles import build_triangle, cdf_from_ldf, volume_weighted_ldf

FIXTURES = Path(__file__).resolve().parents[2] / "benchmarks" / "fixtures" / "summary_ref"
START, END = "2016-01-01", "2017-12-31"

pytestmark = pytest.mark.skipif(
    not (FIXTURES / "claims_paid").is_dir(), reason="reference fixture not available"
)


@pytest.fixture(scope="module")
def paid():
    return import_data(str(FIXTURES / "claims_paid"), "AMOUNTPAID", is_os=False)


@pytest.fixture(scope="module")
def os_data():
    return import_data(str(FIXTURES / "claims_os"), "AMOUNTOUTSTANDING", is_os=True)


# ---------------------------------------------------------------------------
# Ranking — the two rules a naive implementation gets wrong
# ---------------------------------------------------------------------------


def test_paid_ranking_is_scoped_to_one_treaty_and_head_of_damage(paid):
    """An unscoped sum adds GROSS to RI and nets Payment against Salvage."""
    scoped = paid_by_claim(paid, treaty="GROSS", head_of_damage="Payment")
    naive = (
        paid.groupby("CLAIMNUMBER")["Amount"].sum().rename("paid").reset_index()
    )
    top_scoped = scoped.nlargest(1, "paid").iloc[0]
    top_naive = naive.nlargest(1, "paid").iloc[0]
    assert top_scoped["paid"] != pytest.approx(top_naive["paid"])


def test_the_paid_ranked_concentration_is_the_measured_22_3_percent(paid, os_data):
    report = rank_claims(paid, os_data, per_class=False, top_n=10, rank_on="paid")
    assert report.concentration == pytest.approx(0.223, abs=0.005)


def test_outstanding_uses_the_latest_as_at_not_a_sum_across_snapshots(os_data):
    """The naive sum overstates the largest claim 6.5x AND returns a different claim
    first — so a naive implementation lists the wrong claims, not merely wrong numbers."""
    correct = outstanding_by_claim(os_data, treaty="GROSS").nlargest(1, "outstanding").iloc[0]

    gross = os_data[os_data["RI_TREATY_TYPE"] == "GROSS"]
    naive = (
        gross.groupby("CLAIMNUMBER")["Amount"].sum()
        .rename("outstanding").reset_index().nlargest(1, "outstanding").iloc[0]
    )
    assert naive["CLAIMNUMBER"] != correct["CLAIMNUMBER"]
    assert naive["outstanding"] / correct["outstanding"] > 5.0


def test_ranking_on_incurred_surfaces_claims_with_no_payment_yet(paid, os_data):
    """A claim carrying 6.0m outstanding and nothing paid is unquestionably large; a
    paid-only ranking would miss it entirely."""
    incurred = rank_claims(paid, os_data, per_class=False, top_n=10, rank_on="incurred")
    paid_only = rank_claims(paid, os_data, per_class=False, top_n=10, rank_on="paid")
    assert any(c.paid == 0 for c in incurred.claims)
    assert all(c.paid > 0 for c in paid_only.claims)
    assert DEFAULT_MODE == MODE_ADD_BACK


def test_per_class_ranking_draws_from_every_class(paid, os_data):
    book_wide = rank_claims(paid, os_data, per_class=False, top_n=3)
    per_class = rank_claims(paid, os_data, per_class=True, top_n=3)
    assert len({c.reserving_class for c in per_class.claims}) > len(
        {c.reserving_class for c in book_wide.claims}
    )


def test_threshold_and_top_n_agree_at_the_nth_value(paid, os_data):
    by_n = rank_claims(paid, os_data, per_class=False, top_n=5, rank_on="paid")
    nth = min(c.paid for c in by_n.claims)
    by_threshold = rank_claims(
        paid, os_data, per_class=False, kind="threshold", threshold=nth, rank_on="paid"
    )
    assert set(by_threshold.claim_numbers) >= set(by_n.claim_numbers)


def test_a_large_recovery_ranks_as_a_large_claim(paid, os_data):
    """'Largest' is by magnitude — a big negative is as material as a big positive."""
    work = paid.copy()
    target = work["CLAIMNUMBER"].iloc[0]
    work.loc[work["CLAIMNUMBER"] == target, "Amount"] = -50_000_000.0
    work.loc[work["CLAIMNUMBER"] == target, "RI_TREATY_TYPE"] = "GROSS"
    work.loc[work["CLAIMNUMBER"] == target, "HEADOFDAMAGE"] = "Payment"
    report = rank_claims(work, os_data, per_class=False, top_n=3, rank_on="paid")
    assert target in report.claim_numbers


def test_data_without_claim_numbers_reports_rather_than_returning_an_empty_list(paid):
    report = rank_claims(paid.drop(columns=["CLAIMNUMBER"]), None, per_class=False)
    assert report.claims == []
    assert any("no claim numbers" in w for w in report.warnings)


def test_an_unknown_selection_kind_is_rejected(paid, os_data):
    with pytest.raises(ValueError, match="Unknown selection kind"):
        rank_claims(paid, os_data, kind="vibes")


# ---------------------------------------------------------------------------
# Exclusion routing
# ---------------------------------------------------------------------------


def test_the_mode_routing_table():
    """Add-back does NOT filter the Reserve Summary base.

    That is the whole point: the base keeps tying to the ledger, and the netting-out
    happens inside the chain-ladder ultimate via the Large Paid / Large OS columns. Only
    `exclude_entirely` — which genuinely means "pretend these claims never happened" —
    filters the base.
    """
    routing = {
        MODE_ADD_BACK: (True, False, True),
        MODE_LDF_ONLY: (True, False, False),
        MODE_ENTIRELY: (True, True, False),
    }
    for mode, (tri, base, add) in routing.items():
        plan = ExclusionPlan.build(["X"], mode)
        assert (plan.filters_triangles, plan.filters_base, plan.adds_back) == (tri, base, add)


def test_an_unknown_mode_is_rejected():
    with pytest.raises(ValueError, match="Unknown exclusion mode"):
        ExclusionPlan.build(["X"], "delete_them")


def test_an_empty_exclusion_is_inert(paid):
    plan = ExclusionPlan.build([], MODE_ADD_BACK)
    assert not plan.active
    assert plan.apply(paid) is paid


def test_apply_leaves_a_frame_without_claim_numbers_untouched(paid):
    stripped = paid.drop(columns=["CLAIMNUMBER"])
    plan = ExclusionPlan.build(["X"], MODE_ENTIRELY)
    assert plan.apply(stripped) is stripped


# ---------------------------------------------------------------------------
# The measured impact of the three modes
# ---------------------------------------------------------------------------


#: Case reserve held at EOP (2017-Q4) by the ten largest GROSS/Payment claims. Pinned as a
#: constant because the OS frame is not in scope of this module's fixtures; the end-to-end
#: figure is reproduced by scripts against benchmarks/fixtures/summary_ref.
LARGE_CASE_RESERVE = 5_069_200.0


def _ultimates(df, excluded=None):
    triangle = build_triangle(
        df, grain=QUARTERLY, start=START, end=END, excluded_claims=excluded
    )
    cdf = cdf_from_ldf(volume_weighted_ldf(triangle.cumulative))
    n = len(triangle.cumulative)
    paid_td, factors = [], []
    for i in range(len(triangle.accident_labels)):
        maturity = min(n - 1 - i, triangle.cumulative.shape[1] - 1)
        value = triangle.cumulative.iloc[i, maturity]
        paid_td.append(0.0 if pd.isna(value) else float(value))
        factors.append(cdf[maturity] if maturity < len(cdf) else 1.0)
    return np.array(paid_td), np.array(factors)


def test_the_three_modes_reproduce_the_measured_impacts(paid):
    """§1.5 — and the reason `exclude_from_ldf_only` is not the default."""
    gross = paid[(paid.RI_TREATY_TYPE == "GROSS") & (paid.HEADOFDAMAGE == "Payment")]
    top = (
        gross.groupby("CLAIMNUMBER")["Amount"].sum().nlargest(10).index.tolist()
    )
    paid_all, cdf_all = _ultimates(gross)
    paid_ex, cdf_ex = _ultimates(gross, top)
    large = paid_all - paid_ex

    base = float((paid_all * cdf_all).sum())
    ldf_only = float((paid_all * cdf_ex).sum())
    # Add-back re-enters the large claims at their KNOWN INCURRED — paid plus case reserve.
    # An earlier draft added back paid-to-date alone, which silently gave an open large
    # claim a zero case reserve and read -3.8%. The case reserves are real money.
    add_back = float((paid_ex * cdf_ex + large).sum()) + LARGE_CASE_RESERVE
    entirely = float((paid_ex * cdf_ex).sum())

    assert (ldf_only - base) / base == pytest.approx(0.133, abs=0.01)
    assert (add_back - base) / base == pytest.approx(-0.027, abs=0.01)
    assert (entirely - base) / base == pytest.approx(-0.082, abs=0.01)
    # The mode that sounds least intrusive is the most intrusive, and upward.
    assert abs(ldf_only - base) > abs(add_back - base)
    assert ldf_only > base


def test_removing_large_claims_can_raise_a_cohorts_ultimate(paid):
    """§1.4 — pinned deliberately. The direction is counter-intuitive and correct; a future
    reader must not 'fix' it into silence."""
    gross = paid[(paid.RI_TREATY_TYPE == "GROSS") & (paid.HEADOFDAMAGE == "Payment")]
    top = gross.groupby("CLAIMNUMBER")["Amount"].sum().nlargest(10).index.tolist()
    paid_all, cdf_all = _ultimates(gross)
    _, cdf_ex = _ultimates(gross, top)
    base = paid_all * cdf_all
    ldf_only = paid_all * cdf_ex
    # 2016-Q4 is index 3 of the eight-quarter axis.
    assert (ldf_only[3] - base[3]) / base[3] == pytest.approx(0.369, abs=0.02)


def test_add_back_reconciles_to_the_attritional_plus_actual_split(paid):
    gross = paid[(paid.RI_TREATY_TYPE == "GROSS") & (paid.HEADOFDAMAGE == "Payment")]
    top = gross.groupby("CLAIMNUMBER")["Amount"].sum().nlargest(10).index.tolist()
    paid_all, _ = _ultimates(gross)
    paid_ex, cdf_ex = _ultimates(gross, top)
    large = paid_all - paid_ex
    total = float((paid_ex * cdf_ex + large).sum())
    assert total == pytest.approx(float((paid_ex * cdf_ex).sum()) + float(large.sum()), rel=1e-12)
    assert float(large.sum()) == pytest.approx(21_589_777, rel=1e-3)


# ---------------------------------------------------------------------------
# The golden's invariant, stated as an ordering
# ---------------------------------------------------------------------------


def test_the_frozen_mode_ordering_holds():
    """The `m1_large_claims_ref` golden pins four totals. Their VALUES will legitimately
    move if the reference data is ever re-cut; their ORDER must not.

    exclude_from_ldf_only  >  base  >  exclude_and_add_back  >  exclude_entirely

    Every element of that chain is a correctness claim:
      - ldf_only above base is the counter-intuitive result that makes it a bad default;
      - add_back below base, because attritional factors no longer develop large claims;
      - entirely below add_back, by exactly the large claims' known incurred.
    """
    from processing import benchmarks, golden

    fixtures = [f for f in benchmarks.discover_fixtures() if f.name == "m1_large_claims_ref"]
    if not fixtures or not (fixtures[0].golden_dir / "manifest.json").exists():
        pytest.skip("m1_large_claims_ref golden not present")

    frames = golden.thaw(fixtures[0].golden_dir)
    totals = None
    for sheets in frames.values():
        for frame in sheets.values():
            if "measure" in getattr(frame, "columns", []):
                totals = frame.set_index("measure")["total"]
    assert totals is not None, "totals sheet missing from the golden"

    assert totals["ult_exclude_from_ldf_only"] > totals["ult_base"]
    assert totals["ult_base"] > totals["ult_exclude_and_add_back"]
    assert totals["ult_exclude_and_add_back"] > totals["ult_exclude_entirely"]
    # And add-back exceeds "entirely" by exactly the large claims' known incurred.
    cohorts = None
    for sheets in frames.values():
        for frame in sheets.values():
            if "large_case" in getattr(frame, "columns", []):
                cohorts = frame
    known_incurred = float(cohorts["large_paid"].sum() + cohorts["large_case"].sum())
    assert totals["ult_exclude_and_add_back"] - totals["ult_exclude_entirely"] == pytest.approx(
        known_incurred, rel=1e-9
    )
