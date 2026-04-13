from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.exc import DBAPIError
from starlette.requests import Request

from src.auth.auth import AuthUser
from src.routers import leads as leads_router


def _make_request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/api/leads", "headers": []})


class FakeExecuteResult:
    def __init__(self, *, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def __iter__(self):
        for row in self._rows:
            yield SimpleNamespace(_mapping=row)

    def scalar(self):
        return self._scalar


class FakeAsyncSession:
    def __init__(self, results):
        self._results = list(results)
        self.calls: list[tuple[str, dict | None]] = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        if not self._results:
            raise AssertionError("Unexpected execute call")
        next_result = self._results.pop(0)
        if isinstance(next_result, Exception):
            raise next_result
        return next_result


def _lead_row(**overrides):
    row = {
        "lead_id": "lead-1",
        "agent_name": "Acme Management",
        "owner_name": "",
        "owner_type": "corporation",
        "live_portfolio_size": 12,
        "live_total_units": 144,
        "phone": None,
        "email": None,
        "website": None,
        "business_summary": None,
        "address": None,
        "primary_borough": "MANHATTAN",
        "boros": ["MANHATTAN"],
        "score": 88.0,
        "enrichment_status": "partial",
        "outreach_status": "new",
        "entity_type": "company",
        "company_name": "Acme Management LLC",
        "primary_contact": None,
        "primary_contact_title": None,
        "estimated_monthly_revenue": 12000.0,
        "estimated_annual_revenue": 144000.0,
        "violation_count": 2,
        "violation_class_a": 1,
        "violation_class_b": 1,
        "violation_class_c": 0,
        "violations_per_unit": 0.2,
        "pipeline_stage": "research",
        "next_follow_up": None,
        "priority_rank": 0,
    }
    row.update(overrides)
    return row


@pytest.mark.anyio
async def test_get_leads_uses_simple_query_for_unfiltered_supported_sorts():
    session = FakeAsyncSession(
        [
            FakeExecuteResult(rows=[_lead_row()]),
            FakeExecuteResult(scalar=314723),
        ]
    )

    response = await leads_router.get_leads(
        request=_make_request(),
        min_score=None,
        max_score=None,
        min_portfolio=None,
        max_portfolio=None,
        boro=None,
        neighborhood=None,
        has_phone=None,
        has_email=None,
        has_website=None,
        entity_type=None,
        enrichment_status=None,
        outreach_status=None,
        pipeline_stage=None,
        search=None,
        sort_by="portfolio_size",
        sort_dir="desc",
        min_units=None,
        max_units=None,
        min_units_per_bldg=None,
        max_units_per_bldg=None,
        building_type_has=None,
        limit=50,
        offset=0,
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["total"] == 314723
    assert response["limit"] == 50
    assert len(response["leads"]) == 1
    first_sql = session.calls[0][0]
    assert "FROM leads" in first_sql
    assert "WITH live_link_stats" not in first_sql
    assert "ORDER BY portfolio_size DESC" in first_sql


@pytest.mark.anyio
async def test_get_leads_estimates_total_when_simple_count_fails():
    session = FakeAsyncSession(
        [
            FakeExecuteResult(rows=[_lead_row(), _lead_row(lead_id="lead-2")]),
            DBAPIError("SELECT COUNT(*) FROM leads", {}, Exception("disk full")),
        ]
    )

    response = await leads_router.get_leads(
        request=_make_request(),
        min_score=None,
        max_score=None,
        min_portfolio=None,
        max_portfolio=None,
        boro=None,
        neighborhood=None,
        has_phone=None,
        has_email=None,
        has_website=None,
        entity_type=None,
        enrichment_status=None,
        outreach_status=None,
        pipeline_stage=None,
        search=None,
        limit=2,
        offset=0,
        sort_by="score",
        sort_dir="desc",
        min_units=None,
        max_units=None,
        min_units_per_bldg=None,
        max_units_per_bldg=None,
        building_type_has=None,
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert len(response["leads"]) == 2
    assert response["total"] == 3
