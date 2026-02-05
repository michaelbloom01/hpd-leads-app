
import React, { useState, useEffect, useCallback } from 'react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from 'recharts';
import { COLORS } from '../constants';
import { 
  fetchLeads, 
  fetchStatus, 
  fetchStats,
  ApiLead, 
  PipelineStatus, 
  getEnrichmentProgress,
  startBatchEnrichment,
  EnrichmentProgress 
} from '../services/api';

interface DashboardProps {
  onSelectLead?: (lead: ApiLead) => void;
}

const Dashboard: React.FC<DashboardProps> = ({ onSelectLead }) => {
  const [topLeads, setTopLeads] = useState<ApiLead[]>([]);
  const [readyToContact, setReadyToContact] = useState<ApiLead[]>([]);
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [stats, setStats] = useState<any>(null);
  const [enrichmentStatus, setEnrichmentStatus] = useState<EnrichmentProgress | null>(null);
  const [loading, setLoading] = useState(true);
  const [startingEnrichment, setStartingEnrichment] = useState(false);

  const loadData = useCallback(async () => {
    try {
      const [topLeadsData, statusData, statsData, enrichData] = await Promise.all([
        fetchLeads({ limit: 50, min_portfolio: 10 }),
        fetchStatus(),
        fetchStats(),
        getEnrichmentProgress(),
      ]);
      setTopLeads(topLeadsData);
      setStatus(statusData);
      setStats(statsData);
      setEnrichmentStatus(enrichData);
      
      // Find leads that are ready to contact (have contact info, not yet contacted)
      const contactable = topLeadsData.filter(l => 
        (l.phone || l.email) && 
        l.outreach_status === 'new' &&
        l.portfolio_size >= 10
      );
      setReadyToContact(contactable);
    } catch (err) {
      console.error('Failed to load dashboard data:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
    
    // Poll enrichment status every 5 seconds if running
    const interval = setInterval(async () => {
      try {
        const enrichData = await getEnrichmentProgress();
        setEnrichmentStatus(enrichData);
        
        // Refresh leads if enrichment just finished
        if (enrichData.finished_at && !enrichData.running) {
          loadData();
        }
      } catch (err) {
        console.error('Failed to poll enrichment status:', err);
      }
    }, 5000);
    
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
      alert('Failed to start enrichment. Check console for details.');
    } finally {
      setStartingEnrichment(false);
    }
  };

  const chartData = topLeads.slice(0, 10).map(lead => ({
    name: (lead.agent_name || lead.owner_name || 'Unknown').split(' ')[0].slice(0, 8),
    buildings: lead.portfolio_size,
    score: lead.score
  }));

  const enrichedCount = stats?.with_phone || 0;
  const withEmail = stats?.with_email || 0;
  const withWebsite = stats?.with_website || 0;

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

      {/* Key Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
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
          <p className="text-2xl font-mono font-bold text-rose-400">{status?.top_score?.toFixed(1) || 0}</p>
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
                    <p className="text-slate-500 text-xs">{lead.portfolio_size} buildings • {lead.boros?.join(', ') || lead.boro}</p>
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
      </div>

      {/* Charts Section */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Top Leads Chart */}
        <div className="bg-slate-900/50 border border-white/5 rounded-2xl p-6">
          <h4 className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-6">Top 10 by Portfolio Size</h4>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#ffffff08" />
                <XAxis dataKey="name" fontSize={10} tickLine={false} axisLine={false} tick={{fill: '#64748b'}} />
                <YAxis fontSize={10} tickLine={false} axisLine={false} tick={{fill: '#64748b'}} />
                <Tooltip 
                  contentStyle={{ 
                    backgroundColor: '#0f172a', 
                    borderRadius: '12px', 
                    border: '1px solid rgba(255,255,255,0.1)', 
                    fontSize: '11px',
                    color: '#f8fafc'
                  }}
                />
                <Bar dataKey="buildings" radius={[6, 6, 0, 0]} barSize={40}>
                  {chartData.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={entry.buildings > 100 ? COLORS.primary : COLORS.accent} fillOpacity={0.8} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Top Leads List */}
        <div className="bg-slate-900/50 border border-white/5 rounded-2xl p-6">
          <h4 className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-4">Top Leads by Score</h4>
          <div className="space-y-3">
            {[...topLeads].sort((a,b) => b.score - a.score).slice(0, 6).map((lead) => (
              <div 
                key={lead.lead_id} 
                onClick={() => onSelectLead?.(lead)}
                className="flex items-center justify-between p-4 bg-slate-950/50 border border-white/5 rounded-xl hover:border-blue-500/30 hover:bg-slate-900/50 transition-all cursor-pointer"
              >
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-bold text-slate-100 truncate">{lead.agent_name || lead.owner_name}</p>
                  <p className="text-[10px] text-slate-600 mt-0.5">
                    {lead.portfolio_size} buildings • {lead.boros?.join(', ') || lead.boro}
                  </p>
                </div>
                <div className="flex items-center gap-4 ml-4">
                  {/* Contact indicators */}
                  <div className="flex gap-1">
                    {lead.phone && (
                      <span className="p-1 bg-emerald-900/50 rounded">
                        <svg className="w-3 h-3 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z"/>
                        </svg>
                      </span>
                    )}
                    {lead.email && (
                      <span className="p-1 bg-blue-900/50 rounded">
                        <svg className="w-3 h-3 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>
                        </svg>
                      </span>
                    )}
                  </div>
                  {/* Score */}
                  <div className={`text-lg font-mono font-bold ${lead.score >= 60 ? 'text-rose-400' : lead.score >= 40 ? 'text-amber-400' : 'text-slate-400'}`}>
                    {lead.score.toFixed(0)}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Building Type Distribution */}
      {stats?.building_type_distribution && (
        <div className="bg-slate-900/50 border border-white/5 rounded-2xl p-6">
          <h4 className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-4">Building Type Distribution</h4>
          <div className="grid grid-cols-2 md:grid-cols-7 gap-3">
            {Object.entries(stats.building_type_distribution).map(([type, count]) => (
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
