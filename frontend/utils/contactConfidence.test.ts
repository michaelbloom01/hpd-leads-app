import { describe, expect, it } from 'vitest';

import { assessContactConfidence, contactEvidenceAgeDays } from './contactConfidence';

const now = new Date('2026-05-14T12:00:00Z');

describe('contact confidence assessment', () => {
  it('treats fresh board/DOS records as research paths rather than verified PM contacts', () => {
    const assessment = assessContactConfidence({
      name: 'Jane Boardmember',
      role: 'DOS Chairman (Biennial)',
      source: 'NY DOS Filing',
      is_decision_maker: true,
      board_role: 'Chairman',
      filing_date: '2026-04-14',
    }, now);

    expect(assessment.label).toBe('Board/owner research');
    expect(assessment.safeAction).toBe('Use as a research path');
    expect(assessment.score).toBeGreaterThan(0.75);
  });

  it('downgrades registered-agent and mailbox records to verification-only evidence', () => {
    const assessment = assessContactConfidence({
      name: 'Example Law PLLC',
      role: 'Agent',
      source: 'HPD Contacts',
      address: 'C/O Example Law PLLC',
      as_of_date: '2026-03-01',
    }, now);

    expect(assessment.label).toBe('Legal or mailing path');
    expect(assessment.safeAction).toBe('Use for verification only');
    expect(assessment.warnings.join(' ')).toMatch(/registered agent/i);
  });

  it('flags old source dates before contact evidence is used for outreach', () => {
    const assessment = assessContactConfidence({
      name: 'Old Manager LLC',
      role: 'ManagementCompany',
      source: 'HPD Contacts',
      as_of_date: '2022-01-01',
    }, now);

    expect(contactEvidenceAgeDays({ as_of_date: '2022-01-01' }, now)).toBeGreaterThan(1500);
    expect(assessment.label).toBe('Stale evidence');
    expect(assessment.safeAction).toBe('Verify before outreach');
  });
});
