/**
 * Shared formatting utilities and constants used across components.
 * Single source of truth — DO NOT duplicate these in component files.
 */

// ---------------------------------------------------------------------------
// Borough constants
// ---------------------------------------------------------------------------

export const BOROUGHS = ['MANHATTAN', 'BROOKLYN', 'QUEENS', 'BRONX', 'STATEN ISLAND'] as const;

export const BOROUGH_SHORT: Record<string, string> = {
  'MANHATTAN': 'Man',
  'BROOKLYN': 'Bklyn',
  'QUEENS': 'Qns',
  'BRONX': 'Bronx',
  'STATEN ISLAND': 'SI',
};

export const BOROUGH_COLORS: Record<string, string> = {
  'MANHATTAN': '#2563eb',
  'BROOKLYN': '#7c3aed',
  'QUEENS': '#059669',
  'BRONX': '#ea580c',
  'STATEN ISLAND': '#6b7280',
};

// ---------------------------------------------------------------------------
// Pipeline stages
// ---------------------------------------------------------------------------

export const PIPELINE_STAGES = [
  { value: 'research', key: 'research', label: 'Research', color: '#475569', tailwind: 'bg-slate-600' },
  { value: 'first_contact', key: 'first_contact', label: 'First Contact', color: '#2563eb', tailwind: 'bg-blue-600' },
  { value: 'follow_up', key: 'follow_up', label: 'Follow-Up', color: '#4f46e5', tailwind: 'bg-indigo-600' },
  { value: 'meeting_scheduled', key: 'meeting_scheduled', label: 'Meeting Set', color: '#9333ea', tailwind: 'bg-purple-600' },
  { value: 'meeting_done', key: 'meeting_done', label: 'Meeting Done', color: '#7c3aed', tailwind: 'bg-violet-600' },
  { value: 'loi', key: 'loi', label: 'LOI', color: '#d97706', tailwind: 'bg-amber-600' },
  { value: 'due_diligence', key: 'due_diligence', label: 'Due Diligence', color: '#ea580c', tailwind: 'bg-orange-600' },
  { value: 'closed', key: 'closed', label: 'Closed', color: '#059669', tailwind: 'bg-emerald-600' },
] as const;

// ---------------------------------------------------------------------------
// Outreach statuses
// ---------------------------------------------------------------------------

export const OUTREACH_STATUSES = [
  { value: 'new', label: 'New', color: 'bg-gray-200 text-gray-700' },
  { value: 'contacted', label: 'Contacted', color: 'bg-blue-600 text-blue-100' },
  { value: 'interested', label: 'Interested', color: 'bg-emerald-600 text-emerald-100' },
  { value: 'not_interested', label: 'Not Interested', color: 'bg-amber-600 text-amber-100' },
  { value: 'closed', label: 'Closed', color: 'bg-purple-600 text-purple-100' },
] as const;

export const OUTREACH_METHODS = ['phone', 'email', 'linkedin', 'in_person', 'other'] as const;
export const OUTREACH_OUTCOMES = ['no_answer', 'left_voicemail', 'spoke_with_contact', 'sent_email', 'meeting_scheduled', 'not_interested', 'other'] as const;

export const OUTREACH_COLORS: Record<string, string> = {
  'new': '#94a3b8',
  'contacted': '#3b82f6',
  'interested': '#10b981',
  'not_interested': '#f87171',
  'closed': '#6b7280',
};

export const OUTREACH_LABELS: Record<string, string> = {
  'new': 'New',
  'contacted': 'Contacted',
  'interested': 'Interested',
  'not_interested': 'Not Interested',
  'closed': 'Closed',
};

// ---------------------------------------------------------------------------
// Enrichment statuses
// ---------------------------------------------------------------------------

export const ENRICHMENT_STATUSES = [
  { value: 'none', label: 'Not Enriched' },
  { value: 'partial', label: 'Partial' },
  { value: 'complete', label: 'Enriched' },
  { value: 'failed', label: 'No Data' },
] as const;

export const ENRICHMENT_COLORS: Record<string, string> = {
  'complete': '#10b981',
  'partial': '#fbbf24',
  'failed': '#f87171',
  'none': '#e2e8f0',
};

export const ENRICHMENT_LABELS: Record<string, string> = {
  'complete': 'Enriched',
  'partial': 'Partial',
  'failed': 'No Data',
  'none': 'Not Enriched',
};

// ---------------------------------------------------------------------------
// Score / chart colors
// ---------------------------------------------------------------------------

export const SCORE_COLORS = ['#cbd5e1', '#fbbf24', '#fb923c', '#34d399', '#059669'];

// ---------------------------------------------------------------------------
// Formatting functions
// ---------------------------------------------------------------------------

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
