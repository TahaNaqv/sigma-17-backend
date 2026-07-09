"""HTTP-level tests for the IFRS 17 movement-analysis job endpoint.

Mirrors test_module2_api.py: the Celery task is mocked, so these exercise the
view (permissions, validation, job creation, input_meta) without running the engine.
The engine itself is covered by module2_engine/tests/test_movement_*.py.
"""

import io
import shutil
import tempfile
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from processing.models import Module1Job
from processing.tests.test_module2_api import (
    _give_role_with_permissions,
    _xlsx_with_sheet,
    _zip_with_xlsx,
)
from tenants.models import Organization

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="sigma17-test-media-movement-")
URL = "/api/module2/jobs/movement/"


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class MovementApiTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.org = Organization.objects.create(name="mv-org")
        self.user = User.objects.create_user(username="mv", password="testpass123")
        _give_role_with_permissions(
            self.user, "Movement Runner", ["module2.run", "runhistory.view"], self.org
        )
        self.viewer = User.objects.create_user(username="mv-viewer", password="testpass123")
        _give_role_with_permissions(self.viewer, "Viewer", ["runhistory.view"], self.org)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _allocate_job(self, slug: str) -> Module1Job:
        job = Module1Job.objects.create(
            user=self.user,
            organization=self.org,
            job_type=Module1Job.JobType.MODULE2_ALLOCATE,
            status=Module1Job.Status.SUCCESS,
            work_dir=f"module1_jobs/{slug}",
            input_meta={},
            output_artifacts=["Combined_Summary.xlsx"],
        )
        job.output_zip.save(
            f"{job.id}.zip",
            ContentFile(_zip_with_xlsx("Combined_Summary.xlsx")),
            save=True,
        )
        return job

    def _files(self):
        prev = io.BytesIO(_xlsx_with_sheet("LIC_BOP"))
        prev.name = "prev.xlsx"
        exp = io.BytesIO(_xlsx_with_sheet("Expense-CF"))
        exp.name = "expense.xlsx"
        return prev, exp

    @patch("processing.views.run_module2_movement_task.delay")
    def test_creates_job_and_dispatches(self, mocked_delay):
        allocate = self._allocate_job("mv-ok")
        prev, exp = self._files()
        res = self.client.post(
            URL,
            {
                "allocate_job_id": str(allocate.id),
                "accounting_period": "2024",
                "selected_ulr": "[]",
                "reporting_date": "31/12/2024",
                "reserving_classes": '["PROPERTY"]',
                "uwys": "[2022, 2023]",
                "previous_period": prev,
                "expense_cf": exp,
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 202, res.content)
        body = res.json()
        self.assertEqual("module2_movement", body["job_type"])
        mocked_delay.assert_called_once()
        job = Module1Job.objects.get(pk=body["id"])
        self.assertEqual(job.source_job_id, allocate.id)
        self.assertEqual(job.input_meta["reporting_date"], "31/12/2024")
        self.assertEqual(job.input_meta["scope"]["reserving_classes"], ["PROPERTY"])
        self.assertEqual(job.input_meta["scope"]["uwys"], [2022, 2023])

    @patch("processing.views.run_module2_movement_task.delay")
    def test_requires_module2_run_permission(self, mocked_delay):
        allocate = self._allocate_job("mv-perm")
        c = APIClient()
        c.force_authenticate(user=self.viewer)
        prev, exp = self._files()
        res = c.post(
            URL,
            {
                "allocate_job_id": str(allocate.id),
                "accounting_period": "2024",
                "previous_period": prev,
                "expense_cf": exp,
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 403, res.content)
        mocked_delay.assert_not_called()

    @patch("processing.views.run_module2_movement_task.delay")
    def test_rejects_invalid_accounting_period(self, mocked_delay):
        allocate = self._allocate_job("mv-badyear")
        prev, exp = self._files()
        res = self.client.post(
            URL,
            {
                "allocate_job_id": str(allocate.id),
                "accounting_period": "not-a-year",
                "previous_period": prev,
                "expense_cf": exp,
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 400, res.content)
        mocked_delay.assert_not_called()

    @patch("processing.views.run_module2_movement_task.delay")
    def test_rejects_missing_inputs(self, mocked_delay):
        allocate = self._allocate_job("mv-noinput")
        res = self.client.post(
            URL,
            {"allocate_job_id": str(allocate.id), "accounting_period": "2024"},
            format="multipart",
        )
        self.assertEqual(res.status_code, 400, res.content)
        mocked_delay.assert_not_called()

    @patch("processing.views.run_module2_movement_task.delay")
    def test_accepts_movement_override_dataset(self, mocked_delay):
        from datasets.models import Dataset, MovementOverrideRow

        allocate = self._allocate_job("mv-ovr")
        ds = Dataset.objects.create(
            organization=self.org, kind=Dataset.Kind.MOVEMENT_OVERRIDE,
            name="ovr", source=Dataset.Source.MANUAL, created_by=self.user,
        )
        MovementOverrideRow.objects.create(
            dataset=ds, row_index=0, reserving_class="PROPERTY", uwy=2023,
            ri_loss_recovery_new_onerous="500.00",
        )
        prev, exp = self._files()
        res = self.client.post(
            URL,
            {
                "allocate_job_id": str(allocate.id),
                "accounting_period": "2024",
                "previous_period": prev,
                "expense_cf": exp,
                "movement_override_dataset_id": str(ds.id),
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 202, res.content)
        job = Module1Job.objects.get(pk=res.json()["id"])
        self.assertIn("movement_override", job.input_meta["dataset_snapshots"])
        mocked_delay.assert_called_once()

    @patch("processing.views.run_module2_movement_task.delay")
    def test_rejects_wrong_kind_override_dataset(self, mocked_delay):
        from datasets.models import Dataset

        allocate = self._allocate_job("mv-ovr-wrong")
        ds = Dataset.objects.create(
            organization=self.org, kind=Dataset.Kind.EXPENSE_CF,
            name="not-ovr", source=Dataset.Source.MANUAL, created_by=self.user,
        )
        prev, exp = self._files()
        res = self.client.post(
            URL,
            {
                "allocate_job_id": str(allocate.id),
                "accounting_period": "2024",
                "previous_period": prev,
                "expense_cf": exp,
                "movement_override_dataset_id": str(ds.id),
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 400, res.content)
        mocked_delay.assert_not_called()

    def test_load_movement_overrides_builds_frame_from_snapshot(self):
        """The task seam the mocked endpoint test skips: snapshot → serializer →
        class×cohort frame with override_key columns."""
        from datasets.models import Dataset, MovementOverrideRow
        from datasets.services.snapshots import create_snapshot
        from processing.tasks import _load_movement_overrides

        ds = Dataset.objects.create(
            organization=self.org, kind=Dataset.Kind.MOVEMENT_OVERRIDE,
            name="ovr", source=Dataset.Source.MANUAL, created_by=self.user,
        )
        MovementOverrideRow.objects.create(
            dataset=ds, row_index=0, reserving_class="PROPERTY", uwy=2023,
            ri_loss_recovery_new_onerous="500.00",
        )
        snap = create_snapshot(dataset=ds)
        job = Module1Job.objects.create(
            user=self.user, organization=self.org,
            job_type=Module1Job.JobType.MODULE2_MOVEMENT, status=Module1Job.Status.PENDING,
            input_meta={"dataset_snapshots": {"movement_override": [str(snap.id)]}},
        )
        df = _load_movement_overrides(job)
        self.assertIsNotNone(df)
        r = df.iloc[0]
        self.assertEqual(r["RESERVINGCLASS"], "PROPERTY")
        self.assertEqual(int(r["UWY"]), 2023)
        self.assertEqual(float(r["ri_loss_recovery_new_onerous"]), 500.0)

    def test_load_movement_overrides_none_when_absent(self):
        from processing.tasks import _load_movement_overrides

        job = Module1Job.objects.create(
            user=self.user, organization=self.org,
            job_type=Module1Job.JobType.MODULE2_MOVEMENT, status=Module1Job.Status.PENDING,
            input_meta={},
        )
        self.assertIsNone(_load_movement_overrides(job))

    @patch("processing.views.run_module2_movement_task.delay")
    def test_rejects_bad_scope_json(self, mocked_delay):
        allocate = self._allocate_job("mv-badscope")
        prev, exp = self._files()
        res = self.client.post(
            URL,
            {
                "allocate_job_id": str(allocate.id),
                "accounting_period": "2024",
                "uwys": "not-json",
                "previous_period": prev,
                "expense_cf": exp,
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 400, res.content)
        mocked_delay.assert_not_called()
