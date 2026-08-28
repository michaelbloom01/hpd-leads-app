"""
NY DOS Corporation Database lookup.

See docs/01-data-sources.md and docs/04-enrichment-strategy.md for details.

Data source: https://data.ny.gov/Economic-Development/Active-Corporations-Beginning-1800/7tqb-y2d4
"""
import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# NY State Open Data endpoint for Active Corporations
# Updated endpoint as of Feb 2026 (old endpoint 7tqb-y2d4 is no longer available)
NY_DOS_ENDPOINT = "https://data.ny.gov/resource/n9v6-gdp6.json"
NY_DOS_FILINGS_ADDR_ENDPOINT = "https://data.ny.gov/resource/2tms-hftb.json"


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
    ceo_name: Optional[str] = None
    ceo_address: Optional[str] = None


class NYDOSClient:
    """Client for NY Department of State corporation lookups."""
    
    def __init__(self, app_token: Optional[str] = None):
        self.session = requests.Session()
        self.app_token = app_token
        if app_token:
            self.session.headers['X-App-Token'] = app_token
        
        # In-memory cache as first layer (fast)
        self._cache: Dict[str, Optional[DOSEntity]] = {}
        
    
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

    @staticmethod
    def _canonical_exact_name(value: str | None) -> str:
        """Canonical key for punctuation-tolerant, whole-name equality."""
        normalized = re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9]+", " ", (value or "").upper())).strip()
        return re.sub(r"\bOWNER S\b", "OWNERS", normalized)

    @classmethod
    def _exact_query_variants(cls, value: str) -> list[str]:
        """Generate punctuation-only legal-suffix variants for exact API queries."""
        original = str(value or "").strip().upper()
        canonical = cls._canonical_exact_name(original)
        tokens = canonical.split()
        variants = {original, canonical}
        if tokens and tokens[-1] in {"INC", "CORP", "LLC", "LTD", "CO"}:
            suffix = tokens[-1]
            base = " ".join(tokens[:-1])
            base_variants = {base}
            if re.search(r"\bOWNERS\b", base):
                base_variants.add(re.sub(r"\bOWNERS\b", "OWNER'S", base))
            variants.update({
                variant
                for base_variant in base_variants
                for variant in (
                    f"{base_variant} {suffix}",
                    f"{base_variant} {suffix}.",
                    f"{base_variant}, {suffix}",
                    f"{base_variant}, {suffix}.",
                )
            })
        return sorted(variant for variant in variants if variant)

    def lookup_entities_exact(
        self,
        names: list[str],
        *,
        batch_size: int = 40,
    ) -> dict[str, list[DOSEntity]]:
        """Fetch case-insensitive exact-name matches in batches.

        Results are keyed by a punctuation-tolerant canonical name. The API query
        remains whole-name exact, which avoids upgrading substring candidates.
        """
        unique_names = list(dict.fromkeys(
            str(name).strip().upper()
            for name in names
            if str(name or "").strip()
        ))
        grouped: dict[str, list[DOSEntity]] = {
            self._canonical_exact_name(name): [] for name in unique_names
        }
        safe_batch_size = max(1, min(int(batch_size or 1), 100))

        query_names = list(dict.fromkeys(
            variant
            for name in unique_names
            for variant in self._exact_query_variants(name)
        ))
        for offset in range(0, len(query_names), safe_batch_size):
            batch = query_names[offset:offset + safe_batch_size]
            quoted = ",".join(f"'{self._escape_soql(name)}'" for name in batch)
            params = {
                "$where": f"upper(current_entity_name) IN ({quoted})",
                "$limit": max(100, len(batch) * 5),
            }
            response = self.session.get(NY_DOS_ENDPOINT, params=params, timeout=(15, 60))
            response.raise_for_status()
            for record in response.json():
                entity = self._parse_entity(record)
                key = self._canonical_exact_name(entity.name)
                if key in grouped:
                    grouped[key].append(entity)

        return grouped

    def lookup_chairmen_for_exact_names(
        self,
        names: list[str],
        *,
        page_size: int = 50000,
    ) -> dict[str, list[DOSEntity]]:
        """Scan the chairman-bearing DOS subset once, then exact-match locally."""
        target_keys = {
            self._canonical_exact_name(name)
            for name in names
            if self._canonical_exact_name(name)
        }
        grouped: dict[str, list[DOSEntity]] = {key: [] for key in target_keys}
        safe_page_size = max(1000, min(int(page_size or 1000), 50000))
        offset = 0
        select_fields = (
            "dos_id,current_entity_name,entity_type,jurisdiction,initial_dos_filing_date,county,"
            "chairman_name,chairman_address_1,chairman_address_2,chairman_city,chairman_state,chairman_zip"
        )

        while True:
            params = {
                "$select": select_fields,
                "$where": "chairman_name IS NOT NULL AND TRIM(chairman_name) != ''",
                "$order": "dos_id",
                "$limit": safe_page_size,
                "$offset": offset,
            }
            response = self.session.get(NY_DOS_ENDPOINT, params=params, timeout=(30, 120))
            response.raise_for_status()
            records = response.json()
            for record in records:
                key = self._canonical_exact_name(record.get("current_entity_name"))
                if key in grouped:
                    grouped[key].append(self._parse_entity(record))
            if len(records) < safe_page_size:
                break
            offset += safe_page_size

        return grouped
    
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
        
        ceo_addr_parts = []
        for field in ['chairman_address_1', 'chairman_address_2', 'chairman_city', 'chairman_state', 'chairman_zip']:
            if record.get(field):
                ceo_addr_parts.append(record[field])
        
        return DOSEntity(
            dos_id=record.get('dos_id', ''),
            name=record.get('current_entity_name', ''),
            entity_type=record.get('entity_type', ''),  # Changed from current_entity_type
            status='ACTIVE',  # New endpoint only has active corporations
            jurisdiction=record.get('jurisdiction', ''),
            formation_date=record.get('initial_dos_filing_date'),  # Changed from dos_file_date
            county=record.get('county'),
            process_name=record.get('dos_process_name'),
            process_address=', '.join(process_addr_parts) if process_addr_parts else None,
            ceo_name=record.get('chairman_name'),
            ceo_address=', '.join(ceo_addr_parts) if ceo_addr_parts else None,
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

    def get_filing_officers(self, dos_id: str) -> list[dict]:
        """Fetch all addr_type=3 officers from the most recent DOS filing for this entity.
        
        Uses the NY DOS All Filings Address dataset (2tms-hftb).
        Returns list of dicts with name, address, city, state, zip, filing_date, filing_num.
        """
        try:
            escaped_id = self._escape_soql(dos_id)
            params = {
                "$where": f"corpid_num='{escaped_id}' AND addr_type='3'",
                "$order": "date_filed DESC",
                "$limit": 50,
            }
            response = self.session.get(NY_DOS_FILINGS_ADDR_ENDPOINT, params=params, timeout=15)
            response.raise_for_status()
            results = response.json()
            
            if not results:
                return []
            
            most_recent_film = results[0].get('film_num')
            officers = []
            for r in results:
                if r.get('film_num') != most_recent_film:
                    break
                
                addr_parts = [r.get('addr1', ''), r.get('addr2', '')]
                addr_str = ', '.join(p for p in addr_parts if p and p.strip())
                
                officers.append({
                    'name': r.get('name', '').strip(),
                    'address': addr_str,
                    'city': r.get('city', '').strip(),
                    'state': r.get('state', '').strip(),
                    'zip': r.get('zip5', '').strip(),
                    'filing_date': r.get('date_filed'),
                    'filing_num': r.get('film_num'),
                })
            
            return officers
        except requests.RequestException as e:
            logger.error(f"DOS filing officers lookup failed for DOS ID {dos_id}: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error in DOS filing officers lookup: {e}")
            return []

    def search_corporation(self, name: str, limit: int = 10) -> List[DOSEntity]:
        """Backward-compatible alias for legacy API naming."""
        return self.search_entities(name, limit=limit)
    
    def get_registered_agent(self, dos_id: str) -> Optional[str]:
        """
        Get the registered agent for an entity by DOS ID.
        
        Args:
            dos_id: NY DOS entity ID
            
        Returns:
            Registered agent name or None
        """
        try:
            escaped_id = self._escape_soql(dos_id)
            params = {
                "$where": f"dos_id = '{escaped_id}'",
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
