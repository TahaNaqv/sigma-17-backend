# WP6 — Monthly / Quarterly / Yearly Triangles

> **Goal:** Give the actuary monthly and yearly development triangles as a diagnostic and selection
> aid, with a mathematically sound bridge from monthly factors back to the quarterly booking basis —
> while replacing the ~28 hard-coded quarterly sites with a `PeriodGrain` abstraction so a future
> re-granularisation is configuration, not a rewrite.

Status: planned (2026-08-21). Decisions: `docs/CLIENT_REQUIREMENTS_DECISIONS.md` §3 D4.
Requirement 5. Depends on WP0 and WP1.

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

### 2.3 The monthly → quarterly bridge

A monthly triangle is only useful if its conclusions can reach the booking basis. They can, exactly:

```
quarterly_LDF[k] = monthly_LDF[3k] × monthly_LDF[3k+1] × monthly_LDF[3k+2]
```

Development factors compose multiplicatively, so the product of three consecutive monthly link ratios
**is** the quarterly link ratio for that development year-quarter. The monthly view therefore offers
**"derive quarterly LDFs"**, producing a vector that writes through WP1's existing `ldf_overrides`
path. The actuary selects at monthly resolution — where the pattern is legible — and books quarterly.

The converse does not hold: a yearly LDF cannot be decomposed into quarterly factors without an
assumption. **Yearly is diagnostic only** and the UI offers no derive action for it. Stating this
plainly in the product is better than offering an action that silently assumes a within-year shape.

Provenance is recorded — `ldf_selection.derived_from = {"grain": "monthly", "method": "compose_3"}` —
so a reviewer can see that a quarterly factor came from a monthly selection.

### 2.4 Alignment caveats, surfaced not hidden

* A monthly triangle needs the accident-period start to align to a quarter boundary for composition
  to be exact. When the experience period does not start on a quarter boundary the UI disables
  derivation and says why.
* Monthly triangles on thin classes are sparse; zero-denominator cells are far more common. WP1's
  NaN-not-zero correction is a **prerequisite**, not a coincidence — at monthly grain the old
  zero-fill would have made the simple average garbage in nearly every column.

## 3. Backend changes

| File | Change |
|---|---|
| `core/grain.py` | **new** — `PeriodGrain`, `MONTHLY` / `QUARTERLY` / `YEARLY` |
| `module1_engine/engine.py` | route all quarterly sites through `grain` (default `QUARTERLY`): `calculate_quarterly_premium`, `get_quarter_end_dates`, `calculate_upr` loops, `summarize_upr_by_reserving_class`, `calculate_claims_os_summary`, `calculate_incremental_triangle`, the reserve loop's `period_range` |
| `module1_engine/triangles.py` | **new** — grain-parameterised triangle set |
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
| `src/lib/ldfAverages.ts` | `composeQuarterlyFromMonthly(monthlyLdf)` — mirrored in Python, shared fixture |

The derive action shows a **preview diff** — proposed quarterly LDFs against the current Selected LDF
row — before applying. Deriving factors from a different grain is a significant judgement and must
not be a one-click surprise.

## 5. Bit-identity and goldens

Every Module 1 and Module 2 entry point defaults to `QUARTERLY`; the refactor must be
**bit-identical**, and that is the entire acceptance criterion for §3's refactor half. Existing
goldens are the assertion and are not re-captured.

The diagnostic triangle service writes nothing into job output, so it has no golden.

## 6. Tests

**`core/tests/test_grain.py`** (new)
* label / parse round-trip at all three grains
* `QUARTERLY.parse("2018-Q1")` equals the old `split("-Q")` on every label in the reference `LIC_BOP`
* `annual_to_period_rate` reproduces `(1+r)^(1/4)−1` exactly at quarterly
* `range` boundaries match `pd.date_range(freq=...)` at all grains

**`module1_engine/tests/test_golden_engines.py`**
* full summary run after the refactor is bit-identical — the gating test

**`module1_engine/tests/test_triangles.py`** (new)
* monthly triangle sums to the quarterly triangle by quarter (conservation)
* yearly sums to quarterly by year
* `composeQuarterlyFromMonthly` equals the directly-computed quarterly LDF on a dense synthetic
  triangle to 1e-10
* composition with a NaN monthly factor propagates NaN rather than silently treating it as 1.0
* non-quarter-aligned start disables derivation
* WP5 exclusions apply identically at every grain

**`src/lib/ldfAverages.test.ts`**
* TS composition matches the Python fixture

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

Refactor to `PeriodGrain` with bit-identity proof 5d, triangle service 3d, frontend 4d, tests 3d.
**~15 days.** The refactor is the majority and is the part that retains value regardless of whether
monthly booking is ever adopted.
