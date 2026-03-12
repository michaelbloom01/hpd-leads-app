import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import SmartListsPage from './SmartListsPage';

const navigateMock = vi.fn();
const getSmartListsMock = vi.fn();
const createSmartListMock = vi.fn();
const deleteSmartListMock = vi.fn();
const evaluateSmartListMock = vi.fn();
const updateSmartListMock = vi.fn();
const runDueAutoEvaluationsMock = vi.fn();

vi.mock('react-router-dom', () => ({
  useNavigate: () => navigateMock,
}));

vi.mock('react-hot-toast', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock('../services/api', () => ({
  getSmartLists: (...args: unknown[]) => getSmartListsMock(...args),
  createSmartList: (...args: unknown[]) => createSmartListMock(...args),
  deleteSmartList: (...args: unknown[]) => deleteSmartListMock(...args),
  evaluateSmartList: (...args: unknown[]) => evaluateSmartListMock(...args),
  updateSmartList: (...args: unknown[]) => updateSmartListMock(...args),
  runDueAutoEvaluations: (...args: unknown[]) => runDueAutoEvaluationsMock(...args),
}));

describe('SmartListsPage', () => {
  beforeEach(() => {
    navigateMock.mockReset();
    getSmartListsMock.mockReset();
    createSmartListMock.mockReset();
    deleteSmartListMock.mockReset();
    evaluateSmartListMock.mockReset();
    updateSmartListMock.mockReset();
    runDueAutoEvaluationsMock.mockReset();
    getSmartListsMock.mockResolvedValue({ smart_lists: [] });
    createSmartListMock.mockResolvedValue({ id: 'list-1', name: 'High Value Manhattan', status: 'created' });
    deleteSmartListMock.mockResolvedValue({ id: 'list-1', status: 'deleted' });
    evaluateSmartListMock.mockResolvedValue({ entered: 0, exited: 0, total: 0 });
    updateSmartListMock.mockResolvedValue({ id: 'list-1', status: 'updated' });
    runDueAutoEvaluationsMock.mockResolvedValue({ status: 'queued', evaluated_count: 0, results: [] });
  });

  it('creates a smart list with authored filters', async () => {
    render(<SmartListsPage />);

    expect(await screen.findByText('No Smart Lists yet')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '+ New Smart List' }));
    fireEvent.change(screen.getByPlaceholderText('List name (e.g., Bronx 100+ units)'), {
      target: { value: 'High Value Manhattan' },
    });
    fireEvent.change(screen.getByPlaceholderText('Company or owner name'), {
      target: { value: 'acme' },
    });
    fireEvent.change(screen.getByPlaceholderText('Min score'), {
      target: { value: '80' },
    });
    fireEvent.change(screen.getByPlaceholderText('Min units'), {
      target: { value: '100' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'MANHATTAN' }));
    fireEvent.click(screen.getByRole('button', { name: 'First Contact' }));
    fireEvent.click(screen.getByRole('button', { name: 'Company' }));
    fireEvent.click(screen.getByLabelText('Has phone'));

    expect(screen.getByText('Search: acme')).toBeInTheDocument();
    expect(screen.getByText('Boroughs: MANHATTAN')).toBeInTheDocument();
    expect(screen.getByText('Min score: 80')).toBeInTheDocument();
    expect(screen.getByText('Min units: 100')).toBeInTheDocument();
    expect(screen.getByText('Stages: first_contact')).toBeInTheDocument();
    expect(screen.getByText('Entities: company')).toBeInTheDocument();
    expect(screen.getAllByText('Has phone')).toHaveLength(2);

    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => {
      expect(createSmartListMock).toHaveBeenCalledWith({
        name: 'High Value Manhattan',
        description: '',
        filters: {
          search: 'acme',
          boroughs: ['MANHATTAN'],
          min_score: 80,
          min_units: 100,
          pipeline_stages: ['first_contact'],
          entity_types: ['company'],
          has_phone: true,
        },
        pinned: false,
      });
    });
  }, 15000);

  it('opens a saved smart list in the leads page with translated query params', async () => {
    getSmartListsMock.mockResolvedValue({
      smart_lists: [
        {
          id: 'list-1',
          name: 'Pinned Manhattan',
          description: '',
          filters: {
            boroughs: ['MANHATTAN'],
            neighborhood: 'Midtown',
            min_score: 80,
            max_score: 95,
            min_portfolio: 5,
            min_units: 100,
            max_units: 300,
            min_units_per_bldg: 10,
            max_units_per_bldg: 40,
            entity_types: ['company'],
            pipeline_stages: ['first_contact'],
            outreach_statuses: ['new'],
            enrichment_statuses: ['partial'],
            building_types: ['condo'],
            has_phone: true,
            has_website: true,
            search: 'acme',
          },
          pinned: true,
          auto_evaluate: false,
          evaluation_interval_hours: 24,
          next_evaluation_at: null,
          last_evaluated_at: null,
          last_count: 12,
          created_at: '2026-03-12T00:00:00Z',
          updated_at: '2026-03-12T00:00:00Z',
        },
      ],
    });

    render(<SmartListsPage />);

    expect(await screen.findByText('Pinned Manhattan')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'View' }));

    expect(navigateMock).toHaveBeenCalledWith(
      '/leads?boro=MANHATTAN&neighborhood=Midtown&min_score=80&max_score=95&min_portfolio=5&min_units=100&max_units=300&min_upb=10&max_upb=40&entity=company&outreach=new&stage=first_contact&enrichment=partial&type=condo&phone=1&website=1&q=acme',
    );
  });
});
