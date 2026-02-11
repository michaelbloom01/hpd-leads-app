/**
 * Shared formatting utilities used across components.
 */

export const BOROUGHS = ['MANHATTAN', 'BROOKLYN', 'QUEENS', 'BRONX', 'STATEN ISLAND'] as const;

export const BOROUGH_SHORT: Record<string, string> = {
  'MANHATTAN': 'Man',
  'BROOKLYN': 'Bklyn',
  'QUEENS': 'Qns',
  'BRONX': 'Bronx',
  'STATEN ISLAND': 'SI',
};

export const PIPELINE_STAGES = [
  { value: 'research', label: 'Research' },
  { value: 'first_contact', label: 'First Contact' },
  { value: 'meeting_scheduled', label: 'Meeting Scheduled' },
  { value: 'meeting_done', label: 'Meeting Done' },
  { value: 'loi', label: 'LOI' },
  { value: 'due_diligence', label: 'Due Diligence' },
  { value: 'closed', label: 'Closed' },
  { value: 'passed', label: 'Passed' },
] as const;

export const ENRICHMENT_STATUSES = [
  { value: 'none', label: 'Not Enriched' },
  { value: 'partial', label: 'Partial' },
  { value: 'complete', label: 'Enriched' },
  { value: 'failed', label: 'No Data' },
] as const;

export function formatCurrency(amount: number | undefined | null): string {
  if (amount == null || isNaN(amount)) return '\u2014';
  if (amount >= 1_000_000) return `$${(amount / 1_000_000).toFixed(1)}m`;
  if (amount >= 1_000) return `$${(amount / 1_000).toFixed(0)}k`;
  return `$${amount.toFixed(0)}`;
}

export function scoreColor(score: number): string {
  if (score >= 60) return 'text-emerald-600';
  if (score >= 40) return 'text-amber-600';
  return 'text-gray-400';
}

export function pipelineStageColor(stage: string): string {
  switch (stage) {
    case 'meeting_scheduled':
    case 'meeting_done':
      return 'bg-purple-50 text-purple-700';
    case 'loi':
    case 'due_diligence':
      return 'bg-emerald-50 text-emerald-700';
    case 'closed':
      return 'bg-gray-100 text-gray-600';
    case 'first_contact':
      return 'bg-blue-50 text-blue-700';
    case 'passed':
      return 'bg-red-50 text-red-600';
    default:
      return 'bg-gray-50 text-gray-500';
  }
}

export function enrichmentStatusBadge(status: string): { bg: string; text: string; label: string } {
  switch (status) {
    case 'complete':
      return { bg: 'bg-emerald-50', text: 'text-emerald-700', label: 'Enriched' };
    case 'partial':
      return { bg: 'bg-amber-50', text: 'text-amber-700', label: 'Partial' };
    case 'failed':
      return { bg: 'bg-red-50', text: 'text-red-600', label: 'No Data' };
    default:
      return { bg: 'bg-gray-50', text: 'text-gray-500', label: 'Not Enriched' };
  }
}

export function formatPipelineStage(stage: string): string {
  return stage.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export function formatRelativeTime(dateString: string | null): string {
  if (!dateString) return 'Never';
  const date = new Date(dateString);
  const now = new Date();
  const diff = now.getTime() - date.getTime();
  const minutes = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days = Math.floor(diff / 86400000);

  if (minutes < 1) return 'Just now';
  if (minutes < 60) return `${minutes}m ago`;
  if (hours < 24) return `${hours}h ago`;
  if (days < 7) return `${days}d ago`;
  return date.toLocaleDateString();
}
