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
}

export const fetchJobs = (status?: string, limit = 20): Promise<Job[]> =>
  apiGet(`/api/v1/jobs${status ? `?status=${status}&limit=${limit}` : `?limit=${limit}`}`);

export const fetchJob = (id: number): Promise<Job> => apiGet(`/api/v1/jobs/${id}`);

export const startJob = (jobType: string): Promise<StartJobResponse> =>
  apiPost(`/api/v1/jobs/${jobType}/start`);

export const fetchJobsSummary = (): Promise<JobsSummary> => apiGet('/api/v1/jobs/summary');
