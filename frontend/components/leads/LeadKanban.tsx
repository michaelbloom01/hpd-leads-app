import React from 'react';
import { Link, useInRouterContext } from 'react-router-dom';
import type { ApiLead } from '../../services/api';
import { formatCurrency, scoreColor } from '../../utils/format';
import { getLeadDisplayName } from '../../utils/leads';

const KANBAN_STAGES = [
  'research',
  'first_contact',
  'follow_up',
  'meeting_scheduled',
  'meeting_done',
  'loi',
  'due_diligence',
  'closed',
] as const;

const label = (stage: string) => stage.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

interface LeadKanbanProps {
  leads: ApiLead[];
  onSelectLead: (lead: ApiLead) => void;
  getLeadHref?: (leadId: string) => string;
  selectedLeadIds?: Set<string>;
  onToggleSelect?: (leadId: string) => void;
  onToggleVisibleSelect?: () => void;
  areAllVisibleSelected?: boolean;
  onToggleStageSelect?: (stage: string) => void;
  isStageFullySelected?: (stage: string) => boolean;
  onClearSelection?: () => void;
}

const LeadKanban: React.FC<LeadKanbanProps> = ({
  leads,
  onSelectLead,
  getLeadHref,
  selectedLeadIds,
  onToggleSelect,
  onToggleVisibleSelect,
  areAllVisibleSelected = false,
  onToggleStageSelect,
  isStageFullySelected,
  onClearSelection,
}) => {
  const buildLeadHref = getLeadHref || ((leadId: string) => `/leads?lead=${encodeURIComponent(leadId)}`);
  const hasRouterContext = useInRouterContext();
  const grouped = KANBAN_STAGES.map((stage) => ({
    stage,
    leads: leads
      .filter((lead) => (lead.pipeline_stage || 'research') === stage)
      .sort((a, b) => (b.score || 0) - (a.score || 0)),
  }));

  return (
    <div className="overflow-x-auto">
      {onToggleSelect && (
        <div className="flex items-center justify-between gap-2 px-3 pt-3">
          <div className="text-[11px] text-gray-500">
            {selectedLeadIds?.size ? `${selectedLeadIds.size} selected` : 'Select cards to use bulk actions'}
          </div>
          <div className="flex items-center gap-2">
            {onToggleVisibleSelect && leads.length > 0 && (
              <button
                type="button"
                onClick={onToggleVisibleSelect}
                className="px-2 py-1 text-[11px] rounded border border-gray-200 bg-white text-gray-600 hover:text-gray-800 hover:border-gray-300 transition-colors"
              >
                {areAllVisibleSelected ? 'Clear Visible' : `Select Visible (${leads.length})`}
              </button>
            )}
            {onClearSelection && (selectedLeadIds?.size || 0) > 0 && (
              <button
                type="button"
                onClick={onClearSelection}
                className="px-2 py-1 text-[11px] rounded border border-gray-200 bg-white text-gray-600 hover:text-gray-800 hover:border-gray-300 transition-colors"
              >
                Clear Selection
              </button>
            )}
          </div>
        </div>
      )}
      <div className="flex gap-3 min-w-[1200px] p-3">
        {grouped.map((column) => (
          <div key={column.stage} className="w-[260px] bg-gray-50 border border-gray-200 rounded-xl flex flex-col">
            <div className="px-3 py-2 border-b border-gray-200 flex items-center justify-between">
              <div className="flex items-center gap-2 min-w-0">
                <h4 className="text-[11px] font-bold text-gray-600 uppercase tracking-wider">{label(column.stage)}</h4>
                <span className="text-[11px] text-gray-500">{column.leads.length}</span>
              </div>
              {onToggleStageSelect && column.leads.length > 0 && (
                <button
                  type="button"
                  onClick={() => onToggleStageSelect(column.stage)}
                  className="text-[10px] font-medium text-blue-600 hover:text-blue-700 hover:underline"
                >
                  {isStageFullySelected?.(column.stage) ? 'Clear Stage' : 'Select Stage'}
                </button>
              )}
            </div>
            <div className="p-2 space-y-2 max-h-[560px] overflow-y-auto">
              {column.leads.length === 0 ? (
                <div className="text-[11px] text-gray-400 p-2">No leads</div>
              ) : (
                column.leads.map((lead) => (
                  <div
                    key={lead.lead_id}
                    onClick={() => onSelectLead(lead)}
                    className={`w-full text-left bg-white border rounded-lg p-2 transition-colors ${
                      selectedLeadIds?.has(lead.lead_id)
                        ? 'border-blue-400 bg-blue-50/60'
                        : 'border-gray-200 hover:border-blue-300 hover:bg-blue-50/40'
                    }`}
                  >
                    {onToggleSelect && (
                      <div className="mb-2 flex items-center justify-between">
                        <label className="flex items-center gap-2 text-[10px] text-gray-500" onClick={(e) => e.stopPropagation()}>
                          <input
                            type="checkbox"
                            checked={selectedLeadIds?.has(lead.lead_id) || false}
                            onChange={() => onToggleSelect(lead.lead_id)}
                            className="rounded border-gray-300"
                          />
                          Select
                        </label>
                      </div>
                    )}
                    {hasRouterContext ? (
                      <Link
                        to={buildLeadHref(lead.lead_id)}
                        onClick={(e) => e.stopPropagation()}
                        className="block text-xs font-semibold text-gray-800 truncate hover:text-blue-700 hover:underline"
                      >
                        {getLeadDisplayName(lead)}
                      </Link>
                    ) : (
                      <a
                        href={buildLeadHref(lead.lead_id)}
                        onClick={(e) => e.stopPropagation()}
                        className="block text-xs font-semibold text-gray-800 truncate hover:text-blue-700 hover:underline"
                      >
                        {getLeadDisplayName(lead)}
                      </a>
                    )}
                    <div className="mt-1 text-[10px] text-gray-500">
                      {lead.portfolio_size || 0} bldgs • {(lead.total_units || 0).toLocaleString()} units
                    </div>
                    <div className="mt-1.5 flex items-center justify-between">
                      <span className={`text-xs font-mono font-bold ${scoreColor(lead.score || 0)}`}>
                        {(lead.score || 0).toFixed(0)}
                      </span>
                      <span className="text-[10px] text-emerald-600 font-mono">
                        {lead.estimated_annual_revenue ? formatCurrency(lead.estimated_annual_revenue) : '--'}
                      </span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default LeadKanban;
