
import React, { useState, useEffect, useCallback } from 'react';
import { fetchLeads, ApiLead, enrichLeads, API_BASE_URL, checkHealth } from '../services/api';

interface Props {
  onSelectLead: (lead: ApiLead) => void;
}

// R4: Cold-start polling interval (ms)
const HEALTH_POLL_INTERVAL = 5000;

type SortField = 'agent_name' | 'portfolio_size' | 'total_units' | 'score' | 'boro' | 'enrichment_status' | 'estimated_annual_revenue' | 'violations_per_unit';
type SortDir = 'asc' | 'desc';

const BOROUGHS = ['MANHATTAN', 'BROOKLYN', 'QUEENS', 'BRONX', 'STATEN ISLAND'];
const BOROUGH_SHORT: Record<string, string> = { 'MANHATTAN': 'Man', 'BROOKLYN': 'Bklyn', 'QUEENS': 'Qns', 'BRONX': 'Bronx', 'STATEN ISLAND': 'SI' };

const formatCurrency = (amount: number | undefined | null): string => {
  if (amount == null || isNaN(amount)) return '—';
  if (amount >= 1_000_000) return `$${(amount / 1_000_000).toFixed(1)}m`;
  if (amount >= 1_000) return `$${(amount / 1_000).toFixed(0)}k`;
  return `$${amount.toFixed(0)}`;
};

const scoreColor = (score: number): string => {
  if (score >= 60) return 'text-emerald-400';
  if (score >= 40) return 'text-amber-400';
  return 'text-slate-500';
};

const LeadTable: React.FC<Props> = ({ onSelectLead }) => {
  const [leads, setLeads] = useState<ApiLead[]>([]);
  const [totalLeads, setTotalLeads] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [backendStarting, setBackendStarting] = useState(false);
  
  // Server-side filters
  const [searchTerm, setSearchTerm] = useState('');
  const [filterBoroughs, setFilterBoroughs] = useState<string[]>([]);
  const [filterMinScore, setFilterMinScore] = useState<string>('');
  const [filterMinPortfolio, setFilterMinPortfolio] = useState<string>('10');
  const [filterHasPhone, setFilterHasPhone] = useState<boolean | null>(null);
  const [filterHasEmail, setFilterHasEmail] = useState<boolean | null>(null);
  const [filterHasWebsite, setFilterHasWebsite] = useState<boolean | null>(null);
  const [filterEntityType, setFilterEntityType] = useState<string>('');
  const [filterOutreachStatus, setFilterOutreachStatus] = useState<string>('');
  const [filterPipelineStage, setFilterPipelineStage] = useState<string>('');
  const [filterMinUnits, setFilterMinUnits] = useState<string>('');
  const [showMoreFilters, setShowMoreFilters] = useState(false);
  
  // Sorting
  const [sortField, setSortField] = useState<SortField>('score');
  const [sortDir, setSortDir] = useState<SortDir>('desc');
  
  // Server-side pagination
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(50);
  
  // Selection for bulk actions
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [enriching, setEnriching] = useState(false);

  const toggleBorough = (boro: string) => {
    setFilterBoroughs(prev => 
      prev.includes(boro) ? prev.filter(b => b !== boro) : [...prev, boro]
    );
  };

  // Count of secondary filters active (for badge)
  const activeSecondaryFilterCount = [
    filterEntityType, filterOutreachStatus, filterMinScore, filterMinUnits,
    filterHasWebsite === true ? 'yes' : '',
  ].filter(Boolean).length;

  // Debounced search term for server requests
  const [debouncedSearch, setDebouncedSearch] = useState('');
  
  // Debounce search input (300ms)
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(searchTerm), 300);
    return () => clearTimeout(timer);
  }, [searchTerm]);

  // Fetch leads from server with current filters, sort, and search
  const loadLeads = useCallback(async (currentPage: number = 0) => {
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, any> = {
        limit: pageSize,
        offset: currentPage * pageSize,
        sort_by: sortField === 'agent_name' ? 'company_name' : sortField,
        sort_dir: sortDir,
      };
      
      const minScore = parseFloat(filterMinScore);
      if (!isNaN(minScore) && minScore > 0) params.min_score = minScore;
      
      const minPortfolio = parseInt(filterMinPortfolio);
      if (!isNaN(minPortfolio) && minPortfolio > 0) params.min_portfolio = minPortfolio;
      
      if (filterBoroughs.length > 0) params.boro = filterBoroughs.join(',');
      if (filterHasPhone !== null) params.has_phone = filterHasPhone;
      if (filterHasEmail !== null) params.has_email = filterHasEmail;
      if (filterHasWebsite !== null) params.has_website = filterHasWebsite;
      if (filterEntityType) params.entity_type = filterEntityType;
      if (filterOutreachStatus) params.outreach_status = filterOutreachStatus;
      if (filterPipelineStage) params.pipeline_stage = filterPipelineStage;
      if (debouncedSearch.trim()) params.search = debouncedSearch.trim();
      
      const minUnits = parseInt(filterMinUnits);
      if (!isNaN(minUnits) && minUnits > 0) params.min_units = minUnits;
      
      const response = await fetchLeads(params);
      setLeads(response.leads);
      setTotalLeads(response.total);
    } catch (err) {
      console.error('Failed to fetch leads:', err);
      setError('Failed to load leads. Make sure the backend is running.');
    } finally {
      setLoading(false);
    }
  }, [filterMinScore, filterMinPortfolio, filterBoroughs, filterHasPhone, filterHasEmail, filterHasWebsite, filterEntityType, filterOutreachStatus, filterPipelineStage, filterMinUnits, pageSize, sortField, sortDir, debouncedSearch]);

  // R4: Health check on initial mount — show cold-start banner if backend is booting
  useEffect(() => {
    let cancelled = false;
    let timerId: ReturnType<typeof setTimeout>;
    
    const pollHealth = async () => {
      const health = await checkHealth();
      if (cancelled) return;
      if (health.status === 'starting') {
        setBackendStarting(true);
        timerId = setTimeout(pollHealth, HEALTH_POLL_INTERVAL);
      } else {
        setBackendStarting(false);
        // Backend is ready — trigger first data load
        loadLeads(0);
      }
    };
    
    pollHealth();
    return () => { cancelled = true; clearTimeout(timerId); };
    // Only run once on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Reload on filter change - reset to page 0 (skip if backend still starting)
  const isFirstLoad = React.useRef(true);
  useEffect(() => {
    if (isFirstLoad.current) {
      isFirstLoad.current = false;
      return; // Skip first run — handled by health check above
    }
    if (backendStarting) return;
    setPage(0);
    loadLeads(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadLeads]);

  // Page change (user clicking next/prev) - only fire when page > 0 to avoid double-fetch
  useEffect(() => {
    if (page > 0) {
      loadLeads(page);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  const totalPages = Math.ceil(totalLeads / pageSize);

  // Server handles filtering, sorting, and search — display leads directly
  const displayLeads = leads;

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDir('desc');
    }
    setPage(0); // Reset to first page on sort change
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
    if (selectedIds.size === displayLeads.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(displayLeads.map(l => l.lead_id)));
    }
  };

  const handleEnrichSelected = async () => {
    if (selectedIds.size === 0) return;
    setEnriching(true);
    try {
      await enrichLeads(Array.from(selectedIds));
      loadLeads(page);
      setSelectedIds(new Set());
    } catch (err) {
      console.error('Enrichment failed:', err);
    } finally {
      setEnriching(false);
    }
  };

  const clearFilters = () => {
    setSearchTerm('');
    setFilterBoroughs([]);
    setFilterMinScore('');
    setFilterMinPortfolio('10');
    setFilterHasPhone(null);
    setFilterHasEmail(null);
    setFilterHasWebsite(null);
    setFilterEntityType('');
    setFilterOutreachStatus('');
    setFilterPipelineStage('');
    setFilterMinUnits('');
  };

  const exportToCsv = async () => {
    // R5: Removed duplicate API_BASE_URL — imported from api.ts
    const params = new URLSearchParams();
    params.set('limit', totalLeads.toString());
    
    const minScore = parseFloat(filterMinScore);
    if (!isNaN(minScore) && minScore > 0) params.set('min_score', minScore.toString());
    
    const minPortfolio = parseInt(filterMinPortfolio);
    if (!isNaN(minPortfolio) && minPortfolio > 0) params.set('min_portfolio', minPortfolio.toString());
    
    if (filterBoroughs.length > 0) params.set('boro', filterBoroughs.join(','));
    if (filterHasPhone === true) params.set('has_phone', 'true');
    if (filterHasEmail === true) params.set('has_email', 'true');
    if (filterEntityType) params.set('entity_type', filterEntityType);

    try {
      const response = await fetch(`${API_BASE_URL}/api/export/csv?${params.toString()}`);
      if (!response.ok) throw new Error('Export failed');
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `hpd_leads_${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Server export failed, falling back to client export:', err);
      const headers = ['Company','Company Name','Entity Type','Primary Contact','Owner Name','Boroughs','Buildings','Units','Score','Est Annual Revenue','Violations/Unit','Violation Count','Phone','Email','Website','Enrichment Status','Outreach Status','Pipeline Stage','Priority','Next Follow-Up'];
      const rows = displayLeads.map(lead => [
        lead.agent_name || '', lead.company_name || '', lead.entity_type || '', lead.primary_contact || '',
        lead.owner_name || '', lead.boros?.join(', ') || lead.boro || '', lead.portfolio_size, lead.total_units,
        lead.score.toFixed(1), lead.estimated_annual_revenue || 0, lead.violations_per_unit || 0, lead.violation_count || 0,
        lead.phone || '', lead.email || '', lead.website || '', lead.enrichment_status, lead.outreach_status || 'new',
        lead.pipeline_stage || '', lead.priority_rank || 0, lead.next_follow_up || ''
      ]);
      const escapeValue = (val: string | number) => {
        const str = String(val);
        return (str.includes(',') || str.includes('"') || str.includes('\n')) ? `"${str.replace(/"/g, '""')}"` : str;
      };
      const csvContent = [headers.join(','), ...rows.map(row => row.map(escapeValue).join(','))].join('\n');
      const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `hpd_leads_${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    }
  };

  const SortIcon = ({ field }: { field: SortField }) => (
    <span className="ml-1 text-slate-600">
      {sortField === field ? (sortDir === 'asc' ? '↑' : '↓') : '↕'}
    </span>
  );

  // R4: Cold-start banner
  if (backendStarting) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center space-y-4">
        <div className="w-12 h-12 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
        <h2 className="text-lg font-semibold text-blue-400">Backend is starting up</h2>
        <p className="text-slate-400 text-sm max-w-md">
          This usually takes 1–2 minutes after inactivity. The page will load automatically once the server is ready.
        </p>
        <p className="text-slate-600 text-xs">Polling every {HEALTH_POLL_INTERVAL / 1000}s...</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="space-y-4 animate-pulse">
        <div className="h-12 bg-slate-800/50 rounded-xl" />
        <div className="h-10 bg-slate-800/30 rounded-xl" />
        {[...Array(8)].map((_, i) => <div key={i} className="h-14 bg-slate-800/20 rounded-lg" />)}
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center space-y-4">
        <div className="text-rose-400 mb-4">{error}</div>
        <p className="text-slate-500 text-sm">Try refreshing the page or check the Dashboard for data status.</p>
        {/* R5: Retry button */}
        <button 
          onClick={() => { setError(null); loadLeads(page); }}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-sm font-medium transition-colors"
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Filter Bar */}
      <div className="bg-slate-900/60 border border-white/5 rounded-xl p-3 md:p-4">
        {/* Primary Filters Row */}
        <div className="flex flex-wrap gap-2 md:gap-3 items-center">
          <div className="flex-1 min-w-[160px] md:min-w-[200px]">
            <input type="text" placeholder="Search name, company, address..."
              className="w-full px-3 py-2 bg-slate-950 border border-white/10 rounded-lg text-sm text-slate-100 focus:outline-none focus:ring-1 focus:ring-blue-500"
              value={searchTerm} onChange={(e) => setSearchTerm(e.target.value)} />
          </div>
          {/* Borough multi-select toggles */}
          <div className="flex gap-1">
            {BOROUGHS.map(b => (
              <button key={b} onClick={() => toggleBorough(b)}
                className={`px-2 py-1.5 rounded text-xs font-medium transition-colors ${
                  filterBoroughs.includes(b)
                    ? 'bg-blue-600/30 text-blue-300 border border-blue-500/40'
                    : 'bg-slate-800 text-slate-500 border border-white/5 hover:text-slate-300'
                }`}>
                {BOROUGH_SHORT[b] || b}
              </button>
            ))}
          </div>
          <select className="px-3 py-2 bg-slate-950 border border-white/10 rounded-lg text-sm text-slate-300" value={filterPipelineStage} onChange={(e) => setFilterPipelineStage(e.target.value)}>
            <option value="">All Stages</option>
            <option value="research">Research</option>
            <option value="first_contact">First Contact</option>
            <option value="follow_up">Follow-Up</option>
            <option value="meeting_scheduled">Meeting Set</option>
            <option value="meeting_done">Meeting Done</option>
            <option value="loi">LOI</option>
            <option value="due_diligence">Due Diligence</option>
            <option value="closed">Closed</option>
          </select>
          {/* Min Buildings - promoted to primary row */}
          <div className="flex items-center gap-1.5" title="Only show leads managing at least this many buildings">
            <span className="text-[10px] text-slate-600 uppercase">Min Buildings</span>
            <input type="number" className="w-16 px-2 py-1.5 bg-slate-950 border border-white/10 rounded-lg text-sm text-slate-100" value={filterMinPortfolio} onChange={(e) => setFilterMinPortfolio(e.target.value)} />
          </div>
          <div className="flex gap-1.5">
            <button onClick={() => setFilterHasPhone(filterHasPhone === true ? null : true)}
              className={`px-2 py-1.5 rounded text-xs font-medium transition-colors flex items-center gap-1 ${filterHasPhone === true ? 'bg-emerald-600/30 text-emerald-400 border border-emerald-500/30' : 'bg-slate-800 text-slate-500 border border-white/5 hover:text-slate-300'}`}>
              {filterHasPhone === true && <span className="text-emerald-400">✓</span>}Phone
            </button>
            <button onClick={() => setFilterHasEmail(filterHasEmail === true ? null : true)}
              className={`px-2 py-1.5 rounded text-xs font-medium transition-colors flex items-center gap-1 ${filterHasEmail === true ? 'bg-emerald-600/30 text-emerald-400 border border-emerald-500/30' : 'bg-slate-800 text-slate-500 border border-white/5 hover:text-slate-300'}`}>
              {filterHasEmail === true && <span className="text-emerald-400">✓</span>}Email
            </button>
          </div>
          <button onClick={() => setShowMoreFilters(!showMoreFilters)} className="px-3 py-2 text-xs text-slate-500 hover:text-slate-300 transition-colors relative">
            {showMoreFilters ? 'Less Filters' : 'More Filters'}
            {activeSecondaryFilterCount > 0 && !showMoreFilters && (
              <span className="absolute -top-1 -right-1 w-4 h-4 bg-blue-500 text-white text-[9px] rounded-full flex items-center justify-center font-bold">{activeSecondaryFilterCount}</span>
            )}
          </button>
          <button onClick={clearFilters} className="px-3 py-2 text-xs text-slate-500 hover:text-slate-300 transition-colors">Clear</button>
          <button onClick={exportToCsv} disabled={displayLeads.length === 0}
            className="px-3 py-2 bg-slate-800 border border-white/10 rounded-lg text-xs text-slate-300 hover:bg-slate-700 hover:text-white disabled:opacity-40 transition-colors flex items-center gap-1.5">
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
            Export
          </button>
        </div>
        {/* More Filters (collapsed) */}
        {showMoreFilters && (
          <div className="flex flex-wrap gap-3 items-center mt-3 pt-3 border-t border-white/5">
            <select className="px-3 py-2 bg-slate-950 border border-white/10 rounded-lg text-sm text-slate-300" value={filterEntityType} onChange={(e) => setFilterEntityType(e.target.value)}>
              <option value="">All Entity Types</option>
              <option value="company">Company</option>
              <option value="individual_agent">Individual Agent</option>
              <option value="owner_operator">Owner-Operator</option>
            </select>
            <select className="px-3 py-2 bg-slate-950 border border-white/10 rounded-lg text-sm text-slate-300" value={filterOutreachStatus} onChange={(e) => setFilterOutreachStatus(e.target.value)}>
              <option value="">All Outreach</option>
              <option value="new">New</option>
              <option value="contacted">Contacted</option>
              <option value="interested">Interested</option>
              <option value="not_interested">Not Interested</option>
              <option value="closed">Closed</option>
            </select>
            <input type="number" placeholder="Min Score" className="w-24 px-2 py-2 bg-slate-950 border border-white/10 rounded-lg text-sm text-slate-100" value={filterMinScore} onChange={(e) => setFilterMinScore(e.target.value)} />
            <input type="number" placeholder="Min Units" className="w-24 px-2 py-2 bg-slate-950 border border-white/10 rounded-lg text-sm text-slate-100" value={filterMinUnits} onChange={(e) => setFilterMinUnits(e.target.value)} />
            <button onClick={() => setFilterHasWebsite(filterHasWebsite === true ? null : true)}
              className={`px-2 py-1.5 rounded text-xs font-medium transition-colors flex items-center gap-1 ${filterHasWebsite === true ? 'bg-emerald-600/30 text-emerald-400 border border-emerald-500/30' : 'bg-slate-800 text-slate-500 border border-white/5 hover:text-slate-300'}`}>
              {filterHasWebsite === true && <span className="text-emerald-400">✓</span>}Website
            </button>
          </div>
        )}
        
        {/* Results count and bulk actions */}
        <div className="flex justify-between items-center mt-3 pt-3 border-t border-white/5">
          <div className="text-sm text-slate-500">
            Showing <span className="text-slate-300 font-medium">{displayLeads.length}</span> of <span className="text-slate-300 font-medium">{totalLeads.toLocaleString()}</span> leads
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

      {/* Data Table — desktop table, mobile cards */}
      <div className="bg-slate-900/40 border border-white/5 rounded-xl overflow-hidden">
        {/* Desktop Table */}
        <div className="hidden md:block overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="bg-slate-950/80 text-slate-500 text-xs uppercase tracking-wider border-b border-white/5">
                <th className="px-4 py-3 w-8">
                  <input
                    type="checkbox"
                    checked={selectedIds.size === displayLeads.length && displayLeads.length > 0}
                    onChange={selectAll}
                    className="rounded bg-slate-800 border-slate-600"
                  />
                </th>
                <th className="px-4 py-3 cursor-pointer hover:text-slate-300 transition-colors" onClick={() => handleSort('agent_name')}>
                  Company <SortIcon field="agent_name" />
                </th>
                <th className="px-4 py-3 cursor-pointer hover:text-slate-300 transition-colors" onClick={() => handleSort('boro')}>
                  Borough <SortIcon field="boro" />
                </th>
                <th className="px-4 py-3 cursor-pointer hover:text-slate-300 transition-colors text-right" onClick={() => handleSort('portfolio_size')}>
                  Bldgs <SortIcon field="portfolio_size" />
                </th>
                <th className="px-4 py-3 cursor-pointer hover:text-slate-300 transition-colors text-right" onClick={() => handleSort('total_units')}>
                  Units <SortIcon field="total_units" />
                </th>
                <th className="px-4 py-3 cursor-pointer hover:text-slate-300 transition-colors text-right" onClick={() => handleSort('score')}
                  title="Lead quality score (0–100) based on portfolio size, building types, registration status, and data completeness. Higher = stronger acquisition target.">
                  Score <SortIcon field="score" />
                </th>
                <th className="px-4 py-3 cursor-pointer hover:text-slate-300 transition-colors text-right" onClick={() => handleSort('estimated_annual_revenue')}
                  title="Estimated annual management fee revenue = Total Units × Average Rent (by borough & building type) × 5% management fee rate">
                  Mgmt Fee <SortIcon field="estimated_annual_revenue" />
                </th>
                <th className="px-4 py-3 cursor-pointer hover:text-slate-300 transition-colors text-right" onClick={() => handleSort('violations_per_unit')}
                  title="Open HPD violations per residential unit — higher = more maintenance issues, potential distressed asset opportunity">
                  Violations <SortIcon field="violations_per_unit" />
                </th>
                <th className="px-4 py-3">Contact</th>
                <th className="px-4 py-3 cursor-pointer hover:text-slate-300 transition-colors" onClick={() => handleSort('enrichment_status')}
                  title="How much contact data we have: green dots = phone/email found, website found, full research complete">
                  Info <SortIcon field="enrichment_status" />
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {displayLeads.map((lead) => {
                return (
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
                    <div className="font-medium text-slate-200 text-sm">{lead.company_name || lead.agent_name || lead.owner_name}</div>
                    <div className="text-xs text-slate-600 truncate max-w-[250px] flex items-center gap-1.5">
                      <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold uppercase ${
                        lead.entity_type === 'company' ? 'bg-blue-900/40 text-blue-400' :
                        lead.entity_type === 'individual_agent' ? 'bg-amber-900/40 text-amber-400' :
                        lead.entity_type === 'owner_operator' ? 'bg-purple-900/40 text-purple-400' :
                        'bg-slate-800 text-slate-600'
                      }`} title={lead.entity_type === 'company' ? 'Registered management company or housing entity' : lead.entity_type === 'individual_agent' ? 'Individual person acting as managing agent for these buildings' : lead.entity_type === 'owner_operator' ? 'Property owner who self-manages without a separate management company' : 'Entity type could not be determined from HPD records'}>
                        {lead.entity_type === 'company' ? 'Company' : 
                         lead.entity_type === 'individual_agent' ? 'Individual' : 
                         lead.entity_type === 'owner_operator' ? 'Owner-Op' : 'Unknown'}
                      </span>
                      {lead.primary_contact && 
                       lead.primary_contact.toLowerCase() !== (lead.company_name || lead.agent_name || lead.owner_name || '').toLowerCase() && (
                        <span className="text-slate-500 truncate">{lead.primary_contact}</span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {(lead.boros?.length > 0 ? lead.boros : [lead.boro]).filter(Boolean).map((b, i) => (
                        <span key={i} className="px-1.5 py-0.5 bg-slate-800 text-slate-400 text-[10px] rounded capitalize">
                          {b ? b.charAt(0) + b.slice(1).toLowerCase() : ''}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <span className="text-slate-300 font-mono text-sm">{lead.portfolio_size}</span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <span className="text-blue-400 font-mono text-sm">{lead.total_units.toLocaleString()}</span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <span className={`font-mono text-sm font-bold ${scoreColor(lead.score)}`}
                      title={`Score ${lead.score.toFixed(1)}/100 — ${lead.score >= 60 ? 'Strong target' : lead.score >= 40 ? 'Moderate potential' : 'Lower priority'}`}>
                      {lead.score.toFixed(0)}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    {lead.estimated_annual_revenue > 0 ? (
                      <span className="font-mono text-sm text-emerald-400">
                        {formatCurrency(lead.estimated_annual_revenue)}
                      </span>
                    ) : (
                      <span className="text-slate-700">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right">
                    {lead.violations_per_unit > 0 ? (
                      <div className="flex items-center justify-end gap-1.5" title={`${lead.violation_count.toLocaleString()} total open violations\nPer unit: ${lead.violations_per_unit.toFixed(2)}\nClass A (non-hazardous): ${lead.violation_class_a}\nClass B (hazardous): ${lead.violation_class_b}\nClass C (immediately hazardous): ${lead.violation_class_c}`}>
                        <span className={`w-2 h-2 rounded-full ${
                          lead.violations_per_unit > 1.0 ? 'bg-rose-500' :
                          lead.violations_per_unit > 0.3 ? 'bg-amber-500' : 'bg-emerald-500'
                        }`} />
                        <span className={`font-mono text-sm ${
                          lead.violations_per_unit > 1.0 ? 'text-rose-400' :
                          lead.violations_per_unit > 0.3 ? 'text-amber-400' : 'text-slate-300'
                        }`}>{lead.violations_per_unit.toFixed(2)}</span>
                        <span className="text-[9px] text-slate-600">per unit</span>
                      </div>
                    ) : lead.violation_count > 0 ? (
                      <span className="font-mono text-sm text-slate-400" title={`${lead.violation_count} total violations (per-unit data not yet available)`}>{lead.violation_count}</span>
                    ) : (
                      <span className="text-slate-700" title="No open HPD violations on record, or data not yet loaded">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                    <div className="flex gap-1.5">
                      {lead.phone && (
                        <a href={`tel:${lead.phone}`} title={`Call: ${lead.phone}`} className="p-1.5 hover:bg-emerald-900/50 rounded transition-colors">
                          <svg className="w-4 h-4 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z"/>
                          </svg>
                        </a>
                      )}
                      {lead.email && (
                        <a href={`mailto:${lead.email}`} title={`Email: ${lead.email}`} className="p-1.5 hover:bg-blue-900/50 rounded transition-colors">
                          <svg className="w-4 h-4 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>
                          </svg>
                        </a>
                      )}
                      {lead.website && (
                        <a href={lead.website.startsWith('http') ? lead.website : `https://${lead.website}`} target="_blank" rel="noopener noreferrer" title={`Website: ${lead.website}`} className="p-1.5 hover:bg-purple-900/50 rounded transition-colors">
                          <svg className="w-4 h-4 text-purple-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9"/>
                          </svg>
                        </a>
                      )}
                      {!lead.phone && !lead.email && !lead.website && <span className="text-slate-700">—</span>}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-col gap-1">
                      <div className="flex items-center gap-1.5" title={`Contact: ${lead.phone || lead.email ? 'Found' : 'Missing'} • Website: ${lead.website ? 'Found' : 'Missing'} • Research: ${lead.enrichment_status}`}>
                        <span className={`text-[9px] font-medium ${lead.phone || lead.email ? 'text-emerald-400' : 'text-slate-700'}`}>
                          {lead.phone || lead.email ? '● Contact' : '○ Contact'}
                        </span>
                        <span className={`text-[9px] font-medium ${lead.website ? 'text-emerald-400' : 'text-slate-700'}`}>
                          {lead.website ? '●' : '○'}
                        </span>
                        <span className={`text-[9px] font-medium ${lead.enrichment_status === 'complete' ? 'text-emerald-400' : lead.enrichment_status === 'partial' ? 'text-amber-400' : 'text-slate-700'}`}>
                          {lead.enrichment_status === 'complete' ? '●' : '○'}
                        </span>
                      </div>
                      {lead.pipeline_stage && lead.pipeline_stage !== 'research' && (
                        <span className="px-2 py-0.5 text-[10px] rounded bg-blue-900/30 text-blue-400 inline-block w-fit">
                          {lead.pipeline_stage.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}
                        </span>
                      )}
                    </div>
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* Empty State */}
        {displayLeads.length === 0 && !loading && (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <svg className="w-12 h-12 text-slate-700 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
            </svg>
            <p className="text-slate-400 text-sm font-medium">No leads match your filters</p>
            <p className="text-slate-600 text-xs mt-1">Try adjusting your search or filter criteria</p>
            <button onClick={clearFilters} className="mt-3 px-4 py-1.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-xs font-medium transition-colors">
              Clear All Filters
            </button>
          </div>
        )}

        {/* Mobile Card View */}
        <div className="md:hidden divide-y divide-white/5">
          {displayLeads.map((lead) => (
            <div
              key={lead.lead_id}
              className="p-4 active:bg-slate-800/50 transition-colors cursor-pointer"
              onClick={() => onSelectLead(lead)}
            >
              <div className="flex justify-between items-start mb-2">
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-slate-200 text-sm truncate">{lead.company_name || lead.agent_name || lead.owner_name}</div>
                  <div className="flex items-center gap-1.5 mt-1">
                    <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold uppercase ${
                      lead.entity_type === 'company' ? 'bg-blue-900/40 text-blue-400' :
                      lead.entity_type === 'individual_agent' ? 'bg-amber-900/40 text-amber-400' :
                      lead.entity_type === 'owner_operator' ? 'bg-purple-900/40 text-purple-400' :
                      'bg-slate-800 text-slate-600'
                    }`}>
                      {lead.entity_type === 'company' ? 'Company' : 
                       lead.entity_type === 'individual_agent' ? 'Individual' : 
                       lead.entity_type === 'owner_operator' ? 'Owner-Op' : 'Unknown'}
                    </span>
                    {(lead.boros?.length > 0 ? lead.boros : [lead.boro]).filter(Boolean).slice(0, 2).map((b, i) => (
                      <span key={i} className="px-1 py-0.5 bg-slate-800 text-slate-400 text-[9px] rounded capitalize">
                        {b ? b.charAt(0) + b.slice(1).toLowerCase() : ''}
                      </span>
                    ))}
                  </div>
                </div>
                <span className={`font-mono text-lg font-bold ${scoreColor(lead.score)}`}>
                  {lead.score.toFixed(0)}
                </span>
              </div>
              <div className="flex items-center gap-4 text-xs">
                <span className="text-slate-400"><span className="font-mono text-slate-300">{lead.portfolio_size}</span> bldgs</span>
                <span className="text-slate-400"><span className="font-mono text-blue-400">{lead.total_units.toLocaleString()}</span> units</span>
                {lead.estimated_annual_revenue > 0 && (
                  <span className="font-mono text-emerald-400">{formatCurrency(lead.estimated_annual_revenue)}</span>
                )}
                {lead.violations_per_unit > 0 && (
                  <span className={`font-mono ${lead.violations_per_unit > 1.0 ? 'text-rose-400' : 'text-amber-400'}`}>
                    {lead.violations_per_unit.toFixed(2)} v/u
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2 mt-2" onClick={(e) => e.stopPropagation()}>
                {lead.phone && (
                  <a href={`tel:${lead.phone}`} className="p-2 bg-emerald-900/30 rounded-lg">
                    <svg className="w-4 h-4 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z"/>
                    </svg>
                  </a>
                )}
                {lead.email && (
                  <a href={`mailto:${lead.email}`} className="p-2 bg-blue-900/30 rounded-lg">
                    <svg className="w-4 h-4 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>
                    </svg>
                  </a>
                )}
                {lead.pipeline_stage && lead.pipeline_stage !== 'research' && (
                  <span className="px-2 py-1 text-[10px] rounded bg-blue-900/30 text-blue-400 ml-auto">
                    {lead.pipeline_stage.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
        
        {/* Pagination */}
        <div className="flex items-center justify-between px-4 py-3 border-t border-white/5 bg-slate-950/50">
          <button
            onClick={() => setPage(Math.max(0, page - 1))}
            disabled={page === 0}
            className="px-3 py-1 text-sm text-slate-400 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            ← Previous
          </button>
          <div className="flex items-center gap-4">
            <div className="text-sm text-slate-500">
              Page {page + 1} of {totalPages || 1}
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate-600">Per page:</span>
              <select
                value={pageSize}
                onChange={(e) => { setPageSize(Number(e.target.value)); setPage(0); }}
                className="px-2 py-1 bg-slate-800 border border-white/10 rounded text-xs text-slate-300 focus:outline-none"
              >
                <option value={25}>25</option>
                <option value={50}>50</option>
                <option value={100}>100</option>
                <option value={250}>250</option>
                <option value={500}>500</option>
              </select>
            </div>
          </div>
          <button
            onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
            disabled={page >= totalPages - 1}
            className="px-3 py-1 text-sm text-slate-400 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            Next →
          </button>
        </div>
      </div>
    </div>
  );
};

export default LeadTable;
