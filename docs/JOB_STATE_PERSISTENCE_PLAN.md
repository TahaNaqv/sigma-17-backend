# Job / Wizard State Persistence & Resume — Implementation Plan

> **PHASE 1 — COMPLETE (2026-07-07).** All four wizards (Reserve Summary incl. the UW-parameters sub-flow, Update Reserves, Cash Flow Allocation, Movement Analysis) persist their full input state (step, dates, dataset picks, uploaded files, validation) to a scoped store + IndexedDB vault, and every job is tracked globally so it survives tab switches / reloads and reattaches on return. Scoped per (org, user); wiped on logout; reconciles on org switch. `tsc` 34 (all pre-existing `.error`/`.message` union pattern, none introduced), build + 36 tests pass.
>
> **PHASE 2 — COMPLETE (2026-07-07).** Server-side drafts for cross-device / cross-session resume. Backend: `processing.JobDraft` model (org+user scoped, unique per wizard key, 256 KB state cap) + `GET/PUT/DELETE /api/processing/job-drafts/` + migration `0004_jobdraft` (8 API tests pass). Frontend: `api/jobDrafts.ts`, `useDraftSync` (debounced autosave + non-destructive resume decision by `updatedAt`, re-reconciles on org switch, guards saves by scope-at-schedule so a debounce can't cross tenants), `ResumeDraftBanner`, wired into all four wizards. Uploaded file bytes stay device-local (refs won't resolve cross-device — documented). Frontend `tsc` 34 / build / 36 tests; backend 8 tests pass.
>
> **PHASE 3 — Reliability COMPLETE (2026-07-07); input convergence HANDED OFF.** Stuck-job watchdog: `processing/services/watchdog.py` `reap_stuck_jobs()` fails jobs stuck in `running`/`pending` past the Celery hard time limit (+5 min margin) via a status-guarded UPDATE (never clobbers a worker's finalisation); `reap_stuck_jobs_task` beat-scheduled every 15 min on the `retention` queue (5 tests pass). A reattached-but-dead run now resolves to `failed` (with an explanatory message the UI already surfaces) instead of polling forever. **Input convergence on Datasets (§6.1) is handed off to the Excel-free/Dataset initiative**, which already owns auto-Dataset-from-upload — not duplicated here. Optional per-step `progress` field deferred (cosmetic; would touch bit-identical engine paths).

**Status:** Phases 1–2 complete; Phase 3 reliability complete (Datasets convergence owned by the Dataset initiative)
**Scope:** All four processing wizards (Reserve Summary, Update Reserves, Cash Flow Allocation / IBNR, Movement Analysis)
**Repos:** `sigma-17-dashboard` (React/Vite/TS), `sigma-17-backend` (Django/DRF/Celery)
**Goal:** Enterprise-grade, production-ready state persistence so switching tabs (or reloading, or moving devices) never loses in-progress work.

---

## 1. Problem statement (client report)

> "Whenever we click on any other tab, our previous job vanishes/disappears — we have to start from scratch. It should be stored somewhere so we can resume where we finished."

### 1.1 Root-cause diagnosis (code-grounded)

"Tabs" are **React Router routes**. In [`App.tsx`](sigma-17-dashboard/src/App.tsx) every sidebar destination is a `<Route element={<DashboardLayout>…}>` with **no keep-alive / persistent `<Outlet>`**. Navigating between them **unmounts the entire page component**, and all its `useState` is destroyed.

Each wizard holds its full multi-step state in local `useState`. Example — [`SummaryGeneratorPage.tsx`](sigma-17-dashboard/src/pages/SummaryGeneratorPage.tsx) holds ~30 `useState` hooks: `step`, four dates (`expStart/expEnd/accBop/accEop`), uploaded `File[]` per kind (`premiumFiles`, `claimsPaidFiles`, `claimsOsFiles`), dataset-id selections, validation mappings (`*Validated`), `isProcessing`, `result`, and the running `jobId` (inside `result`). On unmount **every one of these is lost**, and remount re-initialises to defaults (`useState<Step>(1)`, `useState<File[]>([])`, …) → the wizard is back at step 1, empty.

Aggravating factors found in code:
- The status poll is bound to the component: `handleGenerate` runs `waitForModule1Job(jobId, { signal })` with an `AbortController` (`jobAbortRef`). On tab switch the poll is abandoned and the running `jobId` handle is gone — nothing can call `fetchModule1Job` to reattach ([`module1.ts:281`](sigma-17-dashboard/src/api/module1.ts)).
- [`UnsavedChangesWarning`](sigma-17-dashboard/src/components/UnsavedChangesWarning.tsx) only guards the browser `beforeunload` event — it does **nothing** for in-app React Router navigation.
- [`NotificationContext`](sigma-17-dashboard/src/contexts/NotificationContext.tsx) is in-memory `useState` — no way to learn a job finished while you were elsewhere.

### 1.2 What the backend already persists (so we don't rebuild it)

- Jobs are a single model, [`processing/models.py::Module1Job`](sigma-17-backend/processing/models.py), backing **both** engines. Async via **Celery + Redis**; a row is written at submit time (`status=pending`) and progresses `pending → running → success/failed`. Full submit-time config lives in `input_meta` (JSONField). Org+user scoped.
- Read/list/poll endpoints already exist ([`processing/urls.py`](sigma-17-backend/processing/urls.py)): `GET /api/module1/jobs/<uuid>/`, `GET /api/module2/jobs/<uuid>/`, `GET /api/processing/jobs/?page=…`, plus output preview + download. A **submitted job is already durable and re-fetchable** — the frontend just discards the handle.

### 1.3 The two distinct problems

| | Pre-submit wizard state | Submitted / running job |
|---|---|---|
| What | dates, file picks, dataset ids, validations, current step | the server-side `Module1Job` run |
| Lives in | ephemeral `useState` | Postgres (Celery) |
| Lost on tab switch? | **Yes — destroyed** | No (keeps running) — but the UI loses the handle |
| Fix needed | persist form state (client, then server) | reattach + globally track |

---

## 2. Goals, non-goals, constraints

**Goals**
- G1. Switching tabs and returning restores the wizard exactly (step, dates, selections, validations, uploaded files).
- G2. A job that is running when you leave keeps being tracked; you're notified on completion from anywhere; returning to the module reattaches to it.
- G3. Full page reload (same browser) restores the same.
- G4. (Enterprise) Resume from another device / session.
- G5. All four wizards covered by one shared pattern.

**Non-goals**
- Real-time collaborative editing of a wizard across users.
- Resuming a *raw file upload* across devices (a browser `File` cannot be serialised server-side without uploading it; see §6).

**Constraints**
- **Tenant isolation:** all persisted client state must be namespaced by (user, active org) and cleared on logout / org switch. Mirrors the `sigma17_*` + `X-Organization-Id` model in [`client.ts`](sigma-17-dashboard/src/api/client.ts).
- **Bit-identical processing** must be unaffected (persistence is UI/orchestration only).
- No data-router migration required for the core fix (see §4.3).
- Additive backend migrations only; no change to existing job semantics.

---

## 3. Target architecture (layered)

```
┌─────────────────────────────────────────────────────────────┐
│  App (QueryClientProvider ▸ Auth ▸ Notification ▸ Rbac)      │
│  + ActiveRunsProvider   ← NEW (global, above the router)     │
│      • tracks in-flight/finished jobs across all modules     │
│      • one react-query poll per active jobId (survives nav)  │
│      • fires completion → NotificationContext + toast        │
│      • persisted (localStorage, namespaced by user+org)      │
│  + RunsIndicator (header badge)  ← NEW                       │
└───────────────┬─────────────────────────────────────────────┘
                │
     ┌──────────┴───────────┐
     │  Wizard pages         │   each backed by:
     │  (Summary, Update,    │   • useWizardStore(key)  ← NEW Zustand slice
     │   Ibnr, Movement)     │       - scalars/step/selections → localStorage
     │                       │       - uploaded File blobs → IndexedDB (idb-keyval)
     │                       │   • useActiveRun(key)  ← subscribes to global poll
     └───────────────────────┘
                │  (Phase 2)
     ┌──────────┴───────────┐
     │  Backend: JobDraft    │   POST/GET/PUT/DELETE /api/processing/job-drafts/
     │  (org+user scoped,    │   • debounced autosave of wizard state (no files)
     │   JSON state bag)     │   • "Resume?" banner on mount
     └───────────────────────┘
```

Three layers, shipped in phases; nothing is thrown away between phases.

---

## 4. Phase 1 — Frontend persistence (fixes the literal complaint; no backend deploy)

### 4.1 Wizard state store (`useWizardStore`)

**New dependency:** `zustand` (+ its `persist` middleware) and `idb-keyval` (tiny IndexedDB helper for File blobs). Both are small, well-maintained, tree-shakeable.

**New files**
- `src/state/wizardStore.ts` — a factory that creates one persisted slice per wizard `key`.
- `src/state/fileVault.ts` — thin wrapper over `idb-keyval` for storing/reading `File`/`Blob` objects by id, namespaced by user+org.
- `src/state/storageKeys.ts` — builds namespaced keys: `sigma17:wizard:{orgId}:{userId}:{key}`.

**Store shape (per wizard) — Reserve Summary example**
```ts
interface SummaryWizardState {
  step: 1 | 2 | 3;
  // dates persisted as ISO strings; hydrated back to Date in the page
  expStart?: string; expEnd?: string; accBop?: string; accEop?: string;
  // dataset selections (already serialisable)
  premiumDatasetIds: string[]; claimsPaidDatasetIds: string[]; claimsOsDatasetIds: string[];
  // uploaded files: store *references*, blobs live in IndexedDB
  premiumFileRefs: FileRef[];      // { id, name, size, type }
  claimsPaidFileRefs: FileRef[];
  claimsOsFileRefs: FileRef[];
  // validation metadata (serialisable; excludes the raw File)
  premiumValidated: ValidatedMeta[]; /* … */
  existingCsInput: CombinedSummaryInputValue;
  // active run handle (lets us reattach even at page level)
  activeJobId?: string;
  updatedAt: string;
}
```

**Mechanics**
- `persist` middleware writes the serialisable slice to `localStorage` under the namespaced key. A `partialize` drops transient fields (`isProcessing`, timers) that should not survive.
- Uploaded `File` objects are **not** put in localStorage (too big / not serialisable). On file selection we `fileVault.put(id, file)` into IndexedDB and keep only a `FileRef` in the store. On rehydrate, the page calls `fileVault.get(id)` to reconstruct `File[]`. (`File`/`Blob` are structured-cloneable, so IndexedDB stores them natively.)
- **Migration/versioning:** `persist` `version` + `migrate` so shape changes don't crash old clients.
- **Reset** (`handleReset` in each page) clears the slice *and* `fileVault.delete(...)` the blobs.

**Page refactor pattern (applies to all four wizards)**
1. Replace the pile of `useState` with a single `const w = useSummaryWizard()` selector-based store hook. Keep local `useState` only for truly transient UI (spinner, timer, dialog open).
2. On mount, hydrate `File[]` from `fileVault` using the stored refs (async `useEffect`).
3. Every setter writes through the store instead of local state.
4. `handleReset` → `store.reset()` + vault cleanup.

> Net effect: switching tabs and returning restores step, dates, dataset picks, validations **and** uploaded files — same browser, and across full reloads.

### 4.2 Global run manager (`ActiveRunsProvider`)

**New files**
- `src/contexts/ActiveRunsContext.tsx` — provider mounted in `App.tsx` directly under `NotificationProvider` (so it can call `addNotification`).
- `src/components/RunsIndicator.tsx` — a header badge showing "N running"; click → dropdown of active runs with links back to their module.

**State**
```ts
interface TrackedRun {
  runKey: string;         // e.g. "summary" — the wizard key
  module: "module1" | "module2";
  jobId: string;
  jobType: Module1JobDto["job_type"];
  status: "pending" | "running" | "success" | "failed";
  label: string;          // jobTypeLabel(jobType)
  startedAt: string;
  route: string;          // where to send the user on click
}
```
- Persisted to `localStorage` (namespaced) so a reload re-attaches to still-running jobs.
- Polling: for each non-terminal run, a `useQuery`/`useQueries` with `refetchInterval` (reuse the backoff idea from `waitForModule1Job`, or a fixed 2.5s while mounted) calling `fetchModule1Job` / `fetchModule2Job`. **Because the provider is above the router, the poll survives tab switches.**
- On transition to `success`/`failed`: fire `addNotification(...)` + `toast`, and update the run so the originating page (if mounted) reacts.

**API surface**
```ts
const { startTracking, runs, getRun, clearRun } = useActiveRuns();
```
- `handleGenerate` calls `startTracking({ runKey, module, jobId, jobType, route })` right after the job is created, then stops running its own `waitForModule1Job` loop — it subscribes to `getRun(runKey)` instead (or a `useActiveRun(runKey)` convenience hook that returns the live status + terminal job).

**Result:** start a run → switch tabs freely → global badge shows it running → toast + bell notification when it finishes → returning to the module shows the finished result (reattached), not an empty wizard.

### 4.3 Navigation guard (optional, downgraded)

Because state is now persisted, leaving a wizard is **safe** — the guard is no longer required to prevent data loss. Keep `UnsavedChangesWarning` for the browser-close case (unchanged). If the client still wants an explicit in-app "you have an in-progress job" prompt, that requires migrating `App.tsx` from `BrowserRouter` to `createBrowserRouter` (data router) to unlock `useBlocker`. **Recommendation: defer** — treat as a small optional follow-up, not part of the core fix.

### 4.4 Security / lifecycle (Phase 1)
- Namespace every persisted key by `{activeOrgId}:{userId}` (read from `getActiveOrgId()` + auth context).
- On logout (`clearTokens`) and on **org switch**, clear all wizard slices + the file vault + active-runs store for the previous scope. Add this to `AuthContext` logout and the org-switch handler.
- `partialize` ensures no auth tokens or PII beyond what the user typed is persisted.

### 4.5 Phase 1 deliverables (all four wizards)
- [ ] `zustand`, `idb-keyval` added; `wizardStore.ts`, `fileVault.ts`, `storageKeys.ts`.
- [ ] `ActiveRunsProvider` + `RunsIndicator` wired into `App.tsx` and the header.
- [ ] `module2.ts` reattach parity (`fetchModule2Job` used by provider).
- [ ] Refactor `SummaryGeneratorPage`, `UpdateReservePage`, `IbnrAllocationPage`, `MovementAnalysisPage` to the store + `useActiveRun` pattern.
- [ ] Logout / org-switch cleanup hooks.
- [ ] Tests (see §8).

**Outcome:** the client's literal complaint is fully resolved for the same-browser case, including uploaded files, across tab switches and reloads. **Zero backend changes, zero deploy risk on the Django side.**

> **Phase 1 delivered (2026-07-07).** All four wizards refactored; Summary UW sub-flow persisted + tracked (`runKey "summary-uw"`); IBNR tracks both allocate + process jobs and reattaches the ULR fetch on return. Added `src/state/{scope,fileVault,wizardStore,usePersistedFiles}.ts`, `src/state/wizards/{summary,updateReserve,movement,ibnr}.ts`, `ActiveRunsContext`, `RunsIndicator`; `useVaultedFile` for single-file inputs; `initialValidatedFiles` seeding on FileUploadZone/DatasetSourceInput and `initialOverrides` on ReserveCdfEditor. File hooks **reconcile** on org/scope switch (no cross-tenant file leak). Persistence test in `wizardStore.test.ts` caught + fixed a draft-clobber-on-scope-switch bug.

---

## 5. Phase 2 — Server-side drafts (cross-device / enterprise durability)

### 5.1 New model — `JobDraft` (in `processing` app)
```python
class JobDraft(models.Model):
    class Key(models.TextChoices):
        SUMMARY        = "summary", "Reserve Summary"
        UPDATE_RESERVE = "update_reserve", "Update Reserves"
        IBNR_ALLOCATE  = "ibnr_allocate", "Cash Flow Allocation"
        MOVEMENT       = "movement", "Movement Analysis"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="job_drafts")
    organization = models.ForeignKey("tenants.Organization", on_delete=models.CASCADE,
                                     related_name="job_drafts", db_index=True)
    key = models.CharField(max_length=32, choices=Key.choices)
    state = models.JSONField(default=dict, blank=True)   # serialisable wizard state (NO files)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "organization", "key"],
                                    name="uniq_draft_per_user_org_key"),
        ]
```
One draft per (user, org, wizard) — "resume where I left off." Additive migration.

### 5.2 Endpoints (DRF) — mounted in `processing/urls.py`
- `GET  /api/processing/job-drafts/?key=summary` → the draft or `204`.
- `PUT  /api/processing/job-drafts/` → upsert by `key` (body: `{key, state}`).
- `DELETE /api/processing/job-drafts/?key=summary` → on reset/submit.

Scope with the existing `_scope_jobs_qs`-style filter (org from `get_request_org`, user = request.user). Reuse `IsAuthenticated` + the module run permission. Serializer validates `key` and caps `state` size (e.g. reject > 256 KB) to prevent abuse.

### 5.3 Frontend integration
- The Zustand `persist` middleware gains a **remote sync**: on any state change, debounce ~1s and `PUT` the serialisable slice to the draft endpoint (never files). localStorage remains the fast local cache; the server is the cross-device source of truth.
- On wizard mount: `GET` the draft. If it exists and is newer than the local copy, show a **non-destructive resume banner**: *"You have an in-progress Reserve Summary from 2h ago — Resume / Discard."* Avoid silent auto-fill (surprising). If local copy is present and matches, hydrate silently.
- On submit success and on explicit reset → `DELETE` the draft.
- **Files caveat (documented in UI):** cross-device resume restores everything except locally-uploaded files (they never left the origin browser). The banner notes "re-select data files" when file refs exist but blobs are absent. Phase 3 removes this caveat by moving inputs to Datasets.

### 5.4 Phase 2 deliverables
- [ ] `JobDraft` model + migration.
- [ ] Serializer + views + URLs + tests (org/user scoping, size cap).
- [ ] Debounced remote autosave in the store; resume banner component; delete-on-submit/reset.

---

## 6. Phase 3 — Input durability (Datasets) + reliability hardening

### 6.1 Converge inputs on Datasets
The only thing that cannot be truly resumed cross-device is a **raw `File`** (browser-memory only). The `datasets` app already persists uploaded data server-side (import-excel → DRAFT dataset with typed rows) and the wizards already accept `*_dataset_ids` alongside file uploads ([`SummaryGeneratorPage` step 2](sigma-17-dashboard/src/pages/SummaryGeneratorPage.tsx)). Steering users to pick/auto-create **Datasets** instead of raw uploads makes a draft `state` fully rehydratable anywhere (it only needs `datasetId`s). This is exactly the direction of the existing **Excel-free / Dataset initiative** — convergence, not new scope.
- Optional UX: when a user drops an Excel file, auto-create a DRAFT Dataset (already supported by `/api/datasets/import-excel/`) and store the returned `datasetId` in the draft, instead of holding the `File`.

### 6.2 Reliability: stuck-job reaper + optional progress
- **Reaper (production hardening):** today a worker that dies mid-run leaves a job stuck at `running` forever (no heartbeat). Add a Celery-beat periodic task on the `retention`/`default` queue that marks jobs `running` with `started_at` older than the hard time limit (Celery `task_time_limit`, currently 1h) as `failed` with an explanatory `error_message`. Prevents the UI from polling a dead job indefinitely.
- **Optional progress field:** add `progress` / `current_step` to `Module1Job` (or a side table), updated in `processing/tasks.py` between natural engine phases. Surface a determinate progress bar in the run indicator instead of an indeterminate spinner. Nice-to-have; not required for the core fix.

### 6.3 Phase 3 deliverables
- [ ] Auto-Dataset-on-upload option; wizards prefer dataset ids in drafts.
- [ ] Celery-beat reaper task + config + test.
- [ ] (Optional) `progress` field + task instrumentation + UI bar.

---

## 7. API & data contracts (summary)

| Concern | Endpoint / store | Phase |
|---|---|---|
| Reattach to a run | existing `GET /api/module1|2/jobs/<uuid>/` | 1 |
| List running jobs | existing `GET /api/processing/jobs/?page=` | 1 (indicator) |
| Wizard draft read | `GET /api/processing/job-drafts/?key=` | 2 |
| Wizard draft upsert | `PUT /api/processing/job-drafts/` | 2 |
| Wizard draft delete | `DELETE /api/processing/job-drafts/?key=` | 2 |
| Local form cache | localStorage `sigma17:wizard:{org}:{user}:{key}` | 1 |
| Local file blobs | IndexedDB via `idb-keyval`, same namespace | 1 |
| Stuck-job reaper | Celery-beat periodic task | 3 |

---

## 8. Testing strategy

**Frontend (vitest + testing-library)**
- Store: set → persist → rehydrate restores identical state; `partialize` excludes transient fields; version `migrate` path.
- File vault: put/get/delete round-trips a `File`; namespace isolation between orgs.
- Provider: a run transitions running→success fires exactly one notification; poll continues across simulated route change (render provider, unmount page, assert poll still fires).
- Security: switching org clears the previous scope's keys; logout wipes everything.
- Per-wizard integration: fill step 2, navigate away (unmount), remount → step/dates/files restored.

**Backend (pytest)**
- `JobDraft` scoping: user A/org X cannot read user B or org Y drafts; upsert respects unique constraint; size cap rejects oversized `state`.
- Reaper: a job stuck `running` past the limit is marked `failed`; a fresh running job is untouched.

**Manual QA (client scenario):** start a Reserve Summary, fill dates + upload files, switch to Movement Analysis, come back → everything intact; submit a job, switch tabs, get the completion toast, return → result shown.

---

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Cross-tenant leakage on shared machine | Namespace all client storage by org+user; wipe on logout/org-switch (§4.4). |
| IndexedDB quota / large files | Cap total vaulted bytes; evict oldest on reset; Phase 3 moves inputs to Datasets. |
| Stale draft auto-loads wrong data | Non-destructive **resume banner**, never silent auto-fill (§5.3). |
| Draft `state` schema drift | `persist` `version` + `migrate`; backend stores opaque JSON (no server-side schema coupling). |
| Reaper marks a slow-but-alive job failed | Threshold = Celery hard time limit + margin; only reaps beyond the limit the worker itself enforces. |
| Scope creep into data-router migration | Nav-guard explicitly deferred (§4.3); core fix needs no router change. |

---

## 10. Sequencing & rollout

1. **Phase 1** (frontend-only) → ships behind no flag needed; solves the client complaint. Roll out one wizard first (Reserve Summary) to validate the store+provider pattern, then apply to the other three in the same PR series.
2. **Phase 2** (backend `JobDraft` + autosave) → additive migration; deploy backend, then enable remote sync in the store.
3. **Phase 3** (Datasets convergence + reaper) → folds into the Excel-free/Dataset initiative and closes reliability gaps.

**Feature flags:** gate remote autosave (Phase 2) and auto-Dataset (Phase 3) behind env flags so frontend can ship ahead of backend.

---

## 11. File-touch checklist

**Frontend — new**
- `src/state/wizardStore.ts`, `src/state/fileVault.ts`, `src/state/storageKeys.ts`
- `src/contexts/ActiveRunsContext.tsx`, `src/components/RunsIndicator.tsx`
- `src/components/ResumeDraftBanner.tsx` (Phase 2)

**Frontend — modified**
- `src/App.tsx` (mount `ActiveRunsProvider`)
- `src/pages/SummaryGeneratorPage.tsx`, `UpdateReservePage.tsx`, `IbnrAllocationPage.tsx`, `MovementAnalysisPage.tsx`
- `src/contexts/AuthContext.tsx` (clear stores on logout/org-switch)
- `src/api/module2.ts` (ensure `fetchModule2Job` parity for the provider)
- header/layout component (mount `RunsIndicator`)

**Backend — new (Phase 2/3)**
- `processing/models.py` (`JobDraft`, optional `progress`), migration(s)
- `processing/serializers.py` (`JobDraftSerializer`), `processing/views.py` (`JobDraftView`), `processing/urls.py` (routes)
- `processing/tasks.py` (reaper task + optional progress instrumentation), Celery-beat schedule in `config/`

---

## 12. Recommendation

Ship **Phase 1 across all four wizards first** — it fully resolves the reported problem (tab switches, reloads, and running-job tracking, including uploaded files) with no backend deploy risk. Then layer **Phase 2** for cross-device durability and **Phase 3** to make inputs fully portable (Datasets) and the pipeline self-healing (reaper). Every phase builds on the last; nothing is rework.
