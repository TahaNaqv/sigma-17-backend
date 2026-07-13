# IFRS 17 Movement Analysis — Input Reuse Plan

> **Goal:** Stop the *Movement Analysis* page from re-collecting **Previous Period**
> and **Expense CF** that the *Cash Flow Allocation* step already captured. A movement
> disclosure should be generated from a completed Cash Flow Allocation (process) job
> without re-entering its inputs.

Status: planned (2026-07-13). Companion to `docs/IFRS17_MOVEMENT_PLAN.md`.

---

## 0. Client requirement

> "In IFRS 17 Workflow we are already taking information in the Cash Flow Allocation tab
> for Previous period and Expense CF; it's asking again for the same information in the
> Movement Analysis tab. Ideally it should not ask the same information twice."

Confirmed accurate. The two pages keep independent state and the movement API is a near-copy
of the process API, so Previous Period + Expense CF (and accounting period + ULR selection)
are collected twice.

## 1. Why reuse is sound

Both `module2_process` and `module2_movement` feed the **identical** engine call
`_process_intermediates(combined, previous, expense, accounting_period, selected_ulr)`
(`module2_engine/engine.py:788`, called at `:858` and `:965`). The movement workbook is a
pure re-projection of the frames the process job already builds
(`module2_engine/movement/compute.py::build_sama_movement`). So a completed process job
already holds everything the movement disclosure needs for Previous Period + Expense CF.
`docs/IFRS17_MOVEMENT_PLAN.md` §10 already lists "chaining off `module2_process`" as intended
follow-up.

Two side benefits of chaining off the process job:
- **ULR selection is inherited.** Today the Movement page sends an *empty* `selected_ulr`
  (`MovementAnalysisPage.tsx` `handleRun` never passes `selectedUlr`), so movement silently
  ignores ULR selections. Inheriting the process job's `selected_ulr` fixes this.
- **Accounting period is inherited**, removing another duplicated field.

## 2. The crux: uploaded inputs are ephemeral

- **File uploads do not survive the job.** The process task stages
  `Previous_Period.xlsx` / `Expense_CF.xlsx` under the job root, then calls
  `_cleanup_root(job)` in its `finally` (`processing/tasks.py:505`), deleting them. Only
  `input_meta["files"]` metadata (name/size) survives — not the bytes.
- **Dataset inputs do survive.** `DatasetSnapshot.rows_payload` persists in the DB and
  outlives the job (`datasets/models.py:489`).

Therefore a production-grade reuse must make the process job's inputs durably reusable
**regardless of whether they were supplied as uploads or datasets**. A design that only reads
the process job's staged files would work for dataset-fed jobs and silently break for
upload-fed ones.

## 3. Chosen design

**Movement Analysis chains off a completed Cash Flow Allocation (process) job and inherits
its inputs. Each process/movement job persists the exact canonical input workbooks it
consumed into a dedicated `input_archive`, so reuse is byte-identical and uniform across
upload- and dataset-fed jobs.**

### Why byte-reuse over auto-Dataset conversion

| | Byte-reuse (chosen) | Auto-convert uploads → Datasets |
|---|---|---|
| Determinism / bit-identical output | Reuses exact consumed bytes — safest | Round-trips through row parse + re-serialize — risk |
| New parsing code | None | New xlsx→typed-row parser for 3 kinds |
| Uniform for upload & dataset | Yes | Yes |
| Advances Excel-free initiative | No | Yes (do separately, later) |

Byte-reuse is lower-risk and honors the bit-identical constraint from the performance
initiative. Auto-Dataset conversion remains worthwhile but belongs to the Dataset initiative,
independent of this fix.

### Why `input_archive` and not the output zip

The download endpoint streams `output_zip` verbatim (`processing/views.py:1154`) and the
preview lists it (`list_preview_files`). Putting inputs there would leak them into user
downloads/preview or force a re-zip on every download. A dedicated, nullable
`input_archive` FileField on `Module1Job` is durable, retention-aware, and completely
invisible to the user-facing output surface.

### Data flow

```
Cash Flow Allocation (module2_process job)
  ├─ output_zip:    Module2_Final_Output.xlsx          (unchanged, user-facing)
  └─ input_archive: Previous_Period.xlsx, Expense_CF.xlsx   (NEW, durable, non-user-facing)
        │
        │  Movement Analysis selects this process job (process_job_id)
        ▼
module2_movement job
  • source_job = process_job.source_job (the allocate) → Combined_Summary   (unchanged read)
  • reads Previous_Period.xlsx + Expense_CF.xlsx from process_job.input_archive
  • inherits accounting_period + selected_ulr from process_job.input_meta
  • user supplies ONLY movement-specific inputs: reporting date, scope, RI overrides
```

## 4. Backend changes (sigma-17-backend)

1. **Model + migration.** Add nullable `input_archive = models.FileField(...)` to
   `Module1Job` (`processing/models.py`). Wire it into retention purge alongside
   `output_zip` (wherever `output_purged_at` / purge clears the output file).
2. **Persist canonical inputs.** In `run_module2_process_task`
   (`processing/tasks.py:459`) and `run_module2_movement_task` (`:509`), after
   `_materialize_job_snapshots` and after reading `previous_path` / `expense_path`, zip those
   two workbooks and save to `job.input_archive`. This captures exactly what the engine
   consumed, whether upload- or dataset-fed.
3. **Generalize the reader.** Add `read_zip_member(zip_field_file, member) -> bytes` in
   `processing/services/source_resolver.py` (extract the core of `read_artifact_bytes`), so a
   caller can read a member from any job's `input_archive` with the same expired/missing
   guards.
4. **Movement view: `process_job_id` inherit/override**
   (`Module2MovementJobView`, `processing/views.py:1449`):
   - New optional `process_job_id`. When present: resolve the completed `module2_process`
     job (org-scoped, `status=SUCCESS`, `job_type=MODULE2_PROCESS`). Set movement's
     `source_job = process_job.source_job` (the allocate) for Combined_Summary. Record
     `input_meta["process_job_id"]`.
   - Make `previous_period` / `expense_cf` / the three dataset ids / `accounting_period`
     optional when `process_job_id` is given; any supplied value **overrides** the inherited
     one (per slot). Keep the existing XOR validation only when *not* inheriting.
   - When absent: behavior is byte-for-byte unchanged (backward-compatible).
5. **Serializer.** Add optional `process_job_id` (UUID) and relax `accounting_period` to
   optional in the movement path — either a movement-specific serializer or a `partial`
   variant so the process view's contract is untouched (`processing/serializers.py:172`).
6. **Movement task: read inherited inputs.** In `run_module2_movement_task`, when
   `input_meta["process_job_id"]` is set and the local staged files/snapshots are absent,
   read `Previous_Period.xlsx` + `Expense_CF.xlsx` from the process job's `input_archive`
   via `read_zip_member`, and pull `accounting_period` / `selected_ulr` from the process
   job's `input_meta` when the movement job didn't override them.
7. **Source candidates.** Let the frontend list completed `module2_process` jobs — reuse the
   existing `GET /api/module2/jobs/?page=…` list (filter client-side to
   `job_type == "module2_process" && status == "success"`), or extend `SourceCandidatesView`.

### Edge cases

- Process job failed / purged / expired → clear error via the `read_zip_member` guards
  (mirrors today's Combined_Summary behavior).
- User overrides one slot but not the other → per-slot inheritance.
- Upload-fed vs dataset-fed process job → identical downstream (canonical bytes persisted).
- Tenant isolation preserved — all resolves are org-scoped.
- Old process jobs created before this change have no `input_archive`; movement reuse of
  those must fail gracefully with a "re-run allocation or supply inputs" message, and the
  override fields remain available as the fallback.

## 5. Frontend changes (sigma-17-dashboard)

1. **Source selector → process job** (`MovementAnalysisPage.tsx:251-272`): list completed
   *Cash Flow Allocation (process)* jobs; send `process_job_id`. Resolve the allocate via the
   process job's `source` to keep the Scope/ULR class chips populated
   (they derive from allocate ULR rows).
2. **Collapse duplicated inputs** (`:340-395`): hide Previous Period + Expense CF under an
   **"Override inputs (optional)"** disclosure; default (collapsed) = reuse from the selected
   job. Keeps the escape hatch.
3. **Keep movement-specific inputs primary:** reporting (closing) date, scope, RI overrides.
4. **API + store:** add `processJobId` to `startModule2MovementJob` (`api/module2.ts:157`)
   and `MovementWizardData` (`state/wizards/movement.ts`); make prev/expense params
   conditional; bump the persisted store `version`.

## 6. Testing & rollout

- **Backend unit:** movement view inherit-vs-override branches; `input_archive` populated on
  process success; a movement job chained off an *upload-fed* process job produces
  byte-identical frames to the standalone run (determinism); `input_archive` never appears
  in output preview / download / `output_artifacts`.
- **Frontend:** collapsed-by-default UX; override still works; scope chips populate from the
  process job's allocate.
- **/verify:** run Cash Flow Allocation → Movement Analysis without re-entering inputs →
  confirm disclosure matches the standalone path.
- **Backward compatibility:** the `process_job_id`-less path is unchanged; in-flight drafts
  migrate via the store version bump.

## 7. Out of scope (future)

- Auto-Dataset from uploaded Previous Period / Expense CF (Excel-free initiative).
- Chaining the *process* job itself off a prior process job.
