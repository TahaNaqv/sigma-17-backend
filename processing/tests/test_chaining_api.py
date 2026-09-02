"""End-to-end API tests for chaining + retention.

These tests use the multi-tenant setup as it exists today: each test user has
a Membership in an Organization with a Role granting the required permission
keys, and UserProfile.active_organization is set so request-level org
resolution works.
"""

import io
import json
import shutil
import tempfile
import zipfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from openpyxl import Workbook
from rest_framework.test import APIClient

from accounts.models import Permission, Role
from processing.models import Module1Job
from processing.services.source_resolver import ARTIFACT_COMBINED_SUMMARY
from tenants.models import Membership, Organization

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="sigma17-test-media-chaining-")
User = get_user_model()


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _xlsx_bytes(sheet_name: str = "Sheet1") -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(["a", "b"])
    ws.append([1, 2])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _zip_with(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, raw in files.items():
            zf.writestr(name, raw)
    return buf.getvalue()


def _make_org_user(*, username: str, perm_keys: list[str], org: Organization) -> User:
    user = User.objects.create_user(username=username, password="testpass123")
    user.profile.active_organization = org
    user.profile.save(update_fields=["active_organization"])
    role, _ = Role.objects.get_or_create(name=f"role-{username}")
    for key in perm_keys:
        perm, _ = Permission.objects.get_or_create(
            key=key,
            defaults={"name": key, "module": "Processing", "description": ""},
        )
        role.permissions.add(perm)
    m = Membership.objects.create(user=user, organization=org, status="active")
    m.roles.add(role)
    return user


def _make_successful_source(
    *,
    user: User,
    org: Organization,
    job_type: str = Module1Job.JobType.SUMMARY,
    artifacts: list[str] | None = None,
    output_purged: bool = False,
) -> Module1Job:
    artifacts = artifacts or [ARTIFACT_COMBINED_SUMMARY]
    payload = {a: _xlsx_bytes(a.replace(".xlsx", "")) for a in artifacts}
    job = Module1Job.objects.create(
        user=user,
        organization=org,
        job_type=job_type,
        status=Module1Job.Status.SUCCESS,
        work_dir=f"module1_jobs/{job_type}",
        input_meta={},
        output_artifacts=artifacts,
    )
    job.output_zip.save(f"{job.id}.zip", ContentFile(_zip_with(payload)), save=True)
    if output_purged:
        job.output_purged_at = __import__("django.utils.timezone").utils.timezone.now()
        job.save(update_fields=["output_purged_at"])
    return job


# ---------------------------------------------------------------------------
# Allocate chaining
# ---------------------------------------------------------------------------


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class AllocateChainingTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.org = Organization.objects.create(name="Acme", slug="acme")
        self.user = _make_org_user(
            username="alice",
            perm_keys=["module1.run", "module2.run", "runhistory.view"],
            org=self.org,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @patch("processing.views.run_module2_allocate_task.delay")
    def test_allocate_from_source_creates_job_with_lineage_fk(self, _mocked):
        source = _make_successful_source(user=self.user, org=self.org)
        res = self.client.post(
            "/api/module2/jobs/allocate/",
            {"source_job_id": str(source.id)},
            format="multipart",
        )
        self.assertEqual(res.status_code, 202, res.content)
        body = res.json()
        self.assertEqual(body["job_type"], "module2_allocate")

        new_job = Module1Job.objects.get(pk=body["id"])
        self.assertEqual(new_job.source_job_id, source.id)
        self.assertEqual(new_job.source_artifact, ARTIFACT_COMBINED_SUMMARY)
        self.assertEqual(body["source"]["job_id"], str(source.id))
        self.assertEqual(body["source"]["artifact"], ARTIFACT_COMBINED_SUMMARY)

    @patch("processing.views.run_module2_allocate_task.delay")
    def test_allocate_rejects_both_file_and_source(self, _mocked):
        source = _make_successful_source(user=self.user, org=self.org)
        payload_file = io.BytesIO(_xlsx_bytes("Combined Summary"))
        payload_file.name = "Combined_Summary.xlsx"
        res = self.client.post(
            "/api/module2/jobs/allocate/",
            {"source_job_id": str(source.id), "combined_summary": payload_file},
            format="multipart",
        )
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("not both", res.json().get("detail", ""))

    @patch("processing.views.run_module2_allocate_task.delay")
    def test_allocate_rejects_neither_file_nor_source(self, _mocked):
        res = self.client.post(
            "/api/module2/jobs/allocate/", {}, format="multipart"
        )
        self.assertEqual(res.status_code, 400, res.content)

    def test_allocate_source_must_be_success(self):
        source = _make_successful_source(user=self.user, org=self.org)
        for bad in [
            Module1Job.Status.PENDING,
            Module1Job.Status.RUNNING,
            Module1Job.Status.FAILED,
        ]:
            source.status = bad
            source.save(update_fields=["status"])
            res = self.client.post(
                "/api/module2/jobs/allocate/",
                {"source_job_id": str(source.id)},
                format="multipart",
            )
            self.assertEqual(res.status_code, 400, f"{bad}: {res.content}")
            self.assertIn("not completed successfully", str(res.content))

    def test_allocate_source_must_contain_artifact(self):
        source = _make_successful_source(
            user=self.user, org=self.org, artifacts=["Other.xlsx"]
        )
        res = self.client.post(
            "/api/module2/jobs/allocate/",
            {"source_job_id": str(source.id)},
            format="multipart",
        )
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("does not contain", str(res.content))

    def test_allocate_source_purged_is_rejected(self):
        source = _make_successful_source(
            user=self.user, org=self.org, output_purged=True
        )
        res = self.client.post(
            "/api/module2/jobs/allocate/",
            {"source_job_id": str(source.id)},
            format="multipart",
        )
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("expired", str(res.content))

    def test_allocate_source_other_org_returns_400(self):
        other_org = Organization.objects.create(name="Bravo", slug="bravo")
        other_user = User.objects.create_user(
            username="bob", password="testpass123"
        )
        other_user.profile.active_organization = other_org
        other_user.profile.save(update_fields=["active_organization"])
        source = _make_successful_source(user=other_user, org=other_org)
        res = self.client.post(
            "/api/module2/jobs/allocate/",
            {"source_job_id": str(source.id)},
            format="multipart",
        )
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("not found", str(res.content))

    def test_allocate_source_other_user_in_same_org_returns_400(self):
        other = _make_org_user(
            username="carol",
            perm_keys=["module1.run", "module2.run"],
            org=self.org,
        )
        source = _make_successful_source(user=other, org=self.org)
        res = self.client.post(
            "/api/module2/jobs/allocate/",
            {"source_job_id": str(source.id)},
            format="multipart",
        )
        # Non-superuser cannot chain another user's job within their own org.
        self.assertEqual(res.status_code, 400, res.content)

    @patch("processing.views.run_module2_allocate_task.delay")
    def test_allocate_with_file_upload_still_works(self, mocked_delay):
        payload_file = io.BytesIO(_xlsx_bytes("Combined Summary"))
        payload_file.name = "Combined_Summary.xlsx"
        res = self.client.post(
            "/api/module2/jobs/allocate/",
            {"combined_summary": payload_file},
            format="multipart",
        )
        self.assertEqual(res.status_code, 202, res.content)
        self.assertIsNone(res.json()["source"])
        mocked_delay.assert_called_once()


# ---------------------------------------------------------------------------
# Module 1 → Module 1 chaining (uw_parameters, update_reserve, summary)
# ---------------------------------------------------------------------------


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class Module1ChainingTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.org = Organization.objects.create(name="Acme1", slug="acme1")
        self.user = _make_org_user(
            username="dave", perm_keys=["module1.run"], org=self.org,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @patch("processing.views.run_module1_uw_parameters_task.delay")
    def test_uw_parameters_from_source(self, _mocked):
        source = _make_successful_source(user=self.user, org=self.org)
        res = self.client.post(
            "/api/module1/jobs/uw-parameters/",
            {"source_job_id": str(source.id), "payload": "{}"},
            format="multipart",
        )
        self.assertEqual(res.status_code, 202, res.content)
        new_job = Module1Job.objects.get(pk=res.json()["id"])
        self.assertEqual(new_job.source_job_id, source.id)

    def test_uw_parameters_requires_exactly_one(self):
        res = self.client.post(
            "/api/module1/jobs/uw-parameters/",
            {"payload": "{}"},
            format="multipart",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("Provide either", str(res.content))

    @patch("processing.views.run_module1_update_reserve_task.delay")
    def test_update_reserve_from_source_is_optional(self, _mocked):
        source = _make_successful_source(user=self.user, org=self.org)
        reserve = io.BytesIO(_xlsx_bytes("Reserve"))
        reserve.name = "ABC_GROSS.xlsx"
        res = self.client.post(
            "/api/module1/jobs/update-reserve/",
            {"reserve": reserve, "source_job_id": str(source.id)},
            format="multipart",
        )
        self.assertEqual(res.status_code, 202, res.content)
        new_job = Module1Job.objects.get(pk=res.json()["id"])
        self.assertEqual(new_job.source_job_id, source.id)

    @patch("processing.views.run_module1_update_reserve_task.delay")
    def test_update_reserve_neither_is_ok(self, _mocked):
        reserve = io.BytesIO(_xlsx_bytes("Reserve"))
        reserve.name = "ABC_GROSS.xlsx"
        res = self.client.post(
            "/api/module1/jobs/update-reserve/",
            {"reserve": reserve},
            format="multipart",
        )
        self.assertEqual(res.status_code, 202, res.content)

    @patch("processing.views.run_module1_update_reserve_task.delay")
    def test_update_reserve_method_overrides_round_trip(self, mocked_delay):
        source = _make_successful_source(user=self.user, org=self.org)
        overrides = {
            "Motor TP GROSS 2024-12.xlsx": {
                "2024Q1": {"implied_lr": 0.6, "selected_method": "ELR"},
            }
        }
        res = self.client.post(
            "/api/module1/jobs/update-reserve/",
            {
                "source_job_id": str(source.id),
                "method_overrides": json.dumps(overrides),
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 202, res.content)
        job = Module1Job.objects.get(pk=res.json()["id"])
        self.assertEqual(job.source_job_id, source.id)
        self.assertEqual(job.input_meta["method_overrides"], overrides)
        mocked_delay.assert_called_once()

    @patch("processing.views.run_module1_update_reserve_task.delay")
    def test_update_reserve_ldf_overrides_round_trip(self, mocked_delay):
        source = _make_successful_source(user=self.user, org=self.org)
        overrides = {
            "Motor TP GROSS 2024-12.xlsx": {
                "Paid Claims Triangle": [1.05, 1.02, 1.0],
                "Reported Triangle": [1.1, 1.03, 1.0],
            }
        }
        res = self.client.post(
            "/api/module1/jobs/update-reserve/",
            {
                "source_job_id": str(source.id),
                "ldf_overrides": json.dumps(overrides),
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 202, res.content)
        job = Module1Job.objects.get(pk=res.json()["id"])
        self.assertEqual(job.source_job_id, source.id)
        self.assertEqual(job.input_meta["ldf_overrides"], overrides)
        mocked_delay.assert_called_once()

    @patch("processing.views.run_module1_update_reserve_task.delay")
    def test_update_reserve_records_the_ldf_selection_behind_the_override(self, mocked_delay):
        """WP1 audit trail. The engine consumes the derived vector; a reviewer needs the
        judgement — which basis, which factors struck out, and how many each column actually
        averaged. `factor_counts` is what distinguishes "chose a 4-period average" from
        "chose a label that silently did nothing"."""
        source = _make_successful_source(user=self.user, org=self.org)
        overrides = {"Motor TP GROSS 2024-12.xlsx": {"Paid Claims Triangle": [1.05, 1.0]}}
        selection = {
            "Motor TP GROSS 2024-12.xlsx": {
                "Paid Claims Triangle": {
                    "basis": "ex_hi_lo",
                    "excluded_cells": [[2, 0]],
                    "factor_counts": [1, 0],
                    "derived_ldf": [1.05, 1.0],
                    "applied_at": "2026-09-01T10:14:03Z",
                }
            }
        }
        res = self.client.post(
            "/api/module1/jobs/update-reserve/",
            {
                "source_job_id": str(source.id),
                "ldf_overrides": json.dumps(overrides),
                "ldf_selection": json.dumps(selection),
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 202, res.content)
        job = Module1Job.objects.get(pk=res.json()["id"])
        self.assertEqual(job.input_meta["ldf_selection"], selection)
        self.assertEqual(job.input_meta["ldf_overrides"], overrides)
        mocked_delay.assert_called_once()

    @patch("processing.views.run_module1_update_reserve_task.delay")
    def test_update_reserve_rejects_a_malformed_ldf_selection(self, mocked_delay):
        source = _make_successful_source(user=self.user, org=self.org)
        res = self.client.post(
            "/api/module1/jobs/update-reserve/",
            {
                "source_job_id": str(source.id),
                "ldf_overrides": json.dumps({"f.xlsx": {"Paid Claims Triangle": [1.0]}}),
                "ldf_selection": "not json",
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 400, res.content)
        mocked_delay.assert_not_called()

    @patch("processing.views.run_module1_update_reserve_task.delay")
    def test_the_ldf_selection_is_optional(self, mocked_delay):
        """A hand-typed LDF row carries no basis; the override must still be accepted."""
        source = _make_successful_source(user=self.user, org=self.org)
        res = self.client.post(
            "/api/module1/jobs/update-reserve/",
            {
                "source_job_id": str(source.id),
                "ldf_overrides": json.dumps({"f.xlsx": {"Paid Claims Triangle": [1.0]}}),
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 202, res.content)
        job = Module1Job.objects.get(pk=res.json()["id"])
        self.assertNotIn("ldf_selection", job.input_meta)

    @patch("processing.views.run_module1_update_reserve_task.delay")
    def test_update_reserve_ldf_overrides_require_source(self, mocked_delay):
        res = self.client.post(
            "/api/module1/jobs/update-reserve/",
            {"ldf_overrides": json.dumps({"f.xlsx": {}})},
            format="multipart",
        )
        self.assertEqual(res.status_code, 400, res.content)
        mocked_delay.assert_not_called()

    @patch("processing.views.run_module1_update_reserve_task.delay")
    def test_update_reserve_method_overrides_require_source(self, mocked_delay):
        res = self.client.post(
            "/api/module1/jobs/update-reserve/",
            {"method_overrides": json.dumps({"f.xlsx": {}})},
            format="multipart",
        )
        self.assertEqual(res.status_code, 400, res.content)
        mocked_delay.assert_not_called()

    @patch("processing.views.run_module1_update_reserve_task.delay")
    def test_update_reserve_method_overrides_reject_with_files(self, mocked_delay):
        source = _make_successful_source(user=self.user, org=self.org)
        reserve = io.BytesIO(_xlsx_bytes("Reserve"))
        reserve.name = "ABC_GROSS.xlsx"
        res = self.client.post(
            "/api/module1/jobs/update-reserve/",
            {
                "reserve": reserve,
                "source_job_id": str(source.id),
                "method_overrides": json.dumps({"f.xlsx": {}}),
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 400, res.content)
        mocked_delay.assert_not_called()

    def test_uw_preview_from_source(self):
        source = _make_successful_source(user=self.user, org=self.org)
        # The default workbook lacks the structure the UW preview requires,
        # so this should reach the engine and produce a 400 about workbook
        # contents — but importantly NOT a 400 about chaining inputs.
        res = self.client.post(
            "/api/module1/combined-summary/uw-preview/",
            {"source_job_id": str(source.id)},
            format="multipart",
        )
        self.assertIn(res.status_code, (200, 400))
        if res.status_code == 400:
            self.assertNotIn("Provide either", str(res.content))


# ---------------------------------------------------------------------------
# Source candidates endpoint
# ---------------------------------------------------------------------------


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class SourceCandidatesTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.org = Organization.objects.create(name="Acme2", slug="acme2")
        self.user = _make_org_user(
            username="eve",
            perm_keys=["module1.run", "module2.run", "runhistory.view"],
            org=self.org,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_lists_only_jobs_with_artifact_in_output(self):
        # Successful summary with CS — included.
        _make_successful_source(user=self.user, org=self.org)
        # Successful job whose ZIP contains a different artifact — excluded.
        _make_successful_source(
            user=self.user, org=self.org,
            job_type=Module1Job.JobType.UPDATE_RESERVE,
            artifacts=["Reserve.xlsx"],
        )
        # Failed job — excluded.
        Module1Job.objects.create(
            user=self.user,
            organization=self.org,
            job_type=Module1Job.JobType.SUMMARY,
            status=Module1Job.Status.FAILED,
            work_dir="module1_jobs/x",
            input_meta={},
        )
        # Purged job — excluded.
        _make_successful_source(
            user=self.user, org=self.org, output_purged=True
        )

        res = self.client.get(
            "/api/processing/source-candidates/"
            f"?artifact={ARTIFACT_COMBINED_SUMMARY}"
        )
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(len(body["results"]), 1)
        self.assertEqual(body["results"][0]["job_type"], "summary")

    def test_is_org_scoped(self):
        # Create a successful job in another org.
        other_org = Organization.objects.create(name="Other2", slug="other2")
        other_user = User.objects.create_user(
            username="frank2", password="testpass123"
        )
        other_user.profile.active_organization = other_org
        other_user.profile.save(update_fields=["active_organization"])
        _make_successful_source(user=other_user, org=other_org)

        res = self.client.get(
            "/api/processing/source-candidates/"
            f"?artifact={ARTIFACT_COMBINED_SUMMARY}"
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["count"], 0)

    def test_rejects_unknown_artifact(self):
        res = self.client.get(
            "/api/processing/source-candidates/?artifact=Bogus.xlsx"
        )
        self.assertEqual(res.status_code, 400)


# ---------------------------------------------------------------------------
# Lineage protection
# ---------------------------------------------------------------------------


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class LineageProtectTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.org = Organization.objects.create(name="Acme3", slug="acme3")
        self.user = _make_org_user(
            username="gina", perm_keys=["module1.run"], org=self.org,
        )

    def test_protect_blocks_source_deletion_when_descendants_exist(self):
        from django.db.models.deletion import ProtectedError

        source = _make_successful_source(user=self.user, org=self.org)
        Module1Job.objects.create(
            user=self.user,
            organization=self.org,
            job_type=Module1Job.JobType.MODULE2_ALLOCATE,
            status=Module1Job.Status.PENDING,
            work_dir="module1_jobs/desc",
            input_meta={},
            source_job=source,
            source_artifact=ARTIFACT_COMBINED_SUMMARY,
        )

        with self.assertRaises(ProtectedError):
            source.delete()


# ---------------------------------------------------------------------------
# Retention sweeper + cascade purge
# ---------------------------------------------------------------------------


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class RetentionAndCascadeTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.org = Organization.objects.create(name="Acme4", slug="acme4")
        self.user = _make_org_user(
            username="harry", perm_keys=["module1.run"], org=self.org,
        )

    def test_sweeper_purges_expired_output_only(self):
        from datetime import timedelta
        from django.utils import timezone

        from processing.services.retention import purge_expired_outputs

        expired = _make_successful_source(user=self.user, org=self.org)
        expired.retention_until = timezone.now() - timedelta(days=1)
        expired.save(update_fields=["retention_until"])

        held = _make_successful_source(user=self.user, org=self.org)
        held.retention_until = timezone.now() - timedelta(days=1)
        held.legal_hold = True
        held.save(update_fields=["retention_until", "legal_hold"])

        future = _make_successful_source(user=self.user, org=self.org)
        future.retention_until = timezone.now() + timedelta(days=30)
        future.save(update_fields=["retention_until"])

        result = purge_expired_outputs(batch_size=100)
        self.assertEqual(result["purged"], 1, result)

        expired.refresh_from_db()
        held.refresh_from_db()
        future.refresh_from_db()
        self.assertIsNotNone(expired.output_purged_at)
        self.assertIsNone(held.output_purged_at)
        self.assertIsNone(future.output_purged_at)

    def test_cascade_purge_removes_subtree(self):
        from processing.services.retention import cascade_purge

        root = _make_successful_source(user=self.user, org=self.org)
        child = Module1Job.objects.create(
            user=self.user,
            organization=self.org,
            job_type=Module1Job.JobType.MODULE2_ALLOCATE,
            status=Module1Job.Status.SUCCESS,
            work_dir="module1_jobs/child",
            input_meta={},
            source_job=root,
            source_artifact=ARTIFACT_COMBINED_SUMMARY,
            output_artifacts=[ARTIFACT_COMBINED_SUMMARY],
        )
        child.output_zip.save(
            f"{child.id}.zip",
            ContentFile(_zip_with({ARTIFACT_COMBINED_SUMMARY: _xlsx_bytes()})),
            save=True,
        )

        result = cascade_purge(root, actor=self.user)
        self.assertEqual(result["deleted_rows"], 2)
        self.assertFalse(Module1Job.objects.filter(pk=root.pk).exists())
        self.assertFalse(Module1Job.objects.filter(pk=child.pk).exists())

    def test_cascade_purge_respects_legal_hold(self):
        from processing.services.retention import cascade_purge

        root = _make_successful_source(user=self.user, org=self.org)
        child = Module1Job.objects.create(
            user=self.user,
            organization=self.org,
            job_type=Module1Job.JobType.MODULE2_ALLOCATE,
            status=Module1Job.Status.SUCCESS,
            work_dir="module1_jobs/childlh",
            input_meta={},
            source_job=root,
            source_artifact=ARTIFACT_COMBINED_SUMMARY,
            legal_hold=True,
        )

        with self.assertRaises(PermissionError):
            cascade_purge(root, actor=self.user)

        # Both still present.
        self.assertTrue(Module1Job.objects.filter(pk=root.pk).exists())
        self.assertTrue(Module1Job.objects.filter(pk=child.pk).exists())


# ---------------------------------------------------------------------------
# Serializer surface
# ---------------------------------------------------------------------------


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class SerializerSurfaceTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.org = Organization.objects.create(name="Acme5", slug="acme5")
        self.user = _make_org_user(
            username="ivy",
            perm_keys=["module1.run", "runhistory.view"],
            org=self.org,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_detail_includes_lineage_and_retention_fields(self):
        source = _make_successful_source(user=self.user, org=self.org)
        derived = Module1Job.objects.create(
            user=self.user,
            organization=self.org,
            job_type=Module1Job.JobType.UW_PARAMETERS,
            status=Module1Job.Status.SUCCESS,
            work_dir="module1_jobs/derived",
            input_meta={},
            source_job=source,
            source_artifact=ARTIFACT_COMBINED_SUMMARY,
            output_artifacts=[ARTIFACT_COMBINED_SUMMARY],
        )
        derived.output_zip.save(
            f"{derived.id}.zip",
            ContentFile(_zip_with({ARTIFACT_COMBINED_SUMMARY: _xlsx_bytes()})),
            save=True,
        )
        res = self.client.get(f"/api/module1/jobs/{derived.id}/")
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body["source"]["job_id"], str(source.id))
        self.assertEqual(body["source"]["artifact"], ARTIFACT_COMBINED_SUMMARY)
        self.assertIn(ARTIFACT_COMBINED_SUMMARY, body["output_artifacts"])
        self.assertIn("retention_until", body)
        self.assertIn("legal_hold", body)
        self.assertIn("output_purged_at", body)
