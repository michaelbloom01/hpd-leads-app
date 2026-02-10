"""
FastAPI server for HPD Leads Pipeline.

Exposes REST endpoints for the frontend to:
- Fetch leads
- Trigger enrichment
- Get pipeline status
"""
import gc
import logging
import threading
from datetime import datetime
from typing import List, Optional, Dict

from fastapi import FastAPI, Query, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.ingest.hpd_client import HPDClient
from src.transform.normalize import normalize_building
from src.transform.aggregate import aggregate_to_leads, Lead, StreamingLeadAggregator
from src.score.scorer import score_leads
from src.enrich.enricher import Enricher
from src.enrich.ny_dos import NYDOSClient
from src.storage.database import get_database

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="HPD Leads API",
    description="API for NYC HPD property management lead generation",
    version="1.0.0",
)

# Enable CORS for frontend
# R9: Use regex-based origin matching so all Vercel preview deploys work automatically
import os
_cors_origins_env = os.environ.get("CORS_ORIGINS", "")
_allowed_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()] if _cors_origins_env else []
# Always allow localhost for dev
_allowed_origins.extend(["http://localhost:5173", "http://localhost:3000", "http://localhost:3001", "http://localhost:3002"])

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_origin_regex=r"https://.*\.vercel\.app",  # R9: matches all Vercel preview/production deploys
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state - loaded from SQLite on startup
_leads_cache: List[Lead] = []
_last_refresh: Optional[datetime] = None
_leads_lock = threading.Lock()  # Protects _leads_cache modifications
_startup_complete = False  # R1: Set True after startup_load finishes (even on error)

# Background enrichment state (enhanced for batch processing)
_enrichment_state: Dict = {
    "running": False,
    "phase": "",  # "dos_lookup", "web_crawl", "complete"
    "progress": 0,
    "total": 0,
    "completed": 0,
    "failed": 0,
    "dos_completed": 0,
    "dos_found": 0,
    "web_completed": 0,
    "web_found": 0,
    "current_lead": None,
    "started_at": None,
    "finished_at": None,
    "error": None,
}
_enrichment_lock = threading.Lock()  # Protects _enrichment_state
_enrichment_stop_requested = False  # Flag to stop enrichment gracefully

# Background refresh state
_refresh_state: Dict = {
    "running": False,
    "phase": None,  # "fetching", "normalizing", "aggregating", "scoring", "persisting"
    "buildings_fetched": 0,
    "total_buildings": 0,
    "leads_created": 0,
    "started_at": None,
    "finished_at": None,
    "error": None,
}
_refresh_lock = threading.Lock()  # Protects _refresh_state


@app.on_event("startup")
async def startup_load():
    """Load leads from SQLite database on startup and resume/start enrichment if needed."""
    global _leads_cache, _last_refresh, _startup_complete
    
    try:
        await _startup_load_inner()
    except Exception as e:
        logger.error(f"Startup failed (server will start with empty cache): {e}", exc_info=True)
    finally:
        _startup_complete = True
        logger.info("Startup: _startup_complete = True")


def _ping_socrata_endpoints():
    """R7: Verify Socrata dataset endpoints are reachable at startup."""
    import requests as _req
    from src.ingest.hpd_client import BUILDINGS_ENDPOINT, CONTACTS_ENDPOINT
    from src.ingest.pluto_client import PLUTO_ENDPOINT
    from src.ingest.hpd_violations import HPD_VIOLATIONS_ENDPOINT
    
    endpoints = {
        "HPD Buildings": BUILDINGS_ENDPOINT,
        "HPD Contacts": CONTACTS_ENDPOINT,
        "PLUTO": PLUTO_ENDPOINT,
        "HPD Violations": HPD_VIOLATIONS_ENDPOINT,
    }
    for name, url in endpoints.items():
        try:
            resp = _req.get(url, params={"$limit": 1}, timeout=10)
            if resp.status_code == 404:
                logger.warning(f"R7: {name} dataset returned 404 — dataset ID may have changed! URL: {url}")
            elif resp.status_code >= 400:
                logger.warning(f"R7: {name} dataset returned HTTP {resp.status_code}: {url}")
            else:
                logger.info(f"R7: {name} dataset OK ({resp.status_code})")
        except Exception as e:
            logger.warning(f"R7: {name} dataset unreachable ({e})")


async def _startup_load_inner():
    """Inner startup logic — separated so startup_load can catch all errors."""
    global _leads_cache, _last_refresh
    
    # 1B: Run a database backup on startup (non-blocking, best-effort)
    try:
        from scripts.backup_db import backup_database, DB_PATH
        if DB_PATH.exists():
            backup_path = backup_database(max_backups=7)
            logger.info(f"Startup: Database backed up to {backup_path}")
        else:
            logger.info("Startup: No database file yet — skipping backup")
    except Exception as e:
        logger.warning(f"Startup: Backup failed (non-fatal): {e}")
    
    # R7: Verify Socrata endpoints are still valid
    _ping_socrata_endpoints()
    
    db = get_database()
    loaded = db.load_all_leads()
    
    if loaded:
        _leads_cache = loaded
        _last_refresh = db.get_last_refresh_time()
        logger.info(f"Startup: Loaded {len(loaded)} leads from database")
        
        # Auto-compute revenue if not yet computed (pure math, no API calls)
        # v2: force recompute if computed before the fix (building counts vs unit counts bug)
        revenue_computed = db.get_setting('revenue_computed_at')
        revenue_version = db.get_setting('revenue_formula_version')
        if not revenue_computed or revenue_version != '2':
            logger.info("Startup: Computing revenue estimates for all leads (first time)...")
            from src.score.revenue import estimate_revenue
            batch_updates = []
            for lead in _leads_cache:
                rev = estimate_revenue(lead)
                lead.estimated_monthly_revenue = rev["estimated_monthly_revenue"]
                lead.estimated_annual_revenue = rev["estimated_annual_revenue"]
                if rev["estimated_monthly_revenue"] > 0:
                    batch_updates.append((lead.lead_id, rev["estimated_monthly_revenue"], rev["estimated_annual_revenue"]))
            # Persist in a single transaction (fast even for 100k leads)
            db.batch_update_revenue(batch_updates)
            db.set_setting('revenue_computed_at', datetime.now().isoformat())
            db.set_setting('revenue_formula_version', '2')
            logger.info(f"Startup: Revenue estimated for {len(batch_updates)} leads")
        else:
            logger.info(f"Startup: Revenue already computed (v2) at {revenue_computed}")
        
        # Auto-compute violations if not yet done (runs as background task - hits HPD API)
        violations_computed = db.get_setting('violations_computed_at')
        if not violations_computed:
            logger.info("Startup: Violations not yet computed, scheduling background fetch...")
            import asyncio
            asyncio.create_task(_auto_compute_violations())
        else:
            logger.info(f"Startup: Violations already computed at {violations_computed}")
        
        # Check if there's an incomplete enrichment job to resume
        active_job = db.get_active_enrichment_job()
        if active_job and active_job['status'] == 'running':
            # R2: Check if the job is stale (older than 24 hours) — likely killed mid-flight
            from datetime import timedelta
            job_started = active_job.get('started_at')
            is_stale = False
            if job_started:
                try:
                    started_dt = datetime.fromisoformat(job_started)
                    if (datetime.now() - started_dt) > timedelta(hours=24):
                        is_stale = True
                except (ValueError, TypeError):
                    is_stale = True  # Can't parse date — treat as stale
            
            if is_stale:
                logger.warning(f"Startup: Enrichment job {active_job['id']} is stale (started {job_started}), marking interrupted")
                db.update_enrichment_job(active_job['id'], status='interrupted', error='Stale job detected on startup (>24h)')
            else:
                remaining = active_job.get('lead_ids_remaining', [])
                if remaining:
                    logger.info(f"Startup: Found incomplete enrichment job {active_job['id']} with {len(remaining)} leads remaining")
                    # Resume in background
                    import asyncio
                    asyncio.create_task(_resume_enrichment_job(active_job))
        else:
            # Check if enrichment was already completed (persisted across restarts)
            enrichment_completed = db.get_setting('enrichment_completed_at')
            if enrichment_completed:
                logger.info(f"Startup: Enrichment already completed at {enrichment_completed}, skipping auto-enrich")
            else:
                # Check if we need to auto-start enrichment
                enrichment_stats = db.get_enrichment_stats()
                enriched_count = enrichment_stats.get('with_phone', 0) + enrichment_stats.get('with_email', 0)
                
                if enriched_count < 100:
                    logger.info(f"Startup: Only {enriched_count} leads enriched, auto-starting batch enrichment")
                    import asyncio
                    asyncio.create_task(_auto_start_enrichment())
                else:
                    logger.info(f"Startup: {enriched_count} leads already enriched, no auto-enrich needed")
        
        # Check for stale data and create alerts (Phase 5.4)
        from datetime import timedelta
        if _last_refresh and (datetime.now() - _last_refresh) > timedelta(days=7):
            days_stale = (datetime.now() - _last_refresh).days
            db.add_change_alert(
                "stale_data",
                f"Data hasn't been refreshed in {days_stale} days. Consider running /api/refresh.",
                details={"last_refresh": _last_refresh.isoformat(), "days_stale": days_stale},
            )
            logger.warning(f"Startup: Data is {days_stale} days stale")
    else:
        logger.info("Startup: No leads in database, run /api/refresh to populate")


async def _auto_compute_violations():
    """Auto-compute violations data in background on first startup.
    Runs the heavy HPD API calls in a thread to avoid blocking the event loop."""
    import asyncio
    await asyncio.sleep(10)  # Wait for server to fully start
    try:
        # Run the synchronous violations computation in a thread pool
        await asyncio.to_thread(_compute_violations_sync)
    except Exception as e:
        logger.error(f"Background violations computation failed: {e}")


def _compute_violations_sync():
    """Synchronous violations computation (runs in thread pool)."""
    logger.info("Background: Starting violations computation...")
    from src.ingest.hpd_violations import HPDViolationsClient, aggregate_violations_for_lead
    import json as _json
    
    db = get_database()
    rows, total = db.get_leads_filtered(min_portfolio=10, limit=5000, offset=0)
    
    if not rows:
        logger.info("Background: No high-value leads for violations")
        db.set_setting('violations_computed_at', datetime.now().isoformat())
        return
    
    client = HPDViolationsClient()
    
    all_building_ids = []
    lead_buildings = {}
    lead_units = {}
    
    for row in rows:
        lead_id = row['lead_id']
        bids = _json.loads(row.get('building_ids') or '[]')
        lead_buildings[lead_id] = bids
        lead_units[lead_id] = row.get('total_units', 0) or 0
        all_building_ids.extend(bids)
    
    logger.info(f"Background: Fetching violations for {len(all_building_ids)} buildings across {len(rows)} leads")
    violations_data = client.fetch_violations_for_buildings(all_building_ids, batch_size=200)
    logger.info(f"Background: Violations API returned data for {len(violations_data)} buildings")
    
    updated = 0
    for lead_id, bids in lead_buildings.items():
        agg = aggregate_violations_for_lead(bids, violations_data, lead_units.get(lead_id, 0))
        if agg["violation_count"] > 0:
            db.update_lead(lead_id, agg)
            updated += 1
    
    # Update cache
    global _leads_cache
    for lead in _leads_cache:
        if lead.lead_id in lead_buildings:
            agg = aggregate_violations_for_lead(
                lead_buildings[lead.lead_id],
                violations_data,
                lead_units.get(lead.lead_id, 0)
            )
            lead.violation_count = agg["violation_count"]
            lead.violation_class_a = agg["violation_class_a"]
            lead.violation_class_b = agg["violation_class_b"]
            lead.violation_class_c = agg["violation_class_c"]
            lead.violations_per_unit = agg["violations_per_unit"]
    
    db.set_setting('violations_computed_at', datetime.now().isoformat())
    logger.info(f"Background: Violations computed for {updated}/{len(rows)} leads")


async def _auto_start_enrichment():
    """Auto-start enrichment for top 500 leads if few are enriched."""
    import asyncio
    await asyncio.sleep(5)  # Wait for server to fully start
    
    global _leads_cache, _enrichment_state
    
    if not _leads_cache:
        return
    
    with _enrichment_lock:
        if _enrichment_state["running"]:
            logger.info("Auto-enrich: Enrichment already running, skipping")
            return
    
    logger.info("Auto-enrich: Starting batch enrichment for top 500 leads")
    
    # Run in thread to not block
    import threading
    thread = threading.Thread(target=_run_background_enrichment, args=(500, None))
    thread.daemon = True
    thread.start()


# _continuous_enrichment_scheduler removed - enrichment is now one-time with persistent completion tracking


async def _resume_enrichment_job(job: dict):
    """Resume an incomplete enrichment job."""
    import asyncio
    await asyncio.sleep(5)  # Wait for server to fully start
    
    global _leads_cache, _enrichment_state
    
    if not _leads_cache:
        return
    
    with _enrichment_lock:
        if _enrichment_state["running"]:
            logger.info("Resume enrichment: Enrichment already running, skipping")
            return
    
    remaining_ids = set(job.get('lead_ids_remaining', []))
    if not remaining_ids:
        logger.info("Resume enrichment: No leads remaining, marking complete")
        db = get_database()
        db.update_enrichment_job(job['id'], status='completed')
        return
    
    logger.info(f"Resume enrichment: Resuming job {job['id']} with {len(remaining_ids)} leads")
    
    # Run in thread to not block (Phase 4.2: removed unused min_score param)
    import threading
    thread = threading.Thread(
        target=_run_background_enrichment_with_job, 
        args=(job['id'], remaining_ids)
    )
    thread.daemon = True
    thread.start()


# Health check endpoint
@app.get("/api/health")
async def health_check():
    """Health check endpoint for monitoring. Returns 'starting' until startup completes."""
    if not _startup_complete:
        return {"status": "starting", "message": "Backend is loading data. This usually takes 1-2 minutes."}
    
    db = get_database()
    lead_count = db.get_leads_count()
    
    # Thread-safe snapshot of cache (Phase 4.1)
    with _leads_lock:
        cache_size = len(_leads_cache)
        unenriched_count = sum(1 for lead in _leads_cache 
                              if lead.portfolio_size >= 10 and lead.enrichment_status in ['none', None, ''])
    
    return {
        "status": "ok",
        "leads_in_cache": cache_size,
        "leads_in_db": lead_count,
        "enrichment_running": _enrichment_state.get("running", False),
        "unenriched_high_value": unenriched_count,
        "last_refresh": _last_refresh.isoformat() if _last_refresh else None,
    }


@app.get("/api/health/detailed")
async def health_detailed():
    """Comprehensive health and diagnostics endpoint (Phase 4.4)."""
    import time
    start = time.time()
    
    db = get_database()
    lead_count = db.get_leads_count()
    enrichment_stats = db.get_enrichment_stats()
    
    # DB file size
    db_size_bytes = 0
    try:
        db_size_bytes = db.db_path.stat().st_size if db.db_path.exists() else 0
    except Exception:
        pass
    
    with _leads_lock:
        cache_size = len(_leads_cache)
    
    # Active enrichment job
    active_job = db.get_active_enrichment_job()
    
    # Follow-ups due
    follow_ups = db.get_follow_ups_due()
    
    # Recent alerts
    alerts = db.get_change_alerts(limit=10)
    
    return {
        "status": "healthy",
        "uptime_check_ms": round((time.time() - start) * 1000, 1),
        "database": {
            "path": str(db.db_path),
            "size_mb": round(db_size_bytes / (1024 * 1024), 2),
            "total_leads": lead_count,
        },
        "cache": {
            "leads_in_cache": cache_size,
        },
        "enrichment": {
            "running": _enrichment_state.get("running", False),
            "by_status": enrichment_stats.get("by_status", {}),
            "with_phone": enrichment_stats.get("with_phone", 0),
            "with_email": enrichment_stats.get("with_email", 0),
            "with_website": enrichment_stats.get("with_website", 0),
            "active_job": {
                "id": active_job["id"],
                "status": active_job["status"],
                "processed": active_job.get("processed", 0),
                "total": active_job.get("total_leads", 0),
                "remaining": len(active_job.get("lead_ids_remaining", [])),
            } if active_job else None,
        },
        "pipeline": {
            "follow_ups_due": len(follow_ups),
            "recent_alerts": len(alerts),
        },
        "last_refresh": _last_refresh.isoformat() if _last_refresh else None,
    }


@app.get("/api/enrichment/queue")
async def get_enrichment_queue():
    """Get info about leads waiting for enrichment."""
    # Thread-safe snapshot of cache (Phase 4.1)
    with _leads_lock:
        cache_snapshot = list(_leads_cache)
    
    status_counts = {"none": 0, "partial": 0, "complete": 0, "failed": 0}
    high_value_unenriched = []
    
    for lead in cache_snapshot:
        status = lead.enrichment_status or "none"
        if status in status_counts:
            status_counts[status] += 1
        
        # Track high-value unenriched leads
        if lead.portfolio_size >= 10 and status in ["none", ""]:
            high_value_unenriched.append({
                "lead_id": lead.lead_id,
                "name": lead.agent_name or lead.owner_name,
                "portfolio_size": lead.portfolio_size,
                "score": lead.score,
            })
    
    # Sort by score descending
    high_value_unenriched.sort(key=lambda x: x["score"], reverse=True)
    
    return {
        "total_leads": len(cache_snapshot),
        "status_counts": status_counts,
        "high_value_unenriched_count": len(high_value_unenriched),
        "next_in_queue": high_value_unenriched[:10],  # Top 10 waiting
    }


class OutreachAttemptResponse(BaseModel):
    """Outreach attempt data for API response."""
    id: str
    method: str
    outcome: str
    notes: Optional[str]
    timestamp: str


class ScoreBreakdown(BaseModel):
    """Score breakdown showing component scores."""
    portfolio: float = 0.0
    units: float = 0.0
    professional: float = 0.0
    contact: float = 0.0
    concentration: float = 0.0


class BuildingTypeBreakdownResponse(BaseModel):
    """Building type composition for a lead's portfolio."""
    condo: int = 0
    coop: int = 0
    rental_elevator: int = 0
    rental_walkup: int = 0
    small_residential: int = 0
    other: int = 0
    unknown: int = 0
    total: int = 0
    total_rental: int = 0


class ContactWithSource(BaseModel):
    """Contact info with source attribution."""
    value: str
    type: str  # "phone" or "email"
    source: str  # "google_places", "hunter", "web_crawl", etc.
    source_url: Optional[str] = None  # Clickable link to verify
    confidence: int = 50  # 0-100
    verified: bool = False


class LeadResponse(BaseModel):
    """Lead data for API response."""
    lead_id: str
    agent_name: str
    owner_name: str
    owner_type: str
    portfolio_size: int
    total_units: int
    buildings: List[str]
    phone: Optional[str]  # Best phone (for backwards compatibility)
    email: Optional[str]  # Best email (for backwards compatibility)
    phones: List[ContactWithSource] = []  # All phones with sources
    emails: List[ContactWithSource] = []  # All emails with sources
    website: Optional[str]
    website_source: Optional[str] = None  # Where we found the website
    business_summary: Optional[str]
    linkedin_url: Optional[str] = None  # Company LinkedIn page
    linkedin_people: List[str] = []  # Key people's LinkedIn profiles
    address: Optional[str]
    boro: str
    boros: List[str]
    # Building type composition from PLUTO
    building_types: Optional[BuildingTypeBreakdownResponse] = None
    building_classes: Dict[str, int] = {}  # Raw building class codes with counts
    score: float
    score_breakdown: Optional[ScoreBreakdown] = None
    tags: List[str]
    enrichment_status: str
    outreach_status: str
    notes: Optional[str]
    outreach_attempts: List[OutreachAttemptResponse] = []
    # Entity classification (Phase 0)
    contacts: List[Dict] = []  # HPD contact records
    entity_type: str = "unknown"
    company_name: Optional[str] = None
    primary_contact: Optional[str] = None
    primary_contact_title: Optional[str] = None
    # Revenue estimation (Phase 5.1)
    estimated_monthly_revenue: float = 0.0
    estimated_annual_revenue: float = 0.0
    revenue_breakdown: Optional[List[Dict]] = None  # Per-type breakdown for display
    # Violations (Phase 5.2)
    violation_count: int = 0
    violation_class_a: int = 0
    violation_class_b: int = 0
    violation_class_c: int = 0
    violations_per_unit: float = 0.0
    # Pipeline (Phase 5.3)
    pipeline_stage: str = "research"
    next_follow_up: Optional[str] = None
    priority_rank: int = 0


class PipelineStatus(BaseModel):
    """Pipeline status response."""
    total_leads: int
    last_refresh: Optional[str]
    enriched_count: int
    top_score: float


class EnrichmentRequest(BaseModel):
    """Request to enrich specific leads."""
    lead_ids: List[str]


class UpdateLeadRequest(BaseModel):
    """Request to update lead status/notes/pipeline."""
    outreach_status: Optional[str] = None  # new, contacted, interested, not_interested, closed
    notes: Optional[str] = None
    pipeline_stage: Optional[str] = None  # research, first_contact, follow_up, meeting_scheduled, meeting_done, loi, due_diligence, closed
    next_follow_up: Optional[str] = None  # ISO date string (YYYY-MM-DD)
    priority_rank: Optional[int] = None  # 1-5 stars


class OutreachEventRequest(BaseModel):
    """Request to log an outreach event (Phase 5.3)."""
    stage: str  # Pipeline stage at time of event
    method: Optional[str] = None  # call, email, linkedin, meeting, other
    outcome: Optional[str] = None  # connected, voicemail, no_answer, replied, bounced, scheduled, etc.
    notes: Optional[str] = None
    next_follow_up: Optional[str] = None  # ISO date for next action


@app.get("/")
async def root():
    """Root health check endpoint."""
    return {
        "status": "ok" if _startup_complete else "starting",
        "service": "hpd-leads-api",
    }


class LeadsListResponse(BaseModel):
    """Paginated leads response with total count."""
    leads: List[LeadResponse]
    total: int
    offset: int
    limit: int


def _row_to_lead(row_dict: Dict) -> Lead:
    """Convert a raw DB row dict to a Lead object (lightweight, no full deserialization)."""
    import json as _json
    from src.transform.aggregate import Lead, BuildingTypeBreakdown
    
    buildings = _json.loads(row_dict.get('buildings') or '[]')
    building_ids = _json.loads(row_dict.get('building_ids') or '[]')
    contacts = _json.loads(row_dict.get('contacts') or '[]')
    boros = _json.loads(row_dict.get('boros') or '[]')
    enrichment_sources = _json.loads(row_dict.get('enrichment_sources') or '[]')
    score_breakdown = _json.loads(row_dict.get('score_breakdown') or '{}')
    tags = _json.loads(row_dict.get('tags') or '[]')
    
    building_types = BuildingTypeBreakdown()
    if row_dict.get('building_types'):
        try:
            bt_data = _json.loads(row_dict['building_types'])
            building_types = BuildingTypeBreakdown(
                condo=bt_data.get('condo', 0), coop=bt_data.get('coop', 0),
                rental_elevator=bt_data.get('rental_elevator', 0),
                rental_walkup=bt_data.get('rental_walkup', 0),
                small_residential=bt_data.get('small_residential', 0),
                other=bt_data.get('other', 0), unknown=bt_data.get('unknown', 0),
            )
        except (ValueError, TypeError):
            pass
    
    building_classes = {}
    if row_dict.get('building_classes'):
        try:
            building_classes = _json.loads(row_dict['building_classes'])
        except (ValueError, TypeError):
            pass
    
    return Lead(
        lead_id=row_dict['lead_id'],
        agent_name=row_dict.get('agent_name') or '',
        owner_name=row_dict.get('owner_name') or '',
        owner_type=row_dict.get('owner_type') or '',
        portfolio_size=row_dict.get('portfolio_size') or 0,
        total_units=row_dict.get('total_units') or 0,
        buildings=buildings, building_ids=building_ids, contacts=contacts,
        address=row_dict.get('address'), boro=row_dict.get('boro') or '', boros=boros,
        building_types=building_types, building_classes=building_classes,
        reg_status=row_dict.get('reg_status') or '',
        dos_id=row_dict.get('dos_id'), dos_status=row_dict.get('dos_status'),
        phone=row_dict.get('phone'), email=row_dict.get('email'),
        website=row_dict.get('website'),
        business_summary=row_dict.get('business_summary'),
        owner_principal=row_dict.get('owner_principal'),
        enrichment_status=row_dict.get('enrichment_status') or 'none',
        enrichment_sources=enrichment_sources,
        score=row_dict.get('score') or 0.0, score_breakdown=score_breakdown,
        tags=tags, opportunity_note=row_dict.get('opportunity_note'),
        outreach_status=row_dict.get('outreach_status') or 'new',
        notes=row_dict.get('notes'),
        entity_type=row_dict.get('entity_type') or 'unknown',
        company_name=row_dict.get('company_name'),
        primary_contact=row_dict.get('primary_contact'),
        primary_contact_title=row_dict.get('primary_contact_title'),
        # Revenue (Phase 5.1)
        estimated_monthly_revenue=row_dict.get('estimated_monthly_revenue') or 0.0,
        estimated_annual_revenue=row_dict.get('estimated_annual_revenue') or 0.0,
        # Violations (Phase 5.2)
        violation_count=row_dict.get('violation_count') or 0,
        violation_class_a=row_dict.get('violation_class_a') or 0,
        violation_class_b=row_dict.get('violation_class_b') or 0,
        violation_class_c=row_dict.get('violation_class_c') or 0,
        violations_per_unit=row_dict.get('violations_per_unit') or 0.0,
        # Pipeline (Phase 5.3)
        pipeline_stage=row_dict.get('pipeline_stage') or 'research',
        next_follow_up=row_dict.get('next_follow_up'),
        priority_rank=row_dict.get('priority_rank') or 0,
        # Retry tracking (Phase 2.7)
        enrichment_retries=row_dict.get('enrichment_retries') or 0,
    )


@app.get("/api/leads", response_model=LeadsListResponse)
async def get_leads(
    min_score: Optional[float] = Query(None, description="Minimum score filter"),
    max_score: Optional[float] = Query(None, description="Maximum score filter"),
    min_portfolio: Optional[int] = Query(None, description="Minimum portfolio size"),
    max_portfolio: Optional[int] = Query(None, description="Maximum portfolio size"),
    boro: Optional[str] = Query(None, description="Filter by borough"),
    has_phone: Optional[bool] = Query(None, description="Filter by phone availability"),
    has_email: Optional[bool] = Query(None, description="Filter by email availability"),
    has_website: Optional[bool] = Query(None, description="Filter by website availability"),
    entity_type: Optional[str] = Query(None, description="Filter by entity type (company, individual_agent, owner_operator)"),
    enrichment_status: Optional[str] = Query(None, description="Filter by enrichment status"),
    outreach_status: Optional[str] = Query(None, description="Filter by outreach status"),
    pipeline_stage: Optional[str] = Query(None, description="Filter by pipeline stage"),
    search: Optional[str] = Query(None, description="Full-text search across name, company, address"),
    sort_by: str = Query('score', description="Sort column"),
    sort_dir: str = Query('desc', description="Sort direction (asc/desc)"),
    min_units: Optional[int] = Query(None, description="Minimum total residential units"),
    max_units: Optional[int] = Query(None, description="Maximum total residential units"),
    min_units_per_bldg: Optional[float] = Query(None, description="Minimum average units per building"),
    max_units_per_bldg: Optional[float] = Query(None, description="Maximum average units per building"),
    building_type_has: Optional[str] = Query(None, description="Filter by building type (comma-separated: condo,coop,rental_elevator,rental_walkup,small_residential)"),
    limit: int = Query(50, ge=1, le=1000, description="Max results to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
):
    """
    Get leads with SQL-based filtering (fast, indexed).
    Returns paginated results with total count.
    """
    db = get_database()
    rows, total = db.get_leads_filtered(
        min_score=min_score, max_score=max_score,
        min_portfolio=min_portfolio, max_portfolio=max_portfolio,
        boro=boro,
        has_phone=has_phone, has_email=has_email, has_website=has_website,
        entity_type=entity_type,
        min_units=min_units, max_units=max_units,
        min_units_per_bldg=min_units_per_bldg, max_units_per_bldg=max_units_per_bldg,
        building_type_has=building_type_has,
        enrichment_status=enrichment_status, outreach_status=outreach_status,
        pipeline_stage=pipeline_stage, search=search,
        sort_by=sort_by, sort_dir=sort_dir,
        limit=limit, offset=offset,
    )
    
    leads = [_row_to_lead(r) for r in rows]
    
    return LeadsListResponse(
        leads=[_lead_to_response(l) for l in leads],
        total=total,
        offset=offset,
        limit=limit,
    )


@app.get("/api/enrichment-gaps")
async def get_enrichment_gaps():
    """1D: Return counts of unenriched leads broken down by enrichment status."""
    db = get_database()
    with db._get_connection() as conn:
        rows = conn.execute(
            "SELECT enrichment_status, COUNT(*) as cnt FROM leads GROUP BY enrichment_status"
        ).fetchall()
        breakdown = {r['enrichment_status'] or 'none': r['cnt'] for r in rows}
        total = sum(breakdown.values())
        unenriched = breakdown.get('none', 0) + breakdown.get('pending', 0)
        return {
            "total_leads": total,
            "unenriched": unenriched,
            "breakdown": breakdown,
        }


@app.post("/api/backup")
async def trigger_backup():
    """Trigger a manual database backup."""
    try:
        from scripts.backup_db import backup_database, DB_PATH
        if not DB_PATH.exists():
            raise HTTPException(status_code=404, detail="No database file found")
        backup_path = backup_database(max_backups=7)
        return {"status": "ok", "backup_path": str(backup_path)}
    except Exception as e:
        logger.error(f"Backup failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/leads/{lead_id}", response_model=LeadResponse)
async def get_lead(lead_id: str):
    """Get a single lead by ID (reads from DB for reliability, Phase 4.2)."""
    db = get_database()
    row = db.get_lead_by_id(lead_id)
    if row:
        lead = _row_to_lead(row)
        return _lead_to_response(lead)
    raise HTTPException(status_code=404, detail="Lead not found")


@app.patch("/api/leads/{lead_id}")
async def update_lead(lead_id: str, request: UpdateLeadRequest):
    """
    Update a lead's outreach status, notes, pipeline stage, follow-up, and priority.
    """
    global _leads_cache
    
    valid_statuses = {"new", "contacted", "interested", "not_interested", "closed"}
    valid_stages = {"research", "first_contact", "follow_up", "meeting_scheduled", 
                    "meeting_done", "loi", "due_diligence", "closed"}
    
    if request.outreach_status and request.outreach_status not in valid_statuses:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
        )
    
    if request.pipeline_stage and request.pipeline_stage not in valid_stages:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid pipeline stage. Must be one of: {', '.join(valid_stages)}"
        )
    
    if request.priority_rank is not None and not (0 <= request.priority_rank <= 5):
        raise HTTPException(status_code=400, detail="priority_rank must be 0-5")
    
    # Use lock to prevent race conditions
    with _leads_lock:
        for i, lead in enumerate(_leads_cache):
            if lead.lead_id == lead_id:
                if request.outreach_status is not None:
                    lead.outreach_status = request.outreach_status
                if request.notes is not None:
                    lead.notes = request.notes
                if request.pipeline_stage is not None:
                    lead.pipeline_stage = request.pipeline_stage
                if request.next_follow_up is not None:
                    lead.next_follow_up = request.next_follow_up
                if request.priority_rank is not None:
                    lead.priority_rank = request.priority_rank
                lead.updated_at = datetime.now()
                _leads_cache[i] = lead
                
                # Persist to database
                db = get_database()
                updates = {}
                if request.outreach_status is not None:
                    updates['outreach_status'] = request.outreach_status
                if request.notes is not None:
                    updates['notes'] = request.notes
                if request.pipeline_stage is not None:
                    updates['pipeline_stage'] = request.pipeline_stage
                if request.next_follow_up is not None:
                    updates['next_follow_up'] = request.next_follow_up
                if request.priority_rank is not None:
                    updates['priority_rank'] = request.priority_rank
                
                if updates:
                    db.update_lead(lead_id, updates)
                
                # Also update legacy user_data table
                db.save_lead_user_data(
                    lead_id=lead_id,
                    outreach_status=request.outreach_status,
                    notes=request.notes
                )
                
                return {
                    "status": "success",
                    "lead_id": lead_id,
                    "outreach_status": lead.outreach_status,
                    "pipeline_stage": lead.pipeline_stage,
                    "next_follow_up": lead.next_follow_up,
                    "priority_rank": lead.priority_rank,
                    "notes": lead.notes,
                }
    
    raise HTTPException(status_code=404, detail="Lead not found")


@app.post("/api/leads/{lead_id}/outreach-event")
async def log_outreach_event(lead_id: str, request: OutreachEventRequest):
    """Log an outreach event for a lead (Phase 5.3)."""
    db = get_database()
    
    # Verify lead exists
    lead_row = db.get_lead_by_id(lead_id)
    if not lead_row:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    # Save the event
    event_id = db.add_outreach_event(lead_id, {
        "stage": request.stage,
        "method": request.method,
        "outcome": request.outcome,
        "notes": request.notes,
        "next_follow_up": request.next_follow_up,
    })
    
    # Update the lead's pipeline stage and follow-up
    updates = {"pipeline_stage": request.stage}
    if request.next_follow_up:
        updates["next_follow_up"] = request.next_follow_up
    db.update_lead(lead_id, updates)
    
    # Update cache
    with _leads_lock:
        for lead in _leads_cache:
            if lead.lead_id == lead_id:
                lead.pipeline_stage = request.stage
                if request.next_follow_up:
                    lead.next_follow_up = request.next_follow_up
                break
    
    return {"status": "success", "event_id": event_id}


@app.get("/api/leads/{lead_id}/outreach-events")
async def get_lead_outreach_events(lead_id: str):
    """Get all outreach events for a lead (Phase 5.3)."""
    db = get_database()
    events = db.get_outreach_events(lead_id)
    return {"lead_id": lead_id, "events": events}


@app.get("/api/follow-ups")
async def get_follow_ups_due(before: Optional[str] = None):
    """Get leads with follow-ups due on or before a date (Phase 5.3)."""
    db = get_database()
    follow_ups = db.get_follow_ups_due(before)
    return {"count": len(follow_ups), "leads": follow_ups}


@app.get("/api/status", response_model=PipelineStatus)
async def get_status():
    """Get pipeline status (thread-safe, Phase 4.1)."""
    with _leads_lock:
        enriched = len([l for l in _leads_cache if l.enrichment_status in ["complete", "partial"]])
        top = max((l.score for l in _leads_cache), default=0)
        total = len(_leads_cache)
    
    return PipelineStatus(
        total_leads=total,
        last_refresh=_last_refresh.isoformat() if _last_refresh else None,
        enriched_count=enriched,
        top_score=top,
    )


@app.get("/api/stats")
async def get_stats():
    """Get detailed statistics using SQL aggregation (fast, no Python iteration)."""
    db = get_database()
    stats = db.get_stats_sql()
    stats["last_refresh"] = _last_refresh.isoformat() if _last_refresh else None
    return stats


@app.get("/api/data-status")
async def get_data_status():
    """Get data freshness timestamps for revenue, violations, enrichment."""
    db = get_database()
    return {
        "revenue_computed_at": db.get_setting('revenue_computed_at'),
        "revenue_formula_version": db.get_setting('revenue_formula_version'),
        "violations_computed_at": db.get_setting('violations_computed_at'),
        "enrichment_completed_at": db.get_setting('enrichment_completed_at'),
        "last_refresh": _last_refresh.isoformat() if _last_refresh else None,
    }


def _run_background_refresh(limit: Optional[int], include_pluto: bool = True):
    """
    Background task to refresh pipeline data using streaming/chunked processing.
    
    This is memory-efficient: instead of loading all 200k buildings into memory,
    it processes in 25k chunks and aggregates incrementally.
    
    Args:
        limit: Max buildings to fetch (None = all)
        include_pluto: If True, fetch PLUTO data for building classification
    """
    global _leads_cache, _last_refresh, _refresh_state
    
    # Chunk size - tuned for Railway's memory limits (512MB hobby tier)
    # 10k buildings per chunk to stay well under memory limits during PLUTO lookup
    # PLUTO data can be 50-100KB per building, so 10k = 500MB-1GB peak
    CHUNK_SIZE = 10000
    
    with _refresh_lock:
        _refresh_state["running"] = True
        _refresh_state["phase"] = "initializing"
        _refresh_state["buildings_fetched"] = 0
        _refresh_state["total_buildings"] = limit or 0
        _refresh_state["leads_created"] = 0
        _refresh_state["pluto_lookups"] = 0
        _refresh_state["started_at"] = datetime.now().isoformat()
        _refresh_state["finished_at"] = None
        _refresh_state["error"] = None
    
    try:
        logger.info(f"Background refresh started with limit={limit or 'ALL'}, pluto={include_pluto}")
        
        client = HPDClient()
        db = get_database()
        
        # Initialize PLUTO client if enabled
        pluto_client = None
        if include_pluto:
            from src.ingest.pluto_client import PLUTOClient, classify_building
            pluto_client = PLUTOClient()
            logger.info("PLUTO client initialized for building classification")
        
        # Use streaming aggregator for memory efficiency
        aggregator = StreamingLeadAggregator()
        chunk_num = 0
        total_pluto_lookups = 0
        
        def update_progress(fetched, total):
            with _refresh_lock:
                _refresh_state["buildings_fetched"] = fetched
                if total:
                    _refresh_state["total_buildings"] = total
        
        # Stream buildings in chunks
        for chunk_buildings in client.stream_buildings_with_contacts(
            chunk_size=CHUNK_SIZE,
            total_limit=limit,
            progress_callback=update_progress
        ):
            chunk_num += 1
            
            with _refresh_lock:
                _refresh_state["phase"] = f"processing chunk {chunk_num}"
            
            logger.info(f"Processing chunk {chunk_num}: {len(chunk_buildings)} buildings")
            
            # Fetch PLUTO data for this chunk if enabled
            if pluto_client:
                with _refresh_lock:
                    _refresh_state["phase"] = f"PLUTO lookup chunk {chunk_num}"
                
                logger.info(f"Fetching PLUTO data for chunk {chunk_num}...")
                pluto_data = pluto_client.get_classifications_batch(
                    chunk_buildings,
                    boro_field='boroid',
                    block_field='block',
                    lot_field='lot'
                )
                total_pluto_lookups += len(pluto_data)
                
                with _refresh_lock:
                    _refresh_state["pluto_lookups"] = total_pluto_lookups
                
                logger.info(f"Got {len(pluto_data)} PLUTO classifications")
                
                # Enrich raw buildings with PLUTO data
                for building in chunk_buildings:
                    boro_id = building.get('boroid', '')
                    block = building.get('block', '')
                    lot = building.get('lot', '')
                    
                    if boro_id and block and lot:
                        bbl = pluto_client.make_bbl(boro_id, block, lot)
                        if bbl in pluto_data:
                            pluto = pluto_data[bbl]
                            building['building_class'] = pluto.building_class
                            building['building_type'] = pluto.building_type
                            building['units_res'] = pluto.units_res
                            building['year_built'] = pluto.year_built
            
            with _refresh_lock:
                _refresh_state["phase"] = f"normalizing chunk {chunk_num}"
            
            # Normalize buildings in this chunk (now includes PLUTO data)
            normalized = [normalize_building(b) for b in chunk_buildings]
            
            # Free the raw buildings from memory
            del chunk_buildings
            if pluto_client:
                del pluto_data
            
            # Add to streaming aggregator (memory-efficient - only keeps lead summaries)
            num_leads = aggregator.process_chunk(normalized)
            
            # Free normalized buildings and force garbage collection
            # This is critical on memory-constrained Railway instances
            del normalized
            gc.collect()
            
            stats = aggregator.get_stats()
            logger.info(f"After chunk {chunk_num}: {stats['unique_leads']} leads, {stats['total_buildings']} buildings")
            
            with _refresh_lock:
                _refresh_state["leads_created"] = stats["unique_leads"]
        
        # Finalize: get all leads from aggregator
        with _refresh_lock:
            _refresh_state["phase"] = "finalizing"
        
        logger.info("Finalizing leads from streaming aggregator...")
        leads = aggregator.get_leads()
        logger.info(f"Created {len(leads)} leads from streaming aggregation")
        
        # Score leads
        with _refresh_lock:
            _refresh_state["phase"] = "scoring"
        
        leads = score_leads(leads)
        logger.info(f"Scored {len(leads)} leads")
        
        # Apply persisted user data (notes, status, enrichment)
        with _refresh_lock:
            _refresh_state["phase"] = "persisting"
        
        leads = db.apply_persisted_data_to_leads(leads)
        
        # Save to database
        db.save_leads(leads)
        logger.info(f"Persisted {len(leads)} leads to database")
        
        # Update cache
        with _leads_lock:
            _leads_cache = leads
        
        _last_refresh = datetime.now()
        
        with _refresh_lock:
            _refresh_state["leads_created"] = len(leads)
        
        logger.info(f"Background refresh complete: {_refresh_state['buildings_fetched']} buildings -> {len(leads)} leads, {total_pluto_lookups} PLUTO lookups")
        
    except Exception as e:
        logger.error(f"Background refresh failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        with _refresh_lock:
            _refresh_state["error"] = str(e)
    finally:
        with _refresh_lock:
            _refresh_state["running"] = False
            _refresh_state["finished_at"] = datetime.now().isoformat()


@app.post("/api/refresh")
async def refresh_pipeline(
    background_tasks: BackgroundTasks,
    limit: Optional[int] = Query(None, description="Max buildings to fetch (None = ALL ~200k)"),
    full: bool = Query(False, description="Fetch ALL buildings (overrides limit)"),
    background: bool = Query(True, description="Run in background (recommended for large datasets)"),
    pluto: bool = Query(True, description="Include PLUTO building classification (condo/coop/rental)"),
):
    """
    Refresh the pipeline: fetch from HPD, normalize, aggregate, score.
    
    By default fetches 10,000 buildings for speed. Set full=true to fetch ALL ~200k.
    For full refresh, background=true is REQUIRED (default) due to worker timeouts.
    
    PLUTO Integration (pluto=true, default):
    - Fetches building classification from NYC PLUTO dataset
    - Adds building type breakdown: condo, coop, rental_elevator, rental_walkup, etc.
    - Adds total units count per building
    
    Check progress at GET /api/refresh/status
    """
    global _leads_cache, _last_refresh, _refresh_state
    
    # If full=true, remove limit to get everything
    if full:
        limit = None
    elif limit is None:
        limit = 10000  # Default for quick refresh
    
    # Check if already running
    with _refresh_lock:
        if _refresh_state["running"]:
            return {
                "status": "already_running",
                "message": "Background refresh is already in progress",
                "phase": _refresh_state["phase"],
                "buildings_fetched": _refresh_state["buildings_fetched"],
                "check_progress": "/api/refresh/status",
            }
    
    # For large datasets or full refresh, require background mode
    if (limit is None or limit > 15000) and not background:
        return {
            "status": "error",
            "message": "For datasets > 15,000 buildings, background=true is required to avoid timeouts",
        }
    
    logger.info(f"Starting pipeline refresh with limit={limit or 'ALL'}, background={background}, pluto={pluto}")
    
    if background:
        # Run in background
        background_tasks.add_task(_run_background_refresh, limit, pluto)
        return {
            "status": "started",
            "message": f"Background refresh started for {limit or 'ALL'} buildings" + (" with PLUTO data" if pluto else ""),
            "pluto_enabled": pluto,
            "check_progress": "/api/refresh/status",
        }
    else:
        # Run synchronously (only for small datasets)
        try:
            # Ingest
            client = HPDClient()
            raw_buildings = client.get_combined_data(building_limit=limit)
            logger.info(f"Fetched {len(raw_buildings)} buildings from HPD")
            
            # Normalize
            buildings = [normalize_building(b) for b in raw_buildings]
            
            # Aggregate
            leads = aggregate_to_leads(buildings)
            logger.info(f"Aggregated into {len(leads)} leads")
            
            # Score
            leads = score_leads(leads)
            
            # Apply persisted user data (notes, status, enrichment)
            db = get_database()
            leads = db.apply_persisted_data_to_leads(leads)
            
            # Persist all leads to SQLite (survives server restarts)
            db.save_leads(leads)
            logger.info(f"Persisted {len(leads)} leads to database")
            
            # Update cache
            _leads_cache = leads
            _last_refresh = datetime.now()
            
            return {
                "status": "success",
                "buildings_fetched": len(raw_buildings),
                "leads_created": len(leads),
                "timestamp": _last_refresh.isoformat(),
            }
            
        except Exception as e:
            logger.error(f"Pipeline refresh failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/refresh/status")
async def get_refresh_status():
    """
    Get the status of background refresh.
    
    Returns progress, current phase, and completion stats.
    """
    with _refresh_lock:
        return {
            "running": _refresh_state["running"],
            "phase": _refresh_state["phase"],
            "buildings_fetched": _refresh_state["buildings_fetched"],
            "total_buildings": _refresh_state["total_buildings"],
            "leads_created": _refresh_state["leads_created"],
            "started_at": _refresh_state["started_at"],
            "finished_at": _refresh_state["finished_at"],
            "error": _refresh_state["error"],
        }


@app.post("/api/enrich")
async def enrich_leads(request: EnrichmentRequest):
    """
    Enrich specific leads by ID.
    
    Uses web crawl to find website, phone, email.
    """
    global _leads_cache
    
    if not request.lead_ids:
        raise HTTPException(status_code=400, detail="No lead IDs provided")
    
    # Find leads to enrich
    to_enrich = []
    lead_index = {l.lead_id: i for i, l in enumerate(_leads_cache)}
    
    for lid in request.lead_ids:
        if lid in lead_index:
            to_enrich.append(_leads_cache[lead_index[lid]])
    
    if not to_enrich:
        raise HTTPException(status_code=404, detail="No matching leads found")
    
    # Run enrichment
    enricher = Enricher(use_cache=True)
    enriched = enricher.enrich_batch(to_enrich, limit=len(to_enrich), skip_enriched=False)
    
    # Update cache with lock and persist enrichment to database
    db = get_database()
    updated_leads = []
    with _leads_lock:
        for lead in enriched:
            if lead.lead_id in lead_index:
                _leads_cache[lead_index[lead.lead_id]] = lead
                updated_leads.append(lead)
            # Save to enrichment cache (backward compatibility)
            db.save_enrichment(
                lead_id=lead.lead_id,
                phone=lead.phone,
                email=lead.email,
                website=lead.website,
                business_summary=lead.business_summary,
                owner_principal=lead.owner_principal,
                enrichment_status=lead.enrichment_status,
                enrichment_sources=lead.enrichment_sources,
            )
    
    # Also save updated leads to main leads table
    if updated_leads:
        db.save_leads(updated_leads)
    
    return {
        "status": "success",
        "enriched_count": len(enriched),
        "results": [
            {
                "lead_id": l.lead_id,
                "agent_name": l.agent_name,
                "website": l.website,
                "phone": l.phone,
                "email": l.email,
                "status": l.enrichment_status,
            }
            for l in enriched
        ],
    }


def _run_background_enrichment(limit: int, min_score: Optional[float] = None):
    """Background task to enrich leads with persistent job tracking."""
    global _leads_cache, _enrichment_state, _enrichment_stop_requested
    
    _enrichment_stop_requested = False  # Reset stop flag
    
    db = get_database()
    
    # Filter candidates
    candidates = []
    for lead in _leads_cache:
        if lead.enrichment_status in ["complete", "partial"]:
            continue
        if min_score is not None and lead.score < min_score:
            continue
        candidates.append(lead)
    
    # Sort by score and limit
    candidates.sort(key=lambda l: l.score, reverse=True)
    batch = candidates[:limit]
    
    if not batch:
        logger.info("Background enrichment: No candidates to enrich")
        return
    
    # Create persistent job
    lead_ids = [l.lead_id for l in batch]
    job_id = db.create_enrichment_job(
        job_type='batch',
        lead_ids=lead_ids,
        config={'min_score': min_score, 'limit': limit}
    )
    
    # Run with job tracking
    _run_background_enrichment_with_job(job_id, set(lead_ids), min_score)


def _run_background_enrichment_with_job(
    job_id: int, 
    lead_ids_remaining: set, 
    min_score: Optional[float] = None
):
    """Background enrichment with persistent job tracking (supports resume)."""
    global _leads_cache, _enrichment_state, _enrichment_stop_requested
    
    _enrichment_stop_requested = False
    
    with _enrichment_lock:
        _enrichment_state["running"] = True
        _enrichment_state["phase"] = "sequential"
        _enrichment_state["progress"] = 0
        _enrichment_state["total"] = len(lead_ids_remaining)
        _enrichment_state["completed"] = 0
        _enrichment_state["failed"] = 0
        _enrichment_state["started_at"] = datetime.now().isoformat()
        _enrichment_state["finished_at"] = None
        _enrichment_state["error"] = None
    
    try:
        enricher = Enricher(use_cache=True)
        db = get_database()
        
        # Build lead lookup and batch
        lead_index = {l.lead_id: i for i, l in enumerate(_leads_cache)}
        batch = [l for l in _leads_cache if l.lead_id in lead_ids_remaining]
        batch.sort(key=lambda l: l.score, reverse=True)
        
        with _enrichment_lock:
            _enrichment_state["total"] = len(batch)
        
        logger.info(f"Background enrichment started: {len(batch)} leads (job {job_id})")
        
        remaining = list(lead_ids_remaining)
        
        for i, lead in enumerate(batch):
            # Check for stop request
            if _enrichment_stop_requested:
                logger.info("Enrichment stopped by user request")
                with _enrichment_lock:
                    _enrichment_state["error"] = "Stopped by user"
                # Persist remaining leads
                db.update_enrichment_job(
                    job_id,
                    status='paused',
                    lead_ids_remaining=remaining,
                    processed=i,
                    completed=_enrichment_state["completed"],
                    failed=_enrichment_state["failed"],
                    error="Stopped by user"
                )
                break
            
            with _enrichment_lock:
                _enrichment_state["progress"] = i + 1
                _enrichment_state["current_lead"] = lead.agent_name or lead.owner_name
            
            try:
                logger.info(f"[{i+1}/{len(batch)}] Enriching: {lead.agent_name or lead.owner_name}")
                enriched_lead = enricher.enrich_lead(lead)
                
                # Update cache with lock
                with _leads_lock:
                    if lead.lead_id in lead_index:
                        _leads_cache[lead_index[lead.lead_id]] = enriched_lead
                
                # Persist to database
                db.save_leads([enriched_lead])
                
                # Remove from remaining
                if lead.lead_id in remaining:
                    remaining.remove(lead.lead_id)
                
                if enriched_lead.enrichment_status in ["complete", "partial"]:
                    with _enrichment_lock:
                        _enrichment_state["completed"] += 1
                else:
                    with _enrichment_lock:
                        _enrichment_state["failed"] += 1
                
                # Update job progress every 10 leads
                if (i + 1) % 10 == 0:
                    db.update_enrichment_job(
                        job_id,
                        processed=i + 1,
                        completed=_enrichment_state["completed"],
                        failed=_enrichment_state["failed"],
                        lead_ids_remaining=remaining,
                        current_lead=lead.agent_name or lead.owner_name
                    )
                        
            except Exception as e:
                logger.warning(f"Failed to enrich {lead.agent_name}: {e}")
                with _enrichment_lock:
                    _enrichment_state["failed"] += 1
                # Still remove from remaining (don't retry failed leads indefinitely)
                if lead.lead_id in remaining:
                    remaining.remove(lead.lead_id)
        
        # Mark job complete
        db.update_enrichment_job(
            job_id,
            status='completed',
            processed=len(batch),
            completed=_enrichment_state["completed"],
            failed=_enrichment_state["failed"],
            lead_ids_remaining=[]
        )
        
        logger.info(f"Background enrichment complete: {_enrichment_state['completed']} successful, {_enrichment_state['failed']} failed")
        
        # Persist completion so enrichment doesn't restart on next deploy
        try:
            db.set_setting('enrichment_completed_at', datetime.now().isoformat())
        except Exception:
            pass
        
    except Exception as e:
        logger.error(f"Background enrichment failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        with _enrichment_lock:
            _enrichment_state["error"] = str(e)
        # Mark job failed
        db.update_enrichment_job(job_id, status='failed', error=str(e))
    finally:
        _enrichment_stop_requested = False
        with _enrichment_lock:
            _enrichment_state["running"] = False
            _enrichment_state["phase"] = "complete"
            _enrichment_state["current_lead"] = None
            _enrichment_state["finished_at"] = datetime.now().isoformat()


def _run_batch_enrichment(
    dos_enabled: bool = True,
    web_enabled: bool = True,
    max_leads: Optional[int] = None,
    max_web_crawl: int = 500,
    min_portfolio: int = 5,
    min_score: float = 0.0,
):
    """
    High-performance batch enrichment using parallel processing.
    
    Phase 1: NY DOS lookups (fast, parallel)
    Phase 2: Web crawling (rate-limited, for high-priority leads)
    """
    global _leads_cache, _enrichment_state
    
    from src.enrich.batch_enricher import BatchEnricher, EnrichmentConfig
    
    with _enrichment_lock:
        _enrichment_state["running"] = True
        _enrichment_state["phase"] = "initializing"
        _enrichment_state["progress"] = 0
        _enrichment_state["total"] = len(_leads_cache)
        _enrichment_state["completed"] = 0
        _enrichment_state["failed"] = 0
        _enrichment_state["dos_completed"] = 0
        _enrichment_state["dos_found"] = 0
        _enrichment_state["web_completed"] = 0
        _enrichment_state["web_found"] = 0
        _enrichment_state["started_at"] = datetime.now().isoformat()
        _enrichment_state["finished_at"] = None
        _enrichment_state["error"] = None
    
    try:
        db = get_database()
        lead_index = {l.lead_id: i for i, l in enumerate(_leads_cache)}
        
        # Configure batch enricher
        config = EnrichmentConfig(
            dos_enabled=dos_enabled,
            dos_batch_size=100,
            dos_workers=5,
            web_enabled=web_enabled,
            web_batch_size=50,
            web_workers=3,
            min_portfolio_for_web=min_portfolio,
            min_score_for_web=min_score,
            skip_already_enriched=True,
            save_interval=50,
            max_leads=max_leads,
            max_web_crawl=max_web_crawl,
        )
        
        enricher = BatchEnricher(config)
        
        def on_progress(progress):
            """Update global state from enricher progress."""
            with _enrichment_lock:
                _enrichment_state["phase"] = progress.phase
                _enrichment_state["progress"] = progress.processed
                _enrichment_state["total"] = progress.total_leads
                _enrichment_state["dos_completed"] = progress.dos_completed
                _enrichment_state["dos_found"] = progress.dos_found
                _enrichment_state["web_completed"] = progress.web_completed
                _enrichment_state["web_found"] = progress.web_found
                _enrichment_state["failed"] = progress.failed
                _enrichment_state["current_lead"] = progress.current_lead
        
        def on_batch_complete(enriched_leads):
            """Save batch and update cache."""
            # Update cache
            with _leads_lock:
                for lead in enriched_leads:
                    if lead.lead_id in lead_index:
                        _leads_cache[lead_index[lead.lead_id]] = lead
            
            # Persist to database
            db.save_leads(enriched_leads)
            logger.info(f"Saved batch of {len(enriched_leads)} enriched leads")
        
        # Run batch enrichment
        logger.info(f"Starting batch enrichment: DOS={dos_enabled}, Web={web_enabled}, MaxLeads={max_leads}, MaxWeb={max_web_crawl}")
        enriched = enricher.enrich_all(
            list(_leads_cache),
            on_progress=on_progress,
            on_batch_complete=on_batch_complete,
        )
        
        # Final stats
        with _enrichment_lock:
            _enrichment_state["completed"] = _enrichment_state["dos_found"] + _enrichment_state["web_found"]
        
        logger.info(f"Batch enrichment complete: DOS found {_enrichment_state['dos_found']}, Web found {_enrichment_state['web_found']}")
        
    except Exception as e:
        logger.error(f"Batch enrichment failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        with _enrichment_lock:
            _enrichment_state["error"] = str(e)
    finally:
        with _enrichment_lock:
            _enrichment_state["running"] = False
            _enrichment_state["phase"] = "complete"
            _enrichment_state["current_lead"] = None
            _enrichment_state["finished_at"] = datetime.now().isoformat()


@app.post("/api/enrich/batch-full")
async def enrich_batch_full(
    background_tasks: BackgroundTasks,
    limit: int = Query(100, le=2000, description="Max leads to enrich"),
    min_units: int = Query(40, description="Only enrich leads with total_units >= this"),
    min_score: Optional[float] = Query(None, description="Only enrich leads with score >= this"),
):
    """
    Full unified enrichment (contacts + research + AI summary) for leads
    with >= min_units that haven't been enriched yet.
    Runs in background. Check progress at GET /api/enrich/status.
    """
    global _leads_cache, _enrichment_state
    
    with _enrichment_lock:
        if _enrichment_state["running"]:
            return {
                "status": "already_running",
                "message": "Background enrichment is already in progress",
                "progress": _enrichment_state["progress"],
                "total": _enrichment_state["total"],
            }
    
    if not _leads_cache:
        raise HTTPException(status_code=400, detail="No leads loaded.")
    
    # Filter candidates: 40+ units, not yet enriched
    candidates = [
        l for l in _leads_cache
        if (l.total_units or 0) >= min_units
        and l.enrichment_status not in ["complete", "partial"]
        and (min_score is None or (l.score or 0) >= min_score)
    ]
    candidates.sort(key=lambda l: (l.total_units or 0), reverse=True)
    batch = candidates[:limit]
    
    if not batch:
        return {"status": "no_candidates", "message": f"No unenriched leads with >= {min_units} units found."}
    
    background_tasks.add_task(_run_full_enrichment_batch, [l.lead_id for l in batch])
    
    return {
        "status": "started",
        "message": f"Full enrichment started for {len(batch)} leads (>= {min_units} units)",
        "total": len(batch),
        "total_candidates": len(candidates),
    }


def _run_full_enrichment_batch(lead_ids: list):
    """Background: unified enrichment (contacts + research + AI) per lead."""
    global _leads_cache, _enrichment_state, _enrichment_stop_requested
    
    _enrichment_stop_requested = False
    
    with _enrichment_lock:
        _enrichment_state["running"] = True
        _enrichment_state["phase"] = "full_enrichment"
        _enrichment_state["progress"] = 0
        _enrichment_state["total"] = len(lead_ids)
        _enrichment_state["completed"] = 0
        _enrichment_state["failed"] = 0
        _enrichment_state["started_at"] = datetime.now().isoformat()
        _enrichment_state["finished_at"] = None
        _enrichment_state["error"] = None
    
    try:
        from src.enrich.multi_source import MultiSourceEnricher
        from src.enrich.web_crawl import WebCrawler
        from src.enrich.ai_summary import generate_company_description
        
        enricher = MultiSourceEnricher()
        crawler = WebCrawler()
        db = get_database()
        
        lead_index = {l.lead_id: i for i, l in enumerate(_leads_cache)}
        
        for idx, lead_id in enumerate(lead_ids):
            if _enrichment_stop_requested:
                logger.info("Full enrichment stopped by user request")
                break
            
            cache_idx = lead_index.get(lead_id)
            if cache_idx is None:
                continue
            lead = _leads_cache[cache_idx]
            company_name = lead.agent_name or lead.owner_name
            
            with _enrichment_lock:
                _enrichment_state["progress"] = idx + 1
                _enrichment_state["current_lead"] = company_name
            
            try:
                # Step 1: Multi-source contacts
                try:
                    result = enricher.enrich(
                        company_name=company_name,
                        borough=lead.boro,
                        address=None,
                    )
                    if result.get('phone') and not lead.phone:
                        lead.phone = result['phone']
                    if result.get('email') and not lead.email:
                        lead.email = result['email']
                    if result.get('website') and not lead.website:
                        lead.website = result['website']
                    if result.get('owner_principal') and not lead.owner_principal:
                        lead.owner_principal = result['owner_principal']
                except Exception as e:
                    logger.warning(f"Contact enrichment failed for {lead_id}: {e}")
                
                # Step 2: Website discovery + deep scrape
                try:
                    if not lead.website:
                        website = crawler.find_website(company_name + " property management NYC")
                        if website:
                            lead.website = website
                    
                    if lead.website:
                        try:
                            scrape_data = crawler.deep_scrape(lead.website)
                            if scrape_data.get('phones'):
                                for p in scrape_data['phones']:
                                    if not lead.phone:
                                        lead.phone = p
                            if scrape_data.get('emails'):
                                for e in scrape_data['emails']:
                                    if not lead.email:
                                        lead.email = e
                        except Exception:
                            pass
                except Exception as e:
                    logger.warning(f"Research failed for {lead_id}: {e}")
                
                # Step 3: AI summary
                try:
                    building_types_dict = None
                    if lead.building_types:
                        building_types_dict = {
                            'condo': lead.building_types.condo,
                            'coop': lead.building_types.coop,
                            'rental_elevator': lead.building_types.rental_elevator,
                            'rental_walkup': lead.building_types.rental_walkup,
                            'small_residential': lead.building_types.small_residential,
                        }
                    
                    description, failure_reason = generate_company_description(
                        company_name=company_name,
                        portfolio_size=lead.portfolio_size,
                        total_units=lead.total_units,
                        boroughs=lead.boros,
                        building_types=building_types_dict,
                        owner_names=[lead.owner_principal] if lead.owner_principal else None,
                    )
                    if description:
                        lead.business_summary = description
                except Exception as e:
                    logger.warning(f"AI summary failed for {lead_id}: {e}")
                
                # Update status
                has_contact = bool(lead.phone or lead.email)
                has_website = bool(lead.website)
                if has_contact and has_website:
                    lead.enrichment_status = 'complete'
                elif has_contact or has_website:
                    lead.enrichment_status = 'partial'
                else:
                    lead.enrichment_status = 'failed'
                
                _leads_cache[cache_idx] = lead
                
                # Persist to DB
                db.update_lead(lead_id, {
                    "phone": lead.phone,
                    "email": lead.email,
                    "website": lead.website,
                    "owner_principal": lead.owner_principal,
                    "enrichment_status": lead.enrichment_status,
                    "business_summary": lead.business_summary,
                })
                
                with _enrichment_lock:
                    _enrichment_state["completed"] = _enrichment_state.get("completed", 0) + 1
                
                logger.info(f"Full enrichment [{idx+1}/{len(lead_ids)}]: {company_name} -> {lead.enrichment_status}")
                
            except Exception as e:
                logger.error(f"Full enrichment failed for {lead_id}: {e}")
                with _enrichment_lock:
                    _enrichment_state["failed"] = _enrichment_state.get("failed", 0) + 1
            
            # Rate limiting: small delay between leads to avoid hammering APIs
            import time
            time.sleep(2)
    
    except Exception as e:
        logger.error(f"Full enrichment batch error: {e}")
        with _enrichment_lock:
            _enrichment_state["error"] = str(e)
    finally:
        with _enrichment_lock:
            _enrichment_state["running"] = False
            _enrichment_state["phase"] = None
            _enrichment_state["current_lead"] = None
            _enrichment_state["finished_at"] = datetime.now().isoformat()


@app.post("/api/enrich/batch")
async def enrich_batch(
    background_tasks: BackgroundTasks,
    limit: int = Query(50, le=500, description="Max leads to enrich"),
    min_score: Optional[float] = Query(None, description="Only enrich leads with score >= this"),
    background: bool = Query(True, description="Run in background (recommended)"),
):
    """
    Enrich a batch of top leads.
    
    By default runs in background so you don't have to wait.
    Check progress at GET /api/enrich/status
    
    Prioritizes high-score leads that haven't been enriched.
    """
    global _leads_cache, _enrichment_state
    
    # Check if already running
    with _enrichment_lock:
        if _enrichment_state["running"]:
            return {
                "status": "already_running",
                "message": "Background enrichment is already in progress",
                "progress": _enrichment_state["progress"],
                "total": _enrichment_state["total"],
            }
    
    if not _leads_cache:
        raise HTTPException(status_code=400, detail="No leads loaded. Run /api/refresh first.")
    
    if background:
        # Run in background
        background_tasks.add_task(_run_background_enrichment, limit, min_score)
        return {
            "status": "started",
            "message": f"Background enrichment started for up to {limit} leads",
            "check_progress": "/api/enrich/status",
        }
    else:
        # Run synchronously (old behavior, may timeout)
        enricher = Enricher(use_cache=True)
        enriched = enricher.enrich_batch(
            _leads_cache,
            limit=limit,
            skip_enriched=True,
            min_score=min_score,
        )
        
        # Update cache and persist to database
        lead_index = {l.lead_id: i for i, l in enumerate(_leads_cache)}
        updated_leads = []
        db = get_database()
        
        for lead in enriched:
            if lead.lead_id in lead_index:
                _leads_cache[lead_index[lead.lead_id]] = lead
                updated_leads.append(lead)
        
        if updated_leads:
            db.save_leads(updated_leads)
            logger.info(f"Persisted {len(updated_leads)} enriched leads to database")
        
        return {
            "status": "success",
            "enriched_count": len(enriched),
        }


@app.post("/api/enrich/all")
async def enrich_all_leads(
    background_tasks: BackgroundTasks,
    dos_enabled: bool = Query(True, description="Run NY DOS lookups (fast, parallel)"),
    web_enabled: bool = Query(True, description="Run web crawling (rate-limited)"),
    max_leads: Optional[int] = Query(None, description="Max leads to process (None = all)"),
    max_web_crawl: int = Query(500, description="Max leads to web crawl"),
    min_portfolio: int = Query(5, description="Min portfolio size for web crawl"),
    min_score: float = Query(0.0, description="Min score for web crawl"),
):
    """
    High-performance batch enrichment for all leads.
    
    Two-phase process:
    1. NY DOS lookups (parallel, fast) - all corporation-like leads
    2. Web crawling (rate-limited) - high-priority leads only
    
    This is the recommended way to enrich large datasets (100k+ leads).
    DOS lookups typically complete in minutes, web crawling takes longer.
    
    Check progress at GET /api/enrich/status
    """
    global _leads_cache, _enrichment_state
    
    # Check if already running
    with _enrichment_lock:
        if _enrichment_state["running"]:
            return {
                "status": "already_running",
                "message": "Background enrichment is already in progress",
                "phase": _enrichment_state["phase"],
                "progress": _enrichment_state["progress"],
                "total": _enrichment_state["total"],
                "check_progress": "/api/enrich/status",
            }
    
    if not _leads_cache:
        raise HTTPException(status_code=400, detail="No leads loaded. Run /api/refresh first.")
    
    # Run in background
    background_tasks.add_task(
        _run_batch_enrichment,
        dos_enabled=dos_enabled,
        web_enabled=web_enabled,
        max_leads=max_leads,
        max_web_crawl=max_web_crawl,
        min_portfolio=min_portfolio,
        min_score=min_score,
    )
    
    return {
        "status": "started",
        "message": f"Batch enrichment started for {len(_leads_cache)} leads",
        "config": {
            "dos_enabled": dos_enabled,
            "web_enabled": web_enabled,
            "max_leads": max_leads,
            "max_web_crawl": max_web_crawl,
            "min_portfolio_for_web": min_portfolio,
            "min_score_for_web": min_score,
        },
        "check_progress": "/api/enrich/status",
    }


@app.get("/api/enrich/status")
async def get_enrichment_status():
    """
    Get the status of background enrichment.
    
    Returns progress, current lead being processed, and completion stats.
    Enhanced for batch enrichment with DOS/web phase tracking.
    
    NOTE: Reads state WITHOUT the lock to avoid blocking the event loop.
    A slightly stale read is acceptable for a status display endpoint.
    """
    # Snapshot the state dict to avoid partial reads (dict copy is atomic in CPython)
    state = dict(_enrichment_state)
    total = state.get("total", 0)
    progress = state.get("progress", 0)
    
    # Get persistent completion timestamp
    db = get_database()
    last_completed = db.get_setting('enrichment_completed_at')
    
    return {
        "running": state.get("running", False),
        "phase": state.get("phase", ""),
        "progress": progress,
        "total": total,
        "completed": state.get("completed", 0),
        "failed": state.get("failed", 0),
        "dos_completed": state.get("dos_completed", 0),
        "dos_found": state.get("dos_found", 0),
        "web_completed": state.get("web_completed", 0),
        "web_found": state.get("web_found", 0),
        "current_lead": state.get("current_lead"),
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
        "error": state.get("error"),
        "percent_complete": round(progress / total * 100, 1) if total > 0 else 0,
        "last_completed_at": last_completed,
    }


# Global stop flag for enrichment
_enrichment_stop_requested = False


@app.post("/api/enrich/stop")
async def stop_enrichment():
    """
    Stop the running enrichment job.
    
    The job will stop after completing the current lead.
    """
    global _enrichment_stop_requested
    
    with _enrichment_lock:
        if not _enrichment_state["running"]:
            return {
                "status": "not_running",
                "message": "No enrichment job is currently running",
            }
        
        _enrichment_stop_requested = True
        return {
            "status": "stopping",
            "message": "Stop requested. Job will stop after current lead completes.",
            "current_lead": _enrichment_state["current_lead"],
        }


@app.post("/api/enrich/reset")
async def reset_enrichment():
    """
    Reset all stuck enrichment jobs and optionally reset failed leads.
    
    This is useful when:
    - A batch job got stuck in 'running' state
    - Leads are stuck in 'failed' status and you want to retry
    """
    global _enrichment_state, _enrichment_stop_requested
    
    db = get_database()
    
    # Force-stop any in-memory enrichment state
    with _enrichment_lock:
        was_running = _enrichment_state["running"]
        _enrichment_state = {
            "running": False, "phase": "", "progress": 0, "total": 0,
            "completed": 0, "failed": 0, "dos_completed": 0, "dos_found": 0,
            "web_completed": 0, "web_found": 0, "current_lead": None,
            "started_at": None, "finished_at": None, "error": None,
        }
        _enrichment_stop_requested = False
    
    # Mark all 'running' DB jobs as 'failed'
    with db._get_connection() as conn:
        stuck_jobs = conn.execute(
            "SELECT id FROM enrichment_jobs WHERE status = 'running'"
        ).fetchall()
        for job in stuck_jobs:
            conn.execute(
                "UPDATE enrichment_jobs SET status = 'failed', error = 'Force reset', "
                "finished_at = ?, updated_at = ? WHERE id = ?",
                (datetime.now().isoformat(), datetime.now().isoformat(), job['id'])
            )
        
        # Reset 'failed' leads back to 'none' so they can be re-enriched
        failed_count = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE enrichment_status = 'failed'"
        ).fetchone()[0]
        conn.execute(
            "UPDATE leads SET enrichment_status = 'none' WHERE enrichment_status = 'failed'"
        )
        conn.commit()
    
    # Also update the in-memory cache
    for lead in _leads_cache:
        if lead.enrichment_status == 'failed':
            lead.enrichment_status = 'none'
    
    return {
        "status": "reset_complete",
        "was_running": was_running,
        "stuck_jobs_cleared": len(stuck_jobs),
        "failed_leads_reset": failed_count,
        "message": f"Reset complete. {failed_count} failed leads can now be re-enriched.",
    }


@app.post("/api/classify-entities")
async def classify_entities():
    """
    Run entity classification on all existing leads in the database.
    
    This is a one-time migration to populate entity_type, company_name,
    primary_contact, and primary_contact_title for leads that were saved
    before entity classification was added.
    """
    import json as _json
    from src.transform.aggregate import _classify_entity
    
    db = get_database()
    classified = 0
    
    with db._get_connection() as conn:
        rows = conn.execute(
            "SELECT lead_id, agent_name, owner_name, owner_type, contacts "
            "FROM leads WHERE entity_type IS NULL OR entity_type = 'unknown'"
        ).fetchall()
        
        for row in rows:
            contacts = _json.loads(row['contacts'] or '[]')
            entity_info = _classify_entity(
                row['agent_name'] or '',
                row['owner_name'] or '',
                row['owner_type'] or '',
                contacts
            )
            
            conn.execute(
                "UPDATE leads SET entity_type=?, company_name=?, primary_contact=?, "
                "primary_contact_title=? WHERE lead_id=?",
                (
                    entity_info['entity_type'],
                    entity_info['company_name'],
                    entity_info['primary_contact'],
                    entity_info['primary_contact_title'],
                    row['lead_id'],
                )
            )
            classified += 1
        
        conn.commit()
    
    # Also update in-memory cache
    for lead in _leads_cache:
        if lead.entity_type in (None, 'unknown'):
            entity_info = _classify_entity(
                lead.agent_name, lead.owner_name, lead.owner_type, lead.contacts
            )
            lead.entity_type = entity_info['entity_type']
            lead.company_name = entity_info['company_name']
            lead.primary_contact = entity_info['primary_contact']
            lead.primary_contact_title = entity_info['primary_contact_title']
    
    return {
        "status": "complete",
        "classified": classified,
        "message": f"Classified {classified} leads into entity types.",
    }


def _run_api_enrichment(limit: int, min_portfolio: int = 5, boro: Optional[str] = None):
    """
    API-only batch enrichment using Google Places + NY DOS.
    
    This avoids web scraping/Google search which gets rate limited.
    Uses only reliable API sources:
    - Google Places API: Phone numbers, website (uses your API key, $200/mo free)
    - NY DOS Registry: Corporation info (completely free, no limits)
    """
    global _leads_cache, _enrichment_state, _enrichment_stop_requested
    
    from src.enrich.contact_sources import GooglePlacesEnricher
    from src.enrich.ny_dos import NYDOSClient
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import re
    
    _enrichment_stop_requested = False
    
    with _enrichment_lock:
        _enrichment_state["running"] = True
        _enrichment_state["phase"] = "api_enrichment"
        _enrichment_state["progress"] = 0
        _enrichment_state["total"] = limit
        _enrichment_state["completed"] = 0
        _enrichment_state["failed"] = 0
        _enrichment_state["dos_completed"] = 0
        _enrichment_state["dos_found"] = 0
        _enrichment_state["web_completed"] = 0  # Will track Google Places instead
        _enrichment_state["web_found"] = 0
        _enrichment_state["started_at"] = datetime.now().isoformat()
        _enrichment_state["finished_at"] = None
        _enrichment_state["error"] = None
    
    try:
        google_places = GooglePlacesEnricher()
        dos_client = NYDOSClient()
        db = get_database()
        
        if not google_places.is_configured():
            logger.warning("Google Places API key not configured - will only use NY DOS")
        else:
            logger.info("Google Places API configured - will use for phone/website lookup")
        
        # Filter candidates - prioritize by portfolio size
        candidates = []
        for lead in _leads_cache:
            # Skip already enriched with contact info
            if lead.phone or lead.email:
                continue
            # Portfolio filter
            if lead.portfolio_size < min_portfolio:
                continue
            # Borough filter
            if boro and lead.boro != boro.upper():
                continue
            candidates.append(lead)
        
        # Sort by portfolio size (larger = more valuable) 
        candidates.sort(key=lambda l: (l.score, l.portfolio_size), reverse=True)
        batch = candidates[:limit]
        
        with _enrichment_lock:
            _enrichment_state["total"] = len(batch)
        
        logger.info(f"API enrichment started: {len(batch)} leads (Google Places + NY DOS)")
        
        lead_index = {l.lead_id: i for i, l in enumerate(_leads_cache)}
        
        # Process in parallel with thread pool
        def enrich_single_lead(lead):
            """Enrich a single lead using APIs only."""
            name = lead.agent_name or lead.owner_name
            if not name:
                return lead, False, False
            
            google_found = False
            dos_found = False
            
            # 1. Try Google Places for phone/website
            if google_places.is_configured():
                try:
                    gp_result = google_places.find_business(name)
                    if gp_result:
                        if gp_result.get("phone") and not lead.phone:
                            # Normalize phone
                            phone = gp_result["phone"]
                            digits = re.sub(r"\D", "", phone)
                            if len(digits) == 11 and digits.startswith("1"):
                                digits = digits[1:]
                            if len(digits) == 10:
                                lead.phone = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
                                if "has_phone" not in lead.tags:
                                    lead.tags.append("has_phone")
                                google_found = True
                        
                        if gp_result.get("website") and not lead.website:
                            lead.website = gp_result["website"]
                            if "has_website" not in lead.tags:
                                lead.tags.append("has_website")
                            google_found = True
                        
                        if "google_places" not in lead.enrichment_sources:
                            lead.enrichment_sources.append("google_places")
                except Exception as e:
                    logger.debug(f"Google Places failed for {name}: {e}")
            
            # 2. Try NY DOS for corporation info (if name looks like a corp)
            corp_indicators = ['LLC', 'L.L.C.', 'INC', 'CORP', 'LP', 'L.P.', 'LLP', 'COMPANY', 'CO.', 'PARTNERS']
            if any(ind in name.upper() for ind in corp_indicators):
                try:
                    dos_entity = dos_client.lookup_entity(name)
                    if dos_entity:
                        if dos_entity.process_name and not lead.owner_principal:
                            lead.owner_principal = dos_entity.process_name
                        
                        if not lead.business_summary:
                            # Format DOS data as a readable sentence (not raw pipe-delimited string)
                            sentence_parts = []
                            entity_type = (dos_entity.entity_type or "").replace("_", " ").title()
                            if entity_type:
                                sentence_parts.append(f"Registered as a {entity_type.lower()} in New York State")
                            if dos_entity.formation_date:
                                sentence_parts.append(f"formed {dos_entity.formation_date[:10]}")
                            if dos_entity.status:
                                sentence_parts.append(f"currently {dos_entity.status.lower()}")
                            if dos_entity.process_name:
                                sentence_parts.append(f"with {dos_entity.process_name} as registered agent")
                            if sentence_parts:
                                lead.business_summary = ". ".join(s.capitalize() if i == 0 else s for i, s in enumerate(sentence_parts)) + "."
                        
                        lead.dos_id = dos_entity.dos_id
                        lead.dos_status = dos_entity.status
                        
                        if "ny_dos_verified" not in lead.tags:
                            lead.tags.append("ny_dos_verified")
                        if "ny_dos" not in lead.enrichment_sources:
                            lead.enrichment_sources.append("ny_dos")
                        
                        dos_found = True
                except Exception as e:
                    logger.debug(f"NY DOS failed for {name}: {e}")
            
            # Update enrichment status
            lead.last_enriched = datetime.now()
            has_contact = bool(lead.phone or lead.email)
            has_website = bool(lead.website)
            if has_contact and has_website:
                lead.enrichment_status = "complete"
            elif has_contact or has_website or dos_found:
                lead.enrichment_status = "partial"
            else:
                lead.enrichment_status = "failed"
            
            return lead, google_found, dos_found
        
        # Process with thread pool (5 workers for APIs)
        completed_count = 0
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(enrich_single_lead, lead): lead for lead in batch}
            
            for future in as_completed(futures):
                if _enrichment_stop_requested:
                    logger.info("API enrichment stopped by user")
                    with _enrichment_lock:
                        _enrichment_state["error"] = "Stopped by user"
                    break
                
                original_lead = futures[future]
                try:
                    enriched_lead, gp_found, dos_found = future.result()
                    
                    # Update cache
                    with _leads_lock:
                        if enriched_lead.lead_id in lead_index:
                            _leads_cache[lead_index[enriched_lead.lead_id]] = enriched_lead
                    
                    # Persist to DB
                    db.save_leads([enriched_lead])
                    
                    completed_count += 1
                    with _enrichment_lock:
                        _enrichment_state["progress"] = completed_count
                        _enrichment_state["current_lead"] = enriched_lead.agent_name or enriched_lead.owner_name
                        if gp_found:
                            _enrichment_state["web_found"] += 1  # Using web_found for Google Places
                        if dos_found:
                            _enrichment_state["dos_found"] += 1
                        if enriched_lead.enrichment_status in ["complete", "partial"]:
                            _enrichment_state["completed"] += 1
                        else:
                            _enrichment_state["failed"] += 1
                    
                    if completed_count % 50 == 0:
                        logger.info(f"API enrichment progress: {completed_count}/{len(batch)}")
                        
                except Exception as e:
                    logger.warning(f"Failed to enrich {original_lead.agent_name}: {e}")
                    with _enrichment_lock:
                        _enrichment_state["failed"] += 1
        
        logger.info(f"API enrichment complete: {_enrichment_state['completed']} enriched, {_enrichment_state['failed']} failed")
        logger.info(f"  Google Places found: {_enrichment_state['web_found']}, NY DOS found: {_enrichment_state['dos_found']}")
        
    except Exception as e:
        logger.error(f"API enrichment failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        with _enrichment_lock:
            _enrichment_state["error"] = str(e)
    finally:
        _enrichment_stop_requested = False
        with _enrichment_lock:
            _enrichment_state["running"] = False
            _enrichment_state["phase"] = "complete"
            _enrichment_state["current_lead"] = None
            _enrichment_state["finished_at"] = datetime.now().isoformat()


@app.post("/api/enrich/api-only")
async def enrich_api_only(
    background_tasks: BackgroundTasks,
    limit: int = Query(500, le=2000, description="Max leads to enrich"),
    min_portfolio: int = Query(5, description="Minimum portfolio size"),
    boro: Optional[str] = Query(None, description="Filter by borough (e.g., MANHATTAN)"),
):
    """
    API-only batch enrichment using Google Places + NY DOS.
    
    This is the recommended enrichment method - no web scraping or Google search.
    Uses only reliable, rate-limit-free API sources:
    
    1. Google Places API: Phone numbers and websites
       - Uses your configured API key (GOOGLE_PLACES_API_KEY)
       - $200/month free credit (~11,700 lookups)
       
    2. NY DOS Registry: Corporation info (registered agent, formation date)
       - Completely free, no rate limits
       - Only for corporation-like names (LLC, Inc, Corp, etc.)
    
    Check progress at GET /api/enrich/status
    """
    global _leads_cache, _enrichment_state
    
    # Check if already running
    with _enrichment_lock:
        if _enrichment_state["running"]:
            return {
                "status": "already_running",
                "message": "Background enrichment is already in progress",
                "progress": _enrichment_state["progress"],
                "total": _enrichment_state["total"],
                "check_progress": "/api/enrich/status",
            }
    
    if not _leads_cache:
        raise HTTPException(status_code=400, detail="No leads loaded. Run /api/refresh first.")
    
    # Check Google Places config
    from src.enrich.contact_sources import GooglePlacesEnricher
    gp = GooglePlacesEnricher()
    
    # Run in background
    background_tasks.add_task(_run_api_enrichment, limit, min_portfolio, boro)
    
    return {
        "status": "started",
        "message": f"API-only enrichment started for up to {limit} leads",
        "config": {
            "limit": limit,
            "min_portfolio": min_portfolio,
            "boro": boro,
            "google_places_configured": gp.is_configured(),
        },
        "check_progress": "/api/enrich/status",
    }


@app.get("/api/dos/lookup")
async def lookup_dos_entity(
    name: str = Query(..., description="Entity name to look up"),
    include_inactive: bool = Query(False, description="Include inactive entities"),
):
    """
    Look up a business entity in the NY DOS registry.
    
    Returns entity details including registered agent info.
    """
    client = NYDOSClient()
    entity = client.lookup_entity(name, include_inactive=include_inactive)
    
    if not entity:
        raise HTTPException(status_code=404, detail=f"No entity found matching '{name}'")
    
    return {
        "dos_id": entity.dos_id,
        "name": entity.name,
        "entity_type": entity.entity_type,
        "status": entity.status,
        "jurisdiction": entity.jurisdiction,
        "formation_date": entity.formation_date,
        "county": entity.county,
        "process_name": entity.process_name,
        "process_address": entity.process_address,
    }


@app.get("/api/dos/search")
async def search_dos_entities(
    name: str = Query(..., description="Search term"),
    limit: int = Query(10, le=50, description="Max results"),
):
    """
    Search for business entities in the NY DOS registry.
    
    Returns multiple matching entities.
    """
    client = NYDOSClient()
    entities = client.search_entities(name, limit=limit)
    
    return {
        "count": len(entities),
        "results": [
            {
                "dos_id": e.dos_id,
                "name": e.name,
                "entity_type": e.entity_type,
                "status": e.status,
                "formation_date": e.formation_date,
                "process_name": e.process_name,
            }
            for e in entities
        ],
    }


# PLUTO building type enrichment state
_pluto_enrichment_state: Dict = {
    "running": False,
    "progress": 0,
    "total": 0,
    "found": 0,
    "started_at": None,
    "finished_at": None,
    "error": None,
}
_pluto_enrichment_lock = threading.Lock()


def _run_pluto_enrichment(limit: Optional[int] = None):
    """Background task to enrich leads with PLUTO building type data."""
    global _leads_cache, _pluto_enrichment_state
    
    from src.ingest.pluto_client import PLUTOClient, classify_building
    from src.transform.aggregate import BuildingTypeBreakdown
    from collections import Counter
    
    with _pluto_enrichment_lock:
        _pluto_enrichment_state["running"] = True
        _pluto_enrichment_state["progress"] = 0
        _pluto_enrichment_state["total"] = len(_leads_cache) if not limit else min(limit, len(_leads_cache))
        _pluto_enrichment_state["found"] = 0
        _pluto_enrichment_state["started_at"] = datetime.now().isoformat()
        _pluto_enrichment_state["finished_at"] = None
        _pluto_enrichment_state["error"] = None
    
    try:
        pluto_client = PLUTOClient()
        db = get_database()
        
        # Get leads to process
        leads_to_process = _leads_cache[:limit] if limit else _leads_cache
        
        logger.info(f"PLUTO enrichment started for {len(leads_to_process)} leads")
        
        # We need building-level data with BBL info
        # Get all unique building IDs and their BBL info
        # First, let's enrich per lead by looking up their buildings
        
        for i, lead in enumerate(leads_to_process):
            with _pluto_enrichment_lock:
                _pluto_enrichment_state["progress"] = i + 1
            
            # Skip if already has building type data
            if lead.building_types and lead.building_types.total > 0:
                continue
            
            # We need to fetch building info from HPD to get BBL
            # For now, we'll mark this as a limitation and note that a full refresh
            # with PLUTO integration is needed
            
            # Initialize breakdown
            breakdown = BuildingTypeBreakdown()
            building_classes = Counter()
            
            # For each building ID, we'd need to look up the BBL
            # This requires the original building data which we don't have in the lead
            # We'll need to enhance the refresh pipeline to include PLUTO data
            
            # For now, set all as unknown
            breakdown.unknown = lead.portfolio_size
            
            lead.building_types = breakdown
            lead.building_classes = dict(building_classes)
            
            if i > 0 and i % 1000 == 0:
                logger.info(f"PLUTO enrichment progress: {i}/{len(leads_to_process)}")
        
        # Note: This is a placeholder implementation
        # Full PLUTO integration requires enhancing the refresh pipeline
        # to fetch PLUTO data during building ingestion
        
        logger.info(f"PLUTO enrichment complete (placeholder - needs refresh pipeline integration)")
        
    except Exception as e:
        logger.error(f"PLUTO enrichment failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        with _pluto_enrichment_lock:
            _pluto_enrichment_state["error"] = str(e)
    finally:
        with _pluto_enrichment_lock:
            _pluto_enrichment_state["running"] = False
            _pluto_enrichment_state["finished_at"] = datetime.now().isoformat()


@app.post("/api/enrich/pluto")
async def enrich_pluto(
    background_tasks: BackgroundTasks,
    limit: Optional[int] = Query(None, description="Max leads to enrich (None = all)"),
):
    """
    Enrich leads with PLUTO building type classifications.
    
    Adds building type breakdown (condo, coop, rental_elevator, rental_walkup, etc.)
    to each lead based on NYC PLUTO data.
    
    NOTE: For full PLUTO integration, run a fresh /api/refresh which will
    fetch PLUTO data during building ingestion. This endpoint is for
    incremental updates.
    """
    with _pluto_enrichment_lock:
        if _pluto_enrichment_state["running"]:
            return {
                "status": "already_running",
                "progress": _pluto_enrichment_state["progress"],
                "total": _pluto_enrichment_state["total"],
            }
    
    if not _leads_cache:
        raise HTTPException(status_code=400, detail="No leads loaded. Run /api/refresh first.")
    
    background_tasks.add_task(_run_pluto_enrichment, limit)
    
    return {
        "status": "started",
        "message": f"PLUTO enrichment started for {limit or len(_leads_cache)} leads",
        "note": "For full PLUTO integration, run /api/refresh?full=true&pluto=true",
    }


@app.get("/api/enrich/pluto/status")
async def get_pluto_enrichment_status():
    """Get status of PLUTO enrichment."""
    with _pluto_enrichment_lock:
        return {
            "running": _pluto_enrichment_state["running"],
            "progress": _pluto_enrichment_state["progress"],
            "total": _pluto_enrichment_state["total"],
            "found": _pluto_enrichment_state["found"],
            "started_at": _pluto_enrichment_state["started_at"],
            "finished_at": _pluto_enrichment_state["finished_at"],
            "error": _pluto_enrichment_state["error"],
        }


@app.get("/api/pluto/lookup")
async def lookup_pluto(
    boro_id: str = Query(..., description="Borough ID (1=Manhattan, 2=Bronx, 3=Brooklyn, 4=Queens, 5=SI)"),
    block: str = Query(..., description="Tax block number"),
    lot: str = Query(..., description="Tax lot number"),
):
    """
    Look up a single property's PLUTO classification.
    
    Returns building type (condo, coop, rental, etc.) and other property details.
    """
    from src.ingest.pluto_client import PLUTOClient
    
    client = PLUTOClient()
    result = client.get_classification(boro_id, block, lot)
    
    if not result:
        raise HTTPException(status_code=404, detail=f"No PLUTO record found for BBL {boro_id}-{block}-{lot}")
    
    return {
        "bbl": result.bbl,
        "address": result.address,
        "building_class": result.building_class,
        "building_type": result.building_type,
        "land_use": result.land_use,
        "units_residential": result.units_res,
        "units_total": result.units_total,
        "year_built": result.year_built,
    }


class OutreachAttemptRequest(BaseModel):
    """Request to log an outreach attempt."""
    method: str  # phone, email, linkedin, in_person, other
    outcome: str  # no_answer, left_voicemail, spoke_with_contact, sent_email, meeting_scheduled, not_interested, other
    notes: Optional[str] = None


@app.post("/api/leads/{lead_id}/enrich-contacts")
async def enrich_lead_contacts(lead_id: str):
    """
    Enrich a single lead's contact info using multiple sources.
    
    Sources (in priority order):
    1. Google Places API - Business phone numbers (free $200/mo credit)
    2. NY DOS Registry - Corporation info, registered agent (free)
    3. Web Crawl - Scrape company website (free)
    4. Hunter.io - Email finder (optional, 25 free/month)
    
    Returns all found contacts with source attribution and confidence scores.
    """
    global _leads_cache
    
    # Find the lead
    lead = None
    lead_idx = None
    for i, l in enumerate(_leads_cache):
        if l.lead_id == lead_id:
            lead = l
            lead_idx = i
            break
    
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    company_name = lead.agent_name or lead.owner_name
    
    try:
        from src.enrich.contact_sources import MultiSourceEnricher, ContactInfo as SourceContactInfo
        from src.transform.aggregate import ContactWithSource
        
        enricher = MultiSourceEnricher()
        result = enricher.enrich(
            lead_id=lead_id,
            company_name=company_name,
            website=lead.website,
            location=f"{lead.boro}, New York" if lead.boro else "New York, NY",
            address=lead.address,
            contacts=lead.contacts,
        )
        
        # Update lead with results
        if result.phones:
            # Convert to Lead's ContactWithSource format
            lead.phones = [
                ContactWithSource(
                    value=p.value,
                    type=p.type,
                    source=p.source,
                    source_url=p.source_url,
                    confidence=p.confidence,
                    verified=p.verified,
                    found_at=p.found_at,
                )
                for p in result.phones
            ]
            # Set best phone for backwards compatibility
            best = result.best_phone()
            if best:
                lead.phone = best.value
        
        if result.emails:
            lead.emails = [
                ContactWithSource(
                    value=e.value,
                    type=e.type,
                    source=e.source,
                    source_url=e.source_url,
                    confidence=e.confidence,
                    verified=e.verified,
                    found_at=e.found_at,
                )
                for e in result.emails
            ]
            best = result.best_email()
            if best:
                lead.email = best.value
        
        if result.website and not lead.website:
            lead.website = result.website
            lead.website_source = result.website_source
        
        # Update enrichment status
        if result.phones or result.emails:
            lead.enrichment_status = "complete" if (result.phones and result.emails) else "partial"
        
        lead.enrichment_sources = list(set(lead.enrichment_sources + result.sources_succeeded))
        lead.last_enriched = datetime.now()
        lead.updated_at = datetime.now()
        
        # Update cache
        _leads_cache[lead_idx] = lead
        
        # Persist to database
        db = get_database()
        db.save_leads([lead])
        
        return {
            "status": "success",
            "lead_id": lead_id,
            "company_name": company_name,
            "phones": [p.to_dict() for p in result.phones],
            "emails": [e.to_dict() for e in result.emails],
            "website": result.website,
            "website_source": result.website_source,
            # NY DOS data
            "dos_info": {
                "dos_id": result.dos_id,
                "entity_name": result.dos_entity_name,
                "entity_type": result.dos_entity_type,
                "formation_date": result.dos_formation_date,
                "registered_agent": result.dos_registered_agent,
                "registered_address": result.dos_registered_address,
                "lookup_url": result.dos_lookup_url,
            } if result.dos_id else None,
            "sources_tried": result.sources_tried,
            "sources_succeeded": result.sources_succeeded,
            "errors": result.errors,
            "api_status": enricher.get_api_status(),
        }
        
    except Exception as e:
        logger.error(f"Multi-source enrichment failed for {lead_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/enrichment/sources")
async def get_enrichment_sources():
    """
    Get status of configured enrichment sources.
    
    Shows which APIs are configured and available.
    """
    try:
        from src.enrich.contact_sources import MultiSourceEnricher
        enricher = MultiSourceEnricher()
        return enricher.get_api_status()
    except Exception as e:
        return {
            "error": str(e),
            "google_places": {"configured": False},
            "hunter": {"configured": False},
            "web_crawl": {"configured": True},
        }


@app.post("/api/leads/{lead_id}/research")
async def research_lead(lead_id: str):
    """
    Deep research a lead by scraping their website.
    
    Extracts owner names, year established, service areas, contact info, etc.
    """
    global _leads_cache
    
    # Find the lead
    lead = None
    for l in _leads_cache:
        if l.lead_id == lead_id:
            lead = l
            break
    
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    # Import the deep research function
    from src.enrich.web_crawl import WebCrawler
    
    crawler = WebCrawler()
    company_name = lead.agent_name or lead.owner_name
    
    # First, try to find their website if we don't have it
    if not lead.website:
        website = crawler.find_website(company_name + " property management NYC")
        if website:
            lead.website = website
            # Update the lead in cache
            for i, l in enumerate(_leads_cache):
                if l.lead_id == lead_id:
                    _leads_cache[i] = lead
                    break
    
    # If we have a website, do deep scrape
    research_result = {
        "lead_id": lead_id,
        "owner_names": None,
        "year_established": None,
        "service_areas": None,
        "description": None,
        "phones": None,
        "emails": None,
        "social_links": None,
        "scraped_at": datetime.now().isoformat(),
    }
    
    if lead.website:
        try:
            deep_data = crawler.deep_scrape(lead.website, company_name)
            research_result.update({
                "owner_names": deep_data.get("owner_names"),
                "year_established": deep_data.get("year_established"),
                "service_areas": deep_data.get("service_areas"),
                "description": deep_data.get("description"),
                "phones": deep_data.get("phones"),
                "emails": deep_data.get("emails"),
                "social_links": deep_data.get("social_links"),
            })
        except Exception as e:
            logger.error(f"Deep scrape failed for {lead.website}: {e}")
    
    # Also try NY DOS lookup for additional info
    try:
        dos_client = NYDOSClient()
        dos_entity = dos_client.lookup_entity(company_name)
        if dos_entity:
            if dos_entity.formation_date and not research_result["year_established"]:
                research_result["year_established"] = dos_entity.formation_date[:4]  # Just the year
            if dos_entity.process_name and not research_result["owner_names"]:
                # Registered agent might be the owner
                research_result["owner_names"] = [dos_entity.process_name]
    except Exception as e:
        logger.error(f"DOS lookup failed: {e}")
    
    # Generate AI summary if API key is configured
    try:
        from src.enrich.ai_summary import generate_company_description
        
        building_types_dict = None
        if lead.building_types:
            building_types_dict = {
                'condo': lead.building_types.condo,
                'coop': lead.building_types.coop,
                'rental_elevator': lead.building_types.rental_elevator,
                'rental_walkup': lead.building_types.rental_walkup,
            }
        
        ai_description, _ai_reason = generate_company_description(
            company_name=company_name,
            portfolio_size=lead.portfolio_size,
            total_units=lead.total_units,
            boroughs=lead.boros,
            building_types=building_types_dict,
            owner_names=research_result.get("owner_names"),
            year_established=research_result.get("year_established"),
            service_areas=research_result.get("service_areas"),
            website_description=research_result.get("description"),
        )
        
        if ai_description:
            research_result["ai_description"] = ai_description
            # Also save to lead
            lead.business_summary = ai_description
            for i, l in enumerate(_leads_cache):
                if l.lead_id == lead_id:
                    _leads_cache[i] = lead
                    break
            db = get_database()
            db.update_lead(lead_id, {"business_summary": ai_description})
    except Exception as e:
        logger.error(f"AI summary generation failed: {e}")
    
    return research_result


@app.post("/api/leads/{lead_id}/ai-summary")
async def generate_ai_summary(lead_id: str):
    """
    Generate an AI-powered description of the company.
    
    Uses Claude to create a succinct 2-3 sentence description based on
    the company's portfolio, building types, and any research data.
    
    Requires ANTHROPIC_API_KEY environment variable to be set.
    """
    global _leads_cache
    
    # Find the lead
    lead = None
    for l in _leads_cache:
        if l.lead_id == lead_id:
            lead = l
            break
    
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    # R11: Check AI summary cache first
    db = get_database()
    cached_summary = db.get_ai_summary_cache(lead_id)
    if cached_summary:
        logger.info(f"AI summary cache hit for lead {lead_id}")
        return {
            "status": "cached",
            "lead_id": lead_id,
            "ai_description": cached_summary,
        }
    
    from src.enrich.ai_summary import generate_company_description, ANTHROPIC_MODEL
    
    company_name = lead.agent_name or lead.owner_name
    
    # Get building types dict if available
    building_types_dict = None
    if lead.building_types:
        building_types_dict = {
            'condo': lead.building_types.condo,
            'coop': lead.building_types.coop,
            'rental_elevator': lead.building_types.rental_elevator,
            'rental_walkup': lead.building_types.rental_walkup,
            'small_residential': lead.building_types.small_residential,
            'other': lead.building_types.other,
        }
    
    # Generate description — DO NOT pass lead.business_summary as website_description
    # (that creates a circular reference where old summaries feed into new ones)
    description, failure_reason = generate_company_description(
        company_name=company_name,
        portfolio_size=lead.portfolio_size,
        total_units=lead.total_units,
        boroughs=lead.boros,
        building_types=building_types_dict,
        owner_names=[lead.owner_principal] if lead.owner_principal else None,
    )
    
    if not description:
        return {
            "status": "skipped",
            "lead_id": lead_id,
            "message": failure_reason or "AI summary generation failed for an unknown reason.",
            "ai_description": None,
        }
    
    # Save the description to the lead
    lead.business_summary = description
    
    # Update in cache
    for i, l in enumerate(_leads_cache):
        if l.lead_id == lead_id:
            _leads_cache[i] = lead
            break
    
    # Persist to database and AI summary cache (R11)
    db.update_lead(lead_id, {"business_summary": description})
    db.set_ai_summary_cache(lead_id, description, model=ANTHROPIC_MODEL)
    
    return {
        "status": "generated",
        "lead_id": lead_id,
        "ai_description": description,
    }


@app.post("/api/leads/{lead_id}/enrich-all")
async def enrich_lead_all(lead_id: str):
    """
    Unified enrichment: contacts (multi-source) + deep research + AI summary.
    One button, one call, does everything.
    """
    results = {
        "lead_id": lead_id,
        "contacts": {"phones_found": 0, "emails_found": 0, "website_found": False},
        "research": {"owner_names": [], "year_established": None, "website_scraped": False},
        "ai_summary": {"generated": False, "description": None},
        "errors": [],
    }
    
    # Find the lead
    lead = None
    for l in _leads_cache:
        if l.lead_id == lead_id:
            lead = l
            break
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    db = get_database()
    company_name = lead.agent_name or lead.owner_name
    
    # Step 1: Multi-source contact enrichment (Google Places, NY DOS, Web Crawl, Hunter)
    try:
        from src.enrich.multi_source import MultiSourceEnricher
        enricher = MultiSourceEnricher()
        enrich_result = enricher.enrich(
            company_name=company_name,
            borough=lead.boro,
            address=None,
        )
        
        if enrich_result.get('phone') and not lead.phone:
            lead.phone = enrich_result['phone']
        if enrich_result.get('email') and not lead.email:
            lead.email = enrich_result['email']
        if enrich_result.get('website') and not lead.website:
            lead.website = enrich_result['website']
        if enrich_result.get('owner_principal') and not lead.owner_principal:
            lead.owner_principal = enrich_result['owner_principal']
        
        # Collect all phones/emails from multi-source
        phones = enrich_result.get('phones', [])
        emails = enrich_result.get('emails', [])
        if enrich_result.get('phone') and enrich_result['phone'] not in phones:
            phones.append(enrich_result['phone'])
        if enrich_result.get('email') and enrich_result['email'] not in emails:
            emails.append(enrich_result['email'])
        
        results["contacts"]["phones_found"] = len(phones)
        results["contacts"]["emails_found"] = len(emails)
        results["contacts"]["website_found"] = bool(enrich_result.get('website'))
        
        # Update enrichment status
        has_contact = bool(lead.phone or lead.email)
        has_website = bool(lead.website)
        if has_contact and has_website:
            lead.enrichment_status = 'complete'
        elif has_contact or has_website:
            lead.enrichment_status = 'partial'
        else:
            lead.enrichment_status = 'failed'
    except Exception as e:
        logger.error(f"Contact enrichment failed for {lead_id}: {e}")
        results["errors"].append(f"Contact enrichment: {str(e)}")
    
    # Step 2: Deep research (website scrape + DOS + more contacts)
    try:
        from src.enrich.web_crawl import WebCrawler
        crawler = WebCrawler()
        
        # Find website if still missing
        if not lead.website:
            website = crawler.find_website(company_name + " property management NYC")
            if website:
                lead.website = website
                results["contacts"]["website_found"] = True
        
        # Deep scrape if we have a website
        if lead.website:
            try:
                scrape_data = crawler.deep_scrape(lead.website)
                results["research"]["website_scraped"] = True
                
                if scrape_data.get('owner_names'):
                    results["research"]["owner_names"] = scrape_data['owner_names']
                if scrape_data.get('year_established'):
                    results["research"]["year_established"] = scrape_data['year_established']
                # Pick up any additional phones/emails from scraping
                if scrape_data.get('phones'):
                    for p in scrape_data['phones']:
                        if not lead.phone:
                            lead.phone = p
                if scrape_data.get('emails'):
                    for e in scrape_data['emails']:
                        if not lead.email:
                            lead.email = e
            except Exception as scrape_err:
                logger.warning(f"Deep scrape failed for {lead_id}: {scrape_err}")
                results["errors"].append(f"Website scrape: {str(scrape_err)}")
    except Exception as e:
        logger.error(f"Research failed for {lead_id}: {e}")
        results["errors"].append(f"Research: {str(e)}")
    
    # Step 3: AI summary
    try:
        from src.enrich.ai_summary import generate_company_description
        
        building_types_dict = None
        if lead.building_types:
            building_types_dict = {
                'condo': lead.building_types.condo,
                'coop': lead.building_types.coop,
                'rental_elevator': lead.building_types.rental_elevator,
                'rental_walkup': lead.building_types.rental_walkup,
                'small_residential': lead.building_types.small_residential,
            }
        
        description, failure_reason = generate_company_description(
            company_name=company_name,
            portfolio_size=lead.portfolio_size,
            total_units=lead.total_units,
            boroughs=lead.boros,
            building_types=building_types_dict,
            owner_names=results["research"].get("owner_names") or ([lead.owner_principal] if lead.owner_principal else None),
        )
        
        if description:
            lead.business_summary = description
            results["ai_summary"]["generated"] = True
            results["ai_summary"]["description"] = description
        else:
            results["errors"].append(f"AI summary: {failure_reason or 'Unknown error'}")
    except Exception as e:
        logger.error(f"AI summary failed for {lead_id}: {e}")
        results["errors"].append(f"AI summary: {str(e)}")
    
    # Update enrichment status based on final state
    has_contact = bool(lead.phone or lead.email)
    has_website = bool(lead.website)
    if has_contact and has_website:
        lead.enrichment_status = 'complete'
    elif has_contact or has_website:
        lead.enrichment_status = 'partial'
    elif lead.enrichment_status == 'none':
        lead.enrichment_status = 'failed'
    
    # Persist to cache and DB
    for i, l in enumerate(_leads_cache):
        if l.lead_id == lead_id:
            _leads_cache[i] = lead
            break
    
    db.update_lead(lead_id, {
        "phone": lead.phone,
        "email": lead.email,
        "website": lead.website,
        "owner_principal": lead.owner_principal,
        "enrichment_status": lead.enrichment_status,
        "business_summary": lead.business_summary,
    })
    
    return results


@app.post("/api/leads/{lead_id}/outreach")
async def add_outreach_attempt(lead_id: str, request: OutreachAttemptRequest):
    """
    Log an outreach attempt for a lead.
    """
    import uuid
    
    # Validate lead exists
    lead_exists = any(l.lead_id == lead_id for l in _leads_cache)
    if not lead_exists:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    valid_methods = {"phone", "email", "linkedin", "in_person", "other"}
    valid_outcomes = {"no_answer", "left_voicemail", "spoke_with_contact", "sent_email", "meeting_scheduled", "not_interested", "other"}
    
    if request.method not in valid_methods:
        raise HTTPException(status_code=400, detail=f"Invalid method. Must be one of: {', '.join(valid_methods)}")
    if request.outcome not in valid_outcomes:
        raise HTTPException(status_code=400, detail=f"Invalid outcome. Must be one of: {', '.join(valid_outcomes)}")
    
    attempt = {
        "id": str(uuid.uuid4()),
        "method": request.method,
        "outcome": request.outcome,
        "notes": request.notes,
        "timestamp": datetime.now().isoformat(),
    }
    
    # Save to database
    db = get_database()
    db.add_outreach_attempt(lead_id, attempt)
    
    return {
        "status": "success",
        "attempt": attempt,
    }


@app.get("/api/leads/{lead_id}/outreach")
async def get_outreach_attempts(lead_id: str):
    """
    Get all outreach attempts for a lead.
    """
    # Validate lead exists
    lead_exists = any(l.lead_id == lead_id for l in _leads_cache)
    if not lead_exists:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    db = get_database()
    attempts = db.get_outreach_attempts(lead_id)
    
    return attempts


def _get_revenue_breakdown(lead) -> Optional[List[Dict]]:
    """Compute revenue breakdown for display (live, not cached)."""
    try:
        from src.score.revenue import estimate_revenue
        result = estimate_revenue(lead)
        bd = result.get("revenue_breakdown", [])
        if bd:
            return [{
                **item,
                "fee_rate": result.get("fee_rate", 0.05),
                "avg_rent_per_unit": result.get("avg_rent_per_unit", 0),
                "borough_used": result.get("borough_used", ""),
                "total_units_used": result.get("total_units_used", 0),
            } for item in bd]
    except Exception:
        pass
    return None


def _lead_to_response(lead: Lead) -> LeadResponse:
    """Convert Lead dataclass to API response."""
    # Convert score breakdown if available
    breakdown = None
    if lead.score_breakdown:
        breakdown = ScoreBreakdown(
            portfolio=lead.score_breakdown.get("portfolio", 0.0),
            units=lead.score_breakdown.get("units", 0.0),
            professional=lead.score_breakdown.get("professional", 0.0),
            contact=lead.score_breakdown.get("contact", 0.0),
            concentration=lead.score_breakdown.get("concentration", 0.0),
        )
    
    # Convert phones with sources
    phones_with_source = []
    if hasattr(lead, 'phones') and lead.phones:
        for p in lead.phones:
            if hasattr(p, 'value'):
                phones_with_source.append(ContactWithSource(
                    value=p.value,
                    type=p.type,
                    source=p.source,
                    source_url=p.source_url,
                    confidence=p.confidence,
                    verified=p.verified,
                ))
    
    # Convert emails with sources
    emails_with_source = []
    if hasattr(lead, 'emails') and lead.emails:
        for e in lead.emails:
            if hasattr(e, 'value'):
                emails_with_source.append(ContactWithSource(
                    value=e.value,
                    type=e.type,
                    source=e.source,
                    source_url=e.source_url,
                    confidence=e.confidence,
                    verified=e.verified,
                ))
    
    # Build building types response
    building_types_response = None
    if lead.building_types:
        building_types_response = BuildingTypeBreakdownResponse(
            condo=lead.building_types.condo,
            coop=lead.building_types.coop,
            rental_elevator=lead.building_types.rental_elevator,
            rental_walkup=lead.building_types.rental_walkup,
            small_residential=lead.building_types.small_residential,
            other=lead.building_types.other,
            unknown=lead.building_types.unknown,
            total=lead.building_types.total,
            total_rental=lead.building_types.total_rental,
        )
    
    return LeadResponse(
        lead_id=lead.lead_id,
        agent_name=lead.agent_name,
        owner_name=lead.owner_name,
        owner_type=lead.owner_type,
        portfolio_size=lead.portfolio_size,
        total_units=lead.total_units,
        buildings=lead.buildings,  # Return all buildings
        phone=lead.phone,
        email=lead.email,
        phones=phones_with_source,
        emails=emails_with_source,
        website=lead.website,
        website_source=getattr(lead, 'website_source', None),
        business_summary=lead.business_summary[:200] if isinstance(lead.business_summary, str) else None,
        linkedin_url=getattr(lead, 'linkedin_url', None),
        linkedin_people=getattr(lead, 'linkedin_people', []) or [],
        address=lead.address,
        boro=lead.boro,
        boros=lead.boros,
        building_types=building_types_response,
        building_classes=getattr(lead, 'building_classes', {}) or {},
        score=lead.score,
        score_breakdown=breakdown,
        tags=lead.tags,
        enrichment_status=lead.enrichment_status,
        outreach_status=lead.outreach_status,
        notes=lead.notes,
        outreach_attempts=_get_outreach_attempts_for_lead(lead.lead_id),
        contacts=lead.contacts if lead.contacts else [],
        entity_type=getattr(lead, 'entity_type', 'unknown'),
        company_name=getattr(lead, 'company_name', None),
        primary_contact=getattr(lead, 'primary_contact', None),
        primary_contact_title=getattr(lead, 'primary_contact_title', None),
        # Revenue estimation (Phase 5.1) - compute live breakdown for display
        estimated_monthly_revenue=getattr(lead, 'estimated_monthly_revenue', 0.0) or 0.0,
        estimated_annual_revenue=getattr(lead, 'estimated_annual_revenue', 0.0) or 0.0,
        revenue_breakdown=_get_revenue_breakdown(lead),
        # Violations (Phase 5.2)
        violation_count=getattr(lead, 'violation_count', 0) or 0,
        violation_class_a=getattr(lead, 'violation_class_a', 0) or 0,
        violation_class_b=getattr(lead, 'violation_class_b', 0) or 0,
        violation_class_c=getattr(lead, 'violation_class_c', 0) or 0,
        violations_per_unit=getattr(lead, 'violations_per_unit', 0.0) or 0.0,
        # Pipeline (Phase 5.3)
        pipeline_stage=getattr(lead, 'pipeline_stage', 'research') or 'research',
        next_follow_up=getattr(lead, 'next_follow_up', None),
        priority_rank=getattr(lead, 'priority_rank', 0) or 0,
    )


def _get_outreach_attempts_for_lead(lead_id: str) -> List[OutreachAttemptResponse]:
    """Get outreach attempts for a lead from database."""
    db = get_database()
    attempts = db.get_outreach_attempts(lead_id)
    return [
        OutreachAttemptResponse(
            id=a.get('id', ''),
            method=a.get('method', ''),
            outcome=a.get('outcome', ''),
            notes=a.get('notes'),
            timestamp=a.get('timestamp', ''),
        )
        for a in attempts
    ]


@app.get("/api/leads/{lead_id}/due-diligence")
async def generate_due_diligence(lead_id: str):
    """
    Generate a structured due diligence snapshot for a lead (Phase 5.5).
    Returns both structured JSON and a markdown report.
    """
    db = get_database()
    row = db.get_lead_by_id(lead_id)
    if not row:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    lead = _row_to_lead(row)
    
    # Get outreach history
    outreach_events = db.get_outreach_events(lead_id)
    outreach_attempts = db.get_outreach_attempts(lead_id)
    
    # Find comparable leads (same borough, similar portfolio size)
    comparable_rows, _ = db.get_leads_filtered(
        boro=lead.boro if lead.boro else None,
        min_portfolio=max(1, lead.portfolio_size - 20),
        limit=6,
        offset=0,
    )
    comparables = []
    for r in comparable_rows:
        if r['lead_id'] != lead_id:
            comparables.append({
                "lead_id": r['lead_id'],
                "name": r.get('company_name') or r.get('agent_name') or r.get('owner_name'),
                "portfolio_size": r.get('portfolio_size', 0),
                "total_units": r.get('total_units', 0),
                "score": r.get('score', 0),
                "entity_type": r.get('entity_type', 'unknown'),
            })
    comparables = comparables[:5]
    
    # Build markdown report
    company_display = lead.company_name or lead.agent_name or lead.owner_name
    md_lines = [
        f"# Due Diligence: {company_display}",
        "",
        "## Company Overview",
        f"- **Entity Type:** {lead.entity_type}",
        f"- **Company Name:** {lead.company_name or 'N/A'}",
        f"- **Agent/Owner:** {lead.agent_name}",
        f"- **Owner:** {lead.owner_name}",
        f"- **Primary Contact:** {lead.primary_contact or 'N/A'} ({lead.primary_contact_title or 'N/A'})",
        f"- **Borough:** {lead.boro}",
        f"- **Score:** {lead.score}/100",
        f"- **Pipeline Stage:** {lead.pipeline_stage}",
        f"- **Priority:** {'*' * lead.priority_rank if lead.priority_rank else 'Unranked'}",
    ]
    
    if lead.dos_id:
        md_lines.extend([
            "",
            "### NY DOS Corporation Info",
            f"- **DOS ID:** {lead.dos_id}",
            f"- **Status:** {lead.dos_status or 'N/A'}",
        ])
    
    if lead.business_summary:
        md_lines.extend(["", f"### Business Summary", lead.business_summary])
    
    md_lines.extend([
        "",
        "## Portfolio",
        f"- **Buildings:** {lead.portfolio_size}",
        f"- **Total Units:** {lead.total_units}",
        f"- **Boroughs:** {', '.join(lead.boros) if lead.boros else lead.boro}",
    ])
    
    if lead.building_types:
        bt = lead.building_types
        md_lines.extend([
            "",
            "### Building Type Breakdown",
            f"- Condo: {bt.condo}" if bt.condo else "",
            f"- Coop: {bt.coop}" if bt.coop else "",
            f"- Rental Elevator: {bt.rental_elevator}" if bt.rental_elevator else "",
            f"- Rental Walkup: {bt.rental_walkup}" if bt.rental_walkup else "",
            f"- Small Residential: {bt.small_residential}" if bt.small_residential else "",
            f"- Other: {bt.other}" if bt.other else "",
        ])
    
    md_lines.extend([
        "",
        "## Financial Estimate",
        f"- **Estimated Monthly Revenue:** ${lead.estimated_monthly_revenue:,.0f}" if lead.estimated_monthly_revenue else "- **Estimated Monthly Revenue:** N/A",
        f"- **Estimated Annual Revenue:** ${lead.estimated_annual_revenue:,.0f}" if lead.estimated_annual_revenue else "- **Estimated Annual Revenue:** N/A",
        f"- *Based on 5% management fee rate, borough-adjusted rent estimates*",
    ])
    
    if lead.violation_count > 0:
        md_lines.extend([
            "",
            "## Operational Risk: Violations",
            f"- **Total Violations:** {lead.violation_count}",
            f"- **Class A (non-hazardous):** {lead.violation_class_a}",
            f"- **Class B (hazardous):** {lead.violation_class_b}",
            f"- **Class C (immediately hazardous):** {lead.violation_class_c}",
            f"- **Violations per Unit:** {lead.violations_per_unit}",
        ])
    
    md_lines.extend([
        "",
        "## Contact Information",
        f"- **Phone:** {lead.phone or 'N/A'}",
        f"- **Email:** {lead.email or 'N/A'}",
        f"- **Website:** {lead.website or 'N/A'}",
        f"- **Address:** {lead.address or 'N/A'}",
    ])
    
    if lead.contacts:
        md_lines.extend(["", "### HPD Registered Contacts"])
        for c in lead.contacts[:10]:
            name = c.get('name', 'Unknown')
            ctype = c.get('type', '')
            title = c.get('title', '')
            md_lines.append(f"- {name} ({ctype}{', ' + title if title else ''})")
    
    if outreach_events or outreach_attempts:
        md_lines.extend(["", "## Outreach History"])
        for e in outreach_events[:10]:
            md_lines.append(
                f"- [{e.get('timestamp', 'N/A')[:10]}] {e.get('stage', '')} - "
                f"{e.get('method', '')} - {e.get('outcome', '')} "
                f"{'/ ' + e.get('notes', '') if e.get('notes') else ''}"
            )
        for a in outreach_attempts[:10]:
            md_lines.append(
                f"- [{a.get('timestamp', 'N/A')[:10]}] {a.get('method', '')} - {a.get('outcome', '')}"
            )
    
    if comparables:
        md_lines.extend(["", "## Comparable Companies"])
        for comp in comparables:
            md_lines.append(
                f"- {comp['name']} - {comp['portfolio_size']} buildings, "
                f"{comp['total_units']} units, score: {comp['score']}"
            )
    
    md_lines.extend([
        "",
        "### Building Addresses",
    ])
    for addr in lead.buildings[:50]:
        md_lines.append(f"- {addr}")
    if len(lead.buildings) > 50:
        md_lines.append(f"- ... and {len(lead.buildings) - 50} more")
    
    # Filter empty lines from conditional sections
    md_lines = [l for l in md_lines if l is not None and l != ""]
    markdown_report = "\n".join(md_lines)
    
    return {
        "lead_id": lead_id,
        "company_name": company_display,
        "report_markdown": markdown_report,
        "data": {
            "entity_type": lead.entity_type,
            "portfolio_size": lead.portfolio_size,
            "total_units": lead.total_units,
            "score": lead.score,
            "estimated_annual_revenue": lead.estimated_annual_revenue,
            "violation_count": lead.violation_count,
            "violations_per_unit": lead.violations_per_unit,
            "phone": lead.phone,
            "email": lead.email,
            "website": lead.website,
            "pipeline_stage": lead.pipeline_stage,
            "priority_rank": lead.priority_rank,
            "contacts_count": len(lead.contacts),
            "outreach_events_count": len(outreach_events),
            "comparables_count": len(comparables),
        },
        "comparables": comparables,
    }


@app.post("/api/refresh/check-updates")
async def check_for_updates():
    """
    Compare current HPD data against DB and detect changes (Phase 5.4).
    Detects: new high-value companies, portfolio changes, contact changes.
    """
    db = get_database()
    
    # Get current high-value leads from DB
    rows, total = db.get_leads_filtered(min_portfolio=10, limit=5000, offset=0)
    current_leads = {}
    for row in rows:
        current_leads[row['lead_id']] = {
            'portfolio_size': row.get('portfolio_size', 0),
            'agent_name': row.get('agent_name', ''),
            'phone': row.get('phone'),
            'email': row.get('email'),
        }
    
    # Fetch fresh data from HPD (limited check)
    from src.ingest.hpd_client import HPDClient
    client = HPDClient()
    
    alerts_created = 0
    
    try:
        # Check for new registrations (just count to see if total changed)
        import requests as _req
        resp = _req.get(
            "https://data.cityofnewyork.us/resource/tesw-yqqr.json",
            params={"$select": "COUNT(*) as cnt"},
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            new_count = int(data[0].get('cnt', 0)) if data else 0
            
            # If significantly more buildings, flag it
            if new_count > total * 1.01:  # More than 1% growth
                db.add_change_alert(
                    "data_growth",
                    f"HPD building count increased: {new_count} total (our DB has {total} high-value leads)",
                    details={"hpd_count": new_count, "our_count": total},
                )
                alerts_created += 1
    except Exception as e:
        logger.warning(f"Failed to check HPD building count: {e}")
    
    # Check for leads with stale data (not refreshed in 7+ days)
    from datetime import timedelta
    stale_cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    
    with db._get_connection() as conn:
        stale_count = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE updated_at < ? AND portfolio_size >= 10",
            (stale_cutoff,)
        ).fetchone()[0]
    
    if stale_count > 100:
        db.add_change_alert(
            "stale_data",
            f"{stale_count} high-value leads haven't been refreshed in 7+ days",
            details={"stale_count": stale_count},
        )
        alerts_created += 1
    
    return {
        "status": "success",
        "alerts_created": alerts_created,
        "high_value_leads": len(current_leads),
    }


@app.get("/api/alerts")
async def get_alerts(limit: int = Query(50, le=200)):
    """Get recent change alerts (Phase 5.4)."""
    db = get_database()
    alerts = db.get_change_alerts(limit=limit)
    return {"count": len(alerts), "alerts": alerts}


@app.post("/api/alerts/{alert_id}/dismiss")
async def dismiss_alert(alert_id: int):
    """Dismiss a change alert (Phase 5.4)."""
    db = get_database()
    db.dismiss_alert(alert_id)
    return {"status": "success", "alert_id": alert_id}


@app.post("/api/violations/refresh")
async def refresh_violations():
    """
    Fetch HPD violations data and aggregate per lead (Phase 5.2).
    Processes high-value leads (portfolio >= 10) in batches.
    """
    from src.ingest.hpd_violations import HPDViolationsClient, aggregate_violations_for_lead
    
    db = get_database()
    
    # Get high-value leads with their building IDs
    rows, total = db.get_leads_filtered(min_portfolio=10, limit=5000, offset=0)
    
    if not rows:
        return {"status": "no_leads", "message": "No high-value leads found"}
    
    client = HPDViolationsClient()
    
    # Collect all building IDs
    import json as _json
    all_building_ids = []
    lead_buildings = {}  # lead_id -> [building_ids]
    lead_units = {}  # lead_id -> total_units
    
    for row in rows:
        lead_id = row['lead_id']
        bids = _json.loads(row.get('building_ids') or '[]')
        lead_buildings[lead_id] = bids
        lead_units[lead_id] = row.get('total_units', 0) or 0
        all_building_ids.extend(bids)
    
    # Fetch violations in bulk
    logger.info(f"Fetching violations for {len(all_building_ids)} buildings across {len(rows)} leads")
    violations_data = client.fetch_violations_for_buildings(all_building_ids, batch_size=100)
    
    # Aggregate per lead and save
    updated = 0
    for lead_id, bids in lead_buildings.items():
        agg = aggregate_violations_for_lead(bids, violations_data, lead_units.get(lead_id, 0))
        
        if agg["violation_count"] > 0:
            db.update_lead(lead_id, agg)
            updated += 1
    
    # Update cache too
    with _leads_lock:
        for lead in _leads_cache:
            if lead.lead_id in lead_buildings:
                agg = aggregate_violations_for_lead(
                    lead_buildings[lead.lead_id], 
                    violations_data, 
                    lead_units.get(lead.lead_id, 0)
                )
                lead.violation_count = agg["violation_count"]
                lead.violation_class_a = agg["violation_class_a"]
                lead.violation_class_b = agg["violation_class_b"]
                lead.violation_class_c = agg["violation_class_c"]
                lead.violations_per_unit = agg["violations_per_unit"]
    
    db.set_setting('violations_computed_at', datetime.now().isoformat())
    
    return {
        "status": "success",
        "leads_checked": len(rows),
        "leads_with_violations": updated,
        "buildings_checked": len(all_building_ids),
        "buildings_with_violations": len(violations_data),
    }


@app.post("/api/rescore")
async def rescore_all_leads():
    """Re-score all leads using Scoring V2 (Phase 5.6)."""
    from src.score.scorer import Scorer
    
    db = get_database()
    leads = db.load_all_leads()
    scorer = Scorer()
    
    updated = 0
    for lead in leads:
        old_score = lead.score
        scorer.score_lead(lead)
        
        if lead.score != old_score:
            import json as _j
            db.update_lead(lead.lead_id, {
                "score": lead.score,
                "score_breakdown": _j.dumps(lead.score_breakdown),
            })
            updated += 1
    
    # Update cache
    with _leads_lock:
        for lead in _leads_cache:
            scorer.score_lead(lead)
    
    return {
        "status": "success",
        "leads_rescored": len(leads),
        "leads_changed": updated,
    }


@app.post("/api/estimate-revenue")
async def estimate_all_revenue():
    """Run revenue estimation on all leads in the database (Phase 5.1)."""
    from src.score.revenue import estimate_revenue
    
    db = get_database()
    leads = db.load_all_leads()
    updated = 0
    
    for lead in leads:
        rev = estimate_revenue(lead)
        monthly = rev["estimated_monthly_revenue"]
        annual = rev["estimated_annual_revenue"]
        
        if monthly > 0:
            db.update_lead(lead.lead_id, {
                "estimated_monthly_revenue": monthly,
                "estimated_annual_revenue": annual,
            })
            updated += 1
    
    # Also update cache
    with _leads_lock:
        for lead in _leads_cache:
            rev = estimate_revenue(lead)
            lead.estimated_monthly_revenue = rev["estimated_monthly_revenue"]
            lead.estimated_annual_revenue = rev["estimated_annual_revenue"]
    
    return {
        "status": "success",
        "leads_updated": updated,
        "total_leads": len(leads),
    }


@app.get("/api/export/csv")
async def export_leads_csv(
    min_score: Optional[float] = None,
    min_portfolio: Optional[int] = None,
    boro: Optional[str] = None,
    limit: int = Query(500, le=5000, description="Max leads to export"),
):
    """
    Export leads to CSV format.
    
    Returns a CSV file download.
    """
    from fastapi.responses import StreamingResponse
    import csv
    import io
    
    # Use DB query for thread safety (Phase 4.1)
    db = get_database()
    rows, total = db.get_leads_filtered(
        min_score=min_score,
        min_portfolio=min_portfolio,
        boro=boro,
        limit=limit,
        offset=0,
    )
    filtered = [_row_to_lead(r) for r in rows]
    
    # Generate CSV
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Headers
    writer.writerow([
        "Score", "Tier", "Agent Name", "Owner Name", "Portfolio Size",
        "Phone", "Email", "Website", "LinkedIn Company", "LinkedIn People",
        "Business Summary", "Address", "Borough", "Tags", 
        "Enrichment Status", "Lead ID"
    ])
    
    # Rows
    for lead in filtered:
        tier = "A" if lead.score >= 80 else "B" if lead.score >= 60 else "C" if lead.score >= 40 else "D"
        linkedin_people = "; ".join(getattr(lead, 'linkedin_people', []) or [])
        
        writer.writerow([
            round(lead.score, 1),
            tier,
            lead.agent_name or "",
            lead.owner_name or "",
            lead.portfolio_size,
            lead.phone or "",
            lead.email or "",
            lead.website or "",
            getattr(lead, 'linkedin_url', "") or "",
            linkedin_people,
            (lead.business_summary or "")[:200],
            lead.address or "",
            lead.boro,
            ", ".join(lead.tags) if lead.tags else "",
            lead.enrichment_status,
            lead.lead_id,
        ])
    
    output.seek(0)
    
    # Return as downloadable CSV
    filename = f"hpd_leads_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
