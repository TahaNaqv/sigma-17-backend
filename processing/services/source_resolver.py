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

from django.db.models import Q, QuerySet
from rest_framework.exceptions import ValidationError

from processing.models import Module1Job
from tenants.permissions import get_request_org

logger = logging.getLogger(__name__)


ARTIFACT_COMBINED_SUMMARY = "Combined_Summary.xlsx"
ARTIFACT_MODULE2_FINAL = "Module2_Final_Output.xlsx"


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
# Consumer fitness: which SHEETS a consumer needs inside the artifact
# ---------------------------------------------------------------------------
#
# Producing `Combined_Summary.xlsx` and producing one a given engine can READ
# are different claims. Module 1 emits the file on every summary / update-reserve
# run, but never writes the `ULAE-RA` and `Discount Rate` sheets — those are
# actuary judgement, added to the workbook by hand. So every Module 1 job was
# offered as a Cash Flow Allocation source and every one of them failed several
# minutes into a Celery run with "Required sheet 'ULAE-RA' is missing".
#
# Requirements are keyed by CONSUMER, not by artifact: the Module 1 consumers
# (Summary appending to an existing book, Update Reserves, Triangles) legitimately
# accept a partial workbook, so they declare nothing here and are unaffected.
CONSUMER_REQUIRED_SHEETS: dict[str, tuple[str, ...]] = {}


def _register_consumer_requirements() -> None:
    """Populate CONSUMER_REQUIRED_SHEETS from the engines themselves.

    Imported lazily and defensively: this module is imported by views and by
    Celery tasks, and the engine module pulls in pandas. A failure to import it
    must degrade to "no sheet requirement known" (every job stays offerable,
    i.e. today's behaviour) rather than break chaining entirely.
    """
    try:
        from module2_engine.engine import ALLOCATE_REQUIRED_SHEETS
    except Exception:  # pragma: no cover - defensive
        logger.warning("source_resolver: could not load Module 2 sheet requirements")
        return
    # Allocate and sensitivity both run `_compute_allocate_frames` over the
    # workbook, so they need exactly the same sheets.
    CONSUMER_REQUIRED_SHEETS[Module1Job.JobType.MODULE2_ALLOCATE] = ALLOCATE_REQUIRED_SHEETS
    CONSUMER_REQUIRED_SHEETS[Module1Job.JobType.MODULE2_SENSITIVITY] = ALLOCATE_REQUIRED_SHEETS


_register_consumer_requirements()


def required_sheets_for_consumer(consumer: str | None) -> tuple[str, ...]:
    """Sheets `consumer` needs inside the artifact. Empty tuple = no requirement."""
    if not consumer:
        return ()
    return CONSUMER_REQUIRED_SHEETS.get(consumer, ())


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
    consumer: str | None = None,
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
      - not KNOWN to be missing a sheet `consumer` requires

    The sheet filter is deliberately one-sided. Rows whose sheet index says they
    lack a required sheet are excluded in SQL; rows with no index yet are kept,
    because "we have not looked" must not read as "unusable" — that would empty
    the picker the moment this shipped, before any backfill ran. The caller
    annotates the returned page via `missing_sheets_for`, which fills the index
    lazily, so an un-indexed bad row shows once (labelled with its missing
    sheets) and is filtered out of every later query.
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

    required = required_sheets_for_consumer(consumer)
    if required:
        base = base.filter(
            Q(output_sheets__contains={artifact: list(required)})
            | ~Q(output_sheets__has_key=artifact)
        )

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
    consumer: str | None = None,
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

    # Fitness, not just presence. Without this the run is accepted, queued, and
    # fails minutes later inside Celery with a message the user cannot connect
    # back to the job they picked. Rejecting at submit costs one ZIP read and
    # names the exact sheets to add.
    missing = missing_sheets_for(job, artifact, consumer)
    if missing:
        raise ValidationError(
            {
                field_name: (
                    f"That job's {artifact} is missing the "
                    f"{'sheet' if len(missing) == 1 else 'sheets'} "
                    f"{', '.join(missing)}. Download the workbook, add "
                    f"{'it' if len(missing) == 1 else 'them'}, then upload it here."
                )
            }
        )

    return job


# ---------------------------------------------------------------------------
# Task-layer reads + success-stamping
# ---------------------------------------------------------------------------


def read_zip_member(zip_field_file, member: str, *, label: str = "archive") -> bytes:
    """Read `member` out of an arbitrary FileField-backed ZIP.

    Shared by `read_artifact_bytes` (output_zip) and input-archive reuse
    (input_archive). Raises ValueError on any failure; the caller (a Celery
    task) normalizes the message and marks its own job FAILED. `label` is woven
    into error messages so the user sees "output"/"input archive" appropriately.
    """
    if not zip_field_file:
        raise ValueError(f"Referenced source job has no {label}.")
    try:
        with zip_field_file.open("rb") as zf_stream:
            raw = zf_stream.read()
    except FileNotFoundError as exc:
        raise ValueError(f"Referenced source job {label} is missing on disk.") from exc

    with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
        if member not in zf.namelist():
            raise ValueError(
                f"Referenced source job {label} does not contain {member}."
            )
        return zf.read(member)


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
    return read_zip_member(source_job.output_zip, artifact, label="output")


def read_input_archive_bytes(*, source_job: Module1Job, member: str) -> bytes:
    """Read `member` from `source_job.input_archive` (the durable input archive).

    Used when a downstream job reuses another job's already-captured inputs
    (e.g. a movement analysis chained off a process job). Applies the same
    success/expiry guards as `read_artifact_bytes` so a failed or purged upstream
    job reports a clear reason.
    """
    if source_job is None:
        raise ValueError("Source job is missing.")
    if source_job.status != Module1Job.Status.SUCCESS:
        raise ValueError("Referenced source job is not successful.")
    if source_job.output_purged_at is not None:
        raise ValueError(
            "Referenced source job inputs have expired and are no longer available."
        )
    return read_zip_member(source_job.input_archive, member, label="input archive")


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


# Only artifacts that can be CHAINED get a sheet index. A summary run's ZIP holds
# a reserve workbook per (class, head of damage, gross/RI) — dozens of files no
# one can chain from — and indexing those would turn every success into dozens of
# pointless ZIP reads.
INDEXABLE_ARTIFACTS: tuple[str, ...] = (ARTIFACT_COMBINED_SUMMARY,)


def sheet_index_from_zip_path(zip_path) -> dict[str, list[str]]:
    """Build the {artifact: [sheets]} index for a freshly-written output ZIP."""
    from processing.services.workbook_probe import sheet_names_from_xlsx_bytes

    index: dict[str, list[str]] = {}
    try:
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            members = set(zf.namelist())
            for artifact in INDEXABLE_ARTIFACTS:
                if artifact not in members:
                    continue
                sheets = sheet_names_from_xlsx_bytes(zf.read(artifact))
                if sheets:
                    index[artifact] = sheets
    except (FileNotFoundError, zipfile.BadZipFile, OSError, KeyError):
        # Never fail a successful job over its own index; an absent key just
        # means "unknown", which every consumer already tolerates.
        logger.warning("sheet_index: unreadable output archive", exc_info=True)
    return index


def stamp_output_sheets(job: Module1Job, index: dict[str, list[str]]) -> None:
    """Persist the denormalized sheet index on a job (caller saves). Idempotent."""
    job.output_sheets = {k: list(v) for k, v in (index or {}).items() if k and v}


def sheets_for_artifact(
    job: Module1Job, artifact: str, *, allow_fill: bool = True
) -> list[str] | None:
    """Sheets in `job`'s copy of `artifact`, or None if genuinely unknown.

    Reads the denormalized index first. A job stamped before `output_sheets`
    existed has no entry, so the index is filled lazily from the ZIP and
    persisted — the candidate list self-heals as it is paged through, and a
    deploy needs no migration-ordering dance to be correct. `None` (unreadable
    or purged output, or `allow_fill=False`) means unknown, never "unusable".

    `allow_fill=False` answers from the index alone, for callers that need to
    bound how many archives one request may crack open.
    """
    stored = (job.output_sheets or {}).get(artifact)
    if stored:
        return list(stored)
    if not allow_fill:
        return None
    if not job.output_available:
        return None
    sheets = list_sheets_in_zip(job.output_zip, artifact)
    if not sheets:
        return None
    # Persist just this column; the job row is not otherwise being written here.
    merged = dict(job.output_sheets or {})
    merged[artifact] = sheets
    job.output_sheets = merged
    try:
        job.save(update_fields=["output_sheets"])
    except Exception:  # pragma: no cover - a cache fill must never break a read
        logger.warning("sheet_index: lazy fill failed for %s", job.pk, exc_info=True)
    return sheets


def list_sheets_in_zip(zip_field_file, artifact: str) -> list[str]:
    """Sheet names of `artifact` inside a FileField-backed output ZIP. [] on any error."""
    from processing.services.workbook_probe import sheet_names_from_xlsx_bytes

    if not zip_field_file:
        return []
    try:
        with zip_field_file.open("rb") as f:
            raw = f.read()
        with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
            if artifact not in zf.namelist():
                return []
            return sheet_names_from_xlsx_bytes(zf.read(artifact))
    except (FileNotFoundError, zipfile.BadZipFile, OSError, KeyError):
        return []


def missing_sheets_for(
    job: Module1Job, artifact: str, consumer: str | None, *, allow_fill: bool = True
) -> list[str]:
    """Required sheets `consumer` needs that `job`'s artifact does not have.

    `[]` means usable — INCLUDING when the sheet list is unknown. Refusing a job
    we simply failed to index would take a working source away from the user on
    the strength of a read error, which is the worse failure of the two.
    """
    required = required_sheets_for_consumer(consumer)
    if not required:
        return []
    from processing.services.workbook_probe import missing_sheets

    present = sheets_for_artifact(job, artifact, allow_fill=allow_fill)
    if present is None:
        return []
    return missing_sheets(present, required)


# How many un-indexed archives one candidate-list request may crack open. The
# picker pages 10 at a time, so this is never reached in the UI; it exists so a
# caller asking for page_size=100 against a wholly un-indexed table cannot turn
# one request into 100 ZIP reads. Rows past the budget report as unknown (i.e.
# still offered) and get indexed by a later page view or `backfill_output_sheets`.
LAZY_SHEET_FILL_BUDGET = 20


def annotate_missing_sheets(
    jobs, artifact: str, consumer: str | None
) -> dict:
    """{job_id: [missing sheets]} for one page of candidates.

    Indexes at most `LAZY_SHEET_FILL_BUDGET` un-indexed jobs per call, spending
    the budget on the jobs that need it and answering the rest from the index.
    """
    if not required_sheets_for_consumer(consumer):
        return {job.id: [] for job in jobs}

    budget = LAZY_SHEET_FILL_BUDGET
    out: dict = {}
    for job in jobs:
        indexed = bool((job.output_sheets or {}).get(artifact))
        allow_fill = indexed or budget > 0
        if not indexed and allow_fill:
            budget -= 1
        out[job.id] = missing_sheets_for(
            job, artifact, consumer, allow_fill=allow_fill
        )
    return out


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
