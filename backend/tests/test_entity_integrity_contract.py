from pathlib import Path

from src.routers.buildings import _normalize_bbl_digits
from src.services.contact_roster import _detect_board_role


def _read(path: str) -> str:
    return (Path(__file__).resolve().parents[1] / path).read_text(encoding="utf-8")


def test_normalize_bbl_digits_handles_decimal_and_short_values():
    assert _normalize_bbl_digits("3010850001.0") == "3010850001"
    assert _normalize_bbl_digits("10850001") == "0010850001"
    assert _normalize_bbl_digits("3-01085-0001") == "3010850001"


def test_detect_board_role_flags_head_officer_and_chair_titles():
    assert _detect_board_role("HeadOfficer", None) == "Board Head"
    assert _detect_board_role("DOS Officer", "Board President") == "Board Officer"
    assert _detect_board_role("Agent", None) is None


def test_leads_search_includes_primary_contact_and_normalized_name():
    source = _read("src/routers/leads.py")
    assert "OR primary_contact ILIKE :search" in source
    assert "OR normalized_name ILIKE :search" in source
    assert "OR phone ILIKE :search" in source
    assert "JOIN buildings b ON b.bbl = bm.bbl" in source


def test_buildings_search_includes_pm_company_lookup():
    source = _read("src/routers/admin.py")
    assert "lead_match.lead_name" in source
    assert "bc_search.contact_type IN ('Agent', 'ManagementCompany', 'CorporateOwner')" in source


def test_quality_data_health_exposes_entity_coverage_metrics():
    source = _read("src/routers/quality.py")
    assert '"entity_coverage_ratio": entity_coverage_ratio' in source
    assert '"distinct_entities_in_contacts": distinct_entities_in_contacts' in source
    assert '"last_lead_generation": last_lead_generation' in source
    assert '"integrity": {' in source
