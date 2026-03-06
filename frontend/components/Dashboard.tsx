
import React, { useState, useEffect, useCallback, useRef } from 'react';
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
  PipelineStats,
  getEnrichmentProgress,
  startBatchEnrichment,
  getFollowUpsDue,
  EnrichmentProgress,
  EnrichmentGaps,
  getEnrichmentGaps,
  checkHealth,
  fetchDataHealth,
  DataHealthResponse,
  getSmartLists,
  SmartList,
} from '../services/api';
import {
  PIPELINE_STAGES,
  BOROUGH_COLORS,
  SCORE_COLORS,
  OUTREACH_COLORS,
  OUTREACH_LABELS,
  ENRICHMENT_COLORS,
  ENRICHMENT_LABELS,
  formatCurrency,
  scoreColor,
} from '../utils/format';
import { fetchBuildingStats, fetchBuildings, type BuildingStats, type BuildingOutreachStats, type BuildingRow } from '../services/buildings-api';

interface LeadFilterPreset {
  outreachStatuses?: string[];
  pipelineStages?: string[];
  enrichmentStatuses?: string[];
  entityTypes?: string[];
  minPortfolio?: string;
  hasPhone?: boolean;
  hasEmail?: boolean;
}

interface DashboardProps {
  onSelectLead?: (lead: ApiLead) => void;
  onNavigateToLeads?: (filters?: LeadFilterPreset) => void;
}

// scoreColor imported from utils/format

const getLeadDisplayName = (lead: ApiLead): string =>
  lead.company_name ||
  lead.agent_name ||
  lead.owner_name ||
  lead.primary_contact ||
  lead.address ||
  lead.lead_id;


const Dashboard: React.FC<DashboardProps> = ({ onSelectLead, onNavigateToLeads }) => {
  const [topLeads, setTopLeads] = useState<ApiLead[]>([]);
  const [readyToContactLeads, setReadyToContactLeads] = useState<ApiLead[]>([]);
  const [showReadyDrawer, setShowReadyDrawer] = useState(false);
  const [stats, setStats] = useState<PipelineStats | null>(null);
  const [enrichmentStatus, setEnrichmentStatus] = useState<EnrichmentProgress | null>(null);
  const [followUpsDue, setFollowUpsDue] = useState<{ count: number; leads: Array<Record<string, unknown>> } | null>(null);
  const [loading, setLoading] = useState(true);
  const [startingEnrichment, setStartingEnrichment] = useState(false);
  const [enrichmentGaps, setEnrichmentGaps] = useState<EnrichmentGaps | null>(null);
  const [backendStarting, setBackendStarting] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [dataHealth, setDataHealth] = useState<DataHealthResponse | null>(null);
  const [buildingStats, setBuildingStats] = useState<BuildingStats | null>(null);
  const [pinnedSmartLists, setPinnedSmartLists] = useState<SmartList[]>([]);

  // Pipeline drawer state
  const [selectedStage, setSelectedStage] = useState<string | null>(null);
  const [drawerLeads, setDrawerLeads] = useState<ApiLead[]>([]);
  const [drawerLoading, setDrawerLoading] = useState(false);
  const [selectedBuildingStage, setSelectedBuildingStage] = useState<keyof Pick<BuildingOutreachStats, 'pipeline' | 'contacted' | 'meeting' | 'won' | 'lost'> | null>(null);
  const [buildingDrawerRows, setBuildingDrawerRows] = useState<BuildingRow[]>([]);
  const [buildingDrawerLoading, setBuildingDrawerLoading] = useState(false);

  const isMounted = useRef(true);
  useEffect(() => {
    return () => { isMounted.current = false; };
  }, []);

  const loadData = useCallback(async () => {
    try {
      const withTimeout = <T,>(promise: Promise<T>, ms: number): Promise<T> => {
        return Promise.race([
          promise,
          new Promise<T>((_, reject) => setTimeout(() => reject(new Error('Request timeout')), ms)),
        ]);
      };

      const [leadsResult, statsResult, enrichResult, followUpsResult, dataStatusResult, gapsResult, healthResult, bldgStatsResult, smartListsResult] = await Promise.allSettled([
        withTimeout(fetchLeads({ limit: 100, min_portfolio: 10 }), 15000),
        withTimeout(fetchStats(), 15000),
        withTimeout(getEnrichmentProgress(), 10000),
        withTimeout(getFollowUpsDue(), 10000),
        withTimeout(fetchDataStatus(), 10000),
        withTimeout(getEnrichmentGaps(), 10000),
        withTimeout(fetchDataHealth(), 10000),
        withTimeout(fetchBuildingStats(), 10000),
        withTimeout(getSmartLists(), 10000),
      ]);
      
      if (leadsResult.status === 'fulfilled') {
        const sortedByScore = [...leadsResult.value.leads].sort((a, b) => b.score - a.score);
        if (isMounted.current) {
          setTopLeads(sortedByScore);
          const contactable = sortedByScore.filter(l =>
            (l.phone || l.email) &&
            l.outreach_status === 'new' &&
            l.portfolio_size >= 10
          );
          setReadyToContactLeads(contactable);
        }
      } else {
        console.error('Failed to load leads:', leadsResult.reason);
      }
      
      if (statsResult.status === 'fulfilled' && isMounted.current) {
        const statsData = statsResult.value;
        setStats(statsData);
      } else if (statsResult.status !== 'fulfilled') {
        console.error('Failed to load stats:', statsResult.reason);
      }
      
      if (enrichResult.status === 'fulfilled' && isMounted.current) {
        setEnrichmentStatus(enrichResult.value);
      }

      if (followUpsResult.status === 'fulfilled' && isMounted.current) {
        setFollowUpsDue(followUpsResult.value);
      }

      if (dataStatusResult.status !== 'fulfilled') {
        console.error('Failed to load data status:', dataStatusResult.reason);
      }

      if (gapsResult.status === 'fulfilled' && isMounted.current) {
        setEnrichmentGaps(gapsResult.value);
      }

      if (healthResult.status === 'fulfilled' && isMounted.current) {
        setDataHealth(healthResult.value);
      } else if (healthResult.status !== 'fulfilled') {
        console.error('Failed to load data health:', healthResult.reason);
      }

      if (bldgStatsResult.status === 'fulfilled' && isMounted.current) {
        setBuildingStats(bldgStatsResult.value);
      }
      if (smartListsResult.status === 'fulfilled' && isMounted.current) {
        const pinned = (smartListsResult.value.smart_lists || []).filter((list) => list.pinned);
        setPinnedSmartLists(pinned);
      }
    } catch (err) {
      console.error('Failed to load dashboard data:', err);
      if (isMounted.current) setLoadError('Failed to load dashboard data. Check your connection and try again.');
    } finally {
      if (isMounted.current) setLoading(false);
    }
  }, []);

  // Health check before first data load
  useEffect(() => {
    let timerId: ReturnType<typeof setTimeout>;
    let errorRetries = 0;
    const MAX_ERROR_RETRIES = 24; // ~2 minutes of polling at 5s intervals
    
    const pollHealth = async () => {
      const health = await checkHealth();
      if (!isMounted.current) return;
      if (health.status === 'starting') {
        setBackendStarting(true);
        errorRetries = 0; // Reset error counter when we get a proper 'starting' response
        timerId = setTimeout(pollHealth, 5000);
      } else if (health.status === 'error' && errorRetries < MAX_ERROR_RETRIES) {
        // Backend may be sleeping or crashing — keep polling
        setBackendStarting(true);
        errorRetries++;
        timerId = setTimeout(pollHealth, 5000);
      } else {
        setBackendStarting(false);
        loadData();
      }
    };
    
    pollHealth();
    return () => { clearTimeout(timerId); };
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
        if (!isMounted.current) return;
        setEnrichmentStatus(enrichData);
        
        if (enrichData.finished_at && !enrichData.running) {
          loadData();
        }
      } catch {
        if (isMounted.current) toast.error('Failed to load dashboard data');
      }
    }, 30000);
    
    return () => clearInterval(interval);
  }, [loadData, backendStarting]);

  const handleStartEnrichment = async () => {
    setStartingEnrichment(true);
    try {
      const started = await startBatchEnrichment(500);
      if (started.status !== 'started' && started.status !== 'queued') {
        toast(started.message || 'Batch enrichment is unavailable right now.');
        return;
      }
      const enrichData = await getEnrichmentProgress();
      if (isMounted.current) {
        setEnrichmentStatus(enrichData);
        if (started.dispatch_mode === 'in_process') {
          toast(started.message || 'Enrichment queued in local fallback mode.');
        } else {
          toast.success(started.message || 'Enrichment started for top 500 leads');
        }
      }
    } catch {
      if (isMounted.current) toast.error('Failed to start enrichment');
    } finally {
      if (isMounted.current) setStartingEnrichment(false);
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
      if (isMounted.current) setDrawerLeads(result.leads);
    } catch {
      if (isMounted.current) setDrawerLeads([]);
    } finally {
      if (isMounted.current) setDrawerLoading(false);
    }
  };

  const handleOpenSmartListInLeads = (list: SmartList) => {
    const filters = list.filters as Record<string, unknown>;
    const preset: LeadFilterPreset = {};
    if (filters.pipeline_stages) {
      const stages = Array.isArray(filters.pipeline_stages)
        ? filters.pipeline_stages
        : String(filters.pipeline_stages).split(",");
      preset.pipelineStages = stages.map((s) => String(s).trim()).filter(Boolean);
    }
    if (filters.entity_types) {
      const types = Array.isArray(filters.entity_types)
        ? filters.entity_types
        : String(filters.entity_types).split(",");
      preset.entityTypes = types.map((t) => String(t).trim()).filter(Boolean);
    }
    if (filters.has_phone) preset.hasPhone = true;
    if (filters.has_email) preset.hasEmail = true;
    if (filters.min_portfolio) preset.minPortfolio = String(filters.min_portfolio);
    onNavigateToLeads?.(preset);
  };

  const handleBuildingStageClick = async (
    stageKey: keyof Pick<BuildingOutreachStats, 'pipeline' | 'contacted' | 'meeting' | 'won' | 'lost'>,
  ) => {
    if (selectedBuildingStage === stageKey) {
      setSelectedBuildingStage(null);
      setBuildingDrawerRows([]);
      return;
    }
    setSelectedBuildingStage(stageKey);
    setBuildingDrawerLoading(true);
    try {
      const result = await fetchBuildings({
        outreach_status: stageKey,
        sort_by: 'churn_score',
        sort_dir: 'desc',
        limit: 50,
        offset: 0,
      });
      if (isMounted.current) setBuildingDrawerRows(result.buildings || []);
    } catch {
      if (isMounted.current) setBuildingDrawerRows([]);
    } finally {
      if (isMounted.current) setBuildingDrawerLoading(false);
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
    ? Math.min(100, (stats.with_phone + stats.with_email) / stats.total_leads * 100)
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

      {/* Error banner */}
      {loadError && (
        <div className="bg-red-50 border border-red-200 rounded-2xl px-5 py-3 flex items-center justify-between">
          <span className="text-sm text-red-700">{loadError}</span>
          <button onClick={() => { setLoadError(null); setLoading(true); loadData(); }} className="text-xs font-medium text-red-600 hover:text-red-800 px-3 py-1 bg-red-100 rounded-lg">Retry</button>
        </div>
      )}

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
                onClick={() => { setShowReadyDrawer(false); onNavigateToLeads?.({ outreachStatuses: ['new'], minPortfolio: '10' }); }}
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
                    <p className="text-sm font-medium text-gray-900 truncate group-hover:text-blue-700 transition-colors">{getLeadDisplayName(lead)}</p>
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
                  onClick={() => { setShowReadyDrawer(false); onNavigateToLeads?.({ outreachStatuses: ['new'], minPortfolio: '10' }); }}
                  className="text-xs text-blue-600 hover:text-blue-500 font-medium transition-colors"
                >
                  + {readyToContactLeads.length - 10} more leads &rarr;
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── Pinned Smart Lists ───────────────────────────────────── */}
      {pinnedSmartLists.length > 0 && (
        <div className="bg-white shadow-sm border border-gray-200 rounded-2xl p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-xs font-bold text-gray-400 uppercase tracking-wider">Pinned Smart Lists</h3>
            <span className="text-xs text-gray-400">{pinnedSmartLists.length} pinned</span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
            {pinnedSmartLists.slice(0, 4).map((list) => (
              <button
                key={list.id}
                onClick={() => handleOpenSmartListInLeads(list)}
                className="text-left p-3 rounded-xl border border-gray-200 hover:border-blue-300 hover:bg-blue-50/40 transition-colors"
                title="Open list in Leads"
              >
                <div className="flex items-center justify-between">
                  <p className="text-sm font-semibold text-gray-900 truncate">{list.name}</p>
                  <span className="px-2 py-0.5 bg-blue-50 text-blue-700 text-[10px] rounded font-medium">
                    {list.last_count}
                  </span>
                </div>
                {list.description && (
                  <p className="mt-1 text-xs text-gray-500 truncate">{list.description}</p>
                )}
                <p className="mt-2 text-[10px] text-gray-400">
                  {list.last_evaluated_at
                    ? `Last eval ${new Date(list.last_evaluated_at).toLocaleDateString()}`
                    : "Not evaluated yet"}
                </p>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* ── Section 2: PM Company Deal Pipeline ──────────────────── */}
      <div className="bg-white shadow-sm border border-gray-200 rounded-2xl p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-xs font-bold text-gray-400 uppercase tracking-wider">PM Company Deal Pipeline</h3>
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
                          <p className="text-sm font-medium text-gray-900 truncate max-w-[200px]">{getLeadDisplayName(lead)}</p>
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

      {/* ── Section 2b: Building Outreach Pipeline ────────────────── */}
      {buildingStats?.outreach && (
        <div className="bg-white shadow-sm border border-gray-200 rounded-2xl p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-xs font-bold text-gray-400 uppercase tracking-wider">Building Outreach Pipeline</h3>
            <span className="text-xs text-gray-400">
              {buildingStats.outreach.in_pipeline} building{buildingStats.outreach.in_pipeline !== 1 ? 's' : ''} in pipeline
              {' '}&middot;{' '}
              {buildingStats.hot} hot &middot; {buildingStats.warm} warm
            </span>
          </div>
          <div className="flex gap-1.5">
            {([
              { key: 'pipeline', label: 'Pipeline', tw: 'bg-blue-500' },
              { key: 'contacted', label: 'Contacted', tw: 'bg-indigo-500' },
              { key: 'meeting', label: 'Meeting', tw: 'bg-purple-500' },
              { key: 'won', label: 'Won', tw: 'bg-emerald-500' },
              { key: 'lost', label: 'Lost', tw: 'bg-gray-400' },
            ] as Array<{ key: keyof Pick<BuildingOutreachStats, 'pipeline' | 'contacted' | 'meeting' | 'won' | 'lost'>; label: string; tw: string }>).map((stage) => {
              const count = buildingStats.outreach?.[stage.key] ?? 0;
              const isSelected = selectedBuildingStage === stage.key;
              return (
                <button
                  key={stage.key}
                  onClick={() => handleBuildingStageClick(stage.key)}
                  className={`flex-1 rounded-xl overflow-hidden transition-all duration-200 ${isSelected ? 'ring-2 ring-offset-2 ring-blue-500' : ''}`}
                >
                  <div
                    className={`${count > 0 ? stage.tw : 'bg-gray-100'} rounded-xl px-2 text-center flex flex-col items-center justify-center`}
                    style={{ minHeight: '64px' }}
                  >
                    <div className={`text-lg font-bold font-mono ${count > 0 ? 'text-white' : 'text-gray-400'}`}>{count}</div>
                    <div className={`text-[9px] uppercase tracking-wider font-medium ${count > 0 ? 'text-white/70' : 'text-gray-400'}`}>{stage.label}</div>
                  </div>
                </button>
              );
            })}
          </div>

          {selectedBuildingStage && (
            <div className="mt-4 pt-4 border-t border-gray-200 animate-in slide-in-from-top duration-300">
              <div className="flex items-center justify-between mb-3">
                <h4 className="text-sm font-bold text-gray-700">
                  {selectedBuildingStage.charAt(0).toUpperCase() + selectedBuildingStage.slice(1)} — {buildingDrawerRows.length} building{buildingDrawerRows.length !== 1 ? 's' : ''}
                </h4>
                <button
                  onClick={() => { setSelectedBuildingStage(null); setBuildingDrawerRows([]); }}
                  className="p-1 rounded-lg hover:bg-gray-100 text-gray-400 hover:text-gray-600 transition-colors"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" /></svg>
                </button>
              </div>
              {buildingDrawerLoading ? (
                <div className="flex items-center justify-center py-8">
                  <div className="w-6 h-6 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                </div>
              ) : buildingDrawerRows.length === 0 ? (
                <p className="text-sm text-gray-400 py-4 text-center">No buildings in this stage</p>
              ) : (
                <div className="overflow-x-auto max-h-80 overflow-y-auto">
                  <table className="w-full">
                    <thead className="sticky top-0 bg-white z-10">
                      <tr className="text-[10px] text-gray-400 uppercase tracking-wider border-b border-gray-100">
                        <th className="text-left py-2 px-3">Building</th>
                        <th className="text-right py-2 px-3">Units</th>
                        <th className="text-right py-2 px-3">Score</th>
                        <th className="text-left py-2 px-3">PM Company</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-50">
                      {buildingDrawerRows.map((row) => (
                        <tr key={row.bbl} className="hover:bg-blue-50/50 transition-colors">
                          <td className="py-2.5 px-3">
                            <p className="text-sm font-medium text-gray-900 truncate max-w-[280px]">{row.address || row.bbl}</p>
                            <p className="text-[10px] text-gray-400">{row.borough || 'N/A'} • {row.bbl}</p>
                          </td>
                          <td className="py-2.5 px-3 text-right font-mono text-sm text-gray-700">{(row.unit_count || 0).toLocaleString()}</td>
                          <td className="py-2.5 px-3 text-right">
                            {row.churn_score != null ? (
                              <span className="font-mono font-bold text-sm text-gray-700">{row.churn_score.toFixed(1)}</span>
                            ) : (
                              <span className="text-xs text-gray-300">—</span>
                            )}
                          </td>
                          <td className="py-2.5 px-3 text-xs text-gray-600">{row.pm_company || 'Unknown'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      )}

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
        <div className="bg-white shadow-sm border border-gray-200 rounded-2xl p-5" role="img" aria-label="Bar chart showing lead count by borough">
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
        <div className="bg-white shadow-sm border border-gray-200 rounded-2xl p-5" role="img" aria-label="Bar chart showing lead count by score range (0-20, 20-40, 40-60, 60-80, 80-100)">
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
        <div className="bg-white shadow-sm border border-gray-200 rounded-2xl p-5" role="img" aria-label="Pie chart showing outreach status breakdown (New, Contacted, Interested, Not Interested, Closed)">
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
        <div className="bg-white shadow-sm border border-gray-200 rounded-2xl p-5" role="img" aria-label="Pie chart showing enrichment status breakdown (Enriched, Partial, No Data, Not Enriched)">
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

      {/* ── Data Health Badge ──────────────────────────────────── */}
      <div className="flex items-center justify-between px-3 py-2 text-[11px] rounded-lg bg-gray-50 border border-gray-100">
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${
            !dataHealth ? 'bg-gray-300' :
            dataHealth.warnings.length === 0 ? 'bg-emerald-400' :
            dataHealth.warnings.length <= 2 ? 'bg-amber-400' : 'bg-red-400'
          }`} />
          <span className="text-gray-600">
            {dataHealth ? (
              <>
                {dataHealth.total_leads.toLocaleString()} leads
                {dataHealth.coverage_percent != null && ` · ${dataHealth.coverage_percent}% data coverage`}
                {dataHealth.total_buildings_registered > 0 && ` · ${dataHealth.total_buildings_registered.toLocaleString()} buildings`}
                {dataHealth.entity_coverage_ratio != null && ` · ${dataHealth.entity_coverage_ratio}% entity coverage`}
                {dataHealth.last_lead_generation?.finished_at && ` · leads gen ${new Date(dataHealth.last_lead_generation.finished_at).toLocaleDateString()}`}
                {dataHealth.last_refresh?.finished_at && ` · refreshed ${new Date(dataHealth.last_refresh.finished_at).toLocaleDateString()}`}
              </>
            ) : (
              <>{(stats?.total_leads || 0).toLocaleString()} leads{loading ? ' · Data health loading...' : ''}</>
            )}
          </span>
        </div>
        <div className="flex items-center gap-2 text-gray-400">
          {enrichmentStatus?.running && (
            <span className="text-blue-500">Enrichment {enrichmentStatus.percent_complete}%</span>
          )}
          {dataHealth?.warnings.length ? (
            <span className="text-amber-500 cursor-help" title={dataHealth.warnings.join('\n')}>
              {dataHealth.warnings.length} warning{dataHealth.warnings.length > 1 ? 's' : ''}
            </span>
          ) : null}
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
