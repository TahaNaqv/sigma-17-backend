"""Fixture-driven engine benchmark/golden runner.

Discovers benchmark *fixtures* and runs the corresponding engine entry point
under a profiling session, returning the normalised output plus stage timings.
Shared by the ``bench_engines`` and ``capture_golden`` management commands and
by the golden regression tests, so all three drive the engines identically.

Fixture layout (data lives outside version control — see benchmarks/README.md)::

    benchmarks/
      fixtures/
        <fixture_name>/
          spec.json
          <input files / folders referenced by spec.json>
      goldens/
        <fixture_name>/          # produced by capture_golden

``spec.json`` schema::

    {
      "job_type": "summary" | "policy_upr" | "update_reserve"
                  | "m2_allocate" | "m2_process",
      "params":  { ... engine-specific scalar params ... },
      "inputs":  { "<role>": "<relative path to file or folder>" }
    }
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.profiling import StageTiming, profiling_session, stage_timer
from processing import golden

# Repo-root/benchmarks (this file is <root>/processing/benchmarks.py)
BENCH_ROOT = Path(__file__).resolve().parent.parent / "benchmarks"
FIXTURES_DIR = BENCH_ROOT / "fixtures"
GOLDENS_DIR = BENCH_ROOT / "goldens"


@dataclass
class Fixture:
    name: str
    spec: dict[str, Any]
    dir: Path

    @property
    def job_type(self) -> str:
        return self.spec["job_type"]

    @property
    def golden_dir(self) -> Path:
        return GOLDENS_DIR / self.name

    def input_path(self, role: str) -> Path:
        return self.dir / self.spec["inputs"][role]


def discover_fixtures() -> list[Fixture]:
    """Return all fixtures that have a readable ``spec.json`` (sorted by name)."""
    if not FIXTURES_DIR.is_dir():
        return []
    out: list[Fixture] = []
    for spec_path in sorted(FIXTURES_DIR.glob("*/spec.json")):
        spec = json.loads(spec_path.read_text())
        out.append(Fixture(name=spec_path.parent.name, spec=spec, dir=spec_path.parent))
    return out


def get_fixture(name: str) -> Fixture:
    for fx in discover_fixtures():
        if fx.name == name:
            return fx
    raise KeyError(f"no fixture named {name!r} under {FIXTURES_DIR}")


def run_fixture(fx: Fixture) -> tuple[golden.OutputStruct, list[StageTiming]]:
    """Run a fixture's engine entry point, returning (output_struct, timings)."""
    with profiling_session() as prof:
        with stage_timer(f"fixture:{fx.name}"):
            struct = _DISPATCH[fx.job_type](fx)
    return struct, prof.records


# --------------------------------------------------------------------------- #
# Per-job-type dispatch. Each returns a normalised OutputStruct.
# --------------------------------------------------------------------------- #
def _run_summary(fx: Fixture) -> golden.OutputStruct:
    from module1_engine.engine import run_generate_summary

    p = fx.spec["params"]
    with tempfile.TemporaryDirectory() as out_dir:
        with stage_timer("run_generate_summary"):
            run_generate_summary(
                p["start"], p["end"], p["bop"], p["eop"],
                str(fx.input_path("premium")),
                str(fx.input_path("claims_paid")),
                str(fx.input_path("claims_os")),
                out_dir,
            )
        return golden.normalize_output_dir(out_dir)


def _run_policy_upr(fx: Fixture) -> golden.OutputStruct:
    from module1_engine.engine import run_policy_level_upr

    p = fx.spec["params"]
    with tempfile.TemporaryDirectory() as out_dir:
        with stage_timer("run_policy_level_upr"):
            run_policy_level_upr(
                p["bop"], p["eop"], str(fx.input_path("premium")), out_dir
            )
        return golden.normalize_output_dir(out_dir)


def _run_update_reserve(fx: Fixture) -> golden.OutputStruct:
    from module1_engine.engine import run_update_reserve_summary

    # Runs in place over a folder of workbooks; copy first so the fixture is pristine.
    with tempfile.TemporaryDirectory() as work_dir:
        staged = Path(work_dir) / "work"
        shutil.copytree(fx.input_path("folder"), staged)
        with stage_timer("run_update_reserve_summary"):
            run_update_reserve_summary(str(staged))
        return golden.normalize_output_dir(staged)


def _run_m2_allocate(fx: Fixture) -> golden.OutputStruct:
    from module2_engine.engine import run_module2_allocate

    combined = fx.input_path("combined_summary").read_bytes()
    with stage_timer("run_module2_allocate"):
        result = run_module2_allocate(combined)
    return golden.normalize_bytes("Module2_Allocate_Output.xlsx", result["workbook_bytes"])


def _run_m2_process(fx: Fixture) -> golden.OutputStruct:
    from module2_engine.engine import run_module2_process

    p = fx.spec["params"]
    combined = fx.input_path("combined_summary").read_bytes()
    previous = fx.input_path("previous_period").read_bytes()
    expense = fx.input_path("expense_cf").read_bytes()
    with stage_timer("run_module2_process"):
        out_bytes = run_module2_process(
            combined, previous, expense,
            int(p["accounting_period"]),
            p.get("selected_ulr_rows", []),
        )
    return golden.normalize_bytes("Module2_Final_Output.xlsx", out_bytes)


_DISPATCH = {
    "summary": _run_summary,
    "policy_upr": _run_policy_upr,
    "update_reserve": _run_update_reserve,
    "m2_allocate": _run_m2_allocate,
    "m2_process": _run_m2_process,
}
