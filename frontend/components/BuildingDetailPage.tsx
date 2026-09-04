/**
 * BuildingDetailPage — Deep dive into a single building's churn signals.
 */
import React, { lazy, Suspense } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useParams, useNavigate } from 'react-router-dom';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';
import {
  fetchBuildingDetail, fetchBuildingTimeline, fetchBuildingScoreHistory,
  addBuildingToPipeline, fetchBuildingOutreachEvents, logBuildingOutreachEvent,
  type BuildingContactEntry, type BuildingDetail,
} from '../services/buildings-api';
import { fetchSubjectTruthSummary } from '../services/truth-api';
import {
  formatClaimSubtitle,
  formatClaimTitle,
  formatSourceName,
} from '../utils/truthDisplay';
import { toast } from 'react-hot-toast';
import CompliancePanel from './CompliancePanel';

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

const normalizedContactName = (value: string | null | undefined): string =>
  (value || '').toUpperCase().replace(/[^A-Z0-9]/g, '');

const contactEvidenceDate = (contact: BuildingContactEntry): string | null | undefined =>
  contact.filing_date || contact.snapshot_as_of || contact.publication_date || contact.source_observed_at || contact.as_of_date;

const contactDateLabel = (contact: BuildingContactEntry): string =>
  contact.filing_date ? 'Filed' : contact.snapshot_as_of ? 'Snapshot' : contact.publication_date ? 'Published' : 'Stored';

const boardRoleLabel = (contact: BuildingContactEntry): string =>
  contact.board_role_status === 'verified' && contact.board_role
    ? contact.board_role
    : `${contact.board_role || 'Corporate officer'} candidate`;

const ContactSourceLine: React.FC<{ contact: BuildingContactEntry }> = ({ contact }) => (
  <div className="mt-1 text-xs text-gray-500">
    {contact.source_url ? (
      <a href={contact.source_url} target="_blank" rel="noopener noreferrer" className="font-medium text-blue-700 hover:underline">
        {contact.source}
      </a>
    ) : (
      <span className="font-medium text-gray-600">{contact.source}</span>
    )}
    {' '}· {contactDateLabel(contact)} {formatAbsoluteDate(contactEvidenceDate(contact))}
  </div>
);

interface RoleLaneProps {
  title: string;
  description: string;
  children: React.ReactNode;
}

const RoleLane: React.FC<RoleLaneProps> = ({ title, description, children }) => (
  <section className="rounded-lg border border-gray-200 bg-gray-50 p-4">
    <h3 className="text-xs font-bold uppercase tracking-wider text-gray-500">{title}</h3>
    <p className="mt-1 text-xs text-gray-500">{description}</p>
    <div className="mt-3 space-y-3">{children}</div>
  </section>
);

const BuildingDetailPage: React.FC = () => {
  const { bbl: rawBbl } = useParams<{ bbl: string }>();
  const navigate = useNavigate();

  const decodedBbl = React.useMemo(() => {
    if (!rawBbl) return null;
    return decodeURIComponent(rawBbl).trim() || null;
  }, [rawBbl]);

  const { data: building, isLoading, error } = useQuery({
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
    const aTime = Date.parse(contactEvidenceDate(a) || '') || 0;
    const bTime = Date.parse(contactEvidenceDate(b) || '') || 0;
    return bTime - aTime;
  });
  const managementContacts = sortedContacts.filter(contact => {
    const role = contact.role.toLowerCase().replace(/[^a-z]/g, '');
    return ['agent', 'managingagent', 'sitemanager', 'manager'].includes(role);
  });
  // Use the source's company/person fields, including both on the same HPD Agent row.
  const managerOrganizations = managementContacts.filter((contact, index, contacts) =>
    contact.company_name && contacts.findIndex(other =>
      normalizedContactName(other.company_name) === normalizedContactName(contact.company_name)) === index,
  );
  const managerPeople = [...managementContacts].sort((a, b) => Number(Boolean(b.company_name)) - Number(Boolean(a.company_name)))
    .filter((contact, index, contacts) => contact.person_name && contacts.findIndex(other =>
      normalizedContactName(other.person_name) === normalizedContactName(contact.person_name)) === index);
  const boardPeople = sortedContacts.filter(contact => contact.board_role || (
    (contact.source === 'NY DOS Filing' || contact.source === 'NY DOS Snapshot')
    && /officer|chair|president|ceo/i.test(contact.role)
  ));
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
  const truthClaims = (truthSummary?.claims || []).filter(claim => claim.predicate !== 'exists_in_building_table');
  const conflictingClaims = truthClaims.filter(claim => claim.contradicting_evidence_count > 0);
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
        text: 'DOS contact data is stale. These names need a current source check.',
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
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <button onClick={() => navigate('/buildings')} className="text-sm text-gray-500 hover:text-gray-700 mb-2 flex items-center gap-1">
            ← Back to Buildings
          </button>
          <h1 className="break-words text-2xl font-bold text-gray-900">{building.address || building.bbl}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-gray-500">
            <span>{building.borough}</span>
            <span title="Borough-Block-Lot: NYC's unique property identifier">BBL: {building.bbl}</span>
            {building.unit_count && <span>{building.unit_count} units</span>}
            {building.year_built && <span>Built {building.year_built}</span>}
          </div>
        </div>
        <div className="flex w-full items-center justify-between gap-3 sm:w-auto sm:justify-end">
          {building.churn_score !== null ? (
            <div className="shrink-0 text-left sm:text-right">
              <div className={`text-3xl font-bold ${getChurnTone(building.churn_score)}`}>
                {building.churn_score.toFixed(1)}
              </div>
              <div className="text-[11px] text-gray-500">
                {getChurnCategoryLabel(building.churn_category)}
              </div>
            </div>
          ) : (
            <div className="shrink-0 text-left sm:text-right">
              <div className="text-2xl font-semibold text-gray-400">--</div>
              <div className="text-[11px] text-gray-500">Score pending</div>
            </div>
          )}
          <button
            onClick={handleAddToPipeline}
            className="min-w-0 flex-1 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 sm:flex-none"
          >
            Add to Pipeline
          </button>
        </div>
      </div>

      {showContactsCard && (
        <section className="rounded-lg border border-gray-200 bg-white p-5" aria-labelledby="building-roles-heading">
          <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h2 id="building-roles-heading" className="text-lg font-semibold text-gray-900">Who runs this building?</h2>
              <p className="mt-1 text-sm text-gray-500">Management and board roles stay separate. Each name remains tied to its source and date.</p>
            </div>
            <span className="text-xs text-gray-400">{contactCount} source records</span>
          </div>

          <div className="mt-4 grid gap-3 lg:grid-cols-3">
            <RoleLane title="Property manager" description="Companies listed in management roles by the source.">
              {managerOrganizations.length > 0 ? managerOrganizations.map((contact, index) => (
                <div key={`company-${index}`}>
                  <div className="font-semibold text-gray-900">{contact.company_name}</div>
                  <div className="mt-1 text-xs text-gray-600">Listed as {contact.role}</div>
                  <ContactSourceLine contact={contact} />
                </div>
              )) : (
                <p className="text-sm text-gray-600">No company named in the available management records.</p>
              )}
            </RoleLane>

            <RoleLane title="Management contacts" description="Named people, with company associations where the source records them.">
              {managerPeople.length > 0 ? managerPeople.map((contact, index) => (
                <div key={`person-${index}`}>
                  <div className="font-semibold text-gray-900">{contact.person_name}</div>
                  <div className="mt-1 text-xs text-gray-600">
                    Listed as {contact.role}{contact.company_name ? ` with ${contact.company_name} on the same record.` : '. Company association unrecorded.'}
                  </div>
                  <ContactSourceLine contact={contact} />
                </div>
              )) : (
                <p className="text-sm text-gray-600">No person named in the available management records.</p>
              )}
            </RoleLane>

            <RoleLane title="Board people" description="Board names and officer candidates, with the source role shown.">
              {boardPeople.length > 0 ? boardPeople.map((contact, index) => (
                <div key={`board-${index}`}>
                  <div className="font-semibold text-gray-900">{contact.person_name || contact.name}</div>
                  <div className="mt-1 text-xs font-medium text-indigo-700">{boardRoleLabel(contact)}</div>
                  <div className="mt-1 text-xs text-gray-600">Source role: {contact.source_title || contact.role}</div>
                  {contact.board_role_status !== 'verified' && (
                    <p className="mt-1 text-xs text-amber-700">Board role unverified.</p>
                  )}
                  <ContactSourceLine contact={contact} />
                </div>
              )) : (
                <p className="text-sm text-gray-600">No board person found in the available records.</p>
              )}
            </RoleLane>
          </div>

          {building.corporate_owner && (
            <div className="mt-3 rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-600">
              <span className="font-medium text-gray-900">Ownership entity:</span> {building.corporate_owner}
            </div>
          )}

          {dosBanner && (
            <div className={`mt-3 rounded border px-3 py-2 ${dosBanner.tone}`}>
              <div className="flex items-center justify-between gap-3">
                <div className="text-xs">
                  {dosBanner.text}
                  {building.dos_contacts_last_refreshed_at ? ` Last refresh: ${formatRelativeDate(building.dos_contacts_last_refreshed_at)}.` : ''}
                </div>
              </div>
            </div>
          )}
        </section>
      )}

      {truthClaims.length > 0 && (
        <details className="rounded-lg border border-gray-200 bg-white" aria-label="Relationship evidence">
          <summary className="cursor-pointer px-5 py-3 text-sm font-semibold text-gray-700">
            Relationship evidence ({truthClaims.length})
            {conflictingClaims.length > 0 && (
              <span className="ml-2 text-amber-700">{conflictingClaims.length} relationship{conflictingClaims.length === 1 ? '' : 's'} with conflicting sources</span>
            )}
          </summary>
          <div className="space-y-3 border-t border-gray-200 p-5">
            {truthClaims.map(claim => (
              <article key={claim.claim_id} className="text-sm">
                <h3 className="font-semibold text-gray-900">{formatClaimTitle(claim)}</h3>
                <p className="mt-1 text-xs text-gray-600">{formatClaimSubtitle(claim)}</p>
                <p className="mt-1 text-xs text-gray-500">Supporting sources: {claim.supporting_sources.map(formatSourceName).join(', ') || 'None recorded'}</p>
                {claim.contradicting_evidence_count > 0 && (
                  <p className="mt-1 text-xs text-amber-800">Conflicting sources: {claim.contradicting_sources.map(formatSourceName).join(', ') || 'Source details unavailable'}</p>
                )}
              </article>
            ))}
          </div>
        </details>
      )}

      {/* Full source records stay available without dominating the operating view. */}
      {showContactsCard && (
        <details className="rounded-lg border border-gray-200 bg-white">
          <summary className="cursor-pointer px-5 py-3 text-sm font-semibold text-gray-700">
            All source records ({contactCount})
          </summary>
          <div className="overflow-x-auto border-t border-gray-200">
            <table className="w-full text-sm">
              <thead className="border-b border-gray-200 bg-gray-50">
                <tr>
                  {['Name', 'Source role', 'Source and date', 'Address', 'Role notes'].map(label => (
                    <th key={label} className="px-3 py-2 text-left text-xs font-medium text-gray-500">{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {sortedContacts.map((contact, index) => (
                  <tr key={index}>
                    <td className="px-3 py-2 font-medium text-gray-900">
                      {contact.name}
                      {contact.person_name && contact.person_name !== contact.name && (
                        <div className="mt-1 text-xs font-normal text-gray-600">{contact.person_name}</div>
                      )}
                    </td>
                    <td className="px-3 py-2 text-gray-600">
                      {contact.role}
                      {contact.source_title && <div className="mt-1 text-xs">{contact.source_title}</div>}
                    </td>
                    <td className="px-3 py-2"><ContactSourceLine contact={contact} /></td>
                    <td className="max-w-xs px-3 py-2 text-xs text-gray-500">{contact.address || '--'}</td>
                    <td className="max-w-xs px-3 py-2 text-xs text-gray-600">
                      {contact.board_role && (
                        <p>{boardRoleLabel(contact)}{contact.board_role_status !== 'verified' ? '. Board role unverified.' : ''}</p>
                      )}
                      {contact.confidence_hint && <p className="mt-1">App interpretation: {contact.confidence_hint}</p>}
                    </td>
                  </tr>
                ))}
                {sortedContacts.length === 0 && (
                  <tr><td colSpan={5} className="px-3 py-4 text-sm text-gray-400">No contact evidence is currently attached to this building.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </details>
      )}

      <div className="rounded-xl border border-gray-200 bg-white p-4 sm:p-5">
        <CompliancePanel scope="parcels" scopeId={building.bbl} />
      </div>

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
              [/^[1-5]\d{6}$/.test(String(building.bin || '')) ? 'Saved BIN' : 'Legacy identifier (refresh pending)', building.bin],
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
            <p className="text-sm text-gray-500 mt-1">Shows the building location when stored or public geocoded coordinates are available.</p>
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
            allowClientGeocodingFallback
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
