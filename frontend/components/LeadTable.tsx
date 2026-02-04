
import React, { useState, useEffect } from 'react';
import { fetchLeads, ApiLead } from '../services/api';
import { BuildingLead, Borough } from '../types';

interface Props {
  onSelectLead: (lead: BuildingLead) => void;
}

// Convert API lead to frontend BuildingLead format
function apiLeadToBuildingLead(lead: ApiLead): BuildingLead {
  return {
    buildingId: lead.lead_id,
    bbl: lead.lead_id, // Using lead_id as BBL placeholder
    address: lead.buildings[0] || lead.address || 'Unknown Address',
    borough: (lead.boro?.toUpperCase() as Borough) || Borough.MANHATTAN,
    zip: lead.address?.match(/\d{5}/)?.[0] || '10001',
    totalViolations: lead.portfolio_size, // Using portfolio size as a proxy
    criticalViolations: Math.floor(lead.portfolio_size * 0.1),
    lastInspection: new Date().toISOString().split('T')[0],
    score: Math.round(lead.score),
    ownerName: lead.agent_name || lead.owner_name || 'Unknown Owner',
    contacts: [{
      name: lead.agent_name || lead.owner_name || 'Unknown',
      role: lead.owner_type || 'Principal',
      phone: lead.phone || 'N/A',
      email: lead.email || 'N/A',
      mailingAddress: lead.address || 'N/A',
    }],
  };
}

const LeadTable: React.FC<Props> = ({ onSelectLead }) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [filterBorough, setFilterBorough] = useState('ALL');
  const [leads, setLeads] = useState<BuildingLead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch leads from API
  useEffect(() => {
    async function loadLeads() {
      setLoading(true);
      setError(null);
      try {
        const boroFilter = filterBorough === 'ALL' ? undefined : filterBorough;
        const apiLeads = await fetchLeads({ 
          boro: boroFilter,
          limit: 100,
        });
        setLeads(apiLeads.map(apiLeadToBuildingLead));
      } catch (err) {
        console.error('Failed to fetch leads:', err);
        setError('Failed to load leads. Make sure the backend is running.');
      } finally {
        setLoading(false);
      }
    }
    loadLeads();
  }, [filterBorough]);

  const filteredLeads = leads.filter(lead => {
    const matchesSearch = 
      lead.address.toLowerCase().includes(searchTerm.toLowerCase()) || 
      lead.ownerName.toLowerCase().includes(searchTerm.toLowerCase()) ||
      lead.bbl.includes(searchTerm) ||
      (lead.contacts[0]?.phone || '').includes(searchTerm);
    return matchesSearch;
  });

  return (
    <div className="bg-slate-900/40 border border-white/5 rounded-[2.5rem] overflow-hidden shadow-2xl backdrop-blur-xl animate-in fade-in slide-in-from-bottom-8 duration-700">
      <div className="p-10 border-b border-white/5 flex flex-col lg:flex-row gap-10 justify-between items-center bg-slate-950/40">
        <div className="relative w-full lg:w-[650px]">
          <input
            type="text"
            placeholder="Query Asset Database (BBL, BIN, Owner, Address)..."
            className="w-full pl-14 pr-4 py-5 bg-slate-950/60 border border-white/10 rounded-2xl text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500/40 transition-all placeholder:text-slate-700 font-mono tracking-tight"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
          <svg className="w-6 h-6 text-slate-700 absolute left-5 top-4.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
          </svg>
        </div>
        <div className="flex flex-wrap gap-2.5 justify-center lg:justify-end">
          {['ALL', 'MANHATTAN', 'BROOKLYN', 'QUEENS', 'BRONX'].map(b => (
            <button
              key={b}
              onClick={() => setFilterBorough(b)}
              className={`px-6 py-3 rounded-xl text-[10px] font-black tracking-widest uppercase transition-all border ${
                filterBorough === b 
                ? 'bg-blue-600 text-white border-blue-500 shadow-[0_0_20px_rgba(37,99,235,0.4)] translate-y-[-2px]' 
                : 'bg-slate-950/40 text-slate-500 border-white/5 hover:text-slate-200 hover:bg-slate-800'
              }`}
            >
              {b}
            </button>
          ))}
        </div>
      </div>
      
      <div className="overflow-x-auto">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="bg-slate-950/60 text-slate-600 text-[10px] font-black uppercase tracking-[0.35em] border-b border-white/5">
              <th className="px-12 py-7">Target Asset</th>
              <th className="px-12 py-7">Identifiers</th>
              <th className="px-12 py-7">Ownership Network</th>
              <th className="px-12 py-7 text-center">Score</th>
              <th className="px-12 py-7">Violations</th>
              <th className="px-12 py-7 text-right">Dossier</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {filteredLeads.map((lead) => (
              <tr 
                key={lead.buildingId} 
                className="hover:bg-blue-600/[0.04] transition-all cursor-pointer group" 
                onClick={() => onSelectLead(lead)}
              >
                <td className="px-12 py-9">
                  <div className="text-lg font-black text-white group-hover:text-blue-400 transition-colors tracking-tighter leading-none">{lead.address}</div>
                  <div className="text-[10px] text-slate-600 font-bold uppercase tracking-[0.15em] mt-2.5 flex items-center gap-2">
                    <span className="w-1.5 h-1.5 bg-slate-800 rounded-full"></span>
                    {lead.borough}, NY {lead.zip}
                  </div>
                </td>
                <td className="px-12 py-9">
                  <div className="font-mono text-[11px] text-blue-400/90 bg-blue-500/10 px-3.5 py-1.5 rounded-lg inline-block border border-blue-500/10 shadow-sm">BBL: {lead.bbl}</div>
                  <div className="text-[10px] text-slate-700 mt-3 font-mono font-bold tracking-tighter">BIN: {lead.buildingId}</div>
                </td>
                <td className="px-12 py-9">
                  <div className="text-[13px] font-black text-slate-300 group-hover:text-slate-100 transition-colors uppercase tracking-tight">{lead.ownerName}</div>
                  <div className="flex items-center gap-3 mt-3">
                    <span className="text-[9px] font-black text-blue-500 bg-blue-500/10 px-2.5 py-1 rounded border border-blue-500/10 uppercase tracking-widest">
                      {lead.contacts[0]?.role || 'PRINCIPAL'}
                    </span>
                    <span className="text-[11px] text-slate-500 font-mono tracking-tighter">{lead.contacts[0]?.phone}</span>
                  </div>
                </td>
                <td className="px-12 py-9">
                  <div className="flex flex-col items-center">
                    <span className={`text-3xl font-black font-mono leading-none ${lead.score > 80 ? 'text-rose-500' : 'text-blue-500'}`}>{lead.score}</span>
                    <div className="w-28 h-1.5 bg-slate-900 rounded-full mt-4 overflow-hidden shadow-inner">
                       <div className={`h-full ${lead.score > 80 ? 'bg-rose-500 shadow-[0_0_15px_#f43f5e]' : 'bg-blue-500 shadow-[0_0_15px_#3b82f6]'}`} style={{ width: `${lead.score}%` }} />
                    </div>
                  </div>
                </td>
                <td className="px-12 py-9">
                  <div className="flex items-center gap-10">
                    <div className="text-center">
                      <div className="text-base font-black text-slate-100">{lead.totalViolations}</div>
                      <div className="text-[9px] text-slate-700 uppercase font-black tracking-widest mt-1.5">Gross</div>
                    </div>
                    <div className="w-[1px] h-14 bg-white/5"></div>
                    <div className="text-center">
                      <div className="text-sm font-black text-rose-500">{lead.criticalViolations}</div>
                      <div className="text-[9px] text-rose-950 uppercase font-black tracking-widest mt-1.5">Class C</div>
                    </div>
                  </div>
                </td>
                <td className="px-12 py-9 text-right">
                  <div className="inline-flex p-5 bg-slate-950 border border-white/5 rounded-[1.5rem] group-hover:bg-blue-600 group-hover:text-white group-hover:shadow-[0_10px_30px_rgba(37,99,235,0.4)] group-hover:translate-x-2 transition-all duration-300">
                    <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="3" d="M13 7l5 5m0 0l-5 5m5-5H6"/></svg>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      
      {filteredLeads.length === 0 && (
        <div className="py-48 text-center bg-slate-950/20">
          <div className="w-28 h-28 bg-slate-950 border border-white/5 rounded-[2.5rem] flex items-center justify-center mx-auto mb-10 text-slate-800 shadow-2xl">
            <svg className="w-14 h-14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
          </div>
          <p className="text-slate-700 font-black uppercase tracking-[0.4em] text-[11px]">Zero intelligence matches for active filters</p>
        </div>
      )}
    </div>
  );
};

export default LeadTable;
