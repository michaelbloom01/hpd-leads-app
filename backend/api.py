"""
FastAPI server for HPD Leads Pipeline.

Thin entry point that registers routers and handles startup/shutdown.
All route logic lives in src/routers/.

SQLAlchemy async sessions are available via the `get_session` dependency
for new code. Existing SQLite code continues to work via `get_database()`.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.storage.database import get_database
from src.services.cache_manager import get_cache
from src.services.violations_service import ViolationsService

# SQLAlchemy async session (new code path)
from src.db.session import get_session  # noqa: F401 -- re-exported for router imports

# Routers
from src.routers.auth import router as auth_router
from src.routers.leads import router as leads_router
from src.routers.enrichment import router as enrichment_router
from src.routers.pipeline import router as pipeline_router
from src.routers.export import router as export_router
from src.routers.admin import router as admin_router
try:
    from src.routers.agent import router as agent_router
except ImportError:
    agent_router = None  # anthropic SDK not installed — agent feature disabled

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="HPD Leads API",
    description="API for NYC HPD property management lead generation",
    version="2.0.0",
)

# Enable CORS for frontend
_cors_origins_env = os.environ.get("CORS_ORIGINS", "")
_allowed_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()] if _cors_origins_env else []
_allowed_origins.extend(["http://localhost:5173", "http://localhost:3000", "http://localhost:3001", "http://localhost:3002"])

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(auth_router)
app.include_router(leads_router)
app.include_router(enrichment_router)
app.include_router(pipeline_router)
app.include_router(export_router)
app.include_router(admin_router)
if agent_router is not None:
    app.include_router(agent_router)


# ---------------------------------------------------------------------------
# Root endpoint
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    cache = get_cache()
    if cache.startup_state == "ready":
        status = "ok"
    elif cache.startup_state == "degraded":
        status = "degraded"
    else:
        status = "starting"
    return {"status": status, "service": "hpd-leads-api"}


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup_load():
    """Load leads from SQLite on startup and run background tasks."""
    cache = get_cache()
    cache.startup_complete = False
    cache.startup_state = "starting"
    cache.startup_error = None
    asyncio.create_task(_startup_load_background())


async def _startup_load_background():
    """Background wrapper that runs startup in a thread pool."""
    cache = get_cache()
    try:
        await asyncio.to_thread(_startup_load_sync)
        cache.startup_state = "ready"
        cache.startup_error = None
    except Exception as e:
        logger.error(f"Startup failed (server will start with empty cache): {e}", exc_info=True)
        cache.startup_state = "degraded"
        cache.startup_error = str(e)
    finally:
        cache.startup_complete = True
        logger.info("Startup: _startup_complete = True")


def _startup_load_sync():
    """Synchronous startup logic — runs in a thread pool."""
    cache = get_cache()

    # 1. Database backup (best-effort)
    try:
        from scripts.backup_db import backup_database, DB_PATH
        if DB_PATH.exists():
            backup_path = backup_database(max_backups=7)
            logger.info(f"Startup: Database backed up to {backup_path}")
    except Exception as e:
        logger.warning(f"Startup: Backup failed (non-fatal): {e}")

    # 2. Verify Socrata endpoints
    _ping_socrata_endpoints()

    # 3. Load leads
    db = get_database()
    loaded = db.load_all_leads()

    if loaded:
        cache.leads = loaded
        cache.last_refresh = db.get_last_refresh_time()
        logger.info(f"Startup: Loaded {len(loaded)} leads from database")

        # 4. Auto-compute revenue
        _startup_compute_revenue(db, cache)

        # 5. Check enrichment jobs
        _startup_check_enrichment(db)

        # 6. Check stale data
        _startup_check_stale_data(db, cache)
    else:
        logger.info("Startup: No leads in database, run /api/refresh to populate")


def _ping_socrata_endpoints():
    """Verify Socrata dataset endpoints are reachable at startup."""
    import requests as _req
    from src.ingest.hpd_client import BUILDINGS_ENDPOINT, CONTACTS_ENDPOINT
    from src.ingest.pluto_client import PLUTO_ENDPOINT
    from src.ingest.hpd_violations import HPD_VIOLATIONS_ENDPOINT

    endpoints = {
        "HPD Buildings": BUILDINGS_ENDPOINT, "HPD Contacts": CONTACTS_ENDPOINT,
        "PLUTO": PLUTO_ENDPOINT, "HPD Violations": HPD_VIOLATIONS_ENDPOINT,
    }
    for name, url in endpoints.items():
        try:
            resp = _req.get(url, params={"$limit": 1}, timeout=10)
            if resp.status_code == 404:
                logger.warning(f"R7: {name} dataset returned 404 — dataset ID may have changed!")
            elif resp.status_code >= 400:
                logger.warning(f"R7: {name} dataset returned HTTP {resp.status_code}")
            else:
                logger.info(f"R7: {name} dataset OK ({resp.status_code})")
        except Exception as e:
            logger.warning(f"R7: {name} dataset unreachable ({e})")


def _startup_compute_revenue(db, cache):
    """Auto-compute revenue estimates if not yet computed."""
    revenue_computed = db.get_setting("revenue_computed_at")
    revenue_version = db.get_setting("revenue_formula_version")
    if not revenue_computed or revenue_version != "2":
        logger.info("Startup: Computing revenue estimates for all leads...")
        from src.score.revenue import estimate_revenue
        batch_updates = []
        for lead in cache.leads:
            rev = estimate_revenue(lead)
            lead.estimated_monthly_revenue = rev["estimated_monthly_revenue"]
            lead.estimated_annual_revenue = rev["estimated_annual_revenue"]
            if rev["estimated_monthly_revenue"] > 0:
                batch_updates.append((lead.lead_id, rev["estimated_monthly_revenue"], rev["estimated_annual_revenue"]))
        db.batch_update_revenue(batch_updates)
        db.set_setting("revenue_computed_at", datetime.now().isoformat())
        db.set_setting("revenue_formula_version", "2")
        logger.info(f"Startup: Revenue estimated for {len(batch_updates)} leads")
    else:
        logger.info(f"Startup: Revenue already computed (v2) at {revenue_computed}")


def _startup_check_enrichment(db):
    """Check for incomplete enrichment jobs."""
    active_job = db.get_active_enrichment_job()
    if active_job and active_job["status"] == "running":
        job_started = active_job.get("started_at")
        is_stale = False
        if job_started:
            try:
                started_dt = datetime.fromisoformat(job_started)
                if (datetime.now() - started_dt) > timedelta(hours=24):
                    is_stale = True
            except (ValueError, TypeError):
                is_stale = True
        if is_stale:
            logger.warning(f"Startup: Enrichment job {active_job['id']} is stale, marking interrupted")
            db.update_enrichment_job(active_job["id"], status="interrupted", error="Stale job detected on startup (>24h)")
        else:
            remaining = active_job.get("lead_ids_remaining", [])
            if remaining:
                logger.info(f"Startup: Incomplete enrichment job with {len(remaining)} leads remaining")


def _startup_check_stale_data(db, cache):
    """Check for stale data and create alerts."""
    if cache.last_refresh and (datetime.now() - cache.last_refresh) > timedelta(days=7):
        days_stale = (datetime.now() - cache.last_refresh).days
        db.add_change_alert("stale_data", f"Data hasn't been refreshed in {days_stale} days.", details={"days_stale": days_stale})
        logger.warning(f"Startup: Data is {days_stale} days stale")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
