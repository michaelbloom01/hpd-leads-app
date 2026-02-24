"""Buildings API router.

The PM Operator's primary workspace: find buildings with high churn
probability, understand why, and do outreach.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_session
from src.auth.auth import AuthUser, get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/buildings", tags=["buildings"])


@router.get("")
async def list_buildings(
    borough: Optional[str] = None,
    building_type: Optional[str] = None,
    min_units: Optional[int] = None,
    max_units: Optional[int] = None,
    min_churn: Optional[float] = None,
    max_churn: Optional[float] = None,
    churn_category: Optional[str] = None,
    outreach_status: Optional[str] = None,
    lead_id: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: str = "churn_score",
    sort_dir: str = "desc",
    limit: int = Query(default=50, le=500),
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    wheres = []
    params: dict = {"limit": limit, "offset": offset}

    if borough:
        wheres.append("b.borough = :borough")
        params["borough"] = borough
    if building_type:
        wheres.append("b.building_type = :btype")
        params["btype"] = building_type
    if min_units is not None:
        wheres.append("b.unit_count >= :min_units")
        params["min_units"] = min_units
    if max_units is not None:
        wheres.append("b.unit_count <= :max_units")
        params["max_units"] = max_units
    if min_churn is not None:
        wheres.append("b.churn_score >= :min_churn")
        params["min_churn"] = min_churn
    if max_churn is not None:
        wheres.append("b.churn_score <= :max_churn")
        params["max_churn"] = max_churn
    if churn_category:
        wheres.append("b.churn_category = :category")
        params["category"] = churn_category
    if lead_id:
        wheres.append("EXISTS (SELECT 1 FROM building_management bm WHERE bm.bbl = b.bbl AND bm.lead_id = :lead_id AND bm.is_current)")
        params["lead_id"] = lead_id
    if outreach_status:
        if outreach_status == "in_pipeline":
            wheres.append("b.outreach_status != 'none'")
        else:
            wheres.append("b.outreach_status = :ostatus")
            params["ostatus"] = outreach_status
    if search:
        wheres.append("(b.address ILIKE :search OR b.bbl ILIKE :search)")
        params["search"] = f"%{search}%"

    where_sql = " AND ".join(wheres) if wheres else "1=1"

    allowed_sorts = {"churn_score", "unit_count", "borough", "address", "assessed_value"}
    sort_col = sort_by if sort_by in allowed_sorts else "churn_score"
    direction = "ASC" if sort_dir.lower() == "asc" else "DESC"

    count_result = await session.execute(
        text(f"SELECT COUNT(*) FROM buildings b WHERE {where_sql}"), params
    )
    total = count_result.scalar()

    result = await session.execute(
        text(f"""
            SELECT b.bbl, b.address, b.borough, b.unit_count, b.building_type,
                   b.churn_score, b.churn_category, b.key_signal,
                   b.coverage_ratio, b.outreach_status, b.last_scored_at,
                   bm.lead_id AS current_lead_id
            FROM buildings b
            LEFT JOIN building_management bm ON bm.bbl = b.bbl AND bm.is_current = true
            WHERE {where_sql}
            ORDER BY b.{sort_col} {direction} NULLS LAST
            LIMIT :limit OFFSET :offset
        """),
        params,
    )
    buildings = [dict(r._mapping) for r in result]
    return {"buildings": buildings, "total": total, "limit": limit, "offset": offset}


@router.get("/stats")
async def building_stats(session: AsyncSession = Depends(get_session)):
    result = await session.execute(text("""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE churn_category = 'hot') AS hot,
            COUNT(*) FILTER (WHERE churn_category = 'warm') AS warm,
            COUNT(*) FILTER (WHERE churn_category = 'stable') AS stable,
            ROUND(AVG(churn_score)::numeric, 1) AS avg_score,
            COUNT(*) FILTER (WHERE churn_score IS NOT NULL) AS scored
        FROM buildings
    """))
    row = result.first()
    return {
        "total": row[0], "hot": row[1], "warm": row[2], "stable": row[3],
        "avg_score": float(row[4]) if row[4] else 0, "scored": row[5],
    }


@router.get("/hot")
async def hot_buildings(
    limit: int = Query(default=20, le=100),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        text("""
            SELECT b.bbl, b.address, b.borough, b.unit_count,
                   b.churn_score, b.churn_category, b.key_signal
            FROM buildings b
            WHERE b.churn_score IS NOT NULL
            ORDER BY b.churn_score DESC
            LIMIT :limit
        """),
        {"limit": limit},
    )
    return [dict(r._mapping) for r in result]


@router.get("/{bbl}")
async def get_building(bbl: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        text("""
            SELECT b.*, bm.lead_id AS current_lead_id
            FROM buildings b
            LEFT JOIN building_management bm ON bm.bbl = b.bbl AND bm.is_current = true
            WHERE b.bbl = :bbl
        """),
        {"bbl": bbl},
    )
    row = result.first()
    if not row:
        raise HTTPException(404, "Building not found")
    return dict(row._mapping)


@router.get("/{bbl}/timeline")
async def building_timeline(bbl: str, session: AsyncSession = Depends(get_session)):
    """Chronological event feed merging all signal sources."""
    events = []

    complaints = await session.execute(
        text("SELECT 'complaint' AS type, received_date AS date, major_category AS detail FROM hpd_complaints WHERE bbl = :bbl ORDER BY received_date DESC LIMIT 50"),
        {"bbl": bbl},
    )
    events.extend([dict(r._mapping) for r in complaints])

    violations = await session.execute(
        text("SELECT 'violation' AS type, inspection_date AS date, nov_description AS detail FROM hpd_violations WHERE bbl = :bbl ORDER BY inspection_date DESC LIMIT 50"),
        {"bbl": bbl},
    )
    events.extend([dict(r._mapping) for r in violations])

    transactions = await session.execute(
        text("SELECT 'transaction' AS type, recorded_date AS date, doc_type_description AS detail FROM acris_transactions WHERE bbl = :bbl ORDER BY recorded_date DESC LIMIT 20"),
        {"bbl": bbl},
    )
    events.extend([dict(r._mapping) for r in transactions])

    permits = await session.execute(
        text("SELECT 'permit' AS type, filing_date AS date, job_description AS detail FROM dob_permits WHERE bbl = :bbl ORDER BY filing_date DESC LIMIT 20"),
        {"bbl": bbl},
    )
    events.extend([dict(r._mapping) for r in permits])

    litigation = await session.execute(
        text("SELECT 'litigation' AS type, case_open_date AS date, case_type AS detail FROM hpd_litigation WHERE bbl = :bbl ORDER BY case_open_date DESC LIMIT 20"),
        {"bbl": bbl},
    )
    events.extend([dict(r._mapping) for r in litigation])

    events.sort(key=lambda e: str(e.get("date") or ""), reverse=True)
    return events


@router.get("/{bbl}/score-history")
async def building_score_history(bbl: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        text("""
            SELECT churn_score, churn_category, churn_breakdown, scored_at
            FROM building_score_history
            WHERE bbl = :bbl
            ORDER BY scored_at DESC
            LIMIT 52
        """),
        {"bbl": bbl},
    )
    return [dict(r._mapping) for r in result]


@router.post("/{bbl}/pipeline")
async def add_to_pipeline(
    bbl: str,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    result = await session.execute(
        text("SELECT 1 FROM buildings WHERE bbl = :bbl"), {"bbl": bbl}
    )
    if not result.first():
        raise HTTPException(404, "Building not found")

    await session.execute(
        text("UPDATE buildings SET outreach_status = 'pipeline', updated_at = now() WHERE bbl = :bbl"),
        {"bbl": bbl},
    )
    return {"bbl": bbl, "status": "added_to_pipeline"}


@router.patch("/{bbl}/pipeline")
async def update_pipeline_status(
    bbl: str,
    outreach_status: str,
    outreach_priority: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """Update a building's outreach status and priority."""
    valid = {"none", "pipeline", "contacted", "meeting", "won", "lost"}
    if outreach_status not in valid:
        raise HTTPException(400, f"Invalid outreach_status. Must be one of: {', '.join(valid)}")
    result = await session.execute(
        text("SELECT 1 FROM buildings WHERE bbl = :bbl"), {"bbl": bbl}
    )
    if not result.first():
        raise HTTPException(404, "Building not found")

    sets = ["outreach_status = :os", "updated_at = now()"]
    params: dict = {"bbl": bbl, "os": outreach_status}
    if outreach_priority is not None:
        sets.append("outreach_priority = :op")
        params["op"] = outreach_priority
    await session.execute(
        text(f"UPDATE buildings SET {', '.join(sets)} WHERE bbl = :bbl"), params
    )
    return {"bbl": bbl, "outreach_status": outreach_status}


@router.post("/{bbl}/outreach-event")
async def log_building_outreach_event(
    bbl: str,
    stage: str,
    method: Optional[str] = None,
    outcome: Optional[str] = None,
    notes: Optional[str] = None,
    next_follow_up: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """Log an outreach event for a building."""
    from datetime import datetime, timezone
    exists = (await session.execute(
        text("SELECT 1 FROM buildings WHERE bbl = :bbl"), {"bbl": bbl}
    )).first()
    if not exists:
        raise HTTPException(404, "Building not found")

    await session.execute(
        text("""
            INSERT INTO outreach_events (bbl, stage, method, outcome, notes, next_follow_up, event_timestamp, created_at, updated_at)
            VALUES (:bbl, :stage, :method, :outcome, :notes, :nfu, :ts, NOW(), NOW())
        """),
        {
            "bbl": bbl, "stage": stage, "method": method,
            "outcome": outcome, "notes": notes, "nfu": next_follow_up,
            "ts": datetime.now(timezone.utc),
        },
    )

    update_sql = "UPDATE buildings SET outreach_status = :os, updated_at = now()"
    update_params: dict = {"bbl": bbl, "os": stage}
    if next_follow_up:
        update_sql += ", next_outreach_date = :nod"
        update_params["nod"] = next_follow_up
    update_sql += " WHERE bbl = :bbl"
    await session.execute(text(update_sql), update_params)

    return {"status": "success", "bbl": bbl, "stage": stage}


@router.get("/{bbl}/outreach-events")
async def get_building_outreach_events(
    bbl: str,
    session: AsyncSession = Depends(get_session),
):
    """Get outreach event history for a building."""
    result = await session.execute(
        text("""
            SELECT id, bbl, stage, method, outcome, notes, next_follow_up,
                   event_timestamp, created_at
            FROM outreach_events
            WHERE bbl = :bbl
            ORDER BY event_timestamp DESC
        """),
        {"bbl": bbl},
    )
    events = [dict(r._mapping) for r in result]
    for e in events:
        for k in ("event_timestamp", "created_at", "next_follow_up"):
            if e.get(k) and hasattr(e[k], "isoformat"):
                e[k] = e[k].isoformat()
    return {"bbl": bbl, "events": events}
