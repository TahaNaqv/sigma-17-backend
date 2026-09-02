# WP1 — LDF Average Bases & Factor Exclusion (Strikethrough)

> **Goal:** Correct two defects in the average rows the actuary selects factors against, then
> turn the triangle into a real selection surface: multiple average bases (all / ex-high-low /
> last-4 / last-8 / median / volume-weighted / custom), per-cell strikethrough, and one click
> to adopt the chosen basis as the Selected LDF.

Status: **implemented 2026-09-01** (see §10). Re-planned the same day after measurement. Requirement 7.
Decisions: `CLIENT_REQUIREMENTS_DECISIONS.md` §2 F3/F5, §3 D6. Depends on WP0.
Every figure below is measured on `benchmarks/fixtures/summary_ref`; §9 lists the scripts.

---

## 0. Client requirement

> "simple and weighted averages are already being calculated, want to have feature of simple
> average by removing/strikethrough high and low averages, last 4 period, last 8 period average
> or customise average for the user"

The premise — "already being calculated" — is where this work package starts, because **both**
of those rows are currently wrong, in different ways, and one of them is wrong by a factor of
three.

---

## 1. How it works today

### 1.1 What the engine writes

Per triangle sheet (`module1_engine/engine.py`, Paid `:1250-1325`, Reported `:1330-1400`),
stacked down one sheet with blank-row gaps:

```
Incremental Triangle          (Paid sheet only)
Cumulative Triangle
Age-to-Age Factors
Simple Avg LDF                column mean of the age-to-age block
Simple Avg CDF                reverse-cumulative product of the above
Weighted Avg LDF              sum(cum[:n-i, i]) / sum(cum[:n-i, i-1])
Weighted Avg CDF
Selected LDF                  seeded "=1"        <-- the only input the engine consumes
Selected CDF                  "=PRODUCT(col:last)"
```

`run_update_reserve_summary` reads **only `Selected CDF`**. Everything above it is a benchmark
the actuary reads in order to decide what to put into `Selected LDF`.

### 1.2 The column convention, established from the code

The cumulative frame's columns are `['Accident Period', 0, 1, ... n-1]`, so `iloc[:, k]` is
development column `k-1`. In `calculate_age_to_age_factors`:

```python
for i in range(1, len(cumulative_triangle.columns) - 1):
    age_to_age_factors.iloc[:, i] = (cum.iloc[:, i+1] / cum.iloc[:, i].replace(0, np.nan)).fillna(0)
    age_to_age_factors.iloc[:, 0] = cumulative_triangle.iloc[:, 0]
```

So **age-to-age development column `j` holds the factor from dev `j` to dev `j+1`**, for
`j = 0 … n-2`; the final development column is never assigned and is always blank. (`iloc[:, 0]`
is the *Accident Period label* column being overwritten with itself — harmless, and not, as an
earlier draft of this plan claimed, a raw cumulative amount.)

That convention is forced, not arbitrary. `Selected CDF[j] = PRODUCT(LDF[j] : LDF[last])`, and
`cdf_for_row` hands the CDF at development column `m` to the cohort whose maturity is `m`. For
that CDF to mean "develop from `m` to ultimate", `LDF[m]` must be the factor from `m` to `m+1`.
**Age-to-age and Simple Avg are aligned with Selected LDF. Anything else is not.**

### 1.3 Defect F5 — `Weighted Avg LDF` is written one column too far right

```python
for i in range(1, len(cumulative_sums)):
    num = cum_numeric.iloc[:n-i, i].sum()        # cum_numeric is dev columns only
    den = cum_numeric.iloc[:n-i, i-1].sum()      # -> factor(dev i-1 -> dev i)
    weighted_ldfs.append(num / den)
weighted_ldfs.insert(0, np.nan)                  # -> lands at development column i
```

`weighted[j]` is the factor from dev `j-1` to dev `j`, against `simple[j]` = the factor from
dev `j` to dev `j+1`. **A one-column shift.** The frozen golden for
`Banker's Blanket Payment GROSS 2017-12.xlsx` shows it directly — the age-to-age block's only
two factors are `1.0` at dev column 2 and `1.015748` at dev column 3:

```
Simple Avg LDF     ...  col2=0.125000 (=1.0/8)   col3=0.126968 (=1.015748/8)
Weighted Avg LDF   ...  col3=3.213402            col4=1.015748
                                                 ^^^^^^^^^^^^^ the same factor, one column right
```

**Consequence.** Since F3 (below) leaves Simple Avg unusable, `Weighted Avg LDF` is today the
*only* usable benchmark row — so copying it into Selected LDF is the natural, and effectively
the only, action available. Doing so applies every factor one development period late:

| | total Paid CL ultimate |
|---|---|
| Weighted Avg copied verbatim, as the workbook writes it | **829,920,872** |
| the same factors, correctly aligned | **298,356,105** |
| | **+178.2%** |

Seven workbooks are affected; the worst is `Miscellaneous Payment GROSS` at **+299.3%**, then
`Banker's Blanket` at +153.0% and `Motor Insurance` at +119.1%.

The repository already contains a **correct** implementation of the same quantity —
`module1_engine/triangles.py::volume_weighted_ldf` (built for WP6) puts
`out[j] = cum[:rows, j+1] / cum[:rows, j]`, the aligned convention. So there are currently two
volume-weighted implementations that disagree by one column.

### 1.4 Defect F3 — `Simple Avg` means over zero-filled cells

`.fillna(0)` on the division result turns every undeveloped and zero-denominator cell into a
`0.0` factor, and the column mean then averages over them:

```
Simple Avg LDF    0.0  0.0  0.125  0.126968  0.0  0.0  0.0
Simple Avg CDF    0.0  0.0  0.000  0.000000  0.0  0.0  0.0     <-- collapses to zero
```

`0.125` is `1.0 / 8` — one real factor divided by eight rows, six of which do not exist. The CDF
row, a reverse-cumulative product through zeros, collapses to zero and is unusable.

Verified by repository-wide search: `Simple Avg` and `Weighted Avg` are **written, formatted for
display, and asserted in tests — never read back by any computation**. The only references are
`processing/output_column_kinds.py` (preview formatting),
`processing/services/reserve_workbook.py` (whole-grid display) and tests.

**This is the safety property the whole work package rests on: correcting these rows cannot
change any number the engine computes. It changes the number a human reads — which is precisely
why it matters, and why it warrants an advisory rather than a restatement.**

### 1.5 The baseline nobody has seen: today's web reserve is `2 x paid`

`Selected LDF` is seeded with the string `=1` and `Selected CDF` with `=PRODUCT(...)`. Nothing
evaluates those formulas — the engine writes the file, and `run_update_reserve_summary` reads it
back with `data_only=True`, which returns `None` for a formula no spreadsheet application has
ever opened. `selected_cdf_row_to_series` then applies its blank -> **2.0** default.

So for any job that is not put through the Update Reserve editor, **every cohort develops at a
flat CDF of 2.0**. This was found during WP5, where `exclude_from_ldf_only` measured +0.00%
end-to-end: not a null result, but the signature of a constant CDF.

`ldf_overrides` (already wired: `views.py:1044` -> `tasks.py:483` ->
`reserve_workbook._apply_overrides_to_bytes`) writes **literals** for both rows, which is what
makes a factor real. **WP1's "Apply as Selected LDF" is therefore not a convenience feature. It
is the only route by which a web-only user gets any actuarial factor into the reserve at all.**

### 1.6 The spread this work package controls

Across every reserve workbook (sum of paid-to-date = 72,086,339):

| basis | total Paid CL ultimate | vs today's default |
|---|---|---|
| **today's default (CDF = 2.0)** | 144,172,678 | — |
| ex-high-low / median | 240,311,243 | +66.7% |
| volume-weighted (correctly aligned) | 298,356,105 | +107.0% |
| simple average (after the F3 fix) | 478,635,253 | +232.0% |
| Weighted Avg as written today | 829,920,872 | +475.6% |

**5.8x between top and bottom.** Basis selection is the largest single lever in the nine
requirements — larger than sensitivity shocks (item 1), the pattern override (item 2) or
large-claim exclusion (item 6).

### 1.7 What the web already has

More than expected. `_read_triangle_cdf` already returns the **entire sheet grid** plus 1-based
`ldf_row` / `cdf_row`; `ReserveCdfEditor.tsx::TriangleLdfTable` already renders it with Selected
LDF editable in place and Selected CDF derived live via `selectedCdfFromLdf`. And
`src/components/TriangleGrid.tsx` **already exists** — WP5 built it, with the `factor` exclusion
styling (strikethrough + dotted underline, distinct from WP5's claim exclusion) already designed
in for this work package.

---

## 2. Design

### 2.1 Correct the two rows

1. **F5, alignment.** Delete the inline weighted loop; call
   `triangles.volume_weighted_ldf(cumulative)`, which is already correct and already tested, and
   write the result at development column `j`. One implementation, repository-wide.
2. **F3, zero-fill.** `calculate_age_to_age_factors` emits **NaN**, not `0`, where a factor is
   undefined. `mean(axis=0)` already skips NaN, so Simple Avg becomes correct as a consequence.

Both are pure benchmark-region changes: no computed output moves (§1.4).

### 2.2 Column labelling — documented, not renumbered

Development column `j` holds the `j -> j+1` factor and the last column is always blank. **Do not
renumber the workbook**: Selected LDF is written positionally against this layout, and historic
workbooks are re-used as Update Reserve source jobs. Instead the **web** renders headers as
`0→1`, `1→2`, … and suppresses the always-empty tail column. Cosmetic, zero risk, removes the
actual confusion.

### 2.3 Block labels

The web must know where the age-to-age block begins. Rather than reproduce the engine's row
arithmetic (brittle, and it has already drifted once), the engine writes a label in column 1 of
the blank row above each block: `Incremental Triangle`, `Cumulative Triangle`,
`Age-to-Age Factors`. The reader locates them with the existing `_find_row_by_label` primitive —
the same mechanism already used for Selected LDF/CDF. Side benefit: the downloaded workbook
becomes self-describing, which also serves WP7.

### 2.4 One primitive: a per-cell exclusion mask

Every named basis is a **mask generator** over the age-to-age matrix; `custom` is the user
editing the mask directly. One code path, one audit record, and "customise average for the user"
falls out for free.

Given `A[i][j]` and validity `V[i][j] = not isnan(A[i][j])`:

| Basis | Mask |
|---|---|
| `all` | exclude nothing |
| `ex_hi_lo` | per column, exclude the max and min valid cell (needs >= 3 valid, else falls back to `all` **for that column**) |
| `last_4` / `last_8` | per column, keep the N most recent accident periods **that have a valid cell** |
| `median` | a different reducer, not a mask |
| `volume_weighted` | computed from the cumulative triangle over non-excluded rows |
| `volume_weighted_last_n` | as above, restricted to the N most recent rows |
| `custom` | the user's mask, seeded from whichever basis was last applied |

Reducer: mean over valid, non-excluded cells. A column with no survivor yields `null`, rendered
blank — **never silently 1.0**, which would understate the ultimate.

### 2.5 Three measured facts the UI must carry

These come from §1.6's measurement and are requirements, not polish:

* **`last_4` and `last_8` are inert on this book.** Of 448 development columns across all
  workbooks, **zero** have more than 3 valid factors, so `last_4` returns the simple average to
  the cent. They acquire content only with more accident periods — a longer experience period,
  or WP6's **monthly grain** (24 accident months over 2016-2017 instead of 8 quarters). This
  connects requirement 7 to requirement 5 directly.
* **`ex_hi_lo` and `median` are identical on this book.** Every qualifying column has exactly 3
  valid factors, so dropping the high and the low leaves one cell — the median. They diverge only
  at n >= 4.
* Therefore **every average cell must display the count it averaged (`n`)**, and the engine
  writes a matching `Factor Count` row. Without it an actuary selects "Last 4" , sees a number
  change by nothing, and believes a judgement was applied that was not.

### 2.6 "Last N accident periods" vs "last N diagonals" — not actually ambiguous

Standard practice is split between averaging the last N *accident periods* and the last N
*calendar diagonals*. Within a single development column `j`, the factor at accident row `i`
belongs to calendar period `i + j`; with `j` fixed, ordering by accident period and ordering by
calendar period give **the same order and the same selected set**. Because every basis here is
computed column-wise, the distinction collapses. Documented so the question is closed rather
than re-litigated.

### 2.7 Where each average is computed — deliberately twice

* **Engine** writes the benchmark rows into the workbook (`Simple Avg`, `Weighted Avg`,
  `Ex-Hi-Lo Avg`, `Last 4 Avg`, `Last 8 Avg`, `Median`, `Factor Count`, each LDF with its CDF)
  so the downloaded file is self-documenting offline.
* **Frontend** recomputes live as the user toggles cells, because a custom mask cannot be
  precomputed.

Parity enforced exactly as `selected_cdf_from_ldf` already is: pure Python
(`module1_engine/averages.py`) and pure TS (`src/lib/ldfAverages.ts`), unit-tested against a
**shared fixture vector** checked into both repos.

### 2.8 Adoption and the write path

No new write contract, no engine signature change:

```
basis + mask --(shared reducer)--> LDF vector --> ldf_overrides -->
  _apply_overrides_to_bytes --> Selected LDF literals + derived Selected CDF literals
```

### 2.9 Audit

The LDF vector is what the engine consumes; the *reason* must survive it. Persisted in
`input_meta`:

```json
"ldf_selection": {
  "<file>.xlsx": {
    "Paid Claims Triangle": {
      "basis": "ex_hi_lo",
      "excluded_cells": [[3, 1], [7, 1]],
      "factor_counts": [3, 3, 3, 2, 1, 0, 0],
      "derived_ldf": [8.151, 1.148, 1.050, 1.616, 1.415, 1.535, 1.909],
      "applied_at": "2026-09-01T10:14:03Z"
    }
  }
}
```

`factor_counts` is stored, not just displayed: at audit it is the difference between "the
actuary chose a 4-period average" and "the actuary chose a label that silently did nothing".

---

## 3. Backend changes

| File | Change |
|---|---|
| `module1_engine/engine.py` | F5: replace the inline weighted loop with `triangles.volume_weighted_ldf`, written at the aligned column. F3: NaN not `0`. Write block labels, the extended benchmark rows and `Factor Count` |
| `module1_engine/averages.py` | **new** — `age_to_age_validity`, `mask_for_basis`, `reduce_masked`, `factor_counts`; reuses `triangles.cdf_from_ldf`; no pandas in signatures |
| `module1_engine/triangles.py` | unchanged — it is the reference implementation both callers now share |
| `processing/services/reserve_workbook.py` | `_read_triangle_cdf` also returns `blocks` (label -> `{start_row, end_row}`) and `a2a_matrix` (numeric, NaN-preserving) so the client does not re-parse the grid |
| `processing/views.py` | `Module1UpdateReserveJobView` accepts `ldf_selection` (JSON) -> `input_meta` |
| `processing/tasks.py` | pass through; no engine signature change |

## 4. Frontend changes

| File | Change |
|---|---|
| `src/lib/ldfAverages.ts` | **new** — TS mirror of `averages.py` |
| `src/components/TriangleGrid.tsx` | **exists** (WP5) — add the factor-cell click handler; the `factor` exclusion styling is already there |
| `src/components/ReserveCdfEditor.tsx` | consume `TriangleGrid`; basis selector; per-column `n` badge; "Apply as Selected LDF"; `0→1` headers; drop the empty tail column |
| `src/api/module1.ts` | `blocks`, `a2a_matrix` on `ReserveTriangleCdfDto`; `LdfSelection` type |
| `src/state/wizards/updateReserve.ts` | persist `basis` + `excludedCells` per (file, sheet) |
| `src/pages/UpdateReservePage.tsx` | submit `ldf_selection` alongside `ldf_overrides` |

**Interaction:** click a factor to strike it through; the column average and derived CDF update
immediately. Choosing a preset replaces the mask; editing afterwards switches the basis to
`custom` **with the mask retained** — never silently discarding the user's edits.

---

## 5. Bit-identity and goldens

WP1 changes output **by design**, entirely within the benchmark region of the triangle sheets:

1. age-to-age undeveloped cells `0` -> NaN (blank);
2. `Simple Avg LDF/CDF` become correct;
3. `Weighted Avg LDF/CDF` move one column left;
4. new label rows, benchmark rows and `Factor Count` shift subsequent rows down.

`Selected LDF` / `Selected CDF` values, `Reserve Summary`, `Combined_Summary` and every Module 2
output are **unchanged for an unedited workbook** — item 4 shifts row positions, and every reader
locates rows by label. That invariance is the primary regression assertion, and it is what makes
this safe to ship: **no filed number moves.**

Golden handling: copy the current `summary_ref` to `summary_ref_prewp1` as the invariance
baseline, then re-capture `summary_ref`. Dated: **2026-09-01, WP1, a2a NaN + Simple Avg
correction + Weighted Avg realignment + benchmark rows.**

---

## 6. Tests

**`module1_engine/tests/test_averages.py`** (new)
* validity mask excludes undeveloped and zero-denominator cells
* `ex_hi_lo` with < 3 valid cells falls back to `all` for that column
* `last_4` / `last_8` pick the N most recent **valid** rows, not the N most recent rows
* a column with every cell excluded -> `None`, not `0`, not a crash
* `median` on even and odd counts
* `ex_hi_lo == median` exactly when n == 3, and diverges at n == 4 (§2.5, pinned deliberately)

**`module1_engine/tests/test_ldf_alignment.py`** (new — the F5 regression)
* the written `Weighted Avg LDF` at column `j` equals `triangles.volume_weighted_ldf(cum)[j]`
* on the reference book, `Weighted Avg` and `Simple Avg` now describe the same development step:
  the factor `1.015748` appears at column 3 in **both** rows
* copying `Weighted Avg` into Selected LDF reproduces the volume-weighted total
  (298,356,105), not the shifted one (829,920,872)

**`module1_engine/tests/test_golden_engines.py`**
* `Selected LDF` / `Selected CDF` / `Reserve Summary` unchanged vs `summary_ref_prewp1`
* `Simple Avg CDF` is no longer identically zero on the reference book

**`processing/tests/test_reserve_workbook.py`**
* `blocks` locates the a2a block by label on a synthetic workbook
* `a2a_matrix` preserves NaN rather than coercing to `0`
* the reader still finds Selected LDF/CDF after the rows shift

**`src/lib/ldfAverages.test.ts`**
* every basis matches the shared Python fixture vector to 1e-10
* a mask edit after a preset switches the basis to `custom` and retains the mask
* a column whose average is `null` renders blank and blocks "Apply"

---

## 7. Edge cases

* **Fully undeveloped column** -> no valid cells -> `null`, blank, user must act.
* **Single valid cell** -> `ex_hi_lo` would exclude everything; falls back to `all`.
* **Zero factors.** Cumulative paid can fall (Motor recovery substitution puts `AMOUNTRECOVERED`
  into `Amount` for recovery heads), so a genuine `0.0` or sub-1.0 factor is real data, not a
  gap. Only *undefined* cells become NaN. `TriangleGrid` already shades sub-1.0 factors
  divergently.
* **Negative factors** — valid, included, flagged. Excluding them is the actuary's judgement.
* **`grid_truncated`** (`MODULE1_OUTPUT_PREVIEW_MAX_CELLS`) — `a2a_matrix` is returned even when
  the full grid is suppressed, so averages still work on very large triangles.
* **Legacy source workbooks** have no label rows and the old weighted alignment. The reader falls
  back to row arithmetic; the UI hides bases it cannot compute and **warns that the workbook's
  Weighted Avg row predates the F5 fix**.
* **Reported vs Paid layout differ** (Reported has no incremental block) — block labels make this
  a non-issue.

---

## 8. Estimate

| | |
|---|---|
| F5 alignment fix + consolidate onto `triangles.volume_weighted_ldf` | 1d |
| F3 fix + `averages.py` + benchmark & count rows + block labels | 2.5d |
| reader (`blocks`, `a2a_matrix`) + `ldf_selection` persistence | 1.5d |
| frontend: basis selector, mask editing, `n` badges, apply (TriangleGrid exists) | 3d |
| tests | 2.5d |
| golden baseline + re-capture | 0.5d |
| **Total** | **~11 days** |

Up from the pre-verification 9.5d: F5 and the count rows are new, offset by `TriangleGrid`
already existing.

---

## 9. What changed after verification

Every item here was found by measuring, not by reading. Scripts:
`measure_ldf.py`, `measure_ldf2.py`, `measure_avg.py`, `measure_bases.py`.

* **F5 is new and is the largest defect in the nine requirements.** `Weighted Avg LDF` is written
  one development column right of where Selected LDF is read; copying it verbatim overstates the
  total by **+178%**. It outranks F3, which the plan had treated as the headline.
* **Two disagreeing implementations already exist in the repo.** `triangles.volume_weighted_ldf`
  (WP6) is correct; the engine's inline loop is not. The fix is consolidation, not new code.
* **Today's web reserve is `2 x paid-to-date`** for any job not run through Update Reserve
  (§1.5). This reframes WP1 from "nicer benchmarks" to "the only path to a real factor".
* **`last_4` / `last_8` are inert on the reference book** — 0 of 448 columns have more than 3
  valid factors. Hence the `Factor Count` row and the per-column `n` badge, which were not in the
  draft.
* **`ex_hi_lo` == `median` on this book.** Corrected during implementation (§10.4): they
  coincide up to **four** cells, not three, so a column needs **five** valid factors before the
  two bases differ at all. Shipping both as separate named bases is only honest alongside the
  count.
* **The "last N accident periods vs last N diagonals" ambiguity is not real** when averages are
  computed column-wise (§2.6). Closed rather than deferred.
* **The draft's §2.2 was wrong**: development column 0 holds the `0→1` factor (not a raw
  cumulative amount), `iloc[:, 0]` is the label column, and no factor is missing. The
  don't-renumber conclusion survives; its stated reason does not.
* **`TriangleGrid` already exists** — WP5 built it, with `factor` exclusion styling already in
  place. The draft budgeted 2.5d to create it.
* **The same two defects exist in `sigma-17-desktop-app/module1.py`** (`:1150`, `:1229`). Fixing
  the web engine deliberately diverges it from the desktop origin; that divergence is intended
  and dated.

---

## 10. Implementation status — built (2026-09-01)

Implemented and tested. What follows records where **building it corrected the plan**.

### 10.1 The safety property, proven rather than asserted

`benchmarks/goldens/summary_ref_prewp1/` was frozen *before* any code changed, and
`module1_engine/tests/test_wp1_invariance.py` asserts against it that **every Reserve Summary
sheet and the whole Combined_Summary are byte-identical**. The benchmark region moved as
designed; no computed figure did. That baseline is not a fixture, so the ordinary golden suite
neither runs nor re-captures it — it exists only for this assertion. If it ever fails, WP1 has
changed a filed number.

### 10.2 Both defects are fixed, and both were visible immediately

On `Banker's Blanket Payment GROSS`, whose age-to-age block holds exactly two factors — `1.0`
at development column 2 and `1.015748` at column 3:

| row | before | after |
|---|---|---|
| `Simple Avg LDF` col 2 / 3 | `0.125` / `0.126968` | `1.0` / `1.015748` |
| `Weighted Avg LDF` | `3.213402` at col **3**, `1.015748` at col **4** | `1.0` at col **2**, `1.015748` at col **3** |

Simple Avg now equals the factors it averages (F3), and Weighted Avg sits on the same columns
as Simple Avg (F5). The engine's inline weighted loop is gone; both the workbook and the web
now compute it through one implementation.

### 10.3 Block labels: the row-1 problem the plan did not anticipate

§2.3 said to label each block on the blank row above it. The **first** block on each sheet has
no blank row above it, and it is a *different* block per sheet (Paid leads with incremental,
Reported with cumulative) — so it genuinely needed a label.

Shifting every sheet down one row to make space was implemented, then reverted: `pd.read_excel`
takes row 1 as the header, so a title row there turns every triangle sheet's golden frame into
`columns = ['Incremental Triangle', 'Unnamed: 1', ...]`. Goldens are this project's core safety
mechanism and will not be degraded for a cosmetic gain.

**Resolution:** blocks 2..n carry a label; the leading block is named by sheet through
`engine.LEADING_BLOCK` / `reserve_workbook.LEADING_BLOCK`. The block WP1 actually needs — the
age-to-age factors — is labelled on both sheets either way.

### 10.4 `ex_hi_lo` and `median` coincide up to **four** cells, not three

The plan said they diverge at n >= 4. They do not. Drop the highest and lowest of n sorted
values and average the rest:

```
n = 3  ->  v2                 == median
n = 4  ->  (v2 + v3) / 2      == median   (an even median IS that mean)
n = 5  ->  (v2 + v3 + v4) / 3 != v3
```

So a development column needs **five** valid factors before the two bases differ at all. The
reference book's maximum is three, and even a fourth would not separate them. Pinned in
`test_averages.py::test_ex_hi_lo_equals_median_up_to_four_cells_and_diverges_only_at_five`.

### 10.5 The reader serves the cumulative block too

`volume_weighted` is a ratio of cumulative sums and cannot be recovered from the age-to-age
factors, so `_read_triangle_cdf` returns `cumulative_matrix` alongside `a2a_matrix`. Both are
served even when the cell guard suppresses `grid`, so selection still works on large triangles.
A test asserts the served `a2a_matrix` is exactly what the served `cumulative_matrix` implies —
otherwise the browser's preview could disagree with the workbook the client receives.

### 10.6 `factor_counts` is captured at apply time, not recomputed

Recomputing the counts when the payload is built would describe the triangle as it is *now*.
The audit question is what the actuary averaged *when they pressed Apply*, so `applyBasis`
records the counts into the selection state and `buildLdfSelectionPayload` copies them. A basis
chosen but never applied has no counts and is therefore omitted — it never reached the workbook.

### 10.7 Files

| | |
|---|---|
| `module1_engine/averages.py` | **new** — masks, reducers, `benchmark_rows`, `column_counts` |
| `module1_engine/engine.py` | F3 fix; F5 fix via `triangles.volume_weighted_ldf`; `write_triangle_benchmarks` / `write_selected_rows` shared by both sheets (the duplication that let the two rows drift is gone); block labels |
| `processing/services/reserve_workbook.py` | `blocks`, `a2a_matrix`, `cumulative_matrix`, `LEADING_BLOCK` |
| `processing/views.py` | `ldf_selection` -> `input_meta` |
| `benchmarks/goldens/summary_ref_prewp1/` | the invariance baseline (§10.1) |
| `src/lib/ldfAverages.ts` + `.fixture.json` | the TS mirror and its Python-generated parity fixture |
| `src/lib/ldfSelectionPayload.ts` | the audit payload |
| `src/components/LdfBasisSelector.tsx` | basis dropdown, derived LDF, factor counts, inert badge, Apply |
| `src/components/ReserveCdfEditor.tsx` | strikethrough on age-to-age cells; adopt a basis as Selected LDF |
| `src/state/wizards/updateReserve.ts`, `src/pages/UpdateReservePage.tsx` | persist + submit the selection |

### 10.8 Verification

* `pytest module1_engine/tests module2_engine` — **280 passed**, all 9 goldens green,
  invariance suite green.
* `manage.py test processing datasets accounts tenants` — **288/290**; the two failures are
  `test_dataset_e2e`, which need a live Redis broker for `.delay()` and fail identically on
  this machine regardless of these changes.
* `vitest` — **201 passed** (23 files), including **35 parity assertions** against the
  Python-generated fixture. `tsc` at its unchanged 45-error baseline.

**Not verified:** nothing here has been exercised against the running stack (Postgres + Redis +
Celery + Vite). The same caveat stands for items 1-6.
