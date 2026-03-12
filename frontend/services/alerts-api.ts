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

export interface ChangeAlertRow {
  id: number;
  alert_type: string;
  lead_id?: string | null;
  bbl?: string | null;
  target_item_id?: string | null;
  description: string;
  details?: Record<string, unknown> | null;
  created_at: string;
  dismissed: boolean;
}

export async function listAlerts(limit = 50): Promise<ChangeAlertRow[]> {
  return apiRequest(`/api/v1/alerts?limit=${limit}`);
}

export async function dismissAlert(alertId: number): Promise<{ status: string }> {
  return apiRequest(`/api/v1/alerts/${alertId}/dismiss`, { method: 'POST' });
}

export async function getAlertCount(): Promise<{ count: number }> {
  return apiRequest('/api/v1/alerts/count');
}
