"""Chaining eligibility: a source must carry the SHEETS the consumer reads.

Module 1 emits Combined_Summary.xlsx on every summary / update-reserve run but
never writes the `ULAE-RA` / `Discount Rate` sheets (actuary judgement, added by
hand). Before this, every one of those jobs was offered as a Cash Flow Allocation
source and every one failed minutes into a Celery run. These tests pin that the
picker hides them, the submit endpoint rejects them by name, and — critically —
that neither the Module 1 consumers nor un-indexed legacy rows get caught up in it.
"""

import io
import shutil
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from openpyxl import Workbook
from rest_framework.test import APIClient

from accounts.models import Permission, Role
from module2_engine.engine import ALLOCATE_REQUIRED_SHEETS
from processing.models import Module1Job
from processing.services.source_resolver import ARTIFACT_COMBINED_SUMMARY
from tenants.models import Membership, Organization

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="sigma17-test-media-eligibility-")
User = get_user_model()

# What Module 1 actually produces: everything except the two judgement sheets.
MODULE1_SHEETS = [s for s in ALLOCATE_REQUIRED_SHEETS if s not in ("ULAE-RA", "Discount Rate")]


def _xlsx_with_sheets(sheets) -> bytes:
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


def _source(*, user, org, sheets, job_type=Module1Job.JobType.SUMMARY, index=True):
    """A successful job whose Combined_Summary carries exactly `sheets`.

    `index=False` reproduces a row created before output_sheets existed.
    """
    raw = _xlsx_with_sheets(sheets)
    job = Module1Job.objects.create(
        user=user,
        organization=org,
        job_type=job_type,
        status=Module1Job.Status.SUCCESS,
        work_dir=f"module1_jobs/{job_type}",
        input_meta={},
        output_artifacts=[ARTIFACT_COMBINED_SUMMARY],
        output_sheets={ARTIFACT_COMBINED_SUMMARY: list(sheets)} if index else {},
    )
    job.output_zip.save(f"{job.id}.zip", ContentFile(_zip_with({ARTIFACT_COMBINED_SUMMARY: raw})), save=True)
    return job


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class SourceEligibilityTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.org = Organization.objects.create(name="Acme", slug="acme-elig")
        self.user = _make_org_user(
            username="elig-alice",
            perm_keys=["module1.run", "module2.run", "runhistory.view"],
            org=self.org,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _candidates(self, **params):
        q = "&".join(f"{k}={v}" for k, v in params.items())
        res = self.client.get(f"/api/processing/source-candidates/?{q}")
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    # -- picker filtering ---------------------------------------------------

    def test_module1_source_is_hidden_from_the_allocate_picker(self):
        _source(user=self.user, org=self.org, sheets=MODULE1_SHEETS)
        body = self._candidates(consumer="module2_allocate")
        self.assertEqual(body["count"], 0)
        self.assertEqual(body["results"], [])
        self.assertEqual(body["required_sheets"], list(ALLOCATE_REQUIRED_SHEETS))

    def test_complete_workbook_is_offered(self):
        job = _source(user=self.user, org=self.org, sheets=ALLOCATE_REQUIRED_SHEETS)
        body = self._candidates(consumer="module2_allocate")
        self.assertEqual([r["id"] for r in body["results"]], [str(job.id)])
        self.assertTrue(body["results"][0]["eligible"])
        self.assertEqual(body["results"][0]["missing_sheets"], [])

    def test_real_workbook_shape_is_offered(self):
        """The shape an actuary actually uploads: every required sheet, plus
        extras, in a different order than the requirement declares.

        This is the case that matters in production — JSONB array containment is
        subset-based and order-independent, and a regression to an equality check
        would reject every real workbook while still passing the tidy
        exact-match test above.
        """
        realistic = [
            "IBNR Summary", "Allocation EP", "Combined Summary",
            "Additional Summary", "LIC (OS) Summary", "UPR Run-Off",
            "UW Summary", "FY Summary", "ULAE-RA", "Discount Rate",
        ]
        assert set(ALLOCATE_REQUIRED_SHEETS) < set(realistic)
        job = _source(user=self.user, org=self.org, sheets=realistic)
        body = self._candidates(consumer="module2_allocate")
        self.assertEqual([r["id"] for r in body["results"]], [str(job.id)])
        self.assertTrue(body["results"][0]["eligible"])

    def test_one_missing_sheet_is_enough_to_hide_a_job(self):
        for omitted in ("ULAE-RA", "Discount Rate", "IBNR Summary"):
            with self.subTest(omitted=omitted):
                Module1Job.objects.all().delete()
                _source(
                    user=self.user,
                    org=self.org,
                    sheets=[s for s in ALLOCATE_REQUIRED_SHEETS if s != omitted],
                )
                self.assertEqual(
                    self._candidates(consumer="module2_allocate")["count"], 0
                )

    def test_module1_consumers_are_unaffected(self):
        """Summary / Update Reserves legitimately append to a partial workbook,
        so omitting `consumer` must keep listing everything it listed before."""
        _source(user=self.user, org=self.org, sheets=MODULE1_SHEETS)
        self.assertEqual(self._candidates()["count"], 1)
        self.assertEqual(self._candidates(consumer="update_reserve")["count"], 1)

    def test_unknown_consumer_degrades_to_no_filter(self):
        _source(user=self.user, org=self.org, sheets=MODULE1_SHEETS)
        self.assertEqual(self._candidates(consumer="not_a_real_consumer")["count"], 1)

    # -- un-indexed legacy rows --------------------------------------------

    def test_unindexed_row_is_still_listed_then_self_heals(self):
        """A row from before output_sheets existed must not vanish. It is shown
        once, annotated with what it lacks, and indexed as a side effect — so the
        next query filters it out in SQL."""
        job = _source(user=self.user, org=self.org, sheets=MODULE1_SHEETS, index=False)

        first = self._candidates(consumer="module2_allocate")
        self.assertEqual(first["count"], 1)
        row = first["results"][0]
        self.assertFalse(row["eligible"])
        self.assertEqual(row["missing_sheets"], ["ULAE-RA", "Discount Rate"])

        job.refresh_from_db()
        self.assertEqual(
            job.output_sheets[ARTIFACT_COMBINED_SUMMARY], list(MODULE1_SHEETS)
        )
        self.assertEqual(self._candidates(consumer="module2_allocate")["count"], 0)

    def test_unreadable_output_is_treated_as_unknown_not_unusable(self):
        """A read failure must never silently remove a working source.

        The row still claims an output ZIP (so it is not excluded for the
        unrelated "no output" reason); the bytes on disk are simply not a
        readable archive, which is what a partial upload or a storage glitch
        looks like."""
        job = _source(user=self.user, org=self.org, sheets=ALLOCATE_REQUIRED_SHEETS, index=False)
        Path(job.output_zip.path).write_bytes(b"not a zip at all")

        body = self._candidates(consumer="module2_allocate")
        self.assertEqual(body["count"], 1)
        self.assertTrue(body["results"][0]["eligible"])
        self.assertEqual(body["results"][0]["missing_sheets"], [])
        # Nothing was cached from the failed read, so a later repair still indexes.
        job.refresh_from_db()
        self.assertEqual(job.output_sheets, {})

    # -- submit-time guard --------------------------------------------------

    @patch("processing.views.run_module2_allocate_task.delay")
    def test_allocate_submit_rejects_incomplete_source_by_name(self, mocked):
        job = _source(user=self.user, org=self.org, sheets=MODULE1_SHEETS)
        res = self.client.post(
            "/api/module2/jobs/allocate/",
            {"source_job_id": str(job.id)},
            format="multipart",
        )
        self.assertEqual(res.status_code, 400, res.content)
        detail = str(res.json())
        self.assertIn("ULAE-RA", detail)
        self.assertIn("Discount Rate", detail)
        # Nothing was queued: the point is to fail before burning a worker run.
        mocked.assert_not_called()

    @patch("processing.views.run_module2_allocate_task.delay")
    def test_allocate_submit_accepts_complete_source(self, mocked):
        job = _source(user=self.user, org=self.org, sheets=ALLOCATE_REQUIRED_SHEETS)
        res = self.client.post(
            "/api/module2/jobs/allocate/",
            {"source_job_id": str(job.id)},
            format="multipart",
        )
        self.assertEqual(res.status_code, 202, res.content)
        mocked.assert_called_once()

    @patch("processing.views.run_module1_update_reserve_task.delay")
    def test_module1_submit_does_not_apply_the_sheet_guard(self, _mocked):
        """The guard must not leak into the Module 1 endpoints.

        Update Reserves has its own unrelated required inputs, so this request is
        still rejected — what matters is WHY. If the sheet guard were applied to
        Module 1 consumers, the one-sheet source below would be refused for its
        missing sheets, and the reserve-inputs complaint would never be reached.
        """
        job = _source(user=self.user, org=self.org, sheets=["Combined Summary"])
        res = self.client.post(
            "/api/module1/jobs/update-reserve/",
            {"source_job_id": str(job.id)},
            format="multipart",
        )
        detail = res.content.decode()
        self.assertNotIn("ULAE-RA", detail)
        self.assertNotIn("is missing the sheet", detail)
        self.assertIn("reserve", detail.lower())
