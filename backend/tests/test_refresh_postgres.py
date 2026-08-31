"""Opt-in real PostgreSQL regression tests against the disposable CI database."""

import asyncio
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.db.session import get_compatible_sync_url
from src.routers.quality import SUCCESSFUL_BUILDING_REFRESH_SQL
from src.services.compliance import load_compliance, publish_snapshot
from src.tasks import ingest
from tests.test_building_identity_refresh import _green_contacts, _green_rows
from tests.test_compliance_intelligence import snapshot as compliance_snapshot

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_INTEGRATION") != "1",
    reason="Requires the explicit disposable PostgreSQL integration environment",
)


@pytest.fixture
def database(monkeypatch):
    engine = create_engine(get_compatible_sync_url())
    assert engine.url.host in {"localhost", "127.0.0.1"}
    assert engine.url.database == "hpd_leads_test"
    connection = engine.connect()
    transaction = connection.begin()
    connection.execute(text("""
        INSERT INTO buildings (bbl, bin, address, created_at, updated_at)
        VALUES ('3025217501', '822087', 'Legacy parcel label', now(), now())
    """))
    connection.execute(text("""
        INSERT INTO building_contacts
            (bbl, registration_id, registration_contact_id, contact_type,
             corporation_name, created_at, updated_at)
        VALUES
            ('3025217501', '378111', '9000', 'Agent', 'Old manager', now(), now()),
            ('3025217501', NULL, NULL, 'BoardHead', 'Verified board person', now(), now()),
            ('3025217501', '777777', '7777', 'Agent', 'Separate registration', now(), now())
    """))
    monkeypatch.setattr(
        ingest, "_get_pg_session",
        lambda: Session(connection, join_transaction_mode="create_savepoint"),
    )
    snapshot = ingest._prepare_building_refresh_snapshot(_green_rows(), _green_contacts(), [])
    snapshot["stats"]["source_fingerprint"] = "a" * 64
    monkeypatch.setattr(ingest, "fetch_building_refresh_snapshot", lambda: snapshot)
    # The golden fixture is intentionally four physical buildings, not citywide.
    monkeypatch.setattr(ingest, "_validate_building_refresh_snapshot", lambda _stats: [])
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_real_postgres_promotion_preserves_siblings_contacts_and_before_images(database):
    old_contact_id = database.execute(text("""
        SELECT id FROM building_contacts WHERE registration_contact_id = '9000'
    """)).scalar_one()
    ingest.ingest_buildings_from_hpd.run(
        dry_run=False, confirm_execute=True, expected_source_fingerprint="a" * 64,
    )
    assert database.execute(text("SELECT count(*) FROM physical_buildings")).scalar_one() == 4
    assert database.execute(text("SELECT count(*) FROM building_parcel_links WHERE is_current")).scalar_one() == 4
    assert database.execute(text("SELECT count(*) FROM hpd_registration_snapshots WHERE is_current")).scalar_one() == 4
    contacts = database.execute(text("""
        SELECT registration_contact_id, contact_type, corporation_name
        FROM building_contacts WHERE bbl = '3025217501'
    """)).all()
    assert len(contacts) == 6
    assert (None, "BoardHead", "Verified board person") in contacts
    assert ("7777", "Agent", "Separate registration") in contacts
    assert database.execute(text("SELECT id FROM building_contacts WHERE registration_contact_id = '9000'")).scalar_one() == old_contact_id
    assert database.execute(text("SELECT bin FROM buildings WHERE bbl = '3025217501'")).scalar_one() == "3348179"
    before = database.execute(text("""
        SELECT before_payload FROM hpd_refresh_rollback_rows
        WHERE table_name = 'buildings' AND row_key = '3025217501'
    """)).scalar_one()
    assert before["bin"] == "822087"
    assert database.execute(text("SELECT status FROM ingestion_jobs ORDER BY id DESC LIMIT 1")).scalar_one() == "succeeded"


def test_real_postgres_failure_keeps_previous_committed_business_rows(database, monkeypatch):
    def fail_after_parcel_merge(*_args, **_kwargs):
        raise RuntimeError("Injected identity publication failure")

    monkeypatch.setattr(ingest, "_persist_building_identity_snapshot", fail_after_parcel_merge)
    with pytest.raises(RuntimeError, match="Injected identity"):
        ingest.ingest_buildings_from_hpd.run(
            dry_run=False, confirm_execute=True, expected_source_fingerprint="a" * 64,
        )
    assert database.execute(text("SELECT bin FROM buildings WHERE bbl = '3025217501'")).scalar_one() == "822087"
    assert database.execute(text("SELECT count(*) FROM building_contacts WHERE bbl = '3025217501'")).scalar_one() == 3
    assert database.execute(text("SELECT count(*) FROM physical_buildings")).scalar_one() == 0
    assert database.execute(text("SELECT count(*) FROM hpd_refresh_rollback_rows")).scalar_one() == 0
    assert database.execute(text("SELECT status FROM ingestion_jobs ORDER BY id DESC LIMIT 1")).scalar_one() == "failed"


def test_real_postgres_previews_and_failed_attempts_cannot_freshen_data(database):
    database.execute(text("""
        INSERT INTO ingestion_jobs
            (job_type, source, status, started_at, finished_at, failed, config, created_at, updated_at)
        VALUES
            ('buildings', 'buildings', 'completed', now()-interval '10 days', now()-interval '10 days', 0, '{}', now(), now()),
            ('buildings_preview', 'buildings', 'completed', now(), now(), 0, '{}', now(), now()),
            ('buildings', 'buildings', 'failed', now(), now(), 1, '{}', now(), now()),
            ('buildings', 'buildings', 'completed', now(), now(), 0, jsonb_build_object('dry_run', true), now(), now())
    """))
    row = database.execute(SUCCESSFUL_BUILDING_REFRESH_SQL).mappings().one()
    from datetime import datetime, timezone

    assert (datetime.now(timezone.utc) - row["finished_at"]).days == 10


def test_real_postgres_compliance_publish_and_all_scope_reads(database, monkeypatch):
    monkeypatch.setenv("COMPLIANCE_INTELLIGENCE_ENABLED", "true")
    ingest.ingest_buildings_from_hpd.run(
        dry_run=False, confirm_execute=True, expected_source_fingerprint="a" * 64,
    )
    database.execute(text("""
        INSERT INTO leads (lead_id, company_name, created_at, updated_at)
        VALUES ('pg-smoke-pm', 'Synthetic test manager', now(), now())
    """))
    database.execute(text("""
        INSERT INTO building_management
            (bbl, lead_id, role, is_current, created_at, updated_at)
        VALUES ('3025217501', 'pg-smoke-pm', 'Agent', true, now(), now())
    """))
    with Session(database, join_transaction_mode="create_savepoint") as session:
        first = publish_snapshot(session, compliance_snapshot(), run_id="pg-smoke-1")
        second = publish_snapshot(session, compliance_snapshot(), run_id="pg-smoke-2")
        assert first["inserted"] == 4
        assert second["unchanged"] == 4
        assert database.execute(text("SELECT count(*) FROM compliance_observations")).scalar_one() == 4

        class ReadAdapter:
            async def execute(self, statement, params=None):
                return session.execute(statement, params)

        for scope_type, scope_id, count in (
            ("parcel", "3025217501", 4),
            ("portfolio", "pg-smoke-pm", 4),
            ("building", "3348179", 1),
        ):
            response = asyncio.run(load_compliance(ReadAdapter(), scope_type=scope_type, scope_id=scope_id))
            assert response["coverage"]["status"] == "complete"
            assert response["coverage"]["physical_building_count"] == count
            assert response["coverage"]["active_records_count"] == count
            assert response["reported_balance_cents"] is None
