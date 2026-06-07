import pytest

from src.services.portfolio_export import (
    flatten_contact_rows,
    normalize_company_key,
)
from src.auth.auth import AuthUser
from src.routers import export_v1 as export_router
from starlette.requests import Request


def _make_request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/api/test", "headers": []})


def test_normalize_company_key_matches_venture_variants():
    assert normalize_company_key("VENTURE NY PROPERTY MANAGEMENT, LLC") == (
        "VENTURENYPROPERTYMANAGEMENTLLC"
    )
    assert normalize_company_key("Venture NY Property Management") == (
        "VENTURENYPROPERTYMANAGEMENT"
    )


def test_flatten_contact_rows_preserves_building_and_source_context():
    rows = flatten_contact_rows([
        {
            "bbl": "3023587501",
            "address": "100 NORTH 3 STREET",
            "borough": "BROOKLYN",
            "zip_code": "11249",
            "unit_count": 24,
            "churn_score": 3.5,
            "churn_category": "stable",
            "management_company": "VENTURE NY PROPERTY MANAGEMENT, LLC",
            "corporate_owner": "100 N 3 CORP",
            "dos_contacts_status": "not_loaded",
            "contacts": [
                {
                    "name": "VENTURE NY PROPERTY MANAGEMENT, LLC",
                    "role": "Agent",
                    "source": "HPD Registration",
                    "as_of_date": "2026-02-23",
                    "address": "43-10 11TH STREET, Long Island City, NY, 11101",
                    "source_record_id": "123",
                    "source_url": "https://data.cityofnewyork.us/resource/feu5-w2e2.json",
                    "is_decision_maker": False,
                }
            ],
        }
    ])

    assert rows == [
        {
            "bbl": "3023587501",
            "address": "100 NORTH 3 STREET",
            "borough": "BROOKLYN",
            "zip_code": "11249",
            "unit_count": 24,
            "churn_score": 3.5,
            "churn_category": "stable",
            "management_company": "VENTURE NY PROPERTY MANAGEMENT, LLC",
            "corporate_owner": "100 N 3 CORP",
            "dos_contacts_status": "not_loaded",
            "contact_name": "VENTURE NY PROPERTY MANAGEMENT, LLC",
            "contact_role": "Agent",
            "contact_source": "HPD Registration",
            "contact_updated": "2026-02-23",
            "contact_address": "43-10 11TH STREET, Long Island City, NY, 11101",
            "contact_confidence": "--",
            "contact_source_record_id": "123",
            "contact_source_url": "https://data.cityofnewyork.us/resource/feu5-w2e2.json",
            "board_role": None,
            "is_decision_maker": False,
        }
    ]


async def _collect_streaming_body(response) -> str:
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
    return "".join(chunks)


@pytest.mark.anyio
async def test_portfolio_contacts_export_endpoint_streams_csv(monkeypatch):
    async def fake_build_portfolio_export(*, session, company, lead_id=None):
        return {
            "contact_rows": [
                {
                    "bbl": "3023587501",
                    "address": "100 NORTH 3 STREET",
                    "borough": "BROOKLYN",
                    "zip_code": "11249",
                    "unit_count": 24,
                    "churn_score": 3.5,
                    "churn_category": "stable",
                    "management_company": "VENTURE NY PROPERTY MANAGEMENT, LLC",
                    "corporate_owner": "100 N 3 CORP",
                    "dos_contacts_status": "not_loaded",
                    "contact_name": "VENTURE NY PROPERTY MANAGEMENT, LLC",
                    "contact_role": "Agent",
                    "contact_source": "HPD Registration",
                    "contact_updated": "2026-02-23",
                    "contact_address": "43-10 11TH STREET, Long Island City, NY, 11101",
                    "contact_confidence": "--",
                    "contact_source_record_id": "123",
                    "contact_source_url": "https://data.cityofnewyork.us/resource/feu5-w2e2.json",
                    "board_role": None,
                    "is_decision_maker": False,
                }
            ]
        }

    monkeypatch.setattr(export_router, "build_portfolio_export", fake_build_portfolio_export)

    response = await export_router.export_portfolio_contacts_csv.__wrapped__(
        request=_make_request(),
        company="VENTURE NY PROPERTY MANAGEMENT, LLC",
        lead_id=None,
        session=object(),
        user=AuthUser(user_id="u1", email="user@example.com"),
    )

    body = await _collect_streaming_body(response)

    assert response.media_type == "text/csv"
    assert "double_edge_venture_ny_property_management_llc_portfolio_contacts.csv" in (
        response.headers["Content-Disposition"]
    )
    assert "bbl,address,borough" in body
    assert "3023587501,100 NORTH 3 STREET,BROOKLYN" in body
    assert "VENTURE NY PROPERTY MANAGEMENT, LLC" in body
