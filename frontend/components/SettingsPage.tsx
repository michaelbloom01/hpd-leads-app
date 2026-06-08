/**
 * SettingsPage — Scoring Weights + Data Health Dashboard.
 * Two sub-sections:
 *  1. Scoring Configuration: manage presets, custom configs, weights
 *  2. Data Health: ingestion freshness, match rates, coverage
 */
import React, { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'react-hot-toast';
import {
  fetchConfigs, fetchActiveConfig, activateConfig, triggerRecalculate,
  createConfig,
  type ScoringWeights,
} from '../services/scoring-api';
import { fetchQualitySummary, fetchCoverage, fetchSourceAudit } from '../services/quality-api';
import {
  fetchGoldenBenchmark,
  fetchTruthActivationPacket,
  fetchTruthDashboard,
  fetchTruthHealthReport,
  fetchTruthAdjudicationPreview,
  fetchTruthCompletionAudit,
  fetchTruthSourceOverlapApprovalPacket,
  fetchTruthSourceOverlapPostRecordingCheck,
  fetchTruthSourceAcquisitionWorklist,
  fetchTruthSourceOverlapBlockerReport,
  fetchTruthVerificationFrontier,
  fetchTruthMaterializationPreview,
  fetchTruthReviewQueue,
  fetchTruthValidationPreview,
  previewTruthSourceOverlapBlockerReport,
  previewTruthRoleClaimCorrection,
  previewTruthManualEvidence,
  submitTruthReviewDecision,
  type TruthMaterializedClaimSpecPreview,
  type TruthActionabilityRule,
  type TruthReviewItem,
} from '../services/truth-api';
import { fetchJobs, fetchJobsSummary, startJob } from '../services/jobs-api';

const SIGNAL_LABELS: Record<string, string> = {
  ownership_change: 'Ownership Change',
  complaint_spike: 'Complaint Spike',
  violation_trend: 'Violation Trend',
  energy_grade_drop: 'Energy Grade Drop',
  dob_permits: 'DOB Permits',
  hpd_litigation: 'Housing Litigation',
  emergency_repairs: 'Emergency Repairs',
  building_size: 'Building Scale',
  eviction_activity: 'Eviction Activity',
  facade_status: 'Facade Status',
};

const DEFAULT_WEIGHTS: ScoringWeights = {
  ownership_change: 22, complaint_spike: 12, violation_trend: 12,
  energy_grade_drop: 8, dob_permits: 8, hpd_litigation: 12,
  emergency_repairs: 8, building_size: 5, eviction_activity: 7, facade_status: 6,
};

const actionabilityCriteria = (rule: TruthActionabilityRule): string[] => {
  const criteria: string[] = [];
  if (typeof rule.minimum_score === 'number') criteria.push(`${Math.round(rule.minimum_score * 100)}%+ confidence`);
  if (typeof rule.max_contradictions === 'number') criteria.push(`${rule.max_contradictions} contradiction${rule.max_contradictions === 1 ? '' : 's'}`);
  if (typeof rule.max_freshness_days === 'number') criteria.push(`${rule.max_freshness_days}d freshness`);
  if (typeof rule.min_supporting_sources === 'number') criteria.push(`${rule.min_supporting_sources} source${rule.min_supporting_sources === 1 ? '' : 's'}`);
  if (typeof rule.min_supporting_evidence === 'number') criteria.push(`${rule.min_supporting_evidence} evidence`);
  return criteria;
};

const formatPercent = (value: number | null | undefined): string => (
  typeof value === 'number' ? `${Math.round(value * 100)}%` : 'n/a'
);

const formatLabel = (value: string | number | null | undefined, fallback = 'unknown'): string => (
  String(value ?? fallback).replace(/_/g, ' ')
);

const ScoringSection: React.FC = () => {
  const queryClient = useQueryClient();
  const { data: configs } = useQuery({ queryKey: ['scoring-configs'], queryFn: fetchConfigs });
  const { data: activeConfig } = useQuery({ queryKey: ['active-config'], queryFn: fetchActiveConfig });
  const [editWeights, setEditWeights] = useState<ScoringWeights>(DEFAULT_WEIGHTS);
  const [editName, setEditName] = useState('');
  const [selectedConfig, setSelectedConfig] = useState<number | null>(null);

  useEffect(() => {
    if (activeConfig) {
      setEditWeights(activeConfig.weights);
      setSelectedConfig(activeConfig.id);
      setEditName(activeConfig.name);
    }
  }, [activeConfig]);

  const total = Object.values(editWeights).reduce((a, b) => a + b, 0);
  const isValid = total === 100;

  const activateMut = useMutation({
    mutationFn: activateConfig,
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['scoring-configs'] }); queryClient.invalidateQueries({ queryKey: ['active-config'] }); toast.success('Configuration activated'); },
  });

  const recalcMut = useMutation({
    mutationFn: triggerRecalculate,
    onSuccess: (data) => {
      if (data.status === 'approval_required') {
        toast('Score recalculation preview ready. Execution requires explicit approval and confirm_execute=true.');
        return;
      }
      if (data.dispatch_mode === 'in_process') {
        toast(`Recalculation queued (Job #${data.job_id}) in local fallback mode.`);
      } else {
        toast.success(`Recalculation queued (Job #${data.job_id})`);
      }
    },
    onError: () => toast.error('Failed to trigger recalculation'),
  });

  const saveMut = useMutation({
    mutationFn: ({ name, weights }: { name: string; weights: ScoringWeights }) => createConfig(name, weights),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['scoring-configs'] }); toast.success('Configuration saved'); },
  });

  const handleWeightChange = (key: keyof ScoringWeights, value: number) => {
    setEditWeights(prev => ({ ...prev, [key]: Math.max(0, Math.min(100, value)) }));
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-gray-900">Scoring Configuration</h2>
        <div className="flex gap-2">
          <button
            onClick={() => saveMut.mutate({ name: editName || 'Custom', weights: editWeights })}
            disabled={!isValid || saveMut.isPending}
            className="px-3 py-1.5 text-sm bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 disabled:opacity-50"
          >
            Save as New
          </button>
          <button
            onClick={() => recalcMut.mutate()}
            disabled={recalcMut.isPending}
            className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 font-medium"
          >
            {recalcMut.isPending ? 'Checking...' : 'Preview Recalculation'}
          </button>
        </div>
      </div>

      {/* Preset selector */}
      {configs && (
        <div className="flex flex-wrap gap-2">
          {configs.map(c => (
            <button
              key={c.id}
              onClick={() => { setSelectedConfig(c.id); setEditWeights(c.weights); setEditName(c.name); }}
              className={`px-3 py-1.5 rounded-lg text-sm border transition-colors ${
                selectedConfig === c.id
                  ? 'bg-blue-50 border-blue-300 text-blue-700 font-medium'
                  : 'bg-white border-gray-200 text-gray-600 hover:bg-gray-50'
              }`}
            >
              {c.name}
              {c.is_active && <span className="ml-1 text-xs text-green-600">(active)</span>}
            </button>
          ))}
        </div>
      )}

      {/* Weight sliders */}
      <div className="bg-white rounded-lg border border-gray-200 p-5">
        <div className="flex items-center justify-between mb-4">
          <span className="text-sm text-gray-500">Total: <span className={isValid ? 'text-green-600 font-medium' : 'text-red-600 font-medium'}>{total}/100</span></span>
        </div>
        <div className="space-y-4">
          {(Object.keys(SIGNAL_LABELS) as (keyof ScoringWeights)[]).map(key => (
            <div key={key} className="flex items-center gap-4">
              <span className="text-sm text-gray-700 w-40 flex-shrink-0">{SIGNAL_LABELS[key]}</span>
              <input
                type="range"
                min={0} max={50} step={1}
                value={editWeights[key]}
                onChange={e => handleWeightChange(key, parseInt(e.target.value))}
                className="flex-1 h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-blue-600"
              />
              <input
                type="number"
                min={0} max={100}
                value={editWeights[key]}
                onChange={e => handleWeightChange(key, parseInt(e.target.value) || 0)}
                className="w-14 px-2 py-1 text-sm border border-gray-300 rounded text-center"
              />
            </div>
          ))}
        </div>
      </div>

      {/* Activate selected */}
      {selectedConfig && selectedConfig !== activeConfig?.id && (
        <button
          onClick={() => activateMut.mutate(selectedConfig)}
          className="px-4 py-2 text-sm bg-green-600 text-white rounded-lg hover:bg-green-700 font-medium"
        >
          Activate Selected Configuration
        </button>
      )}
    </div>
  );
};

const DataHealthSection: React.FC = () => {
  const { data: quality } = useQuery({ queryKey: ['quality-summary'], queryFn: fetchQualitySummary, staleTime: 30000 });
  const { data: coverage } = useQuery({ queryKey: ['quality-coverage'], queryFn: fetchCoverage, staleTime: 30000 });
  const { data: sourceAudit } = useQuery({ queryKey: ['quality-source-audit'], queryFn: fetchSourceAudit, staleTime: 30000 });
  const { data: truthDashboard } = useQuery({ queryKey: ['truth-dashboard'], queryFn: fetchTruthDashboard, staleTime: 30000 });
  const { data: truthHealth } = useQuery({ queryKey: ['truth-health-report'], queryFn: fetchTruthHealthReport, staleTime: 60000 });
  const { data: truthActivationPacket } = useQuery({ queryKey: ['truth-activation-packet'], queryFn: fetchTruthActivationPacket, staleTime: 60000 });
  const { data: truthCompletionAudit } = useQuery({ queryKey: ['truth-completion-audit'], queryFn: fetchTruthCompletionAudit, staleTime: 60000 });
  const { data: truthSourceOverlapApprovalPacket } = useQuery({ queryKey: ['truth-source-overlap-approval-packet'], queryFn: fetchTruthSourceOverlapApprovalPacket, staleTime: 60000 });
  const { data: truthSourceOverlapPostRecordingCheck } = useQuery({ queryKey: ['truth-source-overlap-post-recording-check'], queryFn: fetchTruthSourceOverlapPostRecordingCheck, staleTime: 60000 });
  const { data: truthVerificationFrontier } = useQuery({ queryKey: ['truth-verification-frontier'], queryFn: () => fetchTruthVerificationFrontier(5), staleTime: 60000 });
  const { data: truthSourceAcquisitionWorklist } = useQuery({ queryKey: ['truth-source-acquisition-worklist'], queryFn: () => fetchTruthSourceAcquisitionWorklist(5), staleTime: 60000 });
  const { data: truthSourceOverlapBaseBlockerReport } = useQuery({ queryKey: ['truth-source-overlap-blocker-report'], queryFn: () => fetchTruthSourceOverlapBlockerReport(5), staleTime: 60000 });
  const { data: goldenBenchmark } = useQuery({ queryKey: ['truth-golden-benchmark'], queryFn: fetchGoldenBenchmark, staleTime: 30000 });
  const { data: reviewQueue } = useQuery({ queryKey: ['truth-review-queue'], queryFn: () => fetchTruthReviewQueue(5), staleTime: 30000 });
  const { data: validationPreview } = useQuery({ queryKey: ['truth-validation-preview'], queryFn: () => fetchTruthValidationPreview(10), staleTime: 60000 });
  const { data: materializationPreview } = useQuery({ queryKey: ['truth-materialization-preview'], queryFn: () => fetchTruthMaterializationPreview(5), staleTime: 60000 });
  const { data: adjudicationPreview } = useQuery({ queryKey: ['truth-adjudication-preview'], queryFn: () => fetchTruthAdjudicationPreview(20), staleTime: 60000 });
  const [sourceEvidenceCandidateJson, setSourceEvidenceCandidateJson] = useState('');
  const [sourceEvidenceCandidateJsonError, setSourceEvidenceCandidateJsonError] = useState<string | null>(null);
  const sourceOverlapBlockerPreviewMut = useMutation({
    mutationFn: (request: Parameters<typeof previewTruthSourceOverlapBlockerReport>[0]) => previewTruthSourceOverlapBlockerReport(request, 5),
    onSuccess: () => toast.success('Source-overlap candidate preview refreshed'),
    onError: () => toast.error('Failed to preview source-overlap candidates'),
  });
  const truthSourceOverlapBlockerReport = sourceOverlapBlockerPreviewMut.data ?? truthSourceOverlapBaseBlockerReport;
  const { data: jobs } = useQuery({ queryKey: ['jobs'], queryFn: () => fetchJobs(undefined, 10), refetchInterval: 10000 });
  const { data: jobsSummary } = useQuery({ queryKey: ['jobs-summary'], queryFn: fetchJobsSummary, refetchInterval: 10000 });
  const coordinateAudit = (sourceAudit?.sources ?? []).find((source) => source.source_name === 'building_coordinates');
  const formatMetric = (value: number | null | undefined) => (
    typeof value === 'number' ? `${Math.round(value * 100)}%` : 'n/a'
  );
  const truthPanelAvailable = Boolean(truthDashboard || truthHealth);
  const truthClaimCount = truthDashboard?.claim_count ?? truthHealth?.summary.claim_count ?? 0;
  const truthVerifiedCount = truthDashboard?.verified_claim_count ?? truthHealth?.summary.verified_claim_count ?? 0;
  const truthConflictCount = truthDashboard?.conflicting_claim_count ?? truthHealth?.summary.conflicting_claim_count ?? 0;
  const truthOpenReviewCount = truthDashboard?.open_review_count ?? truthHealth?.summary.open_review_count ?? 0;
  const truthGoldenCaseCount = truthDashboard?.active_golden_case_count ?? truthHealth?.summary.configured_golden_cases ?? 0;
  const topTrustGaps = (truthHealth?.trust_gaps ?? []).slice(0, 3);
  const activationChecklist = truthHealth?.activation_checklist ?? [];
  const validationChecks = validationPreview?.checks ?? [];
  const validationMutationCount = validationPreview?.mutations_planned ?? 0;
  const topValidationChecks = validationChecks.slice(0, 3);
  const materializedClaimSpecs = (materializationPreview?.sample_materialized_claim_specs ?? []).slice(0, 3);
  const adjudication = adjudicationPreview ?? truthHealth?.adjudication_preview ?? null;
  const adjudicationSamples = (adjudication?.samples ?? []).slice(0, 3);
  const adjudicationBlockerEntries = Object.entries(adjudication?.blocker_counts ?? {}).slice(0, 4);
  const adjudicationSourceCoverage = adjudication?.source_coverage;
  const adjudicationTopSources = adjudicationSourceCoverage?.top_sources?.slice(0, 4) ?? [];
  const ledgerSourceOverlap = adjudication?.ledger_source_overlap;
  const ledgerTopSources = ledgerSourceOverlap?.top_sources?.slice(0, 4) ?? [];
  const roleSourceOverlapPilot = adjudication?.role_source_overlap_pilot;
  const roleSourceOverlapSamples = roleSourceOverlapPilot?.samples?.slice(0, 3) ?? [];
  const scaledRoleSourceOverlap = adjudication?.scaled_role_source_overlap;
  const scaledRoleSourceOverlapBatches = scaledRoleSourceOverlap?.batches?.slice(0, 3) ?? [];
  const roleOverlapSimulation = adjudication?.role_overlap_post_materialization_simulation;
  const managerSourceBridgePreview = adjudication?.manager_source_bridge_preview;
  const managerSourceBridgeSamples = managerSourceBridgePreview?.samples?.slice(0, 3) ?? [];
  const managerExternalSourcePreview = adjudication?.manager_external_source_acquisition_preview;
  const managerExternalBatchPreview = managerExternalSourcePreview?.manual_evidence_batch_preview;
  const sourceOverlapApprovalPacket = truthSourceOverlapApprovalPacket;
  const sourceOverlapRecordingGate = sourceOverlapApprovalPacket?.source_overlap_recording_gate;
  const sourceOverlapApprovalSummary = sourceOverlapApprovalPacket?.recommended_first_packet?.approval_decision_summary;
  const sourceOverlapOperatorApprovalSummary = sourceOverlapApprovalPacket?.operator_strict_packet?.approval_decision_summary;
  const sourceOverlapMutationScope = sourceOverlapApprovalPacket?.recommended_first_packet?.sample_manual_evidence_previews?.[0]?.mutation_scope;
  const sourceOverlapOperatorMutationScope = sourceOverlapApprovalPacket?.operator_strict_packet?.sample_manual_evidence_previews?.[0]?.mutation_scope;
  const sourceOverlapNewRelationshipSummary = sourceOverlapApprovalPacket?.manager_new_relationship_candidate_summary;
  const sourceOverlapNewRelationshipFamilies = Object.entries(sourceOverlapNewRelationshipSummary?.source_family_counts ?? {});
  const sourceOverlapPostRecordingCheck = truthSourceOverlapPostRecordingCheck;
  const sourceOverlapPostRecordingFailedChecks = sourceOverlapPostRecordingCheck?.checks?.filter((check) => check.status !== 'pass') ?? [];
  const managerExternalPostRecordingSimulation = managerExternalSourcePreview?.post_recording_simulation;
  const managerExternalNextSourceBatches = managerExternalSourcePreview?.next_source_batches;
  const managerExternalClaimGroups = managerExternalSourcePreview?.claim_groups?.slice(0, 3) ?? [];
  const managerExternalNewRelationshipCandidates = managerExternalSourcePreview?.new_relationship_candidates?.slice(0, 2) ?? [];
  const managerExternalBlockedCandidates = managerExternalSourcePreview?.evidence_candidates
    ?.filter((candidate) => !candidate.clean_for_operator_review)
    ?.slice(0, 2) ?? [];
  const operatorConfirmedPreview = adjudication?.operator_confirmed_management_preview;
  const operatorConfirmedCandidates = operatorConfirmedPreview?.candidates?.slice(0, 4) ?? [];
  const operatorSecondSourceSeedBatches = operatorConfirmedPreview?.second_source_seed_batches;
  const operatorSecondSourceProposals = operatorSecondSourceSeedBatches?.proposals?.slice(0, 4) ?? [];
  const roleClaimCorrectionPreview = adjudication?.role_claim_correction_preview;
  const roleClaimCorrectionSamples = roleClaimCorrectionPreview?.samples?.slice(0, 3) ?? [];
  const roleOverlapActivationPlan = adjudication?.role_overlap_activation_plan;
  const roleOverlapActivationSteps = roleOverlapActivationPlan?.ordered_steps ?? [];
  const verificationGapPlan = adjudication?.verification_gap_plan;
  const verificationGapProposals = verificationGapPlan?.proposals?.slice(0, 3) ?? [];
  const verifiedConfidenceGapPlan = adjudication?.verified_confidence_gap_plan;
  const verifiedConfidenceGapProposals = verifiedConfidenceGapPlan?.proposals?.slice(0, 3) ?? [];
  const verificationFrontierReadyGaps = truthVerificationFrontier?.source_ready_below_verified?.proposals?.slice(0, 2) ?? [];
  const verificationFrontierManagerProposals = truthVerificationFrontier?.source_acquisition_frontier?.manager_proposals?.slice(0, 2) ?? [];
  const verificationFrontierOperatorProposals = truthVerificationFrontier?.source_acquisition_frontier?.operator_proposals?.slice(0, 2) ?? [];
  const verificationEvidenceRequestPacket = truthVerificationFrontier?.evidence_request_packet;
  const verificationEvidenceRequests = verificationEvidenceRequestPacket?.requests?.slice(0, 3) ?? [];
  const sourceAcquisitionWorkItems = truthSourceAcquisitionWorklist?.work_items?.slice(0, 3) ?? [];
  const sourceOverlapThresholdRelationships = truthSourceOverlapBlockerReport?.threshold_sensitive_relationships?.slice(0, 3) ?? [];
  const sourceOverlapBlockerTopRelationships = truthSourceOverlapBlockerReport?.top_blocked_relationships?.slice(0, 3) ?? [];
  const sourceOverlapBridgeAssessment = truthSourceOverlapBlockerReport?.source_bridge_assessment;
  const sourceOverlapEvidenceRequestSummary = truthSourceOverlapBlockerReport?.evidence_request_summary;
  const sourceOverlapBlockerReasons = sourceOverlapBridgeAssessment?.blocking_reasons?.slice(0, 4) ?? [];
  const sourceEvidenceCandidateSummary = truthSourceOverlapBlockerReport?.source_evidence_candidate_summary;
  const sourceEvidenceCandidateRelationships = sourceEvidenceCandidateSummary?.recommended_relationships?.slice(0, 2) ?? [];
  const sourceEvidenceCandidatePayloadReview = sourceEvidenceCandidateSummary?.recording_approval_packet?.manual_evidence_payload_review?.slice(0, 2) ?? [];
  const sourceEvidenceExpectedOverlap = sourceEvidenceCandidateSummary?.recording_approval_packet?.expected_post_recording_source_overlap;
  const sourceEvidenceCandidateClues = sourceEvidenceCandidateSummary?.source_acquisition_clues?.slice(0, 2) ?? [];
  const truthVerificationCandidateCount = adjudication?.verification_candidate_count ?? truthHealth?.summary.verification_candidate_count ?? 0;
  const truthSchemaStatus = truthHealth?.schema_status;
  const missingTruthTables = truthSchemaStatus?.missing_tables ?? [];
  const truthSourceAudit = truthHealth?.source_audit;
  const truthSourceRefreshPlan = truthHealth?.source_refresh_plan ?? truthSourceAudit?.refresh_plan;
  const truthSourceSummary = truthSourceAudit?.summary;
  const truthSourceGaps = truthSourceAudit?.critical_gaps ?? [];
  const staleSourceCount = truthSourceSummary?.stale_ingest ?? 0;
  const noRecentSourceCount = truthSourceSummary?.no_recent_ingest ?? 0;
  const missingSourceSchemaCount = truthSourceSummary?.schema_missing ?? 0;
  const activationNextSteps = (truthActivationPacket?.next_safe_steps ?? []).slice(0, 3);
  const activationRefreshJobs = (truthActivationPacket?.source_refresh?.next_jobs ?? []).slice(0, 3);
  const activationClaimReadiness = truthActivationPacket?.claim_readiness;
  const activationVerificationFrontier = truthActivationPacket?.verification_frontier;
  const completionPromptChecklist = truthCompletionAudit?.prompt_to_artifact_checklist ?? [];
  const completionStatusCounts = completionPromptChecklist.reduce<Record<string, number>>((acc, item) => {
    acc[item.status] = (acc[item.status] ?? 0) + 1;
    return acc;
  }, {});
  const completionBlockedItems = completionPromptChecklist.filter((item) => item.status !== 'satisfied').slice(0, 3);
  const completionSourceOverlapBlocker = truthCompletionAudit?.runtime_blockers?.find((blocker) => blocker.gate === 'source_overlap_recording');
  const completionOperatorEvidence = completionSourceOverlapBlocker?.evidence?.operator_confirmed as Record<string, unknown> | undefined;
  const completionOperatorGapSummary = completionOperatorEvidence?.strict_gap_summary as Record<string, unknown> | undefined;
  const completionOperatorGapCandidates = (
    Array.isArray(completionOperatorGapSummary?.gap_candidates)
      ? completionOperatorGapSummary.gap_candidates as Array<Record<string, unknown>>
      : []
  );
  const severityClass = (severity: string) => (
    severity === 'critical' ? 'text-red-700 bg-red-50 border-red-100'
      : severity === 'high' ? 'text-rose-700 bg-rose-50 border-rose-100'
        : severity === 'medium' ? 'text-amber-700 bg-amber-50 border-amber-100'
          : 'text-gray-700 bg-gray-50 border-gray-100'
  );
  const summarizeEvidence = (evidence: Record<string, unknown> | null | undefined) => {
    if (!evidence) return [];
    const candidates = [
      evidence.sources,
      evidence.source_names,
      evidence.supporting_sources,
      evidence.contradicting_sources,
    ];
    for (const value of candidates) {
      if (Array.isArray(value) && value.length > 0) {
        return value.slice(0, 4).map(String);
      }
    }
    const sourceName = evidence.source_name || evidence.source;
    return sourceName ? [String(sourceName)] : [];
  };
  const summarizeRationale = (item: TruthReviewItem) => {
    const rationale = item.rationale || {};
    const proposed = item.proposed_change || {};
    const text = rationale.reason || rationale.message || rationale.why || proposed.reason || proposed.operation;
    return text ? String(text) : null;
  };
  const handleSourceOverlapCandidatePreview = () => {
    setSourceEvidenceCandidateJsonError(null);
    const raw = sourceEvidenceCandidateJson.trim();
    if (!raw) {
      setSourceEvidenceCandidateJsonError('Paste source_evidence_intake_candidates JSON or HPD audit JSON first.');
      return;
    }
    try {
      const parsed: unknown = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        sourceOverlapBlockerPreviewMut.mutate({
          candidates: parsed as Parameters<typeof previewTruthSourceOverlapBlockerReport>[0]['candidates'],
          source_mode: 'candidate_list',
          recommended_scope_only: true,
        });
        return;
      }
      if (parsed && typeof parsed === 'object') {
        const record = parsed as Record<string, unknown>;
        if (Array.isArray(record.candidates)) {
          sourceOverlapBlockerPreviewMut.mutate({
            candidates: record.candidates as Parameters<typeof previewTruthSourceOverlapBlockerReport>[0]['candidates'],
            hpd_audit_output: (record.hpd_audit_output as Record<string, unknown> | Array<Record<string, unknown>> | null | undefined) ?? null,
            source_mode: String(record.source_mode || 'candidate_list'),
            recommended_scope_only: true,
          });
          return;
        }
        sourceOverlapBlockerPreviewMut.mutate({
          hpd_audit_output: record,
          source_mode: 'hpd_audit_output',
          recommended_scope_only: true,
        });
        return;
      }
      setSourceEvidenceCandidateJsonError('Candidate preview JSON must be an object or array.');
    } catch {
      setSourceEvidenceCandidateJsonError('Candidate preview JSON is not valid JSON.');
    }
  };
  const [manualEvidenceDraft, setManualEvidenceDraft] = useState({
    subject_type: 'lead',
    subject_id: 'lead-1',
    predicate: 'manages_building',
    object_type: 'building',
    object_id: '1000000001',
    claim_type: 'building_management',
    normalized_value: 'manager',
    support_status: 'supports' as 'supports' | 'contradicts',
    source_name: 'manual_evidence',
    source_type: 'operator_review',
    source_record_id: 'settings-preview',
    source_url: '',
    note: '',
  });
  const [manualEvidenceSeeded, setManualEvidenceSeeded] = useState(false);
  useEffect(() => {
    const spec = materializedClaimSpecs[0];
    if (manualEvidenceSeeded || !spec) return;
    setManualEvidenceDraft((current) => ({
      ...current,
      subject_type: spec.subject_type || current.subject_type,
      subject_id: spec.subject_id || current.subject_id,
      predicate: spec.predicate || current.predicate,
      object_type: spec.object_type || current.object_type,
      object_id: spec.object_id || current.object_id,
      claim_type: spec.claim_type || current.claim_type,
      normalized_value: spec.normalized_value || current.normalized_value,
      source_record_id: `settings-preview-${spec.source_record_id || spec.object_id || 'evidence'}`.slice(0, 120),
    }));
    setManualEvidenceSeeded(true);
  }, [manualEvidenceSeeded, materializedClaimSpecs]);
  const setManualEvidenceField = (field: keyof typeof manualEvidenceDraft, value: string) => {
    setManualEvidenceDraft((current) => ({ ...current, [field]: value }));
  };

  const triggerJob = useMutation({
    mutationFn: (jobType: string) => startJob(jobType),
    onSuccess: (data, jobType) => {
      if (data.status === 'approval_required') {
        toast(`${jobType} preview ready. Execution requires explicit approval and confirm_execute=true.`);
        return;
      }
      if (data.status === 'schema_not_ready') {
        const missingCount = data.schema_status?.missing_tables?.length ?? 0;
        toast.error(`${jobType} blocked: truth schema is not ready${missingCount ? ` (${missingCount} missing tables)` : ''}`);
        return;
      }
      if (data.dispatch_mode === 'in_process') {
        toast(`${jobType} job started in local fallback mode${data.job_id ? ` (#${data.job_id})` : ''}`);
      } else {
        toast.success(`${jobType} job started${data.job_id ? ` (#${data.job_id})` : ''}`);
      }
    },
    onError: (_, jobType) => toast.error(`Failed to start ${jobType}`),
  });
  const previewDecision = useMutation({
    mutationFn: ({ item, decision }: { item: TruthReviewItem; decision: 'approve' | 'reject' | 'needs_more_evidence' | 'do_not_merge' }) =>
      submitTruthReviewDecision(item.review_id, { decision, dry_run: true, confirm_execute: false }),
    onSuccess: (data) => {
      const suffix = data.allowed_execute ? `would mark ${data.target_status}` : data.blocked_reason || 'preview only';
      toast.success(`Decision preview: ${suffix}`);
    },
    onError: () => toast.error('Failed to preview review decision'),
  });
  const manualEvidencePreview = useMutation({
    mutationFn: () => previewTruthManualEvidence({
      ...manualEvidenceDraft,
      source_url: manualEvidenceDraft.source_url || null,
      note: manualEvidenceDraft.note || null,
    }),
    onSuccess: () => toast.success('Manual evidence preview ready'),
    onError: () => toast.error('Failed to preview manual evidence'),
  });
  const roleCorrectionPreview = useMutation({
    mutationFn: () => previewTruthRoleClaimCorrection({
      lead_id: roleClaimCorrectionPreview?.lead_id || roleSourceOverlapPilot?.lead_id || '0ff794d3ba2d',
      limit: 100,
      dry_run: true,
      confirm_execute: false,
    }),
    onSuccess: (data) => toast.success(`Role correction preview: ${data.mutations_planned.toLocaleString()} planned update${data.mutations_planned === 1 ? '' : 's'}`),
    onError: () => toast.error('Failed to preview role correction'),
  });

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-semibold text-gray-900">Data Health Dashboard</h2>

      {truthPanelAvailable && (
        <div className="bg-white rounded-lg border border-gray-200 p-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h3 className="text-sm font-medium text-gray-700">Data Truth & Confidence</h3>
              <p className="text-xs text-gray-500 mt-1">
                Claim ledger, review queue, actionability thresholds, and golden-case coverage.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                onClick={() => triggerJob.mutate('truth_materialization')}
                disabled={triggerJob.isPending}
                className="px-3 py-1.5 text-sm bg-white text-gray-700 border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-50 font-medium"
              >
                {triggerJob.isPending ? 'Queuing...' : 'Preview Ledger Backfill'}
              </button>
              <button
                onClick={() => triggerJob.mutate('truth_validation')}
                disabled={triggerJob.isPending}
                className="px-3 py-1.5 text-sm bg-gray-900 text-white rounded-lg hover:bg-gray-800 disabled:opacity-50 font-medium"
              >
                {triggerJob.isPending ? 'Queuing...' : 'Run Validation Preview'}
              </button>
            </div>
          </div>
          <div className="mt-4 grid grid-cols-2 md:grid-cols-5 gap-3">
            {[
              { label: 'Claims', value: truthClaimCount, box: 'bg-gray-50', labelClass: 'text-gray-600', valueClass: 'text-gray-700' },
              { label: 'Verified', value: truthVerifiedCount, box: 'bg-green-50', labelClass: 'text-green-600', valueClass: 'text-green-700' },
              { label: 'Conflicts', value: truthConflictCount, box: 'bg-rose-50', labelClass: 'text-rose-600', valueClass: 'text-rose-700' },
              { label: 'Open Reviews', value: truthOpenReviewCount, box: 'bg-amber-50', labelClass: 'text-amber-600', valueClass: 'text-amber-700' },
              { label: 'Golden Cases', value: truthGoldenCaseCount, box: 'bg-blue-50', labelClass: 'text-blue-600', valueClass: 'text-blue-700' },
            ].map((item) => (
              <div key={item.label} className={`rounded-lg px-3 py-2 ${item.box}`}>
                <div className={`text-[10px] uppercase ${item.labelClass}`}>{item.label}</div>
                <div className={`text-lg font-semibold ${item.valueClass}`}>{item.value.toLocaleString()}</div>
              </div>
            ))}
          </div>
          {truthHealth && (
            <div className="mt-4 rounded-lg border border-gray-100 bg-gray-50 px-3 py-3">
              <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
                <div>
                  <div className="text-xs font-semibold text-gray-800">Trust posture</div>
                  <div className="mt-1 text-xs text-gray-500">
                    {truthHealth.summary.critical_or_high_gap_count.toLocaleString()} critical/high gaps, {truthHealth.summary.planned_claims_total.toLocaleString()} dry-run claims pending.
                  </div>
                </div>
                <div className={`self-start rounded border px-2 py-1 text-xs font-medium ${severityClass(truthHealth.summary.trust_posture === 'not_ready' ? 'high' : 'low')}`}>
                  {truthHealth.summary.trust_posture.replace(/_/g, ' ')}
                </div>
              </div>
              {topTrustGaps.length > 0 && (
                <div className="mt-3 grid gap-2">
                  {topTrustGaps.map((gap) => (
                    <div key={`${gap.area}-${gap.message}`} className="rounded border border-white bg-white px-3 py-2">
                      <div className="flex items-center justify-between gap-2">
                        <div className="text-xs font-semibold text-gray-800">{gap.area.replace(/_/g, ' ')}</div>
                        <div className={`rounded border px-2 py-0.5 text-[10px] font-medium ${severityClass(gap.severity)}`}>{gap.severity}</div>
                      </div>
                      <div className="mt-1 text-xs text-gray-500">{gap.message}</div>
                    </div>
                  ))}
                </div>
              )}
              {activationChecklist.length > 0 && (
                <div className="mt-3 rounded border border-white bg-white px-3 py-2">
                  <div className="text-[10px] uppercase text-gray-500">Activation checklist</div>
                  <div className="mt-2 grid gap-2">
                    {activationChecklist.map((item) => (
                      <div key={item.step} className="flex flex-col gap-1 rounded bg-gray-50 px-2 py-2 sm:flex-row sm:items-start sm:justify-between">
                        <div>
                          <div className="text-xs font-semibold text-gray-800">{item.step.replace(/_/g, ' ')}</div>
                          <div className="mt-0.5 text-xs text-gray-500">{item.reason}</div>
                          <div className="mt-1 flex flex-wrap gap-1 text-[10px] text-gray-500">
                            {item.approval_required && (
                              <span className="rounded border border-amber-100 bg-amber-50 px-1.5 py-0.5 text-amber-700">approval required</span>
                            )}
                            <span className="rounded border border-gray-100 bg-white px-1.5 py-0.5">
                              mutations planned: {item.mutations_planned.toLocaleString()}
                            </span>
                          </div>
                        </div>
                        <div className={`shrink-0 rounded border px-2 py-0.5 text-[10px] font-medium ${severityClass(item.status === 'blocked' || item.status === 'approval_required' ? 'high' : item.status === 'needs_review' || item.status === 'manual_review' ? 'medium' : 'low')}`}>
                          {item.status.replace(/_/g, ' ')}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {truthSchemaStatus && (
                <div className="mt-3 rounded border border-white bg-white px-3 py-2">
                  <div className="grid gap-2 sm:grid-cols-3">
                    <div>
                      <div className="text-[10px] uppercase text-gray-500">Current revision</div>
                      <div className="text-xs font-semibold text-gray-800">{truthSchemaStatus.current_revision || 'unknown'}</div>
                    </div>
                    <div>
                      <div className="text-[10px] uppercase text-gray-500">Expected revision</div>
                      <div className="text-xs font-semibold text-gray-800">{truthSchemaStatus.expected_revision}</div>
                    </div>
                    <div>
                      <div className="text-[10px] uppercase text-gray-500">Schema status</div>
                      <div className="text-xs font-semibold text-gray-800">{(truthSchemaStatus.revision_status || 'unknown').replace(/_/g, ' ')}</div>
                    </div>
                  </div>
                  {missingTruthTables.length > 0 && (
                    <div className="mt-2 text-xs text-gray-500">
                      Missing tables: {missingTruthTables.join(', ')}
                    </div>
                  )}
                </div>
              )}
              {truthActivationPacket && (
                <div className="mt-3 rounded border border-white bg-white px-3 py-2">
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                    <div>
                      <div className="text-[10px] uppercase text-gray-500">Activation packet</div>
                      <div className="text-xs font-semibold text-gray-800">
                        {truthActivationPacket.verdict.replace(/_/g, ' ')}
                      </div>
                      <div className="mt-1 text-xs text-gray-500">
                        Business use {truthActivationPacket.business_use_allowed ? 'allowed' : 'blocked'}; {truthActivationPacket.approval_steps.length.toLocaleString()} approval step{truthActivationPacket.approval_steps.length === 1 ? '' : 's'}.
                      </div>
                    </div>
                    <div className={`shrink-0 rounded border px-2 py-0.5 text-[10px] font-medium ${severityClass(truthActivationPacket.business_use_allowed ? 'low' : 'high')}`}>
                      {truthActivationPacket.approval_required ? 'approval required' : 'no approval needed'}
                    </div>
                  </div>
                  {activationNextSteps.length > 0 && (
                    <div className="mt-2 grid gap-1">
                      {activationNextSteps.map((step) => (
                        <div key={step.step} className="rounded bg-gray-50 px-2 py-1">
                          <div className="flex items-center justify-between gap-2">
                            <div className="text-xs font-semibold text-gray-800">{step.step.replace(/_/g, ' ')}</div>
                            {step.mutates_data && (
                              <span className="rounded border border-amber-100 bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700">
                                approval
                              </span>
                            )}
                          </div>
                          <div className="mt-0.5 break-all font-mono text-[10px] text-gray-500">{step.command}</div>
                        </div>
                      ))}
                    </div>
                  )}
                  {activationClaimReadiness && (
                    <div className="mt-2 rounded bg-gray-50 px-2 py-1">
                      <div className="text-[10px] uppercase text-gray-500">Ledger readiness</div>
                      <div className="mt-1 grid grid-cols-1 gap-1 sm:grid-cols-3">
                        <div className={`rounded border bg-white px-2 py-1 ${activationClaimReadiness.has_materialized_claims ? 'border-emerald-100' : 'border-rose-100'}`}>
                          <div className="text-[10px] uppercase text-gray-500">Claims</div>
                          <div className="text-xs font-semibold text-gray-800">{activationClaimReadiness.claim_count.toLocaleString()}</div>
                        </div>
                        <div className={`rounded border bg-white px-2 py-1 ${activationClaimReadiness.has_verified_claims ? 'border-emerald-100' : 'border-rose-100'}`}>
                          <div className="text-[10px] uppercase text-gray-500">Verified</div>
                          <div className="text-xs font-semibold text-gray-800">{activationClaimReadiness.verified_claim_count.toLocaleString()}</div>
                        </div>
                        <div className={`rounded border bg-white px-2 py-1 ${activationClaimReadiness.has_no_critical_or_high_gaps ? 'border-emerald-100' : 'border-rose-100'}`}>
                          <div className="text-[10px] uppercase text-gray-500">Critical/high gaps</div>
                          <div className="text-xs font-semibold text-gray-800">{activationClaimReadiness.critical_or_high_gap_count.toLocaleString()}</div>
                        </div>
                      </div>
                    </div>
                  )}
                  {activationVerificationFrontier && (
                    <div className="mt-2 rounded bg-gray-50 px-2 py-1">
                      <div className="text-[10px] uppercase text-gray-500">Activation frontier</div>
                      <div className="mt-1 grid grid-cols-1 gap-1 sm:grid-cols-4">
                        <div className="rounded border border-gray-100 bg-white px-2 py-1">
                          <div className="text-[10px] uppercase text-gray-500">Candidates</div>
                          <div className="text-xs font-semibold text-gray-800">{activationVerificationFrontier.verification_candidate_count.toLocaleString()}</div>
                        </div>
                        <div className="rounded border border-gray-100 bg-white px-2 py-1">
                          <div className="text-[10px] uppercase text-gray-500">Source-ready</div>
                          <div className="text-xs font-semibold text-gray-800">{(activationVerificationFrontier.current_ledger.source_ready_fact_group_count ?? 0).toLocaleString()}</div>
                        </div>
                        <div className="rounded border border-gray-100 bg-white px-2 py-1">
                          <div className="text-[10px] uppercase text-gray-500">Below verified</div>
                          <div className="text-xs font-semibold text-gray-800">{activationVerificationFrontier.source_ready_below_verified_count.toLocaleString()}</div>
                        </div>
                        <div className="rounded border border-gray-100 bg-white px-2 py-1">
                          <div className="text-[10px] uppercase text-gray-500">Single-source gaps</div>
                          <div className="text-xs font-semibold text-gray-800">{activationVerificationFrontier.single_source_gap_count.toLocaleString()}</div>
                        </div>
                      </div>
                      <div className="mt-1 flex flex-wrap gap-1 text-[10px] text-gray-600">
                        <span className="rounded border border-gray-100 bg-white px-1.5 py-0.5">
                          one-source clears: {(activationVerificationFrontier.single_source_upgrade_would_verify_count ?? 0).toLocaleString()}
                        </span>
                        <span className="rounded border border-gray-100 bg-white px-1.5 py-0.5">
                          bundle clears: {(activationVerificationFrontier.bundle_upgrade_would_verify_count ?? 0).toLocaleString()}
                        </span>
                        <span className="rounded border border-gray-100 bg-white px-1.5 py-0.5">
                          HPM seeds: {(activationVerificationFrontier.manager_next_source_seed_count ?? 0).toLocaleString()}
                        </span>
                        <span className="rounded border border-gray-100 bg-white px-1.5 py-0.5">
                          operator seeds: {(activationVerificationFrontier.operator_second_source_seed_count ?? 0).toLocaleString()}
                        </span>
                      </div>
                      {activationVerificationFrontier.business_use_blocker && (
                        <div className="mt-2 rounded border border-amber-100 bg-amber-50 px-2 py-1 text-xs text-amber-800">
                          {activationVerificationFrontier.business_use_blocker}
                        </div>
                      )}
                      {activationVerificationFrontier.next_preview_command && (
                        <div className="mt-1 break-all font-mono text-[10px] text-gray-500">
                          {activationVerificationFrontier.next_preview_command}
                        </div>
                      )}
                    </div>
                  )}
                  {truthActivationPacket.rollback.offline_rollback_command && (
                    <div className="mt-2 break-all rounded bg-gray-50 px-2 py-1 font-mono text-[10px] text-gray-500">
                      Rollback preview: {truthActivationPacket.rollback.offline_rollback_command}
                    </div>
                  )}
                  {activationRefreshJobs.length > 0 && (
                    <div className="mt-2 rounded bg-gray-50 px-2 py-1">
                      <div className="text-[10px] uppercase text-gray-500">Next source refresh jobs</div>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {activationRefreshJobs.map((job) => (
                          <span key={`${job.job_type}-${job.reason}`} className="rounded border border-gray-100 bg-white px-1.5 py-0.5 text-[10px] text-gray-600">
                            {String(job.job_type || 'source').replace(/_/g, ' ')}{job.blocked ? ' (blocked)' : job.approval_required ? ' (approval)' : ''}
                            {job.sources?.[0]?.source_name ? `: ${String(job.sources[0].source_name).replace(/_/g, ' ')}` : ''}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
              {truthCompletionAudit && (
                <div className="mt-3 rounded border border-white bg-white px-3 py-2">
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                    <div>
                      <div className="text-[10px] uppercase text-gray-500">Completion audit</div>
                      <div className="text-xs font-semibold text-gray-800">
                        {truthCompletionAudit.completion_status.replace(/_/g, ' ')}
                      </div>
                      <div className="mt-1 text-xs text-gray-500">
                        {(truthCompletionAudit.artifact_summary?.satisfied ?? 0).toLocaleString()} of {(truthCompletionAudit.artifact_summary?.total ?? 0).toLocaleString()} artifacts; {(truthCompletionAudit.runtime_blockers ?? []).length.toLocaleString()} runtime blocker{(truthCompletionAudit.runtime_blockers ?? []).length === 1 ? '' : 's'}.
                      </div>
                    </div>
                    <div className={`shrink-0 rounded border px-2 py-0.5 text-[10px] font-medium ${truthCompletionAudit.completion_status === 'complete' ? severityClass('low') : severityClass('high')}`}>
                      {truthCompletionAudit.completion_status === 'complete' ? 'complete' : 'blocked'}
                    </div>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-1">
                    {Object.entries(completionStatusCounts).map(([status, count]) => (
                      <span key={status} className="rounded border border-gray-100 bg-gray-50 px-1.5 py-0.5 text-[10px] text-gray-600">
                        {status.replace(/_/g, ' ')}: {count.toLocaleString()}
                      </span>
                    ))}
                  </div>
                  {completionBlockedItems.length > 0 && (
                    <div className="mt-2 space-y-1">
                      {completionBlockedItems.map((item) => (
                        <div key={item.requirement} className="rounded bg-gray-50 px-2 py-1 text-[10px] text-gray-600">
                          <span className="font-medium text-gray-800">{item.status.replace(/_/g, ' ')}</span>
                          {': '}{item.requirement}
                        </div>
                      ))}
                    </div>
                  )}
                  {completionOperatorGapSummary && (
                    <div className="mt-2 rounded border border-orange-100 bg-orange-50 px-2 py-1.5 text-[10px] text-orange-800">
                      <div>
                        Runtime operator strict gaps: {Number(completionOperatorGapSummary.broad_source_ready_not_strict_count ?? 0).toLocaleString()} broad-only / {Number(completionOperatorGapSummary.strict_ready_proposal_count ?? 0).toLocaleString()} strict-ready.
                      </div>
                      {completionOperatorGapCandidates.slice(0, 2).map((candidate) => (
                        <div key={`${String(candidate.bbl || '')}-${String(candidate.address || '')}`} className="mt-1 text-orange-700">
                          {String(candidate.address || 'operator seed')}: {String(candidate.strict_manager_gap_status || 'needs source').replace(/_/g, ' ')}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {truthSourceSummary && (
                <div className="mt-3 rounded border border-white bg-white px-3 py-2">
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                    <div>
                      <div className="text-[10px] uppercase text-gray-500">Source audit</div>
                      <div className="text-xs font-semibold text-gray-800">
                        {truthSourceSummary.operational.toLocaleString()} of {truthSourceSummary.total_sources.toLocaleString()} sources operational
                      </div>
                    </div>
                    <div className="text-xs text-gray-500">
                      {truthSourceGaps.length.toLocaleString()} gap{truthSourceGaps.length === 1 ? '' : 's'}
                    </div>
                  </div>
                  {truthSourceGaps.length > 0 && (
                    <>
                      <div className="mt-2 grid grid-cols-3 gap-2">
                        <div className="rounded bg-gray-50 px-2 py-1">
                          <div className="text-[10px] uppercase text-gray-500">Stale</div>
                          <div className="text-xs font-semibold text-gray-800">{staleSourceCount.toLocaleString()}</div>
                        </div>
                        <div className="rounded bg-gray-50 px-2 py-1">
                          <div className="text-[10px] uppercase text-gray-500">No ingest</div>
                          <div className="text-xs font-semibold text-gray-800">{noRecentSourceCount.toLocaleString()}</div>
                        </div>
                        <div className="rounded bg-gray-50 px-2 py-1">
                          <div className="text-[10px] uppercase text-gray-500">Schema</div>
                          <div className="text-xs font-semibold text-gray-800">{missingSourceSchemaCount.toLocaleString()}</div>
                        </div>
                      </div>
                      <div className="mt-2 text-xs text-gray-500">
                        Needs attention: {truthSourceGaps.slice(0, 3).map((gap) => String(gap.source_name || gap.table_name || gap.status || 'source')).join(', ')}
                      </div>
                      {truthSourceRefreshPlan && (
                        <div className="mt-2 text-xs text-gray-500">
                          Refresh plan: {truthSourceRefreshPlan.summary.refreshable_job_count.toLocaleString()} refreshable job{truthSourceRefreshPlan.summary.refreshable_job_count === 1 ? '' : 's'}, {truthSourceRefreshPlan.summary.blocked_job_count.toLocaleString()} blocked{truthSourceRefreshPlan.summary.non_refreshable_gap_count ? `, ${truthSourceRefreshPlan.summary.non_refreshable_gap_count.toLocaleString()} tracked manually` : ''}.
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}
              {adjudication && (
                <div className="mt-3 rounded border border-white bg-white px-3 py-2">
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                    <div>
                      <div className="text-[10px] uppercase text-gray-500">Claim adjudication</div>
                      <div className="text-xs font-semibold text-gray-800">
                        {truthVerificationCandidateCount.toLocaleString()} verification candidate{truthVerificationCandidateCount === 1 ? '' : 's'}
                      </div>
                      <div className="mt-1 text-xs text-gray-500">
                        {(adjudication.fact_group_count ?? 0).toLocaleString()} fact groups sampled; independent support, freshness, and no contradictions required.
                      </div>
                    </div>
                    <div className={`shrink-0 rounded border px-2 py-0.5 text-[10px] font-medium ${adjudication.mutations_planned === 0 ? severityClass('low') : severityClass('high')}`}>
                      {adjudication.dry_run ? 'dry run' : 'execution'}
                    </div>
                  </div>
                  {adjudicationBlockerEntries.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {adjudicationBlockerEntries.map(([blocker, count]) => (
                        <span key={blocker} className="rounded border border-amber-100 bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700">
                          {blocker.replace(/_/g, ' ')}: {count.toLocaleString()}
                        </span>
                      ))}
                    </div>
                  )}
                  {adjudicationSourceCoverage && (
                    <div className="mt-2 rounded border border-gray-100 bg-gray-50 px-2 py-2">
                      <div className="grid gap-2 sm:grid-cols-3">
                        <div>
                          <div className="text-[10px] uppercase text-gray-500">Multi-source facts</div>
                          <div className="text-xs font-semibold text-gray-800">{adjudicationSourceCoverage.multi_source_fact_group_count.toLocaleString()}</div>
                        </div>
                        <div>
                          <div className="text-[10px] uppercase text-gray-500">Single-source facts</div>
                          <div className="text-xs font-semibold text-gray-800">{adjudicationSourceCoverage.single_source_fact_group_count.toLocaleString()}</div>
                        </div>
                        <div>
                          <div className="text-[10px] uppercase text-gray-500">Max sources</div>
                          <div className="text-xs font-semibold text-gray-800">{adjudicationSourceCoverage.max_supporting_source_count.toLocaleString()}</div>
                        </div>
                      </div>
                      {adjudicationSourceCoverage.verification_blocker && (
                        <div className="mt-2 text-xs text-amber-700">{adjudicationSourceCoverage.verification_blocker}</div>
                      )}
                      {adjudicationTopSources.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1">
                          {adjudicationTopSources.map((source) => (
                            <span key={source.source_name} className="rounded border border-gray-100 bg-white px-1.5 py-0.5 text-[10px] text-gray-600">
                              {source.source_name.replace(/_/g, ' ')}: {source.fact_group_count.toLocaleString()}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                  {ledgerSourceOverlap && (
                    <div className="mt-2 rounded border border-gray-100 bg-gray-50 px-2 py-2">
                      <div className="grid gap-2 sm:grid-cols-6">
                        <div>
                          <div className="text-[10px] uppercase text-gray-500">Ledger facts</div>
                          <div className="text-xs font-semibold text-gray-800">{ledgerSourceOverlap.total_fact_group_count.toLocaleString()}</div>
                        </div>
                        <div>
                          <div className="text-[10px] uppercase text-gray-500">Ledger multi-source</div>
                          <div className="text-xs font-semibold text-gray-800">{ledgerSourceOverlap.multi_source_fact_group_count.toLocaleString()}</div>
                        </div>
                        <div>
                          <div className="text-[10px] uppercase text-gray-500">Source-ready</div>
                          <div className="text-xs font-semibold text-gray-800">{ledgerSourceOverlap.source_ready_fact_group_count.toLocaleString()}</div>
                        </div>
                        <div>
                          <div className="text-[10px] uppercase text-gray-500">Max ledger sources</div>
                          <div className="text-xs font-semibold text-gray-800">{ledgerSourceOverlap.max_supporting_source_count.toLocaleString()}</div>
                        </div>
                      </div>
                      {ledgerSourceOverlap.business_readiness_blocker && (
                        <div className="mt-2 text-xs text-amber-700">{ledgerSourceOverlap.business_readiness_blocker}</div>
                      )}
                      {ledgerTopSources.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1">
                          {ledgerTopSources.map((source) => (
                            <span key={source.source_name} className="rounded border border-gray-100 bg-white px-1.5 py-0.5 text-[10px] text-gray-600">
                              {source.source_name.replace(/_/g, ' ')}: {source.fact_group_count.toLocaleString()}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                  {roleSourceOverlapPilot && (
                    <div className="mt-2 rounded border border-gray-100 bg-gray-50 px-2 py-2">
                      <div className="grid gap-2 sm:grid-cols-4">
                        <div>
                          <div className="text-[10px] uppercase text-gray-500">Role pilot</div>
                          <div className="text-xs font-semibold text-gray-800">
                            {(roleSourceOverlapPilot.scope_relationship_count ?? roleSourceOverlapPilot.sampled_relationship_count).toLocaleString()}
                          </div>
                          <div className="text-[10px] text-gray-500">
                            {roleSourceOverlapPilot.sampled_relationship_count.toLocaleString()} sampled
                          </div>
                        </div>
                        <div>
                          <div className="text-[10px] uppercase text-gray-500">If materialized</div>
                          <div className="text-xs font-semibold text-gray-800">{roleSourceOverlapPilot.source_ready_if_materialized_count.toLocaleString()} source-ready</div>
                        </div>
                        <div>
                          <div className="text-[10px] uppercase text-gray-500">Manager-ready</div>
                          <div className="text-xs font-semibold text-gray-800">{roleSourceOverlapPilot.management_source_ready_if_materialized_count.toLocaleString()}</div>
                        </div>
                        <div>
                          <div className="text-[10px] uppercase text-gray-500">Agent-ready</div>
                          <div className="text-xs font-semibold text-gray-800">{roleSourceOverlapPilot.registered_agent_source_ready_if_materialized_count.toLocaleString()}</div>
                        </div>
                      </div>
                      {roleSourceOverlapPilot.business_readiness_note && (
                        <div className="mt-2 text-xs text-amber-700">{roleSourceOverlapPilot.business_readiness_note}</div>
                      )}
                      {roleSourceOverlapPilot.identity_policy?.warning && (
                        <div className="mt-1 text-xs text-gray-600">{roleSourceOverlapPilot.identity_policy.warning}</div>
                      )}
                      {roleSourceOverlapPilot.claim_count_by_predicate_if_materialized && (
                        <div className="mt-2 flex flex-wrap gap-1">
                          {Object.entries(roleSourceOverlapPilot.claim_count_by_predicate_if_materialized).map(([predicate, count]) => (
                            <span key={predicate} className="rounded border border-gray-100 bg-white px-1.5 py-0.5 text-[10px] text-gray-600">
                              {predicate.replace(/_/g, ' ')}: {Number(count).toLocaleString()}
                            </span>
                          ))}
                        </div>
                      )}
                      {roleSourceOverlapSamples.length > 0 && (
                        <div className="mt-2 divide-y divide-gray-100 rounded border border-gray-100 bg-white">
                          {roleSourceOverlapSamples.map((sample) => (
                            <div key={`${sample.fact_key.subject_id}-${sample.fact_key.object_id}-${sample.fact_key.predicate}`} className="px-2 py-2">
                              <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                                <div className="min-w-0">
                                  <div className="truncate text-xs font-medium text-gray-800">
                                    {sample.fact_key.predicate?.replace(/_/g, ' ')} - {sample.fact_key.object_id}
                                  </div>
                                  <div className="text-[10px] text-gray-500">
                                    {sample.supporting_sources_if_materialized.map((source) => source.replace(/_/g, ' ')).join(' + ')}
                                  </div>
                                </div>
                                <div className={`rounded border px-1.5 py-0.5 text-[10px] ${sample.source_ready_if_materialized ? 'border-emerald-100 bg-emerald-50 text-emerald-700' : 'border-gray-100 bg-gray-50 text-gray-600'}`}>
                                  {sample.source_ready_if_materialized ? 'source-ready' : 'blocked'}
                                </div>
                              </div>
                              <div className="mt-1 text-[10px] text-gray-600">{sample.safe_action}</div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                  {scaledRoleSourceOverlap && (
                    <div className="mt-2 rounded border border-gray-100 bg-white px-2 py-2">
                      <div className="grid gap-2 sm:grid-cols-4">
                        <div>
                          <div className="text-[10px] uppercase text-gray-500">Scaled role scan</div>
                          <div className="text-xs font-semibold text-gray-800">
                            {scaledRoleSourceOverlap.scanned_relationship_count.toLocaleString()}
                          </div>
                          <div className="text-[10px] text-gray-500">
                            {scaledRoleSourceOverlap.source_ready_batch_count.toLocaleString()} ready batch{scaledRoleSourceOverlap.source_ready_batch_count === 1 ? '' : 'es'}
                          </div>
                        </div>
                        <div>
                          <div className="text-[10px] uppercase text-gray-500">Source-ready</div>
                          <div className="text-xs font-semibold text-gray-800">{scaledRoleSourceOverlap.source_ready_if_materialized_count.toLocaleString()}</div>
                        </div>
                        <div>
                          <div className="text-[10px] uppercase text-gray-500">Manager-ready</div>
                          <div className="text-xs font-semibold text-gray-800">{scaledRoleSourceOverlap.management_source_ready_if_materialized_count.toLocaleString()}</div>
                        </div>
                        <div>
                          <div className="text-[10px] uppercase text-gray-500">Agent-ready</div>
                          <div className="text-xs font-semibold text-gray-800">{scaledRoleSourceOverlap.registered_agent_source_ready_if_materialized_count.toLocaleString()}</div>
                        </div>
                      </div>
                      {scaledRoleSourceOverlap.business_readiness_note && (
                        <div className="mt-2 text-xs text-gray-600">{scaledRoleSourceOverlap.business_readiness_note}</div>
                      )}
                      {scaledRoleSourceOverlapBatches.length > 0 && (
                        <div className="mt-2 divide-y divide-gray-100 rounded border border-gray-100 bg-gray-50">
                          {scaledRoleSourceOverlapBatches.map((batch) => (
                            <div key={batch.lead_id} className="px-2 py-2">
                              <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                                <div className="min-w-0">
                                  <div className="truncate text-xs font-medium text-gray-800">
                                    {batch.lead_name || batch.lead_id}
                                  </div>
                                  <div className="text-[10px] text-gray-500">
                                    {batch.scope_relationship_count.toLocaleString()} relationships / {batch.source_ready_if_materialized_count.toLocaleString()} source-ready
                                  </div>
                                </div>
                                <div className={`rounded border px-1.5 py-0.5 text-[10px] ${batch.management_source_ready_if_materialized_count > 0 ? 'border-emerald-100 bg-emerald-50 text-emerald-700' : 'border-amber-100 bg-amber-50 text-amber-700'}`}>
                                  {batch.management_source_ready_if_materialized_count.toLocaleString()} manager-ready
                                </div>
                              </div>
                              <div className="mt-1 flex flex-wrap gap-1">
                                {Object.entries(batch.claim_count_by_predicate_if_materialized ?? {}).map(([predicate, count]) => (
                                  <span key={predicate} className="rounded border border-gray-100 bg-white px-1.5 py-0.5 text-[10px] text-gray-600">
                                    {predicate.replace(/_/g, ' ')}: {Number(count).toLocaleString()}
                                  </span>
                                ))}
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                  {managerSourceBridgePreview && (
                    <div className="mt-2 rounded border border-rose-100 bg-rose-50 px-2 py-2">
                      <div className="grid gap-2 sm:grid-cols-4">
                        <div>
                          <div className="text-[10px] uppercase text-rose-700">Manager bridge</div>
                          <div className="text-xs font-semibold text-gray-800">{managerSourceBridgePreview.relationship_count.toLocaleString()}</div>
                          <div className="text-[10px] text-gray-600">relationships checked</div>
                        </div>
                        <div>
                          <div className="text-[10px] uppercase text-rose-700">Manager-role rows</div>
                          <div className="text-xs font-semibold text-gray-800">{managerSourceBridgePreview.current_manager_role_relationship_count.toLocaleString()}</div>
                        </div>
                        <div>
                          <div className="text-[10px] uppercase text-rose-700">HPD management matches</div>
                          <div className="text-xs font-semibold text-gray-800">{managerSourceBridgePreview.hpd_management_company_strict_match_count.toLocaleString()}</div>
                        </div>
                        <div>
                          <div className="text-[10px] uppercase text-rose-700">Manager-ready</div>
                          <div className="text-xs font-semibold text-gray-800">{managerSourceBridgePreview.manager_source_ready_if_materialized_count.toLocaleString()}</div>
                        </div>
                      </div>
                      {managerSourceBridgePreview.business_readiness_note && (
                        <div className="mt-2 text-xs text-rose-700">{managerSourceBridgePreview.business_readiness_note}</div>
                      )}
                      <div className="mt-2 flex flex-wrap gap-1">
                        <span className="rounded border border-rose-100 bg-white px-1.5 py-0.5 text-[10px] text-rose-700">
                          agent bridge: {managerSourceBridgePreview.registered_agent_bridge_count.toLocaleString()}
                        </span>
                        <span className="rounded border border-rose-100 bg-white px-1.5 py-0.5 text-[10px] text-rose-700">
                          site manager rows: {managerSourceBridgePreview.hpd_site_manager_row_count.toLocaleString()}
                        </span>
                        {(managerSourceBridgePreview.blocking_reasons ?? []).slice(0, 4).map((reason) => (
                          <span key={reason} className="rounded border border-rose-100 bg-white px-1.5 py-0.5 text-[10px] text-rose-700">
                            {reason.replace(/_/g, ' ')}
                          </span>
                        ))}
                      </div>
                      {managerSourceBridgeSamples.length > 0 && (
                        <div className="mt-2 divide-y divide-rose-100 rounded border border-rose-100 bg-white">
                          {managerSourceBridgeSamples.map((sample) => (
                            <div key={`${sample.bbl}-${sample.contact_type}-${sample.display_name || sample.verification_key || ''}`} className="px-2 py-2">
                              <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                                <div className="min-w-0">
                                  <div className="truncate text-xs font-medium text-gray-800">
                                    {sample.contact_type}: {sample.display_name || sample.verification_key || sample.bbl}
                                  </div>
                                  <div className="text-[10px] text-gray-500">
                                    {sample.bbl} / {sample.hpd_predicate?.replace(/_/g, ' ') || 'no predicate'}
                                  </div>
                                </div>
                                <div className={`rounded border px-1.5 py-0.5 text-[10px] ${sample.role_matches_building_management ? 'border-emerald-100 bg-emerald-50 text-emerald-700' : 'border-amber-100 bg-amber-50 text-amber-700'}`}>
                                  {sample.role_matches_building_management ? 'role aligned' : 'not manager proof'}
                                </div>
                              </div>
                              {sample.safe_action && (
                                <div className="mt-1 text-[10px] text-gray-500">{sample.safe_action}</div>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                  {managerExternalSourcePreview && (
                    <div className="mt-2 rounded border border-cyan-100 bg-cyan-50 px-2 py-2">
                      <div className="grid gap-2 sm:grid-cols-4">
                        <div>
                          <div className="text-[10px] uppercase text-cyan-700">External manager sources</div>
                          <div className="text-xs font-semibold text-gray-800">{managerExternalSourcePreview.matched_evidence_candidate_count.toLocaleString()}</div>
                          <div className="text-[10px] text-gray-600">matched evidence candidates</div>
                        </div>
                        <div>
                          <div className="text-[10px] uppercase text-cyan-700">Clean exact claims</div>
                          <div className="text-xs font-semibold text-gray-800">{managerExternalSourcePreview.clean_exact_claim_count.toLocaleString()}</div>
                        </div>
                        <div>
                          <div className="text-[10px] uppercase text-cyan-700">Source-ready if recorded</div>
                          <div className="text-xs font-semibold text-gray-800">{managerExternalSourcePreview.source_ready_if_recorded_count.toLocaleString()}</div>
                        </div>
                        <div>
                          <div className="text-[10px] uppercase text-cyan-700">Independent families</div>
                          <div className="text-xs font-semibold text-gray-800">{managerExternalSourcePreview.independent_source_ready_if_recorded_count.toLocaleString()}</div>
                        </div>
                        <div>
                          <div className="text-[10px] uppercase text-cyan-700">Manager-proof overlap</div>
                          <div className="text-xs font-semibold text-gray-800">{(managerExternalSourcePreview.strict_manager_source_ready_if_recorded_count ?? 0).toLocaleString()}</div>
                        </div>
                        <div>
                          <div className="text-[10px] uppercase text-cyan-700">New relationship candidates</div>
                          <div className="text-xs font-semibold text-gray-800">{(managerExternalSourcePreview.new_relationship_candidate_count ?? 0).toLocaleString()}</div>
                        </div>
                      </div>
                      {typeof managerExternalSourcePreview.policy?.freshness_warning === 'string' && (
                        <div className="mt-2 text-xs text-cyan-700">{managerExternalSourcePreview.policy.freshness_warning}</div>
                      )}
                      <div className="mt-2 flex flex-wrap gap-1">
                        <span className="rounded border border-cyan-100 bg-white px-1.5 py-0.5 text-[10px] text-cyan-700">
                          dry run
                        </span>
                        <span className="rounded border border-cyan-100 bg-white px-1.5 py-0.5 text-[10px] text-cyan-700">
                          review required: {managerExternalSourcePreview.review_required_count.toLocaleString()}
                        </span>
                        <span className="rounded border border-cyan-100 bg-white px-1.5 py-0.5 text-[10px] text-cyan-700">
                          mutations planned: {managerExternalSourcePreview.mutations_planned.toLocaleString()}
                        </span>
                      </div>
                      {managerExternalClaimGroups.length > 0 && (
                        <div className="mt-2 divide-y divide-cyan-100 rounded border border-cyan-100 bg-white">
                          {managerExternalClaimGroups.map((group) => (
                            <div key={String(group.fact_key.object_id || group.address || '')} className="px-2 py-2">
                              <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                                <div className="min-w-0">
                                  <div className="truncate text-xs font-medium text-gray-800">
                                    {group.address || group.fact_key.object_id}
                                  </div>
                                  <div className="text-[10px] text-gray-500">
                                    {group.fact_key.object_id} / local role: {group.building_management_role || 'unknown'}
                                  </div>
                                </div>
                                <div className={`rounded border px-1.5 py-0.5 text-[10px] ${group.strict_manager_source_ready_if_recorded ? 'border-emerald-100 bg-emerald-50 text-emerald-700' : group.independent_source_ready_if_recorded ? 'border-cyan-100 bg-cyan-50 text-cyan-700' : 'border-amber-100 bg-amber-50 text-amber-700'}`}>
                                  {group.strict_manager_source_ready_if_recorded ? 'manager-proof overlap' : group.independent_source_ready_if_recorded ? 'source-ready after review' : 'needs source'}
                                </div>
                              </div>
                              <div className="mt-1 flex flex-wrap gap-1">
                                {group.supporting_sources_if_recorded.map((source) => (
                                  <span key={source} className="rounded border border-cyan-100 bg-cyan-50 px-1.5 py-0.5 text-[10px] text-cyan-700">
                                    {source.replace(/_/g, ' ')}
                                  </span>
                                ))}
                              </div>
                              {group.safe_action && (
                                <div className="mt-1 text-[10px] text-gray-500">{group.safe_action}</div>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                      {managerExternalNewRelationshipCandidates.length > 0 && (
                        <div className="mt-2 rounded border border-amber-100 bg-amber-50">
                          <div className="border-b border-amber-100 px-2 py-1.5 text-[10px] text-amber-800">
                            Source-backed new relationships are review leads, not current-ledger source overlap.
                          </div>
                          <div className="divide-y divide-amber-100">
                            {managerExternalNewRelationshipCandidates.map((candidate) => {
                              const localBuilding = candidate.local_building_match || {};
                              const localAddress = typeof localBuilding.address === 'string' ? localBuilding.address : candidate.local_address;
                              const localBbl = typeof localBuilding.bbl === 'string' ? localBuilding.bbl : undefined;
                              return (
                                <div key={String(candidate.candidate_id || candidate.source_record_id || localAddress || '')} className="px-2 py-2">
                                  <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                                    <div className="min-w-0">
                                      <div className="truncate text-xs font-medium text-gray-800">
                                        {localAddress || candidate.external_address || 'Source-backed relationship'}
                                      </div>
                                      <div className="text-[10px] text-gray-500">
                                        {localBbl || 'local building'} / {candidate.source_name?.replace(/_/g, ' ') || 'external source'}
                                      </div>
                                    </div>
                                    <div className="rounded border border-amber-100 bg-white px-1.5 py-0.5 text-[10px] text-amber-700">
                                      needs relationship review
                                    </div>
                                  </div>
                                  {candidate.safe_action && (
                                    <div className="mt-1 text-[10px] text-gray-600">{candidate.safe_action}</div>
                                  )}
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      )}
                      {managerExternalBatchPreview && (
                        <div className="mt-2 rounded border border-cyan-100 bg-white px-2 py-2">
                          <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                            <div>
                              <div className="text-[10px] uppercase text-cyan-700">Manual evidence batch</div>
                              <div className="text-xs font-semibold text-gray-800">
                                {managerExternalBatchPreview.template_count.toLocaleString()} templates / {managerExternalBatchPreview.planned_upsert_count.toLocaleString()} planned upserts
                              </div>
                              <div className="mt-1 text-[10px] text-gray-600">
                                {managerExternalBatchPreview.claim_group_count.toLocaleString()} source-ready claim groups; address-review excluded: {managerExternalBatchPreview.excluded_address_review_candidate_count.toLocaleString()}
                              </div>
                            </div>
                            <div className={`rounded border px-1.5 py-0.5 text-[10px] ${managerExternalBatchPreview.allowed_execute ? 'border-amber-100 bg-amber-50 text-amber-700' : 'border-cyan-100 bg-cyan-50 text-cyan-700'}`}>
                              {managerExternalBatchPreview.allowed_execute ? 'execute enabled' : 'preview only'}
                            </div>
                          </div>
                          {managerExternalBatchPreview.source_names.length > 0 && (
                            <div className="mt-2 flex flex-wrap gap-1">
                              {managerExternalBatchPreview.source_names.map((source) => (
                                <span key={source} className="rounded border border-cyan-100 bg-cyan-50 px-1.5 py-0.5 text-[10px] text-cyan-700">
                                  {source.replace(/_/g, ' ')}
                                </span>
                              ))}
                            </div>
                          )}
                          {managerExternalBatchPreview.recommended_strict_manager_proof_batch && (
                            <div className="mt-2 rounded border border-emerald-100 bg-emerald-50 px-2 py-1.5">
                              <div className="text-[10px] uppercase text-emerald-700">Recommended strict packet</div>
                              <div className="text-xs font-semibold text-gray-800">
                                {managerExternalBatchPreview.recommended_strict_manager_proof_batch.template_count.toLocaleString()} templates / {managerExternalBatchPreview.recommended_strict_manager_proof_batch.planned_upsert_count.toLocaleString()} planned upserts
                              </div>
                              <div className="mt-1 text-[10px] text-gray-600">
                                {managerExternalBatchPreview.recommended_strict_manager_proof_batch.claim_group_count.toLocaleString()} manager-proof claim groups
                              </div>
                              {managerExternalBatchPreview.recommended_strict_manager_proof_batch.manager_proof_source_families && managerExternalBatchPreview.recommended_strict_manager_proof_batch.manager_proof_source_families.length > 0 && (
                                <div className="mt-2">
                                  <div className="text-[10px] font-semibold uppercase text-emerald-700">Manager-proof families</div>
                                  <div className="mt-1 flex flex-wrap gap-1">
                                    {managerExternalBatchPreview.recommended_strict_manager_proof_batch.manager_proof_source_families.map((family) => (
                                      <span key={family} className="rounded border border-emerald-100 bg-white px-1.5 py-0.5 text-[10px] text-emerald-700">
                                        {family.replace(/_/g, ' ')}
                                      </span>
                                    ))}
                                  </div>
                                </div>
                              )}
                              {managerExternalBatchPreview.recommended_strict_manager_proof_batch.source_families && managerExternalBatchPreview.recommended_strict_manager_proof_batch.source_families.length > 0 && (
                                <div className="mt-1 text-[10px] text-gray-500">
                                  Source families reviewed: {managerExternalBatchPreview.recommended_strict_manager_proof_batch.source_families.map((family) => family.replace(/_/g, ' ')).join(', ')}
                                </div>
                              )}
                              {managerExternalBatchPreview.recommended_strict_manager_proof_batch.rollback_preview && (
                                <div className="mt-1 text-[10px] text-gray-600">
                                  Rollback estimate: {managerExternalBatchPreview.recommended_strict_manager_proof_batch.rollback_preview.estimated_claim_count.toLocaleString()} claims / {managerExternalBatchPreview.recommended_strict_manager_proof_batch.rollback_preview.estimated_evidence_count.toLocaleString()} evidence / {managerExternalBatchPreview.recommended_strict_manager_proof_batch.rollback_preview.estimated_manifest_entry_count.toLocaleString()} manifest entries
                                </div>
                              )}
                              {managerExternalBatchPreview.recommended_strict_manager_proof_batch.safe_action && (
                                <div className="mt-1 text-[10px] text-gray-500">{managerExternalBatchPreview.recommended_strict_manager_proof_batch.safe_action}</div>
                              )}
                            </div>
                          )}
                          {managerExternalBatchPreview.safe_action && (
                            <div className="mt-2 text-[10px] text-gray-500">{managerExternalBatchPreview.safe_action}</div>
                          )}
                        </div>
                      )}
                      {sourceOverlapApprovalPacket && (
                        <div className="mt-2 rounded border border-slate-200 bg-white px-2 py-2">
                          <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                            <div>
                              <div className="text-[10px] uppercase text-slate-600">Source-overlap approval packet</div>
                              <div className="text-xs font-semibold text-gray-800">
                                Current ledger: {sourceOverlapApprovalPacket.current_ledger.multi_source_fact_group_count.toLocaleString()} multi-source / {sourceOverlapApprovalPacket.current_ledger.source_ready_fact_group_count.toLocaleString()} source-ready
                              </div>
                              <div className="mt-1 text-[10px] text-gray-600">
                                Strict HPM packet: {sourceOverlapApprovalPacket.recommended_first_packet.template_count.toLocaleString()} templates / {(sourceOverlapApprovalPacket.recommended_first_packet.planned_upsert_count_if_approved ?? 0).toLocaleString()} planned upserts / {sourceOverlapApprovalPacket.previewed_overlap_if_approved.manager_strict_source_ready_if_recorded_count.toLocaleString()} strict groups
                              </div>
                              {sourceOverlapRecordingGate && (
                                <div className={`mt-1 text-[10px] ${sourceOverlapRecordingGate.source_overlap_proof_satisfied ? 'text-emerald-700' : 'text-amber-700'}`}>
                                  Source-overlap proof: {sourceOverlapRecordingGate.status.replace(/_/g, ' ')}; current {sourceOverlapRecordingGate.current_multi_source_fact_group_count.toLocaleString()} multi-source / {sourceOverlapRecordingGate.current_source_ready_fact_group_count.toLocaleString()} source-ready.
                                </div>
                              )}
                            </div>
                            <div className={`rounded border px-1.5 py-0.5 text-[10px] ${sourceOverlapApprovalPacket.approval_required ? 'border-amber-100 bg-amber-50 text-amber-700' : 'border-slate-100 bg-slate-50 text-slate-600'}`}>
                              {sourceOverlapApprovalPacket.approval_required ? 'approval required' : 'review only'}
                            </div>
                          </div>
                          {sourceOverlapApprovalPacket.recommended_first_packet.manager_proof_source_families && sourceOverlapApprovalPacket.recommended_first_packet.manager_proof_source_families.length > 0 && (
                            <div className="mt-2">
                              <div className="text-[10px] font-semibold uppercase text-slate-600">Strict HPM manager-proof families</div>
                              <div className="mt-1 flex flex-wrap gap-1">
                                {sourceOverlapApprovalPacket.recommended_first_packet.manager_proof_source_families.map((family) => (
                                  <span key={family} className="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[10px] text-slate-700">
                                    {family.replace(/_/g, ' ')}
                                  </span>
                                ))}
                              </div>
                            </div>
                          )}
                          {sourceOverlapApprovalSummary && (
                            <div className="mt-2 rounded border border-amber-100 bg-amber-50 px-2 py-1.5 text-[10px] text-amber-800">
                              <div className="font-semibold uppercase">Approval effects</div>
                              <div className="mt-1">
                                Would record {sourceOverlapApprovalSummary.would_record_template_count.toLocaleString()} templates / {sourceOverlapApprovalSummary.would_plan_upsert_count.toLocaleString()} upserts; expected source-ready groups: {Number(sourceOverlapApprovalSummary.expected_source_ready_fact_group_count ?? 0).toLocaleString()}; verified-safe: {Number(sourceOverlapApprovalSummary.expected_safe_to_mark_verified_count ?? 0).toLocaleString()}.
                              </div>
                              <div className="mt-1">
                                Marks verified: {sourceOverlapApprovalSummary.will_mark_verified ? 'yes' : 'no'} / refreshes sources: {sourceOverlapApprovalSummary.will_create_or_refresh_source_data ? 'yes' : 'no'} / materializes relationships: {sourceOverlapApprovalSummary.will_materialize_new_relationships ? 'yes' : 'no'}.
                              </div>
                              {sourceOverlapApprovalSummary.included_addresses && sourceOverlapApprovalSummary.included_addresses.length > 0 && (
                                <div className="mt-1 text-amber-700">
                                  Included addresses: {sourceOverlapApprovalSummary.included_addresses.slice(0, 4).join(', ')}
                                </div>
                              )}
                              {sourceOverlapApprovalSummary.safe_action && (
                                <div className="mt-1 text-amber-700">{sourceOverlapApprovalSummary.safe_action}</div>
                              )}
                            </div>
                          )}
                          {sourceOverlapRecordingGate?.safe_action && (
                            <div className="mt-2 rounded border border-emerald-100 bg-emerald-50 px-2 py-1.5 text-[10px] text-emerald-800">
                              {sourceOverlapRecordingGate.safe_action}
                            </div>
                          )}
                          {sourceOverlapMutationScope && (
                            <div className="mt-2 rounded border border-amber-100 bg-white px-2 py-1.5 text-[10px] text-amber-800">
                              <div className="font-semibold uppercase">Strict HPM mutation scope</div>
                              <div className="mt-1">
                                Allowed tables: {sourceOverlapMutationScope.allowed_tables.map((table) => table.replace(/_/g, ' ')).join(', ')}.
                              </div>
                              <div className="mt-1">
                                Marks verified: {sourceOverlapMutationScope.forbidden_side_effects.will_mark_verified ? 'yes' : 'no'} / starts jobs: {sourceOverlapMutationScope.forbidden_side_effects.will_start_jobs ? 'yes' : 'no'} / allows business use: {sourceOverlapMutationScope.forbidden_side_effects.will_allow_business_use ? 'yes' : 'no'}.
                              </div>
                              <div className="mt-1">
                                Materializes current relationships: {sourceOverlapMutationScope.forbidden_side_effects.will_materialize_building_management_relationships ? 'yes' : 'no'}.
                              </div>
                            </div>
                          )}
                          {sourceOverlapApprovalPacket.manager_strict_gap_summary && (
                            <div className="mt-2 rounded border border-sky-100 bg-sky-50 px-2 py-1.5 text-[10px] text-sky-800">
                              <div>
                                HPM broad-only gaps: {sourceOverlapApprovalPacket.manager_strict_gap_summary.broad_source_ready_not_strict_count.toLocaleString()} broad but not strict / {sourceOverlapApprovalPacket.manager_strict_gap_summary.strict_ready_claim_group_count.toLocaleString()} strict-ready.
                              </div>
                              {sourceOverlapApprovalPacket.manager_strict_gap_summary.gap_candidates.slice(0, 2).map((candidate) => (
                                <div key={`${candidate.bbl}-${candidate.address}`} className="mt-1 text-sky-700">
                                  {candidate.address}: {candidate.strict_manager_gap_status?.replace(/_/g, ' ') ?? 'needs source'}; missing {Number(candidate.missing_manager_proof_source_family_count ?? 0).toLocaleString()} manager-proof family
                                </div>
                              ))}
                            </div>
                          )}
                          {sourceOverlapNewRelationshipSummary && sourceOverlapNewRelationshipSummary.candidate_count > 0 && (
                            <div className="mt-2 rounded border border-indigo-100 bg-indigo-50 px-2 py-1.5 text-[10px] text-indigo-800">
                              <div>
                                Relationship-acquisition leads: {sourceOverlapNewRelationshipSummary.candidate_count.toLocaleString()} source-backed candidate{sourceOverlapNewRelationshipSummary.candidate_count === 1 ? '' : 's'}; not current-ledger overlap.
                              </div>
                              {sourceOverlapNewRelationshipFamilies.length > 0 && (
                                <div className="mt-1 flex flex-wrap gap-1">
                                  {sourceOverlapNewRelationshipFamilies.map(([family, count]) => (
                                    <span key={family} className="rounded border border-indigo-100 bg-white px-1.5 py-0.5 text-[10px] text-indigo-700">
                                      {family.replace(/_/g, ' ')}: {count.toLocaleString()}
                                    </span>
                                  ))}
                                </div>
                              )}
                              {sourceOverlapNewRelationshipSummary.candidates.slice(0, 2).map((candidate) => (
                                <div key={`${candidate.candidate_id}-${candidate.bbl}`} className="mt-1 text-indigo-700">
                                  {candidate.local_address || candidate.external_address || 'relationship candidate'}: {candidate.source_family?.replace(/_/g, ' ') ?? 'source'}; review before relationship creation
                                  {candidate.current_relationship_state && (
                                    <span className="block">
                                      Current rows: {Number(candidate.current_relationship_state.current_building_management_relationship_count ?? 0).toLocaleString()} building-management / {Number(candidate.current_relationship_state.current_truth_claim_count ?? 0).toLocaleString()} truth claims
                                    </span>
                                  )}
                                </div>
                              ))}
                              {sourceOverlapNewRelationshipSummary.safe_action && (
                                <div className="mt-1 text-indigo-700">{sourceOverlapNewRelationshipSummary.safe_action}</div>
                              )}
                            </div>
                          )}
                          {sourceOverlapApprovalPacket.operator_strict_packet?.manager_proof_source_families && sourceOverlapApprovalPacket.operator_strict_packet.manager_proof_source_families.length > 0 && (
                            <div className="mt-2 text-[10px] text-gray-600">
                              Strict operator packet: {sourceOverlapApprovalPacket.operator_strict_packet.template_count.toLocaleString()} templates / {(sourceOverlapApprovalPacket.operator_strict_packet.planned_upsert_count_if_approved ?? 0).toLocaleString()} planned upserts / {sourceOverlapApprovalPacket.previewed_overlap_if_approved.operator_strict_source_ready_if_recorded_count.toLocaleString()} strict groups; families: {sourceOverlapApprovalPacket.operator_strict_packet.manager_proof_source_families.map((family) => family.replace(/_/g, ' ')).join(', ')}
                            </div>
                          )}
                          {sourceOverlapApprovalPacket.operator_strict_packet?.current_recording_status && (
                            <div className="mt-2 rounded border border-emerald-100 bg-emerald-50 px-2 py-1.5 text-[10px] text-emerald-800">
                              <div className="font-semibold uppercase">Operator recording status</div>
                              <div className="mt-1">
                                {sourceOverlapApprovalPacket.operator_strict_packet.current_recording_status.replace(/_/g, ' ')}
                              </div>
                              {sourceOverlapApprovalPacket.operator_strict_packet.recording_effect_if_rerun && (
                                <div className="mt-1">
                                  Rerun would create {Number(sourceOverlapApprovalPacket.operator_strict_packet.recording_effect_if_rerun.would_create_new_claim_count ?? 0).toLocaleString()} new claims / {Number(sourceOverlapApprovalPacket.operator_strict_packet.recording_effect_if_rerun.would_create_new_evidence_count ?? 0).toLocaleString()} new evidence rows and update {Number(sourceOverlapApprovalPacket.operator_strict_packet.recording_effect_if_rerun.would_update_existing_claim_count ?? 0).toLocaleString()} existing claims / {Number(sourceOverlapApprovalPacket.operator_strict_packet.recording_effect_if_rerun.would_update_existing_evidence_count ?? 0).toLocaleString()} existing evidence rows.
                                </div>
                              )}
                              {sourceOverlapApprovalPacket.operator_strict_packet.safe_action && (
                                <div className="mt-1 text-emerald-700">{sourceOverlapApprovalPacket.operator_strict_packet.safe_action}</div>
                              )}
                            </div>
                          )}
                          {sourceOverlapOperatorApprovalSummary && (
                            <div className="mt-2 rounded border border-emerald-100 bg-emerald-50 px-2 py-1.5 text-[10px] text-emerald-800">
                              <div className="font-semibold uppercase">Operator approval effects</div>
                              <div className="mt-1">
                                Would record {sourceOverlapOperatorApprovalSummary.would_record_template_count.toLocaleString()} templates / {sourceOverlapOperatorApprovalSummary.would_plan_upsert_count.toLocaleString()} upserts; expected source-ready groups: {Number(sourceOverlapOperatorApprovalSummary.expected_source_ready_fact_group_count ?? 0).toLocaleString()}; verified-safe: {Number(sourceOverlapOperatorApprovalSummary.expected_safe_to_mark_verified_count ?? 0).toLocaleString()}.
                              </div>
                              <div className="mt-1">
                                Marks verified: {sourceOverlapOperatorApprovalSummary.will_mark_verified ? 'yes' : 'no'} / refreshes sources: {sourceOverlapOperatorApprovalSummary.will_create_or_refresh_source_data ? 'yes' : 'no'} / materializes relationships: {sourceOverlapOperatorApprovalSummary.will_materialize_new_relationships ? 'yes' : 'no'}.
                              </div>
                              {sourceOverlapOperatorApprovalSummary.included_addresses && sourceOverlapOperatorApprovalSummary.included_addresses.length > 0 && (
                                <div className="mt-1 text-emerald-700">
                                  Included operator addresses: {sourceOverlapOperatorApprovalSummary.included_addresses.slice(0, 4).join(', ')}
                                </div>
                              )}
                              {sourceOverlapOperatorApprovalSummary.safe_action && (
                                <div className="mt-1 text-emerald-700">{sourceOverlapOperatorApprovalSummary.safe_action}</div>
                              )}
                            </div>
                          )}
                          {sourceOverlapOperatorMutationScope && (
                            <div className="mt-2 rounded border border-emerald-100 bg-white px-2 py-1.5 text-[10px] text-emerald-800">
                              <div className="font-semibold uppercase">Operator mutation scope</div>
                              <div className="mt-1">
                                Allowed tables: {sourceOverlapOperatorMutationScope.allowed_tables.map((table) => table.replace(/_/g, ' ')).join(', ')}.
                              </div>
                              <div className="mt-1">
                                Marks verified: {sourceOverlapOperatorMutationScope.forbidden_side_effects.will_mark_verified ? 'yes' : 'no'} / starts jobs: {sourceOverlapOperatorMutationScope.forbidden_side_effects.will_start_jobs ? 'yes' : 'no'} / allows business use: {sourceOverlapOperatorMutationScope.forbidden_side_effects.will_allow_business_use ? 'yes' : 'no'}.
                              </div>
                              <div className="mt-1">
                                Materializes current relationships: {sourceOverlapOperatorMutationScope.forbidden_side_effects.will_materialize_building_management_relationships ? 'yes' : 'no'}.
                              </div>
                            </div>
                          )}
                          {sourceOverlapApprovalPacket.operator_strict_packet?.excluded_non_strict_candidates && sourceOverlapApprovalPacket.operator_strict_packet.excluded_non_strict_candidates.length > 0 && (
                            <div className="mt-2 rounded border border-orange-100 bg-orange-50 px-2 py-1.5 text-[10px] text-orange-800">
                              <div>
                                Strict operator packet excludes {sourceOverlapApprovalPacket.operator_strict_packet.excluded_non_strict_candidates.length.toLocaleString()} broad-only candidate{sourceOverlapApprovalPacket.operator_strict_packet.excluded_non_strict_candidates.length === 1 ? '' : 's'}.
                              </div>
                              {sourceOverlapApprovalPacket.operator_strict_packet.excluded_non_strict_candidates.slice(0, 2).map((candidate) => (
                                <div key={`${candidate.candidate_id}-${candidate.address}`} className="mt-1 text-orange-700">
                                  {candidate.address}: {candidate.strict_manager_gap_status?.replace(/_/g, ' ') ?? 'excluded'}; next proof required
                                </div>
                              ))}
                            </div>
                          )}
                          {sourceOverlapApprovalPacket.operator_strict_gap_summary && (
                            <div className="mt-2 rounded border border-amber-100 bg-amber-50 px-2 py-1.5 text-[10px] text-amber-800">
                              <div>
                                Operator broad-only gaps: {sourceOverlapApprovalPacket.operator_strict_gap_summary.broad_source_ready_not_strict_count.toLocaleString()} broad but not strict / {sourceOverlapApprovalPacket.operator_strict_gap_summary.strict_ready_candidate_count.toLocaleString()} strict-ready.
                              </div>
                              {sourceOverlapApprovalPacket.operator_strict_gap_summary.gap_candidates.slice(0, 2).map((candidate) => (
                                <div key={`${candidate.candidate_id}-${candidate.address}`} className="mt-1 text-amber-700">
                                  {candidate.address}: {candidate.strict_manager_gap_status?.replace(/_/g, ' ') ?? 'needs source'}; missing {Number(candidate.missing_manager_proof_source_family_count ?? 0).toLocaleString()} manager-proof family
                                </div>
                              ))}
                            </div>
                          )}
                          <div className="mt-2 text-[10px] text-gray-500">
                            {sourceOverlapApprovalPacket.blocked_business_use_reason}
                          </div>
                          {sourceOverlapPostRecordingCheck && (
                            <div className="mt-2 rounded border border-violet-100 bg-violet-50 px-2 py-2 text-[10px] text-violet-800">
                              <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                                <div>
                                  <div className="font-semibold uppercase">Post-recording proof</div>
                                  <div className="mt-1 text-gray-700">
                                    Current multi-source: {sourceOverlapPostRecordingCheck.current_ledger.multi_source_fact_group_count.toLocaleString()} / current source-ready: {sourceOverlapPostRecordingCheck.current_ledger.source_ready_fact_group_count.toLocaleString()} / verified single-source: {sourceOverlapPostRecordingCheck.verified_single_source_policy.verified_single_source_claim_count.toLocaleString()}
                                  </div>
                                </div>
                                <div className={`shrink-0 rounded border px-1.5 py-0.5 ${sourceOverlapPostRecordingCheck.post_recording_success ? 'border-emerald-100 bg-emerald-50 text-emerald-700' : 'border-violet-200 bg-white text-violet-700'}`}>
                                  {sourceOverlapPostRecordingCheck.post_recording_success ? 'passed' : 'blocked'}
                                </div>
                              </div>
                              {sourceOverlapPostRecordingFailedChecks.length > 0 && (
                                <div className="mt-2 flex flex-wrap gap-1">
                                  {sourceOverlapPostRecordingFailedChecks.map((check) => (
                                    <span key={check.check} className="rounded border border-violet-100 bg-white px-1.5 py-0.5 text-violet-700">
                                      {check.check}: {check.observed.toLocaleString()}
                                    </span>
                                  ))}
                                </div>
                              )}
                              <div className="mt-2 text-violet-700">
                                {sourceOverlapPostRecordingCheck.safe_action}
                              </div>
                            </div>
                          )}
                        </div>
                      )}
                      {managerExternalPostRecordingSimulation && (
                        <div className="mt-2 rounded border border-emerald-100 bg-white px-2 py-2">
                          <div className="grid gap-2 sm:grid-cols-5">
                            <div>
                              <div className="text-[10px] uppercase text-emerald-700">Post-record simulation</div>
                              <div className="text-xs font-semibold text-gray-800">
                                {managerExternalPostRecordingSimulation.simulated_fact_group_count.toLocaleString()} fact groups
                              </div>
                            </div>
                            <div>
                              <div className="text-[10px] uppercase text-emerald-700">Multi-source</div>
                              <div className="text-xs font-semibold text-gray-800">
                                {managerExternalPostRecordingSimulation.multi_source_fact_group_count.toLocaleString()}
                              </div>
                            </div>
                            <div>
                              <div className="text-[10px] uppercase text-emerald-700">Source-ready</div>
                              <div className="text-xs font-semibold text-gray-800">
                                {managerExternalPostRecordingSimulation.source_ready_fact_group_count.toLocaleString()}
                              </div>
                            </div>
                            <div>
                              <div className="text-[10px] uppercase text-emerald-700">Manager-proof</div>
                              <div className="text-xs font-semibold text-gray-800">
                                {(managerExternalPostRecordingSimulation.strict_manager_source_ready_fact_group_count ?? 0).toLocaleString()}
                              </div>
                            </div>
                            <div>
                              <div className="text-[10px] uppercase text-emerald-700">Verified-safe</div>
                              <div className="text-xs font-semibold text-gray-800">
                                {managerExternalPostRecordingSimulation.safe_to_mark_verified_count.toLocaleString()}
                              </div>
                            </div>
                          </div>
                          {Object.keys(managerExternalPostRecordingSimulation.blocker_counts ?? {}).length > 0 && (
                            <div className="mt-2 flex flex-wrap gap-1">
                              {Object.entries(managerExternalPostRecordingSimulation.blocker_counts ?? {}).slice(0, 4).map(([blocker, count]) => (
                                <span key={blocker} className="rounded border border-emerald-100 bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-700">
                                  {blocker.replace(/_/g, ' ')}: {count.toLocaleString()}
                                </span>
                              ))}
                            </div>
                          )}
                          {managerExternalPostRecordingSimulation.safe_action && (
                            <div className="mt-2 text-[10px] text-gray-500">{managerExternalPostRecordingSimulation.safe_action}</div>
                          )}
                        </div>
                      )}
                      {managerExternalNextSourceBatches && managerExternalNextSourceBatches.candidate_count > 0 && (
                        <div className="mt-2 rounded border border-cyan-100 bg-white px-2 py-2">
                          <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                            <div>
                              <div className="text-[10px] uppercase text-cyan-700">Next source batches</div>
                              <div className="text-xs font-semibold text-gray-800">
                                {managerExternalNextSourceBatches.candidate_count.toLocaleString()} groups need one more manager-proof source
                              </div>
                            </div>
                            <div className="rounded border border-cyan-100 bg-cyan-50 px-1.5 py-0.5 text-[10px] text-cyan-700">
                              dry run
                            </div>
                          </div>
                          <div className="mt-2 flex flex-wrap gap-1">
                            {Object.entries(managerExternalNextSourceBatches.suggested_source_family_counts ?? {}).slice(0, 4).map(([family, count]) => (
                              <span key={family} className="rounded border border-cyan-100 bg-cyan-50 px-1.5 py-0.5 text-[10px] text-cyan-700">
                                {family.replace(/_/g, ' ')}: {Number(count).toLocaleString()}
                              </span>
                            ))}
                          </div>
                          {(managerExternalNextSourceBatches.proposals ?? []).slice(0, 3).map((proposal) => (
                            <div key={String(proposal.bbl || proposal.address)} className="mt-2 border-t border-cyan-50 pt-1.5 text-[10px] text-gray-600">
                              <span className="font-medium text-gray-800">{proposal.address || proposal.bbl}</span>
                              {' '}needs {(proposal.suggested_source_families ?? []).slice(0, 2).map((family) => family.replace(/_/g, ' ')).join(' or ')}
                              {proposal.search_queries && proposal.search_queries.length > 0 && (
                                <div className="mt-1 truncate text-gray-500">{proposal.search_queries[0]}</div>
                              )}
                            </div>
                          ))}
                          {managerExternalNextSourceBatches.reviewed_source_findings && managerExternalNextSourceBatches.reviewed_source_findings.length > 0 && (
                            <div className="mt-2 border-t border-cyan-50 pt-1.5">
                              <div className="text-[10px] font-semibold uppercase text-cyan-700">Reviewed source findings</div>
                              <div className="mt-1 space-y-1 text-[10px] text-gray-600">
                                {managerExternalNextSourceBatches.reviewed_source_findings.slice(0, 3).map((finding) => (
                                  <div key={finding.source_family}>
                                    <span className="font-medium text-gray-800">{finding.source_family.replace(/_/g, ' ')}</span>
                                    {': '}{finding.qualification}
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}
                          {managerExternalNextSourceBatches.source_boundary_notes && managerExternalNextSourceBatches.source_boundary_notes.length > 0 && (
                            <div className="mt-2 space-y-1 text-[10px] text-gray-500">
                              {managerExternalNextSourceBatches.source_boundary_notes.slice(0, 2).map((note) => (
                                <div key={note}>{note}</div>
                              ))}
                            </div>
                          )}
                          {managerExternalNextSourceBatches.safe_action && (
                            <div className="mt-2 text-[10px] text-gray-500">{managerExternalNextSourceBatches.safe_action}</div>
                          )}
                        </div>
                      )}
                      {managerExternalBlockedCandidates.length > 0 && (
                        <div className="mt-2 rounded border border-amber-100 bg-white px-2 py-1.5">
                          <div className="text-[10px] uppercase text-amber-700">Needs address review</div>
                          {managerExternalBlockedCandidates.map((candidate) => (
                            <div key={candidate.candidate_id} className="mt-1 text-[10px] text-gray-600">
                              {candidate.external_address || candidate.candidate_id}: {candidate.candidate_status.replace(/_/g, ' ')}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                  {operatorConfirmedPreview && (
                    <div className="mt-2 rounded border border-violet-100 bg-violet-50 px-2 py-2">
                      <div className="grid gap-2 sm:grid-cols-5">
                        <div>
                          <div className="text-[10px] uppercase text-violet-700">Operator-confirmed</div>
                          <div className="text-xs font-semibold text-gray-800">{operatorConfirmedPreview.matched_candidate_count.toLocaleString()}</div>
                          <div className="text-[10px] text-gray-600">matched facts</div>
                        </div>
                        <div>
                          <div className="text-[10px] uppercase text-violet-700">New relationships</div>
                          <div className="text-xs font-semibold text-gray-800">{operatorConfirmedPreview.new_relationship_candidate_count.toLocaleString()}</div>
                        </div>
                        <div>
                          <div className="text-[10px] uppercase text-violet-700">Conflicts</div>
                          <div className="text-xs font-semibold text-gray-800">{operatorConfirmedPreview.conflict_candidate_count.toLocaleString()}</div>
                        </div>
                        <div>
                          <div className="text-[10px] uppercase text-violet-700">Templates</div>
                          <div className="text-xs font-semibold text-gray-800">{operatorConfirmedPreview.manual_evidence_template_count.toLocaleString()}</div>
                          {operatorConfirmedPreview.second_source_template_count !== undefined && (
                            <div className="text-[10px] text-gray-600">{operatorConfirmedPreview.second_source_template_count.toLocaleString()} second source</div>
                          )}
                        </div>
                        <div>
                          <div className="text-[10px] uppercase text-violet-700">Source-ready</div>
                          <div className="text-xs font-semibold text-gray-800">{operatorConfirmedPreview.source_ready_if_recorded_count.toLocaleString()}</div>
                        </div>
                        <div>
                          <div className="text-[10px] uppercase text-violet-700">Manager-proof</div>
                          <div className="text-xs font-semibold text-gray-800">{(operatorConfirmedPreview.strict_manager_source_ready_if_recorded_count ?? 0).toLocaleString()}</div>
                        </div>
                      </div>
                      {typeof operatorConfirmedPreview.policy?.single_source_policy === 'string' && (
                        <div className="mt-2 text-xs text-violet-700">{operatorConfirmedPreview.policy.single_source_policy}</div>
                      )}
                      {operatorConfirmedCandidates.length > 0 && (
                        <div className="mt-2 divide-y divide-violet-100 rounded border border-violet-100 bg-white">
                          {operatorConfirmedCandidates.map((candidate) => (
                            <div key={candidate.candidate_id} className="px-2 py-2">
                              {(() => {
                                const currentRelationshipCount = candidate.current_building_management?.length ?? 0;
                                const currentTruthClaimCount = candidate.current_truth_claims?.length ?? 0;
                                return (
                              <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between">
                                <div>
                                  <div className="text-xs font-semibold text-gray-800">
                                    {String(candidate.matched_building?.address ?? candidate.user_address)} - {String(candidate.matched_lead?.company_name ?? candidate.manager_name_supplied)}
                                  </div>
                                  <div className="mt-0.5 text-[10px] text-gray-500">
                                    queue: {candidate.review_queue.replace(/_/g, ' ')}
                                  </div>
                                  <div className="mt-0.5 text-[10px] text-gray-500">
                                    current links: {currentRelationshipCount.toLocaleString()} / truth claims: {currentTruthClaimCount.toLocaleString()}
                                  </div>
                                </div>
                                <div className={`rounded border px-1.5 py-0.5 text-[10px] ${candidate.conflicting_current_manager_count || candidate.conflicting_truth_claim_count ? 'border-amber-100 bg-amber-50 text-amber-700' : candidate.strict_manager_source_ready_if_recorded ? 'border-emerald-100 bg-emerald-50 text-emerald-700' : candidate.source_ready_if_recorded ? 'border-cyan-100 bg-cyan-50 text-cyan-700' : 'border-violet-100 bg-violet-50 text-violet-700'}`}>
                                  {candidate.conflicting_current_manager_count || candidate.conflicting_truth_claim_count ? 'review conflict' : candidate.strict_manager_source_ready_if_recorded ? 'manager-proof overlap' : candidate.source_ready_if_recorded ? 'source-ready preview' : 'single source'}
                                </div>
                              </div>
                                );
                              })()}
                              {candidate.supporting_sources_if_recorded && candidate.supporting_sources_if_recorded.length > 0 && (
                                <div className="mt-1 flex flex-wrap gap-1">
                                  {candidate.supporting_sources_if_recorded.map((source) => (
                                    <span key={`${candidate.candidate_id}-${source}`} className="rounded border border-violet-100 bg-violet-50 px-1.5 py-0.5 text-[10px] text-violet-700">
                                      {source.replace(/_/g, ' ')}
                                    </span>
                                  ))}
                                </div>
                              )}
                              {candidate.strict_manager_gap_reason && (
                                <div className="mt-1 rounded bg-gray-50 px-2 py-1 text-[10px] text-gray-600">
                                  <span className="font-medium text-gray-800">{candidate.strict_manager_gap_status?.replace(/_/g, ' ') ?? 'manager proof gap'}:</span>{' '}
                                  {candidate.strict_manager_gap_reason}
                                </div>
                              )}
                              {candidate.safe_action && (
                                <div className="mt-1 text-[10px] text-gray-500">{candidate.safe_action}</div>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                      {operatorSecondSourceProposals.length > 0 && (
                        <div className="mt-2 rounded border border-violet-100 bg-white px-2 py-1.5">
                          <div className="text-[10px] uppercase text-violet-700">Second-source seeds</div>
                          {operatorSecondSourceSeedBatches?.safe_action && (
                            <div className="mt-1 text-[10px] text-violet-700">{operatorSecondSourceSeedBatches.safe_action}</div>
                          )}
                          {operatorSecondSourceProposals.map((proposal) => (
                            <div key={`${proposal.bbl}-${proposal.manager_lead_id}`} className="mt-1 text-[10px] text-gray-600">
                              {proposal.address}: {proposal.source_ready_if_recorded ? 'source-ready preview' : proposal.suggested_source_families.slice(0, 3).map((source) => source.replace(/_/g, ' ')).join(', ')}
                              {proposal.strict_manager_source_ready_if_recorded ? ' / manager-proof' : ''}
                              {proposal.strict_manager_gap_status ? ` / ${proposal.strict_manager_gap_status.replace(/_/g, ' ')}` : ''}
                              {proposal.search_queries?.[0] ? ` / ${proposal.search_queries[0]}` : ''}
                              {proposal.next_required_manager_proof ? ` / next: ${proposal.next_required_manager_proof}` : ''}
                            </div>
                          ))}
                          {operatorSecondSourceSeedBatches?.reviewed_source_findings && operatorSecondSourceSeedBatches.reviewed_source_findings.length > 0 && (
                            <div className="mt-2 border-t border-violet-50 pt-1.5">
                              <div className="text-[10px] font-semibold uppercase text-violet-700">Reviewed source findings</div>
                              <div className="mt-1 space-y-1 text-[10px] text-gray-600">
                                {operatorSecondSourceSeedBatches.reviewed_source_findings.slice(0, 3).map((finding) => (
                                  <div key={finding.source_family}>
                                    <span className="font-medium text-gray-800">{finding.source_family.replace(/_/g, ' ')}</span>
                                    {': '}{finding.qualification}
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}
                          {operatorSecondSourceSeedBatches?.source_boundary_notes && operatorSecondSourceSeedBatches.source_boundary_notes.length > 0 && (
                            <div className="mt-2 space-y-1 text-[10px] text-gray-500">
                              {operatorSecondSourceSeedBatches.source_boundary_notes.slice(0, 2).map((note) => (
                                <div key={note}>{note}</div>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                      {operatorConfirmedPreview.safe_action && (
                        <div className="mt-2 text-[10px] text-gray-500">{operatorConfirmedPreview.safe_action}</div>
                      )}
                    </div>
                  )}
                  {roleOverlapActivationPlan && (
                    <div className="mt-2 rounded border border-blue-100 bg-blue-50 px-2 py-2">
                      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                        <div>
                          <div className="text-[10px] uppercase text-blue-700">Role overlap activation plan</div>
                          <div className="text-xs font-semibold text-gray-800">
                            {roleOverlapActivationPlan.predicted_if_approved.source_ready_fact_groups_added.toLocaleString()} source-ready after approval
                          </div>
                          <div className="mt-1 text-[10px] text-gray-600">
                            {roleOverlapActivationPlan.predicted_if_approved.management_source_ready_fact_groups_added.toLocaleString()} manager-ready / {roleOverlapActivationPlan.predicted_if_approved.registered_agent_source_ready_fact_groups_added.toLocaleString()} agent-ready
                          </div>
                        </div>
                        <div className={`rounded border px-1.5 py-0.5 text-[10px] ${roleOverlapActivationPlan.approval_required ? 'border-blue-200 bg-white text-blue-700' : 'border-gray-100 bg-gray-50 text-gray-600'}`}>
                          {roleOverlapActivationPlan.approval_required ? 'approval required' : 'no action'}
                        </div>
                      </div>
                      {roleOverlapActivationPlan.business_readiness_note && (
                        <div className="mt-2 text-xs text-blue-700">{roleOverlapActivationPlan.business_readiness_note}</div>
                      )}
                      {roleOverlapActivationSteps.length > 0 && (
                        <div className="mt-2 grid gap-1">
                          {roleOverlapActivationSteps.map((step) => (
                            <div key={step.step} className="rounded border border-blue-100 bg-white px-2 py-1.5">
                              <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                                <div className="text-xs font-medium text-gray-800">{step.step.replace(/_/g, ' ')}</div>
                                <div className={`rounded border px-1.5 py-0.5 text-[10px] ${step.approval_required ? 'border-amber-100 bg-amber-50 text-amber-700' : 'border-gray-100 bg-gray-50 text-gray-600'}`}>
                                  {step.status.replace(/_/g, ' ')}
                                </div>
                              </div>
                              <div className="mt-1 text-[10px] text-gray-500">
                                mutations planned: {step.mutations_planned.toLocaleString()}
                              </div>
                              {step.safe_action && (
                                <div className="mt-1 text-[10px] text-gray-600">{step.safe_action}</div>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                  {roleOverlapSimulation && (
                    <div className="mt-2 rounded border border-emerald-100 bg-emerald-50 px-2 py-2">
                      <div className="grid gap-2 sm:grid-cols-4">
                        <div>
                          <div className="text-[10px] uppercase text-emerald-700">Post-activation simulation</div>
                          <div className="text-xs font-semibold text-gray-800">{roleOverlapSimulation.simulated_fact_group_count.toLocaleString()}</div>
                          <div className="text-[10px] text-gray-600">fact groups</div>
                        </div>
                        <div>
                          <div className="text-[10px] uppercase text-emerald-700">Multi-source</div>
                          <div className="text-xs font-semibold text-gray-800">{roleOverlapSimulation.multi_source_fact_group_count.toLocaleString()}</div>
                        </div>
                        <div>
                          <div className="text-[10px] uppercase text-emerald-700">Source-ready</div>
                          <div className="text-xs font-semibold text-gray-800">{roleOverlapSimulation.source_ready_fact_group_count.toLocaleString()}</div>
                        </div>
                        <div>
                          <div className="text-[10px] uppercase text-emerald-700">Verified-safe</div>
                          <div className="text-xs font-semibold text-gray-800">{roleOverlapSimulation.safe_to_mark_verified_count.toLocaleString()}</div>
                        </div>
                      </div>
                      {roleOverlapSimulation.business_readiness_note && (
                        <div className="mt-2 text-xs text-emerald-700">{roleOverlapSimulation.business_readiness_note}</div>
                      )}
                      {Object.keys(roleOverlapSimulation.source_ready_count_by_predicate).length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1">
                          {Object.entries(roleOverlapSimulation.source_ready_count_by_predicate).map(([predicate, count]) => (
                            <span key={predicate} className="rounded border border-emerald-100 bg-white px-1.5 py-0.5 text-[10px] text-emerald-700">
                              {predicate.replace(/_/g, ' ')}: {Number(count).toLocaleString()}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                  {roleClaimCorrectionPreview && (
                    <div className="mt-2 rounded border border-amber-100 bg-amber-50 px-2 py-2">
                      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                        <div>
                          <div className="text-[10px] uppercase text-amber-700">Role correction preview</div>
                          <div className="text-xs font-semibold text-gray-800">
                            {roleClaimCorrectionPreview.sampled_stale_claim_count.toLocaleString()} stale Agent-as-manager claim{roleClaimCorrectionPreview.sampled_stale_claim_count === 1 ? '' : 's'}
                          </div>
                        </div>
                        <div className="rounded border border-amber-200 bg-white px-1.5 py-0.5 text-[10px] text-amber-700">
                          approval required
                        </div>
                      </div>
                      {roleClaimCorrectionPreview.business_readiness_note && (
                        <div className="mt-1 text-xs text-amber-700">{roleClaimCorrectionPreview.business_readiness_note}</div>
                      )}
                      {roleClaimCorrectionSamples.length > 0 && (
                        <div className="mt-2 divide-y divide-amber-100 rounded border border-amber-100 bg-white">
                          {roleClaimCorrectionSamples.map((sample) => (
                            <div key={sample.claim_id} className="px-2 py-2">
                              <div className="text-xs font-medium text-gray-800">
                                {sample.fact_key.predicate?.replace(/_/g, ' ')} - {sample.fact_key.object_id || sample.fact_key.normalized_value}
                              </div>
                              <div className="mt-0.5 break-all text-[10px] text-gray-500">
                                {sample.claim_id} / {sample.source_names.join(', ')}
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        <button
                          onClick={() => roleCorrectionPreview.mutate()}
                          disabled={roleCorrectionPreview.isPending}
                          className="rounded border border-amber-200 bg-white px-2 py-1 text-xs font-medium text-amber-700 hover:bg-amber-100 disabled:opacity-50"
                        >
                          {roleCorrectionPreview.isPending ? 'Previewing...' : 'Preview correction plan'}
                        </button>
                        <div className="text-[10px] text-amber-700">
                          Execution still requires explicit confirm_execute approval.
                        </div>
                      </div>
                      {roleCorrectionPreview.data && (
                        <div className="mt-2 rounded border border-amber-100 bg-white px-2 py-2">
                          <div className="text-xs font-semibold text-gray-800">
                            {roleCorrectionPreview.data.mutations_planned.toLocaleString()} planned stale-claim update{roleCorrectionPreview.data.mutations_planned === 1 ? '' : 's'}
                          </div>
                          <div className="mt-1 text-[10px] text-gray-500">
                            {roleCorrectionPreview.data.rollback_strategy || roleCorrectionPreview.data.blocked_reason}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                  {verificationGapPlan && (
                    <div className="mt-2 rounded border border-gray-100 bg-gray-50 px-2 py-2">
                      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                        <div>
                          <div className="text-[10px] uppercase text-gray-500">Verification gap plan</div>
                          <div className="text-xs font-semibold text-gray-800">
                            {verificationGapPlan.proposal_count.toLocaleString()} evidence acquisition proposal{verificationGapPlan.proposal_count === 1 ? '' : 's'}
                          </div>
                        </div>
                        <div className="rounded border border-gray-100 bg-white px-1.5 py-0.5 text-[10px] text-gray-600">
                          dry run
                        </div>
                      </div>
                      {verificationGapProposals.length > 0 && (
                        <div className="mt-2 divide-y divide-gray-100 rounded border border-gray-100 bg-white">
                          {verificationGapProposals.map((proposal) => (
                            <div key={`${proposal.fact_key.subject_type}-${proposal.fact_key.subject_id}-${proposal.fact_key.predicate}-${proposal.fact_key.object_id || proposal.fact_key.normalized_value || ''}`} className="px-2 py-2">
                              <div className="text-xs font-semibold text-gray-800">{proposal.fact_key.predicate.replace(/_/g, ' ')}</div>
                              <div className="mt-0.5 break-all text-[11px] text-gray-500">
                                {proposal.fact_key.subject_type}:{proposal.fact_key.subject_id}
                                {proposal.fact_key.object_type && proposal.fact_key.object_id ? ` -> ${proposal.fact_key.object_type}:${proposal.fact_key.object_id}` : ''}
                              </div>
                              <div className="mt-1 flex flex-wrap gap-1 text-[10px]">
                                <span className="rounded border border-amber-100 bg-amber-50 px-1.5 py-0.5 text-amber-700">
                                  needs {proposal.missing_source_count.toLocaleString()} source{proposal.missing_source_count === 1 ? '' : 's'}
                                </span>
                                <span className="rounded border border-amber-100 bg-amber-50 px-1.5 py-0.5 text-amber-700">
                                  needs {proposal.missing_evidence_count.toLocaleString()} evidence
                                </span>
                                {proposal.suggested_sources.slice(0, 3).map((source) => (
                                  <span key={source} className="rounded border border-gray-100 bg-gray-50 px-1.5 py-0.5 text-gray-600">
                                    {source.replace(/_/g, ' ')}
                                  </span>
                                ))}
                              </div>
                              <div className="mt-1 text-[10px] text-gray-500">{proposal.safe_action}</div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                  {truthVerificationFrontier && (
                    <div className="mt-2 rounded border border-sky-100 bg-sky-50 px-2 py-2">
                      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                        <div>
                          <div className="text-[10px] uppercase text-sky-700">Verification frontier</div>
                          <div className="text-xs font-semibold text-gray-800">
                            {Number(truthVerificationFrontier.current_ledger?.source_ready_fact_group_count ?? 0).toLocaleString()} source-ready / {Number(truthVerificationFrontier.verification_candidate_count ?? 0).toLocaleString()} verification candidates
                          </div>
                          <div className="mt-0.5 text-[10px] text-gray-600">
                            One-source threshold clears: {Number(truthVerificationFrontier.source_ready_below_verified?.single_source_upgrade_would_verify_count ?? 0).toLocaleString()} / {Number(truthVerificationFrontier.source_ready_below_verified?.proposal_count ?? 0).toLocaleString()}.
                            {' '}Bundle clears: {Number(truthVerificationFrontier.source_ready_below_verified?.bundle_upgrade_would_verify_count ?? 0).toLocaleString()}.
                          </div>
                          <div className="mt-0.5 text-[10px] text-gray-600">
                            HPM next-source seeds: {Number(truthVerificationFrontier.source_acquisition_frontier?.manager_next_source_seed_count ?? 0).toLocaleString()} / operator second-source seeds: {Number(truthVerificationFrontier.source_acquisition_frontier?.operator_second_source_seed_count ?? 0).toLocaleString()}.
                          </div>
                          {truthVerificationFrontier.verification_readiness_gate && (
                            <div className="mt-0.5 text-[10px] text-amber-700">
                              Verification gate: {truthVerificationFrontier.verification_readiness_gate.status.replace(/_/g, ' ')}; record-ready {Number(truthVerificationFrontier.verification_readiness_gate.record_ready_count ?? 0).toLocaleString()} / acquisition-required {Number(truthVerificationFrontier.verification_readiness_gate.acquisition_required_count ?? 0).toLocaleString()} / required evidence {Number(truthVerificationFrontier.verification_readiness_gate.required_real_evidence_count ?? 0).toLocaleString()}.
                            </div>
                          )}
                          {verificationEvidenceRequestPacket && (
                            <div className="mt-0.5 text-[10px] text-sky-700">
                              Evidence request packet: {Number(verificationEvidenceRequestPacket.request_count ?? 0).toLocaleString()} requests; source-ready {Number(verificationEvidenceRequestPacket.source_ready_request_count ?? 0).toLocaleString()} / single-source {Number(verificationEvidenceRequestPacket.single_source_request_count ?? 0).toLocaleString()} / source-acquisition {Number(verificationEvidenceRequestPacket.source_acquisition_request_count ?? 0).toLocaleString()}; record-ready {Number(verificationEvidenceRequestPacket.recording_ready_count ?? 0).toLocaleString()}.
                            </div>
                          )}
                          {verificationEvidenceRequestPacket?.reviewed_source_finding_count != null && (
                            <div className="mt-0.5 text-[10px] text-amber-700">
                              Reviewed source history: {Number(verificationEvidenceRequestPacket.reviewed_source_finding_count).toLocaleString()} findings / {verificationEvidenceRequestPacket.reviewed_source_history_status?.replace(/_/g, ' ') || 'available'}.
                            </div>
                          )}
                        </div>
                        <div className="rounded border border-sky-100 bg-white px-1.5 py-0.5 text-[10px] text-sky-700">
                          read only
                        </div>
                      </div>
                      {verificationEvidenceRequests.length > 0 && (
                        <div className="mt-2 divide-y divide-sky-100 rounded border border-sky-100 bg-white">
                          {verificationEvidenceRequests.map((request) => {
                            const requestLabel = request.relationship_label
                              || request.display?.relationship_label
                              || request.relationship?.relationship_label
                              || request.candidate_id
                              || request.request_type.replace(/_/g, ' ');
                            const sourceHints = request.suggested_source_families
                              || request.required_sources
                              || request.suggested_sources
                              || [];
                            const reviewedFinding = request.reviewed_source_findings?.[0];
                            const acquisitionGuidance = request.required_real_evidence?.find((item) => (
                              Boolean(item.read_only_preview_command || item.official_query_urls?.registrations_api)
                            ));
                            return (
                              <div key={`${request.request_type}-${request.candidate_id || requestLabel}`} className="px-2 py-1.5 text-[10px] text-gray-600">
                                <div className="font-semibold text-gray-800">{requestLabel}</div>
                                <div className="mt-0.5">
                                  {request.request_type.replace(/_/g, ' ')} / {request.can_become?.replace(/_/g, ' ') || 'evidence acquisition'}
                                </div>
                                {request.evidence_need && (
                                  <div className="mt-0.5 text-gray-500">{request.evidence_need}</div>
                                )}
                                {sourceHints.length > 0 && (
                                  <div className="mt-1 flex flex-wrap gap-1">
                                    {sourceHints.slice(0, 4).map((source) => (
                                      <span key={source} className="rounded border border-gray-100 bg-gray-50 px-1.5 py-0.5 text-gray-600">
                                        {source.replace(/_/g, ' ')}
                                      </span>
                                    ))}
                                  </div>
                                )}
                                {acquisitionGuidance?.read_only_preview_command && (
                                  <div className="mt-1 rounded border border-sky-100 bg-sky-50 px-1.5 py-1 text-sky-800">
                                    <div className="font-medium">
                                      HPD query packet{acquisitionGuidance.source_dataset_ids?.length ? ` / ${acquisitionGuidance.source_dataset_ids.join(' + ')}` : ''}
                                    </div>
                                    <code className="mt-0.5 block whitespace-pre-wrap break-all font-mono text-[9px] leading-snug">
                                      {acquisitionGuidance.read_only_preview_command}
                                    </code>
                                    {acquisitionGuidance.official_query_urls?.registrations_api && (
                                      <div className="mt-0.5 break-all text-[9px] text-sky-700">
                                        {acquisitionGuidance.official_query_urls.registrations_api}
                                      </div>
                                    )}
                                  </div>
                                )}
                                {reviewedFinding && (
                                  <div className="mt-1 rounded border border-amber-100 bg-amber-50 px-1.5 py-1 text-amber-800">
                                    Reviewed source history: {reviewedFinding.source_family?.replace(/_/g, ' ') || 'source review'}
                                    {request.reviewed_source_history_status ? ` / ${request.reviewed_source_history_status.replace(/_/g, ' ')}` : ''}
                                    {(reviewedFinding.qualification || reviewedFinding.finding) ? ` - ${reviewedFinding.qualification || reviewedFinding.finding}` : ''}
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      )}
                      {verificationFrontierReadyGaps.length > 0 && (
                        <div className="mt-2 divide-y divide-sky-100 rounded border border-sky-100 bg-white">
                          {verificationFrontierReadyGaps.map((proposal) => (
                            <div key={`${proposal.fact_key.subject_type}-${proposal.fact_key.subject_id}-${proposal.fact_key.predicate}-${proposal.fact_key.object_id || proposal.fact_key.normalized_value || ''}`} className="px-2 py-2">
                              <div className="text-xs font-semibold text-gray-800">
                                {proposal.display?.relationship_label
                                  || `${proposal.fact_key.predicate?.replace(/_/g, ' ') || 'source-ready fact'}${proposal.fact_key.object_id ? ` - ${proposal.fact_key.object_id}` : ''}`}
                              </div>
                              {proposal.display?.building?.address && (
                                <div className="mt-0.5 text-[10px] text-gray-500">
                                  {proposal.display.building.borough || 'NYC'} / BBL {proposal.display.building.bbl || proposal.fact_key.object_id}
                                  {proposal.display.building.unit_count != null ? ` / ${Number(proposal.display.building.unit_count).toLocaleString()} units` : ''}
                                </div>
                              )}
                              <div className="mt-1 flex flex-wrap gap-1 text-[10px]">
                                <span className="rounded border border-sky-100 bg-sky-50 px-1.5 py-0.5 text-sky-700">
                                  score {formatPercent(proposal.recomputed_confidence_score)}
                                </span>
                                <span className="rounded border border-sky-100 bg-sky-50 px-1.5 py-0.5 text-sky-700">
                                  gap {formatPercent(proposal.score_gap_to_verified)}
                                </span>
                                {proposal.best_single_source_upgrade?.suggested_source && (
                                  <span className="rounded border border-amber-100 bg-amber-50 px-1.5 py-0.5 text-amber-700">
                                    best {proposal.best_single_source_upgrade.suggested_source.replace(/_/g, ' ')} {formatPercent(proposal.best_single_source_upgrade.simulated_confidence_score)}
                                  </span>
                                )}
                                {proposal.required_bundle_sources?.slice(0, 3).map((source) => (
                                  <span key={source} className="rounded border border-gray-100 bg-gray-50 px-1.5 py-0.5 text-gray-600">
                                    {source.replace(/_/g, ' ')}
                                  </span>
                                ))}
                              </div>
                              {proposal.required_real_evidence?.length ? (
                                <div className="mt-1 text-[10px] text-gray-600">
                                  Required real evidence:{' '}
                                  {proposal.required_real_evidence.slice(0, 2).map((item) => {
                                    const sourceName = item.suggested_source || item.simulated_supporting_source_name || 'source';
                                    const fields = item.required_fields?.slice(0, 2).map((field) => field.replace(/_/g, ' ')).join(', ');
                                    return `${sourceName.replace(/_/g, ' ')}${fields ? `: ${fields}` : ''}`;
                                  }).join('; ')}
                                  {Number(proposal.required_real_evidence_count ?? proposal.required_real_evidence.length) > 2 ? ' ...' : ''}
                                </div>
                              ) : null}
                              <div className="mt-1 text-[10px] text-gray-600">{proposal.safe_action}</div>
                            </div>
                          ))}
                        </div>
                      )}
                      {(verificationFrontierManagerProposals.length > 0 || verificationFrontierOperatorProposals.length > 0) && (
                        <div className="mt-2 grid gap-2 lg:grid-cols-2">
                          {verificationFrontierManagerProposals.length > 0 && (
                            <div className="rounded border border-sky-100 bg-white px-2 py-2">
                              <div className="text-[10px] font-semibold uppercase text-sky-700">HPM next source</div>
                              {verificationFrontierManagerProposals.map((proposal) => (
                                <div key={`${proposal.candidate_id || proposal.bbl || proposal.address}`} className="mt-1 text-[10px] text-gray-600">
                                  <span className="font-medium text-gray-800">{proposal.address || proposal.bbl}</span>
                                  {proposal.first_search_query ? ` / ${proposal.first_search_query}` : ''}
                                  {proposal.strict_manager_gap_status ? ` / ${proposal.strict_manager_gap_status.replace(/_/g, ' ')}` : ''}
                                </div>
                              ))}
                            </div>
                          )}
                          {verificationFrontierOperatorProposals.length > 0 && (
                            <div className="rounded border border-sky-100 bg-white px-2 py-2">
                              <div className="text-[10px] font-semibold uppercase text-sky-700">Operator second source</div>
                              {verificationFrontierOperatorProposals.map((proposal) => (
                                <div key={`${proposal.candidate_id || proposal.bbl || proposal.address}`} className="mt-1 text-[10px] text-gray-600">
                                  <span className="font-medium text-gray-800">{proposal.address || proposal.bbl}</span>
                                  {proposal.first_search_query ? ` / ${proposal.first_search_query}` : ''}
                                  {proposal.next_required_manager_proof ? ` / ${proposal.next_required_manager_proof}` : ''}
                                  {proposal.current_relationship_state && (
                                    <span className="block text-gray-500">
                                      current: {Number(proposal.current_relationship_state.current_building_management_relationship_count ?? 0).toLocaleString()} mgmt / {Number(proposal.current_relationship_state.current_truth_claim_count ?? 0).toLocaleString()} truth; ledger source-ready: {proposal.current_relationship_state.current_ledger_source_ready ? 'yes' : 'no'}
                                    </span>
                                  )}
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                      <div className="mt-2 text-[10px] text-sky-700">
                        {truthVerificationFrontier.safe_action}
                      </div>
                    </div>
                  )}
                  {truthSourceAcquisitionWorklist && (
                    <div className="mt-2 rounded border border-emerald-100 bg-emerald-50 px-2 py-2">
                      <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between">
                        <div>
                          <div className="text-[10px] uppercase text-emerald-700">Source-acquisition worklist</div>
                          <div className="text-xs font-semibold text-gray-800">
                            {Number(truthSourceAcquisitionWorklist.work_item_count ?? 0).toLocaleString()} work items / {Number(truthSourceAcquisitionWorklist.hpd_work_item_count ?? 0).toLocaleString()} HPD work items
                          </div>
                          <div className="mt-0.5 text-[10px] text-gray-600">
                            Requests {Number(truthSourceAcquisitionWorklist.request_count ?? 0).toLocaleString()} / record-ready {Number(truthSourceAcquisitionWorklist.recording_ready_count ?? 0).toLocaleString()} / approval-required {Number(truthSourceAcquisitionWorklist.approval_required_count ?? 0).toLocaleString()}.
                          </div>
                          <div className="mt-0.5 text-[10px] text-emerald-800">
                            {truthSourceAcquisitionWorklist.safe_action || 'Use this as a human/source-acquisition checklist only.'}
                          </div>
                          <div className="mt-0.5 text-[10px] text-emerald-800">
                            Filled paste-back or HPD audit JSON can be checked through source-evidence intake preview before manual-evidence recording.
                          </div>
                            {truthSourceAcquisitionWorklist.csv_template_command && (
                              <div className="mt-0.5 text-[10px] text-emerald-800">
                                CSV paste-back: <code className="font-mono">{truthSourceAcquisitionWorklist.csv_template_command}</code>
                                {truthSourceAcquisitionWorklist.candidate_csv_preview_command ? (
                                  <>; preview filled CSV with <code className="font-mono">{truthSourceAcquisitionWorklist.candidate_csv_preview_command}</code>.</>
                                ) : null}
                              </div>
                            )}
                            {truthSourceAcquisitionWorklist.hpd_fetch_packet_command && (
                              <div className="mt-0.5 text-[10px] text-emerald-800">
                                Official HPD fetch packet: <code className="font-mono">{truthSourceAcquisitionWorklist.hpd_fetch_packet_command}</code>.
                              </div>
                            )}
                            {truthSourceAcquisitionWorklist.operator_confirmation_packet_command && (
                              <div className="mt-0.5 text-[10px] text-emerald-800">
                                Operator confirmation packet: <code className="font-mono">{truthSourceAcquisitionWorklist.operator_confirmation_packet_command}</code>.
                              </div>
                            )}
                            <div className="mt-0.5 text-[10px] text-emerald-800">
                              Batch replay from a reviewed preview requires --execute --confirm-execute --confirm-batch-execute.
                            </div>
                            <div className="mt-2 rounded border border-emerald-100 bg-white px-2 py-2">
                              <label htmlFor="source-overlap-candidate-json" className="block text-[10px] font-semibold uppercase text-emerald-700">
                                Candidate JSON preview
                              </label>
                              <textarea
                                id="source-overlap-candidate-json"
                                value={sourceEvidenceCandidateJson}
                                onChange={(event) => setSourceEvidenceCandidateJson(event.target.value)}
                                rows={3}
                                className="mt-1 w-full rounded border border-emerald-100 px-2 py-1 font-mono text-[10px] text-gray-700 focus:border-emerald-300 focus:outline-none focus:ring-1 focus:ring-emerald-200"
                                placeholder='{"source_evidence_intake_candidates":[...]} or {"source_acquisition_clues":[...]}'
                              />
                              <div className="mt-1 flex flex-wrap items-center gap-2">
                                <button
                                  type="button"
                                  onClick={handleSourceOverlapCandidatePreview}
                                  disabled={sourceOverlapBlockerPreviewMut.isPending}
                                  className="rounded bg-emerald-600 px-2 py-1 text-[10px] font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
                                >
                                  {sourceOverlapBlockerPreviewMut.isPending ? 'Previewing...' : 'Preview blocker packet'}
                                </button>
                                <span className="text-[10px] text-emerald-800">
                                  Read-only: returns the approval packet, not a write.
                                </span>
                              </div>
                              {sourceEvidenceCandidateJsonError && (
                                <div className="mt-1 text-[10px] text-rose-700">{sourceEvidenceCandidateJsonError}</div>
                              )}
                              {sourceOverlapBlockerPreviewMut.data && (
                                <div className="mt-1 text-[10px] text-emerald-800">
                                  Candidate blocker report is using the reviewed preview response below.
                                </div>
                              )}
                            </div>
                          <div className="mt-0.5 text-[10px] text-emerald-800">
                            After any approved evidence recording, rerun adjudication, post-recording proof, truth health, and runtime audit; verification candidates may still remain zero.
                          </div>
                        </div>
                        <div className="rounded border border-emerald-100 bg-white px-1.5 py-0.5 text-[10px] text-emerald-700">
                          no writes
                        </div>
                      </div>
                      {sourceAcquisitionWorkItems.length > 0 && (
                        <div className="mt-2 divide-y divide-emerald-100 rounded border border-emerald-100 bg-white">
                          {sourceAcquisitionWorkItems.map((item) => {
                            const relationshipLabel = item.relationship.relationship_label
                              || [item.relationship.manager_name, item.relationship.address || item.relationship.bbl].filter(Boolean).join(' / ')
                              || item.work_item_id;
                            const firstReviewedFinding = item.reviewed_source_findings?.[0];
                            const pasteBackFields = (item.paste_back_fields ?? []).slice(0, 5).map((field) => formatLabel(field)).join(', ');
                            return (
                              <div key={item.work_item_id} className="px-2 py-1.5 text-[10px] text-gray-600">
                                <div className="flex flex-wrap items-center gap-1">
                                  <span className="font-semibold text-gray-800">{relationshipLabel}</span>
                                  <span className="rounded border border-emerald-100 bg-emerald-50 px-1.5 py-0.5 text-emerald-700">
                                    priority {item.priority}
                                  </span>
                                  {item.strict_manager_gap_status && (
                                    <span className="rounded border border-amber-100 bg-amber-50 px-1.5 py-0.5 text-amber-700">
                                      {item.strict_manager_gap_status.replace(/_/g, ' ')}
                                    </span>
                                  )}
                                </div>
                                <div className="mt-0.5">
                                  BBL {item.relationship.bbl || 'n/a'} / {item.request_type?.replace(/_/g, ' ') || 'source acquisition'}
                                </div>
                                {item.evidence_need && (
                                  <div className="mt-0.5 text-gray-500">{item.evidence_need}</div>
                                )}
                                {(item.source_family_needs ?? []).length > 0 && (
                                  <div className="mt-1 flex flex-wrap gap-1">
                                    {(item.source_family_needs ?? []).slice(0, 5).map((source) => (
                                      <span key={source} className="rounded border border-gray-100 bg-gray-50 px-1.5 py-0.5 text-gray-600">
                                        {formatLabel(source)}
                                      </span>
                                    ))}
                                  </div>
                                )}
                                {item.operator_confirmation_request && (
                                  <div className="mt-1 rounded border border-cyan-100 bg-cyan-50 px-1.5 py-1 text-cyan-800">
                                    <div className="font-medium">Operator confirmation request</div>
                                    {item.operator_confirmation_request.question_prompt && (
                                      <div className="mt-0.5">{item.operator_confirmation_request.question_prompt}</div>
                                    )}
                                    {item.operator_confirmation_request.non_duplicate_boundary && (
                                      <div className="mt-0.5 text-cyan-700">{item.operator_confirmation_request.non_duplicate_boundary}</div>
                                    )}
                                    {item.operator_confirmation_request.contradiction_handling && (
                                      <div className="mt-0.5 text-cyan-700">{item.operator_confirmation_request.contradiction_handling}</div>
                                    )}
                                    {item.operator_confirmation_request.preview_command && (
                                      <code className="mt-0.5 block whitespace-pre-wrap break-all font-mono text-[9px] leading-snug">
                                        {item.operator_confirmation_request.preview_command}
                                      </code>
                                    )}
                                  </div>
                                )}
                                {item.read_only_hpd_preview_command && (
                                  <div className="mt-1 rounded border border-emerald-100 bg-emerald-50 px-1.5 py-1 text-emerald-800">
                                    <div className="font-medium">Read-only HPD packet</div>
                                    <code className="mt-0.5 block whitespace-pre-wrap break-all font-mono text-[9px] leading-snug">
                                      {item.read_only_hpd_preview_command}
                                    </code>
                                  </div>
                                )}
                                <div className="mt-1 text-[10px] text-gray-500">
                                  Paste-back fields: {pasteBackFields || 'n/a'}{(item.paste_back_fields ?? []).length > 5 ? ' ...' : ''}
                                </div>
                                {firstReviewedFinding && (
                                  <div className="mt-1 rounded border border-amber-100 bg-amber-50 px-1.5 py-1 text-amber-800">
                                    Reviewed dead end: {firstReviewedFinding.source_family?.replace(/_/g, ' ') || 'source review'}
                                    {(firstReviewedFinding.qualification || firstReviewedFinding.finding) ? ` - ${firstReviewedFinding.qualification || firstReviewedFinding.finding}` : ''}
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  )}
                  {truthSourceOverlapBlockerReport && (
                    <div className="mt-2 rounded border border-rose-100 bg-rose-50 px-2 py-2">
                      <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between">
                        <div>
                          <div className="text-[10px] uppercase text-rose-700">Source-overlap blocker report</div>
                          <div className="text-xs font-semibold text-gray-800">
                            {formatLabel(truthSourceOverlapBlockerReport.status, 'not_checked')}
                          </div>
                          <div className="mt-0.5 text-[10px] text-gray-600">
                            Source-ready {Number(truthSourceOverlapBlockerReport.source_ready_fact_group_count ?? 0).toLocaleString()} / verification candidates {Number(truthSourceOverlapBlockerReport.verification_candidate_count ?? 0).toLocaleString()} / record-ready {Number(sourceOverlapEvidenceRequestSummary?.recording_ready_count ?? 0).toLocaleString()}.
                          </div>
                          <div className="mt-0.5 text-[10px] text-gray-600">
                            Requests {Number(sourceOverlapEvidenceRequestSummary?.request_count ?? 0).toLocaleString()} / reviewed source findings {Number(sourceOverlapEvidenceRequestSummary?.reviewed_source_finding_count ?? 0).toLocaleString()}.
                          </div>
                          {sourceEvidenceCandidateSummary && (
                            <div className="mt-1 rounded border border-white bg-white px-1.5 py-1 text-[10px] text-gray-700">
                              Candidate preview: {(sourceEvidenceCandidateSummary.status || 'not_checked').replace(/_/g, ' ')}
                              {' '}({Number(sourceEvidenceCandidateSummary.recording_ready_count ?? 0).toLocaleString()} record-ready preview / {Number(sourceEvidenceCandidateSummary.recommended_count ?? 0).toLocaleString()} recommended).
                              {Number(sourceEvidenceCandidateSummary.source_acquisition_clue_count ?? 0) > 0 && (
                                <span className="ml-1 text-amber-700">
                                  Source clues: {Number(sourceEvidenceCandidateSummary.source_acquisition_clue_count ?? 0).toLocaleString()} primary-source review required.
                                </span>
                              )}
                              {sourceEvidenceCandidateSummary.allowed_execute === false && (
                                <span className="ml-1 text-rose-700">Execution still requires explicit approval.</span>
                              )}
                              {sourceOverlapBridgeAssessment?.can_request_recording_approval && (
                                <div className="mt-1 rounded border border-amber-100 bg-amber-50 px-1.5 py-1 text-amber-900">
                                  Approval-ready preview: {Number(sourceOverlapBridgeAssessment?.candidate_recommended_count ?? 0).toLocaleString()} recommended row(s), {Number(sourceOverlapBridgeAssessment?.candidate_recording_ready_count ?? 0).toLocaleString()} record-ready preview row(s). No execution is allowed from this screen.
                                </div>
                              )}
                              {sourceOverlapBridgeAssessment?.approval_boundary && (
                                <div className="mt-1 text-gray-600">
                                  {sourceOverlapBridgeAssessment.approval_boundary}
                                </div>
                              )}
                              {sourceEvidenceCandidateClues.length > 0 && (
                                <div className="mt-1 grid gap-1">
                                  {sourceEvidenceCandidateClues.map((clue, index) => {
                                    const key = String(clue.clue_id || clue.bbl || clue.address || clue.source_family || `source-clue-${index}`);
                                    const address = String(clue.address || clue.relationship_label || clue.bbl || 'source clue');
                                    const status = String(clue.clue_status || 'source_clue_only');
                                    return (
                                      <div key={key} className="rounded border border-amber-100 bg-amber-50 px-1.5 py-1 text-amber-900">
                                        {address} / {status.replace(/_/g, ' ')}
                                      </div>
                                    );
                                  })}
                                </div>
                              )}
                              {sourceEvidenceCandidateSummary.recording_approval_packet?.approval_question && (
                                <div className="mt-1 text-rose-700">
                                  {sourceEvidenceCandidateSummary.recording_approval_packet.approval_question}
                                </div>
                              )}
                              {sourceEvidenceCandidateSummary.recording_approval_packet?.execute_command_after_approval && (
                                <code className="mt-1 block whitespace-pre-wrap break-all font-mono text-[9px] leading-snug text-gray-700">
                                  {sourceEvidenceCandidateSummary.recording_approval_packet.execute_command_after_approval}
                                </code>
                              )}
                              {Number(sourceEvidenceCandidateSummary.recording_approval_packet?.manual_evidence_payload_count ?? 0) > 0 && (
                                <div className="mt-1 text-gray-600">
                                  Replay payloads: {Number(sourceEvidenceCandidateSummary.recording_approval_packet?.manual_evidence_payload_count ?? 0).toLocaleString()} exact manual-evidence row(s).
                                </div>
                              )}
                              {sourceEvidenceExpectedOverlap && (
                                <div className="mt-1 rounded border border-amber-100 bg-amber-50 px-1.5 py-1 text-amber-900">
                                  Expected after recording: {Number(sourceEvidenceExpectedOverlap.first_source_only_after_recording_count ?? 0).toLocaleString()} first-source-only row(s), {Number(sourceEvidenceExpectedOverlap.source_ready_after_recording_count ?? 0).toLocaleString()} source-ready row(s).
                                  {sourceEvidenceExpectedOverlap.safe_action && (
                                    <div className="mt-0.5 text-amber-800">{sourceEvidenceExpectedOverlap.safe_action}</div>
                                  )}
                                </div>
                              )}
                              {sourceEvidenceCandidatePayloadReview.length > 0 && (
                                <div className="mt-1 grid gap-1">
                                  {sourceEvidenceCandidatePayloadReview.map((payload) => {
                                    const key = String(payload.payload_index || payload.object_id || payload.source_record_id || 'payload');
                                    const manager = String(payload.manager_name || payload.extracted_value || 'manager');
                                    const objectId = String(payload.object_id || payload.bbl || 'building');
                                    const source = String(payload.source_name || 'source');
                                    return (
                                      <div key={key} className="rounded border border-emerald-100 bg-emerald-50 px-1.5 py-1 text-emerald-900">
                                        {manager} / {objectId} / {source}
                                      </div>
                                    );
                                  })}
                                </div>
                              )}
                              {sourceEvidenceCandidateSummary.recording_approval_packet?.safe_action && (
                                <div className="mt-1 text-gray-600">
                                  {sourceEvidenceCandidateSummary.recording_approval_packet.safe_action}
                                </div>
                              )}
                              {sourceEvidenceCandidateRelationships.length > 0 && (
                                <div className="mt-1 flex flex-wrap gap-1">
                                  {sourceEvidenceCandidateRelationships.map((relationship) => {
                                    const key = String(relationship.work_item_id || relationship.bbl || relationship.relationship_label || relationship.address || 'candidate');
                                    const label = String(relationship.relationship_label || relationship.address || relationship.bbl || 'Preview candidate');
                                    return (
                                      <span key={key} className="rounded border border-emerald-100 bg-emerald-50 px-1.5 py-0.5 text-emerald-800">
                                        {label}
                                      </span>
                                    );
                                  })}
                                </div>
                              )}
                            </div>
                          )}
                          {sourceOverlapBlockerReasons.length > 0 && (
                            <div className="mt-1 flex flex-wrap gap-1">
                              {sourceOverlapBlockerReasons.map((reason) => (
                                <span key={reason} className="rounded border border-rose-100 bg-white px-1.5 py-0.5 text-[10px] text-rose-700">
                                  {reason.replace(/_/g, ' ')}
                                </span>
                              ))}
                            </div>
                          )}
                          {sourceOverlapThresholdRelationships.length > 0 && (
                            <div className="mt-2 rounded border border-amber-100 bg-amber-50 px-1.5 py-1 text-[10px] text-amber-900">
                              <div className="font-semibold">Threshold-sensitive relationships</div>
                              <div className="mt-0.5 text-amber-800">
                                These source-ready facts could clear the verified threshold with one stronger exact role-specific source, but none is recording-ready from this report.
                              </div>
                              <div className="mt-1 grid gap-1">
                                {sourceOverlapThresholdRelationships.map((relationship) => (
                                  <div key={relationship.relationship_label || relationship.bbl || relationship.address || 'threshold-sensitive'} className="rounded border border-amber-100 bg-white px-1.5 py-1 text-gray-700">
                                    <div className="font-semibold text-gray-800">
                                      {relationship.relationship_label || [relationship.manager_name, relationship.address || relationship.bbl].filter(Boolean).join(' / ') || 'Threshold-sensitive relationship'}
                                    </div>
                                    <div className="mt-0.5">
                                      Current {formatPercent(Number(relationship.current_confidence_score ?? 0))} / best single source {relationship.best_single_source?.replace(/_/g, ' ') || 'source'} to {formatPercent(Number(relationship.best_single_source_simulated_confidence ?? 0))}
                                      {typeof relationship.score_gap_to_verified === 'number' && (
                                        <> / gap {formatPercent(relationship.score_gap_to_verified)}</>
                                      )}
                                    </div>
                                    {relationship.required_bundle_sources && relationship.required_bundle_sources.length > 0 && (
                                      <div className="mt-1 flex flex-wrap gap-1">
                                        {relationship.required_bundle_sources.slice(0, 5).map((source) => (
                                          <span key={source} className="rounded border border-gray-100 bg-gray-50 px-1.5 py-0.5 text-gray-600">
                                            {source.replace(/_/g, ' ')}
                                          </span>
                                        ))}
                                      </div>
                                    )}
                                    <div className="mt-0.5 text-amber-800">
                                      recording-ready: {relationship.recording_ready ? 'yes' : 'no'} / approval before recording: {relationship.approval_required_before_recording ? 'yes' : 'no'}
                                    </div>
                                    {relationship.reviewed_source_history_status && (
                                      <div className="mt-0.5 text-amber-800">
                                        reviewed: {relationship.reviewed_source_history_status.replace(/_/g, ' ')}
                                        {typeof relationship.required_real_evidence_count === 'number' && (
                                          <> / real evidence fields: {relationship.required_real_evidence_count.toLocaleString()}</>
                                        )}
                                      </div>
                                    )}
                                    {relationship.reviewed_source_findings && relationship.reviewed_source_findings.length > 0 && (
                                      <div className="mt-0.5 text-gray-500">
                                        latest reviewed source: {relationship.reviewed_source_findings[0]?.source_family?.replace(/_/g, ' ') || 'source history'}
                                      </div>
                                    )}
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}
                          <div className="mt-1 text-[10px] text-rose-800">
                            {truthSourceOverlapBlockerReport.safe_action}
                          </div>
                        </div>
                        <div className="rounded border border-rose-100 bg-white px-1.5 py-0.5 text-[10px] text-rose-700">
                          proof only
                        </div>
                      </div>
                      {sourceOverlapBlockerTopRelationships.length > 0 && (
                        <div className="mt-2 divide-y divide-rose-100 rounded border border-rose-100 bg-white">
                          {sourceOverlapBlockerTopRelationships.map((relationship) => (
                            <div key={relationship.work_item_id || relationship.relationship_label || relationship.bbl} className="px-2 py-1.5 text-[10px] text-gray-600">
                              <div className="font-semibold text-gray-800">
                                {relationship.relationship_label || [relationship.manager_name, relationship.address || relationship.bbl].filter(Boolean).join(' / ') || 'Blocked relationship'}
                              </div>
                              <div className="mt-0.5">
                                BBL {relationship.bbl || 'n/a'} / {relationship.strict_manager_gap_status?.replace(/_/g, ' ') || relationship.request_type?.replace(/_/g, ' ') || 'source gap'}
                              </div>
                              {relationship.source_family_needs && relationship.source_family_needs.length > 0 && (
                                <div className="mt-1 flex flex-wrap gap-1">
                                  {relationship.source_family_needs.slice(0, 5).map((source) => (
                                    <span key={source} className="rounded border border-gray-100 bg-gray-50 px-1.5 py-0.5 text-gray-600">
                                      {source.replace(/_/g, ' ')}
                                    </span>
                                  ))}
                                </div>
                              )}
                              {relationship.post_fetch_local_extract_command && (
                                <code className="mt-1 block whitespace-pre-wrap break-all rounded border border-rose-100 bg-rose-50 px-1.5 py-1 font-mono text-[9px] leading-snug text-rose-800">
                                  {relationship.post_fetch_local_extract_command}
                                </code>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                  {verifiedConfidenceGapPlan && (
                    <div className="mt-2 rounded border border-amber-100 bg-amber-50 px-2 py-2">
                      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                        <div>
                          <div className="text-[10px] uppercase text-amber-700">Verified confidence gap</div>
                          <div className="text-xs font-semibold text-gray-800">
                            {verifiedConfidenceGapPlan.proposal_count.toLocaleString()} source-ready fact{verifiedConfidenceGapPlan.proposal_count === 1 ? '' : 's'} below verified threshold
                          </div>
                          {typeof verifiedConfidenceGapPlan.single_source_upgrade_would_verify_count === 'number' && (
                            <div className="mt-0.5 text-[10px] text-gray-600">
                              One-source upgrades clearing threshold: {verifiedConfidenceGapPlan.single_source_upgrade_would_verify_count.toLocaleString()} / {verifiedConfidenceGapPlan.proposal_count.toLocaleString()}
                              {verifiedConfidenceGapPlan.best_single_source_upgrade_overall && (
                                <span>
                                  {' '}best {formatPercent(verifiedConfidenceGapPlan.best_single_source_upgrade_overall.simulated_confidence_score)}
                                </span>
                              )}
                            </div>
                          )}
                          {typeof verifiedConfidenceGapPlan.bundle_upgrade_would_verify_count === 'number' && (
                            <div className="mt-0.5 text-[10px] text-gray-600">
                              Suggested bundles clearing threshold: {verifiedConfidenceGapPlan.bundle_upgrade_would_verify_count.toLocaleString()} / {verifiedConfidenceGapPlan.proposal_count.toLocaleString()}
                              {verifiedConfidenceGapPlan.best_bundle_upgrade_overall && (
                                <span>
                                  {' '}best {formatPercent(verifiedConfidenceGapPlan.best_bundle_upgrade_overall.simulated_confidence_score)}
                                </span>
                              )}
                            </div>
                          )}
                        </div>
                        <div className="rounded border border-amber-100 bg-white px-1.5 py-0.5 text-[10px] text-amber-700">
                          no status change
                        </div>
                      </div>
                      {verifiedConfidenceGapProposals.length > 0 && (
                        <div className="mt-2 divide-y divide-amber-100 rounded border border-amber-100 bg-white">
                          {verifiedConfidenceGapProposals.map((proposal) => (
                            <div key={`${proposal.fact_key.subject_type}-${proposal.fact_key.subject_id}-${proposal.fact_key.predicate}-${proposal.fact_key.object_id || proposal.fact_key.normalized_value || ''}`} className="px-2 py-2">
                              <div className="text-xs font-semibold text-gray-800">{proposal.fact_key.predicate.replace(/_/g, ' ')}</div>
                              <div className="mt-1 flex flex-wrap gap-1 text-[10px]">
                                <span className="rounded border border-amber-100 bg-amber-50 px-1.5 py-0.5 text-amber-700">
                                  score {formatPercent(proposal.recomputed_confidence_score)}
                                </span>
                                <span className="rounded border border-amber-100 bg-amber-50 px-1.5 py-0.5 text-amber-700">
                                  gap {formatPercent(proposal.score_gap_to_verified)}
                                </span>
                                {typeof proposal.average_supporting_source_quality === 'number' && (
                                  <span className="rounded border border-gray-100 bg-gray-50 px-1.5 py-0.5 text-gray-600">
                                    avg quality {formatPercent(proposal.average_supporting_source_quality)}
                                  </span>
                                )}
                                {proposal.suggested_quality_upgrade_sources.slice(0, 3).map((source) => (
                                  <span key={source} className="rounded border border-gray-100 bg-gray-50 px-1.5 py-0.5 text-gray-600">
                                    {source.replace(/_/g, ' ')}
                                  </span>
                                ))}
                              </div>
                              {proposal.best_single_source_upgrade && (
                                <div className="mt-1 rounded border border-amber-100 bg-amber-50 px-2 py-1 text-[10px] text-amber-800">
                                  Best one-source upgrade: {proposal.best_single_source_upgrade.suggested_source.replace(/_/g, ' ')} - score {formatPercent(proposal.best_single_source_upgrade.simulated_confidence_score)}
                                  {' '}({proposal.best_single_source_upgrade.would_reach_verified_threshold ? 'would clear verified threshold' : 'still below verified'})
                                </div>
                              )}
                              {proposal.simulated_quality_bundle_upgrade && (
                                <div className="mt-1 rounded border border-gray-100 bg-gray-50 px-2 py-1 text-[10px] text-gray-700">
                                  Suggested bundle: score {formatPercent(proposal.simulated_quality_bundle_upgrade.simulated_confidence_score)}
                                  {' '}({proposal.simulated_quality_bundle_upgrade.would_reach_verified_threshold ? 'would clear verified threshold' : 'still below verified'})
                                  {proposal.simulated_quality_bundle_upgrade.acquisition_required && (
                                    <span>
                                      {' '} / acquisition required
                                    </span>
                                  )}
                                </div>
                              )}
                              {proposal.simulated_quality_upgrades && proposal.simulated_quality_upgrades.length > 0 && (
                                <div className="mt-1 flex flex-wrap gap-1 text-[10px]">
                                  {proposal.simulated_quality_upgrades.slice(0, 3).map((upgrade) => (
                                    <span key={`${upgrade.suggested_source}-${upgrade.simulated_supporting_source_name}`} className="rounded border border-gray-100 bg-gray-50 px-1.5 py-0.5 text-gray-600">
                                      {upgrade.suggested_source.replace(/_/g, ' ')}: {formatPercent(upgrade.simulated_confidence_score)}
                                      {' '}{upgrade.would_reach_verified_threshold ? 'verifies' : 'below'}
                                    </span>
                                  ))}
                                </div>
                              )}
                              <div className="mt-1 text-[10px] text-gray-600">{proposal.safe_action}</div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                  {adjudicationSamples.length > 0 ? (
                    <div className="mt-2 divide-y divide-gray-100 rounded border border-gray-100">
                      {adjudicationSamples.map((sample) => {
                        const sampleConfidence = sample.proposed_confidence ?? sample.recomputed_confidence_score;
                        return (
                          <div key={`${sample.fact_key.subject_type}-${sample.fact_key.subject_id}-${sample.fact_key.predicate}-${sample.fact_key.object_id || sample.fact_key.normalized_value || ''}`} className="px-2 py-2">
                            <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                              <div className="min-w-0">
                                <div className="text-xs font-semibold text-gray-800">{sample.fact_key.predicate.replace(/_/g, ' ')}</div>
                                <div className="mt-0.5 break-all text-[11px] text-gray-500">
                                  {sample.fact_key.subject_type}:{sample.fact_key.subject_id}
                                  {sample.fact_key.object_type && sample.fact_key.object_id ? ` -> ${sample.fact_key.object_type}:${sample.fact_key.object_id}` : ''}
                                </div>
                                <div className="mt-1 flex flex-wrap gap-1 text-[10px]">
                                  <span className="rounded border border-gray-100 bg-gray-50 px-1.5 py-0.5 text-gray-600">
                                    confidence {formatPercent(sampleConfidence)}
                                  </span>
                                  {typeof sample.score_gap_to_verified === 'number' && sample.score_gap_to_verified > 0 && (
                                    <span className="rounded border border-amber-100 bg-amber-50 px-1.5 py-0.5 text-amber-700">
                                      gap to verified {formatPercent(sample.score_gap_to_verified)}
                                    </span>
                                  )}
                                  <span className={`rounded border px-1.5 py-0.5 ${sample.safe_to_mark_verified ? 'border-green-100 bg-green-50 text-green-700' : 'border-amber-100 bg-amber-50 text-amber-700'}`}>
                                    {sample.proposed_belief_status.replace(/_/g, ' ')}
                                  </span>
                                  <span className="rounded border border-gray-100 bg-gray-50 px-1.5 py-0.5 text-gray-600">
                                    queue: {sample.recommended_queue.replace(/_/g, ' ')}
                                  </span>
                                </div>
                                {typeof sample.confidence_rationale?.average_supporting_source_quality === 'number' && (
                                  <div className="mt-1 text-[10px] text-gray-500">
                                    Avg source quality {formatPercent(sample.confidence_rationale.average_supporting_source_quality)}
                                    {typeof sample.confidence_rationale.raw_confidence_before_smoothing === 'number' ? ` / raw confidence ${formatPercent(sample.confidence_rationale.raw_confidence_before_smoothing)}` : ''}
                                  </div>
                                )}
                                {sample.blockers.length > 0 && (
                                  <div className="mt-1 text-[10px] text-gray-500">
                                    Blocked by {sample.blockers.slice(0, 3).map((blocker) => blocker.replace(/_/g, ' ')).join(', ')}
                                  </div>
                                )}
                              </div>
                              <div className="shrink-0 text-left text-[10px] text-gray-500 sm:text-right">
                                {(sample.supporting_source_count ?? sample.supporting_sources.length).toLocaleString()} source{(sample.supporting_source_count ?? sample.supporting_sources.length) === 1 ? '' : 's'} / {sample.supporting_evidence_count.toLocaleString()} evidence
                                {sample.contradicting_evidence_count > 0 ? ` / ${sample.contradicting_evidence_count.toLocaleString()} contradictions` : ''}
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="mt-2 rounded border border-gray-100 bg-gray-50 px-2 py-2 text-xs text-gray-500">
                      No adjudication samples returned yet.
                    </div>
                  )}
                </div>
              )}
              <div className="mt-3 rounded border border-white bg-white px-3 py-2">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                  <div>
                    <div className="text-[10px] uppercase text-gray-500">Manual evidence preview</div>
                    <div className="text-xs font-semibold text-gray-800">Operator-reviewed claim evidence</div>
                  </div>
                  <div className="rounded border border-green-100 bg-green-50 px-2 py-0.5 text-[10px] font-medium text-green-700">
                    dry run
                  </div>
                </div>
                <div className="mt-3 grid gap-2 md:grid-cols-3">
                  <label className="grid gap-1 text-[10px] uppercase text-gray-500">
                    Subject
                    <div className="grid grid-cols-[90px_1fr] gap-1">
                      <select
                        value={manualEvidenceDraft.subject_type}
                        onChange={(event) => setManualEvidenceField('subject_type', event.target.value)}
                        className="rounded border border-gray-200 bg-white px-2 py-1 text-xs normal-case text-gray-800"
                      >
                        <option value="lead">lead</option>
                        <option value="building">building</option>
                        <option value="canonical_entity">entity</option>
                        <option value="contact">contact</option>
                      </select>
                      <input
                        value={manualEvidenceDraft.subject_id}
                        onChange={(event) => setManualEvidenceField('subject_id', event.target.value)}
                        className="rounded border border-gray-200 px-2 py-1 text-xs normal-case text-gray-800"
                      />
                    </div>
                  </label>
                  <label className="grid gap-1 text-[10px] uppercase text-gray-500">
                    Predicate
                    <input
                      value={manualEvidenceDraft.predicate}
                      onChange={(event) => setManualEvidenceField('predicate', event.target.value)}
                      className="rounded border border-gray-200 px-2 py-1 text-xs normal-case text-gray-800"
                    />
                  </label>
                  <label className="grid gap-1 text-[10px] uppercase text-gray-500">
                    Object
                    <div className="grid grid-cols-[90px_1fr] gap-1">
                      <select
                        value={manualEvidenceDraft.object_type}
                        onChange={(event) => setManualEvidenceField('object_type', event.target.value)}
                        className="rounded border border-gray-200 bg-white px-2 py-1 text-xs normal-case text-gray-800"
                      >
                        <option value="building">building</option>
                        <option value="lead">lead</option>
                        <option value="canonical_entity">entity</option>
                        <option value="contact">contact</option>
                      </select>
                      <input
                        value={manualEvidenceDraft.object_id}
                        onChange={(event) => setManualEvidenceField('object_id', event.target.value)}
                        className="rounded border border-gray-200 px-2 py-1 text-xs normal-case text-gray-800"
                      />
                    </div>
                  </label>
                  <label className="grid gap-1 text-[10px] uppercase text-gray-500">
                    Claim type
                    <input
                      value={manualEvidenceDraft.claim_type}
                      onChange={(event) => setManualEvidenceField('claim_type', event.target.value)}
                      className="rounded border border-gray-200 px-2 py-1 text-xs normal-case text-gray-800"
                    />
                  </label>
                  <label className="grid gap-1 text-[10px] uppercase text-gray-500">
                    Value
                    <input
                      value={manualEvidenceDraft.normalized_value}
                      onChange={(event) => setManualEvidenceField('normalized_value', event.target.value)}
                      className="rounded border border-gray-200 px-2 py-1 text-xs normal-case text-gray-800"
                    />
                  </label>
                  <label className="grid gap-1 text-[10px] uppercase text-gray-500">
                    Support
                    <select
                      value={manualEvidenceDraft.support_status}
                      onChange={(event) => setManualEvidenceField('support_status', event.target.value)}
                      className="rounded border border-gray-200 bg-white px-2 py-1 text-xs normal-case text-gray-800"
                    >
                      <option value="supports">supports</option>
                      <option value="contradicts">contradicts</option>
                    </select>
                  </label>
                  <label className="grid gap-1 text-[10px] uppercase text-gray-500">
                    Source
                    <select
                      value={manualEvidenceDraft.source_name}
                      onChange={(event) => setManualEvidenceField('source_name', event.target.value)}
                      className="rounded border border-gray-200 bg-white px-2 py-1 text-xs normal-case text-gray-800"
                    >
                      <option value="manual_evidence">manual evidence</option>
                      <option value="operator_review">operator review</option>
                      <option value="company_website">company website</option>
                      <option value="google_places">google places</option>
                      <option value="ny_dos">ny dos</option>
                      <option value="outreach_confirmed">outreach confirmed</option>
                    </select>
                  </label>
                  <label className="grid gap-1 text-[10px] uppercase text-gray-500">
                    Record ID
                    <input
                      value={manualEvidenceDraft.source_record_id}
                      onChange={(event) => setManualEvidenceField('source_record_id', event.target.value)}
                      className="rounded border border-gray-200 px-2 py-1 text-xs normal-case text-gray-800"
                    />
                  </label>
                  <label className="grid gap-1 text-[10px] uppercase text-gray-500">
                    Source URL
                    <input
                      value={manualEvidenceDraft.source_url}
                      onChange={(event) => setManualEvidenceField('source_url', event.target.value)}
                      className="rounded border border-gray-200 px-2 py-1 text-xs normal-case text-gray-800"
                    />
                  </label>
                </div>
                <label className="mt-2 grid gap-1 text-[10px] uppercase text-gray-500">
                  Note
                  <textarea
                    value={manualEvidenceDraft.note}
                    onChange={(event) => setManualEvidenceField('note', event.target.value)}
                    rows={2}
                    className="rounded border border-gray-200 px-2 py-1 text-xs normal-case text-gray-800"
                  />
                </label>
                <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                  <button
                    type="button"
                    onClick={() => manualEvidencePreview.mutate()}
                    disabled={manualEvidencePreview.isPending}
                    className="px-3 py-1.5 text-sm bg-white text-gray-700 border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-50 font-medium"
                  >
                    {manualEvidencePreview.isPending ? 'Previewing...' : 'Preview Manual Evidence'}
                  </button>
                  <div className="text-xs text-gray-500">Execution remains API/CLI gated with confirm_execute=true.</div>
                </div>
                {manualEvidencePreview.data && (
                  <div className="mt-3 rounded border border-gray-100 bg-gray-50 px-3 py-2">
                    <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                      <div>
                        <div className="text-xs font-semibold text-gray-800">
                          {manualEvidencePreview.data.claim_spec?.predicate?.replace(/_/g, ' ') || 'manual evidence'}
                        </div>
                        <div className="mt-1 break-all text-[11px] text-gray-500">
                          {manualEvidencePreview.data.claim_spec?.claim_id} / {manualEvidencePreview.data.claim_spec?.evidence_id}
                        </div>
                      </div>
                      <div className={`shrink-0 rounded border px-2 py-0.5 text-[10px] font-medium ${manualEvidencePreview.data.allowed_execute ? severityClass('medium') : severityClass('low')}`}>
                        {manualEvidencePreview.data.allowed_execute ? 'execution ready' : 'preview only'}
                      </div>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1 text-[10px]">
                      <span className="rounded border border-gray-100 bg-white px-1.5 py-0.5 text-gray-600">
                        mutations planned: {manualEvidencePreview.data.mutations_planned.toLocaleString()}
                      </span>
                      <span className="rounded border border-gray-100 bg-white px-1.5 py-0.5 text-gray-600">
                        confidence {formatPercent(manualEvidencePreview.data.claim_spec?.confidence_score)}
                      </span>
                      <span className={`rounded border px-1.5 py-0.5 ${manualEvidencePreview.data.claim_spec?.support_status === 'contradicts' ? 'border-rose-100 bg-rose-50 text-rose-700' : 'border-green-100 bg-green-50 text-green-700'}`}>
                        {manualEvidencePreview.data.claim_spec?.support_status || 'supports'}
                      </span>
                    </div>
                    {manualEvidencePreview.data.rollback_strategy && (
                      <div className="mt-2 text-xs text-gray-500">
                        Rollback: {manualEvidencePreview.data.rollback_strategy}
                      </div>
                    )}
                  </div>
                )}
              </div>
              {materializationPreview && (
                <div className="mt-3 rounded border border-white bg-white px-3 py-2">
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                    <div>
                      <div className="text-[10px] uppercase text-gray-500">Ledger backfill preview</div>
                      <div className="text-xs font-semibold text-gray-800">
                        {materializationPreview.planned_claims_total.toLocaleString()} claims pending
                      </div>
                      <div className="mt-1 text-xs text-gray-500">
                        {materializationPreview.existing_claim_count.toLocaleString()} existing claims; {materializationPreview.existing_evidence_count.toLocaleString()} existing evidence rows.
                      </div>
                      <div className="mt-2 flex flex-wrap gap-1">
                        {Object.entries(materializationPreview.planned_claims_by_source)
                          .sort(([, left], [, right]) => Number(right) - Number(left))
                          .slice(0, 5)
                          .map(([source, count]) => (
                            <span key={source} className="rounded border border-gray-100 bg-gray-50 px-1.5 py-0.5 text-[10px] font-medium text-gray-600">
                              {source.replace(/_/g, ' ')}: {Number(count).toLocaleString()}
                            </span>
                          ))}
                      </div>
                      {materializationPreview.strict_materializable_claims_by_source && Object.keys(materializationPreview.strict_materializable_claims_by_source).length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1">
                          {Object.entries(materializationPreview.strict_materializable_claims_by_source).map(([source, count]) => (
                            <span key={source} className="rounded border border-emerald-100 bg-emerald-50 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700">
                              strict {source.replace(/_/g, ' ')}: {Number(count).toLocaleString()}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                    <div className={`shrink-0 rounded border px-2 py-0.5 text-[10px] font-medium ${materializationPreview.mutations_planned === 0 ? severityClass('low') : severityClass('high')}`}>
                      {materializationPreview.dry_run ? 'dry run' : 'execution'}{materializationPreview.source_filter_applied ? ' / filtered' : ''}
                    </div>
                  </div>
                  {materializedClaimSpecs.length > 0 ? (
                    <div className="mt-2 divide-y divide-gray-100 rounded border border-gray-100">
                      {materializedClaimSpecs.map((spec: TruthMaterializedClaimSpecPreview) => (
                        <div key={`${spec.claim_id}-${spec.evidence_id}`} className="px-2 py-2">
                          <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                            <div className="min-w-0">
                              <div className="text-xs font-semibold text-gray-800">{spec.predicate.replace(/_/g, ' ')}</div>
                              <div className="mt-0.5 break-all text-[11px] text-gray-500">
                                {spec.subject_type}:{spec.subject_id} {'->'} {spec.object_type}:{spec.object_id}
                              </div>
                              <div className="mt-1 flex flex-wrap gap-1 text-[10px]">
                                <span className="rounded border border-gray-100 bg-gray-50 px-1.5 py-0.5 text-gray-600">
                                  source: {spec.source_name.replace(/_/g, ' ')}
                                </span>
                                <span className="rounded border border-gray-100 bg-gray-50 px-1.5 py-0.5 text-gray-600">
                                  confidence {formatPercent(spec.confidence_score)}
                                </span>
                                <span className="rounded border border-gray-100 bg-gray-50 px-1.5 py-0.5 text-gray-600">
                                  {spec.freshness_days == null ? 'freshness n/a' : `${spec.freshness_days}d freshness`}
                                </span>
                                <span className={`rounded border px-1.5 py-0.5 ${spec.support_status === 'contradicts' ? 'border-rose-100 bg-rose-50 text-rose-700' : 'border-green-100 bg-green-50 text-green-700'}`}>
                                  {String(spec.support_status || 'supports').replace(/_/g, ' ')}
                                </span>
                              </div>
                            </div>
                            <div className="shrink-0 text-left sm:text-right">
                              <div className="text-[10px] uppercase text-gray-500">Action</div>
                              <div className="text-xs font-semibold text-gray-800">{String(spec.actionability_level || 'unknown').replace(/_/g, ' ')}</div>
                            </div>
                          </div>
                          <div className="mt-1 break-all font-mono text-[10px] text-gray-400">
                            {spec.claim_id} / {spec.evidence_id} / {spec.source_record_id}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="mt-2 rounded border border-gray-100 bg-gray-50 px-2 py-2 text-xs text-gray-500">
                      No sample claim/evidence specs returned for the current preview.
                    </div>
                  )}
                  <div className="mt-2 text-xs text-gray-500">
                    Rollback: {materializationPreview.rollback_strategy}
                  </div>
                </div>
              )}
            </div>
          )}
          {truthDashboard && (
            <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-3">
              {truthDashboard.actionability_rules.map((rule) => (
                <div key={rule.level} className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
                  <div className="text-xs font-semibold text-gray-800">{rule.level.replace(/_/g, ' ')}</div>
                  <div className="mt-1 text-xs text-gray-500">{rule.minimum}</div>
                  {actionabilityCriteria(rule).length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {actionabilityCriteria(rule).map((criterion) => (
                        <span key={criterion} className="rounded bg-white px-1.5 py-0.5 text-[10px] font-medium text-gray-600 ring-1 ring-gray-200">
                          {criterion}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
          {validationPreview && (
            <div className="mt-4 border-t border-gray-100 pt-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-xs font-semibold text-gray-800">Adversarial validation</div>
                  <div className="mt-1 text-xs text-gray-500">
                    {validationChecks.length.toLocaleString()} active checks. {validationMutationCount.toLocaleString()} mutations planned.
                  </div>
                </div>
                <div className="text-xs text-gray-500">Dry run</div>
              </div>
              {topValidationChecks.length > 0 ? (
                <div className="mt-3 divide-y divide-gray-100 border-y border-gray-100">
                  {topValidationChecks.map((check) => (
                    <div key={check.check} className="py-3">
                      <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
                        <div>
                          <div className="text-xs font-semibold text-gray-800">{check.check.replace(/_/g, ' ')}</div>
                          <div className="mt-1 text-xs text-gray-500">{check.why_it_matters}</div>
                        </div>
                        <div className={`shrink-0 rounded border px-2 py-1 text-xs font-medium ${severityClass(check.severity)}`}>
                          {check.severity} - {check.count_sampled.toLocaleString()}
                        </div>
                      </div>
                      <div className="mt-2 text-xs text-gray-500">
                        Queue: {check.recommended_queue.replace(/_/g, ' ')}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="mt-3 text-xs text-green-700 bg-green-50 border border-green-100 rounded p-2">
                  No sampled validation issues returned.
                </div>
              )}
            </div>
          )}
          {goldenBenchmark && (
            <div className="mt-4 border-t border-gray-100 pt-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-xs font-semibold text-gray-800">Golden benchmark</div>
                  <div className="mt-1 text-xs text-gray-500">
                    {goldenBenchmark.configured_cases.toLocaleString()} configured of {goldenBenchmark.total_cases.toLocaleString()} cases.
                  </div>
                </div>
                <div className="text-xs text-gray-500">
                  {goldenBenchmark.passed_cases.toLocaleString()} pass / {goldenBenchmark.failed_cases.toLocaleString()} fail
                </div>
              </div>
              <div className="mt-3 grid grid-cols-2 md:grid-cols-4 gap-2">
                {[
                  ['Precision', goldenBenchmark.metrics.precision],
                  ['Recall', goldenBenchmark.metrics.recall],
                  ['False merge', goldenBenchmark.metrics.false_merge_rate],
                  ['Freshness', goldenBenchmark.metrics.freshness_accuracy],
                ].map(([label, value]) => (
                  <div key={label as string} className="rounded-lg bg-gray-50 px-3 py-2">
                    <div className="text-[10px] uppercase text-gray-500">{label as string}</div>
                    <div className="text-sm font-semibold text-gray-800">{formatMetric(value as number | null)}</div>
                  </div>
                ))}
              </div>
              {goldenBenchmark.cases.some((item) => item.status === 'fail') && (
                <div className="mt-3 text-xs text-rose-700 bg-rose-50 border border-rose-200 rounded p-2">
                  Failing cases: {goldenBenchmark.cases.filter((item) => item.status === 'fail').slice(0, 3).map((item) => item.name).join(', ')}
                </div>
              )}
            </div>
          )}
          {reviewQueue && (
            <div className="mt-4 border-t border-gray-100 pt-4">
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-xs font-semibold text-gray-800">Review queue</div>
                  <div className="mt-1 text-xs text-gray-500">
                    Highest-priority items with dry-run decision previews.
                  </div>
                </div>
                <div className="text-xs text-gray-500">
                  {reviewQueue.source === 'schema_not_ready' ? 'schema gated' : `${reviewQueue.items.length.toLocaleString()} shown`}
                </div>
              </div>
              {reviewQueue.items.length > 0 ? (
                <div className="mt-3 divide-y divide-gray-100 rounded-lg border border-gray-100">
                  {reviewQueue.items.map((item) => {
                    const supporting = summarizeEvidence(item.supporting_evidence);
                    const contradicting = summarizeEvidence(item.contradicting_evidence);
                    const rationaleText = summarizeRationale(item);
                    return (
                      <div key={item.review_id} className="p-3">
                        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                          <div className="min-w-0">
                            <div className="text-xs font-semibold text-gray-800">{item.queue_name.replace(/_/g, ' ')}</div>
                            <div className="mt-1 text-xs text-gray-500">
                              {item.subject_type}:{item.subject_id} {item.actionability_level ? `- ${item.actionability_level.replace(/_/g, ' ')}` : ''}
                            </div>
                            <div className="mt-2 flex flex-wrap gap-2 text-[11px]">
                              {typeof item.confidence_score === 'number' && (
                                <span className="rounded border border-gray-100 bg-gray-50 px-2 py-0.5 text-gray-700">
                                  confidence {Math.round(item.confidence_score * 100)}%
                                </span>
                              )}
                              {supporting.length > 0 && (
                                <span className="rounded border border-green-100 bg-green-50 px-2 py-0.5 text-green-700">
                                  supports: {supporting.join(', ')}
                                </span>
                              )}
                              {contradicting.length > 0 && (
                                <span className="rounded border border-rose-100 bg-rose-50 px-2 py-0.5 text-rose-700">
                                  contradicts: {contradicting.join(', ')}
                                </span>
                              )}
                            </div>
                            {rationaleText && (
                              <div className="mt-2 text-xs text-gray-600">{rationaleText}</div>
                            )}
                          </div>
                          <div className="flex shrink-0 flex-wrap gap-2">
                            <button
                              onClick={() => previewDecision.mutate({ item, decision: 'approve' })}
                              disabled={previewDecision.isPending}
                              className="px-2 py-1 text-xs bg-green-50 text-green-700 rounded border border-green-100 hover:bg-green-100 disabled:opacity-50"
                            >
                              Preview approve
                            </button>
                            <button
                              onClick={() => previewDecision.mutate({ item, decision: 'reject' })}
                              disabled={previewDecision.isPending}
                              className="px-2 py-1 text-xs bg-rose-50 text-rose-700 rounded border border-rose-100 hover:bg-rose-100 disabled:opacity-50"
                            >
                              Preview reject
                            </button>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="mt-3 rounded border border-gray-100 bg-gray-50 px-3 py-2 text-xs text-gray-600">
                  {reviewQueue.source === 'schema_not_ready'
                    ? 'Review items are blocked until the truth-confidence schema is applied.'
                    : 'No open review items returned.'}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Signal Coverage */}
      {coverage && (
        <div className="bg-white rounded-lg border border-gray-200 p-5">
          <h3 className="text-sm font-medium text-gray-700 mb-3">Signal Coverage</h3>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
            {[
              { label: 'Complaints', count: coverage.with_complaints },
              { label: 'Violations', count: coverage.with_violations },
              { label: 'Transactions', count: coverage.with_transactions },
              { label: 'Permits', count: coverage.with_permits },
              { label: 'Litigation', count: coverage.with_litigation },
              { label: 'Emergency Repairs', count: coverage.with_erp },
              { label: 'Energy Grades', count: coverage.with_energy },
              { label: 'Evictions', count: coverage.with_evictions },
              { label: 'Facades', count: coverage.with_facades },
              { label: 'AEP', count: coverage.with_aep },
            ].map(s => {
              const pct = coverage.total_buildings > 0 ? (s.count / coverage.total_buildings * 100).toFixed(1) : '0';
              return (
                <div key={s.label} className="p-2">
                  <div className="text-xs text-gray-500">{s.label}</div>
                  <div className="text-sm font-medium">{s.count.toLocaleString()} <span className="text-gray-400">({pct}%)</span></div>
                  <div className="mt-1 h-1.5 bg-gray-100 rounded-full">
                    <div className="h-1.5 bg-blue-500 rounded-full" style={{ width: `${Math.min(100, parseFloat(pct))}%` }} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="bg-white rounded-lg border border-gray-200 p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h3 className="text-sm font-medium text-gray-700">Map Coordinate Backfill</h3>
            <p className="text-sm text-gray-500 mt-1">
              Persist coordinates and provenance for buildings that still rely on browser geocoding so portfolio maps load faster and with clearer source attribution.
            </p>
            {coordinateAudit && (
              <p className="text-xs text-gray-500 mt-2">
                Source status: <span className="font-medium text-gray-700">{coordinateAudit.status}</span>
                {coordinateAudit.last_run ? ` • last run ${new Date(coordinateAudit.last_run).toLocaleString()}` : ' • no run recorded yet'}
              </p>
            )}
          </div>
          <button
            onClick={() => triggerJob.mutate('building_coordinates')}
            disabled={triggerJob.isPending}
            className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 font-medium"
          >
            {triggerJob.isPending ? 'Checking...' : 'Preview Coordinate Sync'}
          </button>
        </div>
      </div>

      {/* Source Freshness */}
      {quality && quality.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
          <h3 className="text-sm font-medium text-gray-700 p-4 border-b border-gray-100">Last Ingestion Runs</h3>
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase">Source</th>
                <th className="px-4 py-2 text-right text-xs text-gray-500 uppercase">Fetched</th>
                <th className="px-4 py-2 text-right text-xs text-gray-500 uppercase">Matched</th>
                <th className="px-4 py-2 text-right text-xs text-gray-500 uppercase">Match Rate</th>
                <th className="px-4 py-2 text-right text-xs text-gray-500 uppercase">Last Run</th>
                <th className="px-4 py-2 text-center text-xs text-gray-500 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {quality.map(q => (
                <tr key={q.source_name}>
                  <td className="px-4 py-2 font-medium">{q.source_name}</td>
                  <td className="px-4 py-2 text-right">{q.records_fetched.toLocaleString()}</td>
                  <td className="px-4 py-2 text-right">{q.records_matched.toLocaleString()}</td>
                  <td className="px-4 py-2 text-right">
                    <span className={q.match_rate < 0.5 ? 'text-red-600' : q.match_rate < 0.8 ? 'text-amber-600' : 'text-green-600'}>
                      {(q.match_rate * 100).toFixed(1)}%
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right text-gray-500">{new Date(q.run_timestamp).toLocaleDateString()}</td>
                  <td className="px-4 py-2 text-center">
                    <button
                      onClick={() => triggerJob.mutate(q.source_name)}
                      className="text-xs text-blue-600 hover:text-blue-800"
                    >
                      Preview re-run
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Source Integrity */}
      {sourceAudit && (
        <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
          <div className="p-4 border-b border-gray-100">
            <h3 className="text-sm font-medium text-gray-700">Source Integrity Matrix</h3>
            <p className="text-xs text-gray-500 mt-1">
              Canonical view of configured sources vs runnable jobs vs active ingestion.
            </p>
            <div className="mt-3 grid grid-cols-2 sm:grid-cols-5 gap-2">
              <div className="px-2 py-1.5 bg-gray-50 rounded">
                <div className="text-[10px] text-gray-500 uppercase">Total</div>
                <div className="text-sm font-semibold text-gray-800">{sourceAudit.summary.total_sources}</div>
              </div>
              <div className="px-2 py-1.5 bg-green-50 rounded">
                <div className="text-[10px] text-green-600 uppercase">Operational</div>
                <div className="text-sm font-semibold text-green-700">{sourceAudit.summary.operational}</div>
              </div>
              <div className="px-2 py-1.5 bg-amber-50 rounded">
                <div className="text-[10px] text-amber-600 uppercase">No Recent Ingest</div>
                <div className="text-sm font-semibold text-amber-700">{sourceAudit.summary.no_recent_ingest}</div>
              </div>
              <div className="px-2 py-1.5 bg-rose-50 rounded">
                <div className="text-[10px] text-rose-600 uppercase">Not Wired</div>
                <div className="text-sm font-semibold text-rose-700">{sourceAudit.summary.not_wired}</div>
              </div>
              <div className="px-2 py-1.5 bg-red-50 rounded">
                <div className="text-[10px] text-red-600 uppercase">Schema Missing</div>
                <div className="text-sm font-semibold text-red-700">{sourceAudit.summary.schema_missing}</div>
              </div>
            </div>
            {sourceAudit.critical_gaps.length > 0 && (
              <div className="mt-3 text-xs text-rose-700 bg-rose-50 border border-rose-200 rounded p-2">
                Critical gaps: {sourceAudit.critical_gaps.map(g => g.source_name).join(', ')}
              </div>
            )}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase">Source</th>
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase">Dataset</th>
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase">Job</th>
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase">UI Surface</th>
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase">Status</th>
                  <th className="px-4 py-2 text-right text-xs text-gray-500 uppercase">Last Run</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {sourceAudit.sources.map(s => (
                  <tr key={s.source_name}>
                    <td className="px-4 py-2 font-medium">{s.source_name}</td>
                    <td className="px-4 py-2 text-xs text-gray-500">{s.dataset_id}</td>
                    <td className="px-4 py-2 text-xs">{s.job_type}</td>
                    <td className="px-4 py-2 text-xs text-gray-600">{s.ui_surface}</td>
                    <td className="px-4 py-2">
                      <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                        s.status === 'operational' ? 'bg-green-100 text-green-700' :
                        s.status === 'no_recent_ingest' ? 'bg-amber-100 text-amber-700' :
                        s.status === 'not_wired' ? 'bg-rose-100 text-rose-700' :
                        'bg-red-100 text-red-700'
                      }`}>{s.status}</span>
                    </td>
                    <td className="px-4 py-2 text-right text-xs text-gray-500">
                      {s.last_run ? new Date(s.last_run).toLocaleDateString() : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Recent Jobs */}
      {jobsSummary && (
        <div className="bg-white rounded-lg border border-gray-200 p-5">
          <h3 className="text-sm font-medium text-gray-700 mb-3">Queue Health (24h)</h3>
          <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
            <div className="p-3 bg-gray-50 rounded-lg">
              <div className="text-[10px] text-gray-500 uppercase">Queued</div>
              <div className="text-xl font-semibold text-gray-800">{jobsSummary.queued_count.toLocaleString()}</div>
            </div>
            <div className="p-3 bg-blue-50 rounded-lg">
              <div className="text-[10px] text-blue-600 uppercase">Running</div>
              <div className="text-xl font-semibold text-blue-700">{jobsSummary.running_count.toLocaleString()}</div>
            </div>
            <div className="p-3 bg-green-50 rounded-lg">
              <div className="text-[10px] text-green-600 uppercase">Succeeded</div>
              <div className="text-xl font-semibold text-green-700">{jobsSummary.succeeded_24h.toLocaleString()}</div>
            </div>
            <div className="p-3 bg-red-50 rounded-lg">
              <div className="text-[10px] text-red-600 uppercase">Failed</div>
              <div className="text-xl font-semibold text-red-700">{jobsSummary.failed_24h.toLocaleString()}</div>
            </div>
            <div className="p-3 bg-amber-50 rounded-lg">
              <div className="text-[10px] text-amber-600 uppercase">Avg Duration</div>
              <div className="text-xl font-semibold text-amber-700">{Math.round(jobsSummary.avg_duration_seconds_24h)}s</div>
            </div>
          </div>
        </div>
      )}

      {jobs && jobs.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
          <h3 className="text-sm font-medium text-gray-700 p-4 border-b border-gray-100">Recent Jobs</h3>
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase">ID</th>
                <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase">Type</th>
                <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase">Source</th>
                <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase">Status</th>
                <th className="px-4 py-2 text-right text-xs text-gray-500 uppercase">Succeeded</th>
                <th className="px-4 py-2 text-right text-xs text-gray-500 uppercase">Failed</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {jobs.map(j => (
                <tr key={j.id}>
                  <td className="px-4 py-2 text-gray-500">#{j.id}</td>
                  <td className="px-4 py-2">{j.job_type}</td>
                  <td className="px-4 py-2">{j.source}</td>
                  <td className="px-4 py-2">
                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                      (j.status === 'completed' || j.status === 'succeeded') ? 'bg-green-100 text-green-700' :
                      j.status === 'failed' ? 'bg-red-100 text-red-700' :
                      (j.status === 'running' || j.status === 'queued') ? 'bg-blue-100 text-blue-700' :
                      'bg-gray-100 text-gray-700'
                    }`}>{j.status}</span>
                  </td>
                  <td className="px-4 py-2 text-right">{j.succeeded ?? '--'}</td>
                  <td className="px-4 py-2 text-right">{j.failed ?? '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

class DataHealthErrorBoundary extends React.Component<{ children: React.ReactNode }, { hasError: boolean; errorMessage: string | null; errorStack: string | null }> {
  state = { hasError: false, errorMessage: null, errorStack: null };

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, errorMessage: error.message, errorStack: error.stack ?? null };
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="space-y-6">
          <h2 className="text-lg font-semibold text-gray-900">Data Health Dashboard</h2>
          <div className="rounded-lg border border-amber-200 bg-amber-50 p-5">
            <h3 className="text-sm font-semibold text-amber-900">Partial truth preview unavailable</h3>
            <p className="mt-1 text-sm text-amber-800">
              One truth-confidence preview returned an unexpected partial payload. Scoring settings remain available; refresh this tab after the data-health contract is updated.
            </p>
            {this.state.errorMessage && (
              <p className="mt-3 font-mono text-xs text-amber-900">{this.state.errorMessage}</p>
            )}
            {this.state.errorStack && (
              <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap font-mono text-[10px] text-amber-900">{this.state.errorStack}</pre>
            )}
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

const SettingsPage: React.FC = () => {
  const [tab, setTab] = useState<'scoring' | 'data-health'>('scoring');
  return (
    <div className="space-y-6">
      <div className="flex gap-4 border-b border-gray-200">
        {[
          { key: 'scoring' as const, label: 'Scoring Weights' },
          { key: 'data-health' as const, label: 'Data Health' },
        ].map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              tab === t.key ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'scoring' ? (
        <ScoringSection />
      ) : (
        <DataHealthErrorBoundary key="data-health">
          <DataHealthSection />
        </DataHealthErrorBoundary>
      )}
    </div>
  );
};

export default SettingsPage;
