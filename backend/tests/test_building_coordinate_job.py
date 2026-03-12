from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.auth.auth import AuthUser
from src.routers import jobs as jobs_router
from src.tasks import ingest as ingest_tasks
from src.services.building_geocode import BuildingGeocode


class FakeSyncExecuteResult:
    def __init__(self, *, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return self._rows


class FakeSyncSession:
    def __init__(self, rows):
        self.rows = rows
        self.calls: list[tuple[str, dict | None]] = []
        self.commit_count = 0
        self.rollback_count = 0
        self.closed = False

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        if "SELECT bbl, address, borough" in sql:
            return FakeSyncExecuteResult(rows=self.rows)
        return FakeSyncExecuteResult()

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1

    def close(self):
        self.closed = True


class FakeExecuteResult:
    def __init__(self, *, scalar=None):
        self._scalar = scalar

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


def test_backfill_building_coordinates_updates_buildings_and_job_progress(monkeypatch):
    session = FakeSyncSession(
        [
            SimpleNamespace(bbl="1000000001", address="350 5th Ave", borough="MANHATTAN"),
            SimpleNamespace(bbl="1000000002", address="Unknown", borough="MANHATTAN"),
        ]
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(ingest_tasks, "_get_pg_session", lambda: session)
    monkeypatch.setattr(ingest_tasks, "_ensure_or_create_job", lambda *_args, **_kwargs: 77)
    monkeypatch.setattr(ingest_tasks, "_log_quality", lambda *args, **kwargs: captured.setdefault("quality", (args, kwargs)))
    monkeypatch.setattr(ingest_tasks, "_finish_job", lambda *args, **kwargs: captured.setdefault("finish", (args, kwargs)))
    monkeypatch.setattr(ingest_tasks.time, "sleep", lambda *_args, **_kwargs: None)

    def fake_geocode(address: str, borough: str | None = None):
        if address == "350 5th Ave":
            return BuildingGeocode(
                latitude=40.7484,
                longitude=-73.9857,
                coordinate_source="planninglabs",
                coordinate_precision="parcel",
            )
        return None

    monkeypatch.setattr(ingest_tasks, "geocode_building", fake_geocode)

    fn = ingest_tasks.backfill_building_coordinates.run if hasattr(ingest_tasks.backfill_building_coordinates, "run") else ingest_tasks.backfill_building_coordinates
    result = fn(None, job_id=77, limit=10)

    assert result == {"processed": 2, "succeeded": 1, "failed": 1}
    assert session.commit_count >= 2
    assert session.rollback_count == 0
    assert session.closed is True

    update_calls = [params for sql, params in session.calls if "UPDATE buildings" in sql]
    assert update_calls == [
        {
            "bbl": "1000000001",
            "latitude": 40.7484,
            "longitude": -73.9857,
            "coordinate_source": "planninglabs",
            "coordinate_precision": "parcel",
        }
    ]

    finish_args, finish_kwargs = captured["finish"]
    assert finish_args[1:] == (77, "completed", 2, 1, 1)
    assert finish_kwargs == {}


@pytest.mark.anyio
async def test_start_building_coordinates_job_falls_back_to_in_process(monkeypatch):
    session = FakeAsyncSession([FakeExecuteResult(scalar=456)])
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
    monkeypatch.setattr("src.tasks.ingest.backfill_building_coordinates", FailingTask())

    response = await jobs_router.start_job(
        job_type="building_coordinates",
        limit=250,
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response == {
        "status": "queued",
        "job_type": "building_coordinates",
        "requested_job_type": "building_coordinates",
        "job_id": 456,
        "limit": 250,
        "dispatch_mode": "in_process",
    }
    assert session.commit_count == 1
    assert captured["delay_kwargs"] == {"job_id": 456, "limit": 250}

    task = captured["task"]
    await task
    assert captured["run_kwargs"] == {"job_id": 456, "limit": 250}
