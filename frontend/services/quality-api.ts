/**
 * Data quality API service — Data Health Dashboard on Settings page.
 */
import { getAuthHeaders, clearToken } from './auth';
import { API_BASE_URL } from './config';

async function apiGet<T>(path: string): Promise<T> {
  const resp = await fetch(`${API_BASE_URL}${path}`, { headers: getAuthHeaders() });
  if (resp.status === 401) { clearToken(); window.dispatchEvent(new Event('auth:logout')); throw new Error('Session expired'); }
  if (!resp.ok) throw new Error(`GET ${path}: ${resp.statusText}`);
  return resp.json();
}

export interface QualitySummary {
  source_name: string;
  records_fetched: number;
  records_matched: number;
  records_rejected: number;
  records_inserted: number;
  match_rate: number;
  volume_anomaly: boolean;
  notes: string;
  run_timestamp: string;
}

export interface CoverageStats {
  total_buildings: number;
  with_complaints: number;
  with_violations: number;
  with_transactions: number;
  with_permits: number;
  with_litigation: number;
  with_erp: number;
  with_energy: number;
  with_evictions: number;
  with_facades: number;
  with_aep: number;
}

export interface SourceAuditRow {
  source_name: string;
  dataset_id: string;
  table_name: string;
  job_type: string;
  ui_surface: string;
  table_exists: boolean;
  runnable_job: boolean;
  has_quality_log: boolean;
  last_run: string | null;
  last_records_fetched: number;
  last_records_inserted: number;
  status: 'operational' | 'not_wired' | 'schema_missing' | 'no_recent_ingest';
}

export interface SourceAuditSummary {
  total_sources: number;
  operational: number;
  not_wired: number;
  schema_missing: number;
  no_recent_ingest: number;
}

export interface SourceAuditResponse {
  summary: SourceAuditSummary;
  critical_gaps: SourceAuditRow[];
  sources: SourceAuditRow[];
}

export const fetchQualitySummary = (): Promise<QualitySummary[]> => apiGet('/api/v1/quality/summary');
export const fetchQualityHistory = (source?: string, limit = 30): Promise<QualitySummary[]> =>
  apiGet(`/api/v1/quality/history${source ? `?source=${source}&limit=${limit}` : `?limit=${limit}`}`);
export const fetchCoverage = (): Promise<CoverageStats> => apiGet('/api/v1/quality/coverage');
export const fetchSourceAudit = (): Promise<SourceAuditResponse> => apiGet('/api/v1/quality/source-audit');
