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


@router.post("/admin/recalculate-categories")
async def recalculate_categories(
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """Recalculate churn_category in batches (35/15 thresholds). Returns immediately."""
    import threading
    from src.db.session import get_sync_url

    def _run():
        from sqlalchemy import create_engine, text as sa_text
        from sqlalchemy.orm import Session as SyncSession
        try:
            engine = create_engine(get_sync_url())
            s = SyncSession(engine)
            batch = 20000
            for i in range(0, 200000, batch):
                s.execute(sa_text(f"""
                    UPDATE buildings SET churn_category = CASE
                        WHEN churn_score >= 35 THEN 'hot'
                        WHEN churn_score >= 15 THEN 'warm'
                        ELSE 'stable' END
                    WHERE bbl IN (
                        SELECT bbl FROM buildings WHERE churn_score IS NOT NULL
                        ORDER BY bbl LIMIT {batch} OFFSET {i}
                    )
                """))
                s.commit()
            s.close()
            engine.dispose()
        except Exception as e:
            logger.error(f"Category recalculation failed: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started", "message": "Recalculating categories in background"}


@router.post("/admin/recompute-lead-units")
async def recompute_lead_units(
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """Recompute total_units for all leads from building_contacts + buildings,
    then recalculate scores using the updated unit counts.

    Uses the same normalization as generate_leads_from_buildings.py:
    lead.normalized_name = UPPER(TRIM(contact_name)).
    """
    result = await session.execute(text("""
        UPDATE leads l
        SET total_units = sub.units, updated_at = NOW()
        FROM (
            SELECT norm_name, COALESCE(SUM(unit_count), 0) AS units
            FROM (
                SELECT DISTINCT
                    UPPER(TRIM(
                        CASE WHEN bc.corporation_name IS NOT NULL AND TRIM(bc.corporation_name) != ''
                             THEN bc.corporation_name
                             ELSE CONCAT(COALESCE(bc.first_name,''), ' ', COALESCE(bc.last_name,''))
                        END
                    )) AS norm_name,
                    bc.bbl,
                    b.unit_count
                FROM building_contacts bc
                JOIN buildings b ON bc.bbl = b.bbl
                WHERE bc.contact_type IN ('Agent','Owner','CorporateOwner','IndividualOwner',
                                          'HeadOfficer','Officer','Shareholder')
            ) deduped
            GROUP BY norm_name
        ) sub
        WHERE l.normalized_name = sub.norm_name
          AND sub.units > 0
    """))
    await session.commit()
    units_updated = result.rowcount

    score_result = await session.execute(text("""
        UPDATE leads SET
            score = LEAST(100.0, ROUND((
                CASE
                    WHEN portfolio_size >= 50 THEN 40
                    WHEN portfolio_size >= 20 THEN 30
                    WHEN portfolio_size >= 10 THEN 20
                    WHEN portfolio_size >= 5 THEN 10
                    ELSE portfolio_size * 2
                END
                +
                CASE
                    WHEN COALESCE(total_units, 0) >= 500 THEN 30
                    WHEN COALESCE(total_units, 0) >= 200 THEN 20
                    WHEN COALESCE(total_units, 0) >= 100 THEN 15
                    WHEN COALESCE(total_units, 0) >= 50 THEN 10
                    ELSE LEAST(COALESCE(total_units, 0) / 5.0, 10)
                END
                +
                CASE WHEN COALESCE(violation_count, 0) > 0 AND COALESCE(total_units, 0) > 0
                     THEN LEAST(COALESCE(violation_count, 0)::float / total_units * 10, 30)
                     ELSE 0
                END
            )::numeric, 2)),
            updated_at = NOW()
        WHERE total_units > 0
    """))
    await session.commit()
    scores_updated = score_result.rowcount

    sample = await session.execute(text("""
        SELECT normalized_name, total_units, portfolio_size
        FROM leads WHERE total_units > 0
        ORDER BY total_units DESC LIMIT 5
    """))
    top = [{"name": r[0], "units": r[1], "buildings": r[2]} for r in sample]

    stats = await session.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE total_units > 0) AS with_units,
            COUNT(*) FILTER (WHERE total_units = 0 OR total_units IS NULL) AS without_units,
            MAX(total_units) AS max_units,
            ROUND(AVG(CASE WHEN total_units > 0 THEN total_units END)::numeric, 1) AS avg_units
        FROM leads
    """))
    s = stats.first()

    return {
        "units_updated": units_updated,
        "scores_updated": scores_updated,
        "with_units": s[0],
        "without_units": s[1],
        "max_units": s[2],
        "avg_units": float(s[3]) if s[3] else 0,
        "top_leads": top,
    }


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
