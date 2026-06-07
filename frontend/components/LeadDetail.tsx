import React, { useState, useEffect, useRef, useCallback, lazy, Suspense } from 'react';
import { toast } from 'react-hot-toast';
import { useNavigate } from 'react-router-dom';
import { ApiLead, fetchLead, fetchLeadLineage, type LeadLineageResponse, updateLead, addOutreachAttempt, enrichLeadAll, estimateLeadRevenue, OutreachAttempt, fetchLeadContacts, downloadPortfolioContactsWorkbook } from '../services/api';
import { addBuildingToPipeline, fetchBuildings, type BuildingRow, type BuildingContactEntry } from '../services/buildings-api';
import { PIPELINE_STAGES, OUTREACH_STATUSES, OUTREACH_METHODS, OUTREACH_OUTCOMES, formatCurrency } from '../utils/format';
import { getLeadDisplayName } from '../utils/leads';

// Lazy-load map to avoid large initial bundle
const PortfolioMap = lazy(() => import('./PortfolioMap'));

type TabId = 'overview' | 'contacts' | 'pipeline' | 'buildings' | 'dd';

const formatRelativeDate = (value: string | null | undefined): string => {
  if (!value) return '--';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const days = Math.floor(diffMs / (1000 * 60 * 60 * 24));
  if (days < 1) return 'Today';
  if (days === 1) return '1 day ago';
  if (days < 30) return `${days} days ago`;
  const months = Math.floor(days / 30);
  if (months === 1) return '1 month ago';
  if (months < 12) return `${months} months ago`;
  const years = Math.floor(months / 12);
  return years === 1 ? '1 year ago' : `${years} years ago`;
};

const formatAbsoluteDate = (value: string | null | undefined): string => {
  if (!value) return '--';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString();
};

interface Props {
  lead: ApiLead;
  onClose: () => void;
  onLeadUpdated?: (lead: ApiLead) => void;
}

const LeadDetail: React.FC<Props> = ({ lead, onClose, onLeadUpdated }) => {
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState<TabId>('overview');
  const [isEnriching, setIsEnriching] = useState(false);
  const [isExportingContacts, setIsExportingContacts] = useState(false);
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
  const [linkedBuildings, setLinkedBuildings] = useState<BuildingRow[]>([]);
  const [loadingLinkedBuildings, setLoadingLinkedBuildings] = useState(false);
  const [leadLineage, setLeadLineage] = useState<LeadLineageResponse | null>(null);
  const [pipelineAddBusy, setPipelineAddBusy] = useState<Record<string, boolean>>({});
  const [buildingContacts, setBuildingContacts] = useState<{ bbl: string; address: string; outreach_status?: string | null; contacts: BuildingContactEntry[] }[]>([]);
  const [loadingBuildingContacts, setLoadingBuildingContacts] = useState(false);
  const [enrichmentElapsedSec, setEnrichmentElapsedSec] = useState(0);

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

  // Always refresh with canonical lead detail on open.
  // LeadTable rows are intentionally slim and can be stale/missing nested fields.
  useEffect(() => {
    let cancelled = false;
    const loadFullLead = async () => {
      try {
        const fullLead = await fetchLead(lead.lead_id);
        if (cancelled) return;
        setEnrichedLead(fullLead);
        setNotes(fullLead.notes || '');
        setOutreachStatus(fullLead.outreach_status || 'new');
        setOutreachAttempts(fullLead.outreach_attempts || []);
        setAiDescription(fullLead.business_summary || null);
        setPipelineStage(fullLead.pipeline_stage || 'research');
        setPriorityRank(fullLead.priority_rank || 0);
        setNextFollowUp(fullLead.next_follow_up || '');
        onLeadUpdated?.(fullLead);
      } catch (err) {
        console.error('Failed to load full lead detail:', err);
      }
    };
    loadFullLead();
    return () => { cancelled = true; };
  }, [lead.lead_id, onLeadUpdated]);

  useEffect(() => {
    let cancelled = false;
    const loadLinkedBuildings = async () => {
      if (activeTab !== 'buildings' && activeTab !== 'overview') return;
      setLoadingLinkedBuildings(true);
      try {
        const res = await fetchBuildings({
          lead_id: enrichedLead.lead_id,
          sort_by: 'address',
          sort_dir: 'asc',
          limit: 500,
          offset: 0,
        });
        if (cancelled) return;
        setLinkedBuildings(res.buildings || []);
      } catch {
        if (!cancelled) setLinkedBuildings([]);
      } finally {
        if (!cancelled) setLoadingLinkedBuildings(false);
      }
    };
    loadLinkedBuildings();
    return () => { cancelled = true; };
  }, [activeTab, enrichedLead.lead_id]);

  useEffect(() => {
    let cancelled = false;
    const loadLeadLineage = async () => {
      try {
        const lineage = await fetchLeadLineage(lead.lead_id);
        if (!cancelled) setLeadLineage(lineage);
      } catch (err) {
        if (!cancelled) setLeadLineage(null);
      }
    };
    loadLeadLineage();
    return () => { cancelled = true; };
  }, [lead.lead_id]);

  useEffect(() => {
    let cancelled = false;
    const loadBuildingContacts = async () => {
      if (activeTab !== 'contacts') return;
      setLoadingBuildingContacts(true);
      try {
        const contactsRes = await fetchLeadContacts(enrichedLead.lead_id);
        if (cancelled) return;
        const results = (contactsRes.buildings || []).filter((b) => (b.contacts || []).length > 0);
        setBuildingContacts(results);
      } catch {
        if (!cancelled) setBuildingContacts([]);
      } finally {
        if (!cancelled) setLoadingBuildingContacts(false);
      }
    };
    loadBuildingContacts();
    return () => { cancelled = true; };
  }, [activeTab, enrichedLead.lead_id]);

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

  useEffect(() => {
    if (!isEnriching) {
      setEnrichmentElapsedSec(0);
      return;
    }
    const started = Date.now();
    const intervalId = window.setInterval(() => {
      setEnrichmentElapsedSec(Math.floor((Date.now() - started) / 1000));
    }, 1000);
    return () => window.clearInterval(intervalId);
  }, [isEnriching]);

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
          toast.error('Revenue estimation failed. Try "Enrich Lead" or reopen.');
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

  const fallbackAnnualRevenue = ((enrichedLead.revenue_breakdown || []).reduce((sum: number, item) => {
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
      toast.error('Enrichment failed. Please try again. This action can take up to 1-2 minutes when web lookup is slow.');
    } finally {
      setIsEnriching(false);
    }
  };

  const handleExportPortfolioContacts = async () => {
    const companyName = getLeadDisplayName(enrichedLead);
    if (!companyName) {
      toast.error('No company name available for export');
      return;
    }
    setIsExportingContacts(true);
    try {
      const { blob, filename } = await downloadPortfolioContactsWorkbook(companyName, enrichedLead.lead_id);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      toast.success('Portfolio workbook downloaded');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to export portfolio contacts');
    } finally {
      setIsExportingContacts(false);
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
    if (!url) url = `https://www.google.com/search?q=${encodeURIComponent(`${getLeadDisplayName(enrichedLead)} property management NYC`)}`;
    window.open(url, '_blank');
  };

  const toBuildingRouteParam = (raw: string | null | undefined): string | null => {
    const trimmed = String(raw ?? '').trim();
    if (!trimmed) return null;
    return encodeURIComponent(trimmed);
  };

  const openBuildingDetail = (bbl: string) => {
    const routeParam = toBuildingRouteParam(bbl);
    if (!routeParam) return;
    onClose();
    navigate(`/buildings/${routeParam}`);
  };

  const handleAddBuildingToPipeline = async (bbl: string) => {
    setPipelineAddBusy((prev) => ({ ...prev, [bbl]: true }));
    try {
      await addBuildingToPipeline(bbl);
      setLinkedBuildings((prev) =>
        prev.map((b) => (b.bbl === bbl ? { ...b, outreach_status: 'pipeline' } : b)),
      );
      toast.success('Building added to pipeline');
    } catch {
      toast.error('Failed to add building to pipeline');
    } finally {
      setPipelineAddBusy((prev) => ({ ...prev, [bbl]: false }));
    }
  };

  const TABS: { id: TabId; label: string }[] = [
    { id: 'overview', label: 'Overview' },
    { id: 'contacts', label: 'Contacts' },
    { id: 'pipeline', label: 'Pipeline' },
    { id: 'buildings', label: `Buildings (${enrichedLead.portfolio_size || 0})` },
    { id: 'dd', label: 'Due Diligence' },
  ];
  const hasDirectContact = Boolean(enrichedLead.phone || enrichedLead.email);
  const nextActionLabel = !hasDirectContact
    ? 'Run Enrich Lead to gather contact coverage'
    : pipelineStage === 'research'
      ? 'Move to First Contact once outreach copy is ready'
      : pipelineStage === 'first_contact'
        ? 'Log outreach and set a follow-up date'
        : pipelineStage === 'follow_up'
          ? (nextFollowUp ? `Prepare for ${formatAbsoluteDate(nextFollowUp)} follow-up` : 'Set the next follow-up date')
          : 'Review open diligence items and advance the deal';
  const duplicateSummary = leadLineage?.canonical_entity
    ? `Mapped to canonical entity ${leadLineage.canonical_entity.display_name || leadLineage.canonical_entity.normalized_name || leadLineage.canonical_entity.canonical_entity_id}`
    : leadLineage?.sibling_count
      ? `${leadLineage.sibling_count} related workflow row${leadLineage.sibling_count === 1 ? '' : 's'} hidden in Leads`
      : 'No duplicate cohort surfaced for this lead';

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
                <h2 id="lead-detail-title" className="text-lg sm:text-xl font-bold text-gray-900 truncate">{getLeadDisplayName(enrichedLead)}</h2>
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
                  <a href={`mailto:${enrichedLead.email}?subject=Property Management Services — ${getLeadDisplayName(enrichedLead) || 'Introduction'}&body=Hi ${enrichedLead.primary_contact || 'there'},%0D%0A%0D%0AI noticed your portfolio of ${enrichedLead.portfolio_size} buildings across ${(enrichedLead.boros || [enrichedLead.boro]).join(', ')} and wanted to introduce our property management services.%0D%0A%0D%0AWould you have time for a brief call this week?%0D%0A%0D%0ABest regards`}
                    onClick={() => setShowEmailMenu(false)}
                    className="block px-4 py-2 text-xs text-gray-700 hover:bg-gray-50 transition-colors">
                    Intro Template
                  </a>
                  <a href={`mailto:${enrichedLead.email}?subject=Following up — ${getLeadDisplayName(enrichedLead)}&body=Hi ${enrichedLead.primary_contact || 'there'},%0D%0A%0D%0AI wanted to follow up on my previous message regarding your ${enrichedLead.portfolio_size}-building portfolio.%0D%0A%0D%0AWe specialize in portfolios like yours in ${(enrichedLead.boros || [enrichedLead.boro]).join(' and ')} and believe we can add value.%0D%0A%0D%0AWould you be open to a brief conversation?%0D%0A%0D%0ABest regards`}
                    onClick={() => setShowEmailMenu(false)}
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
            <button
              onClick={handleExportPortfolioContacts}
              disabled={isExportingContacts}
              className="px-3 py-2 sm:py-1.5 bg-gray-900 text-white text-xs font-bold rounded-lg hover:bg-gray-800 disabled:opacity-50 transition-colors"
              title="Download all buildings, contacts, roles, sources, and confidence hints for this portfolio"
            >
              {isExportingContacts ? 'Exporting...' : 'Export Contacts'}
            </button>
            <button onClick={handleEnrichAll} disabled={isEnriching} className="px-3 py-2 sm:py-1.5 bg-emerald-600 text-white text-xs font-bold rounded-lg hover:bg-emerald-500 disabled:opacity-50 transition-colors" title="Find contacts, scrape website, and generate AI summary — all in one step">
              {isEnriching ? `Enriching... ${enrichmentElapsedSec}s` : 'Enrich Lead'}
            </button>
            {isEnriching && (
              <span className="text-[10px] text-gray-500">
                Searching public sources and generating summary. This can take 30-120 seconds.
              </span>
            )}
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
          <div className="mx-4 sm:mx-5 mb-3 grid grid-cols-1 md:grid-cols-4 gap-3">
            <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 md:col-span-2">
              <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500">Next Best Action</div>
              <p className="mt-1 text-sm font-medium text-gray-900">{nextActionLabel}</p>
              <p className="mt-1 text-xs text-gray-500">
                {hasDirectContact ? 'Contact coverage is ready for outreach work.' : 'Use enrichment before starting outreach.'}
              </p>
            </div>
            <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
              <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500">Follow-Up</div>
              <p className="mt-1 text-sm font-medium text-gray-900">{nextFollowUp ? formatAbsoluteDate(nextFollowUp) : 'Not scheduled'}</p>
              <p className="mt-1 text-xs text-gray-500">{nextFollowUp ? formatRelativeDate(nextFollowUp) : 'Set a date once outreach is logged.'}</p>
            </div>
            <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
              <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500">Identity And Audit</div>
              <p className="mt-1 text-sm font-medium text-gray-900">
                {leadLineage?.canonical_entity ? 'Canonical entity linked' : leadLineage?.sibling_count ? 'Review duplicate cohort' : 'Single workflow row'}
              </p>
              <p className="mt-1 text-xs text-gray-500">{duplicateSummary}</p>
            </div>
          </div>
          {leadLineage && leadLineage.sibling_count > 0 && (
            <div className="mx-4 sm:mx-5 mb-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
              <div className="text-xs font-bold uppercase tracking-wider text-amber-700">Potential duplicate records hidden in list view</div>
              <p className="mt-1 text-sm text-amber-800">
                This entity has {leadLineage.sibling_count} sibling lead record{leadLineage.sibling_count === 1 ? '' : 's'} with the same display identity. The Leads table now hides likely duplicates to reduce clutter, but the underlying rows remain available for audit.
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                {leadLineage.sibling_leads.map((sibling) => (
                  <span key={sibling.lead_id} className="rounded-full border border-amber-200 bg-white px-2 py-1 text-[11px] text-amber-800">
                    {sibling.lead_id} · {sibling.pipeline_stage || 'research'}
                  </span>
                ))}
              </div>
            </div>
          )}

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
                          {(enrichedLead.revenue_breakdown && enrichedLead.revenue_breakdown.length > 0) ? (
                            enrichedLead.revenue_breakdown.map((item: { label?: string; estimated_units?: number; rent_per_unit?: number; monthly_gross: number }, i: number) => (
                              <div key={i} className="flex items-center justify-between text-xs">
                                <span className="text-gray-500">{item.label}: <span className="text-gray-700 font-mono">{item.estimated_units?.toLocaleString()}</span> units @ <span className="text-gray-700 font-mono">${item.rent_per_unit?.toLocaleString()}</span>/mo</span>
                                <span className="text-emerald-600 font-mono">{formatCurrency(item.monthly_gross * 0.05 * 12)}/yr</span>
                              </div>
                            ))
                          ) : (
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
                    <div className="flex items-center gap-2 text-gray-500 text-sm">
                      <div className="w-4 h-4 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin flex-shrink-0" />
                      Estimating revenue...
                    </div>
                  ) : revenueEstimateFailed ? (
                    <div className="text-amber-600 text-sm font-medium">Revenue estimation failed. Try "Enrich Lead" or reopen this lead.</div>
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
                    <h3 className="text-xs font-bold text-gray-700 uppercase tracking-wider">Housing Violations</h3>
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
                <div className="bg-gray-50 rounded-xl p-3 text-center" title="Total number of buildings managed by this entity per city registration records">
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
                  <span className="text-[10px] text-gray-400">
                    {isEnriching ? 'Updating from the single enrich action above' : aiDescription ? 'Generated from latest enrichment run' : 'Generated after enrichment'}
                  </span>
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
              {(linkedBuildings.length > 0 || (enrichedLead.buildings && enrichedLead.buildings.length > 0)) && (
                <div className="bg-gray-50 rounded-xl p-4">
                  <h3 className="text-xs font-bold text-gray-700 uppercase tracking-wider mb-3">Portfolio Footprint</h3>
                  <Suspense fallback={<div className="h-[250px] bg-gray-100 rounded-lg animate-pulse" />}>
                    <PortfolioMap
                      buildings={linkedBuildings.length > 0
                        ? linkedBuildings
                            .filter((b) => Boolean(b.address))
                            .map((b) => ({
                              address: b.address,
                              borough: b.borough || undefined,
                              latitude: b.latitude ?? null,
                              longitude: b.longitude ?? null,
                              coordinate_source: b.coordinate_source ?? null,
                              coordinate_precision: b.coordinate_precision ?? null,
                            }))
                        : enrichedLead.buildings}
                      boro={enrichedLead.boro}
                      boros={enrichedLead.boros}
                    />
                  </Suspense>
                </div>
              )}

              {/* Building contacts summary → link to Contacts tab */}
              {buildingContacts.length > 0 && (
                <button
                  onClick={() => setActiveTab('contacts')}
                  className="w-full text-left bg-blue-50 border border-blue-200 rounded-xl p-3 hover:bg-blue-100 transition-colors"
                >
                  <span className="text-xs font-bold text-blue-700 uppercase tracking-wider">Building Contacts</span>
                  <span className="text-sm text-blue-600 ml-2">
                    {buildingContacts.reduce((sum, b) => sum + b.contacts.length, 0)} contacts found across {buildingContacts.length} buildings →
                  </span>
                </button>
              )}
            </>
          )}

          {/* TAB: CONTACTS & RESEARCH */}
          {activeTab === 'contacts' && (
            <>
              {/* Enrichment Status Banner */}
              <div className={`rounded-lg px-3 py-2 text-xs font-medium flex items-center gap-2 ${
                isEnriching ? 'bg-blue-50 border border-blue-200 text-blue-700' :
                enrichedLead.enrichment_status === 'complete' ? 'bg-emerald-50 border border-emerald-200 text-emerald-700' :
                enrichedLead.enrichment_status === 'partial' ? 'bg-amber-50 border border-amber-200 text-amber-700' :
                enrichedLead.enrichment_status === 'failed' ? 'bg-rose-50 border border-rose-200 text-rose-700' :
                'bg-gray-50 border border-gray-200 text-gray-500'
              }`}>
                <span className="text-sm">
                  {isEnriching ? '◌' :
                   enrichedLead.enrichment_status === 'complete' ? '●' :
                   enrichedLead.enrichment_status === 'partial' ? '◐' :
                   enrichedLead.enrichment_status === 'failed' ? '●' : '○'}
                </span>
                {isEnriching ? `Enrichment in progress for ${enrichmentElapsedSec}s. Public web lookups and summary generation can take up to 1-2 minutes.` :
                 enrichedLead.enrichment_status === 'complete' ? 'Enrichment returned strong coverage: direct contact info and a company profile are available.' :
                 enrichedLead.enrichment_status === 'partial' ? 'Enrichment returned some useful data, but not every source produced a result.' :
                 enrichedLead.enrichment_status === 'failed' ? 'Enrichment ran but did not find public contact matches yet.' :
                 'Enrichment has not run yet. Use the single "Enrich Lead" action in the header to gather contacts and a profile.'}
              </div>

              {/* Single Enrich Action Guidance */}
              <div className={`${!enrichedLead.phone && !enrichedLead.email ? 'bg-emerald-50 border border-emerald-200 rounded-xl p-4' : 'bg-gray-50 rounded-xl p-4'}`}>
                <p className="text-sm text-gray-600">
                  Use the single `Enrich Lead` action in the header to search Google Places, NY DOS, the public web, and Hunter, then refresh the company overview from that same run.
                </p>
                <p className="text-[10px] text-gray-400 mt-1.5">
                  The result may be `partial` when only some sources match. That is expected and safer than implying full coverage.
                </p>
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
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] text-gray-400">{p.source}</span>
                          {p.source_url && (
                            <a
                              href={p.source_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-[10px] text-blue-600 hover:underline"
                            >
                              Verify
                            </a>
                          )}
                        </div>
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
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] text-gray-400">{e.source}</span>
                          {e.source_url && (
                            <a
                              href={e.source_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-[10px] text-blue-600 hover:underline"
                            >
                              Verify
                            </a>
                          )}
                        </div>
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

              {/* Building Contacts — aggregated from linked buildings */}
              <div className="bg-gray-50 rounded-xl p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <h3 className="text-xs font-bold text-gray-700 uppercase tracking-wider">Building Contacts</h3>
                  {buildingContacts.length > 0 && (
                    <span className="text-[10px] text-gray-400">
                      {buildingContacts.reduce((sum, b) => sum + b.contacts.length, 0)} contacts across {buildingContacts.length} buildings
                    </span>
                  )}
                </div>
                <p className="text-[11px] text-gray-500">
                  Building-linked evidence from HPD and NY DOS. Useful for board and ownership research, but not always the direct PM company contact.
                </p>
                {loadingBuildingContacts ? (
                  <div className="flex items-center gap-2 text-gray-500 text-sm py-2">
                    <div className="w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin flex-shrink-0" />
                    Loading building contacts...
                  </div>
                ) : buildingContacts.length > 0 ? (
                  <div className="space-y-4 max-h-80 overflow-y-auto">
                    {buildingContacts.map(building => (
                      <div key={building.bbl}>
                        <div className="flex items-center gap-2 mb-1">
                          <span className="text-xs font-medium text-gray-700">{building.address}</span>
                          {building.outreach_status && building.outreach_status !== 'none' && (
                            <span className="px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 text-[10px] uppercase">
                              Contacted
                            </span>
                          )}
                          <button
                            onClick={() => {
                              const routeParam = toBuildingRouteParam(building.bbl);
                              if (!routeParam) return;
                              navigate(`/buildings/${routeParam}`);
                            }}
                            className="text-[10px] text-blue-600 hover:underline"
                          >
                            View →
                          </button>
                        </div>
                        <div className="space-y-1">
                          {building.contacts.map((c, i) => (
                            <div key={i} className={`flex items-center gap-2 py-1 px-2 rounded text-xs ${c.is_decision_maker ? 'bg-green-50 border-l-2 border-green-400' : 'bg-white'}`}>
                              <span className="font-medium text-gray-800 min-w-[120px]">
                                {c.is_decision_maker ? '★ ' : ''}{c.name}
                              </span>
                              <span className="text-gray-500">{c.role}</span>
                              {c.board_role && (
                                <span className="px-1.5 py-0.5 rounded bg-purple-50 text-purple-700 text-[10px]">
                                  {c.board_role}
                                </span>
                              )}
                              {c.source_url ? (
                                <a
                                  href={c.source_url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className={`px-1.5 py-0.5 rounded hover:underline ${
                                    c.source === 'NY DOS Filing' ? 'bg-blue-50 text-blue-600' :
                                    c.source === 'NY DOS Snapshot' ? 'bg-indigo-50 text-indigo-600' :
                                    'bg-gray-100 text-gray-500'
                                  }`}
                                >
                                  {c.source}
                                </a>
                              ) : (
                                <span className={`px-1.5 py-0.5 rounded ${
                                  c.source === 'NY DOS Filing' ? 'bg-blue-50 text-blue-600' :
                                  c.source === 'NY DOS Snapshot' ? 'bg-indigo-50 text-indigo-600' :
                                  'bg-gray-100 text-gray-500'
                                }`}>{c.source}</span>
                              )}
                              <span
                                className="text-[10px] text-gray-400"
                                title={`Published: ${formatAbsoluteDate(c.filing_date || c.snapshot_as_of || c.publication_date || c.as_of_date)}`}
                              >
                                {formatRelativeDate(c.filing_date || c.snapshot_as_of || c.publication_date || c.as_of_date)}
                              </span>
                              {c.confidence_hint && (
                                <span className={`px-1.5 py-0.5 rounded text-[10px] ${c.confidence_hint === 'Likely board member (resident)' ? 'bg-green-100 text-green-700' : 'bg-gray-200 text-gray-600'}`}>
                                  {c.confidence_hint}
                                </span>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-gray-400 text-sm italic py-1">No building-level contacts found for linked buildings.</p>
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
              {(linkedBuildings.length > 0 || (enrichedLead.buildings && enrichedLead.buildings.length > 0)) && (
                <Suspense fallback={<div className="h-[250px] bg-gray-100 rounded-lg animate-pulse" />}>
                  <PortfolioMap
                    buildings={linkedBuildings.length > 0
                      ? linkedBuildings
                          .filter((b) => Boolean(b.address))
                          .map((b) => ({
                            address: b.address,
                            borough: b.borough || undefined,
                            latitude: b.latitude ?? null,
                            longitude: b.longitude ?? null,
                            coordinate_source: b.coordinate_source ?? null,
                            coordinate_precision: b.coordinate_precision ?? null,
                          }))
                      : enrichedLead.buildings}
                    boro={enrichedLead.boro}
                    boros={enrichedLead.boros}
                  />
                </Suspense>
              )}

              {/* Building List */}
              <div className="space-y-1 max-h-96 overflow-y-auto">
                {loadingLinkedBuildings ? (
                  <p className="text-gray-400 text-sm italic">Loading linked buildings...</p>
                ) : linkedBuildings.length > 0 ? (
                  linkedBuildings
                    .filter((b) => !buildingSearch || String(b.address || '').toLowerCase().includes(buildingSearch.toLowerCase()) || String(b.bbl || '').toLowerCase().includes(buildingSearch.toLowerCase()))
                    .map((building) => (
                      <div key={building.bbl} className="flex items-center justify-between p-2 bg-white hover:bg-gray-50 border-b border-gray-100 rounded-lg gap-2">
                        <div className="min-w-0">
                          <button
                            onClick={() => openBuildingDetail(building.bbl)}
                            className="text-sm text-blue-700 hover:underline text-left truncate"
                            title="Open building detail view"
                          >
                            {building.address || building.bbl}
                          </button>
                          <div className="text-[10px] text-gray-400">
                            BBL (Borough-Block-Lot): {building.bbl} • {building.borough || 'N/A'} • {building.unit_count ?? '--'} units
                          </div>
                        </div>
                        <div className="flex items-center gap-2 flex-shrink-0">
                          <button
                            onClick={() => handleAddBuildingToPipeline(building.bbl)}
                            disabled={pipelineAddBusy[building.bbl] || building.outreach_status === 'pipeline'}
                            className="text-[10px] px-2 py-1 rounded border border-blue-200 text-blue-700 hover:bg-blue-50 disabled:opacity-50"
                            title="Add this building to pipeline list"
                          >
                            {building.outreach_status === 'pipeline'
                              ? 'In Pipeline'
                              : (pipelineAddBusy[building.bbl] ? 'Adding...' : 'Add to Pipeline')}
                          </button>
                          <a href={`https://www.google.com/maps/search/${encodeURIComponent((building.address || building.bbl) + ', New York, NY')}`} target="_blank" rel="noopener"
                            className="text-[10px] text-blue-600 hover:underline">Map</a>
                        </div>
                      </div>
                    ))
                ) : (
                  (enrichedLead.buildings || [])
                    .filter((b: string) => !buildingSearch || String(b || '').toLowerCase().includes(buildingSearch.toLowerCase()))
                    .map((building: string, i: number) => (
                      <div key={i} className="flex items-center justify-between p-2 bg-white hover:bg-gray-50 border-b border-gray-100 rounded-lg">
                        <span className="text-sm text-gray-700">{building}</span>
                        <a href={`https://www.google.com/maps/search/${encodeURIComponent(building + ', New York, NY')}`} target="_blank" rel="noopener"
                          className="text-[10px] text-blue-600 hover:underline">Map</a>
                      </div>
                    ))
                )}
                {!loadingLinkedBuildings && linkedBuildings.length === 0 && (!enrichedLead.buildings || enrichedLead.buildings.length === 0) && (
                  <p className="text-gray-400 text-sm italic">No buildings data available</p>
                )}
              </div>
            </>
          )}

          {/* TAB: DUE DILIGENCE */}
          {activeTab === 'dd' && (
            <div className="space-y-4">
              <div className="bg-indigo-50 border border-indigo-200 rounded-xl p-4">
                <h3 className="text-sm font-bold text-indigo-800">Due Diligence Snapshot</h3>
                <p className="text-xs text-indigo-700 mt-1">
                  Auto-generated from current lead, building, enrichment, and outreach data.
                </p>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div className="bg-white border border-gray-200 rounded-xl p-4">
                  <h4 className="text-xs font-bold text-gray-700 uppercase tracking-wider mb-2">Portfolio & Revenue</h4>
                  <div className="space-y-1.5 text-sm text-gray-700">
                    <div>Buildings: <span className="font-medium">{enrichedLead.portfolio_size || 0}</span></div>
                    <div>Units: <span className="font-medium">{(enrichedLead.total_units || 0).toLocaleString()}</span></div>
                    <div>Avg units/building: <span className="font-medium">{(enrichedLead.portfolio_size || 0) > 0 ? ((enrichedLead.total_units || 0) / enrichedLead.portfolio_size).toFixed(1) : '--'}</span></div>
                    <div>Estimated annual fee: <span className="font-medium">{enrichedLead.estimated_annual_revenue ? formatCurrency(enrichedLead.estimated_annual_revenue) : '--'}</span></div>
                  </div>
                </div>

                <div className="bg-white border border-gray-200 rounded-xl p-4">
                  <h4 className="text-xs font-bold text-gray-700 uppercase tracking-wider mb-2">Data Confidence</h4>
                  <div className="space-y-1.5 text-sm text-gray-700">
                    <div>Lead score: <span className="font-medium">{(enrichedLead.score || 0).toFixed(1)}</span></div>
                    <div>Enrichment status: <span className="font-medium capitalize">{enrichedLead.enrichment_status || 'none'}</span></div>
                    <div>Contact coverage: <span className="font-medium">{enrichedLead.phone || enrichedLead.email ? 'Direct contact found' : 'No direct contact found'}</span></div>
                    <div>Pipeline stage: <span className="font-medium capitalize">{(enrichedLead.pipeline_stage || 'research').replace(/_/g, ' ')}</span></div>
                  </div>
                </div>
              </div>

              <div className="w-full bg-gray-50 border border-gray-200 rounded-xl p-4 text-left">
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

              <div className="bg-white border border-gray-200 rounded-xl p-4">
                <h4 className="text-xs font-bold text-gray-700 uppercase tracking-wider mb-2">Recommended Next Actions</h4>
                <ul className="text-sm text-gray-700 space-y-1">
                  <li>Prioritize decision-maker outreach for top-contact buildings in this lead.</li>
                  <li>Validate one high-signal building (violations/permits/litigation) before first call.</li>
                  <li>Move to <span className="font-medium">first_contact</span> when script + contact owner are confirmed.</li>
                </ul>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default LeadDetail;
