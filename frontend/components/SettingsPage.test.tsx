import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import SettingsPage from './SettingsPage';

const fetchQualitySummaryMock = vi.fn();
const fetchCoverageMock = vi.fn();
const fetchSourceAuditMock = vi.fn();
const fetchTruthDashboardMock = vi.fn();
const fetchTruthHealthReportMock = vi.fn();
const fetchTruthActivationPacketMock = vi.fn();
const fetchTruthCompletionAuditMock = vi.fn();
const fetchTruthSourceOverlapApprovalPacketMock = vi.fn();
const fetchTruthSourceOverlapPostRecordingCheckMock = vi.fn();
const fetchTruthSourceAcquisitionWorklistMock = vi.fn();
const fetchTruthSourceOverlapBlockerReportMock = vi.fn();
const fetchTruthVerificationFrontierMock = vi.fn();
const fetchGoldenBenchmarkMock = vi.fn();
const fetchTruthReviewQueueMock = vi.fn();
const fetchTruthValidationPreviewMock = vi.fn();
const fetchTruthMaterializationPreviewMock = vi.fn();
const fetchTruthAdjudicationPreviewMock = vi.fn();
const previewTruthManualEvidenceMock = vi.fn();
const previewTruthRoleClaimCorrectionMock = vi.fn();
const fetchJobsMock = vi.fn();
const fetchJobsSummaryMock = vi.fn();
const startJobMock = vi.fn();

vi.mock('react-hot-toast', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
}));

vi.mock('../services/quality-api', () => ({
  fetchQualitySummary: (...args: unknown[]) => fetchQualitySummaryMock(...args),
  fetchCoverage: (...args: unknown[]) => fetchCoverageMock(...args),
  fetchSourceAudit: (...args: unknown[]) => fetchSourceAuditMock(...args),
}));

vi.mock('../services/truth-api', () => ({
  fetchTruthDashboard: (...args: unknown[]) => fetchTruthDashboardMock(...args),
  fetchTruthHealthReport: (...args: unknown[]) => fetchTruthHealthReportMock(...args),
  fetchTruthActivationPacket: (...args: unknown[]) => fetchTruthActivationPacketMock(...args),
  fetchTruthCompletionAudit: (...args: unknown[]) => fetchTruthCompletionAuditMock(...args),
  fetchTruthSourceOverlapApprovalPacket: (...args: unknown[]) => fetchTruthSourceOverlapApprovalPacketMock(...args),
  fetchTruthSourceOverlapPostRecordingCheck: (...args: unknown[]) => fetchTruthSourceOverlapPostRecordingCheckMock(...args),
  fetchTruthSourceAcquisitionWorklist: (...args: unknown[]) => fetchTruthSourceAcquisitionWorklistMock(...args),
  fetchTruthSourceOverlapBlockerReport: (...args: unknown[]) => fetchTruthSourceOverlapBlockerReportMock(...args),
  fetchTruthVerificationFrontier: (...args: unknown[]) => fetchTruthVerificationFrontierMock(...args),
  fetchGoldenBenchmark: (...args: unknown[]) => fetchGoldenBenchmarkMock(...args),
  fetchTruthReviewQueue: (...args: unknown[]) => fetchTruthReviewQueueMock(...args),
  fetchTruthValidationPreview: (...args: unknown[]) => fetchTruthValidationPreviewMock(...args),
  fetchTruthMaterializationPreview: (...args: unknown[]) => fetchTruthMaterializationPreviewMock(...args),
  fetchTruthAdjudicationPreview: (...args: unknown[]) => fetchTruthAdjudicationPreviewMock(...args),
  previewTruthManualEvidence: (...args: unknown[]) => previewTruthManualEvidenceMock(...args),
  previewTruthRoleClaimCorrection: (...args: unknown[]) => previewTruthRoleClaimCorrectionMock(...args),
  submitTruthReviewDecision: vi.fn(),
}));

vi.mock('../services/jobs-api', () => ({
  fetchJobs: (...args: unknown[]) => fetchJobsMock(...args),
  fetchJobsSummary: (...args: unknown[]) => fetchJobsSummaryMock(...args),
  startJob: (...args: unknown[]) => startJobMock(...args),
}));

vi.mock('../services/scoring-api', () => ({
  fetchConfigs: vi.fn().mockResolvedValue([]),
  fetchActiveConfig: vi.fn().mockResolvedValue(null),
  activateConfig: vi.fn(),
  triggerRecalculate: vi.fn(),
  createConfig: vi.fn(),
}));

describe('SettingsPage truth health', () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    fetchQualitySummaryMock.mockResolvedValue(null);
    fetchCoverageMock.mockResolvedValue(null);
    fetchSourceAuditMock.mockResolvedValue({
      summary: {
        total_sources: 0,
        operational: 0,
        no_recent_ingest: 0,
        not_wired: 0,
        schema_missing: 0,
        stale_ingest: 0,
      },
      critical_gaps: [],
      sources: [],
    });
    fetchTruthDashboardMock.mockRejectedValue(new Error('truth tables missing'));
    fetchTruthActivationPacketMock.mockResolvedValue({
      dry_run: true,
      mutations_planned: 0,
      verdict: 'schema_approval_required',
      business_use_allowed: false,
      trust_posture: 'not_ready',
      schema: {
        ready: false,
        current_revision: '008_lead_lineage',
        expected_revision: '010_truth_manifest',
        missing_tables: ['truth_claims', 'truth_evidence'],
        ready_to_apply_additive_truth_migration: true,
      },
      approval_required: true,
      approval_steps: [{
        step: 'apply_truth_schema',
        status: 'approval_required',
        reason: 'Apply additive migration 010_truth_manifest.',
        mutations_planned: 7,
      }],
      source_refresh: {
        approval_required: true,
        planned_job_count: 2,
        affected_source_count: 2,
        non_refreshable_gap_count: 1,
        next_jobs: [
          {
            job_type: 'acris',
            reason: 'no_recent_ingest',
            priority: 30,
            blocked: false,
            approval_required: true,
            preview_endpoint: '/api/v1/jobs/acris/start',
            execute_endpoint: '/api/v1/jobs/acris/start?dry_run=false&confirm_execute=true',
            source_count: 1,
            sources: [{ source_name: 'acris_transactions', status: 'no_recent_ingest', source_age_days: null }],
          },
          {
            job_type: 'dob_permits',
            reason: 'schema_missing',
            priority: 10,
            blocked: true,
            approval_required: false,
            preview_endpoint: null,
            execute_endpoint: null,
            source_count: 1,
            sources: [{ source_name: 'dob_permits', status: 'schema_missing', source_age_days: null }],
          },
        ],
      },
      golden_benchmark: {
        configured_cases: 7,
        evaluable_cases: 7,
      },
      claim_readiness: {
        claim_count: 0,
        verified_claim_count: 0,
        critical_or_high_gap_count: 2,
        has_materialized_claims: false,
        has_verified_claims: false,
        has_no_critical_or_high_gaps: false,
      },
      verification_frontier: {
        dry_run: true,
        mutations_planned: 0,
        verification_candidate_count: 0,
        current_ledger: {
          total_fact_group_count: 2078,
          single_source_fact_group_count: 2063,
          multi_source_fact_group_count: 15,
          source_ready_fact_group_count: 15,
        },
        source_ready_below_verified_count: 10,
        single_source_gap_count: 10,
        single_source_upgrade_would_verify_count: 2,
        bundle_upgrade_would_verify_count: 10,
        manager_next_source_seed_count: 1,
        operator_second_source_seed_count: 4,
        evidence_acquisition_required: true,
        business_use_blocker: 'No facts are verified or eligible for verified adjudication yet. Source-ready facts still need stronger exact-property, role-specific evidence before business use can be activated.',
        next_preview_command: 'python scripts/truth_verification_frontier.py --limit 10 --indent 2',
        safe_action: 'Review the verification frontier and evidence request packet.',
      },
      trust_gap_summary: [{
        severity: 'critical',
        area: 'schema_readiness',
        message: 'Truth tables are missing.',
      }],
      next_safe_steps: [
        {
          step: 'review_preflight_sql',
          command: 'python scripts/truth_migration_preflight.py --indent 2',
          mutates_data: false,
        },
        {
          step: 'apply_truth_schema',
          command: 'python -m alembic upgrade 010_truth_manifest',
          mutates_data: true,
          requires_explicit_approval: true,
        },
      ],
      rollback: {
        strategy: 'Drop additive truth tables in dependency order.',
        offline_rollback_command: 'python -m alembic downgrade 010_truth_manifest:008_lead_lineage --sql',
      },
    });
    fetchTruthCompletionAuditMock.mockResolvedValue({
      dry_run: true,
      mutations_planned: 0,
      objective: 'Transform Double Edge into an evidence-backed intelligence system.',
      completion_status: 'not_complete',
      success_criteria: ['Artifacts', 'Source overlap', 'Production'],
      prompt_to_artifact_checklist: [
        { requirement: 'Artifacts exist.', status: 'satisfied', evidence: { artifact_missing: 0 } },
        {
          requirement: 'A narrow Harlem manager source-overlap pilot proves real independent overlap without broad dedupe.',
          status: 'satisfied',
          evidence: {
            current_ledger_multi_source_fact_group_count: 13,
            current_ledger_source_ready_fact_group_count: 13,
            approval_required: false,
          },
        },
        {
          requirement: 'Production truth surface remains blocked until production data-health gates pass.',
          status: 'blocked',
          evidence: { production_business_use_allowed: false },
        },
      ],
      artifact_summary: { total: 25, satisfied: 25, missing: 0 },
      runtime_blockers: [
        {
          gate: 'local_truth_health',
          reason: 'Local truth health does not yet prove business-ready claim coverage.',
          evidence: {
            claim_count: 2109,
            verified_claim_count: 0,
          },
        },
        {
          gate: 'activation_packet',
          reason: 'Activation packet does not allow business use.',
          evidence: {
            verdict: 'materialization_or_review_required',
            business_use_allowed: false,
          },
        },
        { gate: 'production_truth_surface', reason: 'Production not ready.' },
      ],
      production_probe_included: false,
      production_probe_note: 'Use scripts/truth_completion_audit.py --include-runtime --include-production.',
    });
    fetchTruthSourceOverlapApprovalPacketMock.mockResolvedValue({
      run_type: 'truth_source_overlap_approval_packet',
      run_id: 'truth-source-overlap-approval-test',
      dry_run: true,
      mutations_planned: 0,
      current_ledger: {
        total_fact_group_count: 2076,
        single_source_fact_group_count: 2063,
        multi_source_fact_group_count: 13,
        source_ready_fact_group_count: 13,
        verification_candidate_count: 0,
      },
      previewed_overlap_if_approved: {
        manager_source_ready_if_recorded_count: 14,
        manager_strict_source_ready_if_recorded_count: 13,
        operator_source_ready_if_recorded_count: 4,
        operator_strict_source_ready_if_recorded_count: 2,
        safe_to_mark_verified_after_recording: 0,
      },
      source_overlap_recording_gate: {
        status: 'satisfied',
        current_multi_source_fact_group_count: 13,
        current_source_ready_fact_group_count: 13,
        current_verification_candidate_count: 0,
        source_overlap_proof_satisfied: true,
        additional_evidence_recording_requires_approval: true,
        safe_action: 'The current ledger already proves source overlap; do not rerun an evidence batch just to satisfy the source-overlap gate.',
      },
      recommended_first_packet: {
        template_count: 46,
        claim_group_count: 13,
        source_names: ['hpm_revenue_by_property_summary', 'justia', 'mystatemls', 'ny_dps_order_entry', 'openigloo', 'renthistory', 'renthop', 'verizon_order_entry_petition', 'zillow'],
        source_families: ['external_web_profile', 'first_party_operator_document', 'hpd_registration_derived', 'litigation_records', 'ny_dps_order_entry', 'real_estate_listing'],
        manager_proof_source_families: ['external_web_profile', 'first_party_operator_document', 'litigation_records', 'ny_dps_order_entry', 'real_estate_listing'],
        planned_upsert_count_if_approved: 138,
        recommended_execute_command: 'python scripts/truth_manager_external_evidence_batch.py --strict-manager-proof-only --execute --confirm-execute --indent 2',
        sample_manual_evidence_previews: [{
          claim_id: 'claim-hpm-342',
          evidence_id: 'evidence-hpm-342',
          predicate: 'manages_building',
          object_id: '1010460054',
          source_name: 'openigloo',
          mutations_planned: 3,
          allowed_execute: false,
          mutation_scope: {
            allowed_tables: ['truth_materialization_manifest', 'truth_claims', 'truth_evidence', 'confidence_snapshots'],
            forbidden_side_effects: {
              will_mark_verified: false,
              will_create_or_refresh_source_data: false,
              will_materialize_building_management_relationships: false,
              will_start_jobs: false,
              will_allow_business_use: false,
            },
          },
        }],
        approval_decision_summary: {
          approval_required: true,
          batch_filter: 'strict_manager_proof',
          recommended_execute_command: 'python scripts/truth_manager_external_evidence_batch.py --strict-manager-proof-only --execute --confirm-execute --indent 2',
          would_record_template_count: 46,
          would_record_claim_group_count: 13,
          would_plan_upsert_count: 138,
          included_addresses: ['324 EAST 112 STREET', '36 WEST 138 STREET', '204 WEST 140 STREET', '2257 ADAM C POWELL BOULEVARD', '306 WEST 115 STREET', '330 WEST 145 STREET', '342 WEST 56 STREET', '345 LENOX AVENUE', '42 WEST 120 STREET', '506 EAST 119 STREET', '555 LENOX AVENUE', '61 LENOX AVENUE', '11 ST NICHOLAS AVENUE'],
          expected_multi_source_fact_group_count: 13,
          expected_source_ready_fact_group_count: 13,
          expected_strict_manager_source_ready_fact_group_count: 13,
          expected_safe_to_mark_verified_count: 0,
          single_source_claims_stay_unverified: true,
          will_mark_verified: false,
          will_create_or_refresh_source_data: false,
          will_materialize_new_relationships: false,
          post_execution_required_checks: [
            'python scripts/truth_adjudication_preview.py --limit 20 --no-samples --indent 2',
          ],
          safe_action: 'Approve only if the listed exact-property sources have been inspected.',
        },
      },
      manager_strict_gap_summary: {
        claim_group_count: 14,
        strict_ready_claim_group_count: 13,
        broad_source_ready_not_strict_count: 1,
        single_source_only_count: 0,
        status_counts: {
          broad_source_ready_not_strict: 1,
          strict_manager_proof_ready_if_recorded: 13,
        },
        gap_candidates: [{
          bbl: '1019080014',
          address: '141 WEST 123 STREET',
          strict_manager_gap_status: 'broad_source_ready_not_strict',
          missing_manager_proof_source_family_count: 1,
          suggested_source_families: ['first_party_operator_document', 'ny_dps_order_entry', 'company_website'],
          next_required_manager_proof: 'Acquire one exact non-HPD manager-proof source family.',
        }],
        safe_action: 'Treat non-strict HPM groups as source-acquisition targets only.',
      },
      manager_new_relationship_candidate_summary: {
        candidate_count: 3,
        counts_as_current_ledger_overlap: false,
        approval_required_for_relationship_creation: true,
        source_family_counts: {
          company_website: 1,
          ny_dps_order_entry: 1,
          nyc_dof_billing_record: 1,
        },
        candidates: [{
          candidate_id: 'ny-dps-verizon-402-w-153-petition',
          source_name: 'ny_dps_order_entry',
          source_family: 'ny_dps_order_entry',
          external_address: '402 WEST 153 STREET',
          local_address: '402 WEST 153 STREET',
          bbl: '1020670047',
          manager_name: 'Harlem Property Management, Inc.',
          evidence_role: 'managing_agent',
          source_url: 'https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=example',
          current_relationship_state: {
            current_building_management_relationship_count: 0,
            current_truth_claim_count: 0,
            counts_as_current_ledger_overlap: false,
            relationship_review_required: true,
          },
          safe_action: 'Review as possible new relationship; not current-ledger overlap.',
        }],
        safe_action: 'Source-backed new relationship candidates are acquisition leads only. They do not count as current-ledger source overlap and require explicit review/approval before relationship creation.',
      },
      operator_strict_packet: {
        current_recording_status: 'truth_ledger_evidence_already_recorded',
        template_count: 6,
        claim_group_count: 2,
        source_names: ['homes', 'justia', 'outreach_confirmed', 'redfin', 'renthistory'],
        source_families: ['hpd_registration_derived', 'litigation_records', 'operator_confirmed', 'real_estate_listing'],
        manager_proof_source_families: ['litigation_records', 'operator_confirmed', 'real_estate_listing'],
        planned_upsert_count_if_approved: 18,
        recording_effect_if_rerun: {
          would_create_new_claim_count: 0,
          would_create_new_evidence_count: 0,
          would_update_existing_claim_count: 6,
          would_update_existing_evidence_count: 6,
          would_create_confidence_snapshot_count: 6,
        },
        safe_action: 'The strict operator evidence is already represented in truth_claims/truth_evidence. Treat this packet as an idempotent repair/review packet only.',
        sample_manual_evidence_previews: [{
          claim_id: 'claim-operator-9ppw',
          evidence_id: 'evidence-operator-9ppw',
          predicate: 'manages_building',
          object_id: '3010680037',
          source_name: 'outreach_confirmed',
          mutations_planned: 3,
          allowed_execute: false,
          mutation_scope: {
            allowed_tables: ['truth_materialization_manifest', 'truth_claims', 'truth_evidence', 'confidence_snapshots'],
            forbidden_side_effects: {
              will_mark_verified: false,
              will_create_or_refresh_source_data: false,
              will_materialize_building_management_relationships: false,
              will_start_jobs: false,
              will_allow_business_use: false,
            },
          },
        }],
        approval_decision_summary: {
          approval_required: true,
          batch_filter: 'strict_manager_proof',
          recommended_execute_command: 'python scripts/truth_operator_confirmed_evidence_batch.py --strict-manager-proof-only --execute --confirm-execute --indent 2',
          would_record_template_count: 6,
          would_record_claim_group_count: 2,
          would_plan_upsert_count: 18,
          included_addresses: ['4 WEST 16 STREET', '9 PROSPECT PARK WEST'],
          expected_multi_source_fact_group_count: 2,
          expected_source_ready_fact_group_count: 2,
          expected_strict_manager_source_ready_fact_group_count: 2,
          expected_safe_to_mark_verified_count: 0,
          single_source_claims_stay_unverified: true,
          will_mark_verified: false,
          will_create_or_refresh_source_data: false,
          will_materialize_new_relationships: false,
          safe_action: 'Approve only if the first-hand operator confirmation and listed exact-property second sources have been inspected.',
        },
        included_candidate_count: 2,
        excluded_non_strict_candidate_count: 2,
        excluded_non_strict_candidates: [{
          candidate_id: 'operator-confirmed-md-squared-220-3-ave',
          address: '220 3 AVENUE',
          manager_name: 'MD Squared Property Group',
          strict_manager_gap_status: 'broad_source_ready_not_strict',
          missing_manager_proof_source_family_count: 1,
          next_required_manager_proof: 'Acquire one exact non-HPD manager-proof source family.',
          exclusion_reason: 'strict_manager_proof_not_ready',
        }, {
          candidate_id: 'operator-confirmed-md-squared-57-bond',
          address: '57 BOND STREET',
          manager_name: 'MD Squared Property Group',
          strict_manager_gap_status: 'broad_source_ready_not_strict',
          missing_manager_proof_source_family_count: 1,
          next_required_manager_proof: 'Acquire one exact non-HPD manager-proof source family.',
          exclusion_reason: 'strict_manager_proof_not_ready',
        }],
      },
      operator_strict_gap_summary: {
        candidate_count: 4,
        strict_ready_candidate_count: 2,
        broad_source_ready_not_strict_count: 2,
        single_source_only_count: 0,
        status_counts: {
          broad_source_ready_not_strict: 2,
          strict_manager_proof_ready_if_recorded: 2,
        },
        gap_candidates: [{
          candidate_id: 'operator-confirmed-md-squared-220-3-ave',
          address: '220 3 AVENUE',
          manager_name: 'MD Squared Property Group',
          strict_manager_gap_status: 'broad_source_ready_not_strict',
          missing_manager_proof_source_family_count: 1,
          strict_manager_gap_reason: 'Broad source-ready only because RentHistory is HPD-registration-derived.',
          next_required_manager_proof: 'Acquire one exact non-HPD manager-proof source family.',
        }, {
          candidate_id: 'operator-confirmed-md-squared-57-bond',
          address: '57 BOND STREET',
          manager_name: 'MD Squared Property Group',
          strict_manager_gap_status: 'broad_source_ready_not_strict',
          missing_manager_proof_source_family_count: 1,
          strict_manager_gap_reason: 'Broad source-ready only because RentHistory is HPD-registration-derived.',
          next_required_manager_proof: 'Acquire one exact non-HPD manager-proof source family.',
        }],
        safe_action: 'Treat broad operator overlap as source-acquisition context only.',
      },
      approval_required: true,
      approval_policy: {
        single_source_claims_stay_unverified: true,
      },
      post_execution_required_checks: [
        'python scripts/truth_adjudication_preview.py --limit 20 --no-samples --indent 2',
      ],
      blocked_business_use_reason: 'Business use remains blocked until local truth health, activation, and production truth-surface gates pass.',
      safe_action: 'Review only.',
    });
    fetchTruthSourceOverlapPostRecordingCheckMock.mockResolvedValue({
      run_type: 'truth_source_overlap_post_recording_check',
      dry_run: true,
      mutations_planned: 0,
      post_recording_success: true,
      thresholds: {
        min_multi_source_fact_groups: 1,
        min_source_ready_fact_groups: 1,
        max_verified_single_source_claims: 0,
      },
      current_ledger: {
        total_fact_group_count: 2076,
        single_source_fact_group_count: 2063,
        multi_source_fact_group_count: 13,
        source_ready_fact_group_count: 13,
        max_supporting_source_count: 5,
        max_supporting_evidence_count: 5,
      },
      verified_single_source_policy: {
        verified_claim_count: 0,
        verified_single_source_claim_count: 0,
        sample_limit: 5,
        samples: [],
      },
      checks: [
        {
          check: 'actual_current_ledger_multi_source',
          status: 'pass',
          observed: 13,
          minimum: 1,
          reason: 'Current ledger has nonzero independent source overlap.',
        },
        {
          check: 'actual_current_ledger_source_ready',
          status: 'pass',
          observed: 13,
          minimum: 1,
          reason: 'Current ledger has source-ready fact groups under adjudication thresholds.',
        },
        {
          check: 'no_single_source_verified_claims',
          status: 'pass',
          observed: 0,
          maximum: 0,
          reason: 'No verified current claim has fewer than two supporting source names.',
        },
      ],
      safe_action: 'Post-recording source-overlap gate passed; continue with truth health, completion audit, activation packet, and production truth-surface checks before business use.',
    });
    fetchTruthSourceAcquisitionWorklistMock.mockResolvedValue({
      run_type: 'truth_source_acquisition_worklist',
      dry_run: true,
      mutations_planned: 0,
      source: 'truth_verification_frontier.evidence_request_packet',
      frontier_current_ledger: {
        total_fact_group_count: 2078,
        single_source_fact_group_count: 2063,
        multi_source_fact_group_count: 15,
        source_ready_fact_group_count: 15,
      },
      verification_candidate_count: 0,
      request_count: 15,
      work_item_count: 5,
      hpd_work_item_count: 5,
      recording_ready_count: 0,
      approval_required_count: 15,
      csv_template: 'relationship_label,bbl,address,manager_name,source_family,source_name\nMD Squared Property Group manages building 220 3 AVENUE,1008747504,220 3 AVENUE,MD Squared Property Group,hpd_management_company,hpd_management_company\nMD Squared Property Group manages building 220 3 AVENUE,1008747504,220 3 AVENUE,MD Squared Property Group,outreach_confirmed,outreach_confirmed\n',
      csv_template_command: 'truth_source_acquisition_worklist.py --lead-id 0ff794d3ba2d --frontier-limit 10 --max-items 5 --csv-template',
      hpd_fetch_packet: 'work_item_id,relationship_label,bbl,address,manager_name,registrations_api\nsource-acquisition-001,MD Squared Property Group manages building 220 3 AVENUE,1008747504,220 3 AVENUE,MD Squared Property Group,https://data.cityofnewyork.us/resource/tesw-yqqr.json?bbl=1008747504\n',
      hpd_fetch_packet_command: 'truth_source_acquisition_worklist.py --lead-id 0ff794d3ba2d --frontier-limit 10 --max-items 5 --hpd-fetch-packet',
      operator_confirmation_packet: 'work_item_id,question_prompt,non_duplicate_boundary\nsource-acquisition-001,Can you independently confirm that MD Squared Property Group currently manages 220 3 AVENUE?,Do not reuse the same first-hand note already in the ledger.\n',
      operator_confirmation_packet_command: 'truth_source_acquisition_worklist.py --lead-id 0ff794d3ba2d --frontier-limit 10 --max-items 5 --operator-confirmation-packet',
      candidate_csv_preview_command: 'truth_source_evidence_intake.py --candidate-csv <filled-worklist.csv> --recommended-scope-only --indent 2',
      work_items: [{
        work_item_id: 'source-acquisition-001',
        priority: 10,
        request_type: 'operator_source_acquisition',
        relationship: {
          relationship_label: 'MD Squared Property Group manages building 220 3 AVENUE',
          bbl: '1008747504',
          address: '220 3 AVENUE',
          manager_name: 'MD Squared Property Group',
          manager_lead_id: '56a71624c6c0',
        },
        current_sources: [],
        strict_manager_gap_status: 'broad_source_ready_not_strict',
        can_become: 'strict_manager_source_gap_after_operator_seed',
        evidence_need: 'Acquire one exact non-HPD manager-proof source family.',
        source_family_needs: ['company_website', 'external_web_profile', 'hpd_management_company', 'ny_dos', 'outreach_confirmed'],
        search_queries: ['"MD Squared" "220 3 Avenue"'],
        source_targets: [],
        official_hpd_query: {
          registrations_api: 'https://data.cityofnewyork.us/resource/tesw-yqqr.json?$where=boroid%3D1%20AND%20block%3D874%20AND%20lot%3D7504',
        },
        official_hpd_download_urls: [
          'https://data.cityofnewyork.us/api/views/tesw-yqqr/rows.csv?accessType=DOWNLOAD',
          'https://data.cityofnewyork.us/api/views/feu5-w2e2/rows.csv?accessType=DOWNLOAD',
        ],
        read_only_hpd_preview_command: '.\\.venv-x64\\Scripts\\python.exe scripts\\truth_live_hpd_role_audit.py --bbl 1008747504 --expected-agent "MD SQUARED PROPERTY GROUP" --expected-manager "MD Squared Property Group" --no-include-operator-seeds --no-include-hpm-nonstrict --query-packet-only --indent 2',
        post_fetch_local_extract_command: '.\\.venv-x64\\Scripts\\python.exe scripts\\truth_live_hpd_role_audit.py --bbl 1008747504 --registrations-file <path> --contacts-file <path>',
        acceptance_criteria: [
          'Only `ManagementCompany` contact rows can support the HPD manager-proof family.',
        ],
        paste_back_template: {
          relationship_label: 'MD Squared Property Group manages building 220 3 AVENUE',
          bbl: '1008747504',
          address: '220 3 AVENUE',
          manager_name: 'MD Squared Property Group',
          source_family: 'hpd_management_company',
        },
        paste_back_templates: [
          { source_family: 'hpd_management_company' },
          { source_family: 'outreach_confirmed' },
        ],
        operator_confirmation_request: {
          status: 'needs_dated_independent_confirmation',
          source_family: 'outreach_confirmed',
          question_prompt: 'Can you independently confirm that MD Squared Property Group currently manages 220 3 AVENUE?',
          non_duplicate_boundary: 'Do not reuse the same first-hand note already in the ledger.',
          required_fields: ['confirmation_channel', 'confirmed_by_name_or_role'],
          paste_back_template: { source_family: 'outreach_confirmed', exact_property_match: true },
          contradiction_paste_back_template: {
            source_family: 'outreach_confirmed',
            contradicts_current_claim: true,
          },
          contradiction_handling: 'If the source names a different current manager, use contradiction_paste_back_template and route to review.',
          preview_command: 'truth_source_evidence_intake.py --candidate-csv <filled-worklist.csv> --recommended-scope-only --indent 2',
          safe_action: 'Preview only.',
        },
        paste_back_fields: [
          'relationship_label',
          'bbl',
          'address',
          'manager_name',
          'source_family',
          'source_record_id',
        ],
        reviewed_source_history_status: 'reviewed_dead_end_no_recording_ready_source',
        reviewed_source_findings: [{
          source_family: 'official_hpd_and_public_web_refresh_md_daisy_2026_05_18',
          qualification: 'No exact non-HPD manager-proof source was found.',
        }],
        safe_action: 'Source acquisition only. Do not record evidence or mark verified.',
      }],
      policy: {
        single_source_policy: 'No single-source claim may be marked verified.',
        role_policy: 'Agent is not manager; role-specific evidence stays role-specific.',
        execution_policy: 'This worklist is read-only and cannot record evidence or change claim status.',
      },
      next_step_after_source_found: 'Paste back the filled template, then preview manual evidence.',
      safe_action: 'Use this as a human/source-acquisition checklist. It is not evidence and does not permit business use.',
    });
    fetchTruthSourceOverlapBlockerReportMock.mockResolvedValue({
      run_type: 'truth_source_overlap_blocker_report',
      dry_run: true,
      mutations_planned: 0,
      status: 'blocked_evidence_acquisition_required',
      current_ledger: {
        total_fact_group_count: 2078,
        single_source_fact_group_count: 2063,
        multi_source_fact_group_count: 15,
        source_ready_fact_group_count: 15,
      },
      verification_candidate_count: 0,
      source_ready_fact_group_count: 15,
      source_ready_below_verified_count: 10,
      single_source_upgrade_would_verify_count: 2,
      bundle_upgrade_would_verify_count: 10,
      threshold_sensitive_relationships: [
        {
          relationship_label: 'Daisy Management manages building 9 PROSPECT PARK WEST',
          bbl: '3010680037',
          address: '9 PROSPECT PARK WEST',
          manager_name: 'Daisy Management',
          current_sources: ['homes', 'outreach_confirmed', 'redfin'],
          current_confidence_score: 0.817,
          verified_confidence_threshold: 0.9,
          score_gap_to_verified: 0.083,
          best_single_source: 'hpd_management_company',
          best_single_source_simulated_confidence: 0.901,
          required_bundle_sources: ['hpd_management_company', 'company_website', 'ny_dos'],
          required_real_evidence_count: 4,
          recording_ready: false,
          approval_required_before_recording: true,
          reviewed_source_history_status: 'reviewed_dead_end_no_recording_ready_source',
          reviewed_source_findings: [
            {
              source_family: 'live_hpd_threshold_candidate_role_audit_2026_06_01',
              finding: 'Official HPD rows name Daisy as Agent but do not include ManagementCompany.',
              qualification: 'Agent rows stay legal-contact evidence and are not manager-proof support.',
            },
          ],
          safe_action: 'Acquire stronger role-explicit evidence before marking verified.',
        },
      ],
      evidence_request_summary: {
        request_count: 15,
        work_item_count: 5,
        hpd_work_item_count: 5,
        recording_ready_count: 0,
        approval_required_count: 15,
        reviewed_source_finding_count: 75,
        reviewed_source_history_status: 'reviewed_dead_end_no_recording_ready_source',
      },
      source_bridge_assessment: {
        can_record_evidence_now: false,
        has_preview_ready_candidate_batch: true,
        can_mark_verified_now: false,
        blocking_reasons: [
          'verification_candidate_count=0',
          'recording_ready_count=0',
          'source_acquisition_requests_remain_open',
        ],
        why_current_overlap_is_not_enough: 'Already-reviewed context does not supply a recording-ready exact-property manager-proof source.',
      },
      source_evidence_candidate_summary: {
        status: 'preview_ready_approval_required',
        checked: true,
        source_mode: 'candidate_file_recommended_scope_only',
        candidate_count: 2,
        original_candidate_count: 4,
        filtered_out_candidate_count: 2,
        ready_for_manual_evidence_preview_count: 2,
        recording_ready_count: 2,
        new_supporting_source_ready_count: 2,
        supporting_source_already_present_count: 0,
        contradiction_candidate_count: 0,
        blocked_count: 0,
        recommended_count: 2,
        recommended_relationships: [{
          work_item_id: 'source-acquisition-001',
          relationship_label: 'MD Squared Property Group manages building 220 3 AVENUE',
          bbl: '1008747504',
          address: '220 3 AVENUE',
          manager_name: 'MD Squared Property Group',
          source_name: 'outreach_confirmed',
          effect_status: 'adds_new_supporting_source',
        }],
        duplicate_or_freshness_only_count: 2,
        duplicate_or_freshness_only_relationships: [],
        contradiction_review_count: 0,
        contradiction_relationships: [],
        allowed_execute: false,
        required_execute_flags_for_batch: ['--execute', '--confirm-execute', '--confirm-batch-execute'],
        recording_approval_packet: {
          status: 'preview_ready_approval_required',
          approval_required: true,
          allowed_execute: false,
          recommended_count: 2,
          manual_evidence_payload_count: 2,
          manual_evidence_payload_review: [{
            payload_index: 1,
            manager_name: 'MD Squared Property Group',
            object_id: '1008747504',
            source_name: 'outreach_confirmed',
          }],
          expected_post_recording_source_overlap: {
            recommended_row_count: 2,
            first_source_only_after_recording_count: 2,
            multi_source_after_recording_count: 0,
            source_ready_after_recording_count: 0,
            safe_action: 'The recommended recording scope would add evidence, but none of the listed rows would become multi-source/source-ready immediately after recording.',
          },
          approval_question: 'Approve recording 2 preview-clean new-supporting-source manual-evidence row(s) only?',
          execute_command_after_approval: 'truth_manual_evidence.py --payload-file <reviewed-preview.json> --execute --confirm-execute --confirm-batch-execute',
          safe_action: 'Use this packet as the human approval boundary.',
        },
        safe_action: 'Candidate preview is read-only. A preview-ready candidate still requires explicit execution approval.',
      },
      top_blocked_relationships: [{
        work_item_id: 'source-acquisition-001',
        priority: 10,
        request_type: 'operator_source_acquisition',
        relationship_label: 'MD Squared Property Group manages building 220 3 AVENUE',
        bbl: '1008747504',
        address: '220 3 AVENUE',
        manager_name: 'MD Squared Property Group',
        current_sources: [],
        source_family_needs: ['company_website', 'hpd_management_company', 'outreach_confirmed'],
        strict_manager_gap_status: 'broad_source_ready_not_strict',
        reviewed_source_history_status: 'reviewed_dead_end_no_recording_ready_source',
        has_official_hpd_query_packet: true,
        post_fetch_local_extract_command: '.\\.venv-x64\\Scripts\\python.exe scripts\\truth_live_hpd_role_audit.py --bbl 1008747504 --registrations-file <path> --contacts-file <path>',
        safe_action: 'Source acquisition only. Do not record evidence or mark verified.',
      }],
      reviewed_source_summary: {
        reviewed_source_family_counts: {
          official_hpd_and_public_web_refresh_md_daisy_2026_05_19_phase2: 1,
        },
        sample_reviewed_source_findings: [],
      },
      policy: {
        single_source_policy: 'No single-source claim may be marked verified.',
        role_policy: 'Agent is not manager.',
        recording_policy: 'Manual evidence recording requires explicit approval.',
        business_use_policy: 'Business use remains blocked.',
      },
      next_required_action: 'Acquire a real exact-property manager source and preview it before recording.',
      safe_action: 'Use this report to explain the current blocker. It is not evidence, does not record evidence, and does not permit business use.',
    });
    fetchTruthVerificationFrontierMock.mockResolvedValue({
      run_type: 'truth_verification_frontier',
      dry_run: true,
      mutations_planned: 0,
      limit: 5,
      current_ledger: {
        total_fact_group_count: 2078,
        single_source_fact_group_count: 2063,
        multi_source_fact_group_count: 15,
        source_ready_fact_group_count: 15,
      },
      verification_candidate_count: 0,
      source_ready_below_verified: {
        proposal_count: 10,
        single_source_upgrade_would_verify_count: 2,
        bundle_upgrade_would_verify_count: 10,
        proposals: [{
          fact_key: {
            subject_type: 'lead',
            subject_id: 'd11246cb2dae',
            predicate: 'manages_building',
            object_type: 'building',
            object_id: '3010680037',
            normalized_value: 'manager',
          },
          display: {
            subject_label: 'Daisy Management',
            predicate_label: 'manages building',
            object_label: '9 PROSPECT PARK WEST',
            relationship_label: 'Daisy Management manages building 9 PROSPECT PARK WEST',
            building: {
              bbl: '3010680037',
              address: '9 PROSPECT PARK WEST',
              borough: 'BROOKLYN',
              unit_count: 48,
            },
          },
          current_sources: ['homes', 'outreach_confirmed', 'redfin'],
          supporting_source_count: 3,
          supporting_evidence_count: 3,
          recomputed_confidence_score: 0.817,
          verified_confidence_threshold: 0.9,
          score_gap_to_verified: 0.083,
          best_single_source_upgrade: {
            suggested_source: 'hpd_management_company',
            simulated_confidence_score: 0.901,
            would_reach_verified_threshold: true,
          },
          bundle_upgrade_would_verify: true,
          bundle_simulated_confidence_score: 0.906,
          required_bundle_sources: ['hpd_management_company', 'company_website', 'outreach_confirmed'],
          required_real_evidence: [{
            suggested_source: 'hpd_management_company',
            required_fields: ['exact_property_match', 'role_specific_management_support'],
            acquisition_mode: 'official_hpd_query_packet_only',
            source_dataset_ids: ['tesw-yqqr', 'feu5-w2e2'],
            official_query_urls: {
              registrations_api: 'https://data.cityofnewyork.us/resource/tesw-yqqr.json?$where=boroid%3D3%20AND%20block%3D1068%20AND%20lot%3D37',
              contacts_api_template: 'https://data.cityofnewyork.us/resource/feu5-w2e2.json?$where=registrationid={registration_id}',
            },
            read_only_preview_command: '.\\.venv-x64\\Scripts\\python.exe scripts\\truth_live_hpd_role_audit.py --bbl 3010680037 --expected-agent "DAISY MANAGEMENT" --expected-manager "Daisy Management" --no-include-operator-seeds --no-include-hpm-nonstrict --query-packet-only --indent 2',
          }],
          required_real_evidence_count: 1,
          evidence_acquisition_status: 'acquisition_required',
          recording_ready: false,
          approval_required_before_recording: true,
          safe_action: 'Use this frontier for evidence acquisition and review planning only.',
        }],
      },
      single_source_gaps: { proposal_count: 10, proposals: [] },
      source_acquisition_frontier: {
        manager_next_source_seed_count: 1,
        operator_second_source_seed_count: 4,
        manager_proposals: [{
          candidate_id: 'hpm-141-w-123',
          bbl: '1019080014',
          address: '141 WEST 123 STREET',
          manager_name: 'Harlem Property Management, Inc.',
          strict_manager_gap_status: 'broad_source_ready_not_strict',
          missing_manager_proof_source_family_count: 1,
          suggested_source_families: ['company_website', 'outreach_confirmed'],
          first_search_query: '"Harlem Property Management" "141 West 123 Street"',
          search_queries: ['"Harlem Property Management" "141 West 123 Street"'],
          source_targets: [],
          safe_action: 'Acquire one more manager-specific source.',
        }],
        operator_proposals: [{
          candidate_id: 'operator-md-220',
          bbl: '1008747504',
          address: '220 3 AVENUE',
          manager_name: 'MD Squared Property Group',
          strict_manager_gap_status: 'broad_source_ready_not_strict',
          missing_manager_proof_source_family_count: 1,
          suggested_source_families: ['company_website', 'ny_dos'],
          first_search_query: '"MD Squared" "220 3 Avenue"',
          search_queries: ['"MD Squared" "220 3 Avenue"'],
          source_targets: [],
          current_relationship_state: {
            current_building_management_relationship_count: 0,
            current_truth_claim_count: 0,
            current_ledger_source_ready: false,
          },
          next_required_manager_proof: 'Acquire one exact non-HPD manager-proof source family.',
          safe_action: 'Treat first-hand confirmations as evidence seeds.',
        }],
      },
      verification_readiness_gate: {
        status: 'blocked_evidence_acquisition_required',
        verification_candidate_count: 0,
        source_ready_below_verified_count: 10,
        record_ready_count: 0,
        acquisition_required_count: 10,
        approval_required_count: 10,
        required_real_evidence_count: 42,
        one_source_threshold_clear_count: 2,
        bundle_threshold_clear_count: 10,
        single_source_gap_count: 0,
        reason: 'Source-ready facts still require real exact-property evidence.',
        safe_action: 'Do not mark any claim verified from the frontier alone.',
      },
      evidence_request_packet: {
        dry_run: true,
        mutations_planned: 0,
        shown_limit_per_section: 5,
        request_count: 15,
        displayed_request_count: 3,
        source_ready_request_count: 10,
        single_source_request_count: 0,
        manager_source_request_count: 1,
        operator_source_request_count: 4,
        source_acquisition_request_count: 5,
        recording_ready_count: 0,
        approval_required_count: 3,
        reviewed_source_finding_count: 2,
        reviewed_source_history_status: 'reviewed_dead_end_no_recording_ready_source',
        reviewed_source_findings: [{
          source_family: 'public_web_search_followup_hpm_batch_20_2026_05_16',
          source_urls: ['https://renthistory.org/corporation/corp_report.php?corporationname=HARLEM+PROPERTY+MANAGEMENT+INC.'],
          finding: '141 WEST 123 STREET still resolves only to HPD-registration-derived context.',
          qualification: 'This adds no strict evidence template and does not change source-ready or verified counts.',
        }, {
          source_family: 'public_web_search_followup_md_squared_batch_18_2026_05_16',
          source_urls: ['https://renthistory.org/corporation/corp_report.php?corporationname=MD+Squared+Property+Group'],
          finding: '220 3 Avenue and 57 Bond Street still resolve only to HPD-derived context.',
          qualification: 'No real HPD ManagementCompany row or exact non-HPD manager-proof source was found.',
        }],
        source_ready_requests: [{
          request_type: 'source_ready_below_verified',
          relationship_label: 'Daisy Management manages building 9 PROSPECT PARK WEST',
          fact_key: {
            subject_type: 'lead',
            subject_id: 'd11246cb2dae',
            predicate: 'manages_building',
            object_type: 'building',
            object_id: '3010680037',
          },
          display: {
            relationship_label: 'Daisy Management manages building 9 PROSPECT PARK WEST',
          },
          current_sources: ['homes', 'outreach_confirmed', 'redfin'],
          current_confidence_score: 0.817,
          can_become: 'verified_candidate_after_real_evidence_preview_recording_and_adjudication',
          evidence_need: 'Acquire stronger exact-property, role-specific management evidence before verification.',
          required_sources: ['hpd_management_company', 'company_website', 'outreach_confirmed'],
          required_real_evidence: [{
            suggested_source: 'hpd_management_company',
            required_fields: ['exact_property_match', 'role_specific_management_support'],
          }],
          recording_ready: false,
          approval_required_before_recording: true,
          reviewed_source_history_status: 'reviewed_dead_end_no_recording_ready_source',
          reviewed_source_findings: [{
            source_family: 'public_web_search_followup_md_squared_batch_18_2026_05_16',
            qualification: 'No real HPD ManagementCompany row or exact non-HPD manager-proof source was found.',
          }],
        }],
        single_source_requests: [],
        source_acquisition_requests: [{
          request_type: 'manager_source_acquisition',
          candidate_id: 'hpm-141-w-123',
          relationship_label: 'Harlem Property Management, Inc. manages building 141 WEST 123 STREET',
          relationship: {
            manager_name: 'Harlem Property Management, Inc.',
            address: '141 WEST 123 STREET',
            bbl: '1019080014',
            relationship_label: 'Harlem Property Management, Inc. manages building 141 WEST 123 STREET',
          },
          can_become: 'strict_manager_source_gap_after_operator_seed',
          evidence_need: 'Acquire one exact non-HPD manager-proof source family.',
          suggested_source_families: ['company_website', 'outreach_confirmed'],
          recording_ready: false,
          approval_required_before_recording: true,
          reviewed_source_history_status: 'reviewed_dead_end_no_recording_ready_source',
          reviewed_source_findings: [{
            source_family: 'public_web_search_followup_hpm_batch_20_2026_05_16',
            qualification: 'This adds no strict evidence template and does not change source-ready or verified counts.',
          }],
        }, {
          request_type: 'operator_source_acquisition',
          candidate_id: 'operator-md-220',
          relationship_label: 'MD Squared Property Group manages building 220 3 AVENUE',
          relationship: {
            manager_name: 'MD Squared Property Group',
            address: '220 3 AVENUE',
            bbl: '1008747504',
            relationship_label: 'MD Squared Property Group manages building 220 3 AVENUE',
          },
          can_become: 'strict_manager_source_gap_after_operator_seed',
          evidence_need: 'Acquire one exact non-HPD manager-proof source family.',
          suggested_source_families: ['company_website', 'ny_dos'],
          recording_ready: false,
          approval_required_before_recording: true,
          reviewed_source_history_status: 'reviewed_dead_end_no_recording_ready_source',
          reviewed_source_findings: [{
            source_family: 'public_web_search_followup_md_squared_batch_18_2026_05_16',
            qualification: 'No real HPD ManagementCompany row or exact non-HPD manager-proof source was found.',
          }],
        }],
        requests: [{
          request_type: 'source_ready_below_verified',
          relationship_label: 'Daisy Management manages building 9 PROSPECT PARK WEST',
          can_become: 'verified_candidate_after_real_evidence_preview_recording_and_adjudication',
          evidence_need: 'Acquire stronger exact-property, role-specific management evidence before verification.',
          required_sources: ['hpd_management_company', 'company_website', 'outreach_confirmed'],
          required_real_evidence: [{
            suggested_source: 'hpd_management_company',
            required_fields: ['exact_property_match', 'role_specific_management_support'],
            acquisition_mode: 'official_hpd_query_packet_only',
            source_dataset_ids: ['tesw-yqqr', 'feu5-w2e2'],
            official_query_urls: {
              registrations_api: 'https://data.cityofnewyork.us/resource/tesw-yqqr.json?$where=boroid%3D3%20AND%20block%3D1068%20AND%20lot%3D37',
              contacts_api_template: 'https://data.cityofnewyork.us/resource/feu5-w2e2.json?$where=registrationid={registration_id}',
            },
            read_only_preview_command: '.\\.venv-x64\\Scripts\\python.exe scripts\\truth_live_hpd_role_audit.py --bbl 3010680037 --expected-agent "DAISY MANAGEMENT" --expected-manager "Daisy Management" --no-include-operator-seeds --no-include-hpm-nonstrict --query-packet-only --indent 2',
          }],
          reviewed_source_history_status: 'reviewed_dead_end_no_recording_ready_source',
          reviewed_source_findings: [{
            source_family: 'public_web_search_followup_md_squared_batch_18_2026_05_16',
            qualification: 'No real HPD ManagementCompany row or exact non-HPD manager-proof source was found.',
          }],
        }, {
          request_type: 'manager_source_acquisition',
          candidate_id: 'hpm-141-w-123',
          relationship_label: 'Harlem Property Management, Inc. manages building 141 WEST 123 STREET',
          can_become: 'strict_manager_source_gap_after_operator_seed',
          evidence_need: 'Acquire one exact non-HPD manager-proof source family.',
          suggested_source_families: ['company_website', 'outreach_confirmed'],
          reviewed_source_history_status: 'reviewed_dead_end_no_recording_ready_source',
          reviewed_source_findings: [{
            source_family: 'public_web_search_followup_hpm_batch_20_2026_05_16',
            qualification: 'This adds no strict evidence template and does not change source-ready or verified counts.',
          }],
        }, {
          request_type: 'operator_source_acquisition',
          candidate_id: 'operator-md-220',
          relationship_label: 'MD Squared Property Group manages building 220 3 AVENUE',
          can_become: 'strict_manager_source_gap_after_operator_seed',
          evidence_need: 'Acquire one exact non-HPD manager-proof source family.',
          suggested_source_families: ['company_website', 'ny_dos'],
          reviewed_source_history_status: 'reviewed_dead_end_no_recording_ready_source',
          reviewed_source_findings: [{
            source_family: 'public_web_search_followup_md_squared_batch_18_2026_05_16',
            qualification: 'No real HPD ManagementCompany row or exact non-HPD manager-proof source was found.',
          }],
        }],
        policy: {
          role_policy: 'Agent is not manager.',
        },
        safe_action: 'Use these requests to acquire and review real evidence only.',
      },
      safe_action: 'Use this frontier for evidence acquisition and review planning only. It is read-only, does not mark claims verified, does not record evidence, and does not permit business use.',
      next_required_action: 'Acquire exact-property, role-explicit evidence bundles.',
    });
    fetchTruthHealthReportMock.mockResolvedValue({
      dry_run: true,
      mutations_planned: 0,
      summary: {
        claim_count: 0,
        verified_claim_count: 0,
        conflicting_claim_count: 0,
        conflicting_claim_ratio: 0,
        open_review_count: 0,
        open_review_ratio: 0,
        planned_claims_total: 0,
        validation_check_count: 0,
        configured_golden_cases: 0,
        verification_candidate_count: 0,
        critical_or_high_gap_count: 1,
        trust_posture: 'not_ready',
      },
      trust_gaps: [{
        severity: 'critical',
        area: 'schema_readiness',
        message: 'Truth health report could not run because the database schema is missing required truth-confidence tables or columns.',
        evidence: { missing_tables: ['truth_claims'] },
      }],
      activation_checklist: [
        {
          step: 'apply_truth_schema',
          status: 'approval_required',
          reason: 'Apply additive migration 010_truth_manifest before claim-ledger materialization or review execution.',
          approval_required: true,
          mutations_planned: 7,
        },
        {
          step: 'run_materialization_dry_run',
          status: 'blocked',
          reason: 'Blocked until the truth-confidence schema exists.',
          approval_required: false,
          mutations_planned: 0,
        },
        {
          step: 'allow_business_use',
          status: 'blocked',
          reason: 'Do not use for sourcing, diligence, or outreach decisions until schema, claims, sources, reviews, and benchmarks clear the trust gates.',
          approval_required: false,
          mutations_planned: 0,
        },
      ],
      schema_status: {
        ready: false,
        expected_revision: '010_truth_manifest',
        current_revision: '008_lead_lineage',
        expected_revision_applied: false,
        truth_tables_ready: false,
        revision_status: 'schema_missing',
        missing_tables: ['truth_claims', 'truth_evidence'],
        mutations_planned: 0,
      },
      source_audit: {
        dry_run: true,
        mutations_planned: 0,
        summary: {
          total_sources: 15,
          operational: 0,
          no_recent_ingest: 10,
          not_wired: 0,
          schema_missing: 0,
          stale_ingest: 5,
        },
        critical_gaps: [
          { source_name: 'acris_transactions', status: 'stale_ingest' },
          { source_name: 'dob_permits', status: 'no_recent_ingest' },
        ],
        sources: [],
        refresh_plan: {
          dry_run: true,
          mutations_planned: 0,
          approval_required: true,
          safe_to_run_automatically: false,
          summary: {
            planned_job_count: 2,
            refreshable_job_count: 2,
            blocked_job_count: 0,
            affected_source_count: 2,
            non_refreshable_gap_count: 1,
          },
          items: [],
          rollback_strategy: 'Plan is read-only.',
        },
      },
      source_refresh_plan: {
        dry_run: true,
        mutations_planned: 0,
        approval_required: true,
        safe_to_run_automatically: false,
        summary: {
          planned_job_count: 2,
          refreshable_job_count: 2,
          blocked_job_count: 0,
          affected_source_count: 2,
          non_refreshable_gap_count: 1,
        },
        items: [],
        rollback_strategy: 'Plan is read-only.',
      },
    });
    fetchGoldenBenchmarkMock.mockResolvedValue(null);
    fetchTruthReviewQueueMock.mockResolvedValue({ items: [], limit: 5, offset: 0, source: 'truth' });
    fetchTruthValidationPreviewMock.mockResolvedValue({
      generated_at: '2026-05-14T00:00:00Z',
      dry_run: true,
      mutations_planned: 0,
      summary: { trust_posture: 'not_ready' },
      schema_status: { ready: false },
    });
    fetchTruthMaterializationPreviewMock.mockResolvedValue({
      dry_run: true,
      run_type: 'truth_claim_materialization',
      supported_sources: ['building_management'],
      selected_sources: ['building_management'],
      source_filter_applied: false,
      limit: 5,
      planned_claims_by_source: { building_management: 1 },
      planned_claims_total: 1,
      candidate_claims_by_source: { hpd_contact_role_links: 0, hpd_contact_management_links: 0 },
      strict_materializable_claims_by_source: { hpd_contact_role_links: 20 },
      strict_materializable_claims_by_predicate: { registered_agent_for_building: 20 },
      existing_claim_count: 0,
      existing_evidence_count: 0,
      mutations_planned: 0,
      rollback_strategy: 'Preview mode makes no changes.',
      sample_strict_hpd_role_link_claim_specs: [],
      sample_materialized_claim_specs: [{
        claim_id: 'claim-1',
        evidence_id: 'evidence-1',
        subject_type: 'lead',
        subject_id: 'lead-1',
        predicate: 'manages_building',
        object_type: 'building',
        object_id: '1000000001',
        normalized_value: 'manager',
        claim_type: 'building_management',
        belief_status: 'likely',
        confidence_score: 0.707,
        freshness_days: 13,
        actionability_level: 'automated_enrichment',
        source_name: 'building_management',
        source_type: 'derived_hpd_registration_link',
        source_record_id: 'building_management:249',
        observed_at: '2026-04-30T14:13:07.821864+00:00',
        support_status: 'supports',
        source_quality_score: 0.78,
      }],
    });
    fetchTruthAdjudicationPreviewMock.mockResolvedValue({
      dry_run: true,
      mutations_planned: 0,
      limit: 20,
      fact_group_count: 20,
      verification_candidate_count: 0,
      status_counts: { likely: 20 },
      recommended_queue_counts: { insufficient_evidence: 20 },
      blocker_counts: {
        needs_independent_source: 20,
        needs_additional_evidence: 20,
        confidence_below_verified_threshold: 4,
      },
      source_coverage: {
        sampled_fact_group_count: 20,
        zero_source_fact_group_count: 0,
        single_source_fact_group_count: 20,
        multi_source_fact_group_count: 0,
        max_supporting_source_count: 1,
        max_supporting_evidence_count: 1,
        source_count_distribution: { 1: 20 },
        top_sources: [{ source_name: 'building_management', fact_group_count: 20 }],
        verification_blocker: 'No sampled fact group has independent supporting sources.',
      },
      ledger_source_overlap: {
        dry_run: true,
        mutations_planned: 0,
        total_fact_group_count: 2063,
        zero_source_fact_group_count: 0,
        single_source_fact_group_count: 2063,
        multi_source_fact_group_count: 0,
        source_ready_fact_group_count: 0,
        max_supporting_source_count: 1,
        max_supporting_evidence_count: 1,
        top_sources: [
          { source_name: 'hpd_contacts', fact_group_count: 981 },
          { source_name: 'building_management', fact_group_count: 82 },
        ],
        business_readiness_blocker: 'No current ledger fact groups have enough independent supporting sources and evidence for adjudication.',
      },
      role_source_overlap_pilot: {
        dry_run: true,
        mutations_planned: 0,
        lead_id: '0ff794d3ba2d',
        limit: 20,
        scope_relationship_count: 82,
        sampled_relationship_count: 20,
        multi_source_if_materialized_count: 20,
        source_ready_if_materialized_count: 20,
        management_source_ready_if_materialized_count: 0,
        registered_agent_source_ready_if_materialized_count: 20,
        claim_count_by_predicate_if_materialized: { registered_agent_for_building: 82 },
        identity_policy: {
          strict_key_example: 'HARLEM PROPERTY MANAGEMENT',
          broad_dedupe_key_example: 'HARLEM',
          warning: "Broad dedupe key collapses HARLEM PROPERTY MANAGEMENT to 'HARLEM'; verification uses 'HARLEM PROPERTY MANAGEMENT' so HARLEM-only matches cannot verify a role.",
        },
        business_readiness_note: 'Pilot found role-aligned registered-agent overlap, but no management-company overlap.',
        samples: [{
          fact_key: {
            subject_type: 'lead',
            subject_id: '0ff794d3ba2d',
            predicate: 'registered_agent_for_building',
            object_type: 'building',
            object_id: '1018250029',
            normalized_value: 'registered_agent',
            claim_type: 'registered_agent',
          },
          building_management_role: 'agent',
          lead_verification_keys: ['HARLEM PROPERTY MANAGEMENT'],
          lead_broad_dedupe_keys: ['HARLEM'],
          supporting_sources_if_materialized: ['building_management', 'hpd_contacts'],
          supporting_source_count_if_materialized: 2,
          source_ready_if_materialized: true,
          safe_action: 'Use as registered-agent/legal verification only; do not treat as operating-manager proof.',
          matched_role_contacts: [{ contact_type: 'Agent' }],
          adjacent_role_contacts: [],
          blocked_contact_count: 0,
        }],
      },
      role_claim_correction_preview: {
        dry_run: true,
        mutations_planned: 0,
        lead_id: '0ff794d3ba2d',
        sampled_stale_claim_count: 1,
        requires_operator_approval: true,
        business_readiness_note: 'Stale Agent-as-manager claims must be deactivated or superseded before management facts are safe for use.',
        safe_action: 'Preview only. Do not execute correction without explicit dry_run=false and confirm_execute=true approval.',
        samples: [{
          claim_id: 'stale-agent-claim',
          fact_key: {
            subject_type: 'lead',
            subject_id: '0ff794d3ba2d',
            predicate: 'manages_building',
            object_type: 'building',
            object_id: '1018250029',
            normalized_value: 'agent',
            claim_type: 'building_management',
          },
          belief_status: 'likely',
          confidence_score: 0.72,
          actionability_level: 'automated_enrichment',
          evidence_ids: ['stale-evidence'],
          source_names: ['building_management'],
          source_record_ids: ['building_management:99'],
          source_roles: ['agent'],
          recommended_change: { operation: 'set_current_flag_false' },
        }],
      },
      manager_source_bridge_preview: {
        dry_run: true,
        mutations_planned: 0,
        lead_id: '0ff794d3ba2d',
        relationship_count: 82,
        role_counts: { pilot_current: { agent: 82 } },
        source_counts: {
          dos_cache_records: 0,
          company_website_records: 0,
          outreach_event_records: 0,
          outreach_confirmed_manager_events: 0,
        },
        registered_agent_bridge_count: 82,
        current_manager_role_relationship_count: 0,
        hpd_management_company_strict_match_count: 0,
        hpd_site_manager_row_count: 81,
        hpd_site_manager_strict_identity_match_count: 0,
        manager_source_ready_if_materialized_count: 0,
        blocking_reasons: [
          'current_building_management_rows_are_not_manager_role',
          'no_strict_hpd_management_company_matches',
          'no_local_company_website_evidence',
        ],
        business_readiness_note: 'Local evidence can support registered-agent overlap, but it cannot yet verify operating-manager facts.',
        safe_action: 'Collect a manager-specific independent source before marking manager facts verified.',
        samples: [{
          bbl: '1018250029',
          building_management_role: 'agent',
          contact_type: 'SiteManager',
          display_name: 'JAMES SIMARI',
          verification_key: 'JAMES SIMARI',
          strict_identity_matches_lead: false,
          role_matches_building_management: false,
          hpd_predicate: 'site_manager_for_building',
          safe_action: 'SiteManager is person/site evidence; it does not verify the company as operating manager.',
        }],
      },
      manager_external_source_acquisition_preview: {
        dry_run: true,
        mutations_planned: 0,
        lead_id: '0ff794d3ba2d',
        candidate_source_count: 30,
        matched_evidence_candidate_count: 49,
        clean_exact_claim_count: 7,
        claim_group_count: 14,
        source_ready_if_recorded_count: 14,
        independent_source_ready_if_recorded_count: 14,
        strict_manager_source_ready_if_recorded_count: 13,
        excluded_manager_proof_source_families: ['hpd_registration_derived', 'nyc_dof_billing_record'],
        review_required_count: 28,
        unmatched_candidate_count: 3,
        new_relationship_candidate_count: 3,
        policy: {
          freshness_warning: 'Most public filings are older than the 120-day verified threshold; source overlap alone does not make the manager fact verified.',
        },
        manual_evidence_batch_preview: {
          dry_run: true,
          allowed_execute: false,
          template_count: 48,
          claim_group_count: 14,
          planned_upsert_count: 144,
          source_names: ['hpm_revenue_by_property_summary', 'justia', 'mystatemls', 'ny_dps_order_entry', 'openigloo', 'renthistory', 'renthop', 'verizon_order_entry_petition', 'zillow'],
          recommended_strict_manager_proof_batch: {
            dry_run: true,
            allowed_execute: false,
            template_count: 46,
            claim_group_count: 13,
            planned_upsert_count: 138,
            rollback_preview: {
              estimated_claim_count: 46,
              estimated_evidence_count: 46,
              estimated_confidence_snapshot_count: 46,
              estimated_manifest_entry_count: 138,
              note: 'Exact new-vs-existing rollback counts are produced by truth_manager_external_evidence_batch.py after checking the local ledger.',
            },
            source_names: ['hpm_revenue_by_property_summary', 'justia', 'mystatemls', 'ny_dps_order_entry', 'openigloo', 'renthistory', 'renthop', 'verizon_order_entry_petition', 'zillow'],
            source_families: ['external_web_profile', 'first_party_operator_document', 'hpd_registration_derived', 'litigation_records', 'ny_dps_order_entry', 'real_estate_listing'],
            manager_proof_source_families: ['external_web_profile', 'first_party_operator_document', 'litigation_records', 'ny_dps_order_entry', 'real_estate_listing'],
            command: 'python scripts/truth_manager_external_evidence_batch.py --strict-manager-proof-only --execute --confirm-execute --indent 2',
            safe_action: 'Recommended first approval packet: records only strict manager-proof groups, then requires adjudication, health, and completion-audit reruns.',
          },
          excluded_address_review_candidate_count: 1,
          required_execute_params: { execute: true, confirm_execute: true },
          command: 'python scripts/truth_manager_external_evidence_batch.py --execute --confirm-execute --indent 2',
          safe_action: 'Review the batch preview only. Recording requires explicit operator approval and must be followed by adjudication, health, and completion-audit reruns.',
        },
        new_relationship_candidates: [{
          candidate_id: 'ny-dps-verizon-402-w-153-petition',
          candidate_status: 'new_relationship_candidate',
          source_name: 'ny_dps_order_entry',
          source_type: 'ny_dps_hosted_verizon_petition',
          source_family: 'ny_dps_order_entry',
          source_record_id: 'ny-dps-22-402-w-153-petition',
          source_url: 'https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7B516B105C-8647-4BFE-AB6E-62263DA10207%7D',
          external_address: '402 WEST 153 STREET',
          local_address: '402 WEST 153 STREET',
          manager_name: 'Harlem Property Management, Inc.',
          manager_contact_name: 'James Simari',
          evidence_role: 'managing_agent',
          evidence_summary: 'Verizon order-of-entry petition states the owner and managing agent for 402 West 153rd Street.',
          local_building_match: {
            bbl: '1020670047',
            address: '402 WEST 153 STREET',
          },
          relationship_claim_preview: {
            object_id: '1020670047',
            predicate: 'manages_building',
          },
          safe_action: 'Review as a possible new relationship claim; do not count it as source overlap for an existing ledger fact until the relationship is separately approved and materialized.',
        }, {
          candidate_id: 'nyc-dof-275-greenwich-hpm-billing-record',
          candidate_status: 'new_relationship_candidate',
          source_name: 'nyc_dof_assessment',
          source_type: 'nyc_finance_tax_assessment',
          source_family: 'nyc_dof_billing_record',
          source_record_id: 'nyc-dof-1001327501-2026-2027-tentative-assessment',
          source_url: 'https://a836-pts-access.nyc.gov/care/datalets/datalet.aspx?pin=1001327501',
          external_address: '275 GREENWICH STREET',
          local_address: '269 GREENWICH STREET',
          manager_name: 'Harlem Property Management, Inc.',
          evidence_role: 'tax_billing_contact',
          evidence_summary: 'NYC Finance lists Harlem Property Management as billing name and address.',
          local_building_match: {
            bbl: '1001327501',
            address: '269 GREENWICH STREET',
          },
          relationship_claim_preview: {
            object_id: '1001327501',
            predicate: 'manages_building',
          },
          safe_action: 'Review as a possible new relationship claim; do not count it as source overlap for an existing ledger fact until the relationship is separately approved and materialized.',
        }],
        post_recording_simulation: {
          dry_run: true,
          mutations_planned: 0,
          template_count: 48,
          simulated_fact_group_count: 14,
          multi_source_fact_group_count: 14,
          source_ready_fact_group_count: 14,
          independent_source_ready_fact_group_count: 14,
          strict_manager_source_ready_fact_group_count: 13,
          excluded_manager_proof_source_families: ['hpd_registration_derived'],
          safe_to_mark_verified_count: 0,
          source_ready_count_by_predicate: { manages_building: 14 },
          safe_to_mark_verified_count_by_predicate: {},
          blocker_counts: { confidence_below_verified_threshold: 14 },
          safe_action: 'This is a no-write simulation. It proves source overlap if the batch is later recorded, but verification/business use still require actual recording and post-record adjudication.',
          samples: [],
        },
        next_source_batches: {
          dry_run: true,
          mutations_planned: 0,
          candidate_count: 1,
          suggested_source_family_counts: {
            first_party_operator_document: 1,
            ny_dps_order_entry: 1,
          },
          source_boundary_notes: [
            'RentHistory and HPD-registration-derived context can support review, but it is not counted as manager-proof independent evidence.',
            'NY DOS service-of-process or c/o mailing records are role-specific legal/mailing evidence unless the source explicitly states a property-management or managing-agent role.',
            'Company profile pages can prove Harlem Property Management is a property manager, but they do not support a specific building relationship unless they list the exact property.',
            'First-party HPM operating documents can be manager-proof evidence only when the row names the exact property and the operator confirms document provenance.',
          ],
          reviewed_source_findings: [
            {
              source_family: 'ny_dps_order_entry',
              finding: 'Exact manager/care-of public-utility order evidence exists for 324 EAST 112 STREET and 36 WEST 138 STREET.',
              qualification: 'Counts as manager-proof evidence when the order/notice names Harlem Property Management at the exact property.',
            },
            {
              source_family: 'company_website',
              finding: 'The current Harlem Property Management website proves HPM is an NYC condo/co-op property-management company, but does not list exact pilot buildings.',
              qualification: 'Useful for company-role context and outreach, but it cannot support a specific building relationship unless an exact property page is found.',
            },
            {
              source_family: 'renthistory',
              finding: 'RentHistory covers multiple exact pilot addresses but is derived from HPD registration context.',
              qualification: 'Supports review and broad source overlap, but remains excluded from strict manager-proof source-family counts.',
            },
            {
              source_family: 'first_party_operator_document',
              finding: "The operator-identified Google Drive sheet 'Revenue by Property - Summary' has exact property rows for 13 current HPM pilot buildings.",
              qualification: 'Row-level revenue amounts are intentionally not copied into truth templates, and recording still requires explicit approval.',
            },
            {
              source_family: 'real_estate_listing',
              finding: 'MyStateMLS listing 11146569 names Harlem Property Management as the HOA for 2257 Adam Clayton Powell Jr Blvd.',
              qualification: 'Counts only for the exact local 2257 Adam C Powell Boulevard relationship.',
            },
          ],
          proposals: [{
            bbl: '1019080014',
            address: '141 WEST 123 STREET',
            existing_manager_proof_source_families: ['external_web_profile'],
            missing_manager_proof_source_family_count: 1,
            suggested_source_families: ['first_party_operator_document', 'ny_dps_order_entry', 'company_website', 'outreach_confirmed', 'ny_dos'],
            search_queries: ['"Harlem Property Management" "141 West 123 Street"'],
            source_targets: [{
              source_family: 'ny_dps_order_entry',
              evidence_needed: 'Find an exact NY DPS/PSC order-entry petition, exhibit, or notice naming Harlem Property Management as managing company for 141 WEST 123 STREET.',
            }],
            source_boundary_notes: [
              'NY DOS service-of-process or c/o mailing records are role-specific legal/mailing evidence unless the source explicitly states a property-management or managing-agent role.',
            ],
            safe_action: 'Acquire one more non-HPD-derived manager-specific source before treating this group as strict manager-proof.',
          }],
          safe_action: 'Use this plan for the next source-acquisition pass only. Recording remains approval-gated through manual evidence capture.',
        },
        claim_groups: [{
          fact_key: {
            subject_type: 'lead',
            subject_id: '0ff794d3ba2d',
            predicate: 'manages_building',
            object_type: 'building',
            object_id: '1016837501',
            normalized_value: 'manager',
            claim_type: 'building_management',
          },
          address: '324 EAST 112 STREET',
          building_management_role: 'agent',
          supporting_sources_if_recorded: ['verizon_order_entry_petition', 'ny_dps_order_entry', 'renthistory'],
          supporting_source_families_if_recorded: ['ny_dps_order_entry', 'hpd_registration_derived'],
          manager_proof_source_families_if_recorded: ['ny_dps_order_entry'],
          supporting_source_count_if_recorded: 3,
          independent_source_family_count_if_recorded: 2,
          manager_proof_source_family_count_if_recorded: 1,
          source_ready_if_recorded: true,
          independent_source_ready_if_recorded: true,
          strict_manager_source_ready_if_recorded: false,
          evidence_candidate_ids: ['ny-dps-verizon-324-e-112-petition'],
          manual_evidence_templates: [{ source_name: 'verizon_order_entry_petition' }],
          safe_action: 'Preview and record only after operator review; rerun adjudication afterward. Do not treat current local Agent rows as manager support.',
        }],
        evidence_candidates: [{
          candidate_id: 'ny-dps-exhibit-202-w-140-address-range-review',
          candidate_status: 'address_range_review_required',
          source_name: 'ny_dps_order_entry',
          source_type: 'ny_dps_order_entry_exhibit',
          source_family: 'ny_dps_order_entry',
          source_record_id: 'ny-dps-9365475-202-w-140-exhibit',
          source_url: 'https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=example',
          observed_at: '2016-02-10T00:00:00+00:00',
          external_address: '202 WEST 140 STREET',
          local_match: { bbl: '1020257501', address: '204 WEST 140 STREET' },
          evidence_role: 'mdu_managing_agent_company',
          evidence_summary: 'Address range needs review.',
          manager_name: 'Harlem Property Management, Inc.',
          manager_contact_name: 'Jim Simari',
          clean_for_operator_review: false,
          independence_warning: null,
          manual_evidence_template: { source_name: 'ny_dps_order_entry' },
        }],
        unmatched_candidates: [],
      },
      operator_confirmed_management_preview: {
        dry_run: true,
        mutations_planned: 0,
        source_name: 'outreach_confirmed',
        source_type: 'operator_first_hand_confirmation',
        source_family: 'operator_confirmed',
        candidate_count: 4,
        matched_candidate_count: 4,
        unmatched_candidate_count: 0,
        new_relationship_candidate_count: 4,
        conflict_candidate_count: 0,
        operator_confirmation_template_count: 4,
        second_source_template_count: 6,
        manual_evidence_template_count: 10,
        contradiction_template_count: 0,
        planned_upsert_count: 30,
        source_ready_if_recorded_count: 4,
        independent_source_ready_if_recorded_count: 4,
        strict_manager_source_ready_if_recorded_count: 2,
        verified_safe_if_recorded_count: 0,
        policy: {
          single_source_policy: 'Operator-confirmed evidence is high-quality first-hand evidence, but a single source is not verified. Previewed second-source templates do not affect the ledger until explicitly recorded.',
          manager_proof_policy: 'RentHistory/HPD-registration-derived context can create broad source overlap but is excluded from strict manager-proof source-family counts.',
        },
        post_recording_simulation: {
          dry_run: true,
          mutations_planned: 0,
          template_count: 10,
          simulated_fact_group_count: 4,
          multi_source_fact_group_count: 4,
          source_ready_fact_group_count: 4,
          independent_source_ready_fact_group_count: 4,
          strict_manager_source_ready_fact_group_count: 2,
          safe_to_mark_verified_count: 0,
          blocker_counts: { confidence_below_verified_threshold: 4 },
        },
        second_source_seed_batches: {
          dry_run: true,
          mutations_planned: 0,
          candidate_count: 4,
          template_count: 6,
          source_ready_if_recorded_count: 4,
          strict_manager_source_ready_if_recorded_count: 2,
          proposals: [{
            bbl: '1008747504',
            address: '220 3 AVENUE',
            manager_lead_id: '56a71624c6c0',
            manager_name: 'MD Squared Property Group',
            existing_manager_proof_source_families: ['operator_confirmed'],
            existing_source_families_if_recorded: ['operator_confirmed', 'hpd_registration_derived'],
            supporting_sources_if_recorded: ['outreach_confirmed', 'renthistory'],
            source_ready_if_recorded: true,
            strict_manager_source_ready_if_recorded: false,
            verified_safe_if_recorded: false,
            second_source_templates: [{ source_name: 'renthistory' }],
            missing_manager_proof_source_family_count: 1,
            strict_manager_gap_status: 'broad_source_ready_not_strict',
            strict_manager_gap_reason: 'Broad source-ready only: the preview has multiple source families, but at least one second source is HPD-registration-derived and excluded from strict manager-proof counts.',
            next_required_manager_proof: 'Acquire one exact non-HPD manager-proof source family.',
            suggested_source_families: ['company_website', 'external_web_profile', 'hpd_management_company', 'ny_dos'],
            search_queries: ['"MD Squared" "220 3 Avenue"'],
            source_targets: [{
              source_family: 'company_website',
              evidence_needed: 'Find a MD Squared-controlled page that names 220 3 AVENUE.',
            }],
            safe_action: 'Review the attached second-source templates before recording.',
          }],
          source_boundary_notes: [
            'Operator-confirmed evidence is a first-hand source family, but it still needs an independent second family before any claim is source-ready.',
            'RentHistory/HPD-registration-derived pages can create broad source overlap, but they are excluded from strict manager-proof counts.',
          ],
          reviewed_source_findings: [{
            source_family: 'company_website',
            finding: 'MD Squared site proves the company provides NYC property management services.',
            qualification: 'It remains company-role context rather than building relationship proof until an exact managed-property page is found.',
          }, {
            source_family: 'real_estate_listing',
            finding: 'Homes.com names Daisy Property Management for 9 Prospect Park W.',
            qualification: 'This is the current strict manager-proof second source for the Daisy seed.',
          }],
          safe_action: 'Inspect exact-property second-source templates before recording. Source-ready preview counts are not ledger truth until approved and written.',
        },
        manual_evidence_templates: [{ source_name: 'outreach_confirmed' }, { source_name: 'renthistory' }],
        contradiction_templates: [],
        candidates: [{
          candidate_id: 'operator-confirmed-md-squared-220-3-ave',
          user_address: '220 Third Ave',
          manager_name_supplied: 'MD Squared',
          matched_building: { bbl: '1008747504', address: '220 3 AVENUE' },
          matched_lead: { lead_id: '56a71624c6c0', company_name: 'MD Squared Property Group' },
          current_building_management: [],
          current_truth_claims: [],
          conflicting_current_manager_count: 0,
          conflicting_truth_claim_count: 0,
          review_queue: 'new_relationship_review',
          supporting_sources_if_recorded: ['outreach_confirmed', 'renthistory'],
          supporting_source_families_if_recorded: ['operator_confirmed', 'hpd_registration_derived'],
          manager_proof_source_families_if_recorded: ['operator_confirmed'],
          source_ready_if_recorded: true,
          strict_manager_source_ready_if_recorded: false,
          strict_manager_gap_status: 'broad_source_ready_not_strict',
          strict_manager_gap_reason: 'Broad source-ready only: the preview has multiple source families, but at least one second source is HPD-registration-derived and excluded from strict manager-proof counts.',
          missing_manager_proof_source_family_count: 1,
          next_required_manager_proof: 'Acquire one exact non-HPD manager-proof source family.',
          verified_safe_if_recorded: false,
          manual_evidence_template: { source_name: 'outreach_confirmed' },
          second_source_templates: [{ source_name: 'renthistory' }],
          contradiction_templates: [],
          safe_action: 'Preview-only new manager relationship. Record only after explicit approval and adjudicate after recording.',
        }],
        unmatched_candidates: [],
        safe_action: 'Preview only. Do not execute/write these operator-confirmed facts without explicit post-boundary approval.',
      },
      verification_gap_plan: {
        dry_run: true,
        mutations_planned: 0,
        proposal_count: 1,
        policy: { min_independent_supporting_sources: 2 },
        proposals: [{
          fact_key: {
            subject_type: 'lead',
            subject_id: 'lead-1',
            predicate: 'manages_building',
            object_type: 'building',
            object_id: '1000000001',
            normalized_value: 'manager',
          },
          current_sources: ['building_management'],
          current_supporting_evidence_count: 1,
          missing_source_count: 1,
          missing_evidence_count: 1,
          suggested_sources: ['hpd_management_company', 'company_website', 'outreach_confirmed'],
          recommended_queue: 'needs_human_review',
          safe_action: 'Collect or record independent evidence before adjudication; do not mark verified from this proposal alone.',
          manual_evidence_template: { source_type: 'hpd_management_company' },
        }],
      },
      verified_confidence_gap_plan: {
        dry_run: true,
        mutations_planned: 0,
        proposal_count: 1,
        single_source_upgrade_would_verify_count: 0,
        best_single_source_upgrade_overall: {
          suggested_source: 'outreach_confirmed',
          simulated_supporting_source_name: 'outreach_confirmed',
          source_quality_score: 0.98,
          simulated_confidence_score: 0.893,
          score_gap_to_verified: 0.007,
          would_reach_verified_threshold: false,
        },
        bundle_upgrade_would_verify_count: 1,
        best_bundle_upgrade_overall: {
          suggested_sources: ['hpd_management_company', 'company_website', 'outreach_confirmed'],
          simulated_supporting_source_names: ['hpd_management_company', 'company_website', 'outreach_confirmed'],
          simulated_confidence_score: 0.906,
          score_gap_to_verified: 0,
          would_reach_verified_threshold: true,
        },
        policy: { verified_confidence_threshold: 0.9 },
        proposals: [{
          fact_key: {
            subject_type: 'lead',
            subject_id: '0ff794d3ba2d',
            predicate: 'manages_building',
            object_type: 'building',
            object_id: '1010460054',
            normalized_value: 'manager',
          },
          current_sources: ['hpm_revenue_by_property_summary', 'openigloo', 'renthistory'],
          supporting_source_count: 3,
          supporting_evidence_count: 3,
          recomputed_confidence_score: 0.808,
          verified_confidence_threshold: 0.9,
          score_gap_to_verified: 0.092,
          average_supporting_source_quality: 0.604,
          raw_confidence_before_smoothing: 0.787,
          source_quality_scores: [],
          suggested_quality_upgrade_sources: ['hpd_management_company', 'company_website', 'outreach_confirmed'],
          simulated_quality_upgrades: [
            {
              suggested_source: 'outreach_confirmed',
              simulated_supporting_source_name: 'outreach_confirmed',
              source_quality_score: 0.98,
              simulated_confidence_score: 0.893,
              score_gap_to_verified: 0.007,
              would_reach_verified_threshold: false,
              safe_action: 'This one-source upgrade would improve confidence but still leave the fact below verified.',
            },
            {
              suggested_source: 'hpd_management_company',
              simulated_supporting_source_name: 'hpd_management_company',
              source_quality_score: 0.86,
              simulated_confidence_score: 0.87,
              score_gap_to_verified: 0.03,
              would_reach_verified_threshold: false,
            },
          ],
          best_single_source_upgrade: {
            suggested_source: 'outreach_confirmed',
            simulated_supporting_source_name: 'outreach_confirmed',
            source_quality_score: 0.98,
            simulated_confidence_score: 0.893,
            score_gap_to_verified: 0.007,
            would_reach_verified_threshold: false,
          },
          simulated_quality_bundle_upgrade: {
            suggested_sources: ['hpd_management_company', 'company_website', 'outreach_confirmed'],
            simulated_supporting_source_names: ['hpd_management_company', 'company_website', 'outreach_confirmed'],
            simulated_confidence_score: 0.906,
            score_gap_to_verified: 0,
            would_reach_verified_threshold: true,
            acquisition_required: true,
            recording_ready: false,
            approval_required_before_recording: true,
            required_real_evidence: [{
              suggested_source: 'hpd_management_company',
              simulated_supporting_source_name: 'hpd_management_company',
              required_fields: ['source_record_id', 'source_url_or_local_record_reference'],
            }],
          },
          single_source_upgrade_would_verify: false,
          recommended_queue: 'needs_human_review',
          safe_action: 'This fact is source-ready but not verified. Acquire stronger role-explicit, fresh evidence before marking verified.',
          manual_evidence_template: { source_type: 'hpd_management_company' },
        }],
      },
      policy: {
        min_independent_supporting_sources: 2,
        min_supporting_evidence: 2,
        max_freshness_days: 120,
      },
      samples: [{
        fact_key: {
          subject_type: 'lead',
          subject_id: 'lead-1',
          predicate: 'manages_building',
          object_type: 'building',
          object_id: '1000000001',
          normalized_value: 'manager',
        },
        claim_count: 1,
        supporting_evidence_count: 1,
        contradicting_evidence_count: 0,
        supporting_sources: ['building_management'],
        contradicting_sources: [],
        proposed_confidence: 0.7,
        recomputed_confidence_score: 0.7,
        verified_confidence_threshold: 0.9,
        score_gap_to_verified: 0.2,
        confidence_rationale: {
          average_supporting_source_quality: 0.78,
          raw_confidence_before_smoothing: 0.68,
        },
        proposed_belief_status: 'likely',
        recommended_queue: 'insufficient_evidence',
        safe_to_mark_verified: false,
        blockers: ['needs_independent_source', 'needs_additional_evidence'],
      }],
    });
    previewTruthManualEvidenceMock.mockResolvedValue({
      run_type: 'manual_evidence_capture',
      run_id: 'manual-preview',
      dry_run: true,
      mutations_planned: 3,
      allowed_execute: false,
      claim_spec: {
        claim_id: 'claim-manual',
        evidence_id: 'evidence-manual',
        predicate: 'manages_building',
        confidence_score: 0.723,
        support_status: 'supports',
      },
      rollback_strategy: 'New rows are safe to delete by ID in dependency order.',
    });
    previewTruthRoleClaimCorrectionMock.mockResolvedValue({
      run_type: 'truth_role_claim_correction',
      dry_run: true,
      mutations_planned: 50,
      allowed_execute: false,
      rollback_strategy: 'Role correction preview only.',
    });
    fetchJobsMock.mockResolvedValue([]);
    fetchJobsSummaryMock.mockResolvedValue(null);
    startJobMock.mockResolvedValue({
      status: 'approval_required',
      job_type: 'building_coordinates',
      approval_required: true,
      dry_run: true,
      confirm_execute: false,
    });
  });

  it('shows schema-readiness trust posture when the truth dashboard is unavailable', async () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    render(
      <QueryClientProvider client={client}>
        <SettingsPage />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Data Health' }));

    expect(await screen.findByText('Trust posture')).toBeInTheDocument();
    expect(screen.getByText('not ready')).toBeInTheDocument();
    expect(screen.getByText('schema readiness')).toBeInTheDocument();
    expect(screen.getByText('Activation checklist')).toBeInTheDocument();
    expect(screen.getAllByText('apply truth schema').length).toBeGreaterThan(0);
    expect(screen.getByText(/before claim-ledger materialization/i)).toBeInTheDocument();
    expect(screen.getByText('mutations planned: 7')).toBeInTheDocument();
    expect(screen.getAllByText('approval required').length).toBeGreaterThan(0);
    expect(screen.getByText('allow business use')).toBeInTheDocument();
    expect(screen.getByText(/Do not use for sourcing, diligence, or outreach decisions/i)).toBeInTheDocument();
    expect(screen.getAllByText('blocked').length).toBeGreaterThan(0);
    expect(screen.getByText(/database schema is missing required truth-confidence tables/i)).toBeInTheDocument();
    expect(screen.getByText('008_lead_lineage')).toBeInTheDocument();
    expect(screen.getByText('010_truth_manifest')).toBeInTheDocument();
    expect(screen.getByText('schema missing')).toBeInTheDocument();
    expect(screen.getByText('Missing tables: truth_claims, truth_evidence')).toBeInTheDocument();
    expect(screen.getByText('0 of 15 sources operational')).toBeInTheDocument();
    expect(screen.getByText('Stale')).toBeInTheDocument();
    expect(screen.getByText('No ingest')).toBeInTheDocument();
    expect(screen.getByText('Needs attention: acris_transactions, dob_permits')).toBeInTheDocument();
    expect(screen.getByText('Refresh plan: 2 refreshable jobs, 0 blocked, 1 tracked manually.')).toBeInTheDocument();
    expect(screen.getByText('Activation packet')).toBeInTheDocument();
    expect(screen.getByText('schema approval required')).toBeInTheDocument();
    expect(screen.getByText(/Business use blocked; 1 approval step/i)).toBeInTheDocument();
    expect(screen.getByText('review preflight sql')).toBeInTheDocument();
    expect(screen.getAllByText('apply truth schema').length).toBeGreaterThan(1);
    expect(screen.getByText(/python -m alembic upgrade 010_truth_manifest/i)).toBeInTheDocument();
    expect(screen.getByText('Ledger readiness')).toBeInTheDocument();
    expect(screen.getAllByText('Claims').length).toBeGreaterThan(1);
    expect(screen.getAllByText('Verified').length).toBeGreaterThan(1);
    expect(screen.getByText('Critical/high gaps')).toBeInTheDocument();
    expect(screen.getByText('Activation frontier')).toBeInTheDocument();
    expect(screen.getByText('Below verified')).toBeInTheDocument();
    expect(screen.getByText('Single-source gaps')).toBeInTheDocument();
    expect(screen.getByText('one-source clears: 2')).toBeInTheDocument();
    expect(screen.getByText('bundle clears: 10')).toBeInTheDocument();
    expect(screen.getByText('HPM seeds: 1')).toBeInTheDocument();
    expect(screen.getByText('operator seeds: 4')).toBeInTheDocument();
    expect(screen.getByText(/No facts are verified or eligible for verified adjudication yet/i)).toBeInTheDocument();
    expect(screen.getByText(/truth_verification_frontier.py --limit 10 --indent 2/i)).toBeInTheDocument();
    expect(screen.getByText(/Rollback preview: python -m alembic downgrade/i)).toBeInTheDocument();
    expect(screen.getByText('Next source refresh jobs')).toBeInTheDocument();
    expect(screen.getByText('acris (approval): acris transactions')).toBeInTheDocument();
    expect(screen.getByText('dob permits (blocked): dob permits')).toBeInTheDocument();
    expect(screen.getByText('Completion audit')).toBeInTheDocument();
    expect(screen.getByText('not complete')).toBeInTheDocument();
    expect(screen.getByText(/25 of 25 artifacts; 3 runtime blockers/i)).toBeInTheDocument();
    expect(screen.getByText('satisfied: 2')).toBeInTheDocument();
    expect(screen.getByText('blocked: 1')).toBeInTheDocument();
  });

  it('keeps Data Health mounted when truth preview packets omit optional arrays', async () => {
    fetchTruthCompletionAuditMock.mockResolvedValue({
      dry_run: true,
      mutations_planned: 0,
      completion_status: 'not_complete',
      prompt_to_artifact_checklist: [],
    });
    fetchTruthAdjudicationPreviewMock.mockResolvedValue({
      manager_source_bridge_preview: {
        dry_run: true,
        mutations_planned: 0,
        lead_id: '0ff794d3ba2d',
        relationship_count: 1,
        current_manager_role_relationship_count: 0,
        hpd_management_company_strict_match_count: 0,
        manager_source_ready_if_materialized_count: 0,
        registered_agent_bridge_count: 1,
        hpd_site_manager_row_count: 1,
        samples: [],
      },
      manager_external_source_acquisition_preview: {
        dry_run: true,
        mutations_planned: 0,
        lead_id: '0ff794d3ba2d',
        candidate_source_count: 1,
        matched_evidence_candidate_count: 1,
        clean_exact_claim_count: 0,
        claim_group_count: 0,
        source_ready_if_recorded_count: 0,
        independent_source_ready_if_recorded_count: 0,
        strict_manager_source_ready_if_recorded_count: 0,
        review_required_count: 0,
        unmatched_candidate_count: 0,
        new_relationship_candidate_count: 0,
        post_recording_simulation: {
          simulated_fact_group_count: 0,
          multi_source_fact_group_count: 0,
          source_ready_fact_group_count: 0,
          safe_to_mark_verified_count: 0,
        },
        next_source_batches: {
          candidate_count: 1,
        },
      },
    });

    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    render(
      <QueryClientProvider client={client}>
        <SettingsPage />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Data Health' }));

    expect(await screen.findByText('Data Health Dashboard')).toBeInTheDocument();
    await waitFor(() => expect(fetchTruthCompletionAuditMock).toHaveBeenCalled());
    await waitFor(() => expect(fetchTruthAdjudicationPreviewMock).toHaveBeenCalled());
    await waitFor(() => {
      expect(screen.queryByText('Something went wrong loading this page.')).not.toBeInTheDocument();
    });
  });

  it('shows review evidence and contradiction context for human decisions', async () => {
    fetchTruthReviewQueueMock.mockResolvedValue({
      limit: 5,
      offset: 0,
      source: 'truth',
      items: [{
        review_id: 'review-1',
        queue_name: 'conflicting_evidence',
        subject_type: 'lead',
        subject_id: 'lead-1',
        status: 'open',
        priority: 10,
        confidence_score: 0.82,
        actionability_level: 'recommended_outreach',
        proposed_change: { operation: 'mark_conflicted' },
        supporting_evidence: { sources: ['hpd_contacts', 'company_website'] },
        contradicting_evidence: { sources: ['outreach_feedback'] },
        rationale: { reason: 'Outreach says this firm does not manage the building.' },
      }],
    });

    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    render(
      <QueryClientProvider client={client}>
        <SettingsPage />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Data Health' }));

    expect(await screen.findByText('Review queue')).toBeInTheDocument();
    expect(screen.getByText('conflicting evidence')).toBeInTheDocument();
    expect(screen.getByText(/confidence 82%/i)).toBeInTheDocument();
    expect(screen.getByText(/supports: hpd_contacts, company_website/i)).toBeInTheDocument();
    expect(screen.getByText(/contradicts: outreach_feedback/i)).toBeInTheDocument();
    expect(screen.getByText(/does not manage the building/i)).toBeInTheDocument();
  });

  it('shows materialization preview claim and evidence specs before execution', async () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    render(
      <QueryClientProvider client={client}>
        <SettingsPage />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Data Health' }));

    expect(await screen.findByText('Ledger backfill preview')).toBeInTheDocument();
    expect(screen.getByText('1 claims pending')).toBeInTheDocument();
    expect(screen.getAllByText('manages building').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/lead:lead-1 -> building:1000000001/i).length).toBeGreaterThan(0);
    expect(screen.getByText('source: building management')).toBeInTheDocument();
    expect(screen.getByText('confidence 71%')).toBeInTheDocument();
    expect(screen.getByText('13d freshness')).toBeInTheDocument();
    expect(screen.getByText('automated enrichment')).toBeInTheDocument();
    expect(screen.getByText(/claim-1 \/ evidence-1 \/ building_management:249/i)).toBeInTheDocument();
    expect(screen.getByText(/Preview mode makes no changes/i)).toBeInTheDocument();
  });

  it('shows claim adjudication blockers before claims can be verified', async () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    render(
      <QueryClientProvider client={client}>
        <SettingsPage />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Data Health' }));

    expect(await screen.findByText('Claim adjudication')).toBeInTheDocument();
    expect(screen.getByText('0 verification candidates')).toBeInTheDocument();
    expect(screen.getByText(/20 fact groups sampled/i)).toBeInTheDocument();
    expect(screen.getByText('needs independent source: 20')).toBeInTheDocument();
    expect(screen.getByText('needs additional evidence: 20')).toBeInTheDocument();
    expect(screen.getByText('confidence below verified threshold: 4')).toBeInTheDocument();
    expect(screen.getByText('Ledger facts')).toBeInTheDocument();
    expect(screen.getByText('Ledger multi-source')).toBeInTheDocument();
    expect(screen.getAllByText('Source-ready').length).toBeGreaterThan(0);
    expect(screen.getByText('No current ledger fact groups have enough independent supporting sources and evidence for adjudication.')).toBeInTheDocument();
    expect(screen.getByText('hpd contacts: 981')).toBeInTheDocument();
    expect(screen.getByText('Role pilot')).toBeInTheDocument();
    expect(screen.getAllByText('82').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('20 source-ready')).toBeInTheDocument();
    expect(screen.getByText('registered agent for building: 82')).toBeInTheDocument();
    expect(screen.getAllByText('Manager-ready').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Agent-ready')).toBeInTheDocument();
    expect(screen.getByText('Pilot found role-aligned registered-agent overlap, but no management-company overlap.')).toBeInTheDocument();
    expect(screen.getByText(/HARLEM-only matches cannot verify a role/i)).toBeInTheDocument();
    expect(screen.getByText(/registered agent for building - 1018250029/i)).toBeInTheDocument();
    expect(screen.getByText('Manager bridge')).toBeInTheDocument();
    expect(screen.getByText('relationships checked')).toBeInTheDocument();
    expect(screen.getByText(/cannot yet verify operating-manager facts/i)).toBeInTheDocument();
    expect(screen.getByText('agent bridge: 82')).toBeInTheDocument();
    expect(screen.getByText('site manager rows: 81')).toBeInTheDocument();
    expect(screen.getByText(/current building management rows are not manager role/i)).toBeInTheDocument();
    expect(screen.getByText(/SiteManager: JAMES SIMARI/i)).toBeInTheDocument();
    expect(screen.getByText('External manager sources')).toBeInTheDocument();
    expect(screen.getByText('matched evidence candidates')).toBeInTheDocument();
    expect(screen.getByText('Clean exact claims')).toBeInTheDocument();
    expect(screen.getByText('Manager-proof overlap')).toBeInTheDocument();
    expect(screen.getByText('New relationship candidates')).toBeInTheDocument();
    expect(screen.getByText('402 WEST 153 STREET')).toBeInTheDocument();
    expect(screen.getByText('269 GREENWICH STREET')).toBeInTheDocument();
    expect(screen.getByText(/Source-backed new relationships are review leads, not current-ledger source overlap/i)).toBeInTheDocument();
    expect(screen.getAllByText('needs relationship review').length).toBeGreaterThan(0);
    expect(screen.getByText('review required: 28')).toBeInTheDocument();
    expect(screen.getByText(/older than the 120-day verified threshold/i)).toBeInTheDocument();
    expect(screen.getByText('324 EAST 112 STREET')).toBeInTheDocument();
    expect(screen.getAllByText('verizon order entry petition').length).toBeGreaterThan(0);
    expect(screen.getByText('source-ready after review')).toBeInTheDocument();
    expect(screen.getByText('Manual evidence batch')).toBeInTheDocument();
    expect(screen.getByText('48 templates / 144 planned upserts')).toBeInTheDocument();
    expect(screen.getByText(/14 source-ready claim groups; address-review excluded: 1/i)).toBeInTheDocument();
    expect(screen.getByText('Recommended strict packet')).toBeInTheDocument();
    expect(screen.getByText('46 templates / 138 planned upserts')).toBeInTheDocument();
    expect(screen.getByText(/13 manager-proof claim groups/i)).toBeInTheDocument();
    expect(screen.getByText('Manager-proof families')).toBeInTheDocument();
    expect(screen.getAllByText('ny dps order entry').length).toBeGreaterThan(0);
    expect(screen.getByText(/Source families reviewed: external web profile, first party operator document, hpd registration derived, litigation records, ny dps order entry, real estate listing/i)).toBeInTheDocument();
    expect(screen.getByText(/Rollback estimate: 46 claims \/ 46 evidence \/ 138 manifest entries/i)).toBeInTheDocument();
    expect(screen.getByText('Source-overlap approval packet')).toBeInTheDocument();
    expect(screen.getByText(/Current ledger: 13 multi-source \/ 13 source-ready/i)).toBeInTheDocument();
    expect(screen.getByText(/Source-overlap proof: satisfied; current 13 multi-source \/ 13 source-ready/i)).toBeInTheDocument();
    expect(screen.getByText(/Strict HPM packet: 46 templates \/ 138 planned upserts \/ 13 strict groups/i)).toBeInTheDocument();
    expect(screen.getByText('Strict HPM manager-proof families')).toBeInTheDocument();
    expect(screen.getByText('Approval effects')).toBeInTheDocument();
    expect(screen.getByText(/Would record 46 templates \/ 138 upserts; expected source-ready groups: 13; verified-safe: 0/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Marks verified: no \/ refreshes sources: no \/ materializes relationships: no/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/Included addresses: 324 EAST 112 STREET, 36 WEST 138 STREET, 204 WEST 140 STREET, 2257 ADAM C POWELL BOULEVARD/i)).toBeInTheDocument();
    expect(screen.getByText('Strict HPM mutation scope')).toBeInTheDocument();
    expect(screen.getAllByText(/Allowed tables: truth materialization manifest, truth claims, truth evidence, confidence snapshots/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/Marks verified: no \/ starts jobs: no \/ allows business use: no/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/Materializes current relationships: no/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/HPM broad-only gaps: 1 broad but not strict \/ 13 strict-ready/i)).toBeInTheDocument();
    expect(screen.getByText(/141 WEST 123 STREET: broad source ready not strict; missing 1 manager-proof family/i)).toBeInTheDocument();
    expect(screen.getByText(/Relationship-acquisition leads: 3 source-backed candidates; not current-ledger overlap/i)).toBeInTheDocument();
    expect(screen.getAllByText('ny dps order entry: 1').length).toBeGreaterThan(0);
    expect(screen.getByText(/402 WEST 153 STREET: ny dps order entry; review before relationship creation/i)).toBeInTheDocument();
    expect(screen.getByText(/Current rows: 0 building-management \/ 0 truth claims/i)).toBeInTheDocument();
    expect(screen.getByText(/Source-backed new relationship candidates are acquisition leads only/i)).toBeInTheDocument();
    expect(screen.getByText(/Strict operator packet: 6 templates \/ 18 planned upserts \/ 2 strict groups/i)).toBeInTheDocument();
    expect(screen.getByText('Operator recording status')).toBeInTheDocument();
    expect(screen.getByText(/truth ledger evidence already recorded/i)).toBeInTheDocument();
    expect(screen.getByText(/Rerun would create 0 new claims \/ 0 new evidence rows and update 6 existing claims \/ 6 existing evidence rows/i)).toBeInTheDocument();
    expect(screen.getByText('Operator approval effects')).toBeInTheDocument();
    expect(screen.getByText(/Would record 6 templates \/ 18 upserts; expected source-ready groups: 2; verified-safe: 0/i)).toBeInTheDocument();
    expect(screen.getByText(/Included operator addresses: 4 WEST 16 STREET, 9 PROSPECT PARK WEST/i)).toBeInTheDocument();
    expect(screen.getByText('Operator mutation scope')).toBeInTheDocument();
    expect(screen.getByText(/Operator broad-only gaps: 2 broad but not strict \/ 2 strict-ready/i)).toBeInTheDocument();
    expect(screen.getByText(/220 3 AVENUE: broad source ready not strict; missing 1 manager-proof family/i)).toBeInTheDocument();
    expect(screen.getByText(/Business use remains blocked until local truth health/i)).toBeInTheDocument();
    expect(screen.getByText('Post-recording proof')).toBeInTheDocument();
    expect(screen.getByText(/Current multi-source: 13 \/ current source-ready: 13 \/ verified single-source: 0/i)).toBeInTheDocument();
    expect(screen.getByText(/Post-recording source-overlap gate passed/i)).toBeInTheDocument();
    expect(screen.getAllByText('preview only').length).toBeGreaterThan(0);
    expect(screen.getByText('Post-record simulation')).toBeInTheDocument();
    expect(screen.getByText('14 fact groups')).toBeInTheDocument();
    expect(screen.getByText('Verified-safe')).toBeInTheDocument();
    expect(screen.getAllByText('Manager-proof').length).toBeGreaterThan(0);
    expect(screen.getByText('confidence below verified threshold: 14')).toBeInTheDocument();
    expect(screen.getByText('Next source batches')).toBeInTheDocument();
    expect(screen.getByText(/1 groups need one more manager-proof source/i)).toBeInTheDocument();
    expect(screen.getByText('first party operator document: 1')).toBeInTheDocument();
    expect(screen.getAllByText(/141 WEST 123 STREET/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/"Harlem Property Management" "141 West 123 Street"/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText('Reviewed source findings').length).toBeGreaterThan(0);
    expect(screen.getByText(/Useful for company-role context and outreach/i)).toBeInTheDocument();
    expect(screen.getByText(/service-of-process or c\/o mailing records are role-specific legal\/mailing evidence/i)).toBeInTheDocument();
    expect(screen.getByText('Needs address review')).toBeInTheDocument();
    expect(screen.getByText(/202 WEST 140 STREET/i)).toBeInTheDocument();
    expect(screen.getByText('Operator-confirmed')).toBeInTheDocument();
    expect(screen.getByText('matched facts')).toBeInTheDocument();
    expect(screen.getByText(/Operator-confirmed evidence is high-quality first-hand evidence/i)).toBeInTheDocument();
    expect(screen.getByText(/220 3 AVENUE - MD Squared Property Group/i)).toBeInTheDocument();
    expect(screen.getByText(/queue: new relationship review/i)).toBeInTheDocument();
    expect(screen.getByText('current links: 0 / truth claims: 0')).toBeInTheDocument();
    expect(screen.getByText('source-ready preview')).toBeInTheDocument();
    expect(screen.getAllByText(/broad source ready not strict/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/HPD-registration-derived and excluded from strict manager-proof counts/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText('renthistory').length).toBeGreaterThan(0);
    expect(screen.getByText('Second-source seeds')).toBeInTheDocument();
    expect(screen.getByText(/Source-ready preview counts are not ledger truth/i)).toBeInTheDocument();
    expect(screen.getAllByText(/"MD Squared" "220 3 Avenue"/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/next: Acquire one exact non-HPD manager-proof source family/i)).toBeInTheDocument();
    expect(screen.getByText(/Strict operator packet excludes 2 broad-only candidates/i)).toBeInTheDocument();
    expect(screen.getByText(/220 3 AVENUE: broad source ready not strict; next proof required/i)).toBeInTheDocument();
    expect(screen.getByText(/company-role context rather than building relationship proof/i)).toBeInTheDocument();
    expect(screen.getByText(/Operator-confirmed evidence is a first-hand source family/i)).toBeInTheDocument();
    expect(screen.getByText('Role correction preview')).toBeInTheDocument();
    expect(screen.getByText('1 stale Agent-as-manager claim')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Preview correction plan' }));
    await waitFor(() => expect(previewTruthRoleClaimCorrectionMock).toHaveBeenCalledWith({
      lead_id: '0ff794d3ba2d',
      limit: 100,
      dry_run: true,
      confirm_execute: false,
    }));
    expect(screen.getByText('Multi-source facts')).toBeInTheDocument();
    expect(screen.getByText('Single-source facts')).toBeInTheDocument();
    expect(screen.getByText('Max sources')).toBeInTheDocument();
    expect(screen.getByText(/No sampled fact group has independent supporting sources/i)).toBeInTheDocument();
    expect(screen.getByText('building management: 20')).toBeInTheDocument();
    expect(screen.getByText('Verification gap plan')).toBeInTheDocument();
    expect(screen.getByText('1 evidence acquisition proposal')).toBeInTheDocument();
    expect(screen.getByText('needs 1 source')).toBeInTheDocument();
    expect(screen.getByText('needs 1 evidence')).toBeInTheDocument();
    expect(screen.getAllByText('hpd management company').length).toBeGreaterThan(0);
    expect(screen.getByText(/do not mark verified from this proposal alone/i)).toBeInTheDocument();
    expect(screen.getByText('Verification frontier')).toBeInTheDocument();
    expect(screen.getByText(/15 source-ready \/ 0 verification candidates/i)).toBeInTheDocument();
    expect(screen.getByText(/One-source threshold clears: 2 \/ 10/i)).toBeInTheDocument();
    expect(screen.getByText(/HPM next-source seeds: 1 \/ operator second-source seeds: 4/i)).toBeInTheDocument();
    expect(screen.getByText(/Verification gate: blocked evidence acquisition required; record-ready 0 \/ acquisition-required 10 \/ required evidence 42/i)).toBeInTheDocument();
    expect(screen.getByText(/Evidence request packet: 15 requests; source-ready 10 \/ single-source 0 \/ source-acquisition 5; record-ready 0/i)).toBeInTheDocument();
    expect(screen.getByText(/Reviewed source history: 2 findings \/ reviewed dead end no recording ready source/i)).toBeInTheDocument();
    expect(screen.getByText(/source ready below verified \/ verified candidate after real evidence preview recording and adjudication/i)).toBeInTheDocument();
    expect(screen.getByText(/Acquire stronger exact-property, role-specific management evidence before verification/i)).toBeInTheDocument();
    expect(screen.getByText(/HPD query packet \/ tesw-yqqr \+ feu5-w2e2/i)).toBeInTheDocument();
    expect(screen.getByText(/truth_live_hpd_role_audit\.py --bbl 3010680037/i)).toBeInTheDocument();
    expect(screen.getByText(/manager source acquisition \/ strict manager source gap after operator seed/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Reviewed source history: public web search followup md squared batch 18 2026 05 16/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/No real HPD ManagementCompany row or exact non-HPD manager-proof source was found/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/MD Squared Property Group manages building 220 3 AVENUE/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText('Daisy Management manages building 9 PROSPECT PARK WEST').length).toBeGreaterThan(0);
    expect(screen.getByText(/BROOKLYN \/ BBL 3010680037 \/ 48 units/i)).toBeInTheDocument();
    expect(screen.getByText(/best hpd management company 90%/i)).toBeInTheDocument();
    expect(screen.getByText(/Required real evidence: hpd management company: exact property match, role specific management support/i)).toBeInTheDocument();
    expect(screen.getByText('HPM next source')).toBeInTheDocument();
    expect(screen.getAllByText(/141 WEST 123 STREET/i).length).toBeGreaterThan(0);
    expect(screen.getByText('Operator second source')).toBeInTheDocument();
    expect(screen.getAllByText(/220 3 AVENUE/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/current: 0 mgmt \/ 0 truth; ledger source-ready: no/i)).toBeInTheDocument();
    expect(screen.getByText('Source-acquisition worklist')).toBeInTheDocument();
    expect(screen.getByText(/5 work items \/ 5 HPD work items/i)).toBeInTheDocument();
    expect(screen.getByText(/Requests 15 \/ record-ready 0 \/ approval-required 15/i)).toBeInTheDocument();
    expect(screen.getByText(/Use this as a human\/source-acquisition checklist/i)).toBeInTheDocument();
    expect(screen.getByText(/Filled paste-back or HPD audit JSON can be checked through source-evidence intake preview/i)).toBeInTheDocument();
    expect(screen.getByText(/CSV paste-back:/i)).toBeInTheDocument();
    expect(screen.getByText(/truth_source_acquisition_worklist\.py --lead-id 0ff794d3ba2d --frontier-limit 10 --max-items 5 --csv-template/i)).toBeInTheDocument();
    expect(screen.getByText(/Official HPD fetch packet:/i)).toBeInTheDocument();
    expect(screen.getByText(/truth_source_acquisition_worklist\.py --lead-id 0ff794d3ba2d --frontier-limit 10 --max-items 5 --hpd-fetch-packet/i)).toBeInTheDocument();
    expect(screen.getByText(/Operator confirmation packet:/i)).toBeInTheDocument();
    expect(screen.getByText(/truth_source_acquisition_worklist\.py --lead-id 0ff794d3ba2d --frontier-limit 10 --max-items 5 --operator-confirmation-packet/i)).toBeInTheDocument();
    expect(screen.getAllByText(/truth_source_evidence_intake\.py --candidate-csv <filled-worklist\.csv> --recommended-scope-only --indent 2/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Batch replay from a reviewed preview requires --execute --confirm-execute --confirm-batch-execute/i)).toBeInTheDocument();
    expect(screen.getByText('Candidate JSON preview')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Preview blocker packet/i })).toBeInTheDocument();
    expect(screen.getByText(/Read-only: returns the approval packet, not a write/i)).toBeInTheDocument();
    expect(screen.getByText(/After any approved evidence recording, rerun adjudication, post-recording proof, truth health, and runtime audit; verification candidates may still remain zero/i)).toBeInTheDocument();
    expect(screen.getByText('priority 10')).toBeInTheDocument();
    expect(screen.getByText(/BBL 1008747504 \/ operator source acquisition/i)).toBeInTheDocument();
    expect(screen.getAllByText('outreach confirmed').length).toBeGreaterThan(0);
    expect(screen.getByText('Operator confirmation request')).toBeInTheDocument();
    expect(screen.getByText(/Can you independently confirm that MD Squared Property Group currently manages 220 3 AVENUE/i)).toBeInTheDocument();
    expect(screen.getByText(/Do not reuse the same first-hand note already in the ledger/i)).toBeInTheDocument();
    expect(screen.getByText(/source names a different current manager/i)).toBeInTheDocument();
    expect(screen.getByText(/route to review/i)).toBeInTheDocument();
    expect(screen.getByText('Read-only HPD packet')).toBeInTheDocument();
    expect(screen.getAllByText(/truth_live_hpd_role_audit\.py --bbl 1008747504/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Paste-back fields: relationship label, bbl, address, manager name, source family/i)).toBeInTheDocument();
    expect(screen.getByText(/Reviewed dead end: official hpd and public web refresh md daisy 2026 05 18/i)).toBeInTheDocument();
    expect(screen.getByText('Source-overlap blocker report')).toBeInTheDocument();
    expect(screen.getAllByText(/blocked evidence acquisition required/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Source-ready 15 \/ verification candidates 0 \/ record-ready 0/i)).toBeInTheDocument();
    expect(screen.getByText(/Requests 15 \/ reviewed source findings 75/i)).toBeInTheDocument();
    expect(screen.getByText(/Candidate preview: preview ready approval required/i)).toBeInTheDocument();
    expect(screen.getByText(/Replay payloads: 2 exact manual-evidence row\(s\)/i)).toBeInTheDocument();
    expect(screen.getByText(/Expected after recording: 2 first-source-only row\(s\), 0 source-ready row\(s\)/i)).toBeInTheDocument();
    expect(screen.getByText(/none of the listed rows would become multi-source\/source-ready immediately after recording/i)).toBeInTheDocument();
    expect(screen.getByText(/MD Squared Property Group \/ 1008747504 \/ outreach_confirmed/i)).toBeInTheDocument();
    expect(screen.getByText(/2 record-ready preview \/ 2 recommended/i)).toBeInTheDocument();
    expect(screen.getByText(/Execution still requires explicit approval/i)).toBeInTheDocument();
    expect(screen.getByText(/Approve recording 2 preview-clean new-supporting-source manual-evidence row\(s\) only/i)).toBeInTheDocument();
    expect(screen.getByText(/truth_manual_evidence\.py --payload-file <reviewed-preview\.json> --execute --confirm-execute --confirm-batch-execute/i)).toBeInTheDocument();
    expect(screen.getByText(/Use this packet as the human approval boundary/i)).toBeInTheDocument();
    expect(screen.getAllByText('MD Squared Property Group manages building 220 3 AVENUE').length).toBeGreaterThan(0);
    expect(screen.getByText(/verification candidate count=0/i)).toBeInTheDocument();
    expect(screen.getByText(/recording ready count=0/i)).toBeInTheDocument();
    const thresholdRelationshipSection = screen.getByText('Threshold-sensitive relationships').parentElement;
    expect(thresholdRelationshipSection).not.toBeNull();
    expect(within(thresholdRelationshipSection as HTMLElement).getByText(/could clear the verified threshold with one stronger exact role-specific source/i)).toBeInTheDocument();
    expect(within(thresholdRelationshipSection as HTMLElement).getByText('Daisy Management manages building 9 PROSPECT PARK WEST')).toBeInTheDocument();
    expect(within(thresholdRelationshipSection as HTMLElement).getByText(/Current 82% \/ best single source hpd management company to 90% \/ gap 8%/i)).toBeInTheDocument();
    expect(within(thresholdRelationshipSection as HTMLElement).getByText(/recording-ready: no \/ approval before recording: yes/i)).toBeInTheDocument();
    expect(within(thresholdRelationshipSection as HTMLElement).getByText(/reviewed: reviewed dead end no recording ready source \/ real evidence fields: 4/i)).toBeInTheDocument();
    expect(within(thresholdRelationshipSection as HTMLElement).getByText(/latest reviewed source: live hpd threshold candidate role audit 2026 06 01/i)).toBeInTheDocument();
    expect(screen.getByText(/proof only/i)).toBeInTheDocument();
    expect(screen.getByText(/Use this report to explain the current blocker/i)).toBeInTheDocument();
    expect(screen.getAllByText(/company website/i).length).toBeGreaterThan(0);
    expect(screen.getByText('Verified confidence gap')).toBeInTheDocument();
    expect(screen.getByText(/1 source-ready fact below verified threshold/i)).toBeInTheDocument();
    expect(screen.getByText(/One-source upgrades clearing threshold: 0 \/ 1/i)).toBeInTheDocument();
    expect(screen.getByText(/best 89%/i)).toBeInTheDocument();
    expect(screen.getByText(/Suggested bundles clearing threshold: 1 \/ 1/i)).toBeInTheDocument();
    expect(screen.getByText(/Suggested bundle: score 91%/i)).toBeInTheDocument();
    expect(screen.getAllByText(/acquisition required/i).length).toBeGreaterThan(0);
    expect(screen.getByText('avg quality 60%')).toBeInTheDocument();
    expect(screen.getByText(/Best one-source upgrade: outreach confirmed - score 89%/i)).toBeInTheDocument();
    expect(screen.getAllByText(/still below verified/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/hpd management company: 87% below/i)).toBeInTheDocument();
    expect(screen.getByText('queue: insufficient evidence')).toBeInTheDocument();
    expect(screen.getByText('gap to verified 20%')).toBeInTheDocument();
    expect(screen.getByText(/Avg source quality 78% \/ raw confidence 68%/i)).toBeInTheDocument();
    expect(screen.getByText(/Blocked by needs independent source, needs additional evidence/i)).toBeInTheDocument();
  });

  it('previews manual evidence capture with rollback context before execution', async () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    render(
      <QueryClientProvider client={client}>
        <SettingsPage />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Data Health' }));

    expect(await screen.findByText('Manual evidence preview')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Preview Manual Evidence' }));

    await waitFor(() => expect(previewTruthManualEvidenceMock).toHaveBeenCalledTimes(1));
    expect(previewTruthManualEvidenceMock).toHaveBeenCalledWith(expect.objectContaining({
      subject_type: 'lead',
      subject_id: 'lead-1',
      predicate: 'manages_building',
      object_type: 'building',
      object_id: '1000000001',
      claim_type: 'building_management',
      normalized_value: 'manager',
      support_status: 'supports',
      source_name: 'manual_evidence',
      source_type: 'operator_review',
    }));
    expect(await screen.findByText(/claim-manual \/ evidence-manual/i)).toBeInTheDocument();
    expect(screen.getAllByText('preview only').length).toBeGreaterThan(0);
    expect(screen.getByText('mutations planned: 3')).toBeInTheDocument();
    expect(screen.getByText('confidence 72%')).toBeInTheDocument();
    expect(screen.getByText(/Rollback: New rows are safe to delete by ID/i)).toBeInTheDocument();
  });

  it('shows structured actionability thresholds from the truth dashboard', async () => {
    fetchTruthDashboardMock.mockResolvedValue({
      claim_count: 12,
      verified_claim_count: 5,
      conflicting_claim_count: 1,
      recommended_outreach_claim_count: 2,
      open_review_count: 3,
      active_golden_case_count: 7,
      confidence_snapshot_count: 4,
      actionability_distribution: { acquisition_quality_diligence: 1 },
      review_queue_distribution: {},
      claim_type_distribution: {},
      actionability_rules: [{
        level: 'acquisition_quality_diligence',
        meaning: 'Strong enough to ground diligence.',
        minimum: 'confidence >= 0.90, at least two supporting sources and evidence items',
        minimum_score: 0.9,
        max_contradictions: 0,
        max_freshness_days: 60,
        min_supporting_sources: 2,
        min_supporting_evidence: 2,
      }],
    });

    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    render(
      <QueryClientProvider client={client}>
        <SettingsPage />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Data Health' }));

    expect(await screen.findByText('acquisition quality diligence')).toBeInTheDocument();
    expect(screen.getByText('90%+ confidence')).toBeInTheDocument();
    expect(screen.getByText('0 contradictions')).toBeInTheDocument();
    expect(screen.getByText('60d freshness')).toBeInTheDocument();
    expect(screen.getByText('2 sources')).toBeInTheDocument();
    expect(screen.getByText('2 evidence')).toBeInTheDocument();
  });

  it('shows schema revision drift as a lineage-review gate', async () => {
    fetchTruthHealthReportMock.mockResolvedValue({
      dry_run: true,
      mutations_planned: 0,
      summary: {
        claim_count: 0,
        verified_claim_count: 0,
        conflicting_claim_count: 0,
        conflicting_claim_ratio: 0,
        open_review_count: 0,
        open_review_ratio: 0,
        planned_claims_total: 0,
        validation_check_count: 0,
        configured_golden_cases: 7,
        critical_or_high_gap_count: 1,
        trust_posture: 'not_ready',
      },
      trust_gaps: [{
        severity: 'high',
        area: 'schema_revision',
        message: 'Truth-confidence tables are present, but the Alembic revision does not match the expected truth-confidence migration.',
        evidence: { current_revision: '010_future_revision' },
      }],
      activation_checklist: [
        {
          step: 'apply_truth_schema',
          status: 'approval_required',
          reason: 'Truth-confidence tables exist, but Alembic revision differs from the expected truth-confidence migration; inspect migration lineage before ledger previews or execution.',
          approval_required: true,
          mutations_planned: 0,
        },
        {
          step: 'run_materialization_dry_run',
          status: 'blocked',
          reason: 'Blocked until the truth-confidence schema exists at the expected migration.',
          approval_required: false,
          mutations_planned: 0,
        },
        {
          step: 'allow_business_use',
          status: 'blocked',
          reason: 'Do not use for sourcing, diligence, or outreach decisions until schema, claims, sources, reviews, and benchmarks clear the trust gates.',
          approval_required: false,
          mutations_planned: 0,
        },
      ],
      schema_status: {
        ready: true,
        expected_revision: '010_truth_manifest',
        current_revision: '010_future_revision',
        expected_revision_applied: false,
        truth_tables_ready: true,
        revision_status: 'schema_present_revision_differs',
        missing_tables: [],
        mutations_planned: 0,
      },
      source_audit: {
        dry_run: true,
        mutations_planned: 0,
        summary: {
          total_sources: 20,
          operational: 20,
          no_recent_ingest: 0,
          not_wired: 0,
          schema_missing: 0,
          stale_ingest: 0,
        },
        critical_gaps: [],
        sources: [],
      },
      source_refresh_plan: null,
    });

    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    render(
      <QueryClientProvider client={client}>
        <SettingsPage />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Data Health' }));

    expect(await screen.findByText('schema revision')).toBeInTheDocument();
    expect(screen.getByText('schema present revision differs')).toBeInTheDocument();
    expect(screen.getByText('010_future_revision')).toBeInTheDocument();
    expect(screen.getByText(/inspect migration lineage before ledger previews/i)).toBeInTheDocument();
    expect(screen.getAllByText('mutations planned: 0').length).toBeGreaterThan(0);
    expect(screen.getAllByText('approval required').length).toBeGreaterThan(0);
    expect(screen.getByText('run materialization dry run')).toBeInTheDocument();
    expect(screen.getAllByText('blocked').length).toBeGreaterThan(0);
  });

  it('keeps source refresh controls in preview mode by default', async () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    render(
      <QueryClientProvider client={client}>
        <SettingsPage />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Data Health' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Preview Coordinate Sync' }));

    await waitFor(() => expect(startJobMock).toHaveBeenCalled());
    expect(startJobMock.mock.calls[0][0]).toBe('building_coordinates');
  });
});
