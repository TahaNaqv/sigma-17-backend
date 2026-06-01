"""Lightweight stage-level timing & memory instrumentation.

This is the measurement backbone for the performance-optimization effort
(see docs/PERFORMANCE_OPTIMIZATION_PLAN.md, Phase 0). It is deliberately
*behaviour-preserving*: when no profiling session is active and the
``SIGMA_PROFILE`` env flag is off, every helper here is a near-zero-cost
no-op, so it is safe to leave wired into hot paths permanently.

Usage
-----
Wrap a region:

    from core.profiling import stage_timer
    with stage_timer("calculate_additional_matrix"):
        ...

Decorate a function:

    @profile_stage()                 # label = qualified function name
    def calculate_upr(...): ...

Collect a structured report (e.g. in a benchmark command or a Celery task):

    from core.profiling import profiling_session, format_report
    with profiling_session() as prof:
        run_generate_summary(...)
    print(format_report(prof.records))

Setting ``SIGMA_PROFILE=1`` additionally emits one ``logging`` line per stage,
which is handy for profiling real jobs running under the Celery worker without
having to open a session explicitly.
"""

from __future__ import annotations

import contextlib
import functools
import logging
import os
import threading
import time
from dataclasses import dataclass, field

try:  # POSIX only; falls back to 0 on platforms without resource (e.g. Windows)
    import resource
except ImportError:  # pragma: no cover - non-POSIX
    resource = None  # type: ignore[assignment]

logger = logging.getLogger("sigma.profiling")

_local = threading.local()


@dataclass
class StageTiming:
    """One timed region. ``rss_delta_kb`` is the change in peak RSS across it."""

    name: str
    seconds: float
    rss_delta_kb: int
    depth: int = 0


@dataclass
class Profiler:
    """Accumulates :class:`StageTiming` records for the current session."""

    records: list[StageTiming] = field(default_factory=list)
    _depth: int = 0

    def record(self, name: str, seconds: float, rss_delta_kb: int, depth: int) -> None:
        self.records.append(StageTiming(name, seconds, rss_delta_kb, depth))


def _peak_rss_kb() -> int:
    """Peak resident-set size in kilobytes (0 where unavailable).

    On Linux ``ru_maxrss`` is already in KB; on macOS it is bytes, so we
    normalise. This is a process-wide high-water mark, hence we report deltas.
    """
    if resource is None:
        return 0
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if os.uname().sysname == "Darwin":  # pragma: no cover - platform specific
        return int(raw / 1024)
    return int(raw)


def _env_enabled() -> bool:
    return os.environ.get("SIGMA_PROFILE", "").strip().lower() in {"1", "true", "yes", "on"}


def _active() -> Profiler | None:
    return getattr(_local, "profiler", None)


@contextlib.contextmanager
def profiling_session():
    """Activate collection for the current thread; yields the :class:`Profiler`.

    Nestable: the previous session (if any) is restored on exit.
    """
    prev = _active()
    prof = Profiler()
    _local.profiler = prof
    try:
        yield prof
    finally:
        _local.profiler = prev


@contextlib.contextmanager
def stage_timer(name: str):
    """Time a region, recording into the active session and/or the log.

    No-op (just runs the body) when there is no active session *and*
    ``SIGMA_PROFILE`` is unset, keeping overhead off the default code path.
    """
    prof = _active()
    if prof is None and not _env_enabled():
        yield
        return

    entry_depth = prof._depth if prof is not None else 0
    if prof is not None:
        prof._depth += 1
    start_rss = _peak_rss_kb()
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        rss_delta = _peak_rss_kb() - start_rss
        if prof is not None:
            prof._depth -= 1
            prof.record(name, elapsed, rss_delta, entry_depth)
        if _env_enabled():
            logger.info(
                "stage %s%s took %.3fs (peak RSS +%d KB)",
                "  " * entry_depth,
                name,
                elapsed,
                rss_delta,
            )


def profile_stage(name: str | None = None):
    """Decorator form of :func:`stage_timer`; defaults the label to the qualname."""

    def decorator(fn):
        label = name or fn.__qualname__

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with stage_timer(label):
                return fn(*args, **kwargs)

        return wrapper

    return decorator


def format_report(records: list[StageTiming]) -> str:
    """Render timing records as an indented, aligned table string."""
    if not records:
        return "(no timing records)"
    label_w = max(len(("  " * r.depth) + r.name) for r in records)
    label_w = max(label_w, len("Stage"))
    lines = [
        f"{'Stage':<{label_w}}  {'Seconds':>10}  {'Peak RSS Δ':>12}",
        f"{'-' * label_w}  {'-' * 10}  {'-' * 12}",
    ]
    for r in records:
        label = ("  " * r.depth) + r.name
        rss = f"{r.rss_delta_kb / 1024:+.1f} MB" if r.rss_delta_kb else "—"
        lines.append(f"{label:<{label_w}}  {r.seconds:>10.3f}  {rss:>12}")
    total = sum(r.seconds for r in records if r.depth == 0)
    lines.append(f"{'TOTAL (depth 0)':<{label_w}}  {total:>10.3f}  {'':>12}")
    return "\n".join(lines)
