/**
 * API service for communicating with the HPD Leads backend.
 * Updated: Phase 5 (revenue, violations, pipeline, scoring V2)
 */

// API base URL - uses environment variable in production, localhost in dev
const API_BASE_URL = (import.meta.env.VITE_API_URL || 'http://localhost:8000').trim().replace(/\\r\\n$/, '').replace(/[\r\n]+$/, '');

/**
 * Score breakdown showing component scores (V2)
 */
export interface ScoreBreakdown {
  portfolio: number;
  units: number;
  professional: number;
  contact: number;
  concentration: number;
  revenue: number;     // V2
  distress: number;    // V2
  deal_fit: number;    // V2
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
  min_portfolio?: number;
  boro?: string;
  has_phone?: boolean;
  has_email?: boolean;
  has_website?: boolean;
  entity_type?: string;
  enrichment_status?: string;
  outreach_status?: string;
  min_units?: number;
  limit?: number;
  offset?: number;
}): Promise<LeadsListResponse> {
  const searchParams = new URLSearchParams();
  
  if (params) {
    if (params.min_score !== undefined) searchParams.set('min_score', params.min_score.toString());
    if (params.min_portfolio !== undefined) searchParams.set('min_portfolio', params.min_portfolio.toString());
    if (params.boro) searchParams.set('boro', params.boro);
    if (params.has_phone !== undefined) searchParams.set('has_phone', params.has_phone.toString());
    if (params.has_email !== undefined) searchParams.set('has_email', params.has_email.toString());
    if (params.has_website !== undefined) searchParams.set('has_website', params.has_website.toString());
    if (params.entity_type) searchParams.set('entity_type', params.entity_type);
    if (params.enrichment_status) searchParams.set('enrichment_status', params.enrichment_status);
    if (params.outreach_status) searchParams.set('outreach_status', params.outreach_status);
    if (params.min_units !== undefined) searchParams.set('min_units', params.min_units.toString());
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
  if (!response.ok) throw new Error(`Failed to fetch lead: ${response.statusText}`);
  return response.json();
}

/**
 * Get detailed statistics
 */
export async function fetchStats(): Promise<PipelineStats> {
  const response = await fetch(`${API_BASE_URL}/api/stats`);
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

export async function fetchDataStatus(): Promise<DataStatus> {
  const response = await fetch(`${API_BASE_URL}/api/data-status`);
  if (!response.ok) throw new Error(`Failed to fetch data status: ${response.statusText}`);
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
}> {
  const params = full ? '?full=true' : '';
  const response = await fetch(`${API_BASE_URL}/api/refresh${params}`, { method: 'POST' });
  if (!response.ok) throw new Error(`Failed to refresh pipeline: ${response.statusText}`);
  return response.json();
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
  const response = await fetch(`${API_BASE_URL}/api/leads/${leadId}`, {
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
  const response = await fetch(`${API_BASE_URL}/api/enrich`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lead_ids: leadIds }),
  });
  if (!response.ok) throw new Error(`Failed to enrich leads: ${response.statusText}`);
  return response.json();
}

/**
 * Deep research a lead
 */
export async function researchLead(leadId: string): Promise<CompanyResearch> {
  const response = await fetch(`${API_BASE_URL}/api/leads/${leadId}/research`, { method: 'POST' });
  if (!response.ok) throw new Error(`Failed to research lead: ${response.statusText}`);
  return response.json();
}

/**
 * Generate AI summary for a lead
 */
export async function generateAiSummary(leadId: string): Promise<{
  status: string;
  lead_id: string;
  ai_description: string | null;
  message?: string;
}> {
  const response = await fetch(`${API_BASE_URL}/api/leads/${leadId}/ai-summary`, { method: 'POST' });
  if (!response.ok) throw new Error(`Failed to generate AI summary: ${response.statusText}`);
  return response.json();
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
 * Enrich a lead's contact info using multiple sources
 */
export async function enrichLeadContacts(leadId: string): Promise<ContactEnrichmentResult> {
  const response = await fetch(`${API_BASE_URL}/api/leads/${leadId}/enrich-contacts`, { method: 'POST' });
  if (!response.ok) throw new Error(`Failed to enrich contacts: ${response.statusText}`);
  return response.json();
}

/**
 * Add an outreach attempt to a lead (legacy)
 */
export async function addOutreachAttempt(
  leadId: string,
  attempt: { method: string; outcome: string; notes?: string }
): Promise<{ status: string; attempt: OutreachAttempt }> {
  const response = await fetch(`${API_BASE_URL}/api/leads/${leadId}/outreach`, {
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
  const response = await fetch(`${API_BASE_URL}/api/leads/${leadId}/outreach-event`, {
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
  const response = await fetch(`${API_BASE_URL}/api/leads/${leadId}/outreach-events`);
  if (!response.ok) throw new Error(`Failed to get outreach events: ${response.statusText}`);
  return response.json();
}

/**
 * Get leads with follow-ups due (Phase 5.3)
 */
export async function getFollowUpsDue(before?: string): Promise<{ count: number; leads: Array<Record<string, unknown>> }> {
  const params = before ? `?before=${before}` : '';
  const response = await fetch(`${API_BASE_URL}/api/follow-ups${params}`);
  if (!response.ok) throw new Error(`Failed to get follow-ups: ${response.statusText}`);
  return response.json();
}

/**
 * Get change alerts (Phase 5.4)
 */
export async function getAlerts(limit: number = 50): Promise<{ count: number; alerts: ChangeAlert[] }> {
  const response = await fetch(`${API_BASE_URL}/api/alerts?limit=${limit}`);
  if (!response.ok) throw new Error(`Failed to get alerts: ${response.statusText}`);
  return response.json();
}

/**
 * Dismiss a change alert (Phase 5.4)
 */
export async function dismissAlert(alertId: number): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE_URL}/api/alerts/${alertId}/dismiss`, { method: 'POST' });
  if (!response.ok) throw new Error(`Failed to dismiss alert: ${response.statusText}`);
  return response.json();
}

/**
 * Generate due diligence report (Phase 5.5)
 */
export async function getDueDiligence(leadId: string): Promise<{
  lead_id: string;
  company_name: string;
  report_markdown: string;
  data: Record<string, unknown>;
  comparables: Array<Record<string, unknown>>;
}> {
  const response = await fetch(`${API_BASE_URL}/api/leads/${leadId}/due-diligence`);
  if (!response.ok) throw new Error(`Failed to generate due diligence: ${response.statusText}`);
  return response.json();
}

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

/**
 * Get enrichment progress status
 */
export async function getEnrichmentProgress(): Promise<EnrichmentProgress> {
  const response = await fetch(`${API_BASE_URL}/api/enrich/status`);
  if (!response.ok) throw new Error(`Failed to get enrichment status: ${response.statusText}`);
  return response.json();
}

/**
 * Start batch enrichment of top leads
 */
export async function startBatchEnrichment(limit: number = 100): Promise<{
  status: string;
  message: string;
  target_count: number;
}> {
  const response = await fetch(`${API_BASE_URL}/api/enrich/batch?limit=${limit}`, { method: 'POST' });
  if (!response.ok) throw new Error(`Failed to start batch enrichment: ${response.statusText}`);
  return response.json();
}

/**
 * Run revenue estimation on all leads (Phase 5.1)
 */
export async function estimateRevenue(): Promise<{ status: string; leads_updated: number }> {
  const response = await fetch(`${API_BASE_URL}/api/estimate-revenue`, { method: 'POST' });
  if (!response.ok) throw new Error(`Failed to estimate revenue: ${response.statusText}`);
  return response.json();
}

/**
 * Refresh violations data (Phase 5.2)
 */
export async function refreshViolations(): Promise<{ status: string; leads_with_violations: number }> {
  const response = await fetch(`${API_BASE_URL}/api/violations/refresh`, { method: 'POST' });
  if (!response.ok) throw new Error(`Failed to refresh violations: ${response.statusText}`);
  return response.json();
}

/**
 * Re-score all leads with V2 scoring (Phase 5.6)
 */
export async function rescoreLeads(): Promise<{ status: string; leads_rescored: number; leads_changed: number }> {
  const response = await fetch(`${API_BASE_URL}/api/rescore`, { method: 'POST' });
  if (!response.ok) throw new Error(`Failed to rescore leads: ${response.statusText}`);
  return response.json();
}

/**
 * Check for data updates (Phase 5.4)
 */
export async function checkForUpdates(): Promise<{ status: string; alerts_created: number }> {
  const response = await fetch(`${API_BASE_URL}/api/refresh/check-updates`, { method: 'POST' });
  if (!response.ok) throw new Error(`Failed to check for updates: ${response.statusText}`);
  return response.json();
}
