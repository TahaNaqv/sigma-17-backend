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


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class TriangleFilterVocabularyTests(TestCase):
    """The page populates its class and treaty pickers from the triangle response itself.

    Served from the UNFILTERED frame on purpose: if the lists were derived after filtering,
    choosing a class would empty the list that offered it and the user could not get back.
    """

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.org = Organization.objects.create(name="TFV", slug="tfv")
        self.user = User.objects.create_user("tfv", "tfv@example.com", "pw")
        _give_role(self.user, "ActuaryTFV", ["module1.run"], self.org)
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
        from urllib.parse import urlencode

        return self.client.get(
            f"/api/module1/jobs/{self.job.id}/triangles/?{urlencode(params)}"
        )

    def test_the_response_carries_the_classes_present_in_the_data(self):
        body = self._get(grain="quarterly").json()
        self.assertIn("Motor Insurance", body["reserving_classes"])
        self.assertIn("GROSS", body["treaties"])

    def test_filtering_does_not_shrink_the_vocabulary(self):
        unfiltered = self._get(grain="quarterly").json()["reserving_classes"]
        filtered = self._get(
            grain="quarterly", reserving_class="Motor Insurance"
        ).json()["reserving_classes"]
        self.assertEqual(unfiltered, filtered)


OS_DIR = (
    __import__("pathlib").Path(__file__).resolve().parents[2]
    / "benchmarks" / "fixtures" / "summary_ref" / "claims_os"
)
PREMIUM_DIR = (
    __import__("pathlib").Path(__file__).resolve().parents[2]
    / "benchmarks" / "fixtures" / "summary_ref" / "premium"
)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class TrianglesBasisAndExposureTests(TrianglesApiTests):
    """WP8.4 — the reported basis, the head-of-damage filter, and earned premium.

    The reference extract is `diagonal` (every valuation lists only that period's own claims),
    so it is the case where the reported basis must be REFUSED with a reason. That is the
    behaviour worth pinning hardest: returning paid figures under a "reported" label, or a
    triangle of nulls that looks like a rendering fault, are both worse than a clear 400.
    """

    def _job(self):
        job = super()._job()
        from processing.utils import job_input_subdir

        for folder, kind in ((OS_DIR, "claims_os"), (PREMIUM_DIR, "premium")):
            if not folder.is_dir():
                continue
            dest = job_input_subdir(job, kind)
            for f in folder.glob("*.xlsx"):
                shutil.copy(f, dest / f.name)
        return job

    def test_the_paid_basis_is_the_default_and_is_unchanged(self):
        resp = self._get(grain="quarterly")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["basis"], "paid")
        self.assertIsNone(resp.data.get("coverage"))

    def test_an_unknown_basis_is_rejected(self):
        resp = self._get(grain="quarterly", basis="incurred")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("basis", resp.data["fieldErrors"])

    def test_the_reported_basis_is_refused_with_the_reason_on_this_extract(self):
        resp = self._get(grain="quarterly", basis="reported")
        self.assertEqual(resp.status_code, 400, resp.data)
        detail = str(resp.data["fieldErrors"]["basis"])
        self.assertIn("only the claims that occurred in that same period", detail)
        self.assertIn("full open-claim inventory", detail)

    def test_the_refusal_names_the_valuation_dates_it_found(self):
        """The message must stand alone: the error envelope carries strings, so a client
        showing only the detail still learns what the extract holds."""
        detail = str(
            self._get(grain="quarterly", basis="reported").data["fieldErrors"]["basis"]
        )
        self.assertIn("2017-Q1, 2017-Q2, 2017-Q3, 2017-Q4", detail)

    def test_the_head_of_damage_filter_narrows_the_triangle(self):
        everything = self._get(grain="quarterly").data
        self.assertIn("Payment", everything["heads_of_damage"])
        narrowed = self._get(grain="quarterly", head_of_damage="Payment").data
        self.assertEqual(narrowed["head_of_damage"], "Payment")
        # Claim COUNT, not money: on this book the Salvage rows carry no usable amount at all
        # (see F7 — the recovery substitution never fires), so excluding them moves the count
        # without moving a penny.
        self.assertLess(
            narrowed["triangle"]["credibility"]["claims"],
            everything["triangle"]["credibility"]["claims"],
        )

    def test_an_unknown_head_of_damage_empties_rather_than_errors(self):
        resp = self._get(grain="quarterly", head_of_damage="Nonexistent")
        self.assertEqual(resp.status_code, 200, resp.data)

    def test_earned_premium_is_opt_in(self):
        self.assertNotIn("earned_premium", self._get(grain="quarterly").data)
        payload = self._get(grain="quarterly", include="ep").data["earned_premium"]
        self.assertTrue(payload["available"], payload)
        self.assertEqual(len(payload["labels"]), 8)
        self.assertEqual(len(payload["ep"]), 8)
        self.assertIn("as booked", payload["basis"])

    def test_earned_premium_follows_the_grain(self):
        for grain, periods in (("monthly", 24), ("quarterly", 8), ("yearly", 2)):
            payload = self._get(grain=grain, include="ep").data["earned_premium"]
            self.assertEqual(len(payload["labels"]), periods, grain)

    def test_earned_premium_explains_itself_when_premium_was_not_retained(self):
        """Every job made before WP8.6 is in this state; an empty panel would read as a bug."""
        from processing.utils import job_input_subdir

        for f in job_input_subdir(self.job, "premium").glob("*.xlsx"):
            f.unlink()
        payload = self._get(grain="quarterly", include="ep").data["earned_premium"]
        self.assertFalse(payload["available"])
        self.assertIn("not retained", payload["reason"])

    def test_earned_premium_is_computed_once_per_job_grain_and_filter(self):
        """The premium workbook is the most expensive input the app reads — 30.4 MB in
        production — so the second request for the same view must not parse it again."""
        from unittest.mock import patch

        from django.core.cache import cache

        cache.clear()
        with patch(
            "processing.views._premium_source_frame",
            wraps=__import__("processing.views", fromlist=["x"])._premium_source_frame,
        ) as loader:
            self._get(grain="quarterly", include="ep")
            self._get(grain="quarterly", include="ep")
            self.assertEqual(loader.call_count, 1, "the premium frame was re-read")

            # A different grain is a different answer, so it is computed rather than served
            # from the quarterly entry.
            self._get(grain="monthly", include="ep")
            self.assertEqual(loader.call_count, 2)

            # As is a different slice of the same grain. The class name carries a space,
            # which is exactly what a concatenated cache key cannot hold.
            self._get(grain="quarterly", include="ep", reserving_class="Motor%20Insurance")
            self.assertEqual(loader.call_count, 3)
        cache.clear()

    def test_a_cached_absence_still_explains_itself(self):
        """The explained-absence path is cached too; it must not degrade to a bare empty."""
        from django.core.cache import cache
        from processing.utils import job_input_subdir

        cache.clear()
        for f in job_input_subdir(self.job, "premium").glob("*.xlsx"):
            f.unlink()
        first = self._get(grain="quarterly", include="ep").data["earned_premium"]
        second = self._get(grain="quarterly", include="ep").data["earned_premium"]
        self.assertEqual(first, second)
        self.assertIn("not retained", second["reason"])
        cache.clear()

    def test_coverage_can_be_asked_for_without_switching_basis(self):
        data = self._get(grain="quarterly", include="coverage").data
        self.assertIn("coverage", data)

    def test_the_refusal_names_the_grain_that_would_work(self):
        """Refusing is only half an answer. An extract valued at year-ends supports a yearly
        reported triangle perfectly well, and the user has no other way to discover that than
        by trying each button."""
        from unittest.mock import patch

        import pandas as pd

        # An annual-valuation inventory: usable at yearly, not at quarterly.
        quarters = [f"{y}Q{q}" for y in (2016, 2017) for q in range(1, 5)]
        inventory = pd.DataFrame([
            {
                "LOSSDATE": pd.Period(q, freq="Q").start_time,
                "As at": pd.Timestamp(v),
                "Amount": 500.0,
                "RESERVINGCLASS": "Motor Insurance",
                "RI_TREATY_TYPE": "GROSS",
                "HEADOFDAMAGE": "Payment",
            }
            for v in ("2016-12-31", "2017-12-31")
            for q in quarters
            if pd.Period(q, freq="Q") <= pd.Period(pd.Timestamp(v), freq="Q")
        ])
        with patch("processing.views._os_source_frame", return_value=inventory):
            detail = str(
                self._get(grain="quarterly", basis="reported").data["fieldErrors"]["basis"]
            )
            self.assertIn("Not sufficient information", detail)
            self.assertIn("available at yearly", detail)

            ok = self._get(grain="yearly", basis="reported")
            self.assertEqual(ok.status_code, 200, ok.data)
            self.assertEqual(ok.data["coverage"]["shape"], "inventory")
