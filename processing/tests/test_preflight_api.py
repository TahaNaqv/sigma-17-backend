"""WP0 — the pre-flight endpoint, the gate, and reserving-class aliases end to end.

These run the real reference fixtures, because the defect they guard is only visible against
real data: `Health` in the claims files, `Health Insurance` in premium, 3,044 rows discarded
without a word.
"""

import shutil
import tempfile

import pandas as pd
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Permission, Role
from processing.models import Module1Job
from tenants.models import Membership, Organization, ReservingClassAlias, alias_map_for

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="sigma17-test-media-pf-")
FIXTURES = (
    __import__("pathlib").Path(__file__).resolve().parents[2]
    / "benchmarks" / "fixtures" / "summary_ref"
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


def _first_xlsx(kind):
    return sorted((FIXTURES / kind).glob("*.xlsx"))[0]


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class PreflightEndpointTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.org = Organization.objects.create(name="PF", slug="pf")
        self.user = User.objects.create_user("pf", "pf@example.com", "pw")
        _give_role(
            self.user,
            "ActuaryPF",
            ["module1.run", "class_alias.view", "class_alias.manage"],
            self.org,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _post(self):
        with (
            _first_xlsx("premium").open("rb") as p,
            _first_xlsx("claims_paid").open("rb") as cp,
            _first_xlsx("claims_os").open("rb") as co,
        ):
            return self.client.post(
                "/api/module1/preflight/",
                {"premium": p, "claims_paid": cp, "claims_os": co},
                format="multipart",
            )

    def test_it_reports_the_reference_defect_without_creating_a_job(self):
        before = Module1Job.objects.count()
        res = self._post()
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body["severity"], "error")
        self.assertTrue(body["would_block"])
        self.assertGreater(body["dropped_row_count"], 0)
        self.assertGreater(body["dropped_amount"], 0)
        self.assertEqual(Module1Job.objects.count(), before, "pre-flight must create nothing")

    def test_it_proposes_the_health_alias(self):
        body = self._post().json()
        pairs = {(s["alias"], s["canonical"]) for s in body["suggestions"]}
        self.assertIn(("Health", "Health Insurance"), pairs)

    def test_an_alias_clears_the_block(self):
        ReservingClassAlias.objects.create(
            organization=self.org, alias="Health", canonical="Health Insurance"
        )
        body = self._post().json()
        self.assertFalse(body["would_block"], body["messages"])
        self.assertEqual(body["dropped_row_count"], 0)
        self.assertEqual(body["aliases_applied"], {"Health": "Health Insurance"})

    def test_permissive_mode_reports_but_does_not_block(self):
        self.org.preflight_mode = Organization.PreflightMode.PERMISSIVE
        self.org.save(update_fields=["preflight_mode"])
        body = self._post().json()
        self.assertEqual(body["severity"], "error")
        self.assertFalse(body["would_block"])
        self.assertEqual(body["mode"], "permissive")

    def test_it_refuses_an_empty_request_rather_than_reporting_a_clean_book(self):
        res = self.client.post("/api/module1/preflight/", {}, format="multipart")
        self.assertEqual(res.status_code, 400, res.content)

    def test_running_the_job_requires_module1_run(self):
        other = User.objects.create_user("nopf", "nopf@example.com", "pw")
        _give_role(other, "ViewerPF", ["dashboard.view"], self.org)
        client = APIClient()
        client.force_authenticate(other)
        with _first_xlsx("premium").open("rb") as p:
            res = client.post("/api/module1/preflight/", {"premium": p}, format="multipart")
        self.assertIn(res.status_code, (403, 404), res.content)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class AliasModelTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="AL", slug="al")

    def test_alias_map_is_empty_by_default_so_runs_stay_bit_identical(self):
        self.assertEqual(alias_map_for(self.org), {})

    def test_an_alias_that_only_differs_by_case_is_rejected(self):
        alias = ReservingClassAlias(
            organization=self.org, alias="health insurance", canonical="Health Insurance"
        )
        with self.assertRaises(Exception) as ctx:
            alias.full_clean(exclude=["alias_key"])
        self.assertIn("no alias is needed", str(ctx.exception))

    def test_a_chain_is_rejected(self):
        """A->B, B->C would make the resolved value depend on iteration order."""
        ReservingClassAlias.objects.create(
            organization=self.org, alias="Health", canonical="Health Insurance"
        )
        chained = ReservingClassAlias(
            organization=self.org, alias="HI", canonical="Health"
        )
        with self.assertRaises(Exception) as ctx:
            chained.full_clean(exclude=["alias_key"])
        self.assertIn("itself an alias", str(ctx.exception))

    def test_one_alias_per_value_per_org(self):
        from django.db import IntegrityError

        ReservingClassAlias.objects.create(
            organization=self.org, alias="Health", canonical="Health Insurance"
        )
        with self.assertRaises(IntegrityError):
            ReservingClassAlias.objects.create(
                organization=self.org, alias="  HEALTH  ", canonical="Motor Insurance"
            )

    def test_the_same_alias_may_exist_in_a_different_org(self):
        other = Organization.objects.create(name="AL2", slug="al2")
        ReservingClassAlias.objects.create(
            organization=self.org, alias="Health", canonical="Health Insurance"
        )
        ReservingClassAlias.objects.create(
            organization=other, alias="Health", canonical="Health Insurance"
        )
        self.assertEqual(ReservingClassAlias.objects.count(), 2)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class PreflightGateTests(TestCase):
    """The gate, run through the real Celery task body (called synchronously).

    A unit test on the report proves the defect is *detected*. Only this proves it is
    *blocked* — and WP5 taught the lesson that a correct property nothing consumes is worth
    nothing.
    """

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.org = Organization.objects.create(name="GT", slug="gt")
        self.user = User.objects.create_user("gt", "gt@example.com", "pw")
        _give_role(self.user, "ActuaryGT", ["module1.run"], self.org)

    def _staged_job(self):
        """A summary job with the reference fixtures staged where the engine expects them."""
        from processing.utils import init_job_work_dir, job_input_subdir

        job = Module1Job.objects.create(
            user=self.user,
            organization=self.org,
            job_type=Module1Job.JobType.SUMMARY,
            input_meta={
                "exp_start": "01-01-2016", "exp_end": "31-12-2017",
                "bop": "01-01-2016", "eop": "31-12-2017",
            },
        )
        job.work_dir = f"module1_jobs/{job.id}"
        job.save(update_fields=["work_dir"])
        init_job_work_dir(job)
        for kind in ("premium", "claims_paid", "claims_os"):
            dest = job_input_subdir(job, kind)
            for src in sorted((FIXTURES / kind).glob("*.xlsx")):
                shutil.copy(src, dest / src.name)
        return job

    def test_strict_mode_blocks_and_produces_nothing(self):
        from processing.tasks import run_module1_summary_task

        job = self._staged_job()
        run_module1_summary_task(str(job.id))
        job.refresh_from_db()
        self.assertEqual(job.status, Module1Job.Status.FAILED)
        self.assertIn("Health", job.error_message)
        self.assertFalse(job.output_zip, "no output may be written for a blocked run")

    def test_the_report_is_persisted_even_when_the_run_is_blocked(self):
        from processing.tasks import run_module1_summary_task

        job = self._staged_job()
        run_module1_summary_task(str(job.id))
        job.refresh_from_db()
        pf = job.input_meta["preflight"]
        self.assertEqual(pf["severity"], "error")
        self.assertEqual(pf["mode"], "strict")
        self.assertGreater(pf["dropped_row_count"], 0)

    def test_an_alias_lets_the_same_inputs_through(self):
        """The whole point: the fix is a configuration change, not a data re-cut."""
        from processing.tasks import run_module1_summary_task

        ReservingClassAlias.objects.create(
            organization=self.org, alias="Health", canonical="Health Insurance"
        )
        job = self._staged_job()
        job.input_meta = {**job.input_meta, "class_aliases": alias_map_for(self.org)}
        job.save(update_fields=["input_meta"])
        run_module1_summary_task(str(job.id))
        job.refresh_from_db()
        self.assertEqual(job.status, Module1Job.Status.SUCCESS, job.error_message)
        self.assertEqual(job.input_meta["preflight"]["severity"], "warn")

    def test_permissive_mode_runs_and_still_records_what_was_discarded(self):
        from processing.tasks import run_module1_summary_task

        self.org.preflight_mode = Organization.PreflightMode.PERMISSIVE
        self.org.save(update_fields=["preflight_mode"])
        job = self._staged_job()
        run_module1_summary_task(str(job.id))
        job.refresh_from_db()
        self.assertEqual(job.status, Module1Job.Status.SUCCESS, job.error_message)
        pf = job.input_meta["preflight"]
        self.assertEqual(pf["severity"], "error")
        self.assertGreater(pf["dropped_amount"], 0)
