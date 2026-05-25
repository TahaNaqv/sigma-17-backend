"""End-to-end API tests for the Datasets app.

Covers:
- Tenant isolation (cross-org leakage)
- Lock semantics (writes blocked, reads allowed)
- Single + bulk + replace + bulk-delete row flows
- Clone / fork
- Permission gating per role (view vs edit vs lock vs delete)

These follow the same fixture pattern as processing/tests/test_chaining_api.py.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Permission, Role
from datasets.models import Dataset, PremiumRow
from tenants.models import Membership, Organization

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_org(name: str) -> Organization:
    return Organization.objects.create(name=name)


def _make_user(
    *,
    username: str,
    perm_keys: list[str],
    org: Organization,
) -> User:
    user = User.objects.create_user(username=username, password="testpass123")
    user.profile.active_organization = org
    user.profile.save(update_fields=["active_organization"])
    role, _ = Role.objects.get_or_create(name=f"role-{username}")
    for key in perm_keys:
        perm, _ = Permission.objects.get_or_create(
            key=key,
            defaults={"name": key, "module": "Datasets", "description": ""},
        )
        role.permissions.add(perm)
    m = Membership.objects.create(user=user, organization=org, status="active")
    m.roles.add(role)
    return user


def _client_for(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _premium_row_payload(**overrides) -> dict:
    base = {
        "policy_number": "POL-001",
        "policy_start_date": "2024-01-01",
        "policy_end_date": "2024-12-31",
        "risk_start_date": "2024-01-01",
        "risk_end_date": "2024-12-31",
        "issue_date": "2023-12-15",
        "reserving_class": "Motor",
        "policy_class": "Motor",
        "product_type": "Comprehensive",
        "ri_treaty_type": "GROSS",
        "premium_amount": "1000.00",
        "commission_amount": "100.00",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class TenantIsolationTests(TestCase):
    """Datasets created in one org must be invisible to other orgs."""

    def setUp(self):
        self.org_a = _make_org("Org A")
        self.org_b = _make_org("Org B")
        self.user_a = _make_user(
            username="alice",
            perm_keys=["datasets.view", "datasets.edit"],
            org=self.org_a,
        )
        self.user_b = _make_user(
            username="bob",
            perm_keys=["datasets.view", "datasets.edit"],
            org=self.org_b,
        )
        self.dataset_a = Dataset.objects.create(
            organization=self.org_a,
            kind=Dataset.Kind.PREMIUM,
            name="Org A Premium",
            created_by=self.user_a,
        )

    def test_other_org_user_cannot_see_dataset_in_list(self):
        resp = _client_for(self.user_b).get("/api/datasets/")
        self.assertEqual(resp.status_code, 200)
        ids = [d["id"] for d in resp.data["results"]]
        self.assertNotIn(str(self.dataset_a.id), ids)

    def test_other_org_user_cannot_retrieve_dataset_detail(self):
        resp = _client_for(self.user_b).get(f"/api/datasets/{self.dataset_a.id}/")
        self.assertEqual(resp.status_code, 404)

    def test_other_org_user_cannot_post_rows(self):
        resp = _client_for(self.user_b).post(
            f"/api/datasets/{self.dataset_a.id}/rows/",
            data=_premium_row_payload(),
            format="json",
        )
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# CRUD happy paths
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class DatasetCrudTests(TestCase):
    def setUp(self):
        self.org = _make_org("Test Org")
        self.user = _make_user(
            username="actuary",
            perm_keys=[
                "datasets.view",
                "datasets.edit",
                "datasets.lock",
                "datasets.delete",
            ],
            org=self.org,
        )
        self.client = _client_for(self.user)

    def test_create_dataset(self):
        resp = self.client.post(
            "/api/datasets/",
            data={"kind": "premium", "name": "Q1 2024 Premium"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["kind"], "premium")
        self.assertEqual(resp.data["status"], "draft")
        self.assertEqual(resp.data["row_count"], 0)
        self.assertEqual(resp.data["source"], "manual")

    def test_create_rejects_blank_name(self):
        resp = self.client.post(
            "/api/datasets/",
            data={"kind": "premium", "name": "   "},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_create_rejects_duplicate_name_per_kind(self):
        r1 = self.client.post(
            "/api/datasets/",
            data={"kind": "premium", "name": "Dup"},
            format="json",
        )
        self.assertEqual(r1.status_code, 201)
        r2 = self.client.post(
            "/api/datasets/",
            data={"kind": "premium", "name": "Dup"},
            format="json",
        )
        self.assertEqual(r2.status_code, 400)
        self.assertIn("name", r2.data)

    def test_list_filters_by_kind(self):
        Dataset.objects.create(
            organization=self.org, kind=Dataset.Kind.PREMIUM, name="P1"
        )
        Dataset.objects.create(
            organization=self.org, kind=Dataset.Kind.CLAIMS_PAID, name="C1"
        )
        resp = self.client.get("/api/datasets/?kind=premium")
        self.assertEqual(resp.status_code, 200)
        kinds = {d["kind"] for d in resp.data["results"]}
        self.assertEqual(kinds, {"premium"})

    def test_patch_updates_name(self):
        ds = Dataset.objects.create(
            organization=self.org, kind=Dataset.Kind.PREMIUM, name="Old"
        )
        resp = self.client.patch(
            f"/api/datasets/{ds.id}/",
            data={"name": "New"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["name"], "New")

    def test_delete_draft_succeeds(self):
        ds = Dataset.objects.create(
            organization=self.org, kind=Dataset.Kind.PREMIUM, name="Doomed"
        )
        resp = self.client.delete(f"/api/datasets/{ds.id}/")
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(Dataset.objects.filter(id=ds.id).exists())


# ---------------------------------------------------------------------------
# Lock semantics
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class LockSemanticsTests(TestCase):
    def setUp(self):
        self.org = _make_org("Lock Org")
        self.user = _make_user(
            username="locker",
            perm_keys=[
                "datasets.view",
                "datasets.edit",
                "datasets.lock",
                "datasets.delete",
            ],
            org=self.org,
        )
        self.client = _client_for(self.user)
        self.ds = Dataset.objects.create(
            organization=self.org,
            kind=Dataset.Kind.PREMIUM,
            name="Lockable",
            created_by=self.user,
        )

    def test_lock_is_idempotent(self):
        url = f"/api/datasets/{self.ds.id}/lock/"
        r1 = self.client.post(url)
        r2 = self.client.post(url)
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.data["status"], "locked")

    def test_locked_dataset_rejects_patch(self):
        self.client.post(f"/api/datasets/{self.ds.id}/lock/")
        resp = self.client.patch(
            f"/api/datasets/{self.ds.id}/",
            data={"name": "New name"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_locked_dataset_rejects_row_insert(self):
        self.client.post(f"/api/datasets/{self.ds.id}/lock/")
        resp = self.client.post(
            f"/api/datasets/{self.ds.id}/rows/",
            data=_premium_row_payload(),
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_locked_dataset_rejects_delete(self):
        self.client.post(f"/api/datasets/{self.ds.id}/lock/")
        resp = self.client.delete(f"/api/datasets/{self.ds.id}/")
        self.assertEqual(resp.status_code, 400)

    def test_locked_dataset_still_allows_reads(self):
        self.client.post(f"/api/datasets/{self.ds.id}/lock/")
        resp = self.client.get(f"/api/datasets/{self.ds.id}/")
        self.assertEqual(resp.status_code, 200)
        resp2 = self.client.get(f"/api/datasets/{self.ds.id}/rows/")
        self.assertEqual(resp2.status_code, 200)


# ---------------------------------------------------------------------------
# Row flows — single, bulk, replace, bulk-delete
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class RowFlowsTests(TestCase):
    def setUp(self):
        self.org = _make_org("Rows Org")
        self.user = _make_user(
            username="rower",
            perm_keys=["datasets.view", "datasets.edit", "datasets.delete"],
            org=self.org,
        )
        self.client = _client_for(self.user)
        self.ds = Dataset.objects.create(
            organization=self.org,
            kind=Dataset.Kind.PREMIUM,
            name="Rows DS",
            created_by=self.user,
        )

    def test_append_single_row_increments_count(self):
        resp = self.client.post(
            f"/api/datasets/{self.ds.id}/rows/",
            data=_premium_row_payload(),
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.ds.refresh_from_db()
        self.assertEqual(self.ds.row_count, 1)
        self.assertEqual(resp.data["row_index"], 0)

    def test_bulk_insert_validates_each_row_and_reports_index(self):
        resp = self.client.post(
            f"/api/datasets/{self.ds.id}/rows/bulk/",
            data={
                "rows": [
                    _premium_row_payload(),
                    # Bad row: missing required reserving_class
                    {**_premium_row_payload(), "reserving_class": ""},
                ]
            },
            format="json",
        )
        # Even one bad row should reject the whole batch (atomic).
        self.assertEqual(resp.status_code, 400)
        self.assertIn("rows", resp.data)
        self.assertEqual(PremiumRow.objects.filter(dataset=self.ds).count(), 0)

    def test_bulk_insert_succeeds_and_assigns_sequential_indexes(self):
        resp = self.client.post(
            f"/api/datasets/{self.ds.id}/rows/bulk/",
            data={
                "rows": [
                    _premium_row_payload(policy_number="A"),
                    _premium_row_payload(policy_number="B"),
                    _premium_row_payload(policy_number="C"),
                ]
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["created"], 3)
        self.ds.refresh_from_db()
        self.assertEqual(self.ds.row_count, 3)
        indexes = sorted(
            PremiumRow.objects.filter(dataset=self.ds).values_list("row_index", flat=True)
        )
        self.assertEqual(indexes, [0, 1, 2])

    def test_replace_clears_then_inserts(self):
        # Seed with 3 rows
        self.client.post(
            f"/api/datasets/{self.ds.id}/rows/bulk/",
            data={"rows": [_premium_row_payload() for _ in range(3)]},
            format="json",
        )
        resp = self.client.post(
            f"/api/datasets/{self.ds.id}/rows/replace/",
            data={"rows": [_premium_row_payload(policy_number="ONLY")]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["replaced_with"], 1)
        self.ds.refresh_from_db()
        self.assertEqual(self.ds.row_count, 1)
        remaining = list(PremiumRow.objects.filter(dataset=self.ds))
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].policy_number, "ONLY")

    def test_bulk_delete_with_all_true(self):
        self.client.post(
            f"/api/datasets/{self.ds.id}/rows/bulk/",
            data={"rows": [_premium_row_payload() for _ in range(5)]},
            format="json",
        )
        resp = self.client.post(
            f"/api/datasets/{self.ds.id}/rows/bulk-delete/",
            data={"all": True},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["deleted"], 5)
        self.ds.refresh_from_db()
        self.assertEqual(self.ds.row_count, 0)

    def test_bulk_delete_with_ids(self):
        # Seed and grab some ids back
        self.client.post(
            f"/api/datasets/{self.ds.id}/rows/bulk/",
            data={"rows": [_premium_row_payload() for _ in range(3)]},
            format="json",
        )
        all_ids = list(
            PremiumRow.objects.filter(dataset=self.ds).values_list("id", flat=True)
        )
        keep = all_ids[0]
        remove = all_ids[1:]
        resp = self.client.post(
            f"/api/datasets/{self.ds.id}/rows/bulk-delete/",
            data={"row_ids": remove},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["deleted"], 2)
        remaining = list(
            PremiumRow.objects.filter(dataset=self.ds).values_list("id", flat=True)
        )
        self.assertEqual(remaining, [keep])

    def test_patch_single_row(self):
        post_resp = self.client.post(
            f"/api/datasets/{self.ds.id}/rows/",
            data=_premium_row_payload(),
            format="json",
        )
        row_id = post_resp.data["id"]
        resp = self.client.patch(
            f"/api/datasets/{self.ds.id}/rows/{row_id}/",
            data={"premium_amount": "9999.00"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["premium_amount"], "9999.00")

    def test_delete_single_row_decrements_count(self):
        post_resp = self.client.post(
            f"/api/datasets/{self.ds.id}/rows/",
            data=_premium_row_payload(),
            format="json",
        )
        row_id = post_resp.data["id"]
        resp = self.client.delete(f"/api/datasets/{self.ds.id}/rows/{row_id}/")
        self.assertEqual(resp.status_code, 204)
        self.ds.refresh_from_db()
        self.assertEqual(self.ds.row_count, 0)


# ---------------------------------------------------------------------------
# Clone
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class CloneTests(TestCase):
    def setUp(self):
        self.org = _make_org("Clone Org")
        self.user = _make_user(
            username="cloner",
            perm_keys=["datasets.view", "datasets.edit"],
            org=self.org,
        )
        self.client = _client_for(self.user)
        self.ds = Dataset.objects.create(
            organization=self.org,
            kind=Dataset.Kind.PREMIUM,
            name="Source",
            created_by=self.user,
        )
        self.client.post(
            f"/api/datasets/{self.ds.id}/rows/bulk/",
            data={"rows": [_premium_row_payload() for _ in range(3)]},
            format="json",
        )
        # Lock the source so the clone path is realistic
        Dataset.objects.filter(id=self.ds.id).update(status=Dataset.Status.LOCKED)

    def test_clone_creates_draft_with_copied_rows(self):
        resp = self.client.post(
            f"/api/datasets/{self.ds.id}/clone/",
            data={"name": "Source (forked)"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["status"], "draft")
        self.assertEqual(resp.data["row_count"], 3)
        self.assertEqual(str(resp.data["forked_from"]), str(self.ds.id))


# ---------------------------------------------------------------------------
# Permission gating
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class PermissionGatingTests(TestCase):
    def setUp(self):
        self.org = _make_org("Perms Org")
        self.viewer = _make_user(
            username="viewer",
            perm_keys=["datasets.view"],
            org=self.org,
        )
        self.editor = _make_user(
            username="editor",
            perm_keys=["datasets.view", "datasets.edit"],
            org=self.org,
        )

    def test_viewer_can_list_but_not_create(self):
        resp = _client_for(self.viewer).get("/api/datasets/")
        self.assertEqual(resp.status_code, 200)
        resp = _client_for(self.viewer).post(
            "/api/datasets/",
            data={"kind": "premium", "name": "Should fail"},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_editor_without_lock_permission_cannot_lock(self):
        ds = Dataset.objects.create(
            organization=self.org, kind=Dataset.Kind.PREMIUM, name="Locktest"
        )
        resp = _client_for(self.editor).post(f"/api/datasets/{ds.id}/lock/")
        self.assertEqual(resp.status_code, 403)
