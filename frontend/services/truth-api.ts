import { getAuthHeaders, clearToken } from './auth';
import { API_BASE_URL } from './config';

async function apiGet<T>(path: string): Promise<T> {
  const resp = await fetch(`${API_BASE_URL}${path}`, { headers: getAuthHeaders() });
  if (resp.status === 401) {
    clearToken();
    window.dispatchEvent(new Event('auth:logout'));
    throw new Error('Session expired');
  }
  if (!resp.ok) throw new Error(`GET ${path}: ${resp.statusText}`);
  return resp.json();
}

async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: { ...getAuthHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (resp.status === 401) {
    clearToken();
    window.dispatchEvent(new Event('auth:logout'));
    throw new Error('Session expired');
  }
  if (!resp.ok) throw new Error(`POST ${path}: ${resp.statusText}`);
  return resp.json();
}

export interface TruthClaim {
  claim_id: string;
  subject_type: string;
  subject_id: string;
  predicate: string;
  object_type?: string | null;
  object_id?: string | null;
  normalized_value?: string | null;
  claim_type: string;
  belief_status: string;
  confidence_score: number;
  freshness_days?: number | null;
  actionability_level: string;
  supporting_evidence_count: number;
  contradicting_evidence_count: number;
  supporting_sources: string[];
  contradicting_sources: string[];
  rationale?: Record<string, unknown>;
}

export interface LeadTruthSummary {
  lead_id: string;
  entity_name: string;
  canonical_entity: {
    canonical_entity_id: string;
    display_name?: string | null;
    normalized_name?: string | null;
    confidence_score?: number | null;
    relationship_type?: string | null;
    membership_confidence?: number | null;
  } | null;
  overall_confidence_score: number;
  review_bucket: string;
  belief_summary: {
    what_we_believe: string[];
    why_we_believe?: string[];
    supporting_sources?: string[];
    contradicting_sources?: string[];
    contradiction_count: number;
    freshness_days?: number | null;
    safe_actions: string[];
  };
  claims: TruthClaim[];
}

export type TruthSubjectType = 'lead' | 'canonical_entity' | 'entity' | 'building' | 'contact' | 'hpd_contact' | 'person';

export interface SubjectTruthSummary {
  subject_type: TruthSubjectType | string;
  subject_id: string;
  overall_confidence_score: number;
  review_bucket: string;
  belief_summary: {
    what_we_believe: string[];
    why_we_believe?: string[];
    supporting_sources?: string[];
    contradicting_sources?: string[];
    contradiction_count: number;
    freshness_days?: number | null;
    safe_actions: string[];
  };
  claims: TruthClaim[];
  schema_status?: TruthSchemaStatus;
}

export interface TruthActionabilityRule {
  level: string;
  meaning: string;
  minimum: string;
  confidence_policy_version?: string | null;
  minimum_score?: number | null;
  max_contradictions?: number | null;
  max_freshness_days?: number | null;
  min_supporting_sources?: number | null;
  min_supporting_evidence?: number | null;
}

export interface TruthDashboard {
  claim_count: number;
  verified_claim_count: number;
  conflicting_claim_count: number;
  recommended_outreach_claim_count: number;
  open_review_count: number;
  active_golden_case_count: number;
  confidence_snapshot_count: number;
  actionability_distribution: Record<string, number>;
  review_queue_distribution: Record<string, number>;
  claim_type_distribution: Record<string, number>;
  actionability_rules: TruthActionabilityRule[];
}

export interface GoldenBenchmarkCase {
  case_id: string;
  name: string;
  case_type: string;
  subject_type?: string | null;
  subject_id?: string | null;
  expected_outcome?: string | null;
  status: 'pass' | 'fail' | 'not_seeded' | 'not_configured';
  required_claim_count: number;
  matched_required_count: number;
  forbidden_claim_count: number;
  actual_claim_count: number;
  missing_required: Record<string, unknown>[];
  violated_forbidden: Record<string, unknown>[];
  tricky_features: string[];
}

export interface GoldenBenchmark {
  generated_at: string;
  seeded: boolean;
  total_cases: number;
  configured_cases: number;
  evaluable_cases: number;
  passed_cases: number;
  failed_cases: number;
  benchmark_coverage: number | null;
  feature_coverage?: {
    required_features: string[];
    observed_features: string[];
    missing_required_features: string[];
    coverage: number | null;
  };
  metrics: Record<string, number | null>;
  metric_counts: Record<string, unknown>;
  cases: GoldenBenchmarkCase[];
  notes: string[];
}

export interface TruthReviewItem {
  review_id: string;
  queue_name: string;
  subject_type: string;
  subject_id: string;
  status: string;
  priority: number;
  confidence_score?: number | null;
  actionability_level?: string | null;
  proposed_change: Record<string, unknown>;
  supporting_evidence: Record<string, unknown>;
  contradicting_evidence: Record<string, unknown>;
  rationale: Record<string, unknown>;
  run_id?: string | null;
  updated_at?: string | null;
}

export interface TruthReviewQueue {
  items: TruthReviewItem[];
  limit: number;
  offset: number;
  source: string;
}

export interface TruthValidationCheck {
  check: string;
  severity: 'critical' | 'high' | 'medium' | 'low' | string;
  count_sampled: number;
  why_it_matters: string;
  sample: Array<Record<string, unknown>>;
  recommended_queue: string;
}

export interface TruthValidationPreview {
  dry_run: boolean;
  run_type: string;
  generated_at: string;
  sample_limit: number;
  checks: TruthValidationCheck[];
  mutations_planned: number;
  rollback_strategy: string;
}

export interface TruthMaterializedClaimSpecPreview {
  claim_id: string;
  evidence_id: string;
  subject_type: string;
  subject_id: string;
  predicate: string;
  object_type: string;
  object_id: string;
  normalized_value?: string | null;
  claim_type: string;
  belief_status?: string | null;
  confidence_score?: number | null;
  freshness_days?: number | null;
  actionability_level?: string | null;
  source_name: string;
  source_type?: string | null;
  source_record_id: string;
  observed_at?: string | null;
  support_status?: string | null;
  source_quality_score?: number | null;
}

export interface TruthMaterializationPreview {
  dry_run: boolean;
  run_type: string;
  supported_sources: string[];
  selected_sources: string[];
  source_filter_applied: boolean;
  limit: number;
  planned_claims_by_source: Record<string, number>;
  planned_claims_total: number;
  candidate_claims_by_source?: Record<string, number>;
  strict_materializable_claims_by_source?: Record<string, number>;
  strict_materializable_claims_by_predicate?: Record<string, number>;
  existing_claim_count: number;
  existing_evidence_count: number;
  sample_strict_hpd_role_link_claim_specs?: TruthMaterializedClaimSpecPreview[];
  sample_materialized_claim_specs: TruthMaterializedClaimSpecPreview[];
  mutations_planned: number;
  rollback_strategy: string;
}

export interface TruthAdjudicationFactKey {
  subject_type: string;
  subject_id: string;
  predicate: string;
  object_type?: string | null;
  object_id?: string | null;
  normalized_value?: string | null;
}

export interface TruthAdjudicationSample {
  fact_key: TruthAdjudicationFactKey;
  claim_count?: number;
  claim_ids?: string[];
  evidence_ids?: string[];
  supporting_source_count?: number;
  supporting_evidence_count: number;
  contradicting_evidence_count: number;
  supporting_sources: string[];
  contradicting_sources: string[];
  current_max_confidence_score?: number | null;
  recomputed_confidence_score?: number | null;
  proposed_confidence?: number | null;
  verified_confidence_threshold?: number;
  score_gap_to_verified?: number;
  proposed_belief_status: string;
  proposed_actionability_level?: string;
  recommended_queue: string;
  safe_to_mark_verified: boolean;
  blockers: string[];
  confidence_rationale?: {
    confidence_policy_version?: string;
    claim_type?: string;
    average_supporting_source_quality?: number;
    raw_confidence_before_smoothing?: number;
    freshness_factor?: number;
    source_agreement_count?: number;
    source_disagreement_count?: number;
    supporting_source_count?: number;
    supporting_evidence_count?: number;
    verified_confidence_threshold?: number;
    [key: string]: unknown;
  };
}

export interface TruthAdjudicationPreview {
  dry_run: boolean;
  mutations_planned: number;
  generated_at?: string;
  limit: number;
  fact_group_count: number;
  verification_candidate_count: number;
  status_counts: Record<string, number>;
  recommended_queue_counts: Record<string, number>;
  blocker_counts: Record<string, number>;
  source_coverage?: {
    sampled_fact_group_count: number;
    zero_source_fact_group_count: number;
    single_source_fact_group_count: number;
    multi_source_fact_group_count: number;
    max_supporting_source_count: number;
    max_supporting_evidence_count: number;
    source_count_distribution: Record<string, number>;
    top_sources: Array<{
      source_name: string;
      fact_group_count: number;
    }>;
    verification_blocker?: string | null;
  };
  ledger_source_overlap?: {
    dry_run: boolean;
    mutations_planned: number;
    total_fact_group_count: number;
    zero_source_fact_group_count: number;
    single_source_fact_group_count: number;
    multi_source_fact_group_count: number;
    source_ready_fact_group_count: number;
    max_supporting_source_count: number;
    max_supporting_evidence_count: number;
    top_sources: Array<{
      source_name: string;
      fact_group_count: number;
    }>;
    business_readiness_blocker?: string | null;
  };
  role_source_overlap_pilot?: {
    dry_run: boolean;
    mutations_planned: number;
    lead_id: string;
    limit: number;
    scope_relationship_count?: number;
    sampled_relationship_count: number;
    multi_source_if_materialized_count: number;
    source_ready_if_materialized_count: number;
    management_source_ready_if_materialized_count: number;
    registered_agent_source_ready_if_materialized_count: number;
    claim_count_by_predicate_if_materialized?: Record<string, number>;
    identity_policy?: {
      strict_key_example?: string | null;
      broad_dedupe_key_example?: string | null;
      warning?: string | null;
    };
    business_readiness_note?: string | null;
    samples: Array<{
      fact_key: TruthAdjudicationFactKey;
      building_management_role?: string | null;
      lead_verification_keys: string[];
      lead_broad_dedupe_keys: string[];
      supporting_sources_if_materialized: string[];
      supporting_source_count_if_materialized: number;
      source_ready_if_materialized: boolean;
      safe_action: string;
      matched_role_contacts: Array<Record<string, unknown>>;
      adjacent_role_contacts: Array<Record<string, unknown>>;
      blocked_contact_count: number;
    }>;
  };
  scaled_role_source_overlap?: {
    dry_run: boolean;
    mutations_planned: number;
    relationship_limit: number;
    batch_limit: number;
    source_ready_batch_count: number;
    scanned_relationship_count: number;
    multi_source_if_materialized_count: number;
    source_ready_if_materialized_count: number;
    management_source_ready_if_materialized_count: number;
    registered_agent_source_ready_if_materialized_count: number;
    claim_count_by_predicate_if_materialized?: Record<string, number>;
    business_readiness_note?: string | null;
    batches: Array<{
      lead_id: string;
      lead_name?: string | null;
      scope_relationship_count: number;
      multi_source_if_materialized_count: number;
      source_ready_if_materialized_count: number;
      management_source_ready_if_materialized_count: number;
      registered_agent_source_ready_if_materialized_count: number;
      claim_count_by_predicate_if_materialized?: Record<string, number>;
      samples: Array<{
        fact_key: TruthAdjudicationFactKey;
        building_management_role?: string | null;
        supporting_sources_if_materialized: string[];
        source_ready_if_materialized: boolean;
        matched_role_contacts: Array<Record<string, unknown>>;
        adjacent_role_contacts: Array<Record<string, unknown>>;
        blocked_contact_count: number;
        safe_action: string;
      }>;
    }>;
  };
  role_overlap_post_materialization_simulation?: {
    dry_run: boolean;
    mutations_planned: number;
    selected_sources: string[];
    planned_claim_spec_count: number;
    simulated_fact_group_count: number;
    multi_source_fact_group_count: number;
    source_ready_fact_group_count: number;
    safe_to_mark_verified_count: number;
    fact_group_count_by_predicate: Record<string, number>;
    source_ready_count_by_predicate: Record<string, number>;
    safe_to_mark_verified_count_by_predicate: Record<string, number>;
    business_readiness_note?: string | null;
    samples: TruthAdjudicationSample[];
  };
  manager_source_bridge_preview?: {
    dry_run: boolean;
    mutations_planned: number;
    lead_id: string;
    relationship_count: number;
    role_counts: Record<string, Record<string, number>>;
    source_counts: Record<string, number>;
    registered_agent_bridge_count: number;
    current_manager_role_relationship_count: number;
    hpd_management_company_strict_match_count: number;
    hpd_site_manager_row_count: number;
    hpd_site_manager_strict_identity_match_count: number;
    manager_source_ready_if_materialized_count: number;
    blocking_reasons: string[];
    business_readiness_note?: string | null;
    safe_action?: string | null;
    samples: Array<{
      bbl: string;
      building_management_role?: string | null;
      contact_type?: string | null;
      display_name?: string | null;
      verification_key?: string | null;
      strict_identity_matches_lead: boolean;
      role_matches_building_management: boolean;
      hpd_predicate?: string | null;
      safe_action?: string | null;
    }>;
  };
  manager_external_source_acquisition_preview?: {
    dry_run: boolean;
    mutations_planned: number;
    lead_id: string;
    candidate_source_count: number;
    matched_evidence_candidate_count: number;
    clean_exact_claim_count: number;
    claim_group_count: number;
    source_ready_if_recorded_count: number;
    independent_source_ready_if_recorded_count: number;
    strict_manager_source_ready_if_recorded_count?: number;
    excluded_manager_proof_source_families?: string[];
    review_required_count: number;
    unmatched_candidate_count: number;
    new_relationship_candidate_count?: number;
    policy: Record<string, unknown>;
    manual_evidence_batch_preview?: {
      dry_run: boolean;
      allowed_execute: boolean;
      template_count: number;
      claim_group_count: number;
      planned_upsert_count: number;
      source_names: string[];
      recommended_strict_manager_proof_batch?: {
        dry_run: boolean;
        allowed_execute: boolean;
        template_count: number;
        claim_group_count: number;
        planned_upsert_count: number;
        rollback_preview?: {
          estimated_claim_count: number;
          estimated_evidence_count: number;
          estimated_confidence_snapshot_count: number;
          estimated_manifest_entry_count: number;
          note?: string | null;
        };
        source_names: string[];
        source_families?: string[];
        manager_proof_source_families?: string[];
        command?: string | null;
        safe_action?: string | null;
      };
      excluded_address_review_candidate_count: number;
      required_execute_params: Record<string, unknown>;
      command?: string | null;
      safe_action?: string | null;
    };
    post_recording_simulation?: {
      dry_run: boolean;
      mutations_planned: number;
      template_count: number;
      simulated_fact_group_count: number;
      multi_source_fact_group_count: number;
      source_ready_fact_group_count: number;
      independent_source_ready_fact_group_count: number;
      strict_manager_source_ready_fact_group_count?: number;
      excluded_manager_proof_source_families?: string[];
      safe_to_mark_verified_count: number;
      source_ready_count_by_predicate: Record<string, number>;
      safe_to_mark_verified_count_by_predicate: Record<string, number>;
      blocker_counts: Record<string, number>;
      safe_action?: string | null;
      samples?: Array<Record<string, unknown>>;
    };
    next_source_batches?: {
      dry_run: boolean;
      mutations_planned: number;
      candidate_count: number;
      suggested_source_family_counts: Record<string, number>;
      proposals: Array<{
        bbl?: string | null;
        address?: string | null;
        existing_manager_proof_source_families: string[];
        missing_manager_proof_source_family_count: number;
        suggested_source_families: string[];
        search_queries?: string[];
        source_targets?: Array<{
          source_family: string;
          evidence_needed: string;
        }>;
        source_boundary_notes?: string[];
        safe_action?: string | null;
      }>;
      source_boundary_notes?: string[];
      reviewed_source_findings?: Array<{
        source_family: string;
        source_urls?: string[];
        finding: string;
        qualification: string;
      }>;
      safe_action?: string | null;
    };
    claim_groups: Array<{
      fact_key: TruthAdjudicationFactKey;
      address?: string | null;
      building_management_role?: string | null;
      supporting_sources_if_recorded: string[];
      supporting_source_families_if_recorded: string[];
      manager_proof_source_families_if_recorded?: string[];
      supporting_source_count_if_recorded: number;
      independent_source_family_count_if_recorded: number;
      manager_proof_source_family_count_if_recorded?: number;
      source_ready_if_recorded: boolean;
      independent_source_ready_if_recorded: boolean;
      strict_manager_source_ready_if_recorded?: boolean;
      evidence_candidate_ids: string[];
      manual_evidence_templates: Array<Record<string, unknown>>;
      safe_action?: string | null;
    }>;
    evidence_candidates: Array<{
      candidate_id: string;
      candidate_status: string;
      source_name: string;
      source_type: string;
      source_family: string;
      source_record_id: string;
      source_url?: string | null;
      observed_at?: string | null;
      external_address?: string | null;
      local_match: Record<string, unknown>;
      evidence_role?: string | null;
      evidence_summary?: string | null;
      manager_name?: string | null;
      manager_contact_name?: string | null;
      clean_for_operator_review: boolean;
      independence_warning?: string | null;
      manual_evidence_template: Record<string, unknown>;
    }>;
    unmatched_candidates: Array<Record<string, unknown>>;
    new_relationship_candidates?: Array<{
      candidate_id?: string | null;
      candidate_status?: string | null;
      source_name?: string | null;
      source_type?: string | null;
      source_family?: string | null;
      source_record_id?: string | null;
      source_url?: string | null;
      external_address?: string | null;
      local_address?: string | null;
      manager_name?: string | null;
      manager_contact_name?: string | null;
      evidence_role?: string | null;
      evidence_summary?: string | null;
      local_building_match?: Record<string, unknown>;
      relationship_claim_preview?: Record<string, unknown>;
      reason?: string | null;
      safe_action?: string | null;
    }>;
  };
  operator_confirmed_management_preview?: {
    dry_run: boolean;
    mutations_planned: number;
    source_name: string;
    source_type: string;
    source_family: string;
    candidate_count: number;
    matched_candidate_count: number;
    unmatched_candidate_count: number;
    new_relationship_candidate_count: number;
    conflict_candidate_count: number;
    operator_confirmation_template_count?: number;
    second_source_template_count?: number;
    manual_evidence_template_count: number;
    contradiction_template_count: number;
    planned_upsert_count: number;
    source_ready_if_recorded_count: number;
    independent_source_ready_if_recorded_count?: number;
    strict_manager_source_ready_if_recorded_count?: number;
    verified_safe_if_recorded_count: number;
    policy?: Record<string, string>;
    post_recording_simulation?: {
      dry_run: boolean;
      mutations_planned: number;
      template_count: number;
      simulated_fact_group_count: number;
      multi_source_fact_group_count: number;
      source_ready_fact_group_count: number;
      independent_source_ready_fact_group_count?: number;
      strict_manager_source_ready_fact_group_count?: number;
      safe_to_mark_verified_count: number;
      blocker_counts: Record<string, number>;
      safe_action?: string | null;
    };
    second_source_seed_batches?: {
      dry_run: boolean;
      mutations_planned: number;
      candidate_count: number;
      template_count?: number;
      source_ready_if_recorded_count?: number;
      strict_manager_source_ready_if_recorded_count?: number;
      proposals: Array<{
        bbl?: string | null;
        address?: string | null;
        manager_lead_id?: string | null;
        manager_name?: string | null;
        existing_manager_proof_source_families: string[];
        existing_source_families_if_recorded?: string[];
        supporting_sources_if_recorded?: string[];
        source_ready_if_recorded?: boolean;
        strict_manager_source_ready_if_recorded?: boolean;
        verified_safe_if_recorded?: boolean;
        second_source_templates?: Array<Record<string, unknown>>;
        missing_manager_proof_source_family_count: number;
        strict_manager_gap_status?: string | null;
        strict_manager_gap_reason?: string | null;
        next_required_manager_proof?: string | null;
        suggested_source_families: string[];
        search_queries?: string[];
        source_targets?: Array<{
          source_family: string;
          evidence_needed: string;
        }>;
        safe_action?: string | null;
      }>;
      source_boundary_notes?: string[];
      reviewed_source_findings?: Array<{
        source_family: string;
        source_urls?: string[];
        finding: string;
        qualification: string;
      }>;
      safe_action?: string | null;
    };
    manual_evidence_templates: Array<Record<string, unknown>>;
    contradiction_templates: Array<Record<string, unknown>>;
    candidates: Array<{
      candidate_id: string;
      user_address: string;
      manager_name_supplied: string;
      matched_building?: Record<string, unknown> | null;
      matched_lead?: Record<string, unknown> | null;
      current_building_management?: Array<Record<string, unknown>>;
      current_truth_claims?: Array<Record<string, unknown>>;
      conflicting_current_manager_count: number;
      conflicting_truth_claim_count: number;
      review_queue: string;
      supporting_sources_if_recorded?: string[];
      supporting_source_families_if_recorded?: string[];
      manager_proof_source_families_if_recorded?: string[];
      source_ready_if_recorded: boolean;
      strict_manager_source_ready_if_recorded?: boolean;
      strict_manager_gap_status?: string | null;
      strict_manager_gap_reason?: string | null;
      missing_manager_proof_source_family_count?: number;
      next_required_manager_proof?: string | null;
      verified_safe_if_recorded: boolean;
      manual_evidence_template: Record<string, unknown>;
      second_source_templates?: Array<Record<string, unknown>>;
      contradiction_templates: Array<Record<string, unknown>>;
      safe_action?: string | null;
    }>;
    unmatched_candidates: Array<Record<string, unknown>>;
    safe_action?: string | null;
  };
  role_claim_correction_preview?: {
    dry_run: boolean;
    mutations_planned: number;
    lead_id: string;
    sampled_stale_claim_count: number;
    requires_operator_approval: boolean;
    business_readiness_note?: string | null;
    safe_action?: string | null;
    samples: Array<{
      claim_id: string;
      fact_key: TruthAdjudicationFactKey;
      belief_status?: string | null;
      confidence_score?: number | null;
      actionability_level?: string | null;
      evidence_ids: string[];
      source_names: string[];
      source_record_ids: string[];
      source_roles: string[];
      recommended_change: Record<string, unknown>;
    }>;
  };
  role_overlap_activation_plan?: {
    dry_run: boolean;
    mutations_planned: number;
    approval_required: boolean;
    current_ledger_verified_claims_added: number;
    predicted_if_approved: {
      source_ready_fact_groups_added: number;
      management_source_ready_fact_groups_added: number;
      registered_agent_source_ready_fact_groups_added: number;
      stale_role_claims_to_supersede: number;
    };
    materialization_sources: string[];
    ordered_steps: Array<{
      step: string;
      status: string;
      approval_required: boolean;
      mutations_planned: number;
      required_execute_params?: Record<string, unknown>;
      sources?: string[];
      commands?: string[];
      safe_action?: string;
      evidence?: Record<string, unknown>;
    }>;
    business_readiness_note?: string | null;
    safe_action?: string | null;
  };
  verification_gap_plan?: {
    dry_run: boolean;
    mutations_planned: number;
    proposal_count: number;
    policy: Record<string, unknown>;
    proposals: Array<{
      fact_key: TruthAdjudicationFactKey;
      current_sources: string[];
      current_supporting_evidence_count?: number;
      missing_source_count: number;
      missing_evidence_count: number;
      suggested_sources: string[];
      recommended_queue: string;
      safe_action: string;
      manual_evidence_template: Record<string, unknown>;
    }>;
  };
  verified_confidence_gap_plan?: {
    dry_run: boolean;
    mutations_planned: number;
    proposal_count: number;
    single_source_upgrade_would_verify_count?: number;
    best_single_source_upgrade_overall?: {
      suggested_source: string;
      simulated_supporting_source_name: string;
      source_quality_score?: number;
      simulated_confidence_score?: number;
      score_gap_to_verified?: number;
      would_reach_verified_threshold: boolean;
      safe_action?: string;
    } | null;
    bundle_upgrade_would_verify_count?: number;
    best_bundle_upgrade_overall?: {
      suggested_sources: string[];
      simulated_supporting_source_names: string[];
      simulated_confidence_score?: number;
      score_gap_to_verified?: number;
      would_reach_verified_threshold: boolean;
      safe_action?: string;
    } | null;
    policy: Record<string, unknown>;
    proposals: Array<{
      fact_key: TruthAdjudicationFactKey;
      current_sources: string[];
      supporting_source_count?: number;
      supporting_evidence_count?: number;
      recomputed_confidence_score?: number;
      verified_confidence_threshold?: number;
      score_gap_to_verified?: number;
      average_supporting_source_quality?: number | null;
      raw_confidence_before_smoothing?: number | null;
      source_quality_scores?: Array<Record<string, unknown>>;
      suggested_quality_upgrade_sources: string[];
      simulated_quality_upgrades?: Array<{
        suggested_source: string;
        simulated_supporting_source_name: string;
        simulated_source_already_present?: boolean;
        source_quality_score?: number;
        simulated_supporting_source_count?: number;
        simulated_supporting_evidence_count?: number;
        simulated_confidence_score?: number;
        simulated_belief_status?: string;
        simulated_actionability_level?: string;
        score_gap_to_verified?: number;
        would_reach_verified_threshold: boolean;
        safe_action?: string;
      }>;
      best_single_source_upgrade?: {
        suggested_source: string;
        simulated_supporting_source_name: string;
        source_quality_score?: number;
        simulated_confidence_score?: number;
        score_gap_to_verified?: number;
        would_reach_verified_threshold: boolean;
        safe_action?: string;
      } | null;
      simulated_quality_bundle_upgrade?: {
        suggested_sources: string[];
        simulated_supporting_source_names: string[];
        simulated_supporting_source_count?: number;
        simulated_supporting_evidence_count?: number;
        simulated_confidence_score?: number;
        simulated_belief_status?: string;
        simulated_actionability_level?: string;
        score_gap_to_verified?: number;
        would_reach_verified_threshold: boolean;
        acquisition_required?: boolean;
        recording_ready?: boolean;
        approval_required_before_recording?: boolean;
        required_real_evidence?: Array<{
          suggested_source: string;
          simulated_supporting_source_name: string;
          required_fields: string[];
        }>;
        safe_action?: string;
      };
      single_source_upgrade_would_verify?: boolean;
      recommended_queue: string;
      safe_action: string;
      manual_evidence_template: Record<string, unknown>;
    }>;
  };
  policy: Record<string, unknown>;
  samples: TruthAdjudicationSample[];
}

export interface TruthTrustGap {
  severity: 'critical' | 'high' | 'medium' | 'low' | string;
  area: string;
  message: string;
  evidence: Record<string, unknown>;
}

export interface TruthSchemaStatus {
  ready: boolean;
  expected_revision: string;
  current_revision?: string | null;
  migration_current?: boolean;
  expected_revision_applied?: boolean;
  truth_tables_ready?: boolean;
  revision_status?: string;
  alembic_table_exists?: boolean;
  required_tables?: Record<string, boolean>;
  missing_tables?: string[];
  mutations_planned: number;
}

export interface TruthHealthReport {
  generated_at: string;
  dry_run: boolean;
  mutations_planned: number;
  thresholds: Record<string, number>;
  summary: {
    claim_count: number;
    verified_claim_count: number;
    conflicting_claim_count: number;
    conflicting_claim_ratio: number;
    open_review_count: number;
    open_review_ratio: number;
    planned_claims_total: number;
    validation_check_count: number;
    configured_golden_cases: number;
    evaluable_golden_cases?: number;
    verification_candidate_count?: number;
    critical_or_high_gap_count: number;
    trust_posture: string;
  };
  trust_gaps: TruthTrustGap[];
  activation_checklist?: Array<{
    step: string;
    status: string;
    reason: string;
    approval_required: boolean;
    mutations_planned: number;
  }>;
  schema_status?: TruthSchemaStatus | null;
  source_audit?: {
    dry_run: boolean;
    mutations_planned: number;
    summary: {
      total_sources: number;
      operational: number;
      not_wired: number;
      schema_missing: number;
      no_recent_ingest: number;
      stale_ingest?: number;
    };
    critical_gaps: Array<Record<string, unknown>>;
    sources: Array<Record<string, unknown>>;
    refresh_plan?: TruthSourceRefreshPlan;
  } | null;
  source_refresh_plan?: TruthSourceRefreshPlan | null;
  adjudication_preview?: TruthAdjudicationPreview | null;
}

export interface TruthSourceRefreshPlan {
  dry_run: boolean;
  mutations_planned: number;
  approval_required: boolean;
  safe_to_run_automatically: boolean;
  summary: {
    planned_job_count: number;
    refreshable_job_count: number;
    blocked_job_count: number;
    affected_source_count: number;
    non_refreshable_gap_count?: number;
  };
  items: Array<Record<string, unknown>>;
  rollback_strategy: string;
}

export interface TruthActivationPacket {
  dry_run: boolean;
  mutations_planned: number;
  verdict: string;
  business_use_allowed: boolean;
  trust_posture: string;
  schema: {
    ready: boolean;
    current_revision?: string | null;
    expected_revision?: string | null;
    missing_tables: string[];
    ready_to_apply_additive_truth_migration: boolean;
  };
  approval_required: boolean;
  approval_steps: Array<{
    step: string;
    status: string;
    reason: string;
    mutations_planned: number;
  }>;
  source_refresh: {
    approval_required: boolean;
    planned_job_count: number;
    refreshable_job_count?: number;
    blocked_job_count?: number;
    affected_source_count: number;
    non_refreshable_gap_count: number;
    next_jobs?: Array<{
      job_type?: string | null;
      reason?: string | null;
      priority?: number | null;
      blocked: boolean;
      approval_required: boolean;
      preview_endpoint?: string | null;
      execute_endpoint?: string | null;
      source_count: number;
      sources?: Array<{
        source_name?: string | null;
        status?: string | null;
        source_age_days?: number | null;
      }>;
    }>;
  };
  golden_benchmark: {
    configured_cases: number;
    evaluable_cases: number;
  };
  claim_readiness?: {
    claim_count: number;
    verified_claim_count: number;
    critical_or_high_gap_count: number;
    has_materialized_claims: boolean;
    has_verified_claims: boolean;
    has_no_critical_or_high_gaps: boolean;
  };
  verification_frontier?: {
    dry_run: boolean;
    mutations_planned: number;
    verification_candidate_count: number;
    current_ledger: {
      total_fact_group_count?: number | null;
      single_source_fact_group_count?: number | null;
      multi_source_fact_group_count?: number | null;
      source_ready_fact_group_count?: number | null;
    };
    source_ready_below_verified_count: number;
    single_source_gap_count: number;
    single_source_upgrade_would_verify_count?: number | null;
    bundle_upgrade_would_verify_count?: number | null;
    manager_next_source_seed_count?: number | null;
    operator_second_source_seed_count?: number | null;
    evidence_acquisition_required: boolean;
    business_use_blocker?: string | null;
    next_preview_command?: string | null;
    safe_action?: string | null;
  };
  trust_gap_summary: Array<{
    severity: string;
    area: string;
    message: string;
  }>;
  next_safe_steps: Array<{
    step: string;
    command: string;
    mutates_data: boolean;
    requires_explicit_approval?: boolean;
    blocked_until?: string;
  }>;
  rollback: {
    strategy?: string | null;
    offline_rollback_command?: string | null;
  };
}

export interface TruthApprovalDecisionSummary {
  approval_required: boolean;
  batch_filter?: string | null;
  recommended_execute_command: string;
  would_record_template_count: number;
  would_record_claim_group_count: number;
  would_plan_upsert_count: number;
  included_addresses?: string[] | null;
  expected_multi_source_fact_group_count?: number | null;
  expected_source_ready_fact_group_count?: number | null;
  expected_strict_manager_source_ready_fact_group_count?: number | null;
  expected_safe_to_mark_verified_count?: number | null;
  single_source_claims_stay_unverified: boolean;
  will_mark_verified: boolean;
  will_create_or_refresh_source_data: boolean;
  will_materialize_new_relationships: boolean;
  post_execution_required_checks?: string[] | null;
  safe_action?: string | null;
}

export interface TruthRelationshipPreviewState {
  current_building_management_relationship_count?: number | null;
  current_truth_claim_count?: number | null;
  counts_as_current_ledger_overlap?: boolean | null;
  relationship_review_required?: boolean | null;
}

export interface TruthEvidenceMutationScope {
  allowed_tables: string[];
  forbidden_side_effects: {
    will_mark_verified: boolean;
    will_create_or_refresh_source_data: boolean;
    will_materialize_building_management_relationships: boolean;
    will_start_jobs: boolean;
    will_allow_business_use: boolean;
  };
  safe_action?: string | null;
}

export interface TruthManualEvidencePreviewSample {
  claim_id?: string | null;
  evidence_id?: string | null;
  predicate?: string | null;
  object_id?: string | null;
  source_name?: string | null;
  source_record_id?: string | null;
  actionability_level?: string | null;
  mutations_planned?: number | null;
  allowed_execute?: boolean | null;
  mutation_scope?: TruthEvidenceMutationScope | null;
}

export interface TruthSourceOverlapApprovalPacket {
  run_type: string;
  run_id: string;
  dry_run: boolean;
  mutations_planned: number;
  current_ledger: {
    total_fact_group_count: number;
    single_source_fact_group_count: number;
    multi_source_fact_group_count: number;
    source_ready_fact_group_count: number;
    verification_candidate_count: number;
  };
  previewed_overlap_if_approved: {
    manager_source_ready_if_recorded_count: number;
    manager_strict_source_ready_if_recorded_count: number;
    operator_source_ready_if_recorded_count: number;
    operator_strict_source_ready_if_recorded_count: number;
    safe_to_mark_verified_after_recording: number;
  };
  source_overlap_recording_gate?: {
    status: string;
    current_multi_source_fact_group_count: number;
    current_source_ready_fact_group_count: number;
    current_verification_candidate_count: number;
    source_overlap_proof_satisfied: boolean;
    additional_evidence_recording_requires_approval: boolean;
    safe_action?: string | null;
  };
  recommended_first_packet: {
    batch_filter?: string | null;
    current_recording_status?: string | null;
    recording_effect_if_rerun?: {
      would_create_new_claim_count?: number | null;
      would_create_new_evidence_count?: number | null;
      would_update_existing_claim_count?: number | null;
      would_update_existing_evidence_count?: number | null;
      would_create_confidence_snapshot_count?: number | null;
    } | null;
    template_count: number;
    claim_group_count: number;
    included_bbls?: string[] | null;
    included_addresses?: string[] | null;
    included_candidate_count?: number | null;
    excluded_non_strict_candidate_count?: number | null;
    excluded_non_strict_candidates?: Array<Record<string, unknown>> | null;
    excluded_conflict_candidate_count?: number | null;
    excluded_conflict_candidates?: Array<Record<string, unknown>> | null;
    source_names?: string[] | null;
    source_families?: string[] | null;
    manager_proof_source_families?: string[] | null;
    planned_upsert_count_if_approved?: number | null;
    recommended_execute_command?: string | null;
    approval_decision_summary?: TruthApprovalDecisionSummary | null;
    sample_manual_evidence_previews?: TruthManualEvidencePreviewSample[] | null;
    safe_action?: string | null;
  };
  manager_strict_gap_summary?: {
    claim_group_count: number;
    strict_ready_claim_group_count: number;
    broad_source_ready_not_strict_count: number;
    single_source_only_count: number;
    status_counts?: Record<string, number>;
    gap_candidates: Array<{
      bbl?: string | null;
      address?: string | null;
      strict_manager_gap_status?: string | null;
      existing_manager_proof_source_families?: string[] | null;
      supporting_source_families_if_recorded?: string[] | null;
      missing_manager_proof_source_family_count?: number | null;
      suggested_source_families?: string[] | null;
      first_search_query?: string | null;
      next_required_manager_proof?: string | null;
    }>;
    safe_action?: string | null;
  };
  manager_new_relationship_candidate_summary?: {
    candidate_count: number;
    counts_as_current_ledger_overlap: boolean;
    approval_required_for_relationship_creation: boolean;
    source_family_counts: Record<string, number>;
    candidates: Array<{
      candidate_id?: string | null;
      source_name?: string | null;
      source_family?: string | null;
      external_address?: string | null;
      local_address?: string | null;
      bbl?: string | null;
      manager_name?: string | null;
      evidence_role?: string | null;
      source_url?: string | null;
      current_relationship_state?: TruthRelationshipPreviewState | null;
      safe_action?: string | null;
    }>;
    safe_action?: string | null;
  };
  operator_strict_packet?: {
    current_recording_status?: string | null;
    template_count: number;
    claim_group_count: number;
    included_candidate_count?: number | null;
    excluded_non_strict_candidate_count?: number | null;
    excluded_non_strict_candidates?: Array<{
      candidate_id?: string | null;
      bbl?: string | null;
      address?: string | null;
      manager_name?: string | null;
      reason?: string | null;
      strict_manager_gap_status?: string | null;
      missing_manager_proof_source_family_count?: number | null;
      strict_manager_gap_reason?: string | null;
      next_required_manager_proof?: string | null;
    }> | null;
    excluded_conflict_candidate_count?: number | null;
    excluded_conflict_candidates?: Array<Record<string, unknown>> | null;
    source_names?: string[] | null;
    source_families?: string[] | null;
    manager_proof_source_families?: string[] | null;
    planned_upsert_count_if_approved?: number | null;
    recording_effect_if_rerun?: {
      would_create_new_claim_count?: number | null;
      would_create_new_evidence_count?: number | null;
      would_update_existing_claim_count?: number | null;
      would_update_existing_evidence_count?: number | null;
      would_create_confidence_snapshot_count?: number | null;
    } | null;
    recommended_execute_command?: string | null;
    approval_decision_summary?: TruthApprovalDecisionSummary | null;
    sample_manual_evidence_previews?: TruthManualEvidencePreviewSample[] | null;
    safe_action?: string | null;
  };
  operator_strict_gap_summary?: {
    candidate_count: number;
    strict_ready_candidate_count: number;
    broad_source_ready_not_strict_count: number;
    single_source_only_count: number;
    status_counts?: Record<string, number>;
    gap_candidates: Array<{
      candidate_id?: string | null;
      address?: string | null;
      manager_name?: string | null;
      strict_manager_gap_status?: string | null;
      missing_manager_proof_source_family_count?: number | null;
      strict_manager_gap_reason?: string | null;
      next_required_manager_proof?: string | null;
    }>;
    safe_action?: string | null;
  };
  approval_required: boolean;
  approval_policy: Record<string, unknown>;
  post_execution_required_checks: string[];
  blocked_business_use_reason: string;
  safe_action: string;
}

export interface TruthSourceOverlapPostRecordingCheck {
  run_type?: string;
  dry_run: boolean;
  mutations_planned: number;
  post_recording_success: boolean;
  thresholds: {
    min_multi_source_fact_groups: number;
    min_source_ready_fact_groups: number;
    max_verified_single_source_claims: number;
  };
  current_ledger: {
    total_fact_group_count?: number | null;
    single_source_fact_group_count?: number | null;
    multi_source_fact_group_count: number;
    source_ready_fact_group_count: number;
    max_supporting_source_count?: number | null;
    max_supporting_evidence_count?: number | null;
  };
  verified_single_source_policy: {
    verified_claim_count: number;
    verified_single_source_claim_count: number;
    sample_limit: number;
    samples: Array<Record<string, unknown>>;
  };
  checks: Array<{
    check: string;
    status: string;
    observed: number;
    minimum?: number;
    maximum?: number;
    reason: string;
  }>;
  safe_action: string;
  schema_status?: TruthSchemaStatus;
}

export interface TruthManagerSourceAcquisitionPacket {
  run_type: string;
  run_id: string;
  lead_id: string;
  dry_run: boolean;
  mutations_planned: number;
  candidate_count: number;
  source_ready_if_recorded_count: number;
  independent_source_ready_if_recorded_count: number;
  strict_manager_source_ready_if_recorded_count: number;
  verified_safe_if_recorded_count: number;
  next_source_seed_count: number;
  new_relationship_candidate_count?: number;
  new_relationship_candidates?: Array<{
    candidate_id?: string | null;
    source_name?: string | null;
    source_family?: string | null;
    external_address?: string | null;
    local_address?: string | null;
    manager_name?: string | null;
    evidence_role?: string | null;
    evidence_summary?: string | null;
    source_url?: string | null;
    local_building_match?: Record<string, unknown>;
    current_relationship_state?: TruthRelationshipPreviewState | null;
    safe_action?: string | null;
  }>;
  new_relationship_policy?: string;
  suggested_source_family_counts: Record<string, number>;
  priority_source_families: string[];
  proposals: Array<{
    bbl?: string | null;
    address?: string | null;
    existing_manager_proof_source_families?: string[];
    missing_manager_proof_source_family_count?: number;
    suggested_source_families?: string[];
    first_search_query?: string | null;
    search_queries?: string[];
    source_targets?: Array<Record<string, unknown>>;
    safe_action?: string | null;
  }>;
  reviewed_source_findings: Array<Record<string, unknown>>;
  source_boundary_notes: string[];
  current_preview_summary: Record<string, unknown>;
  safe_action: string;
}

export interface TruthReviewedSourceFinding {
  source_family?: string | null;
  source_urls?: string[];
  finding?: string | null;
  qualification?: string | null;
}

export interface TruthEvidenceRequest {
  request_type: string;
  candidate_id?: string | null;
  relationship_label?: string | null;
  relationship?: {
    manager_name?: string | null;
    manager_lead_id?: string | null;
    address?: string | null;
    bbl?: string | null;
    relationship_label?: string | null;
  };
  fact_key?: Partial<TruthAdjudicationFactKey>;
  display?: {
    subject_label?: string | null;
    predicate_label?: string | null;
    object_label?: string | null;
    relationship_label?: string | null;
    building?: {
      bbl?: string | null;
      address?: string | null;
      borough?: string | null;
      unit_count?: number | null;
    };
  };
  current_sources?: string[];
  current_supporting_source_count?: number | null;
  current_supporting_evidence_count?: number | null;
  current_confidence_score?: number | null;
  verified_confidence_threshold?: number | null;
  score_gap_to_verified?: number | null;
  missing_source_count?: number | null;
  missing_evidence_count?: number | null;
  can_become?: string | null;
  evidence_need?: string | null;
  threshold_paths?: Array<Record<string, unknown>>;
  required_sources?: string[];
  suggested_sources?: string[];
  required_real_evidence?: Array<{
    suggested_source?: string | null;
    suggested_source_family?: string | null;
    simulated_supporting_source_name?: string | null;
    required_fields?: string[];
    acquisition_mode?: string | null;
    source_name?: string | null;
    source_dataset_ids?: string[];
    official_query_urls?: Record<string, string>;
    download_urls?: string[];
    read_only_preview_command?: string | null;
    post_fetch_local_extract_command?: string | null;
    acquisition_note?: string | null;
  }>;
  required_real_evidence_count?: number | null;
  manual_evidence_template?: Record<string, unknown> | null;
  current_relationship_state?: TruthSourceAcquisitionFrontierProposal['current_relationship_state'];
  existing_manager_proof_source_families?: string[];
  supporting_source_families_if_recorded?: string[];
  strict_manager_source_ready_if_recorded?: boolean;
  strict_manager_gap_status?: string | null;
  strict_manager_gap_reason?: string | null;
  missing_manager_proof_source_family_count?: number | null;
  suggested_source_families?: string[];
  search_queries?: string[];
  source_targets?: Array<Record<string, unknown>>;
  recording_ready?: boolean;
  approval_required_before_recording?: boolean;
  reviewed_source_findings?: TruthReviewedSourceFinding[];
  reviewed_source_history_status?: string | null;
  safe_action?: string | null;
}

export interface TruthEvidenceRequestPacket {
  dry_run: boolean;
  mutations_planned: number;
  shown_limit_per_section?: number | null;
  request_count: number;
  displayed_request_count: number;
  source_ready_request_count: number;
  single_source_request_count: number;
  manager_source_request_count: number;
  operator_source_request_count: number;
  source_acquisition_request_count: number;
  recording_ready_count: number;
  approval_required_count: number;
  reviewed_source_finding_count?: number | null;
  reviewed_source_findings?: TruthReviewedSourceFinding[];
  reviewed_source_history_status?: string | null;
  source_ready_requests: TruthEvidenceRequest[];
  single_source_requests: TruthEvidenceRequest[];
  source_acquisition_requests: TruthEvidenceRequest[];
  requests: TruthEvidenceRequest[];
  policy?: Record<string, string>;
  safe_action?: string | null;
}

export interface TruthSourceAcquisitionWorkItem {
  work_item_id: string;
  priority: number;
  request_type?: string | null;
  relationship: {
    relationship_label?: string | null;
    bbl?: string | null;
    address?: string | null;
    manager_name?: string | null;
    manager_lead_id?: string | null;
  };
  current_sources?: string[];
  current_confidence_score?: number | null;
  score_gap_to_verified?: number | null;
  strict_manager_gap_status?: string | null;
  can_become?: string | null;
  evidence_need?: string | null;
  source_family_needs: string[];
  search_queries: string[];
  source_targets: Array<Record<string, unknown>>;
  official_hpd_query?: Record<string, string> | null;
  official_hpd_download_urls?: string[];
  read_only_hpd_preview_command?: string | null;
  post_fetch_local_extract_command?: string | null;
  acceptance_criteria: string[];
  paste_back_template: Record<string, unknown>;
  paste_back_templates?: Array<Record<string, unknown>>;
  operator_confirmation_request?: {
    status?: string | null;
    source_family?: string | null;
    question_prompt?: string | null;
    non_duplicate_boundary?: string | null;
    required_fields?: string[];
    paste_back_template?: Record<string, unknown>;
    contradiction_paste_back_template?: Record<string, unknown>;
    contradiction_handling?: string | null;
    preview_command?: string | null;
    safe_action?: string | null;
  } | null;
  paste_back_fields: string[];
  reviewed_source_history_status?: string | null;
  reviewed_source_findings?: TruthReviewedSourceFinding[];
  safe_action?: string | null;
}

export interface TruthSourceAcquisitionWorklist {
  run_type: string;
  dry_run: boolean;
  mutations_planned: number;
  source: string;
  frontier_current_ledger?: TruthVerificationFrontier['current_ledger'];
  verification_candidate_count?: number | null;
  request_count: number;
  work_item_count: number;
  hpd_work_item_count: number;
  recording_ready_count: number;
  approval_required_count: number;
  csv_template?: string | null;
  csv_template_command?: string | null;
  hpd_fetch_packet?: string | null;
  hpd_fetch_packet_command?: string | null;
  operator_confirmation_packet?: string | null;
  operator_confirmation_packet_command?: string | null;
  candidate_csv_preview_command?: string | null;
  work_items: TruthSourceAcquisitionWorkItem[];
  policy: {
    single_source_policy?: string;
    role_policy?: string;
    execution_policy?: string;
  };
  next_step_after_source_found?: string | null;
  safe_action?: string | null;
  schema_status?: TruthSchemaStatus;
}

export interface TruthSourceOverlapBlockerReport {
  run_type: string;
  dry_run: boolean;
  mutations_planned: number;
  status: string;
  current_ledger?: TruthVerificationFrontier['current_ledger'];
  verification_candidate_count?: number | null;
  source_ready_fact_group_count?: number | null;
  source_ready_below_verified_count?: number | null;
  single_source_upgrade_would_verify_count?: number | null;
  bundle_upgrade_would_verify_count?: number | null;
  threshold_sensitive_relationships?: Array<{
    relationship_label?: string | null;
    bbl?: string | null;
    address?: string | null;
    manager_name?: string | null;
    current_sources?: string[];
    current_confidence_score?: number | null;
    verified_confidence_threshold?: number | null;
    score_gap_to_verified?: number | null;
    best_single_source?: string | null;
    best_single_source_simulated_confidence?: number | null;
    required_bundle_sources?: string[];
    required_real_evidence_count?: number | null;
    recording_ready?: boolean;
    approval_required_before_recording?: boolean;
    reviewed_source_history_status?: string | null;
    reviewed_source_findings?: Array<{
      source_family?: string | null;
      finding?: string | null;
      qualification?: string | null;
    }>;
    safe_action?: string | null;
  }>;
  evidence_request_summary: {
    request_count?: number | null;
    work_item_count?: number | null;
    hpd_work_item_count?: number | null;
    recording_ready_count?: number | null;
    approval_required_count?: number | null;
    reviewed_source_finding_count?: number | null;
    reviewed_source_history_status?: string | null;
    verification_readiness_gate?: Record<string, unknown>;
  };
  source_bridge_assessment: {
    can_record_evidence_now?: boolean;
    can_request_recording_approval?: boolean;
    has_preview_ready_candidate_batch?: boolean;
    candidate_preview_status?: string | null;
    candidate_recording_ready_count?: number | null;
    candidate_recommended_count?: number | null;
    candidate_allowed_execute?: boolean;
    has_source_acquisition_clues?: boolean;
    source_acquisition_clue_count?: number | null;
    can_mark_verified_now?: boolean;
    blocking_reasons?: string[];
    approval_boundary?: string | null;
    why_current_overlap_is_not_enough?: string | null;
  };
  source_evidence_candidate_summary?: {
    status?: string | null;
    checked?: boolean;
    source_mode?: string | null;
    candidate_count?: number | null;
    source_acquisition_clue_count?: number | null;
    source_acquisition_clues?: Array<Record<string, unknown>>;
    original_candidate_count?: number | null;
    filtered_out_candidate_count?: number | null;
    ready_for_manual_evidence_preview_count?: number | null;
    recording_ready_count?: number | null;
    new_supporting_source_ready_count?: number | null;
    supporting_source_already_present_count?: number | null;
    contradiction_candidate_count?: number | null;
    blocked_count?: number | null;
    recommended_count?: number | null;
    recommended_relationships?: Array<Record<string, unknown>>;
    duplicate_or_freshness_only_count?: number | null;
    duplicate_or_freshness_only_relationships?: Array<Record<string, unknown>>;
    contradiction_review_count?: number | null;
    contradiction_relationships?: Array<Record<string, unknown>>;
    allowed_execute?: boolean;
    approval_required_before_recording?: boolean;
    can_record_evidence_now?: boolean;
    required_execute_flags_for_batch?: string[];
    recording_approval_packet?: TruthSourceEvidenceRecordingApprovalPacket | null;
    post_recording_expectations?: Record<string, unknown>;
    safe_action?: string | null;
  };
  top_blocked_relationships: Array<{
    work_item_id?: string | null;
    priority?: number | null;
    request_type?: string | null;
    relationship_label?: string | null;
    bbl?: string | null;
    address?: string | null;
    manager_name?: string | null;
    current_sources?: string[];
    source_family_needs?: string[];
    strict_manager_gap_status?: string | null;
    reviewed_source_history_status?: string | null;
    has_official_hpd_query_packet?: boolean;
    post_fetch_local_extract_command?: string | null;
    safe_action?: string | null;
  }>;
  reviewed_source_summary?: {
    reviewed_source_family_counts?: Record<string, number>;
    sample_reviewed_source_findings?: Array<Record<string, unknown>>;
  };
  policy?: {
    single_source_policy?: string;
    role_policy?: string;
    recording_policy?: string;
    business_use_policy?: string;
  };
  next_required_action?: string | null;
  safe_action?: string | null;
  schema_status?: TruthSchemaStatus;
}

export interface TruthVerificationFrontier {
  run_type: string;
  dry_run: boolean;
  mutations_planned: number;
  limit: number;
  current_ledger: {
    total_fact_group_count?: number | null;
    single_source_fact_group_count?: number | null;
    multi_source_fact_group_count?: number | null;
    source_ready_fact_group_count?: number | null;
  };
  verification_candidate_count?: number | null;
  source_ready_below_verified: {
    proposal_count?: number | null;
    single_source_upgrade_would_verify_count?: number | null;
    bundle_upgrade_would_verify_count?: number | null;
    proposals: Array<{
      fact_key: Partial<TruthAdjudicationFactKey>;
      display?: {
        subject_label?: string | null;
        predicate_label?: string | null;
        object_label?: string | null;
        relationship_label?: string | null;
        building?: {
          bbl?: string | null;
          address?: string | null;
          borough?: string | null;
          unit_count?: number | null;
        };
      };
      current_sources: string[];
      supporting_source_count?: number | null;
      supporting_evidence_count?: number | null;
      recomputed_confidence_score?: number | null;
      verified_confidence_threshold?: number | null;
      score_gap_to_verified?: number | null;
      best_single_source_upgrade?: {
        suggested_source?: string | null;
        simulated_confidence_score?: number | null;
        would_reach_verified_threshold?: boolean | null;
      };
      bundle_upgrade_would_verify?: boolean;
      bundle_simulated_confidence_score?: number | null;
      required_bundle_sources?: string[];
      required_real_evidence?: Array<{
        suggested_source?: string | null;
        simulated_supporting_source_name?: string | null;
        required_fields?: string[];
        acquisition_mode?: string | null;
        source_name?: string | null;
        source_dataset_ids?: string[];
        official_query_urls?: Record<string, string>;
        download_urls?: string[];
        read_only_preview_command?: string | null;
        post_fetch_local_extract_command?: string | null;
        acquisition_note?: string | null;
      }>;
      required_real_evidence_count?: number | null;
      evidence_acquisition_status?: string | null;
      recording_ready?: boolean;
      approval_required_before_recording?: boolean;
      manual_evidence_template?: Record<string, unknown> | null;
      safe_action?: string | null;
    }>;
  };
  single_source_gaps: {
    proposal_count?: number | null;
    proposals: Array<{
      fact_key: Partial<TruthAdjudicationFactKey>;
      display?: {
        subject_label?: string | null;
        predicate_label?: string | null;
        object_label?: string | null;
        relationship_label?: string | null;
        building?: {
          bbl?: string | null;
          address?: string | null;
          borough?: string | null;
          unit_count?: number | null;
        };
      };
      current_sources: string[];
      supporting_source_count?: number | null;
      supporting_evidence_count?: number | null;
      recomputed_confidence_score?: number | null;
      suggested_sources?: string[];
      safe_action?: string | null;
      manual_evidence_template?: Record<string, unknown> | null;
    }>;
  };
  source_acquisition_frontier: {
    manager_next_source_seed_count?: number | null;
    operator_second_source_seed_count?: number | null;
    manager_proposals: Array<TruthSourceAcquisitionFrontierProposal>;
    operator_proposals: Array<TruthSourceAcquisitionFrontierProposal>;
  };
  verification_readiness_gate?: {
    status: string;
    verification_candidate_count: number;
    source_ready_below_verified_count: number;
    record_ready_count: number;
    acquisition_required_count: number;
    approval_required_count: number;
    required_real_evidence_count: number;
    one_source_threshold_clear_count: number;
    bundle_threshold_clear_count: number;
    single_source_gap_count: number;
    reason?: string | null;
    safe_action?: string | null;
  };
  evidence_request_packet?: TruthEvidenceRequestPacket;
  safe_action: string;
  next_required_action: string;
}

export interface TruthSourceAcquisitionFrontierProposal {
  candidate_id?: string | null;
  bbl?: string | null;
  address?: string | null;
  manager_lead_id?: string | null;
  manager_name?: string | null;
  existing_manager_proof_source_families?: string[];
  supporting_source_families_if_recorded?: string[];
  strict_manager_source_ready_if_recorded?: boolean;
  strict_manager_gap_status?: string | null;
  strict_manager_gap_reason?: string | null;
  missing_manager_proof_source_family_count?: number | null;
  suggested_source_families?: string[];
  first_search_query?: string | null;
  search_queries?: string[];
  source_targets?: Array<Record<string, unknown>>;
  current_relationship_state?: {
    current_building_management_relationship_count?: number | null;
    current_matching_building_management_relationship_count?: number | null;
    current_truth_claim_count?: number | null;
    current_matching_truth_claim_count?: number | null;
    conflicting_current_manager_count?: number | null;
    conflicting_truth_claim_count?: number | null;
    current_source_names?: string[];
    current_supporting_source_count?: number | null;
    current_supporting_evidence_count?: number | null;
    current_ledger_source_ready?: boolean | null;
    has_operator_confirmed_evidence_recorded?: boolean | null;
    safe_action?: string | null;
  };
  next_required_manager_proof?: string | null;
  safe_action?: string | null;
}

export interface TruthCompletionAudit {
  dry_run: boolean;
  mutations_planned: number;
  objective: string;
  completion_status: string;
  success_criteria: string[];
  prompt_to_artifact_checklist: Array<{
    requirement: string;
    status: 'satisfied' | 'blocked' | 'missing' | 'requires_review' | 'runtime_not_checked' | string;
    evidence: Record<string, unknown>;
  }>;
  artifact_summary: {
    total: number;
    satisfied: number;
    missing: number;
  };
  runtime_blockers: Array<{
    gate: string;
    reason: string;
    evidence?: Record<string, unknown>;
  }>;
  production_probe_included?: boolean;
  production_probe_note?: string;
}

export interface TruthReviewDecisionRequest {
  decision: 'approve' | 'reject' | 'needs_more_evidence' | 'do_not_merge';
  note?: string;
  dry_run?: boolean;
  confirm_execute?: boolean;
}

export interface TruthReviewDecisionResponse {
  dry_run: boolean;
  review_id: string;
  decision: string;
  current_status: string;
  target_status: string;
  queue_name: string;
  subject_type: string;
  subject_id: string;
  allowed_execute: boolean;
  blocked_reason?: string | null;
  previous_state?: Record<string, unknown>;
  resulting_state?: Record<string, unknown>;
  decision_payload?: Record<string, unknown>;
  proposed_database_changes: Array<Record<string, unknown>>;
  rollback_strategy: string;
  schema_status?: TruthSchemaStatus;
}

export interface TruthManualEvidenceRequest {
  subject_type: string;
  subject_id: string;
  predicate: string;
  object_type: string;
  object_id: string;
  claim_type: string;
  normalized_value?: string | null;
  extracted_value?: string | null;
  support_status?: 'supports' | 'contradicts';
  source_name?: 'manual_evidence' | 'operator_review' | 'company_website' | 'google_places' | 'ny_dos' | 'outreach_confirmed' | string;
  source_type?: string;
  source_record_id?: string | null;
  source_url?: string | null;
  observed_at?: string | null;
  note?: string | null;
  raw_payload?: Record<string, unknown> | null;
  dry_run?: boolean;
  confirm_execute?: boolean;
  run_id?: string | null;
}

export interface TruthManualEvidenceResponse {
  run_type: string;
  run_id?: string;
  dry_run: boolean;
  mutations_planned: number;
  allowed_execute: boolean;
  blocked_reason?: string | null;
  claim_spec?: {
    claim_id: string;
    evidence_id: string;
    subject_type: string;
    subject_id: string;
    predicate: string;
    object_type: string;
    object_id: string;
    normalized_value?: string | null;
    claim_type: string;
    belief_status?: string | null;
    confidence_score?: number | null;
    freshness_days?: number | null;
    actionability_level?: string | null;
    source_name: string;
    source_type?: string | null;
    support_status: string;
    source_url?: string | null;
  };
  proposed_database_changes?: Array<Record<string, unknown>>;
  rollback_plan?: Record<string, unknown>;
  rollback_manifest?: Record<string, unknown>;
  rollback_strategy?: string;
  required_execute_params?: Record<string, unknown>;
  schema_status?: TruthSchemaStatus;
}

export interface TruthSourceEvidenceIntakeRequest {
  relationship_label?: string | null;
  bbl?: string | null;
  address?: string | null;
  manager_name?: string | null;
  manager_lead_id?: string | null;
  source_family?: string | null;
  source_name?: string | null;
  source_url_or_local_record_reference?: string | null;
  source_record_id?: string | null;
  observed_at?: string | null;
  exact_property_match?: boolean | string | null;
  role_specific_management_support?: boolean | string | null;
  source_excerpt_or_row_summary?: string | null;
  contradicts_current_claim?: boolean | string | null;
  notes?: string | null;
}

export interface TruthSourceEvidenceIntakeOverlapEffect {
  source_name?: string | null;
  current_sources: string[];
  current_supporting_source_count?: number | null;
  projected_supporting_source_count?: number | null;
  projected_new_supporting_source_count_delta?: number | null;
  source_already_present: boolean;
  adds_new_supporting_source: boolean;
  effect_status: string;
  expected_overlap_effect_status?: string | null;
  would_be_first_source_only_after_recording?: boolean;
  would_be_multi_source_after_recording?: boolean;
  would_be_source_ready_after_recording?: boolean;
  safe_action?: string | null;
}

export interface TruthExpectedPostRecordingSourceOverlap {
  recommended_row_count?: number | null;
  new_supporting_source_count_delta?: number | null;
  first_source_only_after_recording_count?: number | null;
  multi_source_after_recording_count?: number | null;
  source_ready_after_recording_count?: number | null;
  rows?: Array<Record<string, unknown>>;
  safe_action?: string | null;
}

export interface TruthManualEvidenceReplayBoundary {
  explicit_approval_required: boolean;
  payload_file_replay_command?: string | null;
  payload_file_preview_command?: string | null;
  payload_file_execute_command_after_approval?: string | null;
  required_execute_flags_for_batch?: string[];
  will_mark_verified?: boolean;
  will_refresh_sources?: boolean;
  will_materialize_relationships?: boolean;
  will_start_jobs?: boolean;
  will_allow_business_use?: boolean;
  post_recording_expectations?: TruthPostRecordingExpectations;
}

export interface TruthPostRecordingExpectations {
  must_run?: string[];
  must_hold?: {
    no_single_source_claim_marked_verified?: boolean;
    no_automatic_verified_status_change?: boolean;
    no_source_refresh?: boolean;
    no_relationship_materialization?: boolean;
    no_business_use_activation?: boolean;
  };
  acceptable_after_operator_seed_recording?: {
    verification_candidate_count_may_remain_zero?: boolean;
    runtime_completion_audit_may_remain_not_complete?: boolean;
    truth_health_may_remain_not_ready?: boolean;
    reason?: string | null;
  };
}

export interface TruthSourceEvidenceRecordingApprovalPacket {
  status?: string | null;
  approval_required?: boolean;
  allowed_execute?: boolean;
  approval_scope?: string | null;
  filtered_view?: boolean;
  recommended_count?: number | null;
  recommended_relationships?: Array<Record<string, unknown>>;
  manual_evidence_payload_count?: number | null;
  manual_evidence_payloads?: Array<TruthManualEvidenceRequest | Record<string, unknown>>;
  manual_evidence_payload_review?: Array<Record<string, unknown>>;
  expected_post_recording_source_overlap?: TruthExpectedPostRecordingSourceOverlap;
  excluded_count?: number | null;
  excluded_relationships?: {
    duplicate_or_freshness_only?: Array<Record<string, unknown>>;
    contradiction_review?: Array<Record<string, unknown>>;
  };
  approval_question?: string | null;
  preview_command?: string | null;
  execute_command_after_approval?: string | null;
  required_execute_flags_for_batch?: string[];
  mutation_scope?: {
    allowed_tables?: string[];
    forbidden_side_effects?: Record<string, boolean>;
  };
  post_recording_expectations?: TruthPostRecordingExpectations;
  safe_action?: string | null;
}

export interface TruthSourceEvidenceIntakePreview {
  run_type: string;
  dry_run: boolean;
  mutations_planned: number;
  validation_status: string;
  recording_ready: boolean;
  approval_required_before_recording?: boolean;
  relationship_match?: {
    status?: string | null;
    work_item_id?: string | null;
    request_type?: string | null;
    relationship?: Record<string, unknown>;
  };
  required_paste_back_fields?: string[];
  required_before_manual_preview?: string[];
  blocking_reasons?: string[];
  support_status?: string | null;
  source_overlap_effect?: TruthSourceEvidenceIntakeOverlapEffect;
  manual_evidence_payload?: TruthManualEvidenceRequest | Record<string, unknown>;
  manual_evidence_preview?: TruthManualEvidenceResponse;
  worklist_context?: {
    request_count?: number | null;
    work_item_count?: number | null;
    recording_ready_count?: number | null;
    approval_required_count?: number | null;
  };
  next_required_action?: string | null;
  safe_action?: string | null;
  schema_status?: TruthSchemaStatus;
  blocked_reason?: string | null;
}

export interface TruthSourceEvidenceIntakeBatchRequest {
  candidates?: TruthSourceEvidenceIntakeRequest[];
  hpd_audit_output?: Record<string, unknown> | Array<Record<string, unknown>> | null;
  source_mode?: string | null;
  recommended_scope_only?: boolean;
}

export type TruthSourceOverlapBlockerReportPreviewRequest = TruthSourceEvidenceIntakeBatchRequest;

export interface TruthSourceEvidenceIntakeBatchPreview {
  run_type: string;
  source_mode?: string | null;
  dry_run: boolean;
  mutations_planned: number;
  allowed_execute: boolean;
  recording_ready_status?: string | null;
  required_execute_flags_for_batch?: string[];
  candidate_count: number;
  source_acquisition_clue_count?: number;
  source_acquisition_clues?: Array<Record<string, unknown>>;
  ready_for_manual_evidence_preview_count?: number;
  recording_ready_count: number;
  new_supporting_source_ready_count?: number;
  supporting_source_already_present_count?: number;
  contradiction_candidate_count?: number;
  blocked_count?: number;
  original_candidate_count?: number;
  filtered_out_candidate_count?: number;
  recommended_recording_scope?: {
    scope: string;
    dry_run: boolean;
    mutations_planned: number;
    explicit_approval_required: boolean;
    required_execute_flags_for_batch?: string[];
    recommended_count: number;
    recommended_relationships: Array<{
      work_item_id?: string | null;
      request_type?: string | null;
      relationship_label?: string | null;
      bbl?: string | null;
      address?: string | null;
      manager_name?: string | null;
      source_name?: string | null;
      effect_status?: string | null;
      claim_id?: string | null;
      evidence_id?: string | null;
    }>;
    manual_evidence_payload_count?: number | null;
    manual_evidence_payloads?: Array<TruthManualEvidenceRequest | Record<string, unknown>>;
    manual_evidence_payload_review?: Array<Record<string, unknown>>;
    expected_post_recording_source_overlap?: TruthExpectedPostRecordingSourceOverlap;
    duplicate_or_freshness_only_count: number;
    duplicate_or_freshness_only_relationships: Array<Record<string, unknown>>;
    contradiction_review_count: number;
    contradiction_relationships: Array<Record<string, unknown>>;
    expected_effect?: string | null;
    non_effects?: {
      will_mark_verified: boolean;
      will_refresh_sources: boolean;
      will_materialize_relationships: boolean;
      will_start_jobs: boolean;
      will_allow_business_use: boolean;
    };
    post_recording_expectations?: TruthPostRecordingExpectations;
    safe_action?: string | null;
    filtered_view?: boolean;
    filtered_candidate_count?: number;
  };
  manual_evidence_replay_boundary?: TruthManualEvidenceReplayBoundary;
  recording_approval_packet?: TruthSourceEvidenceRecordingApprovalPacket;
  previews?: TruthSourceEvidenceIntakePreview[];
  approval_required_before_recording?: boolean;
  can_record_evidence_now?: boolean;
  source_clue_safe_action?: string | null;
  worklist_context?: TruthSourceEvidenceIntakePreview['worklist_context'];
  blocking_reasons?: string[];
  safe_action?: string | null;
  schema_status?: TruthSchemaStatus;
  blocked_reason?: string | null;
}

export interface TruthAdjudicationApplyRequest {
  limit?: number;
  dry_run?: boolean;
  confirm_execute?: boolean;
  run_id?: string | null;
}

export interface TruthAdjudicationApplyResponse {
  run_type: string;
  run_id?: string;
  dry_run: boolean;
  mutations_planned: number;
  allowed_execute: boolean;
  blocked_reason?: string | null;
  candidate_summary?: {
    fact_group_count?: number;
    verification_candidate_count?: number;
    safe_candidate_count?: number;
    claim_update_count?: number;
    skipped_candidate_count?: number;
  };
  proposed_database_changes?: Array<Record<string, unknown>>;
  rollback_manifest?: Record<string, unknown>;
  rollback_strategy?: string;
  required_execute_params?: Record<string, unknown>;
  schema_status?: TruthSchemaStatus;
}

export interface TruthRoleClaimCorrectionRequest {
  lead_id?: string;
  limit?: number;
  dry_run?: boolean;
  confirm_execute?: boolean;
  run_id?: string | null;
}

export interface TruthRoleClaimCorrectionResponse {
  run_type: string;
  run_id?: string;
  dry_run: boolean;
  mutations_planned: number;
  allowed_execute: boolean;
  blocked_reason?: string | null;
  lead_id?: string;
  candidate_summary?: {
    sampled_stale_claim_count?: number;
    claim_update_count?: number;
  };
  proposed_database_changes?: Array<Record<string, unknown>>;
  rollback_manifest?: Record<string, unknown>;
  rollback_strategy?: string;
  required_execute_params?: Record<string, unknown>;
  correction_preview?: TruthAdjudicationPreview['role_claim_correction_preview'];
  schema_status?: TruthSchemaStatus;
}

export const fetchLeadTruthSummary = (leadId: string): Promise<LeadTruthSummary> =>
  apiGet(`/api/v1/truth/leads/${encodeURIComponent(leadId)}/summary`);

export const fetchSubjectTruthSummary = (
  subjectType: TruthSubjectType | string,
  subjectId: string,
): Promise<SubjectTruthSummary> =>
  apiGet(`/api/v1/truth/subjects/${encodeURIComponent(subjectType)}/${encodeURIComponent(subjectId)}/summary`);

export const fetchTruthDashboard = (): Promise<TruthDashboard> =>
  apiGet('/api/v1/truth/dashboard');

export const fetchGoldenBenchmark = (): Promise<GoldenBenchmark> =>
  apiGet('/api/v1/truth/golden-benchmark');

export const fetchTruthReviewQueue = (limit = 5): Promise<TruthReviewQueue> =>
  apiGet(`/api/v1/truth/review-queue?limit=${limit}`);

export const fetchTruthValidationPreview = (sampleLimit = 10): Promise<TruthValidationPreview> =>
  apiPost(`/api/v1/truth/validate/preview?sample_limit=${sampleLimit}`, {});

export const fetchTruthMaterializationPreview = (limit = 5, sources: string[] = []): Promise<TruthMaterializationPreview> => {
  const params = new URLSearchParams({ limit: String(limit) });
  sources.forEach((source) => params.append('source', source));
  return apiPost(`/api/v1/truth/materialize/preview?${params.toString()}`, {});
};

export const fetchTruthAdjudicationPreview = (limit = 20): Promise<TruthAdjudicationPreview> =>
  apiGet(`/api/v1/truth/adjudication-preview?limit=${limit}`);

export const fetchTruthHealthReport = (): Promise<TruthHealthReport> =>
  apiGet('/api/v1/truth/health-report');

export const fetchTruthActivationPacket = (): Promise<TruthActivationPacket> =>
  apiGet('/api/v1/truth/activation-packet');

export const fetchTruthCompletionAudit = (): Promise<TruthCompletionAudit> =>
  apiGet('/api/v1/truth/completion-audit');

export const fetchTruthSourceOverlapApprovalPacket = (): Promise<TruthSourceOverlapApprovalPacket> =>
  apiGet('/api/v1/truth/source-overlap-approval-packet');

export const fetchTruthSourceOverlapPostRecordingCheck = (): Promise<TruthSourceOverlapPostRecordingCheck> =>
  apiGet('/api/v1/truth/source-overlap-post-recording-check');

export const fetchTruthManagerSourceAcquisitionPacket = (): Promise<TruthManagerSourceAcquisitionPacket> =>
  apiGet('/api/v1/truth/manager-source-acquisition-packet');

export const fetchTruthVerificationFrontier = (limit = 10): Promise<TruthVerificationFrontier> =>
  apiGet(`/api/v1/truth/verification-frontier?limit=${limit}`);

export const fetchTruthSourceAcquisitionWorklist = (maxItems = 10): Promise<TruthSourceAcquisitionWorklist> =>
  apiGet(`/api/v1/truth/source-acquisition-worklist?max_items=${maxItems}`);

export const fetchTruthSourceOverlapBlockerReport = (maxItems = 10): Promise<TruthSourceOverlapBlockerReport> =>
  apiGet(`/api/v1/truth/source-overlap-blocker-report?max_items=${maxItems}`);

export const previewTruthSourceOverlapBlockerReport = (
  request: TruthSourceOverlapBlockerReportPreviewRequest,
  maxItems = 10,
): Promise<TruthSourceOverlapBlockerReport> =>
  apiPost(`/api/v1/truth/source-overlap-blocker-report/preview?max_items=${maxItems}`, {
    recommended_scope_only: true,
    ...request,
  });

export const submitTruthReviewDecision = (
  reviewId: string,
  request: TruthReviewDecisionRequest,
): Promise<TruthReviewDecisionResponse> =>
  apiPost(`/api/v1/truth/review-queue/${encodeURIComponent(reviewId)}/decision`, {
    dry_run: true,
    confirm_execute: false,
    ...request,
  });

export const previewTruthManualEvidence = (
  request: TruthManualEvidenceRequest,
): Promise<TruthManualEvidenceResponse> =>
  apiPost('/api/v1/truth/manual-evidence', {
    dry_run: true,
    confirm_execute: false,
    ...request,
  });

export const previewTruthSourceEvidenceIntake = (
  request: TruthSourceEvidenceIntakeRequest,
): Promise<TruthSourceEvidenceIntakePreview> =>
  apiPost('/api/v1/truth/source-evidence-intake/preview', request);

export const previewTruthSourceEvidenceIntakeBatch = (
  request: TruthSourceEvidenceIntakeBatchRequest,
): Promise<TruthSourceEvidenceIntakeBatchPreview> =>
  apiPost('/api/v1/truth/source-evidence-intake/batch-preview', request);

export const previewTruthAdjudicationApply = (
  request: TruthAdjudicationApplyRequest = {},
): Promise<TruthAdjudicationApplyResponse> =>
  apiPost('/api/v1/truth/adjudication/apply', {
    dry_run: true,
    confirm_execute: false,
    ...request,
  });

export const previewTruthRoleClaimCorrection = (
  request: TruthRoleClaimCorrectionRequest = {},
): Promise<TruthRoleClaimCorrectionResponse> =>
  apiPost('/api/v1/truth/role-claim-corrections/apply', {
    dry_run: true,
    confirm_execute: false,
    ...request,
  });
