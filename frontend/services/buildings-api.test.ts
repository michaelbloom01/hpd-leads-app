import { beforeEach, describe, expect, it, vi } from 'vitest';

import { setToken } from './auth';
import { createBuildingList, deleteBuildingList, getBuildingLists, updateBuildingList } from './buildings-api';

describe('buildings api service', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    setToken('test-token');
  });

  const schemaNotReadyResponse = () => new Response(JSON.stringify({
    detail: {
      status: 'schema_not_ready',
      message: 'Building Lists schema is not ready; run Alembic migrations or explicit startup repair.',
    },
  }), {
    status: 503,
    statusText: 'Service Unavailable',
    headers: { 'Content-Type': 'application/json' },
  });

  it('preserves schema-not-ready details for building list reads', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(schemaNotReadyResponse());

    await expect(getBuildingLists()).rejects.toThrow(
      'Building Lists schema is not ready; run Alembic migrations or explicit startup repair.',
    );
  });

  it('preserves schema-not-ready details for building list creates', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(schemaNotReadyResponse());

    await expect(createBuildingList('Needs schema')).rejects.toThrow(
      'Building Lists schema is not ready; run Alembic migrations or explicit startup repair.',
    );
  });

  it('preserves schema-not-ready details for building list updates', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(schemaNotReadyResponse());

    await expect(updateBuildingList('list-1', 'Needs schema')).rejects.toThrow(
      'Building Lists schema is not ready; run Alembic migrations or explicit startup repair.',
    );
  });

  it('preserves schema-not-ready details for building list deletes', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(schemaNotReadyResponse());

    await expect(deleteBuildingList('list-1')).rejects.toThrow(
      'Building Lists schema is not ready; run Alembic migrations or explicit startup repair.',
    );
  });
});
