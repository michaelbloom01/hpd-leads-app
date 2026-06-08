/**
 * Jobs API service — background task monitoring.
 */
import { getAuthHeaders, clearToken } from './auth';
import { API_BASE_URL } from './config';

async function apiGet<T>(path: string): Promise<T> {
  const resp = await fetch(`${API_BASE_URL}${path}`, { headers: getAuthHeaders() });
  if (resp.status === 401) { clearToken(); window.dispatchEvent(new Event('auth:logout')); throw new Error('Session expired'); }
  if (!resp.ok) throw new Error(`GET ${path}: ${resp.statusText}`);
  return resp.json();
}

async function apiPost<T>(path: string): Promise<T> {
  const resp = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: getAuthHeaders(),
  });
  if (!resp.ok) throw new Error(`POST ${path}: ${resp.statusText}`);
  return resp.json();
}

const queryString = (params: Record<string, string | number | boolean | undefined>): string => {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined) search.set(key, String(value));
  });
  const rendered = search.toString();
  return rendered ? `?${rendered}` : '';
};

export interface Job {
  id: number;
  job_type: string;
  source: string;
  status: string;
  total: number | null;
  processed: number | null;
  succeeded: number | null;
  failed: number | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface JobsSummary {
  queued_count: number;
  running_count: number;
  succeeded_24h: number;
  failed_24h: number;
  avg_duration_seconds_24h: number;
}

export interface StartJobResponse {
  status: string;
  job_type: string;
  requested_job_type?: string;
  job_id?: number;
  limit?: number;
  dispatch_mode?: string;
  dry_run?: boolean;
  confirm_execute?: boolean;
  approval_required?: boolean;
  safe_to_run_automatically?: boolean;
  mutations_planned?: number;
  rollback_strategy?: string;
  preview?: {
    operation?: string;
    would_enqueue_job_type?: string;
    would_mutate?: string[];
    required_execute_query?: string;
  };
  schema_status?: {
    ready: boolean;
    expected_revision?: string;
    current_revision?: string | null;
    missing_tables?: string[];
  };
}

export interface StartJobOptions {
  limit?: number;
  dryRun?: boolean;
  confirmExecute?: boolean;
  cohortFilter?: string | null;
}

export const fetchJobs = (status?: string, limit = 20): Promise<Job[]> =>
  apiGet(`/api/v1/jobs${status ? `?status=${status}&limit=${limit}` : `?limit=${limit}`}`);

export const fetchJob = (id: number): Promise<Job> => apiGet(`/api/v1/jobs/${id}`);

export const startJob = (jobType: string, options: StartJobOptions = {}): Promise<StartJobResponse> =>
  apiPost(`/api/v1/jobs/${jobType}/start${queryString({
    limit: options.limit,
    dry_run: options.dryRun,
    confirm_execute: options.confirmExecute,
    cohort_filter: options.cohortFilter ?? undefined,
  })}`);

export const fetchJobsSummary = (): Promise<JobsSummary> => apiGet('/api/v1/jobs/summary');
