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

import pandas as pd

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
    # `class_aliases` lets one fixture pin the un-aliased behaviour and another the aliased
    # one. WP0's whole point is that the two differ: without the Health alias the reference
    # book discards 3,044 paid rows in silence.
    aliases = fx.spec.get("class_aliases") or None
    with tempfile.TemporaryDirectory() as out_dir:
        with stage_timer("run_generate_summary"):
            run_generate_summary(
                p["start"], p["end"], p["bop"], p["eop"],
                str(fx.input_path("premium")),
                str(fx.input_path("claims_paid")),
                str(fx.input_path("claims_os")),
                out_dir,
                class_aliases=aliases,
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


def _run_m2_sensitivity(fx: Fixture) -> golden.OutputStruct:
    """Sensitivity run, frozen at the MEASURE level rather than the workbook.

    The comparison workbook is presentation; the numbers that must not drift are
    the per-measure base and shocked values. Freezing those directly makes a
    golden diff point at the measure and scenario that moved, instead of at a
    cell address in a rendered sheet.
    """
    from module2_engine.scenarios import (
        ScenarioShock,
        TOTAL,
        run_sensitivity,
    )

    p = fx.spec["params"]
    combined = fx.input_path("combined_summary").read_bytes()
    shocks = [ScenarioShock.from_dict(d) for d in p["scenarios"]]
    with stage_timer("run_module2_sensitivity"):
        result = run_sensitivity(
            combined, shocks,
            selected_ulr_rows=p.get("selected_ulr_rows") or None,
            scope=p.get("scope", "allocate"),
        )

    # measure x (class | TOTAL) for base, then one frame per scenario.
    frames: dict[str, pd.DataFrame] = {
        "Base": pd.DataFrame(result.base).sort_index().sort_index(axis=1)
    }
    for shock, values in result.per_scenario:
        frames[shock.label] = pd.DataFrame(values).sort_index().sort_index(axis=1)
    return {"Sensitivity_Measures": frames}


def _run_m2_pattern(fx: Fixture) -> golden.OutputStruct:
    """Allocate with a payment-pattern override, frozen at the MEASURE level.

    Freezing measures rather than the workbook makes a golden diff point at the measure
    that moved. The invariants are the load-bearing part: a pattern only redistributes
    cash flows in time and sums to 1, so IBNR / ULAE / RA / Future CF / PAA_LRC /
    GMM LRC_Undiscounted must never move. If one of them drifts, the override has been
    wired into the wrong place.
    """
    import numpy as np

    from module2_engine.engine import _compute_allocate_frames
    from module2_engine.pattern_override import PatternOverride

    p = fx.spec["params"]
    combined = fx.input_path("combined_summary").read_bytes()

    base, _ = _compute_allocate_frames(combined, None)
    cols = [c for c in base["MainSheet"].columns if isinstance(c, (int, np.integer))]
    classes = sorted(base["MainSheet"]["RESERVINGCLASS"].astype(str).unique())

    decay = float(p.get("decay", 0.85))
    vec = np.array([decay ** k for k in range(len(cols))])
    vec = vec / vec.sum()
    rows = [
        {"reserving_class": rc, "dev_period": i, "weight": float(w)}
        for rc in classes
        for i, w in enumerate(vec)
    ]
    override = PatternOverride.from_rows(rows, mode=p.get("pattern_mode", "shape_only"))

    with stage_timer("run_module2_pattern_override"):
        frames, _ = _compute_allocate_frames(combined, None, pattern_override=override)

    measures = [
        ("MainSheet", "IBNR"), ("MainSheet", "ULAE"),
        ("MainSheet", "RA (OS)"), ("MainSheet", "RA (IBNR)"),
        ("MainSheet", "Future CF"), ("MainSheet", "Discounting Impact"),
        ("MainSheet", "Change in Discounting Impact"),
        ("LC", "PAA_LRC"), ("LC", "GMM LRC_Undiscounted"),
        ("LC", "GMM LRC_Discounted_CY"), ("LC", "GMM LRC_Discounted_PY"),
        ("LC", "LC Discounted_CY"), ("LC", "Loss Recovery Component"),
    ]
    summary = pd.DataFrame(
        [
            {
                "measure": f"{sheet}.{column}",
                "base": float(pd.to_numeric(base[sheet][column], errors="coerce").sum()),
                "override": float(pd.to_numeric(frames[sheet][column], errors="coerce").sum()),
            }
            for sheet, column in measures
        ]
    )
    return {
        "Pattern_Override": {
            "Measures": summary,
            "Payment Pattern": frames["Payment Pattern"],
        }
    }


def _run_m1_upr_methods(fx: Fixture) -> golden.OutputStruct:
    """Freeze every UPR method's per-class UPR at a fixed valuation date.

    Frozen at the MEASURE level rather than by running a full summary: a diff then names
    the method and reserving class that moved, and the fixture runs in seconds rather than
    minutes. The default (pro-rata) row doubles as a second guard on the bit-identity
    property the whole work package rests on.
    """
    import numpy as np

    from module1_engine.engine import preprocess_data, preprocess_dates
    from module1_engine.upr_methods import METHOD_KEYS, UprPolicy, unearned_fraction

    p = fx.spec["params"]
    premium_dir = (fx.dir / p["premium_dir"]).resolve()
    eop = pd.to_datetime(p["eop"], format="%d-%m-%Y")

    df = preprocess_data(str(premium_dir))
    df["PREMIUMAMOUNT"] = pd.to_numeric(df["PREMIUMAMOUNT"], errors="coerce")
    preprocess_dates(df)
    df["Duration"] = pd.to_numeric(
        (df["RiskEndDate"] - df["RiskStartDate"]).dt.days + 1, errors="coerce"
    )

    frames: dict[str, pd.DataFrame] = {}
    for method in METHOD_KEYS:
        params = {"percent": 0.5} if method == "flat_percentage" else {}
        policy = UprPolicy.from_dicts([{"method": method, "params": params}])
        with stage_timer(f"upr:{method}"):
            upr = unearned_fraction(df, eop, policy) * df["PREMIUMAMOUNT"].to_numpy()
        frames[method] = (
            pd.DataFrame({
                "RESERVINGCLASS": df["RESERVINGCLASS"].astype(str),
                "upr": np.nan_to_num(upr),
            })
            .groupby("RESERVINGCLASS", dropna=False)
            .sum()
            .reset_index()
            .sort_values("RESERVINGCLASS")
            .reset_index(drop=True)
        )
    return {"UPR_Methods": frames}


def _run_m1_large_claims(fx: Fixture) -> golden.OutputStruct:
    """Freeze the three exclusion modes' per-cohort ultimates on the reference book.

    Frozen at the MEASURE level for the same reasons as the UPR fixture — but here it is
    load-bearing for a second reason: the reserve WORKBOOK'S ultimates are computed from a
    Selected CDF that the engine writes as a placeholder Excel formula, so a full-summary
    golden would pin CDF = 2.0 everywhere and could not tell the modes apart at all. This
    fixture computes the factors directly, which is what the workbook does once an actuary
    has selected them.

    The mode column is what makes it useful: `exclude_from_ldf_only` must stay ABOVE the
    unexcluded base, and `exclude_and_add_back` must stay between it and `exclude_entirely`.
    """
    import numpy as np

    from core.grain import QUARTERLY
    from module1_engine.triangles import build_triangle, cdf_from_ldf, volume_weighted_ldf

    p = fx.spec["params"]
    paid_dir = (fx.dir / p["paid_dir"]).resolve()
    os_dir = (fx.dir / p["os_dir"]).resolve()
    start = pd.to_datetime(p["start"], format="%d-%m-%Y")
    end = pd.to_datetime(p["end"], format="%d-%m-%Y")
    eop_q = pd.to_datetime(p["eop"], format="%d-%m-%Y").to_period("Q")
    top_n = int(p.get("top_n", 10))

    def _load(folder):
        frames = [
            pd.read_excel(f) for f in sorted(Path(folder).glob("*.xls*"))
        ]
        d = pd.concat(frames, ignore_index=True)
        d.columns = [str(c).upper().replace(" ", "").replace("_", "") for c in d.columns]
        return d

    paid, os_df = _load(paid_dir), _load(os_dir)
    sel = lambda d: d[
        (d["RITREATYTYPE"].astype(str).str.upper() == "GROSS")
        & (d["HEADOFDAMAGE"] == p["head_of_damage"])
    ].copy()
    gp, go = sel(paid), sel(os_df)
    gp["Amount"] = gp["AMOUNTPAID"]
    gp["As at"] = pd.to_datetime(gp["PAYMENTDATE"])
    gp["LOSSDATE"] = pd.to_datetime(gp["LOSSDATE"])
    go["LOSSDATE"] = pd.to_datetime(go["LOSSDATE"])
    go["ASAT"] = pd.to_datetime(go["ASAT"])

    top = gp.groupby("CLAIMNUMBER")["Amount"].sum().nlargest(top_n).index.astype(str).tolist()

    def _to_date(excluded):
        tri = build_triangle(
            gp, grain=QUARTERLY, start=start, end=end, excluded_claims=excluded
        )
        cdf = cdf_from_ldf(volume_weighted_ldf(tri.cumulative))
        n = len(tri.cumulative)
        td, factors = [], []
        for i in range(len(tri.accident_labels)):
            maturity = min(n - 1 - i, tri.cumulative.shape[1] - 1)
            value = tri.cumulative.iloc[i, maturity]
            td.append(0.0 if pd.isna(value) else float(value))
            factors.append(cdf[maturity] if maturity < len(cdf) else 1.0)
        return np.array(td), np.array(factors), tri.accident_labels

    with stage_timer("large_claims:triangles"):
        paid_all, cdf_all, labels = _to_date(None)
        paid_ex, cdf_ex, _ = _to_date(top)
    large_paid = paid_all - paid_ex

    lo = go[go["CLAIMNUMBER"].astype(str).isin(top)]
    lo = lo[lo["ASAT"].dt.to_period("Q") == eop_q]
    by_q = lo.groupby(lo["LOSSDATE"].dt.to_period("Q"))["AMOUNTOUTSTANDING"].sum()
    large_case = np.array([
        float(by_q.get(pd.Period(label.replace("-", ""), freq="Q"), 0.0)) for label in labels
    ])

    cohorts = pd.DataFrame({
        "Accident_Period": labels,
        "paid_to_date": paid_all,
        "attritional_paid_to_date": paid_ex,
        "large_paid": large_paid,
        "large_case": large_case,
        "cdf_all": cdf_all,
        "cdf_attritional": cdf_ex,
        "ult_base": paid_all * cdf_all,
        "ult_exclude_from_ldf_only": paid_all * cdf_ex,
        "ult_exclude_and_add_back": paid_ex * cdf_ex + large_paid + large_case,
        "ult_exclude_entirely": paid_ex * cdf_ex,
    })
    totals = pd.DataFrame({
        "measure": [
            "ult_base", "ult_exclude_from_ldf_only", "ult_exclude_and_add_back",
            "ult_exclude_entirely",
        ],
        "total": [
            float(cohorts["ult_base"].sum()),
            float(cohorts["ult_exclude_from_ldf_only"].sum()),
            float(cohorts["ult_exclude_and_add_back"].sum()),
            float(cohorts["ult_exclude_entirely"].sum()),
        ],
    })
    totals["vs_base_pct"] = (
        totals["total"] / float(cohorts["ult_base"].sum()) - 1.0
    ) * 100.0
    claims = pd.DataFrame({"rank": range(1, len(top) + 1), "claim_number": top})
    return {
        "Large_Claims": {
            "cohorts": cohorts,
            "totals": totals,
            "excluded_claims": claims,
        }
    }


_DISPATCH = {
    "summary": _run_summary,
    "policy_upr": _run_policy_upr,
    "update_reserve": _run_update_reserve,
    "m2_allocate": _run_m2_allocate,
    "m2_process": _run_m2_process,
    "m2_sensitivity": _run_m2_sensitivity,
    "m2_pattern": _run_m2_pattern,
    "m1_upr_methods": _run_m1_upr_methods,
    "m1_large_claims": _run_m1_large_claims,
}
