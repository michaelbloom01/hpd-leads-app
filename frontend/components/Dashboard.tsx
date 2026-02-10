
import React, { useState, useEffect, useCallback } from 'react';
import { toast } from 'react-hot-toast';
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
  refreshViolations,
  EnrichmentProgress,
  EnrichmentGaps,
  getEnrichmentGaps,
  checkHealth,
} from '../services/api';

interface DashboardProps {
  onSelectLead?: (lead: ApiLead) => void;
}

const PIPELINE_STAGES = [
  { key: 'research', label: 'Research', color: 'bg-slate-600' },
  { key: 'first_contact', label: 'Contact', color: 'bg-blue-600' },
  { key: 'follow_up', label: 'Follow-Up', color: 'bg-indigo-600' },
  { key: 'meeting_scheduled', label: 'Meeting Set', color: 'bg-purple-600' },
  { key: 'meeting_done', label: 'Meeting Done', color: 'bg-violet-600' },
  { key: 'loi', label: 'LOI', color: 'bg-amber-600' },
  { key: 'due_diligence', label: 'Due Diligence', color: 'bg-orange-600' },
  { key: 'closed', label: 'Closed', color: 'bg-emerald-600' },
];

const formatCurrency = (amount: number | undefined | null): string => {
  if (amount == null || isNaN(amount)) return '—';
  if (amount >= 1_000_000) return `$${(amount / 1_000_000).toFixed(1)}m`;
  if (amount >= 1_000) return `$${(amount / 1_000).toFixed(0)}k`;
  return `$${amount.toFixed(0)}`;
};

const Dashboard: React.FC<DashboardProps> = ({ onSelectLead }) => {
  const [topLeads, setTopLeads] = useState<ApiLead[]>([]);
  const [readyToContact, setReadyToContact] = useState<ApiLead[]>([]);
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [stats, setStats] = useState<PipelineStats | null>(null);
  const [enrichmentStatus, setEnrichmentStatus] = useState<EnrichmentProgress | null>(null);
  const [followUpsDue, setFollowUpsDue] = useState<{ count: number; leads: Array<Record<string, unknown>> } | null>(null);
  const [loading, setLoading] = useState(true);
  const [startingEnrichment, setStartingEnrichment] = useState(false);
  const [refreshingViolations, setRefreshingViolations] = useState(false);
  const [dataStatus, setDataStatus] = useState<DataStatus | null>(null);
  const [enrichmentGaps, setEnrichmentGaps] = useState<EnrichmentGaps | null>(null);
  const [backendStarting, setBackendStarting] = useState(false);
  const [showFeeTooltip, setShowFeeTooltip] = useState(false);

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
        setReadyToContact(contactable);
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
      } else {
        console.warn('Enrichment status unavailable:', enrichResult.reason);
      }

      if (followUpsResult.status === 'fulfilled') {
        setFollowUpsDue(followUpsResult.value);
      } else {
        console.warn('Follow-ups unavailable:', followUpsResult.reason);
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

  // R4: Health check before first data load
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
    // Only start polling if backend is ready (loadData called from health check above)
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
      } catch (err) {
        // Silently ignore poll failures
      }
    }, 30000);
    
    return () => clearInterval(interval);
  }, [loadData]);

  const handleStartEnrichment = async () => {
    setStartingEnrichment(true);
    try {
      await startBatchEnrichment(500);
      const enrichData = await getEnrichmentProgress();
      setEnrichmentStatus(enrichData);
      toast.success('Enrichment started for top 500 leads');
    } catch (err) {
      console.error('Failed to start enrichment:', err);
      toast.error('Failed to start enrichment');
    } finally {
      setStartingEnrichment(false);
    }
  };

  const handleRefreshViolations = async () => {
    setRefreshingViolations(true);
    try {
      const result = await refreshViolations();
      toast.success(`Violations updated: ${result.leads_with_violations} leads with violations found`);
      loadData();
    } catch (err) {
      console.error('Failed to refresh violations:', err);
      toast.error('Failed to refresh violations data');
    } finally {
      setRefreshingViolations(false);
    }
  };

  // Compute pipeline stage counts from top leads
  const pipelineCounts: Record<string, number> = {};
  PIPELINE_STAGES.forEach(s => { pipelineCounts[s.key] = 0; });
  topLeads.forEach(lead => {
    const stage = lead.pipeline_stage || 'research';
    if (pipelineCounts[stage] !== undefined) pipelineCounts[stage]++;
  });
  const leadsInPipeline = topLeads.filter(l => l.pipeline_stage && l.pipeline_stage !== 'research').length;

  // Compute estimated pipeline revenue (leads past research stage)
  const pipelineRevenue = topLeads
    .filter(l => l.pipeline_stage && l.pipeline_stage !== 'research')
    .reduce((sum, l) => sum + (l.estimated_annual_revenue || 0), 0);

  // Total estimated revenue across all target leads
  const totalTargetRevenue = topLeads.reduce((sum, l) => sum + (l.estimated_annual_revenue || 0), 0);

  // High-value lead count (portfolio >= 10)
  const highValueCount = stats?.portfolio_distribution ? 
    Object.entries(stats.portfolio_distribution)
      .filter(([k]) => ['11-25', '26-50', '51-100', '100+'].includes(k))
      .reduce((sum, [, v]) => sum + (v as number), 0)
    : 0;

  const enrichedCount = stats?.with_phone || 0;
  const withEmail = stats?.with_email || 0;

  // Leads with violations
  const leadsWithViolations = topLeads.filter(l => l.violation_count > 0).length;

  // R4: Cold-start banner
  if (backendStarting) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center space-y-4">
        <div className="w-12 h-12 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
        <h2 className="text-lg font-semibold text-blue-400">Backend is starting up</h2>
        <p className="text-slate-400 text-sm max-w-md">
          This usually takes 1–2 minutes after inactivity. The dashboard will load automatically once the server is ready.
        </p>
        <p className="text-slate-600 text-xs">Polling every 5s...</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="space-y-6 animate-pulse">
        <div className="h-16 bg-slate-800/50 rounded-2xl" />
        <div className="grid grid-cols-2 md:grid-cols-6 gap-4">
          {[...Array(6)].map((_, i) => <div key={i} className="h-24 bg-slate-800/50 rounded-2xl" />)}
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="h-64 bg-slate-800/50 rounded-2xl" />
          <div className="h-64 bg-slate-800/50 rounded-2xl" />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      {/* Pipeline Funnel */}
      <div className="bg-slate-900/50 border border-white/5 rounded-2xl p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-xs font-bold text-slate-500 uppercase tracking-wider">Deal Pipeline</h3>
          <span className="text-xs text-slate-600">
            {leadsInPipeline} active {leadsInPipeline === 1 ? 'deal' : 'deals'}
            {pipelineRevenue > 0 && ` • ${formatCurrency(pipelineRevenue)}/yr est. management fee`}
          </span>
        </div>
        <div className="flex gap-1">
          {PIPELINE_STAGES.map((stage) => {
            const count = pipelineCounts[stage.key] || 0;
            return (
              <div key={stage.key} className="flex-1 group">
                <div className={`${count > 0 ? stage.color : 'bg-slate-800'} rounded-lg px-2 py-3 text-center transition-all hover:opacity-80`}>
                  <div className="text-lg font-bold font-mono text-white">{count}</div>
                  <div className="text-[9px] uppercase tracking-wider text-white/70 font-medium">{stage.label}</div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Key Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
        <div className="bg-slate-900/50 border border-white/5 rounded-2xl p-4">
          <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">Target Leads</p>
          <p className="text-2xl font-mono font-bold text-white">{highValueCount.toLocaleString()}</p>
          <p className="text-[10px] text-slate-600 mt-0.5">10+ buildings</p>
        </div>
        <div className="bg-slate-900/50 border border-white/5 rounded-2xl p-4 relative">
          <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1 flex items-center gap-1">
            Mgmt Fee Opportunity
            <button
              onClick={() => setShowFeeTooltip(!showFeeTooltip)}
              className="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full border border-slate-600 text-[8px] text-slate-500 hover:text-slate-300 hover:border-slate-400 transition-colors"
              aria-label="How is this calculated?"
            >?</button>
          </p>
          {showFeeTooltip && (
            <div className="absolute top-full left-0 mt-1 z-50 w-64 p-3 bg-slate-800 border border-white/10 rounded-lg shadow-xl text-xs text-slate-300 leading-relaxed">
              <p>Estimated annual management fees if these portfolios were acquired.</p>
              <p className="mt-1 text-slate-500">Total Units x Avg Rent (by borough & building type) x 5% management fee rate.</p>
              <button onClick={() => setShowFeeTooltip(false)} className="mt-2 text-blue-400 hover:text-blue-300 text-[10px]">Got it</button>
            </div>
          )}
          <p className="text-2xl font-mono font-bold text-emerald-400">{totalTargetRevenue > 0 ? formatCurrency(totalTargetRevenue) : '—'}</p>
          <p className="text-[10px] text-slate-600 mt-0.5">Top 100 leads • 5% fee rate</p>
        </div>
        <div className="bg-slate-900/50 border border-white/5 rounded-2xl p-4">
          <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">Follow-Ups Due</p>
          <p className="text-2xl font-mono font-bold text-rose-400">{followUpsDue?.count || 0}</p>
          <p className="text-[10px] text-slate-600 mt-0.5">Need action</p>
        </div>
        <div className="bg-slate-900/50 border border-white/5 rounded-2xl p-4">
          <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">With Contact</p>
          <p className="text-2xl font-mono font-bold text-blue-400">{enrichedCount}</p>
          <p className="text-[10px] text-slate-600 mt-0.5">{withEmail} emails</p>
        </div>
        <div className="bg-slate-900/50 border border-white/5 rounded-2xl p-4">
          <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">Ready to Call</p>
          <p className="text-2xl font-mono font-bold text-amber-400">{readyToContact.length}</p>
          <p className="text-[10px] text-slate-600 mt-0.5">Uncontacted</p>
        </div>
        <div className="bg-slate-900/50 border border-white/5 rounded-2xl p-4" title="Leads with open HPD violations — potential distressed assets or management turnover opportunities">
          <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">With Violations</p>
          <p className="text-2xl font-mono font-bold text-orange-400">{leadsWithViolations}</p>
          <p className="text-[10px] text-slate-600 mt-0.5">Open HPD violations</p>
        </div>
        {enrichmentGaps && enrichmentGaps.unenriched > 0 && (
          <div className="bg-slate-900/50 border border-amber-500/20 rounded-2xl p-4 col-span-2 md:col-span-1" title="Leads that have not been enriched with contact info yet">
            <p className="text-[10px] font-bold text-amber-500 uppercase tracking-wider mb-1">Needs Enrichment</p>
            <p className="text-2xl font-mono font-bold text-amber-400">{enrichmentGaps.unenriched.toLocaleString()}</p>
            <p className="text-[10px] text-slate-600 mt-0.5">of {enrichmentGaps.total_leads.toLocaleString()} total</p>
          </div>
        )}
      </div>

      {/* Action Cards Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Follow-Ups Due */}
        {followUpsDue && followUpsDue.count > 0 ? (
          <div className="bg-gradient-to-br from-rose-900/30 to-rose-950/30 border border-rose-500/30 rounded-2xl p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-bold text-white">Follow-Ups Due</h3>
              <span className="px-2 py-1 bg-rose-600 text-white text-xs font-bold rounded-lg">
                {followUpsDue.count} due
              </span>
            </div>
            <div className="space-y-2 max-h-52 overflow-y-auto">
              {followUpsDue.leads.slice(0, 5).map((lead: any, i: number) => {
                const dueDate = lead.next_follow_up ? new Date(lead.next_follow_up as string) : null;
                const today = new Date(new Date().toDateString());
                const isOverdue = dueDate && dueDate < today;
                const isDueToday = dueDate && dueDate.toDateString() === today.toDateString();
                const daysOverdue = dueDate ? Math.floor((today.getTime() - dueDate.getTime()) / (1000 * 60 * 60 * 24)) : 0;
                return (
                  <div 
                    key={lead.lead_id as string || i}
                    onClick={() => {
                      const fullLead = topLeads.find(l => l.lead_id === lead.lead_id);
                      if (fullLead) onSelectLead?.(fullLead);
                    }}
                    className="flex items-center justify-between p-3 bg-slate-900/50 rounded-xl hover:bg-slate-800/50 cursor-pointer transition-colors"
                  >
                    <div>
                      <p className="text-white font-medium text-sm">{lead.agent_name || lead.owner_name || lead.company_name || 'Unknown'}</p>
                      <div className="flex items-center gap-2 mt-0.5">
                        {lead.pipeline_stage && (
                          <span className="text-[10px] px-1.5 py-0.5 bg-blue-900/30 text-blue-400 rounded">
                            {(lead.pipeline_stage as string).replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase())}
                          </span>
                        )}
                      </div>
                    </div>
                    <span className={`text-xs font-bold ${isOverdue ? 'text-rose-400' : isDueToday ? 'text-amber-400' : 'text-slate-500'}`}>
                      {isOverdue ? `${daysOverdue}d overdue` : isDueToday ? 'Due today' : lead.next_follow_up}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        ) : (
          // Ready to Contact (show when no follow-ups)
          <div className="bg-gradient-to-br from-emerald-900/30 to-emerald-950/30 border border-emerald-500/30 rounded-2xl p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-bold text-white">Ready to Contact</h3>
              <span className="px-2 py-1 bg-emerald-600 text-white text-xs font-bold rounded-lg">
                {readyToContact.length} leads
              </span>
            </div>
            <div className="space-y-2 max-h-52 overflow-y-auto">
              {readyToContact.slice(0, 5).map(lead => (
                <div 
                  key={lead.lead_id}
                  onClick={() => onSelectLead?.(lead)}
                  className="flex items-center justify-between p-3 bg-slate-900/50 rounded-xl hover:bg-slate-800/50 cursor-pointer transition-colors"
                >
                  <div>
                    <p className="text-white font-medium text-sm">{lead.company_name || lead.agent_name || lead.owner_name}</p>
                    <p className="text-slate-500 text-xs">{lead.portfolio_size} bldgs • {(lead.total_units || 0).toLocaleString()} units</p>
                  </div>
                  <div className="flex items-center gap-1.5">
                    {lead.phone && <span className="p-1 bg-emerald-900/50 rounded text-emerald-400 text-[10px]">Phone</span>}
                    {lead.email && <span className="p-1 bg-blue-900/50 rounded text-blue-400 text-[10px]">Email</span>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Ready to Contact (when follow-ups exist) OR Ready to Contact always shows */}
        {followUpsDue && followUpsDue.count > 0 && readyToContact.length > 0 && (
          <div className="bg-gradient-to-br from-emerald-900/30 to-emerald-950/30 border border-emerald-500/30 rounded-2xl p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-bold text-white">Ready to Contact</h3>
              <span className="px-2 py-1 bg-emerald-600 text-white text-xs font-bold rounded-lg">
                {readyToContact.length} leads
              </span>
            </div>
            <div className="space-y-2 max-h-52 overflow-y-auto">
              {readyToContact.slice(0, 5).map(lead => (
                <div 
                  key={lead.lead_id}
                  onClick={() => onSelectLead?.(lead)}
                  className="flex items-center justify-between p-3 bg-slate-900/50 rounded-xl hover:bg-slate-800/50 cursor-pointer transition-colors"
                >
                  <div>
                    <p className="text-white font-medium text-sm">{lead.company_name || lead.agent_name || lead.owner_name}</p>
                    <p className="text-slate-500 text-xs">{lead.portfolio_size} bldgs • {(lead.total_units || 0).toLocaleString()} units</p>
                  </div>
                  <div className="flex items-center gap-1.5">
                    {lead.phone && <span className="p-1 bg-emerald-900/50 rounded text-emerald-400 text-[10px]">Phone</span>}
                    {lead.email && <span className="p-1 bg-blue-900/50 rounded text-blue-400 text-[10px]">Email</span>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Top Leads Table */}
      <div className="bg-slate-900/50 border border-white/5 rounded-2xl p-6">
        <div className="flex items-center justify-between mb-4">
          <h4 className="text-xs font-bold text-slate-500 uppercase tracking-wider">Top Leads by Score</h4>
          <span className="text-[10px] text-slate-600">Click to view details</span>
        </div>
        <div className="overflow-x-auto max-h-[400px] overflow-y-auto">
          <table className="w-full">
            <thead className="sticky top-0 bg-slate-900 z-10">
              <tr className="text-[10px] text-slate-500 uppercase tracking-wider border-b border-white/5">
                <th className="text-left py-2 px-3">#</th>
                <th className="text-left py-2 px-3">Company</th>
                <th className="text-right py-2 px-3">Buildings</th>
                <th className="text-right py-2 px-3">Units</th>
                <th className="text-right py-2 px-3" title="Estimated annual management fee revenue = Units × Avg Rent × 5% fee">Mgmt Fee</th>
                <th className="text-left py-2 px-3">Boroughs</th>
                <th className="text-right py-2 px-3" title="Lead quality score (0–100) based on portfolio size, building types, and data completeness">Score</th>
              </tr>
            </thead>
            <tbody>
              {topLeads.slice(0, 10).map((lead, i) => (
                <tr 
                  key={lead.lead_id}
                  onClick={() => onSelectLead?.(lead)}
                  className="border-b border-white/5 hover:bg-slate-800/30 cursor-pointer transition-colors"
                >
                  <td className="py-3 px-3 text-slate-600 font-mono text-sm">{i + 1}</td>
                  <td className="py-3 px-3">
                    <p className="text-white font-medium text-sm">{lead.company_name || lead.agent_name || lead.owner_name}</p>
                    {lead.pipeline_stage && lead.pipeline_stage !== 'research' && (
                      <span className="text-[9px] px-1.5 py-0.5 bg-blue-900/30 text-blue-400 rounded mt-0.5 inline-block">
                        {lead.pipeline_stage.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}
                      </span>
                    )}
                  </td>
                  <td className="py-3 px-3 text-right font-mono text-sm text-blue-400 font-bold">{lead.portfolio_size}</td>
                  <td className="py-3 px-3 text-right font-mono text-sm text-slate-300">{(lead.total_units || 0).toLocaleString()}</td>
                  <td className="py-3 px-3 text-right font-mono text-sm text-emerald-400">
                    {lead.estimated_annual_revenue > 0 ? formatCurrency(lead.estimated_annual_revenue) : '—'}
                  </td>
                  <td className="py-3 px-3">
                    <div className="flex gap-1 flex-wrap">
                      {(lead.boros || [lead.boro]).slice(0, 2).map((b, j) => (
                        <span key={j} className="px-1.5 py-0.5 bg-slate-800 text-slate-400 text-[9px] rounded">
                          {b ? b.charAt(0) + b.slice(1).toLowerCase() : ''}
                        </span>
                      ))}
                      {(lead.boros?.length || 0) > 2 && (
                        <span className="px-1.5 py-0.5 bg-slate-800 text-slate-500 text-[9px] rounded">
                          +{(lead.boros?.length || 0) - 2}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="py-3 px-3 text-right">
                    <span className={`font-mono font-bold ${lead.score >= 60 ? 'text-emerald-400' : lead.score >= 40 ? 'text-amber-400' : 'text-slate-500'}`}>
                      {lead.score.toFixed(0)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Data Status Footer */}
      <div className="flex items-center justify-between px-2 py-3 border-t border-white/5">
        <div className="flex items-center gap-4 text-xs text-slate-600">
          {/* Contact Lookup Status */}
          {enrichmentStatus?.running ? (
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 bg-blue-500 rounded-full animate-pulse" />
              <span>Finding contact info... {enrichmentStatus.percent_complete}% ({enrichmentStatus.completed}/{enrichmentStatus.total})</span>
            </div>
          ) : enrichmentStatus?.last_completed_at ? (
            <span>Contact info last updated: {new Date(enrichmentStatus.last_completed_at).toLocaleDateString()}</span>
          ) : (
            <button
              onClick={handleStartEnrichment}
              disabled={startingEnrichment}
              className="text-amber-500 hover:text-amber-400 underline disabled:opacity-50"
            >
              {startingEnrichment ? 'Starting...' : `Find contact info (${enrichedCount} leads have data so far)`}
            </button>
          )}

          <span className="text-slate-700">|</span>

          {/* Violations Status */}
          {refreshingViolations ? (
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 bg-orange-500 rounded-full animate-pulse" />
              <span>Loading violations data...</span>
            </div>
          ) : leadsWithViolations > 0 ? (
            <span>{leadsWithViolations} leads with violations • <button onClick={handleRefreshViolations} className="text-orange-500 hover:text-orange-400 underline">Refresh</button></span>
          ) : (
            <span className="flex items-center gap-1">
              <div className="w-2 h-2 bg-amber-500 rounded-full animate-pulse" />
              Violations computing in background...
            </span>
          )}

          <span className="text-slate-700">|</span>
          
          <span>Data refreshed: {status?.last_refresh ? new Date(status.last_refresh).toLocaleDateString() : 'Never'}</span>
        </div>
        {/* Data freshness indicators */}
        {dataStatus && (
          <div className="flex flex-wrap gap-4 text-[10px] text-slate-600 mt-1">
            <span>Revenue: {dataStatus.revenue_computed_at ? `computed ${new Date(dataStatus.revenue_computed_at).toLocaleString()} (v${dataStatus.revenue_formula_version || '?'})` : 'not yet computed'}</span>
            <span>Violations: {dataStatus.violations_computed_at ? `fetched ${new Date(dataStatus.violations_computed_at).toLocaleString()}` : 'computing...'}</span>
            <span>Enrichment: {dataStatus.enrichment_completed_at ? `completed ${new Date(dataStatus.enrichment_completed_at).toLocaleString()}` : 'in progress'}</span>
          </div>
        )}
      </div>
    </div>
  );
};

export default Dashboard;
