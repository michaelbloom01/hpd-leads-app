from pathlib import Path

from src.routers.leads import _row_to_response
from src.services.lead_generation import (
    _collapse_duplicate_company_leads,
    _is_probably_junk_name,
    summarize_portfolio_building_types,
)
from src.transform.normalize import normalize_name, normalize_name_for_grouping


def test_normalize_name_expands_common_abbreviations():
    assert normalize_name("Harlem Prop Mgmt, L.L.C.") == "HARLEM PROPERTY MANAGEMENT LLC"


def test_grouping_normalization_collapses_legal_suffix_variants():
    a = normalize_name_for_grouping("Harlem Property Management LLC")
    b = normalize_name_for_grouping("HARLEM PROP MGMT INC")
    assert a == b == "HARLEM"


def test_generate_leads_rejects_placeholder_seed_names():
    assert _is_probably_junk_name("N/A") is True
    assert _is_probably_junk_name("Board Member") is True
    assert _is_probably_junk_name("Harlem Property Management LLC") is False


def test_generate_leads_collapses_identical_company_names_conservatively():
    leads = {
        "HARLEM PROP MGMT": {
            "lead_id": "lead1",
            "normalized_name": "HARLEM PROP MGMT",
            "company_name": "Harlem Property Management LLC",
            "agent_name": "Harlem Property Management LLC",
            "owner_name": None,
            "entity_type": "company",
            "address": "1 Main St",
            "boroughs": {"MANHATTAN": 1},
            "bbl_set": {"1000000001"},
            "address_set": {"1 Main St"},
            "unit_counts_by_bbl": {"1000000001": 10},
            "unit_count_total": 10,
            "building_classes": ["R1"],
        },
        "HARLEM PROPERTY MANAGEMENT": {
            "lead_id": "lead2",
            "normalized_name": "HARLEM PROPERTY MANAGEMENT",
            "company_name": "HARLEM PROPERTY MANAGEMENT INC",
            "agent_name": "HARLEM PROPERTY MANAGEMENT INC",
            "owner_name": None,
            "entity_type": "company",
            "address": "2 Main St",
            "boroughs": {"BROOKLYN": 1},
            "bbl_set": {"2000000002"},
            "address_set": {"2 Main St"},
            "unit_counts_by_bbl": {"2000000002": 20},
            "unit_count_total": 20,
            "building_classes": ["R2"],
        },
    }

    collapsed = _collapse_duplicate_company_leads(leads)

    assert len(collapsed) == 1
    only = next(iter(collapsed.values()))
    assert only["bbl_set"] == {"1000000001", "2000000002"}
    assert only["unit_count_total"] == 30


def test_row_to_response_parses_json_like_fields():
    row = {
        "lead_id": "abc123",
        "owner_name": "Owner",
        "owner_type": "company",
        "portfolio_size": 1,
        "total_units": 10,
        "buildings": '["100 MAIN ST"]',
        "boros": '["MANHATTAN"]',
        "building_types": '{"condo": 1, "total": 1}',
        "building_classes": '{"R4": 1}',
        "score": 55.0,
        "score_breakdown": '{"portfolio": 10}',
        "enrichment_status": "none",
        "outreach_status": "new",
        "contacts": '[{"name":"Jane"}]',
        "estimated_monthly_revenue": 1000.0,
        "estimated_annual_revenue": 12000.0,
        "revenue_breakdown": '[{"label":"x"}]',
    }

    payload = _row_to_response(row)
    assert payload["buildings"] == ["100 MAIN ST"]
    assert payload["boros"] == ["MANHATTAN"]
    assert payload["building_types"] is not None
    assert payload["building_classes"] == {"R4": 1}
    assert payload["revenue_breakdown"] == [{"label": "x"}]


def test_summarize_portfolio_building_types_matches_ui_categories():
    building_types, type_units = summarize_portfolio_building_types(
        [
            {"building_type": "Condo", "unit_count": 10},
            {"building_type": "Co-op", "unit_count": 20},
            {"building_type": "Rental Elevator", "unit_count": 30},
            {"building_type": "Rental Walk-Up", "unit_count": 8},
            {"building_type": "Small Residential", "unit_count": 3},
            {"building_type": "", "unit_count": 1},
        ]
    )

    assert building_types == {
        "condo": 1,
        "coop": 1,
        "rental_elevator": 1,
        "rental_walkup": 1,
        "small_residential": 1,
        "other": 1,
        "unknown": 0,
        "total": 6,
        "total_rental": 3,
    }
    assert type_units["condo"] == 10
    assert type_units["coop"] == 20
    assert type_units["rental_elevator"] == 30
    assert type_units["rental_walkup"] == 8
    assert type_units["small_residential"] == 3
    assert type_units["other"] == 1


def test_leads_search_query_does_not_partition_by_normalized_name():
    """
    Regression guard: search results should not be deduped by normalized_name.
    Distinct PM companies can share normalized grouping labels and must still appear.
    """
    leads_router = Path(__file__).resolve().parents[1] / "src" / "routers" / "leads.py"
    source = leads_router.read_text(encoding="utf-8")
    assert "PARTITION BY COALESCE(NULLIF(normalized_name, ''), lead_id)" not in source


def test_generate_leads_persists_building_types_snapshot():
    lead_generation = Path(__file__).resolve().parents[1] / "src" / "services" / "lead_generation.py"
    source = lead_generation.read_text(encoding="utf-8")
    assert "building_types" in source
    assert "CAST(:building_types AS JSONB)" in source
    assert "building_types = EXCLUDED.building_types" in source


def test_admin_recompute_portfolio_uses_building_management():
    """
    Regression guard: lead portfolio recompute must source live linkage data
    from building_management instead of stale lead snapshots.
    """
    admin_router = Path(__file__).resolve().parents[1] / "src" / "routers" / "admin.py"
    source = admin_router.read_text(encoding="utf-8")
    assert "/admin/recompute-lead-portfolio" in source
    assert "FROM building_management bm" in source
