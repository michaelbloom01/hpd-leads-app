from __future__ import annotations

import asyncio
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.auth.auth import AuthUser
from src.routers import leads as leads_router
from src.schemas.requests import BatchPipelineStageUpdateRequest, EnrichmentRequest


def _make_request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/api/test", "headers": []})


MINIMAL_LEAD_ROW = {
    "lead_id": "lead-1",
    "company_name": "Example Management",
    "agent_name": "Example Management",
    "owner_name": "",
    "owner_type": "property_manager",
    "portfolio_size": 3,
    "total_units": 42,
    "website": None,
    "score": 0.0,
    "enrichment_status": "none",
    "pipeline_stage": "research",
}


class FakeExecuteResult:
    def __init__(self, *, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def fetchall(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._scalar


class FakeAsyncSession:
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


class FakeMappingRow:
    def __init__(self, mapping):
        self._mapping = mapping


@pytest.mark.anyio
async def test_update_leads_pipeline_stage_batch_updates_found_ids_only():
    session = FakeAsyncSession(
        [
            FakeExecuteResult(rows=[("lead-1",), ("lead-2",)]),
            FakeExecuteResult(),
        ]
    )

    response = await leads_router.update_leads_pipeline_stage_batch(
        request=_make_request(),
        body=BatchPipelineStageUpdateRequest(
            lead_ids=["lead-1", " lead-2 ", "", "missing-lead"],
            pipeline_stage="first_contact",
        ),
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response == {
        "status": "success",
        "pipeline_stage": "first_contact",
        "updated_count": 2,
        "missing_lead_ids": ["missing-lead"],
    }
    assert session.commit_count == 1
    assert "UPDATE leads" in session.calls[1][0]
    assert session.calls[1][1]["ps"] == "first_contact"


@pytest.mark.anyio
async def test_update_leads_pipeline_stage_batch_rejects_empty_ids():
    session = FakeAsyncSession([])

    with pytest.raises(HTTPException) as exc:
        await leads_router.update_leads_pipeline_stage_batch(
            request=_make_request(),
            body=BatchPipelineStageUpdateRequest(lead_ids=[" ", ""], pipeline_stage="research"),
            session=session,
            user=AuthUser(user_id="u1", email="test@example.com"),
        )

    assert exc.value.status_code == 400
    assert "lead_ids must contain at least one lead id" in str(exc.value.detail)


@pytest.mark.anyio
async def test_update_leads_pipeline_stage_batch_rejects_invalid_stage():
    session = FakeAsyncSession([])

    with pytest.raises(HTTPException) as exc:
        await leads_router.update_leads_pipeline_stage_batch(
            request=_make_request(),
            body=BatchPipelineStageUpdateRequest(lead_ids=["lead-1"], pipeline_stage="invalid_stage"),
            session=session,
            user=AuthUser(user_id="u1", email="test@example.com"),
        )

    assert exc.value.status_code == 400
    assert "Invalid pipeline stage" in str(exc.value.detail)


@pytest.mark.anyio
async def test_update_leads_pipeline_stage_batch_rejects_all_missing_ids():
    session = FakeAsyncSession([FakeExecuteResult(rows=[])])

    with pytest.raises(HTTPException) as exc:
        await leads_router.update_leads_pipeline_stage_batch(
            request=_make_request(),
            body=BatchPipelineStageUpdateRequest(lead_ids=["missing-1", "missing-2"], pipeline_stage="research"),
            session=session,
            user=AuthUser(user_id="u1", email="test@example.com"),
        )

    assert exc.value.status_code == 404
    assert "No matching leads found" in str(exc.value.detail)


@pytest.mark.anyio
async def test_start_selected_leads_enrichment_defaults_to_approval_preview():
    session = FakeAsyncSession([FakeExecuteResult(rows=[("lead-1",), ("lead-2",)])])

    response = await leads_router.start_selected_leads_enrichment(
        request=_make_request(),
        body=EnrichmentRequest(lead_ids=["lead-1", "lead-2", "missing-lead"]),
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["status"] == "approval_required"
    assert response["job_id"] is None
    assert response["target_count"] == 2
    assert response["missing_lead_ids"] == ["missing-lead"]
    assert response["approval_required"] is True
    assert response["safe_to_run_automatically"] is False
    assert response["mutations_planned"] == 0
    assert response["preview"]["operation"] == "selected_lead_enrichment"
    assert session.commit_count == 0


@pytest.mark.anyio
async def test_enrich_lead_all_defaults_to_approval_preview_without_mutation():
    session = FakeAsyncSession([FakeExecuteResult(rows=[FakeMappingRow(MINIMAL_LEAD_ROW)])])

    response = await leads_router.enrich_lead_all(
        request=_make_request(),
        lead_id="lead-1",
        dry_run=True,
        confirm_execute=False,
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["status"] == "approval_required"
    assert response["lead_id"] == "lead-1"
    assert response["approval_required"] is True
    assert response["safe_to_run_automatically"] is False
    assert response["mutations_planned"] == 0
    assert response["preview"]["operation"] == "lead_enrichment"
    assert "confirm_execute=true" in response["preview"]["required_execute_query"]
    assert response["lead"]["company_name"] == "Example Management"
    assert session.commit_count == 0
    assert len(session.calls) == 1


@pytest.mark.anyio
async def test_enrich_lead_all_rejects_execute_without_confirmation():
    session = FakeAsyncSession([])

    with pytest.raises(HTTPException) as exc:
        await leads_router.enrich_lead_all(
            request=_make_request(),
            lead_id="lead-1",
            dry_run=False,
            confirm_execute=False,
            session=session,
            user=AuthUser(user_id="u1", email="test@example.com"),
        )

    assert exc.value.status_code == 400
    assert "confirm_execute=true" in str(exc.value.detail)
    assert session.commit_count == 0
    assert session.calls == []


@pytest.mark.anyio
async def test_start_selected_leads_enrichment_falls_back_to_in_process(monkeypatch):
    session = FakeAsyncSession(
        [
            FakeExecuteResult(rows=[("lead-1",), ("lead-2",)]),
            FakeExecuteResult(scalar=123),
        ]
    )

    captured: dict[str, object] = {}

    class FailingTask:
        def delay(self, **kwargs):
            captured["delay_kwargs"] = kwargs
            raise RuntimeError("celery unavailable")

    async def fake_run_enrichment_job_async(**kwargs):
        captured["fallback_kwargs"] = kwargs
        return {"ok": True}

    def fake_create_task(coro):
        captured["task_created"] = True
        loop = asyncio.get_running_loop()
        task = loop.create_task(coro)
        captured["task"] = task
        return task

    monkeypatch.setattr(leads_router.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr("src.tasks.enrich.run_enrichment_job", FailingTask())
    monkeypatch.setattr("src.tasks.enrich._run_enrichment_job_async", fake_run_enrichment_job_async)

    response = await leads_router.start_selected_leads_enrichment(
        request=_make_request(),
        body=EnrichmentRequest(
            lead_ids=["lead-1", "lead-2", "lead-1", "missing-lead"],
            dry_run=False,
            confirm_execute=True,
        ),
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response == {
        "status": "queued",
        "job_id": 123,
        "target_count": 2,
        "missing_lead_ids": ["missing-lead"],
        "dispatch_mode": "in_process",
    }
    assert session.commit_count == 1
    assert captured["delay_kwargs"] == {"job_id": 123, "limit": 2, "lead_ids": ["lead-1", "lead-2"]}
    task = captured["task"]
    await task
    assert captured["fallback_kwargs"] == {"job_id": 123, "limit": 2, "lead_ids": ["lead-1", "lead-2"]}


@pytest.mark.anyio
async def test_start_selected_leads_enrichment_rejects_empty_ids():
    session = FakeAsyncSession([])

    with pytest.raises(HTTPException) as exc:
        await leads_router.start_selected_leads_enrichment(
            request=_make_request(),
            body=EnrichmentRequest(lead_ids=[" ", ""]),
            session=session,
            user=AuthUser(user_id="u1", email="test@example.com"),
        )

    assert exc.value.status_code == 400
    assert "lead_ids must contain at least one lead id" in str(exc.value.detail)


@pytest.mark.anyio
async def test_start_selected_leads_enrichment_rejects_all_missing_ids():
    session = FakeAsyncSession([FakeExecuteResult(rows=[])])

    with pytest.raises(HTTPException) as exc:
        await leads_router.start_selected_leads_enrichment(
            request=_make_request(),
            body=EnrichmentRequest(lead_ids=["missing-1"]),
            session=session,
            user=AuthUser(user_id="u1", email="test@example.com"),
        )

    assert exc.value.status_code == 404
    assert "No matching leads found" in str(exc.value.detail)


@pytest.mark.anyio
async def test_start_selected_leads_enrichment_prefers_celery_dispatch(monkeypatch):
    session = FakeAsyncSession(
        [
            FakeExecuteResult(rows=[("lead-1",)]),
            FakeExecuteResult(scalar=321),
        ]
    )
    captured: dict[str, object] = {}

    class SuccessTask:
        def delay(self, **kwargs):
            captured["delay_kwargs"] = kwargs
            return {"queued": True}

    monkeypatch.setattr("src.tasks.enrich.run_enrichment_job", SuccessTask())

    response = await leads_router.start_selected_leads_enrichment(
        request=_make_request(),
        body=EnrichmentRequest(lead_ids=["lead-1"], dry_run=False, confirm_execute=True),
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response == {
        "status": "queued",
        "job_id": 321,
        "target_count": 1,
        "missing_lead_ids": [],
        "dispatch_mode": "celery",
    }
    assert session.commit_count == 1
    assert captured["delay_kwargs"] == {"job_id": 321, "limit": 1, "lead_ids": ["lead-1"]}
