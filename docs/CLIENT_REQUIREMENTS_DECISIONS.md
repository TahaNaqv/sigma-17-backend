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

### F5 — `Weighted Avg LDF` is written one development column too far right

**Found 2026-09-01 while planning WP1.** The engine writes the weighted link ratio for
`dev i-1 → dev i` at development column `i`, while the age-to-age block, `Simple Avg` and
`Selected LDF` all put the `dev j → dev j+1` factor at column `j`. A one-column shift.

Visible directly in the frozen golden for `Banker's Blanket Payment GROSS 2017-12.xlsx`: the same
factor `1.015748` sits at `Simple Avg` column 3 and `Weighted Avg` column 4.

Because F3 leaves `Simple Avg` unusable, `Weighted Avg` is today the **only** usable benchmark
row — so copying it into `Selected LDF` is the natural and effectively the only action available.
Doing so applies every factor one development period late and overstates the total Paid CL
ultimate by **+178.2%** (829,920,872 against 298,356,105 correctly aligned). Seven workbooks are
affected; worst is `Miscellaneous Payment GROSS` at **+299.3%**.

The repository already holds a correct implementation of the same quantity —
`module1_engine/triangles.py::volume_weighted_ldf`, built for WP6 — so the fix is consolidation
onto one implementation, not new code.

**Consequence:** identical in kind to F3. Nothing reads the row, so no filed number changes; but
it is the benchmark prior LDF selections were made against, so it belongs in the same advisory.

### F6 — every un-edited web reserve develops at a flat CDF of 2.0

**Found 2026-09-01 (surfaced by WP5's `exclude_from_ldf_only` measuring +0.00% end-to-end).**
`Selected LDF` is seeded with the literal string `=1` and `Selected CDF` with `=PRODUCT(...)`.
Nothing ever evaluates them — the engine writes the workbook and reads it back with
`data_only=True`, which returns `None` for a formula no spreadsheet has opened — so
`selected_cdf_row_to_series` applies its blank → **2.0** default to every cohort.

Any Module 1 job not put through the Update Reserve editor therefore reports
`ultimate = 2 x paid-to-date` exactly. On the reference book that is 144,172,678 against a
paid-to-date of 72,086,339.

This is not a defect to fix in isolation — `2.0` is a deliberate, documented fallback — but it
establishes that **WP1's "apply as Selected LDF" is the only mechanism by which a web-only user
obtains a real actuarial factor.** It moves WP1 from a refinement to a prerequisite for the
product being actuarially meaningful without Excel.

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

**Verified 2026-08-21 (requirement 2).** The sheet named `Payment Pattern` holds a *conditional*
future-payout average across cohorts of every maturity, **not** a from-inception payment pattern —
it puts 48% of ENGINEERING claims in the first quarter against an actual from-inception 6%, and
runs 1.7x–3.4x shorter in duration than the true pattern across most classes. The LRC run-off
convolution needs a from-inception pattern, so it is currently fed the wrong object. A hand
replication reproducing the engine to the digit confirms the override moves **only** the
discounted LRC (`PAA_LRC` and `GMM LRC_Undiscounted` are pattern-independent because the pattern
sums to 1). A second pass corrected that: the override moves the **LIC** discounting path too
(`Discounting Impact` −115.6%, `Change in Discounting Impact` −168.7% on a long-tail test
pattern) and by more than it moves LRC (−5.0%). It also established that the engine's LIC matrix
*already is* the re-based from-inception pattern — provable to 0.00e+00 — which makes "supplying
the derived pattern is a no-op" the feature's strongest regression check. Full detail and the
corrected acceptance map: `PAYMENT_PATTERN_OVERRIDE_PLAN.md` §1.3 / §1.3b.

Consequently requirements 2 and 3 were split into WP3a / WP3b — see the roadmap.

**Verified 2026-08-21 (requirement 3).** Three findings reshaped WP3b:

1. **The reconciliation gate is the feature, not a validation nicety.** `Discounting Impact`
   subtracts the *supplied* cash-flow total, not `Future CF`, so any shortfall is absorbed
   into a figure labelled a discounting effect — and `GROSS LIC = components + Discounting
   Impact`. The genuine discounting effect is only −3.60% of Future CF; a 5% under-supply
   reports 2.4x that and understates LIC by 10,272,145. The error exceeds the entire real
   effect and wears its name.
2. **A class-grain, total-preserving cash-flow override is mathematically identical to the
   requirement-2 pattern override** (verified: same `FutureCF`, same `Discounting Impact`).
   WP3b therefore only earns its 15.5 days if the client wants finer grain or externally
   sourced *amounts* — see `CASHFLOW_OVERRIDE_PLAN.md` §9.
3. **Overriding the LIC matrix leaks into LRC.** `avg_df` is derived from that matrix, so a
   cash-flow override moved `GMM LRC_Discounted_CY` by −2.388% with no LRC input supplied.
   The earlier "cash flow drives LIC only" note was an intent, not a behaviour. Fixed by
   computing `avg_df` from the pre-override matrix, which makes `GMM LRC_Discounted_CY` a
   structural invariant of WP3b.

**Requirement 3 descoped, 2026-08-21.** The engine's cash-flow projection has exactly one
degree of freedom per (class, treaty) — the from-inception pattern, which requirement 2 now
exposes (no-op identity to 2.22e-16). UWY carries **zero** timing information (identical rows
across 520 of 592 groups, spread `0.000e+00`), and `IBNR Summary` — the sole source of timing
— has no UWY column, so a finer grain would be *inconsistent* with the CDFs that produced the
amounts, not merely redundant. No external cash-flow projection exists to import: the client's
`Expense-CF` input is actual ledger cash flows, a different object. What is genuinely missing
is visibility (`FutureCF` is 74,280 cells against a 20,000-cell preview guard, so it cannot be
read in-app) and reach (the override is process-step-only and labelled "payment pattern").
**WP3b is therefore descoped from ~15.5 days to ~3**: a cash-flow projection view plus
surfacing the existing override on allocate. Sections 1-8 of the plan stay shelved against a
specific trigger — see `CASHFLOW_OVERRIDE_PLAN.md` §10.

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
* **Seeded default: `pro_rata_daily` for every class** — **proven** bit-identical, not argued: a
  prototype resolver reproduces all three current blocks across 14,791 rows x 12 valuation dates
  with `max |diff| = 0.000e+00` (`UPR_METHOD_SELECTION_PLAN.md` §1.3).
* **Eligibility (`ISSUEDATE <= valuation date`) is a SEPARATE gate from the earning method.** In
  the current code it is implicit in `np.select(..., default=0)`. A registry that folds it into
  each method cannot reproduce today's output and would grant UPR to policies not yet issued.
* **Six methods ship:** `pro_rata_daily`, `sum_of_digits`, `full_premium_in_period`, `eighths`,
  `twenty_fourths`, `flat_percentage` — but `eighths` / `twenty_fourths` sit behind a
  **book-suitability guard**, not merely a dropdown. Measured at **−243%** and **−429%** on the
  reference book: they weight by issue date alone and ignore the risk period, so the 699 rows
  (4.7%) of negative endorsement worth −3.16bn that pro-rata correctly gives ~0.109 weight get
  ~0.426. The book is *nominally* suited (92.8% annual terms), which makes the failure more
  dangerous, not less.
* **Impact is material where it lands**: the intended policy (Engineering → `sum_of_digits`,
  Marine → `full_premium_in_period`) moves the book **+0.86%** but Engineering **+51.25%** and
  Marine **−6.20%**. That is why the impact preview is required, not optional.

### D4 — Triangle granularity (requirement 5)

**Verified 2026-08-21.** Two findings reshaped WP6:

1. **The "derive quarterly LDFs from monthly" bridge is mathematically invalid** and has been
   removed. Measured error against the reference claims: **+408.98%** at development 0. A
   quarterly accident period aggregates three monthly cohorts at *different maturities* (the
   2016-01 cohort has 3 months of development by the end of 2016Q1, 2016-02 has 2, 2016-03
   has 1), so a quarterly link ratio is not the product of three monthly link ratios at any
   offset. The valid route is through **ultimates → an implied quarterly CDF**, which is
   exact and injectable through the existing `ldf_overrides` path.
2. **Monthly is statistically unusable on much of this book.** Applying the valid route
   produces a **+92.16%** higher total ultimate, driven by a tail CDF of **69.8** vs 25.5.
   That is sparsity: median claims per cell falls from 146 (quarterly) to 24 (monthly), and
   at the reserving grain **4 of 14 class-treaty triangles have fewer than 10 non-empty
   monthly cells** — Banker's Blanket has 3 cells from 6 claims. Every triangle therefore
   carries a credibility score, and the derive action is disabled below a floor.

The core decision — booking stays quarterly, `PeriodGrain` introduced, diagnostic-first —
survived verification unchanged.

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
* **Treatment modes** — default **corrected 2026-09-01** after measurement:
  1. `exclude_and_add_back` — **default.** Attritional triangle developed with attritional
     factors; the excluded claims re-enter at their **known incurred (paid + case)**, carrying no
     IBNR of their own. The only mode whose factors and base describe the same population.
     Measured **−2.7%** against base — revised from −3.8% on 2026-09-01: the earlier figure added
     back paid-to-date alone, which silently assigns an open large claim a zero case reserve
     (PKR 5,069,200 on the reference book).
     The base is **not** filtered in this mode — Paid/OS/Reported must keep tying to the ledger,
     and BF reads its known component from them. The split is carried in two extra base columns,
     `Large Paid` / `Large OS`. See `LARGE_CLAIMS_EXCLUSION_PLAN.md` §10.2.
  2. `exclude_from_ldf_only` — factors ex-large applied to a base that still contains them.
     **No longer the default**: measured **+13.3%**, the largest move of the three and in the
     direction opposite to intent, because it double-counts large-claim development. One
     accident quarter rose **36.9%**. Available, with the effect stated before selection.
  3. `exclude_entirely` — available, loudly warned. Measured −8.2%; understates the ultimate.
* **Excluding large claims can RAISE the reserve.** Large claims pay early and large, so removing
  them makes the attritional book look slower-developing (development-4 factor +18.7%). Per-cohort
  impact is a required part of the UI, not a nicety.
* Reference concentration: top 10 = **22.3% of gross paid** on the correct `GROSS`/`Payment` slice.
  (An earlier 29.9% summed across treaty and head of damage — the very error the slice rule exists
  to prevent.) OS must rank on the **latest as-at**: a naive sum across snapshots overstates the top
  claim **6.5×** and returns a different claim first.
* **Every exclusion is snapshotted with the job**: claim numbers, mode, threshold, actor, timestamp.

### D6 — Average bases (requirement 7)

**Revised 2026-09-01 after measurement.** Fix `Weighted Avg` alignment (F5) **and** `Simple Avg`
(F3) — neither needs sign-off, because neither row is read by any computation. Ship the average
bases as a **selection surface**, not just extra workbook rows: all-periods,
excluding-high-and-low, last-4, last-8, median, volume-weighted variants, and free custom period
selection via per-cell exclusion. Selected basis writes through the **existing `ldf_overrides`
path**; no new write contract.

Basis selection is the **largest single lever in the nine requirements**. Total Paid CL ultimate
across every reserve workbook, on paid-to-date of 72,086,339:

| basis | total | vs today |
|---|---|---|
| today's default (CDF = 2.0, F6) | 144,172,678 | — |
| ex-high-low / median | 240,311,243 | +66.7% |
| volume-weighted (correctly aligned) | 298,356,105 | +107.0% |
| simple average (after F3) | 478,635,253 | +232.0% |
| `Weighted Avg` as written today (F5) | 829,920,872 | +475.6% |

Three measured constraints on the UI, all mandatory:

* **`last_4` / `last_8` are inert on the reference book** — 0 of 448 development columns have more
  than 3 valid factors, so `last_4` returns the simple average to the cent. They acquire content
  only with a longer experience period or WP6's **monthly grain**. Requirement 7 therefore depends
  on requirement 5 to be useful.
* **`ex_hi_lo` and `median` are identical on this book.** Corrected while building: they
  coincide up to **four** cells (an even median *is* the mean of the middle two), so a column
  needs **five** valid factors before the two bases differ at all.
* Consequently **every average must show the count it averaged**, and the engine writes a
  `Factor Count` row. Without it an actuary picks "Last 4", sees nothing change, and believes a
  judgement was applied that was not.

### D7 — Visual system (requirement 8)

Proposed, built, then shown — not solicited. Scope fixed in `docs/UI_VISUAL_SYSTEM_PLAN.md`.
Colour is never the sole signal (accessibility, and these pages print in mono).

**Revised 2026-09-01 after auditing the built frontend.** "Some colouring" is a live
accessibility defect, not a polish request. Measured against the actual tokens in
`src/index.css` and the actual classes in `src/`:

* **Every semantic colour fails WCAG AA as text in one theme** — `--primary` 3.19 in light
  (57 uses), `--destructive` 3.94 in dark (51 uses), `--warning` 2.85 and `--success` 2.89 in
  light. 125 usages. `--warning` and `--success` fail even the 3:1 large-text threshold.
* **The primary button's own white label is 3.19:1 in light mode** — the most-clicked element
  in the product.
* **All 25 distinct literal Tailwind palette classes** (56 occurrences, 14 files) fail AA in a
  reachable theme; six are light-only and paint white panels into the dark default.
* **The output preview cannot format a triangle sheet**: kind varies by ROW there, so a
  development factor of `1.015748` renders as **1.01** and `Factor Count` 3 renders as "3.00".
  An actuary cannot read their own factors. WP1 enlarged this from four benchmark rows to
  thirteen.

Both themes are live: `next-themes` defaults to dark, and `ThemeToggle` in the header makes
light one click away and persistent.

**The palette fix is visible and needs showing before merge.** `--primary` moves
`187 72% 40%` → `187 72% 32%` (same teal, deeper) so that both text and button labels reach
4.74:1; `--success` and `--warning` likewise. Dark mode — the default, and what the client has
seen — is unchanged apart from one new `--destructive-text` token, because in a dark theme a
colour used as both text and fill needs two values (the page background and the fill's label
sit on opposite sides of it).

The durable deliverable is `src/lib/palette.test.ts`: it parses `index.css`, checks every
token × role × theme against AA, and rejects any literal palette class outside an allowlist —
turning this class of defect from "caught in review, sometimes" into "cannot merge".

**WP7 is now a standalone pass, not threaded.** It was sequenced through WP1-WP6; those have
all shipped, so one palette change, one cell renderer adopted across seven surfaces, and one
visual-regression baseline is strictly better than seven separate reviews of the same decision.

---

## 4. Delivery sequence

Data integrity precedes features: shipping selection tools on top of a broken join would let users
make confident judgements against wrong numbers.

| WP | Scope | Req | Plan | Gate |
|---|---|---|---|---|
| **WP0** | Class reconciliation + pre-flight gate (F1, F4) | — | `DATA_INTEGRITY_PREFLIGHT_PLAN.md` §9 | **implemented 2026-09-02** |
| **WP1** | LDF benchmark fixes (F3, F5) + average selection + strikethrough | 7 | `LDF_AVERAGE_SELECTION_PLAN.md` §10 | **implemented 2026-09-01** |
| **WP2** | UPR method registry + per-LOB policy (F2) | 4 | `UPR_METHOD_SELECTION_PLAN.md` | **implemented 2026-08-21** |
| **WP3a** | Payment-pattern override | 2 | `PAYMENT_PATTERN_OVERRIDE_PLAN.md` | — |
| **WP3b** | Cash-flow view + override reach ("lite") | 3 | `CASHFLOW_OVERRIDE_PLAN.md` §10 | **descoped 15.5d → ~3d** |
| **WP4** | Sensitivity / scenario runner | 1 | `SENSITIVITY_TESTING_PLAN.md` | **independent — verified 2026-08-21** |
| **WP5** | Large-claims summary + exclusion | 6 | `LARGE_CLAIMS_EXCLUSION_PLAN.md` §10 | **implemented 2026-09-01** |
| **WP6** | Triangle granularity (diagnostic) + `PeriodGrain` | 5 | `TRIANGLE_GRANULARITY_PLAN.md` | **implemented 2026-08-21** |
| **WP7** | Visual system + palette accessibility | 8 | `UI_VISUAL_SYSTEM_PLAN.md` §10 | **implemented 2026-09-01** |

WP1 and WP5 share the triangle grid component. **WP5 shipped first and built it**
(`src/components/TriangleGrid.tsx`), so WP1 extends rather than creates it. The grid already
distinguishes claim-driven from factor-driven exclusion visually, which WP1 needs.

**WP1 was re-scoped on 2026-09-01 after verification and is now the highest-value remaining work
package.** Three findings drive that: F5 overstates the reserve by +178% for anyone who copies the
only usable benchmark row; F6 means every un-edited web reserve is a flat `2 x paid`; and basis
selection spans 5.8x end to end (D6). Nothing else in WP2-WP7 moves a number by that much. It stays
behind WP0 — selection tools on a broken join are worse than no selection tools — but it should be
the first feature built after it, ahead of WP3a.

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
| ~~Whether excluded large losses carry a separate IBNR loading~~ | ~~WP5~~ | **Decided 2026-09-01, no longer open.** They carry none: an excluded claim re-enters at its known incurred (paid + case), so its case reserve *is* its ultimate. That is the standard treatment, and it is precisely why its development must not run through an attritional factor. A separate loading can be added later as an explicit percentage without disturbing this default. |

**Advisory to send (revised 2026-09-01):** F3 **and F5** mean prior-period LDF selections were made
against defective benchmark rows — a Simple Average that collapses to zero, and a Weighted Average
displaced one development period. No restatement is implied: nothing reads those rows, so the
numbers entered are the numbers used. But the *selections* warrant review at the next valuation,
and F5 is the more serious of the two because `Weighted Avg` was the only row that looked usable.
Both defects are inherited from `sigma-17-desktop-app/module1.py` (`:1124`, `:1150`), so any
selection made in the desktop tool carries them too.
