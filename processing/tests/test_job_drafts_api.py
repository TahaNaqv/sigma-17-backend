"""API tests for wizard draft persistence (/api/processing/job-drafts/).

Verifies upsert semantics, per-(user, org) scoping, and the state size cap.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from processing.models import JobDraft
from tenants.models import Membership, Organization

User = get_user_model()


def _make_org_user(*, username: str, org: Organization) -> User:
    user = User.objects.create_user(username=username, password="testpass123")
    user.profile.active_organization = org
    user.profile.save(update_fields=["active_organization"])
    Membership.objects.create(user=user, organization=org, status="active")
    return user


@override_settings(SECURE_SSL_REDIRECT=False)
class JobDraftApiTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org A")
        self.other_org = Organization.objects.create(name="Org B")
        self.user = _make_org_user(username="drafter", org=self.org)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_put_creates_then_get_returns_draft(self):
        res = self.client.put(
            "/api/processing/job-drafts/",
            {"key": "summary", "state": {"step": 2, "updatedAt": "2026-07-07T00:00:00Z"}},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["key"], "summary")
        self.assertEqual(res.data["state"]["step"], 2)

        got = self.client.get("/api/processing/job-drafts/?key=summary")
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.data["state"]["step"], 2)

    def test_put_is_upsert_one_row_per_wizard(self):
        self.client.put(
            "/api/processing/job-drafts/",
            {"key": "summary", "state": {"step": 1}},
            format="json",
        )
        self.client.put(
            "/api/processing/job-drafts/",
            {"key": "summary", "state": {"step": 3}},
            format="json",
        )
        self.assertEqual(
            JobDraft.objects.filter(user=self.user, organization=self.org, key="summary").count(),
            1,
        )
        got = self.client.get("/api/processing/job-drafts/?key=summary")
        self.assertEqual(got.data["state"]["step"], 3)

    def test_get_missing_returns_204(self):
        res = self.client.get("/api/processing/job-drafts/?key=movement")
        self.assertEqual(res.status_code, 204)

    def test_delete_removes_draft(self):
        self.client.put(
            "/api/processing/job-drafts/",
            {"key": "ibnr_allocation", "state": {"step": 2}},
            format="json",
        )
        res = self.client.delete("/api/processing/job-drafts/?key=ibnr_allocation")
        self.assertEqual(res.status_code, 204)
        self.assertEqual(self.client.get("/api/processing/job-drafts/?key=ibnr_allocation").status_code, 204)

    def test_drafts_are_scoped_to_user(self):
        self.client.put(
            "/api/processing/job-drafts/",
            {"key": "summary", "state": {"secret": "u1"}},
            format="json",
        )
        other = _make_org_user(username="other-user", org=self.org)
        other_client = APIClient()
        other_client.force_authenticate(other)
        # Same org, different user -> cannot see the first user's draft.
        self.assertEqual(other_client.get("/api/processing/job-drafts/?key=summary").status_code, 204)

    def test_drafts_are_scoped_to_org(self):
        self.client.put(
            "/api/processing/job-drafts/",
            {"key": "summary", "state": {"secret": "orgA"}},
            format="json",
        )
        # Same user active in a different org context -> no draft there.
        self.user.profile.active_organization = self.other_org
        self.user.profile.save(update_fields=["active_organization"])
        Membership.objects.create(user=self.user, organization=self.other_org, status="active")
        self.assertEqual(self.client.get("/api/processing/job-drafts/?key=summary").status_code, 204)

    def test_rejects_unknown_key(self):
        res = self.client.put(
            "/api/processing/job-drafts/",
            {"key": "bogus", "state": {}},
            format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_rejects_oversized_state(self):
        big = {"blob": "x" * (256 * 1024 + 10)}
        res = self.client.put(
            "/api/processing/job-drafts/",
            {"key": "summary", "state": big},
            format="json",
        )
        self.assertEqual(res.status_code, 400)
