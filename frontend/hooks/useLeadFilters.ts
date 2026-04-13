/**
 * useLeadFilters — consolidates 20+ filter-related useState calls into
 * a single useReducer.  The reducer mirrors the old individual states
 * so existing code only needs to swap `setX(v)` → `dispatch({ type: 'SET_X', payload: v })`.
 *
 * Also exports helper toggle functions and the activeFiltersCount.
 */
import { useReducer, useCallback, useMemo } from 'react';

export interface FilterState {
  searchTerm: string;
  boroughs: string[];
  neighborhood: string;
  minScore: string;
  maxScore: string;
  minPortfolio: string;
  maxPortfolio: string;
  hasPhone: boolean | null;
  hasEmail: boolean | null;
  hasWebsite: boolean | null;
  entityTypes: string[];
  outreachStatuses: string[];
  pipelineStages: string[];
  enrichmentStatuses: string[];
  enrichmentStatus: string;
  minUnits: string;
  maxUnits: string;
  minUnitsPerBldg: string;
  maxUnitsPerBldg: string;
  buildingTypes: string[];
  showMoreFilters: boolean;
  // Address search
  searchMode: 'leads' | 'address';
}

const INITIAL_STATE: FilterState = {
  searchTerm: '',
  boroughs: [],
  neighborhood: '',
  minScore: '',
  maxScore: '',
  minPortfolio: '',
  maxPortfolio: '',
  hasPhone: null,
  hasEmail: null,
  hasWebsite: null,
  entityTypes: [],
  outreachStatuses: [],
  pipelineStages: [],
  enrichmentStatuses: [],
  enrichmentStatus: '',
  minUnits: '',
  maxUnits: '',
  minUnitsPerBldg: '',
  maxUnitsPerBldg: '',
  buildingTypes: [],
  showMoreFilters: false,
  searchMode: 'leads',
};

type Action =
  | { type: 'SET_FIELD'; field: keyof FilterState; value: FilterState[keyof FilterState] }
  | { type: 'TOGGLE_ARRAY'; field: 'boroughs' | 'entityTypes' | 'outreachStatuses' | 'pipelineStages' | 'enrichmentStatuses' | 'buildingTypes'; value: string }
  | { type: 'CLEAR_ALL' }
  | { type: 'REPLACE_ALL'; next: FilterState }
  | { type: 'APPLY_PRESET'; preset: Partial<FilterState> };

function filterReducer(state: FilterState, action: Action): FilterState {
  switch (action.type) {
    case 'SET_FIELD':
      return { ...state, [action.field]: action.value };
    case 'TOGGLE_ARRAY': {
      const arr = state[action.field] as string[];
      const next = arr.includes(action.value)
        ? arr.filter((v) => v !== action.value)
        : [...arr, action.value];
      return { ...state, [action.field]: next };
    }
    case 'CLEAR_ALL':
      return { ...INITIAL_STATE };
    case 'REPLACE_ALL':
      return { ...action.next };
    case 'APPLY_PRESET':
      return { ...state, ...action.preset, showMoreFilters: true };
    default:
      return state;
  }
}

export function useLeadFilters() {
  const [filters, dispatch] = useReducer(filterReducer, INITIAL_STATE);

  const setField = useCallback(
    <K extends keyof FilterState>(field: K, value: FilterState[K]) =>
      dispatch({ type: 'SET_FIELD', field, value }),
    [],
  );

  const toggle = useCallback(
    (field: 'boroughs' | 'entityTypes' | 'outreachStatuses' | 'pipelineStages' | 'enrichmentStatuses' | 'buildingTypes', value: string) =>
      dispatch({ type: 'TOGGLE_ARRAY', field, value }),
    [],
  );

  const clearAll = useCallback(() => dispatch({ type: 'CLEAR_ALL' }), []);
  const replaceAll = useCallback((next: FilterState) => dispatch({ type: 'REPLACE_ALL', next }), []);
  const applyPreset = useCallback(
    (preset: Partial<FilterState>) => dispatch({ type: 'APPLY_PRESET', preset }),
    [],
  );

  const activeFiltersCount = useMemo(() => {
    return [
      filters.entityTypes.length > 0,
      filters.neighborhood !== '',
      filters.outreachStatuses.length > 0,
      filters.enrichmentStatuses.length > 0,
      filters.pipelineStages.length > 0,
      filters.minScore !== '',
      filters.maxScore !== '',
      filters.minUnits !== '',
      filters.maxUnits !== '',
      filters.minPortfolio !== '',
      filters.maxPortfolio !== '',
      filters.minUnitsPerBldg !== '',
      filters.maxUnitsPerBldg !== '',
      filters.hasWebsite === true,
      filters.buildingTypes.length > 0,
    ].filter(Boolean).length;
  }, [filters]);

  /** Build the query params object for the API call. */
  const toApiParams = useCallback(
    (sortField: string, sortDir: string, pageSize: number, currentPage: number) => {
      const params: Record<string, string | number | boolean | undefined> = {
        limit: pageSize,
        offset: currentPage * pageSize,
        sort_by: sortField === 'agent_name' ? 'company_name' : sortField,
        sort_dir: sortDir,
      };
      const num = (v: string) => { const n = parseFloat(v); return isNaN(n) || n <= 0 ? undefined : n; };
      if (num(filters.minScore) !== undefined) params.min_score = num(filters.minScore);
      if (num(filters.maxScore) !== undefined) params.max_score = num(filters.maxScore);
      if (num(filters.minPortfolio) !== undefined) params.min_portfolio = num(filters.minPortfolio);
      if (num(filters.maxPortfolio) !== undefined) params.max_portfolio = num(filters.maxPortfolio);
      if (filters.boroughs.length > 0) params.boro = filters.boroughs.join(',');
      if (filters.neighborhood.trim()) params.neighborhood = filters.neighborhood.trim();
      if (filters.hasPhone !== null) params.has_phone = filters.hasPhone;
      if (filters.hasEmail !== null) params.has_email = filters.hasEmail;
      if (filters.entityTypes.length > 0) params.entity_type = filters.entityTypes.join(',');
      if (filters.outreachStatuses.length > 0) params.outreach_status = filters.outreachStatuses.join(',');
      if (filters.pipelineStages.length > 0) params.pipeline_stage = filters.pipelineStages.join(',');
      if (filters.enrichmentStatuses.length > 0) params.enrichment_status = filters.enrichmentStatuses.join(',');
      if (filters.searchTerm.trim()) params.search = filters.searchTerm.trim();
      if (num(filters.minUnits) !== undefined) params.min_units = num(filters.minUnits);
      if (num(filters.maxUnits) !== undefined) params.max_units = num(filters.maxUnits);
      if (num(filters.minUnitsPerBldg) !== undefined) params.min_units_per_bldg = num(filters.minUnitsPerBldg);
      if (num(filters.maxUnitsPerBldg) !== undefined) params.max_units_per_bldg = num(filters.maxUnitsPerBldg);
      if (filters.buildingTypes.length > 0) params.building_type_has = filters.buildingTypes.join(',');
      return params;
    },
    [filters],
  );

  return { filters, setField, toggle, clearAll, replaceAll, applyPreset, activeFiltersCount, toApiParams };
}

export type { Action as FilterAction };
export { INITIAL_STATE as FILTER_INITIAL_STATE };
