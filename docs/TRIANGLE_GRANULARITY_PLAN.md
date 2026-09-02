# WP6 — Monthly / Quarterly / Yearly Triangles

> **Goal:** Give the actuary monthly and yearly development triangles as a diagnostic and selection
> aid, with a mathematically sound bridge from monthly factors back to the quarterly booking basis —
> while replacing the ~28 hard-coded quarterly sites with a `PeriodGrain` abstraction so a future
> re-granularisation is configuration, not a rewrite.

Status: **implemented** (2026-08-21). Decisions: `docs/CLIENT_REQUIREMENTS_DECISIONS.md` §3 D4.
Requirement 5. Depends on WP0 and WP1.

---

## Implementation status (2026-08-21)

Delivered. Sections below are the as-built design.

**Backend**

| File | State |
|---|---|
| `core/grain.py` | **new** — `PeriodGrain` (monthly / quarterly / yearly): period construction, the `'%Y-Q%q'` label contract, `parse`, `sort_key`, `annual_to_period_rate` |
| `module1_engine/triangles.py` | **new** — grain-parameterised triangles, credibility scoring, `volume_weighted_ldf`, `cdf_from_ldf`, `implied_cdf_from_finer_grain` |
| `module1_engine/engine.py` | ~20 quarterly sites routed through `DEFAULT_GRAIN`; one site left literal and annotated (`Underwriting Quarter` is a calendar quarter, not an accident axis) |
| `module2_engine/engine.py` | `calculate_sequence`'s `split("-Q")` replaced by `grain.sort_key`; `(1/4)` and `len(data) * 4` replaced by `periods_per_year` |
| `processing/{views,urls}.py` | `Module1TrianglesView` — any grain, optional class/treaty filter, optional implied CDF; reads the job's snapshotted claims |
| tests | `test_triangles.py` (17), `test_triangles_api.py` (11) |

**Frontend**

| File | State |
|---|---|
| `src/components/TriangleCredibility.tsx` | **new** — level, shape, fill, claim volume, sparse-column flag |
| `src/components/TriangleCredibility.test.tsx` | **new** — 8 render tests |
| `src/api/module1.ts` | `PeriodGrain`, `TriangleDto`, `ImpliedCdfDto`, `fetchTriangles` |

**Verification**

* 265 Django tests (2 pre-existing Redis-broker failures, unrelated); **224 engine tests**
  including all 8 goldens; 130 frontend tests; `vite build` clean; `tsc` unchanged at 45.
* **The `PeriodGrain` refactor is bit-identical** — `summary_ref`, `policy_upr_ref`,
  `m2_allocate_ref` and `m2_process_ref` all pass untouched. That was the gate for the
  refactor and it was run after every step of it.
* Conservation holds: monthly, quarterly and yearly triangles all total `107,488,826`.
* The implied-CDF route reproduces the measured values exactly — total ultimate
  `1,069,489,457`, 2017Q4 CDF `69.8101`.

**A scoring bug caught by verification**

The first credibility implementation gated on raw cell count (`non_empty < 30` → low). That
scored the reference book's **quarterly** triangle (26 cells, median 146 claims each) as
*less* credible than its **monthly** one (158 cells, median 24) — exactly backwards, because
a coarser grain has fewer cells by construction. Rescored on **density and volume**
(`fill_ratio`, `median_claims_per_cell`) with an absolute floor only for genuinely tiny
triangles. Quarterly now scores `high`, monthly `medium`, and at the reserving grain Health
drops `medium` → `low` between the two while Banker's Blanket is `unusable` at both. A
regression test names the original mistake.

---

## 0. Client requirement

> "quarterly triangles are already being calculated, want to have functionality of monthly and
> yearly triangles"

## 1. Why booking stays quarterly

Quarterly is not a formatting choice — it is a **data contract** spanning both engines and the
client's historic data.

### 1.1 The contract

| Site | Coupling |
|---|---|
| `module2_engine/engine.py::calculate_sequence` | **parses** `"YYYY-Qn"` with `str(x).split("-Q")` |
| `calculate_discount_rates` | builds `len(data) × 4` quarterly buckets from annual bands |
| `convert_annual_to_quarterly` | `(1+r)^(1/4) − 1` |
| run-off loop | maps development index → discount index in quarters |
| `Allocation EP`, `LIC (OS) Summary`, `UPR Run-Off` | keyed on `Accident_Period` strings |
| `PREVIOUS_PERIOD_LIC` dataset kind | `accident_period` column, quarterly strings |
| movement disclosure | prior-period comparatives joined on the same key |

### 1.2 The client already holds quarterly-keyed history

`benchmarks/fixtures/m2_process_ref/Previous_period.xlsx`, sheet `LIC_BOP`: **2,144 rows across 24
distinct quarterly `Accident_Period` strings** (`2018-Q1` …). Re-granularising the booking basis
orphans every one of those rows and breaks the IFRS 17 movement comparatives. That is a data
migration with regulatory consequences, not a refactor.

### 1.3 Cost

Monthly across eight years is ~96 development columns versus ~32. `m1.reserve_loop` is the dominant
stage and is openpyxl-write-bound; the reference book already produces 33 workbooks per run. Monthly
end-to-end is roughly a 9x cell-count increase in the slowest stage.

**Therefore: monthly and yearly are a diagnostic and selection view. Booking stays quarterly.**

## 2. Design

### 2.1 `PeriodGrain`

One abstraction replacing every hard-coded quarterly assumption:

```python
# core/grain.py  (new)
@dataclass(frozen=True)
class PeriodGrain:
    key: str                       # "monthly" | "quarterly" | "yearly"
    pandas_freq: str               # "ME" | "QE" | "YE"
    period_alias: str              # "M"  | "Q"  | "Y"
    label_format: str              # "%Y-%m" | "%Y-Q%q" | "%Y"
    periods_per_year: int          # 12 | 4 | 1

    def to_period(self, s: pd.Series) -> pd.Series
    def label(self, p) -> str
    def parse(self, label: str) -> pd.Period          # replaces the "-Q" split
    def range(self, start, end) -> pd.PeriodIndex
    def annual_to_period_rate(self, r) -> float       # (1+r)^(1/periods_per_year) − 1
```

`QUARTERLY` is the default everywhere; every existing call site is rewritten to route through it with
no behaviour change. `calculate_sequence`'s string split becomes `grain.parse`, which is the single
most important line to remove — it is the hard-coupling that makes any future change dangerous.

**This refactor is the durable deliverable of WP6.** Even if the client never adopts monthly booking,
the codebase stops being one string-format decision away from a rewrite.

### 2.2 The diagnostic triangle service

Triangles at any grain are built from the same claim frames, independent of the reserving pipeline:

```python
# module1_engine/triangles.py  (new)
def build_triangle_set(paid_df, os_df, *, grain, start, end,
                       reserving_class, head_of_damage, ri_type,
                       excluded_claims=None) -> TriangleSet
```

Returns incremental, cumulative, age-to-age and the WP1 average-basis set — reusing
`module1_engine/averages.py` unchanged, so every average basis and the exclusion mask work
identically at every grain. WP5's claim exclusions apply here too.

Exposed as `GET /api/module1/jobs/{pk}/triangles/?grain=monthly&...`, computed on demand from the
job's snapshotted claim data. **Not written into the reserve workbooks** — that would change output
shape for every client and multiply the write-bound stage for a diagnostic.

### 2.3 The LDF-composition bridge does NOT exist — measured

An earlier draft of this plan asserted:

> `quarterly_LDF[k] = monthly_LDF[3k] × monthly_LDF[3k+1] × monthly_LDF[3k+2]`
> "Development factors compose multiplicatively, so the product of three consecutive
> monthly link ratios **is** the quarterly link ratio."

**That is false.** Measured on the reference claims (6,556 paid rows in the experience
window):

| k | quarterly LDF | product of 3 monthly | error |
|---:|---:|---:|---:|
| 0 | 3.457375 | 17.597184 | **+408.98%** |
| 1 | 1.610481 | 1.731994 | +7.55% |
| 2 | 1.559178 | 1.471923 | +5.60% |
| 3 | 1.196602 | 1.590750 | +32.94% |

The reason is structural, not numerical. A quarterly accident period **aggregates three
monthly cohorts at different maturities**:

```
accident month 2016-01 -> by the end of 2016Q1 it has had 3 months to develop (12 claims)
accident month 2016-02 -> 2 months                                            ( 8 claims)
accident month 2016-03 -> 1 month                                             (28 claims)
```

So the quarterly development-0 cell blends cohorts with 3, 2 and 1 months of development.
A quarterly link ratio is therefore not the product of three monthly link ratios at any
fixed monthly offset, and no re-indexing fixes it.

**Any feature offering "derive quarterly LDFs from the monthly triangle" would be wrong.**
It is removed from this plan.

### 2.3b The valid bridge is through ultimates, not factors

What *is* sound: each quarterly cohort is **exactly three monthly cohorts** (verified), and
the two triangles carry identical totals (`107,488,826` both ways). So monthly experience
can be projected per monthly cohort, the ultimates summed within a quarter, and the result
expressed as an **implied quarterly CDF** — which is precisely the object the engine already
consumes via the `Selected CDF` row.

```
implied_CDF[q] = Σ(monthly ultimates for the 3 months in q) / paid-to-date[q]
```

This is exact and injectable through the existing `ldf_overrides` path. **But it must be
gated on credibility** — see §2.3c.

### 2.3c Monthly is not statistically usable on much of this book

Applying the §2.3b bridge to the reference book:

| quarter | ultimate (quarterly) | ultimate (from monthly) | CDF quarterly | CDF implied |
|---|---:|---:|---:|---:|
| 2016Q1 | 188,405 | 206,897 | 1.00 | 1.10 |
| 2017Q3 | 109,832,532 | 178,851,838 | 7.38 | 12.02 |
| 2017Q4 | 182,198,395 | 498,343,334 | **25.52** | **69.81** |
| **total** | **556,572,230** | **1,069,489,457** | | **+92.16%** |

A 92% higher ultimate, driven by a tail CDF of 69.8. That is sparsity, not signal:

| grain | triangle | upper-triangle cells | non-empty | median claims/cell |
|---|---|---:|---:|---:|
| quarterly | 8 × 8 | 36 | 26 (72%) | **146** |
| monthly | 24 × 23 | 299 | 158 (53%) | **24** |

And the engine reserves per **(class × head-of-damage × treaty)**, not book-wide. At that
grain: median non-empty cells 16 quarterly / 51 monthly, but **4 of 14 class-treaty
triangles have fewer than 10 non-empty monthly cells** — Banker's Blanket has 3 cells from
6 claims, Marine 1 cell from 2 claims.

**Design consequence:** every monthly triangle carries a credibility indicator (accident
periods, non-empty cells, claim count, and the sparsest development column), and the
§2.3b derive action is **disabled** below a credibility floor. Offering an implied CDF from
a three-cell triangle would be malpractice, and it is exactly what an eager user would click.

### 2.3d Yearly is horizon-limited

The reference experience window is 24 months (`spec.json`: 01-01-2016 → 31-12-2017), giving
**2 accident years** — a 2×2 triangle with one usable link ratio. Yearly is worth building
because a real valuation runs a longer window, but the view must state its own accident-period
count rather than present a 2×2 as an analysis.

### 2.4 Credibility is a first-class output, not a footnote

Every triangle the service returns carries a `credibility` block:

```python
{"accident_periods": 24, "dev_periods": 23,
 "cells_in_upper_triangle": 299, "non_empty_cells": 158,
 "claims": 6556, "median_claims_per_cell": 24,
 "sparsest_dev_column": {"index": 19, "non_empty": 2},
 "level": "low"}          # high | medium | low | unusable
```

`unusable` (< 10 non-empty cells) disables the §2.3b derive action outright and renders the
triangle greyed with the reason. `low` allows it behind an explicit confirmation. The
thresholds are properties of the data, so they are computed per triangle rather than assumed
from the grain — a high-volume class may support monthly where the book as a whole does not.

### 2.5 Alignment caveats, surfaced not hidden

* A monthly triangle needs the experience period to start on a month boundary; the service
  reports the actual first accident period rather than silently shifting it.
* Monthly triangles on thin classes are sparse and zero-denominator cells are common. WP1's
  NaN-not-zero correction to `calculate_age_to_age_factors` is a **prerequisite**, not a
  coincidence — at monthly grain the old zero-fill would have made the simple average
  meaningless in almost every column.

## 3. Backend changes

| File | Change |
|---|---|
| `core/grain.py` | **new** — `PeriodGrain`, `MONTHLY` / `QUARTERLY` / `YEARLY` |
| `module1_engine/engine.py` | route all quarterly sites through `grain` (default `QUARTERLY`): `calculate_quarterly_premium`, `get_quarter_end_dates`, `calculate_upr` loops, `summarize_upr_by_reserving_class`, `calculate_claims_os_summary`, `calculate_incremental_triangle`, the reserve loop's `period_range` |
| `module1_engine/triangles.py` | **new** — grain-parameterised triangle set, credibility scoring, and the implied-CDF derivation of §2.3b |
| `module2_engine/engine.py` | `calculate_sequence` → `grain.parse`; `convert_annual_to_quarterly` → `grain.annual_to_period_rate`; `calculate_discount_rates` → `periods_per_year` |
| `processing/views.py` | `Module1TrianglesView` (grain, class, head of damage, treaty, exclusions) |
| `processing/urls.py` | route |

The Module 2 changes are **pure refactors with `QUARTERLY` bound** — Module 2 is not made
grain-variable in WP6, because doing so without the data migration would be a trap. The abstraction
is introduced; the switch is not thrown.

## 4. Frontend changes

| File | Change |
|---|---|
| `src/components/GrainSelector.tsx` | **new** — monthly / quarterly / yearly toggle |
| `src/components/TriangleGrid.tsx` | accept any grain; column headers from grain labels |
| `src/components/ReserveCdfEditor.tsx` | grain toggle above each triangle; "derive quarterly LDFs" on monthly with a preview diff against the current selection |
| `src/api/module1.ts` | `fetchTriangles(jobId, params)`, `TriangleSetDto` |
| `src/components/TriangleCredibility.tsx` | **new** — accident periods, non-empty cells, claims, sparsest column, and the level badge |

The derive action shows a **preview diff** — the implied quarterly CDF against the current Selected
CDF row — before applying, and is disabled outright on an `unusable` triangle. Deriving factors from
a different grain is a significant judgement; on a three-cell triangle it is not a judgement at all.

## 5. Bit-identity and goldens

Every Module 1 and Module 2 entry point defaults to `QUARTERLY`; the refactor must be
**bit-identical**, and that is the entire acceptance criterion for §3's refactor half. Existing
goldens are the assertion and are not re-captured.

The diagnostic triangle service writes nothing into job output, so it has no golden.

## 6. Tests

**`core/tests/test_grain.py`** (new)
* label / parse round-trip at all three grains
* `QUARTERLY.parse("2018-Q1")` equals the old `split("-Q")` on every label in the reference
  `LIC_BOP` (2,144 rows, 24 distinct quarters)
* `annual_to_period_rate` reproduces `(1+r)^(1/4)−1` exactly at quarterly
* `range` boundaries match `pd.date_range(freq=...)` at all grains

**`module1_engine/tests/test_golden_engines.py`**
* the full summary run after the refactor is bit-identical — the gating test

**`module1_engine/tests/test_triangles.py`** (new)
* **conservation**: the monthly triangle's total equals the quarterly triangle's total
  (measured `107,488,826` both ways) and the yearly triangle's likewise
* each quarterly cohort maps to exactly 3 monthly cohorts
* **§2.3 negative test**: the product of three monthly LDFs does **not** equal the quarterly
  LDF on the reference data, and no derive action exposes that composition. This is a test
  that an incorrect feature has *not* been reintroduced
* **§2.3b**: the implied quarterly CDF from monthly ultimates reproduces the measured values
  (2017Q4 → 69.81) and reconciles to the summed monthly ultimates
* credibility: the reference book's monthly triangle scores `low`; Banker's Blanket
  (3 non-empty cells, 6 claims) scores `unusable` and the derive action is disabled
* a non-month-aligned experience start is reported, not silently shifted
* WP5 claim exclusions apply identically at every grain

## 7. Edge cases

* **Sparse monthly triangles** — many columns with a single observation; WP1's "no valid cells" path
  and the `ex_hi_lo` fallback both apply and are asserted at monthly grain.
* **Yearly with fewer than 3 accident years** — too few for any average basis; the UI states this
  rather than showing a one-point mean.
* **Development horizon** — a monthly triangle over the reference experience period is ~96 columns.
  `MODULE1_OUTPUT_PREVIEW_MAX_CELLS` will trip; the triangle endpoint returns the numeric matrix
  even when the display grid is suppressed, as WP1 already provides.
* **Partial trailing period** — a monthly triangle whose final month is incomplete produces a
  misleadingly low final diagonal; flagged in the UI and excluded from `last_n` bases by default.
* **Composition across a gap** — if any of the three monthly factors is missing, the derived
  quarterly factor is `null`, never a partial product.

## 8. Estimate

| | |
|---|---|
| `PeriodGrain` refactor across ~28 sites, with the bit-identity proof | 5d |
| triangle service (all grains) + credibility scoring | 3d |
| implied-CDF derive (§2.3b) behind the credibility gate | 2d |
| frontend: grain selector, credibility surfacing, derive-with-preview | 4d |
| tests | 3d |
| **Total** | **~17 days** |

Two days above the pre-verification estimate. The credibility machinery (§2.3c) and the
replacement of the LDF-composition bridge with the implied-CDF route (§2.3b) were both
discovered by measuring, and neither is optional: without them the feature would either
mislead or offer a mathematically invalid action.

## 9. What changed after verification

The earlier draft's headline capability — "derive quarterly LDFs from the monthly triangle" —
was **mathematically wrong** and has been removed. It was replaced by the implied-CDF route,
which is exact, plus a credibility gate, because on this book the monthly view produces a 92%
higher ultimate driven entirely by sparsity. Everything else in the plan (booking stays
quarterly, `PeriodGrain`, diagnostic-first) survived verification unchanged.
