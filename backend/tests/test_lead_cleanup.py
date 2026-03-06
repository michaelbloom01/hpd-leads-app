from src.services.lead_cleanup import _classify_orphan_row


def test_classifies_blank_rootless_zero_state_lead_as_retire_only():
    row = {
        "keeper_count": 0,
        "company_name": None,
        "agent_name": None,
        "owner_name": None,
        "primary_contact": None,
        "pipeline_stage": "research",
        "outreach_status": "new",
        "next_follow_up": None,
        "priority_rank": 0,
        "notes": None,
        "phone": None,
        "email": None,
        "website": None,
        "business_summary": None,
        "owner_principal": None,
        "enrichment_status": "none",
        "last_enriched": None,
        "enrichment_retries": 0,
        "outreach_event_count": 0,
        "enrichment_result_count": 0,
        "historical_link_count": 0,
    }

    assert _classify_orphan_row(row) == "safe_orphan_retire_only"


def test_does_not_retire_orphan_with_any_history():
    row = {
        "keeper_count": 0,
        "company_name": None,
        "agent_name": None,
        "owner_name": None,
        "primary_contact": None,
        "pipeline_stage": "research",
        "outreach_status": "new",
        "next_follow_up": None,
        "priority_rank": 0,
        "notes": None,
        "phone": None,
        "email": None,
        "website": None,
        "business_summary": None,
        "owner_principal": None,
        "enrichment_status": "none",
        "last_enriched": None,
        "enrichment_retries": 0,
        "outreach_event_count": 0,
        "enrichment_result_count": 0,
        "historical_link_count": 1,
    }

    assert _classify_orphan_row(row) == "ambiguous_orphan"


def test_prefers_keeper_migration_when_clear_keeper_exists():
    row = {
        "keeper_count": 1,
        "company_name": "Acme Management",
        "agent_name": None,
        "owner_name": None,
        "primary_contact": None,
        "pipeline_stage": "research",
        "outreach_status": "new",
        "next_follow_up": None,
        "priority_rank": 0,
        "notes": None,
        "phone": None,
        "email": None,
        "website": None,
        "business_summary": None,
        "owner_principal": None,
        "enrichment_status": "none",
        "last_enriched": None,
        "enrichment_retries": 0,
        "outreach_event_count": 0,
        "enrichment_result_count": 0,
        "historical_link_count": 0,
    }

    assert _classify_orphan_row(row) == "safe_orphan_with_clear_keeper"


def test_retire_only_ignores_empty_enrichment_bookkeeping():
    row = {
        "keeper_count": 0,
        "company_name": None,
        "agent_name": None,
        "owner_name": None,
        "primary_contact": None,
        "pipeline_stage": "research",
        "outreach_status": "new",
        "next_follow_up": None,
        "priority_rank": 0,
        "notes": None,
        "phone": None,
        "email": None,
        "website": None,
        "business_summary": None,
        "owner_principal": None,
        "enrichment_status": "failed",
        "last_enriched": None,
        "enrichment_retries": 2,
        "outreach_event_count": 0,
        "enrichment_result_count": 0,
        "historical_link_count": 0,
    }

    assert _classify_orphan_row(row) == "safe_orphan_retire_only"
