"""Enrichment Celery tasks.

Wraps existing enrichment logic to run in background workers
instead of blocking the API server.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    from src.worker import app as celery_app
except ImportError:
    class _FakeCelery:
        @staticmethod
        def task(*args, **kwargs):
            return lambda fn: fn
    celery_app = _FakeCelery()

logger = logging.getLogger(__name__)


def _job_status_from_counts(succeeded: int, failed: int) -> str:
    if failed > 0 and succeeded == 0:
        return "failed"
    return "succeeded"


async def _run_enrichment_job_async(job_id: int, limit: int) -> dict[str, Any]:
    from sqlalchemy import text
    from src.db.session import get_session_factory
    from src.routers.leads import enrich_lead_all_core

    session_factory = get_session_factory()
    safe_limit = max(1, int(limit or 1))

    async with session_factory() as session:
        await session.execute(
            text("""
                UPDATE ingestion_jobs
                SET status = 'running',
                    started_at = COALESCE(started_at, now()),
                    processed = 0,
                    succeeded = 0,
                    failed = 0,
                    error = NULL,
                    updated_at = now()
                WHERE id = :job_id
            """),
            {"job_id": job_id},
        )
        result = await session.execute(
            text("""
                SELECT lead_id
                FROM leads
                WHERE COALESCE(enrichment_status, 'none') IN ('none', 'pending', 'failed')
                ORDER BY score DESC NULLS LAST, updated_at ASC NULLS FIRST
                LIMIT :limit
            """),
            {"limit": safe_limit},
        )
        lead_ids = [r[0] for r in result.fetchall()]
        total = len(lead_ids)
        await session.execute(
            text("""
                UPDATE ingestion_jobs
                SET total = :total, updated_at = now()
                WHERE id = :job_id
            """),
            {"job_id": job_id, "total": total},
        )
        await session.commit()

    processed = 0
    succeeded = 0
    failed = 0
    last_error: str | None = None

    for lead_id in lead_ids:
        async with session_factory() as session:
            try:
                await enrich_lead_all_core(lead_id=lead_id, session=session)
                succeeded += 1
            except Exception as exc:  # pragma: no cover - defensive task isolation
                failed += 1
                last_error = str(exc)[:500]
                logger.warning("Enrichment failed for lead %s: %s", lead_id, exc)
            finally:
                processed += 1
                await session.execute(
                    text("""
                        UPDATE ingestion_jobs
                        SET processed = :processed,
                            succeeded = :succeeded,
                            failed = :failed,
                            status = 'running',
                            error = :error,
                            updated_at = now()
                        WHERE id = :job_id
                    """),
                    {
                        "job_id": job_id,
                        "processed": processed,
                        "succeeded": succeeded,
                        "failed": failed,
                        "error": last_error,
                    },
                )
                await session.commit()

    async with session_factory() as session:
        final_status = _job_status_from_counts(succeeded=succeeded, failed=failed)
        await session.execute(
            text("""
                UPDATE ingestion_jobs
                SET status = :status,
                    processed = :processed,
                    succeeded = :succeeded,
                    failed = :failed,
                    error = :error,
                    finished_at = now(),
                    updated_at = now()
                WHERE id = :job_id
            """),
            {
                "job_id": job_id,
                "status": final_status,
                "processed": processed,
                "succeeded": succeeded,
                "failed": failed,
                "error": last_error,
            },
        )
        await session.commit()

    return {
        "job_id": job_id,
        "status": _job_status_from_counts(succeeded=succeeded, failed=failed),
        "total": len(lead_ids),
        "processed": processed,
        "succeeded": succeeded,
        "failed": failed,
    }


@celery_app.task(bind=True, name="src.tasks.enrich.run_enrichment_job")
def run_enrichment_job(self, job_id: int, limit: int = 500):
    """Run batch enrichment for top leads and update ingestion_jobs lifecycle."""
    return asyncio.run(_run_enrichment_job_async(job_id=job_id, limit=limit))


def _build_dos_cache_payload(corporate_owner_name: str, dos_entity: Any, officers: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "lookup_name": corporate_owner_name,
        "dos_id": getattr(dos_entity, "dos_id", None),
        "entity_name": getattr(dos_entity, "name", None),
        "entity_type": getattr(dos_entity, "entity_type", None),
        "officers": officers or [],
        "ceo_name": getattr(dos_entity, "ceo_name", None),
        "ceo_address": getattr(dos_entity, "ceo_address", None),
        "snapshot_as_of": datetime.now(timezone.utc).date().isoformat(),
    }


@celery_app.task(bind=True, name="src.tasks.enrich.refresh_dos_contacts")
def refresh_dos_contacts(self, bbl: str, corporate_owner_name: str):
    """Refresh DOS officers into dos_cache for a building."""
    from sqlalchemy import create_engine, text
    from src.db.session import get_sync_url
    from src.enrich.ny_dos import NYDOSClient

    owner_name = (corporate_owner_name or "").strip()
    if not bbl or not owner_name:
        return {"status": "skipped", "reason": "missing_bbl_or_owner"}

    client = NYDOSClient(app_token=os.environ.get("NY_DATA_APP_TOKEN"))
    dos_entity = client.lookup_entity(owner_name)
    if not dos_entity:
        payload = {
            "lookup_name": owner_name,
            "officers": [],
            "ceo_name": None,
            "ceo_address": None,
            "snapshot_as_of": datetime.now(timezone.utc).date().isoformat(),
        }
    else:
        officers = client.get_filing_officers(dos_entity.dos_id)
        payload = _build_dos_cache_payload(
            corporate_owner_name=owner_name,
            dos_entity=dos_entity,
            officers=officers,
        )

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=30)
    cache_key = f"officers:{bbl}"

    engine = create_engine(get_sync_url())
    try:
        with engine.begin() as conn:
            params = {
                "cache_key": cache_key,
                "result": json.dumps(payload),
                "cached_at": now,
                "expires_at": expires_at,
            }
            try:
                conn.execute(
                    text(
                        """
                        INSERT INTO dos_cache (cache_key, result, cached_at, expires_at)
                        VALUES (:cache_key, :result, :cached_at, :expires_at)
                        ON CONFLICT (cache_key)
                        DO UPDATE SET
                            result = EXCLUDED.result,
                            cached_at = EXCLUDED.cached_at,
                            expires_at = EXCLUDED.expires_at
                        """
                    ),
                    params,
                )
            except Exception:
                conn.execute(
                    text(
                        """
                        INSERT INTO dos_cache (cache_key, result, cached_at)
                        VALUES (:cache_key, :result, :cached_at)
                        ON CONFLICT (cache_key)
                        DO UPDATE SET
                            result = EXCLUDED.result,
                            cached_at = EXCLUDED.cached_at
                        """
                    ),
                    {
                        "cache_key": cache_key,
                        "result": json.dumps(payload),
                        "cached_at": now,
                    },
                )
    finally:
        engine.dispose()

    return {
        "status": "success",
        "bbl": bbl,
        "cache_key": cache_key,
        "officers_count": len(payload.get("officers") or []),
    }


@celery_app.task(bind=True, name="src.tasks.enrich.enrich_lead")
def enrich_lead(self, lead_id: str):
    """Enrich a single lead in worker-safe async DB context."""
    from src.db.session import get_session_factory
    from src.routers.leads import enrich_lead_all_core

    async def _run() -> dict[str, Any]:
        session_factory = get_session_factory()
        async with session_factory() as session:
            try:
                await enrich_lead_all_core(lead_id=lead_id, session=session)
                return {"status": "completed", "lead_id": lead_id}
            except Exception as exc:
                logger.exception("Enrichment failed for lead %s: %s", lead_id, exc)
                return {"status": "failed", "lead_id": lead_id, "error": str(exc)[:500]}

    return asyncio.run(_run())


@celery_app.task(bind=True, name="src.tasks.enrich.enrich_batch")
def enrich_batch(self, lead_ids: list[str]):
    """Enrich a batch of leads; queue via Celery when available."""
    results = {"queued": 0, "completed": 0, "failed": 0}
    can_queue = hasattr(enrich_lead, "delay")
    for lid in lead_ids:
        try:
            if can_queue:
                enrich_lead.delay(lid)
                results["queued"] += 1
            else:
                outcome = enrich_lead(None, lid)
                if outcome.get("status") == "completed":
                    results["completed"] += 1
                else:
                    results["failed"] += 1
        except Exception as exc:
            logger.error("Enrichment failed for %s: %s", lid, exc)
            results["failed"] += 1
    if can_queue:
        results["status"] = "queued"
    else:
        results["status"] = "completed"
    return results
