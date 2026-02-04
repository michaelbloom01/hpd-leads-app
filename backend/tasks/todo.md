# HPD Leads — Task List

## Current Sprint

- [ ] Build NY DOS registry lookup
- [ ] Add Apollo API integration
- [ ] Set up Google Sheets output (needs credentials)

## Backlog

- [ ] Set up daily scheduler
- [ ] Add failure alerting
- [ ] Tune scoring weights based on real data

## Done

- [x] Create project structure
- [x] Write knowledge base docs
- [x] Define data model
- [x] Define scoring rules
- [x] **Build HPD ingest module** — Two endpoints: buildings (tesw-yqqr) + contacts (feu5-w2e2)
- [x] **Build normalization module** — Building and Contact dataclasses with proper field mapping
- [x] **Build aggregation module** — Groups by agent name, computes portfolio metrics
- [x] **Implement scoring logic** — Portfolio size weighted scoring with tiers
- [x] **Build Google Sheets publisher** — Full refresh + incremental update + CSV export
- [x] **Test end-to-end** — 5000 buildings → 2542 leads in 15s
- [x] **Build web crawl enrichment** — Google search + DuckDuckGo fallback + website scraping + caching

---

## Notes

### Web Crawl Enrichment (Feb 4, 2026)

**Implementation:**
- `src/enrich/web_crawl.py` - WebCrawler class with:
  - Google search (via `googlesearch-python`) with DuckDuckGo fallback
  - Website scraping to extract phone, email, address, summary, owner
  - File-based caching with 30-day TTL
  - Excluded domains list (Yelp, LinkedIn, Zillow, etc.)
  - Rate limiting (1s delay + random jitter)

- `src/enrich/enricher.py` - Enricher orchestrator with:
  - Tier-based enrichment (web_crawl implemented, NY DOS and Apollo stubbed)
  - Batch processing with filtering by score/portfolio size
  - Automatic tag updates (has_website, has_phone, has_email)

**Test Results:**
- Douglas Elliman → Found ellimanpm.com, phone (212) 370-9200
- Corcoran Group → Found corcoran.com, phone (800) 544-4055
- Enrichment completes in ~20-30s per lead (includes rate limiting)

**Known Limitations:**
- `googlesearch-python` often gets rate-limited by Google (returns 0 results)
- DuckDuckGo fallback works reliably
- Email extraction skips generic addresses (info@, support@, noreply@)

### API Endpoints (Feb 3, 2026)
- Buildings: `https://data.cityofnewyork.us/resource/tesw-yqqr.json`
- Contacts: `https://data.cityofnewyork.us/resource/feu5-w2e2.json`
- The original endpoint in docs (vx8i-nprf) was wrong

### Test Results
- 5000 buildings → 2542 leads
- Top lead: GRINBERG MANAGEMENT & DEVELOPMENT LLC (125 buildings, score 59.5)
- Pipeline runs in ~15s for 5000 buildings

---

## Review

### Feb 3, 2026 — Initial Implementation
- Core pipeline working: ingest → normalize → aggregate → score → export
- CSV export works; Google Sheets needs credentials
- Enrichment module stubbed but not implemented
- Next: Add enrichment to get contact info (phone, email, website)
