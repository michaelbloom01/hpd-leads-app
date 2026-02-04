
import React, { useState, useEffect, useMemo } from 'react';
import { fetchLeads, ApiLead, enrichLeads } from '../services/api';

interface Props {
  onSelectLead: (lead: ApiLead) => void;
}

type SortField = 'agent_name' | 'portfolio_size' | 'score' | 'boro' | 'enrichment_status';
type SortDir = 'asc' | 'desc';

const LeadTable: React.FC<Props> = ({ onSelectLead }) => {
  const [leads, setLeads] = useState<ApiLead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  // Filters
  const [searchTerm, setSearchTerm] = useState('');
  const [filterBorough, setFilterBorough] = useState<string>('');
  const [filterMinScore, setFilterMinScore] = useState<string>('');
  const [filterMaxScore, setFilterMaxScore] = useState<string>('');
  const [filterMinPortfolio, setFilterMinPortfolio] = useState<string>('');
  const [filterHasPhone, setFilterHasPhone] = useState<boolean | null>(null);
  const [filterHasEmail, setFilterHasEmail] = useState<boolean | null>(null);
  const [filterHasWebsite, setFilterHasWebsite] = useState<boolean | null>(null);
  const [filterHasMgmtCompany, setFilterHasMgmtCompany] = useState<boolean | null>(null);
  
  // Sorting
  const [sortField, setSortField] = useState<SortField>('score');
  const [sortDir, setSortDir] = useState<SortDir>('desc');
  
  // Pagination
  const [page, setPage] = useState(0);
  const pageSize = 50;
  
  // Selection for bulk actions
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [enriching, setEnriching] = useState(false);

  // Fetch leads
  useEffect(() => {
    async function loadLeads() {
      setLoading(true);
      setError(null);
      try {
        const apiLeads = await fetchLeads({ limit: 500 });
        setLeads(apiLeads);
      } catch (err) {
        console.error('Failed to fetch leads:', err);
        setError('Failed to load leads. Make sure the backend is running and data is loaded.');
      } finally {
        setLoading(false);
      }
    }
    loadLeads();
  }, []);

  // Filter and sort leads
  const filteredLeads = useMemo(() => {
    let result = [...leads];
    
    // Text search
    if (searchTerm) {
      const term = searchTerm.toLowerCase();
      result = result.filter(lead => 
        (lead.agent_name || '').toLowerCase().includes(term) ||
        (lead.owner_name || '').toLowerCase().includes(term) ||
        (lead.address || '').toLowerCase().includes(term) ||
        lead.buildings.some(b => b.toLowerCase().includes(term))
      );
    }
    
    // Borough filter
    if (filterBorough) {
      result = result.filter(lead => lead.boro.toUpperCase() === filterBorough.toUpperCase());
    }
    
    // Score filters
    if (filterMinScore) {
      const min = parseFloat(filterMinScore);
      if (!isNaN(min)) result = result.filter(lead => lead.score >= min);
    }
    if (filterMaxScore) {
      const max = parseFloat(filterMaxScore);
      if (!isNaN(max)) result = result.filter(lead => lead.score <= max);
    }
    
    // Portfolio filter
    if (filterMinPortfolio) {
      const min = parseInt(filterMinPortfolio);
      if (!isNaN(min)) result = result.filter(lead => lead.portfolio_size >= min);
    }
    
    // Contact filters
    if (filterHasPhone === true) result = result.filter(lead => lead.phone);
    if (filterHasPhone === false) result = result.filter(lead => !lead.phone);
    if (filterHasEmail === true) result = result.filter(lead => lead.email);
    if (filterHasEmail === false) result = result.filter(lead => !lead.email);
    if (filterHasWebsite === true) result = result.filter(lead => lead.website);
    if (filterHasWebsite === false) result = result.filter(lead => !lead.website);
    
    // Management company filter (has agent vs only owner)
    if (filterHasMgmtCompany === true) result = result.filter(lead => lead.agent_name);
    if (filterHasMgmtCompany === false) result = result.filter(lead => !lead.agent_name);
    
    // Sort
    result.sort((a, b) => {
      let aVal: any = a[sortField];
      let bVal: any = b[sortField];
      
      if (typeof aVal === 'string') aVal = aVal.toLowerCase();
      if (typeof bVal === 'string') bVal = bVal.toLowerCase();
      
      if (aVal < bVal) return sortDir === 'asc' ? -1 : 1;
      if (aVal > bVal) return sortDir === 'asc' ? 1 : -1;
      return 0;
    });
    
    return result;
  }, [leads, searchTerm, filterBorough, filterMinScore, filterMaxScore, filterMinPortfolio, filterHasPhone, filterHasEmail, filterHasWebsite, filterHasMgmtCompany, sortField, sortDir]);

  // Paginated results
  const paginatedLeads = useMemo(() => {
    const start = page * pageSize;
    return filteredLeads.slice(start, start + pageSize);
  }, [filteredLeads, page, pageSize]);

  const totalPages = Math.ceil(filteredLeads.length / pageSize);

  // Get unique boroughs for filter
  const boroughs = useMemo(() => {
    const boroSet = new Set(leads.map(l => l.boro).filter(Boolean));
    return Array.from(boroSet).sort();
  }, [leads]);

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDir('desc');
    }
  };

  const toggleSelect = (id: string) => {
    const newSelected = new Set(selectedIds);
    if (newSelected.has(id)) {
      newSelected.delete(id);
    } else {
      newSelected.add(id);
    }
    setSelectedIds(newSelected);
  };

  const selectAll = () => {
    if (selectedIds.size === paginatedLeads.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(paginatedLeads.map(l => l.lead_id)));
    }
  };

  const handleEnrichSelected = async () => {
    if (selectedIds.size === 0) return;
    setEnriching(true);
    try {
      await enrichLeads(Array.from(selectedIds));
      // Reload data
      const apiLeads = await fetchLeads({ limit: 500 });
      setLeads(apiLeads);
      setSelectedIds(new Set());
    } catch (err) {
      console.error('Enrichment failed:', err);
    } finally {
      setEnriching(false);
    }
  };

  const clearFilters = () => {
    setSearchTerm('');
    setFilterBorough('');
    setFilterMinScore('');
    setFilterMaxScore('');
    setFilterMinPortfolio('');
    setFilterHasPhone(null);
    setFilterHasEmail(null);
    setFilterHasWebsite(null);
    setFilterHasMgmtCompany(null);
  };

  const SortIcon = ({ field }: { field: SortField }) => (
    <span className="ml-1 text-slate-600">
      {sortField === field ? (sortDir === 'asc' ? '↑' : '↓') : '↕'}
    </span>
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="text-slate-500">Loading leads...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center">
        <div className="text-rose-400 mb-4">{error}</div>
        <p className="text-slate-500 text-sm">Click "Refresh from HPD" to load data</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Filter Bar - Vantage.sh style */}
      <div className="bg-slate-900/60 border border-white/5 rounded-xl p-4">
        <div className="flex flex-wrap gap-3 items-center">
          {/* Search */}
          <div className="flex-1 min-w-[200px]">
            <input
              type="text"
              placeholder="Search name, address..."
              className="w-full px-3 py-2 bg-slate-950 border border-white/10 rounded-lg text-sm text-slate-100 focus:outline-none focus:ring-1 focus:ring-blue-500"
              value={searchTerm}
              onChange={(e) => { setSearchTerm(e.target.value); setPage(0); }}
            />
          </div>
          
          {/* Borough */}
          <select
            className="px-3 py-2 bg-slate-950 border border-white/10 rounded-lg text-sm text-slate-300 focus:outline-none focus:ring-1 focus:ring-blue-500"
            value={filterBorough}
            onChange={(e) => { setFilterBorough(e.target.value); setPage(0); }}
          >
            <option value="">All Boroughs</option>
            {boroughs.map(b => <option key={b} value={b}>{b}</option>)}
          </select>
          
          {/* Score Range */}
          <div className="flex items-center gap-1">
            <input
              type="number"
              placeholder="Min Score"
              className="w-24 px-2 py-2 bg-slate-950 border border-white/10 rounded-lg text-sm text-slate-100 focus:outline-none focus:ring-1 focus:ring-blue-500"
              value={filterMinScore}
              onChange={(e) => { setFilterMinScore(e.target.value); setPage(0); }}
            />
            <span className="text-slate-600">-</span>
            <input
              type="number"
              placeholder="Max"
              className="w-20 px-2 py-2 bg-slate-950 border border-white/10 rounded-lg text-sm text-slate-100 focus:outline-none focus:ring-1 focus:ring-blue-500"
              value={filterMaxScore}
              onChange={(e) => { setFilterMaxScore(e.target.value); setPage(0); }}
            />
          </div>
          
          {/* Portfolio Size */}
          <input
            type="number"
            placeholder="Min Buildings"
            className="w-28 px-2 py-2 bg-slate-950 border border-white/10 rounded-lg text-sm text-slate-100 focus:outline-none focus:ring-1 focus:ring-blue-500"
            value={filterMinPortfolio}
            onChange={(e) => { setFilterMinPortfolio(e.target.value); setPage(0); }}
          />
          
          {/* Contact Filters */}
          <div className="flex gap-2">
            <button
              onClick={() => setFilterHasMgmtCompany(filterHasMgmtCompany === true ? null : true)}
              className={`px-2 py-1 rounded text-xs font-medium transition-colors ${
                filterHasMgmtCompany === true 
                  ? 'bg-blue-600/30 text-blue-400 border border-blue-500/30' 
                  : 'bg-slate-800 text-slate-500 border border-white/5 hover:text-slate-300'
              }`}
            >
              🏢 Mgmt Co
            </button>
            <button
              onClick={() => setFilterHasPhone(filterHasPhone === true ? null : true)}
              className={`px-2 py-1 rounded text-xs font-medium transition-colors ${
                filterHasPhone === true 
                  ? 'bg-emerald-600/30 text-emerald-400 border border-emerald-500/30' 
                  : 'bg-slate-800 text-slate-500 border border-white/5 hover:text-slate-300'
              }`}
            >
              📞 Phone
            </button>
            <button
              onClick={() => setFilterHasEmail(filterHasEmail === true ? null : true)}
              className={`px-2 py-1 rounded text-xs font-medium transition-colors ${
                filterHasEmail === true 
                  ? 'bg-emerald-600/30 text-emerald-400 border border-emerald-500/30' 
                  : 'bg-slate-800 text-slate-500 border border-white/5 hover:text-slate-300'
              }`}
            >
              ✉️ Email
            </button>
            <button
              onClick={() => setFilterHasWebsite(filterHasWebsite === true ? null : true)}
              className={`px-2 py-1 rounded text-xs font-medium transition-colors ${
                filterHasWebsite === true 
                  ? 'bg-emerald-600/30 text-emerald-400 border border-emerald-500/30' 
                  : 'bg-slate-800 text-slate-500 border border-white/5 hover:text-slate-300'
              }`}
            >
              🌐 Website
            </button>
          </div>
          
          {/* Clear Filters */}
          <button
            onClick={clearFilters}
            className="px-3 py-2 text-xs text-slate-500 hover:text-slate-300 transition-colors"
          >
            Clear Filters
          </button>
        </div>
        
        {/* Results count and bulk actions */}
        <div className="flex justify-between items-center mt-3 pt-3 border-t border-white/5">
          <div className="text-sm text-slate-500">
            Showing <span className="text-slate-300 font-medium">{filteredLeads.length}</span> of {leads.length} leads
            {selectedIds.size > 0 && (
              <span className="ml-3 text-blue-400">({selectedIds.size} selected)</span>
            )}
          </div>
          {selectedIds.size > 0 && (
            <button
              onClick={handleEnrichSelected}
              disabled={enriching}
              className="px-4 py-1.5 bg-blue-600 text-white text-xs font-medium rounded-lg hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {enriching ? 'Enriching...' : `Enrich ${selectedIds.size} Selected`}
            </button>
          )}
        </div>
      </div>

      {/* Data Table */}
      <div className="bg-slate-900/40 border border-white/5 rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="bg-slate-950/80 text-slate-500 text-xs uppercase tracking-wider border-b border-white/5">
                <th className="px-4 py-3 w-8">
                  <input
                    type="checkbox"
                    checked={selectedIds.size === paginatedLeads.length && paginatedLeads.length > 0}
                    onChange={selectAll}
                    className="rounded bg-slate-800 border-slate-600"
                  />
                </th>
                <th 
                  className="px-4 py-3 cursor-pointer hover:text-slate-300 transition-colors"
                  onClick={() => handleSort('agent_name')}
                >
                  Management Company <SortIcon field="agent_name" />
                </th>
                <th 
                  className="px-4 py-3 cursor-pointer hover:text-slate-300 transition-colors"
                  onClick={() => handleSort('boro')}
                >
                  Borough <SortIcon field="boro" />
                </th>
                <th 
                  className="px-4 py-3 cursor-pointer hover:text-slate-300 transition-colors text-right"
                  onClick={() => handleSort('portfolio_size')}
                >
                  Buildings <SortIcon field="portfolio_size" />
                </th>
                <th 
                  className="px-4 py-3 cursor-pointer hover:text-slate-300 transition-colors text-right"
                  onClick={() => handleSort('score')}
                >
                  Score <SortIcon field="score" />
                </th>
                <th className="px-4 py-3">Contact</th>
                <th 
                  className="px-4 py-3 cursor-pointer hover:text-slate-300 transition-colors"
                  onClick={() => handleSort('enrichment_status')}
                >
                  Status <SortIcon field="enrichment_status" />
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {paginatedLeads.map((lead) => (
                <tr 
                  key={lead.lead_id}
                  className="hover:bg-slate-800/50 transition-colors cursor-pointer"
                  onClick={() => onSelectLead(lead)}
                >
                  <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={selectedIds.has(lead.lead_id)}
                      onChange={() => toggleSelect(lead.lead_id)}
                      className="rounded bg-slate-800 border-slate-600"
                    />
                  </td>
                  <td className="px-4 py-3">
                    <div className="font-medium text-slate-200 text-sm">{lead.agent_name || lead.owner_name}</div>
                    <div className="text-xs text-slate-600 truncate max-w-[200px]">
                      {lead.agent_name && lead.owner_name && lead.agent_name !== lead.owner_name && (
                        <span className="text-slate-500">Owner: {lead.owner_name.slice(0, 30)}{lead.owner_name.length > 30 ? '...' : ''}</span>
                      )}
                      {!lead.agent_name && <span className="text-amber-600/70">No management agent</span>}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <span className="px-2 py-0.5 bg-slate-800 text-slate-400 text-xs rounded">
                      {lead.boro}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <span className="text-slate-300 font-mono text-sm">{lead.portfolio_size}</span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <span className={`font-mono text-sm font-medium ${
                      lead.score >= 60 ? 'text-emerald-400' : 
                      lead.score >= 40 ? 'text-amber-400' : 'text-slate-500'
                    }`}>
                      {lead.score.toFixed(1)}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex gap-1.5">
                      {lead.phone && <span title={lead.phone} className="text-emerald-500">📞</span>}
                      {lead.email && <span title={lead.email} className="text-blue-500">✉️</span>}
                      {lead.website && <span title={lead.website} className="text-purple-500">🌐</span>}
                      {!lead.phone && !lead.email && !lead.website && <span className="text-slate-700">—</span>}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 text-xs rounded ${
                      lead.enrichment_status === 'complete' ? 'bg-emerald-900/30 text-emerald-400' :
                      lead.enrichment_status === 'partial' ? 'bg-amber-900/30 text-amber-400' :
                      lead.enrichment_status === 'failed' ? 'bg-rose-900/30 text-rose-400' :
                      'bg-slate-800 text-slate-500'
                    }`}>
                      {lead.enrichment_status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        
        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-white/5 bg-slate-950/50">
            <button
              onClick={() => setPage(Math.max(0, page - 1))}
              disabled={page === 0}
              className="px-3 py-1 text-sm text-slate-400 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              ← Previous
            </button>
            <div className="text-sm text-slate-500">
              Page {page + 1} of {totalPages}
            </div>
            <button
              onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
              disabled={page >= totalPages - 1}
              className="px-3 py-1 text-sm text-slate-400 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              Next →
            </button>
          </div>
        )}
      </div>
    </div>
  );
};

export default LeadTable;
