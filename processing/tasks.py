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
from processing.models import Module1Job
from processing.utils import init_job_work_dir, job_input_subdir, job_output_dir, job_root

logger = logging.getLogger(__name__)


def _zip_output_dir(out_dir: Path, zip_base: Path) -> Path:
    """Create zip_base.zip from contents of out_dir (zip_base without .zip suffix)."""
    base = str(zip_base)
    if base.endswith(".zip"):
        base = base[:-4]
    shutil.make_archive(base, "zip", root_dir=out_dir)
    zip_path = Path(base + ".zip")
    if not zip_path.exists():
        raise RuntimeError("ZIP archive was not created")
    return zip_path


@shared_task(bind=True, ignore_result=True)
def run_module1_summary_task(self, job_id: str) -> None:
    job = Module1Job.objects.get(pk=job_id)
    job.status = Module1Job.Status.RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])
    out_dir = job_output_dir(job)
    try:
        meta = job.input_meta or {}
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
            zip_base = Path(tmp) / "outputs"
            zip_path = _zip_output_dir(out_dir, zip_base)
            with open(zip_path, "rb") as f:
                job.output_zip.save(f"{job.id}.zip", File(f), save=False)
        job.status = Module1Job.Status.SUCCESS
        job.completed_at = timezone.now()
        job.error_message = ""
        job.save(update_fields=["status", "completed_at", "error_message", "output_zip"])
    except Exception as exc:
        logger.exception("Module1 summary job %s failed", job_id)
        job.status = Module1Job.Status.FAILED
        job.completed_at = timezone.now()
        job.error_message = str(exc)[:8000]
        job.save(update_fields=["status", "completed_at", "error_message"])
    finally:
        root = job_root(job)
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)


@shared_task(bind=True, ignore_result=True)
def run_module1_policy_upr_task(self, job_id: str) -> None:
    job = Module1Job.objects.get(pk=job_id)
    job.status = Module1Job.Status.RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])
    out_dir = job_output_dir(job)
    try:
        meta = job.input_meta or {}
        run_policy_level_upr(
            meta["bop"],
            meta["eop"],
            str(job_input_subdir(job, "premium")),
            str(out_dir),
        )
        with tempfile.TemporaryDirectory() as tmp:
            zip_base = Path(tmp) / "outputs"
            zip_path = _zip_output_dir(out_dir, zip_base)
            with open(zip_path, "rb") as f:
                job.output_zip.save(f"{job.id}.zip", File(f), save=False)
        job.status = Module1Job.Status.SUCCESS
        job.completed_at = timezone.now()
        job.error_message = ""
        job.save(update_fields=["status", "completed_at", "error_message", "output_zip"])
    except Exception as exc:
        logger.exception("Module1 policy UPR job %s failed", job_id)
        job.status = Module1Job.Status.FAILED
        job.completed_at = timezone.now()
        job.error_message = str(exc)[:8000]
        job.save(update_fields=["status", "completed_at", "error_message"])
    finally:
        root = job_root(job)
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)


@shared_task(bind=True, ignore_result=True)
def run_module1_update_reserve_task(self, job_id: str) -> None:
    job = Module1Job.objects.get(pk=job_id)
    job.status = Module1Job.Status.RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])
    staging = job_root(job) / "in" / "reserve_batch"
    staging.mkdir(parents=True, exist_ok=True)
    out_dir = job_output_dir(job)
    try:
        # Files were saved flat into staging; mirror desktop: all xlsx in one folder
        run_update_reserve_summary(str(staging))
        # Desktop writes in-place; after run, copy staging tree to out for download
        shutil.copytree(staging, out_dir, dirs_exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            zip_base = Path(tmp) / "outputs"
            zip_path = _zip_output_dir(out_dir, zip_base)
            with open(zip_path, "rb") as f:
                job.output_zip.save(f"{job.id}.zip", File(f), save=False)
        job.status = Module1Job.Status.SUCCESS
        job.completed_at = timezone.now()
        job.error_message = ""
        job.save(update_fields=["status", "completed_at", "error_message", "output_zip"])
    except Exception as exc:
        logger.exception("Module1 update reserve job %s failed", job_id)
        job.status = Module1Job.Status.FAILED
        job.completed_at = timezone.now()
        job.error_message = str(exc)[:8000]
        job.save(update_fields=["status", "completed_at", "error_message"])
    finally:
        root = job_root(job)
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)


@shared_task(bind=True, ignore_result=True)
def run_module1_uw_parameters_task(self, job_id: str) -> None:
    job = Module1Job.objects.get(pk=job_id)
    job.status = Module1Job.Status.RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])
    out_dir = job_output_dir(job)
    combined_path = job_root(job) / "in" / "combined" / "Combined_Summary.xlsx"
    try:
        meta = job.input_meta or {}
        payload = meta.get("payload") or {}
        if not combined_path.is_file():
            raise ValueError("Combined_Summary.xlsx missing from job staging.")
        out_bytes = apply_uw_parameters_to_combined_summary(
            str(combined_path), payload
        )
        out_file = out_dir / "Combined_Summary.xlsx"
        out_file.write_bytes(out_bytes)
        with tempfile.TemporaryDirectory() as tmp:
            zip_base = Path(tmp) / "outputs"
            zip_path = _zip_output_dir(out_dir, zip_base)
            with open(zip_path, "rb") as f:
                job.output_zip.save(f"{job.id}.zip", File(f), save=False)
        job.status = Module1Job.Status.SUCCESS
        job.completed_at = timezone.now()
        job.error_message = ""
        job.save(update_fields=["status", "completed_at", "error_message", "output_zip"])
    except Exception as exc:
        logger.exception("Module1 UW parameters job %s failed", job_id)
        job.status = Module1Job.Status.FAILED
        job.completed_at = timezone.now()
        job.error_message = str(exc)[:8000]
        job.save(update_fields=["status", "completed_at", "error_message"])
    finally:
        root = job_root(job)
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
