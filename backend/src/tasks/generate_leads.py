"""Lead generation task entrypoint.

This module provides a reusable callable that both scripts and background jobs
can use to materialize leads from buildings + contacts.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

try:
    from src.worker import app as celery_app
except ImportError:
    class _FakeCelery:
        @staticmethod
        def task(*args, **kwargs):
            return lambda fn: fn
    celery_app = _FakeCelery()

logger = logging.getLogger(__name__)


def run_generate_leads(min_portfolio: int = 1) -> dict[str, Any]:
    from scripts.generate_leads_from_buildings import main as generate_main

    result = generate_main(min_portfolio=min_portfolio)
    if isinstance(result, dict):
        return result
    return {"status": "completed"}


@celery_app.task(bind=True, name="src.tasks.generate_leads.run_generate_leads_task")
def run_generate_leads_task(self, min_portfolio: int = 1, job_id: Optional[int] = None) -> dict[str, Any]:
    """Queue-safe wrapper used by ingestion/job orchestration."""
    logger.info("Running lead generation task (min_portfolio=%s)", min_portfolio)
    payload = run_generate_leads(min_portfolio=min_portfolio)
    if job_id is not None:
        payload["job_id"] = job_id
    return payload

