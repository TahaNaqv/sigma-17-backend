"""Dataset-backed input for actuarial jobs.

A `Dataset` is a named, org-scoped collection of input rows of a single
`kind` (e.g. premium, claims_paid, claims_os). Datasets replace the
"upload an Excel file every time you run a job" workflow with a persistent,
auditable data layer that users can curate directly in the UI.

Snapshot-on-run semantics: when a job consumes a dataset, a
`DatasetSnapshot` is created that freezes the exact rows the engine
operated on. Re-running the job months later reproduces the same result
even if the source dataset has since been edited. The snapshot is the
audit-grade record of "what data ran through the engine for this job."

Excel uploads are not removed — they auto-create a Dataset with
`source = excel_import` so all consumer code paths inside the engine
adapter see the same shape regardless of how the data entered the system.
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class Dataset(models.Model):
    """A named, org-scoped collection of input rows for one job-input kind."""

    class Kind(models.TextChoices):
        PREMIUM = "premium", "Premium"
        CLAIMS_PAID = "claims_paid", "Claims Paid"
        CLAIMS_OS = "claims_os", "Claims Outstanding"
        # Module 2 Process inputs. PREVIOUS_PERIOD_LIC and PREVIOUS_PERIOD_UPR
        # are two separate datasets that the adapter writes as two sheets in
        # a single Previous_Period.xlsx workbook for the engine.
        EXPENSE_CF = "expense_cf", "Expense Cash Flow"
        PREVIOUS_PERIOD_LIC = "previous_period_lic", "Previous Period — LIC_BOP"
        PREVIOUS_PERIOD_UPR = "previous_period_upr", "Previous Period — UPR-DAC_BOP"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        LOCKED = "locked", "Locked"

    class Source(models.TextChoices):
        MANUAL = "manual", "Manual entry"
        EXCEL_IMPORT = "excel_import", "Excel import"
        CSV_IMPORT = "csv_import", "CSV import"
        API = "api", "API"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "tenants.Organization",
        on_delete=models.CASCADE,
        related_name="datasets",
        db_index=True,
    )
    kind = models.CharField(max_length=32, choices=Kind.choices, db_index=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    source = models.CharField(
        max_length=24,
        choices=Source.choices,
        default=Source.MANUAL,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_datasets",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    # Denormalized count — refreshed on every bulk mutation. Cheaper than
    # COUNT(*) on the list endpoint where we'll regularly show "1,234 rows".
    row_count = models.PositiveIntegerField(default=0)

    # If this dataset was produced from forking another (e.g. user edits a
    # locked dataset and we copy-on-write a new draft), track the lineage.
    forked_from = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="forks",
    )

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "kind", "name"],
                name="dataset_unique_name_per_kind_per_org",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "kind", "status", "-updated_at"],
                name="dataset_org_kind_status_idx",
            ),
        ]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.name} ({self.status})"

    @property
    def is_locked(self) -> bool:
        return self.status == self.Status.LOCKED

    def lock(self, *, by_user=None):
        """Idempotent lock; once locked, rows are read-only via the API."""
        if self.is_locked:
            return
        self.status = self.Status.LOCKED
        self.locked_at = timezone.now()
        if by_user is not None:
            self.locked_by = by_user
        self.save(update_fields=["status", "locked_at", "locked_by", "updated_at"])

    def refresh_row_count(self, *, save: bool = True) -> int:
        """Recompute `row_count` from the appropriate row table.

        Called after any bulk insert/delete. Cheap if rows live in the
        same DB as the dataset (postgres count is fast on indexed FK).
        """
        manager = self.rows_manager()
        count = manager.filter(dataset=self).count() if manager else 0
        self.row_count = count
        if save:
            self.save(update_fields=["row_count", "updated_at"])
        return count

    def rows_manager(self):
        """Return the row model manager for this dataset's kind, or None."""
        model = ROW_MODEL_FOR_KIND.get(self.kind)
        return model.objects if model else None


# ---------------------------------------------------------------------------
# Row models — typed columns mirroring the Excel schemas the engine expects.
# Column names are snake_case here; the engine adapter maps them to the
# mixed-case names the pandas pipeline uses (POLICYSTARTDATE, etc.).
# ---------------------------------------------------------------------------


class _BaseRow(models.Model):
    """Shared fields for every row table.

    `row_index` preserves the original order rows were entered or imported
    in; the engine's pandas pipeline doesn't depend on it, but it gives
    users a stable position in the UI grid.
    """

    id = models.BigAutoField(primary_key=True)
    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.CASCADE,
        related_name="%(class)s_rows",
        db_index=True,
    )
    row_index = models.PositiveIntegerField(default=0, db_index=True)

    class Meta:
        abstract = True
        ordering = ["dataset_id", "row_index", "id"]


class _TreatyType(models.TextChoices):
    GROSS = "GROSS", "Gross"
    RI = "RI", "Reinsurance"


class PremiumRow(_BaseRow):
    """One row of policy/premium data. Maps to the Excel `premium` files."""

    policy_number = models.CharField(max_length=64, blank=True)  # POLICYNUMBER
    policy_start_date = models.DateField(null=True, blank=True)  # POLICYSTARTDATE
    policy_end_date = models.DateField(null=True, blank=True)    # POLICYENDDATE
    risk_start_date = models.DateField(null=True, blank=True)    # RiskStartDate
    risk_end_date = models.DateField(null=True, blank=True)      # RiskEndDate
    issue_date = models.DateField(null=True, blank=True)         # ISSUEDATE

    reserving_class = models.CharField(max_length=128, db_index=True)  # RESERVINGCLASS
    policy_class = models.CharField(max_length=128, blank=True)        # POLICYCLASS
    product_type = models.CharField(max_length=128, blank=True)        # PRODUCTTYPE
    ri_treaty_type = models.CharField(
        max_length=16,
        choices=_TreatyType.choices,
    )  # RI_TREATY_TYPE

    premium_amount = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )  # PREMIUMAMOUNT
    commission_amount = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )  # COMMISSIONAMOUNT

    class Meta(_BaseRow.Meta):
        indexes = [
            models.Index(fields=["dataset", "reserving_class"]),
        ]


class ClaimsPaidRow(_BaseRow):
    """One row of claims paid data. Maps to the Excel `claims_paid` files."""

    amount_paid = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )  # AMOUNTPAID
    amount_recovered = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )  # AMOUNTRECOVERED

    issue_date = models.DateField(null=True, blank=True)
    loss_date = models.DateField(null=True, blank=True)
    payment_date = models.DateField(null=True, blank=True)

    reserving_class = models.CharField(max_length=128, db_index=True)
    policy_class = models.CharField(max_length=128, blank=True)
    ri_treaty_type = models.CharField(max_length=16, choices=_TreatyType.choices)
    head_of_damage = models.CharField(max_length=128, blank=True)  # HEADOFDAMAGE

    class Meta(_BaseRow.Meta):
        indexes = [
            models.Index(fields=["dataset", "reserving_class"]),
        ]


class ClaimsOSRow(_BaseRow):
    """One row of claims outstanding data. Maps to the Excel `claims_os` files.

    `as_at` is the snapshot-date column the engine reads as "As at" (note
    the space in the Excel header). We expose it as snake_case here.
    """

    amount_outstanding = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )  # AMOUNTOUTSTANDING
    issue_date = models.DateField(null=True, blank=True)
    loss_date = models.DateField(null=True, blank=True)
    as_at = models.DateField(null=True, blank=True)  # "As at"

    reserving_class = models.CharField(max_length=128, db_index=True)
    policy_class = models.CharField(max_length=128, blank=True)
    ri_treaty_type = models.CharField(max_length=16, choices=_TreatyType.choices)
    head_of_damage = models.CharField(max_length=128, blank=True)

    class Meta(_BaseRow.Meta):
        indexes = [
            models.Index(fields=["dataset", "reserving_class"]),
        ]


class ExpenseCfRow(_BaseRow):
    """One row of the Expense-CF sheet (Module 2 Process input).

    Matches the columns the Module 2 engine reads from `Expense-CF`:
    `RESERVINGCLASS`, `UWY`, and the prev/curr expense components.
    """

    reserving_class = models.CharField(max_length=128, db_index=True)
    uwy = models.IntegerField()
    comm_payable_prev = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )  # Comm_Payable_prev
    comm_payable_curr = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )  # Comm_Payable_curr
    rec_gop_prev = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )  # Rec_GOP_prev
    rec_gop_curr = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )  # Rec_GOP_curr
    rec_provision_prev = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )  # Rec_Provision_prev
    rec_provision_curr = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )  # Rec_Provision_curr

    class Meta(_BaseRow.Meta):
        indexes = [
            models.Index(fields=["dataset", "reserving_class"]),
        ]


class PreviousPeriodLicRow(_BaseRow):
    """One row of the LIC_BOP sheet — claims-side balances at beginning
    of period. Required columns from module2_engine `_require_columns`:
    RESERVINGCLASS, UWY, Accident_Period, GROSS/RI, Outstanding, SS,
    Payment, S&S, ULAE, RA (OS), RA (IBNR), Discounting Impact.
    """

    reserving_class = models.CharField(max_length=128, db_index=True)
    uwy = models.IntegerField()
    accident_period = models.CharField(max_length=32)
    gross_ri = models.CharField(max_length=16, choices=_TreatyType.choices)
    outstanding = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )
    ss = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    payment = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )
    s_and_s = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )  # S&S
    ulae = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )
    ra_os = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )  # RA (OS)
    ra_ibnr = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )  # RA (IBNR)
    discounting_impact = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )

    class Meta(_BaseRow.Meta):
        indexes = [
            models.Index(fields=["dataset", "reserving_class"]),
        ]


class PreviousPeriodUprRow(_BaseRow):
    """One row of the UPR-DAC_BOP sheet — premium-side balances at
    beginning of period. Required columns: RESERVINGCLASS, UWY,
    Gross UPR, DAC, RI UPR, UCR."""

    reserving_class = models.CharField(max_length=128, db_index=True)
    uwy = models.IntegerField()
    gross_upr = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )  # Gross UPR
    dac = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    ri_upr = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )  # RI UPR
    ucr = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    class Meta(_BaseRow.Meta):
        indexes = [
            models.Index(fields=["dataset", "reserving_class"]),
        ]


# Kind → row model lookup. Adapter and serializers use this to route to
# the right table without an if/elif chain.
ROW_MODEL_FOR_KIND = {
    Dataset.Kind.PREMIUM: PremiumRow,
    Dataset.Kind.CLAIMS_PAID: ClaimsPaidRow,
    Dataset.Kind.CLAIMS_OS: ClaimsOSRow,
    Dataset.Kind.EXPENSE_CF: ExpenseCfRow,
    Dataset.Kind.PREVIOUS_PERIOD_LIC: PreviousPeriodLicRow,
    Dataset.Kind.PREVIOUS_PERIOD_UPR: PreviousPeriodUprRow,
}


# ---------------------------------------------------------------------------
# Snapshot table — the reproducibility record.
# ---------------------------------------------------------------------------


class DatasetSnapshot(models.Model):
    """Frozen row payload captured at the moment a job consumed a dataset.

    The job's engine adapter reads rows from the snapshot, never from
    the live dataset, so subsequent edits to the dataset don't change
    historical job inputs.

    For Phase 1 we store the row payload inline as JSON (compact, simple,
    works for the dataset sizes we see today). If/when datasets grow past
    ~100k rows we'll move the payload to a file (parquet) and keep just
    the metadata here; the API contract is unaffected.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.PROTECT,  # snapshots outlive dataset deletion attempts
        related_name="snapshots",
        db_index=True,
    )
    organization = models.ForeignKey(
        "tenants.Organization",
        on_delete=models.CASCADE,
        related_name="dataset_snapshots",
        db_index=True,
    )
    consumer_job = models.ForeignKey(
        "processing.Module1Job",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dataset_snapshots",
        db_index=True,
    )

    kind = models.CharField(max_length=32, choices=Dataset.Kind.choices)
    row_count = models.PositiveIntegerField(default=0)

    # List of row dicts, in stable row_index order, with snake_case keys.
    # Adapter converts to DataFrame; see datasets/services/snapshots.py.
    rows_payload = models.JSONField(default=list)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["organization", "kind", "-created_at"],
                name="dssnap_org_kind_created_idx",
            ),
        ]

    def __str__(self):
        return f"Snapshot of {self.dataset_id} ({self.row_count} rows)"
