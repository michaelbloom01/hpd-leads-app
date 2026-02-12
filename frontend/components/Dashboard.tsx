
import React, { useState, useEffect, useCallback } from 'react';
import { toast } from 'react-hot-toast';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
  PieChart, Pie,
} from 'recharts';
import { 
  fetchLeads, 
  fetchStats,
  fetchDataStatus,
  ApiLead, 
  PipelineStatus,
  PipelineStats,
  DataStatus,
  getEnrichmentProgress,
  startBatchEnrichment,
  getFollowUpsDue,
  EnrichmentProgress,
  EnrichmentGaps,
  getEnrichmentGaps,
  checkHealth,
} from '../services/api';

interface DashboardProps {
  onSelectLead?: (lead: ApiLead) => void;
  onNavigateToLeads?: () => void;
}

const PIPELINE_STAGES = [
  { key: 'research', label: 'Research', color: '#475569', tailwind: 'bg-slate-600' },
  { key: 'first_contact', label: 'Contact', color: '#2563eb', tailwind: 'bg-blue-600' },
  { key: 'follow_up', label: 'Follow-Up', color: '#4f46e5', tailwind: 'bg-indigo-600' },
  { key: 'meeting_scheduled', label: 'Meeting Set', color: '#9333ea', tailwind: 'bg-purple-600' },
  { key: 'meeting_done', label: 'Meeting Done', color: '#7c3aed', tailwind: 'bg-violet-600' },
  { key: 'loi', label: 'LOI', color: '#d97706', tailwind: 'bg-amber-600' },
  { key: 'due_diligence', label: 'Due Diligence', color: '#ea580c', tailwind: 'bg-orange-600' },
  { key: 'closed', label: 'Closed', color: '#059669', tailwind: 'bg-emerald-600' },
];

const BOROUGH_COLORS: Record<string, string> = {
  'MANHATTAN': '#2563eb',
  'BROOKLYN': '#7c3aed',
  'QUEENS': '#059669',
  'BRONX': '#ea580c',
  'STATEN ISLAND': '#6b7280',
};

const SCORE_COLORS = ['#cbd5e1', '#fbbf24', '#fb923c', '#34d399', '#059669'];

const OUTREACH_COLORS: Record<string, string> = {
  'new': '#94a3b8',
  'contacted': '#3b82f6',
  'interested': '#10b981',
  'not_interested': '#f87171',
  'closed': '#6b7280',
};

const ENRICHMENT_COLORS: Record<string, string> = {
  'complete': '#10b981',
  'partial': '#fbbf24',
  'failed': '#f87171',
  'none': '#e2e8f0',
};

const OUTREACH_LABELS: Record<string, string> = {
  'new': 'New',
  'contacted': 'Contacted',
  'interested': 'Interested',
  'not_interested': 'Not Interested',
  'closed': 'Closed',
};

const ENRICHMENT_LABELS: Record<string, string> = {
  'complete': 'Enriched',
  'partial': 'Partial',
  'failed': 'No Data',
  'none': 'Not Enriched',
};

const formatCurrency = (amount: number | undefined | null): string => {
  if (amount == null || isNaN(amount)) return '—';
  if (amount >= 1_000_000) return `$${(amount / 1_000_000).toFixed(1)}m`;
  if (amount >= 1_000) return `$${(amount / 1_000).toFixed(0)}k`;
  return `$${amount.toFixed(0)}`;
};

const scoreColor = (score: number): string => {
  if (score >= 60) return 'text-emerald-600';
  if (score >= 40) return 'text-amber-600';
  return 'text-gray-400';
};


const Dashboard: React.FC<DashboardProps> = ({ onSelectLead, onNavigateToLeads }) => {
  const [topLeads, setTopLeads] = useState<ApiLead[]>([]);
  const [readyToContactLeads, setReadyToContactLeads] = useState<ApiLead[]>([]);
  const [showReadyDrawer, setShowReadyDrawer] = useState(false);
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [stats, setStats] = useState<PipelineStats | null>(null);
  const [enrichmentStatus, setEnrichmentStatus] = useState<EnrichmentProgress | null>(null);
  const [followUpsDue, setFollowUpsDue] = useState<{ count: number; leads: Array<Record<string, unknown>> } | null>(null);
  const [loading, setLoading] = useState(true);
  const [startingEnrichment, setStartingEnrichment] = useState(false);
  const [dataStatus, setDataStatus] = useState<DataStatus | null>(null);
  const [enrichmentGaps, setEnrichmentGaps] = useState<EnrichmentGaps | null>(null);
  const [backendStarting, setBackendStarting] = useState(false);

  // Pipeline drawer state
  const [selectedStage, setSelectedStage] = useState<string | null>(null);
  const [drawerLeads, setDrawerLeads] = useState<ApiLead[]>([]);
  const [drawerLoading, setDrawerLoading] = useState(false);

  const loadData = useCallback(async () => {
    try {
      const withTimeout = <T,>(promise: Promise<T>, ms: number): Promise<T> => {
        return Promise.race([
          promise,
          new Promise<T>((_, reject) => setTimeout(() => reject(new Error('Request timeout')), ms)),
        ]);
      };

      const [leadsResult, statsResult, enrichResult, followUpsResult, dataStatusResult, gapsResult] = await Promise.allSettled([
        withTimeout(fetchLeads({ limit: 100, min_portfolio: 10 }), 15000),
        withTimeout(fetchStats(), 15000),
        withTimeout(getEnrichmentProgress(), 10000),
        withTimeout(getFollowUpsDue(), 10000),
        withTimeout(fetchDataStatus(), 10000),
        withTimeout(getEnrichmentGaps(), 10000),
      ]);
      
      if (leadsResult.status === 'fulfilled') {
        const sortedByScore = [...leadsResult.value.leads].sort((a, b) => b.score - a.score);
        setTopLeads(sortedByScore);
        
        const contactable = sortedByScore.filter(l => 
          (l.phone || l.email) && 
          l.outreach_status === 'new' &&
          l.portfolio_size >= 10
        );
        setReadyToContactLeads(contactable);
      } else {
        console.error('Failed to load leads:', leadsResult.reason);
      }
      
      if (statsResult.status === 'fulfilled') {
        const statsData = statsResult.value;
        setStatus({
          total_leads: statsData.total_leads,
          last_refresh: statsData.last_refresh,
          enriched_count: statsData.with_phone + statsData.with_email,
          top_score: statsData.top_score || 0,
        });
        setStats(statsData);
      } else {
        console.error('Failed to load stats:', statsResult.reason);
      }
      
      if (enrichResult.status === 'fulfilled') {
        setEnrichmentStatus(enrichResult.value);
      }

      if (followUpsResult.status === 'fulfilled') {
        setFollowUpsDue(followUpsResult.value);
      }

      if (dataStatusResult.status === 'fulfilled') {
        setDataStatus(dataStatusResult.value);
      }

      if (gapsResult.status === 'fulfilled') {
        setEnrichmentGaps(gapsResult.value);
      }
    } catch (err) {
      console.error('Failed to load dashboard data:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  // Health check before first data load
  useEffect(() => {
    let cancelled = false;
    let timerId: ReturnType<typeof setTimeout>;
    
    const pollHealth = async () => {
      const health = await checkHealth();
      if (cancelled) return;
      if (health.status === 'starting') {
        setBackendStarting(true);
        timerId = setTimeout(pollHealth, 5000);
      } else {
        setBackendStarting(false);
        loadData();
      }
    };
    
    pollHealth();
    return () => { cancelled = true; clearTimeout(timerId); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (backendStarting) return;
    
    const interval = setInterval(async () => {
      try {
        const enrichData = await Promise.race([
          getEnrichmentProgress(),
          new Promise<never>((_, reject) => setTimeout(() => reject(new Error('Poll timeout')), 8000)),
        ]);
        setEnrichmentStatus(enrichData);
        
        if (enrichData.finished_at && !enrichData.running) {
          loadData();
        }
      } catch {
        // Silently ignore poll failures
      }
    }, 30000);
    
    return () => clearInterval(interval);
  }, [loadData, backendStarting]);

  const handleStartEnrichment = async () => {
    setStartingEnrichment(true);
    try {
      await startBatchEnrichment(500);
      const enrichData = await getEnrichmentProgress();
      setEnrichmentStatus(enrichData);
      toast.success('Enrichment started for top 500 leads');
    } catch {
      toast.error('Failed to start enrichment');
    } finally {
      setStartingEnrichment(false);
    }
  };

  // Pipeline stage click handler — fetch leads for that stage
  const handleStageClick = async (stageKey: string) => {
    if (selectedStage === stageKey) {
      // Clicking same stage again closes drawer
      setSelectedStage(null);
      setDrawerLeads([]);
      return;
    }
    setSelectedStage(stageKey);
    setDrawerLoading(true);
    try {
      const result = await fetchLeads({ pipeline_stage: stageKey, limit: 50, sort_by: 'score', sort_dir: 'desc' });
      setDrawerLeads(result.leads);
    } catch {
      setDrawerLeads([]);
    } finally {
      setDrawerLoading(false);
    }
  };

  // ─── Derived data ────────────────────────────────────────────

  // Pipeline stage counts
  const pipelineCounts: Record<string, number> = {};
  PIPELINE_STAGES.forEach(s => { pipelineCounts[s.key] = 0; });
  if (stats?.by_pipeline_stage) {
    Object.entries(stats.by_pipeline_stage).forEach(([stage, count]) => {
      if (pipelineCounts[stage] !== undefined) pipelineCounts[stage] = count;
    });
  }
  const maxStageCount = Math.max(...Object.values(pipelineCounts), 1);
  const leadsInPipeline = Object.entries(pipelineCounts)
    .filter(([key]) => key !== 'research')
    .reduce((sum, [, count]) => sum + count, 0);

  // Pipeline revenue
  const pipelineRevenue = topLeads
    .filter(l => l.pipeline_stage && l.pipeline_stage !== 'research')
    .reduce((sum, l) => sum + (l.estimated_annual_revenue || 0), 0);

  // Total estimated revenue
  const totalTargetRevenue = topLeads.reduce((sum, l) => sum + (l.estimated_annual_revenue || 0), 0);

  // Contact rate
  const contactRate = stats && stats.total_leads > 0
    ? ((stats.with_phone + stats.with_email) / stats.total_leads * 100)
    : 0;

  // Follow-ups breakdown
  const followUpsOverdue = (followUpsDue?.leads || []).filter((l) => {
    const d = l.next_follow_up ? new Date(l.next_follow_up as string) : null;
    return d && d < new Date(new Date().toDateString());
  }).length;
  const followUpsDueToday = (followUpsDue?.leads || []).filter((l) => {
    const d = l.next_follow_up ? new Date(l.next_follow_up as string) : null;
    return d && d.toDateString() === new Date().toDateString();
  }).length;

  // Borough chart data
  const boroughData = stats?.by_borough
    ? Object.entries(stats.by_borough)
        .map(([name, value]) => ({ name: name.charAt(0) + name.slice(1).toLowerCase(), value, fullName: name }))
        .sort((a, b) => b.value - a.value)
    : [];

  // Score distribution chart data
  const scoreData = stats?.score_distribution
    ? Object.entries(stats.score_distribution)
        .map(([bucket, count]) => ({ name: bucket, value: count }))
        .sort((a, b) => {
          const order = ['0-20', '20-40', '40-60', '60-80', '80-100'];
          return order.indexOf(a.name) - order.indexOf(b.name);
        })
    : [];

  // Outreach donut data
  const outreachData = stats?.by_outreach_status
    ? Object.entries(stats.by_outreach_status)
        .filter(([, v]) => v > 0)
        .map(([key, value]) => ({ name: OUTREACH_LABELS[key] || key, value, key }))
    : [];

  // Enrichment donut data
  const enrichmentData = stats?.by_enrichment_status
    ? Object.entries(stats.by_enrichment_status)
        .filter(([, v]) => v > 0)
        .map(([key, value]) => ({ name: ENRICHMENT_LABELS[key] || key, value, key }))
    : [];

  // ─── Renders ─────────────────────────────────────────────────

  if (backendStarting) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center space-y-4">
        <div className="w-12 h-12 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
        <h2 className="text-lg font-semibold text-blue-600">Backend is starting up</h2>
        <p className="text-gray-500 text-sm max-w-md">
          This usually takes 1-2 minutes after inactivity. The dashboard will load automatically once the server is ready.
        </p>
        <p className="text-gray-400 text-xs">Polling every 5s...</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="space-y-6 animate-pulse">
        <div className="h-12 bg-gray-100 rounded-2xl" />
        <div className="h-28 bg-gray-100 rounded-2xl" />
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {[...Array(4)].map((_, i) => <div key={i} className="h-24 bg-gray-100 rounded-2xl" />)}
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="h-64 bg-gray-100 rounded-2xl" />
          <div className="h-64 bg-gray-100 rounded-2xl" />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6 animate-in fade-in duration-500">

      {/* ── Section 1: Today's Actions ─────────────────────────── */}
      <div className="bg-white shadow-sm border border-gray-200 rounded-2xl px-5 py-3">
        <div className="flex flex-wrap items-center gap-4 md:gap-6">
          <h3 className="text-xs font-bold text-gray-400 uppercase tracking-wider mr-2">Today</h3>

          {/* Follow-ups overdue */}
          {followUpsOverdue > 0 && (
            <div className="flex items-center gap-2">
              <span className="flex items-center justify-center w-7 h-7 rounded-full bg-rose-100 text-rose-700 text-sm font-bold">{followUpsOverdue}</span>
              <span className="text-sm text-rose-700 font-medium">overdue follow-up{followUpsOverdue !== 1 ? 's' : ''}</span>
            </div>
          )}

          {/* Follow-ups due today */}
          {followUpsDueToday > 0 && (
            <div className="flex items-center gap-2">
              <span className="flex items-center justify-center w-7 h-7 rounded-full bg-amber-100 text-amber-700 text-sm font-bold">{followUpsDueToday}</span>
              <span className="text-sm text-amber-700 font-medium">due today</span>
            </div>
          )}

          {/* No follow-ups */}
          {followUpsOverdue === 0 && followUpsDueToday === 0 && (
            <div className="flex items-center gap-2">
              <span className="flex items-center justify-center w-7 h-7 rounded-full bg-emerald-100 text-emerald-700 text-sm font-bold">
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" d="M5 13l4 4L19 7" /></svg>
              </span>
              <span className="text-sm text-emerald-700 font-medium">No follow-ups due</span>
            </div>
          )}

          <div className="h-5 w-px bg-gray-200 hidden md:block" />

          {/* Ready to contact -- clickable */}
          <button
            onClick={() => setShowReadyDrawer(!showReadyDrawer)}
            className="flex items-center gap-2 hover:bg-blue-50 rounded-lg px-2 py-1 -mx-2 -my-1 transition-colors"
          >
            <span className="flex items-center justify-center w-7 h-7 rounded-full bg-blue-100 text-blue-700 text-sm font-bold">{readyToContactLeads.length}</span>
            <span className="text-sm text-gray-600">ready to contact</span>
            <svg className={`w-3.5 h-3.5 text-gray-400 transition-transform ${showReadyDrawer ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7" /></svg>
          </button>

          {/* Enrichment running indicator */}
          {enrichmentStatus?.running && (
            <>
              <div className="h-5 w-px bg-gray-200 hidden md:block" />
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 bg-blue-500 rounded-full animate-pulse" />
                <span className="text-xs text-gray-400">Enriching... {enrichmentStatus.percent_complete}%</span>
              </div>
            </>
          )}
        </div>

        {/* Ready to Contact Preview Drawer */}
        {showReadyDrawer && readyToContactLeads.length > 0 && (
          <div className="mt-3 pt-3 border-t border-gray-200 animate-in slide-in-from-top duration-200">
            <div className="flex items-center justify-between mb-2">
              <h4 className="text-xs font-bold text-gray-500 uppercase tracking-wider">Top Ready Leads</h4>
              <button
                onClick={() => { setShowReadyDrawer(false); onNavigateToLeads?.(); }}
                className="text-xs text-blue-600 hover:text-blue-500 font-medium flex items-center gap-1 transition-colors"
              >
                View all {readyToContactLeads.length} in Leads
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 7l5 5m0 0l-5 5m5-5H6" /></svg>
              </button>
            </div>
            <div className="space-y-1.5 max-h-72 overflow-y-auto">
              {readyToContactLeads.slice(0, 10).map((lead) => (
                <div
                  key={lead.lead_id}
                  onClick={() => onSelectLead?.(lead)}
                  className="flex items-center justify-between p-2.5 rounded-lg hover:bg-blue-50/60 cursor-pointer transition-colors group"
                >
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-900 truncate group-hover:text-blue-700 transition-colors">{lead.company_name || lead.agent_name || lead.owner_name}</p>
                    <p className="text-[10px] text-gray-400">{lead.portfolio_size} bldgs &middot; {(lead.total_units || 0).toLocaleString()} units &middot; Score {lead.score.toFixed(0)}</p>
                  </div>
                  <div className="flex items-center gap-1.5 ml-3 flex-shrink-0">
                    {lead.phone && <span className="px-1.5 py-0.5 bg-emerald-50 text-emerald-600 text-[9px] rounded font-medium">Phone</span>}
                    {lead.email && <span className="px-1.5 py-0.5 bg-blue-50 text-blue-600 text-[9px] rounded font-medium">Email</span>}
                  </div>
                </div>
              ))}
            </div>
            {readyToContactLeads.length > 10 && (
              <div className="mt-2 pt-2 border-t border-gray-100 text-center">
                <button
                  onClick={() => { setShowReadyDrawer(false); onNavigateToLeads?.(); }}
                  className="text-xs text-blue-600 hover:text-blue-500 font-medium transition-colors"
                >
                  + {readyToContactLeads.length - 10} more leads &rarr;
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── Section 2: Deal Pipeline ───────────────────────────── */}
      <div className="bg-white shadow-sm border border-gray-200 rounded-2xl p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-xs font-bold text-gray-400 uppercase tracking-wider">Deal Pipeline</h3>
          <span className="text-xs text-gray-400">
            {leadsInPipeline} active {leadsInPipeline === 1 ? 'deal' : 'deals'}
            {pipelineRevenue > 0 && ` \u2022 ${formatCurrency(pipelineRevenue)}/yr est. fee`}
          </span>
        </div>
        <div className="flex gap-1.5">
          {PIPELINE_STAGES.map((stage) => {
            const count = pipelineCounts[stage.key] || 0;
            const isSelected = selectedStage === stage.key;
            const heightPct = count > 0 ? Math.max(40, (count / maxStageCount) * 100) : 30;
            return (
              <button
                key={stage.key}
                onClick={() => handleStageClick(stage.key)}
                className={`flex-1 group transition-all duration-200 rounded-xl overflow-hidden ${
                  isSelected ? 'ring-2 ring-offset-2 ring-blue-500' : ''
                }`}
              >
                <div
                  className={`${count > 0 ? stage.tailwind : 'bg-gray-100'} rounded-xl px-2 text-center transition-all duration-200 hover:opacity-90 cursor-pointer flex flex-col items-center justify-center`}
                  style={{ minHeight: `${heightPct}px`, height: `${Math.max(64, heightPct * 0.8)}px` }}
                >
                  <div className={`text-lg font-bold font-mono ${count > 0 ? 'text-white' : 'text-gray-400'}`}>{count}</div>
                  <div className={`text-[9px] uppercase tracking-wider font-medium ${count > 0 ? 'text-white/70' : 'text-gray-400'}`}>{stage.label}</div>
                </div>
              </button>
            );
          })}
        </div>

        {/* Pipeline Drawer */}
        {selectedStage && (
          <div className="mt-4 pt-4 border-t border-gray-200 animate-in slide-in-from-top duration-300">
            <div className="flex items-center justify-between mb-3">
              <h4 className="text-sm font-bold text-gray-700">
                {PIPELINE_STAGES.find(s => s.key === selectedStage)?.label} — {drawerLeads.length} lead{drawerLeads.length !== 1 ? 's' : ''}
              </h4>
              <button
                onClick={() => { setSelectedStage(null); setDrawerLeads([]); }}
                className="p-1 rounded-lg hover:bg-gray-100 text-gray-400 hover:text-gray-600 transition-colors"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" /></svg>
              </button>
            </div>
            {drawerLoading ? (
              <div className="flex items-center justify-center py-8">
                <div className="w-6 h-6 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
              </div>
            ) : drawerLeads.length === 0 ? (
              <p className="text-sm text-gray-400 py-4 text-center">No leads in this stage</p>
            ) : (
              <div className="overflow-x-auto max-h-80 overflow-y-auto">
                <table className="w-full">
                  <thead className="sticky top-0 bg-white z-10">
                    <tr className="text-[10px] text-gray-400 uppercase tracking-wider border-b border-gray-100">
                      <th className="text-left py-2 px-3">Company</th>
                      <th className="text-right py-2 px-3">Units</th>
                      <th className="text-right py-2 px-3">Score</th>
                      <th className="text-center py-2 px-3">Contact</th>
                      <th className="text-left py-2 px-3">Follow-Up</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-50">
                    {drawerLeads.map((lead) => (
                      <tr
                        key={lead.lead_id}
                        onClick={() => onSelectLead?.(lead)}
                        className="hover:bg-blue-50/50 cursor-pointer transition-colors"
                      >
                        <td className="py-2.5 px-3">
                          <p className="text-sm font-medium text-gray-900 truncate max-w-[200px]">{lead.company_name || lead.agent_name || lead.owner_name}</p>
                          <p className="text-[10px] text-gray-400">{lead.portfolio_size} bldgs</p>
                        </td>
                        <td className="py-2.5 px-3 text-right font-mono text-sm text-gray-700">{(lead.total_units || 0).toLocaleString()}</td>
                        <td className="py-2.5 px-3 text-right">
                          <span className={`font-mono font-bold text-sm ${scoreColor(lead.score)}`}>{lead.score.toFixed(0)}</span>
                        </td>
                        <td className="py-2.5 px-3">
                          <div className="flex justify-center gap-1">
                            {lead.phone && <span className="px-1.5 py-0.5 bg-emerald-50 text-emerald-600 text-[9px] rounded font-medium">Ph</span>}
                            {lead.email && <span className="px-1.5 py-0.5 bg-blue-50 text-blue-600 text-[9px] rounded font-medium">Em</span>}
                            {!lead.phone && !lead.email && <span className="text-gray-300 text-[9px]">—</span>}
                          </div>
                        </td>
                        <td className="py-2.5 px-3 text-xs text-gray-400">
                          {lead.next_follow_up ? new Date(lead.next_follow_up).toLocaleDateString() : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── Section 3: KPI Metrics ─────────────────────────────── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-white shadow-sm border border-gray-100 rounded-2xl p-4">
          <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-1">Total Leads</p>
          <p className="text-2xl font-mono font-bold text-gray-900">{(stats?.total_leads || 0).toLocaleString()}</p>
          <p className="text-[10px] text-gray-400 mt-0.5">{(stats?.total_units || 0).toLocaleString()} units across {(stats?.total_buildings || 0).toLocaleString()} buildings</p>
        </div>
        <div className="bg-white shadow-sm border border-gray-100 rounded-2xl p-4">
          <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-1">Avg Lead Score</p>
          <p className={`text-2xl font-mono font-bold ${scoreColor(stats?.avg_score || 0)}`}>{(stats?.avg_score || 0).toFixed(1)}</p>
          <p className="text-[10px] text-gray-400 mt-0.5">Top score: {(stats?.top_score || 0).toFixed(0)}</p>
        </div>
        <div className="bg-white shadow-sm border border-gray-100 rounded-2xl p-4">
          <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-1">Contact Rate</p>
          <p className="text-2xl font-mono font-bold text-blue-600">{contactRate.toFixed(0)}%</p>
          <p className="text-[10px] text-gray-400 mt-0.5">{stats?.with_phone || 0} phone, {stats?.with_email || 0} email</p>
        </div>
        <div className="bg-white shadow-sm border border-gray-100 rounded-2xl p-4 relative">
          <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-1">Mgmt Fee Opportunity</p>
          <p className="text-2xl font-mono font-bold text-emerald-600">{totalTargetRevenue > 0 ? formatCurrency(totalTargetRevenue) : '—'}</p>
          <p className="text-[10px] text-gray-400 mt-0.5">Top 100 leads at 5% fee</p>
        </div>
      </div>

      {/* ── Section 4: Analytics Charts ────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Borough Distribution */}
        <div className="bg-white shadow-sm border border-gray-200 rounded-2xl p-5">
          <h3 className="text-xs font-bold text-gray-400 uppercase tracking-wider mb-4">Leads by Borough</h3>
          {boroughData.length > 0 ? (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={boroughData} layout="vertical" margin={{ left: 0, right: 20, top: 0, bottom: 0 }}>
                <XAxis type="number" tick={{ fontSize: 11, fill: '#9ca3af' }} axisLine={false} tickLine={false} />
                <YAxis type="category" dataKey="name" tick={{ fontSize: 12, fill: '#374151' }} axisLine={false} tickLine={false} width={80} />
                <Tooltip
                  formatter={(value: number) => [value.toLocaleString(), 'Leads']}
                  contentStyle={{ borderRadius: '8px', border: '1px solid #e5e7eb', fontSize: '12px' }}
                />
                <Bar dataKey="value" radius={[0, 6, 6, 0]} barSize={24}>
                  {boroughData.map((entry) => (
                    <Cell key={entry.name} fill={BOROUGH_COLORS[entry.fullName] || '#6b7280'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-[200px] flex items-center justify-center text-gray-300 text-sm">No data</div>
          )}
        </div>

        {/* Score Distribution */}
        <div className="bg-white shadow-sm border border-gray-200 rounded-2xl p-5">
          <h3 className="text-xs font-bold text-gray-400 uppercase tracking-wider mb-4">Score Distribution</h3>
          {scoreData.length > 0 ? (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={scoreData} margin={{ left: -20, right: 20, top: 0, bottom: 0 }}>
                <XAxis dataKey="name" tick={{ fontSize: 11, fill: '#9ca3af' }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontSize: 11, fill: '#9ca3af' }} axisLine={false} tickLine={false} />
                <Tooltip
                  formatter={(value: number) => [value.toLocaleString(), 'Leads']}
                  contentStyle={{ borderRadius: '8px', border: '1px solid #e5e7eb', fontSize: '12px' }}
                />
                <Bar dataKey="value" radius={[6, 6, 0, 0]} barSize={40}>
                  {scoreData.map((_, index) => (
                    <Cell key={index} fill={SCORE_COLORS[index] || '#94a3b8'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-[200px] flex items-center justify-center text-gray-300 text-sm">No data</div>
          )}
        </div>
      </div>

      {/* ── Section 5: Outreach & Enrichment ───────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Outreach Progress */}
        <div className="bg-white shadow-sm border border-gray-200 rounded-2xl p-5">
          <h3 className="text-xs font-bold text-gray-400 uppercase tracking-wider mb-2">Outreach Progress</h3>
          {outreachData.length > 0 ? (
            <div className="flex items-center">
              <ResponsiveContainer width="55%" height={180}>
                <PieChart>
                  <Pie
                    data={outreachData}
                    cx="50%"
                    cy="50%"
                    innerRadius={45}
                    outerRadius={70}
                    paddingAngle={2}
                    dataKey="value"
                  >
                    {outreachData.map((entry) => (
                      <Cell key={entry.key} fill={OUTREACH_COLORS[entry.key] || '#94a3b8'} />
                    ))}
                  </Pie>
                  <Tooltip
                    formatter={(value: number) => [value.toLocaleString(), 'Leads']}
                    contentStyle={{ borderRadius: '8px', border: '1px solid #e5e7eb', fontSize: '12px' }}
                  />
                </PieChart>
              </ResponsiveContainer>
              <div className="flex-1 space-y-1.5">
                {outreachData.map((entry) => (
                  <div key={entry.key} className="flex items-center gap-2">
                    <div className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: OUTREACH_COLORS[entry.key] || '#94a3b8' }} />
                    <span className="text-xs text-gray-600 flex-1">{entry.name}</span>
                    <span className="text-xs font-mono font-medium text-gray-900">{entry.value.toLocaleString()}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="h-[180px] flex items-center justify-center text-gray-300 text-sm">No data</div>
          )}
        </div>

        {/* Enrichment Coverage */}
        <div className="bg-white shadow-sm border border-gray-200 rounded-2xl p-5">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-xs font-bold text-gray-400 uppercase tracking-wider">Enrichment Coverage</h3>
            {enrichmentGaps && enrichmentGaps.unenriched > 0 && !enrichmentStatus?.running && (
              <button
                onClick={handleStartEnrichment}
                disabled={startingEnrichment}
                className="text-[10px] px-2 py-1 bg-blue-50 text-blue-600 rounded-lg hover:bg-blue-100 transition-colors font-medium disabled:opacity-50"
              >
                {startingEnrichment ? 'Starting...' : 'Enrich Top 500'}
              </button>
            )}
          </div>
          {enrichmentData.length > 0 ? (
            <div className="flex items-center">
              <ResponsiveContainer width="55%" height={180}>
                <PieChart>
                  <Pie
                    data={enrichmentData}
                    cx="50%"
                    cy="50%"
                    innerRadius={45}
                    outerRadius={70}
                    paddingAngle={2}
                    dataKey="value"
                  >
                    {enrichmentData.map((entry) => (
                      <Cell key={entry.key} fill={ENRICHMENT_COLORS[entry.key] || '#e2e8f0'} />
                    ))}
                  </Pie>
                  <Tooltip
                    formatter={(value: number) => [value.toLocaleString(), 'Leads']}
                    contentStyle={{ borderRadius: '8px', border: '1px solid #e5e7eb', fontSize: '12px' }}
                  />
                </PieChart>
              </ResponsiveContainer>
              <div className="flex-1 space-y-1.5">
                {enrichmentData.map((entry) => (
                  <div key={entry.key} className="flex items-center gap-2">
                    <div className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: ENRICHMENT_COLORS[entry.key] || '#e2e8f0' }} />
                    <span className="text-xs text-gray-600 flex-1">{entry.name}</span>
                    <span className="text-xs font-mono font-medium text-gray-900">{entry.value.toLocaleString()}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="h-[180px] flex items-center justify-center text-gray-300 text-sm">No data</div>
          )}
        </div>
      </div>

      {/* ── Footer ─────────────────────────────────────────────── */}
      <div className="flex items-center justify-between px-2 py-2 text-[10px] text-gray-400">
        <span>
          Data refreshed: {status?.last_refresh ? new Date(status.last_refresh).toLocaleDateString() : 'Never'}
          {enrichmentStatus?.running && ` \u2022 Enrichment ${enrichmentStatus.percent_complete}% complete`}
        </span>
        <span>{(stats?.total_leads || 0).toLocaleString()} total leads in database</span>
      </div>
    </div>
  );
};

export default Dashboard;
