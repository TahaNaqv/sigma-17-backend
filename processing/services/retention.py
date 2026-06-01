"""Output-ZIP retention and lineage-aware cascade purge.

Two operations:
- `purge_expired_outputs(...)` — daily sweeper. Drops output ZIPs whose
  retention window has elapsed and that are not on legal hold.
- `cascade_purge(...)` — admin-triggered. Walks `derived_jobs` recursively
  and removes descendants (output + row) before removing the root, so the
  PROTECT FK never fires.

Neither operation deletes job rows during the daily sweep — only output ZIP
files are deleted by retention. Rows are kept for audit/history. Cascade
purge does delete rows because it is an explicit admin action.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from processing.models import Module1Job

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Daily sweeper
# ---------------------------------------------------------------------------


def find_expired_outputs(now=None) -> QuerySet:
    """Successful jobs whose output is past its retention window, not on
    legal hold, and not yet purged."""
    now = now or timezone.now()
    return (
        Module1Job.objects
        .filter(
            status=Module1Job.Status.SUCCESS,
            retention_until__isnull=False,
            retention_until__lte=now,
            legal_hold=False,
            output_purged_at__isnull=True,
        )
        .exclude(output_zip="")
        .exclude(output_zip__isnull=True)
        .order_by("retention_until")
    )


def purge_expired_outputs(*, batch_size: int = 500, now=None) -> dict:
    """Purge expired output ZIPs. Bounded by `batch_size` per call so a single
    invocation can't lock the worker for too long.

    Returns a dict with counters suitable for log lines / metrics.
    """
    qs = find_expired_outputs(now=now)
    examined = 0
    purged = 0
    errors = 0
    for job in qs[:batch_size].iterator():
        examined += 1
        try:
            if job.purge_output(reason="retention"):
                purged += 1
            logger.info(
                "retention.purged",
                extra={
                    "job_id": str(job.id),
                    "job_type": job.job_type,
                    "org_id": str(job.organization_id),
                    "retention_until": job.retention_until.isoformat()
                        if job.retention_until else None,
                },
            )
        except Exception:
            errors += 1
            logger.exception(
                "retention.purge_failed",
                extra={"job_id": str(job.id)},
            )
    return {"examined": examined, "purged": purged, "errors": errors}


# ---------------------------------------------------------------------------
# Cascade purge (admin action)
# ---------------------------------------------------------------------------


def _walk_descendants(root: Module1Job) -> list[Module1Job]:
    """Return all descendants of `root` in BFS order. The root is NOT included.

    Lineage never crosses organizations, so we fetch the whole org's
    (id, source_job_id) edge list once and walk it in memory, then bulk-load
    the descendant rows — two queries total instead of one per node (the old
    `derived_jobs.all()`-per-node N+1, plan §3.6). Sibling order follows the
    model's default ``-created_at`` ordering; deletion correctness only depends
    on the parent-before-child BFS invariant, which is preserved.
    """
    edges = (
        Module1Job.objects
        .filter(organization_id=root.organization_id)
        .order_by("-created_at")
        .values_list("id", "source_job_id")
    )
    children_by_parent: dict = defaultdict(list)
    for child_id, parent_id in edges:
        if parent_id is not None:
            children_by_parent[parent_id].append(child_id)

    ordered_ids: list = []
    queue: deque = deque([root.id])
    seen: set = {root.id}
    while queue:
        node_id = queue.popleft()
        for child_id in children_by_parent.get(node_id, []):
            if child_id in seen:
                continue
            seen.add(child_id)
            ordered_ids.append(child_id)
            queue.append(child_id)

    jobs_by_id = Module1Job.objects.in_bulk(ordered_ids)
    return [jobs_by_id[i] for i in ordered_ids]


def cascade_purge(root_job: Module1Job, *, actor=None, force: bool = False) -> dict:
    """Force-delete `root_job` and all of its descendants.

    Order matters: we delete leaves first (deepest descendants), then walk
    upward, so the PROTECT FK never fires. Output ZIPs are deleted along the
    way.

    By default `legal_hold=True` rows abort the whole operation; pass
    `force=True` to override (the admin UI surfaces this as a separate,
    higher-friction action).
    """
    descendants = _walk_descendants(root_job)
    # Topological order: leaves first. _walk_descendants returns BFS from root,
    # so reverse + leaf-deeper invariant by sorting by depth via successive passes.
    # Simpler: just iterate in reverse and let PROTECT catch any mistake.
    descendants_reversed = list(reversed(descendants))

    holds = [
        j for j in [root_job, *descendants_reversed]
        if j.legal_hold
    ]
    if holds and not force:
        ids = ", ".join(str(j.id) for j in holds)
        raise PermissionError(
            f"Cannot purge: legal hold on {len(holds)} job(s): {ids}. "
            f"Re-run with force=True to override."
        )

    purged_outputs = 0
    deleted_rows = 0
    actor_id = getattr(actor, "id", None)

    with transaction.atomic():
        # Delete leaves first so PROTECT never blocks us.
        for job in descendants_reversed:
            if job.output_zip and job.output_purged_at is None:
                try:
                    job.output_zip.delete(save=False)
                except Exception:
                    logger.exception(
                        "cascade_purge.file_delete_failed",
                        extra={"job_id": str(job.id)},
                    )
                purged_outputs += 1
            job.delete()
            deleted_rows += 1
            logger.info(
                "cascade_purge.descendant_removed",
                extra={
                    "job_id": str(job.id),
                    "root_job_id": str(root_job.id),
                    "actor_id": actor_id,
                },
            )

        # Finally remove the root.
        if root_job.output_zip and root_job.output_purged_at is None:
            try:
                root_job.output_zip.delete(save=False)
            except Exception:
                logger.exception(
                    "cascade_purge.root_file_delete_failed",
                    extra={"job_id": str(root_job.id)},
                )
            purged_outputs += 1
        root_job.delete()
        deleted_rows += 1
        logger.info(
            "cascade_purge.root_removed",
            extra={"root_job_id": str(root_job.id), "actor_id": actor_id},
        )

    return {
        "root_job_id": str(root_job.id),
        "deleted_rows": deleted_rows,
        "purged_outputs": purged_outputs,
        "actor_id": actor_id,
    }
