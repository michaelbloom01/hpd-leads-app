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

export interface UnifiedFollowUpItem {
  entity_type: 'lead' | 'target' | 'building';
  entity_id: string;
  display_name: string;
  due_date: string;
  stage: string;
  status: string | null;
  secondary_ref?: string | null;
}

export async function getUnifiedFollowUps(before?: string): Promise<{
  count: number;
  counts: Record<string, number>;
  items: UnifiedFollowUpItem[];
}> {
  const suffix = before ? `?before=${encodeURIComponent(before)}` : '';
  return apiRequest(`/api/v1/outreach/follow-ups${suffix}`);
}

export async function createUnifiedOutreachEvent(body: {
  lead_id?: string;
  bbl?: string;
  target_item_id?: string;
  canonical_entity_id?: string;
  stage: string;
  method?: string;
  outcome?: string;
  notes?: string;
  next_follow_up?: string;
}): Promise<{ status: string; event_id: number; truth_claim_ids?: string[] }> {
  return apiRequest('/api/v1/outreach/events', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
