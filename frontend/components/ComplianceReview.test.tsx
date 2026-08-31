import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ComplianceReview from './ComplianceReview';
import { ComplianceApiError } from '../services/compliance-api';
import type { ReviewHistory, ReviewState } from '../services/compliance-workflow-api';

const fetchReviewsMock = vi.fn();
const saveReviewMock = vi.fn();
vi.mock('../services/compliance-workflow-api', async importOriginal => ({
  ...await importOriginal<typeof import('../services/compliance-workflow-api')>(),
  fetchComplianceReviews: (...args: unknown[]) => fetchReviewsMock(...args),
  saveComplianceReview: (...args: unknown[]) => saveReviewMock(...args),
}));

const RECORD_ID = 'a'.repeat(32);
const QUERY_KEY = ['compliance-review', RECORD_ID];
const clients: QueryClient[] = [];

function history(version = 3, state: ReviewState = 'in_review'): ReviewHistory {
  return {
    record_id: RECORD_ID,
    source_record_key: 'SYNTHETIC-COMPLAINT-1',
    agency_status: 'ACTIVE',
    state,
    version,
    notice: 'Internal review leaves agency status unchanged.',
    history_limit: 50,
    history: version ? [{
      id: `synthetic-review-${version}`, version, state,
      reason: 'Synthetic prior review evidence.',
      actor: 'reviewer@example.test', created_at: '2026-08-31T12:00:00Z',
    }] : [],
  };
}

function renderReview() {
  const client = new QueryClient({ defaultOptions: {
    queries: { retry: false, gcTime: 0 },
    // A component-level retry:false must override permissive app defaults.
    mutations: { retry: 3, retryDelay: 0 },
  } });
  clients.push(client);
  render(<QueryClientProvider client={client}><ComplianceReview recordId={RECORD_ID} /></QueryClientProvider>);
  return client;
}

async function openReview() {
  fireEvent.click(screen.getByRole('button', { name: 'Internal review' }));
  const state = await screen.findByRole('combobox', { name: 'Internal state' });
  await waitFor(() => expect(state).toHaveValue('in_review'));
  return state;
}

function enterDraft(state: ReviewState, reason: string) {
  fireEvent.change(screen.getByRole('combobox', { name: 'Internal state' }), { target: { value: state } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Reason and evidence reference' }), { target: { value: reason } });
}

describe('ComplianceReview internal-only decisions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchReviewsMock.mockReset().mockResolvedValue(history());
    saveReviewMock.mockReset();
  });

  afterEach(() => {
    cleanup();
    clients.splice(0).forEach(client => client.clear());
  });

  it('loads history only after the accessible review control is opened', async () => {
    renderReview();
    expect(screen.getByRole('button', { name: 'Internal review' })).toHaveAttribute('aria-expanded', 'false');
    expect(fetchReviewsMock).not.toHaveBeenCalled();
    expect(saveReviewMock).not.toHaveBeenCalled();
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();

    await openReview();
    expect(fetchReviewsMock).toHaveBeenCalledTimes(1);
    expect(fetchReviewsMock.mock.calls[0][0]).toBe(RECORD_ID);
    expect(fetchReviewsMock.mock.calls[0][1]).toHaveProperty('aborted', false);
    expect(screen.getByRole('button', { name: 'Internal review' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('button', { name: 'Save internal review' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Internal review' }));
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(saveReviewMock).not.toHaveBeenCalled();
  });

  it('saves only explicit state and trimmed evidence with the draft base version', async () => {
    saveReviewMock.mockResolvedValue(history(4, 'verified_for_briefing'));
    const client = renderReview();
    await openReview();
    enterDraft('verified_for_briefing', '  Checked the dated agency source.  ');
    fireEvent.click(screen.getByRole('button', { name: 'Save internal review' }));

    await screen.findByText('Internal review saved. Agency status is unchanged.');
    expect(saveReviewMock).toHaveBeenCalledExactlyOnceWith(RECORD_ID, {
      state: 'verified_for_briefing', reason: 'Checked the dated agency source.', expected_version: 3,
    });
    expect(screen.getByRole('textbox', { name: 'Reason and evidence reference' })).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Save internal review' })).toBeDisabled();
    expect(client.getQueryData<ReviewHistory>(QUERY_KEY)).toMatchObject({ version: 4, agency_status: 'ACTIVE' });
  });

  it('never automatically retries a failed save even when mutation defaults allow retries', async () => {
    saveReviewMock.mockRejectedValue(new Error('Synthetic save unavailable. Reload history before trying again.'));
    renderReview();
    await openReview();
    enterDraft('monitoring', 'Keep watching the dated agency source.');
    fireEvent.click(screen.getByRole('button', { name: 'Save internal review' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Synthetic save unavailable.');
    expect(saveReviewMock).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('Internal review saved. Agency status is unchanged.')).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Reason and evidence reference' })).toHaveValue('Keep watching the dated agency source.');
  });

  it('surfaces a server conflict and uses the new version only after explicit reload', async () => {
    const conflictMessage = 'Another reviewer updated this record. Reload review history, then check your change before saving.';
    saveReviewMock.mockRejectedValueOnce(new ComplianceApiError(conflictMessage, 409))
      .mockResolvedValueOnce(history(5, 'verified_for_briefing'));
    renderReview();
    await openReview();
    enterDraft('verified_for_briefing', 'Checked the source before the concurrent change.');
    fireEvent.click(screen.getByRole('button', { name: 'Save internal review' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(conflictMessage);
    expect(saveReviewMock).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('combobox', { name: 'Internal state' })).toHaveValue('verified_for_briefing');
    expect(screen.getByRole('textbox', { name: 'Reason and evidence reference' })).toHaveValue('Checked the source before the concurrent change.');
    expect(screen.queryByText('Internal review saved. Agency status is unchanged.')).not.toBeInTheDocument();

    fetchReviewsMock.mockResolvedValue(history(4, 'monitoring'));
    fireEvent.click(screen.getByRole('button', { name: 'Reload history and reset draft' }));
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Internal state' })).toHaveValue('monitoring'));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Reason and evidence reference' })).toHaveValue('');
    enterDraft('verified_for_briefing', 'Rechecked source and the newer internal review.');
    fireEvent.click(screen.getByRole('button', { name: 'Save internal review' }));
    await screen.findByText('Internal review saved. Agency status is unchanged.');
    expect(saveReviewMock).toHaveBeenLastCalledWith(RECORD_ID, {
      state: 'verified_for_briefing', reason: 'Rechecked source and the newer internal review.', expected_version: 4,
    });
  });

  it('preserves a dirty draft on background cache updates and blocks saving until explicit reset', async () => {
    saveReviewMock.mockResolvedValue(history(5, 'closed_internally'));
    const client = renderReview();
    await openReview();
    const draftReason = 'Local review notes that must survive a background refresh.';
    enterDraft('closed_internally', draftReason);

    act(() => { client.setQueryData(QUERY_KEY, history(4, 'monitoring')); });
    expect(await screen.findByRole('alert')).toHaveTextContent('A newer review arrived while you were drafting.');
    expect(screen.getByRole('combobox', { name: 'Internal state' })).toHaveValue('closed_internally');
    expect(screen.getByRole('textbox', { name: 'Reason and evidence reference' })).toHaveValue(draftReason);
    expect(screen.getByRole('button', { name: 'Save internal review' })).toBeDisabled();
    fireEvent.submit(screen.getByRole('textbox', { name: 'Reason and evidence reference' }).closest('form')!);
    expect(saveReviewMock).not.toHaveBeenCalled();

    fetchReviewsMock.mockResolvedValue(history(4, 'monitoring'));
    fireEvent.click(screen.getByRole('button', { name: 'Reload history and reset draft' }));
    await waitFor(() => expect(fetchReviewsMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Internal state' })).toHaveValue('monitoring'));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Reason and evidence reference' })).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Save internal review' })).toBeDisabled();

    enterDraft('closed_internally', 'Reviewed the newer history before closing internally.');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save internal review' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Save internal review' }));
    await screen.findByText('Internal review saved. Agency status is unchanged.');
    expect(saveReviewMock).toHaveBeenCalledExactlyOnceWith(RECORD_ID, {
      state: 'closed_internally', reason: 'Reviewed the newer history before closing internally.', expected_version: 4,
    });
  });
});
