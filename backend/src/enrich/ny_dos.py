"""
NY DOS Corporation Database lookup.

See docs/01-data-sources.md and docs/04-enrichment-strategy.md for details.

Data source: https://data.ny.gov/Economic-Development/Active-Corporations-Beginning-1800/7tqb-y2d4
"""
import logging
import re
import time
from typing import Optional, Dict, List
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

# NY State Open Data endpoint for Active Corporations
# Updated endpoint as of Feb 2026 (old endpoint 7tqb-y2d4 is no longer available)
NY_DOS_ENDPOINT = "https://data.ny.gov/resource/n9v6-gdp6.json"


@dataclass
class DOSEntity:
    """NY DOS business entity record."""
    dos_id: str
    name: str
    entity_type: str  # DOMESTIC LIMITED LIABILITY COMPANY, DOMESTIC BUSINESS CORPORATION, etc.
    status: str  # ACTIVE, INACTIVE
    jurisdiction: str
    formation_date: Optional[str]
    county: Optional[str]
    process_name: Optional[str]  # Registered agent name
    process_address: Optional[str]  # Registered agent address


class NYDOSClient:
    """Client for NY Department of State corporation lookups."""
    
    def __init__(self, app_token: Optional[str] = None):
        self.session = requests.Session()
        self.app_token = app_token
        if app_token:
            self.session.headers['X-App-Token'] = app_token
        
        # In-memory cache as first layer (fast)
        self._cache: Dict[str, Optional[DOSEntity]] = {}
        
        # SQLite-backed persistent cache (survives restarts)
        self._db = None
        try:
            from src.storage.database import get_database
            self._db = get_database()
        except Exception:
            pass  # Gracefully degrade without persistent cache
    
    def _normalize_name(self, name: str) -> str:
        """Normalize company name for search."""
        # Uppercase
        name = name.upper().strip()
        
        # Remove common suffixes for broader matching
        suffixes = [
            r'\s+LLC$', r'\s+L\.L\.C\.$', r'\s+LIMITED LIABILITY COMPANY$',
            r'\s+INC\.?$', r'\s+INCORPORATED$', r'\s+CORP\.?$', r'\s+CORPORATION$',
            r'\s+LP$', r'\s+L\.P\.$', r'\s+LIMITED PARTNERSHIP$',
            r'\s+LLP$', r'\s+L\.L\.P\.$',
            r'\s+CO\.?$', r'\s+COMPANY$',
            r',?\s+THE$', r'^THE\s+',
        ]
        
        for suffix in suffixes:
            name = re.sub(suffix, '', name)
        
        return name.strip()
    
    def _escape_soql(self, value: str) -> str:
        """Escape a string for use in SoQL queries to prevent injection."""
        # Escape single quotes by doubling them (SoQL standard)
        return value.replace("'", "''")
    
    def lookup_entity(self, name: str, include_inactive: bool = False) -> Optional[DOSEntity]:
        """
        Look up a business entity by name.
        
        Args:
            name: Entity name to search (LLC, Corp, etc.)
            include_inactive: Whether to include inactive entities
            
        Returns:
            Best matching DOSEntity or None
        """
        cache_key = f"{name}:{include_inactive}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        # Check persistent SQLite cache
        if self._db and self._db.has_dos_cache(cache_key):
            cached = self._db.get_dos_cache(cache_key)
            if cached:
                entity = DOSEntity(**cached)
                self._cache[cache_key] = entity
                return entity
            else:
                self._cache[cache_key] = None
                return None
        
        normalized = self._normalize_name(name)
        if not normalized:
            return None
        
        try:
            # Search using SoQL (Socrata Query Language)
            # Use LIKE for partial matching
            # Note: New endpoint (n9v6-gdp6) only contains active corporations,
            # so no need to filter by status
            # Escape the normalized name to prevent SQL injection
            escaped_normalized = self._escape_soql(normalized)
            params = {
                "$where": f"upper(current_entity_name) LIKE '%{escaped_normalized}%'",
                "$limit": 20,
                "$order": "initial_dos_filing_date DESC"  # Most recent first
            }
            
            response = self.session.get(NY_DOS_ENDPOINT, params=params, timeout=15)
            response.raise_for_status()
            results = response.json()
            
            if not results:
                # Try exact match with original name
                escaped_name = self._escape_soql(name.upper())
                params["$where"] = f"upper(current_entity_name) = '{escaped_name}'"
                
                response = self.session.get(NY_DOS_ENDPOINT, params=params, timeout=15)
                response.raise_for_status()
                results = response.json()
            
            if not results:
                self._cache[cache_key] = None
                # Persist negative result to SQLite
                if self._db:
                    self._db.set_dos_cache(cache_key, None)
                return None
            
            # Find best match (prefer exact match, then by recency)
            best_match = None
            name_upper = name.upper()
            
            for r in results:
                entity = self._parse_entity(r)
                if entity.name == name_upper:
                    best_match = entity
                    break
                if best_match is None:
                    best_match = entity
            
            self._cache[cache_key] = best_match
            # Persist to SQLite
            if self._db and best_match:
                from dataclasses import asdict
                self._db.set_dos_cache(cache_key, asdict(best_match))
            elif self._db:
                self._db.set_dos_cache(cache_key, None)
            return best_match
            
        except requests.RequestException as e:
            logger.error(f"NY DOS lookup failed for '{name}': {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error in NY DOS lookup: {e}")
            return None
    
    def _parse_entity(self, record: Dict) -> DOSEntity:
        """Parse API response into DOSEntity."""
        # Build process address from components
        # Note: New API uses dos_process_address_1 instead of dos_process_addr1
        process_addr_parts = []
        for field in ['dos_process_address_1', 'dos_process_address_2', 'dos_process_city', 'dos_process_state', 'dos_process_zip']:
            if record.get(field):
                process_addr_parts.append(record[field])
        
        return DOSEntity(
            dos_id=record.get('dos_id', ''),
            name=record.get('current_entity_name', ''),
            entity_type=record.get('entity_type', ''),  # Changed from current_entity_type
            status='ACTIVE',  # New endpoint only has active corporations
            jurisdiction=record.get('jurisdiction', ''),
            formation_date=record.get('initial_dos_filing_date'),  # Changed from dos_file_date
            county=record.get('county'),
            process_name=record.get('dos_process_name'),
            process_address=', '.join(process_addr_parts) if process_addr_parts else None
        )
    
    def search_entities(self, name: str, limit: int = 10) -> List[DOSEntity]:
        """
        Search for entities matching a name (returns multiple results).
        
        Args:
            name: Search term
            limit: Max results
            
        Returns:
            List of matching DOSEntity records
        """
        normalized = self._normalize_name(name)
        if not normalized:
            return []
        
        try:
            escaped_normalized = self._escape_soql(normalized)
            params = {
                "$where": f"upper(current_entity_name) LIKE '%{escaped_normalized}%'",
                "$limit": limit,
                "$order": "initial_dos_filing_date DESC"
            }
            
            response = self.session.get(NY_DOS_ENDPOINT, params=params, timeout=15)
            response.raise_for_status()
            results = response.json()
            
            return [self._parse_entity(r) for r in results]
            
        except Exception as e:
            logger.error(f"NY DOS search failed: {e}")
            return []
    
    def get_registered_agent(self, dos_id: str) -> Optional[str]:
        """
        Get the registered agent for an entity by DOS ID.
        
        Args:
            dos_id: NY DOS entity ID
            
        Returns:
            Registered agent name or None
        """
        try:
            params = {
                "$where": f"dos_id = '{dos_id}'",
                "$limit": 1
            }
            
            response = self.session.get(NY_DOS_ENDPOINT, params=params, timeout=15)
            response.raise_for_status()
            results = response.json()
            
            if results:
                return results[0].get('dos_process_name')
            return None
            
        except Exception as e:
            logger.error(f"Failed to get registered agent for {dos_id}: {e}")
            return None
