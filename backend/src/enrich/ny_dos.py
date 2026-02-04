"""
NY DOS Corporation Database lookup.

See docs/01-data-sources.md and docs/04-enrichment-strategy.md for details.
"""
import logging
from typing import Optional, Dict

import requests

logger = logging.getLogger(__name__)

# NY State Open Data endpoint
NY_DOS_ENDPOINT = "https://data.ny.gov/resource/7tqb-y2d4.json"


class NYDOSClient:
    """Client for NY Department of State corporation lookups."""
    
    def __init__(self):
        self.session = requests.Session()
    
    def lookup_entity(self, name: str) -> Optional[Dict]:
        """
        Look up a business entity by name.
        
        Args:
            name: Entity name to search (LLC, Corp, etc.)
            
        Returns:
            Entity record or None
        """
        # TODO: Implement fuzzy search
        # - Normalize name for search
        # - Filter for active entities
        # - Return best match
        raise NotImplementedError("Implement in next sprint")
    
    def get_registered_agent(self, dos_id: str) -> Optional[str]:
        """
        Get the registered agent for an entity.
        
        Args:
            dos_id: NY DOS entity ID
            
        Returns:
            Registered agent name or None
        """
        # TODO: Implement registered agent lookup
        raise NotImplementedError("Implement in next sprint")
