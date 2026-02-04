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
    
    def find_business(self, company_name: str, location: str = "New York, NY") -> Optional[Dict]:
        """
        Search for a business and get its phone number.
        
        Args:
            company_name: Business name to search
            location: Location context (default NYC)
            
        Returns:
            Dict with phone, place_id, maps_url, or None
        """
        if not self.api_key:
            logger.debug("Google Places API key not configured")
            return None
        
        try:
            # Step 1: Text Search to find the place
            search_query = f"{company_name} property management {location}"
            search_url = f"{self.base_url}/textsearch/json"
            
            response = requests.get(search_url, params={
                "query": search_query,
                "key": self.api_key,
            }, timeout=10)
            
            if response.status_code != 200:
                logger.warning(f"Google Places search failed: {response.status_code}")
                return None
            
            data = response.json()
            if data.get("status") != "OK" or not data.get("results"):
                logger.debug(f"No Google Places results for: {company_name}")
                return None
            
            # Get the first result
            place = data["results"][0]
            place_id = place.get("place_id")
            
            if not place_id:
                return None
            
            # Step 2: Place Details to get phone number
            details_url = f"{self.base_url}/details/json"
            
            details_response = requests.get(details_url, params={
                "place_id": place_id,
                "fields": "formatted_phone_number,international_phone_number,website,url,name",
                "key": self.api_key,
            }, timeout=10)
            
            if details_response.status_code != 200:
                logger.warning(f"Google Places details failed: {details_response.status_code}")
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
                "maps_url": result.get("url"),  # Direct link to Google Maps
                "name": result.get("name"),
            }
            
        except Exception as e:
            logger.warning(f"Google Places error for {company_name}: {e}")
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
               use_web: bool = True) -> EnrichmentResult:
        """
        Enrich a lead using multiple sources.
        
        Priority order:
        1. Google Places (most reliable for phone) - requires API key
        2. NY DOS Registry (corporation info) - always free
        3. Web Crawl (phone, email, website) - always free
        4. Hunter.io (email) - requires API key
        
        Args:
            lead_id: Lead identifier
            company_name: Company name to search
            website: Known website (optional, improves Hunter results)
            location: Location context for Google search
            use_google: Whether to use Google Places
            use_ny_dos: Whether to use NY DOS registry
            use_hunter: Whether to use Hunter.io
            use_web: Whether to use web crawling
            
        Returns:
            EnrichmentResult with all found contacts and sources
        """
        result = EnrichmentResult(lead_id=lead_id, company_name=company_name)
        
        # Track which website we find
        found_website = website
        
        # 1. Google Places - Best for phone numbers (if configured)
        if use_google and self.google_places.is_configured():
            result.sources_tried.append("google_places")
            self._api_calls["google_places"] += 1
            
            try:
                gp_result = self.google_places.find_business(company_name, location)
                
                if gp_result:
                    result.sources_succeeded.append("google_places")
                    result.google_place_id = gp_result.get("place_id")
                    
                    # Add phone with high confidence
                    phone = gp_result.get("phone")
                    if phone:
                        normalized = self._normalize_phone(phone)
                        if normalized:
                            result.phones.append(ContactInfo(
                                value=normalized,
                                type="phone",
                                source="google_places",
                                source_url=gp_result.get("maps_url"),
                                confidence=90,  # High confidence from Google
                                verified=True,
                            ))
                    
                    # Use Google's website if we don't have one
                    if not found_website and gp_result.get("website"):
                        found_website = gp_result.get("website")
                        result.website = found_website
                        result.website_source = "google_places"
                        
            except Exception as e:
                result.errors.append(f"google_places: {str(e)}")
                logger.warning(f"Google Places failed for {company_name}: {e}")
        
        # 2. NY DOS Registry - Corporation info and registered agent (always free)
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
                    # Link to NY DOS website for verification
                    result.dos_lookup_url = f"https://appext20.dos.ny.gov/corp_public/CORPSEARCH.ENTITY_INFORMATION?p_token=&p_nameid={dos_entity.dos_id}"
                    
                    logger.info(f"NY DOS found: {dos_entity.name} (ID: {dos_entity.dos_id})")
                else:
                    logger.debug(f"No NY DOS record found for: {company_name}")
                    
            except Exception as e:
                result.errors.append(f"ny_dos: {str(e)}")
                logger.warning(f"NY DOS lookup failed for {company_name}: {e}")
        
        # 3. Web Crawl - Find website and scrape contacts (always free)
        if use_web:
            result.sources_tried.append("web_crawl")
            self._api_calls["web_crawl"] += 1
            
            try:
                web_result = self.web_crawler.enrich(company_name)
                
                if web_result.success:
                    result.sources_succeeded.append("web_crawl")
                    
                    # Use website if we don't have one
                    if not found_website and web_result.website:
                        found_website = web_result.website
                        result.website = found_website
                        result.website_source = "web_crawl"
                    
                    # Add phone (lower confidence than Google)
                    if web_result.phone:
                        # Check if we already have this phone
                        existing_phones = {p.value for p in result.phones}
                        if web_result.phone not in existing_phones:
                            result.phones.append(ContactInfo(
                                value=web_result.phone,
                                type="phone",
                                source="web_crawl",
                                source_url=web_result.website,
                                confidence=70,
                                verified=False,
                            ))
                    
                    # Add email
                    if web_result.email:
                        result.emails.append(ContactInfo(
                            value=web_result.email,
                            type="email",
                            source="web_crawl",
                            source_url=web_result.website,
                            confidence=70,
                            verified=False,
                        ))
                        
            except Exception as e:
                result.errors.append(f"web_crawl: {str(e)}")
                logger.warning(f"Web crawl failed for {company_name}: {e}")
        
        # 4. Hunter.io - Best for emails if we have a domain (requires API key)
        if use_hunter and self.hunter.is_configured() and found_website:
            result.sources_tried.append("hunter")
            self._api_calls["hunter"] += 1
            
            try:
                # Extract domain from website
                domain = urlparse(found_website).netloc
                if domain.startswith("www."):
                    domain = domain[4:]
                
                if domain:
                    hunter_results = self.hunter.domain_search(domain, limit=3)
                    
                    if hunter_results:
                        result.sources_succeeded.append("hunter")
                        
                        for hr in hunter_results:
                            email = hr.get("email")
                            if email:
                                # Check if we already have this email
                                existing_emails = {e.value.lower() for e in result.emails}
                                if email.lower() not in existing_emails:
                                    # Build source URL from Hunter sources
                                    sources = hr.get("sources", [])
                                    source_url = sources[0].get("uri") if sources else f"https://hunter.io/verify/{email}"
                                    
                                    result.emails.append(ContactInfo(
                                        value=email,
                                        type="email",
                                        source="hunter",
                                        source_url=source_url,
                                        confidence=hr.get("confidence", 50),
                                        verified=hr.get("verification", {}).get("status") == "valid",
                                    ))
                                    
            except Exception as e:
                result.errors.append(f"hunter: {str(e)}")
                logger.warning(f"Hunter failed for {company_name}: {e}")
        
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
                         website: Optional[str] = None) -> EnrichmentResult:
    """
    Enrich a lead's contact information using all available sources.
    
    Args:
        lead_id: Lead identifier
        company_name: Company name
        website: Known website (optional)
        
    Returns:
        EnrichmentResult with phones, emails, and source attribution
    """
    enricher = MultiSourceEnricher()
    return enricher.enrich(lead_id, company_name, website)
