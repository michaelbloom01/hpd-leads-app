import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchCompliance } from './compliance-api';

const clearTokenMock = vi.fn();
vi.mock('./auth', () => ({ getAuthHeaders: () => ({ Authorization: 'Bearer test-only' }), clearToken: () => clearTokenMock() }));
const fetchMock = vi.fn();
const envelope = { enabled: false, identity_ready: false, stale: true, scope: { type: 'parcel', id: '3025217501' },
  as_of: null, source_updated_at: null,
  coverage: { status: 'disabled', physical_building_count: 0, checked_building_count: 0, records_count: 0, active_records_count: 0,
    balance_known_building_count: 0, missing_balance_bin_count: 0 },
  reported_balance_cents: null, estimated_penalty_cents: null, buildings: [], warnings: [], provenance: [] };

describe('compliance API transport', () => {
  beforeEach(() => { vi.clearAllMocks(); vi.stubGlobal('fetch', fetchMock); });
  afterEach(() => { vi.unstubAllGlobals(); });

  it('encodes the scope key and uses existing auth for read-only requests', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify(envelope), { status: 200 }));
    await expect(fetchCompliance('portfolio', 'lead/one')).resolves.toEqual(envelope);
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/api/v1/compliance/portfolio/lead%2Fone'), expect.objectContaining({ headers: { Authorization: 'Bearer test-only' } }));
    expect(fetchMock.mock.calls[0][1].method).toBeUndefined();
  });

  it('rejects malformed successful responses', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ status: 'ok' }), { status: 200 }));
    await expect(fetchCompliance('parcels', '3025217501')).rejects.toThrow('could not be verified');
  });

  it('rejects malformed nested records and unsafe monetary precision', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ ...envelope, reported_balance_cents: Number.MAX_SAFE_INTEGER + 1 }), { status: 200 }));
    await expect(fetchCompliance('parcels', '3025217501')).rejects.toThrow('could not be verified');
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ ...envelope, buildings: [{ bin: '3348179', records: null }] }), { status: 200 }));
    await expect(fetchCompliance('parcels', '3025217501')).rejects.toThrow('could not be verified');
  });

  it('rejects an HTML fallback instead of converting it to empty coverage', async () => {
    fetchMock.mockResolvedValue(new Response('<html>App shell</html>', { status: 200 }));
    await expect(fetchCompliance('parcels', '3025217501')).rejects.toThrow();
  });

  it('handles absent backend rollout without showing an empty successful result', async () => {
    fetchMock.mockResolvedValue(new Response('not found', { status: 404 }));
    await expect(fetchCompliance('parcels', '3025217501')).rejects.toMatchObject({ status: 404, message: 'Compliance data is temporarily unavailable.' });
  });

  it('expires auth through the existing app event', async () => {
    const logout = vi.fn();
    window.addEventListener('auth:logout', logout);
    fetchMock.mockResolvedValue(new Response('unauthorized', { status: 401 }));
    await expect(fetchCompliance('buildings', '3348179')).rejects.toMatchObject({ status: 401 });
    expect(clearTokenMock).toHaveBeenCalledOnce();
    expect(logout).toHaveBeenCalledOnce();
    window.removeEventListener('auth:logout', logout);
  });

  it('forwards query cancellation to the request', async () => {
    const cancellation = new AbortController();
    fetchMock.mockImplementation((_url, options) => new Promise((_, reject) => {
      options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    }));
    const request = fetchCompliance('parcels', '3025217501', cancellation.signal);
    cancellation.abort();
    await expect(request).rejects.toMatchObject({ name: 'AbortError' });
    expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true);
  });
});
