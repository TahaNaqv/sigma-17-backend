# WP3a — Payment Pattern Override

> **Goal:** Let the actuary supply their own payment pattern per reserving class — by Excel
> upload or in-app grid — in place of the engine-derived one, mirroring the Update Reserve
> LDF surface the client pointed at.

Status: **implemented** (2026-08-21). Requirement 2.
Decisions: `docs/CLIENT_REQUIREMENTS_DECISIONS.md` §3 D2.
Split out of `CASHFLOW_OVERRIDE_PLAN.md`, which now covers requirement 3 only —
the two turned out to have different semantics and different blast radii.

Every claim in §1 was **measured**, not inferred, and the measurements are reproduced so a
reviewer can re-run them.

---

## Implementation status (2026-08-21)

Delivered. The sections below are the as-built design.

**Backend**

| File | State |
|---|---|
| `module2_engine/pattern_override.py` | **new** — `PatternOverride`, `rebase`, validation/modes, `OverrideReport`, `apply_to_additional_matrix`, `apply_to_avg_df` |
| `module2_engine/engine.py` | `pattern_override` threaded through `_compute_allocate_frames` → `_build_allocate_outputs` → `run_module2_allocate` / `_process_intermediates` / `run_module2_process` / `run_module2_movement`; two insertion points (LIC re-based, LRC direct) |
| `datasets/models.py` | `PaymentPatternRow`, `Kind.PAYMENT_PATTERN` (+ migration `0005`) |
| `datasets/services/wide_pattern.py` | **new** — the single wide↔long converter |
| `datasets/services/{columns,excel_import,templates}.py`, `serializers.py` | wide template, unpivot on import, long row serializer |
| `processing/{views,tasks,urls}.py` | `payment_pattern_dataset_id` + `pattern_mode` on allocate and process; `_load_pattern_override` (snapshot-backed); `Module2PatternPreviewView`; `PatternValidationError` surfaced with per-class detail |
| `processing/benchmarks.py` + `benchmarks/fixtures/m2_pattern_ref/` | **new golden**, frozen at measure level |

**Frontend**

| File | State |
|---|---|
| `src/components/PaymentPatternEditor.tsx` | **new** — three curves per class (engine / derived / yours), sparkline, per-row and bulk "derive", normalise, reset |
| `src/components/PaymentPatternEditor.test.tsx` | **new** — 10 render tests |
| `src/api/module2.ts` | preview DTOs, `savePatternDraftAsDataset`, override on the process payload |
| `src/api/datasets.ts` | the new kind, label and grid columns |
| `src/pages/IbnrAllocationPage.tsx`, `src/state/wizards/ibnr.ts` | collapsible optional step; draft persisted and saved as a dataset on submit |

**Verification**

* 226 Django tests pass (2 pre-existing Redis-broker failures, unrelated); 161 engine tests
  (+21 pattern); 105 frontend tests (+10); `vite build` clean; `tsc` unchanged at its
  45-error baseline.
* The **path-specific no-op** holds exactly: the derived pattern leaves `additional_matrix`
  identical to `2.22e-16` and `Discounting Impact` unchanged, while moving
  `GMM LRC_Discounted_CY` by −0.499%.
* The **§1.3b acceptance map** is asserted measure by measure on the reference book.
* The new `m2_pattern_ref` golden was drift-tested: disabling the LRC half of the override
  fails it, restoring passes.

**Completeness audit — four further gaps found and closed**

1. **The movement disclosure did not inherit the pattern its process job used.** The task
   passed `pattern_override` but the movement job's own `input_meta` never carried one, so
   it was always `None`. A movement disclosure chained off a process run that *used* a
   pattern would re-run the pipeline **without** it and publish figures that silently
   disagreed with the process output it was based on. Fixed with
   `_load_pattern_override(job, inherit_from=process_job)`, mirroring the existing
   Previous Period / Expense CF inheritance; an explicit pattern on the movement job still
   wins. This was the most serious of the four — it affects a signed disclosure.
2. **The preview endpoint accepted process jobs, which cannot serve it.** A process job's
   output is `Module2_Final_Output.xlsx` only; `Combined_Summary.xlsx` lives on its allocate
   ancestor. Now resolved through `job.source_job`, with an actionable error when there is none.
3. **Pattern snapshots were being staged as engine input sheets.** They are consumed as rows,
   so `_materialize_job_snapshots` was writing throwaway workbooks nothing reads. Skipped,
   like the movement-override kind already was.
4. **Payment-pattern datasets were invisible in Data Hub** — creatable from the wizard but
   with no tab, so un-editable, un-lockable and un-deletable. Added, along with
   `ifrs17_movement_override`, which had the same pre-existing omission.

Plus strict-mode coverage end to end (`shape_only` renormalises `20/30/30/20`; `strict`
rejects it naming the class; `strict` accepts weights that already sum to 1).

**Two things worth recording**

1. *A patch script that asserted mid-way silently discarded its own file write*, leaving
   `run_module2_allocate` called without the override while every unit test still passed —
   caught only because the end-to-end task test asserted `override_report` was persisted.
   End-to-end assertions earn their keep precisely here.
2. The client's editor request is satisfied by the **process** step, not allocate: the
   preview needs a completed allocate run, and the process job re-runs allocate internally,
   so an override supplied there reaches both the LIC and LRC paths.

**Deployment steps performed**

`manage.py migrate` (datasets `0005`). No new permissions or seed data — the feature reuses
`module2.run` and `datasets.edit`.

---

## 0. Client requirement

> "payment pattern calculation already being done, need a place as an excel input if want to
> use different pattern (similar to where LDFS are calculated and a space to select LDFs)"

The parenthetical is the design brief: mirror the Update Reserve LDF editor — show what the
engine computed, let the user override it, keep preview equal to output.

---

## 1. Verified ground truth

### 1.1 The sheet named "Payment Pattern" is not a payment pattern

`_build_allocate_outputs` derives `avg_df` as the **normalised column-sum of the per-row
`additional_matrix` across gross rows** (reconstruction verified exactly). Each row of that
matrix is one cohort's *conditional* future payout — "of what remains unpaid, what fraction
pays in each subsequent quarter" — and every row sums to exactly 1.0 (min = median = max =
1.0 across all 1,238 gross rows).

Averaging conditional patterns across cohorts of **every maturity** is the problem. A nearly
run-off cohort has essentially all of its small remainder paying immediately, so its row is
≈ `[1, 0, 0, …]`. Averaging those in with young cohorts loads the first column heavily:

| ENGINEERING, first four development quarters | 0 | 1 | 2 | 3 |
|---|---:|---:|---:|---:|
| `Payment Pattern` sheet (what the engine uses) | **0.4810** | 0.1844 | 0.1278 | 0.0822 |
| From-inception incremental pattern | 0.0604 | 0.0595 | 0.1261 | 0.2505 |
| The newest cohort's own future pattern | 0.0634 | 0.1342 | 0.2666 | 0.2274 |

The sheet claims 48% of claims pay in the first quarter. The book's actual from-inception
first-quarter payment is 6%. These are different objects, and the difference is not small.

Weighted-average payment lag, per class:

| Class | sheet | from-inception | |
|---|---:|---:|---|
| MEDICAL MALPRACTICE | 1.67q | 5.27q | **3.2× longer** |
| PROPERTY | 1.13q | 3.87q | **3.4× longer** |
| ENGINEERING | 1.32q | 3.53q | 2.7× longer |
| GENERAL ACCIDENT | 1.38q | 3.47q | 2.5× longer |
| MARINE | 1.37q | 2.27q | 1.7× longer |
| MOTOR COMPULSORY (AGGREGATORS) | 1.87q | 0.79q | 0.4× — *shorter* |

The direction is not uniform, but the magnitude is large in every class.

**This is very likely why the client asked.** They opened the Payment Pattern sheet, saw a
front-loaded curve that does not describe their book, and asked for a way to supply their own.

### 1.2 What the run-off actually wants

The run-off loop computes, per class and UWY:

```
cash flow(p, q) = GEP[q] × CombinedRatio × pattern[p]
discounted at    cy_disc[p + q]
```

for each future earning quarter `q` and payment lag `p`. That is **literally a from-inception
convolution**: "of claims incurred in earning quarter q, fraction `pattern[p]` pays p quarters
later." LRC covers *unexpired* risk — claims that have not occurred — so a conditional
"given already partly paid" pattern is the wrong object for it.

**The run-off wants a from-inception pattern and is being fed a conditional average.** Note
this is a defect in the LRC path specifically; the LIC path (§1.3) is already correct.

### 1.3 The engine's LIC matrix ALREADY is the re-based from-inception pattern

Proved, to zero difference:

```
engine[a][c] = Incremental[a+c+1] / (1 - Cum%[a])
rebase[a][c] = Incremental[a+c+1] / SUM_{k>a} Incremental[k]
```

These are identical because the increments telescope to exactly 1.0 per (class, treaty)
group — verified: `Incremental` sums to `1.000000000000`, and at ages 0, 3 and 7 the
re-based vector matches the engine's matrix row with `max|diff| = 0.00e+00`.

Two consequences, both load-bearing:

* The LIC path is **already internally consistent**. Re-basing is the right operation, and
  it agrees with what the engine does today.
* **The derived pattern is a PATH-SPECIFIC no-op, and the asymmetry is the feature.**
  Measured on the built implementation:

  | Path | Supplying the derived pattern |
  |---|---|
  | LIC (`additional_matrix`, `FutureCF`, `Discounting Impact`) | **exact no-op** — matrix identical to `2.22e-16`, `Discounting Impact` unchanged |
  | LRC (`avg_df` → `GMM LRC_Discounted_CY`) | **−0.499%** — deliberate; `avg_df` was holding a conditional average, not a from-inception pattern |

  An earlier draft of this plan stated the no-op as "reproduces the base run", full stop.
  That was wrong and would have made the LRC correction — the actual point of the feature —
  look like a defect. The LIC half is the regression check; the LRC half is the deliverable.

  (Knock-on: `LC Discounted_CY` moves −41.96%. `LC = max(GMM LRC − PAA_LRC, 0)` is a threshold
  residual, so a sub-1% LRC move is amplified enormously. Expected — the same convexity WP4
  found — not a bug.)

Re-basing therefore matters only when the supplied pattern **differs** from the derived one.

### 1.3b Acceptance map — measured with a genuinely different pattern

A first attempt at this map was **wrong**, and the way it was wrong is worth recording: an
analytic check that swapped only `avg_df` concluded the override "moves the discounted LRC
only". It missed the LIC path entirely, because a prototype that touched only
`additional_matrix` used the *derived* pattern — a provable no-op (§1.3). Neither probe alone
could see the whole picture.

Re-measured with a deliberately different pattern (geometric decay, 0.85^k, applied to both
consumers):

| Measure | base | override | delta |
|---|---:|---:|---:|
| `IBNR` | 117,385,053 | 117,385,053 | **—** |
| `ULAE` | 9,883,771 | 9,883,771 | **—** |
| `RA (OS)` / `RA (IBNR)` | 3,189,354 / 4,221,445 | unchanged | **—** |
| `Future CF` | 205,442,901 | 205,442,901 | **—** |
| `Discounting Impact` | −7,397,885 | −15,949,685 | **−115.598%** |
| `Change in Discounting Impact` | −384,266 | −1,032,709 | **−168.748%** |
| `PAA_LRC` | 442,956,700 | 442,956,700 | **—** |
| `GMM LRC_Undiscounted` | 339,205,368 | 339,205,368 | **—** |
| `GMM LRC_Discounted_CY` | 323,146,549 | 307,137,076 | **−4.954%** |

**A pattern override moves both paths, and the LIC move is much the larger.** The structural
zeros are the checks that matter: `IBNR`, `ULAE`, `RA`, `Future CF`, `PAA_LRC` and
`GMM LRC_Undiscounted` must be unchanged under *any* pattern, because the pattern only
redistributes cash flows in time and sums to 1. An implementation that moved any of them has
entered at the wrong place.

Blast radius, stated plainly: `Discounting Impact` flows into the IFRS 17 movement
disclosure, so a pattern override reaches the disclosure — a larger reach than a first read
of this feature suggests.

### 1.4 A class-level grain is sufficient — RI needs no separate pattern

`Paid CDF` is merged from **gross rows only**, on `(Accident_Period, RESERVINGCLASS)` without
`GROSS/RI`. Both treaty rows therefore inherit the same CDF, the same `Cumulative %`, the same
`Incremental`, and the same pattern — **by construction, not by coincidence**. Verified: gross
and RI patterns are identical for all 12 classes (`max|diff| = 0.0000`).

So the input grain is `(RESERVINGCLASS, development period)`. No treaty axis. This matches the
existing sheet shape and keeps the editor simple.

---

## 2. Design

### 2.1 The user supplies a from-inception pattern

One vector per reserving class, indexed by development period from **inception** (period 0 =
the quarter the claim is incurred), summing to 1. That is the object an actuary means by "a
payment pattern", and §1.2 shows it is the object the run-off needs.

### 2.2 Two consumers, two applications

| Consumer | Application |
|---|---|
| **LRC run-off** (`avg_df`) | Used **directly**, position for position. The run-off convolution already expects a from-inception pattern |
| **LIC cash flows** (`additional_matrix`) | **Re-based per row.** A cohort at `Age = a` has already developed through period `a`, so its future is `pattern[a+1:]` renormalised to sum 1 |

The re-basing is not optional: applying an inception pattern directly to an aged row would
restart its development and double-count what it has already paid. §1.3 proves that re-basing
is also the operation the engine already performs, so the two paths stay consistent — and that
supplying the derived pattern must therefore be a no-op.

```python
def rebase(pattern: np.ndarray, age: int) -> np.ndarray:
    """Future-conditional pattern for a cohort that has developed through `age`."""
    tail = pattern[age + 1:]
    total = tail.sum()
    out = np.zeros_like(pattern)
    if total > 0:
        out[: len(tail)] = tail / total
    else:
        out[0] = 1.0          # fully developed: anything left pays immediately
    return out
```

`Age` runs newest-first (`calculate_sequence` sorts descending), so `Age = a` means the cohort
has `a` completed development periods — consistent with the engine's own
`incr_by_key` lookup at `age + c + 1`.

### 2.3 The default is NOT changed

Feeding the from-inception pattern by default would be defensible actuarially and would move
`GMM LRC_Discounted_CY` by −0.5%. **It is not done in this work package.** Same discipline as
WP2: ship the mechanism with a bit-identical default, surface the finding, and let the client
adopt the change deliberately with the impact in front of them.

`override=None` → `avg_df` and `additional_matrix` are computed exactly as today.

### 2.4 "Derive from experience" — the bridge

The editor offers a one-click seed that computes the from-inception pattern from the job's own
data (`Incremental` indexed by `Age`, per class, renormalised). This is what makes the feature
usable rather than a blank grid: the actuary sees the correct object, compares it against the
engine's current curve side by side, and adopts or edits it.

The derived vector is exactly the one measured in §1.1, so the plan's numbers and the product's
numbers are the same numbers.

### 2.5 The `Payment Pattern` sheet stays the pattern actually used

No rename, no second sheet. Today the sheet holds the conditional average because that is what
drives the run-off; with an override it holds the override, because that is what drives the
run-off. The sheet's contract — "the pattern this run used" — is unchanged and stays true.

### 2.6 Modes and validation

A pattern is a **shape**. Supplied vectors are normalised to sum 1 by default:

| Mode | Behaviour |
|---|---|
| `shape_only` (**default**) | Renormalise to 1.0. A user entering `20/30/30/20` gets exactly that intent |
| `strict` | Reject the run if any class's vector does not already sum to 1.0 ± 1e-6 |

Hard rejections in both modes: a vector summing to 0, a vector longer than the development
horizon (truncate + warn), all-blank rows. Negative entries are **permitted** — recoveries
genuinely produce negative increments — but flagged, since a negative early period usually
signals a data error rather than intent.

Unmatched class keys produce a warning naming them, never a silent no-op.

### 2.7 Storage: long in the database, wide in Excel

Actuaries read a pattern wide (one column per development period, unbounded N); a wide table is
a poor relational schema. Templates and imports are **wide**; the row model is **long**; the
engine adapter re-pivots. The unpivot lives in `excel_import` and the pivot in `engine_adapter`,
both driven from one shared column spec so they cannot drift.

```python
class PaymentPatternRow(_BaseRow):
    reserving_class = CharField(128, db_index=True)
    dev_period      = IntegerField()          # 0-based, from inception
    weight          = DecimalField(18, 10)
    # unique (dataset, reserving_class, dev_period)
```

New `Dataset.Kind.PAYMENT_PATTERN`.

---

## 3. Backend changes

| File | Change |
|---|---|
| `module2_engine/pattern_override.py` | **new** — `PatternOverride`, `rebase`, `normalise`, `validate`, `OverrideReport` |
| `module2_engine/engine.py` | `_compute_allocate_frames` accepts `pattern_override`; applies to `additional_matrix` (re-based per row) and assigns `avg_df` directly; threads through allocate / process / movement |
| `datasets/models.py` | `PaymentPatternRow`, `Kind.PAYMENT_PATTERN` + migration |
| `datasets/services/columns.py` | wide column spec + required fields |
| `datasets/services/excel_import.py` | wide → long unpivot |
| `datasets/services/engine_adapter.py` | long → wide pivot; `KIND_RECIPE` entry |
| `datasets/services/templates.py` | dynamic-width template (period count from the source job) |
| `processing/views.py` | allocate / process job views accept `payment_pattern_dataset_id` + `pattern_mode`; new `Module2PatternPreviewView` returning both the engine curve and the derived from-inception curve for seeding |
| `processing/tasks.py` | materialise the dataset, build the override, persist `override_report` |
| `core/exceptions.py` | `PatternValidationError` → structured 422 |

## 4. Frontend changes

| File | Change |
|---|---|
| `src/components/PaymentPatternEditor.tsx` | **new** — one row per class, one editable column per development period, live row total, cumulative sparkline, "Derive from experience", "Normalise" |
| `src/pages/IbnrAllocationPage.tsx` | optional step between allocate and ULR selection |
| `src/api/module2.ts` | override payload, `PatternPreviewDto`, `OverrideReportDto` |
| `src/state/wizards/ibnr.ts` | persist the selection and mode |
| `src/pages/DataHubPage.tsx` | the new dataset kind |

**The editor shows three curves at once**: the engine's current pattern, the derived
from-inception pattern, and the user's edit. A pattern is judged as a *shape*, so the
sparkline is not decoration — twenty numbers in a row are unreadable as a curve.

---

## 5. Bit-identity and goldens

* No override → **value-identical**. Existing `m2_allocate_ref` / `m2_process_ref` goldens pass
  untouched, and this is the gating assertion for the whole work package.
* **Structural invariants under ANY pattern** (§1.3b): `IBNR`, `ULAE`, `RA (OS)`, `RA (IBNR)`,
  `Future CF`, `PAA_LRC` and `GMM LRC_Undiscounted` must be unchanged. The pattern only
  redistributes cash flows in time and sums to 1, so anything else moving means the override
  entered at the wrong place. These are the checks that catch a mis-wired implementation.
* **The path-specific no-op**: the derived from-inception pattern must leave the LIC path
  identical (`additional_matrix` to 1e-12, `Discounting Impact` unchanged) while moving
  `GMM LRC_Discounted_CY` by −0.499%.
* New goldens from a synthetic fixture: pattern override in each mode.

## 6. Tests

**`module2_engine/tests/test_pattern_override.py`** (new)
* `rebase` at `Age = 0` returns the pattern from period 1 renormalised
* `rebase` past the end of the pattern returns `[1, 0, 0, …]` — a fully developed cohort
* `rebase` preserves sum 1 for every age in the reference horizon
* an override changes `avg_df` to exactly the supplied vector
* an override changes `additional_matrix` for both GROSS and RI rows
* **the §1.3b acceptance map**, asserted measure by measure: `IBNR`, `ULAE`, `RA (OS)`,
  `RA (IBNR)`, `Future CF`, `PAA_LRC`, `GMM LRC_Undiscounted` unchanged; `Discounting Impact`,
  `Change in Discounting Impact` and `GMM LRC_Discounted_CY` all move
* **the path-specific no-op** (§1.3): the derived from-inception pattern leaves
  `additional_matrix` and `Discounting Impact` untouched while moving `GMM LRC_Discounted_CY`
  by −0.499%. Both halves are asserted — the LIC half catches a mis-wired matrix, the LRC half
  proves the correction the client asked for actually lands
* an override that changes only `avg_df` must NOT move `Discounting Impact`; one that changes
  only `additional_matrix` must NOT move `GMM LRC_Discounted_CY`. Wiring one path and forgetting
  the other is the most likely implementation error, and these two tests are what catch it
* the derived pattern applied to the LRC path alone reproduces the −0.499% of §1.2
* `shape_only` renormalises `20/30/30/20`; `strict` rejects it
* zero-sum vector rejected, no divide-by-zero
* pattern longer than the horizon truncates + warns; shorter zero-fills + warns
* unmatched class appears in `report.unmatched`, never silently ignored
* negative entries permitted and flagged

**`datasets/tests/test_payment_pattern_io.py`** (new)
* wide → long → wide round-trip lossless
* template width derives from the source job's development horizon
* duplicate `(class, dev_period)` rejected with the row number

**`processing/tests/test_pattern_api.py`** (new)
* preview endpoint returns both the engine curve and the derived curve
* override persisted to `input_meta`; `override_report` persisted on success
* `strict` failure surfaces a 422 naming the offending classes, not a 500

## 7. Edge cases

* **Class in the override but absent from the data** → warning, not error.
* **Class in the data but absent from the override** → keeps the engine-derived pattern; mixed
  runs are legitimate and must not be silently all-or-nothing.
* **`Expected Unpaid % == 0`** rows currently get `[1, 0, 0, …]`; an override must not resurrect
  development on a fully-developed row. Preserved and asserted.
* **RI rows** take the class pattern (§1.4). The `Payment Pattern` sheet stays gross-only so the
  sheet contract is unchanged.
* **Development horizon** is set by the source job (26 periods on the reference book). A longer
  supplied pattern is truncated with a warning rather than silently dropping value.
* **Movement disclosure**: any override changes `Discounting Impact`, which flows into the
  movement workbook. The movement job records that its source allocate run carried an override,
  so a disclosure is never produced from overridden figures without that being visible.

## 8. Estimate

| | |
|---|---|
| override engine (`rebase`, validate, apply, report) | 2d |
| dataset kind, import/adapter/template | 2d |
| preview endpoint + job wiring | 1d |
| frontend editor (3-curve view, derive, normalise) | 3d |
| tests | 2d |
| goldens + validation | 0.5d |
| **Total** | **~10.5 days** |

## 9. What this does NOT cover

Requirement 3 (cash-flow override) stays in `CASHFLOW_OVERRIDE_PLAN.md`. The two are
not symmetric: a cash-flow override targets the LIC path at row grain and must reconcile to
`IBNR + ULAE + Outstanding + SS`, whereas a pattern is a class-level shape that reconciles by
construction. Building them together would have hidden that difference.
