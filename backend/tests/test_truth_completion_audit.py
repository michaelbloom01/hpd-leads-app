from scripts.truth_completion_audit import (
    ARTIFACT_REQUIREMENTS,
    build_artifact_checklist,
    build_artifact_only_audit,
    build_completion_audit,
)


def test_truth_completion_audit_requires_runtime_gates_even_when_artifacts_exist(tmp_path):
    for requirement in ARTIFACT_REQUIREMENTS:
        content = "\n".join([
            requirement.requirement,
            requirement.source_item,
            *requirement.required_terms,
        ])
        for name in requirement.files:
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            prior = path.read_text(encoding="utf-8") if path.exists() else ""
            path.write_text(f"{prior}\n{content}", encoding="utf-8")

    checklist = build_artifact_checklist(tmp_path)
    audit = build_completion_audit(
        artifact_checklist=checklist,
        health_report={
            "summary": {"trust_posture": "not_ready"},
            "trust_gaps": [{
                "severity": "critical",
                "area": "schema_readiness",
                "message": "Truth schema is missing.",
                "evidence": {"missing_tables": ["truth_claims"]},
            }],
            "adjudication_preview": {
                "ledger_source_overlap": {
                    "single_source_fact_group_count": 2063,
                    "multi_source_fact_group_count": 0,
                    "source_ready_fact_group_count": 0,
                },
                "manager_external_source_acquisition_preview": {
                    "lead_id": "0ff794d3ba2d",
                    "candidate_source_count": 30,
                    "matched_evidence_candidate_count": 49,
                    "new_relationship_candidate_count": 3,
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
                    "next_source_batches": {
                        "candidate_count": 1,
                        "source_boundary_notes": [
                            "RentHistory and HPD-registration-derived context can support review.",
                            "NY DOS service-of-process records are legal/mailing evidence unless they state a managing-agent role.",
                        ],
                        "reviewed_source_findings": [
                            {
                                "source_family": "company_website",
                                "source_urls": ["https://harlempm.com/"],
                                "finding": "HPM's website proves company-role context but not exact pilot buildings.",
                                "qualification": "It cannot support a specific building relationship unless an exact property page is found.",
                            }
                        ],
                        "proposals": [{
                            "bbl": "1019080014",
                            "address": "141 WEST 123 STREET",
                            "existing_manager_proof_source_families": ["external_web_profile"],
                            "suggested_source_families": ["ny_dps_order_entry", "company_website"],
                            "search_queries": ['"Harlem Property Management" "141 West 123 Street"'],
                            "source_targets": [{
                                "source_family": "ny_dps_order_entry",
                                "evidence_needed": "Find exact manager proof for 141 WEST 123 STREET.",
                            }],
                        }],
                    },
                    "claim_group_count": 14,
                    "clean_exact_claim_count": 7,
                    "source_ready_if_recorded_count": 14,
                    "independent_source_ready_if_recorded_count": 14,
                    "strict_manager_source_ready_if_recorded_count": 13,
                    "manual_evidence_batch_preview": {
                        "template_count": 48,
                        "planned_upsert_count": 144,
                        "recommended_strict_manager_proof_batch": {
                            "template_count": 46,
                            "claim_group_count": 13,
                            "planned_upsert_count": 138,
                        },
                        "excluded_address_review_candidate_count": 1,
                    },
                    "post_recording_simulation": {
                        "source_ready_fact_group_count": 14,
                        "strict_manager_source_ready_fact_group_count": 13,
                        "safe_to_mark_verified_count": 0,
                        "blocker_counts": {"confidence_below_verified_threshold": 14},
                    },
                },
                "operator_confirmed_management_preview": {
                    "candidate_count": 4,
                    "matched_candidate_count": 4,
                    "new_relationship_candidate_count": 4,
                    "conflict_candidate_count": 0,
                    "operator_confirmation_template_count": 4,
                    "second_source_template_count": 6,
                    "manual_evidence_template_count": 10,
                    "planned_upsert_count": 30,
                    "source_ready_if_recorded_count": 4,
                    "independent_source_ready_if_recorded_count": 4,
                    "strict_manager_source_ready_if_recorded_count": 2,
                    "verified_safe_if_recorded_count": 0,
                    "second_source_seed_batches": {
                        "candidate_count": 4,
                        "template_count": 4,
                        "source_ready_if_recorded_count": 4,
                        "strict_manager_source_ready_if_recorded_count": 2,
                        "source_boundary_notes": [
                            "Operator-confirmed evidence is a first-hand source family.",
                            "RentHistory/HPD-registration-derived pages are excluded from strict manager-proof counts.",
                        ],
                        "reviewed_source_findings": [
                            {
                                "source_family": "company_website",
                                "source_urls": ["https://www.mdsquaredpropertygroup.com/property-management-services/"],
                                "finding": "MD Squared site proves company-role context.",
                                "qualification": "No exact managed-property page was found for the seed buildings.",
                            },
                            {
                                "source_family": "external_web_profile",
                                "source_urls": [
                                    "https://www.openigloo.com/contact/nyc/c072472d-b88d-4f79-aaf2-c5f8df6327eb/md-squared-property-group",
                                ],
                                "finding": "OpenIgloo profile exists but lists other MD Squared-associated properties.",
                                "qualification": "It cannot be recorded as support for the exact seed buildings.",
                            },
                            {
                                "source_family": "ny_dos_or_legal_mailing",
                                "source_urls": ["https://www.bizprofile.net/ny/new-york/4-w-16-street"],
                                "finding": "A NY DOS mirror lists MD Squared as a service-of-process mailing recipient.",
                                "qualification": "This is legal-mailing context, not a property-management statement.",
                            },
                            {
                                "source_family": "stale_public_utility_notice",
                                "source_urls": ["https://device.report/m/c7d7baaaf59d4e3d7ba88af56226a7a3af8df7e354ca2750dda829a139ab1396"],
                                "finding": "A 2015 public utility notice names a different care-of party for 57 Bond.",
                                "qualification": "This is stale care-of context, not current MD Squared management proof.",
                            },
                            {
                                "source_family": "real_estate_listing",
                                "source_urls": ["https://www.homes.com/property/9-prospect-park-w-brooklyn-ny-unit-1a/s54jg6l4hhxzj/"],
                                "finding": "Homes.com names Daisy for 9 Prospect Park W.",
                                "qualification": "This is the current strict manager-proof second source.",
                            },
                        ],
                        "proposals": [
                            {
                                "bbl": "1008747504",
                                "address": "220 3 AVENUE",
                                "manager_name": "MD Squared Property Group",
                                "existing_manager_proof_source_families": ["operator_confirmed"],
                                "supporting_source_families_if_recorded": [
                                    "operator_confirmed",
                                    "hpd_registration_derived",
                                ],
                                "suggested_source_families": ["company_website", "external_web_profile"],
                                "search_queries": ['"MD Squared" "220 3 Avenue"'],
                                "source_targets": [{
                                    "source_family": "company_website",
                                    "evidence_needed": "Find an exact MD Squared page for 220 3 AVENUE.",
                                }],
                                "strict_manager_source_ready_if_recorded": False,
                                "strict_manager_gap_status": "broad_source_ready_not_strict",
                                "missing_manager_proof_source_family_count": 1,
                                "next_required_manager_proof": (
                                    "Acquire one exact non-HPD manager-proof source family."
                                ),
                            },
                            {
                                "bbl": "3010680037",
                                "address": "9 PROSPECT PARK WEST",
                                "manager_name": "Daisy Management",
                                "existing_manager_proof_source_families": [
                                    "operator_confirmed",
                                    "real_estate_listing",
                                ],
                                "supporting_source_families_if_recorded": [
                                    "operator_confirmed",
                                    "real_estate_listing",
                                ],
                                "suggested_source_families": ["real_estate_listing"],
                                "search_queries": ['"Daisy" "9 Prospect Park West"'],
                                "source_targets": [{
                                    "source_family": "real_estate_listing",
                                    "evidence_needed": "Inspect exact Homes.com listing for 9 PROSPECT PARK WEST.",
                                }],
                                "strict_manager_source_ready_if_recorded": True,
                                "strict_manager_gap_status": "strict_manager_proof_ready_if_recorded",
                                "missing_manager_proof_source_family_count": 0,
                                "next_required_manager_proof": "Review templates, record only after explicit approval.",
                            }
                        ],
                    },
                    "policy": {
                        "single_source_policy": (
                            "Operator-confirmed evidence is high-quality first-hand evidence, "
                            "but a single source is not verified."
                        ),
                    },
                },
            },
            "activation_checklist": [
                {
                    "step": "apply_truth_schema",
                    "status": "approval_required",
                    "reason": "Apply additive migration.",
                    "approval_required": True,
                    "mutations_planned": 6,
                },
                {
                    "step": "allow_business_use",
                    "status": "blocked",
                    "reason": "Verified claims are missing.",
                    "approval_required": False,
                    "mutations_planned": 0,
                },
            ],
        },
        activation_packet={
            "verdict": "schema_approval_required",
            "business_use_allowed": False,
            "verification_frontier": {
                "verification_candidate_count": 0,
                "source_ready_below_verified_count": 10,
                "single_source_gap_count": 5,
                "evidence_acquisition_required": True,
            },
        },
        production_probe={
            "truth_surface_status": "not_deployed",
            "production_data_health_ready": False,
            "production_business_use_allowed": False,
            "data_health_thresholds": {"max_zero_active_links": 0},
            "trust_gaps": [
                {"severity": "critical", "area": "truth_surface", "message": "Truth routes missing."},
                {"severity": "critical", "area": "activation_claim_readiness", "message": "Verified claims are missing."},
                {"severity": "high", "area": "freshness", "message": "Leads are stale."},
            ],
            "activation_gaps": [{
                "severity": "critical",
                "area": "activation_claim_readiness",
                "message": "Verified claims are missing.",
            }],
            "data_health_gaps": [{"severity": "high", "area": "freshness", "message": "Leads are stale."}],
        },
    )

    assert audit["artifact_summary"] == {
        "total": len(ARTIFACT_REQUIREMENTS),
        "satisfied": len(ARTIFACT_REQUIREMENTS),
        "missing": 0,
    }
    assert {
        item["source_item"]
        for item in audit["artifact_checklist"]
        if item["source_item"].startswith("Numbered workstream")
    } == {f"Numbered workstream {number}" for number in range(1, 13)}
    product_standard = next(
        item for item in audit["artifact_checklist"]
        if item["source_item"].startswith("Primary product standard")
    )
    assert product_standard["required_terms"] == [
        "what_we_believe",
        "why_we_believe",
        "supporting_sources",
        "contradicting_sources",
        "freshness_days",
        "overall_confidence_score",
        "safe_actions",
    ]
    source_overlap_requirement = next(
        item for item in audit["artifact_checklist"]
        if item["requirement"] == "Narrow manager source-overlap pilot is previewed, simulated, and approval-gated"
    )
    assert "manager_new_relationship_candidate_summary" in source_overlap_requirement["required_terms"]
    assert "counts_as_current_ledger_overlap" in source_overlap_requirement["required_terms"]
    assert "approval_required_for_relationship_creation" in source_overlap_requirement["required_terms"]
    assert "verification-frontier" in source_overlap_requirement["required_terms"]
    assert "source_ready_below_verified" in source_overlap_requirement["required_terms"]
    assert "source_acquisition_frontier" in source_overlap_requirement["required_terms"]
    assert "current_relationship_state" in source_overlap_requirement["required_terms"]
    assert "current_ledger_source_ready" in source_overlap_requirement["required_terms"]
    assert "required_real_evidence" in source_overlap_requirement["required_terms"]
    assert "official_hpd_query_packet_only" in source_overlap_requirement["required_terms"]
    assert "official_query_urls" in source_overlap_requirement["required_terms"]
    assert "read_only_preview_command" in source_overlap_requirement["required_terms"]
    assert "source_dataset_ids" in source_overlap_requirement["required_terms"]
    assert "evidence_request_packet" in source_overlap_requirement["required_terms"]
    assert "source_ready_requests" in source_overlap_requirement["required_terms"]
    assert "source_acquisition_requests" in source_overlap_requirement["required_terms"]
    assert "reviewed_source_finding_count" in source_overlap_requirement["required_terms"]
    assert "reviewed_source_history_status" in source_overlap_requirement["required_terms"]
    assert "evidence_acquisition_status" in source_overlap_requirement["required_terms"]
    assert "verification_readiness_gate" in source_overlap_requirement["required_terms"]
    assert "expected_post_recording_source_overlap" in source_overlap_requirement["required_terms"]
    assert "first_source_only_after_recording_count" in source_overlap_requirement["required_terms"]
    assert "multi_source_after_recording_count" in source_overlap_requirement["required_terms"]
    assert "source_ready_after_recording_count" in source_overlap_requirement["required_terms"]
    assert "approval_required_before_recording" in source_overlap_requirement["required_terms"]
    assert "allowed_execute" in source_overlap_requirement["required_terms"]
    assert "record_ready_count" in source_overlap_requirement["required_terms"]
    assert "acquisition_required_count" in source_overlap_requirement["required_terms"]
    assert audit["completion_status"] == "not_complete"
    prompt_checklist = {item["requirement"]: item for item in audit["prompt_to_artifact_checklist"]}
    assert len(audit["success_criteria"]) == len(prompt_checklist)
    assert prompt_checklist[
        "A narrow Harlem manager source-overlap pilot proves real independent overlap without broad dedupe."
    ]["status"] == "blocked"
    assert prompt_checklist[
        "A narrow Harlem manager source-overlap pilot proves real independent overlap without broad dedupe."
    ]["evidence"]["strict_manager_source_ready_if_recorded_count"] == 13
    assert "truth_verification_frontier" in prompt_checklist[
        "Read-only previews explain exact claims, sources, contradictions, freshness, confidence, safe action, and next source acquisition."
    ]["evidence"]["requires_terms"]
    assert "required_real_evidence" in prompt_checklist[
        "Read-only previews explain exact claims, sources, contradictions, freshness, confidence, safe action, and next source acquisition."
    ]["evidence"]["requires_terms"]
    assert "official_hpd_query_packet_only" in prompt_checklist[
        "Read-only previews explain exact claims, sources, contradictions, freshness, confidence, safe action, and next source acquisition."
    ]["evidence"]["requires_terms"]
    assert "read_only_preview_command" in prompt_checklist[
        "Read-only previews explain exact claims, sources, contradictions, freshness, confidence, safe action, and next source acquisition."
    ]["evidence"]["requires_terms"]
    assert "evidence_request_packet" in prompt_checklist[
        "Read-only previews explain exact claims, sources, contradictions, freshness, confidence, safe action, and next source acquisition."
    ]["evidence"]["requires_terms"]
    assert "reviewed_source_history_status" in prompt_checklist[
        "Read-only previews explain exact claims, sources, contradictions, freshness, confidence, safe action, and next source acquisition."
    ]["evidence"]["requires_terms"]
    assert "verification_readiness_gate" in prompt_checklist[
        "Read-only previews explain exact claims, sources, contradictions, freshness, confidence, safe action, and next source acquisition."
    ]["evidence"]["requires_terms"]
    assert prompt_checklist["No single-source claim is marked verified."]["status"] == "satisfied"
    assert prompt_checklist[
        "Production truth surface remains blocked until production data-health gates pass."
    ]["status"] == "blocked"
    assert {blocker["gate"] for blocker in audit["runtime_blockers"]} == {
        "local_truth_health",
        "source_overlap_recording",
        "activation_packet",
        "production_truth_surface",
    }
    local_blocker = next(blocker for blocker in audit["runtime_blockers"] if blocker["gate"] == "local_truth_health")
    assert "trust_posture_not_ready" in local_blocker["evidence"]["health_failures"]
    assert "insufficient_materialized_claims" in local_blocker["evidence"]["health_failures"]
    assert "no_verified_claims" in local_blocker["evidence"]["health_failures"]
    assert local_blocker["evidence"]["top_trust_gaps"] == [{
        "severity": "critical",
        "area": "schema_readiness",
        "message": "Truth schema is missing.",
    }]
    activation_blocker = next(blocker for blocker in audit["runtime_blockers"] if blocker["gate"] == "activation_packet")
    assert activation_blocker["evidence"]["blocked_activation_step_count"] == 2
    assert activation_blocker["evidence"]["top_blocked_activation_steps"] == [
        {
            "step": "apply_truth_schema",
            "status": "approval_required",
            "reason": "Apply additive migration.",
            "approval_required": True,
            "mutations_planned": 6,
        },
        {
            "step": "allow_business_use",
            "status": "blocked",
            "reason": "Verified claims are missing.",
            "approval_required": False,
            "mutations_planned": 0,
        },
    ]
    source_overlap_blocker = next(
        blocker for blocker in audit["runtime_blockers"] if blocker["gate"] == "source_overlap_recording"
    )
    assert activation_blocker["evidence"]["verification_frontier"] == {
        "verification_candidate_count": 0,
        "source_ready_below_verified_count": 10,
        "single_source_gap_count": 5,
        "evidence_acquisition_required": True,
    }
    assert audit["activation_summary"]["verification_frontier"]["source_ready_below_verified_count"] == 10
    assert source_overlap_blocker["evidence"]["approval_required"] is True
    assert source_overlap_blocker["evidence"]["candidate_source_count"] == 30
    assert source_overlap_blocker["evidence"]["matched_evidence_candidate_count"] == 49
    assert source_overlap_blocker["evidence"]["new_relationship_candidate_count"] == 3
    assert source_overlap_blocker["evidence"]["new_relationship_counts_as_current_ledger_overlap"] is False
    assert (
        source_overlap_blocker["evidence"]["new_relationship_approval_required_for_relationship_creation"]
        is True
    )
    assert source_overlap_blocker["evidence"]["new_relationship_source_family_counts"] == {
        "nyc_dof_billing_record": 1,
    }
    assert source_overlap_blocker["evidence"]["new_relationship_candidates_sample"][0][
        "candidate_id"
    ] == "nyc-dof-275-greenwich-hpm-billing-record"
    assert source_overlap_blocker["evidence"]["new_relationship_candidates_sample"][0][
        "local_building_match"
    ]["bbl"] == "1001327501"
    assert "not counted as current-ledger source overlap" in source_overlap_blocker["evidence"][
        "new_relationship_policy"
    ]
    assert source_overlap_blocker["evidence"]["next_source_batch_count"] == 1
    assert "service-of-process" in source_overlap_blocker["evidence"]["next_source_boundary_notes"][1]
    assert source_overlap_blocker["evidence"]["next_source_reviewed_findings"][0]["source_family"] == "company_website"
    search_pack = source_overlap_blocker["evidence"]["next_source_search_pack_sample"]
    assert search_pack[0]["search_queries"] == ['"Harlem Property Management" "141 West 123 Street"']
    assert search_pack[0]["source_targets"][0]["source_family"] == "ny_dps_order_entry"
    assert source_overlap_blocker["evidence"]["claim_group_count"] == 14
    assert source_overlap_blocker["evidence"]["clean_exact_claim_count"] == 7
    assert source_overlap_blocker["evidence"]["strict_manager_source_ready_if_recorded_count"] == 13
    assert source_overlap_blocker["evidence"]["manual_evidence_template_count"] == 48
    assert source_overlap_blocker["evidence"]["planned_upsert_count"] == 144
    assert source_overlap_blocker["evidence"]["strict_manager_proof_template_count"] == 46
    assert source_overlap_blocker["evidence"]["strict_manager_proof_claim_group_count"] == 13
    assert source_overlap_blocker["evidence"]["strict_manager_proof_planned_upsert_count"] == 138
    assert source_overlap_blocker["evidence"]["excluded_address_review_candidate_count"] == 1
    assert source_overlap_blocker["evidence"]["simulated_strict_manager_source_ready_fact_group_count"] == 13
    assert source_overlap_blocker["evidence"]["current_ledger_source_ready_fact_group_count"] == 0
    assert source_overlap_blocker["evidence"]["operator_confirmed"]["matched_candidate_count"] == 4
    assert source_overlap_blocker["evidence"]["operator_confirmed"]["new_relationship_candidate_count"] == 4
    assert source_overlap_blocker["evidence"]["operator_confirmed"]["source_ready_if_recorded_count"] == 4
    assert source_overlap_blocker["evidence"]["operator_confirmed"]["strict_manager_source_ready_if_recorded_count"] == 2
    assert source_overlap_blocker["evidence"]["operator_confirmed"]["second_source_seed_count"] == 4
    assert "--source-acquisition-only" in (
        source_overlap_blocker["evidence"]["operator_confirmed"]["source_acquisition_command"]
    )
    assert "first-hand source family" in (
        source_overlap_blocker["evidence"]["operator_confirmed"]["second_source_boundary_notes"][0]
    )
    assert (
        source_overlap_blocker["evidence"]["operator_confirmed"]["second_source_reviewed_findings"][0][
            "source_family"
        ]
        == "company_website"
    )
    assert any(
        finding["source_family"] == "external_web_profile"
        and "cannot be recorded as support" in finding["qualification"]
        for finding in source_overlap_blocker["evidence"]["operator_confirmed"][
            "second_source_reviewed_findings"
        ]
    )
    assert any(
        finding["source_family"] == "ny_dos_or_legal_mailing"
        and "not a property-management statement" in finding["qualification"]
        for finding in source_overlap_blocker["evidence"]["operator_confirmed"][
            "second_source_reviewed_findings"
        ]
    )
    assert any(
        finding["source_family"] == "stale_public_utility_notice"
        and "not current MD Squared management proof" in finding["qualification"]
        for finding in source_overlap_blocker["evidence"]["operator_confirmed"][
            "second_source_reviewed_findings"
        ]
    )
    operator_search_pack = source_overlap_blocker["evidence"]["operator_confirmed"][
        "second_source_search_pack_sample"
    ]
    assert operator_search_pack[0]["search_queries"] == ['"MD Squared" "220 3 Avenue"']
    assert operator_search_pack[0]["strict_manager_source_ready_if_recorded"] is False
    assert operator_search_pack[0]["strict_manager_gap_status"] == "broad_source_ready_not_strict"
    assert operator_search_pack[0]["missing_manager_proof_source_family_count"] == 1
    assert "exact non-HPD manager-proof" in operator_search_pack[0]["next_required_manager_proof"]
    assert source_overlap_blocker["evidence"]["operator_confirmed"]["strict_gap_summary"] == {
        "proposal_count": 2,
        "strict_ready_proposal_count": 1,
        "broad_source_ready_not_strict_count": 1,
        "gap_candidates": [{
            "bbl": "1008747504",
            "address": "220 3 AVENUE",
            "manager_name": "MD Squared Property Group",
            "strict_manager_gap_status": "broad_source_ready_not_strict",
            "missing_manager_proof_source_family_count": 1,
            "next_required_manager_proof": "Acquire one exact non-HPD manager-proof source family.",
        }],
    }
    assert "--strict-manager-proof-only --execute --confirm-execute" in source_overlap_blocker["evidence"]["required_command"]
    assert source_overlap_blocker["evidence"]["all_source_ready_command"].endswith(
        "truth_manager_external_evidence_batch.py --execute --confirm-execute --indent 2"
    )
    production_blocker = next(blocker for blocker in audit["runtime_blockers"] if blocker["gate"] == "production_truth_surface")
    assert production_blocker["evidence"]["production_data_health_ready"] is False
    assert production_blocker["evidence"]["data_health_thresholds"] == {"max_zero_active_links": 0}
    assert production_blocker["evidence"]["trust_gap_count"] == 3
    assert production_blocker["evidence"]["activation_gap_count"] == 1
    assert production_blocker["evidence"]["data_health_gap_count"] == 1
    assert production_blocker["evidence"]["top_trust_gaps"][0] == {
        "severity": "critical",
        "area": "truth_surface",
        "message": "Truth routes missing.",
    }
    assert production_blocker["evidence"]["top_activation_gaps"] == [{
        "severity": "critical",
        "area": "activation_claim_readiness",
        "message": "Verified claims are missing.",
    }]
    assert production_blocker["evidence"]["top_data_health_gaps"] == [{
        "severity": "high",
        "area": "freshness",
        "message": "Leads are stale.",
    }]
    assert audit["production_summary"] == {
        "truth_surface_status": "not_deployed",
        "production_data_health_ready": False,
        "production_business_use_allowed": False,
        "trust_gap_count": 3,
        "activation_gap_count": 1,
        "data_health_gap_count": 1,
    }
    assert audit["activation_summary"]["blocked_activation_step_count"] == 2


def test_truth_completion_audit_can_mark_complete_only_when_artifacts_and_gates_clear():
    audit = build_completion_audit(
        artifact_checklist=[{"requirement": "x", "status": "satisfied"}],
        health_report={
            "thresholds": {"minimum_claim_count": 1},
            "summary": {
                "trust_posture": "monitor",
                "claim_count": 10,
                "verified_claim_count": 8,
                "critical_or_high_gap_count": 0,
            },
            "trust_gaps": [],
            "adjudication_preview": {
                "ledger_source_overlap": {
                    "total_fact_group_count": 10,
                    "single_source_fact_group_count": 4,
                    "multi_source_fact_group_count": 6,
                    "source_ready_fact_group_count": 6,
                },
                "manager_external_source_acquisition_preview": {
                    "source_ready_if_recorded_count": 6,
                    "strict_manager_source_ready_if_recorded_count": 4,
                    "post_recording_simulation": {
                        "source_ready_fact_group_count": 6,
                        "strict_manager_source_ready_fact_group_count": 4,
                    },
                },
            },
        },
        activation_packet={
            "verdict": "ready_for_business_use",
            "business_use_allowed": True,
            "approval_required": False,
            "claim_readiness": {
                "claim_count": 10,
                "verified_claim_count": 8,
                "critical_or_high_gap_count": 0,
                "has_materialized_claims": True,
                "has_verified_claims": True,
                "has_no_critical_or_high_gaps": True,
            },
        },
        production_probe={
            "truth_surface_status": "deployed",
            "production_data_health_ready": True,
            "production_business_use_allowed": True,
            "data_health_thresholds": {"max_zero_active_links": 0},
            "trust_gaps": [],
            "activation_gaps": [],
            "data_health_gaps": [],
        },
    )

    assert audit["completion_status"] == "complete"
    assert audit["runtime_blockers"] == []
    assert {item["status"] for item in audit["prompt_to_artifact_checklist"]} == {"satisfied"}
    source_overlap_requirement = next(
        item
        for item in audit["prompt_to_artifact_checklist"]
        if item["requirement"].startswith("A narrow Harlem manager source-overlap pilot")
    )
    assert source_overlap_requirement["evidence"]["current_ledger_multi_source_fact_group_count"] == 6
    assert source_overlap_requirement["evidence"]["current_ledger_source_ready_fact_group_count"] == 6
    assert source_overlap_requirement["evidence"]["approval_required"] is False
    assert audit["activation_summary"]["blocked_activation_step_count"] == 0
    assert audit["production_summary"] == {
        "truth_surface_status": "deployed",
        "production_data_health_ready": True,
        "production_business_use_allowed": True,
        "trust_gap_count": 0,
        "activation_gap_count": 0,
        "data_health_gap_count": 0,
    }


def test_truth_completion_audit_blocks_activation_packet_missing_claim_readiness():
    audit = build_completion_audit(
        artifact_checklist=[{"requirement": "x", "status": "satisfied"}],
        health_report={
            "thresholds": {"minimum_claim_count": 1},
            "summary": {
                "trust_posture": "monitor",
                "claim_count": 10,
                "verified_claim_count": 8,
                "critical_or_high_gap_count": 0,
            },
            "trust_gaps": [],
        },
        activation_packet={
            "verdict": "ready_for_business_use",
            "business_use_allowed": True,
            "approval_required": False,
        },
        production_probe={
            "truth_surface_status": "deployed",
            "production_data_health_ready": True,
            "production_business_use_allowed": True,
            "data_health_thresholds": {"max_zero_active_links": 0},
            "trust_gaps": [],
            "activation_gaps": [],
            "data_health_gaps": [],
        },
    )

    activation_blocker = next(blocker for blocker in audit["runtime_blockers"] if blocker["gate"] == "activation_packet")

    assert audit["completion_status"] == "not_complete"
    assert activation_blocker["reason"] == "Activation packet does not prove materialized, verified, gap-free claim readiness."
    assert activation_blocker["evidence"]["activation_claim_failures"] == ["missing_claim_readiness"]


def test_truth_completion_audit_blocks_malformed_production_probe():
    audit = build_completion_audit(
        artifact_checklist=[{"requirement": "x", "status": "satisfied"}],
        health_report={
            "thresholds": {"minimum_claim_count": 1},
            "summary": {
                "trust_posture": "monitor",
                "claim_count": 10,
                "verified_claim_count": 8,
                "critical_or_high_gap_count": 0,
            },
            "trust_gaps": [],
        },
        activation_packet={
            "verdict": "ready_for_business_use",
            "business_use_allowed": True,
            "approval_required": False,
            "claim_readiness": {
                "claim_count": 10,
                "verified_claim_count": 8,
                "critical_or_high_gap_count": 0,
                "has_materialized_claims": True,
                "has_verified_claims": True,
                "has_no_critical_or_high_gaps": True,
            },
        },
        production_probe={
            "truth_surface_status": "not_deployed",
            "production_business_use_allowed": True,
        },
    )

    production_blocker = next(blocker for blocker in audit["runtime_blockers"] if blocker["gate"] == "production_truth_surface")

    assert audit["completion_status"] == "not_complete"
    assert production_blocker["evidence"]["production_probe_failures"] == [
        "production_truth_surface_not_ready",
        "production_data_health_not_ready",
        "missing_production_data_health_thresholds",
        "missing_production_trust_gap_evidence",
        "missing_production_activation_gap_evidence",
        "missing_production_data_health_gap_evidence",
    ]


def test_truth_completion_audit_blocks_production_probe_without_gap_evidence():
    audit = build_completion_audit(
        artifact_checklist=[{"requirement": "x", "status": "satisfied"}],
        health_report={
            "thresholds": {"minimum_claim_count": 1},
            "summary": {
                "trust_posture": "monitor",
                "claim_count": 10,
                "verified_claim_count": 8,
                "critical_or_high_gap_count": 0,
            },
            "trust_gaps": [],
        },
        activation_packet={
            "verdict": "ready_for_business_use",
            "business_use_allowed": True,
            "approval_required": False,
            "claim_readiness": {
                "claim_count": 10,
                "verified_claim_count": 8,
                "critical_or_high_gap_count": 0,
                "has_materialized_claims": True,
                "has_verified_claims": True,
                "has_no_critical_or_high_gaps": True,
            },
        },
        production_probe={
            "truth_surface_status": "deployed",
            "production_data_health_ready": True,
            "production_business_use_allowed": True,
        },
    )

    production_blocker = next(blocker for blocker in audit["runtime_blockers"] if blocker["gate"] == "production_truth_surface")

    assert audit["completion_status"] == "not_complete"
    assert production_blocker["evidence"]["production_probe_failures"] == [
        "missing_production_data_health_thresholds",
        "missing_production_trust_gap_evidence",
        "missing_production_activation_gap_evidence",
        "missing_production_data_health_gap_evidence",
    ]


def test_truth_completion_audit_blocks_partial_or_auth_gated_production_truth_surface():
    audit = build_completion_audit(
        artifact_checklist=[{"requirement": "x", "status": "satisfied"}],
        health_report={
            "thresholds": {"minimum_claim_count": 1},
            "summary": {
                "trust_posture": "monitor",
                "claim_count": 10,
                "verified_claim_count": 8,
                "critical_or_high_gap_count": 0,
            },
            "trust_gaps": [],
        },
        activation_packet={
            "verdict": "ready_for_business_use",
            "business_use_allowed": True,
            "approval_required": False,
            "claim_readiness": {
                "claim_count": 10,
                "verified_claim_count": 8,
                "critical_or_high_gap_count": 0,
                "has_materialized_claims": True,
                "has_verified_claims": True,
                "has_no_critical_or_high_gaps": True,
            },
        },
        production_probe={
            "truth_surface_status": "partial_or_auth_gated",
            "production_data_health_ready": True,
            "production_business_use_allowed": False,
            "data_health_thresholds": {"max_zero_active_links": 0},
            "trust_gaps": [{"severity": "critical", "area": "truth_surface", "message": "Route is auth gated."}],
            "activation_gaps": [],
            "data_health_gaps": [],
        },
    )

    production_blocker = next(blocker for blocker in audit["runtime_blockers"] if blocker["gate"] == "production_truth_surface")

    assert audit["completion_status"] == "not_complete"
    assert production_blocker["evidence"]["truth_surface_status"] == "partial_or_auth_gated"
    assert production_blocker["evidence"]["production_probe_failures"] == [
        "production_truth_surface_not_ready",
        "production_business_use_not_allowed",
        "open_production_trust_gap_evidence",
    ]


def test_truth_completion_audit_blocks_monitor_posture_without_claim_coverage():
    audit = build_completion_audit(
        artifact_checklist=[{"requirement": "x", "status": "satisfied"}],
        health_report={
            "thresholds": {"minimum_claim_count": 1},
            "summary": {
                "trust_posture": "monitor",
                "claim_count": 0,
                "verified_claim_count": 0,
                "critical_or_high_gap_count": 0,
            },
            "trust_gaps": [],
        },
        activation_packet={
            "verdict": "ready_for_business_use",
            "business_use_allowed": True,
            "approval_required": False,
        },
        production_probe={
            "truth_surface_status": "deployed",
            "production_business_use_allowed": True,
        },
    )

    assert audit["completion_status"] == "not_complete"
    assert audit["runtime_blockers"][0]["gate"] == "local_truth_health"
    assert audit["runtime_blockers"][0]["evidence"]["health_failures"] == [
        "insufficient_materialized_claims",
        "no_verified_claims",
    ]


def test_truth_completion_artifact_only_audit_is_ci_safe_without_runtime_gates():
    audit = build_artifact_only_audit(
        artifact_checklist=[
            {"requirement": "present", "status": "satisfied"},
            {"requirement": "missing", "status": "missing"},
        ],
    )

    assert audit["dry_run"] is True
    assert audit["mutations_planned"] == 0
    assert audit["completion_status"] == "artifact_incomplete"
    assert audit["artifact_summary"] == {"total": 2, "satisfied": 1, "missing": 1}
    assert audit["runtime_blockers"][0]["gate"] == "runtime_not_checked"

    complete = build_artifact_only_audit(artifact_checklist=[{"requirement": "present", "status": "satisfied"}])
    assert complete["completion_status"] == "artifacts_satisfied_runtime_not_checked"
    prompt_checklist = {item["requirement"]: item for item in complete["prompt_to_artifact_checklist"]}
    assert prompt_checklist[
        "A narrow Harlem manager source-overlap pilot proves real independent overlap without broad dedupe."
    ]["status"] == "runtime_not_checked"
    assert prompt_checklist["No single-source claim is marked verified."]["status"] == "runtime_not_checked"
    assert prompt_checklist[
        "Local truth health and activation packet allow business use only after verified-claim and review gates pass."
    ]["status"] == "runtime_not_checked"
    assert prompt_checklist[
        "Production truth surface remains blocked until production data-health gates pass."
    ]["status"] == "runtime_not_checked"
