"""WP3b-lite — the cash flow projection view (requirement 3).

Exists because `FutureCF` ships at 2,476 x 30 = 74,280 cells against a 20,000-cell
preview guard: the projection was in the ZIP but unreadable in-app.
"""

import io
import shutil
import tempfile

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Permission, Role
from datasets.models import Dataset, PaymentPatternRow
from processing.models import Module1Job
from tenants.models import Membership, Organization

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="sigma17-test-media-cfproj-")


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
class CashflowProjectionTests(TestCase):
    FIXTURE = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "benchmarks" / "fixtures" / "m2_allocate_ref" / "Combined_Summary.xlsx"
    )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        if not self.FIXTURE.is_file():
            self.skipTest("reference fixture not available")
        self.org = Organization.objects.create(name="CF", slug="cf")
        self.user = User.objects.create_user("cf", "cf@example.com", "pw")
        _give_role(self.user, "ActuaryCF", ["module2.run", "datasets.view"], self.org)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _allocate_job(self, pattern_ds=None):
        from processing.tasks import run_module2_allocate_task
        from processing.utils import init_module2_allocate_job_dirs

        job = Module1Job.objects.create(
            user=self.user, organization=self.org,
            job_type=Module1Job.JobType.MODULE2_ALLOCATE,
        )
        job.work_dir = f"module1_jobs/{job.id}"
        job.save(update_fields=["work_dir"])
        combined_dir = init_module2_allocate_job_dirs(job)
        (combined_dir / "Combined_Summary.xlsx").write_bytes(self.FIXTURE.read_bytes())
        meta = {"files": {}}
        if pattern_ds is not None:
            from datasets.services.snapshots import create_snapshot
            snap = create_snapshot(dataset=pattern_ds, consumer_job=job)
            meta["dataset_snapshots"] = {"payment_pattern": [str(snap.id)]}
            meta["pattern_mode"] = "shape_only"
        job.input_meta = meta
        job.save(update_fields=["input_meta"])
        run_module2_allocate_task(str(job.id))
        job.refresh_from_db()
        return job

    # -- the reason this endpoint exists ------------------------------------

    def test_default_grain_fits_inside_the_preview_guard(self):
        """The native FutureCF sheet does not — that is the whole point."""
        job = self._allocate_job()
        self.assertEqual(job.status, Module1Job.Status.SUCCESS, job.error_message)
        resp = self.client.get(f"/api/module2/jobs/{job.id}/cashflow-projection/")
        self.assertEqual(resp.status_code, 200, resp.data)
        cells = len(resp.data["rows"]) * len(resp.data["periods"])
        self.assertLess(cells, settings.MODULE1_OUTPUT_PREVIEW_MAX_CELLS)
        # Native grain would be ~74k cells; confirm we are far below it.
        self.assertLess(cells, 5000)

    def test_totals_reconcile_to_the_engine(self):
        job = self._allocate_job()
        resp = self.client.get(f"/api/module2/jobs/{job.id}/cashflow-projection/")
        totals = resp.data["totals"]
        row_sum = sum(r["total_undiscounted"] for r in resp.data["rows"])
        self.assertAlmostEqual(row_sum, totals["undiscounted"], places=4)
        self.assertAlmostEqual(
            totals["discounting_impact"],
            totals["discounted"] - totals["undiscounted"],
            places=6,
        )
        # Discounting must be a benefit on a positive reserve.
        self.assertLess(totals["discounting_impact"], 0)

    def test_every_grain_produces_the_same_totals(self):
        """Aggregation must not create or destroy value."""
        job = self._allocate_job()
        seen = {}
        for grain in ("class", "class_treaty", "class_uwy", "class_uwy_treaty"):
            resp = self.client.get(
                f"/api/module2/jobs/{job.id}/cashflow-projection/?grain={grain}"
            )
            self.assertEqual(resp.status_code, 200, resp.data)
            seen[grain] = resp.data["totals"]["undiscounted"]
            self.assertEqual(resp.data["key_columns"][0], "RESERVINGCLASS")
        values = list(seen.values())
        for v in values[1:]:
            self.assertAlmostEqual(v, values[0], places=4, msg=seen)

    def test_finer_grain_yields_more_rows(self):
        job = self._allocate_job()
        counts = {}
        for grain in ("class", "class_uwy", "class_uwy_treaty"):
            resp = self.client.get(
                f"/api/module2/jobs/{job.id}/cashflow-projection/?grain={grain}"
            )
            counts[grain] = len(resp.data["rows"])
        self.assertLess(counts["class"], counts["class_uwy"])
        self.assertLess(counts["class_uwy"], counts["class_uwy_treaty"])

    # -- correctness: the view must show what the job RAN with ---------------

    def test_the_view_reflects_the_jobs_own_pattern_override(self):
        """Recomputing without the job's override would show a projection that
        disagrees with the workbook the job actually produced."""
        ds = Dataset.objects.create(
            organization=self.org, kind=Dataset.Kind.PAYMENT_PATTERN, name="Long tail"
        )
        weights = [0.85 ** k for k in range(26)]
        total = sum(weights)
        PaymentPatternRow.objects.bulk_create([
            PaymentPatternRow(dataset=ds, reserving_class="ENGINEERING",
                              dev_period=p, weight=w / total, row_index=p)
            for p, w in enumerate(weights)
        ])
        ds.refresh_row_count()

        plain = self._allocate_job()
        shocked = self._allocate_job(pattern_ds=ds)
        self.assertEqual(shocked.status, Module1Job.Status.SUCCESS, shocked.error_message)

        def _eng_row(job):
            resp = self.client.get(
                f"/api/module2/jobs/{job.id}/cashflow-projection/?grain=class"
            )
            self.assertEqual(resp.status_code, 200, resp.data)
            self.assertTrue(resp.data["rows"])
            return next(r for r in resp.data["rows"] if r["label"] == "ENGINEERING")

        before, after = _eng_row(plain), _eng_row(shocked)
        # Same money, different timing -> same undiscounted total, different profile.
        self.assertAlmostEqual(
            before["total_undiscounted"], after["total_undiscounted"], places=2
        )
        self.assertNotAlmostEqual(
            before["undiscounted"][0], after["undiscounted"][0], places=2
        )
        self.assertTrue(_eng_row.__name__)  # keep linters quiet about the closure
        resp = self.client.get(
            f"/api/module2/jobs/{shocked.id}/cashflow-projection/?grain=class"
        )
        self.assertTrue(resp.data["has_pattern_override"])

    def test_a_class_untouched_by_the_override_is_unchanged(self):
        ds = Dataset.objects.create(
            organization=self.org, kind=Dataset.Kind.PAYMENT_PATTERN, name="Eng only"
        )
        PaymentPatternRow.objects.bulk_create([
            PaymentPatternRow(dataset=ds, reserving_class="ENGINEERING",
                              dev_period=p, weight=w, row_index=p)
            for p, w in enumerate([0.4, 0.3, 0.2, 0.1])
        ])
        ds.refresh_row_count()
        plain = self._allocate_job()
        shocked = self._allocate_job(pattern_ds=ds)

        def _row(job, label):
            resp = self.client.get(
                f"/api/module2/jobs/{job.id}/cashflow-projection/?grain=class"
            )
            return next(r for r in resp.data["rows"] if r["label"] == label)

        self.assertAlmostEqual(
            _row(plain, "MARINE")["undiscounted"][0],
            _row(shocked, "MARINE")["undiscounted"][0],
            places=6,
        )

    # -- guards --------------------------------------------------------------

    def test_unknown_grain_is_rejected(self):
        job = self._allocate_job()
        resp = self.client.get(
            f"/api/module2/jobs/{job.id}/cashflow-projection/?grain=galaxy"
        )
        self.assertEqual(resp.status_code, 400)

    def test_requires_a_successful_job(self):
        job = Module1Job.objects.create(
            user=self.user, organization=self.org,
            job_type=Module1Job.JobType.MODULE2_ALLOCATE,
        )
        resp = self.client.get(f"/api/module2/jobs/{job.id}/cashflow-projection/")
        self.assertEqual(resp.status_code, 400)

    def test_rejects_a_non_module2_job_type(self):
        job = Module1Job.objects.create(
            user=self.user, organization=self.org,
            job_type=Module1Job.JobType.MODULE2_SENSITIVITY,
            status=Module1Job.Status.SUCCESS,
        )
        resp = self.client.get(f"/api/module2/jobs/{job.id}/cashflow-projection/")
        self.assertEqual(resp.status_code, 400)

    def test_a_process_job_without_an_allocate_ancestor_is_reported_clearly(self):
        job = Module1Job.objects.create(
            user=self.user, organization=self.org,
            job_type=Module1Job.JobType.MODULE2_PROCESS,
            status=Module1Job.Status.SUCCESS,
            source_job=None,
        )
        resp = self.client.get(f"/api/module2/jobs/{job.id}/cashflow-projection/")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("allocate ancestor", str(resp.data))
