"""Stuck-job watchdog.

A worker that dies mid-run (OOM-killed, deploy, lost broker connection) leaves
its ``Module1Job`` stuck at ``running`` forever — and a job that is enqueued
while no worker is consuming stays at ``pending``. Either way the frontend (which
reattaches to and polls tracked jobs) would spin indefinitely.

`reap_stuck_jobs(...)` is a beat-scheduled sweep that fails such jobs once they
exceed the Celery hard time limit (plus a margin). The flip is done with a
status-guarded ``UPDATE`` so it can never clobber a job a worker is finalising
in the same instant — if the row is no longer ``running``/``pending``, zero rows
are updated.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import Q, QuerySet
from django.utils import timezone

from processing.models import Module1Job

logger = logging.getLogger(__name__)

# Margin added to the Celery hard time limit before a job is considered stuck.
# A running job past `time_limit` would already have been killed by Celery, so
# anything beyond time_limit + margin is definitively dead.
DEFAULT_MARGIN_SECONDS = 5 * 60

RUNNING_MESSAGE = (
    "Job exceeded the maximum runtime and was marked failed by the system "
    "watchdog (the worker did not report completion)."
)
PENDING_MESSAGE = (
    "Job did not start within the allotted time and was marked failed by the "
    "system watchdog (no worker picked it up)."
)


def _default_max_age_seconds() -> int:
    limit = getattr(settings, "CELERY_TASK_TIME_LIMIT", 3600) or 3600
    return int(limit) + DEFAULT_MARGIN_SECONDS


def find_stuck_running(*, now=None, max_age_seconds: int | None = None) -> QuerySet:
    now = now or timezone.now()
    max_age = max_age_seconds or _default_max_age_seconds()
    cutoff = now - timedelta(seconds=max_age)
    return Module1Job.objects.filter(status=Module1Job.Status.RUNNING).filter(
        Q(started_at__lt=cutoff)
        | Q(started_at__isnull=True, created_at__lt=cutoff)
    )


def find_stuck_pending(*, now=None, max_age_seconds: int | None = None) -> QuerySet:
    now = now or timezone.now()
    max_age = max_age_seconds or _default_max_age_seconds()
    cutoff = now - timedelta(seconds=max_age)
    return Module1Job.objects.filter(
        status=Module1Job.Status.PENDING,
        created_at__lt=cutoff,
    )


def _fail_if_still(job_id, expected_status: str, message: str, now) -> bool:
    """Atomically flip a job to FAILED only if it is still in `expected_status`.

    Returns True if this call performed the flip. A concurrent worker
    finalisation (status already moved on) results in 0 rows updated -> False,
    so the watchdog never overwrites a real terminal result.
    """
    updated = (
        Module1Job.objects.filter(id=job_id, status=expected_status).update(
            status=Module1Job.Status.FAILED,
            completed_at=now,
            error_message=message,
        )
    )
    return bool(updated)


def reap_stuck_jobs(
    *,
    now=None,
    max_age_seconds: int | None = None,
    batch_size: int = 500,
) -> dict:
    """Fail jobs stuck in running/pending past the watchdog threshold.

    Bounded by `batch_size` per invocation. Idempotent: a row already failed is
    not matched. Returns counters suitable for a log line / metrics.
    """
    now = now or timezone.now()
    max_age = max_age_seconds or _default_max_age_seconds()

    reaped_running = 0
    reaped_pending = 0
    examined = 0

    running_ids = list(
        find_stuck_running(now=now, max_age_seconds=max_age)
        .order_by("started_at")
        .values_list("id", flat=True)[:batch_size]
    )
    for job_id in running_ids:
        examined += 1
        if _fail_if_still(job_id, Module1Job.Status.RUNNING, RUNNING_MESSAGE, now):
            reaped_running += 1
            logger.warning("watchdog.reaped_running", extra={"job_id": str(job_id)})

    remaining = max(0, batch_size - len(running_ids))
    if remaining:
        pending_ids = list(
            find_stuck_pending(now=now, max_age_seconds=max_age)
            .order_by("created_at")
            .values_list("id", flat=True)[:remaining]
        )
        for job_id in pending_ids:
            examined += 1
            if _fail_if_still(job_id, Module1Job.Status.PENDING, PENDING_MESSAGE, now):
                reaped_pending += 1
                logger.warning("watchdog.reaped_pending", extra={"job_id": str(job_id)})

    return {
        "examined": examined,
        "reaped_running": reaped_running,
        "reaped_pending": reaped_pending,
        "max_age_seconds": max_age,
    }
