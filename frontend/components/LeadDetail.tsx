import React, { useState, useEffect } from 'react';
import { ApiLead, updateLead, researchLead, addOutreachAttempt, enrichLeadContacts, generateAiSummary, CompanyResearch, OutreachAttempt, DOSInfo } from '../services/api';

interface Props {
  lead: ApiLead;
  onClose: () => void;
}

const OUTREACH_STATUSES = [
  { value: 'new', label: 'New', color: 'bg-slate-600 text-slate-200' },
  { value: 'contacted', label: 'Contacted', color: 'bg-blue-600 text-blue-100' },
  { value: 'interested', label: 'Interested', color: 'bg-emerald-600 text-emerald-100' },
  { value: 'not_interested', label: 'Not Interested', color: 'bg-amber-600 text-amber-100' },
  { value: 'closed', label: 'Closed', color: 'bg-purple-600 text-purple-100' },
];

const OUTREACH_METHODS = ['phone', 'email', 'linkedin', 'in_person', 'other'];
const OUTREACH_OUTCOMES = ['no_answer', 'left_voicemail', 'spoke_with_contact', 'sent_email', 'meeting_scheduled', 'not_interested', 'other'];

const LeadDetail: React.FC<Props> = ({ lead, onClose }) => {
  const [isEnriching, setIsEnriching] = useState(false);
  const [isResearching, setIsResearching] = useState(false);
  const [isGeneratingAI, setIsGeneratingAI] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [enrichedLead, setEnrichedLead] = useState(lead);
  const [notes, setNotes] = useState(lead.notes || '');
  const [outreachStatus, setOutreachStatus] = useState(lead.outreach_status || 'new');
  const [companyResearch, setCompanyResearch] = useState<CompanyResearch | null>(null);
  const [dosInfo, setDosInfo] = useState<DOSInfo | null>(null);
  const [aiDescription, setAiDescription] = useState<string | null>(lead.business_summary || null);
  const [outreachAttempts, setOutreachAttempts] = useState<OutreachAttempt[]>(lead.outreach_attempts || []);
  const [showAddOutreach, setShowAddOutreach] = useState(false);
  const [newOutreach, setNewOutreach] = useState({ method: 'phone', outcome: 'no_answer', notes: '' });

  // Sync state when lead prop changes
  useEffect(() => {
    setEnrichedLead(lead);
    setNotes(lead.notes || '');
    setOutreachStatus(lead.outreach_status || 'new');
    setOutreachAttempts(lead.outreach_attempts || []);
    setAiDescription(lead.business_summary || null);
    setDosInfo(null);
    setCompanyResearch(null);
  }, [lead]);

  const handleSaveStatus = async (newStatus: string) => {
    setOutreachStatus(newStatus);
    setIsSaving(true);
    try {
      await updateLead(lead.lead_id, { outreach_status: newStatus });
    } catch (err) {
      console.error('Failed to update status:', err);
    } finally {
      setIsSaving(false);
    }
  };

  const handleSaveNotes = async () => {
    setIsSaving(true);
    try {
      await updateLead(lead.lead_id, { notes });
    } catch (err) {
      console.error('Failed to save notes:', err);
    } finally {
      setIsSaving(false);
    }
  };

  const handleResearch = async () => {
    setIsResearching(true);
    try {
      const result = await researchLead(lead.lead_id);
      setCompanyResearch(result);
      // Update lead with any new contact info found
      if (result.phones?.length || result.emails?.length) {
        setEnrichedLead({
          ...enrichedLead,
          phone: result.phones?.[0] || enrichedLead.phone,
          email: result.emails?.[0] || enrichedLead.email,
        });
      }
      // Update AI description if generated
      if (result.ai_description) {
        setAiDescription(result.ai_description);
      }
    } catch (err) {
      console.error('Research failed:', err);
    } finally {
      setIsResearching(false);
    }
  };
  
  const handleGenerateAI = async () => {
    setIsGeneratingAI(true);
    try {
      const result = await generateAiSummary(lead.lead_id);
      if (result.ai_description) {
        setAiDescription(result.ai_description);
        setEnrichedLead({
          ...enrichedLead,
          business_summary: result.ai_description,
        });
      }
    } catch (err) {
      console.error('AI summary generation failed:', err);
    } finally {
      setIsGeneratingAI(false);
    }
  };

  const handleAddOutreachAttempt = async () => {
    if (!newOutreach.method || !newOutreach.outcome) return;
    setIsSaving(true);
    try {
      const result = await addOutreachAttempt(lead.lead_id, newOutreach);
      setOutreachAttempts([result.attempt, ...outreachAttempts]);
      setShowAddOutreach(false);
      setNewOutreach({ method: 'phone', outcome: 'no_answer', notes: '' });
    } catch (err) {
      console.error('Failed to add outreach attempt:', err);
    } finally {
      setIsSaving(false);
    }
  };

  const openWebsite = () => {
    const url = enrichedLead.website || `https://www.google.com/search?q=${encodeURIComponent((enrichedLead.agent_name || enrichedLead.owner_name) + ' property management NYC')}`;
    window.open(url, '_blank');
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

          {/* Building Type Composition */}
          {enrichedLead.building_types && enrichedLead.building_types.total > 0 && (
            <div className="bg-slate-800/30 rounded-xl p-5">
              <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider mb-4">Portfolio Composition</h3>
              <div className="space-y-4">
                {/* Summary Stats First */}
                <div className="grid grid-cols-2 gap-4 pb-4 border-b border-white/5">
                  <div className="text-center">
                    <div className="text-2xl font-bold text-white">{enrichedLead.building_types.total}</div>
                    <div className="text-xs text-slate-500 uppercase tracking-wider">Buildings</div>
                  </div>
                  <div className="text-center">
                    <div className="text-2xl font-bold text-blue-400">{enrichedLead.total_units.toLocaleString()}</div>
                    <div className="text-xs text-slate-500 uppercase tracking-wider">Total Units</div>
                  </div>
                </div>
                
                {/* Visual breakdown bar */}
                <div className="h-6 rounded-lg overflow-hidden flex bg-slate-700/50">
                  {enrichedLead.building_types.condo > 0 && (
                    <div 
                      className="bg-blue-500 flex items-center justify-center text-[10px] text-white font-bold"
                      style={{ width: `${(enrichedLead.building_types.condo / enrichedLead.building_types.total) * 100}%` }}
                      title={`Condo: ${enrichedLead.building_types.condo}`}
                    >
                      {enrichedLead.building_types.condo >= 3 && enrichedLead.building_types.condo}
                    </div>
                  )}
                  {enrichedLead.building_types.coop > 0 && (
                    <div 
                      className="bg-purple-500 flex items-center justify-center text-[10px] text-white font-bold"
                      style={{ width: `${(enrichedLead.building_types.coop / enrichedLead.building_types.total) * 100}%` }}
                      title={`Coop: ${enrichedLead.building_types.coop}`}
                    >
                      {enrichedLead.building_types.coop >= 3 && enrichedLead.building_types.coop}
                    </div>
                  )}
                  {enrichedLead.building_types.rental_elevator > 0 && (
                    <div 
                      className="bg-emerald-500 flex items-center justify-center text-[10px] text-white font-bold"
                      style={{ width: `${(enrichedLead.building_types.rental_elevator / enrichedLead.building_types.total) * 100}%` }}
                      title={`Elevator Rental: ${enrichedLead.building_types.rental_elevator}`}
                    >
                      {enrichedLead.building_types.rental_elevator >= 3 && enrichedLead.building_types.rental_elevator}
                    </div>
                  )}
                  {enrichedLead.building_types.rental_walkup > 0 && (
                    <div 
                      className="bg-amber-500 flex items-center justify-center text-[10px] text-white font-bold"
                      style={{ width: `${(enrichedLead.building_types.rental_walkup / enrichedLead.building_types.total) * 100}%` }}
                      title={`Walk-up Rental: ${enrichedLead.building_types.rental_walkup}`}
                    >
                      {enrichedLead.building_types.rental_walkup >= 3 && enrichedLead.building_types.rental_walkup}
                    </div>
                  )}
                  {enrichedLead.building_types.small_residential > 0 && (
                    <div 
                      className="bg-cyan-500 flex items-center justify-center text-[10px] text-white font-bold"
                      style={{ width: `${(enrichedLead.building_types.small_residential / enrichedLead.building_types.total) * 100}%` }}
                      title={`1-2 Family: ${enrichedLead.building_types.small_residential}`}
                    >
                      {enrichedLead.building_types.small_residential >= 3 && enrichedLead.building_types.small_residential}
                    </div>
                  )}
                  {(enrichedLead.building_types.other + enrichedLead.building_types.unknown) > 0 && (
                    <div 
                      className="bg-slate-600 flex items-center justify-center text-[10px] text-white font-bold"
                      style={{ width: `${((enrichedLead.building_types.other + enrichedLead.building_types.unknown) / enrichedLead.building_types.total) * 100}%` }}
                      title={`Other/Unknown: ${enrichedLead.building_types.other + enrichedLead.building_types.unknown}`}
                    >
                      {(enrichedLead.building_types.other + enrichedLead.building_types.unknown) >= 3 && (enrichedLead.building_types.other + enrichedLead.building_types.unknown)}
                    </div>
                  )}
                </div>
                
                {/* Breakdown Legend */}
                <div className="grid grid-cols-3 gap-2 text-xs">
                  {enrichedLead.building_types.condo > 0 && (
                    <div className="flex items-center gap-2">
                      <span className="w-3 h-3 bg-blue-500 rounded"></span>
                      <span className="text-slate-400">Condo</span>
                      <span className="text-white font-bold ml-auto">{enrichedLead.building_types.condo}</span>
                    </div>
                  )}
                  {enrichedLead.building_types.coop > 0 && (
                    <div className="flex items-center gap-2">
                      <span className="w-3 h-3 bg-purple-500 rounded"></span>
                      <span className="text-slate-400">Coop</span>
                      <span className="text-white font-bold ml-auto">{enrichedLead.building_types.coop}</span>
                    </div>
                  )}
                  {enrichedLead.building_types.rental_elevator > 0 && (
                    <div className="flex items-center gap-2">
                      <span className="w-3 h-3 bg-emerald-500 rounded"></span>
                      <span className="text-slate-400">Elevator</span>
                      <span className="text-white font-bold ml-auto">{enrichedLead.building_types.rental_elevator}</span>
                    </div>
                  )}
                  {enrichedLead.building_types.rental_walkup > 0 && (
                    <div className="flex items-center gap-2">
                      <span className="w-3 h-3 bg-amber-500 rounded"></span>
                      <span className="text-slate-400">Walk-up</span>
                      <span className="text-white font-bold ml-auto">{enrichedLead.building_types.rental_walkup}</span>
                    </div>
                  )}
                  {enrichedLead.building_types.small_residential > 0 && (
                    <div className="flex items-center gap-2">
                      <span className="w-3 h-3 bg-cyan-500 rounded"></span>
                      <span className="text-slate-400">1-2 Family</span>
                      <span className="text-white font-bold ml-auto">{enrichedLead.building_types.small_residential}</span>
                    </div>
                  )}
                  {(enrichedLead.building_types.other + enrichedLead.building_types.unknown) > 0 && (
                    <div className="flex items-center gap-2">
                      <span className="w-3 h-3 bg-slate-600 rounded"></span>
                      <span className="text-slate-400">Other</span>
                      <span className="text-white font-bold ml-auto">{enrichedLead.building_types.other + enrichedLead.building_types.unknown}</span>
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Score Breakdown */}
          {enrichedLead.score_breakdown && (
            <div className="bg-slate-800/30 rounded-xl p-5">
              <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider mb-4">Score Breakdown</h3>
              <div className="space-y-3">
                {/* Portfolio Score */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-slate-400 text-sm">Portfolio Size</span>
                    <span className="text-xs text-slate-600">(50% weight)</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="w-24 h-2 bg-slate-700 rounded-full overflow-hidden">
                      <div 
                        className="h-full bg-blue-500 rounded-full" 
                        style={{ width: `${enrichedLead.score_breakdown.portfolio}%` }}
                      />
                    </div>
                    <span className="text-sm font-mono text-blue-400 w-8 text-right">
                      {enrichedLead.score_breakdown.portfolio.toFixed(0)}
                    </span>
                  </div>
                </div>
                
                {/* Units Score */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-slate-400 text-sm">Total Units</span>
                    <span className="text-xs text-slate-600">(20% weight)</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="w-24 h-2 bg-slate-700 rounded-full overflow-hidden">
                      <div 
                        className="h-full bg-purple-500 rounded-full" 
                        style={{ width: `${enrichedLead.score_breakdown.units}%` }}
                      />
                    </div>
                    <span className="text-sm font-mono text-purple-400 w-8 text-right">
                      {enrichedLead.score_breakdown.units.toFixed(0)}
                    </span>
                  </div>
                </div>
                
                {/* Professional Score */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-slate-400 text-sm">Professional Indicators</span>
                    <span className="text-xs text-slate-600">(15% weight)</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="w-24 h-2 bg-slate-700 rounded-full overflow-hidden">
                      <div 
                        className="h-full bg-amber-500 rounded-full" 
                        style={{ width: `${enrichedLead.score_breakdown.professional}%` }}
                      />
                    </div>
                    <span className="text-sm font-mono text-amber-400 w-8 text-right">
                      {enrichedLead.score_breakdown.professional.toFixed(0)}
                    </span>
                  </div>
                </div>
                
                {/* Contact Score */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-slate-400 text-sm">Contact Info</span>
                    <span className="text-xs text-slate-600">(15% weight)</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="w-24 h-2 bg-slate-700 rounded-full overflow-hidden">
                      <div 
                        className="h-full bg-emerald-500 rounded-full" 
                        style={{ width: `${enrichedLead.score_breakdown.contact}%` }}
                      />
                    </div>
                    <span className="text-sm font-mono text-emerald-400 w-8 text-right">
                      {enrichedLead.score_breakdown.contact.toFixed(0)}
                    </span>
                  </div>
                </div>
                
                {/* Concentration Score (Bonus) */}
                <div className="flex items-center justify-between pt-2 border-t border-white/5">
                  <div className="flex items-center gap-2">
                    <span className="text-slate-400 text-sm">Geographic Focus</span>
                    <span className="text-xs text-slate-600">(+10% bonus)</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="w-24 h-2 bg-slate-700 rounded-full overflow-hidden">
                      <div 
                        className="h-full bg-cyan-500 rounded-full" 
                        style={{ width: `${enrichedLead.score_breakdown.concentration}%` }}
                      />
                    </div>
                    <span className="text-sm font-mono text-cyan-400 w-8 text-right">
                      {enrichedLead.score_breakdown.concentration.toFixed(0)}
                    </span>
                  </div>
                </div>
              </div>
              
              {/* Explanation */}
              <div className="mt-4 pt-3 border-t border-white/5">
                <p className="text-xs text-slate-500 leading-relaxed">
                  <strong className="text-slate-400">How scoring works:</strong> Portfolio size and total units measure scale. 
                  Professional indicators check for LLC/Corp structure and management keywords. 
                  Contact info rewards having phone, email, and website. 
                  Geographic focus gives a bonus for operators concentrated in fewer boroughs (easier to integrate).
                </p>
              </div>
            </div>
          )}

          {/* Quick Actions */}
          <div className="flex gap-2">
            <button
              onClick={openWebsite}
              className="flex-1 flex items-center justify-center gap-2 px-4 py-3 bg-purple-600 text-white text-sm font-medium rounded-xl hover:bg-purple-500 transition-colors"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/>
              </svg>
              {enrichedLead.website ? 'Open Website' : 'Search Google'}
            </button>
            <button
              onClick={handleResearch}
              disabled={isResearching}
              className="flex-1 flex items-center justify-center gap-2 px-4 py-3 bg-emerald-600 text-white text-sm font-medium rounded-xl hover:bg-emerald-500 disabled:opacity-50 transition-colors"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
              </svg>
              {isResearching ? 'Researching...' : 'Deep Research'}
            </button>
          </div>

          {/* Company Research Results */}
          {companyResearch && (
            <div className="bg-emerald-900/20 border border-emerald-500/30 rounded-xl p-5">
              <h3 className="text-sm font-bold text-emerald-400 uppercase tracking-wider mb-4">Company Research</h3>
              <div className="space-y-3 text-sm">
                {companyResearch.owner_names && companyResearch.owner_names.length > 0 && (
                  <div>
                    <span className="text-slate-500">Owner/Principal:</span>
                    <span className="text-white ml-2">{companyResearch.owner_names.join(', ')}</span>
                  </div>
                )}
                {companyResearch.year_established && (
                  <div>
                    <span className="text-slate-500">Established:</span>
                    <span className="text-white ml-2">{companyResearch.year_established}</span>
                  </div>
                )}
                {companyResearch.service_areas && companyResearch.service_areas.length > 0 && (
                  <div>
                    <span className="text-slate-500">Service Areas:</span>
                    <span className="text-white ml-2">{companyResearch.service_areas.join(', ')}</span>
                  </div>
                )}
                {companyResearch.description && (
                  <div>
                    <span className="text-slate-500">About:</span>
                    <p className="text-slate-300 mt-1">{companyResearch.description}</p>
                  </div>
                )}
                {companyResearch.phones && companyResearch.phones.length > 0 && (
                  <div>
                    <span className="text-slate-500">Phones Found:</span>
                    <span className="text-emerald-400 ml-2">{companyResearch.phones.join(', ')}</span>
                  </div>
                )}
                {companyResearch.emails && companyResearch.emails.length > 0 && (
                  <div>
                    <span className="text-slate-500">Emails Found:</span>
                    <span className="text-blue-400 ml-2">{companyResearch.emails.join(', ')}</span>
                  </div>
                )}
                {companyResearch.social_links && Object.keys(companyResearch.social_links).length > 0 && (
                  <div className="flex gap-2 mt-2">
                    {Object.entries(companyResearch.social_links).map(([platform, url]) => (
                      <a key={platform} href={url} target="_blank" rel="noopener noreferrer" 
                         className="px-2 py-1 bg-slate-800 text-slate-400 text-xs rounded hover:text-white">
                        {platform}
                      </a>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Contact Information */}
          <div className="bg-slate-800/30 rounded-xl p-5">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider">Contact Information</h3>
              <button
                onClick={async () => {
                  setIsEnriching(true);
                  try {
                    const result = await enrichLeadContacts(lead.lead_id);
                    // Update local state with new contacts
                    setEnrichedLead({
                      ...enrichedLead,
                      phone: result.phones[0]?.value || enrichedLead.phone,
                      email: result.emails[0]?.value || enrichedLead.email,
                      phones: result.phones,
                      emails: result.emails,
                      website: result.website || enrichedLead.website,
                      website_source: result.website_source,
                      enrichment_status: (result.phones.length || result.emails.length) ? 'complete' : 'partial',
                    });
                    // Save DOS info if found
                    if (result.dos_info) {
                      setDosInfo(result.dos_info);
                    }
                  } catch (err) {
                    console.error('Contact enrichment failed:', err);
                  } finally {
                    setIsEnriching(false);
                  }
                }}
                disabled={isEnriching}
                className="px-3 py-1.5 bg-emerald-600 text-white text-xs font-medium rounded-lg hover:bg-emerald-500 disabled:opacity-50 transition-colors flex items-center gap-2"
              >
                <svg className={`w-3 h-3 ${isEnriching ? 'animate-spin' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
                </svg>
                {isEnriching ? 'Finding...' : 'Find Contacts'}
              </button>
            </div>
            
            {/* Phone Numbers */}
            <div className="space-y-2 mb-4">
              <div className="text-xs text-slate-500 uppercase tracking-wider font-medium">Phone Numbers</div>
              {(enrichedLead.phones && enrichedLead.phones.length > 0) ? (
                enrichedLead.phones.map((phone, idx) => (
                  <div key={idx} className="flex items-center justify-between p-2 bg-slate-900/50 rounded-lg">
                    <div className="flex items-center gap-3">
                      <span className="text-lg">📞</span>
                      <a href={`tel:${phone.value}`} className="text-emerald-400 hover:underline font-mono">
                        {phone.value}
                      </a>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`px-2 py-0.5 text-[10px] rounded font-medium ${
                        phone.confidence >= 80 ? 'bg-emerald-900/50 text-emerald-400' :
                        phone.confidence >= 50 ? 'bg-amber-900/50 text-amber-400' :
                        'bg-slate-800 text-slate-400'
                      }`}>
                        {phone.confidence}% conf
                      </span>
                      {phone.source_url ? (
                        <a 
                          href={phone.source_url} 
                          target="_blank" 
                          rel="noopener noreferrer"
                          className="px-2 py-0.5 text-[10px] rounded bg-blue-900/50 text-blue-400 hover:bg-blue-800/50 transition-colors"
                          title={`Source: ${phone.source}`}
                        >
                          {phone.source === 'google_places' ? 'Google Maps' : 
                           phone.source === 'hunter' ? 'Hunter.io' : 
                           phone.source === 'web_crawl' ? 'Website' : phone.source}
                        </a>
                      ) : (
                        <span className="px-2 py-0.5 text-[10px] rounded bg-slate-800 text-slate-500">
                          {phone.source}
                        </span>
                      )}
                      {phone.verified && (
                        <span className="text-emerald-400" title="Verified">✓</span>
                      )}
                    </div>
                  </div>
                ))
              ) : enrichedLead.phone ? (
                <div className="flex items-center gap-3 p-2 bg-slate-900/50 rounded-lg">
                  <span className="text-lg">📞</span>
                  <a href={`tel:${enrichedLead.phone}`} className="text-emerald-400 hover:underline font-mono">
                    {enrichedLead.phone}
                  </a>
                </div>
              ) : (
                <div className="text-slate-600 text-sm p-2">No phone found - click "Find Contacts" to search</div>
              )}
            </div>
            
            {/* Email Addresses */}
            <div className="space-y-2 mb-4">
              <div className="text-xs text-slate-500 uppercase tracking-wider font-medium">Email Addresses</div>
              {(enrichedLead.emails && enrichedLead.emails.length > 0) ? (
                enrichedLead.emails.map((email, idx) => (
                  <div key={idx} className="flex items-center justify-between p-2 bg-slate-900/50 rounded-lg">
                    <div className="flex items-center gap-3">
                      <span className="text-lg">✉️</span>
                      <a href={`mailto:${email.value}`} className="text-blue-400 hover:underline">
                        {email.value}
                      </a>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`px-2 py-0.5 text-[10px] rounded font-medium ${
                        email.confidence >= 80 ? 'bg-emerald-900/50 text-emerald-400' :
                        email.confidence >= 50 ? 'bg-amber-900/50 text-amber-400' :
                        'bg-slate-800 text-slate-400'
                      }`}>
                        {email.confidence}% conf
                      </span>
                      {email.source_url ? (
                        <a 
                          href={email.source_url} 
                          target="_blank" 
                          rel="noopener noreferrer"
                          className="px-2 py-0.5 text-[10px] rounded bg-blue-900/50 text-blue-400 hover:bg-blue-800/50 transition-colors"
                          title={`Source: ${email.source}`}
                        >
                          {email.source === 'google_places' ? 'Google' : 
                           email.source === 'hunter' ? 'Hunter.io' : 
                           email.source === 'web_crawl' ? 'Website' : email.source}
                        </a>
                      ) : (
                        <span className="px-2 py-0.5 text-[10px] rounded bg-slate-800 text-slate-500">
                          {email.source}
                        </span>
                      )}
                      {email.verified && (
                        <span className="text-emerald-400" title="Verified">✓</span>
                      )}
                    </div>
                  </div>
                ))
              ) : enrichedLead.email ? (
                <div className="flex items-center gap-3 p-2 bg-slate-900/50 rounded-lg">
                  <span className="text-lg">✉️</span>
                  <a href={`mailto:${enrichedLead.email}`} className="text-blue-400 hover:underline">
                    {enrichedLead.email}
                  </a>
                </div>
              ) : (
                <div className="text-slate-600 text-sm p-2">No email found - click "Find Contacts" to search</div>
              )}
            </div>
            
            {/* Website */}
            <div className="space-y-2 mb-4">
              <div className="text-xs text-slate-500 uppercase tracking-wider font-medium">Website</div>
              <div className="flex items-center justify-between p-2 bg-slate-900/50 rounded-lg">
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
                {enrichedLead.website_source && (
                  <span className="px-2 py-0.5 text-[10px] rounded bg-slate-800 text-slate-500">
                    via {enrichedLead.website_source}
                  </span>
                )}
              </div>
            </div>
            
            {/* LinkedIn */}
            <div className="space-y-2 mb-4">
              <div className="text-xs text-slate-500 uppercase tracking-wider font-medium">LinkedIn</div>
              {enrichedLead.linkedin_url ? (
                <div className="flex items-center gap-3 p-2 bg-slate-900/50 rounded-lg">
                  <span className="text-lg">💼</span>
                  <a href={enrichedLead.linkedin_url} target="_blank" rel="noopener noreferrer" className="text-blue-400 hover:underline">
                    Company Page
                  </a>
                </div>
              ) : (
                <div className="text-slate-600 text-sm p-2">No company LinkedIn found</div>
              )}
              
              {enrichedLead.linkedin_people && enrichedLead.linkedin_people.length > 0 && (
                <div className="space-y-1 mt-2">
                  <div className="text-[10px] text-slate-600 uppercase tracking-wider">Key People</div>
                  {enrichedLead.linkedin_people.map((url, i) => (
                    <div key={i} className="flex items-center gap-3 p-2 bg-slate-900/50 rounded-lg">
                      <span className="text-sm">👤</span>
                      <a href={url} target="_blank" rel="noopener noreferrer" className="text-blue-400 hover:underline text-sm truncate">
                        {url.split('/in/')[1]?.split('?')[0] || 'Profile'}
                      </a>
                    </div>
                  ))}
                </div>
              )}
            </div>
            
            {/* Address */}
            {enrichedLead.address && (
              <div className="space-y-2">
                <div className="text-xs text-slate-500 uppercase tracking-wider font-medium">Business Address</div>
                <div className="flex items-center gap-3 p-2 bg-slate-900/50 rounded-lg">
                  <span className="text-lg">📍</span>
                  <span className="text-slate-300">{enrichedLead.address}</span>
                </div>
              </div>
            )}
            
            <div className="mt-4 pt-3 border-t border-white/5 flex items-center gap-2">
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

          {/* NY DOS Corporation Info */}
          {dosInfo && (
            <div className="bg-indigo-900/20 border border-indigo-500/30 rounded-xl p-5">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-sm font-bold text-indigo-400 uppercase tracking-wider">NY Corporation Registry</h3>
                <a 
                  href={dosInfo.lookup_url} 
                  target="_blank" 
                  rel="noopener noreferrer"
                  className="px-3 py-1 bg-indigo-600 text-white text-xs font-medium rounded-lg hover:bg-indigo-500 transition-colors flex items-center gap-1"
                >
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/>
                  </svg>
                  Verify on NY DOS
                </a>
              </div>
              <div className="grid grid-cols-2 gap-4 text-sm">
                <div>
                  <span className="text-slate-500 block text-xs uppercase tracking-wider mb-1">Entity Name</span>
                  <span className="text-white font-medium">{dosInfo.entity_name}</span>
                </div>
                <div>
                  <span className="text-slate-500 block text-xs uppercase tracking-wider mb-1">Entity Type</span>
                  <span className="text-slate-300">{dosInfo.entity_type}</span>
                </div>
                {dosInfo.formation_date && (
                  <div>
                    <span className="text-slate-500 block text-xs uppercase tracking-wider mb-1">Formation Date</span>
                    <span className="text-slate-300">{new Date(dosInfo.formation_date).toLocaleDateString()}</span>
                  </div>
                )}
                <div>
                  <span className="text-slate-500 block text-xs uppercase tracking-wider mb-1">DOS ID</span>
                  <span className="text-slate-400 font-mono text-xs">{dosInfo.dos_id}</span>
                </div>
                {dosInfo.registered_agent && (
                  <div className="col-span-2">
                    <span className="text-slate-500 block text-xs uppercase tracking-wider mb-1">Registered Agent</span>
                    <span className="text-emerald-400">{dosInfo.registered_agent}</span>
                  </div>
                )}
                {dosInfo.registered_address && (
                  <div className="col-span-2">
                    <span className="text-slate-500 block text-xs uppercase tracking-wider mb-1">Registered Address</span>
                    <span className="text-slate-300 text-xs">{dosInfo.registered_address}</span>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Outreach Status */}
          <div className="bg-slate-800/30 rounded-xl p-5">
            <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider mb-3">Outreach Status</h3>
            <div className="flex flex-wrap gap-2">
              {OUTREACH_STATUSES.map((status) => (
                <button
                  key={status.value}
                  onClick={() => handleSaveStatus(status.value)}
                  disabled={isSaving}
                  className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                    outreachStatus === status.value 
                      ? `${status.color} ring-2 ring-offset-2 ring-offset-slate-900 ring-white/20` 
                      : 'bg-slate-800 text-slate-500 hover:bg-slate-700 hover:text-slate-300'
                  }`}
                >
                  {status.label}
                </button>
              ))}
            </div>
          </div>

          {/* Outreach Log */}
          <div className="bg-slate-800/30 rounded-xl p-5">
            <div className="flex justify-between items-center mb-3">
              <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider">Outreach Log</h3>
              <button
                onClick={() => setShowAddOutreach(!showAddOutreach)}
                className="px-3 py-1 bg-blue-600 text-white text-xs font-medium rounded-lg hover:bg-blue-500 transition-colors"
              >
                + Log Attempt
              </button>
            </div>

            {showAddOutreach && (
              <div className="bg-slate-900/50 rounded-lg p-4 mb-4 space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs text-slate-500 block mb-1">Method</label>
                    <select
                      value={newOutreach.method}
                      onChange={(e) => setNewOutreach({ ...newOutreach, method: e.target.value })}
                      className="w-full px-3 py-2 bg-slate-800 border border-white/10 rounded-lg text-sm text-slate-200"
                    >
                      {OUTREACH_METHODS.map((m) => (
                        <option key={m} value={m}>{m.replace('_', ' ')}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="text-xs text-slate-500 block mb-1">Outcome</label>
                    <select
                      value={newOutreach.outcome}
                      onChange={(e) => setNewOutreach({ ...newOutreach, outcome: e.target.value })}
                      className="w-full px-3 py-2 bg-slate-800 border border-white/10 rounded-lg text-sm text-slate-200"
                    >
                      {OUTREACH_OUTCOMES.map((o) => (
                        <option key={o} value={o}>{o.replace(/_/g, ' ')}</option>
                      ))}
                    </select>
                  </div>
                </div>
                <div>
                  <label className="text-xs text-slate-500 block mb-1">Notes</label>
                  <input
                    type="text"
                    value={newOutreach.notes}
                    onChange={(e) => setNewOutreach({ ...newOutreach, notes: e.target.value })}
                    placeholder="Spoke with John, will follow up next week..."
                    className="w-full px-3 py-2 bg-slate-800 border border-white/10 rounded-lg text-sm text-slate-200 placeholder-slate-600"
                  />
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={handleAddOutreachAttempt}
                    disabled={isSaving}
                    className="px-4 py-2 bg-emerald-600 text-white text-xs font-medium rounded-lg hover:bg-emerald-500 disabled:opacity-50"
                  >
                    Save
                  </button>
                  <button
                    onClick={() => setShowAddOutreach(false)}
                    className="px-4 py-2 bg-slate-700 text-slate-300 text-xs font-medium rounded-lg hover:bg-slate-600"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {outreachAttempts.length > 0 ? (
              <div className="space-y-2 max-h-48 overflow-y-auto">
                {outreachAttempts.map((attempt, i) => (
                  <div key={i} className="flex items-start gap-3 p-3 bg-slate-900/50 rounded-lg text-sm">
                    <div className={`px-2 py-0.5 rounded text-xs font-medium ${
                      attempt.method === 'phone' ? 'bg-emerald-900/50 text-emerald-400' :
                      attempt.method === 'email' ? 'bg-blue-900/50 text-blue-400' :
                      'bg-slate-800 text-slate-400'
                    }`}>
                      {attempt.method}
                    </div>
                    <div className="flex-1">
                      <div className="text-slate-300">{attempt.outcome.replace(/_/g, ' ')}</div>
                      {attempt.notes && <div className="text-slate-500 text-xs mt-1">{attempt.notes}</div>}
                    </div>
                    <div className="text-slate-600 text-xs">
                      {new Date(attempt.timestamp).toLocaleDateString()}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-slate-600 text-sm">No outreach attempts logged yet.</p>
            )}
          </div>

          {/* Notes */}
          <div className="bg-slate-800/30 rounded-xl p-5">
            <div className="flex justify-between items-center mb-3">
              <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider">Notes</h3>
              <button
                onClick={handleSaveNotes}
                disabled={isSaving || notes === (lead.notes || '')}
                className="px-3 py-1 bg-blue-600 text-white text-xs font-medium rounded-lg hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {isSaving ? 'Saving...' : 'Save Notes'}
              </button>
            </div>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Add notes about this lead..."
              className="w-full h-24 px-3 py-2 bg-slate-900 border border-white/10 rounded-lg text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:ring-1 focus:ring-blue-500 resize-none"
            />
          </div>

          {/* AI Company Summary */}
          <div className="bg-gradient-to-br from-indigo-900/20 to-purple-900/20 border border-indigo-500/20 rounded-xl p-5">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-bold text-indigo-300 uppercase tracking-wider flex items-center gap-2">
                <span>✨</span> AI Summary
              </h3>
              {!aiDescription && (
                <button
                  onClick={handleGenerateAI}
                  disabled={isGeneratingAI}
                  className="px-3 py-1.5 bg-indigo-600 text-white text-xs font-medium rounded-lg hover:bg-indigo-500 disabled:opacity-50 transition-colors flex items-center gap-2"
                >
                  {isGeneratingAI ? (
                    <>
                      <svg className="w-3 h-3 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
                      </svg>
                      Generating...
                    </>
                  ) : (
                    <>Generate</>
                  )}
                </button>
              )}
            </div>
            {aiDescription ? (
              <p className="text-slate-300 text-sm leading-relaxed">{aiDescription}</p>
            ) : (
              <p className="text-slate-500 text-sm italic">
                Click "Generate" to create an AI-powered summary of this company based on portfolio data and research.
              </p>
            )}
          </div>

          {/* Buildings List */}
          <div className="bg-slate-800/30 rounded-xl p-5">
            <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider mb-3">
              All Buildings ({enrichedLead.buildings.length})
            </h3>
            <div className="space-y-2 max-h-64 overflow-y-auto">
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
