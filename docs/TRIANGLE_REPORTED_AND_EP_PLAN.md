# WP8 — Reported Triangles and Earned Premium at Any Grain

> **Goal:** Put the **reported (incurred) triangle** and **earned premium** beside the paid
> triangle in the Triangle view, at monthly / quarterly / yearly grain, on the same basis the
> booking calculation already uses — and be explicit, per job, about which of those the client's
> own data can actually support.

Status: **proposed** (2026-09-11). Client requirement raised on the Triangle view screenshot.
Depends on WP6 (`docs/TRIANGLE_GRANULARITY_PLAN.md`). Decisions it inherits:
`docs/CLIENT_REQUIREMENTS_DECISIONS.md` §3 D4.

---

## 0. The requirement, and what is already true

> "i don't understand this diagnostic only? can we use these in our calculation or its for show
> only? — To use these triangles, we must have their reported paid also i.e. paid claims triangle
> and reported claims triangle, along with their earned premium thats also monthly, quarterly,
> annually"

Two separate things are being asked. The first is a misunderstanding the product created; the
second is a real gap, but a narrower one than it reads.

**The triangles are not "for show".** They are the reserving calculation. Every Reserve Summary
run writes, per `reserving_class × head_of_damage × treaty`, a workbook whose
`Paid Claims Triangle` and `Reported Triangle` sheets each carry an age-to-age block, benchmark
averages and a **Selected LDF / Selected CDF** row. Those two Selected CDF rows are read back
into `Reserve Summary` as `Paid CDF` and `Reported CDF`, which drive:

```
Paid CL Ultimate      = (Paid Claims − Large Paid) × Paid CDF + Large Incurred
Reported CL Ultimate  = (Reported Claims − Large Incurred) × Reported CDF + Large Incurred
ELR Ultimate          = Implied LR × EP
Paid BF Ultimate      = (1 − 1/Paid CDF) × EP × Implied LR + OS Claims
Reported BF Ultimate  = (1 − 1/Reported CDF) × EP × Implied LR + Reported Claims
Ultimate Claims       = IF(Selected Method = …)  →  IBNR = Ultimate − Reported Claims
```

So the paid triangle, the reported triangle **and** earned premium are already inputs to four of
the five ultimate methods, and the LDF rows of *both* triangles are user-editable in Update
Reserves (`ReserveCdfEditor` → `ldf_overrides` → re-run). What "Diagnostic only" was trying to say
is much narrower: **this page** writes nothing, and the **monthly / yearly** grains cannot be
booked (D4: `LIC_BOP` carries 2,144 rows keyed on `"YYYY-Qn"`; monthly link ratios cannot be
composed into quarterly ones — measured +408.98% at dev 0; monthly lifts the book ultimate +92%
on sparsity). The copy needs replacing regardless of the rest of this plan — see WP8.1.

| Client asks for | Booking calculation | Triangle view |
|---|---|---|
| Paid claims triangle | ✅ quarterly, booked | ✅ monthly / quarterly / yearly |
| Reported claims triangle | ✅ quarterly, booked | ❌ **missing** |
| Earned premium per period | ✅ quarterly, booked (`EP`) | ❌ **missing** |

---

## 1. Findings that shape the plan

Each was verified against the code and the reference book (`benchmarks/fixtures/summary_ref`,
described in its own `spec.json` as the *real* reference dataset from the desktop app).

### F0 — Using the reported triangle today can zero a reserve **(P0, ships first)**

This was found while quantifying F2, and it reorders the whole plan.

`read_workbook_cdfs` returns an `a2a_matrix` for **both** triangle sheets, and `ReserveCdfEditor`
renders an `LdfBasisSelector` per sheet (`cdfs.triangles.map(...)`). So an actuary can select
"Volume weighted" — or any of the six bases — on the **Reported Triangle**, and the result is
written to `Selected LDF`, returned as `ldf_overrides`, and becomes `Reported CDF` in the booked
`Reserve Summary`. Two clicks, no warning.

On a diagonal-only extract (F2) the reported triangle carries the OS balance at development 0 and
nothing beyond it, so the 0→1 factor measures a **drop**. Run against the reference book with the
product's own `ldf_for_basis(..., "volume_weighted")`:

| Slice | Basis | LDF[0] | CDF[0] | Reported claims (dev 0) | Ultimate | **IBNR** |
|---|---|---:|---:|---:|---:|---:|
| Health \| GROSS | paid | 1.9794 | 2.2336 | 6,018,817 | 13,443,700 | +7,424,883 |
| Health \| GROSS | reported | 1.9794 | 2.2336 | 6,018,817 | 13,443,700 | +7,424,883 |
| Miscellaneous \| GROSS | paid | 18.2500 | 64.6355 | 600,000 | 38,781,318 | +38,181,318 |
| Miscellaneous \| GROSS | **reported** | **0.1853** | **0.6561** | 12,920,100 | 8,476,954 | **−4,443,146** |
| Motor Insurance \| GROSS | paid | 4.7954 | 57.2728 | 507,141 | 29,045,361 | +28,538,220 |
| Motor Insurance \| GROSS | **reported** | **0.4503** | 5.3782 | 10,031,750 | 53,952,914 | +43,921,164 |
| Banker's Blanket \| GROSS | **reported** | **0.0000** | **0.0000** | 4,046,900 | **0** | **−4,046,900** |
| Banker's Blanket \| RI | **reported** | **0.0000** | **0.0000** | 3,844,554 | **0** | **−3,844,554** |

A CDF below 1 makes the ultimate smaller than claims already reported — **negative IBNR**. A CDF
of 0 wipes the reserve out entirely. Health is untouched because that slice has no OS rows at all,
which is itself the tell: the corruption is proportional to how much case reserve the slice
carries. Motor stays positive but is built on a first factor that is off by an order of magnitude.

A **sub-1.0 LDF is not automatically wrong** — favourable development, salvage and subrogation all
produce them legitimately on an incurred triangle. That is exactly why this has been invisible:
every individual number looks like something an actuary might accept. It is the *structural*
cause — a balance present in one column and absent from the next — that makes these particular
ones meaningless, and only the coverage model (§3.1) can see it.

**Consequence for this plan:** the client asked whether they can use the reported triangle in the
calculation. Today they can, and doing so can silently destroy a reserve. The guard is therefore
not a footnote to the feature — it is the first thing that ships, ahead of the feature itself.

### F7 — The recovery substitution is keyed on a spelling this book does not use

Found while building the reconciliation harness (WP8.8). `import_data` substitutes
`AMOUNTRECOVERED` for `AMOUNTPAID` only when `POLICYCLASS == 'Motor'` and the head of damage is a
recovery category. On the 2016-17 reference book `POLICYCLASS` is spelled **'Motor Insurance'**,
so the substitution never fires: the `Motor Insurance / Salvage / GROSS` slice carries **483 rows
whose `AMOUNTPAID` is entirely empty and whose `AMOUNTRECOVERED` is entirely populated**, and every
one of them contributes nothing to the triangle.

The production extract spells it `'Motor'` (confirmed in the 2021-2024 `Claim OS Full.xlsx`), so
there the substitution *does* fire. The two books therefore behave differently on recoveries
because of a string, and `class_aliases` normalises `RESERVINGCLASS` but nothing normalises
`POLICYCLASS`.

**Measured across the whole book:** 1,645 rows match the pattern, but 1,633 carry a recovered
amount of zero and lose nothing. **Twelve carry money — 1,743,541 of it** — across Health, Motor
Insurance, Miscellaneous, Engineering, Fire, Banker's Blanket and Marine. And no row in this book
spells `POLICYCLASS` as `Motor` at all, so the substitution never fires anywhere in it. Note also
that the substitution is motor-only *by design*, so non-motor recoveries are never substituted
regardless of spelling.

**Not fixed — detected.** The arithmetic is unchanged, because whether recoveries belong in the
paid triangle is an actuarial decision that moves booked numbers and every golden. Instead
`preflight._stranded_recoveries` reports, before every run, how many recovery rows carry money the
substitution will not reach, what they are worth and which policy classes they sit in — found from
the data (a recovery head, no paid amount, a non-zero recovered amount) rather than from the
spelling, so it works on any book. `RECOVERY_CATEGORIES` was hoisted to a module constant so the
detector and the reader cannot drift apart.

### F8 — Zero earned premium is a third route to the silent zero

Found while settling which EP window the page should show. The reserve loop fills the `EP`
column with **zero** for every accident period outside the run's booking window
(`engine.py`: *"additional_summaries only has GEP/RI_EP for quarters between BOP and EOP; fill
missing with 0"*). Since `Reported BF Ultimate = (1 − 1/CDF) × EP × Implied LR + Reported Claims`,
a zero EP kills the expected-loss term exactly as a blank Implied LR does — so those rows return
reported claims unchanged and book **no IBNR**, with the loss ratio and the development factor
both looking healthy.

Production job `3cb6aba9` is precisely this shape: experience 2021-2024, booking 2024.

**The guard this plan already shipped had the same blind spot.** `method_notes` checked only the
loss ratio, so it stayed silent here and reported the bare consequence (`no_ibnr`) without the
cause — which is the one thing it exists to supply. Now fixed in both the Python and its
TypeScript mirror: the expected-loss term is dead when *either* factor is zero, and the message
names which one, because "set an Implied LR" and "this period is outside the booking window" are
different problems with different fixes.

### F1 — The gap is the page, not the engine

`Module1TrianglesView` loads **only** `claims_paid` (`_triangle_source_frame`) and returns one
triangle. The job already carries everything else: `claims_os` is archived and snapshotted for
every Summary run, and premium is snapshotted for dataset-driven runs.

### F2 — The OS extract's shape is the binding constraint, not our code

Outstanding is a **balance at a valuation date**, so each distinct `As at` in the OS extract
yields exactly one *diagonal* of the incurred triangle. The reference extract carries four
`As at` dates (2017-03-31 / 06-30 / 09-30 / 12-31) — and each snapshot contains **only claims
whose loss date falls in that same quarter**:

```
row counts, As-at (rows) × accident quarter (cols)
accq    2017Q1  2017Q2  2017Q3  2017Q4
2017Q1    1292       0       0       0
2017Q2       0    1488       0       0
2017Q3       0       0    1636       0
2017Q4       0       0       0    1856
```

That is a **diagonal-only** extract: it reports what was newly outstanding for the current
quarter's accidents, never the standing inventory for prior accident periods. Consequences:

* The OS triangle it produces is populated **at development 0 only**. Everything to the right
  is structurally zero — not "no open claims", but *not supplied*.
* Therefore the workbook's `Reported Triangle` (= cumulative paid + OS triangle) equals the
  **cumulative paid triangle plus a development-0 bump**. Its age-to-age factors are paid
  factors everywhere except the 0→1 step, which is depressed by the dev-0 OS that never recurs.
  `Reported CDF`, `Reported CL Ultimate` and `Reported BF Ultimate` inherit that.
* No amount of engineering produces an incurred development triangle from this extract.

**This is a data-supply requirement to put back to the client, and it is the single most
important output of this plan.** It is also *not yet established for their production data*: the
2021-2024 book in the screenshot is far richer than the 2016-2017 reference (a monthly paid
triangle at 87% fill), so their current OS extract may well be a full inventory. WP8.0 exists to
answer that question with their own data before anything else is built.

**What we need from them, stated precisely:** for each valuation date (each month-end if a
monthly reported triangle is wanted; each quarter-end for quarterly), the OS extract must contain
**one row per open claim for every accident period still open at that date**, carrying
`CLAIMNUMBER`, `LOSSDATE`, `AMOUNTOUTSTANDING`, `As at`, `RESERVINGCLASS`, `RI_TREATY_TYPE`,
`HEADOFDAMAGE`. A claim that closes simply stops appearing; a claim open across ten valuations
appears ten times.

### F3 — Two earned-premium definitions exist in the engine; only one is booked

* **Booked (`Reserve Summary.EP`)** — a UPR-movement basis, built per period in
  `summarize_upr_by_reserving_class`:
  `GEP_p = GWP_p − UPR(end of p) + UPR(end of p−1)`, where `GWP_p` is premium **issued** in `p`,
  `UPR(d) = unearned_fraction(df, d, upr_policy) × PREMIUMAMOUNT`, masked `GROSS` → `GEP_`,
  `RI` → `RI_EP_`. It therefore depends on the run's **UPR method policy**.
* **Unbooked (`calculate_quarterly_premium`)** — a pro-rata-by-risk-days basis. Its result
  (`summary_df`) is computed inside a timed stage (`m1.quarterly_premium`) at `engine.py:1142`
  and **never used again**; `export_upr_summary_to_excel` has no callers. It is dead compute.

The page must use the **booked** definition, or the client will see two different earned premiums
for the same period. The dead one should be deleted (a free win for
`docs/PERFORMANCE_OPTIMIZATION_PLAN.md`) or repurposed only behind an explicit "pro-rata" label.

### F4 — Premium is deliberately not archived for upload-driven runs

`_persist_summary_claims` archives `claims_paid/` and `claims_os/` only, with the comment
"Premium is not read back by any diagnostic". This plan makes that statement false. Dataset-driven
runs already snapshot premium; upload-driven runs will need it archived, and the change is
**forward-only** — jobs that ran before it can never show EP at a non-quarterly grain.

### F5 — The page and the workbook are sliced differently

Workbook triangles are per `reserving_class × head_of_damage × treaty`. The page filters on
`reserving_class × treaty` only. Until head of damage is a filter, no page triangle can be tied
back to a specific workbook, which is the only way a user can trust it.

---

## 1A. Production evidence (read-only query, 2026-09-11)

> **Read §1B before acting on this section.** The zero-IBNR result described below is real and
> the figures are accurate, but the client confirmed on 2026-09-12 that it was a deliberate
> modelling choice, not a defect. The language here of a method "silently" booking zero is
> superseded; what survives is B1 (the method label misdescribes the calculation) and B2 (where
> the IBNR sits changes the Module 2 discounting). This section is kept as the evidence trail.

F0 and F2 were reasoned from the reference book. The production database settles them. All
figures below are from read-only `SELECT`s against `sigma17` on the live host.

**The shape of the estate**

| Fact | Value | Consequence |
|---|---|---|
| Module 1 jobs | 272 (59 successful `summary`, 25 `update_reserve`) | — |
| Rows in every `datasets_*row` table | **0** | Every production run is **upload-driven**. No Dataset snapshots exist. |
| Premium in the input archive | never written (`_persist_summary_claims`) | **EP is unavailable for all 272 existing jobs.** F4 is not a corner case; it is 100% of the estate. |

**The reported basis is the dominant booked method**

`Selected Method` across all 18 runs carrying `method_overrides`:

| Method | Rows booked |
|---|---:|
| **Reported BF** | **447** |
| ELR | 45 |
| Paid CL | 25 |
| Reported CL | 2 |
| Paid BF | 1 |

Since 2026-09-08 it is effectively the *only* method: 44, 44, 44, 44, 44, 60, 104 and 60 rows
per run, all `Reported BF`.

**Every reported LDF ever submitted to production is exactly 1.0**

`ldf_overrides` covers 32 workbook-sheets on `Reported Triangle` against 27 on
`Paid Claims Triangle`. For the same job and the same workbook:

```
Fire Payment GROSS 2024-12.xlsx
  paid     [5.365, 1.703, 1.213, 1.188, 1.029, 1.007, 1.01, 1, 1.001, 1.001, 1, 1.003, 1, 1, 1, —]
  reported [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
```

The `ldf_selection` audit payload records that a basis *was* applied to that sheet — `all` and
`volume_weighted`, with `factor_counts` of `[15, 14, 13, 12, …]`, the full staircase — yet the
vector stored alongside it is all 1.0.

> **Correction (2026-09-11, after examining the production workbook).** An earlier draft of this
> section inferred from those two facts that the reported triangle must be *flat*. **That was
> wrong.** The production workbook `Fire Payment GROSS 2024-12.xlsx` shows a perfectly healthy
> reported triangle: 16 accident periods, 15 development columns, a full benchmark block
> (`Simple Avg LDF` `28.3965, 5.5182, 1.3814, …`, `Factor Count` `15, 14, 13, …`). Its
> `Selected LDF` and `Selected CDF` rows are the placeholder 1.0 written by
> `write_selected_rows` — which is correct at that stage, because real factors are only meant to
> arrive through Update Reserve.

So the arithmetic is not at fault anywhere. Verified end to end against that workbook:

* `ldf_for_basis` on the backend reproduces every benchmark row in the sheet exactly (all six
  bases);
* the frontend's `computeBasis`, run against the same matrices, returns
  `[28.3965, 5.5182, 1.3814, 1.2231, 1.8152]` with counts `[15, 14, 13, 12, 11]` — identical;
* the LDF selection code last changed on 2026-09-02, before these runs, so the deployed build and
  the current source agree.

**The defect is therefore a missing guard, not wrong maths:** a `Selected LDF` row of all 1.0 —
the untouched placeholder — can be submitted and booked, and nothing anywhere says that a CDF of
1.0 means "no development at all".

**What that does to the booked number**

`_apply_overrides_to_bytes` writes the Selected CDF row as literals (`selected_cdf_from_ldf`,
the suffix product), so a Selected LDF row of 1s gives `Reported CDF = 1.0` for every cohort.
Substituting into the engine's own formula:

```
Reported BF Ultimate = (1 − 1/Reported CDF) × EP × Implied LR + Reported Claims
                     = (1 − 1/1)           × EP × Implied LR + Reported Claims
                     = 0                                     + Reported Claims
IBNR = Ultimate − Reported Claims = 0
```

Splitting the 449 reported-method rows by whether their own run also wrote an all-1.0 Reported
LDF row: **400 rows are certainly at `Reported CDF = 1.0`**; the other 49 inherit whatever the
source workbook carried, which is either the same 1.0 or the documented blank→**2.0** fallback in
`selected_cdf_row_to_series` — an arbitrary factor, not a selected one. Neither is a development
factor measured from the data.

**There is a second, independent cause — `Implied LR` is unset.** The booked workbook
`Fire Payment GROSS 2024-12.xlsx` shows both at once:

| Accident period | Paid CDF | Reported CDF | Implied LR | Method | IBNR |
|---|---:|---:|---:|---|---:|
| 2021-Q1 … 2023-Q4 | 1.0000 → 1.0515 | **1.0000** | — | Reported BF | **0** |
| 2024-Q1 | 1.2489 | **1.0000** | — | Reported BF | **0** |
| 2024-Q2 | 1.5151 | **1.0000** | — | Reported BF | **0** |
| 2024-Q3 | 2.5803 | **1.0000** | — | Reported BF | **0** |
| 2024-Q4 | 13.8442 | 1.0000 | 0.6377 | ELR | 182,784,933 |

**15 of 16 accident quarters book exactly zero IBNR.** Reported BF is neutered twice over:

1. `Reported CDF = 1` ⟹ the development term `(1 − 1/CDF)` is zero; and
2. `Implied LR` is unset on 15 of 16 rows ⟹ `EP × Implied LR` is zero as well.

So `Reported BF Ultimate = Reported Claims` identically, whatever the triangle says. The
comparison that needs no speculation is the last two quarters: **the client's own paid basis says
2024-Q3 needs a CDF of 2.5803 and 2024-Q4 needs 13.8442; the reported basis they booked on says
1.0000 for both.** Both cannot be right.

A projection at the reported triangle's own volume-weighted factors puts this one class/treaty's
IBNR in the billions against the 182.8m booked — but those tail factors (10.6 at 2024-Q3, 139.9 at
2024-Q4) are chain-ladder instability at immature cohorts, which is precisely what BF exists to
damp. **That figure should not be quoted as "the understatement"**; the honest statement is that
the true number is materially above zero for fifteen quarters, and its size is the actuary's
selection to make.

**The client's dominant reserving method is currently booking zero IBNR on 400 booked rows.** The
Bornhuetter-Ferguson expected-loss term is multiplied by zero; the method has been silently
reduced to "ultimate = claims already reported". This is live, it is a month old, and it is almost
certainly why they wrote to us about the triangles.

**The 2016-2017 sample extract** (`sigma-17-desktop-app/Input Format-Module 1/Claims OS/`,
md5 `31d41ecc…`, byte-identical to the benchmark fixture) contains:

* 6,272 rows, 1,685 distinct `CLAIMNUMBER`, **4 valuation dates**, all 2017 quarter-ends;
* 772 claim numbers appearing at more than one valuation, of which **zero have a single loss
  date**. Every apparent repeat is a *different claim reusing the number*: `CLAIMNUMBER` is not a
  unique key, and **no claim's outstanding is ever observed at two valuations.**

Two consequences follow, and neither is a matter of opinion:

1. A reported **development** triangle cannot be built from this format at all — outstanding is
   only ever seen at development 0.
2. Whether case estimates are revised over a claim's life is **unknowable** from this extract:
   the same claim never appears twice, so there is nothing to compare.

The desktop app's own output for the same book (`Fire and property damage Payment GROSS
2017-12.xlsx`) agrees with our engine on every cell the two share; ours additionally carries the
2017-Q4 accident row, where the outstanding lands (2,940,186 against nil paid). This is therefore
not a divergence between the legacy application and ours — both faithfully report an extract that
carries no development.

**The production extract (2021-2024) — the one that matters.** 32,044 rows, matching the
preflight count on job `3cb6aba9` exactly, so this is the file the client actually runs on.

| Property | Value |
|---|---|
| Valuation dates | **4, all year-ends**: 2021-12-31, 2022-12-31, 2023-12-31, 2024-12-31 |
| Loss dates | 1999-02-02 → 2024-12-31 |
| Accident years present per valuation | 13 – 17 |
| Claims genuinely carried forward (>1 valuation, single loss date) | **3,961** |
| …of which revise `AMOUNTOUTSTANDING` between valuations | **1,483 (37%)** |

This is a **full open-claim inventory**, not the diagonal-only shape of the sample file, and case
estimates *are* revised. Both are good news — and both answer questions we were about to ask.

The constraint is the **frequency**: four annual valuations. Consequences, per grain:

| Grain | Reported triangle |
|---|---|
| **Yearly** | Fully supported — every diagonal has a valuation. This is exactly the 4 × 4, 100%-populated view in the client's screenshot. |
| **Quarterly** (the booking basis) | **Sawtooth.** Outstanding lands only on the four year-end diagonals. For `Fire / Payment / GROSS`, **40 of 136 upper-triangle cells (29%)** carry any outstanding; the other 71% are paid-only. |
| **Monthly** | Not derivable. |

The quarterly consequence is the serious one. That triangle alternates between "paid + case
reserves" cells and "paid only" cells, so its age-to-age factors measure *where the valuation
dates fall*, not how incurred develops. A chain-ladder or BF built on it is not measuring
development. **This — not a flat triangle — is the real defect in the reported basis at the
booking grain**, and it is a data-frequency problem, fixable only by supplying quarter-end
valuations.

---

## 1B. Client answers (2026-09-12) and what they change

> **A — Can you produce outstanding at every quarter-end?** "yes"
> **B — The fifteen zero-IBNR quarters?** "IBNR is as of position like OS, and it can belongs to
> any quarter/ or accident periods, for simplicity I just put Ibnr in the latest quarter"
> **C — Health Insurance premium with no claims?** "no claims data uploaded"

### B first: the zero IBNR was deliberate. §1A's framing was wrong.

The fifteen zero-IBNR quarters are not a defect and were not an accident. The actuary computed the
book's IBNR once — ELR on 2024-Q4, `0.6377 × 308,742,785 − 14,110,434 = 182,784,933` — and booked
it entirely in the latest accident quarter, on the view that IBNR is a balance-sheet position
rather than something to allocate backwards. That is a coherent stance, and the
"dominant method is silently booking zero IBNR" language in §1A must be read as superseded.

Two narrower points survive, and both are worth fixing:

**B1 — The recorded method describes a calculation that did not happen.** Those rows carry
`Selected Method = Reported BF` with `Reported CDF = 1.0` and `Implied LR` blank, so both terms of
the BF formula are zero and it degenerates to `Ultimate = Reported Claims`. The audit trail
asserts a Bornhuetter-Ferguson calculation where the intent was "reported claims are ultimate for
this quarter". The product should offer that intent explicitly — a `Reported = Ultimate` /
`No further development` selection — rather than leaving it expressed as a BF with its inputs
switched off. This is a disclosure and auditability fix, not a numbers fix.

**B2 — Where IBNR sits is not presentational in this system; it changes the IFRS 17 numbers.**
Verified through `module2_engine/engine.py`:

```
merged_df["Age"]            = calculate_sequence(merged_df)   # 0 = newest accident period
merged_df["Cumulative %"]   = 1 / Paid CDF
merged_df["Future CF"]      = IBNR + ULAE + Outstanding + SS
additional_matrix           # distributes Future CF across future quarters, keyed on Age
discounted_cf_cy_df[col]    = future_cf_df[col] * cumulative_discount_rate[col]
Discounting Impact          = discounted − undiscounted
```

The accident period an IBNR amount occupies sets its `Age`, which sets how far into the future its
cash flows are spread, which sets its discounting. IBNR parked at the newest accident quarter gets
`Age = 0` and — with that quarter's `Paid CDF` of 13.84, so `Expected Unpaid %` of 92.8% — is run
off over the **longest** remaining pattern. That is the maximum duration and therefore the maximum
discount: **LIC is understated relative to the same IBNR sitting in the accident periods it
actually belongs to.** It also misstates LIC by accident period, and `PREVIOUS_PERIOD_LIC` carries
`accident_period` as a join key, so next period's movement comparatives inherit the distortion.

The size of the effect is measurable — re-run Module 2 with the IBNR allocated by accident period
and difference the `Discounting Impact` — but it needs the client's Module 2 inputs (discount
curve, ULAE-RA), which are not in the database. Offer the measurement; do not guess the number.

### A: quarter-end valuations are available. This unlocks the booking grain.

The reported basis is therefore **not** permanently yearly-only. With sixteen quarter-end
valuations the quarterly reported triangle becomes sound, `ValuationCoverage` reports `inventory`
at quarterly, and the basis unlocks with no code change — which is exactly why the coverage model
is data-driven rather than a configuration flag.

It also unlocks the assumption underneath B. "Reported claims are ultimate for mature quarters"
is precisely the claim a quarterly reported triangle tests: if reported CDFs come out above 1.0
for mature cohorts, case reserves are developing and the zero-IBNR position understates. Today
that cannot be checked at the booking grain at all. **This is the strongest argument for the new
extract, and it should be put to them in those terms.**

### C: Health Insurance has no claims because none were supplied.

Not a naming mismatch — the claims extract carries exactly four classes (Motor, Misc, Marine,
Fire) and no row anywhere mentions health, medical or hospital. So the class earns premium
(13,370 rows, feeding GEP → UPR → LRC) while contributing **no claims liability at all**: no OS,
no IBNR, a zero loss ratio, and a zero ELR/BF ultimate. The balance sheet is one-sided for that
class. Either the health claims are supplied, or the class is excluded from the reserving run —
carrying it with premium only is the one option that should not stand.

---

## 1C. Client answers (2026-09-15) and what they change

> **On the OS extract:** "the OS sheet has the information as at date for 16 quarters (for each
> quarter end closing) thus quarterly reported triangles are already part of the initial
> calculation"
> **On `LIC (OS) Summary`:** "this sheet has a separate purpose which will be used in module 2 for
> having claims as per the UW basis, as it produces all the claims prior to UW 2018 to club in UW
> 2018, while OS triangles discard using the experience start date"
> **Offered:** "we can bring all the monthly OS in the original uploaded data"
> **Health Insurance:** "we have not uploaded the claims paid/OS data, so let it leave and just
> run the overall process without it"
> **ULAE %:** "i have intentionally left it empty"

**The 16-quarter belief does not match the file that was uploaded.** Re-checked on the exact
artefact: `Claim OS Full.xlsx` has a single sheet `OS`, 32,044 rows, and its `As at` column holds
**four distinct values — 2021-12-31, 2022-12-31, 2023-12-31 and 2024-12-31**. The file is the one
production received: 2,223,077 bytes against the 2.2 MB recorded on job `3cb6aba9`, and 32,044
rows against that job's preflight OS count of 32,044. The source system may well hold sixteen
quarter-end closings; the extract handed to us carries four year-ends. This is worth settling
before any further work, because the quarterly reported triangle turns entirely on it.

**The `LIC (OS) Summary` route is withdrawn.** The client is right and the correction matters:
that sheet clubs everything prior to UW 2018 into UW 2018 for a Module 2 underwriting-year view,
whereas the OS triangles discard by experience start date. The two are on different bases, so
building a triangle from that sheet would mix them. §2 of the follow-up note offered it as
"Route B" — that offer should be withdrawn rather than left standing.

**Monthly OS in the original upload is the best available answer** and supersedes the quarterly
ask: month-end valuations support the reported triangle at monthly, quarterly *and* yearly,
since coverage is computed per grain from whatever dates the extract carries.

**Health Insurance is now decided** — excluded. Implemented as WP8.10 below rather than left as a
question.

**ULAE % is intentional**, so nothing should nag about it. The `RA %` gaps were reported in the
same breath and have not been confirmed either way; they are a separate question.

---

## 1D. Client answers (2026-09-17)

> **On grain and coverage:** "if OS is available at each frequency, there will be reporting
> triangle, else not ... at quarterly dates available, it can show quarterly but also show
> monthly and there may be some diagonal blank due to empty or 0 OS at that date and that will be
> misleading or we can say not sufficient information provided"
> **On RI risk adjustment:** "as currently we are focusing on Gross calculations, RI will come
> second — i am fine for treating blank as zero as of now"

**The coverage rule is now the client's, and it is stricter than what was built.** The
implementation accepted `sparse` as usable and drew the triangle with unvalued cells left null,
relying on the blanks plus a banner to convey the problem. The client's rule is that a grain the
extract cannot carry produces **no triangle at all** — a triangle with three cells in four missing
reads as a triangle, not as insufficient data. `ValuationCoverage.usable` is now `inventory` only,
the warning says *"not sufficient information for a &lt;grain&gt; reported triangle"*, and the
refusal names the grain that **would** work, because refusing is only half an answer.

One branch became unreachable as a result — the page's "no age-to-age factor can be formed" line,
which could only have shown on a rendered sparse triangle — and was removed rather than left as
dead copy. The coverage banner now earns its place on the *paid* basis, where it says why the
Reported button will not work before it is pressed.

**RI risk adjustment: blank-as-zero is confirmed as intended** for now, with RI in scope later.
Nothing to change, and nothing should nag about it.

---

## 2. Scope

**In scope**

1. Reported (incurred) triangle at any grain the data supports, driven by an explicit
   valuation-coverage model.
2. Earned premium per accident period at any grain, on the booked definition, per class × treaty.
3. Derived exposure on the page: paid LR and reported LR per accident period.
4. A head-of-damage filter, so a page view reconciles to exactly one workbook.
5. A per-job data diagnostic stating what that job's own inputs can support.
6. Premium archiving for upload-driven runs; the "Diagnostic only" copy fix.
7. **Identity-collapse disclosure** (§1B B1): when a selected method's inputs leave it equal to
   `Ultimate = Reported Claims`, say so on screen and in the workbook.
8. **Health-class handling** (§1B C): a class carrying premium with no claims of any kind must be
   reported as such, and excludable from a run.

**Out of scope — explicitly**

* **Booking stays quarterly.** No engine output changes. Every golden set in
  `benchmarks/goldens` stays byte-identical; that is the merge gate.
* **No new `Selected Method` value.** `RESERVE_METHODS` is baked into the `Ultimate Claims`
  `IF()` formula, so extending it rewrites every workbook and breaks every golden. It is also
  unnecessary — see D9.
* No change to the `Reported Triangle` arithmetic (D4). Where the input cannot support the
  triangle, the answer is coverage reporting, not different maths.
* Re-granularising the booking basis (assessed and rejected in D4).
* Restating past runs. §1B established the zero IBNR was intended; there is nothing to restate.

---

## 3. Design

### 3.1 `ValuationCoverage` — the core new abstraction

New in `module1_engine/triangles.py`. Classifies what an OS extract can support **at a given
grain**, before any triangle is built, and travels with the response so the UI never guesses.

```python
@dataclass(frozen=True)
class ValuationCoverage:
    grain: str
    valuation_periods: list[str]                 # As-at periods present, at this grain
    accident_span: dict[str, tuple[str, str]]    # per valuation: min/max accident period observed
    shape: str                                   # see table
    covered_cells: list[tuple[int, int]]         # (accident idx, dev idx) backed by a valuation
    coverage_ratio: float                        # covered_cells / upper-triangle cells
    warnings: list[str]
```

| `shape` | Detected when | Reported triangle |
|---|---|---|
| `inventory` | every diagonal of the upper triangle has a valuation, and valuations reach back over multiple accident periods | built in full |
| `sparse` | valuations reach back (a true inventory) but exist for only *some* diagonals — the frequency is coarser than the requested grain | **refused** — "not sufficient information for a *grain* reported triangle", naming the grain that would work (client's rule, 2026-09-15) |
| `diagonal` | every valuation's accident span is exactly its own period | refused — no development is observable |
| `single` | one valuation only | latest incurred position; a column, not a triangle |
| `none` | no OS rows, or no `As at` | refused |

`sparse` is the client's actual case and the one the earlier draft lacked: four annual valuations
against a quarterly grain gives coverage on one diagonal in four
(§1A: 40 of 136 cells, 29%, for Fire/Payment/GROSS). The rule that makes it safe:

> A missing cell **inside** a covered diagonal is a genuine zero — the claim closed.
> A cell **outside** any covered diagonal is unknown and must be `null`.
> An age-to-age factor requires **both** of its cells to be covered.

Under that rule a sparse quarterly triangle yields *no* factors at all, which is the correct
answer — far better than the sawtooth the current code produces by treating uncovered cells as
paid-only. The same detector returns `inventory` at yearly for the same data, so the yearly
reported triangle stays available, and returns `inventory` at quarterly the moment the client's
quarter-end extract arrives — with no code change. That is why this is data-driven and not a flag.

### 3.2 Reported triangle

```
os(i, j)       = Σ AMOUNTOUTSTANDING where accident period = i and As-at period = i + j
reported(i, j) = cumulative_paid(i, j) + os(i, j)   if (i, j) ∈ covered_cells
               = null                                otherwise
```

`build_triangle` gains `basis: "paid" | "reported"` and an optional `os_frame`. The paid path is
untouched and its existing tests are the regression gate. Age-to-age, credibility and the
implied-CDF bridge all run on whichever basis was built.

### 3.3 Earned premium

New `module1_engine/earned_premium.py`, lifting the **booked** definition out of
`summarize_upr_by_reserving_class` unchanged:

```python
def earned_premium_by_period(premium_df, *, grain, bop, eop, upr_policy) -> pd.DataFrame
# → RESERVINGCLASS, RI_TREATY_TYPE, period, ep
```

`GWP_p` from `ISSUEDATE` in `p`; `UPR(d) = unearned_fraction(df, d, upr_policy) × PREMIUMAMOUNT`;
`EP_p = GWP_p − UPR(end of p) + UPR(end of p−1)`, masked `GROSS` → `GEP`, `RI` → `RI_EP`. Binds
the job's own `upr_policy` from `input_meta` (production uses "House basis" / `pro_rata_daily`).

**Acceptance: at `grain=quarterly` it reproduces `Reserve Summary.EP` to the cent for every
class × treaty in the reference book.** That equality is the entire trust argument for the monthly
and yearly figures, which nothing else can check.

### 3.4 Identity-collapse disclosure (B1)

No new method. `Reported CL` with `Reported CDF = 1.0` already evaluates to
`Ultimate = Reported Claims` exactly — the client's intent is expressible in today's vocabulary,
which is why D9 rejects extending `RESERVE_METHODS`.

What is added is a **statement of consequence**, computed from the row's own values, wherever a
method is chosen:

| Condition | Message |
|---|---|
| `Reported BF` with `Reported CDF = 1` | "Development term is zero — this returns reported claims unchanged." |
| any BF with `Implied LR` blank | "Expected-loss term is zero — this returns reported claims unchanged." |
| both | both, plus a suggestion that `Reported CL` states this intent directly |
| any method where `Ultimate == Reported Claims` to the cent | "No IBNR is produced for this row." |

Shown in `ReserveMethodEditor` beside the row, and — because actuaries work offline in Excel —
written as a note column on the `Reserve Summary` sheet. **That note changes workbook bytes and
therefore needs a reviewed golden refresh**, which is why it is its own work package (WP8.7).

### 3.5 API

`GET /api/module1/jobs/{pk}/triangles/` — additive, backwards compatible:

| Param | Values | Notes |
|---|---|---|
| `basis` | `paid` (default) \| `reported` | 422 with the coverage reason when unsupported |
| `head_of_damage` | string | new filter; absent = all |
| `include` | csv of `ep`, `coverage` | opt-in, so the default response does not grow |

Response gains `coverage` (§3.1), `earned_premium` (`{labels[], ep[], basis}`), `exposure`
(`{paid_to_date[], reported_to_date[], paid_lr[], reported_lr[]}`) and `heads_of_damage`.
Existing keys keep their shape and meaning.

### 3.6 Frontend

* **Basis** segmented control beside Grain: Paid / Reported. Disabled with the coverage reason
  when the data cannot support it — never paid relabelled as reported.
* **Exposure card**: accident period, EP, paid to date, reported to date, paid LR, reported LR.
* **Head of damage** filter, and a "reconciles to `<file>.xlsx`" line when the three filters
  resolve to exactly one workbook.
* **Coverage banner** naming the valuation dates found and what they support.
* Header copy: "Diagnostic only" → **"Booking stays quarterly — nothing on this page writes to a
  workbook. Apply factors in Update Reserves."**
* `state/wizards/triangles.ts` → version 2 (`basis`, `headOfDamage`, `showExposure`) with a
  migration from v1.

### 3.7 Reconciliation — the trust anchor

One test asserts the chain end to end: for a reference job, the page's quarterly `basis=reported`
triangle, its EP vector and its LRs equal the corresponding workbook's `Reported Triangle`,
`Reserve Summary.EP` and `Reported LR` for the same class × head of damage × treaty. Without it
the page is a second implementation of the numbers and will drift.

---

## 4. Work packages

Dependencies are strict top to bottom within a block; blocks are independent.

**Block 1 — disclosure and correctness — DELIVERED 2026-09-12**

| WP | Deliverable | Files | Acceptance | Est. |
|---|---|---|---|---|
| WP | Delivered | Where | Verified by |
|---|---|---|---|
| **8.0** ✅ | `ValuationCoverage` + `manage.py triangle_coverage` | `module1_engine/triangles.py`, `processing/management/commands/triangle_coverage.py` | `sparse` at quarterly / `inventory` at yearly on the production extract; `diagonal` on the 2016-17 sample. 11 unit tests + one that runs the real task and reads a finished job. |
| **8.1** ✅ | "Diagnostic only" → "Booking stays quarterly — nothing on this page writes to a workbook. Apply factors in Update Reserves." | `TrianglesPage.tsx` | Test asserts the page no longer says "diagnostic only". |
| **8.7a** ✅ | Identity-collapse disclosure in the UI, under each row | `module1_engine/method_notes.py`, `src/lib/methodNotes.ts`, `ReserveMethodTable.tsx` | 12 engine tests, 14 parity tests against a generated fixture, 5 render tests on the production row. Collapsed a third copy of the five ultimate formulas onto the parity-tested one. |
| **8.7b** ✅ | `Method Note` column on `Reserve Summary`, appended last | `module1_engine/engine.py` | 4 tests; `test_reserve_summary_formulas` now pins that the historic thirteen keep their letters and additions sit strictly after them. |

**Block 2 — earned premium — DELIVERED 2026-09-13**

| WP | Deliverable | Files | Acceptance | Est. |
|---|---|---|---|---|
| **8.6** ✅ | Premium archiving for upload-driven runs, forward-only, behind `MODULE1_ARCHIVE_PREMIUM` | `processing/tasks.py`, `config/settings.py`, `.env.example` | A new run's archive contains `premium/`; with the setting off it does not, the claims kinds still do, and the diagnostic explains why EP is unavailable. **Storage: premium is 30.4 MB against 7.1 MB of claims on the production book — roughly a fivefold increase in archived bytes per job, not a doubling.** | 1 d |
| **8.2** ✅ | `earned_premium_by_period` on the booked UPR-movement basis, at any grain; dead pro-rata path deleted | new `module1_engine/earned_premium.py`, `engine.py` (−40 lines) | **Quarterly reproduces `Reserve Summary.EP` exactly** — 64 (class, quarter) cells, 27 non-zero, 203,750,563.55 total, max abs delta **0.00000000**. Grain additivity to the cent: monthly = quarterly = yearly. 6 tests. `calculate_quarterly_premium` and three caller-less `export_*_to_excel` functions removed; goldens prove the deletion is bit-identical. | 3 d |

**Block 3 — reported basis and the page — DELIVERED 2026-09-14**

| WP | Deliverable | Files | Acceptance | Est. |
|---|---|---|---|---|
| **8.3** ✅ | Reported basis in `build_triangle`, coverage-aware nulls | `module1_engine/triangles.py` | Outstanding added as a **balance** at its valuation maturity, never accumulated; uncovered cells null, not zero; **no factor spans an uncovered cell** (annual valuations at quarterly grain yield 40 of 136 cells and **zero** age-to-age factors, while the same extract gives a full 10-cell triangle with 6 factors at yearly). Paid path bit-identical — all goldens pass. 10 tests. | 3 d |
| **8.4** ✅ | API: `basis`, `head_of_damage`, `include=ep,coverage`; premium loader on the same 3-tier cascade (`_os_source_frame` already existed and is reused) | `processing/views.py` | `basis=reported` is refused 400 with a **self-contained** message — the error envelope carries strings only, so it names the shape, the valuation dates found, and what would fix it. Response gains `basis`, `coverage`, `heads_of_damage`, `earned_premium`. 34 tests pass. | 3 d |

> **Design note — the EP window.** The page's earned premium is computed over the **experience
> period**, because it sits beside a triangle drawn on the experience axis and an EP spanning a
> different window could not be read against it. The workbook's `EP` column instead zero-fills
> outside `bop`/`eop`, so on a run whose experience period is wider than its booking period the
> two legitimately differ at the edges. The response names its own `window` rather than leaving
> that implicit, and extending WP8.8 to EP must assert the relationship rather than blanket
> equality.
| **8.5** ✅ | Basis control, head-of-damage filter, coverage banner, exposure card, store v2 | `TrianglesPage.tsx`, `api/module1.ts`, `state/wizards/triangles.ts`, new `ExposurePanel.tsx` | Basis stays **enabled** on a job that cannot serve it — the refusal names what the extract holds, which beats a disabled button with no explanation. Coverage banner appears only when the data is valued less often than the grain. EP requested only when the panel is open. A v1 view keeps its job and grain and defaults only the new fields. 348 frontend tests, tsc at the 45-error baseline. | 4 d |
| **8.8** ✅ | Reconciliation harness — **pulled forward**, written against the paid basis first, then extended to the reported basis and earned premium | `test_triangle_reconciliation.py`, `test_earned_premium.py` | **Paid:** page and workbook agree on every cell the workbook populates, across all 21 slices; one bounded divergence pinned exactly (91 cells, 7 slices — see F7). **Reported:** they agree on every covered cell; on the 96 uncovered cells the sheet shows paid alone while the page shows null, and the sheet yields factors from that sawtooth while the page yields none. Quarter-end valuations remove the divergence entirely — asserted, so the fix is demonstrably the data. **EP:** overlapping periods match to the cent; the window difference is asserted as a relationship, with a synthetic case for materiality since the reference book earns nothing outside 2017. | 1 d |

**Block 4 — on request only**

| WP | Deliverable | Trigger | Est. |
|---|---|---|---|
| **8.9** | Measure the discounting effect of IBNR placement (§1B B2): re-run Module 2 with IBNR attributed by accident period, difference `Discounting Impact` and LIC by accident period | Client supplies Module 2 inputs (discount curve, ULAE-RA) and asks | 2 d |
| **8.10** ✅ | Reserving-class exclusion, so a premium-only class can be kept out of a run | Client chose exclusion on 2026-09-15 | done |

**Blocks 1–3: 23–24 working days.** Block 1 alone (5.5 d) is worth shipping on its own — it is the
answer to the client's original question plus the disclosure that would have made the last
fortnight's investigation unnecessary.

---

## 5. Production readiness

**Golden re-baseline, 2026-09-12.** Four Module 2 goldens (`m2_allocate_ref`, `m2_pattern_ref`,
`m2_process_ref`, `m2_sensitivity_ref`) were failing before this work began. Bisected to
`6cee8a3` "payment pattern override" (2026-09-10): `Payment Pattern` changed from an unweighted
mean of per-row conditional patterns to the FutureCF-weighted class profile, which
`docs/PAYMENT_PATTERN_OVERRIDE_PLAN.md` records as client-requested and which all 171 module2
unit tests cover. The code was right; the goldens had never been re-captured. Re-captured after
review, and all ten now pass. **Process note:** `benchmarks/` is gitignored, so a stale golden is
invisible in review — the refresh has to be part of the change that causes it.

**Bit-identical gate.** Every golden set in `benchmarks/goldens` (`summary_ref`,
`summary_ref_aliased`, `summary_ref_prewp1`, `policy_upr_ref`, `m1_large_claims_ref`,
`m1_upr_methods_ref`, `m2_allocate_ref`, `m2_pattern_ref`, `m2_process_ref`,
`m2_sensitivity_ref`) passes untouched after every WP except 8.7b, which refreshes them under
review. The paid triangle's existing tests are the regression suite for `build_triangle`.

**Performance — measured 2026-09-14.** Loading dominates; computation is cheap.

| Step | Cost |
|---|---:|
| Parse paid claims (6.6k rows, 0.4 MB) | 383 ms |
| Parse outstanding (32k rows, 2.2 MB — the production file) | 1,526 ms |
| Parse premium (14.8k rows, 0.9 MB) | 493 ms |
| `build_triangle`, monthly | 33 ms |
| `valuation_coverage`, quarterly over 32k OS rows | 21 ms |
| `earned_premium_by_period`, quarterly | 367 ms |
| `earned_premium_by_period`, monthly | 910 ms |

The budget is **p95 ≤ 2 s**, and the risk is entirely in parsing. The client's production premium
workbook is **30.4 MB against the 0.9 MB fixture measured above**, so the uncached EP path would
miss the budget by a wide margin — EP is also the most expensive computation, since
`unearned_fraction` is evaluated at every period end (48 of them at monthly) and cannot be
collapsed into one pass without changing the arithmetic.

**Done:** the computed EP payload is cached per `(job, grain, class, treaty)` for
`MODULE1_TRIANGLE_CACHE_SECONDS` (default 900). The *payload* is cached, not the frame — a few
hundred floats rather than a large DataFrame in every worker, which would fight
`CELERY_WORKER_MAX_MEMORY_PER_CHILD`. A finished job's inputs are frozen, so job identity is the
only correct invalidation and the TTL bounds memory rather than catching change. Django has no
`CACHES` block, so this is per-process LocMem: with N gunicorn workers the first N requests pay
and the rest do not.

**Still open:** the outstanding frame (1.5 s) is re-parsed per reported-basis request. It is an
order of magnitude cheaper than premium and only paid on that basis, so it is left measured
rather than optimised. If it bites, cache the outstanding *grid* per `(job, grain, filters)` —
n×n floats — on the same principle.

**Tenancy and permissions.** Unchanged: `_get_accessible_job` + `module1.run`. New loaders read
the same org-scoped snapshots and job-owned archive. No new file paths, no new upload surface.

**Degradation matrix — every one is a stated message, never a silent zero:**

| Condition | Behaviour |
|---|---|
| Coverage `sparse` at the requested grain | triangle rendered with null cells; basis selector blocked; banner names the valuation dates and the grain that *would* work |
| Coverage `diagonal` / `none` | reported basis refused with the reason and the extract spec |
| Premium absent (pre-8.6 run) | EP unavailable, with the reason and the fix |
| `upr_policy` absent from `input_meta` | EP on the engine default, labelled as such |
| Class with premium and no claims | named in the coverage report; excluded only if the client opts in (8.10) |
| Job not a Summary run / not succeeded | existing 422s, unchanged |

**Observability.** Log coverage shape, valuation count and coverage ratio per request
(cardinality-safe, no PII); count reported-basis refusals by reason.

**Rollout.** Additive and default-off: `basis` defaults to `paid`, `include` to empty. Premium
archiving is forward-only — the runbook and the UI must both say so rather than showing an empty
panel.

---

## 6. Test plan

* **Engine** — coverage classification for all five shapes; `sparse` produces null cells and no
  factor spanning an uncovered cell; a closed claim inside a covered diagonal reads 0, not null;
  EP grain additivity; EP under each UPR method.
* **Reconciliation** — quarterly EP == `Reserve Summary.EP`; reported triangle == workbook
  `Reported Triangle`; LRs == `Reported LR`.
* **Disclosure** — every row of the §3.4 table, positive and negative.
* **API** — refusal carries the reason; `include` shapes the payload; filters compose; org
  isolation; permission denial.
* **Frontend** — basis disabled with reason; exposure panel renders EP and LRs; coverage banner
  names dates; store v1→v2; nothing rendered where coverage is null.
* **Goldens** — every set, byte-identical, after each WP except 8.7b.
* **Fixtures** — add the production-shape case: an inventory extract with annual valuations, so
  `sparse` has a permanent regression home.

---

## 7. Decisions

**D1 — The disclosure ships first, with its purpose revised.** Not "stop a reserve being zeroed"
(§1B established the zero was intended) but: *make a method that has collapsed to an identity say
so.* Had it existed, none of this investigation would have been needed.

**D2 — Done.** The client was told, with numbers, and answered on 2026-09-12 (§1B).

**D3 — Superseded.** No restatement to propose. What replaces it is B1 (method labelling) and B2
(IBNR placement affecting discounting); offer to measure B2, do not assert a number.

**D4 — Do not change the reported-triangle arithmetic.** It is correct. Where the input cannot
support it, report coverage.

**D5 — Guard structurally, disclose on judgement.** Block only where a *data fact* says no factor
can be formed. Everything else is a statement of consequence the actuary may proceed past.

**D6 — Sequence by data dependency.** EP is independent of the OS question, but needs archiving
first, so 8.6 precedes 8.2. The reported basis follows 8.0.

**D7 — Promise what the data can carry.** Monthly paid and monthly EP: committed. **Quarterly
reported: committed** — the client confirmed quarter-end valuations are available, and coverage
unlocks it on arrival with no code change. Monthly reported stays uncommitted.

**D8 — Yearly reported stays available throughout.** It is `inventory` on today's extract and is
the only grain at which the client can currently test case-reserve adequacy.

**D9 — Express "reported is ultimate" as `Reported CL` with CDF 1.0, not a new method.**
`RESERVE_METHODS` is baked into the `Ultimate Claims` formula; extending it rewrites every
workbook and breaks every golden, to express something the existing vocabulary already says
exactly.

---

## 8. Immediate actions

1. ~~Obtain a production artefact~~ — done; both files analysed (§1A, §1B).
2. ~~Put the finding to the client~~ — done; answered 2026-09-12.
3. ~~Ship Block 1~~ — done 2026-09-12 (8.0, 8.1, 8.7a, 8.7b).
4. **Request the sixteen quarter-end valuations** and re-run `manage.py triangle_coverage`
   against the new extract. Coverage should report `inventory` at quarterly; the basis then
   unlocks itself with no code change.
5. **Block 2 next** — 8.6 premium archiving, then 8.2 earned premium. Neither waits on the
   client. Then Block 3.

---

## 9. Correspondence

* `docs/CLIENT_QUESTIONS_TRIANGLES_2026-09-11.md` — what was asked, and why.
* `docs/CLIENT_REPLY_TRIANGLES_2026-09-12.md` — the reply to their answers: our correction on the
  zero-IBNR reading, the discounting consequence of IBNR placement, and the case for the
  quarter-end extract.
