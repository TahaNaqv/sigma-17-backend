"""Filesystem helpers for Module 1 job staging."""

from pathlib import Path

from django.conf import settings


def job_root(job) -> Path:
    return Path(settings.MEDIA_ROOT) / job.work_dir


def job_input_subdir(job, name: str) -> Path:
    p = job_root(job) / "in" / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def job_output_dir(job) -> Path:
    p = job_root(job) / "out"
    p.mkdir(parents=True, exist_ok=True)
    return p


def init_job_work_dir(job) -> None:
    root = job_root(job)
    root.mkdir(parents=True, exist_ok=True)
    (root / "in" / "premium").mkdir(parents=True, exist_ok=True)
    (root / "in" / "claims_paid").mkdir(parents=True, exist_ok=True)
    (root / "in" / "claims_os").mkdir(parents=True, exist_ok=True)
    (root / "out").mkdir(parents=True, exist_ok=True)


def init_update_reserve_job_dirs(job) -> Path:
    """Create layout for update-reserve: in/reserve_batch + out."""
    root = job_root(job)
    root.mkdir(parents=True, exist_ok=True)
    staging = root / "in" / "reserve_batch"
    staging.mkdir(parents=True, exist_ok=True)
    (root / "out").mkdir(parents=True, exist_ok=True)
    return staging


def init_uw_parameters_job_dirs(job) -> Path:
    """Staging for Combined_Summary.xlsx before UW patch job."""
    root = job_root(job)
    root.mkdir(parents=True, exist_ok=True)
    combined = root / "in" / "combined"
    combined.mkdir(parents=True, exist_ok=True)
    (root / "out").mkdir(parents=True, exist_ok=True)
    return combined
