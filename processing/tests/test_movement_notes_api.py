"""GET /api/module2/jobs/<pk>/movement/notes/ — the note disclosure as structured data."""

import io
import json
import zipfile

import shutil
import tempfile

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from processing.models import Module1Job
from processing.tests.test_module2_api import _give_role_with_permissions
from tenants.models import Organization

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="sigma17-test-media-notes-api-")

COMPANION = {
    "schema_version": "2026.06+r2",
    "notes_schema_version": "2026.07",
    "reporting_date": "31/12/2024",
    "deviations": [
        {"id": "D7", "note": "Gross_Note", "row": 13, "client_cell": "=Gross!C26",
         "resolution": "negated", "evidence": "IS is expense-positive", "status": "assumed"},
    ],
    "views": [
        {"level": "entity", "label": "Total (all classes)", "reserving_class": None, "uwy": None,
         "sheets": {}, "notes": {
             "Gross_Note": {"title": "12.2.1.1 Insurance contracts",
                            "columns": ["LRC_excl_LC", "Total"],
                            "lines": [{"id": "closing_balance_net", "row": 33,
                                       "label": "Closing balance – net", "kind": "subtotal",
                                       "values": {"LRC_excl_LC": 1.0, "Total": 411096232.36}}]},
             "IS": {"title": "Income Statement", "columns": ["Total"], "lines": []},
             "BS": {"title": "Balance Sheet", "columns": ["Total"], "lines": []},
         }},
        {"level": "class", "label": "MOTOR", "reserving_class": "MOTOR", "uwy": None,
         "sheets": {}, "notes": {
             "Gross_Note": {"title": "12.2.1.1 Insurance contracts", "columns": ["Total"],
                            "lines": []},
         }},
        {"level": "cohort", "label": "MOTOR — UWY 2023", "reserving_class": "MOTOR", "uwy": 2023,
         "sheets": {}, "notes": {
             "Gross_Note": {"title": "12.2.1.1 Insurance contracts", "columns": ["Total"],
                            "lines": []},
         }},
    ],
}


def _zip_with(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, payload in files.items():
            zf.writestr(name, payload)
    return buf.getvalue()


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class MovementNotesApiTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.org = Organization.objects.create(name="notes-org")
        self.user = User.objects.create_user(username="notes", password="testpass123")
        _give_role_with_permissions(
            self.user, "Notes Reader", ["module2.run", "runhistory.view"], self.org
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _job(self, *, job_type=Module1Job.JobType.MODULE2_MOVEMENT, companion=COMPANION,
             with_output=True):
        job = Module1Job.objects.create(
            user=self.user, organization=self.org, job_type=job_type,
            status=Module1Job.Status.SUCCESS, work_dir="module1_jobs/notes-api",
        )
        if with_output:
            files = {"IFRS17_Movement_Analysis.xlsx": b"XLSX"}
            if companion is not None:
                files["IFRS17_Movement_Analysis.json"] = json.dumps(companion).encode()
            job.output_zip.save(f"{job.id}.zip", ContentFile(_zip_with(files)), save=True)
        return job

    def _url(self, job, query=""):
        return f"/api/module2/jobs/{job.id}/movement/notes/{query}"

    def test_returns_entity_and_class_views_by_default(self):
        res = self.client.get(self._url(self._job()))
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual([v["level"] for v in body["views"]], ["entity", "class"])
        self.assertEqual(body["schema_version"], "2026.06+r2")
        self.assertEqual(body["notes_schema_version"], "2026.07")
        self.assertEqual(body["reporting_date"], "31/12/2024")

    def test_cohort_grain_is_excluded_unless_requested(self):
        job = self._job()
        default = self.client.get(self._url(job)).json()
        self.assertNotIn("cohort", [v["level"] for v in default["views"]])
        widened = self.client.get(self._url(job, "?level=entity,class,cohort")).json()
        self.assertEqual([v["level"] for v in widened["views"]], ["entity", "class", "cohort"])

    def test_note_tables_and_values_survive_the_round_trip(self):
        body = self.client.get(self._url(self._job())).json()
        entity = next(v for v in body["views"] if v["level"] == "entity")
        self.assertEqual(set(entity["notes"]), {"Gross_Note", "IS", "BS"})
        line = entity["notes"]["Gross_Note"]["lines"][0]
        self.assertEqual(line["id"], "closing_balance_net")
        self.assertEqual(line["values"]["Total"], 411096232.36)

    def test_pending_deviations_are_surfaced(self):
        body = self.client.get(self._url(self._job())).json()
        self.assertEqual(len(body["deviations"]), 1)
        self.assertEqual(body["deviations"][0]["id"], "D7")
        self.assertEqual(body["deviations"][0]["status"], "assumed")

    def test_unknown_level_is_rejected(self):
        res = self.client.get(self._url(self._job(), "?level=entity,galaxy"))
        self.assertEqual(res.status_code, 400, res.content)

    def test_non_movement_job_is_rejected(self):
        job = self._job(job_type=Module1Job.JobType.MODULE2_PROCESS)
        res = self.client.get(self._url(job))
        self.assertEqual(res.status_code, 400, res.content)

    def test_job_without_the_companion_reports_cleanly(self):
        """Movement jobs produced before the companion existed must 400, never 500."""
        res = self.client.get(self._url(self._job(companion=None)))
        self.assertEqual(res.status_code, 400, res.content)

    def test_job_without_output_is_not_found(self):
        res = self.client.get(self._url(self._job(with_output=False)))
        self.assertEqual(res.status_code, 404, res.content)

    def test_another_organisations_job_is_not_readable(self):
        job = self._job()
        other_org = Organization.objects.create(name="other-org")
        outsider = User.objects.create_user(username="outsider", password="testpass123")
        _give_role_with_permissions(outsider, "Outsider", ["runhistory.view"], other_org)
        self.client = APIClient()
        self.client.force_authenticate(user=outsider)
        res = self.client.get(self._url(job))
        self.assertIn(res.status_code, (403, 404), res.content)

    def test_run_predating_the_notes_returns_an_empty_list_not_an_error(self):
        """A companion written before the note layer existed has no `notes` key. The
        endpoint must answer cleanly with no views so the UI can say so, rather than
        erroring or inventing an empty table."""
        stale = {"schema_version": "2026.06", "views": [
            {"level": "entity", "label": "Total (all classes)", "reserving_class": None,
             "uwy": None, "sheets": {}},
        ]}
        res = self.client.get(self._url(self._job(companion=stale)))
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["views"], [])
