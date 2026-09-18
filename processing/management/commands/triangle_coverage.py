"""Report what a Summary job's own inputs can support for reported triangles and EP.

The reported (incurred) triangle is limited by the *frequency* of the outstanding extract,
not by the software: an outstanding balance is only known at a valuation date, so each
distinct ``As at`` supplies one diagonal. This command states, per grain, whether that
extract can carry a development triangle at all — before anyone selects a factor from one.

    python manage.py triangle_coverage <job-id>
    python manage.py triangle_coverage <job-id> --grain quarterly
    python manage.py triangle_coverage --latest 5
    python manage.py triangle_coverage <job-id> --json
"""

import json

from django.core.management.base import BaseCommand, CommandError

from core.grain import GRAIN_KEYS, get_grain
from processing.models import Module1Job


class Command(BaseCommand):
    help = "Report valuation coverage and earned-premium availability for Summary jobs."

    def add_arguments(self, parser):
        parser.add_argument("job_id", nargs="?", help="Module1Job id (a prefix is enough).")
        parser.add_argument(
            "--latest", type=int, metavar="N",
            help="Report the N most recent successful Summary jobs instead of one id.",
        )
        parser.add_argument(
            "--grain", choices=list(GRAIN_KEYS),
            help="Report one grain only (default: all three).",
        )
        parser.add_argument("--json", action="store_true", help="Machine-readable output.")

    def handle(self, *args, **options):
        jobs = self._resolve(options)
        grains = [get_grain(options["grain"])] if options.get("grain") else [
            get_grain(k) for k in GRAIN_KEYS
        ]
        report = [self._report_job(job, grains) for job in jobs]

        if options["json"]:
            self.stdout.write(json.dumps(report, indent=2, default=str))
            return
        for entry in report:
            self._render(entry)

    # -- resolution ---------------------------------------------------------

    def _resolve(self, options):
        qs = Module1Job.objects.filter(job_type=Module1Job.JobType.SUMMARY)
        if options.get("latest"):
            jobs = list(
                qs.filter(status=Module1Job.Status.SUCCESS).order_by("-created_at")[
                    : options["latest"]
                ]
            )
            if not jobs:
                raise CommandError("No successful Summary jobs found.")
            return jobs
        job_id = options.get("job_id")
        if not job_id:
            raise CommandError("Give a job id, or --latest N.")
        jobs = list(qs.filter(id__startswith=job_id)[:2]) if len(job_id) < 36 else list(
            qs.filter(id=job_id)[:2]
        )
        if not jobs:
            raise CommandError(f"No Summary job matches {job_id!r}.")
        if len(jobs) > 1:
            raise CommandError(f"{job_id!r} matches more than one job; give more characters.")
        return jobs

    # -- reporting ----------------------------------------------------------

    def _report_job(self, job, grains):
        from module1_engine.triangles import valuation_coverage
        from processing.views import _os_source_frame

        meta = job.input_meta or {}
        entry = {
            "job": str(job.id),
            "status": job.status,
            "experience": f"{meta.get('exp_start', '?')} → {meta.get('exp_end', '?')}",
            "grains": {},
            # Earned premium needs the premium rows, which upload-driven runs did not retain
            # before the archiving change. Reported here because an empty EP panel with no
            # explanation is the failure mode this command exists to pre-empt.
            "earned_premium": self._ep_availability(job, meta),
        }
        frame = _os_source_frame(job)
        if frame is None or frame.empty:
            entry["error"] = "This job's outstanding-claims data is no longer available."
            return entry

        for grain in grains:
            coverage = valuation_coverage(
                frame, grain=grain,
                start=meta.get("exp_start"), end=meta.get("exp_end"),
            )
            entry["grains"][grain.key] = coverage.to_dict()
        return entry

    @staticmethod
    def _ep_availability(job, meta):
        if (meta.get("dataset_snapshots") or {}).get("premium"):
            return {"available": True, "source": "dataset snapshot"}
        from processing.tasks import INPUT_ARCHIVE_PREMIUM_PREFIX

        if job.input_archive:
            import zipfile

            try:
                with job.input_archive.open("rb") as fh:
                    names = zipfile.ZipFile(fh).namelist()
                if any(n.startswith(INPUT_ARCHIVE_PREMIUM_PREFIX) for n in names):
                    return {"available": True, "source": "input archive"}
            except (OSError, ValueError, zipfile.BadZipFile):
                pass
        return {
            "available": False,
            "reason": "Premium was not retained for this run; earned premium can only be "
                      "shown for runs made after premium archiving was enabled.",
        }

    def _render(self, entry):
        w = self.stdout.write
        w(self.style.MIGRATE_HEADING(f"\nJob {entry['job'][:8]}  ({entry['status']})"))
        w(f"  experience      {entry['experience']}")
        ep = entry["earned_premium"]
        w("  earned premium  " + (
            self.style.SUCCESS(f"available ({ep['source']})") if ep["available"]
            else self.style.WARNING("unavailable — " + ep["reason"])
        ))
        if entry.get("error"):
            w(self.style.ERROR("  " + entry["error"]))
            return
        for key, c in entry["grains"].items():
            tone = self.style.SUCCESS if c["supports_factors"] else self.style.WARNING
            w(f"  {key:<10} {tone(c['shape']):<22} "
              f"coverage {c['coverage_ratio']:.0%}  factors: {'yes' if c['supports_factors'] else 'no'}")
            for note in c["warnings"]:
                w(f"             {note}")
        first = next(iter(entry["grains"].values()), None)
        if first and first["valuation_periods"]:
            w(f"  valuations      {', '.join(first['valuation_periods'][:12])}")
