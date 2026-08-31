import React, { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  ComplianceApiError, fetchCompliance,
  type ComplianceBuilding, type ComplianceRecord, type ComplianceScope,
} from '../services/compliance-api';
import ComplianceReview from './ComplianceReview';
import ComplianceBalanceCapture from './ComplianceBalanceCapture';
import { downloadComplianceCsv } from '../services/compliance-export';

interface Props { scope: ComplianceScope; scopeId: string }

const COVERAGE_LABELS: Record<string, string> = {
  disabled: 'Pilot paused', schema_unavailable: 'Setup required', identity_unavailable: 'Identity refresh required',
  not_checked: 'Awaiting source check', partial: 'Partial coverage', complete: 'Source check complete',
};

const UNAVAILABLE_MESSAGES: Record<string, string> = {
  disabled: 'Compliance intelligence is awaiting pilot activation. Existing company and building workflows remain available.',
  schema_unavailable: 'Compliance storage is awaiting setup. Source coverage will appear after setup and a completed ingestion.',
  identity_unavailable: 'Verified physical-building identifiers are awaiting refresh. Compliance records will be linked after the identity check.',
  not_checked: 'DOB Safety has not completed a check for this scope. Coverage and balances remain unknown.',
};

export function formatComplianceMoney(cents: number | null | undefined): string {
  if (cents == null || !Number.isSafeInteger(cents) || cents < 0) return 'Unavailable';
  return new Intl.NumberFormat('en-US', {
    style: 'currency', currency: 'USD', minimumFractionDigits: cents % 100 === 0 ? 0 : 2,
  }).format(cents / 100);
}

function dateLabel(value: string | null | undefined): string {
  if (!value) return 'Unavailable';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
}

function sourceLabel(value: string): string {
  return ({ dob_safety: 'DOB Safety', dob_complaints: 'DOB complaints', dob_violations: 'Legacy DOB violations', dob_ecb: 'DOB ECB', dob_unpaid_violations: 'DOB unpaid-violation ledger' } as Record<string, string>)[value]
    || value.replace(/_/g, ' ');
}

function balanceIsStale(building: ComplianceBuilding): boolean {
  // The API orders observations newest first. Earlier history cannot make a
  // current selected balance stale, and the client never recomputes its amount.
  return building.reported_balance_cents != null && Boolean(building.balance_observations[0]?.stale);
}

function balanceObservationDates(buildings: ComplianceBuilding[]): string {
  const dates = buildings
    .filter(building => building.reported_balance_cents != null)
    .map(building => building.balance_observations[0]?.observed_at)
    .filter((value): value is string => Boolean(value))
    .sort();
  if (dates.length === 0) return 'Unavailable';
  const first = dateLabel(dates[0]);
  const last = dateLabel(dates[dates.length - 1]);
  return first === last ? first : `${first} to ${last}`;
}

export function safeComplianceSourceUrl(value: string): string | null {
  try {
    const url = new URL(value);
    const officialHost = url.hostname === 'nyc.gov' || url.hostname.endsWith('.nyc.gov')
      || url.hostname === 'cityofnewyork.us' || url.hostname.endsWith('.cityofnewyork.us');
    return url.protocol === 'https:' && officialHost && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

function SourceLink({ url, children }: { url: string; children: React.ReactNode }) {
  const href = safeComplianceSourceUrl(url);
  return href
    ? <a className="text-blue-700 underline underline-offset-2 hover:text-blue-900" href={href} target="_blank" rel="noopener noreferrer">{children} ↗</a>
    : <span className="text-gray-500">Source link unavailable</span>;
}

function Field({ label, children, wide = false }: { label: string; children: React.ReactNode; wide?: boolean }) {
  return <div className={`min-w-0 ${wide ? 'sm:col-span-2' : ''}`}>
    <dt className="text-xs text-gray-500">{label}</dt>
    <dd className="mt-1 break-words text-sm text-gray-900">{children}</dd>
  </div>;
}

function RecordDetails({ record, evidence }: { record: ComplianceRecord; evidence: boolean }) {
  const complaint = record.record_type === 'complaint';
  const ecb = record.source_system === 'dob_ecb';
  return <article className="border-t border-gray-200 pt-4 first:border-0 first:pt-0">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h4 className="text-sm font-semibold text-gray-900">{complaint ? `Complaint: ${record.complaint_category_label || record.complaint_category || 'Category unavailable'}` : record.device_type || record.category || 'DOB compliance record'}</h4>
      <span className="rounded bg-amber-50 px-2 py-1 text-xs text-amber-800">{record.status || 'Status unavailable'} at source</span>
    </div>
    <p className="mt-2 break-all font-mono text-xs text-gray-700">{record.source_record_key}</p>
    {record.identity_status.includes('conflicting') || record.identity_status === 'unresolved'
      ? <p className="mt-2 rounded bg-amber-50 p-2 text-xs text-amber-900">Building identity requires review: {record.identity_status.replace(/_/g, ' ')}.</p> : null}
    <dl className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
      {complaint ? <>
        <Field label="Complaint received">{dateLabel(record.received_date)}</Field>
        <Field label="Complaint category code">{record.complaint_category || 'Unavailable'}</Field>
        <Field label="Inspection date">{dateLabel(record.inspection_date)}</Field>
        <Field label="Disposition date">{dateLabel(record.disposition_date)}</Field>
        <Field label="Disposition" wide>{record.disposition_code_label || 'Label unavailable'}{record.disposition_code ? ` (code ${record.disposition_code})` : ''}</Field>
      </> : <>
        <Field label="Issue date">{dateLabel(record.issue_date)}</Field>
        <Field label="Violation type">{record.violation_type || 'Unavailable'}</Field>
        {ecb ? <>
          <Field label="Served date">{dateLabel(record.served_date)}</Field>
          <Field label="Hearing date">{dateLabel(record.hearing_date)}</Field>
          <Field label="Hearing status">{record.hearing_status || 'Unavailable'}</Field>
          <Field label="Certification status">{record.certification_status || 'Unavailable'}</Field>
          <Field label="Penalty imposed">{record.penalty_imposed_cents == null ? 'Unavailable' : formatComplianceMoney(record.penalty_imposed_cents)}</Field>
          <Field label="Amount paid">{record.amount_paid_cents == null ? 'Unavailable' : formatComplianceMoney(record.amount_paid_cents)}</Field>
          <Field label="ECB balance due">{record.balance_due_cents == null ? 'Unavailable' : formatComplianceMoney(record.balance_due_cents)}</Field>
          <Field label="Portfolio subtotal">Excluded pending ECB/OATH duplicate review</Field>
        </> : null}
      </>}
      {record.description ? <Field label="Source remarks" wide>{record.description}</Field> : null}
      {evidence ? <>
        <Field label="Source updated">{dateLabel(record.source_updated_at)}</Field>
        <Field label="Observed">{dateLabel(record.observed_at)}</Field>
        <Field label="Identity match">{record.identity_status.replace(/_/g, ' ')}</Field>
        <Field label="Evidence"><SourceLink url={record.source_url}>{sourceLabel(record.source_system)} record</SourceLink></Field>
        {complaint && record.category_codebook_url ? <Field label="Category definitions"><SourceLink url={record.category_codebook_url}>DOB codebook ({record.category_codebook_revision || 'revision unavailable'})</SourceLink></Field> : null}
        {complaint && record.disposition_codebook_url ? <Field label="Disposition definitions"><SourceLink url={record.disposition_codebook_url}>DOB dispositions ({record.disposition_codebook_revision || 'revision unavailable'})</SourceLink></Field> : null}
      </> : null}
    </dl>
    {record.stale ? <p className="mt-3 text-xs text-amber-800">Historical source snapshot. Refresh needed.</p> : null}
    {complaint ? <p className="mt-3 text-xs text-gray-500">A complaint is a reported concern. Its presence on the same building does not establish that it caused a violation or remains an active condition.</p> : null}
    {record.date_parse_warnings?.length ? <p className="mt-2 text-xs text-amber-800">Source date needs review: {record.date_parse_warnings.join('; ')}</p> : null}
    <ComplianceReview recordId={record.id} />
  </article>;
}

function BuildingDetails({ building, evidence }: { building: ComplianceBuilding; evidence: boolean }) {
  const [recordFilter, setRecordFilter] = useState<'active' | 'complaint' | 'all'>('active');
  const [visibleCount, setVisibleCount] = useState(10);
  const hasBalance = building.reported_balance_cents != null;
  const staleBalance = balanceIsStale(building);
  const active = building.records.filter(record => record.status?.toLowerCase() === 'active');
  const complaints = building.records.filter(record => record.record_type === 'complaint');
  const selectedRecords = recordFilter === 'active' ? active : recordFilter === 'complaint' ? complaints : building.records;
  return <div className="space-y-4 p-4">
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <h3 className="font-semibold text-gray-900">{building.address || 'Address unavailable'}</h3>
      <span className="font-mono text-xs text-gray-500">BIN {building.bin}</span>
    </div>
    {building.hpd_registration ? <p className={`rounded-lg p-3 text-xs ${building.hpd_registration.status === 'expired' ? 'bg-amber-50 text-amber-900' : 'bg-gray-50 text-gray-600'}`}>
      HPD registration {building.hpd_registration.registration_id} · Last registered {dateLabel(building.hpd_registration.last_registration_date)} · {building.hpd_registration.status === 'expired' ? 'Expired' : 'Expires'} {dateLabel(building.hpd_registration.registration_end_date)}.
      {' '}<SourceLink url={building.hpd_registration.source_url}>HPD identity evidence</SourceLink>
      {building.hpd_registration.status === 'expired' ? ' Registration details require currentness review.' : ''}
      {building.hpd_registration.status === 'conflicting_current_records' ? ' Multiple current HPD records require identity review.' : ''}
    </p> : null}
    <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <Field label={staleBalance ? 'Last reported balance' : 'Reported unpaid balance'}>
        {hasBalance ? formatComplianceMoney(building.reported_balance_cents) : 'Balance unavailable'}
      </Field>
      <Field label="Balance scope">{hasBalance ? 'BIN/category observations' : 'No balance evidence loaded'}</Field>
      <Field label="Interest">Unverified</Field>
      <Field label="Lien status">Unverified</Field>
    </dl>
    {hasBalance ? <p className="text-xs text-gray-500">Payment and compliance closure are tracked separately.{staleBalance ? ' Balance refresh needed.' : ''}</p> : null}
    <ComplianceBalanceCapture key={building.bin} bin={building.bin} />
    {building.source_checks?.length ? <section aria-label="Building source checks" className="space-y-2 rounded-lg bg-gray-50 p-3">
      {building.source_checks.map(check => <p key={check.source_system} className="text-xs text-gray-600">
        <span className="font-medium">{sourceLabel(check.source_system)}</span>: {check.status === 'checked' ? `${check.records_count} records in completed check` : 'Awaiting source check'} · Observed {dateLabel(check.observed_at)}{check.stale ? ' · Refresh needed' : ''}
      </p>)}
    </section> : null}
    {building.records.length > 0 ? <div className="space-y-4 border-t border-gray-200 pt-4">
      <div className="flex flex-wrap gap-2" aria-label="Filter saved compliance records">
        {([
          ['active', `Active at source (${active.length})`],
          ['complaint', `Complaint history (${complaints.length})`],
          ['all', `All records (${building.records.length})`],
        ] as const).map(([filter, label]) => <button key={filter} type="button" aria-pressed={recordFilter === filter}
          onClick={() => { setRecordFilter(filter); setVisibleCount(10); }}
          className={`min-h-11 rounded-lg border px-3 text-xs ${recordFilter === filter ? 'border-blue-500 bg-blue-50 text-blue-800' : 'border-gray-200 text-gray-600'}`}>{label}</button>)}
      </div>
      {selectedRecords.slice(0, visibleCount).map(record => <RecordDetails key={record.id} record={record} evidence={evidence} />)}
      {selectedRecords.length === 0 ? <p className="text-sm text-gray-500">No saved records in this filter. Check source coverage and the other record views separately.</p> : null}
      {selectedRecords.length > visibleCount ? <button type="button" onClick={() => setVisibleCount(count => count + 10)} className="min-h-11 text-sm text-blue-700 underline">Show more records ({selectedRecords.length - visibleCount} remaining)</button> : null}
    </div> : <p className="rounded-lg bg-gray-50 p-3 text-sm text-gray-600">
      {building.source_check_status === 'checked'
        ? 'No DOB Safety records in the completed source check. Other compliance sources have separate coverage.'
        : 'DOB Safety has not completed a source check for this physical building.'}
    </p>}
    {evidence ? <section className="space-y-3 border-t border-gray-200 pt-4" aria-label="Balance source evidence">
      <h4 className="text-sm font-semibold text-gray-900">Balance source evidence</h4>
      {building.balance_observations.length > 0 ? building.balance_observations.map(observation => <article key={observation.id} className="rounded-lg bg-gray-50 p-3 text-xs text-gray-600">
        <p className="font-medium text-gray-900">{observation.stale ? 'Last portal-observed balance' : 'Portal-observed balance'}: {formatComplianceMoney(observation.amount_cents)} · {observation.category}</p>
        <p className="mt-1">BIN {observation.bin} · BIN/category total · Checked {dateLabel(observation.observed_at)}</p>
        <p className="mt-1">Reviewer: {observation.reviewer}</p>
        <p className="mt-1">Source updated: {dateLabel(observation.source_updated_at)}</p>
        {observation.source_timestamp_raw ? <p className="mt-1 break-words">Source display label: {observation.source_timestamp_raw}</p> : null}
        <p className="mt-2"><SourceLink url={observation.source_url}>Official balance source</SourceLink></p>
        {observation.stale ? <p className="mt-2 text-amber-800">Refresh needed.</p> : null}
      </article>) : <p className="text-sm text-gray-500">DOB Safety records do not include unpaid balances. A separately dated ledger observation is required.</p>}
    </section> : null}
  </div>;
}

function ParcelGroup({ bbl, buildings }: { bbl: string | null; buildings: ComplianceBuilding[] }) {
  const [selectedBin, setSelectedBin] = useState(buildings[0]?.bin);
  const [evidence, setEvidence] = useState(false);
  const selected = buildings.find(building => building.bin === selectedBin) || buildings[0];
  if (!selected) return null;
  return <section className="overflow-hidden rounded-xl border border-gray-200 bg-white" aria-label={`Compliance parcel ${bbl || 'unverified'}`}>
    <div className="border-b border-gray-200 p-4">
      <h3 className="text-sm font-semibold text-gray-900">{bbl ? `BBL ${bbl}` : 'Parcel identity unverified'}</h3>
      <p className="mt-1 text-xs text-gray-500">{buildings.length} physical building{buildings.length === 1 ? '' : 's'}{buildings.length > 1 ? ' on this shared tax lot' : ''}</p>
    </div>
    <div className="grid gap-2 p-4" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 160px), 1fr))' }} aria-label="Select physical building">
      {buildings.map(building => <button
        key={building.bin} type="button" aria-pressed={building.bin === selected.bin}
        aria-label={`Inspect ${building.address || `BIN ${building.bin}`}`}
        onClick={() => setSelectedBin(building.bin)}
        className={`min-w-0 rounded-lg border p-3 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 ${building.bin === selected.bin ? 'border-blue-500 bg-blue-50' : 'border-gray-200 bg-white hover:bg-gray-50'}`}
      >
        <span className="block break-words text-sm font-medium text-gray-900">{building.address || 'Address unavailable'}</span>
        <span className="mt-1 block break-all font-mono text-xs text-gray-500">BIN {building.bin}</span>
        <span className="mt-2 block break-words text-sm font-semibold text-gray-900 [overflow-wrap:anywhere]">{building.reported_balance_cents == null ? 'Balance unavailable' : formatComplianceMoney(building.reported_balance_cents)}</span>
        {balanceIsStale(building) ? <span className="mt-1 block text-xs text-amber-800">Last reported. Refresh needed.</span> : null}
      </button>)}
    </div>
    <div className="flex gap-5 border-y border-gray-200 bg-gray-50 px-4" aria-label="Compliance case view">
      {[false, true].map(showEvidence => <button key={String(showEvidence)} type="button" aria-pressed={evidence === showEvidence}
        onClick={() => setEvidence(showEvidence)}
        className={`min-h-11 border-b-2 text-sm ${evidence === showEvidence ? 'border-blue-600 font-medium text-blue-700' : 'border-transparent text-gray-600 hover:text-gray-900'}`}
      >{showEvidence ? 'Source evidence' : 'Case details'}</button>)}
    </div>
    <BuildingDetails key={selected.bin} building={selected} evidence={evidence} />
  </section>;
}

const CompliancePanel: React.FC<Props> = ({ scope, scopeId }) => {
  const query = useQuery({
    queryKey: ['compliance', scope, scopeId],
    queryFn: ({ signal }) => fetchCompliance(scope, scopeId, signal),
    enabled: Boolean(scopeId), retry: false, staleTime: 60_000,
  });
  const data = query.data;
  const groups = useMemo(() => {
    const grouped = new Map<string, { bbl: string | null; buildings: ComplianceBuilding[] }>();
    for (const building of data?.buildings || []) {
      const key = building.bbl || `unverified-${building.bin}`;
      const group = grouped.get(key) || { bbl: building.bbl, buildings: [] };
      group.buildings.push(building);
      grouped.set(key, group);
    }
    return [...grouped.entries()];
  }, [data?.buildings]);
  const coverageStatus = data?.coverage.status || 'not_checked';
  const staleBalance = data?.buildings.some(balanceIsStale);
  const sourceNeedsRefresh = (sourceSystem?: string) => {
    const checks = data?.buildings.flatMap(building => building.source_checks || [])
      .filter(check => !sourceSystem || check.source_system === sourceSystem) || [];
    if (checks.length > 0) return checks.some(check => check.stale);
    return Boolean(!data?.coverage.unmapped_parcel_count && (sourceSystem
      ? data?.source_coverage?.find(source => source.source_system === sourceSystem)?.stale
      : data?.stale));
  };
  const unavailableMessage = data && (!data.enabled || ['schema_unavailable', 'identity_unavailable'].includes(coverageStatus))
    ? UNAVAILABLE_MESSAGES[data.enabled ? coverageStatus : 'disabled'] : null;

  return <section className="min-w-0 space-y-4" aria-label="Compliance intelligence">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 className="text-lg font-semibold text-gray-900">Compliance intelligence</h2>
        <p className="mt-1 text-sm text-gray-500">DOB pilot · Violations, complaints and source evidence</p>
      </div>
      <div className="flex flex-wrap gap-2">
      {data?.enabled && data.identity_ready && data.buildings.length > 0 ? <button type="button" disabled={Boolean(query.error) || query.isFetching} onClick={() => downloadComplianceCsv(data)} className="min-h-11 rounded-lg border border-gray-200 px-3 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50">Export evidence CSV</button> : null}
      <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}
        className="min-h-11 rounded-lg border border-gray-200 px-3 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
      >{query.isFetching ? 'Loading…' : 'Reload saved data'}</button>
      </div>
    </div>
    {query.isLoading ? <p role="status" className="rounded-lg bg-gray-50 p-4 text-sm text-gray-600">Loading compliance evidence…</p> : null}
    {query.error ? <p role="alert" className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
      {query.error instanceof ComplianceApiError ? query.error.message : 'Compliance data is temporarily unavailable. Try reloading saved data.'}
      {' '}Coverage and balances remain unverified.{data ? ' Previously loaded evidence is shown below.' : ''}
    </p> : null}
    {data ? <>
      <div className="flex flex-wrap gap-2 text-xs">
        <span className={`rounded border px-2 py-1 ${coverageStatus === 'complete' && !data.stale ? 'border-gray-200 bg-gray-50 text-gray-700' : 'border-amber-200 bg-amber-50 text-amber-900'}`}>{COVERAGE_LABELS[coverageStatus] || 'Coverage unverified'}</span>
        {data.enabled && data.identity_ready && data.coverage.unmapped_parcel_count ? <span className="rounded border border-amber-200 bg-amber-50 px-2 py-1 text-amber-900">More identities to map</span> : null}
        {data.enabled && data.identity_ready && sourceNeedsRefresh() ? <span className="rounded border border-amber-200 bg-amber-50 px-2 py-1 text-amber-900">Source refresh needed</span> : null}
      </div>
      {unavailableMessage ? <p className="rounded-lg bg-gray-50 p-4 text-sm text-gray-600">{unavailableMessage}</p> : <>
        {data.coverage.scope_parcel_count !== undefined ? <p className={`rounded-lg p-3 text-sm ${data.coverage.unmapped_parcel_count ? 'bg-amber-50 text-amber-900' : 'bg-gray-50 text-gray-600'}`}>
          Physical-building identities mapped for {data.coverage.mapped_parcel_count} of {data.coverage.scope_parcel_count} parcels in this scope.
          {data.coverage.unmapped_parcel_count ? ` ${data.coverage.unmapped_parcel_count} parcels remain outside the identity pilot. Counts below cover mapped buildings only.` : ''}
        </p> : null}
        <div className="flex flex-wrap items-start justify-between gap-4 rounded-xl border border-gray-200 bg-white p-4">
          <div>
            <p className="text-sm text-gray-500">{staleBalance ? 'Last reported balance' : 'Reported unpaid balance'}{data.coverage.missing_balance_bin_count > 0 && data.reported_balance_cents != null ? ' · Known subtotal' : ''}</p>
            <p className="mt-1 text-2xl font-semibold tracking-tight text-gray-900">{formatComplianceMoney(data.reported_balance_cents)}</p>
            <p className="mt-1 text-xs text-gray-500">Balance observations: {data.coverage.balance_known_building_count} of {data.coverage.physical_building_count} physical buildings</p>
            {data.reported_balance_cents != null ? <p className="mt-1 text-xs text-gray-500">Balance evidence checked: {balanceObservationDates(data.buildings)}</p> : null}
            {data.coverage.missing_balance_bin_count > 0 ? <p className="mt-1 text-xs text-amber-800">{data.coverage.missing_balance_bin_count} building balance{data.coverage.missing_balance_bin_count === 1 ? '' : 's'} unavailable</p> : null}
            {data.estimated_penalty_cents != null ? <p className="mt-2 text-xs text-gray-600">Estimated face penalty: {formatComplianceMoney(data.estimated_penalty_cents)}. Separate from reported balance.</p> : null}
          </div>
          <div className="text-sm text-gray-700">
            <p>{data.coverage.checked_building_count} of {data.coverage.physical_building_count} physical buildings checked</p>
            <p className="mt-1">{data.coverage.active_records_count} active source records · {data.coverage.records_count} total source records</p>
            {data.coverage.complaints_count !== undefined ? <p className="mt-1">{data.coverage.open_complaints_count} open complaints · {data.coverage.complaints_count} complaint records</p> : null}
            <p className="mt-2 text-xs text-gray-500">Source updated: {dateLabel(data.source_updated_at)}</p>
            <p className="mt-1 text-xs text-gray-500">Latest DOB check observed: {dateLabel(data.as_of)}</p>
          </div>
        </div>
        {data.source_coverage?.length ? <section aria-label="Source-specific coverage" className="grid gap-3 sm:grid-cols-2">
          {data.source_coverage.map(source => <div key={source.source_system} className="rounded-lg border border-gray-200 p-3 text-xs text-gray-600">
            <p className="font-semibold text-gray-900">{sourceLabel(source.source_system)}</p>
            <p className="mt-1">{source.checked_building_count} of {source.physical_building_count} mapped buildings checked · {source.records_count} records</p>
            <p className="mt-1">{COVERAGE_LABELS[source.status] || source.status.replace(/_/g, ' ')}{data.coverage.unmapped_parcel_count ? ' · Scope incomplete' : ''}{sourceNeedsRefresh(source.source_system) ? ' · Refresh needed' : ''}</p>
            <p className="mt-1">Source updated {dateLabel(source.source_updated_at)} · Observed {dateLabel(source.observed_at)}</p>
          </div>)}
        </section> : null}
        {coverageStatus === 'not_checked' ? <p className="text-sm text-gray-600">{UNAVAILABLE_MESSAGES.not_checked}</p> : null}
        {groups.length > 0 ? groups.map(([key, group]) => <ParcelGroup key={key} {...group} />)
          : <p className="rounded-lg bg-gray-50 p-4 text-sm text-gray-600">No verified physical buildings are available in this scope. A completed identity and source check is required.</p>}
      </>}
      {data.warnings.length > 0 ? <details className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
        <summary className="cursor-pointer font-medium">Coverage notes ({data.warnings.length})</summary>
        <ul className="mt-2 list-disc space-y-1 pl-5">{data.warnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}</ul>
      </details> : null}
      {data.provenance.length > 0 ? <details className="rounded-lg border border-gray-200 p-3 text-sm">
        <summary className="cursor-pointer font-medium text-gray-700">Source coverage and dates</summary>
        <div className="mt-3 space-y-3">{data.provenance.map((source, index) => <div key={`${source.source_system}-${index}`} className="text-xs text-gray-500">
          <SourceLink url={source.source_url}>{sourceLabel(source.source_system)}</SourceLink>
          <p className="mt-1">{source.status.replace(/_/g, ' ')} · Source updated {dateLabel(source.source_updated_at)} · Observed {dateLabel(source.observed_at)}</p>
        </div>)}</div>
      </details> : null}
    </> : null}
    <p className="text-xs text-gray-500">Source status, payment, and compliance closure are separate. Interest and lien status require specific evidence. Reloading reads saved evidence; it does not start a source refresh.</p>
  </section>;
};

export default CompliancePanel;
