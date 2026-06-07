import pytest
from starlette.requests import Request

from src.auth.auth import AuthUser
from src.routers.admin import _normalize_address_for_search, search_buildings


class FakeExecuteResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def __iter__(self):
        for row in self._rows:
            yield type("Row", (), {"_mapping": row})()


class FakeAsyncSession:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        if not self._results:
            raise AssertionError("Unexpected execute call")
        return self._results.pop(0)


def _make_request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/api/buildings/search", "headers": []})


def test_address_search_keeps_ordinal_and_stripped_variants():
    patterns = set(_normalize_address_for_search("110 East 55th St"))

    assert "%110 EAST 55TH ST%" in patterns
    assert "%110 EAST 55 ST%" in patterns
    assert "%110 E 55TH ST%" in patterns
    assert "%110 EAST 55TH STREET%" in patterns


@pytest.mark.anyio
async def test_building_search_falls_back_to_lead_address_records():
    session = FakeAsyncSession([
        FakeExecuteResult(rows=[]),
        FakeExecuteResult(rows=[{
            "lead_id": "lead-1",
            "address": "110 EAST 55TH ST",
            "primary_borough": "MANHATTAN",
            "lead_name": "110 EAST 55TH ST",
            "entity_type": "owner_operator",
            "score": 82.5,
            "portfolio_size": 64,
            "total_units": 120,
        }]),
    ])

    response = await search_buildings(
        request=_make_request(),
        address="110 EAST 55TH ST",
        limit=20,
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["total"] == 1
    result = response["buildings"][0]
    assert result["status"] == "lead_address"
    assert result["building_id"] == "lead:lead-1"
    assert result["bbl"] is None
    assert result["lead_id"] == "lead-1"
