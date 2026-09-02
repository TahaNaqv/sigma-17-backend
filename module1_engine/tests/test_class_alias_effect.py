"""WP0's headline: what a reserving-class alias actually changes.

`summary_ref` and `summary_ref_aliased` are the same inputs, differing only by the
`Health -> Health Insurance` alias. Comparing them is what turns "the join is broken" from an
assertion into a measurement, and it pins the correction so a future change cannot quietly
undo it.
"""

import numpy as np
import pandas as pd
import pytest

from processing import benchmarks, golden


def _golden(name):
    fixtures = [f for f in benchmarks.discover_fixtures() if f.name == name]
    if not fixtures or not (fixtures[0].golden_dir / "manifest.json").exists():
        pytest.skip(f"{name} golden not present")
    return golden.thaw(fixtures[0].golden_dir)


@pytest.fixture(scope="module")
def unaliased():
    return _golden("summary_ref")


@pytest.fixture(scope="module")
def aliased():
    return _golden("summary_ref_aliased")


def _paid_total(struct, prefix):
    """Sum of the Reserve Summary's Paid Claims across every workbook of a class."""
    total = 0.0
    for name, sheets in struct.items():
        if not name.startswith(prefix):
            continue
        rs = sheets.get("Reserve Summary")
        if rs is None or "Paid Claims" not in getattr(rs, "columns", []):
            continue
        total += float(pd.to_numeric(rs["Paid Claims"], errors="coerce").fillna(0).sum())
    return total


def test_without_the_alias_health_has_no_paid_claims_at_all(unaliased):
    """Defect F1, pinned. The workbook is produced, looks plausible, and is wrong."""
    assert _paid_total(unaliased, "Health") == 0.0


def test_the_alias_restores_the_discarded_paid_claims(aliased):
    total = _paid_total(aliased, "Health")
    assert total == pytest.approx(35_402_487, rel=1e-6), (
        "the Health paid claims must reach the reserve once the alias is applied"
    )


def test_no_other_class_moves(unaliased, aliased):
    """The alias must be surgical. If any other class changed, it is doing more than joining
    two spellings of one name."""
    prefixes = {name.split(" ")[0] for name in unaliased} - {"Health", "Combined_Summary.xlsx"}
    for prefix in sorted(prefixes):
        before = _paid_total(unaliased, prefix)
        after = _paid_total(aliased, prefix)
        assert before == pytest.approx(after, rel=1e-9), f"{prefix} moved: {before} -> {after}"


def test_the_health_paid_triangle_is_no_longer_empty(aliased):
    sheets = aliased["Health Insurance Payment GROSS 2017-12.xlsx"]
    triangle = sheets["Paid Claims Triangle"].iloc[:8, 1:].apply(pd.to_numeric, errors="coerce")
    assert np.nansum(triangle.to_numpy()) > 0


def test_both_goldens_contain_the_same_workbooks(unaliased, aliased):
    """An alias changes what is INSIDE a workbook, never which workbooks exist — the loop is
    driven by the premium frame, which the alias does not add classes to."""
    assert set(unaliased) == set(aliased)
