import React from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { dismissAlert, listAlerts } from '../services/alerts-api';
import { getUnifiedFollowUps } from '../services/outreach-api';

const AlertsPage: React.FC = () => {
  const queryClient = useQueryClient();
  const { data: alerts, isLoading: loadingAlerts } = useQuery({
    queryKey: ['alerts-page'],
    queryFn: () => listAlerts(75),
    refetchInterval: 15000,
  });
  const { data: followUps, isLoading: loadingFollowUps } = useQuery({
    queryKey: ['unified-follow-ups'],
    queryFn: () => getUnifiedFollowUps(),
    refetchInterval: 15000,
  });

  const dismissMutation = useMutation({
    mutationFn: (id: number) => dismissAlert(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['alerts-page'] });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return (
    <div className="h-full overflow-auto bg-gray-50">
      <div className="max-w-7xl mx-auto p-6 space-y-6">
        <div className="bg-white border border-gray-200 rounded-2xl p-6 shadow-sm">
          <h1 className="text-2xl font-semibold text-gray-900">Alerts And Follow-Ups</h1>
          <p className="text-sm text-gray-500 mt-1">
            One place for review-required target matches, follow-up queue, and operational change alerts across leads, buildings, and imported targets.
          </p>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
          <div className="bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between">
              <div>
                <h2 className="text-sm font-semibold text-gray-900">Follow-Up Queue</h2>
                <p className="text-xs text-gray-500 mt-1">Due today or overdue across all workflow types.</p>
              </div>
              {loadingFollowUps && <span className="text-xs text-gray-400">Loading...</span>}
            </div>
            <div className="p-4 space-y-3">
              {(followUps?.items || []).map((item) => {
                const href = item.entity_type === 'target'
                  ? `/targets/${item.entity_id}`
                  : item.entity_type === 'lead'
                  ? `/leads/${encodeURIComponent(item.entity_id)}`
                  : `/buildings/${item.entity_id}`;
                return (
                  <Link key={`${item.entity_type}-${item.entity_id}`} to={href} className="block border border-gray-200 rounded-xl p-4 hover:bg-gray-50">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <div className="font-medium text-gray-900">{item.display_name}</div>
                        <div className="text-xs text-gray-500 mt-1">
                          {[item.entity_type, item.stage, item.status, item.secondary_ref].filter(Boolean).join(' • ')}
                        </div>
                      </div>
                      <div className="text-sm font-medium text-gray-900">{item.due_date}</div>
                    </div>
                  </Link>
                );
              })}
              {!loadingFollowUps && !(followUps?.items || []).length && (
                <div className="text-sm text-gray-500">No follow-ups due right now.</div>
              )}
            </div>
          </div>

          <div className="bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between">
              <div>
                <h2 className="text-sm font-semibold text-gray-900">Change Alerts</h2>
                <p className="text-xs text-gray-500 mt-1">Ambiguous target matches and other operator-visible deltas.</p>
              </div>
              {loadingAlerts && <span className="text-xs text-gray-400">Loading...</span>}
            </div>
            <div className="p-4 space-y-3">
              {(alerts || []).map((alert) => {
                const href = alert.target_item_id
                  ? `/targets/${alert.target_item_id}`
                  : alert.lead_id
                  ? `/leads/${encodeURIComponent(alert.lead_id)}`
                  : alert.bbl
                  ? `/buildings/${alert.bbl}`
                  : undefined;
                const content = (
                  <div className="flex items-start justify-between gap-3 border border-gray-200 rounded-xl p-4 hover:bg-gray-50">
                    <div>
                      <div className="font-medium text-gray-900">{alert.description}</div>
                      <div className="text-xs text-gray-500 mt-1">
                        {[alert.alert_type, alert.created_at].filter(Boolean).join(' • ')}
                      </div>
                    </div>
                    <button
                      onClick={(e) => {
                        e.preventDefault();
                        dismissMutation.mutate(alert.id);
                      }}
                      className="px-2.5 py-1 rounded-lg border border-gray-300 text-xs font-medium text-gray-700"
                    >
                      Dismiss
                    </button>
                  </div>
                );
                return href ? (
                  <Link key={alert.id} to={href} className="block">
                    {content}
                  </Link>
                ) : (
                  <div key={alert.id}>{content}</div>
                );
              })}
              {!loadingAlerts && !(alerts || []).length && (
                <div className="text-sm text-gray-500">No active alerts.</div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default AlertsPage;
