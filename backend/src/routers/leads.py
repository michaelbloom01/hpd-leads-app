"""Lead CRUD routes — list, get, update, outreach events.

Migrated to PostgreSQL (AsyncSession). The PE Searcher persona uses these
endpoints to evaluate PM companies as acquisition targets.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_session
from src.auth.auth import AuthUser, get_current_user
from src.schemas.requests import UpdateLeadRequest, OutreachEventRequest, OutreachAttemptRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["leads"])

ALLOWED_SORT_COLS = {
    "score", "portfolio_size", "total_units", "pipeline_stage",
    "outreach_status", "priority_rank", "company_name", "owner_name",
    "estimated_annual_revenue", "violation_count", "created_at", "updated_at",
}


def _row_to_response(r: dict) -> dict:
    """Convert a DB row dict to the LeadResponse shape the frontend expects."""
    breakdown = r.get("score_breakdown") or {}
    buildings = r.get("buildings") or []
    boros = r.get("boros") or []
    return {
        "lead_id": r["lead_id"],
        "agent_name": r.get("agent_name") or "",
        "owner_name": r.get("owner_name") or "",
        "owner_type": r.get("owner_type") or "unknown",
        "portfolio_size": r.get("portfolio_size") or 0,
        "total_units": r.get("total_units") or 0,
        "buildings": buildings if isinstance(buildings, list) else [],
        "phone": r.get("phone"),
        "email": r.get("email"),
        "phones": [],
        "emails": [],
        "website": r.get("website"),
        "business_summary": (r.get("business_summary") or "")[:200] or None,
        "address": r.get("address"),
        "boro": r.get("primary_borough") or (boros[0] if boros else ""),
        "boros": boros if isinstance(boros, list) else [],
        "building_types": None,
        "building_classes": r.get("building_classes") or {},
        "score": r.get("score") or 0.0,
        "score_breakdown": {
            "portfolio": breakdown.get("portfolio", 0.0),
            "units": breakdown.get("units", 0.0),
            "professional": breakdown.get("professional", 0.0),
            "contact": breakdown.get("contact", 0.0),
            "concentration": breakdown.get("concentration", 0.0),
            "condo_coop": breakdown.get("condo_coop", 0.0),
            "density": breakdown.get("density", 0.0),
            "location": breakdown.get("location", 0.0),
            "revenue": breakdown.get("revenue", 0.0),
            "distress": breakdown.get("distress", 0.0),
            "deal_fit": breakdown.get("deal_fit", 0.0),
        } if breakdown else None,
        "tags": r.get("tags") or [],
        "enrichment_status": r.get("enrichment_status") or "none",
        "outreach_status": r.get("outreach_status") or "new",
        "notes": r.get("notes"),
        "outreach_attempts": [],
        "contacts": r.get("contacts") or [],
        "entity_type": r.get("entity_type") or "unknown",
        "company_name": r.get("company_name"),
        "primary_contact": r.get("primary_contact"),
        "primary_contact_title": r.get("primary_contact_title"),
        "estimated_monthly_revenue": r.get("estimated_monthly_revenue") or 0.0,
        "estimated_annual_revenue": r.get("estimated_annual_revenue") or 0.0,
        "revenue_breakdown": None,
        "violation_count": r.get("violation_count") or 0,
        "violation_class_a": r.get("violation_class_a") or 0,
        "violation_class_b": r.get("violation_class_b") or 0,
        "violation_class_c": r.get("violation_class_c") or 0,
        "violations_per_unit": r.get("violations_per_unit") or 0.0,
        "pipeline_stage": r.get("pipeline_stage") or "research",
        "next_follow_up": str(r["next_follow_up"]) if r.get("next_follow_up") else None,
        "priority_rank": r.get("priority_rank") or 0,
        "data_staleness": None,
    }


@router.get("/leads")
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
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    wheres: list[str] = []
    params: dict = {"limit": limit, "offset": offset}

    if min_score is not None:
        wheres.append("score >= :min_score")
        params["min_score"] = min_score
    if max_score is not None:
        wheres.append("score <= :max_score")
        params["max_score"] = max_score
    if min_portfolio is not None:
        wheres.append("portfolio_size >= :min_portfolio")
        params["min_portfolio"] = min_portfolio
    if max_portfolio is not None:
        wheres.append("portfolio_size <= :max_portfolio")
        params["max_portfolio"] = max_portfolio
    if boro:
        wheres.append("primary_borough = :boro")
        params["boro"] = boro
    if has_phone:
        wheres.append("phone IS NOT NULL AND phone != ''")
    if has_email:
        wheres.append("email IS NOT NULL AND email != ''")
    if has_website:
        wheres.append("website IS NOT NULL AND website != ''")
    if entity_type:
        wheres.append("entity_type = :entity_type")
        params["entity_type"] = entity_type
    if enrichment_status:
        wheres.append("enrichment_status = :enrichment_status")
        params["enrichment_status"] = enrichment_status
    if outreach_status:
        wheres.append("outreach_status = :outreach_status")
        params["outreach_status"] = outreach_status
    if pipeline_stage:
        wheres.append("pipeline_stage = :pipeline_stage")
        params["pipeline_stage"] = pipeline_stage
    if min_units is not None:
        wheres.append("total_units >= :min_units")
        params["min_units"] = min_units
    if max_units is not None:
        wheres.append("total_units <= :max_units")
        params["max_units"] = max_units
    if search:
        wheres.append("(owner_name ILIKE :search OR company_name ILIKE :search OR agent_name ILIKE :search)")
        params["search"] = f"%{search}%"

    where_sql = " AND ".join(wheres) if wheres else "1=1"
    sort_col = sort_by if sort_by in ALLOWED_SORT_COLS else "score"
    sort_direction = "ASC" if sort_dir.lower() == "asc" else "DESC"

    count_result = await session.execute(
        text(f"SELECT COUNT(*) FROM leads WHERE {where_sql}"), params
    )
    total = count_result.scalar() or 0

    result = await session.execute(
        text(f"""
            SELECT * FROM leads
            WHERE {where_sql}
            ORDER BY {sort_col} {sort_direction} NULLS LAST
            LIMIT :limit OFFSET :offset
        """),
        params,
    )
    leads = [_row_to_response(dict(r._mapping)) for r in result]
    return {"leads": leads, "total": total, "offset": offset, "limit": limit}


@router.get("/leads/{lead_id}")
async def get_lead(lead_id: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        text("SELECT * FROM leads WHERE lead_id = :lid"), {"lid": lead_id}
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Lead not found")
    return _row_to_response(dict(row._mapping))


@router.patch("/leads/{lead_id}")
async def update_lead(
    lead_id: str,
    request: UpdateLeadRequest,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    valid_statuses = {"new", "contacted", "interested", "not_interested", "closed"}
    valid_stages = {
        "research", "first_contact", "follow_up", "meeting_scheduled",
        "meeting_done", "loi", "due_diligence", "closed",
    }

    if request.outreach_status and request.outreach_status not in valid_statuses:
        raise HTTPException(400, f"Invalid status. Must be one of: {', '.join(valid_statuses)}")
    if request.pipeline_stage and request.pipeline_stage not in valid_stages:
        raise HTTPException(400, f"Invalid pipeline stage. Must be one of: {', '.join(valid_stages)}")
    if request.priority_rank is not None and not (0 <= request.priority_rank <= 5):
        raise HTTPException(400, "priority_rank must be 0-5")

    exists = (await session.execute(
        text("SELECT 1 FROM leads WHERE lead_id = :lid"), {"lid": lead_id}
    )).first()
    if not exists:
        raise HTTPException(404, "Lead not found")

    sets: list[str] = ["updated_at = NOW()"]
    params: dict = {"lid": lead_id}
    if request.outreach_status is not None:
        sets.append("outreach_status = :os")
        params["os"] = request.outreach_status
    if request.notes is not None:
        sets.append("notes = :notes")
        params["notes"] = request.notes
    if request.pipeline_stage is not None:
        sets.append("pipeline_stage = :ps")
        params["ps"] = request.pipeline_stage
    if request.next_follow_up is not None:
        sets.append("next_follow_up = :nfu")
        params["nfu"] = request.next_follow_up
    if request.priority_rank is not None:
        sets.append("priority_rank = :pr")
        params["pr"] = request.priority_rank

    if len(sets) > 1:
        await session.execute(
            text(f"UPDATE leads SET {', '.join(sets)} WHERE lead_id = :lid"), params
        )
        await session.commit()

    updated = await session.execute(
        text("SELECT * FROM leads WHERE lead_id = :lid"), {"lid": lead_id}
    )
    row = dict(updated.first()._mapping)
    return {
        "status": "success",
        "lead_id": lead_id,
        "outreach_status": row.get("outreach_status"),
        "pipeline_stage": row.get("pipeline_stage"),
        "next_follow_up": str(row["next_follow_up"]) if row.get("next_follow_up") else None,
        "priority_rank": row.get("priority_rank"),
        "notes": row.get("notes"),
    }


@router.post("/leads/{lead_id}/outreach-event")
async def log_outreach_event(
    lead_id: str,
    request: OutreachEventRequest,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    exists = (await session.execute(
        text("SELECT 1 FROM leads WHERE lead_id = :lid"), {"lid": lead_id}
    )).first()
    if not exists:
        raise HTTPException(404, "Lead not found")

    result_insert = await session.execute(
        text("""
            INSERT INTO outreach_events (lead_id, stage, method, outcome, notes, next_follow_up, event_timestamp, created_at, updated_at)
            VALUES (:lid, :stage, :method, :outcome, :notes, :nfu, :ts, NOW(), NOW())
            RETURNING id
        """),
        {
            "lid": lead_id, "stage": request.stage,
            "method": request.method, "outcome": request.outcome,
            "notes": request.notes, "nfu": request.next_follow_up,
            "ts": datetime.now(timezone.utc),
        },
    )
    eid = result_insert.scalar_one()

    update_params: dict = {"lid": lead_id, "ps": request.stage}
    update_sql = "UPDATE leads SET pipeline_stage = :ps, updated_at = NOW()"
    if request.next_follow_up:
        update_sql += ", next_follow_up = :nfu"
        update_params["nfu"] = request.next_follow_up
    update_sql += " WHERE lead_id = :lid"
    await session.execute(text(update_sql), update_params)
    await session.commit()

    return {"status": "success", "event_id": eid}


@router.get("/leads/{lead_id}/outreach-events")
async def get_lead_outreach_events(
    lead_id: str,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        text("""
            SELECT id, lead_id, stage, method, outcome, notes, next_follow_up,
                   event_timestamp, created_at
            FROM outreach_events
            WHERE lead_id = :lid
            ORDER BY event_timestamp DESC
        """),
        {"lid": lead_id},
    )
    events = [dict(r._mapping) for r in result]
    for e in events:
        for k in ("event_timestamp", "created_at", "next_follow_up"):
            if e.get(k) and hasattr(e[k], "isoformat"):
                e[k] = e[k].isoformat()
    return {"lead_id": lead_id, "events": events}


@router.post("/leads/{lead_id}/outreach")
async def add_outreach_attempt(
    lead_id: str,
    request: OutreachAttemptRequest,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    exists = (await session.execute(
        text("SELECT 1 FROM leads WHERE lead_id = :lid"), {"lid": lead_id}
    )).first()
    if not exists:
        raise HTTPException(404, "Lead not found")

    result_insert = await session.execute(
        text("""
            INSERT INTO outreach_events (lead_id, stage, method, outcome, notes, event_timestamp, created_at, updated_at)
            VALUES (:lid, 'outreach', :method, :outcome, :notes, :ts, NOW(), NOW())
            RETURNING id
        """),
        {
            "lid": lead_id,
            "method": request.method, "outcome": request.outcome,
            "notes": request.notes, "ts": datetime.now(timezone.utc),
        },
    )
    eid = result_insert.scalar_one()
    await session.commit()
    return {
        "status": "success",
        "attempt": {
            "id": eid, "method": request.method,
            "outcome": request.outcome, "notes": request.notes,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


@router.get("/leads/{lead_id}/outreach")
async def get_outreach_attempts(
    lead_id: str,
    session: AsyncSession = Depends(get_session),
):
    exists = (await session.execute(
        text("SELECT 1 FROM leads WHERE lead_id = :lid"), {"lid": lead_id}
    )).first()
    if not exists:
        raise HTTPException(404, "Lead not found")
    result = await session.execute(
        text("""
            SELECT id, method, outcome, notes, event_timestamp AS timestamp
            FROM outreach_events WHERE lead_id = :lid
            ORDER BY event_timestamp DESC
        """),
        {"lid": lead_id},
    )
    events = []
    for r in result:
        d = dict(r._mapping)
        if d.get("timestamp") and hasattr(d["timestamp"], "isoformat"):
            d["timestamp"] = d["timestamp"].isoformat()
        events.append(d)
    return events


@router.get("/follow-ups")
async def get_follow_ups_due(
    before: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    if before:
        result = await session.execute(
            text("""
                SELECT lead_id, owner_name, company_name, next_follow_up, pipeline_stage, outreach_status
                FROM leads WHERE next_follow_up IS NOT NULL AND next_follow_up <= :before
                ORDER BY next_follow_up ASC
            """),
            {"before": before},
        )
    else:
        result = await session.execute(
            text("""
                SELECT lead_id, owner_name, company_name, next_follow_up, pipeline_stage, outreach_status
                FROM leads WHERE next_follow_up IS NOT NULL AND next_follow_up <= CURRENT_DATE
                ORDER BY next_follow_up ASC
            """)
        )
    rows = [dict(r._mapping) for r in result]
    for r in rows:
        if r.get("next_follow_up") and hasattr(r["next_follow_up"], "isoformat"):
            r["next_follow_up"] = r["next_follow_up"].isoformat()
    return {"count": len(rows), "leads": rows}


@router.get("/stats")
async def get_stats(session: AsyncSession = Depends(get_session)):
    """Aggregate stats for the dashboard — full structure expected by the frontend."""
    core = dict((await session.execute(text("""
        SELECT
            COUNT(*) AS total_leads,
            COALESCE(SUM(portfolio_size), 0) AS total_buildings,
            COALESCE(SUM(total_units), 0) AS total_units,
            COALESCE(MAX(score), 0) AS top_score,
            COALESCE(AVG(score), 0) AS avg_score,
            COALESCE(SUM(CASE WHEN phone IS NOT NULL AND phone != '' THEN 1 ELSE 0 END), 0) AS with_phone,
            COALESCE(SUM(CASE WHEN email IS NOT NULL AND email != '' THEN 1 ELSE 0 END), 0) AS with_email,
            COALESCE(SUM(CASE WHEN website IS NOT NULL AND website != '' THEN 1 ELSE 0 END), 0) AS with_website
        FROM leads
    """))).first()._mapping)

    # Borough distribution
    by_borough = {
        r[0] or "Unknown": r[1]
        for r in (await session.execute(text(
            "SELECT primary_borough, COUNT(*) FROM leads GROUP BY primary_borough"
        ))).fetchall()
    }

    # Enrichment status distribution
    by_enrichment = {
        r[0] or "none": r[1]
        for r in (await session.execute(text(
            "SELECT enrichment_status, COUNT(*) FROM leads GROUP BY enrichment_status"
        ))).fetchall()
    }

    # Outreach status distribution
    by_outreach = {
        r[0] or "new": r[1]
        for r in (await session.execute(text(
            "SELECT outreach_status, COUNT(*) FROM leads GROUP BY outreach_status"
        ))).fetchall()
    }

    # Entity type distribution
    by_entity = {
        r[0] or "unknown": r[1]
        for r in (await session.execute(text(
            "SELECT entity_type, COUNT(*) FROM leads GROUP BY entity_type"
        ))).fetchall()
    }

    # Pipeline stage distribution
    by_pipeline = {
        r[0] or "research": r[1]
        for r in (await session.execute(text(
            "SELECT pipeline_stage, COUNT(*) FROM leads GROUP BY pipeline_stage"
        ))).fetchall()
    }

    # Score distribution (buckets)
    score_dist = {
        r[0]: r[1]
        for r in (await session.execute(text("""
            SELECT
                CASE
                    WHEN score < 20 THEN '0-20'
                    WHEN score < 40 THEN '20-40'
                    WHEN score < 60 THEN '40-60'
                    WHEN score < 80 THEN '60-80'
                    ELSE '80-100'
                END AS bucket,
                COUNT(*) AS cnt
            FROM leads GROUP BY bucket
        """))).fetchall()
    }

    # Portfolio size distribution
    portfolio_dist = {
        r[0]: r[1]
        for r in (await session.execute(text("""
            SELECT
                CASE
                    WHEN portfolio_size <= 5 THEN '1-5'
                    WHEN portfolio_size <= 10 THEN '6-10'
                    WHEN portfolio_size <= 25 THEN '11-25'
                    WHEN portfolio_size <= 50 THEN '26-50'
                    WHEN portfolio_size <= 100 THEN '51-100'
                    ELSE '100+'
                END AS bucket,
                COUNT(*) AS cnt
            FROM leads GROUP BY bucket
        """))).fetchall()
    }

    refresh_row = (await session.execute(text("""
        SELECT started_at FROM ingestion_jobs
        WHERE job_type = 'buildings' AND status = 'completed'
        ORDER BY started_at DESC LIMIT 1
    """))).first()

    return {
        "total_leads": core["total_leads"],
        "total_buildings": core["total_buildings"],
        "total_units": core["total_units"],
        "top_score": round(float(core["top_score"]), 1),
        "avg_score": round(float(core["avg_score"]), 1),
        "with_phone": core["with_phone"],
        "with_email": core["with_email"],
        "with_website": core["with_website"],
        "by_borough": by_borough,
        "by_enrichment_status": by_enrichment,
        "by_outreach_status": by_outreach,
        "by_entity_type": by_entity,
        "by_pipeline_stage": by_pipeline,
        "score_distribution": score_dist,
        "portfolio_distribution": portfolio_dist,
        "last_refresh": refresh_row[0].isoformat() if refresh_row else None,
    }
