# Session Notes

Session notes and decisions from development work on Double Edge.

---

## 2026-03-12 — Worktree Stabilization, Smart Lists Coverage, And Geospatial Closure

### Objective
Stabilize the mixed Mar 12 worktree into validated workstreams, finish the geospatial/map-provenance tranche properly, and leave canonical entity work explicitly deferred until the tree is reviewable.

### What Changed
1. **Worktree stabilization pass**
   - Re-audited the active diff into separate workstreams: backend lead contracts, Leads UX, Smart Lists, geospatial/map provenance, and docs/meta.
   - Kept canonical entity work out of this tranche on purpose.
2. **Backend/runtime validation**
   - Re-ran focused contract/runtime coverage for lead generation, batch endpoints, and lead contact/detail behavior.
   - Confirmed the current lead runtime/data-contract changes are green before doing more frontend/docs work.
3. **Smart Lists validation**
   - Added focused component regression coverage for Smart List authoring and the open-in-leads handoff:
     - `frontend/components/SmartListsPage.test.tsx`
   - Locked in filter payload construction and query-param translation from saved list filters.
4. **Geospatial closure**
   - Hardened `backend/src/services/building_geocode.py` with bounded retries/backoff and stricter lat/lon validation.
   - Hardened `backend/src/tasks/ingest.py` `building_coordinates` backfill with explicit throttle pacing and tighter progress cadence.
   - Updated `frontend/components/PortfolioMap.tsx` so normal display now defaults to persisted coordinates and omits unmapped buildings instead of browser-primary geocoding.
   - Added backend regression coverage for the coordinate job + jobs API fallback:
     - `backend/tests/test_building_coordinate_job.py`
     - expanded `backend/tests/test_building_geocode_service.py`

### Validation
- Backend focused runtime suite:
  - `backend/tests/test_normalization_contract.py`
  - `backend/tests/test_lead_batch_endpoints.py`
  - `backend/tests/test_lead_generation_runtime.py`
  - `backend/tests/test_lead_contacts_endpoint.py`
- Backend geospatial suite:
  - `backend/tests/test_building_geocode_service.py`
  - `backend/tests/test_building_coordinate_job.py`
  - `backend/tests/test_quality_source_audit_contract.py`
  - `backend/tests/test_jobs_contract.py`
- Frontend:
  - `npm run test:run -- components/LeadKanban.test.tsx`
  - `npm run test:run -- components/SmartListsPage.test.tsx`
  - `npm run build`

### Decisions Locked In
- **Normal map UX should prefer persisted coordinates** — stored coordinates are now the default path for portfolio/building maps.
- **Missing coordinates should be operationally fixed, not silently approximated** — operators are pointed to the coordinate sync job instead of relying on browser geocoding during normal display.
- **Canonical merge/dedupe work remains deferred** until the stabilized slices are independently reviewable and the next tranche is narrowed to canonical-prep only.

### Next Recommended Tranche
- Full hardening sweep across:
  - jobs chain behavior
  - source completeness gaps
  - slow query/performance checks
  - external-service error handling
- Then start a **canonical-prep** tranche:
  - ambiguous-tail classifier
  - merge safety metrics
  - audit/rollback surfaces

---

## 2026-03-06 — Production Lead Jobs & Conservative Cleanup

### Objective
Fix the broken production `lead_generation` path, run the attached conservative cleanup plan end-to-end, and verify live jobs/data-quality behavior before pushing.

### What Changed
1. **Runtime-safe lead generation**
   - Moved shared lead-generation logic into `backend/src/services/lead_generation.py`.
   - Left `backend/scripts/generate_leads_from_buildings.py` as a thin wrapper.
   - Updated `backend/src/tasks/generate_leads.py` and tests to stop depending on `scripts...` imports in worker runtime.
2. **Conservative orphan cleanup tooling**
   - Added `backend/src/services/lead_cleanup.py`.
   - Added CLI/admin entrypoints for preview + execution.
   - Added a phase-two retire-only classifier for zero-link leads, but kept thresholds strict enough to preserve ambiguous rows.
3. **Integrity metrics split**
   - `backend/src/routers/quality.py` now distinguishes:
     - multiple current links, any role
     - multiple current links in the same role
     - multiple current links to the same normalized entity
4. **Production-only hardening discovered during rollout**
   - Fixed `data_quality_log` sequence drift in `backend/src/tasks/ingest.py`.
   - Fixed `quality_checks` timezone math and `change_alerts.dismissed` insert behavior in `backend/src/tasks/quality_checks.py`.

### Production Operations Executed
- Deployed backend API service to Railway.
- Deployed dedicated Railway worker service with backend-root Docker build (`railway up . --path-as-root --service hpd-leads-worker`).
- Triggered and verified:
  - `lead_generation` job `44` -> `succeeded`
  - `lead_reconciliation` job `45` -> `succeeded`
  - `quality_checks` job `48` -> `succeeded`
- Ran conservative orphan cleanup preview + execute in production.

### Outcome
- Production job path now works through the normal worker/runtime flow.
- The conservative cleanup correctly made **no deletions**:
  - `safe_orphan_with_clear_keeper = 0`
  - `safe_orphan_retire_only = 0`
- This means the remaining orphan/blank tail is still ambiguous under a high-confidence rule set and should not be bulk-retired yet.

### Current Live Snapshot
- `179,985` buildings
- `314,723` leads
- `55,804` leads with zero active building links
- `54,507` blank display-name leads
- `106,721` buildings with multiple current links
- `0` buildings with multiple current links to the same normalized entity

### Key Spot Check
- `9 PROSPECT PARK WEST` still shows expected sourced contacts in production, including `SAMUEL WASSEMAN` and `LOUISE HAINLINE`, with `dos_contacts_status = loaded`.

---

## 2026-02-25 — Production Go-Live Completion (End-to-End)

### Objective
Ship the critical PM surfacing fix to production, reconcile live data snapshots, and complete full runtime readiness (API + frontend + queue worker).

### Production Access
- **Frontend (prod):** `https://frontend-nine-psi-58.vercel.app`
- **Backend API (prod):** `https://hpd-leads-app-production.up.railway.app`
- **Repository:** `https://github.com/michaelbloom01/hpd-leads-app`

### What Was Deployed
1. **Leads filter reliability hotfix**
   - `backend/src/routers/leads.py`
   - Fixed comma-delimited multi-select handling (`entity_type`, `pipeline_stage`, `enrichment_status`, `outreach_status`).
   - Fixed boolean filter semantics for `has_phone/has_email/has_website`.
   - Added snapshot sync guard for `portfolio_size` / `total_units` when unit/portfolio filters are applied.
2. **Worker runtime support**
   - `backend/start.py`, `backend/Dockerfile`
   - Added startup mode switch (`WORKER_MODE`) so one container image can run API or Celery worker.
   - Added worker-mode HTTP health endpoint for Railway health checks.
   - Added worker-aware container healthcheck behavior.

### Production Operations Executed
- Deployed backend to Railway and frontend to Vercel.
- Ran one-time production data repair:
  - `POST /api/admin/recompute-lead-portfolio`
  - `POST /api/admin/recompute-lead-units`
- Reconciled stale running jobs:
  - `POST /api/v1/jobs/reconcile-stale-running?dry_run=true`
  - `POST /api/v1/jobs/reconcile-stale-running?dry_run=false`
- Provisioned Redis service and configured backend `REDIS_URL`.
- Created and deployed dedicated Railway worker service (`hpd-leads-worker`).

### Final Validation (Production)
- `GET /api/health` -> `ok`
- Auth login -> `ok`
- Leads list loads (`~38k` leads in live DB)
- Critical PM filter now returns results:
  - `min_units=50,max_units=250,min_portfolio=2,max_portfolio=20`
  - non-zero results confirmed in production (~6.7k)
- Lead detail endpoint loads successfully from filtered result.
- Worker health now green:
  - `GET /api/v1/jobs/worker-health` -> `status=ok`, `broker_configured=true`, `celery_ping_ok=true`, `stale_running_jobs=0`

### Outcome
The project is live end-to-end in production with:
- stable data pulls for PM surfacing filters,
- repaired lead unit snapshots,
- and fully operational queue/worker infrastructure.

---

## 2026-02-24 — HPD Branding Removal & Production Hardening

### User-Reported Issues Addressed
1. **"HPD Leads" and "Connected to HPD" still visible** — Implemented full HPD branding removal plan across 11 files
2. **Agent chat timeout** — "No response after 90 seconds" — cold-start / backend startup latency
3. **Data health dashboard** — Review requested; page uses `fetchDataHealth()` from quality API
4. **Dashboard pipelines** — PM company pipeline vs building pipeline should be clearly separate; PM companies moved through deal pipeline no longer showing
5. **Lead filter bug** — 50–250 units, 2–20 buildings returning no results (root cause: total_units backfill)
6. **Search toggle UX** — PM/address flip not intuitive; redesigned as segmented control
7. **"Website" unclear** — Relabeled to "Company Website" / "Has Website"

### Work Completed This Session
- **Remove Remaining HPD Branding plan** — All 11 files updated
- **Production hardening** (prior session): Auth on all endpoints, rate limiting, input validation, accessibility, error handling, form validation, memory leak fixes

### Key Decisions
- **"Connected"** → **"API connected"** — Clarifies status without referencing HPD
- **"HPD Violations"** → **"Housing Violations"** — Data is from NYC HPD but label is user-friendly
- **"HPD Litigation"** (signal type) → **"Housing Litigation"** — Consistent with violations relabel
- **package name** — `hpd-leads-frontend` → `double-edge-frontend` for build artifacts

### Files Modified (HPD Branding)
- `frontend/components/Sidebar.tsx`
- `frontend/components/Header.tsx`
- `frontend/metadata.json`
- `frontend/components/Dashboard.tsx`
- `frontend/App.tsx`
- `frontend/components/LeadDetail.tsx`
- `frontend/components/LeadTable.tsx`
- `frontend/components/BuildingDetailPage.tsx`
- `frontend/components/SettingsPage.tsx`
- `frontend/package.json`
- `frontend/package-lock.json`

### Notes for Next Session
- If user still sees "HPD Leads" or "Connected to HPD": hard refresh (Ctrl+Shift+R), clear cache, verify deployment
- Dashboard PM vs building pipeline separation may need further UX work
- Churn score breakdown on Building Detail page shows "No score breakdown available" — consider populating

---

## 2026-02-24 — Holistic Architecture Planning & Execution Readiness

### What Was Accomplished
1. Completed a full project-level architecture/code review with JTBD framing.
2. Rewrote plan into a holistic 3-track execution model:
   - Runtime convergence
   - Durable async platform
   - Delivery confidence
3. Added explicit phase gates to prevent piecemeal implementation.
4. Documented end-to-end data flow (sources -> ingest/transform -> canonical store -> API -> user outputs).

### Execution Constraints Confirmed
- No dataset reduction required.
- Architecture simplification must preserve all current JTBD outcomes.
- Feature backlog expansion resumes after architecture gates pass.

### Context Handoff (Fresh Start)
- **Primary objective:** simplify internal flow, not product scope.
- **Target model:** one canonical PostgreSQL runtime path + one queue/worker async model + one API contract layer.
- **Next implementation start point:** Track A / Gate 1 (runtime convergence).

---

## 2026-02-24 — Track A / Gate 1 (Initial Execution Slice)

### Changes Implemented
1. **Smart List contract alignment**
   - Backend now allows empty `filters` for Smart List creation scaffolds.
2. **Runtime convergence (frontend API clients)**
   - Bulk enrich switched from legacy `/api/enrich` to PostgreSQL-native `POST /api/leads/{id}/enrich-all`.
   - Refresh action switched from legacy `/api/refresh` to jobs endpoint `POST /api/v1/jobs/buildings/start`.
   - Legacy enrichment progress/gaps/update API calls now use stable convergence-safe fallbacks while queued job path is finalized.
3. **Jobs API reliability**
   - Job start now commits transaction and records jobs as `queued` (not misleadingly `running`).

### Validation
- Backend tests: **32 passed**
- Frontend build still has pre-existing TypeScript issues in `AgentChat.tsx` and `LeadDetail.tsx` (not introduced by this slice).

### Follow-up Stabilization
- Fixed pre-existing frontend TypeScript issues:
  - Added `keepalive` SSE event type support in `frontend/services/api.ts`.
  - Corrected revenue fallback typing in `frontend/components/LeadDetail.tsx`.
  - Corrected building outreach stage typing in `frontend/components/Dashboard.tsx`.
- Frontend production build now passes successfully (`npm run build`).

### Runtime Convergence Hardening
- Updated `backend/api.py` to enforce PostgreSQL-first router composition by default.
- Legacy SQLite routers (`enrichment`, `pipeline`) now require explicit opt-in via `ENABLE_LEGACY_SQLITE_ROUTERS=1`.
- Added startup warnings for legacy router opt-in/failure states.
- Backend test suite remains green after this change (`32 passed`).

### Enrichment Job Contract Convergence
- Added `enrichment` as a valid job type in `backend/src/routers/jobs.py`.
- Replaced frontend enrichment placeholders with jobs API contract usage in `frontend/services/api.ts`:
  - `startBatchEnrichment()` now queues `POST /api/v1/jobs/enrichment/start`.
  - `getEnrichmentProgress()` now reads latest `enrichment` job from `GET /api/v1/jobs`.
- Dashboard enrichment action remains unchanged at call site but now runs against the canonical jobs API path.
- Validation:
  - Backend tests: **32 passed**
  - Frontend build: **passes** (`npm run build`)

### Worker Lifecycle Slice
- Added real Celery app bootstrap at `backend/src/worker.py` (broker/backend from `REDIS_URL`) and task autodiscovery includes.
- Refactored enrichment route logic into reusable core:
  - Added `enrich_lead_all_core()` in `backend/src/routers/leads.py`.
  - Route `POST /api/leads/{lead_id}/enrich-all` now delegates to shared core.
- Implemented queued enrichment worker execution in `backend/src/tasks/enrich.py`:
  - New task: `run_enrichment_job(job_id, limit)`.
  - Job lifecycle now transitions `queued -> running -> succeeded/failed`.
  - Progress fields (`total`, `processed`, `succeeded`, `failed`, `error`) are updated incrementally.
- Updated `backend/src/routers/jobs.py`:
  - `start_job` now accepts `limit` query param.
  - Enrichment jobs dispatch to Celery task; dispatch failures are marked on `ingestion_jobs`.
- Updated `frontend/services/api.ts` to pass `limit` when queueing enrichment.
- Validation:
  - Backend tests: **32 passed**
  - Frontend build: **passes** (`npm run build`)

### Dispatch Reliability Hardening
- Updated enrichment job dispatch in `backend/src/routers/jobs.py`:
  - Primary mode: Celery dispatch via `run_enrichment_job.delay(...)`.
  - Fallback mode: in-process async execution via `asyncio.create_task(...)` if Celery dispatch fails.
- API response now includes `dispatch_mode` for enrichment job starts (`celery` or `in_process`).
- Validation:
  - Backend tests: **32 passed**

### Operator Visibility Slice
- Updated `frontend/services/api.ts` to surface `dispatch_mode` returned from enrichment job start.
- Updated `frontend/components/Dashboard.tsx` enrichment start toast behavior:
  - `celery`: success toast.
  - `in_process`: neutral informational toast with fallback context.
- Validation:
  - Frontend build: **passes** (`npm run build`)

### Documentation Convergence
- Updated `backend/README.md` to reflect:
  - real worker bootstrap at `src/worker.py`
  - task module placement under `src/tasks/`
  - PostgreSQL-first runtime toggle (`ENABLE_LEGACY_SQLITE_ROUTERS=0`)
  - enrichment jobs API dispatch behavior (Celery primary, in-process fallback)

### Worker API Refinement
- Added public `get_session_factory()` in `backend/src/db/session.py` for worker contexts.
- Updated enrichment worker task to use public session factory accessor (avoids private API coupling).
- Validation:
  - Backend tests: **32 passed**

### Buildings Job Lifecycle Slice
- Updated `backend/src/routers/jobs.py` so `POST /api/v1/jobs/buildings/start` dispatches executable work:
  - Primary: Celery (`ingest_buildings_from_hpd.delay(job_id=...)`)
  - Fallback: in-process thread execution (`ingest_buildings_from_hpd.run(...)`)
  - Returns `dispatch_mode` for operator visibility.
- Updated `backend/src/tasks/ingest.py`:
  - `ingest_buildings_from_hpd` now accepts optional `job_id`.
  - When `job_id` is provided, task updates the existing queued row to `running` and reports progress to that same row.
  - Preserves standalone behavior when invoked without `job_id`.
- Validation:
  - Backend tests: **32 passed**
  - Frontend build: **passes** (`npm run build`)

### Buildings Dispatch Visibility Slice
- Updated `frontend/services/api.ts` `refreshPipeline()` response typing to include `dispatch_mode`.
- Updated `frontend/App.tsx` refresh CTA toast behavior:
  - `celery`: normal success message.
  - `in_process`: informational fallback-mode message.
- Validation:
  - Frontend build: **passes** (`npm run build`)

### Scoring Lifecycle Convergence Slice
- Added `run_scoring_job(job_id, config_id?)` task in `backend/src/tasks/score.py` to update `ingestion_jobs` lifecycle around scoring execution.
- Updated `backend/src/routers/jobs.py` to dispatch `job_type=scoring` through Celery with in-process fallback.
- Replaced custom thread-based scoring execution in `backend/src/routers/scoring.py` `POST /api/v1/scoring/recalculate`:
  - now creates a queued job row,
  - dispatches via worker/fallback,
  - returns `dispatch_mode` for operator visibility.
- Updated scoring frontend integration:
  - `frontend/services/scoring-api.ts` now types `dispatch_mode` in recalc response.
  - `frontend/components/SettingsPage.tsx` shows fallback-mode toast when relevant.
  - recent jobs badge coloring now treats `succeeded` as success and `queued` as active.
- Validation:
  - Backend tests: **32 passed**
  - Frontend build: **passes** (`npm run build`)

### Jobs UI Dispatch Consistency Slice
- Updated `frontend/services/jobs-api.ts` `startJob()` response typing to include optional `dispatch_mode`.
- Updated `frontend/components/SettingsPage.tsx` data-health re-run toast:
  - `in_process` dispatch now shows fallback informational messaging.
  - worker dispatch remains success toast.
- Validation:
  - Frontend build: **passes** (`npm run build`)

### Job Status Normalization Slice
- Standardized canonical lifecycle vocabulary toward: `queued`, `running`, `succeeded`, `failed`.
- Backend compatibility updates:
  - `backend/src/tasks/ingest.py` now normalizes legacy `"completed"` writes to `"succeeded"` in `_finish_job()`.
  - `backend/src/tasks/score.py` writes `"succeeded"` for successful scoring job completion.
  - `backend/src/routers/jobs.py` normalizes returned job status (`completed -> succeeded`) for list/get responses.
  - `backend/src/routers/jobs.py` status filter now treats `succeeded` and `completed` as equivalent for backward compatibility.
  - `backend/src/routers/leads.py` `last_refresh` query now accepts either `succeeded` or legacy `completed`.
- Validation:
  - Backend tests: **32 passed**
  - Frontend build: **passes** (`npm run build`)

### Job Type Alias Reliability Slice
- Added canonical alias mapping in `backend/src/routers/jobs.py` for source-oriented rerun inputs:
  - `hpd_buildings`, `hpd_registrations`, `hpd_contacts` -> `buildings`
  - `acris_transactions` -> `acris`
  - `energy_grades` -> `energy`
  - `eviction_filings` -> `evictions`
  - `facade_inspections` -> `facades`
  - `aep_designations` -> `aep`
- `start_job` now returns both canonical `job_type` and `requested_job_type` for traceability.
- Validation:
  - Backend tests: **32 passed**

### Docs Alignment Slice
- Updated `backend/README.md` operational notes with:
  - canonical job status vocabulary (`queued/running/succeeded/failed`) and compatibility note for legacy `completed`
  - jobs API alias acceptance for source-style reruns

### Ingestion Dispatch Completion Slice
- Refactored `backend/src/tasks/ingest.py` to support canonical queued job adoption across ingestion tasks:
  - added `_ensure_or_create_job(...)`
  - updated ingestion task signatures to accept optional `job_id`
  - when `job_id` is provided, tasks now execute against the pre-created queued row instead of creating orphan shadow jobs
- Extended `backend/src/routers/jobs.py` dispatch coverage for canonical ingestion types:
  - `hpd_complaints`, `acris`, `hpd_violations`, `dob_permits`, `hpd_litigation`,
    `emergency_repairs`, `aep`, `evictions`, `energy`, `facades`, `pad`
  - each uses Celery-first dispatch with in-process fallback and returns `dispatch_mode`
- Validation:
  - Backend tests: **32 passed**
  - Frontend build: **passes** (`npm run build`)

### Migration Safety & CI Gate Slice
- Updated `backend/alembic/versions/001_initial_schema.py`:
  - replaced metadata-wide `create_all/drop_all` behavior with explicit frozen table snapshot loops for upgrade/downgrade
  - keeps revision deterministic and prevents future model drift from mutating the initial migration behavior
- Updated `.github/workflows/ci.yml` backend job:
  - added migration safety check step that fails CI if `metadata.create_all` or `metadata.drop_all` appears in Alembic version files
- Validation:
  - Backend tests: **32 passed**
  - Frontend build: **passes** (`npm run build`)
  - Safety scan: no `metadata.create_all/drop_all` matches under `backend/alembic/versions`

### Jobs Observability Slice
- Added `GET /api/v1/jobs/summary` in `backend/src/routers/jobs.py` exposing:
  - `queued_count`
  - `running_count`
  - `succeeded_24h`
  - `failed_24h`
  - `avg_duration_seconds_24h`
- This provides queue-depth/error-rate/latency primitives for operational dashboards.
- Validation:
  - Backend tests: **32 passed**

### Queue Health UI Slice
- Extended `frontend/services/jobs-api.ts` with:
  - `JobsSummary` type
  - `fetchJobsSummary()` for `GET /api/v1/jobs/summary`
- Updated `frontend/components/SettingsPage.tsx` Data Health tab:
  - added “Queue Health (24h)” KPI cards for queued/running/succeeded/failed/avg duration
  - wired polling every 10s via React Query
- Validation:
  - Frontend build: **passes** (`npm run build`)

### CI Contract Test Slice
- Added `backend/tests/test_jobs_contract.py` to lock in jobs API convergence behavior:
  - legacy status normalization (`completed -> succeeded`)
  - canonical status passthrough expectations
  - alias map coverage for key source-style rerun names
- Validation:
  - Backend tests: **36 passed**

### Changelog Sync Slice
- Updated `CHANGELOG.md` (Unreleased) to reflect completed convergence work:
  - worker + queue execution rollout
  - jobs observability endpoint/UI
  - lifecycle status normalization
  - ingestion queued-job adoption
  - migration safety hardening + portable CI guard

### Frontend Test Gate Slice
- Added frontend unit test infrastructure with Vitest:
  - `frontend/package.json` scripts: `test`, `test:run`
  - `frontend/vitest.config.ts`
  - `frontend/test/setup.ts`
- Added critical-path jobs API tests:
  - `frontend/services/jobs-api.test.ts` covers job-start auth headers, summary fetch contract, and 401 logout behavior.
- Updated CI frontend job:
  - now runs `npm run test:run` before `npm run build`.
- Resolved config compatibility issue:
  - kept app build config in `vite.config.ts` (Vite-only)
  - moved test config to separate `vitest.config.ts` to avoid Vite/Vitest type conflicts.
- Validation:
  - Frontend tests: **3 passed**
  - Frontend build: **passes** (`npm run build`)
  - Backend tests remain green: **36 passed**

### Final Closure Sweep
- Performed full validation sweep:
  - Backend tests: **36 passed**
  - Migration safety scan: **passed** (no metadata-wide create/drop patterns in Alembic versions)
  - Frontend tests: **3 passed**
  - Frontend production build: **passes**
- Updated `PRODUCT_PLAN.md` convergence section to explicitly mark the architecture convergence program as completed.
