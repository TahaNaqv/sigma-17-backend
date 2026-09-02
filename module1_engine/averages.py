"""LDF average bases for the triangle sheets (WP1 / requirement 7).

Every named basis here is a **per-cell exclusion mask** over the age-to-age matrix, reduced by
a mean (or a median). ``custom`` is the user editing that mask directly. One primitive, one
code path, one audit record — and "customise average for the user" falls out of it for free.

Two conventions are load-bearing and must not drift:

**Column alignment.** Development column ``j`` holds the factor from dev ``j`` to dev ``j+1``.
This is forced, not chosen: the workbook derives ``Selected CDF[j] = PRODUCT(LDF[j] : last)``
and hands the CDF at column ``m`` to the cohort whose maturity is ``m``, so ``LDF[m]`` has to
be the ``m -> m+1`` factor. The age-to-age block and Simple Avg already obeyed it; the
engine's old inline ``Weighted Avg`` loop did not, and was written one column to the right
(defect F5 — see docs/LDF_AVERAGE_SELECTION_PLAN.md §1.3).

**Undefined is NaN, zero is data.** A factor is undefined when the denominator is zero or
either cumulative cell is missing. It is *not* undefined when the numerator is genuinely zero:
cumulative paid can fall, because the Motor recovery substitution puts ``AMOUNTRECOVERED``
into ``Amount`` for recovery heads. The engine used to ``.fillna(0)`` the undefined cells and
average over them, which is what collapsed Simple Avg to zero (defect F3).
"""

from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np

# --------------------------------------------------------------------------- #
# Bases
# --------------------------------------------------------------------------- #

BASIS_ALL = "all"
BASIS_EX_HI_LO = "ex_hi_lo"
BASIS_LAST_4 = "last_4"
BASIS_LAST_8 = "last_8"
BASIS_MEDIAN = "median"
BASIS_VOLUME_WEIGHTED = "volume_weighted"
BASIS_CUSTOM = "custom"

BASES: tuple[str, ...] = (
    BASIS_ALL,
    BASIS_EX_HI_LO,
    BASIS_LAST_4,
    BASIS_LAST_8,
    BASIS_MEDIAN,
    BASIS_VOLUME_WEIGHTED,
    BASIS_CUSTOM,
)

BASIS_LABELS: dict[str, str] = {
    BASIS_ALL: "Simple average (all periods)",
    BASIS_EX_HI_LO: "Simple average excluding high and low",
    BASIS_LAST_4: "Simple average, last 4 periods",
    BASIS_LAST_8: "Simple average, last 8 periods",
    BASIS_MEDIAN: "Median",
    BASIS_VOLUME_WEIGHTED: "Volume weighted",
    BASIS_CUSTOM: "Custom selection",
}

#: How many most-recent valid accident periods each "last N" basis keeps.
BASIS_LAST_N: dict[str, int] = {BASIS_LAST_4: 4, BASIS_LAST_8: 8}

#: Minimum valid cells before excluding the high and the low is meaningful. Below this the
#: column falls back to `all` — dropping the extremes of two cells leaves nothing, and of
#: three leaves exactly the median.
EX_HI_LO_MIN_VALID = 3


# --------------------------------------------------------------------------- #
# Primitives
# --------------------------------------------------------------------------- #


def age_to_age_matrix(cumulative: np.ndarray) -> np.ndarray:
    """Age-to-age factors from a cumulative triangle's development columns.

    Returns an array the same shape as ``cumulative``; column ``j`` is the ``j -> j+1``
    factor and the final column is always NaN (there is no factor beyond the last observed
    development). Undefined cells are NaN, never 0.
    """
    cum = np.asarray(cumulative, dtype=float)
    if cum.ndim != 2 or cum.shape[1] == 0:
        return np.full(cum.shape, np.nan, dtype=float)
    out = np.full(cum.shape, np.nan, dtype=float)
    for j in range(cum.shape[1] - 1):
        den = cum[:, j]
        num = cum[:, j + 1]
        ok = np.isfinite(den) & np.isfinite(num) & (den != 0)
        out[ok, j] = num[ok] / den[ok]
    return out


def validity(a2a: np.ndarray) -> np.ndarray:
    """Boolean mask of cells that hold a real factor."""
    return np.isfinite(np.asarray(a2a, dtype=float))


def _last_n_mask(a2a: np.ndarray, n_last: int) -> np.ndarray:
    """Exclude all but the ``n_last`` most recent VALID accident periods, per column.

    "Most recent valid" rather than "most recent" is the point: taking the last four rows of
    a column whose last three rows are undefined would average one factor and call it four.
    """
    valid = validity(a2a)
    excluded = np.zeros(a2a.shape, dtype=bool)
    for j in range(a2a.shape[1]):
        rows = np.flatnonzero(valid[:, j])
        if rows.size > n_last:
            excluded[rows[:-n_last], j] = True
    return excluded


def _ex_hi_lo_mask(a2a: np.ndarray) -> np.ndarray:
    """Exclude one highest and one lowest valid cell per column.

    Ties drop a single occurrence of each, not every cell holding that value — excluding
    every tied cell could empty a column that has plenty of data.
    """
    arr = np.asarray(a2a, dtype=float)
    valid = validity(arr)
    excluded = np.zeros(arr.shape, dtype=bool)
    for j in range(arr.shape[1]):
        rows = np.flatnonzero(valid[:, j])
        if rows.size < EX_HI_LO_MIN_VALID:
            continue  # falls back to `all` for this column
        values = arr[rows, j]
        excluded[rows[int(np.argmax(values))], j] = True
        excluded[rows[int(np.argmin(values))], j] = True
    return excluded


def mask_for_basis(
    a2a: np.ndarray,
    basis: str,
    extra_excluded: Iterable[Sequence[int]] | None = None,
) -> np.ndarray:
    """Boolean mask, ``True`` where a cell is EXCLUDED from the average.

    ``extra_excluded`` is a list of ``(row, col)`` pairs — the user's own strikethroughs,
    unioned on top of whatever the named basis produced. Out-of-range pairs are ignored
    rather than raising: a stored selection may outlive a re-shaped triangle.
    """
    arr = np.asarray(a2a, dtype=float)
    if basis in BASIS_LAST_N:
        mask = _last_n_mask(arr, BASIS_LAST_N[basis])
    elif basis == BASIS_EX_HI_LO:
        mask = _ex_hi_lo_mask(arr)
    else:
        mask = np.zeros(arr.shape, dtype=bool)
    for pair in extra_excluded or ():
        try:
            r, c = int(pair[0]), int(pair[1])
        except (TypeError, ValueError, IndexError):
            continue
        if 0 <= r < mask.shape[0] and 0 <= c < mask.shape[1]:
            mask[r, c] = True
    return mask


def column_counts(a2a: np.ndarray, mask: np.ndarray | None = None) -> list[int]:
    """Valid, non-excluded cells per development column.

    Written into the workbook as `Factor Count` and shown per column in the UI. On the
    reference book no column has more than three, which is why `last_4` and `last_8` return
    the plain simple average there — a fact the user must be able to see rather than infer.
    """
    valid = validity(a2a)
    if mask is not None:
        valid = valid & ~np.asarray(mask, dtype=bool)
    return [int(valid[:, j].sum()) for j in range(valid.shape[1])]


def reduce_masked(
    a2a: np.ndarray, mask: np.ndarray | None = None, reducer: str = "mean"
) -> list[float | None]:
    """Per-column mean (or median) over valid, non-excluded cells.

    A column with no survivor yields ``None`` — never ``0`` (which the old code produced and
    which collapses the CDF) and never ``1.0`` (which silently means "no development" and
    would understate the ultimate).
    """
    arr = np.asarray(a2a, dtype=float)
    keep = validity(arr)
    if mask is not None:
        keep = keep & ~np.asarray(mask, dtype=bool)
    out: list[float | None] = []
    for j in range(arr.shape[1]):
        values = arr[keep[:, j], j]
        if values.size == 0:
            out.append(None)
        elif reducer == "median":
            out.append(float(np.median(values)))
        else:
            out.append(float(values.mean()))
    return out


def volume_weighted(
    cumulative: np.ndarray, mask: np.ndarray | None = None
) -> list[float | None]:
    """Volume-weighted link ratios, honouring the same exclusion mask.

    A masked cell removes that accident period's contribution from BOTH the numerator and
    the denominator of its column, which is what keeps the ratio a ratio.
    """
    cum = np.asarray(cumulative, dtype=float)
    a2a = age_to_age_matrix(cum)
    keep = validity(a2a)
    if mask is not None:
        keep = keep & ~np.asarray(mask, dtype=bool)
    out: list[float | None] = []
    for j in range(cum.shape[1]):
        if j >= cum.shape[1] - 1:
            out.append(None)
            continue
        rows = keep[:, j]
        den = float(np.nansum(cum[rows, j]))
        num = float(np.nansum(cum[rows, j + 1]))
        out.append(num / den if den else None)
    return out


def ldf_for_basis(
    cumulative: np.ndarray,
    basis: str,
    extra_excluded: Iterable[Sequence[int]] | None = None,
) -> list[float | None]:
    """The LDF vector a basis produces, aligned to development columns."""
    cum = np.asarray(cumulative, dtype=float)
    a2a = age_to_age_matrix(cum)
    mask = mask_for_basis(a2a, basis, extra_excluded)
    if basis == BASIS_VOLUME_WEIGHTED:
        return volume_weighted(cum, mask)
    reducer = "median" if basis == BASIS_MEDIAN else "mean"
    return reduce_masked(a2a, mask, reducer)


def cdf_from_ldf_row(ldf: Sequence[float | None]) -> list[float]:
    """Suffix product, blanks treated as 1.0 — exactly what Excel's ``=PRODUCT(j:last)``
    does with empty cells, so the benchmark CDF behaves like the Selected CDF beside it."""
    values = [float(v) if v is not None and np.isfinite(v) else 1.0 for v in ldf]
    out = [1.0] * len(values)
    running = 1.0
    for i in range(len(values) - 1, -1, -1):
        running *= values[i]
        out[i] = running
    return out


# --------------------------------------------------------------------------- #
# The benchmark block the engine writes
# --------------------------------------------------------------------------- #

#: (row label, basis) for the LDF rows written under the age-to-age block. Each is followed
#: by its CDF row. `Factor Count` is appended last by `benchmark_rows`.
BENCHMARK_BASES: tuple[tuple[str, str], ...] = (
    ("Simple Avg", BASIS_ALL),
    ("Weighted Avg", BASIS_VOLUME_WEIGHTED),
    ("Ex-Hi-Lo Avg", BASIS_EX_HI_LO),
    ("Last 4 Avg", BASIS_LAST_4),
    ("Last 8 Avg", BASIS_LAST_8),
    ("Median", BASIS_MEDIAN),
)


def benchmark_rows(cumulative: np.ndarray) -> list[tuple[str, list]]:
    """Every benchmark row for one triangle sheet, in write order.

    `Simple Avg` and `Weighted Avg` keep their historic labels because actuaries and the
    preview formatter both know them; the rest are new. `Factor Count` closes the block so
    a reader can see immediately that, say, "Last 4 Avg" averaged three factors and is
    therefore identical to the row above it.
    """
    rows: list[tuple[str, list]] = []
    for label, basis in BENCHMARK_BASES:
        ldf = ldf_for_basis(cumulative, basis)
        rows.append((f"{label} LDF", list(ldf)))
        rows.append((f"{label} CDF", cdf_from_ldf_row(ldf)))
    a2a = age_to_age_matrix(np.asarray(cumulative, dtype=float))
    rows.append(("Factor Count", column_counts(a2a)))
    return rows
