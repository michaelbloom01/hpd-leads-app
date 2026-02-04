"""
Web crawl enrichment - Google search and website scraping.

See docs/04-enrichment-strategy.md for details.
"""
import json
import logging
import random
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    from googlesearch import search as google_search
    GOOGLE_SEARCH_AVAILABLE = True
except ImportError:
    GOOGLE_SEARCH_AVAILABLE = False

try:
    import trafilatura
    TRAFILATURA_AVAILABLE = True
except ImportError:
    TRAFILATURA_AVAILABLE = False

from config.settings import settings

logger = logging.getLogger(__name__)

# DuckDuckGo HTML search URL (fallback when Google fails)
DUCKDUCKGO_URL = "https://html.duckduckgo.com/html/"


# Domains to exclude from search results (directories, review sites, etc.)
EXCLUDED_DOMAINS = {
    "yelp.com",
    "linkedin.com",
    "facebook.com",
    "twitter.com",
    "instagram.com",
    "yellowpages.com",
    "bbb.org",
    "manta.com",
    "bizapedia.com",
    "opencorporates.com",
    "crunchbase.com",
    "zoominfo.com",
    "dnb.com",
    "buzzfile.com",
    "owler.com",
    "wikipedia.org",
    "zillow.com",
    "apartments.com",
    "rent.com",
    "trulia.com",
    "realtor.com",
    "nyc.gov",
    "ny.gov",
    "google.com",
}


@dataclass
class WebEnrichmentResult:
    """Result from web crawl enrichment."""
    website: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    business_summary: Optional[str] = None
    owner_principal: Optional[str] = None
    source: str = "web_crawl"
    success: bool = False
    error: Optional[str] = None
    cached: bool = False
    timestamp: Optional[str] = None

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return asdict(self)


class WebCrawlCache:
    """Simple file-based cache for web crawl results."""
    
    def __init__(self, cache_dir: Path = None, ttl_days: int = 30):
        self.cache_dir = cache_dir or settings.cache_dir / "web_crawl"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl = timedelta(days=ttl_days)
        self.cache_file = self.cache_dir / "web_crawl_cache.json"
        self._cache: Dict = self._load_cache()
    
    def _load_cache(self) -> Dict:
        """Load cache from disk."""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load cache: {e}")
        return {}
    
    def _save_cache(self):
        """Save cache to disk."""
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, indent=2)
        except IOError as e:
            logger.warning(f"Failed to save cache: {e}")
    
    def _normalize_key(self, company_name: str) -> str:
        """Normalize company name for cache key."""
        # Lowercase, remove punctuation, remove common suffixes
        key = company_name.lower().strip()
        key = re.sub(r"[^\w\s]", "", key)
        for suffix in [" llc", " inc", " corp", " company", " co", " ltd"]:
            if key.endswith(suffix):
                key = key[:-len(suffix)]
        key = re.sub(r"\s+", "_", key.strip())
        return key
    
    def get(self, company_name: str) -> Optional[WebEnrichmentResult]:
        """Get cached result if valid."""
        key = self._normalize_key(company_name)
        if key not in self._cache:
            return None
        
        entry = self._cache[key]
        cached_time = datetime.fromisoformat(entry.get("timestamp", "2000-01-01"))
        
        if datetime.now() - cached_time > self.ttl:
            logger.debug(f"Cache expired for {company_name}")
            return None
        
        result = WebEnrichmentResult(
            website=entry.get("website"),
            phone=entry.get("phone"),
            email=entry.get("email"),
            address=entry.get("address"),
            business_summary=entry.get("business_summary"),
            owner_principal=entry.get("owner_principal"),
            source=entry.get("source", "web_crawl"),
            success=entry.get("success", False),
            error=entry.get("error"),
            cached=True,
            timestamp=entry.get("timestamp"),
        )
        return result
    
    def set(self, company_name: str, result: WebEnrichmentResult):
        """Cache a result."""
        key = self._normalize_key(company_name)
        result.timestamp = datetime.now().isoformat()
        self._cache[key] = result.to_dict()
        self._save_cache()


class WebCrawler:
    """Find and scrape company websites."""
    
    # Regex patterns for extraction
    PHONE_PATTERN = re.compile(
        r"""
        (?:
            \+?1?[-.\s]?
            )?
        \(?(\d{3})\)?    # Area code
        [-.\s]?
        (\d{3})          # First 3 digits
        [-.\s]?
        (\d{4})          # Last 4 digits
        """,
        re.VERBOSE
    )
    
    EMAIL_PATTERN = re.compile(
        r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        re.IGNORECASE
    )
    
    OWNER_KEYWORDS = [
        "owner", "principal", "founder", "president", "ceo",
        "managing director", "managing partner", "partner"
    ]
    
    def __init__(self, use_cache: bool = True):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
        self.delay = settings.web_crawl_delay_seconds
        self.cache = WebCrawlCache() if use_cache else None
        
        if not GOOGLE_SEARCH_AVAILABLE:
            logger.warning("googlesearch-python not installed. Google search will be disabled.")
        if not TRAFILATURA_AVAILABLE:
            logger.warning("trafilatura not installed. Text extraction will use basic methods.")
    
    def enrich(self, company_name: str) -> WebEnrichmentResult:
        """
        Full web enrichment: find website, scrape contact info.
        
        Args:
            company_name: Company name to enrich
            
        Returns:
            WebEnrichmentResult with extracted data
        """
        # Check cache first
        if self.cache:
            cached = self.cache.get(company_name)
            if cached:
                logger.info(f"Cache hit for {company_name}")
                return cached
        
        result = WebEnrichmentResult()
        
        try:
            # Step 1: Find website
            website = self.find_website(company_name)
            if not website:
                result.error = "No website found"
                if self.cache:
                    self.cache.set(company_name, result)
                return result
            
            result.website = website
            
            # Step 2: Scrape contact info
            contact_info = self.scrape_contact_info(website)
            result.phone = contact_info.get("phone")
            result.email = contact_info.get("email")
            result.address = contact_info.get("address")
            result.business_summary = contact_info.get("summary")
            result.owner_principal = contact_info.get("owner")
            result.success = True
            
        except Exception as e:
            logger.warning(f"Web enrichment failed for {company_name}: {e}")
            result.error = str(e)
        
        # Cache the result
        if self.cache:
            self.cache.set(company_name, result)
        
        return result
    
    def find_website(self, company_name: str) -> Optional[str]:
        """
        Search for the company website using multiple search engines.
        
        Args:
            company_name: Company name to search
            
        Returns:
            Website URL or None
        """
        # Try Google first (if available)
        if GOOGLE_SEARCH_AVAILABLE:
            result = self._search_google(company_name)
            if result:
                return result
        
        # Fallback to DuckDuckGo
        result = self._search_duckduckgo(company_name)
        if result:
            return result
        
        logger.info(f"No website found for {company_name}")
        return None
    
    def _search_google(self, company_name: str) -> Optional[str]:
        """Search Google for company website."""
        search_queries = [
            f'"{company_name}" property management NYC site',
            f'"{company_name}" NYC real estate',
            f'{company_name} property management',
        ]
        
        for query in search_queries:
            try:
                logger.debug(f"Google search: {query}")
                
                # Add random delay to avoid rate limiting
                time.sleep(self.delay + random.uniform(0.5, 1.5))
                
                # Search Google (limit to 10 results)
                results = list(google_search(query, num_results=10, sleep_interval=2))
                
                if not results:
                    logger.debug(f"Google returned no results for: {query}")
                    continue
                
                # Find first result that's not an excluded domain
                for url in results:
                    if self._is_valid_company_url(url):
                        logger.info(f"Found website via Google for {company_name}: {url}")
                        return self._normalize_url(url)
                
            except Exception as e:
                logger.warning(f"Google search failed for query '{query}': {e}")
                time.sleep(5)
        
        return None
    
    def _search_duckduckgo(self, company_name: str) -> Optional[str]:
        """Search DuckDuckGo for company website (fallback)."""
        import urllib.parse
        
        search_queries = [
            f'{company_name} property management NYC',
            f'{company_name} real estate NYC',
            f'{company_name} official website',
        ]
        
        for query in search_queries:
            try:
                logger.debug(f"DuckDuckGo search: {query}")
                
                time.sleep(self.delay + random.uniform(0.5, 1.0))
                
                # Make request to DuckDuckGo HTML version
                response = self.session.post(
                    DUCKDUCKGO_URL,
                    data={"q": query},
                    timeout=10
                )
                
                if response.status_code != 200:
                    logger.debug(f"DuckDuckGo returned status {response.status_code}")
                    continue
                
                # Parse results
                soup = BeautifulSoup(response.text, "lxml")
                
                # DuckDuckGo organic results are in result__a links
                # Skip ad results which have different URL patterns
                for link in soup.select("a.result__a"):
                    href = link.get("href", "")
                    
                    # Skip DuckDuckGo internal/ad redirect URLs
                    if "duckduckgo.com" in href:
                        # Try to extract the actual URL from uddg parameter
                        if "uddg=" in href:
                            try:
                                parsed = urllib.parse.urlparse(href)
                                params = urllib.parse.parse_qs(parsed.query)
                                if "uddg" in params:
                                    href = urllib.parse.unquote(params["uddg"][0])
                            except Exception:
                                continue
                        else:
                            # Skip DuckDuckGo internal links
                            continue
                    
                    # Ensure proper URL format
                    if not href.startswith("http"):
                        if href.startswith("//"):
                            href = "https:" + href
                        else:
                            continue  # Skip malformed URLs
                    
                    # Validate it's not a blocked domain
                    if self._is_valid_company_url(href):
                        logger.info(f"Found website via DuckDuckGo for {company_name}: {href}")
                        return self._normalize_url(href)
                
                # Also check result__url elements (display URLs)
                for span in soup.select("span.result__url"):
                    url_text = span.get_text(strip=True)
                    if url_text:
                        # Add https if missing
                        if not url_text.startswith("http"):
                            url_text = "https://" + url_text
                        if self._is_valid_company_url(url_text):
                            logger.info(f"Found website via DuckDuckGo for {company_name}: {url_text}")
                            return self._normalize_url(url_text)
                        
            except Exception as e:
                logger.warning(f"DuckDuckGo search failed for query '{query}': {e}")
                time.sleep(2)
        
        return None
    
    def _is_valid_company_url(self, url: str) -> bool:
        """Check if URL is a valid company website (not a directory)."""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            
            # Remove www. prefix
            if domain.startswith("www."):
                domain = domain[4:]
            
            # Check against excluded domains
            for excluded in EXCLUDED_DOMAINS:
                if domain == excluded or domain.endswith(f".{excluded}"):
                    return False
            
            return True
        except Exception:
            return False
    
    def _normalize_url(self, url: str) -> str:
        """Normalize URL to base domain."""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"
    
    def scrape_contact_info(self, url: str) -> Dict:
        """
        Scrape contact info from a website.
        
        Args:
            url: Website URL
            
        Returns:
            Dict with phone, email, address, summary, owner
        """
        result = {
            "phone": None,
            "email": None,
            "address": None,
            "summary": None,
            "owner": None,
        }
        
        # Pages to try in order of preference
        contact_paths = ["/contact", "/contact-us", "/contactus"]
        about_paths = ["/about", "/about-us", "/aboutus", "/team", "/our-team"]
        
        # First try contact pages
        for path in contact_paths:
            page_url = urljoin(url, path)
            content = self._fetch_page(page_url)
            if content:
                self._extract_contact_data(content, result)
                if result["phone"] or result["email"]:
                    break
        
        # Then try about pages for additional info
        for path in about_paths:
            page_url = urljoin(url, path)
            content = self._fetch_page(page_url)
            if content:
                self._extract_about_data(content, result)
                if result["summary"] or result["owner"]:
                    break
        
        # Finally try homepage if we still need data
        if not result["phone"] and not result["email"]:
            content = self._fetch_page(url)
            if content:
                self._extract_contact_data(content, result)
        
        if not result["summary"]:
            content = self._fetch_page(url)
            if content:
                self._extract_about_data(content, result)
        
        return result
    
    def _fetch_page(self, url: str) -> Optional[str]:
        """Fetch a page with rate limiting."""
        try:
            response = self._respectful_get(url)
            if response.status_code == 200:
                return response.text
            logger.debug(f"Got status {response.status_code} for {url}")
        except requests.RequestException as e:
            logger.debug(f"Failed to fetch {url}: {e}")
        return None
    
    def _extract_contact_data(self, html: str, result: Dict):
        """Extract phone, email, address from HTML."""
        soup = BeautifulSoup(html, "lxml")
        
        # Remove script and style elements
        for element in soup(["script", "style", "noscript"]):
            element.decompose()
        
        text = soup.get_text(separator=" ", strip=True)
        
        # Extract phone from tel: links first
        if not result["phone"]:
            for link in soup.find_all("a", href=True):
                href = link.get("href", "")
                if href.startswith("tel:"):
                    phone = href.replace("tel:", "").strip()
                    normalized = self._normalize_phone(phone)
                    if normalized:
                        result["phone"] = normalized
                        break
        
        # Extract phone from text
        if not result["phone"]:
            matches = self.PHONE_PATTERN.findall(text)
            for match in matches:
                normalized = self._normalize_phone("".join(match))
                if normalized:
                    result["phone"] = normalized
                    break
        
        # Extract email from mailto: links first
        if not result["email"]:
            for link in soup.find_all("a", href=True):
                href = link.get("href", "")
                if href.startswith("mailto:"):
                    email = href.replace("mailto:", "").split("?")[0].strip()
                    if self._is_valid_email(email):
                        result["email"] = email.lower()
                        break
        
        # Extract email from text
        if not result["email"]:
            matches = self.EMAIL_PATTERN.findall(text)
            for email in matches:
                if self._is_valid_email(email):
                    result["email"] = email.lower()
                    break
        
        # Extract address from structured data or address tag
        if not result["address"]:
            # Try Schema.org markup
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.string)
                    if isinstance(data, dict):
                        addr = data.get("address")
                        if addr:
                            result["address"] = self._format_address(addr)
                            break
                except (json.JSONDecodeError, TypeError):
                    pass
            
            # Try <address> tag
            if not result["address"]:
                addr_tag = soup.find("address")
                if addr_tag:
                    result["address"] = addr_tag.get_text(separator=", ", strip=True)
    
    def _extract_about_data(self, html: str, result: Dict):
        """Extract summary and owner info from HTML."""
        soup = BeautifulSoup(html, "lxml")
        
        # Remove script and style
        for element in soup(["script", "style", "noscript"]):
            element.decompose()
        
        # Extract summary from meta description
        if not result["summary"]:
            meta_desc = soup.find("meta", attrs={"name": "description"})
            if meta_desc:
                result["summary"] = meta_desc.get("content", "").strip()[:500]
        
        # Extract summary from first substantial paragraph if no meta
        if not result["summary"]:
            if TRAFILATURA_AVAILABLE:
                text = trafilatura.extract(str(soup))
                if text:
                    # Take first 500 chars as summary
                    result["summary"] = text[:500].strip()
            else:
                # Fallback: find first paragraph with substantial text
                for p in soup.find_all("p"):
                    text = p.get_text(strip=True)
                    if len(text) > 100:
                        result["summary"] = text[:500]
                        break
        
        # Try to find owner/principal
        if not result["owner"]:
            text = soup.get_text(separator=" ", strip=True).lower()
            
            # Look for owner keywords near names
            for keyword in self.OWNER_KEYWORDS:
                if keyword in text:
                    # Find the context around the keyword
                    idx = text.find(keyword)
                    context = text[idx:idx+200]
                    
                    # Look for a name pattern (Capitalized First Last)
                    # This is a simple heuristic
                    name_match = re.search(
                        r"(?:owner|principal|founder|president|ceo|managing\s+(?:director|partner)|partner)"
                        r"[:\s,]*([A-Z][a-z]+ [A-Z][a-z]+)",
                        soup.get_text(separator=" ", strip=True),
                        re.IGNORECASE
                    )
                    if name_match:
                        result["owner"] = name_match.group(1)
                        break
    
    def _normalize_phone(self, phone: str) -> Optional[str]:
        """Normalize phone number to (XXX) XXX-XXXX format."""
        # Remove all non-digits
        digits = re.sub(r"\D", "", phone)
        
        # Remove leading 1 (country code)
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        
        # Must be exactly 10 digits
        if len(digits) != 10:
            return None
        
        # Format
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    
    def _is_valid_email(self, email: str) -> bool:
        """Basic email validation."""
        if not email or "@" not in email:
            return False
        
        # Skip generic/noreply emails
        skip_patterns = ["noreply", "no-reply", "donotreply", "info@", "support@", "sales@"]
        email_lower = email.lower()
        for pattern in skip_patterns:
            if pattern in email_lower:
                return False
        
        return True
    
    def _format_address(self, addr: dict) -> str:
        """Format Schema.org address to string."""
        parts = []
        for field in ["streetAddress", "addressLocality", "addressRegion", "postalCode"]:
            value = addr.get(field)
            if value:
                parts.append(value)
        return ", ".join(parts)
    
    def _respectful_get(self, url: str) -> requests.Response:
        """Make a request with rate limiting."""
        time.sleep(self.delay + random.uniform(0.1, 0.5))
        return self.session.get(url, timeout=10)
