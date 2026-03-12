from src.services.targets import (
    score_adjacent_candidate,
    score_lead_candidate,
    score_target_item_snapshot,
)


def test_score_lead_candidate_prioritizes_exact_name_and_domain():
    item = {
        "company_name": "AJ Clarke",
        "website": "https://ajclarke.com",
        "phone": "(212) 555-0000",
        "geography": "MANHATTAN",
    }
    lead = {
        "normalized_name": "AJ CLARKE",
        "company_name": "AJ CLARKE MANAGEMENT",
        "agent_name": None,
        "owner_name": None,
        "website": "https://ajclarke.com/contact",
        "phone": "(212) 555-0000",
        "primary_borough": "MANHATTAN",
    }

    confidence, reasons = score_lead_candidate(item, lead)

    assert confidence >= 0.9
    assert "normalized_name_exact" in reasons
    assert "website_domain" in reasons
    assert "phone_exact" in reasons


def test_score_target_item_snapshot_rewards_focus_and_contactability():
    item = {
        "portfolio_estimate": "40 buildings",
        "units_estimate": "2,500 units",
        "condo_focus": "High condo / co-op concentration",
        "ownership": "Founder-led family business",
        "established": "1988",
        "website": "https://example.com",
        "phone": "(212) 555-0000",
    }
    lead = {
        "portfolio_size": 38,
        "total_units": 2400,
        "phone": "(212) 555-0000",
        "email": "principal@example.com",
        "website": "https://example.com",
        "building_types": {"condo": 22, "coop": 11},
        "estimated_annual_revenue": 1500000,
    }

    result = score_target_item_snapshot(item, lead)

    assert result["score"] >= 70
    assert result["breakdown"]["focus_fit"] >= 15
    assert result["breakdown"]["contactability"] >= 10
    assert "contactable" in result["summary"]


def test_score_adjacent_candidate_prefers_seed_similarity():
    seed_summary = {
        "avg_portfolio_size": 30,
        "avg_units": 1800,
        "boroughs": ["MANHATTAN", "BROOKLYN"],
        "prefers_condo_focus": True,
    }
    lead = {
        "portfolio_size": 28,
        "total_units": 1700,
        "primary_borough": "MANHATTAN",
        "building_types": {"condo": 18},
        "phone": "(212) 555-1111",
        "estimated_annual_revenue": 900000,
    }

    score, reasons = score_adjacent_candidate(seed_summary, lead)

    assert score >= 70
    assert "portfolio_similarity" in reasons
    assert "borough_overlap" in reasons
    assert "condo_focus" in reasons
