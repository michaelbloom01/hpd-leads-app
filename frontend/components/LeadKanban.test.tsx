import { fireEvent, render, screen } from '@testing-library/react';
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

  it('renders selection controls and stage actions when bulk-selection props are provided', () => {
    const onToggleVisibleSelect = vi.fn();
    const onToggleStageSelect = vi.fn();
    const onClearSelection = vi.fn();

    render(
      <LeadKanban
        leads={[baseLead]}
        onSelectLead={vi.fn()}
        selectedLeadIds={new Set([baseLead.lead_id])}
        onToggleSelect={vi.fn()}
        onToggleVisibleSelect={onToggleVisibleSelect}
        areAllVisibleSelected={true}
        onToggleStageSelect={onToggleStageSelect}
        isStageFullySelected={(stage) => stage === 'first_contact'}
        onClearSelection={onClearSelection}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: 'Clear Visible' }));
    fireEvent.click(screen.getByRole('button', { name: 'Clear Selection' }));
    fireEvent.click(screen.getByRole('button', { name: 'Clear Stage' }));

    expect(onToggleVisibleSelect).toHaveBeenCalledTimes(1);
    expect(onClearSelection).toHaveBeenCalledTimes(1);
    expect(onToggleStageSelect).toHaveBeenCalledWith('first_contact');
  });
});
