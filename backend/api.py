"""
FastAPI server for HPD Leads Pipeline.

Exposes REST endpoints for the frontend to:
- Fetch leads
- Trigger enrichment
- Get pipeline status
"""
import logging
import threading
from datetime import datetime
from typing import List, Optional, Dict

from fastapi import FastAPI, Query, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.ingest.hpd_client import HPDClient
from src.transform.normalize import normalize_building
from src.transform.aggregate import aggregate_to_leads, Lead
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict to your frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state - loaded from SQLite on startup
_leads_cache: List[Lead] = []
_last_refresh: Optional[datetime] = None
_leads_lock = threading.Lock()  # Protects _leads_cache modifications

# Background enrichment state
_enrichment_state: Dict = {
    "running": False,
    "progress": 0,
    "total": 0,
    "completed": 0,
    "failed": 0,
    "current_lead": None,
    "started_at": None,
    "finished_at": None,
}
_enrichment_lock = threading.Lock()  # Protects _enrichment_state

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
    """Load leads from SQLite database on startup."""
    global _leads_cache, _last_refresh
    
    db = get_database()
    loaded = db.load_all_leads()
    
    if loaded:
        _leads_cache = loaded
        _last_refresh = db.get_last_refresh_time()
        logger.info(f"Startup: Loaded {len(loaded)} leads from database")
    else:
        logger.info("Startup: No leads in database, run /api/refresh to populate")


class OutreachAttemptResponse(BaseModel):
    """Outreach attempt data for API response."""
    id: str
    method: str
    outcome: str
    notes: Optional[str]
    timestamp: str


class LeadResponse(BaseModel):
    """Lead data for API response."""
    lead_id: str
    agent_name: str
    owner_name: str
    owner_type: str
    portfolio_size: int
    total_units: int
    buildings: List[str]
    phone: Optional[str]
    email: Optional[str]
    website: Optional[str]
    business_summary: Optional[str]
    address: Optional[str]
    boro: str
    boros: List[str]
    score: float
    tags: List[str]
    enrichment_status: str
    outreach_status: str
    notes: Optional[str]
    outreach_attempts: List[OutreachAttemptResponse] = []


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
    """Request to update lead status/notes."""
    outreach_status: Optional[str] = None  # new, contacted, interested, not_interested, closed
    notes: Optional[str] = None


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "service": "hpd-leads-api"}


@app.get("/api/leads", response_model=List[LeadResponse])
async def get_leads(
    min_score: Optional[float] = Query(None, description="Minimum score filter"),
    min_portfolio: Optional[int] = Query(None, description="Minimum portfolio size"),
    boro: Optional[str] = Query(None, description="Filter by borough"),
    has_website: Optional[bool] = Query(None, description="Filter by website availability"),
    has_email: Optional[bool] = Query(None, description="Filter by email availability"),
    limit: int = Query(100, le=500, description="Max results to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
):
    """
    Get leads with optional filtering.
    
    Returns leads sorted by score descending.
    """
    if not _leads_cache:
        return []  # Return empty list instead of 404 for better frontend handling
    
    # Apply filters
    filtered = _leads_cache
    
    if min_score is not None:
        filtered = [l for l in filtered if l.score >= min_score]
    
    if min_portfolio is not None:
        filtered = [l for l in filtered if l.portfolio_size >= min_portfolio]
    
    if boro is not None:
        filtered = [l for l in filtered if boro.upper() in [b.upper() for b in l.boros]]
    
    if has_website is not None:
        if has_website:
            filtered = [l for l in filtered if l.website]
        else:
            filtered = [l for l in filtered if not l.website]
    
    if has_email is not None:
        if has_email:
            filtered = [l for l in filtered if l.email]
        else:
            filtered = [l for l in filtered if not l.email]
    
    # Paginate
    paginated = filtered[offset:offset + limit]
    
    return [_lead_to_response(l) for l in paginated]


@app.get("/api/leads/{lead_id}", response_model=LeadResponse)
async def get_lead(lead_id: str):
    """Get a single lead by ID."""
    for lead in _leads_cache:
        if lead.lead_id == lead_id:
            return _lead_to_response(lead)
    raise HTTPException(status_code=404, detail="Lead not found")


@app.patch("/api/leads/{lead_id}")
async def update_lead(lead_id: str, request: UpdateLeadRequest):
    """
    Update a lead's outreach status and/or notes.
    
    Valid statuses: new, contacted, interested, not_interested, closed
    """
    global _leads_cache
    
    valid_statuses = {"new", "contacted", "interested", "not_interested", "closed"}
    
    if request.outreach_status and request.outreach_status not in valid_statuses:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
        )
    
    for i, lead in enumerate(_leads_cache):
        if lead.lead_id == lead_id:
            if request.outreach_status is not None:
                lead.outreach_status = request.outreach_status
            if request.notes is not None:
                lead.notes = request.notes
            lead.updated_at = datetime.now()
            _leads_cache[i] = lead
            
            # Persist to database (both user_data table for backward compat and leads table)
            db = get_database()
            db.save_lead_user_data(
                lead_id=lead_id,
                outreach_status=request.outreach_status,
                notes=request.notes
            )
            # Also update the main leads table
            db.save_leads([lead])
            
            return {
                "status": "success",
                "lead_id": lead_id,
                "outreach_status": lead.outreach_status,
                "notes": lead.notes,
            }
    
    raise HTTPException(status_code=404, detail="Lead not found")


@app.get("/api/status", response_model=PipelineStatus)
async def get_status():
    """Get pipeline status."""
    enriched = len([l for l in _leads_cache if l.enrichment_status in ["complete", "partial"]])
    top = max((l.score for l in _leads_cache), default=0)
    
    return PipelineStatus(
        total_leads=len(_leads_cache),
        last_refresh=_last_refresh.isoformat() if _last_refresh else None,
        enriched_count=enriched,
        top_score=top,
    )


@app.get("/api/stats")
async def get_stats():
    """Get detailed statistics about the loaded leads."""
    if not _leads_cache:
        return {
            "total_leads": 0,
            "total_buildings": 0,
            "by_borough": {},
            "by_enrichment_status": {},
            "score_distribution": {},
            "portfolio_distribution": {},
            "with_phone": 0,
            "with_email": 0,
            "with_website": 0,
        }
    
    # Count by borough
    by_borough = {}
    for lead in _leads_cache:
        boro = lead.boro or "Unknown"
        by_borough[boro] = by_borough.get(boro, 0) + 1
    
    # Count by enrichment status
    by_status = {}
    for lead in _leads_cache:
        status = lead.enrichment_status
        by_status[status] = by_status.get(status, 0) + 1
    
    # Score distribution
    score_dist = {"0-20": 0, "20-40": 0, "40-60": 0, "60-80": 0, "80-100": 0}
    for lead in _leads_cache:
        if lead.score < 20:
            score_dist["0-20"] += 1
        elif lead.score < 40:
            score_dist["20-40"] += 1
        elif lead.score < 60:
            score_dist["40-60"] += 1
        elif lead.score < 80:
            score_dist["60-80"] += 1
        else:
            score_dist["80-100"] += 1
    
    # Portfolio size distribution
    portfolio_dist = {"1-5": 0, "6-10": 0, "11-25": 0, "26-50": 0, "51-100": 0, "100+": 0}
    for lead in _leads_cache:
        if lead.portfolio_size <= 5:
            portfolio_dist["1-5"] += 1
        elif lead.portfolio_size <= 10:
            portfolio_dist["6-10"] += 1
        elif lead.portfolio_size <= 25:
            portfolio_dist["11-25"] += 1
        elif lead.portfolio_size <= 50:
            portfolio_dist["26-50"] += 1
        elif lead.portfolio_size <= 100:
            portfolio_dist["51-100"] += 1
        else:
            portfolio_dist["100+"] += 1
    
    return {
        "total_leads": len(_leads_cache),
        "total_buildings": sum(l.portfolio_size for l in _leads_cache),
        "by_borough": by_borough,
        "by_enrichment_status": by_status,
        "score_distribution": score_dist,
        "portfolio_distribution": portfolio_dist,
        "with_phone": len([l for l in _leads_cache if l.phone]),
        "with_email": len([l for l in _leads_cache if l.email]),
        "with_website": len([l for l in _leads_cache if l.website]),
    }


def _run_background_refresh(limit: Optional[int]):
    """Background task to refresh pipeline data with chunked processing for memory efficiency."""
    global _leads_cache, _last_refresh, _refresh_state
    
    # Process in chunks to avoid memory issues
    # For full refresh (200k+), process in 50k chunks
    CHUNK_SIZE = 50000
    
    with _refresh_lock:
        _refresh_state["running"] = True
        _refresh_state["phase"] = "fetching"
        _refresh_state["buildings_fetched"] = 0
        _refresh_state["total_buildings"] = limit or 0  # 0 means all
        _refresh_state["leads_created"] = 0
        _refresh_state["started_at"] = datetime.now().isoformat()
        _refresh_state["finished_at"] = None
        _refresh_state["error"] = None
    
    try:
        logger.info(f"Background refresh started with limit={limit or 'ALL'}")
        
        client = HPDClient()
        db = get_database()
        
        # For smaller datasets, process normally
        if limit and limit <= CHUNK_SIZE:
            with _refresh_lock:
                _refresh_state["phase"] = "fetching"
            
            raw_buildings = client.get_combined_data(building_limit=limit)
            
            with _refresh_lock:
                _refresh_state["buildings_fetched"] = len(raw_buildings)
                _refresh_state["phase"] = "normalizing"
            
            logger.info(f"Fetched {len(raw_buildings)} buildings from HPD")
            
            buildings = [normalize_building(b) for b in raw_buildings]
            
            with _refresh_lock:
                _refresh_state["phase"] = "aggregating"
            
            leads = aggregate_to_leads(buildings)
            logger.info(f"Aggregated into {len(leads)} leads")
            
            with _refresh_lock:
                _refresh_state["phase"] = "scoring"
            
            leads = score_leads(leads)
            
            with _refresh_lock:
                _refresh_state["phase"] = "persisting"
            
            leads = db.apply_persisted_data_to_leads(leads)
            db.save_leads(leads)
            logger.info(f"Persisted {len(leads)} leads to database")
            
            with _leads_lock:
                _leads_cache = leads
            
            _last_refresh = datetime.now()
            
            with _refresh_lock:
                _refresh_state["leads_created"] = len(leads)
            
        else:
            # Large dataset: process in chunks and merge leads
            # This is memory-efficient as we process and discard each chunk
            from collections import defaultdict
            
            all_lead_data = defaultdict(lambda: {
                "buildings": [],
                "building_ids": [],
                "contacts": [],
                "boros": [],
                "agent_names": [],
                "owner_names": [],
                "most_recent_building": None,
            })
            
            offset = 0
            total_fetched = 0
            target = limit or 999999  # Large number for "all"
            
            while total_fetched < target:
                chunk_limit = min(CHUNK_SIZE, target - total_fetched)
                
                with _refresh_lock:
                    _refresh_state["phase"] = f"fetching chunk {offset // CHUNK_SIZE + 1}"
                
                logger.info(f"Fetching chunk: offset={offset}, limit={chunk_limit}")
                
                # Fetch buildings for this chunk
                raw_buildings = client.fetch_all_buildings(limit=chunk_limit)
                if offset > 0:
                    # Need to skip already fetched buildings
                    # Since we can't pass offset to the API easily, we fetch sequentially
                    # This is a simplification - for real chunked processing we'd need 
                    # to modify the HPD client to support true pagination
                    break  # For now, just do one chunk at full limit
                
                if not raw_buildings:
                    break
                
                # Fetch contacts for these buildings
                reg_ids = list(set(b.get("registrationid") for b in raw_buildings if b.get("registrationid")))
                contacts = client.fetch_contacts_for_registrations(reg_ids)
                
                # Group contacts by registration
                contacts_by_reg = {}
                for contact in contacts:
                    reg_id = contact.get("registrationid")
                    if reg_id:
                        if reg_id not in contacts_by_reg:
                            contacts_by_reg[reg_id] = []
                        contacts_by_reg[reg_id].append(contact)
                
                # Attach contacts to buildings
                for building in raw_buildings:
                    reg_id = building.get("registrationid")
                    building["contacts"] = contacts_by_reg.get(reg_id, [])
                
                total_fetched += len(raw_buildings)
                
                with _refresh_lock:
                    _refresh_state["buildings_fetched"] = total_fetched
                    _refresh_state["phase"] = f"processing chunk {offset // CHUNK_SIZE + 1}"
                
                logger.info(f"Processing {len(raw_buildings)} buildings from chunk")
                
                # Normalize this chunk
                buildings = [normalize_building(b) for b in raw_buildings]
                
                # Free memory
                del raw_buildings
                
                # Aggregate this chunk
                chunk_leads = aggregate_to_leads(buildings)
                
                # Free memory
                del buildings
                
                # Merge into all_lead_data (by lead_id for deduplication)
                for lead in chunk_leads:
                    key = lead.lead_id
                    data = all_lead_data[key]
                    data["buildings"].extend(lead.buildings)
                    data["building_ids"].extend(lead.building_ids)
                    data["contacts"].extend(lead.contacts)
                    data["boros"].extend(lead.boros)
                    data["agent_names"].append(lead.agent_name)
                    data["owner_names"].append(lead.owner_name)
                    if data["most_recent_building"] is None:
                        data["most_recent_building"] = lead
                    elif lead.last_registration and (
                        data["most_recent_building"].last_registration is None or 
                        lead.last_registration > data["most_recent_building"].last_registration
                    ):
                        data["most_recent_building"] = lead
                
                del chunk_leads
                
                offset += CHUNK_SIZE
                
                # For full refresh, we actually need the API to support proper pagination
                # which it doesn't easily. So for now, fall back to single large chunk
                break
            
            with _refresh_lock:
                _refresh_state["phase"] = "merging"
            
            logger.info(f"Merging {len(all_lead_data)} unique leads")
            
            # Convert merged data back to Lead objects
            from collections import Counter
            
            leads = []
            for lead_id, data in all_lead_data.items():
                base = data["most_recent_building"]
                if base:
                    # Update with merged data
                    base.buildings = list(set(data["buildings"]))
                    base.building_ids = list(set(data["building_ids"]))
                    base.portfolio_size = len(base.buildings)
                    base.boros = list(set(data["boros"]))
                    base.boro = Counter(data["boros"]).most_common(1)[0][0] if data["boros"] else ""
                    # Use most common names
                    if data["agent_names"]:
                        base.agent_name = Counter(data["agent_names"]).most_common(1)[0][0]
                    if data["owner_names"]:
                        base.owner_name = Counter(data["owner_names"]).most_common(1)[0][0]
                    leads.append(base)
            
            del all_lead_data
            
            with _refresh_lock:
                _refresh_state["phase"] = "scoring"
            
            leads = score_leads(leads)
            
            with _refresh_lock:
                _refresh_state["phase"] = "persisting"
            
            leads = db.apply_persisted_data_to_leads(leads)
            db.save_leads(leads)
            logger.info(f"Persisted {len(leads)} leads to database")
            
            with _leads_lock:
                _leads_cache = leads
            
            _last_refresh = datetime.now()
            
            with _refresh_lock:
                _refresh_state["leads_created"] = len(leads)
        
        logger.info(f"Background refresh complete: {_refresh_state['buildings_fetched']} buildings -> {_refresh_state['leads_created']} leads")
        
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
):
    """
    Refresh the pipeline: fetch from HPD, normalize, aggregate, score.
    
    By default fetches 10,000 buildings for speed. Set full=true to fetch ALL ~200k.
    For full refresh, background=true is REQUIRED (default) due to worker timeouts.
    
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
    
    logger.info(f"Starting pipeline refresh with limit={limit or 'ALL'}, background={background}")
    
    if background:
        # Run in background
        background_tasks.add_task(_run_background_refresh, limit)
        return {
            "status": "started",
            "message": f"Background refresh started for {limit or 'ALL'} buildings",
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
    
    # Update cache and persist enrichment to database
    db = get_database()
    updated_leads = []
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
    """Background task to enrich leads."""
    global _leads_cache, _enrichment_state
    
    with _enrichment_lock:
        _enrichment_state["running"] = True
        _enrichment_state["progress"] = 0
        _enrichment_state["total"] = limit
        _enrichment_state["completed"] = 0
        _enrichment_state["failed"] = 0
        _enrichment_state["started_at"] = datetime.now().isoformat()
        _enrichment_state["finished_at"] = None
    
    try:
        enricher = Enricher(use_cache=True)
        
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
        
        with _enrichment_lock:
            _enrichment_state["total"] = len(batch)
        
        logger.info(f"Background enrichment started: {len(batch)} leads")
        
        db = get_database()
        lead_index = {l.lead_id: i for i, l in enumerate(_leads_cache)}
        
        for i, lead in enumerate(batch):
            with _enrichment_lock:
                _enrichment_state["progress"] = i + 1
                _enrichment_state["current_lead"] = lead.agent_name or lead.owner_name
            
            try:
                logger.info(f"[{i+1}/{len(batch)}] Enriching: {lead.agent_name or lead.owner_name}")
                enriched_lead = enricher.enrich_lead(lead)
                
                # Update cache with lock to prevent race conditions
                with _leads_lock:
                    if lead.lead_id in lead_index:
                        _leads_cache[lead_index[lead.lead_id]] = enriched_lead
                
                # Persist to database
                db.save_leads([enriched_lead])
                
                if enriched_lead.enrichment_status in ["complete", "partial"]:
                    with _enrichment_lock:
                        _enrichment_state["completed"] += 1
                else:
                    with _enrichment_lock:
                        _enrichment_state["failed"] += 1
                        
            except Exception as e:
                logger.warning(f"Failed to enrich {lead.agent_name}: {e}")
                with _enrichment_lock:
                    _enrichment_state["failed"] += 1
        
        logger.info(f"Background enrichment complete: {_enrichment_state['completed']} successful, {_enrichment_state['failed']} failed")
        
    finally:
        with _enrichment_lock:
            _enrichment_state["running"] = False
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


@app.get("/api/enrich/status")
async def get_enrichment_status():
    """
    Get the status of background enrichment.
    
    Returns progress, current lead being processed, and completion stats.
    """
    with _enrichment_lock:
        return {
            "running": _enrichment_state["running"],
            "progress": _enrichment_state["progress"],
            "total": _enrichment_state["total"],
            "completed": _enrichment_state["completed"],
            "failed": _enrichment_state["failed"],
            "current_lead": _enrichment_state["current_lead"],
            "started_at": _enrichment_state["started_at"],
            "finished_at": _enrichment_state["finished_at"],
            "percent_complete": round(_enrichment_state["progress"] / _enrichment_state["total"] * 100, 1) if _enrichment_state["total"] > 0 else 0,
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


class OutreachAttemptRequest(BaseModel):
    """Request to log an outreach attempt."""
    method: str  # phone, email, linkedin, in_person, other
    outcome: str  # no_answer, left_voicemail, spoke_with_contact, sent_email, meeting_scheduled, not_interested, other
    notes: Optional[str] = None


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
    
    return research_result


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


def _lead_to_response(lead: Lead) -> LeadResponse:
    """Convert Lead dataclass to API response."""
    return LeadResponse(
        lead_id=lead.lead_id,
        agent_name=lead.agent_name,
        owner_name=lead.owner_name,
        owner_type=lead.owner_type,
        portfolio_size=lead.portfolio_size,
        total_units=lead.total_units,
        buildings=lead.buildings[:10],  # Limit for response size
        phone=lead.phone,
        email=lead.email,
        website=lead.website,
        business_summary=lead.business_summary[:200] if isinstance(lead.business_summary, str) else None,
        address=lead.address,
        boro=lead.boro,
        boros=lead.boros,
        score=lead.score,
        tags=lead.tags,
        enrichment_status=lead.enrichment_status,
        outreach_status=lead.outreach_status,
        notes=lead.notes,
        outreach_attempts=_get_outreach_attempts_for_lead(lead.lead_id),
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
