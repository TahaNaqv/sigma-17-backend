"""Adapter tests: snapshot → xlsx → pandas round-trip.

These are the contract tests between datasets and the engine: when we
render a snapshot to xlsx, the resulting file must have exactly the
mixed-case headers the engine looks up in `df.columns`.
"""

import shutil
import tempfile
from decimal import Decimal
from pathlib import Path

import pandas as pd
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from datasets.models import Dataset, PremiumRow
from datasets.services.engine_adapter import (
    materialize_datasets_to_folder,
    write_snapshot_to_folder,
)
from datasets.services.snapshots import create_snapshot
from tenants.models import Organization

User = get_user_model()


@override_settings(SECURE_SSL_REDIRECT=False)
class EngineAdapterTests(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sigma17-adapter-test-"))
        self.org = Organization.objects.create(name="Adapter Org")
        self.user = User.objects.create_user(
            username="adapter-user", password="testpass123"
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_premium_dataset(self) -> Dataset:
        ds = Dataset.objects.create(
            organization=self.org,
            kind=Dataset.Kind.PREMIUM,
            name="Adapter source",
            created_by=self.user,
        )
        PremiumRow.objects.create(
            dataset=ds,
            row_index=0,
            policy_number="POL-1",
            reserving_class="Motor",
            ri_treaty_type="GROSS",
            premium_amount=Decimal("1000.50"),
            commission_amount=Decimal("100.00"),
        )
        PremiumRow.objects.create(
            dataset=ds,
            row_index=1,
            policy_number="POL-2",
            reserving_class="Property",
            ri_treaty_type="RI",
            premium_amount=Decimal("2500.00"),
            commission_amount=Decimal("250.00"),
        )
        ds.refresh_row_count()
        return ds

    def test_snapshot_to_xlsx_round_trip(self):
        ds = self._seed_premium_dataset()
        snap = create_snapshot(dataset=ds)

        out_file = write_snapshot_to_folder(snap, self.tmp)
        self.assertTrue(out_file.exists())
        self.assertTrue(out_file.suffix == ".xlsx")

        df = pd.read_excel(out_file, engine="openpyxl")
        self.assertEqual(set(df.columns) >= {
            "POLICYNUMBER",
            "RESERVINGCLASS",
            "RI_TREATY_TYPE",
            "PREMIUMAMOUNT",
            "COMMISSIONAMOUNT",
        }, True)
        self.assertEqual(list(df["POLICYNUMBER"]), ["POL-1", "POL-2"])
        self.assertEqual(list(df["RESERVINGCLASS"]), ["Motor", "Property"])
        self.assertEqual(list(df["RI_TREATY_TYPE"]), ["GROSS", "RI"])

    def test_materialize_locks_dataset_and_writes_files(self):
        ds = self._seed_premium_dataset()
        snaps = materialize_datasets_to_folder(
            datasets=[ds], folder=self.tmp
        )
        self.assertEqual(len(snaps), 1)
        # Folder contains the xlsx
        xlsx_files = list(self.tmp.glob("*.xlsx"))
        self.assertEqual(len(xlsx_files), 1)
        # Source dataset is now locked
        ds.refresh_from_db()
        self.assertEqual(ds.status, Dataset.Status.LOCKED)

    def test_empty_dataset_produces_header_only_sheet(self):
        ds = Dataset.objects.create(
            organization=self.org,
            kind=Dataset.Kind.PREMIUM,
            name="Empty",
            created_by=self.user,
        )
        snap = create_snapshot(dataset=ds)
        out_file = write_snapshot_to_folder(snap, self.tmp)
        df = pd.read_excel(out_file, engine="openpyxl")
        self.assertEqual(len(df), 0)
        self.assertIn("RESERVINGCLASS", df.columns)
