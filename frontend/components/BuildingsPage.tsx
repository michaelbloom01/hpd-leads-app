/**
 * BuildingsPage — PM Operator persona workspace.
 * Find buildings with high churn probability for outreach.
 */
import React, { useState, useMemo, useCallback, useRef, useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  SortingState,
} from '@tanstack/react-table';
import {
  fetchBuildings,
  fetchBuildingStats,
  getBuildingLists,
  createBuildingList,
  addBuildingToList,
  type BuildingRow,
  type BuildingsQueryParams,
  type BuildingList,
} from '../services/buildings-api';
import { useDebounce } from '../hooks/useDebounce';

const columnHelper = createColumnHelper<BuildingRow>();

const churnBadge = (category: string | null) => {
  if (!category) return <span className="text-xs text-gray-400">--</span>;
  const cls = category === 'hot' ? 'bg-red-100 text-red-700' :
    category === 'warm' ? 'bg-amber-100 text-amber-700' : 'bg-green-100 text-green-700';
  return <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>{category}</span>;
};

const baseColumns = [
  columnHelper.accessor('bbl', {
    header: 'BBL (Borough-Block-Lot)',
    cell: info => (
      <span className="text-gray-600 font-mono text-xs" title="NYC's unique property identifier">
        {info.getValue() || '--'}
      </span>
    ),
  }),
  columnHelper.accessor('address', {
    header: 'Address',
    cell: info => <span className="font-medium text-gray-900">{info.getValue() || '--'}</span>,
  }),
  columnHelper.accessor('borough', { header: 'Borough' }),
  columnHelper.accessor('pm_company', {
    header: 'PM Company',
    cell: info => {
      const v = info.getValue();
      return v
        ? <span className="text-gray-700 text-xs truncate max-w-[180px] block">{v}</span>
        : <span className="text-gray-400 text-xs italic">Unknown</span>;
    },
  }),
  columnHelper.accessor('unit_count', {
    header: 'Units',
    cell: info => info.getValue()?.toLocaleString() ?? '--',
  }),
  columnHelper.accessor('churn_score', {
    header: 'Churn Score',
    cell: info => {
      const v = info.getValue();
      if (v === null || v === undefined) return '--';
      const color = v >= 35 ? 'text-red-600' : v >= 15 ? 'text-amber-600' : 'text-green-600';
      return <span className={`font-semibold ${color}`}>{v.toFixed(1)}</span>;
    },
  }),
  columnHelper.accessor('churn_category', {
    header: 'Category',
    cell: info => churnBadge(info.getValue()),
  }),
  columnHelper.accessor('key_signal', {
    header: 'Key Signal',
    cell: info => {
      const v = info.getValue();
      return v ? <span className="text-xs bg-gray-100 px-2 py-0.5 rounded">{v.replace(/_/g, ' ')}</span> : '--';
    },
  }),
  columnHelper.accessor('outreach_status', {
    header: () => (
      <span title="Pipeline status when building is in outreach (pipeline, contacted, meeting, won, lost). Empty when not in pipeline.">
        Outreach
      </span>
    ),
    cell: info => {
      const v = info.getValue();
      if (!v || v === 'none') return <span className="text-gray-400 text-xs">--</span>;
      return <span className="text-xs bg-blue-100 text-blue-700 px-2 py-0.5 rounded">{v}</span>;
    },
  }),
  columnHelper.accessor('estimated_annual_revenue', {
    header: 'Est. Revenue',
    cell: info => {
      const v = info.getValue();
      if (v == null || v === 0) return <span className="text-gray-400 text-xs">--</span>;
      return <span className="font-mono text-emerald-600 text-xs">${(v / 1000).toFixed(0)}k</span>;
    },
  }),
];

const BuildingsPage: React.FC = () => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [sorting, setSorting] = useState<SortingState>([{ id: 'churn_score', desc: true }]);
  const [searchInput, setSearchInput] = useState('');
  const debouncedSearch = useDebounce(searchInput, 300);
  const [filters, setFilters] = useState<BuildingsQueryParams>({});
  const [page, setPage] = useState(0);
  const pageSize = 50;
  const [addToListOpen, setAddToListOpen] = useState<string | null>(null);
  const [addToListBusy, setAddToListBusy] = useState<Record<string, boolean>>({});
  const [createListName, setCreateListName] = useState('');
  const [createListForBbl, setCreateListForBbl] = useState<string | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const { data: listsData } = useQuery({
    queryKey: ['building-lists'],
    queryFn: getBuildingLists,
    staleTime: 30000,
  });
  const buildingLists = listsData?.building_lists ?? [];

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setAddToListOpen(null);
        setCreateListForBbl(null);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleAddToExistingList = useCallback(async (listId: string, bbl: string) => {
    setAddToListBusy(prev => ({ ...prev, [bbl]: true }));
    try {
      await addBuildingToList(listId, bbl);
      setAddToListOpen(null);
      queryClient.invalidateQueries({ queryKey: ['building-lists'] });
    } catch (err) {
      console.error('Failed to add building to list:', err);
    } finally {
      setAddToListBusy(prev => ({ ...prev, [bbl]: false }));
    }
  }, [queryClient]);

  const handleCreateListAndAdd = useCallback(async (bbl: string) => {
    const name = createListName.trim() || 'New list';
    setAddToListBusy(prev => ({ ...prev, [bbl]: true }));
    try {
      const { id } = await createBuildingList(name);
      await addBuildingToList(id, bbl);
      setAddToListOpen(null);
      setCreateListForBbl(null);
      setCreateListName('');
      queryClient.invalidateQueries({ queryKey: ['building-lists'] });
    } catch (err) {
      console.error('Failed to create list and add building:', err);
    } finally {
      setAddToListBusy(prev => ({ ...prev, [bbl]: false }));
    }
  }, [createListName, queryClient]);

  const columns = useMemo(() => [
    ...baseColumns,
    columnHelper.display({
      id: 'actions',
      header: 'Actions',
      cell: ({ row }) => {
        const bbl = row.original.bbl;
        const isOpen = addToListOpen === bbl;
        const busy = addToListBusy[bbl];
        const showCreate = createListForBbl === bbl;
        return (
          <div ref={isOpen ? dropdownRef : undefined} className="relative">
            <button
              type="button"
              onClick={e => { e.stopPropagation(); setAddToListOpen(isOpen ? null : bbl); setCreateListForBbl(null); }}
              disabled={busy}
              className="text-xs px-2 py-1 rounded border border-gray-300 text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              {busy ? 'Adding...' : 'Add to List'}
            </button>
            {isOpen && (
              <div className="absolute right-0 top-full mt-1 z-20 bg-white border border-gray-200 rounded-lg shadow-lg py-1 min-w-[160px]">
                {showCreate ? (
                  <div className="px-3 py-2 space-y-2" onClick={e => e.stopPropagation()}>
                    <input
                      type="text"
                      value={createListName}
                      onChange={e => setCreateListName(e.target.value)}
                      placeholder="List name"
                      className="w-full px-2 py-1 text-sm border rounded"
                      autoFocus
                    />
                    <div className="flex gap-1">
                      <button
                        type="button"
                        onClick={() => handleCreateListAndAdd(bbl)}
                        className="text-xs px-2 py-1 bg-blue-600 text-white rounded hover:bg-blue-700"
                      >
                        Create & Add
                      </button>
                      <button
                        type="button"
                        onClick={() => { setCreateListForBbl(null); setCreateListName(''); }}
                        className="text-xs px-2 py-1 border rounded"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    {buildingLists.length === 0 ? (
                      <div className="px-3 py-2 text-xs text-gray-500">No lists yet</div>
                    ) : (
                      buildingLists.map((list: BuildingList) => (
                        <button
                          key={list.id}
                          type="button"
                          onClick={e => { e.stopPropagation(); handleAddToExistingList(list.id, bbl); }}
                          className="w-full text-left px-3 py-1.5 text-xs hover:bg-gray-50"
                        >
                          {list.name} {list.member_count != null ? `(${list.member_count})` : ''}
                        </button>
                      ))
                    )}
                    <button
                      type="button"
                      onClick={e => { e.stopPropagation(); setCreateListForBbl(bbl); }}
                      className="w-full text-left px-3 py-1.5 text-xs border-t border-gray-100 hover:bg-gray-50 text-blue-600"
                    >
                      + Create New List
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
        );
      },
    }),
  ], [addToListOpen, addToListBusy, createListForBbl, createListName, buildingLists, handleAddToExistingList, handleCreateListAndAdd]);

  const queryParams = useMemo(() => ({
    ...filters,
    search: debouncedSearch || undefined,
    sort_by: sorting[0]?.id || 'churn_score',
    sort_dir: sorting[0]?.desc ? 'desc' : 'asc',
    limit: pageSize,
    offset: page * pageSize,
  }), [filters, debouncedSearch, sorting, page]);

  const { data, isLoading, error } = useQuery({
    queryKey: ['buildings', queryParams],
    queryFn: () => fetchBuildings(queryParams),
    placeholderData: (prev) => prev,
  });

  const { data: stats } = useQuery({
    queryKey: ['building-stats'],
    queryFn: fetchBuildingStats,
    staleTime: 60000,
  });

  const table = useReactTable({
    data: data?.buildings ?? [],
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    manualSorting: true,
  });

  const totalPages = Math.ceil((data?.total ?? 0) / pageSize);

  const handleFilterChange = useCallback((key: keyof BuildingsQueryParams, value: string) => {
    setPage(0);
    setFilters(prev => ({ ...prev, [key]: value || undefined }));
  }, []);

  return (
    <div className="space-y-6">
      {/* Stats Bar */}
      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          {[
            { label: 'Total Buildings', value: stats.total.toLocaleString(), category: '' },
            { label: 'Hot', value: stats.hot.toLocaleString(), color: 'text-red-600', category: 'hot' },
            { label: 'Warm', value: stats.warm.toLocaleString(), color: 'text-amber-600', category: 'warm' },
            { label: 'Stable', value: stats.stable.toLocaleString(), color: 'text-green-600', category: 'stable' },
            { label: 'Avg Score', value: stats.avg_score?.toFixed(1) ?? '--', category: null },
            { label: 'Scored', value: stats.scored.toLocaleString(), category: null },
          ].map(s => (
            <button
              key={s.label}
              type="button"
              onClick={() => {
                if (s.category === null) return;
                handleFilterChange('churn_category', s.category);
              }}
              className={`bg-white rounded-lg border p-3 text-left transition-colors ${
                s.category === null
                  ? 'border-gray-200 cursor-default'
                  : (filters.churn_category || '') === s.category
                    ? 'border-blue-500 bg-blue-50'
                    : 'border-gray-200 hover:border-gray-300'
              }`}
            >
              <div className="text-xs text-gray-500">{s.label}</div>
              <div className={`text-lg font-bold ${s.color || 'text-gray-900'}`}>{s.value}</div>
            </button>
          ))}
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap gap-3 items-center">
        <input
          type="text"
          placeholder="Search address or BBL (Borough-Block-Lot)..."
          value={searchInput}
          onChange={e => setSearchInput(e.target.value)}
          className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent w-64"
        />
        <select
          onChange={e => handleFilterChange('borough', e.target.value)}
          className="px-3 py-2 border border-gray-300 rounded-lg text-sm"
        >
          <option value="">All Boroughs</option>
          <option value="MANHATTAN">Manhattan</option>
          <option value="BROOKLYN">Brooklyn</option>
          <option value="QUEENS">Queens</option>
          <option value="BRONX">Bronx</option>
          <option value="STATEN ISLAND">Staten Island</option>
        </select>
        <select
          onChange={e => handleFilterChange('churn_category', e.target.value)}
          className="px-3 py-2 border border-gray-300 rounded-lg text-sm"
        >
          <option value="">All Categories</option>
          <option value="hot">Hot</option>
          <option value="warm">Warm</option>
          <option value="stable">Stable</option>
        </select>
        <select
          onChange={e => handleFilterChange('outreach_status', e.target.value)}
          className="px-3 py-2 border border-gray-300 rounded-lg text-sm"
        >
          <option value="">All Outreach</option>
          <option value="in_pipeline">In Pipeline</option>
          <option value="pipeline">Pipeline</option>
          <option value="contacted">Contacted</option>
          <option value="meeting">Meeting</option>
          <option value="won">Won</option>
          <option value="lost">Lost</option>
        </select>
        <input
          type="number"
          placeholder="Min units"
          onChange={e => handleFilterChange('min_units', e.target.value)}
          className="px-3 py-2 border border-gray-300 rounded-lg text-sm w-28"
        />
      </div>

      {/* Table */}
      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        {error ? (
          <div className="p-8 text-center text-red-600">{(error as Error).message}</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                {table.getHeaderGroups().map(hg => (
                  <tr key={hg.id}>
                    {hg.headers.map(header => (
                      <th
                        key={header.id}
                        className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100"
                        onClick={header.column.id !== 'actions' ? header.column.getToggleSortingHandler() : undefined}
                      >
                        <div className="flex items-center gap-1">
                          {flexRender(header.column.columnDef.header, header.getContext())}
                          {header.column.getIsSorted() === 'asc' ? ' ↑' : header.column.getIsSorted() === 'desc' ? ' ↓' : ''}
                        </div>
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody className="divide-y divide-gray-100">
                {isLoading && !data ? (
                  <tr><td colSpan={columns.length} className="px-4 py-12 text-center text-gray-400">Loading...</td></tr>
                ) : table.getRowModel().rows.length === 0 ? (
                  <tr><td colSpan={columns.length} className="px-4 py-12 text-center text-gray-400">No buildings found</td></tr>
                ) : (
                  table.getRowModel().rows.map(row => (
                    <tr
                      key={row.id}
                      className="hover:bg-gray-50 cursor-pointer transition-colors"
                      onClick={() => {
                        const rawBbl = String(row.original.bbl || '').trim();
                        if (!rawBbl) return;
                        navigate(`/buildings/${encodeURIComponent(rawBbl)}`);
                      }}
                    >
                      {row.getVisibleCells().map(cell => (
                        <td key={cell.id} className="px-4 py-3 whitespace-nowrap">
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <span className="text-sm text-gray-500">
            Showing {page * pageSize + 1}-{Math.min((page + 1) * pageSize, data?.total ?? 0)} of {data?.total?.toLocaleString()} buildings
          </span>
          <div className="flex gap-2">
            <button
              onClick={() => setPage(p => Math.max(0, p - 1))}
              disabled={page === 0}
              className="px-3 py-1.5 text-sm border border-gray-300 rounded-lg disabled:opacity-50 hover:bg-gray-50"
            >
              Previous
            </button>
            <button
              onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1}
              className="px-3 py-1.5 text-sm border border-gray-300 rounded-lg disabled:opacity-50 hover:bg-gray-50"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

export default BuildingsPage;
