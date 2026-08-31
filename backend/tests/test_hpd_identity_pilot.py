"""The bounded identity bootstrap preserves parcel, contact, and global-age data."""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.ingest import hpd_identity_pilot as source
from src.tasks import identity_pilot
from tests.test_building_identity_refresh import _green_contacts, _green_rows
from tests.test_refresh_postgres import database  # noqa: F401

BINS = sorted(row["bin"] for row in _green_rows())
POSTGRES = pytest.mark.skipif(os.environ.get("RUN_POSTGRES_INTEGRATION") != "1", reason="Explicit disposable PostgreSQL environment required")


class FixtureClient(source.HPDIdentityPilotClient):
    def __init__(self, registrations=None, contacts=None):
        self.registrations = [
            {**row, source.ROW_ID: f"reg-{index}"}
            for index, row in enumerate(_green_rows() if registrations is None else registrations)
        ]
        self.contacts = [
            {**row, source.ROW_ID: f"contact-{index}"}
            for index, row in enumerate(_green_contacts() if contacts is None else contacts)
        ]
        self.fetch_count = 0
        self.metadata_calls = 0

    def metadata(self, dataset):
        self.metadata_calls += 1
        return {"rows_updated_at": 1786546925}

    def _fetch_complete(self, dataset, where, *, limit, page_size):
        self.fetch_count += 1
        if dataset == source.CONTACTS:
            rows = self.contacts
        elif self.fetch_count == 1:
            rows = [row for row in self.registrations if f"'{row['bin']}'" in where]
        else:
            rows = self.registrations
        return rows, {"dataset": dataset, "where": where, "count": len(rows), "content_digest": source.fingerprint(sorted(source.fingerprint(row) for row in rows))}


def test_bounded_source_retains_four_bins_exact_source_ids_and_contacts_as_evidence():
    snapshot = FixtureClient().fetch_snapshot(BINS)
    assert snapshot["bins"] == BINS
    assert len(snapshot["physical_buildings"]) == 4
    assert len(snapshot["parcel_links"]) == 4
    assert len(snapshot["registration_snapshots"]) == 4
    assert {row["hpd_building_id"] for row in snapshot["registration_snapshots"]} == {"822087", "822089", "822090", "822091"}
    assert len(snapshot["source_fingerprint"]) == 64
    assert 0 < snapshot["evidence_bytes"] < source.MAX_EVIDENCE_BYTES
    assert source.ROW_ID not in snapshot["registration_snapshots"][0]["raw_payload"]["registration"]
    assert source.ROW_ID not in snapshot["registration_snapshots"][0]["raw_payload"]["contacts"][0]


@pytest.mark.parametrize("bins", [None, [], ["822087"], [3348179], [" 3348179"], ["6348179"], BINS * 7])
def test_only_explicit_exact_one_to_twenty_five_bins_are_allowed(bins):
    with pytest.raises(source.IdentityPilotError):
        source.validate_bins(bins)


def test_omitted_parcel_siblings_fail_closed_and_identify_required_scope():
    with pytest.raises(source.IdentityPilotError) as exc:
        FixtureClient().fetch_snapshot([BINS[0]])
    assert exc.value.code == "omitted_or_conflicting_sibling_bins"
    assert set(exc.value.details["additional_bins"]) == set(BINS[1:])
    assert len(exc.value.details["evidence"]) == 4


def test_shared_registration_sibling_on_other_parcel_is_not_silently_omitted():
    outside = {**_green_rows()[0], "bin": "3999999", "buildingid": "999999", "block": "2522"}
    with pytest.raises(source.IdentityPilotError, match="omitted_or_conflicting_sibling_bins"):
        FixtureClient([*_green_rows(), outside]).fetch_snapshot(BINS)


def test_same_hpd_id_with_multiple_requested_bins_fails_identity_validation():
    rows = _green_rows()
    rows[1]["buildingid"] = rows[0]["buildingid"]
    with pytest.raises(source.IdentityPilotError, match="source_identity_or_contact_conflicts"):
        FixtureClient(rows).fetch_snapshot(BINS)


def test_conflicting_contact_payloads_fail_before_identity_publication():
    contacts = _green_contacts()
    contacts.append({**contacts[0], "corporationname": "Conflicting evidence"})
    with pytest.raises(source.IdentityPilotError, match="source_identity_or_contact_conflicts"):
        FixtureClient(contacts=contacts).fetch_snapshot(BINS)


def test_fingerprint_binds_scope_payload_and_publication_but_not_row_order():
    client = FixtureClient()
    first = client.fetch_snapshot(BINS)
    client.fetch_count = 0
    client.registrations.reverse()
    client.contacts.reverse()
    reordered = client.fetch_snapshot(list(reversed(BINS)))
    assert reordered["source_fingerprint"] == first["source_fingerprint"]
    client.fetch_count = 0
    client.contacts[0]["corporationname"] = "Changed manager source"
    assert client.fetch_snapshot(BINS)["source_fingerprint"] != first["source_fingerprint"]


def test_source_publication_change_and_evidence_size_fail_closed(monkeypatch):
    class ChangedClient(FixtureClient):
        def metadata(self, dataset):
            self.metadata_calls += 1
            return {"rows_updated_at": 1 if self.metadata_calls <= 2 else 2}

    with pytest.raises(source.IdentityPilotError, match="source_publication_changed"):
        ChangedClient().fetch_snapshot(BINS)
    monkeypatch.setattr(source, "MAX_EVIDENCE_BYTES", 0)
    with pytest.raises(source.IdentityPilotError, match="pilot_evidence_storage_limit_exceeded"):
        FixtureClient().fetch_snapshot(BINS)


@pytest.mark.parametrize("responses,error", [
    ([[{"count": "2"}], [{source.ROW_ID: "one"}]], "incomplete_source_page"),
    ([[{"count": "2"}], [{source.ROW_ID: "same"}, {source.ROW_ID: "same"}]], "missing_or_duplicate_source_row_id"),
    ([[{"count": "2"}], [{source.ROW_ID: "one"}, {source.ROW_ID: "two"}], [{"count": "3"}]], "source_count_changed"),
    ([[{"count": "6"}]], "bounded_source_volume_exceeded"),
])
def test_count_pagination_and_unique_row_checks(responses, error, monkeypatch):
    client = source.HPDIdentityPilotClient(session=object())
    queue = iter(responses)
    monkeypatch.setattr(client, "_get", lambda *args, **kwargs: next(queue))
    with pytest.raises(source.IdentityPilotError, match=error):
        client._fetch_complete(source.REGISTRATIONS, "bin='3348179'", limit=5, page_size=2)


def test_complete_pagination_proves_expected_count_and_schema_drift_is_rejected(monkeypatch):
    client = source.HPDIdentityPilotClient(session=object())
    queue = iter([[{"count": "3"}], [{source.ROW_ID: "one"}, {source.ROW_ID: "two"}], [{source.ROW_ID: "three"}], [{"count": "3"}]])
    monkeypatch.setattr(client, "_get", lambda *args, **kwargs: next(queue))
    rows, check = client._fetch_complete(source.REGISTRATIONS, "bin='3348179'", limit=5, page_size=2)
    assert len(rows) == check["count"] == 3
    monkeypatch.setattr(client, "_get", lambda *args, **kwargs: {"columns": [], "rowsUpdatedAt": 1})
    with pytest.raises(source.IdentityPilotError, match="hpd_schema_or_publication_marker_unavailable"):
        client.metadata(source.REGISTRATIONS)


@pytest.mark.parametrize("confirm,expected", [(False, "a" * 64), (True, None), (True, "invalid")])
def test_execute_requires_confirmation_and_reviewed_fingerprint_before_session(monkeypatch, confirm, expected):
    monkeypatch.setattr(identity_pilot, "_get_pg_session", lambda: pytest.fail("Database session must stay unopened"))
    with pytest.raises(source.IdentityPilotError, match="execution_requires_confirmation"):
        identity_pilot.ingest_hpd_identity_pilot.run(bins=BINS, dry_run=False, confirm_execute=confirm, expected_source_fingerprint=expected)


@pytest.fixture
def pilot_db(request, monkeypatch):
    connection = request.getfixturevalue("database")
    monkeypatch.setattr(identity_pilot, "_get_pg_session", lambda: Session(connection, join_transaction_mode="create_savepoint"))
    monkeypatch.setattr(identity_pilot, "HPDIdentityPilotClient", FixtureClient)
    return connection


def _legacy_hash(connection):
    return connection.execute(text("""
        SELECT
            (SELECT md5(string_agg(to_jsonb(b)::text,'|' ORDER BY b.bbl)) FROM buildings b),
            (SELECT md5(string_agg(to_jsonb(c)::text,'|' ORDER BY c.id)) FROM building_contacts c),
            (SELECT count(*) FROM data_quality_log)
    """)).one()


@POSTGRES
def test_postgres_pilot_preview_writes_only_job_audit(pilot_db):
    before = _legacy_hash(pilot_db)
    result = identity_pilot.ingest_hpd_identity_pilot.run(bins=BINS)
    assert result["ready_to_execute"] is True
    assert result["business_rows_written"] == 0
    assert pilot_db.execute(text("SELECT count(*) FROM physical_buildings")).scalar_one() == 0
    assert pilot_db.execute(text("SELECT count(*) FROM hpd_refresh_rollback_rows")).scalar_one() == 0
    assert _legacy_hash(pilot_db) == before
    assert pilot_db.execute(text("SELECT job_type FROM ingestion_jobs ORDER BY id DESC LIMIT 1")).scalar_one() == "hpd_identity_pilot_preview"


@POSTGRES
def test_postgres_pilot_publication_idempotence_before_images_and_legacy_preservation(pilot_db):
    before = _legacy_hash(pilot_db)
    expected = FixtureClient().fetch_snapshot(BINS)["source_fingerprint"]
    first = identity_pilot.ingest_hpd_identity_pilot.run(bins=BINS, dry_run=False, confirm_execute=True, expected_source_fingerprint=expected)
    assert first["published"] is True
    for table in identity_pilot.TABLES:
        assert pilot_db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 4
    assert pilot_db.execute(text("SELECT count(*) FROM hpd_refresh_rollback_rows WHERE ingestion_job_id=:job_id AND NOT was_existing"), {"job_id": first["job_id"]}).scalar_one() == 12
    second = identity_pilot.ingest_hpd_identity_pilot.run(bins=BINS, dry_run=False, confirm_execute=True, expected_source_fingerprint=expected)
    for table in identity_pilot.TABLES:
        assert pilot_db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 4
    assert pilot_db.execute(text("SELECT count(*) FROM hpd_refresh_rollback_rows WHERE ingestion_job_id=:job_id AND was_existing"), {"job_id": second["job_id"]}).scalar_one() == 12
    assert _legacy_hash(pilot_db) == before


@POSTGRES
def test_postgres_pilot_preserves_outside_bin_with_shared_registration_and_history(pilot_db):
    pilot_db.execute(text("""
        INSERT INTO physical_buildings (bin,source_system,source_record_key,first_seen_at,last_seen_at)
        VALUES ('3999999','hpd_registrations','999999',now(),now());
        INSERT INTO building_parcel_links (bin,bbl,relationship_type,source_system,source_record_key,source_url,is_current,first_seen_at,last_seen_at)
        VALUES ('3999999','3999999999','hpd_registration','hpd_registrations','378111','https://data.cityofnewyork.us',true,now(),now());
        INSERT INTO hpd_registration_snapshots (registration_id,payload_hash,hpd_building_id,bin,bbl,is_current,identity_status,source_url,raw_payload,first_seen_at,last_seen_at)
        VALUES ('378111','outside-scope-version','999999','3999999','3999999999',true,'official_hpd','https://data.cityofnewyork.us','{}',now(),now());
    """))
    before = {
        table: pilot_db.execute(text(f"SELECT to_jsonb(t) FROM {table} t WHERE bin='3999999'")).scalar_one()
        for table in identity_pilot.TABLES
    }
    result = identity_pilot.ingest_hpd_identity_pilot.run(bins=BINS, dry_run=False, confirm_execute=True, expected_source_fingerprint=FixtureClient().fetch_snapshot(BINS)["source_fingerprint"])
    for table in identity_pilot.TABLES:
        assert pilot_db.execute(text(f"SELECT to_jsonb(t) FROM {table} t WHERE bin='3999999'")).scalar_one() == before[table]
    assert pilot_db.execute(text("SELECT count(*) FROM hpd_refresh_rollback_rows WHERE ingestion_job_id=:job_id AND before_payload->>'bin'='3999999'"), {"job_id": result["job_id"]}).scalar_one() == 0


@POSTGRES
def test_postgres_pilot_source_change_and_persisted_conflict_fail_closed(pilot_db):
    with pytest.raises(source.IdentityPilotError, match="source_changed_since_reviewed"):
        identity_pilot.ingest_hpd_identity_pilot.run(bins=BINS, dry_run=False, confirm_execute=True, expected_source_fingerprint="a" * 64)
    assert pilot_db.execute(text("SELECT count(*) FROM physical_buildings")).scalar_one() == 0
    pilot_db.execute(text("""
        INSERT INTO physical_buildings (bin,source_system,source_record_key,first_seen_at,last_seen_at)
        VALUES ('3348179','hpd_registrations','999999',now(),now())
    """))
    preview = identity_pilot.ingest_hpd_identity_pilot.run(bins=BINS)
    assert preview["ready_to_execute"] is False
    assert "persisted_physical_identity_conflict" in preview["validation_errors"]
    with pytest.raises(source.IdentityPilotError, match="identity_pilot_preflight_failed"):
        identity_pilot.ingest_hpd_identity_pilot.run(bins=BINS, dry_run=False, confirm_execute=True, expected_source_fingerprint=FixtureClient().fetch_snapshot(BINS)["source_fingerprint"])
    assert pilot_db.execute(text("SELECT count(*) FROM hpd_refresh_rollback_rows")).scalar_one() == 0


@POSTGRES
def test_postgres_pilot_failure_rolls_back_all_identity_business_writes(pilot_db, monkeypatch):
    class FailingSession(Session):
        def execute(self, statement, params=None, **kwargs):
            if "INSERT INTO hpd_registration_snapshots" in str(statement):
                raise RuntimeError("Injected pilot publication failure")
            return super().execute(statement, params, **kwargs)

    monkeypatch.setattr(identity_pilot, "_get_pg_session", lambda: FailingSession(pilot_db, join_transaction_mode="create_savepoint"))
    before = _legacy_hash(pilot_db)
    with pytest.raises(RuntimeError, match="Injected pilot"):
        identity_pilot.ingest_hpd_identity_pilot.run(bins=BINS, dry_run=False, confirm_execute=True, expected_source_fingerprint=FixtureClient().fetch_snapshot(BINS)["source_fingerprint"])
    for table in (*identity_pilot.TABLES, "hpd_refresh_rollback_rows"):
        assert pilot_db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
    assert _legacy_hash(pilot_db) == before
    assert pilot_db.execute(text("SELECT status FROM ingestion_jobs ORDER BY id DESC LIMIT 1")).scalar_one() == "failed"
