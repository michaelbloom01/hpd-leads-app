"""
Multi-source contact enrichment with source attribution.

Sources (in priority order):
1. Google Places API - Business phone numbers (free tier: $200/month credit, no charge under that)
2. NY DOS Registry - Corporation info, registered agent (completely free, public data)
3. Web Crawl - Scrape company websites (completely free)
4. Hunter.io - Email finder (optional, free tier: 25 credits/month)

Each contact found includes:
- The actual data (phone/email)
- Source name (e.g., "google_places", "ny_dos", "web_crawl")
- Source URL (clickable link to verify)
- Confidence score (0-100)
- Timestamp
"""
import logging
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, List, Dict
from urllib.parse import urlparse

import requests

from config.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class ContactInfo:
    """A single piece of contact information with source attribution."""
    value: str  # The phone number or email
    type: str  # "phone" or "email"
    source: str  # "google_places", "hunter", "web_crawl", "apollo"
    source_url: Optional[str] = None  # URL to verify (e.g., Google Maps link)
    confidence: int = 50  # 0-100
    verified: bool = False
    found_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass 
class EnrichmentResult:
    """Result from multi-source enrichment."""
    lead_id: str
    company_name: str
    phones: List[ContactInfo] = field(default_factory=list)
    emails: List[ContactInfo] = field(default_factory=list)
    website: Optional[str] = None
    website_source: Optional[str] = None
    google_place_id: Optional[str] = None
    # NY DOS data
    dos_id: Optional[str] = None
    dos_entity_name: Optional[str] = None
    dos_entity_type: Optional[str] = None
    dos_formation_date: Optional[str] = None
    dos_registered_agent: Optional[str] = None
    dos_registered_address: Optional[str] = None
    dos_lookup_url: Optional[str] = None
    # Tracking
    sources_tried: List[str] = field(default_factory=list)
    sources_succeeded: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    enriched_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def best_phone(self) -> Optional[ContactInfo]:
        """Get highest confidence phone."""
        if not self.phones:
            return None
        return max(self.phones, key=lambda p: p.confidence)
    
    def best_email(self) -> Optional[ContactInfo]:
        """Get highest confidence email."""
        if not self.emails:
            return None
        return max(self.emails, key=lambda e: e.confidence)
    
    def to_dict(self) -> Dict:
        return {
            "lead_id": self.lead_id,
            "company_name": self.company_name,
            "phones": [p.to_dict() for p in self.phones],
            "emails": [e.to_dict() for e in self.emails],
            "website": self.website,
            "website_source": self.website_source,
            "google_place_id": self.google_place_id,
            "dos_id": self.dos_id,
            "dos_entity_name": self.dos_entity_name,
            "dos_entity_type": self.dos_entity_type,
            "dos_formation_date": self.dos_formation_date,
            "dos_registered_agent": self.dos_registered_agent,
            "dos_registered_address": self.dos_registered_address,
            "dos_lookup_url": self.dos_lookup_url,
            "sources_tried": self.sources_tried,
            "sources_succeeded": self.sources_succeeded,
            "errors": self.errors,
            "enriched_at": self.enriched_at,
        }


class GooglePlacesEnricher:
    """
    Google Places API for business phone numbers.
    
    Free tier: $200/month credit (~$17/1000 requests for Place Details)
    That's roughly 11,700 free lookups per month.
    """
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("GOOGLE_PLACES_API_KEY")
        self.base_url = "https://maps.googleapis.com/maps/api/place"
        
    def is_configured(self) -> bool:
        return bool(self.api_key)
    
    def find_business(self, company_name: str, location: str = "New York, NY",
                      address: Optional[str] = None) -> Optional[Dict]:
        """
        Search for a business and get its phone number.
        
        Strategy:
        1. If address provided, search by address first (most specific)
        2. Fall back to company name + location search
        
        Args:
            company_name: Business name to search
            location: Location context (default NYC)
            address: Specific business address (improves accuracy significantly)
            
        Returns:
            Dict with phone, place_id, maps_url, or None
        """
        if not self.api_key:
            logger.debug("Google Places API key not configured")
            return None
        
        # Try address-based search first (highest confidence)
        if address:
            result = self._search_and_get_details(f"{company_name} {address}")
            if result and result.get("phone"):
                return result
            # Try just the address if company name search didn't work
            result = self._search_and_get_details(f"property management near {address}")
            if result and result.get("phone"):
                return result
        
        # Fall back to name-based search
        search_query = f"{company_name} property management {location}"
        return self._search_and_get_details(search_query)
    
    def _search_and_get_details(self, query: str) -> Optional[Dict]:
        """Execute a Places text search + details lookup."""
        try:
            search_url = f"{self.base_url}/textsearch/json"
            response = requests.get(search_url, params={
                "query": query, "key": self.api_key,
            }, timeout=10)
            
            if response.status_code != 200:
                logger.warning(f"Google Places search failed: {response.status_code}")
                return None
            
            data = response.json()
            if data.get("status") != "OK" or not data.get("results"):
                logger.debug(f"No Google Places results for: {query}")
                return None
            
            place = data["results"][0]
            place_id = place.get("place_id")
            if not place_id:
                return None
            
            details_url = f"{self.base_url}/details/json"
            details_response = requests.get(details_url, params={
                "place_id": place_id,
                "fields": "formatted_phone_number,international_phone_number,website,url,name",
                "key": self.api_key,
            }, timeout=10)
            
            if details_response.status_code != 200:
                return None
            
            details_data = details_response.json()
            if details_data.get("status") != "OK":
                return None
            
            result = details_data.get("result", {})
            return {
                "phone": result.get("formatted_phone_number"),
                "phone_international": result.get("international_phone_number"),
                "website": result.get("website"),
                "place_id": place_id,
                "maps_url": result.get("url"),
                "name": result.get("name"),
            }
        except Exception as e:
            logger.warning(f"Google Places error for query '{query[:60]}': {e}")
            return None


class HunterEnricher:
    """
    Hunter.io API for email finding.
    
    Free tier: 50 credits/month
    - Domain Search: 1 credit per email revealed
    - Email Finder: 1 credit per email found
    """
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("HUNTER_API_KEY")
        self.base_url = "https://api.hunter.io/v2"
        
    def is_configured(self) -> bool:
        return bool(self.api_key)
    
    def domain_search(self, domain: str, limit: int = 5) -> List[Dict]:
        """
        Find emails associated with a domain.
        
        Args:
            domain: Website domain (e.g., "acme-pm.com")
            limit: Max emails to return
            
        Returns:
            List of email dicts with email, confidence, sources
        """
        if not self.api_key:
            logger.debug("Hunter API key not configured")
            return []
        
        try:
            response = requests.get(f"{self.base_url}/domain-search", params={
                "domain": domain,
                "limit": limit,
                "api_key": self.api_key,
            }, timeout=10)
            
            if response.status_code != 200:
                logger.warning(f"Hunter domain search failed: {response.status_code}")
                return []
            
            data = response.json()
            emails = data.get("data", {}).get("emails", [])
            
            results = []
            for email_data in emails:
                results.append({
                    "email": email_data.get("value"),
                    "confidence": email_data.get("confidence", 0),
                    "first_name": email_data.get("first_name"),
                    "last_name": email_data.get("last_name"),
                    "position": email_data.get("position"),
                    "sources": email_data.get("sources", []),
                    "verification": email_data.get("verification", {}),
                })
            
            return results
            
        except Exception as e:
            logger.warning(f"Hunter error for {domain}: {e}")
            return []
    
    def email_finder(self, domain: str, first_name: str, last_name: str) -> Optional[Dict]:
        """
        Find a specific person's email at a company.
        
        Args:
            domain: Company domain
            first_name: Person's first name
            last_name: Person's last name
            
        Returns:
            Dict with email and confidence, or None
        """
        if not self.api_key:
            return None
        
        try:
            response = requests.get(f"{self.base_url}/email-finder", params={
                "domain": domain,
                "first_name": first_name,
                "last_name": last_name,
                "api_key": self.api_key,
            }, timeout=10)
            
            if response.status_code != 200:
                return None
            
            data = response.json()
            email_data = data.get("data", {})
            
            if email_data.get("email"):
                return {
                    "email": email_data.get("email"),
                    "confidence": email_data.get("score", 0),
                    "sources": email_data.get("sources", []),
                }
            
            return None
            
        except Exception as e:
            logger.warning(f"Hunter email finder error: {e}")
            return None


class MultiSourceEnricher:
    """
    Orchestrates multiple enrichment sources with priority and deduplication.
    """
    
    def __init__(self):
        self.google_places = GooglePlacesEnricher()
        self.hunter = HunterEnricher()
        
        # Import web crawler and NY DOS client
        from .web_crawl import WebCrawler
        from .ny_dos import NYDOSClient
        self.web_crawler = WebCrawler(use_cache=True)
        self.ny_dos = NYDOSClient()
        
        # Track API usage
        self._api_calls = {
            "google_places": 0,
            "ny_dos": 0,
            "hunter": 0,
            "web_crawl": 0,
        }
    
    def get_api_status(self) -> Dict:
        """Get status of configured APIs."""
        return {
            "google_places": {
                "configured": self.google_places.is_configured(),
                "calls": self._api_calls["google_places"],
                "description": "Business phone numbers (free $200/month credit)",
            },
            "ny_dos": {
                "configured": True,  # Always available - public data
                "calls": self._api_calls["ny_dos"],
                "description": "NY corporation registry (free, public data)",
            },
            "hunter": {
                "configured": self.hunter.is_configured(),
                "calls": self._api_calls["hunter"],
                "description": "Email finder (25 free/month)",
            },
            "web_crawl": {
                "configured": True,  # Always available
                "calls": self._api_calls["web_crawl"],
                "description": "Website scraping (free, unlimited)",
            },
        }
    
    def enrich(self, lead_id: str, company_name: str, website: Optional[str] = None,
               location: str = "New York, NY", use_google: bool = True,
               use_ny_dos: bool = True, use_hunter: bool = True, 
               use_web: bool = True,
               address: Optional[str] = None,
               contacts: Optional[List[Dict]] = None) -> EnrichmentResult:
        """
        Enrich a lead using a 4-tier cascade with address-first strategy.
        
        Cascade:
        1. Address-Based Google Places (highest confidence - uses business address)
        2. NY DOS Registry (corporation info, registered agent)
        3. Web Crawl (website scraping, phone/email from website)
        4. Hunter.io / Person-Based Email (uses HPD contact names)
        
        Args:
            lead_id: Lead identifier
            company_name: Company name to search
            website: Known website (optional)
            location: Location context for Google search
            address: Business address (from HPD registration - high value!)
            contacts: HPD contact records [{"type": ..., "name": ..., "title": ...}]
        """
        result = EnrichmentResult(lead_id=lead_id, company_name=company_name)
        contacts = contacts or []
        
        # Track which website we find
        found_website = website
        
        # === TIER 1: Google Places with Address (highest confidence) ===
        if use_google and self.google_places.is_configured():
            result.sources_tried.append("google_places")
            self._api_calls["google_places"] += 1
            
            try:
                gp_result = self.google_places.find_business(
                    company_name, location, address=address
                )
                
                if gp_result:
                    result.sources_succeeded.append("google_places")
                    result.google_place_id = gp_result.get("place_id")
                    
                    phone = gp_result.get("phone")
                    if phone:
                        normalized = self._normalize_phone(phone)
                        if normalized:
                            result.phones.append(ContactInfo(
                                value=normalized, type="phone",
                                source="google_places",
                                source_url=gp_result.get("maps_url"),
                                confidence=90, verified=True,
                            ))
                    
                    if not found_website and gp_result.get("website"):
                        found_website = gp_result.get("website")
                        result.website = found_website
                        result.website_source = "google_places"
                        
            except Exception as e:
                result.errors.append(f"google_places: {str(e)}")
                logger.warning(f"Google Places failed for {company_name}: {e}")
        
        # === TIER 2: NY DOS Registry (always free, corporation info) ===
        if use_ny_dos:
            result.sources_tried.append("ny_dos")
            self._api_calls["ny_dos"] += 1
            
            try:
                dos_entity = self.ny_dos.lookup_entity(company_name)
                
                if dos_entity:
                    result.sources_succeeded.append("ny_dos")
                    result.dos_id = dos_entity.dos_id
                    result.dos_entity_name = dos_entity.name
                    result.dos_entity_type = dos_entity.entity_type
                    result.dos_formation_date = dos_entity.formation_date
                    result.dos_registered_agent = dos_entity.process_name
                    result.dos_registered_address = dos_entity.process_address
                    result.dos_lookup_url = f"https://appext20.dos.ny.gov/corp_public/CORPSEARCH.ENTITY_INFORMATION?p_token=&p_nameid={dos_entity.dos_id}"
                    logger.info(f"NY DOS found: {dos_entity.name} (ID: {dos_entity.dos_id})")
                    
            except Exception as e:
                result.errors.append(f"ny_dos: {str(e)}")
                logger.warning(f"NY DOS lookup failed for {company_name}: {e}")
        
        # === TIER 3: Web Crawl (free, uses address context for better search) ===
        if use_web:
            result.sources_tried.append("web_crawl")
            self._api_calls["web_crawl"] += 1
            
            try:
                # Build a richer search query using address context
                search_name = company_name
                if address and not result.phones:
                    # Add address for more targeted search if we still need a phone
                    addr_parts = address.split(',')
                    if addr_parts:
                        search_name = f"{company_name} {addr_parts[0].strip()}"
                
                web_result = self.web_crawler.enrich(search_name)
                
                if web_result.success:
                    result.sources_succeeded.append("web_crawl")
                    
                    if not found_website and web_result.website:
                        found_website = web_result.website
                        result.website = found_website
                        result.website_source = "web_crawl"
                    
                    if web_result.phone:
                        existing_phones = {p.value for p in result.phones}
                        if web_result.phone not in existing_phones:
                            result.phones.append(ContactInfo(
                                value=web_result.phone, type="phone",
                                source="web_crawl",
                                source_url=web_result.website,
                                confidence=70, verified=False,
                            ))
                    
                    if web_result.email:
                        result.emails.append(ContactInfo(
                            value=web_result.email, type="email",
                            source="web_crawl",
                            source_url=web_result.website,
                            confidence=70, verified=False,
                        ))
                        
            except Exception as e:
                result.errors.append(f"web_crawl: {str(e)}")
                logger.warning(f"Web crawl failed for {company_name}: {e}")
        
        # === TIER 4: Hunter.io + Person-Based Email Discovery ===
        if use_hunter and self.hunter.is_configured():
            # 4a. Domain search if we have a website
            if found_website:
                result.sources_tried.append("hunter_domain")
                self._api_calls["hunter"] += 1
                
                try:
                    domain = urlparse(found_website).netloc
                    if domain.startswith("www."):
                        domain = domain[4:]
                    
                    if domain:
                        hunter_results = self.hunter.domain_search(domain, limit=3)
                        if hunter_results:
                            result.sources_succeeded.append("hunter_domain")
                            for hr in hunter_results:
                                email = hr.get("email")
                                if email:
                                    existing = {e.value.lower() for e in result.emails}
                                    if email.lower() not in existing:
                                        sources = hr.get("sources", [])
                                        source_url = sources[0].get("uri") if sources else f"https://hunter.io/verify/{email}"
                                        result.emails.append(ContactInfo(
                                            value=email, type="email", source="hunter",
                                            source_url=source_url,
                                            confidence=hr.get("confidence", 50),
                                            verified=hr.get("verification", {}).get("status") == "valid",
                                        ))
                except Exception as e:
                    result.errors.append(f"hunter_domain: {str(e)}")
            
            # 4b. Person-based email finder using HPD contact names
            if found_website and contacts:
                domain = urlparse(found_website).netloc
                if domain.startswith("www."):
                    domain = domain[4:]
                
                if domain:
                    for c in contacts[:3]:  # Only try first 3 contacts to conserve credits
                        name = c.get('name', '')
                        parts = name.split()
                        if len(parts) >= 2:
                            first_name = parts[0]
                            last_name = parts[-1]
                            
                            # Skip if clearly not a person name
                            if any(ind in name.upper() for ind in ['LLC', 'INC', 'CORP', 'CO.']):
                                continue
                            
                            try:
                                result.sources_tried.append("hunter_person")
                                self._api_calls["hunter"] += 1
                                
                                person_result = self.hunter.email_finder(domain, first_name, last_name)
                                if person_result and person_result.get("email"):
                                    existing = {e.value.lower() for e in result.emails}
                                    email = person_result["email"]
                                    if email.lower() not in existing:
                                        result.sources_succeeded.append("hunter_person")
                                        result.emails.append(ContactInfo(
                                            value=email, type="email",
                                            source="hunter_person",
                                            source_url=f"https://hunter.io/verify/{email}",
                                            confidence=person_result.get("confidence", 60),
                                            verified=False,
                                        ))
                                        break  # Found one person email, good enough
                            except Exception as e:
                                result.errors.append(f"hunter_person: {str(e)}")
        
        # Sort contacts by confidence
        result.phones.sort(key=lambda p: p.confidence, reverse=True)
        result.emails.sort(key=lambda e: e.confidence, reverse=True)
        
        return result
    
    def _normalize_phone(self, phone: str) -> Optional[str]:
        """Normalize phone to (XXX) XXX-XXXX format."""
        digits = re.sub(r"\D", "", phone)
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        if len(digits) != 10:
            return None
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


# Convenience function
def enrich_lead_contacts(lead_id: str, company_name: str, 
                         website: Optional[str] = None,
                         address: Optional[str] = None,
                         contacts: Optional[List[Dict]] = None) -> EnrichmentResult:
    """
    Enrich a lead's contact information using all available sources.
    
    Args:
        lead_id: Lead identifier
        company_name: Company name
        website: Known website (optional)
        address: Business address from HPD (optional, improves Google Places accuracy)
        contacts: HPD contact records (optional, enables person-based email discovery)
        
    Returns:
        EnrichmentResult with phones, emails, and source attribution
    """
    enricher = MultiSourceEnricher()
    return enricher.enrich(lead_id, company_name, website, 
                          address=address, contacts=contacts)
