# HPD Leads App — Agent Context

## What This Project Does

Enterprise-grade lead generation platform for acquiring Property Management businesses in NYC.
Two distinct use cases / personas:

1. **Leads tab (PE Searcher)** — Find PM companies to acquire based on deal criteria (portfolio size, units, revenue, borough, entity type)
2. **Buildings tab (Existing PM Operator)** — Find buildings ripe for high-value outreach based on churn signals

## Live Deployment

| Layer | URL | Platform |
|-------|-----|----------|
| Frontend | https://frontend-nine-psi-58.vercel.app | Vercel (auto-deploy from GitHub) |
| Backend API | https://hpd-leads-app-production.up.railway.app | Railway (auto-deploy from GitHub) |
| Database | Railway managed PostgreSQL 16 | Railway |
| Branch | `feature/enterprise-rearchitecture` | GitHub |

## Architecture

```
frontend/          React + TypeScript + Vite + shadcn/ui + TanStack Table/Query
backend/
  src/
    routers/       FastAPI routers (leads.py, buildings.py, agent.py, quality.py, scoring.py, ...)
    agent/         AI Agent: orchestrator.py, tools.py, memory.py, types.py, system_prompt.py
    db/            session.py — SQLAlchemy 2.0 async engine + get_sync_url()
    storage/       database.py — LEGACY SQLite wrapper (DO NOT USE for new code)
    tasks/         score.py — Celery task (no-op fallback for local dev)
    enrich/        contact_sources.py, ai_summary.py, web_crawl.py
  scripts/
    run_ingestion.py   — NYC Open Data ingest (HPD + PLUTO)
    run_scoring.py     — Churn scoring runner
    score_buildings.sql — Pure SQL scoring implementation
  alembic/         Database migrations
```

## Database: PostgreSQL via SQLAlchemy 2.0

### Connection

```python
# Async (FastAPI endpoints)
from src.db.session import get_session
async def endpoint(session: AsyncSession = Depends(get_session)): ...

# Sync (agent tools, Alembic)
from src.db.session import get_sync_url
engine = create_engine(get_sync_url(), ...)
```

### Key Tables

| Table | Rows | Purpose |
|-------|------|---------|
| `leads` | 38,494 | PM company leads (aggregated from buildings) |
| `buildings` | 179,985 | Individual buildings with PLUTO data + churn scores |
| `hpd_complaints` | ~200K | Raw HPD complaint signals |
| `hpd_violations` | ~150K | Raw HPD violation signals |
| `scoring_configs` | 1 | Configurable scoring weights |
| `building_score_history` | — | Historical score snapshots |
| `data_quality_log` | — | Ingestion audit log |
| `ingestion_jobs` | — | Job tracking |

### IMPORTANT: Never use `src/storage/database.py` for new code
That file is the legacy SQLite wrapper. All new endpoints use `src.db.session.get_session`.

## Key API Endpoints

### Leads (PE Searcher Persona)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/leads` | GET | Filtered/sorted leads with pagination |
| `/api/v1/leads/{id}` | GET | Single lead detail |
| `/api/v1/leads/{id}` | PATCH | Update pipeline stage, status, notes, priority |
| `/api/v1/leads/{id}/outreach-events` | GET/POST | Pipeline event log |

### Buildings (PM Operator Persona)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/buildings` | GET | Buildings sorted by churn_score |
| `/api/v1/buildings/{bbl}` | GET | Single building detail |
| `/api/v1/buildings/{bbl}/score` | POST | Rescore single building |

### Agent (AI Assistant)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/agent/chat` | POST | SSE streaming chat (Claude tool-use) |
| `/api/agent/conversations` | GET | List past conversations |
| `/api/agent/conversations/{id}` | GET/DELETE | Conversation history |

### Quality & Scoring
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/quality/summary` | GET | Ingestion job summary |
| `/api/v1/quality/coverage` | GET | Signal coverage per borough/type |
| `/api/v1/quality/data-health` | GET | Data health badge (used in footer) |
| `/api/v1/scoring/configs` | GET/POST | Scoring weight configurations |

## AI Agent Architecture

```
AgentPanel.tsx (slide-out panel, Cmd+K)
  → AgentChat.tsx (SSE message loop, safety 90s timeout)
    → AgentMessage.tsx (renders leads table, scripts, briefing, confirmation cards)

POST /api/agent/chat
  → orchestrator.py (async generator, Claude tool-use loop)
    → asyncio.to_thread(execute_tool, ...) ← IMPORTANT: tools are blocking I/O
      → tools.py (query_leads, get_lead_details, update_leads_batch, ...)
        → _pg_conn() → PostgreSQL ← All tools use PostgreSQL, NOT SQLite
```

### Critical: execute_tool Must Run in Thread Pool

`execute_tool()` makes synchronous PostgreSQL network calls. It MUST be called with:
```python
result = await asyncio.to_thread(execute_tool, block.name, block.input)
```
Calling it directly blocks the async event loop and prevents SSE events from being flushed to the client → 90-second timeout.

### Agent Tools → PostgreSQL

All 7 agent tools use `_pg_conn()` in `tools.py`:
- `query_leads` → `_pg_leads_filtered()` — PostgreSQL leads table
- `get_lead_details` → `_pg_lead_by_id()` — PostgreSQL leads table
- `update_leads_batch` → direct UPDATE via `_pg_conn()`
- `enrich_leads_batch` → validates IDs via `_pg_lead_by_id()`, writes via `_pg_conn()`
- `generate_cold_call_scripts` → loads leads via `_pg_lead_by_id()`
- `compile_email_briefing` → loads leads via `_pg_lead_by_id()`
- `get_stats` → aggregate SELECT from PostgreSQL

**Never** wire agent tools to `get_database()` (SQLite) — that data doesn't exist.

## Scoring System

Churn probability score (0-100) computed per building using:
- `complaint_spike` (w=17) — 6mo complaint rate vs prior 6mo
- `violation_trend` (w=12) — recent violations / unit_count
- `hpd_litigation` (w=9) — active AEP / litigation / harassment flags
- `building_size` (w=5) — based on unit_count from PLUTO
- `ownership_change` (w=20) — ACRIS data (not yet loaded, contributes 0)
- Others (energy, permits, evictions, facade) — not yet loaded

Weights stored in `scoring_configs` table (configurable from Settings page).
Score categories: `hot` (≥70), `warm` (≥40), `stable` (<40).

SQL implementation: `backend/scripts/score_buildings.sql`

## Data in Production (Feb 2026)

- 38,494 leads, all at `pipeline_stage=new`, `outreach_status=new`
- 179,985 buildings with PLUTO unit counts and churn scores
- 61,095 buildings with complaint data; 45,586 with violation data
- Enrichment (phone/email/website) at 0% — not yet run against PostgreSQL dataset
- No deal pipeline data (building_management, outreach_events tables are empty)

## Local Development

```bash
# Backend
cd backend
python -m uvicorn src.main:app --reload --port 8000

# Frontend
cd frontend
npm run dev
# Proxies /api → http://localhost:8000 via vite.config.ts

# Migrations
cd backend
alembic upgrade head
```

### Local PostgreSQL
Default: `postgresql+asyncpg://postgres:postgres@localhost:5432/hpd_leads`
Override via `DATABASE_URL` env var.

## Environment Variables

```bash
# Required
DATABASE_URL=postgresql+asyncpg://...   # Set on Railway
ANTHROPIC_API_KEY=sk-ant-...            # For AI agent + summaries

# Optional
CORS_ORIGINS=https://...                # Restrict CORS
NYC_OPEN_DATA_APP_TOKEN=...             # Higher rate limits on NYC APIs
AGENT_MODEL=claude-3-5-sonnet-latest    # Override agent model
SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_TO_DEFAULT  # Email briefings
```

## Deployment

### Backend (Railway)
- Auto-deploys from `feature/enterprise-rearchitecture` branch push
- Uses `Dockerfile` in `backend/`
- `railway up` for manual force-deploy

### Frontend (Vercel)
- Auto-deploys from branch push
- Manual: `cd frontend && npx vercel --prod --yes`
- Env: `VITE_API_URL=https://hpd-leads-app-production.up.railway.app`

## Known Issues / Pending Work

- Enrichment (phone/email/website) hasn't been run against PostgreSQL dataset — 38K leads at 0%
- `feature/enterprise-rearchitecture` branch not yet merged to main
- ACRIS ownership change data not loaded → ownership_change signal always 0
- DOB permits, eviction, energy grade, facade signals not yet loaded
- `building_management` and `outreach_events` tables empty (no pipeline data entered yet)
- `src/storage/database.py` (SQLite) still exists but should be treated as dead code

## Status — Feb 23, 2026

Agent fully functional end-to-end:
- Lead query returns results from PostgreSQL
- Clicking agent leads opens LeadDetail drawer correctly
- Lead table in agent paginated (10 per page, Show More button)
- No 90-second timeout (execute_tool runs in thread pool)
