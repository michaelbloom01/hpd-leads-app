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
  estimated_annual_revenue?: number | null;
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

export interface BuildingOutreachStats {
  pipeline: number;
  contacted: number;
  meeting: number;
  won: number;
  lost: number;
  in_pipeline: number;
}

export interface BuildingStats {
  total: number;
  hot: number;
  warm: number;
  stable: number;
  avg_score: number;
  scored: number;
  outreach?: BuildingOutreachStats;
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

export interface BuildingLineageResponse {
  bbl: string;
  address: string | null;
  last_updated: string | null;
  sources: Array<{
    source_name: string;
    record_count: number;
    last_updated: string | null;
    status: 'available' | 'missing';
    mapped_fields: string[];
    missing_reason: string | null;
  }>;
}

export interface BuildingsQueryParams {
  borough?: string;
  building_type?: string;
  min_units?: number;
  max_units?: number;
  min_churn?: number;
  max_churn?: number;
  churn_category?: string;
  outreach_status?: string;
  lead_id?: string;
  search?: string;
  sort_by?: string;
  sort_dir?: string;
  limit?: number;
  offset?: number;
}

export interface OutreachEvent {
  id: number;
  bbl: string;
  stage: string;
  method: string | null;
  outcome: string | null;
  notes: string | null;
  next_follow_up: string | null;
  event_timestamp: string;
  created_at: string;
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

export function fetchBuildingLineage(bbl: string): Promise<BuildingLineageResponse> {
  return apiGet(`/api/v1/buildings/${bbl}/lineage`);
}

export function addBuildingToPipeline(bbl: string): Promise<{ bbl: string; status: string }> {
  return apiPost(`/api/v1/buildings/${bbl}/pipeline`);
}

export function fetchBuildingOutreachEvents(bbl: string): Promise<{ bbl: string; events: OutreachEvent[] }> {
  return apiGet(`/api/v1/buildings/${bbl}/outreach-events`);
}

export interface BuildingList {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
  member_count?: number;
}

export async function getBuildingLists(): Promise<{ building_lists: BuildingList[] }> {
  return apiGet('/api/v1/building-lists');
}

export async function createBuildingList(name: string): Promise<{ id: string; name: string }> {
  return apiPost('/api/v1/building-lists', { name });
}

export async function addBuildingToList(listId: string, bbl: string): Promise<{ list_id: string; bbl: string; status: string }> {
  return apiPost(`/api/v1/building-lists/${listId}/buildings/${bbl}`);
}

export async function removeBuildingFromList(listId: string, bbl: string): Promise<{ list_id: string; bbl: string; status: string }> {
  const response = await fetch(`${API_BASE_URL}/api/v1/building-lists/${listId}/buildings/${bbl}`, {
    method: 'DELETE',
    headers: getAuthHeaders(),
  });
  if (response.status === 401) { clearToken(); window.dispatchEvent(new Event('auth:logout')); throw new Error('Session expired'); }
  if (!response.ok) throw new Error(`DELETE building from list failed: ${response.statusText}`);
  return response.json();
}

export function logBuildingOutreachEvent(
  bbl: string,
  event: { stage: string; method?: string; outcome?: string; notes?: string; next_follow_up?: string },
): Promise<{ status: string }> {
  const params = new URLSearchParams({ stage: event.stage });
  if (event.method) params.set('method', event.method);
  if (event.outcome) params.set('outcome', event.outcome);
  if (event.notes) params.set('notes', event.notes);
  if (event.next_follow_up) params.set('next_follow_up', event.next_follow_up);
  return apiPost(`/api/v1/buildings/${bbl}/outreach-event?${params}`);
}
