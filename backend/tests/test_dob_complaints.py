"""Bounded complaint-source, codebook, coverage and publication contracts."""

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import func, select

from src.ingest import dob_complaints as source
from src.ingest.dob_safety import payload_hash
from src.models.compliance import (
    ComplianceObservation,
    ComplianceRecord,
    ComplianceSourceCheck,
)
from src.services.compliance import build_response, publish_snapshot
from tests.test_compliance_intelligence import (
    BBL,
    GREEN,
    NOW,
    response_inputs,
    snapshot,
)
from tests.test_compliance_intelligence import (
    db as shared_db_fixture,
)


@pytest.fixture
def db():
    yield from shared_db_fixture.__wrapped__()


def complaint_row(**overrides):
    return {
        "complaint_number": "TEST-COMPLAINT-1",
        "bin": GREEN[0][1],
        "house_number": "82",
        "house_street": "SYNTHETIC TEST STREET",
        "status": "ACTIVE",
        "complaint_category": "6S",
        "date_entered": "08/29/2026",
        "inspection_date": "08/30/2026",
        "disposition_date": "08/30/2026",
        "disposition_code": "A8",
        "dobrundate": "20260830000000",
        **overrides,
    }


def complaint_snapshot(rows=None, observed_at=NOW):
    rows = [complaint_row()] if rows is None else rows
    return {
        "source_system": source.SOURCE_SYSTEM,
        "bins": [row[1] for row in GREEN],
        "rows": rows,
        "expected_count": len(rows),
        "source_updated_at": NOW,
        "observed_at": observed_at,
        "snapshot_hash": payload_hash(rows),
        "complete": True,
    }


def complaint_check(bin_value=GREEN[0][1], **overrides):
    return {
        "source_system": source.SOURCE_SYSTEM,
        "bin": bin_value,
        "source_updated_at": NOW,
        "observed_at": NOW,
        "ingestion_run_id": "complaints-1",
        "records_count": 1,
        **overrides,
    }


def normalized_complaint(**overrides):
    return source.normalize_record(
        complaint_row(**overrides),
        observed_at=NOW,
        source_updated_at=NOW,
        run_id="complaints-1",
    )


def test_complaint_preserves_source_without_money_narrative_bbl_or_issue_date():
    raw = complaint_row(bbl=BBL, amount="5000", description="Unsupported extra field")
    row = source.normalize_record(
        raw, observed_at=NOW, source_updated_at=NOW, run_id="test"
    )
    assert row["raw_payload"] == raw
    assert row["record_type"] == "complaint"
    assert row["category"] == "DOB_COMPLAINT"
    assert row["bbl"] is row["description"] is row["issue_date"] is None
    assert row["source_record_key"] == raw["complaint_number"]
    assert row["source_url"].startswith(source.RESOURCE_URL)
    assert "amount" not in row


def test_official_code_labels_and_source_dates():
    details = source.complaint_details(complaint_row())
    assert (
        details["complaint_category_label"]
        == "Elevator: Single Device on Property/No Alternate Service"
    )
    assert details["disposition_code_label"] == "OATH Violation Served"
    assert details["received_date"] == "2026-08-29"
    assert (
        details["inspection_date"]
        == details["disposition_date"]
        == details["source_run_date"]
        == "2026-08-30"
    )
    assert details["category_codebook_revision"] == "2021-09"
    assert details["date_parse_warnings"] == []


def test_unknown_codes_and_malformed_dates_remain_explicit():
    details = source.complaint_details(
        complaint_row(
            complaint_category="ZZ",
            disposition_code="ZZ",
            date_entered="31/08/2026",
            inspection_date="",
        )
    )
    assert details["complaint_category"] == "ZZ"
    assert (
        details["complaint_category_label"] is details["disposition_code_label"] is None
    )
    assert details["received_date"] is details["inspection_date"] is None
    assert len(details["date_parse_warnings"]) == 1


def test_zero_padded_category_keeps_raw_code_and_matches_official_numeric_code():
    details = source.complaint_details(complaint_row(complaint_category="05"))
    assert details["complaint_category"] == "05"
    assert details["complaint_category_label"] == source.CATEGORY_LABELS["5"]


@pytest.mark.parametrize(
    "bins", [None, [], ["822087"], ["3348179.0"], ["3348179'"], ["3348179"] * 26]
)
def test_complaint_scope_is_explicit_and_bounded(bins):
    with pytest.raises(ValueError):
        source.validate_bins(bins)


def test_complaint_only_checks_never_prove_safety_coverage():
    inputs = response_inputs()
    inputs.update(records=[normalized_complaint()], checks=[complaint_check()])
    result = build_response(**inputs)
    assert result["coverage"]["checked_building_count"] == 0
    assert result["coverage"]["status"] == "not_checked"
    assert result["coverage"]["active_records_count"] == 0
    assert (
        result["coverage"]["complaints_count"]
        == result["coverage"]["open_complaints_count"]
        == 1
    )
    assert result["source_updated_at"] is result["as_of"] is None
    assert result["reported_balance_cents"] is None
    coverage = {row["source_system"]: row for row in result["source_coverage"]}
    assert coverage["dob_safety"]["status"] == "not_checked"
    assert coverage["dob_complaints"]["checked_building_count"] == 1
    record = next(
        row for building in result["buildings"] for row in building["records"]
    )
    assert record["present_in_latest_check"] and not record["stale"]
    assert record["complaint_category_label"] and record["received_date"]
    assert "raw_payload" not in record


def test_active_complaints_and_active_violations_are_counted_separately():
    inputs = response_inputs()
    inputs["records"] += [
        normalized_complaint(),
        normalized_complaint(complaint_number="CLOSED-TEST", status="CLOSED"),
    ]
    inputs["checks"].append(complaint_check())
    result = build_response(**inputs)
    assert result["coverage"]["active_records_count"] == 4
    assert result["coverage"]["complaints_count"] == 2
    assert result["coverage"]["open_complaints_count"] == 1
    assert result["coverage"]["records_count"] == 6
    assert len(result["provenance"]) == 2


def test_checked_empty_complaints_are_distinguished_from_unchecked():
    inputs = response_inputs()
    inputs["checks"] += [complaint_check(row[1], records_count=0) for row in GREEN]
    result = build_response(**inputs)
    coverage = next(
        row
        for row in result["source_coverage"]
        if row["source_system"] == "dob_complaints"
    )
    assert coverage["status"] == "complete"
    assert coverage["records_count"] == coverage["open_complaints_count"] == 0
    assert not coverage["stale"]
    assert all(
        building["source_checks"][1]["status"] == "checked"
        and building["source_checks"][1]["records_count"] == 0
        for building in result["buildings"]
    )


def test_four_seeded_bins_do_not_claim_complete_94_parcel_portfolio():
    inputs = response_inputs()
    inputs.update(
        scope={"type": "portfolio", "id": "synthetic-manager"},
        scope_parcel_count=94,
        mapped_parcel_count=1,
    )
    result = build_response(**inputs)
    assert (
        result["coverage"]["physical_building_count"]
        == result["coverage"]["checked_building_count"]
        == 4
    )
    assert result["coverage"]["scope_parcel_count"] == 94
    assert result["coverage"]["mapped_parcel_count"] == 1
    assert result["coverage"]["unmapped_parcel_count"] == 93
    assert (
        result["coverage"]["identity_coverage_status"]
        == result["coverage"]["status"]
        == "partial"
    )
    assert result["source_coverage"][0]["status"] == "partial"
    assert result["stale"]
    assert any(
        "93 parcels remain unmapped" in warning for warning in result["warnings"]
    )


def test_empty_identity_keeps_unmapped_denominator():
    result = build_response(
        scope={"type": "portfolio", "id": "synthetic"},
        buildings=[],
        records=[],
        checks=[],
        balances=[],
        identity_ready=False,
        status_override="identity_unavailable",
        scope_parcel_count=94,
        mapped_parcel_count=0,
    )
    assert result["coverage"]["unmapped_parcel_count"] == 94
    assert result["coverage"]["identity_coverage_status"] == "unavailable"


@pytest.mark.parametrize(
    "end_date,status",
    [("2024-09-30", "expired"), ("2027-09-01", "unexpired"), (None, "unknown")],
)
def test_registration_expiry_keeps_source_dates_and_warns(end_date, status):
    inputs = response_inputs()
    inputs["buildings"][0]["hpd_registration"] = {
        "registration_id": "test-registration",
        "registration_end_date": end_date,
        "last_registration_date": "2024-02-12",
        "source_url": "https://data.cityofnewyork.us/d/tesw-yqqr",
    }
    response = build_response(**inputs)
    building = next(row for row in response["buildings"] if row["bin"] == GREEN[0][1])
    assert building["hpd_registration"]["status"] == status
    assert building["hpd_registration"]["registration_end_date"] == end_date
    assert any(
        "latest saved HPD registration ended" in warning
        for warning in response["warnings"]
    ) is (status == "expired")


def test_retained_complaint_marks_only_its_source_evidence_stale():
    inputs = response_inputs()
    inputs["records"].append(normalized_complaint())
    inputs["checks"] += [
        complaint_check(row[1], records_count=0, ingestion_run_id="new-empty")
        for row in GREEN
    ]
    response = build_response(**inputs)
    coverage = {row["source_system"]: row for row in response["source_coverage"]}
    assert coverage["dob_complaints"]["stale"]
    assert not coverage["dob_safety"]["stale"]


def test_multiple_current_registration_records_are_an_explicit_conflict():
    inputs = response_inputs()
    inputs["buildings"][0]["hpd_registration"] = {
        "registration_id": "test-registration",
        "registration_end_date": "2027-09-01",
        "current_record_count": 2,
    }
    response = build_response(**inputs)
    assert (
        response["buildings"][0]["hpd_registration"]["status"]
        == "conflicting_current_records"
    )
    assert any(
        "multiple current official HPD registration records" in warning
        for warning in response["warnings"]
    )


def test_registration_expiry_uses_nyc_calendar_date():
    inputs = response_inputs()
    inputs["now"] = NOW.replace(hour=1)
    inputs["buildings"][0]["hpd_registration"] = {"registration_end_date": "2026-08-30"}
    response = build_response(**inputs)
    assert response["buildings"][0]["hpd_registration"]["status"] == "unexpired"


def test_complaint_publication_is_idempotent_and_source_checks_are_independent(db):
    publish_snapshot(db, snapshot(), run_id="safety-1")
    first = publish_snapshot(db, complaint_snapshot(), run_id="complaints-1")
    second = publish_snapshot(db, complaint_snapshot(), run_id="complaints-2")
    assert first["inserted"] == second["unchanged"] == 1
    assert db.scalar(select(func.count()).select_from(ComplianceRecord)) == 5
    assert db.scalar(select(func.count()).select_from(ComplianceObservation)) == 5
    assert db.scalar(select(func.count()).select_from(ComplianceSourceCheck)) == 8
    assert (
        db.get(ComplianceSourceCheck, ("dob_safety", GREEN[0][1])).ingestion_run_id
        == "safety-1"
    )
    assert (
        db.get(ComplianceSourceCheck, ("dob_complaints", GREEN[0][1])).ingestion_run_id
        == "complaints-2"
    )
    changed = publish_snapshot(
        db,
        complaint_snapshot([complaint_row(status="CLOSED")], NOW + timedelta(hours=1)),
        run_id="complaints-3",
    )
    assert changed["changed"] == 1
    assert db.scalar(select(func.count()).select_from(ComplianceObservation)) == 6


def test_empty_complaint_snapshot_preserves_history_and_safety_checks(db):
    publish_snapshot(db, snapshot(), run_id="safety-1")
    publish_snapshot(db, complaint_snapshot(), run_id="complaints-1")
    publish_snapshot(
        db, complaint_snapshot([], NOW + timedelta(hours=1)), run_id="complaints-2"
    )
    assert db.scalar(select(func.count()).select_from(ComplianceRecord)) == 5
    assert (
        db.get(ComplianceSourceCheck, ("dob_complaints", GREEN[0][1])).records_count
        == 0
    )
    assert db.get(ComplianceSourceCheck, ("dob_safety", GREEN[0][1])).records_count == 1


def fake_client(rows=None, *, changed_metadata=False, after_count=None):
    rows = [complaint_row()] if rows is None else rows
    client = source.DOBComplaintsClient()
    calls = []
    metadata_calls = 0
    count_calls = 0

    def get(url, params=None):
        nonlocal metadata_calls, count_calls
        calls.append((url, params))
        if url == source.METADATA_URL:
            metadata_calls += 1
            return {
                "columns": [{"fieldName": field} for field in source.REQUIRED_FIELDS],
                "rowsUpdatedAt": 100 + int(changed_metadata and metadata_calls > 1),
            }
        if "$select" in params:
            count_calls += 1
            return [
                {
                    "count": str(
                        after_count
                        if after_count is not None and count_calls > 1
                        else len(rows)
                    )
                }
            ]
        return rows[params["$offset"] : params["$offset"] + params["$limit"]]

    client._get = get
    return client, calls


def test_complete_official_snapshot_and_checked_empty_contract():
    client, calls = fake_client()
    result = client.fetch_snapshot([GREEN[0][1]])
    assert result["complete"] and result["source_system"] == "dob_complaints"
    assert result["expected_count"] == 1
    assert len(calls) == 5
    empty, calls = fake_client([])
    assert empty.fetch_snapshot([GREEN[0][1]])["rows"] == []
    assert len(calls) == 4


@pytest.mark.parametrize("changed_metadata,after_count", [(True, None), (False, 2)])
def test_mid_read_changes_fail_closed(changed_metadata, after_count):
    client, _ = fake_client(changed_metadata=changed_metadata, after_count=after_count)
    with pytest.raises(ValueError, match="changed during"):
        client.fetch_snapshot([GREEN[0][1]])


@pytest.mark.parametrize(
    "rows,match",
    [
        ([complaint_row(), complaint_row()], "Duplicate"),
        ([complaint_row(bin="1000001")], "outside"),
        ([complaint_row(complaint_number="")], "missing"),
    ],
)
def test_invalid_source_rows_fail_closed(rows, match):
    client, _ = fake_client(rows)
    with pytest.raises(ValueError, match=match):
        client.fetch_snapshot([GREEN[0][1]])


def test_source_volume_request_limit_is_enforced_before_pagination():
    client, calls = fake_client(
        [complaint_row(complaint_number=str(i)) for i in range(21)]
    )
    with pytest.raises(ValueError, match="bounded request limit"):
        client.fetch_snapshot([GREEN[0][1]], page_size=1)
    assert len(calls) == 2


def test_source_schema_drift_is_rejected():
    client = source.DOBComplaintsClient()
    client._get = lambda *args: {"columns": [], "rowsUpdatedAt": 100}
    with pytest.raises(ValueError, match="schema drift"):
        client.metadata()


def test_complaint_preview_does_not_publish_or_freshen_source(monkeypatch):
    from src.tasks import compliance as task

    fake_db = MagicMock()
    ensure = MagicMock(return_value=123)
    publish = MagicMock()
    quality = MagicMock()
    monkeypatch.setattr(task, "_get_pg_session", lambda: fake_db)
    monkeypatch.setattr(task, "_ensure_or_create_job", ensure)
    monkeypatch.setattr(task, "_finish_job", MagicMock())
    monkeypatch.setattr(task, "publish_snapshot", publish)
    monkeypatch.setattr(task, "_log_quality", quality)
    monkeypatch.setattr(
        source.DOBComplaintsClient,
        "fetch_snapshot",
        lambda self, bins: complaint_snapshot(),
    )
    result = task.ingest_dob_complaints.run(
        bins=[row[1] for row in GREEN], dry_run=True
    )
    assert result["source_system"] == "dob_complaints"
    assert ensure.call_args.args[2:] == (
        "dob_complaints_preview",
        "dob_complaints_preview",
    )
    publish.assert_not_called()
    quality.assert_not_called()


def test_complaint_execute_requires_confirmation():
    from src.tasks.compliance import ingest_dob_complaints

    with pytest.raises(ValueError, match="confirm_execute"):
        ingest_dob_complaints.run(bins=[GREEN[0][1]], dry_run=False)
