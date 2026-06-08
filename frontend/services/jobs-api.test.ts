import { beforeEach, describe, expect, it, vi } from 'vitest';

import { setToken, clearToken } from './auth';
import { fetchJobsSummary, startJob } from './jobs-api';

describe('jobs-api', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    setToken('test-token');
  });

  it('calls startJob with auth header in approval-preview mode by default', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ status: 'approval_required', job_type: 'energy' }), { status: 200 }),
    );

    const result = await startJob('energy');

    expect(result.status).toBe('approval_required');
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/jobs/energy/start', {
      method: 'POST',
      headers: { Authorization: 'Bearer test-token' },
    });
  });

  it('adds explicit execute parameters when approval has been granted', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ status: 'queued', job_type: 'energy', dry_run: false, confirm_execute: true }), { status: 200 }),
    );

    const result = await startJob('energy', { limit: 250, dryRun: false, confirmExecute: true });

    expect(result.confirm_execute).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/jobs/energy/start?limit=250&dry_run=false&confirm_execute=true', {
      method: 'POST',
      headers: { Authorization: 'Bearer test-token' },
    });
  });

  it('fetches jobs summary', async () => {
    const payload = {
      queued_count: 1,
      running_count: 2,
      succeeded_24h: 10,
      failed_24h: 1,
      avg_duration_seconds_24h: 12.5,
    };
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(payload), { status: 200 }),
    );

    const result = await fetchJobsSummary();

    expect(result).toEqual(payload);
  });

  it('clears token and emits logout on 401', async () => {
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent');
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(null, { status: 401, statusText: 'Unauthorized' }),
    );

    await expect(fetchJobsSummary()).rejects.toThrow('Session expired');
    expect(localStorage.getItem('hpd_auth_token')).toBeNull();
    expect(dispatchSpy).toHaveBeenCalled();
    clearToken();
  });
});

