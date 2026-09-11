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

## 2. Scope

**In scope**

1. A reported (incurred) triangle at any grain, with an explicit valuation-coverage model.
2. Earned premium per accident period at any grain, on the booked definition, per class × treaty.
3. Derived exposure measures on the page: paid LR, reported LR, per accident period.
4. A head-of-damage filter, so a page view reconciles to exactly one workbook.
5. A per-job data diagnostic that states what the job's own inputs can support.
6. Premium archiving for upload-driven runs; copy fix for "Diagnostic only".

**Out of scope — explicitly**

* **Booking stays quarterly.** No change to any engine output. Every golden must stay
  byte-identical; that is the merge gate, not an afterthought.
* No change to the workbook's existing `Reported Triangle` maths. If F2 proves true of production
  data, that sheet is *understated by construction* and the fix is the client's data, not our
  code — raised in §7 as a decision item with its blast radius.
* Re-granularising the booking basis (assessed and rejected in D4).

---

## 3. Design

### 3.1 `ValuationCoverage` — the core new abstraction

New in `module1_engine/triangles.py`. Classifies what an OS extract can support **before** any
triangle is built, and travels with the response so the UI never has to guess.

```python
@dataclass(frozen=True)
class ValuationCoverage:
    grain: str
    valuation_periods: list[str]          # As-at periods present, at this grain
    accident_span: dict[str, tuple[str, str]]   # per valuation period: min/max accident period
    shape: str                            # "inventory" | "diagonal" | "single" | "none"
    covered_cells: list[tuple[int, int]]  # (accident idx, dev idx) with a real valuation
    warnings: list[str]
```

| `shape` | Detected when | Reported triangle |
|---|---|---|
| `inventory` | valuation periods reach back over multiple accident periods | built; cells outside `covered_cells` are **null**, never zero |
| `diagonal` | every snapshot's accident span is exactly its own period | **refused**, with the F2 explanation and the data spec |
| `single` | one valuation period only | latest incurred **position** only (a column, not a triangle) |
| `none` | no OS rows / no `As at` | refused |

The rule that matters: **a missing cell inside a covered diagonal is a genuine zero (the claim
closed); a cell outside any covered diagonal is unknown and must be null.** Conflating those is
exactly the defect F2 describes, and it is why this type exists rather than a boolean.

### 3.2 Reported triangle

```
os(i, j)        = Σ AMOUNTOUTSTANDING  where accident period = i and As-at period = i + j
reported(i, j)  = cumulative_paid(i, j) + os(i, j)      if (i, j) ∈ covered_cells
                = null                                   otherwise
```

`build_triangle` gains `basis: "paid" | "reported"` and an optional `os_frame`. The paid path is
untouched and must stay bit-identical (its tests are the gate). Age-to-age, credibility and the
implied-CDF bridge all run on whichever basis was built — credibility is scored per triangle
already, so a sparse reported triangle scores itself honestly with no new logic.

### 3.3 Earned premium

New `module1_engine/earned_premium.py`, lifting the booked definition out of
`summarize_upr_by_reserving_class` without changing it:

```python
def earned_premium_by_period(premium_df, *, grain, bop, eop, upr_policy) -> pd.DataFrame
# → RESERVINGCLASS, RI_TREATY_TYPE, period, ep
```

* `GWP_p` from `ISSUEDATE` falling in `p`; `UPR(d)` from `unearned_fraction(df, d, upr_policy)`.
* Binds the **job's own** `upr_policy` out of `input_meta`, so the page's EP is the run's EP.
* Grain-agnostic by construction — every date is a period boundary from `PeriodGrain`.
* **Acceptance: at `grain=quarterly` it must reproduce `Reserve Summary.EP` to the cent for
  every class × treaty in the reference book.** That equality is the whole trust argument for
  the monthly and yearly numbers, which nothing else can check.

### 3.4 API

`GET /api/module1/jobs/{pk}/triangles/` — additive, backwards compatible:

| Param | Values | Notes |
|---|---|---|
| `basis` | `paid` (default) \| `reported` | `reported` 422s with the coverage reason when unsupported |
| `head_of_damage` | string | new filter; absent = all |
| `include` | csv of `ep`, `coverage` | opt-in so the default response does not grow |

Response gains `coverage` (§3.1), `earned_premium` (`{labels[], ep[], basis}`) and
`exposure` (`{paid_to_date[], reported_to_date[], paid_lr[], reported_lr[]}`), plus
`heads_of_damage` in the filter vocabulary. Existing keys keep their shape and meaning.

### 3.5 Frontend

* **Basis** segmented control beside Grain: Paid / Reported. Disabled with the coverage reason
  when the job's extract cannot support it — never silently showing paid relabelled.
* **Exposure card** under the triangle: accident period, EP, paid to date, reported to date,
  paid LR, reported LR. This is what makes BF/ELR selectable from the page.
* **Head of damage** filter, and a "reconciles to workbook `<file>.xlsx`" line when the three
  filters resolve to exactly one workbook.
* **Coverage banner** naming the valuation dates found, in the user's words.
* Header copy: "Diagnostic only" → **"Booking stays quarterly — nothing on this page writes to a
  workbook. Apply factors in Update Reserves."**
* `state/wizards/triangles.ts` → version 2 with `basis`, `headOfDamage`, `showExposure`, and a
  migration from v1.

### 3.6 Reconciliation — the trust anchor

One test asserts the whole chain end to end: for a reference job, the page's quarterly
`basis=reported` triangle, its EP vector and its LRs must equal the corresponding workbook's
`Reported Triangle`, `Reserve Summary.EP` and `Reported LR` for the same
class × head of damage × treaty. Without it, the page is a second implementation of the numbers
and will drift.

---

## 4. Work packages

Each ships independently and is useful on its own.

| WP | Deliverable | Files | Est. |
|---|---|---|---|
| **8.0** | **Data diagnostic first.** `ValuationCoverage` + a management command / read-only endpoint reporting, for a given job: OS valuation dates, extract shape, EP availability. Run it against the client's production job **before** building anything else. | `module1_engine/triangles.py`, `processing/views.py`, new `scripts/` command | 1–2 d |
| **8.1** | Copy fix + the D4 explanation in the UI (ship immediately; it is what the client actually complained about) | `TrianglesPage.tsx` | 0.5 d |
| **8.2** | `earned_premium_by_period` + quarterly reconciliation test; delete the dead `calculate_quarterly_premium` path | new `module1_engine/earned_premium.py`, `engine.py`, tests | 2–3 d |
| **8.3** | Reported basis in `build_triangle` + coverage-aware null handling | `module1_engine/triangles.py`, tests | 2–3 d |
| **8.4** | API: `basis`, `head_of_damage`, `include`, OS + premium loaders on the same 3-tier cascade as paid | `processing/views.py`, serializers, tests | 2–3 d |
| **8.5** | Frontend: basis control, exposure card, HOD filter, coverage banner, store v2 | `TrianglesPage.tsx`, `api/module1.ts`, `state/wizards/triangles.ts`, new `ExposurePanel.tsx`, tests | 3–4 d |
| **8.6** | Premium archiving (forward-only) + rollout note; response caching; perf budget; runbook entry | `processing/tasks.py`, `docs/PRODUCTION_RUNBOOK.md` | 1–2 d |

**11–18 working days**, and WP8.0 can change the shape of 8.3–8.5 — it is deliberately first.

---

## 5. Production readiness

**Bit-identical gate.** Every golden set in `benchmarks/goldens` (`summary_ref`,
`summary_ref_aliased`, `summary_ref_prewp1`, `policy_upr_ref`, `m1_large_claims_ref`,
`m1_upr_methods_ref`, `m2_allocate_ref`, `m2_pattern_ref`, `m2_process_ref`,
`m2_sensitivity_ref`) passes untouched after every step. The
paid triangle's existing tests are the regression suite for `build_triangle`'s new parameter.

**Performance.** The endpoint rebuilds frames from snapshots per request; adding OS and premium
roughly triples that. Budget: **p95 ≤ 2 s** for the reference book at monthly with
`include=ep,coverage`. Mitigations, in order: build each frame once per request and reuse it
across bases; cache the per-job coverage (it cannot change — the inputs are frozen); memoise the
response on `(job, grain, class, treaty, hod, basis, include)` with a short TTL. Measure before
caching; `stage_timer` already exists for the numbers.

**Tenancy and permissions.** Unchanged: `_get_accessible_job` + `module1.run`. The new loaders
read the same org-scoped snapshots and the same job-owned archive. No new file paths, no new
upload surface.

**Degradation matrix — every one of these is a stated message, never a silent zero:**

| Condition | Behaviour |
|---|---|
| OS extract diagonal-only | reported basis disabled, F2 explanation + data spec shown |
| Some valuation periods missing | those cells null; coverage banner names the dates present |
| Premium absent (pre-8.6 upload run) | EP panel unavailable with the reason and the fix ("re-run, or use a Dataset") |
| UPR policy absent from `input_meta` | EP computed on the engine default, labelled as such |
| Job not a Summary run / not succeeded | existing 422s, unchanged |

**Observability.** Log coverage shape and valuation count per request (cardinality-safe, no PII);
count `reported` requests refused by reason, so we learn how many clients have the F2 extract.

**Rollout.** All additive and default-off: `basis` defaults to `paid`, `include` defaults to
empty. Premium archiving is forward-only — the runbook must say so, and the UI must say so to the
user rather than showing an empty panel.

---

## 6. Test plan

* **Engine** — coverage classification for all four shapes on synthetic extracts; reported
  triangle nulls outside covered cells; a closed claim inside a covered diagonal reads 0, not
  null; EP grain-additivity (12 monthly = 4 quarterly = 1 yearly, per class × treaty); EP under
  each UPR method.
* **Reconciliation (the important one)** — quarterly EP == `Reserve Summary.EP`; reported
  triangle == workbook `Reported Triangle`; LRs == `Reported LR`.
* **API** — `basis=reported` refusal carries the reason; `include` shapes the payload; filters
  compose; org isolation; permission denial.
* **Frontend** — basis disabled with reason; exposure panel renders EP and LRs; coverage banner
  names dates; store v1→v2 migration; no numbers rendered where coverage is null.
* **Goldens** — every set in `benchmarks/goldens`, byte-identical, after each WP.

---

## 7. Risks and open questions

| # | Item | Owner | Notes |
|---|---|---|---|
| R1 | **Does the production OS extract carry a full inventory per valuation date?** | Client | Blocks 8.3–8.5. WP8.0 answers it from their own job. The reference book says no. |
| R2 | If R1 is "no": the booked `Reported CDF` is understated today, and it feeds two of five ultimate methods. | Client + actuary | Decision item — do **not** change the sheet silently. Quantify per class before proposing anything. |
| R3 | Monthly reported triangles need **month-end** valuations. Quarterly extracts can never produce them. | Client | Say so up front rather than shipping an empty monthly view. |
| R4 | EP at monthly is only as good as the UPR method at monthly boundaries (`full_premium_in_period` is defined on a lookback window). | Actuary | Test EP under each method; label the basis on screen. |
| R5 | Pre-8.6 upload-driven jobs can never show EP. | Us | Forward-only by construction; state it in the UI and the runbook. |

**To send the client, in one message:** (a) the triangles already drive the reserving calculation
and here is the chain; (b) the reported triangle and earned premium already exist at quarterly and
are already booked — we are adding them to this screen at all three grains; (c) for a reported
triangle at *any* grain we need the OS extract to carry the full open-claim inventory at each
valuation date, in this exact shape — please confirm what your extract contains.
