import { beforeEach, describe, expect, it, vi } from 'vitest';

import { setToken } from './auth';
import {
  fetchGoldenBenchmark,
  fetchLeadTruthSummary,
  fetchSubjectTruthSummary,
  fetchTruthActivationPacket,
  fetchTruthAdjudicationPreview,
  fetchTruthCompletionAudit,
  fetchTruthDashboard,
  fetchTruthHealthReport,
  fetchTruthManagerSourceAcquisitionPacket,
  fetchTruthMaterializationPreview,
  fetchTruthReviewQueue,
  fetchTruthSourceAcquisitionWorklist,
  fetchTruthSourceOverlapBlockerReport,
  fetchTruthSourceOverlapApprovalPacket,
  fetchTruthSourceOverlapPostRecordingCheck,
  fetchTruthValidationPreview,
  fetchTruthVerificationFrontier,
  previewTruthAdjudicationApply,
  previewTruthManualEvidence,
  previewTruthRoleClaimCorrection,
  previewTruthSourceOverlapBlockerReport,
  previewTruthSourceEvidenceIntakeBatch,
  previewTruthSourceEvidenceIntake,
  submitTruthReviewDecision,
} from './truth-api';

describe('truth api service', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    setToken('test-token');
  });

  it('fetches lead truth summary with auth headers', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ lead_id: 'lead-1', claims: [] }), { status: 200 }),
    );

    const result = await fetchLeadTruthSummary('lead-1');

    expect(result.lead_id).toBe('lead-1');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/leads/lead-1/summary',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer test-token' }),
      }),
    );
  });

  it('fetches generic subject truth summaries for non-lead relationships', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ subject_type: 'building', subject_id: '1000000001', claims: [] }), { status: 200 }),
    );

    const result = await fetchSubjectTruthSummary('building', '1000000001');

    expect(result.subject_type).toBe('building');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/subjects/building/1000000001/summary',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer test-token' }),
      }),
    );
  });

  it('fetches internal HPD contact truth summaries when reviewing materialized contact claims', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ subject_type: 'hpd_contact', subject_id: '11', claims: [] }), { status: 200 }),
    );

    const result = await fetchSubjectTruthSummary('hpd_contact', '11');

    expect(result.subject_type).toBe('hpd_contact');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/subjects/hpd_contact/11/summary',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer test-token' }),
      }),
    );
  });

  it('fetches truth dashboard from the confidence program endpoint', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ claim_count: 0, actionability_rules: [] }), { status: 200 }),
    );

    const result = await fetchTruthDashboard();

    expect(result.claim_count).toBe(0);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/dashboard',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer test-token' }),
      }),
    );
  });

  it('fetches the completion audit prompt checklist', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        completion_status: 'not_complete',
        success_criteria: ['source overlap'],
        prompt_to_artifact_checklist: [{
          requirement: 'source overlap',
          status: 'runtime_not_checked',
          evidence: {},
        }],
        artifact_summary: { total: 25, satisfied: 25, missing: 0 },
        runtime_blockers: [{ gate: 'source_overlap_recording', reason: 'approval required' }],
      }), { status: 200 }),
    );

    const result = await fetchTruthCompletionAudit();

    expect(result.completion_status).toBe('not_complete');
    expect(result.prompt_to_artifact_checklist[0].status).toBe('runtime_not_checked');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/completion-audit',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer test-token' }),
      }),
    );
  });

  it('fetches the source-overlap approval packet', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        run_type: 'truth_source_overlap_approval_packet',
        current_ledger: { multi_source_fact_group_count: 0, source_ready_fact_group_count: 0 },
        recommended_first_packet: {
          template_count: 15,
          approval_decision_summary: {
            approval_required: true,
            recommended_execute_command: 'python scripts/truth_manager_external_evidence_batch.py --strict-manager-proof-only --execute --confirm-execute --indent 2',
            would_record_template_count: 15,
            would_record_claim_group_count: 4,
            would_plan_upsert_count: 45,
            expected_source_ready_fact_group_count: 4,
            expected_safe_to_mark_verified_count: 0,
            single_source_claims_stay_unverified: true,
            will_mark_verified: false,
            will_create_or_refresh_source_data: false,
            will_materialize_new_relationships: false,
          },
        },
        approval_required: true,
      }), { status: 200 }),
    );

    const result = await fetchTruthSourceOverlapApprovalPacket();

    expect(result.approval_required).toBe(true);
    expect(result.recommended_first_packet.template_count).toBe(15);
    expect(result.recommended_first_packet.approval_decision_summary?.will_mark_verified).toBe(false);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/source-overlap-approval-packet',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer test-token' }),
      }),
    );
  });

  it('fetches the post-recording source-overlap proof check', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        run_type: 'truth_source_overlap_post_recording_check',
        dry_run: true,
        mutations_planned: 0,
        post_recording_success: false,
        current_ledger: {
          total_fact_group_count: 2063,
          single_source_fact_group_count: 2063,
          multi_source_fact_group_count: 0,
          source_ready_fact_group_count: 0,
        },
        verified_single_source_policy: {
          verified_claim_count: 0,
          verified_single_source_claim_count: 0,
          sample_limit: 5,
          samples: [],
        },
        checks: [{ check: 'actual_current_ledger_multi_source', status: 'fail', observed: 0 }],
        safe_action: 'Do not treat previewed source overlap as actual ledger truth.',
      }), { status: 200 }),
    );

    const result = await fetchTruthSourceOverlapPostRecordingCheck();

    expect(result.post_recording_success).toBe(false);
    expect(result.current_ledger.multi_source_fact_group_count).toBe(0);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/source-overlap-post-recording-check',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer test-token' }),
      }),
    );
  });

  it('fetches the manager source-acquisition packet', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        run_type: 'manager_source_acquisition_packet',
        candidate_count: 10,
        source_ready_if_recorded_count: 14,
        strict_manager_source_ready_if_recorded_count: 4,
        verified_safe_if_recorded_count: 0,
        next_source_seed_count: 10,
      }), { status: 200 }),
    );

    const result = await fetchTruthManagerSourceAcquisitionPacket();

    expect(result.run_type).toBe('manager_source_acquisition_packet');
    expect(result.strict_manager_source_ready_if_recorded_count).toBe(4);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/manager-source-acquisition-packet',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer test-token' }),
      }),
    );
  });

  it('fetches the golden benchmark scoreboard', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ metrics: { precision: 1 }, cases: [] }), { status: 200 }),
    );

    const result = await fetchGoldenBenchmark();

    expect(result.metrics.precision).toBe(1);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/golden-benchmark',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer test-token' }),
      }),
    );
  });

  it('fetches review queue items', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ items: [{ review_id: 'r1' }], limit: 5, offset: 0 }), { status: 200 }),
    );

    const result = await fetchTruthReviewQueue(5);

    expect(result.items[0].review_id).toBe('r1');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/review-queue?limit=5',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer test-token' }),
      }),
    );
  });

  it('fetches adversarial validation as a dry-run preview', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ dry_run: true, checks: [{ check: 'conflicting_enrichment_observations' }] }), { status: 200 }),
    );

    const result = await fetchTruthValidationPreview(7);

    expect(result.checks[0].check).toBe('conflicting_enrichment_observations');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/validate/preview?sample_limit=7',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Authorization: 'Bearer test-token',
          'Content-Type': 'application/json',
        }),
        body: JSON.stringify({}),
      }),
    );
  });

  it('fetches materialization preview claim/evidence specs as a dry run', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        dry_run: true,
        mutations_planned: 0,
        planned_claims_total: 1,
        sample_materialized_claim_specs: [{ claim_id: 'claim-1', evidence_id: 'evidence-1' }],
      }), { status: 200 }),
    );

    const result = await fetchTruthMaterializationPreview(5);

    expect(result.sample_materialized_claim_specs[0].claim_id).toBe('claim-1');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/materialize/preview?limit=5',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Authorization: 'Bearer test-token',
          'Content-Type': 'application/json',
        }),
        body: JSON.stringify({}),
      }),
    );
  });

  it('fetches source-scoped materialization previews for safe batch planning', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        dry_run: true,
        mutations_planned: 0,
        selected_sources: ['building_management', 'outreach_feedback'],
        source_filter_applied: true,
        planned_claims_total: 2,
        sample_materialized_claim_specs: [],
      }), { status: 200 }),
    );

    const result = await fetchTruthMaterializationPreview(25, ['building_management', 'outreach_feedback']);

    expect(result.source_filter_applied).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/materialize/preview?limit=25&source=building_management&source=outreach_feedback',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Authorization: 'Bearer test-token',
          'Content-Type': 'application/json',
        }),
        body: JSON.stringify({}),
      }),
    );
  });

  it('fetches claim adjudication preview as a read-only gate', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        dry_run: true,
        mutations_planned: 0,
        verification_candidate_count: 0,
        blocker_counts: { needs_independent_source: 2 },
      }), { status: 200 }),
    );

    const result = await fetchTruthAdjudicationPreview(12);

    expect(result.dry_run).toBe(true);
    expect(result.verification_candidate_count).toBe(0);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/adjudication-preview?limit=12',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer test-token' }),
      }),
    );
  });

  it('previews manual evidence capture as a dry-run ledger write', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        run_type: 'manual_evidence_capture',
        dry_run: true,
        mutations_planned: 3,
        allowed_execute: false,
        claim_spec: { claim_id: 'claim-1', evidence_id: 'evidence-1' },
      }), { status: 200 }),
    );

    const result = await previewTruthManualEvidence({
      subject_type: 'lead',
      subject_id: 'lead-1',
      predicate: 'manages_building',
      object_type: 'building',
      object_id: '1000000001',
      claim_type: 'building_management',
      normalized_value: 'manager',
      support_status: 'supports',
      source_name: 'manual_evidence',
    });

    expect(result.allowed_execute).toBe(false);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/manual-evidence',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Authorization: 'Bearer test-token',
          'Content-Type': 'application/json',
        }),
        body: JSON.stringify({
          dry_run: true,
          confirm_execute: false,
          subject_type: 'lead',
          subject_id: 'lead-1',
          predicate: 'manages_building',
          object_type: 'building',
          object_id: '1000000001',
          claim_type: 'building_management',
          normalized_value: 'manager',
          support_status: 'supports',
          source_name: 'manual_evidence',
        }),
      }),
    );
  });

  it('previews source-evidence intake without execute flags', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        run_type: 'truth_source_evidence_intake_preview',
        dry_run: true,
        mutations_planned: 0,
        validation_status: 'ready_for_manual_evidence_preview',
        recording_ready: true,
        approval_required_before_recording: true,
        source_overlap_effect: {
          source_name: 'hpd_management_company',
          current_sources: [],
          source_already_present: false,
          adds_new_supporting_source: true,
          effect_status: 'adds_new_supporting_source',
        },
        relationship_match: {
          status: 'matched_current_work_item',
          work_item_id: 'source-acquisition-001',
        },
        manual_evidence_preview: {
          run_type: 'manual_evidence_capture',
          dry_run: true,
          mutations_planned: 3,
          allowed_execute: false,
        },
      }), { status: 200 }),
    );

    const result = await previewTruthSourceEvidenceIntake({
      relationship_label: 'MD Squared Property Group manages building 220 3 AVENUE',
      bbl: '1008747504',
      address: '220 3 AVENUE',
      manager_name: 'MD Squared Property Group',
      source_family: 'hpd_management_company',
      source_name: 'hpd_management_company',
      source_url_or_local_record_reference: 'https://data.cityofnewyork.us/resource/feu5-w2e2.json?registrationid=123',
      source_record_id: 'feu5-w2e2:123:ManagementCompany',
      observed_at: '2026-05-19T00:00:00+00:00',
      exact_property_match: true,
      role_specific_management_support: true,
      contradicts_current_claim: false,
    });

    expect(result.validation_status).toBe('ready_for_manual_evidence_preview');
    expect(result.source_overlap_effect?.adds_new_supporting_source).toBe(true);
    expect(result.manual_evidence_preview?.allowed_execute).toBe(false);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/source-evidence-intake/preview',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Authorization: 'Bearer test-token',
          'Content-Type': 'application/json',
        }),
        body: JSON.stringify({
          relationship_label: 'MD Squared Property Group manages building 220 3 AVENUE',
          bbl: '1008747504',
          address: '220 3 AVENUE',
          manager_name: 'MD Squared Property Group',
          source_family: 'hpd_management_company',
          source_name: 'hpd_management_company',
          source_url_or_local_record_reference: 'https://data.cityofnewyork.us/resource/feu5-w2e2.json?registrationid=123',
          source_record_id: 'feu5-w2e2:123:ManagementCompany',
          observed_at: '2026-05-19T00:00:00+00:00',
          exact_property_match: true,
          role_specific_management_support: true,
          contradicts_current_claim: false,
        }),
      }),
    );
  });

  it('previews source-evidence intake batches without execute flags', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        run_type: 'truth_source_evidence_intake_batch_preview',
        source_mode: 'hpd_audit_output',
        dry_run: true,
        mutations_planned: 0,
        allowed_execute: false,
        recording_ready_status: 'preview_ready_approval_required',
        required_execute_flags_for_batch: ['--execute', '--confirm-execute', '--confirm-batch-execute'],
        candidate_count: 1,
        original_candidate_count: 2,
        filtered_out_candidate_count: 1,
        ready_for_manual_evidence_preview_count: 1,
        recording_ready_count: 1,
        new_supporting_source_ready_count: 1,
        supporting_source_already_present_count: 0,
        contradiction_candidate_count: 0,
        blocked_count: 0,
        recommended_recording_scope: {
          scope: 'new_supporting_sources_only',
          dry_run: true,
          mutations_planned: 0,
          explicit_approval_required: true,
          required_execute_flags_for_batch: ['--execute', '--confirm-execute', '--confirm-batch-execute'],
          recommended_count: 1,
          recommended_relationships: [
            {
              work_item_id: 'source-acquisition-001',
              address: '220 3 AVENUE',
              manager_name: 'MD Squared Property Group',
              source_name: 'hpd_management_company',
              effect_status: 'adds_new_supporting_source',
            },
          ],
          duplicate_or_freshness_only_count: 1,
          duplicate_or_freshness_only_relationships: [],
          contradiction_review_count: 1,
          contradiction_relationships: [],
          expected_effect: 'Only new supporting sources should be approved first.',
          filtered_view: true,
          filtered_candidate_count: 1,
          non_effects: {
            will_mark_verified: false,
            will_refresh_sources: false,
            will_materialize_relationships: false,
            will_start_jobs: false,
            will_allow_business_use: false,
          },
          post_recording_expectations: {
            must_run: [
              'truth_adjudication_preview.py',
              'truth_source_overlap_post_recording_check.py',
              'truth_health_report.py',
              'truth_completion_audit.py --include-runtime',
            ],
            must_hold: {
              no_single_source_claim_marked_verified: true,
              no_business_use_activation: true,
            },
            acceptable_after_operator_seed_recording: {
              verification_candidate_count_may_remain_zero: true,
            },
          },
        },
        manual_evidence_replay_boundary: {
          explicit_approval_required: true,
          payload_file_replay_command: 'truth_manual_evidence.py --payload-file <reviewed-preview.json>',
          required_execute_flags_for_batch: ['--execute', '--confirm-execute', '--confirm-batch-execute'],
          will_mark_verified: false,
          will_refresh_sources: false,
          will_materialize_relationships: false,
          will_start_jobs: false,
          will_allow_business_use: false,
          post_recording_expectations: {
            must_run: [
              'truth_adjudication_preview.py',
              'truth_source_overlap_post_recording_check.py',
              'truth_health_report.py',
              'truth_completion_audit.py --include-runtime',
            ],
            must_hold: {
              no_single_source_claim_marked_verified: true,
              no_business_use_activation: true,
            },
            acceptable_after_operator_seed_recording: {
              verification_candidate_count_may_remain_zero: true,
            },
          },
        },
        previews: [
          {
            run_type: 'truth_source_evidence_intake_preview',
            dry_run: true,
            mutations_planned: 0,
            validation_status: 'ready_for_manual_evidence_preview',
            recording_ready: true,
            source_overlap_effect: {
              source_name: 'hpd_management_company',
              current_sources: ['outreach_confirmed'],
              source_already_present: false,
              adds_new_supporting_source: true,
              effect_status: 'adds_new_supporting_source',
            },
            manual_evidence_preview: {
              run_type: 'manual_evidence_capture',
              dry_run: true,
              mutations_planned: 3,
              allowed_execute: false,
            },
          },
        ],
      }), { status: 200 }),
    );

    const result = await previewTruthSourceEvidenceIntakeBatch({
      hpd_audit_output: {
        source_evidence_intake_candidates: [
          {
            relationship_label: 'MD Squared Property Group manages building 220 3 AVENUE',
            bbl: '1008747504',
            address: '220 3 AVENUE',
            manager_name: 'MD Squared Property Group',
            source_family: 'hpd_management_company',
            source_name: 'hpd_management_company',
            source_url_or_local_record_reference: 'https://data.cityofnewyork.us/resource/feu5-w2e2.json?registrationid=123',
            source_record_id: 'feu5-w2e2:123:ManagementCompany',
            observed_at: '2026-05-19T00:00:00+00:00',
            exact_property_match: true,
            role_specific_management_support: true,
            contradicts_current_claim: false,
          },
        ],
      },
      recommended_scope_only: true,
    });

    expect(result.run_type).toBe('truth_source_evidence_intake_batch_preview');
    expect(result.dry_run).toBe(true);
    expect(result.allowed_execute).toBe(false);
    expect(result.recording_ready_status).toBe('preview_ready_approval_required');
    expect(result.required_execute_flags_for_batch).toEqual([
      '--execute',
      '--confirm-execute',
      '--confirm-batch-execute',
    ]);
    expect(result.original_candidate_count).toBe(2);
    expect(result.filtered_out_candidate_count).toBe(1);
    expect(result.recording_ready_count).toBe(1);
    expect(result.new_supporting_source_ready_count).toBe(1);
    expect(result.supporting_source_already_present_count).toBe(0);
    expect(result.contradiction_candidate_count).toBe(0);
    expect(result.recommended_recording_scope?.recommended_count).toBe(1);
    expect(result.recommended_recording_scope?.filtered_view).toBe(true);
    expect(result.recommended_recording_scope?.recommended_relationships[0].work_item_id).toBe('source-acquisition-001');
    expect(result.recommended_recording_scope?.required_execute_flags_for_batch).toEqual([
      '--execute',
      '--confirm-execute',
      '--confirm-batch-execute',
    ]);
    expect(result.recommended_recording_scope?.non_effects?.will_mark_verified).toBe(false);
    expect(
      result.recommended_recording_scope?.post_recording_expectations
        ?.acceptable_after_operator_seed_recording?.verification_candidate_count_may_remain_zero,
    ).toBe(true);
    expect(result.manual_evidence_replay_boundary?.payload_file_replay_command).toContain('--payload-file');
    expect(result.manual_evidence_replay_boundary?.required_execute_flags_for_batch).toEqual([
      '--execute',
      '--confirm-execute',
      '--confirm-batch-execute',
    ]);
    expect(result.manual_evidence_replay_boundary?.will_mark_verified).toBe(false);
    expect(result.manual_evidence_replay_boundary?.post_recording_expectations?.must_run).toContain(
      'truth_source_overlap_post_recording_check.py',
    );
    expect(result.previews[0].source_overlap_effect?.adds_new_supporting_source).toBe(true);
    expect(result.previews[0].source_overlap_effect?.source_already_present).toBe(false);
    expect(result.previews[0].manual_evidence_preview?.allowed_execute).toBe(false);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/source-evidence-intake/batch-preview',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Authorization: 'Bearer test-token',
          'Content-Type': 'application/json',
        }),
        body: expect.stringContaining('source_evidence_intake_candidates'),
      }),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/source-evidence-intake/batch-preview',
      expect.objectContaining({
        body: expect.stringContaining('recommended_scope_only'),
      }),
    );
  });

  it('previews adjudication apply as a dry-run claim status update', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        run_type: 'truth_claim_adjudication',
        dry_run: true,
        mutations_planned: 2,
        allowed_execute: false,
        candidate_summary: { safe_candidate_count: 1, claim_update_count: 2 },
      }), { status: 200 }),
    );

    const result = await previewTruthAdjudicationApply({ limit: 10 });

    expect(result.allowed_execute).toBe(false);
    expect(result.candidate_summary?.claim_update_count).toBe(2);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/adjudication/apply',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Authorization: 'Bearer test-token',
          'Content-Type': 'application/json',
        }),
        body: JSON.stringify({
          dry_run: true,
          confirm_execute: false,
          limit: 10,
        }),
      }),
    );
  });

  it('previews role-claim corrections as a dry-run guarded update', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        run_type: 'truth_role_claim_correction',
        dry_run: true,
        mutations_planned: 50,
        allowed_execute: false,
        candidate_summary: {
          sampled_stale_claim_count: 50,
          claim_update_count: 50,
        },
      }), { status: 200 }),
    );

    const result = await previewTruthRoleClaimCorrection({ lead_id: '0ff794d3ba2d', limit: 100 });

    expect(result.dry_run).toBe(true);
    expect(result.allowed_execute).toBe(false);
    expect(result.candidate_summary?.claim_update_count).toBe(50);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/role-claim-corrections/apply',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Authorization: 'Bearer test-token',
          'Content-Type': 'application/json',
        }),
        body: JSON.stringify({
          dry_run: true,
          confirm_execute: false,
          lead_id: '0ff794d3ba2d',
          limit: 100,
        }),
      }),
    );
  });

  it('fetches the verification frontier without planning mutations', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        run_type: 'truth_verification_frontier',
        dry_run: true,
        mutations_planned: 0,
        current_ledger: {
          total_fact_group_count: 2078,
          single_source_fact_group_count: 2063,
          multi_source_fact_group_count: 15,
          source_ready_fact_group_count: 15,
        },
        source_ready_below_verified: {
          proposal_count: 10,
          single_source_upgrade_would_verify_count: 0,
          bundle_upgrade_would_verify_count: 10,
          proposals: [],
        },
        single_source_gaps: { proposal_count: 10, proposals: [] },
        source_acquisition_frontier: {
          manager_next_source_seed_count: 1,
          operator_second_source_seed_count: 4,
          manager_proposals: [],
          operator_proposals: [],
        },
        evidence_request_packet: {
          dry_run: true,
          mutations_planned: 0,
          request_count: 1,
          displayed_request_count: 1,
          source_ready_request_count: 1,
          single_source_request_count: 0,
          manager_source_request_count: 0,
          operator_source_request_count: 0,
          source_acquisition_request_count: 0,
          recording_ready_count: 0,
          approval_required_count: 1,
          reviewed_source_finding_count: 1,
          reviewed_source_history_status: 'reviewed_dead_end_no_recording_ready_source',
          reviewed_source_findings: [{
            source_family: 'public_web_search_followup_hpm_batch_20_2026_05_16',
            qualification: 'No recording-ready source was found.',
          }],
          source_ready_requests: [{
            request_type: 'source_ready_below_verified',
            relationship_label: 'Harlem Property Management manages building 141 WEST 123 STREET',
            reviewed_source_history_status: 'reviewed_dead_end_no_recording_ready_source',
            reviewed_source_findings: [{
              source_family: 'public_web_search_followup_hpm_batch_20_2026_05_16',
              qualification: 'No recording-ready source was found.',
            }],
          }],
          single_source_requests: [],
          source_acquisition_requests: [],
          requests: [],
        },
        safe_action: 'Read-only evidence acquisition planning only.',
        next_required_action: 'Acquire exact-property role-explicit evidence.',
      }), { status: 200 }),
    );

    const result = await fetchTruthVerificationFrontier(5);

    expect(result.dry_run).toBe(true);
    expect(result.mutations_planned).toBe(0);
    expect(result.current_ledger.multi_source_fact_group_count).toBe(15);
    expect(result.source_ready_below_verified.single_source_upgrade_would_verify_count).toBe(0);
    expect(result.evidence_request_packet?.reviewed_source_finding_count).toBe(1);
    expect(result.evidence_request_packet?.source_ready_requests[0].reviewed_source_history_status).toBe(
      'reviewed_dead_end_no_recording_ready_source',
    );
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/verification-frontier?limit=5',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer test-token' }),
      }),
    );
  });

  it('fetches the source-acquisition worklist', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        run_type: 'truth_source_acquisition_worklist',
        dry_run: true,
        mutations_planned: 0,
        source: 'truth_verification_frontier.evidence_request_packet',
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
          relationship: {
            relationship_label: 'MD Squared Property Group manages building 220 3 AVENUE',
            bbl: '1008747504',
          },
          source_family_needs: ['hpd_management_company'],
          search_queries: [],
          source_targets: [],
          acceptance_criteria: ['Only ManagementCompany rows count.'],
          paste_back_template: {},
          paste_back_templates: [{ source_family: 'hpd_management_company' }, { source_family: 'outreach_confirmed' }],
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
            contradiction_handling: 'If the source names a different current manager, route to review.',
            preview_command: 'truth_source_evidence_intake.py --candidate-csv <filled-worklist.csv> --recommended-scope-only --indent 2',
            safe_action: 'Preview only.',
          },
          paste_back_fields: ['source_record_id'],
        }],
        policy: {
          single_source_policy: 'No single-source claim may be marked verified.',
          role_policy: 'Agent is not manager.',
          execution_policy: 'Read-only.',
        },
        safe_action: 'Not evidence.',
      }), { status: 200 }),
    );

    const result = await fetchTruthSourceAcquisitionWorklist(5);

    expect(result.dry_run).toBe(true);
    expect(result.work_item_count).toBe(5);
    expect(result.work_items[0].relationship.bbl).toBe('1008747504');
    expect(result.policy.role_policy).toContain('Agent is not manager');
    expect(result.csv_template).toContain('relationship_label,bbl,address');
    expect(result.csv_template).toContain('outreach_confirmed');
    expect(result.work_items[0].paste_back_templates?.[1].source_family).toBe('outreach_confirmed');
    expect(result.work_items[0].operator_confirmation_request?.source_family).toBe('outreach_confirmed');
    expect(result.work_items[0].operator_confirmation_request?.contradiction_paste_back_template?.contradicts_current_claim).toBe(true);
    expect(result.work_items[0].operator_confirmation_request?.contradiction_handling).toContain('route to review');
    expect(result.work_items[0].operator_confirmation_request?.preview_command).toContain('--candidate-csv');
    expect(result.csv_template_command).toContain('--csv-template');
    expect(result.hpd_fetch_packet).toContain('registrations_api');
    expect(result.hpd_fetch_packet_command).toContain('--hpd-fetch-packet');
    expect(result.operator_confirmation_packet).toContain('question_prompt');
    expect(result.operator_confirmation_packet_command).toContain('--operator-confirmation-packet');
    expect(result.candidate_csv_preview_command).toContain('--candidate-csv <filled-worklist.csv>');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/source-acquisition-worklist?max_items=5',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer test-token' }),
      }),
    );
  });

  it('fetches the source-overlap blocker report', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
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
        evidence_request_summary: {
          request_count: 15,
          work_item_count: 5,
          hpd_work_item_count: 5,
          recording_ready_count: 0,
          approval_required_count: 15,
          reviewed_source_finding_count: 75,
        },
        source_bridge_assessment: {
          can_record_evidence_now: false,
          has_preview_ready_candidate_batch: true,
          can_mark_verified_now: false,
          blocking_reasons: ['verification_candidate_count=0', 'recording_ready_count=0'],
        },
        source_evidence_candidate_summary: {
          status: 'preview_ready_approval_required',
          checked: true,
          source_mode: 'candidate_file_recommended_scope_only',
          recording_ready_count: 2,
          recommended_count: 2,
          allowed_execute: false,
          required_execute_flags_for_batch: ['--execute', '--confirm-execute', '--confirm-batch-execute'],
          recommended_relationships: [{
            work_item_id: 'source-acquisition-001',
            relationship_label: 'MD Squared Property Group manages building 220 3 AVENUE',
            bbl: '1008747504',
          }],
        },
        top_blocked_relationships: [{
          work_item_id: 'source-acquisition-001',
          relationship_label: 'MD Squared Property Group manages building 220 3 AVENUE',
          bbl: '1008747504',
          source_family_needs: ['hpd_management_company'],
        }],
        safe_action: 'Not evidence.',
      }), { status: 200 }),
    );

    const result = await fetchTruthSourceOverlapBlockerReport(5);

    expect(result.dry_run).toBe(true);
    expect(result.status).toBe('blocked_evidence_acquisition_required');
    expect(result.evidence_request_summary.recording_ready_count).toBe(0);
    expect(result.source_bridge_assessment.has_preview_ready_candidate_batch).toBe(true);
    expect(result.source_evidence_candidate_summary?.status).toBe('preview_ready_approval_required');
    expect(result.source_evidence_candidate_summary?.allowed_execute).toBe(false);
    expect(result.source_evidence_candidate_summary?.recommended_relationships?.[0].bbl).toBe('1008747504');
    expect(result.top_blocked_relationships[0].bbl).toBe('1008747504');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/source-overlap-blocker-report?max_items=5',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer test-token' }),
      }),
    );
  });

  it('previews the source-overlap blocker report with candidate approval packets', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        run_type: 'truth_source_overlap_blocker_report',
        dry_run: true,
        mutations_planned: 0,
        status: 'blocked_evidence_acquisition_required',
        evidence_request_summary: {
          request_count: 15,
          work_item_count: 5,
          hpd_work_item_count: 5,
          recording_ready_count: 0,
          approval_required_count: 15,
        },
        source_bridge_assessment: {
          can_record_evidence_now: false,
          can_request_recording_approval: true,
          has_preview_ready_candidate_batch: true,
          candidate_preview_status: 'preview_ready_approval_required',
          candidate_recording_ready_count: 1,
          candidate_recommended_count: 1,
          candidate_allowed_execute: false,
          can_mark_verified_now: false,
          blocking_reasons: ['verification_candidate_count=0'],
          approval_boundary: 'A preview-clean candidate batch can be used to ask for explicit recording approval.',
        },
        source_evidence_candidate_summary: {
          status: 'preview_ready_approval_required',
          checked: true,
          source_mode: 'operator_confirmed_candidate_recommended_scope_only',
          recording_ready_count: 1,
          recommended_count: 1,
          allowed_execute: false,
          recording_approval_packet: {
            approval_required: true,
            allowed_execute: false,
            manual_evidence_payload_count: 1,
            manual_evidence_payload_review: [{
              payload_index: 1,
              manager_name: 'MD Squared Property Group',
              object_id: '1008747504',
              source_name: 'outreach_confirmed',
            }],
            expected_post_recording_source_overlap: {
              recommended_row_count: 1,
              first_source_only_after_recording_count: 1,
              multi_source_after_recording_count: 0,
              source_ready_after_recording_count: 0,
              safe_action: 'The recommended recording scope would add evidence, but none of the listed rows would become multi-source/source-ready immediately after recording.',
            },
            approval_question: 'Approve recording 1 preview-clean new-supporting-source manual-evidence row(s) only?',
            execute_command_after_approval: 'truth_manual_evidence.py --payload-file <reviewed-preview.json> --execute --confirm-execute --confirm-batch-execute',
          },
          recommended_relationships: [{
            work_item_id: 'source-acquisition-001',
            relationship_label: 'MD Squared Property Group manages building 220 3 AVENUE',
            bbl: '1008747504',
          }],
        },
        top_blocked_relationships: [],
        safe_action: 'Not evidence.',
      }), { status: 200 }),
    );

    const result = await previewTruthSourceOverlapBlockerReport({
      candidates: [{
        relationship_label: 'MD Squared Property Group manages building 220 3 AVENUE',
        bbl: '1008747504',
        address: '220 3 AVENUE',
        manager_name: 'MD Squared Property Group',
        source_family: 'outreach_confirmed',
        source_name: 'outreach_confirmed',
        exact_property_match: true,
        role_specific_management_support: true,
      }],
      source_mode: 'operator_confirmed_candidate',
    }, 5);

    expect(result.dry_run).toBe(true);
    expect(result.source_bridge_assessment.has_preview_ready_candidate_batch).toBe(true);
    expect(result.source_bridge_assessment.can_request_recording_approval).toBe(true);
    expect(result.source_bridge_assessment.candidate_allowed_execute).toBe(false);
    expect(result.source_bridge_assessment.candidate_recommended_count).toBe(1);
    expect(result.source_bridge_assessment.approval_boundary).toContain('preview-clean candidate batch');
    expect(result.source_evidence_candidate_summary?.recording_approval_packet?.approval_required).toBe(true);
    expect(result.source_evidence_candidate_summary?.recording_approval_packet?.allowed_execute).toBe(false);
    expect(result.source_evidence_candidate_summary?.recording_approval_packet?.manual_evidence_payload_count).toBe(1);
    expect(
      result.source_evidence_candidate_summary?.recording_approval_packet?.expected_post_recording_source_overlap?.source_ready_after_recording_count,
    ).toBe(0);
    expect(
      result.source_evidence_candidate_summary?.recording_approval_packet?.manual_evidence_payload_review?.[0].object_id,
    ).toBe('1008747504');
    expect(result.source_evidence_candidate_summary?.recommended_relationships?.[0].bbl).toBe('1008747504');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/source-overlap-blocker-report/preview?max_items=5',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ Authorization: 'Bearer test-token' }),
        body: JSON.stringify({
          recommended_scope_only: true,
          candidates: [{
            relationship_label: 'MD Squared Property Group manages building 220 3 AVENUE',
            bbl: '1008747504',
            address: '220 3 AVENUE',
            manager_name: 'MD Squared Property Group',
            source_family: 'outreach_confirmed',
            source_name: 'outreach_confirmed',
            exact_property_match: true,
            role_specific_management_support: true,
          }],
          source_mode: 'operator_confirmed_candidate',
        }),
      }),
    );
  });

  it('previews clue-only source-overlap blocker packets without approval payloads', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        run_type: 'truth_source_overlap_blocker_report',
        dry_run: true,
        mutations_planned: 0,
        status: 'blocked_evidence_acquisition_required',
        source_bridge_assessment: {
          can_record_evidence_now: false,
          has_preview_ready_candidate_batch: false,
          has_source_acquisition_clues: true,
          source_acquisition_clue_count: 1,
          can_mark_verified_now: false,
          blocking_reasons: ['source_clue_only_primary_source_required'],
        },
        source_evidence_candidate_summary: {
          status: 'source_clue_only_primary_source_required',
          checked: true,
          source_mode: 'derived_research',
          candidate_count: 0,
          source_acquisition_clue_count: 1,
          source_acquisition_clues: [{
            address: '342 WEST 56 STREET',
            clue_status: 'source_clue_only',
          }],
          recording_ready_count: 0,
          recommended_count: 0,
          allowed_execute: false,
          can_record_evidence_now: false,
        },
        top_blocked_relationships: [],
        safe_action: 'Not evidence.',
      }), { status: 200 }),
    );

    const result = await previewTruthSourceOverlapBlockerReport({
      hpd_audit_output: {
        document_kind: 'derived_research',
        source_acquisition_clues: [{
          address: '342 WEST 56 STREET',
          clue_status: 'source_clue_only',
        }],
      },
      source_mode: 'derived_research',
      recommended_scope_only: true,
    }, 5);

    expect(result.source_bridge_assessment.has_preview_ready_candidate_batch).toBe(false);
    expect(result.source_bridge_assessment.has_source_acquisition_clues).toBe(true);
    expect(result.source_evidence_candidate_summary?.status).toBe('source_clue_only_primary_source_required');
    expect(result.source_evidence_candidate_summary?.source_acquisition_clue_count).toBe(1);
    expect(result.source_evidence_candidate_summary?.recording_ready_count).toBe(0);
    expect(result.source_evidence_candidate_summary?.allowed_execute).toBe(false);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/source-overlap-blocker-report/preview?max_items=5',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          recommended_scope_only: true,
          hpd_audit_output: {
            document_kind: 'derived_research',
            source_acquisition_clues: [{
              address: '342 WEST 56 STREET',
              clue_status: 'source_clue_only',
            }],
          },
          source_mode: 'derived_research',
        }),
      }),
    );
  });

  it('fetches the conservative truth health report', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ dry_run: true, summary: { trust_posture: 'not_ready' }, trust_gaps: [] }), { status: 200 }),
    );

    const result = await fetchTruthHealthReport();

    expect(result.summary.trust_posture).toBe('not_ready');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/health-report',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer test-token' }),
      }),
    );
  });

  it('fetches the activation approval packet', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        verdict: 'schema_approval_required',
        business_use_allowed: false,
        approval_steps: [{ step: 'apply_truth_schema' }],
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
          evidence_acquisition_required: true,
        },
      }), { status: 200 }),
    );

    const result = await fetchTruthActivationPacket();

    expect(result.verdict).toBe('schema_approval_required');
    expect(result.business_use_allowed).toBe(false);
    expect(result.verification_frontier?.source_ready_below_verified_count).toBe(10);
    expect(result.verification_frontier?.current_ledger.source_ready_fact_group_count).toBe(15);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/activation-packet',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer test-token' }),
      }),
    );
  });

  it('submits review decisions as dry-run previews by default', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ review_id: 'r1', dry_run: true, target_status: 'approved' }), { status: 200 }),
    );

    const result = await submitTruthReviewDecision('r1', { decision: 'approve' });

    expect(result.target_status).toBe('approved');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/truth/review-queue/r1/decision',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Authorization: 'Bearer test-token',
          'Content-Type': 'application/json',
        }),
        body: JSON.stringify({ dry_run: true, confirm_execute: false, decision: 'approve' }),
      }),
    );
  });
});
