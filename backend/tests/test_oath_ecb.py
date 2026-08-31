"""OATH exact-ticket linking, signed balance and bounded-read contracts."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.ingest import oath_ecb as source
from src.ingest.dob_safety import payload_hash
from src.services.compliance import build_response
from tests.test_compliance_intelligence import GREEN, NOW, response_inputs


def ecb_seed(**overrides):
    return {
        "ecb_violation_number": "35299130P",
        "bin": GREEN[0][1],
        **overrides,
    }


def oath_row(**overrides):
    return {
        "ticket_number": "035299130P",
        "issuing_agency": "DEPT. OF BUILDINGS",
        "balance_due": "0",
        "violation_location_borough": "BROOKLYN",
        "violation_location_block_no": "02521",
        "violation_location_lot_no": "7501",
        "violation_location_house": "82",
        "violation_location_street_name": "SYNTHETIC TEST STREET",
        "hearing_status": "PAID IN FULL",
        "hearing_result": "IN VIOLATION",
        "hearing_date": "2018-10-18T00:00:00.000",
        "decision_date": "2018-10-22T00:00:00.000",
        "date_judgment_docketed": "2019-01-31T00:00:00.000",
        "penalty_imposed": "2500",
        "paid_amount": "2552",
        "additional_penalties_or_late_fees": "0",
        "total_violation_amount": "1000",
        "compliance_status": "All Terms Met",
        "violation_description": "SYNTHETIC SOURCE DESCRIPTION",
        "violation_details": "SYNTHETIC SOURCE DETAILS",
        "violation_date": "2018-01-19T00:00:00.000",
        "_source_bin": GREEN[0][1],
        "_linked_dob_ecb_violation_number": "35299130P",
        **overrides,
    }


def normalized(**overrides):
    return source.normalize_record(
        oath_row(**overrides),
        observed_at=NOW,
        source_updated_at=NOW,
        run_id="oath-1",
    )


def test_leading_zero_ticket_links_exactly_and_preserves_oath_evidence():
    row = normalized()
    details = source.oath_details(row["raw_payload"])
    assert source.canonical_ticket("035299130P") == "35299130P"
    assert row["source_record_key"] == "035299130P"
    assert row["bin"] == GREEN[0][1]
    assert row["bbl"] == "3025217501"
    assert row["identity_status"] == "linked_via_exact_ticket"
    assert row["record_type"] == "case_evidence"
    assert row["status"] == "resolved"
    assert details["linked_dob_ecb_violation_number"] == "35299130P"
    assert details["judgment_docketed_date"] == "2019-01-31"
    assert details["oath_balance_due_cents"] == 0
    assert details["amount_paid_cents"] == 255200


def test_negative_oath_balance_is_preserved_as_credit_or_adjustment():
    row = normalized(balance_due="-53.09")
    details = source.oath_details(row["raw_payload"])
    assert row["status"] == "resolved"
    assert details["oath_balance_due_cents"] == -5309
    assert details["oath_balance_character"] == "credit_or_adjustment"


@pytest.mark.parametrize(
    "overrides",
    [
        {"ticket_number": "035299130X"},
        {"_source_bin": "3348179.0"},
        {"hearing_date": "not-a-date"},
        {"paid_amount": "3.141"},
    ],
)
def test_invalid_ticket_link_identity_or_value_fails_closed(overrides):
    with pytest.raises(ValueError):
        normalized(**overrides)


def test_oath_response_exposes_case_money_without_counting_another_violation():
    inputs = response_inputs()
    inputs["records"].append(normalized())
    inputs["checks"].append(
        {
            "source_system": source.SOURCE_SYSTEM,
            "bin": GREEN[0][1],
            "source_updated_at": NOW,
            "observed_at": NOW,
            "ingestion_run_id": "oath-1",
            "records_count": 1,
        }
    )
    response = build_response(**inputs)
    assert response["coverage"]["active_records_count"] == 4
    record = next(
        row
        for building in response["buildings"]
        for row in building["records"]
        if row["source_system"] == source.SOURCE_SYSTEM
    )
    assert record["oath_balance_due_cents"] == 0
    assert record["judgment_docketed_date"] == "2019-01-31"
    assert record["monetary_rollup_status"] == "record_only_exact_oath_ticket_evidence"
    assert "raw_payload" not in record


def test_client_uses_count_verified_exact_ticket_candidates():
    metadata = {
        "rowsUpdatedAt": int(datetime(2026, 8, 31, tzinfo=timezone.utc).timestamp()),
        "columns": [{"fieldName": field} for field in source.REQUIRED_FIELDS],
    }
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.side_effect = [
        metadata,
        [{"count": "1"}],
        [{key: value for key, value in oath_row().items() if not key.startswith("_")}],
        [{"count": "1"}],
        metadata,
    ]
    session = MagicMock()
    session.get.return_value = response
    snapshot = source.OATHECBClient(session=session).fetch_seeded_snapshot(
        [GREEN[0][1]], [ecb_seed()], page_size=1
    )
    assert snapshot["complete"]
    assert snapshot["expected_count"] == 1
    assert snapshot["snapshot_hash"] == payload_hash(snapshot["rows"])
    assert snapshot["rows"][0]["_source_bin"] == GREEN[0][1]
    page_params = session.get.call_args_list[2].kwargs["params"]
    assert "'35299130P'" in page_params["$where"]
    assert "'035299130P'" in page_params["$where"]


def test_seed_scope_rejects_ticket_collision_across_bins():
    with pytest.raises(ValueError, match="multiple reviewed BINs"):
        source._validated_seed_map(
            [ecb_seed(), ecb_seed(bin=GREEN[1][1])],
            [GREEN[0][1], GREEN[1][1]],
        )
