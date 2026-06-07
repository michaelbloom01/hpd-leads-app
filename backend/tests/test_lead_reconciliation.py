from src.services.lead_generation import make_lead_id
import pytest

from src.services.lead_reconciliation import (
    build_stale_lead_reconciliation_report,
    execute_stale_lead_reconciliation,
)


def test_stale_reconciliation_groups_harlem_suffix_variants():
    report = build_stale_lead_reconciliation_report(
        [
            {
                "lead_id": "d8440125dca5",
                "company_name": "Harlem Property Management",
                "agent_name": "Harlem Property Management",
                "normalized_name": "HARLEM PROPERTY MANAGEMENT",
                "portfolio_size": 68,
                "total_units": 1465,
                "pipeline_stage": "new",
                "outreach_status": "none",
            },
            {
                "lead_id": "5ff0e8e0050b",
                "company_name": "HARLEM PROPERTY MANAGEMENT INC.",
                "agent_name": "HARLEM PROPERTY MANAGEMENT INC.",
                "normalized_name": "HARLEM PROPERTY MANAGEMENT INC.",
                "portfolio_size": 13,
                "total_units": 294,
                "pipeline_stage": "new",
                "outreach_status": "none",
            },
        ],
        current_link_count=0,
        search="harlem property",
    )

    assert report["mode"] == "dry_run"
    assert report["duplicate_group_count"] == 1
    assert report["low_risk_legal_suffix_variant_count"] == 1
    group = report["samples"][0]
    assert group["grouping_key"] == "HARLEM"
    assert group["canonical_lead_id"] == make_lead_id("HARLEM")
    assert group["bucket"] == "low_risk_legal_suffix_variant"
    assert group["legal_name_keys"] == ["HARLEM PROPERTY MANAGEMENT"]
    assert group["would_retire_lead_ids"] == ["d8440125dca5", "5ff0e8e0050b"]
    assert group["combined_portfolio_size"] == 81
    assert group["combined_total_units"] == 1759
    assert report["status_drift"]["pipeline_new"] == 2
    assert report["status_drift"]["outreach_none"] == 2


def test_stale_reconciliation_flags_user_state_groups_for_review():
    report = build_stale_lead_reconciliation_report(
        [
            {
                "lead_id": "lead-1",
                "company_name": "Acme Management LLC",
                "portfolio_size": 10,
                "total_units": 100,
                "pipeline_stage": "new",
                "outreach_status": "none",
            },
            {
                "lead_id": "lead-2",
                "company_name": "ACME REALTY INC.",
                "portfolio_size": 2,
                "total_units": 20,
                "pipeline_stage": "follow_up",
                "outreach_status": "contacted",
            },
        ],
        current_link_count=0,
    )

    group = report["samples"][0]
    assert group["grouping_key"] == "ACME"
    assert group["bucket"] == "review_required"
    assert "broad_grouping_key_matches_multiple_legal_name_bases" in group["review_reasons"]
    assert "candidate_has_user_state" in group["review_reasons"]
    assert group["would_preserve_user_state"] is True
    assert report["review_required_count"] == 1
    assert report["status_drift"]["non_baseline_user_state"] == 1


def test_stale_reconciliation_ignores_retired_rows():
    report = build_stale_lead_reconciliation_report(
        [
            {
                "lead_id": make_lead_id("HARLEM"),
                "company_name": "Harlem Property Management",
                "pipeline_stage": "research",
                "outreach_status": "new",
                "retired_at": None,
            },
            {
                "lead_id": "retired-1",
                "company_name": "HARLEM PROPERTY MANAGEMENT INC.",
                "pipeline_stage": "new",
                "outreach_status": "none",
                "retired_at": "2026-04-30T10:13:07-04:00",
            },
        ],
        current_link_count=82,
        search="harlem property",
    )

    assert report["total_leads"] == 1
    assert report["duplicate_group_count"] == 0


def test_stale_reconciliation_can_target_exact_grouping_key():
    rows = [
        {
            "lead_id": "lead-1",
            "company_name": "Prestige Management Inc",
            "portfolio_size": 10,
            "total_units": 100,
            "pipeline_stage": "new",
            "outreach_status": "none",
        },
        {
            "lead_id": "lead-2",
            "company_name": "Prestige Management",
            "portfolio_size": 3,
            "total_units": 30,
            "pipeline_stage": "new",
            "outreach_status": "none",
        },
        {
            "lead_id": "lead-3",
            "company_name": "Bedford Prestige Management LLC",
            "portfolio_size": 2,
            "total_units": 20,
            "pipeline_stage": "new",
            "outreach_status": "none",
        },
        {
            "lead_id": "lead-4",
            "company_name": "Bedford Prestige Management",
            "portfolio_size": 1,
            "total_units": 10,
            "pipeline_stage": "new",
            "outreach_status": "none",
        },
    ]

    broad = build_stale_lead_reconciliation_report(rows, search="prestige management")
    targeted = build_stale_lead_reconciliation_report(
        rows,
        search="prestige management",
        grouping_key="PRESTIGE",
    )

    assert broad["duplicate_group_count"] == 2
    assert targeted["duplicate_group_count"] == 1
    assert targeted["samples"][0]["grouping_key"] == "PRESTIGE"


def test_execute_reconciliation_requires_explicit_confirmation():
    with pytest.raises(ValueError, match="confirm_execute=True"):
        execute_stale_lead_reconciliation(session=None)  # type: ignore[arg-type]
