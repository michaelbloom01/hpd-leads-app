# HPD Leads Backend — Agent Context

## What This Project Does

Full-stack lead generation platform for acquiring Property Management companies in NYC:

1. **Ingest** - Pull 200k+ buildings from HPD + PLUTO classification data
2. **Transform** - Group by management company, create ~100k leads
3. **Score** - Rank by portfolio size, building types, professional indicators
4. **Enrich** - Auto-find phone, email, website via web search + NY DOS
5. **Revenue** - Estimate mgmt fee revenue per lead (Units x Avg Rent x 5%)
6. **Violations** - Aggregate HPD violations per lead (auto-computed at startup)
7. **Serve** - REST API for React frontend with filtering, pagination, export

## Live Deployment

- **Backend:** https://hpd-leads-app-production.up.railway.app
- **Frontend:** https://frontend-nine-psi-58.vercel.app
- **Database:** SQLite with Railway persistent storage

## Quick Start

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn api:app --reload --port 8000
# API at http://localhost:8000
# Docs at http://localhost:8000/docs
```

## Key Files

| File | Purpose |
|------|---------|
| `api.py` | FastAPI app (~3,200 lines) - REST endpoints + startup tasks |
| `src/score/revenue.py` | Revenue estimation (Units x Avg Rent x 5% fee) |
| `src/score/scorer.py` | Lead scoring algorithm |
| `src/ingest/hpd_client.py` | HPD Buildings & Contacts API client |
| `src/ingest/pluto_client.py` | PLUTO building classification lookup |
| `src/ingest/hpd_violations.py` | HPD violations API client |
| `src/transform/aggregate.py` | Group buildings into leads |
| `src/enrich/enricher.py` | Sequential enrichment orchestration |
| `src/enrich/web_crawl.py` | DuckDuckGo/Bing/Google search + scraping |
| `src/enrich/ny_dos.py` | NY DOS corporation registry lookup |
| `src/enrich/ai_summary.py` | Claude AI company descriptions |
| `src/storage/database.py` | SQLite persistence (leads + enrichment jobs + settings) |
| `start.py` | Gunicorn startup (300s timeout for long startup) |

## Critical: Revenue Calculation (revenue.py)

**building_types fields are BUILDING COUNTS, not unit counts.**

The `building_types.condo`, `.rental_elevator`, etc. count how many buildings of each type, NOT how many units. Revenue estimation distributes `total_units` proportionally across building types using the largest-remainder method to ensure exact unit sums.

Formula: `Total Units x Avg Rent (by borough & building type) x 5% mgmt fee`

- Borough rents: StreetEasy + Census ACS averages
- Condo/co-op adjustment: 60% of rental rate
- Revenue breakdown per-type is returned in API for auditability

## Startup Behavior

On startup, `api.py` runs:
1. Load all leads from SQLite (~100k leads, ~10s)
2. Compute revenue estimates if `revenue_formula_version != '2'` (batch update, ~10s)
3. Auto-compute violations in **background thread** if not yet done (HPD API calls)
4. Resume incomplete enrichment jobs

**Important:** Violations run in `asyncio.to_thread()` to avoid blocking the event loop. The gunicorn timeout is 300s to handle the long startup.

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/leads` | GET | Filtered lead list (multi-borough via `boro=MAN,BKLYN`) |
| `/api/leads/{id}` | GET | Single lead detail with revenue_breakdown |
| `/api/leads/{id}` | PUT | Update lead (notes, status, pipeline) |
| `/api/status` | GET | Pipeline status |
| `/api/stats` | GET | Aggregate statistics |
| `/api/enrich/status` | GET | Current enrichment progress |
| `/api/enrich/batch` | POST | Start batch enrichment |
| `/api/violations/refresh` | POST | Refresh violations from HPD |
| `/api/export/csv` | GET | CSV export |

### Multi-Borough Filter

The `boro` parameter accepts comma-separated values (e.g., `?boro=MANHATTAN,BROOKLYN`). Backend uses `WHERE boro IN (?,?)` SQL query.

## Persistent Settings (app_settings table)

| Key | Purpose |
|-----|---------|
| `revenue_computed_at` | When revenue was last calculated |
| `revenue_formula_version` | Current formula version (`2` = fixed) |
| `violations_computed_at` | When violations were last fetched |
| `enrichment_completed_at` | When enrichment batch last finished |

## Data Model

### Lead Fields

```python
lead_id: str           # Unique hash
agent_name: str        # Management company name
owner_name: str        # Building owner
portfolio_size: int    # Number of buildings
total_units: int       # Total residential units
buildings: List[str]   # Building addresses
boros: List[str]       # Boroughs served
building_types: {      # PLUTO classification (BUILDING COUNTS, not unit counts!)
    condo, coop, rental_elevator, rental_walkup, ...
}
score: float           # 0-100 lead score
phone, email, website  # Contact info
estimated_monthly_revenue: float   # Monthly mgmt fee estimate
estimated_annual_revenue: float    # Annual mgmt fee estimate
violation_count: int               # Total HPD violations
violations_per_unit: float         # Violations density metric
pipeline_stage: str    # research, first_contact, follow_up, meeting_scheduled, etc.
enrichment_status      # none, partial, complete, failed
```

## Frontend Components

| Component | Purpose |
|-----------|---------|
| `Dashboard.tsx` | Overview metrics, enrichment status, top leads |
| `LeadTable.tsx` | Filterable/sortable lead list with multi-borough toggles |
| `LeadDetail.tsx` | 5-tab detail view (Overview, Contacts, Pipeline, Buildings, DD) |

### Key UX Decisions
- Violations column shows **per-unit density** (not raw count) - sortable
- Score shows A/B/C tier labels alongside number
- Data quality shown as visual dots (not text jargon)
- Revenue breakdown expandable with actual per-lead numbers
- Maps hidden gracefully when Google API key is missing
- Pipeline uses dropdown selector (not tiny button bar)
- Borough filter is multi-select toggle buttons

## Environment Variables

```bash
# Required for AI summaries
ANTHROPIC_API_KEY=sk-ant-...

# Optional
NYC_OPEN_DATA_APP_TOKEN=...    # Higher rate limits
CORS_ORIGINS=https://...       # Restrict CORS (default: *)
VITE_GOOGLE_MAPS_KEY=...       # For static maps in frontend
```

## Database

SQLite with tables:
- `leads` - All lead data
- `metadata` - Refresh timestamps
- `enrichment_jobs` - Persistent job tracking
- `app_settings` - Key/value settings (revenue/violations timestamps, versions)

Location: `./leads.db` (Railway persistent volume)

## Deployment

### Backend (Railway)
1. Uses `Dockerfile` for build
2. Persistent volume mounted at `/app`
3. Auto-deploys from GitHub `master` branch
4. Gunicorn with 300s timeout

### Frontend (Vercel)
1. Vite + React + TypeScript
2. Manual deploy: `cd frontend && npx vercel --prod --yes`
3. Production URL: https://frontend-nine-psi-58.vercel.app
4. Env vars: `VITE_API_URL`, `VITE_GOOGLE_MAPS_KEY`

## Known Issues / Future Work

- Google Static Maps API key needs billing/enabling in GCP console
- Violations take several minutes to compute on first startup (59k buildings)
- Some leads are co-op complexes (e.g., Deepdale Gardens with 128k units) which may not be true PE targets
- LinkedIn search gets 429'd by Google; consider alternative data sources
