import logging
import shutil
import tempfile
from pathlib import Path

from celery import shared_task
from django.core.files import File
from django.utils import timezone

from module1_engine import (
    apply_uw_parameters_to_combined_summary,
    run_generate_summary,
    run_policy_level_upr,
    run_update_reserve_summary,
)
from module2_engine import run_module2_allocate, run_module2_movement, run_module2_process
from processing.models import Module1Job
from processing.services.retention import (
    cascade_purge as _cascade_purge,
    purge_expired_outputs as _purge_expired_outputs,
)
from processing.services.watchdog import reap_stuck_jobs as _reap_stuck_jobs
from processing.services.reserve_workbook import (
    write_workbooks_with_overrides,
)
from processing.services.source_resolver import (
    ARTIFACT_COMBINED_SUMMARY,
    list_artifacts_in_zip_path,
    read_artifact_bytes,
    stamp_output_artifacts,
    stamp_retention,
)
from datasets.models import Dataset, DatasetSnapshot
from datasets.services.engine_adapter import (
    KIND_RECIPE,
    write_snapshot_as_sheet,
    write_snapshot_to_folder,
    write_snapshot_to_named_file,
)
from processing.utils import (
    init_job_work_dir,
    job_input_subdir,
    job_output_dir,
    job_root,
)


def _materialize_job_snapshots(job: Module1Job) -> None:
    """For each kind in `input_meta["dataset_snapshots"]`, write each
    referenced snapshot into the engine's staging location, routed by
    the kind's `KIND_RECIPE` (folder / named_file / named_sheet).

    No-op when the job was driven by file uploads.
    """
    meta = job.input_meta or {}
    snaps_by_kind = meta.get("dataset_snapshots") or {}
    if not snaps_by_kind:
        return
    job_root_path = job_root(job)
    for kind, snap_ids in snaps_by_kind.items():
        if not snap_ids:
            continue
        snapshots = list(
            DatasetSnapshot.objects.filter(
                id__in=snap_ids, organization=job.organization
            )
        )
        found = {str(s.id) for s in snapshots}
        missing = [i for i in snap_ids if i not in found]
        if missing:
            raise ValueError(
                f"Dataset snapshots missing for kind '{kind}': {missing}"
            )

        # Newly-defined kinds carry a recipe; legacy kinds (premium /
        # claims_paid / claims_os) hit the default "folder" branch via
        # KIND_RECIPE too.
        recipe = KIND_RECIPE.get(kind)
        if recipe is None:
            # Fall back to the old behavior for unknown kinds: dump into
            # `in/{kind}/`. Keeps the code resilient to schema drift.
            folder = job_input_subdir(job, kind)
            for snap in snapshots:
                write_snapshot_to_folder(snap, folder)
            continue

        mode = recipe["mode"]
        if mode == "folder":
            folder = job_input_subdir(job, kind)
            for snap in snapshots:
                write_snapshot_to_folder(snap, folder)
        elif mode == "named_file":
            # Module 2 Process: Expense_CF.xlsx at a specific path with a
            # specific sheet name the engine reads by name.
            if kind == Dataset.Kind.EXPENSE_CF:
                target = job_root_path / "in" / "module2" / "expense" / recipe["filename"]
            else:
                target = job_root_path / recipe["filename"]
            # Only one snapshot makes sense for a named_file kind — if
            # the user attaches multiple, the last one wins (matches the
            # "one expense_cf per job" semantics).
            for snap in snapshots:
                write_snapshot_to_named_file(snap, target, sheet_name=recipe["sheet_name"])
        elif mode == "named_sheet":
            # Module 2 Process: Previous_Period.xlsx with two sheets
            # (LIC_BOP, UPR-DAC_BOP) contributed by two different kinds.
            target = job_root_path / "in" / "module2" / "previous" / recipe["filename"]
            for snap in snapshots:
                write_snapshot_as_sheet(snap, target, sheet_name=recipe["sheet_name"])
        else:
            raise ValueError(f"Unknown KIND_RECIPE mode '{mode}' for kind '{kind}'.")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers shared across tasks
# ---------------------------------------------------------------------------


def _normalize_module2_error(exc: Exception) -> str:
    msg = str(exc).strip() or "Module 2 processing failed."
    if "Required sheet" in msg or "missing required columns" in msg:
        return msg
    if "Source job" in msg or "Referenced source job" in msg:
        return msg
    if "Allocate output archive is missing Combined_Summary.xlsx" in msg:
        return "Allocate output is invalid: Combined_Summary.xlsx not found."
    if "Referenced allocate job is not completed" in msg:
        return "Referenced allocate job is not completed successfully."
    return "Module 2 processing failed. Check workbook formats and required sheets."


def _zip_output_dir(out_dir: Path, zip_base: Path) -> Path:
    """Create zip_base.zip from contents of out_dir."""
    base = str(zip_base)
    if base.endswith(".zip"):
        base = base[:-4]
    shutil.make_archive(base, "zip", root_dir=out_dir)
    zip_path = Path(base + ".zip")
    if not zip_path.exists():
        raise RuntimeError("ZIP archive was not created")
    return zip_path


def _finalize_success(job: Module1Job, out_dir: Path, zip_path: Path) -> None:
    """Common success-path: persist the ZIP, stamp artifacts + retention, save.

    Called by every task that runs an engine and produces an output archive.
    """
    with open(zip_path, "rb") as f:
        job.output_zip.save(f"{job.id}.zip", File(f), save=False)
    stamp_output_artifacts(job, list_artifacts_in_zip_path(zip_path))
    job.status = Module1Job.Status.SUCCESS
    job.completed_at = timezone.now()
    job.error_message = ""
    stamp_retention(job)
    job.save(update_fields=[
        "status",
        "completed_at",
        "error_message",
        "output_zip",
        "output_artifacts",
        "retention_until",
    ])


def _mark_failed(job: Module1Job, exc: Exception, *, normalizer=None) -> None:
    msg = (normalizer(exc) if normalizer else str(exc))[:8000]
    job.status = Module1Job.Status.FAILED
    job.completed_at = timezone.now()
    job.error_message = msg
    job.save(update_fields=["status", "completed_at", "error_message"])


def _cleanup_root(job: Module1Job) -> None:
    root = job_root(job)
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)


def _log_extra(job: Module1Job) -> dict:
    return {
        "job_id": str(job.id),
        "job_type": job.job_type,
        "org_id": str(job.organization_id),
        "user_id": job.user_id,
        "source_job_id": str(job.source_job_id) if job.source_job_id else None,
        "source_artifact": job.source_artifact or None,
    }


def _resolve_combined_summary_bytes(job: Module1Job, *, fallback_path: Path | None) -> bytes:
    """Return the Combined_Summary bytes for a job that consumes one.

    Prefers `job.source_job` (chained) over the staged file at `fallback_path`.
    """
    if job.source_job_id:
        return read_artifact_bytes(source_job=job.source_job, artifact=ARTIFACT_COMBINED_SUMMARY)
    if fallback_path is None or not fallback_path.is_file():
        raise ValueError("Combined_Summary.xlsx missing from job staging.")
    return fallback_path.read_bytes()


# ---------------------------------------------------------------------------
# Module 1 tasks
# ---------------------------------------------------------------------------


@shared_task(bind=True, ignore_result=True)
def run_module1_summary_task(self, job_id: str) -> None:
    job = Module1Job.objects.select_related("organization", "source_job").get(pk=job_id)
    job.status = Module1Job.Status.RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])
    logger.info("summary.start", extra=_log_extra(job))

    out_dir = job_output_dir(job)
    try:
        meta = job.input_meta or {}

        # If the user chained an existing Combined_Summary from a previous job,
        # materialize it at the path the engine expects so the output ZIP also
        # contains it (consistent with the upload path).
        if job.source_job_id:
            cs_bytes = read_artifact_bytes(
                source_job=job.source_job, artifact=ARTIFACT_COMBINED_SUMMARY
            )
            (out_dir / "Combined_Summary.xlsx").write_bytes(cs_bytes)

        # Dataset-driven kinds: render each snapshot as an xlsx into the
        # engine's staging folder. File-driven kinds are already populated.
        _materialize_job_snapshots(job)

        run_generate_summary(
            meta["exp_start"],
            meta["exp_end"],
            meta["bop"],
            meta["eop"],
            str(job_input_subdir(job, "premium")),
            str(job_input_subdir(job, "claims_paid")),
            str(job_input_subdir(job, "claims_os")),
            str(out_dir),
        )
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = _zip_output_dir(out_dir, Path(tmp) / "outputs")
            _finalize_success(job, out_dir, zip_path)
        logger.info("summary.success", extra=_log_extra(job))
    except Exception as exc:
        logger.exception("summary.failed", extra=_log_extra(job))
        _mark_failed(job, exc)
    finally:
        _cleanup_root(job)


@shared_task(bind=True, ignore_result=True)
def run_module1_policy_upr_task(self, job_id: str) -> None:
    job = Module1Job.objects.select_related("organization").get(pk=job_id)
    job.status = Module1Job.Status.RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])
    logger.info("policy_upr.start", extra=_log_extra(job))

    out_dir = job_output_dir(job)
    try:
        meta = job.input_meta or {}
        _materialize_job_snapshots(job)
        run_policy_level_upr(
            meta["bop"],
            meta["eop"],
            str(job_input_subdir(job, "premium")),
            str(out_dir),
        )
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = _zip_output_dir(out_dir, Path(tmp) / "outputs")
            _finalize_success(job, out_dir, zip_path)
        logger.info("policy_upr.success", extra=_log_extra(job))
    except Exception as exc:
        logger.exception("policy_upr.failed", extra=_log_extra(job))
        _mark_failed(job, exc)
    finally:
        _cleanup_root(job)


@shared_task(bind=True, ignore_result=True)
def run_module1_update_reserve_task(self, job_id: str) -> None:
    job = Module1Job.objects.select_related("organization", "source_job").get(pk=job_id)
    job.status = Module1Job.Status.RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])
    logger.info("update_reserve.start", extra=_log_extra(job))

    staging = job_root(job) / "in" / "reserve_batch"
    staging.mkdir(parents=True, exist_ok=True)
    out_dir = job_output_dir(job)
    try:
        meta = job.input_meta or {}
        cdf_overrides = meta.get("cdf_overrides")

        # Excel-free path: when the user submitted CDF overrides instead
        # of uploaded reserve files, materialize the source Summary
        # job's reserve workbooks (with overrides applied to Selected
        # CDF rows) into the engine's staging dir.
        if cdf_overrides is not None:
            if not job.source_job_id:
                raise ValueError(
                    "Update reserve with cdf_overrides requires a source job."
                )
            write_workbooks_with_overrides(
                source_job=job.source_job,
                overrides_by_filename=cdf_overrides,
                dest_folder=staging,
            )

        # If chained, drop the source Combined_Summary into staging so the
        # engine sees it the same way it would have seen an uploaded file.
        if job.source_job_id:
            cs_bytes = read_artifact_bytes(
                source_job=job.source_job, artifact=ARTIFACT_COMBINED_SUMMARY
            )
            (staging / "Combined_Summary.xlsx").write_bytes(cs_bytes)

        run_update_reserve_summary(str(staging))
        shutil.copytree(staging, out_dir, dirs_exist_ok=True)

        with tempfile.TemporaryDirectory() as tmp:
            zip_path = _zip_output_dir(out_dir, Path(tmp) / "outputs")
            _finalize_success(job, out_dir, zip_path)
        logger.info("update_reserve.success", extra=_log_extra(job))
    except Exception as exc:
        logger.exception("update_reserve.failed", extra=_log_extra(job))
        _mark_failed(job, exc)
    finally:
        _cleanup_root(job)


@shared_task(bind=True, ignore_result=True)
def run_module1_uw_parameters_task(self, job_id: str) -> None:
    job = Module1Job.objects.select_related("organization", "source_job").get(pk=job_id)
    job.status = Module1Job.Status.RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])
    logger.info("uw_parameters.start", extra=_log_extra(job))

    out_dir = job_output_dir(job)
    combined_path = job_root(job) / "in" / "combined" / "Combined_Summary.xlsx"
    try:
        cs_bytes = _resolve_combined_summary_bytes(job, fallback_path=combined_path)
        meta = job.input_meta or {}
        payload = meta.get("payload") or {}

        # Write to a temp path so apply_uw_parameters_to_combined_summary
        # can still take a path argument (its existing signature).
        if not combined_path.exists():
            combined_path.parent.mkdir(parents=True, exist_ok=True)
            combined_path.write_bytes(cs_bytes)

        out_bytes = apply_uw_parameters_to_combined_summary(
            str(combined_path), payload
        )
        out_file = out_dir / "Combined_Summary.xlsx"
        out_file.write_bytes(out_bytes)

        with tempfile.TemporaryDirectory() as tmp:
            zip_path = _zip_output_dir(out_dir, Path(tmp) / "outputs")
            _finalize_success(job, out_dir, zip_path)
        logger.info("uw_parameters.success", extra=_log_extra(job))
    except Exception as exc:
        logger.exception("uw_parameters.failed", extra=_log_extra(job))
        _mark_failed(job, exc)
    finally:
        _cleanup_root(job)


# ---------------------------------------------------------------------------
# Module 2 tasks
# ---------------------------------------------------------------------------


@shared_task(bind=True, ignore_result=True)
def run_module2_allocate_task(self, job_id: str) -> None:
    job = Module1Job.objects.select_related("organization", "source_job").get(pk=job_id)
    job.status = Module1Job.Status.RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])
    logger.info("module2_allocate.start", extra=_log_extra(job))

    out_dir = job_output_dir(job)
    fallback = job_root(job) / "in" / "module2" / "combined" / "Combined_Summary.xlsx"
    try:
        combined_bytes = _resolve_combined_summary_bytes(job, fallback_path=fallback)
        result = run_module2_allocate(combined_bytes)
        (out_dir / "Module2_Allocate_Output.xlsx").write_bytes(result["workbook_bytes"])
        (out_dir / "Combined_Summary.xlsx").write_bytes(combined_bytes)

        meta = job.input_meta or {}
        meta["ulr_rows"] = result["ulr_rows"]
        job.input_meta = meta

        with tempfile.TemporaryDirectory() as tmp:
            zip_path = _zip_output_dir(out_dir, Path(tmp) / "outputs")
            # Save ZIP + stamp artifacts + retention via the shared finalizer,
            # but also persist input_meta atomically.
            with open(zip_path, "rb") as f:
                job.output_zip.save(f"{job.id}.zip", File(f), save=False)
            stamp_output_artifacts(job, list_artifacts_in_zip_path(zip_path))
            job.status = Module1Job.Status.SUCCESS
            job.completed_at = timezone.now()
            job.error_message = ""
            stamp_retention(job)
            job.save(update_fields=[
                "status",
                "completed_at",
                "error_message",
                "output_zip",
                "output_artifacts",
                "retention_until",
                "input_meta",
            ])
        logger.info("module2_allocate.success", extra=_log_extra(job))
    except Exception as exc:
        logger.exception("module2_allocate.failed", extra=_log_extra(job))
        _mark_failed(job, exc, normalizer=_normalize_module2_error)
    finally:
        _cleanup_root(job)


@shared_task(bind=True, ignore_result=True)
def run_module2_process_task(self, job_id: str) -> None:
    job = Module1Job.objects.select_related("organization", "source_job").get(pk=job_id)
    job.status = Module1Job.Status.RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])
    logger.info("module2_process.start", extra=_log_extra(job))

    out_dir = job_output_dir(job)
    previous_path = job_root(job) / "in" / "module2" / "previous" / "Previous_Period.xlsx"
    expense_path = job_root(job) / "in" / "module2" / "expense" / "Expense_CF.xlsx"
    try:
        meta = job.input_meta or {}
        selected_ulr = meta.get("selected_ulr") or []
        accounting_period = int(meta.get("accounting_period"))

        # Dataset-driven kinds (Expense CF, Previous Period LIC/UPR):
        # materialize the user's snapshots into the engine's specific
        # named paths via KIND_RECIPE. File-upload kinds are already on
        # disk from the view.
        _materialize_job_snapshots(job)

        # The process task is always chained off an allocate job; the view
        # sets source_job to the allocate. Read Combined_Summary out of that
        # ZIP via the resolver.
        if not job.source_job_id:
            raise ValueError("Module 2 process requires a source allocate job.")
        combined_bytes = read_artifact_bytes(
            source_job=job.source_job, artifact=ARTIFACT_COMBINED_SUMMARY
        )

        final_bytes = run_module2_process(
            combined_bytes,
            previous_path.read_bytes(),
            expense_path.read_bytes(),
            accounting_period,
            selected_ulr,
        )
        (out_dir / "Module2_Final_Output.xlsx").write_bytes(final_bytes)

        with tempfile.TemporaryDirectory() as tmp:
            zip_path = _zip_output_dir(out_dir, Path(tmp) / "outputs")
            _finalize_success(job, out_dir, zip_path)
        logger.info("module2_process.success", extra=_log_extra(job))
    except Exception as exc:
        logger.exception("module2_process.failed", extra=_log_extra(job))
        _mark_failed(job, exc, normalizer=_normalize_module2_error)
    finally:
        _cleanup_root(job)


@shared_task(bind=True, ignore_result=True)
def run_module2_movement_task(self, job_id: str) -> None:
    """IFRS 17 movement-analysis disclosure. Same inputs as module2_process
    (chained off an allocate job for Combined_Summary, plus Previous_Period +
    Expense_CF), projected into the per-(class, UWY) SAMA Gross + RI tables.
    """
    job = Module1Job.objects.select_related("organization", "source_job").get(pk=job_id)
    job.status = Module1Job.Status.RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])
    logger.info("module2_movement.start", extra=_log_extra(job))

    out_dir = job_output_dir(job)
    previous_path = job_root(job) / "in" / "module2" / "previous" / "Previous_Period.xlsx"
    expense_path = job_root(job) / "in" / "module2" / "expense" / "Expense_CF.xlsx"
    try:
        meta = job.input_meta or {}
        selected_ulr = meta.get("selected_ulr") or []
        accounting_period = int(meta.get("accounting_period"))
        reporting_date = meta.get("reporting_date")
        scope = meta.get("scope") or {}

        _materialize_job_snapshots(job)
        if not job.source_job_id:
            raise ValueError("Module 2 movement requires a source allocate job.")
        combined_bytes = read_artifact_bytes(
            source_job=job.source_job, artifact=ARTIFACT_COMBINED_SUMMARY
        )

        xlsx, result = run_module2_movement(
            combined_bytes,
            previous_path.read_bytes(),
            expense_path.read_bytes(),
            accounting_period,
            selected_ulr,
            reporting_date=reporting_date,
            classes=scope.get("reserving_classes") or None,
            uwys=scope.get("uwys") or None,
        )
        (out_dir / "IFRS17_Movement_Analysis.xlsx").write_bytes(xlsx)
        # Record non-fatal projection diagnostics (missing-column gaps) on the job
        # so the UI can surface them without cracking the workbook.
        if result.missing_columns:
            meta["movement_warnings"] = {
                "missing_columns": sorted(result.missing_columns),
                "pairs": len(result.pairs),
            }
            job.input_meta = meta
            job.save(update_fields=["input_meta"])

        with tempfile.TemporaryDirectory() as tmp:
            zip_path = _zip_output_dir(out_dir, Path(tmp) / "outputs")
            _finalize_success(job, out_dir, zip_path)
        logger.info("module2_movement.success", extra=_log_extra(job))
    except Exception as exc:
        logger.exception("module2_movement.failed", extra=_log_extra(job))
        _mark_failed(job, exc, normalizer=_normalize_module2_error)
    finally:
        _cleanup_root(job)


# ---------------------------------------------------------------------------
# Retention tasks
# ---------------------------------------------------------------------------


@shared_task(bind=True, ignore_result=True)
def purge_expired_outputs_task(self, batch_size: int = 500) -> dict:
    """Beat-scheduled daily. Idempotent. Bounded by batch_size per call."""
    result = _purge_expired_outputs(batch_size=batch_size)
    logger.info("retention.sweep_complete", extra=result)
    return result


@shared_task(bind=True, ignore_result=True)
def reap_stuck_jobs_task(self, batch_size: int = 500) -> dict:
    """Beat-scheduled watchdog. Fails jobs stuck in running/pending past the
    Celery hard time limit (+ margin). Idempotent; bounded by batch_size."""
    result = _reap_stuck_jobs(batch_size=batch_size)
    if result["reaped_running"] or result["reaped_pending"]:
        logger.warning("watchdog.reap_complete", extra=result)
    else:
        logger.info("watchdog.reap_complete", extra=result)
    return result


@shared_task(bind=True, ignore_result=True)
def cascade_purge_task(self, root_job_id: str, actor_user_id: int | None = None,
                       force: bool = False) -> dict:
    """Admin-triggered. Removes a job and its descendant subtree."""
    from django.contrib.auth import get_user_model

    actor = None
    if actor_user_id:
        actor = get_user_model().objects.filter(pk=actor_user_id).first()
    root = Module1Job.objects.get(pk=root_job_id)
    result = _cascade_purge(root, actor=actor, force=force)
    logger.info("retention.cascade_purge_complete", extra=result)
    return result
