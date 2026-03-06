from src.transform.normalize import normalize_name, normalize_name_for_grouping
from src.routers.leads import _row_to_response
from pathlib import Path
from scripts.generate_leads_from_buildings import _is_probably_junk_name


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


def test_leads_search_query_does_not_partition_by_normalized_name():
    """
    Regression guard: search results should not be deduped by normalized_name.
    Distinct PM companies can share normalized grouping labels and must still appear.
    """
    leads_router = Path(__file__).resolve().parents[1] / "src" / "routers" / "leads.py"
    source = leads_router.read_text(encoding="utf-8")
    assert "PARTITION BY COALESCE(NULLIF(normalized_name, ''), lead_id)" not in source


def test_admin_recompute_portfolio_uses_building_management():
    """
    Regression guard: lead portfolio recompute must source live linkage data
    from building_management instead of stale lead snapshots.
    """
    admin_router = Path(__file__).resolve().parents[1] / "src" / "routers" / "admin.py"
    source = admin_router.read_text(encoding="utf-8")
    assert "/admin/recompute-lead-portfolio" in source
    assert "FROM building_management bm" in source
