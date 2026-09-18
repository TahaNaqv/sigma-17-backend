"""Excluding a reserving class, through a real Summary run.

The unit tests for `drop_reserving_classes` prove the filter itself. They do not prove that the
instruction survives the journey from the API, onto the job, through the task and into the
engine — which is the part that actually has to work when the client runs without Health
Insurance. This runs the real task body and looks at the workbooks it produces.

Slow, deliberately: it is the only way this class of gap shows up.
"""

import json
import shutil
import tempfile
import zipfile
from pathlib import Path

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from processing.models import Module1Job
from processing.utils import init_job_work_dir, job_input_subdir
from tenants.models import Organization, ReservingClassAlias, alias_map_for

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="sigma17-test-media-excl-")
FIXTURES = Path(__file__).resolve().parents[2] / "benchmarks" / "fixtures" / "summary_ref"
EXCLUDED = "Health Insurance"


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class ExcludedClassEndToEndTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        if not FIXTURES.is_dir():
            self.skipTest("reference fixture not available")
        self.org = Organization.objects.create(name="Excl", slug="excl")
        self.user = User.objects.create_user("excl", "excl@example.com", "pw")
        # Without this alias the reference book's `Health` claims never join to
        # `Health Insurance` premium — and the exclusion must be written in the CANONICAL
        # name, which is exactly what applying it after aliasing buys.
        ReservingClassAlias.objects.create(
            organization=self.org, alias="Health", canonical=EXCLUDED
        )

    def _run(self, excluded=None) -> Module1Job:
        from processing.tasks import run_module1_summary_task

        meta = {
            "exp_start": "01-01-2016", "exp_end": "31-12-2017",
            "bop": "01-01-2016", "eop": "31-12-2017",
            "class_aliases": alias_map_for(self.org),
        }
        if excluded:
            meta["excluded_classes"] = excluded
        job = Module1Job.objects.create(
            user=self.user, organization=self.org,
            job_type=Module1Job.JobType.SUMMARY, input_meta=meta,
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

    @staticmethod
    def _artifacts(job) -> list[str]:
        with job.output_zip.open("rb") as fh:
            return zipfile.ZipFile(fh).namelist()

    def test_the_excluded_class_produces_no_workbook(self):
        job = self._run(excluded=[EXCLUDED])
        self.assertEqual(job.status, Module1Job.Status.SUCCESS, job.error_message)
        names = self._artifacts(job)
        self.assertTrue(names, "the run produced no output at all")
        offending = [n for n in names if EXCLUDED in n]
        self.assertEqual(offending, [], f"excluded class still has workbooks: {offending}")
        # The run must still be a real run, not an empty one.
        self.assertTrue([n for n in names if n.endswith(".xlsx")])

    def test_the_other_classes_are_untouched(self):
        """An exclusion removes one class; it must not quietly shrink the rest of the book."""
        kept = self._artifacts(self._run(excluded=[EXCLUDED]))
        for expected in ("Motor Insurance", "Miscellaneous", "Fire and property damage"):
            self.assertTrue(
                any(expected in n for n in kept), f"{expected} disappeared with the exclusion"
            )

    def test_without_an_exclusion_the_class_is_present(self):
        """The control. Without it the first test could pass on a run that produced nothing."""
        names = self._artifacts(self._run())
        self.assertTrue(
            any(EXCLUDED in n for n in names),
            f"{EXCLUDED} should be present when nothing is excluded: {names[:10]}",
        )

    def test_the_exclusion_is_recorded_on_the_job_for_a_re_run(self):
        job = self._run(excluded=[EXCLUDED])
        self.assertEqual((job.input_meta or {}).get("excluded_classes"), [EXCLUDED])
        json.dumps(job.input_meta)  # must stay JSON-safe for the API and any re-run
