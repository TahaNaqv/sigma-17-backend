import io
import shutil
import tempfile
import zipfile
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from openpyxl import Workbook
from rest_framework.test import APIClient

from accounts.models import Permission, Role
from processing.models import Module1Job

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="sigma17-test-media-module2-")


def _give_role_with_permissions(user: User, role_name: str, perm_keys: list[str]) -> None:
    role = Role.objects.create(name=role_name)
    for key in perm_keys:
        perm, _ = Permission.objects.get_or_create(
            key=key,
            defaults={"name": key, "module": "Processing", "description": ""},
        )
        role.permissions.add(perm)
    user.profile.roles.add(role)


def _xlsx_with_sheet(name: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = name
    ws.append(["a", "b"])
    ws.append([1, 2])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _zip_with_xlsx(name: str = "output.xlsx") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, _xlsx_with_sheet("Sheet1"))
    return buf.getvalue()


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class Module2ApiTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.user = User.objects.create_user(username="m2", password="testpass123")
        _give_role_with_permissions(
            self.user, "Module2 Runner", ["module2.run", "runhistory.view"]
        )
        self.other = User.objects.create_user(username="other", password="testpass123")
        _give_role_with_permissions(
            self.other, "Other Runner", ["module2.run", "runhistory.view"]
        )
        self.viewer = User.objects.create_user(username="viewer", password="testpass123")
        _give_role_with_permissions(self.viewer, "Viewer", ["runhistory.view"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @patch("processing.views.run_module2_allocate_task.delay")
    def test_allocate_endpoint_creates_job(self, mocked_delay):
        payload = io.BytesIO(_xlsx_with_sheet("Combined Summary"))
        payload.name = "Combined_Summary.xlsx"
        res = self.client.post(
            "/api/module2/jobs/allocate/", {"combined_summary": payload}, format="multipart"
        )
        self.assertEqual(res.status_code, 202, res.content)
        body = res.json()
        self.assertEqual("module2_allocate", body["job_type"])
        mocked_delay.assert_called_once()

    def test_allocate_requires_module2_run_permission(self):
        c = APIClient()
        c.force_authenticate(user=self.viewer)
        payload = io.BytesIO(_xlsx_with_sheet("Combined Summary"))
        payload.name = "Combined_Summary.xlsx"
        res = c.post("/api/module2/jobs/allocate/", {"combined_summary": payload}, format="multipart")
        self.assertEqual(res.status_code, 403)

    def test_list_returns_only_module2_jobs(self):
        Module1Job.objects.create(
            user=self.user,
            job_type=Module1Job.JobType.MODULE2_ALLOCATE,
            status=Module1Job.Status.PENDING,
            work_dir="module1_jobs/a",
            input_meta={},
        )
        Module1Job.objects.create(
            user=self.user,
            job_type=Module1Job.JobType.SUMMARY,
            status=Module1Job.Status.PENDING,
            work_dir="module1_jobs/b",
            input_meta={},
        )
        res = self.client.get("/api/module2/jobs/")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(1, len(res.json()["results"]))
        self.assertEqual("module2_allocate", res.json()["results"][0]["job_type"])

    def test_unified_processing_history_returns_mixed_jobs(self):
        Module1Job.objects.create(
            user=self.user,
            job_type=Module1Job.JobType.MODULE2_ALLOCATE,
            status=Module1Job.Status.PENDING,
            work_dir="module1_jobs/u1",
            input_meta={},
        )
        Module1Job.objects.create(
            user=self.user,
            job_type=Module1Job.JobType.SUMMARY,
            status=Module1Job.Status.PENDING,
            work_dir="module1_jobs/u2",
            input_meta={},
        )
        res = self.client.get("/api/processing/jobs/?page=1&page_size=10")
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(2, body["count"])
        self.assertEqual(2, len(body["results"]))
        self.assertIn(body["results"][0]["job_type"], ["summary", "module2_allocate"])

    def test_unified_processing_history_is_owner_scoped(self):
        Module1Job.objects.create(
            user=self.other,
            job_type=Module1Job.JobType.MODULE2_PROCESS,
            status=Module1Job.Status.PENDING,
            work_dir="module1_jobs/u3",
            input_meta={},
        )
        res = self.client.get("/api/processing/jobs/?page=1&page_size=10")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(0, res.json()["count"])

    def test_ulr_endpoint_returns_rows_after_allocate_success(self):
        job = Module1Job.objects.create(
            user=self.user,
            job_type=Module1Job.JobType.MODULE2_ALLOCATE,
            status=Module1Job.Status.SUCCESS,
            work_dir="module1_jobs/m2",
            input_meta={
                "ulr_rows": [
                    {
                        "id": "1",
                        "reserving_class": "Motor",
                        "uwy": "2024",
                        "gwp": 1,
                        "upr": 1,
                        "gep": 1,
                        "paid_claims": 1,
                        "os": 1,
                        "ibnr": 1,
                        "incurred_claims": 1,
                        "ultimate_claims": 1,
                        "paid_lr": 0.1,
                        "inc_lr": 0.2,
                        "ult_lr": 0.3,
                        "commission_expense": 1,
                        "comm_ratio": 0.1,
                        "exp_ratio": 0.1,
                        "ri_percent": 0.1,
                        "ra_percent": 0.1,
                        "selected_ulr": 0.3,
                        "combined_ratio": 0.5,
                    }
                ]
            },
        )
        res = self.client.get(f"/api/module2/jobs/{job.id}/ulr/")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(1, len(res.json()["rows"]))

    def test_ulr_endpoint_rejects_non_success_allocate(self):
        job = Module1Job.objects.create(
            user=self.user,
            job_type=Module1Job.JobType.MODULE2_ALLOCATE,
            status=Module1Job.Status.RUNNING,
            work_dir="module1_jobs/m2-running",
            input_meta={},
        )
        res = self.client.get(f"/api/module2/jobs/{job.id}/ulr/")
        self.assertEqual(res.status_code, 400)

    def test_detail_and_download_are_owner_scoped(self):
        job = Module1Job.objects.create(
            user=self.user,
            job_type=Module1Job.JobType.MODULE2_PROCESS,
            status=Module1Job.Status.SUCCESS,
            work_dir="module1_jobs/m2-detail",
            input_meta={},
        )
        job.output_zip.save(f"{job.id}.zip", ContentFile(_zip_with_xlsx()), save=True)
        other_client = APIClient()
        other_client.force_authenticate(user=self.other)
        self.assertEqual(other_client.get(f"/api/module2/jobs/{job.id}/").status_code, 404)
        self.assertEqual(
            other_client.get(f"/api/module2/jobs/{job.id}/download/").status_code, 404
        )

    @patch("processing.views.run_module2_process_task.delay")
    def test_process_rejects_invalid_accounting_period(self, mocked_delay):
        allocate_job = Module1Job.objects.create(
            user=self.user,
            job_type=Module1Job.JobType.MODULE2_ALLOCATE,
            status=Module1Job.Status.SUCCESS,
            work_dir="module1_jobs/m2-alloc-ok1",
            input_meta={},
        )
        allocate_job.output_zip.save(
            f"{allocate_job.id}.zip",
            ContentFile(_zip_with_xlsx("Combined_Summary.xlsx")),
            save=True,
        )
        prev = io.BytesIO(_xlsx_with_sheet("LIC_BOP"))
        prev.name = "prev.xlsx"
        exp = io.BytesIO(_xlsx_with_sheet("Expense-CF"))
        exp.name = "expense.xlsx"
        res = self.client.post(
            "/api/module2/jobs/process/",
            {
                "allocate_job_id": str(allocate_job.id),
                "accounting_period": "not-a-year",
                "selected_ulr": "[]",
                "previous_period": prev,
                "expense_cf": exp,
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 400, res.content)
        mocked_delay.assert_not_called()

    @patch("processing.views.run_module2_process_task.delay")
    def test_process_rejects_malformed_selected_ulr(self, mocked_delay):
        allocate_job = Module1Job.objects.create(
            user=self.user,
            job_type=Module1Job.JobType.MODULE2_ALLOCATE,
            status=Module1Job.Status.SUCCESS,
            work_dir="module1_jobs/m2-alloc-ok2",
            input_meta={},
        )
        allocate_job.output_zip.save(
            f"{allocate_job.id}.zip",
            ContentFile(_zip_with_xlsx("Combined_Summary.xlsx")),
            save=True,
        )
        prev = io.BytesIO(_xlsx_with_sheet("LIC_BOP"))
        prev.name = "prev.xlsx"
        exp = io.BytesIO(_xlsx_with_sheet("Expense-CF"))
        exp.name = "expense.xlsx"
        res = self.client.post(
            "/api/module2/jobs/process/",
            {
                "allocate_job_id": str(allocate_job.id),
                "accounting_period": "2024",
                "selected_ulr": '[{"reserving_class":"A"}]',
                "previous_period": prev,
                "expense_cf": exp,
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 400, res.content)
        mocked_delay.assert_not_called()

    @patch("processing.views.run_module2_process_task.delay")
    def test_process_rejects_allocate_job_not_success(self, mocked_delay):
        allocate_job = Module1Job.objects.create(
            user=self.user,
            job_type=Module1Job.JobType.MODULE2_ALLOCATE,
            status=Module1Job.Status.RUNNING,
            work_dir="module1_jobs/m2-alloc-running",
            input_meta={},
        )
        prev = io.BytesIO(_xlsx_with_sheet("LIC_BOP"))
        prev.name = "prev.xlsx"
        exp = io.BytesIO(_xlsx_with_sheet("Expense-CF"))
        exp.name = "expense.xlsx"
        res = self.client.post(
            "/api/module2/jobs/process/",
            {
                "allocate_job_id": str(allocate_job.id),
                "accounting_period": "2024",
                "selected_ulr": "[]",
                "previous_period": prev,
                "expense_cf": exp,
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 400, res.content)
        mocked_delay.assert_not_called()

    @patch("processing.views.run_module2_process_task.delay")
    def test_process_endpoint_creates_job(self, mocked_delay):
        allocate_job = Module1Job.objects.create(
            user=self.user,
            job_type=Module1Job.JobType.MODULE2_ALLOCATE,
            status=Module1Job.Status.SUCCESS,
            work_dir="module1_jobs/m2-alloc",
            input_meta={},
        )
        allocate_job.output_zip.save(
            f"{allocate_job.id}.zip",
            ContentFile(_zip_with_xlsx("Combined_Summary.xlsx")),
            save=True,
        )
        prev = io.BytesIO(_xlsx_with_sheet("LIC_BOP"))
        prev.name = "prev.xlsx"
        exp = io.BytesIO(_xlsx_with_sheet("Expense-CF"))
        exp.name = "expense.xlsx"
        res = self.client.post(
            "/api/module2/jobs/process/",
            {
                "allocate_job_id": str(allocate_job.id),
                "accounting_period": "2024",
                "selected_ulr": "[]",
                "previous_period": prev,
                "expense_cf": exp,
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 202, res.content)
        self.assertEqual("module2_process", res.json()["job_type"])
        mocked_delay.assert_called_once()
