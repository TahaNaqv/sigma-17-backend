"""Single-source-of-truth chaining primitive.

Every endpoint that can consume the output of an earlier job (Module 1 -> Module 2,
Module 1 -> Module 1) routes through this module. There is exactly one
implementation of "is this source job usable" and "read this artifact from a
source job's ZIP" — view-layer and task-layer callers both go through here.

Public API:

- ARTIFACT_COMBINED_SUMMARY           the bridge artifact name
- ARTIFACT_PRODUCERS                  artifact -> set of job_types that produce it
- list_candidate_sources(...)         org+owner-scoped queryset of eligible sources
- resolve_source_job(...)             view-layer validation entrypoint
- read_artifact_bytes(...)            task-layer artifact read
- artifact_exists_in_zip(...)         central-directory probe (used at success-stamp time)
"""

from __future__ import annotations

import io
import logging
import zipfile
from typing import Iterable
from uuid import UUID

from django.db.models import QuerySet
from rest_framework.exceptions import ValidationError

from processing.models import Module1Job
from tenants.permissions import get_request_org

logger = logging.getLogger(__name__)


ARTIFACT_COMBINED_SUMMARY = "Combined_Summary.xlsx"


# Whitelist of producer types per artifact. Final eligibility is always
# confirmed against the row's output_artifacts (denormalized at success time),
# so this map is purely for query narrowing.
ARTIFACT_PRODUCERS: dict[str, set[str]] = {
    ARTIFACT_COMBINED_SUMMARY: {
        Module1Job.JobType.SUMMARY,
        Module1Job.JobType.UW_PARAMETERS,
        Module1Job.JobType.UPDATE_RESERVE,
        Module1Job.JobType.MODULE2_ALLOCATE,
    },
}


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def _producer_types_for(artifact: str) -> set[str]:
    if artifact not in ARTIFACT_PRODUCERS:
        raise ValidationError({"artifact": f"Unknown artifact '{artifact}'."})
    return ARTIFACT_PRODUCERS[artifact]


def _scope_for_user(request) -> QuerySet:
    """Org+owner scope mirroring processing.views._scope_jobs_qs but without
    requiring the caller to import view internals.

    Superusers see all orgs (or the org they've selected). Non-superusers see
    only their own jobs within their active org.
    """
    user = request.user
    org = get_request_org(request)
    qs = Module1Job.objects.all()
    if user.is_superuser:
        if org is not None:
            qs = qs.filter(organization=org)
        return qs
    if org is None:
        return Module1Job.objects.none()
    return qs.filter(organization=org, user=user)


def list_candidate_sources(
    *,
    request,
    artifact: str,
    job_type: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[QuerySet, int]:
    """Return (page_queryset, total_count) of jobs eligible to source `artifact`.

    Eligibility (cheap, indexed):
      - in producer set for the artifact (optionally narrowed by `job_type`)
      - status = SUCCESS
      - output_zip set (truthy)
      - output_purged_at is NULL
      - artifact in output_artifacts (JSON contains)
    """
    producers = _producer_types_for(artifact)
    if job_type:
        if job_type not in producers:
            return Module1Job.objects.none(), 0
        producers = {job_type}

    base = _scope_for_user(request).filter(
        job_type__in=producers,
        status=Module1Job.Status.SUCCESS,
        output_purged_at__isnull=True,
    ).exclude(output_zip="").exclude(output_zip__isnull=True)

    # JSON contains: requires Postgres JSONB. We use `__contains` which works
    # for JSONField on Postgres for list containment.
    base = base.filter(output_artifacts__contains=[artifact])

    total = base.count()
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 20
    if page_size > 100:
        page_size = 100
    start = (page - 1) * page_size
    end = start + page_size
    return base.order_by("-completed_at", "-created_at")[start:end], total


# ---------------------------------------------------------------------------
# View-layer validation
# ---------------------------------------------------------------------------


def _coerce_uuid(value, *, field: str) -> UUID:
    if isinstance(value, UUID):
        return value
    if value is None or str(value).strip() == "":
        raise ValidationError({field: "This field is required."})
    try:
        return UUID(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValidationError({field: "Must be a UUID."}) from exc


def resolve_source_job(
    *,
    request,
    source_job_id,
    artifact: str,
    field_name: str = "source_job_id",
) -> Module1Job:
    """Validate and return a usable source job.

    Raises rest_framework.exceptions.ValidationError(400) with a uniform
    error shape on any failure. The 404 vs 400 distinction is deliberately
    collapsed to 400 here so the error payload always lives under the
    field that the client supplied (better UX, no info leak about other
    orgs' rows).
    """
    uid = _coerce_uuid(source_job_id, field=field_name)

    scope = _scope_for_user(request)
    job = scope.filter(pk=uid).first()
    if job is None:
        # Uniform message — do not reveal whether the UUID exists in another
        # org or under another user.
        raise ValidationError(
            {field_name: "Source job not found."}
        )

    if job.status != Module1Job.Status.SUCCESS:
        raise ValidationError(
            {field_name: "Source job has not completed successfully."}
        )

    if not job.output_zip:
        raise ValidationError(
            {field_name: "Source job has no downloadable output."}
        )

    if job.output_purged_at is not None:
        raise ValidationError(
            {
                field_name: (
                    "Source job output has expired and is no longer available."
                )
            }
        )

    if artifact not in (job.output_artifacts or []):
        raise ValidationError(
            {
                field_name: (
                    f"Source job output does not contain {artifact}."
                )
            }
        )

    return job


# ---------------------------------------------------------------------------
# Task-layer reads + success-stamping
# ---------------------------------------------------------------------------


def read_artifact_bytes(*, source_job: Module1Job, artifact: str) -> bytes:
    """Read `artifact` out of `source_job.output_zip`.

    Raises ValueError on any failure; the caller (a Celery task) normalizes
    the message and marks its own job FAILED.
    """
    if source_job is None:
        raise ValueError("Source job is missing.")
    if source_job.status != Module1Job.Status.SUCCESS:
        raise ValueError("Referenced source job is not successful.")
    if source_job.output_purged_at is not None:
        raise ValueError(
            "Referenced source job output has expired and is no longer available."
        )
    if not source_job.output_zip:
        raise ValueError("Referenced source job has no downloadable output.")

    try:
        with source_job.output_zip.open("rb") as zf_stream:
            raw = zf_stream.read()
    except FileNotFoundError as exc:
        raise ValueError("Referenced source job output is missing on disk.") from exc

    with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
        if artifact not in zf.namelist():
            raise ValueError(
                f"Referenced source job output does not contain {artifact}."
            )
        return zf.read(artifact)


def artifact_exists_in_zip(zip_field_file, artifact: str) -> bool:
    """Probe a ZIP's central directory for `artifact`. Returns False on any
    error (missing file, corrupt ZIP). Used at success-stamp time."""
    if not zip_field_file:
        return False
    try:
        with zip_field_file.open("rb") as f:
            raw = f.read()
        with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
            return artifact in zf.namelist()
    except (FileNotFoundError, zipfile.BadZipFile, OSError):
        return False


def list_artifacts_in_zip(zip_field_file) -> list[str]:
    """Return the list of file basenames (non-directory entries) in a ZIP.

    Used by the backfill management command and by tasks that want to stamp
    `output_artifacts` from a freshly-written ZIP.
    """
    if not zip_field_file:
        return []
    try:
        with zip_field_file.open("rb") as f:
            raw = f.read()
        with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
            return [
                info.filename
                for info in zf.infolist()
                if not info.is_dir()
            ]
    except (FileNotFoundError, zipfile.BadZipFile, OSError):
        return []


def list_artifacts_in_zip_path(zip_path) -> list[str]:
    """Same as list_artifacts_in_zip but takes a filesystem path. Used by
    tasks before the FileField has been assigned."""
    try:
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            return [
                info.filename
                for info in zf.infolist()
                if not info.is_dir()
            ]
    except (FileNotFoundError, zipfile.BadZipFile, OSError):
        return []


# ---------------------------------------------------------------------------
# Success-stamping helpers (used by every task on the success path)
# ---------------------------------------------------------------------------


def stamp_output_artifacts(job: Module1Job, artifacts: Iterable[str]) -> None:
    """Persist the denormalized artifact list on a job. Idempotent."""
    job.output_artifacts = sorted(set(a for a in artifacts if a))


def compute_retention_until(job: Module1Job):
    """Return the timezone-aware datetime when this job's output becomes
    eligible for cleanup, or None if the org has no retention policy.
    """
    from django.utils import timezone
    from datetime import timedelta

    org = job.organization
    days = getattr(org, "default_output_retention_days", None)
    if not days:
        return None
    return timezone.now() + timedelta(days=int(days))


def stamp_retention(job: Module1Job) -> None:
    """Stamp retention_until on the in-memory job (caller is responsible for save).

    Only set on the success path; failed jobs keep retention_until=None so
    they're retained for forensics until manually purged.
    """
    job.retention_until = compute_retention_until(job)
