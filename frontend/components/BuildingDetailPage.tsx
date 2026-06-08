/**
 * BuildingDetailPage — Deep dive into a single building's churn signals.
 */
import React, { lazy, Suspense } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useParams, useNavigate } from 'react-router-dom';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';
import {
  fetchBuildingDetail, fetchBuildingTimeline, fetchBuildingScoreHistory,
  addBuildingToPipeline, fetchBuildingOutreachEvents, logBuildingOutreachEvent, requestBuildingDosContactsRefresh,
  type BuildingContactEntry, type BuildingDetail,
} from '../services/buildings-api';
import { fetchSubjectTruthSummary, type SubjectTruthSummary } from '../services/truth-api';
import { assessContactConfidence } from '../utils/contactConfidence';
import {
  formatClaimSubtitle,
  formatClaimTitle,
  formatSourceName,
  formatTruthAction,
  formatReviewBucket,
  truthSummaryHeadline,
  visibleTruthClaims,
} from '../utils/truthDisplay';
import { toast } from 'react-hot-toast';

const PortfolioMap = lazy(() => import('./PortfolioMap'));

const formatRelativeDate = (value: string | null | undefined): string => {
  if (!value) return '--';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  const now = new Date();
  const diffDays = Math.floor((now.getTime() - d.getTime()) / (1000 * 60 * 60 * 24));
  if (diffDays < 1) return 'Today';
  if (diffDays === 1) return '1 day ago';
  if (diffDays < 30) return `${diffDays} days ago`;
  const months = Math.floor(diffDays / 30);
  if (months === 1) return '1 month ago';
  if (months < 12) return `${months} months ago`;
  const years = Math.floor(months / 12);
  return years === 1 ? '1 year ago' : `${years} years ago`;
};

const formatAbsoluteDate = (value: string | null | undefined): string => {
  if (!value) return '--';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString();
};

const signalLabels: Record<string, string> = {
  ownership_change: 'Ownership Change',
  complaint_spike: 'Complaint Spike',
  violation_trend: 'Violation Trend',
  energy_grade_drop: 'Energy Grade Drop',
  dob_permits: 'DOB Permits',
  hpd_litigation: 'Housing Litigation',
  emergency_repairs: 'Emergency Repairs',
  building_size: 'Building Scale',
  eviction_activity: 'Eviction Activity',
  facade_status: 'Facade Status',
};

const getChurnTone = (score: number | null | undefined): string => {
  if (score == null) return 'text-gray-400';
  if (score >= 70) return 'text-red-600';
  if (score >= 40) return 'text-amber-600';
  return 'text-green-600';
};

const getChurnCategoryLabel = (category: string | null | undefined): string => {
  if (category === 'hot') return 'Hot churn risk';
  if (category === 'warm') return 'Watchlist';
  if (category === 'stable') return 'More stable';
  return 'Not scored yet';
};

const pct = (value: number | null | undefined): string => {
  if (value == null) return '--';
  return `${Math.round(value * 100)}%`;
};

const confidenceClass = (value: number | null | undefined): string => {
  if (value == null) return 'border-gray-200 bg-gray-50 text-gray-600';
  if (value >= 0.8) return 'border-emerald-200 bg-emerald-50 text-emerald-700';
  if (value >= 0.55) return 'border-amber-200 bg-amber-50 text-amber-700';
  return 'border-rose-200 bg-rose-50 text-rose-700';
};

const eventIcons: Record<string, string> = {
  complaint: 'C',
  violation: 'V',
  transaction: 'T',
  permit: 'P',
  litigation: 'L',
  emergency_repair: 'ER',
  eviction: 'E',
  energy: 'EN',
  facade: 'F',
  aep: 'A',
};

const BuildingDetailPage: React.FC = () => {
  const { bbl: rawBbl } = useParams<{ bbl: string }>();
  const navigate = useNavigate();

  const decodedBbl = React.useMemo(() => {
    if (!rawBbl) return null;
    return decodeURIComponent(rawBbl).trim() || null;
  }, [rawBbl]);

  const [isRefreshingDos, setIsRefreshingDos] = React.useState(false);

  const { data: building, isLoading, error, refetch: refetchBuilding } = useQuery({
    queryKey: ['building', decodedBbl],
    queryFn: () => fetchBuildingDetail(decodedBbl!),
    enabled: !!decodedBbl,
    retry: 1,
    refetchInterval: (query) => {
      const status = (query.state.data as BuildingDetail | undefined)?.dos_contacts_status;
      return status === 'refreshing' ? 5000 : false;
    },
    refetchOnWindowFocus: true,
  });

  const activeBbl = building?.bbl || decodedBbl;

  const { data: truthSummary } = useQuery({
    queryKey: ['truth-subject', 'building', activeBbl],
    queryFn: () => fetchSubjectTruthSummary('building', activeBbl!),
    enabled: !!activeBbl && !!building,
    retry: false,
  });

  const { data: timeline } = useQuery({
    queryKey: ['building-timeline', activeBbl],
    queryFn: () => fetchBuildingTimeline(activeBbl!),
    enabled: !!activeBbl && !!building,
  });

  const { data: scoreHistory } = useQuery({
    queryKey: ['building-score-history', activeBbl],
    queryFn: () => fetchBuildingScoreHistory(activeBbl!),
    enabled: !!activeBbl && !!building,
  });

  const { data: outreachData, refetch: refetchOutreach } = useQuery({
    queryKey: ['building-outreach', activeBbl],
    queryFn: () => fetchBuildingOutreachEvents(activeBbl!),
    enabled: !!activeBbl && !!building,
  });

  const handleLogOutreach = async (stage: string) => {
    if (!activeBbl) return;
    try {
      await logBuildingOutreachEvent(activeBbl, { stage, method: 'manual' });
      toast.success(`Outreach logged: ${stage}`);
      refetchOutreach();
    } catch {
      toast.error('Failed to log outreach');
    }
  };

  const handleAddToPipeline = async () => {
    if (!activeBbl) return;
    try {
      await addBuildingToPipeline(activeBbl);
      toast.success('Building added to pipeline');
    } catch {
      toast.error('Failed to add to pipeline');
    }
  };

  const handleRefreshDosContacts = async () => {
    if (!activeBbl || isRefreshingDos) return;
    setIsRefreshingDos(true);
    try {
      const response = await requestBuildingDosContactsRefresh(activeBbl);
      if (response.status === 'skipped') {
        toast.error('No corporate owner found for DOS lookup');
      } else if (response.status === 'approval_required') {
        toast.success('DOS refresh preview ready; no refresh was queued');
      } else {
        toast.success(response.status === 'refreshing' ? 'DOS refresh requested' : 'DOS contacts updated');
      }
      await refetchBuilding();
    } catch {
      toast.error('Failed to request DOS refresh');
    } finally {
      setIsRefreshingDos(false);
    }
  };

  if (isLoading) return <div className="p-8 text-center text-gray-400">Loading...</div>;
  if (!decodedBbl || !building) return <div className="p-8 text-center text-red-500">Building not found{error ? ` — ${(error as Error).message}` : ''}</div>;

  const latestHistoryBreakdown =
    scoreHistory && scoreHistory.length > 0
      ? (scoreHistory[0]?.churn_breakdown as BuildingDetail['churn_breakdown'])
      : null;
  const breakdown: BuildingDetail['churn_breakdown'] = building.churn_breakdown || latestHistoryBreakdown;
  const chartData = scoreHistory?.slice().reverse().map(h => ({
    date: new Date(h.scored_at).toLocaleDateString(),
    score: h.churn_score,
  })) || [];
  const hasDosContacts = Boolean(
    building.all_contacts?.some((c: BuildingContactEntry) => c.source === 'NY DOS Filing' || c.source === 'NY DOS Snapshot')
  );
  const dosStatus = building.dos_contacts_status || (building.dos_contacts_is_stale ? 'stale' : 'loaded');
  const contactCount = building.all_contacts?.length || 0;
  const sortedContacts = [...(building.all_contacts || [])].sort((a, b) => {
    if (a.is_decision_maker !== b.is_decision_maker) return a.is_decision_maker ? -1 : 1;
    const aTime = a.as_of_date ? Date.parse(a.as_of_date) : 0;
    const bTime = b.as_of_date ? Date.parse(b.as_of_date) : 0;
    return bTime - aTime;
  });
  const boardLeaders = sortedContacts.filter((c: BuildingContactEntry) =>
    c.confidence_hint === 'Likely board member (resident)' || c.role === 'DOS Chairman (Biennial)')
    .slice(0, 3);
  const showContactsCard =
    dosStatus !== 'loaded' ||
    Boolean(building.management_company) ||
    Boolean(building.corporate_owner) ||
    contactCount > 0;
  const breakdownEntries = Object.entries(breakdown || {}).sort(([, a], [, b]) => b.contribution - a.contribution);
  const activeBreakdownEntries = breakdownEntries.filter(([, val]) =>
    val.raw !== null && (Number(val.raw || 0) > 0 || Number(val.contribution || 0) > 0)
  );
  const noSignalBreakdownEntries = breakdownEntries.filter(([, val]) =>
    val.raw !== null && Number(val.raw || 0) === 0 && Number(val.contribution || 0) === 0
  );
  const unavailableBreakdownEntries = breakdownEntries.filter(([, val]) => val.raw === null);
  const truthClaims = (truthSummary?.claims || []).slice(0, 6);
  const truthSourceNames = Array.from(new Set(
    truthClaims.flatMap((claim) => [
      ...(claim.supporting_sources || []),
      ...(claim.contradicting_sources || []).map((source) => `${source} (contradicts)`),
    ]),
  ));
  const truthSchemaReady = (truthSummary as SubjectTruthSummary | undefined)?.schema_status?.ready;
  const contradictionCount = truthSummary?.belief_summary.contradiction_count || 0;
  const truthHeadline = truthSummaryHeadline(truthSummary, 'building');
  const strongestTruthClaims = visibleTruthClaims(truthClaims, 4);
  const dosBanner = (() => {
    if (dosStatus === 'refreshing') {
      return {
        tone: 'border-blue-200 bg-blue-50 text-blue-700',
        text: 'Refreshing DOS contact data now. This page updates automatically when the refresh finishes.',
      };
    }
    if (dosStatus === 'stale') {
      return {
        tone: 'border-amber-200 bg-amber-50 text-amber-700',
        text: 'DOS contact data is stale. Request a refresh to pull the latest filing snapshot.',
      };
    }
    if (dosStatus === 'not_loaded') {
      return {
        tone: 'border-gray-200 bg-gray-50 text-gray-600',
        text: 'DOS corporate officer data has not been loaded for this building yet.',
      };
    }
    if (dosStatus === 'no_match') {
      return {
        tone: 'border-gray-200 bg-gray-50 text-gray-600',
        text: 'No NY DOS filing match was found for the current corporate owner lookup.',
      };
    }
    if (dosStatus === 'loaded' && !hasDosContacts) {
      return {
        tone: 'border-gray-200 bg-gray-50 text-gray-600',
        text: 'NY DOS data loaded, but no officer or chairman contacts were found in the current filing snapshot.',
      };
    }
    return null;
  })();

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <button onClick={() => navigate('/buildings')} className="text-sm text-gray-500 hover:text-gray-700 mb-2 flex items-center gap-1">
            ← Back to Buildings
          </button>
          <h1 className="text-2xl font-bold text-gray-900">{building.address || building.bbl}</h1>
          <div className="flex items-center gap-3 mt-1 text-sm text-gray-500">
            <span>{building.borough}</span>
            <span title="Borough-Block-Lot: NYC's unique property identifier">BBL: {building.bbl}</span>
            {building.unit_count && <span>{building.unit_count} units</span>}
            {building.year_built && <span>Built {building.year_built}</span>}
          </div>
        </div>
        <div className="flex items-center gap-3">
          {building.churn_score !== null ? (
            <div className="text-right">
              <div className={`text-3xl font-bold ${getChurnTone(building.churn_score)}`}>
                {building.churn_score.toFixed(1)}
              </div>
              <div className="text-[11px] text-gray-500">
                {getChurnCategoryLabel(building.churn_category)}
              </div>
            </div>
          ) : (
            <div className="text-right">
              <div className="text-2xl font-semibold text-gray-400">--</div>
              <div className="text-[11px] text-gray-500">Score pending</div>
            </div>
          )}
          <button
            onClick={handleAddToPipeline}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
          >
            Add to Pipeline
          </button>
        </div>
      </div>

      <div className={`rounded-lg border p-5 ${confidenceClass(truthSummary?.overall_confidence_score)}`}>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="font-semibold">Data Confidence</h2>
              {truthSummary?.schema_status && (
                <span className="rounded border border-current px-2 py-0.5 text-[11px] font-medium">
                  {truthSchemaReady ? 'ledger online' : 'ledger setup incomplete'}
                </span>
              )}
            </div>
            <p className="mt-1 max-w-2xl text-sm opacity-85">{truthHeadline}</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {(truthSummary?.belief_summary.safe_actions || ['not_evaluated']).map((action) => (
                <span key={action} className="rounded-full bg-white/70 px-2 py-1 text-[11px] font-medium">
                  {formatTruthAction(action)}
                </span>
              ))}
            </div>
          </div>
          <div className="lg:text-right">
            <div className="text-3xl font-bold">{pct(truthSummary?.overall_confidence_score)}</div>
            <div className="text-[11px] uppercase font-bold opacity-70">
              {formatReviewBucket(truthSummary?.review_bucket)}
            </div>
            <div className="mt-1 text-xs opacity-75">
              {truthSummary?.belief_summary.freshness_days != null
                ? `${truthSummary.belief_summary.freshness_days}d freshest claim`
                : 'freshness pending'}
            </div>
          </div>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-2 lg:grid-cols-4">
          {[
            ['Claims checked', truthSummary?.claims.length ?? '--'],
            ['Conflicts', contradictionCount],
            ['Supporting sources', truthSummary?.belief_summary.supporting_sources?.length ?? '--'],
            ['Contradicting sources', truthSummary?.belief_summary.contradicting_sources?.length ?? 0],
          ].map(([label, value]) => (
            <div key={label as string} className="rounded border border-white/70 bg-white/70 px-3 py-2">
              <div className="text-lg font-semibold">{value}</div>
              <div className="text-[11px] uppercase font-bold opacity-65">{label}</div>
            </div>
          ))}
        </div>

        <div className="mt-4 rounded border border-white/70 bg-white/70 p-3">
          <h3 className="text-xs font-bold uppercase tracking-wider opacity-70">What Matters</h3>
          <div className="mt-2 space-y-2">
            {(strongestTruthClaims.length ? strongestTruthClaims : truthClaims).slice(0, 4).map((claim) => (
              <div key={claim.claim_id} className="rounded border border-white bg-white/80 px-3 py-2">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-sm font-semibold">{formatClaimTitle(claim)}</div>
                    <div className="mt-0.5 text-xs opacity-75">{formatClaimSubtitle(claim)}</div>
                  </div>
                  <span className="shrink-0 rounded border border-current px-1.5 py-0.5 text-[11px] font-semibold">
                    {pct(claim.confidence_score)}
                  </span>
                </div>
              </div>
            ))}
            {!truthClaims.length && (
              <p className="text-sm opacity-75">No building-level truth claims are available yet.</p>
            )}
          </div>
        </div>

        <details
          aria-label="Evidence Ledger audit trail"
          className="mt-3 rounded border border-white/70 bg-white/60"
        >
          <summary className="cursor-pointer px-3 py-2 text-xs font-bold uppercase tracking-wider opacity-75">
            Audit trail ({truthClaims.length} claims)
          </summary>
          <div className="border-t border-white/70 p-3">
            {!truthClaims.length ? (
              <p className="mt-2 text-sm opacity-75">
                No building-level claims are materialized yet. The schema/ledger gate is keeping this from looking more certain than it is.
              </p>
            ) : (
              <div className="mt-2 space-y-2">
                {truthClaims.map((claim) => (
                  <div key={claim.claim_id} className="rounded border border-white bg-white/80 px-3 py-2">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-semibold">{formatClaimTitle(claim)}</div>
                        <div className="truncate text-xs opacity-70">{formatClaimSubtitle(claim)}</div>
                      </div>
                      <span className="shrink-0 rounded border border-current px-1.5 py-0.5 text-[11px] font-semibold">
                        {pct(claim.confidence_score)}
                      </span>
                    </div>
                    <div className="mt-1 flex flex-wrap gap-1 text-[10px]">
                      <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-emerald-700">{claim.supporting_evidence_count} supporting</span>
                      {claim.contradicting_evidence_count > 0 && (
                        <span className="rounded bg-rose-100 px-1.5 py-0.5 text-rose-700">{claim.contradicting_evidence_count} contradicting</span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
            {truthSourceNames.length > 0 && (
              <p className="mt-2 text-xs opacity-75">Sources: {truthSourceNames.map(formatSourceName).join(', ')}</p>
            )}
          </div>
        </details>
      </div>

      {/* People & Companies — contacts from all sources */}
      {showContactsCard && (
        <div className="bg-white rounded-lg border border-gray-200 p-5">
          {boardLeaders.length > 0 && (
            <div className="mb-4 rounded-lg border border-slate-200 bg-slate-50 p-4">
              <h3 className="text-sm font-semibold text-slate-900 mb-3">Board Leadership</h3>
              <div className="space-y-2">
                {boardLeaders.map((leader: BuildingContactEntry, idx: number) => (
                  <div key={`${leader.name}-${idx}`} className="rounded border border-slate-200 bg-white px-3 py-2">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-slate-900">{leader.name}</span>
                      {leader.confidence_hint === 'Likely board member (resident)' ? (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-100 text-green-700">Resident board officer</span>
                      ) : (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-700">DOS Chairman</span>
                      )}
                    </div>
                    <div className="text-xs text-slate-600 mt-1">
                      {leader.source_url ? (
                        <a href={leader.source_url} target="_blank" rel="noopener noreferrer" className="hover:underline">
                          {leader.source}
                        </a>
                      ) : leader.source}
                      {' '}• Filed/Published: {formatAbsoluteDate(leader.filing_date || leader.snapshot_as_of || leader.publication_date || leader.as_of_date)}
                    </div>
                    {leader.address && <div className="text-xs text-slate-500">{leader.address}</div>}
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-semibold text-gray-900">People & Companies</h2>
            <span className="text-xs text-gray-400">{contactCount} contacts from {new Set((building.all_contacts || []).map((c: BuildingContactEntry) => c.source)).size} sources</span>
          </div>
          {dosBanner && (
            <div className={`mb-3 rounded border px-2 py-2 ${dosBanner.tone}`}>
              <div className="flex items-center justify-between gap-3">
                <div className="text-xs">
                  {dosBanner.text}
                  {building.dos_contacts_last_refreshed_at ? ` Last refresh: ${formatRelativeDate(building.dos_contacts_last_refreshed_at)}.` : ''}
                </div>
                {(dosStatus === 'stale' || dosStatus === 'not_loaded') && building.corporate_owner && (
                  <button
                    type="button"
                    onClick={handleRefreshDosContacts}
                    disabled={isRefreshingDos}
                    className="shrink-0 rounded border border-current px-2 py-1 text-[11px] font-medium disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {isRefreshingDos ? 'Requesting...' : dosStatus === 'not_loaded' ? 'Preview DOS contacts refresh' : 'Preview DOS contacts refresh'}
                  </button>
                )}
              </div>
            </div>
          )}
          {building.management_company && (
            <div className="mb-3 text-sm"><span className="text-gray-500">Managing Agent:</span> <span className="font-medium text-gray-900">{building.management_company}</span></div>
          )}
          {building.corporate_owner && (
            <div className="mb-3 text-sm"><span className="text-gray-500">Corporate Owner:</span> <span className="font-medium text-gray-900">{building.corporate_owner}</span></div>
          )}
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Name</th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Role</th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Source</th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Updated</th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Address</th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Safe Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {sortedContacts.map((contact: BuildingContactEntry, i: number) => {
                  const contactAssessment = assessContactConfidence(contact);
                  return (
                  <tr key={i} className={contact.is_decision_maker ? 'border-l-4 border-l-green-400 bg-green-50/40' : ''}>
                    <td className="px-3 py-2 font-medium text-gray-900 whitespace-nowrap">
                      <button
                        className="hover:underline"
                        onClick={() => {
                          if (navigator.clipboard?.writeText) {
                            navigator.clipboard.writeText(contact.name).catch(() => undefined);
                            toast.success('Copied name');
                          }
                        }}
                      >
                        {contact.is_decision_maker ? '★ ' : ''}{contact.name}
                      </button>
                      {contact.board_role && (
                        <span className="ml-2 px-1.5 py-0.5 rounded bg-purple-50 text-purple-700 text-[10px]">
                          {contact.board_role}
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-gray-600">{contact.role}</td>
                    <td className="px-3 py-2">
                      {contact.source_url ? (
                        <a
                          href={contact.source_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className={`text-xs px-2 py-0.5 rounded hover:underline ${
                            contact.source === 'NY DOS Filing' ? 'bg-blue-50 text-blue-700' :
                            contact.source === 'NY DOS Snapshot' ? 'bg-indigo-50 text-indigo-700' :
                            'bg-gray-100 text-gray-600'
                          }`}
                        >
                          {contact.source}
                        </a>
                      ) : (
                        <span className={`text-xs px-2 py-0.5 rounded ${
                          contact.source === 'NY DOS Filing' ? 'bg-blue-50 text-blue-700' :
                          contact.source === 'NY DOS Snapshot' ? 'bg-indigo-50 text-indigo-700' :
                          'bg-gray-100 text-gray-600'
                        }`}>{contact.source}</span>
                      )}
                    </td>
                    <td
                      className="px-3 py-2 text-gray-500 text-xs"
                      title={`Filed/Published: ${formatAbsoluteDate(contact.filing_date || contact.snapshot_as_of || contact.publication_date || contact.as_of_date)}`}
                    >
                      {formatRelativeDate(contact.filing_date || contact.snapshot_as_of || contact.publication_date || contact.as_of_date)}
                    </td>
                    <td className="px-3 py-2 text-gray-500 text-xs max-w-[200px] truncate">
                      {contact.address ? (
                        <button
                          className="hover:underline"
                          onClick={() => {
                            if (navigator.clipboard?.writeText) {
                              navigator.clipboard.writeText(contact.address || '').catch(() => undefined);
                              toast.success('Copied address');
                            }
                          }}
                        >
                          {contact.address}
                        </button>
                      ) : '--'}
                    </td>
                    <td className="px-3 py-2 text-xs">
                      <div className="flex flex-col gap-1">
                        <span
                          className={`w-fit border px-1.5 py-0.5 rounded ${contactAssessment.toneClass}`}
                          title={`${contactAssessment.label}: ${contactAssessment.rationale}`}
                        >
                          {contactAssessment.safeAction}
                        </span>
                        {contact.confidence_hint && (
                          <span className={`w-fit px-1.5 py-0.5 rounded ${
                            contact.confidence_hint === 'Likely board member (resident)' ? 'bg-green-100 text-green-700' : 'bg-gray-200 text-gray-600'
                          }`}>
                            {contact.confidence_hint}
                          </span>
                        )}
                      </div>
                    </td>
                  </tr>
                  );
                })}
                {sortedContacts.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-3 py-4 text-sm text-gray-400">
                      No contact evidence is currently attached to this building.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Score Breakdown */}
        <div className="lg:col-span-2 bg-white rounded-lg border border-gray-200 p-5">
          <h2 className="font-semibold text-gray-900 mb-4">Churn Score Breakdown</h2>
          <p className="text-sm text-gray-500 mb-4">
            `Signal score` shows how strong each underlying issue is on a 0-100 scale. `Weight share` is the configured importance of that signal, and `score impact` is how many final churn-score points it adds after weighting.
          </p>
          {breakdown ? (
            <div className="space-y-3">
              {activeBreakdownEntries.length > 0 ? activeBreakdownEntries.map(([key, val]) => {
                const raw = val.raw;
                const contribution = val.contribution;
                const adjustedWeightLabel = val.effective_weight !== val.weight ? `${val.weight}% -> ${val.effective_weight}%` : `${val.weight}%`;
                return (
                  <div key={key} className="rounded-lg border border-gray-200 p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className="text-sm font-medium text-gray-900">{signalLabels[key] || key}</div>
                        <div className="text-[11px] text-gray-500 mt-1">
                          {raw === null ? 'No source data available yet for this signal.' : `Signal score: ${raw.toFixed(1)} / 100`}
                        </div>
                      </div>
                      <div className="text-right">
                        <div className="text-sm font-semibold text-gray-900">+{contribution.toFixed(1)}</div>
                        <div className="text-[11px] text-gray-500">score impact</div>
                      </div>
                    </div>
                    <div className="mt-3 bg-gray-100 rounded-full h-2.5">
                      <div
                        className={`h-2.5 rounded-full ${contribution > 0 ? 'bg-red-400' : 'bg-gray-200'}`}
                        style={{ width: `${Math.min(100, Math.max(contribution, raw || 0))}%` }}
                      />
                    </div>
                    <div className="mt-2 flex items-center justify-between text-[11px] text-gray-500">
                      <span>Weight share: {adjustedWeightLabel}</span>
                      <span>{raw === null ? 'Awaiting data' : `${Math.round((contribution / 100) * 1000) / 10}% of max score`}</span>
                    </div>
                  </div>
                );
              }) : (
                <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-700">
                  No active churn drivers in this scoring run.
                </div>
              )}

              {noSignalBreakdownEntries.length > 0 && (
                <details className="rounded-lg border border-gray-200 bg-gray-50">
                  <summary className="cursor-pointer px-3 py-2 text-xs font-semibold uppercase tracking-wider text-gray-600">
                    Checked zero-signal sources ({noSignalBreakdownEntries.length})
                  </summary>
                  <div className="divide-y divide-gray-200 border-t border-gray-200">
                    {noSignalBreakdownEntries.map(([key, val]) => {
                      const adjustedWeightLabel = val.effective_weight !== val.weight ? `${val.weight}% -> ${val.effective_weight}%` : `${val.weight}%`;
                      return (
                        <div key={key} className="flex items-center justify-between gap-3 px-3 py-2 text-xs">
                          <div>
                            <div className="font-medium text-gray-700">{signalLabels[key] || key}</div>
                            <div className="text-gray-500">Checked: no churn signal detected.</div>
                          </div>
                          <div className="text-right text-gray-500">
                            <div>0 impact</div>
                            <div>Weight share: {adjustedWeightLabel}</div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </details>
              )}

              {unavailableBreakdownEntries.length > 0 && (
                <details className="rounded-lg border border-amber-200 bg-amber-50">
                  <summary className="cursor-pointer px-3 py-2 text-xs font-semibold uppercase tracking-wider text-amber-700">
                    Awaiting source data ({unavailableBreakdownEntries.length})
                  </summary>
                  <div className="divide-y divide-amber-200 border-t border-amber-200">
                    {unavailableBreakdownEntries.map(([key, val]) => (
                      <div key={key} className="flex items-center justify-between gap-3 px-3 py-2 text-xs">
                        <div>
                          <div className="font-medium text-amber-800">{signalLabels[key] || key}</div>
                          <div className="text-amber-700">No source data available yet for this signal.</div>
                        </div>
                        <div className="text-right text-amber-700">
                          <div>Awaiting data</div>
                          <div>Base weight: {val.weight}%</div>
                        </div>
                      </div>
                    ))}
                  </div>
                </details>
              )}
            </div>
          ) : (
            <div className="text-gray-400 text-sm">No score breakdown available</div>
          )}
        </div>

        {/* Building Info */}
        <div className="bg-white rounded-lg border border-gray-200 p-5">
          <h2 className="font-semibold text-gray-900 mb-4">Details</h2>
          <dl className="space-y-2 text-sm">
            {[
              ['BBL', building.bbl],
              ['BIN', building.bin],
              ['ZIP', building.zip_code],
              ['Building Class', building.building_class],
              ['Assessed Value', building.assessed_value ? `$${building.assessed_value.toLocaleString()}` : null],
              ['Council District', building.council_district],
              ['Community Board', building.community_board],
              ['Census Tract', building.census_tract],
              ['NTA', building.nta],
              ['Key Signal', building.key_signal ? (signalLabels[building.key_signal] || building.key_signal.replace(/_/g, ' ')) : null],
              ['Signal Coverage', building.coverage_ratio !== null && building.coverage_ratio !== undefined ? `${(building.coverage_ratio * 100).toFixed(0)}%` : null],
              ['Outreach Status', building.outreach_status],
            ].filter(([, v]) => v).map(([label, value]) => (
              <div key={label as string} className="flex justify-between">
                <dt className="text-gray-500">{label}</dt>
                <dd className="font-medium text-gray-900">{value}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>

      <div className="bg-white rounded-lg border border-gray-200 p-5">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="font-semibold text-gray-900">Building Map</h2>
            <p className="text-sm text-gray-500 mt-1">Map the current building record in the same shared map component used elsewhere in the app.</p>
          </div>
        </div>
        <Suspense fallback={<div className="h-[280px] bg-gray-100 rounded-lg animate-pulse" />}>
          <PortfolioMap
            buildings={[{
              address: building.address || building.bbl,
              borough: building.borough || undefined,
              latitude: building.latitude ?? null,
              longitude: building.longitude ?? null,
              coordinate_source: building.coordinate_source ?? null,
              coordinate_precision: building.coordinate_precision ?? null,
            }]}
            boro={building.borough || undefined}
            height="280px"
          />
        </Suspense>
      </div>

      {/* Score History Chart */}
      {chartData.length > 1 && (
        <div className="bg-white rounded-lg border border-gray-200 p-5">
          <h2 className="font-semibold text-gray-900 mb-4">Score Trend</h2>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="date" tick={{ fontSize: 11 }} />
              <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} />
              <Tooltip />
              <Line type="monotone" dataKey="score" stroke="#ef4444" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Outreach Pipeline */}
      <div className="bg-white rounded-lg border border-gray-200 p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-semibold text-gray-900">Outreach Pipeline</h2>
          <div className="flex gap-2">
            {['contacted', 'meeting', 'won', 'lost'].map(stage => (
              <button
                key={stage}
                onClick={() => handleLogOutreach(stage)}
                className="px-3 py-1 text-xs border border-gray-300 rounded-lg hover:bg-gray-50 capitalize"
              >
                {stage}
              </button>
            ))}
          </div>
        </div>
        {outreachData?.events && outreachData.events.length > 0 ? (
          <div className="space-y-2 max-h-48 overflow-y-auto">
            {outreachData.events.map((evt) => (
              <div key={evt.id} className="flex items-center gap-3 py-2 border-b border-gray-50 last:border-0 text-sm">
                <span className="px-2 py-0.5 bg-blue-100 text-blue-700 rounded text-xs font-medium capitalize">{evt.stage}</span>
                <span className="text-gray-500">{evt.method || '--'}</span>
                <span className="text-gray-400">{evt.event_timestamp ? new Date(evt.event_timestamp).toLocaleDateString() : '--'}</span>
                {evt.notes && <span className="text-gray-600 truncate">{evt.notes}</span>}
              </div>
            ))}
          </div>
        ) : (
          <div className="text-gray-400 text-sm">No outreach events yet</div>
        )}
      </div>

      {/* Timeline */}
      {timeline && timeline.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 p-5">
          <h2 className="font-semibold text-gray-900 mb-4">Event Timeline</h2>
          <div className="space-y-2 max-h-96 overflow-y-auto">
            {timeline.map((evt, i) => (
              <div key={i} className="flex items-start gap-3 py-2 border-b border-gray-50 last:border-0">
                <div className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0 ${
                  evt.type === 'complaint' ? 'bg-red-100 text-red-600' :
                  evt.type === 'violation' ? 'bg-orange-100 text-orange-600' :
                  evt.type === 'transaction' ? 'bg-blue-100 text-blue-600' :
                  evt.type === 'permit' ? 'bg-green-100 text-green-600' :
                  evt.type === 'litigation' ? 'bg-purple-100 text-purple-600' :
                  evt.type === 'emergency_repair' ? 'bg-rose-100 text-rose-700' :
                  evt.type === 'eviction' ? 'bg-fuchsia-100 text-fuchsia-700' :
                  evt.type === 'energy' ? 'bg-emerald-100 text-emerald-700' :
                  evt.type === 'facade' ? 'bg-cyan-100 text-cyan-700' :
                  'bg-indigo-100 text-indigo-700'
                }`}>
                  {eventIcons[evt.type] || '?'}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium text-gray-700 uppercase">{evt.type}</span>
                    <span className="text-xs text-gray-400">{evt.date || '--'}</span>
                  </div>
                  <p className="text-sm text-gray-600 truncate">{evt.detail || 'No details'}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

export default BuildingDetailPage;
