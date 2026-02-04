"""
FastAPI server for HPD Leads Pipeline.

Exposes REST endpoints for the frontend to:
- Fetch leads
- Trigger enrichment
- Get pipeline status
"""
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.ingest.hpd_client import HPDClient
from src.transform.normalize import normalize_building
from src.transform.aggregate import aggregate_to_leads, Lead
from src.score.scorer import score_leads
from src.enrich.enricher import Enricher
from src.enrich.ny_dos import NYDOSClient

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

# Global state (in production, use a database)
_leads_cache: List[Lead] = []
_last_refresh: Optional[datetime] = None


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
        raise HTTPException(status_code=404, detail="No leads available. Run /api/refresh first.")
    
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


@app.post("/api/refresh")
async def refresh_pipeline(
    limit: Optional[int] = Query(None, description="Max buildings to fetch (None = ALL ~200k)"),
    full: bool = Query(False, description="Fetch ALL buildings (overrides limit)")
):
    """
    Refresh the pipeline: fetch from HPD, normalize, aggregate, score.
    
    By default fetches 10,000 buildings for speed. Set full=true to fetch ALL ~200k.
    This is an expensive operation - call sparingly.
    """
    global _leads_cache, _last_refresh
    
    # If full=true, remove limit to get everything
    if full:
        limit = None
    elif limit is None:
        limit = 10000  # Default for quick refresh
    
    logger.info(f"Starting pipeline refresh with limit={limit or 'ALL'}")
    
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
    
    # Update cache
    for lead in enriched:
        if lead.lead_id in lead_index:
            _leads_cache[lead_index[lead.lead_id]] = lead
    
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


@app.post("/api/enrich/batch")
async def enrich_batch(
    limit: int = Query(10, le=50, description="Max leads to enrich"),
    min_score: Optional[float] = Query(None, description="Only enrich leads with score >= this"),
):
    """
    Enrich a batch of top leads automatically.
    
    Prioritizes high-score leads that haven't been enriched.
    """
    global _leads_cache
    
    enricher = Enricher(use_cache=True)
    enriched = enricher.enrich_batch(
        _leads_cache,
        limit=limit,
        skip_enriched=True,
        min_score=min_score,
    )
    
    # Update cache
    lead_index = {l.lead_id: i for i, l in enumerate(_leads_cache)}
    for lead in enriched:
        if lead.lead_id in lead_index:
            _leads_cache[lead_index[lead.lead_id]] = lead
    
    return {
        "status": "success",
        "enriched_count": len(enriched),
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
        business_summary=lead.business_summary[:200] if lead.business_summary else None,
        address=lead.address,
        boro=lead.boro,
        boros=lead.boros,
        score=lead.score,
        tags=lead.tags,
        enrichment_status=lead.enrichment_status,
        outreach_status=lead.outreach_status,
        notes=lead.notes,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
