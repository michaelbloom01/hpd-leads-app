"""
High-performance batch enrichment for large lead datasets.

Optimized for processing 100k+ leads efficiently:
1. Parallel NY DOS lookups (fast, no rate limits)
2. Batched web crawling with concurrent workers
3. Incremental progress saving
4. Resume capability
"""
import logging
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Callable

from src.transform.aggregate import Lead
from src.enrich.web_crawl import WebCrawler, WebEnrichmentResult
from src.enrich.ny_dos import NYDOSClient, DOSEntity

logger = logging.getLogger(__name__)


@dataclass
class EnrichmentProgress:
    """Track enrichment progress for UI updates."""
    running: bool = False
    phase: str = ""  # "dos_lookup", "web_crawl", "complete"
    total_leads: int = 0
    processed: int = 0
    dos_completed: int = 0
    dos_found: int = 0
    web_completed: int = 0
    web_found: int = 0
    failed: int = 0
    current_batch: int = 0
    total_batches: int = 0
    current_lead: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return {
            "running": self.running,
            "phase": self.phase,
            "total_leads": self.total_leads,
            "processed": self.processed,
            "dos_completed": self.dos_completed,
            "dos_found": self.dos_found,
            "web_completed": self.web_completed,
            "web_found": self.web_found,
            "failed": self.failed,
            "current_batch": self.current_batch,
            "total_batches": self.total_batches,
            "current_lead": self.current_lead,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "percent_complete": round(self.processed / self.total_leads * 100, 1) if self.total_leads > 0 else 0,
        }


@dataclass
class EnrichmentConfig:
    """Configuration for batch enrichment."""
    # NY DOS settings
    dos_enabled: bool = True
    dos_batch_size: int = 100  # How many to look up at once
    dos_workers: int = 5  # Concurrent DOS API calls
    
    # Web crawl settings
    web_enabled: bool = True
    web_batch_size: int = 50  # How many to process before saving
    web_workers: int = 3  # Concurrent web crawlers (keep low to avoid blocks)
    web_delay: float = 2.0  # Delay between web requests
    
    # Filtering
    min_portfolio_for_web: int = 5  # Only web crawl leads with X+ buildings
    min_score_for_web: float = 0.0  # Only web crawl leads with score >= X
    skip_already_enriched: bool = True  # Skip leads with existing enrichment
    
    # Persistence
    save_interval: int = 50  # Save to DB every N leads
    
    # Limits
    max_leads: Optional[int] = None  # Max leads to process (None = all)
    max_web_crawl: Optional[int] = 500  # Max leads to web crawl (rate limited)


class BatchEnricher:
    """
    High-performance batch enrichment with parallel processing.
    
    Design:
    1. First pass: NY DOS lookups (parallel, fast)
       - All leads that look like corporations
       - Updates: owner_principal, business_summary, tags
    
    2. Second pass: Web crawling (parallel but rate-limited)
       - High-priority leads only (score + portfolio filters)
       - Updates: website, phone, email, business_summary
    
    3. Progress is saved incrementally to survive interruption
    """
    
    def __init__(self, config: Optional[EnrichmentConfig] = None):
        self.config = config or EnrichmentConfig()
        self.progress = EnrichmentProgress()
        self._lock = threading.Lock()
        self._stop_requested = False
        
        # Initialize clients
        self.dos_client = NYDOSClient()
        self.web_crawler = WebCrawler(use_cache=True)
    
    def stop(self):
        """Request graceful stop of enrichment."""
        self._stop_requested = True
        logger.info("Stop requested for batch enrichment")
    
    def enrich_all(
        self,
        leads: List[Lead],
        on_progress: Optional[Callable[[EnrichmentProgress], None]] = None,
        on_batch_complete: Optional[Callable[[List[Lead]], None]] = None,
    ) -> List[Lead]:
        """
        Enrich all leads using optimized batch processing.
        
        Args:
            leads: List of leads to enrich
            on_progress: Callback for progress updates
            on_batch_complete: Callback when a batch is done (for persistence)
            
        Returns:
            List of enriched leads
        """
        self._stop_requested = False
        
        with self._lock:
            self.progress = EnrichmentProgress(
                running=True,
                phase="initializing",
                total_leads=len(leads),
                started_at=datetime.now().isoformat(),
            )
        
        try:
            # Apply limits
            if self.config.max_leads:
                leads = leads[:self.config.max_leads]
                with self._lock:
                    self.progress.total_leads = len(leads)
            
            logger.info(f"Starting batch enrichment for {len(leads)} leads")
            
            # Phase 1: NY DOS lookups (fast)
            if self.config.dos_enabled:
                leads = self._run_dos_enrichment(leads, on_progress, on_batch_complete)
            
            if self._stop_requested:
                logger.info("Enrichment stopped after DOS phase")
                return leads
            
            # Phase 2: Web crawling (slow, rate-limited)
            if self.config.web_enabled:
                leads = self._run_web_enrichment(leads, on_progress, on_batch_complete)
            
            with self._lock:
                self.progress.phase = "complete"
                self.progress.running = False
                self.progress.finished_at = datetime.now().isoformat()
            
            if on_progress:
                on_progress(self.progress)
            
            logger.info(f"Batch enrichment complete: DOS found {self.progress.dos_found}, Web found {self.progress.web_found}")
            
            return leads
            
        except Exception as e:
            logger.error(f"Batch enrichment failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            
            with self._lock:
                self.progress.error = str(e)
                self.progress.running = False
                self.progress.finished_at = datetime.now().isoformat()
            
            return leads
    
    def _run_dos_enrichment(
        self,
        leads: List[Lead],
        on_progress: Optional[Callable],
        on_batch_complete: Optional[Callable],
    ) -> List[Lead]:
        """Run NY DOS lookups in parallel batches."""
        with self._lock:
            self.progress.phase = "dos_lookup"
        
        # Filter to corporation-like names only
        corp_indicators = ['LLC', 'L.L.C.', 'INC', 'CORP', 'LP', 'L.P.', 'LLP', 'COMPANY', 'CO.', 'PARTNERS']
        
        candidates = []
        for lead in leads:
            if self.config.skip_already_enriched and "ny_dos_verified" in lead.tags:
                continue
            
            name = lead.owner_name or lead.agent_name
            if name and any(ind in name.upper() for ind in corp_indicators):
                candidates.append(lead)
        
        if not candidates:
            logger.info("No candidates for DOS lookup")
            return leads
        
        logger.info(f"Running DOS lookup for {len(candidates)} corporation leads")
        
        lead_index = {l.lead_id: l for l in leads}
        batch_size = self.config.dos_batch_size
        total_batches = (len(candidates) + batch_size - 1) // batch_size
        
        with self._lock:
            self.progress.total_batches = total_batches
        
        with ThreadPoolExecutor(max_workers=self.config.dos_workers) as executor:
            for batch_num in range(total_batches):
                if self._stop_requested:
                    break
                
                start_idx = batch_num * batch_size
                batch = candidates[start_idx:start_idx + batch_size]
                
                with self._lock:
                    self.progress.current_batch = batch_num + 1
                
                # Submit all lookups for this batch
                futures = {}
                for lead in batch:
                    name = lead.owner_name or lead.agent_name
                    future = executor.submit(self._dos_lookup_single, name)
                    futures[future] = lead
                
                # Collect results
                enriched_batch = []
                for future in as_completed(futures):
                    if self._stop_requested:
                        break
                    
                    lead = futures[future]
                    try:
                        dos_result = future.result()
                        if dos_result:
                            lead = self._apply_dos_to_lead(lead, dos_result)
                            with self._lock:
                                self.progress.dos_found += 1
                        
                        with self._lock:
                            self.progress.dos_completed += 1
                            self.progress.processed += 1
                            self.progress.current_lead = lead.agent_name or lead.owner_name
                        
                        lead_index[lead.lead_id] = lead
                        enriched_batch.append(lead)
                        
                    except Exception as e:
                        logger.warning(f"DOS lookup failed for {lead.agent_name}: {e}")
                        with self._lock:
                            self.progress.failed += 1
                
                # Save batch
                if on_batch_complete and enriched_batch:
                    on_batch_complete(enriched_batch)
                
                if on_progress:
                    on_progress(self.progress)
        
        return list(lead_index.values())
    
    def _dos_lookup_single(self, name: str) -> Optional[DOSEntity]:
        """Single DOS lookup (for thread pool)."""
        try:
            return self.dos_client.lookup_entity(name)
        except Exception as e:
            logger.debug(f"DOS lookup error for {name}: {e}")
            return None
    
    def _apply_dos_to_lead(self, lead: Lead, dos: DOSEntity) -> Lead:
        """Apply DOS data to lead."""
        if dos.process_name and not lead.owner_principal:
            lead.owner_principal = dos.process_name
        
        # Build summary
        if not lead.business_summary:
            parts = []
            if dos.entity_type:
                parts.append(f"Entity: {dos.entity_type}")
            if dos.status:
                parts.append(f"Status: {dos.status}")
            if dos.formation_date:
                parts.append(f"Formed: {dos.formation_date[:10]}")
            if dos.county:
                parts.append(f"County: {dos.county}")
            if dos.process_name:
                parts.append(f"Registered Agent: {dos.process_name}")
            if parts:
                lead.business_summary = " | ".join(parts)
        
        # Update metadata
        if "ny_dos_verified" not in lead.tags:
            lead.tags.append("ny_dos_verified")
        
        lead.dos_id = dos.dos_id
        lead.dos_status = dos.status
        
        if "ny_dos" not in lead.enrichment_sources:
            lead.enrichment_sources.append("ny_dos")
        
        lead.last_enriched = datetime.now()
        
        # Update enrichment status
        has_contact = bool(lead.phone or lead.email)
        has_website = bool(lead.website)
        if has_contact or has_website:
            lead.enrichment_status = "partial"
        elif lead.enrichment_status == "none":
            lead.enrichment_status = "partial"  # DOS data counts as partial
        
        return lead
    
    def _run_web_enrichment(
        self,
        leads: List[Lead],
        on_progress: Optional[Callable],
        on_batch_complete: Optional[Callable],
    ) -> List[Lead]:
        """Run web crawling for high-priority leads."""
        with self._lock:
            self.progress.phase = "web_crawl"
        
        # Filter candidates for web crawling
        candidates = []
        for lead in leads:
            # Skip already complete
            if self.config.skip_already_enriched and lead.enrichment_status == "complete":
                continue
            
            # Skip if already has website + contact
            if lead.website and (lead.phone or lead.email):
                continue
            
            # Apply portfolio filter
            if lead.portfolio_size < self.config.min_portfolio_for_web:
                continue
            
            # Apply score filter
            if lead.score < self.config.min_score_for_web:
                continue
            
            candidates.append(lead)
        
        # Sort by priority (score * portfolio_size)
        candidates.sort(key=lambda l: l.score * (1 + l.portfolio_size / 100), reverse=True)
        
        # Apply limit
        if self.config.max_web_crawl:
            candidates = candidates[:self.config.max_web_crawl]
        
        if not candidates:
            logger.info("No candidates for web crawling")
            return leads
        
        logger.info(f"Running web crawl for {len(candidates)} high-priority leads")
        
        lead_index = {l.lead_id: l for l in leads}
        batch_size = self.config.web_batch_size
        total_batches = (len(candidates) + batch_size - 1) // batch_size
        
        with self._lock:
            self.progress.total_batches = total_batches
        
        # Web crawling is rate-limited, so we use fewer workers and add delays
        with ThreadPoolExecutor(max_workers=self.config.web_workers) as executor:
            for batch_num in range(total_batches):
                if self._stop_requested:
                    break
                
                start_idx = batch_num * batch_size
                batch = candidates[start_idx:start_idx + batch_size]
                
                with self._lock:
                    self.progress.current_batch = batch_num + 1
                
                # Submit lookups with staggered start
                futures = {}
                for i, lead in enumerate(batch):
                    # Stagger submissions to avoid burst
                    if i > 0:
                        time.sleep(0.5)
                    
                    name = lead.agent_name or lead.owner_name
                    future = executor.submit(self._web_crawl_single, name)
                    futures[future] = lead
                
                # Collect results
                enriched_batch = []
                for future in as_completed(futures):
                    if self._stop_requested:
                        break
                    
                    lead = futures[future]
                    try:
                        web_result = future.result()
                        if web_result and web_result.success:
                            lead = self._apply_web_to_lead(lead, web_result)
                            with self._lock:
                                self.progress.web_found += 1
                        
                        with self._lock:
                            self.progress.web_completed += 1
                            self.progress.processed += 1
                            self.progress.current_lead = lead.agent_name or lead.owner_name
                        
                        lead_index[lead.lead_id] = lead
                        enriched_batch.append(lead)
                        
                    except Exception as e:
                        logger.warning(f"Web crawl failed for {lead.agent_name}: {e}")
                        with self._lock:
                            self.progress.failed += 1
                
                # Save batch
                if on_batch_complete and enriched_batch:
                    on_batch_complete(enriched_batch)
                
                if on_progress:
                    on_progress(self.progress)
                
                # Delay between batches to avoid rate limits
                if batch_num < total_batches - 1:
                    time.sleep(self.config.web_delay)
        
        return list(lead_index.values())
    
    def _web_crawl_single(self, name: str) -> Optional[WebEnrichmentResult]:
        """Single web crawl (for thread pool)."""
        try:
            return self.web_crawler.enrich(name)
        except Exception as e:
            logger.debug(f"Web crawl error for {name}: {e}")
            return None
    
    def _apply_web_to_lead(self, lead: Lead, result: WebEnrichmentResult) -> Lead:
        """Apply web crawl data to lead."""
        if result.website and not lead.website:
            lead.website = result.website
            if "has_website" not in lead.tags:
                lead.tags.append("has_website")
        
        if result.phone and not lead.phone:
            lead.phone = result.phone
            if "has_phone" not in lead.tags:
                lead.tags.append("has_phone")
        
        if result.email and not lead.email:
            lead.email = result.email
            if "has_email" not in lead.tags:
                lead.tags.append("has_email")
        
        if result.business_summary and not lead.business_summary:
            lead.business_summary = result.business_summary
        
        if result.owner_principal and not lead.owner_principal:
            lead.owner_principal = result.owner_principal
        
        # Update enrichment sources
        if "web_crawl" not in lead.enrichment_sources:
            lead.enrichment_sources.append("web_crawl")
        
        lead.last_enriched = datetime.now()
        
        # Update enrichment status
        has_contact = bool(lead.phone or lead.email)
        has_website = bool(lead.website)
        
        if has_contact and has_website:
            lead.enrichment_status = "complete"
        elif has_contact or has_website:
            lead.enrichment_status = "partial"
        
        return lead
    
    def get_progress(self) -> EnrichmentProgress:
        """Get current progress (thread-safe)."""
        with self._lock:
            return self.progress


# Convenience function for API
def run_batch_enrichment(
    leads: List[Lead],
    config: Optional[EnrichmentConfig] = None,
    on_progress: Optional[Callable[[EnrichmentProgress], None]] = None,
    on_batch_complete: Optional[Callable[[List[Lead]], None]] = None,
) -> List[Lead]:
    """
    Run batch enrichment with optional callbacks.
    
    Args:
        leads: Leads to enrich
        config: Optional configuration overrides
        on_progress: Called with progress updates
        on_batch_complete: Called when a batch is saved (for persistence)
        
    Returns:
        Enriched leads
    """
    enricher = BatchEnricher(config)
    return enricher.enrich_all(leads, on_progress, on_batch_complete)
