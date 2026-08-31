"""Physical identity, source history, and no-write refresh preview contract."""

from datetime import datetime, timezone

import pytest

from src.tasks import ingest


def _green_rows():
    return [
        {
            "registrationid": str(378111 + index), "buildingid": str(hpd_id), "bin": str(bin_value),
            "boroid": "3", "boro": "BROOKLYN", "block": "2521", "lot": "7501",
            "housenumber": str(82 + index * 2), "streetname": "GREEN STREET", "zip": "11222",
            "lastregistrationdate": "2025-07-09T00:00:00.000", "registrationenddate": "2026-09-01T00:00:00.000",
        }
        for index, (hpd_id, bin_value) in enumerate(((822087, 3348179), (822089, 3348178), (822090, 3064119), (822091, 3350268)))
    ]


def _green_contacts():
    return [
        {"registrationid": str(378111 + index), "registrationcontactid": str(9000 + index),
         "type": "Agent", "corporationname": "Brownstone Management"}
        for index in range(4)
    ]


def test_green_street_keeps_four_physical_buildings_one_parcel_and_every_contact():
    snapshot = ingest._prepare_building_refresh_snapshot(_green_rows(), _green_contacts(), [])
    assert len(snapshot["buildings"]) == 1
    assert snapshot["buildings"][0]["bin"] == "3348179"
    assert snapshot["buildings"][0]["address"] == "82 GREEN STREET"
    assert {row["bin"] for row in snapshot["physical_buildings"]} == {"3348179", "3348178", "3064119", "3350268"}
    assert {row["hpd_building_id"] for row in snapshot["registration_snapshots"]} == {"822087", "822089", "822090", "822091"}
    assert len(snapshot["contacts_by_bbl"]["3025217501"]) == 4
    assert len(snapshot["current_registrations_by_bbl"]["3025217501"]) == 4
    assert len(snapshot["parcel_links"]) == 4
    assert snapshot["stats"]["multi_bin_parcels"] == 1
    assert snapshot["stats"]["quarantined_identities"] == 0


def test_registration_selection_is_deterministic_and_history_retains_contacts():
    current = _green_rows()[0]
    old = {**current, "registrationid": "123456", "lastregistrationdate": "2020-01-01"}
    contacts = [*_green_contacts()[:1], {"registrationid": "123456", "registrationcontactid": "5000", "type": "HeadOfficer", "firstname": "Historical"}]
    first = ingest._prepare_building_refresh_snapshot([old, current], contacts, [])
    second = ingest._prepare_building_refresh_snapshot([current, old], contacts, [])
    assert first["buildings"] == second["buildings"]
    assert first["current_registrations_by_bbl"] == second["current_registrations_by_bbl"]
    assert first["stats"]["historical_registrations"] == 1
    historical = next(row for row in first["registration_snapshots"] if row["registration_id"] == "123456")
    assert historical["is_current"] is False
    assert historical["raw_payload"]["contacts"][0]["firstname"] == "Historical"
    assert len(first["contacts_by_bbl"]["3025217501"]) == 1
    assert set(first["replacement_registration_ids_by_bbl"]["3025217501"]) == {"123456", "378111"}


def test_registration_group_can_cover_multiple_official_physical_structures():
    rows = [{**row, "registrationid": "911741"} for row in _green_rows()]
    snapshot = ingest._prepare_building_refresh_snapshot(rows, [], [])
    assert snapshot["stats"]["quarantined_identities"] == 0
    assert len(snapshot["physical_buildings"]) == 4
    assert len(snapshot["registration_snapshots"]) == 4


def test_same_registration_versions_publish_one_current_parcel_link_in_any_order():
    current = _green_rows()[0]
    historical = {**current, "lastregistrationdate": "2020-01-01"}
    for rows in ([current, historical], [historical, current]):
        snapshot = ingest._prepare_building_refresh_snapshot(rows, [], [])
        assert len(snapshot["registration_snapshots"]) == 2
        assert len(snapshot["parcel_links"]) == 1
        assert snapshot["parcel_links"][0]["is_current"] is True
        assert str(snapshot["parcel_links"][0]["effective_from"]) == "2025-07-09"


def test_duplicate_registration_and_contact_payloads_are_idempotent():
    row, contact = _green_rows()[0], _green_contacts()[0]
    snapshot = ingest._prepare_building_refresh_snapshot([row, row], [contact, contact], [])
    assert len(snapshot["registration_snapshots"]) == 1
    assert len(snapshot["physical_buildings"]) == 1
    assert len(snapshot["contacts_by_bbl"]["3025217501"]) == 1


@pytest.mark.parametrize("overrides,reason", [
    ({"bin": "822087"}, "invalid_bin"),
    ({"bin": "1348179"}, "bin_borough_conflict"),
    ({"bin": ""}, "missing_bin"),
    ({"block": "0"}, "invalid_bbl"),
    ({"buildingid": ""}, "invalid_hpd_building_id"),
])
def test_invalid_identity_is_quarantined_with_raw_evidence(overrides, reason):
    raw = {**_green_rows()[0], **overrides}
    snapshot = ingest._prepare_building_refresh_snapshot([raw], [], [])
    assert snapshot["buildings"] == []
    assert snapshot["physical_buildings"] == []
    assert reason in snapshot["quarantine"][0]["reason"]
    assert snapshot["quarantine"][0]["raw_payload"]["registration"] == raw
    assert snapshot["registration_snapshots"][0]["is_current"] is False


def test_valid_length_conflicting_bin_is_quarantined_for_every_affected_version():
    original = _green_rows()[0]
    conflict = {**original, "registrationid": "999999", "bin": "3348100"}
    snapshot = ingest._prepare_building_refresh_snapshot([original, conflict], [], [])
    assert len(snapshot["quarantine"]) == 2
    assert all("hpd_building_multiple_bins" in row["reason"] for row in snapshot["quarantine"])
    assert not snapshot["physical_buildings"]


def test_conflict_resolution_changes_derived_status_without_changing_source_payload():
    row = _green_rows()[0]
    conflict = {**row, "registrationid": "999999", "bin": "3348100"}
    prior = ingest._prepare_building_refresh_snapshot([row, conflict], [], [])
    resolved = ingest._prepare_building_refresh_snapshot([row], [], [])
    old_version = prior["registration_snapshots"][0]
    new_version = resolved["registration_snapshots"][0]
    assert old_version["payload_hash"] == new_version["payload_hash"]
    assert old_version["identity_status"] == "quarantined"
    assert new_version["identity_status"] == "official_hpd"
    statements = []

    class Session:
        def execute(self, statement, params=None):
            statements.append(str(statement))

    ingest._persist_building_identity_snapshot(Session(), resolved, job_id=1)
    upsert = next(sql for sql in statements if "ON CONFLICT (registration_id,payload_hash)" in sql)
    assert "identity_status=EXCLUDED.identity_status" in upsert
    assert "source_updated_at=EXCLUDED.source_updated_at" in upsert


def test_shared_bin_across_different_hpd_structures_is_quarantined():
    one, two = _green_rows()[:2]
    two = {**two, "bin": one["bin"]}
    snapshot = ingest._prepare_building_refresh_snapshot([one, two], [], [])
    assert snapshot["stats"]["quarantined_identities"] == 2
    assert not snapshot["physical_buildings"]


def test_source_timestamp_and_original_identifier_names_survive():
    updated = datetime(2026, 8, 12, tzinfo=timezone.utc)
    snapshot = ingest._prepare_building_refresh_snapshot(_green_rows(), [], [], source_updated_at=updated)
    row = snapshot["registration_snapshots"][0]
    assert row["source_updated_at"] == updated
    assert row["raw_payload"]["registration"]["buildingid"] == "822087"
    assert row["raw_payload"]["registration"]["bin"] == "3348179"
    assert len(row["payload_hash"]) == 64


def test_contact_scope_keeps_all_registrations_and_excludes_board_roles():
    snapshot = ingest._prepare_building_refresh_snapshot(_green_rows(), _green_contacts(), [])
    scope = ingest._contact_refresh_scope(snapshot, ["3025217501"])
    assert {row["registration_id"] for row in scope} == {"378111", "378112", "378113", "378114"}
    assert "BoardHead" not in ingest.HPD_SOURCE_CONTACT_TYPES
    assert "BoardPresident" not in ingest.HPD_SOURCE_CONTACT_TYPES
    assert "registration_contact_id IS NOT NULL" in ingest.CONTACT_REFRESH_SCOPE_SQL
    assert "s.registration_id = bc.registration_id" in ingest.CONTACT_REFRESH_SCOPE_SQL


def test_source_count_drift_fails_before_snapshot_preparation(monkeypatch):
    calls = 0

    def stamp(_dataset_id):
        nonlocal calls
        calls += 1
        return {"count": 1 if calls <= 3 else 2, "rows_updated_at": 1}

    monkeypatch.setattr(ingest, "_source_snapshot_stamp", stamp)
    monkeypatch.setattr(ingest, "_socrata_fetch", lambda *args, **kwargs: [{}])
    with pytest.raises(RuntimeError, match="Incomplete or changing source"):
        ingest.fetch_building_refresh_snapshot()


def test_source_missing_page_fails_even_with_unchanged_metadata(monkeypatch):
    monkeypatch.setattr(ingest, "_source_snapshot_stamp", lambda *_: {"count": 2, "rows_updated_at": 1})
    monkeypatch.setattr(ingest, "_socrata_fetch", lambda *args, **kwargs: [{}])
    with pytest.raises(RuntimeError, match="Incomplete or changing source"):
        ingest.fetch_building_refresh_snapshot()


def test_fetch_digest_binds_full_content_and_is_independent_of_row_order(monkeypatch):
    records = [{"id": "1", "value": "a"}, {"id": "2", "value": "b"}]

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return records

    monkeypatch.setattr(ingest.requests, "get", lambda *args, **kwargs: Response())
    first, reversed_stats, changed = {}, {}, {}
    kept = ingest._socrata_fetch("test", {"$limit": 10}, row_filter=lambda row: row["id"] == "1", fetch_stats=first)
    assert kept == [records[0]]
    assert first["records_fetched"] == 2
    records.reverse()
    ingest._socrata_fetch("test", {"$limit": 10}, fetch_stats=reversed_stats)
    assert first["content_digest"] == reversed_stats["content_digest"]
    records[0] = {**records[0], "value": "changed"}
    ingest._socrata_fetch("test", {"$limit": 10}, fetch_stats=changed)
    assert changed["records_fetched"] == first["records_fetched"]
    assert changed["content_digest"] != first["content_digest"]


def test_execute_requires_confirmation_and_reviewed_fingerprint():
    with pytest.raises(ValueError, match="confirm_execute"):
        ingest.ingest_buildings_from_hpd.run(dry_run=False)
    with pytest.raises(ValueError, match="expected_source_fingerprint"):
        ingest.ingest_buildings_from_hpd.run(dry_run=False, confirm_execute=True)


class _Result:
    def __init__(self, scalar=None, rows=None):
        self.value, self.rows = scalar, rows or []

    def scalar(self):
        return self.value

    def mappings(self):
        return iter(self.rows)


class _PreviewSession:
    def __init__(self):
        self.statements = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        assert sql.strip().startswith("SELECT")
        if "to_regclass" in sql:
            return _Result(scalar=True)
        if "SELECT bbl, bin" in sql:
            return _Result(rows=[{"bbl": "3025217501", "bin": "822087"}])
        if "count(*)" in sql:
            return _Result(scalar=1)
        if "hpd_registration_snapshots" in sql:
            return _Result()
        raise AssertionError(sql)


def test_preview_reports_exact_diff_without_any_business_write(monkeypatch):
    snapshot = ingest._prepare_building_refresh_snapshot(_green_rows(), _green_contacts(), [])
    monkeypatch.setattr(ingest, "_validate_building_refresh_snapshot", lambda stats: [])
    session = _PreviewSession()
    preview = ingest.preview_building_refresh(session, snapshot)
    assert preview["business_rows_written"] == 0
    assert preview["source_snapshot"]["current_physical_buildings"] == 4
    assert preview["diff"]["legacy_bin_corrections"] == 1
    assert preview["diff"]["bin_correction_samples"] == [{"bbl": "3025217501", "before": "822087", "after": "3348179"}]
    assert preview["diff"]["hpd_contact_rows_to_replace"] == 1
    assert preview["diff"]["hpd_contact_rows_to_insert"] == 4
    assert preview["ready_to_execute"] is True


def test_publication_code_has_one_complete_generation_boundary():
    import inspect

    source = inspect.getsource(ingest.ingest_buildings_from_hpd.run)
    promotion = source.split("pg_try_advisory_xact_lock", 1)[1].split("except Exception", 1)[0]
    assert promotion.count("session.commit()") == 1
    assert "CLEAR_BATCH_CONTACTS_SQL" not in source
    assert "_persist_building_identity_snapshot" in promotion
    assert "hpd_refresh_rollback_rows" in promotion
