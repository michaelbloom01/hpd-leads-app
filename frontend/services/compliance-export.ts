import type { ComplianceResponse } from './compliance-api';

function csvCell(value: unknown): string {
  let text = value == null ? '' : String(value);
  // Spreadsheet formulas are source text, never executable export content.
  if (/^[\s]*[=+@-]/.test(text) || /^[\t\r\n]/.test(text)) text = `'${text}`;
  return `"${text.replace(/"/g, '""')}"`;
}

export function complianceCsv(data: ComplianceResponse): string {
  const complaintColumns = ['complaint_category', 'complaint_category_label', 'inspection_date', 'disposition_date', 'disposition_code', 'disposition_code_label', 'date_parse_warnings', 'category_codebook_url', 'category_codebook_revision', 'disposition_codebook_url', 'disposition_codebook_revision'];
  const columns = ['row_type', 'bbl', 'bin', 'address', 'source_system', 'source_record_key', 'record_type', 'status', 'issue_or_received_date', 'description', 'reported_balance_cents', 'balance_scope', 'source_updated_at', 'observed_at', 'source_url', 'qualification', ...complaintColumns];
  const rows: unknown[][] = [[
    'scope', '', '', '', '', data.scope.id, data.scope.type, data.coverage.status, '', '', '', '', data.source_updated_at, data.as_of, '',
    `Known physical buildings: ${data.coverage.physical_building_count}; checked: ${data.coverage.checked_building_count}; amounts missing: ${data.coverage.missing_balance_bin_count}. ${data.warnings.join(' ')}`,
  ]];
  for (const building of data.buildings) {
    const balance = building.balance_observations[0];
    rows.push(['building_balance', building.bbl, building.bin, building.address, 'dob_unpaid_violations', '', '', '', '', '', building.reported_balance_cents, 'bin_category:LL152', balance?.source_updated_at, balance?.observed_at, balance?.source_url,
      `${building.reported_balance_cents == null ? 'Balance unknown.' : `Reported BIN/category balance counted once. Basis: ${balance?.amount_basis || 'unverified'}. ${balance?.stale ? 'Historical/stale balance; refresh needed.' : 'Dated manual portal observation.'}`} Interest and lien status unverified.`]);
    for (const record of building.records) rows.push(['source_record', building.bbl, building.bin, building.address, record.source_system, record.source_record_key, record.record_type, record.status,
      record.received_date || record.issue_date, record.description, '', '', record.source_updated_at, record.observed_at, record.source_url,
      `${record.identity_status}; ${record.stale ? 'Historical/stale evidence.' : 'Dated source observation.'} Same-building context does not establish causation.`,
      ...complaintColumns.map(key => key === 'date_parse_warnings' ? record.date_parse_warnings?.join('; ') : record[key as keyof typeof record])]);
  }
  return '\uFEFF' + [columns, ...rows].map(row => columns.map((_, index) => csvCell(row[index])).join(',')).join('\r\n');
}

export function downloadComplianceCsv(data: ComplianceResponse): void {
  const url = URL.createObjectURL(new Blob([complianceCsv(data)], { type: 'text/csv;charset=utf-8;' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = `compliance-${data.scope.type}-${data.scope.id.replace(/[^a-zA-Z0-9_-]/g, '_')}.csv`;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
