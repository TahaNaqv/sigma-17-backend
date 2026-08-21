"""Seed the default sensitivity scenario set for an organization.

The default set implements the client's stated request — RA +/-%, discounting
+/-5bp, loss ratio +/-5pp — widened to a ladder, because a single 5bp point gives
the reader no sense of whether a response is linear.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from tenants.models import Organization, Scenario, ScenarioSet

DEFAULT_SET_NAME = "Standard sensitivity"

#: (label, lever, magnitude). Units are lever-specific — see Scenario docstring.
DEFAULT_SCENARIOS = [
    ("RA -25%", Scenario.Lever.RA, -0.25),
    ("RA -10%", Scenario.Lever.RA, -0.10),
    ("RA +10%", Scenario.Lever.RA, 0.10),
    ("RA +25%", Scenario.Lever.RA, 0.25),
    ("Discount -25bp", Scenario.Lever.DISCOUNT, -25),
    ("Discount -5bp", Scenario.Lever.DISCOUNT, -5),
    ("Discount +5bp", Scenario.Lever.DISCOUNT, 5),
    ("Discount +25bp", Scenario.Lever.DISCOUNT, 25),
    ("ULR -10pp", Scenario.Lever.ULR, -0.10),
    ("ULR -5pp", Scenario.Lever.ULR, -0.05),
    ("ULR +5pp", Scenario.Lever.ULR, 0.05),
    ("ULR +10pp", Scenario.Lever.ULR, 0.10),
]


class Command(BaseCommand):
    help = "Create the default sensitivity scenario set for one or all organizations."

    def add_arguments(self, parser):
        parser.add_argument("--org", help="Organization slug (default: all).")
        parser.add_argument(
            "--force", action="store_true",
            help="Replace the scenarios of an existing active set of the same name.",
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        orgs = Organization.objects.all()
        if opts.get("org"):
            orgs = orgs.filter(slug=opts["org"])
            if not orgs.exists():
                self.stderr.write(self.style.ERROR(f"No organization {opts['org']!r}."))
                return

        for org in orgs:
            existing = ScenarioSet.objects.filter(
                organization=org, name=DEFAULT_SET_NAME, is_active=True
            ).first()
            if existing and not opts["force"]:
                self.stdout.write(f"  {org.slug}: already seeded (v{existing.version}) — skipped")
                continue
            if existing:
                existing.scenarios.all().delete()
                target = existing
            else:
                target = ScenarioSet.objects.create(
                    organization=org,
                    name=DEFAULT_SET_NAME,
                    description=(
                        "Risk adjustment (relative), discount curve (basis points, CY only) "
                        "and loss ratio (percentage points) shocks."
                    ),
                )
            Scenario.objects.bulk_create([
                Scenario(scenario_set=target, label=label, lever=lever,
                         magnitude=mag, order=i)
                for i, (label, lever, mag) in enumerate(DEFAULT_SCENARIOS)
            ])
            self.stdout.write(self.style.SUCCESS(
                f"  {org.slug}: seeded {len(DEFAULT_SCENARIOS)} scenarios "
                f"into '{DEFAULT_SET_NAME}' v{target.version}"
            ))
