# Double Edge — Full Project Context

> Paste this entire document into Claude to give it full context about the project. Last updated: Feb 24, 2026.

## What This Project Is

Double Edge (formerly HPD Leads) is a dual-purpose NYC housing intelligence platform. **PE acquisition sourcing** (find and evaluate PM companies as acquisition targets) and **PM operator lead generation** (find buildings with high churn probability for outreach). Built on HPD public data — identifies management companies, scores them, enriches with contact info, estimates revenue, tracks violations, and provides a full sourcing workflow.

- **Live App:** https://frontend-nine-psi-58.vercel.app
- **Backend API:** https://hpd-leads-app-production.up.railway.app
- **GitHub:** https://github.com/michaelbloom01/hpd-leads-app

## Architecture

```
Frontend: React + TypeScript + Tailwind CSS + Vite → Vercel
Backend:  Python FastAPI + SQLite → Railway (Docker, persistent volume)
```

### Directory Structure
```
hpd-leads-app/
├── backend/
│   ├── api.py                    # FastAPI app (~3,800 lines) — all REST endpoints
│   ├── src/
│   │   ├── ingest/               # HPD Buildings, PLUTO, Violations API clients
│   │   │   ├── hpd_client.py
│   │   │   ├── pluto_client.py
│   │   │   └── hpd_violations.py
│   │   ├── transform/
│   │   │   ├── normalize.py      # Building data normalization
│   │   │   └── aggregate.py      # Group buildings → leads, entity classification
│   │   ├── score/
│   │   │   ├── scorer.py         # V2 scoring (8 dimensions)
│   │   │   └── revenue.py        # Revenue estimation (Units × Rent × 5%)
│   │   ├── enrich/
│   │   │   ├── enricher.py       # Enrichment orchestrator
│   │   │   ├── contact_sources.py # Google Places, Hunter.io, MultiSourceEnricher
│   │   │   ├── ny_dos.py         # NY DOS corporation lookup
│   │   │   ├── web_crawl.py      # Website scraping
│   │   │   ├── ai_summary.py     # Claude AI company descriptions
│   │   │   └── batch_enricher.py # Batch enrichment logic
│   │   └── storage/
│   │       └── database.py       # SQLite persistence
│   ├── config/scoring_weights.yaml
│   └── requirements.txt
├── frontend/
│   ├── App.tsx                   # Main app, sidebar nav, modal routing
│   ├── components/
│   │   ├── Dashboard.tsx         # Overview metrics, pipeline funnel, top leads
│   │   ├── LeadTable.tsx         # Filterable/sortable lead list, bulk actions
│   │   ├── LeadDetail.tsx        # 5-tab detail: Overview, Contacts, Pipeline, Buildings, DD
│   │   ├── PortfolioMap.tsx      # Leaflet/OpenStreetMap building map
│   │   ├── Header.tsx
│   │   ├── Sidebar.tsx
│   │   └── ErrorBoundary.tsx
│   ├── services/api.ts           # All API functions + types
│   └── package.json
```

## Current Data (Feb 2026)

| Metric | Value |
|--------|-------|
| Total Leads | 102,505 |
| Total Buildings | 189,737 |
| Total Units | 7,835,246 |
| With Phone | 293 |
| With Email | 93 |
| With Website | 674 |
| Enriched (complete) | 301 |
| Enriched (partial) | 419 |
| Unenriched | 101,785 |
| Batch enrichment running | Yes — 500 leads with 40+ units being enriched |

## Data Pipeline

1. **Ingest** — Pull 200k+ buildings from NYC HPD API + PLUTO classification
2. **Transform** — Group by management company → ~102k leads
3. **Classify** — Entity type: Company, Individual Agent, Owner-Operator
4. **Score (V2)** — 8 dimensions: portfolio, units, professional, contact, concentration, revenue, distress, deal fit
5. **Revenue** — Estimate: `Total Units × Avg Rent (by borough & building type) × 5% mgmt fee`
6. **Violations** — Aggregate HPD violations per lead
7. **Enrich** — Multi-source cascade: Google Places → NY DOS → Web Crawl → Hunter.io → AI Summary
8. **Serve** — REST API with filtering, pagination, export

## Key API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/leads` | GET | Filtered lead list with server-side pagination, sorting, search |
| `/api/leads/{id}` | GET/PATCH | Get/update single lead |
| `/api/leads/{id}/enrich-all` | POST | **Unified enrichment** — contacts + research + AI summary in one call |
| `/api/leads/{id}/enrich-contacts` | POST | Multi-source contact enrichment only |
| `/api/leads/{id}/research` | POST | Website scrape + NY DOS + AI summary |
| `/api/leads/{id}/ai-summary` | POST | AI summary only |
| `/api/leads/{id}/due-diligence` | GET | Generate DD report |
| `/api/leads/{id}/outreach` | POST | Log outreach attempt |
| `/api/enrich/batch-full` | POST | Batch unified enrichment (contacts + research + AI) filtered by min_units |
| `/api/enrich/batch` | POST | Batch contact enrichment for top leads |
| `/api/stats` | GET | Aggregate statistics |
| `/api/enrichment-gaps` | GET | Enrichment coverage stats |
| `/api/export/csv` | GET | CSV export with filters |

## Lead Data Model

```typescript
interface ApiLead {
  lead_id: string;
  agent_name: string;           // Management company from HPD
  owner_name: string;
  company_name: string | null;  // Cleaned company name
  entity_type: string;          // company | individual_agent | owner_operator
  portfolio_size: number;       // Number of buildings
  total_units: number;          // Total residential units
  buildings: string[];          // Building addresses
  boros: string[];              // Boroughs served
  building_types: {             // PLUTO classification (BUILDING COUNTS, not unit counts!)
    condo: number; coop: number; rental_elevator: number;
    rental_walkup: number; small_residential: number;
  };
  score: number;                // 0-100
  phone: string | null;
  email: string | null;
  website: string | null;
  business_summary: string | null;  // AI-generated
  enrichment_status: string;    // none | partial | complete | failed
  estimated_annual_revenue: number;
  violations_per_unit: number;
  pipeline_stage: string;       // research → first_contact → follow_up → meeting_scheduled → meeting_done → loi → due_diligence → closed
  next_follow_up: string | null;
  priority_rank: number;        // 1-5 stars
}
```

## Frontend UX

### Dashboard (Dashboard.tsx)
- Pipeline funnel visualization (8 stages)
- 6 key metric cards: Target Leads, Mgmt Fee Opportunity, Follow-Ups Due, With Contact, Ready to Call, With Violations
- Follow-ups due panel (overdue highlighted)
- Ready to Contact panel
- Top 10 leads table

### Lead Table (LeadTable.tsx)
- Server-side search, filter, sort, paginate
- Filters: borough (multi-select), pipeline stage, buildings range, phone/email toggle, score range, units range, units/bldg range, building type toggles (condo/coop/rental/etc), entity type, outreach status, website
- "Go" button to apply filters (no auto-refresh)
- Columns: Company, Borough, Bldgs, Units, U/Bldg, Score, Mgmt Fee, Violations, Contact icons, Enrichment status badge
- Bulk selection + "Enrich Selected"
- CSV export

### Lead Detail (LeadDetail.tsx) — Modal with 5 tabs
- **Sticky Header:** Entity type badge, borough tags, score, revenue, Call/Email/Website/Enrich Lead buttons, pipeline dropdown
- **Overview tab:** Revenue estimate with breakdown, HPD violations (per-unit density), portfolio stats, building type composition, AI company summary, score breakdown, Leaflet map
- **Contacts tab:** Enrichment status banner, single "Enrich Lead" button (replaces old Research/Find Contacts/Generate Summary), phone/email/website with source attribution, NY DOS corporation info, HPD registered contacts
- **Pipeline tab:** Priority stars, follow-up date, outreach status, outreach log
- **Buildings tab:** Searchable building list with Google Maps links, interactive map
- **Due Diligence tab:** One-click DD report generation with key risks, comparables, print/PDF export

### Recent UX Simplification
Previously there were 5 different enrichment endpoints and 9 different buttons (Research Company, Find Contact Info, Find More Contacts, Generate Summary, Regenerate Summary, etc.). This was confusing.

**Now:** Single "Enrich Lead" button that calls `/api/leads/{id}/enrich-all` — does contacts (Google Places, NY DOS, Hunter.io, web crawl) + website deep scrape + AI summary all in one call. The button says "Enrich Lead" for new leads, "Re-enrich Lead" for ones already processed.

## Environment Variables

### Backend (Railway)
```
ANTHROPIC_API_KEY=sk-ant-...         # AI summaries via Claude
GOOGLE_PLACES_API_KEY=AIza...        # Google Places enrichment
HUNTER_API_KEY=...                   # Hunter.io email finder
CORS_ORIGINS=https://...             # CORS whitelist
DATABASE_PATH=./leads.db
```

### Frontend (Vercel)
```
VITE_API_URL=https://hpd-leads-app-production.up.railway.app
VITE_GOOGLE_MAPS_KEY=AIza...
```

## Data Sources

- **HPD Buildings:** https://data.cityofnewyork.us/resource/tesw-yqqr.json
- **HPD Contacts:** https://data.cityofnewyork.us/resource/feu5-w2e2.json
- **HPD Violations:** https://data.cityofnewyork.us/resource/wvxf-dwi5.json
- **PLUTO:** https://data.cityofnewyork.us/resource/64uk-42ks.json
- **NY DOS Corporations:** https://data.ny.gov/resource/n9v6-gdp6.json

## Tech Stack Details

### Frontend Dependencies
- React 18, TypeScript, Vite
- Tailwind CSS 3.4
- react-leaflet 4.2.1 (NOT v5 — v5 has a crash bug)
- recharts (charts on dashboard)
- react-hot-toast (notifications)

### Backend Dependencies
- FastAPI, uvicorn, gunicorn (300s timeout)
- SQLite with WAL mode
- anthropic (Claude AI summaries)
- beautifulsoup4, trafilatura (web scraping)
- googlesearch-python 1.2.5 (pinned)
- pandas, requests, pydantic

## Deployment

- **Backend:** Railway auto-deploys from `master` branch. Dockerfile build. Persistent volume at `/app`. SQLite DB survives redeploys.
- **Frontend:** Manual deploy via `cd frontend && npx vercel --prod --yes`. Production URL: https://frontend-nine-psi-58.vercel.app
- **Important:** Pushing to git triggers Railway redeploy which kills background enrichment. Push code first, then start enrichment.

## Known Issues / Quirks

1. `building_types` fields are BUILDING COUNTS, not unit counts (common source of confusion)
2. react-leaflet must stay at v4.x — v5 causes "r is not a function" crash
3. Violations take several minutes to compute on first startup (59k buildings via HPD API)
4. Some leads are co-op complexes (e.g., Deepdale Gardens with 128k units) — not true PE targets
5. Railway redeploys kill background enrichment processes (they're in-memory threads)
6. `MultiSourceEnricher` class is in `contact_sources.py`, NOT `multi_source.py`
7. PowerShell on Windows — can't use Bash syntax (`&&`, `heredoc`, `wc`) in terminal commands
