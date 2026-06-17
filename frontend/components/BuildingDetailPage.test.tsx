import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import BuildingDetailPage from './BuildingDetailPage';

const fetchBuildingDetailMock = vi.fn();
const fetchBuildingTimelineMock = vi.fn();
const fetchBuildingScoreHistoryMock = vi.fn();
const addBuildingToPipelineMock = vi.fn();
const fetchBuildingOutreachEventsMock = vi.fn();
const logBuildingOutreachEventMock = vi.fn();
const requestBuildingDosContactsRefreshMock = vi.fn();
const fetchSubjectTruthSummaryMock = vi.fn();
const navigateMock = vi.fn();

vi.mock('react-hot-toast', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
}));

vi.mock('react-router-dom', () => ({
  useParams: () => ({ bbl: '1000000001' }),
  useNavigate: () => navigateMock,
}));

vi.mock('./PortfolioMap', () => ({
  default: () => <div data-testid="portfolio-map" />,
}));

vi.mock('../services/buildings-api', () => ({
  fetchBuildingDetail: (...args: unknown[]) => fetchBuildingDetailMock(...args),
  fetchBuildingTimeline: (...args: unknown[]) => fetchBuildingTimelineMock(...args),
  fetchBuildingScoreHistory: (...args: unknown[]) => fetchBuildingScoreHistoryMock(...args),
  addBuildingToPipeline: (...args: unknown[]) => addBuildingToPipelineMock(...args),
  fetchBuildingOutreachEvents: (...args: unknown[]) => fetchBuildingOutreachEventsMock(...args),
  logBuildingOutreachEvent: (...args: unknown[]) => logBuildingOutreachEventMock(...args),
  requestBuildingDosContactsRefresh: (...args: unknown[]) => requestBuildingDosContactsRefreshMock(...args),
}));

vi.mock('../services/truth-api', () => ({
  fetchSubjectTruthSummary: (...args: unknown[]) => fetchSubjectTruthSummaryMock(...args),
}));

describe('BuildingDetailPage truth confidence', () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    fetchBuildingDetailMock.mockResolvedValue({
      bbl: '1000000001',
      address: '100 Example Ave',
      borough: 'MANHATTAN',
      unit_count: 42,
      year_built: 1920,
      churn_score: 62.5,
      churn_category: 'warm',
      churn_breakdown: null,
      all_contacts: [],
      dos_contacts_status: 'loaded',
      dos_contacts_is_stale: false,
      management_company: 'Example Manager Inc',
      corporate_owner: 'Example Owner LLC',
      latitude: null,
      longitude: null,
      known_addresses: [
        {
          display_address: '100 Example Ave',
          source: 'building_record_address',
          confidence_score: 0.75,
          is_primary: true,
        },
        {
          display_address: '100-104 EXAMPLE AVE',
          source: 'hpd_registration_range',
          confidence_score: 0.9,
          is_primary: false,
        },
      ],
    });
    fetchBuildingTimelineMock.mockResolvedValue([]);
    fetchBuildingScoreHistoryMock.mockResolvedValue([]);
    fetchBuildingOutreachEventsMock.mockResolvedValue({ events: [] });
    fetchSubjectTruthSummaryMock.mockResolvedValue({
      subject_type: 'building',
      subject_id: '1000000001',
      overall_confidence_score: 0.82,
      review_bucket: 'conflicting_evidence',
      belief_summary: {
        what_we_believe: ['building 1000000001 has owner: Example Owner LLC (current belief).'],
        why_we_believe: ['has owner: 2 support from acris, hpd_registration; 1 contradiction from outreach_feedback; 82% confidence.'],
        supporting_sources: ['acris', 'hpd_registration'],
        contradicting_sources: ['outreach_feedback'],
        contradiction_count: 1,
        freshness_days: 12,
        safe_actions: ['recommended_outreach'],
      },
      claims: [{
        claim_id: 'claim-1',
        subject_type: 'building',
        subject_id: '1000000001',
        predicate: 'has_owner',
        object_type: 'entity',
        object_id: 'entity-owner',
        normalized_value: 'Example Owner LLC',
        claim_type: 'building_ownership',
        belief_status: 'current_belief',
        confidence_score: 0.82,
        freshness_days: 12,
        actionability_level: 'recommended_outreach',
        supporting_evidence_count: 2,
        contradicting_evidence_count: 1,
        supporting_sources: ['acris', 'hpd_registration'],
        contradicting_sources: ['outreach_feedback'],
      }],
    });
  });

  it('summarizes building truth in user-facing language with the raw ledger collapsed', async () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    render(
      <QueryClientProvider client={client}>
        <BuildingDetailPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText('Evidence Check')).toBeInTheDocument();
    expect((await screen.findAllByText('82%')).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Needs review')).toBeInTheDocument();
    expect(screen.getByText('Outreach-ready')).toBeInTheDocument();
    expect(screen.getAllByText('Owner relationship found: Example Owner LLC').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('Ownership sources conflict. Use as diligence context until reviewed.').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Key Evidence')).toBeInTheDocument();
    expect(screen.getByText('Source details (1 items)')).toBeInTheDocument();
    expect(screen.getByText('2 supporting')).toBeInTheDocument();
    expect(screen.getByText('1 contradicting')).toBeInTheDocument();
    expect(screen.getByText(/Sources: ACRIS, HPD registration, outreach feedback \(conflicts\)/i)).toBeInTheDocument();
    expect(screen.queryByText(/exists in building table/i)).not.toBeInTheDocument();
    expect(screen.getByText('HPD range: 100-104 EXAMPLE AVE')).toBeInTheDocument();

    await waitFor(() => {
      expect(fetchSubjectTruthSummaryMock).toHaveBeenCalledWith('building', '1000000001');
    });
  });

  it('groups zero-signal and unavailable churn factors away from active drivers', async () => {
    fetchBuildingDetailMock.mockResolvedValueOnce({
      bbl: '1000000001',
      address: '100 Example Ave',
      borough: 'MANHATTAN',
      unit_count: 42,
      year_built: 1920,
      churn_score: 3.5,
      churn_category: 'stable',
      churn_breakdown: {
        building_size: { raw: 60, weight: 5, effective_weight: 5.8, contribution: 3.5 },
        dob_permits: { raw: 0, weight: 8, effective_weight: 9.3, contribution: 0 },
        facade_status: { raw: null, weight: 6, effective_weight: 0, contribution: 0 },
      },
      all_contacts: [],
      dos_contacts_status: 'loaded',
      dos_contacts_is_stale: false,
      management_company: 'Example Manager Inc',
      corporate_owner: 'Example Owner LLC',
      latitude: null,
      longitude: null,
    });

    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    render(
      <QueryClientProvider client={client}>
        <BuildingDetailPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText('Building Scale')).toBeInTheDocument();
    expect(screen.getByText('Signal score: 60.0 / 100')).toBeInTheDocument();
    expect(screen.getByText('Checked zero-signal sources (1)')).toBeInTheDocument();
    expect(screen.getByText('Awaiting source data (1)')).toBeInTheDocument();
    expect(screen.getByText('Checked: no churn signal detected.')).toBeInTheDocument();
    expect(screen.getByText('No source data available yet for this signal.')).toBeInTheDocument();
  });
});
