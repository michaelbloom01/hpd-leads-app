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
    assert len(snapshot["registration_snapshots"][0]["raw_payload"]["contacts"]) == 1
    assert snapshot["stats"]["duplicate_contact_payloads"] == 1


def test_conflicting_contact_payloads_preserve_existing_registration_contact_scope():
    contacts = _green_contacts()
    contacts.append({**contacts[0], "corporationname": "Conflicting manager"})
    snapshot = ingest._prepare_building_refresh_snapshot(_green_rows(), contacts, [])
    assert snapshot["stats"]["quarantined_contact_registrations"] == 1
    assert snapshot["stats"]["quarantined_identities"] == 0
    assert snapshot["stats"]["current_contacts"] == 3
    assert "378111" not in snapshot["replacement_registration_ids_by_bbl"]["3025217501"]
    assert any(row["source_record_key"] == "contacts:378111" for row in snapshot["quarantine"])
    assert len(snapshot["registration_snapshots"][0]["raw_payload"]["contacts"]) == 2


@pytest.mark.parametrize("overrides,reason", [
    ({"bin": "822087"}, "invalid_bin"),
    ({"bin": "1348179"}, "bin_borough_conflict"),
    ({"bin": ""}, "missing_bin"),
    ({"block": "0"}, "invalid_bbl"),
    ({"buildingid": ""}, "invalid_hpd_building_id"),
    ({"registrationid": "0"}, "invalid_registration_id"),
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


def test_transient_source_row_ids_detect_duplicate_pages_and_never_become_durable_keys(monkeypatch):
    records = [{"__refresh_row_id": "row-1", "value": "same"}, {"__refresh_row_id": "row-2", "value": "same"}]

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return [dict(row) for row in records]

    monkeypatch.setattr(ingest.requests, "get", lambda *args, **kwargs: Response())
    stats = {}
    rows = ingest._socrata_fetch("test", {"$limit": 10}, fetch_stats=stats, validate_source_row_ids=True)
    assert rows == [{"value": "same"}, {"value": "same"}]
    assert stats["unique_source_rows"] == 2
    assert len(stats["source_row_key_digest"]) == 64
    records[1]["__refresh_row_id"] = "row-1"
    with pytest.raises(RuntimeError, match="duplicated source row"):
        ingest._socrata_fetch("test", {"$limit": 10}, fetch_stats={}, validate_source_row_ids=True)


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
        if "information_schema.columns" in sql:
            return _Result(rows=_contact_schema_rows())
        if "SELECT bbl, bin" in sql:
            return _Result(rows=[{"bbl": "3025217501", "bin": "822087"}])
        if "count(*)" in sql:
            return _Result(scalar=1)
        if "hpd_registration_snapshots" in sql:
            return _Result()
        raise AssertionError(sql)


def _contact_schema_rows():
    from src.models.contacts import BuildingContact

    return [
        {"column_name": name, "data_type": "character varying" if getattr(column.type, "length", None) else "text",
         "character_maximum_length": getattr(column.type, "length", None)}
        for name in ingest.CONTACT_TEXT_PARAMETER_COLUMNS.values()
        for column in [BuildingContact.__table__.c[name]]
    ]


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


def test_preview_retires_prior_registration_contacts_only_with_exact_stored_identity(monkeypatch):
    class Session(_PreviewSession):
        def execute(self, statement, params=None):
            if "FROM hpd_registration_snapshots" in str(statement):
                return _Result(rows=[{
                    "hpd_building_id": "822087", "bin": "3348179", "bbl": "3025217501", "registration_id": "123456",
                }])
            return super().execute(statement, params)

    snapshot = ingest._prepare_building_refresh_snapshot(_green_rows(), _green_contacts(), [])
    monkeypatch.setattr(ingest, "_validate_building_refresh_snapshot", lambda stats: [])
    preview = ingest.preview_building_refresh(Session(), snapshot)
    assert preview["diff"]["historical_registration_scopes_added"] == 1
    assert "123456" in snapshot["replacement_registration_ids_by_bbl"]["3025217501"]


def test_publication_code_has_one_complete_generation_boundary():
    import inspect

    source = inspect.getsource(ingest.ingest_buildings_from_hpd.run)
    promotion = source.split("pg_try_advisory_xact_lock", 1)[1].split("except Exception", 1)[0]
    assert promotion.count("session.commit()") == 1
    assert "CLEAR_BATCH_CONTACTS_SQL" not in source
    assert "_persist_building_identity_snapshot" in promotion
    assert "hpd_refresh_rollback_rows" in promotion


def test_full_region_text_and_raw_source_survive_without_truncation(monkeypatch):
    contact = {**_green_contacts()[0], "businessstate": "OFFICE", "businesszip": "112221234"}
    snapshot = ingest._prepare_building_refresh_snapshot(_green_rows()[:1], [contact], [])
    assert snapshot["contacts_by_bbl"]["3025217501"][0]["state"] == "OFFICE"
    assert snapshot["registration_snapshots"][0]["raw_payload"]["contacts"][0]["businessstate"] == "OFFICE"
    for name in ("source_contact_text_profile", "projected_contact_text_profile"):
        profile = snapshot["stats"][name]
        assert profile["rows"] == 1
        assert profile["columns"]["business_state"]["max_length"] == 6
        assert profile["columns"]["business_state"]["length_counts"] == {6: 1}
    monkeypatch.setattr(ingest, "_validate_building_refresh_snapshot", lambda _stats: [])
    assert ingest.preview_building_refresh(_PreviewSession(), snapshot)["ready_to_execute"] is True


def test_preview_blocks_legacy_region_limit_before_business_writes(monkeypatch):
    class LegacySchemaSession(_PreviewSession):
        def execute(self, statement, params=None):
            if "information_schema.columns" in str(statement):
                rows = _contact_schema_rows()
                for row in rows:
                    if row["column_name"] == "business_state":
                        row.update(data_type="character varying", character_maximum_length=5)
                return _Result(rows=rows)
            return super().execute(statement, params)

    snapshot = ingest._prepare_building_refresh_snapshot(
        _green_rows()[:1], [{**_green_contacts()[0], "businessstate": "OFFICE"}], [],
    )
    monkeypatch.setattr(ingest, "_validate_building_refresh_snapshot", lambda _stats: [])
    preview = ingest.preview_building_refresh(LegacySchemaSession(), snapshot)
    assert preview["ready_to_execute"] is False
    assert preview["business_rows_written"] == 0
    assert "contact_column_too_short:business_state:5<6" in preview["validation_errors"]
    state = next(row for row in preview["contact_schema_preflight"]["columns"] if row["column"] == "business_state")
    assert state["over_limit_rows"] == 1


@pytest.mark.parametrize("parameter,column", ingest.CONTACT_TEXT_PARAMETER_COLUMNS.items())
def test_contact_preflight_checks_every_projected_bounded_field(parameter, column):
    class SchemaSession(_PreviewSession):
        def execute(self, statement, params=None):
            assert "information_schema.columns" in str(statement)
            rows = _contact_schema_rows()
            for row in rows:
                if row["column_name"] == column:
                    row.update(data_type="character varying", character_maximum_length=5)
            return _Result(rows=rows)

    contact = dict(ingest._HPDContactParams("", {}))
    contact[parameter] = "abcdef"
    profile = ingest._new_contact_text_profile()
    ingest._profile_contact_text_row(profile, contact)
    result = ingest._contact_schema_preflight(SchemaSession(), profile)
    assert result["validation_errors"] == [f"contact_column_too_short:{column}:5<6"]


def test_contact_preflight_rejects_missing_profile_and_embedded_nul():
    assert ingest._contact_schema_preflight(_PreviewSession(), None)["validation_errors"] == ["contact_projection_profile_required"]
    profile = ingest._new_contact_text_profile()
    ingest._profile_contact_text_row(profile, ingest._HPDContactParams("", {"businessstate": "N\x00Y"}))
    assert ingest._contact_schema_preflight(_PreviewSession(), profile)["validation_errors"] == ["contact_column_contains_nul:business_state"]


def test_preview_recomputes_older_cached_projection_profile_without_claiming_raw_profile(monkeypatch):
    snapshot = ingest._prepare_building_refresh_snapshot(_green_rows(), _green_contacts(), [])
    del snapshot["stats"]["projected_contact_text_profile"]
    del snapshot["stats"]["source_contact_text_profile"]
    monkeypatch.setattr(ingest, "_validate_building_refresh_snapshot", lambda _stats: [])
    preview = ingest.preview_building_refresh(_PreviewSession(), snapshot)
    assert preview["ready_to_execute"] is True
    assert preview["contact_schema_preflight"]["rows_profiled"] == 4
    assert "source_contact_text_profile" not in snapshot["stats"]
    snapshot["stats"]["current_contacts"] += 1
    preview = ingest.preview_building_refresh(_PreviewSession(), snapshot)
    assert "contact_projection_profile_row_count_mismatch" in preview["validation_errors"]
