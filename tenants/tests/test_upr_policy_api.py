"""WP2 — UPR method policy API, versioning and job snapshotting."""

import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Permission, Role
from processing.models import Module1Job
from tenants.models import Membership, Organization, UprMethodPolicy, UprMethodRule

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="sigma17-test-media-upr-")


def _give_role(user, role_name, perm_keys, org):
    user.profile.active_organization = org
    user.profile.save(update_fields=["active_organization"])
    role = Role.objects.create(name=role_name)
    for key in perm_keys:
        perm, _ = Permission.objects.get_or_create(
            key=key, defaults={"name": key, "module": "Processing", "description": ""}
        )
        role.permissions.add(perm)
    membership = Membership.objects.create(user=user, organization=org, status="active")
    membership.roles.add(role)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class UprPolicyApiTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Upr", slug="upr")
        self.user = User.objects.create_user("upr", "upr@example.com", "pw")
        _give_role(self.user, "ActuaryUpr",
                   ["module1.run", "upr_policy.view", "upr_policy.manage"], self.org)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_the_method_catalog_is_served_so_the_ui_never_hard_codes_it(self):
        resp = self.client.get("/api/upr-methods/")
        self.assertEqual(resp.status_code, 200, resp.data)
        keys = {m["key"] for m in resp.data["methods"]}
        self.assertEqual(keys, {
            "pro_rata_daily", "sum_of_digits", "full_premium_in_period",
            "eighths", "twenty_fourths", "flat_percentage",
        })
        guarded = {m["key"] for m in resp.data["methods"] if m["needsGuard"]}
        self.assertEqual(guarded, {"eighths", "twenty_fourths"})

    def test_create_list_and_fork_on_edit(self):
        resp = self.client.post("/api/upr-policies/", {
            "name": "House basis",
            "rules": [
                {"method": "pro_rata_daily"},
                {"method": "sum_of_digits", "reservingClass": "Engineering Insurance"},
            ],
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        pid = resp.data["id"]
        self.assertEqual(resp.data["version"], 1)
        self.assertEqual(len(resp.data["rules"]), 2)

        # Editing forks v2 and deactivates v1 — historic runs stay reproducible.
        resp = self.client.put(f"/api/upr-policies/{pid}/", {
            "name": "House basis",
            "rules": [{"method": "pro_rata_daily"}],
        }, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["version"], 2)
        self.assertFalse(UprMethodPolicy.objects.get(pk=pid).is_active)
        self.assertEqual(UprMethodPolicy.objects.filter(organization=self.org).count(), 2)

        self.assertEqual(len(self.client.get("/api/upr-policies/").data["results"]), 1)
        self.assertEqual(len(self.client.get("/api/upr-policies/?all=1").data["results"]), 2)

    def test_an_unknown_method_is_rejected(self):
        resp = self.client.post("/api/upr-policies/", {
            "name": "Bad", "rules": [{"method": "wishful_thinking"}],
        }, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_flat_percentage_requires_a_fraction_between_zero_and_one(self):
        for pct in ("abc", 1.5, -0.1):
            resp = self.client.post("/api/upr-policies/", {
                "name": f"Flat-{pct}",
                "rules": [{"method": "flat_percentage", "params": {"percent": pct}}],
            }, format="json")
            self.assertEqual(resp.status_code, 400, f"{pct} should be rejected")

    def test_lookback_months_is_bounded(self):
        resp = self.client.post("/api/upr-policies/", {
            "name": "Marine",
            "rules": [{"method": "full_premium_in_period",
                       "params": {"lookback_months": 99}}],
        }, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_guarded_methods_are_flagged_to_the_ui(self):
        resp = self.client.post("/api/upr-policies/", {
            "name": "Eighths",
            "rules": [{"method": "eighths", "reservingClass": "MOTOR"}],
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertTrue(resp.data["rules"][0]["needsGuard"])

    def test_a_runner_without_upr_policy_view_can_still_read_the_policy(self):
        """module1.run alone must be enough to READ — otherwise a runner cannot see which
        methodology their own job will use."""
        runner = User.objects.create_user("runner", "r@example.com", "pw")
        _give_role(runner, "RunOnly", ["module1.run"], self.org)
        client = APIClient()
        client.force_authenticate(runner)
        self.assertEqual(client.get("/api/upr-policies/").status_code, 200)
        self.assertEqual(client.get("/api/upr-methods/").status_code, 200)
        resp = client.post("/api/upr-policies/", {"name": "X", "rules": []}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_only_one_active_policy_per_name(self):
        UprMethodPolicy.objects.create(organization=self.org, name="Dup", version=1)
        with self.assertRaises(Exception):
            UprMethodPolicy.objects.create(organization=self.org, name="Dup", version=2)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class UprPolicySnapshotTests(TestCase):
    """A job must replay with the methodology it actually used."""

    def setUp(self):
        self.org = Organization.objects.create(name="Snap", slug="snap")
        self.user = User.objects.create_user("snap", "s@example.com", "pw")
        _give_role(self.user, "ActuarySnap",
                   ["module1.run", "upr_policy.view", "upr_policy.manage"], self.org)

    def _policy(self):
        p = UprMethodPolicy.objects.create(organization=self.org, name="House")
        UprMethodRule.objects.bulk_create([
            UprMethodRule(policy=p, method="pro_rata_daily", order=0),
            UprMethodRule(policy=p, method="sum_of_digits",
                          reserving_class="Engineering Insurance", order=1),
        ])
        return p

    def test_the_loader_rebuilds_the_policy_from_the_job_snapshot(self):
        from processing.tasks import _load_upr_policy

        policy = self._policy()
        job = Module1Job.objects.create(
            user=self.user, organization=self.org,
            job_type=Module1Job.JobType.SUMMARY,
            input_meta={"upr_policy": {
                "id": str(policy.id), "name": policy.name,
                "version": policy.version, "rules": policy.resolved(),
            }},
        )
        resolved = _load_upr_policy(job)
        self.assertIsNotNone(resolved)
        self.assertEqual(
            resolved.resolve("Engineering Insurance", "ERECTION ALL RISKS").method,
            "sum_of_digits",
        )
        self.assertEqual(resolved.resolve("MOTOR", "PRIVATE CAR").method, "pro_rata_daily")

    def test_a_job_without_a_policy_uses_the_engine_default(self):
        from processing.tasks import _load_upr_policy

        job = Module1Job.objects.create(
            user=self.user, organization=self.org,
            job_type=Module1Job.JobType.SUMMARY, input_meta={},
        )
        self.assertIsNone(_load_upr_policy(job))

    def test_deleting_the_policy_does_not_break_replay(self):
        from processing.tasks import _load_upr_policy

        policy = self._policy()
        job = Module1Job.objects.create(
            user=self.user, organization=self.org,
            job_type=Module1Job.JobType.SUMMARY,
            input_meta={"upr_policy": {
                "id": str(policy.id), "name": policy.name,
                "version": policy.version, "rules": policy.resolved(),
            }},
        )
        policy.delete()
        resolved = _load_upr_policy(job)
        self.assertIsNotNone(resolved)
        self.assertEqual(
            resolved.resolve("Engineering Insurance", "").method, "sum_of_digits"
        )
