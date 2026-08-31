import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BALANCE_PORTAL_URL, dollarsToCents, submitBalanceEvidence, type BalanceInput } from './compliance-balance-api';
const clear = vi.fn();
vi.mock('./auth', () => ({ getAuthHeaders: () => ({ Authorization: 'Bearer test-only' }), clearToken: () => clear() }));
const fetchMock = vi.fn();
const input: BalanceInput = { bin: '3348179', amount_cents: 500000, category: 'LL152', scope: 'bin_category', source_url: BALANCE_PORTAL_URL, source_updated_at: null, source_timestamp_raw: 'No date shown', observed_at: '2026-08-28T12:00:00.000Z', evidence_note: 'Read category total in official source.' };
describe('balance capture transport', () => {
  beforeEach(() => { vi.clearAllMocks(); vi.stubGlobal('fetch', fetchMock); });
  afterEach(() => vi.unstubAllGlobals());
  it.each([['0', 0], ['5000.01', 500001], [' 1.2 ', 120], ['10000000000', 1000000000000]])('parses %s as integer cents', (value, expected) => expect(dollarsToCents(value)).toBe(expected));
  it.each(['', '1e3', '-1', '1.001', '$5,000', '10000000001', 'NaN'])('rejects %s', value => expect(dollarsToCents(value)).toBeNull());
  it('previews without capture and accepts equivalent ISO timestamps', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ dry_run: true, writes: 0, evidence: { ...input, observed_at: '2026-08-28T12:00:00Z', id: 'abc', reviewer: 'u1' } })));
    await expect(submitBalanceEvidence(input)).resolves.toMatchObject({ writes: 0 });
    expect(fetchMock.mock.calls[0][0]).toMatch(/balance-evidence\/preview$/);
  });
  it('captures only through explicit confirmation route', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ created: false, evidence: { ...input, id: 'abc', reviewer: 'u1' } })));
    await expect(submitBalanceEvidence(input, true)).resolves.toMatchObject({ created: false });
    expect(fetchMock.mock.calls[0][0]).toMatch(/confirm_execute=true$/);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(input);
  });
  it('rejects mismatched evidence and never retries writes automatically', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ created: true, evidence: { ...input, amount_cents: 0, id: 'abc', reviewer: 'u1' } })));
    await expect(submitBalanceEvidence(input, true)).rejects.toThrow('could not be verified');
    expect(fetchMock).toHaveBeenCalledOnce();
  });
  it('retains admin authorization failures', async () => {
    fetchMock.mockResolvedValue(new Response('{}', { status: 403 }));
    await expect(submitBalanceEvidence(input)).rejects.toMatchObject({ status: 403 });
  });
});
