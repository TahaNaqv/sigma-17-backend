# Update Reserve — Selected LDF editing (with derived CDF) Plan

> **Goal:** In the web "Edit CDFs" surface, let the user edit the **Selected LDF**
> row per triangle (the actuary's real input), with the **Selected CDF** row
> deriving live as the reverse-cumulative product — exactly as the Excel
> `=PRODUCT(...)` formula does. Today the web only exposes the Selected CDF row.

Status: planned (2026-07-13). Third refinement to Update Reserve; companion to
`docs/UPDATE_RESERVE_METHOD_SELECTION_PLAN.md`.

---

## 0. Client requirement

Two screenshots + text:
- Screenshot 1 (web `ReserveCdfEditor`): only the **Selected CDF** row is editable.
- Screenshot 2 (triangle output): the highlighted rows are **Selected LDF** *and* **Selected CDF**.
- Text: "we can only update these cells [Selected CDF], ideally it should be available in the
  below figure cells [Selected LDF + Selected CDF]."

Meaning: expose the **Selected LDF** row for editing in the web. In actuarial practice the LDFs
(age-to-age link ratios) are selected and the CDF is *derived* from them — the current web flow is
inverted (edits the derived CDF directly).

## 1. How it works today (from the engine)

Per triangle sheet (`module1_engine/engine.py`):
- **Selected LDF** row — one cell per development column, seeded `=1` (placeholder).
  Paid: `:1178-1182`; Reported: `:1256-1260`.
- **Selected CDF** row — `=PRODUCT(<thisCol><ldfRow>:<lastCol><ldfRow>)` (Paid `:1191-1194`,
  Reported `:1269-1272`). So **CDF[k] = LDF[k] × LDF[k+1] × … × LDF[last]** — a suffix product
  over the Selected LDF cells. The Selected CDF row is *derived*, not an independent input.

Downstream, `run_update_reserve_summary` reads the **Selected CDF** row via `data_only`
(`selected_cdf_row_to_series`) → per-accident-period Paid/Reported CDF → ultimates.

Note (pre-existing): openpyxl never evaluates the `PRODUCT` formula, so an *unedited* workbook's
Selected CDF reads as `None` → the engine defaults it to `2.0`. Editing (today: CDF; after this:
LDF→derived CDF) writes literals the engine reads correctly.

### Current web/read path
- `reserve_workbook.py::_read_triangle_cdf` reads **only** the Selected CDF row.
- `ReserveCdfEditor` renders **only** the Selected CDF row (editable).
- Override write (`_apply_overrides_to_bytes`) writes **only** the Selected CDF row (literals,
  replacing the PRODUCT formula).

## 2. Design

**Make the editor LDF-primary: the Selected LDF row is the editable input; the Selected CDF row is
derived live (read-only) as the reverse-cumulative product. On submit, send the LDF selections; the
backend writes the Selected LDF row (literals) AND the derived Selected CDF row (literals) so the
engine reads correct CDFs.** A single shared suffix-product helper is used by the backend write and
mirrored in the frontend preview (unit-tested for parity) so preview == output.

Why write both rows as literals: openpyxl can't cache a formula result, and the engine reads the
CDF via `data_only`. So the derived CDF must be a literal. The Selected LDF row is also written
(literals) so the output workbook transparently shows the actuary's chosen LDFs (screenshot 2's
other highlighted row). Trade-off (same as today's CDF override): the downloaded workbook's CDF is a
literal, not a live `PRODUCT` — further LDF edits *in Excel* won't auto-recompute CDF. Acceptable
and consistent with the existing behavior.

### The shared derivation
`selected_cdf_from_ldf(ldf_values)` → CDF list where `cdf[i] = ∏_{j≥i} ldf[j]`, each blank LDF
treated as `1.0`. Add to `module1_engine` (exported) next to `selected_cdf_row_to_series`.
The frontend implements the identical suffix product in TS.

## 3. Backend changes (sigma-17-backend)

1. **Shared helper** `selected_cdf_from_ldf(ldf_values)` in `module1_engine/engine.py` (+ export in
   `module1_engine/__init__.py`). Pure, unit-tested.
2. **Reader** `reserve_workbook.py`:
   - Add `_find_row_by_label(ws, "Selected LDF")` (generalize `_find_selected_cdf_row`).
   - Extend `_read_triangle_cdf` to also read the **Selected LDF** row: return
     `selected_ldf: [float|null,...]` and `ldf_row: int|null` alongside the existing
     `values` (CDF) / `cdf_row`. For a placeholder `=1` cell where `data_only` yields `None`,
     surface `1.0` (read the formula cell; `"=1"` → 1.0) so unedited LDFs default sensibly.
3. **Override write** `_apply_overrides_to_bytes` (+ `write_workbooks_with_overrides` contract):
   accept per-sheet **LDF** overrides. When a sheet has LDF overrides: write the Selected LDF row
   literals, then write the Selected CDF row literals = `selected_cdf_from_ldf(ldf)`. Preserve the
   existing per-sheet **CDF** override path (legacy/direct). If both present for a sheet, LDF wins.
4. **View** `Module1UpdateReserveJobView`: accept `ldf_overrides` (JSON, `{file:{sheet:[...]}}`),
   parallel to `cdf_overrides`; part of the "has_overrides" set (requires source job, excludes
   uploaded files); stored in `input_meta["ldf_overrides"]`.
5. **Task** `run_module1_update_reserve_task`: pass LDF overrides into
   `write_workbooks_with_overrides` (already runs for any overrides path). No engine-signature
   change needed — the CDF literals are baked into the staged workbook before
   `run_update_reserve_summary` reads them.

### `ldf_overrides` shape
```json
{ "<file>.xlsx": { "Paid Claims Triangle": [1.05, 1.02, 1.0, ...], "Reported Triangle": [...] } }
```
Positional from column 2 (dev period 0..n), same convention as `cdf_overrides`.

## 4. Frontend changes (sigma-17-dashboard)

1. **API types** (`api/module1.ts`): `ReserveTriangleCdfDto` gains `selected_ldf: (number|null)[]`
   and `ldf_row: number|null`. Add `LdfOverrides` type.
2. **`ReserveCdfEditor`**: render, per triangle, the **Selected LDF** row as editable inputs and the
   **Selected CDF** row as a derived, read-only row that recomputes live via the shared suffix
   product as the user types. Emit `ldfOverrides` (per file→sheet→LDF array). Keep the seed/refresh
   /accordion structure. (Header/labels: "Selected LDF (editable)" + "Selected CDF (derived)".)
3. **Store** (`state/wizards/updateReserve.ts`): add `ldfOverrides`; keep `cdfOverrides` for
   backward compatibility with restored drafts.
4. **Page** (`UpdateReservePage.tsx`): in the `cdfs` mode submit, send `ldf_overrides`
   (JSON.stringify(ldfOverrides)). `hasUnsavedWork` / reset include `ldfOverrides`.
5. **Shared TS suffix-product** mirroring `selected_cdf_from_ldf`, unit-tested against the same
   vectors as the backend.

### Preview ↔ output parity
| Web (derived CDF preview) | Backend write |
|---|---|
| `cdf[i] = ∏_{j≥i} ldf[j]`, blank→1 | `selected_cdf_from_ldf` (identical) |
Then the engine reads those CDF literals via the unchanged `selected_cdf_row_to_series` → identical
downstream ultimates.

## 5. Edge cases

- **Blank / unedited LDF** → treated as `1.0` (no development); CDF suffix product still valid.
- **Tail LDF** (last dev col) → `cdf[last] = ldf[last]` (usually 1.0).
- **Placeholder `=1`** cells read as `1.0` (not empty) so the LDF row shows sensible defaults.
- **LDF vs CDF column counts** are equal (both cols 2..max_col); guard length mismatches.
- **Direct CDF override** still supported by the backend (legacy API), but the UI now produces LDF.
- **Unedited workbooks** copied verbatim (unchanged) — only edited sheets get literals.

## 6. Testing & rollout

- **Engine/helper:** `selected_cdf_from_ldf` suffix-product correctness (incl. blanks→1, tail).
- **Reader:** returns Selected LDF values + row; placeholder `=1`→1.0; parity — reader's derived CDF
  == `selected_cdf_from_ldf(ldf)`.
- **Write:** LDF overrides produce Selected LDF literals + Selected CDF literals = suffix product;
  the engine then reads those CDFs (end-to-end: `run_update_reserve_summary` H/I match the derived
  CDF); legacy CDF override still works; LDF wins when both present.
- **View:** `ldf_overrides` round-trips into `input_meta`; requires source job; excludes files.
- **Frontend:** editor edits LDF, CDF derives live; TS suffix product == backend (unit test);
  submit sends `ldf_overrides`; tsc + vite build clean.
- **/verify:** Summary → Update Reserve "Edit CDFs", edit Selected LDF, confirm the output triangle's
  Selected LDF + Selected CDF and the resulting ultimates match the web preview.
- **Backward compatible:** no `ldf_overrides` → unchanged; existing `cdf_overrides` still honored.

## 7. Out of scope

- Editing Simple/Weighted-Avg rows (computed statistics, not inputs).
- Keeping a live `PRODUCT` formula in the output (openpyxl can't cache its value; engine needs it).
- Selecting LDFs *from* the Simple/Weighted averages by click (could be a later convenience).
