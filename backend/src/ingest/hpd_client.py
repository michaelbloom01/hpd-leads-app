"""
HPD Multiple Dwelling Registrations API client.

See docs/01-data-sources.md for API details.

Two datasets are used:
1. Multiple Dwelling Registrations (tesw-yqqr) - Building info
2. Registration Contacts (feu5-w2e2) - Owner/agent contact info
"""
import logging
import time
from datetime import date
from typing import List, Optional, Dict

import requests

from config.settings import settings

logger = logging.getLogger(__name__)

# NYC Open Data Socrata endpoints
BUILDINGS_ENDPOINT = "https://data.cityofnewyork.us/resource/tesw-yqqr.json"
CONTACTS_ENDPOINT = "https://data.cityofnewyork.us/resource/feu5-w2e2.json"

# Default page size for Socrata API
PAGE_SIZE = 1000


class HPDClient:
    """Client for fetching HPD Multiple Dwelling Registration data."""
    
    def __init__(self, app_token: Optional[str] = None):
        """
        Initialize the HPD client.
        
        Args:
            app_token: NYC Open Data app token (optional but recommended)
        """
        self.app_token = app_token or settings.nyc_open_data_app_token
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/json"
        if self.app_token:
            self.session.headers["X-App-Token"] = self.app_token
    
    def fetch_all_buildings(self, limit: Optional[int] = None) -> List[dict]:
        """
        Fetch all building registrations from HPD, handling pagination.
        
        Args:
            limit: Maximum records to fetch (None for all)
            
        Returns:
            List of raw building records
        """
        return self._fetch_all_paginated(BUILDINGS_ENDPOINT, limit=limit)
    
    def fetch_all_contacts(self, limit: Optional[int] = None) -> List[dict]:
        """
        Fetch all registration contacts from HPD, handling pagination.
        
        Args:
            limit: Maximum records to fetch (None for all)
            
        Returns:
            List of raw contact records
        """
        return self._fetch_all_paginated(CONTACTS_ENDPOINT, limit=limit)
    
    def fetch_buildings_since(self, since_date: date) -> List[dict]:
        """
        Fetch buildings with registrations updated since a specific date.
        
        Args:
            since_date: Only fetch records updated after this date
            
        Returns:
            List of raw building records
        """
        where_clause = f"lastregistrationdate > '{since_date.isoformat()}'"
        return self._fetch_all_paginated(BUILDINGS_ENDPOINT, where=where_clause)
    
    def fetch_contacts_for_registrations(self, registration_ids: List[str]) -> List[dict]:
        """
        Fetch contacts for specific registration IDs.
        
        Args:
            registration_ids: List of registration IDs to fetch contacts for
            
        Returns:
            List of contact records
        """
        if not registration_ids:
            return []
        
        # Socrata $where with IN clause
        # Process in batches to avoid URL length limits
        all_contacts = []
        batch_size = 100
        
        for i in range(0, len(registration_ids), batch_size):
            batch = registration_ids[i:i + batch_size]
            ids_str = ",".join(f"'{rid}'" for rid in batch)
            where_clause = f"registrationid in ({ids_str})"
            contacts = self._fetch_all_paginated(CONTACTS_ENDPOINT, where=where_clause)
            all_contacts.extend(contacts)
        
        return all_contacts
    
    def _fetch_all_paginated(
        self, 
        endpoint: str, 
        limit: Optional[int] = None,
        where: Optional[str] = None
    ) -> List[dict]:
        """
        Fetch all records from an endpoint with pagination.
        
        Args:
            endpoint: Socrata API endpoint URL
            limit: Maximum total records to fetch
            where: Optional $where filter clause
            
        Returns:
            List of all records
        """
        all_records = []
        offset = 0
        page_size = min(PAGE_SIZE, limit) if limit else PAGE_SIZE
        
        while True:
            # Check if we've hit the limit
            if limit and len(all_records) >= limit:
                break
            
            # Adjust page size for final page
            if limit:
                remaining = limit - len(all_records)
                page_size = min(PAGE_SIZE, remaining)
            
            # Fetch page
            page = self._fetch_page(endpoint, offset=offset, limit=page_size, where=where)
            
            if not page:
                # No more records
                break
            
            all_records.extend(page)
            logger.debug(f"Fetched {len(all_records)} records so far...")
            
            if len(page) < page_size:
                # Last page (partial)
                break
            
            offset += len(page)
            
            # Small delay to be nice to the API
            time.sleep(0.1)
        
        logger.info(f"Fetched {len(all_records)} total records from {endpoint.split('/')[-1]}")
        return all_records
    
    def _fetch_page(
        self, 
        endpoint: str,
        offset: int = 0, 
        limit: int = PAGE_SIZE,
        where: Optional[str] = None
    ) -> List[dict]:
        """
        Fetch a single page of results.
        
        Args:
            endpoint: Socrata API endpoint URL
            offset: Number of records to skip
            limit: Number of records to return
            where: Optional $where filter clause
            
        Returns:
            List of records for this page
        """
        params = {
            "$offset": offset,
            "$limit": limit,
            "$order": "registrationid",
        }
        
        if where:
            params["$where"] = where
        
        for attempt in range(settings.api_retry_attempts):
            try:
                response = self.session.get(endpoint, params=params, timeout=30)
                response.raise_for_status()
                return response.json()
            except requests.RequestException as e:
                if attempt < settings.api_retry_attempts - 1:
                    wait_time = settings.api_retry_delay_seconds * (2 ** attempt)
                    logger.warning(f"Request failed, retrying in {wait_time}s: {e}")
                    time.sleep(wait_time)
                else:
                    logger.error(f"Request failed after {settings.api_retry_attempts} attempts: {e}")
                    raise
        
        return []
    
    def get_combined_data(self, building_limit: Optional[int] = None) -> List[Dict]:
        """
        Fetch buildings and their contacts, combining into enriched records.
        
        This is the main method for getting lead data.
        
        Args:
            building_limit: Maximum buildings to fetch
            
        Returns:
            List of building records with contacts attached
        """
        # Fetch buildings
        logger.info("Fetching buildings...")
        buildings = self.fetch_all_buildings(limit=building_limit)
        
        # Get unique registration IDs
        reg_ids = list(set(b.get("registrationid") for b in buildings if b.get("registrationid")))
        logger.info(f"Found {len(reg_ids)} unique registration IDs")
        
        # Fetch contacts for these registrations
        logger.info("Fetching contacts...")
        contacts = self.fetch_contacts_for_registrations(reg_ids)
        
        # Group contacts by registration ID
        contacts_by_reg = {}
        for contact in contacts:
            reg_id = contact.get("registrationid")
            if reg_id:
                if reg_id not in contacts_by_reg:
                    contacts_by_reg[reg_id] = []
                contacts_by_reg[reg_id].append(contact)
        
        # Combine buildings with their contacts
        for building in buildings:
            reg_id = building.get("registrationid")
            building["contacts"] = contacts_by_reg.get(reg_id, [])
        
        logger.info(f"Combined {len(buildings)} buildings with contact data")
        return buildings


# Quick test
if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(__file__).rsplit("src", 1)[0])
    
    logging.basicConfig(level=logging.INFO)
    
    client = HPDClient()
    
    # Test fetching a few buildings
    print("Fetching 5 buildings...")
    buildings = client.fetch_all_buildings(limit=5)
    print(f"Got {len(buildings)} buildings")
    if buildings:
        print(f"Sample building fields: {list(buildings[0].keys())}")
        print(f"Sample: {buildings[0]}")
    
    # Test fetching contacts
    print("\nFetching 5 contacts...")
    contacts = client.fetch_all_contacts(limit=5)
    print(f"Got {len(contacts)} contacts")
    if contacts:
        print(f"Sample contact fields: {list(contacts[0].keys())}")
        print(f"Sample: {contacts[0]}")
    
    # Test combined data
    print("\nFetching combined data (3 buildings)...")
    combined = client.get_combined_data(building_limit=3)
    for b in combined:
        print(f"Building {b['registrationid']}: {len(b['contacts'])} contacts")
