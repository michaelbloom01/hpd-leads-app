import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import LeadDetail from './LeadDetail';

const fetchLeadMock = vi.fn();
const fetchLeadLineageMock = vi.fn();
const updateLeadMock = vi.fn();
const addOutreachAttemptMock = vi.fn();
const enrichLeadAllMock = vi.fn();
const estimateLeadRevenueMock = vi.fn();
const fetchLeadContactsMock = vi.fn();
const downloadPortfolioContactsWorkbookMock = vi.fn();
const fetchBuildingsMock = vi.fn();
const addBuildingToPipelineMock = vi.fn();
const fetchLeadTruthSummaryMock = vi.fn();

vi.mock('react-hot-toast', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
}));

vi.mock('./PortfolioMap', () => ({
  default: () => <div data-testid="portfolio-map" />,
}));

vi.mock('../services/api', () => ({
  fetchLead: (...args: unknown[]) => fetchLeadMock(...args),
  fetchLeadLineage: (...args: unknown[]) => fetchLeadLineageMock(...args),
  updateLead: (...args: unknown[]) => updateLeadMock(...args),
  addOutreachAttempt: (...args: unknown[]) => addOutreachAttemptMock(...args),
  enrichLeadAll: (...args: unknown[]) => enrichLeadAllMock(...args),
  estimateLeadRevenue: (...args: unknown[]) => estimateLeadRevenueMock(...args),
  fetchLeadContacts: (...args: unknown[]) => fetchLeadContactsMock(...args),
  downloadPortfolioContactsWorkbook: (...args: unknown[]) => downloadPortfolioContactsWorkbookMock(...args),
}));

vi.mock('../services/buildings-api', () => ({
  addBuildingToPipeline: (...args: unknown[]) => addBuildingToPipelineMock(...args),
  fetchBuildings: (...args: unknown[]) => fetchBuildingsMock(...args),
}));

vi.mock('../services/truth-api', () => ({
  fetchLeadTruthSummary: (...args: unknown[]) => fetchLeadTruthSummaryMock(...args),
}));

const lead = {
  lead_id: 'lead-1',
  agent_name: 'Acme Management',
  company_name: 'Acme Management LLC',
  primary_contact: 'Alex Manager',
  email: 'alex@example.com',
  phone: '212-555-0100',
  website: 'https://example.com',
  owner_name: '',
  owner_type: 'corporation',
  portfolio_size: 12,
  total_units: 144,
  score: 88,
  boro: 'MANHATTAN',
  boros: ['MANHATTAN'],
  pipeline_stage: 'research',
  enrichment_status: 'partial',
  outreach_status: 'new',
  estimated_annual_revenue: 144000,
  violation_count: 2,
  violation_class_a: 1,
  violation_class_b: 1,
  violation_class_c: 0,
  violations_per_unit: 0.2,
  entity_type: 'company',
  outreach_attempts: [],
  buildings: [],
  revenue_breakdown: [],
} as const;

function renderLeadDetail() {
  return render(
    <MemoryRouter>
      <LeadDetail lead={lead as any} onClose={vi.fn()} onLeadUpdated={vi.fn()} />
    </MemoryRouter>,
  );
}

describe('LeadDetail outreach evidence safety', () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    fetchLeadMock.mockResolvedValue(lead);
    fetchLeadLineageMock.mockResolvedValue({
      lead_id: 'lead-1',
      canonical_entity: null,
      sibling_count: 0,
      sibling_leads: [],
    });
    fetchLeadTruthSummaryMock.mockResolvedValue(null);
    fetchBuildingsMock.mockResolvedValue({ buildings: [] });
    fetchLeadContactsMock.mockResolvedValue({ buildings: [] });
    downloadPortfolioContactsWorkbookMock.mockResolvedValue({
      blob: new Blob(['workbook'], {
        type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      }),
      filename: 'double_edge_acme_contacts.xlsx',
    });
    updateLeadMock.mockResolvedValue(lead);
    addOutreachAttemptMock.mockResolvedValue({ status: 'ok', attempt: {} });
    enrichLeadAllMock.mockResolvedValue({ status: 'approval_required', message: 'Preview only' });
    estimateLeadRevenueMock.mockResolvedValue(lead);
  });

  it('opens email templates without recording false sent-email outreach evidence', async () => {
    renderLeadDetail();

    fireEvent.click(await screen.findByRole('button', { name: /email/i }));
    const introTemplate = await screen.findByRole('link', { name: 'Intro Template' });

    expect(introTemplate).toHaveAttribute('href', expect.stringContaining('mailto:alex@example.com'));
    fireEvent.click(introTemplate);

    await waitFor(() => {
      expect(screen.queryByRole('link', { name: 'Intro Template' })).not.toBeInTheDocument();
    });
    expect(addOutreachAttemptMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /email/i }));
    fireEvent.click(await screen.findByRole('link', { name: 'Follow-Up Template' }));

    await waitFor(() => {
      expect(screen.queryByRole('link', { name: 'Follow-Up Template' })).not.toBeInTheDocument();
    });
    expect(addOutreachAttemptMock).not.toHaveBeenCalled();
  });

  it('downloads portfolio contacts without recording outreach evidence', async () => {
    const createObjectURL = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:portfolio-contacts');
    const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);

    renderLeadDetail();

    fireEvent.click(await screen.findByRole('button', { name: /export contacts/i }));

    await waitFor(() => {
      expect(downloadPortfolioContactsWorkbookMock).toHaveBeenCalledWith('Acme Management LLC', 'lead-1');
    });
    expect(createObjectURL).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:portfolio-contacts');
    expect(click).toHaveBeenCalled();
    expect(addOutreachAttemptMock).not.toHaveBeenCalled();

    createObjectURL.mockRestore();
    revokeObjectURL.mockRestore();
    click.mockRestore();
  });
});
