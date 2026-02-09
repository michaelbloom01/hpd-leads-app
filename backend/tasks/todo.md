# HPD Leads — Task List

## Completed (Feb 9, 2026 Session)

- [x] **Phase 0: Entity Classification** — Classify leads as company/individual_agent/owner_operator
  - Added entity_type, company_name, primary_contact, primary_contact_title to Lead model
  - Classification logic in aggregate.py using company indicators + person name heuristics
  - Exposed contacts array and entity fields in API response
  - Frontend shows entity badges (CO/AGT/OWN) and primary contact
- [x] **Phase 1: Performance** — SQL-based filtering and stats
  - Added 7 new DB indexes (portfolio, phone, email, website, outreach, score+portfolio, entity_type)
  - New `get_leads_filtered()` method with SQL WHERE clauses
  - New `get_stats_sql()` method with SQL GROUP BY aggregation
  - /api/leads returns `{leads, total, offset, limit}` paginated response
  - /api/stats uses SQL aggregation instead of Python iteration over 102k leads
- [x] **Phase 2: Enrichment Rewrite** — Address-first cascade
  - New `/api/enrich/reset` endpoint to clear stuck jobs and failed leads
  - Google Places now searches by business address first (higher confidence)
  - 4-tier cascade: Address Places → Name Places → Web Crawl → Person Email (Hunter)
  - Person-based email discovery using HPD contact names with Hunter.io
  - enricher.py updated to use MultiSourceEnricher
- [x] **Phase 3: UX** — Frontend improvements
  - Server-side pagination (no more loading 500 leads into memory)
  - Entity type filter dropdown
  - Entity info badges (CO/AGT/OWN) in lead table
  - HPD contacts section in lead detail modal
  - Updated CSV export with entity fields
  - Dashboard uses SQL stats (fast load)

## Previously Completed

- [x] Build NY DOS registry lookup — Feb 4, 2026
- [x] Deploy to Railway (backend) + Vercel (frontend) — Feb 4, 2026
- [x] Add CSV export to frontend — Feb 4, 2026
- [x] Add lead status/notes tracking — Feb 4, 2026
- [x] Improve scoring with geographic concentration — Feb 4, 2026
- [x] Persistent database (SQLite) — Feb 4, 2026
- [x] LinkedIn enrichment + deduplication — Feb 4, 2026
- [x] Multi-source enrichment (Google Places, Hunter.io, Web Crawl) — Feb 4, 2026
- [x] PLUTO building classification integration — Feb 4, 2026

## Backlog (Future Sessions)

- [ ] Revenue estimation per lead (units x avg_rent x mgmt_fee)
- [ ] HPD Violations dataset integration (distress signal)
- [ ] Proper outreach pipeline (Kanban stages, follow-up dates)
- [ ] Weekly auto-refresh from HPD with change alerts
- [ ] One-click due diligence snapshot (export to Google Doc)
- [ ] Scoring V2 (revenue, entity quality, distress, growth, deal fit)
- [ ] Apollo.io integration for deeper contact discovery
- [ ] Building-level detail view

---

## Architecture Notes

**GitHub Repo:** https://github.com/michaelbloom01/hpd-leads-app

**Deployments:**
- Frontend: Vercel (frontend-nine-psi-58.vercel.app)
- Backend: Railway (hpd-leads-app-production.up.railway.app)

**Key API Endpoints:**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/leads` | GET | SQL-filtered leads with pagination |
| `/api/stats` | GET | SQL-aggregated statistics |
| `/api/enrich/reset` | POST | Reset stuck enrichment jobs |
| `/api/enrich/batch` | POST | Batch enrich top leads |
| `/api/leads/{id}/enrich-contacts` | POST | Single-lead multi-source enrichment |
