# WP4 — Sensitivity / Stress Testing

> **Goal:** Run the reserving model under a named, saved set of parameter shocks — risk adjustment,
> discount curve, loss ratio — and present base-versus-shock deltas as a reviewable, reproducible
> disclosure artefact.

Status: **implemented** (2026-08-21). Requirement 1.
Decisions: `docs/CLIENT_REQUIREMENTS_DECISIONS.md` §3 D1.

Every unit, insertion point, propagation path and timing below was **measured**, not inferred. The
measurements are reproduced inline so a reviewer can re-run them.

---

## Implementation status (2026-08-21)

Delivered. Everything in this plan was built and verified against the client reference
book; the sections below are the as-built design, not a proposal.

**Backend**

| File | State |
|---|---|
| `module2_engine/scenarios.py` | new — `ScenarioShock`, `MEASURES`, `extract_measures`, `build_comparison`, `build_tornado`, `run_sensitivity` |
| `module2_engine/workbook_sensitivity.py` | new — 6-sheet `Sensitivity_Analysis.xlsx` |
| `module2_engine/engine.py` | `_compute_allocate_frames` extracted; `_write_allocate_workbook` split out; `shock` threaded through allocate + process; `ulr_shock` on `_apply_selected_ulr`; `LRC_COMPONENTS` / `create_lrc_table` / `create_lic_table` lifted to module level; stale return annotation corrected |
| `tenants/models.py` | `ScenarioSet`, `Scenario` (+ migration `0003`) |
| `tenants/{serializers,views,urls}.py` | scenario-set CRUD; PUT forks a version |
| `tenants/management/commands/seed_scenarios.py` | new — the 12-scenario default ladder |
| `processing/models.py` | `JobType.MODULE2_SENSITIVITY`, `JobDraft.Key.SENSITIVITY` (+ migrations `0006`, `0007`) |
| `processing/{views,urls,tasks,utils}.py` | job view, result endpoint, Celery task, staging dirs; `_normalize_module2_error` allowlist extended for configuration errors |
| `accounts/.../seed_rbac.py` | `scenarios.view` / `scenarios.manage` seeded to the roles that already hold `module2.run` |

**Frontend**

| File | State |
|---|---|
| `src/api/sensitivity.ts` | new — unit conversion helpers, scenario-set + run API |
| `src/api/sensitivity.test.ts` | new — 10 tests locking the lever-unit conversions |
| `src/components/SensitivityMatrix.tsx` | new — absolute-default matrix, em-dash structural zeros |
| `src/components/ScenarioSetEditor.tsx` | new — unit-aware editor with `base → shocked` echo |
| `src/components/TornadoChart.tsx` | new — ranked on absolute |
| `src/pages/SensitivityPage.tsx` | new — 3-step flow, lands on Comparison |
| `src/state/wizards/sensitivity.ts` | new wizard slice |
| `src/App.tsx`, `AppSidebar.tsx`, `api/{module1,module2,processing,jobDrafts}.ts` | route, nav, job-type labels, `DraftKey` |

**Verification**

* 225 Django tests pass (2 pre-existing Redis-broker failures unrelated to this work,
  confirmed identical on stashed code); 139 engine tests pass; 87 frontend tests pass;
  `vite build` succeeds; `tsc` error count unchanged at its 45-error baseline.
* The refactor is **value-identical**: old vs new `_build_allocate_outputs` produce
  frame-for-frame equal output with and without ULR selections.
* The §1.3 propagation table is locked as a regression test at 1e-6 relative tolerance.
* Two end-to-end tests run the real Celery task body against
  `benchmarks/fixtures/m2_allocate_ref/`.

**Measured, as built**

* allocate compute ≈ 0.33 s/pass vs 3.30 s with the workbook write — the write-free
  scenario path delivers the predicted ~10x.
* 10 scenarios + base = 3.60 s end to end. The default 12-scenario ladder ≈ 4.3 s.
* Process scope is ≈ 3.8 s/pass and **cannot** get the 10x: `_process_intermediates`
  deliberately round-trips xlsx for dtype normalisation (documented in the engine), so
  a workbook is written per pass. 13 scenarios ≈ 50 s — acceptable, but stated here so
  nobody later "optimises" that round-trip away and breaks the golden net.

---

## 0. Client requirement

> "Stress testing /sensitivity of Risk adjustment, please to give Risk adjustment % (+/-% i.e.
> increase by %, decrease by %), discounting (5 basis point +/-) and Loss ratios (5% Loss ratio +/-)"

---

## 1. Verified ground truth

### 1.1 Units — all three levers are stored as fractions

Read from `benchmarks/fixtures/m2_allocate_ref/Combined_Summary.xlsx`:

| Sheet | Column | Observed | Meaning |
|---|---|---|---|
| `Discount Rate` | `CY Discount` | `0.0608, 0.0561, 0.0535, …` (30 annual bands) | annual spot **fraction**, 6.08% |
| `Discount Rate` | `PY Discount` | `0.0579, 0.0509, …` | prior-period locked-in curve |
| `ULAE-RA` | `RA %` | `0.0463`, range `0.0108`–`0.0463` | **fraction**, 4.63% |
| `ULAE-RA` | `ULAE %` | range `0.0`–`0.15` | fraction |
| `UW Summary` | `Exp Ratio`, `RI %` | `0.0776`, `0.75` | fractions |
| `Loss Ratio` | `Selected ULR` | ~`0.53` average | fraction |

**Therefore:**

```
RA   +10% relative  ->  ra' = ra * 1.10          0.0463 -> 0.05093
Disc +5 bp absolute ->  r'  = r + 0.0005         0.0608 -> 0.0613
ULR  +5 pp absolute ->  ulr' = ulr + 0.05        0.65   -> 0.70
```

An absolute +10 *points* on RA would take 4.63% to 14.63% — three times the loading. Relative is
the only sane reading of the client's "+/-%", and the data confirms it.

### 1.2 The three insertion points, and that each is sufficient

Traced every consumer in `module2_engine/engine.py`:

| Lever | Insert at | Consumers all downstream of it |
|---|---|---|
| **RA** | `ulae_ra_combined` immediately after construction (`:246-249`) | `:281` merge into `merged_df` → `ULAE`, `RA (OS)`, `RA (IBNR)`; `:355-357` `ulae_ra_combined_gross` merge into `uw_summary` → `Combined Ratio` |
| **Discount** | `discount_rate_combined` before `calculate_discount_rates` (`:250-262`) | `:322-326` `Discounted CF CY/PY`; `:403-404` `cy_disc`/`py_disc` in the run-off loop; `:517-519` the `CY-PY Discount` sheet |
| **ULR** | the `Selected ULR` **column**, inside `_apply_selected_ulr`, before `Combined Ratio` | `Loss Ratio` sheet; `Combined Ratio` → run-off → `GMM LRC_*`, `LC *`, `Loss Recovery Component` |

**One insertion point per lever covers every consumer.** RA in particular is read twice from the
same frame — shocking at construction reaches both.

**The ULR subtlety.** `_apply_selected_ulr` assigns `Selected ULR = Ult LR` when `selected_ulr_rows`
is empty. Shocking the *input list* would therefore be a silent no-op for any run without explicit
selections. The shock must be applied to the resulting **column**, after the selection logic and
before `Combined Ratio`, so it behaves identically whether or not the user made selections:

```python
def _apply_selected_ulr(uw_summary, selected_ulr_rows, *, ulr_shock: float | None = None):
    ...                                        # existing selection logic, unchanged
    if ulr_shock:
        uw_summary["Selected ULR"] = (uw_summary["Selected ULR"] + ulr_shock).clip(lower=0.0)
    uw_summary["Combined Ratio"] = ...          # unchanged
```

### 1.3 Propagation map — measured, not assumed

Ran each shock against the reference book and diffed every measure. **This table is the acceptance
specification**; the tests assert it.

| Measure | Base | RA +10% | Disc +5bp | ULR +5pp |
|---|---:|---:|---:|---:|
| `IBNR` | 117,385,053 | – | – | – |
| `ULAE` | 9,883,771 | – | – | – |
| `RA (OS)` | 3,189,354 | **+10.000%** | – | – |
| `RA (IBNR)` | 4,221,445 | **+10.000%** | – | – |
| `Future CF` | 205,442,901 | – | – | – |
| `Discounting Impact` | −7,397,885 | – | **−0.785%** | – |
| `Change in Discounting Impact` | −384,266 | – | **−15.108%** | – |
| `PAA_LRC` | 442,956,700 | – | – | – |
| `GMM LRC_Undiscounted` | 339,205,368 | +0.280% | – | **+6.762%** |
| `GMM LRC_Discounted_CY` | 323,146,549 | +0.280% | −0.039% | **+6.765%** |
| `GMM LRC_Discounted_PY` | 323,989,433 | +0.280% | **–** | +6.765% |
| `LC Discounted_CY` | 632,770 | +6.825% | −0.670% | **+145.077%** |
| `Loss Recovery Component` | 474,578 | +6.825% | −0.670% | **+145.077%** |
| `Selected ULR` | — | – | – | +9.407% |
| `Combined Ratio` | — | +0.288% | – | +7.590% |

Four findings that shape the product:

1. **`RA +10%` moves `RA (OS)` / `RA (IBNR)` by exactly +10.000%** — the relative convention behaves
   as specified, end to end.
2. **`GMM LRC_Discounted_PY` is untouched by a discount shock** — empirical proof that shocking the
   CY curve only leaves the prior-period comparative intact. This is the single most important
   correctness property of the feature and it holds.
3. **`Change in Discounting Impact` moves −15.1% for 5 basis points.** It is a small difference of
   two large discounted totals, so it is violently sensitive. This is very likely *why* the client
   asked for 5bp specifically.
4. **`LC Discounted_CY` moves +145% for +5pp ULR** — `LC = max(GMM LRC − PAA_LRC, 0)` is a threshold
   residual, so it is extremely convex near the onerous-contract trigger. **But +145% of 632,770 is
   ~+918k, against +21.9m on `GMM LRC` at +6.765%.** A percent-only view would rank `LC` as the most
   sensitive measure in the book when in absolute terms it is far from it. **The UI must therefore
   present absolute and percent side by side, and the tornado must default to absolute.** This is a
   design requirement derived from the data, not a preference.

Measures with no response are **shown as zero, never hidden** — a structural zero tells the reader
which lever bites where, which is itself disclosure content.

### 1.4 Performance — and the 10x that changes the design

Measured on the reference book (12 reserving classes, 83 class×UWY rows, `MainSheet` 2,476×45):

```
_build_allocate_outputs (full)     3.30 s
  of which: workbook write         2.97 s        <-- 90%
  actual computation             ~ 0.33 s
run_module2_process (full)         6.93 s
```

**A scenario needs measures, not a workbook.** Skipping the per-scenario write takes a 7-scenario
allocate-scope run from ~23s to **~2.3s**, and makes process-scope viable too.

Consequently: **no Celery fan-out.** My earlier draft proposed fanning out above 12 scenarios; the
measurement makes that unnecessary — 20 scenarios of compute is ~7s. Sequential execution in one
task, with progress, is simpler and faster than coordination. One workbook is written at the end.

### 1.5 Determinism

`_build_allocate_outputs` called twice on identical input produces **different bytes** but
**value-identical frames** (verified). XlsxWriter embeds nondeterministic metadata. So "bit-identical"
in this repo means *value-identical at DataFrame level*, which is exactly what `processing/golden.py`
compares. Every regression assertion in this plan is at frame level; no test compares workbook bytes.

---

## 2. Design

### 2.1 Split compute from write

The enabling refactor, and the only change to existing code paths:

```python
# module2_engine/engine.py
def _compute_allocate_frames(combined_summary_bytes, selected_ulr_rows=None, *,
                             shock=None) -> tuple[dict[str, pd.DataFrame], list[dict]]:
    """Everything _build_allocate_outputs does, up to and excluding the write."""

def _build_allocate_outputs(combined_summary_bytes, selected_ulr_rows=None, *, shock=None):
    sheets, ulr_rows = _compute_allocate_frames(combined_summary_bytes, selected_ulr_rows, shock=shock)
    ... existing write ...
    return out.getvalue(), ulr_rows, sheets        # signature unchanged
```

Pure extraction — no logic moves, no behaviour changes. Existing callers are untouched, which is what
keeps the goldens green.

Same treatment on the process side: `_process_intermediates` is *already* the pure half; only
`create_lrc_table` / `create_lic_table` need lifting from nested functions to module level so
sensitivity can build the LIC/LRC measures without writing a workbook.

**Incidental fix:** `_build_allocate_outputs`'s annotation says `-> tuple[bytes, list[...]]` but it
returns a 3-tuple. Corrected while in the file.

### 2.2 `ScenarioShock`

```python
# module2_engine/scenarios.py  (new)
@dataclass(frozen=True)
class ScenarioShock:
    label: str
    lever: str                    # "ra" | "discount" | "ulr"
    magnitude: float              # ra: relative (0.10) | discount: bp (5) | ulr: pp (0.05)
    scope_classes: tuple[str, ...] = ()      # empty = all classes

    def apply_ra(self, ulae_ra: pd.DataFrame) -> pd.DataFrame
    def apply_discount(self, discount: pd.DataFrame) -> pd.DataFrame   # CY column only
    def ulr_delta(self) -> float | None
```

Class scoping uses WP0's `canonical_key`, so a scope entry matches regardless of spelling.

`shock=None` threads through as a no-op with **no branch inside any loop** — bit-identity is
structural, not tested into existence.

### 2.3 Two scopes, reusing machinery that already exists

| Scope | Needs | Adds |
|---|---|---|
| `allocate` (default) | `Combined_Summary.xlsx` only | the 13 measures of §1.3 |
| `process` | + `Previous_Period.xlsx`, `Expense_CF.xlsx` | `GROSS LIC`, `Gross LRC` (EOP) |

`process` scope requires no new input plumbing. `Module1Job.input_archive` +
`read_input_archive_bytes` + `_read_or_inherit_input` already let the **movement** job chain off a
process job and inherit its inputs. Sensitivity chains the same way: pick a completed process job,
inherit `Previous_Period` and `Expense_CF`, inherit `Combined_Summary` from its allocate ancestor.
The user re-supplies nothing.

**LIC BOP and LRC BOP must not move under any shock** — they are prior-period given data. Asserted.

### 2.4 Named, versioned scenario sets

A sensitivity disclosure is only meaningful if the same shocks recur period after period.

```python
# tenants/models.py
class ScenarioSet(models.Model):
    organization, name, description, version, is_active, created_by, created_at
    # unique (organization, name, version); one active per (org, name)

class Scenario(models.Model):
    scenario_set  = FK(ScenarioSet, related_name="scenarios")
    label         = CharField(64)      # "RA +10%"
    lever         = CharField(choices=ra|discount|ulr)
    magnitude     = DecimalField(12, 6)
    scope_classes = JSONField(default=list)
    order         = IntegerField()
```

Seeded default set — exactly the client's request, plus the wider ladder D1 calls for:

```
RA −25%, RA −10%, RA +10%, RA +25%
Discount −5bp, −25bp, +5bp, +25bp
ULR −5pp, +5pp, −10pp, +10pp
```

12 scenarios + base = 13 compute passes ≈ **4.3s**. Editing an active set forks a version; every run
snapshots the resolved scenario list into `input_meta["scenarios"]`, so a job replays even if the set
is later changed or deleted.

### 2.5 Output

New job type `module2_sensitivity` → `Sensitivity_Analysis.xlsx`:

| Sheet | Content |
|---|---|
| `Scenario Definitions` | label, lever, magnitude **with unit**, scope, and the resolved `base → shocked` values |
| `Base` | measure set, per class and total |
| `Comparison — Absolute` | measure × scenario, absolute deltas |
| `Comparison — Percent` | measure × scenario, relative deltas |
| `Tornado` | measures ranked by **absolute** sensitivity |
| `<label>` × N | each scenario's full measure set |

`MEASURES` is a module-level constant shared by the workbook writer and the UI, so the two can never
disagree about what "the sensitivity table" contains.

### 2.6 Presentation

Per the preview-first directive, results render in-app before any download, landing on **Comparison**
— the deltas are the artefact, not the base.

* absolute / percent toggle, **absolute default** (§1.3 finding 4)
* diverging red/green centred on zero, sign always printed (WP7 `data-cell`)
* class filter; total row pinned
* tornado per measure, absolute by default
* structural zeros rendered `–`, visibly distinct from a computed 0.00%

---

## 3. Backend changes

| File | Change |
|---|---|
| `module2_engine/engine.py` | extract `_compute_allocate_frames`; `shock` kwarg; `ulr_shock` on `_apply_selected_ulr`; lift `create_lrc_table` / `create_lic_table` to module level; fix the stale return annotation |
| `module2_engine/scenarios.py` | **new** — `ScenarioShock`, `MEASURES`, `extract_measures`, `build_comparison`, `run_sensitivity` |
| `module2_engine/workbook_sensitivity.py` | **new** — renders the workbook (openpyxl: multi-table sheets) |
| `processing/models.py` | `JobType.MODULE2_SENSITIVITY` + migration |
| `processing/views.py` | `Module2SensitivityJobView`, `Module2SensitivityResultView` (JSON matrix) |
| `processing/urls.py` | routes |
| `processing/tasks.py` | `run_module2_sensitivity_task`, sequential with per-scenario progress |
| `tenants/models.py`, `serializers.py`, `views.py`, `urls.py` | `ScenarioSet` / `Scenario` CRUD + activate |
| `tenants/management/commands/seed_scenarios.py` | **new** — the default set |
| `accounts/.../seed_rbac.py` | `scenarios.manage` (Actuary+); running reuses `module2.run` |

## 4. Frontend changes

| File | Change |
|---|---|
| `src/pages/SensitivityPage.tsx` | **new** — source job → scenario set → scope → run → review |
| `src/components/ScenarioSetEditor.tsx` | **new** — unit-aware magnitude input with live `base → shocked` echo |
| `src/components/SensitivityMatrix.tsx` | **new** — measure × scenario, absolute/percent toggle, diverging shading |
| `src/components/TornadoChart.tsx` | **new** — per the `dataviz` conventions |
| `src/api/sensitivity.ts` | **new** |
| `src/state/wizards/sensitivity.ts` | **new** slice; `JobDraft.Key.SENSITIVITY` |
| `src/App.tsx`, `AppSidebar.tsx` | route + nav behind `module2.run` |

**Unit-aware input is a correctness control, not polish.** The field renders "RA ±% (relative)" and
"Discount ±bp (absolute)" and echoes the resolved value — `RA 4.63% → 5.09%`. The single most likely
failure of this feature is a user typing `5` meaning 5bp into a field that means 5%.

## 5. Bit-identity and goldens

* `shock=None` is a no-op by construction; existing `m2_allocate_ref` / `m2_process_ref` goldens are
  the gate and are **not** re-captured.
* `_compute_allocate_frames` + the existing write must produce frames value-identical to today's
  `_build_allocate_outputs` — asserted directly on the reference book.
* A sensitivity run's `Base` sheet must equal a plain allocate run measure for measure.
* New golden `m2_sensitivity_ref` from `m2_allocate_ref` with the seeded set.

## 6. Tests

**`module2_engine/tests/test_scenarios.py`** (new)
* unit semantics: `ra +10%` on `0.0463` → `0.05093`; `disc +5bp` on `0.0608` → `0.0613`;
  `ulr +5pp` on `0.65` → `0.70`
* **the §1.3 propagation table**, asserted measure by measure to 1e-9 — including every `–`
* `RA (OS)` and `RA (IBNR)` move by exactly the relative magnitude
* `GMM LRC_Discounted_PY` **unchanged** under a discount shock (the CY-only guarantee)
* RA moves both the RA balances **and** `Combined Ratio` — the double-count is asserted, not accidental
* **ULR shock with `selected_ulr_rows=[]`** shifts `Selected ULR` off `Ult LR` — the §1.2 subtlety
* ULR shock with explicit selections shifts those selections
* class-scoped shock leaves out-of-scope classes value-identical to base
* negative shocks are exact inverses **in the parameter**; asserted non-linear in the output
* zero-magnitude scenario equals base
* `shock=None` frames equal no-shock frames
* LIC BOP / LRC BOP unchanged under every shock (process scope)

**`module2_engine/tests/test_engine_helpers.py`**
* `_compute_allocate_frames` + write ≡ today's `_build_allocate_outputs`, frame by frame

**`processing/tests/test_sensitivity_api.py`** (new)
* Module 1 never invoked
* process scope inherits `Previous_Period` / `Expense_CF` from a chained process job
* chaining a job with no `input_archive` → actionable 400, matching the movement job's message
* scenario set snapshotted; later edits do not alter the stored run
* result endpoint matrix matches the workbook comparison sheets cell for cell
* 13-scenario run completes within the job SLO

## 7. Edge cases

* **`Selected ULR` shocked below zero** → clipped at 0, warned. A negative loss ratio is meaningless.
* **Discount shocked negative** → permitted (negative rates are real); `(1+r)^(1/4)` asserted safe.
  Rate ≤ −100% rejected at input.
* **`RA % == 0`** (present in the book — `ULAE %` reaches 0.0) → a relative shock leaves it 0. Surfaced
  explicitly so "no sensitivity" is not misread as "no exposure"; it means the parameter was never set.
* **`LC` at the threshold** — `max(x, 0)` means a downward shock can pin `LC` to exactly 0 while a
  smaller shock moves it. Non-monotonic-looking output that is correct; the UI annotates a pinned zero.
* **Scoped class absent from data** → warning, not error.
* **Base run fails** → whole job fails; no partial artefact is ever emitted.
* **Chained from a process job** — sensitivity re-runs allocate internally with that job's
  `selected_ulr_rows`; a ULR shock moves the *selected* value, not `Ult LR`. Stated on screen.
* **Discount curve shorter than the cash-flow horizon** — the existing `cy_disc.get(i)` returns
  `None` → contributes 0. Pre-existing; surfaced as a warning rather than changed under this WP.

## 8. Estimate

| | |
|---|---|
| compute/write split + `ulr_shock` + parity proof | 1.5d |
| `ScenarioShock` + measures + comparison | 2d |
| sensitivity workbook | 1.5d |
| scenario set models, API, seed | 1.5d |
| job type, task, endpoints | 1.5d |
| frontend (page, editor, matrix, tornado) | 5d |
| tests incl. the propagation table | 3d |
| goldens + validation | 1d |
| **Total** | **~17 days** |

Two days above the pre-verification estimate: the compute/write split and the `ulr_shock` correction
were not in the original scope and are both load-bearing.
