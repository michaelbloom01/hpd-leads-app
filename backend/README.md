# Double Edge - Backend

Python FastAPI backend for the Double Edge NYC housing intelligence platform.

> Context refresh (Mar 6, 2026): PostgreSQL-backed API + worker runtime are the production path. Some legacy SQLite compatibility code still exists, but lead generation, reconciliation, and quality checks now run through PostgreSQL + Celery on Railway.

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
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=...
TEST_USER_EMAIL=test@example.com
TEST_USER_PASSWORD=...
ANTHROPIC_API_KEY=...
GOOGLE_PLACES_API_KEY=...
HUNTER_API_KEY=...
REDIS_URL=redis://...
CORS_ORIGINS=https://...,http://localhost:5173
```

Notes:
- Enrichment batch jobs are queued through `/api/v1/jobs/enrichment/start` and dispatched to Celery when available, with in-process async fallback for local reliability.
- Canonical job lifecycle statuses are `queued`, `running`, `succeeded`, `failed` (legacy `completed` is normalized for compatibility).
- Jobs API accepts source-style aliases for reruns (for example `energy_grades` -> `energy`, `aep_designations` -> `aep`).
- Worker and queue health can be inspected at `GET /api/v1/jobs/worker-health`.
- DB pool utilization is exposed at `GET /api/health/db-pool`.

## Execution Readiness Focus

The production-critical convergence slice is now live:

1. Canonical PostgreSQL runtime path for lead generation and read models
2. Durable async jobs through Railway worker + Redis
3. Delivery confidence via regression tests, job observability, and production verification runs

See root session notes and changelog for the latest post-deploy cleanup status.

## Operations Runbooks

- `docs/08-operations-runbook.md`
- `docs/09-postgres-backup-restore.md`
