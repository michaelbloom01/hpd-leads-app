import React, { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  BuildingList,
  BuildingListMember,
  createBuildingList,
  deleteBuildingList,
  fetchBuildingListMembers,
  getBuildingLists,
  removeBuildingFromList,
  updateBuildingList,
} from '../services/buildings-api';

const formatDate = (iso?: string) => {
  if (!iso) return '--';
  const dt = new Date(iso);
  return Number.isNaN(dt.getTime()) ? '--' : dt.toLocaleString();
};

const BuildingListsPage: React.FC = () => {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [newListName, setNewListName] = useState('');
  const [selectedListId, setSelectedListId] = useState<string | null>(null);
  const [renamingListId, setRenamingListId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState('');
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ['building-lists'],
    queryFn: getBuildingLists,
  });
  const lists = data?.building_lists ?? [];

  const { data: membersData, isLoading: membersLoading } = useQuery({
    queryKey: ['building-list-members', selectedListId],
    queryFn: () => fetchBuildingListMembers(selectedListId!),
    enabled: !!selectedListId,
  });

  const selectedList = useMemo(
    () => lists.find((l) => l.id === selectedListId) ?? null,
    [lists, selectedListId],
  );

  const members: BuildingListMember[] = membersData?.buildings ?? [];

  const refreshLists = () => {
    queryClient.invalidateQueries({ queryKey: ['building-lists'] });
    if (selectedListId) {
      queryClient.invalidateQueries({ queryKey: ['building-list-members', selectedListId] });
    }
  };

  const handleCreate = async () => {
    const trimmed = newListName.trim();
    if (!trimmed) return;
    setBusyKey('create');
    try {
      const created = await createBuildingList(trimmed);
      setNewListName('');
      setSelectedListId(created.id);
      refreshLists();
    } finally {
      setBusyKey(null);
    }
  };

  const handleDelete = async (list: BuildingList) => {
    const ok = window.confirm(`Delete "${list.name}"? This will remove all buildings from the list.`);
    if (!ok) return;
    setBusyKey(`delete:${list.id}`);
    try {
      await deleteBuildingList(list.id);
      if (selectedListId === list.id) setSelectedListId(null);
      refreshLists();
    } finally {
      setBusyKey(null);
    }
  };

  const startRename = (list: BuildingList) => {
    setRenamingListId(list.id);
    setRenameDraft(list.name);
  };

  const saveRename = async (listId: string) => {
    const trimmed = renameDraft.trim();
    if (!trimmed) return;
    setBusyKey(`rename:${listId}`);
    try {
      await updateBuildingList(listId, trimmed);
      setRenamingListId(null);
      setRenameDraft('');
      refreshLists();
    } finally {
      setBusyKey(null);
    }
  };

  const handleRemoveMember = async (bbl: string) => {
    if (!selectedListId) return;
    setBusyKey(`remove:${bbl}`);
    try {
      await removeBuildingFromList(selectedListId, bbl);
      refreshLists();
    } finally {
      setBusyKey(null);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Building Lists</h1>
        <p className="text-sm text-gray-500 mt-1">Saved collections of buildings for tracking and outreach.</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <section className="lg:col-span-1 bg-white rounded-lg border border-gray-200 p-4 space-y-4">
          <div className="flex gap-2">
            <input
              type="text"
              value={newListName}
              onChange={(e) => setNewListName(e.target.value)}
              placeholder="New list name"
              className="flex-1 px-3 py-2 text-sm border border-gray-300 rounded-lg"
            />
            <button
              type="button"
              onClick={handleCreate}
              disabled={busyKey === 'create' || !newListName.trim()}
              className="px-3 py-2 text-sm bg-blue-600 text-white rounded-lg disabled:opacity-50"
            >
              {busyKey === 'create' ? 'Creating...' : 'Create'}
            </button>
          </div>

          {isLoading ? (
            <p className="text-sm text-gray-500">Loading lists...</p>
          ) : error ? (
            <p className="text-sm text-red-600">{(error as Error).message}</p>
          ) : lists.length === 0 ? (
            <div className="rounded-lg border border-dashed border-gray-300 p-4 text-sm text-gray-500">
              No building lists yet. Create your first list above.
            </div>
          ) : (
            <div className="space-y-2">
              {lists.map((list) => {
                const active = selectedListId === list.id;
                return (
                  <div key={list.id} className={`border rounded-lg p-3 ${active ? 'border-blue-500 bg-blue-50' : 'border-gray-200'}`}>
                    <button
                      type="button"
                      onClick={() => setSelectedListId(list.id)}
                      className="w-full text-left"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <p className="font-medium text-sm text-gray-900 truncate">{list.name}</p>
                        <span className="text-xs text-gray-500">{list.member_count ?? 0}</span>
                      </div>
                      <p className="text-[11px] text-gray-500 mt-1">Updated {formatDate(list.updated_at)}</p>
                    </button>
                    <div className="mt-2 flex gap-2">
                      <button
                        type="button"
                        onClick={() => startRename(list)}
                        className="text-xs px-2 py-1 border border-gray-300 rounded"
                      >
                        Rename
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDelete(list)}
                        disabled={busyKey === `delete:${list.id}`}
                        className="text-xs px-2 py-1 border border-red-200 text-red-700 rounded disabled:opacity-50"
                      >
                        {busyKey === `delete:${list.id}` ? 'Deleting...' : 'Delete'}
                      </button>
                    </div>
                    {renamingListId === list.id && (
                      <div className="mt-2 flex gap-2">
                        <input
                          type="text"
                          value={renameDraft}
                          onChange={(e) => setRenameDraft(e.target.value)}
                          className="flex-1 px-2 py-1 text-xs border border-gray-300 rounded"
                        />
                        <button
                          type="button"
                          onClick={() => saveRename(list.id)}
                          disabled={busyKey === `rename:${list.id}` || !renameDraft.trim()}
                          className="text-xs px-2 py-1 bg-blue-600 text-white rounded disabled:opacity-50"
                        >
                          Save
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </section>

        <section className="lg:col-span-2 bg-white rounded-lg border border-gray-200 p-4">
          {!selectedList ? (
            <div className="text-sm text-gray-500">Select a building list to view members.</div>
          ) : membersLoading ? (
            <div className="text-sm text-gray-500">Loading buildings...</div>
          ) : members.length === 0 ? (
            <div className="text-sm text-gray-500">This list is empty. Use Buildings page actions to add members.</div>
          ) : (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <h2 className="text-base font-semibold text-gray-900">{selectedList.name}</h2>
                <span className="text-xs text-gray-500">{members.length} buildings</span>
              </div>
              {members.map((building) => (
                <div key={building.bbl} className="border border-gray-200 rounded-lg p-3 flex items-center justify-between gap-4">
                  <button
                    type="button"
                    onClick={() => navigate(`/buildings/${building.bbl}`)}
                    className="text-left min-w-0"
                  >
                    <p className="text-sm font-medium text-blue-700 hover:underline truncate">
                      {building.address || building.bbl}
                    </p>
                    <p className="text-xs text-gray-500 mt-1">
                      BBL (Borough-Block-Lot): {building.bbl} • {building.borough || 'N/A'} • {building.unit_count ?? '--'} units
                    </p>
                  </button>
                  <button
                    type="button"
                    onClick={() => handleRemoveMember(building.bbl)}
                    disabled={busyKey === `remove:${building.bbl}`}
                    className="text-xs px-2 py-1 border border-red-200 text-red-700 rounded disabled:opacity-50"
                  >
                    {busyKey === `remove:${building.bbl}` ? 'Removing...' : 'Remove'}
                  </button>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
};

export default BuildingListsPage;
