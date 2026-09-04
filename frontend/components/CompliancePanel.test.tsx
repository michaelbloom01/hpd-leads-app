import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import CompliancePanel, { formatComplianceMoney, safeComplianceSourceUrl } from './CompliancePanel';
import { ComplianceApiError, type ComplianceBuilding, type ComplianceResponse } from '../services/compliance-api';

const fetchComplianceMock = vi.fn();
vi.mock('../contexts/AuthContext', () => ({ useAuth: () => ({ user: { role: 'viewer' } }) }));
vi.mock('../services/compliance-api', async importOriginal => ({
  ...await importOriginal<typeof import('../services/compliance-api')>(),
  fetchCompliance: (...args: unknown[]) => fetchComplianceMock(...args),
}));

function building(bin: string, address: string): ComplianceBuilding {
  return {
    bin, bbl: '3025217501', address, reported_balance_cents: 500000,
    interest_status: 'unverified', lien_status: 'unverified', stale: false,
    source_check_status: 'checked', source_updated_at: '2026-08-27T12:00:00Z', observed_at: '2026-08-28T14:30:00Z',
    records: [{
      id: `safety-${bin}`, source_system: 'dob_safety', source_record_key: `VIO-${bin}`,
      record_type: 'violation', category: 'LL152', violation_type: 'FTF-PL-PER', device_type: 'Gas Piping - LL152',
      status: 'Active', issue_date: '2026-01-08', description: 'Cycle 2, Sub-cycle A',
      source_url: `https://data.cityofnewyork.us/resource/855j-jady.json?bin=${bin}`,
      source_updated_at: '2026-08-27T12:00:00Z', observed_at: '2026-08-28T14:30:00Z', identity_status: 'exact_bin', stale: false,
    }],
    balance_observations: [{
      id: `balance-${bin}`, bin, category: 'LL152', scope: 'bin_category', amount_cents: 500000,
      source_url: 'https://www.nyc.gov/assets/buildings/html/Unpaid_Violations_Search.html',
      source_updated_at: null, source_timestamp_raw: '8/28/2026 10:09', observed_at: '2026-08-28T14:30:00Z',
      reviewer: 'Research reviewer', stale: false, amount_basis: 'manual_portal_observation',
    }],
  };
}

function response(): ComplianceResponse {
  return {
    scope: { type: 'parcel', id: '3025217501' }, enabled: true, identity_ready: true,
    as_of: '2026-08-28T14:30:00Z', source_updated_at: '2026-08-27T12:00:00Z', stale: false,
    coverage: { status: 'complete', physical_building_count: 4, checked_building_count: 4, records_count: 4,
      active_records_count: 4, balance_known_building_count: 4, missing_balance_bin_count: 0 },
    reported_balance_cents: 2000000, estimated_penalty_cents: null, warnings: [],
    buildings: [building('3348179', '82 Green Street'), building('3348178', '84 Green Street'), building('3064119', '86 Green Street'), building('3350268', '88 Green Street')],
    provenance: [{ source_system: 'dob_safety', source_url: 'https://data.cityofnewyork.us/d/855j-jady',
      source_updated_at: '2026-08-27T12:00:00Z', observed_at: '2026-08-28T14:30:00Z', status: 'checked' }],
  };
}

function renderPanel(scope: 'parcels' | 'portfolio' = 'parcels', scopeId = '3025217501') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(<QueryClientProvider client={client}><CompliancePanel scope={scope} scopeId={scopeId} /></QueryClientProvider>);
}

describe('CompliancePanel evidence and identity safety', () => {
  beforeEach(() => { vi.clearAllMocks(); fetchComplianceMock.mockResolvedValue(response()); });
  afterEach(() => { cleanup(); });

  it('preserves four physical BINs on one parcel and switches exact source evidence', async () => {
    renderPanel();
    expect(await screen.findByText('$20,000')).toBeInTheDocument();
    expect(screen.getByText('4 physical buildings on this shared tax lot')).toBeInTheDocument();
    expect(screen.getAllByRole('region', { name: /Compliance parcel/ })).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: /Inspect/ })).toHaveLength(4);
    fireEvent.click(screen.getByRole('button', { name: 'Inspect 84 Green Street' }));
    expect(screen.getByText('VIO-3348178')).toBeInTheDocument();
    expect(screen.queryByText('VIO-3348179')).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: /DOB Safety record/ })).toHaveAttribute('href', expect.stringContaining('bin=3348178'));
    fireEvent.click(screen.getByRole('button', { name: 'Source evidence' }));
    expect(screen.getByRole('link', { name: /DOB Safety record/ })).toHaveAttribute('href', expect.stringContaining('bin=3348178'));
    expect(screen.getByText('Source display label: 8/28/2026 10:09')).toBeInTheDocument();
    expect(screen.getByText('Reviewer: Research reviewer')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Internal review' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /pay|send/i })).not.toBeInTheDocument();
  });

  it('sizes physical-building cards to their container instead of viewport breakpoints', async () => {
    renderPanel();
    const picker = await screen.findByLabelText('Select physical building');
    expect(picker).toHaveStyle({ gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 160px), 1fr))' });
    expect(picker.className).not.toMatch(/(?:sm|md|lg|xl):grid-cols/);
    const card = screen.getByRole('button', { name: 'Inspect 82 Green Street' });
    expect(within(card).getByText('BIN 3348179')).toHaveClass('break-all');
    expect(within(card).getByText('$5,000')).toHaveClass('[overflow-wrap:anywhere]');
  });

  it('shows a single physical building directly with its source link and evidence view', async () => {
    const data = response();
    data.buildings = [data.buildings[0]];
    fetchComplianceMock.mockResolvedValue(data);
    renderPanel();
    expect(await screen.findByText('BIN 3348179')).toBeInTheDocument();
    expect(screen.queryByLabelText('Select physical building')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Inspect/ })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: /DOB Safety record/ })).toHaveAttribute('href', expect.stringContaining('bin=3348179'));
    fireEvent.click(screen.getByRole('button', { name: 'Source evidence' }));
    expect(screen.getAllByRole('link', { name: /DOB Safety record/ })).toHaveLength(1);
    expect(screen.getByRole('region', { name: 'Balance source evidence' })).toBeInTheDocument();
  });

  it('starts with active records and offers separately scoped complaint history', async () => {
    const data = response();
    data.buildings[0].records.push({ ...data.buildings[0].records[0], id: 'closed-complaint', source_system: 'dob_complaints', source_record_key: 'CLOSED-COMPLAINT', record_type: 'complaint', status: 'CLOSED' });
    fetchComplianceMock.mockResolvedValue(data);
    renderPanel();
    expect(await screen.findByText('VIO-3348179')).toBeInTheDocument();
    expect(screen.queryByText('CLOSED-COMPLAINT')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Complaint history (1)' }));
    expect(screen.getByText('CLOSED-COMPLAINT')).toBeInTheDocument();
    expect(screen.queryByText('VIO-3348179')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Inspect 84 Green Street' }));
    expect(screen.getByRole('button', { name: 'Active at source (1)' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('VIO-3348178')).toBeInTheDocument();
  });

  it.each([
    ['complaint', 'dob_complaints', 'CLOSED', 'Complaint history (1)'],
    ['case_evidence', 'oath_ecb', 'resolved', 'All records (1)'],
  ])('shows %s evidence immediately when no active violations are saved', async (recordType, source, status, filter) => {
    const data = response();
    data.buildings = [data.buildings[0]];
    data.buildings[0].records = [{
      ...data.buildings[0].records[0], record_type: recordType, source_system: source,
      source_record_key: 'HISTORY-1', status,
    }];
    fetchComplianceMock.mockResolvedValue(data);
    renderPanel();
    expect(await screen.findByText('HISTORY-1')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: filter })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Active at source (0)' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByText(`${status} at source`)).toBeInTheDocument();
  });

  it('shows ECB money on the exact record and excludes it from the portfolio subtotal', async () => {
    const data = response();
    data.buildings[0].records.push({
      ...data.buildings[0].records[0],
      id: 'ecb-record', source_system: 'dob_ecb', source_record_key: '35299130P',
      category: 'DOB_ECB_VIOLATION', device_type: 'CLASS - 1', violation_type: 'Plumbing',
      served_date: '2026-01-09', hearing_date: '2026-02-01', hearing_status: 'IN VIOLATION',
      certification_status: 'NO COMPLIANCE RECORDED', penalty_imposed_cents: 250000,
      amount_paid_cents: 50025, balance_due_cents: 199975,
      monetary_rollup_status: 'record_only_pending_ecb_oath_deduplication',
    });
    fetchComplianceMock.mockResolvedValue(data);
    renderPanel();
    expect(await screen.findByText('35299130P')).toBeInTheDocument();
    expect(screen.getByText('ECB balance due')).toBeInTheDocument();
    expect(screen.getByText('$1,999.75')).toBeInTheDocument();
    expect(screen.getByText('Excluded pending ECB/OATH duplicate review')).toBeInTheDocument();
    expect(screen.getByText('$20,000')).toBeInTheDocument();
  });

  it('shows exact-ticket OATH judgment evidence and labels signed credits safely', async () => {
    const data = response();
    data.buildings[0].records.push({
      ...data.buildings[0].records[0],
      id: 'oath-record', source_system: 'oath_ecb', source_record_key: '035299129H',
      record_type: 'case_evidence', category: 'OATH_ECB_CASE', device_type: 'OATH case status',
      status: 'resolved', linked_dob_ecb_violation_number: '35299129H',
      hearing_date: '2018-10-18', hearing_status: 'PAID IN FULL', hearing_result: 'IN VIOLATION',
      compliance_status: 'All Terms Met', judgment_docketed_date: '2019-01-31',
      penalty_imposed_cents: 250000, amount_paid_cents: 255200,
      additional_penalties_cents: 0, oath_balance_due_cents: -5309,
      oath_balance_character: 'credit_or_adjustment',
      monetary_rollup_status: 'record_only_exact_oath_ticket_evidence',
      identity_status: 'linked_via_exact_ticket',
    });
    fetchComplianceMock.mockResolvedValue(data);
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'All records (2)' }));
    expect(screen.getByText('035299129H')).toBeInTheDocument();
    expect(screen.getByText('35299129H')).toBeInTheDocument();
    expect(screen.getByText('Judgment docketed')).toBeInTheDocument();
    expect(screen.getByText('Jan 31, 2019')).toBeInTheDocument();
    expect(screen.getByText('$53.09 credit or adjustment')).toBeInTheDocument();
    expect(screen.getByText('Excluded pending cross-source duplicate review')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Active at source (1)' })).toBeInTheDocument();
  });

  it('loads long saved histories in batches and resets the batch on filter change', async () => {
    const data = response();
    data.buildings[0].records = Array.from({ length: 12 }, (_, index) => ({ ...data.buildings[0].records[0], id: `record-${index}`, source_record_key: `RECORD-${index}`, status: 'CLOSED' }));
    fetchComplianceMock.mockResolvedValue(data);
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'All records (12)' }));
    expect(screen.getByText('RECORD-9')).toBeInTheDocument();
    expect(screen.queryByText('RECORD-10')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show more records (2 remaining)' }));
    expect(screen.getByText('RECORD-11')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Active at source (0)' }));
    expect(screen.getByText(/No saved records in this filter/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'All records (12)' }));
    expect(screen.queryByText('RECORD-10')).not.toBeInTheDocument();
  });

  it('distinguishes incomplete portfolio identities from stale source checks', async () => {
    const data = response();
    data.stale = true;
    data.coverage.status = 'partial';
    data.coverage.unmapped_parcel_count = 93;
    fetchComplianceMock.mockResolvedValue(data);
    renderPanel('portfolio', 'lead-1');
    expect(await screen.findByText('More identities to map')).toBeInTheDocument();
    expect(screen.queryByText('Source refresh needed')).not.toBeInTheDocument();
  });

  it('preserves both mapping and source-age warnings when both need attention', async () => {
    const data = response();
    data.stale = true;
    data.coverage.status = 'partial';
    data.coverage.unmapped_parcel_count = 93;
    data.buildings[0].source_checks = [{ source_system: 'dob_safety', status: 'checked', records_count: 1, source_updated_at: '2026-01-01T12:00:00Z', observed_at: '2026-01-02T12:00:00Z', stale: true }];
    data.source_coverage = [{ source_system: 'dob_safety', status: 'partial', checked_building_count: 4, physical_building_count: 4, records_count: 4, active_records_count: 4, open_complaints_count: 0, source_updated_at: '2026-01-01T12:00:00Z', observed_at: '2026-01-02T12:00:00Z', stale: true }];
    fetchComplianceMock.mockResolvedValue(data);
    renderPanel('portfolio', 'lead-1');
    expect(await screen.findByText('More identities to map')).toBeInTheDocument();
    expect(screen.getByText('Source refresh needed')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Source-specific coverage' })).not.toBeVisible();
    fireEvent.click(screen.getByText('Source coverage and dates'));
    expect(within(screen.getByRole('region', { name: 'Source-specific coverage' })).getByText(/Scope incomplete · Refresh needed/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'DOB Safety ↗' })).toHaveAttribute('href', 'https://data.cityofnewyork.us/d/855j-jady');
  });

  it('keeps missing balances distinct from zero and labels a known subtotal', async () => {
    const data = response();
    data.buildings[1].reported_balance_cents = null;
    data.buildings[1].balance_observations = [];
    data.reported_balance_cents = 1500000;
    data.coverage.balance_known_building_count = 3;
    data.coverage.missing_balance_bin_count = 1;
    fetchComplianceMock.mockResolvedValue(data);
    renderPanel();
    expect(await screen.findByText('$15,000')).toBeInTheDocument();
    expect(screen.getByText('Reported unpaid balance · Known subtotal')).toBeInTheDocument();
    expect(screen.getByText('1 building balance unavailable')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Inspect 84 Green Street' }));
    expect(screen.getAllByText('Balance unavailable')).toHaveLength(2);
    expect(screen.queryByText('$0')).not.toBeInTheDocument();
  });

  it('shows an explicitly reported zero independently from Active compliance status', async () => {
    const data = response();
    data.buildings = [building('3348179', '82 Green Street')];
    data.buildings[0].reported_balance_cents = 0;
    data.buildings[0].balance_observations[0].amount_cents = 0;
    data.reported_balance_cents = 0;
    fetchComplianceMock.mockResolvedValue(data);
    renderPanel();
    expect((await screen.findAllByText('$0')).length).toBeGreaterThan(0);
    expect(screen.getByText('Active at source')).toBeInTheDocument();
    expect(screen.getByText('Payment and compliance closure are tracked separately.')).toBeInTheDocument();
  });

  it('keeps a historical balance dated and interest and liens unverified', async () => {
    const data = response();
    data.stale = true;
    data.buildings.forEach(row => { row.stale = true; row.records[0].stale = true; row.balance_observations[0].stale = true; });
    fetchComplianceMock.mockResolvedValue(data);
    renderPanel();
    expect(await screen.findByText('Source refresh needed')).toBeInTheDocument();
    expect(screen.getAllByText('Last reported balance')).toHaveLength(2);
    expect(screen.queryByText('Interest')).not.toBeInTheDocument();
    expect(screen.queryByText('Lien status')).not.toBeInTheDocument();
    expect(screen.queryByText('Balance scope')).not.toBeInTheDocument();
    expect(screen.getByText(/Interest and lien status require specific evidence/)).toBeInTheDocument();
    expect(screen.getByText('Historical source snapshot. Refresh needed.')).toBeInTheDocument();
    expect(screen.queryByText(/no interest|no lien|0% interest/i)).not.toBeInTheDocument();
  });

  it('uses the latest balance freshness while retaining older source history', async () => {
    const data = response();
    data.buildings[0].balance_observations.push({ ...data.buildings[0].balance_observations[0], id: 'older-balance', observed_at: '2026-01-09T12:00:00Z', stale: true });
    fetchComplianceMock.mockResolvedValue(data);
    renderPanel();
    expect(await screen.findByText('Balance evidence checked: Aug 28, 2026')).toBeInTheDocument();
    expect(screen.queryByText('Last reported balance')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Source evidence' }));
    expect(screen.getByText(/Last portal-observed balance/)).toBeInTheDocument();
  });

  it('surfaces a source identity conflict before opening the evidence panel', async () => {
    const data = response();
    data.buildings[0].records[0].identity_status = 'conflicting_source_identifiers';
    fetchComplianceMock.mockResolvedValue(data);
    renderPanel();
    expect(await screen.findByText('Building identity requires review: conflicting source identifiers.')).toBeInTheDocument();
  });

  it('separates a checked empty DOB source from broad compliance clearance', async () => {
    const data = response();
    data.buildings = [building('3348179', '82 Green Street')];
    data.buildings[0].records = [];
    data.buildings[0].reported_balance_cents = null;
    data.buildings[0].balance_observations = [];
    data.reported_balance_cents = null;
    fetchComplianceMock.mockResolvedValue(data);
    renderPanel();
    expect(await screen.findByText(/No DOB Safety records in the completed source check/)).toBeInTheDocument();
    expect(screen.queryByText(/No violations|compliant|cleared/i)).not.toBeInTheDocument();
  });

  it.each(['disabled', 'schema_unavailable', 'identity_unavailable'] as const)('shows the %s gate without sample amounts', async status => {
    const data = response();
    data.coverage.status = status;
    data.enabled = status !== 'disabled';
    data.buildings = [];
    fetchComplianceMock.mockResolvedValue(data);
    renderPanel();
    expect(await screen.findByText(/awaiting/)).toBeInTheDocument();
    expect(screen.queryByText('$20,000')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Compliance intelligence' })).toBeInTheDocument();
  });

  it('shows loading and recoverable backend failure without asserting coverage', async () => {
    let reject: (reason?: unknown) => void = () => {};
    fetchComplianceMock.mockReturnValue(new Promise((_, rejectPromise) => { reject = rejectPromise; }));
    renderPanel();
    expect(screen.getByRole('status')).toHaveTextContent('Loading compliance evidence');
    reject(new ComplianceApiError('Compliance data is temporarily unavailable.', 503));
    expect(await screen.findByRole('alert')).toHaveTextContent('Coverage and balances remain unverified');
    expect(screen.queryByText('$0')).not.toBeInTheDocument();
    fetchComplianceMock.mockResolvedValue(response());
    fireEvent.click(screen.getByRole('button', { name: 'Reload saved data' }));
    expect(await screen.findByText('$20,000')).toBeInTheDocument();
  });

  it('uses the company portfolio endpoint and exposes source notes', async () => {
    const data = response();
    data.warnings = ['The portfolio is only partly checked.'];
    fetchComplianceMock.mockResolvedValue(data);
    renderPanel('portfolio', 'lead-1');
    await waitFor(() => expect(fetchComplianceMock).toHaveBeenCalledWith('portfolio', 'lead-1', expect.any(AbortSignal)));
    fireEvent.click(await screen.findByText('Coverage notes (1)'));
    expect(screen.getByText('The portfolio is only partly checked.')).toBeVisible();
    fireEvent.click(screen.getByText('Source coverage and dates'));
    expect(screen.getByRole('link', { name: 'DOB Safety ↗' })).toHaveAttribute('href', 'https://data.cityofnewyork.us/d/855j-jady');
  });

  it('shows separately scoped category evidence once when multiple records share one building balance', async () => {
    const data = response();
    data.buildings[0].records.push({ ...data.buildings[0].records[0], id: 'another-violation', source_record_key: 'VIO-OTHER' });
    fetchComplianceMock.mockResolvedValue(data);
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Source evidence' }));
    const evidence = screen.getByRole('region', { name: 'Balance source evidence' });
    expect(within(evidence).getAllByText(/Portal-observed balance/)).toHaveLength(1);
    expect(screen.getByText('VIO-OTHER')).toBeInTheDocument();
    expect(screen.getByText('$20,000')).toBeInTheDocument();
  });
});

describe('compliance formatting and source-link safety', () => {
  it('formats integer cents, explicit zero, and unknown values accurately', () => {
    expect(formatComplianceMoney(500001)).toBe('$5,000.01');
    expect(formatComplianceMoney(0)).toBe('$0');
    expect(formatComplianceMoney(null)).toBe('Unavailable');
    expect(formatComplianceMoney(Number.MAX_SAFE_INTEGER + 1)).toBe('Unavailable');
    expect(formatComplianceMoney(-1)).toBe('Unavailable');
  });
  it.each(['javascript:alert(1)', 'https://nyc.gov.attacker.example/record', 'https://user:secret@nyc.gov/record', 'http://nyc.gov/record'])('rejects unsafe source URL %s', url => {
    expect(safeComplianceSourceUrl(url)).toBeNull();
  });
});
