"""HTTP-level tests for the IFRS 17 movement-analysis job endpoint.

Mirrors test_module2_api.py: the Celery task is mocked, so these exercise the
view (permissions, validation, job creation, input_meta) without running the engine.
The engine itself is covered by module2_engine/tests/test_movement_*.py.
"""

import io
import shutil
import tempfile
import zipfile
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

# Deterministic sentinel payloads for byte-exact reuse assertions. (Real .xlsx
# from _xlsx_with_sheet embeds a fresh timestamp per call, so it can't be used
# as an equality oracle; the tasks treat these inputs as opaque bytes anyway
# because the engine is mocked in these tests.)
_PREV_BYTES = b"PREVIOUS-PERIOD-WORKBOOK-BYTES"
_EXP_BYTES = b"EXPENSE-CF-WORKBOOK-BYTES"


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

    def _process_job(self, slug: str, *, allocate: Module1Job, with_archive=True) -> Module1Job:
        """A completed Cash Flow Allocation (process) job whose durable
        input_archive holds the Previous Period + Expense CF it consumed."""
        job = Module1Job.objects.create(
            user=self.user,
            organization=self.org,
            job_type=Module1Job.JobType.MODULE2_PROCESS,
            status=Module1Job.Status.SUCCESS,
            work_dir=f"module1_jobs/{slug}",
            input_meta={
                "allocate_job_id": str(allocate.id),
                "accounting_period": 2024,
                "selected_ulr": [{"reserving_class": "PROPERTY", "uwy": 2023, "selected_ulr": 0.7}],
            },
            output_artifacts=["Module2_Final_Output.xlsx"],
            source_job=allocate,
            source_artifact="Combined_Summary.xlsx",
        )
        job.output_zip.save(
            f"{job.id}.zip",
            ContentFile(_zip_with_xlsx("Module2_Final_Output.xlsx")),
            save=True,
        )
        if with_archive:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("Previous_Period.xlsx", _PREV_BYTES)
                zf.writestr("Expense_CF.xlsx", _EXP_BYTES)
            job.input_archive.save(f"{job.id}-inputs.zip", ContentFile(buf.getvalue()), save=True)
        return job

    @patch("processing.views.run_module2_movement_task.delay")
    def test_chains_off_process_job_inherits_inputs(self, mocked_delay):
        """With process_job_id and no re-supplied inputs, the movement job inherits
        Previous Period + Expense CF + accounting period + ULR — nothing asked twice."""
        allocate = self._allocate_job("mv-chain-alloc")
        process = self._process_job("mv-chain-proc", allocate=allocate)
        res = self.client.post(
            URL,
            {
                "process_job_id": str(process.id),
                "reporting_date": "31/12/2024",
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 202, res.content)
        job = Module1Job.objects.get(pk=res.json()["id"])
        # Combined_Summary lineage points at the allocate ancestor.
        self.assertEqual(job.source_job_id, allocate.id)
        self.assertEqual(job.input_meta["process_job_id"], str(process.id))
        # Accounting period + ULR were inherited from the process job.
        self.assertEqual(job.input_meta["accounting_period"], 2024)
        self.assertEqual(len(job.input_meta["selected_ulr"]), 1)
        # No inputs were staged/snapshotted on this job — they'll be read from
        # the process job's archive at task time.
        self.assertEqual(job.input_meta.get("files", {}), {})
        self.assertEqual(job.input_meta.get("dataset_snapshots", {}), {})
        mocked_delay.assert_called_once()

    @patch("processing.views.run_module2_movement_task.delay")
    def test_process_job_without_input_archive_rejected(self, mocked_delay):
        allocate = self._allocate_job("mv-noarch-alloc")
        process = self._process_job("mv-noarch-proc", allocate=allocate, with_archive=False)
        res = self.client.post(
            URL,
            {"process_job_id": str(process.id), "reporting_date": "31/12/2024"},
            format="multipart",
        )
        self.assertEqual(res.status_code, 400, res.content)
        mocked_delay.assert_not_called()

    @patch("processing.views.run_module2_movement_task.delay")
    def test_process_job_wrong_type_rejected(self, mocked_delay):
        """Passing an allocate job id where a process job is expected is rejected
        (its output has no Module2_Final_Output.xlsx)."""
        allocate = self._allocate_job("mv-wrongtype")
        res = self.client.post(
            URL,
            {"process_job_id": str(allocate.id), "reporting_date": "31/12/2024"},
            format="multipart",
        )
        self.assertEqual(res.status_code, 400, res.content)
        mocked_delay.assert_not_called()

    @patch("processing.views.run_module2_movement_task.delay")
    def test_override_input_supersedes_inherited(self, mocked_delay):
        """A per-slot override is staged on the movement job even when chaining."""
        allocate = self._allocate_job("mv-ovr-alloc")
        process = self._process_job("mv-ovr-proc", allocate=allocate)
        prev, exp = self._files()
        res = self.client.post(
            URL,
            {
                "process_job_id": str(process.id),
                "reporting_date": "31/12/2024",
                "previous_period": prev,  # override only Previous Period
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 202, res.content)
        job = Module1Job.objects.get(pk=res.json()["id"])
        self.assertIn("previous_period", job.input_meta.get("files", {}))
        mocked_delay.assert_called_once()

    def test_read_or_inherit_input_prefers_local_then_archive(self):
        """Task seam: local staged file wins; otherwise inherit from the process
        job's input_archive; error when neither exists."""
        from pathlib import Path

        from processing.tasks import (
            INPUT_ARCHIVE_PREVIOUS,
            _read_or_inherit_input,
        )

        allocate = self._allocate_job("mv-seam-alloc")
        process = self._process_job("mv-seam-proc", allocate=allocate)

        # No local file → inherit exact archived bytes.
        missing = Path(TEST_MEDIA_ROOT) / "nope" / "Previous_Period.xlsx"
        inherited = _read_or_inherit_input(
            missing, INPUT_ARCHIVE_PREVIOUS, process, label="Previous Period"
        )
        self.assertEqual(inherited, _PREV_BYTES)

        # Local file present → override wins.
        local_dir = Path(TEST_MEDIA_ROOT) / "seam-local"
        local_dir.mkdir(parents=True, exist_ok=True)
        local_path = local_dir / "Previous_Period.xlsx"
        local_path.write_bytes(b"LOCAL-OVERRIDE")
        self.assertEqual(
            _read_or_inherit_input(local_path, INPUT_ARCHIVE_PREVIOUS, process, label="Previous Period"),
            b"LOCAL-OVERRIDE",
        )

        # Neither local nor a process job → clear error.
        with self.assertRaises(ValueError):
            _read_or_inherit_input(missing, INPUT_ARCHIVE_PREVIOUS, None, label="Previous Period")

    def test_process_task_persists_input_archive(self):
        """The process task archives the exact Previous Period + Expense CF it
        consumed, so a later movement job can reuse them byte-for-byte."""
        import processing.tasks as tasks
        from processing.services.source_resolver import read_input_archive_bytes
        from processing.utils import init_module2_process_job_dirs

        allocate = self._allocate_job("proc-arch-alloc")
        job = Module1Job.objects.create(
            user=self.user,
            organization=self.org,
            job_type=Module1Job.JobType.MODULE2_PROCESS,
            status=Module1Job.Status.PENDING,
            work_dir="module1_jobs/proc-arch-run",
            input_meta={
                "allocate_job_id": str(allocate.id),
                "accounting_period": 2024,
                "selected_ulr": [],
            },
            source_job=allocate,
            source_artifact="Combined_Summary.xlsx",
        )
        prev_dir, exp_dir = init_module2_process_job_dirs(job)
        (prev_dir / "Previous_Period.xlsx").write_bytes(_PREV_BYTES)
        (exp_dir / "Expense_CF.xlsx").write_bytes(_EXP_BYTES)

        with patch.object(tasks, "run_module2_process", return_value=_xlsx_with_sheet("Out")) as mp:
            tasks.run_module2_process_task(str(job.id))

        job.refresh_from_db()
        self.assertEqual(job.status, Module1Job.Status.SUCCESS, job.error_message)
        # Engine received the staged bytes...
        self.assertEqual(mp.call_args.args[1], _PREV_BYTES)
        # ...and they were archived, retrievable byte-for-byte after job cleanup.
        self.assertTrue(job.input_archive)
        self.assertEqual(
            read_input_archive_bytes(source_job=job, member="Previous_Period.xlsx"),
            _PREV_BYTES,
        )
        self.assertEqual(
            read_input_archive_bytes(source_job=job, member="Expense_CF.xlsx"),
            _EXP_BYTES,
        )

    def test_movement_task_feeds_inherited_bytes_to_engine(self):
        """End-to-end task wiring: a movement job chained off a process job (no
        local inputs) reads Previous Period + Expense CF from the process job's
        input_archive and passes those exact bytes to the engine."""
        import processing.tasks as tasks

        allocate = self._allocate_job("mv-task-alloc")
        process = self._process_job("mv-task-proc", allocate=allocate)
        movement = Module1Job.objects.create(
            user=self.user,
            organization=self.org,
            job_type=Module1Job.JobType.MODULE2_MOVEMENT,
            status=Module1Job.Status.PENDING,
            work_dir=f"module1_jobs/mv-task-run",
            input_meta={
                "allocate_job_id": str(allocate.id),
                "process_job_id": str(process.id),
                "accounting_period": 2024,
                "selected_ulr": [],
                "reporting_date": "31/12/2024",
                "scope": {},
            },
            source_job=allocate,
            source_artifact="Combined_Summary.xlsx",
        )

        recon = {
            "missing_columns": [], "pairs": 0, "ties_out": True, "breaches": 0,
            "cells_checked": 0, "max_abs_residual": 0.0,
            "tolerance": {"abs": 1.0, "rel": 0.0}, "top_breaches": [],
        }
        with patch.object(tasks, "run_module2_movement", return_value=(b"XLSX", {})) as mv, \
             patch.object(tasks, "reconciliation_report", return_value=recon), \
             patch("module2_engine.movement.workbook.build_json_companion", return_value={}):
            tasks.run_module2_movement_task(str(movement.id))

        movement.refresh_from_db()
        self.assertEqual(movement.status, Module1Job.Status.SUCCESS, movement.error_message)
        # The engine received the exact bytes archived on the process job.
        _combined, prev_bytes, exp_bytes = mv.call_args.args[0], mv.call_args.args[1], mv.call_args.args[2]
        self.assertEqual(prev_bytes, _PREV_BYTES)
        self.assertEqual(exp_bytes, _EXP_BYTES)
        # And the movement job persisted its own reusable input archive.
        self.assertTrue(movement.input_archive)

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
