"""Tests for dataset-driven Module1 job submission.

We mock the Celery `.delay()` so the engine never runs; instead we
verify the request shape, that snapshots are taken atomically at submit
time, and that the staging side-effects are correct.
"""

import io
import shutil
import tempfile
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from openpyxl import Workbook
from rest_framework.test import APIClient

from accounts.models import Permission, Role
from datasets.models import (
    ClaimsOSRow,
    ClaimsPaidRow,
    Dataset,
    DatasetSnapshot,
    PremiumRow,
)
from processing.models import Module1Job
from processing.tasks import _materialize_job_snapshots
from processing.utils import init_job_work_dir, job_input_subdir
from tenants.models import Membership, Organization

User = get_user_model()

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="sigma17-job-ds-test-")


def _make_org(name: str) -> Organization:
    return Organization.objects.create(name=name)


def _make_user(*, username: str, perm_keys: list[str], org: Organization) -> User:
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


def _seed_premium_dataset(org, user) -> Dataset:
    ds = Dataset.objects.create(
        organization=org,
        kind=Dataset.Kind.PREMIUM,
        name="P1",
        created_by=user,
    )
    PremiumRow.objects.create(
        dataset=ds,
        row_index=0,
        reserving_class="Motor",
        ri_treaty_type="GROSS",
        premium_amount=Decimal("1000.00"),
    )
    ds.refresh_row_count()
    return ds


def _seed_claims_paid_dataset(org, user) -> Dataset:
    ds = Dataset.objects.create(
        organization=org,
        kind=Dataset.Kind.CLAIMS_PAID,
        name="CP1",
        created_by=user,
    )
    ClaimsPaidRow.objects.create(
        dataset=ds,
        row_index=0,
        reserving_class="Motor",
        ri_treaty_type="GROSS",
        amount_paid=Decimal("500.00"),
    )
    ds.refresh_row_count()
    return ds


def _seed_claims_os_dataset(org, user) -> Dataset:
    ds = Dataset.objects.create(
        organization=org,
        kind=Dataset.Kind.CLAIMS_OS,
        name="OS1",
        created_by=user,
    )
    ClaimsOSRow.objects.create(
        dataset=ds,
        row_index=0,
        reserving_class="Motor",
        ri_treaty_type="GROSS",
        amount_outstanding=Decimal("250.00"),
    )
    ds.refresh_row_count()
    return ds


def _file(name: str = "x.xlsx") -> io.BytesIO:
    """A throwaway xlsx for file-mode tests."""
    wb = Workbook()
    ws = wb.active
    ws.append(["RESERVINGCLASS", "RI_TREATY_TYPE"])
    ws.append(["Motor", "GROSS"])
    buf = io.BytesIO()
    wb.save(buf)
    out = io.BytesIO(buf.getvalue())
    out.name = name
    return out


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class SummaryDatasetSubmissionTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.org = _make_org("Sum Org")
        self.user = _make_user(
            username="summer",
            perm_keys=["module1.run", "datasets.view"],
            org=self.org,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @patch("processing.views.run_module1_summary_task.delay")
    def test_summary_all_dataset_driven_creates_snapshots(self, _mocked):
        prem = _seed_premium_dataset(self.org, self.user)
        cp = _seed_claims_paid_dataset(self.org, self.user)
        os_ds = _seed_claims_os_dataset(self.org, self.user)
        resp = self.client.post(
            "/api/module1/jobs/summary/",
            data={
                "exp_start": "01-01-2024",
                "exp_end": "31-12-2024",
                "bop": "01-01-2024",
                "eop": "31-12-2024",
                "premium_dataset_ids": str(prem.id),
                "claims_paid_dataset_ids": str(cp.id),
                "claims_os_dataset_ids": str(os_ds.id),
            },
            format="multipart",
        )
        self.assertEqual(resp.status_code, 202, resp.data)
        job = Module1Job.objects.get(pk=resp.data["id"])
        snaps = job.input_meta.get("dataset_snapshots") or {}
        self.assertIn("premium", snaps)
        self.assertIn("claims_paid", snaps)
        self.assertIn("claims_os", snaps)
        # Each kind should have exactly one snapshot id
        self.assertEqual(len(snaps["premium"]), 1)
        # Snapshots exist in the DB and are linked to the job
        snap = DatasetSnapshot.objects.get(id=snaps["premium"][0])
        self.assertEqual(snap.consumer_job_id, job.id)
        self.assertEqual(snap.dataset_id, prem.id)
        # The source dataset is now locked (snapshot side-effect)
        prem.refresh_from_db()
        self.assertEqual(prem.status, Dataset.Status.LOCKED)

    @patch("processing.views.run_module1_summary_task.delay")
    def test_summary_mixed_mode_premium_dataset_claims_files(self, _mocked):
        prem = _seed_premium_dataset(self.org, self.user)
        resp = self.client.post(
            "/api/module1/jobs/summary/",
            data={
                "exp_start": "01-01-2024",
                "exp_end": "31-12-2024",
                "bop": "01-01-2024",
                "eop": "31-12-2024",
                "premium_dataset_ids": str(prem.id),
                "claims_paid": _file("cp.xlsx"),
                "claims_os": _file("os.xlsx"),
            },
            format="multipart",
        )
        self.assertEqual(resp.status_code, 202, resp.data)
        job = Module1Job.objects.get(pk=resp.data["id"])
        snaps = job.input_meta.get("dataset_snapshots") or {}
        self.assertIn("premium", snaps)
        self.assertNotIn("claims_paid", snaps)
        files = job.input_meta.get("files") or {}
        self.assertIn("claims_paid", files)
        self.assertIn("claims_os", files)

    @patch("processing.views.run_module1_summary_task.delay")
    def test_summary_rejects_both_files_and_dataset_for_same_kind(self, _mocked):
        prem = _seed_premium_dataset(self.org, self.user)
        resp = self.client.post(
            "/api/module1/jobs/summary/",
            data={
                "exp_start": "01-01-2024",
                "exp_end": "31-12-2024",
                "bop": "01-01-2024",
                "eop": "31-12-2024",
                "premium": _file("p.xlsx"),
                "premium_dataset_ids": str(prem.id),
                "claims_paid": _file("cp.xlsx"),
                "claims_os": _file("os.xlsx"),
            },
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("detail", resp.data)

    @patch("processing.views.run_module1_summary_task.delay")
    def test_summary_rejects_missing_kind(self, _mocked):
        # No premium files or dataset for premium
        resp = self.client.post(
            "/api/module1/jobs/summary/",
            data={
                "exp_start": "01-01-2024",
                "exp_end": "31-12-2024",
                "bop": "01-01-2024",
                "eop": "31-12-2024",
                "claims_paid": _file("cp.xlsx"),
                "claims_os": _file("os.xlsx"),
            },
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("premium", resp.data)

    @patch("processing.views.run_module1_summary_task.delay")
    def test_summary_rejects_dataset_from_other_org(self, _mocked):
        other_org = _make_org("Other")
        other_user = _make_user(
            username="thief",
            perm_keys=["datasets.view"],
            org=other_org,
        )
        foreign = _seed_premium_dataset(other_org, other_user)
        resp = self.client.post(
            "/api/module1/jobs/summary/",
            data={
                "exp_start": "01-01-2024",
                "exp_end": "31-12-2024",
                "bop": "01-01-2024",
                "eop": "31-12-2024",
                "premium_dataset_ids": str(foreign.id),
                "claims_paid": _file("cp.xlsx"),
                "claims_os": _file("os.xlsx"),
            },
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("premium_dataset_ids", resp.data)

    @patch("processing.views.run_module1_summary_task.delay")
    def test_summary_rejects_wrong_kind_for_field(self, _mocked):
        cp = _seed_claims_paid_dataset(self.org, self.user)
        resp = self.client.post(
            "/api/module1/jobs/summary/",
            data={
                "exp_start": "01-01-2024",
                "exp_end": "31-12-2024",
                "bop": "01-01-2024",
                "eop": "31-12-2024",
                # claims_paid dataset id passed where premium expected
                "premium_dataset_ids": str(cp.id),
                "claims_paid": _file("cp.xlsx"),
                "claims_os": _file("os.xlsx"),
            },
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("premium_dataset_ids", resp.data)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class PolicyUprDatasetSubmissionTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.org = _make_org("Pup Org")
        self.user = _make_user(
            username="pupper",
            perm_keys=["module1.run", "datasets.view"],
            org=self.org,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @patch("processing.views.run_module1_policy_upr_task.delay")
    def test_policy_upr_dataset_driven(self, _mocked):
        prem = _seed_premium_dataset(self.org, self.user)
        resp = self.client.post(
            "/api/module1/jobs/policy-upr/",
            data={
                "bop": "01-01-2024",
                "eop": "31-12-2024",
                "premium_dataset_ids": str(prem.id),
            },
            format="multipart",
        )
        self.assertEqual(resp.status_code, 202, resp.data)
        job = Module1Job.objects.get(pk=resp.data["id"])
        snaps = job.input_meta.get("dataset_snapshots") or {}
        self.assertEqual(len(snaps.get("premium", [])), 1)

    @patch("processing.views.run_module1_policy_upr_task.delay")
    def test_policy_upr_file_path_still_works(self, _mocked):
        resp = self.client.post(
            "/api/module1/jobs/policy-upr/",
            data={
                "bop": "01-01-2024",
                "eop": "31-12-2024",
                "premium": _file("p.xlsx"),
            },
            format="multipart",
        )
        self.assertEqual(resp.status_code, 202, resp.data)
        job = Module1Job.objects.get(pk=resp.data["id"])
        self.assertIn("premium", job.input_meta.get("files") or {})


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class TaskMaterializationTests(TestCase):
    """Directly exercise the `_materialize_job_snapshots` helper from
    processing.tasks. This is the unit that converts snapshots → xlsx
    files in the engine's staging dirs."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.org = _make_org("Mat Org")
        self.user = _make_user(
            username="mater",
            perm_keys=["module1.run", "datasets.view"],
            org=self.org,
        )

    def test_materialize_writes_xlsx_for_each_kind(self):
        prem = _seed_premium_dataset(self.org, self.user)
        cp = _seed_claims_paid_dataset(self.org, self.user)
        os_ds = _seed_claims_os_dataset(self.org, self.user)

        from datasets.services.snapshots import create_snapshot
        snap_p = create_snapshot(dataset=prem)
        snap_cp = create_snapshot(dataset=cp)
        snap_os = create_snapshot(dataset=os_ds)

        job = Module1Job.objects.create(
            user=self.user,
            organization=self.org,
            job_type=Module1Job.JobType.SUMMARY,
            work_dir=f"module1_jobs/test-{prem.id}",
            input_meta={
                "dataset_snapshots": {
                    "premium": [str(snap_p.id)],
                    "claims_paid": [str(snap_cp.id)],
                    "claims_os": [str(snap_os.id)],
                }
            },
        )
        init_job_work_dir(job)
        # Link snapshots back to the job (the production code does this
        # at view time; here we mirror it explicitly).
        DatasetSnapshot.objects.filter(
            id__in=[snap_p.id, snap_cp.id, snap_os.id]
        ).update(consumer_job=job)

        _materialize_job_snapshots(job)

        # Each kind folder has at least one xlsx file
        for kind in ("premium", "claims_paid", "claims_os"):
            folder = job_input_subdir(job, kind)
            files = list(folder.glob("*.xlsx"))
            self.assertGreaterEqual(len(files), 1, f"{kind} missing xlsx")
