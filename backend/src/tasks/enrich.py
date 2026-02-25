"""Enrichment Celery tasks.

Wraps existing enrichment logic to run in background workers
instead of blocking the API server.
"""
import asyncio
import logging
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


@celery_app.task(bind=True, name="src.tasks.enrich.enrich_lead")
def enrich_lead(self, lead_id: str):
    """Enrich a single lead using the existing enrichment cascade."""
    from src.storage.database import get_database
    from src.services.cache_manager import get_cache
    from src.enrich.enricher import Enricher

    db = get_database()
    cache = get_cache()

    lead_data = db.get_lead_by_id(lead_id)
    if not lead_data:
        logger.warning(f"Lead {lead_id} not found for enrichment")
        return {"status": "not_found"}

    lead = None
    for l in cache.leads:
        if l.lead_id == lead_id:
            lead = l
            break

    if not lead:
        logger.warning(f"Lead {lead_id} not in cache")
        return {"status": "not_in_cache"}

    enricher = Enricher()
    result = enricher.enrich_lead(lead, db)
    return {"status": "completed", "lead_id": lead_id}


@celery_app.task(bind=True, name="src.tasks.enrich.enrich_batch")
def enrich_batch(self, lead_ids: list[str]):
    """Enrich a batch of leads sequentially."""
    results = {"completed": 0, "failed": 0}
    for lid in lead_ids:
        try:
            enrich_lead(lid)
            results["completed"] += 1
        except Exception as e:
            logger.error(f"Enrichment failed for {lid}: {e}")
            results["failed"] += 1
    return results
