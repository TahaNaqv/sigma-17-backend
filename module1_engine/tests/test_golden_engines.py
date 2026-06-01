"""Golden regression tests: every benchmark fixture must match its frozen golden.

These are the bit-identical guard for the optimisation work. They are
*data-driven*: each fixture under benchmarks/fixtures/ with a frozen golden
becomes one parametrised test. When no fixtures/goldens are present (e.g. a
fresh checkout without the private benchmark data) the suite skips cleanly, so
CI stays green until real data is dropped in.

Workflow:
    python manage.py capture_golden --all     # on current code, once
    pytest                                     # fails if any value changes
"""

import pytest

from processing import benchmarks, golden

_FIXTURES = [
    fx for fx in benchmarks.discover_fixtures()
    if (fx.golden_dir / "manifest.json").exists()
]


@pytest.mark.skipif(not _FIXTURES, reason="no benchmark fixtures with goldens present")
@pytest.mark.parametrize("fx", _FIXTURES, ids=[f.name for f in _FIXTURES])
def test_engine_output_matches_golden(fx):
    struct, _ = benchmarks.run_fixture(fx)
    diffs = golden.diff_struct(struct, golden.thaw(fx.golden_dir))
    assert not diffs, "golden mismatch:\n" + "\n".join(f"  - {d}" for d in diffs[:50])


def test_harness_importable():
    """Smoke test so the harness itself is always exercised, even with no data."""
    assert isinstance(benchmarks.discover_fixtures(), list)
