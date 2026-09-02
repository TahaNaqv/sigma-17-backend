"""Wide <-> long conversion for payment-pattern datasets.

An actuary reads a payment pattern WIDE — one column per development period, an unbounded
number of them. A wide table is a poor relational schema, so the row model is LONG and this
module is the single place that converts. Both the Excel importer and the template generator
route through it, so the two cannot drift.

Excel shape::

    RESERVINGCLASS | 0    | 1    | 2    | ...
    ENGINEERING    | 0.06 | 0.06 | 0.13 | ...

Long shape::

    {"reserving_class": "ENGINEERING", "dev_period": 0, "weight": 0.06}
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

import pandas as pd

CLASS_HEADER = "RESERVINGCLASS"


class PatternShapeError(ValueError):
    """The supplied sheet is not a readable wide payment pattern."""


def _as_period(header: Any) -> int | None:
    """Development-period index for a column header, or None if it is not one.

    Accepts ``0``, ``"0"``, ``"0.0"`` (Excel readily turns integer headers into floats)
    and the friendlier ``"Period 0"`` / ``"Dev 0"`` forms real templates acquire.
    """
    if isinstance(header, bool):
        return None
    if isinstance(header, (int, float)):
        value = float(header)
        return int(value) if value.is_integer() and value >= 0 else None
    text = str(header).strip().lower()
    for prefix in ("period", "dev period", "dev", "development"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return int(value) if value.is_integer() and value >= 0 else None


def wide_to_long(df: pd.DataFrame) -> list[dict]:
    """Unpivot a wide pattern sheet into long rows.

    Blank cells are skipped rather than stored as zero: "not supplied" and "supplied as
    zero" are different statements, and only the latter should pull a class's weight down.
    """
    if CLASS_HEADER not in df.columns:
        raise PatternShapeError(
            f"The sheet must have a '{CLASS_HEADER}' column plus one column per "
            f"development period (0, 1, 2, ...)."
        )

    period_cols: dict[Any, int] = {}
    for col in df.columns:
        if col == CLASS_HEADER:
            continue
        period = _as_period(col)
        if period is not None:
            period_cols[col] = period

    if not period_cols:
        raise PatternShapeError(
            "No development-period columns were found. Headers should be 0, 1, 2, ... "
            "one per development period."
        )

    seen: set[tuple[str, int]] = set()
    rows: list[dict] = []
    for record in df.to_dict(orient="records"):
        raw_class = record.get(CLASS_HEADER)
        if raw_class is None or (isinstance(raw_class, float) and pd.isna(raw_class)):
            continue
        reserving_class = str(raw_class).strip()
        if not reserving_class:
            continue
        for col, period in period_cols.items():
            value = record.get(col)
            if value is None or (isinstance(value, float) and pd.isna(value)):
                continue
            if isinstance(value, str) and not value.strip():
                continue
            try:
                weight = float(value)
            except (TypeError, ValueError) as exc:
                raise PatternShapeError(
                    f"{reserving_class}, development period {period}: "
                    f"{value!r} is not a number."
                ) from exc
            key = (reserving_class.casefold(), period)
            if key in seen:
                raise PatternShapeError(
                    f"{reserving_class}: development period {period} appears more than once."
                )
            seen.add(key)
            rows.append(
                {
                    "reserving_class": reserving_class,
                    "dev_period": period,
                    "weight": weight,
                }
            )
    if not rows:
        raise PatternShapeError("The sheet contains no pattern values.")
    return rows


def long_to_wide(rows: Iterable[Mapping[str, Any]]) -> pd.DataFrame:
    """Pivot long rows back to the wide shape, for display and export."""
    frame = pd.DataFrame(list(rows))
    if frame.empty:
        return pd.DataFrame(columns=[CLASS_HEADER])
    wide = (
        frame.pivot_table(
            index="reserving_class", columns="dev_period", values="weight", aggfunc="first"
        )
        .sort_index(axis=1)
        .reset_index()
        .rename(columns={"reserving_class": CLASS_HEADER})
    )
    wide.columns.name = None
    return wide
