"""Sensitivity / stress-testing shocks for the Module 2 reserving model.

Three levers, each with a **different and deliberately non-interchangeable unit**
(docs/SENSITIVITY_TESTING_PLAN.md D1, verified against the client reference book):

======== ================= ============================================
lever    magnitude means   example
======== ================= ============================================
ra       relative fraction 0.10  -> RA 4.63% becomes 5.093%
discount absolute bp       5     -> CY spot 6.08% becomes 6.13%
ulr      absolute fraction 0.05  -> Selected ULR 0.65 becomes 0.70
======== ================= ============================================

Every underlying value in ``Combined_Summary.xlsx`` is stored as a **fraction**
(``RA % = 0.0463``, ``CY Discount = 0.0608``, ``Selected ULR ~ 0.53``), which is why
5 basis points is ``+0.0005`` and 5 percentage points is ``+0.05``.

The discount shock moves the **CY curve only**. ``Change in Discounting Impact``
is ``CY - PY``; PY is the prior period's locked-in basis and shocking it would
fabricate a comparative that never existed. Verified: a discount shock leaves
``GMM LRC_Discounted_PY`` unchanged.

Shocks are applied at exactly three points in ``_compute_allocate_frames``, each of
which provably dominates every consumer of that parameter — see the plan section 1.2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np
import pandas as pd

# Levers --------------------------------------------------------------------

LEVER_RA = "ra"
LEVER_DISCOUNT = "discount"
LEVER_ULR = "ulr"
LEVERS = (LEVER_RA, LEVER_DISCOUNT, LEVER_ULR)

#: Human-facing unit per lever. Rendered by the UI next to the magnitude input;
#: mixing these up is the most likely user error this feature can suffer.
LEVER_UNITS: dict[str, str] = {
    LEVER_RA: "relative",       # 0.10 == +10% of the existing loading
    LEVER_DISCOUNT: "bp",       # 5    == +5 basis points on the annual spot
    LEVER_ULR: "pp",            # 0.05 == +5 percentage points
}


def canonical_class(value: Any) -> str:
    """Match key for class scoping: casefold + collapse whitespace.

    Mirrors WP0's ``canonical_key`` so a scope entry matches regardless of the
    spelling variations that exist across client input files.
    """
    if value is None:
        return ""
    return " ".join(str(value).split()).strip().casefold()


@dataclass(frozen=True)
class ScenarioShock:
    """One parameter shock. Immutable; safe to reuse across runs."""

    label: str
    lever: str
    magnitude: float
    scope_classes: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if self.lever not in LEVERS:
            raise ValueError(f"Unknown lever {self.lever!r}; expected one of {LEVERS}.")
        if self.magnitude is None or not np.isfinite(float(self.magnitude)):
            raise ValueError(f"Scenario {self.label!r}: magnitude must be a finite number.")

    # -- scoping ------------------------------------------------------------

    def _in_scope(self, classes: pd.Series) -> pd.Series:
        if not self.scope_classes:
            return pd.Series(True, index=classes.index)
        wanted = {canonical_class(c) for c in self.scope_classes}
        return classes.map(lambda v: canonical_class(v) in wanted)

    # -- appliers -----------------------------------------------------------

    def apply_ra(self, ulae_ra: pd.DataFrame) -> pd.DataFrame:
        """Scale ``RA %`` by ``(1 + magnitude)`` — a RELATIVE shock.

        Applied to the parsed ULAE-RA frame, which is read twice downstream
        (into ``merged_df`` for RA (OS)/RA (IBNR), and into ``uw_summary`` for
        Combined Ratio). One application therefore reaches both — the RA shock
        legitimately moves the explicit RA balances *and* loads the combined
        ratio. That double effect is by design, not a bug.
        """
        if self.lever != LEVER_RA or not self.magnitude:
            return ulae_ra
        out = ulae_ra.copy()
        mask = self._in_scope(out["RESERVINGCLASS"])
        out.loc[mask, "RA %"] = out.loc[mask, "RA %"] * (1.0 + float(self.magnitude))
        return out

    def apply_discount(self, discount: pd.DataFrame) -> pd.DataFrame:
        """Add ``magnitude`` basis points to the **CY** annual spot curve.

        Parallel shift; PY is deliberately untouched. Class scoping does not
        apply — the discount curve is not class-specific.
        """
        if self.lever != LEVER_DISCOUNT or not self.magnitude:
            return discount
        out = discount.copy()
        out["CY Discount"] = out["CY Discount"] + (float(self.magnitude) / 10_000.0)
        return out

    def ulr_delta(self) -> float | None:
        """Absolute addition to Selected ULR, or None when this is not a ULR shock."""
        if self.lever != LEVER_ULR or not self.magnitude:
            return None
        return float(self.magnitude)

    def ulr_scope(self) -> tuple[str, ...]:
        return self.scope_classes

    # -- reporting ----------------------------------------------------------

    def describe(self) -> str:
        unit = LEVER_UNITS[self.lever]
        if self.lever == LEVER_DISCOUNT:
            return f"{self.magnitude:+g} bp on the CY spot curve"
        if self.lever == LEVER_ULR:
            return f"{self.magnitude * 100:+g} pp on Selected ULR"
        return f"{self.magnitude * 100:+g}% relative on RA %"

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "lever": self.lever,
            "magnitude": float(self.magnitude),
            "unit": LEVER_UNITS[self.lever],
            "scope_classes": list(self.scope_classes),
            "description": self.describe(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ScenarioShock":
        return cls(
            label=str(raw.get("label") or raw.get("lever") or "scenario"),
            lever=str(raw["lever"]),
            magnitude=float(raw["magnitude"]),
            scope_classes=tuple(raw.get("scope_classes") or ()),
        )


# ---------------------------------------------------------------------------
# Measures
# ---------------------------------------------------------------------------

MONEY = "money"
RATIO = "ratio"

#: Total row key. Reserved — a reserving class may never be named this.
TOTAL = "__TOTAL__"


@dataclass(frozen=True)
class Measure:
    """One comparable quantity in the sensitivity matrix.

    ``aggregate`` matters: summing loss ratios across classes is meaningless, so
    ratios aggregate as a GEP-weighted mean (GEP being the exposure base the
    ratio is struck on). Money measures sum.
    """

    key: str
    label: str
    sheet: str
    column: str
    kind: str = MONEY
    aggregate: str = "sum"          # "sum" | "weighted_mean"
    weight_column: str | None = None
    scope: str = "allocate"         # "allocate" | "process"


#: Measures available from an allocate-scope run. Order is display order.
MEASURES: tuple[Measure, ...] = (
    Measure("ibnr", "IBNR", "MainSheet", "IBNR"),
    Measure("ulae", "ULAE", "MainSheet", "ULAE"),
    Measure("ra_os", "RA (OS)", "MainSheet", "RA (OS)"),
    Measure("ra_ibnr", "RA (IBNR)", "MainSheet", "RA (IBNR)"),
    Measure("future_cf", "Future CF", "MainSheet", "Future CF"),
    Measure("disc_impact", "Discounting Impact", "MainSheet", "Discounting Impact"),
    Measure("chg_disc_impact", "Change in Discounting Impact", "MainSheet",
            "Change in Discounting Impact"),
    Measure("paa_lrc", "PAA LRC", "LC", "PAA_LRC"),
    Measure("gmm_lrc_undisc", "GMM LRC (Undiscounted)", "LC", "GMM LRC_Undiscounted"),
    Measure("gmm_lrc_cy", "GMM LRC (Discounted CY)", "LC", "GMM LRC_Discounted_CY"),
    Measure("gmm_lrc_py", "GMM LRC (Discounted PY)", "LC", "GMM LRC_Discounted_PY"),
    Measure("lc_undisc", "Loss Component (Undiscounted)", "LC", "LC Undiscounted"),
    Measure("lc_cy", "Loss Component (Discounted CY)", "LC", "LC Discounted_CY"),
    Measure("loss_recovery", "Loss Recovery Component", "LC", "Loss Recovery Component"),
    Measure("ult_lr", "Ultimate LR", "Loss Ratio", "Ult LR",
            kind=RATIO, aggregate="weighted_mean", weight_column="GEP"),
    Measure("selected_ulr", "Selected ULR", "Loss Ratio", "Selected ULR",
            kind=RATIO, aggregate="weighted_mean", weight_column="GEP"),
    Measure("combined_ratio", "Combined Ratio", "Loss Ratio", "Combined Ratio",
            kind=RATIO, aggregate="weighted_mean", weight_column="GEP"),
)

#: Additional measures that require a process-scope run (LIC / LRC reconciliation).
#: BOP measures are prior-period given data and MUST NOT move under any shock; they
#: are included precisely so the disclosure evidences that invariance.
PROCESS_MEASURES: tuple[Measure, ...] = (
    Measure("lrc_bop", "Gross LRC (BOP)", "__LRC_PREV__", "Gross LRC", scope="process"),
    Measure("lrc_eop", "Gross LRC (EOP)", "__LRC_CURR__", "Gross LRC", scope="process"),
    Measure("lic_bop", "Gross LIC (BOP)", "__LIC_PREV__", "GROSS LIC", scope="process"),
    Measure("lic_eop", "Gross LIC (EOP)", "__LIC_CURR__", "GROSS LIC", scope="process"),
)

ALL_MEASURES: tuple[Measure, ...] = MEASURES + PROCESS_MEASURES
MEASURES_BY_KEY: dict[str, Measure] = {m.key: m for m in ALL_MEASURES}


def _aggregate(frame: pd.DataFrame, measure: Measure) -> dict[str, float]:
    """Aggregate one measure to ``{reserving_class: value}`` plus a TOTAL entry."""
    if measure.column not in frame.columns or "RESERVINGCLASS" not in frame.columns:
        return {}
    values = pd.to_numeric(frame[measure.column], errors="coerce").fillna(0.0)
    classes = frame["RESERVINGCLASS"].astype(str)

    if measure.aggregate == "weighted_mean":
        weights = pd.to_numeric(
            frame[measure.weight_column], errors="coerce"
        ).fillna(0.0) if measure.weight_column in frame.columns else pd.Series(
            1.0, index=frame.index
        )
        num = (values * weights).groupby(classes).sum()
        den = weights.groupby(classes).sum()
        per_class = (num / den.replace(0, np.nan)).fillna(0.0)
        total_den = float(weights.sum())
        total = float((values * weights).sum() / total_den) if total_den else 0.0
    else:
        per_class = values.groupby(classes).sum()
        total = float(values.sum())

    out = {str(k): float(v) for k, v in per_class.items()}
    out[TOTAL] = total
    return out


def extract_measures(
    allocate_sheets: dict[str, pd.DataFrame],
    process_tables: dict[str, pd.DataFrame] | None = None,
) -> dict[str, dict[str, float]]:
    """``{measure_key: {reserving_class|TOTAL: value}}`` for one run."""
    frames = dict(allocate_sheets)
    if process_tables:
        frames.update(process_tables)
    result: dict[str, dict[str, float]] = {}
    for measure in ALL_MEASURES:
        frame = frames.get(measure.sheet)
        if frame is None:
            continue
        agg = _aggregate(frame, measure)
        if agg:
            result[measure.key] = agg
    return result


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

#: Values whose magnitude is below this are treated as zero when forming a
#: percentage, so a structural zero never becomes a meaningless huge ratio.
_PCT_EPS = 1e-9


def build_comparison(
    base: dict[str, dict[str, float]],
    scenarios: list[tuple[ScenarioShock, dict[str, dict[str, float]]]],
    *,
    reserving_class: str = TOTAL,
) -> pd.DataFrame:
    """Long-form comparison: one row per (measure, scenario).

    ``pct_delta`` is ``None`` — not 0, not inf — when the base is zero, so the UI
    can render an honest blank rather than fabricate a percentage. Percent and
    absolute are BOTH carried: a large relative move on a small base (Loss
    Component is the standard example) misleads badly on its own.
    """
    rows: list[dict[str, Any]] = []
    for measure in ALL_MEASURES:
        if measure.key not in base:
            continue
        b = base[measure.key].get(reserving_class, 0.0)
        for shock, values in scenarios:
            v = values.get(measure.key, {}).get(reserving_class, 0.0)
            delta = v - b
            pct = (delta / abs(b)) if abs(b) > _PCT_EPS else None
            rows.append({
                "measure_key": measure.key,
                "measure": measure.label,
                "kind": measure.kind,
                "reserving_class": reserving_class,
                "scenario": shock.label,
                "lever": shock.lever,
                "base": b,
                "value": v,
                "abs_delta": delta,
                "pct_delta": pct,
                "responds": abs(delta) > _PCT_EPS,
            })
    return pd.DataFrame(rows)


def build_tornado(comparison: pd.DataFrame) -> pd.DataFrame:
    """Measures ranked by peak ABSOLUTE sensitivity across all scenarios.

    Absolute, deliberately. Ranking by percent puts Loss Component — a threshold
    residual that can move 145% on a base three orders of magnitude below GMM LRC
    — at the top of the book's risk ranking, which is false.
    """
    if comparison.empty:
        return comparison
    agg = (
        comparison.groupby(["measure_key", "measure"], sort=False)
        .agg(max_abs_delta=("abs_delta", lambda s: float(np.abs(s).max())),
             min_delta=("abs_delta", "min"),
             max_delta=("abs_delta", "max"))
        .reset_index()
        .sort_values("max_abs_delta", ascending=False)
        .reset_index(drop=True)
    )
    return agg


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

SCOPE_ALLOCATE = "allocate"
SCOPE_PROCESS = "process"


@dataclass(frozen=True)
class SensitivityResult:
    """Everything a sensitivity run produced. Serializable for the API and the workbook."""

    scope: str
    shocks: tuple[ScenarioShock, ...]
    base: dict[str, dict[str, float]]
    per_scenario: tuple[tuple[ScenarioShock, dict[str, dict[str, float]]], ...]
    reserving_classes: tuple[str, ...]
    resolved: tuple[dict[str, Any], ...]        # base -> shocked parameter echo
    warnings: tuple[str, ...] = ()

    def comparison(self, reserving_class: str = TOTAL) -> pd.DataFrame:
        return build_comparison(self.base, list(self.per_scenario),
                                reserving_class=reserving_class)

    def tornado(self, reserving_class: str = TOTAL) -> pd.DataFrame:
        return build_tornado(self.comparison(reserving_class))


def _process_tables(frames: Any) -> dict[str, pd.DataFrame]:
    """Build the LIC/LRC BOP+EOP tables from ProcessFrames without writing a workbook."""
    from module2_engine.engine import (
        LRC_COMPONENTS,
        create_lic_table,
        create_lrc_table,
    )
    ifrs = frames.ifrs_summary_df
    return {
        "__LRC_PREV__": create_lrc_table(ifrs, LRC_COMPONENTS["prev"]),
        "__LRC_CURR__": create_lrc_table(ifrs, LRC_COMPONENTS["curr"]),
        "__LIC_PREV__": create_lic_table(ifrs, "prev"),
        "__LIC_CURR__": create_lic_table(ifrs, "curr"),
    }


def _resolved_echo(
    shock: ScenarioShock, allocate_sheets: dict[str, pd.DataFrame]
) -> dict[str, Any]:
    """`base -> shocked` in absolute terms, for the Scenario Definitions sheet.

    A sensitivity disclosure must never require the reader to infer the
    convention, so we state the realised parameter values, not just the shock.
    """
    echo: dict[str, Any] = shock.to_dict()
    lr = allocate_sheets.get("Loss Ratio")
    if shock.lever == LEVER_RA and lr is not None and "RA %" in lr.columns:
        vals = pd.to_numeric(lr["RA %"], errors="coerce").dropna()
        if len(vals):
            echo["base_min"], echo["base_max"] = float(vals.min()), float(vals.max())
            echo["shocked_min"] = echo["base_min"] * (1 + shock.magnitude)
            echo["shocked_max"] = echo["base_max"] * (1 + shock.magnitude)
    elif shock.lever == LEVER_ULR and lr is not None and "Selected ULR" in lr.columns:
        vals = pd.to_numeric(lr["Selected ULR"], errors="coerce").dropna()
        if len(vals):
            echo["base_min"], echo["base_max"] = float(vals.min()), float(vals.max())
            echo["shocked_min"] = max(0.0, echo["base_min"] + shock.magnitude)
            echo["shocked_max"] = max(0.0, echo["base_max"] + shock.magnitude)
    elif shock.lever == LEVER_DISCOUNT:
        cy = allocate_sheets.get("CY-PY Discount")
        echo["shift"] = shock.magnitude / 10_000.0
        if cy is not None and "CY Discount Factor" in cy.columns:
            echo["note"] = "parallel shift on the CY annual spot curve; PY untouched"
    return echo


def run_sensitivity(
    combined_summary_bytes: bytes,
    shocks: Iterable[ScenarioShock],
    *,
    selected_ulr_rows: list[dict[str, Any]] | None = None,
    scope: str = SCOPE_ALLOCATE,
    previous_period_bytes: bytes | None = None,
    expense_cf_bytes: bytes | None = None,
    accounting_period: int | None = None,
    progress: Any = None,
) -> SensitivityResult:
    """Run base + one pass per shock, collecting measures only.

    No per-scenario workbook is written: on the reference book the allocate
    computation is ~0.33s against a ~2.97s xlsx write, so measure-only evaluation
    is roughly 10x cheaper per scenario. A 13-scenario run is a few seconds, which
    is why this executes sequentially rather than fanning out across workers.

    ``progress(i, n, label)`` is called before each pass when supplied.
    """
    from module2_engine.engine import _compute_allocate_frames, _process_intermediates

    shocks = tuple(shocks)
    if scope not in (SCOPE_ALLOCATE, SCOPE_PROCESS):
        raise ValueError(f"Unknown scope {scope!r}.")
    if scope == SCOPE_PROCESS and not (
        previous_period_bytes and expense_cf_bytes and accounting_period is not None
    ):
        raise ValueError(
            "Process-scope sensitivity requires previous_period_bytes, "
            "expense_cf_bytes and accounting_period."
        )

    warnings: list[str] = []
    total_passes = len(shocks) + 1

    def evaluate(shock: ScenarioShock | None):
        if scope == SCOPE_ALLOCATE:
            sheets, _ = _compute_allocate_frames(
                combined_summary_bytes, selected_ulr_rows, shock=shock
            )
            return sheets, None
        frames = _process_intermediates(
            combined_summary_bytes,
            previous_period_bytes,
            expense_cf_bytes,
            int(accounting_period),
            selected_ulr_rows or [],
            shock=shock,
        )
        return frames.allocate_sheets, _process_tables(frames)

    if progress:
        progress(0, total_passes, "Base")
    base_sheets, base_process = evaluate(None)
    base = extract_measures(base_sheets, base_process)

    known = {
        canonical_class(c)
        for c in base_sheets.get("Loss Ratio", pd.DataFrame({"RESERVINGCLASS": []}))[
            "RESERVINGCLASS"
        ].astype(str).unique()
    }

    per_scenario: list[tuple[ScenarioShock, dict[str, dict[str, float]]]] = []
    resolved: list[dict[str, Any]] = []
    for i, shock in enumerate(shocks, start=1):
        for c in shock.scope_classes:
            if canonical_class(c) not in known:
                warnings.append(
                    f"Scenario {shock.label!r}: scoped class {c!r} is not present "
                    f"in the data; it contributes nothing."
                )
        if progress:
            progress(i, total_passes, shock.label)
        sheets, proc = evaluate(shock)
        per_scenario.append((shock, extract_measures(sheets, proc)))
        resolved.append(_resolved_echo(shock, base_sheets))

    classes = sorted(
        str(c) for c in base_sheets.get(
            "Loss Ratio", pd.DataFrame({"RESERVINGCLASS": []})
        )["RESERVINGCLASS"].astype(str).unique()
    )

    # A shock that moved nothing anywhere is far more likely a misconfiguration
    # than a genuine finding, so it is surfaced rather than left for the reader.
    for shock, values in per_scenario:
        moved = any(
            abs(values.get(m, {}).get(TOTAL, 0.0) - base.get(m, {}).get(TOTAL, 0.0)) > _PCT_EPS
            for m in base
        )
        if not moved:
            warnings.append(
                f"Scenario {shock.label!r} moved no measure. Check the magnitude, "
                f"the scope, and whether the underlying parameter is set at all."
            )

    return SensitivityResult(
        scope=scope,
        shocks=shocks,
        base=base,
        per_scenario=tuple(per_scenario),
        reserving_classes=tuple(classes),
        resolved=tuple(resolved),
        warnings=tuple(warnings),
    )
