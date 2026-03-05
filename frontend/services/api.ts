/**
 * API service for communicating with the Double Edge backend.
 * Updated: Phase 5 (revenue, violations, pipeline, scoring V2)
 * R4/R5: Added health check, fetchWithRetry, AbortController timeouts
 */

import { toast } from 'react-hot-toast';
import { getAuthHeaders, clearToken } from './auth';
import { API_BASE_URL } from './config';
import type { BuildingContactEntry } from './buildings-api';

// Re-export for backward compatibility (other files import from api.ts)
export { API_BASE_URL };

// ============================================================================
// R4: Health Check
// ============================================================================

export interface HealthStatus {
  status: 'starting' | 'ok' | 'error' | 'degraded';
  message?: string;
  leads_in_cache?: number;
  leads_in_db?: number;
  startup_error?: string;
  startup_state?: 'starting' | 'ready' | 'degraded';
}

/**
 * Check backend health status. Returns 'starting' while backend is booting.
 * Railway returns 502/503 when the container is sleeping or cold-starting,
 * so we treat those as 'starting' to keep polling instead of showing an error.
 */
export async function checkHealth(): Promise<HealthStatus> {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);
    const response = await fetch(`${API_BASE_URL}/api/health`, { signal: controller.signal });
    clearTimeout(timeoutId);
    if (!response.ok) {
      // 502/503/504 = Railway container sleeping or cold-starting — treat as 'starting'
      if (response.status >= 502 && response.status <= 504) {
        return { status: 'starting', message: `Backend is waking up (HTTP ${response.status}).` };
      }
      return { status: 'error', message: `HTTP ${response.status}` };
    }
    return response.json();
  } catch (err) {
    // Network error or timeout likely means backend is not up yet
    return { status: 'starting', message: 'Backend unreachable — may be starting up.' };
  }
}

// ============================================================================
// R5: Fetch with Retry + AbortController Timeout
// ============================================================================

/**
 * Convenience wrapper: GET with JSON response.
 * Throws on non-OK and handles auth/retry automatically.
 */
async function apiGet<T>(path: string, timeoutMs = 30000): Promise<T> {
  const response = await fetchWithRetry(`${API_BASE_URL}${path}`, {}, 1, timeoutMs);
  if (!response.ok) throw new Error(`GET ${path} failed: ${response.statusText}`);
  return response.json();
}

/**
 * Convenience wrapper: POST/PATCH/DELETE with JSON body.
 */
async function apiMutate<T>(
  path: string,
  method: 'POST' | 'PATCH' | 'DELETE' = 'POST',
  body?: unknown,
  timeoutMs = 30000,
): Promise<T> {
  const opts: RequestInit = { method };
  if (body !== undefined) {
    opts.headers = { 'Content-Type': 'application/json' };
    opts.body = JSON.stringify(body);
  }
  const response = await fetchWithRetry(`${API_BASE_URL}${path}`, opts, 1, timeoutMs);
  if (!response.ok) throw new Error(`${method} ${path} failed: ${response.statusText}`);
  return response.json();
}

/**
 * Enhanced fetch wrapper with automatic retries and timeout via AbortController.
 */
async function fetchWithRetry(
  url: string,
  options: RequestInit = {},
  retries: number = 1,
  timeoutMs: number = 30000,
): Promise<Response> {
  let lastError: Error | null = null;
  
  for (let attempt = 0; attempt <= retries; attempt++) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    
    // Inject auth headers into every request
    const authHeaders = getAuthHeaders();
    const mergedHeaders = {
      ...authHeaders,
      ...(options.headers || {}),
    };
    
    try {
      const response = await fetch(url, {
        ...options,
        headers: mergedHeaders,
        signal: controller.signal,
      });
      clearTimeout(timeoutId);
      
      if (response.status === 401) {
        clearToken();
        window.dispatchEvent(new Event('auth:logout'));
        toast.error('Session expired — please log in again');
        throw new Error('Session expired — please log in again');
      }

      if (response.status === 403) {
        toast.error('You do not have permission to perform this action.');
        throw new Error('Forbidden');
      }

      if (response.status === 404) {
        throw new Error(`Not found: ${url.replace(API_BASE_URL, '')}`);
      }

      if (response.status === 422) {
        toast.error('Invalid request — please check your inputs.');
        throw new Error('Validation error');
      }

      if (response.status >= 502 && response.status <= 504 && attempt < retries) {
        lastError = new Error(`HTTP ${response.status}`);
        await new Promise(r => setTimeout(r, 1000 * (attempt + 1)));
        continue;
      }

      if (response.status >= 500) {
        toast.error('Server error — please try again in a moment.');
      }

      return response;
    } catch (err: unknown) {
      clearTimeout(timeoutId);
      lastError = err instanceof Error ? err : new Error(String(err));
      
      if (attempt < retries) {
        await new Promise(r => setTimeout(r, 1000 * (attempt + 1)));
      }
    }
  }
  
  const finalError = lastError || new Error('Request failed after retries');
  if (finalError.name === 'AbortError') {
    toast.error('Request timed out — the server may be busy.');
  } else if (!finalError.message.includes('Session expired')) {
    toast.error('Network error — check your connection.');
  }
  throw finalError;
}

/**
 * Score breakdown showing component scores (V3)
 */
export interface ScoreBreakdown {
  // Current backend fields
  portfolio?: number;
  units?: number;
  professional?: number;
  contact?: number;
  concentration?: number;
  // Backward-compatible fields still accepted by some frontend views
  condo_coop?: number;
  density?: number;
  location?: number;
  revenue?: number;
  distress?: number;
  deal_fit?: number;
}

/**
 * Contact info with source attribution
 */
export interface ContactWithSource {
  value: string;
  type: 'phone' | 'email';
  source: string;
  source_url: string | null;
  confidence: number;
  verified: boolean;
}

/**
 * Building type breakdown from PLUTO data
 */
export interface BuildingTypeBreakdown {
  condo: number;
  coop: number;
  rental_elevator: number;
  rental_walkup: number;
  small_residential: number;
  other: number;
  unknown: number;
  total: number;
  total_rental: number;
}

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
  phones: ContactWithSource[];
  emails: ContactWithSource[];
  website: string | null;
  website_source: string | null;
  business_summary: string | null;
  linkedin_url: string | null;
  linkedin_people: string[];
  address: string | null;
  boro: string;
  boros: string[];
  building_types: BuildingTypeBreakdown | null;
  building_classes: Record<string, number>;
  score: number;
  score_breakdown: ScoreBreakdown | null;
  tags: string[];
  enrichment_status: string;
  outreach_status: string;
  notes: string | null;
  outreach_attempts?: OutreachAttempt[];
  // Entity classification
  contacts: Array<{ type: string; name: string; title: string; address?: string }>;
  entity_type: string;
  company_name: string | null;
  primary_contact: string | null;
  primary_contact_title: string | null;
  // Revenue estimation (Phase 5.1)
  estimated_monthly_revenue: number;
  estimated_annual_revenue: number;
  revenue_breakdown: Array<{
    type: string;
    label: string;
    buildings: number;
    estimated_units: number;
    rent_per_unit: number;
    monthly_gross: number;
    fee_rate: number;
    avg_rent_per_unit: number;
    borough_used: string;
    total_units_used: number;
  }> | null;
  // Violations (Phase 5.2)
  violation_count: number;
  violation_class_a: number;
  violation_class_b: number;
  violation_class_c: number;
  violations_per_unit: number;
  // Pipeline (Phase 5.3)
  pipeline_stage: string;
  next_follow_up: string | null;
  priority_rank: number;
  // Data robustness (v2)
  data_staleness?: string;  // 'current' | 'partially_stale' | 'expired'
}

/**
 * Paginated leads response
 */
export interface LeadsListResponse {
  leads: ApiLead[];
  total: number;
  offset: number;
  limit: number;
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

export interface CompanyResearch {
  lead_id: string;
  owner_names: string[] | null;
  year_established: string | null;
  service_areas: string[] | null;
  description: string | null;
  phones: string[] | null;
  emails: string[] | null;
  social_links: Record<string, string> | null;
  scraped_at: string;
  ai_description?: string | null;
}

export interface OutreachAttempt {
  id: string;
  method: string;
  outcome: string;
  notes: string | null;
  timestamp: string;
}

/**
 * Outreach event (Phase 5.3 - richer than OutreachAttempt)
 */
export interface OutreachEvent {
  id: number;
  stage: string;
  method: string | null;
  outcome: string | null;
  notes: string | null;
  next_follow_up: string | null;
  timestamp: string;
}

/**
 * Change alert (Phase 5.4)
 */
export interface ChangeAlert {
  id: number;
  alert_type: string;
  lead_id: string | null;
  description: string;
  details: Record<string, unknown> | null;
  created_at: string;
  dismissed: boolean;
}

/**
 * Statistics response from the API
 */
export interface PipelineStats {
  total_leads: number;
  total_buildings: number;
  total_units: number;
  by_borough: Record<string, number>;
  by_enrichment_status: Record<string, number>;
  by_outreach_status: Record<string, number>;
  by_entity_type: Record<string, number>;
  by_pipeline_stage: Record<string, number>;
  score_distribution: Record<string, number>;
  portfolio_distribution: Record<string, number>;
  with_phone: number;
  with_email: number;
  with_website: number;
  top_score: number;
  avg_score: number;
  last_refresh: string | null;
}

// ============================================================================
// API Functions
// ============================================================================

/**
 * Fetch leads from the backend with server-side filtering and pagination
 */
export async function fetchLeads(params?: {
  min_score?: number;
  max_score?: number;
  min_portfolio?: number;
  max_portfolio?: number;
  boro?: string;
  has_phone?: boolean;
  has_email?: boolean;
  has_website?: boolean;
  entity_type?: string;
  enrichment_status?: string;
  outreach_status?: string;
  pipeline_stage?: string;
  search?: string;
  sort_by?: string;
  sort_dir?: string;
  min_units?: number;
  max_units?: number;
  min_units_per_bldg?: number;
  max_units_per_bldg?: number;
  building_type_has?: string;
  limit?: number;
  offset?: number;
}): Promise<LeadsListResponse> {
  const searchParams = new URLSearchParams();
  
  if (params) {
    if (params.min_score !== undefined) searchParams.set('min_score', params.min_score.toString());
    if (params.max_score !== undefined) searchParams.set('max_score', params.max_score.toString());
    if (params.min_portfolio !== undefined) searchParams.set('min_portfolio', params.min_portfolio.toString());
    if (params.max_portfolio !== undefined) searchParams.set('max_portfolio', params.max_portfolio.toString());
    if (params.boro) searchParams.set('boro', params.boro);
    if (params.has_phone !== undefined) searchParams.set('has_phone', params.has_phone.toString());
    if (params.has_email !== undefined) searchParams.set('has_email', params.has_email.toString());
    if (params.has_website !== undefined) searchParams.set('has_website', params.has_website.toString());
    if (params.entity_type) searchParams.set('entity_type', params.entity_type);
    if (params.enrichment_status) searchParams.set('enrichment_status', params.enrichment_status);
    if (params.outreach_status) searchParams.set('outreach_status', params.outreach_status);
    if (params.pipeline_stage) searchParams.set('pipeline_stage', params.pipeline_stage);
    if (params.search) searchParams.set('search', params.search);
    if (params.sort_by) searchParams.set('sort_by', params.sort_by);
    if (params.sort_dir) searchParams.set('sort_dir', params.sort_dir);
    if (params.min_units !== undefined) searchParams.set('min_units', params.min_units.toString());
    if (params.max_units !== undefined) searchParams.set('max_units', params.max_units.toString());
    if (params.min_units_per_bldg !== undefined) searchParams.set('min_units_per_bldg', params.min_units_per_bldg.toString());
    if (params.max_units_per_bldg !== undefined) searchParams.set('max_units_per_bldg', params.max_units_per_bldg.toString());
    if (params.building_type_has) searchParams.set('building_type_has', params.building_type_has);
    if (params.limit !== undefined) searchParams.set('limit', params.limit.toString());
    if (params.offset !== undefined) searchParams.set('offset', params.offset.toString());
  }
  
  const url = `${API_BASE_URL}/api/leads${searchParams.toString() ? '?' + searchParams.toString() : ''}`;
  const response = await fetchWithRetry(url);
  
  if (!response.ok) {
    throw new Error(`Failed to fetch leads: ${response.statusText}`);
  }
  
  return response.json();
}

/**
 * Fetch a single lead by ID
 */
export async function fetchLead(leadId: string): Promise<ApiLead> {
  const response = await fetchWithRetry(`${API_BASE_URL}/api/leads/${leadId}`);
  if (!response.ok) throw new Error(`Failed to fetch lead: ${response.statusText}`);
  return response.json();
}

/**
 * Estimate and persist revenue for a single lead.
 */
export async function estimateLeadRevenue(leadId: string): Promise<ApiLead> {
  const response = await fetchWithRetry(`${API_BASE_URL}/api/leads/${leadId}/estimate-revenue`, { method: 'POST' });
  if (!response.ok) throw new Error(`Failed to estimate lead revenue: ${response.statusText}`);
  return response.json();
}

/**
 * Get detailed statistics
 */
export async function fetchStats(): Promise<PipelineStats> {
  const response = await fetchWithRetry(`${API_BASE_URL}/api/stats`);
  if (!response.ok) throw new Error(`Failed to fetch stats: ${response.statusText}`);
  return response.json();
}

/**
 * Data freshness timestamps
 */
export interface DataStatus {
  revenue_computed_at: string | null;
  revenue_formula_version: string | null;
  violations_computed_at: string | null;
  enrichment_completed_at: string | null;
  last_refresh: string | null;
}

export interface SourceLineageRow {
  source_name: string;
  record_count: number;
  last_updated: string | null;
  status: 'available' | 'missing';
  mapped_fields: string[];
  missing_reason: string | null;
}

export interface LeadLineageResponse {
  lead_id: string;
  entity_name: string;
  last_updated: string | null;
  linked_buildings: number;
  sources: SourceLineageRow[];
}

export interface LeadContactsResponse {
  lead_id: string;
  buildings: Array<{
    bbl: string;
    address: string;
    outreach_status: string | null;
    contacts: BuildingContactEntry[];
  }>;
}

export async function fetchDataStatus(): Promise<DataStatus> {
  const response = await fetchWithRetry(`${API_BASE_URL}/api/data-status`);
  if (!response.ok) throw new Error(`Failed to fetch data status: ${response.statusText}`);
  return response.json();
}

export async function fetchLeadLineage(leadId: string): Promise<LeadLineageResponse> {
  const response = await fetchWithRetry(`${API_BASE_URL}/api/leads/${leadId}/lineage`);
  if (!response.ok) throw new Error(`Failed to fetch lead lineage: ${response.statusText}`);
  return response.json();
}

export async function fetchLeadContacts(leadId: string): Promise<LeadContactsResponse> {
  const response = await fetchWithRetry(`${API_BASE_URL}/api/leads/${leadId}/contacts`);
  if (!response.ok) throw new Error(`Failed to fetch lead contacts: ${response.statusText}`);
  return response.json();
}

/**
 * Refresh the pipeline - fetch fresh data from HPD
 */
export async function refreshPipeline(full: boolean = false): Promise<{
  status: string;
  buildings_fetched: number;
  leads_created: number;
  timestamp: string;
  dispatch_mode?: string;
}> {
  void full;
  // Runtime convergence: use PostgreSQL-backed jobs API instead of legacy /api/refresh.
  const response = await fetchWithRetry(`${API_BASE_URL}/api/v1/jobs/buildings/start`, { method: 'POST' });
  if (!response.ok) throw new Error(`Failed to queue refresh job: ${response.statusText}`);
  const data = await response.json();
  return {
    status: data.status || 'queued',
    buildings_fetched: 0,
    leads_created: 0,
    timestamp: new Date().toISOString(),
    dispatch_mode: data.dispatch_mode,
  };
}

/**
 * Update a lead's status, notes, pipeline stage, follow-up, and priority
 */
export async function updateLead(
  leadId: string, 
  updates: { 
    outreach_status?: string; 
    notes?: string;
    pipeline_stage?: string;
    next_follow_up?: string | null;
    priority_rank?: number;
  }
): Promise<{
  status: string;
  lead_id: string;
  outreach_status: string;
  pipeline_stage: string;
  next_follow_up: string | null;
  priority_rank: number;
  notes: string | null;
}> {
  const response = await fetchWithRetry(`${API_BASE_URL}/api/leads/${leadId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  });
  if (!response.ok) throw new Error(`Failed to update lead: ${response.statusText}`);
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
  // Runtime convergence: avoid legacy /api/enrich endpoint.
  // Use PostgreSQL-native /api/leads/{id}/enrich-all endpoint per selected lead.
  const results: EnrichmentResult[] = [];
  let enrichedCount = 0;

  const chunkSize = 5;
  for (let i = 0; i < leadIds.length; i += chunkSize) {
    const chunk = leadIds.slice(i, i + chunkSize);
    const settled = await Promise.allSettled(
      chunk.map(async (leadId) => {
        const enriched = await enrichLeadAll(leadId);
        return {
          lead_id: leadId,
          agent_name: enriched.lead.agent_name || enriched.lead.company_name || enriched.lead.owner_name || '',
          website: enriched.lead.website,
          phone: enriched.lead.phone,
          email: enriched.lead.email,
          status: enriched.enrichment_status,
        } as EnrichmentResult;
      })
    );

    settled.forEach((res) => {
      if (res.status === 'fulfilled') {
        results.push(res.value);
        if (res.value.status === 'complete' || res.value.status === 'partial') {
          enrichedCount += 1;
        }
      }
    });
  }

  return {
    status: 'success',
    enriched_count: enrichedCount,
    results,
  };
}

/** NY DOS Corporation info */
export interface DOSInfo {
  dos_id: string;
  entity_name: string;
  entity_type: string;
  formation_date: string | null;
  registered_agent: string | null;
  registered_address: string | null;
  lookup_url: string;
}

/** Multi-source contact enrichment result */
export interface ContactEnrichmentResult {
  status: string;
  lead_id: string;
  company_name: string;
  phones: ContactWithSource[];
  emails: ContactWithSource[];
  website: string | null;
  website_source: string | null;
  dos_info: DOSInfo | null;
  sources_tried: string[];
  sources_succeeded: string[];
  errors: string[];
  api_status: {
    google_places: { configured: boolean; calls: number; description?: string };
    ny_dos: { configured: boolean; calls: number; description?: string };
    hunter: { configured: boolean; calls: number; description?: string };
    web_crawl: { configured: boolean; calls: number; description?: string };
  };
}

/**
 * Unified enrichment: contacts + research + AI summary in one call.
 * This is the single "Enrich Lead" action for the simplified UX.
 */
export async function enrichLeadAll(leadId: string): Promise<{
  lead_id: string;
  contacts: { phones_found: number; emails_found: number; website_found: boolean };
  research: { owner_names: string[]; year_established: string | null; website_scraped: boolean };
  ai_summary: { generated: boolean; description: string | null };
  errors: string[];
  enrichment_status: string;
  pipeline_stage: string;
  lead: ApiLead;  // Full updated lead — use this directly, no second API call needed
}> {
  const response = await fetchWithRetry(`${API_BASE_URL}/api/leads/${leadId}/enrich-all`, { method: 'POST' }, 1, 120000);
  if (!response.ok) throw new Error(`Failed to enrich lead: ${response.statusText}`);
  return response.json();
}

/**
 * Add an outreach attempt to a lead (legacy)
 */
export async function addOutreachAttempt(
  leadId: string,
  attempt: { method: string; outcome: string; notes?: string }
): Promise<{ status: string; attempt: OutreachAttempt }> {
  const response = await fetchWithRetry(`${API_BASE_URL}/api/leads/${leadId}/outreach`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(attempt),
  });
  if (!response.ok) throw new Error(`Failed to add outreach attempt: ${response.statusText}`);
  return response.json();
}

/**
 * Log an outreach event (Phase 5.3 - new pipeline system)
 */
export async function logOutreachEvent(
  leadId: string,
  event: { stage: string; method?: string; outcome?: string; notes?: string; next_follow_up?: string }
): Promise<{ status: string; event_id: number }> {
  const response = await fetchWithRetry(`${API_BASE_URL}/api/leads/${leadId}/outreach-event`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(event),
  });
  if (!response.ok) throw new Error(`Failed to log outreach event: ${response.statusText}`);
  return response.json();
}

/**
 * Get outreach events for a lead (Phase 5.3)
 */
export async function getOutreachEvents(leadId: string): Promise<{ lead_id: string; events: OutreachEvent[] }> {
  const response = await fetchWithRetry(`${API_BASE_URL}/api/leads/${leadId}/outreach-events`);
  if (!response.ok) throw new Error(`Failed to get outreach events: ${response.statusText}`);
  return response.json();
}

/**
 * Get leads with follow-ups due (Phase 5.3)
 */
export async function getFollowUpsDue(before?: string): Promise<{ count: number; leads: Array<Record<string, unknown>> }> {
  const params = before ? `?before=${before}` : '';
  const response = await fetchWithRetry(`${API_BASE_URL}/api/follow-ups${params}`);
  if (!response.ok) throw new Error(`Failed to get follow-ups: ${response.statusText}`);
  return response.json();
}

/**
 * Get change alerts (Phase 5.4)
 */
export async function getAlerts(limit: number = 50): Promise<{ count: number; alerts: ChangeAlert[] }> {
  const response = await fetchWithRetry(`${API_BASE_URL}/api/alerts?limit=${limit}`);
  if (!response.ok) throw new Error(`Failed to get alerts: ${response.statusText}`);
  return response.json();
}

/**
 * Dismiss a change alert (Phase 5.4)
 */
export async function dismissAlert(alertId: number): Promise<{ status: string }> {
  const response = await fetchWithRetry(`${API_BASE_URL}/api/alerts/${alertId}/dismiss`, { method: 'POST' });
  if (!response.ok) throw new Error(`Failed to dismiss alert: ${response.statusText}`);
  return response.json();
}

// getDueDiligence removed — backend endpoint does not exist yet.
// The DD tab in LeadDetail shows a "coming soon" toast instead.

/**
 * Enrichment progress status
 */
export interface EnrichmentProgress {
  running: boolean;
  phase: string;
  progress: number;
  total: number;
  completed: number;
  failed: number;
  dos_completed: number;
  dos_found: number;
  web_completed: number;
  web_found: number;
  current_lead: string | null;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  percent_complete: number;
  last_completed_at: string | null;
}

interface IngestionJobStatusRow {
  id: number;
  job_type: string;
  source?: string;
  status: string;
  total?: number | null;
  processed?: number | null;
  succeeded?: number | null;
  failed?: number | null;
  error?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
}

function idleEnrichmentProgress(): EnrichmentProgress {
  return {
    running: false,
    phase: '',
    progress: 0,
    total: 0,
    completed: 0,
    failed: 0,
    dos_completed: 0,
    dos_found: 0,
    web_completed: 0,
    web_found: 0,
    current_lead: null,
    started_at: null,
    finished_at: null,
    error: null,
    percent_complete: 0,
    last_completed_at: null,
  };
}

/**
 * Get enrichment progress status
 */
export async function getEnrichmentProgress(): Promise<EnrichmentProgress> {
  try {
    const response = await fetchWithRetry(
      `${API_BASE_URL}/api/v1/jobs?job_type=enrichment&limit=1`,
      {},
      1,
      15000,
    );
    if (!response.ok) {
      return idleEnrichmentProgress();
    }

    const jobs = (await response.json()) as IngestionJobStatusRow[];
    const latest = jobs?.[0];
    if (!latest) {
      return idleEnrichmentProgress();
    }

    const total = Number(latest.total || 0);
    const succeeded = Number(latest.succeeded || 0);
    const failed = Number(latest.failed || 0);
    const processedFromJob = Number(latest.processed || 0);
    const processed = processedFromJob > 0 ? processedFromJob : succeeded + failed;
    const running = latest.status === 'queued' || latest.status === 'running';
    const percentComplete = total > 0
      ? Math.max(0, Math.min(100, Math.round((processed / total) * 100)))
      : 0;

    return {
      running,
      phase: running ? latest.status : 'idle',
      progress: processed,
      total,
      completed: succeeded,
      failed,
      dos_completed: 0,
      dos_found: 0,
      web_completed: 0,
      web_found: 0,
      current_lead: null,
      started_at: latest.started_at || null,
      finished_at: latest.finished_at || null,
      error: latest.error || null,
      percent_complete: percentComplete,
      last_completed_at: latest.finished_at || null,
    };
  } catch {
    return idleEnrichmentProgress();
  }
}

/**
 * Start batch enrichment of top leads
 */
export async function startBatchEnrichment(limit: number = 100): Promise<{
  status: string;
  message: string;
  target_count: number;
  dispatch_mode?: string;
}> {
  const safeLimit = Math.max(0, limit);
  const requestedLimit = Math.max(1, safeLimit || 1);
  // Queue an enrichment job in the PostgreSQL-backed jobs API.
  // The worker path is still being converged, but this keeps one canonical contract.
  const response = await fetchWithRetry(
    `${API_BASE_URL}/api/v1/jobs/enrichment/start?limit=${requestedLimit}`,
    { method: 'POST' },
  );
  if (!response.ok) throw new Error(`Failed to queue enrichment job: ${response.statusText}`);
  const data = await response.json();
  const dispatchMode = data.dispatch_mode as string | undefined;
  const modeSuffix = dispatchMode === 'in_process'
    ? ' Running in local fallback mode.'
    : '';
  return {
    status: data.status || 'queued',
    message: `Queued enrichment job (target: top ${safeLimit || requestedLimit}).${modeSuffix}`,
    target_count: safeLimit || requestedLimit,
    dispatch_mode: dispatchMode,
  };
}

/**
 * Run revenue estimation on all leads (Phase 5.1)
 */
export async function estimateRevenue(): Promise<{ status: string; leads_updated: number }> {
  const response = await fetchWithRetry(`${API_BASE_URL}/api/estimate-revenue`, { method: 'POST' });
  if (!response.ok) throw new Error(`Failed to estimate revenue: ${response.statusText}`);
  return response.json();
}

/**
 * Refresh violations data (Phase 5.2)
 */
export async function refreshViolations(): Promise<{ status: string; leads_with_violations: number }> {
  const response = await fetchWithRetry(`${API_BASE_URL}/api/violations/refresh`, { method: 'POST' }, 0, 120000);
  if (!response.ok) throw new Error(`Failed to refresh violations: ${response.statusText}`);
  return response.json();
}

/**
 * Re-score all leads with V2 scoring (Phase 5.6)
 */
export async function rescoreLeads(): Promise<{ status: string; leads_rescored: number; leads_changed: number }> {
  const response = await fetchWithRetry(`${API_BASE_URL}/api/rescore`, { method: 'POST' });
  if (!response.ok) throw new Error(`Failed to rescore leads: ${response.statusText}`);
  return response.json();
}

/**
 * Check for data updates (Phase 5.4)
 */
export async function checkForUpdates(): Promise<{ status: string; alerts_created: number }> {
  // Legacy refresh/check-updates endpoint is being retired.
  return { status: 'unsupported', alerts_created: 0 };
}

/**
 * Get enrichment gap stats (1D)
 */
export interface EnrichmentGaps {
  total_leads: number;
  unenriched: number;
  breakdown: Record<string, number>;
}

export async function getEnrichmentGaps(): Promise<EnrichmentGaps> {
  // Fallback to derived metric from stats while legacy enrichment-gaps endpoint is retired.
  const stats = await fetchStats();
  const breakdown = stats.by_enrichment_status || {};
  const total = stats.total_leads || 0;
  const unenriched = (breakdown.none || 0) + (breakdown.pending || 0);
  return {
    total_leads: total,
    unenriched,
    breakdown,
  };
}

// ===========================================================================
// Agent API (AI Agent feature)
// ===========================================================================

// --- SSE Event types (discriminated union) ---

export type AgentSSEEvent =
  | { type: 'status'; data: string }
  | { type: 'keepalive'; data: string }
  | { type: 'tool_call'; data: { name: string; input: Record<string, unknown> } }
  | { type: 'partial'; data: string }
  | { type: 'leads'; data: AgentLeadRow[] }
  | { type: 'scripts'; data: AgentScriptRow[] }
  | { type: 'briefing_preview'; data: AgentBriefingPreview }
  | { type: 'rent_comparison'; data: AgentRentComparison[] }
  | { type: 'needs_confirmation'; data: AgentConfirmation }
  | { type: 'actions'; data: string[] }
  | { type: 'error'; data: string }
  | { type: 'done'; data: { conversation_id: string } };

export interface AgentLeadRow {
  lead_id: string;
  company_name: string;
  portfolio_size: number;
  total_units: number;
  score: number;
  estimated_annual_revenue: number;
  enrichment_status: string;
  phone: string | null;
  email: string | null;
  website: string | null;
  boros: string[];
  violations_per_unit: number;
  pipeline_stage: string;
}

export interface AgentScriptRow {
  lead_id: string;
  company_name: string;
  phone: string | null;
  owner_name: string | null;
  script: string;
}

export interface AgentRentComparison {
  lead_id: string;
  company_name: string;
  current_monthly_rent: number;
  refined_monthly_rent: number;
  delta_pct: number;
  current_annual_revenue: number;
  refined_annual_revenue: number;
  confidence: 'high' | 'medium' | 'low';
  sources: string[];
}

export interface AgentBriefingPreview {
  briefing_id: string;
  html: string;
  lead_count: number;
}

export interface AgentConfirmation {
  action_id: string;
  description: string;
  count: number;
}

export interface AgentChatRequest {
  message: string;
  conversation_id?: string;
  confirmation?: { action_id: string; confirmed: boolean };
}

export interface ConversationSummary {
  conversation_id: string;
  title: string;
  message_count: number;
  updated_at: string;
}

/**
 * Send a message to the agent and receive SSE events.
 * Returns an AbortController for cancellation and a callback-based event handler.
 */
export function agentChat(
  request: AgentChatRequest,
  onEvent: (event: AgentSSEEvent) => void,
  onError?: (error: Error) => void,
): AbortController {
  const controller = new AbortController();

  (async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/agent/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify(request),
        signal: controller.signal,
      });

      if (response.status === 401) {
        clearToken();
        window.dispatchEvent(new Event('auth:logout'));
        throw new Error('Session expired — please log in again');
      }

      if (!response.ok) {
        throw new Error(`Agent chat failed: ${response.status} ${response.statusText}`);
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error('No response body');

      const decoder = new TextDecoder();
      let buffer = '';
      // SSE parser state — MUST persist across chunk boundaries
      let currentEvent = '';
      let currentData = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || ''; // last element may be incomplete

        for (const line of lines) {
          const trimmed = line.replace(/\r$/, ''); // handle \r\n
          if (trimmed.startsWith('event: ') || trimmed.startsWith('event:')) {
            currentEvent = trimmed.slice(trimmed.indexOf(':') + 1).trim();
          } else if (trimmed.startsWith('data: ') || trimmed.startsWith('data:')) {
            // Append to currentData (SSE spec allows multi-line data)
            const dataContent = trimmed.slice(trimmed.indexOf(':') + 1).trim();
            currentData = currentData ? currentData + '\n' + dataContent : dataContent;
          } else if (trimmed === '' && currentEvent) {
            // Empty line = end of SSE message — dispatch
            try {
              let parsedData: unknown;
              try {
                parsedData = JSON.parse(currentData);
              } catch {
                parsedData = currentData;
              }
              onEvent({ type: currentEvent, data: parsedData } as AgentSSEEvent);
            } catch (e) {
              console.warn('Failed to parse SSE event:', currentEvent, currentData);
            }
            currentEvent = '';
            currentData = '';
          }
        }
      }

      // Dispatch any remaining event if stream ended without trailing newline
      if (currentEvent && currentData) {
        try {
          let parsedData: unknown;
          try {
            parsedData = JSON.parse(currentData);
          } catch {
            parsedData = currentData;
          }
          onEvent({ type: currentEvent, data: parsedData } as AgentSSEEvent);
        } catch (e) {
          console.warn('Failed to parse final SSE event:', currentEvent, currentData);
        }
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') return;
      if (onError) onError(err instanceof Error ? err : new Error(String(err)));
    }
  })();

  return controller;
}

/**
 * Get list of recent conversations.
 */
export async function getConversations(): Promise<ConversationSummary[]> {
  const response = await fetchWithRetry(`${API_BASE_URL}/api/agent/conversations`);
  if (!response.ok) throw new Error('Failed to fetch conversations');
  const data = await response.json();
  return data.conversations || [];
}

/**
 * Get full message history for a conversation.
 */
export async function getConversation(conversationId: string): Promise<{ conversation_id: string; messages: unknown[] }> {
  const response = await fetchWithRetry(`${API_BASE_URL}/api/agent/conversations/${conversationId}`);
  if (!response.ok) throw new Error('Failed to fetch conversation');
  return response.json();
}

// === Smart Lists ===

export interface SmartList {
  id: string;
  name: string;
  description: string;
  filters: Record<string, unknown>;
  pinned: boolean;
  auto_evaluate: boolean;
  evaluation_interval_hours: number;
  next_evaluation_at: string | null;
  last_evaluated_at: string | null;
  last_count: number;
  created_at: string;
  updated_at: string;
}

export interface SmartListEvaluation {
  list_id: string;
  name: string;
  total: number;
  previous_total: number;
  entered: number;
  exited: number;
  entered_ids: string[];
  exited_ids: string[];
}

export async function getSmartLists(): Promise<{ smart_lists: SmartList[] }> {
  return apiGet('/api/smart-lists');
}

export async function createSmartList(body: {
  name: string;
  description?: string;
  filters: Record<string, unknown>;
  pinned?: boolean;
  auto_evaluate?: boolean;
  evaluation_interval_hours?: number;
}): Promise<{ id: string; name: string; status: string }> {
  return apiMutate('/api/smart-lists', 'POST', body);
}

export async function updateSmartList(
  listId: string,
  body: {
    name?: string;
    description?: string;
    filters?: Record<string, unknown>;
    pinned?: boolean;
    auto_evaluate?: boolean;
    evaluation_interval_hours?: number;
  },
): Promise<{ id: string; status: string }> {
  return apiMutate(`/api/smart-lists/${listId}`, 'PATCH', body);
}

export async function deleteSmartList(listId: string): Promise<{ id: string; status: string }> {
  return apiMutate(`/api/smart-lists/${listId}`, 'DELETE');
}

export async function evaluateSmartList(listId: string): Promise<SmartListEvaluation> {
  return apiMutate(`/api/smart-lists/${listId}/evaluate`, 'POST');
}

export async function runDueAutoEvaluations(limit: number = 50): Promise<{
  status: string;
  evaluated_count: number;
  results: SmartListEvaluation[];
}> {
  return apiMutate(`/api/smart-lists/auto-evaluate/run?limit=${limit}`, 'POST');
}

// === Data Robustness APIs (v2) ===

export interface BuildingSearchResult {
  building_id: string;
  address: string;
  house_number: string;
  street_name: string;
  boro: string;
  units_res: number;
  building_class: string;
  building_type: string;
  lead_id: string;
  agent_name: string;
  owner_name: string;
  score: number;
  portfolio_size: number;
  total_units: number;
  status: string;
}

export interface DataHealthResponse {
  total_leads: number;
  total_buildings_registered: number;
  hpd_source_count: number | null;
  coverage_percent: number | null;
  last_refresh: {
    started_at: string;
    finished_at: string;
    status: string;
    leads_net_change: number;
    buildings_fetched: number;
  } | null;
  stale_buildings_count: number;
  buildings_without_contacts: number;
  lead_staleness: Record<string, number>;
  data_age_days: number | null;
  enrichment_coverage: Record<string, number>;
  distinct_entities_in_contacts: number;
  distinct_entities_in_leads: number;
  matched_entities: number;
  entity_coverage_ratio: number | null;
  coverage_ratio?: number | null;
  last_lead_generation: {
    started_at: string | null;
    finished_at: string | null;
    status: string;
  } | null;
  last_lead_generation_at?: string | null;
  warnings: string[];
}

/**
 * Search buildings by address. Returns buildings with their managing lead.
 */
export async function searchBuildings(address: string, limit: number = 20): Promise<{
  query: string;
  buildings: BuildingSearchResult[];
  total: number;
}> {
  const params = new URLSearchParams({ address, limit: limit.toString() });
  const response = await fetchWithRetry(`${API_BASE_URL}/api/buildings/search?${params}`);
  if (!response.ok) throw new Error(`Failed to search buildings: ${response.statusText}`);
  return response.json();
}

/**
 * Get data health metrics for the dashboard badge.
 */
export async function fetchDataHealth(): Promise<DataHealthResponse> {
  const response = await fetchWithRetry(`${API_BASE_URL}/api/v1/quality/data-health`);
  if (!response.ok) throw new Error(`Failed to fetch data health: ${response.statusText}`);
  return response.json();
}

/**
 * Log an outreach attempt from Call Mode.
 */
export async function logOutreachFromCallMode(
  leadId: string,
  outcome: string,
  notes?: string,
): Promise<void> {
  const response = await fetchWithRetry(`${API_BASE_URL}/api/leads/${leadId}/outreach`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      method: 'cold_call',
      outcome,
      notes: notes || '',
    }),
  });
  if (!response.ok) throw new Error('Failed to log outreach');
}
