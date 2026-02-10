"""
HPD Violations data ingestion.

Fetches violation data from NYC Open Data and aggregates per building/lead.
Violations are a distress signal -- moderate violations indicate motivated sellers,
extreme violations indicate operational risk.

Data source: https://data.cityofnewyork.us/resource/wvxf-dwi5.json
"""
import logging
from typing import Dict, List, Optional
from collections import defaultdict

import requests

logger = logging.getLogger(__name__)

HPD_VIOLATIONS_ENDPOINT = "https://data.cityofnewyork.us/resource/wvxf-dwi5.json"

# Socrata app token (optional, increases rate limit)
SOCRATA_APP_TOKEN = None


class HPDViolationsClient:
    """Client for HPD Violations Open Data API."""
    
    def __init__(self, app_token: Optional[str] = None):
        self.session = requests.Session()
        self.app_token = app_token or SOCRATA_APP_TOKEN
        if self.app_token:
            self.session.headers['X-App-Token'] = self.app_token
    
    def fetch_violations_for_buildings(
        self, 
        building_ids: List[str],
        batch_size: int = 50,
    ) -> Dict[str, Dict]:
        """
        Fetch violation counts for a set of buildings.
        
        Args:
            building_ids: List of HPD BuildingID values
            batch_size: How many buildings to query at once
            
        Returns:
            Dict keyed by BuildingID with violation counts:
            {
                "12345": {
                    "total": 47,
                    "class_a": 5,
                    "class_b": 30,
                    "class_c": 12,
                    "open": 15,
                }
            }
        """
        all_results = {}
        
        for i in range(0, len(building_ids), batch_size):
            batch = building_ids[i:i + batch_size]
            batch_results = self._fetch_batch(batch)
            all_results.update(batch_results)
        
        return all_results
    
    def _fetch_batch(self, building_ids: List[str]) -> Dict[str, Dict]:
        """Fetch violations for a batch of building IDs."""
        if not building_ids:
            return {}
        
        # Build SoQL WHERE clause
        id_list = ",".join(f"'{bid}'" for bid in building_ids)
        
        try:
            # Get violation counts grouped by building and class
            params = {
                "$select": "buildingid, class, currentstatus, COUNT(*) as cnt",
                "$where": f"buildingid IN ({id_list})",
                "$group": "buildingid, class, currentstatus",
                "$limit": 50000,
            }
            
            response = self.session.get(
                HPD_VIOLATIONS_ENDPOINT, params=params, timeout=30
            )
            response.raise_for_status()
            rows = response.json()
            
            # Aggregate results
            results = defaultdict(lambda: {
                "total": 0, "class_a": 0, "class_b": 0, "class_c": 0, "open": 0
            })
            
            for row in rows:
                bid = str(row.get("buildingid", ""))
                vclass = (row.get("class") or "").upper()
                status = (row.get("currentstatus") or "").upper()
                count = int(row.get("cnt", 0))
                
                results[bid]["total"] += count
                
                if vclass == "A":
                    results[bid]["class_a"] += count
                elif vclass == "B":
                    results[bid]["class_b"] += count
                elif vclass == "C":
                    results[bid]["class_c"] += count
                
                if status == "OPEN":
                    results[bid]["open"] += count
            
            return dict(results)
            
        except requests.RequestException as e:
            logger.error(f"HPD Violations API error: {e}")
            return {}
        except Exception as e:
            logger.error(f"Unexpected error fetching violations: {e}")
            return {}
    
    def fetch_violations_summary_for_borough(
        self, 
        borough: str,
        limit: int = 10000,
    ) -> Dict[str, Dict]:
        """
        Fetch violation summaries for an entire borough.
        
        More efficient than per-building queries for bulk operations.
        """
        boro_map = {
            "MANHATTAN": "MANHATTAN",
            "BROOKLYN": "BROOKLYN", 
            "BRONX": "BRONX",
            "QUEENS": "QUEENS",
            "STATEN ISLAND": "STATEN ISLAND",
        }
        
        boro_name = boro_map.get(borough.upper(), borough.upper())
        
        try:
            params = {
                "$select": "buildingid, class, COUNT(*) as cnt",
                "$where": f"upper(boroid_boro) = '{boro_name}' OR upper(boro) = '{boro_name}'",
                "$group": "buildingid, class",
                "$limit": limit,
            }
            
            response = self.session.get(
                HPD_VIOLATIONS_ENDPOINT, params=params, timeout=60
            )
            response.raise_for_status()
            rows = response.json()
            
            results = defaultdict(lambda: {
                "total": 0, "class_a": 0, "class_b": 0, "class_c": 0
            })
            
            for row in rows:
                bid = str(row.get("buildingid", ""))
                vclass = (row.get("class") or "").upper()
                count = int(row.get("cnt", 0))
                
                results[bid]["total"] += count
                if vclass == "A":
                    results[bid]["class_a"] += count
                elif vclass == "B":
                    results[bid]["class_b"] += count
                elif vclass == "C":
                    results[bid]["class_c"] += count
            
            logger.info(f"Fetched violations for {len(results)} buildings in {boro_name}")
            return dict(results)
            
        except Exception as e:
            logger.error(f"Failed to fetch violations for {boro_name}: {e}")
            return {}


def aggregate_violations_for_lead(
    building_ids: List[str],
    violations_data: Dict[str, Dict],
    total_units: int = 0,
) -> Dict:
    """
    Aggregate violation data from multiple buildings into lead-level stats.
    
    Args:
        building_ids: Building IDs for this lead
        violations_data: Pre-fetched violations keyed by building ID
        total_units: Total units for per-unit calculation
        
    Returns:
        Dict with violation_count, class_a/b/c, violations_per_unit
    """
    total = 0
    class_a = 0
    class_b = 0
    class_c = 0
    
    for bid in building_ids:
        v = violations_data.get(str(bid), {})
        total += v.get("total", 0)
        class_a += v.get("class_a", 0)
        class_b += v.get("class_b", 0)
        class_c += v.get("class_c", 0)
    
    per_unit = round(total / total_units, 2) if total_units > 0 else 0.0
    
    return {
        "violation_count": total,
        "violation_class_a": class_a,
        "violation_class_b": class_b,
        "violation_class_c": class_c,
        "violations_per_unit": per_unit,
    }
