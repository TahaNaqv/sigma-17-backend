# WP2 — UPR Method Registry & Per-Line-of-Business Policy

> **Goal:** Replace three copy-pasted, string-matched, unreachable UPR branches with a named method
> registry and an auditable per-line-of-business policy the actuary controls from the interface.

Status: planned (2026-08-21). Decisions: `docs/CLIENT_REQUIREMENTS_DECISIONS.md` §2 F2, §3 D3.
Requirement 4. Depends on WP0.

---

## 0. Client requirement

> "UPR methods are hard coded in code, want to have them on interface, if someone want to select
> different UPR methodology for different Line of business"

## 1. How it works today — and why it does not

`calculate_upr` (`module1_engine/engine.py`) selects a basis with `np.select` over three conditions:

```python
conditions = [
    (df['POLICYCLASS'] != "Marine cargo") & (df['ISSUEDATE'] <= year_end)
        & ( df['PRODUCTTYPE'].isin(["Contractors All Risks", "Erection All Risks"])),
    (df['POLICYCLASS'] != "Marine cargo") & (df['ISSUEDATE'] <= year_end)
        & (~df['PRODUCTTYPE'].isin(["Contractors All Risks", "Erection All Risks"])),
    (df['POLICYCLASS'] == "Marine cargo") & (df['ISSUEDATE'] <= year_end)
        & (df['ISSUEDATE'] > previous_quarter),
]
```

### 1.1 The block is duplicated three times

| Site | Purpose |
|---|---|
| `calculate_upr`, EOP block | the `UPR` / `DAC` columns |
| `calculate_upr`, quarterly loop | the `UPR_{date}` columns per quarter end |
| `summarize_upr_by_reserving_class`, run-off loop | the 18-month UPR run-off |

Three near-identical copies, already drifted: the first spells `"Marine cargo"`, the other two spell
`"Marine Cargo"`. Any future change must be made three times or the frames disagree.

### 1.2 None of the branches fire (F2)

Verified against `benchmarks/fixtures/summary_ref/premium/Premium - Full.xlsx` (14,791 rows):

```
branch hit                     rows
pro_rata_daily (fallthrough)   14,791
sum_of_digits (CAR/EAR)             0
full_premium (marine)               0
```

| Code matches | Data contains |
|---|---|
| `PRODUCTTYPE in ["Contractors All Risks", "Erection All Risks"]` | `"CONTRACTORS'ALL RISK"`, `"ERECTION ALL RISKS"` |
| `POLICYCLASS == "Marine cargo"` | `POLICYCLASS = "Marine, aviation and transport insurance"`; marine appears in `PRODUCTTYPE` as `"MARINE CARGO"` |

Case, apostrophe spacing, plurality, and — for marine — the **wrong column** entirely. The
`"Marine cargo"` / `"Marine Cargo"` drift is therefore moot in effect, but is removed regardless.

**The real defect:** Engineering and Marine business has been reserved on straight pro-rata since
inception. The client's request is not a preference; it is the workaround for a silent failure.

## 2. Design

### 2.1 One function, three callers

Every site is asking the same question — *what fraction of this policy's premium is unearned at
date D?* Extract exactly that:

```python
# module1_engine/upr_methods.py  (new)
def unearned_fraction(df: pd.DataFrame, at_date: pd.Timestamp,
                      policy: ResolvedUprPolicy) -> pd.Series:
    """Unearned fraction in [0, 1] per row at `at_date`. Vectorised.
    The single source of truth for all UPR bases; the three historic
    condition blocks all become calls to this."""
```

`calculate_upr` (both blocks) and the run-off loop each become one call. The drift class of bug is
eliminated by construction rather than by fixing two spellings.

### 2.2 The method registry

Six methods, each a pure vectorised function of `(df, at_date, params)`:

| Key | Basis | Unearned fraction at date `D` |
|---|---|---|
| `pro_rata_daily` | 365ths / time apportionment | `max(0, min(Duration, (RiskEnd − D).days)) / Duration` |
| `sum_of_digits` | increasing risk (CAR / EAR) | `1 − min(max((D − RiskStart).days + 1, 0), Duration)² / Duration²` |
| `full_premium_in_period` | marine cargo style | `1` if `IssueDate ∈ (D − lookback, D]` else `0` |
| `eighths` | 1/8ths, quarterly issuance | `(7 − 2k)/8` for `k` whole quarters elapsed, floored at 0 |
| `twenty_fourths` | 1/24ths, monthly issuance | `(23 − 2m)/24` for `m` whole months elapsed, floored at 0 |
| `flat_percentage` | treaty / regulatory override | constant `p` from rule params |

`params` (JSON per rule) carries `lookback_months` for `full_premium_in_period`, `percent` for
`flat_percentage`, and `term_months` for the 8ths/24ths (default 12).

Registered in one dict so the API, the UI dropdown, the template generator and the engine all
enumerate from the same place and cannot drift.

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

Not a guess: 100% of the reference book already resolves to `pro_rata_daily` (F2), so a default of
pro-rata-everywhere is **bit-identical by construction**. This is the assertion the golden test makes.

Note that a *faithful* port of today's dead branches would also be bit-identical, but it would
re-enshrine logic that never fires. We ship the correct default and let the client enable the
special bases deliberately.

### 2.6 Suggested starting policy (client-facing, not shipped active)

Offered in the UI as "apply suggested policy", never applied automatically:

| Reserving class | Product type match | Method |
|---|---|---|
| Engineering Insurance | `contains "all risk"`, `contains "erection"` | `sum_of_digits` |
| Marine, aviation and transport insurance | `contains "marine cargo"` | `full_premium_in_period` |
| *(all others)* | — | `pro_rata_daily` |

This is what the original code was *trying* to express. Applying it changes numbers; that is the
point, and it is the client's decision to make, with a preview (§2.7) in front of them.

### 2.7 Impact preview before adoption

Changing a UPR method moves UPR, GEP, Allocation EP, the run-off and everything downstream. The
policy editor therefore offers **"preview impact"**: resolve the candidate policy against the
selected premium dataset and show, per class, current vs proposed UPR at EOP with the delta — without
running a job. Reuses `unearned_fraction` directly, so preview equals output by construction.

No actuary should adopt a methodology change without seeing its number.

## 3. Backend changes

| File | Change |
|---|---|
| `module1_engine/upr_methods.py` | **new** — registry, the six methods, `resolve_rule`, `unearned_fraction` |
| `module1_engine/engine.py` | `calculate_upr` both blocks and the run-off loop call `unearned_fraction`; `run_generate_summary` / `run_policy_level_upr` accept `upr_policy` |
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

* Default policy (or `upr_policy=None`) → **bit-identical**. Existing goldens pass untouched. This is
  the gating test for the whole work package.
* Each non-default method gets its own small captured golden from a synthetic fixture, not from the
  client reference book (which cannot exercise them — that is F2).
* The suggested policy is **not** applied in goldens; adopting it is a client decision.

## 6. Tests

**`module1_engine/tests/test_upr_methods.py`** (new)
* each method against hand-computed values at policy start, mid-term, expiry, post-expiry
* `pro_rata_daily` equals the current implementation on the reference frame, row for row
* `eighths`: 7/8, 5/8, 3/8, 1/8, 0 across five successive quarter-ends
* `twenty_fourths`: 23/24 at issue month, 1/24 at month 11, 0 at month 12
* zero-duration and one-day policies do not divide by zero
* `RiskEndDate < RiskStartDate` (bad data) → 0, not negative
* resolution order: product rule beats class rule beats default
* `contains` matching resolves `"CONTRACTORS'ALL RISK"` **and** `"Contractors All Risks"`
* unresolvable class falls to default rather than raising

**`module1_engine/tests/test_golden_engines.py`**
* `run_generate_summary(upr_policy=None)` bit-identical to the existing golden
* explicit all-`pro_rata_daily` policy identical to `None`

**`tenants/tests/test_upr_policy_api.py`** (new)
* editing an active policy forks a version; the old version stays readable
* exactly one active policy per org enforced
* resolved policy is snapshotted into `input_meta` at job creation
* deleting a policy does not break replay of a job that used it
* `upr_policy.manage` required to activate; `module1.run` alone is insufficient

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

Backend 4d (registry 1d, refactor + parity proof 1.5d, policy model/API 1.5d), frontend 3d, tests
2d, impact-preview 1d. **~10 days.**
