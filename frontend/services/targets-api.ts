import { getAuthHeaders, clearToken } from './auth';
import { API_BASE_URL } from './config';

async function apiRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      ...getAuthHeaders(),
      ...(options.headers || {}),
    },
  });
  if (response.status === 401) {
    clearToken();
    window.dispatchEvent(new Event('auth:logout'));
    throw new Error('Session expired');
  }
  if (!response.ok) throw new Error(`${options.method || 'GET'} ${path}: ${response.statusText}`);
  return response.json();
}

export interface TargetListSummary {
  target_list_id: string;
  name: string;
  description?: string | null;
  targeting_mode?: string | null;
  item_count: number;
  matched_count: number;
  updated_at: string;
}

export interface TargetListItem {
  target_item_id: string;
  target_list_id: string;
  company_name: string;
  tier?: string | null;
  geography?: string | null;
  ownership?: string | null;
  key_principals?: string | null;
  condo_focus?: string | null;
  website?: string | null;
  phone?: string | null;
  address?: string | null;
  acquisition_fit_notes?: string | null;
  risk_flag?: string | null;
  notes?: string | null;
  match_status?: string | null;
  matched_lead_id?: string | null;
  thesis_score?: number | null;
  thesis_summary?: string | null;
  next_follow_up?: string | null;
  portfolio_estimate?: string | null;
  units_estimate?: string | null;
  established?: string | null;
}

export interface TargetListDetail extends TargetListSummary {
  thesis_id?: string | null;
  source_notes?: string | null;
  items: TargetListItem[];
}

export interface TargetImportRow {
  company_name: string;
  established?: string;
  portfolio_estimate?: string;
  units_estimate?: string;
  geography?: string;
  ownership?: string;
  key_principals?: string;
  condo_focus?: string;
  website?: string;
  phone?: string;
  address?: string;
  tier?: string;
  acquisition_fit_notes?: string;
  risk_flag?: string;
  notes?: string;
}

export interface TargetDiscovery {
  lead_id: string;
  company_name: string;
  primary_borough?: string | null;
  portfolio_size?: number | null;
  total_units?: number | null;
  estimated_annual_revenue?: number | null;
  discovery_score: number;
  reasons: string[];
}

export interface TargetDossier {
  target_item: TargetListItem & Record<string, unknown>;
  matches: Array<Record<string, unknown>>;
  matched_lead: Record<string, unknown> | null;
  linked_buildings: Array<Record<string, unknown>>;
  building_contacts: Array<Record<string, unknown>>;
  people_graph: Array<Record<string, unknown>>;
  outreach_events: Array<Record<string, unknown>>;
  missing_diligence: string[];
  adjacent_targets: TargetDiscovery[];
}

export async function getTargetLists(): Promise<{ target_lists: TargetListSummary[] }> {
  return apiRequest('/api/v1/targets/lists');
}

export async function createTargetList(body: {
  name: string;
  description?: string;
  targeting_mode?: string;
  source_notes?: string;
}): Promise<{ status: string; target_list_id: string; name: string }> {
  return apiRequest('/api/v1/targets/lists', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function getTargetList(targetListId: string): Promise<TargetListDetail> {
  return apiRequest(`/api/v1/targets/lists/${targetListId}`);
}

export async function importTargetItems(targetListId: string, rows: TargetImportRow[]): Promise<{
  status: string;
  imported_count: number;
  results: Array<Record<string, unknown>>;
}> {
  return apiRequest(`/api/v1/targets/lists/${targetListId}/items/import`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rows }),
  });
}

export async function rescoreTargetList(targetListId: string): Promise<{
  status: string;
  count: number;
  results: Array<Record<string, unknown>>;
}> {
  return apiRequest(`/api/v1/targets/lists/${targetListId}/rescore`, { method: 'POST' });
}

export async function discoverAdjacentTargets(targetListId: string, limit = 25): Promise<{
  target_list_id: string;
  discoveries: TargetDiscovery[];
}> {
  return apiRequest(`/api/v1/targets/lists/${targetListId}/discover?limit=${limit}`);
}

export async function patchTargetItem(targetItemId: string, body: Record<string, unknown>): Promise<{ status: string }> {
  return apiRequest(`/api/v1/targets/items/${targetItemId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function selectTargetMatch(targetItemId: string, leadId: string): Promise<{ status: string }> {
  return apiRequest(`/api/v1/targets/items/${targetItemId}/match/select`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lead_id: leadId }),
  });
}

export async function getTargetDossier(targetItemId: string): Promise<TargetDossier> {
  return apiRequest(`/api/v1/targets/items/${targetItemId}/dossier`);
}
