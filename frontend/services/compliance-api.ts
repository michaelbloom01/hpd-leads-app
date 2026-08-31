import { clearToken, getAuthHeaders } from './auth';
import { API_BASE_URL } from './config';

export type ComplianceScope = 'portfolio' | 'parcels' | 'buildings';
export type ComplianceCoverageStatus = 'disabled' | 'schema_unavailable' | 'identity_unavailable' | 'not_checked' | 'partial' | 'complete';

export interface ComplianceRecord {
  id: string;
  source_system: string;
  source_record_key: string;
  record_type: string;
  category: string | null;
  violation_type: string | null;
  device_type: string | null;
  status: string | null;
  issue_date: string | null;
  description: string | null;
  source_url: string;
  source_updated_at: string | null;
  observed_at: string;
  identity_status: string;
  stale: boolean;
}

export interface ComplianceBalanceObservation {
  id: string;
  bin: string;
  category: string;
  scope: 'bin_category';
  amount_cents: number;
  source_url: string;
  source_updated_at: string | null;
  source_timestamp_raw: string | null;
  observed_at: string;
  reviewer: string;
  stale: boolean;
  amount_basis: 'manual_portal_observation';
}

export interface ComplianceBuilding {
  bin: string;
  bbl: string | null;
  address: string | null;
  records: ComplianceRecord[];
  reported_balance_cents: number | null;
  balance_observations: ComplianceBalanceObservation[];
  interest_status: 'unverified';
  lien_status: 'unverified';
  stale: boolean;
  source_check_status: 'not_checked' | 'checked';
  source_updated_at: string | null;
  observed_at: string | null;
}

export interface ComplianceProvenance {
  source_system: string;
  source_url: string;
  source_updated_at: string | null;
  observed_at: string | null;
  status: string;
}

export interface ComplianceResponse {
  scope: { type: string; id: string };
  enabled: boolean;
  identity_ready: boolean;
  as_of: string | null;
  source_updated_at: string | null;
  stale: boolean;
  coverage: {
    status: ComplianceCoverageStatus;
    physical_building_count: number;
    checked_building_count: number;
    records_count: number;
    active_records_count: number;
    balance_known_building_count: number;
    missing_balance_bin_count: number;
  };
  reported_balance_cents: number | null;
  estimated_penalty_cents: number | null;
  warnings: string[];
  buildings: ComplianceBuilding[];
  provenance: ComplianceProvenance[];
}

export class ComplianceApiError extends Error {
  constructor(message: string, public readonly status?: number) {
    super(message);
    this.name = 'ComplianceApiError';
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function validCount(value: unknown): boolean {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

function validMoney(value: unknown): boolean {
  return value === null || validCount(value);
}

function nullableText(value: unknown): boolean {
  return value === null || typeof value === 'string';
}

function validResponse(value: unknown): value is ComplianceResponse {
  if (!isObject(value) || !isObject(value.coverage) || !isObject(value.scope)) return false;
  const coverage = value.coverage;
  const counts = ['physical_building_count', 'checked_building_count', 'records_count', 'active_records_count', 'balance_known_building_count', 'missing_balance_bin_count'];
  if (typeof value.enabled !== 'boolean' || typeof value.identity_ready !== 'boolean' || typeof value.stale !== 'boolean'
    || !nullableText(value.as_of) || !nullableText(value.source_updated_at)
    || typeof value.scope.type !== 'string' || typeof value.scope.id !== 'string'
    || typeof coverage.status !== 'string' || !counts.every(key => validCount(coverage[key]))
    || !validMoney(value.reported_balance_cents) || !validMoney(value.estimated_penalty_cents)
    || !Array.isArray(value.buildings) || !Array.isArray(value.warnings) || !value.warnings.every(item => typeof item === 'string')
    || !Array.isArray(value.provenance)) return false;
  const validBuildings = value.buildings.every(building => isObject(building)
    && typeof building.bin === 'string' && /^\d{7}$/.test(building.bin)
    && (building.bbl === null || typeof building.bbl === 'string')
    && ['address', 'source_updated_at', 'observed_at'].every(key => nullableText(building[key]))
    && validMoney(building.reported_balance_cents) && typeof building.stale === 'boolean'
    && ['checked', 'not_checked'].includes(String(building.source_check_status))
    && Array.isArray(building.records) && building.records.every(record => isObject(record)
      && ['id', 'source_system', 'source_record_key', 'source_url', 'identity_status'].every(key => typeof record[key] === 'string')
      && ['category', 'violation_type', 'device_type', 'status', 'issue_date', 'description', 'source_updated_at'].every(key => nullableText(record[key]))
      && typeof record.observed_at === 'string'
      && typeof record.stale === 'boolean')
    && Array.isArray(building.balance_observations) && building.balance_observations.every(observation => isObject(observation)
      && ['id', 'bin', 'category', 'source_url', 'reviewer'].every(key => typeof observation[key] === 'string')
      && ['source_updated_at', 'source_timestamp_raw'].every(key => nullableText(observation[key]))
      && typeof observation.observed_at === 'string'
      && observation.scope === 'bin_category' && validCount(observation.amount_cents) && typeof observation.stale === 'boolean'));
  const validProvenance = value.provenance.every(source => isObject(source)
    && ['source_system', 'source_url', 'status'].every(key => typeof source[key] === 'string')
    && ['source_updated_at', 'observed_at'].every(key => nullableText(source[key])));
  return validBuildings && validProvenance;
}

export async function fetchCompliance(
  scope: ComplianceScope,
  id: string,
  signal?: AbortSignal,
): Promise<ComplianceResponse> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted) controller.abort();
  const timeout = window.setTimeout(abort, 30_000);
  try {
    const response = await fetch(`${API_BASE_URL}/api/v1/compliance/${scope}/${encodeURIComponent(id)}`, {
      headers: getAuthHeaders(),
      signal: controller.signal,
    });
    if (response.status === 401) {
      clearToken();
      window.dispatchEvent(new Event('auth:logout'));
      throw new ComplianceApiError('Your session expired. Sign in again to view compliance.', 401);
    }
    if (!response.ok) {
      throw new ComplianceApiError(
        response.status === 403 ? 'You do not have access to this compliance view.' : 'Compliance data is temporarily unavailable.',
        response.status,
      );
    }
    const data = await response.json();
    // A successful HTML fallback or a different API shape must never look like clean coverage.
    if (!validResponse(data)) {
      throw new ComplianceApiError('The compliance response could not be verified. Please try again.');
    }
    return data;
  } finally {
    window.clearTimeout(timeout);
    signal?.removeEventListener('abort', abort);
  }
}
