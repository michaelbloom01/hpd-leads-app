import { clearToken, getAuthHeaders } from './auth';
import { API_BASE_URL } from './config';
import { ComplianceApiError } from './compliance-api';

export const REVIEW_STATES = {
  new: 'New / reopened', in_review: 'In review', verified_for_briefing: 'Verified for briefing',
  monitoring: 'Monitoring', closed_internally: 'Closed internally', dismissed: 'Dismissed as irrelevant',
  source_mismatch: 'Source mismatch',
} as const;
export type ReviewState = keyof typeof REVIEW_STATES;
export interface ReviewHistory {
  record_id: string; source_record_key: string; agency_status: string | null;
  state: ReviewState; version: number; notice: string; history_limit: number;
  history: Array<{ id: string; version: number; state: ReviewState; reason: string; actor: string; created_at: string }>;
}

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function validHistory(value: unknown): value is ReviewHistory {
  if (!object(value) || typeof value.record_id !== 'string' || typeof value.source_record_key !== 'string'
    || typeof value.state !== 'string' || !Object.prototype.hasOwnProperty.call(REVIEW_STATES, value.state)
    || !Number.isSafeInteger(value.version) || Number(value.version) < 0
    || !(value.agency_status === null || typeof value.agency_status === 'string')
    || typeof value.notice !== 'string' || !Number.isSafeInteger(value.history_limit)
    || !Array.isArray(value.history)) return false;
  return value.history.every(row => object(row) && typeof row.id === 'string'
    && Number.isSafeInteger(row.version) && Number(row.version) > 0
    && typeof row.state === 'string' && Object.prototype.hasOwnProperty.call(REVIEW_STATES, row.state)
    && ['reason', 'actor', 'created_at'].every(key => typeof row[key] === 'string'));
}

async function reviewRequest(recordId: string, body?: { state: ReviewState; reason: string; expected_version: number }, signal?: AbortSignal): Promise<ReviewHistory> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted) controller.abort();
  const timeout = window.setTimeout(abort, 30_000);
  try {
    const response = await fetch(`${API_BASE_URL}/api/v1/compliance/records/${encodeURIComponent(recordId)}/reviews`, {
      method: body ? 'POST' : 'GET', headers: { ...getAuthHeaders(), ...(body ? { 'Content-Type': 'application/json' } : {}) },
      ...(body ? { body: JSON.stringify(body) } : {}), signal: controller.signal,
    });
    if (response.status === 401) {
      clearToken(); window.dispatchEvent(new Event('auth:logout'));
      throw new ComplianceApiError('Your session expired. Sign in again.', 401);
    }
    if (!response.ok) {
      const messages: Record<number, string> = {
        409: 'Another reviewer updated this record. Reload review history, then check your change before saving.',
        403: 'You do not have access to this review.', 404: 'The compliance record could not be found.',
        503: 'Internal review is awaiting rollout.', 422: 'Check the review state and provide a reason of 5 to 2,000 characters.',
      };
      throw new ComplianceApiError(messages[response.status] || 'Review could not be saved or loaded. Reload history to check its latest state.', response.status);
    }
    const result: unknown = await response.json();
    if (!validHistory(result) || result.record_id !== recordId) throw new ComplianceApiError('The review response could not be verified. Reload history before saving again.');
    return result;
  } finally {
    window.clearTimeout(timeout); signal?.removeEventListener('abort', abort);
  }
}

export const fetchComplianceReviews = (id: string, signal?: AbortSignal) => reviewRequest(id, undefined, signal);
export const saveComplianceReview = (id: string, body: { state: ReviewState; reason: string; expected_version: number }) => reviewRequest(id, body);
