/**
 * API service for communicating with the HPD Leads backend.
 */

// API base URL - uses environment variable in production, localhost in dev
const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

/**
 * Lead data from the backend API
 */
export interface ApiLead {
  lead_id: string;
  agent_name: string;
  owner_name: string;
  owner_type: string;
  portfolio_size: number;
  total_units: number;
  buildings: string[];
  phone: string | null;
  email: string | null;
  website: string | null;
  business_summary: string | null;
  address: string | null;
  boro: string;
  boros: string[];
  score: number;
  tags: string[];
  enrichment_status: string;
  outreach_status: string;
  notes: string | null;
}

export interface PipelineStatus {
  total_leads: number;
  last_refresh: string | null;
  enriched_count: number;
  top_score: number;
}

export interface EnrichmentResult {
  lead_id: string;
  agent_name: string;
  website: string | null;
  phone: string | null;
  email: string | null;
  status: string;
}

/**
 * Fetch leads from the backend with optional filtering
 */
export async function fetchLeads(params?: {
  min_score?: number;
  min_portfolio?: number;
  boro?: string;
  has_website?: boolean;
  has_email?: boolean;
  limit?: number;
  offset?: number;
}): Promise<ApiLead[]> {
  const searchParams = new URLSearchParams();
  
  if (params) {
    if (params.min_score !== undefined) searchParams.set('min_score', params.min_score.toString());
    if (params.min_portfolio !== undefined) searchParams.set('min_portfolio', params.min_portfolio.toString());
    if (params.boro) searchParams.set('boro', params.boro);
    if (params.has_website !== undefined) searchParams.set('has_website', params.has_website.toString());
    if (params.has_email !== undefined) searchParams.set('has_email', params.has_email.toString());
    if (params.limit !== undefined) searchParams.set('limit', params.limit.toString());
    if (params.offset !== undefined) searchParams.set('offset', params.offset.toString());
  }
  
  const url = `${API_BASE_URL}/api/leads${searchParams.toString() ? '?' + searchParams.toString() : ''}`;
  const response = await fetch(url);
  
  if (!response.ok) {
    throw new Error(`Failed to fetch leads: ${response.statusText}`);
  }
  
  return response.json();
}

/**
 * Fetch a single lead by ID
 */
export async function fetchLead(leadId: string): Promise<ApiLead> {
  const response = await fetch(`${API_BASE_URL}/api/leads/${leadId}`);
  
  if (!response.ok) {
    throw new Error(`Failed to fetch lead: ${response.statusText}`);
  }
  
  return response.json();
}

/**
 * Get pipeline status
 */
export async function fetchStatus(): Promise<PipelineStatus> {
  const response = await fetch(`${API_BASE_URL}/api/status`);
  
  if (!response.ok) {
    throw new Error(`Failed to fetch status: ${response.statusText}`);
  }
  
  return response.json();
}

/**
 * Refresh the pipeline - fetch fresh data from HPD
 * @param full - If true, fetches ALL ~200k buildings (slow). Otherwise fetches 10k.
 */
export async function refreshPipeline(full: boolean = false): Promise<{
  status: string;
  buildings_fetched: number;
  leads_created: number;
  timestamp: string;
}> {
  const params = full ? '?full=true' : '';
  const response = await fetch(`${API_BASE_URL}/api/refresh${params}`, {
    method: 'POST',
  });
  
  if (!response.ok) {
    throw new Error(`Failed to refresh pipeline: ${response.statusText}`);
  }
  
  return response.json();
}

/**
 * Get detailed statistics
 */
export async function fetchStats(): Promise<{
  total_leads: number;
  total_buildings: number;
  by_borough: Record<string, number>;
  by_enrichment_status: Record<string, number>;
  score_distribution: Record<string, number>;
  portfolio_distribution: Record<string, number>;
  with_phone: number;
  with_email: number;
  with_website: number;
}> {
  const response = await fetch(`${API_BASE_URL}/api/stats`);
  
  if (!response.ok) {
    throw new Error(`Failed to fetch stats: ${response.statusText}`);
  }
  
  return response.json();
}

/**
 * Enrich specific leads by ID
 */
export async function enrichLeads(leadIds: string[]): Promise<{
  status: string;
  enriched_count: number;
  results: EnrichmentResult[];
}> {
  const response = await fetch(`${API_BASE_URL}/api/enrich`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lead_ids: leadIds }),
  });
  
  if (!response.ok) {
    throw new Error(`Failed to enrich leads: ${response.statusText}`);
  }
  
  return response.json();
}

/**
 * Enrich a batch of top leads automatically
 */
export async function enrichBatch(limit: number = 10, minScore?: number): Promise<{
  status: string;
  enriched_count: number;
}> {
  const params = new URLSearchParams({ limit: limit.toString() });
  if (minScore !== undefined) params.set('min_score', minScore.toString());
  
  const response = await fetch(`${API_BASE_URL}/api/enrich/batch?${params.toString()}`, {
    method: 'POST',
  });
  
  if (!response.ok) {
    throw new Error(`Failed to enrich batch: ${response.statusText}`);
  }
  
  return response.json();
}

/**
 * Update a lead's status and/or notes
 */
export async function updateLead(
  leadId: string, 
  updates: { outreach_status?: string; notes?: string }
): Promise<{
  status: string;
  lead_id: string;
  outreach_status: string;
  notes: string | null;
}> {
  const response = await fetch(`${API_BASE_URL}/api/leads/${leadId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  });
  
  if (!response.ok) {
    throw new Error(`Failed to update lead: ${response.statusText}`);
  }
  
  return response.json();
}

/**
 * Health check
 */
export async function healthCheck(): Promise<{ status: string; service: string }> {
  const response = await fetch(`${API_BASE_URL}/`);
  
  if (!response.ok) {
    throw new Error(`API health check failed: ${response.statusText}`);
  }
  
  return response.json();
}
