"""Unit tests for the stage profiling instrumentation (core.profiling)."""

import time

from core.profiling import (
    format_report,
    profile_stage,
    profiling_session,
    stage_timer,
)


def test_noop_without_session_or_env(monkeypatch):
    monkeypatch.delenv("SIGMA_PROFILE", raising=False)
    # Should run the body and record nothing.
    with stage_timer("nothing"):
        x = 1 + 1
    assert x == 2


def test_session_collects_records():
    with profiling_session() as prof:
        with stage_timer("outer"):
            with stage_timer("inner"):
                time.sleep(0.001)
    names = [r.name for r in prof.records]
    assert names == ["inner", "outer"]  # inner closes first
    depths = {r.name: r.depth for r in prof.records}
    assert depths["outer"] == 0
    assert depths["inner"] == 1
    assert all(r.seconds >= 0 for r in prof.records)


def test_profile_stage_decorator():
    @profile_stage("decorated")
    def work():
        return 42

    with profiling_session() as prof:
        assert work() == 42
    assert [r.name for r in prof.records] == ["decorated"]


def test_format_report_includes_total():
    with profiling_session() as prof:
        with stage_timer("a"):
            pass
    report = format_report(prof.records)
    assert "Stage" in report
    assert "TOTAL" in report
    assert format_report([]) == "(no timing records)"
