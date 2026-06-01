"""Benchmark the actuarial engines against fixtures, with optional golden check.

Phase 0 scoreboard for docs/PERFORMANCE_OPTIMIZATION_PLAN.md — the before/after
table every optimisation PR is measured against.

    python manage.py bench_engines --list
    python manage.py bench_engines                 # run all fixtures
    python manage.py bench_engines --only summary_large
    python manage.py bench_engines --check         # also diff vs frozen goldens
    python manage.py bench_engines --repeat 3      # best-of-N wall clock
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from core.profiling import format_report
from processing import benchmarks, golden


class Command(BaseCommand):
    help = "Run engine benchmarks over benchmark fixtures and report stage timings."

    def add_arguments(self, parser):
        parser.add_argument("--list", action="store_true", help="List discovered fixtures and exit.")
        parser.add_argument("--only", default=None, help="Run only the named fixture.")
        parser.add_argument("--check", action="store_true", help="Diff output against frozen goldens.")
        parser.add_argument("--repeat", type=int, default=1, help="Run each fixture N times; report best wall-clock.")

    def handle(self, *args, **opts):
        fixtures = benchmarks.discover_fixtures()
        if opts["only"]:
            fixtures = [f for f in fixtures if f.name == opts["only"]]

        if not fixtures:
            self.stdout.write(self.style.WARNING(
                f"No fixtures found under {benchmarks.FIXTURES_DIR}.\n"
                "Add a fixture (spec.json + inputs) — see benchmarks/README.md — "
                "then re-run."
            ))
            return

        if opts["list"]:
            for fx in fixtures:
                has_golden = (fx.golden_dir / "manifest.json").exists()
                self.stdout.write(
                    f"  {fx.name:<28} type={fx.job_type:<14} "
                    f"golden={'yes' if has_golden else 'NO'}"
                )
            return

        repeat = max(1, opts["repeat"])
        for fx in fixtures:
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n=== {fx.name} ({fx.job_type}) ==="))
            best_struct = None
            best_records = None
            best_total = float("inf")
            for _ in range(repeat):
                struct, records = benchmarks.run_fixture(fx)
                total = sum(r.seconds for r in records if r.depth == 0)
                if total < best_total:
                    best_total, best_struct, best_records = total, struct, records

            self.stdout.write(format_report(best_records or []))

            if opts["check"]:
                if not (fx.golden_dir / "manifest.json").exists():
                    self.stdout.write(self.style.WARNING(
                        f"  (no golden for {fx.name}; run capture_golden first)"
                    ))
                    continue
                diffs = golden.diff_struct(best_struct, golden.thaw(fx.golden_dir))
                if diffs:
                    self.stdout.write(self.style.ERROR(f"  GOLDEN MISMATCH ({len(diffs)}):"))
                    for d in diffs[:25]:
                        self.stdout.write(self.style.ERROR(f"    - {d}"))
                else:
                    self.stdout.write(self.style.SUCCESS("  golden: identical ✓"))
