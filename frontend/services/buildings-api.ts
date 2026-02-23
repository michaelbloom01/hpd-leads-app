/**
 * Buildings API service — PM Operator persona.
 * All v1 API endpoints for the buildings workspace.
 */
import { getAuthHeaders, clearToken } from './auth';
import { API_BASE_URL } from './config';

async function apiGet<T>(path: string, timeoutMs = 30000): Promise<T> {
  const controller = new AbortController();
  const tid = setTimeout(() => controller.abort(), timeoutMs);
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: getAuthHeaders(),
    signal: controller.signal,
  });
  clearTimeout(tid);
  if (response.status === 401) { clearToken(); window.dispatchEvent(new Event('auth:logout')); throw new Error('Session expired'); }
  if (!response.ok) throw new Error(`GET ${path} failed: ${response.statusText}`);
  return response.json();
}

async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (response.status === 401) { clearToken(); window.dispatchEvent(new Event('auth:logout')); throw new Error('Session expired'); }
  if (!response.ok) throw new Error(`POST ${path} failed: ${response.statusText}`);
  return response.json();
}

export interface BuildingRow {
  bbl: string;
  address: string;
  borough: string;
  unit_count: number | null;
  building_type: string | null;
  churn_score: number | null;
  churn_category: 'hot' | 'warm' | 'stable' | null;
  key_signal: string | null;
  coverage_ratio: number | null;
  outreach_status: string | null;
  last_scored_at: string | null;
  current_lead_id: string | null;
}

export interface BuildingsListResponse {
  buildings: BuildingRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface BuildingDetail {
  bbl: string;
  bin: string | null;
  address: string;
  borough: string;
  block: string | null;
  lot: string | null;
  zip_code: string | null;
  building_class: string | null;
  building_type: string | null;
  unit_count: number | null;
  year_built: number | null;
  assessed_value: number | null;
  council_district: string | null;
  community_board: string | null;
  census_tract: string | null;
  nta: string | null;
  churn_score: number | null;
  churn_category: string | null;
  churn_breakdown: Record<string, { raw: number | null; weight: number; effective_weight: number; contribution: number }> | null;
  key_signal: string | null;
  signals_available: number | null;
  coverage_ratio: number | null;
  scoring_config_id: number | null;
  last_scored_at: string | null;
  outreach_status: string | null;
  current_lead_id: string | null;
}

export interface BuildingStats {
  total: number;
  hot: number;
  warm: number;
  stable: number;
  avg_score: number;
  scored: number;
}

export interface TimelineEvent {
  type: string;
  date: string | null;
  detail: string | null;
}

export interface ScoreHistoryEntry {
  churn_score: number;
  churn_category: string;
  churn_breakdown: Record<string, unknown>;
  scored_at: string;
}

export interface BuildingsQueryParams {
  borough?: string;
  building_type?: string;
  min_units?: number;
  max_units?: number;
  min_churn?: number;
  max_churn?: number;
  churn_category?: string;
  lead_id?: string;
  search?: string;
  sort_by?: string;
  sort_dir?: string;
  limit?: number;
  offset?: number;
}

export function fetchBuildings(params: BuildingsQueryParams = {}): Promise<BuildingsListResponse> {
  const sp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') sp.set(k, String(v));
  });
  const qs = sp.toString();
  return apiGet(`/api/v1/buildings${qs ? '?' + qs : ''}`);
}

export function fetchBuildingStats(): Promise<BuildingStats> {
  return apiGet('/api/v1/buildings/stats');
}

export function fetchHotBuildings(limit = 20): Promise<BuildingRow[]> {
  return apiGet(`/api/v1/buildings/hot?limit=${limit}`);
}

export function fetchBuildingDetail(bbl: string): Promise<BuildingDetail> {
  return apiGet(`/api/v1/buildings/${bbl}`);
}

export function fetchBuildingTimeline(bbl: string): Promise<TimelineEvent[]> {
  return apiGet(`/api/v1/buildings/${bbl}/timeline`);
}

export function fetchBuildingScoreHistory(bbl: string): Promise<ScoreHistoryEntry[]> {
  return apiGet(`/api/v1/buildings/${bbl}/score-history`);
}

export function addBuildingToPipeline(bbl: string): Promise<{ bbl: string; status: string }> {
  return apiPost(`/api/v1/buildings/${bbl}/pipeline`);
}
