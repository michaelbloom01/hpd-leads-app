from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import SQLAlchemyError
from starlette.requests import Request

from scripts import truth_manual_evidence as manual_evidence_script
from scripts import truth_operator_document_audit as operator_document_script
from scripts import truth_source_evidence_intake as intake_script
from src.auth.auth import AuthUser
from src.routers import buildings as buildings_router
from src.routers import jobs as jobs_router
from src.routers import leads as leads_router
from src.routers import truth as truth_router
from src.schemas.requests import OutreachEventRequest
from src.services.confidence import (
    CONFIDENCE_POLICY_VERSION,
    VERIFIED_CONFIDENCE_THRESHOLD,
    ConfidenceInput,
    actionability_level,
    compute_confidence,
    review_bucket,
)
from src.services.golden_benchmark import evaluate_golden_cases
from src.services.manual_evidence import build_manual_evidence_claim_spec, preview_or_record_manual_evidence
from src.services.outreach_feedback import (
    EXPECTED_TRUTH_ALEMBIC_REVISION as OUTREACH_TRUTH_REVISION,
    classify_outreach_feedback,
    load_outreach_feedback_truth_write_status,
    stable_outreach_feedback_id,
)
from src.services.operator_document_audit import (
    TARGET_PRESETS,
    audit_operator_document_rows,
    build_relationship_targets_from_rows,
)
from src.services.source_evidence_intake import (
    build_source_evidence_intake_batch_preview,
    build_source_evidence_intake_preview,
    filter_source_evidence_batch_to_recommended_scope,
)
from src.services.truth_adjudication import (
    adjudicate_fact_group,
    build_adjudication_update_plan,
    build_verified_confidence_gap_plan,
    build_role_overlap_activation_plan,
    load_claim_adjudication_preview,
    load_ledger_source_overlap_summary,
    load_manager_external_source_acquisition_preview,
    load_manager_source_bridge_preview,
    load_operator_confirmed_management_preview,
    load_role_claim_correction_preview,
    load_role_source_overlap_pilot,
    load_scaled_role_source_overlap_preview,
    preview_or_apply_role_claim_corrections,
    simulate_role_overlap_post_materialization,
)
from src.services.truth_activation import build_activation_packet, build_runtime_preflight_summary
from src.services.truth_health import (
    EXPECTED_TRUTH_ALEMBIC_REVISION,
    REQUIRED_TRUTH_TABLES,
    build_activation_checklist,
    build_schema_readiness_report,
    build_truth_health_report,
    evaluate_truth_health_outputs,
    is_truth_schema_current,
    load_truth_schema_status,
)
from src.services.truth_materialization import (
    build_building_signal_claim_spec,
    build_confidence_snapshots_from_specs,
    build_enrichment_result_claim_specs,
    build_hpd_contact_claim_specs,
    build_hpd_management_link_claim_spec,
    build_hpd_role_link_claim_spec,
    build_materialization_manifest_entries,
    build_materialization_rollback_plan,
    build_materialized_claim,
    build_outreach_feedback_claim_specs,
    materialize_truth_claims,
    normalize_materialization_sources,
)
from src.services.truth_program import GOLDEN_CASE_SEEDS, build_lead_truth_summary, build_subject_truth_summary, preview_adversarial_validation
from src.services.truth_review import apply_review_decision, build_review_decision_preview, load_review_decision_item
from src.tasks import truth_materialization as truth_materialization_task
from src.tasks import truth_validation as truth_validation_task
from src.tasks.truth_validation import build_review_items_from_validation_preview
from scripts.truth_migration_preflight import build_preflight_result
from scripts.truth_manager_external_evidence_batch import (
    _build_manual_evidence_preview as build_manager_manual_evidence_preview,
    build_manager_external_evidence_batch,
    build_manager_source_acquisition_packet,
)
from scripts.truth_operator_document_audit import _read_rows as read_operator_document_rows
from scripts.truth_operator_confirmed_evidence_batch import (
    _build_manual_evidence_preview as build_operator_manual_evidence_preview,
    build_operator_confirmed_evidence_batch,
    build_operator_source_acquisition_packet,
)
from scripts.truth_role_overlap_activation_packet import build_role_overlap_activation_packet
from scripts.truth_source_overlap_approval_packet import _source_overlap_recording_gate, build_source_overlap_approval_packet
from scripts.truth_source_overlap_post_recording_check import (
    build_post_recording_check,
    load_verified_single_source_summary,
)
from scripts.truth_source_acquisition_worklist import (
    build_source_acquisition_csv_template,
    build_source_acquisition_hpd_fetch_packet,
    build_source_acquisition_operator_confirmation_packet,
    build_source_acquisition_worklist,
)
from scripts.truth_source_overlap_blocker_report import build_source_overlap_blocker_report
from scripts.truth_validation_rollback import build_validation_rollback_summary
from scripts.truth_verification_frontier import build_truth_verification_frontier


class FakeExecuteResult:
    def __init__(self, *, rows=None):
        self._rows = rows or []

    @staticmethod
    def _row(row):
        class FakeRow(SimpleNamespace):
            def __getitem__(self, key):
                if isinstance(key, int):
                    return list(self._mapping.values())[key]
                return self._mapping[key]

        return FakeRow(_mapping=row, **row)

    def __iter__(self):
        for row in self._rows:
            yield self._row(row)

    def first(self):
        if not self._rows:
            return None
        row = self._rows[0]
        return self._row(row)

    def scalar_one(self):
        if not self._rows:
            raise AssertionError("No scalar row")
        row = self._rows[0]
        if len(row) == 1:
            return next(iter(row.values()))
        return row


class FakeAsyncSession:
    def __init__(self, results):
        self._results = list(results)
        self.rollback_count = 0

    async def execute(self, statement, params=None):
        if not self._results:
            raise AssertionError(f"Unexpected execute call: {statement}")
        return self._results.pop(0)

    async def rollback(self):
        self.rollback_count += 1


class CapturingFakeAsyncSession(FakeAsyncSession):
    def __init__(self, results):
        super().__init__(results)
        self.executed = []
        self.committed = False

    async def execute(self, statement, params=None):
        self.executed.append({"statement": str(statement), "params": params or {}})
        return await super().execute(statement, params)

    async def commit(self):
        self.committed = True


class FailingAsyncSession:
    def __init__(self):
        self.rolled_back = False

    async def execute(self, statement, params=None):
        raise SQLAlchemyError("missing truth table")

    async def rollback(self):
        self.rolled_back = True


def test_operator_document_audit_matches_exact_rows_and_redacts_financials():
    rows = [
        {
            "Property": "342 W 56th - Coop",
            "Management Fee": 36202.74,
            "Annual Rent": 768000,
            "Source": "https://www.google.com/search?q=56th+342+W+Coop+NYC+building",
        },
        {
            "Property": "157W 123rd St HDFC",
            "Management Fee": 41652.71,
            "Source": "https://streeteasy.com/building/157-west-123rd-street-new_york",
        },
    ]

    report = audit_operator_document_rows(
        rows,
        targets=TARGET_PRESETS["hpm-next"] + TARGET_PRESETS["hpm-pilot"][-3:-2],
        document_title="Revenue by Property - Summary",
        observed_at="2026-03-20T14:24:44+00:00",
        operator_confirmed_document_provenance=True,
    )

    assert report["dry_run"] is True
    assert report["mutations_planned"] == 0
    assert report["recording_ready_count"] == 0
    assert report["matched_target_count"] == 1
    assert report["unmatched_target_count"] == 1
    assert report["source_evidence_intake_candidate_count"] == 1
    assert report["match_rows"][0]["address"] == "342 WEST 56 STREET"
    assert report["match_rows"][0]["bbl"] == "1010460054"
    assert report["match_rows"][0]["source_document_row_number"] == 2
    assert report["match_rows"][0]["can_become_manual_evidence_template"] is True
    assert report["match_rows"][0]["source_evidence_intake_candidate_ready"] is True
    candidate = report["source_evidence_intake_candidates"][0]
    assert candidate["relationship_label"] == "Harlem Property Management manages building 342 WEST 56 STREET"
    assert candidate["bbl"] == "1010460054"
    assert candidate["source_name"] == "hpm_revenue_by_property_summary"
    assert candidate["source_family"] == "first_party_operator_document"
    assert candidate["source_record_id"] == "operator-document:hpm-342-w-56:row-2"
    assert candidate["observed_at"] == "2026-03-20T14:24:44+00:00"
    assert candidate["exact_property_match"] is True
    assert candidate["role_specific_management_support"] is True
    assert report["unmatched_targets"][0]["address"] == "141 WEST 123 STREET"
    serialized = json.dumps(report)
    assert "36202.74" not in serialized
    assert "768000" not in serialized
    assert "Management Fee" not in serialized


def test_operator_document_audit_reads_local_csv_without_recording_path(tmp_path):
    source_file = tmp_path / "operator-document.csv"
    source_file.write_text(
        "Property,Management Fee,Source\n"
        "220 Third Avenue,100,https://example.test/220\n"
        "57 Bond Street,200,https://example.test/57\n",
        encoding="utf-8",
    )

    rows = read_operator_document_rows(source_file, sheet_name=None)
    report = audit_operator_document_rows(
        rows,
        targets=TARGET_PRESETS["operator-seeds"],
        document_title="operator-document",
        observed_at="2026-05-19T00:00:00+00:00",
    )

    assert report["matched_target_count"] == 2
    assert report["unmatched_target_count"] == 2
    assert report["source_evidence_intake_candidate_count"] == 2
    assert {row["address"] for row in report["match_rows"]} == {"220 3 AVENUE", "57 BOND STREET"}
    assert {candidate["bbl"] for candidate in report["source_evidence_intake_candidates"]} == {
        "1008747504",
        "1005297507",
    }
    assert {candidate["source_name"] for candidate in report["source_evidence_intake_candidates"]} == {
        "operator_review",
    }
    assert all(
        candidate["role_specific_management_support"] is None
        for candidate in report["source_evidence_intake_candidates"]
    )
    assert report["redaction_policy"].startswith("Output excludes management-fee")


def test_operator_document_audit_treats_derived_research_as_source_clues_only():
    rows = [
        {
            "Property": "342 W 56th - Coop",
            "Source": "Cited workbook and public-source appendix",
            "Narrative": "Research note says this appears in the HPM source materials.",
        },
    ]

    report = audit_operator_document_rows(
        rows,
        targets=TARGET_PRESETS["hpm-pilot"][-3:-2],
        document_title="HPM Deep Research",
        observed_at="2026-05-19T00:00:00+00:00",
        operator_confirmed_document_provenance=True,
        document_kind="derived_research",
    )

    assert report["dry_run"] is True
    assert report["document_kind"] == "derived_research"
    assert report["matched_target_count"] == 1
    assert report["source_evidence_intake_candidate_count"] == 0
    assert report["source_evidence_intake_candidates"] == []
    assert report["source_acquisition_clue_count"] == 1
    assert report["recording_ready_count"] == 0
    match = report["match_rows"][0]
    assert match["can_become_manual_evidence_template"] is False
    assert match["source_evidence_intake_candidate_ready"] is False
    assert match["requires_primary_source_review"] is True
    clue = report["source_acquisition_clues"][0]
    assert clue["address"] == "342 WEST 56 STREET"
    assert clue["bbl"] == "1010460054"
    assert clue["clue_status"] == "source_clue_only"
    assert clue["can_become_manual_evidence_template"] is False
    assert clue["requires_primary_source_review"] is True
    assert "Do not record evidence" in clue["safe_action"]


def test_operator_document_targets_from_current_relationships_are_matching_context_only():
    targets = build_relationship_targets_from_rows(
        [{"bbl": "1010460054", "address": "342 West 56 Street", "role": "agent"}],
        expected_manager="Harlem Property Management",
        manager_lead_id="0ff794d3ba2d",
        source_name="hpm_revenue_by_property_summary",
    )

    assert len(targets) == 1
    target = targets[0]
    assert target.target_id == "current-relationship-1010460054"
    assert target.address == "342 WEST 56 STREET"
    assert target.bbl == "1010460054"
    assert target.expected_manager == "Harlem Property Management"
    assert "342 W 56 ST" in {alias.upper() for alias in target.aliases}
    assert "role=agent" in (target.target_context or "")
    assert "not as manager-proof evidence" in (target.target_context or "")

    report = audit_operator_document_rows(
        [{"Property": "342 W 56th - Coop", "Source": "Revenue by Property - Summary row 2"}],
        targets=targets,
        document_title="Revenue by Property - Summary",
        observed_at="2026-05-19T00:00:00+00:00",
        operator_confirmed_document_provenance=True,
    )

    assert report["matched_target_count"] == 1
    assert report["match_rows"][0]["target_context"] == target.target_context
    candidate = report["source_evidence_intake_candidates"][0]
    assert candidate["bbl"] == "1010460054"
    assert "Target context:" in candidate["notes"]
    assert "use for exact-property matching only" in candidate["notes"]


def test_operator_document_audit_nested_intake_preview_keeps_clues_read_only():
    rows = [{"Property": "342 W 56th - Coop", "Source": "Cited workbook and public appendix"}]
    report = audit_operator_document_rows(
        rows,
        targets=TARGET_PRESETS["hpm-pilot"][-3:-2],
        document_title="HPM Deep Research",
        document_kind="derived_research",
    )

    preview = asyncio.run(
        operator_document_script._preview_source_evidence_intake_for_audit(
            report,
            SimpleNamespace(
                preview_recommended_scope_only=False,
                intake_lead_id="0ff794d3ba2d",
                intake_frontier_limit=10,
                intake_max_items=25,
                recorded_by="operator",
                run_id=None,
            ),
        ),
    )

    assert preview["run_type"] == "truth_source_evidence_intake_clue_only_preview"
    assert preview["dry_run"] is True
    assert preview["mutations_planned"] == 0
    assert preview["allowed_execute"] is False
    assert preview["candidate_count"] == 0
    assert preview["source_acquisition_clue_count"] == 1
    assert preview["recording_ready_count"] == 0
    assert preview["recording_ready_status"] == "source_clue_only_primary_source_required"
    assert preview["source_acquisition_clues"][0]["clue_status"] == "source_clue_only"


def schema_status_results(*, ready: bool = True, revision: str | None = None) -> list[FakeExecuteResult]:
    table_rows = [
        FakeExecuteResult(rows=[{"exists": ready}])
        for _ in REQUIRED_TRUTH_TABLES
    ]
    revision = revision or (EXPECTED_TRUTH_ALEMBIC_REVISION if ready else "008_lead_lineage")
    return [
        *table_rows,
        FakeExecuteResult(rows=[{"exists": True}]),
        FakeExecuteResult(rows=[{"version_num": revision}]),
    ]


def test_confidence_rewards_agreement_and_blocks_diligence_on_contradiction():
    strong = compute_confidence(ConfidenceInput(
        claim_type="building_management",
        supporting_sources=["hpd_contacts", "building_management", "company_website"],
        contradicting_sources=[],
        freshness_days=12,
        source_agreement_count=3,
    ))
    conflicted = compute_confidence(ConfidenceInput(
        claim_type="building_management",
        supporting_sources=["hpd_contacts", "building_management", "company_website"],
        contradicting_sources=["outreach_confirmed"],
        freshness_days=12,
        source_agreement_count=3,
        source_disagreement_count=1,
    ))

    assert strong["confidence_score"] > conflicted["confidence_score"]
    assert strong["actionability_level"] in {"recommended_outreach", "acquisition_quality_diligence"}
    assert conflicted["actionability_level"] != "acquisition_quality_diligence"
    assert strong["rationale"]["confidence_policy_version"] == CONFIDENCE_POLICY_VERSION
    assert strong["rationale"]["verified_confidence_threshold"] == VERIFIED_CONFIDENCE_THRESHOLD
    assert strong["rationale"]["average_supporting_source_quality"] > 0
    assert strong["rationale"]["raw_confidence_before_smoothing"] > 0


def test_confidence_dedupes_sources_and_rewards_distinct_high_authority_bundle():
    duplicate_outreach = compute_confidence(ConfidenceInput(
        claim_type="building_management",
        supporting_sources=["outreach_confirmed", "outreach_confirmed", "outreach_confirmed"],
        contradicting_sources=[],
        freshness_days=7,
        source_agreement_count=3,
    ))
    distinct_bundle = compute_confidence(ConfidenceInput(
        claim_type="building_management",
        supporting_sources=["outreach_confirmed", "hpd_management_company"],
        contradicting_sources=[],
        freshness_days=7,
        source_agreement_count=4,
    ))
    broad_corrob = compute_confidence(ConfidenceInput(
        claim_type="building_management",
        supporting_sources=[
            "hpm_revenue_by_property_summary",
            "openigloo",
            "renthistory",
            "renthop",
            "zillow",
        ],
        contradicting_sources=[],
        freshness_days=7,
        source_agreement_count=5,
    ))

    assert duplicate_outreach["belief_status"] != "verified"
    assert duplicate_outreach["actionability_level"] != "acquisition_quality_diligence"
    assert duplicate_outreach["rationale"]["supporting_source_count"] == 1
    assert duplicate_outreach["rationale"]["supporting_sources"] == ["outreach_confirmed"]
    assert distinct_bundle["belief_status"] == "verified"
    assert distinct_bundle["confidence_score"] >= VERIFIED_CONFIDENCE_THRESHOLD
    assert distinct_bundle["actionability_level"] == "acquisition_quality_diligence"
    assert distinct_bundle["rationale"]["confidence_source_quality_basis"] == (
        "strongest_distinct_supporting_sources"
    )
    assert distinct_bundle["rationale"]["confidence_source_quality_basis_average"] > (
        broad_corrob["rationale"]["confidence_source_quality_basis_average"]
    )
    assert broad_corrob["belief_status"] != "verified"


def test_claim_adjudication_marks_only_independently_supported_fresh_facts_verified():
    candidate = adjudicate_fact_group({
        "subject_type": "lead",
        "subject_id": "lead-1",
        "predicate": "manages_building",
        "object_type": "building",
        "object_id": "1000000001",
        "normalized_value": "Lead 1 manages 1000000001",
        "claim_type": "building_management",
        "claim_ids": ["claim-a", "claim-b"],
        "evidence_ids": ["evidence-a", "evidence-b"],
        "supporting_sources": ["outreach_confirmed", "hpd_management_company"],
        "contradicting_sources": [],
        "supporting_evidence_count": 4,
        "contradicting_evidence_count": 0,
        "freshest_observed_freshness_days": 7,
        "oldest_observed_freshness_days": 30,
        "existing_belief_statuses": ["likely"],
        "max_confidence_score": 0.82,
    })

    assert candidate["safe_to_mark_verified"] is True
    assert candidate["proposed_confidence"] == candidate["recomputed_confidence_score"]
    assert candidate["verified_confidence_threshold"] == VERIFIED_CONFIDENCE_THRESHOLD
    assert candidate["score_gap_to_verified"] == 0
    assert candidate["proposed_belief_status"] == "verified"
    assert candidate["recommended_queue"] == "safe_auto_accept"
    assert candidate["blockers"] == []

    single_source = adjudicate_fact_group({
        "subject_type": "lead",
        "subject_id": "lead-2",
        "predicate": "manages_building",
        "object_type": "building",
        "object_id": "1000000002",
        "normalized_value": "Lead 2 manages 1000000002",
        "claim_type": "building_management",
        "claim_ids": ["claim-c"],
        "evidence_ids": ["evidence-c"],
        "supporting_sources": ["building_management"],
        "contradicting_sources": [],
        "supporting_evidence_count": 1,
        "contradicting_evidence_count": 0,
        "freshest_observed_freshness_days": 7,
        "existing_belief_statuses": ["likely"],
        "max_confidence_score": 0.71,
    })

    assert single_source["safe_to_mark_verified"] is False
    assert single_source["proposed_confidence"] == single_source["recomputed_confidence_score"]
    assert single_source["verified_confidence_threshold"] == VERIFIED_CONFIDENCE_THRESHOLD
    assert single_source["score_gap_to_verified"] > 0
    assert single_source["confidence_rationale"]["average_supporting_source_quality"] > 0
    assert single_source["recommended_queue"] == "insufficient_evidence"
    assert "needs_independent_source" in single_source["blockers"]
    assert "confidence_below_verified_threshold" in single_source["blockers"]


def test_verified_confidence_gap_plan_targets_source_ready_below_threshold():
    candidate = adjudicate_fact_group({
        "subject_type": "lead",
        "subject_id": "lead-1",
        "predicate": "manages_building",
        "object_type": "building",
        "object_id": "1000000001",
        "normalized_value": "manager",
        "claim_type": "building_management",
        "claim_ids": ["claim-a"],
        "evidence_ids": ["evidence-a", "evidence-b"],
        "supporting_sources": ["building_management", "openigloo"],
        "contradicting_sources": [],
        "supporting_evidence_count": 2,
        "contradicting_evidence_count": 0,
        "freshest_observed_freshness_days": 7,
        "oldest_observed_freshness_days": 7,
        "existing_belief_statuses": ["likely"],
        "max_confidence_score": 0.7,
    })

    plan = build_verified_confidence_gap_plan([candidate])

    assert candidate["blockers"] == ["confidence_below_verified_threshold"]
    assert plan["dry_run"] is True
    assert plan["mutations_planned"] == 0
    assert plan["proposal_count"] == 1
    assert plan["single_source_upgrade_would_verify_count"] == 0
    assert plan["best_single_source_upgrade_overall"]["suggested_source"] == "outreach_confirmed"
    assert plan["bundle_upgrade_would_verify_count"] == 1
    assert plan["best_bundle_upgrade_overall"]["would_reach_verified_threshold"] is True
    proposal = plan["proposals"][0]
    assert proposal["score_gap_to_verified"] > 0
    assert proposal["verified_confidence_threshold"] == VERIFIED_CONFIDENCE_THRESHOLD
    assert proposal["suggested_quality_upgrade_sources"][:2] == [
        "hpd_management_company",
        "company_website",
    ]
    assert proposal["single_source_upgrade_would_verify"] is False
    simulations = {
        upgrade["suggested_source"]: upgrade
        for upgrade in proposal["simulated_quality_upgrades"]
    }
    assert simulations["hpd_management_company"]["simulated_supporting_source_name"] == "hpd_management_company"
    assert simulations["hpd_management_company"]["source_quality_score"] == pytest.approx(0.86)
    assert simulations["outreach_confirmed"]["simulated_confidence_score"] > proposal["recomputed_confidence_score"]
    assert simulations["outreach_confirmed"]["would_reach_verified_threshold"] is False
    assert proposal["best_single_source_upgrade"]["suggested_source"] == "outreach_confirmed"
    assert "still leave the fact below verified" in simulations["outreach_confirmed"]["safe_action"]
    assert proposal["simulated_quality_bundle_upgrade"]["would_reach_verified_threshold"] is True
    assert (
        proposal["simulated_quality_bundle_upgrade"]["simulated_confidence_score"]
        > proposal["best_single_source_upgrade"]["simulated_confidence_score"]
    )
    assert "would clear the confidence threshold" in proposal["simulated_quality_bundle_upgrade"]["safe_action"]
    assert proposal["simulated_quality_bundle_upgrade"]["acquisition_required"] is True
    assert proposal["simulated_quality_bundle_upgrade"]["recording_ready"] is False
    assert proposal["simulated_quality_bundle_upgrade"]["approval_required_before_recording"] is True
    assert proposal["simulated_quality_bundle_upgrade"]["required_real_evidence"][0]["required_fields"] == [
        "source_record_id",
        "source_url_or_local_record_reference",
        "observed_at",
        "exact_property_match",
        "role_specific_management_support",
    ]
    assert proposal["manual_evidence_template"]["source_name"] == "hpd_management_company"
    assert proposal["manual_evidence_template"]["source_type"] == "hpd_management_company"
    assert proposal["manual_evidence_template"]["recording_ready"] is False
    assert "reviewed_source_record_or_url" in proposal["manual_evidence_template"]["required_before_execution"]
    assert "source-ready but not verified" in proposal["safe_action"]


def test_truth_verification_frontier_compacts_verified_and_source_acquisition_gaps():
    frontier = build_truth_verification_frontier(
        adjudication_preview={
            "verification_candidate_count": 0,
            "ledger_source_overlap": {
                "total_fact_group_count": 2078,
                "single_source_fact_group_count": 2063,
                "multi_source_fact_group_count": 15,
                "source_ready_fact_group_count": 15,
            },
            "verified_confidence_gap_plan": {
                "proposal_count": 2,
                "single_source_upgrade_would_verify_count": 1,
                "bundle_upgrade_would_verify_count": 2,
                "proposals": [{
                    "fact_key": {
                        "subject_type": "lead",
                        "subject_id": "lead-1",
                        "predicate": "manages_building",
                        "object_type": "building",
                        "object_id": "1000000001",
                        "claim_type": "building_management",
                        "normalized_value": "manager",
                    },
                    "current_sources": ["hpm_revenue_by_property_summary", "openigloo"],
                    "supporting_source_count": 2,
                    "supporting_evidence_count": 2,
                    "recomputed_confidence_score": 0.808,
                    "verified_confidence_threshold": 0.9,
                    "score_gap_to_verified": 0.092,
                    "best_single_source_upgrade": {
                        "suggested_source": "outreach_confirmed",
                        "simulated_confidence_score": 0.893,
                        "would_reach_verified_threshold": False,
                    },
                    "simulated_quality_bundle_upgrade": {
                        "suggested_sources": [
                            "hpd_management_company",
                            "company_website",
                            "outreach_confirmed",
                        ],
                        "simulated_confidence_score": 0.906,
                        "would_reach_verified_threshold": True,
                        "acquisition_required": True,
                        "recording_ready": False,
                        "approval_required_before_recording": True,
                        "required_real_evidence": [{
                            "suggested_source": "hpd_management_company",
                            "required_fields": [
                                "source_record_id",
                                "exact_property_match",
                                "role_specific_management_support",
                            ],
                        }],
                    },
                    "manual_evidence_template": {"source_name": "hpd_management_company"},
                    "safe_action": "This fact is source-ready but not verified.",
                }, {
                    "fact_key": {
                        "subject_type": "lead",
                        "subject_id": "lead-3",
                        "predicate": "manages_building",
                        "object_type": "building",
                        "object_id": "1000000003",
                        "claim_type": "building_management",
                        "normalized_value": "manager",
                    },
                    "current_sources": ["outreach_confirmed", "redfin"],
                    "supporting_source_count": 2,
                    "supporting_evidence_count": 2,
                    "recomputed_confidence_score": 0.817,
                    "verified_confidence_threshold": 0.9,
                    "score_gap_to_verified": 0.083,
                    "best_single_source_upgrade": {
                        "suggested_source": "hpd_management_company",
                        "simulated_confidence_score": 0.901,
                        "would_reach_verified_threshold": True,
                    },
                    "simulated_quality_bundle_upgrade": {
                        "suggested_sources": ["hpd_management_company", "company_website"],
                        "simulated_confidence_score": 0.906,
                        "would_reach_verified_threshold": True,
                        "acquisition_required": True,
                        "recording_ready": False,
                        "approval_required_before_recording": True,
                        "required_real_evidence": [{
                            "suggested_source": "hpd_management_company",
                            "required_fields": ["source_record_id", "exact_property_match"],
                        }],
                    },
                    "manual_evidence_template": {"source_name": "hpd_management_company"},
                    "safe_action": "This fact is source-ready but not verified.",
                }],
            },
            "verification_gap_plan": {
                "proposal_count": 1,
                "proposals": [{
                    "fact_key": {
                        "subject_type": "lead",
                        "subject_id": "lead-2",
                        "predicate": "manages_building",
                    },
                    "current_sources": ["building_management"],
                    "supporting_source_count": 1,
                    "supporting_evidence_count": 1,
                    "recomputed_confidence_score": 0.7,
                    "missing_source_count": 1,
                    "missing_evidence_count": 1,
                    "suggested_sources": ["hpd_management_company"],
                    "safe_action": "Acquire another independent source.",
                }],
            },
        },
        manager_source_packet={
            "next_source_seed_count": 1,
            "proposals": [{
                "candidate_id": "hpm-141-w-123",
                "bbl": "1019080014",
                "address": "141 WEST 123 STREET",
                "manager_lead_id": "0ff794d3ba2d",
                "manager_name": "Harlem Property Management, Inc.",
                "existing_manager_proof_source_families": ["hpd_registration_derived"],
                "supporting_source_families_if_recorded": [
                    "hpd_registration_derived",
                    "first_party_operator_document",
                ],
                "strict_manager_source_ready_if_recorded": False,
                "strict_manager_gap_status": "broad_source_ready_not_strict",
                "strict_manager_gap_reason": "One more non-HPD manager-proof family is required.",
                "missing_manager_proof_source_family_count": 1,
                "suggested_source_families": ["company_website", "outreach_confirmed"],
                "first_search_query": '"Harlem Property Management" "141 West 123 Street"',
                "search_queries": ['"Harlem Property Management" "141 West 123 Street"'],
                "safe_action": "Acquire one more manager-specific source.",
            }],
            "reviewed_source_findings": [{
                "source_family": "public_web_search_followup_hpm_batch_20_2026_05_16",
                "source_urls": ["https://renthistory.org/corporation/corp_report.php?corporationname=HARLEM+PROPERTY+MANAGEMENT+INC."],
                "finding": "141 WEST 123 STREET still resolves only to RentHistory / HPD-registration-derived context.",
                "qualification": "This adds no strict evidence template and does not change source-ready or verified counts.",
            }],
        },
        operator_source_packet={
            "second_source_seed_count": 4,
            "proposals": [{
                "candidate_id": "operator-md-220",
                "bbl": "1008747504",
                "address": "220 3 AVENUE",
                "manager_lead_id": "md-squared-lead",
                "manager_name": "MD Squared Property Group",
                "existing_manager_proof_source_families": ["operator_confirmed"],
                "supporting_source_families_if_recorded": ["operator_confirmed", "hpd_registration_derived"],
                "strict_manager_source_ready_if_recorded": False,
                "strict_manager_gap_status": "broad_source_ready_not_strict",
                "strict_manager_gap_reason": "HPD-derived second source is excluded from strict manager proof.",
                "missing_manager_proof_source_family_count": 1,
                "suggested_source_families": ["company_website", "ny_dos"],
                "first_search_query": '"MD Squared" "220 3 Avenue"',
                "search_queries": ['"MD Squared" "220 3 Avenue"'],
                "current_relationship_state": {
                    "current_building_management_relationship_count": 0,
                    "current_truth_claim_count": 0,
                    "current_ledger_source_ready": False,
                },
                "next_required_manager_proof": "Acquire one exact non-HPD manager-proof source family.",
            }],
            "reviewed_source_findings": [{
                "source_family": "public_web_search_followup_md_squared_batch_18_2026_05_16",
                "source_urls": ["https://renthistory.org/corporation/corp_report.php?corporationname=MD+Squared+Property+Group"],
                "finding": "220 3 Avenue and 57 Bond Street still resolve only to HPD-derived context.",
                "qualification": "No real HPD ManagementCompany row or exact non-HPD manager-proof source was found.",
            }],
        },
        display_context={
            "buildings_by_bbl": {
                "1000000001": {
                    "bbl": "1000000001",
                    "address": "11 ST NICHOLAS AVENUE",
                    "borough": "MANHATTAN",
                    "unit_count": 44,
                }
            },
            "leads_by_id": {
                "lead-1": {
                    "lead_id": "lead-1",
                    "company_name": "Harlem Property Management",
                }
            },
        },
        limit=1,
    )

    assert frontier["dry_run"] is True
    assert frontier["mutations_planned"] == 0
    assert frontier["current_ledger"]["multi_source_fact_group_count"] == 15
    assert frontier["verification_candidate_count"] == 0
    assert len(frontier["source_ready_below_verified"]["proposals"]) == 1
    assert frontier["source_ready_below_verified"]["proposal_count"] == 2
    ready_gap = frontier["source_ready_below_verified"]["proposals"][0]
    assert ready_gap["best_single_source_upgrade"]["would_reach_verified_threshold"] is False
    assert ready_gap["display"]["object_label"] == "11 ST NICHOLAS AVENUE"
    assert ready_gap["display"]["subject_label"] == "Harlem Property Management"
    assert ready_gap["display"]["building"]["unit_count"] == 44
    assert ready_gap["bundle_upgrade_would_verify"] is True
    assert ready_gap["recording_ready"] is False
    assert "outreach_confirmed" in ready_gap["required_bundle_sources"]
    assert ready_gap["evidence_acquisition_status"] == "acquisition_required"
    assert ready_gap["required_real_evidence_count"] == 1
    assert ready_gap["required_real_evidence"][0]["required_fields"] == [
        "source_record_id",
        "exact_property_match",
        "role_specific_management_support",
    ]
    assert ready_gap["required_real_evidence"][0]["source_dataset_ids"] == ["tesw-yqqr", "feu5-w2e2"]
    assert "--query-packet-only --indent 2" in ready_gap["required_real_evidence"][0]["read_only_preview_command"]
    assert "truth_live_hpd_role_audit.py --bbl 1000000001" in ready_gap["required_real_evidence"][0][
        "read_only_preview_command"
    ]
    assert ready_gap["required_real_evidence"][0]["official_query_urls"]["registrations_api"].startswith(
        "https://data.cityofnewyork.us/resource/tesw-yqqr"
    )
    assert ready_gap["manual_evidence_template"]["source_name"] == "hpd_management_company"
    readiness_gate = frontier["verification_readiness_gate"]
    assert readiness_gate["status"] == "blocked_evidence_acquisition_required"
    assert readiness_gate["record_ready_count"] == 0
    assert readiness_gate["source_ready_below_verified_count"] == 2
    assert readiness_gate["acquisition_required_count"] == 2
    assert readiness_gate["required_real_evidence_count"] == 2
    assert readiness_gate["one_source_threshold_clear_count"] == 1
    assert readiness_gate["bundle_threshold_clear_count"] == 2
    assert frontier["single_source_gaps"]["proposals"][0]["suggested_sources"] == ["hpd_management_company"]
    assert frontier["single_source_gaps"]["proposals"][0]["missing_source_count"] == 1
    assert frontier["source_acquisition_frontier"]["manager_next_source_seed_count"] == 1
    assert frontier["source_acquisition_frontier"]["manager_proposals"][0]["address"] == "141 WEST 123 STREET"
    assert frontier["source_acquisition_frontier"]["manager_proposals"][0][
        "existing_manager_proof_source_families"
    ] == ["hpd_registration_derived"]
    assert frontier["source_acquisition_frontier"]["operator_proposals"][0]["address"] == "220 3 AVENUE"
    assert frontier["source_acquisition_frontier"]["operator_proposals"][0]["current_relationship_state"][
        "current_truth_claim_count"
    ] == 0
    evidence_packet = frontier["evidence_request_packet"]
    assert evidence_packet["dry_run"] is True
    assert evidence_packet["mutations_planned"] == 0
    assert evidence_packet["request_count"] == 5
    assert evidence_packet["displayed_request_count"] == 4
    assert evidence_packet["recording_ready_count"] == 0
    assert evidence_packet["approval_required_count"] == 4
    assert evidence_packet["source_ready_request_count"] == 2
    assert evidence_packet["single_source_request_count"] == 1
    assert evidence_packet["source_acquisition_request_count"] == 2
    assert evidence_packet["reviewed_source_finding_count"] == 2
    assert evidence_packet["reviewed_source_history_status"] == "reviewed_dead_end_no_recording_ready_source"
    assert evidence_packet["reviewed_source_findings"][0]["source_family"] == (
        "public_web_search_followup_hpm_batch_20_2026_05_16"
    )
    assert "Agent is not manager" in evidence_packet["policy"]["role_policy"]
    source_ready_request = evidence_packet["source_ready_requests"][0]
    assert source_ready_request["relationship_label"] == "Harlem Property Management manages building 11 ST NICHOLAS AVENUE"
    assert source_ready_request["can_become"] == "verified_candidate_after_real_evidence_preview_recording_and_adjudication"
    assert source_ready_request["required_real_evidence"][0]["required_fields"] == [
        "source_record_id",
        "exact_property_match",
        "role_specific_management_support",
    ]
    assert source_ready_request["required_real_evidence"][0]["acquisition_mode"] == "official_hpd_query_packet_only"
    assert "--expected-manager \"Harlem Property Management\"" in source_ready_request["required_real_evidence"][0][
        "read_only_preview_command"
    ]
    assert source_ready_request["threshold_paths"][1]["path_type"] == "quality_bundle"
    assert source_ready_request["reviewed_source_history_status"] == "reviewed_dead_end_no_recording_ready_source"
    assert source_ready_request["reviewed_source_findings"][0]["source_family"] == (
        "public_web_search_followup_hpm_batch_20_2026_05_16"
    )
    single_request = evidence_packet["single_source_requests"][0]
    assert single_request["request_type"] == "single_source_gap"
    assert single_request["missing_source_count"] == 1
    assert single_request["suggested_sources"] == ["hpd_management_company"]
    manager_acquisition_request = evidence_packet["source_acquisition_requests"][0]
    assert manager_acquisition_request["reviewed_source_findings"][0]["qualification"].startswith(
        "This adds no strict evidence template"
    )
    acquisition_request = evidence_packet["source_acquisition_requests"][1]
    assert acquisition_request["request_type"] == "operator_source_acquisition"
    assert acquisition_request["relationship"]["manager_name"] == "MD Squared Property Group"
    assert acquisition_request["relationship"]["bbl"] == "1008747504"
    assert acquisition_request["strict_manager_gap_status"] == "broad_source_ready_not_strict"
    assert acquisition_request["fact_key"]["subject_id"] == "md-squared-lead"
    assert acquisition_request["reviewed_source_history_status"] == "reviewed_dead_end_no_recording_ready_source"
    assert acquisition_request["reviewed_source_findings"][0]["source_family"] == (
        "public_web_search_followup_md_squared_batch_18_2026_05_16"
    )
    assert "does not mark claims verified" in frontier["safe_action"]


def test_truth_verification_frontier_prioritizes_threshold_review_history():
    frontier = build_truth_verification_frontier(
        adjudication_preview={
            "ledger_source_overlap": {
                "total_fact_group_count": 2078,
                "single_source_fact_group_count": 2063,
                "multi_source_fact_group_count": 15,
                "source_ready_fact_group_count": 15,
            },
            "verification_candidate_count": 0,
            "verified_confidence_gap_plan": {
                "proposal_count": 1,
                "single_source_upgrade_would_verify_count": 1,
                "bundle_upgrade_would_verify_count": 1,
                "proposals": [{
                    "fact_key": {
                        "subject_type": "lead",
                        "subject_id": "daisy-lead",
                        "predicate": "manages_building",
                        "object_type": "building",
                        "object_id": "3010680037",
                    },
                    "display": {
                        "subject_label": "Daisy Management",
                        "relationship_label": "Daisy Management manages building 9 PROSPECT PARK WEST",
                        "building": {"bbl": "3010680037", "address": "9 PROSPECT PARK WEST"},
                    },
                    "current_sources": ["homes", "outreach_confirmed", "redfin"],
                    "supporting_source_count": 3,
                    "supporting_evidence_count": 3,
                    "recomputed_confidence_score": 0.817,
                    "verified_confidence_threshold": 0.9,
                    "score_gap_to_verified": 0.083,
                    "best_single_source_upgrade": {
                        "suggested_source": "hpd_management_company",
                        "simulated_confidence_score": 0.906,
                        "would_reach_verified_threshold": True,
                    },
                    "required_bundle_sources": [
                        "hpd_management_company",
                        "company_website",
                        "ny_dos",
                    ],
                    "simulated_quality_bundle_upgrade": {
                        "suggested_sources": [
                            "hpd_management_company",
                            "company_website",
                            "ny_dos",
                        ],
                        "simulated_confidence_score": 0.906,
                        "would_reach_verified_threshold": True,
                        "acquisition_required": True,
                        "recording_ready": False,
                        "approval_required_before_recording": True,
                        "required_real_evidence": [{
                            "suggested_source": "hpd_management_company",
                            "required_fields": ["source_record_id", "exact_property_match"],
                        }],
                    },
                    "required_real_evidence": [{
                        "suggested_source": "hpd_management_company",
                        "required_fields": ["source_record_id", "exact_property_match"],
                    }],
                    "manual_evidence_template": {"source_name": "hpd_management_company"},
                    "safe_action": "Acquire exact role-specific evidence before marking verified.",
                }],
            },
            "verification_gap_plan": {"proposal_count": 0, "proposals": []},
        },
        manager_source_packet={"reviewed_source_findings": []},
        operator_source_packet={
            "reviewed_source_findings": [
                {
                    "source_family": "operator_document_native_sheet_retry_md_daisy_2026_05_19_phase7",
                    "finding": "The operator workbook did not contain exact target rows.",
                    "qualification": "No manager-proof source was found.",
                },
                {
                    "source_family": "live_hpd_threshold_candidate_role_audit_2026_06_01",
                    "finding": (
                        "Official HPD rows name Daisy Management as Agent for 9 Prospect Park West "
                        "but do not include ManagementCompany."
                    ),
                    "qualification": (
                        "Agent rows stay legal-contact evidence and are not manager-proof support."
                    ),
                },
                {
                    "source_family": "public_web_dob_now_md2_57_bond_clue_2026_05_19",
                    "finding": "A DOB-style clue was role-ambiguous for a different relationship.",
                    "qualification": "Clue-only context cannot support management.",
                },
            ],
        },
        display_context={
            "buildings_by_bbl": {
                "3010680037": {"bbl": "3010680037", "address": "9 PROSPECT PARK WEST"},
            },
            "leads_by_id": {
                "daisy-lead": {"lead_id": "daisy-lead", "company_name": "Daisy Management"},
            },
        },
        limit=1,
    )

    source_ready_request = frontier["evidence_request_packet"]["source_ready_requests"][0]

    assert source_ready_request["relationship_label"] == "Daisy Management manages building 9 PROSPECT PARK WEST"
    assert source_ready_request["reviewed_source_findings"][0]["source_family"] == (
        "live_hpd_threshold_candidate_role_audit_2026_06_01"
    )
    assert "ManagementCompany" in source_ready_request["reviewed_source_findings"][0]["finding"]
    assert source_ready_request["reviewed_source_history_status"] == "reviewed_dead_end_no_recording_ready_source"


def test_truth_source_acquisition_worklist_prioritizes_human_source_tasks():
    frontier = {
        "current_ledger": {
            "total_fact_group_count": 2078,
            "single_source_fact_group_count": 2063,
            "multi_source_fact_group_count": 15,
            "source_ready_fact_group_count": 15,
        },
        "verification_candidate_count": 0,
        "evidence_request_packet": {
            "recording_ready_count": 0,
            "approval_required_count": 2,
            "requests": [
                {
                    "request_type": "source_ready_below_verified",
                    "relationship_label": "Daisy Management manages building 9 PROSPECT PARK WEST",
                    "fact_key": {
                        "subject_type": "lead",
                        "subject_id": "daisy-lead",
                        "predicate": "manages_building",
                        "object_type": "building",
                        "object_id": "3010680037",
                    },
                    "display": {
                        "subject_label": "Daisy Management",
                        "object_label": "9 PROSPECT PARK WEST",
                        "relationship_label": "Daisy Management manages building 9 PROSPECT PARK WEST",
                        "building": {"bbl": "3010680037", "address": "9 PROSPECT PARK WEST"},
                    },
                    "current_sources": ["homes", "redfin", "outreach_confirmed"],
                    "current_confidence_score": 0.817,
                    "score_gap_to_verified": 0.083,
                    "can_become": "verified_candidate_after_real_evidence_preview_recording_and_adjudication",
                    "evidence_need": "Acquire role-specific HPD manager evidence.",
                    "required_sources": ["hpd_management_company", "company_website"],
                    "required_real_evidence": [
                        {
                            "suggested_source": "hpd_management_company",
                            "required_fields": [
                                "source_record_id",
                                "source_url_or_local_record_reference",
                                "observed_at",
                                "exact_property_match",
                                "role_specific_management_support",
                            ],
                            "official_query_urls": {
                                "registrations_api": "https://data.cityofnewyork.us/resource/tesw-yqqr.json?$limit=50000",
                            },
                            "download_urls": [
                                "https://data.cityofnewyork.us/api/views/tesw-yqqr/rows.csv?accessType=DOWNLOAD",
                                "https://data.cityofnewyork.us/api/views/feu5-w2e2/rows.csv?accessType=DOWNLOAD",
                            ],
                            "read_only_preview_command": (
                                ".\\.venv-x64\\Scripts\\python.exe "
                                "scripts\\truth_live_hpd_role_audit.py --query-packet-only --bbl 3010680037"
                            ),
                            "post_fetch_local_extract_command": (
                                ".\\.venv-x64\\Scripts\\python.exe "
                                "scripts\\truth_live_hpd_role_audit.py --registrations-file <path> --contacts-file <path>"
                            ),
                        }
                    ],
                    "reviewed_source_history_status": "reviewed_dead_end_no_recording_ready_source",
                    "reviewed_source_findings": [
                        {
                            "source_family": "official_hpd_and_public_web_refresh_md_daisy_2026_05_18",
                            "source_urls": ["https://example.test/already-reviewed"],
                            "finding": "No official ManagementCompany row has been acquired yet.",
                            "qualification": "This is reviewed history, not recording-ready evidence.",
                        }
                    ],
                },
                {
                    "request_type": "operator_source_acquisition",
                    "relationship_label": "MD Squared Property Group manages building 220 3 AVENUE",
                    "relationship": {
                        "manager_name": "MD Squared Property Group",
                        "manager_lead_id": "md-squared-lead",
                        "address": "220 3 AVENUE",
                        "bbl": "1008747504",
                        "relationship_label": "MD Squared Property Group manages building 220 3 AVENUE",
                    },
                    "fact_key": {
                        "subject_type": "lead",
                        "subject_id": "md-squared-lead",
                        "predicate": "manages_building",
                        "object_type": "building",
                        "object_id": "1008747504",
                    },
                    "current_sources": [],
                    "strict_manager_gap_status": "broad_source_ready_not_strict",
                    "can_become": "strict_manager_source_gap_after_operator_seed",
                    "evidence_need": "Acquire one exact non-HPD manager-proof source family.",
                    "suggested_source_families": [
                        "company_website",
                        "hpd_management_company",
                        "outreach_confirmed",
                    ],
                    "search_queries": ['"MD Squared" "220 3 Avenue"'],
                    "source_targets": [{"source_family": "company_website", "target": "Exact property page"}],
                    "required_real_evidence": [
                        {
                            "suggested_source_family": "hpd_management_company",
                            "required_fields": [
                                "source_record_id",
                                "source_url_or_local_record_reference",
                                "observed_at",
                                "exact_property_match",
                                "role_specific_management_support",
                            ],
                            "official_query_urls": {
                                "registrations_api": "https://data.cityofnewyork.us/resource/tesw-yqqr.json?bbl=1008747504",
                            },
                            "download_urls": [
                                "https://data.cityofnewyork.us/api/views/tesw-yqqr/rows.csv?accessType=DOWNLOAD",
                                "https://data.cityofnewyork.us/api/views/feu5-w2e2/rows.csv?accessType=DOWNLOAD",
                            ],
                            "read_only_preview_command": (
                                ".\\.venv-x64\\Scripts\\python.exe "
                                "scripts\\truth_live_hpd_role_audit.py --query-packet-only --bbl 1008747504"
                            ),
                            "post_fetch_local_extract_command": (
                                ".\\.venv-x64\\Scripts\\python.exe "
                                "scripts\\truth_live_hpd_role_audit.py --registrations-file <path> --contacts-file <path>"
                            ),
                        }
                    ],
                    "reviewed_source_history_status": "reviewed_dead_end_no_recording_ready_source",
                    "reviewed_source_findings": [
                        {
                            "source_family": "official_hpd_and_public_web_refresh_md_daisy_2026_05_18",
                            "source_urls": ["https://example.test/renthistory-derived"],
                            "finding": "220 3 Avenue still resolves only to HPD-derived context.",
                            "qualification": "No exact non-HPD manager-proof source was found.",
                        }
                    ],
                },
            ],
        },
    }

    worklist = build_source_acquisition_worklist(frontier, max_items=2)

    assert worklist["dry_run"] is True
    assert worklist["mutations_planned"] == 0
    assert worklist["request_count"] == 2
    assert worklist["work_item_count"] == 2
    assert worklist["hpd_work_item_count"] == 2
    assert worklist["recording_ready_count"] == 0
    assert "No single-source claim" in worklist["policy"]["single_source_policy"]
    assert "Agent is not manager" in worklist["policy"]["role_policy"]
    assert "not evidence" in worklist["safe_action"]

    first_item = worklist["work_items"][0]
    assert first_item["priority"] == 10
    assert first_item["request_type"] == "operator_source_acquisition"
    assert first_item["relationship"]["address"] == "220 3 AVENUE"
    assert first_item["relationship"]["bbl"] == "1008747504"
    assert first_item["paste_back_template"]["source_family"] == "hpd_management_company"
    assert first_item["paste_back_template"]["manager_name"] == "MD Squared Property Group"
    assert [template["source_family"] for template in first_item["paste_back_templates"]] == [
        "hpd_management_company",
        "company_website",
        "outreach_confirmed",
    ]
    assert [template["source_name"] for template in first_item["paste_back_templates"]] == [
        "hpd_management_company",
        "company_website",
        "outreach_confirmed",
    ]
    assert first_item["operator_confirmation_request"]["source_family"] == "outreach_confirmed"
    assert first_item["operator_confirmation_request"]["paste_back_template"]["exact_property_match"] is True
    assert first_item["operator_confirmation_request"]["paste_back_template"]["role_specific_management_support"] is True
    assert first_item["operator_confirmation_request"]["contradiction_paste_back_template"]["contradicts_current_claim"] is True
    assert first_item["operator_confirmation_request"]["contradiction_paste_back_template"]["role_specific_management_support"] is False
    assert "different manager" in first_item["operator_confirmation_request"]["contradiction_handling"]
    assert "route to review" in first_item["operator_confirmation_request"]["contradiction_handling"]
    assert "same first-hand note" in first_item["operator_confirmation_request"]["non_duplicate_boundary"]
    assert "--candidate-csv <filled-worklist.csv>" in first_item["operator_confirmation_request"]["preview_command"]
    assert first_item["official_hpd_query"]["registrations_api"].endswith("bbl=1008747504")
    assert "truth_live_hpd_role_audit.py" in first_item["read_only_hpd_preview_command"]
    assert "Only `ManagementCompany` contact rows" in first_item["acceptance_criteria"][2]
    assert "Agent" in first_item["acceptance_criteria"][3]
    assert first_item["source_family_needs"] == ["company_website", "hpd_management_company", "outreach_confirmed"]
    assert first_item["reviewed_source_findings"][0]["qualification"] == "No exact non-HPD manager-proof source was found."
    assert "Do not record evidence or mark verified" in first_item["safe_action"]

    second_item = worklist["work_items"][1]
    assert second_item["priority"] == 20
    assert second_item["relationship"]["address"] == "9 PROSPECT PARK WEST"
    assert second_item["relationship"]["manager_name"] == "Daisy Management"
    assert second_item["current_sources"] == ["homes", "redfin", "outreach_confirmed"]
    assert second_item["paste_back_fields"] == [
        "relationship_label",
        "bbl",
        "address",
        "manager_name",
        "source_family",
        "source_name",
        "source_url_or_local_record_reference",
        "source_record_id",
        "observed_at",
        "exact_property_match",
        "role_specific_management_support",
        "source_excerpt_or_row_summary",
        "contradicts_current_claim",
        "notes",
    ]
    csv_template = build_source_acquisition_csv_template(worklist)
    assert csv_template.startswith("relationship_label,bbl,address,manager_name,source_family,source_name")
    assert "MD Squared Property Group manages building 220 3 AVENUE,1008747504,220 3 AVENUE" in csv_template
    assert "Daisy Management manages building 9 PROSPECT PARK WEST,3010680037,9 PROSPECT PARK WEST" in csv_template
    assert ",hpd_management_company,hpd_management_company,,,,,,,," in csv_template
    assert ",company_website,company_website,,,,,,,," in csv_template
    assert ",outreach_confirmed,outreach_confirmed,,,,,,,," in csv_template
    assert csv_template.count("MD Squared Property Group manages building 220 3 AVENUE") == 3

    hpd_fetch_packet = build_source_acquisition_hpd_fetch_packet(worklist)
    assert hpd_fetch_packet.startswith("work_item_id,relationship_label,bbl,address,manager_name")
    assert "source-acquisition-001,MD Squared Property Group manages building 220 3 AVENUE" in hpd_fetch_packet
    assert "https://data.cityofnewyork.us/resource/tesw-yqqr.json?bbl=1008747504" in hpd_fetch_packet
    assert "Only ManagementCompany rows can support manages_building evidence" in hpd_fetch_packet

    operator_packet = build_source_acquisition_operator_confirmation_packet(worklist)
    assert operator_packet.startswith("work_item_id,question_prompt,non_duplicate_boundary")
    assert "Can you independently confirm that MD Squared Property Group currently manages 220 3 AVENUE?" in operator_packet
    assert "same first-hand note already in the ledger" in operator_packet
    assert "MD Squared Property Group manages building 220 3 AVENUE,1008747504,220 3 AVENUE" in operator_packet
    assert "truth_source_evidence_intake.py --candidate-csv <filled-worklist.csv>" in operator_packet
    assert "If the source names a different manager as current manager" in operator_packet

    api_worklist = truth_router._attach_source_acquisition_csv_handoff(
        dict(worklist),
        lead_id="0ff794d3ba2d",
        frontier_limit=10,
        max_items=2,
    )
    assert api_worklist["csv_template"].startswith("relationship_label,bbl,address,manager_name")
    assert api_worklist["hpd_fetch_packet"].startswith("work_item_id,relationship_label,bbl,address,manager_name")
    assert api_worklist["operator_confirmation_packet"].startswith("work_item_id,question_prompt,non_duplicate_boundary")
    assert api_worklist["csv_template_command"] == (
        "truth_source_acquisition_worklist.py --lead-id 0ff794d3ba2d "
        "--frontier-limit 10 --max-items 2 --csv-template"
    )
    assert api_worklist["hpd_fetch_packet_command"] == (
        "truth_source_acquisition_worklist.py --lead-id 0ff794d3ba2d "
        "--frontier-limit 10 --max-items 2 --hpd-fetch-packet"
    )
    assert api_worklist["operator_confirmation_packet_command"] == (
        "truth_source_acquisition_worklist.py --lead-id 0ff794d3ba2d "
        "--frontier-limit 10 --max-items 2 --operator-confirmation-packet"
    )
    assert api_worklist["candidate_csv_preview_command"] == (
        "truth_source_evidence_intake.py --candidate-csv <filled-worklist.csv> "
        "--recommended-scope-only --indent 2"
    )


def test_source_overlap_blocker_report_explains_no_recording_ready_sources():
    frontier = {
        "current_ledger": {
            "total_fact_group_count": 2078,
            "single_source_fact_group_count": 2063,
            "multi_source_fact_group_count": 15,
            "source_ready_fact_group_count": 15,
        },
        "verification_candidate_count": 0,
        "source_ready_below_verified": {
            "proposal_count": 10,
            "single_source_upgrade_would_verify_count": 2,
            "bundle_upgrade_would_verify_count": 10,
            "proposals": [
                {
                    "display": {
                        "subject_label": "Daisy Management",
                        "relationship_label": "Daisy Management manages building 9 PROSPECT PARK WEST",
                        "building": {
                            "bbl": "3010680037",
                            "address": "9 PROSPECT PARK WEST",
                        },
                    },
                    "current_sources": ["homes", "outreach_confirmed", "redfin"],
                    "recomputed_confidence_score": 0.817,
                    "verified_confidence_threshold": 0.9,
                    "score_gap_to_verified": 0.083,
                    "best_single_source_upgrade": {
                        "suggested_source": "hpd_management_company",
                        "simulated_confidence_score": 0.906,
                        "would_reach_verified_threshold": True,
                    },
                    "required_bundle_sources": [
                        "hpd_management_company",
                        "company_website",
                        "ny_dos",
                        "ny_dps_order_entry",
                    ],
                    "recording_ready": False,
                    "approval_required_before_recording": True,
                    "safe_action": "Acquire exact role-specific evidence before marking verified.",
                },
                {
                    "display": {
                        "subject_label": "Harlem Property Management",
                        "relationship_label": "Harlem Property Management manages building 342 WEST 56 STREET",
                        "building": {
                            "bbl": "1010460054",
                            "address": "342 WEST 56 STREET",
                        },
                    },
                    "current_sources": ["hpm_revenue_by_property_summary", "openigloo"],
                    "best_single_source_upgrade": {
                        "suggested_source": "outreach_confirmed",
                        "simulated_confidence_score": 0.893,
                        "would_reach_verified_threshold": False,
                    },
                },
            ],
        },
        "evidence_request_packet": {
            "recording_ready_count": 0,
            "approval_required_count": 15,
            "reviewed_source_finding_count": 75,
            "reviewed_source_history_status": "reviewed_dead_end_no_recording_ready_source",
            "verification_readiness_gate": {
                "status": "blocked_evidence_acquisition_required",
                "record_ready_count": 0,
            },
            "requests": [
                {
                    "request_type": "source_ready_below_verified",
                    "relationship_label": "Daisy Management manages building 9 PROSPECT PARK WEST",
                    "required_real_evidence_count": 4,
                    "reviewed_source_history_status": "reviewed_dead_end_no_recording_ready_source",
                    "reviewed_source_findings": [
                        {
                            "source_family": "live_hpd_threshold_candidate_role_audit_2026_06_01",
                            "finding": "Official HPD rows name Daisy as Agent but do not include ManagementCompany.",
                            "qualification": "Agent rows stay legal-contact evidence and are not manager-proof support.",
                        }
                    ],
                },
                {
                    "request_type": "operator_source_acquisition",
                    "relationship": {
                        "relationship_label": "Daisy Management manages building 9 PROSPECT PARK WEST",
                        "bbl": "3010680037",
                        "address": "9 PROSPECT PARK WEST",
                        "manager_name": "Daisy Management",
                    },
                    "reviewed_source_history_status": "reviewed_dead_end_no_recording_ready_source",
                    "reviewed_source_findings": [
                        {
                            "source_family": "operator_document_native_sheet_retry_md_daisy_2026_05_19_phase7",
                            "finding": "Later duplicate acquisition request with less-specific reviewed history.",
                            "qualification": "Should not overwrite the source-ready threshold review context.",
                        }
                    ],
                },
                {
                    "request_type": "operator_source_acquisition",
                    "relationship": {
                        "relationship_label": "MD Squared Property Group manages building 220 3 AVENUE",
                        "bbl": "1008747504",
                        "address": "220 3 AVENUE",
                        "manager_name": "MD Squared Property Group",
                        "manager_lead_id": "56a71624c6c0",
                    },
                    "strict_manager_gap_status": "broad_source_ready_not_strict",
                    "suggested_source_families": ["company_website", "hpd_management_company"],
                    "required_real_evidence": [
                        {
                            "suggested_source_family": "hpd_management_company",
                            "official_query_urls": {"registrations_api": "https://example.test/hpd"},
                            "download_urls": ["https://example.test/download.csv"],
                            "post_fetch_local_extract_command": (
                                "truth_live_hpd_role_audit.py --registrations-file <path>"
                            ),
                        }
                    ],
                    "reviewed_source_history_status": "reviewed_dead_end_no_recording_ready_source",
                    "reviewed_source_findings": [
                        {
                            "source_family": "official_hpd_and_public_web_refresh_md_daisy_2026_05_19_phase2",
                            "finding": (
                                "Official row fetch was blocked and public search found only HPD-derived context."
                            ),
                            "qualification": "No recording-ready manager-proof source was found.",
                        }
                    ],
                }
            ],
        },
    }
    worklist = build_source_acquisition_worklist(frontier, max_items=1)

    report = build_source_overlap_blocker_report(
        frontier=frontier,
        worklist=worklist,
        source_evidence_batch_preview={
            "source_mode": "candidate_file_recommended_scope_only",
            "candidate_count": 2,
            "original_candidate_count": 4,
            "filtered_out_candidate_count": 2,
            "ready_for_manual_evidence_preview_count": 2,
            "recording_ready_count": 2,
            "new_supporting_source_ready_count": 2,
            "supporting_source_already_present_count": 0,
            "contradiction_candidate_count": 0,
            "blocked_count": 0,
            "allowed_execute": False,
            "required_execute_flags_for_batch": [
                "--execute",
                "--confirm-execute",
                "--confirm-batch-execute",
            ],
            "recommended_recording_scope": {
                "recommended_count": 2,
                "recommended_relationships": [{
                    "work_item_id": "source-acquisition-001",
                    "relationship_label": "MD Squared Property Group manages building 220 3 AVENUE",
                    "bbl": "1008747504",
                    "address": "220 3 AVENUE",
                    "manager_name": "MD Squared Property Group",
                    "source_name": "outreach_confirmed",
                    "effect_status": "adds_new_supporting_source",
                }],
                "duplicate_or_freshness_only_count": 2,
                "duplicate_or_freshness_only_relationships": [],
                "contradiction_review_count": 0,
                "contradiction_relationships": [],
                "post_recording_expectations": {
                    "must_hold": {"no_single_source_claim_marked_verified": True},
                },
            },
            "recording_approval_packet": {
                "status": "preview_ready_approval_required",
                "approval_required": True,
                "allowed_execute": False,
                "recommended_count": 2,
                "approval_question": "Approve recording 2 preview-clean new-supporting-source manual-evidence row(s) only?",
                "execute_command_after_approval": (
                    "truth_manual_evidence.py --payload-file <reviewed-preview.json> "
                    "--execute --confirm-execute --confirm-batch-execute"
                ),
            },
        },
        max_relationships=1,
    )

    assert report["run_type"] == "truth_source_overlap_blocker_report"
    assert report["dry_run"] is True
    assert report["mutations_planned"] == 0
    assert report["status"] == "blocked_evidence_acquisition_required"
    assert report["verification_candidate_count"] == 0
    assert report["threshold_sensitive_relationships"] == [
        {
            "relationship_label": "Daisy Management manages building 9 PROSPECT PARK WEST",
            "bbl": "3010680037",
            "address": "9 PROSPECT PARK WEST",
            "manager_name": "Daisy Management",
            "current_sources": ["homes", "outreach_confirmed", "redfin"],
            "current_confidence_score": 0.817,
            "verified_confidence_threshold": 0.9,
            "score_gap_to_verified": 0.083,
            "best_single_source": "hpd_management_company",
            "best_single_source_simulated_confidence": 0.906,
            "required_bundle_sources": [
                "hpd_management_company",
                "company_website",
                "ny_dos",
                "ny_dps_order_entry",
            ],
            "required_real_evidence_count": 4,
            "recording_ready": False,
            "approval_required_before_recording": True,
            "reviewed_source_history_status": "reviewed_dead_end_no_recording_ready_source",
            "reviewed_source_findings": [
                {
                    "source_family": "live_hpd_threshold_candidate_role_audit_2026_06_01",
                    "finding": "Official HPD rows name Daisy as Agent but do not include ManagementCompany.",
                    "qualification": "Agent rows stay legal-contact evidence and are not manager-proof support.",
                }
            ],
            "safe_action": "Acquire exact role-specific evidence before marking verified.",
        }
    ]
    assert report["evidence_request_summary"]["recording_ready_count"] == 0
    assert report["evidence_request_summary"]["reviewed_source_finding_count"] == 75
    assert report["source_bridge_assessment"]["can_record_evidence_now"] is False
    assert report["source_bridge_assessment"]["can_request_recording_approval"] is True
    assert report["source_bridge_assessment"]["has_preview_ready_candidate_batch"] is True
    assert report["source_bridge_assessment"]["candidate_preview_status"] == "preview_ready_approval_required"
    assert report["source_bridge_assessment"]["candidate_recording_ready_count"] == 2
    assert report["source_bridge_assessment"]["candidate_recommended_count"] == 2
    assert report["source_bridge_assessment"]["candidate_allowed_execute"] is False
    assert "preview-clean candidate batch" in report["source_bridge_assessment"]["approval_boundary"]
    assert "recording_ready_count=0" in report["source_bridge_assessment"]["blocking_reasons"]
    assert "verification_candidate_count=0" in report["source_bridge_assessment"]["blocking_reasons"]
    assert "execution_approval_required_for_preview_ready_candidates" in report["source_bridge_assessment"][
        "blocking_reasons"
    ]
    assert report["source_evidence_candidate_summary"]["status"] == "preview_ready_approval_required"
    assert report["source_evidence_candidate_summary"]["checked"] is True
    assert report["source_evidence_candidate_summary"]["recording_ready_count"] == 2
    assert report["source_evidence_candidate_summary"]["recommended_count"] == 2
    assert report["source_evidence_candidate_summary"]["allowed_execute"] is False
    assert report["source_evidence_candidate_summary"]["required_execute_flags_for_batch"] == [
        "--execute",
        "--confirm-execute",
        "--confirm-batch-execute",
    ]
    assert report["source_evidence_candidate_summary"]["recording_approval_packet"]["approval_required"] is True
    assert report["source_evidence_candidate_summary"]["recording_approval_packet"]["recommended_count"] == 2
    assert report["source_evidence_candidate_summary"]["recording_approval_packet"][
        "execute_command_after_approval"
    ].endswith("--execute --confirm-execute --confirm-batch-execute")
    assert report["source_evidence_candidate_summary"]["recommended_relationships"][0]["bbl"] == "1008747504"
    assert report["top_blocked_relationships"][0]["relationship_label"] == (
        "MD Squared Property Group manages building 220 3 AVENUE"
    )
    assert report["top_blocked_relationships"][0]["has_official_hpd_query_packet"] is True
    assert report["reviewed_source_summary"]["reviewed_source_family_counts"] == {
        "official_hpd_and_public_web_refresh_md_daisy_2026_05_19_phase2": 1
    }
    assert "No single-source claim" in report["policy"]["single_source_policy"]
    assert "does not permit business use" in report["safe_action"]


def test_source_overlap_blocker_report_marks_clue_only_packets_not_recordable():
    frontier = {
        "current_ledger": {
            "total_fact_group_count": 2078,
            "single_source_fact_group_count": 2063,
            "multi_source_fact_group_count": 15,
            "source_ready_fact_group_count": 15,
        },
        "verification_candidate_count": 0,
        "source_ready_below_verified": {
            "proposal_count": 10,
            "single_source_upgrade_would_verify_count": 2,
            "bundle_upgrade_would_verify_count": 10,
        },
        "evidence_request_packet": {
            "recording_ready_count": 0,
            "approval_required_count": 15,
            "reviewed_source_finding_count": 79,
            "reviewed_source_history_status": "reviewed_dead_end_no_recording_ready_source",
            "verification_readiness_gate": {
                "status": "blocked_evidence_acquisition_required",
                "record_ready_count": 0,
            },
            "requests": [],
        },
    }
    worklist = build_source_acquisition_worklist(frontier, max_items=1)
    clue_preview = intake_script.build_source_acquisition_clue_only_preview(
        [{
            "address": "342 WEST 56 STREET",
            "bbl": "1010460054",
            "expected_manager": "Harlem Property Management",
            "clue_status": "source_clue_only",
            "requires_primary_source_review": True,
        }],
        source_mode="candidate_file",
    )

    report = build_source_overlap_blocker_report(
        frontier=frontier,
        worklist=worklist,
        source_evidence_batch_preview=clue_preview,
    )

    summary = report["source_evidence_candidate_summary"]
    assert summary["status"] == "source_clue_only_primary_source_required"
    assert summary["checked"] is True
    assert summary["candidate_count"] == 0
    assert summary["source_acquisition_clue_count"] == 1
    assert summary["recording_ready_count"] == 0
    assert summary["recommended_count"] == 0
    assert summary["allowed_execute"] is False
    assert summary["can_record_evidence_now"] is False
    assert report["source_bridge_assessment"]["has_preview_ready_candidate_batch"] is False
    assert report["source_bridge_assessment"]["has_source_acquisition_clues"] is True
    assert "source_clue_only_primary_source_required" in report["source_bridge_assessment"]["blocking_reasons"]


@pytest.mark.anyio
async def test_truth_source_overlap_blocker_report_endpoint_is_read_only(monkeypatch):
    async def fake_schema_status(session):
        return {
            "ready": True,
            "current_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "expected_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "missing_tables": [],
            "migration_current": True,
            "mutations_planned": 0,
        }

    async def fake_adjudication(session, *, limit, include_samples):
        return {
            "verification_candidate_count": 0,
            "ledger_source_overlap": {
                "total_fact_group_count": 2078,
                "single_source_fact_group_count": 2063,
                "multi_source_fact_group_count": 15,
                "source_ready_fact_group_count": 15,
            },
        }

    async def fake_manager_preview(session, *, lead_id, limit):
        return {"lead_id": lead_id, "evidence_candidates": []}

    async def fake_operator_preview(session, *, limit):
        return {"candidate_count": 0}

    async def fake_display_context(session, adjudication_preview):
        return {}

    def fake_frontier(**kwargs):
        return {
            "current_ledger": {
                "total_fact_group_count": 2078,
                "single_source_fact_group_count": 2063,
                "multi_source_fact_group_count": 15,
                "source_ready_fact_group_count": 15,
            },
            "verification_candidate_count": 0,
            "source_ready_below_verified": {
                "proposal_count": 10,
                "single_source_upgrade_would_verify_count": 2,
                "bundle_upgrade_would_verify_count": 10,
            },
            "evidence_request_packet": {
                "reviewed_source_finding_count": 75,
                "reviewed_source_history_status": "reviewed_dead_end_no_recording_ready_source",
                "verification_readiness_gate": {"status": "blocked_evidence_acquisition_required"},
            },
        }

    def fake_worklist(frontier, *, max_items=10):
        return {
            "request_count": 15,
            "work_item_count": max_items,
            "hpd_work_item_count": max_items,
            "recording_ready_count": 0,
            "approval_required_count": 15,
            "work_items": [{
                "work_item_id": "source-acquisition-001",
                "priority": 10,
                "request_type": "operator_source_acquisition",
                "relationship": {
                    "relationship_label": "MD Squared Property Group manages building 220 3 AVENUE",
                    "bbl": "1008747504",
                    "address": "220 3 AVENUE",
                    "manager_name": "MD Squared Property Group",
                },
                "current_sources": [],
                "source_family_needs": ["hpd_management_company"],
                "official_hpd_query": {"registrations_api": "https://example.test/hpd"},
                "reviewed_source_history_status": "reviewed_dead_end_no_recording_ready_source",
                "reviewed_source_findings": [{
                    "source_family": "official_hpd_and_public_web_refresh_md_daisy_2026_05_19_phase2",
                    "qualification": "No recording-ready manager-proof source was found.",
                }],
            }],
        }

    monkeypatch.setattr(truth_router, "load_truth_schema_status", fake_schema_status)
    monkeypatch.setattr(truth_router, "load_claim_adjudication_preview", fake_adjudication)
    monkeypatch.setattr(truth_router, "load_manager_external_source_acquisition_preview", fake_manager_preview)
    monkeypatch.setattr(truth_router, "load_operator_confirmed_management_preview", fake_operator_preview)
    monkeypatch.setattr(truth_router, "load_frontier_display_context", fake_display_context)
    monkeypatch.setattr(truth_router, "build_manager_source_acquisition_packet", lambda *args, **kwargs: {})
    monkeypatch.setattr(truth_router, "build_operator_source_acquisition_packet", lambda *args, **kwargs: {})
    monkeypatch.setattr(truth_router, "build_truth_verification_frontier", fake_frontier)
    monkeypatch.setattr(truth_router, "build_source_acquisition_worklist", fake_worklist)

    session = FakeAsyncSession([])
    response = await truth_router.truth_source_overlap_blocker_report(
        lead_id="0ff794d3ba2d",
        frontier_limit=10,
        max_items=5,
        max_relationships=1,
        session=session,
        user=AuthUser(user_id="u1", email="reviewer@example.com"),
    )

    assert response["run_type"] == "truth_source_overlap_blocker_report"
    assert response["dry_run"] is True
    assert response["mutations_planned"] == 0
    assert response["status"] == "blocked_evidence_acquisition_required"
    assert response["verification_candidate_count"] == 0
    assert response["evidence_request_summary"]["recording_ready_count"] == 0
    assert response["source_bridge_assessment"]["can_record_evidence_now"] is False
    assert response["top_blocked_relationships"][0]["bbl"] == "1008747504"
    assert response["schema_status"]["ready"] is True
    assert session.rollback_count == 1


@pytest.mark.anyio
async def test_truth_source_overlap_blocker_report_preview_embeds_candidate_packet(monkeypatch):
    async def fake_schema_status(session):
        return {
            "ready": True,
            "current_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "expected_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "missing_tables": [],
            "migration_current": True,
            "mutations_planned": 0,
        }

    async def fake_adjudication(session, *, limit, include_samples):
        return {
            "verification_candidate_count": 0,
            "ledger_source_overlap": {
                "total_fact_group_count": 2078,
                "single_source_fact_group_count": 2063,
                "multi_source_fact_group_count": 15,
                "source_ready_fact_group_count": 15,
            },
        }

    async def fake_manager_preview(session, *, lead_id, limit):
        return {"lead_id": lead_id, "evidence_candidates": []}

    async def fake_operator_preview(session, *, limit):
        return {"candidate_count": 0}

    async def fake_display_context(session, adjudication_preview):
        return {}

    def fake_frontier(**kwargs):
        return {
            "current_ledger": {
                "total_fact_group_count": 2078,
                "single_source_fact_group_count": 2063,
                "multi_source_fact_group_count": 15,
                "source_ready_fact_group_count": 15,
            },
            "verification_candidate_count": 0,
            "source_ready_below_verified": {
                "proposal_count": 10,
                "single_source_upgrade_would_verify_count": 2,
                "bundle_upgrade_would_verify_count": 10,
            },
            "evidence_request_packet": {
                "reviewed_source_finding_count": 75,
                "reviewed_source_history_status": "reviewed_dead_end_no_recording_ready_source",
                "verification_readiness_gate": {"status": "blocked_evidence_acquisition_required"},
            },
        }

    def fake_worklist(frontier, *, max_items=10):
        return {
            "request_count": 15,
            "work_item_count": max_items,
            "hpd_work_item_count": max_items,
            "recording_ready_count": 0,
            "approval_required_count": 15,
            "work_items": [{
                "work_item_id": "source-acquisition-001",
                "priority": 10,
                "request_type": "operator_source_acquisition",
                "relationship": {
                    "relationship_label": "MD Squared Property Group manages building 220 3 AVENUE",
                    "bbl": "1008747504",
                    "address": "220 3 AVENUE",
                    "manager_name": "MD Squared Property Group",
                },
                "current_sources": [],
                "source_family_needs": ["hpd_management_company"],
            }],
        }

    def fake_intake_preview(payload, *, worklist):
        return {
            "run_type": "truth_source_evidence_intake_preview",
            "dry_run": True,
            "mutations_planned": 0,
            "validation_status": "ready_for_manual_evidence_preview",
            "recording_ready": False,
            "support_status": "supports",
            "relationship_match": {
                "work_item_id": "source-acquisition-001",
                "request_type": "operator_source_acquisition",
                "relationship": {
                    "relationship_label": "MD Squared Property Group manages building 220 3 AVENUE",
                    "bbl": "1008747504",
                    "address": "220 3 AVENUE",
                    "manager_name": "MD Squared Property Group",
                },
            },
            "source_overlap_effect": {
                "source_name": "outreach_confirmed",
                "current_sources": [],
                "source_already_present": False,
                "adds_new_supporting_source": True,
                "effect_status": "new_supporting_source",
            },
            "manual_evidence_payload": {
                "subject_type": "lead",
                "subject_id": "md-squared-lead",
                "predicate": "manages_building",
                "object_type": "building",
                "object_id": "1008747504",
                "claim_type": "management_relationship",
                "normalized_value": "MD Squared Property Group manages building 220 3 AVENUE",
                "source_name": "outreach_confirmed",
            },
        }

    async def fake_manual_evidence(session, *, payload, recorded_by, dry_run, confirm_execute):
        return {
            "run_type": "manual_evidence_capture",
            "dry_run": True,
            "mutations_planned": 1,
            "allowed_execute": False,
            "payload": payload,
            "claim_spec": {
                "claim_id": "claim-001",
                "evidence_id": "evidence-001",
                "source_name": payload["source_name"],
            },
        }

    monkeypatch.setattr(truth_router, "load_truth_schema_status", fake_schema_status)
    monkeypatch.setattr(truth_router, "load_claim_adjudication_preview", fake_adjudication)
    monkeypatch.setattr(truth_router, "load_manager_external_source_acquisition_preview", fake_manager_preview)
    monkeypatch.setattr(truth_router, "load_operator_confirmed_management_preview", fake_operator_preview)
    monkeypatch.setattr(truth_router, "load_frontier_display_context", fake_display_context)
    monkeypatch.setattr(truth_router, "build_manager_source_acquisition_packet", lambda *args, **kwargs: {})
    monkeypatch.setattr(truth_router, "build_operator_source_acquisition_packet", lambda *args, **kwargs: {})
    monkeypatch.setattr(truth_router, "build_truth_verification_frontier", fake_frontier)
    monkeypatch.setattr(truth_router, "build_source_acquisition_worklist", fake_worklist)
    monkeypatch.setattr(truth_router, "build_source_evidence_intake_preview", fake_intake_preview)
    monkeypatch.setattr(truth_router, "preview_or_record_manual_evidence", fake_manual_evidence)

    session = FakeAsyncSession([])
    response = await truth_router.truth_source_overlap_blocker_report_preview(
        body=truth_router.SourceOverlapBlockerReportPreviewRequest(
            candidates=[truth_router.SourceEvidenceIntakeRequest(
                relationship_label="MD Squared Property Group manages building 220 3 AVENUE",
                bbl="1008747504",
                address="220 3 AVENUE",
                manager_name="MD Squared Property Group",
                source_family="outreach_confirmed",
                source_name="outreach_confirmed",
                exact_property_match=True,
                role_specific_management_support=True,
                source_excerpt_or_row_summary="Operator confirmed current manager.",
            )],
            source_mode="operator_confirmed_candidate",
            recommended_scope_only=True,
        ),
        lead_id="0ff794d3ba2d",
        frontier_limit=10,
        max_items=5,
        max_relationships=1,
        session=session,
        user=AuthUser(user_id="u1", email="reviewer@example.com"),
    )

    candidate_summary = response["source_evidence_candidate_summary"]
    assert response["run_type"] == "truth_source_overlap_blocker_report"
    assert response["dry_run"] is True
    assert response["mutations_planned"] == 0
    assert response["source_bridge_assessment"]["has_preview_ready_candidate_batch"] is True
    assert response["source_bridge_assessment"]["can_record_evidence_now"] is False
    assert response["source_bridge_assessment"]["can_request_recording_approval"] is True
    assert response["source_bridge_assessment"]["candidate_recording_ready_count"] == 1
    assert response["source_bridge_assessment"]["candidate_recommended_count"] == 1
    assert response["source_bridge_assessment"]["candidate_allowed_execute"] is False
    assert candidate_summary["status"] == "preview_ready_approval_required"
    assert candidate_summary["source_mode"] == "operator_confirmed_candidate_recommended_scope_only"
    assert candidate_summary["recording_ready_count"] == 1
    assert candidate_summary["recommended_count"] == 1
    assert candidate_summary["allowed_execute"] is False
    assert candidate_summary["recording_approval_packet"]["approval_required"] is True
    assert candidate_summary["recording_approval_packet"]["allowed_execute"] is False
    assert candidate_summary["recording_approval_packet"]["recommended_relationships"][0]["bbl"] == "1008747504"
    assert candidate_summary["recording_approval_packet"]["manual_evidence_payload_count"] == 1
    assert candidate_summary["recording_approval_packet"]["manual_evidence_payloads"][0]["object_id"] == "1008747504"
    assert (
        candidate_summary["recording_approval_packet"]["manual_evidence_payload_review"][0]["manager_name"]
        == "MD Squared Property Group"
    )
    assert candidate_summary["recording_approval_packet"]["execute_command_after_approval"].endswith(
        "--execute --confirm-execute --confirm-batch-execute"
    )
    assert response["schema_status"]["ready"] is True
    assert session.rollback_count == 1


@pytest.mark.anyio
async def test_source_evidence_batch_preview_api_returns_clue_only_packet():
    worklist = {
        "request_count": 15,
        "work_item_count": 5,
        "recording_ready_count": 0,
        "approval_required_count": 15,
        "work_items": [],
    }
    schema_status = {"ready": True, "mutations_planned": 0}
    response = await truth_router._preview_source_evidence_intake_batch_request(
        truth_router.SourceEvidenceIntakeBatchRequest(
            hpd_audit_output={
                "run_type": "operator_document_audit",
                "document_kind": "derived_research",
                "source_evidence_intake_candidates": [],
                "source_acquisition_clues": [{
                    "address": "342 WEST 56 STREET",
                    "bbl": "1010460054",
                    "expected_manager": "Harlem Property Management",
                    "clue_status": "source_clue_only",
                    "requires_primary_source_review": True,
                }],
            },
            source_mode="derived_research",
            recommended_scope_only=True,
        ),
        worklist=worklist,
        schema_status=schema_status,
        session=FakeAsyncSession([]),
        recorded_by="reviewer@example.com",
    )

    assert response["run_type"] == "truth_source_evidence_intake_clue_only_preview"
    assert response["source_mode"] == "derived_research"
    assert response["candidate_count"] == 0
    assert response["source_acquisition_clue_count"] == 1
    assert response["recording_ready_count"] == 0
    assert response["allowed_execute"] is False
    assert response["recording_ready_status"] == "source_clue_only_primary_source_required"
    assert response["schema_status"] == schema_status
    assert response["worklist_context"]["recording_ready_count"] == 0


def test_source_evidence_intake_converts_clean_paste_back_to_manual_preview_payload():
    worklist = {
        "work_items": [{
            "work_item_id": "source-acquisition-001",
            "request_type": "operator_source_acquisition",
            "relationship": {
                "relationship_label": "MD Squared Property Group manages building 220 3 AVENUE",
                "bbl": "1008747504",
                "address": "220 3 AVENUE",
                "manager_name": "MD Squared Property Group",
                "manager_lead_id": "md-squared-lead",
            },
            "current_sources": [],
        }],
    }
    paste_back = {
        "relationship_label": "MD Squared Property Group manages building 220 3 AVENUE",
        "bbl": "1008747504",
        "address": "220 3 AVENUE",
        "manager_name": "MD Squared Property Group",
        "source_family": "hpd_management_company",
        "source_name": "hpd_management_company",
        "source_url_or_local_record_reference": "https://data.cityofnewyork.us/resource/feu5-w2e2.json?registrationid=123",
        "source_record_id": "feu5-w2e2:registrationid:123:ManagementCompany:MD Squared",
        "observed_at": "2026-05-19T00:00:00+00:00",
        "exact_property_match": True,
        "role_specific_management_support": True,
        "source_excerpt_or_row_summary": "ManagementCompany row names MD Squared for the exact BBL.",
        "contradicts_current_claim": False,
        "notes": "Official HPD local extract reviewed.",
    }

    preview = build_source_evidence_intake_preview(paste_back, worklist=worklist)

    assert preview["dry_run"] is True
    assert preview["mutations_planned"] == 0
    assert preview["validation_status"] == "ready_for_manual_evidence_preview"
    assert preview["recording_ready"] is False
    assert preview["approval_required_before_recording"] is True
    assert preview["relationship_match"]["status"] == "matched_current_work_item"
    assert preview["relationship_match"]["work_item_id"] == "source-acquisition-001"
    assert preview["blocking_reasons"] == []
    assert preview["source_overlap_effect"] == {
        "source_name": "hpd_management_company",
        "current_sources": [],
        "current_supporting_source_count": 0,
        "projected_supporting_source_count": 1,
        "projected_new_supporting_source_count_delta": 1,
        "source_already_present": False,
        "adds_new_supporting_source": True,
        "effect_status": "adds_new_supporting_source",
        "expected_overlap_effect_status": "adds_first_supporting_source_only",
        "would_be_first_source_only_after_recording": True,
        "would_be_multi_source_after_recording": False,
        "would_be_source_ready_after_recording": False,
        "safe_action": (
            "This candidate would create the first supporting source for this relationship, but it would not "
            "create independent source overlap by itself. A second independent exact-property source is still "
            "needed before the fact can be source-ready."
        ),
    }
    payload = preview["manual_evidence_payload"]
    assert payload["subject_type"] == "lead"
    assert payload["subject_id"] == "md-squared-lead"
    assert payload["predicate"] == "manages_building"
    assert payload["object_id"] == "1008747504"
    assert payload["claim_type"] == "building_management"
    assert payload["support_status"] == "supports"
    assert payload["source_name"] == "hpd_management_company"
    assert payload["source_url"].startswith("https://data.cityofnewyork.us/resource/feu5-w2e2")
    assert payload["raw_payload"]["source_acquisition_intake"] is True
    assert payload["raw_payload"]["exact_property_match"] is True
    assert "explicit approval" in preview["safe_action"]
    assert "manual-evidence execute flags" in preview["safe_action"]


def test_source_evidence_intake_flags_duplicate_support_source_without_blocking_preview():
    worklist = {
        "work_items": [{
            "work_item_id": "source-acquisition-007",
            "request_type": "source_ready_below_verified",
            "relationship": {
                "relationship_label": "Daisy Management manages building 9 PROSPECT PARK WEST",
                "bbl": "3010680037",
                "address": "9 PROSPECT PARK WEST",
                "manager_name": "Daisy Management",
                "manager_lead_id": "daisy-lead",
            },
            "current_sources": ["homes", "outreach_confirmed", "redfin"],
        }],
    }
    paste_back = {
        "relationship_label": "Daisy Management manages building 9 PROSPECT PARK WEST",
        "bbl": "3010680037",
        "address": "9 PROSPECT PARK WEST",
        "manager_name": "Daisy Management",
        "source_family": "operator_confirmed",
        "source_name": "outreach_confirmed",
        "source_url_or_local_record_reference": "operator-confirmed-chat-2026-05-19:first-hand",
        "source_record_id": "operator-confirmed-2026-05-19-9-prospect-park-west-daisy",
        "observed_at": "2026-05-19",
        "exact_property_match": True,
        "role_specific_management_support": True,
        "source_excerpt_or_row_summary": "Operator confirmed Daisy manages 9 Prospect Park W.",
        "contradicts_current_claim": False,
        "notes": "Preview only.",
    }

    preview = build_source_evidence_intake_preview(paste_back, worklist=worklist)

    assert preview["validation_status"] == "ready_for_manual_evidence_preview"
    assert preview["blocking_reasons"] == []
    assert preview["source_overlap_effect"]["source_already_present"] is True
    assert preview["source_overlap_effect"]["adds_new_supporting_source"] is False
    assert preview["source_overlap_effect"]["effect_status"] == "source_already_present"
    assert "does not add a new independent supporting source" in preview["source_overlap_effect"]["safe_action"]
    assert preview["manual_evidence_payload"]["source_name"] == "outreach_confirmed"


def test_source_evidence_intake_accepts_outreach_family_csv_row_without_duplicate_source_name():
    worklist = {
        "work_items": [{
            "work_item_id": "source-acquisition-001",
            "request_type": "operator_source_acquisition",
            "relationship": {
                "relationship_label": "MD Squared Property Group manages building 220 3 AVENUE",
                "bbl": "1008747504",
                "address": "220 3 AVENUE",
                "manager_name": "MD Squared Property Group",
                "manager_lead_id": "md-squared-lead",
            },
            "current_sources": [],
        }],
    }
    paste_back = {
        "relationship_label": "MD Squared Property Group manages building 220 3 AVENUE",
        "bbl": "1008747504",
        "address": "220 3 AVENUE",
        "manager_name": "MD Squared Property Group",
        "source_family": "outreach_confirmed",
        "source_name": "",
        "source_url_or_local_record_reference": "operator-call:2026-05-19:md-squared-220-3-avenue",
        "source_record_id": "operator-call:2026-05-19:md-squared-220-3-avenue",
        "observed_at": "2026-05-19T00:00:00+00:00",
        "exact_property_match": "true",
        "role_specific_management_support": "true",
        "source_excerpt_or_row_summary": "Second dated operator call confirmed MD Squared manages 220 3 Avenue.",
        "contradicts_current_claim": "false",
        "notes": "CSV row filled from source-acquisition worklist.",
    }

    preview = build_source_evidence_intake_preview(paste_back, worklist=worklist)

    assert preview["validation_status"] == "ready_for_manual_evidence_preview"
    assert preview["blocking_reasons"] == []
    assert preview["source_overlap_effect"]["source_name"] == "outreach_confirmed"
    assert preview["source_overlap_effect"]["adds_new_supporting_source"] is True
    assert preview["manual_evidence_payload"]["source_name"] == "outreach_confirmed"
    assert preview["manual_evidence_payload"]["source_type"] == "outreach_confirmed"
    assert preview["manual_evidence_payload"]["raw_payload"]["role_specific_management_support"] is True


def test_source_evidence_intake_blocks_unmatched_or_role_weak_paste_back():
    paste_back = {
        "relationship_label": "MD Squared Property Group manages building 220 3 AVENUE",
        "bbl": "1008747504",
        "address": "220 3 AVENUE",
        "manager_name": "MD Squared Property Group",
        "source_family": "hpd_management_company",
        "source_name": "hpd_management_company",
        "source_url_or_local_record_reference": "local extract row",
        "source_record_id": "agent-row-only",
        "observed_at": "2026-05-19T00:00:00+00:00",
        "exact_property_match": True,
        "role_specific_management_support": False,
        "contradicts_current_claim": False,
    }

    preview = build_source_evidence_intake_preview(paste_back, worklist={"work_items": []})

    assert preview["validation_status"] == "blocked_before_manual_evidence_preview"
    assert preview["relationship_match"]["status"] == "unmatched"
    assert "relationship_not_matched_to_current_worklist" in preview["blocking_reasons"]
    assert "missing_or_invalid_role_specific_management_support=true" in preview["blocking_reasons"]
    assert preview["source_overlap_effect"]["adds_new_supporting_source"] is True
    assert preview["manual_evidence_payload"]["support_status"] == "supports"


def test_source_evidence_intake_routes_contradictions_as_contradicting_evidence():
    worklist = {
        "work_items": [{
            "work_item_id": "source-acquisition-004",
            "request_type": "source_ready_below_verified",
            "relationship": {
                "relationship_label": "Harlem Property Management manages building 324 EAST 112 STREET",
                "bbl": "1016837501",
                "address": "324 EAST 112 STREET",
                "manager_name": "Harlem Property Management",
                "manager_lead_id": "0ff794d3ba2d",
            },
        }],
    }
    paste_back = {
        "relationship_label": "Harlem Property Management manages building 324 EAST 112 STREET",
        "bbl": "1016837501",
        "address": "324 EAST 112 STREET",
        "manager_name": "Harlem Property Management",
        "source_family": "outreach_confirmed",
        "source_name": "outreach_confirmed",
        "source_url_or_local_record_reference": "operator call note 2026-05-19",
        "source_record_id": "operator-call:2026-05-19:324-e-112",
        "observed_at": "2026-05-19T00:00:00+00:00",
        "exact_property_match": True,
        "role_specific_management_support": False,
        "source_excerpt_or_row_summary": "Contact said HPM does not manage this building.",
        "contradicts_current_claim": True,
        "notes": "Route to review.",
    }

    preview = build_source_evidence_intake_preview(paste_back, worklist=worklist)

    assert preview["validation_status"] == "ready_for_manual_evidence_preview"
    assert preview["support_status"] == "contradicts"
    assert preview["blocking_reasons"] == []
    assert preview["source_overlap_effect"]["effect_status"] == "contradiction_review_evidence"
    assert preview["source_overlap_effect"]["adds_new_supporting_source"] is False
    assert preview["manual_evidence_payload"]["support_status"] == "contradicts"
    assert preview["manual_evidence_payload"]["raw_payload"]["contradicts_current_claim"] is True


def test_source_evidence_intake_extracts_candidates_from_hpd_audit_output():
    candidate = {
        "relationship_label": "MD Squared Property Group manages building 220 3 AVENUE",
        "bbl": "1008747504",
        "manager_name": "MD Squared Property Group",
        "source_name": "hpd_management_company",
    }
    payload = {
        "run_type": "truth_live_hpd_role_audit",
        "results": [
            {"source_evidence_intake_candidates": [candidate]},
            {"source_evidence_intake_candidates": []},
        ],
    }

    assert intake_script._extract_hpd_audit_candidates(payload) == [candidate]
    assert intake_script._extract_hpd_audit_candidates([candidate]) == [candidate]


def test_source_evidence_intake_extracts_candidates_from_operator_document_audit_output():
    rows = [{"Property": "141 W 123", "Source": "Revenue by Property - Summary row 99"}]
    payload = audit_operator_document_rows(
        rows,
        targets=TARGET_PRESETS["hpm-next"],
        document_title="Revenue by Property - Summary",
        observed_at="2026-05-19T00:00:00+00:00",
        operator_confirmed_document_provenance=True,
    )

    candidates = intake_script._extract_hpd_audit_candidates(payload)

    assert len(candidates) == 1
    assert candidates[0]["relationship_label"] == "Harlem Property Management manages building 141 WEST 123 STREET"
    assert candidates[0]["bbl"] == "1019080014"
    assert candidates[0]["source_name"] == "hpm_revenue_by_property_summary"
    assert candidates[0]["role_specific_management_support"] is True


def test_source_evidence_intake_handles_operator_document_clues_without_candidates():
    rows = [{"Property": "342 W 56th - Coop", "Source": "Cited workbook and public appendix"}]
    payload = audit_operator_document_rows(
        rows,
        targets=TARGET_PRESETS["hpm-pilot"][-3:-2],
        document_title="HPM Deep Research",
        document_kind="derived_research",
    )

    assert intake_script._extract_hpd_audit_candidates(payload) == []
    clues = intake_script._extract_candidate_file_clues(payload)
    preview = intake_script.build_source_acquisition_clue_only_preview(clues, source_mode="candidate_file")

    assert preview["run_type"] == "truth_source_evidence_intake_clue_only_preview"
    assert preview["candidate_count"] == 0
    assert preview["source_acquisition_clue_count"] == 1
    assert preview["recording_ready_count"] == 0
    assert preview["allowed_execute"] is False
    assert preview["recording_ready_status"] == "source_clue_only_primary_source_required"
    assert preview["source_acquisition_clues"][0]["clue_status"] == "source_clue_only"
    assert "Do not record evidence" in preview["safe_action"]


def test_manual_evidence_payload_file_extractor_keeps_only_new_supporting_source_batch_items():
    recommended_payload = {
        "subject_type": "lead",
        "subject_id": "md-squared-lead",
        "predicate": "manages_building",
        "object_type": "building",
        "object_id": "1008747504",
        "claim_type": "building_management",
        "source_name": "outreach_confirmed",
    }
    duplicate_payload = {
        **recommended_payload,
        "object_id": "1008170057",
    }
    contradiction_payload = {
        **recommended_payload,
        "object_id": "1005297507",
        "support_status": "contradicts",
    }

    preview_batch = {
        "run_type": "truth_source_evidence_intake_batch_preview",
        "previews": [
            {
                "validation_status": "ready_for_manual_evidence_preview",
                "recording_ready": True,
                "source_overlap_effect": {"adds_new_supporting_source": True},
                "manual_evidence_payload": recommended_payload,
            },
            {
                "validation_status": "ready_for_manual_evidence_preview",
                "recording_ready": True,
                "source_overlap_effect": {"source_already_present": True},
                "manual_evidence_payload": duplicate_payload,
            },
            {
                "validation_status": "ready_for_manual_evidence_preview",
                "recording_ready": True,
                "source_overlap_effect": {"adds_new_supporting_source": False},
                "manual_evidence_payload": contradiction_payload,
            },
        ],
    }

    assert manual_evidence_script._extract_manual_payloads(preview_batch) == [recommended_payload]
    assert manual_evidence_script._extract_manual_payloads({"manual_evidence_payload": recommended_payload}) == [
        recommended_payload
    ]


def test_manual_evidence_batch_result_requires_explicit_batch_execute_confirmation():
    preview = {
        "run_id": "truth-manual-evidence-batch-test-001",
        "mutations_planned": 3,
        "claims_upserted": 0,
        "evidence_upserted": 0,
        "confidence_snapshots_upserted": 0,
        "mutation_scope": {"allowed_tables": ["truth_claims"]},
    }

    blocked = manual_evidence_script._batch_result(
        results=[preview],
        batch_run_id="truth-manual-evidence-batch-test",
        payload_count=1,
        execute_requested=True,
        confirm_execute=True,
        confirm_batch_execute=False,
    )
    allowed = manual_evidence_script._batch_result(
        results=[{**preview, "claims_upserted": 1, "evidence_upserted": 1, "confidence_snapshots_upserted": 1}],
        batch_run_id="truth-manual-evidence-batch-test",
        payload_count=1,
        execute_requested=True,
        confirm_execute=True,
        confirm_batch_execute=True,
    )

    assert blocked["dry_run"] is True
    assert blocked["allowed_execute"] is False
    assert "--confirm-batch-execute" in blocked["approval_boundary"]["required_execute_flags"]
    assert blocked["blocked_reason"]
    assert "truth_adjudication_preview.py" in blocked["post_execution_expectations"]["must_run"]
    assert blocked["post_execution_expectations"]["must_hold"]["no_single_source_claim_marked_verified"] is True
    assert (
        blocked["post_execution_expectations"]["acceptable_after_operator_seed_recording"][
            "verification_candidate_count_may_remain_zero"
        ]
        is True
    )
    assert allowed["dry_run"] is False
    assert allowed["allowed_execute"] is True
    assert allowed["blocked_reason"] is None
    assert allowed["post_execution_expectations"]["must_hold"]["no_business_use_activation"] is True


def test_source_evidence_intake_reads_candidate_csv(tmp_path):
    csv_path = tmp_path / "source_candidates.csv"
    csv_path.write_text(
        "\ufeffrelationship_label,bbl,address,manager_name,source_family,source_name,"
        "source_url_or_local_record_reference,source_record_id,observed_at,"
        "exact_property_match,role_specific_management_support,"
        "source_excerpt_or_row_summary,contradicts_current_claim,notes\n"
        "MD Squared Property Group manages building 220 3 AVENUE,1008747504,"
        "220 3 AVENUE,MD Squared Property Group,hpd_management_company,"
        "hpd_management_company,local-extract:feu5-w2e2,record-1,"
        "2026-05-19T00:00:00+00:00,true,true,"
        "Official HPD row names MD Squared as ManagementCompany,false,reviewed\n"
        ",,,,,,,,,,,,,\n",
        encoding="utf-8",
    )

    candidates = intake_script._read_candidate_csv(csv_path)

    assert len(candidates) == 1
    assert candidates[0]["relationship_label"] == "MD Squared Property Group manages building 220 3 AVENUE"
    assert candidates[0]["bbl"] == "1008747504"
    assert candidates[0]["exact_property_match"] == "true"
    assert candidates[0]["role_specific_management_support"] == "true"
    assert candidates[0]["contradicts_current_claim"] == "false"


@pytest.mark.anyio
async def test_source_evidence_intake_batch_preview_from_hpd_candidates_is_read_only(monkeypatch):
    class FakeSessionManager:
        def __init__(self, session):
            self.session = session

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    session = FakeAsyncSession([])

    def fake_session_factory():
        return FakeSessionManager(session)

    async def fake_schema_status(fake_session):
        return {
            "ready": True,
            "current_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "expected_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "missing_tables": [],
            "migration_current": True,
            "mutations_planned": 0,
        }

    async def fake_manual_preview(fake_session, *, payload, recorded_by, dry_run, confirm_execute, run_id=None):
        return {
            "run_type": "manual_evidence_capture",
            "dry_run": dry_run,
            "mutations_planned": 3,
            "allowed_execute": False,
            "recorded_by": recorded_by,
            "payload": payload,
        }

    monkeypatch.setattr(intake_script, "get_session_factory", lambda: fake_session_factory)
    monkeypatch.setattr(intake_script, "load_truth_schema_status", fake_schema_status)
    monkeypatch.setattr(intake_script, "preview_or_record_manual_evidence", fake_manual_preview)

    worklist = {
        "request_count": 2,
        "work_item_count": 2,
        "recording_ready_count": 0,
        "approval_required_count": 2,
        "work_items": [
            {
                "work_item_id": "source-acquisition-001",
                "request_type": "operator_source_acquisition",
                "relationship": {
                    "relationship_label": "MD Squared Property Group manages building 220 3 AVENUE",
                    "bbl": "1008747504",
                    "address": "220 3 AVENUE",
                    "manager_name": "MD Squared Property Group",
                    "manager_lead_id": "md-squared-lead",
                },
            },
            {
                "work_item_id": "source-acquisition-002",
                "request_type": "operator_source_acquisition",
                "relationship": {
                    "relationship_label": "MD Squared Property Group manages building 57 BOND STREET",
                    "bbl": "1005297507",
                    "address": "57 BOND STREET",
                    "manager_name": "MD Squared Property Group",
                    "manager_lead_id": "md-squared-lead",
                },
            },
        ],
    }
    payloads = [
        {
            "relationship_label": "MD Squared Property Group manages building 220 3 AVENUE",
            "bbl": "1008747504",
            "address": "220 3 AVENUE",
            "manager_name": "MD Squared Property Group",
            "source_family": "hpd_management_company",
            "source_name": "hpd_management_company",
            "source_url_or_local_record_reference": "https://data.cityofnewyork.us/resource/feu5-w2e2.json?registrationid=113190",
            "source_record_id": "feu5-w2e2:registration:113190:managementcompany:mdsquaredpropertygroup",
            "observed_at": "2026-05-19T00:00:00+00:00",
            "exact_property_match": True,
            "role_specific_management_support": True,
            "source_excerpt_or_row_summary": "Official HPD row names MD Squared as ManagementCompany.",
            "contradicts_current_claim": False,
        },
        {
            "relationship_label": "MD Squared Property Group manages building 57 BOND STREET",
            "bbl": "1005297507",
            "address": "57 BOND STREET",
            "manager_name": "MD Squared Property Group",
            "source_family": "hpd_management_company",
            "source_name": "hpd_management_company",
            "source_url_or_local_record_reference": "https://data.cityofnewyork.us/resource/feu5-w2e2.json?registrationid=113191",
            "source_record_id": "feu5-w2e2:registration:113191:managementcompany:othermanager",
            "observed_at": "2026-05-19T00:00:00+00:00",
            "exact_property_match": True,
            "role_specific_management_support": False,
            "source_excerpt_or_row_summary": "Official HPD row names a different ManagementCompany.",
            "contradicts_current_claim": True,
        },
    ]

    result = await intake_script._preview_payloads(
        payloads=payloads,
        worklist=worklist,
        recorded_by="reviewer@example.com",
        run_id=None,
        source_mode="hpd_audit_file",
    )

    assert result["run_type"] == "truth_source_evidence_intake_batch_preview"
    assert result["dry_run"] is True
    assert result["mutations_planned"] == 0
    assert result["allowed_execute"] is False
    assert result["recording_ready_status"] == "preview_ready_approval_required"
    assert result["required_execute_flags_for_batch"] == [
        "--execute",
        "--confirm-execute",
        "--confirm-batch-execute",
    ]
    assert result["candidate_count"] == 2
    assert result["ready_for_manual_evidence_preview_count"] == 2
    assert result["recording_ready_count"] == 2
    assert result["contradiction_candidate_count"] == 1
    assert result["blocked_count"] == 0
    assert result["recommended_recording_scope"]["scope"] == "new_supporting_sources_only"
    assert result["recommended_recording_scope"]["recommended_count"] == 1
    assert result["recommended_recording_scope"]["recommended_relationships"][0]["work_item_id"] == (
        "source-acquisition-001"
    )
    assert result["recommended_recording_scope"]["manual_evidence_payload_count"] == 1
    assert result["recommended_recording_scope"]["manual_evidence_payloads"][0]["object_id"] == "1008747504"
    assert result["recommended_recording_scope"]["manual_evidence_payloads"][0]["source_name"] == "hpd_management_company"
    assert (
        result["recommended_recording_scope"]["manual_evidence_payload_review"][0]["relationship_label"]
        == "MD Squared Property Group manages building 220 3 AVENUE"
    )
    assert result["recommended_recording_scope"]["manual_evidence_payload_review"][0]["exact_property_match"] is True
    assert result["recommended_recording_scope"]["expected_post_recording_source_overlap"][
        "source_ready_after_recording_count"
    ] == 0
    assert result["recommended_recording_scope"]["expected_post_recording_source_overlap"][
        "first_source_only_after_recording_count"
    ] == 1
    assert result["recommended_recording_scope"]["expected_post_recording_source_overlap"]["rows"][0][
        "expected_overlap_effect_status"
    ] == "adds_first_supporting_source_only"
    assert result["recommended_recording_scope"]["contradiction_review_count"] == 1
    assert result["recommended_recording_scope"]["non_effects"]["will_mark_verified"] is False
    assert (
        result["recommended_recording_scope"]["post_recording_expectations"]["acceptable_after_operator_seed_recording"][
            "verification_candidate_count_may_remain_zero"
        ]
        is True
    )
    assert result["recommended_recording_scope"]["required_execute_flags_for_batch"] == [
        "--execute",
        "--confirm-execute",
        "--confirm-batch-execute",
    ]
    assert result["manual_evidence_replay_boundary"]["required_execute_flags_for_batch"] == [
        "--execute",
        "--confirm-execute",
        "--confirm-batch-execute",
    ]
    assert result["manual_evidence_replay_boundary"]["payload_file_preview_command"] == (
        "truth_manual_evidence.py --payload-file <reviewed-preview.json>"
    )
    assert result["manual_evidence_replay_boundary"]["payload_file_execute_command_after_approval"] == (
        "truth_manual_evidence.py --payload-file <reviewed-preview.json> "
        "--execute --confirm-execute --confirm-batch-execute"
    )
    assert result["recording_approval_packet"]["status"] == "preview_ready_approval_required"
    assert result["recording_approval_packet"]["approval_required"] is True
    assert result["recording_approval_packet"]["allowed_execute"] is False
    assert result["recording_approval_packet"]["recommended_count"] == 1
    assert result["recording_approval_packet"]["manual_evidence_payload_count"] == 1
    assert result["recording_approval_packet"]["manual_evidence_payloads"][0]["object_id"] == "1008747504"
    assert result["recording_approval_packet"]["expected_post_recording_source_overlap"][
        "source_ready_after_recording_count"
    ] == 0
    assert result["recording_approval_packet"]["manual_evidence_payload_review"][0]["source_record_id"] == (
        "feu5-w2e2:registration:113190:managementcompany:mdsquaredpropertygroup"
    )
    assert result["recording_approval_packet"]["excluded_count"] == 1
    assert result["recording_approval_packet"]["mutation_scope"]["forbidden_side_effects"]["will_mark_verified"] is False
    assert result["recording_approval_packet"]["execute_command_after_approval"].endswith(
        "--execute --confirm-execute --confirm-batch-execute"
    )
    assert result["manual_evidence_replay_boundary"]["will_mark_verified"] is False
    assert "truth_source_overlap_post_recording_check.py" in (
        result["manual_evidence_replay_boundary"]["post_recording_expectations"]["must_run"]
    )
    assert result["previews"][0]["manual_evidence_preview"]["payload"]["support_status"] == "supports"
    assert result["previews"][1]["manual_evidence_preview"]["payload"]["support_status"] == "contradicts"
    filtered = filter_source_evidence_batch_to_recommended_scope(result)
    assert filtered["source_mode"] == "hpd_audit_file_recommended_scope_only"
    assert filtered["allowed_execute"] is False
    assert filtered["recording_ready_status"] == "preview_ready_approval_required"
    assert filtered["required_execute_flags_for_batch"] == [
        "--execute",
        "--confirm-execute",
        "--confirm-batch-execute",
    ]
    assert filtered["original_candidate_count"] == 2
    assert filtered["candidate_count"] == 1
    assert filtered["filtered_out_candidate_count"] == 1
    assert filtered["new_supporting_source_ready_count"] == 1
    assert filtered["supporting_source_already_present_count"] == 0
    assert filtered["contradiction_candidate_count"] == 0
    assert filtered["previews"][0]["relationship_match"]["work_item_id"] == "source-acquisition-001"
    assert filtered["recommended_recording_scope"]["filtered_view"] is True
    assert filtered["manual_evidence_replay_boundary"]["required_execute_flags_for_batch"] == [
        "--execute",
        "--confirm-execute",
        "--confirm-batch-execute",
    ]
    assert filtered["recording_approval_packet"]["filtered_view"] is True
    assert filtered["recording_approval_packet"]["recommended_count"] == 1
    assert filtered["recording_approval_packet"]["manual_evidence_payload_count"] == 1
    assert filtered["recording_approval_packet"]["manual_evidence_payloads"][0]["object_id"] == "1008747504"
    assert filtered["recording_approval_packet"]["expected_post_recording_source_overlap"][
        "first_source_only_after_recording_count"
    ] == 1
    assert filtered["recording_approval_packet"]["excluded_count"] == 1
    assert session.rollback_count == 1


def test_recommended_scope_filter_is_row_level_when_work_item_has_mixed_sources():
    base_match = {
        "work_item_id": "source-acquisition-001",
        "request_type": "operator_source_acquisition",
        "relationship": {
            "relationship_label": "MD Squared Property Group manages building 220 3 AVENUE",
            "bbl": "1008747504",
            "address": "220 3 AVENUE",
            "manager_name": "MD Squared Property Group",
        },
    }
    batch = build_source_evidence_intake_batch_preview(
        [
            {
                "validation_status": "ready_for_manual_evidence_preview",
                "recording_ready": True,
                "support_status": "supports",
                "relationship_match": base_match,
                "source_overlap_effect": {
                    "source_name": "outreach_confirmed",
                    "adds_new_supporting_source": True,
                    "source_already_present": False,
                },
            },
            {
                "validation_status": "ready_for_manual_evidence_preview",
                "recording_ready": True,
                "support_status": "supports",
                "relationship_match": base_match,
                "source_overlap_effect": {
                    "source_name": "outreach_confirmed",
                    "adds_new_supporting_source": False,
                    "source_already_present": True,
                },
            },
        ],
        candidate_count=2,
        source_mode="candidate_csv",
    )

    filtered = filter_source_evidence_batch_to_recommended_scope(batch)

    assert batch["recommended_recording_scope"]["recommended_count"] == 1
    assert batch["allowed_execute"] is False
    assert batch["recording_ready_status"] == "preview_ready_approval_required"
    assert filtered["candidate_count"] == 1
    assert filtered["allowed_execute"] is False
    assert filtered["recording_ready_status"] == "preview_ready_approval_required"
    assert filtered["filtered_out_candidate_count"] == 1
    assert filtered["new_supporting_source_ready_count"] == 1
    assert filtered["supporting_source_already_present_count"] == 0
    assert filtered["previews"][0]["source_overlap_effect"]["adds_new_supporting_source"] is True
    assert filtered["previews"][0]["source_overlap_effect"]["source_already_present"] is False


@pytest.mark.anyio
async def test_truth_source_evidence_intake_endpoint_runs_preview_only(monkeypatch):
    async def fake_schema_status(session):
        return {
            "ready": True,
            "current_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "expected_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "missing_tables": [],
            "migration_current": True,
            "mutations_planned": 0,
        }

    async def fake_adjudication(session, *, limit, include_samples):
        return {
            "verification_candidate_count": 0,
            "ledger_source_overlap": {"multi_source_fact_group_count": 15},
        }

    async def fake_manager_preview(session, *, lead_id, limit):
        return {"lead_id": lead_id, "evidence_candidates": []}

    async def fake_operator_preview(session, *, limit):
        return {"candidate_count": 0}

    async def fake_display_context(session, adjudication_preview):
        return {}

    async def fake_manual_preview(session, *, payload, recorded_by, dry_run, confirm_execute, run_id=None):
        return {
            "run_type": "manual_evidence_capture",
            "dry_run": dry_run,
            "mutations_planned": 3,
            "allowed_execute": False,
            "recorded_by": recorded_by,
            "payload": payload,
        }

    def fake_worklist(frontier, *, max_items=25):
        return {
            "request_count": 1,
            "work_item_count": 1,
            "recording_ready_count": 0,
            "approval_required_count": 1,
            "work_items": [{
                "work_item_id": "source-acquisition-001",
                "request_type": "operator_source_acquisition",
                "relationship": {
                    "relationship_label": "MD Squared Property Group manages building 220 3 AVENUE",
                    "bbl": "1008747504",
                    "address": "220 3 AVENUE",
                    "manager_name": "MD Squared Property Group",
                    "manager_lead_id": "md-squared-lead",
                },
            }],
        }

    monkeypatch.setattr(truth_router, "load_truth_schema_status", fake_schema_status)
    monkeypatch.setattr(truth_router, "load_claim_adjudication_preview", fake_adjudication)
    monkeypatch.setattr(truth_router, "load_manager_external_source_acquisition_preview", fake_manager_preview)
    monkeypatch.setattr(truth_router, "load_operator_confirmed_management_preview", fake_operator_preview)
    monkeypatch.setattr(truth_router, "load_frontier_display_context", fake_display_context)
    monkeypatch.setattr(truth_router, "build_manager_source_acquisition_packet", lambda *args, **kwargs: {})
    monkeypatch.setattr(truth_router, "build_operator_source_acquisition_packet", lambda *args, **kwargs: {})
    monkeypatch.setattr(truth_router, "build_truth_verification_frontier", lambda **kwargs: {"evidence_request_packet": {}})
    monkeypatch.setattr(truth_router, "build_source_acquisition_worklist", fake_worklist)
    monkeypatch.setattr(truth_router, "preview_or_record_manual_evidence", fake_manual_preview)

    session = FakeAsyncSession([])
    response = await truth_router.truth_source_evidence_intake_preview(
        body=truth_router.SourceEvidenceIntakeRequest(
            relationship_label="MD Squared Property Group manages building 220 3 AVENUE",
            bbl="1008747504",
            address="220 3 AVENUE",
            manager_name="MD Squared Property Group",
            source_family="hpd_management_company",
            source_name="hpd_management_company",
            source_url_or_local_record_reference="https://data.cityofnewyork.us/resource/feu5-w2e2.json?registrationid=123",
            source_record_id="feu5-w2e2:123:ManagementCompany",
            observed_at="2026-05-19T00:00:00+00:00",
            exact_property_match=True,
            role_specific_management_support=True,
            contradicts_current_claim=False,
        ),
        lead_id="0ff794d3ba2d",
        frontier_limit=10,
        max_items=25,
        session=session,
        user=AuthUser(user_id="u1", email="reviewer@example.com"),
    )

    assert response["dry_run"] is True
    assert response["mutations_planned"] == 0
    assert response["validation_status"] == "ready_for_manual_evidence_preview"
    assert response["recording_ready"] is True
    assert response["manual_evidence_preview"]["dry_run"] is True
    assert response["manual_evidence_preview"]["allowed_execute"] is False
    assert response["manual_evidence_preview"]["recorded_by"] == "reviewer@example.com"
    assert response["manual_evidence_preview"]["payload"]["subject_id"] == "md-squared-lead"
    assert response["manual_evidence_preview"]["payload"]["source_name"] == "hpd_management_company"
    assert response["worklist_context"]["request_count"] == 1
    assert session.rollback_count == 1


@pytest.mark.anyio
async def test_truth_source_evidence_intake_batch_endpoint_runs_preview_only(monkeypatch):
    async def fake_schema_status(session):
        return {
            "ready": True,
            "current_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "expected_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "missing_tables": [],
            "migration_current": True,
            "mutations_planned": 0,
        }

    async def fake_adjudication(session, *, limit, include_samples):
        return {
            "verification_candidate_count": 0,
            "ledger_source_overlap": {"multi_source_fact_group_count": 15},
        }

    async def fake_manager_preview(session, *, lead_id, limit):
        return {"lead_id": lead_id, "evidence_candidates": []}

    async def fake_operator_preview(session, *, limit):
        return {"candidate_count": 0}

    async def fake_display_context(session, adjudication_preview):
        return {}

    async def fake_manual_preview(session, *, payload, recorded_by, dry_run, confirm_execute, run_id=None):
        return {
            "run_type": "manual_evidence_capture",
            "dry_run": dry_run,
            "mutations_planned": 3,
            "allowed_execute": False,
            "recorded_by": recorded_by,
            "payload": payload,
        }

    def fake_worklist(frontier, *, max_items=25):
        return {
            "request_count": 2,
            "work_item_count": 2,
            "recording_ready_count": 0,
            "approval_required_count": 2,
            "work_items": [
                {
                    "work_item_id": "source-acquisition-001",
                    "request_type": "operator_source_acquisition",
                    "relationship": {
                        "relationship_label": "MD Squared Property Group manages building 220 3 AVENUE",
                        "bbl": "1008747504",
                        "address": "220 3 AVENUE",
                        "manager_name": "MD Squared Property Group",
                        "manager_lead_id": "md-squared-lead",
                    },
                },
                {
                    "work_item_id": "source-acquisition-002",
                    "request_type": "operator_source_acquisition",
                    "relationship": {
                        "relationship_label": "MD Squared Property Group manages building 57 BOND STREET",
                        "bbl": "1005297507",
                        "address": "57 BOND STREET",
                        "manager_name": "MD Squared Property Group",
                        "manager_lead_id": "md-squared-lead",
                    },
                },
            ],
        }

    monkeypatch.setattr(truth_router, "load_truth_schema_status", fake_schema_status)
    monkeypatch.setattr(truth_router, "load_claim_adjudication_preview", fake_adjudication)
    monkeypatch.setattr(truth_router, "load_manager_external_source_acquisition_preview", fake_manager_preview)
    monkeypatch.setattr(truth_router, "load_operator_confirmed_management_preview", fake_operator_preview)
    monkeypatch.setattr(truth_router, "load_frontier_display_context", fake_display_context)
    monkeypatch.setattr(truth_router, "build_manager_source_acquisition_packet", lambda *args, **kwargs: {})
    monkeypatch.setattr(truth_router, "build_operator_source_acquisition_packet", lambda *args, **kwargs: {})
    monkeypatch.setattr(truth_router, "build_truth_verification_frontier", lambda **kwargs: {"evidence_request_packet": {}})
    monkeypatch.setattr(truth_router, "build_source_acquisition_worklist", fake_worklist)
    monkeypatch.setattr(truth_router, "preview_or_record_manual_evidence", fake_manual_preview)

    session = FakeAsyncSession([])
    response = await truth_router.truth_source_evidence_intake_batch_preview(
        body=truth_router.SourceEvidenceIntakeBatchRequest(
            recommended_scope_only=True,
            hpd_audit_output={
                "source_evidence_intake_candidates": [
                    {
                        "relationship_label": "MD Squared Property Group manages building 220 3 AVENUE",
                        "bbl": "1008747504",
                        "address": "220 3 AVENUE",
                        "manager_name": "MD Squared Property Group",
                        "source_family": "hpd_management_company",
                        "source_name": "hpd_management_company",
                        "source_url_or_local_record_reference": (
                            "https://data.cityofnewyork.us/resource/feu5-w2e2.json?registrationid=123"
                        ),
                        "source_record_id": "feu5-w2e2:123:ManagementCompany",
                        "observed_at": "2026-05-19T00:00:00+00:00",
                        "exact_property_match": True,
                        "role_specific_management_support": True,
                        "contradicts_current_claim": False,
                    },
                    {
                        "relationship_label": "MD Squared Property Group manages building 57 BOND STREET",
                        "bbl": "1005297507",
                        "address": "57 BOND STREET",
                        "manager_name": "MD Squared Property Group",
                        "source_family": "hpd_management_company",
                        "source_name": "hpd_management_company",
                        "source_url_or_local_record_reference": (
                            "https://data.cityofnewyork.us/resource/feu5-w2e2.json?registrationid=124"
                        ),
                        "source_record_id": "feu5-w2e2:124:ManagementCompany:other",
                        "observed_at": "2026-05-19T00:00:00+00:00",
                        "exact_property_match": True,
                        "role_specific_management_support": False,
                        "contradicts_current_claim": True,
                    },
                ],
            },
        ),
        lead_id="0ff794d3ba2d",
        frontier_limit=10,
        max_items=25,
        session=session,
        user=AuthUser(user_id="u1", email="reviewer@example.com"),
    )

    assert response["run_type"] == "truth_source_evidence_intake_batch_preview"
    assert response["dry_run"] is True
    assert response["mutations_planned"] == 0
    assert response["source_mode"] == "hpd_audit_output_recommended_scope_only"
    assert response["original_candidate_count"] == 2
    assert response["candidate_count"] == 1
    assert response["filtered_out_candidate_count"] == 1
    assert response["ready_for_manual_evidence_preview_count"] == 1
    assert response["recording_ready_count"] == 1
    assert response["new_supporting_source_ready_count"] == 1
    assert response["contradiction_candidate_count"] == 0
    assert response["blocked_count"] == 0
    assert response["recommended_recording_scope"]["recommended_count"] == 1
    assert response["recommended_recording_scope"]["recommended_relationships"][0]["address"] == "220 3 AVENUE"
    assert response["recommended_recording_scope"]["contradiction_review_count"] == 1
    assert response["recommended_recording_scope"]["explicit_approval_required"] is True
    assert response["recommended_recording_scope"]["filtered_view"] is True
    assert response["previews"][0]["manual_evidence_preview"]["allowed_execute"] is False
    assert response["worklist_context"]["request_count"] == 2
    assert session.rollback_count == 1


def test_claim_adjudication_update_plan_only_updates_safe_candidates():
    safe = adjudicate_fact_group({
        "subject_type": "lead",
        "subject_id": "lead-1",
        "predicate": "manages_building",
        "object_type": "building",
        "object_id": "1000000001",
        "normalized_value": "Lead 1 manages 1000000001",
        "claim_type": "building_management",
        "claim_ids": ["claim-a", "claim-b"],
        "evidence_ids": ["evidence-a", "evidence-b"],
        "supporting_sources": ["outreach_confirmed", "hpd_management_company"],
        "contradicting_sources": [],
        "supporting_evidence_count": 4,
        "contradicting_evidence_count": 0,
        "freshest_observed_freshness_days": 7,
        "oldest_observed_freshness_days": 30,
        "existing_belief_statuses": ["likely"],
        "max_confidence_score": 0.82,
    })
    blocked = adjudicate_fact_group({
        "subject_type": "lead",
        "subject_id": "lead-2",
        "predicate": "manages_building",
        "object_type": "building",
        "object_id": "1000000002",
        "normalized_value": "Lead 2 manages 1000000002",
        "claim_type": "building_management",
        "claim_ids": ["claim-c"],
        "evidence_ids": ["evidence-c"],
        "supporting_sources": ["building_management"],
        "contradicting_sources": [],
        "supporting_evidence_count": 1,
        "contradicting_evidence_count": 0,
        "freshest_observed_freshness_days": 12,
        "existing_belief_statuses": ["likely"],
        "max_confidence_score": 0.71,
    })

    plan = build_adjudication_update_plan([safe, blocked], run_id="truth-adjudication-test")

    assert safe["safe_to_mark_verified"] is True
    assert blocked["safe_to_mark_verified"] is False
    assert plan["safe_candidate_count"] == 1
    assert plan["claim_update_count"] == 2
    assert [update["claim_id"] for update in plan["claim_updates"]] == ["claim-a", "claim-b"]
    assert {update["belief_status"] for update in plan["claim_updates"]} == {"verified"}
    assert plan["skipped_candidate_count"] == 1


@pytest.mark.anyio
async def test_claim_adjudication_preview_is_read_only_and_summarizes_blockers():
    session = FakeAsyncSession([
        FakeExecuteResult(rows=[{
            "total_fact_group_count": 3,
            "zero_source_fact_group_count": 0,
            "single_source_fact_group_count": 2,
            "multi_source_fact_group_count": 1,
            "source_ready_fact_group_count": 1,
            "max_supporting_source_count": 2,
            "max_supporting_evidence_count": 3,
        }]),
        FakeExecuteResult(rows=[
            {"source_name": "building_management", "fact_group_count": 2},
            {"source_name": "hpd_contacts", "fact_group_count": 1},
        ]),
        FakeExecuteResult(rows=[{
            "building_management_id": 11,
            "lead_id": "lead-1",
            "bbl": "1000000001",
            "building_management_role": "agent",
            "building_management_observed_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
            "lead_normalized_name": "Example Property Management Inc",
            "lead_company_name": "Example Property Management Inc",
            "lead_agent_name": None,
            "lead_owner_name": None,
            "hpd_contact_id": 22,
            "registration_contact_id": "rc-1",
            "registration_id": "reg-1",
            "contact_type": "Agent",
            "description": None,
            "corporation_name": "Example Property Management LLC",
            "first_name": None,
            "last_name": None,
            "title": None,
            "business_address": "123 Main St",
            "business_city": "New York",
            "business_state": "NY",
            "business_zip": "10001",
            "hpd_contact_observed_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
        }]),
        FakeExecuteResult(rows=[{
            "building_management_id": 11,
            "lead_id": "lead-1",
            "bbl": "1000000001",
            "building_management_role": "agent",
            "building_management_observed_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
            "lead_normalized_name": "Example Property Management Inc",
            "lead_company_name": "Example Property Management Inc",
            "lead_agent_name": None,
            "lead_owner_name": None,
            "hpd_contact_id": 22,
            "registration_contact_id": "rc-1",
            "registration_id": "reg-1",
            "contact_type": "Agent",
            "description": None,
            "corporation_name": "Example Property Management LLC",
            "first_name": None,
            "last_name": None,
            "title": None,
            "business_address": "123 Main St",
            "business_city": "New York",
            "business_state": "NY",
            "business_zip": "10001",
            "hpd_contact_observed_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
        }]),
        FakeExecuteResult(rows=[{
            "id": 11,
            "lead_id": "lead-1",
            "bbl": "1000000001",
            "role": "agent",
            "registration_start": None,
            "registration_end": None,
            "observed_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
        }]),
        FakeExecuteResult(rows=[{
            "id": 22,
            "bbl": "1000000001",
            "registration_contact_id": "rc-1",
            "registration_id": "reg-1",
            "contact_type": "Agent",
            "description": None,
            "corporation_name": "Example Property Management LLC",
            "first_name": None,
            "last_name": None,
            "title": None,
            "business_address": "123 Main St",
            "business_city": "New York",
            "business_state": "NY",
            "business_zip": "10001",
            "observed_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
            "building_management_id": 11,
            "building_management_role": "agent",
            "lead_id": "lead-1",
            "lead_normalized_name": "Example Property Management Inc",
            "lead_company_name": "Example Property Management Inc",
            "lead_agent_name": None,
            "lead_owner_name": None,
        }]),
        FakeExecuteResult(rows=[{
            "claim_id": "stale-agent-claim",
            "subject_type": "lead",
            "subject_id": "lead-1",
            "predicate": "manages_building",
            "object_type": "building",
            "object_id": "1000000001",
            "normalized_value": "agent",
            "claim_type": "building_management",
            "belief_status": "likely",
            "confidence_score": 0.72,
            "actionability_level": "automated_enrichment",
            "observed_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
            "evidence_ids": ["evidence-stale"],
            "source_names": ["building_management"],
            "source_record_ids": ["building_management:11"],
            "source_roles": ["agent"],
        }]),
        FakeExecuteResult(rows=[
            {"scope": "all_current", "role": "agent", "count": 1},
            {"scope": "pilot_current", "role": "agent", "count": 1},
        ]),
        FakeExecuteResult(rows=[{
            "dos_cache_records": 0,
            "company_website_records": 0,
            "outreach_event_records": 0,
            "outreach_confirmed_manager_events": 0,
        }]),
        FakeExecuteResult(rows=[{
            "building_management_id": 11,
            "lead_id": "lead-1",
            "bbl": "1000000001",
            "building_management_role": "agent",
            "lead_normalized_name": "Example Property Management Inc",
            "lead_company_name": "Example Property Management Inc",
            "lead_agent_name": None,
            "lead_owner_name": None,
            "hpd_contact_id": 22,
            "contact_type": "Agent",
            "description": None,
            "corporation_name": "Example Property Management LLC",
            "first_name": None,
            "last_name": None,
            "title": None,
            "business_address": "123 Main St",
            "business_city": "New York",
            "business_state": "NY",
            "business_zip": "10001",
            "hpd_contact_observed_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
        }]),
        *[FakeExecuteResult(rows=[]) for _ in range(16)],
        *[FakeExecuteResult(rows=[]) for _ in range(4)],
        FakeExecuteResult(rows=[{
            "subject_type": "lead",
            "subject_id": "lead-1",
            "predicate": "manages_building",
            "object_type": "building",
            "object_id": "1000000001",
            "normalized_value": "Lead 1 manages 1000000001",
            "claim_type": "building_management",
            "claim_ids": ["claim-a"],
            "evidence_ids": ["evidence-a"],
            "supporting_sources": ["building_management"],
            "contradicting_sources": [],
            "supporting_evidence_count": 1,
            "contradicting_evidence_count": 0,
            "existing_belief_statuses": ["likely"],
            "max_confidence_score": 0.707,
            "min_confidence_score": 0.707,
            "freshest_observed_freshness_days": 7,
            "oldest_observed_freshness_days": 7,
            "last_claim_updated_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
        }]),
    ])

    preview = await load_claim_adjudication_preview(session, limit=5)

    assert preview["dry_run"] is True
    assert preview["mutations_planned"] == 0
    assert preview["fact_group_count"] == 1
    assert preview["verification_candidate_count"] == 0
    assert preview["blocker_counts"]["needs_independent_source"] == 1
    assert preview["ledger_source_overlap"]["dry_run"] is True
    assert preview["ledger_source_overlap"]["mutations_planned"] == 0
    assert preview["ledger_source_overlap"]["total_fact_group_count"] == 3
    assert preview["ledger_source_overlap"]["single_source_fact_group_count"] == 2
    assert preview["ledger_source_overlap"]["multi_source_fact_group_count"] == 1
    assert preview["ledger_source_overlap"]["source_ready_fact_group_count"] == 1
    assert preview["ledger_source_overlap"]["top_sources"][0] == {
        "source_name": "building_management",
        "fact_group_count": 2,
    }
    assert preview["role_source_overlap_pilot"]["dry_run"] is True
    assert preview["role_source_overlap_pilot"]["mutations_planned"] == 0
    assert preview["role_source_overlap_pilot"]["registered_agent_source_ready_if_materialized_count"] == 1
    assert preview["role_source_overlap_pilot"]["management_source_ready_if_materialized_count"] == 0
    assert "HARLEM PROPERTY MANAGEMENT" in preview["role_source_overlap_pilot"]["identity_policy"]["strict_key_example"]
    assert preview["scaled_role_source_overlap"]["dry_run"] is True
    assert preview["scaled_role_source_overlap"]["mutations_planned"] == 0
    assert preview["scaled_role_source_overlap"]["source_ready_if_materialized_count"] == 1
    assert preview["scaled_role_source_overlap"]["registered_agent_source_ready_if_materialized_count"] == 1
    assert preview["scaled_role_source_overlap"]["management_source_ready_if_materialized_count"] == 0
    assert preview["scaled_role_source_overlap"]["batches"][0]["lead_id"] == "lead-1"
    assert preview["role_overlap_activation_plan"]["dry_run"] is True
    assert preview["role_overlap_activation_plan"]["mutations_planned"] == 0
    assert preview["role_overlap_activation_plan"]["approval_required"] is True
    assert preview["role_overlap_activation_plan"]["predicted_if_approved"] == {
        "source_ready_fact_groups_added": 1,
        "management_source_ready_fact_groups_added": 0,
        "registered_agent_source_ready_fact_groups_added": 1,
        "stale_role_claims_to_supersede": 1,
    }
    assert preview["role_overlap_post_materialization_simulation"]["dry_run"] is True
    assert preview["role_overlap_post_materialization_simulation"]["mutations_planned"] == 0
    assert preview["role_overlap_post_materialization_simulation"]["source_ready_fact_group_count"] == 1
    assert preview["role_overlap_post_materialization_simulation"]["source_ready_count_by_predicate"] == {
        "registered_agent_for_building": 1
    }
    assert preview["role_claim_correction_preview"]["dry_run"] is True
    assert preview["role_claim_correction_preview"]["mutations_planned"] == 0
    assert preview["role_claim_correction_preview"]["sampled_stale_claim_count"] == 1
    assert preview["role_claim_correction_preview"]["samples"][0]["recommended_change"]["replacement_predicate"] == "registered_agent_for_building"
    assert preview["manager_source_bridge_preview"]["dry_run"] is True
    assert preview["manager_source_bridge_preview"]["registered_agent_bridge_count"] == 1
    assert preview["manager_source_bridge_preview"]["manager_source_ready_if_materialized_count"] == 0
    assert "no_local_company_website_evidence" in preview["manager_source_bridge_preview"]["blocking_reasons"]
    assert preview["manager_external_source_acquisition_preview"]["dry_run"] is True
    assert preview["manager_external_source_acquisition_preview"]["matched_evidence_candidate_count"] == 0
    assert preview["manager_external_source_acquisition_preview"]["unmatched_candidate_count"] == 30
    assert preview["manager_external_source_acquisition_preview"]["new_relationship_candidate_count"] == 0
    assert preview["source_coverage"]["single_source_fact_group_count"] == 1
    assert preview["source_coverage"]["multi_source_fact_group_count"] == 0
    assert preview["source_coverage"]["verification_blocker"] == "No sampled fact group has independent supporting sources."
    assert preview["source_coverage"]["top_sources"] == [{"source_name": "building_management", "fact_group_count": 1}]
    assert preview["verification_gap_plan"]["dry_run"] is True
    assert preview["verification_gap_plan"]["mutations_planned"] == 0
    assert preview["verification_gap_plan"]["proposal_count"] == 1
    gap_proposal = preview["verification_gap_plan"]["proposals"][0]
    assert gap_proposal["missing_source_count"] == 1
    assert gap_proposal["missing_evidence_count"] == 1
    assert gap_proposal["suggested_sources"][:2] == ["hpd_management_company", "company_website"]
    assert gap_proposal["manual_evidence_template"]["subject_type"] == "lead"
    assert gap_proposal["manual_evidence_template"]["predicate"] == "manages_building"
    assert gap_proposal["manual_evidence_template"]["source_type"] == "hpd_management_company"
    assert preview["verified_confidence_gap_plan"]["dry_run"] is True
    assert preview["verified_confidence_gap_plan"]["mutations_planned"] == 0
    assert preview["verified_confidence_gap_plan"]["proposal_count"] == 0
    assert preview["samples"][0]["fact_key"]["predicate"] == "manages_building"


@pytest.mark.anyio
async def test_ledger_source_overlap_reports_business_readiness_blocker():
    session = FakeAsyncSession([
        FakeExecuteResult(rows=[{
            "total_fact_group_count": 2063,
            "zero_source_fact_group_count": 0,
            "single_source_fact_group_count": 2063,
            "multi_source_fact_group_count": 0,
            "source_ready_fact_group_count": 0,
            "max_supporting_source_count": 1,
            "max_supporting_evidence_count": 1,
        }]),
        FakeExecuteResult(rows=[
            {"source_name": "hpd_contacts", "fact_group_count": 981},
            {"source_name": "hpd_complaints", "fact_group_count": 500},
        ]),
    ])

    summary = await load_ledger_source_overlap_summary(session)

    assert summary["dry_run"] is True
    assert summary["mutations_planned"] == 0
    assert summary["total_fact_group_count"] == 2063
    assert summary["single_source_fact_group_count"] == 2063
    assert summary["multi_source_fact_group_count"] == 0
    assert summary["source_ready_fact_group_count"] == 0
    assert summary["business_readiness_blocker"] == (
        "No current ledger fact groups have enough independent supporting sources and evidence for adjudication."
    )


@pytest.mark.anyio
async def test_role_source_overlap_pilot_uses_strict_identity_and_role_specific_claims():
    session = FakeAsyncSession([
        FakeExecuteResult(rows=[
            {
                "building_management_id": 11,
                "lead_id": "0ff794d3ba2d",
                "bbl": "1018250029",
                "building_management_role": "agent",
                "building_management_observed_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
                "lead_normalized_name": "Harlem Property Management Inc",
                "lead_company_name": "Harlem Property Management Inc",
                "lead_agent_name": None,
                "lead_owner_name": None,
                "hpd_contact_id": 21,
                "registration_contact_id": "rc-agent",
                "registration_id": "reg-agent",
                "contact_type": "Agent",
                "description": None,
                "corporation_name": "Harlem Property Management LLC",
                "first_name": None,
                "last_name": None,
                "title": None,
                "business_address": "1 Harlem Ave",
                "business_city": "New York",
                "business_state": "NY",
                "business_zip": "10027",
                "hpd_contact_observed_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
            },
            {
                "building_management_id": 12,
                "lead_id": "0ff794d3ba2d",
                "bbl": "1018250030",
                "building_management_role": "agent",
                "building_management_observed_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
                "lead_normalized_name": "Harlem Property Management Inc",
                "lead_company_name": "Harlem Property Management Inc",
                "lead_agent_name": None,
                "lead_owner_name": None,
                "hpd_contact_id": 22,
                "registration_contact_id": "rc-realty",
                "registration_id": "reg-realty",
                "contact_type": "Agent",
                "description": None,
                "corporation_name": "Harlem Realty LLC",
                "first_name": None,
                "last_name": None,
                "title": None,
                "business_address": "2 Harlem Ave",
                "business_city": "New York",
                "business_state": "NY",
                "business_zip": "10027",
                "hpd_contact_observed_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
            },
        ]),
    ])

    preview = await load_role_source_overlap_pilot(session, limit=20)

    assert preview["dry_run"] is True
    assert preview["scope_relationship_count"] == 2
    assert preview["sampled_relationship_count"] == 2
    assert preview["source_ready_if_materialized_count"] == 1
    assert preview["registered_agent_source_ready_if_materialized_count"] == 1
    assert preview["management_source_ready_if_materialized_count"] == 0
    ready = next(sample for sample in preview["samples"] if sample["source_ready_if_materialized"])
    blocked = next(sample for sample in preview["samples"] if not sample["source_ready_if_materialized"])
    assert ready["fact_key"]["predicate"] == "registered_agent_for_building"
    assert ready["supporting_sources_if_materialized"] == ["building_management", "hpd_contacts"]
    assert "not treat as operating-manager proof" in ready["safe_action"]
    assert blocked["blocked_contact_count"] == 1
    assert preview["identity_policy"]["strict_key_example"] == "HARLEM PROPERTY MANAGEMENT"
    assert preview["identity_policy"]["broad_dedupe_key_example"] == "HARLEM"


@pytest.mark.anyio
async def test_scaled_role_source_overlap_preview_ranks_strict_batches():
    session = FakeAsyncSession([
        FakeExecuteResult(rows=[
            {
                "building_management_id": 11,
                "lead_id": "lead-agent",
                "bbl": "1018250029",
                "building_management_role": "agent",
                "building_management_observed_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
                "lead_normalized_name": "Example Property Management Inc",
                "lead_company_name": "Example Property Management Inc",
                "lead_agent_name": None,
                "lead_owner_name": None,
                "hpd_contact_id": 22,
                "registration_contact_id": "rc-agent",
                "registration_id": "reg-agent",
                "contact_type": "Agent",
                "description": None,
                "corporation_name": "Example Property Management LLC",
                "first_name": None,
                "last_name": None,
                "title": None,
                "business_address": "123 Main St",
                "business_city": "New York",
                "business_state": "NY",
                "business_zip": "10001",
                "hpd_contact_observed_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
            },
            {
                "building_management_id": 12,
                "lead_id": "lead-manager",
                "bbl": "1018250030",
                "building_management_role": "manager",
                "building_management_observed_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
                "lead_normalized_name": "Real Manager LLC",
                "lead_company_name": "Real Manager LLC",
                "lead_agent_name": None,
                "lead_owner_name": None,
                "hpd_contact_id": 23,
                "registration_contact_id": "rc-manager",
                "registration_id": "reg-manager",
                "contact_type": "ManagementCompany",
                "description": None,
                "corporation_name": "Real Manager Inc",
                "first_name": None,
                "last_name": None,
                "title": None,
                "business_address": "456 Main St",
                "business_city": "New York",
                "business_state": "NY",
                "business_zip": "10002",
                "hpd_contact_observed_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
            },
            {
                "building_management_id": 13,
                "lead_id": "lead-loose",
                "bbl": "1018250031",
                "building_management_role": "manager",
                "building_management_observed_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
                "lead_normalized_name": "Loose Harlem Property Management Inc",
                "lead_company_name": "Loose Harlem Property Management Inc",
                "lead_agent_name": None,
                "lead_owner_name": None,
                "hpd_contact_id": 24,
                "registration_contact_id": "rc-loose",
                "registration_id": "reg-loose",
                "contact_type": "ManagementCompany",
                "description": None,
                "corporation_name": "Loose Harlem LLC",
                "first_name": None,
                "last_name": None,
                "title": None,
                "business_address": "789 Main St",
                "business_city": "New York",
                "business_state": "NY",
                "business_zip": "10003",
                "hpd_contact_observed_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
            },
        ]),
    ])

    preview = await load_scaled_role_source_overlap_preview(session, relationship_limit=100, batch_limit=5)

    assert preview["dry_run"] is True
    assert preview["mutations_planned"] == 0
    assert preview["scanned_relationship_count"] == 3
    assert preview["source_ready_if_materialized_count"] == 2
    assert preview["registered_agent_source_ready_if_materialized_count"] == 1
    assert preview["management_source_ready_if_materialized_count"] == 1
    assert preview["claim_count_by_predicate_if_materialized"]["registered_agent_for_building"] == 1
    assert preview["claim_count_by_predicate_if_materialized"]["manages_building"] == 2
    assert preview["source_ready_batch_count"] == 2
    manager_batch = next(batch for batch in preview["batches"] if batch["lead_id"] == "lead-manager")
    loose_batch = next(batch for batch in preview["batches"] if batch["lead_id"] == "lead-loose")
    assert manager_batch["management_source_ready_if_materialized_count"] == 1
    assert manager_batch["samples"][0]["supporting_sources_if_materialized"] == ["building_management", "hpd_contacts"]
    assert loose_batch["source_ready_if_materialized_count"] == 0
    assert loose_batch["samples"][0]["blocked_contact_count"] == 1


@pytest.mark.anyio
async def test_manager_source_bridge_preview_explains_missing_manager_evidence():
    session = FakeAsyncSession([
        FakeExecuteResult(rows=[
            {"scope": "all_current", "role": "agent", "count": 82},
            {"scope": "pilot_current", "role": "agent", "count": 82},
        ]),
        FakeExecuteResult(rows=[{
            "dos_cache_records": 0,
            "company_website_records": 0,
            "outreach_event_records": 0,
            "outreach_confirmed_manager_events": 0,
        }]),
        FakeExecuteResult(rows=[
            {
                "building_management_id": 11,
                "lead_id": "0ff794d3ba2d",
                "bbl": "1018250029",
                "building_management_role": "agent",
                "lead_normalized_name": "Harlem Property Management Inc",
                "lead_company_name": "Harlem Property Management Inc",
                "lead_agent_name": None,
                "lead_owner_name": None,
                "hpd_contact_id": 22,
                "contact_type": "Agent",
                "description": None,
                "corporation_name": "Harlem Property Management LLC",
                "first_name": None,
                "last_name": None,
                "title": None,
                "business_address": "270 Lenox Ave",
                "business_city": "New York",
                "business_state": "NY",
                "business_zip": "10027",
                "hpd_contact_observed_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
            },
            {
                "building_management_id": 11,
                "lead_id": "0ff794d3ba2d",
                "bbl": "1018250029",
                "building_management_role": "agent",
                "lead_normalized_name": "Harlem Property Management Inc",
                "lead_company_name": "Harlem Property Management Inc",
                "lead_agent_name": None,
                "lead_owner_name": None,
                "hpd_contact_id": 23,
                "contact_type": "SiteManager",
                "description": None,
                "corporation_name": None,
                "first_name": "James",
                "last_name": "Simari",
                "title": None,
                "business_address": "",
                "business_city": None,
                "business_state": None,
                "business_zip": None,
                "hpd_contact_observed_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
            },
        ]),
    ])

    preview = await load_manager_source_bridge_preview(session, lead_id="0ff794d3ba2d")

    assert preview["dry_run"] is True
    assert preview["mutations_planned"] == 0
    assert preview["relationship_count"] == 1
    assert preview["role_counts"]["pilot_current"] == {"agent": 82}
    assert preview["registered_agent_bridge_count"] == 1
    assert preview["hpd_site_manager_row_count"] == 1
    assert preview["current_manager_role_relationship_count"] == 0
    assert preview["hpd_management_company_strict_match_count"] == 0
    assert preview["manager_source_ready_if_materialized_count"] == 0
    assert preview["source_counts"]["dos_cache_records"] == 0
    assert preview["source_counts"]["company_website_records"] == 0
    assert preview["source_counts"]["outreach_confirmed_manager_events"] == 0
    assert "current_building_management_rows_are_not_manager_role" in preview["blocking_reasons"]
    assert "no_strict_hpd_management_company_matches" in preview["blocking_reasons"]
    assert "registered-agent overlap" in preview["business_readiness_note"]


@pytest.mark.anyio
async def test_manager_external_source_acquisition_preview_matches_public_sources_to_local_bbls():
    def manager_row(bbl: str, address: str, zip_code: str, unit_count: int) -> dict[str, Any]:
        return {
            "bbl": bbl,
            "address": address,
            "borough": "MANHATTAN",
            "zip_code": zip_code,
            "unit_count": unit_count,
            "lead_id": "0ff794d3ba2d",
            "building_management_role": "agent",
            "company_name": "Harlem Property Management",
            "normalized_name": "HARLEM PROPERTY MANAGEMENT",
        }

    rows_by_address = {
        "11 ST NICHOLAS AVENUE": manager_row("1018210025", "11 ST NICHOLAS AVENUE", "10026", 11),
        "141 WEST 123 STREET": manager_row("1019080014", "141 WEST 123 STREET", "10027", 3),
        "2257 ADAM C POWELL BOULEVARD": manager_row(
            "1019177501", "2257 ADAM C POWELL BOULEVARD", "10027", 10
        ),
        "269 GREENWICH STREET": {
            "bbl": "1001327501",
            "address": "269 GREENWICH STREET",
            "borough": "MANHATTAN",
            "zip_code": "10007",
            "unit_count": 128,
            "lead_id": None,
            "building_management_role": None,
            "company_name": None,
            "normalized_name": None,
        },
        "204 WEST 140 STREET": manager_row("1020257501", "204 WEST 140 STREET", "10030", 29),
        "306 WEST 115 STREET": manager_row("1018487504", "306 WEST 115 STREET", "10026", 4),
        "324 EAST 112 STREET": manager_row("1016837501", "324 EAST 112 STREET", "10029", 20),
        "330 WEST 145 STREET": manager_row("1020517501", "330 WEST 145 STREET", "10039", 77),
        "342 WEST 56 STREET": manager_row("1010460054", "342 WEST 56 STREET", "10019", 42),
        "345 LENOX AVENUE": manager_row("1019127501", "345 LENOX AVENUE", "10027", 4),
        "36 WEST 138 STREET": manager_row("1017350053", "36 WEST 138 STREET", "10037", 35),
        "402 WEST 153 STREET": {
            "bbl": "1020670047",
            "address": "402 WEST 153 STREET",
            "borough": "MANHATTAN",
            "zip_code": "10031",
            "unit_count": 11,
            "lead_id": None,
            "building_management_role": None,
            "company_name": None,
            "normalized_name": None,
        },
        "42 WEST 120 STREET": manager_row("1017187501", "42 WEST 120 STREET", "10027", 28),
        "506 EAST 119 STREET": manager_row("1018157501", "506 EAST 119 STREET", "10035", 4),
        "555 LENOX AVENUE": manager_row("1020077501", "555 LENOX AVENUE", "10037", 32),
        "61 LENOX AVENUE": manager_row("1018237502", "61 LENOX AVENUE", "10026", 15),
    }
    query_addresses = [
        "11 ST NICHOLAS AVENUE",
        "141 WEST 123 STREET",
        "204 WEST 140 STREET",
        "2257 ADAM C POWELL BOULEVARD",
        "269 GREENWICH STREET",
        "306 WEST 115 STREET",
        "324 EAST 112 STREET",
        "330 WEST 145 STREET",
        "342 WEST 56 STREET",
        "345 LENOX AVENUE",
        "36 WEST 138 STREET",
        "402 WEST 153 STREET",
        "42 WEST 120 STREET",
        "506 EAST 119 STREET",
        "555 LENOX AVENUE",
        "61 LENOX AVENUE",
    ]
    session = FakeAsyncSession([
        FakeExecuteResult(rows=[rows_by_address[address]] if address in rows_by_address else [])
        for address in query_addresses
    ])

    preview = await load_manager_external_source_acquisition_preview(session, lead_id="0ff794d3ba2d")

    assert preview["dry_run"] is True
    assert preview["mutations_planned"] == 0
    assert preview["candidate_source_count"] == 30
    assert preview["matched_evidence_candidate_count"] == 49
    assert preview["clean_exact_claim_count"] == 7
    assert preview["claim_group_count"] == 14
    assert preview["source_ready_if_recorded_count"] == 14
    assert preview["independent_source_ready_if_recorded_count"] == 14
    assert preview["strict_manager_source_ready_if_recorded_count"] == 13
    assert preview["excluded_manager_proof_source_families"] == [
        "hpd_registration_derived",
        "nyc_dof_billing_record",
    ]
    assert preview["unmatched_candidate_count"] == 3
    assert preview["new_relationship_candidate_count"] == 3
    new_relationships_by_candidate = {
        item["candidate_id"]: item for item in preview["new_relationship_candidates"]
    }
    assert new_relationships_by_candidate["ny-dps-verizon-402-w-153-petition"][
        "local_building_match"
    ]["bbl"] == "1020670047"
    assert new_relationships_by_candidate["ny-dps-verizon-402-w-153-petition"][
        "relationship_claim_preview"
    ]["object_id"] == "1020670047"
    assert new_relationships_by_candidate["ny-dps-verizon-402-w-153-petition"][
        "current_relationship_state"
    ] == {
        "current_building_management_relationship_count": 0,
        "current_truth_claim_count": 0,
        "counts_as_current_ledger_overlap": False,
        "relationship_review_required": True,
    }
    greenwich_dof = new_relationships_by_candidate["nyc-dof-275-greenwich-hpm-billing-record"]
    assert greenwich_dof["local_building_match"]["bbl"] == "1001327501"
    assert greenwich_dof["relationship_claim_preview"]["object_id"] == "1001327501"
    assert greenwich_dof["relationship_claim_preview"]["raw_payload"]["source_family"] == (
        "nyc_dof_billing_record"
    )
    greenwich_site = new_relationships_by_candidate["hpm-site-review-275-greenwich-management-takeover"]
    assert greenwich_site["local_building_match"]["address"] == "269 GREENWICH STREET"
    assert greenwich_site["relationship_claim_preview"]["raw_payload"]["source_family"] == "company_website"
    assert "possible new relationship claim" in greenwich_site["safe_action"]
    assert "120-day verified threshold" in preview["policy"]["freshness_warning"]
    assert preview["manual_evidence_batch_preview"]["dry_run"] is True
    assert preview["manual_evidence_batch_preview"]["allowed_execute"] is False
    assert preview["manual_evidence_batch_preview"]["template_count"] == 48
    assert preview["manual_evidence_batch_preview"]["claim_group_count"] == 14
    assert preview["manual_evidence_batch_preview"]["planned_upsert_count"] == 144
    assert preview["manual_evidence_batch_preview"]["excluded_address_review_candidate_count"] == 1
    assert preview["manual_evidence_batch_preview"]["recommended_strict_manager_proof_batch"]["template_count"] == 46
    assert preview["manual_evidence_batch_preview"]["recommended_strict_manager_proof_batch"]["claim_group_count"] == 13
    assert preview["manual_evidence_batch_preview"]["recommended_strict_manager_proof_batch"]["planned_upsert_count"] == 138
    assert preview["manual_evidence_batch_preview"]["recommended_strict_manager_proof_batch"][
        "manager_proof_source_families"
    ] == ["external_web_profile", "first_party_operator_document", "litigation_records", "ny_dps_order_entry", "real_estate_listing"]
    assert "hpd_registration_derived" in preview["manual_evidence_batch_preview"][
        "recommended_strict_manager_proof_batch"
    ]["source_families"]
    rollback_preview = preview["manual_evidence_batch_preview"]["recommended_strict_manager_proof_batch"][
        "rollback_preview"
    ]
    assert rollback_preview["estimated_claim_count"] == 46
    assert rollback_preview["estimated_evidence_count"] == 46
    assert rollback_preview["estimated_confidence_snapshot_count"] == 46
    assert rollback_preview["estimated_manifest_entry_count"] == 138
    assert "--strict-manager-proof-only" in preview["manual_evidence_batch_preview"]["recommended_strict_manager_proof_batch"]["command"]
    assert preview["manual_evidence_batch_preview"]["source_names"] == [
        "hpm_revenue_by_property_summary",
        "justia",
        "mystatemls",
        "ny_dps_order_entry",
        "openigloo",
        "renthistory",
        "renthop",
        "verizon_order_entry_petition",
        "zillow",
    ]
    assert preview["post_recording_simulation"]["dry_run"] is True
    assert preview["post_recording_simulation"]["template_count"] == 48
    assert preview["post_recording_simulation"]["simulated_fact_group_count"] == 14
    assert preview["post_recording_simulation"]["multi_source_fact_group_count"] == 14
    assert preview["post_recording_simulation"]["source_ready_fact_group_count"] == 14
    assert preview["post_recording_simulation"]["independent_source_ready_fact_group_count"] == 14
    assert preview["post_recording_simulation"]["strict_manager_source_ready_fact_group_count"] == 13
    assert preview["post_recording_simulation"]["source_ready_count_by_predicate"] == {"manages_building": 14}
    assert preview["post_recording_simulation"]["safe_to_mark_verified_count"] == 0
    assert "confidence_below_verified_threshold" in preview["post_recording_simulation"]["blocker_counts"]
    assert preview["next_source_batches"]["dry_run"] is True
    assert preview["next_source_batches"]["candidate_count"] == 1
    assert preview["next_source_batches"]["suggested_source_family_counts"]["first_party_operator_document"] == 1
    assert preview["next_source_batches"]["suggested_source_family_counts"]["ny_dps_order_entry"] == 1
    next_batch_by_bbl = {item["bbl"]: item for item in preview["next_source_batches"]["proposals"]}
    assert set(next_batch_by_bbl) == {"1019080014"}
    assert next_batch_by_bbl["1019080014"]["existing_manager_proof_source_families"] == ["external_web_profile"]
    assert "first_party_operator_document" in next_batch_by_bbl["1019080014"]["suggested_source_families"]
    assert "ny_dps_order_entry" in next_batch_by_bbl["1019080014"]["suggested_source_families"]
    assert next_batch_by_bbl["1019080014"]["search_queries"][0] == (
        '"Harlem Property Management" "141 West 123 Street"'
    )
    assert any(
        target["source_family"] == "ny_dps_order_entry"
        and "141 WEST 123 STREET" in target["evidence_needed"]
        for target in next_batch_by_bbl["1019080014"]["source_targets"]
    )
    assert "service-of-process" in preview["next_source_batches"]["source_boundary_notes"][1]
    assert "First-party HPM operating documents" in preview["next_source_batches"]["source_boundary_notes"][3]
    reviewed_findings = preview["next_source_batches"]["reviewed_source_findings"]
    assert {finding["source_family"] for finding in reviewed_findings} >= {
        "ny_dps_order_entry",
        "company_website",
        "openigloo",
        "renthistory",
        "first_party_operator_document",
        "third_party_company_profile",
        "local_hpd_contact_role_audit",
        "live_hpd_open_data_role_audit_2026_05_15",
        "real_estate_listing",
        "ny_dos_or_legal_mailing",
        "litigation_records",
        "public_web_search_followup",
        "public_web_search_followup_hpm_batch_2",
        "public_web_search_followup_hpm_batch_3",
        "public_web_search_live_refresh_hpm_2026_05_15",
        "public_web_search_followup_hpm_batch_4_2026_05_15",
        "public_web_search_followup_hpm_batch_5_2026_05_15",
        "public_web_search_followup_hpm_batch_6_2026_05_15",
        "public_web_search_followup_hpm_batch_7_2026_05_15",
        "public_web_search_followup_hpm_batch_10_2026_05_15",
        "public_web_search_followup_hpm_batch_11_2026_05_15",
        "public_web_search_followup_hpm_batch_12_2026_05_15",
        "public_web_search_followup_hpm_batch_13_2026_05_15",
        "public_web_search_followup_hpm_batch_14_2026_05_15",
        "public_web_search_followup_hpm_batch_15_2026_05_15",
        "public_web_search_followup_hpm_batch_16_2026_05_15",
        "public_web_search_followup_hpm_batch_17_2026_05_15",
        "public_web_search_followup_hpm_batch_18_2026_05_15",
        "public_web_search_followup_hpm_batch_20_2026_05_16",
        "public_web_search_followup_hpm_new_relationship_275_greenwich_2026_05_15",
        "live_hpd_role_audit_hpm_new_relationship_402_w_153_2026_05_15",
        "site_native_search_hpm_2026_05_15",
        "operator_document_raw_xlsx_followup_hpm_2026_05_19",
        "operator_document_raw_drive_fetch_hpm_2026_05_19_phase4",
        "operator_document_native_sheet_followup_hpm_2026_05_19",
    }
    assert reviewed_findings[0]["source_urls"]
    assert reviewed_findings[1]["source_urls"] == ["https://harlempm.com/"]
    assert "do not list the exact pilot buildings" in reviewed_findings[1]["finding"]
    assert "no exact 330 WEST 145 STREET match" in reviewed_findings[2]["finding"]
    assert "same real-estate-listing family" in reviewed_findings[3]["qualification"]
    assert "excluded from strict manager-proof" in reviewed_findings[4]["qualification"]
    assert any(
        finding["source_family"] == "first_party_operator_document"
        and "Revenue by Property - Summary" in finding["finding"]
        and "Row-level revenue amounts are intentionally not copied" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "third_party_company_profile"
        and "NYS Part 36 court-appointed property manager" in finding["finding"]
        and "do not name the exact pilot buildings" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_hpm_batch_20_2026_05_16"
        and "141 West 123 Street" in finding["finding"]
        and "141 WEST 123 STREET remains the HPM next-source seed" in finding["qualification"]
        and "HPD-registration-derived broad context" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "local_hpd_contact_role_audit"
        and "no ManagementCompany contact rows" in finding["finding"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "live_hpd_open_data_role_audit_2026_05_15"
        and "Do not count HPD Agent rows" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "live_hpd_property_managers_first_step_view_2026_06_01"
        and "Property Managers-1st Step" in finding["finding"]
        and "RegistrationID" in finding["finding"]
        and "does not expose manager" in finding["finding"]
        and "reviewed source-acquisition boundary context only" in finding["qualification"]
        and "can only help locate registration rows" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "litigation_records"
        and "do not independently state" in finding["finding"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup"
        and "wrong-property context" in finding["finding"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_hpm_batch_2"
        and "did not add a second independent manager-proof family" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_hpm_batch_3"
        and "found no new qualifying strict manager-proof source family" in finding["finding"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_live_refresh_hpm_2026_05_15"
        and "Zillow corroborates that bridge inside the same listing family" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_hpm_batch_4_2026_05_15"
        and "does not change source-ready counts" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_hpm_batch_5_2026_05_15"
        and "PropertyShark" in finding["finding"]
        and "Gated contact placeholders" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_hpm_batch_6_2026_05_15"
        and "Apartments.com 555 Lenox" in finding["finding"]
        and "property-only listings" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_hpm_batch_7_2026_05_15"
        and "found no new strict independent manager-proof source" in finding["finding"]
        and "did not change source-ready counts" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_hpm_batch_9_2026_05_15"
        and "Apartments.com 555 Lenox pages" in finding["finding"]
        and "Do not add a strict evidence template" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_hpm_batch_10_2026_05_15"
        and "again found no new strict manager-proof source" in finding["finding"]
        and "adds no source template" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_hpm_batch_11_2026_05_15"
        and "No new NY DPS/PSC" in finding["finding"]
        and "does not change source-ready counts" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_hpm_batch_12_2026_05_15"
        and "PropertyShark 306 West 115" in finding["finding"]
        and "adds no source template" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_hpm_batch_13_2026_05_15"
        and "targeted NY DPS/PSC document searches" in finding["finding"]
        and "does not change source-ready counts" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_hpm_batch_14_2026_05_15"
        and "latest exact-source pass" in finding["finding"]
        and "does not change source-ready counts" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_hpm_batch_15_2026_05_15"
        and "11-15 St. Nicholas refresh" in finding["finding"]
        and "Homes.com is brokerage/listing context" in finding["qualification"]
        and "company/leadership profiles still do not name exact pilot buildings" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_hpm_batch_16_2026_05_15"
        and "202-204 West 140 exact-source refresh" in finding["finding"]
        and "exact 204 W 140 ST" in finding["finding"]
        and "certified-mail surfaces" in finding["finding"]
        and "same ny_dps_order_entry source family" in finding["qualification"]
        and "still needs a non-DPS exact-property manager source" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_hpm_batch_17_2026_05_15"
        and "FirstService Residential press release" in finding["finding"]
        and "stale conflicting-manager context" in finding["qualification"]
        and "does not create strict HPM manager-proof overlap" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_hpm_batch_18_2026_05_15"
        and "Building Team source pass" in finding["finding"]
        and "Apartments.com 555 Lenox pages" in finding["finding"]
        and "Building-profile pages are usable only" in finding["qualification"]
        and "does not change source-ready counts" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_hpm_batch_19_2026_05_15"
        and "post-approval-boundary exact search" in finding["finding"]
        and "306 West 115 Street" in finding["finding"]
        and "OpenIgloo remains the existing" in finding["qualification"]
        and "does not change source-ready counts" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_hpm_new_relationship_275_greenwich_2026_05_15"
        and "NYC Finance billing" in finding["finding"]
        and "C/O MILFORD MGMT" in finding["finding"]
        and "review-only relationship-acquisition context" in finding["qualification"]
        and "not current-ledger source overlap" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "live_hpd_role_audit_hpm_new_relationship_402_w_153_2026_05_15"
        and "Empire State Property Management as Agent" in finding["finding"]
        and "ROCKBRIDGE PM" in finding["finding"]
        and "no ManagementCompany row" in finding["finding"]
        and "does not support HPM" in finding["qualification"]
        and "HPD-registration-derived context" in finding["qualification"]
        and "contradiction-review context" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "site_native_search_hpm_2026_05_15"
        and "HPM's own public search endpoint" in finding["finding"]
        and "553-559 Lenox" in finding["finding"]
        and "320-338 West 145" in finding["finding"]
        and "Abbreviated 141 W 123" in finding["finding"]
        and "does not change source-ready counts" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "operator_document_raw_xlsx_followup_hpm_2026_05_19"
        and not finding["source_urls"]
        and "Revenue by Property - Summary" in finding["finding"]
        and "141 WEST 123 STREET" in finding["finding"]
        and "private Drive URL" in finding["qualification"]
        and "does not change source-ready or verified counts" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "operator_document_raw_drive_fetch_hpm_2026_05_19_phase4"
        and not finding["source_urls"]
        and "Revenue by Property - Summary.xlsx" in finding["finding"]
        and "141 WEST 123 STREET" in finding["finding"]
        and "private Drive URL" in finding["qualification"]
        and "does not change source-ready or verified counts" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "operator_document_native_sheet_followup_hpm_2026_05_19"
        and not finding["source_urls"]
        and "native Google Sheet follow-up" in finding["finding"]
        and "342 West 56 Street" in finding["finding"]
        and "141 WEST 123 STREET" in finding["finding"]
        and "no private Drive URL" in finding["qualification"]
        and "does not change source-ready or verified counts" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "operator_document_exact_drive_search_hpm_2026_05_19_phase5"
        and not finding["source_urls"]
        and "Revenue by Property - Summary" in finding["finding"]
        and "141 WEST 123 STREET" in finding["finding"]
        and "no private Drive URL" in finding["qualification"]
        and "does not change source-ready or verified counts" in finding["qualification"]
        for finding in reviewed_findings
    )
    assert any(
        finding["source_family"] == "ny_dps_order_entry"
        and "204 WEST 140 STREET" in finding["finding"]
        and "202 WEST 140 STREET" in finding["qualification"]
        for finding in reviewed_findings
    )

    by_bbl = {group["fact_key"]["object_id"]: group for group in preview["claim_groups"]}
    assert by_bbl["1018210025"]["address"] == "11 ST NICHOLAS AVENUE"
    assert by_bbl["1018210025"]["source_ready_if_recorded"] is True
    assert by_bbl["1018210025"]["strict_manager_source_ready_if_recorded"] is True
    assert by_bbl["1018210025"]["manager_proof_source_families_if_recorded"] == [
        "litigation_records",
        "first_party_operator_document",
    ]
    assert by_bbl["1018210025"]["supporting_sources_if_recorded"] == [
        "renthistory",
        "justia",
        "hpm_revenue_by_property_summary",
    ]
    assert any(
        template["raw_payload"]["identity_evidence_urls"]
        for template in by_bbl["1018210025"]["manual_evidence_templates"]
    )
    assert by_bbl["1016837501"]["address"] == "324 EAST 112 STREET"
    assert by_bbl["1019177501"]["address"] == "2257 ADAM C POWELL BOULEVARD"
    assert by_bbl["1019177501"]["strict_manager_source_ready_if_recorded"] is True
    assert "mystatemls" in by_bbl["1019177501"]["supporting_sources_if_recorded"]
    assert by_bbl["1016837501"]["building_management_role"] == "agent"
    assert "verizon_order_entry_petition" in by_bbl["1016837501"]["supporting_sources_if_recorded"]
    assert "openigloo" in by_bbl["1019080014"]["supporting_sources_if_recorded"]
    assert by_bbl["1019080014"]["strict_manager_source_ready_if_recorded"] is False
    assert "renthistory" in by_bbl["1020517501"]["supporting_sources_if_recorded"]
    assert by_bbl["1020517501"]["independent_source_ready_if_recorded"] is True
    assert by_bbl["1020517501"]["strict_manager_source_ready_if_recorded"] is True
    assert by_bbl["1020257501"]["address"] == "204 WEST 140 STREET"
    assert by_bbl["1020257501"]["source_ready_if_recorded"] is True
    assert by_bbl["1020257501"]["strict_manager_source_ready_if_recorded"] is True
    assert by_bbl["1020257501"]["supporting_sources_if_recorded"] == [
        "renthistory",
        "ny_dps_order_entry",
        "hpm_revenue_by_property_summary",
    ]
    assert by_bbl["1020257501"]["manager_proof_source_families_if_recorded"] == [
        "ny_dps_order_entry",
        "first_party_operator_document",
    ]
    assert "renthop" in by_bbl["1010460054"]["supporting_sources_if_recorded"]
    assert "zillow" in by_bbl["1010460054"]["supporting_sources_if_recorded"]
    assert by_bbl["1010460054"]["strict_manager_source_ready_if_recorded"] is True
    assert by_bbl["1016837501"]["strict_manager_source_ready_if_recorded"] is True
    assert by_bbl["1017350053"]["source_ready_if_recorded"] is True
    assert by_bbl["1017350053"]["manual_evidence_templates"][0]["predicate"] == "manages_building"
    assert by_bbl["1017350053"]["manual_evidence_templates"][0]["source_name"] == "ny_dps_order_entry"
    revenue_template = next(
        template
        for template in by_bbl["1016837501"]["manual_evidence_templates"]
        if template["source_name"] == "hpm_revenue_by_property_summary"
    )
    assert revenue_template["source_url"] is None
    assert revenue_template["raw_payload"]["source_document_title"] == "Revenue by Property - Summary"
    assert revenue_template["raw_payload"]["source_document_row_number"] == 48

    renthistory_candidate = next(
        candidate for candidate in preview["evidence_candidates"] if candidate["source_name"] == "renthistory"
    )
    assert renthistory_candidate["independence_warning"] == "Treat as HPD-derived context, not independent manager proof."


@pytest.mark.anyio
async def test_operator_confirmed_management_preview_matches_buildings_and_keeps_strict_boundaries():
    building_rows = [
        {
            "bbl": "1008747504",
            "address": "220 3 AVENUE",
            "borough": "MANHATTAN",
            "zip_code": "10003",
            "unit_count": 16,
            "building_class": "RM",
        },
        {
            "bbl": "1005297507",
            "address": "57 BOND STREET",
            "borough": "MANHATTAN",
            "zip_code": "10012",
            "unit_count": 10,
            "building_class": "RM",
        },
        {
            "bbl": "1008170057",
            "address": "4 WEST 16 STREET",
            "borough": "MANHATTAN",
            "zip_code": "10011",
            "unit_count": 16,
            "building_class": "D0",
        },
        {
            "bbl": "3010680037",
            "address": "9 PROSPECT PARK WEST",
            "borough": "BROOKLYN",
            "zip_code": "11215",
            "unit_count": 41,
            "building_class": "D4",
        },
    ]
    lead_rows = [
        {
            "lead_id": "d11246cb2dae",
            "company_name": "Daisy Management",
            "normalized_name": "DAISY MANAGEMENT",
            "entity_type": "corporation",
            "portfolio_size": 187,
            "total_units": 4331,
        },
        {
            "lead_id": "56a71624c6c0",
            "company_name": "MD Squared Property Group",
            "normalized_name": "MD SQUARED PROPERTY GROUP",
            "entity_type": "corporation",
            "portfolio_size": 110,
            "total_units": 2863,
        },
    ]
    session = FakeAsyncSession([
        FakeExecuteResult(rows=building_rows),
        FakeExecuteResult(rows=lead_rows),
        FakeExecuteResult(rows=[]),
        FakeExecuteResult(rows=[]),
    ])

    preview = await load_operator_confirmed_management_preview(session)

    assert preview["dry_run"] is True
    assert preview["mutations_planned"] == 0
    assert preview["candidate_count"] == 4
    assert preview["matched_candidate_count"] == 4
    assert preview["unmatched_candidate_count"] == 0
    assert preview["new_relationship_candidate_count"] == 4
    assert preview["conflict_candidate_count"] == 0
    assert preview["operator_confirmation_template_count"] == 4
    assert preview["second_source_template_count"] == 6
    assert preview["manual_evidence_template_count"] == 10
    assert preview["contradiction_template_count"] == 0
    assert preview["planned_upsert_count"] == 30
    assert preview["source_ready_if_recorded_count"] == 4
    assert preview["independent_source_ready_if_recorded_count"] == 4
    assert preview["strict_manager_source_ready_if_recorded_count"] == 2
    assert preview["verified_safe_if_recorded_count"] == 0
    assert preview["post_recording_simulation"]["multi_source_fact_group_count"] == 4
    assert preview["post_recording_simulation"]["source_ready_fact_group_count"] == 4
    assert preview["post_recording_simulation"]["strict_manager_source_ready_fact_group_count"] == 2
    assert preview["post_recording_simulation"]["safe_to_mark_verified_count"] == 0
    assert "single source is not verified" in preview["policy"]["single_source_policy"]
    assert "RentHistory/HPD-registration-derived" in preview["policy"]["manager_proof_policy"]
    by_address = {candidate["user_address"]: candidate for candidate in preview["candidates"]}
    assert by_address["220 Third Ave"]["matched_building"]["bbl"] == "1008747504"
    assert by_address["57 Bond St"]["matched_lead"]["lead_id"] == "56a71624c6c0"
    assert by_address["9 Prospect Park W"]["matched_lead"]["lead_id"] == "d11246cb2dae"
    assert by_address["9 Prospect Park W"]["source_ready_if_recorded"] is True
    assert by_address["9 Prospect Park W"]["strict_manager_source_ready_if_recorded"] is True
    assert by_address["9 Prospect Park W"]["supporting_sources_if_recorded"] == [
        "outreach_confirmed",
        "homes",
        "redfin",
    ]
    assert by_address["9 Prospect Park W"]["manager_proof_source_families_if_recorded"] == [
        "operator_confirmed",
        "real_estate_listing",
    ]
    assert by_address["220 Third Ave"]["source_ready_if_recorded"] is True
    assert by_address["220 Third Ave"]["strict_manager_source_ready_if_recorded"] is False
    assert by_address["220 Third Ave"]["current_relationship_state"] == {
        "current_building_management_relationship_count": 0,
        "current_matching_building_management_relationship_count": 0,
        "current_truth_claim_count": 0,
        "current_matching_truth_claim_count": 0,
        "conflicting_current_manager_count": 0,
        "conflicting_truth_claim_count": 0,
        "current_source_names": [],
        "current_supporting_source_count": 0,
        "current_supporting_evidence_count": 0,
        "current_ledger_source_ready": False,
        "has_operator_confirmed_evidence_recorded": False,
        "safe_action": "No current ledger evidence is recorded for this operator seed; use it for source acquisition only.",
    }
    assert by_address["220 Third Ave"]["supporting_sources_if_recorded"] == ["outreach_confirmed", "renthistory"]
    assert by_address["220 Third Ave"]["manager_proof_source_families_if_recorded"] == ["operator_confirmed"]
    assert by_address["4 W 16th St"]["manual_evidence_template"]["source_name"] == "outreach_confirmed"
    assert by_address["4 W 16th St"]["manual_evidence_template"]["source_type"] == (
        "operator_first_hand_confirmation"
    )
    assert by_address["4 W 16th St"]["second_source_templates"][0]["source_name"] == "renthistory"
    assert by_address["4 W 16th St"]["second_source_templates"][0]["raw_payload"]["source_family"] == (
        "hpd_registration_derived"
    )
    assert by_address["4 W 16th St"]["second_source_templates"][1]["source_name"] == "justia"
    assert by_address["4 W 16th St"]["second_source_templates"][1]["raw_payload"]["source_family"] == (
        "litigation_records"
    )
    assert by_address["4 W 16th St"]["strict_manager_source_ready_if_recorded"] is True
    assert by_address["4 W 16th St"]["strict_manager_gap_status"] == "strict_manager_proof_ready_if_recorded"
    assert by_address["4 W 16th St"]["review_queue"] == "new_relationship_review"
    proposal_by_bbl = {
        proposal["bbl"]: proposal
        for proposal in preview["second_source_seed_batches"]["proposals"]
    }
    assert "Operator-confirmed evidence is a first-hand source family" in (
        preview["second_source_seed_batches"]["source_boundary_notes"][0]
    )
    operator_reviewed_findings = preview["second_source_seed_batches"]["reviewed_source_findings"]
    assert any(
        finding["source_family"] == "company_website"
        and "company-role context" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "external_web_profile"
        and "cannot be recorded as support" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "real_estate_listing"
        and "same real-estate-listing family" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "ny_dos_or_legal_mailing"
        and "not a property-management statement" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "stale_public_utility_notice"
        and "documents.dps.ny.gov" in finding["source_urls"][0]
        and "Andrews Building Corp. as MDU Managing Agent Co." in finding["finding"]
        and "not current MD Squared management proof" in finding["qualification"]
        and "contradiction/review context" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "local_hpd_contact_role_audit"
        and "must not be promoted to manager proof" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "live_hpd_open_data_role_audit_2026_05_15"
        and "cannot be recorded as manager-proof support" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_md_squared_batch_2"
        and "Do not count these as a new strict manager-proof family" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_live_refresh_2026_05_15"
        and "Homes.com and Redfin stay one" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_md_squared_batch_3"
        and "https://md2pg.appfolio.com/listings/listings" in finding["source_urls"]
        and "unrelated Edinboro, PA rentals" in finding["finding"]
        and "Do not add a strict second-source template" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_md_squared_batch_4_2026_05_15"
        and "service-of-process" in finding["finding"]
        and "does not add strict manager-proof evidence" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_md_squared_batch_5_2026_05_15"
        and "found no company-controlled exact-property page" in finding["finding"]
        and "negative reviewed-source finding only" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_md_squared_batch_6_2026_05_15"
        and "Bond Street Lofts" in finding["finding"]
        and "does not change strict source-ready counts" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_md_squared_batch_7_2026_05_15"
        and "OpenStoop owner/boiler-style context" in finding["finding"]
        and "RentHistory remains HPD-registration-derived" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_md_squared_batch_8_2026_05_15"
        and "strict Daisy listing support" in finding["finding"]
        and "adds no new strict MD Squared source template" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_md_squared_batch_9_2026_05_15"
        and "220 Third Avenue Condominium" in finding["finding"]
        and "MD Squared seeds remain broad source-ready only" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_md_squared_batch_10_2026_05_15"
        and "alternate-name pass" in finding["finding"]
        and "does not add a strict MD Squared manager-proof source" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_md_squared_batch_11_2026_05_15"
        and "fresh exact-property pass" in finding["finding"]
        and "adds no strict MD Squared manager-proof source" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_md_squared_batch_12_2026_05_15"
        and "company-controlled service pages" in finding["finding"]
        and "Generic company-service pages prove MD Squared offers property management" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_md_squared_batch_13_2026_05_15"
        and "landlord and its property manager were named as defendants" in finding["finding"]
        and "first exact non-HPD manager-role bridge" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_md_squared_batch_14_2026_05_15"
        and "two remaining MD Squared gaps" in finding["finding"]
        and "live HPD Agent rows are registered-agent/legal-contact evidence" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_md_squared_batch_15_2026_05_15"
        and "managed-by/property-manager variants" in finding["finding"]
        and "adds no strict source template" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_md_squared_batch_16_2026_05_15"
        and "latest exact web pass" in finding["finding"]
        and "does not change source-ready counts" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "site_native_search_md_squared_2026_05_15"
        and "MD Squared's own public search endpoint" in finding["finding"]
        and "220-222 3 Avenue" in finding["finding"]
        and "Bond Street Lofts Condominium" in finding["finding"]
        and "still require one exact non-HPD manager-proof" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_md_squared_batch_17_2026_05_15"
        and "post-approval-boundary exact search" in finding["finding"]
        and "BOND STREET LOFTS CONDOMINIUM" in finding["finding"]
        and "adds no strict template" in finding["qualification"]
        and "dated second outreach confirmation" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_search_followup_md_squared_batch_18_2026_05_16"
        and "one-source threshold-clear simulations" in finding["finding"]
        and "9 Prospect Park West" in finding["finding"]
        and "No real HPD ManagementCompany row was found" in finding["qualification"]
        and "remain simulations only" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "operator_document_raw_xlsx_followup_md_daisy_2026_05_19"
        and not finding["source_urls"]
        and "Revenue by Property - Summary" in finding["finding"]
        and "220 3 Avenue" in finding["finding"]
        and "9 Prospect Park West" in finding["finding"]
        and "private Drive URLs" in finding["qualification"]
        and "does not change source-ready or verified counts" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "operator_document_native_sheet_followup_md_daisy_2026_05_19"
        and not finding["source_urls"]
        and "Revenue by Building" in finding["finding"]
        and "220 3 Avenue" in finding["finding"]
        and "57 Bond Street" in finding["finding"]
        and "9 Prospect Park West" in finding["finding"]
        and "private Drive URL" in finding["qualification"]
        and "does not change source-ready or verified counts" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_and_drive_retry_md_daisy_2026_05_19_phase3"
        and "220 THIRD AVENUE CONDOMINIUM" in finding["finding"]
        and "BOND STREET LOFTS CONDOMINIUM" in finding["finding"]
        and "Sheets row reads hit the per-minute API quota" in finding["finding"]
        and "reviewed source-acquisition history only" in finding["qualification"]
        and "exact non-HPD manager-proof" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "operator_document_raw_drive_fetch_md_daisy_2026_05_19_phase4"
        and not finding["source_urls"]
        and "Revenue by Property - Summary.xlsx" in finding["finding"]
        and "220 3 Avenue" in finding["finding"]
        and "9 Prospect Park West" in finding["finding"]
        and "private Drive URL" in finding["qualification"]
        and "row-level revenue data" in finding["qualification"]
        and "does not change recording-ready/source-ready/verified counts"
        in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "operator_document_exact_drive_search_md_daisy_2026_05_19_phase5"
        and not finding["source_urls"]
        and "Bond, 220, Prospect, West 16, and 141" in finding["finding"]
        and "generic shortlist or portfolio/research materials" in finding["finding"]
        and "reviewed source-acquisition history only" in finding["qualification"]
        and "does not change recording-ready, source-ready, verified, or business-use counts"
        in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_adjacent_md_squared_building_clue_2026_05_19"
        and "572 WEST 141 STREET" in finding["finding"]
        and "613 WEST 140 STREET" in finding["finding"]
        and "220-222 3 Avenue" in finding["finding"]
        and "role-ambiguous" in finding["qualification"]
        and "Do not convert these clues into source evidence" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_exact_gap_retry_md_daisy_2026_05_19_phase6"
        and "57 Bond Street" in finding["finding"]
        and "220-222 3 Avenue" in finding["finding"]
        and "Daisy Property Management" in finding["finding"]
        and "HPD-registration-derived" in finding["finding"]
        and "reviewed source-acquisition history only" in finding["qualification"]
        and "does not change recording-ready, source-ready, verified, or business-use counts"
        in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "public_web_dob_now_md2_57_bond_clue_2026_05_19"
        and "57B Bond Street" in finding["finding"]
        and "BBL 1005297507" in finding["finding"]
        and "DOB NOW Build job-application filings" in finding["finding"]
        and "primary-source acquisition clue only" in finding["qualification"]
        and "Do not convert this clue into source evidence" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "live_hpd_property_managers_first_step_view_2026_06_01"
        and "Property Managers-1st Step" in finding["finding"]
        and "does not expose manager" in finding["finding"]
        and "can only help locate registration rows" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "live_dob_now_md2_57_bond_official_query_2026_06_01"
        and "57 BOND STREET" in finding["finding"]
        and "BBL 1005297507 returned 11 DOB NOW rows" in finding["finding"]
        and "party query for MD2 / MD SQUARED terms returned 9 DOB NOW rows" in finding["finding"]
        and "combined exact target-plus-party query returned 0 rows" in finding["finding"]
        and "official reviewed source-acquisition history only" in finding["qualification"]
        and "no source-evidence template" in finding["qualification"]
        and "role-ambiguous for property management" in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert any(
        finding["source_family"] == "operator_document_native_sheet_retry_md_daisy_2026_05_19_phase7"
        and not finding["source_urls"]
        and "Revenue by Property - Summary" in finding["finding"]
        and "Revenue by Building" in finding["finding"]
        and "56th 342 W - Coop" in finding["finding"]
        and "220 3 Avenue" in finding["finding"]
        and "private Drive URL" in finding["qualification"]
        and "row-level revenue data" in finding["qualification"]
        and "does not change recording-ready, source-ready, verified, or business-use counts"
        in finding["qualification"]
        for finding in operator_reviewed_findings
    )
    assert proposal_by_bbl["1008747504"]["existing_manager_proof_source_families"] == ["operator_confirmed"]
    assert proposal_by_bbl["1008747504"]["supporting_sources_if_recorded"] == ["outreach_confirmed", "renthistory"]
    assert proposal_by_bbl["1008747504"]["source_ready_if_recorded"] is True
    assert proposal_by_bbl["1008747504"]["strict_manager_source_ready_if_recorded"] is False
    assert proposal_by_bbl["1008747504"]["strict_manager_gap_status"] == "broad_source_ready_not_strict"
    assert proposal_by_bbl["1008747504"]["current_relationship_state"]["current_truth_claim_count"] == 0
    assert proposal_by_bbl["1008747504"]["current_relationship_state"]["current_ledger_source_ready"] is False
    assert "HPD-registration-derived" in proposal_by_bbl["1008747504"]["strict_manager_gap_reason"]
    assert "exact non-HPD manager-proof" in proposal_by_bbl["1008747504"]["next_required_manager_proof"]
    assert "company_website" in proposal_by_bbl["1008747504"]["suggested_source_families"]
    assert "outreach_confirmed" in proposal_by_bbl["1008747504"]["suggested_source_families"]
    assert any(
        target["source_family"] == "outreach_confirmed"
        and "dated second operator/outreach confirmation" in target["evidence_needed"]
        and "duplicate freshness context" in target["evidence_needed"]
        for target in proposal_by_bbl["1008747504"]["source_targets"]
    )
    assert proposal_by_bbl["1008170057"]["existing_manager_proof_source_families"] == [
        "operator_confirmed",
        "litigation_records",
    ]
    assert proposal_by_bbl["1008170057"]["supporting_sources_if_recorded"] == [
        "outreach_confirmed",
        "renthistory",
        "justia",
    ]
    assert proposal_by_bbl["1008170057"]["strict_manager_source_ready_if_recorded"] is True
    assert proposal_by_bbl["1008170057"]["strict_manager_gap_status"] == (
        "strict_manager_proof_ready_if_recorded"
    )
    assert proposal_by_bbl["3010680037"]["existing_manager_proof_source_families"] == [
        "operator_confirmed",
        "real_estate_listing",
    ]
    assert proposal_by_bbl["3010680037"]["strict_manager_source_ready_if_recorded"] is True
    assert proposal_by_bbl["3010680037"]["strict_manager_gap_status"] == (
        "strict_manager_proof_ready_if_recorded"
    )
    assert proposal_by_bbl["3010680037"]["second_source_templates"][0]["source_name"] == "homes"
    assert proposal_by_bbl["3010680037"]["second_source_templates"][1]["source_name"] == "redfin"
    assert proposal_by_bbl["3010680037"]["search_queries"][0] == '"Daisy" "9 Prospect Park West"'
    assert any(
        target["source_family"] == "hpd_management_company"
        and "HPD Agent" in target["evidence_needed"]
        for target in proposal_by_bbl["3010680037"]["source_targets"]
    )
    assert any(
        target["source_family"] == "outreach_confirmed"
        and "dated second operator/outreach confirmation" in target["evidence_needed"]
        for target in proposal_by_bbl["3010680037"]["source_targets"]
    )
    assert "explicit post-boundary approval" in preview["safe_action"]

    strict_batch = build_operator_confirmed_evidence_batch(
        preview,
        run_id="truth-operator-confirmed-preview-test",
        recorded_by="operator",
        strict_manager_proof_only=True,
    )
    assert strict_batch["template_count"] == 6
    assert strict_batch["approval_required"] is True
    assert strict_batch["approval_required_before_recording"] is True
    assert strict_batch["allowed_execute"] is False
    assert strict_batch["claim_group_count"] == 2
    assert strict_batch["source_names"] == ["homes", "justia", "outreach_confirmed", "redfin", "renthistory"]
    assert strict_batch["approval_decision_summary"]["would_record_template_count"] == 6
    assert strict_batch["approval_decision_summary"]["would_plan_upsert_count"] == 18
    assert strict_batch["approval_decision_summary"]["expected_safe_to_mark_verified_count"] == 0
    assert strict_batch["approval_decision_summary"]["will_materialize_new_relationships"] is False
    assert strict_batch["excluded_non_strict_candidate_count"] == 2
    assert {candidate["address"] for candidate in strict_batch["excluded_non_strict_candidates"]} == {
        "220 3 AVENUE",
        "57 BOND STREET",
    }
    assert {
        candidate["strict_manager_gap_status"]
        for candidate in strict_batch["excluded_non_strict_candidates"]
    } == {"broad_source_ready_not_strict"}
    assert all(
        candidate["missing_manager_proof_source_family_count"] == 1
        for candidate in strict_batch["excluded_non_strict_candidates"]
    )


@pytest.mark.anyio
async def test_operator_confirmed_management_preview_routes_conflicts_to_review():
    session = FakeAsyncSession([
        FakeExecuteResult(rows=[
            {
                "bbl": "1005297507",
                "address": "57 BOND STREET",
                "borough": "MANHATTAN",
                "zip_code": "10012",
                "unit_count": 10,
                "building_class": "RM",
            },
        ]),
        FakeExecuteResult(rows=[
            {
                "lead_id": "56a71624c6c0",
                "company_name": "MD Squared Property Group",
                "normalized_name": "MD SQUARED PROPERTY GROUP",
                "entity_type": "corporation",
                "portfolio_size": 110,
                "total_units": 2863,
            },
        ]),
        FakeExecuteResult(rows=[
            {
                "building_management_id": 44,
                "bbl": "1005297507",
                "lead_id": "other-lead",
                "role": "manager",
                "registration_start": None,
                "registration_end": None,
                "company_name": "Other Manager",
                "normalized_name": "OTHER MANAGER",
                "entity_type": "corporation",
            },
        ]),
        FakeExecuteResult(rows=[
            {
                "claim_id": "existing-manager-claim",
                "subject_type": "lead",
                "subject_id": "other-lead",
                "predicate": "manages_building",
                "object_type": "building",
                "object_id": "1005297507",
                "normalized_value": "manager",
                "claim_type": "building_management",
                "belief_status": "likely",
                "confidence_score": 0.72,
                "actionability_level": "ranked_sourcing",
                "observed_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
                "evidence_count": 1,
                "source_names": ["building_management"],
                "support_statuses": ["supports"],
                "company_name": "Other Manager",
                "normalized_name": "OTHER MANAGER",
            },
        ]),
    ])

    preview = await load_operator_confirmed_management_preview(session)

    assert preview["matched_candidate_count"] == 1
    assert preview["unmatched_candidate_count"] == 3
    assert preview["conflict_candidate_count"] == 1
    assert preview["contradiction_template_count"] == 1
    candidate = preview["candidates"][0]
    assert candidate["review_queue"] == "conflicting_evidence"
    assert candidate["conflicting_current_manager_count"] == 1
    assert candidate["conflicting_truth_claim_count"] == 1
    assert candidate["current_relationship_state"]["current_building_management_relationship_count"] == 1
    assert candidate["current_relationship_state"]["current_truth_claim_count"] == 1
    assert candidate["current_relationship_state"]["conflicting_current_manager_count"] == 1
    assert candidate["current_relationship_state"]["conflicting_truth_claim_count"] == 1
    assert candidate["current_relationship_state"]["current_source_names"] == []
    assert candidate["current_relationship_state"]["current_ledger_source_ready"] is False
    assert candidate["contradiction_templates"][0]["support_status"] == "contradicts"
    assert candidate["contradiction_templates"][0]["subject_id"] == "other-lead"
    assert "Route conflict to review" in candidate["safe_action"]


def test_operator_confirmed_evidence_batch_builds_broad_and_strict_packets():
    operator_preview = {
        "candidate_count": 2,
        "matched_candidate_count": 2,
        "new_relationship_candidate_count": 2,
        "conflict_candidate_count": 0,
        "manual_evidence_template_count": 4,
        "source_ready_if_recorded_count": 2,
        "strict_manager_source_ready_if_recorded_count": 1,
        "verified_safe_if_recorded_count": 0,
        "second_source_seed_batches": {
            "source_boundary_notes": [
                "Operator-confirmed evidence is a first-hand source family.",
                "RentHistory/HPD-registration-derived pages are excluded from strict manager-proof counts.",
            ],
            "reviewed_source_findings": [
                {
                    "source_family": "company_website",
                    "finding": "MD Squared site proves company-role context.",
                    "qualification": "No exact managed-property page was found.",
                }
            ],
            "proposals": [
                {
                    "candidate_id": "operator-confirmed-md-squared-220-3-ave",
                    "bbl": "1008747504",
                    "address": "220 3 AVENUE",
                    "manager_lead_id": "56a71624c6c0",
                    "manager_name": "MD Squared Property Group",
                    "existing_manager_proof_source_families": ["operator_confirmed"],
                    "supporting_source_families_if_recorded": [
                        "operator_confirmed",
                        "hpd_registration_derived",
                    ],
                    "strict_manager_source_ready_if_recorded": False,
                    "strict_manager_gap_status": "broad_source_ready_not_strict",
                    "strict_manager_gap_reason": (
                        "Broad source-ready only: the preview has multiple source families, but at least one "
                        "second source is HPD-registration-derived and excluded from strict manager-proof counts."
                    ),
                    "current_relationship_state": {
                        "current_building_management_relationship_count": 0,
                        "current_truth_claim_count": 0,
                        "current_ledger_source_ready": False,
                    },
                    "missing_manager_proof_source_family_count": 1,
                    "next_required_manager_proof": "Acquire one exact non-HPD manager-proof source family.",
                    "suggested_source_families": ["company_website", "external_web_profile"],
                    "search_queries": ['"MD Squared" "220 3 Avenue"'],
                    "source_targets": [{
                        "source_family": "company_website",
                        "evidence_needed": "Find an exact MD Squared page for 220 3 AVENUE.",
                    }],
                },
                {
                    "candidate_id": "operator-confirmed-daisy-9-prospect-park-w",
                    "bbl": "3010680037",
                    "address": "9 PROSPECT PARK WEST",
                    "manager_lead_id": "d11246cb2dae",
                    "manager_name": "Daisy Management",
                    "existing_manager_proof_source_families": ["operator_confirmed"],
                    "supporting_source_families_if_recorded": [
                        "operator_confirmed",
                        "real_estate_listing",
                    ],
                    "strict_manager_source_ready_if_recorded": True,
                    "strict_manager_gap_status": "strict_manager_proof_ready_if_recorded",
                    "strict_manager_gap_reason": "Strict manager-proof overlap exists if approved.",
                    "current_relationship_state": {
                        "current_building_management_relationship_count": 0,
                        "current_truth_claim_count": 3,
                        "current_source_names": ["homes", "outreach_confirmed", "redfin"],
                        "current_supporting_source_count": 3,
                        "current_supporting_evidence_count": 3,
                        "current_ledger_source_ready": True,
                    },
                    "missing_manager_proof_source_family_count": 0,
                    "next_required_manager_proof": "Review templates, record only after explicit approval.",
                    "suggested_source_families": ["real_estate_listing"],
                    "search_queries": ['"Daisy" "9 Prospect Park W"'],
                    "source_targets": [{
                        "source_family": "real_estate_listing",
                        "evidence_needed": "Inspect exact Homes.com listing for 9 PROSPECT PARK WEST.",
                    }],
                },
            ],
        },
        "candidates": [
            {
                "candidate_id": "operator-confirmed-md-squared-220-3-ave",
                "user_address": "220 Third Ave",
                "manager_name_supplied": "MD Squared",
                "matched_building": {"bbl": "1008747504", "address": "220 3 AVENUE"},
                "matched_lead": {"lead_id": "56a71624c6c0", "company_name": "MD Squared Property Group"},
                "review_queue": "new_relationship_review",
                "supporting_sources_if_recorded": ["outreach_confirmed", "renthistory"],
                "supporting_source_families_if_recorded": ["operator_confirmed", "hpd_registration_derived"],
                "manager_proof_source_families_if_recorded": ["operator_confirmed"],
                "current_relationship_state": {
                    "current_building_management_relationship_count": 0,
                    "current_truth_claim_count": 0,
                    "current_ledger_source_ready": False,
                },
                "source_ready_if_recorded": True,
                "strict_manager_source_ready_if_recorded": False,
                "verified_safe_if_recorded": False,
                "manual_evidence_template": {
                    "subject_type": "lead",
                    "subject_id": "56a71624c6c0",
                    "predicate": "manages_building",
                    "object_type": "building",
                    "object_id": "1008747504",
                    "claim_type": "building_management",
                    "normalized_value": "manager",
                    "support_status": "supports",
                    "source_name": "outreach_confirmed",
                    "source_type": "operator_first_hand_confirmation",
                    "source_record_id": "operator-md-220",
                    "observed_at": "2026-05-14T00:00:00+00:00",
                    "raw_payload": {
                        "source_family": "operator_confirmed",
                        "candidate_id": "operator-confirmed-md-squared-220-3-ave",
                    },
                },
                "second_source_templates": [
                    {
                        "subject_type": "lead",
                        "subject_id": "56a71624c6c0",
                        "predicate": "manages_building",
                        "object_type": "building",
                        "object_id": "1008747504",
                        "claim_type": "building_management",
                        "normalized_value": "manager",
                        "support_status": "supports",
                        "source_name": "renthistory",
                        "source_type": "hpd_registration_index",
                        "source_record_id": "renthistory-md-220",
                        "observed_at": "2026-05-14T00:00:00+00:00",
                        "raw_payload": {
                            "source_family": "hpd_registration_derived",
                            "candidate_status": "hpd_registration_derived_review_required",
                            "operator_candidate_id": "operator-confirmed-md-squared-220-3-ave",
                        },
                    }
                ],
            },
            {
                "candidate_id": "operator-confirmed-daisy-9-prospect-park-w",
                "user_address": "9 Prospect Park W",
                "manager_name_supplied": "Daisy",
                "matched_building": {"bbl": "3010680037", "address": "9 PROSPECT PARK WEST"},
                "matched_lead": {"lead_id": "d11246cb2dae", "company_name": "Daisy Management"},
                "review_queue": "new_relationship_review",
                "supporting_sources_if_recorded": ["outreach_confirmed", "homes"],
                "supporting_source_families_if_recorded": ["operator_confirmed", "real_estate_listing"],
                "manager_proof_source_families_if_recorded": ["operator_confirmed", "real_estate_listing"],
                "current_relationship_state": {
                    "current_building_management_relationship_count": 0,
                    "current_truth_claim_count": 3,
                    "current_source_names": ["homes", "outreach_confirmed", "redfin"],
                    "current_supporting_source_count": 3,
                    "current_supporting_evidence_count": 3,
                    "current_ledger_source_ready": True,
                },
                "source_ready_if_recorded": True,
                "strict_manager_source_ready_if_recorded": True,
                "verified_safe_if_recorded": False,
                "manual_evidence_template": {
                    "subject_type": "lead",
                    "subject_id": "d11246cb2dae",
                    "predicate": "manages_building",
                    "object_type": "building",
                    "object_id": "3010680037",
                    "claim_type": "building_management",
                    "normalized_value": "manager",
                    "support_status": "supports",
                    "source_name": "outreach_confirmed",
                    "source_type": "operator_first_hand_confirmation",
                    "source_record_id": "operator-daisy-9ppw",
                    "observed_at": "2026-05-14T00:00:00+00:00",
                    "raw_payload": {
                        "source_family": "operator_confirmed",
                        "candidate_id": "operator-confirmed-daisy-9-prospect-park-w",
                    },
                },
                "second_source_templates": [
                    {
                        "subject_type": "lead",
                        "subject_id": "d11246cb2dae",
                        "predicate": "manages_building",
                        "object_type": "building",
                        "object_id": "3010680037",
                        "claim_type": "building_management",
                        "normalized_value": "manager",
                        "support_status": "supports",
                        "source_name": "homes",
                        "source_type": "real_estate_listing_property_profile",
                        "source_record_id": "homes-daisy-9ppw",
                        "observed_at": "2026-05-14T00:00:00+00:00",
                        "raw_payload": {
                            "source_family": "real_estate_listing",
                            "candidate_status": "clean_exact_match",
                            "operator_candidate_id": "operator-confirmed-daisy-9-prospect-park-w",
                        },
                    }
                ],
            },
        ],
    }

    broad = build_operator_confirmed_evidence_batch(
        operator_preview,
        run_id="truth-operator-confirmed-test",
        recorded_by="operator",
    )
    strict = build_operator_confirmed_evidence_batch(
        operator_preview,
        run_id="truth-operator-confirmed-test",
        recorded_by="operator",
        strict_manager_proof_only=True,
    )

    assert broad["dry_run"] is True
    assert broad["approval_required"] is True
    assert broad["approval_required_before_recording"] is True
    assert broad["allowed_execute"] is False
    assert broad["batch_filter"] == "all_source_ready"
    assert broad["template_count"] == 4
    assert broad["claim_group_count"] == 2
    assert broad["source_names"] == ["homes", "outreach_confirmed", "renthistory"]
    assert broad["post_recording_simulation"]["source_ready_fact_group_count"] == 2
    assert broad["post_recording_simulation"]["strict_manager_source_ready_fact_group_count"] == 1
    assert broad["post_recording_simulation"]["safe_to_mark_verified_count"] == 0
    assert "--execute --confirm-execute" in broad["recommended_execute_command"]
    assert "RentHistory/HPD-registration-derived" in broad["source_boundary_note"]
    assert broad["reviewed_source_findings"][0]["source_family"] == "company_website"
    assert "first-hand source family" in broad["source_boundary_notes"][0]
    assert strict["batch_filter"] == "strict_manager_proof"
    assert strict["approval_required"] is True
    assert strict["approval_required_before_recording"] is True
    assert strict["allowed_execute"] is False
    assert strict["template_count"] == 2
    assert strict["claim_group_count"] == 1
    assert strict["source_names"] == ["homes", "outreach_confirmed"]
    assert strict["post_recording_simulation"]["strict_manager_source_ready_fact_group_count"] == 1
    assert strict["approval_decision_summary"]["included_addresses"] == ["9 PROSPECT PARK WEST"]
    assert strict["approval_decision_summary"]["will_mark_verified"] is False
    assert strict["excluded_non_strict_candidate_count"] == 1
    assert strict["excluded_non_strict_candidates"][0]["address"] == "220 3 AVENUE"
    assert strict["excluded_non_strict_candidates"][0]["strict_manager_gap_status"] == (
        "broad_source_ready_not_strict"
    )
    assert strict["excluded_non_strict_candidates"][0]["current_relationship_state"][
        "current_ledger_source_ready"
    ] is False
    assert "exact non-HPD manager-proof" in strict["excluded_non_strict_candidates"][0][
        "next_required_manager_proof"
    ]
    assert strict["claim_group_review_summary"][0]["current_relationship_state"]["current_truth_claim_count"] == 3
    assert strict["claim_group_review_summary"][0]["current_relationship_state"]["current_ledger_source_ready"] is True
    assert strict["claim_group_review_summary"][0]["verification_score_if_recorded"]["score_gap_to_verified"] > 0
    assert "confidence_below_verified_threshold" in (
        strict["claim_group_review_summary"][0]["verification_score_if_recorded"]["verified_blockers"]
    )

    source_packet = build_operator_source_acquisition_packet(
        operator_preview,
        run_id="truth-operator-source-acquisition-test",
    )
    assert source_packet["run_type"] == "operator_source_acquisition_packet"
    assert source_packet["dry_run"] is True
    assert source_packet["mutations_planned"] == 0
    assert source_packet["second_source_seed_count"] == 2
    assert source_packet["strict_manager_source_ready_if_recorded_count"] == 1
    assert source_packet["suggested_source_family_counts"] == {
        "company_website": 1,
        "external_web_profile": 1,
        "real_estate_listing": 1,
    }
    assert source_packet["proposals"][0]["first_search_query"] == '"MD Squared" "220 3 Avenue"'
    assert source_packet["proposals"][0]["strict_manager_source_ready_if_recorded"] is False
    assert source_packet["proposals"][0]["current_relationship_state"]["current_truth_claim_count"] == 0
    assert source_packet["proposals"][0]["current_relationship_state"]["current_ledger_source_ready"] is False
    assert source_packet["proposals"][0]["strict_manager_gap_status"] == "broad_source_ready_not_strict"
    assert source_packet["proposals"][1]["current_relationship_state"]["current_truth_claim_count"] == 3
    assert source_packet["proposals"][1]["current_relationship_state"]["current_ledger_source_ready"] is True
    assert "exact non-HPD manager-proof" in source_packet["proposals"][0]["next_required_manager_proof"]
    assert "Read-only operator source-acquisition packet" in source_packet["safe_action"]


def test_manager_external_evidence_batch_excludes_address_range_review_templates():
    batch = build_manager_external_evidence_batch(
        {
            "candidate_source_count": 7,
            "matched_evidence_candidate_count": 2,
            "claim_group_count": 1,
            "clean_exact_claim_count": 0,
            "source_ready_if_recorded_count": 1,
            "independent_source_ready_if_recorded_count": 1,
            "strict_manager_source_ready_if_recorded_count": 0,
            "excluded_manager_proof_source_families": ["hpd_registration_derived"],
            "review_required_count": 1,
            "unmatched_candidate_count": 1,
            "new_relationship_candidate_count": 1,
            "new_relationship_candidates": [{
                "candidate_id": "ny-dps-verizon-402-w-153-petition",
                "local_building_match": {"bbl": "1020670047", "address": "402 WEST 153 STREET"},
            }],
            "next_source_batches": {
                "proposals": [{
                    "bbl": "1019080014",
                    "address": "141 WEST 123 STREET",
                    "existing_manager_proof_source_families": ["external_web_profile"],
                    "missing_manager_proof_source_family_count": 1,
                    "suggested_source_families": ["ny_dps_order_entry", "company_website"],
                    "search_queries": ['"Harlem Property Management" "141 West 123 Street"'],
                    "source_targets": [{
                        "source_family": "ny_dps_order_entry",
                        "evidence_needed": "Find exact manager proof for 141 WEST 123 STREET.",
                    }],
                    "safe_action": "Acquire one more non-HPD-derived manager-specific source.",
                }],
            },
            "manual_evidence_batch_preview": {"excluded_address_review_candidate_count": 1},
            "post_recording_simulation": {
                "dry_run": True,
                "simulated_fact_group_count": 1,
                "source_ready_fact_group_count": 1,
                "strict_manager_source_ready_fact_group_count": 0,
                "safe_to_mark_verified_count": 0,
            },
            "claim_groups": [{
                "fact_key": {"object_id": "1016837501"},
                "address": "324 EAST 112 STREET",
                "source_ready_if_recorded": True,
                "independent_source_ready_if_recorded": True,
                "manual_evidence_templates": [
                    {
                        "subject_type": "lead",
                        "subject_id": "0ff794d3ba2d",
                        "predicate": "manages_building",
                        "object_type": "building",
                        "object_id": "1016837501",
                        "claim_type": "building_management",
                        "normalized_value": "manager",
                        "source_name": "openigloo",
                        "source_type": "management_company_profile",
                        "support_status": "supports",
                        "raw_payload": {"candidate_status": "external_web_review_required"},
                    },
                    {
                        "subject_type": "lead",
                        "subject_id": "0ff794d3ba2d",
                        "predicate": "manages_building",
                        "object_type": "building",
                        "object_id": "1020257501",
                        "claim_type": "building_management",
                        "normalized_value": "manager",
                        "source_name": "ny_dps_order_entry",
                        "source_type": "ny_dps_order_entry_exhibit",
                        "support_status": "supports",
                        "raw_payload": {"candidate_status": "address_range_review_required"},
                    },
                ],
            }],
        },
        lead_id="0ff794d3ba2d",
        run_id="manager-batch-test",
        recorded_by="operator",
    )

    assert batch["dry_run"] is True
    assert batch["mutations_planned"] == 0
    assert batch["approval_required"] is True
    assert batch["approval_required_before_recording"] is True
    assert batch["allowed_execute"] is False
    assert batch["batch_filter"] == "all_source_ready"
    assert batch["template_count"] == 1
    assert batch["excluded_template_count"] == 1
    assert batch["excluded_address_review_candidate_count"] == 1
    assert batch["included_bbls"] == ["1016837501"]
    assert batch["manual_evidence_templates"][0]["source_name"] == "openigloo"
    assert batch["candidate_status_counts"] == {"external_web_review_required": 1}
    assert batch["new_relationship_candidate_count"] == 1
    assert batch["new_relationship_candidates"][0]["local_building_match"]["bbl"] == "1020670047"
    assert "not included in this evidence batch" in batch["new_relationship_policy"]
    assert batch["next_source_search_pack_count"] == 1
    assert batch["next_source_search_pack"][0]["search_queries"] == [
        '"Harlem Property Management" "141 West 123 Street"'
    ]
    assert "Exact-property search guidance only" in batch["next_source_search_policy"]
    assert batch["claim_group_review_summary"] == [{
        "bbl": "1016837501",
        "address": "324 EAST 112 STREET",
        "template_count": 1,
        "acquisition_source_ready_if_recorded": True,
        "acquisition_independent_source_ready_if_recorded": True,
        "acquisition_strict_manager_source_ready_if_recorded": False,
        "supporting_sources_if_recorded": ["openigloo"],
        "supporting_source_families_if_recorded": [],
        "manager_proof_source_families_if_recorded": [],
        "supporting_source_count_if_recorded": 1,
        "independent_source_family_count_if_recorded": 0,
        "manager_proof_source_family_count_if_recorded": 0,
        "source_ready_if_recorded": False,
        "independent_source_ready_if_recorded": False,
        "strict_manager_source_ready_if_recorded": False,
        "safe_to_mark_verified_if_recorded": False,
        "verification_score_if_recorded": {
            "recomputed_confidence_score": 0.559,
            "proposed_belief_status": "proposed",
            "proposed_actionability_level": "broad_discovery",
            "freshest_observed_freshness_days": 0,
            "score_gap_to_verified": 0.341,
            "verified_blockers": [
                "needs_independent_source",
                "needs_additional_evidence",
                "confidence_below_verified_threshold",
            ],
        },
        "readiness_blockers": [
            "needs_two_supporting_sources",
            "needs_two_independent_source_families",
            "needs_two_manager_proof_source_families",
            "not_safe_to_mark_verified_after_recording",
            "confidence_below_verified_threshold",
            "needs_additional_evidence",
            "needs_independent_source",
        ],
        "source_names": ["openigloo"],
        "source_families": [],
        "manager_proof_source_families": [],
        "source_record_ids": [],
        "source_urls": [],
        "candidate_status_counts": {"external_web_review_required": 1},
        "post_recording_simulation": {
            "multi_source_fact_group_count": 0,
            "source_ready_fact_group_count": 0,
            "independent_source_ready_fact_group_count": 0,
            "strict_manager_source_ready_fact_group_count": 0,
            "safe_to_mark_verified_count": 0,
            "blocker_counts": {
                "confidence_below_verified_threshold": 1,
                "needs_additional_evidence": 1,
                "needs_independent_source": 1,
            },
        },
    }]
    assert batch["acquisition_preview_summary"]["unmatched_candidate_count"] == 1
    assert batch["acquisition_preview_summary"]["new_relationship_candidate_count"] == 1
    assert batch["acquisition_preview_summary"]["clean_exact_claim_count"] == 0
    assert batch["acquisition_preview_summary"]["strict_manager_source_ready_if_recorded_count"] == 0
    assert batch["post_recording_simulation"]["source_ready_fact_group_count"] == 0
    assert batch["recommended_execute_command"] == (
        "python scripts/truth_manager_external_evidence_batch.py --execute --confirm-execute --indent 2"
    )


def test_manager_source_acquisition_packet_prioritizes_exact_source_searches():
    packet = build_manager_source_acquisition_packet(
        {
            "claim_group_count": 13,
            "clean_exact_claim_count": 5,
            "source_ready_if_recorded_count": 13,
            "strict_manager_source_ready_if_recorded_count": 3,
            "new_relationship_candidate_count": 2,
            "new_relationship_candidates": [
                {
                    "candidate_id": "nyc-dof-275-greenwich-hpm-billing-record",
                    "source_name": "nyc_dof_assessment",
                    "source_family": "nyc_dof_billing_record",
                    "external_address": "275 GREENWICH STREET",
                    "local_address": "269 GREENWICH STREET",
                    "manager_name": "Harlem Property Management, Inc.",
                    "evidence_role": "tax_billing_contact",
                    "source_url": "https://a836-pts-access.nyc.gov/care/datalets/datalet.aspx?pin=1001327501",
                    "local_building_match": {
                        "bbl": "1001327501",
                        "address": "269 GREENWICH STREET",
                    },
                    "safe_action": "Review as a possible new relationship claim.",
                },
            ],
            "post_recording_simulation": {"safe_to_mark_verified_count": 0},
            "next_source_batches": {
                "candidate_count": 2,
                "suggested_source_family_counts": {"ny_dps_order_entry": 1, "company_website": 2},
                "source_boundary_notes": ["RentHistory is review-only."],
                "reviewed_source_findings": [{
                    "source_family": "public_web_search_followup_hpm_batch_2",
                    "finding": "Repeated OpenIgloo/RentHistory only.",
                    "qualification": "No second independent manager-proof family.",
                }],
                "proposals": [
                    {
                        "bbl": "1020517501",
                        "address": "330 WEST 145 STREET",
                        "existing_manager_proof_source_families": ["ny_dps_order_entry"],
                        "missing_manager_proof_source_family_count": 1,
                        "suggested_source_families": ["company_website", "outreach_confirmed"],
                        "search_queries": ['"Harlem Property Management" "330 West 145 Street"'],
                        "source_targets": [{
                            "source_family": "company_website",
                            "evidence_needed": "Find exact HPM portal evidence.",
                        }],
                        "safe_action": "Acquire one more exact source.",
                    },
                    {
                        "bbl": "1019080014",
                        "address": "141 WEST 123 STREET",
                        "existing_manager_proof_source_families": ["external_web_profile"],
                        "missing_manager_proof_source_family_count": 1,
                        "suggested_source_families": ["ny_dps_order_entry", "company_website"],
                        "search_queries": ['"Harlem Property Management" "141 West 123 Street"'],
                        "source_targets": [{
                            "source_family": "ny_dps_order_entry",
                            "evidence_needed": "Find exact NY DPS manager evidence.",
                        }],
                        "safe_action": "Acquire one more exact source.",
                    },
                ],
            },
        },
        lead_id="0ff794d3ba2d",
        run_id="source-acquisition-test",
    )

    assert packet["run_type"] == "manager_source_acquisition_packet"
    assert packet["dry_run"] is True
    assert packet["mutations_planned"] == 0
    assert packet["candidate_count"] == 2
    assert packet["source_ready_if_recorded_count"] == 13
    assert packet["strict_manager_source_ready_if_recorded_count"] == 3
    assert packet["verified_safe_if_recorded_count"] == 0
    assert packet["next_source_seed_count"] == 2
    assert packet["new_relationship_candidate_count"] == 2
    assert packet["new_relationship_candidates"][0]["local_building_match"]["bbl"] == "1001327501"
    assert "not counted as current-ledger source overlap" in packet["new_relationship_policy"]
    assert packet["proposals"][0]["bbl"] == "1019080014"
    assert packet["proposals"][0]["first_search_query"] == '"Harlem Property Management" "141 West 123 Street"'
    assert packet["proposals"][0]["source_targets"][0]["source_family"] == "ny_dps_order_entry"
    assert packet["reviewed_source_findings"][0]["source_family"] == "public_web_search_followup_hpm_batch_2"
    assert packet["current_preview_summary"]["strict_manager_source_ready_if_recorded_count"] == 3
    assert packet["current_preview_summary"]["new_relationship_candidate_count"] == 2
    assert "Read-only source-acquisition packet" in packet["safe_action"]


def test_manager_external_evidence_batch_can_filter_to_strict_manager_proof_groups():
    batch = build_manager_external_evidence_batch(
        {
            "candidate_source_count": 7,
            "matched_evidence_candidate_count": 3,
            "claim_group_count": 2,
            "clean_exact_claim_count": 1,
            "source_ready_if_recorded_count": 2,
            "independent_source_ready_if_recorded_count": 2,
            "strict_manager_source_ready_if_recorded_count": 1,
            "manual_evidence_batch_preview": {"excluded_address_review_candidate_count": 0},
            "claim_groups": [
                {
                    "fact_key": {"object_id": "1016837501"},
                    "address": "324 EAST 112 STREET",
                    "source_ready_if_recorded": True,
                    "independent_source_ready_if_recorded": True,
                    "strict_manager_source_ready_if_recorded": True,
                    "manual_evidence_templates": [
                        {
                            "subject_type": "lead",
                            "subject_id": "0ff794d3ba2d",
                            "predicate": "manages_building",
                            "object_type": "building",
                            "object_id": "1016837501",
                            "claim_type": "building_management",
                            "normalized_value": "manager",
                            "source_name": "ny_dps_order_entry",
                            "source_type": "ny_dps_order_entry_exhibit",
                            "support_status": "supports",
                            "raw_payload": {
                                "candidate_status": "clean_exact_match",
                                "source_family": "ny_dps_order_entry",
                            },
                        },
                        {
                            "subject_type": "lead",
                            "subject_id": "0ff794d3ba2d",
                            "predicate": "manages_building",
                            "object_type": "building",
                            "object_id": "1016837501",
                            "claim_type": "building_management",
                            "normalized_value": "manager",
                            "source_name": "openigloo",
                            "source_type": "management_company_profile",
                            "support_status": "supports",
                            "raw_payload": {
                                "candidate_status": "external_web_review_required",
                                "source_family": "external_web_profile",
                            },
                        },
                    ],
                },
                {
                    "fact_key": {"object_id": "1019080014"},
                    "address": "141 WEST 123 STREET",
                    "source_ready_if_recorded": True,
                    "independent_source_ready_if_recorded": True,
                    "strict_manager_source_ready_if_recorded": False,
                    "manual_evidence_templates": [{
                        "subject_type": "lead",
                        "subject_id": "0ff794d3ba2d",
                        "predicate": "manages_building",
                        "object_type": "building",
                        "object_id": "1019080014",
                        "claim_type": "building_management",
                        "normalized_value": "manager",
                        "source_name": "renthistory",
                        "source_type": "hpd_registration_index",
                        "support_status": "supports",
                        "raw_payload": {
                            "candidate_status": "derived_source_review_required",
                            "source_family": "hpd_registration_derived",
                        },
                    }],
                },
            ],
        },
        lead_id="0ff794d3ba2d",
        run_id="manager-batch-strict-test",
        recorded_by="operator",
        strict_manager_proof_only=True,
    )

    assert batch["batch_filter"] == "strict_manager_proof"
    assert batch["approval_required"] is True
    assert batch["approval_required_before_recording"] is True
    assert batch["allowed_execute"] is False
    assert batch["claim_group_count"] == 1
    assert batch["included_bbls"] == ["1016837501"]
    assert batch["template_count"] == 2
    assert batch["source_families"] == ["external_web_profile", "ny_dps_order_entry"]
    assert batch["manager_proof_source_families"] == ["external_web_profile", "ny_dps_order_entry"]
    assert batch["post_recording_simulation"]["strict_manager_source_ready_fact_group_count"] == 1
    approval_summary = batch["approval_decision_summary"]
    assert approval_summary["approval_required"] is True
    assert approval_summary["batch_filter"] == "strict_manager_proof"
    assert approval_summary["would_record_template_count"] == 2
    assert approval_summary["would_record_claim_group_count"] == 1
    assert approval_summary["would_plan_upsert_count"] == 6
    assert approval_summary["included_addresses"] == ["324 EAST 112 STREET"]
    assert approval_summary["expected_strict_manager_source_ready_fact_group_count"] == 1
    assert approval_summary["expected_safe_to_mark_verified_count"] == 0
    assert approval_summary["single_source_claims_stay_unverified"] is True
    assert approval_summary["will_mark_verified"] is False
    assert approval_summary["will_create_or_refresh_source_data"] is False
    assert approval_summary["will_materialize_new_relationships"] is False
    assert approval_summary["post_execution_required_checks"] == [
        "python scripts/truth_adjudication_preview.py --limit 20 --no-samples --indent 2",
        "python scripts/truth_health_report.py --indent 2",
        "python scripts/truth_completion_audit.py --include-runtime --include-production --indent 2",
    ]
    assert batch["recommended_execute_command"] == (
        "python scripts/truth_manager_external_evidence_batch.py "
        "--strict-manager-proof-only --execute --confirm-execute --indent 2"
    )


def test_manager_external_evidence_batch_rollup_helpers_merge_rollback_counts():
    from scripts.truth_manager_external_evidence_batch import _add_rollback_summary, _empty_rollback_aggregate

    aggregate = _empty_rollback_aggregate()

    _add_rollback_summary(
        aggregate,
        {
            "rollback_plan": {
                "new_claim_count": 1,
                "updated_claim_count": 0,
                "new_evidence_count": 1,
                "updated_evidence_count": 0,
                "new_confidence_snapshot_count": 1,
                "updated_confidence_snapshot_count": 0,
            },
            "rollback_manifest": {
                "entry_count": 3,
                "by_type": {
                    "truth_claim": {"new": 1},
                    "truth_evidence": {"new": 1},
                    "confidence_snapshot": {"new": 1},
                },
            },
        },
    )
    _add_rollback_summary(
        aggregate,
        {
            "rollback_plan": {
                "new_claim_count": 0,
                "updated_claim_count": 1,
                "new_evidence_count": 1,
                "updated_evidence_count": 0,
                "new_confidence_snapshot_count": 1,
                "updated_confidence_snapshot_count": 0,
            },
            "rollback_manifest": {
                "entry_count": 3,
                "by_type": {
                    "truth_claim": {"existing": 1},
                    "truth_evidence": {"new": 1},
                    "confidence_snapshot": {"new": 1},
                },
            },
        },
    )

    assert aggregate["new_claim_count"] == 1
    assert aggregate["updated_claim_count"] == 1
    assert aggregate["new_evidence_count"] == 2
    assert aggregate["new_confidence_snapshot_count"] == 2
    assert aggregate["manifest_entry_count"] == 6
    assert aggregate["manifest_by_type"]["truth_claim"] == {"new": 1, "existing": 1}


def test_source_overlap_batch_preview_items_expose_manual_evidence_mutation_scope():
    mutation_scope = {
        "allowed_tables": [
            "truth_materialization_manifest",
            "truth_claims",
            "truth_evidence",
            "confidence_snapshots",
        ],
        "forbidden_side_effects": {
            "will_mark_verified": False,
            "will_create_or_refresh_source_data": False,
            "will_materialize_building_management_relationships": False,
            "will_start_jobs": False,
            "will_allow_business_use": False,
        },
    }
    result = {
        "claim_spec": {
            "claim_id": "claim-1",
            "evidence_id": "evidence-1",
            "object_id": "1016837501",
            "source_name": "openigloo",
            "source_type": "management_company_profile",
            "freshness_days": 30,
            "actionability_level": "broad_discovery",
        },
        "mutations_planned": 3,
        "allowed_execute": False,
        "mutation_scope": mutation_scope,
        "rollback_plan": {
            "new_claim_count": 1,
            "updated_claim_count": 0,
            "new_evidence_count": 1,
            "updated_evidence_count": 0,
            "new_confidence_snapshot_count": 1,
            "updated_confidence_snapshot_count": 0,
        },
        "rollback_manifest": {
            "entry_count": 3,
            "by_type": {
                "truth_claim": {"new": 1},
                "truth_evidence": {"new": 1},
                "confidence_snapshot": {"new": 1},
            },
        },
    }

    manager_preview = build_manager_manual_evidence_preview(
        {
            "predicate": "manages_building",
            "support_status": "supports",
            "source_record_id": "openigloo-324-east-112",
            "source_url": "https://example.com/324-east-112",
            "raw_payload": {
                "local_address": "324 EAST 112 STREET",
                "candidate_status": "external_web_review_required",
            },
        },
        result,
    )
    operator_preview = build_operator_manual_evidence_preview(
        {
            "predicate": "manages_building",
            "support_status": "supports",
            "source_record_id": "operator-md-squared-4-w-16",
            "source_url": "operator://first-hand/md-squared-4-w-16",
            "raw_payload": {
                "canonical_address": "4 WEST 16 STREET",
                "source_family": "operator_confirmed",
                "candidate_id": "operator-confirmed-md-squared-4-w-16",
            },
        },
        result,
    )

    for preview in (manager_preview, operator_preview):
        assert preview["mutation_scope"] == mutation_scope
        assert preview["mutation_scope"]["allowed_tables"] == [
            "truth_materialization_manifest",
            "truth_claims",
            "truth_evidence",
            "confidence_snapshots",
        ]
        assert preview["mutation_scope"]["forbidden_side_effects"][
            "will_materialize_building_management_relationships"
        ] is False
        assert preview["mutation_scope"]["forbidden_side_effects"]["will_mark_verified"] is False


def test_role_overlap_activation_plan_keeps_registered_agent_and_manager_readiness_separate():
    plan = build_role_overlap_activation_plan(
        correction_preview={"sampled_stale_claim_count": 50},
        scaled_role_source_overlap={
            "source_ready_if_materialized_count": 82,
            "management_source_ready_if_materialized_count": 0,
            "registered_agent_source_ready_if_materialized_count": 82,
        },
    )

    assert plan["dry_run"] is True
    assert plan["mutations_planned"] == 0
    assert plan["approval_required"] is True
    assert plan["current_ledger_verified_claims_added"] == 0
    assert plan["predicted_if_approved"]["source_ready_fact_groups_added"] == 82
    assert plan["predicted_if_approved"]["management_source_ready_fact_groups_added"] == 0
    assert plan["predicted_if_approved"]["registered_agent_source_ready_fact_groups_added"] == 82
    assert plan["ordered_steps"][1]["status"] == "approval_required"
    assert plan["ordered_steps"][2]["sources"] == ["building_management", "hpd_contact_role_links"]
    assert "cannot verify operating-manager facts" in plan["business_readiness_note"]


def test_role_overlap_activation_packet_is_preview_only_and_command_gated():
    packet = build_role_overlap_activation_packet(
        schema_status={"ready": True, "current_revision": "010_truth_manifest"},
        adjudication_preview={
            "verification_candidate_count": 0,
            "ledger_source_overlap": {
                "total_fact_group_count": 2063,
                "multi_source_fact_group_count": 0,
                "source_ready_fact_group_count": 0,
            },
            "role_overlap_post_materialization_simulation": {
                "planned_claim_spec_count": 164,
                "simulated_fact_group_count": 82,
                "multi_source_fact_group_count": 82,
                "source_ready_fact_group_count": 82,
                "safe_to_mark_verified_count": 0,
                "source_ready_count_by_predicate": {"registered_agent_for_building": 82},
                "safe_to_mark_verified_count_by_predicate": {},
            },
            "role_overlap_activation_plan": {
                "predicted_if_approved": {
                    "source_ready_fact_groups_added": 82,
                    "management_source_ready_fact_groups_added": 0,
                    "registered_agent_source_ready_fact_groups_added": 82,
                    "stale_role_claims_to_supersede": 50,
                },
                "ordered_steps": [
                    {"step": "execute_role_claim_corrections", "approval_required": True, "mutations_planned": 50},
                    {
                        "step": "execute_bounded_role_overlap_materialization",
                        "approval_required": True,
                        "mutations_planned": 164,
                    },
                ],
            },
            "manager_external_source_acquisition_preview": {
                "candidate_source_count": 11,
                "matched_evidence_candidate_count": 29,
                "clean_exact_claim_count": 3,
                "claim_group_count": 11,
                "source_ready_if_recorded_count": 12,
                "independent_source_ready_if_recorded_count": 12,
                "review_required_count": 26,
                "unmatched_candidate_count": 0,
                "claim_groups": [{
                    "fact_key": {"object_id": "1016837501", "predicate": "manages_building"},
                    "address": "324 EAST 112 STREET",
                    "building_management_role": "agent",
                    "supporting_sources_if_recorded": ["verizon_order_entry_petition", "ny_dps_order_entry"],
                    "supporting_source_families_if_recorded": ["ny_dps_order_entry"],
                    "source_ready_if_recorded": True,
                    "independent_source_ready_if_recorded": False,
                }],
            },
        },
        materialization_preview={
            "planned_claims_total": 164,
            "planned_claims_by_source": {"building_management": 82, "hpd_contact_role_links": 82},
            "strict_materializable_claims_by_source": {"hpd_contact_role_links": 82},
            "strict_materializable_claims_by_predicate": {"registered_agent_for_building": 82},
            "candidate_claims_by_source": {"hpd_contact_role_links": 355},
        },
        limit=500,
    )

    assert packet["dry_run"] is True
    assert packet["mutations_planned"] == 0
    assert packet["readiness"]["current_source_ready_fact_groups"] == 0
    assert packet["manager_external_source_acquisition_preview"]["clean_exact_claim_count"] == 3
    assert packet["manager_external_source_acquisition_preview"]["claim_groups"][0]["address"] == "324 EAST 112 STREET"
    assert packet["post_materialization_simulation"]["source_ready_fact_group_count"] == 82
    assert packet["post_materialization_simulation"]["safe_to_mark_verified_count"] == 0
    assert packet["predicted_if_approved"]["management_source_ready_fact_groups_added"] == 0
    assert packet["approval_steps"][0]["command"].endswith("--execute --confirm-execute --indent 2")
    assert "--source building_management --source hpd_contact_role_links" in packet["approval_steps"][1]["command"]
    assert "identifies manager-specific external evidence" in packet["blocked_business_use_reason"]


def test_source_overlap_approval_packet_summarizes_exact_recording_boundary():
    mutation_scope = {
        "allowed_tables": [
            "truth_materialization_manifest",
            "truth_claims",
            "truth_evidence",
            "confidence_snapshots",
        ],
        "forbidden_side_effects": {
            "will_mark_verified": False,
            "will_create_or_refresh_source_data": False,
            "will_materialize_building_management_relationships": False,
            "will_start_jobs": False,
            "will_allow_business_use": False,
        },
    }
    packet = build_source_overlap_approval_packet(
        run_id="truth-source-overlap-approval-test",
        schema_status={
            "ready": True,
            "current_revision": "010_truth_manifest",
            "expected_revision": "010_truth_manifest",
            "migration_current": True,
        },
        adjudication_preview={
            "verification_candidate_count": 0,
            "ledger_source_overlap": {
                "total_fact_group_count": 2063,
                "single_source_fact_group_count": 2063,
                "multi_source_fact_group_count": 0,
                "source_ready_fact_group_count": 0,
            },
            "manager_external_source_acquisition_preview": {
                "source_ready_if_recorded_count": 13,
                "strict_manager_source_ready_if_recorded_count": 4,
                "new_relationship_candidate_count": 2,
                "new_relationship_candidates": [
                    {
                        "candidate_id": "ny-dps-verizon-402-w-153-petition",
                        "source_name": "ny_dps_order_entry",
                        "source_family": "ny_dps_order_entry",
                        "external_address": "402 WEST 153 STREET",
                        "local_building_match": {
                            "bbl": "1020670047",
                            "address": "402 WEST 153 STREET",
                        },
                        "manager_name": "Harlem Property Management, Inc.",
                        "evidence_role": "managing_agent",
                        "source_url": "https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=test",
                        "safe_action": "Review as possible new relationship; do not count as current-ledger overlap.",
                    },
                    {
                        "candidate_id": "hpm-site-review-275-greenwich-management-takeover",
                        "source_name": "company_website",
                        "source_family": "company_website",
                        "external_address": "275 GREENWICH STREET",
                        "local_address": "269 GREENWICH STREET",
                        "local_building_match": {
                            "bbl": "1001327501",
                            "address": "269 GREENWICH STREET",
                        },
                        "manager_name": "Harlem Property Management, Inc.",
                        "evidence_role": "customer_review_management_takeover",
                        "source_url": "https://example.com/hpm-review",
                        "safe_action": "Review-gated company-site context; not current operating-manager proof.",
                    },
                ],
                "claim_groups": [
                    {
                        "fact_key": {"object_id": "1010460054"},
                        "address": "342 WEST 56 STREET",
                        "supporting_source_families_if_recorded": [
                            "external_web_profile",
                            "real_estate_listing",
                        ],
                        "manager_proof_source_families_if_recorded": [
                            "external_web_profile",
                            "real_estate_listing",
                        ],
                        "source_ready_if_recorded": True,
                        "independent_source_ready_if_recorded": True,
                        "strict_manager_source_ready_if_recorded": True,
                    },
                    {
                        "fact_key": {"object_id": "1019080014"},
                        "address": "141 WEST 123 STREET",
                        "supporting_source_families_if_recorded": [
                            "external_web_profile",
                            "hpd_registration_derived",
                        ],
                        "manager_proof_source_families_if_recorded": [
                            "external_web_profile",
                        ],
                        "source_ready_if_recorded": True,
                        "independent_source_ready_if_recorded": True,
                        "strict_manager_source_ready_if_recorded": False,
                    },
                ],
                "next_source_batches": {
                    "proposals": [{
                        "bbl": "1019080014",
                        "suggested_source_families": ["ny_dps_order_entry", "company_website"],
                        "first_search_query": '"Harlem Property Management" "141 West 123 Street"',
                    }],
                },
            },
            "operator_confirmed_management_preview": {
                "source_ready_if_recorded_count": 4,
                "strict_manager_source_ready_if_recorded_count": 1,
                "candidates": [
                    {
                        "candidate_id": "operator-confirmed-md-squared-220-3-ave",
                        "matched_building": {"address": "220 3 AVENUE"},
                        "matched_lead": {"company_name": "MD Squared Property Group"},
                        "strict_manager_gap_status": "broad_source_ready_not_strict",
                        "missing_manager_proof_source_family_count": 1,
                        "strict_manager_gap_reason": (
                            "Broad source-ready only because RentHistory is HPD-registration-derived."
                        ),
                        "next_required_manager_proof": (
                            "Acquire one exact non-HPD manager-proof source family."
                        ),
                    },
                    {
                        "candidate_id": "operator-confirmed-daisy-9-prospect-park-w",
                        "matched_building": {"address": "9 PROSPECT PARK WEST"},
                        "matched_lead": {"company_name": "Daisy Management"},
                        "strict_manager_gap_status": "strict_manager_proof_ready_if_recorded",
                        "missing_manager_proof_source_family_count": 0,
                    },
                ],
            },
        },
        manager_batch={
            "run_type": "manager_external_evidence_batch",
            "batch_filter": "strict_manager_proof",
            "template_count": 15,
            "claim_group_count": 4,
            "included_bbls": ["1010460054"],
            "included_addresses": ["342 WEST 56 STREET"],
            "source_names": ["openigloo", "renthop", "zillow"],
            "source_families": [
                "external_web_profile",
                "hpd_registration_derived",
                "ny_dps_order_entry",
                "real_estate_listing",
            ],
            "manager_proof_source_families": [
                "external_web_profile",
                "ny_dps_order_entry",
                "real_estate_listing",
            ],
            "planned_upsert_count": 45,
            "rollback_preview": {"new_claim_count": 15, "manifest_entry_count": 45},
            "sample_manual_evidence_previews": [
                {
                    "claim_id": "claim-hpm-342",
                    "evidence_id": "evidence-hpm-342",
                    "predicate": "manages_building",
                    "object_id": "1010460054",
                    "source_name": "openigloo",
                    "mutations_planned": 3,
                    "allowed_execute": False,
                    "mutation_scope": mutation_scope,
                },
            ],
            "post_recording_simulation": {
                "multi_source_fact_group_count": 4,
                "source_ready_fact_group_count": 4,
                "strict_manager_source_ready_fact_group_count": 4,
                "safe_to_mark_verified_count": 0,
                "blocker_counts": {"confidence_below_verified_threshold": 4},
            },
            "approval_decision_summary": {
                "approval_required": True,
                "batch_filter": "strict_manager_proof",
                "would_record_template_count": 15,
                "would_record_claim_group_count": 4,
                "would_plan_upsert_count": 45,
                "expected_strict_manager_source_ready_fact_group_count": 4,
                "expected_safe_to_mark_verified_count": 0,
                "single_source_claims_stay_unverified": True,
                "will_mark_verified": False,
                "will_create_or_refresh_source_data": False,
                "will_materialize_new_relationships": False,
            },
            "claim_group_review_summary": [{
                "bbl": "1010460054",
                "address": "342 WEST 56 STREET",
                "template_count": 4,
                "manager_proof_source_families": ["external_web_profile", "real_estate_listing"],
                "strict_manager_source_ready_if_recorded": True,
            }],
            "recommended_execute_command": (
                "python scripts/truth_manager_external_evidence_batch.py "
                "--strict-manager-proof-only --execute --confirm-execute --indent 2"
            ),
        },
        operator_batch={
            "run_type": "operator_confirmed_management_evidence_batch",
            "batch_filter": "strict_manager_proof",
            "template_count": 3,
            "claim_group_count": 1,
            "included_candidate_count": 1,
            "excluded_non_strict_candidate_count": 1,
            "excluded_non_strict_candidates": [{
                "candidate_id": "operator-confirmed-md-squared-220-3-ave",
                "address": "220 3 AVENUE",
                "manager_name": "MD Squared Property Group",
                "reason": "strict_manager_proof_filter",
                "strict_manager_gap_status": "broad_source_ready_not_strict",
                "missing_manager_proof_source_family_count": 1,
                "next_required_manager_proof": "Acquire one exact non-HPD manager-proof source family.",
            }],
            "source_names": ["outreach_confirmed", "homes", "redfin"],
            "source_families": ["operator_confirmed", "real_estate_listing"],
            "manager_proof_source_families": ["operator_confirmed", "real_estate_listing"],
            "planned_upsert_count": 9,
            "rollback_preview": {"new_claim_count": 3, "manifest_entry_count": 9},
            "sample_manual_evidence_previews": [
                {
                    "claim_id": "claim-operator-9ppw",
                    "evidence_id": "evidence-operator-9ppw",
                    "predicate": "manages_building",
                    "object_id": "3010680037",
                    "source_name": "outreach_confirmed",
                    "mutations_planned": 3,
                    "allowed_execute": False,
                    "mutation_scope": mutation_scope,
                },
            ],
            "post_recording_simulation": {
                "multi_source_fact_group_count": 1,
                "source_ready_fact_group_count": 1,
                "strict_manager_source_ready_fact_group_count": 1,
                "safe_to_mark_verified_count": 0,
                "blocker_counts": {"confidence_below_verified_threshold": 1},
            },
            "approval_decision_summary": {
                "approval_required": True,
                "batch_filter": "strict_manager_proof",
                "recommended_execute_command": (
                    "python scripts/truth_operator_confirmed_evidence_batch.py "
                    "--strict-manager-proof-only --execute --confirm-execute --indent 2"
                ),
                "would_record_template_count": 3,
                "would_record_claim_group_count": 1,
                "would_plan_upsert_count": 9,
                "included_addresses": ["9 PROSPECT PARK WEST"],
                "expected_multi_source_fact_group_count": 1,
                "expected_source_ready_fact_group_count": 1,
                "expected_strict_manager_source_ready_fact_group_count": 1,
                "expected_safe_to_mark_verified_count": 0,
                "single_source_claims_stay_unverified": True,
                "will_mark_verified": False,
                "will_create_or_refresh_source_data": False,
                "will_materialize_new_relationships": False,
            },
            "claim_group_review_summary": [{
                "bbl": "3010680037",
                "address": "9 PROSPECT PARK WEST",
                "template_count": 3,
                "manager_proof_source_families_if_recorded": [
                    "operator_confirmed",
                    "real_estate_listing",
                ],
                "strict_manager_source_ready_if_recorded": True,
            }],
            "recommended_execute_command": (
                "python scripts/truth_operator_confirmed_evidence_batch.py "
                "--strict-manager-proof-only --execute --confirm-execute --indent 2"
            ),
        },
    )

    assert packet["dry_run"] is True
    assert packet["mutations_planned"] == 0
    assert packet["current_ledger"]["multi_source_fact_group_count"] == 0
    assert packet["current_ledger"]["source_ready_fact_group_count"] == 0
    assert packet["source_overlap_recording_gate"]["status"] == "approval_required"
    assert packet["source_overlap_recording_gate"]["source_overlap_proof_satisfied"] is False
    assert packet["previewed_overlap_if_approved"]["manager_strict_source_ready_if_recorded_count"] == 4
    assert packet["previewed_overlap_if_approved"]["safe_to_mark_verified_after_recording"] == 0
    assert packet["recommended_first_packet"]["template_count"] == 15
    assert packet["recommended_first_packet"]["planned_upsert_count_if_approved"] == 45
    assert packet["recommended_first_packet"]["manager_proof_source_families"] == [
        "external_web_profile",
        "ny_dps_order_entry",
        "real_estate_listing",
    ]
    assert packet["recommended_first_packet"]["approval_decision_summary"]["would_plan_upsert_count"] == 45
    assert packet["recommended_first_packet"]["approval_decision_summary"]["will_mark_verified"] is False
    assert packet["recommended_first_packet"]["approval_decision_summary"]["will_create_or_refresh_source_data"] is False
    assert packet["recommended_first_packet"]["approval_decision_summary"]["will_materialize_new_relationships"] is False
    assert packet["recommended_first_packet"]["sample_manual_evidence_previews"][0]["mutation_scope"] == mutation_scope
    assert packet["recommended_first_packet"]["sample_manual_evidence_previews"][0]["mutation_scope"][
        "forbidden_side_effects"
    ]["will_mark_verified"] is False
    assert packet["recommended_first_packet"]["sample_manual_evidence_previews"][0]["mutation_scope"][
        "forbidden_side_effects"
    ]["will_materialize_building_management_relationships"] is False
    assert packet["recommended_first_packet"]["claim_group_review_summary"][0]["verification_score_if_recorded"] is None
    assert packet["manager_strict_gap_summary"]["broad_source_ready_not_strict_count"] == 1
    assert packet["manager_strict_gap_summary"]["strict_ready_claim_group_count"] == 1
    assert packet["manager_strict_gap_summary"]["gap_candidates"][0]["address"] == "141 WEST 123 STREET"
    assert packet["manager_strict_gap_summary"]["gap_candidates"][0]["suggested_source_families"] == [
        "ny_dps_order_entry",
        "company_website",
    ]
    assert packet["manager_new_relationship_candidate_summary"]["candidate_count"] == 2
    assert packet["manager_new_relationship_candidate_summary"]["counts_as_current_ledger_overlap"] is False
    assert packet["manager_new_relationship_candidate_summary"]["approval_required_for_relationship_creation"] is True
    assert packet["manager_new_relationship_candidate_summary"]["source_family_counts"] == {
        "company_website": 1,
        "ny_dps_order_entry": 1,
    }
    assert packet["manager_new_relationship_candidate_summary"]["candidates"][0] == {
        "candidate_id": "ny-dps-verizon-402-w-153-petition",
        "source_name": "ny_dps_order_entry",
        "source_family": "ny_dps_order_entry",
        "external_address": "402 WEST 153 STREET",
        "local_address": "402 WEST 153 STREET",
        "bbl": "1020670047",
        "manager_name": "Harlem Property Management, Inc.",
        "evidence_role": "managing_agent",
        "source_url": "https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=test",
        "safe_action": "Review as possible new relationship; do not count as current-ledger overlap.",
    }
    assert "do not count as current-ledger source overlap" in packet[
        "manager_new_relationship_candidate_summary"
    ]["safe_action"]
    assert packet["operator_strict_packet"]["claim_group_count"] == 1
    assert packet["operator_strict_packet"]["approval_decision_summary"]["would_plan_upsert_count"] == 9
    assert packet["operator_strict_packet"]["approval_decision_summary"]["will_materialize_new_relationships"] is False
    assert packet["operator_strict_packet"]["sample_manual_evidence_previews"][0]["mutation_scope"] == mutation_scope
    assert packet["operator_strict_packet"]["excluded_non_strict_candidate_count"] == 1
    assert packet["operator_strict_packet"]["excluded_non_strict_candidates"][0]["address"] == "220 3 AVENUE"
    assert packet["operator_strict_gap_summary"]["broad_source_ready_not_strict_count"] == 1
    assert packet["operator_strict_gap_summary"]["strict_ready_candidate_count"] == 1
    assert packet["operator_strict_gap_summary"]["gap_candidates"][0]["address"] == "220 3 AVENUE"
    assert packet["operator_strict_gap_summary"]["gap_candidates"][0]["strict_manager_gap_status"] == (
        "broad_source_ready_not_strict"
    )
    assert packet["approval_policy"]["single_source_claims_stay_unverified"] is True
    assert "--confirm-execute" in packet["recommended_first_packet"]["recommended_execute_command"]
    assert "Actual ledger source overlap is still zero" in packet["blocked_business_use_reason"]
    assert packet["post_execution_required_checks"][0].startswith("python scripts/truth_adjudication_preview.py")
    assert packet["post_execution_required_checks"][1] == (
        "python scripts/truth_source_overlap_post_recording_check.py --indent 2"
    )


def test_source_overlap_recording_gate_marks_current_ledger_proof_satisfied():
    gate = _source_overlap_recording_gate(
        ledger={
            "multi_source_fact_group_count": 13,
            "source_ready_fact_group_count": 13,
            "verification_candidate_count": 0,
        }
    )

    assert gate["status"] == "satisfied"
    assert gate["source_overlap_proof_satisfied"] is True
    assert gate["additional_evidence_recording_requires_approval"] is True
    assert "do not rerun an evidence batch just to satisfy the source-overlap gate" in gate["safe_action"]


def test_source_overlap_approval_packet_marks_existing_operator_evidence_repair_only():
    def existing_batch(label: str, template_count: int) -> dict[str, Any]:
        return {
            "run_type": label,
            "batch_filter": "strict_manager_proof",
            "template_count": template_count,
            "claim_group_count": 1,
            "planned_upsert_count": template_count * 3,
            "rollback_preview": {
                "new_claim_count": 0,
                "updated_claim_count": template_count,
                "new_evidence_count": 0,
                "updated_evidence_count": template_count,
                "new_confidence_snapshot_count": template_count,
                "updated_confidence_snapshot_count": 0,
                "manifest_entry_count": template_count * 3,
            },
            "post_recording_simulation": {
                "multi_source_fact_group_count": 1,
                "source_ready_fact_group_count": 1,
                "strict_manager_source_ready_fact_group_count": 1,
                "safe_to_mark_verified_count": 0,
            },
            "approval_decision_summary": {
                "will_mark_verified": False,
                "will_create_or_refresh_source_data": False,
                "will_materialize_new_relationships": False,
                "expected_safe_to_mark_verified_count": 0,
            },
            "claim_group_review_summary": [],
        }

    packet = build_source_overlap_approval_packet(
        run_id="truth-source-overlap-approval-existing-operator-test",
        schema_status={"ready": True, "current_revision": "010_truth_manifest", "expected_revision": "010_truth_manifest"},
        adjudication_preview={
            "verification_candidate_count": 0,
            "ledger_source_overlap": {
                "total_fact_group_count": 2078,
                "single_source_fact_group_count": 2063,
                "multi_source_fact_group_count": 15,
                "source_ready_fact_group_count": 15,
            },
            "manager_external_source_acquisition_preview": {},
            "operator_confirmed_management_preview": {},
        },
        manager_batch=existing_batch("manager_external_evidence_batch", 46),
        operator_batch=existing_batch("operator_confirmed_management_evidence_batch", 6),
    )

    assert packet["source_overlap_recording_gate"]["status"] == "satisfied"
    assert packet["recommended_first_packet"]["current_recording_status"] == "truth_ledger_evidence_already_recorded"
    assert packet["operator_strict_packet"]["current_recording_status"] == "truth_ledger_evidence_already_recorded"
    assert packet["operator_strict_packet"]["recording_effect_if_rerun"]["would_create_new_claim_count"] == 0
    assert packet["operator_strict_packet"]["recording_effect_if_rerun"]["would_update_existing_claim_count"] == 6
    assert "repair/review packet only" in packet["operator_strict_packet"]["safe_action"]
    assert packet["operator_strict_packet"]["approval_decision_summary"]["will_mark_verified"] is False


def test_post_recording_check_separates_current_ledger_from_preview_counts():
    failed = build_post_recording_check(
        ledger_source_overlap={
            "total_fact_group_count": 2063,
            "single_source_fact_group_count": 2063,
            "multi_source_fact_group_count": 0,
            "source_ready_fact_group_count": 0,
            "max_supporting_source_count": 1,
            "max_supporting_evidence_count": 1,
        },
        verified_single_source_summary={
            "verified_claim_count": 0,
            "verified_single_source_claim_count": 0,
            "sample_limit": 5,
            "samples": [],
        },
    )

    assert failed["dry_run"] is True
    assert failed["mutations_planned"] == 0
    assert failed["post_recording_success"] is False
    assert failed["checks"][0]["check"] == "actual_current_ledger_multi_source"
    assert failed["checks"][0]["status"] == "fail"
    assert "preview counts do not satisfy this gate" in failed["checks"][0]["reason"]
    assert failed["checks"][2]["status"] == "pass"

    passed = build_post_recording_check(
        ledger_source_overlap={
            "total_fact_group_count": 2063,
            "single_source_fact_group_count": 2059,
            "multi_source_fact_group_count": 4,
            "source_ready_fact_group_count": 4,
            "max_supporting_source_count": 2,
            "max_supporting_evidence_count": 3,
        },
        verified_single_source_summary={
            "verified_claim_count": 0,
            "verified_single_source_claim_count": 0,
            "sample_limit": 5,
            "samples": [],
        },
    )

    assert passed["post_recording_success"] is True
    assert {check["status"] for check in passed["checks"]} == {"pass"}

    unsafe_verified = build_post_recording_check(
        ledger_source_overlap={
            "total_fact_group_count": 2063,
            "single_source_fact_group_count": 2059,
            "multi_source_fact_group_count": 4,
            "source_ready_fact_group_count": 4,
            "max_supporting_source_count": 2,
            "max_supporting_evidence_count": 3,
        },
        verified_single_source_summary={
            "verified_claim_count": 1,
            "verified_single_source_claim_count": 1,
            "sample_limit": 5,
            "samples": [{"claim_id": "bad-verified", "supporting_source_count": 1}],
        },
    )

    assert unsafe_verified["post_recording_success"] is False
    assert unsafe_verified["checks"][2]["check"] == "no_single_source_verified_claims"
    assert unsafe_verified["checks"][2]["status"] == "fail"


@pytest.mark.anyio
async def test_verified_single_source_summary_is_read_only():
    session = FakeAsyncSession([
        FakeExecuteResult(rows=[{
            "verified_claim_count": 2,
            "verified_single_source_claim_count": 1,
        }]),
        FakeExecuteResult(rows=[{
            "claim_id": "verified-single-source",
            "subject_type": "lead",
            "subject_id": "lead-1",
            "predicate": "manages_building",
            "object_type": "building",
            "object_id": "1000000001",
            "normalized_value": "manager",
            "claim_type": "building_management",
            "confidence_score": 0.95,
            "supporting_source_count": 1,
            "supporting_sources": ["outreach_confirmed"],
        }]),
    ])

    summary = await load_verified_single_source_summary(session, sample_limit=3)

    assert summary["verified_claim_count"] == 2
    assert summary["verified_single_source_claim_count"] == 1
    assert summary["sample_limit"] == 3
    assert summary["samples"][0]["claim_id"] == "verified-single-source"
    assert summary["samples"][0]["supporting_source_count"] == 1


@pytest.mark.anyio
async def test_role_overlap_post_materialization_simulation_groups_exact_proposed_sources():
    session = FakeAsyncSession([
        FakeExecuteResult(rows=[{
            "id": 11,
            "lead_id": "lead-1",
            "bbl": "1000000001",
            "role": "agent",
            "registration_start": None,
            "registration_end": None,
            "observed_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
        }]),
        FakeExecuteResult(rows=[{
            "id": 22,
            "bbl": "1000000001",
            "registration_contact_id": "rc-1",
            "registration_id": "reg-1",
            "contact_type": "Agent",
            "description": None,
            "corporation_name": "Example Property Management LLC",
            "first_name": None,
            "last_name": None,
            "title": None,
            "business_address": "123 Main St",
            "business_city": "New York",
            "business_state": "NY",
            "business_zip": "10001",
            "observed_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
            "building_management_id": 11,
            "building_management_role": "agent",
            "lead_id": "lead-1",
            "lead_normalized_name": "Example Property Management Inc",
            "lead_company_name": "Example Property Management Inc",
            "lead_agent_name": None,
            "lead_owner_name": None,
        }]),
    ])

    preview = await simulate_role_overlap_post_materialization(session, limit=100)

    assert preview["dry_run"] is True
    assert preview["mutations_planned"] == 0
    assert preview["planned_claim_spec_count"] == 2
    assert preview["simulated_fact_group_count"] == 1
    assert preview["multi_source_fact_group_count"] == 1
    assert preview["source_ready_fact_group_count"] == 1
    assert preview["source_ready_count_by_predicate"] == {"registered_agent_for_building": 1}
    assert preview["safe_to_mark_verified_count"] in {0, 1}
    assert preview["samples"][0]["supporting_sources"] == ["building_management", "hpd_contacts"]


@pytest.mark.anyio
async def test_role_claim_correction_preview_flags_stale_agent_manager_claims():
    session = FakeAsyncSession([
        FakeExecuteResult(rows=[{
            "claim_id": "claim-stale-agent",
            "subject_type": "lead",
            "subject_id": "0ff794d3ba2d",
            "predicate": "manages_building",
            "object_type": "building",
            "object_id": "1018250029",
            "normalized_value": "agent",
            "claim_type": "building_management",
            "belief_status": "likely",
            "confidence_score": 0.72,
            "actionability_level": "automated_enrichment",
            "observed_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
            "evidence_ids": ["evidence-stale-agent"],
            "source_names": ["building_management"],
            "source_record_ids": ["building_management:99"],
            "source_roles": ["agent"],
        }]),
    ])

    preview = await load_role_claim_correction_preview(session)

    assert preview["dry_run"] is True
    assert preview["mutations_planned"] == 0
    assert preview["requires_operator_approval"] is True
    assert preview["sampled_stale_claim_count"] == 1
    sample = preview["samples"][0]
    assert sample["recommended_change"]["operation"] == "set_current_flag_false"
    assert sample["recommended_change"]["replacement_claim_type"] == "registered_agent"


@pytest.mark.anyio
async def test_role_claim_correction_apply_defaults_to_preview_with_manifest_plan():
    session = FakeAsyncSession([
        FakeExecuteResult(rows=[{
            "claim_id": "claim-stale-agent",
            "subject_type": "lead",
            "subject_id": "0ff794d3ba2d",
            "predicate": "manages_building",
            "object_type": "building",
            "object_id": "1018250029",
            "normalized_value": "agent",
            "claim_type": "building_management",
            "belief_status": "likely",
            "confidence_score": 0.72,
            "actionability_level": "automated_enrichment",
            "observed_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
            "evidence_ids": ["evidence-stale-agent"],
            "source_names": ["building_management"],
            "source_record_ids": ["building_management:99"],
            "source_roles": ["agent"],
        }]),
        FakeExecuteResult(rows=[{"existing_id": "claim-stale-agent"}]),
        FakeExecuteResult(rows=[{
            "claim_id": "claim-stale-agent",
            "belief_status": "likely",
            "confidence_score": 0.72,
            "actionability_level": "automated_enrichment",
            "current_flag": True,
            "rationale": {"materialized_from": "building_management"},
            "updated_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
        }]),
    ])

    result = await preview_or_apply_role_claim_corrections(
        session,
        dry_run=True,
        confirm_execute=False,
        run_id="role-correction-preview",
    )

    assert result["dry_run"] is True
    assert result["allowed_execute"] is False
    assert result["mutations_planned"] == 1
    assert result["candidate_summary"]["claim_update_count"] == 1
    assert result["proposed_database_changes"][0]["current_flag"] is False
    assert result["proposed_database_changes"][0]["belief_status"] == "superseded"
    assert result["rollback_manifest"]["by_type"]["truth_claim"]["existing"] == 1


def test_actionability_rules_require_freshness_and_supporting_evidence():
    assert actionability_level(
        score=0.92,
        contradictions=0,
        freshness_days=30,
        supporting_source_count=2,
        supporting_evidence_count=2,
    ) == "acquisition_quality_diligence"
    assert actionability_level(
        score=0.74,
        contradictions=1,
        freshness_days=200,
        supporting_source_count=1,
        supporting_evidence_count=1,
    ) == "automated_enrichment"
    assert actionability_level(
        score=0.92,
        contradictions=0,
        freshness_days=300,
        supporting_source_count=1,
        supporting_evidence_count=1,
    ) == "broad_discovery"
    assert actionability_level(
        score=0.99,
        contradictions=0,
        freshness_days=1,
        supporting_source_count=0,
        supporting_evidence_count=0,
    ) == "do_not_act"
    assert actionability_level(
        score=0.92,
        contradictions=0,
        freshness_days=30,
        supporting_source_count=1,
        supporting_evidence_count=1,
    ) == "recommended_outreach"


def test_review_bucket_keeps_conflicts_out_of_safe_auto_accept():
    assert review_bucket(confidence_score=0.95, contradictions=0, safe_to_execute=True) == "safe_auto_accept"
    assert review_bucket(confidence_score=0.95, contradictions=1, safe_to_execute=True) == "conflicting_evidence"


def test_truth_health_report_flags_unready_trust_surfaces():
    report = evaluate_truth_health_outputs(
        dashboard={
            "claim_count": 0,
            "verified_claim_count": 0,
            "conflicting_claim_count": 0,
            "open_review_count": 0,
        },
        materialization_preview={
            "planned_claims_total": 12,
            "planned_claims_by_source": {"building_management": 7, "enrichment_observations": 5},
        },
        validation_preview={
            "checks": [{
                "check": "conflicting_enrichment_observations",
                "severity": "high",
                "count_sampled": 3,
                "recommended_queue": "conflicting_evidence",
                "why_it_matters": "Contact paths disagree.",
            }]
        },
        golden_benchmark={
            "seeded": True,
            "configured_cases": 1,
            "evaluable_cases": 0,
            "feature_coverage": {"missing_required_features": ["wrong contact"], "coverage": 0.9},
            "metrics": {"precision": None, "recall": 0.5},
        },
        source_audit={
            "dry_run": True,
            "mutations_planned": 0,
            "summary": {"total_sources": 2, "operational": 1, "schema_missing": 0, "no_recent_ingest": 0, "not_wired": 0, "stale_ingest": 1},
            "critical_gaps": [{"source_name": "dob_permits", "status": "schema_missing"}],
            "sources": [],
        },
    )

    areas = {gap["area"] for gap in report["trust_gaps"]}
    assert report["dry_run"] is True
    assert report["mutations_planned"] == 0
    assert report["summary"]["trust_posture"] == "not_ready"
    assert "claim_ledger" in areas
    assert "claim_materialization" in areas
    assert "adversarial_validation:conflicting_enrichment_observations" in areas
    assert "golden_set" in areas
    assert "golden_evaluation" in areas
    assert "golden_feature_coverage" in areas
    assert "golden_metrics" in areas
    assert "source_audit" in areas
    assert report["source_audit"]["summary"]["operational"] == 1
    assert report["activation_checklist"][0]["step"] == "apply_truth_schema"
    assert report["activation_checklist"][0]["status"] == "complete"
    assert any(item["step"] == "execute_truth_materialization" and item["approval_required"] for item in report["activation_checklist"])


def test_activation_packet_summarizes_schema_approval_gate():
    preflight = {
        "ready_to_apply_additive_truth_migration": True,
        "schema_status": {
            "ready": False,
            "current_revision": "008_lead_lineage",
            "expected_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "missing_tables": ["truth_claims", "truth_evidence"],
        },
        "rollback_strategy": "Downgrade to 008_lead_lineage and drop additive truth tables.",
        "offline_rollback_sql": {"command": "python -m alembic downgrade 010_truth_manifest:008_lead_lineage --sql"},
    }
    health_report = {
        "summary": {
            "trust_posture": "not_ready",
            "configured_golden_cases": 7,
            "evaluable_golden_cases": 7,
        },
        "activation_checklist": [
            {
                "step": "apply_truth_schema",
                "status": "approval_required",
                "reason": "Apply additive migration 010_truth_manifest.",
                "approval_required": True,
                "mutations_planned": 6,
            },
            {
                "step": "allow_business_use",
                "status": "blocked",
                "reason": "Do not use for business decisions yet.",
                "approval_required": False,
                "mutations_planned": 0,
            },
        ],
        "source_refresh_plan": {
            "approval_required": True,
            "summary": {
                "planned_job_count": 14,
                "refreshable_job_count": 13,
                "blocked_job_count": 1,
                "affected_source_count": 20,
                "non_refreshable_gap_count": 1,
            },
            "items": [{
                "job_type": "acris",
                "reason": "no_recent_ingest",
                "priority": 30,
                "blocked": False,
                "approval_required": True,
                "preview_endpoint": "/api/v1/jobs/acris/start",
                "execute_endpoint": "/api/v1/jobs/acris/start?dry_run=false&confirm_execute=true",
                "sources": [{"source_name": "acris_transactions"}],
            }, {
                "job_type": "dob_permits",
                "reason": "schema_missing",
                "priority": 10,
                "blocked": True,
                "approval_required": False,
                "preview_endpoint": None,
                "execute_endpoint": None,
                "sources": [{"source_name": "dob_permits", "status": "schema_missing"}],
            }],
        },
        "trust_gaps": [{
            "severity": "critical",
            "area": "schema_readiness",
            "message": "Truth tables are missing.",
        }],
    }

    packet = build_activation_packet(preflight=preflight, health_report=health_report)

    assert packet["dry_run"] is True
    assert packet["mutations_planned"] == 0
    assert packet["verdict"] == "schema_approval_required"
    assert packet["business_use_allowed"] is False
    assert packet["schema"]["missing_tables"] == ["truth_claims", "truth_evidence"]
    assert packet["approval_required"] is True
    assert packet["approval_steps"][0]["step"] == "apply_truth_schema"
    assert packet["source_refresh"]["planned_job_count"] == 14
    assert packet["source_refresh"]["refreshable_job_count"] == 13
    assert packet["source_refresh"]["blocked_job_count"] == 1
    assert packet["source_refresh"]["next_jobs"][0]["job_type"] == "acris"
    assert packet["source_refresh"]["next_jobs"][0]["execute_endpoint"].endswith("confirm_execute=true")
    assert packet["source_refresh"]["next_jobs"][0]["sources"] == [{
        "source_name": "acris_transactions",
        "status": None,
        "source_age_days": None,
    }]
    assert packet["source_refresh"]["next_jobs"][1]["blocked"] is True
    assert packet["source_refresh"]["next_jobs"][1]["approval_required"] is False
    assert packet["source_refresh"]["next_jobs"][1]["execute_endpoint"] is None
    assert packet["rollback"]["offline_rollback_command"].endswith("--sql")
    next_steps = {step["step"]: step for step in packet["next_safe_steps"]}
    assert next_steps["review_materialization_evidence_after_schema"]["mutates_data"] is False
    assert "sample_materialized_claim_specs" in next_steps["review_materialization_evidence_after_schema"]["command"]
    assert next_steps["execute_materialization_after_review"]["blocked_until"] == "review_materialization_evidence_after_schema"
    assert next_steps["review_verification_frontier"]["mutates_data"] is False
    assert "truth_verification_frontier.py" in next_steps["review_verification_frontier"]["command"]
    assert next_steps["review_source_refresh_plan"]["mutates_data"] is False
    assert next_steps["execute_approved_source_refresh_jobs"]["requires_explicit_approval"] is True
    assert next_steps["execute_approved_source_refresh_jobs"]["blocked_until"] == "review_source_refresh_plan"
    assert any(step["mutates_data"] is True and step["requires_explicit_approval"] for step in packet["next_safe_steps"])
    for step in packet["next_safe_steps"]:
        if step.get("mutates_data"):
            assert step.get("requires_explicit_approval") is True
            assert "dry_run=false" not in step["command"] or "confirm_execute=true" in step["command"]
    for job in packet["source_refresh"]["next_jobs"]:
        if job["blocked"]:
            assert job["execute_endpoint"] is None
            continue
        assert job["approval_required"] is True
        assert "dry_run=false" not in job["preview_endpoint"]
        assert "dry_run=false" in job["execute_endpoint"]
        assert "confirm_execute=true" in job["execute_endpoint"]


def test_activation_packet_blocks_business_use_when_source_refresh_still_needs_approval():
    preflight = {
        "ready_to_apply_additive_truth_migration": False,
        "schema_status": {
            "ready": True,
            "current_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "expected_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "missing_tables": [],
        },
        "rollback_strategy": "Downgrade additive truth tables if activation is abandoned.",
        "offline_rollback_sql": {"command": "python -m alembic downgrade 010_truth_manifest:008_lead_lineage --sql"},
    }
    health_report = {
        "summary": {
            "trust_posture": "usable_for_review",
            "configured_golden_cases": 7,
            "evaluable_golden_cases": 7,
        },
        "activation_checklist": [
            {
                "step": "allow_business_use",
                "status": "complete",
                "reason": "All non-source gates are complete.",
                "approval_required": False,
                "mutations_planned": 0,
            },
        ],
        "source_refresh_plan": {
            "approval_required": True,
            "summary": {
                "planned_job_count": 1,
                "refreshable_job_count": 1,
                "blocked_job_count": 0,
                "affected_source_count": 1,
                "non_refreshable_gap_count": 0,
            },
            "items": [{
                "job_type": "dob_permits",
                "reason": "stale_ingest",
                "priority": 40,
                "blocked": False,
                "approval_required": True,
                "preview_endpoint": "/api/v1/jobs/dob_permits/start",
                "execute_endpoint": "/api/v1/jobs/dob_permits/start?dry_run=false&confirm_execute=true",
                "sources": [{"source_name": "dob_permits", "status": "stale_ingest"}],
            }],
        },
        "trust_gaps": [],
    }

    packet = build_activation_packet(preflight=preflight, health_report=health_report)

    assert packet["business_use_allowed"] is False
    assert packet["verdict"] == "materialization_or_review_required"
    assert packet["source_refresh"]["approval_required"] is True
    next_steps = {step["step"]: step for step in packet["next_safe_steps"]}
    assert "apply_truth_schema" not in next_steps
    assert next_steps["preview_materialization"]["mutates_data"] is False
    assert next_steps["review_materialization_evidence"]["blocked_until"] == "preview_materialization"
    assert next_steps["execute_materialization_after_review"]["blocked_until"] == "review_materialization_evidence"
    assert next_steps["review_source_refresh_plan"]["mutates_data"] is False
    assert next_steps["execute_approved_source_refresh_jobs"]["blocked_until"] == "review_source_refresh_plan"


def test_activation_packet_blocks_business_use_when_source_refresh_has_blocked_or_manual_gaps():
    preflight = {
        "ready_to_apply_additive_truth_migration": False,
        "schema_status": {
            "ready": True,
            "current_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "expected_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "missing_tables": [],
        },
        "rollback_strategy": "Downgrade additive truth tables if activation is abandoned.",
        "offline_rollback_sql": {"command": "python -m alembic downgrade 010_truth_manifest:008_lead_lineage --sql"},
    }
    health_report = {
        "summary": {
            "trust_posture": "usable_for_review",
            "configured_golden_cases": 7,
            "evaluable_golden_cases": 7,
        },
        "activation_checklist": [{
            "step": "allow_business_use",
            "status": "complete",
            "reason": "All non-source gates are complete.",
            "approval_required": False,
            "mutations_planned": 0,
        }],
        "source_refresh_plan": {
            "approval_required": False,
            "summary": {
                "planned_job_count": 1,
                "refreshable_job_count": 0,
                "blocked_job_count": 1,
                "affected_source_count": 2,
                "non_refreshable_gap_count": 1,
            },
            "items": [{
                "job_type": "dob_permits",
                "reason": "schema_missing",
                "priority": 10,
                "blocked": True,
                "approval_required": False,
                "preview_endpoint": None,
                "execute_endpoint": None,
                "sources": [{"source_name": "dob_permits", "status": "schema_missing"}],
            }],
        },
        "trust_gaps": [],
    }

    packet = build_activation_packet(preflight=preflight, health_report=health_report)

    assert packet["approval_required"] is False
    assert packet["source_refresh"]["approval_required"] is False
    assert packet["source_refresh"]["refreshable_job_count"] == 0
    assert packet["source_refresh"]["blocked_job_count"] == 1
    assert packet["business_use_allowed"] is False
    assert packet["verdict"] == "materialization_or_review_required"


def test_activation_packet_requires_all_activation_gates_complete_for_business_use():
    preflight = {
        "ready_to_apply_additive_truth_migration": False,
        "schema_status": {
            "ready": True,
            "current_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "expected_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "missing_tables": [],
        },
        "rollback_strategy": "Downgrade additive truth tables if activation is abandoned.",
        "offline_rollback_sql": {"command": "python -m alembic downgrade 010_truth_manifest:008_lead_lineage --sql"},
    }
    health_report = {
        "summary": {
            "trust_posture": "monitor",
            "configured_golden_cases": 7,
            "evaluable_golden_cases": 7,
        },
        "activation_checklist": [{
            "step": "allow_business_use",
            "status": "complete",
            "reason": "Final row alone is not enough.",
            "approval_required": False,
            "mutations_planned": 0,
        }],
        "source_refresh_plan": {
            "approval_required": False,
            "summary": {
                "planned_job_count": 0,
                "refreshable_job_count": 0,
                "blocked_job_count": 0,
                "affected_source_count": 0,
                "non_refreshable_gap_count": 0,
            },
            "items": [],
        },
        "adjudication_preview": {
            "verification_candidate_count": 0,
            "ledger_source_overlap": {
                "total_fact_group_count": 2078,
                "single_source_fact_group_count": 2063,
                "multi_source_fact_group_count": 15,
                "source_ready_fact_group_count": 15,
            },
            "verified_confidence_gap_plan": {
                "proposal_count": 10,
                "single_source_upgrade_would_verify_count": 2,
                "bundle_upgrade_would_verify_count": 10,
            },
            "verification_gap_plan": {"proposal_count": 5},
            "manager_external_source_acquisition_preview": {
                "next_source_batches": {"candidate_count": 1},
            },
            "operator_confirmed_management_preview": {
                "second_source_seed_batches": {"candidate_count": 4},
            },
        },
        "trust_gaps": [],
    }

    packet = build_activation_packet(preflight=preflight, health_report=health_report)

    assert packet["business_use_allowed"] is False
    assert packet["verdict"] == "materialization_or_review_required"


def test_activation_packet_allows_business_use_only_when_every_activation_gate_is_complete():
    preflight = {
        "ready_to_apply_additive_truth_migration": False,
        "schema_status": {
            "ready": True,
            "current_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "expected_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "missing_tables": [],
        },
        "rollback_strategy": "Downgrade additive truth tables if activation is abandoned.",
        "offline_rollback_sql": {"command": "python -m alembic downgrade 010_truth_manifest:008_lead_lineage --sql"},
    }
    health_report = {
        "summary": {
            "trust_posture": "monitor",
            "configured_golden_cases": 7,
            "evaluable_golden_cases": 7,
            "claim_count": 20,
            "verified_claim_count": 12,
            "critical_or_high_gap_count": 0,
        },
        "activation_checklist": [
            {
                "step": step,
                "status": "complete",
                "reason": "Gate verified.",
                "approval_required": False,
                "mutations_planned": 0,
            }
            for step in [
                "apply_truth_schema",
                "run_materialization_dry_run",
                "review_truth_outputs",
                "execute_truth_materialization",
                "refresh_or_record_sources",
                "allow_business_use",
            ]
        ],
        "source_refresh_plan": {
            "approval_required": False,
            "summary": {
                "planned_job_count": 0,
                "refreshable_job_count": 0,
                "blocked_job_count": 0,
                "affected_source_count": 0,
                "non_refreshable_gap_count": 0,
            },
            "items": [],
        },
        "adjudication_preview": {
            "verification_candidate_count": 0,
            "ledger_source_overlap": {
                "total_fact_group_count": 2078,
                "single_source_fact_group_count": 2063,
                "multi_source_fact_group_count": 15,
                "source_ready_fact_group_count": 15,
            },
            "verified_confidence_gap_plan": {
                "proposal_count": 10,
                "single_source_upgrade_would_verify_count": 2,
                "bundle_upgrade_would_verify_count": 10,
            },
            "verification_gap_plan": {
                "proposal_count": 10,
            },
            "manager_external_source_acquisition_preview": {
                "next_source_batches": {
                    "candidate_count": 1,
                },
            },
            "operator_confirmed_management_preview": {
                "second_source_seed_batches": {
                    "candidate_count": 4,
                },
            },
        },
        "trust_gaps": [],
    }

    packet = build_activation_packet(preflight=preflight, health_report=health_report)

    assert packet["business_use_allowed"] is True
    assert packet["verdict"] == "ready_for_business_use"
    assert packet["approval_required"] is False
    assert packet["claim_readiness"]["has_materialized_claims"] is True
    assert packet["claim_readiness"]["has_verified_claims"] is True


def test_activation_packet_blocks_business_use_without_verified_claim_evidence():
    preflight = {
        "ready_to_apply_additive_truth_migration": False,
        "schema_status": {
            "ready": True,
            "current_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "expected_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "missing_tables": [],
        },
        "rollback_strategy": "Downgrade additive truth tables if activation is abandoned.",
        "offline_rollback_sql": {"command": "python -m alembic downgrade 010_truth_manifest:008_lead_lineage --sql"},
    }
    health_report = {
        "summary": {
            "trust_posture": "monitor",
            "configured_golden_cases": 7,
            "evaluable_golden_cases": 7,
            "claim_count": 0,
            "verified_claim_count": 0,
            "critical_or_high_gap_count": 0,
        },
        "activation_checklist": [
            {
                "step": step,
                "status": "complete",
                "reason": "Gate verified.",
                "approval_required": False,
                "mutations_planned": 0,
            }
            for step in [
                "apply_truth_schema",
                "run_materialization_dry_run",
                "review_truth_outputs",
                "execute_truth_materialization",
                "refresh_or_record_sources",
                "allow_business_use",
            ]
        ],
        "source_refresh_plan": {
            "approval_required": False,
            "summary": {
                "planned_job_count": 0,
                "refreshable_job_count": 0,
                "blocked_job_count": 0,
                "affected_source_count": 0,
                "non_refreshable_gap_count": 0,
            },
            "items": [],
        },
        "adjudication_preview": {
            "verification_candidate_count": 0,
            "ledger_source_overlap": {
                "total_fact_group_count": 2078,
                "single_source_fact_group_count": 2063,
                "multi_source_fact_group_count": 15,
                "source_ready_fact_group_count": 15,
            },
            "verified_confidence_gap_plan": {
                "proposal_count": 10,
                "single_source_upgrade_would_verify_count": 2,
                "bundle_upgrade_would_verify_count": 10,
            },
            "verification_gap_plan": {
                "proposal_count": 10,
            },
            "manager_external_source_acquisition_preview": {
                "next_source_batches": {
                    "candidate_count": 1,
                },
            },
            "operator_confirmed_management_preview": {
                "second_source_seed_batches": {
                    "candidate_count": 4,
                },
            },
        },
        "trust_gaps": [],
    }

    packet = build_activation_packet(preflight=preflight, health_report=health_report)

    assert packet["business_use_allowed"] is False
    assert packet["verdict"] == "materialization_or_review_required"
    assert packet["claim_readiness"]["has_materialized_claims"] is False
    assert packet["claim_readiness"]["has_verified_claims"] is False
    frontier = packet["verification_frontier"]
    assert frontier["dry_run"] is True
    assert frontier["mutations_planned"] == 0
    assert frontier["verification_candidate_count"] == 0
    assert frontier["current_ledger"]["source_ready_fact_group_count"] == 15
    assert frontier["source_ready_below_verified_count"] == 10
    assert frontier["single_source_gap_count"] == 10
    assert frontier["single_source_upgrade_would_verify_count"] == 2
    assert frontier["bundle_upgrade_would_verify_count"] == 10
    assert frontier["manager_next_source_seed_count"] == 1
    assert frontier["operator_second_source_seed_count"] == 4
    assert frontier["evidence_acquisition_required"] is True
    assert "truth_verification_frontier.py" in frontier["next_preview_command"]
    assert "No facts are verified" in frontier["business_use_blocker"]


def test_activation_checklist_marks_materialization_complete_when_claims_are_current():
    checklist = build_activation_checklist(
        schema_status={"ready": True, "truth_tables_ready": True, "migration_current": True},
        summary={
            "claim_count": 42,
            "verified_claim_count": 40,
            "planned_claims_total": 0,
            "validation_check_count": 0,
            "critical_or_high_gap_count": 0,
            "trust_posture": "monitor",
        },
        source_refresh_plan={
            "summary": {
                "refreshable_job_count": 0,
                "blocked_job_count": 0,
                "non_refreshable_gap_count": 0,
            },
        },
    )

    statuses = {item["step"]: item["status"] for item in checklist}
    approvals = {item["step"]: item["approval_required"] for item in checklist}

    assert statuses == {
        "apply_truth_schema": "complete",
        "run_materialization_dry_run": "complete",
        "review_truth_outputs": "complete",
        "execute_truth_materialization": "complete",
        "refresh_or_record_sources": "complete",
        "allow_business_use": "complete",
    }
    assert approvals["execute_truth_materialization"] is False


def test_activation_checklist_blocks_business_use_without_verified_claim_readiness():
    checklist = build_activation_checklist(
        schema_status={"ready": True, "truth_tables_ready": True, "migration_current": True},
        summary={
            "claim_count": 25,
            "verified_claim_count": 0,
            "planned_claims_total": 0,
            "validation_check_count": 0,
            "critical_or_high_gap_count": 0,
            "trust_posture": "monitor",
        },
        source_refresh_plan={
            "summary": {
                "refreshable_job_count": 0,
                "blocked_job_count": 0,
                "non_refreshable_gap_count": 0,
            },
        },
    )

    allow_business_use = next(item for item in checklist if item["step"] == "allow_business_use")

    assert allow_business_use["status"] == "blocked"
    assert "materialized and verified claims" in allow_business_use["reason"]


def test_activation_checklist_blocks_business_use_with_source_gaps():
    checklist = build_activation_checklist(
        schema_status={"ready": True, "truth_tables_ready": True, "migration_current": True},
        summary={
            "claim_count": 25,
            "verified_claim_count": 12,
            "planned_claims_total": 0,
            "validation_check_count": 0,
            "critical_or_high_gap_count": 0,
            "trust_posture": "monitor",
        },
        source_refresh_plan={
            "summary": {
                "refreshable_job_count": 0,
                "blocked_job_count": 1,
                "non_refreshable_gap_count": 0,
            },
        },
    )

    allow_business_use = next(item for item in checklist if item["step"] == "allow_business_use")

    assert allow_business_use["status"] == "blocked"
    assert "source gaps are cleared" in allow_business_use["reason"]


def test_runtime_preflight_summary_is_api_safe_without_alembic_shellout():
    summary = build_runtime_preflight_summary({
        "ready": False,
        "current_revision": "008_lead_lineage",
        "expected_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
        "missing_tables": ["truth_claims"],
    })

    assert summary["dry_run"] is True
    assert summary["mutations_planned"] == 0
    assert summary["ready_to_apply_additive_truth_migration"] is True
    assert summary["offline_rollback_sql"]["command"].endswith("--sql")


def test_truth_health_report_flags_schema_revision_drift_even_when_tables_exist():
    report = evaluate_truth_health_outputs(
        dashboard={
            "claim_count": 10,
            "verified_claim_count": 9,
            "conflicting_claim_count": 0,
            "open_review_count": 0,
        },
        materialization_preview={"planned_claims_total": 0},
        validation_preview={"checks": []},
        golden_benchmark={
            "seeded": True,
            "configured_cases": len(GOLDEN_CASE_SEEDS),
            "evaluable_cases": len(GOLDEN_CASE_SEEDS),
            "feature_coverage": {"missing_required_features": [], "coverage": 1.0},
            "metrics": {"precision": 1.0, "recall": 1.0},
        },
        schema_status={
            "ready": True,
            "migration_current": False,
            "expected_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "current_revision": "010_future_revision",
            "revision_status": "schema_present_revision_differs",
        },
    )

    areas = {gap["area"] for gap in report["trust_gaps"]}
    assert "schema_revision" in areas
    assert report["schema_status"]["current_revision"] == "010_future_revision"
    assert report["summary"]["trust_posture"] == "not_ready"
    assert report["activation_checklist"][0]["step"] == "apply_truth_schema"
    assert report["activation_checklist"][0]["status"] == "approval_required"
    assert report["activation_checklist"][-1]["status"] == "blocked"


def test_schema_readiness_report_includes_source_audit_gaps():
    report = build_schema_readiness_report(
        schema_status={
            "ready": False,
            "current_revision": "008_lead_lineage",
            "missing_tables": REQUIRED_TRUTH_TABLES,
        },
        source_audit={
            "dry_run": True,
            "mutations_planned": 0,
            "summary": {"total_sources": 2, "operational": 1, "schema_missing": 0, "no_recent_ingest": 0, "not_wired": 0, "stale_ingest": 1},
            "critical_gaps": [{"source_name": "acris_transactions", "status": "stale_ingest", "source_age_days": 90}],
            "sources": [],
            "refresh_plan": {
                "summary": {
                    "refreshable_job_count": 1,
                    "non_refreshable_gap_count": 0,
                },
            },
        },
    )

    areas = {gap["area"] for gap in report["trust_gaps"]}
    assert report["summary"]["critical_or_high_gap_count"] == 2
    assert "schema_readiness" in areas
    assert "source_audit" in areas
    assert report["source_audit"]["summary"]["stale_ingest"] == 1
    assert report["activation_checklist"][0]["step"] == "apply_truth_schema"
    assert report["activation_checklist"][0]["status"] == "approval_required"
    assert report["activation_checklist"][0]["approval_required"] is True
    assert report["activation_checklist"][0]["mutations_planned"] == len(REQUIRED_TRUTH_TABLES)
    assert report["activation_checklist"][-1]["step"] == "allow_business_use"
    assert report["activation_checklist"][-1]["status"] == "blocked"


def test_schema_readiness_report_blocks_revision_drift_with_tables_present():
    schema_status = {
        "ready": True,
        "truth_tables_ready": True,
        "migration_current": False,
        "expected_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
        "current_revision": "010_future_revision",
        "revision_status": "schema_present_revision_differs",
        "missing_tables": [],
    }
    report = build_schema_readiness_report(schema_status=schema_status)

    assert is_truth_schema_current(schema_status) is False
    assert report["trust_gaps"][0]["area"] == "schema_revision"
    assert report["trust_gaps"][0]["severity"] == "high"
    assert report["activation_checklist"][0]["status"] == "approval_required"
    assert report["activation_checklist"][0]["mutations_planned"] == 0
    assert "revision differs" in report["activation_checklist"][0]["reason"]
    statuses = {item["step"]: item["status"] for item in report["activation_checklist"]}
    assert statuses["run_materialization_dry_run"] == "blocked"
    assert statuses["review_truth_outputs"] == "blocked"
    assert statuses["execute_truth_materialization"] == "blocked"
    assert report["activation_checklist"][-1]["status"] == "blocked"


def test_activation_checklist_tracks_manual_and_refreshable_source_gaps():
    checklist = build_activation_checklist(
        schema_status={"ready": True, "migration_current": True},
        summary={
            "trust_posture": "not_ready",
            "planned_claims_total": 18,
            "validation_check_count": 2,
            "critical_or_high_gap_count": 1,
        },
        source_refresh_plan={
            "summary": {
                "refreshable_job_count": 3,
                "non_refreshable_gap_count": 1,
            },
        },
    )

    by_step = {item["step"]: item for item in checklist}
    assert by_step["apply_truth_schema"]["status"] == "complete"
    assert by_step["review_truth_outputs"]["status"] == "needs_review"
    assert by_step["execute_truth_materialization"]["mutations_planned"] == 18
    assert by_step["refresh_or_record_sources"]["approval_required"] is True
    assert "1 evidence stream" in by_step["refresh_or_record_sources"]["reason"]
    assert by_step["allow_business_use"]["status"] == "blocked"


def test_truth_validation_job_short_circuits_when_schema_is_not_ready(monkeypatch):
    async def fake_preview(sample_limit):
        return {
            "dry_run": True,
            "schema_status": {"ready": False, "missing_tables": ["truth_claims"]},
            "trust_gaps": [{"area": "schema_readiness"}],
            "mutations_planned": 0,
        }

    def fail_if_called():
        raise AssertionError("validation run should not open sync write session when schema is not ready")

    monkeypatch.setattr(truth_validation_task, "_run_preview_async", fake_preview)
    monkeypatch.setattr(truth_validation_task, "_get_pg_session", fail_if_called)

    result = truth_validation_task.run_truth_validation(job_id=None, sample_limit=5)

    assert result["status"] == "schema_not_ready"
    assert result["schema_status"]["missing_tables"] == ["truth_claims"]
    assert result["mutations_planned"] == 0


def test_truth_validation_job_short_circuits_when_schema_revision_drifts(monkeypatch):
    async def fake_preview(sample_limit):
        return {
            "dry_run": True,
            "schema_status": {
                "ready": True,
                "truth_tables_ready": True,
                "migration_current": False,
                "current_revision": "010_future_revision",
                "expected_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            },
            "trust_gaps": [{"area": "schema_revision"}],
            "mutations_planned": 0,
        }

    def fail_if_called():
        raise AssertionError("validation run should not open sync write session when schema revision drifts")

    monkeypatch.setattr(truth_validation_task, "_run_preview_async", fake_preview)
    monkeypatch.setattr(truth_validation_task, "_get_pg_session", fail_if_called)

    result = truth_validation_task.run_truth_validation(job_id=None, sample_limit=5)

    assert result["status"] == "schema_not_ready"
    assert result["schema_status"]["current_revision"] == "010_future_revision"
    assert result["trust_gaps"][0]["area"] == "schema_revision"


def test_truth_validation_requires_confirmation_before_review_item_execution(monkeypatch):
    def fail_if_called(sample_limit):
        raise AssertionError("validation preview should not run before execution gate")

    monkeypatch.setattr(truth_validation_task, "_run_preview_async", fail_if_called)

    with pytest.raises(ValueError, match="confirm_execute=true"):
        truth_validation_task.run_truth_validation(job_id=None, sample_limit=5, dry_run=False, confirm_execute=False)


def test_truth_validation_dry_run_does_not_open_write_session(monkeypatch):
    async def fake_preview(sample_limit):
        return {
            "dry_run": True,
            "generated_at": "2026-05-14T10:00:00+00:00",
            "checks": [{
                "check": "zero_current_building_links",
                "severity": "high",
                "recommended_queue": "insufficient_evidence",
                "why_it_matters": "No links.",
                "sample": [{"lead_id": "lead-1", "name": "No Link LLC"}],
            }],
            "mutations_planned": 0,
        }

    def fail_if_called():
        raise AssertionError("dry-run validation should not open a sync write session")

    monkeypatch.setattr(truth_validation_task, "_run_preview_async", fake_preview)
    monkeypatch.setattr(truth_validation_task, "_get_pg_session", fail_if_called)

    result = truth_validation_task.run_truth_validation(job_id=None, sample_limit=5, dry_run=True)

    assert result["dry_run"] is True
    assert result["mutations_planned"] == 0
    assert result["review_items_planned"] == 1
    assert result["review_items_upserted"] == 0
    assert "truth_validation_run.py" in result["required_execute_command"]


def test_validation_preview_builds_review_item_proposals_without_mutation():
    preview = {
        "checks": [
            {
                "check": "outreach_contradicted_relationships",
                "severity": "critical",
                "count_sampled": 1,
                "why_it_matters": "Outreach contradicted the manager relationship.",
                "recommended_queue": "conflicting_evidence",
                "sample": [{
                    "lead_id": "lead-1",
                    "bbl": "1000000001",
                    "outcome": "does_not_manage",
                }],
            },
            {
                "check": "possible_false_canonical_merges",
                "severity": "high",
                "count_sampled": 1,
                "why_it_matters": "Potential false merge.",
                "recommended_queue": "do_not_merge",
                "sample": [{
                    "canonical_entity_id": "entity-1",
                    "lead_ids": ["lead-a", "lead-b"],
                }],
            },
        ]
    }

    items = build_review_items_from_validation_preview(preview, run_id="truth-preview-test")

    assert len(items) == 2
    assert items[0]["queue_name"] == "conflicting_evidence"
    assert items[0]["subject_type"] == "lead"
    assert items[0]["subject_id"] == "lead-1"
    assert items[0]["actionability_level"] == "do_not_act"
    assert items[0]["supporting_evidence"]["sample"]["outcome"] == "does_not_manage"
    assert items[0]["contradicting_evidence"]["severity"] == "critical"
    assert items[1]["queue_name"] == "do_not_merge"
    assert items[1]["subject_type"] == "canonical_entity"
    assert items[1]["run_id"] == "truth-preview-test"


def test_truth_validation_rollback_preserves_reviewed_items_by_default():
    summary = build_validation_rollback_summary(
        run_id="truth-preview-test",
        review_status_counts={"open": 4, "approved": 1, "rejected": 2},
        validation_run_exists=True,
        execute=False,
    )

    assert summary["would_delete_review_items"] == 4
    assert summary["would_leave_reviewed_items"] == 3
    assert summary["would_delete_validation_run"] is True
    assert summary["mutations_planned"] == 0


def test_truth_validation_rollback_requires_reviewed_item_override_on_execute():
    summary = build_validation_rollback_summary(
        run_id="truth-preview-test",
        review_status_counts={"open": 4, "approved": 1},
        validation_run_exists=True,
        execute=True,
        confirm_execute=True,
        include_reviewed=False,
    )

    assert summary["blocked_reason"]
    assert "include-reviewed" in summary["blocked_reason"]


def test_truth_materialization_job_short_circuits_when_schema_is_not_ready(monkeypatch):
    async def fake_materialization(*, limit, dry_run, confirm_execute, run_id, sources=None):
        del sources
        return {
            "dry_run": True,
            "schema_status": {"ready": False, "missing_tables": ["truth_claims"]},
            "trust_gaps": [{"area": "schema_readiness"}],
            "mutations_planned": 0,
        }

    def fail_if_called():
        raise AssertionError("materialization run should not open sync write session when schema is not ready")

    monkeypatch.setattr(truth_materialization_task, "_run_materialization_async", fake_materialization)
    monkeypatch.setattr(truth_materialization_task, "_get_pg_session", fail_if_called)

    result = truth_materialization_task.run_truth_materialization(job_id=None, limit=5)

    assert result["status"] == "schema_not_ready"
    assert result["schema_status"]["missing_tables"] == ["truth_claims"]
    assert result["mutations_planned"] == 0


def test_truth_materialization_job_short_circuits_when_schema_revision_drifts(monkeypatch):
    async def fake_materialization(*, limit, dry_run, confirm_execute, run_id, sources=None):
        del sources
        return {
            "dry_run": True,
            "schema_status": {
                "ready": True,
                "truth_tables_ready": True,
                "migration_current": False,
                "current_revision": "010_future_revision",
                "expected_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            },
            "trust_gaps": [{"area": "schema_revision"}],
            "mutations_planned": 0,
        }

    def fail_if_called():
        raise AssertionError("materialization run should not open sync write session when schema revision drifts")

    monkeypatch.setattr(truth_materialization_task, "_run_materialization_async", fake_materialization)
    monkeypatch.setattr(truth_materialization_task, "_get_pg_session", fail_if_called)

    result = truth_materialization_task.run_truth_materialization(job_id=None, limit=5)

    assert result["status"] == "schema_not_ready"
    assert result["schema_status"]["current_revision"] == "010_future_revision"
    assert result["trust_gaps"][0]["area"] == "schema_revision"


def test_truth_materialization_run_metrics_include_audit_counts_and_samples():
    result = {
        "planned_claims_total": 12,
        "claims_upserted": 7,
        "evidence_upserted": 7,
        "confidence_snapshots_upserted": 3,
        "skipped_claims": 1,
        "conflicts": [{"claim_id": "claim-conflict"}],
        "before_counts": {"truth_claim_count": 10, "truth_evidence_count": 20, "confidence_snapshot_count": 4, "open_review_count": 2},
        "after_counts": {"truth_claim_count": 17, "truth_evidence_count": 27, "confidence_snapshot_count": 7, "open_review_count": 2},
        "claims_upserted_by_source": {"building_management": 7},
        "rollback_plan": {"new_claim_count": 7, "updated_claim_count": 0},
        "rollback_manifest": {"entry_count": 17, "by_type": {"truth_claim": {"new": 7}}},
        "sample_building_management_claims": [{"lead_id": "lead-1"}],
        "sample_hpd_contact_claims": [{"bbl": "1000000001"}],
        "sample_building_signal_claims": [{"source_name": "acris_transactions"}],
    }

    metrics = truth_materialization_task._materialization_run_metrics(result, dry_run=False)
    samples = truth_materialization_task._materialization_sample_findings(result)

    assert metrics["before_counts"]["truth_claim_count"] == 10
    assert metrics["after_counts"]["truth_claim_count"] == 17
    assert metrics["skipped_claims"] == 1
    assert metrics["conflict_count"] == 1
    assert metrics["confidence_snapshots_upserted"] == 3
    assert metrics["rollback_plan"] == {"new_claim_count": 7, "updated_claim_count": 0}
    assert metrics["rollback_manifest"]["entry_count"] == 17
    assert metrics["sources"] == {"building_management": 7}
    assert samples["sample_building_management_claims"][0]["lead_id"] == "lead-1"
    assert samples["sample_hpd_contact_claims"][0]["bbl"] == "1000000001"
    assert samples["sample_building_signal_claims"][0]["source_name"] == "acris_transactions"
    assert samples["conflicts"][0]["claim_id"] == "claim-conflict"


def test_truth_migration_preflight_marks_ready_state_without_mutation():
    result = build_preflight_result(
        schema_status={
            "ready": False,
            "current_revision": "008_lead_lineage",
            "missing_tables": REQUIRED_TRUTH_TABLES,
        },
        current_result={"ok": True, "stdout": "008_lead_lineage\n", "stderr": ""},
        heads_result={"ok": True, "stdout": f"{EXPECTED_TRUTH_ALEMBIC_REVISION} (head)\n", "stderr": ""},
        sql_result={"ok": True, "stdout": "BEGIN;\nCREATE TABLE IF NOT EXISTS truth_claims (...);\nCOMMIT;\n", "stderr": "", "command": "python -m alembic upgrade 008_lead_lineage:010_truth_manifest --sql"},
        rollback_sql_result={"ok": True, "stdout": "BEGIN;\nDROP TABLE IF EXISTS truth_claims;\nCOMMIT;\n", "stderr": "", "command": "python -m alembic downgrade 010_truth_manifest:008_lead_lineage --sql"},
    )

    assert result["dry_run"] is True
    assert result["mutations_planned"] == 0
    assert result["ready_to_apply_additive_truth_migration"] is True
    assert result["approval_required"] is True
    assert result["offline_rollback_sql"]["ok"] is True
    assert "truth-confidence migration must be backed out" in result["rollback_strategy"]


def test_truth_confidence_migration_is_additive_and_truth_scoped():
    migration_path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "009_truth_confidence_program.py"
    source = migration_path.read_text(encoding="utf-8")
    upgrade_source = source.split("def upgrade() -> None:", 1)[1].split("def downgrade() -> None:", 1)[0]
    downgrade_source = source.split("def downgrade() -> None:", 1)[1]

    destructive_upgrade = re.findall(
        r"\b(?:DROP\s+TABLE|ALTER\s+TABLE|TRUNCATE\s+TABLE|DELETE\s+FROM|UPDATE\s+\w+|INSERT\s+INTO)\b",
        upgrade_source,
        flags=re.IGNORECASE,
    )
    assert destructive_upgrade == []

    created_tables = re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", upgrade_source, flags=re.IGNORECASE)
    assert set(created_tables) == set(REQUIRED_TRUTH_TABLES) - {"truth_materialization_manifest"}
    assert created_tables[:2] == ["truth_claims", "truth_evidence"]

    dropped_tables = re.findall(r"DROP TABLE IF EXISTS\s+(\w+)", downgrade_source, flags=re.IGNORECASE)
    assert dropped_tables == [
        "truth_validation_runs",
        "golden_verification_cases",
        "truth_review_items",
        "confidence_snapshots",
        "truth_evidence",
        "truth_claims",
    ]


def test_truth_materialization_manifest_migration_is_additive_and_truth_scoped():
    migration_path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "010_truth_materialization_manifest.py"
    source = migration_path.read_text(encoding="utf-8")
    upgrade_source = source.split("def upgrade() -> None:", 1)[1].split("def downgrade() -> None:", 1)[0]
    downgrade_source = source.split("def downgrade() -> None:", 1)[1]

    destructive_upgrade = re.findall(
        r"\b(?:DROP\s+TABLE|ALTER\s+TABLE|TRUNCATE\s+TABLE|DELETE\s+FROM|UPDATE\s+\w+)\b",
        upgrade_source,
        flags=re.IGNORECASE,
    )
    assert destructive_upgrade == []
    assert "CREATE TABLE IF NOT EXISTS truth_materialization_manifest" in upgrade_source
    assert "UNIQUE (run_id, item_type, item_id)" in upgrade_source
    assert "DROP TABLE IF EXISTS truth_materialization_manifest" in downgrade_source


@pytest.mark.anyio
async def test_truth_health_report_returns_schema_gap_instead_of_raising():
    session = FailingAsyncSession()

    report = await build_truth_health_report(session)

    assert session.rolled_back is True
    assert report["summary"]["trust_posture"] == "not_ready"
    assert report["trust_gaps"][0]["area"] == "schema_readiness"
    assert report["trust_gaps"][0]["severity"] == "critical"
    assert report["mutations_planned"] == 0
    assert report["summary"]["configured_golden_cases"] == len(GOLDEN_CASE_SEEDS)
    assert report["summary"]["evaluable_golden_cases"] == len(GOLDEN_CASE_SEEDS)
    assert report["golden_benchmark"]["benchmark_coverage"] == 1.0


@pytest.mark.anyio
async def test_truth_schema_status_reports_missing_tables_and_revision():
    session = FakeAsyncSession(
        [
            *[FakeExecuteResult(rows=[{"exists": table_name != "truth_claims"}]) for table_name in REQUIRED_TRUTH_TABLES],
            FakeExecuteResult(rows=[{"exists": True}]),
            FakeExecuteResult(rows=[{"version_num": "008_lead_lineage"}]),
        ]
    )

    status = await load_truth_schema_status(session)

    assert status["ready"] is False
    assert status["expected_revision"] == EXPECTED_TRUTH_ALEMBIC_REVISION
    assert status["current_revision"] == "008_lead_lineage"
    assert status["migration_current"] is False
    assert status["truth_tables_ready"] is False
    assert status["revision_status"] == "schema_missing"
    assert status["missing_tables"] == ["truth_claims"]
    assert status["mutations_planned"] == 0


@pytest.mark.anyio
async def test_truth_schema_status_allows_future_revision_when_tables_exist():
    session = FakeAsyncSession(
        [
            *[FakeExecuteResult(rows=[{"exists": True}]) for _ in REQUIRED_TRUTH_TABLES],
            FakeExecuteResult(rows=[{"exists": True}]),
            FakeExecuteResult(rows=[{"version_num": "010_future_truth_followup"}]),
        ]
    )

    status = await load_truth_schema_status(session)

    assert status["ready"] is True
    assert status["truth_tables_ready"] is True
    assert status["expected_revision_applied"] is False
    assert status["revision_status"] == "schema_present_revision_differs"
    assert status["missing_tables"] == []


def test_outreach_feedback_classifies_wrong_manager_as_contradicting_management_claim():
    claims = classify_outreach_feedback(
        lead_id="lead-1",
        event_id=22,
        method="phone",
        outcome="does_not_manage",
        notes="They said we do not manage that building.",
        bbl="1000000001",
    )

    assert len(claims) == 1
    assert claims[0]["predicate"] == "has_valid_management_relationship"
    assert claims[0]["object_id"] == "1000000001"
    assert claims[0]["support_status"] == "contradicts"


def test_outreach_feedback_classifies_building_only_manager_contradiction():
    claims = classify_outreach_feedback(
        event_id=25,
        method="phone",
        outcome="does_not_manage",
        notes="Building-level reply says that is not the manager.",
        bbl="1000000001",
    )

    assert len(claims) == 1
    assert claims[0]["subject_type"] == "building"
    assert claims[0]["subject_id"] == "1000000001"
    assert claims[0]["predicate"] == "has_valid_management_relationship"
    assert claims[0]["object_type"] == "management_relationship"
    assert claims[0]["support_status"] == "contradicts"


def test_outreach_feedback_classifies_confirmed_decision_maker_as_contact_support():
    claims = classify_outreach_feedback(
        lead_id="lead-1",
        event_id=23,
        method="phone",
        outcome="confirmed_decision_maker",
        notes="Confirmed decision maker.",
    )

    assert len(claims) == 1
    assert claims[0]["predicate"] == "has_valid_contact_path"
    assert claims[0]["support_status"] == "supports"


def test_outreach_feedback_ignores_activity_without_truth_signal():
    claims = classify_outreach_feedback(
        lead_id="lead-1",
        event_id=24,
        method="email",
        outcome="sent_email",
        notes="Intro sent.",
    )

    assert claims == []


@pytest.mark.anyio
async def test_outreach_feedback_truth_write_status_requires_schema_and_revision():
    missing = await load_outreach_feedback_truth_write_status(FakeAsyncSession([
        FakeExecuteResult(rows=[{"exists": False}]),
        FakeExecuteResult(rows=[{"exists": False}]),
        FakeExecuteResult(rows=[{"exists": True}]),
        FakeExecuteResult(rows=[{"version_num": "008_lead_lineage"}]),
    ]))
    ready = await load_outreach_feedback_truth_write_status(FakeAsyncSession([
        FakeExecuteResult(rows=[{"exists": True}]),
        FakeExecuteResult(rows=[{"exists": True}]),
        FakeExecuteResult(rows=[{"exists": True}]),
        FakeExecuteResult(rows=[{"version_num": OUTREACH_TRUTH_REVISION}]),
    ]))

    assert missing["ready"] is False
    assert missing["reason"] == "truth_schema_missing"
    assert missing["missing_tables"] == ["truth_claims", "truth_evidence"]
    assert ready["ready"] is True
    assert ready["reason"] == "truth_feedback_claims_recorded"


def test_outreach_feedback_materialization_uses_claim_ledger_evidence_contract():
    observed_at = datetime.now(timezone.utc)
    specs = build_outreach_feedback_claim_specs({
        "id": 91,
        "lead_id": "lead-1",
        "bbl": "1000000001",
        "canonical_entity_id": "entity-1",
        "target_item_id": "target-1",
        "stage": "contacted",
        "method": "email",
        "outcome": "does_not_manage",
        "notes": "Reply says they do not manage this building.",
        "event_timestamp": observed_at,
    })

    assert len(specs) == 1
    claim = specs[0]["claim"]
    evidence = specs[0]["evidence"]
    assert claim["predicate"] == "has_valid_management_relationship"
    assert claim["claim_type"] == "building_management"
    assert claim["belief_status"] == "conflicting"
    assert claim["actionability_level"] == "do_not_act"
    assert evidence["source_name"] == "outreach_confirmed"
    assert evidence["source_type"] == "operator_feedback"
    assert evidence["support_status"] == "contradicts"
    assert evidence["evidence_weight"] == -1.0
    assert evidence["raw_payload"]["canonical_entity_id"] == "entity-1"
    assert claim["claim_id"] == stable_outreach_feedback_id(
        "lead",
        "lead-1",
        "has_valid_management_relationship",
        "building",
        "1000000001",
        91,
    )


def test_building_only_outreach_feedback_materialization_uses_building_subject():
    specs = build_outreach_feedback_claim_specs({
        "id": 92,
        "lead_id": None,
        "bbl": "1000000001",
        "canonical_entity_id": None,
        "target_item_id": None,
        "stage": "contacted",
        "method": "phone",
        "outcome": "does_not_manage",
        "notes": "Building-level response says wrong manager.",
        "event_timestamp": datetime.now(timezone.utc),
    })

    assert len(specs) == 1
    claim = specs[0]["claim"]
    evidence = specs[0]["evidence"]
    assert claim["subject_type"] == "building"
    assert claim["subject_id"] == "1000000001"
    assert claim["object_type"] == "management_relationship"
    assert claim["belief_status"] == "conflicting"
    assert claim["actionability_level"] == "do_not_act"
    assert evidence["support_status"] == "contradicts"
    assert evidence["raw_payload"]["bbl"] == "1000000001"


@pytest.mark.anyio
async def test_lead_outreach_event_preserves_logging_when_truth_schema_missing(monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("truth ledger writes should be skipped while schema is missing")

    monkeypatch.setattr(leads_router, "record_outreach_feedback_claims", fail_if_called)
    session = CapturingFakeAsyncSession([
        FakeExecuteResult(rows=[{"exists": True}]),
        FakeExecuteResult(rows=[{"exists": False}]),
        FakeExecuteResult(rows=[{"exists": False}]),
        FakeExecuteResult(rows=[{"exists": True}]),
        FakeExecuteResult(rows=[{"version_num": "008_lead_lineage"}]),
        FakeExecuteResult(rows=[{"id": 91}]),
        FakeExecuteResult(rows=[]),
    ])
    request = Request({"type": "http", "method": "POST", "path": "/api/leads/lead-1/outreach-event", "headers": []})

    response = await leads_router.log_outreach_event.__wrapped__(
        request=request,
        lead_id="lead-1",
        body=OutreachEventRequest(
            stage="contacted",
            method="email",
            outcome="does_not_manage",
            notes="They do not manage this building.",
        ),
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["status"] == "success"
    assert response["event_id"] == 91
    assert response["truth_claim_ids"] == []
    assert response["truth_claim_status"]["reason"] == "truth_schema_missing"
    assert session.committed is True


@pytest.mark.anyio
async def test_building_outreach_event_preserves_logging_when_truth_schema_missing(monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("truth ledger writes should be skipped while schema is missing")

    monkeypatch.setattr(buildings_router, "record_outreach_feedback_claims", fail_if_called)
    session = CapturingFakeAsyncSession([
        FakeExecuteResult(rows=[{"bbl": "1000000001"}]),
        FakeExecuteResult(rows=[{"exists": True}]),
        FakeExecuteResult(rows=[{"exists": False}]),
        FakeExecuteResult(rows=[{"exists": False}]),
        FakeExecuteResult(rows=[{"exists": True}]),
        FakeExecuteResult(rows=[{"version_num": "008_lead_lineage"}]),
        FakeExecuteResult(rows=[{"id": 92}]),
        FakeExecuteResult(rows=[]),
    ])
    request = Request({"type": "http", "method": "POST", "path": "/api/v1/buildings/1000000001/outreach-event", "headers": []})

    response = await buildings_router.log_building_outreach_event.__wrapped__(
        request=request,
        bbl="1000000001",
        stage="contacted",
        method="phone",
        outcome="does_not_manage",
        notes="They do not manage this building.",
        next_follow_up=None,
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["status"] == "success"
    assert response["event_id"] == 92
    assert response["truth_claim_ids"] == []
    assert response["truth_claim_status"]["reason"] == "truth_schema_missing"
    assert session.committed is True


def test_golden_benchmark_scores_required_forbidden_and_freshness_claims():
    cases = [
        {
            "case_id": "case-1",
            "name": "Known PM duplicate",
            "case_type": "pm_duplicate",
            "subject_type": "canonical_entity",
            "subject_id": "ce-1",
            "expected_outcome": "merge_only_low_risk_suffix_variants",
            "expected_claims": {
                "required_claims": [
                    {
                        "predicate": "manages_buildings",
                        "object_id": "1000000001",
                        "claim_type": "building_management",
                        "min_confidence": 0.75,
                        "max_freshness_days": 90,
                    },
                    {
                        "predicate": "has_valid_contact_path",
                        "claim_type": "person_contact",
                    },
                ],
                "forbidden_claims": [
                    {
                        "predicate": "maps_to_canonical_entity",
                        "object_id": "wrong-merge",
                        "metric": "false_merge",
                    }
                ],
            },
            "tricky_features": ["shared address"],
        }
    ]
    actual_claims = {
        "case-1": [
            {
                "claim_id": "claim-1",
                "subject_type": "canonical_entity",
                "subject_id": "ce-1",
                "predicate": "manages_buildings",
                "object_id": "1000000001",
                "claim_type": "building_management",
                "confidence_score": 0.84,
                "freshness_days": 30,
            },
            {
                "claim_id": "claim-2",
                "subject_type": "canonical_entity",
                "subject_id": "ce-1",
                "predicate": "maps_to_canonical_entity",
                "object_id": "wrong-merge",
                "claim_type": "entity_identity",
                "confidence_score": 0.9,
            },
        ]
    }

    result = evaluate_golden_cases(cases, actual_claims, seeded=True)

    assert result["configured_cases"] == 1
    assert result["failed_cases"] == 1
    assert result["metrics"]["precision"] == 0.5
    assert result["metrics"]["recall"] == 0.5
    assert result["metrics"]["false_merge_rate"] == 1.0
    assert result["metrics"]["building_link_accuracy"] == 1.0
    assert result["metrics"]["contact_accuracy"] == 0.0
    assert result["metrics"]["freshness_accuracy"] == 1.0


def test_golden_benchmark_reports_required_hard_case_feature_coverage():
    result = evaluate_golden_cases(
        [
            {
                "case_id": "thin-case",
                "name": "Thin case",
                "case_type": "thin",
                "subject_type": "entity",
                "tricky_features": ["co-op board"],
            }
        ],
        {},
        seeded=False,
    )

    assert result["feature_coverage"]["coverage"] is not None
    assert "wrong contact" in result["feature_coverage"]["missing_required_features"]


def test_golden_seed_cases_are_evaluable_and_fit_schema_ids():
    result = evaluate_golden_cases(GOLDEN_CASE_SEEDS, {}, seeded=False)

    assert all(len(case["case_id"]) <= 80 for case in GOLDEN_CASE_SEEDS)
    assert result["configured_cases"] == len(GOLDEN_CASE_SEEDS)
    assert result["evaluable_cases"] == len(GOLDEN_CASE_SEEDS)
    assert result["benchmark_coverage"] == 1.0
    assert result["feature_coverage"]["missing_required_features"] == []
    denominators = result["metric_counts"]["category_denominators"]
    assert denominators["false_merge_rate"] > 0
    assert denominators["false_split_rate"] > 0
    assert denominators["building_link_accuracy"] > 0
    assert denominators["contact_accuracy"] > 0
    assert denominators["freshness_accuracy"] > 0


def test_materialized_claim_builder_adds_confidence_and_evidence_contract():
    claim = build_materialized_claim(
        subject_type="lead",
        subject_id="lead-1",
        predicate="manages_building",
        object_type="building",
        object_id="1000000001",
        normalized_value="manager",
        claim_type="building_management",
        source_name="building_management",
        source_type="derived_hpd_registration_link",
        source_record_id="building_management:10",
        observed_at=None,
    )

    assert claim["claim"]["claim_id"]
    assert claim["claim"]["belief_status"] in {"proposed", "likely", "verified", "insufficient_evidence"}
    assert claim["claim"]["actionability_level"] in {
        "do_not_act",
        "broad_discovery",
        "ranked_sourcing",
        "automated_enrichment",
        "recommended_outreach",
        "acquisition_quality_diligence",
    }
    assert claim["evidence"]["claim_id"] == claim["claim"]["claim_id"]


def test_manual_evidence_claim_spec_supports_or_contradicts_truth_fact():
    base_payload = {
        "subject_type": "lead",
        "subject_id": "lead-1",
        "predicate": "manages_building",
        "object_type": "building",
        "object_id": "1018250029",
        "normalized_value": "manager",
        "claim_type": "building_management",
        "source_record_id": "operator-note-1",
        "observed_at": "2026-05-14T16:30:00+00:00",
    }

    supporting = build_manual_evidence_claim_spec(
        {**base_payload, "support_status": "supports", "source_name": "operator_review"},
        recorded_by="admin@test.com",
    )
    contradicting = build_manual_evidence_claim_spec(
        {**base_payload, "support_status": "contradicts", "source_name": "operator_review"},
        recorded_by="admin@test.com",
    )

    assert supporting["claim"]["claim_id"] != contradicting["claim"]["claim_id"]
    assert supporting["evidence"]["support_status"] == "supports"
    assert contradicting["evidence"]["support_status"] == "contradicts"
    assert supporting["claim"]["confidence_score"] > contradicting["claim"]["confidence_score"]
    assert supporting["claim"]["rationale"]["manual_evidence"] is True
    assert contradicting["evidence"]["raw_payload"]["recorded_by"] == "admin@test.com"


@pytest.mark.anyio
async def test_manual_evidence_preview_is_no_mutation_and_rollback_aware():
    session = CapturingFakeAsyncSession([
        FakeExecuteResult(rows=[]),
        FakeExecuteResult(rows=[]),
        FakeExecuteResult(rows=[]),
    ])
    result = await preview_or_record_manual_evidence(
        session,
        payload={
            "subject_type": "lead",
            "subject_id": "lead-1",
            "predicate": "manages_building",
            "object_type": "building",
            "object_id": "1018250029",
            "normalized_value": "manager",
            "claim_type": "building_management",
            "support_status": "supports",
            "source_name": "manual_evidence",
            "source_url": "https://example.com/manager-confirmation",
            "note": "Operator reviewed public site and matched the building.",
            "observed_at": "2026-05-14T16:30:00+00:00",
        },
        recorded_by="admin@test.com",
        dry_run=True,
        confirm_execute=False,
        run_id="truth-manual-evidence-test",
    )

    assert result["dry_run"] is True
    assert result["allowed_execute"] is False
    assert result["mutations_planned"] == 3
    assert result["rollback_plan"]["new_claim_count"] == 1
    assert result["rollback_plan"]["new_evidence_count"] == 1
    assert result["rollback_plan"]["new_confidence_snapshot_count"] == 1
    assert result["claim_spec"]["source_url"] == "https://example.com/manager-confirmation"
    assert result["mutation_scope"]["allowed_tables"] == [
        "truth_materialization_manifest",
        "truth_claims",
        "truth_evidence",
        "confidence_snapshots",
    ]
    assert result["mutation_scope"]["forbidden_side_effects"] == {
        "will_mark_verified": False,
        "will_create_or_refresh_source_data": False,
        "will_materialize_building_management_relationships": False,
        "will_start_jobs": False,
        "will_allow_business_use": False,
    }
    assert session.committed is False


def test_hpd_contact_materialization_distinguishes_owner_role_and_address_claims():
    specs = build_hpd_contact_claim_specs({
        "id": 123,
        "bbl": "1000000001",
        "registration_contact_id": "rc-1",
        "registration_id": "reg-1",
        "contact_type": "CorporateOwner",
        "corporation_name": "Example Owner LLC",
        "first_name": None,
        "last_name": None,
        "title": None,
        "business_address": "123 Main St",
        "business_city": "New York",
        "business_state": "NY",
        "business_zip": "10001",
        "observed_at": None,
    })

    assert len(specs) == 2
    role_claim = specs[0]["claim"]
    address_claim = specs[1]["claim"]
    assert role_claim["subject_type"] == "hpd_contact"
    assert role_claim["predicate"] == "owns_building"
    assert role_claim["claim_type"] == "building_ownership"
    assert role_claim["object_id"] == "1000000001"
    assert specs[0]["evidence"]["source_name"] == "hpd_contacts"
    assert address_claim["predicate"] == "has_mailing_address"
    assert address_claim["claim_type"] == "mailing_address"
    assert address_claim["normalized_value"] == "123 Main St, New York, NY, 10001"


def test_hpd_management_contact_can_independently_support_lead_building_relationship():
    spec = build_hpd_management_link_claim_spec({
        "id": 123,
        "bbl": "1000000001",
        "registration_contact_id": "rc-1",
        "registration_id": "reg-1",
        "contact_type": "ManagementCompany",
        "corporation_name": "Example Mgmt LLC",
        "first_name": None,
        "last_name": None,
        "title": None,
        "building_management_id": 99,
        "lead_id": "lead-1",
        "lead_normalized_name": "EXAMPLE MANAGEMENT LLC",
        "lead_company_name": "Example Management LLC",
        "lead_agent_name": None,
        "lead_owner_name": None,
        "observed_at": None,
    })

    assert spec is not None
    claim = spec["claim"]
    evidence = spec["evidence"]
    assert claim["subject_type"] == "lead"
    assert claim["subject_id"] == "lead-1"
    assert claim["predicate"] == "manages_building"
    assert claim["object_type"] == "building"
    assert claim["object_id"] == "1000000001"
    assert claim["normalized_value"] == "manager"
    assert claim["claim_type"] == "building_management"
    assert evidence["source_name"] == "hpd_contacts"
    assert evidence["source_type"] == "hpd_registration_management_company_match"
    assert evidence["raw_payload"]["match_rule"] == "same_bbl_management_company_strict_name_key"


def test_hpd_agent_overlap_materializes_as_registered_agent_not_manager():
    spec = build_hpd_role_link_claim_spec({
        "id": 123,
        "bbl": "1000000001",
        "registration_contact_id": "rc-1",
        "registration_id": "reg-1",
        "contact_type": "Agent",
        "corporation_name": "Harlem Property Management LLC",
        "first_name": None,
        "last_name": None,
        "title": None,
        "building_management_id": 99,
        "building_management_role": "agent",
        "lead_id": "0ff794d3ba2d",
        "lead_normalized_name": "HARLEM PROPERTY MANAGEMENT INC.",
        "lead_company_name": "Harlem Property Management Inc.",
        "lead_agent_name": None,
        "lead_owner_name": None,
        "observed_at": None,
    })

    assert spec is not None
    claim = spec["claim"]
    evidence = spec["evidence"]
    assert claim["predicate"] == "registered_agent_for_building"
    assert claim["claim_type"] == "registered_agent"
    assert claim["normalized_value"] == "registered_agent"
    assert evidence["source_name"] == "hpd_contacts"
    assert evidence["raw_payload"]["hpd_contact_verification_key"] == "HARLEM PROPERTY MANAGEMENT"
    assert evidence["raw_payload"]["matched_lead_verification_keys"] == ["HARLEM PROPERTY MANAGEMENT"]
    assert "not proof of operating management" in evidence["raw_payload"]["safe_action_note"]


def test_strict_verification_key_blocks_broad_harlem_false_match():
    spec = build_hpd_role_link_claim_spec({
        "id": 124,
        "bbl": "1000000001",
        "registration_contact_id": "rc-2",
        "registration_id": "reg-2",
        "contact_type": "Agent",
        "corporation_name": "Harlem Realty LLC",
        "first_name": None,
        "last_name": None,
        "title": None,
        "building_management_id": 99,
        "building_management_role": "agent",
        "lead_id": "0ff794d3ba2d",
        "lead_normalized_name": "HARLEM PROPERTY MANAGEMENT INC.",
        "lead_company_name": "Harlem Property Management Inc.",
        "lead_agent_name": None,
        "lead_owner_name": None,
        "observed_at": None,
    })

    assert spec is None


def test_enrichment_result_materialization_preserves_source_specific_observations():
    specs = build_enrichment_result_claim_specs({
        "id": 44,
        "lead_id": "lead-1",
        "source": "google_places",
        "phone": "2125550100",
        "email": "hello@example.com",
        "website": "https://example.com",
        "owner_principal": "Jane Owner",
        "raw_data": {"place_id": "abc"},
        "fetched_at": None,
    })

    predicates = {spec["claim"]["predicate"]: spec for spec in specs}
    assert set(predicates) == {"has_phone", "has_email", "has_website", "has_owner_principal"}
    assert predicates["has_phone"]["claim"]["claim_type"] == "phone"
    assert predicates["has_email"]["claim"]["claim_type"] == "email"
    assert predicates["has_website"]["claim"]["claim_type"] == "website"
    assert predicates["has_owner_principal"]["claim"]["claim_type"] == "person_contact"
    assert all(spec["evidence"]["source_name"] == "google_places" for spec in specs)
    assert predicates["has_website"]["evidence"]["raw_payload"]["field"] == "website"


def test_building_signal_materialization_preserves_public_source_context():
    acris = build_building_signal_claim_spec("acris_transactions", {
        "id": 7,
        "document_id": "202405010001",
        "bbl": "1000000001",
        "doc_type_description": "DEED",
        "recorded_date": date(2026, 2, 1),
        "doc_amount": 1_250_000,
        "party_type": "2",
        "party_name": "Example Owner LLC",
    })
    energy = build_building_signal_claim_spec("energy_grades", {
        "id": 8,
        "bbl": "1000000001",
        "grade": "D",
        "score": 42,
        "year": 2025,
        "property_name": "Example Building",
    })

    assert acris is not None
    assert acris["claim"]["subject_type"] == "building"
    assert acris["claim"]["predicate"] == "has_recorded_property_transaction"
    assert acris["claim"]["claim_type"] == "property_transaction"
    assert acris["evidence"]["source_name"] == "acris"
    assert acris["evidence"]["source_record_id"] == "acris_transactions:202405010001"
    assert acris["evidence"]["raw_payload"]["party_name"] == "Example Owner LLC"

    assert energy is not None
    assert energy["claim"]["predicate"] == "has_energy_grade"
    assert energy["claim"]["freshness_days"] is not None
    assert energy["evidence"]["source_name"] == "energy_grades"


def test_confidence_snapshots_are_built_from_materialized_claims_by_subject():
    run_id = "truth-materialization-test"
    fresh = build_materialized_claim(
        subject_type="building",
        subject_id="1000000001",
        predicate="has_hpd_violation",
        object_type="source_record",
        object_id="hpd_violations:v1",
        normalized_value="class=C",
        claim_type="building_condition_signal",
        source_name="hpd_violations",
        source_type="hpd_violations",
        source_record_id="hpd_violations:v1",
        observed_at=datetime.now(timezone.utc),
    )
    stale = build_materialized_claim(
        subject_type="building",
        subject_id="1000000001",
        predicate="has_recorded_property_transaction",
        object_type="source_record",
        object_id="acris_transactions:d1",
        normalized_value="DEED",
        claim_type="property_transaction",
        source_name="acris",
        source_type="acris_transactions",
        source_record_id="acris_transactions:d1",
        observed_at=date(2024, 1, 1),
    )

    snapshots = build_confidence_snapshots_from_specs([fresh, stale], run_id=run_id)

    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot["entity_type"] == "building"
    assert snapshot["entity_id"] == "1000000001"
    assert snapshot["confidence_scope"] == "materialized_claims"
    assert snapshot["supporting_claim_count"] == 2
    assert snapshot["stale_claim_count"] >= 1
    assert snapshot["run_id"] == run_id
    assert set(snapshot["rationale"]["claim_types"]) == {"building_condition_signal", "property_transaction"}
    assert snapshot["rationale"]["confidence_policy_version"] == CONFIDENCE_POLICY_VERSION


def test_materialization_rollback_plan_separates_new_from_updated_upsert_rows():
    plan = build_materialization_rollback_plan(
        run_id="truth-materialization-test",
        claim_ids=["claim-new", "claim-existing"],
        evidence_ids=["evidence-new", "evidence-existing"],
        snapshot_ids=["snapshot-new", "snapshot-existing"],
        existing_claim_ids={"claim-existing"},
        existing_evidence_ids={"evidence-existing"},
        existing_snapshot_ids={"snapshot-existing"},
        before_snapshot_samples={
            "truth_claims": [{"claim_id": "claim-existing", "confidence_score": 0.61}],
            "truth_evidence": [{"evidence_id": "evidence-existing", "support_status": "supports"}],
            "confidence_snapshots": [{"snapshot_id": "snapshot-existing", "confidence_score": 0.58}],
        },
    )

    assert plan["new_claim_count"] == 1
    assert plan["updated_claim_count"] == 1
    assert plan["new_evidence_count"] == 1
    assert plan["updated_evidence_count"] == 1
    assert plan["new_confidence_snapshot_count"] == 1
    assert plan["updated_confidence_snapshot_count"] == 1
    assert plan["new_claim_ids_sample"] == ["claim-new"]
    assert plan["updated_claim_ids_sample"] == ["claim-existing"]
    assert plan["before_snapshot_samples"]["truth_claims"][0]["claim_id"] == "claim-existing"
    assert "broad rollback of updated rows requires" in plan["rollback_order"][-1]


def test_materialization_manifest_entries_capture_complete_new_vs_existing_ids():
    entries = build_materialization_manifest_entries(
        run_id="truth-materialization-test",
        item_type="truth_claim",
        item_ids=["claim-new", "claim-existing", "claim-new"],
        existing_item_ids={"claim-existing"},
        before_snapshots_by_id={
            "claim-existing": {
                "claim_id": "claim-existing",
                "belief_status": "verified",
                "confidence_score": 0.91,
            },
        },
    )

    assert entries == [
        {
            "run_id": "truth-materialization-test",
            "item_type": "truth_claim",
            "item_id": "claim-existing",
            "was_existing": True,
            "before_snapshot": {
                "claim_id": "claim-existing",
                "belief_status": "verified",
                "confidence_score": 0.91,
            },
        },
        {
            "run_id": "truth-materialization-test",
            "item_type": "truth_claim",
            "item_id": "claim-new",
            "was_existing": False,
            "before_snapshot": None,
        },
    ]


@pytest.mark.anyio
async def test_lead_truth_summary_answers_belief_evidence_confidence_and_actionability():
    observed_at = datetime.now(timezone.utc)
    session = FakeAsyncSession(
        [
            FakeExecuteResult(rows=[{
                "lead_id": "lead-1",
                "display_name": "Example PM",
                "phone": "2125550100",
                "email": "ops@example.com",
                "website": "https://example.com",
                "enrichment_status": "complete",
                "last_enriched": observed_at,
                "updated_at": observed_at,
            }]),
            FakeExecuteResult(rows=[{
                "canonical_entity_id": "entity-1",
                "display_name": "Example PM LLC",
                "normalized_name": "example pm",
                "confidence_score": 0.91,
                "relationship_type": "primary",
                "membership_confidence": 0.88,
            }]),
            FakeExecuteResult(rows=[{
                "linked_buildings": 12,
                "last_linked_at": observed_at,
            }]),
            FakeExecuteResult(rows=[{
                "conflicting_buildings": 1,
            }]),
        ]
    )

    summary = await build_lead_truth_summary(session, "lead-1", include_persisted_claims=False)

    assert summary is not None
    assert summary["entity_name"] == "Example PM"
    assert summary["canonical_entity"]["canonical_entity_id"] == "entity-1"
    assert summary["belief_summary"]["contradiction_count"] == 1
    assert summary["belief_summary"]["freshness_days"] == 0
    assert any("12 current linked building" in belief for belief in summary["belief_summary"]["what_we_believe"])
    assert any("manages buildings" in why for why in summary["belief_summary"]["why_we_believe"])
    assert "building_management" in summary["belief_summary"]["supporting_sources"]
    assert "building_management" in summary["belief_summary"]["contradicting_sources"]
    assert "recommended_outreach" in summary["belief_summary"]["safe_actions"]
    assert "acquisition_quality_diligence" not in summary["belief_summary"]["safe_actions"]

    claims = {claim["predicate"]: claim for claim in summary["claims"]}
    assert claims["manages_buildings"]["supporting_sources"] == ["building_management", "hpd_contacts"]
    assert claims["manages_buildings"]["contradicting_sources"] == ["building_management"]
    assert claims["manages_buildings"]["supporting_evidence_count"] == 12
    assert claims["has_contact_path"]["supporting_sources"] == ["hpd_contacts", "enrichment", "company_website"]
    assert claims["maps_to_canonical_entity"]["object_id"] == "entity-1"
    assert 0 < summary["overall_confidence_score"] <= 1
    assert summary["review_bucket"] in {"conflicting_evidence", "needs_human_review", "safe_auto_accept", "suggested_merge"}


@pytest.mark.anyio
async def test_lead_truth_summary_does_not_promote_blocked_claims_to_broad_discovery():
    observed_at = datetime.now(timezone.utc)
    session = FakeAsyncSession(
        [
            FakeExecuteResult(rows=[{
                "lead_id": "lead-1",
                "display_name": "Unverified PM",
                "phone": None,
                "email": None,
                "website": None,
                "enrichment_status": None,
                "last_enriched": None,
                "updated_at": observed_at,
            }]),
            FakeExecuteResult(rows=[]),
            FakeExecuteResult(rows=[{
                "linked_buildings": 0,
                "last_linked_at": None,
            }]),
            FakeExecuteResult(rows=[{
                "conflicting_buildings": 0,
            }]),
        ]
    )

    summary = await build_lead_truth_summary(session, "lead-1", include_persisted_claims=False)

    assert summary is not None
    assert summary["belief_summary"]["safe_actions"] == ["do_not_act"]
    assert "broad_discovery" not in summary["belief_summary"]["safe_actions"]
    assert all(claim["actionability_level"] == "do_not_act" for claim in summary["claims"])


@pytest.mark.anyio
async def test_subject_truth_summary_answers_building_evidence_confidence_and_actionability():
    observed_at = datetime.now(timezone.utc)
    session = FakeAsyncSession([
        FakeExecuteResult(rows=[{
            "bbl": "1000000001",
            "address": "100 Example Ave",
            "borough": "MANHATTAN",
            "updated_at": observed_at,
        }]),
        FakeExecuteResult(rows=[{
            "current_manager_count": 0,
            "lead_ids": [],
            "lead_names": [],
            "roles": [],
            "observed_at": None,
        }]),
        FakeExecuteResult(rows=[]),
        FakeExecuteResult(rows=[]),
        FakeExecuteResult(rows=[
            {
                "claim_id": "claim-building-owner",
                "subject_type": "building",
                "subject_id": "1000000001",
                "predicate": "has_owner",
                "object_type": "entity",
                "object_id": "entity-owner",
                "normalized_value": "Example Owner LLC",
                "claim_type": "building_ownership",
                "belief_status": "current_belief",
                "confidence_score": 0.86,
                "freshness_days": 15,
                "observed_at": observed_at,
                "actionability_level": "recommended_outreach",
                "rationale": {"source_quality": "public_record"},
                "supporting_evidence_count": 2,
                "contradicting_evidence_count": 1,
                "supporting_sources": ["acris", "hpd_registration"],
                "contradicting_sources": ["outreach_confirmed"],
            },
            {
                "claim_id": "claim-building-manager",
                "subject_type": "entity",
                "subject_id": "entity-manager",
                "predicate": "manages_building",
                "object_type": "building",
                "object_id": "1000000001",
                "normalized_value": "Example Manager Inc",
                "claim_type": "building_management",
                "belief_status": "proposed",
                "confidence_score": 0.74,
                "freshness_days": 45,
                "observed_at": observed_at,
                "actionability_level": "automated_enrichment",
                "rationale": {"source_quality": "hpd_contact"},
                "supporting_evidence_count": 1,
                "contradicting_evidence_count": 0,
                "supporting_sources": ["hpd_contacts"],
                "contradicting_sources": [],
            },
        ])
    ])

    summary = await build_subject_truth_summary(
        session,
        subject_type="building",
        subject_id="1000000001",
        include_persisted_claims=True,
    )

    assert summary["subject_type"] == "building"
    assert summary["subject_id"] == "1000000001"
    assert summary["belief_summary"]["contradiction_count"] == 1
    assert summary["belief_summary"]["freshness_days"] == 0
    assert "automated_enrichment" in summary["belief_summary"]["safe_actions"]
    assert "recommended_outreach" in summary["belief_summary"]["safe_actions"]
    assert any("has owner" in belief for belief in summary["belief_summary"]["what_we_believe"])
    assert any("has owner" in why and "acris" in why for why in summary["belief_summary"]["why_we_believe"])
    assert summary["belief_summary"]["supporting_sources"] == ["hpd_registrations", "acris", "hpd_registration", "hpd_contacts"]
    assert summary["belief_summary"]["contradicting_sources"] == ["outreach_confirmed"]
    assert len(summary["claims"]) == 3
    assert summary["claims"][0]["predicate"] == "exists_in_building_table"
    assert summary["claims"][1]["supporting_sources"] == ["acris", "hpd_registration"]
    assert summary["claims"][1]["contradicting_sources"] == ["outreach_confirmed"]
    assert summary["review_bucket"] in {"conflicting_evidence", "needs_human_review", "safe_auto_accept", "suggested_merge"}


@pytest.mark.anyio
async def test_subject_truth_summary_converts_outreach_negative_feedback_to_blocking_claim():
    observed_at = datetime.now(timezone.utc)
    session = FakeAsyncSession([
        FakeExecuteResult(rows=[{
            "bbl": "1000000001",
            "address": "100 Example Ave",
            "borough": "MANHATTAN",
            "updated_at": observed_at,
        }]),
        FakeExecuteResult(rows=[{
            "current_manager_count": 1,
            "lead_ids": ["lead-1"],
            "lead_names": ["Example PM"],
            "roles": ["manager"],
            "observed_at": observed_at,
        }]),
        FakeExecuteResult(rows=[]),
        FakeExecuteResult(rows=[{
            "id": 91,
            "lead_id": "lead-1",
            "bbl": "1000000001",
            "canonical_entity_id": "entity-1",
            "target_item_id": None,
            "method": "email",
            "outcome": "does_not_manage",
            "notes": "Confirmed by reply: they do not manage this building.",
            "event_timestamp": observed_at,
            "created_at": observed_at,
            "updated_at": observed_at,
        }]),
    ])

    summary = await build_subject_truth_summary(
        session,
        subject_type="building",
        subject_id="1000000001",
        include_persisted_claims=False,
    )

    feedback_claim = next(
        claim for claim in summary["claims"]
        if claim["predicate"] == "has_valid_management_relationship"
    )
    assert feedback_claim["belief_status"] == "conflicting"
    assert feedback_claim["actionability_level"] == "do_not_act"
    assert feedback_claim["supporting_sources"] == []
    assert feedback_claim["contradicting_sources"] == ["outreach_confirmed"]
    assert feedback_claim["confidence_score"] <= 0.44
    assert summary["belief_summary"]["contradiction_count"] >= 1
    assert summary["review_bucket"] == "conflicting_evidence"


@pytest.mark.anyio
async def test_subject_truth_endpoint_returns_read_only_building_claims_without_claim_tables():
    observed_at = datetime.now(timezone.utc)
    session = FakeAsyncSession([
        *schema_status_results(ready=False),
        FakeExecuteResult(rows=[{
            "bbl": "1000000001",
            "address": "100 Example Ave",
            "borough": "MANHATTAN",
            "updated_at": observed_at,
        }]),
        FakeExecuteResult(rows=[{
            "current_manager_count": 2,
            "lead_ids": ["lead-1", "lead-2"],
            "lead_names": ["Example PM", "Other PM"],
            "roles": ["manager"],
            "observed_at": observed_at,
        }]),
        FakeExecuteResult(rows=[{
            "id": 11,
            "bbl": "1000000001",
            "registration_contact_id": "rc-1",
            "registration_id": "reg-1",
            "contact_type": "Agent",
            "description": None,
            "corporation_name": "Example Law PLLC",
            "first_name": None,
            "last_name": None,
            "title": None,
            "business_address": "C/O Example Law PLLC",
            "business_city": "New York",
            "business_state": "NY",
            "business_zip": "10001",
            "observed_at": observed_at,
        }]),
        FakeExecuteResult(rows=[]),
    ])

    response = await truth_router.subject_truth_summary(
        subject_type="building",
        subject_id="1000000001",
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["subject_type"] == "building"
    assert response["schema_status"]["ready"] is False
    assert {claim["predicate"] for claim in response["claims"]} >= {
        "exists_in_building_table",
        "has_current_management_link",
        "has_registered_agent",
    }
    management_claim = next(claim for claim in response["claims"] if claim["predicate"] == "has_current_management_link")
    assert management_claim["contradicting_evidence_count"] == 1
    assert management_claim["contradicting_sources"] == ["building_management"]
    assert response["belief_summary"]["contradiction_count"] == 1


@pytest.mark.anyio
async def test_subject_truth_endpoint_resolves_contact_to_hpd_contact_evidence_without_claim_tables():
    observed_at = datetime.now(timezone.utc)
    session = FakeAsyncSession([
        *schema_status_results(ready=False),
        FakeExecuteResult(rows=[{
            "id": 11,
            "bbl": "1000000001",
            "registration_contact_id": "rc-1",
            "registration_id": "reg-1",
            "contact_type": "Agent",
            "description": None,
            "corporation_name": "Example Law PLLC",
            "first_name": None,
            "last_name": None,
            "title": None,
            "business_address": "C/O Example Law PLLC",
            "business_city": "New York",
            "business_state": "NY",
            "business_zip": "10001",
            "observed_at": observed_at,
        }]),
        FakeExecuteResult(rows=[]),
    ])

    response = await truth_router.subject_truth_summary(
        subject_type="contact",
        subject_id="11",
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    predicates = {claim["predicate"] for claim in response["claims"]}
    assert response["subject_type"] == "contact"
    assert response["schema_status"]["ready"] is False
    assert predicates >= {
        "identified_in_hpd_contacts",
        "has_registered_agent",
        "has_mailing_address",
    }
    assert all(claim["supporting_sources"] == ["hpd_contacts"] for claim in response["claims"])
    assert any("registered agent" in belief for belief in response["belief_summary"]["what_we_believe"])


@pytest.mark.anyio
async def test_subject_truth_endpoint_accepts_internal_hpd_contact_subject_type():
    observed_at = datetime.now(timezone.utc)
    session = FakeAsyncSession([
        *schema_status_results(ready=False),
        FakeExecuteResult(rows=[{
            "id": 12,
            "bbl": "1000000002",
            "registration_contact_id": "rc-2",
            "registration_id": "reg-2",
            "contact_type": "ManagementCompany",
            "description": None,
            "corporation_name": "Example Manager Inc",
            "first_name": None,
            "last_name": None,
            "title": None,
            "business_address": None,
            "business_city": None,
            "business_state": None,
            "business_zip": None,
            "observed_at": observed_at,
        }]),
        FakeExecuteResult(rows=[]),
    ])

    response = await truth_router.subject_truth_summary(
        subject_type="hpd_contact",
        subject_id="12",
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["subject_type"] == "hpd_contact"
    assert {claim["predicate"] for claim in response["claims"]} >= {
        "identified_in_hpd_contacts",
        "has_management_contact",
    }


@pytest.mark.anyio
async def test_subject_truth_endpoint_returns_read_only_canonical_entity_claims_without_claim_tables():
    observed_at = datetime.now(timezone.utc)
    session = FakeAsyncSession([
        *schema_status_results(ready=False),
        FakeExecuteResult(rows=[{
            "canonical_entity_id": "entity-1",
            "normalized_name": "EXAMPLE PM",
            "display_name": "Example PM",
            "entity_type": "pm_company",
            "status": "proposed",
            "confidence_score": 0.82,
            "updated_at": observed_at,
        }]),
        FakeExecuteResult(rows=[{
            "alias_count": 2,
            "alias_names": ["Example PM", "Example Property Management"],
            "observed_at": observed_at,
        }]),
        FakeExecuteResult(rows=[{
            "lead_count": 2,
            "primary_count": 1,
            "non_keeper_count": 1,
            "lead_ids": ["lead-1", "lead-2"],
            "lead_names": ["Example PM", "Example Realty"],
            "min_confidence": 0.6,
            "observed_at": observed_at,
        }]),
        FakeExecuteResult(rows=[{
            "building_count": 3,
            "bbls": ["1000000001", "1000000002", "1000000003"],
            "min_confidence": 0.75,
            "observed_at": observed_at,
        }]),
        FakeExecuteResult(rows=[{
            "proposal_count": 1,
            "unsafe_count": 1,
            "buckets": ["review_required"],
            "observed_at": observed_at,
        }]),
        FakeExecuteResult(rows=[]),
    ])

    response = await truth_router.subject_truth_summary(
        subject_type="canonical_entity",
        subject_id="entity-1",
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    predicates = {claim["predicate"] for claim in response["claims"]}
    assert response["subject_type"] == "canonical_entity"
    assert response["schema_status"]["ready"] is False
    assert predicates >= {
        "has_canonical_identity",
        "has_aliases",
        "has_lead_memberships",
        "has_building_memberships",
        "has_open_match_proposals",
    }
    lead_claim = next(claim for claim in response["claims"] if claim["predicate"] == "has_lead_memberships")
    proposal_claim = next(claim for claim in response["claims"] if claim["predicate"] == "has_open_match_proposals")
    assert lead_claim["contradicting_sources"] == ["canonical_entity_leads"]
    assert proposal_claim["contradicting_evidence_count"] == 1
    assert response["belief_summary"]["contradiction_count"] == 2
    assert response["review_bucket"] == "conflicting_evidence"


@pytest.mark.anyio
async def test_subject_truth_endpoint_treats_entity_as_public_canonical_entity_alias():
    observed_at = datetime.now(timezone.utc)
    session = FakeAsyncSession([
        *schema_status_results(ready=False),
        FakeExecuteResult(rows=[{
            "canonical_entity_id": "entity-2",
            "normalized_name": "SIMPLE PM",
            "display_name": "Simple PM",
            "entity_type": "pm_company",
            "status": "proposed",
            "confidence_score": 0.9,
            "updated_at": observed_at,
        }]),
        FakeExecuteResult(rows=[{"alias_count": 0, "alias_names": [], "observed_at": None}]),
        FakeExecuteResult(rows=[{
            "lead_count": 1,
            "primary_count": 1,
            "non_keeper_count": 0,
            "lead_ids": ["lead-1"],
            "lead_names": ["Simple PM"],
            "min_confidence": 1.0,
            "observed_at": observed_at,
        }]),
        FakeExecuteResult(rows=[{"building_count": 0, "bbls": [], "min_confidence": 0, "observed_at": None}]),
        FakeExecuteResult(rows=[{"proposal_count": 0, "unsafe_count": 0, "buckets": [], "observed_at": None}]),
        FakeExecuteResult(rows=[]),
    ])

    response = await truth_router.subject_truth_summary(
        subject_type="entity",
        subject_id="entity-2",
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["subject_type"] == "entity"
    assert {claim["predicate"] for claim in response["claims"]} >= {
        "has_canonical_identity",
        "has_lead_memberships",
    }
    assert response["belief_summary"]["contradiction_count"] == 0


@pytest.mark.anyio
async def test_subject_truth_endpoint_returns_read_only_person_claims_without_person_table():
    observed_at = datetime.now(timezone.utc)
    session = FakeAsyncSession([
        *schema_status_results(ready=False),
        FakeExecuteResult(rows=[
            {
                "id": 21,
                "bbl": "1000000001",
                "registration_contact_id": "rc-21",
                "registration_id": "reg-21",
                "contact_type": "HeadOfficer",
                "description": None,
                "corporation_name": None,
                "first_name": "Jane",
                "last_name": "Doe",
                "title": "President",
                "business_address": "10 Main St",
                "business_city": "New York",
                "business_state": "NY",
                "business_zip": "10001",
                "observed_at": observed_at,
            },
            {
                "id": 22,
                "bbl": "1000000002",
                "registration_contact_id": "rc-22",
                "registration_id": "reg-22",
                "contact_type": "Officer",
                "description": None,
                "corporation_name": None,
                "first_name": "Jane",
                "last_name": "Doe",
                "title": "Treasurer",
                "business_address": "99 Other Ave",
                "business_city": "New York",
                "business_state": "NY",
                "business_zip": "10002",
                "observed_at": observed_at,
            },
        ]),
        FakeExecuteResult(rows=[{
            "id": 31,
            "lead_id": "lead-1",
            "source": "hunter_person",
            "owner_principal": "Jane Doe",
            "observed_at": observed_at,
        }]),
        FakeExecuteResult(rows=[]),
    ])

    response = await truth_router.subject_truth_summary(
        subject_type="person",
        subject_id="person:Jane Doe",
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    predicates = {claim["predicate"] for claim in response["claims"]}
    assert response["subject_type"] == "person"
    assert response["schema_status"]["ready"] is False
    assert predicates >= {
        "identified_by_person_name",
        "associated_with_building",
        "observed_as_owner_principal",
    }
    identity_claim = next(claim for claim in response["claims"] if claim["predicate"] == "identified_by_person_name")
    assert identity_claim["contradicting_sources"] == ["hpd_contacts"]
    assert identity_claim["rationale"]["distinct_address_count"] == 2
    assert response["belief_summary"]["contradiction_count"] == 1
    assert response["review_bucket"] == "conflicting_evidence"


def test_review_decision_preview_lists_safe_status_change_without_mutation():
    preview = build_review_decision_preview(
        item={
            "source": "truth_review_items",
            "review_id": "review-1",
            "queue_name": "conflicting_evidence",
            "subject_type": "lead",
            "subject_id": "lead-1",
            "status": "open",
            "reviewed_by": None,
            "reviewed_at": None,
            "supporting_evidence": {"sources": ["hpd_contacts"]},
            "contradicting_evidence": {"sources": ["outreach_confirmed"]},
            "rationale": {"reason": "manager conflict"},
        },
        decision="reject",
        reviewer_email="reviewer@example.com",
        note="Outreach contradicted HPD.",
        dry_run=True,
    )

    assert preview["dry_run"] is True
    assert preview["target_status"] == "rejected"
    assert preview["allowed_execute"] is True
    assert preview["proposed_database_changes"][0]["table"] == "truth_review_items"
    assert preview["previous_state"]["status"] == "open"
    assert preview["resulting_state"]["status"] == "rejected"
    assert preview["decision_payload"]["last_review_note"] == "Outreach contradicted HPD."
    assert preview["decision_payload"]["previous_state"]["status"] == "open"
    assert preview["decision_payload"]["resulting_state"]["status"] == "rejected"
    assert preview["proposed_database_changes"][0]["jsonb_append"]["rationale"]["last_review_decision"] == "reject"
    assert preview["proposed_database_changes"][0]["jsonb_append"]["rationale"]["previous_state"]["status"] == "open"
    assert preview["supporting_evidence"]["sources"] == ["hpd_contacts"]


def test_review_decision_blocks_canonical_proposal_execution_path():
    preview = build_review_decision_preview(
        item={
            "source": "canonical_entity_match_proposals",
            "review_id": "canonical-proposal-12",
            "queue_name": "needs_human_review",
            "subject_type": "canonical_entity",
            "subject_id": "ce-1",
            "status": "proposed",
        },
        decision="approve",
        reviewer_email="reviewer@example.com",
        note=None,
        dry_run=True,
    )

    assert preview["allowed_execute"] is False
    assert preview["blocked_reason"]
    assert preview["proposed_database_changes"][0]["operation"] == "blocked_preview_only"


def test_review_decision_blocks_approval_for_conflicting_or_non_actionable_items():
    conflicting_preview = build_review_decision_preview(
        item={
            "source": "truth_review_items",
            "review_id": "review-conflict",
            "queue_name": "conflicting_evidence",
            "actionability_level": "recommended_outreach",
            "subject_type": "lead",
            "subject_id": "lead-1",
            "status": "open",
        },
        decision="approve",
        reviewer_email="reviewer@example.com",
        note=None,
        dry_run=True,
    )
    blocked_preview = build_review_decision_preview(
        item={
            "source": "truth_review_items",
            "review_id": "review-blocked",
            "queue_name": "needs_human_review",
            "actionability_level": "do_not_act",
            "subject_type": "canonical_entity",
            "subject_id": "entity-1",
            "status": "open",
        },
        decision="approve",
        reviewer_email="reviewer@example.com",
        note=None,
        dry_run=True,
    )
    safe_reject_preview = build_review_decision_preview(
        item={
            "source": "truth_review_items",
            "review_id": "review-conflict",
            "queue_name": "conflicting_evidence",
            "actionability_level": "do_not_act",
            "subject_type": "lead",
            "subject_id": "lead-1",
            "status": "open",
        },
        decision="reject",
        reviewer_email="reviewer@example.com",
        note=None,
        dry_run=True,
    )

    assert conflicting_preview["allowed_execute"] is False
    assert "conflicting_evidence" in conflicting_preview["blocked_reason"]
    assert blocked_preview["allowed_execute"] is False
    assert "do_not_act" in blocked_preview["blocked_reason"]
    assert safe_reject_preview["allowed_execute"] is True


@pytest.mark.anyio
async def test_review_decision_execute_persists_prior_and_resulting_state_in_audit_payload():
    reviewed_at = datetime(2026, 5, 1, 14, 30, tzinfo=timezone.utc)
    updated_at = datetime(2026, 5, 2, 9, 15, tzinfo=timezone.utc)
    session = CapturingFakeAsyncSession([
        FakeExecuteResult(rows=[{
            "review_id": "review-1",
            "queue_name": "conflicting_evidence",
            "subject_type": "lead",
            "subject_id": "lead-1",
            "status": "open",
            "priority": 10,
            "confidence_score": 0.72,
            "actionability_level": "automated_enrichment",
            "proposed_change": {"operation": "mark_conflicted"},
            "supporting_evidence": {"sources": ["hpd_contacts"]},
            "contradicting_evidence": {"sources": ["outreach_feedback"]},
            "rationale": {"reason": "manager conflict"},
            "run_id": "run-1",
            "reviewed_by": "previous@example.com",
            "reviewed_at": reviewed_at,
            "updated_at": updated_at,
        }]),
        FakeExecuteResult(rows=[]),
    ])

    response = await apply_review_decision(
        session,
        review_id="review-1",
        decision="needs_more_evidence",
        reviewer_email="reviewer@example.com",
        note="Need a fresher manager source before outreach.",
        dry_run=False,
        confirm_execute=True,
    )

    update_params = session.executed[1]["params"]
    decision_payload = json.loads(update_params["decision_payload"])
    assert response["executed"] is True
    assert session.committed is True
    assert update_params["status"] == "needs_more_evidence"
    assert decision_payload["last_review_decision"] == "needs_more_evidence"
    assert decision_payload["previous_state"]["status"] == "open"
    assert decision_payload["previous_state"]["reviewed_by"] == "previous@example.com"
    assert decision_payload["previous_state"]["reviewed_at"] == reviewed_at.isoformat()
    assert decision_payload["previous_state"]["updated_at"] == updated_at.isoformat()
    assert decision_payload["resulting_state"]["status"] == "needs_more_evidence"
    assert decision_payload["resulting_state"]["reviewed_by"] == "reviewer@example.com"
    assert decision_payload["executed_at"]


@pytest.mark.anyio
async def test_truth_materialization_preview_is_dry_run_and_reports_sources():
    session = FakeAsyncSession(
        [
            FakeExecuteResult(rows=[{
                "building_management_claims": 7,
                "hpd_contact_role_link_candidates": 1,
                "hpd_contact_management_link_candidates": 1,
                "hpd_contact_role_claims": 4,
                "hpd_contact_address_claims": 2,
                "enrichment_observation_claims": 4,
                "lead_contact_claims": 3,
                "canonical_membership_claims": 2,
                "outreach_feedback_claims": 1,
                "existing_claim_count": 5,
                "existing_evidence_count": 6,
            }]),
            FakeExecuteResult(rows=[{
                "id": 124,
                "bbl": "1000000001",
                "registration_contact_id": "rc-2",
                "registration_id": "reg-2",
                "contact_type": "ManagementCompany",
                "description": "Management Company",
                "corporation_name": "Example Management LLC",
                "first_name": None,
                "last_name": None,
                "title": None,
                "business_address": "123 Main St",
                "business_city": "New York",
                "business_state": "NY",
                "business_zip": "10001",
                "observed_at": None,
                "building_management_id": 88,
                "building_management_role": "manager",
                "lead_id": "lead-1",
                "lead_normalized_name": "EXAMPLE MANAGEMENT LLC",
                "lead_company_name": "Example Management LLC",
                "lead_agent_name": None,
                "lead_owner_name": None,
            }]),
            FakeExecuteResult(rows=[
                {
                    "id": idx,
                    "lead_id": f"lead-{idx}",
                    "bbl": f"100000000{idx}",
                    "role": "manager",
                    "registration_start": None,
                    "registration_end": None,
                    "observed_at": None,
                    "updated_at": None,
                }
                for idx in range(1, 8)
            ]),
            FakeExecuteResult(rows=[{
                "id": 123,
                "bbl": "1000000001",
                "registration_contact_id": "rc-1",
                "registration_id": "reg-1",
                "contact_type": "CorporateOwner",
                "description": "Owner",
                "corporation_name": "Example Owner LLC",
                "first_name": None,
                "last_name": None,
                "title": None,
                "business_address": "123 Main St",
                "business_city": "New York",
                "business_state": "NY",
                "business_zip": "10001",
                "observed_at": None,
                "updated_at": None,
            }]),
            FakeExecuteResult(rows=[{
                "id": 124,
                "bbl": "1000000001",
                "registration_contact_id": "rc-2",
                "registration_id": "reg-2",
                "contact_type": "ManagementCompany",
                "description": "Management Company",
                "corporation_name": "Example Mgmt LLC",
                "first_name": None,
                "last_name": None,
                "title": None,
                "business_address": "123 Main St",
                "business_city": "New York",
                "business_state": "NY",
                "business_zip": "10001",
                "observed_at": None,
                "building_management_id": 88,
                "building_management_role": "manager",
                "lead_id": "lead-1",
                "lead_normalized_name": "EXAMPLE MANAGEMENT LLC",
                "lead_company_name": "Example Management LLC",
                "lead_agent_name": None,
                "lead_owner_name": None,
            }]),
            FakeExecuteResult(rows=[{
                "id": 44,
                "lead_id": "lead-1",
                "source": "google_places",
                "phone": "2125550100",
                "email": "hello@example.com",
                "website": "https://example.com",
                "owner_principal": "Jane Owner",
                "raw_data": {"place_id": "abc"},
                "fetched_at": None,
            }]),
            FakeExecuteResult(rows=[{
                "source_name": "acris_transactions",
                "source_record_id": "doc-1",
                "bbl": "1000000001",
                "observed_at": None,
                "value": "DEED",
            }]),
            FakeExecuteResult(rows=[{
                "id": 91,
                "lead_id": "lead-1",
                "bbl": "1000000001",
                "canonical_entity_id": "entity-1",
                "target_item_id": None,
                "stage": "contacted",
                "method": "email",
                "outcome": "does_not_manage",
                "notes": "They do not manage this building.",
                "event_timestamp": None,
            }]),
        ]
    )

    result = await materialize_truth_claims(session, limit=50, dry_run=True, confirm_execute=False)

    assert result["dry_run"] is True
    assert result["mutations_planned"] == 0
    assert result["planned_claims_total"] == 25
    assert result["planned_claims_by_source"]["building_management"] == 7
    assert result["planned_claims_by_source"]["hpd_contact_role_links"] == 1
    assert result["planned_claims_by_source"]["hpd_contact_management_links"] == 1
    assert result["strict_materializable_claims_by_source"]["hpd_contact_role_links"] == 1
    assert result["strict_materializable_claims_by_predicate"]["manages_building"] == 2
    assert result["planned_claims_by_source"]["hpd_contact_roles"] == 4
    assert result["planned_claims_by_source"]["hpd_contact_addresses"] == 2
    assert result["planned_claims_by_source"]["enrichment_observations"] == 4
    assert result["planned_claims_by_source"]["outreach_feedback"] == 1
    assert result["sample_building_management_claims"][0]["bbl"] == "1000000001"
    assert result["sample_hpd_role_link_claims"][0]["predicate"] == "manages_building"
    assert result["sample_hpd_management_link_claims"][0]["predicate"] == "manages_building"
    assert result["sample_hpd_contact_claims"][0]["contact_type"] == "CorporateOwner"
    assert result["sample_enrichment_observation_claims"][0]["source"] == "google_places"
    assert result["sample_building_signal_claims"][0]["source_name"] == "acris_transactions"
    assert result["sample_outreach_feedback_claims"][0]["generated_claim_count"] == 1
    assert result["sample_outreach_feedback_claims"][0]["predicates"] == ["has_valid_management_relationship"]
    preview_specs = result["sample_materialized_claim_specs"]
    assert len(preview_specs) >= 5
    assert {
        "claim_id",
        "evidence_id",
        "subject_type",
        "subject_id",
        "predicate",
        "object_type",
        "object_id",
        "normalized_value",
        "claim_type",
        "source_name",
        "source_record_id",
        "support_status",
        "confidence_score",
        "freshness_days",
        "actionability_level",
    } <= set(preview_specs[0])
    assert {spec["source_name"] for spec in preview_specs} >= {
        "building_management",
        "hpd_contacts",
        "google_places",
        "acris",
        "outreach_confirmed",
    }
    assert preview_specs[0]["source_name"] == "building_management"
    assert preview_specs[1]["source_name"] == "hpd_contacts"
    assert preview_specs[2]["source_name"] == "google_places"
    assert preview_specs[3]["source_name"] == "acris"
    assert any(spec["claim_id"] and spec["evidence_id"] for spec in preview_specs)
    assert any(spec["predicate"] == "owns_building" for spec in preview_specs)
    assert any(spec["predicate"] == "has_recorded_property_transaction" for spec in preview_specs)
    assert any(spec["support_status"] == "contradicts" for spec in preview_specs)


@pytest.mark.anyio
async def test_truth_materialization_preview_can_scope_to_sources_for_batch_safety():
    session = FakeAsyncSession(
        [
            FakeExecuteResult(rows=[{
                "building_management_claims": 7,
                "hpd_contact_role_link_candidates": 4,
                "hpd_contact_management_link_candidates": 4,
                "hpd_contact_role_claims": 11,
                "hpd_contact_address_claims": 9,
                "enrichment_observation_claims": 0,
                "lead_contact_claims": 0,
                "canonical_membership_claims": 0,
                "outreach_feedback_claims": 3,
                "existing_claim_count": 5,
                "existing_evidence_count": 6,
            }]),
            FakeExecuteResult(rows=[{
                "id": 1,
                "lead_id": "lead-1",
                "bbl": "1000000001",
                "role": "manager",
                "registration_start": None,
                "registration_end": None,
                "observed_at": None,
                "updated_at": None,
            }]),
        ]
    )

    result = await materialize_truth_claims(
        session,
        limit=10,
        dry_run=True,
        confirm_execute=False,
        sources=["building_management"],
    )

    assert result["source_filter_applied"] is True
    assert result["selected_sources"] == ["building_management"]
    assert result["planned_claims_by_source"] == {"building_management": 7}
    assert result["planned_claims_total"] == 7
    assert [spec["source_name"] for spec in result["sample_materialized_claim_specs"]] == ["building_management"]
    assert session._results == []


def test_truth_materialization_source_normalization_rejects_unsupported_sources():
    assert normalize_materialization_sources("building_management,outreach_feedback") == (
        "building_management",
        "outreach_feedback",
    )
    with pytest.raises(ValueError, match="Unsupported truth materialization source"):
        normalize_materialization_sources(["building_management", "unsafe_everything"])


@pytest.mark.anyio
async def test_adversarial_validation_preview_includes_deeper_truth_checks():
    session = FakeAsyncSession(
        [
            FakeExecuteResult(rows=[]),
            FakeExecuteResult(rows=[]),
            FakeExecuteResult(rows=[]),
            FakeExecuteResult(rows=[{
                "lead_id": "lead-1",
                "name": "Inactive Owner LLC",
                "dos_status": "INACTIVE",
                "updated_at": None,
            }]),
            FakeExecuteResult(rows=[{
                "bbl": "1000000001",
                "contact_type": "Agent",
                "corporation_name": "Example Law PLLC",
                "business_address": "P.O. Box 1",
                "business_city": "New York",
                "business_state": "NY",
                "linked_lead_count": 1,
                "lead_ids": ["lead-1"],
            }]),
            FakeExecuteResult(rows=[{
                "bbl": "1000000002",
                "lead_count": 2,
                "lead_ids": ["owner-1", "manager-1"],
                "roles": ["owner", "manager"],
            }]),
            FakeExecuteResult(rows=[{
                "lead_id": "lead-2",
                "name": "Stale Contact PM",
                "phone": "2125550100",
                "email": None,
                "website": "https://example.com",
                "last_enriched": None,
            }]),
            FakeExecuteResult(rows=[{
                "lead_id": "lead-3",
                "field": "phone",
                "distinct_value_count": 2,
                "values": ["2125550100", "7185550100"],
                "sources": ["google_places", "hunter"],
                "last_observed_at": None,
            }]),
            FakeExecuteResult(rows=[{
                "lead_id": "lead-4",
                "name": "Wrong Contact PM",
                "bbl": "1000000003",
                "canonical_entity_id": "entity-1",
                "method": "email",
                "stage": "research",
                "outcome": "does_not_manage",
                "notes": "They said they do not manage the building.",
                "event_timestamp": None,
            }]),
            FakeExecuteResult(rows=[{
                "canonical_entity_id": "entity-2",
                "canonical_name": "Merged Maybe PM",
                "lead_count": 2,
                "distinct_name_count": 2,
                "lead_ids": ["lead-a", "lead-b"],
                "lead_names": ["Alpha PM", "Beta Realty"],
                "relationship_types": ["candidate"],
                "min_link_confidence": 0.42,
                "max_link_confidence": 0.7,
            }]),
        ]
    )

    result = await preview_adversarial_validation(session, sample_limit=5)
    checks = {check["check"]: check for check in result["checks"]}

    assert result["dry_run"] is True
    assert result["mutations_planned"] == 0
    assert checks["non_active_dos_entities"]["recommended_queue"] == "conflicting_evidence"
    assert checks["legal_mailbox_or_agent_addresses"]["sample"][0]["corporation_name"] == "Example Law PLLC"
    assert checks["owner_manager_role_ambiguity"]["severity"] == "high"
    assert checks["stale_contact_or_website_evidence"]["sample"][0]["last_enriched"] is None
    assert checks["conflicting_enrichment_observations"]["recommended_queue"] == "conflicting_evidence"
    assert checks["conflicting_enrichment_observations"]["sample"][0]["sources"] == ["google_places", "hunter"]
    assert checks["outreach_contradicted_relationships"]["severity"] == "critical"
    assert checks["outreach_contradicted_relationships"]["sample"][0]["outcome"] == "does_not_manage"
    assert checks["possible_false_canonical_merges"]["recommended_queue"] == "do_not_merge"
    assert checks["possible_false_canonical_merges"]["sample"][0]["lead_names"] == ["Alpha PM", "Beta Realty"]


@pytest.mark.anyio
async def test_truth_dashboard_reports_claim_review_and_actionability_counts():
    session = FakeAsyncSession(
        [
            *schema_status_results(),
            FakeExecuteResult(rows=[{
                "claim_count": 10,
                "verified_claim_count": 4,
                "conflicting_claim_count": 2,
                "recommended_outreach_claim_count": 3,
                "open_review_count": 5,
                "active_golden_case_count": 4,
                "confidence_snapshot_count": 6,
            }]),
            FakeExecuteResult(rows=[{"actionability_level": "recommended_outreach", "cnt": 3}]),
            FakeExecuteResult(rows=[{"queue_name": "conflicting_evidence", "cnt": 2}]),
            FakeExecuteResult(rows=[{"claim_type": "building_management", "cnt": 7}]),
        ]
    )

    response = await truth_router.truth_dashboard(
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["claim_count"] == 10
    assert response["conflicting_claim_count"] == 2
    assert response["open_review_count"] == 5
    assert response["actionability_distribution"]["recommended_outreach"] == 3
    assert response["review_queue_distribution"]["conflicting_evidence"] == 2
    assert response["actionability_rules"][0]["level"] == "broad_discovery"
    diligence_rule = next(rule for rule in response["actionability_rules"] if rule["level"] == "acquisition_quality_diligence")
    assert diligence_rule["confidence_policy_version"] == CONFIDENCE_POLICY_VERSION
    assert diligence_rule["minimum_score"] == 0.90
    assert diligence_rule["max_contradictions"] == 0
    assert diligence_rule["max_freshness_days"] == 60
    assert diligence_rule["min_supporting_sources"] == 2
    assert diligence_rule["min_supporting_evidence"] == 2
    assert {rule["level"] for rule in response["actionability_rules"]} >= {
        "automated_enrichment",
        "do_not_act",
    }


@pytest.mark.anyio
async def test_truth_dashboard_returns_schema_status_when_truth_tables_are_missing():
    session = FakeAsyncSession(schema_status_results(ready=False))

    response = await truth_router.truth_dashboard(
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["claim_count"] == 0
    assert response["schema_status"]["ready"] is False
    assert response["schema_status"]["current_revision"] == "008_lead_lineage"
    assert {rule["level"] for rule in response["actionability_rules"]} >= {
        "broad_discovery",
        "recommended_outreach",
        "acquisition_quality_diligence",
        "do_not_act",
    }
    outreach_rule = next(rule for rule in response["actionability_rules"] if rule["level"] == "recommended_outreach")
    assert outreach_rule["confidence_policy_version"] == CONFIDENCE_POLICY_VERSION
    assert outreach_rule["minimum_score"] == 0.78
    assert outreach_rule["min_supporting_sources"] == 1
    assert outreach_rule["min_supporting_evidence"] == 1


@pytest.mark.anyio
async def test_truth_activation_packet_endpoint_summarizes_approval_gate(monkeypatch):
    schema_status = {
        "ready": False,
        "current_revision": "008_lead_lineage",
        "expected_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
        "missing_tables": ["truth_claims", "truth_evidence"],
        "mutations_planned": 0,
    }

    async def fake_schema_status(session):
        return schema_status

    async def fake_health_report(session, *, materialization_limit=500, validation_sample_limit=20):
        return {
            "summary": {
                "trust_posture": "not_ready",
                "configured_golden_cases": 7,
                "evaluable_golden_cases": 7,
            },
            "activation_checklist": [{
                "step": "apply_truth_schema",
                "status": "approval_required",
                "reason": "Apply additive migration 010_truth_manifest.",
                "approval_required": True,
                "mutations_planned": 6,
            }],
            "source_refresh_plan": {
                "approval_required": True,
            "summary": {
                "planned_job_count": 14,
                "refreshable_job_count": 14,
                "blocked_job_count": 0,
                "affected_source_count": 20,
                "non_refreshable_gap_count": 1,
            },
            },
            "trust_gaps": [{
                "severity": "critical",
                "area": "schema_readiness",
                "message": "Truth tables are missing.",
            }],
        }

    monkeypatch.setattr(truth_router, "load_truth_schema_status", fake_schema_status)
    monkeypatch.setattr(truth_router, "build_truth_health_report", fake_health_report)

    response = await truth_router.truth_activation_packet(
        session=FakeAsyncSession([]),
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["verdict"] == "schema_approval_required"
    assert response["business_use_allowed"] is False
    assert response["schema"]["missing_tables"] == ["truth_claims", "truth_evidence"]
    assert response["source_refresh"]["planned_job_count"] == 14
    assert response["approval_steps"][0]["step"] == "apply_truth_schema"


@pytest.mark.anyio
async def test_truth_completion_audit_endpoint_surfaces_prompt_checklist_without_production_probe(monkeypatch):
    async def fake_schema_status(session):
        return {
            "ready": True,
            "current_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "expected_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "missing_tables": [],
            "migration_current": True,
            "mutations_planned": 0,
        }

    async def fake_health_report(session, *, materialization_limit=500, validation_sample_limit=20):
        return {
            "thresholds": {"minimum_claim_count": 1},
            "summary": {
                "trust_posture": "not_ready",
                "claim_count": 2063,
                "verified_claim_count": 0,
                "critical_or_high_gap_count": 1,
            },
            "activation_checklist": [{
                "step": "allow_business_use",
                "status": "blocked",
                "reason": "Verified claims are missing.",
                "approval_required": False,
                "mutations_planned": 0,
            }],
            "source_refresh_plan": {
                "approval_required": False,
                "summary": {
                    "planned_job_count": 0,
                    "refreshable_job_count": 0,
                    "blocked_job_count": 0,
                    "affected_source_count": 0,
                    "non_refreshable_gap_count": 0,
                },
            },
            "trust_gaps": [{
                "severity": "critical",
                "area": "source_overlap",
                "message": "Evidence is still preview-only.",
            }],
        }

    monkeypatch.setattr(truth_router, "load_truth_schema_status", fake_schema_status)
    monkeypatch.setattr(truth_router, "build_truth_health_report", fake_health_report)
    monkeypatch.setattr(
        truth_router,
        "build_artifact_checklist",
        lambda: [{"requirement": "prompt checklist artifact", "status": "satisfied"}],
    )

    response = await truth_router.truth_completion_audit(
        session=FakeAsyncSession([]),
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["dry_run"] is True
    assert response["mutations_planned"] == 0
    assert response["completion_status"] == "not_complete"
    assert response["artifact_summary"] == {"total": 1, "satisfied": 1, "missing": 0}
    assert response["production_probe_included"] is False
    assert "include-production" in response["production_probe_note"]
    assert response["prompt_to_artifact_checklist"][0]["status"] == "satisfied"
    assert any(item["status"] == "blocked" for item in response["prompt_to_artifact_checklist"])


@pytest.mark.anyio
async def test_truth_source_overlap_post_recording_check_endpoint_reports_current_ledger(monkeypatch):
    async def fake_schema_status(session):
        return {
            "ready": True,
            "current_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "expected_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "missing_tables": [],
            "migration_current": True,
            "mutations_planned": 0,
        }

    async def fake_ledger_source_overlap(session):
        return {
            "dry_run": True,
            "mutations_planned": 0,
            "total_fact_group_count": 2063,
            "single_source_fact_group_count": 2063,
            "multi_source_fact_group_count": 0,
            "source_ready_fact_group_count": 0,
            "max_supporting_source_count": 1,
            "max_supporting_evidence_count": 1,
        }

    async def fake_verified_single_source_summary(session, *, min_sources=2, sample_limit=5):
        return {
            "verified_claim_count": 0,
            "verified_single_source_claim_count": 0,
            "sample_limit": sample_limit,
            "samples": [],
        }

    monkeypatch.setattr(truth_router, "load_truth_schema_status", fake_schema_status)
    monkeypatch.setattr(truth_router, "load_ledger_source_overlap_summary", fake_ledger_source_overlap)
    monkeypatch.setattr(truth_router, "load_verified_single_source_summary", fake_verified_single_source_summary)

    session = FakeAsyncSession([])
    response = await truth_router.truth_source_overlap_post_recording_check(
        min_multi_source=1,
        min_source_ready=1,
        sample_limit=5,
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["dry_run"] is True
    assert response["mutations_planned"] == 0
    assert response["post_recording_success"] is False
    assert response["current_ledger"]["multi_source_fact_group_count"] == 0
    assert response["current_ledger"]["source_ready_fact_group_count"] == 0
    assert response["verified_single_source_policy"]["verified_single_source_claim_count"] == 0
    assert response["checks"][0]["check"] == "actual_current_ledger_multi_source"
    assert response["checks"][0]["status"] == "fail"
    assert response["checks"][2]["check"] == "no_single_source_verified_claims"
    assert response["checks"][2]["status"] == "pass"
    assert response["schema_status"]["current_revision"] == EXPECTED_TRUTH_ALEMBIC_REVISION
    assert session.rollback_count == 1


@pytest.mark.anyio
async def test_truth_source_overlap_post_recording_check_endpoint_respects_schema_gate(monkeypatch):
    async def fake_schema_status(session):
        return {
            "ready": False,
            "current_revision": "008_lead_lineage",
            "expected_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "missing_tables": REQUIRED_TRUTH_TABLES,
            "migration_current": False,
            "mutations_planned": 0,
        }

    monkeypatch.setattr(truth_router, "load_truth_schema_status", fake_schema_status)

    response = await truth_router.truth_source_overlap_post_recording_check(
        min_multi_source=1,
        min_source_ready=1,
        sample_limit=5,
        session=FakeAsyncSession([]),
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["schema_status"]["ready"] is False
    assert response["post_recording_success"] is False
    assert response["schema_status"]["missing_tables"] == REQUIRED_TRUTH_TABLES
    assert response["checks"][0]["check"] == "truth_schema_current"


@pytest.mark.anyio
async def test_truth_review_queue_returns_empty_schema_gap_without_truth_tables():
    session = FakeAsyncSession(schema_status_results(ready=False))

    response = await truth_router.review_queue(
        queue=None,
        status="open",
        limit=50,
        offset=0,
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["items"] == []
    assert response["source"] == "schema_not_ready"
    assert response["schema_status"]["missing_tables"] == REQUIRED_TRUTH_TABLES


@pytest.mark.anyio
async def test_truth_dashboard_blocks_when_schema_revision_drifts():
    session = FakeAsyncSession(schema_status_results(ready=True, revision="010_future_revision"))

    response = await truth_router.truth_dashboard(
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["claim_count"] == 0
    assert response["schema_status"]["revision_status"] == "schema_present_revision_differs"
    assert response["schema_status"]["migration_current"] is False


@pytest.mark.anyio
async def test_lead_truth_summary_skips_persisted_claims_when_schema_revision_drifts(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_build_summary(session, lead_id, *, include_persisted_claims):
        captured["lead_id"] = lead_id
        captured["include_persisted_claims"] = include_persisted_claims
        return {"lead_id": lead_id, "claims": []}

    monkeypatch.setattr(truth_router, "build_lead_truth_summary", fake_build_summary)
    session = FakeAsyncSession(schema_status_results(ready=True, revision="010_future_revision"))

    response = await truth_router.lead_truth_summary(
        lead_id="lead-1",
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert captured == {"lead_id": "lead-1", "include_persisted_claims": False}
    assert response["schema_status"]["revision_status"] == "schema_present_revision_differs"


@pytest.mark.anyio
async def test_subject_truth_summary_skips_persisted_claims_when_schema_revision_drifts(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_build_summary(session, *, subject_type, subject_id, include_persisted_claims):
        captured["subject_type"] = subject_type
        captured["subject_id"] = subject_id
        captured["include_persisted_claims"] = include_persisted_claims
        return {"subject_type": subject_type, "subject_id": subject_id, "claims": []}

    monkeypatch.setattr(truth_router, "build_subject_truth_summary", fake_build_summary)
    session = FakeAsyncSession(schema_status_results(ready=True, revision="010_future_revision"))

    response = await truth_router.subject_truth_summary(
        subject_type="building",
        subject_id="1000000001",
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert captured == {
        "subject_type": "building",
        "subject_id": "1000000001",
        "include_persisted_claims": False,
    }
    assert response["schema_status"]["revision_status"] == "schema_present_revision_differs"


@pytest.mark.anyio
async def test_truth_review_queue_blocks_when_schema_revision_drifts():
    session = FakeAsyncSession(schema_status_results(ready=True, revision="010_future_revision"))

    response = await truth_router.review_queue(
        queue=None,
        status="open",
        limit=50,
        offset=0,
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["items"] == []
    assert response["source"] == "schema_not_ready"
    assert response["schema_status"]["revision_status"] == "schema_present_revision_differs"


@pytest.mark.anyio
async def test_canonical_review_queue_keeps_unsafe_proposals_non_actionable():
    session = CapturingFakeAsyncSession([
        *schema_status_results(ready=True),
        FakeExecuteResult(rows=[]),
        FakeExecuteResult(rows=[{
            "review_id": "canonical-proposal-1",
            "queue_name": "do_not_merge",
            "subject_type": "canonical_entity",
            "subject_id": "entity-1",
            "status": "open",
            "priority": 50,
            "confidence_score": None,
            "actionability_level": "do_not_act",
            "proposed_change": {"reason": "shared mailbox"},
            "supporting_evidence": {"name_similarity": 0.82},
            "contradicting_evidence": {"address_type": "mailbox"},
            "rationale": {"safe_to_execute": False},
            "run_id": None,
            "updated_at": datetime.now(timezone.utc),
        }]),
    ])

    response = await truth_router.review_queue(
        queue=None,
        status="open",
        limit=50,
        offset=0,
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["items"][0]["queue_name"] == "do_not_merge"
    assert response["items"][0]["actionability_level"] == "do_not_act"
    assert "ELSE 'do_not_act'" in session.executed[-1]["statement"]
    assert "ELSE 'broad_discovery'" not in session.executed[-1]["statement"]


@pytest.mark.anyio
async def test_canonical_review_decision_preview_keeps_unsafe_proposals_non_actionable():
    session = CapturingFakeAsyncSession([
        FakeExecuteResult(rows=[]),
        FakeExecuteResult(rows=[{
            "review_id": "canonical-proposal-7",
            "queue_name": "do_not_merge",
            "subject_type": "canonical_entity",
            "subject_id": "entity-7",
            "status": "proposed",
            "priority": 50,
            "confidence_score": None,
            "actionability_level": "do_not_act",
            "proposed_change": {"reason": "name-only match"},
            "supporting_evidence": {"name_similarity": 0.74},
            "contradicting_evidence": {"address_conflict": True},
            "rationale": {"safe_to_execute": False},
            "run_id": None,
            "updated_at": datetime.now(timezone.utc),
        }]),
    ])

    item = await load_review_decision_item(session, "canonical-proposal-7")

    assert item is not None
    assert item["actionability_level"] == "do_not_act"
    assert item["source"] == "canonical_entity_match_proposals"
    assert "ELSE 'do_not_act'" in session.executed[-1]["statement"]
    assert "ELSE 'broad_discovery'" not in session.executed[-1]["statement"]


@pytest.mark.anyio
async def test_truth_review_decision_blocks_when_schema_missing():
    session = FakeAsyncSession(schema_status_results(ready=False))

    response = await truth_router.review_queue_decision(
        review_id="review-1",
        body=truth_router.ReviewDecisionRequest(decision="approve"),
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["dry_run"] is True
    assert response["allowed_execute"] is False
    assert response["queue_name"] == "schema_not_ready"
    assert response["schema_status"]["missing_tables"] == REQUIRED_TRUTH_TABLES


@pytest.mark.anyio
async def test_truth_role_claim_correction_endpoint_blocks_when_schema_missing():
    session = FakeAsyncSession(schema_status_results(ready=False))

    response = await truth_router.role_claim_correction_apply(
        body=truth_router.RoleClaimCorrectionRequest(),
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["dry_run"] is True
    assert response["allowed_execute"] is False
    assert response["run_type"] == "truth_role_claim_correction"
    assert response["schema_status"]["missing_tables"] == REQUIRED_TRUTH_TABLES


@pytest.mark.anyio
async def test_truth_role_claim_correction_endpoint_previews_guarded_updates():
    session = FakeAsyncSession([
        *schema_status_results(ready=True),
        FakeExecuteResult(rows=[{
            "claim_id": "claim-stale-agent",
            "subject_type": "lead",
            "subject_id": "0ff794d3ba2d",
            "predicate": "manages_building",
            "object_type": "building",
            "object_id": "1018250029",
            "normalized_value": "agent",
            "claim_type": "building_management",
            "belief_status": "likely",
            "confidence_score": 0.72,
            "actionability_level": "automated_enrichment",
            "observed_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
            "evidence_ids": ["evidence-stale-agent"],
            "source_names": ["building_management"],
            "source_record_ids": ["building_management:99"],
            "source_roles": ["agent"],
        }]),
        FakeExecuteResult(rows=[{"existing_id": "claim-stale-agent"}]),
        FakeExecuteResult(rows=[{
            "claim_id": "claim-stale-agent",
            "belief_status": "likely",
            "confidence_score": 0.72,
            "actionability_level": "automated_enrichment",
            "current_flag": True,
            "rationale": {"materialized_from": "building_management"},
            "updated_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
        }]),
    ])

    response = await truth_router.role_claim_correction_apply(
        body=truth_router.RoleClaimCorrectionRequest(
            lead_id="0ff794d3ba2d",
            limit=100,
            dry_run=True,
            confirm_execute=False,
            run_id="role-correction-api-preview",
        ),
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["dry_run"] is True
    assert response["allowed_execute"] is False
    assert response["mutations_planned"] == 1
    assert response["candidate_summary"]["claim_update_count"] == 1
    assert response["schema_status"]["ready"] is True


@pytest.mark.anyio
async def test_truth_validate_preview_blocks_when_schema_revision_drifts():
    session = FakeAsyncSession(schema_status_results(ready=True, revision="010_future_revision"))

    response = await truth_router.validate_preview.__wrapped__(
        request=None,
        sample_limit=20,
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["trust_gaps"][0]["area"] == "schema_revision"
    assert response["schema_status"]["revision_status"] == "schema_present_revision_differs"


@pytest.mark.anyio
async def test_golden_cases_and_benchmark_use_seed_data_when_schema_revision_drifts():
    cases_session = FakeAsyncSession(schema_status_results(ready=True, revision="010_future_revision"))

    cases_response = await truth_router.golden_cases(
        session=cases_session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert cases_response["seeded"] is False
    assert cases_response["schema_status"]["revision_status"] == "schema_present_revision_differs"
    assert len(cases_response["cases"]) == len(GOLDEN_CASE_SEEDS)

    benchmark_session = FakeAsyncSession(schema_status_results(ready=True, revision="010_future_revision"))
    benchmark_response = await truth_router.golden_benchmark(
        session=benchmark_session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert benchmark_response["seeded"] is False
    assert benchmark_response["schema_status"]["revision_status"] == "schema_present_revision_differs"
    assert benchmark_response["configured_cases"] == len(GOLDEN_CASE_SEEDS)


@pytest.mark.anyio
async def test_truth_materialize_preview_blocks_when_schema_revision_drifts():
    session = FakeAsyncSession(schema_status_results(ready=True, revision="010_future_revision"))

    response = await truth_router.materialize_preview.__wrapped__(
        request=None,
        limit=50,
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["trust_gaps"][0]["area"] == "schema_revision"
    assert response["schema_status"]["revision_status"] == "schema_present_revision_differs"
    assert response["activation_checklist"][0]["status"] == "approval_required"


@pytest.mark.anyio
async def test_truth_job_start_blocks_before_enqueue_when_schema_missing():
    session = FakeAsyncSession(schema_status_results(ready=False))

    response = await jobs_router.start_job(
        job_type="truth_materialization",
        limit=500,
        dry_run=True,
        confirm_execute=False,
        cohort_filter=None,
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["status"] == "schema_not_ready"
    assert response["job_id"] is None
    assert response["schema_status"]["missing_tables"] == REQUIRED_TRUTH_TABLES


@pytest.mark.anyio
async def test_truth_job_start_blocks_before_enqueue_when_schema_revision_drifts():
    session = FakeAsyncSession(schema_status_results(ready=True, revision="010_future_revision"))

    response = await jobs_router.start_job(
        job_type="truth_materialization",
        limit=500,
        dry_run=True,
        confirm_execute=False,
        cohort_filter=None,
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["status"] == "schema_not_ready"
    assert response["job_id"] is None
    assert response["schema_status"]["revision_status"] == "schema_present_revision_differs"


@pytest.mark.anyio
async def test_truth_job_start_defaults_to_approval_preview_when_schema_current():
    session = CapturingFakeAsyncSession(schema_status_results(ready=True))

    response = await jobs_router.start_job(
        job_type="truth_materialization",
        limit=500,
        dry_run=True,
        confirm_execute=False,
        cohort_filter=None,
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["status"] == "approval_required"
    assert response["job_id"] is None
    assert response["approval_required"] is True
    assert response["safe_to_run_automatically"] is False
    assert response["mutations_planned"] == 0
    assert "confirm_execute=true" in response["preview"]["required_execute_query"]
    assert session.committed is False
