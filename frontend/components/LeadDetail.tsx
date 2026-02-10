import React, { useState, useEffect } from 'react';
import { toast } from 'react-hot-toast';
import { ApiLead, updateLead, researchLead, addOutreachAttempt, enrichLeadContacts, generateAiSummary, getDueDiligence, CompanyResearch, OutreachAttempt, DOSInfo } from '../services/api';

const PIPELINE_STAGES = [
  { value: 'research', label: 'Research' },
  { value: 'first_contact', label: 'First Contact' },
  { value: 'follow_up', label: 'Follow-Up' },
  { value: 'meeting_scheduled', label: 'Meeting Set' },
  { value: 'meeting_done', label: 'Meeting Done' },
  { value: 'loi', label: 'LOI' },
  { value: 'due_diligence', label: 'Due Diligence' },
  { value: 'closed', label: 'Closed' },
];

const OUTREACH_STATUSES = [
  { value: 'new', label: 'New', color: 'bg-slate-600 text-slate-200' },
  { value: 'contacted', label: 'Contacted', color: 'bg-blue-600 text-blue-100' },
  { value: 'interested', label: 'Interested', color: 'bg-emerald-600 text-emerald-100' },
  { value: 'not_interested', label: 'Not Interested', color: 'bg-amber-600 text-amber-100' },
  { value: 'closed', label: 'Closed', color: 'bg-purple-600 text-purple-100' },
];

const OUTREACH_METHODS = ['phone', 'email', 'linkedin', 'in_person', 'other'];
const OUTREACH_OUTCOMES = ['no_answer', 'left_voicemail', 'spoke_with_contact', 'sent_email', 'meeting_scheduled', 'not_interested', 'other'];

const formatCurrency = (amount: number): string => {
  if (amount >= 1_000_000) return `$${(amount / 1_000_000).toFixed(1)}m`;
  if (amount >= 1_000) return `$${(amount / 1_000).toFixed(0)}k`;
  return `$${amount.toFixed(0)}`;
};

type TabId = 'overview' | 'contacts' | 'pipeline' | 'buildings' | 'dd';

interface Props {
  lead: ApiLead;
  onClose: () => void;
}

const LeadDetail: React.FC<Props> = ({ lead, onClose }) => {
  const [activeTab, setActiveTab] = useState<TabId>('overview');
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
  const [ddReport, setDdReport] = useState<{ report_markdown: string; comparables: any[] } | null>(null);
  const [isLoadingDD, setIsLoadingDD] = useState(false);
  const [buildingSearch, setBuildingSearch] = useState('');

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
    setActiveTab('overview');
  }, [lead]);

  // === Handlers ===
  const handleSaveStatus = async (newStatus: string) => {
    setOutreachStatus(newStatus);
    setIsSaving(true);
    try { await updateLead(lead.lead_id, { outreach_status: newStatus }); } 
    catch (err) { console.error('Failed to update status:', err); } 
    finally { setIsSaving(false); }
  };

  const handleSaveNotes = async () => {
    setIsSaving(true);
    try { await updateLead(lead.lead_id, { notes }); toast.success('Notes saved'); } 
    catch (err) { console.error('Failed to save notes:', err); toast.error('Failed to save notes'); } 
    finally { setIsSaving(false); }
  };

  const handlePipelineChange = async (stage: string) => {
    setPipelineStage(stage);
    try {
      await updateLead(lead.lead_id, { pipeline_stage: stage });
      toast.success(`Pipeline: ${PIPELINE_STAGES.find(s => s.value === stage)?.label}`);
    } catch (err) { console.error('Failed to update pipeline:', err); toast.error('Failed to update pipeline'); }
  };

  const handlePriorityChange = async (rank: number) => {
    const newRank = rank === priorityRank ? 0 : rank;
    setPriorityRank(newRank);
    try { await updateLead(lead.lead_id, { priority_rank: newRank }); } 
    catch (err) { console.error('Failed to update priority:', err); }
  };

  const handleFollowUpChange = async (date: string) => {
    setNextFollowUp(date);
    try {
      await updateLead(lead.lead_id, { next_follow_up: date || null });
      toast.success(date ? `Follow-up: ${date}` : 'Follow-up cleared');
    } catch (err) { console.error('Failed to update follow-up:', err); toast.error('Failed to update'); }
  };

  const handleGenerateDD = async () => {
    setIsLoadingDD(true);
    try { const result = await getDueDiligence(lead.lead_id); setDdReport(result); } 
    catch (err) { console.error('DD report failed:', err); toast.error('Failed to generate DD report'); } 
    finally { setIsLoadingDD(false); }
  };

  const handleResearch = async () => {
    setIsResearching(true);
    try {
      const result = await researchLead(lead.lead_id);
      setCompanyResearch(result);
      if (result.phones?.length || result.emails?.length) {
        setEnrichedLead({ ...enrichedLead, phone: result.phones?.[0] || enrichedLead.phone, email: result.emails?.[0] || enrichedLead.email });
      }
      if (result.ai_description) setAiDescription(result.ai_description);
      toast.success('Research complete');
    } catch (err) { console.error('Research failed:', err); toast.error('Research failed'); } 
    finally { setIsResearching(false); }
  };

  const handleGenerateAI = async () => {
    setIsGeneratingAI(true);
    try {
      const result = await generateAiSummary(lead.lead_id);
      if (result.ai_description) { setAiDescription(result.ai_description); setEnrichedLead({ ...enrichedLead, business_summary: result.ai_description }); }
    } catch (err) { console.error('AI summary failed:', err); } 
    finally { setIsGeneratingAI(false); }
  };

  const handleEnrichContacts = async () => {
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
      toast.success('Contact enrichment complete');
    } catch (err) { console.error('Enrichment failed:', err); toast.error('Contact enrichment failed'); } 
    finally { setIsEnriching(false); }
  };

  const handleAddOutreachAttempt = async () => {
    if (!newOutreach.method || !newOutreach.outcome) return;
    setIsSaving(true);
    try {
      const result = await addOutreachAttempt(lead.lead_id, newOutreach);
      setOutreachAttempts([result.attempt, ...outreachAttempts]);
      setShowAddOutreach(false);
      setNewOutreach({ method: 'phone', outcome: 'no_answer', notes: '' });
    } catch (err) { console.error('Failed to add outreach:', err); } 
    finally { setIsSaving(false); }
  };

  const openWebsite = () => {
    let url = enrichedLead.website;
    if (url && !url.startsWith('http')) url = `https://${url}`;
    if (!url) url = `https://www.google.com/search?q=${encodeURIComponent((enrichedLead.agent_name || enrichedLead.owner_name) + ' property management NYC')}`;
    window.open(url, '_blank');
  };

  // Deduplicate HPD contacts by name
  const uniqueContacts = enrichedLead.contacts ? 
    enrichedLead.contacts.filter((c: any, i: number, arr: any[]) => 
      arr.findIndex(x => (x.name || '').toLowerCase() === (c.name || '').toLowerCase()) === i
    ) : [];

  const TABS: { id: TabId; label: string }[] = [
    { id: 'overview', label: 'Overview' },
    { id: 'contacts', label: 'Contacts' },
    { id: 'pipeline', label: 'Pipeline' },
    { id: 'buildings', label: `Buildings (${enrichedLead.portfolio_size})` },
    { id: 'dd', label: 'Due Diligence' },
  ];

  return (
    <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-slate-900 border border-white/10 rounded-2xl max-w-3xl w-full max-h-[90vh] flex flex-col shadow-2xl" onClick={e => e.stopPropagation()}>
        
        {/* === STICKY HEADER === */}
        <div className="flex-shrink-0 border-b border-white/10">
          {/* Top row: name + close */}
          <div className="p-5 pb-3 flex justify-between items-start">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1 flex-wrap">
                <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold uppercase ${
                  enrichedLead.entity_type === 'company' ? 'bg-blue-900/50 text-blue-400' :
                  enrichedLead.entity_type === 'individual_agent' ? 'bg-amber-900/50 text-amber-400' :
                  enrichedLead.entity_type === 'owner_operator' ? 'bg-purple-900/50 text-purple-400' :
                  'bg-slate-800 text-slate-500'
                }`}>
                  {enrichedLead.entity_type === 'company' ? 'Company' : 
                   enrichedLead.entity_type === 'individual_agent' ? 'Individual' : 
                   enrichedLead.entity_type === 'owner_operator' ? 'Owner-Op' : 'Unknown'}
                </span>
                {(enrichedLead.boros || [enrichedLead.boro]).map((b, i) => (
                  <span key={i} className="px-1.5 py-0.5 bg-slate-800 text-slate-400 text-[10px] rounded">
                    {b ? b.charAt(0) + b.slice(1).toLowerCase() : ''}
                  </span>
                ))}
              </div>
              <h2 className="text-xl font-bold text-white truncate">{enrichedLead.company_name || enrichedLead.agent_name || enrichedLead.owner_name}</h2>
            </div>
            <div className="flex items-center gap-3 ml-4">
              {/* Score */}
              <div className={`text-2xl font-bold font-mono px-3 py-1 rounded-lg ${
                enrichedLead.score >= 60 ? 'bg-emerald-900/30 text-emerald-400' :
                enrichedLead.score >= 40 ? 'bg-amber-900/30 text-amber-400' : 'bg-slate-800 text-slate-400'
              }`}>
                {enrichedLead.score.toFixed(0)}
              </div>
              {/* Revenue */}
              {enrichedLead.estimated_annual_revenue > 0 && (
                <div className="text-lg font-bold font-mono text-emerald-400">
                  {formatCurrency(enrichedLead.estimated_annual_revenue)}<span className="text-xs text-emerald-600">/yr</span>
                </div>
              )}
              <button onClick={onClose} className="p-2 text-slate-500 hover:text-white transition-colors">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12"/>
                </svg>
              </button>
            </div>
          </div>

          {/* Quick actions + pipeline in header */}
          <div className="px-5 pb-3 flex items-center gap-2 flex-wrap">
            {enrichedLead.phone && (
              <a href={`tel:${enrichedLead.phone}`} className="px-3 py-1.5 bg-emerald-600 text-white text-xs font-bold rounded-lg hover:bg-emerald-500 transition-colors flex items-center gap-1">
                <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z"/></svg>
                Call
              </a>
            )}
            {enrichedLead.email && (
              <a href={`mailto:${enrichedLead.email}`} className="px-3 py-1.5 bg-blue-600 text-white text-xs font-bold rounded-lg hover:bg-blue-500 transition-colors flex items-center gap-1">
                <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/></svg>
                Email
              </a>
            )}
            <button onClick={openWebsite} className="px-3 py-1.5 bg-purple-600 text-white text-xs font-bold rounded-lg hover:bg-purple-500 transition-colors">
              {enrichedLead.website ? 'Website' : 'Search'}
            </button>
            <button onClick={handleResearch} disabled={isResearching} className="px-3 py-1.5 bg-slate-700 text-slate-300 text-xs font-bold rounded-lg hover:bg-slate-600 disabled:opacity-50 transition-colors">
              {isResearching ? 'Researching...' : 'Deep Research'}
            </button>
            <div className="flex-1" />
            {/* Pipeline selector inline */}
            <div className="flex gap-0.5">
              {PIPELINE_STAGES.map((stage, idx) => {
                const isActive = stage.value === pipelineStage;
                const currentIdx = PIPELINE_STAGES.findIndex(s => s.value === pipelineStage);
                const isPast = idx < currentIdx;
                return (
                  <button key={stage.value} onClick={() => handlePipelineChange(stage.value)}
                    className={`px-2 py-1 text-[9px] font-bold uppercase tracking-wider transition-all ${
                      isActive ? 'bg-blue-600/40 text-blue-300 border-b-2 border-blue-400' :
                      isPast ? 'bg-emerald-600/20 text-emerald-500' :
                      'bg-slate-800/50 text-slate-600 hover:text-slate-400'
                    } ${idx === 0 ? 'rounded-l' : ''} ${idx === PIPELINE_STAGES.length - 1 ? 'rounded-r' : ''}`}>
                    {stage.label}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Tab bar */}
          <div className="flex border-t border-white/5">
            {TABS.map(tab => (
              <button key={tab.id} onClick={() => setActiveTab(tab.id)}
                className={`flex-1 px-4 py-2.5 text-xs font-bold uppercase tracking-wider transition-colors ${
                  activeTab === tab.id ? 'text-white border-b-2 border-blue-500 bg-slate-800/30' : 'text-slate-600 hover:text-slate-400'
                }`}>
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        {/* === SCROLLABLE TAB CONTENT === */}
        <div className="flex-1 overflow-y-auto p-5 space-y-5">

          {/* TAB: OVERVIEW */}
          {activeTab === 'overview' && (
            <>
              {/* Revenue & Violations */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="bg-gradient-to-br from-emerald-900/20 to-emerald-950/20 border border-emerald-500/20 rounded-xl p-4">
                  <h3 className="text-xs font-bold text-emerald-400 uppercase tracking-wider mb-2">Est. Revenue</h3>
                  {enrichedLead.estimated_annual_revenue > 0 ? (
                    <div>
                      <div className="text-2xl font-bold font-mono text-emerald-400">{formatCurrency(enrichedLead.estimated_annual_revenue)}<span className="text-sm text-emerald-600">/yr</span></div>
                      <div className="text-sm font-mono text-slate-400 mt-1">{formatCurrency(enrichedLead.estimated_monthly_revenue)}<span className="text-xs text-slate-600">/mo</span></div>
                    </div>
                  ) : (
                    <div className="text-slate-600 text-sm">Pending computation</div>
                  )}
                </div>
                <div className={`bg-gradient-to-br ${
                  enrichedLead.violations_per_unit > 1.0 ? 'from-rose-900/20 to-rose-950/20 border-rose-500/20' :
                  enrichedLead.violation_count > 0 ? 'from-amber-900/20 to-amber-950/20 border-amber-500/20' :
                  'from-slate-900/20 to-slate-950/20 border-white/5'
                } border rounded-xl p-4`}>
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider">HPD Violations</h3>
                    {enrichedLead.violations_per_unit > 1.0 && (
                      <span className="px-2 py-0.5 bg-rose-600/30 text-rose-400 text-[10px] font-bold rounded uppercase">High Distress</span>
                    )}
                  </div>
                  {enrichedLead.violation_count > 0 ? (
                    <div>
                      <div className="text-2xl font-bold font-mono text-white">{enrichedLead.violation_count.toLocaleString()}</div>
                      <div className="flex gap-3 mt-2 text-xs">
                        <span className="text-slate-400">A: {enrichedLead.violation_class_a}</span>
                        <span className="text-amber-400">B: {enrichedLead.violation_class_b}</span>
                        <span className="text-rose-400">C: {enrichedLead.violation_class_c}</span>
                      </div>
                      <div className="text-xs text-slate-500 mt-1"><span className="font-mono text-slate-300">{enrichedLead.violations_per_unit.toFixed(2)}</span> per unit</div>
                    </div>
                  ) : (
                    <div className="text-slate-600 text-sm">No violations on record</div>
                  )}
                </div>
              </div>

              {/* Portfolio Summary */}
              <div className="grid grid-cols-3 gap-3">
                <div className="bg-slate-800/50 rounded-xl p-3 text-center">
                  <div className={`text-2xl font-bold font-mono ${enrichedLead.score >= 60 ? 'text-emerald-400' : enrichedLead.score >= 40 ? 'text-amber-400' : 'text-slate-400'}`}>{enrichedLead.score.toFixed(1)}</div>
                  <div className="text-[10px] text-slate-500 mt-0.5 uppercase">Score</div>
                </div>
                <div className="bg-slate-800/50 rounded-xl p-3 text-center">
                  <div className="text-2xl font-bold font-mono text-blue-400">{enrichedLead.portfolio_size}</div>
                  <div className="text-[10px] text-slate-500 mt-0.5 uppercase">Buildings</div>
                </div>
                <div className="bg-slate-800/50 rounded-xl p-3 text-center">
                  <div className="text-2xl font-bold font-mono text-purple-400">{enrichedLead.total_units.toLocaleString()}</div>
                  <div className="text-[10px] text-slate-500 mt-0.5 uppercase">Units</div>
                </div>
              </div>

              {/* Building Type Breakdown */}
              {enrichedLead.building_types && enrichedLead.building_types.total > 0 && (
                <div className="bg-slate-800/30 rounded-xl p-4">
                  <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider mb-3">Portfolio Composition</h3>
                  <div className="flex gap-2 flex-wrap">
                    {enrichedLead.building_types.condo > 0 && <span className="px-2 py-1 bg-blue-900/30 text-blue-400 text-xs rounded">{enrichedLead.building_types.condo} Condo</span>}
                    {enrichedLead.building_types.coop > 0 && <span className="px-2 py-1 bg-purple-900/30 text-purple-400 text-xs rounded">{enrichedLead.building_types.coop} Coop</span>}
                    {enrichedLead.building_types.rental_elevator > 0 && <span className="px-2 py-1 bg-emerald-900/30 text-emerald-400 text-xs rounded">{enrichedLead.building_types.rental_elevator} Elevator</span>}
                    {enrichedLead.building_types.rental_walkup > 0 && <span className="px-2 py-1 bg-amber-900/30 text-amber-400 text-xs rounded">{enrichedLead.building_types.rental_walkup} Walkup</span>}
                    {enrichedLead.building_types.small_residential > 0 && <span className="px-2 py-1 bg-slate-700 text-slate-300 text-xs rounded">{enrichedLead.building_types.small_residential} Small Res</span>}
                  </div>
                </div>
              )}

              {/* AI Summary */}
              <div className="bg-slate-800/30 rounded-xl p-4">
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider">AI Summary</h3>
                  <button onClick={handleGenerateAI} disabled={isGeneratingAI} className="text-[10px] text-blue-400 hover:text-blue-300 disabled:opacity-50">
                    {isGeneratingAI ? 'Generating...' : aiDescription ? 'Regenerate' : 'Generate'}
                  </button>
                </div>
                {aiDescription ? (
                  <p className="text-sm text-slate-300 leading-relaxed">{aiDescription}</p>
                ) : (
                  <p className="text-sm text-slate-600 italic">Click "Generate" for an AI-powered business summary</p>
                )}
              </div>

              {/* Score Breakdown (collapsed) */}
              {enrichedLead.score_breakdown && (
                <details className="bg-slate-800/30 rounded-xl">
                  <summary className="p-4 cursor-pointer text-xs font-bold text-slate-500 uppercase tracking-wider hover:text-slate-300">Score Breakdown</summary>
                  <div className="px-4 pb-4 grid grid-cols-2 gap-3">
                    {Object.entries(enrichedLead.score_breakdown).map(([key, value]) => (
                      <div key={key} className="flex items-center justify-between">
                        <span className="text-xs text-slate-500 capitalize">{key.replace(/_/g, ' ')}</span>
                        <span className="text-xs font-mono text-slate-300">{typeof value === 'number' ? value.toFixed(1) : String(value)}</span>
                      </div>
                    ))}
                  </div>
                </details>
              )}

              {/* Map */}
              {enrichedLead.buildings && enrichedLead.buildings.length > 0 && (
                <div className="bg-slate-800/30 rounded-xl p-4">
                  <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider mb-3">Portfolio Footprint</h3>
                  <img
                    src={`https://maps.googleapis.com/maps/api/staticmap?size=600x250&scale=2&maptype=roadmap&center=40.7128,-73.95&zoom=11${
                      enrichedLead.buildings.slice(0, 20).map((b: string) => `&markers=size:tiny|color:0x3b82f6|${encodeURIComponent(b + ', New York, NY')}`).join('')
                    }&key=${import.meta.env.VITE_GOOGLE_MAPS_KEY || ''}`}
                    alt="Portfolio map" className="w-full rounded-lg"
                  />
                </div>
              )}
            </>
          )}

          {/* TAB: CONTACTS & RESEARCH */}
          {activeTab === 'contacts' && (
            <>
              {/* Contact Actions */}
              <div className="flex gap-2 flex-wrap">
                <button onClick={handleEnrichContacts} disabled={isEnriching}
                  className="px-4 py-2 bg-emerald-600 text-white text-sm font-bold rounded-lg hover:bg-emerald-500 disabled:opacity-50 transition-colors">
                  {isEnriching ? 'Finding Contacts...' : 'Find Contacts'}
                </button>
                <button onClick={handleResearch} disabled={isResearching}
                  className="px-4 py-2 bg-slate-700 text-slate-300 text-sm font-bold rounded-lg hover:bg-slate-600 disabled:opacity-50 transition-colors">
                  {isResearching ? 'Researching...' : 'Deep Research'}
                </button>
              </div>

              {/* All Contact Info */}
              <div className="bg-slate-800/30 rounded-xl p-4 space-y-3">
                <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider">Contact Information</h3>
                
                {/* Phones */}
                {(enrichedLead.phones?.length > 0 || enrichedLead.phone) && (
                  <div>
                    <label className="text-[10px] text-slate-500 uppercase">Phone</label>
                    {enrichedLead.phones?.length > 0 ? enrichedLead.phones.map((p, i) => (
                      <div key={i} className="flex items-center justify-between py-1">
                        <a href={`tel:${p.value}`} className="text-emerald-400 text-sm font-mono hover:underline">{p.value}</a>
                        <span className="text-[10px] text-slate-600">{p.source}</span>
                      </div>
                    )) : enrichedLead.phone && (
                      <a href={`tel:${enrichedLead.phone}`} className="text-emerald-400 text-sm font-mono hover:underline block">{enrichedLead.phone}</a>
                    )}
                  </div>
                )}

                {/* Emails */}
                {(enrichedLead.emails?.length > 0 || enrichedLead.email) && (
                  <div>
                    <label className="text-[10px] text-slate-500 uppercase">Email</label>
                    {enrichedLead.emails?.length > 0 ? enrichedLead.emails.map((e, i) => (
                      <div key={i} className="flex items-center justify-between py-1">
                        <a href={`mailto:${e.value}`} className="text-blue-400 text-sm hover:underline">{e.value}</a>
                        <span className="text-[10px] text-slate-600">{e.source}</span>
                      </div>
                    )) : enrichedLead.email && (
                      <a href={`mailto:${enrichedLead.email}`} className="text-blue-400 text-sm hover:underline block">{enrichedLead.email}</a>
                    )}
                  </div>
                )}

                {/* Website */}
                {enrichedLead.website && (
                  <div>
                    <label className="text-[10px] text-slate-500 uppercase">Website</label>
                    <a href={enrichedLead.website.startsWith('http') ? enrichedLead.website : `https://${enrichedLead.website}`} target="_blank" rel="noopener" className="text-purple-400 text-sm hover:underline block truncate">{enrichedLead.website}</a>
                  </div>
                )}

                {/* LinkedIn */}
                {enrichedLead.linkedin_url && (
                  <div>
                    <label className="text-[10px] text-slate-500 uppercase">LinkedIn</label>
                    <a href={enrichedLead.linkedin_url} target="_blank" rel="noopener" className="text-blue-400 text-sm hover:underline block">{enrichedLead.linkedin_url}</a>
                  </div>
                )}

                {!enrichedLead.phone && !enrichedLead.email && !enrichedLead.website && (
                  <p className="text-slate-600 text-sm italic">No contact info yet. Click "Find Contacts" above.</p>
                )}
              </div>

              {/* Company Research Results */}
              {companyResearch && (
                <div className="bg-emerald-900/20 border border-emerald-500/30 rounded-xl p-4 space-y-2">
                  <h3 className="text-xs font-bold text-emerald-400 uppercase tracking-wider">Company Research</h3>
                  {companyResearch.owner_names?.length > 0 && <div><span className="text-xs text-slate-500">Owners: </span><span className="text-sm text-slate-300">{companyResearch.owner_names.join(', ')}</span></div>}
                  {companyResearch.year_established && <div><span className="text-xs text-slate-500">Est: </span><span className="text-sm text-slate-300">{companyResearch.year_established}</span></div>}
                  {companyResearch.service_areas?.length > 0 && <div><span className="text-xs text-slate-500">Areas: </span><span className="text-sm text-slate-300">{companyResearch.service_areas.join(', ')}</span></div>}
                  {companyResearch.description && <p className="text-sm text-slate-400 leading-relaxed">{companyResearch.description}</p>}
                </div>
              )}

              {/* NY DOS Info */}
              {dosInfo && (
                <div className="bg-slate-800/30 rounded-xl p-4 space-y-2">
                  <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider">NY DOS Corporation</h3>
                  <div className="grid grid-cols-2 gap-2 text-sm">
                    {dosInfo.entity_name && <div><span className="text-xs text-slate-500 block">Entity</span><span className="text-slate-300">{dosInfo.entity_name}</span></div>}
                    {dosInfo.entity_type && <div><span className="text-xs text-slate-500 block">Type</span><span className="text-slate-300">{dosInfo.entity_type}</span></div>}
                    {dosInfo.formation_date && <div><span className="text-xs text-slate-500 block">Formed</span><span className="text-slate-300">{dosInfo.formation_date}</span></div>}
                    {dosInfo.dos_id && <div><span className="text-xs text-slate-500 block">DOS ID</span><span className="text-slate-300">{dosInfo.dos_id}</span></div>}
                    {dosInfo.registered_agent && <div className="col-span-2"><span className="text-xs text-slate-500 block">Agent</span><span className="text-emerald-400">{dosInfo.registered_agent}</span></div>}
                  </div>
                </div>
              )}

              {/* HPD Registered Contacts */}
              {uniqueContacts.length > 0 && (
                <div className="bg-slate-800/30 rounded-xl p-4">
                  <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider mb-2">HPD Registered Contacts</h3>
                  <div className="space-y-1.5">
                    {uniqueContacts.map((contact: any, i: number) => (
                      <div key={i} className="flex items-center justify-between py-1">
                        <span className="text-sm text-slate-300">{contact.name}</span>
                        <span className="text-[10px] text-slate-600">{(contact.type || '').replace(/([A-Z])/g, ' $1').trim()}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}

          {/* TAB: PIPELINE & OUTREACH */}
          {activeTab === 'pipeline' && (
            <>
              {/* Priority & Follow-Up */}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-xs text-slate-500 uppercase tracking-wider block mb-2">Priority</label>
                  <div className="flex gap-1">
                    {[1, 2, 3, 4, 5].map(star => (
                      <button key={star} onClick={() => handlePriorityChange(star)} className="transition-transform hover:scale-110">
                        <svg className={`w-7 h-7 ${star <= priorityRank ? 'text-amber-400' : 'text-slate-700'}`} fill="currentColor" viewBox="0 0 20 20">
                          <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
                        </svg>
                      </button>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="text-xs text-slate-500 uppercase tracking-wider block mb-2">Next Follow-Up</label>
                  <div className="relative">
                    <input type="date" value={nextFollowUp} onChange={e => handleFollowUpChange(e.target.value)}
                      className={`w-full px-3 py-2 bg-slate-800 border rounded-lg text-sm font-mono ${
                        nextFollowUp && new Date(nextFollowUp) < new Date(new Date().toDateString()) ? 'border-rose-500/50 text-rose-400' :
                        nextFollowUp && new Date(nextFollowUp).toDateString() === new Date().toDateString() ? 'border-amber-500/50 text-amber-400' :
                        'border-slate-700 text-slate-300'
                      }`} />
                    {nextFollowUp && new Date(nextFollowUp) < new Date(new Date().toDateString()) && (
                      <span className="absolute -top-2 right-2 px-1.5 py-0.5 bg-rose-600 text-[9px] font-bold text-white rounded">OVERDUE</span>
                    )}
                  </div>
                </div>
              </div>

              {/* Outreach Status */}
              <div>
                <label className="text-xs text-slate-500 uppercase tracking-wider block mb-2">Outreach Status</label>
                <div className="flex flex-wrap gap-2">
                  {OUTREACH_STATUSES.map(status => (
                    <button key={status.value} onClick={() => handleSaveStatus(status.value)} disabled={isSaving}
                      className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                        outreachStatus === status.value ? `${status.color} ring-2 ring-offset-2 ring-offset-slate-900 ring-white/20` :
                        'bg-slate-800 text-slate-500 hover:bg-slate-700 hover:text-slate-300'
                      }`}>
                      {status.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Outreach Log */}
              <div className="bg-slate-800/30 rounded-xl p-4">
                <div className="flex justify-between items-center mb-3">
                  <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider">Outreach Log</h3>
                  <button onClick={() => setShowAddOutreach(!showAddOutreach)} className="px-3 py-1 bg-blue-600 text-white text-xs font-medium rounded-lg hover:bg-blue-500 transition-colors">
                    + Log Attempt
                  </button>
                </div>
                {showAddOutreach && (
                  <div className="bg-slate-900/50 rounded-lg p-3 mb-3 space-y-2">
                    <div className="grid grid-cols-2 gap-2">
                      <select value={newOutreach.method} onChange={e => setNewOutreach({...newOutreach, method: e.target.value})}
                        className="px-3 py-1.5 bg-slate-800 border border-slate-700 rounded-lg text-xs text-slate-300">
                        {OUTREACH_METHODS.map(m => <option key={m} value={m}>{m.replace('_', ' ')}</option>)}
                      </select>
                      <select value={newOutreach.outcome} onChange={e => setNewOutreach({...newOutreach, outcome: e.target.value})}
                        className="px-3 py-1.5 bg-slate-800 border border-slate-700 rounded-lg text-xs text-slate-300">
                        {OUTREACH_OUTCOMES.map(o => <option key={o} value={o}>{o.replace(/_/g, ' ')}</option>)}
                      </select>
                    </div>
                    <textarea value={newOutreach.notes} onChange={e => setNewOutreach({...newOutreach, notes: e.target.value})}
                      placeholder="Notes..." rows={2} className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-xs text-slate-300 resize-none" />
                    <button onClick={handleAddOutreachAttempt} disabled={isSaving}
                      className="px-4 py-1.5 bg-blue-600 text-white text-xs font-medium rounded-lg hover:bg-blue-500 disabled:opacity-50">
                      {isSaving ? 'Saving...' : 'Save'}
                    </button>
                  </div>
                )}
                <div className="space-y-2 max-h-64 overflow-y-auto">
                  {outreachAttempts.length > 0 ? outreachAttempts.map((attempt, i) => (
                    <div key={i} className="p-2 bg-slate-900/50 rounded-lg">
                      <div className="flex items-center gap-2">
                        <span className="px-1.5 py-0.5 bg-blue-900/30 text-blue-400 text-[10px] rounded">{attempt.method}</span>
                        <span className="px-1.5 py-0.5 bg-slate-800 text-slate-400 text-[10px] rounded">{attempt.outcome?.replace(/_/g, ' ')}</span>
                        <span className="text-[10px] text-slate-600 ml-auto">{new Date(attempt.timestamp).toLocaleDateString()}</span>
                      </div>
                      {attempt.notes && <p className="text-xs text-slate-500 mt-1">{attempt.notes}</p>}
                    </div>
                  )) : (
                    <p className="text-slate-600 text-sm italic">No outreach attempts logged yet</p>
                  )}
                </div>
              </div>

              {/* Notes */}
              <div className="bg-slate-800/30 rounded-xl p-4">
                <div className="flex justify-between items-center mb-2">
                  <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider">Notes</h3>
                  <button onClick={handleSaveNotes} disabled={isSaving} className="px-3 py-1 bg-emerald-600 text-white text-xs font-medium rounded-lg hover:bg-emerald-500 disabled:opacity-50">
                    {isSaving ? 'Saving...' : 'Save'}
                  </button>
                </div>
                <textarea value={notes} onChange={e => setNotes(e.target.value)} rows={4} placeholder="Add notes about this lead..."
                  className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm text-slate-300 resize-none" />
              </div>
            </>
          )}

          {/* TAB: BUILDINGS */}
          {activeTab === 'buildings' && (
            <>
              {/* Search */}
              <input type="text" value={buildingSearch} onChange={e => setBuildingSearch(e.target.value)} placeholder="Search buildings..."
                className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm text-slate-300" />
              
              {/* Map */}
              {enrichedLead.buildings && enrichedLead.buildings.length > 0 && (
                <img
                  src={`https://maps.googleapis.com/maps/api/staticmap?size=600x250&scale=2&maptype=roadmap&center=40.7128,-73.95&zoom=11${
                    enrichedLead.buildings.slice(0, 25).map((b: string) => `&markers=size:tiny|color:0x3b82f6|${encodeURIComponent(b + ', New York, NY')}`).join('')
                  }&key=${import.meta.env.VITE_GOOGLE_MAPS_KEY || ''}`}
                  alt="Portfolio map" className="w-full rounded-lg"
                />
              )}

              {/* Building List */}
              <div className="space-y-1 max-h-96 overflow-y-auto">
                {(enrichedLead.buildings || [])
                  .filter((b: string) => !buildingSearch || b.toLowerCase().includes(buildingSearch.toLowerCase()))
                  .map((building: string, i: number) => (
                    <div key={i} className="flex items-center justify-between p-2 bg-slate-800/30 rounded-lg hover:bg-slate-800/50">
                      <span className="text-sm text-slate-300">{building}</span>
                      <a href={`https://www.google.com/maps/search/${encodeURIComponent(building + ', New York, NY')}`} target="_blank" rel="noopener"
                        className="text-[10px] text-blue-400 hover:underline">Map</a>
                    </div>
                  ))}
                {(!enrichedLead.buildings || enrichedLead.buildings.length === 0) && <p className="text-slate-600 text-sm italic">No buildings data available</p>}
              </div>
            </>
          )}

          {/* TAB: DUE DILIGENCE */}
          {activeTab === 'dd' && (
            <>
              <button onClick={handleGenerateDD} disabled={isLoadingDD}
                className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-gradient-to-r from-indigo-600 to-purple-600 text-white text-sm font-medium rounded-xl hover:from-indigo-500 hover:to-purple-500 disabled:opacity-50 transition-all">
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
                </svg>
                {isLoadingDD ? 'Generating...' : ddReport ? 'Regenerate DD Report' : 'Generate Due Diligence Report'}
              </button>

              {ddReport && (
                <div className="space-y-4">
                  <div className="flex justify-end">
                    <button onClick={() => { navigator.clipboard.writeText(ddReport.report_markdown); toast.success('Copied to clipboard'); }}
                      className="px-3 py-1 bg-slate-700 text-slate-300 text-xs rounded-lg hover:bg-slate-600">Copy Markdown</button>
                  </div>
                  <div className="prose prose-invert prose-sm max-w-none">
                    {ddReport.report_markdown.split('\n').map((line, i) => {
                      if (line.startsWith('# ')) return <h1 key={i} className="text-xl font-bold text-white mt-4 mb-2">{line.slice(2)}</h1>;
                      if (line.startsWith('## ')) return <h2 key={i} className="text-lg font-bold text-blue-400 mt-3 mb-1">{line.slice(3)}</h2>;
                      if (line.startsWith('### ')) return <h3 key={i} className="text-sm font-bold text-slate-300 mt-3 mb-1">{line.slice(4)}</h3>;
                      if (line.startsWith('- ')) return <li key={i} className="text-slate-300 text-sm ml-4">{line.slice(2)}</li>;
                      if (line.trim() === '') return <br key={i} />;
                      return <p key={i} className="text-slate-400 text-sm leading-relaxed">{line}</p>;
                    })}
                  </div>
                  {ddReport.comparables?.length > 0 && (
                    <div className="border-t border-slate-800 pt-3">
                      <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider mb-2">Comparables</h3>
                      <table className="w-full text-sm">
                        <thead><tr className="text-slate-500 text-xs uppercase">
                          <th className="text-left py-1 px-2">Name</th><th className="text-right py-1 px-2">Score</th><th className="text-right py-1 px-2">Buildings</th><th className="text-right py-1 px-2">Revenue</th>
                        </tr></thead>
                        <tbody className="divide-y divide-slate-800">
                          {ddReport.comparables.map((comp: any, i: number) => (
                            <tr key={i} className="text-slate-300">
                              <td className="py-1 px-2">{comp.name || comp.lead_name}</td>
                              <td className="py-1 px-2 text-right font-mono">{comp.score?.toFixed(1)}</td>
                              <td className="py-1 px-2 text-right font-mono">{comp.portfolio_size || comp.buildings}</td>
                              <td className="py-1 px-2 text-right font-mono text-emerald-400">{comp.estimated_annual_revenue ? formatCurrency(comp.estimated_annual_revenue) : '---'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}

              {!ddReport && !isLoadingDD && (
                <div className="text-center py-10">
                  <p className="text-slate-600 text-sm">Generate a comprehensive due diligence report including portfolio analysis, financials, violations, contacts, and comparables.</p>
                </div>
              )}

              {isLoadingDD && !ddReport && (
                <div className="flex flex-col items-center justify-center py-16">
                  <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin mb-4" />
                  <p className="text-slate-400 text-sm">Generating report...</p>
                  <p className="text-slate-600 text-xs mt-1">This may take 15-30 seconds</p>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
};

export default LeadDetail;
