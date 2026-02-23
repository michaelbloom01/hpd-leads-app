"""Enrichment Celery tasks.

Wraps existing enrichment logic to run in background workers
instead of blocking the API server.
"""
import logging

from src.worker import app as celery_app

logger = logging.getLogger(__name__)


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
