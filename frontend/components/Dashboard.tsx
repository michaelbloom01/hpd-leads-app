
import React, { useState, useEffect } from 'react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from 'recharts';
import { COLORS } from '../constants';
import { fetchLeads, fetchStatus, ApiLead, PipelineStatus } from '../services/api';

const Dashboard: React.FC = () => {
  const [leads, setLeads] = useState<ApiLead[]>([]);
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function loadData() {
      try {
        const [leadsData, statusData] = await Promise.all([
          fetchLeads({ limit: 50 }),
          fetchStatus(),
        ]);
        setLeads(leadsData);
        setStatus(statusData);
      } catch (err) {
        console.error('Failed to load dashboard data:', err);
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, []);

  const chartData = leads.slice(0, 10).map(lead => ({
    name: (lead.agent_name || lead.owner_name || 'Unknown').split(' ')[0].slice(0, 8),
    violations: lead.portfolio_size,
    score: lead.score
  }));

  const totalBuildings = leads.reduce((sum, l) => sum + l.portfolio_size, 0);
  const avgScore = leads.length > 0 ? (leads.reduce((sum, l) => sum + l.score, 0) / leads.length).toFixed(1) : '0';
  const enrichedCount = leads.filter(l => l.enrichment_status === 'complete' || l.enrichment_status === 'partial').length;

  return (
    <div className="space-y-12 animate-in fade-in duration-1000">
      {loading ? (
        <div className="flex items-center justify-center py-20">
          <div className="text-slate-500 text-sm">Loading dashboard data...</div>
        </div>
      ) : (
      <>
      <div className="grid grid-cols-1 md:grid-cols-4 gap-8">
        {[
          { label: 'Total Leads', value: status?.total_leads || leads.length, trend: `${enrichedCount} enriched`, color: 'text-blue-500' },
          { label: 'Total Buildings', value: totalBuildings, trend: 'In portfolio', color: 'text-rose-500' },
          { label: 'Mean Score', value: avgScore, trend: `Top: ${status?.top_score?.toFixed(0) || 0}`, color: 'text-amber-500' },
          { label: 'With Contact Info', value: leads.filter(l => l.phone || l.email).length, trend: `${leads.filter(l => l.website).length} websites`, color: 'text-emerald-500' }
        ].map((stat, i) => (
          <div key={i} className="glass p-8 rounded-[2.5rem] border border-white/5 relative overflow-hidden group hover:translate-y-[-4px] transition-all duration-500 shadow-2xl">
            <div className="absolute top-0 right-0 w-32 h-32 bg-blue-600/[0.03] blur-3xl -mr-16 -mt-16 group-hover:bg-blue-600/10 transition-colors"></div>
            <p className="text-[11px] font-black text-slate-600 uppercase tracking-[0.3em] mb-4">{stat.label}</p>
            <h3 className={`text-4xl font-mono font-black ${stat.color} mb-2 tracking-tighter`}>{stat.value}</h3>
            <span className="text-[10px] font-black text-slate-700 uppercase tracking-widest bg-white/[0.03] px-2 py-0.5 rounded-lg border border-white/5">{stat.trend}</span>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-12">
        <div className="glass p-12 rounded-[3.5rem] border border-white/5 shadow-2xl">
          <h4 className="text-[12px] font-black text-slate-600 uppercase tracking-[0.5em] mb-12">Asset Severity Distribution</h4>
          <div className="h-80">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData}>
                <CartesianGrid strokeDasharray="6 6" vertical={false} stroke="#ffffff0a" />
                <XAxis dataKey="name" fontSize={10} tickLine={false} axisLine={false} tick={{fill: '#475569', fontWeight: 700}} />
                <YAxis fontSize={10} tickLine={false} axisLine={false} tick={{fill: '#475569', fontWeight: 700}} />
                <Tooltip 
                  cursor={{fill: 'rgba(255,255,255,0.02)'}}
                  contentStyle={{ 
                    backgroundColor: '#030712', 
                    borderRadius: '24px', 
                    border: '1px solid rgba(255,255,255,0.1)', 
                    boxShadow: '0 30px 60px -12px rgb(0 0 0 / 0.5)',
                    fontSize: '11px',
                    fontWeight: 'black',
                    color: '#f8fafc',
                    textTransform: 'uppercase'
                  }}
                />
                <Bar dataKey="violations" radius={[12, 12, 0, 0]} barSize={50}>
                  {chartData.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={entry.violations > 50 ? COLORS.danger : COLORS.accent} fillOpacity={0.7} className="hover:fill-opacity-100 transition-all duration-300" />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="glass p-12 rounded-[3.5rem] border border-white/5 shadow-2xl">
          <h4 className="text-[12px] font-black text-slate-600 uppercase tracking-[0.5em] mb-10">Top Leads by Score</h4>
          <div className="space-y-6">
            {leads.sort((a,b) => b.score - a.score).slice(0, 5).map((lead) => (
              <div key={lead.lead_id} className="flex items-center justify-between p-6 bg-slate-950/40 border border-white/5 rounded-3xl hover:border-blue-500/40 hover:bg-slate-900 transition-all group cursor-pointer shadow-lg">
                <div className="flex flex-col">
                  <span className="text-base font-black text-slate-100 group-hover:text-blue-500 transition-colors tracking-tighter">{lead.agent_name || lead.owner_name}</span>
                  <span className="text-[10px] font-mono text-slate-600 tracking-[0.1em] uppercase font-bold mt-1.5">{lead.portfolio_size} buildings • {lead.boro}</span>
                </div>
                <div className="flex items-center gap-10">
                  <div className="text-right">
                    <p className="text-[9px] font-black text-slate-700 uppercase tracking-widest mb-1">Score</p>
                    <p className={`text-2xl font-mono font-black ${lead.score > 50 ? 'text-rose-500' : 'text-slate-300'}`}>{lead.score.toFixed(1)}</p>
                  </div>
                  {lead.phone && (
                    <div className="p-4 bg-emerald-800/50 rounded-2xl text-emerald-400">
                      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z"/></svg>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
      </>
      )}
    </div>
  );
};

export default Dashboard;
