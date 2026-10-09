"""Persistence around the data checks: the previous-valuation baseline (check 1.2) and the
company's explanations. ORM-side companion to the pure ``data_checks`` module."""

from __future__ import annotations

from typing import Any

from processing.models import DataCheckExplanation, Module1Job

#: The evidence workbook's name inside a reserving run's output.
DATA_CHECKS_FILENAME = "Data_Checks.xlsx"

#: How many earlier runs to scan for one that recorded an input schema.
_BASELINE_SCAN = 25


def _prior_summary_runs(organization, *, before=None, exclude_job_id=None):
    qs = Module1Job.objects.filter(
        organization=organization,
        job_type=Module1Job.JobType.SUMMARY,
        status=Module1Job.Status.SUCCESS,
    )
    if before is not None:
        qs = qs.filter(created_at__lt=before)
    if exclude_job_id is not None:
        qs = qs.exclude(pk=exclude_job_id)
    return qs.order_by("-created_at")[:_BASELINE_SCAN]


def previous_baseline(organization, *, before=None, exclude_job_id=None) -> dict[str, Any] | None:
    """The most recent earlier successful reserving run that recorded its input schema.

    Returns ``{"job_id", "eop", "input_schema"}`` or None. Runs that predate data checks carry
    no schema and are skipped, so the first run after this ships reports 1.2 as not run rather
    than comparing against nothing.
    """
    if organization is None:
        return None
    for job in _prior_summary_runs(organization, before=before, exclude_job_id=exclude_job_id):
        block = (job.input_meta or {}).get("data_checks") or {}
        schema = block.get("input_schema")
        if schema:
            return {"job_id": str(job.id), "eop": (job.input_meta or {}).get("eop"),
                    "input_schema": schema}
    return None


def explanations_for(job: Module1Job) -> dict[str, dict[str, Any]]:
    return {
        e.check_id: {
            "text": e.text,
            "updated_by": getattr(e.updated_by, "username", None),
            "updated_at": e.updated_at.isoformat(),
        }
        for e in DataCheckExplanation.objects.filter(job=job).select_related("updated_by")
    }


def previous_explanations(job: Module1Job) -> dict[str, dict[str, Any]]:
    """Explanations from the most recent earlier run that has any, keyed by check — offered in
    the app to carry forward, since a company's reasons tend to recur valuation to valuation."""
    for prior in _prior_summary_runs(job.organization, before=job.created_at, exclude_job_id=job.pk):
        found = explanations_for(prior)
        if found:
            return {k: {**v, "job_id": str(prior.id)} for k, v in found.items()}
    return {}
