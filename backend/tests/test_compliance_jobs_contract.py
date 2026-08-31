"""Bounded compliance dispatch and no-business-write preview contracts."""

import json
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.auth.auth import AuthUser
from src.routers import jobs


class Result:
    def __init__(self, rows=()):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def scalar_one(self):
        return 77


class Session:
    def __init__(self, rows=()):
        self.rows = rows
        self.calls = []
        self.commits = 0

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return Result(self.rows if "SELECT id, status, config" in str(statement) else ())

    async def commit(self):
        self.commits += 1


def test_pilot_bin_validation_is_bounded_sorted_and_deduplicated():
    assert jobs._normalize_pilot_bins(["3348179", " 3064119 ", "3348179"]) == ["3064119", "3348179"]
    for invalid in [["3025217501"], ["822087"], ["0000000"], ["3348179 OR 1=1"], ["3348179"] * 101]:
        with pytest.raises(HTTPException):
            jobs._normalize_pilot_bins(invalid)


@pytest.mark.anyio
async def test_compliance_default_gate_is_read_only_and_explains_required_scope():
    session = Session()
    response = await jobs.start_job(job_type="dob_safety", limit=25, session=session, user=AuthUser(user_id="u1", email="test@example.com"))
    assert response["status"] == "approval_required"
    assert response["preview"]["required_parameters"] == ["bin"]
    assert session.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("job_type,dry_run,confirm", [("dob_safety", False, True), ("dob_safety_preview", True, False)])
async def test_real_compliance_fetch_requires_explicit_bins(job_type, dry_run, confirm):
    session = Session()
    with pytest.raises(HTTPException, match="explicit pilot list"):
        await jobs.start_job(job_type=job_type, limit=25, dry_run=dry_run, confirm_execute=confirm, session=session, user=AuthUser(user_id="u1", email="test@example.com"))
    assert session.calls == []


@pytest.mark.anyio
async def test_preview_rejects_execute_flags():
    with pytest.raises(HTTPException, match="Preview jobs require"):
        await jobs.start_job(job_type="buildings_preview", limit=25, dry_run=False, confirm_execute=True, session=Session(), user=AuthUser(user_id="u1", email="test@example.com"))


@pytest.mark.anyio
async def test_dob_preview_dispatches_only_preview_and_retains_scope(monkeypatch):
    dispatched = []
    task = SimpleNamespace(delay=lambda **kwargs: dispatched.append(kwargs))
    monkeypatch.setitem(sys.modules, "src.tasks.compliance", SimpleNamespace(ingest_dob_safety=task))
    session = Session()
    response = await jobs.start_job(job_type="dob_safety_preview", limit=25, bins=["3348179"], dry_run=True, confirm_execute=False, session=session, user=AuthUser(user_id="u1", email="test@example.com"))
    assert response["status"] == "queued"
    assert response["dry_run"] is True
    assert dispatched == [{"job_id": 77, "bins": ["3348179"], "dry_run": True, "confirm_execute": False}]
    insert = next(params for sql, params in session.calls if "INSERT INTO ingestion_jobs" in sql)
    config = json.loads(insert["config"])
    assert config["business_data_mutations_planned"] == 0
    assert config["write_permitted"] is False
    assert config["request"]["bins"] == ["3348179"]


@pytest.mark.anyio
async def test_overlapping_pilot_jobs_are_rejected_even_with_different_scope():
    config = {"request": {"limit": 25, "bins": ["3348179", "3348178"], "dry_run": False, "confirm_execute": True}}
    result = await jobs._find_equivalent_inflight_job(Session([(9, "running", config)]), job_type="dob_safety", signature={"bins": ["3348178", "3064119"], "limit": 50})
    assert result["job_id"] == 9


@pytest.mark.anyio
async def test_disjoint_pilot_jobs_remain_distinct():
    config = {"request": {"limit": 25, "bins": ["3348179"], "dry_run": False, "confirm_execute": True}}
    result = await jobs._find_equivalent_inflight_job(Session([(9, "running", config)]), job_type="dob_safety", signature={"bins": ["3064119"], "limit": 50})
    assert result is None


@pytest.mark.anyio
async def test_dispatch_config_merge_preserves_concurrent_worker_result():
    session = Session()
    await jobs._persist_job_record(session, job_id=77, config={"dispatch": {"state": "dispatched"}})
    sql, _ = session.calls[0]
    assert "COALESCE(CAST(config AS JSONB), '{}'::jsonb) || CAST(:config AS JSONB)" in sql


@pytest.mark.anyio
async def test_building_execution_requires_reviewed_fingerprint_before_any_write():
    session = Session()
    with pytest.raises(HTTPException, match="reviewed full buildings_preview"):
        await jobs.start_job(job_type="buildings", limit=25, dry_run=False, confirm_execute=True, session=session, user=AuthUser(user_id="u1", email="test@example.com"))
    assert session.calls == []


@pytest.mark.anyio
async def test_building_execution_binds_and_persists_reviewed_fingerprint(monkeypatch):
    dispatched = []
    task = SimpleNamespace(delay=lambda **kwargs: dispatched.append(kwargs))
    monkeypatch.setitem(sys.modules, "src.tasks.ingest", SimpleNamespace(ingest_buildings_from_hpd=task))
    session = Session()
    fingerprint = "a" * 64
    response = await jobs.start_job(job_type="buildings", limit=25, dry_run=False, confirm_execute=True, expected_source_fingerprint=fingerprint, session=session, user=AuthUser(user_id="u1", email="test@example.com"))
    assert response["status"] == "queued"
    assert dispatched == [{"job_id": 77, "dry_run": False, "confirm_execute": True, "expected_source_fingerprint": fingerprint}]
    insert = next(params for sql, params in session.calls if "INSERT INTO ingestion_jobs" in sql)
    assert json.loads(insert["config"])["request"]["expected_source_fingerprint"] == fingerprint


@pytest.mark.anyio
async def test_building_default_gate_explains_actual_source_preview():
    response = await jobs.start_job(job_type="buildings", limit=25, session=Session(), user=AuthUser(user_id="u1", email="test@example.com"))
    assert response["preview"]["required_parameters"] == ["expected_source_fingerprint"]
    assert "buildings_preview/start" in response["preview"]["source_preview_query"]


@pytest.mark.anyio
@pytest.mark.parametrize("job_type", ["dob_complaints_preview", "hpd_identity_pilot_preview"])
async def test_new_pilot_previews_dispatch_bounded_scope(monkeypatch, job_type):
    dispatched = []
    task = SimpleNamespace(delay=lambda **kwargs: dispatched.append(kwargs))
    if job_type.startswith("hpd_identity"):
        monkeypatch.setitem(sys.modules, "src.tasks.identity_pilot", SimpleNamespace(ingest_hpd_identity_pilot=task))
    else:
        monkeypatch.setitem(sys.modules, "src.tasks.compliance", SimpleNamespace(ingest_dob_complaints=task))
    response = await jobs.start_job(job_type=job_type, limit=25, bins=["3348179"], session=Session(), user=AuthUser(user_id="u1", email="test@example.com"))
    assert response["status"] == "queued"
    assert dispatched[0]["bins"] == ["3348179"]
    assert dispatched[0]["dry_run"] is True
    assert dispatched[0]["confirm_execute"] is False


@pytest.mark.anyio
async def test_identity_pilot_requires_its_reviewed_fingerprint_before_writes():
    session = Session()
    with pytest.raises(HTTPException, match="hpd_identity_pilot_preview"):
        await jobs.start_job(job_type="hpd_identity_pilot", limit=25, bins=["3348179"], dry_run=False, confirm_execute=True, session=session, user=AuthUser(user_id="u1", email="test@example.com"))
    assert not session.calls


def test_pilot_scope_never_expands_past_twenty_five_bins():
    with pytest.raises(HTTPException, match="at most 25"):
        jobs._normalize_pilot_bins([str(3000000 + number) for number in range(26)])
