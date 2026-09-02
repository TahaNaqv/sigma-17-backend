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

    class PreflightMode(models.TextChoices):
        STRICT = "strict", "Strict — block a run with reconciliation errors"
        PERMISSIVE = "permissive", "Permissive — warn but run anyway"

    #: How the Module 1 input pre-flight gate behaves for this organization.
    #:
    #: `strict` is the default and the intended steady state: a run whose reserving classes do
    #: not reconcile produces a plausible workbook containing a wrong number, which is worse
    #: than no workbook. `permissive` exists only so that enabling the gate cannot hard-block
    #: an organization mid-valuation; the UI labels it as a temporary state.
    preflight_mode = models.CharField(
        max_length=16,
        choices=PreflightMode.choices,
        default=PreflightMode.STRICT,
        help_text=(
            "Strict blocks a reserving run whose claims and premium classes do not "
            "reconcile. Permissive runs anyway and records the report."
        ),
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


# ---------------------------------------------------------------------------
# UPR earning-method policy (requirement 4)
# ---------------------------------------------------------------------------


class UprMethodPolicy(models.Model):
    """A named, versioned set of UPR earning-method rules for one organization.

    UPR methodology is a standing actuarial choice, not a per-run option: it must be stable
    across periods, auditable, and reproducible. So it is a durable org-level object,
    versioned on edit, and snapshotted into each job's ``input_meta`` — a run replays with
    the exact rules it used even if the policy is later changed or deleted.

    The shipped default is pro-rata for every class, which is **proven** bit-identical to
    the historic hard-coded behaviour (docs/UPR_METHOD_SELECTION_PLAN.md §1.3), so adopting
    the feature changes nothing until a rule is deliberately added.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="upr_policies", db_index=True
    )
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True, db_index=True)
    #: Free text stamped on the version. Also carries the recorded acknowledgement when a
    #: user overrides a book-suitability block (see module1_engine.upr_guard).
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="created_upr_policies",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name", "version"],
                name="uniq_upr_policy_version",
            ),
            models.UniqueConstraint(
                fields=["organization", "name"],
                condition=models.Q(is_active=True),
                name="uniq_active_upr_policy_per_name",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "is_active"], name="uprpolicy_org_active_idx"),
        ]

    def __str__(self):
        return f"{self.name} v{self.version}"

    def resolved(self) -> list[dict]:
        """Plain-dict rule list for the engine. No Django types cross the boundary."""
        return [r.to_rule_dict() for r in self.rules.all()]


class UprMethodRule(models.Model):
    """One rule. Blank ``reserving_class`` matches every class; blank ``product_type``
    makes the rule the class-level default.

    Matching is normalised and never literal — exact-literal matching is precisely why the
    historic CAR/EAR and marine branches never fired (plan §1.2).
    """

    class MatchMode(models.TextChoices):
        EXACT = "exact", "Exact"
        CONTAINS = "contains", "Contains"
        PREFIX = "prefix", "Starts with"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    policy = models.ForeignKey(
        UprMethodPolicy, on_delete=models.CASCADE, related_name="rules"
    )
    reserving_class = models.CharField(max_length=128, blank=True)
    product_type = models.CharField(max_length=128, blank=True)
    match_mode = models.CharField(
        max_length=16, choices=MatchMode.choices, default=MatchMode.EXACT
    )
    method = models.CharField(max_length=32)
    params = models.JSONField(default=dict, blank=True)
    priority = models.IntegerField(default=0)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        scope = self.reserving_class or "(all classes)"
        if self.product_type:
            scope = f"{scope} / {self.product_type}"
        return f"{scope} -> {self.method}"

    def to_rule_dict(self) -> dict:
        return {
            "method": self.method,
            "reserving_class": self.reserving_class,
            "product_type": self.product_type,
            "match_mode": self.match_mode,
            "params": dict(self.params or {}),
            "priority": self.priority,
        }


class ReservingClassAlias(models.Model):
    """Maps a spelling found in an input file onto the premium file's spelling.

    The engine joins premium to claims by exact string equality on RESERVINGCLASS, and a
    mismatch is silent: on the reference book the claims files say `Health` where premium says
    `Health Insurance`, and all 3,044 of those rows — 35,503,674 — are discarded without a
    warning (defect F1).

    Case and punctuation differences never need an alias; `core.normalize.canonical_key`
    absorbs those. An alias is for a genuine naming difference, and because it changes which
    claims enter a reserve it is an actuarial decision: recorded per organization, attributed,
    and never applied automatically from a suggestion.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="reserving_class_aliases"
    )
    #: As found in a claims or premium file.
    alias = models.CharField(max_length=128)
    #: The spelling the premium file uses, which the engine loops over.
    canonical = models.CharField(max_length=128)
    #: Denormalised match key, so lookups and the uniqueness constraint ignore case and
    #: punctuation — otherwise "health" and "Health " could both be stored and disagree.
    alias_key = models.CharField(max_length=128, editable=False, db_index=True)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_class_aliases",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["canonical", "alias"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "alias_key"], name="uniq_class_alias_per_org"
            )
        ]

    def save(self, *args, **kwargs):
        from core.normalize import canonical_key

        self.alias = str(self.alias).strip()
        self.canonical = str(self.canonical).strip()
        self.alias_key = canonical_key(self.alias)
        return super().save(*args, **kwargs)

    def clean(self):
        from django.core.exceptions import ValidationError

        from core.normalize import canonical_key

        if not str(self.alias).strip() or not str(self.canonical).strip():
            raise ValidationError("Both the alias and the canonical class are required.")
        if canonical_key(self.alias) == canonical_key(self.canonical):
            raise ValidationError(
                "The alias and the canonical class are already the same once case and "
                "punctuation are ignored, so no alias is needed."
            )
        # One hop only. A chain (A->B, B->C) would make the resolved value depend on
        # iteration order, which is not something an audit could reconstruct.
        siblings = ReservingClassAlias.objects.filter(organization=self.organization)
        if self.pk:
            siblings = siblings.exclude(pk=self.pk)
        if siblings.filter(alias_key=canonical_key(self.canonical)).exists():
            raise ValidationError(
                f"'{self.canonical}' is itself an alias. Point this alias at the class the "
                f"premium file actually uses instead of chaining."
            )
        if siblings.filter(canonical=self.alias).exists():
            raise ValidationError(
                f"'{self.alias}' is already used as a canonical class by another alias."
            )

    def __str__(self) -> str:
        return f"{self.alias} -> {self.canonical}"


def alias_map_for(organization) -> dict[str, str]:
    """`{alias: canonical}` for one organization, or `{}`.

    Resolved to a plain dict at job-creation time and snapshotted onto the job, never read
    live at run time: a re-run months later must apply the aliases the run actually used.
    """
    if organization is None:
        return {}
    return {
        a.alias: a.canonical
        for a in ReservingClassAlias.objects.filter(organization=organization)
    }
