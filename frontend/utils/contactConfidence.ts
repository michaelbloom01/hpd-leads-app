export interface ContactEvidenceInput {
  name?: string | null;
  role?: string | null;
  source?: string | null;
  address?: string | null;
  confidence_hint?: string | null;
  is_decision_maker?: boolean | null;
  board_role?: string | null;
  filing_date?: string | null;
  snapshot_as_of?: string | null;
  publication_date?: string | null;
  as_of_date?: string | null;
}

export interface ContactConfidenceAssessment {
  label: string;
  safeAction: string;
  rationale: string;
  score: number;
  toneClass: string;
  warnings: string[];
}

const MS_PER_DAY = 1000 * 60 * 60 * 24;

const normalize = (value: string | null | undefined): string => (value || '').trim().toLowerCase();

export const contactEvidenceDate = (contact: ContactEvidenceInput): string | null =>
  contact.filing_date || contact.snapshot_as_of || contact.publication_date || contact.as_of_date || null;

export const contactEvidenceAgeDays = (
  contact: ContactEvidenceInput,
  now: Date = new Date(),
): number | null => {
  const rawDate = contactEvidenceDate(contact);
  if (!rawDate) return null;
  const observedAt = new Date(rawDate);
  if (Number.isNaN(observedAt.getTime())) return null;
  return Math.max(0, Math.floor((now.getTime() - observedAt.getTime()) / MS_PER_DAY));
};

export function assessContactConfidence(
  contact: ContactEvidenceInput,
  now: Date = new Date(),
): ContactConfidenceAssessment {
  const source = normalize(contact.source);
  const role = normalize(contact.role);
  const name = normalize(contact.name);
  const address = normalize(contact.address);
  const ageDays = contactEvidenceAgeDays(contact, now);
  const warnings: string[] = [];

  let score = 0.5;
  if (source.includes('dos filing')) score += 0.22;
  else if (source.includes('dos snapshot')) score += 0.15;
  else if (source.includes('hpd')) score += 0.18;

  if (contact.is_decision_maker) score += 0.12;
  if (contact.board_role || normalize(contact.confidence_hint).includes('board')) score += 0.06;
  if (role.includes('management') || role.includes('manager')) score += 0.08;

  const legalOrMailbox =
    role.includes('agent') ||
    name.includes(' law') ||
    name.includes('attorney') ||
    name.includes('legal') ||
    address.includes('p.o.') ||
    address.includes('po box') ||
    address.includes('c/o') ||
    address.includes('care of');

  if (legalOrMailbox) {
    score -= 0.18;
    warnings.push('May be a registered agent, legal mailing address, or mailbox rather than an operator.');
  }

  if (ageDays == null) {
    score -= 0.05;
    warnings.push('No usable evidence date.');
  } else if (ageDays > 730) {
    score -= 0.22;
    warnings.push('Evidence is more than two years old.');
  } else if (ageDays > 365) {
    score -= 0.12;
    warnings.push('Evidence is more than one year old.');
  }

  const boundedScore = Math.max(0.05, Math.min(0.98, score));

  if (ageDays != null && ageDays > 730) {
    return {
      label: 'Stale evidence',
      safeAction: 'Verify before outreach',
      rationale: 'The contact may still be useful as a lead, but its source date is too old for direct action.',
      score: boundedScore,
      toneClass: 'bg-amber-50 text-amber-700 border-amber-200',
      warnings,
    };
  }

  if (legalOrMailbox) {
    return {
      label: 'Legal or mailing path',
      safeAction: 'Use for verification only',
      rationale: 'This record can support ownership or service-of-process research, not a direct manager assumption.',
      score: boundedScore,
      toneClass: 'bg-gray-50 text-gray-700 border-gray-200',
      warnings,
    };
  }

  if (contact.is_decision_maker || contact.board_role || normalize(contact.confidence_hint).includes('board')) {
    return {
      label: 'Board/owner research',
      safeAction: 'Use as a research path',
      rationale: 'The record points to a likely building stakeholder, but still needs confirmation before outreach claims.',
      score: boundedScore,
      toneClass: 'bg-emerald-50 text-emerald-700 border-emerald-200',
      warnings,
    };
  }

  if (role.includes('management') || role.includes('manager')) {
    return {
      label: 'Possible manager path',
      safeAction: 'Confirm manager role',
      rationale: 'The role suggests management, but the system should still check for contradictory or newer evidence.',
      score: boundedScore,
      toneClass: 'bg-blue-50 text-blue-700 border-blue-200',
      warnings,
    };
  }

  return {
    label: 'Needs role review',
    safeAction: 'Do not assume decision maker',
    rationale: 'The source confirms a contact record, not necessarily the current owner, manager, or buyer contact.',
    score: boundedScore,
    toneClass: 'bg-amber-50 text-amber-700 border-amber-200',
    warnings,
  };
}
