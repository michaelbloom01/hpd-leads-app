import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import BuildingDetailPage from './BuildingDetailPage';
const fetchComplianceMock = vi.fn();

vi.mock('../services/compliance-api', async importOriginal => ({
  ...await importOriginal<typeof import('../services/compliance-api')>(),
  fetchCompliance: (...args: unknown[]) => fetchComplianceMock(...args),
}));

const fetchBuildingDetailMock = vi.fn();
const fetchBuildingTimelineMock = vi.fn();
const fetchBuildingScoreHistoryMock = vi.fn();
const addBuildingToPipelineMock = vi.fn();
const fetchBuildingOutreachEventsMock = vi.fn();
const logBuildingOutreachEventMock = vi.fn();
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
}));

vi.mock('../services/truth-api', () => ({
  fetchSubjectTruthSummary: (...args: unknown[]) => fetchSubjectTruthSummaryMock(...args),
}));

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><BuildingDetailPage /></QueryClientProvider>);
}

describe('BuildingDetailPage roles and evidence', () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    fetchComplianceMock.mockResolvedValue({ enabled: false, coverage: { status: 'disabled' }, buildings: [], warnings: [], provenance: [] });
    fetchBuildingDetailMock.mockResolvedValue({
      bbl: '1000000001',
      address: '100 Example Ave',
      borough: 'MANHATTAN',
      unit_count: 42,
      year_built: 1920,
      churn_score: 62.5,
      churn_category: 'warm',
      churn_breakdown: null,
      all_contacts: [
        {
          name: 'Example Manager Inc',
          company_name: 'Example Manager Inc',
          person_name: null,
          role: 'Agent',
          source: 'HPD Registration',
          source_record_id: 'manager-1',
          as_of_date: '2026-08-15',
          address: '10 Manager Street',
          confidence_hint: null,
          is_decision_maker: false,
          source_url: 'https://data.cityofnewyork.us/resource/feu5-w2e2.json?registrationcontactid=manager-1',
        },
        {
          name: 'Tara Manager',
          person_name: 'Tara Manager',
          company_name: null,
          role: 'SiteManager',
          source: 'HPD Registration',
          source_record_id: 'manager-person-1',
          as_of_date: '2026-08-15',
          address: null,
          confidence_hint: null,
          is_decision_maker: false,
          source_url: 'https://data.cityofnewyork.us/resource/feu5-w2e2.json?registrationcontactid=manager-person-1',
        },
        {
          name: 'Alex Boardperson',
          role: 'Officer',
          board_role: 'Board Head',
          board_role_status: 'verified',
          source_title: 'Board President',
          source: 'Property Website',
          source_record_id: 'board-1',
          as_of_date: '2026-08-20',
          address: null,
          confidence_hint: null,
          is_decision_maker: true,
          source_url: 'https://example.com/board',
        },
        {
          name: 'Dana Officer',
          role: 'Officer',
          source: 'NY DOS Snapshot',
          source_record_id: 'dos-1',
          as_of_date: '2026-08-10',
          address: null,
          confidence_hint: null,
          is_decision_maker: true,
          source_url: 'https://apps.dos.ny.gov/publicInquiry/',
        },
      ],
      dos_contacts_status: 'loaded',
      dos_contacts_is_stale: false,
      management_company: 'Example Manager Inc',
      corporate_owner: 'Example Owner LLC',
      latitude: null,
      longitude: null,
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

  it('keeps role evidence and individual conflicts while removing duplicate global judgments', async () => {
    renderPage();
    await screen.findByText('Relationship evidence (1)');
    const roleHeading = screen.getByText('Who runs this building?');
    const evidenceHeading = screen.getByText('Relationship evidence (1)');
    expect(roleHeading.compareDocumentPosition(evidenceHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText('Property manager')).toBeInTheDocument();
    expect(screen.getByText('Management contacts')).toBeInTheDocument();
    expect(screen.getByText('Board people')).toBeInTheDocument();
    expect(screen.getAllByText('Tara Manager').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('Alex Boardperson').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Corporate officer candidate')).toBeInTheDocument();
    expect(screen.getByText('Board role unverified.')).toBeInTheDocument();
    expect(screen.getByText('All source records (4)')).toBeInTheDocument();
    expect(await screen.findByText('Pilot paused')).toBeInTheDocument();
    expect(fetchComplianceMock).toHaveBeenCalledWith('parcels', '1000000001', expect.any(AbortSignal));
    expect(screen.queryByText('82%')).not.toBeInTheDocument();
    expect(screen.queryByText('Outreach-ready')).not.toBeInTheDocument();
    expect(screen.queryByText('Evidence Check')).not.toBeInTheDocument();
    expect(screen.queryByText('Key Evidence')).not.toBeInTheDocument();
    expect(screen.getAllByText('Owner relationship found: Example Owner LLC')).toHaveLength(1);
    expect(screen.getByText('1 relationship with conflicting sources')).toBeInTheDocument();
    expect(screen.getByText(/Supporting sources: ACRIS, HPD registration/i)).toBeInTheDocument();
    expect(screen.getByText(/Conflicting sources: outreach feedback/i)).toBeInTheDocument();
    expect(screen.getByLabelText('Relationship evidence')).not.toHaveAttribute('open');
    expect(screen.queryByText(/exists in building table/i)).not.toBeInTheDocument();

    await waitFor(() => {
      expect(fetchSubjectTruthSummaryMock).toHaveBeenCalledWith('building', '1000000001');
    });
  });

  it('uses both names on the HPD Agent row and keeps an unverified DOS chairman as a candidate', async () => {
    const building = await fetchBuildingDetailMock();
    const agent = {
      ...building.all_contacts[0], name: 'Livingston', company_name: 'Livingston', person_name: 'Tara Dexter',
      source_url: 'https://data.cityofnewyork.us/resource/feu5-w2e2.json?registrationcontactid=14050904',
    };
    fetchBuildingDetailMock.mockResolvedValue({ ...building, management_company: 'Livingston', all_contacts: [
      agent,
      { ...building.all_contacts[1], name: 'Tara Dexter', person_name: 'Tara Dexter' },
      { ...building.all_contacts[3], name: 'DOS Candidate', role: 'DOS Chairman/CEO (Biennial)', board_role: 'Board Head', board_role_status: 'unverified' },
      { ...building.all_contacts[1], name: 'Jeff Pisano', person_name: 'Jeff Pisano', role: 'HeadOfficer' },
    ] });
    renderPage();
    await screen.findByText('Who runs this building?');
    const company = within(screen.getByText('Property manager').closest('section')!);
    const people = within(screen.getByText('Management contacts').closest('section')!);
    const board = within(screen.getByText('Board people').closest('section')!);
    expect(company.getByText('Livingston')).toBeInTheDocument();
    expect(company.queryByText('Tara Dexter')).not.toBeInTheDocument();
    expect(people.getAllByText('Tara Dexter')).toHaveLength(1);
    expect(people.getByText('Listed as Agent with Livingston on the same record.')).toBeInTheDocument();
    expect(people.getByRole('link', { name: 'HPD Registration' })).toHaveAttribute('href', agent.source_url);
    expect(board.getByText('DOS Candidate')).toBeInTheDocument();
    expect(board.getByText('Board Head candidate')).toBeInTheDocument();
    expect(board.getByText('Board role unverified.')).toBeInTheDocument();
    expect(board.queryByText('Board Head', { exact: true })).not.toBeInTheDocument();
    expect(board.queryByText('Jeff Pisano')).not.toBeInTheDocument();
    expect(screen.getByText('Jeff Pisano')).toBeInTheDocument();
  });

  it('preserves linked names and roles without treating their count alone as conflicting evidence', async () => {
    const summary = await fetchSubjectTruthSummaryMock();
    fetchSubjectTruthSummaryMock.mockResolvedValue({ ...summary, claims: [{
      ...summary.claims[0], predicate: 'has_current_management_link', normalized_value: 'Example Manager Inc, Example Owner LLC',
      supporting_sources: ['building_management'], contradicting_sources: ['building_management'],
      contradicting_evidence_count: 3, rationale: { source: 'synthetic_subject_summary', roles: ['Agent', 'CorporateOwner'] },
    }] });
    renderPage();
    await screen.findByText('Relationship evidence (1)');
    expect(screen.getByText('Linked company records')).toBeInTheDocument();
    expect(screen.getByText('Example Manager Inc, Example Owner LLC')).toBeInTheDocument();
    expect(screen.getByText('Recorded roles: Agent, CorporateOwner')).toBeInTheDocument();
    expect(screen.queryByText(/relationship.*with conflicting sources/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Conflicting sources:/)).not.toBeInTheDocument();
    expect(screen.queryByText('Management relationship needs review')).not.toBeInTheDocument();
  });

  it('keeps a person-only Agent out of the company lane and identifies the missing company association', async () => {
    const building = await fetchBuildingDetailMock();
    fetchBuildingDetailMock.mockResolvedValue({ ...building, management_company: 'Tara Dexter', all_contacts: [
      { ...building.all_contacts[0], name: 'Tara Dexter', company_name: null, person_name: 'Tara Dexter' },
    ] });
    renderPage();
    await screen.findByText('Who runs this building?');
    const company = within(screen.getByText('Property manager').closest('section')!);
    const people = within(screen.getByText('Management contacts').closest('section')!);
    expect(company.queryByText('Tara Dexter')).not.toBeInTheDocument();
    expect(company.queryByRole('link')).not.toBeInTheDocument();
    expect(people.getByText('Tara Dexter')).toBeInTheDocument();
    expect(people.getByText('Listed as Agent. Company association unrecorded.')).toBeInTheDocument();
  });

  it('shows source-record companies independently of a mismatched scalar manager value', async () => {
    const building = await fetchBuildingDetailMock();
    fetchBuildingDetailMock.mockResolvedValue({ ...building, management_company: 'Unrelated Company' });
    renderPage();
    await screen.findByText('Who runs this building?');
    const company = within(screen.getByText('Property manager').closest('section')!);
    expect(company.getByText('Example Manager Inc')).toBeInTheDocument();
    expect(company.queryByText('Unrelated Company')).not.toBeInTheDocument();
    expect(company.getByRole('link', { name: 'HPD Registration' })).toHaveAttribute('href', building.all_contacts[0].source_url);
  });

  it('keeps all board candidates visible and requires explicit verification before dropping the qualifier', async () => {
    const building = await fetchBuildingDetailMock();
    fetchBuildingDetailMock.mockResolvedValue({ ...building, all_contacts: Array.from({ length: 6 }, (_, index) => ({
      ...building.all_contacts[2], name: `Board Candidate ${index + 1}`, board_role_status: undefined,
    })) });
    renderPage();
    await screen.findByText('Who runs this building?');
    const board = within(screen.getByText('Board people').closest('section')!);
    expect(board.getByText('Board Candidate 6')).toBeInTheDocument();
    expect(board.getAllByText('Board Head candidate')).toHaveLength(6);
    expect(board.queryByText('Board Head', { exact: true })).not.toBeInTheDocument();
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
