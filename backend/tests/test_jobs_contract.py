"""Tests for jobs API contract helpers."""

import pytest
from fastapi import HTTPException

from src.auth.auth import AuthUser
from src.routers import jobs as jobs_router
from src.routers.jobs import JOB_TYPE_ALIASES, _normalize_status
from src.services.source_audit import SOURCE_REGISTRY


class NoExecuteSession:
    def __init__(self):
        self.calls = []
        self.commit_count = 0

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        raise AssertionError("approval preview should not touch the database")

    async def commit(self):
        self.commit_count += 1


def test_normalize_status_maps_legacy_completed():
    assert _normalize_status("completed") == "succeeded"


def test_normalize_status_passthrough_for_canonical_values():
    assert _normalize_status("queued") == "queued"
    assert _normalize_status("running") == "running"
    assert _normalize_status("succeeded") == "succeeded"
    assert _normalize_status("failed") == "failed"


def test_normalize_status_handles_none():
    assert _normalize_status(None) == "unknown"


def test_job_type_aliases_cover_source_style_names():
    assert JOB_TYPE_ALIASES["energy_grades"] == "energy"
    assert JOB_TYPE_ALIASES["aep_designations"] == "aep"
    assert JOB_TYPE_ALIASES["eviction_filings"] == "evictions"
    assert JOB_TYPE_ALIASES["facade_inspections"] == "facades"
    assert JOB_TYPE_ALIASES["acris_transactions"] == "acris"


@pytest.mark.anyio
async def test_mutating_jobs_default_to_approval_preview_without_queueing():
    user = AuthUser(user_id="u1", email="test@example.com")
    for job_type in [
        "building_coordinates",
        "board_chairs",
        "acris",
        "enrichment",
        "scoring",
        "smart_lists_evaluation",
        "quality_checks",
        "lead_generation",
        "lead_reconciliation",
    ]:
        session = NoExecuteSession()

        response = await jobs_router.start_job(
            job_type=job_type,
            limit=500,
            session=session,
            user=user,
        )

        assert response["status"] == "approval_required"
        assert response["job_id"] is None
        assert response["approval_required"] is True
        assert response["safe_to_run_automatically"] is False
        assert response["mutations_planned"] == 0
        assert "confirm_execute=true" in response["preview"]["required_execute_query"]
        assert session.calls == []
        assert session.commit_count == 0


@pytest.mark.anyio
async def test_all_refreshable_source_audit_jobs_default_to_approval_preview_without_queueing():
    user = AuthUser(user_id="u1", email="test@example.com")
    job_types = sorted({
        str(source["job_type"])
        for source in SOURCE_REGISTRY
        if source.get("refreshable", True) is not False
    })

    assert "outreach_feedback" not in job_types
    assert job_types

    for job_type in job_types:
        session = NoExecuteSession()

        response = await jobs_router.start_job(
            job_type=job_type,
            limit=500,
            session=session,
            user=user,
        )

        assert response["status"] == "approval_required", job_type
        assert response["job_id"] is None
        assert response["approval_required"] is True
        assert response["safe_to_run_automatically"] is False
        assert response["mutations_planned"] == 0
        assert "confirm_execute=true" in response["preview"]["required_execute_query"]
        assert session.calls == []
        assert session.commit_count == 0


@pytest.mark.anyio
async def test_source_filters_are_limited_to_truth_materialization_jobs():
    user = AuthUser(user_id="u1", email="test@example.com")

    with pytest.raises(HTTPException, match="source filters"):
        await jobs_router.start_job(
            job_type="acris",
            limit=500,
            source=["building_management"],
            session=NoExecuteSession(),
            user=user,
        )
