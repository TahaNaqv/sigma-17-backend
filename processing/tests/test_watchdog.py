"""Tests for the stuck-job watchdog (processing.services.watchdog)."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from processing.models import Module1Job
from processing.services.watchdog import reap_stuck_jobs
from tenants.models import Organization

User = get_user_model()


class WatchdogTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org A")
        self.user = User.objects.create_user(username="watchdog-user", password="pw")
        self.now = timezone.now()
        self.old = self.now - timedelta(hours=3)  # well past time_limit + margin
        self.recent = self.now - timedelta(minutes=1)

    def _job(self, *, status, started_at=None, created_offset=None):
        job = Module1Job.objects.create(
            user=self.user,
            organization=self.org,
            job_type=Module1Job.JobType.SUMMARY,
            status=status,
            started_at=started_at,
        )
        if created_offset is not None:
            # created_at is auto_now_add; override for the test.
            Module1Job.objects.filter(id=job.id).update(created_at=created_offset)
            job.refresh_from_db()
        return job

    def test_reaps_stuck_running_job(self):
        job = self._job(status=Module1Job.Status.RUNNING, started_at=self.old)
        result = reap_stuck_jobs(now=self.now)
        job.refresh_from_db()
        self.assertEqual(job.status, Module1Job.Status.FAILED)
        self.assertIsNotNone(job.completed_at)
        self.assertIn("watchdog", job.error_message)
        self.assertEqual(result["reaped_running"], 1)

    def test_reaps_stuck_pending_job(self):
        job = self._job(status=Module1Job.Status.PENDING, created_offset=self.old)
        result = reap_stuck_jobs(now=self.now)
        job.refresh_from_db()
        self.assertEqual(job.status, Module1Job.Status.FAILED)
        self.assertEqual(result["reaped_pending"], 1)

    def test_leaves_recent_running_job(self):
        job = self._job(status=Module1Job.Status.RUNNING, started_at=self.recent)
        reap_stuck_jobs(now=self.now)
        job.refresh_from_db()
        self.assertEqual(job.status, Module1Job.Status.RUNNING)

    def test_leaves_terminal_jobs(self):
        done = self._job(status=Module1Job.Status.SUCCESS, started_at=self.old)
        failed = self._job(status=Module1Job.Status.FAILED, started_at=self.old)
        reap_stuck_jobs(now=self.now)
        done.refresh_from_db()
        failed.refresh_from_db()
        self.assertEqual(done.status, Module1Job.Status.SUCCESS)
        self.assertEqual(failed.status, Module1Job.Status.FAILED)

    def test_is_idempotent(self):
        self._job(status=Module1Job.Status.RUNNING, started_at=self.old)
        first = reap_stuck_jobs(now=self.now)
        second = reap_stuck_jobs(now=self.now)
        self.assertEqual(first["reaped_running"], 1)
        self.assertEqual(second["reaped_running"], 0)
