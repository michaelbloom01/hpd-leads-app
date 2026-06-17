import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import LeadTable from './LeadTable';

const fetchLeadsMock = vi.fn();
const checkHealthMock = vi.fn();
const searchBuildingsMock = vi.fn();

vi.mock('../services/auth', () => ({
  getAuthHeaders: () => ({}),
}));

vi.mock('../services/api', () => ({
  API_BASE_URL: 'https://example.test',
  fetchLeads: (...args: unknown[]) => fetchLeadsMock(...args),
  startSelectedLeadsEnrichment: vi.fn(),
  updateLeadPipelineStages: vi.fn(),
  checkHealth: (...args: unknown[]) => checkHealthMock(...args),
  searchBuildings: (...args: unknown[]) => searchBuildingsMock(...args),
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
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    checkHealthMock.mockResolvedValue({ status: 'ok' });
    fetchLeadsMock.mockResolvedValue({
      leads: [lead],
      total: 1,
      offset: 0,
      limit: 50,
    });
    searchBuildingsMock.mockResolvedValue({
      query: '110 EAST 55TH ST',
      total: 1,
      buildings: [{
        building_id: 'lead:lead-1',
        bbl: null,
        address: '110 EAST 55TH ST',
        canonical_address: null,
        house_number: '110',
        street_name: 'EAST 55TH ST',
        boro: 'MANHATTAN',
        units_res: 120,
        building_class: '',
        building_type: null,
        lead_id: 'lead-1',
        lead_name: 'Acme Management LLC',
        agent_name: 'Acme Management LLC',
        owner_name: '',
        score: 88,
        portfolio_size: 12,
        total_units: 120,
        status: 'lead_address',
      }],
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

  it('renders kanban columns when view=kanban is in the URL', async () => {
    render(
      <MemoryRouter initialEntries={['/leads?view=kanban']}>
        <LeadTable onSelectLead={vi.fn()} />
      </MemoryRouter>,
    );

    await screen.findByText('Research');

    expect(screen.getByText('First Contact')).toBeInTheDocument();
    expect(screen.getByText('Due Diligence')).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: 'Acme Management LLC' }).length).toBeGreaterThan(0);
  });

  it('hydrates address lookup results from URL filters', async () => {
    render(
      <MemoryRouter initialEntries={['/leads?mode=address&q=110%20EAST%2055TH%20ST']}>
        <LeadTable onSelectLead={vi.fn()} />
      </MemoryRouter>,
    );

    await screen.findByText('Address Results (1)');

    expect(searchBuildingsMock).toHaveBeenCalledWith('110 EAST 55TH ST');
    expect(screen.getByText('MANHATTAN - lead address record')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Open building' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Open lead (score: 88.0)' })).toBeInTheDocument();
  });

  it('shows canonical address context for matched aliases', async () => {
    searchBuildingsMock.mockResolvedValueOnce({
      query: '10 HANOVER SQUARE',
      total: 1,
      buildings: [{
        building_id: '1000310001',
        bbl: '1000310001',
        address: '10 HANOVER SQUARE',
        canonical_address: '4 HANOVER SQUARE',
        house_number: '10',
        street_name: 'HANOVER SQUARE',
        boro: 'MANHATTAN',
        units_res: 493,
        building_class: 'D6',
        building_type: null,
        lead_id: null,
        agent_name: '',
        owner_name: '',
        score: null,
        portfolio_size: null,
        total_units: null,
        status: 'unlinked',
      }],
    });

    render(
      <MemoryRouter initialEntries={['/leads?mode=address&q=10%20HANOVER%20SQUARE']}>
        <LeadTable onSelectLead={vi.fn()} />
      </MemoryRouter>,
    );

    await screen.findByText('10 HANOVER SQUARE');
    expect(screen.getByText('Canonical record: 4 HANOVER SQUARE')).toBeInTheDocument();
  });
});
