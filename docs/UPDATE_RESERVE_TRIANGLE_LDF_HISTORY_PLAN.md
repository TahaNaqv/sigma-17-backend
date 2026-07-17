# Update Reserve — LDF history in the triangle + link to Select Methods

> **Goal:** Let the actuary select the LDF **inside the Paid/Reported triangle view**, with the
> **LDF history** (age-to-age factors + Simple/Weighted Avg benchmarks) visible as the basis for the
> selection, and have that selection **flow into the Select Methods (Reserve Summary) tab** so the
> ultimates reflect it — one continuous workflow, as in Excel.

Status: planned (2026-07-13). Third follow-up on Update Reserve; companion to
`UPDATE_RESERVE_LDF_EDITING_PLAN.md` and `UPDATE_RESERVE_METHOD_SELECTION_PLAN.md`.

---

## 0. Client requirement

> "i can find the option to select the LDF in this, while i could not find as per the required, the
> user can not find/utilize the **history of LDF** which later can be used to select LDF.
> **select LDF should be put in here paid/reported triangle** and **linked with select method tab for
> reserve summary format**."

Three asks:
1. **Show the LDF history** — the basis for choosing a link ratio. Today our editor shows a bare
   Selected LDF input with zero context.
2. **Put the Selected LDF inside the Paid/Reported triangle view** (the client's screenshot 2
   layout), not as an isolated 2-row widget.
3. **Link the LDF selection to the Select Methods tab** so the Reserve Summary ultimates reflect it.

## 1. What the triangle sheet actually contains

`run_generate_summary` stacks blocks per triangle sheet; all share the column layout
(col A = accident-period label, cols B.. = development 0,1,2,3…):

| Block | Written at | Content | Cell type |
|---|---|---|---|
| Incremental triangle | `startrow=0` | raw claims | literals |
| Cumulative triangle | `+ n+3` | cumulative claims | literals |
| **Age-to-age factors** | `start_row_age_to_age` (`engine.py:1146-1148`) | **the LDF history** — historical link ratios per accident period | literals |
| **Simple Avg LDF / CDF** | `engine.py:1153-1160` | benchmark averages | literals |
| **Weighted Avg LDF / CDF** | `engine.py:1179-1190` | volume-weighted benchmarks | literals |
| **Selected LDF** | `start_row_weighted_ldf + 3` (`:1192-1198`) | input, placeholder `=1` | **formula** |
| **Selected CDF** | `+1` (`:1200-1210`) | `=PRODUCT(LDF[k]:LDF[last])` | **formula** |

The Reported Triangle mirrors this exactly (`engine.py:1218-1272`).

**"History of LDF" = the age-to-age block + the four Avg LDF/CDF benchmark rows.** None of it is
exposed by our reader or UI today.

## 2. Key discovery that shapes the design

The client's screenshot 2 **is our existing generic output preview** —
`GET /api/module1/jobs/<pk>/output/rows/?file=<workbook>&sheet=Paid Claims Triangle`
(`processing/output_preview.py::read_sheet_page`), which loads with **`data_only=True`**
(`output_preview.py:116`). That is precisely why Selected LDF/CDF render **blank** there (uncached
formulas) while the history/benchmarks render fine (literals).

**Therefore: render the whole triangle grid and overlay the editable Selected LDF row in place.**
The history and benchmarks come along for free, exactly as Excel shows them — no fragile
block-parsing, and no reinterpretation of the engine's quirks (§7).

## 3. Design

**(a) Triangle view with in-place LDF editing.** The reserve-workbook reader returns, per triangle
sheet, the **full cell grid** plus `ldf_row` / `cdf_row` / resolved `selected_ldf`. The frontend
renders the grid Excel-style (read-only), and at `ldf_row` renders editable inputs; at `cdf_row`
renders the **live derived** CDF (`selectedCdfFromLdf`). The actuary sees history → benchmarks →
selection in one view, like Excel.

**(b) Link LDF → Select Methods.** `read_reserve_summary_rows` and its endpoint accept
`ldf_overrides`; they apply them (LDF → derived CDF literals) before deriving each row's Paid/
Reported CDF, so the five method ultimates in the Select Methods tab reflect the selected LDFs.

**(c) One combined run.** The page currently submits **one** override type per run (modes are
mutually exclusive). The backend already accepts `ldf_overrides` + `cdf_overrides` +
`method_overrides` together. Make the from-Summary-job flow submit **both** `ldf_overrides` and
`method_overrides` on Run, so "select LDFs" and "select methods" become two steps of one workflow.

Data flow:
```
Triangle tab: grid (history + benchmarks) + editable Selected LDF ──► ldfOverrides
                                   │  (derived CDF, live)
                                   ▼
Select Methods tab: fetch reserve-summary rows WITH ldf_overrides
                 → Paid/Reported CDF reflect the selected LDFs
                 → five ultimates → pick Implied LR + Method ──► methodOverrides
                                   │
                                   ▼
      Run: POST ldf_overrides + method_overrides (one job)
      → engine writes Selected LDF + derived CDF, then G/O, then ultimates
```

## 4. Backend changes (sigma-17-backend)

1. **Reader — full triangle grid.** In `processing/services/reserve_workbook.py`, extend
   `_read_triangle_cdf` (and thus `read_workbook_cdfs`) to include the sheet grid:
   `grid: [[cell,…],…]` (row 1..max_row × col 1..max_col, `data_only` values, JSON-safe:
   numbers/strings/null) plus the existing `cdf_row`, `ldf_row`, `column_labels`, `values`,
   `selected_ldf`. Add a **cell-count guard** (mirror `MODULE1_OUTPUT_PREVIEW_MAX_CELLS`) and return
   `grid_truncated: bool` rather than ever returning an unbounded payload. Triangles are small
   (~26×5), so one call carries everything the editor needs.
2. **Reader — LDF-aware summary rows.** `read_reserve_summary_rows(source_job, filename, *,
   cdf_overrides=None, ldf_overrides=None)`: pass both into `_apply_overrides_to_bytes` (already
   supports `ldf_overrides` → writes Selected LDF + derived CDF) before deriving the per-row
   Paid/Reported CDF. LDF wins over CDF per sheet (existing rule).
3. **Endpoint.** `Module1ReserveWorkbookSummaryView`
   (`GET …/reserve-workbooks/<file>/reserve-summary/`) accepts `?ldf_overrides=` (URL-encoded JSON)
   alongside the existing `?cdf_overrides=`; same JSON validation as the job view.
4. **No engine change.** `run_update_reserve_summary` already consumes whatever Selected CDF
   literals the write path bakes in; `write_workbooks_with_overrides` already handles
   `ldf_overrides_by_filename`; the job view already accepts all three override types together.

## 5. Frontend changes (sigma-17-dashboard)

1. **API** (`api/module1.ts`): `ReserveTriangleCdfDto` gains `grid: Array<Array<string|number|null>>`
   and `grid_truncated: boolean`. `fetchReserveSummaryRows(sourceJobId, filename, cdfOverrides?,
   ldfOverrides?)` gains the LDF param.
2. **`ReserveCdfEditor` → triangle view.** Replace the 2-row widget with an Excel-like grid per
   triangle sheet: render `grid` read-only (history, benchmarks, headers), and:
   - at `ldf_row`: editable numeric inputs seeded from `selected_ldf` (placeholder `=1` → `1.0`),
   - at `cdf_row`: the **live derived** CDF via `selectedCdfFromLdf`,
   - highlight both rows (mirroring the client's cyan highlight) and keep a sticky header.
   Still emits `ldfOverrides` per file→sheet. Row/label semantics come from the grid itself, so no
   block parsing.
3. **`ReserveMethodEditor` — consume the LDF.** Pass `ldfOverrides?.[filename]` into
   `fetchReserveSummaryRows` so the previewed Paid/Reported CDF (and the five ultimates) reflect the
   selected LDFs. Re-fetch when the LDF selection for that workbook changes (invalidate the cached
   rows for that file).
4. **`UpdateReservePage` — one combined run.** In the from-Summary-job flow, submit **both**
   `ldf_overrides` and `method_overrides` on Run (backend accepts both). Keep the tabs as the two
   steps; add a short note that LDF selections feed the Method tab.
5. **Store** (`state/wizards/updateReserve.ts`): unchanged shape (`ldfOverrides`, `methodOverrides`
   already exist).

## 6. Consistency guarantees

- **One shared suffix product.** `selected_cdf_from_ldf` (backend) ≡ `selectedCdfFromLdf` (TS) —
  already parity-tested both sides. The triangle preview's derived CDF, the method tab's CDF, and
  the engine's baked CDF all come from this one rule.
- **Grid is Excel-faithful by construction** — we display exactly the cells the workbook holds
  (`data_only`), so what the actuary sees equals what Excel shows.
- **One override contract** — `ldf_overrides` drives both the method preview (read path) and the
  job output (write path), so preview == output.

## 7. Pre-existing quirks — flag, do NOT silently change

1. **The two benchmark rows are misaligned by one development column.** Simple Avg LDF lands at dev
   **0–2** (`engine.py:1153-1155`), Weighted Avg LDF at dev **1–3** (`:1178-1183`, note the
   `insert(0, np.nan)`). They describe the same transitions but sit one column apart — visible in the
   client's screenshot (row 21: `6.78, 0.54, 0.26`; row 23: `blank, 4.79, 1.06, 1.05`). Surfacing
   both as selection guidance could mislead a column-by-column comparison. **Needs actuarial
   sign-off.** The grid approach displays them exactly as Excel does, so we introduce no new error —
   but we should raise it.
2. **`calculate_age_to_age_factors` fills gaps with `0.00`** (`engine.py:586`) and leaves the last
   dev column empty, so the history shows zeros where there is simply no data. Rendering the raw
   grid reproduces Excel exactly; optionally mute zeros visually **without** changing the engine.

Both pre-date this work and live in the Summary engine (they'd change every client workbook). Raise
with the actuary; do not unilaterally "fix".

## 8. Edge cases

- **Large triangles** (many accident periods × dev columns) → cell-count guard + `grid_truncated`.
- **Missing Selected LDF row** (older workbooks) → grid still renders; LDF editing disabled for that
  sheet with a clear note (the write path already no-ops when `ldf_row` is absent).
- **Blank LDF** → treated as `1.0` in the suffix product (existing, tested).
- **Method preview staleness** → changing a workbook's LDF must invalidate that file's cached
  reserve-summary rows, else the ultimates would silently show pre-edit CDFs.
- **Backward compatible** — no `ldf_overrides` → unchanged behaviour; legacy `cdf_overrides` still
  honoured.

## 9. Testing & rollout

- **Reader:** grid returned with correct dimensions/values; `ldf_row`/`cdf_row` indices point at the
  Selected LDF/CDF rows within the grid; formula rows read as blank in the grid while `selected_ldf`
  resolves `=1`→1.0; cell-count guard sets `grid_truncated`.
- **Summary rows + LDF:** `read_reserve_summary_rows(..., ldf_overrides=…)` yields Paid/Reported CDF
  equal to `selected_cdf_from_ldf(ldf)` (reversed per the existing accident-period mapping) — i.e.
  the method tab's ultimates provably reflect the LDF selection.
- **Endpoint:** `?ldf_overrides=` validated + honoured; invalid JSON → 400.
- **Combined run:** a job with `ldf_overrides` + `method_overrides` produces a workbook whose
  Selected LDF, derived Selected CDF, G (Implied LR) and O (Selected Method) all reflect the inputs,
  and whose ultimates match the web preview (end-to-end).
- **Frontend:** grid renders history/benchmarks; LDF inputs at `ldf_row`; derived CDF updates live;
  method tab re-fetches on LDF change; tsc + vite build clean.
- **/verify:** Summary → Update Reserve → triangle tab (see history, edit LDF) → Methods tab
  (confirm CDF/ultimates moved) → Run → output matches preview.

## 10. Out of scope

- Changing the engine's benchmark alignment or zero-filling (§7 — actuarial sign-off first).
- Click-to-select an LDF *from* a benchmark row (nice-to-have convenience; note as follow-up).
- Editing the incremental/cumulative triangles or the benchmark rows (computed, not inputs).
