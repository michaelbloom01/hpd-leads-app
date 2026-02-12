
import React, { useState, useEffect, useCallback } from 'react';
import { fetchLeads, ApiLead, enrichLeads, API_BASE_URL, checkHealth, searchBuildings, BuildingSearchResult } from '../services/api';

export interface LeadFilterPreset {
  outreachStatuses?: string[];
  pipelineStages?: string[];
  enrichmentStatuses?: string[];
  entityTypes?: string[];
  minPortfolio?: string;
  hasPhone?: boolean;
  hasEmail?: boolean;
}

interface Props {
  onSelectLead: (lead: ApiLead) => void;
  filterPreset?: LeadFilterPreset | null;
  onFilterPresetConsumed?: () => void;
}

// R4: Cold-start polling interval (ms)
const HEALTH_POLL_INTERVAL = 5000;

type SortField = 'agent_name' | 'portfolio_size' | 'total_units' | 'units_per_bldg' | 'score' | 'boro' | 'enrichment_status' | 'estimated_annual_revenue' | 'violations_per_unit';
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
  if (score >= 60) return 'text-emerald-600';
  if (score >= 40) return 'text-amber-600';
  return 'text-gray-400';
};

const LeadTable: React.FC<Props> = ({ onSelectLead, filterPreset, onFilterPresetConsumed }) => {
  const [leads, setLeads] = useState<ApiLead[]>([]);
  const [totalLeads, setTotalLeads] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [backendStarting, setBackendStarting] = useState(false);
  
  // Server-side filters
  const [searchTerm, setSearchTerm] = useState('');
  const [filterBoroughs, setFilterBoroughs] = useState<string[]>([]);
  const [filterMinScore, setFilterMinScore] = useState<string>('');
  const [filterMaxScore, setFilterMaxScore] = useState<string>('');
  const [filterMinPortfolio, setFilterMinPortfolio] = useState<string>('');
  const [filterMaxPortfolio, setFilterMaxPortfolio] = useState<string>('');
  const [filterHasPhone, setFilterHasPhone] = useState<boolean | null>(null);
  const [filterHasEmail, setFilterHasEmail] = useState<boolean | null>(null);
  const [filterHasWebsite, setFilterHasWebsite] = useState<boolean | null>(null);
  const [filterEntityTypes, setFilterEntityTypes] = useState<string[]>([]);
  const [filterOutreachStatuses, setFilterOutreachStatuses] = useState<string[]>([]);
  const [filterPipelineStages, setFilterPipelineStages] = useState<string[]>([]);
  const [filterEnrichmentStatuses, setFilterEnrichmentStatuses] = useState<string[]>([]);
  const [filterEnrichmentStatus, setFilterEnrichmentStatus] = useState<string>('');
  const [filterMinUnits, setFilterMinUnits] = useState<string>('');
  const [filterMaxUnits, setFilterMaxUnits] = useState<string>('');
  const [filterMinUnitsPerBldg, setFilterMinUnitsPerBldg] = useState<string>('');
  const [filterMaxUnitsPerBldg, setFilterMaxUnitsPerBldg] = useState<string>('');
  const [filterBuildingTypes, setFilterBuildingTypes] = useState<string[]>([]);
  const [showMoreFilters, setShowMoreFilters] = useState(false);
  // Address search mode
  const [searchMode, setSearchMode] = useState<'leads' | 'address'>('leads');
  const [addressResults, setAddressResults] = useState<BuildingSearchResult[]>([]);
  const [addressSearching, setAddressSearching] = useState(false);
  
  
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

  const toggleBuildingType = (type: string) => {
    setFilterBuildingTypes(prev =>
      prev.includes(type) ? prev.filter(t => t !== type) : [...prev, type]
    );
  };
  const toggleEntityType = (type: string) => {
    setFilterEntityTypes(prev =>
      prev.includes(type) ? prev.filter(t => t !== type) : [...prev, type]
    );
  };

  const toggleOutreachStatus = (status: string) => {
    setFilterOutreachStatuses(prev =>
      prev.includes(status) ? prev.filter(s => s !== status) : [...prev, status]
    );
  };

  const togglePipelineStage = (stage: string) => {
    setFilterPipelineStages(prev =>
      prev.includes(stage) ? prev.filter(s => s !== stage) : [...prev, stage]
    );
  };

  const toggleEnrichmentStatus = (status: string) => {
    setFilterEnrichmentStatuses(prev =>
      prev.includes(status) ? prev.filter(s => s !== status) : [...prev, status]
    );
  };


  // Count of secondary filters active (for badge)
  const activeFiltersCount = [
    filterEntityTypes.length > 0 ? 'yes' : '',
    filterOutreachStatuses.length > 0 ? 'yes' : '',
    filterEnrichmentStatuses.length > 0 ? 'yes' : '',
    filterPipelineStages.length > 0 ? 'yes' : '',
    filterMinScore, filterMaxScore,
    filterMinUnits, filterMaxUnits,
    filterMinPortfolio, filterMaxPortfolio,
    filterMinUnitsPerBldg, filterMaxUnitsPerBldg,
    filterHasWebsite === true ? 'yes' : '',
    filterBuildingTypes.length > 0 ? 'yes' : '',
  ].filter(Boolean).length;

  // Fetch leads from server with current filters, sort, and search
  // This is called explicitly by the "Go" button, sort clicks, page changes, etc.
  // It reads directly from current filter state — no debouncing needed.
  const loadLeadsRef = React.useRef<(currentPage?: number) => Promise<void>>();
  
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
      const maxScore = parseFloat(filterMaxScore);
      if (!isNaN(maxScore) && maxScore > 0) params.max_score = maxScore;
      
      const minPortfolio = parseInt(filterMinPortfolio);
      if (!isNaN(minPortfolio) && minPortfolio > 0) params.min_portfolio = minPortfolio;
      const maxPortfolio = parseInt(filterMaxPortfolio);
      if (!isNaN(maxPortfolio) && maxPortfolio > 0) params.max_portfolio = maxPortfolio;
      
      if (filterBoroughs.length > 0) params.boro = filterBoroughs.join(',');
      if (filterHasPhone !== null) params.has_phone = filterHasPhone;
      if (filterHasEmail !== null) params.has_email = filterHasEmail;
      if (filterEntityTypes.length > 0) params.entity_type = filterEntityTypes.join(',');
      if (filterOutreachStatuses.length > 0) params.outreach_status = filterOutreachStatuses.join(',');
      if (filterPipelineStages.length > 0) params.pipeline_stage = filterPipelineStages.join(',');
      if (filterEnrichmentStatuses.length > 0) params.enrichment_status = filterEnrichmentStatuses.join(',');
      if (searchTerm.trim()) params.search = searchTerm.trim();
      
      const minUnits = parseInt(filterMinUnits);
      if (!isNaN(minUnits) && minUnits > 0) params.min_units = minUnits;
      const maxUnits = parseInt(filterMaxUnits);
      if (!isNaN(maxUnits) && maxUnits > 0) params.max_units = maxUnits;

      const minUpb = parseFloat(filterMinUnitsPerBldg);
      if (!isNaN(minUpb) && minUpb > 0) params.min_units_per_bldg = minUpb;
      const maxUpb = parseFloat(filterMaxUnitsPerBldg);
      if (!isNaN(maxUpb) && maxUpb > 0) params.max_units_per_bldg = maxUpb;

      if (filterBuildingTypes.length > 0) params.building_type_has = filterBuildingTypes.join(',');
      
      const response = await fetchLeads(params);
      setLeads(response.leads);
      setTotalLeads(response.total);
    } catch (err) {
      console.error('Failed to fetch leads:', err);
      setError('Failed to load leads. Make sure the backend is running.');
    } finally {
      setLoading(false);
    }
  }, [filterMinScore, filterMaxScore, filterMinPortfolio, filterMaxPortfolio, filterBoroughs, filterHasPhone, filterHasEmail, filterHasWebsite, filterEntityTypes, filterOutreachStatuses, filterPipelineStages, filterEnrichmentStatuses, filterMinUnits, filterMaxUnits, filterMinUnitsPerBldg, filterMaxUnitsPerBldg, filterBuildingTypes, pageSize, sortField, sortDir, searchTerm]);

  // Keep ref in sync so callbacks can call latest version
  loadLeadsRef.current = loadLeads;

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
        loadLeadsRef.current?.(0);
      }
    };
    
    pollHealth();
    return () => { cancelled = true; clearTimeout(timerId); };
    // Only run once on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-reload on sort change (clicking a column header is an explicit action)
  const hasMounted = React.useRef(false);
  useEffect(() => {
    if (!hasMounted.current) {
      hasMounted.current = true;
      return; // Skip first run — handled by health check
    }
    if (backendStarting) return;
    setPage(0);
    loadLeadsRef.current?.(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sortField, sortDir]);

  // Page change (user clicking next/prev) - only fire when page > 0 to avoid double-fetch
  useEffect(() => {
    if (page > 0) {
      loadLeadsRef.current?.(page);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  // Apply filters — called by the "Go" button
  const applyFilters = () => {
    setPage(0);
    loadLeads(0);
  };
  // Apply filter preset from Dashboard navigation
  useEffect(() => {
    if (!filterPreset) return;
    // Set all filter state from preset
    if (filterPreset.outreachStatuses) setFilterOutreachStatuses(filterPreset.outreachStatuses);
    if (filterPreset.pipelineStages) setFilterPipelineStages(filterPreset.pipelineStages);
    if (filterPreset.enrichmentStatuses) setFilterEnrichmentStatuses(filterPreset.enrichmentStatuses);
    if (filterPreset.entityTypes) setFilterEntityTypes(filterPreset.entityTypes);
    if (filterPreset.minPortfolio) setFilterMinPortfolio(filterPreset.minPortfolio);
    if (filterPreset.hasPhone !== undefined) setFilterHasPhone(filterPreset.hasPhone ? true : null);
    if (filterPreset.hasEmail !== undefined) setFilterHasEmail(filterPreset.hasEmail ? true : null);
    // Expand "More Filters" so user can see what's active
    setShowMoreFilters(true);
    // Signal consumed so App.tsx can clear it
    onFilterPresetConsumed?.();
    // Trigger load on next tick (after state updates)
    setTimeout(() => { loadLeadsRef.current?.(0); }, 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterPreset]);


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

  const clearFilters = async () => {
    setSearchTerm('');
    setFilterBoroughs([]);
    setFilterMinScore('');
    setFilterMaxScore('');
    setFilterMinPortfolio('');
    setFilterMaxPortfolio('');
    setFilterHasPhone(null);
    setFilterHasEmail(null);
    setFilterEntityTypes([]);
    setFilterOutreachStatuses([]);
    setFilterPipelineStages([]);
    setFilterEnrichmentStatuses([]);
    setFilterPipelineStage('');
    setFilterMinUnits('');
    setFilterMaxUnits('');
    setFilterMinUnitsPerBldg('');
    setFilterMaxUnitsPerBldg('');
    // Reload with no filters (use ref to get latest loadLeads after state resets)
    setPage(0);
    setTimeout(() => loadLeadsRef.current?.(0), 0);
  };

  const exportToCsv = async () => {
    // R5: Removed duplicate API_BASE_URL — imported from api.ts
    const params = new URLSearchParams();
    params.set('limit', totalLeads.toString());
    
    const minScore = parseFloat(filterMinScore);
    if (!isNaN(minScore) && minScore > 0) params.set('min_score', minScore.toString());
    const maxScore = parseFloat(filterMaxScore);
    if (!isNaN(maxScore) && maxScore > 0) params.set('max_score', maxScore.toString());
    
    const minPortfolio = parseInt(filterMinPortfolio);
    if (!isNaN(minPortfolio) && minPortfolio > 0) params.set('min_portfolio', minPortfolio.toString());
    const maxPortfolio = parseInt(filterMaxPortfolio);
    if (!isNaN(maxPortfolio) && maxPortfolio > 0) params.set('max_portfolio', maxPortfolio.toString());
    
    if (filterBoroughs.length > 0) params.set('boro', filterBoroughs.join(','));
    if (filterHasPhone === true) params.set('has_phone', 'true');
    if (filterEntityTypes.length > 0) params.set('entity_type', filterEntityTypes.join(','));
    if (filterEntityType) params.set('entity_type', filterEntityType);
    
    const minUnits = parseInt(filterMinUnits);
    if (!isNaN(minUnits) && minUnits > 0) params.set('min_units', minUnits.toString());
    const maxUnits = parseInt(filterMaxUnits);
    if (!isNaN(maxUnits) && maxUnits > 0) params.set('max_units', maxUnits.toString());
    const minUpb = parseFloat(filterMinUnitsPerBldg);
    if (!isNaN(minUpb) && minUpb > 0) params.set('min_units_per_bldg', minUpb.toString());
    const maxUpb = parseFloat(filterMaxUnitsPerBldg);
    if (!isNaN(maxUpb) && maxUpb > 0) params.set('max_units_per_bldg', maxUpb.toString());
    if (filterOutreachStatuses.length > 0) params.set('outreach_status', filterOutreachStatuses.join(','));
    if (filterPipelineStages.length > 0) params.set('pipeline_stage', filterPipelineStages.join(','));
    if (filterEnrichmentStatuses.length > 0) params.set('enrichment_status', filterEnrichmentStatuses.join(','));
    if (searchTerm.trim()) params.set('search', searchTerm.trim());
    if (filterBuildingTypes.length > 0) params.set('building_type_has', filterBuildingTypes.join(','));

    try {
      const { getAuthHeaders } = await import('../services/auth');
      const response = await fetch(`${API_BASE_URL}/api/export/csv?${params.toString()}`, {
        headers: getAuthHeaders(),
      });
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
    <span className="ml-1 text-gray-400">
      {sortField === field ? (sortDir === 'asc' ? '↑' : '↓') : '↕'}
    </span>
  );

  // R4: Cold-start banner
  if (backendStarting) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center space-y-4">
        <div className="w-12 h-12 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
        <h2 className="text-lg font-semibold text-blue-600">Backend is starting up</h2>
        <p className="text-gray-500 text-sm max-w-md">
          This usually takes 1–2 minutes after inactivity. The page will load automatically once the server is ready.
        </p>
        <p className="text-gray-400 text-xs">Polling every {HEALTH_POLL_INTERVAL / 1000}s...</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="space-y-4 animate-pulse">
        <div className="h-12 bg-gray-50 rounded-xl" />
        <div className="h-10 bg-gray-50 rounded-xl" />
        {[...Array(8)].map((_, i) => <div key={i} className="h-14 bg-gray-100 rounded-lg" />)}
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center space-y-4">
        <div className="text-rose-600 mb-4">{error}</div>
        <p className="text-gray-400 text-sm">Try refreshing the page or check the Dashboard for data status.</p>
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
      <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-3 md:p-4">
        {/* Primary Filters Row */}
        <div className="flex flex-wrap items-center gap-2 md:gap-3">
          <div className="flex-1 min-w-[160px] md:min-w-[200px] flex gap-1">
            <input type="text" placeholder={searchMode === 'leads' ? "Search name, company..." : "Search by address (e.g., 245 Bleecker)..."}
              className="flex-1 px-3 py-2 bg-white border border-gray-300 rounded-lg text-sm text-gray-900 focus:outline-none focus:ring-1 focus:ring-blue-500"
              value={searchTerm} onChange={(e) => setSearchTerm(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  if (searchMode === 'address' && searchTerm.trim()) {
                    setAddressSearching(true);
                    searchBuildings(searchTerm.trim()).then(res => {
                      setAddressResults(res.buildings);
                    }).catch(() => setAddressResults([])).finally(() => setAddressSearching(false));
                  } else {
                    applyFilters();
                  }
                }
              }} />
            <button
              onClick={() => { setSearchMode(searchMode === 'leads' ? 'address' : 'leads'); setAddressResults([]); }}
              className={`px-2 py-1.5 rounded-lg text-[10px] font-medium border transition-colors whitespace-nowrap ${
                searchMode === 'address'
                  ? 'bg-blue-50 border-blue-300 text-blue-700'
                  : 'bg-white border-gray-300 text-gray-500 hover:bg-gray-50'
              }`}
              title={searchMode === 'leads' ? 'Switch to address search' : 'Switch to lead search'}
            >
              {searchMode === 'leads' ? '🏢' : '👤'}
            </button>
          </div>
          {/* Borough multi-select toggles */}
          <div className="flex gap-1">
            {BOROUGHS.map(b => (
              <button key={b} onClick={() => toggleBorough(b)}
                className={`px-2 py-1.5 rounded text-xs font-medium transition-colors ${
                  filterBoroughs.includes(b)
                    ? 'bg-blue-50 text-blue-700 border border-blue-300'
                    : 'bg-gray-100 text-gray-500 border border-gray-200 hover:text-gray-700'
                }`}>
                {BOROUGH_SHORT[b] || b}
              </button>
            ))}
          </div>
          <div className="flex gap-1.5">
            <button onClick={() => setFilterHasPhone(filterHasPhone === true ? null : true)}
              className={`px-2 py-1.5 rounded text-xs font-medium transition-colors flex items-center gap-1 ${filterHasPhone === true ? 'bg-emerald-50 text-emerald-700 border border-emerald-300' : 'bg-gray-100 text-gray-500 border border-gray-200 hover:text-gray-700'}`}>
              {filterHasPhone === true && <span className="text-emerald-600">✓</span>}Phone
            </button>
            <button onClick={() => setFilterHasEmail(filterHasEmail === true ? null : true)}
              className={`px-2 py-1.5 rounded text-xs font-medium transition-colors flex items-center gap-1 ${filterHasEmail === true ? 'bg-emerald-50 text-emerald-700 border border-emerald-300' : 'bg-gray-100 text-gray-500 border border-gray-200 hover:text-gray-700'}`}>
              {filterHasEmail === true && <span className="text-emerald-600">✓</span>}Email
            </button>
          </div>
          <button onClick={() => setShowMoreFilters(!showMoreFilters)} className="px-3 py-2 text-xs text-gray-500 hover:text-gray-700 transition-colors relative">
            {showMoreFilters ? 'Less Filters' : 'More Filters'}
            {activeSecondaryFilterCount > 0 && !showMoreFilters && (
              <span className="absolute -top-1 -right-1 w-4 h-4 bg-blue-500 text-white text-[9px] rounded-full flex items-center justify-center font-bold">{activeSecondaryFilterCount}</span>
            )}
          </button>
          <button onClick={applyFilters} className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold rounded-lg transition-colors">
            Go
          </button>
          <button onClick={clearFilters} className="px-3 py-2 text-xs text-gray-500 hover:text-gray-700 transition-colors">Clear</button>
          <button onClick={exportToCsv} disabled={displayLeads.length === 0}
            className="px-3 py-2 bg-white border border-gray-200 rounded-lg text-xs text-gray-700 hover:bg-gray-50 hover:text-gray-900 disabled:opacity-40 transition-colors flex items-center gap-1.5">
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
            Export
          </button>
        </div>
        {/* More Filters (collapsed) */}
        {showMoreFilters && (
          <div className="mt-3 pt-3 border-t border-gray-200 space-y-3">
            {/* Row 1: Range filters */}
            <div className="flex flex-wrap gap-3 items-center">
              <div className="flex items-center gap-1" title="Filter by lead quality score (0-100)">
                <span className="text-[10px] text-gray-400 uppercase font-bold w-12">Score</span>
                <input type="number" placeholder="Min" className="w-14 px-1.5 py-1.5 bg-white border border-gray-300 rounded-lg text-xs text-gray-900 text-center" value={filterMinScore} onChange={(e) => setFilterMinScore(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && applyFilters()} />
                <span className="text-gray-400 text-xs">—</span>
                <input type="number" placeholder="Max" className="w-14 px-1.5 py-1.5 bg-white border border-gray-300 rounded-lg text-xs text-gray-900 text-center" value={filterMaxScore} onChange={(e) => setFilterMaxScore(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && applyFilters()} />
              </div>
              <div className="flex items-center gap-1" title="Filter by total residential units">
                <span className="text-[10px] text-gray-400 uppercase font-bold w-12">Units</span>
                <input type="number" placeholder="Min" className="w-16 px-1.5 py-1.5 bg-white border border-gray-300 rounded-lg text-xs text-gray-900 text-center" value={filterMinUnits} onChange={(e) => setFilterMinUnits(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && applyFilters()} />
                <span className="text-gray-400 text-xs">—</span>
                <input type="number" placeholder="Max" className="w-16 px-1.5 py-1.5 bg-white border border-gray-300 rounded-lg text-xs text-gray-900 text-center" value={filterMaxUnits} onChange={(e) => setFilterMaxUnits(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && applyFilters()} />
              </div>
              <div className="flex items-center gap-1" title="Filter by number of buildings in portfolio">
                <span className="text-[10px] text-gray-400 uppercase font-bold w-12">Bldgs</span>
                <input type="number" placeholder="Min" className="w-14 px-1.5 py-1.5 bg-white border border-gray-300 rounded-lg text-xs text-gray-900 text-center" value={filterMinPortfolio} onChange={(e) => setFilterMinPortfolio(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && applyFilters()} />
                <span className="text-gray-400 text-xs">—</span>
                <input type="number" placeholder="Max" className="w-14 px-1.5 py-1.5 bg-white border border-gray-300 rounded-lg text-xs text-gray-900 text-center" value={filterMaxPortfolio} onChange={(e) => setFilterMaxPortfolio(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && applyFilters()} />
              </div>
              <div className="flex items-center gap-1" title="Filter by average units per building">
                <span className="text-[10px] text-gray-400 uppercase font-bold w-12">U/Bldg</span>
                <input type="number" placeholder="Min" className="w-14 px-1.5 py-1.5 bg-white border border-gray-300 rounded-lg text-xs text-gray-900 text-center" value={filterMinUnitsPerBldg} onChange={(e) => setFilterMinUnitsPerBldg(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && applyFilters()} />
                <span className="text-gray-400 text-xs">—</span>
                <input type="number" placeholder="Max" className="w-14 px-1.5 py-1.5 bg-white border border-gray-300 rounded-lg text-xs text-gray-900 text-center" value={filterMaxUnitsPerBldg} onChange={(e) => setFilterMaxUnitsPerBldg(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && applyFilters()} />
              </div>
              <button onClick={() => setFilterHasWebsite(filterHasWebsite === true ? null : true)}
                className={`px-2 py-1.5 rounded text-xs font-medium transition-colors flex items-center gap-1 ${filterHasWebsite === true ? 'bg-emerald-50 text-emerald-700 border border-emerald-300' : 'bg-gray-100 text-gray-500 border border-gray-200 hover:text-gray-700'}`}>
                {filterHasWebsite === true && <span className="text-emerald-600">✓</span>}Website
              </button>
            </div>
            {/* Row 2: Building types */}
            <div className="flex flex-wrap gap-3 items-center">
              <div className="flex items-center gap-1.5">
                <span className="text-[10px] text-gray-400 uppercase font-bold">Type</span>
                {([
                  ['condo', 'Condo'],
                  ['coop', 'Co-op'],
                  ['rental_elevator', 'Elevator'],
                  ['rental_walkup', 'Walkup'],
                  ['small_residential', 'Small Res'],
                ] as [string, string][]).map(([val, label]) => (
                  <button key={val} onClick={() => toggleBuildingType(val)}
                    className={`px-2 py-1 rounded text-[10px] font-medium transition-colors ${
                      filterBuildingTypes.includes(val)
                        ? 'bg-purple-50 text-purple-700 border border-purple-300'
                        : 'bg-gray-100 text-gray-500 border border-gray-200 hover:text-gray-700'
                    }`}>
                    {label}
                  </button>
                ))}
              </div>
            </div>
            {/* Row 3: Pipeline stages (multi-select) */}
            <div className="flex flex-wrap gap-1.5 items-center">
              <span className="text-[10px] text-gray-400 uppercase font-bold mr-1">Stage</span>
              {([
                ['research', 'Research'],
                ['first_contact', 'First Contact'],
                ['follow_up', 'Follow-Up'],
                ['meeting_scheduled', 'Meeting Set'],
                ['meeting_done', 'Meeting Done'],
                ['loi', 'LOI'],
                ['due_diligence', 'Due Diligence'],
                ['closed', 'Closed'],
              ] as [string, string][]).map(([val, label]) => (
                <button key={val} onClick={() => togglePipelineStage(val)}
                  className={`px-2 py-1 rounded text-[10px] font-medium transition-colors ${
                    filterPipelineStages.includes(val)
                      ? 'bg-blue-50 text-blue-700 border border-blue-300'
                      : 'bg-gray-100 text-gray-500 border border-gray-200 hover:text-gray-700'
                  }`}>
                  {label}
                </button>
              ))}
            </div>
            {/* Row 4: Outreach status (multi-select) */}
            <div className="flex flex-wrap gap-1.5 items-center">
              <span className="text-[10px] text-gray-400 uppercase font-bold mr-1">Outreach</span>
              {([
                ['new', 'New'],
                ['contacted', 'Contacted'],
                ['interested', 'Interested'],
                ['not_interested', 'Not Interested'],
                ['closed', 'Closed'],
              ] as [string, string][]).map(([val, label]) => (
                <button key={val} onClick={() => toggleOutreachStatus(val)}
                  className={`px-2 py-1 rounded text-[10px] font-medium transition-colors ${
                    filterOutreachStatuses.includes(val)
                      ? 'bg-amber-50 text-amber-700 border border-amber-300'
                      : 'bg-gray-100 text-gray-500 border border-gray-200 hover:text-gray-700'
                  }`}>
                  {label}
                </button>
              ))}
            </div>
            {/* Row 5: Enrichment + Entity type (multi-select) */}
            <div className="flex flex-wrap gap-3 items-center">
              <div className="flex items-center gap-1.5">
                <span className="text-[10px] text-gray-400 uppercase font-bold">Enrichment</span>
                {([
                  ['complete', 'Enriched'],
                  ['partial', 'Partial'],
                  ['failed', 'No Data'],
                  ['none', 'Not Enriched'],
                ] as [string, string][]).map(([val, label]) => (
                  <button key={val} onClick={() => toggleEnrichmentStatus(val)}
                    className={`px-2 py-1 rounded text-[10px] font-medium transition-colors ${
                      filterEnrichmentStatuses.includes(val)
                        ? 'bg-teal-50 text-teal-700 border border-teal-300'
                        : 'bg-gray-100 text-gray-500 border border-gray-200 hover:text-gray-700'
                    }`}>
                    {label}
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-1.5">
                <span className="text-[10px] text-gray-400 uppercase font-bold">Entity</span>
                {([
                  ['company', 'Company'],
                  ['individual_agent', 'Individual'],
                  ['owner_operator', 'Owner-Op'],
                ] as [string, string][]).map(([val, label]) => (
                  <button key={val} onClick={() => toggleEntityType(val)}
                    className={`px-2 py-1 rounded text-[10px] font-medium transition-colors ${
                      filterEntityTypes.includes(val)
                        ? 'bg-indigo-50 text-indigo-700 border border-indigo-300'
                        : 'bg-gray-100 text-gray-500 border border-gray-200 hover:text-gray-700'
                    }`}>
                    {label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
        
        {/* Results count and bulk actions */}
        <div className="flex justify-between items-center mt-3 pt-3 border-t border-gray-200">
          <div className="text-sm text-gray-400">
            Showing <span className="text-gray-700 font-medium">{displayLeads.length}</span> of <span className="text-gray-700 font-medium">{totalLeads.toLocaleString()}</span> leads
            {selectedIds.size > 0 && (
              <span className="ml-3 text-blue-600">({selectedIds.size} selected)</span>
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
      {/* Address Search Results */}
      {searchMode === 'address' && addressResults.length > 0 && (
        <div className="bg-white shadow-sm border border-blue-200 rounded-xl p-4 mb-3">
          <h3 className="text-xs font-bold text-blue-600 uppercase tracking-wider mb-3">
            Building Address Results ({addressResults.length})
          </h3>
          <div className="space-y-2">
            {addressResults.map((b) => (
              <div key={b.building_id} className="flex items-center justify-between p-2 rounded-lg bg-blue-50/50 hover:bg-blue-50 transition-colors">
                <div>
                  <div className="text-sm font-medium text-gray-900">{b.address}</div>
                  <div className="text-xs text-gray-500">{b.boro} · {b.units_res} units · {b.building_type || 'unknown'}</div>
                </div>
                <div className="text-right">
                  {b.agent_name ? (
                    <button
                      onClick={() => {
                        setSearchMode('leads');
                        setSearchTerm(b.agent_name);
                        setAddressResults([]);
                        setTimeout(() => applyFilters(), 0);
                      }}
                      className="text-xs text-blue-600 hover:underline font-medium"
                    >
                      {b.agent_name} (score: {b.score?.toFixed(1)})
                    </button>
                  ) : (
                    <span className="text-xs text-gray-400">No managing lead</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {searchMode === 'address' && addressSearching && (
        <div className="bg-white shadow-sm border border-blue-200 rounded-xl p-4 mb-3 text-center text-sm text-gray-500">
          Searching buildings...
        </div>
      )}


      {/* Data Table — desktop table, mobile cards */}
      <div className="bg-white shadow-sm border border-gray-200 rounded-xl overflow-hidden">
        {/* Desktop Table */}
        <div className="hidden md:block overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="bg-gray-50 text-gray-500 text-xs uppercase tracking-wider border-b border-gray-200">
                <th className="px-4 py-3 w-8">
                  <input
                    type="checkbox"
                    checked={selectedIds.size === displayLeads.length && displayLeads.length > 0}
                    onChange={selectAll}
                    className="rounded bg-white border-gray-300"
                  />
                </th>
                <th className="px-4 py-3 cursor-pointer hover:text-gray-700 transition-colors" onClick={() => handleSort('agent_name')}>
                  Company <SortIcon field="agent_name" />
                </th>
                <th className="px-4 py-3 cursor-pointer hover:text-gray-700 transition-colors" onClick={() => handleSort('boro')}>
                  Borough <SortIcon field="boro" />
                </th>
                <th className="px-4 py-3 cursor-pointer hover:text-gray-700 transition-colors text-right" onClick={() => handleSort('portfolio_size')}>
                  Bldgs <SortIcon field="portfolio_size" />
                </th>
                <th className="px-4 py-3 cursor-pointer hover:text-gray-700 transition-colors text-right" onClick={() => handleSort('total_units')}>
                  Units <SortIcon field="total_units" />
                </th>
                <th className="px-4 py-3 cursor-pointer hover:text-gray-700 transition-colors text-right" onClick={() => handleSort('units_per_bldg')} title="Average residential units per building">
                  U/Bldg <SortIcon field="units_per_bldg" />
                </th>
                <th className="px-4 py-3 cursor-pointer hover:text-gray-700 transition-colors text-right" onClick={() => handleSort('score')}
                  title="Lead quality score (0–100) based on portfolio size, building types, registration status, and data completeness. Higher = stronger acquisition target.">
                  Score <SortIcon field="score" />
                </th>
                <th className="px-4 py-3 cursor-pointer hover:text-gray-700 transition-colors text-right" onClick={() => handleSort('estimated_annual_revenue')}
                  title="Estimated annual management fee revenue = Total Units × Average Rent (by borough & building type) × 5% management fee rate">
                  Mgmt Fee <SortIcon field="estimated_annual_revenue" />
                </th>
                <th className="px-4 py-3 cursor-pointer hover:text-gray-700 transition-colors text-right" onClick={() => handleSort('violations_per_unit')}
                  title="Open HPD violations per residential unit — higher = more maintenance issues, potential distressed asset opportunity">
                  Violations <SortIcon field="violations_per_unit" />
                </th>
                <th className="px-4 py-3">Contact</th>
                <th className="px-4 py-3 cursor-pointer hover:text-gray-700 transition-colors" onClick={() => handleSort('enrichment_status')}
                  title="How much contact data we have: green dots = phone/email found, website found, full research complete">
                  Info <SortIcon field="enrichment_status" />
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {displayLeads.map((lead) => {
                return (
                <tr 
                  key={lead.lead_id}
                  className="bg-white hover:bg-gray-50 transition-colors cursor-pointer border-b border-gray-100"
                  onClick={() => onSelectLead(lead)}
                >
                  <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={selectedIds.has(lead.lead_id)}
                      onChange={() => toggleSelect(lead.lead_id)}
                      className="rounded bg-white border-gray-300"
                    />
                  </td>
                  <td className="px-4 py-3">
                    <div className="font-medium text-gray-800 text-sm flex items-center gap-1.5">
                      {lead.company_name || lead.agent_name || lead.owner_name}
                      {lead.data_staleness === 'expired' && (
                        <span className="px-1 py-0.5 rounded text-[8px] font-bold bg-red-50 text-red-500" title="HPD registration not confirmed in last refresh">EXPIRED</span>
                      )}
                      {lead.data_staleness === 'partially_stale' && (
                        <span className="px-1 py-0.5 rounded text-[8px] font-bold bg-amber-50 text-amber-500" title="Some buildings have stale registrations">STALE</span>
                      )}
                    </div>
                    <div className="text-xs text-gray-400 truncate max-w-[250px] flex items-center gap-1.5">
                      <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold uppercase ${
                        lead.entity_type === 'company' ? 'bg-blue-50 text-blue-700' :
                        lead.entity_type === 'individual_agent' ? 'bg-amber-50 text-amber-700' :
                        lead.entity_type === 'owner_operator' ? 'bg-purple-50 text-purple-700' :
                        'bg-gray-100 text-gray-400'
                      }`} title={lead.entity_type === 'company' ? 'Registered management company or housing entity' : lead.entity_type === 'individual_agent' ? 'Individual person acting as managing agent for these buildings' : lead.entity_type === 'owner_operator' ? 'Property owner who self-manages without a separate management company' : 'Entity type could not be determined from HPD records'}>
                        {lead.entity_type === 'company' ? 'Company' : 
                         lead.entity_type === 'individual_agent' ? 'Individual' : 
                         lead.entity_type === 'owner_operator' ? 'Owner-Op' : 'Unknown'}
                      </span>
                      {lead.primary_contact && 
                       lead.primary_contact.toLowerCase() !== (lead.company_name || lead.agent_name || lead.owner_name || '').toLowerCase() && (
                        <span className="text-gray-400 truncate">{lead.primary_contact}</span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {(lead.boros?.length > 0 ? lead.boros : [lead.boro]).filter(Boolean).map((b, i) => (
                        <span key={i} className="px-1.5 py-0.5 bg-gray-100 text-gray-500 text-[10px] rounded capitalize">
                          {b ? b.charAt(0) + b.slice(1).toLowerCase() : ''}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <span className="text-gray-700 font-mono text-sm">{lead.portfolio_size}</span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <span className="text-blue-600 font-mono text-sm">{(lead.total_units || 0).toLocaleString()}</span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <span className="text-gray-500 font-mono text-sm">
                      {(lead.portfolio_size || 0) > 0 ? ((lead.total_units || 0) / lead.portfolio_size).toFixed(0) : '—'}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <span className={`font-mono text-sm font-bold ${scoreColor(lead.score)}`}
                      title={`Score ${(lead.score || 0).toFixed(1)}/100 — ${(lead.score || 0) >= 60 ? 'Strong target' : (lead.score || 0) >= 40 ? 'Moderate potential' : 'Lower priority'}`}>
                      {(lead.score || 0).toFixed(0)}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    {lead.estimated_annual_revenue > 0 ? (
                      <span className="font-mono text-sm text-emerald-600">
                        {formatCurrency(lead.estimated_annual_revenue)}
                      </span>
                    ) : (
                      <span className="text-gray-300">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right">
                    {(lead.violations_per_unit || 0) > 0 ? (
                      <div className="flex items-center justify-end gap-1.5" title={`${(lead.violation_count || 0).toLocaleString()} total open violations\nPer unit: ${(lead.violations_per_unit || 0).toFixed(2)}\nClass A (non-hazardous): ${lead.violation_class_a || 0}\nClass B (hazardous): ${lead.violation_class_b || 0}\nClass C (immediately hazardous): ${lead.violation_class_c || 0}`}>
                        <span className={`w-2 h-2 rounded-full ${
                          (lead.violations_per_unit || 0) > 1.0 ? 'bg-rose-500' :
                          (lead.violations_per_unit || 0) > 0.3 ? 'bg-amber-500' : 'bg-emerald-500'
                        }`} />
                        <span className={`font-mono text-sm ${
                          (lead.violations_per_unit || 0) > 1.0 ? 'text-rose-600' :
                          (lead.violations_per_unit || 0) > 0.3 ? 'text-amber-600' : 'text-gray-700'
                        }`}>{(lead.violations_per_unit || 0).toFixed(2)}</span>
                        <span className="text-[9px] text-gray-400">per unit</span>
                      </div>
                    ) : (lead.violation_count || 0) > 0 ? (
                      <span className="font-mono text-sm text-gray-500" title={`${lead.violation_count || 0} total violations (per-unit data not yet available)`}>{lead.violation_count}</span>
                    ) : (
                      <span className="text-gray-300" title="No open HPD violations on record, or data not yet loaded">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                    <div className="flex gap-1.5">
                      {lead.phone && (
                        <a href={`tel:${lead.phone}`} title={`Call: ${lead.phone}`} className="p-1.5 hover:bg-emerald-50 rounded transition-colors">
                          <svg className="w-4 h-4 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z"/>
                          </svg>
                        </a>
                      )}
                      {lead.email && (
                        <a href={`mailto:${lead.email}`} title={`Email: ${lead.email}`} className="p-1.5 hover:bg-blue-50 rounded transition-colors">
                          <svg className="w-4 h-4 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>
                          </svg>
                        </a>
                      )}
                      {lead.website && (
                        <a href={lead.website.startsWith('http') ? lead.website : `https://${lead.website}`} target="_blank" rel="noopener noreferrer" title={`Website: ${lead.website}`} className="p-1.5 hover:bg-purple-50 rounded transition-colors">
                          <svg className="w-4 h-4 text-purple-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9"/>
                          </svg>
                        </a>
                      )}
                      {!lead.phone && !lead.email && !lead.website && <span className="text-gray-300">—</span>}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-col gap-1">
                      <div className="flex items-center gap-1.5" title={`Enrichment: ${
                        lead.enrichment_status === 'complete' ? 'Complete — contacts, website, and summary found' :
                        lead.enrichment_status === 'partial' ? 'Partial — some data found' :
                        lead.enrichment_status === 'failed' ? 'Enriched but no data found' :
                        'Not yet enriched'
                      }\nPhone: ${lead.phone || 'None'}\nEmail: ${lead.email || 'None'}\nWebsite: ${lead.website || 'None'}${lead.business_summary ? '\nAI Summary: Yes' : ''}`}>
                        <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${
                          lead.enrichment_status === 'complete' ? 'bg-emerald-50 text-emerald-700' :
                          lead.enrichment_status === 'partial' ? 'bg-amber-50 text-amber-700' :
                          lead.enrichment_status === 'failed' ? 'bg-rose-50 text-rose-700' :
                          'bg-gray-100 text-gray-400'
                        }`}>
                          {lead.enrichment_status === 'complete' ? 'Enriched' :
                           lead.enrichment_status === 'partial' ? 'Partial' :
                           lead.enrichment_status === 'failed' ? 'No Data' :
                           'Not Enriched'}
                        </span>
                        {lead.business_summary && (
                          <span className="text-[9px] px-1 py-0.5 rounded bg-indigo-50 text-indigo-600" title="AI summary available">AI</span>
                        )}
                      </div>
                      {lead.pipeline_stage && lead.pipeline_stage !== 'research' && (
                        <span className={`px-2 py-0.5 text-[10px] rounded inline-block w-fit ${
                          lead.pipeline_stage === 'meeting_scheduled' || lead.pipeline_stage === 'meeting_done' ? 'bg-purple-50 text-purple-700' :
                          lead.pipeline_stage === 'loi' || lead.pipeline_stage === 'due_diligence' ? 'bg-emerald-50 text-emerald-700' :
                          lead.pipeline_stage === 'closed' ? 'bg-gray-100 text-gray-600' :
                          'bg-blue-50 text-blue-700'
                        }`}>
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
            <svg className="w-12 h-12 text-gray-300 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
            </svg>
            <p className="text-gray-500 text-sm font-medium">No leads match your filters</p>
            <p className="text-gray-400 text-xs mt-1">Try adjusting your search or filter criteria</p>
            <button onClick={clearFilters} className="mt-3 px-4 py-1.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-xs font-medium transition-colors">
              Clear All Filters
            </button>
          </div>
        )}

        {/* Mobile Card View */}
        <div className="md:hidden divide-y divide-gray-100">
          {displayLeads.map((lead) => (
            <div
              key={lead.lead_id}
              className="p-4 active:bg-gray-50 transition-colors cursor-pointer"
              onClick={() => onSelectLead(lead)}
            >
              <div className="flex justify-between items-start mb-2">
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-gray-800 text-sm truncate">{lead.company_name || lead.agent_name || lead.owner_name}</div>
                  <div className="flex items-center gap-1.5 mt-1">
                    <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold uppercase ${
                      lead.entity_type === 'company' ? 'bg-blue-50 text-blue-700' :
                      lead.entity_type === 'individual_agent' ? 'bg-amber-50 text-amber-700' :
                      lead.entity_type === 'owner_operator' ? 'bg-purple-50 text-purple-700' :
                      'bg-gray-100 text-gray-400'
                    }`}>
                      {lead.entity_type === 'company' ? 'Company' : 
                       lead.entity_type === 'individual_agent' ? 'Individual' : 
                       lead.entity_type === 'owner_operator' ? 'Owner-Op' : 'Unknown'}
                    </span>
                    {(lead.boros?.length > 0 ? lead.boros : [lead.boro]).filter(Boolean).slice(0, 2).map((b, i) => (
                      <span key={i} className="px-1 py-0.5 bg-gray-100 text-gray-500 text-[9px] rounded capitalize">
                        {b ? b.charAt(0) + b.slice(1).toLowerCase() : ''}
                      </span>
                    ))}
                  </div>
                </div>
                <span className={`font-mono text-lg font-bold ${scoreColor(lead.score)}`}>
                  {(lead.score || 0).toFixed(0)}
                </span>
              </div>
              <div className="flex items-center gap-4 text-xs">
                <span className="text-gray-500"><span className="font-mono text-gray-700">{lead.portfolio_size || 0}</span> bldgs</span>
                <span className="text-gray-500"><span className="font-mono text-blue-600">{(lead.total_units || 0).toLocaleString()}</span> units</span>
                {(lead.portfolio_size || 0) > 0 && (
                  <span className="text-gray-400"><span className="font-mono text-gray-500">{((lead.total_units || 0) / lead.portfolio_size).toFixed(0)}</span> u/b</span>
                )}
                {(lead.estimated_annual_revenue || 0) > 0 && (
                  <span className="font-mono text-emerald-600">{formatCurrency(lead.estimated_annual_revenue)}</span>
                )}
                {(lead.violations_per_unit || 0) > 0 && (
                  <span className={`font-mono ${(lead.violations_per_unit || 0) > 1.0 ? 'text-rose-600' : 'text-amber-600'}`}>
                    {(lead.violations_per_unit || 0).toFixed(2)} v/u
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2 mt-2" onClick={(e) => e.stopPropagation()}>
                {lead.phone && (
                  <a href={`tel:${lead.phone}`} className="p-2 bg-emerald-50 rounded-lg">
                    <svg className="w-4 h-4 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z"/>
                    </svg>
                  </a>
                )}
                {lead.email && (
                  <a href={`mailto:${lead.email}`} className="p-2 bg-blue-50 rounded-lg">
                    <svg className="w-4 h-4 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>
                    </svg>
                  </a>
                )}
                <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${
                  lead.enrichment_status === 'complete' ? 'bg-emerald-50 text-emerald-700' :
                  lead.enrichment_status === 'partial' ? 'bg-amber-50 text-amber-700' :
                  lead.enrichment_status === 'failed' ? 'bg-rose-50 text-rose-700' :
                  'bg-gray-100 text-gray-400'
                }`}>
                  {lead.enrichment_status === 'complete' ? 'Enriched' :
                   lead.enrichment_status === 'partial' ? 'Partial' :
                   lead.enrichment_status === 'failed' ? 'No Data' :
                   'Not Enriched'}
                </span>
                {lead.business_summary && (
                  <span className="text-[9px] px-1 py-0.5 rounded bg-indigo-50 text-indigo-600">AI</span>
                )}
                {lead.pipeline_stage && lead.pipeline_stage !== 'research' && (
                  <span className={`px-2 py-1 text-[10px] rounded ml-auto ${
                    lead.pipeline_stage === 'meeting_scheduled' || lead.pipeline_stage === 'meeting_done' ? 'bg-purple-50 text-purple-700' :
                    lead.pipeline_stage === 'loi' || lead.pipeline_stage === 'due_diligence' ? 'bg-emerald-50 text-emerald-700' :
                    'bg-blue-50 text-blue-700'
                  }`}>
                    {lead.pipeline_stage.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
        
        {/* Pagination */}
        <div className="flex items-center justify-between px-4 py-3 border-t border-gray-200 bg-gray-50">
          <button
            onClick={() => setPage(Math.max(0, page - 1))}
            disabled={page === 0}
            className="px-3 py-1 text-sm text-gray-500 hover:text-gray-900 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            ← Previous
          </button>
          <div className="flex items-center gap-4">
            <div className="text-sm text-gray-400">
              Page {page + 1} of {totalPages || 1}
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-400">Per page:</span>
              <select
                onChange={(e) => { const newSize = Number(e.target.value); setPageSize(newSize); setPage(0); setTimeout(() => loadLeadsRef.current?.(0), 0); }}
                onChange={(e) => { setPageSize(Number(e.target.value)); setPage(0); }}
                onChange={(e) => { setPageSize(Number(e.target.value)); setPage(0); }}
                className="px-2 py-1 bg-white border border-gray-200 rounded text-xs text-gray-700 focus:outline-none"
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
            className="px-3 py-1 text-sm text-gray-500 hover:text-gray-900 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            Next →
          </button>
        </div>
      </div>
    </div>
  );
};

export default LeadTable;
