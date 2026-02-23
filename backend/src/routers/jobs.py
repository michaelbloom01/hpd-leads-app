"""Job tracking API router for background task monitoring."""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


@router.get("")
async def list_jobs(
    status: Optional[str] = None,
    job_type: Optional[str] = None,
    limit: int = Query(default=20, le=100),
    session: AsyncSession = Depends(get_session),
):
    wheres = []
    params: dict = {"limit": limit}
    if status:
        wheres.append("status = :status")
        params["status"] = status
    if job_type:
        wheres.append("job_type = :jtype")
        params["jtype"] = job_type
    where_sql = " AND ".join(wheres) if wheres else "1=1"

    result = await session.execute(
        text(f"""
            SELECT id, job_type, source, status, total, processed, succeeded,
                   failed, error, started_at, finished_at
            FROM ingestion_jobs
            WHERE {where_sql}
            ORDER BY id DESC LIMIT :limit
        """),
        params,
    )
    return [dict(r._mapping) for r in result]


@router.get("/{job_id}")
async def get_job(job_id: int, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        text("SELECT * FROM ingestion_jobs WHERE id = :id"), {"id": job_id}
    )
    row = result.first()
    if not row:
        raise HTTPException(404, "Job not found")
    return dict(row._mapping)


@router.post("/{job_type}/start")
async def start_job(job_type: str, session: AsyncSession = Depends(get_session)):
    """Trigger a background ingestion job."""
    from src.tasks.ingest import (
        ingest_buildings_from_hpd, ingest_hpd_complaints,
        ingest_acris_transactions, ingest_hpd_violations,
        ingest_dob_permits, ingest_hpd_litigation,
        ingest_emergency_repairs, ingest_aep_designations,
        ingest_eviction_filings, ingest_energy_grades,
        ingest_facade_inspections, ingest_pad_addresses,
    )
    from src.tasks.score import compute_churn_scores

    task_map = {
        "buildings": ingest_buildings_from_hpd,
        "hpd_complaints": ingest_hpd_complaints,
        "acris": ingest_acris_transactions,
        "hpd_violations": ingest_hpd_violations,
        "dob_permits": ingest_dob_permits,
        "hpd_litigation": ingest_hpd_litigation,
        "emergency_repairs": ingest_emergency_repairs,
        "aep": ingest_aep_designations,
        "evictions": ingest_eviction_filings,
        "energy": ingest_energy_grades,
        "facades": ingest_facade_inspections,
        "pad": ingest_pad_addresses,
        "scoring": compute_churn_scores,
    }

    task = task_map.get(job_type)
    if not task:
        raise HTTPException(400, f"Unknown job type: {job_type}. Valid: {list(task_map.keys())}")

    task.delay()
    return {"status": "queued", "job_type": job_type}
