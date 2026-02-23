/**
 * BuildingsPage — PM Operator persona workspace.
 * Find buildings with high churn probability for outreach.
 */
import React, { useState, useMemo, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  SortingState,
} from '@tanstack/react-table';
import { fetchBuildings, fetchBuildingStats, type BuildingRow, type BuildingsQueryParams } from '../services/buildings-api';
import { useDebounce } from '../hooks/useDebounce';

const columnHelper = createColumnHelper<BuildingRow>();

const churnBadge = (category: string | null) => {
  if (!category) return <span className="text-xs text-gray-400">--</span>;
  const cls = category === 'hot' ? 'bg-red-100 text-red-700' :
    category === 'warm' ? 'bg-amber-100 text-amber-700' : 'bg-green-100 text-green-700';
  return <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>{category}</span>;
};

const columns = [
  columnHelper.accessor('address', {
    header: 'Address',
    cell: info => <span className="font-medium text-gray-900">{info.getValue() || '--'}</span>,
  }),
  columnHelper.accessor('borough', { header: 'Borough' }),
  columnHelper.accessor('unit_count', {
    header: 'Units',
    cell: info => info.getValue()?.toLocaleString() ?? '--',
  }),
  columnHelper.accessor('churn_score', {
    header: 'Churn Score',
    cell: info => {
      const v = info.getValue();
      if (v === null || v === undefined) return '--';
      const color = v >= 70 ? 'text-red-600' : v >= 40 ? 'text-amber-600' : 'text-green-600';
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
    header: 'Status',
    cell: info => {
      const v = info.getValue();
      if (!v || v === 'none') return <span className="text-gray-400 text-xs">--</span>;
      return <span className="text-xs bg-blue-100 text-blue-700 px-2 py-0.5 rounded">{v}</span>;
    },
  }),
];

const BuildingsPage: React.FC = () => {
  const navigate = useNavigate();
  const [sorting, setSorting] = useState<SortingState>([{ id: 'churn_score', desc: true }]);
  const [searchInput, setSearchInput] = useState('');
  const debouncedSearch = useDebounce(searchInput, 300);
  const [filters, setFilters] = useState<BuildingsQueryParams>({});
  const [page, setPage] = useState(0);
  const pageSize = 50;

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
            { label: 'Total Buildings', value: stats.total.toLocaleString() },
            { label: 'Hot', value: stats.hot.toLocaleString(), color: 'text-red-600' },
            { label: 'Warm', value: stats.warm.toLocaleString(), color: 'text-amber-600' },
            { label: 'Stable', value: stats.stable.toLocaleString(), color: 'text-green-600' },
            { label: 'Avg Score', value: stats.avg_score?.toFixed(1) ?? '--' },
            { label: 'Scored', value: stats.scored.toLocaleString() },
          ].map(s => (
            <div key={s.label} className="bg-white rounded-lg border border-gray-200 p-3">
              <div className="text-xs text-gray-500">{s.label}</div>
              <div className={`text-lg font-bold ${s.color || 'text-gray-900'}`}>{s.value}</div>
            </div>
          ))}
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap gap-3 items-center">
        <input
          type="text"
          placeholder="Search address or BBL..."
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
                        onClick={header.column.getToggleSortingHandler()}
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
                      onClick={() => navigate(`/buildings/${row.original.bbl}`)}
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
