"""User-supplied payment pattern override (requirement 2).

The actuary supplies ONE vector per reserving class, indexed by development period
**from inception** (period 0 = the period the claim is incurred), summing to 1.
That is the object an actuary means by "a payment pattern", and it is the object the
LRC run-off convolution needs:

    cash flow(p, q) = GEP[q] x CombinedRatio x pattern[p],  discounted at cy_disc[p+q]

Two consumers, two applications (docs/PAYMENT_PATTERN_OVERRIDE_PLAN.md section 2.2):

* **LRC run-off** (``avg_df``) — used DIRECTLY, position for position.
* **LIC cash flows** (``additional_matrix``) — RE-BASED per row: a cohort at ``Age = a``
  has already developed through period ``a``, so its future is ``pattern[a+1:]``
  renormalised.

A proven identity anchors the whole design (plan section 1.3). The engine's existing matrix
already IS the re-based from-inception pattern::

    engine[a][c] = Incremental[a+c+1] / (1 - Cum%[a])
    rebase[a][c] = Incremental[a+c+1] / SUM_{k>a} Incremental[k]

equal because the increments telescope to exactly 1.0 per (class, treaty) group.

That makes the derived pattern a **path-specific** no-op, and the asymmetry is the whole
point of the feature:

* **LIC path — exact no-op.** Supplying the derived pattern leaves ``additional_matrix``
  identical to 2.22e-16 and ``Discounting Impact`` unchanged. This is the strongest
  regression check available and is enforced in the tests.
* **LRC path — deliberately moves.** ``GMM LRC_Discounted_CY`` shifts -0.499%, because
  ``avg_df`` was holding a conditional *average across cohorts of every maturity*, not a
  from-inception pattern. Correcting that is precisely what the client asked for.

(The knock-on to ``LC Discounted_CY`` is -41.96%: the loss component is
``max(GMM LRC - PAA_LRC, 0)``, a threshold residual, so a sub-1% move in LRC is amplified
enormously. Expected, not a bug.)

Structural invariants that hold under ANY pattern, because a pattern only redistributes
cash flows in time and sums to 1: ``IBNR``, ``ULAE``, ``RA (OS)``, ``RA (IBNR)``,
``Future CF``, ``PAA_LRC`` and ``GMM LRC_Undiscounted`` never move. Anything else moving
means the override entered at the wrong place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

#: Renormalise a supplied vector to sum 1 (a pattern is a SHAPE).
MODE_SHAPE_ONLY = "shape_only"
#: Reject the run unless every vector already sums to 1 within TOLERANCE.
MODE_STRICT = "strict"
MODES = (MODE_SHAPE_ONLY, MODE_STRICT)

TOLERANCE = 1e-6


class PatternValidationError(ValueError):
    """A supplied pattern cannot be used. Carries per-class detail for the API."""

    def __init__(self, message: str, errors: dict[str, str] | None = None):
        super().__init__(message)
        self.errors = errors or {}


def canonical_class(value: Any) -> str:
    """Match key for class lookups: casefold + collapse whitespace.

    Mirrors WP0's ``canonical_key`` so a supplied class matches regardless of the
    spelling variations that exist across client input files.
    """
    if value is None:
        return ""
    return " ".join(str(value).split()).strip().casefold()


@dataclass
class OverrideReport:
    """What the override actually did. Persisted to ``input_meta`` and surfaced in the UI —
    an override that silently matched nothing is a failure mode worth naming."""

    applied_classes: list[str] = field(default_factory=list)
    unmatched_classes: list[str] = field(default_factory=list)
    rescaled: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    horizon: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied_classes": list(self.applied_classes),
            "unmatched_classes": list(self.unmatched_classes),
            "rescaled": dict(self.rescaled),
            "warnings": list(self.warnings),
            "horizon": self.horizon,
        }


def rebase(pattern: np.ndarray, age: int, width: int) -> np.ndarray:
    """Future-conditional pattern for a cohort that has developed through ``age``.

    Returns a ``width``-long vector summing to 1. A cohort past the end of the pattern
    is fully developed, so whatever remains is treated as paying immediately —
    consistent with the engine's own handling of fully-developed rows.
    """
    out = np.zeros(width, dtype=float)
    tail = np.asarray(pattern, dtype=float)[age + 1:]
    total = float(tail.sum())
    if tail.size and abs(total) > 0.0:
        take = min(tail.size, width)
        out[:take] = tail[:take] / total
    else:
        out[0] = 1.0
    return out


@dataclass
class PatternOverride:
    """A validated, class-keyed set of from-inception payment patterns."""

    #: canonical class key -> pattern vector (sums to 1)
    patterns: dict[str, np.ndarray]
    #: canonical key -> the label the user supplied, for reporting
    labels: dict[str, str]
    mode: str = MODE_SHAPE_ONLY
    report: OverrideReport = field(default_factory=OverrideReport)

    # -- construction -------------------------------------------------------

    @classmethod
    def from_rows(
        cls,
        rows: Iterable[Mapping[str, Any]],
        *,
        mode: str = MODE_SHAPE_ONLY,
    ) -> "PatternOverride":
        """Build from long-form rows: ``{reserving_class, dev_period, weight}``.

        Long form is what the database stores; the Excel template is wide and the
        importer unpivots, so both paths converge here.
        """
        if mode not in MODES:
            raise PatternValidationError(f"Unknown pattern mode {mode!r}; expected one of {MODES}.")

        by_class: dict[str, dict[int, float]] = {}
        labels: dict[str, str] = {}
        for row in rows:
            raw = row.get("reserving_class")
            key = canonical_class(raw)
            if not key:
                continue
            period = row.get("dev_period")
            weight = row.get("weight")
            if period is None or weight is None:
                continue
            try:
                period_i = int(period)
                weight_f = float(weight)
            except (TypeError, ValueError) as exc:
                raise PatternValidationError(
                    f"{raw!r}: development period and weight must be numeric."
                ) from exc
            if period_i < 0:
                raise PatternValidationError(f"{raw!r}: development period cannot be negative.")
            bucket = by_class.setdefault(key, {})
            if period_i in bucket:
                raise PatternValidationError(
                    f"{raw!r}: duplicate entry for development period {period_i}."
                )
            bucket[period_i] = weight_f
            labels.setdefault(key, str(raw).strip())

        report = OverrideReport()
        patterns: dict[str, np.ndarray] = {}
        errors: dict[str, str] = {}
        for key, bucket in by_class.items():
            width = max(bucket) + 1
            vec = np.zeros(width, dtype=float)
            for p, w in bucket.items():
                vec[p] = w
            total = float(vec.sum())
            label = labels[key]

            if abs(total) <= TOLERANCE:
                errors[label] = "The pattern sums to zero; it carries no information."
                continue
            if mode == MODE_STRICT and abs(total - 1.0) > TOLERANCE:
                errors[label] = (
                    f"The pattern sums to {total:.6f}, not 1. Switch to shape-only mode "
                    f"to have it renormalised, or correct the weights."
                )
                continue
            if abs(total - 1.0) > TOLERANCE:
                report.rescaled[label] = total
            if (vec < 0).any():
                report.warnings.append(
                    f"{label}: contains negative weights. Recoveries can legitimately "
                    f"produce these, but a negative early period usually signals a data error."
                )
            patterns[key] = vec / total

        if errors:
            raise PatternValidationError(
                "One or more payment patterns are unusable.", errors=errors
            )
        if not patterns:
            raise PatternValidationError("No usable payment patterns were supplied.")

        return cls(patterns=patterns, labels=labels, mode=mode, report=report)

    # -- application --------------------------------------------------------

    def has(self, reserving_class: Any) -> bool:
        return canonical_class(reserving_class) in self.patterns

    def vector(self, reserving_class: Any, width: int) -> np.ndarray | None:
        """From-inception pattern padded/truncated to ``width`` and renormalised."""
        vec = self.patterns.get(canonical_class(reserving_class))
        if vec is None:
            return None
        out = np.zeros(width, dtype=float)
        take = min(vec.size, width)
        out[:take] = vec[:take]
        total = float(out.sum())
        return out / total if abs(total) > 0.0 else out

    def note_horizon(self, width: int, data_classes: Iterable[Any]) -> None:
        """Record coverage against the run's actual classes and development horizon."""
        self.report.horizon = width
        present = {canonical_class(c) for c in data_classes}
        for key, vec in self.patterns.items():
            label = self.labels.get(key, key)
            if key not in present:
                self.report.unmatched_classes.append(label)
                continue
            self.report.applied_classes.append(label)
            if vec.size > width:
                self.report.warnings.append(
                    f"{label}: supplied {vec.size} development periods but this run has "
                    f"{width}; the tail was truncated and the pattern renormalised."
                )
            elif vec.size < width:
                self.report.warnings.append(
                    f"{label}: supplied {vec.size} development periods but this run has "
                    f"{width}; the remaining periods were treated as zero."
                )
        for label in sorted(self.report.unmatched_classes):
            self.report.warnings.append(
                f"{label}: not present in this run's data; the pattern contributed nothing."
            )


def apply_to_additional_matrix(
    matrix: pd.DataFrame,
    merged_df: pd.DataFrame,
    override: PatternOverride,
) -> pd.DataFrame:
    """Replace matrix rows for overridden classes with the re-based supplied pattern.

    Rows of classes with no override are left exactly as the engine computed them, so a
    partial override is genuinely partial rather than all-or-nothing. Fully-developed rows
    (``Expected Unpaid % == 0``) keep the engine's ``[1, 0, 0, ...]`` — an override must not
    resurrect development on a row that has none left.
    """
    width = matrix.shape[1]
    classes = merged_df["RESERVINGCLASS"].to_numpy()
    ages = merged_df["Age"].to_numpy()
    expected = merged_df["Expected Unpaid %"].to_numpy()

    # Cache one re-based vector per (class, age); the reference book has 12 classes x 26
    # ages against 2,476 rows, so this turns thousands of rebuilds into ~300.
    cache: dict[tuple[str, int], np.ndarray] = {}
    values = matrix.to_numpy(dtype=float, copy=True)
    positions = {idx: i for i, idx in enumerate(matrix.index)}

    for i, idx in enumerate(merged_df.index):
        key = canonical_class(classes[i])
        if key not in override.patterns:
            continue
        if expected[i] == 0:
            continue
        age = int(ages[i])
        ck = (key, age)
        vec = cache.get(ck)
        if vec is None:
            base = override.vector(classes[i], width)
            vec = rebase(base, age, width)
            cache[ck] = vec
        values[positions[idx], :] = vec

    return pd.DataFrame(values, index=matrix.index, columns=matrix.columns)


def apply_to_avg_df(avg_df: pd.DataFrame, override: PatternOverride) -> pd.DataFrame:
    """Replace the run-off multiplier rows with the supplied from-inception patterns.

    Assigned DIRECTLY, not re-derived from the matrix: the run-off convolution consumes a
    from-inception pattern, whereas the matrix holds per-row *conditional* patterns. Deriving
    one from the other would silently reintroduce the conditional-average artefact this
    feature exists to let the actuary escape.
    """
    out = avg_df.copy()
    period_cols = [c for c in out.columns if c != "RESERVINGCLASS"]
    width = len(period_cols)
    for i, rc in enumerate(out["RESERVINGCLASS"]):
        vec = override.vector(rc, width)
        if vec is None:
            continue
        out.loc[out.index[i], period_cols] = vec
    return out
