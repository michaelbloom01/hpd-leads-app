# Double Edge — Agent Context

## Cross-Project Context
For business strategy, deal pipeline, people context, and personal goals: read files in C:\Users\micha\brain\context\. Start with brain\CLAUDE.md for orientation.

## What This Project Does

Dual-purpose NYC housing intelligence platform (formerly HPD Leads).
Two distinct use cases / personas:

1. **Leads tab (PE Searcher)** — Find PM companies to acquire based on deal criteria (portfolio size, units, revenue, borough, entity type)
2. **Buildings tab (Existing PM Operator)** — Find buildings ripe for high-value outreach based on churn signals

## Live Deployment

| Layer | URL | Platform |
|-------|-----|----------|
| Frontend | https://frontend-nine-psi-58.vercel.app | Vercel (auto-deploy from GitHub) |
| Backend API | https://hpd-leads-app-production.up.railway.app | Railway (auto-deploy from GitHub) |
| Database | Railway managed PostgreSQL 16 | Railway |
| Branch | `master` | GitHub |

## Architecture

```
frontend/          React + TypeScript + Vite + shadcn/ui + TanStack Table/Query
backend/
  src/
    routers/       FastAPI routers (leads.py, buildings.py, agent.py, quality.py, scoring.py, ...)
    agent/         AI Agent: orchestrator.py, tools.py, memory.py, types.py, system_prompt.py
    db/            session.py — SQLAlchemy 2.0 async engine + get_sync_url()
    storage/       (legacy SQLite removed)
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
| `leads` | 314,723 | Lead rows materialized from building contacts |
| `buildings` | 179,985 | Individual buildings with PLUTO data + churn scores |
| `hpd_complaints` | ~200K | Raw HPD complaint signals |
| `hpd_violations` | ~150K | Raw HPD violation signals |
| `scoring_configs` | 1 | Configurable scoring weights |
| `building_score_history` | — | Historical score snapshots |
| `data_quality_log` | — | Ingestion audit log |
| `ingestion_jobs` | — | Job tracking |

All endpoints use `src.db.session.get_session` (PostgreSQL via SQLAlchemy 2.0).

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

## Data In Production (Mar 2026)

- 314,723 lead rows currently materialized in PostgreSQL
- 179,985 buildings with PLUTO unit counts and churn scores
- Lead generation now succeeds through the normal Railway worker path
- Lead reconciliation and quality checks also succeed through the worker path
- Multi-link integrity split shows `0` buildings with multiple current links to the same normalized entity
- The remaining zero-link/blank-tail cleanup is still conservative and unresolved:
  - `55,804` leads with zero active building links
  - `54,507` blank display-name leads

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
- Auto-deploys from `master` branch push
- Uses `Dockerfile` in `backend/`
- `railway up` for manual force-deploy
- Worker service: `hpd-leads-worker`
- Important worker deploy note: use `railway up . --path-as-root --service hpd-leads-worker` so Railway builds from `backend/` with the correct Dockerfile

### Frontend (Vercel)
- Auto-deploys from branch push
- Manual: `cd frontend && npx vercel --prod --yes`
- Env: `VITE_API_URL=https://hpd-leads-app-production.up.railway.app`

## Known Issues / Pending Work

- Enrichment (phone/email/website) remains effectively unrun on the current 314k-lead surface
- The remaining zero-link + blank-name lead tail is ambiguous under a high-confidence cleanup policy and still needs a better classifier
- ACRIS ownership change data not loaded -> `ownership_change` signal remains `0`
- DOB permits, eviction, energy grade, facade signals are still incomplete / partially loaded
- Portfolio/building maps now default to persisted coordinates; after fresh building ingest or migration rollout, run the `building_coordinates` job to materialize stored markers for unmapped portfolios
- Some legacy documentation and historical notes still refer to the older ~38k / ~102k lead eras and need to be read as historical context only

## Status — Feb 23, 2026

Agent fully functional end-to-end:
- Lead query returns results from PostgreSQL
- Clicking agent leads opens LeadDetail drawer correctly
- Lead table in agent paginated (10 per page, Show More button)
- No 90-second timeout (execute_tool runs in thread pool)
