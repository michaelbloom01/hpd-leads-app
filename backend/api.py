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
import os
_cors_origins = os.environ.get("CORS_ORIGINS", "*")
_allowed_origins = _cors_origins.split(",") if _cors_origins != "*" else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state - loaded from SQLite on startup
_leads_cache: List[Lead] = []
_last_refresh: Optional[datetime] = None
_leads_lock = threading.Lock()  # Protects _leads_cache modifications

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
    # Building type filters
    building_type: Optional[str] = Query(None, description="Filter by primary building type (condo, coop, rental_elevator, rental_walkup, small_residential)"),
    min_condo: Optional[int] = Query(None, description="Minimum number of condo buildings"),
    min_coop: Optional[int] = Query(None, description="Minimum number of coop buildings"),
    min_rental: Optional[int] = Query(None, description="Minimum number of rental buildings (elevator + walkup)"),
    min_units: Optional[int] = Query(None, description="Minimum total residential units"),
    limit: int = Query(100, le=500, description="Max results to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
):
    """
    Get leads with optional filtering.
    
    Returns leads sorted by score descending.
    
    Building Type Filters:
    - building_type: Filter by the primary (most common) building type
    - min_condo, min_coop, min_rental: Minimum counts for specific types
    - min_units: Minimum total residential units in portfolio
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
    
    # Building type filters
    if building_type is not None:
        building_type_lower = building_type.lower()
        def get_primary_type(lead):
            if not lead.building_types:
                return None
            types = {
                'condo': lead.building_types.condo,
                'coop': lead.building_types.coop,
                'rental_elevator': lead.building_types.rental_elevator,
                'rental_walkup': lead.building_types.rental_walkup,
                'small_residential': lead.building_types.small_residential,
            }
            if max(types.values()) == 0:
                return None
            return max(types, key=types.get)
        filtered = [l for l in filtered if get_primary_type(l) == building_type_lower]
    
    if min_condo is not None:
        filtered = [l for l in filtered if l.building_types and l.building_types.condo >= min_condo]
    
    if min_coop is not None:
        filtered = [l for l in filtered if l.building_types and l.building_types.coop >= min_coop]
    
    if min_rental is not None:
        filtered = [l for l in filtered if l.building_types and l.building_types.total_rental >= min_rental]
    
    if min_units is not None:
        filtered = [l for l in filtered if l.total_units >= min_units]
    
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
    
    # Use lock to prevent race conditions
    with _leads_lock:
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
            "building_type_distribution": {},
            "leads_with_pluto_data": 0,
        }
    
    # Single-pass aggregation for performance (O(n) instead of O(5n))
    by_borough = {}
    by_status = {}
    score_dist = {"0-20": 0, "20-40": 0, "40-60": 0, "60-80": 0, "80-100": 0}
    portfolio_dist = {"1-5": 0, "6-10": 0, "11-25": 0, "26-50": 0, "51-100": 0, "100+": 0}
    building_type_dist = {
        "condo": 0, "coop": 0, "rental_elevator": 0, "rental_walkup": 0,
        "small_residential": 0, "other": 0, "unknown": 0,
    }
    
    total_buildings = 0
    with_phone = 0
    with_email = 0
    with_website = 0
    leads_with_pluto = 0
    
    for lead in _leads_cache:
        # Borough
        boro = lead.boro or "Unknown"
        by_borough[boro] = by_borough.get(boro, 0) + 1
        
        # Enrichment status
        status = lead.enrichment_status
        by_status[status] = by_status.get(status, 0) + 1
        
        # Score distribution
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
        
        # Portfolio distribution
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
        
        # Totals
        total_buildings += lead.portfolio_size
        if lead.phone:
            with_phone += 1
        if lead.email:
            with_email += 1
        if lead.website:
            with_website += 1
        
        # Building types (PLUTO)
        if lead.building_types and lead.building_types.total > 0:
            leads_with_pluto += 1
            building_type_dist["condo"] += lead.building_types.condo
            building_type_dist["coop"] += lead.building_types.coop
            building_type_dist["rental_elevator"] += lead.building_types.rental_elevator
            building_type_dist["rental_walkup"] += lead.building_types.rental_walkup
            building_type_dist["small_residential"] += lead.building_types.small_residential
            building_type_dist["other"] += lead.building_types.other
            building_type_dist["unknown"] += lead.building_types.unknown
    
    return {
        "total_leads": len(_leads_cache),
        "total_buildings": total_buildings,
        "by_borough": by_borough,
        "by_enrichment_status": by_status,
        "score_distribution": score_dist,
        "portfolio_distribution": portfolio_dist,
        "with_phone": with_phone,
        "with_email": with_email,
        "with_website": with_website,
        "building_type_distribution": building_type_dist,
        "leads_with_pluto_data": leads_with_pluto,
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
    
    # Chunk size - tuned for Railway's memory limits
    # 25k buildings per chunk keeps memory usage reasonable
    CHUNK_SIZE = 25000
    
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
            
            # Free normalized buildings
            del normalized
            
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
    """Background task to enrich leads (legacy sequential method)."""
    global _leads_cache, _enrichment_state, _enrichment_stop_requested
    
    _enrichment_stop_requested = False  # Reset stop flag
    
    with _enrichment_lock:
        _enrichment_state["running"] = True
        _enrichment_state["phase"] = "sequential"
        _enrichment_state["progress"] = 0
        _enrichment_state["total"] = limit
        _enrichment_state["completed"] = 0
        _enrichment_state["failed"] = 0
        _enrichment_state["started_at"] = datetime.now().isoformat()
        _enrichment_state["finished_at"] = None
        _enrichment_state["error"] = None
    
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
            # Check for stop request
            if _enrichment_stop_requested:
                logger.info("Enrichment stopped by user request")
                with _enrichment_lock:
                    _enrichment_state["error"] = "Stopped by user"
                break
            
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
        
    except Exception as e:
        logger.error(f"Background enrichment failed: {e}")
        with _enrichment_lock:
            _enrichment_state["error"] = str(e)
    finally:
        _enrichment_stop_requested = False  # Reset
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
    """
    with _enrichment_lock:
        return {
            "running": _enrichment_state["running"],
            "phase": _enrichment_state.get("phase", ""),
            "progress": _enrichment_state["progress"],
            "total": _enrichment_state["total"],
            "completed": _enrichment_state["completed"],
            "failed": _enrichment_state["failed"],
            "dos_completed": _enrichment_state.get("dos_completed", 0),
            "dos_found": _enrichment_state.get("dos_found", 0),
            "web_completed": _enrichment_state.get("web_completed", 0),
            "web_found": _enrichment_state.get("web_found", 0),
            "current_lead": _enrichment_state["current_lead"],
            "started_at": _enrichment_state["started_at"],
            "finished_at": _enrichment_state["finished_at"],
            "error": _enrichment_state.get("error"),
            "percent_complete": round(_enrichment_state["progress"] / _enrichment_state["total"] * 100, 1) if _enrichment_state["total"] > 0 else 0,
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
                            parts = []
                            if dos_entity.entity_type:
                                parts.append(f"Entity: {dos_entity.entity_type}")
                            if dos_entity.status:
                                parts.append(f"Status: {dos_entity.status}")
                            if dos_entity.formation_date:
                                parts.append(f"Formed: {dos_entity.formation_date[:10]}")
                            if dos_entity.process_name:
                                parts.append(f"Agent: {dos_entity.process_name}")
                            if parts:
                                lead.business_summary = " | ".join(parts)
                        
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
        buildings=lead.buildings[:10],  # Limit for response size
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
    
    # Filter leads
    filtered = list(_leads_cache)
    
    if min_score is not None:
        filtered = [l for l in filtered if l.score >= min_score]
    if min_portfolio is not None:
        filtered = [l for l in filtered if l.portfolio_size >= min_portfolio]
    if boro:
        boro_upper = boro.upper()
        filtered = [l for l in filtered if l.boro.upper() == boro_upper]
    
    # Sort by portfolio size and limit
    filtered.sort(key=lambda l: l.portfolio_size, reverse=True)
    filtered = filtered[:limit]
    
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
