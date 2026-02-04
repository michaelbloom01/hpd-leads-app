
import React, { useState } from 'react';
import { ApiLead, enrichLeads } from '../services/api';

interface Props {
  lead: ApiLead;
  onClose: () => void;
}

const LeadDetail: React.FC<Props> = ({ lead, onClose }) => {
  const [isEnriching, setIsEnriching] = useState(false);
  const [enrichedLead, setEnrichedLead] = useState(lead);

  const handleEnrich = async () => {
    setIsEnriching(true);
    try {
      const result = await enrichLeads([lead.lead_id]);
      if (result.results && result.results.length > 0) {
        const enriched = result.results[0];
        setEnrichedLead({
          ...enrichedLead,
          phone: enriched.phone || enrichedLead.phone,
          email: enriched.email || enrichedLead.email,
          website: enriched.website || enrichedLead.website,
          enrichment_status: enriched.status,
        });
      }
    } catch (err) {
      console.error('Enrichment failed:', err);
    } finally {
      setIsEnriching(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div 
        className="bg-slate-900 border border-white/10 rounded-2xl max-w-2xl w-full max-h-[90vh] overflow-y-auto shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="sticky top-0 bg-slate-900 border-b border-white/10 p-6 flex justify-between items-start">
          <div>
            <div className="text-xs text-blue-400 uppercase tracking-wider font-medium mb-1">
              {enrichedLead.agent_name ? 'Management Company' : 'Owner (No Agent Listed)'}
            </div>
            <h2 className="text-xl font-bold text-white">{enrichedLead.agent_name || enrichedLead.owner_name}</h2>
            {enrichedLead.agent_name && enrichedLead.owner_name && enrichedLead.agent_name !== enrichedLead.owner_name && (
              <p className="text-slate-500 text-sm mt-1">Owner: {enrichedLead.owner_name}</p>
            )}
            <p className="text-slate-600 text-xs mt-1">{enrichedLead.owner_type} • {enrichedLead.boro}</p>
          </div>
          <button 
            onClick={onClose}
            className="p-2 text-slate-500 hover:text-white transition-colors"
          >
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12"/>
            </svg>
          </button>
        </div>

        <div className="p-6 space-y-6">
          {/* Score & Portfolio */}
          <div className="grid grid-cols-3 gap-4">
            <div className="bg-slate-800/50 rounded-xl p-4 text-center">
              <div className={`text-3xl font-bold font-mono ${
                enrichedLead.score >= 60 ? 'text-emerald-400' : 
                enrichedLead.score >= 40 ? 'text-amber-400' : 'text-slate-400'
              }`}>
                {enrichedLead.score.toFixed(1)}
              </div>
              <div className="text-xs text-slate-500 mt-1 uppercase tracking-wider">Score</div>
            </div>
            <div className="bg-slate-800/50 rounded-xl p-4 text-center">
              <div className="text-3xl font-bold font-mono text-blue-400">{enrichedLead.portfolio_size}</div>
              <div className="text-xs text-slate-500 mt-1 uppercase tracking-wider">Buildings</div>
            </div>
            <div className="bg-slate-800/50 rounded-xl p-4 text-center">
              <div className="text-3xl font-bold font-mono text-purple-400">{enrichedLead.boros.length}</div>
              <div className="text-xs text-slate-500 mt-1 uppercase tracking-wider">Boroughs</div>
            </div>
          </div>

          {/* Contact Information */}
          <div className="bg-slate-800/30 rounded-xl p-5">
            <div className="flex justify-between items-center mb-4">
              <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider">Contact Information</h3>
              <button
                onClick={handleEnrich}
                disabled={isEnriching}
                className="px-3 py-1 bg-blue-600 text-white text-xs font-medium rounded-lg hover:bg-blue-500 disabled:opacity-50 transition-colors"
              >
                {isEnriching ? 'Enriching...' : 'Enrich'}
              </button>
            </div>
            
            <div className="space-y-3">
              <div className="flex items-center gap-3">
                <span className="text-lg">📞</span>
                {enrichedLead.phone ? (
                  <a href={`tel:${enrichedLead.phone}`} className="text-emerald-400 hover:underline">
                    {enrichedLead.phone}
                  </a>
                ) : (
                  <span className="text-slate-600">No phone found</span>
                )}
              </div>
              
              <div className="flex items-center gap-3">
                <span className="text-lg">✉️</span>
                {enrichedLead.email ? (
                  <a href={`mailto:${enrichedLead.email}`} className="text-blue-400 hover:underline">
                    {enrichedLead.email}
                  </a>
                ) : (
                  <span className="text-slate-600">No email found</span>
                )}
              </div>
              
              <div className="flex items-center gap-3">
                <span className="text-lg">🌐</span>
                {enrichedLead.website ? (
                  <a href={enrichedLead.website} target="_blank" rel="noopener noreferrer" className="text-purple-400 hover:underline">
                    {enrichedLead.website}
                  </a>
                ) : (
                  <span className="text-slate-600">No website found</span>
                )}
              </div>
              
              {enrichedLead.address && (
                <div className="flex items-center gap-3">
                  <span className="text-lg">📍</span>
                  <span className="text-slate-300">{enrichedLead.address}</span>
                </div>
              )}
            </div>
            
            <div className="mt-3 pt-3 border-t border-white/5 flex items-center gap-2">
              <span className={`px-2 py-0.5 text-xs rounded ${
                enrichedLead.enrichment_status === 'complete' ? 'bg-emerald-900/30 text-emerald-400' :
                enrichedLead.enrichment_status === 'partial' ? 'bg-amber-900/30 text-amber-400' :
                enrichedLead.enrichment_status === 'failed' ? 'bg-rose-900/30 text-rose-400' :
                'bg-slate-800 text-slate-500'
              }`}>
                Enrichment: {enrichedLead.enrichment_status}
              </span>
            </div>
          </div>

          {/* Business Summary */}
          {enrichedLead.business_summary && (
            <div className="bg-slate-800/30 rounded-xl p-5">
              <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider mb-3">About</h3>
              <p className="text-slate-400 text-sm leading-relaxed">{enrichedLead.business_summary}</p>
            </div>
          )}

          {/* Buildings List */}
          <div className="bg-slate-800/30 rounded-xl p-5">
            <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider mb-3">
              Buildings ({enrichedLead.buildings.length} shown of {enrichedLead.portfolio_size})
            </h3>
            <div className="space-y-2 max-h-48 overflow-y-auto">
              {enrichedLead.buildings.map((building, i) => (
                <div key={i} className="text-sm text-slate-400 py-1 px-2 bg-slate-900/50 rounded">
                  {building}
                </div>
              ))}
            </div>
          </div>

          {/* Tags */}
          {enrichedLead.tags.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {enrichedLead.tags.map((tag, i) => (
                <span key={i} className="px-2 py-1 bg-slate-800 text-slate-400 text-xs rounded">
                  {tag}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default LeadDetail;
