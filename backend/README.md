# Double Edge - Backend

Python FastAPI backend for the Double Edge NYC housing intelligence platform.

## Quick Start

```bash
pip install -r requirements.txt
python -m uvicorn api:app --reload --port 8000
```

API docs at http://localhost:8000/docs

## Architecture

```
backend/
├── api.py                    # FastAPI app with all endpoints
├── start.py                  # Gunicorn startup script (Railway)
├── Dockerfile                # Railway deployment
├── requirements.txt          # Python dependencies
├── config/
│   └── scoring_weights.yaml  # Scoring V2 configuration
├── src/
│   ├── ingest/
│   │   ├── hpd_client.py     # HPD Buildings & Contacts API
│   │   ├── pluto_client.py   # PLUTO building classification
│   │   └── hpd_violations.py # HPD Violations API
│   ├── transform/
│   │   ├── normalize.py      # Building data normalization
│   │   └── aggregate.py      # Lead aggregation + entity classification
│   ├── score/
│   │   ├── scorer.py         # Scoring V2 (8 dimensions)
│   │   └── revenue.py        # Revenue estimation
│   ├── enrich/
│   │   ├── enricher.py       # Enrichment orchestrator
│   │   ├── contact_sources.py # Google Places, Hunter.io, MultiSourceEnricher
│   │   ├── ny_dos.py         # NY DOS corporation lookup (with SQLite cache)
│   │   └── web_crawl.py      # Web crawling for phone/email
│   └── storage/
│       └── database.py       # SQLite database (leads, caches, events, alerts)
├── data/                     # SQLite database files (gitignored)
├── scripts/                  # Utility scripts
├── tasks/
│   ├── todo.md               # Task tracker
│   └── lessons.md            # Lessons learned
└── docs/                     # Architecture documentation
```

## Key API Endpoints

See root README.md for the full endpoint list.

## Database Schema

The SQLite database (`data/leads.db`) contains:

- **leads** - 102k+ lead records with scores, contacts, revenue, violations, pipeline stage
- **lead_user_data** - User-entered outreach status and notes (legacy)
- **enrichment_cache** - Cached enrichment results
- **outreach_attempts** - Legacy outreach log
- **outreach_events** - Pipeline outreach events (Phase 5.3)
- **enrichment_jobs** - Background enrichment job tracking
- **dos_cache** - Persisted NY DOS lookup cache
- **places_cache** - Persisted Google Places lookup cache
- **change_alerts** - Data change detection alerts

## Environment Variables

```
PORT=8000                              # Server port (Railway sets this)
DATABASE_PATH=/data/leads.db           # SQLite path (Railway volume)
ANTHROPIC_API_KEY=sk-ant-...           # AI summaries
GOOGLE_PLACES_API_KEY=AIza...          # Google Places enrichment
HUNTER_API_KEY=...                     # Hunter.io email finder
CORS_ORIGINS=https://hpd-leads-app.vercel.app,http://localhost:5173
```
