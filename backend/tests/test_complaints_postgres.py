"""Opt-in real PostgreSQL tests for source isolation and scope denominators."""

import asyncio
import os

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.services.compliance import load_compliance, publish_snapshot
from src.tasks import ingest
from tests.test_compliance_intelligence import BBL, GREEN, snapshot
from tests.test_dob_complaints import complaint_snapshot
from tests.test_refresh_postgres import database as shared_database_fixture

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_INTEGRATION") != "1",
    reason="Requires the explicit disposable PostgreSQL integration environment",
)


@pytest.fixture
def database(monkeypatch):
    yield from shared_database_fixture.__wrapped__(monkeypatch)


class ReadAdapter:
    def __init__(self, session):
        self.session = session

    async def execute(self, statement, params=None):
        return self.session.execute(statement, params)


def test_postgres_complaints_publish_idempotently_with_independent_checks(
    database, monkeypatch
):
    monkeypatch.setenv("COMPLIANCE_INTELLIGENCE_ENABLED", "true")
    ingest.ingest_buildings_from_hpd.run(
        dry_run=False, confirm_execute=True, expected_source_fingerprint="a" * 64
    )
    with Session(database, join_transaction_mode="create_savepoint") as session:
        first = publish_snapshot(session, complaint_snapshot(), run_id="pg-complaint-1")
        second = publish_snapshot(
            session, complaint_snapshot(), run_id="pg-complaint-2"
        )
        assert first["inserted"] == second["unchanged"] == 1
        response = asyncio.run(
            load_compliance(ReadAdapter(session), scope_type="parcel", scope_id=BBL)
        )
        assert response["coverage"]["checked_building_count"] == 0
        assert response["coverage"]["active_records_count"] == 0
        assert response["coverage"]["open_complaints_count"] == 1
        assert response["source_coverage"][0]["status"] == "not_checked"
        assert response["source_coverage"][1]["status"] == "complete"
        assert (
            database.execute(
                text("SELECT count(*) FROM compliance_observations")
            ).scalar_one()
            == 1
        )
        publish_snapshot(session, snapshot(), run_id="pg-safety-1")
        response = asyncio.run(
            load_compliance(
                ReadAdapter(session), scope_type="building", scope_id=GREEN[0][1]
            )
        )
        assert (
            response["coverage"]["active_records_count"]
            == response["coverage"]["open_complaints_count"]
            == 1
        )
        assert response["coverage"]["checked_building_count"] == 1
        assert response["reported_balance_cents"] is None
        assert len(response["buildings"][0]["source_checks"]) == 4
        registration = response["buildings"][0]["hpd_registration"]
        assert registration["registration_id"] == "378111"
        assert registration["source_url"].startswith("https://data.cityofnewyork.us/")


def test_postgres_portfolio_retains_94_parcel_denominator_with_four_mapped_bins(
    database, monkeypatch
):
    monkeypatch.setenv("COMPLIANCE_INTELLIGENCE_ENABLED", "true")
    ingest.ingest_buildings_from_hpd.run(
        dry_run=False, confirm_execute=True, expected_source_fingerprint="a" * 64
    )
    database.execute(text("""
        INSERT INTO leads (lead_id, company_name, created_at, updated_at)
        VALUES ('pg-partial', 'Synthetic partial identity test', now(), now());
        INSERT INTO buildings (bbl, address, created_at, updated_at)
        SELECT (4000000000 + i)::text, 'Synthetic unmapped parcel ' || i, now(), now()
        FROM generate_series(1, 93) AS i;
        INSERT INTO building_management (bbl, lead_id, role, is_current, created_at, updated_at)
        SELECT bbl, 'pg-partial', 'Agent', true, now(), now() FROM buildings;
    """))
    with Session(database, join_transaction_mode="create_savepoint") as session:
        publish_snapshot(session, snapshot(), run_id="pg-partial-safety")
        publish_snapshot(
            session, complaint_snapshot([]), run_id="pg-partial-complaints"
        )
        response = asyncio.run(
            load_compliance(
                ReadAdapter(session), scope_type="portfolio", scope_id="pg-partial"
            )
        )
        assert response["coverage"]["physical_building_count"] == 4
        assert response["coverage"]["scope_parcel_count"] == 94
        assert response["coverage"]["mapped_parcel_count"] == 1
        assert response["coverage"]["unmapped_parcel_count"] == 93
        assert (
            response["coverage"]["identity_coverage_status"]
            == response["coverage"]["status"]
            == "partial"
        )
        coverage = {row["source_system"]: row for row in response["source_coverage"]}
        assert coverage["dob_safety"]["status"] == "partial"
        assert coverage["dob_complaints"]["status"] == "partial"
        assert coverage["dob_violations"]["status"] == "not_checked"
        assert coverage["dob_ecb"]["status"] == "not_checked"
        assert response["source_coverage"][1]["records_count"] == 0
        assert response["source_coverage"][1]["checked_building_count"] == 4


def test_postgres_unmapped_parcel_is_explicit(database, monkeypatch):
    monkeypatch.setenv("COMPLIANCE_INTELLIGENCE_ENABLED", "true")
    with Session(database, join_transaction_mode="create_savepoint") as session:
        response = asyncio.run(
            load_compliance(ReadAdapter(session), scope_type="parcel", scope_id=BBL)
        )
        assert (
            response["coverage"]["scope_parcel_count"]
            == response["coverage"]["unmapped_parcel_count"]
            == 1
        )
        assert response["coverage"]["mapped_parcel_count"] == 0
        assert response["coverage"]["status"] == "identity_unavailable"


def test_postgres_registration_read_excludes_nonofficial_identity_evidence(
    database, monkeypatch
):
    monkeypatch.setenv("COMPLIANCE_INTELLIGENCE_ENABLED", "true")
    ingest.ingest_buildings_from_hpd.run(
        dry_run=False, confirm_execute=True, expected_source_fingerprint="a" * 64
    )
    database.execute(
        text(
            "UPDATE hpd_registration_snapshots SET identity_status='quarantined' WHERE bin=:bin"
        ),
        {"bin": GREEN[0][1]},
    )
    with Session(database, join_transaction_mode="create_savepoint") as session:
        response = asyncio.run(
            load_compliance(
                ReadAdapter(session), scope_type="building", scope_id=GREEN[0][1]
            )
        )
        assert response["buildings"][0]["hpd_registration"] is None
