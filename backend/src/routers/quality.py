"""Data quality API router.

Surfaces ingestion health for the Data Health Dashboard on the Settings page.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/quality", tags=["data-quality"])


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
    result = await session.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM buildings) AS total_buildings,
            (SELECT COUNT(DISTINCT bbl) FROM hpd_complaints) AS with_complaints,
            (SELECT COUNT(DISTINCT bbl) FROM hpd_violations) AS with_violations,
            (SELECT COUNT(DISTINCT bbl) FROM acris_transactions) AS with_transactions,
            (SELECT COUNT(DISTINCT bbl) FROM dob_permits) AS with_permits,
            (SELECT COUNT(DISTINCT bbl) FROM hpd_litigation) AS with_litigation,
            (SELECT COUNT(DISTINCT bbl) FROM emergency_repairs) AS with_erp,
            (SELECT COUNT(DISTINCT bbl) FROM energy_grades) AS with_energy,
            (SELECT COUNT(DISTINCT bbl) FROM eviction_filings) AS with_evictions,
            (SELECT COUNT(DISTINCT bbl) FROM facade_inspections) AS with_facades,
            (SELECT COUNT(DISTINCT bbl) FROM aep_designations) AS with_aep
    """))
    return dict(result.first()._mapping)
