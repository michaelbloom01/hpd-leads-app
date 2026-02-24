"""Admin routes — health checks, building search.

Migrated to PostgreSQL (AsyncSession).
"""
import re
import time
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_session
from src.auth.auth import AuthUser, get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["admin"])


@router.get("/health")
async def health_check(session: AsyncSession = Depends(get_session)):
    lead_count = (await session.execute(text("SELECT COUNT(*) FROM leads"))).scalar() or 0
    building_count = (await session.execute(text("SELECT COUNT(*) FROM buildings"))).scalar() or 0
    return {
        "status": "ok",
        "leads_in_db": lead_count,
        "buildings_in_db": building_count,
    }


@router.get("/health/detailed")
async def health_detailed(
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    start = time.time()
    lead_count = (await session.execute(text("SELECT COUNT(*) FROM leads"))).scalar() or 0
    building_count = (await session.execute(text("SELECT COUNT(*) FROM buildings"))).scalar() or 0

    enrich_result = await session.execute(text("""
        SELECT enrichment_status, COUNT(*) AS cnt FROM leads GROUP BY enrichment_status
    """))
    by_status = {r[0] or "none": r[1] for r in enrich_result}

    contact_result = await session.execute(text("""
        SELECT
            COALESCE(SUM(CASE WHEN phone IS NOT NULL AND phone != '' THEN 1 ELSE 0 END), 0) AS with_phone,
            COALESCE(SUM(CASE WHEN email IS NOT NULL AND email != '' THEN 1 ELSE 0 END), 0) AS with_email,
            COALESCE(SUM(CASE WHEN website IS NOT NULL AND website != '' THEN 1 ELSE 0 END), 0) AS with_website
        FROM leads
    """))
    contacts = dict(contact_result.first()._mapping)

    follow_up_count = (await session.execute(text(
        "SELECT COUNT(*) FROM leads WHERE next_follow_up IS NOT NULL AND next_follow_up <= CURRENT_DATE"
    ))).scalar() or 0

    alert_count = 0
    try:
        alert_count = (await session.execute(text(
            "SELECT COUNT(*) FROM change_alerts WHERE dismissed = false"
        ))).scalar() or 0
    except Exception:
        await session.rollback()

    return {
        "status": "healthy",
        "uptime_check_ms": round((time.time() - start) * 1000, 1),
        "database": {"total_leads": lead_count, "total_buildings": building_count},
        "enrichment": {
            "by_status": by_status,
            "with_phone": contacts["with_phone"],
            "with_email": contacts["with_email"],
            "with_website": contacts["with_website"],
        },
        "pipeline": {"follow_ups_due": follow_up_count, "recent_alerts": alert_count},
    }


def _normalize_address_for_search(query: str) -> list[str]:
    q = query.strip().upper()
    q = re.sub(r'\b(\d+)(?:ST|ND|RD|TH)\b', r'\1', q)
    _dir_map = {"E": "EAST", "W": "WEST", "N": "NORTH", "S": "SOUTH"}
    _dir_reverse = {v: k for k, v in _dir_map.items()}
    _type_map = {
        "ST": "STREET", "AVE": "AVENUE", "AV": "AVENUE",
        "BLVD": "BOULEVARD", "DR": "DRIVE", "PL": "PLACE",
        "CT": "COURT", "LN": "LANE", "RD": "ROAD",
        "PKWY": "PARKWAY", "HWY": "HIGHWAY", "SQ": "SQUARE",
        "TER": "TERRACE", "CIR": "CIRCLE",
    }
    _type_reverse = {v: k for k, v in _type_map.items()}

    def _expand(txt: str) -> set[str]:
        variants = {txt}
        words = txt.split()
        for i, w in enumerate(words):
            swaps: list[str] = []
            if w in _dir_map:
                swaps.append(_dir_map[w])
            if w in _dir_reverse:
                swaps.append(_dir_reverse[w])
            if w in _type_map:
                swaps.append(_type_map[w])
            if w in _type_reverse:
                swaps.append(_type_reverse[w])
            for s in swaps:
                new_words = words[:i] + [s] + words[i + 1:]
                variants.add(" ".join(new_words))
        return variants

    patterns = set()
    for v in _expand(q):
        patterns.add(f"%{v}%")
    return list(patterns)


@router.get("/buildings/search")
async def search_buildings(
    address: str = Query(..., min_length=3),
    limit: int = Query(20, le=100),
    session: AsyncSession = Depends(get_session),
):
    """Search buildings by address with fuzzy normalization."""
    patterns = _normalize_address_for_search(address)
    like_clauses = " OR ".join([f"b.address ILIKE :p{i}" for i in range(len(patterns))])
    params = {f"p{i}": p for i, p in enumerate(patterns)}
    params["limit"] = limit

    result = await session.execute(
        text(f"""
            SELECT b.bbl, b.address, b.borough, b.unit_count, b.building_class,
                   b.year_built, b.churn_score
            FROM buildings b
            WHERE ({like_clauses})
            ORDER BY b.churn_score DESC NULLS LAST
            LIMIT :limit
        """),
        params,
    )
    rows = [dict(r._mapping) for r in result]
    buildings = []
    for r in rows:
        addr = r.get("address") or ""
        parts = addr.split(" ", 1)
        buildings.append({
            "building_id": r["bbl"],
            "address": addr,
            "house_number": parts[0] if len(parts) > 1 else "",
            "street_name": parts[1] if len(parts) > 1 else addr,
            "boro": r.get("borough") or "",
            "units_res": r.get("unit_count") or 0,
            "building_class": r.get("building_class") or "",
            "year_built": r.get("year_built"),
            "churn_score": r.get("churn_score"),
        })
    return {"query": address, "buildings": buildings, "total": len(buildings)}
