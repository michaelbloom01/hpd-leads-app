import React, { useState, useEffect, useRef, useCallback, lazy, Suspense } from 'react';
import { toast } from 'react-hot-toast';
import { ApiLead, updateLead, addOutreachAttempt, enrichLeadAll, estimateLeadRevenue, OutreachAttempt } from '../services/api';
import { PIPELINE_STAGES, OUTREACH_STATUSES, OUTREACH_METHODS, OUTREACH_OUTCOMES, formatCurrency } from '../utils/format';

// Lazy-load map to avoid large initial bundle
const PortfolioMap = lazy(() => import('./PortfolioMap'));

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
  const [aiDescription, setAiDescription] = useState<string | null>(lead.business_summary || null);
  const [outreachAttempts, setOutreachAttempts] = useState<OutreachAttempt[]>(lead.outreach_attempts || []);
  const [showAddOutreach, setShowAddOutreach] = useState(false);
  const [newOutreach, setNewOutreach] = useState({ method: 'phone', outcome: 'no_answer', notes: '' });
  const [pipelineStage, setPipelineStage] = useState(lead.pipeline_stage || 'research');
  const [priorityRank, setPriorityRank] = useState(lead.priority_rank || 0);
  const [nextFollowUp, setNextFollowUp] = useState(lead.next_follow_up || '');
  const [buildingSearch, setBuildingSearch] = useState('');
  const [showEmailMenu, setShowEmailMenu] = useState(false);
  const [isEstimatingRevenue, setIsEstimatingRevenue] = useState(false);
  const [revenueEstimateFailed, setRevenueEstimateFailed] = useState(false);

  const modalRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setEnrichedLead(lead);
    setNotes(lead.notes || '');
    setOutreachStatus(lead.outreach_status || 'new');
    setOutreachAttempts(lead.outreach_attempts || []);
    setAiDescription(lead.business_summary || null);
    setPipelineStage(lead.pipeline_stage || 'research');
    setPriorityRank(lead.priority_rank || 0);
    setNextFollowUp(lead.next_follow_up || '');
    setActiveTab('overview');
    setShowEmailMenu(false);
    setIsEstimatingRevenue(false);
    setRevenueEstimateFailed(false);
  }, [lead]);

  // ESC to close
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  // Focus trap: cycle focus within modal
  const handleFocusTrap = useCallback((e: KeyboardEvent) => {
    if (e.key !== 'Tab' || !modalRef.current) return;
    const focusable = modalRef.current.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }, []);

  useEffect(() => {
    document.addEventListener('keydown', handleFocusTrap);
    modalRef.current?.focus();
    return () => document.removeEventListener('keydown', handleFocusTrap);
  }, [handleFocusTrap]);

  // Auto-estimate revenue once when a lead has units but missing persisted values.
  useEffect(() => {
    let cancelled = false;
    const needsEstimate = (enrichedLead.total_units || 0) > 0 && (enrichedLead.estimated_annual_revenue || 0) <= 0;
    if (!needsEstimate || isEstimatingRevenue || revenueEstimateFailed) return;

    const run = async () => {
      setIsEstimatingRevenue(true);
      setRevenueEstimateFailed(false);
      try {
        const updatedLead = await estimateLeadRevenue(enrichedLead.lead_id);
        if (!cancelled) {
          setEnrichedLead(updatedLead);
          onLeadUpdated?.(updatedLead);
          if ((updatedLead.estimated_annual_revenue || 0) <= 0) {
            setRevenueEstimateFailed(true);
          }
        }
      } catch (err) {
        if (!cancelled) {
          setRevenueEstimateFailed(true);
          console.error('Failed to estimate lead revenue:', err);
        }
      } finally {
        if (!cancelled) setIsEstimatingRevenue(false);
      }
    };

    run();
    return () => { cancelled = true; };
  }, [
    enrichedLead.lead_id,
    enrichedLead.total_units,
    enrichedLead.estimated_annual_revenue,
    isEstimatingRevenue,
    revenueEstimateFailed,
    onLeadUpdated,
  ]);

  // Guardrail: never leave revenue in a permanent "calculating" state.
  useEffect(() => {
    if (!isEstimatingRevenue) return;
    const timeout = window.setTimeout(() => {
      setIsEstimatingRevenue(false);
      setRevenueEstimateFailed(true);
    }, 20000);
    return () => window.clearTimeout(timeout);
  }, [isEstimatingRevenue, enrichedLead.lead_id]);

  const fallbackAnnualRevenue = ((enrichedLead.revenue_breakdown || []).reduce((sum: number, item: any) => {
    const monthlyGross = Number(item?.monthly_gross || 0);
    const feeRate = Number(item?.fee_rate || 0.05);
    return sum + (monthlyGross * feeRate * 12);
  }, 0));

  const fallbackMonthlyRevenue = fallbackAnnualRevenue > 0 ? fallbackAnnualRevenue / 12 : 0;

  // === Handlers ===
  const handleSaveStatus = async (newStatus: string) => {
    const prev = outreachStatus;
    setOutreachStatus(newStatus);
    setIsSaving(true);
    try { await updateLead(lead.lead_id, { outreach_status: newStatus }); } 
    catch (err) { setOutreachStatus(prev); console.error('Failed to update status:', err); toast.error('Failed to update status'); } 
    finally { setIsSaving(false); }
  };

  const handleSaveNotes = async () => {
    setIsSaving(true);
    try { await updateLead(lead.lead_id, { notes }); toast.success('Notes saved'); } 
    catch (err) { console.error('Failed to save notes:', err); toast.error('Failed to save notes'); } 
    finally { setIsSaving(false); }
  };

  const handlePipelineChange = async (stage: string) => {
    const prev = pipelineStage;
    setPipelineStage(stage);
    try {
      await updateLead(lead.lead_id, { pipeline_stage: stage });
      toast.success(`Pipeline: ${PIPELINE_STAGES.find(s => s.value === stage)?.label}`);
    } catch (err) { setPipelineStage(prev); console.error('Failed to update pipeline:', err); toast.error('Failed to update pipeline'); }
  };

  const handlePriorityChange = async (rank: number) => {
    const newRank = rank === priorityRank ? 0 : rank;
    const prev = priorityRank;
    setPriorityRank(newRank);
    try { await updateLead(lead.lead_id, { priority_rank: newRank }); } 
    catch (err) { setPriorityRank(prev); console.error('Failed to update priority:', err); toast.error('Failed to update priority'); }
  };

  const handleFollowUpChange = async (date: string) => {
    const prev = nextFollowUp;
    setNextFollowUp(date);
    try {
      await updateLead(lead.lead_id, { next_follow_up: date || null });
      toast.success(date ? `Follow-up: ${date}` : 'Follow-up cleared');
    } catch (err) { setNextFollowUp(prev); console.error('Failed to update follow-up:', err); toast.error('Failed to update'); }
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
        toast('Enrichment finished. No new contact data found this run.');
      }

      const nonBlockingErrors = (result.errors || []).filter((err: string) => {
        const lowered = String(err || '').toLowerCase();
        return !(lowered.includes('anthropic') && lowered.includes('not configured'));
      });
      if (nonBlockingErrors.length > 0) {
        console.warn('Enrichment partial errors:', nonBlockingErrors);
        toast(`Enrichment finished with ${nonBlockingErrors.length} warning(s).`);
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
    } catch (err) {
      console.error('Enrichment failed:', err);
      toast.error('Enrichment failed. Please try again.');
    } finally {
      setIsEnriching(false);
    }
  };

  const handleAddOutreachAttempt = async () => {
    if (!newOutreach.method || !newOutreach.outcome) return;
    setIsSaving(true);
    try {
      await addOutreachAttempt(lead.lead_id, {
        method: newOutreach.method,
        outcome: newOutreach.outcome,
        notes: newOutreach.notes,
      });
      toast.success('Outreach logged');
      setNewOutreach({ method: '', outcome: '', notes: '' });
      setShowAddOutreach(false);
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
    <div className="fixed inset-0 bg-black/20 backdrop-blur-sm z-50 flex items-center justify-center p-0 md:p-4" onClick={onClose} role="dialog" aria-modal="true" aria-labelledby="lead-detail-title">
      <div ref={modalRef} tabIndex={-1} className="bg-white border border-gray-200 md:rounded-2xl max-w-3xl w-full h-full md:h-auto md:max-h-[90vh] flex flex-col shadow-2xl outline-none" onClick={e => e.stopPropagation()}>
        
        {/* === STICKY HEADER === */}
        <div className="flex-shrink-0 border-b border-gray-200">
          {/* Top row: name + close */}
          <div className="p-4 sm:p-5 pb-3">
            {/* Close button — always top-right */}
            <div className="flex justify-between items-start">
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
                <h2 id="lead-detail-title" className="text-lg sm:text-xl font-bold text-gray-900 truncate">{enrichedLead.company_name || enrichedLead.agent_name || enrichedLead.owner_name}</h2>
              </div>
              <button onClick={onClose} aria-label="Close lead detail" className="p-2 text-gray-400 hover:text-gray-900 transition-colors ml-2 flex-shrink-0">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12"/>
                </svg>
              </button>
            </div>
            {/* Score + Revenue — below name on mobile, inline on desktop */}
            <div className="flex items-center gap-3 mt-2">
              <div className={`px-3 py-1 rounded-lg text-center ${
                (enrichedLead.score || 0) >= 60 ? 'bg-emerald-50' :
                (enrichedLead.score || 0) >= 40 ? 'bg-amber-50' : 'bg-gray-100'
              }`} title={`Lead quality score (0–100): ${(enrichedLead.score || 0) >= 60 ? 'Strong acquisition target' : (enrichedLead.score || 0) >= 40 ? 'Moderate potential' : 'Lower priority'}\nBased on portfolio size, building types, registration status, and data completeness`}>
                <div className={`text-xl sm:text-2xl font-bold font-mono ${
                  (enrichedLead.score || 0) >= 60 ? 'text-emerald-600' :
                  (enrichedLead.score || 0) >= 40 ? 'text-amber-600' : 'text-gray-500'
                }`}>{(enrichedLead.score || 0).toFixed(0)}</div>
                <div className="text-[9px] text-gray-400 uppercase font-bold">Score</div>
              </div>
              {(enrichedLead.estimated_annual_revenue || 0) > 0 && (
                <div className="text-right" title="Estimated annual management fee if acquired: Total Units × Avg Rent (by borough & building type) × 5% management fee">
                  <div className="text-base sm:text-lg font-bold font-mono text-emerald-600">{formatCurrency(enrichedLead.estimated_annual_revenue)}<span className="text-xs text-emerald-500">/yr</span></div>
                  <div className="text-[9px] text-gray-400 uppercase font-bold">Mgmt Fee</div>
                </div>
              )}
            </div>
          </div>

          {/* Quick actions + pipeline in header */}
          <div className="px-4 sm:px-5 pb-3 flex items-center gap-2 flex-wrap">
            {enrichedLead.phone && (
              <a href={`tel:${enrichedLead.phone}`} className="px-3 py-2 sm:py-1.5 bg-emerald-600 text-white text-xs font-bold rounded-lg hover:bg-emerald-500 transition-colors flex items-center gap-1">
                <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z"/></svg>
                Call
              </a>
            )}
            {enrichedLead.email && (
              <div className="relative">
                <button 
                  onClick={(e) => { e.preventDefault(); setShowEmailMenu(!showEmailMenu); }}
                  className="px-3 py-2 sm:py-1.5 bg-blue-600 text-white text-xs font-bold rounded-lg hover:bg-blue-500 transition-colors flex items-center gap-1">
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/></svg>
                  Email
                  <svg className={`w-3 h-3 ml-0.5 transition-transform ${showEmailMenu ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7"/></svg>
                </button>
                {showEmailMenu && (
                <div className="absolute top-full left-0 mt-1 w-56 bg-white border border-gray-200 rounded-lg shadow-xl z-50 py-1">
                  <a href={`mailto:${enrichedLead.email}?subject=Property Management Services — ${enrichedLead.company_name || enrichedLead.agent_name || 'Introduction'}&body=Hi ${enrichedLead.primary_contact || 'there'},%0D%0A%0D%0AI noticed your portfolio of ${enrichedLead.portfolio_size} buildings across ${(enrichedLead.boros || [enrichedLead.boro]).join(', ')} and wanted to introduce our property management services.%0D%0A%0D%0AWould you have time for a brief call this week?%0D%0A%0D%0ABest regards`}
                    onClick={() => { setShowEmailMenu(false); addOutreachAttempt(lead.lead_id, { method: 'email', outcome: 'sent_email', notes: 'Intro template sent' }).catch(() => toast.error('Failed to log outreach')); }}
                    className="block px-4 py-2 text-xs text-gray-700 hover:bg-gray-50 transition-colors">
                    Intro Template
                  </a>
                  <a href={`mailto:${enrichedLead.email}?subject=Following up — ${enrichedLead.company_name || enrichedLead.agent_name || ''}&body=Hi ${enrichedLead.primary_contact || 'there'},%0D%0A%0D%0AI wanted to follow up on my previous message regarding your ${enrichedLead.portfolio_size}-building portfolio.%0D%0A%0D%0AWe specialize in portfolios like yours in ${(enrichedLead.boros || [enrichedLead.boro]).join(' and ')} and believe we can add value.%0D%0A%0D%0AWould you be open to a brief conversation?%0D%0A%0D%0ABest regards`}
                    onClick={() => { setShowEmailMenu(false); addOutreachAttempt(lead.lead_id, { method: 'email', outcome: 'sent_email', notes: 'Follow-up template sent' }).catch(() => toast.error('Failed to log outreach')); }}
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
            <button onClick={openWebsite} className="px-3 py-2 sm:py-1.5 bg-purple-600 text-white text-xs font-bold rounded-lg hover:bg-purple-500 transition-colors">
              {enrichedLead.website ? 'Company Website' : 'Search'}
            </button>
            <button onClick={handleEnrichAll} disabled={isEnriching} className="px-3 py-2 sm:py-1.5 bg-emerald-600 text-white text-xs font-bold rounded-lg hover:bg-emerald-500 disabled:opacity-50 transition-colors" title="Find contacts, scrape website, and generate AI summary — all in one step">
              {isEnriching ? 'Enriching...' : 'Enrich Lead'}
            </button>
            <div className="hidden sm:block flex-1" />
            {/* Pipeline selector - dropdown for readability */}
            <div className="flex items-center gap-2 w-full sm:w-auto mt-1 sm:mt-0">
              <span className="text-[10px] text-gray-400 uppercase">Pipeline:</span>
              <select 
                value={pipelineStage} 
                onChange={(e) => handlePipelineChange(e.target.value)}
                className="px-3 py-2 sm:py-1.5 bg-white border border-gray-300 rounded-lg text-xs text-gray-900 font-bold flex-1 sm:flex-initial"
              >
                {PIPELINE_STAGES.map((stage) => (
                  <option key={stage.value} value={stage.value}>{stage.label}</option>
                ))}
              </select>
            </div>
          </div>

          {/* Tab bar — scrollable on mobile */}
          <div className="flex overflow-x-auto border-b border-gray-200 scrollbar-hide">
            {TABS.map(tab => (
              <button key={tab.id} onClick={() => setActiveTab(tab.id)}
                className={`flex-shrink-0 flex-1 min-w-0 px-3 sm:px-4 py-2.5 text-[10px] sm:text-xs font-bold uppercase tracking-wider transition-colors whitespace-nowrap ${
                  activeTab === tab.id ? 'text-gray-900 border-b-2 border-emerald-500' : 'text-gray-500 hover:text-gray-700'
                }`}>
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        {/* === SCROLLABLE TAB CONTENT === */}
        <div className="flex-1 overflow-y-auto p-4 sm:p-5 space-y-4 sm:space-y-5">

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
                  ) : fallbackAnnualRevenue > 0 ? (
                    <div>
                      <div className="text-2xl font-bold font-mono text-emerald-600">{formatCurrency(fallbackAnnualRevenue)}<span className="text-sm text-emerald-500">/yr</span></div>
                      <div className="text-sm font-mono text-gray-500 mt-1">{formatCurrency(fallbackMonthlyRevenue)}<span className="text-xs text-gray-400">/mo</span></div>
                      <div className="text-[10px] text-gray-400 mt-2">Live estimate shown; saving to lead record...</div>
                    </div>
                  ) : isEstimatingRevenue ? (
                    <div className="text-gray-400 text-sm">Estimating revenue...</div>
                  ) : revenueEstimateFailed ? (
                    <div className="text-amber-600 text-sm">Revenue estimate unavailable right now. Try "Enrich Lead" or reopen this lead.</div>
                  ) : (
                    <div className="text-gray-400 text-sm">{(enrichedLead.total_units || 0) > 0 ? 'Revenue not available yet' : 'No unit data available'}</div>
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
              <div className="grid grid-cols-3 sm:grid-cols-3 gap-2 sm:gap-3">
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
                    <p className="text-[10px] text-gray-400 mb-3">Score is weighted across 6 factors: Condo/Co-op % (35%), Density (20%), Units (15%), Location (10%), Professional (10%), Contact (10%). Max 100.</p>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      {Object.entries(enrichedLead.score_breakdown).map(([key, value]) => {
                        const SCORE_LABELS: Record<string, string> = {
                          portfolio_size: 'Portfolio Size',
                          total_units: 'Total Units',
                          building_diversity: 'Building Mix',
                          building_types: 'Building Types',
                          contact_completeness: 'Contact Info Available',
                          data_quality: 'Data Completeness',
                          violation_density: 'Violation Density',
                          condo_coop: 'Condo/Co-op %',
                          density: 'Unit Density',
                          units: 'Unit Count',
                          location: 'Location',
                          professional: 'Professional Mgmt',
                          contact: 'Contact Info',
                        };
                        const label = SCORE_LABELS[key] || key.replace(/_/g, ' ');
                        const numVal = typeof value === 'number' ? value : 0;
                        return (
                          <div key={key} className="flex items-center justify-between text-xs">
                            <span className="text-gray-500">{label}</span>
                            <span className={`font-mono font-medium ${numVal > 0 ? 'text-emerald-600' : 'text-gray-300'}`}>{numVal.toFixed(1)}</span>
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
                {enrichedLead.enrichment_status === 'complete' ? 'Fully enriched — contacts, company website, and AI summary found' :
                 enrichedLead.enrichment_status === 'partial' ? 'Partially enriched — some data found. Click "Re-enrich" to try again.' :
                 enrichedLead.enrichment_status === 'failed' ? 'No contact matches found yet — try "Re-enrich" or search manually' :
                 'Not yet enriched — click "Enrich Lead" to find contacts, company website, and generate a summary'}
              </div>

              {/* Single Enrich Action */}
              <div className={`${!enrichedLead.phone && !enrichedLead.email ? 'bg-emerald-50 border border-emerald-200 rounded-xl p-4 text-center' : ''}`}>
                {!enrichedLead.phone && !enrichedLead.email && (
                  <p className="text-gray-500 text-sm mb-3">No contact info found yet</p>
                )}
                <button onClick={handleEnrichAll} disabled={isEnriching}
                  className={`${!enrichedLead.phone && !enrichedLead.email ? 'px-6 py-2.5' : 'px-4 py-2'} bg-emerald-600 text-white text-sm font-bold rounded-lg hover:bg-emerald-500 disabled:opacity-50 transition-colors`}
                  title="Finds contacts (phone, email, company website) via Google Places, NY DOS, web scraping, and Hunter.io — then generates an AI summary">
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
                    <label className="text-[10px] text-gray-400 uppercase">Company Website</label>
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

            </>
          )}

          {/* TAB: PIPELINE & OUTREACH */}
          {activeTab === 'pipeline' && (
            <>
              {/* Priority & Follow-Up */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
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
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
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
            <div className="flex flex-col items-center justify-center py-16 text-center space-y-4">
              <div className="w-16 h-16 rounded-full bg-indigo-50 flex items-center justify-center">
                <svg className="w-8 h-8 text-indigo-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
                </svg>
              </div>
              <div>
                <h3 className="text-lg font-bold text-gray-900 mb-1">Due Diligence Reports</h3>
                <p className="text-sm text-gray-500 max-w-sm">
                  Automated DD reports with portfolio analysis, financials, violation history, and comparable companies are coming soon.
                </p>
              </div>

              {/* Quick Risk Snapshot (available now) */}
              <div className="w-full max-w-md bg-gray-50 border border-gray-200 rounded-xl p-4 text-left mt-4">
                <h4 className="text-xs font-bold text-gray-700 uppercase tracking-wider mb-3">Quick Risk Snapshot</h4>
                <div className="space-y-2 text-sm">
                  {enrichedLead.violations_per_unit > 1.0 && (
                    <div className="flex items-center gap-2">
                      <span className="w-2 h-2 rounded-full bg-rose-500 flex-shrink-0" />
                      <span className="text-gray-700">High violation density ({enrichedLead.violations_per_unit.toFixed(2)}/unit)</span>
                    </div>
                  )}
                  {enrichedLead.violation_class_c > 10 && (
                    <div className="flex items-center gap-2">
                      <span className="w-2 h-2 rounded-full bg-rose-500 flex-shrink-0" />
                      <span className="text-gray-700">{enrichedLead.violation_class_c} Class C violations</span>
                    </div>
                  )}
                  {!enrichedLead.phone && !enrichedLead.email && (
                    <div className="flex items-center gap-2">
                      <span className="w-2 h-2 rounded-full bg-amber-500 flex-shrink-0" />
                      <span className="text-gray-700">No direct contact info found</span>
                    </div>
                  )}
                  {enrichedLead.portfolio_size <= 5 && (
                    <div className="flex items-center gap-2">
                      <span className="w-2 h-2 rounded-full bg-amber-500 flex-shrink-0" />
                      <span className="text-gray-700">Small portfolio ({enrichedLead.portfolio_size} buildings)</span>
                    </div>
                  )}
                  {enrichedLead.violations_per_unit <= 1.0 && enrichedLead.violation_class_c <= 10 && (enrichedLead.phone || enrichedLead.email) && enrichedLead.portfolio_size > 5 && (
                    <div className="flex items-center gap-2">
                      <span className="w-2 h-2 rounded-full bg-emerald-500 flex-shrink-0" />
                      <span className="text-gray-700">No major red flags identified</span>
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default LeadDetail;
