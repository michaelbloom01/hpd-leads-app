"""
Lead enrichment orchestrator.

See docs/04-enrichment-strategy.md for details.
"""
import logging
from datetime import datetime
from typing import List, Optional

from src.transform.aggregate import Lead
from src.enrich.web_crawl import WebCrawler, WebEnrichmentResult
from src.enrich.ny_dos import NYDOSClient, DOSEntity

logger = logging.getLogger(__name__)


class Enricher:
    """Orchestrates multi-tier enrichment for leads."""
    
    def __init__(self, use_cache: bool = True, ny_dos_token: Optional[str] = None):
        """
        Initialize enrichment clients.
        
        Args:
            use_cache: Whether to use caching for enrichment results
            ny_dos_token: Optional app token for NY DOS API
        """
        self.web_crawler = WebCrawler(use_cache=use_cache)
        self.ny_dos = NYDOSClient(app_token=ny_dos_token)
        # TODO: Initialize paid API clients (Apollo, etc.)
    
    def enrich_lead(self, lead: Lead, tiers: Optional[List[str]] = None) -> Lead:
        """
        Run enrichment using the 4-tier cascade with address-first strategy.
        
        Cascade:
        1. Google Places (address-based, then name-based)
        2. NY DOS Registry (corporation info)
        3. Web Crawl (website scraping)
        4. Hunter.io / Person-based email
        
        Falls back through tiers for best coverage.
        """
        logger.info(f"Enriching lead: {lead.agent_name or lead.owner_name}")
        
        # Use the new MultiSourceEnricher for the full cascade
        from src.enrich.contact_sources import MultiSourceEnricher
        
        enricher = MultiSourceEnricher()
        company_name = lead.agent_name or lead.owner_name
        location = f"{lead.boro}, New York" if lead.boro else "New York, NY"
        
        result = enricher.enrich(
            lead_id=lead.lead_id,
            company_name=company_name,
            website=lead.website,
            location=location,
            address=lead.address,
            contacts=lead.contacts,
        )
        
        # Apply results to lead
        if result.phones:
            best = result.best_phone()
            if best and not lead.phone:
                lead.phone = best.value
        
        if result.emails:
            best = result.best_email()
            if best and not lead.email:
                lead.email = best.value
        
        if result.website and not lead.website:
            lead.website = result.website
            lead.website_source = result.website_source
        
        # Apply NY DOS results
        if result.dos_id:
            lead.dos_id = result.dos_id
            lead.dos_status = result.dos_entity_type
            if result.dos_registered_agent and not lead.owner_principal:
                lead.owner_principal = result.dos_registered_agent
            if not lead.business_summary:
                parts = []
                if result.dos_entity_type:
                    parts.append(f"Entity: {result.dos_entity_type}")
                if result.dos_formation_date:
                    parts.append(f"Formed: {result.dos_formation_date[:10]}")
                if result.dos_registered_agent:
                    parts.append(f"Agent: {result.dos_registered_agent}")
                if parts:
                    lead.business_summary = " | ".join(parts)
        
        # Also run legacy web crawl if the multi-source didn't find enough
        if not lead.phone and not lead.email and not lead.website:
            name = lead.agent_name or lead.owner_name
            if name:
                web_result = self.web_crawler.enrich(name)
                lead = self._apply_web_result(lead, web_result)
                if web_result.success:
                    result.sources_succeeded.append("web_crawl_fallback")
        
        # Update enrichment metadata
        lead.enrichment_sources = result.sources_succeeded
        lead.last_enriched = datetime.now()
        
        # Determine enrichment status
        has_contact = bool(lead.phone or lead.email)
        has_website = bool(lead.website)
        if has_contact and has_website:
            lead.enrichment_status = "complete"
        elif has_contact or has_website:
            lead.enrichment_status = "partial"
        elif result.sources_succeeded:
            lead.enrichment_status = "partial"
        else:
            lead.enrichment_status = "failed"
        
        # Update tags
        lead = self._update_tags(lead)
        
        return lead
    
    def _apply_web_result(self, lead: Lead, result: WebEnrichmentResult) -> Lead:
        """Apply web crawl results to lead."""
        if not result.success:
            logger.debug(f"Web enrichment failed: {result.error}")
            return lead
        
        # Only update fields that are currently empty
        if result.website and not lead.website:
            lead.website = result.website
        
        if result.phone and not lead.phone:
            lead.phone = result.phone
        
        if result.email and not lead.email:
            lead.email = result.email
        
        if result.business_summary and not lead.business_summary:
            lead.business_summary = result.business_summary
        
        if result.owner_principal and not lead.owner_principal:
            lead.owner_principal = result.owner_principal
        
        return lead
    
    def _is_corporation(self, name: str) -> bool:
        """Check if a name looks like a corporation/LLC."""
        corp_indicators = ['LLC', 'L.L.C.', 'INC', 'CORP', 'LP', 'L.P.', 'LLP', 'COMPANY', 'CO.', 'PARTNERS']
        name_upper = name.upper()
        return any(indicator in name_upper for indicator in corp_indicators)
    
    def _apply_dos_result(self, lead: Lead, dos: DOSEntity) -> Lead:
        """Apply NY DOS lookup results to lead."""
        logger.info(f"Found DOS record: {dos.name} ({dos.entity_type}, {dos.status})")
        
        # Add registered agent info
        if dos.process_name and not lead.owner_principal:
            lead.owner_principal = dos.process_name
        
        # Store DOS metadata in business_summary if empty
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
        
        # Add tag
        if "ny_dos_verified" not in lead.tags:
            lead.tags.append("ny_dos_verified")
        
        return lead
    
    def _update_tags(self, lead: Lead) -> Lead:
        """Update tags based on current lead data."""
        tags = set(lead.tags)
        
        # Contact info tags
        if lead.website:
            tags.add("has_website")
        if lead.email:
            tags.add("has_email")
        if lead.phone:
            tags.add("has_phone")
        
        lead.tags = list(tags)
        return lead
    
    def enrich_batch(
        self, 
        leads: List[Lead], 
        limit: int = 50,
        skip_enriched: bool = True,
        min_score: Optional[float] = None,
        min_portfolio: Optional[int] = None,
    ) -> List[Lead]:
        """
        Enrich a batch of leads (respects rate limits).
        
        Args:
            leads: Leads to enrich
            limit: Maximum leads to process
            skip_enriched: Skip leads that already have enrichment
            min_score: Only enrich leads with score >= this value
            min_portfolio: Only enrich leads with portfolio >= this size
            
        Returns:
            List of enriched leads
        """
        # Filter leads based on criteria
        candidates = []
        for lead in leads:
            # Skip already enriched
            if skip_enriched and lead.enrichment_status in ["complete", "partial"]:
                continue
            
            # Skip leads that have been retried too many times (Phase 2.7)
            retries = getattr(lead, 'enrichment_retries', 0) or 0
            if retries >= 3 and lead.enrichment_status == "failed":
                continue
            
            # Filter by score
            if min_score is not None and lead.score < min_score:
                continue
            
            # Filter by portfolio size
            if min_portfolio is not None and lead.portfolio_size < min_portfolio:
                continue
            
            candidates.append(lead)
        
        # Sort by score descending (prioritize high-value leads)
        candidates.sort(key=lambda lead: lead.score, reverse=True)
        
        # Limit batch size
        batch = candidates[:limit]
        
        logger.info(f"Enriching batch of {len(batch)} leads (from {len(leads)} total)")
        
        enriched = []
        success_count = 0
        
        for i, lead in enumerate(batch, 1):
            try:
                logger.info(f"[{i}/{len(batch)}] Enriching: {lead.agent_name or lead.owner_name}")
                enriched_lead = self.enrich_lead(lead)
                enriched.append(enriched_lead)
                
                if enriched_lead.enrichment_status in ["complete", "partial"]:
                    success_count += 1
                    
            except Exception as e:
                logger.warning(f"Failed to enrich {lead.agent_name}: {e}")
                lead.enrichment_status = "failed"
                # Increment retry counter (Phase 2.7)
                try:
                    from src.storage.database import get_database
                    db = get_database()
                    db.increment_enrichment_retries(lead.lead_id)
                except Exception:
                    pass
                enriched.append(lead)
        
        logger.info(f"Enrichment complete: {success_count}/{len(batch)} successful")
        
        return enriched


# Quick test
if __name__ == "__main__":
    from src.transform.aggregate import Lead
    
    logging.basicConfig(level=logging.INFO)
    
    # Create a test lead
    test_lead = Lead(
        lead_id="test123",
        agent_name="GRINBERG MANAGEMENT & DEVELOPMENT LLC",
        owner_name="",
        owner_type="Agent",
        portfolio_size=125,
        total_units=0,
        buildings=["100 Main St"],
        building_ids=["1"],
        score=59.5,
    )
    
    print(f"Testing enrichment for: {test_lead.agent_name}")
    print("=" * 60)
    
    enricher = Enricher(use_cache=True)
    enriched = enricher.enrich_lead(test_lead)
    
    print("\nResults:")
    print(f"  Website: {enriched.website}")
    print(f"  Phone: {enriched.phone}")
    print(f"  Email: {enriched.email}")
    print(f"  Summary: {enriched.business_summary[:100] if enriched.business_summary else None}...")
    print(f"  Owner: {enriched.owner_principal}")
    print(f"  Status: {enriched.enrichment_status}")
    print(f"  Sources: {enriched.enrichment_sources}")
    print(f"  Tags: {enriched.tags}")
