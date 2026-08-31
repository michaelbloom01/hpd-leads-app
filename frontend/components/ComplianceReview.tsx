import React, { useEffect, useId, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchComplianceReviews, REVIEW_STATES, saveComplianceReview, type ReviewState } from '../services/compliance-workflow-api';

export default function ComplianceReview({ recordId }: { recordId: string }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<ReviewState>('in_review');
  const [reason, setReason] = useState('');
  const [dirty, setDirty] = useState(false);
  const [draftVersion, setDraftVersion] = useState<number | undefined>();
  const inputId = useId();
  const client = useQueryClient();
  const key = ['compliance-review', recordId];
  const query = useQuery({ queryKey: key, queryFn: ({ signal }) => fetchComplianceReviews(recordId, signal), enabled: open, retry: false, staleTime: 60_000 });
  const save = useMutation({
    mutationFn: () => saveComplianceReview(recordId, { state, reason: reason.trim(), expected_version: draftVersion! }),
    retry: false,
    onSuccess: data => { client.setQueryData(key, data); setReason(''); setDirty(false); setDraftVersion(data.version); setState(data.state); },
  });
  const savedState = query.data?.state;
  const savedVersion = query.data?.version;
  useEffect(() => { if (!dirty && savedState) { setState(savedState); setDraftVersion(savedVersion); } }, [savedVersion, savedState, dirty]);
  const changedElsewhere = dirty && savedVersion !== draftVersion;
  return <section className="mt-4 border-t border-gray-100 pt-3" aria-label="Internal record review">
    <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className="min-h-11 text-sm font-medium text-blue-700 underline underline-offset-2">Internal review</button>
    {open ? <div className="mt-2 space-y-3">
      <p className="text-xs text-gray-500">Your review is internal. Agency status, payment and legal conclusions stay separate.</p>
      {query.isLoading ? <p role="status" className="text-sm text-gray-500">Loading review history...</p> : null}
      {query.error ? <p role="alert" className="text-sm text-amber-800">{query.error.message}</p> : null}
      <button type="button" disabled={query.isFetching || save.isPending} onClick={() => { save.reset(); setReason(''); setDirty(false); void query.refetch(); }} className="min-h-11 text-xs text-blue-700 underline">Reload history and reset draft</button>
      {changedElsewhere ? <p role="alert" className="text-sm text-amber-800">A newer review arrived while you were drafting. Your draft is preserved. Copy any notes you want to keep, then reload history and reset the draft before saving.</p> : null}
      {query.data ? <>
        <p className="text-sm text-gray-700">Current internal state: {REVIEW_STATES[query.data.state]} · Version {query.data.version}</p>
        <form onSubmit={event => { event.preventDefault(); if (reason.trim().length >= 5 && draftVersion !== undefined && !changedElsewhere && !save.isPending && !query.isFetching && !query.error) save.mutate(); }} className="space-y-3">
          <label htmlFor={`${inputId}-state`} className="block text-xs text-gray-600">Internal state</label>
          <select id={`${inputId}-state`} value={state} onChange={event => { setState(event.target.value as ReviewState); setDirty(true); }} disabled={save.isPending}
            className="min-h-11 w-full rounded-lg border border-gray-300 bg-white p-2 text-sm text-gray-900">
            {Object.entries(REVIEW_STATES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          <label htmlFor={`${inputId}-reason`} className="block text-xs text-gray-600">Reason and evidence reference</label>
          <textarea id={`${inputId}-reason`} value={reason} onChange={event => { setReason(event.target.value); setDirty(true); }} minLength={5} maxLength={2000} required rows={3} disabled={save.isPending}
            placeholder="What did you verify, and which source supports it?" className="w-full rounded-lg border border-gray-300 p-2 text-sm text-gray-900" />
          <button type="submit" disabled={save.isPending || query.isFetching || draftVersion === undefined || changedElsewhere || reason.trim().length < 5 || Boolean(query.error)} className="min-h-11 rounded-lg bg-blue-700 px-3 text-sm font-medium text-white disabled:opacity-50">{save.isPending ? 'Saving...' : 'Save internal review'}</button>
        </form>
        {save.error ? <p role="alert" className="text-sm text-amber-800">{save.error.message}</p> : null}
        {save.isSuccess ? <p role="status" className="text-sm text-green-800">Internal review saved. Agency status is unchanged.</p> : null}
        {query.data.history.length ? <ol className="space-y-2 text-xs text-gray-600" aria-label="Review history">
          {query.data.history.map(item => <li key={item.id} className="rounded-lg bg-gray-50 p-3">
            <p className="font-medium text-gray-900">{REVIEW_STATES[item.state]} · Version {item.version}</p>
            <p className="mt-1 break-words whitespace-pre-wrap">{item.reason}</p>
            <p className="mt-1 break-words">{item.actor} · {new Date(item.created_at).toLocaleString()}</p>
          </li>)}
        </ol> : <p className="text-xs text-gray-500">No internal review recorded yet.</p>}
        {query.data.history.length >= query.data.history_limit ? <p className="text-xs text-gray-500">Showing the most recent {query.data.history_limit} reviews. Earlier history is retained.</p> : null}
      </> : null}
    </div> : null}
  </section>;
}
