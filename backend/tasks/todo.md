# Double Edge - Task Tracker

(Formerly HPD Leads App)

## Completed

### Phase 0: Entity Classification
- [x] Add entity_type, company_name, primary_contact fields to Lead model
- [x] Classify leads as company/individual_agent/owner_operator
- [x] Expose contacts array in API + frontend
- [x] Entity scoring adjustment in scorer.py (Phase 0.4)

### Phase 1: Performance
- [x] Add database indexes for all filter columns
- [x] SQL-based filtering with `get_leads_filtered()`
- [x] SQL-based stats with `get_stats_sql()`
- [x] Server-side pagination in frontend

### Phase 2: Enrichment Rewrite
- [x] 4-tier cascade: Google Places (address) -> NY DOS -> Web Crawl -> Hunter.io
- [x] Address-first Google Places search
- [x] Person-based email discovery using HPD contacts
- [x] `/api/enrich/reset` endpoint
- [x] Enrichment retry logic (max 3 retries per lead) (Phase 2.7)
- [x] NY DOS cache persisted to SQLite (Phase 2.7)
- [x] Google Places cache persisted to SQLite (Phase 2.7)

### Phase 3: UX Features
- [x] Default high-value view (min_portfolio=10)
- [x] Contact columns in lead table
- [x] "Ready to Contact" dashboard card
- [x] CSV export with entity fields
- [x] Enrichment progress display

### Phase 4: Reliability
- [x] Thread safety: cache reads protected with locks
- [x] `get_lead` switched to DB query (not cache iteration)
- [x] Job resume bug fixed (unused min_score param removed)
- [x] CSV export uses DB query instead of cache
- [x] CORS locked to Vercel frontend + localhost
- [x] `/api/health/detailed` endpoint with diagnostics

### Phase 5: PE-Grade Sourcing
- [x] Revenue estimation (borough/type rent table, 5% mgmt fee) (5.1)
- [x] HPD Violations integration (Class A/B/C, per-unit) (5.2)
- [x] Outreach pipeline (stages, follow-ups, priority, events) (5.3)
- [x] Weekly auto-refresh + change detection alerts (5.4)
- [x] Due diligence snapshot (markdown report, comparables) (5.5)
- [x] Scoring V2 (8 dimensions: portfolio, units, professional, contact, concentration, revenue, distress, deal fit) (5.6)

### Phase 6: Cleanup & Rebrand
- [x] Moved stale scripts to backend/scripts/
- [x] Fixed start.py logging (print -> logger)
- [x] Archived REVIEW.md and COMPREHENSIVE_REVIEW.md
- [x] Updated frontend API types for all new fields
- [x] Removed unused frontend API functions
- [x] Updated README.md with current architecture
- [x] Updated scoring_weights.yaml for V2
- [x] Rebrand HPD Leads -> Double Edge
- [x] Remove remaining HPD branding (Connected -> API connected, HPD Violations -> Housing Violations, etc.)

### Phase 7: Production Job Hardening
- [x] Move lead-generation runtime logic out of `backend/scripts/` into `backend/src/services/`
- [x] Restore normal Railway worker execution for `lead_generation`
- [x] Add conservative orphan cleanup preview/execute tooling
- [x] Split integrity metrics so same-entity duplicate links are separated from valid multi-role linkages
- [x] Harden `data_quality_log` sequence handling for production job writes
- [x] Fix `quality_checks` production failures (timezone math + `change_alerts.dismissed`)

### Phase 8: Stabilization And Geospatial Closure
- [x] Re-audit the Mar 12 mixed worktree into validation-first workstreams
- [x] Validate backend lead runtime/data-contract changes with focused regression coverage
- [x] Validate Leads UX selection/route behavior and keep frontend build green
- [x] Add Smart Lists regression coverage for authored filters and open-in-leads handoff
- [x] Add persisted building coordinate fields, backfill job wiring, and map provenance surfaces
- [x] Harden `building_coordinates` retries/throttling and jobs fallback coverage
- [x] Switch normal portfolio/building map display to prefer persisted coordinates over browser-primary geocoding

## Backlog (Future Sessions)

- [ ] Frontend components for revenue display, violations, pipeline stage selector
- [ ] Kanban view for pipeline stages
- [ ] "Follow-ups Due Today" dashboard card
- [ ] Due diligence report modal in frontend
- [ ] Change alerts display in dashboard
- [ ] Apollo.io integration for deeper contact discovery
- [ ] Building-level detail view
- [ ] Export due diligence to Google Doc
- [ ] Email digest for weekly changes
- [ ] Historical violation trending (improving/worsening)
- [ ] Run a full hardening sweep across jobs chain behavior, source completeness, external-service error handling, and slow-query hotspots
- [ ] Design a higher-confidence classifier for the remaining ambiguous zero-link / blank-display lead tail
- [ ] Decide whether the 314k-lead surface should be reduced via stronger canonical grouping or exposed with clearer integrity/audit controls

## Architecture Notes

**GitHub Repo:** https://github.com/michaelbloom01/hpd-leads-app

**Deployment:**
- Frontend: Vercel (hpd-leads-app.vercel.app)
- Backend: Railway (hpd-leads-app-production.up.railway.app)

**Main API Endpoints:**
- GET /api/leads - Filtered, paginated lead list
- GET /api/leads/{id} - Single lead (from DB)
- GET /api/leads/{id}/due-diligence - Due diligence report
- POST /api/leads/{id}/outreach-event - Log pipeline event
- GET /api/stats - SQL-aggregated statistics
- GET /api/follow-ups - Follow-ups due
- GET /api/alerts - Change detection alerts
- POST /api/estimate-revenue - Revenue estimation
- POST /api/violations/refresh - HPD violations
- POST /api/rescore - Re-score with V2
- POST /api/refresh/check-updates - Change detection
