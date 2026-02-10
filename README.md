# HPD Leads App

NYC property management lead generation platform for PE acquisition targets. Identifies **management companies** from the HPD database, scores them with a multi-dimensional V2 algorithm, enriches with contact info, estimates revenue, tracks violations, and provides a full sourcing workflow.

**Live App:** https://hpd-leads-app.vercel.app  
**Backend API:** https://hpd-leads-app-production.up.railway.app  
**GitHub:** https://github.com/michaelbloom01/hpd-leads-app

## What It Does

1. **Fetches ALL buildings** from NYC HPD database (200k+ buildings)
2. **Joins with PLUTO data** for building classification (condo, coop, rental, etc.)
3. **Groups by management company** to create ~100k leads
4. **Classifies entities** as Company, Individual Agent, or Owner-Operator
5. **Estimates revenue** per lead based on units, borough, and building type
6. **Integrates HPD violations** as distress/opportunity signals
7. **Scores leads (V2)** using 8 dimensions: portfolio, units, professional, contact, concentration, revenue, distress, deal fit
8. **Enriches contacts** using 4-tier cascade: Google Places (address-based) -> NY DOS -> Web Crawl -> Hunter.io (with SQLite-backed caching)
9. **Displays in a full sourcing UI** with server-side pagination, pipeline tracking, follow-up management, and due diligence reports

## Current Status (Feb 2026)

| Metric | Value |
|--------|-------|
| Total Leads | 102,505 |
| High-Value Leads (10+ buildings) | ~1,300 |
| Entity Classification | Company / Individual Agent / Owner-Operator |
| Building Type Coverage | 100% (PLUTO data) |
| Scoring | V2: 8-dimension (portfolio, units, professional, contact, concentration, revenue, distress, deal fit) |
| Enrichment Sources | Google Places, NY DOS, Web Crawl, Hunter.io (with SQLite caching) |
| Revenue Estimation | Borough/type-adjusted, 5% mgmt fee assumption |
| Violations Data | HPD Violations (Class A/B/C, per-unit normalized) |
| Pipeline Stages | Research -> First Contact -> Follow-Up -> Meeting -> LOI -> DD -> Closed |
| Performance | SQL-indexed queries, server-side pagination, thread-safe |

## Key Features

### Dashboard
- Key metrics at a glance (total leads, enriched count, top scores)
- "Ready to Contact" quick-access card
- Portfolio size and units/building charts
- Entity type distribution
- Change alerts and follow-up reminders

### Lead Table
- Filter by borough, score, portfolio size, entity type, pipeline stage
- Filter for leads with phone/email/website
- Bulk selection and export to CSV
- Revenue and violations columns
- Click any lead for full details

### Lead Detail Modal
- **Contact info front and center** - phone, email, website with one-click actions
- Revenue estimate and violation summary
- Pipeline stage management with follow-up dates
- Portfolio composition (condos, coops, rentals breakdown)
- HPD registered contacts
- AI-generated company summaries
- Outreach event logging
- One-click due diligence report generation

### Sourcing Pipeline (Phase 5.3)
- Pipeline stages: Research, First Contact, Follow-Up, Meeting Scheduled, Meeting Done, LOI, Due Diligence, Closed
- Follow-up date tracking with "due today" alerts
- Priority ranking (1-5 stars)
- Outreach event history per lead

### Due Diligence Reports (Phase 5.5)
- One-click structured report: company overview, portfolio, financials, violations, contacts, outreach history, comparables
- Markdown format, exportable

### Auto-Enrichment
- 4-tier cascade with address-first strategy
- Google Places, NY DOS, Web Crawl, Hunter.io
- SQLite-backed cache (survives restarts)
- Retry logic (max 3 retries per lead)
- Progress persists across server restarts

## Architecture

```
hpd-leads-app/
├── backend/              # Python FastAPI (Railway)
│   ├── api.py            # REST API + enrichment scheduler
│   ├── src/
│   │   ├── ingest/       # HPD, PLUTO, Violations API clients
│   │   ├── transform/    # Normalize & aggregate to leads
│   │   ├── score/        # Scoring V2 + revenue estimation
│   │   ├── enrich/       # Google Places, NY DOS, Hunter, Web Crawl
│   │   └── storage/      # SQLite persistence + caching
│   └── config/           # Scoring weights YAML
├── frontend/             # React + TypeScript (Vercel)
│   ├── components/       # Dashboard, LeadTable, LeadDetail
│   └── services/         # API client
└── docs/                 # Archived reviews
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | Health check |
| `/api/health/detailed` | GET | Comprehensive diagnostics |
| `/api/leads` | GET | Get leads with filtering + pagination |
| `/api/leads/{id}` | GET | Get single lead (from DB) |
| `/api/leads/{id}` | PATCH | Update status, pipeline, follow-up, priority |
| `/api/leads/{id}/due-diligence` | GET | Generate due diligence report |
| `/api/leads/{id}/outreach-event` | POST | Log outreach event |
| `/api/leads/{id}/outreach-events` | GET | Get outreach history |
| `/api/stats` | GET | Detailed statistics (SQL aggregation) |
| `/api/follow-ups` | GET | Leads with follow-ups due |
| `/api/alerts` | GET | Change detection alerts |
| `/api/enrich/batch` | POST | Start batch enrichment |
| `/api/enrich/status` | GET | Enrichment progress |
| `/api/estimate-revenue` | POST | Run revenue estimation |
| `/api/violations/refresh` | POST | Fetch HPD violations |
| `/api/rescore` | POST | Re-score all leads (V2) |
| `/api/refresh` | POST | Refresh from HPD |
| `/api/refresh/check-updates` | POST | Check for data changes |

## Local Development

### Backend

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn api:app --reload --port 8000
# API at http://localhost:8000
# Docs at http://localhost:8000/docs
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
ANTHROPIC_API_KEY=sk-ant-...            # AI summaries
GOOGLE_PLACES_API_KEY=AIza...           # Google Places enrichment
HUNTER_API_KEY=...                      # Hunter.io email finder
CORS_ORIGINS=https://hpd-leads-app.vercel.app,http://localhost:5173
```

### Frontend (Vercel)

```
VITE_API_URL=https://hpd-leads-app-production.up.railway.app
VITE_GOOGLE_MAPS_KEY=AIza...
```

## Data Sources

- **HPD Buildings:** `https://data.cityofnewyork.us/resource/tesw-yqqr.json`
- **HPD Contacts:** `https://data.cityofnewyork.us/resource/feu5-w2e2.json`
- **HPD Violations:** `https://data.cityofnewyork.us/resource/wvxf-dwi5.json`
- **PLUTO (Building Classes):** `https://data.cityofnewyork.us/resource/64uk-42ks.json`
- **NY DOS Corporations:** `https://data.ny.gov/resource/n9v6-gdp6.json`

## License

Private - Michael Bloom
