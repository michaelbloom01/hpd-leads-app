/**
 * Scoring configuration API service — Settings page.
 */
import { getAuthHeaders, clearToken } from './auth';
import { API_BASE_URL } from './config';

async function apiGet<T>(path: string): Promise<T> {
  const resp = await fetch(`${API_BASE_URL}${path}`, { headers: getAuthHeaders() });
  if (resp.status === 401) { clearToken(); window.dispatchEvent(new Event('auth:logout')); throw new Error('Session expired'); }
  if (!resp.ok) throw new Error(`GET ${path}: ${resp.statusText}`);
  return resp.json();
}

async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const resp = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (resp.status === 401) { clearToken(); window.dispatchEvent(new Event('auth:logout')); throw new Error('Session expired'); }
  if (!resp.ok) throw new Error(`POST ${path}: ${resp.statusText}`);
  return resp.json();
}

async function apiPut<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(`${API_BASE_URL}${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`PUT ${path}: ${resp.statusText}`);
  return resp.json();
}

async function apiDelete<T>(path: string): Promise<T> {
  const resp = await fetch(`${API_BASE_URL}${path}`, {
    method: 'DELETE',
    headers: getAuthHeaders(),
  });
  if (!resp.ok) throw new Error(`DELETE ${path}: ${resp.statusText}`);
  return resp.json();
}

export interface ScoringWeights {
  ownership_change: number;
  complaint_spike: number;
  violation_trend: number;
  energy_grade_drop: number;
  dob_permits: number;
  hpd_litigation: number;
  emergency_repairs: number;
  building_size: number;
  eviction_activity: number;
  facade_status: number;
}

export interface ScoringConfig {
  id: number;
  name: string;
  is_active: boolean;
  is_preset: boolean;
  weights: ScoringWeights;
  created_by: string | null;
  created_at: string | null;
}

export const fetchConfigs = (): Promise<ScoringConfig[]> => apiGet('/api/v1/scoring/configs');
export const fetchActiveConfig = (): Promise<ScoringConfig> => apiGet('/api/v1/scoring/configs/active');
export const createConfig = (name: string, weights: ScoringWeights) => apiPost<{ id: number }>('/api/v1/scoring/configs', { name, weights });
export const updateConfig = (id: number, name: string, weights: ScoringWeights) => apiPut(`/api/v1/scoring/configs/${id}`, { name, weights });
export const deleteConfig = (id: number) => apiDelete(`/api/v1/scoring/configs/${id}`);
export const activateConfig = (id: number) => apiPost(`/api/v1/scoring/configs/${id}/activate`);
export const triggerRecalculate = () => apiPost<{
  job_id: number | null;
  status: string;
  dispatch_mode?: string;
  approval_required?: boolean;
  dry_run?: boolean;
  confirm_execute?: boolean;
}>('/api/v1/scoring/recalculate');
