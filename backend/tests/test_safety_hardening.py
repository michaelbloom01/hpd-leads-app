from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.auth.auth import AuthUser
from src.routers import buildings as buildings_router
from src.routers import building_lists as building_lists_router
from src.routers import smart_lists as smart_lists_router
from src.services import building_links, contact_roster


class _FakeRow:
    def __init__(self, mapping: dict):
        self._mapping = mapping

    def __getitem__(self, index: int):
        return list(self._mapping.values())[index]


class _FakeAsyncResult:
    def __init__(self, *, first=None, scalar_value=None, rows=None):
        self._first = first
        self._scalar_value = scalar_value
        self._rows = rows or []

    def first(self):
        return self._first

    def fetchone(self):
        return self._first

    def scalar(self):
        return self._scalar_value

    def __iter__(self):
        return iter(self._rows)


class _ReadOnlyAsyncSession:
    def __init__(self, results):
        self._results = list(results)
        self.calls: list[tuple[str, dict | None]] = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        if not self._results:
            raise AssertionError("Unexpected execute call")
        return self._results.pop(0)

    async def commit(self):
        raise AssertionError("Read-only route unexpectedly committed")


class _FakeSyncResult:
    def __init__(self, *, rows=None, rowcount: int = 0):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchall(self):
        return self._rows


class _FakeSyncSession:
    def __init__(self):
        self.insert_params = None

    def execute(self, statement, params=None):
        sql = str(statement)
        if "ARRAY_AGG" in sql:
            return _FakeSyncResult(
                rows=[
                    _FakeRow(
                        {
                            "bbl": "1000000001",
                            "current_lead_ids": ["lead-2"],
                            "active_row_count": 1,
                        }
                    ),
                    _FakeRow(
                        {
                            "bbl": "1000000002",
                            "current_lead_ids": ["lead-1"],
                            "active_row_count": 1,
                        }
                    ),
                ]
            )
        if "INSERT INTO building_management" in sql:
            self.insert_params = params
            return _FakeSyncResult(rowcount=1)
        raise AssertionError(f"Unexpected SQL: {sql}")


def _request() -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/buildings/1000000001",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "scheme": "http",
        "query_string": b"",
    }
    return Request(scope)


def test_guarded_insert_current_links_skips_conflicts_and_existing_rows():
    session = _FakeSyncSession()

    result = building_links.guarded_insert_current_links(
        session,
        [
            {"bbl": "1000000001", "lead_id": "lead-1", "role": "agent"},
            {"bbl": "1000000002", "lead_id": "lead-1", "role": "agent"},
            {"bbl": "1000000003", "lead_id": "lead-1", "role": "agent"},
        ],
    )

    assert result["inserted"] == 1
    assert result["skipped_existing"] == 1
    assert result["conflicts"] == [
        {
            "bbl": "1000000001",
            "current_lead_ids": ["lead-2"],
            "active_row_count": 1,
            "has_conflict": False,
        }
    ]
    assert session.insert_params == [{"bbl": "1000000003", "lead_id": "lead-1", "role": "agent"}]


def test_contact_roster_marks_recent_stale_refresh_as_refreshing():
    recent_request = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    payload, status, _last_refreshed_at = contact_roster._get_dos_cache_payload_from_row(
        {
            "result": {
                "lookup_name": "ACME LLC",
                "officers": [],
                "refresh_requested_at": recent_request,
            },
            "cached_at": (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(),
        }
    )

    assert payload["refresh_requested_at"] == recent_request
    assert status == "refreshing"


def test_smart_lists_schema_requirement_reports_gap_without_ddl():
    session = _ReadOnlyAsyncSession([
        _FakeAsyncResult(scalar_value=False),
    ])

    with pytest.raises(HTTPException) as exc:
        asyncio.run(smart_lists_router.require_smart_lists_schema(session))

    assert exc.value.status_code == 503
    assert exc.value.detail["status"] == "schema_not_ready"
    executed_sql = "\n".join(sql for sql, _params in session.calls)
    assert "CREATE TABLE" not in executed_sql
    assert "ALTER TABLE" not in executed_sql


def test_smart_lists_read_route_does_not_run_schema_ddl():
    session = _ReadOnlyAsyncSession([
        _FakeAsyncResult(scalar_value=True),
        _FakeAsyncResult(rows=[(column,) for column in smart_lists_router.REQUIRED_SMART_LIST_COLUMNS]),
        _FakeAsyncResult(rows=[]),
    ])

    result = asyncio.run(
        smart_lists_router.list_smart_lists(
            _request(),
            session=session,
            user=AuthUser(user_id="user-1", email="user@example.com"),
        )
    )

    assert result == {"smart_lists": []}
    executed_sql = "\n".join(sql for sql, _params in session.calls)
    assert "CREATE TABLE" not in executed_sql
    assert "ALTER TABLE" not in executed_sql
    assert "FROM smart_lists" in executed_sql


def test_building_lists_schema_requirement_reports_gap_without_ddl():
    session = _ReadOnlyAsyncSession([
        _FakeAsyncResult(rows=[]),
        _FakeAsyncResult(rows=[]),
    ])

    with pytest.raises(HTTPException) as exc:
        asyncio.run(building_lists_router.require_building_lists_schema(session))

    assert exc.value.status_code == 503
    assert exc.value.detail["status"] == "schema_not_ready"
    assert exc.value.detail["schema_status"]["missing_tables"] == [
        "building_list_members",
        "building_lists",
    ]
    executed_sql = "\n".join(sql for sql, _params in session.calls)
    assert "CREATE TABLE" not in executed_sql
    assert "ALTER TABLE" not in executed_sql


def test_building_lists_read_route_does_not_run_schema_ddl():
    session = _ReadOnlyAsyncSession([
        _FakeAsyncResult(rows=[("building_lists",), ("building_list_members",)]),
        _FakeAsyncResult(rows=[
            ("building_lists", "id"),
            ("building_lists", "user_id"),
            ("building_lists", "name"),
            ("building_lists", "created_at"),
            ("building_lists", "updated_at"),
            ("building_list_members", "list_id"),
            ("building_list_members", "bbl"),
            ("building_list_members", "added_at"),
        ]),
        _FakeAsyncResult(rows=[]),
    ])

    result = asyncio.run(
        building_lists_router.list_building_lists(
            _request(),
            session=session,
            user=AuthUser(user_id="user-1", email="user@example.com"),
        )
    )

    assert result == {"building_lists": []}
    executed_sql = "\n".join(sql for sql, _params in session.calls)
    assert "CREATE TABLE" not in executed_sql
    assert "ALTER TABLE" not in executed_sql
    assert "FROM building_lists" in executed_sql


@pytest.mark.anyio
async def test_get_building_does_not_queue_dos_refresh(monkeypatch):
    session = _ReadOnlyAsyncSession(
        [
            _FakeAsyncResult(
                first=_FakeRow(
                    {
                        "bbl": "1000000001",
                        "address": "1 Main St",
                        "borough": "MANHATTAN",
                        "current_lead_id": "lead-1",
                        "current_link_count": 1,
                        "current_link_lead_ids": ["lead-1"],
                        "current_link_conflict": False,
                    }
                )
            )
        ]
    )

    async def _fake_resolve(_session, _raw_bbl):
        return "1000000001"

    async def _fake_contacts(*, session, bbl, building_address):
        return [], {
            "management_company": "Acme Mgmt",
            "corporate_owner": "Acme Owner LLC",
            "dos_contacts_is_stale": True,
            "dos_contacts_status": "stale",
            "dos_refresh_requested_at": None,
            "dos_contacts_last_refreshed_at": "2026-03-01",
        }

    async def _unexpected_refresh(*args, **kwargs):
        raise AssertionError("GET /buildings/{bbl} should not queue DOS refresh work")

    monkeypatch.setattr(buildings_router, "_resolve_canonical_bbl", _fake_resolve)
    monkeypatch.setattr(buildings_router, "get_building_contacts", _fake_contacts)
    monkeypatch.setattr(buildings_router, "_request_dos_refresh", _unexpected_refresh)

    response = await buildings_router.get_building(
        request=_request(),
        bbl="1000000001",
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["dos_contacts_status"] == "stale"
    assert response["corporate_owner"] == "Acme Owner LLC"


@pytest.mark.anyio
async def test_building_dos_refresh_defaults_to_approval_preview_without_db_touch(monkeypatch):
    session = _ReadOnlyAsyncSession([])

    async def _unexpected_resolve(*args, **kwargs):
        raise AssertionError("DOS refresh preview should not inspect or mutate building data")

    monkeypatch.setattr(buildings_router, "_resolve_canonical_bbl", _unexpected_resolve)

    response = await buildings_router.refresh_building_dos_contacts.__wrapped__(
        request=_request(),
        bbl="1000000001",
        dry_run=True,
        confirm_execute=False,
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["status"] == "approval_required"
    assert response["job_id"] is None
    assert response["approval_required"] is True
    assert response["safe_to_run_automatically"] is False
    assert response["mutations_planned"] == 0
    assert "confirm_execute=true" in response["preview"]["required_execute_query"]
    assert session.calls == []
