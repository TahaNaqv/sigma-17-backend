import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class Module1Job(models.Model):
    class JobType(models.TextChoices):
        SUMMARY = "summary", "Summary"
        POLICY_UPR = "policy_upr", "Policy UPR"
        UPDATE_RESERVE = "update_reserve", "Update Reserve"
        UW_PARAMETERS = "uw_parameters", "UW Parameters"
        MODULE2_ALLOCATE = "module2_allocate", "Module2 Allocate"
        MODULE2_PROCESS = "module2_process", "Module2 Process"
        MODULE2_MOVEMENT = "module2_movement", "IFRS 17 Movement Analysis"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="module1_jobs",
    )
    organization = models.ForeignKey(
        "tenants.Organization",
        on_delete=models.CASCADE,
        related_name="module1_jobs",
        db_index=True,
    )
    job_type = models.CharField(max_length=32, choices=JobType.choices)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    work_dir = models.CharField(max_length=512, blank=True)
    output_zip = models.FileField(
        upload_to="module1_jobs/zips/%Y/%m/",
        null=True,
        blank=True,
    )
    # Durable, non-user-facing archive of the canonical input workbooks a job
    # consumed (currently: module2 process/movement Previous_Period.xlsx +
    # Expense_CF.xlsx). Persisted so a downstream job (e.g. a movement analysis
    # chained off a process job) can reuse the exact same inputs without the
    # user re-supplying them. Never surfaced in output preview/download.
    # Purged together with output_zip by retention.
    input_archive = models.FileField(
        upload_to="module1_jobs/inputs/%Y/%m/",
        null=True,
        blank=True,
    )
    input_meta = models.JSONField(default=dict, blank=True)

    # Lineage: which earlier job supplied an input artifact to this one.
    # PROTECT prevents accidental deletion of a referenced source via the ORM;
    # the cascade_purge admin action is the supported way to remove a source
    # that has descendants.
    source_job = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="derived_jobs",
        db_index=True,
    )
    source_artifact = models.CharField(max_length=128, blank=True)

    # Denormalized list of output artifact basenames (relative to the ZIP root).
    # Filled at task-success time; powers the candidate-list filter without
    # cracking open the ZIP per request.
    output_artifacts = models.JSONField(default=list, blank=True)

    # Retention policy fields. retention_until is stamped on success based on
    # the organization's default_output_retention_days; null = retain forever.
    # legal_hold is an admin opt-in regulatory override that skips the sweeper.
    # output_purged_at marks when the output_zip file was deleted by retention.
    retention_until = models.DateTimeField(null=True, blank=True, db_index=True)
    legal_hold = models.BooleanField(default=False, db_index=True)
    output_purged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["organization", "job_type", "status", "-created_at"],
                name="m1job_org_type_status_idx",
            ),
            models.Index(
                fields=["retention_until", "legal_hold"],
                name="m1job_retention_sweep_idx",
            ),
        ]

    def __str__(self):
        return f"{self.job_type} {self.id} ({self.status})"

    @property
    def output_available(self) -> bool:
        """True if the output ZIP is still on disk (success and not purged)."""
        if self.status != self.Status.SUCCESS:
            return False
        if self.output_purged_at is not None:
            return False
        return bool(self.output_zip)

    def purge_output(self, *, reason: str = "retention", actor=None) -> bool:
        """Delete the output ZIP from storage; keep the row.

        Idempotent: safe to call on already-purged or never-output jobs.
        Returns True if a file was deleted.
        """
        if self.output_purged_at is not None:
            return False
        deleted = False
        if self.output_zip:
            try:
                self.output_zip.delete(save=False)
                deleted = True
            except Exception:
                # Storage backend can throw on missing files; we still mark
                # the row as purged so the sweeper doesn't loop on it.
                pass
        # The durable input archive is purged on the same retention clock as the
        # output; a purged job can no longer serve reuse to a downstream job.
        if self.input_archive:
            try:
                self.input_archive.delete(save=False)
                deleted = True
            except Exception:
                pass
        self.output_purged_at = timezone.now()
        # Clear denormalized list since the file is gone.
        self.output_artifacts = []
        self.save(update_fields=[
            "output_zip",
            "input_archive",
            "output_purged_at",
            "output_artifacts",
        ])
        return deleted


class JobDraft(models.Model):
    """Per-(user, organization, wizard) saved copy of an in-progress wizard's
    input state, enabling cross-device / cross-session resume.

    The frontend autosaves the serialisable slice of a wizard's Zustand store
    here (debounced). `state` is opaque JSON owned by the client — the backend
    stores and returns it verbatim, so no server-side schema coupling to the
    wizard shape. One draft per wizard per user per org (upsert on save).

    Uploaded file *bytes* are never stored here (they live only in the origin
    browser); `state` carries lightweight file references that simply don't
    resolve on another device — everything else restores.
    """

    class Key(models.TextChoices):
        SUMMARY = "summary", "Reserve Summary"
        UPDATE_RESERVE = "update_reserve", "Update Reserves"
        IBNR_ALLOCATION = "ibnr_allocation", "Cash Flow Allocation"
        MOVEMENT = "movement", "Movement Analysis"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="job_drafts",
    )
    organization = models.ForeignKey(
        "tenants.Organization",
        on_delete=models.CASCADE,
        related_name="job_drafts",
        db_index=True,
    )
    key = models.CharField(max_length=32, choices=Key.choices)
    state = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "organization", "key"],
                name="uniq_job_draft_user_org_key",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "user", "key"],
                name="jobdraft_scope_idx",
            ),
        ]

    def __str__(self):
        return f"JobDraft {self.key} u={self.user_id} org={self.organization_id}"
