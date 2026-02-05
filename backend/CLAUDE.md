# HPD Leads Backend — Agent Context

## What This Project Does

Full-stack lead generation platform for acquiring Property Management companies in NYC:

1. **Ingest** - Pull 200k+ buildings from HPD + PLUTO classification data
2. **Transform** - Group by management company, create ~100k leads
3. **Score** - Rank by portfolio size, building types, professional indicators
4. **Enrich** - Auto-find phone, email, website via web search + NY DOS
5. **Serve** - REST API for React frontend with filtering, pagination, export

## Live Deployment

- **Backend:** https://hpd-leads-app-production.up.railway.app
- **Frontend:** https://hpd-leads-app.vercel.app
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
| `api.py` | FastAPI app (~2,500 lines) - REST endpoints + enrichment scheduler |
| `src/ingest/hpd_client.py` | HPD Buildings & Contacts API client |
| `src/ingest/pluto_client.py` | PLUTO building classification lookup |
| `src/transform/aggregate.py` | Group buildings into leads |
| `src/enrich/enricher.py` | Sequential enrichment orchestration |
| `src/enrich/web_crawl.py` | DuckDuckGo/Bing/Google search + scraping |
| `src/enrich/ny_dos.py` | NY DOS corporation registry lookup |
| `src/enrich/ai_summary.py` | Claude AI company descriptions |
| `src/score/scorer.py` | Lead scoring algorithm |
| `src/storage/database.py` | SQLite persistence (leads + enrichment jobs) |
| `tests/test_enrichment.py` | Integration tests |

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | Health check + enrichment status |
| `/api/leads` | GET | Filtered lead list |
| `/api/leads/{id}` | GET | Single lead detail |
| `/api/leads/{id}` | PUT | Update lead (notes, status) |
| `/api/status` | GET | Pipeline status |
| `/api/stats` | GET | Aggregate statistics |
| `/api/enrichment/queue` | GET | Unenriched leads queue |
| `/api/enrich/status` | GET | Current enrichment progress |
| `/api/enrich/batch?limit=500` | POST | Start batch enrichment |
| `/api/refresh?full=true` | POST | Refresh from HPD |

## Enrichment System

### How It Works

1. **Continuous Scheduler** - Background task that auto-enriches high-value leads (portfolio >= 10)
2. **Multi-Source Search** - DuckDuckGo → Bing → Google (reduces rate limiting)
3. **NY DOS Lookup** - Corporation registry for registered agent info
4. **Website Scraping** - Extract phone, email, description from company sites
5. **AI Summary** - Claude-generated company descriptions

### Enrichment Persistence

Enrichment jobs persist to SQLite (`enrichment_jobs` table):
- Jobs survive server restarts
- Auto-resume incomplete jobs on startup
- Track progress, completed, failed counts

### Rate Limiting Strategy

- DuckDuckGo first (most lenient)
- Bing as fallback
- Google last with exponential backoff
- 1-3 second delays between requests

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
building_types: {      # PLUTO classification
    condo, coop, rental_elevator, rental_walkup, ...
}
score: float           # 0-100 lead score
phone, email, website  # Contact info
enrichment_status      # none, partial, complete, failed
outreach_status        # new, contacted, interested, closed
```

## Environment Variables

```bash
# Required for AI summaries
ANTHROPIC_API_KEY=sk-ant-...

# Optional
NYC_OPEN_DATA_APP_TOKEN=...    # Higher rate limits
CORS_ORIGINS=https://...       # Restrict CORS (default: *)
```

## Database

SQLite with tables:
- `leads` - All lead data
- `metadata` - Refresh timestamps
- `enrichment_jobs` - Persistent job tracking

Location: `./leads.db` (Railway persistent volume)

## Testing

```bash
cd backend
pytest tests/ -v
```

## Deployment (Railway)

1. Uses `Dockerfile` for build
2. Persistent volume mounted at `/app`
3. Auto-deploys from GitHub `master` branch

## Scoring Algorithm

Primary factors (in `src/score/scorer.py`):
- Portfolio size (more buildings = higher score)
- Building type mix (condos/coops weighted higher)
- Professional indicators (LLC, Corp, Management in name)
- Contact availability bonus

## Compliance

- HPD data: Public domain (NYC Open Data)
- PLUTO data: Public domain (NYC Open Data)
- NY DOS: Public record
- Web crawling: Respects robots.txt, rate limited

## Task Tracking

- `tasks/todo.md` - Current task list
- `tasks/lessons.md` - Learnings from corrections
- `COMPREHENSIVE_REVIEW.md` - Full project review (root dir)

## Related Files

- Frontend: `../frontend/`
- Project Review: `../COMPREHENSIVE_REVIEW.md`
- Main README: `../README.md`
