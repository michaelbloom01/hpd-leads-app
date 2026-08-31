"""Review API validation and real PostgreSQL writer-serialization contracts."""

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.auth.auth import AuthUser, create_access_token
from src.db.session import get_compatible_sync_url, get_session
from src.models.compliance import ComplianceRecord
from src.models.compliance_reviews import ComplianceReview
from src.routers import compliance_workflow as workflow

POSTGRES = pytest.mark.skipif(os.environ.get("RUN_POSTGRES_INTEGRATION") != "1", reason="Explicit disposable PostgreSQL environment required")
RECORD_ID = "a" * 32
USER = AuthUser(user_id="review-workflow-test", email="reviewer@example.com", role="user")


def _url(record_id=RECORD_ID):
    return f"/api/v1/compliance/records/{record_id}/reviews"


def _input(**overrides):
    return {"state": "in_review", "reason": "Checked the official source.", "expected_version": 0, **overrides}


def _headers():
    # This is a process-local test JWT, never a production token.
    return {"Authorization": "Bearer " + create_access_token(USER)}


async def _request(app, method, path, **kwargs):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


@pytest.fixture
def unit_api(monkeypatch):
    monkeypatch.setenv("COMPLIANCE_INTELLIGENCE_ENABLED", "true")
    session = MagicMock()
    session.execute = AsyncMock()
    app = FastAPI()
    app.include_router(workflow.router)

    async def fake_session():
        yield session

    app.dependency_overrides[get_session] = fake_session
    return app, session


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer invalid-test-token"}])
def test_reviews_require_valid_authentication(unit_api, method, headers):
    app, session = unit_api
    response = asyncio.run(_request(app, method, _url(), headers=headers, **({"json": _input()} if method == "POST" else {})))
    assert response.status_code == 401
    session.execute.assert_not_awaited()


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_review_feature_flag_returns_503_before_database_lookup(unit_api, monkeypatch, method):
    app, session = unit_api
    monkeypatch.setenv("COMPLIANCE_INTELLIGENCE_ENABLED", "false")
    response = asyncio.run(_request(app, method, _url(), headers=_headers(), **({"json": _input()} if method == "POST" else {})))
    assert response.status_code == 503
    session.execute.assert_not_awaited()


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_missing_review_schema_returns_503(unit_api, method):
    app, session = unit_api
    result = MagicMock()
    result.scalar.return_value = False
    session.execute.return_value = result
    response = asyncio.run(_request(app, method, _url(), headers=_headers(), **({"json": _input()} if method == "POST" else {})))
    assert response.status_code == 503
    assert session.execute.await_count == 1


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_missing_record_returns_404_after_schema_gate(unit_api, method):
    app, session = unit_api
    schema, missing = MagicMock(), MagicMock()
    schema.scalar.return_value = True
    missing.scalar_one_or_none.return_value = None
    session.execute.side_effect = [schema, missing]
    response = asyncio.run(_request(app, method, _url(), headers=_headers(), **({"json": _input()} if method == "POST" else {})))
    assert response.status_code == 404
    session.add.assert_not_called()


@pytest.mark.parametrize("overrides", [
    {"state": "paid"}, {"state": "resolved"}, {"state": None},
    {"reason": "abc"}, {"reason": "     "}, {"reason": " x   "}, {"reason": "x" * 2001}, {"reason": "Embedded \x00 character."},
    {"expected_version": -1}, {"expected_version": "0"}, {"expected_version": 0.0}, {"expected_version": False},
    {"actor_id": "spoofed"}, {"actor_label": "spoofed"}, {"agency_status": "Closed"}, {"version": 9},
])
def test_invalid_review_input_and_attribution_spoofing_return_422(unit_api, overrides):
    app, session = unit_api
    response = asyncio.run(_request(app, "POST", _url(), headers=_headers(), json=_input(**overrides)))
    assert response.status_code == 422
    session.execute.assert_not_awaited()


@pytest.mark.parametrize("record_id", ["missing", "A" * 32, "a" * 31, "z" * 32])
def test_record_namespace_is_validated_before_queries(unit_api, record_id):
    app, session = unit_api
    assert asyncio.run(_request(app, "GET", _url(record_id), headers=_headers())).status_code == 422
    session.execute.assert_not_awaited()


@pytest.mark.parametrize("state", ["new", "in_review", "verified_for_briefing", "monitoring", "closed_internally", "dismissed", "source_mismatch"])
def test_all_internal_states_accept_meaningful_trimmed_reasons(state):
    review = workflow.ReviewInput(**_input(state=state, reason="  Source checked carefully.  "))
    assert review.reason == "Source checked carefully."
    assert review.state == state


@pytest.fixture
def pg_record(monkeypatch):
    url = make_url(get_compatible_sync_url())
    assert url.host in {"localhost", "127.0.0.1"}
    assert url.database == "hpd_leads_test"
    engine = create_engine(url)
    record_id = uuid4().hex
    now = datetime.now(timezone.utc)
    record = {
        "id": record_id, "source_system": "review_workflow_test", "source_record_key": record_id,
        "record_type": "violation", "bin": "3348179", "bbl": "3025217501", "category": "LL152",
        "status": "Active", "identity_status": "exact_source_bin", "source_url": "https://data.cityofnewyork.us/d/855j-jady",
        "source_updated_at": now, "first_seen_at": now, "observed_at": now, "payload_hash": record_id * 2,
        "parser_version": "review-test-v1", "ingestion_run_id": record_id, "raw_payload": {"test_record": record_id, "agency_status": "Active"},
    }
    with engine.begin() as connection:
        connection.execute(ComplianceRecord.__table__.insert().values(**record))
    monkeypatch.setenv("COMPLIANCE_INTELLIGENCE_ENABLED", "true")
    try:
        yield engine, url.set(drivername="postgresql+asyncpg"), record_id
    finally:
        # Both cleanup targets are this test's own committed UUID only.
        with engine.begin() as connection:
            connection.execute(delete(ComplianceReview).where(ComplianceReview.record_id == record_id))
            connection.execute(delete(ComplianceRecord).where(ComplianceRecord.id == record_id))
        engine.dispose()


def _pg_app(url, *, application_name=None):
    connect_args = {"server_settings": {"application_name": application_name}} if application_name else {}
    engine = create_async_engine(url, poolclass=NullPool, connect_args=connect_args)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(workflow.router)

    async def db_session():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = db_session
    return app, engine


def _source_record(engine, record_id):
    with engine.connect() as connection:
        return connection.execute(text("SELECT to_jsonb(t) FROM compliance_records t WHERE id=:id"), {"id": record_id}).scalar_one()


@POSTGRES
def test_postgres_append_only_versions_actor_attribution_conflict_and_agency_preservation(pg_record):
    sync_engine, url, record_id = pg_record
    before = _source_record(sync_engine, record_id)

    async def scenario():
        app, engine = _pg_app(url)
        try:
            initial = await _request(app, "GET", _url(record_id), headers=_headers())
            assert initial.status_code == 200
            assert initial.json()["state"] == "new"
            assert initial.json()["version"] == 0
            assert initial.json()["history"] == []
            first = await _request(app, "POST", _url(record_id), headers=_headers(), json=_input(reason="  Confirmed official source.  "))
            assert first.status_code == 200
            first_history = first.json()["history"][0]
            assert first_history["version"] == 1
            assert first_history["actor"] == USER.email
            assert first_history["reason"] == "Confirmed official source."
            stale = await _request(app, "POST", _url(record_id), headers=_headers(), json=_input(state="monitoring"))
            assert stale.status_code == 409
            second = await _request(app, "POST", _url(record_id), headers=_headers(), json=_input(state="closed_internally", expected_version=1))
            assert second.status_code == 200
            response = second.json()
            assert response["version"] == 2
            assert response["state"] == "closed_internally"
            assert response["agency_status"] == "Active"
            assert response["history"][1] == first_history
            assert [row["version"] for row in response["history"]] == [2, 1]
            for method in ("PUT", "PATCH", "DELETE"):
                assert (await _request(app, method, _url(record_id), headers=_headers())).status_code == 405
        finally:
            await engine.dispose()

    asyncio.run(scenario())
    assert _source_record(sync_engine, record_id) == before
    with sync_engine.connect() as connection:
        rows = connection.execute(select(ComplianceReview.actor_id, ComplianceReview.version).where(ComplianceReview.record_id == record_id).order_by(ComplianceReview.version)).all()
        assert rows == [(USER.user_id, 1), (USER.user_id, 2)]


@POSTGRES
def test_postgres_history_limit_keeps_latest_version_and_order(pg_record):
    sync_engine, url, record_id = pg_record
    now = datetime.now(timezone.utc)
    with sync_engine.begin() as connection:
        connection.execute(ComplianceReview.__table__.insert(), [
            {"id": uuid4().hex, "record_id": record_id, "version": version, "state": "monitoring", "reason": "Review history fixture.", "actor_id": USER.user_id, "actor_label": USER.email, "created_at": now}
            for version in range(1, 56)
        ])

    async def scenario():
        app, engine = _pg_app(url)
        try:
            response = await _request(app, "GET", _url(record_id), headers=_headers())
            assert response.status_code == 200
            result = response.json()
            assert result["version"] == 55
            assert result["history_limit"] == len(result["history"]) == 50
            assert [row["version"] for row in result["history"]] == list(range(55, 5, -1))
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@POSTGRES
def test_postgres_forbidden_text_is_rejected_before_any_review_is_added(pg_record):
    sync_engine, url, record_id = pg_record
    before = _source_record(sync_engine, record_id)

    async def scenario():
        app, engine = _pg_app(url)
        try:
            response = await _request(app, "POST", _url(record_id), headers=_headers(), json=_input(reason="Embedded \x00 character."))
            assert response.status_code == 422
        finally:
            await engine.dispose()

    asyncio.run(scenario())
    assert _source_record(sync_engine, record_id) == before
    with sync_engine.connect() as connection:
        assert connection.execute(select(ComplianceReview.id).where(ComplianceReview.record_id == record_id)).first() is None


@POSTGRES
def test_postgres_migration_downgrade_refuses_retained_review_history(pg_record, monkeypatch):
    sync_engine, _, record_id = pg_record
    review_id = uuid4().hex
    with sync_engine.begin() as connection:
        connection.execute(ComplianceReview.__table__.insert().values(
            id=review_id, record_id=record_id, version=1, state="monitoring", reason="Retain reviewed evidence.",
            actor_id=USER.user_id, actor_label=USER.email, created_at=datetime.now(timezone.utc),
        ))
    scripts = ScriptDirectory(str(Path(__file__).resolve().parents[1] / "alembic"))
    migration = scripts.get_revision("014_compliance_reviews").module
    assert migration.down_revision == "013_contact_region_text"

    def forbid_drop(*_args, **_kwargs):
        pytest.fail("A table-drop operation must never be reached by this retention test")

    monkeypatch.setattr(migration.op, "drop_table", forbid_drop)
    with pytest.raises(DBAPIError, match="Review history exists"), sync_engine.begin() as connection:
        monkeypatch.setattr(migration.op, "execute", lambda sql: connection.execute(sql))
        migration.downgrade()
    with sync_engine.connect() as connection:
        assert connection.execute(select(ComplianceReview.id).where(ComplianceReview.id == review_id)).scalar_one() == review_id


@POSTGRES
def test_postgres_concurrent_reviewers_are_serialized_one_success_one_409(pg_record):
    sync_engine, url, record_id = pg_record
    before = _source_record(sync_engine, record_id)

    async def scenario():
        application_name = "workflow-test-" + record_id
        app, engine = _pg_app(url, application_name=application_name)
        tasks = []
        try:
            async with engine.connect() as blocker:
                transaction = await blocker.begin()
                await blocker.execute(select(ComplianceRecord.id).where(ComplianceRecord.id == record_id).with_for_update())
                try:
                    tasks = [
                        asyncio.create_task(_request(app, "POST", _url(record_id), headers=_headers(), json=_input(state=state)))
                        for state in ("in_review", "monitoring")
                    ]
                    async with engine.connect() as observer:
                        for _ in range(200):
                            waiting = (await observer.execute(text("""
                                SELECT count(*) FROM pg_stat_activity
                                WHERE application_name=:name AND wait_event_type='Lock'
                            """), {"name": application_name})).scalar_one()
                            if waiting == 2:
                                break
                            await asyncio.sleep(0.01)
                        assert waiting == 2, "Both real API writers must be observed waiting on the same held record"
                finally:
                    await transaction.rollback()
            responses = await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
            assert sorted(response.status_code for response in responses) == [200, 409]
            success = next(response.json() for response in responses if response.status_code == 200)
            assert success["version"] == 1
            assert len(success["history"]) == 1
            assert success["agency_status"] == "Active"
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await engine.dispose()

    asyncio.run(scenario())
    assert _source_record(sync_engine, record_id) == before
    with sync_engine.connect() as connection:
        versions = connection.execute(select(ComplianceReview.version).where(ComplianceReview.record_id == record_id)).scalars().all()
        assert versions == [1]
