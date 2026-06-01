# Sigma 17 — Jobs & Processing Performance Optimization Plan

**Status:** Proposed (awaiting approval to implement)
**Author:** Engineering
**Date:** 2026-05-31
**Constraints (agreed):** Output must be **bit-identical** to current results · system must **scale to large data** · enterprise/production-grade.

---

## 0. Executive summary

Jobs are slow for four compounding reasons, in order of impact:

1. **Algorithmic hot loops in the compute engines.** Two functions are effectively O(N²): `module2_engine.calculate_additional_matrix` (`iterrows` + a full-DataFrame boolean filter *per cell*) and `module1_engine.calculate_incremental_triangle` (`iterrows` called inside a triple-nested ReservingClass × HeadOfDamage × RI loop). Plus dozens of `.apply(..., axis=1)`, `merge`/`concat`-in-loop, and recomputation of `calculate_upr`.
2. **Excel as the data-transport layer.** Every stage reads/writes `.xlsx` with `openpyxl` (slow), full ZIPs are decompressed into memory to extract a single artifact, dataset snapshots round-trip through pandas→disk→pandas, and Module 2 re-reads its own just-written output sheet-by-sheet.
3. **Job orchestration.** Per-combination work runs serially in one task; Celery runs on defaults (no concurrency/queue/prefetch tuning); `CONN_MAX_AGE=0` churns a new DB connection per task; job creation does ~15 queries with N+1 snapshot reads.
4. **Frontend perceived performance.** 2.5 s blocking poll loops, no history auto-refresh, no table virtualization, no react-query caching, per-row PATCH saves.

The plan is **safety-net first** (golden-output regression tests + timing instrumentation), because bit-identical output is mandatory, then five implementation phases ordered by impact-per-risk. Expected outcome on large data: **single-digit-minutes → seconds for compute-bound stages**, and **order-of-magnitude lower I/O and DB load**.

---

## 1. Current architecture (as-built)

```
Frontend (React)                        Backend (Django + DRF + Celery + Redis + Postgres)
─────────────────                       ──────────────────────────────────────────────────
SummaryGeneratorPage  ──POST form──►  Module1SummaryJobView
IbnrAllocationPage                      ├─ validate dates/files/dataset ids
UpdateReservePage                       ├─ create Module1Job (status=PENDING)
   │                                    ├─ save uploads to work_dir (MEDIA_ROOT)
   │                                    ├─ create DatasetSnapshot rows (lock dataset)
   │                                    └─ run_module1_*_task.delay()         ── enqueue ──► Redis
   │                                                                                            │
   │   poll every 2500ms  ◄──GET job────  Module1JobView (status/progress)                      ▼
   │                                                                                       Celery worker
   └─ preview/download   ◄──GET rows───  output_preview (openpyxl page reads)              ├─ set RUNNING
                                                                                           ├─ materialize snapshots → xlsx
                                                                                           ├─ module1_engine / module2_engine
                                                                                           │     (pandas/openpyxl compute)
                                                                                           ├─ shutil.make_archive → zip
                                                                                           ├─ save output_zip (FileField)
                                                                                           └─ delete work_dir
```

**Job model:** `processing/models.py::Module1Job` (UUID pk; `job_type` ∈ {SUMMARY, POLICY_UPR, UPDATE_RESERVE, UW_PARAMETERS, MODULE2_ALLOCATE, MODULE2_PROCESS}; `status`; `work_dir`; `output_zip`; `input_meta` JSON; `source_job` self-FK for chaining; retention fields). Indexes on (org, type, status, -created_at) and (retention_until, legal_hold).

**Tasks:** `processing/tasks.py` — six run tasks + two retention tasks. Each loads the job, materializes dataset snapshots to `.xlsx`, calls the engine with file paths or bytes, zips output, finalizes.

**Engines:** `module1_engine/engine.py` (summary, policy UPR, update reserve) + `uw_patch.py`; `module2_engine/engine.py` (allocate, process) with thin re-export wrappers `allocator.py`/`processor.py`/`io.py`.

---

## 2. Root-cause diagnosis (ranked, with evidence)

### Tier A — Engine compute (dominant cost on large data)

| # | Location | Problem | Complexity |
|---|----------|---------|-----------|
| A1 | `module2_engine/engine.py:49-69` `calculate_additional_matrix` | `for index, row in df.iterrows()` then **a boolean filter over the whole df for every (row, col) age cell**; writes via `.at[]` | **O(N² · max_age)** |
| A2 | `module1_engine/engine.py:520-547` `calculate_incremental_triangle` | `iterrows` building a triangle with `.at[] +=`; **called twice per (ReservingClass × HeadOfDamage × RI)** combination in the loop at `~966-1172` | O(rows) × O(combinations) |
| A3 | `module2_engine/engine.py:376-430` UPR run-off | triple nested loop + **`pd.concat([...], ignore_index=True)` inside the loop** (quadratic append) + per-cell `.loc` discount lookups | O(u·p²) |
| A4 | `module2_engine/engine.py:538-547, 595-626` | **7 + 8 separate `.apply(lambda row: ..., axis=1)`** for what is one vectorized mask each | O(15·N) |
| A5 | `module1_engine/engine.py` `import_data`/`summarize_upr_by_reserving_class` | `.apply(..., axis=1)` for conditional columns; **`merge`/`concat` accumulated inside the per-quarter loop** (`~317-378`); `in list` membership per row | O(quarters²) |
| A6 | `module1_engine/engine.py:816 & 855` | `calculate_upr` computed on full df, then **recomputed** on the GROSS-filtered df | redundant pass |
| A7 | `module2_engine/engine.py:296-312` | discount factors looked up with `.loc[df["Quarterly"]==i]` **inside a per-column loop** | O(age·log d) |

### Tier B — Excel & file I/O

| # | Location | Problem |
|---|----------|---------|
| B1 | `processing/services/source_resolver.py:204-232` `read_artifact_bytes` | `raw = zf_stream.read()` loads the **entire output ZIP into memory** to extract one inner file; happens 2× per Module 2 process job |
| B2 | `module2_engine/engine.py:780-781` | Module 2 process **re-reads all 10 allocate sheets from bytes** it just produced, instead of reusing the in-memory DataFrames |
| B3 | `datasets/services/engine_adapter.py` `write_snapshot_as_sheet` | each contributing snapshot **reads the whole workbook back from disk and rewrites all sheets** (round-trip per kind) |
| B4 | `module1_engine/engine.py:918-960` | summary export opens the file to read "Additional Summary", then writes 6 sheets in `mode='a'` (full-workbook reload); ~39 Excel I/O calls total in the file |
| B5 | `processing/tasks.py:133-165` `_zip`/`_finalize` | output ZIP opened **multiple times** (save, then list artifacts); openpyxl everywhere (vs `xlsxwriter`) |
| B6 | `module1_engine/engine.py:966-1172` | up to **hundreds of `.xlsx` files** written sequentially (one per combination, 6-8 sheets each) |
| B7 | `processing/output_preview.py` | every preview page does a **full `load_workbook`** (no `read_only`/streaming) |

### Tier C — Job pipeline / infra

| # | Location | Problem |
|---|----------|---------|
| C1 | `config/settings.py:88-93` | **`CONN_MAX_AGE` unset (=0)** → new Postgres connection per task/request |
| C2 | `config/settings.py:156-160`, `config/celery.py` | **no** `worker_concurrency`, `worker_prefetch_multiplier`, task routes/queues, or `acks_late`; all jobs share one default queue |
| C3 | no `CACHES` configured | no cache for org/retention lookups; repeated per task |
| C4 | `processing/views.py` job creation | ~15 queries before enqueue; **N+1 snapshot row reads** (`create_snapshot` queries each dataset's full row table in a loop) |
| C5 | engine per-combination work | **serial**; no use of multiple cores even though combinations are independent |
| C6 | outputs on local `media_volume` | not horizontally scalable; ZIP read/write is local-disk-bound |
| C7 | `retention.py:92-107` cascade purge | N+1 walking `derived_jobs` per node |

### Tier D — Frontend perceived perf

| # | Location | Problem |
|---|----------|---------|
| D1 | `api/module1.ts:305`, `api/module2.ts:153` | fixed **2500 ms** poll, blocking `await` loop; no backoff |
| D2 | `ProcessingHistoryPage.tsx:168` | **no auto-refresh**; manual button only → stale "processing" |
| D3 | `Module1OutputPreviewDialog.tsx:256-264`, `DatasetRowGrid` | **no virtualization**; renders every row; DatasetDetail loads `page_size:1000` |
| D4 | most `useQuery` calls | no `staleTime`/dedup → redundant fetches |
| D5 | `DatasetDetailPage.tsx:128-133` | **per-row PATCH** save (50 rows = 50 requests) |

---

## 3. Guardrails — correctness-first (PHASE 0, must precede all changes)

Because output must be **bit-identical**, nothing in Tiers A/B is touched until a regression net exists.

**0.1 Golden-output harness.** Capture representative real inputs (small + medium + one large) for every job type. Run the *current* engines, store outputs as the golden set (xlsx → normalized to Parquet/CSV per sheet so comparison is value-based, not byte-of-xlsx). Add `pytest` cases that run the engine and assert each optimized output equals golden.

**0.2 Comparison semantics.** Numeric cells compared with `assert_frame_equal(check_exact=True)` first; where a change reorders floating-point summation (see Risk R1), fall back to `rtol=0, atol=0` *only after* proving order-preservation, else an explicitly-signed-off `atol=1e-9`. Strings/labels/sheet order/shape compared exactly.

**0.3 Timing & memory instrumentation.** Add a lightweight `@profile_stage` decorator (logs wall-clock + peak RSS per engine stage and per Celery task) gated behind an env flag. This produces the baseline the `PERFORMANCE_SLO.md` "Capacity baseline to measure" section currently lacks, and tells us empirically which Tier-A item dominates *our* data.

**0.4 Benchmark fixtures.** A `manage.py bench_engines --size {small,medium,large}` command that runs each engine on synthetic-but-realistic data and prints a stage timing table. Used as the before/after scoreboard for every PR.

**Exit criteria for Phase 0:** golden tests green on current code; baseline timing table recorded in this doc's appendix.

---

## 4. Phased implementation plan

> Each item lists: **change · files · expected speedup · risk · effort**. Speedups are order-of-magnitude estimates pending Phase 0 baselines.

### Phase 1 — Engine compute vectorization  *(biggest win; gated by golden tests)*

**1.1 Rewrite `calculate_additional_matrix` (A1).** Replace the per-cell DataFrame filter with a precomputed lookup: build a `{(RESERVINGCLASS, GROSS/RI, Age) → Incremental}` mapping once (a `set_index` / dict), then construct the matrix with vectorized column operations (shift/merge by `future_age`). The output is a division, not a sum → **bit-identical** by construction.
- Files: `module2_engine/engine.py`
- Speedup: **O(N²·age) → ~O(N·age)**; on large data this is the single largest gain.
- Risk: Medium (logic-heavy). Mitigation: golden tests + keep old function as `_calculate_additional_matrix_legacy` behind a feature flag for A/B verification.
- Effort: M.

**1.2 Vectorize `calculate_incremental_triangle` (A2).** Compute `development_quarter` as a vectorized period subtraction, filter valid rows, then `groupby([accident_quarter, development_quarter])["Amount"].sum()` and `unstack` into the triangle. **Per-cell sum order is preserved** (pandas sums within a group in row order; rows for different cells never mixed) → bit-identical.
- Files: `module1_engine/engine.py`
- Speedup: large × (combination count); compounds with 1.5.
- Risk: Medium (float order — see R1). Effort: M.

**1.3 De-loop the UPR run-off (A3).** Build rows into a Python list and do **one** `pd.DataFrame(...)` at the end (kill concat-in-loop); pre-index discount factors into a dict/Series for O(1) lookup.
- Files: `module2_engine/engine.py`. Speedup: O(p²)→O(p). Risk: Low. Effort: M.

**1.4 Vectorize the `.apply(axis=1)` blocks (A4, A5).** Replace each `df.apply(lambda row: x if cond else 0, axis=1)` with `df["col"] = df["src"].where(mask, 0)` (single mask reused across the CY/PY column families). Convert `in [...]` membership lists to `set`s and use `.isin()`.
- Files: `module2_engine/engine.py`, `module1_engine/engine.py`. Speedup: 15·N → N. Risk: Low. Effort: S–M.

**1.5 Remove redundant `calculate_upr` recompute (A6); precompute period columns once (A5).** Compute UPR once and slice the GROSS subset from the already-computed frame; hoist `ISSUEDATE.dt.to_period('Q')` etc. out of the per-quarter loop into a single precomputed column.
- Files: `module1_engine/engine.py`. Speedup: ~2× on affected stage. Risk: Low–Med. Effort: S.

**1.6 Parallelize independent combinations (A2 loop / B6).** The ReservingClass × HeadOfDamage × RI loop is embarrassingly parallel. Run it with `concurrent.futures.ProcessPoolExecutor` (or split into Celery subtasks — see 3.x) so it uses all cores.
- Files: `module1_engine/engine.py` (+ task layer). Speedup: ~×cores. Risk: Med (determinism of output file set — preserve ordering when merging). Effort: M.

### Phase 2 — Excel & file I/O

**2.1 Stream single-artifact ZIP reads (B1).** Use `ZipFile.open(name)` / `read(name)` on the FileField stream without slurping the whole archive into memory; or store artifacts individually (see 5.2).
- Files: `processing/services/source_resolver.py`, `processing/services/reserve_workbook.py`. Speedup: large memory + time drop on chained jobs. Risk: Low. Effort: S.

**2.2 Reuse DataFrames in Module 2 process (B2).** Have `_build_allocate_outputs` return the in-memory frames so `run_module2_process` writes them directly instead of re-reading 10 sheets from bytes.
- Files: `module2_engine/engine.py`. Speedup: removes a full 10-sheet parse. Risk: Low. Effort: S.

**2.3 Switch writes to `xlsxwriter`, single-pass workbooks (B3, B4, B5).** Compose each workbook in memory and write once with `xlsxwriter` (`constant_memory` mode for big sheets); stop append-mode reloads and snapshot disk round-trips. Keep `openpyxl` only where formulas must be preserved.
- Files: `module1_engine/engine.py`, `module2_engine/engine.py`, `datasets/services/engine_adapter.py`. Speedup: 5–50× on write-heavy stages. Risk: Med (xlsxwriter can't edit existing files / formatting parity — verify via golden tests). Effort: M.

**2.4 Streaming preview reads (B7).** `load_workbook(read_only=True)` + `iter_rows` bounded to the requested page; cache nothing large.
- Files: `processing/output_preview.py`. Speedup: preview p95 within SLO on big sheets. Risk: Low. Effort: S.

### Phase 3 — Job pipeline / infra

**3.1 DB connection reuse (C1).** Set `CONN_MAX_AGE=600` (and `OPTIONS={'pool': ...}` or PgBouncer for workers); enable `CONN_HEALTH_CHECKS`.
- Files: `config/settings.py`. Speedup: removes per-task connect overhead. Risk: Low. Effort: XS.

**3.2 Celery tuning + queues (C2).** Set `worker_prefetch_multiplier=1`, `task_acks_late=True`, `worker_max_tasks_per_child` (guard memory leaks), explicit concurrency, and **route heavy compute to a dedicated queue** separate from light/quick tasks so a big job can't starve everything.
- Files: `config/celery.py`, `config/settings.py`, `docker-compose.yml` (worker per queue). Risk: Low–Med. Effort: S.

**3.3 Fan-out per-combination via Celery `group`/`chord` (C5, ties to 1.6).** Split a summary job's combinations into subtasks, then a chord callback zips/finalizes. Gives horizontal scale-out across worker nodes (vs. single-process pool). Choose between this and 1.6 based on infra (1.6 is simpler; 3.3 scales beyond one box).
- Files: `processing/tasks.py`, engines. Risk: Med. Effort: L.

**3.4 Kill N+1 on job creation (C4).** Batch snapshot creation: one query per dataset kind using `bulk_create`, prefetch rows; wrap creation in a single transaction.
- Files: `processing/views.py`, `datasets/services/snapshots.py`. Speedup: ~15 queries → a handful. Risk: Low. Effort: S.

**3.5 Cache config (C3).** Add Redis `CACHES`; cache org retention days and other hot read-mostly lookups.
- Files: `config/settings.py`, `source_resolver.py`. Risk: Low. Effort: S.

**3.6 Cascade purge in one query (C7).** Replace BFS-with-per-node queries by a recursive CTE (raw SQL) to fetch the whole lineage at once.
- Files: `processing/services/retention.py`. Risk: Low. Effort: S.

### Phase 4 — Frontend perceived performance

**4.1 Smart polling (D1).** Move polling into react-query with exponential backoff (e.g., 1s→2s→5s cap), `AbortSignal` on unmount, and stop blocking the handler so users can navigate away.
- Files: `api/module1.ts`, `api/module2.ts`, page handlers. Risk: Low. Effort: S.

**4.2 History auto-refresh (D2).** `refetchInterval` while any row is "processing"; stop when all settled.
- Files: `ProcessingHistoryPage.tsx`. Risk: Low. Effort: XS.

**4.3 Table virtualization (D3).** `@tanstack/react-virtual` for preview + dataset grids; paginate dataset detail instead of `page_size:1000`.
- Files: `Module1OutputPreviewDialog.tsx`, `DatasetRowGrid.tsx`, `DatasetDetailPage.tsx`. Risk: Low. Effort: M.

**4.4 Query caching + batch PATCH (D4, D5).** Set `staleTime`; add a bulk dataset-row update endpoint (the code already flags this as "Phase 5").
- Files: frontend `useQuery` sites, `DatasetDetailPage.tsx`, `datasets/views.py`. Risk: Low. Effort: S–M.

### Phase 5 — Enterprise hardening for large data (architectural)

**5.1 Replace Excel-as-transport between chained stages with a columnar intermediate.** Persist inter-stage data (Combined_Summary, allocate outputs) as **Parquet/Arrow** alongside the user-facing `.xlsx`. Chained jobs read Parquet (10–100× faster, typed) and only render `.xlsx` at the final user download. Keeps user-facing format identical.
**5.2 Store artifacts individually in object storage (S3/MinIO)** instead of one ZIP on local disk (C6, B1): per-artifact fetch becomes a direct object GET; enables multi-node workers and CDN download.
**5.3 Progress/event reporting** via Celery custom states + (optionally) WebSocket/SSE, removing poll load entirely and giving real progress bars.
**5.4 Observability:** structured stage timings → metrics (Prometheus/Grafana or hosted), alerting on the existing SLOs in `PERFORMANCE_SLO.md`; per-job duration histograms by type.
**5.5 Backpressure & limits:** enforce documented max workbook dimensions; reject/queue beyond capacity; idempotent task retries with `acks_late`.

---

## 5. Risks & mitigations

- **R1 — Floating-point summation order (bit-identical risk).** Vectorized `groupby.sum`/`unstack` can reorder additions vs. sequential `+=`. Mitigation: pandas sums *within a group in row order*; since each triangle cell maps to exactly one group, per-cell order is preserved → bit-identical. Verify empirically in Phase 0; if any cell diverges, sort within group by original index before aggregating, or get sign-off for `atol=1e-9`.
- **R2 — `xlsxwriter` formatting/feature parity.** It can't modify existing files and has different styling APIs. Mitigation: golden tests on values; manual visual diff on formatting; keep `openpyxl` for formula-bearing/edit-in-place paths (update-reserve overrides).
- **R3 — Parallelism nondeterminism (1.6/3.3).** Output file set/order could vary. Mitigation: deterministic ordering when collecting results; golden tests assert the full artifact set.
- **R4 — Behavior lock-in of existing bugs.** Bit-identical means we *preserve current results including any quirks*. Any genuine bug fix is a separate, explicitly-approved change, not bundled into perf work.
- **R5 — Connection pooling vs. Celery prefork.** `CONN_MAX_AGE` with prefork needs care (close on fork). Mitigation: validate with health checks; consider PgBouncer.

---

## 6. Sequencing & effort

| Phase | Theme | Depends on | Rel. effort | Rel. impact |
|-------|-------|-----------|-------------|-------------|
| 0 | Safety net + baselines | — | M | (enables all) |
| 1 | Engine vectorization | 0 | L | ★★★★★ |
| 2 | Excel/file I/O | 0 | M | ★★★★ |
| 3 | Pipeline/infra | 0 | M | ★★★ |
| 4 | Frontend perceived | — (parallel) | M | ★★★ |
| 5 | Enterprise/scale-out | 1–3 | L | ★★★★ (large data) |

Recommended order: **0 → (1 ∥ 4) → 2 → 3 → 5**. Phase 4 can run in parallel since it's a separate codebase. Quick wins to land first: **3.1 (CONN_MAX_AGE), 3.2 (Celery), 2.1 (ZIP), 1.4 (`.apply`→vectorized), 4.1/4.2 (polling)** — low risk, immediately felt.

---

## 7. Success metrics (tie to `PERFORMANCE_SLO.md`)

- Compute-bound stage wall-clock on the "large" fixture: target **≥10× reduction** for A1/A2-dominated jobs.
- Module 1/2 submit→success p95 on large workbook: define target after Phase 0 baseline (e.g., ≤ X min).
- Peak worker RSS per job: target **≥2× reduction** (streaming I/O, no full-ZIP slurp).
- DB queries per job creation: from ~15 → ≤ 5.
- Job status polling endpoint p95 ≤ 500 ms under N concurrent users (existing SLO), with reduced request volume from backoff polling.
- Golden-output tests: **100% pass** at every step.

---

## 8. Appendix — baseline timing table (to be filled in Phase 0)

Baseline measured 2026-05-31 on the real desktop reference dataset
(~14.8k premium / 6.6k paid / 6.3k OS rows; M2 accident periods 2018-Q1..2024-Q2),
via `manage.py bench_engines`, current (pre-optimisation) code:

| Job type (fixture) | Total wall-clock | Top stage | Peak RSS Δ | Notes |
|--------------------|------------------|-----------|-----------|-------|
| Summary (`summary_ref`) | **25.4 s** | `run_generate_summary` 24.7 s | — | triple-nested reserve loop + iterrows triangle |
| Policy UPR (`policy_upr_ref`) | 3.0 s | `run_policy_level_upr` 2.8 s | — | light |
| M2 Allocate (`m2_allocate_ref`) | **84.6 s** | `run_module2_allocate` 80.1 s | +124 MB | `calculate_additional_matrix` O(N²) dominates |
| M2 Process (`m2_process_ref`) | **99.5 s** | `run_module2_process` 94.7 s | +12 MB | rebuilds allocate, then movement/IFRS |

Goldens frozen for all four (summary = 33 workbooks / 103 sheets). Golden
regression `pytest module1_engine/tests/test_golden_engines.py` = **5 passed**.

---

## 9. Progress log

**2026-05-31 — Phase 0 foundation landed (behavior-preserving):**
- `core/profiling.py` — `stage_timer`/`profile_stage`/`profiling_session` + `format_report` (no-op unless a session is active or `SIGMA_PROFILE=1`).
- `processing/golden.py` — value-level freeze/compare (`diff_struct`, exact float64 via pickle).
- `processing/benchmarks.py` — fixture discovery + dispatch to all 5 engine entry points under profiling.
- `bench_engines` + `capture_golden` management commands.
- Tests: `module1_engine/tests/test_profiling.py`, `module1_engine/tests/test_golden_engines.py` (data-driven, skips with no fixtures). `benchmarks/README.md` + `.gitignore`.
- Status: 9 passed / 1 skipped. **Blocked on representative fixture data** to capture goldens + record the §8 baseline.

**2026-05-31 — Quick wins landed (no golden data required, behavior-preserving):**
- §3.1 `CONN_MAX_AGE=600` + `CONN_HEALTH_CHECKS` (env-overridable; 0 behind PgBouncer).
- §3.2 Celery: `prefetch=1`, `acks_late`+`reject_on_worker_lost`, `max_tasks_per_child=50`, optional `max_memory_per_child`, and `task_routes` → dedicated `compute`/`retention` queues. Worker command in `docker-compose.yml` updated to consume `compute,default,retention` with `--concurrency`. New env vars documented in `.env.example`.
- §4.1 Frontend pollers (`waitForModule1Job`/`waitForModule2Job`) → exponential backoff (1.5s→×1.5→10s cap); ~240→~70 polls on a 10-min job.
- §4.2 `ProcessingHistoryPage` auto-refreshes (silent, 5s) while any job is processing; stops when all settle.
- Verified: `manage.py check` clean, Celery config asserted, backend 9 passed/1 skipped, frontend 15 passed.

**Still pending (need data / larger change):** §3.4 N+1 on job creation, §3.5 cache, §3.6 cascade-purge CTE; rest of Phases 1 & 2.

**2026-05-31 — Phase 0 ARMED with real data + first Phase-1 win:**
- Built 4 fixtures from the desktop reference dataset (`summary_ref`, `policy_upr_ref`, `m2_allocate_ref`, `m2_process_ref`) under `benchmarks/fixtures/` (git-ignored). Inferred summary dates from the reference output (start=bop=01-01-2016, eop=end=31-12-2017; M2 accounting year 2024).
- Captured goldens (summary = 33 workbooks / 103 sheets) and recorded the §8 baseline. Golden regression: 5 passed (215 s).
- **§1.1 `calculate_additional_matrix` vectorised** (`module2_engine/engine.py`) — replaced the O(N²·age) per-cell `iterrows`+filter with a keyed per-column lookup. **Bit-identical** (golden ✓).

  | Job | Before | After | Stage (matrix) |
  |-----|--------|-------|----------------|
  | M2 Allocate | 84.6 s | **34.3 s** | 80.1 s → 29.5 s |
  | M2 Process  | 99.5 s | **49.7 s** | (re-runs allocate) |

- Next target: §1.3 UPR run-off concat-in-loop (~remaining 29 s in allocate), then §1.4 `.apply` vectorisation, §1.2 Module 1 triangle.

**2026-05-31 — Phase 1 continued; Module 2 + Module 1 summary largely done (all bit-identical):**
All verified via `bench_engines --check` and the full golden suite (`pytest module1_engine/tests/` = 13 passed, 54 s).

| Job | Baseline | Now | Speedup |
|-----|----------|-----|---------|
| M2 Allocate | 84.6 s | **12.5 s** | 6.8× |
| M2 Process  | 99.5 s | **27.1 s** | 3.7× |
| Summary     | 25.4 s | **11.4 s** | 2.2× |
| Policy UPR  | 3.0 s  | 3.0 s   | — |

Changes (all `module1_engine/engine.py` / `module2_engine/engine.py`, bit-identical):
- §1.1 `calculate_additional_matrix` vectorised (80.1 s → 0.04 s).
- §1.3 UPR run-off: dict-row accumulation instead of concat-in-loop + Series cells; discount factors and payment-pattern multipliers pre-indexed (11.6 s → 0.09 s).
- §B Module 2 reads workbook once (`pd.ExcelFile`) instead of 8 re-parses.
- §1.4 `summarize_upr_by_reserving_class`: every per-row `.apply(axis=1)` → vectorised `.where(mask)` with hoisted masks / issue-quarter string, **preserving the in-place `DAC`→`UCR` assignment order** (14.6 s → 0.51 s).
- Added per-stage `stage_timer` instrumentation throughout both engines (no-op unless profiling).

**Remaining hot spots (now mostly I/O — Phase 2):** Module 2 `write_workbook` ~7 s (openpyxl; needs `xlsxwriter` dependency — not yet added), Module 1 `premium_load` ~2.5 s + `reserve_loop` ~4.5 s (openpyxl read/write), M2 process re-reads its own 10 sheets (§2.2). These need either a new dependency or larger changes; deferred pending sign-off.

**2026-05-31 (cont.) — §2.2 done (partial); §1.2 attempted and reverted:**
- §2.2: `_build_allocate_outputs` now returns the in-memory sheet frames; `run_module2_process` writes the allocate sheets into the final workbook from those frames instead of re-parsing `out_bytes` once per sheet. **Bit-identical** (golden ✓). NB: the two *computation inputs* (`MainSheet`, `Combined Summary`) are still read back from `out_bytes` — feeding the in-memory frames into the movement/IFRS math changed several computed cells (caught by the golden net; the xlsx round-trip normalises dtypes the math depends on). Net wall-clock gain is small (process is dominated by the openpyxl write), but it removes 10 redundant full re-parses.
- §1.2 (Module 1 triangle vectorisation): **attempted, reverted.** A groupby version changed the triangle's int/float dtype pattern, and the downstream cumulative `NaN`-fill is dtype-sensitive, so it was not bit-identical (only "Salvage GROSS" workbooks diverged — gold `NaN` vs new `0.0` in the run-off region). Reverted to the original loop; it is not a hot path on the reference data. **Revisit only with an R1 tolerance sign-off** if large-claim datasets make the iterrows a bottleneck.
- Full suite green: `pytest module1_engine/tests/` = 13 passed.

**Net engine results unchanged from the table above:** M2 Allocate 12.5 s, M2 Process ~27 s, Summary 11.4 s — all bit-identical. The compute hot paths are eliminated; what remains is openpyxl I/O, which needs a dependency decision (`xlsxwriter` for writes; optionally `python-calamine` for faster reads).

**2026-06-01 — Phase 2 Excel I/O done (deps approved); FINAL engine numbers:**
Added `xlsxwriter` + `python-calamine` (pyproject + poetry.lock regenerated, `poetry check --lock` clean). New module `core/excel.py` centralises engine choice with openpyxl fallback and env overrides (`SIGMA_EXCEL_READ_ENGINE` / `SIGMA_EXCEL_WRITE_ENGINE`). Reads → calamine where safe; fresh-workbook writes → xlsxwriter. Writes that inject formulas / load-and-edit / style (Module 1 Combined_Summary + per-class reserve workbooks, `uw_patch`) intentionally stay on openpyxl. All golden-verified; `pytest module1_engine/tests/` = 13 passed; 14 processing API tests pass via Django runner.

| Job | Baseline | **Final** | Speedup |
|-----|----------|-----------|---------|
| M2 Allocate (`m2_allocate_ref`) | 84.6 s | **9.9 s** | **8.6×** |
| M2 Process (`m2_process_ref`)   | 99.5 s | **15.9 s** | **6.3×** |
| Summary (`summary_ref`)         | 25.4 s | **8.6 s** | **3.0×** |
| Policy UPR (`policy_upr_ref`)   | 3.0 s  | **1.3 s** | **2.3×** |

Applied: M2 reads (calamine, ~0.7 s→0.13 s) + allocate write + process final write (xlsxwriter, incl. the `startrow` reconciliation sheets — verified xlsxwriter supports same-sheet multi-write); M1 input reads (premium 2.5 s→0.85 s, claims ~2.2 s→~1.0 s). Remaining time is now Module 1's openpyxl per-class reserve-workbook writes (formula injection — must stay openpyxl) and intrinsic compute; further gains there would need a formula-write redesign (xlsxwriter API) — deferred. **The performance objective is met: every job is bit-identical and 2.3–8.6× faster.**

**2026-06-01 — Phase 3 job-pipeline items (no new deps):**
- §3.4 `create_snapshot` (`datasets/services/snapshots.py`): serialize the row queryset with DRF `many=True` in one pass instead of instantiating a serializer per row. Identical payload (flat ModelSerializers, no per-row lookups), materially faster on large datasets — directly helps the large-data goal since snapshotting freezes every row at job-submit time.
- §3.6 `_walk_descendants` (`processing/services/retention.py`): replaced the `derived_jobs.all()`-per-node N+1 with one org-scoped `(id, source_job_id)` edge scan + one `in_bulk` fetch (2 queries total); same BFS leaves-first invariant. Lineage never crosses orgs, so the org scope is exact.
- §3.5 (Redis cache of org retention days): **intentionally skipped** — saves one trivial query per job but risks serving stale retention settings after an admin change; not worth it vs multi-second engine runs.
- Verified: full Django suite **108 passed**, golden suite **13 passed**.

**2026-06-01 — Module 1 reserve-workbook writes: investigated, must stay openpyxl (bit-identical floor reached).**
The per-class reserve workbooks inject formula cells ("Selected LDF" `=1`, "Selected CDF" `=PRODUCT(...)`) via openpyxl `ws.cell`. Tested swapping to xlsxwriter: openpyxl writes formulas with **no cached value**, so calamine reads them back as `nan` (which is exactly what the golden captured); xlsxwriter writes a **cached 0**, so the same cells read back as `0.0`. Switching the writer therefore changes those cells `nan → 0` — a guaranteed golden mismatch. **Conclusion:** these writes are locked to openpyxl under the bit-identical constraint; do not re-attempt without an R1 sign-off. This is the practical performance floor.

**Initiative status: COMPLETE for the bit-identical constraint.** Final per-job speedups 2.3–8.6× (table above). Remaining time is intrinsic Excel output production (openpyxl formula writes + writing the required multi-sheet workbooks). Further gains would require relaxing bit-identical (R1 tolerance) or changing the output format/contract.
