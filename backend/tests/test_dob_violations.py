"""Legacy DOB violation normalization, bounded reads and coverage contracts."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.ingest import dob_violations as source
from src.ingest.dob_safety import payload_hash
from src.services.compliance import build_response
from tests.test_compliance_intelligence import GREEN, NOW, response_inputs


def violation_row(**overrides):
    return {
        "isn_dob_bis_viol": "679526",
        "boro": "3",
        "bin": GREEN[0][1],
        "block": "02521",
        "lot": "07501",
        "issue_date": "20260108",
        "violation_type_code": "C",
        "violation_number": "MTF05FE",
        "house_number": "82",
        "street": "SYNTHETIC TEST STREET",
        "disposition_date": "20260312",
        "disposition_comments": "CONDITION RESOLVED",
        "description": "TEST LEGACY VIOLATION",
        "ecb_number": "35299130P",
        "number": "V*010826CMTF05FE",
        "violation_category": "V*-DOB VIOLATION - Resolved",
        "violation_type": "C-CONSTRUCTION",
        **overrides,
    }


def normalized(**overrides):
    return source.normalize_record(
        violation_row(**overrides),
        observed_at=NOW,
        source_updated_at=NOW,
        run_id="legacy-1",
    )


def test_legacy_violation_preserves_exact_identity_status_dates_and_links():
    raw = violation_row()
    row = normalized()
    assert row["source_record_key"] == raw["isn_dob_bis_viol"]
    assert row["bin"] == GREEN[0][1]
    assert row["bbl"] == "3025217501"
    assert row["issue_date"].isoformat() == "2026-01-08"
    assert row["status"] == "resolved"
    assert row["category"] == "DOB_LEGACY_VIOLATION"
    assert row["record_type"] == "violation"
    assert row["source_url"].startswith(source.RESOURCE_URL)
    assert row["raw_payload"] == raw
    assert "amount" not in row


def test_legacy_details_keep_source_numbers_and_disposition_separate():
    details = source.violation_details(violation_row())
    assert details == {
        "dob_violation_isn": "679526",
        "dob_violation_number": "MTF05FE",
        "display_number": "V*010826CMTF05FE",
        "ecb_number": "35299130P",
        "violation_category_raw": "V*-DOB VIOLATION - Resolved",
        "disposition_date": "2026-03-12",
        "disposition_comments": "CONDITION RESOLVED",
    }


@pytest.mark.parametrize(
    "category,status",
    [
        ("V-DOB VIOLATION - ACTIVE", "active"),
        ("V*-DOB VIOLATION - DISMISSED", "resolved"),
        ("V*-DOB VIOLATION - Resolved", "resolved"),
        ("V%-DOB VIOLATION", "unknown"),
    ],
)
def test_legacy_status_uses_source_category_without_inventing_closure(
    category, status
):
    assert normalized(violation_category=category)["status"] == status


def test_legacy_identity_conflict_is_explicit():
    row = normalized(boro="1", block="00716", lot="00055")
    assert row["bbl"] == "1007160055"
    assert row["identity_status"] == "conflicting_source_identifiers"


@pytest.mark.parametrize(
    "overrides",
    [
        {"isn_dob_bis_viol": ""},
        {"isn_dob_bis_viol": "ABC"},
        {"issue_date": "20261301"},
        {"disposition_date": "03/12/2026"},
    ],
)
def test_invalid_source_identity_or_dates_fail_closed(overrides):
    with pytest.raises(ValueError):
        normalized(**overrides)


@pytest.mark.parametrize(
    "bins", [None, [], ["822087"], ["3348179.0"], ["3348179'"] , ["3348179"] * 26]
)
def test_violation_scope_is_explicit_and_bounded(bins):
    with pytest.raises(ValueError):
        source.validate_bins(bins)


def test_response_keeps_legacy_violations_separate_from_safety_coverage():
    inputs = response_inputs()
    inputs["records"].append(normalized(violation_category="V-DOB VIOLATION - ACTIVE"))
    inputs["checks"].append(
        {
            "source_system": source.SOURCE_SYSTEM,
            "bin": GREEN[0][1],
            "source_updated_at": NOW,
            "observed_at": NOW,
            "ingestion_run_id": "legacy-1",
            "records_count": 1,
        }
    )
    response = build_response(**inputs)
    coverage = {row["source_system"]: row for row in response["source_coverage"]}
    assert coverage[source.SOURCE_SYSTEM]["checked_building_count"] == 1
    assert coverage[source.SOURCE_SYSTEM]["active_records_count"] == 1
    assert response["coverage"]["checked_building_count"] == 4
    record = next(
        row
        for building in response["buildings"]
        for row in building["records"]
        if row["source_system"] == source.SOURCE_SYSTEM
    )
    assert record["ecb_number"] == "35299130P"
    assert record["disposition_date"] == "2026-03-12"
    assert "raw_payload" not in record


def test_client_uses_count_verified_exact_bin_pages():
    metadata = {
        "rowsUpdatedAt": int(datetime(2026, 8, 31, tzinfo=timezone.utc).timestamp()),
        "columns": [{"fieldName": field} for field in source.REQUIRED_FIELDS],
    }
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.side_effect = [
        metadata,
        [{"count": "1"}],
        [violation_row()],
        [{"count": "1"}],
        metadata,
    ]
    session = MagicMock()
    session.get.return_value = response
    snapshot = source.DOBViolationsClient(session=session).fetch_snapshot(
        [GREEN[0][1]], page_size=1
    )
    assert snapshot["complete"]
    assert snapshot["expected_count"] == 1
    assert snapshot["snapshot_hash"] == payload_hash(snapshot["rows"])
    page_params = session.get.call_args_list[2].kwargs["params"]
    assert page_params["$where"] == f"bin in ('{GREEN[0][1]}')"
    assert page_params["$order"] == "isn_dob_bis_viol,bin"


def test_client_rejects_duplicate_source_isns():
    metadata = {
        "rowsUpdatedAt": int(datetime(2026, 8, 31, tzinfo=timezone.utc).timestamp()),
        "columns": [{"fieldName": field} for field in source.REQUIRED_FIELDS],
    }
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.side_effect = [
        metadata,
        [{"count": "2"}],
        [violation_row(), violation_row()],
        [{"count": "2"}],
        metadata,
    ]
    session = MagicMock()
    session.get.return_value = response
    with pytest.raises(ValueError, match="Duplicate or missing"):
        source.DOBViolationsClient(session=session).fetch_snapshot(
            [GREEN[0][1]], page_size=2
        )
