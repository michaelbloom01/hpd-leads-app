import { clearToken, getAuthHeaders } from './auth';
import { API_BASE_URL } from './config';
import { ComplianceApiError } from './compliance-api';

export const BALANCE_PORTAL_URL = 'https://www.nyc.gov/assets/buildings/html/Unpaid_Violations_Search.html';
export interface BalanceInput {
  bin: string; category: 'LL152'; scope: 'bin_category'; amount_cents: number;
  source_url: string; source_updated_at: null; source_timestamp_raw: string;
  observed_at: string; evidence_note: string;
}
export interface BalanceResult { evidence: BalanceInput & { id: string; reviewer: string }; created?: boolean; dry_run?: boolean; writes?: number }

export function dollarsToCents(value: string): number | null {
  if (!/^\d{1,11}(\.\d{1,2})?$/.test(value.trim())) return null;
  const [whole, decimal = ''] = value.trim().split('.');
  const cents = Number(whole) * 100 + Number(decimal.padEnd(2, '0'));
  return Number.isSafeInteger(cents) && cents <= 1_000_000_000_000 ? cents : null;
}

export async function submitBalanceEvidence(input: BalanceInput, capture = false): Promise<BalanceResult> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 30_000);
  try {
    const response = await fetch(`${API_BASE_URL}/api/v1/compliance/balance-evidence${capture ? '?confirm_execute=true' : '/preview'}`, {
      method: 'POST', headers: { ...getAuthHeaders(), 'Content-Type': 'application/json' }, body: JSON.stringify(input), signal: controller.signal,
    });
    if (response.status === 401) { clearToken(); window.dispatchEvent(new Event('auth:logout')); }
    if (!response.ok) throw new ComplianceApiError(response.status === 403 ? 'An administrator is required to capture balance evidence.' : response.status === 422 ? 'Check the amount, observation date and source note. The BIN must have a verified identity.' : 'Balance evidence could not be saved or previewed. Reload saved evidence before retrying a capture.', response.status);
    const data = await response.json();
    if (!data || !data.evidence || data.evidence.bin !== input.bin || data.evidence.amount_cents !== input.amount_cents
      || new Date(data.evidence.observed_at).getTime() !== new Date(input.observed_at).getTime()
      || typeof data.evidence.id !== 'string' || typeof data.evidence.reviewer !== 'string'
      || (capture ? typeof data.created !== 'boolean' : data.dry_run !== true || data.writes !== 0)) {
      throw new ComplianceApiError('The balance evidence response could not be verified. Reload saved data before retrying.');
    }
    return data;
  } finally { window.clearTimeout(timeout); }
}
