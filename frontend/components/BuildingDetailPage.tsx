/**
 * BuildingDetailPage — Deep dive into a single building's churn signals.
 */
import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { useParams, useNavigate } from 'react-router-dom';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';
import {
  fetchBuildingDetail, fetchBuildingTimeline, fetchBuildingScoreHistory,
  addBuildingToPipeline, fetchBuildingOutreachEvents, logBuildingOutreachEvent,
} from '../services/buildings-api';
import { toast } from 'react-hot-toast';

const signalLabels: Record<string, string> = {
  ownership_change: 'Ownership Change',
  complaint_spike: 'Complaint Spike',
  violation_trend: 'Violation Trend',
  energy_grade_drop: 'Energy Grade Drop',
  dob_permits: 'DOB Permits',
  hpd_litigation: 'HPD Litigation',
  emergency_repairs: 'Emergency Repairs',
  building_size: 'Building Size',
  eviction_activity: 'Eviction Activity',
  facade_status: 'Facade Status',
};

const eventIcons: Record<string, string> = {
  complaint: 'C',
  violation: 'V',
  transaction: 'T',
  permit: 'P',
  litigation: 'L',
};

const BuildingDetailPage: React.FC = () => {
  const { bbl } = useParams<{ bbl: string }>();
  const navigate = useNavigate();

  const { data: building, isLoading } = useQuery({
    queryKey: ['building', bbl],
    queryFn: () => fetchBuildingDetail(bbl!),
    enabled: !!bbl,
  });

  const { data: timeline } = useQuery({
    queryKey: ['building-timeline', bbl],
    queryFn: () => fetchBuildingTimeline(bbl!),
    enabled: !!bbl,
  });

  const { data: scoreHistory } = useQuery({
    queryKey: ['building-score-history', bbl],
    queryFn: () => fetchBuildingScoreHistory(bbl!),
    enabled: !!bbl,
  });

  const { data: outreachData, refetch: refetchOutreach } = useQuery({
    queryKey: ['building-outreach', bbl],
    queryFn: () => fetchBuildingOutreachEvents(bbl!),
    enabled: !!bbl,
  });

  const handleLogOutreach = async (stage: string) => {
    if (!bbl) return;
    try {
      await logBuildingOutreachEvent(bbl, { stage, method: 'manual' });
      toast.success(`Outreach logged: ${stage}`);
      refetchOutreach();
    } catch {
      toast.error('Failed to log outreach');
    }
  };

  const handleAddToPipeline = async () => {
    if (!bbl) return;
    try {
      await addBuildingToPipeline(bbl);
      toast.success('Building added to pipeline');
    } catch {
      toast.error('Failed to add to pipeline');
    }
  };

  if (isLoading) return <div className="p-8 text-center text-gray-400">Loading...</div>;
  if (!building) return <div className="p-8 text-center text-red-500">Building not found</div>;

  const breakdown = building.churn_breakdown;
  const chartData = scoreHistory?.slice().reverse().map(h => ({
    date: new Date(h.scored_at).toLocaleDateString(),
    score: h.churn_score,
  })) || [];

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
            <span>BBL: {building.bbl}</span>
            {building.unit_count && <span>{building.unit_count} units</span>}
            {building.year_built && <span>Built {building.year_built}</span>}
          </div>
        </div>
        <div className="flex items-center gap-3">
          {building.churn_score !== null && (
            <div className={`text-3xl font-bold ${building.churn_score >= 70 ? 'text-red-600' : building.churn_score >= 40 ? 'text-amber-600' : 'text-green-600'}`}>
              {building.churn_score.toFixed(1)}
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

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Score Breakdown */}
        <div className="lg:col-span-2 bg-white rounded-lg border border-gray-200 p-5">
          <h2 className="font-semibold text-gray-900 mb-4">Churn Score Breakdown</h2>
          {breakdown ? (
            <div className="space-y-3">
              {Object.entries(breakdown).map(([key, val]) => {
                const raw = val.raw;
                const contribution = val.contribution;
                return (
                  <div key={key} className="flex items-center gap-3">
                    <span className="text-sm text-gray-600 w-40 flex-shrink-0">{signalLabels[key] || key}</span>
                    <div className="flex-1 bg-gray-100 rounded-full h-2.5">
                      <div
                        className={`h-2.5 rounded-full ${contribution > 0 ? 'bg-red-400' : 'bg-gray-200'}`}
                        style={{ width: `${Math.min(100, Math.abs(contribution))}%` }}
                      />
                    </div>
                    <span className="text-sm text-gray-500 w-16 text-right">
                      {raw !== null ? `${raw}` : 'N/A'}
                    </span>
                    <span className="text-xs text-gray-400 w-20 text-right">
                      w: {val.weight} → {val.effective_weight}
                    </span>
                  </div>
                );
              })}
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
              ['Key Signal', building.key_signal?.replace(/_/g, ' ')],
              ['Signal Coverage', building.coverage_ratio ? `${(building.coverage_ratio * 100).toFixed(0)}%` : null],
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
                  'bg-purple-100 text-purple-600'
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
