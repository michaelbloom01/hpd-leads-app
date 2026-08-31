import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import CompliancePanel, { formatComplianceMoney, safeComplianceSourceUrl } from './CompliancePanel';
import { ComplianceApiError, type ComplianceBuilding, type ComplianceResponse } from '../services/compliance-api';

const fetchComplianceMock = vi.fn();
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
    fireEvent.click(screen.getByRole('button', { name: 'Source evidence' }));
    expect(screen.getByRole('link', { name: /DOB Safety record/ })).toHaveAttribute('href', expect.stringContaining('bin=3348178'));
    expect(screen.getByText('Source display label: 8/28/2026 10:09')).toBeInTheDocument();
    expect(screen.getByText('Reviewer: Research reviewer')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /review|pay|send/i })).not.toBeInTheDocument();
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
    expect(screen.getAllByText('Unverified')).toHaveLength(2);
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
