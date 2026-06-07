"""Lead CRUD routes — list, get, update, outreach events, enrichment.

Migrated to PostgreSQL (AsyncSession). The PE Searcher persona uses these
endpoints to evaluate PM companies as acquisition targets.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_session
from src.auth.auth import AuthUser, get_current_user
from src.schemas.requests import (
    BatchPipelineStageUpdateRequest,
    EnrichmentRequest,
    UpdateLeadRequest,
    OutreachEventRequest,
    OutreachAttemptRequest,
)
from src.services.contact_roster import get_lead_contacts
from src.services.lead_generation import summarize_portfolio_building_types

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["leads"])
limiter = Limiter(key_func=get_remote_address)

ALLOWED_SORT_COLS = {
    "score", "portfolio_size", "total_units", "pipeline_stage",
    "outreach_status", "priority_rank", "company_name", "owner_name",
    "estimated_annual_revenue", "violation_count", "created_at", "updated_at",
    "violations_per_unit",
}

SIMPLE_SORT_COLS = {
    "agent_name": "agent_name",
    "portfolio_size": "portfolio_size",
    "total_units": "total_units",
    "score": "score",
    "boro": "primary_borough",
    "enrichment_status": "enrichment_status",
    "estimated_annual_revenue": "estimated_annual_revenue",
    "violations_per_unit": "violations_per_unit",
}

SORT_COL_EXPRESSIONS = {
    "units_per_bldg": "CASE WHEN portfolio_size > 0 THEN total_units::float / portfolio_size ELSE 0 END",
    "boro": "primary_borough",
}

LIVE_PORTFOLIO_COUNT_SQL = "COALESCE(live_link_stats.bldg_count, leads.portfolio_size, 0)"
LIVE_TOTAL_UNITS_SQL = "COALESCE(live_link_stats.unit_sum, leads.total_units, 0)"
LIVE_UNITS_PER_BUILDING_SQL = (
    f"CASE WHEN {LIVE_PORTFOLIO_COUNT_SQL} > 0 "
    f"THEN {LIVE_TOTAL_UNITS_SQL}::float / {LIVE_PORTFOLIO_COUNT_SQL} ELSE 0 END"
)


def _display_name_key_sql(alias: Optional[str] = None) -> str:
    prefix = f"{alias}." if alias else ""
    return f"""
UPPER(TRIM(COALESCE(
    NULLIF({prefix}company_name, ''),
    NULLIF({prefix}agent_name, ''),
    NULLIF({prefix}owner_name, ''),
    NULLIF({prefix}primary_contact, ''),
    {prefix}lead_id
)))
""".strip()

PIPELINE_STAGE_PRIORITY_SQL = """
CASE
    WHEN pipeline_stage = 'due_diligence' THEN 70
    WHEN pipeline_stage = 'loi' THEN 60
    WHEN pipeline_stage = 'meeting_done' THEN 50
    WHEN pipeline_stage = 'meeting_scheduled' THEN 40
    WHEN pipeline_stage = 'follow_up' THEN 30
    WHEN pipeline_stage = 'first_contact' THEN 20
    WHEN pipeline_stage = 'closed' THEN 15
    ELSE 10
END
""".strip()

def _parse_json_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _parse_json_object(value) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _lineage_status(record_count: int, missing_reason: Optional[str]) -> str:
    if record_count > 0:
        return "available"
    return "missing"


def _classify_lineage_for_canonical_prep(
    *,
    linked_buildings: int,
    blank_display_name: bool,
    sibling_rows: list[dict[str, object]],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if linked_buildings <= 0:
        reasons.append("no_active_building_links")
    if blank_display_name:
        reasons.append("blank_display_name")
    if sibling_rows:
        reasons.append("duplicate_sibling_candidates")

    if linked_buildings > 0 and not blank_display_name and not sibling_rows:
        return "safe_keep", reasons
    if linked_buildings <= 0 and blank_display_name and not sibling_rows:
        return "safe_retire", reasons
    if sibling_rows:
        return "review_required", reasons
    return "unresolved", reasons


def _split_csv_values(value: Optional[str]) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


async def _get_canonical_lineage_summary(session: AsyncSession, lead_id: str) -> dict[str, object]:
    membership_result = await session.execute(
        text("""
            SELECT
                ce.canonical_entity_id,
                ce.normalized_name,
                ce.display_name,
                ce.status,
                ce.confidence_score,
                cel.relationship_type,
                cel.is_primary,
                cel.confidence_score AS membership_confidence,
                cel.evidence
            FROM canonical_entity_leads cel
            JOIN canonical_entities ce ON ce.canonical_entity_id = cel.canonical_entity_id
            WHERE cel.lead_id = :lid
            ORDER BY cel.is_primary DESC, ce.updated_at DESC NULLS LAST, ce.canonical_entity_id ASC
        """),
        {"lid": lead_id},
    )
    memberships = []
    for row in membership_result:
        payload = dict(row._mapping)
        payload["is_primary"] = bool(payload.get("is_primary"))
        payload["evidence"] = _parse_json_object(payload.get("evidence"))
        memberships.append(payload)

    if not memberships:
        return {
            "primary_entity": None,
            "memberships": [],
            "aliases": [],
            "proposals": [],
        }

    primary_entity_id = memberships[0]["canonical_entity_id"]
    alias_result = await session.execute(
        text("""
            SELECT alias_name, normalized_alias, source, confidence_score
            FROM canonical_entity_aliases
            WHERE canonical_entity_id = :cid
            ORDER BY confidence_score DESC NULLS LAST, alias_name ASC
            LIMIT 15
        """),
        {"cid": primary_entity_id},
    )
    aliases = [dict(row._mapping) for row in alias_result]

    proposal_result = await session.execute(
        text("""
            SELECT bucket, proposal_status, safe_to_execute, reasons, evidence, updated_at
            FROM canonical_entity_match_proposals
            WHERE canonical_entity_id = :cid OR lead_id = :lid
            ORDER BY updated_at DESC NULLS LAST, id DESC
            LIMIT 15
        """),
        {"cid": primary_entity_id, "lid": lead_id},
    )
    proposals = []
    for row in proposal_result:
        proposal = dict(row._mapping)
        proposal["safe_to_execute"] = bool(proposal.get("safe_to_execute"))
        proposal["reasons"] = _parse_json_object(proposal.get("reasons"))
        proposal["evidence"] = _parse_json_object(proposal.get("evidence"))
        proposal["updated_at"] = proposal["updated_at"].isoformat() if proposal.get("updated_at") else None
        proposals.append(proposal)

    primary_entity = dict(memberships[0])
    primary_entity["aliases"] = aliases

    return {
        "primary_entity": primary_entity,
        "memberships": memberships,
        "aliases": aliases,
        "proposals": proposals,
    }


def _estimate_page_total(*, offset: int, returned_count: int, limit: int) -> int:
    return offset + returned_count + (1 if returned_count == limit else 0)


async def _run_simple_leads_query(
    session: AsyncSession,
    *,
    params: dict,
    sort_sql: str,
    sort_direction: str,
) -> dict[str, object]:
    simple_result = await session.execute(
        text(f"""
            SELECT {LEAD_LIST_SIMPLE_SELECT_SQL}
            FROM leads
            ORDER BY {sort_sql} {sort_direction} NULLS LAST, updated_at DESC NULLS LAST, lead_id ASC
            LIMIT :limit OFFSET :offset
        """),
        params,
    )
    leads = [_row_to_response(dict(r._mapping)) for r in simple_result]
    try:
        total = (
            await session.execute(text("SELECT COUNT(*) FROM leads"))
        ).scalar() or 0
    except DBAPIError as exc:
        logger.warning("Simple lead count fell back to estimated total: %s", exc)
        total = _estimate_page_total(offset=params["offset"], returned_count=len(leads), limit=params["limit"])
    return {"leads": leads, "total": total, "offset": params["offset"], "limit": params["limit"]}


def _row_to_response(r: dict) -> dict:
    """Convert a DB row dict to the LeadResponse shape the frontend expects."""
    def _to_list(value):
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, list) else []
            except Exception:
                return []
        return []

    def _to_dict(value):
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}

    breakdown = _to_dict(r.get("score_breakdown"))
    buildings = _to_list(r.get("buildings"))
    boros = _to_list(r.get("boros"))
    contacts = _to_list(r.get("contacts"))
    tags = _to_list(r.get("tags"))
    revenue_breakdown = _to_list(r.get("revenue_breakdown"))
    building_classes = _to_dict(r.get("building_classes"))
    building_types = _to_dict(r.get("building_types"))
    return {
        "lead_id": r["lead_id"],
        "agent_name": r.get("agent_name") or "",
        "owner_name": r.get("owner_name") or "",
        "owner_type": r.get("owner_type") or "unknown",
        "portfolio_size": r.get("live_portfolio_size") or r.get("portfolio_size") or 0,
        "total_units": r.get("live_total_units") or r.get("total_units") or 0,
        "buildings": buildings if isinstance(buildings, list) else [],
        "phone": r.get("phone"),
        "email": r.get("email"),
        "phones": [],
        "emails": [],
        "website": r.get("website"),
        "business_summary": (r.get("business_summary") or "") or None,
        "address": r.get("address"),
        "boro": r.get("primary_borough") or (boros[0] if boros else ""),
        "boros": boros if isinstance(boros, list) else [],
        "building_types": building_types if building_types else None,
        "building_classes": building_classes,
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
        "tags": tags,
        "enrichment_status": r.get("enrichment_status") or "none",
        "outreach_status": r.get("outreach_status") or "new",
        "notes": r.get("notes"),
        "outreach_attempts": [],
        "contacts": contacts,
        "entity_type": r.get("entity_type") or "unknown",
        "company_name": r.get("company_name"),
        "primary_contact": r.get("primary_contact"),
        "primary_contact_title": r.get("primary_contact_title"),
        "estimated_monthly_revenue": r.get("estimated_monthly_revenue") or 0.0,
        "estimated_annual_revenue": r.get("estimated_annual_revenue") or 0.0,
        "revenue_breakdown": revenue_breakdown if isinstance(revenue_breakdown, list) else None,
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


LEAD_LIST_SELECT_SQL = """
    lead_id,
    agent_name,
    owner_name,
    owner_type,
    live_portfolio_size,
    live_total_units,
    phone,
    email,
    website,
    business_summary,
    address,
    primary_borough,
    boros,
    score,
    enrichment_status,
    outreach_status,
    entity_type,
    company_name,
    primary_contact,
    primary_contact_title,
    estimated_monthly_revenue,
    estimated_annual_revenue,
    violation_count,
    violation_class_a,
    violation_class_b,
    violation_class_c,
    violations_per_unit,
    pipeline_stage,
    next_follow_up,
    priority_rank
""".strip()


LEAD_LIST_SIMPLE_SELECT_SQL = """
    lead_id,
    agent_name,
    owner_name,
    owner_type,
    portfolio_size AS live_portfolio_size,
    total_units AS live_total_units,
    phone,
    email,
    website,
    business_summary,
    address,
    primary_borough,
    boros,
    score,
    enrichment_status,
    outreach_status,
    entity_type,
    company_name,
    primary_contact,
    primary_contact_title,
    estimated_monthly_revenue,
    estimated_annual_revenue,
    violation_count,
    violation_class_a,
    violation_class_b,
    violation_class_c,
    violations_per_unit,
    pipeline_stage,
    next_follow_up,
    priority_rank
""".strip()


@router.get("/leads")
@limiter.limit("60/minute")
async def get_leads(
    request: Request,
    min_score: Optional[float] = Query(None),
    max_score: Optional[float] = Query(None, le=100),
    min_portfolio: Optional[int] = Query(None, le=10000),
    max_portfolio: Optional[int] = Query(None, le=10000),
    boro: Optional[str] = Query(None, max_length=100),
    neighborhood: Optional[str] = Query(None, max_length=120),
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
        wheres.append(f"{LIVE_PORTFOLIO_COUNT_SQL} >= :min_portfolio")
        params["min_portfolio"] = min_portfolio
    if max_portfolio is not None:
        wheres.append(f"{LIVE_PORTFOLIO_COUNT_SQL} <= :max_portfolio")
        params["max_portfolio"] = max_portfolio
    if boro:
        boro_list = [b.strip().upper() for b in boro.split(",") if b.strip()]
        if boro_list:
            boro_clauses: list[str] = []
            for i, value in enumerate(boro_list):
                key = f"boro_{i}"
                params[key] = value
                boro_clauses.append(f"""
                    primary_borough = :{key}
                    OR COALESCE(boros, '[]'::jsonb) ? :{key}
                    OR EXISTS (
                        SELECT 1
                        FROM building_management bm
                        JOIN buildings b ON b.bbl = bm.bbl
                        WHERE bm.lead_id = leads.lead_id
                          AND bm.is_current = true
                          AND UPPER(COALESCE(b.borough, '')) = :{key}
                    )
                """)
            wheres.append(f"({' OR '.join(f'({clause.strip()})' for clause in boro_clauses)})")
    if neighborhood:
        wheres.append("""
            EXISTS (
                SELECT 1
                FROM building_management bm
                JOIN buildings b ON b.bbl = bm.bbl
                WHERE bm.lead_id = leads.lead_id
                  AND bm.is_current = true
                  AND (
                      COALESCE(b.nta, '') ILIKE :neighborhood
                      OR COALESCE(b.community_board, '') ILIKE :neighborhood
                  )
            )
        """)
        params["neighborhood"] = f"%{neighborhood.strip()}%"
    if has_phone is not None:
        wheres.append("phone IS NOT NULL AND phone != ''" if has_phone else "(phone IS NULL OR phone = '')")
    if has_email is not None:
        wheres.append("email IS NOT NULL AND email != ''" if has_email else "(email IS NULL OR email = '')")
    if has_website is not None:
        wheres.append("website IS NOT NULL AND website != ''" if has_website else "(website IS NULL OR website = '')")
    entity_types = _split_csv_values(entity_type)
    if entity_types:
        placeholders = ", ".join(f":entity_type_{i}" for i in range(len(entity_types)))
        wheres.append(f"entity_type IN ({placeholders})")
        for i, value in enumerate(entity_types):
            params[f"entity_type_{i}"] = value
    enrichment_statuses = _split_csv_values(enrichment_status)
    if enrichment_statuses:
        placeholders = ", ".join(f":enrichment_status_{i}" for i in range(len(enrichment_statuses)))
        wheres.append(f"enrichment_status IN ({placeholders})")
        for i, value in enumerate(enrichment_statuses):
            params[f"enrichment_status_{i}"] = value
    outreach_statuses = _split_csv_values(outreach_status)
    if outreach_statuses:
        placeholders = ", ".join(f":outreach_status_{i}" for i in range(len(outreach_statuses)))
        wheres.append(f"outreach_status IN ({placeholders})")
        for i, value in enumerate(outreach_statuses):
            params[f"outreach_status_{i}"] = value
    pipeline_stages = _split_csv_values(pipeline_stage)
    if pipeline_stages:
        placeholders = ", ".join(f":pipeline_stage_{i}" for i in range(len(pipeline_stages)))
        wheres.append(f"pipeline_stage IN ({placeholders})")
        for i, value in enumerate(pipeline_stages):
            params[f"pipeline_stage_{i}"] = value
    if min_units is not None:
        wheres.append(f"{LIVE_TOTAL_UNITS_SQL} >= :min_units")
        params["min_units"] = min_units
    if max_units is not None:
        wheres.append(f"{LIVE_TOTAL_UNITS_SQL} <= :max_units")
        params["max_units"] = max_units
    if min_units_per_bldg is not None:
        wheres.append(f"{LIVE_PORTFOLIO_COUNT_SQL} > 0 AND ({LIVE_UNITS_PER_BUILDING_SQL}) >= :min_upb")
        params["min_upb"] = min_units_per_bldg
    if max_units_per_bldg is not None:
        wheres.append(f"{LIVE_PORTFOLIO_COUNT_SQL} > 0 AND ({LIVE_UNITS_PER_BUILDING_SQL}) <= :max_upb")
        params["max_upb"] = max_units_per_bldg
    if building_type_has:
        for i, btype in enumerate(building_type_has.split(",")):
            btype = btype.strip().lower()
            if btype:
                key = f"btype_{i}"
                wheres.append(f"""
                    (
                        COALESCE((building_types->>:{key})::int, 0) > 0
                        OR EXISTS (
                            SELECT 1
                            FROM building_management bm
                            JOIN buildings b ON b.bbl = bm.bbl
                            WHERE bm.lead_id = leads.lead_id
                              AND bm.is_current = true
                              AND LOWER(COALESCE(b.building_type, '')) = :{key}
                        )
                    )
                """)
                params[key] = btype
    if search:
        wheres.append("""
            (
                owner_name ILIKE :search
                OR company_name ILIKE :search
                OR agent_name ILIKE :search
                OR primary_contact ILIKE :search
                OR normalized_name ILIKE :search
                OR phone ILIKE :search
                OR email ILIKE :search
                OR website ILIKE :search
                OR EXISTS (
                    SELECT 1
                    FROM building_management bm
                    JOIN buildings b ON b.bbl = bm.bbl
                    WHERE bm.lead_id = leads.lead_id
                      AND bm.is_current = true
                      AND (
                        b.address ILIKE :search
                        OR CAST(b.bbl AS TEXT) ILIKE :search
                      )
                )
            )
        """)
        params["search"] = f"%{search}%"

    where_sql = " AND ".join(wheres) if wheres else "1=1"

    if sort_by == "portfolio_size":
        sort_col = "live_portfolio_size"
    elif sort_by == "total_units":
        sort_col = "live_total_units"
    elif sort_by == "units_per_bldg":
        sort_col = "live_units_per_bldg"
    elif sort_by in SORT_COL_EXPRESSIONS:
        sort_col = SORT_COL_EXPRESSIONS[sort_by]
    elif sort_by in ALLOWED_SORT_COLS:
        sort_col = sort_by
    else:
        sort_col = "score"
    sort_direction = "ASC" if sort_dir.lower() == "asc" else "DESC"
    simple_sort_col = SIMPLE_SORT_COLS.get(sort_by)

    if where_sql == "1=1" and simple_sort_col:
        return await _run_simple_leads_query(
            session,
            params=params,
            sort_sql=simple_sort_col,
            sort_direction=sort_direction,
        )

    dedupe_cte_sql = f"""
        WITH live_link_stats AS (
            SELECT bm.lead_id,
                   COUNT(DISTINCT bm.bbl) AS bldg_count,
                   COALESCE(SUM(b.unit_count), 0) AS unit_sum
            FROM building_management bm
            JOIN buildings b ON b.bbl = bm.bbl
            WHERE bm.is_current = true
            GROUP BY bm.lead_id
        ),
        filtered_leads AS (
            SELECT
                leads.*,
                {LIVE_PORTFOLIO_COUNT_SQL} AS live_portfolio_size,
                {LIVE_TOTAL_UNITS_SQL} AS live_total_units,
                {LIVE_UNITS_PER_BUILDING_SQL} AS live_units_per_bldg,
                {_display_name_key_sql("leads")} AS display_name_key
            FROM leads
            LEFT JOIN live_link_stats ON live_link_stats.lead_id = leads.lead_id
            WHERE {where_sql}
        ),
        ranked_leads AS (
            SELECT
                filtered_leads.*,
                ROW_NUMBER() OVER (
                    PARTITION BY CASE
                        WHEN display_name_key <> UPPER(TRIM(lead_id))
                         AND EXISTS (
                            SELECT 1
                            FROM filtered_leads dup
                            WHERE dup.lead_id <> filtered_leads.lead_id
                              AND dup.display_name_key = filtered_leads.display_name_key
                         )
                        THEN display_name_key
                        ELSE lead_id
                    END
                    ORDER BY
                        {PIPELINE_STAGE_PRIORITY_SQL} DESC,
                        CASE WHEN next_follow_up IS NOT NULL THEN 1 ELSE 0 END DESC,
                        COALESCE(priority_rank, 0) DESC,
                        CASE WHEN COALESCE(NULLIF(TRIM(notes), ''), '') <> '' THEN 1 ELSE 0 END DESC,
                        COALESCE(score, 0) DESC,
                        COALESCE(live_portfolio_size, 0) DESC,
                        updated_at DESC NULLS LAST,
                        lead_id ASC
                ) AS dedupe_rank
            FROM filtered_leads
        )
    """

    try:
        result = await session.execute(
            text(f"""
                {dedupe_cte_sql}
                SELECT {LEAD_LIST_SELECT_SQL}
                FROM ranked_leads
                WHERE dedupe_rank = 1
                ORDER BY {sort_col} {sort_direction} NULLS LAST
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
    except DBAPIError as exc:
        if where_sql == "1=1" and simple_sort_col:
            logger.warning(
                "Lead list rich query failed for unfiltered request; falling back to stored-field list: %s",
                exc,
            )
            return await _run_simple_leads_query(
                session,
                params=params,
                sort_sql=simple_sort_col,
                sort_direction=sort_direction,
            )
        raise
    leads = [_row_to_response(dict(r._mapping)) for r in result]
    try:
        count_result = await session.execute(
            text(f"{dedupe_cte_sql} SELECT COUNT(*) FROM ranked_leads WHERE dedupe_rank = 1"),
            params,
        )
        total = count_result.scalar() or 0
    except DBAPIError as exc:
        # Production can temporarily exhaust Postgres temp space on the exact count.
        # Return the current page with a conservative total estimate instead of failing.
        logger.warning("Lead count query fell back to estimated total: %s", exc)
        total = _estimate_page_total(offset=offset, returned_count=len(leads), limit=limit)
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

    lead_data = dict(row._mapping)

    portfolio_rows = await session.execute(
        text("""
            SELECT b.address, b.borough, b.unit_count, b.building_type
            FROM building_management bm
            JOIN buildings b ON b.bbl = bm.bbl
            WHERE bm.lead_id = :lid AND bm.is_current = true
            ORDER BY b.address
        """),
        {"lid": lead_id},
    )
    portfolio = [dict(r._mapping) for r in portfolio_rows]
    if not portfolio:
        raw_building_ids = lead_data.get("building_ids")
        building_ids = raw_building_ids if isinstance(raw_building_ids, list) else []
        if isinstance(raw_building_ids, str):
            try:
                parsed_ids = json.loads(raw_building_ids)
                building_ids = parsed_ids if isinstance(parsed_ids, list) else []
            except Exception:
                building_ids = []
        if building_ids:
            id_params = {f"bid_{i}": bid for i, bid in enumerate(building_ids)}
            placeholders = ", ".join(f":bid_{i}" for i in range(len(building_ids)))
            fallback_rows = await session.execute(
                text(f"""
                    SELECT address, borough, unit_count, building_type
                    FROM buildings
                    WHERE bbl IN ({placeholders})
                    ORDER BY address
                """),
                id_params,
            )
            portfolio = [dict(r._mapping) for r in fallback_rows]
    if not portfolio:
        normalized_name = str(lead_data.get("normalized_name") or "").strip()
        if normalized_name:
            # Some deduped/canonical leads do not carry direct building links, but
            # sibling lead_ids with the same normalized_name do. Hydrate from the
            # entity group to keep Lead Detail complete.
            grouped_rows = await session.execute(
                text("""
                    SELECT DISTINCT ON (b.bbl) b.address, b.borough, b.unit_count, b.building_type
                    FROM leads l
                    JOIN building_management bm ON bm.lead_id = l.lead_id AND bm.is_current = true
                    JOIN buildings b ON b.bbl = bm.bbl
                    WHERE l.normalized_name = :normalized_name
                    ORDER BY b.bbl, b.address
                """),
                {"normalized_name": normalized_name},
            )
            portfolio = [dict(r._mapping) for r in grouped_rows]
    if not portfolio:
        name_hint = (
            str(lead_data.get("agent_name") or "").strip()
            or str(lead_data.get("owner_name") or "").strip()
            or str(lead_data.get("company_name") or "").strip()
        )
        if len(name_hint) >= 5:
            # Last-resort hydration: infer current portfolio from HPD contact records
            # when legacy/entity links are missing for this lead row.
            contact_rows = await session.execute(
                text("""
                    SELECT DISTINCT ON (b.bbl) b.address, b.borough, b.unit_count, b.building_type
                    FROM building_contacts bc
                    JOIN buildings b ON b.bbl = bc.bbl
                    WHERE bc.contact_type IN ('Agent', 'ManagementCompany', 'CorporateOwner')
                      AND bc.corporation_name ILIKE :name_hint
                    ORDER BY b.bbl, b.address
                """),
                {"name_hint": f"%{name_hint}%"},
            )
            portfolio = [dict(r._mapping) for r in contact_rows]
    if portfolio:
        addresses = [p["address"] for p in portfolio if p.get("address")]
        linked_units = sum(int(p.get("unit_count") or 0) for p in portfolio)
        linked_boros = sorted({str(p.get("borough")).upper() for p in portfolio if p.get("borough")})
        existing_building_types = lead_data.get("building_types") or {}
        if isinstance(existing_building_types, str):
            try:
                parsed = json.loads(existing_building_types)
                existing_building_types = parsed if isinstance(parsed, dict) else {}
            except Exception:
                existing_building_types = {}
        if not isinstance(existing_building_types, dict):
            existing_building_types = {}
        building_types, type_units = summarize_portfolio_building_types(
            portfolio,
            total_buildings=len(addresses) or int(lead_data.get("portfolio_size") or 0),
        )

        if not lead_data.get("buildings"):
            lead_data["buildings"] = addresses
        lead_data["portfolio_size"] = len(addresses) or int(lead_data.get("portfolio_size") or 0)
        if linked_units > 0:
            lead_data["total_units"] = linked_units
        if linked_boros and not lead_data.get("boros"):
            lead_data["boros"] = linked_boros
        if linked_boros and not lead_data.get("primary_borough"):
            lead_data["primary_borough"] = linked_boros[0]
        lead_data["building_types"] = building_types
        lead_data["_type_units_for_revenue"] = type_units

        existing_buildings = lead_data.get("buildings") or []
        if isinstance(existing_buildings, str):
            try:
                parsed = json.loads(existing_buildings)
                existing_buildings = parsed if isinstance(parsed, list) else []
            except Exception:
                existing_buildings = []
        if not isinstance(existing_buildings, list):
            existing_buildings = []

        # Keep detail reads side-effect free; do not persist fallback-derived values here.

    annual_revenue = float(lead_data.get("estimated_annual_revenue") or 0.0)
    if annual_revenue <= 0 and int(lead_data.get("total_units") or 0) > 0:
        borough_avg_rents = {
            "MANHATTAN": 3500, "BROOKLYN": 2800, "QUEENS": 2200,
            "BRONX": 1800, "STATEN ISLAND": 1600,
        }
        boro = str(lead_data.get("primary_borough") or "").upper() or "MANHATTAN"
        units = int(lead_data.get("total_units") or 0)
        avg_rent = borough_avg_rents.get(boro, 2200)
        fee_rate = 0.05
        type_units = lead_data.get("_type_units_for_revenue") or {}
        if not isinstance(type_units, dict):
            type_units = {}
        type_units = {
            "condo": int(type_units.get("condo", 0)),
            "coop": int(type_units.get("coop", 0)),
            "rental_elevator": int(type_units.get("rental_elevator", 0)),
            "rental_walkup": int(type_units.get("rental_walkup", 0)),
            "small_residential": int(type_units.get("small_residential", 0)),
            "other": int(type_units.get("other", 0)),
        }
        # If building_types only has building counts (not units), fall back to one-line estimate.
        if sum(type_units.values()) <= 0:
            monthly_gross = float(units * avg_rent)
            monthly_revenue = monthly_gross * fee_rate
            annual_revenue = monthly_revenue * 12
            revenue_breakdown = [{
                "type": "portfolio",
                "label": f"{boro.title()} portfolio estimate",
                "buildings": int(lead_data.get("portfolio_size") or 0),
                "estimated_units": units,
                "rent_per_unit": avg_rent,
                "monthly_gross": monthly_gross,
                "fee_rate": fee_rate,
                "avg_rent_per_unit": avg_rent,
                "borough_used": boro,
                "total_units_used": units,
            }]
        else:
            rent_multiplier = {
                "condo": 0.60,
                "coop": 0.60,
                "rental_elevator": 1.05,
                "rental_walkup": 0.90,
                "small_residential": 0.90,
                "other": 1.00,
            }
            labels = {
                "condo": "Condo",
                "coop": "Co-op",
                "rental_elevator": "Rental (Elevator)",
                "rental_walkup": "Rental (Walkup)",
                "small_residential": "Small Residential",
                "other": "Other",
            }
            revenue_breakdown = []
            annual_revenue = 0.0
            for t, t_units in type_units.items():
                if t_units <= 0:
                    continue
                rent_per_unit = float(avg_rent * rent_multiplier.get(t, 1.0))
                monthly_gross = float(t_units * rent_per_unit)
                annual_revenue += monthly_gross * fee_rate * 12
                revenue_breakdown.append({
                    "type": t,
                    "label": f"{labels.get(t, t)} estimate",
                    "buildings": int((lead_data.get("building_types") or {}).get(t, 0)),
                    "estimated_units": t_units,
                    "rent_per_unit": rent_per_unit,
                    "monthly_gross": monthly_gross,
                    "fee_rate": fee_rate,
                    "avg_rent_per_unit": rent_per_unit,
                    "borough_used": boro,
                    "total_units_used": t_units,
                })
            monthly_revenue = annual_revenue / 12.0
        lead_data["estimated_monthly_revenue"] = monthly_revenue
        lead_data["estimated_annual_revenue"] = annual_revenue
        lead_data["revenue_breakdown"] = revenue_breakdown

    # Recompute violations from current linked buildings for detail accuracy.
    violations_row = (await session.execute(
        text("""
            SELECT
                COUNT(*)::int AS total,
                COUNT(*) FILTER (WHERE COALESCE(v.violation_class, '') ILIKE 'A%')::int AS class_a,
                COUNT(*) FILTER (WHERE COALESCE(v.violation_class, '') ILIKE 'B%')::int AS class_b,
                COUNT(*) FILTER (WHERE COALESCE(v.violation_class, '') ILIKE 'C%')::int AS class_c
            FROM building_management bm
            JOIN hpd_violations v ON v.bbl = bm.bbl
            WHERE bm.lead_id = :lid AND bm.is_current = true
        """),
        {"lid": lead_id},
    )).first()
    if violations_row:
        v_total = int(violations_row.total or 0)
        v_a = int(violations_row.class_a or 0)
        v_b = int(violations_row.class_b or 0)
        v_c = int(violations_row.class_c or 0)
        total_units = int(lead_data.get("total_units") or 0)
        vpu = float(v_total / total_units) if total_units > 0 else 0.0
        lead_data["violation_count"] = v_total
        lead_data["violation_class_a"] = v_a
        lead_data["violation_class_b"] = v_b
        lead_data["violation_class_c"] = v_c
        lead_data["violations_per_unit"] = vpu

    return _row_to_response(lead_data)


@router.get("/leads/{lead_id}/contacts")
@limiter.limit("60/minute")
async def get_lead_contacts_roster(
    request: Request,
    lead_id: str,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    exists = (await session.execute(
        text("SELECT 1 FROM leads WHERE lead_id = :lid"),
        {"lid": lead_id},
    )).first()
    if not exists:
        raise HTTPException(status_code=404, detail="Lead not found")

    buildings = await get_lead_contacts(session=session, lead_id=lead_id)
    return {
        "lead_id": lead_id,
        "buildings": buildings,
    }


@router.get("/leads/{lead_id}/lineage")
@limiter.limit("60/minute")
async def get_lead_lineage(
    request: Request,
    lead_id: str,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    lead_row = (await session.execute(
        text("""
            SELECT lead_id, company_name, agent_name, owner_name, primary_contact, updated_at, enrichment_sources
            FROM leads
            WHERE lead_id = :lid
        """),
        {"lid": lead_id},
    )).first()
    if not lead_row:
        raise HTTPException(status_code=404, detail="Lead not found")

    lead_data = dict(lead_row._mapping)
    display_name_key = str(
        lead_data.get("company_name")
        or lead_data.get("agent_name")
        or lead_data.get("owner_name")
        or lead_data.get("primary_contact")
        or lead_id
    ).strip().upper()
    linked_buildings = int((await session.execute(
        text("""
            SELECT COUNT(DISTINCT bbl)
            FROM building_management
            WHERE lead_id = :lid AND is_current = true
        """),
        {"lid": lead_id},
    )).scalar() or 0)

    building_scope_missing = "no linked buildings for lead" if linked_buildings == 0 else None

    source_rows = []
    sibling_rows = []

    def append_source(source_name: str, record_count: int, last_updated, mapped_fields: list[str], missing_reason: Optional[str]) -> None:
        source_rows.append({
            "source_name": source_name,
            "record_count": int(record_count or 0),
            "last_updated": last_updated.isoformat() if last_updated else None,
            "status": _lineage_status(int(record_count or 0), missing_reason),
            "mapped_fields": mapped_fields,
            "missing_reason": missing_reason if (record_count or 0) == 0 else None,
        })

    append_source(
        "leads",
        1,
        lead_data.get("updated_at"),
        ["score", "portfolio_size", "total_units", "pipeline_stage", "enrichment_status"],
        None,
    )

    building_link_row = (await session.execute(
        text("""
            SELECT COUNT(*)::int AS cnt, MAX(updated_at) AS last_updated
            FROM building_management
            WHERE lead_id = :lid AND is_current = true
        """),
        {"lid": lead_id},
    )).first()
    append_source(
        "building_management",
        int(building_link_row.cnt or 0) if building_link_row else 0,
        building_link_row.last_updated if building_link_row else None,
        ["bbl", "lead_id", "is_current"],
        "no active building links" if not building_link_row or int(building_link_row.cnt or 0) == 0 else None,
    )

    building_rows = (await session.execute(
        text("""
            SELECT COUNT(*)::int AS cnt, MAX(b.updated_at) AS last_updated
            FROM building_management bm
            JOIN buildings b ON b.bbl = bm.bbl
            WHERE bm.lead_id = :lid AND bm.is_current = true
        """),
        {"lid": lead_id},
    )).first()
    append_source(
        "buildings",
        int(building_rows.cnt or 0) if building_rows else 0,
        building_rows.last_updated if building_rows else None,
        ["address", "borough", "unit_count", "building_type", "churn_score"],
        building_scope_missing,
    )

    signal_tables = [
        ("hpd_complaints", "hpd_complaints", "received_date", ["major_category", "minor_category", "status"]),
        ("hpd_violations", "hpd_violations", "inspection_date", ["violation_class", "current_status"]),
        ("acris_transactions", "acris_transactions", "recorded_date", ["doc_type", "doc_amount"]),
        ("dob_permits", "dob_permits", "filing_date", ["permit_type", "job_description", "estimated_cost"]),
        ("hpd_litigation", "hpd_litigation", "case_open_date", ["case_type", "case_status", "penalty"]),
        ("emergency_repairs", "emergency_repairs", "order_date", ["repair_type", "amount", "status"]),
        ("eviction_filings", "eviction_filings", "executed_date", ["case_index_number", "borough"]),
        ("energy_grades", "energy_grades", "created_at", ["grade", "score", "year"]),
        ("facade_inspections", "facade_inspections", "filing_date", ["filing_status", "cycle"]),
        ("aep_designations", "aep_designations", "designation_date", ["is_active"]),
    ]
    for source_name, table_name, last_col, mapped_fields in signal_tables:
        row = (await session.execute(
            text(f"""
                SELECT COUNT(*)::int AS cnt, MAX(t.{last_col}) AS last_updated
                FROM building_management bm
                JOIN {table_name} t ON t.bbl = bm.bbl
                WHERE bm.lead_id = :lid AND bm.is_current = true
            """),
            {"lid": lead_id},
        )).first()
        count = int(row.cnt or 0) if row else 0
        append_source(source_name, count, row.last_updated if row else None, mapped_fields, building_scope_missing)

    enrichment_sources = _parse_json_list(lead_data.get("enrichment_sources"))
    append_source(
        "enrichment",
        len(enrichment_sources),
        lead_data.get("updated_at"),
        ["phone", "email", "website", "business_summary", "owner_principal"],
        "lead has not been enriched yet" if len(enrichment_sources) == 0 else None,
    )

    if display_name_key and display_name_key != lead_id.upper():
        sibling_result = await session.execute(
            text(f"""
                SELECT
                    lead_id,
                    COALESCE(NULLIF(company_name, ''), NULLIF(agent_name, ''), NULLIF(owner_name, ''), NULLIF(primary_contact, ''), lead_id) AS entity_name,
                    pipeline_stage,
                    outreach_status,
                    updated_at
                FROM leads
                WHERE lead_id <> :lid
                  AND {_display_name_key_sql("leads")} = :display_name_key
                ORDER BY updated_at DESC NULLS LAST, lead_id ASC
            """),
            {"lid": lead_id, "display_name_key": display_name_key},
        )
        sibling_rows = [
            {
                "lead_id": row.lead_id,
                "entity_name": row.entity_name,
                "pipeline_stage": row.pipeline_stage,
                "outreach_status": row.outreach_status,
                "last_updated": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in sibling_result
        ]

    blank_display_name = not any(
        str(lead_data.get(field) or "").strip()
        for field in ("company_name", "agent_name", "owner_name", "primary_contact")
    )
    canonical_bucket, canonical_reasons = _classify_lineage_for_canonical_prep(
        linked_buildings=linked_buildings,
        blank_display_name=blank_display_name,
        sibling_rows=sibling_rows,
    )
    canonical_lineage = await _get_canonical_lineage_summary(session, lead_id)

    return {
        "lead_id": lead_id,
        "entity_name": lead_data.get("company_name") or lead_data.get("agent_name") or lead_data.get("owner_name") or "",
        "last_updated": lead_data.get("updated_at").isoformat() if lead_data.get("updated_at") else None,
        "linked_buildings": linked_buildings,
        "sources": source_rows,
        "sibling_count": len(sibling_rows),
        "sibling_leads": sibling_rows,
        "canonical_entity": canonical_lineage["primary_entity"],
        "canonical_memberships": canonical_lineage["memberships"],
        "canonical_proposals": canonical_lineage["proposals"],
        "canonical_prep": {
            "candidate_bucket": canonical_bucket,
            "reasons": canonical_reasons,
            "blank_display_name": blank_display_name,
            "has_active_links": linked_buildings > 0,
            "display_name_key": display_name_key,
            "safe_to_execute": canonical_bucket == "safe_keep",
            "materialized_membership_count": len(canonical_lineage["memberships"]),
            "materialized_proposal_count": len(canonical_lineage["proposals"]),
        },
    }


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


@router.patch("/lead-batches/pipeline-stage")
@limiter.limit("20/minute")
async def update_leads_pipeline_stage_batch(
    request: Request,
    body: BatchPipelineStageUpdateRequest,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    valid_stages = {
        "research", "first_contact", "follow_up", "meeting_scheduled",
        "meeting_done", "loi", "due_diligence", "closed",
    }
    if body.pipeline_stage not in valid_stages:
        raise HTTPException(400, f"Invalid pipeline stage. Must be one of: {', '.join(valid_stages)}")

    lead_ids = [lead_id.strip() for lead_id in body.lead_ids if str(lead_id).strip()]
    if not lead_ids:
        raise HTTPException(400, "lead_ids must contain at least one lead id")

    placeholders = ", ".join(f":lid_{i}" for i in range(len(lead_ids)))
    params = {"ps": body.pipeline_stage}
    for i, lead_id in enumerate(lead_ids):
        params[f"lid_{i}"] = lead_id

    existing_rows = await session.execute(
        text(f"SELECT lead_id FROM leads WHERE lead_id IN ({placeholders})"),
        params,
    )
    found_ids = {str(row[0]) for row in existing_rows.fetchall()}
    missing_ids = [lead_id for lead_id in lead_ids if lead_id not in found_ids]
    if not found_ids:
        raise HTTPException(404, "No matching leads found")

    await session.execute(
        text(f"""
            UPDATE leads
            SET pipeline_stage = :ps,
                updated_at = NOW()
            WHERE lead_id IN ({placeholders})
        """),
        params,
    )
    await session.commit()

    return {
        "status": "success",
        "pipeline_stage": body.pipeline_stage,
        "updated_count": len(found_ids),
        "missing_lead_ids": missing_ids,
    }


@router.post("/lead-batches/enrichment")
@limiter.limit("10/minute")
async def start_selected_leads_enrichment(
    request: Request,
    body: EnrichmentRequest,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    lead_ids = [lead_id.strip() for lead_id in body.lead_ids if str(lead_id).strip()]
    if not lead_ids:
        raise HTTPException(400, "lead_ids must contain at least one lead id")

    deduped_lead_ids = list(dict.fromkeys(lead_ids))
    placeholders = ", ".join(f":lid_{i}" for i in range(len(deduped_lead_ids)))
    params = {f"lid_{i}": lead_id for i, lead_id in enumerate(deduped_lead_ids)}

    existing_rows = await session.execute(
        text(f"SELECT lead_id FROM leads WHERE lead_id IN ({placeholders})"),
        params,
    )
    found_ids = {str(row[0]) for row in existing_rows.fetchall()}
    missing_ids = [lead_id for lead_id in deduped_lead_ids if lead_id not in found_ids]
    selected_lead_ids = [lead_id for lead_id in deduped_lead_ids if lead_id in found_ids]
    if not selected_lead_ids:
        raise HTTPException(404, "No matching leads found")

    job_result = await session.execute(
        text("""INSERT INTO ingestion_jobs (job_type, source, status, started_at, created_at, updated_at)
                VALUES ('enrichment', 'enrichment_selected', 'queued', now(), now(), now()) RETURNING id""")
    )
    job_id = job_result.scalar_one()
    await session.commit()

    dispatch_mode = "celery"
    try:
        from src.tasks.enrich import run_enrichment_job
        run_enrichment_job.delay(job_id=job_id, limit=len(selected_lead_ids), lead_ids=selected_lead_ids)
    except Exception as exc:
        logger.warning(
            "Celery dispatch failed for selected enrichment job %s, falling back to in-process execution: %s",
            job_id,
            exc,
        )
        from src.tasks.enrich import _run_enrichment_job_async
        asyncio.create_task(
            _run_enrichment_job_async(job_id=job_id, limit=len(selected_lead_ids), lead_ids=selected_lead_ids)
        )
        dispatch_mode = "in_process"

    return {
        "status": "queued",
        "job_id": job_id,
        "target_count": len(selected_lead_ids),
        "missing_lead_ids": missing_ids,
        "dispatch_mode": dispatch_mode,
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
    return await enrich_lead_all_core(lead_id=lead_id, session=session)


async def enrich_lead_all_core(lead_id: str, session: AsyncSession):
    """Core enrichment workflow reusable by API routes and background workers."""
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
    dos_status = (str(dos_status).strip()[:20] if dos_status else None)
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
            enrichment_sources = CAST(:enrichment_sources AS JSONB),
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
        WHERE job_type = 'buildings' AND status IN ('succeeded', 'completed')
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
