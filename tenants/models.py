import uuid

from django.conf import settings
from django.db import models
from django.utils.text import slugify


class Organization(models.Model):
    """A tenant. Owns memberships, jobs, and any future tenant-scoped resources."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(max_length=64, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_organizations",
    )

    # Retention policy: output ZIPs are eligible for cleanup this many days
    # after job success. Null means retain indefinitely.
    default_output_retention_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Output ZIPs become eligible for the daily cleanup sweep this "
            "many days after the job succeeds. Leave blank to retain forever."
        ),
    )

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name) or "org"
            slug = base
            n = 1
            while Organization.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                n += 1
                slug = f"{base}-{n}"
            self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Membership(models.Model):
    """A user's membership in an organization, with roles scoped to that org."""

    STATUS_CHOICES = [
        ("active", "Active"),
        ("suspended", "Suspended"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    roles = models.ManyToManyField(
        "accounts.Role",
        related_name="memberships",
        blank=True,
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="active")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("user", "organization")]
        ordering = ["organization__name", "user__email"]

    def __str__(self):
        return f"{self.user.email} @ {self.organization.name}"
