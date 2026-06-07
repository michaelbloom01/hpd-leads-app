"""Job tracking API router for background task monitoring."""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_session
from src.auth.auth import AuthUser, get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])
STALE_RUNNING_THRESHOLD_MINUTES = 120
STALE_QUEUED_THRESHOLD_MINUTES = 15
SOURCE_REFRESH_JOB_TYPES = {
    "buildings",
    "hpd_complaints",
    "acris",
    "hpd_violations",
    "dob_permits",
    "hpd_litigation",
    "emergency_repairs",
    "aep",
    "evictions",
    "energy",
    "facades",
    "pad",
    "building_coordinates",
}
APPROVAL_REQUIRED_JOB_TYPES = SOURCE_REFRESH_JOB_TYPES | {
    "enrichment",
    "scoring",
    "smart_lists_evaluation",
    "quality_checks",
    "lead_generation",
    "lead_reconciliation",
}


def _normalize_status(raw: str | None) -> str:
    if raw == "completed":
        return "succeeded"
    return raw or "unknown"


def _parse_config(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _job_request_signature(
    *,
    job_type: str,
    limit: int,
    dry_run: bool,
    confirm_execute: bool,
    cohort_filter: Optional[str],
    sources: Optional[list[str]] = None,
) -> dict[str, object]:
    return {
        "job_type": job_type,
        "limit": int(limit),
        "dry_run": bool(dry_run),
        "confirm_execute": bool(confirm_execute),
        "cohort_filter": cohort_filter,
        "sources": sources or [],
    }


def _extract_signature_from_config(job_type: str, config: dict) -> dict[str, object]:
    request = config.get("request") if isinstance(config.get("request"), dict) else {}
    return {
        "job_type": job_type,
        "limit": int(request.get("limit") or 0),
        "dry_run": bool(request.get("dry_run", config.get("dry_run", True))),
        "confirm_execute": bool(request.get("confirm_execute", config.get("confirm_execute", False))),
        "cohort_filter": request.get("cohort_filter", config.get("cohort_filter")),
        "sources": request.get("sources", config.get("sources", [])) or [],
    }


def _build_job_config(
    *,
    job_type: str,
    requested_job_type: str,
    limit: int,
    dry_run: bool,
    confirm_execute: bool,
    cohort_filter: Optional[str],
    sources: Optional[list[str]] = None,
    existing_config: Optional[dict] = None,
) -> dict:
    config = dict(existing_config or {})
    config["request"] = {
        "job_type": job_type,
        "requested_job_type": requested_job_type,
        "limit": int(limit),
        "dry_run": bool(dry_run),
        "confirm_execute": bool(confirm_execute),
        "cohort_filter": cohort_filter,
        "sources": sources or [],
    }
    config["dispatch"] = {
        "state": "created",
        "mode": None,
        "fallback_used": False,
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "dispatched_at": None,
        "error": None,
    }
    return config


def _approval_preview_response(
    *,
    job_type: str,
    requested_job_type: str,
    limit: int,
    dry_run: bool,
    confirm_execute: bool,
    operation: str = "job_execution",
    sources: Optional[list[str]] = None,
) -> dict[str, object]:
    source_query = "".join(f"&source={source}" for source in (sources or []))
    return {
        "status": "approval_required",
        "job_type": job_type,
        "requested_job_type": requested_job_type,
        "job_id": None,
        "limit": limit,
        "dispatch_mode": None,
        "dry_run": dry_run,
        "confirm_execute": confirm_execute,
        "approval_required": True,
        "safe_to_run_automatically": False,
        "mutations_planned": 0,
        "preview": {
            "operation": operation,
            "would_enqueue_job_type": job_type,
            "would_mutate": [
                "application data",
                "ingestion_jobs",
                "job-specific output tables",
            ],
            "required_execute_query": f"/api/v1/jobs/{job_type}/start?dry_run=false&confirm_execute=true&limit={limit}{source_query}",
            "sources": sources or [],
        },
        "rollback_strategy": "No job was queued. To execute, rerun with dry_run=false and confirm_execute=true after reviewing the preview.",
    }


async def _persist_job_record(
    session: AsyncSession,
    *,
    job_id: int,
    config: dict,
    status: Optional[str] = None,
    error: Optional[str] = None,
    finish: bool = False,
) -> None:
    sets = [
        "config = CAST(:config AS JSONB)",
        "updated_at = now()",
    ]
    params: dict[str, object] = {
        "job_id": job_id,
        "config": json.dumps(config),
    }
    if status is not None:
        sets.append("status = :status")
        params["status"] = status
    if error is not None:
        sets.append("error = :error")
        params["error"] = error
    if finish:
        sets.append("finished_at = now()")
    await session.execute(
        text(f"UPDATE ingestion_jobs SET {', '.join(sets)} WHERE id = :job_id"),
        params,
    )
    await session.commit()


async def _find_equivalent_inflight_job(
    session: AsyncSession,
    *,
    job_type: str,
    signature: dict[str, object],
) -> Optional[dict[str, object]]:
    rows = (
        await session.execute(
            text(
                """
                SELECT id, status, config
                FROM ingestion_jobs
                WHERE job_type = :job_type
                  AND status IN ('queued', 'running')
                  AND finished_at IS NULL
                ORDER BY id DESC
                """
            ),
            {"job_type": job_type},
        )
    ).fetchall()
    for row in rows:
        config = _parse_config(row[2])
        if not config or "request" not in config or _extract_signature_from_config(job_type, config) == signature:
            return {
                "job_id": int(row[0]),
                "status": _normalize_status(str(row[1])),
                "config": config,
            }
    return None


async def _dispatch_with_fallback(
    *,
    job_type: str,
    job_id: int,
    primary_dispatch,
    fallback_dispatch,
) -> tuple[str, bool]:
    try:
        primary_dispatch()
        return "celery", False
    except Exception as exc:
        logger.warning(
            "Celery dispatch failed for %s job %s, falling back to in-process execution: %s",
            job_type,
            job_id,
            exc,
        )
        fallback_dispatch()
        return "in_process", True


JOB_TYPE_ALIASES = {
    "hpd_buildings": "buildings",
    "hpd_registrations": "buildings",
    "hpd_contacts": "buildings",
    "acris_transactions": "acris",
    "energy_grades": "energy",
    "eviction_filings": "evictions",
    "facade_inspections": "facades",
    "aep_designations": "aep",
    "smart_lists_auto_evaluate": "smart_lists_evaluation",
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
            COALESCE(SUM(CASE
                WHEN status = 'queued'
                 AND finished_at IS NULL
                 AND COALESCE(config->'dispatch'->>'state', 'created') <> 'dispatched'
                THEN 1 ELSE 0 END), 0) AS stranded_queued_count,
            COALESCE(SUM(CASE WHEN status IN ('succeeded', 'completed') AND finished_at >= now() - interval '24 hours' THEN 1 ELSE 0 END), 0) AS succeeded_24h,
            COALESCE(SUM(CASE WHEN status = 'failed' AND finished_at >= now() - interval '24 hours' THEN 1 ELSE 0 END), 0) AS failed_24h,
            COALESCE(AVG(EXTRACT(EPOCH FROM (finished_at - started_at))) FILTER (WHERE finished_at IS NOT NULL AND started_at IS NOT NULL AND finished_at >= now() - interval '24 hours'), 0) AS avg_duration_seconds_24h
        FROM ingestion_jobs
    """))).first()
    data = dict(row._mapping) if row else {}
    return {
        "queued_count": int(data.get("queued_count") or 0),
        "running_count": int(data.get("running_count") or 0),
        "stranded_queued_count": int(data.get("stranded_queued_count") or 0),
        "succeeded_24h": int(data.get("succeeded_24h") or 0),
        "failed_24h": int(data.get("failed_24h") or 0),
        "avg_duration_seconds_24h": float(data.get("avg_duration_seconds_24h") or 0.0),
    }


@router.get("/worker-health")
async def worker_health(
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """Worker/broker liveness and stale-running/queued signal."""
    stale_count = (
        await session.execute(
            text(
                """
                SELECT COUNT(*)
                FROM ingestion_jobs
                WHERE status = 'running'
                  AND finished_at IS NULL
                  AND started_at < now() - (:mins * interval '1 minute')
                """
            ),
            {"mins": STALE_RUNNING_THRESHOLD_MINUTES},
        )
    ).scalar_one()
    stranded_queued_count = (
        await session.execute(
            text(
                """
                SELECT COUNT(*)
                FROM ingestion_jobs
                WHERE status = 'queued'
                  AND finished_at IS NULL
                  AND COALESCE(config->'dispatch'->>'state', 'created') <> 'dispatched'
                  AND updated_at < now() - (:mins * interval '1 minute')
                """
            ),
            {"mins": STALE_QUEUED_THRESHOLD_MINUTES},
        )
    ).scalar_one()

    broker_url = os.environ.get("REDIS_URL", "")
    celery_ping_ok = False
    celery_ping_detail = "unavailable"
    try:
        from src.worker import app as celery_app

        inspector = celery_app.control.inspect(timeout=1.5)
        ping = inspector.ping() or {}
        celery_ping_ok = bool(ping)
        celery_ping_detail = "ok" if celery_ping_ok else "no_workers"
    except Exception as exc:
        celery_ping_detail = f"error:{type(exc).__name__}"

    return {
        "status": "ok" if stale_count == 0 and stranded_queued_count == 0 else "degraded",
        "broker_configured": bool(broker_url),
        "celery_ping_ok": celery_ping_ok,
        "celery_ping_detail": celery_ping_detail,
        "stale_running_threshold_minutes": STALE_RUNNING_THRESHOLD_MINUTES,
        "stale_running_jobs": int(stale_count or 0),
        "stale_queued_threshold_minutes": STALE_QUEUED_THRESHOLD_MINUTES,
        "stale_queued_jobs": int(stranded_queued_count or 0),
    }


@router.post("/reconcile-stale-running")
async def reconcile_stale_running(
    dry_run: bool = Query(default=True),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """Mark excessively old running jobs as failed to avoid silent zombie rows."""
    stale_rows = (
        await session.execute(
            text(
                """
                SELECT id
                FROM ingestion_jobs
                WHERE status = 'running'
                  AND finished_at IS NULL
                  AND started_at < now() - (:mins * interval '1 minute')
                ORDER BY id
                """
            ),
            {"mins": STALE_RUNNING_THRESHOLD_MINUTES},
        )
    ).fetchall()
    stale_ids = [int(r[0]) for r in stale_rows]
    if dry_run or not stale_ids:
        return {
            "status": "dry_run" if dry_run else "no_op",
            "stale_job_ids": stale_ids,
            "updated": 0,
        }

    placeholders = ", ".join(f":sid_{i}" for i in range(len(stale_ids)))
    params = {
        "suffix": "Marked failed by stale-job reconciler",
        "suffix_prefixed": " | Marked failed by stale-job reconciler",
    }
    for i, sid in enumerate(stale_ids):
        params[f"sid_{i}"] = sid
    await session.execute(
        text(
            f"""
            UPDATE ingestion_jobs
            SET status = 'failed',
                finished_at = now(),
                updated_at = now(),
                error = COALESCE(error, '') || CASE
                    WHEN COALESCE(error, '') = '' THEN :suffix
                    ELSE :suffix_prefixed
                END
            WHERE id IN ({placeholders})
            """
        ),
        params,
    )
    await session.commit()
    return {"status": "updated", "stale_job_ids": stale_ids, "updated": len(stale_ids)}


@router.post("/reconcile-stale-queued")
async def reconcile_stale_queued(
    dry_run: bool = Query(default=True),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """Mark stranded queued jobs failed when they were never dispatched."""
    queued_rows = (
        await session.execute(
            text(
                """
                SELECT id
                FROM ingestion_jobs
                WHERE status = 'queued'
                  AND finished_at IS NULL
                  AND COALESCE(config->'dispatch'->>'state', 'created') <> 'dispatched'
                  AND updated_at < now() - (:mins * interval '1 minute')
                ORDER BY id
                """
            ),
            {"mins": STALE_QUEUED_THRESHOLD_MINUTES},
        )
    ).fetchall()
    queued_ids = [int(r[0]) for r in queued_rows]
    if dry_run or not queued_ids:
        return {
            "status": "dry_run" if dry_run else "no_op",
            "stale_job_ids": queued_ids,
            "updated": 0,
        }

    placeholders = ", ".join(f":qid_{i}" for i in range(len(queued_ids)))
    params = {
        "suffix": "Marked failed by queued-job reconciler",
        "suffix_prefixed": " | Marked failed by queued-job reconciler",
    }
    for i, qid in enumerate(queued_ids):
        params[f"qid_{i}"] = qid
    await session.execute(
        text(
            f"""
            UPDATE ingestion_jobs
            SET status = 'failed',
                finished_at = now(),
                updated_at = now(),
                error = COALESCE(error, '') || CASE
                    WHEN COALESCE(error, '') = '' THEN :suffix
                    ELSE :suffix_prefixed
                END
            WHERE id IN ({placeholders})
            """
        ),
        params,
    )
    await session.commit()
    return {"status": "updated", "stale_job_ids": queued_ids, "updated": len(queued_ids)}


@router.get("", include_in_schema=False)
@router.get("/")
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
                   failed, error, started_at, finished_at, config
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
        config = _parse_config(row.get("config"))
        dispatch = config.get("dispatch") if isinstance(config.get("dispatch"), dict) else {}
        row["dispatch_state"] = dispatch.get("state")
        row["dispatch_mode"] = dispatch.get("mode")
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
    dry_run: bool = Query(default=True),
    confirm_execute: bool = Query(default=False),
    cohort_filter: Optional[str] = Query(default=None),
    source: Optional[list[str]] = Query(default=None, description="Optional repeatable truth_materialization source filter."),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """Queue a background job record for execution tracking."""
    dry_run = dry_run if isinstance(dry_run, bool) else True
    confirm_execute = confirm_execute if isinstance(confirm_execute, bool) else False
    cohort_filter = cohort_filter.strip() if isinstance(cohort_filter, str) and cohort_filter.strip() else None
    source_filters = source if isinstance(source, list) else None
    selected_sources: list[str] = []
    original_job_type = job_type
    job_type = JOB_TYPE_ALIASES.get(job_type, job_type)
    if source_filters and job_type != "truth_materialization":
        raise HTTPException(400, "source filters are only supported for truth_materialization jobs")
    if source_filters and job_type == "truth_materialization":
        from src.services.truth_materialization import normalize_materialization_sources

        try:
            selected_sources = list(normalize_materialization_sources(source_filters) or [])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    valid_types = [
        "buildings", "hpd_complaints", "acris", "hpd_violations",
        "dob_permits", "hpd_litigation", "emergency_repairs", "aep",
        "evictions", "energy", "facades", "pad", "scoring", "enrichment",
        "smart_lists_evaluation", "entity_resolution", "quality_checks",
        "lead_generation", "lead_reconciliation", "building_coordinates",
        "truth_validation", "truth_materialization",
    ]
    if job_type not in valid_types:
        raise HTTPException(400, f"Unknown job type: {job_type}. Valid: {valid_types}")

    config_payload = _build_job_config(
        job_type=job_type,
        requested_job_type=original_job_type,
        limit=limit,
        dry_run=dry_run,
        confirm_execute=confirm_execute,
        cohort_filter=cohort_filter,
        sources=selected_sources,
    )
    if job_type == "entity_resolution":
        if not dry_run:
            if not confirm_execute:
                raise HTTPException(400, "entity_resolution execution requires confirm_execute=true")
            if cohort_filter != "safe_keep":
                raise HTTPException(400, "entity_resolution execution currently only allows cohort_filter=safe_keep")
            conflicting_rows = (
                await session.execute(
                    text("""
                        SELECT id, job_type
                        FROM ingestion_jobs
                        WHERE status = 'running'
                          AND job_type IN ('entity_resolution', 'lead_generation', 'lead_reconciliation')
                        ORDER BY id
                    """)
                )
            ).fetchall()
            if conflicting_rows:
                conflicts = [
                    {"job_id": int(row[0]), "job_type": str(row[1])}
                    for row in conflicting_rows
                ]
                raise HTTPException(
                    409,
                    {
                        "message": "entity_resolution execution blocked while related lead/linkage jobs are running",
                        "conflicts": conflicts,
                    },
                )

        config_payload.update({
            "mode": "dry_run" if dry_run or not confirm_execute else "execute",
            "dry_run": dry_run,
            "confirm_execute": confirm_execute,
            "cohort_filter": cohort_filter,
            "write_permitted": (not dry_run) and confirm_execute and cohort_filter == "safe_keep",
        })
    if job_type in APPROVAL_REQUIRED_JOB_TYPES and job_type != "entity_resolution":
        if dry_run:
            return _approval_preview_response(
                job_type=job_type,
                requested_job_type=original_job_type,
                limit=limit,
                dry_run=dry_run,
                confirm_execute=confirm_execute,
                operation="source_refresh" if job_type in SOURCE_REFRESH_JOB_TYPES else "job_execution",
                sources=selected_sources,
            )
        if not confirm_execute:
            raise HTTPException(400, f"{job_type} execution requires confirm_execute=true")
        config_payload.update({
            "mode": "execute",
            "dry_run": False,
            "confirm_execute": True,
            "write_permitted": True,
        })
    if job_type == "truth_materialization" and not dry_run and not confirm_execute:
        raise HTTPException(400, "truth_materialization execution requires confirm_execute=true")
    if job_type == "truth_validation" and not dry_run and not confirm_execute:
        raise HTTPException(400, "truth_validation review-item execution requires confirm_execute=true")
    if job_type in {"truth_validation", "truth_materialization"}:
        from src.services.truth_health import is_truth_schema_current, load_truth_schema_status

        schema_status = await load_truth_schema_status(session)
        if not is_truth_schema_current(schema_status):
            return {
                "status": "schema_not_ready",
                "job_type": job_type,
                "requested_job_type": original_job_type,
                "job_id": None,
                "limit": limit,
                "dispatch_mode": None,
                "dry_run": dry_run,
                "confirm_execute": confirm_execute,
                "schema_status": schema_status,
            }
        if dry_run:
            return _approval_preview_response(
                job_type=job_type,
                requested_job_type=original_job_type,
                limit=limit,
                dry_run=dry_run,
                confirm_execute=confirm_execute,
                operation="truth_claim_review" if job_type == "truth_validation" else "truth_claim_materialization",
                sources=selected_sources,
            )

    signature = _job_request_signature(
        job_type=job_type,
        limit=limit,
        dry_run=dry_run,
        confirm_execute=confirm_execute,
        cohort_filter=cohort_filter,
        sources=selected_sources,
    )
    duplicate_job = await _find_equivalent_inflight_job(
        session,
        job_type=job_type,
        signature=signature,
    )
    if duplicate_job:
        raise HTTPException(
            409,
            {
                "message": "Equivalent job is already queued or running",
                "job_id": duplicate_job["job_id"],
                "job_type": job_type,
                "status": duplicate_job["status"],
            },
        )

    result = await session.execute(
        text("""INSERT INTO ingestion_jobs (job_type, source, status, config, created_at, updated_at)
                VALUES (:jt, :jt, 'queued', CAST(:config AS JSONB), now(), now()) RETURNING id"""),
        {"jt": job_type, "config": json.dumps(config_payload)},
    )
    job_id = result.scalar_one()
    await session.commit()

    async def _record_dispatch_success(dispatch_mode: str, fallback_used: bool) -> None:
        config_payload["dispatch"] = {
            **dict(config_payload.get("dispatch") or {}),
            "state": "dispatched",
            "mode": dispatch_mode,
            "fallback_used": fallback_used,
            "dispatched_at": datetime.now(timezone.utc).isoformat(),
            "error": None,
        }
        await _persist_job_record(session, job_id=job_id, config=config_payload)

    async def _record_dispatch_failure(exc: Exception) -> None:
        config_payload["dispatch"] = {
            **dict(config_payload.get("dispatch") or {}),
            "state": "dispatch_failed",
            "mode": None,
            "fallback_used": False,
            "dispatched_at": None,
            "error": str(exc)[:500],
        }
        await _persist_job_record(
            session,
            job_id=job_id,
            config=config_payload,
            status="failed",
            error=str(exc)[:500],
            finish=True,
        )

    try:
        if job_type == "enrichment":
            from src.tasks.enrich import run_enrichment_job
            from src.tasks.enrich import _run_enrichment_job_async

            dispatch_mode, fallback_used = await _dispatch_with_fallback(
                job_type=job_type,
                job_id=job_id,
                primary_dispatch=lambda: run_enrichment_job.delay(job_id=job_id, limit=limit),
                fallback_dispatch=lambda: asyncio.create_task(_run_enrichment_job_async(job_id=job_id, limit=limit)),
            )
            await _record_dispatch_success(dispatch_mode, fallback_used)
            return {
                "status": "queued",
                "job_type": job_type,
                "requested_job_type": original_job_type,
                "job_id": job_id,
                "limit": limit,
                "dispatch_mode": dispatch_mode,
            }

        if job_type == "buildings":
            from src.tasks.ingest import ingest_buildings_from_hpd

            dispatch_mode, fallback_used = await _dispatch_with_fallback(
                job_type=job_type,
                job_id=job_id,
                primary_dispatch=lambda: ingest_buildings_from_hpd.delay(job_id=job_id),
                fallback_dispatch=lambda: asyncio.create_task(asyncio.to_thread(ingest_buildings_from_hpd.run, job_id=job_id)),
            )
            await _record_dispatch_success(dispatch_mode, fallback_used)
            return {
                "status": "queued",
                "job_type": job_type,
                "requested_job_type": original_job_type,
                "job_id": job_id,
                "limit": limit,
                "dispatch_mode": dispatch_mode,
            }

        if job_type == "building_coordinates":
            from src.tasks.ingest import backfill_building_coordinates

            dispatch_mode, fallback_used = await _dispatch_with_fallback(
                job_type=job_type,
                job_id=job_id,
                primary_dispatch=lambda: backfill_building_coordinates.delay(job_id=job_id, limit=limit),
                fallback_dispatch=lambda: asyncio.create_task(asyncio.to_thread(backfill_building_coordinates.run, job_id=job_id, limit=limit)),
            )
            await _record_dispatch_success(dispatch_mode, fallback_used)
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
            from src.tasks import ingest as ingest_tasks
            task_attr = ingest_dispatch[job_type]
            task_fn = getattr(ingest_tasks, task_attr)

            dispatch_mode, fallback_used = await _dispatch_with_fallback(
                job_type=job_type,
                job_id=job_id,
                primary_dispatch=lambda: task_fn.delay(job_id=job_id),
                fallback_dispatch=lambda: asyncio.create_task(asyncio.to_thread(task_fn.run, job_id=job_id)),
            )
            await _record_dispatch_success(dispatch_mode, fallback_used)
            return {
                "status": "queued",
                "job_type": job_type,
                "requested_job_type": original_job_type,
                "job_id": job_id,
                "limit": limit,
                "dispatch_mode": dispatch_mode,
            }

        if job_type == "scoring":
            from src.tasks.score import run_scoring_job

            dispatch_mode, fallback_used = await _dispatch_with_fallback(
                job_type=job_type,
                job_id=job_id,
                primary_dispatch=lambda: run_scoring_job.delay(job_id=job_id),
                fallback_dispatch=lambda: asyncio.create_task(asyncio.to_thread(run_scoring_job.run, job_id=job_id)),
            )
            await _record_dispatch_success(dispatch_mode, fallback_used)
            return {
                "status": "queued",
                "job_type": job_type,
                "requested_job_type": original_job_type,
                "job_id": job_id,
                "limit": limit,
                "dispatch_mode": dispatch_mode,
            }

        if job_type == "smart_lists_evaluation":
            from src.tasks.smart_lists import run_auto_evaluate_job

            dispatch_mode, fallback_used = await _dispatch_with_fallback(
                job_type=job_type,
                job_id=job_id,
                primary_dispatch=lambda: run_auto_evaluate_job.delay(job_id=job_id, limit=limit),
                fallback_dispatch=lambda: asyncio.create_task(asyncio.to_thread(run_auto_evaluate_job.run, job_id=job_id, limit=limit)),
            )
            await _record_dispatch_success(dispatch_mode, fallback_used)
            return {
                "status": "queued",
                "job_type": job_type,
                "requested_job_type": original_job_type,
                "job_id": job_id,
                "limit": limit,
                "dispatch_mode": dispatch_mode,
            }

        if job_type == "entity_resolution":
            from src.tasks.entity_resolution import resolve_entities
            fn = resolve_entities.run if hasattr(resolve_entities, "run") else resolve_entities

            dispatch_mode, fallback_used = await _dispatch_with_fallback(
                job_type=job_type,
                job_id=job_id,
                primary_dispatch=lambda: resolve_entities.delay(
                    job_id=job_id,
                    dry_run=dry_run,
                    confirm_execute=confirm_execute,
                    cohort_filter=cohort_filter,
                ),
                fallback_dispatch=lambda: asyncio.create_task(
                    asyncio.to_thread(
                        fn,
                        job_id=job_id,
                        dry_run=dry_run,
                        confirm_execute=confirm_execute,
                        cohort_filter=cohort_filter,
                    )
                ),
            )
            await _record_dispatch_success(dispatch_mode, fallback_used)
            return {
                "status": "queued",
                "job_type": job_type,
                "requested_job_type": original_job_type,
                "job_id": job_id,
                "limit": limit,
                "dispatch_mode": dispatch_mode,
                "dry_run": dry_run,
                "confirm_execute": confirm_execute,
                "cohort_filter": cohort_filter,
            }

        if job_type == "quality_checks":
            from src.tasks.quality_checks import run_quality_checks
            fn = run_quality_checks.run if hasattr(run_quality_checks, "run") else run_quality_checks

            dispatch_mode, fallback_used = await _dispatch_with_fallback(
                job_type=job_type,
                job_id=job_id,
                primary_dispatch=lambda: run_quality_checks.delay(job_id=job_id),
                fallback_dispatch=lambda: asyncio.create_task(asyncio.to_thread(fn, job_id=job_id)),
            )
            await _record_dispatch_success(dispatch_mode, fallback_used)
            return {
                "status": "queued",
                "job_type": job_type,
                "requested_job_type": original_job_type,
                "job_id": job_id,
                "limit": limit,
                "dispatch_mode": dispatch_mode,
            }

        if job_type == "truth_validation":
            from src.tasks.truth_validation import run_truth_validation, run_truth_validation_task

            dispatch_mode, fallback_used = await _dispatch_with_fallback(
                job_type=job_type,
                job_id=job_id,
                primary_dispatch=lambda: run_truth_validation_task.delay(
                    job_id=job_id,
                    sample_limit=min(limit, 100),
                    dry_run=dry_run,
                    confirm_execute=confirm_execute,
                ),
                fallback_dispatch=lambda: asyncio.create_task(
                    asyncio.to_thread(
                        run_truth_validation,
                        job_id=job_id,
                        sample_limit=min(limit, 100),
                        dry_run=dry_run,
                        confirm_execute=confirm_execute,
                    )
                ),
            )
            await _record_dispatch_success(dispatch_mode, fallback_used)
            return {
                "status": "queued",
                "job_type": job_type,
                "requested_job_type": original_job_type,
                "job_id": job_id,
                "limit": limit,
                "dispatch_mode": dispatch_mode,
                "dry_run": dry_run,
                "confirm_execute": confirm_execute,
            }

        if job_type == "truth_materialization":
            from src.tasks.truth_materialization import run_truth_materialization, run_truth_materialization_task

            dispatch_mode, fallback_used = await _dispatch_with_fallback(
                job_type=job_type,
                job_id=job_id,
                primary_dispatch=lambda: run_truth_materialization_task.delay(
                    job_id=job_id,
                    limit=limit,
                    dry_run=dry_run,
                    confirm_execute=confirm_execute,
                    sources=selected_sources,
                ),
                fallback_dispatch=lambda: asyncio.create_task(asyncio.to_thread(
                    run_truth_materialization,
                    job_id=job_id,
                    limit=limit,
                    dry_run=dry_run,
                    confirm_execute=confirm_execute,
                    sources=selected_sources,
                )),
            )
            await _record_dispatch_success(dispatch_mode, fallback_used)
            return {
                "status": "queued",
                "job_type": job_type,
                "requested_job_type": original_job_type,
                "job_id": job_id,
                "limit": limit,
                "dispatch_mode": dispatch_mode,
                "dry_run": dry_run,
                "confirm_execute": confirm_execute,
                "sources": selected_sources,
            }

        if job_type == "lead_generation":
            from src.tasks.lead_materialization import generate_leads_job
            fn = generate_leads_job.run if hasattr(generate_leads_job, "run") else generate_leads_job

            dispatch_mode, fallback_used = await _dispatch_with_fallback(
                job_type=job_type,
                job_id=job_id,
                primary_dispatch=lambda: generate_leads_job.delay(job_id=job_id, min_portfolio=1),
                fallback_dispatch=lambda: asyncio.create_task(asyncio.to_thread(fn, job_id=job_id, min_portfolio=1)),
            )
            await _record_dispatch_success(dispatch_mode, fallback_used)
            return {
                "status": "queued",
                "job_type": job_type,
                "requested_job_type": original_job_type,
                "job_id": job_id,
                "limit": limit,
                "dispatch_mode": dispatch_mode,
            }

        if job_type == "lead_reconciliation":
            from src.tasks.reconcile import run_reconciliation_task
            from src.tasks.reconcile import run_reconciliation

            dispatch_mode, fallback_used = await _dispatch_with_fallback(
                job_type=job_type,
                job_id=job_id,
                primary_dispatch=lambda: run_reconciliation_task.delay(job_id=job_id),
                fallback_dispatch=lambda: asyncio.create_task(asyncio.to_thread(run_reconciliation, job_id=job_id)),
            )
            await _record_dispatch_success(dispatch_mode, fallback_used)
            return {
                "status": "queued",
                "job_type": job_type,
                "requested_job_type": original_job_type,
                "job_id": job_id,
                "limit": limit,
                "dispatch_mode": dispatch_mode,
            }

        await _record_dispatch_success("manual", False)
        return {
            "status": "queued",
            "job_type": job_type,
            "requested_job_type": original_job_type,
            "job_id": job_id,
            "limit": limit,
            "dispatch_mode": "manual",
        }
    except Exception as exc:
        await _record_dispatch_failure(exc)
        raise HTTPException(503, f"Failed to dispatch {job_type} job") from exc
