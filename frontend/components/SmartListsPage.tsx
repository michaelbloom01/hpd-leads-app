import React, { useState, useEffect, useCallback } from 'react';
import { toast } from 'react-hot-toast';
import { useNavigate } from 'react-router-dom';
import {
  SmartList,
  getSmartLists,
  createSmartList,
  deleteSmartList,
  evaluateSmartList,
  updateSmartList,
} from '../services/api';

const SmartListsPage: React.FC = () => {
  const [lists, setLists] = useState<SmartList[]>([]);
  const [loading, setLoading] = useState(true);
  const [evaluating, setEvaluating] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [nameError, setNameError] = useState<string | null>(null);
  const navigate = useNavigate();

  const MIN_NAME_LENGTH = 2;
  const isNameValid = newName.trim().length >= MIN_NAME_LENGTH;

  const loadLists = useCallback(async () => {
    try {
      const data = await getSmartLists();
      setLists(data.smart_lists);
    } catch (err) {
      console.error('Failed to load smart lists:', err);
      const message = err instanceof Error ? err.message : 'Unknown error';
      toast.error(`Failed to load smart lists: ${message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadLists(); }, [loadLists]);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    if (newName.trim().length < MIN_NAME_LENGTH) {
      setNameError(`Name must be at least ${MIN_NAME_LENGTH} characters`);
      return;
    }
    setNameError(null);
    setCreating(true);
    try {
      await createSmartList({ name: newName.trim(), description: newDesc.trim(), filters: {}, pinned: false });
      toast.success(`Smart List "${newName}" created`);
      setNewName('');
      setNewDesc('');
      setShowCreate(false);
      loadLists();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unknown error';
      toast.error(`Failed to create smart list: ${message}`);
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (list: SmartList) => {
    if (!window.confirm(`Delete "${list.name}"? This cannot be undone.`)) return;
    setDeletingId(list.id);
    try {
      await deleteSmartList(list.id);
      toast.success('Deleted');
      loadLists();
    } catch (err) {
      toast.error('Failed to delete');
    } finally {
      setDeletingId(null);
    }
  };

  const handleEvaluate = async (list: SmartList) => {
    setEvaluating(list.id);
    try {
      const result = await evaluateSmartList(list.id);
      if (result.entered > 0 || result.exited > 0) {
        toast.success(`${list.name}: ${result.entered} entered, ${result.exited} exited (${result.total} total)`);
      } else {
        toast(`${list.name}: ${result.total} leads, no changes since last run`);
      }
      loadLists();
    } catch (err) {
      toast.error('Evaluation failed');
    } finally {
      setEvaluating(null);
    }
  };

  const handleTogglePin = async (list: SmartList) => {
    try {
      await updateSmartList(list.id, { pinned: !list.pinned });
      loadLists();
    } catch (err) {
      toast.error('Failed to update');
    }
  };

  const handleOpenInLeads = (list: SmartList) => {
    const filters = list.filters as Record<string, unknown>;
    const params = new URLSearchParams();
    if (filters.boroughs) {
      const boros = Array.isArray(filters.boroughs) ? filters.boroughs : String(filters.boroughs).split(',');
      if (boros.length > 0) params.set('boro', boros.join(','));
    }
    if (filters.min_score) params.set('min_score', String(filters.min_score));
    if (filters.max_score) params.set('max_score', String(filters.max_score));
    if (filters.min_portfolio) params.set('min_portfolio', String(filters.min_portfolio));
    if (filters.max_portfolio) params.set('max_portfolio', String(filters.max_portfolio));
    if (filters.min_units) params.set('min_units', String(filters.min_units));
    if (filters.max_units) params.set('max_units', String(filters.max_units));
    if (filters.entity_types) {
      const ets = Array.isArray(filters.entity_types) ? filters.entity_types : String(filters.entity_types).split(',');
      if (ets.length > 0) params.set('entity', ets.join(','));
    }
    if (filters.pipeline_stages) {
      const ps = Array.isArray(filters.pipeline_stages) ? filters.pipeline_stages : String(filters.pipeline_stages).split(',');
      if (ps.length > 0) params.set('stage', ps.join(','));
    }
    if (filters.has_phone) params.set('phone', '1');
    if (filters.has_email) params.set('email', '1');
    if (filters.search) params.set('q', String(filters.search));
    navigate(`/leads?${params.toString()}`);
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-12 space-y-4">
        <div className="w-10 h-10 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
        <p className="text-sm text-gray-500">Loading smart lists...</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-gray-900">Smart Lists</h2>
          <p className="text-sm text-gray-500">Saved filter segments that track lead changes over time.</p>
        </div>
        <button
          onClick={() => setShowCreate(!showCreate)}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg transition-colors"
        >
          + New Smart List
        </button>
      </div>

      {showCreate && (
        <div className="bg-white rounded-xl border border-gray-200 p-4 space-y-3">
          <input
            type="text"
            placeholder="List name (e.g., Bronx 100+ units)"
            className={`w-full px-3 py-2 border rounded-lg text-sm ${nameError ? 'border-red-400' : 'border-gray-300'}`}
            value={newName}
            onChange={(e) => {
              setNewName(e.target.value);
              if (nameError) setNameError(null);
            }}
            onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
            autoFocus
          />
          {nameError && (
            <p className="text-sm text-red-600">{nameError}</p>
          )}
          <input
            type="text"
            placeholder="Description (optional)"
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
            value={newDesc}
            onChange={(e) => setNewDesc(e.target.value)}
          />
          <p className="text-xs text-gray-400">
            Tip: Create the list first, then use "Save Current Filters" from the Leads page to populate it with your active filter set.
          </p>
          <div className="flex gap-2">
            <button onClick={handleCreate} disabled={creating || !isNameValid} className="px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-500 disabled:opacity-50 flex items-center gap-2">
              {creating ? (
                <>
                  <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  Creating...
                </>
              ) : (
                'Create'
              )}
            </button>
            <button onClick={() => { setShowCreate(false); setNameError(null); }} className="px-4 py-2 bg-gray-100 text-gray-700 text-sm rounded-lg">Cancel</button>
          </div>
        </div>
      )}

      {lists.length === 0 && !showCreate && (
        <div className="text-center py-16">
          <div className="w-16 h-16 mx-auto rounded-full bg-blue-50 flex items-center justify-center mb-4">
            <svg className="w-8 h-8 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
            </svg>
          </div>
          <h3 className="text-lg font-bold text-gray-900 mb-1">No Smart Lists yet</h3>
          <p className="text-sm text-gray-500 max-w-md mx-auto">
            Smart Lists save your filter criteria and track which leads enter or exit over time. Create one to get started.
          </p>
        </div>
      )}

      <div className="space-y-3">
        {lists.map((list) => (
          <div key={list.id} className="bg-white rounded-xl border border-gray-200 p-4 hover:shadow-sm transition-shadow">
            <div className="flex items-start justify-between">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  {list.pinned && (
                    <svg className="w-4 h-4 text-amber-500 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                      <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
                    </svg>
                  )}
                  <h3 className="font-bold text-gray-900 truncate">{list.name}</h3>
                  <span className="px-2 py-0.5 bg-blue-50 text-blue-700 text-xs font-medium rounded">
                    {list.last_count} leads
                  </span>
                </div>
                {list.description && (
                  <p className="text-sm text-gray-500 mt-1 truncate">{list.description}</p>
                )}
                <div className="flex items-center gap-4 mt-2 text-xs text-gray-400">
                  {list.last_evaluated_at && (
                    <span>Last evaluated: {new Date(list.last_evaluated_at).toLocaleDateString()}</span>
                  )}
                  <span>Created: {new Date(list.created_at).toLocaleDateString()}</span>
                </div>
              </div>
              <div className="flex items-center gap-1 ml-3 flex-shrink-0">
                <button
                  onClick={() => handleOpenInLeads(list)}
                  className="px-3 py-1.5 bg-blue-600 text-white text-xs font-medium rounded-lg hover:bg-blue-500 transition-colors"
                  title="Open this list's filters in the Leads page"
                >
                  View
                </button>
                <button
                  onClick={() => handleEvaluate(list)}
                  disabled={evaluating === list.id}
                  className="px-3 py-1.5 bg-emerald-600 text-white text-xs font-medium rounded-lg hover:bg-emerald-500 disabled:opacity-50 transition-colors"
                  title="Re-run filters and detect changes"
                >
                  {evaluating === list.id ? 'Running...' : 'Evaluate'}
                </button>
                <button
                  onClick={() => handleTogglePin(list)}
                  className={`p-1.5 rounded-lg transition-colors ${list.pinned ? 'text-amber-500 hover:text-amber-600' : 'text-gray-300 hover:text-amber-400'}`}
                  title={list.pinned ? 'Unpin from dashboard' : 'Pin to dashboard'}
                >
                  <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                    <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
                  </svg>
                </button>
                <button
                  onClick={() => handleDelete(list)}
                  disabled={deletingId === list.id}
                  className="p-1.5 text-gray-300 hover:text-rose-500 rounded-lg transition-colors disabled:opacity-50"
                  title="Delete this smart list"
                >
                  {deletingId === list.id ? (
                    <div className="w-4 h-4 border-2 border-rose-400 border-t-transparent rounded-full animate-spin" />
                  ) : (
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                  )}
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default SmartListsPage;
