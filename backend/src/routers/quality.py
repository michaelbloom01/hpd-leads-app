"""Data quality API router.

Surfaces ingestion health for the Data Health Dashboard on the Settings page.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/quality", tags=["data-quality"])


@router.get("/data-health")
async def data_health(session: AsyncSession = Depends(get_session)):
    """Aggregated data-health metrics for the dashboard badge."""
    counts = await session.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM leads) AS total_leads,
            (SELECT COUNT(*) FROM buildings) AS total_buildings_registered,
            (SELECT COUNT(DISTINCT source_name) FROM data_quality_log) AS hpd_source_count
    """))
    row = dict(counts.first()._mapping)

    total_buildings = (await session.execute(
        text("SELECT COUNT(*) FROM buildings")
    )).scalar() or 0

    coverage_pct = None
    if total_buildings > 0 and row["total_buildings_registered"]:
        coverage_pct = round(row["total_buildings_registered"] / total_buildings * 100, 1)

    refresh_row = await session.execute(text("""
        SELECT id, status, started_at, finished_at, succeeded, failed
        FROM ingestion_jobs
        WHERE source IN ('hpd_buildings', 'buildings') OR job_type IN ('buildings', 'ingest')
        ORDER BY started_at DESC NULLS LAST
        LIMIT 1
    """))
    refresh = refresh_row.first()
    last_refresh = None
    data_age_days = None
    if refresh:
        r = dict(refresh._mapping)
        finished = r.get("finished_at")
        last_refresh = {
            "started_at": r["started_at"].isoformat() if r.get("started_at") else None,
            "finished_at": finished.isoformat() if finished else None,
            "status": r["status"],
            "leads_net_change": (r.get("succeeded") or 0) - (r.get("failed") or 0),
            "buildings_fetched": r.get("succeeded") or 0,
        }
        if finished:
            data_age_days = (datetime.now(timezone.utc) - finished).days

    stale_result = await session.execute(text("""
        SELECT COUNT(*) FROM buildings
        WHERE updated_at < NOW() - INTERVAL '90 days'
    """))
    stale_count = stale_result.scalar() or 0

    staleness_result = await session.execute(text("""
        SELECT
            COALESCE(SUM(CASE WHEN l.updated_at >= NOW() - INTERVAL '7 days'  THEN 1 ELSE 0 END), 0) AS fresh,
            COALESCE(SUM(CASE WHEN l.updated_at >= NOW() - INTERVAL '30 days'
                              AND l.updated_at <  NOW() - INTERVAL '7 days'  THEN 1 ELSE 0 END), 0) AS recent,
            COALESCE(SUM(CASE WHEN l.updated_at <  NOW() - INTERVAL '30 days' THEN 1 ELSE 0 END), 0) AS stale
        FROM leads l
    """))
    st = dict(staleness_result.first()._mapping)

    enrich_result = await session.execute(text("""
        SELECT
            COALESCE(SUM(CASE WHEN phone IS NOT NULL AND phone != '' THEN 1 ELSE 0 END), 0) AS with_phone,
            COALESCE(SUM(CASE WHEN email IS NOT NULL AND email != '' THEN 1 ELSE 0 END), 0) AS with_email,
            COALESCE(SUM(CASE WHEN website IS NOT NULL AND website != '' THEN 1 ELSE 0 END), 0) AS with_website,
            COUNT(*) AS total
        FROM leads
    """))
    enr = dict(enrich_result.first()._mapping)
    total_l = enr["total"] or 1
    enrichment_coverage = {
        "phone": round(enr["with_phone"] / total_l * 100, 1),
        "email": round(enr["with_email"] / total_l * 100, 1),
        "website": round(enr["with_website"] / total_l * 100, 1),
    }

    warnings: list[str] = []
    if data_age_days is not None and data_age_days > 30:
        warnings.append(f"Building data is {data_age_days} days old")
    if stale_count > 1000:
        warnings.append(f"{stale_count:,} buildings not updated in 90+ days")
    if enrichment_coverage["phone"] < 20:
        warnings.append(f"Low phone coverage: {enrichment_coverage['phone']}%")

    return {
        "total_leads": row["total_leads"],
        "total_buildings_registered": row["total_buildings_registered"],
        "hpd_source_count": row["hpd_source_count"],
        "coverage_percent": coverage_pct,
        "last_refresh": last_refresh,
        "stale_buildings_count": stale_count,
        "lead_staleness": {"fresh": st["fresh"], "recent": st["recent"], "stale": st["stale"]},
        "data_age_days": data_age_days,
        "enrichment_coverage": enrichment_coverage,
        "warnings": warnings,
    }


@router.get("/summary")
async def quality_summary(session: AsyncSession = Depends(get_session)):
    """Per-source latest stats for the data health dashboard."""
    result = await session.execute(text("""
        SELECT DISTINCT ON (source_name)
            source_name, records_fetched, records_matched, records_rejected,
            records_inserted, match_rate, volume_anomaly, notes, run_timestamp
        FROM data_quality_log
        ORDER BY source_name, run_timestamp DESC
    """))
    return [dict(r._mapping) for r in result]


@router.get("/history")
async def quality_history(
    source: Optional[str] = None,
    limit: int = Query(default=30, le=200),
    session: AsyncSession = Depends(get_session),
):
    if source:
        result = await session.execute(
            text("""
                SELECT * FROM data_quality_log
                WHERE source_name = :source
                ORDER BY run_timestamp DESC LIMIT :limit
            """),
            {"source": source, "limit": limit},
        )
    else:
        result = await session.execute(
            text("SELECT * FROM data_quality_log ORDER BY run_timestamp DESC LIMIT :limit"),
            {"limit": limit},
        )
    return [dict(r._mapping) for r in result]


@router.get("/coverage")
async def building_coverage(session: AsyncSession = Depends(get_session)):
    """Signal coverage: how many buildings have data for each signal."""
    signal_tables = {
        "with_complaints": "hpd_complaints",
        "with_violations": "hpd_violations",
        "with_transactions": "acris_transactions",
        "with_permits": "dob_permits",
        "with_litigation": "hpd_litigation",
        "with_erp": "emergency_repairs",
        "with_energy": "energy_grades",
        "with_evictions": "eviction_filings",
        "with_facades": "facade_inspections",
        "with_aep": "aep_designations",
    }
    total = (await session.execute(text("SELECT COUNT(*) FROM buildings"))).scalar() or 0
    coverage: dict = {"total_buildings": total}
    for key, table in signal_tables.items():
        try:
            val = (await session.execute(
                text(f"SELECT COUNT(DISTINCT bbl) FROM {table}")
            )).scalar()
            coverage[key] = val or 0
        except Exception:
            coverage[key] = 0
            await session.rollback()
    return coverage
