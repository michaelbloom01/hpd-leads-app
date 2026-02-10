# HPD Leads App - Product Plan

## Status: All Core Phases Complete (Feb 2026)

The HPD Leads App has completed all planned phases (0-6) and is now a full PE-grade sourcing platform.

## Completed Phases

### Phase 0: Entity Classification
- Leads classified as Company, Individual Agent, or Owner-Operator
- Company names resolved for person-named agents
- Primary contact and title populated from HPD data
- Entity-aware scoring adjustments

### Phase 1: Performance
- SQL-indexed database with 9+ indexes
- Server-side filtering and pagination
- SQL-based statistics aggregation
- Sub-second API response times

### Phase 2: Enrichment Rewrite
- 4-tier cascade: Google Places (address-first) -> NY DOS -> Web Crawl -> Hunter.io
- SQLite-backed caches for Google Places and NY DOS (persist across restarts)
- Retry logic with 3-attempt cap
- Address-based search for higher hit rates

### Phase 3: UX Features
- Default high-value view (10+ buildings)
- Contact columns with click-to-call/email
- CSV export with entity classification
- Enrichment progress display

### Phase 4: Reliability
- Thread-safe cache access (all reads under lock)
- Single-lead lookup via DB query
- CORS locked to production frontend
- `/api/health/detailed` comprehensive diagnostics

### Phase 5: PE-Grade Sourcing
- **5.1 Revenue Estimation**: Borough/type rent table, 5% management fee
- **5.2 HPD Violations**: Class A/B/C counts, per-unit normalization
- **5.3 Outreach Pipeline**: 8 stages, follow-up dates, priority ranking, event logging
- **5.4 Auto-Refresh**: Change detection alerts, stale data warnings
- **5.5 Due Diligence**: One-click structured reports with comparables
- **5.6 Scoring V2**: 8-dimension scoring (portfolio, units, professional, contact, concentration, revenue, distress, deal fit)

### Phase 6: Cleanup
- Stale files archived
- Logging standardized
- Documentation updated
- Frontend API cleaned up

## Remaining Backlog

These items can be addressed in future sessions:

1. **Frontend components** for revenue, violations, pipeline stage selector, due diligence modal
2. **Kanban view** for pipeline stages
3. **Apollo.io** integration for deeper contact discovery
4. **Building-level detail** view
5. **Google Doc export** for due diligence reports
6. **Email digest** for weekly change alerts
7. **Historical violation trending**
