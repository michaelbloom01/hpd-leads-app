/**
 * useFilterUrl — syncs FilterState to/from URL search params via useSearchParams.
 * 
 * On mount, reads URL params and dispatches them into the filter reducer.
 * On filter change, serializes non-default values back to the URL.
 * This enables bookmarkable/shareable filtered views.
 */
import { useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { FilterState } from './useLeadFilters';

const FILTER_PARAM_MAP: Record<string, keyof FilterState> = {
  q: 'searchTerm',
  boro: 'boroughs',
  neighborhood: 'neighborhood',
  min_score: 'minScore',
  max_score: 'maxScore',
  min_portfolio: 'minPortfolio',
  max_portfolio: 'maxPortfolio',
  min_units: 'minUnits',
  max_units: 'maxUnits',
  min_upb: 'minUnitsPerBldg',
  max_upb: 'maxUnitsPerBldg',
  entity: 'entityTypes',
  outreach: 'outreachStatuses',
  stage: 'pipelineStages',
  enrichment: 'enrichmentStatuses',
  type: 'buildingTypes',
  phone: 'hasPhone',
  email: 'hasEmail',
  website: 'hasWebsite',
  mode: 'searchMode',
};

const ARRAY_FIELDS = new Set<keyof FilterState>([
  'boroughs', 'entityTypes', 'outreachStatuses', 'pipelineStages',
  'enrichmentStatuses', 'buildingTypes',
]);

const BOOL_FIELDS = new Set<keyof FilterState>(['hasPhone', 'hasEmail', 'hasWebsite']);
const FILTER_PARAM_KEYS = Object.keys(FILTER_PARAM_MAP);

function filtersToParams(filters: FilterState): URLSearchParams {
  const params = new URLSearchParams();
  for (const [param, field] of Object.entries(FILTER_PARAM_MAP)) {
    const val = filters[field];
    if (ARRAY_FIELDS.has(field)) {
      const arr = val as string[];
      if (arr.length > 0) params.set(param, arr.join(','));
    } else if (BOOL_FIELDS.has(field)) {
      if (val === true) params.set(param, '1');
    } else if (field === 'searchMode') {
      if (val !== 'leads') params.set(param, val as string);
    } else {
      if (val && val !== '') params.set(param, String(val));
    }
  }
  return params;
}

function paramsToPartial(params: URLSearchParams): Partial<FilterState> {
  const partial: Partial<FilterState> = {};
  for (const [param, field] of Object.entries(FILTER_PARAM_MAP)) {
    const raw = params.get(param);
    if (raw === null) continue;

    if (ARRAY_FIELDS.has(field)) {
      (partial as Record<string, unknown>)[field] = raw.split(',').filter(Boolean);
    } else if (BOOL_FIELDS.has(field)) {
      (partial as Record<string, unknown>)[field] = raw === '1' ? true : null;
    } else if (field === 'searchMode') {
      (partial as Record<string, unknown>)[field] = raw === 'address' ? 'address' : 'leads';
    } else {
      (partial as Record<string, unknown>)[field] = raw;
    }
  }
  return partial;
}

export function useFilterUrl(
  filters: FilterState,
  applyPreset: (preset: Partial<FilterState>) => void,
) {
  const [searchParams, setSearchParams] = useSearchParams();
  const isInitialized = useRef(false);

  // On mount: read URL params into filter state
  useEffect(() => {
    if (isInitialized.current) return;
    isInitialized.current = true;
    const partial = paramsToPartial(searchParams);
    if (Object.keys(partial).length > 0) {
      applyPreset(partial);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // On filter change: write to URL (skip first render)
  const prevFilters = useRef(filters);
  useEffect(() => {
    if (!isInitialized.current) return;
    if (prevFilters.current === filters) return;
    prevFilters.current = filters;

    const newParams = new URLSearchParams(searchParams);
    FILTER_PARAM_KEYS.forEach((param) => newParams.delete(param));
    const filterParams = filtersToParams(filters);
    filterParams.forEach((value, key) => {
      newParams.set(key, value);
    });
    const newStr = newParams.toString();
    const currentStr = searchParams.toString();
    if (newStr !== currentStr) {
      setSearchParams(newParams, { replace: true });
    }
  }, [filters, searchParams, setSearchParams]);
}
