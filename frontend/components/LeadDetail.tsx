import React, { useState, useEffect, lazy, Suspense } from 'react';
import { toast } from 'react-hot-toast';
import { ApiLead, updateLead, researchLead, addOutreachAttempt, enrichLeadContacts, generateAiSummary, enrichLeadAll, getDueDiligence, CompanyResearch, OutreachAttempt, DOSInfo } from '../services/api';

// Lazy-load map to avoid large initial bundle
const PortfolioMap = lazy(() => import('./PortfolioMap'));

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
  { value: 'new', label: 'New', color: 'bg-gray-200 text-gray-700' },
  { value: 'contacted', label: 'Contacted', color: 'bg-blue-600 text-blue-100' },
  { value: 'interested', label: 'Interested', color: 'bg-emerald-600 text-emerald-100' },
  { value: 'not_interested', label: 'Not Interested', color: 'bg-amber-600 text-amber-100' },
  { value: 'closed', label: 'Closed', color: 'bg-purple-600 text-purple-100' },
];

const OUTREACH_METHODS = ['phone', 'email', 'linkedin', 'in_person', 'other'];
const OUTREACH_OUTCOMES = ['no_answer', 'left_voicemail', 'spoke_with_contact', 'sent_email', 'meeting_scheduled', 'not_interested', 'other'];

const formatCurrency = (amount: number | undefined | null): string => {
  if (amount == null || isNaN(amount)) return '—';
  if (amount >= 1_000_000) return `$${(amount / 1_000_000).toFixed(1)}m`;
  if (amount >= 1_000) return `$${(amount / 1_000).toFixed(0)}k`;
  return `$${amount.toFixed(0)}`;
};

type TabId = 'overview' | 'contacts' | 'pipeline' | 'buildings' | 'dd';

interface Props {
  lead: ApiLead;
  onClose: () => void;
  onLeadUpdated?: (lead: ApiLead) => void;
}

const LeadDetail: React.FC<Props> = ({ lead, onClose, onLeadUpdated }) => {
  const [activeTab, setActiveTab] = useState<TabId>('overview');
  const [isEnriching, setIsEnriching] = useState(false);
  // isResearching and isGeneratingAI removed — unified into isEnriching via handleEnrichAll
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
  const [showEmailMenu, setShowEmailMenu] = useState(false);

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
    setShowEmailMenu(false);
  }, [lead]);

  // === Handlers ===
  const handleSaveStatus = async (newStatus: string) => {
    setOutreachStatus(newStatus);
    setIsSaving(true);
    try { await updateLead(lead.lead_id, { outreach_status: newStatus }); } 
    catch (err) { console.error('Failed to update status:', err); toast.error('Failed to update status'); } 
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
    catch (err) { console.error('Failed to update priority:', err); toast.error('Failed to update priority'); }
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

  // Unified enrichment: contacts + research + AI summary in one call.
  // The backend returns the full updated lead in the response — no second API call needed.
  const handleEnrichAll = async () => {
    setIsEnriching(true);
    try {
      const result = await enrichLeadAll(lead.lead_id);
      
      // Build summary toast
      const found: string[] = [];
      if (result.contacts.phones_found > 0) found.push(`${result.contacts.phones_found} phone(s)`);
      if (result.contacts.emails_found > 0) found.push(`${result.contacts.emails_found} email(s)`);
      if (result.contacts.website_found) found.push('website');
      if (result.ai_summary.generated) found.push('AI summary');
      if (result.research.website_scraped) found.push('web research');
      
      if (found.length > 0) {
        toast.success(`Enrichment complete: found ${found.join(', ')}`);
      } else {
        toast.error('Enrichment complete but no new data found');
      }
      
      if (result.errors?.length > 0) {
        console.warn('Enrichment partial errors:', result.errors);
      }
      
      // Use the full lead returned by the backend — single source of truth, no second call
      if (result.lead) {
        const updatedLead = result.lead;
        setEnrichedLead(updatedLead);
        if (updatedLead.business_summary) {
          setAiDescription(updatedLead.business_summary);
        }
        setPipelineStage(updatedLead.pipeline_stage || 'research');
        // Propagate to parent so the table updates without a page reload
        onLeadUpdated?.(updatedLead);
      }
    } catch (err) { console.error('Enrichment failed:', err); toast.error('Enrichment failed — check server logs'); } 
    finally { setIsEnriching(false); }
  };

  // Keep individual handlers for backward compatibility but mark as legacy
  const handleResearch = handleEnrichAll;
  const handleGenerateAI = handleEnrichAll;
  const handleEnrichContacts = handleEnrichAll;

  const handleAddOutreachAttempt = async () => {
    if (!newOutreach.method || !newOutreach.outcome) return;
    setIsSaving(true);
    try {
      const result = await addOutreachAttempt(lead.lead_id, newOutreach);
      setOutreachAttempts([result.attempt, ...outreachAttempts]);
      setShowAddOutreach(false);
      setNewOutreach({ method: 'phone', outcome: 'no_answer', notes: '' });
    } catch (err) { console.error('Failed to add outreach:', err); toast.error('Failed to log outreach'); } 
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
    { id: 'buildings', label: `Buildings (${enrichedLead.portfolio_size || 0})` },
    { id: 'dd', label: 'Due Diligence' },
  ];

  return (
    <div className="fixed inset-0 bg-black/20 backdrop-blur-sm z-50 flex items-center justify-center p-0 md:p-4" onClick={onClose}>
      <div className="bg-white border border-gray-200 md:rounded-2xl max-w-3xl w-full h-full md:h-auto md:max-h-[90vh] flex flex-col shadow-2xl" onClick={e => e.stopPropagation()}>
        
        {/* === STICKY HEADER === */}
        <div className="flex-shrink-0 border-b border-gray-200">
          {/* Top row: name + close */}
          <div className="p-5 pb-3 flex justify-between items-start">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1 flex-wrap">
                <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold uppercase ${
                  enrichedLead.entity_type === 'company' ? 'bg-blue-50 text-blue-700' :
                  enrichedLead.entity_type === 'individual_agent' ? 'bg-amber-50 text-amber-700' :
                  enrichedLead.entity_type === 'owner_operator' ? 'bg-purple-50 text-purple-700' :
                  'bg-gray-100 text-gray-500'
                }`} title={enrichedLead.entity_type === 'company' ? 'Registered management company or housing entity' : enrichedLead.entity_type === 'individual_agent' ? 'Individual person acting as managing agent' : enrichedLead.entity_type === 'owner_operator' ? 'Property owner who self-manages' : 'Entity type unknown'}>
                  {enrichedLead.entity_type === 'company' ? 'Company' : 
                   enrichedLead.entity_type === 'individual_agent' ? 'Individual Agent' : 
                   enrichedLead.entity_type === 'owner_operator' ? 'Owner-Operator' : 'Unknown'}
                </span>
                {(enrichedLead.boros || [enrichedLead.boro]).map((b, i) => (
                  <span key={i} className="px-1.5 py-0.5 bg-gray-100 text-gray-500 text-[10px] rounded">
                    {b ? b.charAt(0) + b.slice(1).toLowerCase() : ''}
                  </span>
                ))}
              </div>
              <h2 className="text-xl font-bold text-gray-900 truncate">{enrichedLead.company_name || enrichedLead.agent_name || enrichedLead.owner_name}</h2>
            </div>
            <div className="flex items-center gap-3 ml-4">
              {/* Score with context */}
              <div className={`px-3 py-1 rounded-lg text-center ${
                (enrichedLead.score || 0) >= 60 ? 'bg-emerald-50' :
                (enrichedLead.score || 0) >= 40 ? 'bg-amber-50' : 'bg-gray-100'
              }`} title={`Lead quality score (0–100): ${(enrichedLead.score || 0) >= 60 ? 'Strong acquisition target' : (enrichedLead.score || 0) >= 40 ? 'Moderate potential' : 'Lower priority'}\nBased on portfolio size, building types, registration status, and data completeness`}>
                <div className={`text-2xl font-bold font-mono ${
                  (enrichedLead.score || 0) >= 60 ? 'text-emerald-600' :
                  (enrichedLead.score || 0) >= 40 ? 'text-amber-600' : 'text-gray-500'
                }`}>{(enrichedLead.score || 0).toFixed(0)}</div>
                <div className="text-[9px] text-gray-400 uppercase font-bold">Score</div>
              </div>
              {/* Revenue */}
              {(enrichedLead.estimated_annual_revenue || 0) > 0 && (
                <div className="text-right" title="Estimated annual management fee if acquired: Total Units × Avg Rent (by borough & building type) × 5% management fee">
                  <div className="text-lg font-bold font-mono text-emerald-600">{formatCurrency(enrichedLead.estimated_annual_revenue)}<span className="text-xs text-emerald-500">/yr</span></div>
                  <div className="text-[9px] text-gray-400 uppercase font-bold">Mgmt Fee</div>
                </div>
              )}
              <button onClick={onClose} className="p-2 text-gray-400 hover:text-gray-900 transition-colors">
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
              <div className="relative">
                <button 
                  onClick={(e) => { e.preventDefault(); setShowEmailMenu(!showEmailMenu); }}
                  className="px-3 py-1.5 bg-blue-600 text-white text-xs font-bold rounded-lg hover:bg-blue-500 transition-colors flex items-center gap-1">
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/></svg>
                  Email
                  <svg className={`w-3 h-3 ml-0.5 transition-transform ${showEmailMenu ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7"/></svg>
                </button>
                {showEmailMenu && (
                <div className="absolute top-full left-0 mt-1 w-56 bg-white border border-gray-200 rounded-lg shadow-xl z-50 py-1">
                  <a href={`mailto:${enrichedLead.email}?subject=Property Management Services — ${enrichedLead.company_name || enrichedLead.agent_name || 'Introduction'}&body=Hi ${enrichedLead.primary_contact || 'there'},%0D%0A%0D%0AI noticed your portfolio of ${enrichedLead.portfolio_size} buildings across ${(enrichedLead.boros || [enrichedLead.boro]).join(', ')} and wanted to introduce our property management services.%0D%0A%0D%0AWould you have time for a brief call this week?%0D%0A%0D%0ABest regards`}
                    onClick={() => { setShowEmailMenu(false); addOutreachAttempt(lead.lead_id, { method: 'email', outcome: 'sent_email', notes: 'Intro template sent' }).catch(() => {}); }}
                    className="block px-4 py-2 text-xs text-gray-700 hover:bg-gray-50 transition-colors">
                    Intro Template
                  </a>
                  <a href={`mailto:${enrichedLead.email}?subject=Following up — ${enrichedLead.company_name || enrichedLead.agent_name || ''}&body=Hi ${enrichedLead.primary_contact || 'there'},%0D%0A%0D%0AI wanted to follow up on my previous message regarding your ${enrichedLead.portfolio_size}-building portfolio.%0D%0A%0D%0AWe specialize in portfolios like yours in ${(enrichedLead.boros || [enrichedLead.boro]).join(' and ')} and believe we can add value.%0D%0A%0D%0AWould you be open to a brief conversation?%0D%0A%0D%0ABest regards`}
                    onClick={() => { setShowEmailMenu(false); addOutreachAttempt(lead.lead_id, { method: 'email', outcome: 'sent_email', notes: 'Follow-up template sent' }).catch(() => {}); }}
                    className="block px-4 py-2 text-xs text-gray-700 hover:bg-gray-50 transition-colors">
                    Follow-Up Template
                  </a>
                  <a href={`mailto:${enrichedLead.email}`}
                    onClick={() => setShowEmailMenu(false)}
                    className="block px-4 py-2 text-xs text-gray-700 hover:bg-gray-50 transition-colors border-t border-gray-200">
                    Blank Email
                  </a>
                </div>
                )}
              </div>
            )}
            <button onClick={openWebsite} className="px-3 py-1.5 bg-purple-600 text-white text-xs font-bold rounded-lg hover:bg-purple-500 transition-colors">
              {enrichedLead.website ? 'Website' : 'Search'}
            </button>
            <button onClick={handleEnrichAll} disabled={isEnriching} className="px-3 py-1.5 bg-emerald-600 text-white text-xs font-bold rounded-lg hover:bg-emerald-500 disabled:opacity-50 transition-colors" title="Find contacts, scrape website, and generate AI summary — all in one step">
              {isEnriching ? 'Enriching...' : 'Enrich Lead'}
            </button>
            <div className="flex-1" />
            {/* Pipeline selector - dropdown for readability */}
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-gray-400 uppercase">Pipeline:</span>
              <select 
                value={pipelineStage} 
                onChange={(e) => handlePipelineChange(e.target.value)}
                className="px-3 py-1.5 bg-white border border-gray-300 rounded-lg text-xs text-gray-900 font-bold"
              >
                {PIPELINE_STAGES.map((stage) => (
                  <option key={stage.value} value={stage.value}>{stage.label}</option>
                ))}
              </select>
            </div>
          </div>

          {/* Tab bar */}
          <div className="flex border-b border-gray-200">
            {TABS.map(tab => (
              <button key={tab.id} onClick={() => setActiveTab(tab.id)}
                className={`flex-1 px-4 py-2.5 text-xs font-bold uppercase tracking-wider transition-colors ${
                  activeTab === tab.id ? 'text-gray-900 border-b-2 border-emerald-500' : 'text-gray-500 hover:text-gray-700'
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
                <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-4">
                  <h3 className="text-xs font-bold text-emerald-600 uppercase tracking-wider mb-2">Estimated Management Fee</h3>
                  {(enrichedLead.estimated_annual_revenue || 0) > 0 ? (
                    <div>
                      <div className="text-2xl font-bold font-mono text-emerald-600">{formatCurrency(enrichedLead.estimated_annual_revenue)}<span className="text-sm text-emerald-500">/yr</span></div>
                      <div className="text-sm font-mono text-gray-500 mt-1">{formatCurrency(enrichedLead.estimated_monthly_revenue)}<span className="text-xs text-gray-400">/mo</span></div>
                      {/* Detailed breakdown with real numbers */}
                      <details className="mt-3 pt-3 border-t border-emerald-200">
                        <summary className="text-[10px] font-bold text-gray-400 uppercase tracking-wider cursor-pointer hover:text-gray-700">
                          {enrichedLead.total_units?.toLocaleString()} units × avg rent × 5% fee — View Breakdown
                        </summary>
                        <div className="mt-2 space-y-1">
                          {(enrichedLead as any).revenue_breakdown?.map((item: any, i: number) => (
                            <div key={i} className="flex items-center justify-between text-xs">
                              <span className="text-gray-500">{item.label}: <span className="text-gray-700 font-mono">{item.estimated_units?.toLocaleString()}</span> units @ <span className="text-gray-700 font-mono">${item.rent_per_unit?.toLocaleString()}</span>/mo</span>
                              <span className="text-emerald-600 font-mono">{formatCurrency(item.monthly_gross * 0.05 * 12)}/yr</span>
                            </div>
                          )) || (
                            <div className="text-[10px] text-gray-400 space-y-0.5">
                              <p>{enrichedLead.total_units?.toLocaleString() || '—'} units across {enrichedLead.portfolio_size || '—'} buildings</p>
                              <p>Borough: {enrichedLead.boro || 'NYC avg'} • Fee rate: 5%</p>
                              <p>Rents: StreetEasy &amp; Census ACS • Condo/co-op adjusted to 60%</p>
                            </div>
                          )}
                          <p className="text-[10px] text-gray-400 mt-1 pt-1 border-t border-emerald-200">Source: StreetEasy &amp; Census ACS avg rents • 5% mgmt fee rate • Condo/co-op at 60%</p>
                        </div>
                      </details>
                    </div>
                  ) : (
                    <div className="text-gray-400 text-sm">{(enrichedLead.total_units || 0) > 0 ? 'Revenue calculating...' : 'No unit data available'}</div>
                  )}
                </div>
                <div className={`${
                  enrichedLead.violations_per_unit > 1.0 ? 'bg-rose-50 border-rose-200' :
                  enrichedLead.violation_count > 0 ? 'bg-amber-50 border-amber-200' :
                  'bg-gray-50 border-gray-200'
                } border rounded-xl p-4`}>
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="text-xs font-bold text-gray-700 uppercase tracking-wider">HPD Violations</h3>
                    {enrichedLead.violations_per_unit > 1.0 && (
                      <span className="px-2 py-0.5 bg-rose-100 text-rose-700 text-[10px] font-bold rounded uppercase">High Distress</span>
                    )}
                  </div>
                  {enrichedLead.violation_count > 0 ? (
                    <div>
                      <div className="flex items-baseline gap-2">
                        <span className={`text-2xl font-bold font-mono ${
                          (enrichedLead.violations_per_unit || 0) > 1.0 ? 'text-rose-600' :
                          (enrichedLead.violations_per_unit || 0) > 0.3 ? 'text-amber-600' : 'text-gray-900'
                        }`}>{(enrichedLead.violations_per_unit || 0).toFixed(2)}</span>
                        <span className="text-xs text-gray-500">per unit</span>
                      </div>
                      <div className="text-sm text-gray-500 mt-1 font-mono">{(enrichedLead.violation_count || 0).toLocaleString()} total violations</div>
                      <div className="flex flex-col gap-1 mt-2 text-xs">
                        <div className="flex gap-3">
                          <span className="text-gray-500" title="Non-hazardous conditions">A: {enrichedLead.violation_class_a}</span>
                          <span className="text-amber-600" title="Hazardous conditions">B: {enrichedLead.violation_class_b}</span>
                          <span className="text-rose-600" title="Immediately hazardous conditions">C: {enrichedLead.violation_class_c}</span>
                        </div>
                        <div className="text-[9px] text-gray-400">A = non-hazardous • B = hazardous • C = immediately hazardous</div>
                      </div>
                    </div>
                  ) : (
                    <div className="text-gray-400 text-sm">No violations on record</div>
                  )}
                </div>
              </div>

              {/* Portfolio Summary */}
              <div className="grid grid-cols-3 gap-3">
                <div className="bg-gray-50 rounded-xl p-3 text-center" title="Lead quality score (0–100) based on portfolio size, building mix, registration status, and data completeness">
                  <div className={`text-2xl font-bold font-mono ${(enrichedLead.score || 0) >= 60 ? 'text-emerald-600' : (enrichedLead.score || 0) >= 40 ? 'text-amber-600' : 'text-gray-500'}`}>{(enrichedLead.score || 0).toFixed(1)}</div>
                  <div className="text-[10px] text-gray-400 mt-0.5 uppercase">Lead Score</div>
                </div>
                <div className="bg-gray-50 rounded-xl p-3 text-center" title="Total number of buildings managed by this entity per HPD registration records">
                  <div className="text-2xl font-bold font-mono text-blue-600">{enrichedLead.portfolio_size || 0}</div>
                  <div className="text-[10px] text-gray-400 mt-0.5 uppercase">Buildings</div>
                </div>
                <div className="bg-gray-50 rounded-xl p-3 text-center" title="Total residential units across all buildings in this portfolio">
                  <div className="text-2xl font-bold font-mono text-purple-600">{(enrichedLead.total_units || 0).toLocaleString()}</div>
                  <div className="text-[10px] text-gray-400 mt-0.5 uppercase">Res. Units</div>
                </div>
              </div>

              {/* Building Type Breakdown */}
              {enrichedLead.building_types && enrichedLead.building_types.total > 0 && (
                <div className="bg-gray-50 rounded-xl p-4">
                  <h3 className="text-xs font-bold text-gray-700 uppercase tracking-wider mb-3">Portfolio Composition</h3>
                  <div className="flex gap-2 flex-wrap">
                    {enrichedLead.building_types.condo > 0 && <span className="px-2 py-1 bg-blue-50 text-blue-700 text-xs rounded">{enrichedLead.building_types.condo} Condo</span>}
                    {enrichedLead.building_types.coop > 0 && <span className="px-2 py-1 bg-purple-50 text-purple-700 text-xs rounded">{enrichedLead.building_types.coop} Coop</span>}
                    {enrichedLead.building_types.rental_elevator > 0 && <span className="px-2 py-1 bg-emerald-50 text-emerald-700 text-xs rounded">{enrichedLead.building_types.rental_elevator} Elevator</span>}
                    {enrichedLead.building_types.rental_walkup > 0 && <span className="px-2 py-1 bg-amber-50 text-amber-700 text-xs rounded">{enrichedLead.building_types.rental_walkup} Walkup</span>}
                    {enrichedLead.building_types.small_residential > 0 && <span className="px-2 py-1 bg-gray-100 text-gray-700 text-xs rounded">{enrichedLead.building_types.small_residential} Small Res</span>}
                  </div>
                </div>
              )}

              {/* AI Summary */}
              <div className="bg-gray-50 rounded-xl p-4">
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-xs font-bold text-gray-700 uppercase tracking-wider">Company Overview</h3>
                  <button onClick={handleEnrichAll} disabled={isEnriching} className="text-[10px] text-blue-600 hover:text-blue-500 disabled:opacity-50">
                    {isEnriching ? 'Enriching...' : aiDescription ? 'Refresh' : 'Enrich to Generate'}
                  </button>
                </div>
                {aiDescription ? (
                  <p className="text-sm text-gray-700 leading-relaxed">{
                    // Strip accidental markdown formatting from AI output
                    aiDescription
                      .replace(/^#+\s/gm, '')
                      .replace(/\*\*(.*?)\*\*/g, '$1')
                      .replace(/\*(.*?)\*/g, '$1')
                      .replace(/^[-*]\s/gm, '')
                      .trim()
                  }</p>
                ) : (
                  <p className="text-sm text-gray-400 italic">Click "Enrich Lead" to generate contacts and an AI summary</p>
                )}
              </div>

              {/* Score Breakdown (collapsed) */}
              {enrichedLead.score_breakdown && (
                <details className="bg-gray-50 rounded-xl">
                  <summary className="p-4 cursor-pointer text-xs font-bold text-gray-500 uppercase tracking-wider hover:text-gray-700">How is the score calculated?</summary>
                  <div className="px-4 pb-4">
                    <p className="text-[10px] text-gray-400 mb-3">Score is weighted across several factors. Each component contributes points to the total (max 100).</p>
                    <div className="grid grid-cols-2 gap-3">
                      {Object.entries(enrichedLead.score_breakdown).map(([key, value]) => {
                        const SCORE_LABELS: Record<string, string> = {
                          portfolio_size: 'Portfolio Size',
                          total_units: 'Total Units',
                          building_diversity: 'Building Mix',
                          building_types: 'Building Types',
                          registration_recency: 'Registration Recency',
                          contact_completeness: 'Contact Info Available',
                          data_quality: 'Data Completeness',
                          violation_density: 'Violation Density',
                          revenue_potential: 'Revenue Potential',
                          borough_diversity: 'Borough Coverage',
                          entity_type_bonus: 'Entity Type',
                        };
                        return (
                          <div key={key} className="flex items-center justify-between">
                            <span className="text-xs text-gray-500">{SCORE_LABELS[key] || key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}</span>
                            <span className="text-xs font-mono text-gray-700">{typeof value === 'number' ? value.toFixed(1) : String(value)}</span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </details>
              )}

              {/* Interactive Map (OpenStreetMap) */}
              {enrichedLead.buildings && enrichedLead.buildings.length > 0 && (
                <div className="bg-gray-50 rounded-xl p-4">
                  <h3 className="text-xs font-bold text-gray-700 uppercase tracking-wider mb-3">Portfolio Footprint</h3>
                  <Suspense fallback={<div className="h-[250px] bg-gray-100 rounded-lg animate-pulse" />}>
                    <PortfolioMap buildings={enrichedLead.buildings} boro={enrichedLead.boro} boros={enrichedLead.boros} />
                  </Suspense>
                </div>
              )}
            </>
          )}

          {/* TAB: CONTACTS & RESEARCH */}
          {activeTab === 'contacts' && (
            <>
              {/* Enrichment Status Banner */}
              <div className={`rounded-lg px-3 py-2 text-xs font-medium flex items-center gap-2 ${
                enrichedLead.enrichment_status === 'complete' ? 'bg-emerald-50 border border-emerald-200 text-emerald-700' :
                enrichedLead.enrichment_status === 'partial' ? 'bg-amber-50 border border-amber-200 text-amber-700' :
                enrichedLead.enrichment_status === 'failed' ? 'bg-rose-50 border border-rose-200 text-rose-700' :
                'bg-gray-50 border border-gray-200 text-gray-500'
              }`}>
                <span className="text-sm">
                  {enrichedLead.enrichment_status === 'complete' ? '●' :
                   enrichedLead.enrichment_status === 'partial' ? '◐' :
                   enrichedLead.enrichment_status === 'failed' ? '●' : '○'}
                </span>
                {enrichedLead.enrichment_status === 'complete' ? 'Fully enriched — contacts, website, and AI summary found' :
                 enrichedLead.enrichment_status === 'partial' ? 'Partially enriched — some data found. Click "Re-enrich" to try again.' :
                 enrichedLead.enrichment_status === 'failed' ? 'Enriched but nothing found — try "Re-enrich" or search manually' :
                 'Not yet enriched — click "Enrich Lead" to find contacts, website, and generate a summary'}
              </div>

              {/* Single Enrich Action */}
              <div className={`${!enrichedLead.phone && !enrichedLead.email ? 'bg-emerald-50 border border-emerald-200 rounded-xl p-4 text-center' : ''}`}>
                {!enrichedLead.phone && !enrichedLead.email && (
                  <p className="text-gray-500 text-sm mb-3">No contact info found yet</p>
                )}
                <button onClick={handleEnrichAll} disabled={isEnriching}
                  className={`${!enrichedLead.phone && !enrichedLead.email ? 'px-6 py-2.5' : 'px-4 py-2'} bg-emerald-600 text-white text-sm font-bold rounded-lg hover:bg-emerald-500 disabled:opacity-50 transition-colors`}
                  title="Finds contacts (phone, email, website) via Google Places, NY DOS, web scraping, and Hunter.io — then generates an AI summary">
                  {isEnriching ? 'Enriching...' : enrichedLead.enrichment_status === 'none' ? 'Enrich Lead' : 'Re-enrich Lead'}
                </button>
                <p className="text-[10px] text-gray-400 mt-1.5">Searches Google Places, NY DOS, web, and Hunter.io for contacts, then generates an AI summary.</p>
              </div>

              {/* All Contact Info */}
              <div className="bg-gray-50 rounded-xl p-4 space-y-3">
                <h3 className="text-xs font-bold text-gray-700 uppercase tracking-wider">Contact Information</h3>
                
                {/* Phones */}
                {(enrichedLead.phones?.length > 0 || enrichedLead.phone) && (
                  <div>
                    <label className="text-[10px] text-gray-400 uppercase">Phone</label>
                    {enrichedLead.phones?.length > 0 ? enrichedLead.phones.map((p, i) => (
                      <div key={i} className="flex items-center justify-between py-1">
                        <a href={`tel:${p.value}`} className="text-emerald-600 text-sm font-mono hover:underline">{p.value}</a>
                        <span className="text-[10px] text-gray-400">{p.source}</span>
                      </div>
                    )) : enrichedLead.phone && (
                      <a href={`tel:${enrichedLead.phone}`} className="text-emerald-600 text-sm font-mono hover:underline block">{enrichedLead.phone}</a>
                    )}
                  </div>
                )}

                {/* Emails */}
                {(enrichedLead.emails?.length > 0 || enrichedLead.email) && (
                  <div>
                    <label className="text-[10px] text-gray-400 uppercase">Email</label>
                    {enrichedLead.emails?.length > 0 ? enrichedLead.emails.map((e, i) => (
                      <div key={i} className="flex items-center justify-between py-1">
                        <a href={`mailto:${e.value}`} className="text-blue-600 text-sm hover:underline">{e.value}</a>
                        <span className="text-[10px] text-gray-400">{e.source}</span>
                      </div>
                    )) : enrichedLead.email && (
                      <a href={`mailto:${enrichedLead.email}`} className="text-blue-600 text-sm hover:underline block">{enrichedLead.email}</a>
                    )}
                  </div>
                )}

                {/* Website */}
                {enrichedLead.website && (
                  <div>
                    <label className="text-[10px] text-gray-400 uppercase">Website</label>
                    <a href={enrichedLead.website.startsWith('http') ? enrichedLead.website : `https://${enrichedLead.website}`} target="_blank" rel="noopener" className="text-purple-600 text-sm hover:underline block truncate">{enrichedLead.website}</a>
                  </div>
                )}

                {/* LinkedIn */}
                {enrichedLead.linkedin_url && (
                  <div>
                    <label className="text-[10px] text-gray-400 uppercase">LinkedIn</label>
                    <a href={enrichedLead.linkedin_url} target="_blank" rel="noopener" className="text-blue-600 text-sm hover:underline block">{enrichedLead.linkedin_url}</a>
                  </div>
                )}

                {!enrichedLead.phone && !enrichedLead.email && !enrichedLead.website && (
                  <p className="text-gray-400 text-sm italic">No contact info yet. Click "Find Contacts" above.</p>
                )}
              </div>

              {/* Company Research Results */}
              {companyResearch && (
                <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-4 space-y-2">
                  <h3 className="text-xs font-bold text-emerald-600 uppercase tracking-wider">Company Research</h3>
                  {companyResearch.owner_names?.length > 0 && <div><span className="text-xs text-gray-500">Owners: </span><span className="text-sm text-gray-700">{companyResearch.owner_names.join(', ')}</span></div>}
                  {companyResearch.year_established && <div><span className="text-xs text-gray-500">Est: </span><span className="text-sm text-gray-700">{companyResearch.year_established}</span></div>}
                  {companyResearch.service_areas?.length > 0 && <div><span className="text-xs text-gray-500">Areas: </span><span className="text-sm text-gray-700">{companyResearch.service_areas.join(', ')}</span></div>}
                  {companyResearch.description && <p className="text-sm text-gray-500 leading-relaxed">{companyResearch.description}</p>}
                </div>
              )}

              {/* NY DOS Info */}
              {dosInfo && (
                <div className="bg-gray-50 rounded-xl p-4 space-y-2">
                  <h3 className="text-xs font-bold text-gray-700 uppercase tracking-wider">NY DOS Corporation</h3>
                  <div className="grid grid-cols-2 gap-2 text-sm">
                    {dosInfo.entity_name && <div><span className="text-xs text-gray-400 block">Entity</span><span className="text-gray-700">{dosInfo.entity_name}</span></div>}
                    {dosInfo.entity_type && <div><span className="text-xs text-gray-400 block">Type</span><span className="text-gray-700">{dosInfo.entity_type}</span></div>}
                    {dosInfo.formation_date && <div><span className="text-xs text-gray-400 block">Formed</span><span className="text-gray-700">{dosInfo.formation_date}</span></div>}
                    {dosInfo.dos_id && <div><span className="text-xs text-gray-400 block">DOS ID</span><span className="text-gray-700">{dosInfo.dos_id}</span></div>}
                    {dosInfo.registered_agent && <div className="col-span-2"><span className="text-xs text-gray-400 block">Agent</span><span className="text-emerald-600">{dosInfo.registered_agent}</span></div>}
                  </div>
                </div>
              )}

              {/* HPD Registered Contacts */}
              {uniqueContacts.length > 0 && (
                <div className="bg-gray-50 rounded-xl p-4">
                  <h3 className="text-xs font-bold text-gray-700 uppercase tracking-wider mb-2">HPD Registered Contacts</h3>
                  <div className="space-y-1.5">
                    {uniqueContacts.map((contact: any, i: number) => (
                      <div key={i} className="flex items-center justify-between py-1">
                        <span className="text-sm text-gray-700">{contact.name}</span>
                        <span className="text-[10px] text-gray-400">{(contact.type || '').replace(/([A-Z])/g, ' $1').trim()}</span>
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
                  <label className="text-xs text-gray-400 uppercase tracking-wider block mb-2">Priority</label>
                  <div className="flex gap-1">
                    {[1, 2, 3, 4, 5].map(star => (
                      <button key={star} onClick={() => handlePriorityChange(star)} className="transition-transform hover:scale-110">
                        <svg className={`w-7 h-7 ${star <= priorityRank ? 'text-amber-500' : 'text-gray-300'}`} fill="currentColor" viewBox="0 0 20 20">
                          <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
                        </svg>
                      </button>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="text-xs text-gray-400 uppercase tracking-wider block mb-2">Next Follow-Up</label>
                  <div className="relative">
                    <input type="date" value={nextFollowUp} onChange={e => handleFollowUpChange(e.target.value)}
                      className={`w-full px-3 py-2 bg-white border rounded-lg text-sm font-mono ${
                        nextFollowUp && new Date(nextFollowUp) < new Date(new Date().toDateString()) ? 'border-rose-300 text-rose-600' :
                        nextFollowUp && new Date(nextFollowUp).toDateString() === new Date().toDateString() ? 'border-amber-300 text-amber-600' :
                        'border-gray-300 text-gray-900'
                      }`} />
                    {nextFollowUp && new Date(nextFollowUp) < new Date(new Date().toDateString()) && (
                      <span className="absolute -top-2 right-2 px-1.5 py-0.5 bg-rose-600 text-[9px] font-bold text-white rounded">OVERDUE</span>
                    )}
                  </div>
                </div>
              </div>

              {/* Outreach Status */}
              <div>
                <label className="text-xs text-gray-400 uppercase tracking-wider block mb-2">Outreach Status</label>
                <div className="flex flex-wrap gap-2">
                  {OUTREACH_STATUSES.map(status => (
                    <button key={status.value} onClick={() => handleSaveStatus(status.value)} disabled={isSaving}
                      className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                        outreachStatus === status.value ? `${status.color} ring-2 ring-offset-2 ring-offset-white ring-gray-300` :
                        'bg-gray-100 text-gray-500 hover:bg-gray-200 hover:text-gray-700'
                      }`}>
                      {status.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Outreach Log */}
              <div className="bg-gray-50 rounded-xl p-4">
                <div className="flex justify-between items-center mb-3">
                  <h3 className="text-xs font-bold text-gray-700 uppercase tracking-wider">Outreach Log</h3>
                  <button onClick={() => setShowAddOutreach(!showAddOutreach)} className="px-3 py-1 bg-blue-600 text-white text-xs font-medium rounded-lg hover:bg-blue-500 transition-colors">
                    + Log Attempt
                  </button>
                </div>
                {showAddOutreach && (
                  <div className="bg-white shadow-sm rounded-lg p-3 mb-3 space-y-2">
                    <div className="grid grid-cols-2 gap-2">
                      <select value={newOutreach.method} onChange={e => setNewOutreach({...newOutreach, method: e.target.value})}
                        className="px-3 py-1.5 bg-white border border-gray-300 rounded-lg text-xs text-gray-900">
                        {OUTREACH_METHODS.map(m => <option key={m} value={m}>{m.replace('_', ' ')}</option>)}
                      </select>
                      <select value={newOutreach.outcome} onChange={e => setNewOutreach({...newOutreach, outcome: e.target.value})}
                        className="px-3 py-1.5 bg-white border border-gray-300 rounded-lg text-xs text-gray-900">
                        {OUTREACH_OUTCOMES.map(o => <option key={o} value={o}>{o.replace(/_/g, ' ')}</option>)}
                      </select>
                    </div>
                    <textarea value={newOutreach.notes} onChange={e => setNewOutreach({...newOutreach, notes: e.target.value})}
                      placeholder="Notes..." rows={2} className="w-full px-3 py-2 bg-white border border-gray-300 rounded-lg text-xs text-gray-900 resize-none" />
                    <button onClick={handleAddOutreachAttempt} disabled={isSaving}
                      className="px-4 py-1.5 bg-blue-600 text-white text-xs font-medium rounded-lg hover:bg-blue-500 disabled:opacity-50">
                      {isSaving ? 'Saving...' : 'Save'}
                    </button>
                  </div>
                )}
                <div className="space-y-2 max-h-64 overflow-y-auto">
                  {outreachAttempts.length > 0 ? outreachAttempts.map((attempt, i) => (
                    <div key={i} className="p-2 bg-white shadow-sm rounded-lg">
                      <div className="flex items-center gap-2">
                        <span className="px-1.5 py-0.5 bg-blue-50 text-blue-600 text-[10px] rounded">{attempt.method}</span>
                        <span className="px-1.5 py-0.5 bg-gray-100 text-gray-500 text-[10px] rounded">{attempt.outcome?.replace(/_/g, ' ')}</span>
                        <span className="text-[10px] text-gray-400 ml-auto">{new Date(attempt.timestamp).toLocaleDateString()}</span>
                      </div>
                      {attempt.notes && <p className="text-xs text-gray-500 mt-1">{attempt.notes}</p>}
                    </div>
                  )) : (
                    <p className="text-gray-400 text-sm italic">No outreach attempts logged yet</p>
                  )}
                </div>
              </div>

              {/* Notes */}
              <div className="bg-gray-50 rounded-xl p-4">
                <div className="flex justify-between items-center mb-2">
                  <h3 className="text-xs font-bold text-gray-700 uppercase tracking-wider">Notes</h3>
                  <button onClick={handleSaveNotes} disabled={isSaving} className="px-3 py-1 bg-emerald-600 text-white text-xs font-medium rounded-lg hover:bg-emerald-500 disabled:opacity-50">
                    {isSaving ? 'Saving...' : 'Save'}
                  </button>
                </div>
                <textarea value={notes} onChange={e => setNotes(e.target.value)} rows={4} placeholder="Add notes about this lead..."
                  className="w-full px-3 py-2 bg-white border border-gray-300 rounded-lg text-sm text-gray-900 resize-none" />
              </div>
            </>
          )}

          {/* TAB: BUILDINGS */}
          {activeTab === 'buildings' && (
            <>
              {/* Search */}
              <input type="text" value={buildingSearch} onChange={e => setBuildingSearch(e.target.value)} placeholder="Search buildings..."
                className="w-full px-3 py-2 bg-white border border-gray-300 rounded-lg text-sm text-gray-900" />
              
              {/* Interactive Map (OpenStreetMap) */}
              {enrichedLead.buildings && enrichedLead.buildings.length > 0 && (
                <Suspense fallback={<div className="h-[250px] bg-gray-100 rounded-lg animate-pulse" />}>
                  <PortfolioMap buildings={enrichedLead.buildings} boro={enrichedLead.boro} boros={enrichedLead.boros} />
                </Suspense>
              )}

              {/* Building List */}
              <div className="space-y-1 max-h-96 overflow-y-auto">
                {(enrichedLead.buildings || [])
                  .filter((b: string) => !buildingSearch || String(b || '').toLowerCase().includes(buildingSearch.toLowerCase()))
                  .map((building: string, i: number) => (
                    <div key={i} className="flex items-center justify-between p-2 bg-white hover:bg-gray-50 border-b border-gray-100 rounded-lg">
                      <span className="text-sm text-gray-700">{building}</span>
                      <a href={`https://www.google.com/maps/search/${encodeURIComponent(building + ', New York, NY')}`} target="_blank" rel="noopener"
                        className="text-[10px] text-blue-600 hover:underline">Map</a>
                    </div>
                  ))}
                {(!enrichedLead.buildings || enrichedLead.buildings.length === 0) && <p className="text-gray-400 text-sm italic">No buildings data available</p>}
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
                  {/* Key Risks Summary */}
                  <div className="bg-rose-50 border border-rose-200 rounded-xl p-4">
                    <h3 className="text-xs font-bold text-rose-600 uppercase tracking-wider mb-2">Key Risks</h3>
                    <div className="space-y-1.5 text-sm">
                      {enrichedLead.violations_per_unit > 1.0 && (
                        <div className="flex items-center gap-2">
                          <span className="w-2 h-2 rounded-full bg-rose-500" />
                          <span className="text-gray-700">High violation density ({enrichedLead.violations_per_unit.toFixed(2)}/unit) — may indicate deferred maintenance</span>
                        </div>
                      )}
                      {enrichedLead.violation_class_c > 10 && (
                        <div className="flex items-center gap-2">
                          <span className="w-2 h-2 rounded-full bg-rose-500" />
                          <span className="text-gray-700">{enrichedLead.violation_class_c} Class C (immediately hazardous) violations</span>
                        </div>
                      )}
                      {!enrichedLead.phone && !enrichedLead.email && (
                        <div className="flex items-center gap-2">
                          <span className="w-2 h-2 rounded-full bg-amber-500" />
                          <span className="text-gray-700">No direct contact info found — may be difficult to reach</span>
                        </div>
                      )}
                      {enrichedLead.portfolio_size <= 5 && (
                        <div className="flex items-center gap-2">
                          <span className="w-2 h-2 rounded-full bg-amber-500" />
                          <span className="text-gray-700">Small portfolio ({enrichedLead.portfolio_size} buildings) — lower revenue potential</span>
                        </div>
                      )}
                      {enrichedLead.violations_per_unit <= 1.0 && enrichedLead.violation_class_c <= 10 && (enrichedLead.phone || enrichedLead.email) && enrichedLead.portfolio_size > 5 && (
                        <div className="flex items-center gap-2">
                          <span className="w-2 h-2 rounded-full bg-emerald-500" />
                          <span className="text-gray-700">No major red flags identified</span>
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="flex justify-end gap-2">
                    <button onClick={() => { navigator.clipboard.writeText(ddReport.report_markdown); toast.success('Copied to clipboard'); }}
                      className="px-3 py-1 bg-gray-100 text-gray-700 text-xs rounded-lg hover:bg-gray-200">Copy Markdown</button>
                    <button onClick={() => {
                      const printWindow = window.open('', '_blank');
                      if (printWindow) {
                        printWindow.document.write(`<html><head><title>DD Report - ${enrichedLead.company_name || enrichedLead.agent_name}</title>
                          <style>body{font-family:system-ui,sans-serif;max-width:800px;margin:40px auto;padding:0 20px;color:#1e293b}
                          h1{font-size:24px;border-bottom:2px solid #e2e8f0;padding-bottom:8px}
                          h2{font-size:18px;color:#3b82f6;margin-top:24px}
                          h3{font-size:14px;margin-top:16px}
                          li{margin:4px 0}p{line-height:1.6}
                          table{width:100%;border-collapse:collapse;margin:16px 0}
                          th,td{border:1px solid #e2e8f0;padding:6px 10px;text-align:left;font-size:13px}
                          th{background:#f8fafc;font-weight:600}
                          @media print{body{margin:20px}}</style></head><body>`);
                        printWindow.document.write(ddReport.report_markdown
                          .replace(/^# (.*)/gm, '<h1>$1</h1>')
                          .replace(/^## (.*)/gm, '<h2>$1</h2>')
                          .replace(/^### (.*)/gm, '<h3>$1</h3>')
                          .replace(/^- (.*)/gm, '<li>$1</li>')
                          .replace(/\n\n/g, '<p></p>')
                          .replace(/\n/g, '<br>'));
                        if (ddReport.comparables?.length > 0) {
                          printWindow.document.write('<h2>Comparables</h2><table><tr><th>Name</th><th>Score</th><th>Buildings</th><th>Revenue</th></tr>');
                          ddReport.comparables.forEach((c: any) => {
                            printWindow.document.write(`<tr><td>${c.name || c.lead_name || ''}</td><td>${c.score?.toFixed(1) || ''}</td><td>${c.portfolio_size || c.buildings || ''}</td><td>${c.estimated_annual_revenue ? formatCurrency(c.estimated_annual_revenue) : '—'}</td></tr>`);
                          });
                          printWindow.document.write('</table>');
                        }
                        printWindow.document.write('</body></html>');
                        printWindow.document.close();
                        printWindow.print();
                      }
                    }}
                      className="px-3 py-1 bg-indigo-700 text-white text-xs rounded-lg hover:bg-indigo-600 flex items-center gap-1">
                      <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M17 17h2a2 2 0 002-2v-4a2 2 0 00-2-2H5a2 2 0 00-2 2v4a2 2 0 002 2h2m2 4h6a2 2 0 002-2v-4a2 2 0 00-2-2H9a2 2 0 00-2 2v4a2 2 0 002 2zm8-12V5a2 2 0 00-2-2H9a2 2 0 00-2 2v4h10z"/></svg>
                      Print / PDF
                    </button>
                  </div>
                  <div className="prose prose-sm max-w-none">
                    {ddReport.report_markdown.split('\n').map((line, i) => {
                      if (line.startsWith('# ')) return <h1 key={i} className="text-xl font-bold text-gray-900 mt-4 mb-2">{line.slice(2)}</h1>;
                      if (line.startsWith('## ')) return <h2 key={i} className="text-lg font-bold text-blue-600 mt-3 mb-1">{line.slice(3)}</h2>;
                      if (line.startsWith('### ')) return <h3 key={i} className="text-sm font-bold text-gray-700 mt-3 mb-1">{line.slice(4)}</h3>;
                      if (line.startsWith('- ')) return <li key={i} className="text-gray-700 text-sm ml-4">{line.slice(2)}</li>;
                      if (line.trim() === '') return <br key={i} />;
                      return <p key={i} className="text-gray-500 text-sm leading-relaxed">{line}</p>;
                    })}
                  </div>
                  {ddReport.comparables?.length > 0 && (
                    <div className="border-t border-gray-200 pt-3">
                      <h3 className="text-xs font-bold text-gray-700 uppercase tracking-wider mb-2">Comparables</h3>
                      <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                          <thead><tr className="text-gray-500 text-xs uppercase">
                            <th className="text-left py-1 px-2">Name</th><th className="text-right py-1 px-2">Score</th><th className="text-right py-1 px-2">Buildings</th><th className="text-right py-1 px-2">Units</th><th className="text-right py-1 px-2">Revenue</th>
                          </tr></thead>
                          <tbody className="divide-y divide-gray-200">
                            {ddReport.comparables.map((comp: any, i: number) => (
                              <tr key={i} className="text-gray-700 hover:bg-gray-50">
                                <td className="py-1.5 px-2">{comp.name || comp.lead_name}</td>
                                <td className="py-1.5 px-2 text-right font-mono">
                                  <span className={comp.score >= 60 ? 'text-emerald-600' : comp.score >= 40 ? 'text-amber-600' : 'text-gray-500'}>
                                    {comp.score?.toFixed(1)}
                                  </span>
                                </td>
                                <td className="py-1.5 px-2 text-right font-mono">{comp.portfolio_size || comp.buildings}</td>
                                <td className="py-1.5 px-2 text-right font-mono text-blue-600">{comp.total_units?.toLocaleString() || '—'}</td>
                                <td className="py-1.5 px-2 text-right font-mono text-emerald-600">{comp.estimated_annual_revenue ? formatCurrency(comp.estimated_annual_revenue) : '—'}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {!ddReport && !isLoadingDD && (
                <div className="text-center py-10">
                  <p className="text-gray-400 text-sm">Generate a full due diligence report for this lead — includes portfolio analysis, financials, violation history, contacts, and comparison with similar companies.</p>
                </div>
              )}

              {isLoadingDD && !ddReport && (
                <div className="flex flex-col items-center justify-center py-16">
                  <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin mb-4" />
                  <p className="text-gray-500 text-sm">Generating report...</p>
                  <p className="text-gray-400 text-xs mt-1">This may take 15-30 seconds</p>
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
