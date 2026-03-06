import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import LeadKanban from './leads/LeadKanban';
import type { ApiLead } from '../services/api';

const baseLead = {
  lead_id: 'abc123',
  agent_name: 'Test Agent',
  owner_name: 'Test Owner',
  owner_type: 'corporation',
  company_name: 'Test Company LLC',
  portfolio_size: 12,
  total_units: 220,
  score: 78,
  boro: 'MANHATTAN',
  boros: ['MANHATTAN'],
  pipeline_stage: 'first_contact',
  enrichment_status: 'partial',
  outreach_status: 'new',
  estimated_annual_revenue: 100000,
} as unknown as ApiLead;

describe('LeadKanban', () => {
  it('renders lead under matching pipeline stage', () => {
    render(<LeadKanban leads={[baseLead]} onSelectLead={vi.fn()} />);
    expect(screen.getByText('First Contact')).toBeInTheDocument();
    expect(screen.getByText('Test Company LLC')).toBeInTheDocument();
  });

  it('falls back to lead id when display fields are blank', () => {
    const blankLead = {
      ...baseLead,
      lead_id: 'lead-fallback-1',
      company_name: '   ',
      agent_name: ' ',
      owner_name: '',
      primary_contact: '   ',
      address: null,
    } as unknown as ApiLead;

    render(<LeadKanban leads={[blankLead]} onSelectLead={vi.fn()} />);
    expect(screen.getByText('lead-fallback-1')).toBeInTheDocument();
  });
});
