"""Lead CRUD routes — list, get, update, outreach events, enrichment.

Migrated to PostgreSQL (AsyncSession). The PE Searcher persona uses these
endpoints to evaluate PM companies as acquisition targets.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_session
from src.auth.auth import AuthUser, get_current_user
from src.schemas.requests import UpdateLeadRequest, OutreachEventRequest, OutreachAttemptRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["leads"])
limiter = Limiter(key_func=get_remote_address)

ALLOWED_SORT_COLS = {
    "score", "portfolio_size", "total_units", "pipeline_stage",
    "outreach_status", "priority_rank", "company_name", "owner_name",
    "estimated_annual_revenue", "violation_count", "created_at", "updated_at",
    "violations_per_unit",
}

SORT_COL_EXPRESSIONS = {
    "units_per_bldg": "CASE WHEN portfolio_size > 0 THEN total_units::float / portfolio_size ELSE 0 END",
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
@limiter.limit("60/minute")
async def get_leads(
    request: Request,
    min_score: Optional[float] = Query(None),
    max_score: Optional[float] = Query(None, le=100),
    min_portfolio: Optional[int] = Query(None, le=10000),
    max_portfolio: Optional[int] = Query(None, le=10000),
    boro: Optional[str] = Query(None, max_length=100),
    has_phone: Optional[bool] = Query(None),
    has_email: Optional[bool] = Query(None),
    has_website: Optional[bool] = Query(None),
    entity_type: Optional[str] = Query(None, max_length=200),
    enrichment_status: Optional[str] = Query(None),
    outreach_status: Optional[str] = Query(None),
    pipeline_stage: Optional[str] = Query(None),
    search: Optional[str] = Query(None, max_length=200),
    sort_by: str = Query("score"),
    sort_dir: str = Query("desc"),
    min_units: Optional[int] = Query(None, le=1000000),
    max_units: Optional[int] = Query(None, le=1000000),
    min_units_per_bldg: Optional[float] = Query(None),
    max_units_per_bldg: Optional[float] = Query(None),
    building_type_has: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
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
        boro_list = [b.strip() for b in boro.split(",") if b.strip()]
        if len(boro_list) == 1:
            wheres.append("primary_borough = :boro_0")
            params["boro_0"] = boro_list[0]
        elif boro_list:
            placeholders = ", ".join(f":boro_{i}" for i in range(len(boro_list)))
            wheres.append(f"primary_borough IN ({placeholders})")
            for i, b in enumerate(boro_list):
                params[f"boro_{i}"] = b
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
    if min_units_per_bldg is not None:
        wheres.append("portfolio_size > 0 AND (total_units::float / portfolio_size) >= :min_upb")
        params["min_upb"] = min_units_per_bldg
    if max_units_per_bldg is not None:
        wheres.append("portfolio_size > 0 AND (total_units::float / portfolio_size) <= :max_upb")
        params["max_upb"] = max_units_per_bldg
    if building_type_has:
        for i, btype in enumerate(building_type_has.split(",")):
            btype = btype.strip()
            if btype:
                key = f"btype_{i}"
                wheres.append(f"COALESCE((building_types->>:{key})::int, 0) > 0")
                params[key] = btype
    if search:
        wheres.append("(owner_name ILIKE :search OR company_name ILIKE :search OR agent_name ILIKE :search)")
        params["search"] = f"%{search}%"

    where_sql = " AND ".join(wheres) if wheres else "1=1"

    if sort_by in SORT_COL_EXPRESSIONS:
        sort_col = SORT_COL_EXPRESSIONS[sort_by]
    elif sort_by in ALLOWED_SORT_COLS:
        sort_col = sort_by
    else:
        sort_col = "score"
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
@limiter.limit("60/minute")
async def get_lead(request: Request, lead_id: str, session: AsyncSession = Depends(get_session), user: AuthUser = Depends(get_current_user)):
    result = await session.execute(
        text("SELECT * FROM leads WHERE lead_id = :lid"), {"lid": lead_id}
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Lead not found")
    return _row_to_response(dict(row._mapping))


@router.post("/leads/{lead_id}/estimate-revenue")
@limiter.limit("30/minute")
async def estimate_lead_revenue(
    request: Request,
    lead_id: str,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """Estimate and persist revenue for a single lead based on unit count and borough rent data."""
    result = await session.execute(
        text("SELECT * FROM leads WHERE lead_id = :lid"), {"lid": lead_id}
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Lead not found")
    data = dict(row._mapping)

    total_units = data.get("total_units") or 0
    portfolio_size = data.get("portfolio_size") or 0
    boro = data.get("primary_borough") or "MANHATTAN"

    borough_avg_rents = {
        "MANHATTAN": 3500, "BROOKLYN": 2800, "QUEENS": 2200,
        "BRONX": 1800, "STATEN ISLAND": 1600,
    }
    avg_rent = borough_avg_rents.get(boro.upper(), 2200)
    fee_rate = 0.05

    if total_units > 0:
        monthly_gross = total_units * avg_rent
        monthly_revenue = monthly_gross * fee_rate
        annual_revenue = monthly_revenue * 12
    else:
        monthly_revenue = 0.0
        annual_revenue = 0.0

    await session.execute(
        text("""
            UPDATE leads SET
                estimated_monthly_revenue = :monthly,
                estimated_annual_revenue = :annual,
                updated_at = NOW()
            WHERE lead_id = :lid
        """),
        {"monthly": monthly_revenue, "annual": annual_revenue, "lid": lead_id},
    )
    await session.commit()

    data["estimated_monthly_revenue"] = monthly_revenue
    data["estimated_annual_revenue"] = annual_revenue
    return _row_to_response(data)


@router.patch("/leads/{lead_id}")
@limiter.limit("30/minute")
async def update_lead(
    request: Request,
    lead_id: str,
    body: UpdateLeadRequest,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    valid_statuses = {"new", "contacted", "interested", "not_interested", "closed"}
    valid_stages = {
        "research", "first_contact", "follow_up", "meeting_scheduled",
        "meeting_done", "loi", "due_diligence", "closed",
    }

    if body.outreach_status and body.outreach_status not in valid_statuses:
        raise HTTPException(400, f"Invalid status. Must be one of: {', '.join(valid_statuses)}")
    if body.pipeline_stage and body.pipeline_stage not in valid_stages:
        raise HTTPException(400, f"Invalid pipeline stage. Must be one of: {', '.join(valid_stages)}")
    if body.priority_rank is not None and not (0 <= body.priority_rank <= 5):
        raise HTTPException(400, "priority_rank must be 0-5")

    exists = (await session.execute(
        text("SELECT 1 FROM leads WHERE lead_id = :lid"), {"lid": lead_id}
    )).first()
    if not exists:
        raise HTTPException(404, "Lead not found")

    sets: list[str] = ["updated_at = NOW()"]
    params: dict = {"lid": lead_id}
    if body.outreach_status is not None:
        sets.append("outreach_status = :os")
        params["os"] = body.outreach_status
    if body.notes is not None:
        sets.append("notes = :notes")
        params["notes"] = body.notes
    if body.pipeline_stage is not None:
        sets.append("pipeline_stage = :ps")
        params["ps"] = body.pipeline_stage
    if body.next_follow_up is not None:
        sets.append("next_follow_up = :nfu")
        params["nfu"] = body.next_follow_up
    if body.priority_rank is not None:
        sets.append("priority_rank = :pr")
        params["pr"] = body.priority_rank

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
@limiter.limit("30/minute")
async def log_outreach_event(
    request: Request,
    lead_id: str,
    body: OutreachEventRequest,
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
            "lid": lead_id, "stage": body.stage,
            "method": body.method, "outcome": body.outcome,
            "notes": body.notes, "nfu": body.next_follow_up,
            "ts": datetime.now(timezone.utc),
        },
    )
    eid = result_insert.scalar_one()

    update_params: dict = {"lid": lead_id, "ps": body.stage}
    update_sql = "UPDATE leads SET pipeline_stage = :ps, updated_at = NOW()"
    if body.next_follow_up:
        update_sql += ", next_follow_up = :nfu"
        update_params["nfu"] = body.next_follow_up
    update_sql += " WHERE lead_id = :lid"
    await session.execute(text(update_sql), update_params)
    await session.commit()

    return {"status": "success", "event_id": eid}


@router.get("/leads/{lead_id}/outreach-events")
@limiter.limit("60/minute")
async def get_lead_outreach_events(
    request: Request,
    lead_id: str,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
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
@limiter.limit("30/minute")
async def add_outreach_attempt(
    request: Request,
    lead_id: str,
    body: OutreachAttemptRequest,
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
            "method": body.method, "outcome": body.outcome,
            "notes": body.notes, "ts": datetime.now(timezone.utc),
        },
    )
    eid = result_insert.scalar_one()
    await session.commit()
    return {
        "status": "success",
        "attempt": {
            "id": eid, "method": body.method,
            "outcome": body.outcome, "notes": body.notes,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


@router.get("/leads/{lead_id}/outreach")
@limiter.limit("60/minute")
async def get_outreach_attempts(
    request: Request,
    lead_id: str,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
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


@router.post("/leads/{lead_id}/enrich-all")
@limiter.limit("30/minute")
async def enrich_lead_all(
    request: Request,
    lead_id: str,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """PostgreSQL-native lead enrichment: multi-source contacts, web scrape, AI summary."""
    row_result = await session.execute(
        text("SELECT * FROM leads WHERE lead_id = :lid"), {"lid": lead_id}
    )
    row = row_result.first()
    if not row:
        raise HTTPException(404, "Lead not found")
    lead_data = dict(row._mapping)

    company_name = lead_data.get("company_name") or lead_data.get("agent_name") or lead_data.get("owner_name") or ""
    boro = lead_data.get("boro") or lead_data.get("primary_borough") or ""
    location = f"{boro}, New York" if boro else "New York, NY"
    errors: list[str] = []
    owner_names: list[str] = []
    website_scraped = False
    ai_generated = False
    ai_description = None
    phones_found = 0
    emails_found = 0
    phone = lead_data.get("phone")
    email = lead_data.get("email")
    website = lead_data.get("website")
    enrichment_sources = lead_data.get("enrichment_sources") or []
    owner_principal = lead_data.get("owner_principal")
    dos_id = lead_data.get("dos_id")
    dos_status = lead_data.get("dos_status")
    business_summary = lead_data.get("business_summary")

    # 1) Multi-source contact enrichment
    try:
        from src.enrich.contact_sources import MultiSourceEnricher
        ms = MultiSourceEnricher()
        import asyncio
        result = await asyncio.to_thread(
            ms.enrich,
            lead_id=lead_id,
            company_name=company_name,
            website=website,
            location=location,
            address=lead_data.get("address"),
            contacts=None,
        )
        phones_found = len(result.phones or [])
        emails_found = len(result.emails or [])
        if result.phones and not phone:
            best = result.best_phone()
            if best:
                phone = best.value
        if result.emails and not email:
            best = result.best_email()
            if best:
                email = best.value
        if result.website and not website:
            website = result.website
        if result.dos_registered_agent and not owner_principal:
            owner_principal = result.dos_registered_agent
        if result.dos_id:
            dos_id = result.dos_id
            dos_status = result.dos_entity_type or dos_status
        if result.sources_succeeded:
            src_set = set(enrichment_sources)
            for s in result.sources_succeeded:
                src_set.add(s)
            enrichment_sources = list(src_set)
        if result.errors:
            errors.extend(result.errors)
    except Exception as e:
        errors.append(f"contacts: {e}")
        logger.warning(f"Enrichment contacts failed for {lead_id}: {e}")

    # 2) Website research / scrape
    try:
        from src.enrich.web_crawl import WebCrawler
        import asyncio
        crawler = WebCrawler()
        if not website:
            found = await asyncio.to_thread(
                crawler.find_website, f"{company_name} property management NYC"
            )
            if found:
                website = found
        if website:
            scrape_data = await asyncio.to_thread(
                crawler.deep_scrape, website, company_name
            )
            website_scraped = True
            owner_names = scrape_data.get("owner_names", []) or []
            if scrape_data.get("phones") and not phone:
                phone = scrape_data["phones"][0]
            if scrape_data.get("emails") and not email:
                email = scrape_data["emails"][0]
    except Exception as e:
        errors.append(f"research: {e}")
        logger.warning(f"Enrichment web research failed for {lead_id}: {e}")

    # 3) AI summary
    try:
        from src.enrich.ai_summary import generate_company_description
        import asyncio
        portfolio_size = lead_data.get("portfolio_size") or 0
        total_units = lead_data.get("total_units") or 0
        boros = lead_data.get("boros")
        desc, ai_err = await asyncio.to_thread(
            generate_company_description,
            company_name=company_name,
            portfolio_size=portfolio_size,
            total_units=total_units,
            boroughs=boros,
            building_types=None,
            owner_names=owner_names or ([owner_principal] if owner_principal else None),
        )
        if desc:
            ai_generated = True
            ai_description = desc
            business_summary = desc
        elif ai_err:
            ai_err_l = str(ai_err).lower()
            if "api key" not in ai_err_l and "not configured" not in ai_err_l:
                errors.append(f"ai_summary: {ai_err}")
    except Exception as e:
        errors.append(f"ai_summary: {e}")
        logger.warning(f"Enrichment AI summary failed for {lead_id}: {e}")

    # Compute enrichment status
    has_contact = bool(phone or email)
    has_website = bool(website)
    old_status = lead_data.get("enrichment_status") or ""
    if has_contact and has_website:
        enrichment_status = "complete"
    elif has_contact or has_website or ai_generated or business_summary or owner_principal:
        enrichment_status = "partial"
    elif old_status in ("partial", "complete"):
        enrichment_status = old_status
    else:
        enrichment_status = "failed"

    # Persist to PostgreSQL
    await session.execute(text("""
        UPDATE leads SET
            phone = :phone,
            email = :email,
            website = :website,
            owner_principal = :owner_principal,
            dos_id = :dos_id,
            dos_status = :dos_status,
            business_summary = :business_summary,
            enrichment_status = :enrichment_status,
            enrichment_sources = :enrichment_sources::jsonb,
            last_enriched = NOW(),
            updated_at = NOW()
        WHERE lead_id = :lid
    """), {
        "phone": phone, "email": email, "website": website,
        "owner_principal": owner_principal, "dos_id": dos_id,
        "dos_status": dos_status, "business_summary": business_summary,
        "enrichment_status": enrichment_status,
        "enrichment_sources": json.dumps(enrichment_sources),
        "lid": lead_id,
    })
    await session.commit()

    # Re-fetch updated row
    updated = await session.execute(
        text("SELECT * FROM leads WHERE lead_id = :lid"), {"lid": lead_id}
    )
    updated_row = dict(updated.first()._mapping)

    return {
        "lead_id": lead_id,
        "contacts": {"phones_found": phones_found, "emails_found": emails_found, "website_found": bool(website)},
        "research": {"owner_names": owner_names, "year_established": None, "website_scraped": website_scraped},
        "ai_summary": {"generated": ai_generated, "description": ai_description},
        "errors": errors,
        "enrichment_status": enrichment_status,
        "pipeline_stage": updated_row.get("pipeline_stage", "research"),
        "lead": _row_to_response(updated_row),
    }


@router.get("/follow-ups")
@limiter.limit("60/minute")
async def get_follow_ups_due(
    request: Request,
    before: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
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
@limiter.limit("60/minute")
async def get_stats(request: Request, session: AsyncSession = Depends(get_session), user: AuthUser = Depends(get_current_user)):
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
