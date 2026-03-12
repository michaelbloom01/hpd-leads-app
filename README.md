# Double Edge

NYC property management intelligence platform with dual purpose: **PE acquisition sourcing** (identify PM companies as acquisition targets) and **PM operator lead generation** (find buildings ripe for outreach). Built on HPD public data covering 200k+ buildings.

**Live App:** https://frontend-nine-psi-58.vercel.app  
**Backend API:** https://hpd-leads-app-production.up.railway.app  
**GitHub:** https://github.com/michaelbloom01/hpd-leads-app

## What It Does

1. **Fetches ALL buildings** from NYC HPD database (200k+ buildings)
2. **Joins with PLUTO data** for building classification (condo, coop, rental, etc.)
3. **Materializes lead entities from building contacts** to create the current live lead surface
4. **Classifies entities** as Company, Individual Agent, or Owner-Operator
5. **Estimates revenue** per lead based on units, borough, and building type
6. **Integrates HPD violations** as distress/opportunity signals
7. **Scores leads (V2)** using 8 dimensions: portfolio, units, professional, contact, concentration, revenue, distress, deal fit
8. **Enriches contacts** using 4-tier cascade: Google Places -> NY DOS -> Web Crawl -> Hunter.io
9. **Smart Lists** — saved filter segments that track lead changes over time
10. **Building Lists** — saved collections of buildings for outreach workflows
11. **Full sourcing UI** with server-side filtering, pipeline tracking, follow-ups, and bookmarkable filter URLs

## Current Status (Mar 2026)

| Metric | Value |
|--------|-------|
| Total Leads | 314,723 |
| Total Buildings | 179,985 |
| Entity Coverage Ratio | 98.3% |
| Zero-Link Leads | 55,804 |
| Blank Display-Name Leads | 54,507 |
| Entity Classification | Company / Individual Agent / Owner-Operator |
| Building Type Coverage | 100% (PLUTO data) |
| Scoring | V2: 8-dimension |
| Enrichment Sources | Google Places, NY DOS, Web Crawl, Hunter.io |
| Revenue Estimation | Borough/type-adjusted, 5% mgmt fee |
| Violations Data | HPD Violations (Class A/B/C, per-unit normalized) |
| Pipeline Stages | Research -> First Contact -> Follow-Up -> Meeting -> LOI -> DD -> Closed |
| Smart Lists | Saved filter segments with change detection |

Production note: the normal Railway worker path for `lead_generation`, `lead_reconciliation`, and `quality_checks` is now working again. The remaining orphan/blank lead tail is still under conservative cleanup review and has not been bulk-deleted.

## Architecture Execution Readiness (Feb 24, 2026)

Before major feature expansion, the project is executing a holistic architecture convergence program:

- Runtime convergence to one canonical PostgreSQL path
- Durable async processing via queue + worker for long-running jobs
- Delivery confidence baseline (migration safety + critical-path tests + CI gates)

This is an architecture simplification effort and does not reduce product JTBD scope.

## Key Features

### Dashboard
- Key metrics at a glance (total leads, enriched count, top scores)
- "Ready to Contact" quick-access card
- Portfolio size and units/building charts
- Entity type distribution
- Change alerts and follow-up reminders

### Lead Table
- Filter by borough, score, portfolio size, units, units/building, entity type, pipeline stage, building type
- Multi-borough selection, phone/email/website filters
- URL-persisted filters (bookmarkable, shareable)
- "Save as Smart List" to track filter segments over time
- Bulk selection and export to CSV
- Revenue and violations columns
- Server-side sorting including computed columns (units/building)

### Lead Detail Modal
- Contact info front and center — phone, email, website with one-click actions
- Revenue estimate and violation summary
- Pipeline stage management with follow-up dates
- Portfolio composition (condos, coops, rentals breakdown)
- Quick Risk Snapshot on Due Diligence tab
- AI-generated company summaries
- Outreach event logging with email templates
- Keyboard accessible: ESC to close, Tab focus trapping, ARIA labels

### Smart Lists
- Save any filter combination as a named Smart List
- Evaluate to detect which leads entered/exited since last run
- Pin favorites to keep them at the top
- Open in Leads page to apply saved filters instantly
- Change alerts when list composition shifts

### Building Lists
- Create named building collections from the Buildings table
- Add/remove buildings by BBL
- Manage lists from dedicated `/building-lists` page
- Open building detail directly from list members

### Buildings Tab
- Building-level search and filtering
- Churn score and outreach pipeline per building
- CSV export

### AI Agent
- Natural language query interface (Cmd+K)
- Lead lookups, script generation, briefing emails
- Conversation history

## Architecture

```
hpd-leads-app/
├── backend/              # Python FastAPI (Railway)
│   ├── api.py            # REST API entry point
│   ├── src/
│   │   ├── routers/      # leads, buildings, smart_lists, admin, etc.
│   │   ├── ingest/       # HPD, PLUTO, Violations API clients
│   │   ├── transform/    # Normalize & aggregate to leads
│   │   ├── score/        # Scoring V2 + revenue estimation
│   │   ├── enrich/       # Google Places, NY DOS, Hunter, Web Crawl
│   │   ├── db/           # Async SQLAlchemy session (PostgreSQL)
│   │   └── agent/        # AI Agent with tools
│   └── config/           # Scoring weights YAML
├── frontend/             # React + TypeScript (Vercel)
│   ├── components/       # Dashboard, LeadTable, LeadDetail, SmartListsPage
│   ├── hooks/            # useLeadFilters, useFilterUrl
│   └── services/         # API client with retry + error classification
└── docs/                 # Archived reviews
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | Health check |
| `/api/leads` | GET | Get leads with filtering + pagination |
| `/api/leads/{id}` | GET | Get single lead |
| `/api/leads/{id}` | PATCH | Update status, pipeline, follow-up, priority |
| `/api/leads/{id}/estimate-revenue` | POST | Estimate and persist revenue for a lead |
| `/api/leads/{id}/enrich-all` | POST | Unified enrichment (contacts + research + AI) |
| `/api/leads/{id}/outreach-event` | POST | Log outreach event |
| `/api/stats` | GET | Detailed statistics |
| `/api/follow-ups` | GET | Leads with follow-ups due |
| `/api/alerts` | GET | Change detection alerts |
| `/api/smart-lists` | GET/POST | List or create Smart Lists |
| `/api/smart-lists/{id}` | GET/PATCH/DELETE | CRUD for a Smart List |
| `/api/smart-lists/{id}/evaluate` | POST | Re-run filters, detect changes |
| `/api/v1/building-lists` | GET/POST | List or create Building Lists |
| `/api/v1/building-lists/{id}` | PATCH/DELETE | Rename or delete a Building List |
| `/api/v1/building-lists/{id}/buildings/{bbl}` | POST/DELETE | Add or remove a building from list |
| `/api/v1/building-lists/{id}/buildings` | GET | List buildings in a Building List |
| `/api/enrich/batch` | POST | Start batch enrichment |
| `/api/estimate-revenue` | POST | Bulk revenue estimation |
| `/api/violations/refresh` | POST | Fetch HPD violations |
| `/api/rescore` | POST | Re-score all leads (V2) |
| `/api/v1/export/leads/csv` | GET | Export leads to CSV |
| `/api/v1/export/buildings/csv` | GET | Export buildings to CSV |

## Local Development

### Backend

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn api:app --reload --port 8000
# API at http://localhost:8000
# Docs at http://localhost:8000/api/docs
```

### Frontend

```bash
cd frontend
npm install
npm run dev
# UI at http://localhost:5173
```

## Environment Variables

### Backend (Railway)

```
DATABASE_URL=postgresql+asyncpg://...     # PostgreSQL connection
REDIS_URL=redis://...                      # Redis broker/result backend for Celery
JWT_SECRET=...                             # JWT signing secret
ANTHROPIC_API_KEY=sk-ant-...              # AI summaries
GOOGLE_PLACES_API_KEY=AIza...             # Google Places enrichment
HUNTER_API_KEY=...                        # Hunter.io email finder
CORS_ORIGINS=https://your-frontend.vercel.app,http://localhost:5173
```

### Worker Service (Railway)

- Service name: `hpd-leads-worker`
- Uses same backend image with `WORKER_MODE=1`
- Requires: `REDIS_URL`, `DATABASE_URL`, and shared API keys as needed by task modules

### Frontend (Vercel)

```
VITE_API_URL=https://hpd-leads-app-production.up.railway.app
```

## Data Sources

- **HPD Buildings:** `https://data.cityofnewyork.us/resource/tesw-yqqr.json`
- **HPD Contacts:** `https://data.cityofnewyork.us/resource/feu5-w2e2.json`
- **HPD Violations:** `https://data.cityofnewyork.us/resource/wvxf-dwi5.json`
- **PLUTO (Building Classes):** `https://data.cityofnewyork.us/resource/64uk-42ks.json`
- **NY DOS Corporations:** `https://data.ny.gov/resource/n9v6-gdp6.json`

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for release history. Session notes are in [docs/SESSION_NOTES.md](docs/SESSION_NOTES.md).

## License

Private - Michael Bloom
