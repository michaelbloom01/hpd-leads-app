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
    linkedin_url: Optional[str] = None  # Company LinkedIn page
    linkedin_people: Optional[List[str]] = None  # Key people's LinkedIn profiles
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
        # R8: Updated to current Chrome version string (2025)
        self.session.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
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
            
            # Step 3: Find LinkedIn profiles
            linkedin_results = self.find_linkedin_profiles(company_name)
            result.linkedin_url = linkedin_results.get("company_page")
            result.linkedin_people = linkedin_results.get("people", [])
            
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
        # Try DuckDuckGo first (more reliable, less rate limiting)
        result = self._search_duckduckgo(company_name)
        if result:
            return result
        
        # Try Bing as second fallback
        result = self._search_bing(company_name)
        if result:
            return result
        
        # Try Google last (most likely to rate limit)
        if GOOGLE_SEARCH_AVAILABLE:
            result = self._search_google(company_name)
            if result:
                return result
        
        logger.info(f"No website found for {company_name}")
        return None

    def search_website(self, company_name: str) -> Optional[str]:
        """Backward-compatible alias for older call sites/tests."""
        return self.find_website(company_name)
    
    def find_linkedin_profiles(self, company_name: str) -> Dict:
        """
        Search for LinkedIn company page and key people.
        
        Args:
            company_name: Company name to search
            
        Returns:
            Dict with 'company_page' and 'people' list
        """
        result = {"company_page": None, "people": []}
        
        if not GOOGLE_SEARCH_AVAILABLE:
            return result
        
        try:
            # Search for company LinkedIn page
            company_query = f'site:linkedin.com/company "{company_name}" NYC property management'
            time.sleep(self.delay + random.uniform(0.5, 1.0))
            
            company_results = list(google_search(company_query, num_results=5, sleep_interval=2))
            
            for url in company_results:
                if "linkedin.com/company/" in url.lower():
                    result["company_page"] = url
                    logger.info(f"Found LinkedIn company page for {company_name}: {url}")
                    break
            
            # Search for key people (principals/owners) at the company
            people_query = f'site:linkedin.com/in "{company_name}" (owner OR principal OR president OR CEO OR founder) NYC'
            time.sleep(self.delay + random.uniform(0.5, 1.0))
            
            people_results = list(google_search(people_query, num_results=5, sleep_interval=2))
            
            for url in people_results:
                if "linkedin.com/in/" in url.lower():
                    # Avoid duplicates
                    if url not in result["people"]:
                        result["people"].append(url)
                        if len(result["people"]) >= 3:  # Cap at 3 people
                            break
            
            if result["people"]:
                logger.info(f"Found {len(result['people'])} LinkedIn profiles for {company_name}")
                
        except Exception as e:
            logger.warning(f"LinkedIn search failed for {company_name}: {e}")
        
        return result
    
    def _search_google(self, company_name: str) -> Optional[str]:
        """Search Google for company website with exponential backoff."""
        search_queries = [
            f'"{company_name}" property management NYC site',
            f'"{company_name}" NYC real estate',
            f'{company_name} property management',
        ]
        
        for query in search_queries:
            # Retry with exponential backoff
            for attempt in range(3):
                try:
                    logger.debug(f"Google search (attempt {attempt + 1}): {query}")
                    
                    # Exponential backoff delay
                    backoff_delay = self.delay * (2 ** attempt) + random.uniform(0.5, 1.5)
                    time.sleep(backoff_delay)
                    
                    # Search Google (limit to 10 results)
                    results = list(google_search(query, num_results=10, sleep_interval=3))
                    
                    if not results:
                        logger.debug(f"Google returned no results for: {query}")
                        break  # No results, try next query
                    
                    # Find first result that's not an excluded domain
                    for url in results:
                        if self._is_valid_company_url(url):
                            logger.info(f"Found website via Google for {company_name}: {url}")
                            return self._normalize_url(url)
                    
                    break  # Got results but none valid, try next query
                    
                except Exception as e:
                    error_str = str(e).lower()
                    if "429" in error_str or "too many requests" in error_str or "rate" in error_str:
                        # Rate limited - exponential backoff
                        wait_time = 10 * (2 ** attempt)
                        logger.warning(f"Google rate limited, waiting {wait_time}s (attempt {attempt + 1}/3)")
                        time.sleep(wait_time)
                    else:
                        logger.warning(f"Google search failed for query '{query}': {e}")
                        break  # Non-rate-limit error, try next query
        
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
                
                # R8: DuckDuckGo results — primary selector with fallback
                ddg_links = soup.select("a.result__a")
                if not ddg_links:
                    # Fallback: try generic link selectors in result divs
                    ddg_links = soup.select(".results .result a[href]")
                if not ddg_links:
                    # Second fallback: any link with an external href
                    ddg_links = [a for a in soup.select("a[href]") 
                                 if a.get("href", "").startswith("http") and "duckduckgo.com" not in a.get("href", "")]
                
                for link in ddg_links:
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
                url_spans = soup.select("span.result__url")
                if not url_spans:
                    url_spans = soup.select(".result .url")  # R8: fallback selector
                for span in url_spans:
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
    
    def _search_bing(self, company_name: str) -> Optional[str]:
        """Search Bing for company website (additional fallback)."""
        import urllib.parse
        
        search_queries = [
            f'{company_name} property management NYC',
            f'{company_name} real estate NYC',
        ]
        
        for query in search_queries:
            try:
                logger.debug(f"Bing search: {query}")
                
                time.sleep(self.delay + random.uniform(0.5, 1.0))
                
                # Bing search URL
                encoded_query = urllib.parse.quote_plus(query)
                bing_url = f"https://www.bing.com/search?q={encoded_query}"
                
                response = self.session.get(bing_url, timeout=10)
                
                if response.status_code != 200:
                    logger.debug(f"Bing returned status {response.status_code}")
                    continue
                
                soup = BeautifulSoup(response.text, "lxml")
                
                # R8: Bing results — primary selector with fallback
                bing_results = soup.select("li.b_algo a")
                if not bing_results:
                    # Fallback: try generic result selectors
                    bing_results = soup.select(".b_results li a[href]")
                if not bing_results:
                    bing_results = soup.select("#b_results a[href]")
                
                for result_el in bing_results:
                    href = result_el.get("href", "")
                    
                    # Skip Bing internal links
                    if not href or "bing.com" in href or "microsoft.com" in href:
                        continue
                    
                    if not href.startswith("http"):
                        continue
                    
                    if self._is_valid_company_url(href):
                        logger.info(f"Found website via Bing for {company_name}: {href}")
                        return self._normalize_url(href)
                        
            except Exception as e:
                logger.warning(f"Bing search failed for query '{query}': {e}")
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
        
        # Finally try homepage if we still need data (fetch once and reuse)
        homepage_content = None
        if not result["phone"] and not result["email"]:
            homepage_content = self._fetch_page(url)
            if homepage_content:
                self._extract_contact_data(homepage_content, result)
        
        if not result["summary"]:
            if homepage_content is None:
                homepage_content = self._fetch_page(url)
            if homepage_content:
                self._extract_about_data(homepage_content, result)
        
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
    
    def deep_scrape(self, url: str, company_name: str = "") -> Dict:
        """
        Deep scrape a website for comprehensive company information.
        
        Args:
            url: Website URL
            company_name: Company name for context
            
        Returns:
            Dict with owner_names, year_established, service_areas, description, phones, emails, social_links
        """
        result = {
            "owner_names": [],
            "year_established": None,
            "service_areas": [],
            "description": None,
            "phones": [],
            "emails": [],
            "social_links": {},
        }
        
        # Pages to scrape
        pages_to_check = [
            url,  # Homepage
            urljoin(url, "/about"),
            urljoin(url, "/about-us"),
            urljoin(url, "/contact"),
            urljoin(url, "/contact-us"),
            urljoin(url, "/team"),
            urljoin(url, "/our-team"),
            urljoin(url, "/leadership"),
        ]
        
        all_text = ""
        social_platforms = {
            "linkedin": ["linkedin.com"],
            "facebook": ["facebook.com"],
            "twitter": ["twitter.com", "x.com"],
            "instagram": ["instagram.com"],
        }
        
        for page_url in pages_to_check:
            try:
                content = self._fetch_page(page_url)
                if not content:
                    continue
                
                soup = BeautifulSoup(content, "lxml")
                
                # Remove noise
                for element in soup(["script", "style", "noscript", "nav", "footer"]):
                    element.decompose()
                
                page_text = soup.get_text(separator=" ", strip=True)
                all_text += " " + page_text
                
                # Extract phones from tel: links
                for link in soup.find_all("a", href=True):
                    href = link.get("href", "")
                    if href.startswith("tel:"):
                        phone = href.replace("tel:", "").strip()
                        normalized = self._normalize_phone(phone)
                        if normalized and normalized not in result["phones"]:
                            result["phones"].append(normalized)
                
                # Extract emails from mailto: links
                for link in soup.find_all("a", href=True):
                    href = link.get("href", "")
                    if href.startswith("mailto:"):
                        email = href.replace("mailto:", "").split("?")[0].strip().lower()
                        if self._is_valid_email(email) and email not in result["emails"]:
                            result["emails"].append(email)
                
                # Extract social links
                for link in soup.find_all("a", href=True):
                    href = link.get("href", "").lower()
                    for platform, domains in social_platforms.items():
                        if platform not in result["social_links"]:
                            for domain in domains:
                                if domain in href:
                                    result["social_links"][platform] = link.get("href")
                                    break
                
                # Extract emails from page text
                email_matches = self.EMAIL_PATTERN.findall(page_text)
                for email in email_matches:
                    email = email.lower()
                    if self._is_valid_email(email) and email not in result["emails"]:
                        result["emails"].append(email)
                
                # Extract phones from page text
                phone_matches = self.PHONE_PATTERN.findall(page_text)
                for match in phone_matches:
                    normalized = self._normalize_phone("".join(match))
                    if normalized and normalized not in result["phones"]:
                        result["phones"].append(normalized)
                
            except Exception as e:
                logger.debug(f"Failed to scrape {page_url}: {e}")
                continue
        
        # Extract year established
        year_patterns = [
            r"(?:established|founded|since|serving since|in business since)\s*(?:in\s*)?(\d{4})",
            r"(\d{4})\s*(?:-\s*(?:\d{4}|present))",  # "2005 - present" or "2005 - 2024"
        ]
        for pattern in year_patterns:
            match = re.search(pattern, all_text.lower())
            if match:
                year = int(match.group(1))
                if 1900 < year < 2030:  # Sanity check
                    result["year_established"] = str(year)
                    break
        
        # Extract owner/principal names
        owner_patterns = [
            r"(?:owner|principal|founder|president|ceo|managing director|managing partner)[:\s,]+([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)",
            r"([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)[,\s]+(?:owner|principal|founder|president|ceo)",
        ]
        for pattern in owner_patterns:
            matches = re.findall(pattern, all_text, re.IGNORECASE)
            for name in matches:
                name = name.strip()
                if name and name not in result["owner_names"] and len(name.split()) >= 2:
                    result["owner_names"].append(name)
                if len(result["owner_names"]) >= 3:
                    break
        
        # Extract service areas (look for NYC borough mentions)
        borough_keywords = ["manhattan", "brooklyn", "queens", "bronx", "staten island", "new york city", "nyc"]
        for borough in borough_keywords:
            if borough in all_text.lower():
                proper_name = borough.title() if borough != "nyc" else "NYC"
                if proper_name not in result["service_areas"]:
                    result["service_areas"].append(proper_name)
        
        # Extract description from meta or first substantial text
        try:
            homepage = self._fetch_page(url)
            if homepage:
                soup = BeautifulSoup(homepage, "lxml")
                meta_desc = soup.find("meta", attrs={"name": "description"})
                if meta_desc:
                    result["description"] = meta_desc.get("content", "").strip()[:500]
                
                if not result["description"] and TRAFILATURA_AVAILABLE:
                    text = trafilatura.extract(str(soup))
                    if text:
                        result["description"] = text[:500].strip()
        except Exception as e:
            logger.debug(f"Failed to extract description: {e}")
        
        return result