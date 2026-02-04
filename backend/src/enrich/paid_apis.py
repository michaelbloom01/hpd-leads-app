"""
Paid API enrichment (Apollo, Clearbit, etc.).

See docs/04-enrichment-strategy.md for details.
"""
import logging
from typing import Optional, Dict

import requests

from config.settings import settings

logger = logging.getLogger(__name__)


class ApolloClient:
    """Apollo.io API client for contact enrichment."""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.apollo_api_key
        self.base_url = "https://api.apollo.io/v1"
    
    def enrich_company(self, domain: str) -> Optional[Dict]:
        """
        Enrich a company by domain.
        
        Args:
            domain: Company website domain
            
        Returns:
            Company data including contacts
        """
        if not self.api_key:
            logger.warning("Apollo API key not configured")
            return None
        
        # TODO: Implement Apollo company enrichment
        # Endpoint: POST /organizations/enrich
        raise NotImplementedError("Implement in next sprint")
    
    def find_person(self, name: str, company: str) -> Optional[Dict]:
        """
        Find a person by name and company.
        
        Args:
            name: Person's name
            company: Company name
            
        Returns:
            Person data including email
        """
        if not self.api_key:
            logger.warning("Apollo API key not configured")
            return None
        
        # TODO: Implement Apollo person search
        raise NotImplementedError("Implement in next sprint")


# Add other API clients as needed:
# - ClearbitClient
# - PeopleDataLabsClient
