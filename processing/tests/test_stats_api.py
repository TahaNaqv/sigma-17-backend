"""API tests for the dashboard stats endpoint (/api/processing/stats/).

Verifies the aggregates are computed from real job rows, scoped to the active
organization, and gated behind runhistory.view.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Permission, Role
from processing.models import Module1Job
from tenants.models import Membership, Organization

User = get_user_model()


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


def _files_meta(n: int) -> dict:
    return {"files": {"field": [{"name": f"f{i}.xlsx"} for i in range(n)]}}


class ProcessingStatsApiTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org A")
        self.other_org = Organization.objects.create(name="Org B")
        self.user = _make_org_user(
            username="stats-user", perm_keys=["runhistory.view"], org=self.org
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        now = timezone.now()
        # Success job: 2 input files, ran for 60s.
        Module1Job.objects.create(
            user=self.user,
            organization=self.org,
            job_type=Module1Job.JobType.SUMMARY,
            status=Module1Job.Status.SUCCESS,
            input_meta=_files_meta(2),
            started_at=now - timedelta(seconds=60),
            completed_at=now,
        )
        # Failed job: 1 input file, no timestamps.
        Module1Job.objects.create(
            user=self.user,
            organization=self.org,
            job_type=Module1Job.JobType.POLICY_UPR,
            status=Module1Job.Status.FAILED,
            input_meta=_files_meta(1),
        )
        # Another org's job must NOT leak into this org's stats.
        Module1Job.objects.create(
            user=self.user,
            organization=self.other_org,
            job_type=Module1Job.JobType.SUMMARY,
            status=Module1Job.Status.SUCCESS,
            input_meta=_files_meta(9),
        )

    def test_stats_aggregates_are_org_scoped_and_live(self):
        res = self.client.get("/api/processing/stats/?period=month")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["period"], "month")
        self.assertEqual(body["total_jobs"], 2)  # other-org job excluded
        self.assertEqual(body["files_processed"], 3)  # 2 + 1, not 9
        self.assertAlmostEqual(body["success_rate"], 0.5)  # 1 success / (1+1)
        self.assertAlmostEqual(body["avg_duration_seconds"], 60.0)
        # Latest run per job_type, newest first.
        self.assertEqual(len(body["last_runs"]), 2)
        job_types = {r["job_type"] for r in body["last_runs"]}
        self.assertEqual(
            job_types,
            {Module1Job.JobType.SUMMARY, Module1Job.JobType.POLICY_UPR},
        )

    def test_success_rate_and_avg_null_when_no_finished_jobs(self):
        Module1Job.objects.filter(organization=self.org).delete()
        Module1Job.objects.create(
            user=self.user,
            organization=self.org,
            job_type=Module1Job.JobType.SUMMARY,
            status=Module1Job.Status.RUNNING,
            input_meta=_files_meta(1),
        )
        body = self.client.get("/api/processing/stats/").json()
        self.assertEqual(body["total_jobs"], 1)
        self.assertIsNone(body["success_rate"])
        self.assertIsNone(body["avg_duration_seconds"])

    def test_requires_runhistory_permission(self):
        other = _make_org_user(
            username="no-perm", perm_keys=[], org=self.org
        )
        client = APIClient()
        client.force_authenticate(other)
        res = client.get("/api/processing/stats/")
        self.assertEqual(res.status_code, 403)
