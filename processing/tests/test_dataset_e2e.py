"""End-to-end tests for dataset-driven job execution.

Unlike the API tests in processing/tests/test_chaining_api.py which
mock `.delay()` and never run the engine, these tests submit a real
job via the HTTP layer and then call the Celery task synchronously
via `.apply()`. The engine is invoked for real against xlsx files
the adapter renders from dataset snapshots.

The point: catch any mismatch between the xlsx pandas/openpyxl write
(via the engine adapter) and what the engine pipeline expects when it
reads those files back in. This is the single highest-risk seam in the
Excel-free path — column dtypes, date parsing, string-vs-numeric
amounts, NaN handling.
"""

import io
import shutil
import tempfile
import zipfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Permission, Role
from datasets.models import (
    ClaimsOSRow,
    ClaimsPaidRow,
    Dataset,
    PremiumRow,
)
from processing.models import Module1Job
from processing.tasks import (
    run_module1_policy_upr_task,
    run_module1_summary_task,
)
from tenants.models import Membership, Organization

User = get_user_model()

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="sigma17-e2e-test-")


def _make_user(*, username: str, org: Organization, perm_keys: list[str]) -> User:
    user = User.objects.create_user(username=username, password="testpass123")
    user.profile.active_organization = org
    user.profile.save(update_fields=["active_organization"])
    role, _ = Role.objects.get_or_create(name=f"role-{username}")
    for key in perm_keys:
        perm, _ = Permission.objects.get_or_create(
            key=key,
            defaults={"name": key, "module": "Test", "description": ""},
        )
        role.permissions.add(perm)
    m = Membership.objects.create(user=user, organization=org, status="active")
    m.roles.add(role)
    return user


def _seed_premium_rows(dataset: Dataset) -> None:
    """A small but realistic Premium dataset spanning two reserving
    classes and two underwriting years. Date coverage is wide enough
    that the engine's quarterly slicing finds non-empty groups."""
    rows = [
        # Motor, UWY 2023, GROSS
        {
            "policy_number": "MOT-001",
            "policy_start_date": "2023-01-01",
            "policy_end_date": "2023-12-31",
            "risk_start_date": "2023-01-01",
            "risk_end_date": "2023-12-31",
            "issue_date": "2022-12-15",
            "reserving_class": "Motor",
            "policy_class": "Motor",
            "product_type": "Comprehensive",
            "ri_treaty_type": "GROSS",
            "premium_amount": Decimal("12000.00"),
            "commission_amount": Decimal("1200.00"),
        },
        # Motor, UWY 2023, RI
        {
            "policy_number": "MOT-002",
            "policy_start_date": "2023-01-01",
            "policy_end_date": "2023-12-31",
            "risk_start_date": "2023-01-01",
            "risk_end_date": "2023-12-31",
            "issue_date": "2022-12-20",
            "reserving_class": "Motor",
            "policy_class": "Motor",
            "product_type": "Comprehensive",
            "ri_treaty_type": "RI",
            "premium_amount": Decimal("4000.00"),
            "commission_amount": Decimal("200.00"),
        },
        # Motor, UWY 2024
        {
            "policy_number": "MOT-003",
            "policy_start_date": "2024-01-01",
            "policy_end_date": "2024-12-31",
            "risk_start_date": "2024-01-01",
            "risk_end_date": "2024-12-31",
            "issue_date": "2023-12-15",
            "reserving_class": "Motor",
            "policy_class": "Motor",
            "product_type": "Comprehensive",
            "ri_treaty_type": "GROSS",
            "premium_amount": Decimal("15000.00"),
            "commission_amount": Decimal("1500.00"),
        },
        # Property, UWY 2023
        {
            "policy_number": "PROP-001",
            "policy_start_date": "2023-06-01",
            "policy_end_date": "2024-05-31",
            "risk_start_date": "2023-06-01",
            "risk_end_date": "2024-05-31",
            "issue_date": "2023-05-15",
            "reserving_class": "Property",
            "policy_class": "Property",
            "product_type": "Fire",
            "ri_treaty_type": "GROSS",
            "premium_amount": Decimal("25000.00"),
            "commission_amount": Decimal("2500.00"),
        },
        # Property, UWY 2023, RI
        {
            "policy_number": "PROP-002",
            "policy_start_date": "2023-06-01",
            "policy_end_date": "2024-05-31",
            "risk_start_date": "2023-06-01",
            "risk_end_date": "2024-05-31",
            "issue_date": "2023-05-20",
            "reserving_class": "Property",
            "policy_class": "Property",
            "product_type": "Fire",
            "ri_treaty_type": "RI",
            "premium_amount": Decimal("10000.00"),
            "commission_amount": Decimal("500.00"),
        },
        # Policies in-force past EOP (2024-12-31) so the Policy UPR
        # engine has both treaty types still active after its EOP filter.
        {
            "policy_number": "MOT-2025-G",
            "policy_start_date": "2024-07-01",
            "policy_end_date": "2025-06-30",
            "risk_start_date": "2024-07-01",
            "risk_end_date": "2025-06-30",
            "issue_date": "2024-06-15",
            "reserving_class": "Motor",
            "policy_class": "Motor",
            "product_type": "Comprehensive",
            "ri_treaty_type": "GROSS",
            "premium_amount": Decimal("18000.00"),
            "commission_amount": Decimal("1800.00"),
        },
        {
            "policy_number": "MOT-2025-R",
            "policy_start_date": "2024-07-01",
            "policy_end_date": "2025-06-30",
            "risk_start_date": "2024-07-01",
            "risk_end_date": "2025-06-30",
            "issue_date": "2024-06-20",
            "reserving_class": "Motor",
            "policy_class": "Motor",
            "product_type": "Comprehensive",
            "ri_treaty_type": "RI",
            "premium_amount": Decimal("6000.00"),
            "commission_amount": Decimal("300.00"),
        },
    ]
    for i, row in enumerate(rows):
        PremiumRow.objects.create(dataset=dataset, row_index=i, **row)
    dataset.refresh_row_count()


def _seed_claims_paid_rows(dataset: Dataset) -> None:
    rows = [
        {
            "amount_paid": Decimal("500.00"),
            "amount_recovered": Decimal("0.00"),
            "issue_date": "2022-12-15",
            "loss_date": "2023-03-10",
            "payment_date": "2023-04-15",
            "reserving_class": "Motor",
            "policy_class": "Motor",
            "ri_treaty_type": "GROSS",
            "head_of_damage": "OD",
        },
        {
            "amount_paid": Decimal("200.00"),
            "amount_recovered": Decimal("0.00"),
            "issue_date": "2022-12-20",
            "loss_date": "2023-05-10",
            "payment_date": "2023-06-15",
            "reserving_class": "Motor",
            "policy_class": "Motor",
            "ri_treaty_type": "RI",
            "head_of_damage": "OD",
        },
        {
            "amount_paid": Decimal("1500.00"),
            "amount_recovered": Decimal("0.00"),
            "issue_date": "2023-05-15",
            "loss_date": "2023-08-01",
            "payment_date": "2023-09-15",
            "reserving_class": "Property",
            "policy_class": "Property",
            "ri_treaty_type": "GROSS",
            "head_of_damage": "Fire",
        },
    ]
    for i, row in enumerate(rows):
        ClaimsPaidRow.objects.create(dataset=dataset, row_index=i, **row)
    dataset.refresh_row_count()


def _seed_claims_os_rows(dataset: Dataset) -> None:
    rows = [
        {
            "amount_outstanding": Decimal("300.00"),
            "issue_date": "2022-12-15",
            "loss_date": "2023-09-10",
            "as_at": "2023-12-31",
            "reserving_class": "Motor",
            "policy_class": "Motor",
            "ri_treaty_type": "GROSS",
            "head_of_damage": "OD",
        },
        {
            "amount_outstanding": Decimal("100.00"),
            "issue_date": "2022-12-20",
            "loss_date": "2023-10-10",
            "as_at": "2023-12-31",
            "reserving_class": "Motor",
            "policy_class": "Motor",
            "ri_treaty_type": "RI",
            "head_of_damage": "OD",
        },
        {
            "amount_outstanding": Decimal("800.00"),
            "issue_date": "2023-05-15",
            "loss_date": "2023-11-01",
            "as_at": "2023-12-31",
            "reserving_class": "Property",
            "policy_class": "Property",
            "ri_treaty_type": "GROSS",
            "head_of_damage": "Fire",
        },
    ]
    for i, row in enumerate(rows):
        ClaimsOSRow.objects.create(dataset=dataset, row_index=i, **row)
    dataset.refresh_row_count()


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class SummaryDatasetEndToEndTests(TestCase):
    """Submit a Summary job via the API using dataset_ids, then run
    the Celery task synchronously. The test passes only if the engine
    successfully consumes the adapter's xlsx files and writes a real
    output ZIP containing Combined_Summary.xlsx."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.org = Organization.objects.create(name="E2E Org")
        self.user = _make_user(
            username="e2e-user",
            org=self.org,
            perm_keys=["module1.run", "datasets.view"],
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        # Build the three datasets.
        self.premium_ds = Dataset.objects.create(
            organization=self.org,
            kind=Dataset.Kind.PREMIUM,
            name="E2E Premium",
            created_by=self.user,
        )
        _seed_premium_rows(self.premium_ds)
        self.claims_paid_ds = Dataset.objects.create(
            organization=self.org,
            kind=Dataset.Kind.CLAIMS_PAID,
            name="E2E Claims Paid",
            created_by=self.user,
        )
        _seed_claims_paid_rows(self.claims_paid_ds)
        self.claims_os_ds = Dataset.objects.create(
            organization=self.org,
            kind=Dataset.Kind.CLAIMS_OS,
            name="E2E Claims OS",
            created_by=self.user,
        )
        _seed_claims_os_rows(self.claims_os_ds)

    def test_summary_engine_consumes_dataset_xlsx_end_to_end(self):
        """The full chain: HTTP submit → snapshot → adapter writes
        xlsx → Celery task runs engine → output ZIP exists with
        Combined_Summary.xlsx inside."""
        resp = self.client.post(
            "/api/module1/jobs/summary/",
            data={
                "exp_start": "01-01-2023",
                "exp_end": "31-12-2023",
                "bop": "01-01-2024",
                "eop": "31-12-2024",
                "premium_dataset_ids": str(self.premium_ds.id),
                "claims_paid_dataset_ids": str(self.claims_paid_ds.id),
                "claims_os_dataset_ids": str(self.claims_os_ds.id),
            },
            format="multipart",
        )
        self.assertEqual(resp.status_code, 202, resp.data)
        job_id = resp.data["id"]

        # Run the task synchronously. `apply()` returns an EagerResult
        # whose .traceback is the exception the engine raised (if any).
        result = run_module1_summary_task.apply(args=[job_id])
        if result.failed():
            self.fail(
                f"Celery task raised:\n{result.traceback}\n"
                f"The engine could not consume dataset-derived xlsx — see traceback above."
            )

        job = Module1Job.objects.get(pk=job_id)
        self.assertEqual(
            job.status,
            Module1Job.Status.SUCCESS,
            f"Job ended in status={job.status}, error={job.error_message!r}",
        )
        self.assertTrue(
            job.output_zip,
            "output_zip was not saved on the job after success",
        )
        # Read the ZIP and confirm Combined_Summary.xlsx is present —
        # this is the contract every downstream consumer relies on.
        with job.output_zip.open("rb") as f:
            raw = f.read()
        with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
            names = zf.namelist()
        self.assertIn(
            "Combined_Summary.xlsx",
            names,
            f"Output ZIP is missing Combined_Summary.xlsx. Contents: {names}",
        )


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class PolicyUprDatasetEndToEndTests(TestCase):
    """Smaller-scope end-to-end test for Policy UPR (premium only)."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.org = Organization.objects.create(name="PUPR E2E Org")
        self.user = _make_user(
            username="pupr-user",
            org=self.org,
            perm_keys=["module1.run", "datasets.view"],
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.premium_ds = Dataset.objects.create(
            organization=self.org,
            kind=Dataset.Kind.PREMIUM,
            name="PUPR Premium",
            created_by=self.user,
        )
        _seed_premium_rows(self.premium_ds)

    def test_policy_upr_engine_consumes_dataset_xlsx_end_to_end(self):
        resp = self.client.post(
            "/api/module1/jobs/policy-upr/",
            data={
                "bop": "01-01-2024",
                "eop": "31-12-2024",
                "premium_dataset_ids": str(self.premium_ds.id),
            },
            format="multipart",
        )
        self.assertEqual(resp.status_code, 202, resp.data)
        job_id = resp.data["id"]

        result = run_module1_policy_upr_task.apply(args=[job_id])
        if result.failed():
            self.fail(
                f"Celery task raised:\n{result.traceback}\n"
                f"Policy UPR engine could not consume dataset-derived xlsx."
            )

        job = Module1Job.objects.get(pk=job_id)
        self.assertEqual(
            job.status,
            Module1Job.Status.SUCCESS,
            f"Job ended in status={job.status}, error={job.error_message!r}",
        )
        self.assertTrue(job.output_zip)
