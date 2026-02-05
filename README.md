# HPD Leads App

NYC property management lead generation platform for acquisition targets. Identifies **management companies** from the HPD database, scores them, enriches with contact info, and provides a clean UI for outreach.

**Live App:** https://hpd-leads-app.vercel.app  
**Backend API:** https://hpd-leads-app-production.up.railway.app  
**GitHub:** https://github.com/michaelbloom01/hpd-leads-app

## What It Does

1. **Fetches ALL buildings** from NYC HPD database (200k+ buildings)
2. **Joins with PLUTO data** for building classification (condo, coop, rental, etc.)
3. **Groups by management company** to create ~100k leads
4. **Scores leads** based on portfolio size, building types, professional indicators
5. **Auto-enriches** top leads with phone, email, website via web crawling
6. **Displays in a filterable UI** with click-to-call/email functionality

## Current Status (Feb 2026)

| Metric | Value |
|--------|-------|
| Total Leads | 102,505 |
| High-Value Leads (10+ buildings) | ~1,300 |
| With Phone | 70+ (growing via auto-enrichment) |
| With Email | 100+ |
| Building Type Coverage | 100% (PLUTO data) |

## Key Features

### Dashboard
- Key metrics at a glance (total leads, enriched count, top scores)
- "Ready to Contact" quick-access card
- Portfolio size and units/building charts
- Building type distribution

### Lead Table
- Filter by borough, score, portfolio size, building type
- Filter for leads with phone/email/website
- Bulk selection and export to CSV
- Click any lead to see full details

### Lead Detail Modal
- **Contact info front and center** - phone, email, website with one-click actions
- Click-to-call (`tel:`) and click-to-email (`mailto:`)
- Portfolio composition (condos, coops, rentals breakdown)
- Map showing all building locations
- AI-generated company summaries
- Outreach tracking with status and notes

### Auto-Enrichment
- Continuous background scheduler enriches all high-value leads
- Multi-source search: DuckDuckGo → Bing → Google (rate-limit resilient)
- NY DOS registry lookup for corporation info
- Progress persists across server restarts

## Architecture

```
hpd-leads-app/
├── backend/              # Python FastAPI (Railway)
│   ├── api.py            # REST API + enrichment scheduler
│   ├── src/
│   │   ├── ingest/       # HPD & PLUTO API clients
│   │   ├── transform/    # Normalize & aggregate to leads
│   │   ├── score/        # Scoring algorithm
│   │   ├── enrich/       # Web crawl, NY DOS, AI summary
│   │   └── storage/      # SQLite persistence
│   └── tests/            # Integration tests
├── frontend/             # React + TypeScript (Vercel)
│   ├── components/       # Dashboard, LeadTable, LeadDetail
│   ├── services/         # API client
│   └── package.json
└── COMPREHENSIVE_REVIEW.md  # Full project review & roadmap
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | Health check with enrichment status |
| `/api/leads` | GET | Get leads with filtering |
| `/api/leads/{id}` | GET | Get single lead |
| `/api/status` | GET | Pipeline status |
| `/api/stats` | GET | Detailed statistics |
| `/api/enrichment/queue` | GET | What's waiting for enrichment |
| `/api/enrich/status` | GET | Current enrichment progress |
| `/api/enrich/batch` | POST | Start batch enrichment |
| `/api/refresh` | POST | Refresh from HPD |

### Query Parameters for `/api/leads`

- `min_score` - Filter by minimum score
- `min_portfolio` - Filter by minimum portfolio size
- `boro` - Filter by borough
- `has_phone` - Filter by phone availability
- `has_email` - Filter by email availability
- `limit` - Max results (default 100)
- `offset` - Pagination offset

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
ANTHROPIC_API_KEY=sk-ant-...     # For AI summaries
CORS_ORIGINS=https://your-frontend.vercel.app  # Optional, defaults to *
```

### Frontend (Vercel)

```
VITE_API_URL=https://hpd-leads-app-production.up.railway.app
VITE_GOOGLE_MAPS_KEY=AIza...     # For building location maps
```

## Deployment

### Backend → Railway

1. Connect Railway to GitHub repo
2. Set root directory to `backend`
3. Add environment variables
4. Uses `Dockerfile` for build

### Frontend → Vercel

1. Connect Vercel to GitHub repo
2. Set root directory to `frontend`
3. Add environment variables
4. Auto-deploys on push

## Data Sources

- **HPD Buildings:** `https://data.cityofnewyork.us/resource/tesw-yqqr.json`
- **HPD Contacts:** `https://data.cityofnewyork.us/resource/feu5-w2e2.json`
- **PLUTO (Building Classes):** `https://data.cityofnewyork.us/resource/64uk-42ks.json`

## Enrichment Sources

1. **Web Search** - DuckDuckGo, Bing, Google (with rate limiting)
2. **NY DOS** - Corporation registry for registered agent info
3. **Website Scraping** - Extract phone, email from company websites
4. **AI Summary** - Claude-generated company descriptions

## License

Private - Michael Bloom
