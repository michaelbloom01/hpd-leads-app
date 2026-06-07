from argparse import Namespace

from scripts.truth_api_workflow_smoke import (
    Check,
    job_ids,
    render_report,
    summarize_payload,
    validate_activation_packet,
    validate_completion_audit,
    validate_manual_evidence_preview,
    validate_manager_source_acquisition_packet,
    validate_materialization_preview,
    validate_source_overlap_blocker_report,
    validate_source_overlap_approval_packet,
    validate_source_overlap_post_recording_check,
)


def test_truth_api_workflow_smoke_summarizes_schema_gated_payloads():
    payload = {
        "verdict": "schema_approval_required",
        "business_use_allowed": False,
        "approval_required": True,
        "schema": {
            "ready": False,
            "current_revision": "008_lead_lineage",
            "expected_revision": "010_truth_manifest",
            "missing_tables": ["truth_claims", "truth_evidence"],
        },
        "source_refresh": {
            "approval_required": True,
            "planned_job_count": 14,
            "refreshable_job_count": 13,
            "blocked_job_count": 1,
            "affected_source_count": 20,
            "non_refreshable_gap_count": 1,
            "next_jobs": [{"job_type": "acris"}],
        },
        "next_safe_steps": [
            {"step": "review_preflight_sql", "mutates_data": False},
            {"step": "apply_truth_schema", "mutates_data": True, "requires_explicit_approval": True},
        ],
        "claim_readiness": {
            "claim_count": 0,
            "verified_claim_count": 0,
            "critical_or_high_gap_count": 2,
            "has_materialized_claims": False,
            "has_verified_claims": False,
            "has_no_critical_or_high_gaps": False,
        },
        "rollback": {"offline_rollback_command": "python -m alembic downgrade 010_truth_manifest:008_lead_lineage --sql"},
    }

    summary = summarize_payload(payload)

    assert summary == {
        "verdict": "schema_approval_required",
        "business_use_allowed": False,
        "approval_required": True,
        "schema": {
            "ready": False,
            "current_revision": "008_lead_lineage",
            "expected_revision": "010_truth_manifest",
            "missing_tables": ["truth_claims", "truth_evidence"],
        },
        "source_refresh": {
            "approval_required": True,
            "planned_job_count": 14,
            "refreshable_job_count": 13,
            "blocked_job_count": 1,
            "affected_source_count": 20,
            "non_refreshable_gap_count": 1,
            "next_job_count": 1,
        },
        "claim_readiness": {
            "claim_count": 0,
            "verified_claim_count": 0,
            "critical_or_high_gap_count": 2,
            "has_materialized_claims": False,
            "has_verified_claims": False,
            "has_no_critical_or_high_gaps": False,
        },
        "next_safe_steps": {
            "count": 2,
            "mutating_approval_required_count": 1,
            "non_mutating_count": 1,
        },
        "rollback": {"offline_rollback_command_present": True},
    }


def test_truth_api_workflow_smoke_validates_activation_packet_safety_contract():
    payload = {
        "dry_run": True,
        "mutations_planned": 0,
        "business_use_allowed": False,
        "approval_required": True,
        "rollback": {"offline_rollback_command": "python -m alembic downgrade 010_truth_manifest:008_lead_lineage --sql"},
        "next_safe_steps": [
            {"step": "review_preflight_sql", "mutates_data": False},
            {"step": "apply_truth_schema", "mutates_data": True, "requires_explicit_approval": True},
        ],
        "claim_readiness": {
            "claim_count": 0,
            "verified_claim_count": 0,
            "critical_or_high_gap_count": 2,
            "has_materialized_claims": False,
            "has_verified_claims": False,
            "has_no_critical_or_high_gaps": False,
        },
        "source_refresh": {
            "approval_required": True,
            "planned_job_count": 14,
            "refreshable_job_count": 14,
            "blocked_job_count": 0,
            "next_jobs": [{
                "job_type": "acris",
                "approval_required": True,
                "execute_endpoint": "/api/v1/jobs/acris/start?dry_run=false&confirm_execute=true",
            }],
        },
    }

    validate_activation_packet(payload)


def test_truth_api_workflow_smoke_rejects_activation_packet_without_claim_readiness():
    payload = {
        "dry_run": True,
        "mutations_planned": 0,
        "business_use_allowed": False,
        "approval_required": True,
        "rollback": {"offline_rollback_command": "python -m alembic downgrade 010_truth_manifest:008_lead_lineage --sql"},
        "next_safe_steps": [
            {"step": "review_preflight_sql", "mutates_data": False},
            {"step": "apply_truth_schema", "mutates_data": True, "requires_explicit_approval": True},
        ],
        "source_refresh": {
            "approval_required": True,
            "planned_job_count": 14,
            "refreshable_job_count": 14,
            "blocked_job_count": 0,
            "next_jobs": [{
                "job_type": "acris",
                "approval_required": True,
                "execute_endpoint": "/api/v1/jobs/acris/start?dry_run=false&confirm_execute=true",
            }],
        },
    }

    try:
        validate_activation_packet(payload)
    except AssertionError as exc:
        assert "claim_readiness" in str(exc)
    else:
        raise AssertionError("activation packet without claim_readiness should fail smoke validation")


def test_truth_api_workflow_smoke_validates_completion_audit_prompt_checklist():
    payload = {
        "dry_run": True,
        "mutations_planned": 0,
        "completion_status": "not_complete",
        "success_criteria": ["source overlap"],
        "prompt_to_artifact_checklist": [
            {"requirement": "Artifacts exist.", "status": "satisfied", "evidence": {"artifact_missing": 0}},
            {
                "requirement": "Source overlap is recorded.",
                "status": "runtime_not_checked",
                "evidence": {"current_ledger_source_ready_fact_group_count": 0},
            },
        ],
        "artifact_summary": {"total": 25, "satisfied": 25, "missing": 0},
        "runtime_blockers": [{
            "gate": "source_overlap_recording",
            "reason": "approval required",
            "evidence": {
                "new_relationship_candidate_count": 1,
                "new_relationship_counts_as_current_ledger_overlap": False,
                "new_relationship_approval_required_for_relationship_creation": True,
                "new_relationship_source_family_counts": {"nyc_dof_billing_record": 1},
                "new_relationship_candidates_sample": [{
                    "candidate_id": "nyc-dof-275-greenwich-hpm-billing-record",
                    "source_family": "nyc_dof_billing_record",
                    "external_address": "275 GREENWICH STREET",
                    "local_building_match": {"bbl": "1001327501", "address": "269 GREENWICH STREET"},
                    "current_relationship_state": {
                        "current_building_management_relationship_count": 0,
                        "current_truth_claim_count": 0,
                        "counts_as_current_ledger_overlap": False,
                        "relationship_review_required": True,
                    },
                    "safe_action": "Review as a possible new relationship claim.",
                }],
                "new_relationship_policy": (
                    "Source-backed new relationships are source-acquisition leads only. They are not counted as "
                    "current-ledger source overlap and must go through relationship review before any evidence is recorded."
                ),
                "operator_confirmed": {
                    "strict_gap_summary": {
                        "proposal_count": 4,
                        "strict_ready_proposal_count": 1,
                        "broad_source_ready_not_strict_count": 3,
                        "gap_candidates": [{
                            "bbl": "1008747504",
                            "address": "220 3 AVENUE",
                            "manager_name": "MD Squared Property Group",
                            "strict_manager_gap_status": "broad_source_ready_not_strict",
                            "missing_manager_proof_source_family_count": 1,
                        }],
                    },
                },
            },
        }],
    }

    validate_completion_audit(payload)
    summary = summarize_payload(payload)

    assert summary["completion_status"] == "not_complete"
    assert summary["artifact_summary"] == {"total": 25, "satisfied": 25, "missing": 0}
    assert summary["runtime_blockers"] == {"count": 1, "gates": ["source_overlap_recording"]}
    assert summary["prompt_to_artifact_checklist"] == {
        "count": 2,
        "status_counts": {"runtime_not_checked": 1, "satisfied": 1},
    }


def test_truth_api_workflow_smoke_rejects_not_complete_audit_without_blockers():
    payload = {
        "dry_run": True,
        "mutations_planned": 0,
        "completion_status": "not_complete",
        "success_criteria": ["source overlap"],
        "prompt_to_artifact_checklist": [
            {"requirement": "Artifacts exist.", "status": "satisfied", "evidence": {}},
        ],
        "artifact_summary": {"total": 1, "satisfied": 1, "missing": 0},
        "runtime_blockers": [],
    }

    try:
        validate_completion_audit(payload)
    except AssertionError as exc:
        assert "runtime blockers" in str(exc)
    else:
        raise AssertionError("not-complete completion audit without blockers should fail smoke validation")


def test_truth_api_workflow_smoke_rejects_completion_audit_missing_new_relationship_boundary():
    payload = {
        "dry_run": True,
        "mutations_planned": 0,
        "completion_status": "not_complete",
        "success_criteria": ["source overlap"],
        "prompt_to_artifact_checklist": [
            {"requirement": "Source overlap is recorded.", "status": "blocked", "evidence": {}},
        ],
        "artifact_summary": {"total": 25, "satisfied": 25, "missing": 0},
        "runtime_blockers": [{
            "gate": "source_overlap_recording",
            "reason": "approval required",
            "evidence": {
                "new_relationship_candidate_count": 1,
                "new_relationship_counts_as_current_ledger_overlap": True,
                "new_relationship_approval_required_for_relationship_creation": True,
                "new_relationship_source_family_counts": {"nyc_dof_billing_record": 1},
                "new_relationship_candidates_sample": [{
                    "candidate_id": "nyc-dof-275-greenwich-hpm-billing-record",
                    "source_family": "nyc_dof_billing_record",
                    "local_building_match": {"bbl": "1001327501"},
                    "current_relationship_state": {
                        "current_building_management_relationship_count": 0,
                        "current_truth_claim_count": 0,
                        "counts_as_current_ledger_overlap": False,
                    },
                }],
                "new_relationship_policy": "Source-backed new relationships are not counted as current-ledger source overlap.",
            },
        }],
    }

    try:
        validate_completion_audit(payload)
    except AssertionError as exc:
        assert "current-ledger overlap" in str(exc)
    else:
        raise AssertionError("completion audit with relationship candidates counted as overlap should fail")


def test_truth_api_workflow_smoke_validates_source_overlap_approval_packet():
    payload = {
        "dry_run": True,
        "mutations_planned": 0,
        "current_ledger": {
            "total_fact_group_count": 2063,
            "single_source_fact_group_count": 2063,
            "multi_source_fact_group_count": 0,
            "source_ready_fact_group_count": 0,
            "verification_candidate_count": 0,
        },
        "previewed_overlap_if_approved": {
            "manager_source_ready_if_recorded_count": 13,
            "manager_strict_source_ready_if_recorded_count": 4,
            "operator_source_ready_if_recorded_count": 4,
            "operator_strict_source_ready_if_recorded_count": 1,
            "safe_to_mark_verified_after_recording": 0,
        },
        "source_overlap_recording_gate": {
            "status": "approval_required",
            "current_multi_source_fact_group_count": 0,
            "current_source_ready_fact_group_count": 0,
            "current_verification_candidate_count": 0,
            "source_overlap_proof_satisfied": False,
            "additional_evidence_recording_requires_approval": True,
        },
        "recommended_first_packet": {
            "batch_filter": "strict_manager_proof",
            "approval_required": True,
            "approval_required_before_recording": True,
            "allowed_execute": False,
            "template_count": 15,
            "claim_group_count": 4,
            "manager_proof_source_families": [
                "external_web_profile",
                "ny_dps_order_entry",
                "real_estate_listing",
            ],
            "planned_upsert_count_if_approved": 45,
            "recommended_execute_command": (
                "python scripts/truth_manager_external_evidence_batch.py "
                "--strict-manager-proof-only --execute --confirm-execute --indent 2"
            ),
            "approval_decision_summary": {
                "approval_required": True,
                "batch_filter": "strict_manager_proof",
                "recommended_execute_command": (
                    "python scripts/truth_manager_external_evidence_batch.py "
                    "--strict-manager-proof-only --execute --confirm-execute --indent 2"
                ),
                "would_record_template_count": 15,
                "would_record_claim_group_count": 4,
                "would_plan_upsert_count": 45,
                "included_addresses": [
                    "324 EAST 112 STREET",
                    "36 WEST 138 STREET",
                    "2257 ADAM C POWELL BOULEVARD",
                    "342 WEST 56 STREET",
                ],
                "expected_multi_source_fact_group_count": 4,
                "expected_source_ready_fact_group_count": 4,
                "expected_strict_manager_source_ready_fact_group_count": 4,
                "expected_safe_to_mark_verified_count": 0,
                "single_source_claims_stay_unverified": True,
                "will_mark_verified": False,
                "will_create_or_refresh_source_data": False,
                "will_materialize_new_relationships": False,
                "post_execution_required_checks": [
                    "python scripts/truth_adjudication_preview.py --limit 20 --no-samples --indent 2",
                ],
                "safe_action": "Approve only if the listed exact-property sources have been inspected.",
            },
        },
        "manager_strict_gap_summary": {
            "claim_group_count": 13,
            "strict_ready_claim_group_count": 4,
            "broad_source_ready_not_strict_count": 9,
            "gap_candidates": [
                {
                    "address": "141 WEST 123 STREET",
                    "strict_manager_gap_status": "broad_source_ready_not_strict",
                    "missing_manager_proof_source_family_count": 1,
                    "suggested_source_families": ["ny_dps_order_entry", "company_website"],
                    "first_search_query": '"Harlem Property Management" "141 West 123 Street"',
                }
            ],
        },
        "manager_new_relationship_candidate_summary": {
            "candidate_count": 1,
            "counts_as_current_ledger_overlap": False,
            "approval_required_for_relationship_creation": True,
            "source_family_counts": {"ny_dps_order_entry": 1},
            "candidates": [
                {
                    "candidate_id": "ny-dps-verizon-402-w-153-petition",
                    "source_family": "ny_dps_order_entry",
                    "bbl": "1020670047",
                    "safe_action": "Review as a possible new relationship claim.",
                    "current_relationship_state": {
                        "current_building_management_relationship_count": 0,
                        "current_truth_claim_count": 0,
                        "counts_as_current_ledger_overlap": False,
                        "relationship_review_required": True,
                    },
                }
            ],
        },
        "operator_strict_packet": {
            "batch_filter": "strict_operator_manager_proof",
            "approval_required": True,
            "approval_required_before_recording": True,
            "allowed_execute": False,
            "template_count": 3,
            "claim_group_count": 1,
            "manager_proof_source_families": ["operator_confirmed", "real_estate_listing"],
            "planned_upsert_count_if_approved": 9,
            "recommended_execute_command": (
                "python scripts/truth_operator_confirmed_evidence_batch.py "
                "--strict-manager-proof-only --execute --confirm-execute --indent 2"
            ),
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
            "excluded_non_strict_candidate_count": 3,
            "excluded_non_strict_candidates": [
                {
                    "address": "220 3 AVENUE",
                    "strict_manager_gap_status": "broad_source_ready_not_strict",
                    "missing_manager_proof_source_family_count": 1,
                }
            ],
        },
        "operator_strict_gap_summary": {
            "candidate_count": 4,
            "strict_ready_candidate_count": 1,
            "broad_source_ready_not_strict_count": 3,
            "gap_candidates": [
                {
                    "address": "220 3 AVENUE",
                    "strict_manager_gap_status": "broad_source_ready_not_strict",
                    "missing_manager_proof_source_family_count": 1,
                }
            ],
        },
        "approval_required": True,
        "approval_policy": {
            "single_source_claims_stay_unverified": True,
        },
        "post_execution_required_checks": [
            "python scripts/truth_adjudication_preview.py --limit 20 --no-samples --indent 2",
        ],
        "blocked_business_use_reason": "Actual ledger source overlap is still zero.",
    }

    validate_source_overlap_approval_packet(payload)
    summary = summarize_payload(payload)

    assert summary["current_ledger"]["multi_source_fact_group_count"] == 0
    assert summary["source_overlap_recording_gate"]["status"] == "approval_required"
    assert summary["previewed_overlap_if_approved"]["manager_strict_source_ready_if_recorded_count"] == 4
    assert summary["recommended_first_packet"]["manager_proof_source_families"] == [
        "external_web_profile",
        "ny_dps_order_entry",
        "real_estate_listing",
    ]
    assert summary["recommended_first_packet"]["approval_decision_summary"]["would_plan_upsert_count"] == 45
    assert summary["recommended_first_packet"]["approval_decision_summary"]["will_mark_verified"] is False
    assert summary["manager_strict_gap_summary"]["broad_source_ready_not_strict_count"] == 9
    assert summary["manager_strict_gap_summary"]["strict_ready_claim_group_count"] == 4
    assert summary["operator_strict_packet"]["excluded_non_strict_candidate_count"] == 3
    assert summary["operator_strict_packet"]["approval_decision_summary"]["would_plan_upsert_count"] == 9
    assert summary["operator_strict_packet"]["approval_decision_summary"]["will_materialize_new_relationships"] is False
    assert summary["operator_strict_gap_summary"]["broad_source_ready_not_strict_count"] == 3


def test_truth_api_workflow_smoke_rejects_source_overlap_packet_without_confirm_gate():
    payload = {
        "dry_run": True,
        "mutations_planned": 0,
        "current_ledger": {
            "total_fact_group_count": 1,
            "single_source_fact_group_count": 1,
            "multi_source_fact_group_count": 0,
            "source_ready_fact_group_count": 0,
            "verification_candidate_count": 0,
        },
        "previewed_overlap_if_approved": {
            "manager_source_ready_if_recorded_count": 1,
            "manager_strict_source_ready_if_recorded_count": 1,
            "operator_source_ready_if_recorded_count": 0,
            "operator_strict_source_ready_if_recorded_count": 0,
            "safe_to_mark_verified_after_recording": 0,
        },
        "source_overlap_recording_gate": {
            "status": "approval_required",
            "current_multi_source_fact_group_count": 0,
            "current_source_ready_fact_group_count": 0,
            "current_verification_candidate_count": 0,
            "source_overlap_proof_satisfied": False,
            "additional_evidence_recording_requires_approval": True,
        },
        "recommended_first_packet": {
            "approval_required": True,
            "approval_required_before_recording": True,
            "allowed_execute": False,
            "template_count": 1,
            "planned_upsert_count_if_approved": 3,
            "manager_proof_source_families": ["external_web_profile", "ny_dps_order_entry"],
            "recommended_execute_command": "python scripts/truth_manager_external_evidence_batch.py --execute",
        },
        "approval_required": True,
        "approval_policy": {"single_source_claims_stay_unverified": True},
        "post_execution_required_checks": ["python scripts/truth_adjudication_preview.py"],
        "blocked_business_use_reason": "blocked",
    }

    try:
        validate_source_overlap_approval_packet(payload)
    except AssertionError as exc:
        assert "confirm_execute" in str(exc)
    else:
        raise AssertionError("source-overlap packet without confirm gate should fail smoke validation")


def test_truth_api_workflow_smoke_validates_source_overlap_blocker_report_preview():
    payload = {
        "run_type": "truth_source_overlap_blocker_report",
        "dry_run": True,
        "mutations_planned": 0,
        "status": "blocked_evidence_acquisition_required",
        "source_bridge_assessment": {
            "can_record_evidence_now": False,
            "has_preview_ready_candidate_batch": True,
            "can_mark_verified_now": False,
            "blocking_reasons": ["verification_candidate_count=0", "recording_ready_count=0"],
        },
        "source_evidence_candidate_summary": {
            "status": "preview_ready_approval_required",
            "checked": True,
            "source_mode": "operator_confirmed_candidate_recommended_scope_only",
            "candidate_count": 2,
            "recording_ready_count": 2,
            "recommended_count": 2,
            "allowed_execute": False,
            "recording_approval_packet": {
                "status": "preview_ready_approval_required",
                "approval_required": True,
                "allowed_execute": False,
                "approval_scope": "new_supporting_sources_only",
                "recommended_count": 2,
                "manual_evidence_payload_count": 2,
                "manual_evidence_payload_review": [
                    {
                        "payload_index": 1,
                        "manager_name": "MD Squared Property Group",
                        "object_id": "1008747504",
                        "source_name": "outreach_confirmed",
                    },
                    {
                        "payload_index": 2,
                        "manager_name": "MD Squared Property Group",
                        "object_id": "1005297507",
                        "source_name": "outreach_confirmed",
                    },
                ],
                "expected_post_recording_source_overlap": {
                    "candidate_count": 2,
                    "first_source_only_after_recording_count": 2,
                    "multi_source_after_recording_count": 0,
                    "source_ready_after_recording_count": 0,
                    "safe_action": "Approved recording would create first-source support only; acquire another exact source.",
                },
                "execute_command_after_approval": (
                    "truth_manual_evidence.py --payload-file <reviewed-preview.json> "
                    "--execute --confirm-execute --confirm-batch-execute"
                ),
            },
        },
        "top_blocked_relationships": [],
        "safe_action": "Use this report to explain the current blocker. It is not evidence.",
    }

    validate_source_overlap_blocker_report(payload, candidate_preview_checked=True)
    summary = summarize_payload(payload)

    assert summary["source_evidence_candidate_summary"] == {
        "status": "preview_ready_approval_required",
        "checked": True,
        "source_mode": "operator_confirmed_candidate_recommended_scope_only",
        "candidate_count": 2,
        "recording_ready_count": 2,
        "recommended_count": 2,
        "allowed_execute": False,
        "recording_approval_packet": {
            "status": "preview_ready_approval_required",
            "approval_required": True,
            "allowed_execute": False,
            "recommended_count": 2,
            "manual_evidence_payload_count": 2,
            "approval_scope": "new_supporting_sources_only",
            "expected_post_recording_source_overlap": {
                "candidate_count": 2,
                "first_source_only_after_recording_count": 2,
                "multi_source_after_recording_count": 0,
                "source_ready_after_recording_count": 0,
                "safe_action": "Approved recording would create first-source support only; acquire another exact source.",
            },
        },
    }


def test_truth_api_workflow_smoke_rejects_blocker_preview_that_allows_execute():
    payload = {
        "run_type": "truth_source_overlap_blocker_report",
        "dry_run": True,
        "mutations_planned": 0,
        "status": "blocked_evidence_acquisition_required",
        "source_bridge_assessment": {
            "can_record_evidence_now": False,
            "can_mark_verified_now": False,
            "blocking_reasons": ["verification_candidate_count=0"],
        },
        "source_evidence_candidate_summary": {
            "status": "preview_ready_approval_required",
            "checked": True,
            "recording_ready_count": 1,
            "recommended_count": 1,
            "allowed_execute": True,
        },
        "safe_action": "Use this report to explain the current blocker. It is not evidence.",
    }

    try:
        validate_source_overlap_blocker_report(payload, candidate_preview_checked=True)
    except AssertionError as exc:
        assert "allow execute" in str(exc)
    else:
        raise AssertionError("blocker report candidate preview that allows execute should fail")


def test_truth_api_workflow_smoke_accepts_clue_only_blocker_preview():
    payload = {
        "run_type": "truth_source_overlap_blocker_report",
        "dry_run": True,
        "mutations_planned": 0,
        "status": "blocked_evidence_acquisition_required",
        "source_bridge_assessment": {
            "can_record_evidence_now": False,
            "has_preview_ready_candidate_batch": False,
            "has_source_acquisition_clues": True,
            "source_acquisition_clue_count": 1,
            "can_mark_verified_now": False,
            "blocking_reasons": [
                "verification_candidate_count=0",
                "source_clue_only_primary_source_required",
            ],
        },
        "source_evidence_candidate_summary": {
            "status": "source_clue_only_primary_source_required",
            "checked": True,
            "source_mode": "candidate_file",
            "candidate_count": 0,
            "source_acquisition_clue_count": 1,
            "recording_ready_count": 0,
            "recommended_count": 0,
            "allowed_execute": False,
            "can_record_evidence_now": False,
            "safe_action": "Source-acquisition clues require primary-source review before evidence recording.",
        },
        "top_blocked_relationships": [],
        "safe_action": "Use this report to explain the current blocker. It is not evidence.",
    }

    validate_source_overlap_blocker_report(payload, candidate_preview_checked=True)
    summary = summarize_payload(payload)

    assert summary["source_evidence_candidate_summary"] == {
        "status": "source_clue_only_primary_source_required",
        "checked": True,
        "source_mode": "candidate_file",
        "candidate_count": 0,
        "source_acquisition_clue_count": 1,
        "recording_ready_count": 0,
        "recommended_count": 0,
        "allowed_execute": False,
        "can_record_evidence_now": False,
    }


def test_truth_api_workflow_smoke_validates_post_recording_check_blocked_payload():
    payload = {
        "run_type": "truth_source_overlap_post_recording_check",
        "dry_run": True,
        "mutations_planned": 0,
        "post_recording_success": False,
        "thresholds": {
            "min_multi_source_fact_groups": 1,
            "min_source_ready_fact_groups": 1,
            "max_verified_single_source_claims": 0,
        },
        "current_ledger": {
            "total_fact_group_count": 2063,
            "single_source_fact_group_count": 2063,
            "multi_source_fact_group_count": 0,
            "source_ready_fact_group_count": 0,
            "max_supporting_source_count": 1,
            "max_supporting_evidence_count": 1,
        },
        "verified_single_source_policy": {
            "verified_claim_count": 0,
            "verified_single_source_claim_count": 0,
            "sample_limit": 10,
            "samples": [],
        },
        "checks": [
            {
                "check": "actual_current_ledger_multi_source",
                "status": "fail",
                "observed": 0,
                "threshold": 1,
                "reason": "No current fact group has more than one supporting source.",
            },
            {
                "check": "actual_current_ledger_source_ready",
                "status": "fail",
                "observed": 0,
                "threshold": 1,
                "reason": "No current fact group is source-ready.",
            },
            {
                "check": "no_single_source_verified_claims",
                "status": "pass",
                "observed": 0,
                "threshold": 0,
                "reason": "No current verified claims rely on one source.",
            },
        ],
        "safe_action": "Do not activate business use until recorded evidence creates actual ledger overlap.",
    }

    validate_source_overlap_post_recording_check(payload)
    summary = summarize_payload(payload)

    assert summary["dry_run"] is True
    assert summary["current_ledger"]["multi_source_fact_group_count"] == 0


def test_truth_api_workflow_smoke_rejects_post_recording_check_with_verified_single_source():
    payload = {
        "dry_run": True,
        "mutations_planned": 0,
        "post_recording_success": False,
        "thresholds": {
            "min_multi_source_fact_groups": 1,
            "min_source_ready_fact_groups": 1,
            "max_verified_single_source_claims": 0,
        },
        "current_ledger": {
            "multi_source_fact_group_count": 0,
            "source_ready_fact_group_count": 0,
        },
        "verified_single_source_policy": {
            "verified_claim_count": 1,
            "verified_single_source_claim_count": 1,
            "sample_limit": 10,
            "samples": [{"claim_id": "unsafe"}],
        },
        "checks": [
            {
                "check": "no_single_source_verified_claims",
                "status": "fail",
                "observed": 1,
                "threshold": 0,
                "reason": "One verified claim has one source.",
            }
        ],
        "safe_action": "Block business use.",
    }

    try:
        validate_source_overlap_post_recording_check(payload)
    except AssertionError as exc:
        assert "verified single-source" in str(exc)
    else:
        raise AssertionError("post-recording check with verified single-source claims should fail")


def test_truth_api_workflow_smoke_validates_manager_source_acquisition_packet():
    payload = {
        "run_type": "manager_source_acquisition_packet",
        "dry_run": True,
        "mutations_planned": 0,
        "candidate_count": 9,
        "source_ready_if_recorded_count": 13,
        "independent_source_ready_if_recorded_count": 13,
        "strict_manager_source_ready_if_recorded_count": 4,
        "verified_safe_if_recorded_count": 0,
        "next_source_seed_count": 9,
        "new_relationship_candidate_count": 1,
        "new_relationship_candidates": [
            {
                "candidate_id": "nyc-dof-275-greenwich-hpm-billing-record",
                "local_building_match": {"bbl": "1001327501", "address": "269 GREENWICH STREET"},
                "current_relationship_state": {
                    "current_building_management_relationship_count": 0,
                    "current_truth_claim_count": 0,
                    "counts_as_current_ledger_overlap": False,
                    "relationship_review_required": True,
                },
            }
        ],
        "new_relationship_policy": (
            "Source-backed new relationships are source-acquisition leads only. They are not counted as "
            "current-ledger source overlap and must go through relationship review before any evidence is recorded."
        ),
        "proposals": [
            {
                "bbl": "1019080014",
                "address": "141 WEST 123 STREET",
                "first_search_query": '"Harlem Property Management" "141 West 123 Street"',
                "source_targets": [
                    {
                        "source_family": "ny_dps_order_entry",
                        "evidence_needed": "Find exact NY DPS manager evidence.",
                    }
                ],
            }
        ],
        "reviewed_source_findings": [
            {
                "source_family": "public_web_search_followup_hpm_batch_7_2026_05_15",
                "finding": "No new strict bridge.",
                "qualification": "Source-ready counts did not change.",
            }
        ],
        "source_boundary_notes": ["RentHistory is review-only."],
        "safe_action": "Read-only source-acquisition packet. Record nothing from this packet.",
    }

    validate_manager_source_acquisition_packet(payload)


def test_truth_api_workflow_smoke_report_is_read_only():
    args = Namespace(base_url="http://127.0.0.1:8000")

    report = render_report(
        args,
        [Check(name="truth_activation_packet", status="ok", detail="ok", path="/api/v1/truth/activation-packet")],
        [],
        lead_id="lead-1",
        bbl="1000000001",
    )

    assert report["dry_run"] is True
    assert report["mutations_planned"] == 0
    assert report["status"] == "passed"
    assert report["sample"] == {"lead_id": "lead-1", "bbl": "1000000001"}


def test_truth_api_workflow_smoke_extracts_recent_job_ids_defensively():
    assert job_ids([
        {"id": 12},
        {"id": "11"},
        {"id": None},
        {"id": "bad"},
        {"not_id": 10},
    ]) == [12, 11]
    assert job_ids({"id": 1}) == []


def test_truth_api_workflow_smoke_summarizes_preview_and_source_audit_payloads():
    payload = {
        "dry_run": True,
        "mutations_planned": 0,
        "status": "schema_not_ready",
        "job_type": "truth_materialization",
        "job_id": None,
        "safe_to_run_automatically": False,
        "summary": {
            "total_sources": 18,
            "operational": 4,
            "schema_missing": 2,
            "no_recent_ingest": 8,
            "stale_ingest": 3,
            "ignored": "not copied",
        },
        "blocked_reason": "Truth schema is not ready.",
        "allowed_execute": False,
        "preview": {
            "operation": "job_execution",
            "would_enqueue_job_type": "truth_materialization",
            "required_execute_query": "/api/v1/jobs/truth_materialization/start?dry_run=false&confirm_execute=true",
            "would_mutate": ["not copied"],
        },
        "sample_materialized_claim_specs": [
            {
                "claim_id": "claim-1",
                "evidence_id": "evidence-1",
            }
        ],
        "sources": [{"source_name": "acris"}],
    }

    summary = summarize_payload(payload)

    assert summary == {
        "dry_run": True,
        "mutations_planned": 0,
        "status": "schema_not_ready",
        "job_type": "truth_materialization",
        "job_id": None,
        "safe_to_run_automatically": False,
        "summary": {
            "total_sources": 18,
            "operational": 4,
            "schema_missing": 2,
            "no_recent_ingest": 8,
            "stale_ingest": 3,
        },
        "blocked_reason": "Truth schema is not ready.",
        "sample_materialized_claim_spec_count": 1,
        "allowed_execute": False,
        "preview": {
            "operation": "job_execution",
            "would_enqueue_job_type": "truth_materialization",
            "required_execute_query": "/api/v1/jobs/truth_materialization/start?dry_run=false&confirm_execute=true",
        },
        "source_count": 1,
    }


def test_truth_api_workflow_smoke_validates_materialization_preview_specs():
    validate_materialization_preview({
        "dry_run": True,
        "mutations_planned": 0,
        "planned_claims_total": 1,
        "sample_materialized_claim_specs": [{
            "claim_id": "claim-1",
            "evidence_id": "evidence-1",
            "subject_type": "lead",
            "subject_id": "lead-1",
            "predicate": "manages_building",
            "object_type": "building",
            "object_id": "1000000001",
            "claim_type": "building_management",
            "source_name": "building_management",
            "source_record_id": "building_management:1",
            "support_status": "supports",
            "confidence_score": 0.7,
            "freshness_days": 10,
            "actionability_level": "automated_enrichment",
        }],
    })


def test_truth_api_workflow_smoke_validates_manual_evidence_preview_contract():
    validate_manual_evidence_preview({
        "run_type": "manual_evidence_capture",
        "dry_run": True,
        "mutations_planned": 3,
        "allowed_execute": False,
        "claim_spec": {
            "claim_id": "claim-1",
            "evidence_id": "evidence-1",
            "subject_type": "lead",
            "subject_id": "lead-1",
            "predicate": "manages_building",
            "object_type": "building",
            "object_id": "1000000001",
            "claim_type": "building_management",
            "source_name": "manual_evidence",
            "support_status": "supports",
            "confidence_score": 0.7,
            "actionability_level": "ranked_sourcing",
        },
        "rollback_plan": {"rollback_strategy": "delete new rows by id"},
        "required_execute_params": {"dry_run": False, "confirm_execute": True},
    })


def test_truth_api_workflow_smoke_rejects_materialization_preview_without_specs():
    try:
        validate_materialization_preview({
            "dry_run": True,
            "mutations_planned": 0,
            "planned_claims_total": 1,
            "sample_materialized_claim_specs": [],
        })
    except AssertionError as exc:
        assert "sample claim/evidence specs" in str(exc)
    else:
        raise AssertionError("materialization preview without sample specs should fail")
