# WP5 — Large Claims Summary & Triangle Exclusion

> **Goal:** Surface the largest paid and outstanding claims in the data, let the actuary strike them
> out, and rebuild the triangles and factors without them — with the excluded exposure accounted for
> explicitly rather than silently dropped.

Status: planned (2026-09-01), **verified against the reference book**. Decisions: `docs/CLIENT_REQUIREMENTS_DECISIONS.md` §3 D5.
Requirement 6. Depends on WP0 and WP1 (reuses `TriangleGrid`).

---

## 0. Client requirement

> "Summary of high/top 10 claims paid and OS in the data, and a functionality if we remove those in
> our experience, triangles should be adjusted through a strikethrough feature"

## 1. Verified ground truth

Measured against the client reference book; the measurements are reproduced so a reviewer
can re-run them.

### 1.1 Claim identity does not exist in the system yet

`import_data` reads a fixed column list that omits `CLAIMNUMBER`, and
`ClaimsPaidRow` / `ClaimsOSRow` have no such field. The column **is present in the source
files** — verified — but never reaches a DataFrame. `REPORTEDDATE` is likewise present and
discarded. Everything downstream is an aggregate.

Confirmed still true today: `module1_engine.triangles` degrades to a warning
("no CLAIMNUMBER column") when handed exclusions on a frame from `import_data`.

### 1.2 The data grain, measured

| File | Grain | Evidence |
|---|---|---|
| Claims paid | `(claim × head of damage × treaty × transaction)` | 6,580 rows / 1,645 claims; 3,290 single-transaction slices, 1,645 two-transaction |
| Claims OS | `(claim × as-at × treaty)` | 6,272 rows / 1,685 claims; 1–4 snapshots per claim, mean 1.9 |

`CLAIMNUMBER` quality: zero nulls in either file, 836 claims present in both, and **no claim
spans more than one reserving class**. Safe as a key.

### 1.3 Ranking must be slice-scoped — and the OS rule is not optional

**Paid.** A naive `groupby(CLAIMNUMBER).sum()` adds GROSS to RI and nets Payment against
Salvage. Scoped to `GROSS` / `Payment`, the top 10 claims are **22.3%** of gross paid, the
largest single claim **7.9%**. (An earlier draft quoted 29.9%; that figure came from summing
across every treaty and head of damage and is not a meaningful ranking.)

**OS.** Summing across as-at snapshots multiply-counts one reserve. Measured:

| rank | naive sum across snapshots | latest as-at (correct) |
|---|---|---|
| 1 | `SIL/D/C003/…1213/…` 38,994,200 | `SIL/F/C004/…0209/…` 6,000,000 |
| 2 | `SIL/F/C004/…0209/…` 6,000,000 | `SIL/D/C003/…1213/…` 3,999,200 |

The naive sum **overstates the top claim by 6.5×** and **ranks a different claim first**. A
naive implementation would not merely be imprecise — it would list the wrong claims.

### 1.4 Excluding large claims can INCREASE the reserve

The finding that matters most, and the one that inverts this plan's default.

Large claims pay early and large. Removing them shrinks the early diagonal more than the
tail, so the attritional book looks *slower* developing:

| development | all claims | ex top-10 | change |
|---:|---:|---:|---:|
| 0 | 3.4746 | 3.3189 | −4.5% |
| 2 | 1.4075 | 1.2008 | −14.7% |
| 4 | 1.2613 | 1.4975 | **+18.7%** |
| 5 | 1.1972 | 1.3806 | **+15.3%** |

So 2016-Q4's ultimate rises from 46,338,499 to **63,444,124 (+36.9%)** under a mode that
keeps large claims in the base while taking factors from a triangle without them. A user who
clicks "remove the top 10 claims" will not expect the reserve to go **up**.

### 1.5 The three modes, measured

GROSS / Payment, top-10 excluded, against a base ultimate of 487,468,241:

| mode | total ultimate | vs base |
|---|---:|---:|
| 1 `exclude_from_ldf_only` — factors ex-large, base keeps large | 552,106,291 | **+13.3%** |
| 2 `exclude_and_add_back` — attritional developed, large added at face | 469,104,811 | **−3.8%** |
| 3 `exclude_entirely` | 447,515,034 | −8.2% |

**Mode 1 is internally inconsistent**: it applies factors derived from the attritional
population to a base containing the large claims, implicitly assuming those claims will
develop like attritional ones — which contradicts the reason they were excluded. It
double-counts large-claim development, which is why it is the *largest* mover and in the
direction nobody intends.

An earlier draft of this plan made mode 1 the default on the grounds that it was "least
intrusive". Measurement says it is the **most** intrusive. See §2.3.

### 1.6 The strikethrough surface does not exist yet

This plan previously assumed `TriangleGrid` would arrive with WP1 (requirement 7). It has
not been built — triangles render inline inside `ReserveCdfEditor` from a `grid` payload.
Since requirement 6 is being delivered before requirement 7, **this work package extracts
`TriangleGrid`**, and WP1 extends it later for factor exclusion. The roadmap dependency is
reversed accordingly.

## 2. Design

### 2.1 Ranking must be slice-scoped, never a naive sum

A naive `groupby(CLAIMNUMBER).sum()` over `AMOUNTPAID` **adds GROSS to RI and nets Payment against
Salvage** — a meaningless ordering. The ranking is therefore defined over an explicit slice:

```
rank_paid(slice) = SUM(engine "Amount") over rows matching the slice, grouped by CLAIMNUMBER
                   default slice: RI_TREATY_TYPE == "GROSS" AND HEADOFDAMAGE == "Payment"

rank_os(slice)   = SUM over rows at the LATEST "As at" per claim, grouped by CLAIMNUMBER
                   default slice: RI_TREATY_TYPE == "GROSS"
```

OS **must** use the latest as-at. Summing across as-at snapshots multiply-counts one reserve — with
up to 8 snapshots per claim in the reference data, by up to 8x.

Ranking uses the engine's derived `Amount` column (which already handles the Motor/recovery
substitution), not raw `AMOUNTPAID`, so the report agrees with the triangles it is used to adjust.

**No assumption is made about rows per claim.** The reference file's uniform 4 rows per claim is an
artefact of that extract; aggregation is by explicit key and the tests assert behaviour under
1, 2 and n transactions per slice.

### 2.2 Selection: top-N and threshold

| Mode | Parameter | Note |
|---|---|---|
| `top_n` | N, default 10 | What the client asked for |
| `threshold` | amount | More defensible period-over-period; a fixed count silently changes the exclusion basis as the book grows |

Both are scoped per `(RESERVINGCLASS)` by default, optionally book-wide. Per-class is the default
because factor selection is per class.

### 2.3 Treatment modes — default corrected to `exclude_and_add_back`

| mode | effect | default |
|---|---|---|
| `exclude_and_add_back` | Attritional triangle developed with attritional factors; the excluded claims' actual paid + OS added back as a separate `Large Loss` line, plus an optional user-entered large-loss IBNR loading | **yes** |
| `exclude_from_ldf_only` | Factors from the attritional triangle applied to a base that still contains the large claims | no — **warned** |
| `exclude_entirely` | Removed from base and factors alike | no — warned |

`exclude_and_add_back` is the default because it is the only mode whose **factors and base
describe the same population**. Measured at −3.8% against base (§1.5), it behaves the way a
user expects: taking large losses out of the development pattern and putting their actual
cost back.

`exclude_from_ldf_only` remains available — some actuaries genuinely want to strip a
distorting claim from *factor selection* alone — but it is not the default and the UI states
its measured effect (**+13.3%** on this book) before it can be chosen. The earlier draft had
this backwards.

`exclude_entirely` understates the ultimate by dropping real cost; it stays available and
loudly warned.

**Every mode shows per-cohort effects, not just a total.** §1.4 measured a single accident
quarter rising 36.9% under mode 1 while the book moved 13.3%. A total alone would hide that,
and the direction — up, on an exclusion — is the surprise that most needs surfacing.

### 2.4 `TriangleGrid` is extracted here, not inherited

Per §1.6, this work package extracts the triangle renderer out of `ReserveCdfEditor` into a
reusable `TriangleGrid` with per-cell strikethrough. Requirement 7 then extends the same
component for *factor* exclusion rather than building its own.

Two exclusion sources with different consequences must not look identical:

| source | treatment |
|---|---|
| cell affected by an excluded **claim** (this WP) | strikethrough, muted, left accent bar |
| **factor** excluded by the user (WP1) | strikethrough, muted, dotted underline |
| both | accent bar + dotted underline |

Hovering names the reason and, for claims, the claim numbers contributing to that cell.

### 2.5 Prerequisite — the Reserve Summary's appended formulas use hardcoded column letters

`exclude_and_add_back` (the default, §2.3) needs a `Large Loss` line on the Reserve Summary.
That is not currently safe.

The sheet is written with six base columns — `A` Accident_Period, `B` EP, `C` Paid Claims,
`D` OS Claims, `E` Reported Claims, `F` Reported LR — and `run_update_reserve_summary` then
appends thirteen more. Its start column is computed:

```python
new_headers_start_col = len(existing_headers) + 1
```

but every formula it writes hardcodes letters:

```python
data['ELR Ultimate']    = f'=IFERROR(G{r} * B{r},0)'
data['Ultimate Claims'] = f'=IF(O{r}="Paid CL", J{r}, IF(O{r}="Reported CL", K{r}, ...'
data['CDF']             = f'=IFERROR(P{r}/C{r},0)'
```

Add a seventh base column and the append start shifts to `H`, while the formulas still point
at `G` — which is now `Large Loss`. **Every appended formula in every reserve workbook would
be silently wrong**, and no existing test compares formula strings.

**So this work package first makes the letters positional**, derived from the header row by
name. That is bit-identical today (six headers still yield `G`…`S`) and is asserted as such
by comparing the generated formula strings against the current output before any base column
is added. Only then is `Large Loss` introduced.

This is a prerequisite, not a nice-to-have: without it the default treatment mode corrupts
the workbook it is meant to improve.

### 2.6 Audit

An exclusion is a material judgement. Persisted to `input_meta["large_claims"]` and snapshotted:

```json
{
  "mode": "exclude_from_ldf_only",
  "selection": {"kind": "top_n", "n": 10, "per_class": true},
  "slice": {"treaty": "GROSS", "head_of_damage": "Payment"},
  "excluded": [{"claim_number": "SIL/D/C003/0000001213/0317/001",
                "reserving_class": "Banker's Blanket",
                "paid": 7665800.0, "os": 0.0, "manual": false}],
  "manual_additions": [], "manual_removals": [],
  "actor": "user@example.com", "applied_at": "2026-08-21T10:14:03Z"
}
```

`manual` distinguishes claims the rule selected from claims the actuary added or removed by hand —
the part a reviewer will ask about.

## 3. Backend changes

| File | Change |
|---|---|
| `module1_engine/engine.py` | `import_data` reads `CLAIMNUMBER` and `REPORTEDDATE`; `run_generate_summary` accepts `excluded_claims` + `mode`; filter applied at the point dictated by mode |
| `module1_engine/large_claims.py` | **new** — `rank_paid`, `rank_os`, `select_claims`, `LargeClaimReport` |
| `datasets/models.py` | `claim_number`, `reported_date` on `ClaimsPaidRow` / `ClaimsOSRow` + migration |
| `datasets/services/columns.py` | map entries; `claim_number` required, `reported_date` optional |
| `datasets/services/templates.py` | `COLUMN_META` entries |
| `processing/services/large_claims.py` | **new** — build the report from staged frames or a source job |
| `processing/views.py` | `Module1LargeClaimsView` (report), `excluded_claims` on the summary job view |
| `processing/tasks.py` | thread exclusions through; persist the audit block |

**Backfill note.** `claim_number` is `blank=True` on existing rows. Datasets imported before WP5 have
no claim numbers; the large-claims report reports that plainly rather than showing an empty top-10.

## 4. Frontend changes

| File | Change |
|---|---|
| `src/components/LargeClaimsPanel.tsx` | **new** — ranked table (claim, class, paid, OS, % of class total, cumulative %), select-all-top-N, threshold input, manual add/remove, mode selector |
| `src/components/TriangleGrid.tsx` | extend for claim-driven strikethrough with source attribution |
| `src/pages/SummaryGeneratorPage.tsx` | optional large-claims step before generate |
| `src/api/module1.ts` | `LargeClaimReportDto`, `ExcludedClaims` |
| `src/state/wizards/summary.ts` | persist selection, mode and manual edits |
| `src/data/fileSchemas.ts` | `CLAIMNUMBER` required, `REPORTEDDATE` optional on both claims schemas |

The panel shows **cumulative % of class paid** beside each claim. "These 10 claims are 29.9% of paid"
is the number that tells an actuary whether exclusion is warranted; the individual amounts do not.

## 5. Bit-identity and goldens

* No exclusions supplied → **bit-identical**. Reading two extra columns does not change any
  aggregate; asserted explicitly, because widening `needed_columns` touches a hot path.
* New goldens from the reference fixture for each of the three modes with the default top-10.

## 6. Tests

**`module1_engine/tests/test_large_claims.py`** (new)
* ranking excludes RI and Salvage rows under the default slice; the top-10 share is 22.3%
* **OS ranks on the latest as-at**: the naive sum overstates the top claim 6.5× and returns
  a different claim first — both asserted, because a naive implementation lists the wrong claims
* a claim with 1, 2 and n transactions in a slice aggregates correctly — the grain assumption
  is explicitly not relied upon
* ranking uses the engine's derived `Amount`, so it matches the triangles it adjusts
* `top_n` per class vs book-wide differ on a crafted fixture
* `threshold` and `top_n` agree when the threshold is set to the Nth value
* **the §1.5 mode table**, asserted mode by mode: `+13.3% / −3.8% / −8.2%`
* **§1.4 regression**: under `exclude_from_ldf_only`, 2016-Q4 rises 36.9% — the
  counter-intuitive direction is pinned so nobody "fixes" it into silence
* `exclude_and_add_back` reconciles: attritional ultimate + large-loss actuals equals the
  reported total
* excluding every claim in a class → empty triangle handled, warned, no crash
* an excluded claim number absent from the data → warning, not error

**`processing/tests/test_large_claims_api.py`** (new)
* the report works from a source job and from staged uploads
* datasets without claim numbers return an explicit "not available", not an empty list
* the audit block records claim numbers, mode, threshold, actor and timestamp, and
  distinguishes rule-selected from manually added claims

**`datasets/tests/test_dataset_api.py`**
* `claim_number` and `reported_date` round-trip through import, template and engine adapter

## 7. Edge cases

* **Same claim large in paid and in OS** → one entry, both columns populated; never double-listed.
* **Claim spanning reserving classes** — not present in reference data but not guaranteed. Excluded
  per (claim, class) pair so a cross-class claim is handled per class rather than being globally
  dropped.
* **Claim excluded from LDF but with the only observation in a development column** → that column
  loses its only factor; the column renders blank and WP1's "no valid cells" path applies.
* **Negative claim totals** (recoveries exceeding payments) — rankable and shown; "largest" is by
  absolute magnitude with the sign displayed.
* **Threshold above every claim** → empty selection, stated plainly.
* **Motor recovery substitution** — the engine substitutes `AMOUNTRECOVERED` for `AMOUNTPAID` on
  Motor recovery heads; the ranking uses the post-substitution `Amount`, so the report can legitimately
  differ from a naive spreadsheet sort of `AMOUNTPAID`. Documented in the panel.

## 8. Estimate

| | |
|---|---|
| plumb `CLAIMNUMBER` + `REPORTEDDATE` (engine, models, columns, templates, schemas) | 2d |
| ranking service (slice-scoped paid, latest-as-at OS, top-N + threshold) | 1.5d |
| the three treatment modes + per-cohort impact | 2d |
| extract `TriangleGrid` with claim strikethrough (§2.4) | 2.5d |
| large-claims panel + mode selector with measured effects | 2.5d |
| tests | 2.5d |
| goldens per mode | 1d |
| **Total** | **~14 days** |

One day above the pre-verification estimate: extracting `TriangleGrid` moved into this work
package (§1.6) and the per-cohort impact view became mandatory rather than optional (§1.4).

## 9. What changed after verification

* **The default treatment mode was wrong.** `exclude_from_ldf_only` was chosen as "least
  intrusive"; it measures **+13.3%**, the largest move of the three and in the direction
  opposite to intent, because it applies attritional factors to a base containing the large
  claims. `exclude_and_add_back` (−3.8%) is now the default.
* **Excluding claims can raise the reserve** — 2016-Q4 by 36.9%. Per-cohort impact is now a
  required part of the UI, not a nicety.
* **The top-10 share is 22.3%, not 29.9%** — the earlier figure summed across treaty and head
  of damage, which is exactly the ranking error §2.1 exists to prevent.
* **`TriangleGrid` is built here**, not inherited from WP1, since requirement 6 ships first.

## 10. Implementation status — built

Implemented and tested. What follows records where **building it changed the plan**, because
in every case the plan was wrong in a way only measurement exposed.

### 10.1 The default mode did nothing (defect found during implementation)

`ExclusionPlan.adds_back` was a correct, unit-tested property that **no caller consumed**.
`grep adds_back module1_engine/engine.py` returned nothing. The default mode therefore
filtered the triangles and the base and never added the cost back — it behaved exactly like
`exclude_entirely`, understating every ultimate by the large claims' full incurred. All 16
`ExclusionPlan` unit tests passed throughout, because none of them reached a workbook.

This is the second time in this project that a plumbing gap survived a green unit suite (the
first was the Module 2 pattern override). The countermeasure applied here:
`processing/tests/test_large_claims_api.py::ExclusionReachesTheWorkbookTests` runs the real
engine over the reference fixtures and **reads the produced workbook's header row**.

### 10.2 The routing table changed

The plan said add-back filters the base and then adds back. It does not, and must not:

| mode | triangles filtered | base filtered | add-back columns |
|---|---|---|---|
| `exclude_and_add_back` | yes | **no** | yes |
| `exclude_from_ldf_only` | yes | no | no |
| `exclude_entirely` | yes | yes | no |

The Reserve Summary's Paid / OS / Reported columns must keep tying to the ledger, and the BF
ultimates read their known component straight from them — filtering the base would have
silently changed BF as well as CL. The split is carried instead in two new base columns,
`Large Paid` and `Large OS`, written **only** in add-back mode, and consumed by the chain
ladder:

```
Paid CL Ultimate     = (Paid Claims     - Large Paid)     x Paid CDF     + Large Incurred
Reported CL Ultimate = (Reported Claims - Large Incurred) x Reported CDF + Large Incurred
Large Incurred       = Large Paid + Large OS
```

Absent those columns both reduce to `base x CDF` — the historic expression — which is what
keeps all eight pre-existing goldens bit-identical.

### 10.3 Large claims re-enter at known incurred, not paid-to-date

The plan's −3.8% added back **paid-to-date only**, which silently assigns an open large
claim a zero case reserve. The implementation adds back paid **plus case** (PKR 5,069,200 on
the reference book), so a large claim carries no IBNR of its own — its case reserve is taken
as its ultimate, the standard treatment. Re-measured: **−2.7%**.

### 10.4 Measured impact is not observable in a full-summary run

An end-to-end run of `run_generate_summary` + `run_update_reserve_summary` reported
`exclude_from_ldf_only` at **+0.00%**. That is an artifact, not a result: the engine writes
Selected CDF as the placeholder formula `=1`, nothing evaluates it, and the reader's
blank→2.0 fallback makes every CDF the constant 2.0. The workbook's ultimates are
placeholders until an actuary selects factors in Excel.

Consequence for the golden: `m1_large_claims_ref` is frozen at the **measure** level
(per-cohort paid-to-date, both CDF vectors, and all four ultimates) rather than by running a
full summary, because a full-summary golden literally cannot tell the three modes apart.

### 10.5 Two hardcoded column letters, one already latent

`test_reserve_summary_formulas.py` had made the appended formulas positional. Add-back's two
extra base columns found a second instance the earlier fix missed: the **Selected Method
data-validation dropdown** was attached to a literal `O2:O{max_row}`. With eight base columns
Selected Method is at Q, so the dropdown would have landed on Paid CDF. Now derived from the
same header map. The formatting loop's `for col in ws.columns` was also renamed `sheet_col`
so it cannot shadow that map.

### 10.6 A selection that matches nothing is now reported

An exclusion whose claim numbers match no row produces output **identical to no exclusion** —
the one failure mode of this feature that is invisible in the result, and the likely one
(re-uploaded files, a selection made against a previous run). `ExclusionPlan.match_report`
measures it, `run_generate_summary` fills the caller's `run_report`, the summary task
persists it to `input_meta["large_claims"]["match"]`, and the wizard renders it — destructive
styling when nothing matched, warning when only some did.

### 10.7 Files

| | |
|---|---|
| `module1_engine/large_claims.py` | ranking, `ExclusionPlan`, `period_totals`, `match_report` |
| `module1_engine/engine.py` | `Large Paid`/`Large OS` columns, add-back ultimates, positional dropdown, `run_report` |
| `datasets/models.py` + migration `0006` | `claim_number` / `reported_date` on both claims row models |
| `datasets/services/columns.py`, `templates.py` | the Excel-free path can now exclude claims |
| `processing/views.py`, `processing/tasks.py` | endpoint, exclusion persistence, match report |
| `benchmarks/fixtures/m1_large_claims_ref` | the mode golden (§10.4) |
| `src/components/LargeClaimsPanel.tsx`, `TriangleGrid.tsx` | ranked table + mode selector, strikethrough |
| `src/pages/SummaryGeneratorPage.tsx`, `src/state/wizards/summary.ts` | wizard step + persisted selection |

### 10.8 Verification

* `pytest module1_engine/tests` — **106 passed**, all **9** goldens green.
* `manage.py test processing.tests.test_large_claims_api` — **15 passed**.
* `manage.py test datasets` — **52 passed**.
* `manage.py test processing` — **200/202**; the two failures are
  `test_dataset_e2e`, which need a live Redis broker for `.delay()` and fail identically on
  this machine regardless of these changes.
* `vitest` — **149 passed** (20 files). `tsc` at its unchanged 45-error baseline.

**Not verified:** nothing here has been exercised against the running stack (Postgres +
Redis + Celery + Vite). The same caveat stands for items 1–5.
