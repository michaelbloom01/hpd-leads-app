import { describe, expect, it } from 'vitest';
import { complianceCsv } from './compliance-export';
import type { ComplianceBuilding, ComplianceRecord, ComplianceResponse } from './compliance-api';

function record(id: string): ComplianceRecord {
  return {
    id, source_system: 'dob_safety', source_record_key: `SYNTHETIC-${id}`,
    record_type: 'violation', category: 'LL152', violation_type: 'FTF-PL-PER',
    device_type: 'Gas Piping - LL152', status: 'Active', issue_date: '2026-01-08',
    description: 'Synthetic source record.', source_url: 'https://www.nyc.gov/example-source',
    source_updated_at: '2026-08-30T12:00:00Z', observed_at: '2026-08-31T12:00:00Z',
    identity_status: 'exact_bin', stale: false,
  };
}

function building(bin: string, amount: number | null = 500000): ComplianceBuilding {
  return {
    bin, bbl: '0000000000', address: `${bin} Example Street`, records: [record(`${bin}-1`), record(`${bin}-2`)],
    reported_balance_cents: amount, interest_status: 'unverified', lien_status: 'unverified',
    stale: false, source_check_status: 'checked', source_updated_at: '2026-08-30T12:00:00Z', observed_at: '2026-08-31T12:00:00Z',
    balance_observations: amount === null ? [] : [{
      id: `synthetic-balance-${bin}`, bin, category: 'LL152', scope: 'bin_category', amount_cents: amount,
      source_url: 'https://www.nyc.gov/example-balance', source_updated_at: null,
      source_timestamp_raw: '8/31/2026 08:00', observed_at: '2026-08-31T12:00:00Z',
      reviewer: 'synthetic-reviewer@example.test', stale: false, amount_basis: 'manual_portal_observation',
    }],
  };
}

function response(buildings: ComplianceBuilding[]): ComplianceResponse {
  const known = buildings.filter(item => item.reported_balance_cents !== null);
  return {
    scope: { type: 'parcel', id: '0000000000' }, enabled: true, identity_ready: true,
    as_of: '2026-08-31T12:00:00Z', source_updated_at: '2026-08-30T12:00:00Z', stale: false,
    coverage: {
      status: 'complete', physical_building_count: buildings.length, checked_building_count: buildings.length,
      records_count: buildings.reduce((sum, item) => sum + item.records.length, 0),
      active_records_count: buildings.reduce((sum, item) => sum + item.records.length, 0),
      balance_known_building_count: known.length, missing_balance_bin_count: buildings.length - known.length,
    },
    reported_balance_cents: known.length ? known.reduce((sum, item) => sum + item.reported_balance_cents!, 0) : null,
    estimated_penalty_cents: null, warnings: [], buildings, provenance: [],
  };
}

// Parse quoted CSV independently, including commas, newlines and escaped quotes.
function rows(csv: string): Array<Record<string, string>> {
  const parsed: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let quoted = false;
  const text = csv.replace(/^\uFEFF/, '');
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (char === '"') {
      if (quoted && text[index + 1] === '"') { cell += '"'; index += 1; }
      else quoted = !quoted;
    } else if (!quoted && char === ',') {
      row.push(cell); cell = '';
    } else if (!quoted && char === '\r' && text[index + 1] === '\n') {
      row.push(cell); parsed.push(row); row = []; cell = ''; index += 1;
    } else cell += char;
  }
  row.push(cell); parsed.push(row);
  const header = parsed.shift()!;
  return parsed.map(values => Object.fromEntries(header.map((name, index) => [name, values[index]])));
}

describe('Compliance CSV evidence safeguards', () => {
  it('exports unknown amounts as empty cells and preserves a reported zero', () => {
    const exported = rows(complianceCsv(response([building('0000001', null), building('0000002', 0)])));
    const balances = exported.filter(row => row.row_type === 'building_balance');
    expect(balances[0].reported_balance_cents).toBe('');
    expect(balances[0].qualification).toContain('Balance unknown.');
    expect(balances[0].source_url).toBe('');
    expect(balances[1].reported_balance_cents).toBe('0');
    expect(balances[1].qualification).not.toContain('Balance unknown.');
    expect(exported[0].qualification).toContain('amounts missing: 1');
  });

  it('counts a BIN/category balance once regardless of violation count or older observations', () => {
    const first = building('0000001', 500000);
    first.balance_observations.push({ ...first.balance_observations[0], id: 'older-synthetic-balance', amount_cents: 250000, observed_at: '2025-01-01T00:00:00Z', stale: true });
    const exported = rows(complianceCsv(response([first, building('0000002', 700000)])));
    const balances = exported.filter(row => row.row_type === 'building_balance');
    expect(balances).toHaveLength(2);
    expect(new Set(balances.map(row => row.bin)).size).toBe(2);
    expect(balances.reduce((sum, row) => sum + Number(row.reported_balance_cents), 0)).toBe(1200000);
    expect(exported.filter(row => row.row_type === 'source_record')).toHaveLength(4);
    expect(exported.filter(row => row.row_type === 'source_record').every(row => row.reported_balance_cents === '')).toBe(true);
    expect(balances[0].qualification).toContain('Dated manual portal observation.');
    expect(balances[0].qualification).not.toContain('Historical/stale balance');
  });

  it.each(['=SUM(1,2)', '+1+1', '-1+1', '@SUM(A1)', '  =HYPERLINK("https://example.test")', '\t=1+1', '\r=1+1', '\n=1+1'])(
    'neutralizes spreadsheet formula-like source text: %j', value => {
      const synthetic = building('0000001');
      synthetic.address = value;
      synthetic.records[0].description = value;
      const exported = rows(complianceCsv(response([synthetic])));
      expect(exported.find(row => row.row_type === 'building_balance')!.address).toBe(`'${value}`);
      expect(exported.find(row => row.row_type === 'source_record')!.description).toBe(`'${value}`);
    },
  );

  it('preserves ordinary quoted and multiline source text without creating extra rows', () => {
    const synthetic = building('0000001');
    synthetic.records[0].description = 'Inspector said "check, then verify".\r\nSecond source line.';
    const csv = complianceCsv(response([synthetic]));
    const exported = rows(csv);
    expect(csv.startsWith('\uFEFF')).toBe(true);
    expect(exported).toHaveLength(4);
    expect(exported.find(row => row.row_type === 'source_record')!.description).toBe(synthetic.records[0].description);
  });

  it('qualifies stale manual balances even when the building and source records are fresh', () => {
    const synthetic = building('0000001');
    synthetic.balance_observations[0].stale = true;
    synthetic.balance_observations[0].observed_at = '2025-01-01T00:00:00Z';
    const exported = rows(complianceCsv(response([synthetic])));
    const balance = exported.find(row => row.row_type === 'building_balance')!;
    expect(balance.reported_balance_cents).toBe('500000');
    expect(balance.observed_at).toBe('2025-01-01T00:00:00Z');
    expect(balance.qualification).toContain('Basis: manual_portal_observation.');
    expect(balance.qualification).toContain('Historical/stale balance; refresh needed.');
    expect(balance.qualification).toContain('Interest and lien status unverified.');
    expect(exported.find(row => row.row_type === 'source_record')!.qualification).toContain('Dated source observation.');
  });

  it('retains source identity and historical qualifiers without assigning a balance to a record', () => {
    const synthetic = building('0000001');
    synthetic.records[0].stale = true;
    synthetic.records[0].identity_status = 'conflicting_source_identifiers';
    const exported = rows(complianceCsv(response([synthetic])));
    const source = exported.find(row => row.source_record_key === 'SYNTHETIC-0000001-1')!;
    expect(source.reported_balance_cents).toBe('');
    expect(source.source_url).toBe('https://www.nyc.gov/example-source');
    expect(source.qualification).toContain('conflicting_source_identifiers');
    expect(source.qualification).toContain('Historical/stale evidence.');
    expect(source.qualification).toContain('Same-building context does not establish causation.');
  });
});
