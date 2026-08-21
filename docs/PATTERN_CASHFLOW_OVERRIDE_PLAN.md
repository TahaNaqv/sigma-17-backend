# WP3 — Payment Pattern & Future Cash Flow Overrides

> **Goal:** Let the actuary supply their own payment pattern or future cash flows — by Excel upload
> or in-app grid — in place of the chain-ladder-implied ones, with explicit reach, explicit
> precedence, and a reconciliation gate that prevents a supplied override from silently breaking the
> LIC roll-forward.

Status: planned (2026-08-21). Decisions: `docs/CLIENT_REQUIREMENTS_DECISIONS.md` §3 D2.
Requirements 2 and 3. Depends on WP0.

---

## 0. Client requirements

> "payment pattern calculation already being done, need a place as an excel input if want to use
> different pattern (similar to where LDFS are calculated and a space to select LDFs)"

> "cashflow calculation already being done, need a place as an excel input if want to use different
> cash flows"

The parenthetical is the design brief: mirror the Update Reserve LDF surface — show what the engine
computed, let the user override it, keep preview equal to output.

## 1. How it works today

### 1.1 Payment pattern

`_build_allocate_outputs` (`module2_engine/engine.py`):

```python
gross_only  = merged_df[merged_df["GROSS/RI"] == "GROSS"]
sum_columns = gross_only.groupby("RESERVINGCLASS")[dynamic_columns].sum()
avg_df      = sum_columns.div(sum_columns.sum(axis=1), axis=0).reset_index()
```

Written as the `Payment Pattern` sheet. Per reserving class, one weight per development quarter,
summing to 1. Derived from `Cumulative % = 1 / Paid CDF` — it is chain-ladder-implied, not observed.

Consumed in the run-off loop as `avg_by_rc.loc[reserving_class, period_int]`, driving `PAA_LRC`,
`GMM LRC_*` and therefore `LC` and the Loss Recovery Component.

### 1.2 Future cash flow

```python
merged_df["Future CF"] = IBNR + ULAE + Outstanding + SS
future_cf_df[col]      = merged_df["Future CF"] * additional_matrix[col]
```

`additional_matrix` is the **per-row** incremental payout matrix (class, UWY, accident period,
GROSS/RI). `future_cf_df` then feeds `Discounted CF CY/PY`, `Discounting Impact`,
`Change in Discounting Impact`, and everything the movement disclosure derives from them.

### 1.3 The asymmetry that matters

`avg_df` (class-level, gross-only) drives **LRC run-off**. `additional_matrix` (row-level, both
treaty types) drives **LIC cash flows**. They are computed from the same underlying `Incremental`
column but are *different objects at different grains*. An override of "the payment pattern" that
touched only `avg_df` would leave LIC developing on a different pattern — incoherent. Hence D2.

## 2. Design

### 2.1 Reach and precedence (D2, restated for implementers)

| Override | Drives | Does not drive |
|---|---|---|
| Payment pattern | `avg_df` (LRC run-off) **and** `additional_matrix` (LIC cash flows) | — |
| Future cash flow | `future_cf_df` (LIC) | LRC run-off |

Precedence: **cash flow > payment pattern > engine-derived**, applied per key. When both cover the
same key the cash flow wins and the run emits a warning naming the keys — never a silent choice.

### 2.2 Overrides are shapes, not quantums

This is the central design decision. An actuary supplying a pattern or a cash flow is expressing a
view about **timing**, not about the size of the reserve — the quantum comes from IBNR + ULAE +
Outstanding + SS, which the engine computes. Treating a supplied vector as absolute is what breaks
the LIC reconciliation.

Three modes, default first:

| Mode | Behaviour |
|---|---|
| `shape_only` (**default**) | Use the supplied vector's shape; rescale so the total equals the engine's `Future CF` (cash flow) or 1.0 (pattern). Reconciliation foots by construction |
| `strict` | Use as supplied; **fail the run** with the offending keys if the total does not reconcile to tolerance 1e-6 |
| `absolute` | Use as supplied, do not rescale, do not fail; emit a prominent warning that the LIC roll-forward will not foot. For diagnostic use only |

`shape_only` makes the common case safe and the rare case explicit. `absolute` exists because
occasionally an actuary genuinely wants to see an unreconciled figure; it must never be reachable
by accident.

### 2.3 Storage shape: long in the database, wide in Excel

A pattern is naturally wide (one column per development period, unbounded N) — that is how an
actuary reads it, and how the template must look. A wide table is a poor relational schema.

**Decision: templates and imports are wide; the row models are long; the engine adapter re-pivots to
wide.** The unpivot lives in `excel_import` and the pivot in `engine_adapter`, both driven from one
shared column-spec so they cannot drift.

```python
# datasets/models.py
class PaymentPatternRow(_BaseRow):
    reserving_class = CharField(128, db_index=True)
    dev_period      = IntegerField()               # 0-based development quarter
    weight          = DecimalField(18, 10)
    # unique (dataset, reserving_class, dev_period)

class FutureCashflowRow(_BaseRow):
    reserving_class = CharField(128, db_index=True)
    uwy             = IntegerField()
    accident_period = CharField(16)                # "2018-Q1"
    gross_ri        = CharField(16, choices=_TreatyType.choices)
    dev_period      = IntegerField()
    amount          = DecimalField(18, 2)
    # unique (dataset, reserving_class, uwy, accident_period, gross_ri, dev_period)
```

New `Dataset.Kind` members: `payment_pattern`, `future_cashflow`.

### 2.4 Engine contract

```python
def _build_allocate_outputs(
    combined_summary_bytes: bytes,
    selected_ulr_rows: list[dict] | None = None,
    *,
    pattern_override: PatternOverride | None = None,
    cashflow_override: CashflowOverride | None = None,
) -> tuple[bytes, list[dict], dict[str, pd.DataFrame], OverrideReport]:
```

`OverrideReport` carries applied keys, rescale factors, unmatched keys and warnings. It is persisted
to `input_meta["override_report"]` and surfaced in the UI — an override that silently matched nothing
is a failure mode worth naming.

Application points, in order:

1. **Pattern → `additional_matrix`**: for each `(RESERVINGCLASS)` with an override, replace that
   class's rows' incremental payout vector with the supplied shape, re-based at each row's `Age`
   (a row already 3 quarters developed consumes the pattern from period 3 onward, renormalised over
   the remaining tail). Rows of classes without an override are untouched.
2. **Pattern → `avg_df`**: replace the class row directly. Because step 1 already changed
   `additional_matrix`, recomputing `avg_df` from it would reproduce the same vector; we assign
   explicitly so the `Payment Pattern` sheet is exactly what the user supplied.
3. **Cash flow → `future_cf_df`**: replace matched rows post-multiplication, after `Future CF` is
   known (needed for `shape_only` rescaling).
4. **Reconciliation gate** per §2.2, before any workbook write.

The re-basing in step 1 is the subtle part: a supplied pattern is expressed from **inception**, but
`additional_matrix` rows are positioned at their current `Age`. Applying an inception pattern
directly to an aged row would restart its development. The plan re-bases and renormalises over the
remaining tail, and the unit tests assert exactly this.

### 2.5 UI surface — mirroring the LDF editor

Per the client's own analogy, the editor shows the engine-computed vector and lets the user override
it in place:

* **Payment Pattern** — one row per reserving class, one editable column per development quarter, a
  live row total, a cumulative sparkline, and a **Normalise** action. Pattern shape is easier to
  judge as a curve than as twenty numbers, so the sparkline is not decoration.
* **Future Cash Flow** — grouped by class, expandable to (UWY, accident period, treaty type), with
  the engine's value shown greyed behind each override and a per-key reconciliation badge.

Both offer: download template, upload filled workbook, or edit in-app — the three paths every other
dataset kind already supports.

## 3. Backend changes

| File | Change |
|---|---|
| `datasets/models.py` | `PaymentPatternRow`, `FutureCashflowRow`, two `Kind` members + migration |
| `datasets/services/columns.py` | wide column specs; `REQUIRED_FIELDS_FOR_KIND` entries |
| `datasets/services/excel_import.py` | wide → long unpivot for both kinds |
| `datasets/services/engine_adapter.py` | long → wide pivot; `KIND_RECIPE` entries |
| `datasets/services/templates.py` | dynamic-width templates (dev-period count from the source job) |
| `module2_engine/overrides.py` | **new** — `PatternOverride`, `CashflowOverride`, `OverrideReport`, re-basing, rescaling, reconciliation |
| `module2_engine/engine.py` | `_build_allocate_outputs` accepts the overrides; four application points; `run_module2_allocate` / `_process_intermediates` / `run_module2_movement` thread them through |
| `processing/views.py` | `Module2AllocateJobView` / `Module2ProcessJobView` accept dataset ids + mode; new `Module2PatternPreviewView` returning the engine-computed pattern for seeding the editor |
| `processing/tasks.py` | materialise the datasets, build the override objects, persist `override_report` |
| `core/exceptions.py` | `OverrideReconciliationError` → structured 422 |

## 4. Frontend changes

| File | Change |
|---|---|
| `src/components/PaymentPatternEditor.tsx` | **new** |
| `src/components/CashflowOverrideEditor.tsx` | **new** |
| `src/components/OverrideModeSelect.tsx` | **new** — the three modes with plain-language consequences |
| `src/pages/IbnrAllocationPage.tsx` | new optional step between allocate and ULR selection |
| `src/api/module2.ts` | override payloads, `OverrideReportDto` |
| `src/state/wizards/ibnr.ts` | persist selections and mode |
| `src/pages/DataHubPage.tsx` | the two new dataset kinds |

## 5. Bit-identity and goldens

No override supplied → **bit-identical**; existing `m2_allocate_ref` and `m2_process_ref` goldens pass
untouched. That is the gating assertion.

New goldens from synthetic fixtures: pattern-only, cash-flow-only, both (precedence), and each of the
three modes.

## 6. Tests

**`module2_engine/tests/test_overrides.py`** (new)
* pattern re-basing: a row at `Age = 3` consumes the pattern from period 3, renormalised to 1
* pattern override changes `avg_df` **and** `additional_matrix`; a class without an override is
  byte-identical to the no-override run
* cash-flow override does **not** change `avg_df` or the run-off sheets
* precedence: both supplied → cash flow wins, warning names the keys
* `shape_only` rescales to `Future CF` exactly; `Discounting Impact` recomputes off the rescaled values
* `strict` raises `OverrideReconciliationError` listing offending keys; nothing is written
* `absolute` proceeds and warns
* unmatched override keys appear in `OverrideReport.unmatched`, do not silently pass
* pattern with a zero total → rejected, no divide-by-zero
* negative cash flow in a period (recovery) is permitted and preserved

**`datasets/tests/test_pattern_cashflow_io.py`** (new)
* wide → long → wide round-trip is lossless for both kinds
* template width derives from the source job's development-period count
* duplicate `(class, dev_period)` rejected on import with the row number

**`processing/tests/test_module2_api.py`**
* allocate with overrides persists `override_report`
* `strict` failure surfaces a 422 with the offending keys, not a 500

## 7. Edge cases

* **Pattern shorter than the development horizon** → tail treated as zero, warned. Longer → truncated
  and renormalised, warned.
* **Class in the override but absent from the data** → `unmatched`, warning, never an error.
* **RI rows.** `avg_df` is gross-only today. A pattern override applies to `additional_matrix` for
  **both** treaty types (LIC has RI rows), while the `Payment Pattern` sheet stays gross-only to keep
  the sheet contract stable. Explicitly asserted in tests.
* **`Expected Unpaid % == 0`** rows get `[1, 0, 0, …]` today; an override must not resurrect
  development on a fully-developed row. Preserved.
* **Discount horizon.** Longer supplied cash flows can index past the discount curve; the existing
  `cy_disc.get(i)` returns `None` → contributes 0. Made explicit and warned rather than silently
  dropping value.
* **Movement disclosure.** Any override changes `Discounting Impact`, which flows into the movement
  workbook. The movement job records that its source allocate run carried overrides, so a disclosure
  is never produced from overridden figures without that being visible on its face.

## 8. Estimate

Backend 6d (dataset kinds 2d, override engine 3d, API 1d), frontend 5d, tests 3d, goldens 1d.
**~15 days.**
