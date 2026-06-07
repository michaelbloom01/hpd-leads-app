"""Regression coverage for enrichment job status via the current jobs API."""

import asyncio

from src.auth.auth import AuthUser
from src.routers import jobs as jobs_router


class FakeMappingRow:
    def __init__(self, data):
        self._mapping = data


class FakeAsyncSession:
    def __init__(self):
        self.calls = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return [FakeMappingRow({
            "id": 7,
            "job_type": "enrichment",
            "source": "enrichment",
            "status": "completed",
            "total": 10,
            "processed": 10,
            "succeeded": 9,
            "failed": 1,
            "error": None,
            "started_at": None,
            "finished_at": None,
            "config": {"dispatch": {"state": "done", "mode": "worker"}},
        })]


def test_jobs_router_accepts_slash_and_no_slash_list_routes():
    paths = {route.path for route in jobs_router.router.routes}

    assert "/api/v1/jobs" in paths
    assert "/api/v1/jobs/" in paths


def test_enrichment_job_list_normalizes_completed_status():
    session = FakeAsyncSession()
    rows = asyncio.run(
        jobs_router.list_jobs(
            job_type="enrichment",
            limit=1,
            session=session,
            user=AuthUser(user_id="admin", email="admin@test.com", role="admin"),
        )
    )

    assert rows[0]["status"] == "succeeded"
    assert rows[0]["dispatch_state"] == "done"
    assert rows[0]["dispatch_mode"] == "worker"
    assert session.calls[0][1]["jtype"] == "enrichment"
