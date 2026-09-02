# WP3b — Future Cash Flow Override

> **Goal:** Let the actuary supply their own future claims cash flows — typically exported
> from another system — in place of the engine-derived projection, without letting a
> mismatched total silently misstate LIC.

Status: **descoped and implemented as WP3b-lite** (2026-08-21). Requirement 3.
Sections 1-8 are the shelved full design; §9 is the decision that descoped it; §10 is what was built.
Decisions: `docs/CLIENT_REQUIREMENTS_DECISIONS.md` §3 D2. Companion to
`PAYMENT_PATTERN_OVERRIDE_PLAN.md` (requirement 2, delivered).

This plan **replaces** the earlier combined pattern+cashflow draft, which carried two
assumptions that measurement disproved (§1.4, §1.5).

---

## 0. Client requirement

> "cashflow calculation already being done, need a place as an excel input if want to use
> different cash flows"

---

## 1. Verified ground truth

### 1.1 What the engine produces

`future_cf_df` — one row per `(RESERVINGCLASS, UWY, Accident_Period, GROSS/RI)`, one column
per development quarter:

```
shape 2,476 x 26  ->  64,376 cells
future_cf_df[c] = Future CF x additional_matrix[c]
Future CF       = IBNR + ULAE + Outstanding + SS      (exact, verified)
row sum         = Future CF                            (to 1.9e-9)
```

It feeds `Discounted CF CY` / `Discounted CF PY`, and through them `Discounting Impact` and
`Change in Discounting Impact`.

### 1.2 Nobody fills in 64,376 cells

Practical grains, measured on the reference book:

| Grain | Keys | Cells |
|---|---:|---:|
| Class | 12 | 312 |
| Class x treaty | 24 | 624 |
| Class x UWY | 83 | 2,158 |
| **Class x UWY x treaty** | **166** | **4,316** |
| Native (adds accident period) | 2,468 | 64,168 |

**Default grain: `(RESERVINGCLASS, UWY, GROSS/RI)`.** Large enough to carry genuinely
different cohort timing, small enough to export from another system and eyeball. Native
grain is accepted for completeness; class grain is accepted but see §1.4.

### 1.3 The failure mode, quantified

`Discounting Impact` subtracts the **supplied** total, not `Future CF`:

```python
merged_df["Discounting Impact"] = discounted_cf_cy_df[...].sum(axis=1) \
                                - future_cf_df[...].sum(axis=1)
```

So if a user supplies cash flows totalling `T` against the engine's `Future CF = F`, the
difference `(F − T)` is absorbed into a figure **labelled a discounting effect**. And
`GROSS LIC = components + Discounting Impact`, so it lands straight in LIC.

The real discounting effect is only **−3.60% of Future CF** (−7,397,885 on 205,442,901).
Against that:

| Supplied vs Future CF | Reported "Discounting Impact" | vs the real effect |
|---|---:|---:|
| −1% | −9,452,314 | **1.3x** |
| −5% | −17,670,030 | **2.4x** |
| −10% | −27,942,175 | **3.8x** |

A 5% under-supply understates LIC by **10,272,145** — an error larger than the entire
genuine discounting effect, wearing its name. **This is why the reconciliation gate is the
core of this work package, not a validation nicety.**

### 1.4 A class-grain, total-preserving cash flow IS the pattern override

Measured: applying a class-level vector as a cash-flow override (rescaled per row to that
row's Future CF) produces output **identical** to the WP3a pattern override on the LIC path —
`FutureCF` identical, `Discounting Impact` identical at −15,949,685 in both.

So requirement 3 only earns its place by delivering what requirement 2 cannot:

1. **Finer grain** — per (class, UWY[, accident period, treaty]), where cohorts genuinely run
   off on different timings. A class-level pattern forces every cohort of a class onto one shape.
2. **Externally-sourced amounts** — a projection from another system, where the total may
   deliberately differ from the engine's.

And (2) is precisely what §1.3 shows can corrupt LIC. The plan is built around that tension.

*The earlier combined draft treated the two features as symmetric. They are not.*

### 1.5 Overriding the matrix leaks into LRC — this must be stopped

`avg_df` is **derived from** `additional_matrix`:

```python
sum_columns = gross_only.groupby("RESERVINGCLASS")[dynamic_columns].sum()
avg_df      = sum_columns.div(total_sum, axis=0)
```

so a cash-flow override that rewrites matrix rows silently reshapes the LRC run-off.
Measured on a row-grain override: `GMM LRC_Discounted_CY` moved **−2.388%** with no LRC
input supplied at all.

That is wrong on the actuarial merits. A cash-flow override describes how **already-incurred**
claims will be paid; it says nothing about how **not-yet-incurred** claims (LRC) will run off.
Letting one reshape the other produces movements nobody can explain at review.

**Decision: compute `avg_df` from the PRE-override matrix.** Verified: doing so reproduces
the base `Payment Pattern` sheet exactly, and LRC stays at base. The earlier draft's claim
that cash flow "drives the LIC path only" was true as an *intent* and false as *behaviour*;
this makes it true in fact.

### 1.6 Acceptance map — row-grain override (per-UWY timing)

| Measure | base | override | delta |
|---|---:|---:|---:|
| `IBNR` | 117,385,053 | unchanged | **—** |
| `ULAE` | 9,883,771 | unchanged | **—** |
| `RA (OS)` / `RA (IBNR)` | 3,189,354 / 4,221,445 | unchanged | **—** |
| `Future CF` | 205,442,901 | 205,442,901 | **—** |
| `Discounting Impact` | −7,397,885 | −19,570,337 | **−164.540%** |
| `Change in Discounting Impact` | −384,266 | −1,342,835 | **−249.454%** |
| `PAA_LRC` | 442,956,700 | unchanged | **—** |
| `GMM LRC_Undiscounted` | 339,205,368 | unchanged | **—** |
| `GMM LRC_Discounted_CY` | 323,146,549 | *must be* unchanged | **—** (with §1.5) |

Row-sum identity preserved throughout. The structural zeros are the acceptance checks:
anything else moving means the override entered at the wrong place — and
`GMM LRC_Discounted_CY` is now one of them, which it would not have been without §1.5.

---

## 2. Design

### 2.1 Input shape

Long in the database, wide in Excel — the same split WP3a uses, for the same reason:

```
RESERVINGCLASS | UWY | GROSS/RI | 0 | 1 | 2 | ...
```

`UWY`, `GROSS/RI` and `Accident_Period` are all **optional** columns. Omitting one widens the
key: a sheet with only `RESERVINGCLASS` applies to every row of that class. This gives one
input shape that spans every grain in §1.2 without a mode switch.

Matching is on `canonical_class` (WP0/WP3a) plus exact `UWY` / treaty / accident period.

```python
class CashflowRow(_BaseRow):
    reserving_class = CharField(128, db_index=True)
    uwy             = IntegerField(null=True)      # null = all UWYs of the class
    gross_ri        = CharField(16, blank=True)    # "" = both treaty types
    accident_period = CharField(16, blank=True)    # "" = all accident periods
    dev_period      = IntegerField()
    amount          = DecimalField(18, 2)
```

New `Dataset.Kind.FUTURE_CASHFLOW`.

### 2.2 Resolution — most specific key wins

```
(class, uwy, treaty, accident_period)  ->  (class, uwy, treaty)  ->  (class, uwy)
                                       ->  (class, treaty)       ->  (class)
```

A row not covered by any key keeps the engine's own projection. Partial overrides are
genuinely partial; the report names exactly which rows were touched.

### 2.3 Modes — the reconciliation gate

| Mode | Behaviour |
|---|---|
| `shape_only` (**default**) | Take the supplied **timing**; rescale each matched row to that row's own `Future CF`. The row-sum identity holds by construction, so §1.3 cannot occur |
| `strict` | Amounts as supplied. **Fail the run** if any matched key's total differs from its `Future CF` by more than 1e-6, naming the keys and the gaps |
| `absolute` | Amounts as supplied, no rescale, no failure — **but the gap is surfaced, never hidden** (§2.4) |

`shape_only` is the default because it is the only mode in which a mistake cannot misstate
LIC. `strict` is for a user who believes their totals already agree and wants that checked.

### 2.4 `absolute` mode must not let the gap masquerade as discounting

The engine's `Discounting Impact` formula is **not changed** — changing it would break
bit-identity for every existing run. Instead, when `absolute` is used the run computes and
persists, per key and in total:

```
reconciliation_gap = Future CF (engine) − supplied total
```

and:

* records it in `override_report.reconciliation_gap`,
* emits a warning naming every key with a non-zero gap,
* stamps `input_meta["cashflow_unreconciled"] = True`,
* and the output workbook carries a `Cash Flow Reconciliation` sheet showing engine total,
  supplied total, gap, and the resulting `Discounting Impact` **split into** its genuine
  discounting component and the gap.

So the number is still produced, and the reader can see exactly how much of it is not
discounting. `absolute` is reachable but never silent, and never the default.

### 2.5 LRC is held at its pre-override value

Per §1.5, `avg_df` is computed from the matrix **before** the cash-flow override is applied.
Consequences, stated so the two features stay separable:

| Supplied | `avg_df` (LRC) | `additional_matrix` (LIC) |
|---|---|---|
| pattern only (WP3a) | the supplied from-inception pattern | re-based per row |
| cash flow only (WP3b) | **engine-derived, untouched** | overridden per matched row |
| both | the supplied pattern | pattern first, then cash flow on matched rows |

This is D2's precedence made concrete: cash flow wins for LIC, pattern owns LRC, and the run
warns when both cover the same rows.

### 2.6 UI

An `absolute`-mode run is a significant act, so the editor makes its consequence visible
before submit: per key it shows the engine's `Future CF`, the supplied total, and the gap,
with the gap column shaded and totalled. `shape_only` shows the same table with the gap
column greyed and annotated "will be rescaled".

The grid is grouped by class and collapsible to UWY / treaty, mirroring
`PaymentPatternEditor` so the two overrides feel like one family.

---

## 3. Backend changes

| File | Change |
|---|---|
| `module2_engine/cashflow_override.py` | **new** — `CashflowOverride`, key resolution, per-row rescale, `reconciliation_gap`, `OverrideReport` |
| `module2_engine/engine.py` | `cashflow_override` kwarg; applied to `future_cf_df` after `Future CF` is known; **`avg_df` computed from the pre-override matrix**; threaded through allocate / process / movement |
| `module2_engine/workbook_reconciliation.py` | **new** — the `Cash Flow Reconciliation` sheet (absolute mode only) |
| `datasets/models.py` | `CashflowRow`, `Kind.FUTURE_CASHFLOW` + migration |
| `datasets/services/wide_cashflow.py` | **new** — wide↔long with optional key columns (sibling of `wide_pattern.py`) |
| `datasets/services/{columns,excel_import,templates}.py`, `serializers.py` | template, unpivot, row serializer |
| `processing/{views,tasks,urls}.py` | `future_cashflow_dataset_id` + `cashflow_mode` on allocate / process / movement; `_load_cashflow_override` (snapshot-backed, with `inherit_from` as WP3a has); preview endpoint returning the engine projection at the chosen grain |
| `processing/benchmarks.py` + `benchmarks/fixtures/m2_cashflow_ref/` | golden, measure-level, per mode |

## 4. Frontend changes

| File | Change |
|---|---|
| `src/components/CashflowOverrideEditor.tsx` | **new** — grouped grid, engine vs supplied vs gap, mode selector |
| `src/api/module2.ts` | preview DTO, override payload, `savePatternDraftAsDataset` sibling |
| `src/api/datasets.ts` | new kind, label, grid columns |
| `src/pages/IbnrAllocationPage.tsx`, `src/state/wizards/ibnr.ts` | second collapsible step beside the pattern editor |
| `src/pages/DataHubPage.tsx` | new tab + create-dialog entry |

## 5. Bit-identity and goldens

* No override → **value-identical**; existing goldens are the gate.
* **§1.6 structural invariants** asserted under any override: `IBNR`, `ULAE`, `RA (OS)`,
  `RA (IBNR)`, `Future CF`, `PAA_LRC`, `GMM LRC_Undiscounted` **and now
  `GMM LRC_Discounted_CY`** (the §1.5 fix is what makes the last one an invariant).
* **The equivalence check**: a class-grain, total-preserving cash-flow override must produce
  the same `FutureCF` and `Discounting Impact` as the equivalent WP3a pattern override
  (§1.4). If the two ever diverge, one of them is wrong.
* New golden `m2_cashflow_ref` in each of the three modes.

## 6. Tests

**`module2_engine/tests/test_cashflow_override.py`** (new)
* key resolution: most specific wins; `(class)` applies where `(class, uwy)` is absent
* an unmatched row keeps the engine projection byte for byte
* `shape_only` preserves the row-sum identity for **every** matched row
* **`strict` fails and names each key whose total differs**, with the gap
* **`absolute` proceeds, records `reconciliation_gap`, and warns** — and the recorded gap
  equals `Future CF − supplied`, so §1.3's distortion is measurable rather than hidden
* **§1.5: `GMM LRC_Discounted_CY` and the `Payment Pattern` sheet are unchanged** under a
  cash-flow override — the regression that the pre-override `avg_df` fix exists to prevent
* **§1.4 equivalence**: class-grain cash flow ≡ WP3a pattern on `FutureCF` and
  `Discounting Impact`
* the §1.6 acceptance map, measure by measure
* pattern + cash flow together: pattern owns `avg_df`, cash flow owns matched matrix rows,
  and a warning names the overlap
* a matched row with `Expected Unpaid % == 0` keeps `[1, 0, 0, …]`
* supplied vector longer / shorter than the horizon truncates / zero-fills, with a warning

**`datasets/tests/test_cashflow_io.py`** (new)
* wide→long→wide lossless with every combination of optional key columns present/absent
* a sheet with `UWY` but no `GROSS/RI` resolves to both treaty types
* duplicate keys rejected naming the row

**`processing/tests/test_cashflow_api.py`** (new)
* snapshot-on-run; later dataset edits do not change a stored run
* `strict` failure surfaces a 422 naming the offending keys, not a 500
* `absolute` stamps `cashflow_unreconciled` and the workbook carries the reconciliation sheet
* movement inherits the cash flow its process job used (the WP3a §audit lesson, applied up front)
* preview resolves `Combined_Summary` through a process job's allocate ancestor (likewise)

## 7. Edge cases

* **Key present in the override, absent from the data** → warning, never silent.
* **Overlapping keys of equal specificity** → rejected at import, naming both.
* **Negative amounts** (recoveries) permitted, flagged.
* **`Future CF == 0` for a matched row** → `shape_only` has nothing to rescale to; the row
  keeps `[1, 0, 0, …]` and is listed in the report.
* **RI rows**: a key without `GROSS/RI` covers both; the `Payment Pattern` sheet stays
  gross-only and, per §1.5, unchanged.
* **Movement disclosure**: inherits like WP3a, and an `absolute`-mode ancestor propagates the
  `cashflow_unreconciled` stamp so a disclosure built on unreconciled cash flows says so.

## 8. Estimate

| | |
|---|---|
| override engine (resolution, rescale, gap, pre-override `avg_df`) | 3d |
| reconciliation sheet | 1d |
| dataset kind, wide↔long with optional keys, template | 2.5d |
| preview endpoint + job wiring (allocate / process / movement) | 1.5d |
| frontend editor | 3.5d |
| tests | 3d |
| goldens (3 modes) + validation | 1d |
| **Total** | **~15.5 days** |

## 9. Decision — requirement 3 is satisfied by requirement 2's mechanism

The §9 question is answerable from the code and the client's own data. It was, and the
answer is: **do not build WP3b as scoped.** Five findings, all measured:

### 9.1 The engine's cash-flow projection has exactly one degree of freedom per (class, treaty)

That degree of freedom is the from-inception payment pattern, and requirement 2 now exposes
it. Proven by the WP3a no-op identity: feeding the derived pattern back reproduces
`additional_matrix` — and therefore `FutureCF` — to `2.22e-16`. There is no information in
the engine's cash-flow projection that a class-level pattern cannot express.

### 9.2 UWY carries no timing information whatsoever

Across the reference book, **520 of 592** `(class, treaty, Age)` groups span more than one
UWY. Within every one of them the cash-flow rows are **identical — maximum spread
`0.000e+00`**. The engine assigns timing by `(class, treaty, Age)`; UWY is carried for
reporting only.

### 9.3 A finer grain would be internally inconsistent, not merely unnecessary

`IBNR Summary` — the source of `Paid CDF`, which is the *sole* origin of all timing — has
**no UWY column**. Its keys are `(Accident_Period, RESERVINGCLASS, GROSS/RI)`. A per-UWY
cash flow would distribute a reserve derived from accident-period CDFs using a timing
dimension nothing upstream supports. At review nobody could say where the UWY timing came
from, because the model that produced the amounts does not have one.

### 9.4 There is no external cash-flow projection to import

The client already supplies a cash-flow Excel input — `Expense-CF` — and it is **actual
ledger cash flows** (Premium Received, Claims Paid, acquisition cash flows), consumed by the
movement disclosure's "Cash flows" section. That is a different object from a *projected
future payout*. Nothing in the repo, the fixtures or the disclosure mapping shows an external
projection system, and no maturity/liquidity disclosure exists that would require one.

### 9.5 What IS missing is reach and visibility, not mechanism

Two concrete gaps, and between them they are almost certainly what prompted the request:

* **The projection cannot be read in-app.** `FutureCF` is 2,476 x 30 = **74,280 cells**
  against a `MODULE1_OUTPUT_PREVIEW_MAX_CELLS` guard of **20,000**. It is in the output ZIP,
  but the in-app preview refuses to render it. The client is being asked to accept cash flows
  they cannot look at. *"cashflow calculation already being done"* reads exactly like someone
  who knows it happens and cannot see it.
* **The override is hard to find and does not reach allocate.** It is offered only on the
  process step, labelled "payment pattern". Someone looking for "cash flows" will not find
  it, and the allocate step has no UI path at all even though the API accepts one.

---

## 10. Recommendation — WP3b-lite, ~3 days instead of ~15.5

1. **Cash-flow projection view + export** at `(RESERVINGCLASS, UWY)` x development quarter —
   83 x 26 = 2,158 cells, comfortably inside the preview guard — showing undiscounted,
   discounted (CY) and the resulting discounting impact, with an Excel export. This is the
   actuarial-grade view of the object the client is asking about, and today there is none.
2. **Surface the existing override on the allocate step as well**, and frame it under both
   names ("payment pattern / cash-flow timing") so it is discoverable by someone searching
   for cash flows. The mechanism is already built, tested and golden-guarded.
3. **Do not build `absolute` amounts.** §1.3 quantified the hazard: a 5% mismatch understates
   LIC by 10,272,145, disguised as a discounting effect larger than the entire real one. It is
   not worth carrying that risk for a capability nothing in the client's stack currently feeds.

**Sections 1-8 of this plan remain valid and stay on the shelf.** The trigger to build them is
specific and testable: *the client produces an actual cash-flow file at a grain finer than
(class, treaty), or asks for a maturity/liquidity disclosure.* If either happens, the design —
including the §1.5 LRC-leak fix and the §2.4 reconciliation sheet, both of which are real and
would be needed — is ready to implement.

If neither happens, requirement 3 is closed by requirement 2 plus the two items above.


---

## 11. Implementation status (2026-08-21) — WP3b-lite

Built as recommended in §10. **~15.5 days of planned work replaced by a focused change**,
because §9 established that the override mechanism requirement 3 asks for already exists.

**Backend**

| File | State |
|---|---|
| `processing/views.py` | **new** `Module2CashflowProjectionView` — four aggregation grains, undiscounted + discounted profiles, discounting impact; resolves `Combined_Summary` through a process job's allocate ancestor; **re-applies the job's own pattern override** so the view shows what the job actually ran with |
| `processing/urls.py` | `module2/jobs/<pk>/cashflow-projection/` |
| `processing/tests/test_cashflow_projection_api.py` | **new** — 10 tests |

**Frontend**

| File | State |
|---|---|
| `src/components/CashflowProjectionView.tsx` | **new** — grain selector, undiscounted/discounted toggle, per-row shading, totals, CSV export |
| `src/components/CashflowProjectionView.test.tsx` | **new** — 13 tests |
| `src/api/module2.ts` | projection DTOs, `fetchCashflowProjection`, `cashflowProjectionToCsv` |
| `src/pages/IbnrAllocationPage.tsx` | the panel is now **"Cash flows & payment pattern"** — projection first, then the pattern described as "the timing behind those cash flows" |

**Verification**

* 243 Django tests (2 pre-existing Redis-broker failures, unrelated); 118 frontend tests;
  `vite build` clean; `tsc` unchanged at its 45-error baseline. No engine change, so every
  golden is untouched by construction.
* Totals reconcile across **all four grains** — aggregation neither creates nor destroys value.
* The default grain is asserted to fit inside `MODULE1_OUTPUT_PREVIEW_MAX_CELLS`; the raw
  sheet it replaces does not.
* A job that ran **with** a pattern override renders a different profile but the **same
  undiscounted total** — money fixed by the reserve, timing set by the pattern.

**Deliberate deviations from §10**

* §10.2 said "surface the override on the allocate step too". Not done, and it should not be:
  the pattern editor is seeded from a *completed* run's preview, so on the pre-run allocate
  step there is nothing to seed it from. An unseeded editor there would be worse, not better.
  The discoverability half of §10.2 — the reason it was listed — is delivered by renaming the
  panel and leading with the projection. The allocate **API** already accepts an override for
  programmatic callers.
* No `absolute` mode, per §10.3. The §1.3 hazard is unchanged and unmitigated by anything
  built here.

**One UI defect the render tests caught**: the basis toggle and the totals cards both used the
bare words "Undiscounted" / "Discounted", so two different controls read identically. The
totals are now "Total undiscounted" / "Total discounted (CY)".
