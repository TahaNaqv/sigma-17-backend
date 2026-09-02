# WP2 — UPR Method Registry & Per-Line-of-Business Policy

> **Goal:** Replace three copy-pasted, string-matched, unreachable UPR branches with a named method
> registry and an auditable per-line-of-business policy the actuary controls from the interface.

Status: **implemented** (2026-08-21). Decisions: `docs/CLIENT_REQUIREMENTS_DECISIONS.md` §2 F2, §3 D3.
Requirement 4. Depends on WP0.

---

## Implementation status (2026-08-21)

Delivered. Sections below are the as-built design.

**Backend**

| File | State |
|---|---|
| `module1_engine/upr_methods.py` | **new** — registry of six methods, normalised resolution, `UprPolicy`, `unearned_fraction` with eligibility separated |
| `module1_engine/upr_guard.py` | **new** — book-suitability checks for the term-based methods |
| `module1_engine/engine.py` | the three duplicated `np.select` blocks replaced by one resolver call; `upr_policy` threaded through all four call sites including `run_policy_level_upr` |
| `tenants/models.py` | `UprMethodPolicy`, `UprMethodRule` (+ migration `0004`), versioned on edit |
| `tenants/{serializers,views,urls}.py` | policy CRUD, method catalog endpoint, per-lever param validation |
| `processing/{views,tasks,urls}.py` | `_attach_upr_policy` snapshots the active policy at job creation; `_load_upr_policy` rebuilds it at run time; `Module1UprImpactView` |
| `datasets/services/engine_adapter.py` | `dataset_to_dataframe` — live rows for previews that run before a job exists |
| `accounts/.../seed_rbac.py` | `upr_policy.view` / `upr_policy.manage` |
| `benchmarks/fixtures/m1_upr_methods_ref/` | **new golden** — every method's per-class UPR, frozen |

**Frontend**

| File | State |
|---|---|
| `src/pages/UprMethodPolicyPage.tsx` | **new** — rule table, method catalog from the API, "apply suggested", guard warnings |
| `src/components/UprImpactPreview.tsx` | **new** — per-class current vs proposed, guard blocks with the measured reason |
| `src/components/UprImpactPreview.test.tsx` | **new** — 4 render tests |
| `src/api/uprPolicy.ts` | **new** |
| `src/App.tsx`, `AppSidebar.tsx` | route + nav behind `upr_policy.view` |

**Verification**

* 254 Django tests (2 pre-existing Redis-broker failures, unrelated); 59 module-1 engine
  tests including 33 new method tests and 11 guard tests; 122 frontend tests; `vite build`
  clean; `tsc` unchanged at its 45-error baseline.
* **The §1.3 bit-identity measurement is frozen as a test** and re-asserted end to end: all
  8 goldens pass, including `summary_ref` and `policy_upr_ref`, the two that exercise the
  refactored UPR path.
* The new `m1_upr_methods_ref` golden was drift-tested — changing the eighths coefficient
  from 2 to 3 fails it, restoring passes.

**One latent defect found and fixed while building**

A row with `RiskEndDate` before `RiskStartDate` drove `sum_of_digits` to roughly
**−132,000**, which would have flowed straight into UPR as a vast negative. The formula was
inherited unchanged from the historic code; it never fired because no reference row is
malformed, so no golden could have caught it. `unearned_fraction` now clamps to [0, 1] and
coerces NaN/inf to zero — a no-op on well-formed data, asserted by the bit-identity test,
and a regression test covers every method.

---

## 0. Client requirement

> "UPR methods are hard coded in code, want to have them on interface, if someone want to select
> different UPR methodology for different Line of business"

## 1. Verified ground truth

Everything below was **measured** against the client reference book (14,791 premium rows);
the measurements are reproduced so a reviewer can re-run them.

### 1.1 Three duplicated blocks, and TWO independent drifts

`calculate_upr` selects a basis with `np.select` over three conditions. The block is copied
three times — the EOP block, the quarterly loop, and the run-off loop in
`summarize_upr_by_reserving_class` — and the copies have **already drifted in two separate
ways**:

| Block | Marine class string | Marine issue window |
|---|---|---|
| EOP | `"Marine cargo"` | `ISSUEDATE > eop − DateOffset(months=3)` |
| Quarterly loop | `"Marine Cargo"` **(capital C)** | `ISSUEDATE > date − DateOffset(months=3)` |
| Run-off loop | `"Marine cargo"` | `ISSUEDATE > date − Timedelta(days=91)` **(fixed 91 days)** |

The second drift is new to this plan and is not cosmetic — a calendar quarter is 90–92 days,
so the two windows disagree in **three quarters out of four**:

```
2024-03-31   months=3 -> 2023-12-31    days=91 -> 2023-12-31   same
2024-06-30   months=3 -> 2024-03-30    days=91 -> 2024-03-31   DIFFERENT
2024-09-30   months=3 -> 2024-06-30    days=91 -> 2024-07-01   DIFFERENT
2024-12-31   months=3 -> 2024-09-30    days=91 -> 2024-10-01   DIFFERENT
```

Both drifts are dormant only because no row matches either spelling (§1.2). They would
produce inconsistent UPR the moment marine business is correctly classified.

### 1.2 All three branches are unreachable

On the reference book **100% of rows fall through to pro-rata**; the CAR/EAR and marine
branches match zero rows.

| Code matches | Data contains |
|---|---|
| `PRODUCTTYPE in ["Contractors All Risks", "Erection All Risks"]` | `"CONTRACTORS'ALL RISK"`, `"ERECTION ALL RISKS"` |
| `POLICYCLASS == "Marine cargo"` | `POLICYCLASS = "Marine, aviation and transport insurance"`; marine lives in `PRODUCTTYPE` as `"MARINE CARGO"` |

Case, apostrophe spacing, plurality — and for marine, the **wrong column entirely**.

`RESERVINGCLASS == POLICYCLASS` on all 14,791 rows, so the LOB key question is moot: they are
the same field.

### 1.3 A pro-rata-everywhere default is bit-identical — proven

The plan's central safety claim, now measured rather than asserted. A prototype resolver was
compared against **all three** current blocks, across every valuation date the engine uses:

```
rows 14,791   x   12 quarter-end dates   x   3 block variants
max |current − proposed|  =  0.000e+00
rows differing anywhere   =  0
```

**But only under one condition**, which is the design correction this measurement produced:

> **Eligibility (`ISSUEDATE <= valuation date`) must stay a separate gate from the earning
> method.** In the current code that gate is implicit in `np.select(..., default=0)` — every
> condition carries `ISSUEDATE <= date`, and a row matching none of them gets 0. A method
> registry that folds eligibility into each method cannot reproduce this and would silently
> grant UPR to policies not yet issued.

The original draft of this plan folded them together. That would have been wrong.

### 1.4 Two methods self-gate on expiry; the term-based ones do not

Measured at EOP on expired policies (`RiskEndDate < valuation date`):

| Method | Max unearned fraction on an expired policy |
|---|---|
| `pro_rata_daily` | `0.000e+00` — self-gates |
| `sum_of_digits` | `0.000e+00` — self-gates |
| `eighths` | **0.875** — does **not** self-gate |

`pro_rata` and `sum_of_digits` are functions of the risk period, so an expired policy falls
out naturally. `eighths` / `twenty_fourths` are functions of the **issue date alone**.

### 1.5 8ths / 24ths are unsafe on this book — measured, not theorised

Applied to the whole reference book at EOP:

| Method | UPR | vs today |
|---|---:|---:|
| `pro_rata_daily` (today) | 224,367,539 | — |
| `sum_of_digits` | 327,426,095 | +45.93% |
| `eighths` | **−321,788,936** | **−243.4%** |
| `twenty_fourths` | **−738,254,910** | **−429.0%** |

Adding the in-force gate barely helps (−243.4% → −243.5%), so expiry is **not** the cause.
The cause is decomposable:

```
                    pro_rata            eighths           delta
positive premium   1,783,030,733     2,243,267,934     +460,237,201
NEGATIVE premium  -1,558,663,195    -2,565,056,870   -1,006,393,675
```

The book carries **699 rows (4.7%) of negative premium totalling −3,155,953,328** —
cancellations and mid-term endorsements. Pro-rata gives them a mean weight of **0.109**
because their risk period has largely run off. 8ths weights them by issue quarter alone, mean
**0.426** — nearly 4x. That single effect is the −243%.

Term distribution confirms the book is *nominally* suited to 8ths (92.8% annual, median 365
days), which makes this the more dangerous failure: the method looks applicable and is not.

**Design consequence:** 8ths / 24ths ship with a **book-suitability guard**, not merely a
dropdown entry. See §2.5.

### 1.6 What the intended policy would actually do

The number the client needs in order to decide. Engineering → `sum_of_digits`, Marine →
`full_premium_in_period`, everything else unchanged:

| | today | suggested | delta |
|---|---:|---:|---:|
| Book total | 224,367,539 | 226,298,219 | **+0.86%** |
| Engineering Insurance | 3,906,882 | 5,909,344 | **+51.25%** |
| Marine, aviation and transport | 1,157,179 | 1,085,398 | **−6.20%** |

Small at book level, **material within the two classes it touches** — which is exactly the
profile that makes an impact preview worth building rather than optional.

## 2. Design

### 2.1 One function, four callers — with eligibility separated

Every site asks the same question: *what fraction of this policy's premium is unearned at
date D?* Extract exactly that, and — per §1.3 — keep eligibility outside the method:

```python
# module1_engine/upr_methods.py  (new)
def unearned_fraction(df, at_date, policy) -> pd.Series:
    """Unearned fraction in [0, 1] per row at `at_date`. Vectorised.

    Eligibility and earning are SEPARATE concerns:
      * eligibility — the policy must have been issued by `at_date`. This reproduces the
        implicit `np.select(..., default=0)` gate in the current code (plan section 1.3).
      * earning — the method resolved for that row decides the fraction.

    Folding the two together cannot reproduce current output and would grant UPR to
    policies not yet issued.
    """
    eligible = df["ISSUEDATE"] <= at_date
    frac = _method_fraction(df, at_date, policy)      # per-row, method-resolved
    return frac.where(eligible, 0.0)
```

`calculate_upr` (both blocks) and the run-off loop each become one call. `calculate_upr` is
invoked from **four** places (`summarize_upr_by_reserving_class` x2, `calculate_policy_level_upr`,
`run_generate_summary` x2), so `run_policy_level_upr` must accept and thread the policy too —
otherwise policy-level UPR would silently keep the old behaviour while the summary changed.

The drift class of bug (§1.1) is eliminated by construction rather than by fixing two
spellings and a timedelta.

### 2.2 The method registry

| Key | Basis | Unearned fraction at `D` | Self-gates on expiry |
|---|---|---|---|
| `pro_rata_daily` | 365ths / time apportionment | `max(0, min(Duration, (RiskEnd − D).days)) / Duration` | yes (§1.4) |
| `sum_of_digits` | increasing risk (CAR / EAR) | `1 − min(max((D − RiskStart).days + 1, 0), Duration)² / Duration²` | yes (§1.4) |
| `full_premium_in_period` | marine cargo style | `1` if `IssueDate ∈ (D − lookback, D]` else `0` | n/a |
| `eighths` | 1/8ths, quarterly issuance | `(7 − 2k)/8`, `k` whole quarters since issue, clipped to [0,1] | **no — guarded, §2.5** |
| `twenty_fourths` | 1/24ths, monthly issuance | `(23 − 2m)/24`, `m` whole months since issue, clipped to [0,1] | **no — guarded, §2.5** |
| `flat_percentage` | treaty / regulatory override | constant `p` | n/a |

`params` carries `lookback_months` (default 3, matching the current marine window — and the
§1.1 drift is resolved in favour of `DateOffset(months=3)`, the reading used by two of the
three blocks), `percent` for `flat_percentage`, and `term_months` for the 8ths/24ths.

Registered in one dict so the API, the UI dropdown, the template generator and the engine all
enumerate from the same place.

### 2.3 Resolution — normalised, never literal

```
(RESERVINGCLASS, PRODUCTTYPE)  →  (RESERVINGCLASS, *)  →  system default
```

All comparison is on `canonical_key()` from WP0 (casefold, collapse whitespace, strip punctuation).
`"CONTRACTORS'ALL RISK"`, `"Contractors All Risks"` and `"contractors all risk"` all resolve to the
same rule. **Exact-literal matching is the direct cause of F2 and is not repeated anywhere in WP2.**

Because product-type vocabularies are messy, a rule's `product_type` may also be a **prefix or
contains** match, declared explicitly on the rule (`match_mode: exact | contains | prefix`) rather
than inferred. `contains("marine cargo")` catches both `MARINE CARGO` and `MARINE CARGO EXPORT`.

### 2.4 Persistence — org policy, snapshotted per job

UPR methodology is a standing methodology choice, not a per-run whim: it must be stable across
periods, auditable, and reproducible. Modelled the same way datasets already are — a versioned
org-level policy, snapshotted into each job.

```python
# tenants/models.py
class UprMethodPolicy(models.Model):
    organization, version (int), is_active (bool)
    created_by, created_at, note (text)
    # unique (organization, version); exactly one active per org

class UprMethodRule(models.Model):
    policy        = FK(UprMethodPolicy, related_name="rules")
    reserving_class = CharField(128)              # "" = applies to all classes
    product_type    = CharField(128, blank=True)  # "" = class-level default
    match_mode      = CharField(choices=exact|contains|prefix, default="exact")
    method          = CharField(choices=<registry keys>)
    params          = JSONField(default=dict)
    priority        = IntegerField(default=0)     # tiebreak within a specificity tier
```

Editing an active policy **forks a new version**; historic jobs keep pointing at the version they
ran under. `Module1Job.input_meta["upr_policy"]` stores the fully **resolved** rule list, so a job is
reproducible even if the policy is later deleted.

**The engine never imports Django.** The task resolves the policy to a plain list of dicts and passes
it as `run_generate_summary(..., upr_policy=[...])`. `upr_policy=None` means the seeded default.

### 2.5 The seeded default is provably today's behaviour

```python
DEFAULT_POLICY = [{"reserving_class": "", "product_type": "",
                   "method": "pro_rata_daily", "params": {}}]
```

Not an argument from §1.2 that "no row hits the other branches" — an actual measurement.
Per §1.3, a pro-rata-everywhere resolver reproduces **all three** current blocks across
14,791 rows and 12 valuation dates with `max |diff| = 0.000e+00`, provided eligibility stays
a separate gate. That is the gating test for the whole work package.

Note a faithful port of the dead branches would *also* be bit-identical, since they match
nothing. We ship the correct default instead and let the client enable the special bases
deliberately, with §1.6's numbers in front of them.

### 2.6 Term-based methods ship behind a book-suitability guard

§1.5 measured 8ths at **−243%** on this book, and the in-force gate does not fix it: the
method weights by issue date alone, so 699 rows of negative endorsement (−3.16bn) get ~4x
the weight pro-rata gives them. The book is *nominally* suited (92.8% annual terms), which
makes it worse — the method looks applicable and is not.

So selecting `eighths` or `twenty_fourths` runs a guard over the rows it would apply to and
reports, before the policy can be activated:

| Check | Threshold | Action |
|---|---|---|
| share of rows with negative premium | > 1% | **block**, naming the row count and total |
| share of rows outside `term_months ± 10%` | > 10% | **block** |
| share of rows already expired at the valuation date | > 5% | warn |

Blocks are overridable only by a user with `upr_policy.manage` ticking an explicit
acknowledgement, which is recorded in the policy version's `note`. The point is not to
forbid the method — some sub-books genuinely suit it — but to make it impossible to select
by accident on a book like this one.

### 2.7 Suggested starting policy

Offered in the UI as "apply suggested policy", never applied automatically:

| Reserving class | Product type match | Method |
|---|---|---|
| Engineering Insurance | `contains "all risk"`, `contains "erection"` | `sum_of_digits` |
| Marine, aviation and transport insurance | `contains "marine cargo"` | `full_premium_in_period` |
| *(all others)* | — | `pro_rata_daily` |

This is what the original code was trying to express. Its measured effect (§1.6) is **+0.86%
at book level, +51.25% on Engineering, −6.20% on Marine** — small in aggregate, material
where it lands.

### 2.8 Impact preview before adoption

Changing a UPR method moves UPR, GEP, Allocation EP, the run-off and everything downstream. The
policy editor therefore offers **"preview impact"**: resolve the candidate policy against the
selected premium dataset and show, per class, current vs proposed UPR at EOP with the delta — without
running a job. Reuses `unearned_fraction` directly, so preview equals output by construction.

No actuary should adopt a methodology change without seeing its number.

## 3. Backend changes

| File | Change |
|---|---|
| `module1_engine/upr_methods.py` | **new** — registry, the six methods, `resolve_rule`, `unearned_fraction` (eligibility separated per §2.1) |
| `module1_engine/upr_guard.py` | **new** — book-suitability checks for the term-based methods (§2.6) |
| `module1_engine/engine.py` | `calculate_upr` both blocks and the run-off loop call `unearned_fraction`; **all four call sites** threaded; `run_generate_summary` / `run_policy_level_upr` accept `upr_policy` |
| `module1_engine/__init__.py` | export registry + `unearned_fraction` |
| `tenants/models.py` | `UprMethodPolicy`, `UprMethodRule` + migration |
| `tenants/serializers.py`, `views.py`, `urls.py` | policy CRUD, `POST .../activate`, `POST .../preview-impact` |
| `processing/views.py` | summary + policy-UPR jobs resolve the active policy into `input_meta["upr_policy"]` |
| `processing/tasks.py` | pass the resolved policy to the engine |
| `accounts/management/commands/seed_rbac.py` | `upr_policy.view`, `upr_policy.manage` (Actuary and above) |

## 4. Frontend changes

| File | Change |
|---|---|
| `src/pages/UprMethodPolicyPage.tsx` | **new** — rule table (class, product-type match, method, params), version history, activate, "apply suggested policy" |
| `src/components/UprImpactPreview.tsx` | **new** — current vs proposed UPR per class with deltas |
| `src/api/uprPolicy.ts` | **new** |
| `src/App.tsx` | route `/upr-methods` behind `upr_policy.view` |
| `src/components/AppSidebar.tsx` | nav entry under a "Methodology" group |
| `src/pages/SummaryGeneratorPage.tsx` | show which policy version this run will use, with a link |

Surfacing the active policy version **on the run screen** matters: an actuary must never discover
after the fact that the methodology changed under them.

## 5. Bit-identity and goldens

* Default policy (or `upr_policy=None`) → **bit-identical**, already proven at the resolver
  level (§1.3) and re-asserted end to end against the existing `summary_ref` and
  `policy_upr_ref` goldens. This is the gate for the whole package.
* An explicit all-`pro_rata_daily` policy must equal `None` — the two paths through the
  resolver must not diverge.
* **`run_policy_level_upr` is asserted too.** It is a separate entry point into
  `calculate_upr` (§2.1); a policy threaded into the summary but not into policy-level UPR
  would leave the two disagreeing, which no existing golden would catch.
* Each non-default method gets a small captured golden from a **synthetic** fixture. The
  client book cannot exercise them — that is §1.2 — so a golden built from it would prove
  nothing.

## 6. Tests

**`module1_engine/tests/test_upr_methods.py`** (new)
* each method against hand-computed values at policy start, mid-term, expiry, post-expiry
* **§1.3 parity**: the resolver reproduces all three historic blocks across the reference
  frame and every quarter-end date, to `0.0` — the measurement, frozen as a test
* **eligibility is separate**: a policy issued *after* the valuation date yields 0 under
  every method, including the ones that would otherwise return a positive fraction
* **§1.4 self-gating**: `pro_rata_daily` and `sum_of_digits` return 0 on an expired policy;
  `eighths` does not, which is why §2.6 exists
* `eighths`: 7/8, 5/8, 3/8, 1/8, 0 across five successive quarter-ends
* `twenty_fourths`: 23/24 at the issue month, 1/24 at month 11, 0 at month 12
* zero-duration and one-day policies do not divide by zero
* `RiskEndDate < RiskStartDate` (bad data) → 0, never negative
* resolution order: product rule beats class rule beats default
* `contains` matching resolves `"CONTRACTORS'ALL RISK"` **and** `"Contractors All Risks"`
* the marine window resolves to `DateOffset(months=3)`, and the §1.1 91-day variant is gone

**`module1_engine/tests/test_upr_guard.py`** (new)
* the reference book **fails** the negative-premium check for `eighths` (699 rows, −3.16bn)
* a synthetic clean annual book **passes**
* a mixed-term book fails the term check
* an acknowledged override is recorded on the policy version

**`module1_engine/tests/test_golden_engines.py`**
* `run_generate_summary(upr_policy=None)` bit-identical to the existing golden
* `run_policy_level_upr(upr_policy=None)` likewise
* an explicit all-pro-rata policy identical to `None`

**`tenants/tests/test_upr_policy_api.py`** (new)
* editing an active policy forks a version; the old version stays readable
* exactly one active policy per org enforced
* the resolved policy is snapshotted into `input_meta` at job creation
* deleting a policy does not break replay of a job that used it
* `upr_policy.manage` required to activate; `module1.run` alone is insufficient
* the impact preview returns per-class deltas without creating a job

## 7. Edge cases

* **`PRODUCTTYPE` null or blank** → class-level rule; never crash. (Zero nulls in the reference book,
  but production files will differ.)
* **Two rules of equal specificity** → `priority` breaks the tie; equal priority is rejected on save.
* **Policy references a class no longer in the data** → inert, surfaced as a warning in the editor.
* **`flat_percentage` outside [0, 1]** → rejected on save.
* **Marine `full_premium_in_period` at a quarter boundary** — the historic predicate is
  `IssueDate > D − 1 quarter` (strict). Preserved exactly, with `lookback_months` defaulting to 3.
* **Policy changed mid-period** — jobs are pinned to the version they ran under; the run screen shows
  the version so a comparison across periods is never accidentally apples-to-oranges.

## 8. Estimate

| | |
|---|---|
| method registry + eligibility separation | 1.5d |
| refactor the three blocks + four call sites, with the §1.3 parity proof | 2d |
| book-suitability guard (§2.6) | 1d |
| policy model, API, versioning | 1.5d |
| impact preview | 1d |
| frontend (policy editor, guard surfacing, preview) | 3d |
| tests | 2d |
| goldens (synthetic per method) + validation | 1d |
| **Total** | **~13 days** |

Three days above the pre-verification estimate: the eligibility separation (§1.3), the
book-suitability guard (§1.5) and the `run_policy_level_upr` path (§2.1) were all discovered
by measuring, and all three are load-bearing.

## 9. What this does not do

It does not change any number by default. Adopting a non-default method is a deliberate act
with §1.6's impact in front of the user, recorded against a policy version, and — for the
term-based methods — gated on a suitability check the reference book currently fails.
