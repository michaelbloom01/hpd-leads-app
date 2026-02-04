# HPD Leads — Task List

## Current Sprint

- [x] **Build NY DOS registry lookup** — DONE Feb 4, 2026
  - New `/api/dos/lookup` and `/api/dos/search` endpoints
  - Integrated into enrichment pipeline
- [ ] Add Apollo API integration
- [ ] Set up Google Sheets output (needs credentials)
- [x] **Deploy to Render (backend) + Vercel (frontend)** — DONE Feb 4, 2026
  - Frontend: https://frontend-nine-psi-58.vercel.app
  - Backend: https://hpd-leads-api.onrender.com
- [x] **Add CSV export to frontend** — DONE Feb 4, 2026
- [x] **Add lead status/notes tracking** — DONE Feb 4, 2026
  - Outreach statuses: new, contacted, interested, not_interested, closed
  - Notes field per lead
  - Status filter in table

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
- [x] **Build FastAPI backend** — REST API with CORS, pagination, filtering
- [x] **Build React frontend** — From Google AI Studio, Vantage.sh-style data table
- [x] **Connect frontend to backend** — API service with full CRUD
- [x] **Full HPD data support** — 201,282 buildings available, quick (10k) or full (200k+) refresh
- [x] **Management company identification** — Agent contact type = property management company

---

## Notes

### Management Company Identification (Feb 4, 2026)

**Key insight:** The "Agent" contact type in HPD data is the **property management company**.

**Contact Types in HPD Database:**
| Type | Count | Meaning |
|------|-------|---------|
| Agent | 153,418 | **Property management company** |
| SiteManager | 160,905 | Building super |
| HeadOfficer | 126,349 | Individual at owner entity |
| CorporateOwner | 119,915 | Owner entity (LLC, Corp) |
| Officer | 72,791 | Other officers |
| IndividualOwner | 48,054 | Individual owner |
| JointOwner | 44,161 | Joint owner |
| Shareholder | 40,172 | Shareholders |
| Lessee | 8,851 | Lessees |

**Data flow:**
1. Fetch buildings from HPD (tesw-yqqr) — 201,282 total
2. Fetch contacts for each building (feu5-w2e2)
3. Extract "Agent" contact type as management company
4. Group by Agent name (normalized) to create leads
5. Buildings without Agent fall back to Owner

### Frontend/Backend Integration (Feb 4, 2026)

**GitHub Repo:** https://github.com/michaelbloom01/hpd-leads-app

**Structure:**
```
hpd-leads-app/
├── backend/          # Python FastAPI (port 8000)
│   ├── api.py        # REST endpoints
│   ├── src/          # Pipeline code
│   └── requirements.txt
└── frontend/         # React + TypeScript (port 3000)
    ├── services/api.ts
    ├── components/
    └── package.json
```

**Backend API Endpoints:**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Health check |
| `/api/leads` | GET | Get leads with filtering |
| `/api/leads/{id}` | GET | Get single lead |
| `/api/status` | GET | Pipeline status |
| `/api/stats` | GET | Detailed statistics |
| `/api/refresh` | POST | Refresh from HPD (quick or full) |
| `/api/enrich` | POST | Enrich specific leads |
| `/api/enrich/batch` | POST | Auto-enrich top leads |

**Frontend Features:**
- Vantage.sh-style filterable data table
- Filters: search, borough, score, portfolio size, contact info, management company
- Sortable columns
- Pagination (50 per page)
- Bulk selection and enrichment
- Lead detail modal with one-click enrichment
- Quick (10k) or Full (200k+) data refresh toggle

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

### Feb 4, 2026 — Frontend Integration & Full Data Support
- Connected Google AI Studio frontend to Python backend
- Added full HPD database support (201,282 buildings)
- Clarified management company (Agent) vs owner distinction
- Vantage.sh-style UI with powerful filtering
- GitHub repo: https://github.com/michaelbloom01/hpd-leads-app

### Feb 3, 2026 — Initial Implementation
- Core pipeline working: ingest → normalize → aggregate → score → export
- CSV export works; Google Sheets needs credentials
- Enrichment module stubbed but not implemented
- Next: Add enrichment to get contact info (phone, email, website)
