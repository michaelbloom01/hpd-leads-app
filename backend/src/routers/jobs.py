"""Job tracking API router for background task monitoring."""
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_session
from src.auth.auth import AuthUser, get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


def _normalize_status(raw: str | None) -> str:
    if raw == "completed":
        return "succeeded"
    return raw or "unknown"


JOB_TYPE_ALIASES = {
    "hpd_buildings": "buildings",
    "hpd_registrations": "buildings",
    "hpd_contacts": "buildings",
    "acris_transactions": "acris",
    "energy_grades": "energy",
    "eviction_filings": "evictions",
    "facade_inspections": "facades",
    "aep_designations": "aep",
}


@router.get("/summary")
async def jobs_summary(
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """Lightweight queue observability for dashboards/ops."""
    row = (await session.execute(text("""
        SELECT
            COALESCE(SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END), 0) AS queued_count,
            COALESCE(SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END), 0) AS running_count,
            COALESCE(SUM(CASE WHEN status IN ('succeeded', 'completed') AND finished_at >= now() - interval '24 hours' THEN 1 ELSE 0 END), 0) AS succeeded_24h,
            COALESCE(SUM(CASE WHEN status = 'failed' AND finished_at >= now() - interval '24 hours' THEN 1 ELSE 0 END), 0) AS failed_24h,
            COALESCE(AVG(EXTRACT(EPOCH FROM (finished_at - started_at))) FILTER (WHERE finished_at IS NOT NULL AND started_at IS NOT NULL AND finished_at >= now() - interval '24 hours'), 0) AS avg_duration_seconds_24h
        FROM ingestion_jobs
    """))).first()
    data = dict(row._mapping) if row else {}
    return {
        "queued_count": int(data.get("queued_count") or 0),
        "running_count": int(data.get("running_count") or 0),
        "succeeded_24h": int(data.get("succeeded_24h") or 0),
        "failed_24h": int(data.get("failed_24h") or 0),
        "avg_duration_seconds_24h": float(data.get("avg_duration_seconds_24h") or 0.0),
    }


@router.get("")
async def list_jobs(
    status: Optional[str] = None,
    job_type: Optional[str] = None,
    limit: int = Query(default=20, le=100),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    wheres = []
    params: dict = {"limit": limit}
    if status:
        if status in {"succeeded", "completed"}:
            wheres.append("status IN ('succeeded', 'completed')")
        else:
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
    rows = []
    for r in result:
        row = dict(r._mapping)
        row["status"] = _normalize_status(row.get("status"))
        rows.append(row)
    return rows


@router.get("/{job_id}")
async def get_job(job_id: int, session: AsyncSession = Depends(get_session), user: AuthUser = Depends(get_current_user)):
    result = await session.execute(
        text("SELECT * FROM ingestion_jobs WHERE id = :id"), {"id": job_id}
    )
    row = result.first()
    if not row:
        raise HTTPException(404, "Job not found")
    data = dict(row._mapping)
    data["status"] = _normalize_status(data.get("status"))
    return data


@router.post("/{job_type}/start")
async def start_job(
    job_type: str,
    limit: int = Query(default=500, ge=1, le=5000),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """Queue a background job record for execution tracking."""
    original_job_type = job_type
    job_type = JOB_TYPE_ALIASES.get(job_type, job_type)

    valid_types = [
        "buildings", "hpd_complaints", "acris", "hpd_violations",
        "dob_permits", "hpd_litigation", "emergency_repairs", "aep",
        "evictions", "energy", "facades", "pad", "scoring", "enrichment",
    ]
    if job_type not in valid_types:
        raise HTTPException(400, f"Unknown job type: {job_type}. Valid: {valid_types}")

    result = await session.execute(
        text("""INSERT INTO ingestion_jobs (job_type, source, status, started_at, created_at, updated_at)
                VALUES (:jt, :jt, 'queued', now(), now(), now()) RETURNING id"""),
        {"jt": job_type},
    )
    job_id = result.scalar_one()
    await session.commit()

    if job_type == "enrichment":
        dispatch_mode = "celery"
        try:
            from src.tasks.enrich import run_enrichment_job
            run_enrichment_job.delay(job_id=job_id, limit=limit)
        except Exception as exc:
            logger.warning(
                "Celery dispatch failed for enrichment job %s, falling back to in-process execution: %s",
                job_id,
                exc,
            )
            from src.tasks.enrich import _run_enrichment_job_async
            asyncio.create_task(_run_enrichment_job_async(job_id=job_id, limit=limit))
            dispatch_mode = "in_process"

        return {
            "status": "queued",
            "job_type": job_type,
            "requested_job_type": original_job_type,
            "job_id": job_id,
            "limit": limit,
            "dispatch_mode": dispatch_mode,
        }

    if job_type == "buildings":
        dispatch_mode = "celery"
        try:
            from src.tasks.ingest import ingest_buildings_from_hpd
            ingest_buildings_from_hpd.delay(job_id=job_id)
        except Exception as exc:
            logger.warning(
                "Celery dispatch failed for buildings job %s, falling back to in-process execution: %s",
                job_id,
                exc,
            )
            from src.tasks.ingest import ingest_buildings_from_hpd
            asyncio.create_task(asyncio.to_thread(ingest_buildings_from_hpd.run, job_id=job_id))
            dispatch_mode = "in_process"

        return {
            "status": "queued",
            "job_type": job_type,
            "requested_job_type": original_job_type,
            "job_id": job_id,
            "limit": limit,
            "dispatch_mode": dispatch_mode,
        }

    ingest_dispatch = {
        "hpd_complaints": "ingest_hpd_complaints",
        "acris": "ingest_acris_transactions",
        "hpd_violations": "ingest_hpd_violations",
        "dob_permits": "ingest_dob_permits",
        "hpd_litigation": "ingest_hpd_litigation",
        "emergency_repairs": "ingest_emergency_repairs",
        "aep": "ingest_aep_designations",
        "evictions": "ingest_eviction_filings",
        "energy": "ingest_energy_grades",
        "facades": "ingest_facade_inspections",
        "pad": "ingest_pad_addresses",
    }
    if job_type in ingest_dispatch:
        dispatch_mode = "celery"
        task_attr = ingest_dispatch[job_type]
        try:
            from src.tasks import ingest as ingest_tasks
            getattr(ingest_tasks, task_attr).delay(job_id=job_id)
        except Exception as exc:
            logger.warning(
                "Celery dispatch failed for %s job %s, falling back to in-process execution: %s",
                job_type,
                job_id,
                exc,
            )
            from src.tasks import ingest as ingest_tasks
            asyncio.create_task(asyncio.to_thread(getattr(ingest_tasks, task_attr).run, job_id=job_id))
            dispatch_mode = "in_process"

        return {
            "status": "queued",
            "job_type": job_type,
            "requested_job_type": original_job_type,
            "job_id": job_id,
            "limit": limit,
            "dispatch_mode": dispatch_mode,
        }

    if job_type == "scoring":
        dispatch_mode = "celery"
        try:
            from src.tasks.score import run_scoring_job
            run_scoring_job.delay(job_id=job_id)
        except Exception as exc:
            logger.warning(
                "Celery dispatch failed for scoring job %s, falling back to in-process execution: %s",
                job_id,
                exc,
            )
            from src.tasks.score import run_scoring_job
            asyncio.create_task(asyncio.to_thread(run_scoring_job.run, job_id=job_id))
            dispatch_mode = "in_process"

        return {
            "status": "queued",
            "job_type": job_type,
            "requested_job_type": original_job_type,
            "job_id": job_id,
            "limit": limit,
            "dispatch_mode": dispatch_mode,
        }

    return {
        "status": "queued",
        "job_type": job_type,
        "requested_job_type": original_job_type,
        "job_id": job_id,
        "limit": limit,
    }
