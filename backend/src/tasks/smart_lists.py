"""Background tasks for Smart List automation."""

import asyncio
import logging
from typing import Any

from sqlalchemy import text

try:
    from src.worker import app as celery_app
except ImportError:  # pragma: no cover - local fallback when celery unavailable
    from celery import Celery

    celery_app = Celery("smart_lists_fallback")

logger = logging.getLogger(__name__)


async def _run_auto_evaluate_job_async(job_id: int, limit: int = 200) -> dict[str, Any]:
    from src.db.session import get_session_factory
    from src.routers.smart_lists import evaluate_due_auto_lists

    session_factory = get_session_factory()
    safe_limit = max(1, int(limit or 1))
    async with session_factory() as session:
        await session.execute(
            text(
                """
                UPDATE ingestion_jobs
                SET status = 'running',
                    source = 'smart_lists',
                    started_at = COALESCE(started_at, now()),
                    processed = 0,
                    succeeded = 0,
                    failed = 0,
                    error = NULL,
                    updated_at = now()
                WHERE id = :job_id
                """
            ),
            {"job_id": job_id},
        )
        await session.commit()

    processed = 0
    failed = 0
    last_error = None
    results: list[dict] = []
    try:
        async with session_factory() as session:
            results = await evaluate_due_auto_lists(session, limit=safe_limit)
            processed = len(results)
            await session.execute(
                text(
                    """
                    UPDATE ingestion_jobs
                    SET status = 'succeeded',
                        total = :total,
                        processed = :processed,
                        succeeded = :succeeded,
                        failed = :failed,
                        error = NULL,
                        finished_at = now(),
                        updated_at = now()
                    WHERE id = :job_id
                    """
                ),
                {
                    "job_id": job_id,
                    "total": processed,
                    "processed": processed,
                    "succeeded": processed,
                    "failed": failed,
                },
            )
            await session.commit()
    except Exception as exc:  # pragma: no cover - defensive worker boundary
        failed = 1
        last_error = str(exc)[:500]
        logger.exception("Smart list auto-evaluation failed: %s", exc)
        async with session_factory() as session:
            await session.execute(
                text(
                    """
                    UPDATE ingestion_jobs
                    SET status = 'failed',
                        total = :total,
                        processed = :processed,
                        succeeded = :succeeded,
                        failed = :failed,
                        error = :error,
                        finished_at = now(),
                        updated_at = now()
                    WHERE id = :job_id
                    """
                ),
                {
                    "job_id": job_id,
                    "total": processed,
                    "processed": processed,
                    "succeeded": max(0, processed - failed),
                    "failed": failed,
                    "error": last_error,
                },
            )
            await session.commit()
    return {
        "job_id": job_id,
        "status": "failed" if failed else "succeeded",
        "evaluated": processed,
        "results": results[:20],
        "error": last_error,
    }


@celery_app.task(bind=True, name="src.tasks.smart_lists.run_auto_evaluate_job")
def run_auto_evaluate_job(self, job_id: int, limit: int = 200):
    """Evaluate due smart lists in background and track job lifecycle."""
    return asyncio.run(_run_auto_evaluate_job_async(job_id=job_id, limit=limit))
