"""WP6 — the diagnostic triangle endpoint."""

import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Permission, Role
from processing.models import Module1Job
from tenants.models import Membership, Organization

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="sigma17-test-media-tri-")
CLAIMS_DIR = (
    __import__("pathlib").Path(__file__).resolve().parents[2]
    / "benchmarks" / "fixtures" / "summary_ref" / "claims_paid"
)


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
class TrianglesApiTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        if not CLAIMS_DIR.is_dir():
            self.skipTest("reference fixture not available")
        self.org = Organization.objects.create(name="Tri", slug="tri")
        self.user = User.objects.create_user("tri", "tri@example.com", "pw")
        _give_role(self.user, "ActuaryTri", ["module1.run"], self.org)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.job = self._job()

    def _job(self):
        from processing.utils import init_job_work_dir, job_input_subdir

        job = Module1Job.objects.create(
            user=self.user, organization=self.org,
            job_type=Module1Job.JobType.SUMMARY,
            status=Module1Job.Status.SUCCESS,
            input_meta={"exp_start": "01-01-2016", "exp_end": "31-12-2017"},
        )
        job.work_dir = f"module1_jobs/{job.id}"
        job.save(update_fields=["work_dir"])
        init_job_work_dir(job)
        dest = job_input_subdir(job, "claims_paid")
        for f in CLAIMS_DIR.glob("*.xlsx"):
            shutil.copy(f, dest / f.name)
        return job

    def _get(self, **params):
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return self.client.get(f"/api/module1/jobs/{self.job.id}/triangles/?{query}")

    def test_every_grain_returns_a_triangle_with_credibility(self):
        for grain, periods in (("monthly", 24), ("quarterly", 8), ("yearly", 2)):
            resp = self._get(grain=grain)
            self.assertEqual(resp.status_code, 200, resp.data)
            tri = resp.data["triangle"]
            self.assertEqual(len(tri["accident_labels"]), periods)
            self.assertIn("level", tri["credibility"])

    def test_all_grains_carry_the_same_money(self):
        totals = {}
        for grain in ("monthly", "quarterly", "yearly"):
            tri = self._get(grain=grain).data["triangle"]
            totals[grain] = sum(
                v for row in tri["incremental"] for v in row if v is not None
            )
        self.assertAlmostEqual(totals["monthly"], totals["quarterly"], places=2)
        self.assertAlmostEqual(totals["yearly"], totals["quarterly"], places=2)

    def test_quarterly_scores_better_than_monthly_on_this_book(self):
        q = self._get(grain="quarterly").data["triangle"]["credibility"]
        m = self._get(grain="monthly").data["triangle"]["credibility"]
        self.assertEqual(q["level"], "high")
        self.assertEqual(m["level"], "medium")
        self.assertGreater(q["median_claims_per_cell"], m["median_claims_per_cell"])

    def test_an_unknown_grain_is_rejected(self):
        self.assertEqual(self._get(grain="fortnightly").status_code, 400)

    def test_implied_cdf_is_refused_at_the_booking_grain(self):
        resp = self._get(grain="quarterly", imply_cdf=1)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("FINER grain", str(resp.data))

    def test_implied_cdf_from_monthly_returns_coarse_labels(self):
        resp = self._get(grain="monthly", imply_cdf=1)
        self.assertEqual(resp.status_code, 200, resp.data)
        implied = resp.data["implied"]
        self.assertEqual(len(implied["labels"]), 8)
        self.assertEqual(implied["labels"][0], "2016-Q1")
        tail = dict(zip(implied["labels"], implied["implied_cdf"]))["2017-Q4"]
        self.assertAlmostEqual(tail, 69.8101, places=3)

    def test_a_sparse_class_refuses_to_imply_factors(self):
        resp = self._get(
            grain="monthly", imply_cdf=1,
            reserving_class="Banker%27s%20Blanket", treaty="GROSS",
        )
        # Either the filter yields too little data or the guard fires; both are a 400.
        self.assertEqual(resp.status_code, 400)

    def test_filtering_by_class_reduces_the_claim_count(self):
        whole = self._get(grain="quarterly").data["triangle"]["credibility"]["claims"]
        part = self._get(grain="quarterly", treaty="GROSS").data["triangle"]["credibility"]["claims"]
        self.assertLess(part, whole)

    def test_requires_a_successful_summary_job(self):
        job = Module1Job.objects.create(
            user=self.user, organization=self.org,
            job_type=Module1Job.JobType.SUMMARY,
            input_meta={"exp_start": "01-01-2016", "exp_end": "31-12-2017"},
        )
        resp = self.client.get(f"/api/module1/jobs/{job.id}/triangles/")
        self.assertEqual(resp.status_code, 400)

    def test_a_module2_job_is_not_reachable_through_this_endpoint(self):
        """404 rather than 400: `_get_accessible_job` scopes to Module 1 job types, so a
        Module 2 job is not addressable here at all."""
        job = Module1Job.objects.create(
            user=self.user, organization=self.org,
            job_type=Module1Job.JobType.MODULE2_ALLOCATE,
            status=Module1Job.Status.SUCCESS,
        )
        resp = self.client.get(f"/api/module1/jobs/{job.id}/triangles/")
        self.assertEqual(resp.status_code, 404)

    def test_rejects_a_non_summary_module1_job(self):
        job = Module1Job.objects.create(
            user=self.user, organization=self.org,
            job_type=Module1Job.JobType.POLICY_UPR,
            status=Module1Job.Status.SUCCESS,
        )
        resp = self.client.get(f"/api/module1/jobs/{job.id}/triangles/")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Reserve Summary", str(resp.data))
