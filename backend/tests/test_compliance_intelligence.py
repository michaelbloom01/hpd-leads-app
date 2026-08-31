"""Golden-case, monetary, persistence, source-completeness and auth contracts."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from src.auth.auth import AuthUser, get_current_user
from src.db.session import get_session
from src.ingest.dob_safety import (
    REQUIRED_FIELDS,
    DOBSafetyClient,
    normalize_record,
    payload_hash,
    validate_bins,
)
from src.models.compliance import (
    ComplianceBalanceObservation,
    ComplianceObservation,
    ComplianceRecord,
    ComplianceSourceCheck,
)
from src.routers.compliance import PORTAL_URL, BalanceEvidenceInput, router
from src.services.compliance import build_response, publish_snapshot

NOW = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
BBL = "3025217501"
GREEN = [
    ("82", "3348179", "VIO-FTF-PL-PER-202412-0191146"),
    ("84", "3348178", "VIO-FTF-PL-PER-202412-0191145"),
    ("86", "3064119", "VIO-FTF-PL-PER-202412-0187743"),
    ("88", "3350268", "VIO-FTF-PL-PER-202412-0191193"),
]


def raw_rows():
    return [
        {
            "bin": bin_value,
            "bbl": BBL,
            "house_number": number,
            "street": "GREEN STREET",
            "violation_number": key,
            "violation_type": "FTF-PL-PER",
            "device_type": "Gas Piping - LL152",
            "violation_status": "Active",
            "violation_issue_date": "2026-01-08T00:00:00.000",
            "violation_remarks": "Cycle 2, Sub-cycle A, Community District 1",
        }
        for number, bin_value, key in GREEN
    ]


def snapshot(rows=None, observed_at=NOW):
    rows = raw_rows() if rows is None else rows
    return {
        "bins": [row[1] for row in GREEN],
        "rows": rows,
        "expected_count": len(rows),
        "source_updated_at": NOW - timedelta(hours=2),
        "observed_at": observed_at,
        "snapshot_hash": payload_hash(rows),
        "complete": True,
    }


def response_inputs():
    return {
        "scope": {"type": "parcel", "id": BBL},
        "now": NOW,
        "buildings": [
            {"bin": row[1], "bbl": BBL, "address": row[0] + " GREEN STREET"}
            for row in GREEN
        ],
        "records": [
            normalize_record(
                row, observed_at=NOW, source_updated_at=NOW, run_id="test-run"
            )
            for row in raw_rows()
        ],
        "checks": [
            {
                "bin": row[1],
                "source_updated_at": NOW,
                "observed_at": NOW,
                "ingestion_run_id": "test-run",
            }
            for row in GREEN
        ],
        "balances": [],
    }


def balance(bin_value, amount=500000, observed_at=NOW, key=None):
    return {
        "id": key or bin_value,
        "bin": bin_value,
        "category": "LL152",
        "scope": "bin_category",
        "amount_cents": amount,
        "source_url": PORTAL_URL,
        "source_updated_at": None,
        "source_timestamp_raw": "8/28/2026 10:09",
        "observed_at": observed_at,
        "reviewer": "researcher",
    }


@pytest.fixture
def db():
    # Isolated ephemeral test DB only. Production remains canonical PostgreSQL.
    engine = create_engine("sqlite://")
    event.listen(
        engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON")
    )
    for model in (
        ComplianceRecord,
        ComplianceObservation,
        ComplianceSourceCheck,
        ComplianceBalanceObservation,
    ):
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_four_buildings_one_parcel_and_null_balances():
    result = build_response(**response_inputs())
    assert result["coverage"]["physical_building_count"] == 4
    assert result["coverage"]["active_records_count"] == 4
    assert result["coverage"]["status"] == "complete"
    assert result["reported_balance_cents"] is None
    assert result["coverage"]["missing_balance_bin_count"] == 4
    assert {row["bbl"] for row in result["buildings"]} == {BBL}
    assert all(
        row["interest_status"] == row["lien_status"] == "unverified"
        for row in result["buildings"]
    )
    assert all(
        row["records"][0]["source_url"].startswith("https://data.cityofnewyork.us/")
        for row in result["buildings"]
    )


@pytest.mark.parametrize("scope_type", ["portfolio", "parcel", "building"])
def test_saved_management_membership_warning_is_portfolio_only(scope_type):
    inputs = response_inputs()
    inputs["scope"] = {"type": scope_type, "id": "test-scope"}
    result = build_response(**inputs)
    membership_warnings = [
        warning
        for warning in result["warnings"]
        if "saved management links marked current" in warning
    ]
    assert bool(membership_warnings) is (scope_type == "portfolio")
    if scope_type == "portfolio":
        assert (
            "Verify company membership separately after an HPD source refresh"
            in membership_warnings[0]
        )
        assert (
            "Management at the violation date remains unverified"
            in membership_warnings[0]
        )


def test_four_category_balances_total_twenty_thousand():
    inputs = response_inputs()
    inputs["balances"] = [balance(row[1]) for row in GREEN]
    result = build_response(**inputs)
    assert result["reported_balance_cents"] == 2000000
    assert result["coverage"]["balance_known_building_count"] == 4
    assert result["estimated_penalty_cents"] is None


def test_unknown_is_not_zero():
    inputs = response_inputs()
    inputs["balances"] = [balance(row[1]) for row in GREEN[:3]]
    result = build_response(**inputs)
    assert result["reported_balance_cents"] == 1500000
    assert result["coverage"]["missing_balance_bin_count"] == 1


def test_explicit_zero_keeps_active_compliance_status():
    inputs = response_inputs()
    inputs["balances"] = [balance(row[1], amount=0) for row in GREEN]
    result = build_response(**inputs)
    assert result["reported_balance_cents"] == 0
    assert result["coverage"]["active_records_count"] == 4
    assert result["coverage"]["missing_balance_bin_count"] == 0


def test_multiple_violations_contacts_and_old_balances_never_multiply_amount():
    inputs = response_inputs()
    inputs["buildings"] *= 3
    second = dict(inputs["records"][0], id="second", source_record_key="second")
    inputs["records"].append(second)
    inputs["balances"] = [
        balance(GREEN[0][1]),
        balance(GREEN[0][1], 300000, NOW - timedelta(days=1), "old"),
    ]
    result = build_response(**inputs)
    assert result["coverage"]["physical_building_count"] == 4
    assert result["coverage"]["records_count"] == 5
    assert result["reported_balance_cents"] == 500000
    assert all(
        "amount_cents" not in row
        for building in result["buildings"]
        for row in building["records"]
    )


def test_balance_equal_date_conflict_excluded():
    inputs = response_inputs()
    inputs["balances"] = [
        balance(GREEN[0][1]),
        balance(GREEN[0][1], 300000, key="other"),
    ]
    result = build_response(**inputs)
    assert result["reported_balance_cents"] is None
    assert any("conflicting balance" in value for value in result["warnings"])


def test_scope_total_and_unknown_categories_excluded():
    inputs = response_inputs()
    inputs["balances"] = [
        dict(balance(GREEN[0][1]), scope="building_total"),
        dict(balance(GREEN[0][1]), category="OTHER"),
    ]
    assert build_response(**inputs)["reported_balance_cents"] is None


def test_fresh_fetch_with_old_source_is_stale():
    inputs = response_inputs()
    inputs["checks"][0]["source_updated_at"] = NOW - timedelta(days=4)
    result = build_response(**inputs)
    assert result["stale"]
    assert result["source_updated_at"] == NOW - timedelta(days=4)


def test_unchecked_and_successful_empty_differ():
    inputs = response_inputs()
    inputs["records"] = []
    result = build_response(**inputs)
    assert result["coverage"]["status"] == "complete"
    inputs["checks"] = []
    result = build_response(**inputs)
    assert result["coverage"]["status"] == "not_checked"
    assert result["stale"]
    inputs["checks"] = [
        {"bin": GREEN[0][1], "source_updated_at": NOW, "observed_at": NOW}
    ]
    assert build_response(**inputs)["coverage"]["status"] == "partial"


def test_missing_record_retained_without_inferred_closure():
    inputs = response_inputs()
    inputs["checks"][0]["ingestion_run_id"] = "next-run"
    result = build_response(**inputs)
    assert result["buildings"][0]["records"][0]["status"] == "Active"
    assert result["buildings"][0]["records"][0]["stale"]
    assert not result["buildings"][0]["records"][0]["present_in_latest_check"]
    assert any("closure is unverified" in value for value in result["warnings"])


def test_official_bbl_conflict_flags_identity():
    inputs = response_inputs()
    inputs["records"][0]["bbl"] = "3025210001"
    result = build_response(**inputs)
    assert (
        result["buildings"][0]["records"][0]["identity_status"]
        == "conflicting_source_identifiers"
    )


def test_append_only_status_changes_and_idempotent_retries(db):
    first = publish_snapshot(db, snapshot(), run_id="one")
    db.commit()
    assert first["inserted"] == 4
    assert db.scalar(select(func.count()).select_from(ComplianceObservation)) == 4
    publish_snapshot(db, snapshot(), run_id="one")
    db.commit()
    assert db.scalar(select(func.count()).select_from(ComplianceObservation)) == 4
    changed_rows = raw_rows()
    changed_rows[0]["violation_status"] = "Resolved"
    second = publish_snapshot(
        db, snapshot(changed_rows, NOW + timedelta(hours=1)), run_id="two"
    )
    db.commit()
    assert second["changed"] == 1
    assert db.scalar(select(func.count()).select_from(ComplianceObservation)) == 5
    # A later return to an earlier payload is a new historical transition.
    publish_snapshot(db, snapshot(observed_at=NOW + timedelta(hours=2)), run_id="three")
    db.commit()
    assert db.scalar(select(func.count()).select_from(ComplianceObservation)) == 6
    assert db.scalar(select(func.count()).select_from(ComplianceRecord)) == 4


def test_complete_empty_snapshot_preserves_violations(db):
    publish_snapshot(db, snapshot(), run_id="one")
    db.commit()
    publish_snapshot(db, snapshot([], NOW + timedelta(hours=1)), run_id="two")
    db.commit()
    assert db.scalar(select(func.count()).select_from(ComplianceRecord)) == 4
    assert all(
        row.records_count == 0 for row in db.scalars(select(ComplianceSourceCheck))
    )


def test_incomplete_snapshot_does_not_write(db):
    value = snapshot()
    value["expected_count"] = 5
    with pytest.raises(ValueError, match="complete"):
        publish_snapshot(db, value, run_id="bad")
    assert db.scalar(select(func.count()).select_from(ComplianceRecord)) == 0


def test_old_snapshot_does_not_overwrite_newer(db):
    publish_snapshot(db, snapshot(), run_id="one")
    db.commit()
    with pytest.raises(ValueError, match="newer"):
        publish_snapshot(
            db, snapshot(observed_at=NOW - timedelta(hours=1)), run_id="old"
        )
    db.rollback()
    assert db.scalar(select(func.count()).select_from(ComplianceObservation)) == 4


@pytest.mark.parametrize(
    "bins", [None, [], ["822087"], ["3348179' OR 1=1"], ["9999999"], ["3348179"] * 101]
)
def test_pilot_bin_guard(bins):
    with pytest.raises(ValueError):
        validate_bins(bins)


def test_source_adapter_completeness_and_schema():
    metadata = {
        "rowsUpdatedAt": int(NOW.timestamp()),
        "columns": [{"fieldName": value} for value in REQUIRED_FIELDS],
    }
    client = DOBSafetyClient(session=MagicMock())
    client._get = MagicMock(
        side_effect=[
            metadata,
            [{"count": "4"}],
            raw_rows()[:2],
            raw_rows()[2:],
            metadata,
        ]
    )
    result = client.fetch_snapshot([row[1] for row in GREEN], page_size=2)
    assert result["complete"] and result["expected_count"] == 4
    assert "$order" in client._get.call_args_list[2].args[1]


@pytest.mark.parametrize(
    "failure",
    ["missing_page", "changed_snapshot", "duplicate_key", "schema_drift", "wrong_bin"],
)
def test_source_adapter_fails_closed(failure):
    metadata = {
        "rowsUpdatedAt": int(NOW.timestamp()),
        "columns": [{"fieldName": value} for value in REQUIRED_FIELDS],
    }
    end_metadata = dict(metadata)
    rows = raw_rows()
    if failure == "changed_snapshot":
        end_metadata["rowsUpdatedAt"] += 1
    elif failure == "duplicate_key":
        rows[1] = rows[0]
    elif failure == "schema_drift":
        metadata["columns"] = []
    elif failure == "wrong_bin":
        rows[0]["bin"] = "1000001"
    if failure == "missing_page":
        rows = []
    client = DOBSafetyClient(session=MagicMock())
    client._get = MagicMock(
        side_effect=[metadata, [{"count": "4"}], rows, end_metadata]
    )
    with pytest.raises(ValueError):
        client.fetch_snapshot([row[1] for row in GREEN])


def evidence_input(**overrides):
    return {
        "bin": GREEN[0][1],
        "amount_cents": 500000,
        "source_timestamp_raw": "8/28/2026 10:09",
        "observed_at": "2026-08-28T15:00:00+00:00",
        "evidence_note": "Exact address and BIN selected; LL152 category showed $5,000.",
        **overrides,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount_cents": -1},
        {"amount_cents": 1.2},
        {"amount_cents": True},
        {"scope": "building_total"},
        {"source_url": "https://evil.example/source"},
        {
            "source_url": "https://www.nyc.gov.evil.example/assets/buildings/html/Unpaid_Violations_Search.html"
        },
        {"observed_at": "2026-08-28T15:00:00"},
        {"reviewer": "spoofed"},
    ],
)
def test_balance_capture_requires_exact_scope_and_attribution(overrides):
    with pytest.raises(ValidationError):
        BalanceEvidenceInput(**evidence_input(**overrides))


@pytest.fixture
def api_client():
    app = FastAPI()
    app.include_router(router)

    async def fake_session():
        yield MagicMock()

    app.dependency_overrides[get_session] = fake_session

    class LocalASGIClient:
        def __init__(self, app):
            self.app = app

        async def _request(self, method, path, **kwargs):
            async with AsyncClient(
                transport=ASGITransport(app=self.app), base_url="http://test"
            ) as client:
                return await client.request(method, path, **kwargs)

        def get(self, path):
            return asyncio.run(self._request("GET", path))

        def post(self, path, **kwargs):
            return asyncio.run(self._request("POST", path, **kwargs))

    return LocalASGIClient(app)


def test_reads_require_auth(api_client):
    assert api_client.get(f"/api/v1/compliance/parcels/{BBL}").status_code == 401


def test_disabled_envelope_never_returns_mock_data(api_client, monkeypatch):
    monkeypatch.delenv("COMPLIANCE_INTELLIGENCE_ENABLED", raising=False)
    api_client.app.dependency_overrides[get_current_user] = lambda: AuthUser(
        user_id="one", email="one@example.com"
    )
    result = api_client.get(f"/api/v1/compliance/parcels/{BBL}")
    assert result.status_code == 200
    assert result.json()["coverage"]["status"] == "disabled"
    assert result.json()["buildings"] == []


def test_invalid_namespace_rejected(api_client):
    api_client.app.dependency_overrides[get_current_user] = lambda: AuthUser(
        user_id="one", email="one@example.com"
    )
    assert api_client.get("/api/v1/compliance/buildings/822087").status_code == 422
    assert api_client.get(f"/api/v1/compliance/buildings/{BBL}").status_code == 422


def test_balance_preview_is_admin_only_and_has_no_writes(api_client):
    api_client.app.dependency_overrides[get_current_user] = lambda: AuthUser(
        user_id="one", email="one@example.com"
    )
    assert (
        api_client.post(
            "/api/v1/compliance/balance-evidence/preview", json=evidence_input()
        ).status_code
        == 403
    )
    api_client.app.dependency_overrides[get_current_user] = lambda: AuthUser(
        user_id="admin-one", email="admin@example.com", role="admin"
    )
    result = api_client.post(
        "/api/v1/compliance/balance-evidence/preview", json=evidence_input()
    )
    assert result.status_code == 200
    assert result.json()["writes"] == 0
    assert result.json()["evidence"]["reviewer"] == "admin-one"
    assert result.json()["evidence"]["interest_status"] == "unverified"


def test_balance_capture_requires_confirmation(api_client):
    api_client.app.dependency_overrides[get_current_user] = lambda: AuthUser(
        user_id="admin-one", email="admin@example.com", role="admin"
    )
    assert (
        api_client.post(
            "/api/v1/compliance/balance-evidence", json=evidence_input()
        ).status_code
        == 400
    )


def test_preview_task_preserves_preview_job_and_no_source_writes(monkeypatch):
    from src.tasks import compliance as task

    fake_db = MagicMock()
    monkeypatch.setattr(task, "_get_pg_session", lambda: fake_db)
    ensure = MagicMock(return_value=123)
    publish = MagicMock()
    quality = MagicMock()
    monkeypatch.setattr(task, "_ensure_or_create_job", ensure)
    monkeypatch.setattr(task, "_finish_job", MagicMock())
    monkeypatch.setattr(task, "publish_snapshot", publish)
    monkeypatch.setattr(task, "_log_quality", quality)
    monkeypatch.setattr(
        task.DOBSafetyClient, "fetch_snapshot", lambda self, bins: snapshot()
    )
    result = task.ingest_dob_safety.run(bins=[row[1] for row in GREEN], dry_run=True)
    assert result["dry_run"] and result["fetched"] == 4
    assert ensure.call_args.args[2:] == ("dob_safety_preview", "dob_safety_preview")
    publish.assert_not_called()
    quality.assert_not_called()


def test_execute_task_requires_explicit_confirmation():
    from src.tasks.compliance import ingest_dob_safety

    with pytest.raises(ValueError, match="confirm_execute"):
        ingest_dob_safety.run(bins=[GREEN[0][1]], dry_run=False)
