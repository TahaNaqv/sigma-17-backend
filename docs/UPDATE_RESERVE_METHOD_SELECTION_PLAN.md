# Update Reserve — Dynamic Implied LR + Selected Method Plan

> **Goal:** Let users edit **Implied LR** and pick **Selected Method** per accident-period
> row *in the web app*, with the dependent columns (ELR/BF ultimates → Ultimate → IBNR/ULR/CDF)
> recomputing live — exactly as they do in the downloaded Excel — and bake those selections
> into the generated output. Model it on the existing **ULR Selection** step.

Status: planned (2026-07-13). Companion to `docs/IFRS17_MOVEMENT_REUSE_PLAN.md`.

---

## 0. Client requirement

> "In the Update Reserve tab, in the Excel output I can manually update **Implied LR** cells and
> select a **method** via dropdown, and the related values adjust accordingly. I could not find
> this in the web. In the web it should be dynamic — update the Implied LR cells and have the
> related values adjust, as we do in Excel. Similar LR selection we already do in ULR selection."

Confirmed accurate: the web Update Reserve flow exposes **only** triangle CDF editing; Implied LR
and Selected Method are editable **only** inside the downloaded Excel.

## 1. How it works in Excel today (the dependency graph)

`run_update_reserve_summary` (`module1_engine/engine.py:590-719`) appends columns **G–S** to each
`Reserve Summary` row. Base columns (written earlier by the Summary engine): **A** Accident_Period,
**B** EP, **C** Paid, **D** OS, **E** Reported, **F** Reported LR. Appended (`engine.py:676-697`):

| Col | Meaning | Formula / value | Depends on |
|---|---|---|---|
| **G Implied LR** | LR assumption | `None` (blank; user types it) | — input |
| H Paid CDF | selected paid CDF | literal from Selected CDF row (reversed, row idx→`min(idx,n-1)`) | CDF row |
| I Reported CDF | selected reported CDF | literal, same derivation | CDF row |
| J Paid CL Ult | chain-ladder paid | `C × H` (Python literal) | — |
| K Reported CL Ult | chain-ladder reported | `E × I` (Python literal) | — |
| **L ELR Ult** | `=IFERROR(G×B,0)` | Excel formula | **Implied LR** |
| **M Paid BF Ult** | `=IFERROR((1-1/H)×B×G + D,0)` | Excel formula | **Implied LR** |
| **N Reported BF Ult** | `=IFERROR((1-1/I)×B×G + E,0)` | Excel formula | **Implied LR** |
| **O Selected Method** | dropdown, default `"Paid CL"` | literal | — input |
| P Ultimate | `=IF(O="Paid CL",J,IF("Reported CL",K,IF("ELR",L,IF("Reported BF",N,M))))` | formula | **Selected Method** |
| Q IBNR | `=IFERROR(P-E,0)` | formula | P |
| R ULR | `=IFERROR(P/B,0)` | formula | P |
| S CDF | `=IFERROR(P/C,0)` | formula | P |

Dropdown options (data-validation on `O2:O{max}`, `engine.py:708`):
`Paid CL, Reported CL, ELR, Paid BF, Reported BF`. **Recompute happens via live Excel formulas** —
Python writes formulas + the two CL literals only. Editing **Implied LR (G)** recomputes L/M/N and
cascades through P→Q/R/S; editing **Selected Method (O)** re-picks P.

**Quirk to confirm with the actuary (not a blocker):** Paid BF adds **OS claims (D)**, not paid
claims (C) — `engine.py:686`. Reported BF correctly adds E. Preserve as-is for output parity;
flag for sign-off.

## 2. Current web state

- Update Reserve page (`sigma-17-dashboard/src/pages/UpdateReservePage.tsx`) = single-card form,
  two modes: upload workbooks (`files`) or edit **triangle CDFs** from a Summary job (`cdfs` →
  `ReserveCdfEditor` → `cdf_overrides`). **No Implied LR / Selected Method editing.**
- `src/components/ReserveMethodTable.tsx` exists with the table shell, Selected Method `<Select>`,
  and downstream recompute (`getUltimate/getIBNR/getULR/getCDF`, matching engine P/Q/R/S) — **but
  it is orphaned** (never imported/rendered/wired) **and its Implied LR column is read-only**
  (line 129) and it does **not** recompute L/M/N from Implied LR. So it's ~half the feature.
- No backend endpoint exposes Reserve Summary rows; only triangle CDFs
  (`/reserve-workbooks/<file>/cdf/`).

## 3. The ULR Selection pattern to mirror

`GET /module2/jobs/{id}/ulr/` → `fetchModule2UlrRows` → `UlrSelectionTable` (edit one column,
recompute client-side) → persist in wizard store → submit `selected_ulr` JSON at the process step →
engine consumes it. We replicate this for reserve rows.

## 4. Design

**Add a "Method Selection" editable step to Update Reserve:** fetch the per-row Reserve Summary
data, let the user edit **Implied LR** (numeric input) and **Selected Method** (dropdown) with live
client-side recompute, then submit a `method_overrides` payload that the engine bakes into cells
**G** and **O** as literals — keeping L/M/N/P/Q/R/S as formulas so the downloaded Excel still
recomputes and stays editable (exactly how `cdf_overrides` writes literals over PRODUCT formulas).

Data flow:
```
Summary job output (reserve workbooks: triangles + Reserve Summary A–F + Selected CDF rows)
      │  GET reserve-summary rows (A–F + derived H,I)   [optionally applying pending cdf_overrides]
      ▼
Web "Method Selection" table  — edit Implied LR + Selected Method, recompute L/M/N/P/Q/R/S live
      │  submit method_overrides { file: { accident_period: {implied_lr, selected_method} } }
      ▼
update-reserve job → run_update_reserve_summary(method_overrides=…) writes G,O literals; keeps formulas
```

## 5. Backend changes (sigma-17-backend)

1. **Engine** `run_update_reserve_summary(folder_path, *, method_overrides=None)`
   (`module1_engine/engine.py:590`). Inside the row loop (`:676-693`), after building `data`,
   look up the override by the row's `Accident_Period` for this workbook and, when present, set
   `data['Implied LR'] = <fraction>` and/or `data['Selected Method'] = <method>` instead of the
   `None` / `'Paid CL'` defaults. Everything else (formulas, data-validation, IBNR rollup)
   unchanged — the literals propagate to both the per-class sheet and the IBNR Summary df
   (`:722-726`) consistently. Validate method ∈ the five options; ignore unknown accident periods.
2. **Reserve-summary reader** in `processing/services/reserve_workbook.py`: add
   `read_reserve_summary_rows(source_job, filename, *, cdf_overrides=None)` returning per-row
   `{accident_period, ep, paid, os, reported, reported_lr, paid_cdf, reported_cdf}`. It reads
   `Reserve Summary` A–F and derives H/I from the Selected CDF rows using the **same reverse +
   `min(idx,n-1)` mapping as the engine** (factor that derivation into a shared helper to prevent
   drift). When `cdf_overrides` is passed, apply them before deriving H/I so the preview matches
   the final output.
3. **Read endpoint** `Module1ReserveWorkbookSummaryView` →
   `GET /api/module1/jobs/<pk>/reserve-workbooks/<path:filename>/reserve-summary/`
   (`processing/views.py` near `:1015`, `processing/urls.py` near `:28`). `output_available` gated,
   `module1.run` (or read) permission, returns `{job_id, filename, rows:[…]}`. (Accept optional
   `cdf_overrides` via query/POST only if we support the combined flow — see §8.)
4. **Job view** `Module1UpdateReserveJobView` (`processing/views.py:891`): accept
   `method_overrides` (JSON string), parallel to `cdf_overrides` — requires a source job, may
   coexist with `cdf_overrides`. Store under `input_meta["method_overrides"]`.
5. **Task** `run_module1_update_reserve_task` (`processing/tasks.py:361`): read
   `input_meta["method_overrides"]` and pass to `run_update_reserve_summary(..., method_overrides=…)`.
6. **Serializer/validation**: validate `method_overrides` shape (file → accident_period →
   `{implied_lr: float|null, selected_method: enum|null}`); reuse the method enum constant.

### `method_overrides` shape
```json
{
  "<workbook filename>.xlsx": {
    "<accident_period>": { "implied_lr": 0.75, "selected_method": "Paid BF" }
  }
}
```
`implied_lr` is a **fraction** (0.75 = 75%), consistent with Reported LR (F = Reported/EP) and the
engine's `G × B`. Either field may be null (partial override; keeps the engine default).

## 6. Frontend changes (sigma-17-dashboard)

1. **Extend `ReserveMethodTable`**: make **Implied LR** an editable numeric input (percent in,
   fraction stored — mirror `UlrSelectionTable.updateUlr`'s `/100`). Add recompute for
   `ultimateELR = impliedLR*EP`, `ultimatePaidBF = (1-1/paidCDF)*EP*impliedLR + osClaims`,
   `ultimateReportedBF = (1-1/reportedCDF)*EP*impliedLR + reportedClaims` on Implied LR edit
   (these must match §1 L/M/N **exactly**, including Paid BF `+ osClaims`). Keep the existing
   method-pick + IBNR/ULR/CDF recompute.
2. **API** `src/api/module1.ts`: `fetchReserveSummaryRows(sourceJobId, filename)` →
   the new endpoint; extend `startUpdateReserveJob` callers to include `method_overrides`.
3. **Store** `src/state/wizards/updateReserve.ts`: add `methodOverrides` (keyed file →
   accident_period → `{impliedLr, selectedMethod}`) alongside `cdfOverrides`.
4. **Page** `UpdateReservePage.tsx`: add a "Method Selection" surface (third mode or a section
   after CDFs) — pick workbook, fetch rows → build `ReserveRow[]` → render the extended
   `ReserveMethodTable` → persist edits to `methodOverrides` → include in the submit FormData.
   Provide a "Skip — use defaults" affordance (Implied LR blank, method Paid CL), mirroring ULR's
   "Skip — Use Calculated".

### Client ↔ engine formula reconciliation (must be identical)
| Web (extended `ReserveMethodTable`) | Engine (`engine.py`) |
|---|---|
| `ultimateELR = impliedLR*EP` | `L = G*B` |
| `ultimatePaidBF = (1-1/paidCDF)*EP*impliedLR + osClaims` | `M = (1-1/H)*B*G + D` |
| `ultimateReportedBF = (1-1/reportedCDF)*EP*impliedLR + reportedClaims` | `N = (1-1/I)*B*G + E` |
| `getUltimate` picks by method | `P = IF(O…)` |
| `getIBNR = ult - reportedClaims` | `Q = P - E` |
| `getULR = ult / EP` | `R = P / B` |
| `getCDF = ult / paidClaims` | `S = P / C` |
| `ultimatePaidCL`, `ultimateReportedCL` (from server) | `J = C*H`, `K = E*I` |

## 7. Blank Implied LR

Engine default `G = None` → ELR = 0 and BF collapse to their emerged-claims term (`+D`, `+E`). The
web must render blank Implied LR the same way (treat empty as no a-priori → ELR 0, BF = emerged
term), so an un-edited row previews identically to today's output.

## 8. Key design decision — CDF composition

If the user edits **both** CDFs and methods in one session, the previewed H/I (and thus J/K/M/N)
must reflect the CDF edits to match the final output. **Recommended:** the reserve-summary reader
and endpoint accept the pending `cdf_overrides` and apply them before deriving H/I; the job applies
`cdf_overrides` then `method_overrides`. If we scope v1 to method-only (methods on stored CDFs),
document that CDF + method editing must be done in sequence.

## 9. Testing & rollout

- **Engine:** `run_update_reserve_summary(method_overrides=…)` writes G/O literals for targeted
  rows, leaves others at defaults, keeps L–S formulas; unknown accident periods ignored; invalid
  method rejected.
- **Reader:** `read_reserve_summary_rows` H/I derivation matches the engine's reverse+map on the
  same workbook (shared helper → identical); with `cdf_overrides` applied.
- **View/task:** `method_overrides` round-trips into `input_meta` and to the engine; coexists with
  `cdf_overrides`.
- **Frontend:** extended `ReserveMethodTable` recompute equals the engine formula table above
  (unit test the math); Implied LR percent↔fraction; skip path.
- **/verify:** run Summary → Update Reserve with an Implied LR + method edit → confirm the output
  Reserve Summary G/O cells and the Excel-recomputed L/M/N/P/Q/R/S match the web preview.
- **Backward compatible:** no `method_overrides` → current behavior unchanged.

## 10. Out of scope / follow-ups

- The Paid BF `+D` vs `+C` actuarial question (needs sign-off; preserve current behavior).
- Pre-existing IBNR Summary rollup formula-vs-value behavior in `Combined_Summary`
  (`engine.py:722-765`) — unaffected by this change.
- Excel-free Dataset path for method overrides (align with the Dataset initiative later).
