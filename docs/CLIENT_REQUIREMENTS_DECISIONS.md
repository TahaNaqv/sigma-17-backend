# Client Requirements (Aug 2026) — Decision Record & Roadmap

> **Purpose:** Single authoritative record of the nine client requirements, the decisions taken
> against each, the evidence those decisions rest on, and the delivery sequence. Every per-item
> plan in `docs/*_PLAN.md` refers back to this document for its decisions; this document does not
> repeat their designs.

Status: **decided** (2026-08-21). Owner: engineering. Supersedes ad-hoc scoping discussion.

---

## 0. Client requirements (verbatim)

1. Stress testing / sensitivity of Risk adjustment — Risk adjustment % (+/-%, i.e. increase by %,
   decrease by %), discounting (5 basis point +/-) and Loss ratios (5% Loss ratio +/-).
2. Payment pattern calculation already being done; need a place as an Excel input if want to use a
   different pattern (similar to where LDFs are calculated and a space to select LDFs).
3. Cashflow calculation already being done; need a place as an Excel input if want to use different
   cash flows.
4. UPR methods are hard coded in code; want to have them on interface, if someone wants to select a
   different UPR methodology for a different Line of Business.
5. Quarterly triangles are already being calculated; want to have functionality of monthly and
   yearly triangles.
6. Summary of high/top 10 claims paid and OS in the data, and a functionality if we remove those in
   our experience, triangles should be adjusted through a strikethrough feature.
7. Simple and weighted averages are already being calculated; want a feature of simple average by
   removing/strikethrough high and low averages, last 4 period, last 8 period average, or customise
   average for the user.
8. Overall view attractive, some colouring etc.
9. Testing run — internal task (client side).

---

## 1. Evidence base

All findings below were verified against the repository and the client reference data set frozen in
`benchmarks/`. That data set is the same one the golden net is built from, so it is the client's own
extract — **but it is a reference extract, not necessarily today's production file.** Every design
decision in this document is written so that it holds regardless; where a finding drives behaviour,
the behaviour is derived from the data at runtime rather than hard-coded from these observations.

Artefacts inspected:

| Artefact | What it established |
|---|---|
| `benchmarks/fixtures/summary_ref/premium/Premium - Full.xlsx` (14,791 rows) | UPR branch coverage; `RESERVINGCLASS == POLICYCLASS`; PRODUCTTYPE vocabulary |
| `benchmarks/fixtures/summary_ref/claims_paid/Claim Paid Full.xlsx` (6,580 rows / 1,645 claims) | Claim grain; `CLAIMNUMBER` quality; large-loss concentration |
| `benchmarks/fixtures/summary_ref/claims_os/Claim OS Full.xlsx` (6,272 rows / 1,685 claims) | OS grain (claim x as-at x treaty) |
| `benchmarks/fixtures/m2_process_ref/Previous_period.xlsx` | `LIC_BOP` keyed on quarterly `Accident_Period` strings, 2,144 rows, 24 quarters |
| `benchmarks/goldens/summary_ref/` | Confirmed Simple Avg defect and the empty Health paid triangle in frozen output |

---

## 2. Findings that pre-empt the requirement list

These were discovered while scoping the nine items. Two of them are correctness defects on live
reference data and are sequenced **ahead** of the client's list.

### F1 — `Health` paid claims never join to `Health Insurance` (P0, correctness)

```
Premium file      RESERVINGCLASS = "Health Insurance"
Claims OS file    RESERVINGCLASS = "Health Insurance"
Claims PAID file  RESERVINGCLASS = "Health"            <-- does not join
```

`run_generate_summary` iterates `df['RESERVINGCLASS'].unique()` where `df` is the **premium** frame,
then filters claims by equality on that value. `Health` never appears in premium, so:

* every `Health` paid claim is silently discarded, and
* the `Health Insurance` workbooks receive OS but **zero** paid claims.

Confirmed in the frozen golden — `Health Insurance Payment GROSS 2017-12.xlsx`, Paid Claims
Triangle, sum of all cells = `0.0`. Health is the largest class in the book (6,010 of 14,791
premium rows, 41%). Its paid LDFs are meaningless and its reserves derive from outstanding only.

### F2 — all three UPR branches are unreachable (P0, correctness)

`calculate_upr` selects between three bases by string equality. On the reference book **none of the
special branches ever fire**; 100% of rows fall through to pro-rata daily.

| Code matches | Data actually contains |
|---|---|
| `PRODUCTTYPE in ["Contractors All Risks", "Erection All Risks"]` | `"CONTRACTORS'ALL RISK"`, `"ERECTION ALL RISKS"` |
| `POLICYCLASS == "Marine cargo"` | `POLICYCLASS = "Marine, aviation and transport insurance"`; marine lives in `PRODUCTTYPE` as `"MARINE CARGO"` |

Case, punctuation, plurality and column are all wrong. The previously-noted
`"Marine cargo"` / `"Marine Cargo"` inconsistency between the EOP block and the quarterly/run-off
loops is therefore moot in effect — neither spelling was ever going to match — but it is still a
latent bug and is removed by WP2.

**Consequence for requirement 4:** the client's problem is not that the methods are inflexible. It
is that Engineering and Marine business has been reserved on straight pro-rata since inception
because the intended special-case logic is dead code.

### F3 — `Simple Avg LDF` / `Simple Avg CDF` are wrong, but have never been consumed

`calculate_age_to_age_factors` zero-fills the undeveloped lower-right triangle, and the simple
average then means over those zeros. From the frozen golden for
`Banker's Blanket Payment GROSS 2017-12.xlsx`:

```
Simple Avg LDF    0.0  0.0  0.125  0.127  0.0  0.0  0.0     <-- factors below 1
Simple Avg CDF    0.0  0.0  0.000  0.000  0.0  0.0  0.0     <-- collapses to zero
Weighted Avg LDF  NaN  NaN  NaN    3.213  1.016 NaN  NaN    <-- the only usable row
```

A full-repository search establishes that `Simple Avg` is **written and never read back**. The only
consumers are `processing/output_column_kinds.py` (preview formatting),
`processing/services/reserve_workbook.py` (whole-grid display) and tests. The engine's
`run_update_reserve_summary` reads **only** the `Selected CDF` row.

**Consequence:** the row has never entered an ultimate, IBNR, LIC, LRC or disclosure figure.
Correcting it changes **no filed number** and requires no restatement or sign-off. It is a
display-layer defect in a decision aid. It should nonetheless be **communicated** to the client:
LDF selections made in prior periods by eye against this row were informed by a bad benchmark.

### F4 — `D&O` has premium but no claims

Produces reserve workbooks with no claims data at all. Legitimate for a class with no claims
experience, but indistinguishable today from a join failure like F1. WP0 makes the two distinct.

---

## 3. Locked decisions

### D1 — Sensitivity shock semantics (requirement 1)

| Lever | Convention | Defaults | Rationale |
|---|---|---|---|
| Risk adjustment | **Relative multiplier** on the RA loading | ±10%, ±25% | RA is already a % loading; relative shocks are what IFRS 17 §128-132 sensitivity disclosure expects, and match the client's "increase by %". RA 6% → +10% → 6.6% |
| Discounting | **Absolute basis points, parallel shift** of the annual spot curve | ±5bp, ±25bp, ±50bp | Standard yield-curve convention. 5bp alone is too small to be a meaningful disclosure; supply the ladder |
| Loss ratio | **Absolute percentage points** on Selected ULR | ±5pp, ±10pp | "A 5% loss ratio movement" means 5 points in actuarial usage, and point shocks are what regulators tabulate. 65% → 70% |

**Verified 2026-08-21** against the reference book: all three levers are stored as **fractions**
(`RA % = 0.0463`, `CY Discount = 0.0608`, `Selected ULR ~ 0.53`), so `+5bp = +0.0005` and
`+5pp = +0.05`. The full measured propagation map is in `SENSITIVITY_TESTING_PLAN.md` §1.3 and is
the acceptance specification for WP4.

**Shock the CY discount curve only, never PY.** `Change in Discounting Impact = CY − PY`; PY is the
prior period's locked-in basis and moving it fabricates a comparative that never existed.

Every shocked row must render `base → shocked` in absolute terms alongside the shock definition. A
disclosure must never depend on the reader inferring the convention.

### D2 — Override reach for pattern and cash flow (requirements 2, 3)

* **Payment pattern override drives both** the LRC run-off (`avg_df`) **and** the LIC incremental
  matrix (`additional_matrix`). If the actuary asserts a payout pattern, it is incoherent for LIC
  cash flows to develop on a different one.
* **Cash flow override drives the LIC path only** (`future_cf_df`). A cash-flow override is by
  definition a statement about LIC run-off; LRC run-off is UPR x combined ratio x pattern — a
  different object.
* **Precedence:** cash flow > payment pattern > engine-derived. Both supplied for the same key →
  cash flow wins **and the run warns**; it never silently picks.
* **Hard validation gates** (block the run, do not emit a broken workbook):
  * pattern rows sum to 1.0 per class (tol 1e-6), with a normalise action offered;
  * overridden cash flows sum back to `IBNR + ULAE + Outstanding + SS` per key (tol 1e-6). This tie
    is what makes the LIC reconciliation foot; breaking it silently corrupts the movement disclosure.

### D3 — Line-of-business key and UPR methods (requirement 4)

* **Key: `RESERVINGCLASS`.** Not a judgement call — `RESERVINGCLASS == POLICYCLASS` on all 14,791
  reference rows; they are the same field. `RESERVINGCLASS` is additionally the segmentation spine
  of every other surface (reserve workbooks, ULAE-RA, Allocation EP, UW Summary, LIC, IFRS 17
  disclosure, dataset indexes). One spine, no second axis.
* **Exception layer keyed on `PRODUCTTYPE`**, resolved
  `(RESERVINGCLASS, PRODUCTTYPE) → (RESERVINGCLASS, *) → system default`.
* **All matching is case-insensitive on a normalised string** (casefold, collapse whitespace, strip
  punctuation). Exact-literal matching is the direct cause of F2 and is removed, not repeated.
* **Seeded default: `pro_rata_daily` for every class** — provably bit-identical to today because it
  is what 100% of reference rows already do (F2). No client mapping is required to ship safely.
* **Six methods ship:** `pro_rata_daily`, `sum_of_digits`, `full_premium_in_period`, `eighths`,
  `twenty_fourths`, `flat_percentage`.

### D4 — Triangle granularity (requirement 5)

**Diagnostic-first. Booking stays quarterly.** Confirmed, not hedged: `LIC_BOP` carries 2,144 rows
keyed on quarterly `Accident_Period` **strings** across 24 quarters. Re-granularising the booking
basis orphans every one of those rows and breaks the IFRS 17 movement comparatives.

Deliver monthly / quarterly / yearly triangles as a **selection and diagnostic view** whose chosen
factors feed back into the quarterly `Selected LDF` row. Simultaneously introduce a `PeriodGrain`
abstraction replacing the ~28 hard-coded quarterly sites, so a future full re-granularisation is a
configuration change plus a data migration rather than a rewrite.

### D5 — Large claims (requirement 6)

* **Ranking is grain-agnostic and slice-scoped.** The reference grain is
  `(claim x head of damage x treaty type x transaction)` for paid — 3,290 single-transaction slices
  and 1,645 two-transaction slices, so it is genuinely transactional — and
  `(claim x as-at x treaty type)` for OS. Therefore:
  * rank by `SUM` **within an explicit slice** (default `GROSS` / `Payment`); never sum across
    treaty type or head of damage, which would add gross to RI and net payments against salvage;
  * OS ranks on the **latest `As at`** per claim; summing snapshots multiply-counts one reserve;
  * never assume a row count per claim.
* **Selection modes:** top-N (default N = 10, configurable) **and** amount threshold. Threshold is
  the more defensible basis period-over-period and is offered alongside.
* **Treatment modes**, default first:
  1. `exclude_from_ldf_only` — **default.** Removed from the age-to-age calculation only; stays in
     the reserve base. Changes the factors, not the booked reserve.
  2. `exclude_and_add_back` — attritional triangle developed, excluded claims' actual paid + OS
     re-added as a separate `Large Loss` line, with an optional user-entered large-loss IBNR loading.
  3. `exclude_entirely` — available, loudly warned. Understates the ultimate.
* Reference concentration: top 10 claims = **29.9% of total paid**. The book is large-loss dominated
  and exclusions will move factors materially — the feature is high-impact and must be auditable.
* **Every exclusion is snapshotted with the job**: claim numbers, mode, threshold, actor, timestamp.

### D6 — Average bases (requirement 7)

Fix `Simple Avg` outright (F3) — no sign-off required. Ship the average bases as a **selection
surface**, not just extra workbook rows: all-periods, excluding-high-and-low, last-4, last-8,
volume-weighted variants, and free custom period selection via per-cell exclusion. Selected basis
writes through the **existing `ldf_overrides` path**; no new write contract.

### D7 — Visual system (requirement 8)

Proposed, built, then shown — not solicited. Scope fixed in `docs/UI_VISUAL_SYSTEM_PLAN.md`.
Colour is never the sole signal (accessibility, and these pages print in mono).

---

## 4. Delivery sequence

Data integrity precedes features: shipping selection tools on top of a broken join would let users
make confident judgements against wrong numbers.

| WP | Scope | Req | Plan | Gate |
|---|---|---|---|---|
| **WP0** | Class reconciliation + pre-flight gate (F1, F4) | — | `DATA_INTEGRITY_PREFLIGHT_PLAN.md` | Blocks all |
| **WP1** | Simple-Avg fix + average selection + strikethrough | 7 | `LDF_AVERAGE_SELECTION_PLAN.md` | — |
| **WP2** | UPR method registry + per-LOB policy (F2) | 4 | `UPR_METHOD_SELECTION_PLAN.md` | — |
| **WP3** | Payment-pattern & cash-flow overrides | 2, 3 | `PATTERN_CASHFLOW_OVERRIDE_PLAN.md` | — |
| **WP4** | Sensitivity / scenario runner | 1 | `SENSITIVITY_TESTING_PLAN.md` | **independent — verified 2026-08-21** |
| **WP5** | Large-claims summary + exclusion | 6 | `LARGE_CLAIMS_EXCLUSION_PLAN.md` | after WP1 (shares grid) |
| **WP6** | Triangle granularity (diagnostic) + `PeriodGrain` | 5 | `TRIANGLE_GRANULARITY_PLAN.md` | after WP1 |
| **WP7** | Visual system | 8 | `UI_VISUAL_SYSTEM_PLAN.md` | threaded throughout |

WP1 and WP5 share the triangle grid component; WP1 builds it, WP5 extends it.

**WP4 was re-scoped on 2026-08-21 after verification: it is independent and can start immediately.**
The earlier "after WP2, WP3" ordering assumed its scenario payload had to carry their parameters; it
does not. WP4 shocks only RA, the CY discount curve and Selected ULR — none of which WP2 or WP3
touch. It can therefore be delivered first, after WP0.

Requirement 9 is the client's own acceptance run; our obligation is that the golden net is green and
that each WP ships with the regression coverage listed in its plan.

---

## 5. Engineering standards applying to every work package

1. **Golden net.** `processing/golden.py` compares at value level. Every WP that can change numbers
   ships with (a) a default path proven bit-identical against the existing goldens, and (b) freshly
   captured goldens for each new option. WP0, WP1 and WP2 change numbers **by design** — each states
   its expected delta and re-captures deliberately, with the change dated in its plan.
2. **The override contract.** Requirements 2, 3, 4, 6 and 7 are all instances of the pattern already
   proven three times (`ldf_overrides`, `method_overrides`, `selected_ulr_rows`, UW payload):
   job form field → `input_meta` JSON → Celery task → engine kwarg, with **one shared helper** used
   by both the web preview and the workbook write so preview always equals output. No WP invents a
   second mechanism.
3. **New input kinds carry a fixed checklist:** `Dataset.Kind`, `DB_TO_EXCEL` map,
   `REQUIRED_FIELDS_FOR_KIND`, `COLUMN_META`, `KIND_RECIPE`, migration, `JobDraft.Key` if it has a
   wizard, frontend `FILE_SCHEMAS`, wizard store slice.
4. **Auditability.** Every actuarial judgement input — selected factors, average basis, excluded
   claims, UPR policy, shock sets — is persisted in `input_meta`, snapshotted with the job, and
   attributed. See §6.
5. **Performance.** `m1.reserve_loop` is the dominant stage and is openpyxl-write-bound. No WP may
   multiply workbook writes without a stated budget. WP4 in particular must reuse
   `_build_allocate_outputs` in memory and run Module 1 exactly once across all scenarios.
6. **Excel engine constraints.** `core/excel.py`: `WRITE_ENGINE` (XlsxWriter) can only create new
   files. Any load-and-modify or two-tables-per-sheet path stays on openpyxl explicitly.
7. **RBAC.** New endpoints reuse `module1.run` / `module2.run` unless a plan states otherwise; new
   permissions are seeded in `accounts/management/commands/seed_rbac.py` and added to the same roles
   as their siblings.

---

## 6. Cross-cutting: maker-checker (recommended, scoped separately)

Selected LDFs, average bases, excluded claims, UPR policy and shock sets are actuarial **judgement**
feeding a signed IFRS 17 / SAMA disclosure. `input_meta` records *what* was chosen; nothing records
*who approved it*. An approval state on `Module1Job` (`draft → submitted → approved`, with approver
and timestamp, and download gated on approved for disclosure-grade outputs) is table stakes in this
domain and is materially cheaper to add now than to retrofit across seven new judgement surfaces.

Not part of WP0-WP7. Flagged for scheduling.

---

## 7. Items still requiring client input

None blocks any work package.

| Item | Needed for | Fallback if unanswered |
|---|---|---|
| Confirm F1/F2 against a **current production** claims + premium extract | Sizing WP0's reconciliation table | WP0 ships the gate anyway; it reports rather than assumes |
| Which classes should take which UPR method | Realising WP2's benefit | Ships seeded pro-rata everywhere = today's behaviour; client fills in via UI |
| Whether excluded large losses carry a separate IBNR loading | WP5 mode 2 refinement | Field ships optional, defaults to nil loading |

**Advisory to send:** F3 means prior-period LDF selections were made against a defective Simple
Average benchmark. No restatement is implied — the numbers entered are the numbers used — but the
selections warrant review at the next valuation.
