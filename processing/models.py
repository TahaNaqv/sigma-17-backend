import uuid

from django.conf import settings
from django.db import models


class Module1Job(models.Model):
    class JobType(models.TextChoices):
        SUMMARY = "summary", "Summary"
        POLICY_UPR = "policy_upr", "Policy UPR"
        UPDATE_RESERVE = "update_reserve", "Update Reserve"
        UW_PARAMETERS = "uw_parameters", "UW Parameters"

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
    input_meta = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.job_type} {self.id} ({self.status})"
