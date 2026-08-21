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


# ---------------------------------------------------------------------------
# Sensitivity / stress-testing scenario sets
# ---------------------------------------------------------------------------


class ScenarioSet(models.Model):
    """A named, versioned collection of parameter shocks.

    A sensitivity disclosure is only comparable period-over-period if the same
    shocks are applied each time, so the shock definition is a durable org-level
    object rather than a per-run form field. Editing an active set FORKS a new
    version; jobs snapshot the resolved scenario list into ``input_meta`` so a run
    replays even if the set is later changed or deleted.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="scenario_sets", db_index=True
    )
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="created_scenario_sets",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name", "version"],
                name="uniq_scenario_set_version",
            ),
            models.UniqueConstraint(
                fields=["organization", "name"],
                condition=models.Q(is_active=True),
                name="uniq_active_scenario_set_per_name",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "is_active"], name="scenarioset_org_active_idx"),
        ]

    def __str__(self):
        return f"{self.name} v{self.version}"

    def resolved(self) -> list[dict]:
        """Plain-dict shock list for the engine. No Django types cross the boundary."""
        return [s.to_shock_dict() for s in self.scenarios.all()]


class Scenario(models.Model):
    """One shock within a set.

    ``magnitude`` units are lever-specific and NOT interchangeable:
      ra       -> relative fraction  (0.10 == +10% of the existing RA loading)
      discount -> absolute basis points on the CY annual spot curve (5 == +5bp)
      ulr      -> absolute fraction  (0.05 == +5 percentage points)
    """

    class Lever(models.TextChoices):
        RA = "ra", "Risk adjustment"
        DISCOUNT = "discount", "Discount curve"
        ULR = "ulr", "Loss ratio"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scenario_set = models.ForeignKey(
        ScenarioSet, on_delete=models.CASCADE, related_name="scenarios"
    )
    label = models.CharField(max_length=64)
    lever = models.CharField(max_length=16, choices=Lever.choices)
    magnitude = models.DecimalField(max_digits=12, decimal_places=6)
    scope_classes = models.JSONField(default=list, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "label"]

    def __str__(self):
        return f"{self.label} ({self.lever})"

    def to_shock_dict(self) -> dict:
        return {
            "label": self.label,
            "lever": self.lever,
            "magnitude": float(self.magnitude),
            "scope_classes": list(self.scope_classes or []),
        }
