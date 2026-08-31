import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchComplianceReviews, saveComplianceReview } from './compliance-workflow-api';

const clear = vi.fn();
vi.mock('./auth', () => ({ getAuthHeaders: () => ({ Authorization: 'Bearer test-only' }), clearToken: () => clear() }));
const fetchMock = vi.fn();
const recordId = 'a'.repeat(32);
const history = { record_id: recordId, source_record_key: 'VIO-1', agency_status: 'Active', state: 'new', version: 0, notice: 'Internal only', history_limit: 50, history: [] };

describe('internal review transport', () => {
  beforeEach(() => { vi.clearAllMocks(); vi.stubGlobal('fetch', fetchMock); });
  afterEach(() => vi.unstubAllGlobals());
  it('uses existing auth and preserves optimistic concurrency payload', async () => {
    fetchMock.mockImplementation(() => Promise.resolve(new Response(JSON.stringify(history))));
    await fetchComplianceReviews(recordId);
    expect(fetchMock.mock.calls[0][1].method).toBe('GET');
    await saveComplianceReview(recordId, { state: 'in_review', reason: 'Read official source', expected_version: 0 });
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: 'POST', headers: { Authorization: 'Bearer test-only', 'Content-Type': 'application/json' } });
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ state: 'in_review', reason: 'Read official source', expected_version: 0 });
  });
  it.each([409, 403, 404, 503, 422])('reports HTTP %s without retrying a write', async status => {
    fetchMock.mockResolvedValue(new Response('{}', { status }));
    await expect(saveComplianceReview(recordId, { state: 'monitoring', reason: 'Source checked', expected_version: 1 })).rejects.toMatchObject({ status });
    expect(fetchMock).toHaveBeenCalledOnce();
  });
  it('logs out on auth expiration', async () => {
    fetchMock.mockResolvedValue(new Response('{}', { status: 401 }));
    await expect(fetchComplianceReviews(recordId)).rejects.toMatchObject({ status: 401 });
    expect(clear).toHaveBeenCalledOnce();
  });
  it.each([{ state: 'toString' }, { record_id: 'b'.repeat(32) }, { version: -1 }, { history: [{ state: '__proto__' }] }])('rejects malformed successful response %j', async override => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ ...history, ...override })));
    await expect(fetchComplianceReviews(recordId)).rejects.toThrow('could not be verified');
  });
});
