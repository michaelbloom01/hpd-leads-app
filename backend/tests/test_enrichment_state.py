"""
Regression tests for enrichment run-state lifecycle.

These tests guard against background/enrichment jobs getting stuck in
`running=True` after early-exit conditions.
"""
import asyncio
from datetime import datetime

from fastapi import BackgroundTasks

from src.routers import enrichment as enrichment_router
from src.services.cache_manager import get_cache
from src.transform.aggregate import Lead


def _reset_enrichment_state(cache):
    cache.enrichment.reset({
        "running": False,
        "phase": "",
        "progress": 0,
        "total": 0,
        "completed": 0,
        "failed": 0,
        "dos_completed": 0,
        "dos_found": 0,
        "web_completed": 0,
        "web_found": 0,
        "current_lead": None,
        "started_at": None,
        "finished_at": None,
        "error": None,
    })
    cache.enrichment_stop_requested = False


def _make_lead(lead_id: str, enrichment_status: str = "none") -> Lead:
    return Lead(
        lead_id=lead_id,
        agent_name=f"Test {lead_id}",
        owner_name="",
        owner_type="Agent",
        portfolio_size=10,
        total_units=100,
        buildings=["123 Test St"],
        building_ids=[lead_id],
        score=50.0,
        enrichment_status=enrichment_status,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )


def test_enrich_batch_full_no_candidates_resets_running():
    cache = get_cache()
    _reset_enrichment_state(cache)
    cache.leads = [_make_lead("a", enrichment_status="complete")]

    result = asyncio.run(
        enrichment_router.enrich_batch_full(
            BackgroundTasks(),
            limit=50,
            min_units=40,
            min_score=None,
        )
    )

    assert result["status"] == "no_candidates"
    state = cache.enrichment.snapshot()
    assert state["running"] is False
    assert state["phase"] == ""
    assert state["total"] == 0
    assert state["progress"] == 0


def test_background_enrichment_no_candidates_resets_running():
    cache = get_cache()
    _reset_enrichment_state(cache)
    cache.leads = [_make_lead("a", enrichment_status="complete")]
    cache.enrichment.update(running=True, phase="queued", total=1, progress=1)

    enrichment_router._run_background_enrichment(limit=25, min_score=None)

    state = cache.enrichment.snapshot()
    assert state["running"] is False
    assert state["phase"] == ""
    assert state["total"] == 0
    assert state["progress"] == 0


def test_paused_job_not_marked_completed(monkeypatch):
    cache = get_cache()
    _reset_enrichment_state(cache)
    lead_1 = _make_lead("lead-1", enrichment_status="none")
    lead_2 = _make_lead("lead-2", enrichment_status="none")
    cache.leads = [lead_1, lead_2]

    class FakeDB:
        def __init__(self):
            self.status_updates = []

        def save_leads(self, _leads):
            return None

        def update_enrichment_job(self, _job_id, **kwargs):
            if "status" in kwargs:
                self.status_updates.append(kwargs["status"])

        def set_setting(self, _key, _value):
            return None

    fake_db = FakeDB()

    class StopAfterFirstEnricher:
        def __init__(self, use_cache=True):
            self.use_cache = use_cache

        def enrich_lead(self, lead):
            # Simulate a stop request that happens during processing.
            cache.enrichment_stop_requested = True
            lead.enrichment_status = "partial"
            return lead

    monkeypatch.setattr(enrichment_router, "get_database", lambda: fake_db)
    monkeypatch.setattr(enrichment_router, "Enricher", StopAfterFirstEnricher)

    enrichment_router._run_background_enrichment_with_job(
        job_id=123,
        lead_ids_remaining={lead_1.lead_id, lead_2.lead_id},
        min_score=None,
    )

    assert "paused" in fake_db.status_updates
    assert "completed" not in fake_db.status_updates
