"""WP4 — sensitivity job API and scenario-set management."""

import io
import json
import shutil
import tempfile
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from openpyxl import Workbook
from rest_framework.test import APIClient

from accounts.models import Permission, Role
from processing.models import Module1Job
from tenants.models import Membership, Organization, Scenario, ScenarioSet

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="sigma17-test-media-sensitivity-")


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


def _combined_summary_xlsx() -> bytes:
    wb = Workbook()
    wb.active.title = "Combined Summary"
    wb.active.append(["RESERVINGCLASS", "UWY"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class SensitivityJobApiTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.org = Organization.objects.create(name="Acme", slug="acme")
        self.user = User.objects.create_user("actuary", "a@example.com", "pw")
        _give_role(self.user, "Actuary",
                   ["module2.run", "scenarios.view", "scenarios.manage"], self.org)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    # -- job creation ------------------------------------------------------

    def _post(self, **extra):
        payload = {
            "combined_summary": io.BytesIO(_combined_summary_xlsx()),
            "scenarios": json.dumps([{"label": "RA +10%", "lever": "ra", "magnitude": 0.10}]),
        }
        payload.update(extra)
        f = payload.pop("combined_summary", None)
        # Drop explicit Nones — multipart cannot encode them, and a caller passing
        # None means "omit this field".
        data = {k: v for k, v in payload.items() if v is not None}
        if f is not None:
            f.name = "Combined_Summary.xlsx"
            data["combined_summary"] = f
        return self.client.post("/api/module2/jobs/sensitivity/", data, format="multipart")

    @patch("processing.views.run_module2_sensitivity_task")
    def test_inline_scenarios_create_a_job_and_snapshot_the_list(self, task):
        resp = self._post()
        self.assertEqual(resp.status_code, 202, resp.data)
        job = Module1Job.objects.get(pk=resp.data["id"])
        self.assertEqual(job.job_type, Module1Job.JobType.MODULE2_SENSITIVITY)
        self.assertEqual(job.input_meta["scope"], "allocate")
        self.assertEqual(len(job.input_meta["scenarios"]), 1)
        task.delay.assert_called_once()

    @patch("processing.views.run_module2_sensitivity_task")
    def test_scenario_set_is_snapshotted_so_later_edits_do_not_alter_the_run(self, task):
        sset = ScenarioSet.objects.create(organization=self.org, name="Standard")
        Scenario.objects.create(scenario_set=sset, label="RA +10%",
                                lever="ra", magnitude="0.10", order=0)
        resp = self._post(scenarios=None, scenario_set_id=str(sset.id))
        self.assertEqual(resp.status_code, 202, resp.data)
        job = Module1Job.objects.get(pk=resp.data["id"])
        self.assertEqual(job.input_meta["scenario_set"]["version"], 1)
        self.assertEqual(len(job.input_meta["scenarios"]), 1)

        # Mutating the set afterwards must not touch the stored run.
        Scenario.objects.create(scenario_set=sset, label="RA +25%",
                                lever="ra", magnitude="0.25", order=1)
        job.refresh_from_db()
        self.assertEqual(len(job.input_meta["scenarios"]), 1)

    @patch("processing.views.run_module2_sensitivity_task")
    def test_rejects_both_set_and_inline_scenarios(self, task):
        sset = ScenarioSet.objects.create(organization=self.org, name="S")
        resp = self._post(scenario_set_id=str(sset.id))
        self.assertEqual(resp.status_code, 400)

    @patch("processing.views.run_module2_sensitivity_task")
    def test_rejects_empty_and_malformed_scenarios(self, task):
        self.assertEqual(self._post(scenarios=json.dumps([])).status_code, 400)
        self.assertEqual(self._post(scenarios="not json").status_code, 400)
        self.assertEqual(
            self._post(scenarios=json.dumps([{"lever": "inflation", "magnitude": 1}])).status_code,
            400,
        )
        self.assertEqual(
            self._post(scenarios=json.dumps([{"lever": "ra", "magnitude": "abc"}])).status_code,
            400,
        )

    @patch("processing.views.run_module2_sensitivity_task")
    def test_process_scope_requires_accounting_period_and_inputs(self, task):
        resp = self._post(scope="process")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("accounting_period", resp.data["fieldErrors"])

        resp = self._post(scope="process", accounting_period="2024")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("previous_period", resp.data["fieldErrors"])

    @patch("processing.views.run_module2_sensitivity_task")
    def test_rejects_unknown_scope(self, task):
        self.assertEqual(self._post(scope="galaxy").status_code, 400)

    def test_requires_module2_run_permission(self):
        other = User.objects.create_user("viewer", "v@example.com", "pw")
        _give_role(other, "Viewer", ["dashboard.view"], self.org)
        client = APIClient()
        client.force_authenticate(other)
        f = io.BytesIO(_combined_summary_xlsx())
        f.name = "Combined_Summary.xlsx"
        resp = client.post("/api/module2/jobs/sensitivity/", {
            "combined_summary": f,
            "scenarios": json.dumps([{"lever": "ra", "magnitude": 0.1}]),
        }, format="multipart")
        self.assertEqual(resp.status_code, 403)

    # -- results -----------------------------------------------------------

    def test_result_endpoint_rejects_a_non_sensitivity_job(self):
        job = Module1Job.objects.create(
            user=self.user, organization=self.org,
            job_type=Module1Job.JobType.MODULE2_ALLOCATE,
        )
        resp = self.client.get(f"/api/module2/jobs/{job.id}/sensitivity/")
        self.assertEqual(resp.status_code, 400)

    def test_result_endpoint_before_completion(self):
        job = Module1Job.objects.create(
            user=self.user, organization=self.org,
            job_type=Module1Job.JobType.MODULE2_SENSITIVITY,
        )
        resp = self.client.get(f"/api/module2/jobs/{job.id}/sensitivity/")
        self.assertEqual(resp.status_code, 400)

    def test_result_endpoint_shapes_the_matrix(self):
        job = Module1Job.objects.create(
            user=self.user, organization=self.org,
            job_type=Module1Job.JobType.MODULE2_SENSITIVITY,
            status=Module1Job.Status.SUCCESS,
            input_meta={"sensitivity": {
                "scope": "allocate",
                "measures": [{"key": "ibnr", "label": "IBNR", "kind": "money"},
                             {"key": "ra_os", "label": "RA (OS)", "kind": "money"}],
                "reserving_classes": ["Motor"],
                "scenarios": [{"label": "RA +10%", "lever": "ra", "magnitude": 0.1}],
                "resolved": [], "warnings": [],
                "base": {"ibnr": {"__TOTAL__": 100.0, "Motor": 40.0},
                         "ra_os": {"__TOTAL__": 0.0, "Motor": 0.0}},
                "values": [{"scenario": "RA +10%", "measures": {
                    "ibnr": {"__TOTAL__": 100.0, "Motor": 40.0},
                    "ra_os": {"__TOTAL__": 5.0, "Motor": 2.0}}}],
            }},
        )
        resp = self.client.get(f"/api/module2/jobs/{job.id}/sensitivity/")
        self.assertEqual(resp.status_code, 200, resp.data)
        rows = {r["key"]: r for r in resp.data["rows"]}
        # Structural zero: flagged, not silently rendered as a 0% move.
        self.assertFalse(rows["ibnr"]["cells"][0]["responds"])
        self.assertEqual(rows["ibnr"]["cells"][0]["absDelta"], 0.0)
        # Zero base: percent is None rather than a fabricated infinity.
        self.assertTrue(rows["ra_os"]["cells"][0]["responds"])
        self.assertIsNone(rows["ra_os"]["cells"][0]["pctDelta"])

    def test_result_endpoint_drills_into_a_class_and_rejects_unknown_ones(self):
        job = Module1Job.objects.create(
            user=self.user, organization=self.org,
            job_type=Module1Job.JobType.MODULE2_SENSITIVITY,
            status=Module1Job.Status.SUCCESS,
            input_meta={"sensitivity": {
                "scope": "allocate",
                "measures": [{"key": "ibnr", "label": "IBNR", "kind": "money"}],
                "reserving_classes": ["Motor"],
                "scenarios": [{"label": "x", "lever": "ra", "magnitude": 0.1}],
                "resolved": [], "warnings": [],
                "base": {"ibnr": {"__TOTAL__": 100.0, "Motor": 40.0}},
                "values": [{"scenario": "x", "measures": {
                    "ibnr": {"__TOTAL__": 110.0, "Motor": 44.0}}}],
            }},
        )
        resp = self.client.get(f"/api/module2/jobs/{job.id}/sensitivity/?reserving_class=Motor")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["rows"][0]["base"], 40.0)
        self.assertEqual(resp.data["rows"][0]["cells"][0]["absDelta"], 4.0)

        resp = self.client.get(f"/api/module2/jobs/{job.id}/sensitivity/?reserving_class=Nope")
        self.assertEqual(resp.status_code, 400)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class ScenarioSetApiTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Acme2", slug="acme2")
        self.user = User.objects.create_user("act2", "a2@example.com", "pw")
        _give_role(self.user, "Actuary2",
                   ["scenarios.view", "scenarios.manage"], self.org)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_create_list_and_fork_on_edit(self):
        resp = self.client.post("/api/scenario-sets/", {
            "name": "Standard",
            "scenarios": [
                {"label": "RA +10%", "lever": "ra", "magnitude": "0.10"},
                {"label": "Disc +5bp", "lever": "discount", "magnitude": "5"},
            ],
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        set_id = resp.data["id"]
        self.assertEqual(resp.data["version"], 1)
        self.assertEqual(len(resp.data["scenarios"]), 2)

        # Editing forks v2 and deactivates v1 — historic runs stay reproducible.
        resp = self.client.put(f"/api/scenario-sets/{set_id}/", {
            "name": "Standard",
            "scenarios": [{"label": "RA +25%", "lever": "ra", "magnitude": "0.25"}],
        }, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["version"], 2)
        self.assertFalse(ScenarioSet.objects.get(pk=set_id).is_active)
        self.assertEqual(ScenarioSet.objects.filter(organization=self.org).count(), 2)

        # Default listing shows only the active version.
        resp = self.client.get("/api/scenario-sets/")
        self.assertEqual(len(resp.data["results"]), 1)
        self.assertEqual(resp.data["results"][0]["version"], 2)
        resp = self.client.get("/api/scenario-sets/?all=1")
        self.assertEqual(len(resp.data["results"]), 2)

    def test_units_are_surfaced_per_lever(self):
        resp = self.client.post("/api/scenario-sets/", {
            "name": "Units",
            "scenarios": [
                {"label": "RA", "lever": "ra", "magnitude": "0.10"},
                {"label": "Disc", "lever": "discount", "magnitude": "5"},
                {"label": "ULR", "lever": "ulr", "magnitude": "0.05"},
            ],
        }, format="json")
        units = {s["lever"]: s["unit"] for s in resp.data["scenarios"]}
        self.assertEqual(units, {"ra": "relative", "discount": "bp", "ulr": "pp"})

    def test_nonsense_magnitudes_are_rejected_per_lever(self):
        for lever, mag in (("ra", "-1.5"), ("discount", "20000"), ("ulr", "9")):
            resp = self.client.post("/api/scenario-sets/", {
                "name": f"Bad-{lever}",
                "scenarios": [{"label": "x", "lever": lever, "magnitude": mag}],
            }, format="json")
            self.assertEqual(resp.status_code, 400, f"{lever} {mag} should be rejected")

    def test_a_runner_without_scenarios_view_can_still_read_the_sets(self):
        """module2.run alone must be enough to READ sets — otherwise an org with
        custom roles produces a user who can run jobs but cannot see what to run."""
        runner = User.objects.create_user("runner", "runner@example.com", "pw")
        _give_role(runner, "RunOnly", ["module2.run"], self.org)
        client = APIClient()
        client.force_authenticate(runner)
        self.assertEqual(client.get("/api/scenario-sets/").status_code, 200)
        # ...but still cannot create or edit one.
        self.assertEqual(
            client.post("/api/scenario-sets/", {"name": "X", "scenarios": []},
                        format="json").status_code,
            403,
        )

    def test_manage_permission_required_to_create(self):
        other = User.objects.create_user("ro", "ro@example.com", "pw")
        _give_role(other, "ReadOnly", ["scenarios.view"], self.org)
        client = APIClient()
        client.force_authenticate(other)
        resp = client.post("/api/scenario-sets/", {"name": "X", "scenarios": []}, format="json")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(client.get("/api/scenario-sets/").status_code, 200)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class SensitivityTaskEndToEndTests(TestCase):
    """Runs the real Celery task body against the client reference workbook."""

    FIXTURE = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "benchmarks" / "fixtures" / "m2_allocate_ref" / "Combined_Summary.xlsx"
    )

    def setUp(self):
        if not self.FIXTURE.is_file():
            self.skipTest("reference fixture not available")
        self.org = Organization.objects.create(name="E2E", slug="e2e")
        self.user = User.objects.create_user("e2e", "e2e@example.com", "pw")
        _give_role(self.user, "ActuaryE2E", ["module2.run"], self.org)

    def _staged_job(self, **meta):
        import pathlib
        from processing.utils import init_module2_sensitivity_job_dirs

        job = Module1Job.objects.create(
            user=self.user, organization=self.org,
            job_type=Module1Job.JobType.MODULE2_SENSITIVITY,
        )
        job.work_dir = f"module1_jobs/{job.id}"
        job.save(update_fields=["work_dir"])
        combined_dir, _, _ = init_module2_sensitivity_job_dirs(job)
        (combined_dir / "Combined_Summary.xlsx").write_bytes(self.FIXTURE.read_bytes())
        job.input_meta = {
            "files": {}, "scope": "allocate", "selected_ulr": [],
            "scenario_set": None, **meta,
        }
        job.save(update_fields=["input_meta"])
        return job

    def test_full_run_produces_the_workbook_and_the_json_matrix(self):
        import zipfile
        from processing.tasks import SENSITIVITY_ARTIFACT, run_module2_sensitivity_task

        job = self._staged_job(scenarios=[
            {"label": "RA +10%", "lever": "ra", "magnitude": 0.10},
            {"label": "Disc +5bp", "lever": "discount", "magnitude": 5},
            {"label": "ULR +5pp", "lever": "ulr", "magnitude": 0.05},
        ])
        run_module2_sensitivity_task(str(job.id))

        job.refresh_from_db()
        self.assertEqual(job.status, Module1Job.Status.SUCCESS, job.error_message)
        self.assertIn(SENSITIVITY_ARTIFACT, job.output_artifacts)

        with job.output_zip.open("rb") as f:
            names = zipfile.ZipFile(io.BytesIO(f.read())).namelist()
        self.assertIn(SENSITIVITY_ARTIFACT, names)

        payload = job.input_meta["sensitivity"]
        self.assertEqual(payload["scope"], "allocate")
        self.assertEqual(len(payload["values"]), 3)
        self.assertIn("ibnr", payload["base"])
        self.assertTrue(payload["reserving_classes"])

    def test_result_endpoint_serves_the_completed_run(self):
        from processing.tasks import run_module2_sensitivity_task

        job = self._staged_job(scenarios=[
            {"label": "RA +10%", "lever": "ra", "magnitude": 0.10},
        ])
        run_module2_sensitivity_task(str(job.id))
        job.refresh_from_db()
        self.assertEqual(job.status, Module1Job.Status.SUCCESS, job.error_message)

        client = APIClient()
        client.force_authenticate(self.user)
        resp = client.get(f"/api/module2/jobs/{job.id}/sensitivity/")
        self.assertEqual(resp.status_code, 200, resp.data)

        rows = {r["key"]: r for r in resp.data["rows"]}
        # RA is relative: the RA balances move by exactly +10%.
        ra = rows["ra_os"]["cells"][0]
        self.assertTrue(ra["responds"])
        self.assertAlmostEqual(ra["pctDelta"], 0.10, places=9)
        # IBNR is structurally out of reach of an RA shock.
        self.assertFalse(rows["ibnr"]["cells"][0]["responds"])

    def test_a_run_with_no_scenarios_fails_with_a_clear_message(self):
        from processing.tasks import run_module2_sensitivity_task

        job = self._staged_job(scenarios=[])
        run_module2_sensitivity_task(str(job.id))
        job.refresh_from_db()
        self.assertEqual(job.status, Module1Job.Status.FAILED)
        self.assertIn("scenario", job.error_message.lower())


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class SensitivityProcessScopeTests(TestCase):
    """Process scope end-to-end, including input inheritance from a chained job.

    This is the path the UI takes: the user picks a completed Cash Flow Allocation
    job and Previous Period / Expense CF come from its durable input archive.
    """

    FIXTURES = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "benchmarks" / "fixtures" / "m2_process_ref"
    )

    def setUp(self):
        if not (self.FIXTURES / "Combined_Summary.xlsx").is_file():
            self.skipTest("process reference fixtures not available")
        self.org = Organization.objects.create(name="PS", slug="ps")
        self.user = User.objects.create_user("ps", "ps@example.com", "pw")
        _give_role(self.user, "ActuaryPS", ["module2.run"], self.org)

    def _process_job_with_archive(self):
        """A completed process job carrying Previous Period + Expense CF."""
        import io as _io
        import zipfile
        from django.core.files.base import ContentFile
        from processing.tasks import INPUT_ARCHIVE_EXPENSE, INPUT_ARCHIVE_PREVIOUS

        job = Module1Job.objects.create(
            user=self.user, organization=self.org,
            job_type=Module1Job.JobType.MODULE2_PROCESS,
            status=Module1Job.Status.SUCCESS,
        )
        buf = _io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(INPUT_ARCHIVE_PREVIOUS,
                        (self.FIXTURES / "Previous_period.xlsx").read_bytes())
            zf.writestr(INPUT_ARCHIVE_EXPENSE,
                        (self.FIXTURES / "Expense-CF.xlsx").read_bytes())
        job.input_archive.save(f"{job.id}-inputs.zip", ContentFile(buf.getvalue()), save=True)
        return job

    def test_process_scope_inherits_inputs_and_reports_lic_and_lrc(self):
        from processing.tasks import run_module2_sensitivity_task
        from processing.utils import init_module2_sensitivity_job_dirs

        source = self._process_job_with_archive()

        job = Module1Job.objects.create(
            user=self.user, organization=self.org,
            job_type=Module1Job.JobType.MODULE2_SENSITIVITY,
        )
        job.work_dir = f"module1_jobs/{job.id}"
        job.save(update_fields=["work_dir"])
        combined_dir, _, _ = init_module2_sensitivity_job_dirs(job)
        (combined_dir / "Combined_Summary.xlsx").write_bytes(
            (self.FIXTURES / "Combined_Summary.xlsx").read_bytes()
        )
        job.input_meta = {
            "files": {}, "scope": "process", "selected_ulr": [],
            "accounting_period": 2024,
            "process_job_id": str(source.id),
            "scenarios": [
                {"label": "RA +10%", "lever": "ra", "magnitude": 0.10},
                {"label": "Disc +5bp", "lever": "discount", "magnitude": 5},
                {"label": "ULR +5pp", "lever": "ulr", "magnitude": 0.05},
            ],
        }
        job.save(update_fields=["input_meta"])

        run_module2_sensitivity_task(str(job.id))
        job.refresh_from_db()
        self.assertEqual(job.status, Module1Job.Status.SUCCESS, job.error_message)

        payload = job.input_meta["sensitivity"]
        keys = {m["key"] for m in payload["measures"]}
        self.assertTrue({"lic_bop", "lic_eop", "lrc_bop", "lrc_eop"} <= keys)

        # BOP is prior-period given data: no shock may move it.
        for entry in payload["values"]:
            for key in ("lic_bop", "lrc_bop"):
                self.assertAlmostEqual(
                    entry["measures"][key]["__TOTAL__"],
                    payload["base"][key]["__TOTAL__"],
                    places=4,
                    msg=f"{key} moved under {entry['scenario']}",
                )

        # The sensitivity job re-archives its inputs, so it is itself reusable.
        job.refresh_from_db()
        self.assertTrue(job.input_archive)

    def test_process_scope_without_inputs_fails_with_an_actionable_message(self):
        from processing.tasks import run_module2_sensitivity_task
        from processing.utils import init_module2_sensitivity_job_dirs

        job = Module1Job.objects.create(
            user=self.user, organization=self.org,
            job_type=Module1Job.JobType.MODULE2_SENSITIVITY,
        )
        job.work_dir = f"module1_jobs/{job.id}"
        job.save(update_fields=["work_dir"])
        combined_dir, _, _ = init_module2_sensitivity_job_dirs(job)
        (combined_dir / "Combined_Summary.xlsx").write_bytes(
            (self.FIXTURES / "Combined_Summary.xlsx").read_bytes()
        )
        job.input_meta = {
            "files": {}, "scope": "process", "selected_ulr": [],
            "accounting_period": 2024,
            "scenarios": [{"label": "RA +10%", "lever": "ra", "magnitude": 0.10}],
        }
        job.save(update_fields=["input_meta"])

        run_module2_sensitivity_task(str(job.id))
        job.refresh_from_db()
        self.assertEqual(job.status, Module1Job.Status.FAILED)
        # Must name the missing input, not send the user to check sheet formats.
        self.assertIn("Previous Period", job.error_message)
