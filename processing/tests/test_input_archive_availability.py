"""A finished job must still be able to show you its own claims.

`run_module1_summary_task` ends with `_cleanup_root`, which deletes the staged uploads. Every
diagnostic that reads a completed job's claims — the triangle view (requirement 5) and
large-claim ranking (requirement 6) — went through `_triangle_source_frame`, which fell back to
that folder. Measured before this fix: **6,580 rows before cleanup, `None` after**. Only
dataset-driven runs worked, because their rows are snapshotted; upload-driven runs, the primary
path, lost their claims the moment they succeeded.

Both feature suites missed it because they stage files into `job_input_subdir` and never run the
task, so cleanup never fires. **These tests run the real task body.** That is the only way this
class of gap surfaces, and it is why they are slow.
"""

import shutil
import tempfile
from pathlib import Path

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from processing.models import Module1Job
from processing.utils import init_job_work_dir, job_input_subdir, job_root
from tenants.models import Organization, ReservingClassAlias, alias_map_for

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="sigma17-test-media-arch-")
FIXTURES = Path(__file__).resolve().parents[2] / "benchmarks" / "fixtures" / "summary_ref"


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class InputArchiveAvailabilityTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.org = Organization.objects.create(name="Arch", slug="arch")
        self.user = User.objects.create_user("arch", "arch@example.com", "pw")
        # The reference book needs this alias or WP0's gate blocks the run.
        ReservingClassAlias.objects.create(
            organization=self.org, alias="Health", canonical="Health Insurance"
        )

    def _run_upload_driven_summary(self) -> Module1Job:
        from processing.tasks import run_module1_summary_task

        job = Module1Job.objects.create(
            user=self.user,
            organization=self.org,
            job_type=Module1Job.JobType.SUMMARY,
            input_meta={
                "exp_start": "01-01-2016", "exp_end": "31-12-2017",
                "bop": "01-01-2016", "eop": "31-12-2017",
                "class_aliases": alias_map_for(self.org),
            },
        )
        job.work_dir = f"module1_jobs/{job.id}"
        job.save(update_fields=["work_dir"])
        init_job_work_dir(job)
        for kind in ("premium", "claims_paid", "claims_os"):
            dest = job_input_subdir(job, kind)
            for src in sorted((FIXTURES / kind).glob("*.xlsx")):
                shutil.copy(src, dest / src.name)
        run_module1_summary_task(str(job.id))
        job.refresh_from_db()
        return job

    def test_the_run_still_succeeds_and_still_cleans_up(self):
        job = self._run_upload_driven_summary()
        self.assertEqual(job.status, Module1Job.Status.SUCCESS, job.error_message)
        self.assertFalse(
            job_root(job).exists(), "the staging folder must still be removed"
        )

    def test_the_paid_claims_survive_the_run(self):
        """Requirement 5. This is the assertion that was missing."""
        from processing.views import _triangle_source_frame

        job = self._run_upload_driven_summary()
        frame = _triangle_source_frame(job)
        self.assertIsNotNone(frame, "a finished job must still be able to read its claims")
        self.assertEqual(len(frame), 6580)
        self.assertIn("RESERVINGCLASS", frame.columns)

    def test_the_outstanding_claims_survive_the_run(self):
        """Requirement 6 shared the same defect through the same source frame."""
        from processing.views import _os_source_frame

        job = self._run_upload_driven_summary()
        frame = _os_source_frame(job)
        self.assertIsNotNone(frame)
        self.assertGreater(len(frame), 0)

    def test_a_triangle_can_actually_be_built_from_what_survived(self):
        """Reading rows is not enough — they must still carry what the builder needs."""
        import pandas as pd

        from core.grain import MONTHLY
        from module1_engine.triangles import build_triangle
        from processing.views import _triangle_source_frame

        job = self._run_upload_driven_summary()
        triangle = build_triangle(
            _triangle_source_frame(job),
            grain=MONTHLY,
            start=pd.Timestamp("2016-01-01"),
            end=pd.Timestamp("2017-12-31"),
        )
        self.assertEqual(len(triangle.accident_labels), 24)
        self.assertGreater(triangle.credibility.non_empty_cells, 0)

    def test_purging_the_output_takes_the_archive_with_it(self):
        """The archive rides the existing retention clock — a purged job cannot serve
        diagnostics either, and its bytes must not linger."""
        job = self._run_upload_driven_summary()
        self.assertTrue(job.input_archive)
        job.purge_output(reason="retention")
        job.refresh_from_db()
        self.assertFalse(job.input_archive)
        self.assertIsNotNone(job.output_purged_at)

    def test_a_dataset_driven_job_still_prefers_its_snapshots(self):
        """Snapshots are audit-grade and frozen at run time; the archive is a fallback for
        the upload path, not a replacement for them."""
        from processing.views import _triangle_source_frame

        job = Module1Job.objects.create(
            user=self.user, organization=self.org,
            job_type=Module1Job.JobType.SUMMARY,
            input_meta={"dataset_snapshots": {"claims_paid": []}},
        )
        # No snapshots, no archive, no folder -> None rather than a crash.
        self.assertIsNone(_triangle_source_frame(job))
