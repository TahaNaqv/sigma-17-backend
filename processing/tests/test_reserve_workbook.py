"""Tests for the reserve-workbook service (Excel-free Update Reserve).

These tests build synthetic reserve workbooks that mirror the structure
the Summary engine produces:
  - sheets `Paid Claims Triangle` and `Reported Triangle`
  - row 1 = column headers
  - somewhere below, a row whose column 1 is the literal "Selected CDF"

We then exercise the three public service functions: list, read, and
apply-overrides + write. The Celery task is integration-tested via the
existing chaining tests; this file is the unit contract for the
workbook adapter itself.
"""

import io
import shutil
import tempfile
import zipfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from openpyxl import Workbook, load_workbook

from processing.models import Module1Job
from processing.services.reserve_workbook import (
    list_reserve_workbooks,
    read_workbook_cdfs,
    write_workbooks_with_overrides,
)
from tenants.models import Organization

User = get_user_model()

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="sigma17-reserve-test-")


def _build_reserve_workbook(
    *,
    paid_cdf_values: list[float],
    reported_cdf_values: list[float],
    paid_headers: list[str] | None = None,
) -> bytes:
    """Build a minimal reserve workbook with the structure the engine
    produces. We only need the two triangle sheets and the Selected CDF
    row; the reserve service ignores everything else."""
    wb = Workbook()
    # The default sheet is "Sheet" — rename to Paid Claims Triangle
    ws_paid = wb.active
    ws_paid.title = "Paid Claims Triangle"
    headers = paid_headers or ["Accident Period"] + [str(i) for i in range(len(paid_cdf_values))]
    ws_paid.append(headers)
    # A few placeholder data rows so max_row > the cdf row
    ws_paid.append(["2022"] + [0] * len(paid_cdf_values))
    ws_paid.append(["2023"] + [0] * len(paid_cdf_values))
    ws_paid.append(["Selected CDF"] + paid_cdf_values)

    ws_rep = wb.create_sheet("Reported Triangle")
    ws_rep.append(["Accident Period"] + [str(i) for i in range(len(reported_cdf_values))])
    ws_rep.append(["2022"] + [0] * len(reported_cdf_values))
    ws_rep.append(["Selected CDF"] + reported_cdf_values)

    # Add a third sheet for realism — the service should ignore it.
    wb.create_sheet("Reserve Summary")

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _zip_with(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, raw in files.items():
            zf.writestr(name, raw)
    return buf.getvalue()


def _make_source_job(*, user, org, zip_bytes: bytes) -> Module1Job:
    job = Module1Job.objects.create(
        user=user,
        organization=org,
        job_type=Module1Job.JobType.SUMMARY,
        status=Module1Job.Status.SUCCESS,
        work_dir=f"module1_jobs/test-{org.id}",
    )
    job.output_zip.save(f"{job.id}.zip", ContentFile(zip_bytes), save=False)
    job.save()
    return job


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, SECURE_SSL_REDIRECT=False)
class ReserveWorkbookServiceTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.org = Organization.objects.create(name="ResOrg")
        self.user = User.objects.create_user(username="ru", password="testpass123")

    # ---- list ----

    def test_list_workbooks_parses_engine_naming(self):
        wb = _build_reserve_workbook(
            paid_cdf_values=[2.0, 1.5, 1.1],
            reported_cdf_values=[2.1, 1.6, 1.0],
        )
        zb = _zip_with({
            "Combined_Summary.xlsx": b"placeholder",  # should be excluded
            "Motor TP GROSS 2024-12.xlsx": wb,
            "Multi Word Class TP GROSS 2024-12.xlsx": wb,
            "junk.txt": b"x",  # not xlsx
            "Bad.xlsx": b"x",  # not enough name parts
        })
        job = _make_source_job(user=self.user, org=self.org, zip_bytes=zb)

        listed = list_reserve_workbooks(job)
        names = sorted(w["filename"] for w in listed)
        self.assertEqual(
            names,
            sorted([
                "Motor TP GROSS 2024-12.xlsx",
                "Multi Word Class TP GROSS 2024-12.xlsx",
            ]),
        )
        single = next(w for w in listed if w["filename"] == "Motor TP GROSS 2024-12.xlsx")
        self.assertEqual(single["reserving_class"], "Motor")
        self.assertEqual(single["head_of_damage"], "TP")
        self.assertEqual(single["ri_type"], "GROSS")
        self.assertEqual(single["eop_label"], "2024-12")
        multi = next(w for w in listed if w["reserving_class"] == "Multi Word Class")
        self.assertEqual(multi["head_of_damage"], "TP")

    # ---- read CDFs ----

    def test_read_cdfs_returns_values_and_headers(self):
        wb = _build_reserve_workbook(
            paid_cdf_values=[2.0, 1.5, 1.1],
            reported_cdf_values=[2.1, 1.6, 1.0],
        )
        job = _make_source_job(
            user=self.user,
            org=self.org,
            zip_bytes=_zip_with({"Motor TP GROSS 2024-12.xlsx": wb}),
        )

        payload = read_workbook_cdfs(job, "Motor TP GROSS 2024-12.xlsx")
        self.assertEqual(payload["reserving_class"], "Motor")
        triangles = {t["sheet"]: t for t in payload["triangles"]}
        self.assertIn("Paid Claims Triangle", triangles)
        self.assertIn("Reported Triangle", triangles)
        self.assertEqual(triangles["Paid Claims Triangle"]["values"], [2.0, 1.5, 1.1])
        self.assertEqual(triangles["Reported Triangle"]["values"], [2.1, 1.6, 1.0])
        # Headers come from row 1 of each sheet.
        self.assertEqual(
            triangles["Paid Claims Triangle"]["column_labels"],
            ["0", "1", "2"],
        )

    # ---- apply overrides ----

    def test_overrides_replace_selected_cdf_values(self):
        wb = _build_reserve_workbook(
            paid_cdf_values=[2.0, 1.5, 1.1],
            reported_cdf_values=[2.1, 1.6, 1.0],
        )
        job = _make_source_job(
            user=self.user,
            org=self.org,
            zip_bytes=_zip_with({"Motor TP GROSS 2024-12.xlsx": wb}),
        )

        dest = Path(tempfile.mkdtemp(prefix="reserve-override-out-"))
        try:
            written = write_workbooks_with_overrides(
                source_job=job,
                overrides_by_filename={
                    "Motor TP GROSS 2024-12.xlsx": {
                        "Paid Claims Triangle": [3.0, 2.5, 1.2],
                        "Reported Triangle": [3.5, 2.0, 1.1],
                    }
                },
                dest_folder=dest,
            )
            self.assertIn("Motor TP GROSS 2024-12.xlsx", written)

            # Open the modified workbook and confirm the Selected CDF
            # row got rewritten to the user's values.
            wbcheck = load_workbook(dest / "Motor TP GROSS 2024-12.xlsx", data_only=True)
            paid_ws = wbcheck["Paid Claims Triangle"]
            # Find the Selected CDF row
            sel_row = None
            for row in paid_ws.iter_rows(min_row=1, max_row=paid_ws.max_row, min_col=1, max_col=1):
                for cell in row:
                    if cell.value == "Selected CDF":
                        sel_row = cell.row
                        break
                if sel_row:
                    break
            self.assertIsNotNone(sel_row)
            values = [paid_ws.cell(row=sel_row, column=c).value for c in range(2, 5)]
            self.assertEqual(values, [3.0, 2.5, 1.2])
        finally:
            shutil.rmtree(dest, ignore_errors=True)

    def test_unreferenced_workbooks_are_copied_verbatim(self):
        wb = _build_reserve_workbook(
            paid_cdf_values=[2.0, 1.5],
            reported_cdf_values=[2.0, 1.5],
        )
        zb = _zip_with({
            "Motor TP GROSS 2024-12.xlsx": wb,
            "Property Fire RI 2024-12.xlsx": wb,
        })
        job = _make_source_job(user=self.user, org=self.org, zip_bytes=zb)

        dest = Path(tempfile.mkdtemp(prefix="reserve-noov-out-"))
        try:
            written = write_workbooks_with_overrides(
                source_job=job,
                overrides_by_filename={
                    # Override one; the other should still be copied so
                    # the engine sees the full batch.
                    "Motor TP GROSS 2024-12.xlsx": {
                        "Paid Claims Triangle": [9.0, 9.0],
                    },
                },
                dest_folder=dest,
            )
            self.assertEqual(
                sorted(written),
                sorted([
                    "Motor TP GROSS 2024-12.xlsx",
                    "Property Fire RI 2024-12.xlsx",
                ]),
            )
            # Original (non-overridden) values intact in the second file.
            wbcheck = load_workbook(
                dest / "Property Fire RI 2024-12.xlsx", data_only=True
            )
            paid_ws = wbcheck["Paid Claims Triangle"]
            sel_row = None
            for row in paid_ws.iter_rows(min_row=1, max_row=paid_ws.max_row, min_col=1, max_col=1):
                for cell in row:
                    if cell.value == "Selected CDF":
                        sel_row = cell.row
                        break
                if sel_row:
                    break
            self.assertEqual(
                [paid_ws.cell(row=sel_row, column=c).value for c in range(2, 4)],
                [2.0, 1.5],
            )
        finally:
            shutil.rmtree(dest, ignore_errors=True)
