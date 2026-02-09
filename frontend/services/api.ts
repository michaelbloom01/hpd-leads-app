/**
 * API service for communicating with the HPD Leads backend.
 */

// API base URL - uses environment variable in production, localhost in dev
const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

/**
 * Score breakdown showing component scores
 */
export interface ScoreBreakdown {
  portfolio: number;
  units: number;
  professional: number;
  contact: number;
  concentration: number;
}

/**
 * Contact info with source attribution
 */
export interface ContactWithSource {
  value: string;
  type: 'phone' | 'email';
  source: string;  // "google_places", "hunter", "web_crawl", etc.
  source_url: string | null;  // Clickable link to verify
  confidence: number;  // 0-100
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
  phone: string | null;  // Best phone (backwards compat)
  email: string | null;  // Best email (backwards compat)
  phones: ContactWithSource[];  // All phones with sources
  emails: ContactWithSource[];  // All emails with sources
  website: string | null;
  website_source: string | null;
  business_summary: string | null;
  linkedin_url: string | null;  // Company LinkedIn page
  linkedin_people: string[];  // Key people's LinkedIn profiles
  address: string | null;
  boro: string;
  boros: string[];
  // Building type composition from PLUTO
  building_types: BuildingTypeBreakdown | null;
  building_classes: Record<string, number>;  // Raw building class codes with counts
  score: number;
  score_breakdown: ScoreBreakdown | null;
  tags: string[];
  enrichment_status: string;
  outreach_status: string;
  notes: string | null;
  outreach_attempts?: OutreachAttempt[];
  // Entity classification
  contacts: Array<{ type: string; name: string; title: string; address?: string }>;
  entity_type: string;  // company, individual_agent, owner_operator, unknown
  company_name: string | null;
  primary_contact: string | null;
  primary_contact_title: string | null;
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
  ai_description?: string | null;  // AI-generated company summary
}

export interface OutreachAttempt {
  id: string;
  method: string;
  outcome: string;
  notes: string | null;
  timestamp: string;
}

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

/**
 * Get detailed statistics
 */
export async function fetchStats(): Promise<PipelineStats> {
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

/**
 * Deep research a lead - scrapes company website for detailed info
 */
export async function researchLead(leadId: string): Promise<CompanyResearch> {
  const response = await fetch(`${API_BASE_URL}/api/leads/${leadId}/research`, {
    method: 'POST',
  });
  
  if (!response.ok) {
    throw new Error(`Failed to research lead: ${response.statusText}`);
  }
  
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
  const response = await fetch(`${API_BASE_URL}/api/leads/${leadId}/ai-summary`, {
    method: 'POST',
  });
  
  if (!response.ok) {
    throw new Error(`Failed to generate AI summary: ${response.statusText}`);
  }
  
  return response.json();
}

/**
 * NY DOS Corporation info
 */
export interface DOSInfo {
  dos_id: string;
  entity_name: string;
  entity_type: string;
  formation_date: string | null;
  registered_agent: string | null;
  registered_address: string | null;
  lookup_url: string;  // Link to verify on NY DOS website
}

/**
 * Multi-source contact enrichment result
 */
export interface ContactEnrichmentResult {
  status: string;
  lead_id: string;
  company_name: string;
  phones: ContactWithSource[];
  emails: ContactWithSource[];
  website: string | null;
  website_source: string | null;
  dos_info: DOSInfo | null;  // NY DOS corporation data
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
 * Enrich a lead's contact info using multiple sources (Google Places, Hunter, Web Crawl)
 */
export async function enrichLeadContacts(leadId: string): Promise<ContactEnrichmentResult> {
  const response = await fetch(`${API_BASE_URL}/api/leads/${leadId}/enrich-contacts`, {
    method: 'POST',
  });
  
  if (!response.ok) {
    throw new Error(`Failed to enrich contacts: ${response.statusText}`);
  }
  
  return response.json();
}

/**
 * Get status of configured enrichment sources
 */
export async function getEnrichmentSources(): Promise<{
  google_places: { configured: boolean; calls?: number };
  hunter: { configured: boolean; calls?: number };
  web_crawl: { configured: boolean; calls?: number };
}> {
  const response = await fetch(`${API_BASE_URL}/api/enrichment/sources`);
  
  if (!response.ok) {
    throw new Error(`Failed to get enrichment sources: ${response.statusText}`);
  }
  
  return response.json();
}

/**
 * Add an outreach attempt to a lead
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
  
  if (!response.ok) {
    throw new Error(`Failed to add outreach attempt: ${response.statusText}`);
  }
  
  return response.json();
}

/**
 * Get outreach attempts for a lead
 */
export async function getOutreachAttempts(leadId: string): Promise<OutreachAttempt[]> {
  const response = await fetch(`${API_BASE_URL}/api/leads/${leadId}/outreach`);
  
  if (!response.ok) {
    throw new Error(`Failed to get outreach attempts: ${response.statusText}`);
  }
  
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
}

/**
 * Get enrichment progress status
 */
export async function getEnrichmentProgress(): Promise<EnrichmentProgress> {
  const response = await fetch(`${API_BASE_URL}/api/enrich/status`);
  
  if (!response.ok) {
    throw new Error(`Failed to get enrichment status: ${response.statusText}`);
  }
  
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
  const response = await fetch(`${API_BASE_URL}/api/enrich/batch?limit=${limit}`, {
    method: 'POST',
  });
  
  if (!response.ok) {
    throw new Error(`Failed to start batch enrichment: ${response.statusText}`);
  }
  
  return response.json();
}
