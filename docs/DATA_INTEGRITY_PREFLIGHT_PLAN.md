# WP0 — Class Reconciliation & Input Pre-flight Gate

> **Goal:** Make it impossible for a reserving run to silently produce an empty triangle because an
> input file spells a reserving class differently from another. Detect, report and **block** input
> defects before the engine runs, instead of emitting a plausible-looking workbook full of zeros.

Status: **implemented 2026-09-02** (see §9). Decisions: `docs/CLIENT_REQUIREMENTS_DECISIONS.md` §2 F1/F4, §5.
Priority: **P0 — blocks WP1-WP7.**

---

## 0. Why this precedes the client's list

The client asked for better factor-selection tooling (req 6, 7). Shipping selection tools on top of
a broken join lets an actuary make a confident, well-documented judgement against a triangle of
zeros. The tool would work perfectly and the answer would be wrong.

## 1. The defect

`run_generate_summary` (`module1_engine/engine.py`) drives the reserve loop from the **premium**
frame:

```python
for reserving_class in df['RESERVINGCLASS'].unique():        # df == premium
    ...
    filtered_data = combined_data[
        (combined_data['RESERVINGCLASS'] == reserving_class) & ...
    ]
```

Claims are then matched by **exact string equality**. Three failure modes follow, none of which
raise:

| Mode | Effect | Reference example |
|---|---|---|
| Claims class absent from premium | Those claims are **silently dropped entirely** | `Health` (paid file) |
| Premium class absent from claims-paid | Workbook produced with a **zero paid triangle** | `Health Insurance` |
| Premium class absent from all claims | Workbook with no claims data — may be legitimate | `D&O` |

Confirmed in `benchmarks/goldens/summary_ref/`:
`Health Insurance Payment GROSS 2017-12.xlsx` → Paid Claims Triangle, sum of all cells `0.0`.
Health is 41% of the premium book by row count.

The same exact-equality fragility exists for `HEADOFDAMAGE` and `RI_TREATY_TYPE`, which are also
used as loop keys and filter predicates.

## 2. Design

Three layers, in order. Layer 1 prevents, layer 2 detects, layer 3 blocks.

### Layer 1 — canonicalisation at ingest (prevention)

A single normalisation function, used by **both** the dataset importer and the engine adapter, so
neither can drift:

```python
# datasets/services/normalize.py  (new)
def canonical_key(value: str) -> str:
    """Match key for class / head-of-damage / treaty comparisons.
    casefold, collapse internal whitespace, strip surrounding punctuation.
    NOT a display transform — the original string is preserved for output."""
```

Canonicalisation is used for **matching only**. Display and workbook filenames keep the original
spelling, so output is unchanged for already-consistent data. This alone does not fix `Health` vs
`Health Insurance` (they differ by more than case/whitespace) — that needs layer 2.

### Layer 2 — the reconciliation report

A pure function over the three input frames, returning a structured report. No side effects, fully
unit-testable, callable from both the API (pre-submit preview) and the task (enforcement).

```python
# processing/services/preflight.py  (new)
@dataclass(frozen=True)
class ClassReconciliation:
    premium_only:      list[str]   # premium class with no claims at all      (WARN)
    paid_only:         list[str]   # paid class not in premium -> DROPPED     (ERROR)
    os_only:           list[str]   # os class not in premium -> DROPPED       (ERROR)
    missing_paid:      list[str]   # premium class with no paid claims        (ERROR)
    missing_os:        list[str]   # premium class with no OS claims          (WARN)
    near_matches:      list[tuple[str, str, float]]   # suggested pairings
    row_counts:        dict[str, dict[str, int]]      # class -> {premium, paid, os}
    dropped_row_count: int
    dropped_amount:    float       # monetary value of silently dropped claims

@dataclass(frozen=True)
class PreflightReport:
    classes:  ClassReconciliation
    severity: str                  # "ok" | "warn" | "error"
    messages: list[PreflightMessage]
```

`near_matches` uses `difflib.SequenceMatcher` over canonical keys, surfacing
`("Health", "Health Insurance", 0.82)` so the UI can offer a one-click alias.

**`dropped_amount` is the headline number.** "3,140 paid claim rows worth 41,802,113 will be
discarded" is what makes the defect legible to an actuary; a list of class names is not.

### Layer 3 — the gate

Pre-flight runs inside the Celery task **before** the engine, and its severity decides:

| Severity | `strict` (default) | `permissive` |
|---|---|---|
| `error` | job fails with the report in `error_message`; nothing written | proceeds, report persisted |
| `warn`  | proceeds, report persisted | proceeds, report persisted |
| `ok`    | proceeds | proceeds |

Mode is per-organization (`Organization.preflight_mode`), defaulting to **strict**. Permissive
exists solely so an in-flight client is never hard-blocked by a policy change on deploy; it is not
a recommended steady state and the UI labels it as such.

The report is persisted to `Module1Job.input_meta["preflight"]` on every run — success or failure —
so it is part of the audit record, not just an error path.

### Class aliasing

An alias map resolves genuine naming differences:

```python
# tenants/models.py
class ReservingClassAlias(models.Model):
    organization = FK(Organization, related_name="reserving_class_aliases")
    alias        = CharField(128)   # "Health"            (as found in an input file)
    canonical    = CharField(128)   # "Health Insurance"  (the premium-file spelling)
    created_by, created_at
    class Meta:
        constraints = [UniqueConstraint(fields=["organization", "alias"],
                                        name="uniq_alias_per_org")]
```

Applied in the engine adapter and the Excel import path, before the frames reach the engine. Aliases
are org-scoped, auditable, and surfaced in the pre-flight UI as "apply this fix permanently".

**The engine signature does not change.** Aliasing rewrites `RESERVINGCLASS` values in the staged
frames; `module1_engine` continues to see one consistent vocabulary. This keeps WP0 out of the
bit-identical blast radius for any client whose data is already consistent.

## 3. Backend changes

| File | Change |
|---|---|
| `datasets/services/normalize.py` | **new** — `canonical_key`, `near_match_score` |
| `processing/services/preflight.py` | **new** — `reconcile_classes`, `build_preflight_report`, dataclasses |
| `processing/views.py` | **new** `Module1PreflightView` (POST, accepts the same dataset ids / uploads as the summary job, returns the report without creating a job) |
| `processing/urls.py` | `module1/preflight/` |
| `processing/tasks.py` | `run_module1_summary_task` — run pre-flight after frames are staged, before `run_generate_summary`; persist to `input_meta["preflight"]`; raise `PreflightError` when strict + error |
| `datasets/services/engine_adapter.py` | apply `ReservingClassAlias` during materialisation |
| `datasets/services/excel_import.py` | apply aliases on import; record unaliased outliers |
| `tenants/models.py` | `ReservingClassAlias`; `Organization.preflight_mode` |
| `tenants/views.py`, `urls.py` | alias CRUD (`orgs.manage`) |
| `core/exceptions.py` | `PreflightError` → structured 422 |

## 4. Frontend changes

| File | Change |
|---|---|
| `src/api/module1.ts` | `runPreflight()`, `PreflightReportDto` |
| `src/components/PreflightReport.tsx` | **new** — severity banner, per-class reconciliation table (premium / paid / OS row counts side by side), dropped-rows-and-amount callout, near-match suggestions with "create alias" action |
| `src/pages/SummaryGeneratorPage.tsx` | run pre-flight on entering step 3; **block Generate on `error` in strict mode**; show report inline |
| `src/pages/DataHubPage.tsx` | surface unresolved aliases as a dataset-level warning badge |
| `src/state/wizards/summary.ts` | persist last report so it survives tab switches |

The report renders **before** the run, not after. The whole point is that the user never spends
twelve minutes on a job that was doomed at submit.

## 5. Bit-identity and goldens

* For input data with **no** reconciliation errors, output is unchanged. Existing goldens pass
  untouched. This is the assertion to test first.
* For the reference fixtures, WP0 **deliberately changes output**: aliasing `Health` →
  `Health Insurance` populates a previously-empty paid triangle, which changes Health LDFs,
  ultimates and IBNR.
* Therefore: keep the current `summary_ref` golden as `summary_ref_prealias` (frozen, documents
  pre-fix behaviour), and capture `summary_ref` fresh **with** the alias applied. Both are asserted:
  the old one proves we can still reproduce historic output when no aliases exist; the new one
  becomes the forward baseline.
* Dated in the plan and in `benchmarks/README.md`: **2026-08-21, WP0, Health alias.**

## 6. Tests

**`processing/tests/test_preflight.py`** (new)
* class present in all three → `ok`
* claims-only class → `error`, appears in `paid_only`, `dropped_row_count` > 0
* premium class with no paid → `error`, `missing_paid`
* premium class with no claims at all → `warn` (the D&O case), never `error`
* near-match scoring surfaces `("Health", "Health Insurance")` above threshold
* `dropped_amount` sums the engine's `Amount` column, not raw `AMOUNTPAID`
* canonical key: case, whitespace, trailing punctuation collapse; distinct words do not

**`processing/tests/test_preflight_api.py`** (new)
* preflight endpoint returns report without creating a `Module1Job`
* strict + error → summary task fails, `error_message` carries the summary, no output ZIP
* permissive + error → job succeeds, report still persisted
* report persisted on success runs too

**`datasets/tests/test_aliases.py`** (new)
* alias applied during engine-adapter materialisation
* alias applied during Excel import
* alias uniqueness per org enforced
* aliases do not alter output filenames or display strings

**`module1_engine/tests/test_golden_engines.py`**
* add `summary_ref_prealias` assertion alongside the regenerated `summary_ref`

## 7. Edge cases

* **Alias cycles / chains** (`A→B`, `B→C`) — resolve one hop only; reject on save if the canonical
  is itself an alias.
* **Alias to a class that does not exist in premium** — reject on save with the valid list.
* **Case-only differences** — handled by layer 1; never require an alias.
* **Empty claims file** — `error` with a distinct message; today this yields a full set of zero
  triangles with no signal.
* **A class legitimately having no claims** (D&O) — must stay `warn`. Making it an error would train
  users to run permissive, defeating the gate.
* **Very large near-match sets** — cap suggestions at 10 per class, ranked by score.

## 8. Estimate

Backend 2.5d, frontend 1.5d, tests 1d, golden re-capture and validation 0.5d. **~5.5 days.**

---

## 9. Implementation status — built (2026-09-02)

Implemented and tested. F1 is fixed and both states of it are pinned by goldens.

### 9.1 The measured result

Same inputs, one alias:

| | Health paid reaching the reserve |
|---|---|
| today's behaviour | **0** |
| with `Health → Health Insurance` | **35,402,487** |

(35,503,674 is the total in the file; 35,402,487 is the part inside the 2016–2017 experience
period, which is what the engine consumes.) `test_class_alias_effect.py` asserts both, and
asserts that **no other class moves** — an alias that changed anything beyond the two spellings
of one name would be doing more than joining them.

### 9.2 Two corrections to the plan, both found by measuring

**The near-match scorer in §2 does not work.** The plan states difflib surfaces
`("Health", "Health Insurance", 0.82)`. Measured, `SequenceMatcher` scores that pair **0.545**
— below any usable threshold, so the one pairing this feature exists to propose would never
have been offered. Insurance class names differ overwhelmingly by a *qualifier* (one name is
the other plus a word), so `core.normalize.match_score` leads with that shape and keeps
sequence similarity as a last resort. On the reference book it produces exactly one
non-identical pairing — the true one — and no false positives.

**Aliases cannot be applied by "rewriting the staged frames".** §2 proposed that, and it
cannot work for the file-upload path, which is the primary one: uploaded workbooks are staged
as *files* and read straight from the folder by the engine, so there is no intermediate frame.
`_materialize_job_snapshots` is a no-op for uploads. Aliases are therefore applied at the
engine's read boundary through a new `class_aliases` parameter — the same shape as
`upr_policy`, `exclusion` and `run_report` before it. `None` leaves every frame untouched,
which is what keeps existing goldens bit-identical.

A third, smaller one: `canonical_key` must **delete** apostrophes rather than treat them as
word breaks, or `Banker's Blanket` and `Bankers Blanket` fail to reconcile. Caught by its own
test.

### 9.3 Severity choices, and why `warn` matters as much as `error`

* **error** — rows will be discarded, or a class will develop with no paid claims. Both
  produce a plausible workbook containing a wrong number.
* **warn** — a class has premium and no claims at all (`D&O`). Legitimate for a class with no
  experience, so it must never block. An error here would train users into permissive mode and
  the gate would stop meaning anything.

`dropped_amount` uses the engine's own recovery substitution, not a raw `AMOUNTPAID` sum, so
the headline figure is the value the engine would actually have consumed.

### 9.4 What the user sees when a run is blocked

> Input check failed, so no workbooks were produced. 3,044 claim rows worth 35,503,674 would
> be discarded because their reserving class does not appear in the premium data. Affected
> classes: Health, Health Insurance. Adding the alias 'Health' → 'Health Insurance' would
> resolve this. Fix the input files, add a reserving-class alias, or ask an administrator to
> switch this organization to permissive mode.

The size, the classes, and the fix. In the UI the same report renders at the review step —
before submit — with the reconciliation table putting premium / paid / OS row counts side by
side, and Generate disabled while `would_block` is true.

### 9.5 Files

| | |
|---|---|
| `core/normalize.py` | **new** — `canonical_key`, `match_score`, `suggest_matches` |
| `processing/services/preflight.py` | **new** — `build_preflight_report`, severities, `dropped_amount` |
| `module1_engine/engine.py` | `apply_class_aliases`; `class_aliases` parameter |
| `processing/tasks.py` | `_run_preflight`, `PreflightBlocked`, the gate, report persisted every run |
| `processing/views.py` | `Module1PreflightView`; `_attach_class_aliases` snapshots the map onto the job |
| `tenants/models.py` + migration `0005` | `ReservingClassAlias`, `Organization.preflight_mode`, `alias_map_for` |
| `tenants/views.py`, `serializers.py`, `urls.py` | alias CRUD behind `class_alias.manage` |
| `accounts/.../seed_rbac.py` | `class_alias.view` / `class_alias.manage`; Actuary manages, Analyst views |
| `benchmarks/fixtures/summary_ref_aliased` | the corrected-behaviour golden |
| `src/components/PreflightReport.tsx` + test | the report, alias suggestions, reconciliation table |
| `src/pages/SummaryGeneratorPage.tsx` | runs at step 3, blocks Generate, one-click alias |

### 9.6 Verification

* `pytest module1_engine/tests` — **139 passed**, 10 goldens green (both summary states).
* `manage.py test processing datasets accounts tenants` — **328/330**; the two failures are
  `test_dataset_e2e`, which need a reachable Celery broker. On this machine host port 6379 is
  held by another project's password-protected Redis, so `.delay()` raises
  `AuthenticationError` regardless of these changes.
* `vitest` — **282 passed**. `tsc` at its unchanged 45-error baseline.

**Not verified:** no live-stack run. The local Sigma 17 containers are up but orphaned —
`sigma17-celery` cannot resolve its broker and has been retrying for ~50 minutes — so nothing
has been exercised through the real API + queue path.
