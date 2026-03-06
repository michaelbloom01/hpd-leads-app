import React from 'react';
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
}

const LeadKanban: React.FC<LeadKanbanProps> = ({ leads, onSelectLead }) => {
  const grouped = KANBAN_STAGES.map((stage) => ({
    stage,
    leads: leads
      .filter((lead) => (lead.pipeline_stage || 'research') === stage)
      .sort((a, b) => (b.score || 0) - (a.score || 0)),
  }));

  return (
    <div className="overflow-x-auto">
      <div className="flex gap-3 min-w-[1200px] p-3">
        {grouped.map((column) => (
          <div key={column.stage} className="w-[260px] bg-gray-50 border border-gray-200 rounded-xl flex flex-col">
            <div className="px-3 py-2 border-b border-gray-200 flex items-center justify-between">
              <h4 className="text-[11px] font-bold text-gray-600 uppercase tracking-wider">{label(column.stage)}</h4>
              <span className="text-[11px] text-gray-500">{column.leads.length}</span>
            </div>
            <div className="p-2 space-y-2 max-h-[560px] overflow-y-auto">
              {column.leads.length === 0 ? (
                <div className="text-[11px] text-gray-400 p-2">No leads</div>
              ) : (
                column.leads.map((lead) => (
                  <button
                    key={lead.lead_id}
                    onClick={() => onSelectLead(lead)}
                    className="w-full text-left bg-white border border-gray-200 rounded-lg p-2 hover:border-blue-300 hover:bg-blue-50/40 transition-colors"
                  >
                    <div className="text-xs font-semibold text-gray-800 truncate">
                      {getLeadDisplayName(lead)}
                    </div>
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
                  </button>
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
