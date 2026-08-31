from datetime import date, datetime, timedelta, timezone
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
    assert len({case["dos_id"] for case in BOARD_CHAIR_GOLDEN_CASES}) == 10
    assert sum(case["expected_role_is_explicit"] for case in BOARD_CHAIR_GOLDEN_CASES) == 9


def test_historical_benchmark_match_does_not_claim_currentness():
    case = dict(next(case for case in BOARD_CHAIR_GOLDEN_CASES if case["expected_name"] == "Russell A. Raman"))
    result = evaluate_board_chair_case(
        case,
        '{"ceo_name":"Russell A. Raman"}',
        datetime(2026, 8, 28, tzinfo=timezone.utc),
        today=date(2026, 8, 28),
    )

    assert result["identity_match"] is True
    assert result["status"] == "stale_match"
    assert result["benchmark_status"] == "historical_role_match"
    assert result["registry_freshness"] == "fresh"
    assert result["evidence_currentness"] == "historical"
    assert result["current_board_role_supported"] is False


def _benchmark_case(name):
    return dict(next(case for case in BOARD_CHAIR_GOLDEN_CASES if case["expected_name"] == name))


def _recent_explicit_case():
    return {
        **_benchmark_case("Alex Moir"),
        "expected_name": "Synthetic Board President",
        "source_name": "Synthetic dated board roster fixture",
        "source_url": "https://example.test/dated-board-roster",
        "source_date": "2026-08-15",
        "source_date_precision": "day",
    }


def test_nine_prospect_registry_name_and_snapshot_never_prove_an_exact_current_title():
    case = _benchmark_case("Louise Hainline")
    result = evaluate_board_chair_case(
        case,
        {
            "ceo_name": "LOUISE HAINLINE", "dos_id": "454503",
            "entity_match_status": "exact", "snapshot_as_of": "2026-08-31",
            "chair_status": "named_chair", "role_confidence": "high",
        },
        datetime(2026, 8, 31, tzinfo=timezone.utc),
        today=date(2026, 8, 31),
    )

    assert result["expected_name"] == "Louise Hainline"
    assert "Board Head candidate" in result["expected_title"]
    assert result["source_field"] == "chairman_name"
    assert result["source_field_display_name"] == "CEO Name"
    assert result["source_date"] is None
    assert result["source_date_precision"] == "unknown"
    assert result["evidence_age_days"] is None
    assert result["source_observed_on"] == "2026-08-31"
    assert result["identity_match"] is True
    assert result["entity_identity_match"] is True
    assert result["registry_freshness"] == "fresh"
    assert result["benchmark_status"] == "registry_candidate_only"
    assert result["status"] == "missing_current_evidence"
    assert result["evidence_currentness"] == "unverified"
    assert result["exact_board_role_supported"] is False
    assert result["current_board_role_supported"] is False
    assert result["current_title_status"] == "unverified"


def test_a_registry_publication_date_does_not_become_a_role_date():
    case = {**_benchmark_case("Louise Hainline"), "source_date": "2026-08-31"}
    result = evaluate_board_chair_case(
        case, {"ceo_name": "Louise Hainline"},
        datetime(2026, 8, 31, tzinfo=timezone.utc), today=date(2026, 8, 31),
    )

    assert result["evidence_date_status"] == "recent"
    assert result["evidence_currentness"] == "unverified"
    assert result["current_board_role_supported"] is False
    assert result["status"] != "current_match"


def test_historical_doherty_match_keeps_role_end_lead_and_access_limits():
    result = evaluate_board_chair_case(
        _benchmark_case("Stephen Doherty"),
        {"ceo_name": "STEPHEN DOHERTY", "dos_id": "1088921"},
        datetime(2026, 8, 31, tzinfo=timezone.utc), today=date(2026, 8, 31),
    )

    assert result["source_date"] == "2021-02-26"
    assert result["source_date_precision"] == "day"
    assert result["identity_match"] is True
    assert result["registry_freshness"] == "fresh"
    assert result["exact_board_role_supported"] is True
    assert result["evidence_currentness"] == "historical"
    assert result["current_board_role_supported"] is False
    assert result["status"] == "stale_match"
    assert result["benchmark_status"] == "role_conflict_requires_review"
    assert result["has_unresolved_role_conflict"] is True
    assert result["role_conflicts"][0]["role_end_month"] == "2021-02"
    assert result["role_conflicts"][0]["confidence"] == "medium"
    assert result["role_conflicts"][0]["requires_direct_capture"] is True


def test_audited_source_dates_and_entity_ids_preserve_actual_precision():
    assert _benchmark_case("Alex Moir")["source_date"] == "2025-04"
    assert _benchmark_case("David Hales")["source_date"] == "2024-06"
    assert _benchmark_case("David Hales")["dos_id"] == "974822"
    assert _benchmark_case("Debra McEneaney")["source_date"] == "2021-05-17"
    assert _benchmark_case("Robert P.J. Booher")["source_date"] == "2014-07/2014-08"
    assert _benchmark_case("Gerry Maughan")["source_date"] == "2019-06-17"
    assert _benchmark_case("James Ramadei")["dos_id"] == "672936"
    assert _benchmark_case("Josette Cerasuola")["source_date"] == "2013-12-12"


@pytest.mark.parametrize(("name", "earliest", "latest"), [
    ("Alex Moir", "2025-04-01", "2025-04-30"),
    ("Robert P.J. Booher", "2014-07-01", "2014-08-31"),
])
def test_month_and_issue_sources_return_explicit_age_bounds(name, earliest, latest):
    case = _benchmark_case(name)
    result = evaluate_board_chair_case(case, {"ceo_name": name}, None, today=date(2026, 8, 31))

    assert result["source_date"] == case["source_date"]
    assert result["evidence_age_days"] is None
    assert result["source_date_earliest"] == earliest
    assert result["source_date_latest"] == latest
    assert result["evidence_age_days_min"] < result["evidence_age_days_max"]
    assert result["evidence_currentness"] == "historical"


def test_recent_explicit_source_and_fresh_registry_name_can_pass_current_match():
    case = _recent_explicit_case()
    result = evaluate_board_chair_case(
        case, {"ceo_name": case["expected_name"], "dos_id": case["dos_id"]},
        datetime(2026, 8, 31, tzinfo=timezone.utc), today=date(2026, 8, 31),
    )

    assert result["evidence_age_days"] == 16
    assert result["exact_board_role_supported"] is True
    assert result["current_board_role_supported"] is True
    assert result["current_title_status"] == "supported_by_recent_source"
    assert result["benchmark_status"] == "recent_role_and_registry_match"
    assert result["status"] == "current_match"


def test_recent_role_and_fresh_name_match_require_the_expected_dos_entity_id():
    case = _recent_explicit_case()
    result = evaluate_board_chair_case(
        case, {"ceo_name": case["expected_name"]},
        datetime(2026, 8, 31, tzinfo=timezone.utc), today=date(2026, 8, 31),
    )

    assert result["current_board_role_supported"] is True
    assert result["identity_match"] is True
    assert result["registry_freshness"] == "fresh"
    assert result["entity_identity_match"] is None
    assert result["benchmark_status"] == "registry_identity_unverified"
    assert result["status"] == "missing_current_evidence"


@pytest.mark.parametrize(("source_date", "date_status"), [
    ("2025-08", "uncertain_range"),
    ("2026-08", "uncertain_range"),
    ("2026-08-16", "future"),
    ("2026-13", "unknown"),
    (None, "unknown"),
])
def test_undated_future_and_boundary_straddling_sources_cannot_prove_current_roles(source_date, date_status):
    case = {**_recent_explicit_case(), "source_date": source_date}
    result = evaluate_board_chair_case(
        case, {"ceo_name": case["expected_name"]},
        datetime(2026, 8, 15, tzinfo=timezone.utc), today=date(2026, 8, 15),
    )

    assert result["evidence_date_status"] == date_status
    assert result["evidence_currentness"] == "unverified"
    assert result["current_board_role_supported"] is False
    assert result["status"] != "current_match"


@pytest.mark.parametrize(("age", "freshness"), [(31, "stale"), (-1, "future_timestamp")])
def test_stale_or_future_registry_cache_does_not_pass_current_match(age, freshness):
    case = _recent_explicit_case()
    result = evaluate_board_chair_case(
        case, {"ceo_name": case["expected_name"], "dos_id": case["dos_id"]},
        datetime(2026, 8, 31, tzinfo=timezone.utc) - timedelta(days=age), today=date(2026, 8, 31),
    )

    assert result["registry_freshness"] == freshness
    assert result["current_board_role_supported"] is True
    assert result["benchmark_status"] == "recent_role_registry_refresh_needed"
    assert result["status"] == "stale_match"


def test_registry_name_difference_does_not_erase_an_independent_recent_role_source():
    case = _recent_explicit_case()
    result = evaluate_board_chair_case(
        case, {"ceo_name": "Different Registry CEO"},
        datetime(2026, 8, 31, tzinfo=timezone.utc), today=date(2026, 8, 31),
    )

    assert result["current_board_role_supported"] is True
    assert result["identity_match"] is False
    assert result["registry_name_match_status"] == "different"
    assert result["benchmark_status"] == "different_registry_name"
    assert result["status"] == "different_current_name"


@pytest.mark.parametrize("identity", [{"dos_id": "wrong-entity"}, {"entity_match_status": "ambiguous"}])
def test_a_person_name_match_does_not_override_conflicting_entity_identity(identity):
    case = _recent_explicit_case()
    result = evaluate_board_chair_case(
        case, {"ceo_name": case["expected_name"], **identity},
        datetime(2026, 8, 31, tzinfo=timezone.utc), today=date(2026, 8, 31),
    )

    assert result["identity_match"] is True  # Legacy field means name comparison only.
    assert result["identity_match_basis"] == "normalized_person_name_only"
    assert result["entity_identity_match"] is False
    assert result["benchmark_status"] == "entity_identity_conflict"
    assert result["status"] == "missing_current_evidence"


def test_reviewable_jim_james_alias_is_not_promoted_to_an_exact_name_match():
    result = evaluate_board_chair_case(
        _benchmark_case("James Ramadei"), {"ceo_name": "JIM RAMADEI", "dos_id": "672936"},
        datetime(2026, 8, 31, tzinfo=timezone.utc), today=date(2026, 8, 31),
    )

    assert result["identity_match"] is False
    assert result["entity_identity_match"] is True
    assert result["registry_name_match_status"] == "possible_alias"
    assert result["benchmark_status"] == "possible_person_alias"
    assert result["current_board_role_supported"] is False


@pytest.mark.parametrize("payload", [None, "bad-json", "[]", {"head_officer": "Louise Hainline"}, {"ceo_name": {"name": "Louise Hainline"}}])
def test_missing_or_invalid_registry_names_never_fall_back_to_hpd_head_officer(payload):
    result = evaluate_board_chair_case(_benchmark_case("Louise Hainline"), payload, None, today=date(2026, 8, 31))

    assert result["observed_name"] is None
    assert result["identity_match"] is False
    assert result["registry_freshness"] == "unknown"
    assert result["benchmark_status"] == "missing_registry_candidate"
    assert result["current_board_role_supported"] is False


def test_recent_director_evidence_does_not_replace_historical_president_evidence():
    result = evaluate_board_chair_case(
        _benchmark_case("Gerry Maughan"), {"ceo_name": "GERRY MAUGHAN", "dos_id": "614822"},
        datetime(2026, 8, 31, tzinfo=timezone.utc), today=date(2026, 8, 31),
    )

    assert result["expected_name"] == "Gerry Maughan"
    assert result["evidence_currentness"] == "historical"
    assert result["current_board_role_supported"] is False
    assert result["additional_role_evidence"][0]["person"] == "Michael J Grand"
    assert result["additional_role_evidence"][0]["chair_or_president_proof"] is False


def test_all_ten_audited_cases_remain_unconfirmed_for_current_board_role_even_with_fresh_matching_caches():
    results = [evaluate_board_chair_case(
        dict(case), {"ceo_name": case["expected_name"], "dos_id": case["dos_id"]},
        datetime(2026, 8, 31, tzinfo=timezone.utc), today=date(2026, 8, 31),
    ) for case in BOARD_CHAIR_GOLDEN_CASES]

    assert sum(row["identity_match"] for row in results) == 10
    assert sum(row["exact_board_role_supported"] for row in results) == 9
    assert sum(row["current_board_role_supported"] for row in results) == 0
    assert all(row["status"] != "current_match" for row in results)


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
async def test_board_chair_benchmark_surfaces_dos_name_match_without_current_role_proof():
    session = _AsyncSession([_Result(rows=[SimpleNamespace(
        bbl="3010680037",
        result='{"ceo_name":"Louise Hainline"}',
        cached_at=datetime.now(timezone.utc),
    )])])

    result = await board_chair_benchmark(session=session)

    assert result["total_cases"] == 10
    assert result["identity_matches"] == 1
    prospect_park = next(case for case in result["cases"] if case["bbl"] == "3010680037")
    assert prospect_park["identity_match"] is True
    assert prospect_park["status"] == "missing_current_evidence"
    assert prospect_park["benchmark_status"] == "registry_candidate_only"
    assert prospect_park["current_board_role_supported"] is False
    assert result["status_counts"].get("current_match", 0) == 0
