"""Freeze current engine output as the golden set for one or all fixtures.

Run this on the CURRENT (pre-optimisation) code to lock known-good results,
then the golden regression tests / ``bench_engines --check`` will fail loudly
if any optimisation changes a value.

    python manage.py capture_golden --all
    python manage.py capture_golden --only summary_large
    python manage.py capture_golden --all --force     # overwrite existing goldens
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from processing import benchmarks, golden


class Command(BaseCommand):
    help = "Capture golden outputs for benchmark fixtures from the current engine code."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Capture for every fixture.")
        parser.add_argument("--only", default=None, help="Capture only the named fixture.")
        parser.add_argument("--force", action="store_true", help="Overwrite existing goldens.")

    def handle(self, *args, **opts):
        if not opts["all"] and not opts["only"]:
            raise CommandError("specify --all or --only <fixture>")

        fixtures = benchmarks.discover_fixtures()
        if opts["only"]:
            fixtures = [f for f in fixtures if f.name == opts["only"]]
            if not fixtures:
                raise CommandError(f"no fixture named {opts['only']!r}")
        if not fixtures:
            self.stdout.write(self.style.WARNING(
                f"No fixtures under {benchmarks.FIXTURES_DIR} — see benchmarks/README.md"
            ))
            return

        for fx in fixtures:
            exists = (fx.golden_dir / "manifest.json").exists()
            if exists and not opts["force"]:
                self.stdout.write(self.style.WARNING(
                    f"  skip {fx.name}: golden exists (use --force to overwrite)"
                ))
                continue
            self.stdout.write(f"  capturing {fx.name} ({fx.job_type}) ...")
            struct, _ = benchmarks.run_fixture(fx)
            golden.freeze(struct, fx.golden_dir)
            n_sheets = sum(len(s) for s in struct.values())
            self.stdout.write(self.style.SUCCESS(
                f"    froze {len(struct)} workbook(s) / {n_sheets} sheet(s) "
                f"-> {fx.golden_dir}"
            ))
