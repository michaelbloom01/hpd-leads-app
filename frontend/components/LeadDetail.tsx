import React, { useState, useEffect } from 'react';
import { toast } from 'react-hot-toast';
import { ApiLead, updateLead, researchLead, addOutreachAttempt, enrichLeadContacts, generateAiSummary, getDueDiligence, CompanyResearch, OutreachAttempt, DOSInfo } from '../services/api';

const PIPELINE_STAGES = [
  { value: 'research', label: 'Research' },
  { value: 'first_contact', label: 'First Contact' },
  { value: 'follow_up', label: 'Follow-Up' },
  { value: 'meeting_scheduled', label: 'Meeting Scheduled' },
  { value: 'meeting_done', label: 'Meeting Done' },
  { value: 'loi', label: 'LOI' },
  { value: 'due_diligence', label: 'Due Diligence' },
  { value: 'closed', label: 'Closed' },
];

const formatCurrency = (amount: number): string => {
  if (amount >= 1_000_000) return `$${(amount / 1_000_000).toFixed(1)}m`;
  if (amount >= 1_000) return `$${(amount / 1_000).toFixed(0)}k`;
  return `$${amount.toFixed(0)}`;
};

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
  const [pipelineStage, setPipelineStage] = useState(lead.pipeline_stage || 'research');
  const [priorityRank, setPriorityRank] = useState(lead.priority_rank || 0);
  const [nextFollowUp, setNextFollowUp] = useState(lead.next_follow_up || '');
  const [showDDModal, setShowDDModal] = useState(false);
  const [ddReport, setDdReport] = useState<{ report_markdown: string; comparables: any[] } | null>(null);
  const [isLoadingDD, setIsLoadingDD] = useState(false);

  // Sync state when lead prop changes
  useEffect(() => {
    setEnrichedLead(lead);
    setNotes(lead.notes || '');
    setOutreachStatus(lead.outreach_status || 'new');
    setOutreachAttempts(lead.outreach_attempts || []);
    setAiDescription(lead.business_summary || null);
    setPipelineStage(lead.pipeline_stage || 'research');
    setPriorityRank(lead.priority_rank || 0);
    setNextFollowUp(lead.next_follow_up || '');
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

  const handlePipelineChange = async (stage: string) => {
    setPipelineStage(stage);
    try {
      await updateLead(lead.lead_id, { pipeline_stage: stage });
      toast.success(`Pipeline → ${PIPELINE_STAGES.find(s => s.value === stage)?.label}`);
    } catch (err) {
      console.error('Failed to update pipeline stage:', err);
      toast.error('Failed to update pipeline stage');
    }
  };

  const handlePriorityChange = async (rank: number) => {
    const newRank = rank === priorityRank ? 0 : rank; // Toggle off if clicking same star
    setPriorityRank(newRank);
    try {
      await updateLead(lead.lead_id, { priority_rank: newRank });
    } catch (err) {
      console.error('Failed to update priority:', err);
    }
  };

  const handleFollowUpChange = async (date: string) => {
    setNextFollowUp(date);
    try {
      await updateLead(lead.lead_id, { next_follow_up: date || null });
      toast.success(date ? `Follow-up set for ${date}` : 'Follow-up cleared');
    } catch (err) {
      console.error('Failed to update follow-up:', err);
      toast.error('Failed to update follow-up');
    }
  };

  const handleGenerateDD = async () => {
    setIsLoadingDD(true);
    setShowDDModal(true);
    try {
      const result = await getDueDiligence(lead.lead_id);
      setDdReport(result);
    } catch (err) {
      console.error('Failed to generate DD report:', err);
      toast.error('Failed to generate due diligence report');
      setShowDDModal(false);
    } finally {
      setIsLoadingDD(false);
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
            <div className="flex items-center gap-2 mb-1">
              <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold uppercase ${
                enrichedLead.entity_type === 'company' ? 'bg-blue-900/50 text-blue-400' :
                enrichedLead.entity_type === 'individual_agent' ? 'bg-amber-900/50 text-amber-400' :
                enrichedLead.entity_type === 'owner_operator' ? 'bg-purple-900/50 text-purple-400' :
                'bg-slate-800 text-slate-500'
              }`}>
                {enrichedLead.entity_type === 'company' ? 'Company' : 
                 enrichedLead.entity_type === 'individual_agent' ? 'Individual Agent' : 
                 enrichedLead.entity_type === 'owner_operator' ? 'Owner-Operator' : 'Unknown'}
              </span>
            </div>
            <h2 className="text-xl font-bold text-white">{enrichedLead.company_name || enrichedLead.agent_name || enrichedLead.owner_name}</h2>
            {enrichedLead.primary_contact && (
              <p className="text-emerald-400 text-sm mt-1">
                {enrichedLead.primary_contact}
                {enrichedLead.primary_contact_title && <span className="text-slate-500"> - {enrichedLead.primary_contact_title}</span>}
              </p>
            )}
            {enrichedLead.agent_name && enrichedLead.owner_name && enrichedLead.agent_name !== enrichedLead.owner_name && (
              <p className="text-slate-500 text-sm mt-0.5">Owner: {enrichedLead.owner_name}</p>
            )}
            <p className="text-slate-600 text-xs mt-1">
              {enrichedLead.owner_type} • {enrichedLead.boros?.length > 0 ? enrichedLead.boros.join(', ') : enrichedLead.boro}
            </p>
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
          {/* CONTACT INFO - FRONT AND CENTER */}
          <div className="bg-gradient-to-br from-emerald-900/30 to-blue-900/30 border border-emerald-500/30 rounded-xl p-5">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-bold text-emerald-400 uppercase tracking-wider flex items-center gap-2">
                <span>📞</span> Contact Information
              </h3>
              <button
                onClick={async () => {
                  setIsEnriching(true);
                  try {
                    const result = await enrichLeadContacts(lead.lead_id);
                    setEnrichedLead({
                      ...enrichedLead,
                      phone: result.phones[0]?.value || enrichedLead.phone,
                      email: result.emails[0]?.value || enrichedLead.email,
                      phones: result.phones,
                      emails: result.emails,
                      website: result.website || enrichedLead.website,
                      enrichment_status: (result.phones.length || result.emails.length) ? 'complete' : 'partial',
                    });
                    if (result.dos_info) setDosInfo(result.dos_info);
                  } catch (err) {
                    console.error('Contact enrichment failed:', err);
                  } finally {
                    setIsEnriching(false);
                  }
                }}
                disabled={isEnriching}
                className="px-3 py-1.5 bg-emerald-600 text-white text-xs font-medium rounded-lg hover:bg-emerald-500 disabled:opacity-50 transition-colors flex items-center gap-2"
              >
                {isEnriching ? (
                  <>
                    <svg className="w-3 h-3 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
                    </svg>
                    Finding...
                  </>
                ) : (
                  <>Find Contacts</>
                )}
              </button>
            </div>
            
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {/* Phone */}
              <div className="bg-slate-900/50 rounded-lg p-4">
                <div className="text-xs text-slate-500 uppercase tracking-wider mb-2">Phone</div>
                {enrichedLead.phone ? (
                  <a 
                    href={`tel:${enrichedLead.phone}`}
                    className="text-lg font-mono text-emerald-400 hover:text-emerald-300 flex items-center gap-2"
                  >
                    <span>📞</span> {enrichedLead.phone}
                  </a>
                ) : (
                  <span className="text-slate-600 text-sm">Not found</span>
                )}
              </div>
              
              {/* Email */}
              <div className="bg-slate-900/50 rounded-lg p-4">
                <div className="text-xs text-slate-500 uppercase tracking-wider mb-2">Email</div>
                {enrichedLead.email ? (
                  <a 
                    href={`mailto:${enrichedLead.email}`}
                    className="text-lg text-blue-400 hover:text-blue-300 flex items-center gap-2 truncate"
                  >
                    <span>✉️</span> {enrichedLead.email}
                  </a>
                ) : (
                  <span className="text-slate-600 text-sm">Not found</span>
                )}
              </div>
              
              {/* Website */}
              <div className="bg-slate-900/50 rounded-lg p-4">
                <div className="text-xs text-slate-500 uppercase tracking-wider mb-2">Website</div>
                {enrichedLead.website ? (
                  <a 
                    href={enrichedLead.website}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-lg text-purple-400 hover:text-purple-300 flex items-center gap-2 truncate"
                  >
                    <span>🌐</span> {new URL(enrichedLead.website).hostname}
                  </a>
                ) : (
                  <span className="text-slate-600 text-sm">Not found</span>
                )}
              </div>
            </div>
            
            {/* Quick Actions */}
            <div className="flex gap-2 mt-4 pt-4 border-t border-white/10">
              {enrichedLead.phone && (
                <button
                  onClick={() => {
                    navigator.clipboard.writeText(enrichedLead.phone || '');
                    toast.success('Phone copied to clipboard');
                  }}
                  className="flex-1 px-3 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 text-sm rounded-lg transition-colors"
                >
                  Copy Phone
                </button>
              )}
              {enrichedLead.email && (
                <button
                  onClick={() => {
                    navigator.clipboard.writeText(enrichedLead.email || '');
                    toast.success('Email copied to clipboard');
                  }}
                  className="flex-1 px-3 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 text-sm rounded-lg transition-colors"
                >
                  Copy Email
                </button>
              )}
              <button
                onClick={openWebsite}
                className="flex-1 px-3 py-2 bg-purple-600 hover:bg-purple-500 text-white text-sm rounded-lg transition-colors"
              >
                {enrichedLead.website ? 'Visit Website' : 'Search Google'}
              </button>
            </div>
          </div>

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
              <div className="text-3xl font-bold font-mono text-purple-400">{enrichedLead.total_units.toLocaleString()}</div>
              <div className="text-xs text-slate-500 mt-1 uppercase tracking-wider">Units</div>
            </div>
          </div>

          {/* Revenue & Violations */}
          {(enrichedLead.estimated_annual_revenue > 0 || enrichedLead.violation_count > 0) && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Revenue */}
              <div className="bg-gradient-to-br from-emerald-900/20 to-emerald-950/20 border border-emerald-500/20 rounded-xl p-5">
                <h3 className="text-xs font-bold text-emerald-400 uppercase tracking-wider mb-3">Est. Revenue</h3>
                {enrichedLead.estimated_annual_revenue > 0 ? (
                  <div className="space-y-2">
                    <div className="text-2xl font-bold font-mono text-emerald-400">
                      {formatCurrency(enrichedLead.estimated_annual_revenue)}<span className="text-sm text-emerald-600">/yr</span>
                    </div>
                    <div className="text-sm font-mono text-slate-400">
                      {formatCurrency(enrichedLead.estimated_monthly_revenue)}<span className="text-xs text-slate-600">/mo</span>
                    </div>
                    <p className="text-[10px] text-slate-600 mt-2">Based on unit count, borough, and building type with 5% mgmt fee assumption</p>
                  </div>
                ) : (
                  <div className="text-slate-600 text-sm">Not estimated yet</div>
                )}
              </div>

              {/* Violations */}
              <div className={`bg-gradient-to-br ${
                enrichedLead.violations_per_unit > 1.0 ? 'from-rose-900/20 to-rose-950/20 border-rose-500/20' :
                enrichedLead.violation_count > 0 ? 'from-amber-900/20 to-amber-950/20 border-amber-500/20' :
                'from-slate-900/20 to-slate-950/20 border-white/5'
              } border rounded-xl p-5`}>
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider">HPD Violations</h3>
                  {enrichedLead.violations_per_unit > 1.0 && (
                    <span className="px-2 py-0.5 bg-rose-600/30 text-rose-400 text-[10px] font-bold rounded uppercase">High Distress</span>
                  )}
                </div>
                {enrichedLead.violation_count > 0 ? (
                  <div className="space-y-3">
                    <div className="text-2xl font-bold font-mono text-white">{enrichedLead.violation_count.toLocaleString()}</div>
                    <div className="space-y-1.5">
                      <div className="flex items-center justify-between text-xs">
                        <span className="text-slate-500">Class A (Non-hazardous)</span>
                        <span className="font-mono text-slate-400">{enrichedLead.violation_class_a}</span>
                      </div>
                      <div className="h-1.5 bg-slate-700 rounded-full overflow-hidden">
                        <div className="h-full bg-slate-400 rounded-full" style={{ width: `${enrichedLead.violation_count > 0 ? (enrichedLead.violation_class_a / enrichedLead.violation_count) * 100 : 0}%` }} />
                      </div>
                      <div className="flex items-center justify-between text-xs">
                        <span className="text-amber-500">Class B (Hazardous)</span>
                        <span className="font-mono text-amber-400">{enrichedLead.violation_class_b}</span>
                      </div>
                      <div className="h-1.5 bg-slate-700 rounded-full overflow-hidden">
                        <div className="h-full bg-amber-500 rounded-full" style={{ width: `${enrichedLead.violation_count > 0 ? (enrichedLead.violation_class_b / enrichedLead.violation_count) * 100 : 0}%` }} />
                      </div>
                      <div className="flex items-center justify-between text-xs">
                        <span className="text-rose-500">Class C (Immediately Hazardous)</span>
                        <span className="font-mono text-rose-400">{enrichedLead.violation_class_c}</span>
                      </div>
                      <div className="h-1.5 bg-slate-700 rounded-full overflow-hidden">
                        <div className="h-full bg-rose-500 rounded-full" style={{ width: `${enrichedLead.violation_count > 0 ? (enrichedLead.violation_class_c / enrichedLead.violation_count) * 100 : 0}%` }} />
                      </div>
                    </div>
                    <div className="pt-2 border-t border-white/5 text-xs text-slate-500">
                      <span className="font-mono text-slate-300">{enrichedLead.violations_per_unit.toFixed(2)}</span> violations per unit
                    </div>
                  </div>
                ) : (
                  <div className="text-slate-600 text-sm">No violations on record</div>
                )}
              </div>
            </div>
          )}

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

          {/* Generate DD Report Button */}
          <button
            onClick={handleGenerateDD}
            disabled={isLoadingDD}
            className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-gradient-to-r from-indigo-600 to-purple-600 text-white text-sm font-medium rounded-xl hover:from-indigo-500 hover:to-purple-500 disabled:opacity-50 transition-all"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
            </svg>
            {isLoadingDD ? 'Generating DD Report...' : 'Generate Due Diligence Report'}
          </button>

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

          {/* Pipeline Manager */}
          <div className="bg-slate-800/30 rounded-xl p-5 space-y-5">
            <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider">Deal Pipeline</h3>
            
            {/* Pipeline Stage Selector */}
            <div>
              <div className="flex gap-0.5 overflow-x-auto pb-1">
                {PIPELINE_STAGES.map((stage, idx) => {
                  const isActive = stage.value === pipelineStage;
                  const currentIdx = PIPELINE_STAGES.findIndex(s => s.value === pipelineStage);
                  const isPast = idx < currentIdx;
                  return (
                    <button
                      key={stage.value}
                      onClick={() => handlePipelineChange(stage.value)}
                      className={`flex-1 min-w-[80px] px-2 py-2 text-[10px] font-bold uppercase tracking-wider transition-all border-b-2 ${
                        isActive
                          ? 'bg-blue-600/30 text-blue-300 border-blue-400'
                          : isPast
                          ? 'bg-emerald-600/10 text-emerald-500 border-emerald-500/50'
                          : 'bg-slate-800/50 text-slate-600 border-slate-700 hover:bg-slate-700/50 hover:text-slate-400'
                      } ${idx === 0 ? 'rounded-l-lg' : ''} ${idx === PIPELINE_STAGES.length - 1 ? 'rounded-r-lg' : ''}`}
                    >
                      {stage.label}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Priority & Follow-Up Row */}
            <div className="grid grid-cols-2 gap-4">
              {/* Priority Stars */}
              <div>
                <label className="text-xs text-slate-500 uppercase tracking-wider block mb-2">Priority</label>
                <div className="flex gap-1">
                  {[1, 2, 3, 4, 5].map((star) => (
                    <button
                      key={star}
                      onClick={() => handlePriorityChange(star)}
                      className="transition-transform hover:scale-110"
                    >
                      <svg className={`w-6 h-6 ${star <= priorityRank ? 'text-amber-400' : 'text-slate-700'}`} fill="currentColor" viewBox="0 0 20 20">
                        <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
                      </svg>
                    </button>
                  ))}
                </div>
              </div>

              {/* Follow-Up Date */}
              <div>
                <label className="text-xs text-slate-500 uppercase tracking-wider block mb-2">Next Follow-Up</label>
                <div className="relative">
                  <input
                    type="date"
                    value={nextFollowUp}
                    onChange={(e) => handleFollowUpChange(e.target.value)}
                    className={`w-full px-3 py-1.5 bg-slate-800 border rounded-lg text-sm font-mono ${
                      nextFollowUp && new Date(nextFollowUp) < new Date(new Date().toDateString())
                        ? 'border-rose-500/50 text-rose-400'
                        : nextFollowUp && new Date(nextFollowUp).toDateString() === new Date().toDateString()
                        ? 'border-amber-500/50 text-amber-400'
                        : 'border-slate-700 text-slate-300'
                    }`}
                  />
                  {nextFollowUp && new Date(nextFollowUp) < new Date(new Date().toDateString()) && (
                    <span className="absolute -top-2 right-2 px-1.5 py-0.5 bg-rose-600 text-[9px] font-bold text-white rounded">OVERDUE</span>
                  )}
                </div>
              </div>
            </div>
            
            {/* Outreach Status (kept below pipeline) */}
            <div className="pt-3 border-t border-white/5">
              <label className="text-xs text-slate-500 uppercase tracking-wider block mb-2">Outreach Status</label>
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

          {/* Buildings Map */}
          {enrichedLead.buildings.length > 0 && (
            <div className="bg-slate-800/30 rounded-xl p-5">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider flex items-center gap-2">
                  <span>🗺️</span> Portfolio Footprint
                </h3>
                <a
                  href={`https://www.google.com/maps/search/${encodeURIComponent(
                    (enrichedLead.agent_name || enrichedLead.owner_name) + ' buildings ' + (enrichedLead.boros?.[0] || enrichedLead.boro) + ', NYC'
                  )}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="px-3 py-1.5 bg-blue-600 text-white text-xs font-medium rounded-lg hover:bg-blue-500 transition-colors flex items-center gap-1"
                >
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/>
                  </svg>
                  View All on Maps
                </a>
              </div>
              {/* Static Map showing multiple building markers */}
              <div className="rounded-lg overflow-hidden border border-white/10">
                {import.meta.env.VITE_GOOGLE_MAPS_KEY ? (
                  <img
                    src={`https://maps.googleapis.com/maps/api/staticmap?size=600x300&scale=2&maptype=roadmap&center=40.7128,-73.95&zoom=11${
                      // Add markers for up to 25 buildings (API limit is ~8KB URL)
                      enrichedLead.buildings.slice(0, 25).map((addr, i) => 
                        `&markers=color:${i === 0 ? 'red' : 'blue'}%7Csize:small%7C${encodeURIComponent(addr + ', New York, NY')}`
                      ).join('')
                    }&key=${(import.meta.env.VITE_GOOGLE_MAPS_KEY || '').trim().replace(/\\r\\n$/, '').replace(/[\r\n]+$/, '')}`}
                    alt={`Map showing ${Math.min(enrichedLead.buildings.length, 25)} building locations`}
                    className="w-full h-[250px] object-cover"
                    loading="lazy"
                  />
                ) : (
                  <div className="h-[250px] bg-slate-800 flex items-center justify-center text-slate-500 text-sm">
                    Map unavailable - API key not configured
                  </div>
                )}
              </div>
              <p className="text-slate-500 text-xs mt-2">
                {enrichedLead.buildings.length <= 25 ? (
                  <>Showing all {enrichedLead.buildings.length} buildings across {enrichedLead.boros?.join(', ') || enrichedLead.boro}</>
                ) : (
                  <>Showing 25 of {enrichedLead.buildings.length} buildings across {enrichedLead.boros?.join(', ') || enrichedLead.boro} (first marker in red)</>
                )}
              </p>
            </div>
          )}

          {/* HPD Contacts (deduplicated) */}
          {enrichedLead.contacts && enrichedLead.contacts.length > 0 && (() => {
            // Deduplicate contacts by name (case-insensitive), keeping the first occurrence
            const seen = new Set<string>();
            const uniqueContacts = enrichedLead.contacts.filter(contact => {
              const key = (contact.name || '').trim().toUpperCase();
              if (seen.has(key)) return false;
              seen.add(key);
              return true;
            });
            return (
              <div className="bg-slate-800/30 rounded-xl p-5">
                <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider mb-3">
                  HPD Registered Contacts ({uniqueContacts.length})
                </h3>
                <div className="space-y-2 max-h-48 overflow-y-auto">
                  {uniqueContacts.map((contact, i) => (
                    <div key={i} className="flex items-center justify-between text-sm py-1.5 px-3 bg-slate-900/50 rounded">
                      <div>
                        <span className="text-slate-300">{contact.name}</span>
                        {contact.title && <span className="text-slate-600 ml-2 text-xs">{contact.title}</span>}
                      </div>
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                        contact.type === 'Agent' ? 'bg-blue-900/40 text-blue-400' :
                        contact.type === 'CorporateOwner' ? 'bg-purple-900/40 text-purple-400' :
                        contact.type === 'IndividualOwner' ? 'bg-amber-900/40 text-amber-400' :
                        contact.type === 'SiteManager' ? 'bg-emerald-900/40 text-emerald-400' :
                        'bg-slate-800 text-slate-500'
                      }`}>
                        {contact.type === 'CorporateOwner' ? 'Corporate Owner' :
                         contact.type === 'IndividualOwner' ? 'Individual Owner' :
                         contact.type === 'SiteManager' ? 'Site Manager' :
                         contact.type}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            );
          })()}

          {/* Buildings List */}
          <div className="bg-slate-800/30 rounded-xl p-5">
            <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider mb-3">
              All Buildings ({enrichedLead.buildings.length})
            </h3>
            <div className="space-y-2 max-h-64 overflow-y-auto">
              {enrichedLead.buildings.map((building, i) => (
                <div key={i} className="text-sm text-slate-400 py-1 px-2 bg-slate-900/50 rounded flex items-center justify-between group">
                  <span>{building}</span>
                  <a
                    href={`https://www.google.com/maps/search/${encodeURIComponent(building + ', New York, NY')}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="opacity-0 group-hover:opacity-100 text-blue-400 hover:text-blue-300 transition-opacity"
                    title="View on map"
                  >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"/>
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"/>
                    </svg>
                  </a>
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
      {/* Due Diligence Modal */}
      {showDDModal && (
        <div className="fixed inset-0 bg-black/80 z-[60] flex items-center justify-center p-4" onClick={() => setShowDDModal(false)}>
          <div className="bg-slate-900 border border-slate-700 rounded-2xl w-full max-w-4xl max-h-[90vh] flex flex-col" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between p-5 border-b border-slate-800">
              <h2 className="text-lg font-bold text-white">Due Diligence Report</h2>
              <div className="flex gap-2">
                {ddReport && (
                  <button
                    onClick={() => {
                      navigator.clipboard.writeText(ddReport.report_markdown);
                      toast.success('Report copied to clipboard');
                    }}
                    className="px-3 py-1.5 bg-slate-700 text-slate-300 text-xs font-medium rounded-lg hover:bg-slate-600 transition-colors"
                  >
                    Copy Markdown
                  </button>
                )}
                <button onClick={() => setShowDDModal(false)} className="px-3 py-1.5 bg-slate-800 text-slate-400 text-xs rounded-lg hover:bg-slate-700">
                  Close
                </button>
              </div>
            </div>
            <div className="p-5 overflow-y-auto flex-1">
              {isLoadingDD ? (
                <div className="flex flex-col items-center justify-center py-20">
                  <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin mb-4" />
                  <p className="text-slate-400 text-sm">Generating due diligence report...</p>
                  <p className="text-slate-600 text-xs mt-1">This may take 15-30 seconds</p>
                </div>
              ) : ddReport ? (
                <div className="space-y-6">
                  <div className="prose prose-invert prose-sm max-w-none">
                    {ddReport.report_markdown.split('\n').map((line, i) => {
                      if (line.startsWith('# ')) return <h1 key={i} className="text-xl font-bold text-white mt-6 mb-2">{line.slice(2)}</h1>;
                      if (line.startsWith('## ')) return <h2 key={i} className="text-lg font-bold text-blue-400 mt-5 mb-2">{line.slice(3)}</h2>;
                      if (line.startsWith('### ')) return <h3 key={i} className="text-sm font-bold text-slate-300 mt-4 mb-1">{line.slice(4)}</h3>;
                      if (line.startsWith('- ')) return <li key={i} className="text-slate-300 text-sm ml-4">{line.slice(2)}</li>;
                      if (line.startsWith('**') && line.endsWith('**')) return <p key={i} className="text-white font-bold text-sm">{line.slice(2, -2)}</p>;
                      if (line.trim() === '') return <br key={i} />;
                      return <p key={i} className="text-slate-400 text-sm leading-relaxed">{line}</p>;
                    })}
                  </div>
                  {ddReport.comparables && ddReport.comparables.length > 0 && (
                    <div className="border-t border-slate-800 pt-4">
                      <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider mb-3">Comparable Leads</h3>
                      <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                          <thead>
                            <tr className="text-slate-500 text-xs uppercase">
                              <th className="text-left py-2 px-3">Name</th>
                              <th className="text-right py-2 px-3">Score</th>
                              <th className="text-right py-2 px-3">Buildings</th>
                              <th className="text-right py-2 px-3">Units</th>
                              <th className="text-right py-2 px-3">Revenue</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-800">
                            {ddReport.comparables.map((comp: any, i: number) => (
                              <tr key={i} className="text-slate-300">
                                <td className="py-2 px-3 font-medium">{comp.name || comp.lead_name}</td>
                                <td className="py-2 px-3 text-right font-mono">{comp.score?.toFixed(1)}</td>
                                <td className="py-2 px-3 text-right font-mono">{comp.portfolio_size || comp.buildings}</td>
                                <td className="py-2 px-3 text-right font-mono">{comp.total_units?.toLocaleString() || comp.units}</td>
                                <td className="py-2 px-3 text-right font-mono text-emerald-400">{comp.estimated_annual_revenue ? formatCurrency(comp.estimated_annual_revenue) : '—'}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div className="text-slate-500 text-center py-10">No report data</div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default LeadDetail;
