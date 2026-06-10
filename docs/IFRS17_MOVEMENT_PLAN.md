# IFRS 17 Movement Analysis Template — Implementation Plan

**Status:** Proposed (post-discovery) · **Scope:** both repos · **Source artifact:**
`template.xlsx` (SAMA disclosure) → structure in `ifrs17_movement_template.schema.json`.

Deliverable: an IFRS 17 **movement-analysis disclosure** — a fixed roll-forward
(opening 01/01 → P&L/OCI → cash flows → closing 30/06) rendered **once per
`(Reserving Class, UWY)` pair**, two parallel sheets **Gross** + **RI**, five
measurement buckets each, with sign indicators and subtotal formulas.

---

## 0. THE KEY DISCOVERY (changes the whole approach)

`module2_engine/engine.py :: run_module2_process()` **already computes nearly every
number the SAMA disclosure needs**, at the right grain. This is a *disclosure
re-presentation* problem, **not** a new actuarial engine. Concretely, the existing
`module2_process` job (chained off a Module 1 `Combined_Summary.xlsx`) already produces:

| Existing artifact (in-memory frame) | Grain | What it gives the SAMA disclosure |
|---|---|---|
| `result_df` → **"Movement Analysis"** sheet | `(class, UWY, accident_period, GROSS/RI)` | Outstanding, SS, Payment, S&S, ULAE, RA(OS), RA(IBNR), Discounting Impact (both `_curr`/`_prev`); **CY** vs **PY** accident-year splits (`CY O/S`, `CY IBNR`, `CY ULAE`, `PY …`); `Insurance Finance (Income)/Expense`; `Unwinding of Discount` |
| `ifrs_summary_df` → **"IFRS Summary"** | `(class, UWY)` | All of the above pivoted into `GROSS - <m>` / `RI - <m>` columns + `Gross UPR/DAC/RI UPR/UCR` (`_curr`/`_prev`) + expense-CF cols (`Comm_Payable`, `Rec_GOP`, `Rec_Provision` `_curr`/`_prev`) |
| `pivot_summary_df` → **"LC"** | `(class, UWY)` | `PAA_LRC`, `GMM LRC` (undisc/disc CY/PY), **`LC …` (Loss Component)**, **`Loss Recovery Component`** |
| `loss_ratio_sheet` → **"Loss Ratio"** | `(class, UWY)` | `GWP`, `GEP`, `Commission Expense`, `Paid/OS/IBNR`, `Incurred/Ultimate Claims`, ratios, `RI %`, `RA %` |
| `upr_dac_eop_df` → **"UPR-DAC_eop"** | `(class, UWY)` | EOP `Gross UPR/DAC/RI UPR/UCR` |

**But** the engine's own `LRC/LIC BOP-EOP Reconciliation` sheets are *coarser* than SAMA:
they `groupby("RESERVINGCLASS")` (collapsing UWY) and use only ~5 LRC components / ~9 LIC
components as BOP/EOP snapshots — **not** the SAMA 5-bucket, ~67-line, per-`(class,UWY)`
Gross+RI roll-forward. The fix: the per-`(class,UWY)` data we need is already retained in
`ifrs_summary_df` + `LC` + `result_df` (none of those collapse UWY). We **project** those
into the SAMA schema; we do not recompute.

**Hard constraint discovered (engine.py:732–736):** re-reading the process output xlsx
**changes computed cells** (dtype round-trip). Therefore the SAMA layer must consume the
**in-memory frames**, never a re-parsed workbook. → drives the refactor in §4.

---

## 1. Decisions (resolved forks)

| Fork | Decision | Why |
|---|---|---|
| Dimensionality | **Per *present* `(class, UWY)` pair** (sparse grid, not full cross-product) | Template keys on both; data is sparse — 83 real pairs vs 108 full grid (§3a). Generate only pairs present in the data. |
| What it is | **Disclosure projection of `module2_process` intermediates** (computed-first hybrid) | §0 — the measures already exist; only ~⅓ of lines are judgment inputs. |
| Surface | **New `module2_movement` job that reuses the process pipeline in-memory**, + dedicated frontend view; later an override Dataset kind | Reuses Celery/snapshot/preview/RBAC; avoids the xlsx round-trip trap. |
| Source of truth | **Schema-as-code** (`schema.py` + generated `schema.ts`) | Engine projection target == UI render source; no drift. |
| Output grouping | One workbook, a Gross+RI tab-pair per class, UWYs sectioned | Cross-product model, sane artifact. |

**Data-source reality (measured from the proposed mapping artifact — `ifrs17_movement_mapping.proposed.json`):**
- **~60% of *value* lines Direct/Derived** (Gross 32/52, RI 27/46), excluding the ~15
  structural subtotal/header/balance lines per sheet which are pure formulas.
- **~35% Manual/override** (Gross 16, RI 15) + ~3 methodology-diff residual lines per sheet.
- The Expense-CF finding (§9.0) didn't raise the % wildly, but it **moved the entire
  cash-flow + RI section from guesswork into data-backed D-tier** — the qualitative win.

---

## 2. Architecture & data flow

```
Module1 Summary job ─► Combined_Summary.xlsx ─┐
                                              ▼
Datasets (snapshot-on-run):            _build_allocate_outputs()  ──► allocate_sheets (in-mem)
  previous_period (LIC_BOP, UPR-DAC) ─►  pivot_and_calculate_differences() ─► result_df
  expense_cf ───────────────────────►    create_ifrs_summary() ────────────► ifrs_summary_df
                                              │  (+ LC, Loss Ratio, UPR-DAC_eop frames)
                                              ▼
                              ★ build_sama_movement(intermediates, SCHEMA)   ← NEW, pure fn
                                  per (class,UWY): project measures → lines×buckets, Gross & RI
                                              ▼
                              render_sama_workbook()  (XlsxWriter: merged headers, signs,
                                  subtotal FORMULAS, SAMA comments)  ─► output_zip
                                              ▼
                       existing output/{files,sheets,rows} preview  ─► preview-first UI
```

The new `build_sama_movement` is a **pure function of the in-memory frames** the process
pipeline already builds — same computation path, so the SAMA numbers are consistent with
the existing Movement Analysis / IFRS Summary by construction.

---

## 3a. Canonical reference data (extracted from `sigma-17-desktop-app/` Excel)

Source of truth for defaults, scope, and golden-vector tests. Extracted from the Module-2
IFRS dataset (`Combined_Summary.xlsx`, `Previous period.xlsx`, `Expense-CF.xlsx`, and the
`output module 2.xlsx` / `ifrs 456.xlsx` outputs). **Note:** the legacy Module-1 *sample*
inputs use a different, generic taxonomy (Banker's Blanket, D&O, Motor Insurance, …) — do
**not** use those; the canonical movement taxonomy is the 12-class Module-2 one below.

**Reserving classes (12, canonical):**
`ENGINEERING`, `GENERAL ACCIDENT`, `GROUP MEDICAL`, `INDIVIDUAL MEDICAL`, `MARINE`,
`MEDICAL MALPRACTICE`, `MOTOR COMPULSORY (AGGREGATORS ONLY)`,
`MOTOR COMPULSORY (NON-AGGREGATORS ONLY)`, `MOTOR COMPULSORY + OTHERS`, `MOTOR MANAFETH`,
`PROPERTY`, `VISIT VISA`.

**UWY range:** `2018–2026` (9 years), **sparse per class** — generate per *present* pair:
- **83 distinct `(class, UWY)` pairs** present (vs 108 full grid). Most classes 2018–2024;
  `MEDICAL MALPRACTICE` 2018–2026, `VISIT VISA` 2019–2025, `MOTOR MANAFETH` 2021–2024 (new product).
- **Reporting / `accounting_period` = 2024** (Movement Analysis accident-period years run
  2018–2024; current accident year = 2024). UWY extends to 2026 on the premium/UPR side
  (future unearned), but LIC/claims top out at accident year 2024 — so later-UWY pairs are
  LRC-only. The desktop dataset is a **full-year (YE)** run, not the half-year of the
  original `template.xlsx`; the schema is period-agnostic (opening→closing) regardless.

Golden-vector tests should pin a few concrete pairs (e.g. `MOTOR COMPULSORY + OTHERS`/2023,
`PROPERTY`/2022, `GROUP MEDICAL`/2024) against `output module 2.xlsx` numbers.

## 3b. Period cadence — period-agnostic, YTD, default YE (decided)

Cadence is **not** a schema/engine property — the table is just opening→changes→closing,
and the period is set by the BOP snapshot + chosen reporting date. IFRS 17 disclosures are
**YTD**: opening is always **01/01 of the reporting year** (= prior 31/12 close); only the
**closing date** moves (30/06 interim, 31/12 annual, 30/09 Q3). So:
- **One `reporting_date` parameter** (+ `accounting_period` year) on the job. Opening label
  stays "as at 01/01"; the closing-date label is rendered dynamically (workbook.py) — line
  **ids stay stable** (`insurance_contract_..._as_at_30_06` etc.), only the displayed date
  varies. No engine math / schema fork.
- **Default = year-end (31/12)** — the SAMA statutory anchor and what the desktop dataset is.
  Half-year/quarterly reuse the identical machinery with a different reporting_date + EOP data.
- Comparatives (prior-period movement alongside) deferred; standard table is current-period.

## 3. Measure → SAMA line/bucket mapping (the core artifact)

**Authoritative source = the 111-column `IFRS Summary` frame** (`ifrs_summary_df`) + the
`LC` frame. It carries, at `(class, UWY)` grain, *both* Gross and RI for every measure
(parallel `GROSS - <m>` / `RI - <m>` columns), all UPR/DAC/RI-UPR/UCR (`_prev`/`_curr`),
the full cash-flow section, and all provisions incl. `Claim_Pay`. So `build_sama_movement`
consumes essentially **`ifrs_summary_df` + `lc_df`** — nothing needs re-deriving from raw
inputs. Machine-usable mapping emitted to `ifrs17_movement_mapping.proposed.json`.

Tier: **D** = direct column · **Δ** = derived (formula) · **M** = manual/override.
Gross buckets: `LRC_excl_LC, Loss_Component, LIC_excl_RA, Risk_Adjustment`. RI buckets:
`Assets_Remaining_Coverage, Loss_Recovery_Component, Amounts_Recoverable_IC, Risk_Adjustment`.
Below, "source" lists the **Gross** column; the **RI** sheet uses the parallel `RI …` column
named in the same row.

### Opening build-up
| SAMA line | Bucket | Tier | Gross source → (RI parallel) |
|---|---|---|---|
| UPR | LRC/ARC | D | `Gross UPR_prev` → `RI UPR_prev` |
| Premium receivable / payable | LRC/ARC | M | — |
| DAC / RI Commission | LRC/ARC | D | `DAC_prev` → `RI Commission` |
| Commission Payable / receivable | LRC/ARC | D | `Comm_Payable_prev` → (RI: `RI Rec Provision_prev`) |
| Premiums received/paid in Advance | LRC/ARC | M | — |
| Premium Deficiency / Loss (Recovery) Component opening | LC/LRecC | D | `LC Discounted_PY` → `Loss Recovery Component` (LC frame) |
| O/S Claims | LIC/AmtRec | D | `GROSS - Outstanding_prev` → `RI - Outstanding_prev` |
| IBNR | LIC/AmtRec | Δ | `GROSS - Payment_prev + GROSS - S&S_prev` → RI parallels |
| ULAE | LIC/AmtRec | D | `GROSS - ULAE_prev` → `RI - ULAE_prev` |
| S&S O/S | LIC/AmtRec | D | `GROSS - SS_prev` → `RI - SS_prev` |
| S&S IBNR | LIC/AmtRec | D | `GROSS - S&S_prev` → `RI - S&S_prev` |
| Claims payable (pipeline) | LIC/AmtRec | D | `Claim_Pay_prev` → `RI_Payable_prev` |
| Discounting impact | LIC/AmtRec | D | `GROSS - Discounting Impact_prev` → `RI - …_prev` |
| Provision for non-performance risk (RI only) | AmtRec | D | — → `RI_Rec_GOP_prev` / `Rec_GOP_prev` |
| Prudence/Mgmt Margin, Advance-to-hospitals, Doubtful-Debt, Profit-Comm provision | LIC/AmtRec | M | — |
| Risk Adjustment (RA) | RA | D | `GROSS - RA (OS)_prev + GROSS - RA (IBNR)_prev` → RI parallels |

### P&L / OCI changes
| SAMA line | Tier | Gross source → (RI parallel) |
|---|---|---|
| Written / Ceded premium | D | `GWP` → `RI GWP` |
| Change in (RI) UPR | Δ | `-(Gross UPR_curr − Gross UPR_prev)` → RI parallel |
| (RI fixed) Commission & change | D | `Commission Expense` → `RI Commission` / `RI Fixed Commission received` |
| Change in Premium Debtors' Provision | M | — |
| Incurred in CY, Paid in CY | Δ | `CY Payment` (re-expose §9.1) → `RI - CY Payment` |
| Incurred in CY, OS at end | D | `GROSS - CY O/S` → `RI - CY O/S` |
| Incurred in CY, IBNR at end | D | `GROSS - CY IBNR` → `RI - CY IBNR` |
| ULAE for OS & IBNR at end | D | `GROSS - CY ULAE` → `RI - CY ULAE` |
| Directly-attributable expenses (excl. acq.) | D | `Directly Attributable Expenses, excluding Insurance Acquisition cash flows` |
| Other Acquisition Cash Flows | D | `Other Acquistion Cash Flows` |
| Change in DAC | Δ | `-(DAC_curr − DAC_prev)` |
| Losses on new onerous / reversals (LRC for RI) | Δ | `LC Discounted_CY − LC Discounted_PY` (split ≥0/<0) → `Loss Recovery Component` Δ |
| Change in Ultimate for Past Service (Paid/ΔOS/ΔIBNR/ΔULAE) | D | `GROSS - PY O/S`, `PY IBNR`, `PY ULAE`, `PY Payment` (§9.1) → RI parallels |
| Change in Provision for risk of RI non-performance (RI only) | Δ | — → `RI_Rec_GOP_curr − RI_Rec_GOP_prev` |
| Investment components | M | — |
| **Insurance/Reinsurance service result** | subtotal | formula |
| Insurance finance income/expense (total) | D | `GROSS - Insurance Finance (Income)/Expense (+ Unwinding of Discount)` → RI parallels |
| Finance split P&L vs OCI | M | (total known; split is judgment) |
| FX / Other movements / Item 1–2 (Specify) | M | — |
| **Total changes in P&L & OCI** | subtotal | formula |

### Cash flows + closing
| SAMA line | Tier | Gross source → (RI parallel) |
|---|---|---|
| Premium Received / Paid | D | `Premium Received` → `RI Premium Paid` |
| Claims paid / received | D | `Claims Paid` → `RI Claims received` |
| Insurance Acquisition Cash flows | D | `Insurance Acquisition Cash flows` |
| (RI) Fixed/Profit Commission received | D | — → `RI Fixed Commission received` |
| Other Cash Flows | D | `Other Cash Flows` |
| **Total Cash Flows** | subtotal | formula |
| **Closing** | closing | `opening + ΔPnL − CF` **and independently** `_curr` EOP balances → residual (§ recon) |

**Measured coverage** (from `ifrs17_movement_mapping.proposed.json`): of the *value* lines
(excluding ~15 structural formula lines/sheet), **Gross 32/52 (62%)** and **RI 27/46 (59%)**
are D/Δ; the rest are genuinely judgmental M lines (prudence/mgmt margins, premium
receivable/advance, doubtful-debt & policyholder-profit-commission provisions, FX, the
P&L-vs-OCI finance split, "Specify" items) + 3 methodology-diff residual lines/sheet.
⚠️ The artifact is **PROPOSED — pending actuarial sign-off**; one normalization key
(`Change in OS in CY` → PY O/S) needs fixing in the generator before it's authoritative.

### Reconciliation — residual, not a hard gate (important)
Closing is computed two ways: roll-forward (`opening + ΔPnL − CF`) and the **independent**
EOP build-up from `_curr` balances. They will **not** be equal in computed-only mode,
because ~35–45% of lines are Manual (§1) and default to 0 — so a naive "must-equal" gate
would fail spuriously for any class with material judgment components. The actuarially
honest treatment (and what the template itself does): route the per-`(class,UWY,bucket)`
**residual** `= EOP_independent − rollforward_computed` into the existing **"Other
methodology diff" / "Item (Specify)"** lines that appear in every section of the SAMA
sheet — i.e. the template has these lines precisely to absorb unexplained movement.

- **Computed-only phase:** residual is reported (shown in those diff lines + a per-pair
  "% unexplained" metric in the UI), never silently zeroed. Hard-fail only on *structural*
  errors (opening ≠ Σ build-up; a bucket column missing).
- **Override phase (§7):** each override reduces the residual; the gate tightens to "residual
  within tolerance once overrides are applied," and the `note` records the driver (SAMA req).

This makes the recon a **visible quality signal that drives the override workflow**, not a
brittle pass/fail that blocks legitimate computed-only runs.

---

## 4. Backend design

### 4.1 Engine refactor (enables in-memory reuse — avoids the round-trip trap)
In `module2_engine/engine.py`, extract the shared pipeline so frames are reusable:
```python
def _process_intermediates(combined_summary_bytes, previous_period_bytes,
                           expense_cf_bytes, accounting_period, selected_ulr) -> ProcessFrames:
    # everything run_module2_process does up to the writer, returning a dataclass of frames:
    # allocate_sheets, result_df, ifrs_summary_df, lc_df, loss_ratio_df, upr_dac_eop_df
run_module2_process(...)            # unchanged public behaviour; now calls _process_intermediates
```
New module `module2_engine/movement/`:
```
schema.py     # SAMA schema-as-code (buckets, lines, signs, subtotal formulas, SCHEMA_VERSION)
mapping.py    # §3 table as data: line_id -> (bucket -> source spec: column | derived expr | manual)
compute.py    # build_sama_movement(frames, schema) -> {(class,uwy): {sheet: {line: {bucket: val}}}}
workbook.py   # render_sama_workbook(projection, schema) -> xlsx bytes (XlsxWriter)
```
- `compute.py` evaluates the schema: opening = Σ build-up; computed lines via `mapping.py`;
  subtotals/closing via schema formulas; **manual lines default 0** (filled by overrides,
  phase 4). `Decimal`/stable ordering (perf initiative: bit-identical).
- `workbook.py` writes a fresh workbook (XlsxWriter is fine for new files): merged bucket
  headers, the sign column, number formats, **live subtotal formulas** (artifact recomputes
  in Excel), and the SAMA driver comments on methodology-diff lines.

### 4.2 Job type & task (reuse `Module1Job`)
- `processing/models.py`: add `MODULE2_MOVEMENT = "module2_movement", "IFRS 17 Movement Analysis"`.
- `processing/tasks.py`: `run_module2_movement_task` — mirrors `run_module2_process_task`
  (resolve `combined_summary` via chain/dataset, `previous_period` + `expense_cf` snapshots,
  `accounting_period`, `selected_ulr`) → `_process_intermediates(...)` →
  `build_sama_movement` → `render_sama_workbook` → zip → `_finalize_success`.
- `config/settings.py`: route the task to the `compute` queue.
- `input_meta` carries the same inputs as `module2_process` + `schema_version` +
  `scope:{reserving_classes, uwys}` + (phase 4) `overrides_dataset`.

### 4.3 API (reuse > new)
- New: `POST /api/module2/jobs/movement/` → `Module2MovementJobView(APIView)`,
  `HasPermission(["module2.run"])`, validates dates/scope/dataset-ids (resolve snapshots
  fail-fast), `.delay()`, **202**. Modeled on `Module2ProcessJobView` (views.py:~1119).
- **Reused unchanged:** `module2/jobs/<pk>/`, `.../output/{files,sheets,rows}`, `.../download/`
  — the SAMA workbook previews through the existing ZIP-agnostic `output_preview.py`.

### 4.4 RBAC / tenancy / reproducibility / retention
Identical to `module2_process`: `module2.run` to run, `CanReadModule1Job` to view/download;
`organization` FK + `_scope_jobs_qs` isolation; **snapshot-on-run** freezes inputs;
`schema_version` stamped so old jobs re-render against their structure; retention sweeper inherited.

### 4.5 Migration
`processing/migrations/000X_jobtype_movement.py` — additive `AlterField` on `job_type` choices. No backfill.

---

## 5. Schema-as-code (single source of truth)
`module2_engine/movement/schema.py` (dataclasses: `Bucket`, `Line{id,label,level,kind,signs,
formula,source}`, `SheetSchema`, `SCHEMA_VERSION`) + generated TS mirror
`src/features/movement/schema.ts`. `scripts/gen_movement_schema.py` emits both from
`ifrs17_movement_template.schema.json`; CI asserts sync. `mapping.py` references `Line.id`s.

---

## 6. Frontend (`sigma-17-dashboard`)
- **Route/gate:** `/movement-analysis` under `DashboardLayout`, `RequirePermission
  permission="module2.run"`; sidebar under Module 2. Mirrors `IbnrAllocationPage`.
- **Run wizard** `src/pages/MovementAnalysisPage.tsx` (3-step like `SummaryGeneratorPage`):
  source (chain a process job / pick datasets) → scope (classes × UWYs) → review & run.
- **Output:** default = existing `OutputPreviewDialog module="module2"` (preview-first, zero
  new code). Phase 3 = dedicated `src/features/movement/MovementTable.tsx` rendering the
  *disclosure* layout from `schema.ts` (merged bucket headers, indented lines, sign column,
  visually-distinct subtotals, `(class,uwy)` selector, Gross/RI tab) — also the host for
  phase-4 cell overrides (reuse `DatasetRowGrid` dirty/save patterns).
- **API/types:** `src/api/movement.ts` (`createMovementJob`, `fetchMovementScope`, reuse
  module2 output fns); query keys `["movement-job", id]`. Schema types in `features/movement/schema.ts`.

---

## 7. Phase 4 — manual override layer (designed, deferred)
Dataset kind `ifrs17_movement_override` + `MovementOverrideRow(reserving_class, uwy,
sheet, line_id, bucket_key, value, note)`. `compute.py` merges overrides over computed
values (override wins; both kept for audit; `note` satisfies SAMA "list the drivers").
Snapshotted via `input_meta.overrides_dataset` → reproducible.

---

## 8. Testing
- **Engine unit** `module2_engine/tests/test_movement_compute.py`: opening Σ, subtotal
  formulas, **closing identity** (roll-forward == EOP), sign application, a golden vector
  from `template.xlsx` Gross numbers.
- **Schema sync:** `schema.py` ≡ `schema.ts` ≡ JSON; every formula references real line ids;
  every bucket covered; every mapping source column exists in `ProcessFrames`.
- **Workbook:** render → re-read, assert sheets/headers/subtotal-formulas/comments.
- **API** `processing/tests/test_movement_api.py` (mirror `test_module2_api.py` /
  `test_output_preview_api.py`): RBAC 403, org isolation, 202 + snapshot creation, preview
  serves output, reproducibility after dataset edit.
- **Frontend:** wizard happy-path + schema-render snapshot (vitest).

---

## 9. Remaining gaps & open questions (post-discovery)
0. **★ Extend `ExpenseCfRow` to the full Expense-CF schema (required fix).** The real
   Expense-CF sheet has ~22 measure columns (`Premium Received`, `Claims Paid`, `Insurance
   Acquisition Cash flows`, `Other Cash Flows`, `RI Premium Paid`, `RI Claims received`,
   `RI Fixed Commission received`, `Directly Attributable Expenses…`, `Other Acquistion Cash
   Flows`, `Rec_GOP`, `RI_Rec_GOP`, **`Claim_Pay`**, `RI_Payable`, `Comm_Payable`,
   `Rec_Provision`, `RI Rec Provision` — each `_prev`/`_curr`). The current
   `datasets.ExpenseCfRow` models only 6 of these. Consequences: (a) these columns are the
   D-tier source for the entire cash-flow + RI section of the disclosure; (b) the engine's
   `create_lic_table` already **requires `Claim_Pay_{suffix}`**, so the *dataset-driven*
   `module2_process` path is currently incomplete vs the upload path (latent bug —
   verify/fix). → Extend `ExpenseCfRow` (+ serializer, `columns.py`, template, frontend grid)
   to the full schema. Benefits the movement feature **and** completes the Dataset initiative
   for Module 2. Migration: additive nullable columns.
1. **Re-expose dropped CY/PY `Payment`/`S&S`** in `pivot_and_calculate_differences`
   (dropped at engine.py:676) — needed for "Incurred in CY paid in CY" and past-service
   paid lines. Small, safe change behind the golden net.
2. **Confirm `Claim_Pay` source** (used in `create_lic_table`) — which input feeds it.
3. **Acquisition-CF / DAE / premium-received cash-flow** lines: confirm whether to derive
   (Exp Ratio×GEP, GWP) or treat as overrides. Lean override for auditability.
4. **RI cash-flow** measures (ceded premium, RI commission received) — confirm availability
   vs override.
5. ✅ **RESOLVED** — canonical 12-class list, UWY 2018–2026 (83 sparse pairs),
   `accounting_period` = 2024, full-year run. See §3a. (Confirm with user only whether the
   *production* taxonomy/period matches this desktop dataset, or differs.)

---

## 10. Phased rollout
1. **Schema-as-code + generator** (+ `mapping.py` as data). Unblocks all.
2. **Engine: `_process_intermediates` refactor + `build_sama_movement` + `render_sama_workbook`**,
   computed-only (manual lines = 0), with the reconciliation gate. Golden tests.
3. **Job type + run API + wizard**, wired to existing preview. ← first user value.
4. **Dedicated `MovementTable` disclosure view.**
5. **Override layer** (dataset kind + editable cells + audit notes).
6. **Polish:** scope endpoint, chaining off `module2_process`, perf at scale, what-if.

## 11. File change map
**Backend new:** `module2_engine/movement/{schema,mapping,compute,workbook}.py`,
`scripts/gen_movement_schema.py`, `module2_engine/tests/test_movement_compute.py`,
`processing/tests/test_movement_api.py`.
**Backend edit:** `module2_engine/engine.py` (extract `_process_intermediates`; re-expose
CY/PY payment), `processing/models.py` (JobType), `processing/{views,urls,tasks}.py`,
`config/settings.py`, migration.
**Frontend new:** `src/pages/MovementAnalysisPage.tsx`,
`src/features/movement/{schema.ts,MovementTable.tsx}`, `src/api/movement.ts`.
**Frontend edit:** `src/App.tsx`, sidebar nav, `src/api/types.ts`.
