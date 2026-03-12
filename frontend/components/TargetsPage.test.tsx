import { describe, expect, it } from 'vitest';

import { parseTargetPaste } from './TargetsPage';


describe('parseTargetPaste', () => {
  it('parses a tab-delimited PM target table', () => {
    const rows = parseTargetPaste([
      'Company\tPortfolio Size\tUnits(est.)\tGeography\tOwnership\tKey Principal(s)\tCondo/Co-op Focus\tWebsite\tPhone\tAddress\tTier\tAcquisition Fit Notes\tKey Risk / Flag',
      'AJ Clarke\t40 buildings\t2500 units\tManhattan\tFounder-led\tJohn Clarke\tHigh condo\tajclarke.com\t212-555-0000\t123 Main St\t1\tGreat fit\tNeed principal intro',
    ].join('\n'));

    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      company_name: 'AJ Clarke',
      portfolio_estimate: '40 buildings',
      units_estimate: '2500 units',
      geography: 'Manhattan',
      key_principals: 'John Clarke',
      tier: '1',
      acquisition_fit_notes: 'Great fit',
      risk_flag: 'Need principal intro',
    });
  });
});
