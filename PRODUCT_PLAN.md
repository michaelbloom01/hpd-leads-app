# Double Edge - Product Plan

## Status: All Core Phases + Smart Lists Complete (Feb 2026)

Double Edge (formerly HPD Leads) is a dual-purpose NYC housing intelligence platform:
- **PE/Acquirer view (Leads tab):** Source and evaluate PM businesses for acquisition
- **PM Operator view (Buildings tab):** Find buildings ripe for high-value outreach

## Completed Phases

### Phase 0: Entity Classification
- Leads classified as Company, Individual Agent, or Owner-Operator
- Company names resolved for person-named agents
- Primary contact and title populated from HPD data

### Phase 1: Performance
- PostgreSQL with async SQLAlchemy (migrated from SQLite)
- Server-side filtering and pagination with parameterized queries
- SQL-indexed queries with sub-second response times

### Phase 2: Enrichment
- 4-tier cascade: Google Places -> NY DOS -> Web Crawl -> Hunter.io
- Unified "Enrich Lead" action (contacts + research + AI summary in one call)
- Retry logic with background processing

### Phase 3: UX Features
- Multi-borough filtering, units/building computed filters
- Contact columns with click-to-call/email
- CSV export (server-side and client-side fallback)
- Address search alongside PM company search

### Phase 4: Reliability
- JWT authentication
- Rate limiting (slowapi)
- fetchWithRetry with toast-based error classification (401/403/404/422/500/timeout)
- Cold-start detection with health polling

### Phase 5: PE-Grade Sourcing
- **5.1 Revenue Estimation**: Per-lead and bulk, borough/type-adjusted
- **5.2 HPD Violations**: Class A/B/C, per-unit normalized, distress signals
- **5.3 Outreach Pipeline**: 8 stages, follow-up dates, priority ranking, event logging, email templates
- **5.4 Auto-Refresh**: Change detection alerts, stale data warnings
- **5.5 Due Diligence**: Quick Risk Snapshot (full DD reports coming soon)
- **5.6 Scoring V2**: 8-dimension scoring

### Phase 6: Hardening & Smart Lists
- **Rebrand**: HPD Leads -> Double Edge across all surfaces
- **HPD branding removal**: "Connected" -> "API connected"; "HPD Violations/Litigation/coverage" -> "Housing Violations/Litigation/data coverage"; metadata, package name updated
- **SQL hardening**: Whitelist-validated sort columns, parameterized WHERE clauses
- **useLeadFilters hook**: Consolidated 20+ filter useState calls into single useReducer
- **Global error layer**: Toast-based error classification in fetchWithRetry
- **Accessibility**: ESC-to-close, focus trapping, ARIA labels, focus-visible indicators
- **URL filter persistence**: useFilterUrl hook syncs filters to/from URL search params
- **Smart Lists**: Saved filter segments with CRUD, change detection (evaluate), and pin-to-dashboard
- **404 route**: Catch-all with styled page
- **Bug fixes**: Multi-borough filter, CSV export path, per-lead revenue endpoint, building_type_has and units_per_bldg filters

## Remaining Backlog

1. **Full Due Diligence reports** — AI-generated with comparables
2. **Kanban view** for pipeline stages
3. **Apollo.io** integration for deeper contact discovery
4. **Dashboard pinned Smart List tiles** — show pinned lists on the main dashboard
5. **Smart List auto-evaluation** — run evaluations on a schedule
6. **Email digest** for weekly change alerts
7. **Historical violation trending**
8. **Component extraction** — split LeadTable.tsx into smaller components

## Architecture Convergence Program (Pre-Backlog Gate)

Execution is temporarily sequenced through three foundation tracks before deeper backlog expansion:

1. **Runtime Convergence** — canonical PostgreSQL runtime path, no split semantics
2. **Durable Async Platform** — queue + worker for long-running ingestion/enrichment/scoring
3. **Delivery Confidence** — migration safety, critical-path tests, and CI gates

Backlog items above continue after these gates are complete to avoid piecemeal execution and regressions.

### Convergence Status (Completed)

All three convergence tracks are now implemented in the active codebase:

- Runtime is PostgreSQL-first by default; legacy SQLite routers are opt-in only.
- Jobs execute through a canonical queue lifecycle with worker-first dispatch and safe in-process fallback.
- Delivery confidence includes migration safety guards, backend + frontend automated tests, and CI enforcement.
