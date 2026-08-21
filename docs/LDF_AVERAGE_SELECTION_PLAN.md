# WP1 — LDF Average Bases & Factor Exclusion (Strikethrough)

> **Goal:** Correct the defective Simple Average, then turn the triangle view into a real factor-
> selection surface: multiple average bases (all / excluding high & low / last 4 / last 8 / median /
> volume-weighted variants / custom), per-cell strikethrough exclusion, and one click to adopt the
> chosen basis as the Selected LDF row.

Status: planned (2026-08-21). Decisions: `docs/CLIENT_REQUIREMENTS_DECISIONS.md` §2 F3, §3 D6.
Requirement 7. Depends on WP0. Shares its grid component with WP5.

---

## 0. Client requirement

> "simple and weighted averages are already being calculated, want to have feature of simple average
> by removing/strikethrough high and low averages, last 4 period, last 8 period average or customise
> average for the user"

## 1. How it works today

### 1.1 What the engine writes

Per triangle sheet (`module1_engine/engine.py`, Paid ~`:1130-1200`, Reported ~`:1210-1275`), stacked
down one sheet with blank-row gaps:

```
Incremental Triangle          (Paid sheet only)
Cumulative Triangle
Age-to-Age Factors
Simple Avg LDF                mean of the age-to-age column
Simple Avg CDF                reverse-cumulative product of the above
Weighted Avg LDF              sum(cum[:n-i, i]) / sum(cum[:n-i, i-1])
Weighted Avg CDF
Selected LDF                  seeded "=1"        <-- the only input the engine consumes
Selected CDF                  "=PRODUCT(col:last)"
```

`run_update_reserve_summary` reads **only `Selected CDF`**. Everything above it is a benchmark the
actuary reads to decide what to type into `Selected LDF`.

### 1.2 The defect (F3)

`calculate_age_to_age_factors` zero-fills the undeveloped lower-right region:

```python
age_to_age_factors.iloc[:, i] = (next_column / current_column.replace(0, np.nan)).fillna(0)
                                                                                 ^^^^^^^^^
```

The simple average then means over those zeros. On the client's own frozen golden
(`Banker's Blanket Payment GROSS 2017-12.xlsx`):

```
Simple Avg LDF    0.0  0.0  0.125  0.127  0.0  0.0  0.0
Simple Avg CDF    0.0  0.0  0.000  0.000  0.0  0.0  0.0     <-- unusable
Weighted Avg LDF  NaN  NaN  NaN    3.213  1.016 NaN  NaN
```

Verified never read back by any computation (§2 F3 of the decision record) — the correction changes
no filed figure.

### 1.3 What the web already has

**More than expected.** `processing/services/reserve_workbook.py::_read_triangle_cdf` already
returns the **entire sheet grid** plus 1-based `ldf_row` / `cdf_row`, and
`ReserveCdfEditor.tsx::TriangleLdfTable` already renders that grid with the Selected LDF row
editable in place and the Selected CDF row derived live. The read and render scaffolding for this
work package is therefore **already built**; WP1 adds block awareness, average computation and the
exclusion mask on top of it.

## 2. Design

### 2.1 The correction

`calculate_age_to_age_factors` emits **NaN**, not `0`, where a factor is undefined (denominator zero
or either cumulative cell NaN). The simple average already uses `mean(axis=0)` with `skipna=True`,
so it becomes correct as a consequence.

Illustrative, on a 4x4 triangle:

| | before | after |
|---|---|---|
| Simple Avg LDF | `1.042, 0.549, 0.257` | `1.389, 1.098, 1.029` |
| Simple Avg CDF | `0.147, 0.141, 0.257` | `1.569, 1.130, 1.029` |

Weighted Avg is computed from the cumulative triangle directly and is **unaffected** — it already
restricted itself to developed rows, which is why it was the only usable row.

### 2.2 Column labelling quirk — documented, not changed

`calculate_age_to_age_factors` assigns `age_to_age.iloc[:, i] = cum[:, i+1] / cum[:, i]`, so the
column headed `0` holds the **dev 0 → dev 1** factor, and the final column is always empty. Selected
LDF / Selected CDF are written positionally against the same layout, so the workbook is internally
consistent and the engine reads it correctly.

**Decision: do not renumber the workbook columns.** Renumbering risks silently mis-aligning the
Selected LDF row against historic workbooks re-used as source jobs. Instead the **web** renders
development headers as `0→1`, `1→2`, ... and suppresses the always-empty tail column. Cosmetic,
zero risk, and it removes the actual confusion.

### 2.3 Block labelling

The web needs to know where the age-to-age block starts and ends. Rather than reproduce the engine's
row arithmetic (brittle), the engine writes a **label cell in column 1** on the blank row above each
block:

```
A{r}: "Incremental Triangle" | "Cumulative Triangle" | "Age-to-Age Factors"
```

The reader locates them with the existing `_find_row_by_label` primitive — the same mechanism already
used for Selected LDF / Selected CDF. Side benefit: the downloaded Excel becomes self-describing,
which also serves WP7.

### 2.4 Average bases — one primitive, several generators

The general primitive is a **per-cell exclusion mask**. Every named basis is a generator that
produces a mask; `custom` is the user editing it directly. This keeps one code path, one audit
record, and makes "customise average for the user" fall out for free.

Given the age-to-age matrix `A[i][j]` (i = accident period, j = development column) and validity
`V[i][j] = not isnan(A[i][j])`:

| Basis | Mask generated |
|---|---|
| `all` | exclude nothing |
| `ex_hi_lo` | per column, exclude the max and the min valid cell (requires >= 3 valid, else falls back to `all` for that column) |
| `last_4` / `last_8` | per column, keep only the N most recent accident periods with a valid cell |
| `median` | not a mask — a different reducer; listed here as a selectable basis |
| `volume_weighted` | not a mask — computed from the cumulative triangle over non-excluded rows |
| `volume_weighted_last_n` | as above, restricted to the N most recent rows |
| `custom` | the user's mask, seeded from whichever basis was last applied |

Reducer for masked bases: `mean` over valid, non-excluded cells. A column with no surviving cell
yields `null`, rendered blank, and the user must type a factor or accept `1.0`.

### 2.5 Where each average is computed

Deliberately **twice**, from one shared spec:

* **Engine** writes the standard benchmark rows into the workbook (`Simple Avg`, `Weighted Avg`,
  `Ex-Hi-Lo Avg`, `Last 4 Avg`, `Last 8 Avg`, `Median`, each with its CDF) so the downloaded Excel
  is self-documenting for a user working offline.
* **Frontend** recomputes live as the user toggles exclusions, because a custom mask cannot be
  precomputed.

Parity is enforced the same way `selected_cdf_from_ldf` already is: a pure Python implementation
(`module1_engine/averages.py`) and a pure TS implementation (`src/lib/ldfAverages.ts`), unit-tested
against a **shared fixture vector** checked into both repos. This is the existing
preview-equals-output contract from `UPDATE_RESERVE_LDF_EDITING_PLAN.md`, applied again.

### 2.6 Adoption and the write path

"Apply as Selected LDF" writes the computed vector into the existing draft state and submits through
the **existing `ldf_overrides` field**. No new write contract, no engine signature change:

```
basis + mask  --(shared reducer)-->  LDF vector  -->  ldf_overrides  -->
  _apply_overrides_to_bytes  -->  Selected LDF literals + derived Selected CDF literals
```

### 2.7 Audit

The derived LDF vector is what the engine consumes, but the *reason* must survive. Persist alongside
it in `input_meta`:

```json
"ldf_selection": {
  "<file>.xlsx": {
    "Paid Claims Triangle": {
      "basis": "ex_hi_lo",
      "excluded_cells": [[3, 1], [7, 1], [3, 2]],
      "derived_ldf": [1.389, 1.098, 1.029],
      "applied_at": "2026-08-21T10:14:03Z"
    }
  }
}
```

Without this, a reviewer can see the factor but not the judgement. With it, the selection is
reproducible and explainable at audit.

## 3. Backend changes

| File | Change |
|---|---|
| `module1_engine/engine.py` | `calculate_age_to_age_factors` → NaN not `0` (the fix); write block label cells; write the extended benchmark rows |
| `module1_engine/averages.py` | **new** — `age_to_age_validity`, `mask_for_basis`, `reduce_masked`, `volume_weighted_ldf`; pure, no pandas-in-signature |
| `module1_engine/__init__.py` | export the above |
| `processing/services/reserve_workbook.py` | `_read_triangle_cdf` also returns `blocks` (label → `{start_row, end_row}`) and `a2a_matrix` (numeric, NaN-preserving) so the client does not re-parse the grid |
| `processing/views.py` | `Module1UpdateReserveJobView` accepts `ldf_selection` (JSON) → `input_meta` |
| `processing/tasks.py` | pass through; no engine signature change |

## 4. Frontend changes

| File | Change |
|---|---|
| `src/lib/ldfAverages.ts` | **new** — TS mirror of `module1_engine/averages.py` |
| `src/lib/ldfAverages.test.ts` | **new** — shared fixture parity tests |
| `src/components/TriangleGrid.tsx` | **new** — extracted from `ReserveCdfEditor`; owns grid render, block awareness, per-cell strikethrough toggle, heat shading. **WP5 reuses this component for claim exclusion** |
| `src/components/ReserveCdfEditor.tsx` | consume `TriangleGrid`; add the basis selector; "Apply as Selected LDF"; render `0→1` headers; drop the empty tail column |
| `src/api/module1.ts` | `blocks`, `a2a_matrix` on `ReserveTriangleCdfDto`; `LdfSelection` type |
| `src/state/wizards/updateReserve.ts` | persist `basis` + `excludedCells` per (file, sheet) |
| `src/pages/UpdateReservePage.tsx` | submit `ldf_selection` alongside `ldf_overrides` |

**Interaction:** click a factor cell to strike it through; the affected column average and the
derived CDF update immediately. Selecting a preset basis replaces the mask; editing any cell
afterwards switches the basis to `custom` with the mask retained — never silently discarding the
user's edits.

## 5. Bit-identity and goldens

WP1 changes output **by design**, in three ways, all in the benchmark region of the triangle sheets:

1. age-to-age undeveloped cells `0` → NaN (blank in Excel);
2. `Simple Avg LDF` / `Simple Avg CDF` become correct;
3. new label rows and additional benchmark rows shift subsequent rows down.

`Selected LDF` / `Selected CDF` values, `Reserve Summary`, `Combined_Summary` and every Module 2
output are **unchanged for an unedited workbook** — item 3 shifts row positions, and the reader
locates rows by label, not by offset. That invariance is the primary regression assertion.

Golden handling: retain `summary_ref` from WP0 as the row-position baseline under
`summary_ref_wp0`; capture `summary_ref` fresh after WP1. Dated: **2026-08-21, WP1, a2a NaN + simple
average correction + benchmark rows.**

## 6. Tests

**`module1_engine/tests/test_averages.py`** (new)
* validity mask excludes undeveloped and zero-denominator cells
* `all` on the 4x4 fixture → `1.389, 1.098, 1.029`
* `ex_hi_lo` with < 3 valid cells falls back to `all` for that column
* `last_4` / `last_8` pick the N most recent **valid** rows, not the N most recent rows
* a column with every cell excluded → `None`, not `0` and not a crash
* volume-weighted over a masked row set matches a hand-computed value
* `median` on even and odd counts

**`module1_engine/tests/test_golden_engines.py`**
* `Selected LDF` / `Selected CDF` / `Reserve Summary` unchanged vs `summary_ref_wp0`
* `Simple Avg CDF` is no longer identically zero on the reference book

**`processing/tests/test_reserve_workbook.py`**
* `blocks` locates the a2a block by label on a synthetic workbook
* `a2a_matrix` preserves NaN rather than coercing to `0`
* reader still finds Selected LDF/CDF after rows shift

**`src/lib/ldfAverages.test.ts`**
* every basis matches the shared Python fixture vector to 1e-10
* mask edit after preset switches basis to `custom` and retains the mask

## 7. Edge cases

* **Fully undeveloped column** (newest accident period) → no valid cells → `null` LDF, blank cell,
  user must act. Never silently `1.0`, which would understate the ultimate.
* **Single valid cell** → `ex_hi_lo` would exclude everything; falls back to `all`.
* **Negative factors** (recoveries exceeding payments in a period) — valid, included, and flagged in
  the UI. Excluding them must be the actuary's choice, not the code's.
* **`grid_truncated`** (`MODULE1_OUTPUT_PREVIEW_MAX_CELLS`) — `a2a_matrix` is returned even when the
  full grid is suppressed, so averages still work on very large triangles.
* **Legacy source workbooks** produced before WP1 have no label rows — the reader falls back to the
  historic row arithmetic and the UI hides preset bases it cannot compute. Old jobs stay usable.
* **Reported vs Paid layout differ** (Reported has no incremental block); block-label lookup makes
  this a non-issue.

## 8. Estimate

Backend 3d, frontend 4d (`TriangleGrid` is reused by WP5, so this is shared investment), tests 2d,
golden re-capture 0.5d. **~9.5 days.**
