import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import LeadTable from './LeadTable';

const fetchLeadsMock = vi.fn();
const checkHealthMock = vi.fn();

vi.mock('../services/auth', () => ({
  getAuthHeaders: () => ({}),
}));

vi.mock('../services/api', () => ({
  API_BASE_URL: 'https://example.test',
  fetchLeads: (...args: unknown[]) => fetchLeadsMock(...args),
  startSelectedLeadsEnrichment: vi.fn(),
  updateLeadPipelineStages: vi.fn(),
  checkHealth: (...args: unknown[]) => checkHealthMock(...args),
  searchBuildings: vi.fn(),
  createSmartList: vi.fn(),
}));

const lead = {
  lead_id: 'lead-1',
  agent_name: 'Acme Management',
  owner_name: '',
  owner_type: 'corporation',
  company_name: 'Acme Management LLC',
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
} as const;

describe('LeadTable', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    checkHealthMock.mockResolvedValue({ status: 'ok' });
    fetchLeadsMock.mockResolvedValue({
      leads: [lead],
      total: 1,
      offset: 0,
      limit: 50,
    });
  });

  it('preserves current list state in lead links', async () => {
    render(
      <MemoryRouter initialEntries={['/leads?page=2&limit=100&sort=score&dir=desc']}>
        <LeadTable onSelectLead={vi.fn()} />
      </MemoryRouter>,
    );

    await screen.findAllByRole('link', { name: 'Acme Management LLC' });
    const link = screen.getAllByRole('link', { name: 'Acme Management LLC' })[0];
    const href = link.getAttribute('href') || '';

    expect(href).toContain('/leads?');
    expect(href).toContain('page=2');
    expect(href).toContain('limit=100');
    expect(href).toContain('sort=score');
    expect(href).toContain('dir=desc');
    expect(href).toContain('lead=lead-1');
  });

  it('shows and clears table-mode selection', async () => {
    render(
      <MemoryRouter initialEntries={['/leads']}>
        <LeadTable onSelectLead={vi.fn()} />
      </MemoryRouter>,
    );

    await screen.findAllByRole('checkbox');
    const checkbox = screen.getAllByRole('checkbox')[0];
    fireEvent.click(checkbox);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Clear Selection' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Clear Selection' }));

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Clear Selection' })).not.toBeInTheDocument();
    });
  });
});
