
import React, { useState, useEffect, useCallback, useRef } from 'react';
import { toast } from 'react-hot-toast';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { fetchLeads, ApiLead, startSelectedLeadsEnrichment, updateLeadPipelineStages, API_BASE_URL, checkHealth, searchBuildings, BuildingSearchResult, createSmartList } from '../services/api';
import { getAuthHeaders } from '../services/auth';
import { BOROUGHS, BOROUGH_SHORT, PIPELINE_STAGES, formatCurrency, scoreColor } from '../utils/format';
import { useLeadFilters } from '../hooks/useLeadFilters';
import { useFilterUrl } from '../hooks/useFilterUrl';
import LeadKanban from './leads/LeadKanban';
import { getLeadDisplayName, getLeadSecondaryName } from '../utils/leads';

export interface LeadFilterPreset {
  searchTerm?: string;
  boroughs?: string[];
  neighborhood?: string;
  minScore?: string;
  maxScore?: string;
  outreachStatuses?: string[];
  pipelineStages?: string[];
  enrichmentStatuses?: string[];
  entityTypes?: string[];
  minPortfolio?: string;
  maxPortfolio?: string;
  minUnits?: string;
  maxUnits?: string;
  minUnitsPerBldg?: string;
  maxUnitsPerBldg?: string;
  buildingTypes?: string[];
  hasPhone?: boolean;
  hasEmail?: boolean;
  hasWebsite?: boolean;
  searchMode?: 'leads' | 'address';
}

interface Props {
  onSelectLead: (lead: ApiLead) => void;
  filterPreset?: LeadFilterPreset | null;
  onFilterPresetConsumed?: () => void;
}

// R4: Cold-start polling interval (ms)
const HEALTH_POLL_INTERVAL = 5000;
const MAX_HEALTH_STARTING_RETRIES = 24; // ~2 minutes
const MAX_HEALTH_ERROR_RETRIES = 24; // ~2 minutes

type SortField = 'agent_name' | 'portfolio_size' | 'total_units' | 'units_per_bldg' | 'score' | 'boro' | 'enrichment_status' | 'estimated_annual_revenue' | 'violations_per_unit';
type SortDir = 'asc' | 'desc';
type ViewMode = 'table' | 'kanban';

const DEFAULT_SORT_FIELD: SortField = 'score';
const DEFAULT_SORT_DIR: SortDir = 'desc';
const DEFAULT_PAGE_SIZE = 50;
const DEFAULT_VIEW_MODE: ViewMode = 'table';
const VALID_PAGE_SIZES = new Set([25, 50, 100, 250, 500]);
const VALID_SORT_FIELDS = new Set<SortField>([
  'agent_name',
  'portfolio_size',
  'total_units',
  'units_per_bldg',
  'score',
  'boro',
  'enrichment_status',
  'estimated_annual_revenue',
  'violations_per_unit',
]);

function parseSortField(value: string | null): SortField {
  return value && VALID_SORT_FIELDS.has(value as SortField)
    ? (value as SortField)
    : DEFAULT_SORT_FIELD;
}

function parseSortDir(value: string | null): SortDir {
  return value === 'asc' ? 'asc' : DEFAULT_SORT_DIR;
}

function parsePageIndex(value: string | null): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 1 ? parsed - 1 : 0;
}

function parsePageSize(value: string | null): number {
  const parsed = Number(value);
  return VALID_PAGE_SIZES.has(parsed) ? parsed : DEFAULT_PAGE_SIZE;
}

function parseViewMode(value: string | null): ViewMode {
  return value === 'kanban' ? 'kanban' : DEFAULT_VIEW_MODE;
}

function setOptionalParam(params: URLSearchParams, key: string, value: string | null) {
  if (!value) {
    params.delete(key);
    return;
  }
  params.set(key, value);
}

const LeadTable: React.FC<Props> = ({ onSelectLead, filterPreset, onFilterPresetConsumed }) => {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { filters, setField, toggle, clearAll: clearFilterState, replaceAll, applyPreset, activeFiltersCount, toApiParams } = useLeadFilters();
  useFilterUrl(filters, replaceAll);

  const [leads, setLeads] = useState<ApiLead[]>([]);
  const [totalLeads, setTotalLeads] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [backendStarting, setBackendStarting] = useState(false);
  const [addressResults, setAddressResults] = useState<BuildingSearchResult[]>([]);
  const [addressSearching, setAddressSearching] = useState(false);

  // Sorting
  const [sortField, setSortField] = useState<SortField>(() => parseSortField(searchParams.get('sort')));
  const [sortDir, setSortDir] = useState<SortDir>(() => parseSortDir(searchParams.get('dir')));

  // Server-side pagination
  const [page, setPage] = useState(() => parsePageIndex(searchParams.get('page')));
  const [pageSize, setPageSize] = useState(() => parsePageSize(searchParams.get('limit')));

  // Selection for bulk actions
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [enriching, setEnriching] = useState(false);
  const [bulkPipelineStage, setBulkPipelineStage] = useState('');
  const [updatingPipelineStage, setUpdatingPipelineStage] = useState(false);
  const [showSaveListDialog, setShowSaveListDialog] = useState(false);
  const [smartListName, setSmartListName] = useState('');
  const [savingSmartList, setSavingSmartList] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>(() => parseViewMode(searchParams.get('view')));

  const isMounted = useRef(true);
  useEffect(() => {
    return () => { isMounted.current = false; };
  }, []);

  useEffect(() => {
    const nextSortField = parseSortField(searchParams.get('sort'));
    const nextSortDir = parseSortDir(searchParams.get('dir'));
    const nextPage = parsePageIndex(searchParams.get('page'));
    const nextPageSize = parsePageSize(searchParams.get('limit'));
    const nextViewMode = parseViewMode(searchParams.get('view'));

    if (sortField !== nextSortField) setSortField(nextSortField);
    if (sortDir !== nextSortDir) setSortDir(nextSortDir);
    if (page !== nextPage) setPage(nextPage);
    if (pageSize !== nextPageSize) setPageSize(nextPageSize);
    if (viewMode !== nextViewMode) setViewMode(nextViewMode);
  }, [page, pageSize, searchParams, sortDir, sortField, viewMode]);

  const hasWrittenListState = useRef(false);
  useEffect(() => {
    if (!hasWrittenListState.current) {
      hasWrittenListState.current = true;
      return;
    }

    const nextParams = new URLSearchParams(searchParams);
    setOptionalParam(nextParams, 'page', page > 0 ? String(page + 1) : null);
    setOptionalParam(nextParams, 'limit', pageSize !== DEFAULT_PAGE_SIZE ? String(pageSize) : null);
    setOptionalParam(nextParams, 'sort', sortField !== DEFAULT_SORT_FIELD ? sortField : null);
    setOptionalParam(nextParams, 'dir', sortDir !== DEFAULT_SORT_DIR ? sortDir : null);
    setOptionalParam(nextParams, 'view', viewMode !== DEFAULT_VIEW_MODE ? viewMode : null);

    const nextStr = nextParams.toString();
    const currentStr = searchParams.toString();
    if (nextStr !== currentStr) {
      setSearchParams(nextParams, { replace: true });
    }
  }, [page, pageSize, searchParams, setSearchParams, sortDir, sortField, viewMode]);

  // Fetch leads from server with current filters, sort, and search
  // This is called explicitly by the "Go" button, sort clicks, page changes, etc.
  // It reads directly from current filter state — no debouncing needed.
  const loadLeadsRef = React.useRef<(currentPage?: number) => Promise<void>>();
  
  const loadLeads = useCallback(async (currentPage: number = 0) => {
    setLoading(true);
    setError(null);
    try {
      const params = toApiParams(sortField, sortDir, pageSize, currentPage);
      const response = await fetchLeads(params);
      if (isMounted.current) {
        setLeads(response.leads);
        setTotalLeads(response.total);
      }
    } catch (err) {
      console.error('Failed to fetch leads:', err);
      if (isMounted.current) setError('Failed to load leads. Make sure the backend is running.');
    } finally {
      if (isMounted.current) setLoading(false);
    }
  }, [toApiParams, pageSize, sortField, sortDir]);

  // Keep ref in sync so callbacks can call latest version
  loadLeadsRef.current = loadLeads;

  // R4: Health check on initial mount — show cold-start banner if backend is booting
  useEffect(() => {
    let timerId: ReturnType<typeof setTimeout>;
    let startingRetries = 0;
    let errorRetries = 0;

    const finishWithHealthError = (message: string) => {
      setBackendStarting(false);
      setLoading(false);
      setError(message);
    };
    
    const pollHealth = async () => {
      const health = await checkHealth();
      if (!isMounted.current) return;
      if (health.status === 'starting' && startingRetries < MAX_HEALTH_STARTING_RETRIES) {
        setBackendStarting(true);
        setError(null);
        startingRetries++;
        errorRetries = 0;
        timerId = setTimeout(pollHealth, HEALTH_POLL_INTERVAL);
      } else if (health.status === 'error' && errorRetries < MAX_HEALTH_ERROR_RETRIES) {
        setBackendStarting(true);
        setError(null);
        errorRetries++;
        timerId = setTimeout(pollHealth, HEALTH_POLL_INTERVAL);
      } else if (health.status === 'starting') {
        finishWithHealthError(
          health.message || 'Backend is still starting up. Please try again in a moment.',
        );
      } else if (health.status === 'error') {
        finishWithHealthError(
          health.message || 'Backend is unavailable right now. Please try again in a moment.',
        );
      } else {
        setBackendStarting(false);
        setError(null);
        // Backend is ready — trigger first data load
        loadLeadsRef.current?.(page);
      }
    };
    
    pollHealth();
    return () => { clearTimeout(timerId); };
    // Only run once on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-reload on list-state changes after the initial health-check load.
  const hasMounted = React.useRef(false);
  const previousListState = React.useRef({ sortField, sortDir, page, pageSize });
  useEffect(() => {
    if (!hasMounted.current) {
      hasMounted.current = true;
      previousListState.current = { sortField, sortDir, page, pageSize };
      return; // Skip first run — handled by health check
    }
    if (backendStarting) return;

    const previous = previousListState.current;
    previousListState.current = { sortField, sortDir, page, pageSize };

    const sortChanged = previous.sortField !== sortField || previous.sortDir !== sortDir;
    const pageSizeChanged = previous.pageSize !== pageSize;
    const pageChanged = previous.page !== page;

    if (sortChanged) {
      if (page !== 0) {
        setPage(0);
      }
      loadLeadsRef.current?.(0);
      return;
    }

    if (pageSizeChanged) {
      loadLeadsRef.current?.(page);
      return;
    }

    if (pageChanged) {
      loadLeadsRef.current?.(page);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backendStarting, page, pageSize, sortDir, sortField]);

  // Apply filters within the currently selected workspace.
  const applyFilters = () => {
    const shouldLoadFirstPageDirectly = page === 0;
    clearSelected();
    setPage(0);
    setError(null);

    if (filters.searchMode === 'address') {
      const term = filters.searchTerm.trim();
      setLeads([]);
      setTotalLeads(0);
      if (!term) {
        setAddressResults([]);
        setAddressSearching(false);
        return;
      }
      setAddressSearching(true);
      searchBuildings(term)
        .then((res) => {
          if (!isMounted.current) return;
          setAddressResults(res.buildings || []);
        })
        .catch(() => {
          if (!isMounted.current) return;
          setAddressResults([]);
          setError('Failed to search buildings. Make sure the backend is running.');
        })
        .finally(() => {
          if (isMounted.current) setAddressSearching(false);
        });
      return;
    }

    if (addressResults.length > 0) {
      setAddressResults([]);
    }
    if (shouldLoadFirstPageDirectly) {
      loadLeads(0);
    }
  };
  useEffect(() => {
    if (!filterPreset) return;
    clearSelected();
    applyPreset({
      searchTerm: filterPreset.searchTerm || '',
      boroughs: filterPreset.boroughs || [],
      neighborhood: filterPreset.neighborhood || '',
      minScore: filterPreset.minScore || '',
      maxScore: filterPreset.maxScore || '',
      outreachStatuses: filterPreset.outreachStatuses || [],
      pipelineStages: filterPreset.pipelineStages || [],
      enrichmentStatuses: filterPreset.enrichmentStatuses || [],
      entityTypes: filterPreset.entityTypes || [],
      minPortfolio: filterPreset.minPortfolio || '',
      maxPortfolio: filterPreset.maxPortfolio || '',
      minUnits: filterPreset.minUnits || '',
      maxUnits: filterPreset.maxUnits || '',
      minUnitsPerBldg: filterPreset.minUnitsPerBldg || '',
      maxUnitsPerBldg: filterPreset.maxUnitsPerBldg || '',
      buildingTypes: filterPreset.buildingTypes || [],
      hasPhone: filterPreset.hasPhone ? true : null,
      hasEmail: filterPreset.hasEmail ? true : null,
      hasWebsite: filterPreset.hasWebsite ? true : null,
      searchMode: filterPreset.searchMode || 'leads',
    });
    onFilterPresetConsumed?.();
    if (page === 0) {
      setTimeout(() => { loadLeadsRef.current?.(0); }, 0);
    } else {
      setPage(0);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterPreset]);


  const totalPages = Math.ceil(totalLeads / pageSize);
  const showLeadWorkspace = filters.searchMode === 'leads';

  // Server handles filtering, sorting, and search — display leads directly
  const displayLeads = leads;
  const buildLeadHref = useCallback((leadId: string) => {
    const nextParams = new URLSearchParams(searchParams);
    nextParams.set('lead', leadId);
    const query = nextParams.toString();
    return query ? `/leads?${query}` : '/leads';
  }, [searchParams]);

  const handleSort = (field: SortField) => {
    clearSelected();
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

  const toggleSelectMany = (ids: string[]) => {
    if (ids.length === 0) return;
    const nextSelected = new Set(selectedIds);
    const everySelected = ids.every((id) => nextSelected.has(id));
    if (everySelected) {
      ids.forEach((id) => nextSelected.delete(id));
    } else {
      ids.forEach((id) => nextSelected.add(id));
    }
    setSelectedIds(nextSelected);
  };

  const clearSelected = () => {
    setSelectedIds(new Set());
  };

  const visibleLeadIds = displayLeads.map((lead) => lead.lead_id);
  const allVisibleSelected = visibleLeadIds.length > 0 && visibleLeadIds.every((id) => selectedIds.has(id));

  const selectAll = () => {
    toggleSelectMany(visibleLeadIds);
  };

  const toggleStageSelection = (stage: string) => {
    toggleSelectMany(
      displayLeads
        .filter((lead) => (lead.pipeline_stage || 'research') === stage)
        .map((lead) => lead.lead_id)
    );
  };

  const isStageFullySelected = (stage: string) => {
    const stageLeadIds = displayLeads
      .filter((lead) => (lead.pipeline_stage || 'research') === stage)
      .map((lead) => lead.lead_id);
    return stageLeadIds.length > 0 && stageLeadIds.every((id) => selectedIds.has(id));
  };

  const handleEnrichSelected = async () => {
    if (selectedIds.size === 0) return;
    setEnriching(true);
    try {
      const result = await startSelectedLeadsEnrichment(Array.from(selectedIds));
      if (result.missing_lead_ids.length > 0) {
        toast.success(`Queued enrichment for ${result.target_count} lead(s). ${result.missing_lead_ids.length} selection(s) were skipped.`);
      } else if (result.dispatch_mode === 'in_process') {
        toast.success(`Queued enrichment for ${result.target_count} lead(s) in local fallback mode.`);
      } else {
        toast.success(`Queued enrichment for ${result.target_count} selected lead(s).`);
      }
      setSelectedIds(new Set());
    } catch (err) {
      console.error('Enrichment failed:', err);
      toast.error('Failed to queue enrichment. Please try again.');
    } finally {
      setEnriching(false);
    }
  };

  const handleBulkPipelineStageUpdate = async () => {
    if (selectedIds.size === 0 || !bulkPipelineStage) return;
    setUpdatingPipelineStage(true);
    try {
      const result = await updateLeadPipelineStages(Array.from(selectedIds), bulkPipelineStage);
      const stageLabel = PIPELINE_STAGES.find((stage) => stage.value === bulkPipelineStage)?.label || bulkPipelineStage;
      if (result.missing_lead_ids.length > 0) {
        toast.success(`Moved ${result.updated_count} lead(s) to ${stageLabel}. ${result.missing_lead_ids.length} selection(s) were skipped.`);
      } else {
        toast.success(`Moved ${result.updated_count} lead(s) to ${stageLabel}.`);
      }
      loadLeadsRef.current?.(page);
      setSelectedIds(new Set());
      setBulkPipelineStage('');
    } catch (err) {
      console.error('Bulk pipeline update failed:', err);
      toast.error('Failed to update selected leads. Please try again.');
    } finally {
      setUpdatingPipelineStage(false);
    }
  };

  const clearFilters = async () => {
    clearSelected();
    clearFilterState();
    setAddressResults([]);
    if (page === 0) {
      setTimeout(() => loadLeadsRef.current?.(0), 0);
    } else {
      setPage(0);
    }
  };

  const exportToCsv = async () => {
    const apiParams = toApiParams(sortField, sortDir, totalLeads, 0);
    const params = new URLSearchParams();
    Object.entries(apiParams).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') params.set(k, String(v));
    });

    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/export/leads/csv?${params.toString()}`, {
        headers: getAuthHeaders(),
      });
      if (!response.ok) throw new Error('Export failed');
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `double_edge_leads_${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Server export failed, falling back to client export:', err);
      const headers = ['Company','Company Name','Entity Type','Primary Contact','Owner Name','Boroughs','Buildings','Units','Score','Est Annual Revenue','Violations/Unit','Violation Count','Phone','Email','Company Website','Enrichment Status','Outreach Status','Pipeline Stage','Priority','Next Follow-Up'];
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
      link.download = `double_edge_leads_${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    }
  };

  const buildSmartListFilters = useCallback(() => {
    const smartFilters: Record<string, unknown> = {};
    if (filters.boroughs.length > 0) smartFilters.boroughs = filters.boroughs;
    if (filters.neighborhood.trim()) smartFilters.neighborhood = filters.neighborhood.trim();
    if (filters.minScore) smartFilters.min_score = filters.minScore;
    if (filters.maxScore) smartFilters.max_score = filters.maxScore;
    if (filters.minPortfolio) smartFilters.min_portfolio = filters.minPortfolio;
    if (filters.maxPortfolio) smartFilters.max_portfolio = filters.maxPortfolio;
    if (filters.minUnits) smartFilters.min_units = filters.minUnits;
    if (filters.maxUnits) smartFilters.max_units = filters.maxUnits;
    if (filters.entityTypes.length > 0) smartFilters.entity_types = filters.entityTypes;
    if (filters.pipelineStages.length > 0) smartFilters.pipeline_stages = filters.pipelineStages;
    if (filters.outreachStatuses.length > 0) smartFilters.outreach_statuses = filters.outreachStatuses;
    if (filters.enrichmentStatuses.length > 0) smartFilters.enrichment_statuses = filters.enrichmentStatuses;
    if (filters.hasPhone) smartFilters.has_phone = true;
    if (filters.hasEmail) smartFilters.has_email = true;
      if (filters.hasWebsite) smartFilters.has_website = true;
      if (filters.minUnitsPerBldg) smartFilters.min_units_per_bldg = filters.minUnitsPerBldg;
      if (filters.maxUnitsPerBldg) smartFilters.max_units_per_bldg = filters.maxUnitsPerBldg;
      if (filters.buildingTypes.length > 0) smartFilters.building_types = filters.buildingTypes;
    if (filters.searchTerm.trim()) smartFilters.search = filters.searchTerm.trim();
    return smartFilters;
  }, [filters]);

  const smartListSummary = [
    filters.searchTerm.trim() ? `Search: ${filters.searchTerm.trim()}` : null,
    filters.boroughs.length > 0 ? `Boroughs: ${filters.boroughs.join(', ')}` : null,
    filters.neighborhood.trim() ? `Neighborhood: ${filters.neighborhood.trim()}` : null,
    filters.buildingTypes.length > 0 ? `Types: ${filters.buildingTypes.join(', ')}` : null,
    filters.pipelineStages.length > 0 ? `Stages: ${filters.pipelineStages.join(', ')}` : null,
    filters.outreachStatuses.length > 0 ? `Outreach: ${filters.outreachStatuses.join(', ')}` : null,
    filters.enrichmentStatuses.length > 0 ? `Enrichment: ${filters.enrichmentStatuses.join(', ')}` : null,
    filters.entityTypes.length > 0 ? `Entities: ${filters.entityTypes.join(', ')}` : null,
    filters.hasPhone ? 'Has phone' : null,
    filters.hasEmail ? 'Has email' : null,
    filters.hasWebsite ? 'Has website' : null,
  ].filter(Boolean) as string[];

  const saveAsSmartList = async () => {
    if (!smartListName.trim()) {
      toast.error('Please enter a Smart List name.');
      return;
    }
    setSavingSmartList(true);
    try {
      await createSmartList({ name: smartListName.trim(), filters: buildSmartListFilters() });
      toast.success(`Smart List "${smartListName.trim()}" saved`);
      setShowSaveListDialog(false);
      setSmartListName('');
    } catch (err) {
      toast.error('Failed to save Smart List');
    } finally {
      setSavingSmartList(false);
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
      <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-3 md:p-4" role="search" aria-label="Lead filters">
        {/* Primary Filters Row */}
        <div className="flex flex-wrap items-center gap-2 md:gap-3">
          <div className="flex-1 min-w-[160px] md:min-w-[280px] flex flex-col gap-1.5">
            <div className="flex rounded-lg border border-gray-300 overflow-hidden" role="group">
              <button
                onClick={() => {
                  if (filters.searchMode !== 'leads') {
                    clearSelected();
                    setField('searchMode', 'leads');
                    setAddressResults([]);
                  }
                }}
                className={`flex-1 px-3 py-1.5 text-xs font-medium transition-colors ${
                  filters.searchMode === 'leads'
                    ? 'bg-blue-600 text-white'
                    : 'bg-white text-gray-500 hover:bg-gray-50'
                }`}
              >
                PM Companies
              </button>
              <button
                onClick={() => {
                  if (filters.searchMode !== 'address') {
                    clearSelected();
                    setField('searchMode', 'address');
                    setAddressResults([]);
                  }
                }}
                className={`flex-1 px-3 py-1.5 text-xs font-medium transition-colors border-l border-gray-300 ${
                  filters.searchMode === 'address'
                    ? 'bg-blue-600 text-white'
                    : 'bg-white text-gray-500 hover:bg-gray-50'
                }`}
              >
                Address Lookup
              </button>
            </div>
            <input type="text" placeholder={filters.searchMode === 'leads' ? "Search by company name, owner, or agent..." : "Search by building address (e.g., 140 E 28th St)..."}
              className="w-full px-3 py-2 bg-white border border-gray-300 rounded-lg text-sm text-gray-900 focus:outline-none focus:ring-1 focus:ring-blue-500"
              value={filters.searchTerm} onChange={(e) => setField('searchTerm', e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  applyFilters();
                }
              }} />
          </div>
          {/* Borough multi-select toggles — horizontally scrollable on mobile */}
          <div className="flex gap-1 overflow-x-auto flex-shrink-0 scrollbar-hide">
            {BOROUGHS.map(b => (
              <button key={b} onClick={() => toggle('boroughs', b)}
                className={`px-3 py-2 sm:px-2 sm:py-1.5 rounded text-xs font-medium transition-colors flex-shrink-0 ${
                  filters.boroughs.includes(b)
                    ? 'bg-blue-50 text-blue-700 border border-blue-300'
                    : 'bg-gray-100 text-gray-500 border border-gray-200 hover:text-gray-700'
                }`}>
                {BOROUGH_SHORT[b] || b}
              </button>
            ))}
          </div>
          <div className="flex flex-col gap-1.5 min-w-[180px]">
            <input
              type="text"
              placeholder="Neighborhood / NTA"
              className="px-3 py-2 sm:px-2 sm:py-1.5 bg-white border border-gray-300 rounded text-xs text-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-500"
              value={filters.neighborhood}
              onChange={(e) => setField('neighborhood', e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  applyFilters();
                }
              }}
            />
            <span className="text-[10px] text-gray-400 px-1">Search NTA or community board, e.g. `Midtown` or `MN05`</span>
          </div>
          <div className="flex gap-1.5">
            <button onClick={() => setField('hasPhone', filters.hasPhone === true ? null : true)}
              className={`px-3 py-2 sm:px-2 sm:py-1.5 rounded text-xs font-medium transition-colors flex items-center gap-1 ${filters.hasPhone === true ? 'bg-emerald-50 text-emerald-700 border border-emerald-300' : 'bg-gray-100 text-gray-500 border border-gray-200 hover:text-gray-700'}`}>
              {filters.hasPhone === true && <span className="text-emerald-600">✓</span>}Phone
            </button>
            <button onClick={() => setField('hasEmail', filters.hasEmail === true ? null : true)}
              className={`px-3 py-2 sm:px-2 sm:py-1.5 rounded text-xs font-medium transition-colors flex items-center gap-1 ${filters.hasEmail === true ? 'bg-emerald-50 text-emerald-700 border border-emerald-300' : 'bg-gray-100 text-gray-500 border border-gray-200 hover:text-gray-700'}`}>
              {filters.hasEmail === true && <span className="text-emerald-600">✓</span>}Email
            </button>
          </div>
          <button onClick={() => setField('showMoreFilters', !filters.showMoreFilters)} className="px-3 py-2 sm:py-2 text-xs text-gray-500 hover:text-gray-700 transition-colors relative flex-shrink-0">
            {filters.showMoreFilters ? 'Less Filters' : 'More Filters'}
            {activeFiltersCount > 0 && !filters.showMoreFilters && (
              <span className="absolute -top-1 -right-1 w-4 h-4 bg-blue-500 text-white text-[9px] rounded-full flex items-center justify-center font-bold">{activeFiltersCount}</span>
            )}
          </button>
          <button onClick={applyFilters} className="hidden sm:inline-flex px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold rounded-lg transition-colors">
            Go
          </button>
          <button onClick={clearFilters} className="hidden sm:inline-flex px-3 py-2 text-xs text-gray-500 hover:text-gray-700 transition-colors">Clear</button>
          <button onClick={exportToCsv} disabled={displayLeads.length === 0}
            className="hidden sm:inline-flex px-3 py-2 bg-white border border-gray-200 rounded-lg text-xs text-gray-700 hover:bg-gray-50 hover:text-gray-900 disabled:opacity-40 transition-colors items-center gap-1.5">
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
            Export
          </button>
          {activeFiltersCount > 0 && (
            <button
              onClick={() => {
                setSmartListName(smartListName || '');
                setShowSaveListDialog(true);
              }}
              className="hidden sm:inline-flex px-3 py-2 bg-white border border-blue-200 rounded-lg text-xs text-blue-700 hover:bg-blue-50 transition-colors items-center gap-1.5"
              title="Save current filters as a Smart List for tracking changes over time">
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" /></svg>
              Save List
            </button>
          )}
        </div>
        <div className="mt-3 grid grid-cols-3 gap-2 sm:hidden">
          <button
            onClick={applyFilters}
            className="px-3 py-2.5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold rounded-lg transition-colors"
          >
            Apply
          </button>
          <button
            onClick={clearFilters}
            className="px-3 py-2.5 bg-gray-100 text-gray-700 text-xs font-semibold rounded-lg border border-gray-200"
          >
            Clear
          </button>
          <button
            onClick={exportToCsv}
            disabled={displayLeads.length === 0}
            className="px-3 py-2.5 bg-white border border-gray-200 rounded-lg text-xs text-gray-700 disabled:opacity-40"
          >
            Export
          </button>
        </div>
        {/* More Filters (collapsed) */}
        {filters.showMoreFilters && (
          <div className="mt-3 pt-3 border-t border-gray-200 space-y-3">
            <div className="grid grid-cols-2 sm:flex sm:flex-wrap gap-2 sm:gap-3 items-center">
              <div className="flex items-center gap-1" title="Filter by lead quality score (0-100)">
                <span className="text-[10px] text-gray-400 uppercase font-bold w-12">Score</span>
                <input type="number" placeholder="Min" className="w-14 px-1.5 py-1.5 bg-white border border-gray-300 rounded-lg text-xs text-gray-900 text-center" value={filters.minScore} onChange={(e) => setField('minScore', e.target.value)} onKeyDown={(e) => e.key === 'Enter' && applyFilters()} />
                <span className="text-gray-400 text-xs">—</span>
                <input type="number" placeholder="Max" className="w-14 px-1.5 py-1.5 bg-white border border-gray-300 rounded-lg text-xs text-gray-900 text-center" value={filters.maxScore} onChange={(e) => setField('maxScore', e.target.value)} onKeyDown={(e) => e.key === 'Enter' && applyFilters()} />
              </div>
              <div className="flex items-center gap-1" title="Filter by total residential units">
                <span className="text-[10px] text-gray-400 uppercase font-bold w-12">Units</span>
                <input type="number" placeholder="Min" className="w-16 px-1.5 py-1.5 bg-white border border-gray-300 rounded-lg text-xs text-gray-900 text-center" value={filters.minUnits} onChange={(e) => setField('minUnits', e.target.value)} onKeyDown={(e) => e.key === 'Enter' && applyFilters()} />
                <span className="text-gray-400 text-xs">—</span>
                <input type="number" placeholder="Max" className="w-16 px-1.5 py-1.5 bg-white border border-gray-300 rounded-lg text-xs text-gray-900 text-center" value={filters.maxUnits} onChange={(e) => setField('maxUnits', e.target.value)} onKeyDown={(e) => e.key === 'Enter' && applyFilters()} />
              </div>
              <div className="flex items-center gap-1" title="Filter by number of buildings in portfolio">
                <span className="text-[10px] text-gray-400 uppercase font-bold w-12">Bldgs</span>
                <input type="number" placeholder="Min" className="w-14 px-1.5 py-1.5 bg-white border border-gray-300 rounded-lg text-xs text-gray-900 text-center" value={filters.minPortfolio} onChange={(e) => setField('minPortfolio', e.target.value)} onKeyDown={(e) => e.key === 'Enter' && applyFilters()} />
                <span className="text-gray-400 text-xs">—</span>
                <input type="number" placeholder="Max" className="w-14 px-1.5 py-1.5 bg-white border border-gray-300 rounded-lg text-xs text-gray-900 text-center" value={filters.maxPortfolio} onChange={(e) => setField('maxPortfolio', e.target.value)} onKeyDown={(e) => e.key === 'Enter' && applyFilters()} />
              </div>
              <div className="flex items-center gap-1" title="Filter by average units per building">
                <span className="text-[10px] text-gray-400 uppercase font-bold w-12">U/Bldg</span>
                <input type="number" placeholder="Min" className="w-14 px-1.5 py-1.5 bg-white border border-gray-300 rounded-lg text-xs text-gray-900 text-center" value={filters.minUnitsPerBldg} onChange={(e) => setField('minUnitsPerBldg', e.target.value)} onKeyDown={(e) => e.key === 'Enter' && applyFilters()} />
                <span className="text-gray-400 text-xs">—</span>
                <input type="number" placeholder="Max" className="w-14 px-1.5 py-1.5 bg-white border border-gray-300 rounded-lg text-xs text-gray-900 text-center" value={filters.maxUnitsPerBldg} onChange={(e) => setField('maxUnitsPerBldg', e.target.value)} onKeyDown={(e) => e.key === 'Enter' && applyFilters()} />
              </div>
              <button onClick={() => setField('hasWebsite', filters.hasWebsite === true ? null : true)}
                title="Filter leads where a company website was found via enrichment"
                className={`px-2 py-1.5 rounded text-xs font-medium transition-colors flex items-center gap-1 ${filters.hasWebsite === true ? 'bg-emerald-50 text-emerald-700 border border-emerald-300' : 'bg-gray-100 text-gray-500 border border-gray-200 hover:text-gray-700'}`}>
                {filters.hasWebsite === true && <span className="text-emerald-600">✓</span>}Has Website
              </button>
            </div>
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
                  <button key={val} onClick={() => toggle('buildingTypes', val)}
                    className={`px-2 py-1 rounded text-[10px] font-medium transition-colors ${
                      filters.buildingTypes.includes(val)
                        ? 'bg-purple-50 text-purple-700 border border-purple-300'
                        : 'bg-gray-100 text-gray-500 border border-gray-200 hover:text-gray-700'
                    }`}>
                    {label}
                  </button>
                ))}
              </div>
            </div>
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
                <button key={val} onClick={() => toggle('pipelineStages', val)}
                  className={`px-2 py-1 rounded text-[10px] font-medium transition-colors ${
                    filters.pipelineStages.includes(val)
                      ? 'bg-blue-50 text-blue-700 border border-blue-300'
                      : 'bg-gray-100 text-gray-500 border border-gray-200 hover:text-gray-700'
                  }`}>
                  {label}
                </button>
              ))}
            </div>
            <div className="flex flex-wrap gap-1.5 items-center">
              <span className="text-[10px] text-gray-400 uppercase font-bold mr-1">Outreach</span>
              {([
                ['new', 'New'],
                ['contacted', 'Contacted'],
                ['interested', 'Interested'],
                ['not_interested', 'Not Interested'],
                ['closed', 'Closed'],
              ] as [string, string][]).map(([val, label]) => (
                <button key={val} onClick={() => toggle('outreachStatuses', val)}
                  className={`px-2 py-1 rounded text-[10px] font-medium transition-colors ${
                    filters.outreachStatuses.includes(val)
                      ? 'bg-amber-50 text-amber-700 border border-amber-300'
                      : 'bg-gray-100 text-gray-500 border border-gray-200 hover:text-gray-700'
                  }`}>
                  {label}
                </button>
              ))}
            </div>
            <div className="flex flex-wrap gap-3 items-center">
              <div className="flex items-center gap-1.5">
                <span className="text-[10px] text-gray-400 uppercase font-bold">Enrichment</span>
                {([
                  ['complete', 'Enriched'],
                  ['partial', 'Partial'],
                  ['failed', 'No Data'],
                  ['none', 'Not Enriched'],
                ] as [string, string][]).map(([val, label]) => (
                  <button key={val} onClick={() => toggle('enrichmentStatuses', val)}
                    className={`px-2 py-1 rounded text-[10px] font-medium transition-colors ${
                      filters.enrichmentStatuses.includes(val)
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
                  <button key={val} onClick={() => toggle('entityTypes', val)}
                    className={`px-2 py-1 rounded text-[10px] font-medium transition-colors ${
                      filters.entityTypes.includes(val)
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
            {showLeadWorkspace ? (
              <>Showing <span className="text-gray-700 font-medium">{displayLeads.length}</span> of <span className="text-gray-700 font-medium">{totalLeads.toLocaleString()}</span> leads</>
            ) : (
              <>Address lookup returns buildings first, then linked PM leads when available.</>
            )}
            {selectedIds.size > 0 && (
              <span className="ml-3 text-blue-600">({selectedIds.size} selected)</span>
            )}
          </div>
          {showLeadWorkspace && (
            <div className="flex items-center gap-2">
            <button
              onClick={() => setViewMode('table')}
              className={`px-2 py-1 text-[11px] rounded border transition-colors ${
                viewMode === 'table'
                  ? 'bg-blue-50 border-blue-300 text-blue-700'
                  : 'bg-white border-gray-200 text-gray-500 hover:text-gray-700'
              }`}
            >
              Table
            </button>
            <button
              onClick={() => setViewMode('kanban')}
              className={`px-2 py-1 text-[11px] rounded border transition-colors ${
                viewMode === 'kanban'
                  ? 'bg-blue-50 border-blue-300 text-blue-700'
                  : 'bg-white border-gray-200 text-gray-500 hover:text-gray-700'
              }`}
            >
              Kanban
            </button>
            </div>
          )}
          {showLeadWorkspace && selectedIds.size > 0 && (
            <div className="flex items-center gap-2">
              <button
                onClick={clearSelected}
                className="px-3 py-1.5 bg-white border border-gray-200 text-xs font-medium rounded-lg text-gray-600 hover:text-gray-800 hover:border-gray-300 transition-colors"
              >
                Clear Selection
              </button>
              <select
                value={bulkPipelineStage}
                onChange={(e) => setBulkPipelineStage(e.target.value)}
                className="px-2 py-1.5 bg-white border border-gray-200 rounded text-xs text-gray-700 focus:outline-none"
                aria-label="Move selected leads to pipeline stage"
              >
                <option value="">Move selected to...</option>
                {PIPELINE_STAGES.map((stage) => (
                  <option key={stage.value} value={stage.value}>
                    {stage.label}
                  </option>
                ))}
              </select>
              <button
                onClick={handleBulkPipelineStageUpdate}
                disabled={updatingPipelineStage || !bulkPipelineStage}
                className="px-4 py-1.5 bg-emerald-600 text-white text-xs font-medium rounded-lg hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {updatingPipelineStage ? 'Moving...' : 'Move Selected'}
              </button>
              <button
                onClick={handleEnrichSelected}
                disabled={enriching}
                className="px-4 py-1.5 bg-blue-600 text-white text-xs font-medium rounded-lg hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {enriching ? 'Enriching...' : `Enrich ${selectedIds.size} Selected`}
              </button>
            </div>
          )}
        </div>
      </div>
      {filters.searchMode === 'address' && addressResults.length > 0 && (
        <div className="bg-white shadow-sm border border-blue-200 rounded-xl p-4 mb-3">
          <h3 className="text-xs font-bold text-blue-600 uppercase tracking-wider mb-3">
            Building Address Results ({addressResults.length})
          </h3>
          <div className="space-y-2">
            {addressResults.map((b, idx) => (
              <div key={b.building_id || `${b.address}-${idx}`} className="flex items-center justify-between p-2 rounded-lg bg-blue-50/50 hover:bg-blue-50 transition-colors">
                <div>
                  <div className="text-sm font-medium text-gray-900">{b.address}</div>
                  <div className="text-xs text-gray-500">
                    {b.boro} · {b.units_res} units · {b.pm_company || b.building_type || 'building record'}
                  </div>
                </div>
                <div className="text-right">
                  <button
                    onClick={() => navigate(`/buildings/${encodeURIComponent(String(b.bbl || b.building_id))}`)}
                    className="block text-xs text-gray-500 hover:text-gray-700 hover:underline mb-1"
                  >
                    Open building
                  </button>
                  {b.lead_id ? (
                    <button
                      onClick={() => {
                        clearSelected();
                        setField('searchMode', 'leads');
                        setField('searchTerm', b.lead_name || b.agent_name || b.pm_company || '');
                        setAddressResults([]);
                        setTimeout(() => applyFilters(), 0);
                      }}
                      className="text-xs text-blue-600 hover:underline font-medium"
                    >
                      {b.lead_name || b.agent_name} {b.score != null ? `(score: ${b.score.toFixed(1)})` : ''}
                    </button>
                  ) : b.pm_company ? (
                    <div className="text-xs text-gray-600">
                      <div className="font-medium">{b.pm_company}</div>
                      <div className="text-gray-400">Company found, but no linked lead yet</div>
                    </div>
                  ) : (
                    <span className="text-xs text-gray-400">No managing lead</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {filters.searchMode === 'address' && addressSearching && (
        <div className="bg-white shadow-sm border border-blue-200 rounded-xl p-4 mb-3 text-center text-sm text-gray-500">
          Searching buildings...
        </div>
      )}

      {filters.searchMode === 'address' && !addressSearching && filters.searchTerm.trim() && addressResults.length === 0 && !error && (
        <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-6 text-center">
          <p className="text-sm font-medium text-gray-700">No buildings matched that address.</p>
          <p className="text-xs text-gray-400 mt-1">Try a shorter street name or a nearby address variant.</p>
        </div>
      )}

      {filters.searchMode === 'address' && !filters.searchTerm.trim() && (
        <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-6 text-center">
          <p className="text-sm font-medium text-gray-700">Search a building address to investigate a property first.</p>
          <p className="text-xs text-gray-400 mt-1">Use `Leads` mode for outreach entities and `Address Lookup` for a building-first investigation.</p>
        </div>
      )}

      {/* Data Table — desktop table, mobile cards */}
      {showLeadWorkspace && (
      <div className="bg-white shadow-sm border border-gray-200 rounded-xl overflow-hidden">
        {viewMode === 'kanban' ? (
          <LeadKanban
            leads={displayLeads}
            onSelectLead={onSelectLead}
            getLeadHref={buildLeadHref}
            selectedLeadIds={selectedIds}
            onToggleSelect={toggleSelect}
            onToggleVisibleSelect={selectAll}
            areAllVisibleSelected={allVisibleSelected}
            onToggleStageSelect={toggleStageSelection}
            isStageFullySelected={isStageFullySelected}
            onClearSelection={clearSelected}
          />
        ) : (
          <>
        {/* Desktop Table */}
        <div className="hidden md:block overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="bg-gray-50 text-gray-500 text-xs uppercase tracking-wider border-b border-gray-200">
                <th className="px-4 py-3 w-8">
                  <input
                    type="checkbox"
                    checked={allVisibleSelected}
                    onChange={selectAll}
                    className="rounded bg-white border-gray-300"
                  />
                </th>
                <th className="px-4 py-3 cursor-pointer hover:text-gray-700 transition-colors" onClick={() => handleSort('agent_name')} aria-sort={sortField === 'agent_name' ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}>
                  Company <SortIcon field="agent_name" />
                </th>
                <th className="px-4 py-3 cursor-pointer hover:text-gray-700 transition-colors" onClick={() => handleSort('boro')} aria-sort={sortField === 'boro' ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}>
                  Borough <SortIcon field="boro" />
                </th>
                <th className="px-4 py-3 cursor-pointer hover:text-gray-700 transition-colors text-right" onClick={() => handleSort('portfolio_size')} aria-sort={sortField === 'portfolio_size' ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}>
                  Bldgs <SortIcon field="portfolio_size" />
                </th>
                <th className="px-4 py-3 cursor-pointer hover:text-gray-700 transition-colors text-right" onClick={() => handleSort('total_units')} aria-sort={sortField === 'total_units' ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}>
                  Units <SortIcon field="total_units" />
                </th>
                <th className="px-4 py-3 cursor-pointer hover:text-gray-700 transition-colors text-right" onClick={() => handleSort('units_per_bldg')} title="Average residential units per building" aria-sort={sortField === 'units_per_bldg' ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}>
                  U/Bldg <SortIcon field="units_per_bldg" />
                </th>
                <th className="px-4 py-3 cursor-pointer hover:text-gray-700 transition-colors text-right" onClick={() => handleSort('score')}
                  title="Lead quality score (0–100) based on portfolio size, building types, registration status, and data completeness. Higher = stronger acquisition target." aria-sort={sortField === 'score' ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}>
                  Score <SortIcon field="score" />
                </th>
                <th className="px-4 py-3 cursor-pointer hover:text-gray-700 transition-colors text-right" onClick={() => handleSort('estimated_annual_revenue')}
                  title="Estimated annual management fee revenue = Total Units × Average Rent (by borough & building type) × 5% management fee rate" aria-sort={sortField === 'estimated_annual_revenue' ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}>
                  Mgmt Fee <SortIcon field="estimated_annual_revenue" />
                </th>
                <th className="px-4 py-3 cursor-pointer hover:text-gray-700 transition-colors text-right" onClick={() => handleSort('violations_per_unit')}
                  title="Open housing violations per residential unit — higher = more maintenance issues, potential distressed asset opportunity" aria-sort={sortField === 'violations_per_unit' ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}>
                  Violations <SortIcon field="violations_per_unit" />
                </th>
                <th className="px-4 py-3">Contact</th>
                <th className="px-4 py-3 cursor-pointer hover:text-gray-700 transition-colors" onClick={() => handleSort('enrichment_status')}
                  title="Enrichment status: Enriched = phone/email + website + AI summary found; Partial = some data found; No Data = enrichment attempted, nothing found; Not Enriched = not yet run" aria-sort={sortField === 'enrichment_status' ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}>
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
                      <Link
                        to={buildLeadHref(lead.lead_id)}
                        onClick={(e) => e.stopPropagation()}
                        className="truncate hover:text-blue-700 hover:underline"
                      >
                        {getLeadDisplayName(lead)}
                      </Link>
                      {lead.data_staleness === 'expired' && (
                        <span className="px-1 py-0.5 rounded text-[8px] font-bold bg-red-50 text-red-500" title="Registration not confirmed in last refresh">EXPIRED</span>
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
                      }`} title={lead.entity_type === 'company' ? 'Registered management company or housing entity' : lead.entity_type === 'individual_agent' ? 'Individual person acting as managing agent for these buildings' : lead.entity_type === 'owner_operator' ? 'Property owner who self-manages without a separate management company' : 'Entity type could not be determined from city records'}>
                        {lead.entity_type === 'company' ? 'Company' : 
                         lead.entity_type === 'individual_agent' ? 'Individual' : 
                         lead.entity_type === 'owner_operator' ? 'Owner-Op' : 'Unknown'}
                      </span>
                      {getLeadSecondaryName(lead) && (
                        <span className="text-gray-400 truncate">{getLeadSecondaryName(lead)}</span>
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
                      <span className="text-gray-300" title="No open housing violations on record, or data not yet loaded">—</span>
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
                        <a href={lead.website.startsWith('http') ? lead.website : `https://${lead.website}`} target="_blank" rel="noopener noreferrer" title={`Company website: ${lead.website}`} className="p-1.5 hover:bg-purple-50 rounded transition-colors">
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
                      <div className="flex items-center gap-1.5" title={`Enrichment status: ${
                        lead.enrichment_status === 'complete' ? 'Complete — phone, email, company website, and AI summary all found' :
                        lead.enrichment_status === 'partial' ? 'Partial — some contact info found, click lead to see details' :
                        lead.enrichment_status === 'failed' ? 'Attempted — no public contact data could be found' :
                        'Not yet enriched — open lead and click "Enrich Lead" to search for contact info'
                      }\n\nPhone: ${lead.phone || 'Not found'}\nEmail: ${lead.email || 'Not found'}\nCompany Website: ${lead.website || 'Not found'}${lead.business_summary ? '\nAI Summary: Available' : ''}`}>
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
                <div className="flex items-start gap-2 flex-1 min-w-0">
                  <input
                    type="checkbox"
                    checked={selectedIds.has(lead.lead_id)}
                    onChange={() => toggleSelect(lead.lead_id)}
                    onClick={(e) => e.stopPropagation()}
                    className="rounded bg-white border-gray-300 mt-0.5 flex-shrink-0"
                  />
                  <div className="flex-1 min-w-0">
                    <Link
                      to={buildLeadHref(lead.lead_id)}
                      onClick={(e) => e.stopPropagation()}
                      className="font-medium text-gray-800 text-sm truncate block hover:text-blue-700 hover:underline"
                    >
                      {getLeadDisplayName(lead)}
                    </Link>
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
                </div>
                <span className={`font-mono text-lg font-bold ${scoreColor(lead.score)}`}>
                  {(lead.score || 0).toFixed(0)}
                </span>
              </div>
              <div className="flex flex-wrap items-center gap-2 text-xs ml-0 mt-2">
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
              <div className="flex flex-wrap items-center gap-2 mt-2 ml-0" onClick={(e) => e.stopPropagation()}>
                {lead.phone && (
                  <a href={`tel:${lead.phone}`} className="p-2.5 bg-emerald-50 rounded-lg">
                    <svg className="w-4 h-4 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z"/>
                    </svg>
                  </a>
                )}
                {lead.email && (
                  <a href={`mailto:${lead.email}`} className="p-2.5 bg-blue-50 rounded-lg">
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
        
        {/* Pagination — stacks on mobile */}
        <div className="flex flex-col sm:flex-row items-center justify-between gap-2 px-4 py-3 border-t border-gray-200 bg-gray-50">
          <div className="flex items-center justify-between w-full sm:w-auto gap-2">
            <button
              onClick={() => {
                clearSelected();
                setPage(Math.max(0, page - 1));
              }}
              disabled={page === 0}
              className="px-3 py-2 sm:py-1 text-sm text-gray-500 hover:text-gray-900 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              ← Prev
            </button>
            <div className="text-sm text-gray-400">
              Page {page + 1} of {totalPages || 1}
            </div>
            <button
              onClick={() => {
                clearSelected();
                setPage(Math.min(totalPages - 1, page + 1));
              }}
              disabled={page >= totalPages - 1}
              className="px-3 py-2 sm:py-1 text-sm text-gray-500 hover:text-gray-900 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              Next →
            </button>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-400">Per page:</span>
            <select
              value={pageSize}
              onChange={(e) => {
                clearSelected();
                const newSize = Number(e.target.value);
                setPageSize(newSize);
                setPage(0);
              }}
              className="px-2 py-1.5 bg-white border border-gray-200 rounded text-xs text-gray-700 focus:outline-none"
            >
              <option value={25}>25</option>
              <option value={50}>50</option>
              <option value={100}>100</option>
              <option value={250}>250</option>
              <option value={500}>500</option>
            </select>
          </div>
        </div>
          </>
        )}
      </div>
      )}
      {showSaveListDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4" onClick={() => { if (!savingSmartList) setShowSaveListDialog(false); }}>
          <div className="w-full max-w-lg rounded-2xl border border-gray-200 bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="space-y-1">
              <h3 className="text-lg font-bold text-gray-900">Save Smart List</h3>
              <p className="text-sm text-gray-500">Save the current lead filters as a reusable segment.</p>
            </div>
            <div className="mt-4 space-y-3">
              <input
                type="text"
                value={smartListName}
                onChange={(e) => setSmartListName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !savingSmartList) {
                    saveAsSmartList();
                  }
                }}
                placeholder="List name"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-1 focus:ring-blue-500"
                autoFocus
              />
              <div className="rounded-xl border border-gray-200 bg-gray-50 p-3">
                <div className="text-[11px] font-bold uppercase tracking-wider text-gray-500">Filter summary</div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {smartListSummary.length > 0 ? smartListSummary.map((item) => (
                    <span key={item} className="rounded-full bg-white px-2 py-1 text-[11px] text-gray-700 border border-gray-200">
                      {item}
                    </span>
                  )) : (
                    <span className="text-xs text-gray-500">No filters selected yet. This will save the current broad Leads view.</span>
                  )}
                </div>
                <div className="mt-3 text-xs text-gray-500">
                  Current result set: <span className="font-medium text-gray-700">{totalLeads.toLocaleString()}</span> leads
                </div>
              </div>
            </div>
            <div className="mt-5 flex items-center justify-end gap-2">
              <button
                onClick={() => setShowSaveListDialog(false)}
                disabled={savingSmartList}
                className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={saveAsSmartList}
                disabled={savingSmartList || !smartListName.trim()}
                className="px-4 py-2 rounded-lg bg-blue-600 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-50"
              >
                {savingSmartList ? 'Saving...' : 'Save Smart List'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default LeadTable;
