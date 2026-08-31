import React, { useId, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '../contexts/AuthContext';
import { BALANCE_PORTAL_URL, dollarsToCents, submitBalanceEvidence, type BalanceInput } from '../services/compliance-balance-api';

export default function ComplianceBalanceCapture({ bin }: { bin: string }) {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [amount, setAmount] = useState('');
  const [sourceLabel, setSourceLabel] = useState('');
  const [observed, setObserved] = useState('');
  const [note, setNote] = useState('');
  const [reviewed, setReviewed] = useState<BalanceInput | null>(null);
  const id = useId();
  const client = useQueryClient();
  const preview = useMutation({ mutationFn: (input: BalanceInput) => submitBalanceEvidence(input), retry: false, onSuccess: (_result, input) => setReviewed(input) });
  const capture = useMutation({ mutationFn: (input: BalanceInput) => submitBalanceEvidence(input, true), retry: false, onSuccess: () => { setReviewed(null); void client.invalidateQueries({ queryKey: ['compliance'] }); } });
  if (user?.role !== 'admin') return null;
  const busy = preview.isPending || capture.isPending;
  const cents = dollarsToCents(amount);
  const date = new Date(observed);
  const valid = cents !== null && sourceLabel.trim().length > 0 && note.trim().length >= 20 && !Number.isNaN(date.getTime()) && date.getTime() <= Date.now() + 300_000;
  const edit = () => { setReviewed(null); preview.reset(); capture.reset(); };
  return <section aria-label="Capture balance evidence" className="border-t border-gray-200 pt-3">
    <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className="min-h-11 text-sm text-blue-700 underline">Record portal balance</button>
    {open ? <div className="space-y-3">
      <p className="text-xs text-gray-600">Admin evidence capture for BIN {bin}, LL152 category total. Read the <a href={BALANCE_PORTAL_URL} target="_blank" rel="noopener noreferrer" className="text-blue-700 underline">official DOB ledger</a> and record the displayed amount, including an explicit zero. This saves an internal observation and makes no payment.</p>
      <form className="space-y-3" onSubmit={event => { event.preventDefault(); if (!valid || busy || cents === null) return; preview.mutate({ bin, category: 'LL152', scope: 'bin_category', amount_cents: cents, source_url: BALANCE_PORTAL_URL, source_updated_at: null, source_timestamp_raw: sourceLabel.trim(), observed_at: date.toISOString(), evidence_note: note.trim() }); }}>
        <label htmlFor={`${id}-amount`} className="block text-xs text-gray-600">Displayed LL152 total in dollars</label>
        <input id={`${id}-amount`} value={amount} onChange={event => { edit(); setAmount(event.target.value); }} inputMode="decimal" required disabled={busy} placeholder="5000.00" className="min-h-11 w-full rounded-lg border border-gray-300 p-2 text-sm" />
        <label htmlFor={`${id}-source`} className="block text-xs text-gray-600">Source date label, copied exactly (or “No date shown”)</label>
        <input id={`${id}-source`} value={sourceLabel} onChange={event => { edit(); setSourceLabel(event.target.value); }} maxLength={200} required disabled={busy} className="min-h-11 w-full rounded-lg border border-gray-300 p-2 text-sm" />
        <label htmlFor={`${id}-observed`} className="block text-xs text-gray-600">When you checked the portal (your local time)</label>
        <input id={`${id}-observed`} type="datetime-local" value={observed} onChange={event => { edit(); setObserved(event.target.value); }} required disabled={busy} className="min-h-11 w-full rounded-lg border border-gray-300 p-2 text-sm" />
        <label htmlFor={`${id}-note`} className="block text-xs text-gray-600">Evidence note (at least 20 characters)</label>
        <textarea id={`${id}-note`} value={note} onChange={event => { edit(); setNote(event.target.value); }} minLength={20} maxLength={4000} required rows={3} disabled={busy} className="w-full rounded-lg border border-gray-300 p-2 text-sm" />
        <button type="submit" disabled={!valid || busy} className="min-h-11 rounded-lg border border-blue-600 px-3 text-sm text-blue-700 disabled:opacity-50">{preview.isPending ? 'Checking evidence...' : 'Preview evidence'}</button>
      </form>
      {reviewed ? <div className="space-y-2 rounded-lg bg-blue-50 p-3 text-sm text-blue-950">
        <p>Ready to record ${(reviewed.amount_cents / 100).toFixed(2)} for BIN {reviewed.bin}, LL152. Observed {new Date(reviewed.observed_at).toLocaleString()}.</p>
        <p className="text-xs">The source’s exact update time remains unknown. Interest, liens and agency closure remain unverified.</p>
        <button type="button" disabled={busy} onClick={() => capture.mutate(reviewed)} className="min-h-11 rounded-lg bg-blue-700 px-3 text-sm text-white disabled:opacity-50">{capture.isPending ? 'Saving...' : 'Confirm and save observation'}</button>
      </div> : null}
      {preview.error || capture.error ? <p role="alert" className="text-sm text-amber-800">{(preview.error || capture.error)?.message}</p> : null}
      {capture.isSuccess ? <p role="status" className="text-sm text-green-800">Balance observation saved with your reviewer identity. Agency status is unchanged.</p> : null}
    </div> : null}
  </section>;
}
