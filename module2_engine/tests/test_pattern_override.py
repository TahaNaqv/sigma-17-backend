"""WP3a — payment pattern override.

The maps in ``test_derived_pattern_is_a_path_specific_noop`` and
``test_acceptance_map_for_a_different_pattern`` ARE the acceptance specification
(docs/PAYMENT_PATTERN_OVERRIDE_PLAN.md §1.3 / §1.3b). They were derived by measurement
against the client reference book; if they change, the model's behaviour has changed and
that must be a deliberate, reviewed decision.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from module2_engine.engine import _compute_allocate_frames
from module2_engine.pattern_override import (
    MODE_SHAPE_ONLY,
    MODE_STRICT,
    PatternOverride,
    PatternValidationError,
    canonical_class,
    rebase,
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


def _matrix_cols(frames):
    return [c for c in frames["MainSheet"].columns if isinstance(c, (int, np.integer))]


def _rows(df: pd.DataFrame) -> list[dict]:
    return [
        {"reserving_class": rc, "dev_period": p, "weight": float(w)}
        for rc, vec in df.iterrows()
        for p, w in enumerate(vec.values)
    ]


@pytest.fixture(scope="module")
def derived_pattern(base_frames):
    """The from-inception pattern the editor's 'Derive from experience' produces."""
    ms = base_frames["MainSheet"]
    cols = _matrix_cols(base_frames)
    gross = ms[ms["GROSS/RI"] == "GROSS"]
    inc = (
        gross.drop_duplicates(subset=["RESERVINGCLASS", "Age"])
        .pivot(index="RESERVINGCLASS", columns="Age", values="Incremental")
        .reindex(columns=cols)
        .fillna(0.0)
    )
    return inc.div(inc.sum(axis=1), axis=0)


def _total(frames, sheet, column):
    return float(pd.to_numeric(frames[sheet][column], errors="coerce").sum())


# ---------------------------------------------------------------------------
# rebase — the operation that keeps LIC consistent
# ---------------------------------------------------------------------------


def test_rebase_drops_the_developed_head_and_renormalises():
    pattern = np.array([0.4, 0.3, 0.2, 0.1])
    out = rebase(pattern, age=0, width=4)
    assert out.tolist() == pytest.approx([0.5, 1 / 3, 1 / 6, 0.0], abs=1e-12)
    assert out.sum() == pytest.approx(1.0, abs=1e-12)


def test_rebase_sums_to_one_at_every_age():
    pattern = np.array([0.4, 0.3, 0.2, 0.1])
    for age in range(4):
        assert rebase(pattern, age, 4).sum() == pytest.approx(1.0, abs=1e-12)


def test_rebase_past_the_end_pays_immediately():
    """A fully-developed cohort has no future pattern; whatever remains pays now."""
    out = rebase(np.array([0.4, 0.3, 0.2, 0.1]), age=3, width=4)
    assert out.tolist() == pytest.approx([1.0, 0.0, 0.0, 0.0])


def test_rebase_on_a_zero_tail_pays_immediately():
    out = rebase(np.array([1.0, 0.0, 0.0]), age=0, width=3)
    assert out.tolist() == pytest.approx([1.0, 0.0, 0.0])


# ---------------------------------------------------------------------------
# Construction and validation
# ---------------------------------------------------------------------------


def test_shape_only_renormalises_a_vector_that_does_not_sum_to_one():
    ov = PatternOverride.from_rows(
        [
            {"reserving_class": "Motor", "dev_period": 0, "weight": 20},
            {"reserving_class": "Motor", "dev_period": 1, "weight": 30},
            {"reserving_class": "Motor", "dev_period": 2, "weight": 30},
            {"reserving_class": "Motor", "dev_period": 3, "weight": 20},
        ]
    )
    assert ov.vector("Motor", 4).tolist() == pytest.approx([0.2, 0.3, 0.3, 0.2])
    assert ov.report.rescaled["Motor"] == pytest.approx(100.0)


def test_strict_rejects_a_vector_that_does_not_sum_to_one():
    with pytest.raises(PatternValidationError) as exc:
        PatternOverride.from_rows(
            [
                {"reserving_class": "Motor", "dev_period": 0, "weight": 20},
                {"reserving_class": "Motor", "dev_period": 1, "weight": 30},
            ],
            mode=MODE_STRICT,
        )
    assert "Motor" in exc.value.errors


def test_strict_accepts_a_vector_that_already_sums_to_one():
    ov = PatternOverride.from_rows(
        [
            {"reserving_class": "Motor", "dev_period": 0, "weight": 0.6},
            {"reserving_class": "Motor", "dev_period": 1, "weight": 0.4},
        ],
        mode=MODE_STRICT,
    )
    assert ov.vector("Motor", 2).tolist() == pytest.approx([0.6, 0.4])


def test_zero_sum_pattern_is_rejected_without_dividing_by_zero():
    with pytest.raises(PatternValidationError):
        PatternOverride.from_rows(
            [{"reserving_class": "Motor", "dev_period": 0, "weight": 0.0}]
        )


def test_duplicate_development_period_is_rejected():
    with pytest.raises(PatternValidationError, match="duplicate"):
        PatternOverride.from_rows(
            [
                {"reserving_class": "Motor", "dev_period": 0, "weight": 0.5},
                {"reserving_class": "Motor", "dev_period": 0, "weight": 0.5},
            ]
        )


def test_negative_weights_are_permitted_but_flagged():
    """Recoveries genuinely produce negative increments — allowed, but surfaced."""
    ov = PatternOverride.from_rows(
        [
            {"reserving_class": "Motor", "dev_period": 0, "weight": 1.2},
            {"reserving_class": "Motor", "dev_period": 1, "weight": -0.2},
        ]
    )
    assert any("negative" in w for w in ov.report.warnings)


def test_class_matching_is_case_and_whitespace_insensitive():
    ov = PatternOverride.from_rows(
        [{"reserving_class": "  motor   insurance ", "dev_period": 0, "weight": 1.0}]
    )
    assert ov.has("Motor Insurance")
    assert canonical_class("  Motor   Insurance ") == canonical_class("motor insurance")


def test_unknown_mode_is_rejected():
    with pytest.raises(PatternValidationError, match="Unknown pattern mode"):
        PatternOverride.from_rows(
            [{"reserving_class": "Motor", "dev_period": 0, "weight": 1.0}], mode="nope"
        )


# ---------------------------------------------------------------------------
# The acceptance maps
# ---------------------------------------------------------------------------

#: Structural invariants: a pattern only redistributes cash in time and sums to 1, so
#: these can never move under ANY pattern. Anything else means the override entered at
#: the wrong place.
INVARIANT = [
    ("MainSheet", "IBNR"),
    ("MainSheet", "ULAE"),
    ("MainSheet", "RA (OS)"),
    ("MainSheet", "RA (IBNR)"),
    ("MainSheet", "Future CF"),
    ("LC", "PAA_LRC"),
    ("LC", "GMM LRC_Undiscounted"),
]


def test_derived_pattern_is_a_path_specific_noop(
    combined_bytes, base_frames, derived_pattern
):
    """§1.3 — the LIC half is the regression check, the LRC half is the deliverable."""
    ov = PatternOverride.from_rows(_rows(derived_pattern))
    frames, _ = _compute_allocate_frames(combined_bytes, None, pattern_override=ov)
    cols = _matrix_cols(base_frames)

    # LIC path: exact no-op.
    assert np.allclose(
        base_frames["MainSheet"][cols].values, frames["MainSheet"][cols].values, atol=1e-12
    )
    for sheet, column in [("MainSheet", "Discounting Impact"),
                          ("MainSheet", "Change in Discounting Impact")]:
        assert _total(frames, sheet, column) == pytest.approx(
            _total(base_frames, sheet, column), rel=1e-12
        ), f"{column} moved — the LIC path should be untouched by the derived pattern"

    # LRC path: the deliberate correction.
    base_lrc = _total(base_frames, "LC", "GMM LRC_Discounted_CY")
    new_lrc = _total(frames, "LC", "GMM LRC_Discounted_CY")
    assert (new_lrc - base_lrc) / base_lrc == pytest.approx(-0.00499, abs=1e-5)


def test_acceptance_map_for_a_different_pattern(combined_bytes, base_frames):
    """§1.3b — measured with a deliberately long-tailed pattern (0.85^k)."""
    cols = _matrix_cols(base_frames)
    width = len(cols)
    classes = sorted(base_frames["MainSheet"]["RESERVINGCLASS"].unique())
    vec = np.array([0.85 ** k for k in range(width)])
    vec = vec / vec.sum()
    frame = pd.DataFrame([vec] * len(classes), index=classes, columns=range(width))
    ov = PatternOverride.from_rows(_rows(frame))
    frames, _ = _compute_allocate_frames(combined_bytes, None, pattern_override=ov)

    for sheet, column in INVARIANT:
        assert _total(frames, sheet, column) == pytest.approx(
            _total(base_frames, sheet, column), rel=1e-9
        ), f"{column} moved under a pattern override — wrong insertion point"

    expected = {
        ("MainSheet", "Discounting Impact"): -1.15598,
        ("MainSheet", "Change in Discounting Impact"): -1.68748,
        ("LC", "GMM LRC_Discounted_CY"): -0.04954,
    }
    for (sheet, column), want in expected.items():
        base = _total(base_frames, sheet, column)
        got = _total(frames, sheet, column)
        assert (got - base) / abs(base) == pytest.approx(want, rel=1e-3), column


def test_override_reaches_both_paths_not_just_one(combined_bytes, base_frames):
    """The most likely implementation error is wiring one consumer and forgetting the
    other. A long-tail pattern must move BOTH the LIC and the LRC discounting."""
    cols = _matrix_cols(base_frames)
    width = len(cols)
    classes = sorted(base_frames["MainSheet"]["RESERVINGCLASS"].unique())
    vec = np.array([0.85 ** k for k in range(width)])
    vec = vec / vec.sum()
    frame = pd.DataFrame([vec] * len(classes), index=classes, columns=range(width))
    ov = PatternOverride.from_rows(_rows(frame))
    frames, _ = _compute_allocate_frames(combined_bytes, None, pattern_override=ov)

    assert _total(frames, "MainSheet", "Discounting Impact") != pytest.approx(
        _total(base_frames, "MainSheet", "Discounting Impact"), rel=1e-6
    ), "LIC path not wired"
    assert _total(frames, "LC", "GMM LRC_Discounted_CY") != pytest.approx(
        _total(base_frames, "LC", "GMM LRC_Discounted_CY"), rel=1e-6
    ), "LRC path not wired"


def test_partial_override_leaves_other_classes_untouched(combined_bytes, base_frames):
    cols = _matrix_cols(base_frames)
    width = len(cols)
    classes = sorted(base_frames["MainSheet"]["RESERVINGCLASS"].unique())
    target = classes[0]
    vec = np.array([0.85 ** k for k in range(width)])
    vec = vec / vec.sum()
    ov = PatternOverride.from_rows(
        _rows(pd.DataFrame([vec], index=[target], columns=range(width)))
    )
    frames, _ = _compute_allocate_frames(combined_bytes, None, pattern_override=ov)

    base_ms, new_ms = base_frames["MainSheet"], frames["MainSheet"]
    for rc in classes:
        b = base_ms[base_ms.RESERVINGCLASS == rc][cols].to_numpy()
        n = new_ms[new_ms.RESERVINGCLASS == rc][cols].to_numpy()
        if rc == target:
            assert not np.allclose(b, n), "the targeted class did not change"
        else:
            assert np.allclose(b, n, atol=1e-12), f"{rc} changed but was not overridden"


def test_override_applies_to_both_treaty_types(combined_bytes, base_frames):
    """LIC carries RI rows; a class-level pattern must reach them (plan §1.4)."""
    cols = _matrix_cols(base_frames)
    width = len(cols)
    target = sorted(base_frames["MainSheet"]["RESERVINGCLASS"].unique())[0]
    vec = np.array([0.85 ** k for k in range(width)])
    vec = vec / vec.sum()
    ov = PatternOverride.from_rows(
        _rows(pd.DataFrame([vec], index=[target], columns=range(width)))
    )
    frames, _ = _compute_allocate_frames(combined_bytes, None, pattern_override=ov)
    ms = frames["MainSheet"]
    for treaty in ("GROSS", "RI"):
        rows = ms[(ms.RESERVINGCLASS == target) & (ms["GROSS/RI"] == treaty)]
        developed = rows[rows["Expected Unpaid %"] != 0]
        assert len(developed), f"no {treaty} rows to check"
        assert developed[cols].to_numpy().sum() > 0


def test_fully_developed_rows_keep_their_immediate_payout(combined_bytes, base_frames):
    """An override must not resurrect development on a row with none left."""
    cols = _matrix_cols(base_frames)
    width = len(cols)
    classes = sorted(base_frames["MainSheet"]["RESERVINGCLASS"].unique())
    vec = np.array([0.85 ** k for k in range(width)])
    vec = vec / vec.sum()
    frame = pd.DataFrame([vec] * len(classes), index=classes, columns=range(width))
    ov = PatternOverride.from_rows(_rows(frame))
    frames, _ = _compute_allocate_frames(combined_bytes, None, pattern_override=ov)
    ms = frames["MainSheet"]
    done = ms[ms["Expected Unpaid %"] == 0]
    if len(done):
        assert np.allclose(done[cols[0]].to_numpy(), 1.0)
        assert np.allclose(done[cols[1:]].to_numpy(), 0.0)


def test_unmatched_class_is_reported_not_silently_ignored(combined_bytes):
    ov = PatternOverride.from_rows(
        [
            {"reserving_class": "Nonexistent Class", "dev_period": 0, "weight": 0.5},
            {"reserving_class": "Nonexistent Class", "dev_period": 1, "weight": 0.5},
        ]
    )
    _compute_allocate_frames(combined_bytes, None, pattern_override=ov)
    assert "Nonexistent Class" in ov.report.unmatched_classes
    assert any("contributed nothing" in w for w in ov.report.warnings)


def test_pattern_longer_than_the_horizon_truncates_and_warns(combined_bytes, base_frames):
    width = len(_matrix_cols(base_frames)) + 10
    target = sorted(base_frames["MainSheet"]["RESERVINGCLASS"].unique())[0]
    vec = np.full(width, 1.0 / width)
    ov = PatternOverride.from_rows(
        _rows(pd.DataFrame([vec], index=[target], columns=range(width)))
    )
    _compute_allocate_frames(combined_bytes, None, pattern_override=ov)
    assert any("truncated" in w for w in ov.report.warnings)


def test_no_override_is_value_identical_to_no_argument(combined_bytes, base_frames):
    frames, _ = _compute_allocate_frames(combined_bytes, None, pattern_override=None)
    assert [k for k in base_frames if not base_frames[k].equals(frames[k])] == []
