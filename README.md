# HPD Leads App

NYC property management lead generation pipeline with web interface. Identifies **management companies** responsible for each building in the HPD database.

**GitHub:** https://github.com/michaelbloom01/hpd-leads-app

## What It Does

1. **Fetches ALL buildings** from NYC HPD database (201,282 buildings)
2. **Identifies the management company** (Agent contact type) for each building
3. **Groups buildings by management company** to create leads
4. **Scores leads** based on portfolio size, professionalism indicators, contact completeness
5. **Enriches leads** by web crawling to find phone, email, website
6. **Displays in a filterable UI** similar to instances.vantage.sh

## Key Data Insight

The HPD "Agent" contact type = **Property Management Company**

| Contact Type | Count | Meaning |
|--------------|-------|---------|
| **Agent** | 153,418 | Property management company |
| SiteManager | 160,905 | Building super |
| CorporateOwner | 119,915 | Owner entity (LLC, Corp) |
| HeadOfficer | 126,349 | Individual at owner |

## Architecture

```
hpd-leads-app/
├── backend/          # Python FastAPI server (Railway)
│   ├── api.py        # REST API endpoints
│   ├── src/          # Pipeline code
│   │   ├── ingest/   # HPD API client
│   │   ├── transform/# Normalize & aggregate
│   │   ├── score/    # Scoring logic
│   │   ├── enrich/   # Web crawl enrichment
│   │   └── publish/  # Google Sheets export
│   └── requirements.txt
├── frontend/         # React + TypeScript (Vercel)
│   ├── components/   # UI components
│   ├── services/     # API client
│   └── package.json
└── README.md
```

## Backend API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Health check |
| `/api/leads` | GET | Get leads with filtering |
| `/api/leads/{id}` | GET | Get single lead |
| `/api/status` | GET | Pipeline status |
| `/api/stats` | GET | Detailed statistics |
| `/api/refresh?full=true` | POST | Refresh from HPD (full=true for all 200k+) |
| `/api/enrich` | POST | Enrich specific leads |
| `/api/enrich/batch` | POST | Auto-enrich top leads |

### Query Parameters for `/api/leads`

- `min_score` - Filter by minimum score
- `min_portfolio` - Filter by minimum portfolio size
- `boro` - Filter by borough (Manhattan, Brooklyn, Queens, Bronx, Staten Island)
- `has_website` - Filter by website availability
- `has_email` - Filter by email availability
- `limit` - Max results (default 100, max 500)
- `offset` - Pagination offset

## Local Development

### Backend

```bash
cd backend
pip install -r requirements.txt
python api.py
# API runs at http://localhost:8000
# Interactive docs at http://localhost:8000/docs
```

### Frontend

```bash
cd frontend
npm install
npm run dev
# UI runs at http://localhost:3000
```

## Frontend Features

- **Vantage.sh-style data table** with inline filters
- **Filters:** search, borough, score range, portfolio size, contact info, management company
- **Sortable columns** (click headers)
- **Pagination** (50 per page)
- **Bulk selection and enrichment**
- **Lead detail modal** with one-click enrichment
- **Quick (10k) or Full (200k+)** data refresh toggle

## Deployment

### Backend → Railway

1. Connect Railway to this repo
2. Set root directory to `backend`
3. Railway auto-detects Python and runs `uvicorn api:app --host 0.0.0.0 --port $PORT`

### Frontend → Vercel

1. Connect Vercel to this repo
2. Set root directory to `frontend`
3. Set environment variable `VITE_API_URL` to your Railway backend URL

## Environment Variables

### Backend (.env)

```
NYC_OPEN_DATA_APP_TOKEN=optional_for_higher_rate_limits
```

### Frontend (.env)

```
VITE_API_URL=https://your-backend.railway.app
```

## Data Sources

- **Buildings:** `https://data.cityofnewyork.us/resource/tesw-yqqr.json` (201,282 records)
- **Contacts:** `https://data.cityofnewyork.us/resource/feu5-w2e2.json` (774,616 records)
