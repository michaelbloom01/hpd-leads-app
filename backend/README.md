# Double Edge - Backend

Python FastAPI backend for the Double Edge NYC housing intelligence platform.

> Context refresh (Feb 24, 2026): Backend is in a transition state. PostgreSQL-backed routers are primary, while some legacy SQLite-based paths still exist and are being converged.

## Quick Start

```bash
pip install -r requirements.txt
python -m uvicorn api:app --reload --port 8000
```

API docs at http://localhost:8000/docs

## Architecture (Current)

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
│   ├── db/
│   │   └── session.py        # Async SQLAlchemy session (PostgreSQL)
│   ├── worker.py             # Celery app bootstrap (Redis broker/backend)
│   ├── tasks/
│   │   ├── ingest.py         # Ingestion tasks
│   │   ├── enrich.py         # Enrichment job lifecycle task
│   │   └── score.py          # Scoring tasks
│   └── storage/
│       └── database.py       # Legacy SQLite path (transitioning out of active runtime)
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

Primary runtime store is PostgreSQL via SQLAlchemy models and Alembic migrations.
Legacy SQLite tables remain for compatibility during architecture convergence.

Key entities include:

- `leads` - lead-level canonical read model for UI workflows
- `buildings` + management tables - building-level and ownership linkages
- `outreach_events` - pipeline activity history
- `smart_lists` - saved segment definitions and snapshots
- `ingestion_jobs` / jobs-related tables - operational tracking

## Environment Variables

```bash
PORT=8000
DATABASE_URL=postgresql+asyncpg://...
JWT_SECRET=...
ANTHROPIC_API_KEY=...
GOOGLE_PLACES_API_KEY=...
HUNTER_API_KEY=...
REDIS_URL=redis://...
CORS_ORIGINS=https://...,http://localhost:5173
ENABLE_LEGACY_SQLITE_ROUTERS=0
```

Notes:
- `ENABLE_LEGACY_SQLITE_ROUTERS` is opt-in and defaults to disabled; PostgreSQL-first routing is now the default runtime.
- Enrichment batch jobs are queued through `/api/v1/jobs/enrichment/start` and dispatched to Celery when available, with in-process async fallback for local reliability.
- Canonical job lifecycle statuses are `queued`, `running`, `succeeded`, `failed` (legacy `completed` is normalized for compatibility).
- Jobs API accepts source-style aliases for reruns (for example `energy_grades` -> `energy`, `aep_designations` -> `aep`).

## Execution Readiness Focus

Implementation is organized around three tracks:

1. Runtime convergence (single canonical PostgreSQL runtime path)
2. Durable async jobs (queue + worker lifecycle)
3. Delivery confidence (migration safety + critical path tests + CI gates)

See root architecture plan and session notes for current gate status.
