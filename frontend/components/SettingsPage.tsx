/**
 * SettingsPage — Scoring Weights + Data Health Dashboard.
 * Two sub-sections:
 *  1. Scoring Configuration: manage presets, custom configs, weights
 *  2. Data Health: ingestion freshness, match rates, coverage
 */
import React, { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'react-hot-toast';
import {
  fetchConfigs, fetchActiveConfig, activateConfig, triggerRecalculate,
  createConfig,
  type ScoringWeights,
} from '../services/scoring-api';
import { fetchQualitySummary, fetchCoverage, fetchSourceAudit } from '../services/quality-api';
import { fetchJobs, fetchJobsSummary, startJob } from '../services/jobs-api';

const SIGNAL_LABELS: Record<string, string> = {
  ownership_change: 'Ownership Change',
  complaint_spike: 'Complaint Spike',
  violation_trend: 'Violation Trend',
  energy_grade_drop: 'Energy Grade Drop',
  dob_permits: 'DOB Permits',
  hpd_litigation: 'Housing Litigation',
  emergency_repairs: 'Emergency Repairs',
  building_size: 'Building Size',
  eviction_activity: 'Eviction Activity',
  facade_status: 'Facade Status',
};

const DEFAULT_WEIGHTS: ScoringWeights = {
  ownership_change: 15, complaint_spike: 12, violation_trend: 12,
  energy_grade_drop: 8, dob_permits: 8, hpd_litigation: 12,
  emergency_repairs: 10, building_size: 8, eviction_activity: 8, facade_status: 7,
};

const ScoringSection: React.FC = () => {
  const queryClient = useQueryClient();
  const { data: configs } = useQuery({ queryKey: ['scoring-configs'], queryFn: fetchConfigs });
  const { data: activeConfig } = useQuery({ queryKey: ['active-config'], queryFn: fetchActiveConfig });
  const [editWeights, setEditWeights] = useState<ScoringWeights>(DEFAULT_WEIGHTS);
  const [editName, setEditName] = useState('');
  const [selectedConfig, setSelectedConfig] = useState<number | null>(null);

  useEffect(() => {
    if (activeConfig) {
      setEditWeights(activeConfig.weights);
      setSelectedConfig(activeConfig.id);
      setEditName(activeConfig.name);
    }
  }, [activeConfig]);

  const total = Object.values(editWeights).reduce((a, b) => a + b, 0);
  const isValid = total === 100;

  const activateMut = useMutation({
    mutationFn: activateConfig,
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['scoring-configs'] }); queryClient.invalidateQueries({ queryKey: ['active-config'] }); toast.success('Configuration activated'); },
  });

  const recalcMut = useMutation({
    mutationFn: triggerRecalculate,
    onSuccess: (data) => {
      if (data.dispatch_mode === 'in_process') {
        toast(`Recalculation queued (Job #${data.job_id}) in local fallback mode.`);
      } else {
        toast.success(`Recalculation queued (Job #${data.job_id})`);
      }
    },
    onError: () => toast.error('Failed to trigger recalculation'),
  });

  const saveMut = useMutation({
    mutationFn: ({ name, weights }: { name: string; weights: ScoringWeights }) => createConfig(name, weights),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['scoring-configs'] }); toast.success('Configuration saved'); },
  });

  const handleWeightChange = (key: keyof ScoringWeights, value: number) => {
    setEditWeights(prev => ({ ...prev, [key]: Math.max(0, Math.min(100, value)) }));
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-gray-900">Scoring Configuration</h2>
        <div className="flex gap-2">
          <button
            onClick={() => saveMut.mutate({ name: editName || 'Custom', weights: editWeights })}
            disabled={!isValid || saveMut.isPending}
            className="px-3 py-1.5 text-sm bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 disabled:opacity-50"
          >
            Save as New
          </button>
          <button
            onClick={() => recalcMut.mutate()}
            disabled={recalcMut.isPending}
            className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 font-medium"
          >
            {recalcMut.isPending ? 'Queuing...' : 'Recalculate Scores'}
          </button>
        </div>
      </div>

      {/* Preset selector */}
      {configs && (
        <div className="flex flex-wrap gap-2">
          {configs.map(c => (
            <button
              key={c.id}
              onClick={() => { setSelectedConfig(c.id); setEditWeights(c.weights); setEditName(c.name); }}
              className={`px-3 py-1.5 rounded-lg text-sm border transition-colors ${
                selectedConfig === c.id
                  ? 'bg-blue-50 border-blue-300 text-blue-700 font-medium'
                  : 'bg-white border-gray-200 text-gray-600 hover:bg-gray-50'
              }`}
            >
              {c.name}
              {c.is_active && <span className="ml-1 text-xs text-green-600">(active)</span>}
            </button>
          ))}
        </div>
      )}

      {/* Weight sliders */}
      <div className="bg-white rounded-lg border border-gray-200 p-5">
        <div className="flex items-center justify-between mb-4">
          <span className="text-sm text-gray-500">Total: <span className={isValid ? 'text-green-600 font-medium' : 'text-red-600 font-medium'}>{total}/100</span></span>
        </div>
        <div className="space-y-4">
          {(Object.keys(SIGNAL_LABELS) as (keyof ScoringWeights)[]).map(key => (
            <div key={key} className="flex items-center gap-4">
              <span className="text-sm text-gray-700 w-40 flex-shrink-0">{SIGNAL_LABELS[key]}</span>
              <input
                type="range"
                min={0} max={50} step={1}
                value={editWeights[key]}
                onChange={e => handleWeightChange(key, parseInt(e.target.value))}
                className="flex-1 h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-blue-600"
              />
              <input
                type="number"
                min={0} max={100}
                value={editWeights[key]}
                onChange={e => handleWeightChange(key, parseInt(e.target.value) || 0)}
                className="w-14 px-2 py-1 text-sm border border-gray-300 rounded text-center"
              />
            </div>
          ))}
        </div>
      </div>

      {/* Activate selected */}
      {selectedConfig && selectedConfig !== activeConfig?.id && (
        <button
          onClick={() => activateMut.mutate(selectedConfig)}
          className="px-4 py-2 text-sm bg-green-600 text-white rounded-lg hover:bg-green-700 font-medium"
        >
          Activate Selected Configuration
        </button>
      )}
    </div>
  );
};

const DataHealthSection: React.FC = () => {
  const { data: quality } = useQuery({ queryKey: ['quality-summary'], queryFn: fetchQualitySummary, staleTime: 30000 });
  const { data: coverage } = useQuery({ queryKey: ['quality-coverage'], queryFn: fetchCoverage, staleTime: 30000 });
  const { data: sourceAudit } = useQuery({ queryKey: ['quality-source-audit'], queryFn: fetchSourceAudit, staleTime: 30000 });
  const { data: jobs } = useQuery({ queryKey: ['jobs'], queryFn: () => fetchJobs(undefined, 10), refetchInterval: 10000 });
  const { data: jobsSummary } = useQuery({ queryKey: ['jobs-summary'], queryFn: fetchJobsSummary, refetchInterval: 10000 });

  const triggerJob = useMutation({
    mutationFn: startJob,
    onSuccess: (data, jobType) => {
      if (data.dispatch_mode === 'in_process') {
        toast(`${jobType} job started in local fallback mode`);
      } else {
        toast.success(`${jobType} job started`);
      }
    },
    onError: (_, jobType) => toast.error(`Failed to start ${jobType}`),
  });

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-semibold text-gray-900">Data Health Dashboard</h2>

      {/* Signal Coverage */}
      {coverage && (
        <div className="bg-white rounded-lg border border-gray-200 p-5">
          <h3 className="text-sm font-medium text-gray-700 mb-3">Signal Coverage</h3>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
            {[
              { label: 'Complaints', count: coverage.with_complaints },
              { label: 'Violations', count: coverage.with_violations },
              { label: 'Transactions', count: coverage.with_transactions },
              { label: 'Permits', count: coverage.with_permits },
              { label: 'Litigation', count: coverage.with_litigation },
              { label: 'Emergency Repairs', count: coverage.with_erp },
              { label: 'Energy Grades', count: coverage.with_energy },
              { label: 'Evictions', count: coverage.with_evictions },
              { label: 'Facades', count: coverage.with_facades },
              { label: 'AEP', count: coverage.with_aep },
            ].map(s => {
              const pct = coverage.total_buildings > 0 ? (s.count / coverage.total_buildings * 100).toFixed(1) : '0';
              return (
                <div key={s.label} className="p-2">
                  <div className="text-xs text-gray-500">{s.label}</div>
                  <div className="text-sm font-medium">{s.count.toLocaleString()} <span className="text-gray-400">({pct}%)</span></div>
                  <div className="mt-1 h-1.5 bg-gray-100 rounded-full">
                    <div className="h-1.5 bg-blue-500 rounded-full" style={{ width: `${Math.min(100, parseFloat(pct))}%` }} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Source Freshness */}
      {quality && quality.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
          <h3 className="text-sm font-medium text-gray-700 p-4 border-b border-gray-100">Last Ingestion Runs</h3>
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase">Source</th>
                <th className="px-4 py-2 text-right text-xs text-gray-500 uppercase">Fetched</th>
                <th className="px-4 py-2 text-right text-xs text-gray-500 uppercase">Matched</th>
                <th className="px-4 py-2 text-right text-xs text-gray-500 uppercase">Match Rate</th>
                <th className="px-4 py-2 text-right text-xs text-gray-500 uppercase">Last Run</th>
                <th className="px-4 py-2 text-center text-xs text-gray-500 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {quality.map(q => (
                <tr key={q.source_name}>
                  <td className="px-4 py-2 font-medium">{q.source_name}</td>
                  <td className="px-4 py-2 text-right">{q.records_fetched.toLocaleString()}</td>
                  <td className="px-4 py-2 text-right">{q.records_matched.toLocaleString()}</td>
                  <td className="px-4 py-2 text-right">
                    <span className={q.match_rate < 0.5 ? 'text-red-600' : q.match_rate < 0.8 ? 'text-amber-600' : 'text-green-600'}>
                      {(q.match_rate * 100).toFixed(1)}%
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right text-gray-500">{new Date(q.run_timestamp).toLocaleDateString()}</td>
                  <td className="px-4 py-2 text-center">
                    <button
                      onClick={() => triggerJob.mutate(q.source_name)}
                      className="text-xs text-blue-600 hover:text-blue-800"
                    >
                      Re-run
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Source Integrity */}
      {sourceAudit && (
        <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
          <div className="p-4 border-b border-gray-100">
            <h3 className="text-sm font-medium text-gray-700">Source Integrity Matrix</h3>
            <p className="text-xs text-gray-500 mt-1">
              Canonical view of configured sources vs runnable jobs vs active ingestion.
            </p>
            <div className="mt-3 grid grid-cols-2 sm:grid-cols-5 gap-2">
              <div className="px-2 py-1.5 bg-gray-50 rounded">
                <div className="text-[10px] text-gray-500 uppercase">Total</div>
                <div className="text-sm font-semibold text-gray-800">{sourceAudit.summary.total_sources}</div>
              </div>
              <div className="px-2 py-1.5 bg-green-50 rounded">
                <div className="text-[10px] text-green-600 uppercase">Operational</div>
                <div className="text-sm font-semibold text-green-700">{sourceAudit.summary.operational}</div>
              </div>
              <div className="px-2 py-1.5 bg-amber-50 rounded">
                <div className="text-[10px] text-amber-600 uppercase">No Recent Ingest</div>
                <div className="text-sm font-semibold text-amber-700">{sourceAudit.summary.no_recent_ingest}</div>
              </div>
              <div className="px-2 py-1.5 bg-rose-50 rounded">
                <div className="text-[10px] text-rose-600 uppercase">Not Wired</div>
                <div className="text-sm font-semibold text-rose-700">{sourceAudit.summary.not_wired}</div>
              </div>
              <div className="px-2 py-1.5 bg-red-50 rounded">
                <div className="text-[10px] text-red-600 uppercase">Schema Missing</div>
                <div className="text-sm font-semibold text-red-700">{sourceAudit.summary.schema_missing}</div>
              </div>
            </div>
            {sourceAudit.critical_gaps.length > 0 && (
              <div className="mt-3 text-xs text-rose-700 bg-rose-50 border border-rose-200 rounded p-2">
                Critical gaps: {sourceAudit.critical_gaps.map(g => g.source_name).join(', ')}
              </div>
            )}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase">Source</th>
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase">Dataset</th>
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase">Job</th>
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase">UI Surface</th>
                  <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase">Status</th>
                  <th className="px-4 py-2 text-right text-xs text-gray-500 uppercase">Last Run</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {sourceAudit.sources.map(s => (
                  <tr key={s.source_name}>
                    <td className="px-4 py-2 font-medium">{s.source_name}</td>
                    <td className="px-4 py-2 text-xs text-gray-500">{s.dataset_id}</td>
                    <td className="px-4 py-2 text-xs">{s.job_type}</td>
                    <td className="px-4 py-2 text-xs text-gray-600">{s.ui_surface}</td>
                    <td className="px-4 py-2">
                      <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                        s.status === 'operational' ? 'bg-green-100 text-green-700' :
                        s.status === 'no_recent_ingest' ? 'bg-amber-100 text-amber-700' :
                        s.status === 'not_wired' ? 'bg-rose-100 text-rose-700' :
                        'bg-red-100 text-red-700'
                      }`}>{s.status}</span>
                    </td>
                    <td className="px-4 py-2 text-right text-xs text-gray-500">
                      {s.last_run ? new Date(s.last_run).toLocaleDateString() : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Recent Jobs */}
      {jobsSummary && (
        <div className="bg-white rounded-lg border border-gray-200 p-5">
          <h3 className="text-sm font-medium text-gray-700 mb-3">Queue Health (24h)</h3>
          <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
            <div className="p-3 bg-gray-50 rounded-lg">
              <div className="text-[10px] text-gray-500 uppercase">Queued</div>
              <div className="text-xl font-semibold text-gray-800">{jobsSummary.queued_count.toLocaleString()}</div>
            </div>
            <div className="p-3 bg-blue-50 rounded-lg">
              <div className="text-[10px] text-blue-600 uppercase">Running</div>
              <div className="text-xl font-semibold text-blue-700">{jobsSummary.running_count.toLocaleString()}</div>
            </div>
            <div className="p-3 bg-green-50 rounded-lg">
              <div className="text-[10px] text-green-600 uppercase">Succeeded</div>
              <div className="text-xl font-semibold text-green-700">{jobsSummary.succeeded_24h.toLocaleString()}</div>
            </div>
            <div className="p-3 bg-red-50 rounded-lg">
              <div className="text-[10px] text-red-600 uppercase">Failed</div>
              <div className="text-xl font-semibold text-red-700">{jobsSummary.failed_24h.toLocaleString()}</div>
            </div>
            <div className="p-3 bg-amber-50 rounded-lg">
              <div className="text-[10px] text-amber-600 uppercase">Avg Duration</div>
              <div className="text-xl font-semibold text-amber-700">{Math.round(jobsSummary.avg_duration_seconds_24h)}s</div>
            </div>
          </div>
        </div>
      )}

      {jobs && jobs.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
          <h3 className="text-sm font-medium text-gray-700 p-4 border-b border-gray-100">Recent Jobs</h3>
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase">ID</th>
                <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase">Type</th>
                <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase">Source</th>
                <th className="px-4 py-2 text-left text-xs text-gray-500 uppercase">Status</th>
                <th className="px-4 py-2 text-right text-xs text-gray-500 uppercase">Succeeded</th>
                <th className="px-4 py-2 text-right text-xs text-gray-500 uppercase">Failed</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {jobs.map(j => (
                <tr key={j.id}>
                  <td className="px-4 py-2 text-gray-500">#{j.id}</td>
                  <td className="px-4 py-2">{j.job_type}</td>
                  <td className="px-4 py-2">{j.source}</td>
                  <td className="px-4 py-2">
                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                      (j.status === 'completed' || j.status === 'succeeded') ? 'bg-green-100 text-green-700' :
                      j.status === 'failed' ? 'bg-red-100 text-red-700' :
                      (j.status === 'running' || j.status === 'queued') ? 'bg-blue-100 text-blue-700' :
                      'bg-gray-100 text-gray-700'
                    }`}>{j.status}</span>
                  </td>
                  <td className="px-4 py-2 text-right">{j.succeeded ?? '--'}</td>
                  <td className="px-4 py-2 text-right">{j.failed ?? '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

const SettingsPage: React.FC = () => {
  const [tab, setTab] = useState<'scoring' | 'data-health'>('scoring');
  return (
    <div className="space-y-6">
      <div className="flex gap-4 border-b border-gray-200">
        {[
          { key: 'scoring' as const, label: 'Scoring Weights' },
          { key: 'data-health' as const, label: 'Data Health' },
        ].map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              tab === t.key ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'scoring' ? <ScoringSection /> : <DataHealthSection />}
    </div>
  );
};

export default SettingsPage;
