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

export interface BoardChairCoverage {
  total_buildings: number;
  eligible_buildings: number;
  current_exact_chair: number;
  stale_exact_chair: number;
  ambiguous_or_possible: number;
  exact_entity_without_chair: number;
  no_named_chair_match: number;
  not_loaded: number;
  hpd_head_officer_proxy: number;
  hpd_head_officer_included_in_chair_coverage: false;
  current_exact_coverage: number;
  current_exact_all_buildings_coverage: number;
  any_sourced_chair_coverage: number;
  reliability_policy: Record<string, string>;
}

export interface BoardChairBenchmarkCase {
  bbl: string;
  address: string;
  expected_name: string;
  expected_title: string;
  source_date: string | null;
  source_name: string;
  source_url: string;
  observed_name: string | null;
  status: 'current_match' | 'stale_match' | 'different_current_name' | 'missing_current_evidence';
  identity_match: boolean;
  evidence_currentness: 'current' | 'historical' | 'unverified';
}

export interface BoardChairBenchmark {
  total_cases: number;
  identity_matches: number;
  status_counts: Record<string, number>;
  cases: BoardChairBenchmarkCase[];
  interpretation: string;
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
export const fetchBoardChairCoverage = (): Promise<BoardChairCoverage> => apiGet('/api/v1/quality/board-chair-coverage');
export const fetchBoardChairBenchmark = (): Promise<BoardChairBenchmark> => apiGet('/api/v1/quality/board-chair-benchmark');
export const fetchSourceAudit = (): Promise<SourceAuditResponse> => apiGet('/api/v1/quality/source-audit');
