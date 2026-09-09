"""Update Reserve can take its Combined_Summary from a different job than the
reserve workbooks.

`source_job_id` must be the Summary run, because that is the only job whose ZIP
carries the reserve workbooks the LDF / CDF / method editors read. Its
Combined_Summary was therefore the only one this step could carry forward, so a
UW Parameters run layered on top (Exp Ratio / RI %, ULAE-RA, Discount Rate) was
silently discarded and the actuary had to re-add those sheets by hand before
Module 2 would accept the workbook.

These tests pin the precedence the task applies and — most importantly — that the
default path is byte-for-byte what it was before the second source existed.
"""

import io
import shutil
import tempfile
import zipfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from openpyxl import Workbook, load_workbook
from rest_framework.test import APIClient

from accounts.models import Permission, Role
from processing.models import Module1Job
from processing.services.source_resolver import ARTIFACT_COMBINED_SUMMARY
from processing.tasks import run_module1_update_reserve_task
from tenants.models import Membership, Organization

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="sigma17-test-media-ur-cs-")
User = get_user_model()

SUMMARY_SHEETS = ["Combined Summary", "UW Summary", "IBNR Summary"]
UW_PATCHED_SHEETS = SUMMARY_SHEETS + ["ULAE-RA", "Discount Rate"]


def _xlsx(sheets) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for name in sheets:
        wb.create_sheet(title=name).append(["a", "b"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _zip_with(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, raw in files.items():
            zf.writestr(name, raw)
    return buf.getvalue()


def _make_org_user(*, username, perm_keys, org):
    user = User.objects.create_user(username=username, password="testpass123")
    user.profile.active_organization = org
    user.profile.save(update_fields=["active_organization"])
    role, _ = Role.objects.get_or_create(name=f"role-{username}")
    for key in perm_keys:
        perm, _ = Permission.objects.get_or_create(
            key=key, defaults={"name": key, "module": "Processing", "description": ""}
        )
        role.permissions.add(perm)
    m = Membership.objects.create(user=user, organization=org, status="active")
    m.roles.add(role)
    return user


def _job_with(*, user, org, job_type, files: dict[str, bytes]):
    job = Module1Job.objects.create(
        user=user,
        organization=org,
        job_type=job_type,
        status=Module1Job.Status.SUCCESS,
        work_dir=f"module1_jobs/{job_type}",
        input_meta={},
        output_artifacts=sorted(files),
    )
    job.output_zip.save(f"{job.id}.zip", ContentFile(_zip_with(files)), save=True)
    return job


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class UpdateReserveCombinedSummarySourceTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.org = Organization.objects.create(name="Acme", slug="acme-ur-cs")
        self.user = _make_org_user(
            username="ur-cs-alice",
            perm_keys=["module1.run", "module2.run", "runhistory.view"],
            org=self.org,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        # The Summary run: reserve workbooks + a Combined_Summary WITHOUT the
        # actuary-judgement sheets (Module 1 never writes them).
        self.summary_job = _job_with(
            user=self.user, org=self.org,
            job_type=Module1Job.JobType.SUMMARY,
            files={
                ARTIFACT_COMBINED_SUMMARY: _xlsx(SUMMARY_SHEETS),
                "Motor Payment GROSS 2024-12.xlsx": _xlsx(["Reserve Summary"]),
            },
        )
        # The UW Parameters run layered on top: Combined_Summary only, but WITH
        # ULAE-RA / Discount Rate.
        self.uw_job = _job_with(
            user=self.user, org=self.org,
            job_type=Module1Job.JobType.UW_PARAMETERS,
            files={ARTIFACT_COMBINED_SUMMARY: _xlsx(UW_PATCHED_SHEETS)},
        )

    def _run_and_capture_staged_cs(self, job) -> list[str] | None:
        """Run the task with the engine stubbed, returning the staged workbook's
        sheet names — i.e. exactly what the engine would have appended to."""
        captured = {}

        def _fake_engine(folder_path, method_overrides=None):
            from pathlib import Path

            cs = Path(folder_path) / "Combined_Summary.xlsx"
            captured["sheets"] = (
                load_workbook(io.BytesIO(cs.read_bytes()), read_only=True).sheetnames
                if cs.exists() else None
            )

        with patch("processing.tasks.run_update_reserve_summary", _fake_engine):
            run_module1_update_reserve_task(str(job.id))
        job.refresh_from_db()
        self.assertEqual(job.status, Module1Job.Status.SUCCESS, job.error_message)
        return captured.get("sheets")

    def _start(self, payload):
        res = self.client.post(
            "/api/module1/jobs/update-reserve/", payload, format="multipart"
        )
        return res

    # -- the client's scenario ---------------------------------------------

    @patch("processing.views.run_module1_update_reserve_task.delay")
    def test_combined_summary_comes_from_the_named_job_not_the_reserve_source(self, _d):
        res = self._start({
            "source_job_id": str(self.summary_job.id),
            "combined_summary_source_job_id": str(self.uw_job.id),
            "ldf_overrides": "{}",
        })
        self.assertEqual(res.status_code, 202, res.content)
        job = Module1Job.objects.get(pk=res.json()["id"])
        # Lineage stays on the substantive source; the second ref rides in meta.
        self.assertEqual(job.source_job_id, self.summary_job.id)
        self.assertEqual(
            job.input_meta["combined_summary_source_job_id"], str(self.uw_job.id)
        )
        sheets = self._run_and_capture_staged_cs(job)
        self.assertIn("ULAE-RA", sheets)
        self.assertIn("Discount Rate", sheets)

    # -- the default path must not move ------------------------------------

    @patch("processing.views.run_module1_update_reserve_task.delay")
    def test_without_the_override_the_reserve_source_workbook_is_used(self, _d):
        """Historic behaviour, unchanged."""
        res = self._start({
            "source_job_id": str(self.summary_job.id),
            "ldf_overrides": "{}",
        })
        self.assertEqual(res.status_code, 202, res.content)
        job = Module1Job.objects.get(pk=res.json()["id"])
        self.assertNotIn("combined_summary_source_job_id", job.input_meta)
        sheets = self._run_and_capture_staged_cs(job)
        self.assertEqual(sorted(sheets), sorted(SUMMARY_SHEETS))

    @patch("processing.views.run_module1_update_reserve_task.delay")
    def test_uploaded_workbook_is_not_overwritten_by_the_reserve_source(self, _d):
        """With reserve files there is no source job, so the upload is all there
        is — but the task must reach that conclusion from the meta, not from luck."""
        reserve = io.BytesIO(_xlsx(["Reserve Summary"]))
        reserve.name = "Motor Payment GROSS 2024-12.xlsx"
        cs = io.BytesIO(_xlsx(UW_PATCHED_SHEETS))
        cs.name = "Combined_Summary.xlsx"
        res = self._start({"reserve": reserve, "combined_summary": cs})
        self.assertEqual(res.status_code, 202, res.content)
        job = Module1Job.objects.get(pk=res.json()["id"])
        sheets = self._run_and_capture_staged_cs(job)
        self.assertIn("ULAE-RA", sheets)

    # -- validation ---------------------------------------------------------

    @patch("processing.views.run_module1_update_reserve_task.delay")
    def test_upload_and_named_source_together_are_rejected(self, _d):
        cs = io.BytesIO(_xlsx(UW_PATCHED_SHEETS))
        cs.name = "Combined_Summary.xlsx"
        reserve = io.BytesIO(_xlsx(["Reserve Summary"]))
        reserve.name = "Motor.xlsx"
        res = self._start({
            "reserve": reserve,
            "combined_summary": cs,
            "combined_summary_source_job_id": str(self.uw_job.id),
        })
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("not both", res.json().get("detail", ""))

    @patch("processing.views.run_module1_update_reserve_task.delay")
    def test_named_source_must_contain_a_combined_summary(self, _d):
        barren = _job_with(
            user=self.user, org=self.org,
            job_type=Module1Job.JobType.SUMMARY,
            files={"Motor Payment GROSS 2024-12.xlsx": _xlsx(["Reserve Summary"])},
        )
        res = self._start({
            "source_job_id": str(self.summary_job.id),
            "combined_summary_source_job_id": str(barren.id),
            "ldf_overrides": "{}",
        })
        self.assertEqual(res.status_code, 400, res.content)

    @patch("processing.views.run_module1_update_reserve_task.delay")
    def test_named_source_from_another_org_is_not_visible(self, _d):
        other_org = Organization.objects.create(name="Other", slug="other-ur-cs")
        other_user = _make_org_user(
            username="ur-cs-bob", perm_keys=["module1.run"], org=other_org
        )
        foreign = _job_with(
            user=other_user, org=other_org,
            job_type=Module1Job.JobType.UW_PARAMETERS,
            files={ARTIFACT_COMBINED_SUMMARY: _xlsx(UW_PATCHED_SHEETS)},
        )
        res = self._start({
            "source_job_id": str(self.summary_job.id),
            "combined_summary_source_job_id": str(foreign.id),
            "ldf_overrides": "{}",
        })
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("not found", str(res.json()).lower())
