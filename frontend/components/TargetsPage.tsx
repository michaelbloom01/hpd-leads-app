import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import {
  createTargetList,
  discoverAdjacentTargets,
  getTargetList,
  getTargetLists,
  importTargetItems,
  rescoreTargetList,
  type TargetDiscovery,
  type TargetImportRow,
} from '../services/targets-api';

const HEADER_MAP: Record<string, keyof TargetImportRow> = {
  company: 'company_name',
  'company name': 'company_name',
  est: 'established',
  established: 'established',
  'portfolio size': 'portfolio_estimate',
  portfolio: 'portfolio_estimate',
  'units est': 'units_estimate',
  'units(est.)': 'units_estimate',
  units: 'units_estimate',
  geography: 'geography',
  ownership: 'ownership',
  'key principal(s)': 'key_principals',
  'key principals': 'key_principals',
  'condo/co-op focus': 'condo_focus',
  website: 'website',
  phone: 'phone',
  address: 'address',
  tier: 'tier',
  'acquisition fit notes': 'acquisition_fit_notes',
  'key risk / flag': 'risk_flag',
  notes: 'notes',
};

function normalizeHeader(header: string): string {
  return header.trim().toLowerCase().replace(/^#+\s*/, '');
}

export function parseTargetPaste(input: string): TargetImportRow[] {
  const trimmed = input.trim();
  if (!trimmed) return [];
  const lines = trimmed.split(/\r?\n/).filter(Boolean);
  if (lines.length < 2) return [];
  const delimiter = lines[0].includes('\t') ? '\t' : ',';
  const headers = lines[0].split(delimiter).map(normalizeHeader);
  return lines.slice(1).map((line) => {
    const parts = line.split(delimiter);
    const row: TargetImportRow = { company_name: '' };
    headers.forEach((header, idx) => {
      const mapped = HEADER_MAP[header];
      if (!mapped) return;
      const value = parts[idx]?.trim();
      if (value) row[mapped] = value;
    });
    if (!row.company_name) {
      row.company_name = parts[0]?.trim() || '';
    }
    return row;
  }).filter((row) => row.company_name);
}

const badgeClass = (status?: string | null) => {
  switch ((status || '').toLowerCase()) {
    case 'matched':
      return 'bg-emerald-50 text-emerald-700 border-emerald-200';
    case 'ambiguous':
      return 'bg-amber-50 text-amber-700 border-amber-200';
    case 'unmatched':
      return 'bg-rose-50 text-rose-700 border-rose-200';
    default:
      return 'bg-gray-50 text-gray-700 border-gray-200';
  }
};

const TargetsPage: React.FC = () => {
  const queryClient = useQueryClient();
  const [selectedListId, setSelectedListId] = useState<string | null>(null);
  const [newListName, setNewListName] = useState('Prime PM Targets');
  const [newListDescription, setNewListDescription] = useState('Curated acquisition target list');
  const [pasteText, setPasteText] = useState('');
  const [discoveries, setDiscoveries] = useState<TargetDiscovery[]>([]);

  const { data: listsData, isLoading: loadingLists } = useQuery({
    queryKey: ['target-lists'],
    queryFn: getTargetLists,
  });

  useEffect(() => {
    if (!selectedListId && listsData?.target_lists?.length) {
      setSelectedListId(listsData.target_lists[0].target_list_id);
    }
  }, [listsData, selectedListId]);

  const { data: selectedList, isLoading: loadingList } = useQuery({
    queryKey: ['target-list', selectedListId],
    queryFn: () => getTargetList(selectedListId!),
    enabled: !!selectedListId,
  });

  const createListMutation = useMutation({
    mutationFn: createTargetList,
    onSuccess: async (result) => {
      toast.success('Target list created');
      await queryClient.invalidateQueries({ queryKey: ['target-lists'] });
      setSelectedListId(result.target_list_id);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const importMutation = useMutation({
    mutationFn: (rows: TargetImportRow[]) => importTargetItems(selectedListId!, rows),
    onSuccess: async (result) => {
      toast.success(`Imported ${result.imported_count} target${result.imported_count === 1 ? '' : 's'}`);
      setPasteText('');
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['target-lists'] }),
        queryClient.invalidateQueries({ queryKey: ['target-list', selectedListId] }),
      ]);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const rescoreMutation = useMutation({
    mutationFn: () => rescoreTargetList(selectedListId!),
    onSuccess: async () => {
      toast.success('Target list rescored');
      await queryClient.invalidateQueries({ queryKey: ['target-list', selectedListId] });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const discoveryMutation = useMutation({
    mutationFn: () => discoverAdjacentTargets(selectedListId!, 15),
    onSuccess: (result) => setDiscoveries(result.discoveries),
    onError: (error: Error) => toast.error(error.message),
  });

  const parsedRows = useMemo(() => parseTargetPaste(pasteText), [pasteText]);

  return (
    <div className="h-full overflow-auto bg-gray-50">
      <div className="max-w-7xl mx-auto p-6 space-y-6">
        <div className="bg-white border border-gray-200 rounded-2xl p-6 shadow-sm">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <h1 className="text-2xl font-semibold text-gray-900">Targets</h1>
              <p className="text-sm text-gray-500 mt-1">
                Intake your curated PM firms, auto-match them to internal leads, score them against the acquisition thesis,
                and work the resulting dossier/outreach queue.
              </p>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3 w-full lg:w-auto">
              <input
                value={newListName}
                onChange={(e) => setNewListName(e.target.value)}
                className="px-3 py-2 rounded-lg border border-gray-300 text-sm"
                placeholder="List name"
              />
              <input
                value={newListDescription}
                onChange={(e) => setNewListDescription(e.target.value)}
                className="px-3 py-2 rounded-lg border border-gray-300 text-sm"
                placeholder="Description"
              />
              <button
                onClick={() => createListMutation.mutate({ name: newListName, description: newListDescription })}
                className="px-4 py-2 rounded-lg bg-emerald-600 text-white text-sm font-medium hover:bg-emerald-700"
              >
                {createListMutation.isPending ? 'Creating...' : 'Create List'}
              </button>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-[280px_minmax(0,1fr)] gap-6">
          <div className="bg-white border border-gray-200 rounded-2xl p-4 shadow-sm">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-gray-900">Target Lists</h2>
              {loadingLists && <span className="text-xs text-gray-400">Loading...</span>}
            </div>
            <div className="space-y-2">
              {(listsData?.target_lists || []).map((list) => (
                <button
                  key={list.target_list_id}
                  onClick={() => setSelectedListId(list.target_list_id)}
                  className={`w-full text-left p-3 rounded-xl border transition ${
                    selectedListId === list.target_list_id
                      ? 'border-emerald-200 bg-emerald-50'
                      : 'border-gray-200 hover:border-gray-300 hover:bg-gray-50'
                  }`}
                >
                  <div className="text-sm font-medium text-gray-900">{list.name}</div>
                  <div className="text-xs text-gray-500 mt-1">{list.description || 'No description yet'}</div>
                  <div className="text-xs text-gray-500 mt-2">
                    {list.item_count} items, {list.matched_count} matched
                  </div>
                </button>
              ))}
              {!loadingLists && !(listsData?.target_lists || []).length && (
                <div className="text-sm text-gray-500">Create your first target list to begin importing PM firms.</div>
              )}
            </div>
          </div>

          <div className="space-y-6">
            <div className="bg-white border border-gray-200 rounded-2xl p-6 shadow-sm">
              <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-gray-900">
                    {selectedList?.name || 'Select a target list'}
                  </h2>
                  <p className="text-sm text-gray-500 mt-1">
                    {selectedList?.description || 'Paste a banker/referral table to create target items and auto-match them.'}
                  </p>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => rescoreMutation.mutate()}
                    disabled={!selectedListId || rescoreMutation.isPending}
                    className="px-3 py-2 rounded-lg border border-gray-300 text-sm font-medium text-gray-700 disabled:opacity-50"
                  >
                    {rescoreMutation.isPending ? 'Rescoring...' : 'Rescore List'}
                  </button>
                  <button
                    onClick={() => discoveryMutation.mutate()}
                    disabled={!selectedListId || discoveryMutation.isPending}
                    className="px-3 py-2 rounded-lg bg-gray-900 text-white text-sm font-medium disabled:opacity-50"
                  >
                    {discoveryMutation.isPending ? 'Finding...' : 'Find More Like These'}
                  </button>
                </div>
              </div>

              <div className="mt-5 grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_220px] gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">Paste TSV/CSV target table</label>
                  <textarea
                    value={pasteText}
                    onChange={(e) => setPasteText(e.target.value)}
                    className="w-full min-h-[180px] rounded-xl border border-gray-300 p-3 text-sm font-mono"
                    placeholder={'Company\tPortfolio Size\tUnits(est.)\tGeography\tOwnership\tKey Principal(s)\tCondo/Co-op Focus\tWebsite\tPhone\tAddress\tTier\tAcquisition Fit Notes\tKey Risk / Flag'}
                  />
                </div>
                <div className="bg-gray-50 border border-gray-200 rounded-xl p-4">
                  <div className="text-sm font-medium text-gray-900">Import Preview</div>
                  <div className="text-3xl font-semibold text-gray-900 mt-3">{parsedRows.length}</div>
                  <div className="text-xs text-gray-500 mt-1">rows parsed</div>
                  <button
                    onClick={() => importMutation.mutate(parsedRows)}
                    disabled={!selectedListId || !parsedRows.length || importMutation.isPending}
                    className="w-full mt-4 px-4 py-2 rounded-lg bg-emerald-600 text-white text-sm font-medium disabled:opacity-50"
                  >
                    {importMutation.isPending ? 'Importing...' : 'Import Targets'}
                  </button>
                  <p className="text-xs text-gray-500 mt-3">
                    Auto-match runs immediately after import and creates review alerts for ambiguous or unmatched firms.
                  </p>
                </div>
              </div>
            </div>

            <div className="bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden">
              <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-semibold text-gray-900">Imported Targets</h3>
                  <p className="text-xs text-gray-500 mt-1">
                    Ranked by thesis score, with match confidence and next-step readiness.
                  </p>
                </div>
                {loadingList && <span className="text-xs text-gray-400">Loading...</span>}
              </div>
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead className="bg-gray-50 text-gray-500">
                    <tr>
                      <th className="text-left font-medium px-6 py-3">Firm</th>
                      <th className="text-left font-medium px-4 py-3">Match</th>
                      <th className="text-left font-medium px-4 py-3">Thesis</th>
                      <th className="text-left font-medium px-4 py-3">Focus</th>
                      <th className="text-left font-medium px-4 py-3">Next Follow-Up</th>
                      <th className="text-left font-medium px-4 py-3">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(selectedList?.items || []).map((item) => (
                      <tr key={item.target_item_id} className="border-t border-gray-100">
                        <td className="px-6 py-4">
                          <div className="font-medium text-gray-900">{item.company_name}</div>
                          <div className="text-xs text-gray-500 mt-1">
                            {[item.geography, item.tier && `Tier ${item.tier}`, item.portfolio_estimate].filter(Boolean).join(' • ')}
                          </div>
                        </td>
                        <td className="px-4 py-4">
                          <span className={`inline-flex items-center px-2.5 py-1 rounded-full border text-xs font-medium ${badgeClass(item.match_status)}`}>
                            {item.match_status || 'unprocessed'}
                          </span>
                        </td>
                        <td className="px-4 py-4">
                          <div className="font-medium text-gray-900">{item.thesis_score ? Math.round(item.thesis_score) : '--'}</div>
                          <div className="text-xs text-gray-500 mt-1">{item.thesis_summary || 'Not scored yet'}</div>
                        </td>
                        <td className="px-4 py-4 text-gray-600 max-w-[220px]">
                          <div className="truncate">{item.condo_focus || item.acquisition_fit_notes || '--'}</div>
                        </td>
                        <td className="px-4 py-4 text-gray-600">{item.next_follow_up || '--'}</td>
                        <td className="px-4 py-4">
                          <Link
                            to={`/targets/${item.target_item_id}`}
                            className="inline-flex items-center px-3 py-1.5 rounded-lg border border-gray-300 text-xs font-medium text-gray-700 hover:bg-gray-50"
                          >
                            Open Dossier
                          </Link>
                        </td>
                      </tr>
                    ))}
                    {!loadingList && !(selectedList?.items || []).length && (
                      <tr>
                        <td colSpan={6} className="px-6 py-8 text-center text-sm text-gray-500">
                          Import target firms to populate this workbench.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="bg-white border border-gray-200 rounded-2xl p-6 shadow-sm">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-semibold text-gray-900">Adjacent Target Discovery</h3>
                  <p className="text-xs text-gray-500 mt-1">
                    Uses your matched seed set to surface similar PM firms already present in the dataset.
                  </p>
                </div>
                {discoveryMutation.isPending && <span className="text-xs text-gray-400">Ranking...</span>}
              </div>
              <div className="mt-4 space-y-3">
                {discoveries.map((discovery) => (
                  <div key={discovery.lead_id} className="border border-gray-200 rounded-xl p-4">
                    <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-2">
                      <div>
                        <div className="font-medium text-gray-900">{discovery.company_name}</div>
                        <div className="text-xs text-gray-500 mt-1">
                          {[discovery.primary_borough, `${discovery.portfolio_size || 0} buildings`, `${discovery.total_units || 0} units`].join(' • ')}
                        </div>
                      </div>
                      <div className="text-sm font-semibold text-gray-900">{Math.round(discovery.discovery_score)}</div>
                    </div>
                    <div className="text-xs text-gray-500 mt-2">{discovery.reasons.join(', ')}</div>
                  </div>
                ))}
                {!discoveries.length && (
                  <div className="text-sm text-gray-500">
                    Run discovery once you have a few matched seed firms in the selected list.
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default TargetsPage;
