"""Lead CRUD routes — list, get, update, outreach events."""
import logging
import uuid as _uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query, HTTPException

from src.storage.database import get_database
from src.services.cache_manager import get_cache
from src.services.lead_converter import row_to_lead, get_outreach_attempts_for_lead, get_revenue_breakdown
from src.schemas.requests import UpdateLeadRequest, OutreachEventRequest, OutreachAttemptRequest
from src.schemas.responses import LeadResponse, LeadsListResponse
from src.score.revenue import estimate_revenue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["leads"])


@router.get("/leads", response_model=LeadsListResponse)
async def get_leads(
    min_score: Optional[float] = Query(None),
    max_score: Optional[float] = Query(None),
    min_portfolio: Optional[int] = Query(None),
    max_portfolio: Optional[int] = Query(None),
    boro: Optional[str] = Query(None),
    has_phone: Optional[bool] = Query(None),
    has_email: Optional[bool] = Query(None),
    has_website: Optional[bool] = Query(None),
    entity_type: Optional[str] = Query(None),
    enrichment_status: Optional[str] = Query(None),
    outreach_status: Optional[str] = Query(None),
    pipeline_stage: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sort_by: str = Query("score"),
    sort_dir: str = Query("desc"),
    min_units: Optional[int] = Query(None),
    max_units: Optional[int] = Query(None),
    min_units_per_bldg: Optional[float] = Query(None),
    max_units_per_bldg: Optional[float] = Query(None),
    building_type_has: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """Get leads with SQL-based filtering (fast, indexed). Returns paginated results."""
    db = get_database()
    rows, total = db.get_leads_filtered(
        min_score=min_score, max_score=max_score,
        min_portfolio=min_portfolio, max_portfolio=max_portfolio,
        boro=boro, has_phone=has_phone, has_email=has_email, has_website=has_website,
        entity_type=entity_type,
        min_units=min_units, max_units=max_units,
        min_units_per_bldg=min_units_per_bldg, max_units_per_bldg=max_units_per_bldg,
        building_type_has=building_type_has,
        enrichment_status=enrichment_status, outreach_status=outreach_status,
        pipeline_stage=pipeline_stage, search=search,
        sort_by=sort_by, sort_dir=sort_dir,
        limit=limit, offset=offset,
    )

    leads = [row_to_lead(r) for r in rows]
    return LeadsListResponse(
        leads=[
            LeadResponse.from_lead(l, get_outreach_attempts_for_lead(l.lead_id), get_revenue_breakdown(l))
            for l in leads
        ],
        total=total, offset=offset, limit=limit,
    )


@router.get("/leads/{lead_id}", response_model=LeadResponse)
async def get_lead(lead_id: str):
    """Get a single lead by ID."""
    db = get_database()
    row = db.get_lead_by_id(lead_id)
    if row:
        lead = row_to_lead(row)
        return LeadResponse.from_lead(lead, get_outreach_attempts_for_lead(lead_id), get_revenue_breakdown(lead))
    raise HTTPException(status_code=404, detail="Lead not found")


@router.post("/leads/{lead_id}/estimate-revenue", response_model=LeadResponse)
async def estimate_lead_revenue(lead_id: str):
    """Estimate revenue for a single lead, persist it, and return the updated lead."""
    db = get_database()
    row = db.get_lead_by_id(lead_id)
    if not row:
        raise HTTPException(status_code=404, detail="Lead not found")

    lead = row_to_lead(row)
    rev = estimate_revenue(lead)
    monthly = rev.get("estimated_monthly_revenue", 0.0) or 0.0
    annual = rev.get("estimated_annual_revenue", 0.0) or 0.0

    # Persist revenue values for future reads.
    db.update_lead(lead_id, {
        "estimated_monthly_revenue": monthly,
        "estimated_annual_revenue": annual,
    })

    # Keep in-memory cache consistent.
    cache = get_cache()
    with cache.leads_lock:
        for cached_lead in cache.leads:
            if cached_lead.lead_id == lead_id:
                cached_lead.estimated_monthly_revenue = monthly
                cached_lead.estimated_annual_revenue = annual
                break

    updated_row = db.get_lead_by_id(lead_id)
    updated_lead = row_to_lead(updated_row) if updated_row else lead
    return LeadResponse.from_lead(
        updated_lead,
        get_outreach_attempts_for_lead(lead_id),
        get_revenue_breakdown(updated_lead),
    )


@router.patch("/leads/{lead_id}")
async def update_lead(lead_id: str, request: UpdateLeadRequest):
    """Update a lead's outreach status, notes, pipeline stage, follow-up, and priority."""
    cache = get_cache()
    db = get_database()

    valid_statuses = {"new", "contacted", "interested", "not_interested", "closed"}
    valid_stages = {"research", "first_contact", "follow_up", "meeting_scheduled",
                    "meeting_done", "loi", "due_diligence", "closed"}

    if request.outreach_status and request.outreach_status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}")
    if request.pipeline_stage and request.pipeline_stage not in valid_stages:
        raise HTTPException(status_code=400, detail=f"Invalid pipeline stage. Must be one of: {', '.join(valid_stages)}")
    if request.priority_rank is not None and not (0 <= request.priority_rank <= 5):
        raise HTTPException(status_code=400, detail="priority_rank must be 0-5")

    if not db.get_lead_by_id(lead_id):
        raise HTTPException(status_code=404, detail="Lead not found")

    updates = {}
    if request.outreach_status is not None:
        updates["outreach_status"] = request.outreach_status
    if request.notes is not None:
        updates["notes"] = request.notes
    if request.pipeline_stage is not None:
        updates["pipeline_stage"] = request.pipeline_stage
    if request.next_follow_up is not None:
        updates["next_follow_up"] = request.next_follow_up
    if request.priority_rank is not None:
        updates["priority_rank"] = request.priority_rank
    if updates:
        db.update_lead(lead_id, updates)
    db.save_lead_user_data(lead_id=lead_id, outreach_status=request.outreach_status, notes=request.notes)

    with cache.leads_lock:
        for i, lead in enumerate(cache.leads):
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
                cache.leads[i] = lead
                break

    updated = db.get_lead_by_id(lead_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Lead not found after update")
    updated_lead = row_to_lead(updated)
    return {
        "status": "success", "lead_id": lead_id,
        "outreach_status": updated_lead.outreach_status, "pipeline_stage": updated_lead.pipeline_stage,
        "next_follow_up": updated_lead.next_follow_up, "priority_rank": updated_lead.priority_rank,
        "notes": updated_lead.notes,
    }


@router.post("/leads/{lead_id}/outreach-event")
async def log_outreach_event(lead_id: str, request: OutreachEventRequest):
    """Log an outreach event for a lead."""
    db = get_database()
    if not db.get_lead_by_id(lead_id):
        raise HTTPException(status_code=404, detail="Lead not found")

    event_id = db.add_outreach_event(lead_id, {
        "stage": request.stage, "method": request.method,
        "outcome": request.outcome, "notes": request.notes,
        "next_follow_up": request.next_follow_up,
    })
    updates = {"pipeline_stage": request.stage}
    if request.next_follow_up:
        updates["next_follow_up"] = request.next_follow_up
    db.update_lead(lead_id, updates)

    cache = get_cache()
    with cache.leads_lock:
        for lead in cache.leads:
            if lead.lead_id == lead_id:
                lead.pipeline_stage = request.stage
                if request.next_follow_up:
                    lead.next_follow_up = request.next_follow_up
                break

    return {"status": "success", "event_id": event_id}


@router.get("/leads/{lead_id}/outreach-events")
async def get_lead_outreach_events(lead_id: str):
    """Get all outreach events for a lead."""
    db = get_database()
    events = db.get_outreach_events(lead_id)
    return {"lead_id": lead_id, "events": events}


@router.post("/leads/{lead_id}/outreach")
async def add_outreach_attempt(lead_id: str, request: OutreachAttemptRequest):
    """Add an outreach attempt to a lead's history."""
    db = get_database()
    if not db.get_lead_by_id(lead_id):
        raise HTTPException(status_code=404, detail="Lead not found")

    attempt = {
        "id": str(_uuid.uuid4()), "method": request.method,
        "outcome": request.outcome, "notes": request.notes,
        "timestamp": datetime.now().isoformat(),
    }
    db.add_outreach_attempt(lead_id, attempt)
    return {"status": "success", "attempt": attempt}


@router.get("/leads/{lead_id}/outreach")
async def get_outreach_attempts(lead_id: str):
    """Get all outreach attempts for a lead."""
    db = get_database()
    if not db.get_lead_by_id(lead_id):
        raise HTTPException(status_code=404, detail="Lead not found")
    return db.get_outreach_attempts(lead_id)


@router.get("/follow-ups")
async def get_follow_ups_due(before: Optional[str] = None):
    """Get leads with follow-ups due on or before a date."""
    db = get_database()
    follow_ups = db.get_follow_ups_due(before)
    return {"count": len(follow_ups), "leads": follow_ups}
