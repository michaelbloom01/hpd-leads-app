"""Admin routes — health checks, building search.

Migrated to PostgreSQL (AsyncSession).
"""
import asyncio
import time
import logging

from fastapi import APIRouter, Depends, Query, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_session
from src.auth.auth import AuthUser, get_current_admin, get_current_user
from src.services.address_aliases import (
    address_alias_search_sql,
    address_alias_table_exists,
    address_search_patterns,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["admin"])
limiter = Limiter(key_func=get_remote_address)


def _run_sync_admin_job(fn, *args, **kwargs):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as SyncSession
    from src.db.session import get_compatible_sync_url

    engine = create_engine(get_compatible_sync_url())
    session = SyncSession(engine)
    try:
        result = fn(session, *args, **kwargs)
        session.commit()
        return result
    finally:
        session.close()
        engine.dispose()


async def sync_lead_portfolio_snapshot(session: AsyncSession) -> dict:
    """
    Sync lead portfolio_size and total_units from live building_management links.

    This repairs stale lead snapshots that can undercount managed buildings and units.
    """
    update_result = await session.execute(text("""
        UPDATE leads l
        SET portfolio_size = sub.bldg_count,
            total_units = sub.unit_sum,
            updated_at = NOW()
        FROM (
            SELECT bm.lead_id,
                   COUNT(DISTINCT bm.bbl) AS bldg_count,
                   COALESCE(SUM(b.unit_count), 0) AS unit_sum
            FROM building_management bm
            JOIN buildings b ON b.bbl = bm.bbl
            WHERE bm.is_current = true
            GROUP BY bm.lead_id
        ) sub
        WHERE l.lead_id = sub.lead_id
          AND (
                COALESCE(l.portfolio_size, 0) <> sub.bldg_count
             OR COALESCE(l.total_units, 0) <> sub.unit_sum
          )
    """))
    await session.commit()

    stats_result = await session.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE portfolio_size >= 5) AS leads_5_plus_buildings,
            COUNT(*) FILTER (WHERE total_units >= 50) AS leads_50_plus_units,
            COUNT(*) FILTER (WHERE portfolio_size >= 5 AND total_units >= 50) AS leads_50_units_5_buildings
        FROM leads
    """))
    stats = stats_result.first()
    return {
        "rows_updated": int(update_result.rowcount or 0),
        "leads_5_plus_buildings": int(stats[0] or 0),
        "leads_50_plus_units": int(stats[1] or 0),
        "leads_50_units_5_buildings": int(stats[2] or 0),
    }


@router.post("/admin/recalculate-categories")
@limiter.limit("20/minute")
async def recalculate_categories(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_admin),
):
    """Recalculate churn_category in batches (70/40 thresholds). Returns immediately."""
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
                        WHEN churn_score >= 70 THEN 'hot'
                        WHEN churn_score >= 40 THEN 'warm'
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


@router.post("/admin/recompute-lead-portfolio")
@limiter.limit("20/minute")
async def recompute_lead_portfolio(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_admin),
):
    """Recompute lead portfolio_size and total_units from building_management + buildings."""
    snapshot = await sync_lead_portfolio_snapshot(session)
    return {
        "status": "ok",
        **snapshot,
    }


@router.post("/admin/lead-cleanup/preview")
@limiter.limit("20/minute")
async def preview_lead_cleanup(
    request: Request,
    sample_limit: int = Query(default=10, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_admin),
):
    """Preview conservative orphan-lead cleanup cohorts."""
    del session, user
    from src.services.lead_cleanup import preview_orphan_lead_cleanup

    return {
        "status": "ok",
        **await asyncio.to_thread(preview_orphan_lead_cleanup, sample_limit),
    }


@router.post("/admin/lead-cleanup/orphans")
@limiter.limit("10/minute")
async def cleanup_orphan_leads(
    request: Request,
    batch_size: int = Query(default=500, ge=50, le=5000),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_admin),
):
    """Migrate state from zero-link orphan leads into a clear keeper, then retire them."""
    del session, user
    from src.services.lead_cleanup import execute_orphan_lead_cleanup

    return {
        "status": "ok",
        **await asyncio.to_thread(execute_orphan_lead_cleanup, batch_size),
    }


@router.post("/admin/canonical-prep/preview")
@limiter.limit("20/minute")
async def preview_canonical_prep(
    request: Request,
    sample_limit: int = Query(default=10, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_admin),
):
    """Preview canonical-prep confidence buckets without mutating lead/building links."""
    del session, user
    from src.tasks.entity_resolution import preview_entity_resolution

    return {
        "status": "ok",
        **await asyncio.to_thread(preview_entity_resolution, sample_limit),
    }


@router.post("/admin/canonical-prep/materialize")
@limiter.limit("10/minute")
async def materialize_canonical_prep(
    request: Request,
    sample_limit: int = Query(default=10, ge=1, le=50),
    conservative_mode: bool = Query(default=True),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_admin),
):
    """Persist canonical entity proposal rows without mutating live lead/building links."""
    del session, user
    from src.tasks.entity_resolution import materialize_canonical_proposals

    return {
        "status": "ok",
        **await asyncio.to_thread(materialize_canonical_proposals, sample_limit, conservative_mode),
    }


@router.post("/admin/current-link-conflicts/preview")
@limiter.limit("20/minute")
async def preview_current_link_conflict_cleanup(
    request: Request,
    sample_limit: int = Query(default=10, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_admin),
):
    """Preview duplicate current-link cleanup and remaining multi-lead conflicts."""
    del session, user
    from src.services.building_links import preview_current_link_conflicts

    return {
        "status": "ok",
        **await asyncio.to_thread(_run_sync_admin_job, preview_current_link_conflicts, sample_limit=sample_limit),
    }


@router.post("/admin/current-link-conflicts/cleanup")
@limiter.limit("10/minute")
async def cleanup_current_link_conflicts(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_admin),
):
    """Delete exact duplicate active link rows and enforce exact-link uniqueness."""
    del session, user
    from src.services.building_links import cleanup_current_link_duplicates

    return {
        "status": "ok",
        **await asyncio.to_thread(_run_sync_admin_job, cleanup_current_link_duplicates),
    }


@router.post("/admin/recompute-lead-units")
@limiter.limit("20/minute")
async def recompute_lead_units(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_admin),
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
@limiter.limit("20/minute")
async def health_check(request: Request, session: AsyncSession = Depends(get_session)):
    lead_count = (await session.execute(text("SELECT COUNT(*) FROM leads"))).scalar() or 0
    building_count = (await session.execute(text("SELECT COUNT(*) FROM buildings"))).scalar() or 0
    return {
        "status": "ok",
        "leads_in_db": lead_count,
        "buildings_in_db": building_count,
    }


@router.get("/health/detailed")
@limiter.limit("20/minute")
async def health_detailed(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_admin),
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


@router.get("/admin/contacts-reconciliation")
@limiter.limit("20/minute")
async def contacts_reconciliation(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_admin),
):
    """Report contact-linkage and cache freshness issues for monitoring."""
    buildings_multi_pm = (await session.execute(text("""
        SELECT COUNT(*) FROM (
            SELECT bbl
            FROM building_management
            WHERE is_current = true
            GROUP BY bbl
            HAVING COUNT(*) > 1
        ) t
    """))).scalar() or 0

    leads_zero_links = (await session.execute(text("""
        SELECT COUNT(*)
        FROM leads l
        WHERE NOT EXISTS (
            SELECT 1
            FROM building_management bm
            WHERE bm.lead_id = l.lead_id
              AND bm.is_current = true
        )
    """))).scalar() or 0

    stale_dos_cache = 0
    try:
        stale_dos_cache = (await session.execute(text("""
            SELECT COUNT(*)
            FROM dos_cache
            WHERE cache_key LIKE 'officers:%'
              AND (expires_at IS NULL OR expires_at <= NOW())
        """))).scalar() or 0
    except ProgrammingError:
        await session.rollback()
        stale_dos_cache = (await session.execute(text("""
            SELECT COUNT(*)
            FROM dos_cache
            WHERE cache_key LIKE 'officers:%'
              AND cached_at <= (NOW() - INTERVAL '30 days')
        """))).scalar() or 0

    buildings_without_contacts = (await session.execute(text("""
        SELECT COUNT(*)
        FROM buildings b
        WHERE NOT EXISTS (
            SELECT 1 FROM building_contacts bc WHERE bc.bbl = b.bbl
        )
    """))).scalar() or 0

    return {
        "status": "ok",
        "buildings_with_multiple_current_pm_links": int(buildings_multi_pm),
        "leads_with_zero_active_links": int(leads_zero_links),
        "stale_dos_cache_entries": int(stale_dos_cache),
        "buildings_without_hpd_contacts": int(buildings_without_contacts),
    }


def _normalize_address_for_search(query: str) -> list[str]:
    return address_search_patterns(query)


@router.get("/buildings/search")
@limiter.limit("20/minute")
async def search_buildings(
    request: Request,
    address: str = Query(..., min_length=3),
    limit: int = Query(20, le=100),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """Search buildings by address with fuzzy normalization."""
    patterns = _normalize_address_for_search(address)
    like_clauses = " OR ".join([f"b.address ILIKE :p{i}" for i in range(len(patterns))])
    params = {f"p{i}": p for i, p in enumerate(patterns)}
    params["limit"] = limit
    alias_select_sql = """
                NULL::text AS matched_alias_address,
                NULL::text AS alias_source,
                NULL::double precision AS alias_confidence_score,
    """
    alias_join_sql = ""
    alias_where_sql = ""
    if await address_alias_table_exists(session):
        alias_condition, alias_params = address_alias_search_sql("baa", address, "alias")
        params.update(alias_params)
        alias_select_sql = """
                alias_match.display_address AS matched_alias_address,
                alias_match.source AS alias_source,
                alias_match.confidence_score AS alias_confidence_score,
        """
        alias_join_sql = f"""
            LEFT JOIN LATERAL (
                SELECT baa.bbl, baa.display_address, baa.source, baa.confidence_score
                FROM building_address_aliases baa
                WHERE baa.bbl = b.bbl
                  AND {alias_condition}
                ORDER BY CASE WHEN baa.normalized_address = :alias_normalized THEN 0 ELSE 1 END,
                         baa.is_primary DESC,
                         baa.confidence_score DESC,
                         baa.display_address ASC
                LIMIT 1
            ) alias_match ON true
        """
        alias_where_sql = " OR alias_match.bbl IS NOT NULL"

    result = await session.execute(
        text(f"""
            SELECT
                b.bbl,
                b.address,
{alias_select_sql}
                b.borough,
                b.unit_count,
                b.building_class,
                b.year_built,
                b.churn_score,
                lead_match.lead_id,
                lead_match.lead_name,
                lead_match.entity_type,
                lead_match.score,
                lead_match.portfolio_size,
                lead_match.total_units,
                pm_match.corporation_name AS pm_company
            FROM buildings b
            LEFT JOIN LATERAL (
                SELECT
                    l.lead_id,
                    COALESCE(
                        NULLIF(l.company_name, ''),
                        NULLIF(l.agent_name, ''),
                        NULLIF(l.owner_name, ''),
                        NULLIF(l.primary_contact, ''),
                        NULLIF(l.normalized_name, '')
                    ) AS lead_name,
                    l.entity_type,
                    l.score,
                    l.portfolio_size,
                    l.total_units
                FROM building_management bm
                JOIN leads l ON l.lead_id = bm.lead_id
                WHERE bm.bbl = b.bbl
                  AND bm.is_current = true
                ORDER BY l.score DESC NULLS LAST, l.updated_at DESC NULLS LAST
                LIMIT 1
            ) lead_match ON true
            LEFT JOIN LATERAL (
                SELECT bc_search.corporation_name
                FROM building_contacts bc_search
                WHERE bc_search.bbl = b.bbl
                  AND bc_search.contact_type IN ('Agent', 'ManagementCompany', 'CorporateOwner')
                  AND bc_search.corporation_name IS NOT NULL
                  AND TRIM(bc_search.corporation_name) != ''
                ORDER BY CASE
                    WHEN bc_search.contact_type IN ('Agent', 'ManagementCompany') THEN 0
                    ELSE 1
                END,
                bc_search.corporation_name
                LIMIT 1
            ) pm_match ON true
            {alias_join_sql}
            WHERE ({like_clauses}{alias_where_sql})
            ORDER BY b.churn_score DESC NULLS LAST
            LIMIT :limit
        """),
        params,
    )
    rows = [dict(r._mapping) for r in result]
    buildings = []
    seen_lead_ids = set()
    for r in rows:
        canonical_addr = r.get("address") or ""
        matched_addr = r.get("matched_alias_address") or None
        addr = matched_addr or canonical_addr
        parts = addr.split(" ", 1)
        if r.get("lead_id"):
            seen_lead_ids.add(r["lead_id"])
        buildings.append({
            "building_id": r["bbl"],
            "bbl": r["bbl"],
            "address": addr,
            "canonical_address": canonical_addr if matched_addr and matched_addr != canonical_addr else None,
            "matched_address": matched_addr,
            "address_source": r.get("alias_source"),
            "address_confidence": r.get("alias_confidence_score"),
            "house_number": parts[0] if len(parts) > 1 else "",
            "street_name": parts[1] if len(parts) > 1 else addr,
            "boro": r.get("borough") or "",
            "units_res": r.get("unit_count") or 0,
            "building_class": r.get("building_class") or "",
            "building_type": None,
            "year_built": r.get("year_built"),
            "churn_score": r.get("churn_score"),
            "lead_id": r.get("lead_id"),
            "lead_name": r.get("lead_name"),
            "lead_entity_type": r.get("entity_type"),
            "score": r.get("score"),
            "portfolio_size": r.get("portfolio_size"),
            "total_units": r.get("total_units"),
            "agent_name": r.get("lead_name") or r.get("pm_company") or "",
            "owner_name": "",
            "pm_company": r.get("pm_company"),
            "status": "linked" if r.get("lead_id") else ("discovered" if r.get("pm_company") else "unlinked"),
        })

    if len(buildings) < limit:
        lead_like_clauses = " OR ".join([f"l.address ILIKE :p{i}" for i in range(len(patterns))])
        lead_result = await session.execute(
            text(f"""
                SELECT
                    l.lead_id,
                    l.address,
                    l.primary_borough,
                    COALESCE(
                        NULLIF(l.company_name, ''),
                        NULLIF(l.agent_name, ''),
                        NULLIF(l.owner_name, ''),
                        NULLIF(l.primary_contact, ''),
                        NULLIF(l.normalized_name, ''),
                        NULLIF(l.address, '')
                    ) AS lead_name,
                    l.entity_type,
                    l.score,
                    l.portfolio_size,
                    l.total_units
                FROM leads l
                WHERE l.address IS NOT NULL
                  AND TRIM(l.address) != ''
                  AND ({lead_like_clauses})
                ORDER BY l.score DESC NULLS LAST, l.updated_at DESC NULLS LAST
                LIMIT :lead_limit
            """),
            {**params, "lead_limit": limit - len(buildings)},
        )

        for r in [dict(row._mapping) for row in lead_result]:
            if r.get("lead_id") in seen_lead_ids:
                continue
            addr = r.get("address") or ""
            parts = addr.split(" ", 1)
            lead_name = r.get("lead_name") or addr
            buildings.append({
                "building_id": f"lead:{r['lead_id']}",
                "bbl": None,
                "address": addr,
                "house_number": parts[0] if len(parts) > 1 else "",
                "street_name": parts[1] if len(parts) > 1 else addr,
                "boro": r.get("primary_borough") or "",
                "units_res": r.get("total_units") or 0,
                "building_class": "",
                "building_type": None,
                "year_built": None,
                "churn_score": None,
                "lead_id": r.get("lead_id"),
                "lead_name": lead_name,
                "lead_entity_type": r.get("entity_type"),
                "score": r.get("score"),
                "portfolio_size": r.get("portfolio_size"),
                "total_units": r.get("total_units"),
                "agent_name": lead_name,
                "owner_name": "",
                "pm_company": None,
                "status": "lead_address",
            })
            seen_lead_ids.add(r["lead_id"])
            if len(buildings) >= limit:
                break
    return {"query": address, "buildings": buildings, "total": len(buildings)}
