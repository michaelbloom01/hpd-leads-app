from src.transform.normalize import normalize_name, normalize_name_for_grouping
from src.routers.leads import _row_to_response


def test_normalize_name_expands_common_abbreviations():
    assert normalize_name("Harlem Prop Mgmt, L.L.C.") == "HARLEM PROPERTY MANAGEMENT LLC"


def test_grouping_normalization_collapses_legal_suffix_variants():
    a = normalize_name_for_grouping("Harlem Property Management LLC")
    b = normalize_name_for_grouping("HARLEM PROP MGMT INC")
    assert a == b == "HARLEM"


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
