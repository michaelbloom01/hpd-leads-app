import { beforeEach, describe, expect, it, vi } from 'vitest';

import { setToken } from './auth';
import {
  createSmartList,
  enrichLeadAll,
  getEnrichmentProgress,
  getSmartLists,
  refreshPipeline,
  startBatchEnrichment,
  startSelectedLeadsEnrichment,
} from './api';

describe('api service', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    setToken('test-token');
  });

  it('fetches enrichment progress from the canonical slash jobs route', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200 }),
    );

    const result = await getEnrichmentProgress();

    expect(result.running).toBe(false);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/jobs/?job_type=enrichment&limit=1',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer test-token' }),
      }),
    );
  });

  it('describes pipeline refresh approval preview without claiming the job queued', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ status: 'approval_required', approval_required: true }), { status: 200 }),
    );

    const result = await refreshPipeline(true);

    expect(result.status).toBe('approval_required');
    expect(result.approval_required).toBe(true);
    expect(result.message).toMatch(/check ready/i);
    expect(result.message).toMatch(/No refresh was queued/i);
    expect(result.message).toMatch(/requires explicit approval/i);
  });

  it('describes batch enrichment approval preview without claiming the job queued', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ status: 'approval_required', approval_required: true }), { status: 200 }),
    );

    const result = await startBatchEnrichment(500);

    expect(result.status).toBe('approval_required');
    expect(result.approval_required).toBe(true);
    expect(result.message).toMatch(/check ready/i);
    expect(result.message).toMatch(/No enrichment was queued/i);
    expect(result.message).not.toMatch(/^Queued/i);
  });

  it('describes selected enrichment approval preview without claiming the job queued', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        status: 'approval_required',
        job_id: null,
        target_count: 2,
        missing_lead_ids: [],
        approval_required: true,
      }), { status: 200 }),
    );

    const result = await startSelectedLeadsEnrichment(['lead-1', 'lead-2']);

    expect(result.status).toBe('approval_required');
    expect(result.job_id).toBeNull();
    expect(result.approval_required).toBe(true);
    expect(result.message).toMatch(/check ready/i);
    expect(result.message).toMatch(/No enrichment was queued/i);
    expect(result.message).not.toMatch(/^Queued/i);
  });

  it('describes single-lead enrichment approval preview without claiming execution', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        status: 'approval_required',
        lead_id: 'lead-1',
        contacts: { phones_found: 0, emails_found: 0, website_found: false },
        research: { owner_names: [], year_established: null, website_scraped: false },
        ai_summary: { generated: false, description: null },
        errors: [],
        enrichment_status: 'none',
        pipeline_stage: 'research',
        lead: { lead_id: 'lead-1' },
        approval_required: true,
      }), { status: 200 }),
    );

    const result = await enrichLeadAll('lead-1');

    expect(result.status).toBe('approval_required');
    expect(result.approval_required).toBe(true);
    expect(result.message).toMatch(/check ready/i);
    expect(result.message).toMatch(/No enrichment was queued/i);
    expect(result.message).not.toMatch(/^Enrichment complete/i);
  });

  it('preserves schema-not-ready details for smart list reads', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        detail: {
          status: 'schema_not_ready',
          message: 'Smart Lists schema is not ready; run Alembic migrations or explicit startup repair.',
        },
      }), {
        status: 503,
        statusText: 'Service Unavailable',
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    await expect(getSmartLists()).rejects.toThrow(
      'Smart Lists schema is not ready; run Alembic migrations or explicit startup repair.',
    );
  });

  it('preserves schema-not-ready details for smart list mutations', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        detail: {
          status: 'schema_not_ready',
          message: 'Smart Lists schema is not ready; run Alembic migrations or explicit startup repair.',
        },
      }), {
        status: 503,
        statusText: 'Service Unavailable',
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    await expect(createSmartList({ name: 'Needs schema', filters: {} })).rejects.toThrow(
      'Smart Lists schema is not ready; run Alembic migrations or explicit startup repair.',
    );
  });
});
