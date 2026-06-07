"""Prompt-to-artifact completion audit for the truth-confidence upgrade.

The audit is intentionally conservative. File/artifact checks can prove that
the implementation foundation exists, but completion also requires runtime
evidence that local and production activation gates allow business use.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.truth_production_probe import run_probe  # noqa: E402
from src.db.session import get_session_factory, shutdown_engine  # noqa: E402
from src.services.truth_activation import build_activation_packet, build_runtime_preflight_summary  # noqa: E402
from src.services.truth_health import build_truth_health_report, load_truth_schema_status  # noqa: E402


@dataclass(frozen=True)
class ArtifactRequirement:
    requirement: str
    files: tuple[str, ...]
    required_terms: tuple[str, ...] = ()
    source_item: str = ""


ARTIFACT_REQUIREMENTS = [
    ArtifactRequirement(
        "Project guidance and handoff are present",
        ("AGENTS.md", "CODEX_REVIEW_HANDOFF.md", "README.md", "PRODUCT_PLAN.md"),
        ("Double Edge", "codex/leads-stabilization", "Data Truth & Confidence"),
        "Named files: AGENTS.md, CODEX_REVIEW_HANDOFF.md, README.md, PRODUCT_PLAN.md",
    ),
    ArtifactRequirement(
        "Architecture/design notes explain the Data Truth & Confidence system",
        (
            "backend/docs/10-data-truth-confidence-program.md",
            "backend/docs/11-truth-confidence-completion-audit.md",
        ),
        ("Claim Ledger", "Prompt-to-Artifact Checklist", "Production read-only probe"),
        "Deliverable: updated architecture/design notes and final audit report",
    ),
    ArtifactRequirement(
        "Core product-standard answer contract is explicit",
        (
            "backend/src/services/truth_program.py",
            "frontend/services/truth-api.ts",
            "frontend/components/LeadDetail.tsx",
            "frontend/components/BuildingDetailPage.tsx",
            "backend/docs/10-data-truth-confidence-program.md",
        ),
        (
            "what_we_believe",
            "why_we_believe",
            "supporting_sources",
            "contradicting_sources",
            "freshness_days",
            "overall_confidence_score",
            "safe_actions",
        ),
        "Primary product standard: what/why/support/contradict/freshness/confidence/safe action",
    ),
    ArtifactRequirement(
        "Workstream 1 - Canonical Entity Graph is connected to truth summaries",
        (
            "backend/src/services/truth_program.py",
            "backend/src/services/canonical_entities.py",
            "backend/src/models/entity.py",
        ),
        ("canonical_entity", "maps_to_canonical_entity", "proposal"),
        "Numbered workstream 1",
    ),
    ArtifactRequirement(
        "Workstream 2 - Claim Ledger / Evidence Model schema and migration exist",
        (
            "backend/src/models/truth.py",
            "backend/alembic/versions/009_truth_confidence_program.py",
            "backend/alembic/versions/010_truth_materialization_manifest.py",
        ),
        ("TruthClaim", "TruthEvidence", "confidence_snapshots", "belief_status", "TruthMaterializationManifest"),
        "Numbered workstream 2",
    ),
    ArtifactRequirement(
        "Workstream 3 - Multi-Source Verification and source audit are implemented",
        (
            "backend/src/services/truth_materialization.py",
            "backend/src/services/source_audit.py",
            "backend/scripts/truth_health_report.py",
        ),
        ("acris_transactions", "dob_permits", "hpd_litigation", "google_places", "outreach_feedback"),
        "Numbered workstream 3",
    ),
    ArtifactRequirement(
        "Narrow manager source-overlap pilot is previewed, simulated, and approval-gated",
        (
            "backend/src/routers/truth.py",
            "backend/src/services/truth_adjudication.py",
            "backend/scripts/truth_source_overlap_approval_packet.py",
            "backend/scripts/truth_source_overlap_post_recording_check.py",
            "backend/scripts/truth_verification_frontier.py",
            "backend/scripts/truth_manager_external_evidence_batch.py",
            "backend/scripts/truth_operator_confirmed_evidence_batch.py",
            "backend/scripts/truth_source_acquisition_worklist.py",
            "backend/scripts/truth_source_evidence_intake.py",
            "backend/scripts/truth_source_overlap_blocker_report.py",
            "backend/scripts/truth_live_hpd_role_audit.py",
            "backend/src/services/source_evidence_intake.py",
            "backend/scripts/truth_api_workflow_smoke.py",
            "backend/scripts/truth_completion_audit.py",
            "backend/tests/test_confidence_program.py",
            "backend/tests/test_truth_api_workflow_smoke.py",
            "backend/tests/test_truth_completion_audit.py",
            "frontend/components/SettingsPage.tsx",
            "frontend/scripts/verify-activation-packet-smoke.mjs",
            "frontend/services/truth-api.ts",
            "backend/docs/11-truth-confidence-completion-audit.md",
        ),
        (
            "manager_external_source_acquisition_preview",
            "manual_evidence_batch_preview",
            "recommended_strict_manager_proof_batch",
            "post_recording_simulation",
            "rollback_preview",
            "next_source_batches",
            "next_source_search_pack",
            "next_source_search_pack_sample",
            "manager_source_acquisition_packet",
            "manager-source-acquisition-packet",
            "source-acquisition-only",
            "search_queries",
            "source_targets",
            "source_boundary_notes",
            "reviewed_source_findings",
            "hpm_revenue_by_property_summary",
            "first_party_operator_document",
            "operator_confirmed_management_preview",
            "operator_confirmed_management_evidence_batch",
            "operator_source_acquisition_packet",
            "operator_first_hand_confirmation",
            "second_source_seed_batches",
            "source-overlap-approval-packet",
            "source-overlap-post-recording-check",
            "verification-frontier",
            "source-acquisition-worklist",
            "source-evidence-intake/preview",
            "source-evidence-intake/batch-preview",
            "truth_source_acquisition_worklist",
            "source_acquisition_worklist",
            "truth_source_evidence_intake_preview",
            "truth_source_evidence_intake",
            "truth_source_evidence_intake_batch_preview",
            "truth_source_overlap_blocker_report",
            "source-overlap-blocker-report",
            "source-overlap-blocker-report/preview",
            "blocked_evidence_acquisition_required",
            "fetchTruthSourceOverlapBlockerReport",
            "previewTruthSourceOverlapBlockerReport",
            "TruthSourceOverlapBlockerReport",
            "Source-overlap blocker report",
            "Candidate JSON preview",
            "candidate-file",
            "hpd-audit-file",
            "recording_approval_packet",
            "expected_post_recording_source_overlap",
            "first_source_only_after_recording_count",
            "multi_source_after_recording_count",
            "source_ready_after_recording_count",
            "approval_required_before_recording",
            "allowed_execute",
            "manual_evidence_payload_count",
            "manual_evidence_payload_review",
            "source_evidence_candidate_summary",
            "recording_ready_status",
            "preview_ready_approval_required",
            "SourceEvidenceIntakeRequest",
            "SourceEvidenceIntakeBatchRequest",
            "TruthSourceEvidenceIntakePreview",
            "TruthSourceEvidenceIntakeBatchPreview",
            "previewTruthSourceEvidenceIntake",
            "previewTruthSourceEvidenceIntakeBatch",
            "ready_for_manual_evidence_preview",
            "manual_evidence_preview",
            "source_evidence_intake_candidates",
            "source_evidence_intake_candidate_count",
            "source_acquisition_clues",
            "source_acquisition_clue_count",
            "has_source_acquisition_clues",
            "source_clue_only",
            "truth_source_evidence_intake_clue_only_preview",
            "source_clue_only_primary_source_required",
            "document_kind",
            "derived_research",
            "truth_operator_document_audit",
            "operator_confirmed_document_provenance",
            "management_company_contradiction_count",
            "source_url_or_local_record_reference",
            "paste_back_template",
            "paste_back_fields",
            "truth_verification_frontier",
            "source_ready_below_verified",
            "source_acquisition_frontier",
            "current_relationship_state",
            "current_ledger_source_ready",
            "required_real_evidence",
            "official_hpd_query_packet_only",
            "official_query_urls",
            "read_only_preview_command",
            "source_dataset_ids",
            "evidence_request_packet",
            "source_ready_requests",
            "source_acquisition_requests",
            "reviewed_source_finding_count",
            "reviewed_source_history_status",
            "evidence_acquisition_status",
            "verification_readiness_gate",
            "record_ready_count",
            "acquisition_required_count",
            "single_source_upgrade_would_verify_count",
            "bundle_upgrade_would_verify_count",
            "build_source_overlap_approval_packet",
            "build_post_recording_check",
            "build_verification_frontier",
            "fetchTruthSourceOverlapPostRecordingCheck",
            "fetchTruthVerificationFrontier",
            "fetchTruthSourceAcquisitionWorklist",
            "manager_new_relationship_candidate_summary",
            "counts_as_current_ledger_overlap",
            "approval_required_for_relationship_creation",
            "approval_decision_summary",
            "fetchTruthSourceOverlapApprovalPacket",
            "fetchTruthManagerSourceAcquisitionPacket",
            "validate_source_overlap_approval_packet",
            "validate_manager_source_acquisition_packet",
            "Source-overlap approval packet",
            "Post-recording proof",
            "Verification frontier",
            "Source-acquisition worklist",
            "Operator approval effects",
            "Current ledger:",
            "single_source_claims_stay_unverified",
            "source_overlap_recording",
            "confirm_execute",
        ),
        "Primary blocker: prove and scale real independent source overlap without recording evidence before approval",
    ),
    ArtifactRequirement(
        "Workstream 4 - Confidence Scoring is implemented",
        (
            "backend/src/services/confidence.py",
            "backend/src/services/truth_adjudication.py",
            "backend/src/services/truth_health.py",
            "frontend/utils/contactConfidence.ts",
        ),
        ("ACTION_THRESHOLDS", "CONFIDENCE_POLICY_VERSION", "source_quality", "contradiction", "do_not_act", "safe_to_mark_verified"),
        "Numbered workstream 4",
    ),
    ArtifactRequirement(
        "Workstream 5 - Adversarial Validation is implemented",
        (
            "backend/src/services/truth_program.py",
            "backend/src/tasks/truth_validation.py",
            "backend/tests/test_confidence_program.py",
        ),
        ("preview_adversarial_validation", "conflicting", "stale", "false"),
        "Numbered workstream 5",
    ),
    ArtifactRequirement(
        "Workstream 6 - Golden Set / Benchmarking is implemented",
        (
            "backend/src/services/golden_benchmark.py",
            "backend/src/services/truth_health.py",
            "backend/tests/test_confidence_program.py",
        ),
        ("false_merge", "false_split", "building_link_accuracy", "freshness_accuracy"),
        "Numbered workstream 6",
    ),
    ArtifactRequirement(
        "Workstream 7 - Compute-Heavy Reconciliation is dry-run, resumable, and gated",
        (
            "backend/src/tasks/entity_resolution.py",
            "backend/src/services/lead_reconciliation.py",
            "backend/src/tasks/truth_materialization.py",
            "backend/scripts/lead_reconciliation_report.py",
        ),
        ("dry_run", "run_id", "confirm_execute", "candidate"),
        "Numbered workstream 7",
    ),
    ArtifactRequirement(
        "Workstream 8 - Human Review Workflow exists with dry-run decisions",
        (
            "backend/src/services/truth_review.py",
            "backend/src/routers/truth.py",
            "frontend/components/SettingsPage.tsx",
        ),
        ("review_queue", "confirm_execute", "contradicting_evidence"),
        "Numbered workstream 8",
    ),
    ArtifactRequirement(
        "Workstream 9 - Outreach Feedback Loop feeds truth claims or read-only summaries",
        (
            "backend/src/services/outreach_feedback.py",
            "backend/src/routers/outreach.py",
            "backend/src/routers/leads.py",
        ),
        ("does_not_manage", "confirmed_manager", "truth_claim_status"),
        "Numbered workstream 9",
    ),
    ArtifactRequirement(
        "Workstream 10 - Truth Dashboard exposes data-health and confidence status",
        (
            "backend/src/services/truth_health.py",
            "backend/src/routers/truth.py",
            "frontend/components/SettingsPage.tsx",
            "frontend/services/truth-api.ts",
        ),
        ("load_truth_dashboard", "health-report", "source_refresh", "golden_benchmark"),
        "Numbered workstream 10",
    ),
    ArtifactRequirement(
        "Workstream 11 - Actionability Rules define safe-use thresholds",
        (
            "backend/src/services/confidence.py",
            "backend/src/services/truth_health.py",
            "backend/docs/10-data-truth-confidence-program.md",
        ),
        ("broad_discovery", "ranked_sourcing", "recommended_outreach", "acquisition_quality_diligence", "min_supporting_sources"),
        "Numbered workstream 11",
    ),
    ArtifactRequirement(
        "Workstream 12 - Production Safety gates mutating operations",
        (
            "backend/src/routers/jobs.py",
            "backend/src/services/truth_activation.py",
            "backend/scripts/truth_migration_preflight.py",
            "backend/scripts/truth_api_workflow_smoke.py",
        ),
        ("approval_required", "confirm_execute", "mutations_planned", "rollback"),
        "Numbered workstream 12",
    ),
    ArtifactRequirement(
        "Frontend truth surfaces and API clients exist",
        (
            "frontend/services/truth-api.ts",
            "frontend/components/LeadDetail.tsx",
            "frontend/components/BuildingDetailPage.tsx",
            "frontend/components/SettingsPage.tsx",
        ),
        ("activation-packet", "Evidence Ledger", "Truth", "TruthActionabilityRule"),
        "Deliverable: frontend surfaces for confidence and evidence",
    ),
    ArtifactRequirement(
        "Database/model/API changes exist for claims, evidence, confidence, relationships, and review",
        (
            "backend/src/models/truth.py",
            "backend/src/routers/truth.py",
            "backend/src/services/truth_program.py",
            "backend/src/services/truth_review.py",
        ),
        ("TruthClaim", "TruthEvidence", "confidence", "review_queue"),
        "Deliverable: database/model/API changes",
    ),
    ArtifactRequirement(
        "Backend jobs/services exist for validation, reconciliation, scoring, and source audits",
        (
            "backend/src/tasks/truth_materialization.py",
            "backend/src/tasks/truth_validation.py",
            "backend/src/services/confidence.py",
            "backend/src/services/truth_adjudication.py",
            "backend/src/services/source_audit.py",
            "backend/src/services/lead_reconciliation.py",
        ),
        ("materialize", "validation", "confidence", "source_refresh_plan", "reconciliation", "adjudication"),
        "Deliverable: backend jobs/services",
    ),
    ArtifactRequirement(
        "Production safety, preflight, smoke, and production probes exist",
        (
            "backend/scripts/truth_migration_preflight.py",
            "backend/scripts/truth_activation_packet.py",
            "backend/scripts/truth_api_workflow_smoke.py",
            "backend/scripts/truth_production_probe.py",
            "backend/scripts/truth_materialization_rollback.py",
            "backend/scripts/truth_validation_run.py",
            "backend/scripts/truth_validation_rollback.py",
            "backend/scripts/truth_adjudication_preview.py",
        ),
        ("mutations_planned", "confirm_execute", "production_business_use_allowed", "rollback_materialization_run", "rollback_validation_run", "adjudication"),
        "Verification gate: dry-run/preflight/smoke/production comparison",
    ),
    ArtifactRequirement(
        "Operator runbooks expose truth activation and completion gates",
        (
            "backend/README.md",
            "backend/docs/08-operations-runbook.md",
            "README.md",
        ),
        ("truth_completion_audit.py", "business_use_allowed", "confirm_execute"),
        "Deliverable: runbooks and next-step gates",
    ),
    ArtifactRequirement(
        "Verification commands are documented with current results",
        (
            "backend/docs/11-truth-confidence-completion-audit.md",
            "CODEX_REVIEW_HANDOFF.md",
            ".github/workflows/ci.yml",
        ),
        ("241 passed", "55 passed", "npm run build", "ruff check", "activation_gap_count", "blocked_activation_step_count", "activation_claim_failures", "production_probe_failures"),
        "Verification requirements: backend tests, frontend tests, frontend build, lint",
    ),
    ArtifactRequirement(
        "Representative API/browser workflows are verified without mutation",
        (
            "backend/scripts/truth_api_workflow_smoke.py",
            "frontend/scripts/verify-activation-packet-smoke.mjs",
            "frontend/scripts/verify-truth-workflows-smoke.mjs",
            "backend/docs/11-truth-confidence-completion-audit.md",
        ),
        ("lead search", "building", "activation", "recent job IDs", "safe-clickthrough"),
        "Verification requirements: representative API/browser workflows",
    ),
    ArtifactRequirement(
        "Production/live data-health comparison is explicit and blocks business use when not ready",
        (
            "backend/scripts/truth_production_probe.py",
            "backend/docs/11-truth-confidence-completion-audit.md",
            "CODEX_REVIEW_HANDOFF.md",
        ),
        (
            "truth_surface_status",
            "production_business_use_allowed=false",
            "production_data_health_ready",
            "activation_gaps",
            "data_health_gaps",
            "data_health_thresholds",
        ),
        "Verification requirement: compare production/live data-health outputs",
    ),
    ArtifactRequirement(
        "Targeted tests cover confidence, ledger, source audit, workflows, and production readiness",
        (
            "backend/tests/test_confidence_program.py",
            "backend/tests/test_quality_source_audit_contract.py",
            "backend/tests/test_truth_api_workflow_smoke.py",
            "backend/tests/test_truth_production_probe.py",
            "frontend/services/truth-api.test.ts",
            "frontend/components/BuildingDetailPage.test.tsx",
            "frontend/components/SettingsPage.test.tsx",
        ),
        ("activation", "source", "truth", "blocked", "why_we_believe"),
        "Verification requirements: targeted confidence, ledger, reconciliation, source-audit behavior",
    ),
]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _term_present(repo_root: Path, term: str, files: tuple[str, ...]) -> bool:
    lowered = term.lower()
    return any(lowered in _read_text(repo_root / file_name).lower() for file_name in files)


def build_artifact_checklist(repo_root: Path = REPO_ROOT) -> list[dict[str, Any]]:
    checklist: list[dict[str, Any]] = []
    for item in ARTIFACT_REQUIREMENTS:
        missing_files = [file_name for file_name in item.files if not (repo_root / file_name).exists()]
        missing_terms = [
            term for term in item.required_terms
            if not _term_present(repo_root, term, item.files)
        ]
        status = "satisfied" if not missing_files and not missing_terms else "missing"
        checklist.append({
            "requirement": item.requirement,
            "source_item": item.source_item,
            "status": status,
            "files": list(item.files),
            "missing_files": missing_files,
            "required_terms": list(item.required_terms),
            "missing_terms": missing_terms,
        })
    return checklist


def _gap_summaries(gaps: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    return [
        {
            "severity": gap.get("severity"),
            "area": gap.get("area"),
            "message": gap.get("message"),
        }
        for gap in gaps[:limit]
    ]


def _activation_step_summaries(steps: list[dict[str, Any]], *, limit: int = 6) -> list[dict[str, Any]]:
    blocked_statuses = {"approval_required", "blocked", "needs_review", "manual_review", "ready"}
    blocked_steps = [
        step for step in steps
        if str(step.get("status") or "") in blocked_statuses
    ]
    return [
        {
            "step": step.get("step"),
            "status": step.get("status"),
            "reason": step.get("reason"),
            "approval_required": step.get("approval_required"),
            "mutations_planned": step.get("mutations_planned"),
        }
        for step in blocked_steps[:limit]
    ]


def _activation_claim_readiness_failures(activation_packet: dict[str, Any]) -> list[str]:
    claim_readiness = activation_packet.get("claim_readiness")
    if not isinstance(claim_readiness, dict):
        return ["missing_claim_readiness"]

    failures: list[str] = []
    if claim_readiness.get("has_materialized_claims") is not True:
        failures.append("no_materialized_claims")
    if claim_readiness.get("has_verified_claims") is not True:
        failures.append("no_verified_claims")
    if claim_readiness.get("has_no_critical_or_high_gaps") is not True:
        failures.append("critical_or_high_gaps")
    return failures


def _production_probe_failures(production_probe: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if production_probe.get("truth_surface_status") != "deployed":
        failures.append("production_truth_surface_not_ready")
    if production_probe.get("production_data_health_ready") is not True:
        failures.append("production_data_health_not_ready")
    if production_probe.get("production_business_use_allowed") is not True:
        failures.append("production_business_use_not_allowed")
    if not isinstance(production_probe.get("data_health_thresholds"), dict):
        failures.append("missing_production_data_health_thresholds")
    for key, failure_name in (
        ("trust_gaps", "production_trust_gap_evidence"),
        ("activation_gaps", "production_activation_gap_evidence"),
        ("data_health_gaps", "production_data_health_gap_evidence"),
    ):
        gap_value = production_probe.get(key)
        if not isinstance(gap_value, list):
            failures.append(f"missing_{failure_name}")
        elif gap_value:
            failures.append(f"open_{failure_name}")
    return failures


def _source_overlap_recording_blocker(health_report: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(health_report, dict):
        return None

    adjudication_preview = health_report.get("adjudication_preview")
    if not isinstance(adjudication_preview, dict):
        return None

    ledger_overlap = adjudication_preview.get("ledger_source_overlap") or {}
    manager_preview = adjudication_preview.get("manager_external_source_acquisition_preview") or {}
    operator_preview = adjudication_preview.get("operator_confirmed_management_preview") or {}
    if not isinstance(manager_preview, dict):
        return None

    source_ready_if_recorded = int(manager_preview.get("source_ready_if_recorded_count") or 0)
    simulated_source_ready = int(
        (manager_preview.get("post_recording_simulation") or {}).get("source_ready_fact_group_count") or 0
    )
    ledger_source_ready = int(ledger_overlap.get("source_ready_fact_group_count") or 0)
    if source_ready_if_recorded <= 0 or simulated_source_ready <= 0 or ledger_source_ready > 0:
        return None

    batch_preview = manager_preview.get("manual_evidence_batch_preview") or {}
    strict_batch_preview = batch_preview.get("recommended_strict_manager_proof_batch") or {}
    simulation = manager_preview.get("post_recording_simulation") or {}
    next_source_batches = manager_preview.get("next_source_batches") or {}
    new_relationship_candidates = manager_preview.get("new_relationship_candidates") or []
    new_relationship_source_family_counts: dict[str, int] = {}
    for candidate in new_relationship_candidates:
        if not isinstance(candidate, dict):
            continue
        source_family = str(candidate.get("source_family") or "unknown")
        new_relationship_source_family_counts[source_family] = (
            new_relationship_source_family_counts.get(source_family, 0) + 1
        )
    operator_seed_batches = operator_preview.get("second_source_seed_batches") or {}
    operator_source_proposals = [
        proposal
        for proposal in (operator_seed_batches.get("proposals") or [])
        if isinstance(proposal, dict)
    ]
    operator_strict_gap_candidates = [
        {
            "bbl": proposal.get("bbl"),
            "address": proposal.get("address"),
            "manager_name": proposal.get("manager_name"),
            "strict_manager_gap_status": proposal.get("strict_manager_gap_status"),
            "missing_manager_proof_source_family_count": proposal.get(
                "missing_manager_proof_source_family_count"
            ),
            "next_required_manager_proof": proposal.get("next_required_manager_proof"),
        }
        for proposal in operator_source_proposals
        if proposal.get("strict_manager_gap_status") == "broad_source_ready_not_strict"
    ]
    return {
        "gate": "source_overlap_recording",
        "reason": (
            "Manager source overlap is proven only in read-only preview/simulation; "
            "the evidence batch has not been approved and recorded into the ledger."
        ),
        "evidence": {
            "approval_required": True,
            "required_command": (
                "python scripts/truth_manager_external_evidence_batch.py "
                "--strict-manager-proof-only "
                "--execute --confirm-execute --indent 2"
            ),
            "all_source_ready_command": (
                "python scripts/truth_manager_external_evidence_batch.py "
                "--execute --confirm-execute --indent 2"
            ),
            "lead_id": manager_preview.get("lead_id"),
            "candidate_source_count": manager_preview.get("candidate_source_count"),
            "matched_evidence_candidate_count": manager_preview.get("matched_evidence_candidate_count"),
            "unmatched_candidate_count": manager_preview.get("unmatched_candidate_count"),
            "new_relationship_candidate_count": manager_preview.get("new_relationship_candidate_count"),
            "new_relationship_counts_as_current_ledger_overlap": False,
            "new_relationship_approval_required_for_relationship_creation": True,
            "new_relationship_source_family_counts": dict(sorted(new_relationship_source_family_counts.items())),
            "new_relationship_candidates_sample": [
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "source_name": candidate.get("source_name"),
                    "source_family": candidate.get("source_family"),
                    "external_address": candidate.get("external_address"),
                    "local_address": candidate.get("local_address"),
                    "manager_name": candidate.get("manager_name"),
                    "evidence_role": candidate.get("evidence_role"),
                    "source_url": candidate.get("source_url"),
                    "local_building_match": candidate.get("local_building_match") or {},
                    "current_relationship_state": candidate.get("current_relationship_state"),
                    "safe_action": candidate.get("safe_action"),
                }
                for candidate in new_relationship_candidates[:5]
            ],
            "new_relationship_policy": (
                "Source-backed new relationships are source-acquisition leads only. They are not counted as "
                "current-ledger source overlap and must go through relationship review before any evidence is recorded."
            ),
            "next_source_batch_count": next_source_batches.get("candidate_count"),
            "next_source_boundary_notes": next_source_batches.get("source_boundary_notes"),
            "next_source_reviewed_findings": next_source_batches.get("reviewed_source_findings"),
            "next_source_search_pack_sample": [
                {
                    "bbl": proposal.get("bbl"),
                    "address": proposal.get("address"),
                    "existing_manager_proof_source_families": proposal.get(
                        "existing_manager_proof_source_families"
                    ),
                    "suggested_source_families": proposal.get("suggested_source_families"),
                    "search_queries": proposal.get("search_queries"),
                    "source_targets": proposal.get("source_targets"),
                }
                for proposal in (next_source_batches.get("proposals") or [])[:3]
            ],
            "claim_group_count": manager_preview.get("claim_group_count"),
            "clean_exact_claim_count": manager_preview.get("clean_exact_claim_count"),
            "source_ready_if_recorded_count": source_ready_if_recorded,
            "independent_source_ready_if_recorded_count": manager_preview.get(
                "independent_source_ready_if_recorded_count"
            ),
            "strict_manager_source_ready_if_recorded_count": manager_preview.get(
                "strict_manager_source_ready_if_recorded_count"
            ),
            "manual_evidence_template_count": batch_preview.get("template_count"),
            "planned_upsert_count": batch_preview.get("planned_upsert_count"),
            "strict_manager_proof_template_count": strict_batch_preview.get("template_count"),
            "strict_manager_proof_claim_group_count": strict_batch_preview.get("claim_group_count"),
            "strict_manager_proof_planned_upsert_count": strict_batch_preview.get("planned_upsert_count"),
            "excluded_address_review_candidate_count": batch_preview.get(
                "excluded_address_review_candidate_count"
            ),
            "simulated_source_ready_fact_group_count": simulated_source_ready,
            "simulated_strict_manager_source_ready_fact_group_count": simulation.get(
                "strict_manager_source_ready_fact_group_count"
            ),
            "simulated_safe_to_mark_verified_count": simulation.get("safe_to_mark_verified_count"),
            "simulation_blocker_counts": simulation.get("blocker_counts"),
            "current_ledger_single_source_fact_group_count": ledger_overlap.get("single_source_fact_group_count"),
            "current_ledger_multi_source_fact_group_count": ledger_overlap.get("multi_source_fact_group_count"),
            "current_ledger_source_ready_fact_group_count": ledger_source_ready,
            "operator_confirmed": {
                "candidate_count": operator_preview.get("candidate_count"),
                "matched_candidate_count": operator_preview.get("matched_candidate_count"),
                "new_relationship_candidate_count": operator_preview.get("new_relationship_candidate_count"),
                "conflict_candidate_count": operator_preview.get("conflict_candidate_count"),
                "required_command": (
                    "python scripts/truth_operator_confirmed_evidence_batch.py "
                    "--strict-manager-proof-only --execute --confirm-execute --indent 2"
                ),
                "all_source_ready_command": (
                    "python scripts/truth_operator_confirmed_evidence_batch.py "
                    "--execute --confirm-execute --indent 2"
                ),
                "source_acquisition_command": (
                    "python scripts/truth_operator_confirmed_evidence_batch.py "
                    "--source-acquisition-only --indent 2"
                ),
                "manual_evidence_template_count": operator_preview.get("manual_evidence_template_count"),
                "planned_upsert_count": operator_preview.get("planned_upsert_count"),
                "source_ready_if_recorded_count": operator_preview.get("source_ready_if_recorded_count"),
                "independent_source_ready_if_recorded_count": operator_preview.get(
                    "independent_source_ready_if_recorded_count"
                ),
                "strict_manager_source_ready_if_recorded_count": operator_preview.get(
                    "strict_manager_source_ready_if_recorded_count"
                ),
                "verified_safe_if_recorded_count": operator_preview.get("verified_safe_if_recorded_count"),
                "second_source_seed_count": (
                    operator_seed_batches.get("candidate_count")
                ),
                "second_source_template_count": operator_preview.get("second_source_template_count"),
                "second_source_boundary_notes": operator_seed_batches.get("source_boundary_notes"),
                "second_source_reviewed_findings": operator_seed_batches.get("reviewed_source_findings"),
                "strict_gap_summary": {
                    "proposal_count": len(operator_source_proposals),
                    "strict_ready_proposal_count": sum(
                        1
                        for proposal in operator_source_proposals
                        if proposal.get("strict_manager_source_ready_if_recorded") is True
                    ),
                    "broad_source_ready_not_strict_count": len(operator_strict_gap_candidates),
                    "gap_candidates": operator_strict_gap_candidates[:5],
                },
                "second_source_search_pack_sample": [
                    {
                        "bbl": proposal.get("bbl"),
                        "address": proposal.get("address"),
                        "manager_name": proposal.get("manager_name"),
                        "existing_manager_proof_source_families": proposal.get(
                            "existing_manager_proof_source_families"
                        ),
                        "supporting_source_families_if_recorded": proposal.get(
                            "supporting_source_families_if_recorded"
                        ) or proposal.get("existing_source_families_if_recorded"),
                        "suggested_source_families": proposal.get("suggested_source_families"),
                        "search_queries": proposal.get("search_queries"),
                        "source_targets": proposal.get("source_targets"),
                        "strict_manager_source_ready_if_recorded": proposal.get(
                            "strict_manager_source_ready_if_recorded"
                        ),
                        "strict_manager_gap_status": proposal.get("strict_manager_gap_status"),
                        "missing_manager_proof_source_family_count": proposal.get(
                            "missing_manager_proof_source_family_count"
                        ),
                        "next_required_manager_proof": proposal.get("next_required_manager_proof"),
                    }
                    for proposal in operator_source_proposals[:3]
                ],
                "policy": operator_preview.get("policy"),
            },
        },
    }


def _runtime_gate_blockers(
    *,
    health_report: dict[str, Any] | None,
    activation_packet: dict[str, Any] | None,
    production_probe: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if health_report is None:
        blockers.append({
            "gate": "local_truth_health",
            "reason": "No local truth health report was supplied or generated.",
        })
    else:
        summary = health_report.get("summary") or {}
        thresholds = health_report.get("thresholds") or {}
        trust_gaps = list(health_report.get("trust_gaps") or [])
        minimum_claim_count = int(thresholds.get("minimum_claim_count") or 1)
        claim_count = int(summary.get("claim_count") or 0)
        verified_claim_count = int(summary.get("verified_claim_count") or 0)
        critical_or_high_gap_count = int(summary.get("critical_or_high_gap_count") or 0)
        health_failures: list[str] = []
        if summary.get("trust_posture") == "not_ready":
            health_failures.append("trust_posture_not_ready")
        if claim_count < minimum_claim_count:
            health_failures.append("insufficient_materialized_claims")
        if verified_claim_count <= 0:
            health_failures.append("no_verified_claims")
        if critical_or_high_gap_count > 0:
            health_failures.append("critical_or_high_trust_gaps")
        if trust_gaps:
            health_failures.append("open_trust_gaps")
        if health_failures:
            blockers.append({
                "gate": "local_truth_health",
                "reason": "Local truth health does not yet prove business-ready claim coverage.",
                "evidence": {
                    **summary,
                    "minimum_claim_count": minimum_claim_count,
                    "trust_gap_count": len(trust_gaps),
                    "top_trust_gaps": _gap_summaries(trust_gaps),
                    "health_failures": health_failures,
                },
            })

    source_overlap_blocker = _source_overlap_recording_blocker(health_report)
    if source_overlap_blocker is not None:
        blockers.append(source_overlap_blocker)

    if activation_packet is None:
        blockers.append({
            "gate": "activation_packet",
            "reason": "No activation packet was supplied or generated.",
        })
    else:
        activation_claim_failures = _activation_claim_readiness_failures(activation_packet)
    if activation_packet is not None and (
        activation_packet.get("business_use_allowed") is not True
        or activation_claim_failures
    ):
        activation_steps = list(
            activation_packet.get("activation_checklist")
            or (health_report or {}).get("activation_checklist")
            or []
        )
        blockers.append({
            "gate": "activation_packet",
            "reason": (
                "Activation packet does not allow business use."
                if activation_packet.get("business_use_allowed") is not True
                else "Activation packet does not prove materialized, verified, gap-free claim readiness."
            ),
            "evidence": {
                "verdict": activation_packet.get("verdict"),
                "business_use_allowed": activation_packet.get("business_use_allowed"),
                "approval_required": activation_packet.get("approval_required"),
                "claim_readiness": activation_packet.get("claim_readiness"),
                "verification_frontier": activation_packet.get("verification_frontier"),
                "activation_claim_failures": activation_claim_failures,
                "blocked_activation_step_count": len(_activation_step_summaries(activation_steps, limit=len(activation_steps))),
                "top_blocked_activation_steps": _activation_step_summaries(activation_steps),
            },
        })

    if production_probe is None:
        blockers.append({
            "gate": "production_truth_surface",
            "reason": "No production truth-surface probe was supplied or generated.",
        })
    else:
        production_probe_failures = _production_probe_failures(production_probe)
    if production_probe is not None and production_probe_failures:
        blockers.append({
            "gate": "production_truth_surface",
            "reason": "Production truth surface is not business-use ready.",
            "evidence": {
                "truth_surface_status": production_probe.get("truth_surface_status"),
                "production_data_health_ready": production_probe.get("production_data_health_ready"),
                "production_business_use_allowed": production_probe.get("production_business_use_allowed"),
                "production_probe_failures": production_probe_failures,
                "data_health_thresholds": production_probe.get("data_health_thresholds"),
                "trust_gap_count": len(production_probe.get("trust_gaps") or []),
                "activation_gap_count": len(production_probe.get("activation_gaps") or []),
                "data_health_gap_count": len(production_probe.get("data_health_gaps") or []),
                "top_trust_gaps": _gap_summaries(list(production_probe.get("trust_gaps") or [])),
                "top_activation_gaps": _gap_summaries(list(production_probe.get("activation_gaps") or [])),
                "top_data_health_gaps": _gap_summaries(list(production_probe.get("data_health_gaps") or [])),
            },
        })
    return blockers


def _blocker_by_gate(runtime_blockers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(blocker.get("gate") or ""): blocker for blocker in runtime_blockers}


def _source_overlap_prompt_evidence(
    *,
    health_report: dict[str, Any] | None,
    blocker_evidence: dict[str, Any],
) -> dict[str, Any]:
    """Return source-overlap evidence whether the gate is blocked or already satisfied."""
    if blocker_evidence:
        return {
            "current_ledger_total_fact_group_count": blocker_evidence.get(
                "current_ledger_total_fact_group_count"
            ),
            "current_ledger_single_source_fact_group_count": blocker_evidence.get(
                "current_ledger_single_source_fact_group_count"
            ),
            "current_ledger_multi_source_fact_group_count": blocker_evidence.get(
                "current_ledger_multi_source_fact_group_count"
            ),
            "current_ledger_source_ready_fact_group_count": blocker_evidence.get(
                "current_ledger_source_ready_fact_group_count"
            ),
            "source_ready_if_recorded_count": blocker_evidence.get("source_ready_if_recorded_count"),
            "strict_manager_source_ready_if_recorded_count": blocker_evidence.get(
                "strict_manager_source_ready_if_recorded_count"
            ),
            "approval_required": blocker_evidence.get("approval_required"),
        }

    adjudication_preview = (health_report or {}).get("adjudication_preview") or {}
    ledger_overlap = adjudication_preview.get("ledger_source_overlap") or {}
    manager_preview = adjudication_preview.get("manager_external_source_acquisition_preview") or {}
    post_recording_simulation = manager_preview.get("post_recording_simulation") or {}
    ledger_source_ready = int(ledger_overlap.get("source_ready_fact_group_count") or 0)
    ledger_multi_source = int(ledger_overlap.get("multi_source_fact_group_count") or 0)
    return {
        "current_ledger_total_fact_group_count": ledger_overlap.get("total_fact_group_count"),
        "current_ledger_single_source_fact_group_count": ledger_overlap.get("single_source_fact_group_count"),
        "current_ledger_multi_source_fact_group_count": ledger_overlap.get("multi_source_fact_group_count"),
        "current_ledger_source_ready_fact_group_count": ledger_overlap.get("source_ready_fact_group_count"),
        "source_ready_if_recorded_count": manager_preview.get("source_ready_if_recorded_count"),
        "strict_manager_source_ready_if_recorded_count": manager_preview.get(
            "strict_manager_source_ready_if_recorded_count"
        ),
        "simulated_source_ready_fact_group_count": post_recording_simulation.get(
            "source_ready_fact_group_count"
        ),
        "simulated_strict_manager_source_ready_fact_group_count": post_recording_simulation.get(
            "strict_manager_source_ready_fact_group_count"
        ),
        "approval_required": False if ledger_source_ready > 0 and ledger_multi_source > 0 else None,
    }


def build_prompt_to_artifact_checklist(
    *,
    artifact_checklist: list[dict[str, Any]],
    runtime_blockers: list[dict[str, Any]],
    health_report: dict[str, Any] | None = None,
    activation_packet: dict[str, Any] | None = None,
    production_probe: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Map the user-facing completion requirements to concrete evidence."""
    missing_artifacts = [item for item in artifact_checklist if item["status"] != "satisfied"]
    blockers = _blocker_by_gate(runtime_blockers)
    health_summary = (health_report or {}).get("summary") or {}
    source_overlap = (blockers.get("source_overlap_recording") or {}).get("evidence") or {}
    source_overlap_evidence = _source_overlap_prompt_evidence(
        health_report=health_report,
        blocker_evidence=source_overlap,
    )
    production_ready = bool((production_probe or {}).get("production_business_use_allowed"))
    activation_ready = bool((activation_packet or {}).get("business_use_allowed"))
    verified_claim_count = int(health_summary.get("verified_claim_count") or 0)
    verified_source_policy_ready = (
        "local_truth_health" not in blockers
        and "source_overlap_recording" not in blockers
        and "runtime_not_checked" not in blockers
    )

    runtime_not_checked = "runtime_not_checked" in blockers

    def status_for_gate(gate: str) -> str:
        if runtime_not_checked:
            return "runtime_not_checked"
        return "blocked" if gate in blockers else "satisfied"

    return [
        {
            "requirement": "Canonical entities, claim/evidence ledger, confidence scoring, review workflow, API, and UI surfaces exist.",
            "status": "missing" if missing_artifacts else "satisfied",
            "evidence": {
                "artifact_total": len(artifact_checklist),
                "artifact_satisfied": sum(1 for item in artifact_checklist if item["status"] == "satisfied"),
                "artifact_missing": len(missing_artifacts),
            },
        },
        {
            "requirement": "A narrow Harlem manager source-overlap pilot proves real independent overlap without broad dedupe.",
            "status": status_for_gate("source_overlap_recording"),
            "evidence": source_overlap_evidence,
        },
        {
            "requirement": "No single-source claim is marked verified.",
            "status": (
                "runtime_not_checked"
                if runtime_not_checked
                else "satisfied" if verified_claim_count == 0 or verified_source_policy_ready else "requires_review"
            ),
            "evidence": {
                "verified_claim_count": verified_claim_count,
                "reason": (
                    "Artifact-only mode does not inspect current verified claims."
                    if runtime_not_checked
                    else
                    "No verified claims exist in the current local ledger."
                    if verified_claim_count == 0
                    else "Runtime source-overlap gates are clear for verified claims."
                ),
            },
        },
        {
            "requirement": "Read-only previews explain exact claims, sources, contradictions, freshness, confidence, safe action, and next source acquisition.",
            "status": "missing" if missing_artifacts else "satisfied",
            "evidence": {
                "requires_terms": [
                    "manager_external_source_acquisition_preview",
                    "reviewed_source_findings",
                    "next_source_search_pack",
                    "next_source_search_pack_sample",
                    "truth_verification_frontier",
                    "source_ready_below_verified",
                    "source_acquisition_frontier",
                    "required_real_evidence",
                    "official_hpd_query_packet_only",
                    "official_query_urls",
                    "read_only_preview_command",
                    "source_dataset_ids",
                    "evidence_request_packet",
                    "source_ready_requests",
                    "source_acquisition_requests",
                    "reviewed_source_finding_count",
                    "reviewed_source_history_status",
                    "evidence_acquisition_status",
                    "verification_readiness_gate",
                    "safe_actions",
                ],
                "artifact_missing": len(missing_artifacts),
            },
        },
        {
            "requirement": "Local truth health and activation packet allow business use only after verified-claim and review gates pass.",
            "status": (
                "runtime_not_checked"
                if runtime_not_checked
                else "satisfied" if activation_ready and "local_truth_health" not in blockers else "blocked"
            ),
            "evidence": {
                "trust_posture": health_summary.get("trust_posture"),
                "claim_count": health_summary.get("claim_count"),
                "verified_claim_count": health_summary.get("verified_claim_count"),
                "business_use_allowed": activation_ready,
                "blocking_gates": [
                    gate for gate in ("local_truth_health", "activation_packet") if gate in blockers
                ],
            },
        },
        {
            "requirement": "Production truth surface remains blocked until production data-health gates pass.",
            "status": (
                "runtime_not_checked"
                if runtime_not_checked
                else "satisfied" if production_ready and "production_truth_surface" not in blockers else "blocked"
            ),
            "evidence": {
                "truth_surface_status": (production_probe or {}).get("truth_surface_status"),
                "production_data_health_ready": (production_probe or {}).get("production_data_health_ready"),
                "production_business_use_allowed": production_ready,
                "blocking_gate": "production_truth_surface" if "production_truth_surface" in blockers else None,
            },
        },
    ]


def build_completion_audit(
    *,
    artifact_checklist: list[dict[str, Any]],
    health_report: dict[str, Any] | None = None,
    activation_packet: dict[str, Any] | None = None,
    production_probe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    missing_artifacts = [item for item in artifact_checklist if item["status"] != "satisfied"]
    runtime_blockers = _runtime_gate_blockers(
        health_report=health_report,
        activation_packet=activation_packet,
        production_probe=production_probe,
    )
    activation_steps = list(
        (activation_packet or {}).get("activation_checklist")
        or (health_report or {}).get("activation_checklist")
        or []
    )
    prompt_to_artifact_checklist = build_prompt_to_artifact_checklist(
        artifact_checklist=artifact_checklist,
        runtime_blockers=runtime_blockers,
        health_report=health_report,
        activation_packet=activation_packet,
        production_probe=production_probe,
    )
    complete = not missing_artifacts and not runtime_blockers
    return {
        "dry_run": True,
        "mutations_planned": 0,
        "objective": (
            "Transform Double Edge from a lead-table app into an evidence-backed "
            "NYC property intelligence and data-confidence system."
        ),
        "completion_status": "complete" if complete else "not_complete",
        "success_criteria": [
            item["requirement"] for item in prompt_to_artifact_checklist
        ],
        "prompt_to_artifact_checklist": prompt_to_artifact_checklist,
        "artifact_checklist": artifact_checklist,
        "artifact_summary": {
            "total": len(artifact_checklist),
            "satisfied": sum(1 for item in artifact_checklist if item["status"] == "satisfied"),
            "missing": len(missing_artifacts),
        },
        "runtime_blockers": runtime_blockers,
        "health_summary": (health_report or {}).get("summary"),
        "activation_summary": {
            **{
                key: (activation_packet or {}).get(key)
                for key in ("verdict", "business_use_allowed", "approval_required", "trust_posture")
            },
            "claim_readiness": (activation_packet or {}).get("claim_readiness"),
            "verification_frontier": (activation_packet or {}).get("verification_frontier"),
            "blocked_activation_step_count": len(_activation_step_summaries(activation_steps, limit=len(activation_steps))),
        },
        "production_summary": {
            **{
                key: (production_probe or {}).get(key)
                for key in ("truth_surface_status", "production_data_health_ready", "production_business_use_allowed")
            },
            "trust_gap_count": len((production_probe or {}).get("trust_gaps") or []),
            "activation_gap_count": len((production_probe or {}).get("activation_gaps") or []),
            "data_health_gap_count": len((production_probe or {}).get("data_health_gaps") or []),
        },
    }


def build_artifact_only_audit(*, artifact_checklist: list[dict[str, Any]]) -> dict[str, Any]:
    missing_artifacts = [item for item in artifact_checklist if item["status"] != "satisfied"]
    runtime_blockers = [{
        "gate": "runtime_not_checked",
        "reason": "Artifact-only mode does not evaluate local truth health, activation packet, or production readiness.",
    }]
    prompt_to_artifact_checklist = build_prompt_to_artifact_checklist(
        artifact_checklist=artifact_checklist,
        runtime_blockers=runtime_blockers,
    )
    return {
        "dry_run": True,
        "mutations_planned": 0,
        "objective": (
            "Transform Double Edge from a lead-table app into an evidence-backed "
            "NYC property intelligence and data-confidence system."
        ),
        "completion_status": "artifacts_satisfied_runtime_not_checked" if not missing_artifacts else "artifact_incomplete",
        "success_criteria": [
            item["requirement"] for item in prompt_to_artifact_checklist
        ],
        "prompt_to_artifact_checklist": prompt_to_artifact_checklist,
        "artifact_checklist": artifact_checklist,
        "artifact_summary": {
            "total": len(artifact_checklist),
            "satisfied": sum(1 for item in artifact_checklist if item["status"] == "satisfied"),
            "missing": len(missing_artifacts),
        },
        "runtime_blockers": runtime_blockers,
    }


async def _runtime_payloads(*, include_production: bool, production_timeout: float) -> dict[str, dict[str, Any] | None]:
    factory = get_session_factory()
    async with factory() as session:
        schema_status = await load_truth_schema_status(session)
        health_report = await build_truth_health_report(session)
        await session.rollback()
    preflight = build_runtime_preflight_summary(schema_status)
    activation_packet = build_activation_packet(preflight=preflight, health_report=health_report)
    production_probe = run_probe(
        base_url="https://hpd-leads-app-production.up.railway.app",
        timeout=production_timeout,
    ) if include_production else None
    await shutdown_engine()
    return {
        "health_report": health_report,
        "activation_packet": activation_packet,
        "production_probe": production_probe,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-only", action="store_true", help="Check only prompt-to-artifact coverage; intended for CI.")
    parser.add_argument("--include-runtime", action="store_true", help="Read local DB health and activation gates.")
    parser.add_argument("--include-production", action="store_true", help="Probe production truth endpoints.")
    parser.add_argument("--production-timeout", type=float, default=20.0)
    parser.add_argument("--indent", type=int, default=2)
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    artifact_checklist = build_artifact_checklist()
    if args.artifacts_only:
        audit = build_artifact_only_audit(artifact_checklist=artifact_checklist)
        print(json.dumps(audit, indent=args.indent, default=str))
        return 0 if audit["artifact_summary"]["missing"] == 0 else 1

    runtime = (
        await _runtime_payloads(
            include_production=args.include_production,
            production_timeout=args.production_timeout,
        )
        if args.include_runtime or args.include_production
        else {"health_report": None, "activation_packet": None, "production_probe": None}
    )
    audit = build_completion_audit(
        artifact_checklist=artifact_checklist,
        health_report=runtime["health_report"],
        activation_packet=runtime["activation_packet"],
        production_probe=runtime["production_probe"],
    )
    print(json.dumps(audit, indent=args.indent, default=str))
    return 0 if audit["completion_status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
