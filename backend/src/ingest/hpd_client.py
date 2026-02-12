"""
HPD Multiple Dwelling Registrations API client.

See docs/01-data-sources.md for API details.

Two datasets are used:
1. Multiple Dwelling Registrations (tesw-yqqr) - Building info
2. Registration Contacts (feu5-w2e2) - Owner/agent contact info
"""
import logging
import os
import time
from datetime import date
from typing import List, Optional, Dict

import requests

from config.settings import settings

logger = logging.getLogger(__name__)

# NYC Open Data Socrata endpoints — R7: configurable via env vars
_HPD_BUILDINGS_DATASET = os.environ.get("HPD_BUILDINGS_DATASET_ID", "tesw-yqqr")
_HPD_CONTACTS_DATASET = os.environ.get("HPD_CONTACTS_DATASET_ID", "feu5-w2e2")
BUILDINGS_ENDPOINT = f"https://data.cityofnewyork.us/resource/{_HPD_BUILDINGS_DATASET}.json"
CONTACTS_ENDPOINT = f"https://data.cityofnewyork.us/resource/{_HPD_CONTACTS_DATASET}.json"

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
    
    def get_total_count(self, dataset: str = 'buildings') -> int:
        """
        Query Socrata for the total record count in a dataset.
        Used for pre-flight completeness checks before a refresh.
        
        Args:
            dataset: 'buildings' or 'contacts'
            
        Returns:
            Total record count, or 0 if the query fails
        """
        endpoint = BUILDINGS_ENDPOINT if dataset == 'buildings' else CONTACTS_ENDPOINT
        try:
            params = {"$select": "COUNT(*) as cnt"}
            response = self.session.get(endpoint, params=params, timeout=15)
            response.raise_for_status()
            rows = response.json()
            count = int(rows[0]["cnt"]) if rows else 0
            logger.info(f"HPD {dataset} total count from Socrata: {count}")
            return count
        except Exception as e:
            logger.warning(f"Failed to get {dataset} count from Socrata: {e}")
            return 0
    
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
        consecutive_empty = 0  # Track consecutive empty pages to detect real end vs transient errors
        
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
                consecutive_empty += 1
                if consecutive_empty >= 2:
                    # Two consecutive empty pages = real end of data
                    logger.info(f"Confirmed end of data after {consecutive_empty} consecutive empty pages at offset {offset}")
                    break
                else:
                    # Could be transient — retry same offset once after a brief pause
                    logger.warning(f"Empty page at offset {offset}, retrying once...")
                    time.sleep(1.0)
                    continue
            
            consecutive_empty = 0  # Reset on successful page
            all_records.extend(page)
            logger.debug(f"Fetched {len(all_records)} records so far...")
            
            if len(page) < page_size:
                # Partial page — likely the last page, but verify with one more request
                logger.debug(f"Partial page ({len(page)}/{page_size}) at offset {offset}, verifying end...")
                offset += len(page)
                verify_page = self._fetch_page(endpoint, offset=offset, limit=page_size, where=where)
                if verify_page:
                    # Not actually the end — keep going
                    all_records.extend(verify_page)
                    offset += len(verify_page)
                    logger.info(f"Partial page was not end of data, continuing from offset {offset}")
                    time.sleep(0.1)
                    continue
                else:
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
                
                # Handle rate limiting (HTTP 429) explicitly
                if response.status_code == 429:
                    retry_after = response.headers.get('Retry-After')
                    if retry_after:
                        wait_time = int(retry_after)
                    else:
                        wait_time = settings.api_retry_delay_seconds * (2 ** attempt)
                    logger.warning(f"Rate limited (429), waiting {wait_time}s before retry (attempt {attempt + 1})")
                    time.sleep(wait_time)
                    continue
                
                response.raise_for_status()
                return response.json()
            except requests.RequestException as e:
                if attempt < settings.api_retry_attempts - 1:
                    wait_time = settings.api_retry_delay_seconds * (2 ** attempt)
                    logger.warning(f"Request failed (attempt {attempt + 1}/{settings.api_retry_attempts}), retrying in {wait_time}s: {e}")
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

    def stream_buildings_with_contacts(
        self, 
        chunk_size: int = 25000,
        total_limit: Optional[int] = None,
        progress_callback: Optional[callable] = None
    ):
        """
        Generator that yields buildings with contacts in memory-efficient chunks.
        
        This is the recommended method for processing large datasets.
        
        Args:
            chunk_size: Number of buildings per chunk
            total_limit: Maximum total buildings (None for all)
            progress_callback: Optional callback(buildings_fetched, total_estimated)
            
        Yields:
            List[Dict]: Chunks of building records with contacts attached
        """
        offset = 0
        total_fetched = 0
        
        # First, get total count estimate
        logger.info("Estimating total building count...")
        first_page = self._fetch_page(BUILDINGS_ENDPOINT, offset=0, limit=1)
        # We'll discover total as we go
        
        while True:
            if total_limit and total_fetched >= total_limit:
                break
            
            # Calculate this chunk's limit
            this_chunk_limit = chunk_size
            if total_limit:
                remaining = total_limit - total_fetched
                this_chunk_limit = min(chunk_size, remaining)
            
            logger.info(f"Fetching buildings chunk: offset={offset}, limit={this_chunk_limit}")
            
            # Fetch buildings for this chunk using pagination
            chunk_buildings = []
            chunk_offset = offset
            while len(chunk_buildings) < this_chunk_limit:
                page_limit = min(PAGE_SIZE, this_chunk_limit - len(chunk_buildings))
                page = self._fetch_page(BUILDINGS_ENDPOINT, offset=chunk_offset, limit=page_limit)
                
                if not page:
                    break
                    
                chunk_buildings.extend(page)
                chunk_offset += len(page)
                
                if len(page) < page_limit:
                    # No more data
                    break
            
            if not chunk_buildings:
                logger.info("No more buildings to fetch")
                break
            
            logger.info(f"Fetched {len(chunk_buildings)} buildings in this chunk")
            
            # Get unique registration IDs for this chunk
            reg_ids = list(set(b.get("registrationid") for b in chunk_buildings if b.get("registrationid")))
            logger.info(f"Fetching contacts for {len(reg_ids)} registrations...")
            
            # Fetch contacts for these registrations
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
            for building in chunk_buildings:
                reg_id = building.get("registrationid")
                building["contacts"] = contacts_by_reg.get(reg_id, [])
            
            total_fetched += len(chunk_buildings)
            offset += len(chunk_buildings)
            
            if progress_callback:
                progress_callback(total_fetched, total_limit or total_fetched)
            
            logger.info(f"Yielding chunk of {len(chunk_buildings)} buildings (total: {total_fetched})")
            yield chunk_buildings
            
            # Free memory
            del contacts
            del contacts_by_reg
            
            # Check if we got less than requested (means we're done)
            if len(chunk_buildings) < this_chunk_limit:
                logger.info("Reached end of buildings data")
                break
        
        logger.info(f"Streaming complete: {total_fetched} total buildings processed")


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
