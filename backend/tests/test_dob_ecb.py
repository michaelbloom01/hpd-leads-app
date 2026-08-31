"""DOB ECB normalization, monetary isolation and bounded-read contracts."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.ingest import dob_ecb as source
from src.ingest.dob_safety import payload_hash
from src.services.compliance import build_response
from tests.test_compliance_intelligence import GREEN, NOW, balance, response_inputs


def ecb_row(**overrides):
    return {
        "isn_dob_bis_extract": "1360799",
        "ecb_violation_number": "35299130P",
        "ecb_violation_status": "ACTIVE",
        "dob_violation_number": "011918CMTFRS02",
        "bin": GREEN[0][1],
        "boro": "3",
        "block": "02521",
        "lot": "07501",
        "hearing_date": "20260201",
        "served_date": "20260109",
        "issue_date": "20260108",
        "severity": "CLASS - 1",
        "violation_type": "Plumbing",
        "respondent_name": "SYNTHETIC RESPONDENT",
        "violation_description": "SYNTHETIC SOURCE DESCRIPTION",
        "penality_imposed": "2500",
        "amount_paid": "500.25",
        "balance_due": "1999.75",
        "hearing_status": "IN VIOLATION",
        "certification_status": "NO COMPLIANCE RECORDED",
        **overrides,
    }


def normalized(**overrides):
    return source.normalize_record(
        ecb_row(**overrides),
        observed_at=NOW,
        source_updated_at=NOW,
        run_id="ecb-1",
    )


def test_ecb_record_preserves_ticket_identity_and_keeps_money_record_scoped():
    row = normalized()
    details = source.ecb_details(row["raw_payload"])
    assert row["source_record_key"] == "35299130P"
    assert row["bbl"] == "3025217501"
    assert row["issue_date"].isoformat() == "2026-01-08"
    assert row["status"] == "active"
    assert row["record_type"] == "violation"
    assert row["category"] == "DOB_ECB_VIOLATION"
    assert details["penalty_imposed_cents"] == 250000
    assert details["amount_paid_cents"] == 50025
    assert details["balance_due_cents"] == 199975
    assert (
        details["monetary_rollup_status"]
        == "record_only_pending_ecb_oath_deduplication"
    )
    assert "balance_due_cents" not in row


@pytest.mark.parametrize(
    "field,value",
    [
        ("penality_imposed", "-1"),
        ("amount_paid", "3.141"),
        ("balance_due", "unknown"),
        ("balance_due", "10000000001"),
    ],
)
def test_invalid_ecb_money_fails_closed(field, value):
    with pytest.raises(ValueError):
        normalized(**{field: value})


@pytest.mark.parametrize(
    "status,expected",
    [("ACTIVE", "active"), ("RESOLVE", "resolved"), ("Unknown", "unknown")],
)
def test_ecb_status_is_source_explicit(status, expected):
    assert normalized(ecb_violation_status=status)["status"] == expected


def test_ecb_response_exposes_record_money_without_changing_portfolio_subtotal():
    inputs = response_inputs()
    inputs["balances"] = [balance(row[1]) for row in GREEN]
    inputs["records"].append(normalized())
    inputs["checks"].append(
        {
            "source_system": source.SOURCE_SYSTEM,
            "bin": GREEN[0][1],
            "source_updated_at": NOW,
            "observed_at": NOW,
            "ingestion_run_id": "ecb-1",
            "records_count": 1,
        }
    )
    response = build_response(**inputs)
    assert response["reported_balance_cents"] == 2_000_000
    record = next(
        row
        for building in response["buildings"]
        for row in building["records"]
        if row["source_system"] == source.SOURCE_SYSTEM
    )
    assert record["balance_due_cents"] == 199975
    assert (
        record["monetary_rollup_status"]
        == "record_only_pending_ecb_oath_deduplication"
    )
    assert record["served_date"] == "2026-01-09"
    assert "raw_payload" not in record


def test_client_uses_count_verified_ticket_pages():
    metadata = {
        "rowsUpdatedAt": int(datetime(2026, 8, 31, tzinfo=timezone.utc).timestamp()),
        "columns": [{"fieldName": field} for field in source.REQUIRED_FIELDS],
    }
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.side_effect = [
        metadata,
        [{"count": "1"}],
        [ecb_row()],
        [{"count": "1"}],
        metadata,
    ]
    session = MagicMock()
    session.get.return_value = response
    snapshot = source.DOBECBClient(session=session).fetch_snapshot(
        [GREEN[0][1]], page_size=1
    )
    assert snapshot["complete"]
    assert snapshot["expected_count"] == 1
    assert snapshot["snapshot_hash"] == payload_hash(snapshot["rows"])
    page_params = session.get.call_args_list[2].kwargs["params"]
    assert page_params["$where"] == f"bin in ('{GREEN[0][1]}')"
    assert page_params["$order"] == "ecb_violation_number,bin"


def test_client_rejects_duplicate_ticket_numbers():
    metadata = {
        "rowsUpdatedAt": int(datetime(2026, 8, 31, tzinfo=timezone.utc).timestamp()),
        "columns": [{"fieldName": field} for field in source.REQUIRED_FIELDS],
    }
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.side_effect = [
        metadata,
        [{"count": "2"}],
        [ecb_row(), ecb_row()],
        [{"count": "2"}],
        metadata,
    ]
    session = MagicMock()
    session.get.return_value = response
    with pytest.raises(ValueError, match="Duplicate or missing"):
        source.DOBECBClient(session=session).fetch_snapshot(
            [GREEN[0][1]], page_size=2
        )
