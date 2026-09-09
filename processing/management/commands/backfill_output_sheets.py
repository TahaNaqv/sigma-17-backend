"""Backfill Module1Job.output_sheets for jobs that predate the sheet index.

Until a job is indexed, chaining treats its sheet list as UNKNOWN and keeps
offering it as a source — safe, but it means a job whose Combined_Summary lacks
`ULAE-RA` / `Discount Rate` is still listed for Cash Flow Allocation until
something looks. The candidate list fills the index lazily as pages are viewed;
this command does the whole backlog in one pass so the picker is accurate
immediately after a deploy.

Idempotent and interruptible: re-running only re-reads what is still unindexed.

    python manage.py backfill_output_sheets
    python manage.py backfill_output_sheets --dry-run
    python manage.py backfill_output_sheets --batch-size 200 --all
"""

from functools import reduce
from operator import or_

from django.core.management.base import BaseCommand
from django.db.models import Q

from processing.models import Module1Job
from processing.services.source_resolver import (
    INDEXABLE_ARTIFACTS,
    list_sheets_in_zip,
    stamp_output_sheets,
)


class Command(BaseCommand):
    help = "Backfill Module1Job.output_sheets for existing successful jobs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Re-index every job, not just those with no index yet.",
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
            help="Report what would be stamped without saving.",
        )

    def handle(self, *args, **opts):
        do_all: bool = opts["all"]
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
            # Only jobs that actually carry a chainable artifact are worth
            # reading. `__contains` is JSONB `@>`; ArrayField's `__overlap` is
            # not available on a JSONField, so OR one containment per artifact.
            .filter(reduce(or_, (
                Q(output_artifacts__contains=[a]) for a in INDEXABLE_ARTIFACTS
            )))
        )
        if not do_all:
            qs = qs.filter(output_sheets={})

        qs = qs.order_by("-completed_at")[:batch_size]

        examined = updated = unreadable = 0
        for job in qs.iterator():
            examined += 1
            index = {}
            for artifact in INDEXABLE_ARTIFACTS:
                if artifact not in (job.output_artifacts or []):
                    continue
                sheets = list_sheets_in_zip(job.output_zip, artifact)
                if sheets:
                    index[artifact] = sheets
            if not index:
                unreadable += 1
                self.stdout.write(self.style.WARNING(
                    f"  {job.id} ({job.job_type}): no readable workbook — left unindexed"
                ))
                continue
            if dry_run:
                for artifact, sheets in index.items():
                    self.stdout.write(
                        f"  would stamp {job.id} {artifact}: {len(sheets)} sheet(s)"
                    )
                continue
            stamp_output_sheets(job, index)
            job.save(update_fields=["output_sheets"])
            updated += 1

        self.stdout.write(self.style.SUCCESS(
            f"Done. examined={examined} updated={updated} "
            f"unreadable={unreadable} dry_run={dry_run}"
        ))
