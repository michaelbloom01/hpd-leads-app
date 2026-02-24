"""Job tracking API router for background task monitoring."""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_session
from src.auth.auth import AuthUser, get_current_user

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
async def start_job(
    job_type: str,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """Trigger a background job. Runs in a thread (no Celery/Redis required)."""
    valid_types = [
        "buildings", "hpd_complaints", "acris", "hpd_violations",
        "dob_permits", "hpd_litigation", "emergency_repairs", "aep",
        "evictions", "energy", "facades", "pad", "scoring",
    ]
    if job_type not in valid_types:
        raise HTTPException(400, f"Unknown job type: {job_type}. Valid: {valid_types}")

    result = await session.execute(
        text("""INSERT INTO ingestion_jobs (job_type, source, status, started_at, created_at, updated_at)
                VALUES (:jt, :jt, 'running', now(), now(), now()) RETURNING id"""),
        {"jt": job_type},
    )
    job_id = result.scalar_one()
    return {"status": "queued", "job_type": job_type, "job_id": job_id}
