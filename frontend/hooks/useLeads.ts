/**
 * React hook for managing leads data from the API.
 */
import { useState, useEffect, useCallback } from 'react';
import { ApiLead, PipelineStatus, fetchLeads, fetchStatus, refreshPipeline, enrichLeads } from '../services/api';

interface UseLeadsOptions {
  autoRefresh?: boolean;
  refreshInterval?: number;
}

export function useLeads(options: UseLeadsOptions = {}) {
  const [leads, setLeads] = useState<ApiLead[]>([]);
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load leads from API
  const loadLeads = useCallback(async (params?: {
    min_score?: number;
    min_portfolio?: number;
    boro?: string;
  }) => {
    try {
      setLoading(true);
      setError(null);
      const data = await fetchLeads({ ...params, limit: 200 });
      setLeads(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load leads');
      console.error('Failed to load leads:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  // Load pipeline status
  const loadStatus = useCallback(async () => {
    try {
      const data = await fetchStatus();
      setStatus(data);
    } catch (err) {
      console.error('Failed to load status:', err);
    }
  }, []);

  // Refresh pipeline (fetch new data from HPD)
  const refresh = useCallback(async (limit: number = 5000) => {
    try {
      setRefreshing(true);
      setError(null);
      await refreshPipeline(limit);
      // Reload leads after refresh
      await loadLeads();
      await loadStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to refresh pipeline');
      console.error('Failed to refresh:', err);
    } finally {
      setRefreshing(false);
    }
  }, [loadLeads, loadStatus]);

  // Enrich specific leads
  const enrich = useCallback(async (leadIds: string[]) => {
    try {
      setError(null);
      const result = await enrichLeads(leadIds);
      // Reload leads to get updated data
      await loadLeads();
      return result;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to enrich leads');
      console.error('Failed to enrich:', err);
      throw err;
    }
  }, [loadLeads]);

  // Initial load
  useEffect(() => {
    loadLeads();
    loadStatus();
  }, [loadLeads, loadStatus]);

  return {
    leads,
    status,
    loading,
    refreshing,
    error,
    loadLeads,
    loadStatus,
    refresh,
    enrich,
  };
}
