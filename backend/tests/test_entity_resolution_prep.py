from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException

from src.auth.auth import AuthUser
from src.routers import jobs as jobs_router
from src.tasks import entity_resolution


def _cluster() -> dict:
    return {
        "canonical_name": "ACME MANAGEMENT",
        "all_names": ["ACME MANAGEMENT"],
        "bbls": ["1000000001"],
        "portfolio_size": 1,
        "node_count": 2,
        "graph_json": {
            "nodes": [
                {"id": "corp:ACME MANAGEMENT", "type": "corporation", "name": "ACME MANAGEMENT"},
                {"id": "person:JANE DOE", "type": "person", "name": "JANE DOE"},
            ],
            "edges": [],
        },
    }


def test_classifies_single_keeper_cluster_as_safe_keep():
    row = {
        "lead_id": "lead-1",
        "display_name": "Acme Management",
        "normalized_name": "ACME MANAGEMENT",
        "current_link_count": 3,
        "overlapping_current_links": 1,
        "has_active_links": True,
        "blank_display_name": False,
        "has_user_state": False,
    }

    result = entity_resolution._classify_cluster_candidates(_cluster(), [row])

    assert result["bucket"] == "safe_keep"
    assert result["keeper_lead_id"] == "lead-1"
    assert result["signals"]["safe_to_execute"] is True


def test_classifies_blank_zero_link_tail_as_safe_retire():
    keeper = {
        "lead_id": "lead-1",
        "display_name": "Acme Management",
        "normalized_name": "ACME MANAGEMENT",
        "current_link_count": 2,
        "overlapping_current_links": 1,
        "has_active_links": True,
        "blank_display_name": False,
        "has_user_state": False,
    }
    blank_tail = {
        "lead_id": "lead-2",
        "display_name": "lead-2",
        "normalized_name": "ACME MANAGEMENT",
        "current_link_count": 0,
        "overlapping_current_links": 0,
        "has_active_links": False,
        "blank_display_name": True,
        "has_user_state": False,
    }

    result = entity_resolution._classify_cluster_candidates(_cluster(), [keeper, blank_tail])

    assert result["bucket"] == "safe_retire"
    assert result["keeper_lead_id"] == "lead-1"
    assert result["zero_link_sibling_count"] == 1


def test_classifies_user_state_sibling_as_review_required():
    keeper = {
        "lead_id": "lead-1",
        "display_name": "Acme Management",
        "normalized_name": "ACME MANAGEMENT",
        "current_link_count": 2,
        "overlapping_current_links": 1,
        "has_active_links": True,
        "blank_display_name": False,
        "has_user_state": False,
    }
    sibling = {
        "lead_id": "lead-2",
        "display_name": "Acme Mgmt",
        "normalized_name": "ACME MANAGEMENT",
        "current_link_count": 0,
        "overlapping_current_links": 0,
        "has_active_links": False,
        "blank_display_name": False,
        "has_user_state": True,
    }

    result = entity_resolution._classify_cluster_candidates(_cluster(), [keeper, sibling])

    assert result["bucket"] == "review_required"
    assert "non_keeper_has_user_state" in result["blocked_reasons"]


class _DummyEngine:
    def __init__(self):
        self.disposed = False

    def dispose(self):
        self.disposed = True


class _NoWriteSession:
    def __init__(self):
        self.engine = _DummyEngine()
        self.commit_count = 0
        self.rollback_count = 0
        self.closed = False

    def execute(self, statement, params=None):
        sql = str(statement)
        if "UPDATE leads SET" in sql or "INSERT INTO building_management" in sql:
            raise AssertionError(f"Unexpected write SQL during dry run: {sql}")
        raise AssertionError(f"Unexpected SQL execution during dry run: {sql}")

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1

    def close(self):
        self.closed = True

    def get_bind(self):
        return self.engine


def test_resolve_entities_dry_run_does_not_write(monkeypatch):
    session = _NoWriteSession()
    captured: dict[str, object] = {}

    monkeypatch.setattr(entity_resolution, "_get_pg_session", lambda: session)
    monkeypatch.setattr(entity_resolution, "_build_entity_graph", lambda _session: object())
    monkeypatch.setattr(entity_resolution, "_resolve_clusters", lambda _graph: [_cluster()])
    monkeypatch.setattr(
        entity_resolution,
        "_classify_cluster_for_prep",
        lambda _session, _cluster_value: {
            "canonical_name": "ACME MANAGEMENT",
            "bucket": "safe_keep",
            "keeper_lead_id": "lead-1",
            "portfolio_size": 1,
            "candidate_lead_count": 1,
            "blocked_reasons": [],
            "candidate_leads": [],
            "signals": {"safe_to_execute": True},
        },
    )
    monkeypatch.setattr(entity_resolution, "_store_job_config", lambda _session, _job_id, config: captured.setdefault("config", config))
    monkeypatch.setattr("src.tasks.ingest._ensure_or_create_job", lambda *_args, **_kwargs: 77)
    monkeypatch.setattr("src.tasks.ingest._finish_job", lambda *args, **kwargs: captured.setdefault("finish", (args, kwargs)))

    result = entity_resolution.resolve_entities(None, job_id=77, dry_run=True, confirm_execute=False)

    assert result["mode"] == "dry_run"
    assert result["updated"] == 0
    assert str(result["run_id"]).startswith("entity-resolution-77-")
    assert result["preview_counts"]["safe_keep"] == 1
    assert result["guardrails"]["requires_confirm_execute"] is True
    assert result["samples"]["safe_keep"][0]["canonical_name"] == "ACME MANAGEMENT"
    assert result["rollback_strategy"] == "Dry-run mode made no changes."
    assert str(captured["config"]["run_id"]).startswith("entity-resolution-77-")
    assert captured["config"]["write_permitted"] is False
    assert captured["config"]["preview"]["samples"]["safe_keep"][0]["keeper_lead_id"] == "lead-1"
    assert "review_required" in captured["config"]["rollback_strategy"]
    finish_args, finish_kwargs = captured["finish"]
    assert finish_args[1:] == (77, "completed", 1, 0, 0)
    assert finish_kwargs == {}
    assert session.closed is True
    assert session.engine.disposed is True


class _FakeExecuteResult:
    def __init__(self, *, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def fetchall(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._scalar


class _FakeAsyncSession:
    def __init__(self, results):
        self._results = list(results)
        self.calls: list[tuple[str, dict | None]] = []
        self.commit_count = 0

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        if not self._results:
            raise AssertionError("Unexpected execute call")
        return self._results.pop(0)

    async def commit(self):
        self.commit_count += 1


@pytest.mark.anyio
async def test_start_entity_resolution_job_defaults_to_dry_run(monkeypatch):
    session = _FakeAsyncSession(
        [
            _FakeExecuteResult(rows=[]),
            _FakeExecuteResult(scalar=456),
            _FakeExecuteResult(),
        ]
    )
    captured: dict[str, object] = {}

    class FailingTask:
        def delay(self, **kwargs):
            captured["delay_kwargs"] = kwargs
            raise RuntimeError("celery unavailable")

        def run(self, **kwargs):
            captured["run_kwargs"] = kwargs
            return {"ok": True}

    def fake_create_task(coro):
        loop = asyncio.get_running_loop()
        task = loop.create_task(coro)
        captured["task"] = task
        return task

    monkeypatch.setattr(jobs_router.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr("src.tasks.entity_resolution.resolve_entities", FailingTask())

    response = await jobs_router.start_job(
        job_type="entity_resolution",
        limit=250,
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response == {
        "status": "queued",
        "job_type": "entity_resolution",
        "requested_job_type": "entity_resolution",
        "job_id": 456,
        "limit": 250,
        "dispatch_mode": "in_process",
        "dry_run": True,
        "confirm_execute": False,
        "cohort_filter": None,
    }
    assert session.commit_count == 2
    assert captured["delay_kwargs"] == {
        "job_id": 456,
        "dry_run": True,
        "confirm_execute": False,
        "cohort_filter": None,
    }
    assert any("UPDATE ingestion_jobs SET config" in sql for sql, _params in session.calls)
    await captured["task"]
    assert captured["run_kwargs"] == {
        "job_id": 456,
        "dry_run": True,
        "confirm_execute": False,
        "cohort_filter": None,
    }


@pytest.mark.anyio
async def test_start_entity_resolution_rejects_unconfirmed_execute():
    session = _FakeAsyncSession([])

    with pytest.raises(HTTPException) as exc:
        await jobs_router.start_job(
            job_type="entity_resolution",
            limit=100,
            dry_run=False,
            confirm_execute=False,
            cohort_filter="safe_keep",
            session=session,
            user=AuthUser(user_id="u1", email="test@example.com"),
        )

    assert exc.value.status_code == 400
    assert "confirm_execute=true" in str(exc.value.detail)


@pytest.mark.anyio
async def test_start_entity_resolution_rejects_duplicate_inflight_job():
    session = _FakeAsyncSession(
        [
            _FakeExecuteResult(
                rows=[
                    (
                        123,
                        "queued",
                        json.dumps(
                            {
                                "request": {
                                    "job_type": "entity_resolution",
                                    "requested_job_type": "entity_resolution",
                                    "limit": 250,
                                    "dry_run": True,
                                    "confirm_execute": False,
                                    "cohort_filter": None,
                                }
                            }
                        ),
                    )
                ]
            )
        ]
    )

    with pytest.raises(HTTPException) as exc:
        await jobs_router.start_job(
            job_type="entity_resolution",
            limit=250,
            session=session,
            user=AuthUser(user_id="u1", email="test@example.com"),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["job_id"] == 123
    assert session.commit_count == 0
