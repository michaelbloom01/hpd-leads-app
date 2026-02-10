
import React, { useState, useEffect, useCallback } from 'react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from 'recharts';
import { toast } from 'react-hot-toast';
import { COLORS } from '../constants';
import { 
  fetchLeads, 
  fetchStats,
  ApiLead, 
  PipelineStatus,
  PipelineStats,
  getEnrichmentProgress,
  startBatchEnrichment,
  getFollowUpsDue,
  EnrichmentProgress 
} from '../services/api';

interface DashboardProps {
  onSelectLead?: (lead: ApiLead) => void;
}

const Dashboard: React.FC<DashboardProps> = ({ onSelectLead }) => {
  const [topLeads, setTopLeads] = useState<ApiLead[]>([]);
  const [readyToContact, setReadyToContact] = useState<ApiLead[]>([]);
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [stats, setStats] = useState<PipelineStats | null>(null);
  const [enrichmentStatus, setEnrichmentStatus] = useState<EnrichmentProgress | null>(null);
  const [followUpsDue, setFollowUpsDue] = useState<{ count: number; leads: Array<Record<string, unknown>> } | null>(null);
  const [loading, setLoading] = useState(true);
  const [startingEnrichment, setStartingEnrichment] = useState(false);

  const loadData = useCallback(async () => {
    try {
      // Use Promise.allSettled so one hanging endpoint doesn't block the dashboard
      // Add 10s timeout to each call to prevent infinite hangs
      const withTimeout = <T,>(promise: Promise<T>, ms: number): Promise<T> => {
        return Promise.race([
          promise,
          new Promise<T>((_, reject) => setTimeout(() => reject(new Error('Request timeout')), ms)),
        ]);
      };

      const [leadsResult, statsResult, enrichResult, followUpsResult] = await Promise.allSettled([
        withTimeout(fetchLeads({ limit: 50, min_portfolio: 10 }), 15000),
        withTimeout(fetchStats(), 15000),
        withTimeout(getEnrichmentProgress(), 10000),
        withTimeout(getFollowUpsDue(), 10000),
      ]);
      
      // Process leads (most important)
      if (leadsResult.status === 'fulfilled') {
        const sortedByPortfolio = [...leadsResult.value.leads].sort((a, b) => b.portfolio_size - a.portfolio_size);
        setTopLeads(sortedByPortfolio);
        
        const contactable = sortedByPortfolio.filter(l => 
          (l.phone || l.email) && 
          l.outreach_status === 'new' &&
          l.portfolio_size >= 10
        );
        setReadyToContact(contactable);
      } else {
        console.error('Failed to load leads:', leadsResult.reason);
      }
      
      // Process stats
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
      
      // Process enrichment status (optional - may timeout if enrichment lock is held)
      if (enrichResult.status === 'fulfilled') {
        setEnrichmentStatus(enrichResult.value);
      } else {
        console.warn('Enrichment status unavailable:', enrichResult.reason);
      }

      // Process follow-ups due
      if (followUpsResult.status === 'fulfilled') {
        setFollowUpsDue(followUpsResult.value);
      } else {
        console.warn('Follow-ups unavailable:', followUpsResult.reason);
      }
    } catch (err) {
      console.error('Failed to load dashboard data:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
    
    // Poll enrichment status every 30 seconds (with timeout to avoid hangs)
    const interval = setInterval(async () => {
      try {
        const enrichData = await Promise.race([
          getEnrichmentProgress(),
          new Promise<never>((_, reject) => setTimeout(() => reject(new Error('Poll timeout')), 8000)),
        ]);
        setEnrichmentStatus(enrichData);
        
        // Refresh leads if enrichment just finished
        if (enrichData.finished_at && !enrichData.running) {
          loadData();
        }
      } catch (err) {
        // Silently ignore poll failures - enrichment status is non-critical
      }
    }, 30000);
    
    return () => clearInterval(interval);
  }, [loadData]);

  const handleStartEnrichment = async () => {
    setStartingEnrichment(true);
    try {
      await startBatchEnrichment(500);
      // Refresh status
      const enrichData = await getEnrichmentProgress();
      setEnrichmentStatus(enrichData);
    } catch (err) {
      console.error('Failed to start enrichment:', err);
      toast.error('Failed to start enrichment. Check console for details.');
    } finally {
      setStartingEnrichment(false);
    }
  };

  // Calculate units per building for top leads
  const topLeadsWithMetrics = topLeads.slice(0, 10).map(lead => ({
    ...lead,
    unitsPerBuilding: lead.portfolio_size > 0 ? (lead.total_units / lead.portfolio_size).toFixed(1) : '0',
  }));

  // Chart data - sorted by portfolio size
  const chartData = topLeadsWithMetrics.map(lead => ({
    name: (lead.agent_name || lead.owner_name || 'Unknown').split(' ')[0].slice(0, 10),
    buildings: lead.portfolio_size,
    units: lead.total_units,
    unitsPerBuilding: parseFloat(lead.unitsPerBuilding),
  }));

  const enrichedCount = stats?.with_phone || 0;
  const withEmail = stats?.with_email || 0;

  // Calculate total units and avg units per building across all top leads
  const totalUnits = topLeads.reduce((sum, l) => sum + l.total_units, 0);
  const totalBuildings = topLeads.reduce((sum, l) => sum + l.portfolio_size, 0);
  const avgUnitsPerBuilding = totalBuildings > 0 ? (totalUnits / totalBuildings).toFixed(1) : '0';

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="text-slate-500 text-sm">Loading dashboard data...</div>
      </div>
    );
  }

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      {/* Enrichment Status Banner */}
      {enrichmentStatus?.running && (
        <div className="bg-blue-900/30 border border-blue-500/30 rounded-2xl p-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <div className="p-3 bg-blue-600 rounded-xl">
                <svg className="w-5 h-5 text-white animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
                </svg>
              </div>
              <div>
                <h3 className="text-white font-bold">Enriching Leads...</h3>
                <p className="text-blue-300 text-sm">
                  {enrichmentStatus.completed} of {enrichmentStatus.total} leads • 
                  Phase: {enrichmentStatus.phase || 'Starting'}
                  {enrichmentStatus.current_lead && ` • Current: ${enrichmentStatus.current_lead}`}
                </p>
              </div>
            </div>
            <div className="text-right">
              <div className="text-3xl font-mono font-bold text-blue-400">
                {enrichmentStatus.percent_complete}%
              </div>
              <div className="text-xs text-blue-400/70">
                {enrichmentStatus.dos_found} DOS • {enrichmentStatus.web_found} Web
              </div>
            </div>
          </div>
          <div className="mt-4 h-2 bg-blue-950 rounded-full overflow-hidden">
            <div 
              className="h-full bg-blue-500 transition-all duration-500"
              style={{ width: `${enrichmentStatus.percent_complete}%` }}
            />
          </div>
        </div>
      )}

      {/* Key Metrics - 6 columns now */}
      <div className="grid grid-cols-2 md:grid-cols-6 gap-4">
        <div className="bg-slate-900/50 border border-white/5 rounded-2xl p-5">
          <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-2">Total Leads</p>
          <p className="text-2xl font-mono font-bold text-white">{(status?.total_leads || 0).toLocaleString()}</p>
          <p className="text-[10px] text-slate-600 mt-1">Property managers</p>
        </div>
        <div className="bg-slate-900/50 border border-white/5 rounded-2xl p-5">
          <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-2">High Value</p>
          <p className="text-2xl font-mono font-bold text-blue-400">
            {stats?.portfolio_distribution ? 
              Object.entries(stats.portfolio_distribution)
                .filter(([k]) => ['11-25', '26-50', '51-100', '100+'].includes(k))
                .reduce((sum, [, v]) => sum + (v as number), 0).toLocaleString() 
              : 0}
          </p>
          <p className="text-[10px] text-slate-600 mt-1">10+ buildings</p>
        </div>
        <div className="bg-slate-900/50 border border-white/5 rounded-2xl p-5">
          <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-2">Avg Units/Bldg</p>
          <p className="text-2xl font-mono font-bold text-purple-400">{avgUnitsPerBuilding}</p>
          <p className="text-[10px] text-slate-600 mt-1">Top 100 leads</p>
        </div>
        <div className="bg-slate-900/50 border border-white/5 rounded-2xl p-5">
          <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-2">With Phone</p>
          <p className="text-2xl font-mono font-bold text-emerald-400">{enrichedCount}</p>
          <p className="text-[10px] text-slate-600 mt-1">{withEmail} emails</p>
        </div>
        <div className="bg-slate-900/50 border border-white/5 rounded-2xl p-5">
          <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-2">Ready to Call</p>
          <p className="text-2xl font-mono font-bold text-amber-400">{readyToContact.length}</p>
          <p className="text-[10px] text-slate-600 mt-1">Have contact info</p>
        </div>
        <div className="bg-slate-900/50 border border-white/5 rounded-2xl p-5">
          <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-2">Top Score</p>
          <p className="text-2xl font-mono font-bold text-emerald-400">{status?.top_score?.toFixed(1) || 0}</p>
          <p className="text-[10px] text-slate-600 mt-1">Out of 100</p>
        </div>
      </div>

      {/* Action Cards */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Start Enrichment Card - Show if few leads enriched */}
        {enrichedCount < 100 && !enrichmentStatus?.running && (
          <div className="bg-gradient-to-br from-amber-900/30 to-amber-950/30 border border-amber-500/30 rounded-2xl p-6">
            <div className="flex items-start justify-between">
              <div>
                <h3 className="text-lg font-bold text-white mb-2">Get Contact Info</h3>
                <p className="text-amber-200/70 text-sm mb-4">
                  Only {enrichedCount} leads have contact info. Start batch enrichment to find phone numbers 
                  and emails for your top leads.
                </p>
                <button
                  onClick={handleStartEnrichment}
                  disabled={startingEnrichment}
                  className="px-4 py-2 bg-amber-600 hover:bg-amber-500 text-white rounded-lg font-bold text-sm transition-colors disabled:opacity-50"
                >
                  {startingEnrichment ? 'Starting...' : 'Enrich Top 500 Leads'}
                </button>
              </div>
              <div className="p-4 bg-amber-600/20 rounded-xl">
                <svg className="w-8 h-8 text-amber-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
                </svg>
              </div>
            </div>
          </div>
        )}

        {/* Ready to Contact Card */}
        {readyToContact.length > 0 && (
          <div className="bg-gradient-to-br from-emerald-900/30 to-emerald-950/30 border border-emerald-500/30 rounded-2xl p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-bold text-white">Ready to Contact</h3>
              <span className="px-2 py-1 bg-emerald-600 text-white text-xs font-bold rounded-lg">
                {readyToContact.length} leads
              </span>
            </div>
            <div className="space-y-2 max-h-48 overflow-y-auto">
              {readyToContact.slice(0, 5).map(lead => (
                <div 
                  key={lead.lead_id}
                  onClick={() => onSelectLead?.(lead)}
                  className="flex items-center justify-between p-3 bg-slate-900/50 rounded-xl hover:bg-slate-800/50 cursor-pointer transition-colors"
                >
                  <div>
                    <p className="text-white font-medium text-sm">{lead.agent_name || lead.owner_name}</p>
                    <p className="text-slate-500 text-xs">{lead.portfolio_size} bldgs • {lead.total_units.toLocaleString()} units • {(lead.total_units / lead.portfolio_size).toFixed(1)} u/b</p>
                  </div>
                  <div className="flex items-center gap-2">
                    {lead.phone && (
                      <span className="p-1.5 bg-emerald-900/50 rounded-lg">
                        <svg className="w-3 h-3 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z"/>
                        </svg>
                      </span>
                    )}
                    {lead.email && (
                      <span className="p-1.5 bg-blue-900/50 rounded-lg">
                        <svg className="w-3 h-3 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>
                        </svg>
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Follow-Ups Due Widget */}
        {followUpsDue && followUpsDue.count > 0 && (
          <div className="bg-gradient-to-br from-rose-900/30 to-rose-950/30 border border-rose-500/30 rounded-2xl p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-bold text-white">Follow-Ups Due</h3>
              <span className="px-2 py-1 bg-rose-600 text-white text-xs font-bold rounded-lg">
                {followUpsDue.count} due
              </span>
            </div>
            <div className="space-y-2 max-h-48 overflow-y-auto">
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
                      // Try to find the lead in topLeads for full data
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
                        {lead.phone && (
                          <svg className="w-3 h-3 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z"/>
                          </svg>
                        )}
                        {lead.email && (
                          <svg className="w-3 h-3 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>
                          </svg>
                        )}
                      </div>
                    </div>
                    <div className="text-right">
                      <span className={`text-xs font-bold ${
                        isOverdue ? 'text-rose-400' : isDueToday ? 'text-amber-400' : 'text-slate-500'
                      }`}>
                        {isOverdue ? `${daysOverdue}d overdue` : isDueToday ? 'Due today' : lead.next_follow_up}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* Top 10 Largest Property Managers */}
      <div className="bg-slate-900/50 border border-white/5 rounded-2xl p-6">
        <div className="flex items-center justify-between mb-4">
          <h4 className="text-xs font-bold text-slate-500 uppercase tracking-wider">Top 10 Largest Property Managers</h4>
          <span className="text-[10px] text-slate-600">Click any row to view details</span>
        </div>
        <div className="overflow-x-auto max-h-[400px] overflow-y-auto">
          <table className="w-full">
            <thead className="sticky top-0 bg-slate-900 z-10">
              <tr className="text-[10px] text-slate-500 uppercase tracking-wider border-b border-white/5">
                <th className="text-left py-2 px-3">#</th>
                <th className="text-left py-2 px-3">Company</th>
                <th className="text-right py-2 px-3">Buildings</th>
                <th className="text-right py-2 px-3">Units</th>
                <th className="text-right py-2 px-3">Units/Bldg</th>
                <th className="text-left py-2 px-3">Boroughs</th>
                <th className="text-center py-2 px-3">Contact</th>
                <th className="text-right py-2 px-3">Score</th>
              </tr>
            </thead>
            <tbody>
              {topLeadsWithMetrics.map((lead, i) => (
                <tr 
                  key={lead.lead_id}
                  onClick={() => onSelectLead?.(lead)}
                  className="border-b border-white/5 hover:bg-slate-800/30 cursor-pointer transition-colors"
                >
                  <td className="py-3 px-3 text-slate-600 font-mono text-sm">{i + 1}</td>
                  <td className="py-3 px-3">
                    <p className="text-white font-medium text-sm" title={lead.agent_name || lead.owner_name}>
                      {lead.agent_name || lead.owner_name}
                    </p>
                  </td>
                  <td className="py-3 px-3 text-right font-mono text-sm text-blue-400 font-bold">{lead.portfolio_size}</td>
                  <td className="py-3 px-3 text-right font-mono text-sm text-slate-300">{lead.total_units.toLocaleString()}</td>
                  <td className="py-3 px-3 text-right font-mono text-sm text-purple-400">{lead.unitsPerBuilding}</td>
                  <td className="py-3 px-3">
                    <div className="flex gap-1 flex-wrap">
                      {(lead.boros || [lead.boro]).slice(0, 3).map((b, j) => (
                        <span key={j} className="px-1.5 py-0.5 bg-slate-800 text-slate-400 text-[9px] rounded">
                          {b ? b.charAt(0) + b.slice(1).toLowerCase() : ''}
                        </span>
                      ))}
                      {(lead.boros?.length || 0) > 3 && (
                        <span className="px-1.5 py-0.5 bg-slate-800 text-slate-500 text-[9px] rounded">
                          +{(lead.boros?.length || 0) - 3}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="py-3 px-3 text-center">
                    <div className="flex gap-1 justify-center">
                      {lead.phone && <span className="text-emerald-500">📞</span>}
                      {lead.email && <span className="text-blue-500">✉️</span>}
                      {!lead.phone && !lead.email && <span className="text-slate-700">—</span>}
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

      {/* Charts Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Portfolio Size Chart */}
        <div className="bg-slate-900/50 border border-white/5 rounded-2xl p-6">
          <h4 className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-6">Portfolio Size (Top 10)</h4>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" horizontal={true} vertical={false} stroke="#ffffff08" />
                <XAxis type="number" fontSize={10} tickLine={false} axisLine={false} tick={{fill: '#64748b'}} />
                <YAxis type="category" dataKey="name" fontSize={10} tickLine={false} axisLine={false} tick={{fill: '#64748b'}} width={80} />
                <Tooltip 
                  contentStyle={{ 
                    backgroundColor: '#0f172a', 
                    borderRadius: '12px', 
                    border: '1px solid rgba(255,255,255,0.1)', 
                    fontSize: '11px',
                    color: '#f8fafc'
                  }}
                  formatter={(value: number, name: string) => [value.toLocaleString(), name === 'buildings' ? 'Buildings' : 'Units']}
                />
                <Bar dataKey="buildings" fill={COLORS.primary} radius={[0, 6, 6, 0]} barSize={20} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Units per Building Chart */}
        <div className="bg-slate-900/50 border border-white/5 rounded-2xl p-6">
          <h4 className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-6">Units per Building (Top 10)</h4>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" horizontal={true} vertical={false} stroke="#ffffff08" />
                <XAxis type="number" fontSize={10} tickLine={false} axisLine={false} tick={{fill: '#64748b'}} />
                <YAxis type="category" dataKey="name" fontSize={10} tickLine={false} axisLine={false} tick={{fill: '#64748b'}} width={80} />
                <Tooltip 
                  contentStyle={{ 
                    backgroundColor: '#0f172a', 
                    borderRadius: '12px', 
                    border: '1px solid rgba(255,255,255,0.1)', 
                    fontSize: '11px',
                    color: '#f8fafc'
                  }}
                  formatter={(value: number) => [value.toFixed(1), 'Units/Building']}
                />
                <Bar dataKey="unitsPerBuilding" fill="#a855f7" radius={[0, 6, 6, 0]} barSize={20} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Entity Type Distribution */}
      {stats?.by_entity_type && (
        <div className="bg-slate-900/50 border border-white/5 rounded-2xl p-6">
          <h4 className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-4">Entity Type Distribution</h4>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {Object.entries(stats.by_entity_type).map(([type, count]) => (
              <div key={type} className="bg-slate-950/50 rounded-xl p-3 text-center">
                <p className="text-lg font-mono font-bold text-slate-300">{(count as number).toLocaleString()}</p>
                <p className="text-[10px] text-slate-600 uppercase">{type.replace('_', ' ')}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

export default Dashboard;
