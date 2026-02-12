import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import DOMPurify from 'dompurify';
import {
  AgentLeadRow,
  AgentScriptRow,
  AgentBriefingPreview,
  AgentRentComparison,
  AgentConfirmation,
  API_BASE_URL,
} from '../services/api';

interface MessageData {
  text: string;
  leads?: AgentLeadRow[];
  scripts?: AgentScriptRow[];
  briefing?: AgentBriefingPreview;
  rentComparisons?: AgentRentComparison[];
  confirmation?: AgentConfirmation;
  actions?: string[];
  error?: string;
  filtersApplied?: Record<string, unknown>;
}

interface AgentMessageProps {
  message: MessageData;
  onSelectLead: (leadId: string) => void;
  onConfirm: (actionId: string, confirmed: boolean) => void;
}

const AgentMessage: React.FC<AgentMessageProps> = ({ message, onSelectLead, onConfirm }) => {
  return (
    <div className="space-y-3">
      {/* Text content — rendered as markdown */}
      {message.text && (
        <div className="agent-markdown text-sm text-gray-700 leading-relaxed">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {message.text}
          </ReactMarkdown>
        </div>
      )}

      {/* Lead table */}
      {message.leads && message.leads.length > 0 && (
        <LeadTable leads={message.leads} onSelectLead={onSelectLead} />
      )}

      {/* Scripts */}
      {message.scripts && message.scripts.length > 0 && (
        <ScriptCards scripts={message.scripts} />
      )}

      {/* Briefing preview */}
      {message.briefing && (
        <BriefingPreview briefing={message.briefing} />
      )}

      {/* Rent comparisons */}
      {message.rentComparisons && message.rentComparisons.length > 0 && (
        <RentComparisonTable comparisons={message.rentComparisons} />
      )}

      {/* Confirmation card */}
      {message.confirmation && (
        <ConfirmationCard
          confirmation={message.confirmation}
          onConfirm={onConfirm}
          hasActions={!!(message.actions && message.actions.length > 0)}
          hasError={!!message.error}
        />
      )}

      {/* Actions taken */}
      {message.actions && message.actions.length > 0 && (
        <div className="space-y-1">
          {message.actions.map((action, i) => (
            <div key={i} className="flex items-center gap-2 text-sm text-emerald-600 bg-emerald-50 rounded-lg px-3 py-2">
              <svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7"/>
              </svg>
              {action}
            </div>
          ))}
        </div>
      )}

      {/* Error */}
      {message.error && (
        <div className="flex items-start gap-2 text-sm text-rose-600 bg-rose-50 rounded-lg px-3 py-2 border border-rose-100">
          <svg className="w-4 h-4 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
          </svg>
          {message.error}
        </div>
      )}
    </div>
  );
};

// --- Sub-components ---

const LeadTable: React.FC<{ leads: AgentLeadRow[]; onSelectLead: (id: string) => void }> = ({ leads, onSelectLead }) => {
  return (
    <div className="bg-white rounded-lg border border-gray-200 overflow-hidden shadow-sm">
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-gray-50 border-b border-gray-200">
              <th className="text-left px-3 py-2 text-gray-500 font-medium">Company</th>
              <th className="text-center px-2 py-2 text-gray-500 font-medium">Bldgs</th>
              <th className="text-center px-2 py-2 text-gray-500 font-medium">Units</th>
              <th className="text-center px-2 py-2 text-gray-500 font-medium">Score</th>
              <th className="text-right px-2 py-2 text-gray-500 font-medium">Revenue</th>
              <th className="text-center px-2 py-2 text-gray-500 font-medium">Contact</th>
            </tr>
          </thead>
          <tbody>
            {leads.map((lead) => (
              <tr
                key={lead.lead_id}
                onClick={() => onSelectLead(lead.lead_id)}
                className="border-b border-gray-100 hover:bg-gray-50 cursor-pointer transition-colors"
              >
                <td className="px-3 py-2 font-medium text-gray-900 max-w-[160px] truncate">
                  {lead.company_name}
                </td>
                <td className="px-2 py-2 text-center text-gray-600">{lead.portfolio_size}</td>
                <td className="px-2 py-2 text-center text-gray-600">{lead.total_units.toLocaleString()}</td>
                <td className="px-2 py-2 text-center">
                  <span className={`font-medium ${lead.score >= 70 ? 'text-emerald-600' : lead.score >= 40 ? 'text-amber-600' : 'text-gray-500'}`}>
                    {lead.score.toFixed(1)}
                  </span>
                </td>
                <td className="px-2 py-2 text-right text-gray-600">
                  ${(lead.estimated_annual_revenue / 1000).toFixed(0)}k
                </td>
                <td className="px-2 py-2 text-center">
                  <div className="flex items-center justify-center gap-1">
                    {lead.phone && <span className="text-emerald-500" title="Phone">P</span>}
                    {lead.email && <span className="text-blue-500" title="Email">E</span>}
                    {lead.website && <span className="text-purple-500" title="Web">W</span>}
                    {!lead.phone && !lead.email && !lead.website && <span className="text-gray-300">—</span>}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="px-3 py-1.5 text-[10px] text-gray-400 bg-gray-50 border-t border-gray-100">
        {leads.length} leads shown · Click to view details
      </div>
    </div>
  );
};

const ScriptCards: React.FC<{ scripts: AgentScriptRow[] }> = ({ scripts }) => {
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  return (
    <div className="space-y-2">
      {scripts.map((script, i) => (
        <div key={script.lead_id} className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden">
          <div className="flex items-center justify-between px-3 py-2 bg-gray-50 border-b border-gray-100">
            <div>
              <span className="text-sm font-medium text-gray-900">{script.company_name}</span>
              {script.phone && <span className="ml-2 text-xs text-gray-500">{script.phone}</span>}
            </div>
            <button
              onClick={() => setExpandedIdx(expandedIdx === i ? null : i)}
              className="text-xs text-emerald-600 hover:text-emerald-700 font-medium"
            >
              {expandedIdx === i ? 'Collapse' : 'Expand'}
            </button>
          </div>
          <div className={`px-3 py-2 text-xs text-gray-700 whitespace-pre-wrap ${expandedIdx === i ? '' : 'max-h-20 overflow-hidden'}`}>
            {script.script}
          </div>
          {expandedIdx !== i && (
            <div className="h-6 bg-gradient-to-t from-white to-transparent -mt-6 relative pointer-events-none" />
          )}
        </div>
      ))}
    </div>
  );
};

const BriefingPreview: React.FC<{ briefing: AgentBriefingPreview }> = ({ briefing }) => {
  return (
    <div className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden">
      <div className="px-3 py-2 bg-gray-50 border-b border-gray-100 flex items-center justify-between">
        <span className="text-sm font-medium text-gray-900">Email Briefing — {briefing.lead_count} leads</span>
        <a
          href={`${API_BASE_URL}/api/agent/briefing/${briefing.briefing_id}`}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-emerald-600 hover:text-emerald-700 font-medium"
        >
          Download HTML
        </a>
      </div>
      <div
        className="p-3 text-xs max-h-40 overflow-y-auto"
        dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(briefing.html.slice(0, 2000)) }}
      />
    </div>
  );
};

const RentComparisonTable: React.FC<{ comparisons: AgentRentComparison[] }> = ({ comparisons }) => {
  return (
    <div className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-gray-50 border-b border-gray-200">
              <th className="text-left px-3 py-2 text-gray-500 font-medium">Company</th>
              <th className="text-right px-2 py-2 text-gray-500 font-medium">Current</th>
              <th className="text-right px-2 py-2 text-gray-500 font-medium">Refined</th>
              <th className="text-right px-2 py-2 text-gray-500 font-medium">Delta</th>
              <th className="text-center px-2 py-2 text-gray-500 font-medium">Conf.</th>
            </tr>
          </thead>
          <tbody>
            {comparisons.map((comp) => (
              <tr key={comp.lead_id} className="border-b border-gray-100">
                <td className="px-3 py-2 font-medium text-gray-900 max-w-[140px] truncate">
                  {comp.company_name}
                </td>
                <td className="px-2 py-2 text-right text-gray-600">${comp.current_monthly_rent.toFixed(0)}</td>
                <td className="px-2 py-2 text-right text-gray-600">${comp.refined_monthly_rent.toFixed(0)}</td>
                <td className={`px-2 py-2 text-right font-medium ${comp.delta_pct > 0 ? 'text-emerald-600' : comp.delta_pct < 0 ? 'text-rose-600' : 'text-gray-500'}`}>
                  {comp.delta_pct > 0 ? '+' : ''}{comp.delta_pct.toFixed(1)}%
                </td>
                <td className="px-2 py-2 text-center">
                  <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-medium ${
                    comp.confidence === 'high' ? 'bg-emerald-50 text-emerald-700' :
                    comp.confidence === 'medium' ? 'bg-amber-50 text-amber-700' :
                    'bg-gray-100 text-gray-500'
                  }`}>
                    {comp.confidence}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

const ConfirmationCard: React.FC<{
  confirmation: AgentConfirmation;
  onConfirm: (actionId: string, confirmed: boolean) => void;
  hasActions?: boolean;
  hasError?: boolean;
}> = ({ confirmation, onConfirm, hasActions, hasError }) => {
  const [responded, setResponded] = useState<'confirmed' | 'cancelled' | null>(null);

  // Determine the visual state
  const isResolved = hasActions || hasError;

  return (
    <div className={`rounded-lg border p-4 transition-colors ${
      isResolved && hasActions ? 'bg-emerald-50 border-emerald-200' :
      isResolved && hasError ? 'bg-rose-50 border-rose-200' :
      responded ? 'bg-gray-50 border-gray-200' :
      'bg-amber-50 border-amber-200'
    }`}>
      <div className="flex items-center gap-2 mb-2">
        {isResolved && hasActions ? (
          <svg className="w-4 h-4 text-emerald-600 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7"/>
          </svg>
        ) : isResolved && hasError ? (
          <svg className="w-4 h-4 text-rose-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
          </svg>
        ) : null}
        <p className={`text-sm font-medium ${
          isResolved && hasActions ? 'text-emerald-800' :
          isResolved && hasError ? 'text-rose-800' :
          'text-gray-900'
        }`}>
          {confirmation.description}
          {!responded && !isResolved ? '?' : ''}
        </p>
      </div>
      {!responded && !isResolved ? (
        <div className="flex items-center gap-3">
          <button
            onClick={() => { setResponded('cancelled'); onConfirm(confirmation.action_id, false); }}
            className="px-4 py-1.5 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={() => { setResponded('confirmed'); onConfirm(confirmation.action_id, true); }}
            className="px-4 py-1.5 text-sm text-white bg-emerald-600 rounded-lg hover:bg-emerald-700 transition-colors font-medium"
          >
            Confirm ({confirmation.count})
          </button>
        </div>
      ) : responded && !isResolved ? (
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 border-2 border-gray-300 border-t-gray-600 rounded-full animate-spin" />
          <p className="text-xs text-gray-500">
            {responded === 'confirmed' ? 'Executing...' : 'Cancelling...'}
          </p>
        </div>
      ) : null}
    </div>
  );
};

export default AgentMessage;
