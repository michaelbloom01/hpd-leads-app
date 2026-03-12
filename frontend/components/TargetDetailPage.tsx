import React, { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { createUnifiedOutreachEvent } from '../services/outreach-api';
import { getTargetDossier, patchTargetItem, selectTargetMatch } from '../services/targets-api';

const TargetDetailPage: React.FC = () => {
  const { targetItemId } = useParams();
  const queryClient = useQueryClient();
  const [notes, setNotes] = useState('');
  const [outreach, setOutreach] = useState({
    stage: 'research',
    method: 'email',
    outcome: 'pending',
    notes: '',
    next_follow_up: '',
  });

  const { data, isLoading } = useQuery({
    queryKey: ['target-dossier', targetItemId],
    queryFn: () => getTargetDossier(targetItemId!),
    enabled: !!targetItemId,
  });

  const saveNotesMutation = useMutation({
    mutationFn: () => patchTargetItem(targetItemId!, { notes }),
    onSuccess: async () => {
      toast.success('Notes updated');
      await queryClient.invalidateQueries({ queryKey: ['target-dossier', targetItemId] });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const outreachMutation = useMutation({
    mutationFn: () => createUnifiedOutreachEvent({ target_item_id: targetItemId!, ...outreach }),
    onSuccess: async () => {
      toast.success('Outreach event logged');
      setOutreach((prev) => ({ ...prev, notes: '', next_follow_up: '' }));
      await queryClient.invalidateQueries({ queryKey: ['target-dossier', targetItemId] });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const selectMatchMutation = useMutation({
    mutationFn: (leadId: string) => selectTargetMatch(targetItemId!, leadId),
    onSuccess: async () => {
      toast.success('Match selected');
      await queryClient.invalidateQueries({ queryKey: ['target-dossier', targetItemId] });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const targetItem = data?.target_item;
  const matchedLead = data?.matched_lead as Record<string, unknown> | null | undefined;

  const topSignals = useMemo(() => {
    const breakdown = (targetItem?.thesis_breakdown as Record<string, number> | undefined) || {};
    return Object.entries(breakdown)
      .sort((a, b) => (b[1] || 0) - (a[1] || 0))
      .slice(0, 3);
  }, [targetItem]);

  React.useEffect(() => {
    if (targetItem?.notes && !notes) {
      setNotes(String(targetItem.notes));
    }
  }, [notes, targetItem?.notes]);

  if (isLoading) {
    return <div className="p-8 text-sm text-gray-500">Loading target dossier...</div>;
  }
  if (!data || !targetItem) {
    return <div className="p-8 text-sm text-gray-500">Target dossier not found.</div>;
  }

  return (
    <div className="h-full overflow-auto bg-gray-50">
      <div className="max-w-7xl mx-auto p-6 space-y-6">
        <div className="bg-white border border-gray-200 rounded-2xl p-6 shadow-sm">
          <div className="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-4">
            <div>
              <div className="text-sm text-gray-500">Target Dossier</div>
              <h1 className="text-2xl font-semibold text-gray-900 mt-1">{String(targetItem.company_name)}</h1>
              <div className="text-sm text-gray-500 mt-2">
                {[targetItem.geography, targetItem.tier && `Tier ${String(targetItem.tier)}`, targetItem.match_status].filter(Boolean).join(' • ')}
              </div>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div className="bg-gray-50 border border-gray-200 rounded-xl px-4 py-3">
                <div className="text-xs text-gray-500">Thesis Score</div>
                <div className="text-xl font-semibold text-gray-900 mt-1">
                  {targetItem.thesis_score ? Math.round(Number(targetItem.thesis_score)) : '--'}
                </div>
              </div>
              <div className="bg-gray-50 border border-gray-200 rounded-xl px-4 py-3">
                <div className="text-xs text-gray-500">Match Status</div>
                <div className="text-xl font-semibold text-gray-900 mt-1">{String(targetItem.match_status || '--')}</div>
              </div>
              <div className="bg-gray-50 border border-gray-200 rounded-xl px-4 py-3">
                <div className="text-xs text-gray-500">Buildings</div>
                <div className="text-xl font-semibold text-gray-900 mt-1">{data.linked_buildings.length}</div>
              </div>
            </div>
          </div>
          <p className="text-sm text-gray-600 mt-4">{String(targetItem.thesis_summary || 'No thesis summary yet.')}</p>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-[1.15fr_0.85fr] gap-6">
          <div className="space-y-6">
            <div className="bg-white border border-gray-200 rounded-2xl p-6 shadow-sm">
              <h2 className="text-sm font-semibold text-gray-900">Fit And Imported Intelligence</h2>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4 text-sm">
                <div>
                  <div className="text-xs uppercase tracking-wide text-gray-400">Ownership</div>
                  <div className="mt-1 text-gray-700">{String(targetItem.ownership || '--')}</div>
                </div>
                <div>
                  <div className="text-xs uppercase tracking-wide text-gray-400">Key Principals</div>
                  <div className="mt-1 text-gray-700">{String(targetItem.key_principals || '--')}</div>
                </div>
                <div>
                  <div className="text-xs uppercase tracking-wide text-gray-400">Condo / Co-op Focus</div>
                  <div className="mt-1 text-gray-700">{String(targetItem.condo_focus || '--')}</div>
                </div>
                <div>
                  <div className="text-xs uppercase tracking-wide text-gray-400">Fit Note</div>
                  <div className="mt-1 text-gray-700">{String(targetItem.acquisition_fit_notes || '--')}</div>
                </div>
                <div>
                  <div className="text-xs uppercase tracking-wide text-gray-400">Risk Flag</div>
                  <div className="mt-1 text-gray-700">{String(targetItem.risk_flag || '--')}</div>
                </div>
                <div>
                  <div className="text-xs uppercase tracking-wide text-gray-400">Contact Surface</div>
                  <div className="mt-1 text-gray-700">
                    {[targetItem.website, targetItem.phone, matchedLead?.email as string | undefined].filter(Boolean).join(' • ') || '--'}
                  </div>
                </div>
              </div>
              <div className="mt-5">
                <div className="text-xs uppercase tracking-wide text-gray-400">Top Score Drivers</div>
                <div className="flex flex-wrap gap-2 mt-2">
                  {topSignals.map(([label, value]) => (
                    <span key={label} className="inline-flex items-center px-2.5 py-1 rounded-full bg-gray-100 text-gray-700 text-xs">
                      {label.replace(/_/g, ' ')}: {Math.round(value)}
                    </span>
                  ))}
                  {!topSignals.length && <span className="text-sm text-gray-500">No breakdown available yet.</span>}
                </div>
              </div>
            </div>

            <div className="bg-white border border-gray-200 rounded-2xl p-6 shadow-sm">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-gray-900">Matched Lead And Buildings</h2>
                {matchedLead?.lead_id && (
                  <Link
                    to={`/leads/${encodeURIComponent(String(matchedLead.lead_id))}`}
                    className="text-sm font-medium text-emerald-700 hover:text-emerald-800"
                  >
                    Open Lead
                  </Link>
                )}
              </div>
              {matchedLead ? (
                <div className="space-y-4 mt-4">
                  <div className="border border-gray-200 rounded-xl p-4">
                    <div className="font-medium text-gray-900">
                      {String(matchedLead.company_name || matchedLead.agent_name || matchedLead.owner_name || matchedLead.lead_id)}
                    </div>
                    <div className="text-sm text-gray-500 mt-1">
                      {[matchedLead.primary_borough, `${matchedLead.portfolio_size || 0} buildings`, `${matchedLead.total_units || 0} units`].join(' • ')}
                    </div>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    {data.linked_buildings.slice(0, 12).map((building) => (
                      <div key={String(building.bbl)} className="border border-gray-200 rounded-xl p-3">
                        <div className="font-medium text-gray-900">{String(building.address || building.bbl)}</div>
                        <div className="text-xs text-gray-500 mt-1">
                          {[building.borough, `${building.unit_count || 0} units`, building.building_type].filter(Boolean).join(' • ')}
                        </div>
                      </div>
                    ))}
                    {!data.linked_buildings.length && (
                      <div className="text-sm text-gray-500">No linked buildings on the matched lead yet.</div>
                    )}
                  </div>
                </div>
              ) : (
                <div className="mt-4 text-sm text-gray-500">No lead has been selected for this target yet.</div>
              )}
            </div>

            <div className="bg-white border border-gray-200 rounded-2xl p-6 shadow-sm">
              <h2 className="text-sm font-semibold text-gray-900">Manual Match Resolution</h2>
              <div className="space-y-3 mt-4">
                {data.matches.map((match) => (
                  <div key={`${String(match.id)}-${String(match.lead_id)}`} className="border border-gray-200 rounded-xl p-4 flex flex-col lg:flex-row lg:items-center lg:justify-between gap-3">
                    <div>
                      <div className="font-medium text-gray-900">
                        {String(match.company_name || match.agent_name || match.owner_name || match.lead_id || 'Unknown candidate')}
                      </div>
                      <div className="text-xs text-gray-500 mt-1">
                        {[match.primary_borough, `${match.portfolio_size || 0} buildings`, `${match.total_units || 0} units`, `confidence ${Math.round(Number(match.confidence_score || 0) * 100)}%`].join(' • ')}
                      </div>
                    </div>
                    <button
                      onClick={() => selectMatchMutation.mutate(String(match.lead_id))}
                      disabled={selectMatchMutation.isPending || !match.lead_id}
                      className={`px-3 py-2 rounded-lg text-sm font-medium ${
                        match.selected
                          ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                          : 'bg-white text-gray-700 border border-gray-300 hover:bg-gray-50'
                      }`}
                    >
                      {match.selected ? 'Selected' : 'Select Match'}
                    </button>
                  </div>
                ))}
                {!data.matches.length && <div className="text-sm text-gray-500">No candidate matches were found.</div>}
              </div>
            </div>
          </div>

          <div className="space-y-6">
            <div className="bg-white border border-gray-200 rounded-2xl p-6 shadow-sm">
              <h2 className="text-sm font-semibold text-gray-900">People Graph</h2>
              <div className="space-y-3 mt-4">
                {data.people_graph.map((person) => (
                  <div key={`${String(person.name)}-${String(person.role)}`} className="border border-gray-200 rounded-xl p-3">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <div className="font-medium text-gray-900">{String(person.name)}</div>
                        <div className="text-xs text-gray-500 mt-1">{String(person.role || person.source || 'Unknown')}</div>
                      </div>
                      {person.is_decision_maker ? (
                        <span className="inline-flex items-center px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-700 text-xs">
                          Likely decision-maker
                        </span>
                      ) : null}
                    </div>
                  </div>
                ))}
                {!data.people_graph.length && <div className="text-sm text-gray-500">No people graph yet. Enrich the matched lead or import more intelligence.</div>}
              </div>
            </div>

            <div className="bg-white border border-gray-200 rounded-2xl p-6 shadow-sm">
              <h2 className="text-sm font-semibold text-gray-900">Missing Diligence</h2>
              <div className="space-y-2 mt-4">
                {data.missing_diligence.map((item) => (
                  <div key={item} className="flex items-start gap-2 text-sm text-gray-700">
                    <span className="mt-1 h-2 w-2 rounded-full bg-amber-500" />
                    <span>{item}</span>
                  </div>
                ))}
                {!data.missing_diligence.length && <div className="text-sm text-emerald-700">No immediate diligence gaps were detected.</div>}
              </div>
            </div>

            <div className="bg-white border border-gray-200 rounded-2xl p-6 shadow-sm">
              <h2 className="text-sm font-semibold text-gray-900">Outreach</h2>
              <div className="grid grid-cols-1 gap-3 mt-4">
                <input
                  value={outreach.stage}
                  onChange={(e) => setOutreach((prev) => ({ ...prev, stage: e.target.value }))}
                  className="px-3 py-2 rounded-lg border border-gray-300 text-sm"
                  placeholder="Stage"
                />
                <div className="grid grid-cols-2 gap-3">
                  <input
                    value={outreach.method}
                    onChange={(e) => setOutreach((prev) => ({ ...prev, method: e.target.value }))}
                    className="px-3 py-2 rounded-lg border border-gray-300 text-sm"
                    placeholder="Method"
                  />
                  <input
                    value={outreach.outcome}
                    onChange={(e) => setOutreach((prev) => ({ ...prev, outcome: e.target.value }))}
                    className="px-3 py-2 rounded-lg border border-gray-300 text-sm"
                    placeholder="Outcome"
                  />
                </div>
                <input
                  type="date"
                  value={outreach.next_follow_up}
                  onChange={(e) => setOutreach((prev) => ({ ...prev, next_follow_up: e.target.value }))}
                  className="px-3 py-2 rounded-lg border border-gray-300 text-sm"
                />
                <textarea
                  value={outreach.notes}
                  onChange={(e) => setOutreach((prev) => ({ ...prev, notes: e.target.value }))}
                  className="min-h-[100px] px-3 py-2 rounded-lg border border-gray-300 text-sm"
                  placeholder="What happened, who responded, and what should happen next?"
                />
                <button
                  onClick={() => outreachMutation.mutate()}
                  disabled={outreachMutation.isPending}
                  className="px-4 py-2 rounded-lg bg-emerald-600 text-white text-sm font-medium"
                >
                  {outreachMutation.isPending ? 'Logging...' : 'Log Outreach Event'}
                </button>
              </div>
              <div className="space-y-3 mt-5">
                {data.outreach_events.map((event) => (
                  <div key={String(event.id)} className="border border-gray-200 rounded-xl p-3">
                    <div className="font-medium text-gray-900">{String(event.stage || 'event')}</div>
                    <div className="text-xs text-gray-500 mt-1">
                      {[event.method, event.outcome, event.event_timestamp || event.created_at].filter(Boolean).join(' • ')}
                    </div>
                    {event.notes ? <div className="text-sm text-gray-700 mt-2">{String(event.notes)}</div> : null}
                  </div>
                ))}
                {!data.outreach_events.length && <div className="text-sm text-gray-500">No outreach events logged yet.</div>}
              </div>
            </div>

            <div className="bg-white border border-gray-200 rounded-2xl p-6 shadow-sm">
              <h2 className="text-sm font-semibold text-gray-900">Operator Notes</h2>
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                className="w-full min-h-[140px] mt-4 px-3 py-2 rounded-lg border border-gray-300 text-sm"
                placeholder="Banker notes, referral path, personal takes, diligence reminders..."
              />
              <button
                onClick={() => saveNotesMutation.mutate()}
                disabled={saveNotesMutation.isPending}
                className="mt-3 px-4 py-2 rounded-lg border border-gray-300 text-sm font-medium text-gray-700"
              >
                {saveNotesMutation.isPending ? 'Saving...' : 'Save Notes'}
              </button>
            </div>

            <div className="bg-white border border-gray-200 rounded-2xl p-6 shadow-sm">
              <h2 className="text-sm font-semibold text-gray-900">Adjacent Lookalikes</h2>
              <div className="space-y-3 mt-4">
                {data.adjacent_targets.map((candidate) => (
                  <div key={candidate.lead_id} className="border border-gray-200 rounded-xl p-3">
                    <div className="font-medium text-gray-900">{candidate.company_name}</div>
                    <div className="text-xs text-gray-500 mt-1">
                      {[candidate.primary_borough, `${candidate.portfolio_size || 0} buildings`, `${candidate.total_units || 0} units`, `score ${Math.round(candidate.discovery_score)}`].join(' • ')}
                    </div>
                  </div>
                ))}
                {!data.adjacent_targets.length && <div className="text-sm text-gray-500">No adjacent targets surfaced yet.</div>}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default TargetDetailPage;
