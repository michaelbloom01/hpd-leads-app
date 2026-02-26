# Changelog

All notable changes to Double Edge (formerly HPD Leads) are documented here.

## [Unreleased]

### Added
- Holistic architecture execution plan with end-to-end data-flow mapping and phase gates.
- Real worker bootstrap at `backend/src/worker.py` and executable queue dispatch for enrichment/buildings/scoring/ingestion job families.
- Jobs observability endpoint `GET /api/v1/jobs/summary` plus Settings UI “Queue Health (24h)” telemetry cards.
- CI contract coverage for jobs normalization/alias behavior (`backend/tests/test_jobs_contract.py`).
- Building Lists backend surface (`/api/v1/building-lists`) with create/list/rename/delete and member add/remove/list.
- Dedicated Building Lists UI page (`/building-lists`) with list CRUD and member management.
- Dedicated Railway worker service (`hpd-leads-worker`) with Redis-backed Celery runtime.
- Worker-mode runtime support in `backend/start.py` and worker-safe healthcheck behavior in `backend/Dockerfile`.

### Changed
- Updated active markdown documentation to reflect execution baseline (runtime convergence, durable async jobs, confidence gates).
- Updated frontend/backend README files to current architecture and startup expectations.
- Canonical jobs lifecycle normalized to `queued/running/succeeded/failed` with compatibility handling for legacy `completed`.
- `POST /api/v1/jobs/{job_type}/start` now supports source-style aliases (e.g., `energy_grades -> energy`) and returns `requested_job_type`.
- Ingestion tasks now adopt pre-created queued jobs via optional `job_id` instead of creating detached shadow jobs.
- Initial Alembic migration hardened to explicit frozen table snapshot loops instead of metadata-wide create/drop.
- Buildings table UX labels clarified: `Status` -> `Outreach`; `BBL` expanded to `BBL (Borough-Block-Lot)`.
- Buildings table now includes per-building estimated annual revenue.
- Production operations baseline now includes one-time snapshot recompute endpoints and stale-job reconciliation during go-live.

### Fixed
- Documentation drift reduction across key architecture docs.
- CI migration safety guard now uses a portable Python scanner instead of shell-specific tools.
- Leads search no longer dedupes by `normalized_name` at query-time, resolving suppressed valid matches under portfolio/unit filters.
- Added regression guard to prevent reintroducing query-time normalized-name dedupe.
- Fixed leads API filtering behavior for comma-delimited multi-select filters (`entity_type`, `pipeline_stage`, `enrichment_status`, `outreach_status`).
- Fixed `has_phone` / `has_email` / `has_website` false filtering semantics.
- Fixed stale `total_units` / `portfolio_size` snapshot drift impact by adding periodic on-demand snapshot sync for unit/portfolio filter paths.
- Fixed production worker health from degraded (`broker_configured=false` / no worker) to healthy (`broker_configured=true`, `celery_ping_ok=true`, `stale_running_jobs=0`).

---

## [3.1.0] - 2026-02-24

### Changed — HPD Branding Removal
- **Sidebar & Header**: "Connected" → "API connected"
- **metadata.json**: "NYC HPD data" → "NYC housing data"
- **Dashboard**: "X% HPD coverage" → "X% data coverage"
- **Refresh dialog**: "Refresh from HPD" → "Refresh from NYC Open Data"
- **LeadDetail**: "HPD Violations" → "Housing Violations"
- **LeadTable**: Tooltips updated — "HPD violations/registration/records" → "housing violations/registration/city records"
- **BuildingDetailPage & SettingsPage**: "HPD Litigation" → "Housing Litigation"
- **package.json**: "hpd-leads-frontend" → "double-edge-frontend"

---

## [3.0.0] - 2026-02-24

### Added
- **Smart Lists**: Saved filter segments with CRUD, change detection (evaluate), and pin-to-dashboard
- **useFilterUrl hook**: Bookmarkable/shareable filter URLs
- **RouteErrorBoundary**: Error recovery for lazy-loaded routes
- **Per-lead revenue estimation endpoint**: `POST /api/leads/{id}/estimate-revenue`
- **404 catch-all route**: Styled "Page Not Found" for unknown paths

### Changed
- **Rebrand**: HPD Leads → Double Edge across all surfaces
- **useLeadFilters hook**: Consolidated 20+ filter useState calls into single useReducer
- **Global error layer**: Toast-based error classification (401/403/404/422/5xx/timeout) in fetchWithRetry
- **Accessibility**: ESC-to-close, focus trapping, ARIA labels, aria-sort on table headers, focus-visible indicators
- **Authentication**: All data endpoints now require JWT
- **Rate limiting**: 60/min reads, 30/min writes, 10/min exports
- **Input validation**: Query params validated with length/range constraints
- **Form validation**: Login and Smart Lists client-side validation
- **Memory leak fixes**: useRef-based cleanup in Dashboard and LeadTable polling

### Fixed
- Multi-borough filter (comma-separated → IN clause)
- CSV export 404 (correct path `/api/v1/export/leads/csv`)
- Per-lead revenue endpoint (was missing)
- building_type_has and units_per_bldg filters (backend ignored them)
- units_per_bldg sort (was silently falling back)
- DD tab dead-end (replaced with Quick Risk Snapshot placeholder)
- Missing DB commits in buildings.py update endpoints
- SQL injection hardening in quality.py and buildings.py

---

## [2.x] - Prior to Feb 2026

See git history and PRODUCT_PLAN.md for earlier phases (Entity Classification, Performance, Enrichment, UX, Reliability, PE-Grade Sourcing).
