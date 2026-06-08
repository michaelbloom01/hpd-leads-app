import type { LeadTruthSummary, SubjectTruthSummary, TruthClaim } from '../services/truth-api';

type TruthSummary = LeadTruthSummary | SubjectTruthSummary | null | undefined;

const HUMAN_LABELS: Record<string, string> = {
  acris: 'ACRIS',
  activation_gap_count: 'Activation gaps',
  acquisition_quality_diligence: 'Acquisition diligence',
  acquisition_required: 'Needs source acquisition',
  automated_enrichment: 'Use as enrichment context',
  blocked_evidence_acquisition_required: 'Evidence acquisition required',
  broad_discovery: 'Research only',
  building_management: 'relationship ledger',
  claimed_manager: 'claimed manager',
  conflicting_evidence: 'Needs review',
  confirm_execute: 'execution approval',
  current_ledger_source_ready: 'current source-ready claims',
  dob_permits: 'DOB permits',
  do_not_act: 'Do not use yet',
  evidence_request_packet: 'evidence request packet',
  hpd_contacts: 'HPD contacts',
  hpd_registration: 'HPD registration',
  hpd_registrations: 'HPD registration',
  hpd_source_contacts: 'HPD source contacts',
  manages_buildings: 'Management portfolio found',
  materialization_or_review_required: 'Needs review',
  no_action: 'Do not use yet',
  not_checked: 'Not checked',
  not_evaluated: 'Not evaluated',
  official_hpd_query_packet_only: 'official HPD query packet only',
  operator_confirmed_management_evidence_batch: 'operator-confirmed evidence batch',
  outreach_confirmed: 'outreach confirmed',
  outreach_feedback: 'outreach feedback',
  ranked_sourcing: 'Use with caution',
  recommended_outreach: 'Outreach-ready',
  review_queue: 'review queue',
  safe_to_mark_verified: 'safe to mark verified',
  schema_not_ready: 'ledger setup incomplete',
  source_acquisition_frontier: 'source-acquisition frontier',
  source_gap: 'Needs more sources',
  source_ready: 'source-ready',
  source_ready_below_verified: 'source-ready, below verified',
  truth_verification_frontier: 'verification frontier',
  verified: 'Verified',
};

export const humanizeTruthLabel = (
  value: string | number | null | undefined,
  fallback = 'not evaluated',
): string => {
  const raw = String(value ?? fallback);
  if (!raw) return fallback;
  const contradictionSuffix = ' (contradicts)';
  const hasContradictionSuffix = raw.endsWith(contradictionSuffix);
  const normalized = hasContradictionSuffix ? raw.slice(0, -contradictionSuffix.length) : raw;
  const label = HUMAN_LABELS[normalized] || normalized.replace(/_/g, ' ');
  return hasContradictionSuffix ? `${label} (conflicts)` : label;
};

export const formatTruthAction = (action: string | null | undefined): string => (
  humanizeTruthLabel(action, 'not_evaluated')
);

export const formatReviewBucket = (bucket: string | null | undefined): string => (
  humanizeTruthLabel(bucket, 'not_evaluated')
);

export const formatSourceName = (source: string | null | undefined): string => (
  humanizeTruthLabel(source, 'source')
);

export const formatSourceList = (sources: Array<string | null | undefined> | null | undefined): string => (
  (sources || []).filter(Boolean).map((source) => formatSourceName(source)).join(', ')
);

export const formatEvidenceAge = (freshnessDays: number | null | undefined): string => {
  if (freshnessDays == null) return 'Freshness unknown';
  if (freshnessDays <= 0) return 'Updated today';
  if (freshnessDays === 1) return 'Updated 1 day ago';
  if (freshnessDays < 30) return `Updated ${freshnessDays} days ago`;
  const months = Math.floor(freshnessDays / 30);
  if (months === 1) return 'Updated 1 month ago';
  if (months < 12) return `Updated ${months} months ago`;
  const years = Math.floor(months / 12);
  return years === 1 ? 'Updated 1 year ago' : `Updated ${years} years ago`;
};

export const formatClaimTitle = (claim: TruthClaim): string => {
  const value = claim.normalized_value || claim.object_id || '';
  if (claim.predicate === 'exists_in_building_table') return 'Building record found';
  if (claim.predicate === 'has_current_management_link') {
    return claim.contradicting_evidence_count > 0
      ? 'Management relationship needs review'
      : 'Management relationship found';
  }
  if (claim.predicate === 'has_owner') return `Owner relationship found${value ? `: ${value}` : ''}`;
  if (claim.predicate === 'has_person_contact') return `Contact found${value ? `: ${value}` : ''}`;
  if (claim.predicate === 'has_registered_agent') return `Registered agent found${value ? `: ${value}` : ''}`;
  if (claim.predicate === 'has_owner_contact') return `Owner contact found${value ? `: ${value}` : ''}`;
  if (claim.predicate === 'maps_to_canonical_entity') return 'Canonical entity match';
  if (claim.predicate === 'has_direct_contact') return `Direct contact found${value ? `: ${value}` : ''}`;
  if (claim.predicate === 'has_contact_path') return `Contact path found${value ? `: ${value}` : ''}`;
  if (claim.predicate === 'manages_buildings') return `Management portfolio found${value ? `: ${value}` : ''}`;
  if (claim.predicate === 'has_management_relationship') return `Management relationship found${value ? `: ${value}` : ''}`;
  return humanizeTruthLabel(claim.predicate);
};

export const formatClaimSubtitle = (claim: TruthClaim): string => {
  if (claim.predicate === 'exists_in_building_table') {
    return 'The building itself is present in source data.';
  }
  if (claim.predicate === 'has_current_management_link' || claim.predicate === 'has_management_relationship') {
    return claim.contradicting_evidence_count > 0
      ? 'Relationship records point to different entities. Treat the manager as unverified until reviewed.'
      : 'Current relationship evidence points to this manager or entity.';
  }
  if (claim.predicate === 'has_owner') {
    return claim.contradicting_evidence_count > 0
      ? 'Ownership sources conflict. Use as diligence context until reviewed.'
      : 'Ownership evidence is supported by the current source set.';
  }
  if (claim.predicate === 'has_person_contact' || claim.predicate === 'has_direct_contact') {
    return 'Useful as a contact lead, not proof of property-management authority by itself.';
  }
  if (claim.predicate === 'has_contact_path') {
    return 'There is at least one usable contact path, but contactability is separate from manager authority.';
  }
  if (claim.predicate === 'manages_buildings') {
    return 'Portfolio evidence links this lead to buildings; review source support before treating it as verified.';
  }
  if (claim.predicate === 'has_registered_agent') {
    return 'Useful for verification and research; do not assume this is the property manager.';
  }
  if (claim.predicate === 'has_owner_contact') {
    return 'Ownership evidence is useful context, separate from manager authority.';
  }
  if (claim.predicate === 'maps_to_canonical_entity') {
    return claim.contradicting_evidence_count > 0
      ? 'Identity match needs review before relying on rollups.'
      : 'This record is linked to the canonical entity used for rollups.';
  }
  return claim.normalized_value || claim.object_id || humanizeTruthLabel(claim.claim_type);
};

export const visibleTruthClaims = (claims: TruthClaim[] | null | undefined, limit = 4): TruthClaim[] => {
  const nonAdministrative = (claims || []).filter((claim) => claim.predicate !== 'exists_in_building_table');
  return (nonAdministrative.length ? nonAdministrative : claims || []).slice(0, limit);
};

export const truthSummaryHeadline = (summary: TruthSummary, subjectLabel = 'record'): string => {
  if (!summary) return 'Confidence summary has not loaded yet.';
  const contradictions = summary.belief_summary.contradiction_count || 0;
  if (contradictions > 0) {
    return `Conflicting evidence found. Use this ${subjectLabel} as a research lead until review clears it.`;
  }
  if ((summary.belief_summary.supporting_sources || []).length >= 2) {
    return `Multiple sources support this ${subjectLabel}. Use the confidence score to decide the next action.`;
  }
  return `No contradictions surfaced, but this ${subjectLabel} may still need another source before outreach.`;
};
