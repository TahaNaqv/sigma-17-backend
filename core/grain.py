"""Accident/development period granularity.

Quarterly is not a formatting choice in this codebase — it is a **data contract**. The
string ``"2018-Q1"`` is parsed by ``module2_engine.engine.calculate_sequence``, keys the
``Allocation EP`` / ``LIC (OS) Summary`` / ``UPR Run-Off`` sheets, and is stored on every
row of the client's ``PREVIOUS_PERIOD_LIC`` datasets (2,144 rows across 24 quarters on the
reference book). Re-granularising the *booking* basis would orphan all of it and break the
IFRS 17 movement comparatives.

So this module exists for two reasons, in this order:

1. **To stop the assumption being implicit.** Roughly 28 sites across the two engines hard-code
   ``freq='QE'``, ``to_period('Q')``, ``'%Y-Q%q'``, ``split('-Q')`` or ``(1/4)``. Routing them
   through one object makes the quarterly choice visible and changeable, without changing it.
2. **To let the diagnostic triangle service run at any grain** (see
   ``module1_engine.triangles``), which is what requirement 5 actually asked for.

``QUARTERLY`` is the default everywhere and every engine entry point binds it, so introducing
this module must be — and is — bit-identical.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class PeriodGrain:
    """One accident/development period granularity."""

    key: str
    label: str
    #: pandas offset alias for period-END dates ("ME" / "QE" / "YE").
    pandas_freq: str
    #: pandas period alias ("M" / "Q" / "Y").
    period_alias: str
    periods_per_year: int

    # -- period construction ------------------------------------------------

    def to_period(self, values) -> pd.Series:
        """Datetime-like series -> PeriodIndex at this grain."""
        return pd.to_datetime(values).dt.to_period(self.period_alias)

    def period(self, value) -> pd.Period:
        return pd.Period(pd.Timestamp(value), freq=self.period_alias)

    def period_range(self, start, end) -> pd.PeriodIndex:
        return pd.period_range(start=start, end=end, freq=self.period_alias)

    def date_range(self, start, end) -> pd.DatetimeIndex:
        """Period-END dates between start and end, inclusive."""
        return pd.date_range(start=start, end=end, freq=self.pandas_freq)

    # -- labels -------------------------------------------------------------

    def label_for(self, period: Any) -> str:
        """Canonical label. Quarterly reproduces the historic ``'%Y-Q%q'`` exactly, which
        matters because that string is a join key in stored client data."""
        p = period if isinstance(period, pd.Period) else self.period(period)
        if self.period_alias == "Q":
            return f"{p.year}-Q{p.quarter}"
        if self.period_alias == "M":
            return f"{p.year}-{p.month:02d}"
        return f"{p.year}"

    def labels(self, periods) -> list[str]:
        return [self.label_for(p) for p in periods]

    def parse(self, label: str) -> pd.Period:
        """Inverse of :meth:`label_for`.

        Replaces ``calculate_sequence``'s ``str(x).split("-Q")`` — the single hardest-coded
        coupling in the codebase, and the one that makes any future change dangerous.
        """
        text = str(label).strip()
        if self.period_alias == "Q":
            year, quarter = text.split("-Q")
            return pd.Period(freq="Q", year=int(year), quarter=int(quarter))
        if self.period_alias == "M":
            year, month = text.split("-")
            return pd.Period(freq="M", year=int(year), month=int(month))
        return pd.Period(freq="Y", year=int(text))

    def sort_key(self, label: str) -> tuple[int, int]:
        """Chronological ordering key for a label, without constructing a Period."""
        p = self.parse(label)
        return (p.year, getattr(p, "quarter", getattr(p, "month", 1)))

    # -- rates --------------------------------------------------------------

    def annual_to_period_rate(self, annual):
        """Convert an annual spot rate to this grain. Quarterly reproduces
        ``(1 + r) ** (1 / 4) - 1`` exactly."""
        return (1 + annual) ** (1 / self.periods_per_year) - 1


MONTHLY = PeriodGrain("monthly", "Monthly", "ME", "M", 12)
QUARTERLY = PeriodGrain("quarterly", "Quarterly", "QE", "Q", 4)
YEARLY = PeriodGrain("yearly", "Yearly", "YE", "Y", 1)

GRAINS: dict[str, PeriodGrain] = {g.key: g for g in (MONTHLY, QUARTERLY, YEARLY)}
GRAIN_KEYS: tuple[str, ...] = tuple(GRAINS)

#: The booking basis. Every engine entry point binds this; only the diagnostic triangle
#: service varies it. See the module docstring for why.
DEFAULT_GRAIN = QUARTERLY


def get_grain(key: str | None) -> PeriodGrain:
    if not key:
        return DEFAULT_GRAIN
    try:
        return GRAINS[key]
    except KeyError:
        raise ValueError(
            f"Unknown period grain {key!r}; expected one of {GRAIN_KEYS}."
        ) from None
