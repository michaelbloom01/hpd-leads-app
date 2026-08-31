from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from src.enrich.ny_dos import NYDOSClient
from src.routers.quality import board_chair_benchmark, board_chair_coverage
from src.services.board_chair_benchmark import (
    BOARD_CHAIR_GOLDEN_CASES,
    evaluate_board_chair_case,
)
from src.tasks.enrich import _build_board_chair_cache_payload
from src.tasks.ingest import (
    _prepare_building_refresh_snapshot,
    _validate_building_refresh_snapshot,
)


class _Entity:
    def __init__(self, dos_id: str, name: str, chair: str | None):
        self.dos_id = dos_id
        self.name = name
        self.entity_type = "DOMESTIC BUSINESS CORPORATION"
        self.ceo_name = chair
        self.ceo_address = "9 Prospect Park West, Brooklyn, NY"


class _MappingRow(dict):
    @property
    def _mapping(self):
        return self


class _Result:
    def __init__(self, *, row=None, rows=None, scalar_value=None):
        self._row = row
        self._rows = rows or []
        self._scalar_value = scalar_value

    def first(self):
        return self._row

    def fetchall(self):
        return self._rows

    def scalar(self):
        return self._scalar_value


class _AsyncSession:
    def __init__(self, results):
        self.results = list(results)
        self.statements = []

    async def execute(self, *args, **_kwargs):
        if args:
            self.statements.append(str(args[0]))
        return self.results.pop(0)


def test_building_snapshot_keeps_latest_registration_and_current_contacts():
    registrations = [
        {"boroid": "3", "block": "1068", "lot": "37", "bin": "3024538", "buildingid": "278101", "registrationid": "200", "lastregistrationdate": "2026-07-09", "housenumber": "9", "streetname": "PROSPECT PARK WEST"},
        {"boroid": "3", "block": "1068", "lot": "37", "bin": "3024538", "buildingid": "278101", "registrationid": "100", "lastregistrationdate": "2025-07-09", "housenumber": "9", "streetname": "PROSPECT PARK WEST"},
    ]
    contacts = [
        {"registrationid": "200", "registrationcontactid": "1", "type": "CorporateOwner", "corporationname": "Park West Tenants Corp."},
        {"registrationid": "100", "registrationcontactid": "2", "type": "HeadOfficer", "firstname": "Chris", "lastname": "Swensen"},
    ]
    snapshot = _prepare_building_refresh_snapshot(
        registrations,
        contacts,
        [{"bbl": "3010680037.000000", "bldgclass": "D4", "unitsres": "18"}],
    )

    assert len(snapshot["buildings"]) == 1
    assert snapshot["buildings"][0]["bbl"] == "3010680037"
    assert snapshot["buildings"][0]["bldg_class"] == "D4"
    assert snapshot["current_registration_by_bbl"]["3010680037"] == "200"
    assert [row["contact_id"] for row in snapshot["contacts_by_bbl"]["3010680037"]] == ["1"]


def test_building_snapshot_volume_gates_can_be_unit_tested_without_production_scale():
    stats = {
        "registrations_fetched": 1,
        "contacts_fetched": 1,
        "pluto_rows_fetched": 1,
        "current_buildings": 1,
        "rejected_registrations": 0,
    }
    assert _validate_building_refresh_snapshot(stats, enforce_volume_gates=False) == []
    assert "registrations_below_floor:1" in _validate_building_refresh_snapshot(stats)


def test_exact_query_variants_cover_common_dos_punctuation():
    variants = NYDOSClient._exact_query_variants("GOTHAM HOUSE OWNERS CORP")
    assert "GOTHAM HOUSE OWNER'S CORP." in variants
    assert NYDOSClient._canonical_exact_name("GOTHAM HOUSE OWNER'S CORP.") == NYDOSClient._canonical_exact_name("GOTHAM HOUSE OWNERS CORP")


def test_board_chair_payload_promotes_only_one_exact_entity():
    exact = _build_board_chair_cache_payload(
        "Park West Tenants Corp.",
        [_Entity("454503", "PARK WEST TENANTS CORP.", "Louise Hainline")],
        snapshot_as_of="2026-08-28",
    )
    ambiguous = _build_board_chair_cache_payload(
        "Same Name Corp.",
        [_Entity("1", "SAME NAME CORP.", "A"), _Entity("2", "SAME NAME CORP.", "B")],
        snapshot_as_of="2026-08-28",
    )

    assert exact["entity_match_status"] == "exact"
    assert exact["chair_status"] == "named_chair"
    assert exact["ceo_name"] == "Louise Hainline"
    assert ambiguous["entity_match_status"] == "ambiguous"
    assert ambiguous["ceo_name"] is None


def test_public_board_chair_benchmark_has_ten_unique_resolved_buildings():
    assert len(BOARD_CHAIR_GOLDEN_CASES) == 10
    assert len({case["bbl"] for case in BOARD_CHAIR_GOLDEN_CASES}) == 10
    assert all(len(case["bbl"]) == 10 for case in BOARD_CHAIR_GOLDEN_CASES)
    assert all(case["source_url"].startswith("https://") for case in BOARD_CHAIR_GOLDEN_CASES)


def test_historical_benchmark_match_does_not_claim_currentness():
    case = dict(next(case for case in BOARD_CHAIR_GOLDEN_CASES if case["expected_name"] == "Russell A. Raman"))
    result = evaluate_board_chair_case(
        case,
        '{"ceo_name":"Russell A. Raman"}',
        datetime(2026, 8, 28, tzinfo=timezone.utc),
        today=date(2026, 8, 28),
    )

    assert result["identity_match"] is True
    assert result["status"] == "current_match"
    assert result["evidence_currentness"] == "historical"


@pytest.mark.anyio
async def test_board_chair_coverage_reports_relevant_and_all_listing_denominators():
    session = _AsyncSession([
        _Result(row=_MappingRow({
            "total_buildings": 182495,
            "eligible_buildings": 15996,
            "current_exact_chair": 3042,
            "stale_exact_chair": 0,
            "ambiguous_or_possible": 0,
            "exact_entity_without_chair": 0,
            "no_named_chair_match": 12510,
            "not_loaded": 444,
        })),
        _Result(scalar_value=14000),
    ])

    result = await board_chair_coverage(session=session)

    assert result["current_exact_coverage"] == 0.1902
    assert result["current_exact_all_buildings_coverage"] == 0.0167
    assert result["hpd_head_officer_included_in_chair_coverage"] is False
    assert "expires_at" not in session.statements[0]


@pytest.mark.anyio
async def test_board_chair_benchmark_surfaces_current_dos_match():
    session = _AsyncSession([_Result(rows=[SimpleNamespace(
        bbl="3010680037",
        result='{"ceo_name":"Louise Hainline"}',
        cached_at=datetime.now(timezone.utc),
    )])])

    result = await board_chair_benchmark(session=session)

    assert result["total_cases"] == 10
    assert result["identity_matches"] == 1
    prospect_park = next(case for case in result["cases"] if case["bbl"] == "3010680037")
    assert prospect_park["status"] == "current_match"
