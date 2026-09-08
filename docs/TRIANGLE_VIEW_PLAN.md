# WP8 — The Triangle View (requirement 5's missing surface)

> **Goal:** Give monthly and yearly triangles a place in the product. The grain engine, the
> credibility scoring and the API were built in WP6 and are tested; **nothing renders them**,
> so a client clicking through the app cannot reach the feature at all.

Status: **implemented 2026-09-08** (see §11). Requirement 5. Depends on WP6 (shipped).
Every figure below is measured against the current repos; §9 records what the investigation
changed.

---

## 0. What is actually missing

WP6 was reported as implemented. Layer by layer:

| Layer | State |
|---|---|
| `core/grain.py` — `PeriodGrain`, monthly / quarterly / yearly | built, tested |
| `module1_engine/triangles.py` — builder, credibility, implied CDF | built, tested |
| `GET /api/module1/jobs/{id}/triangles/` | built, **11 API tests green** |
| `src/components/TriangleCredibility.tsx` | built, 8 tests green |
| `src/components/TriangleGrid.tsx` | built, 10 tests green |
| **Anything that renders either component** | **does not exist** |

`fetchTriangles` is called from nowhere. `TriangleCredibility` and `TriangleGrid` are rendered
nowhere. There is no route. Requirement 5 is, from the client's seat, not delivered.

---

## 1. How triangles reach a user today

This is what determines where the new view belongs, so it was established before choosing.

### 1.1 Surface one — the output preview (dominant)

`src/components/output/OutputPreviewDialog.tsx`, mounted in **six** pages (Summary Generator,
Update Reserve, IBNR Allocation, Movement Analysis, Sensitivity, Processing History). The user
picks a workbook from a `Select` (`Motor Insurance Payment GROSS 2017-12.xlsx`), then a sheet
(`Paid Claims Triangle`, `Reported Triangle`), and gets an **Excel-faithful** grid: a column-
letter row (A, B, C…), the header row beneath it, a frozen row-number gutter, cell gridlines,
page sizes of 25/50/100/200, and WP7's row-kind formatting so factors read `1.0157` rather than
`1.01`.

It renders the **whole sheet** as the workbook holds it: incremental block, cumulative block,
age-to-age block, the thirteen benchmark rows, Selected LDF and Selected CDF.

It is read-only, and it is **structurally workbook-bound** — its contract is "show what is in
the output ZIP".

### 1.2 Surface two — the reserve editor

`ReserveCdfEditor` / `TriangleLdfTable` on `/update-reserve`. The same whole-sheet grid, but
interactive: Selected LDF editable in place, Selected CDF derived live, age-to-age cells
clickable to strike a factor out of an average, and WP1's basis selector above it. It **writes
back** through `ldf_overrides`.

### 1.3 What both have in common

Both read the **workbook**, and the workbook is quarterly, because quarterly is the booking
basis. Neither can show a monthly or yearly triangle, because no such sheet exists — the finer
grains are computed on demand from the job's claims frame and are deliberately never written
into a reserve workbook.

---

## 2. Where the new view belongs

### 2.1 Rejected: inside the output preview

Tempting — one triangle surface — but it breaks that dialog's contract. It shows what is in the
output ZIP; monthly triangles are not in it. There is also nowhere in a file+sheet picker to put
a grain selector, class and treaty filters, a credibility panel and an implied-CDF comparison,
and the dialog is shared with Module 2.

### 2.2 Rejected: a tab inside Update Reserve

**Rejected on correctness grounds, not layout.** That page exists to select quarterly LDFs and
write them into a workbook. Putting a monthly triangle beside a Selected LDF row invites exactly
the operation the engine forbids: composing link ratios across grains, measured at **+409%**
error, because a coarse accident period aggregates fine cohorts at different maturities. The
only valid bridge is `implied_cdf_from_finer_grain`, and it is a different operation with its
own credibility gate.

### 2.3 Rejected: a section inside Summary Generator

`SummaryGeneratorPage.tsx` is already the heaviest surface in the product — a three-step wizard
plus pre-flight, large-claim exclusion, results, output preview and the UW-parameters sub-flow.
Its state is wizard-scoped and resets. A diagnostic an actuary returns to should not live inside
a run wizard.

### 2.4 **Chosen: a dedicated `/triangles` page in the IBNR Workflow group**

```
IBNR Workflow
  Reserve Summary      /summary-generator
  Update Reserves      /update-reserve
  Triangles            /triangles          <-- new
  UPR Methods          /upr-methods
```

Why this is right:

* **It mirrors an established pattern.** `/sensitivity` is precisely this shape: a diagnostic
  over a completed job, its own page, its own source-job picker. The app already teaches users
  that analysis of a finished run lives on its own page.
* **It is Module 1 work.** It reads a Reserve Summary job's claims, so it belongs beside
  Reserve Summary and Update Reserves, not in the IFRS 17 group.
* **It has room.** Grain selector, reserving-class and treaty filters, credibility panel,
  triangle grid, and the implied-CDF comparison all need space that a tab cannot give.
* **It separates diagnosis from booking.** A page whose header says "diagnostic — booking stays
  quarterly" cannot be mistaken for the surface that writes factors.
* **It gives `TriangleGrid` its first consumer**, retiring a component that is currently built,
  tested and unused.

**Discoverability, without embedding.** Two entry points link *into* the page rather than
duplicating it:

* Processing History — a "Triangles" action on successful Reserve Summary rows, beside the
  existing preview eye.
* Summary Generator — a link on the results card once a run succeeds.

Both navigate to `/triangles?job=<id>`, so the page is reachable at the moment of relevance
while living in one place.

---

## 3. The blocking prerequisite: the data is not there

**This is the finding that matters most, and it is measured, not inferred.**

`Module1TrianglesView` sources its frame from `_triangle_source_frame(job)`, which prefers
dataset snapshots and otherwise falls back to the staged upload folder. But
`run_module1_summary_task` ends with:

```python
finally:
    _cleanup_root(job)      # shutil.rmtree(job_root(job))
```

Measured today, on a job staged with the reference claims files:

```
BEFORE cleanup (what the current tests exercise): 6,580 rows
AFTER  cleanup (what production actually has)   : None
```

So for every **upload-driven** summary job — the primary path, and the one the reference
fixtures use — the triangles endpoint returns *"This job's claims data is no longer available"*.
It works only when the user drove the job from Datasets, which populates
`input_meta["dataset_snapshots"]["claims_paid"]`. There is no auto-Dataset-from-upload path.

**This is not only requirement 5's problem.** `Module1LargeClaimsView` — requirement 6 — calls
the *same* `_triangle_source_frame`. Large-claim ranking and exclusion have the identical
limitation, and `LargeClaimsPanel` is already wired into the Summary Generator, where it will
report no claims for an upload-driven run.

Both feature test suites miss it because they stage files into `job_input_subdir` and **never
run the task**, so cleanup never fires.

### 3.1 The fix

Persist the claims inputs durably on a successful summary job, exactly as Module 2 already does
for its own chaining inputs via `_persist_input_archive` (`processing/tasks.py:423`). The
mechanism, the model field (`Module1Job.input_archive`) and the reader
(`read_input_archive_bytes`) all exist and are proven; this extends them to Module 1.

```python
INPUT_ARCHIVE_CLAIMS_PAID = "claims_paid.zip"
INPUT_ARCHIVE_CLAIMS_OS   = "claims_os.zip"
```

Written inside the success path, **before** `_cleanup_root`, and only for `SUMMARY` jobs.
`_triangle_source_frame` then reads, in order: dataset snapshots → input archive → staged
folder. Three sources, most durable first.

**Retention.** `processing/services/retention.py` sweeps `output_zip` only — an input archive
would otherwise grow without bound. The sweep must cover `input_archive` on the same policy, and
`Module1Job.purge_outputs()` must clear it too. This is a storage-cost decision that has to be
made deliberately rather than discovered.

**Size.** The reference claims-paid set is 6,580 rows; zipped xlsx is single-digit MB. Bounded
by the existing upload budget check.

---

## 4. The page

```
Triangles                                    [ source job: Reserve Summary — 2017-12  ▾ ]

  Diagnostic only. Booking stays quarterly; nothing here is written to a reserve workbook.

  Grain  ( Monthly | Quarterly | Yearly )     Class [ Motor Insurance ▾ ]  Treaty [ GROSS ▾ ]

  ┌ Credibility ─────────────────────────────────────────────────────────────┐
  │  MEDIUM   158 non-empty cells · 24 median claims/cell · 41% fill          │
  │  Reasonably populated. Read development factors with some caution.        │
  └──────────────────────────────────────────────────────────────────────────┘

  [ Cumulative | Incremental | Age-to-age ]        ← which block to render

  <TriangleGrid>  rows = accident periods, cols = development periods

  ▸ Implied quarterly CDFs from this monthly triangle        (monthly only)
```

Decisions inside it:

* **Grain is a segmented control, not a dropdown.** Three mutually exclusive views the user
  flips between; a dropdown hides two of the three.
* **Credibility sits above the grid, never below it.** On the reference book the monthly view is
  `medium` at book level but `unusable` for several reserving classes — Banker's Blanket has
  **3 non-empty cells from 6 claims**. A user must read the warning before the numbers, not
  after.
* **The block selector reuses the workbook's own vocabulary** — Cumulative / Incremental /
  Age-to-age are the same three blocks the output preview shows, so the page teaches nothing new.
* **Age-to-age renders with `kind="factor"`**, so WP7's diverging scale centres on 1.0 and a
  factor below 1 is a different colour, not merely a smaller number.
* **Implied CDF is collapsed by default and offered only at monthly grain.** It is the one valid
  bridge between grains, it carries its own credibility gate, and the endpoint already rejects
  `imply_cdf=1` at quarterly.
* **`PrintableSheet` wraps the grid**, so a triangle goes into a board pack with its job id,
  grain, class and credibility stamped in the footer.

---

## 5. Backend changes

| File | Change |
|---|---|
| `processing/tasks.py` | persist `claims_paid` / `claims_os` into `input_archive` on summary success, before `_cleanup_root`; new member constants |
| `processing/views.py` | `_triangle_source_frame` reads snapshots → input archive → staged folder; same for `_os_source_frame` |
| `processing/services/retention.py` | sweep `input_archive` on the same retention policy as `output_zip` |
| `processing/models.py` | `purge_outputs()` clears `input_archive` |
| `processing/views.py` | `Module1TrianglesView`: return `reserving_classes` and `treaties` so the page can populate its filters without a second call |

The engine is unchanged. `build_triangle` and `implied_cdf_from_finer_grain` already do the work.

## 6. Frontend changes

| File | Change |
|---|---|
| `src/pages/TrianglesPage.tsx` | **new** — the page in §4 |
| `src/App.tsx` | `/triangles` route, behind `module1.run` |
| `src/components/AppSidebar.tsx` | "Triangles" in the IBNR Workflow group |
| `src/state/wizards/triangles.ts` | **new** — persist job, grain, class, treaty, block across tab switches, as every other page does |
| `src/api/module1.ts` | extend `TriangleResponseDto` with the filter lists |
| `src/pages/ProcessingHistoryPage.tsx` | "Triangles" action on successful summary rows |
| `src/pages/SummaryGeneratorPage.tsx` | link on the results card |

`TriangleGrid` and `TriangleCredibility` are consumed as they stand.

## 7. Tests

**`processing/tests/test_input_archive_availability.py`** (new — the regression that matters)
* an upload-driven summary job **still yields its claims after the task completes**, asserted
  by running the real task body and then reading the frame — the exact path the current tests
  skip
* the same for the large-claims frame (requirement 6's silent limitation)
* retention purges the input archive with the output ZIP
* a dataset-driven job still prefers snapshots

**`processing/tests/test_triangles_api.py`** (extend)
* the response carries the reserving classes and treaties present in the data
* `imply_cdf=1` is rejected at quarterly grain, accepted at monthly

**`src/pages/TrianglesPage.test.tsx`** (new)
* grain switch refetches and re-renders
* an `unusable` credibility renders its warning **above** the grid
* the implied-CDF section is absent at quarterly grain
* a job with no claims data available renders an explanation, not an empty grid
* filters survive a remount from persisted state

## 8. Edge cases

* **Job whose claims are genuinely gone** (pre-WP8 jobs, or retention-purged) — the page must
  say so and point at re-running, not render an empty triangle. Old jobs will never have an
  input archive; that is unavoidable and must be stated rather than hidden.
* **`_get_accessible_job` is per-user, not per-org** — a colleague cannot open another user's
  job triangles. Consistent with every other Module 1 read endpoint, so not changed here, but
  it means the page's job picker must only offer the user's own runs.
* **A class/treaty slice with no claims** — `build_triangle` returns an empty set; render the
  credibility panel's `unusable` state rather than a blank page.
* **Yearly grain over a two-year experience period** gives a 2×2 triangle — structurally
  `unusable`, and the panel must say why rather than showing four numbers as if they were a
  development pattern.
* **Very fine grain on a long book** — monthly over ten years is 120 accident periods; the grid
  scrolls inside its container with `stickyFirstColumn`, already supported.

## 9. Estimate

| | |
|---|---|
| input-archive persistence, three-source reader, retention | 1.5d |
| `TrianglesPage` + route + nav + persisted state | 2d |
| entry points from history and the results card | 0.5d |
| tests (including the availability regression) | 1.5d |
| **Total** | **~5.5 days** |

## 10. What the investigation changed

* **Requirement 5 has no UI at all.** Reported implemented; the engine and API are real and
  tested, but nothing renders them. `TriangleGrid` is likewise built, tested and unused.
* **The endpoint does not work for upload-driven jobs** — proven, 6,580 rows before
  `_cleanup_root` and `None` after. Any plan that added only a page would have shipped a screen
  that says "claims data is no longer available" for the primary input path.
* **Requirement 6 shares the defect exactly**, through the same `_triangle_source_frame`. Fixing
  the availability problem repairs large-claim ranking at the same time.
* **Both feature suites miss it** because they stage files and never run the task. The new
  availability test runs the real task body, which is the only way this class of gap surfaces.
* **Placement is a correctness decision, not a layout one.** Update Reserve was the intuitive
  home and is the wrong one: putting a monthly triangle beside a Selected LDF row invites
  composing factors across grains, measured at +409%.

---

## 11. Implementation status — built (2026-09-08)

Requirement 5 is now reachable. `/triangles`, in the IBNR Workflow group, behind `module1.run`.

### 11.1 The availability fix, proven

`_persist_summary_claims` archives the claims inputs into the existing
`Module1Job.input_archive` on success, immediately **before** `_cleanup_root` destroys the
staging folder. `_triangle_source_frame` and `_os_source_frame` now read three sources, most
durable first: **dataset snapshots → input archive → staged folder**.

`test_input_archive_availability.py` runs the **real task body** and then reads the frames — the
step both existing suites skipped, and the only way this class of gap surfaces:

| | before | after |
|---|---|---|
| paid claims readable once the job succeeds | `None` | **6,580 rows** |
| outstanding claims readable | `None` | present |
| a monthly triangle buildable from what survived | — | **24 accident periods** |

Requirement 6's large-claim ranking is repaired by the same change, through the same source
frame.

### 11.2 A correction to §3.1

The plan said retention sweeps `output_zip` only and that the input archive would grow
unbounded. **That was wrong.** `Module1Job.purge_output()` already deletes `input_archive` on
the same retention clock, and says so in its own docstring. No retention change was needed; a
test now pins the behaviour so it stays true.

### 11.3 Placement, as built

The page is deliberately *not* a tab in Update Reserve. That surface writes Selected LDFs into a
quarterly workbook, and putting a monthly triangle beside it invites composing link ratios
across grains — invalid, and measured at +409%. The only bridge offered is the implied-CDF
table, collapsed by default and available **only at monthly grain**, because the endpoint
rejects it at quarterly by design.

Two entry points link in without duplicating the page: a grid icon on successful Reserve
Summary rows in Processing History, and a "View triangles" action on the Summary Generator's
results card. Both navigate to `/triangles?job=<id>`, which the page adopts during its first
render rather than in an effect — an effect would have fetched twice, once for the previous job.

### 11.4 Files

| | |
|---|---|
| `processing/tasks.py` | `_persist_summary_claims`, archive member prefixes, called before cleanup |
| `processing/views.py` | `_frame_from_input_archive`; three-source paid and OS readers; filter vocabulary on the triangles response |
| `processing/tests/test_input_archive_availability.py` | **new** — 6 tests, runs the real task |
| `processing/tests/test_triangles_api.py` | filter-vocabulary cases |
| `src/pages/TrianglesPage.tsx` + test | **new** — the page, 10 tests |
| `src/state/wizards/triangles.ts` | **new** — persisted view state |
| `src/App.tsx`, `src/components/AppSidebar.tsx` | route and nav entry |
| `src/pages/ProcessingHistoryPage.tsx`, `SummaryGeneratorPage.tsx` | entry points |

`TriangleGrid` and `TriangleCredibility` are consumed unchanged — both were built, tested and
unused until now.

### 11.5 Verification

* `pytest module1_engine/tests module2_engine` — **286 passed**, 10 goldens green.
* `manage.py test processing datasets accounts tenants` — **336/338**; the two failures are
  `test_dataset_e2e`, which need a reachable Celery broker (host port 6379 is held by another
  project's password-protected Redis on this machine).
* `vitest` — **292 passed** (31 files). `tsc` at its unchanged 45-error baseline; build clean.

**Not verified:** no live-stack run. The page has never been opened in a browser.
