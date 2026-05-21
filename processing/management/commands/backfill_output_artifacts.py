"""One-shot backfill for Module1Job.output_artifacts.

Walks every successful job that has an output_zip, reads its central
directory, and stamps the artifact list. Safe to re-run: idempotent.

    python manage.py backfill_output_artifacts
    python manage.py backfill_output_artifacts --only-empty
    python manage.py backfill_output_artifacts --batch-size 200 --dry-run
"""

from django.core.management.base import BaseCommand

from processing.models import Module1Job
from processing.services.source_resolver import list_artifacts_in_zip


class Command(BaseCommand):
    help = "Backfill Module1Job.output_artifacts for existing successful jobs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--only-empty",
            action="store_true",
            help="Skip jobs that already have output_artifacts populated.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Maximum number of jobs to process this run.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute the artifact lists but do not save them.",
        )

    def handle(self, *args, **opts):
        only_empty: bool = opts["only_empty"]
        batch_size: int = opts["batch_size"]
        dry_run: bool = opts["dry_run"]

        qs = (
            Module1Job.objects
            .filter(
                status=Module1Job.Status.SUCCESS,
                output_purged_at__isnull=True,
            )
            .exclude(output_zip="")
            .exclude(output_zip__isnull=True)
        )
        if only_empty:
            qs = qs.filter(output_artifacts=[])

        qs = qs.order_by("-completed_at")[:batch_size]

        examined = 0
        updated = 0
        empty = 0
        for job in qs.iterator():
            examined += 1
            artifacts = list_artifacts_in_zip(job.output_zip)
            if not artifacts:
                empty += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"  {job.id} ({job.job_type}): no readable artifacts"
                    )
                )
                continue
            if sorted(set(artifacts)) == sorted(set(job.output_artifacts or [])):
                continue
            if dry_run:
                self.stdout.write(
                    f"  would update {job.id}: {len(artifacts)} artifact(s)"
                )
                continue
            job.output_artifacts = sorted(set(artifacts))
            job.save(update_fields=["output_artifacts"])
            updated += 1

        self.stdout.write(self.style.SUCCESS(
            f"Done. examined={examined} updated={updated} unreadable={empty} "
            f"dry_run={dry_run}"
        ))
