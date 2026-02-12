"""
PLUTO (Primary Land Use Tax Lot Output) API client.

Fetches building classification data from NYC PLUTO dataset.
Used to classify buildings as condo, coop, rental, etc.

Building class codes:
- R1-R9: Condominiums
- C6, C8, D0, D4: Coops
- C0-C5, C7, C9: Walk-up rentals (non-coop)
- D1, D3, D5-D9: Elevator rentals (non-coop)
- A1-A9: One-family dwellings
- B1-B9: Two-family dwellings
"""
import logging
import os
import time
from typing import Dict, List, Optional, Set
from dataclasses import dataclass
from collections import defaultdict

import requests

logger = logging.getLogger(__name__)

# NYC Open Data PLUTO endpoint — R7: configurable via env var
_PLUTO_DATASET = os.environ.get("PLUTO_DATASET_ID", "64uk-42ks")
PLUTO_ENDPOINT = f"https://data.cityofnewyork.us/resource/{_PLUTO_DATASET}.json"


@dataclass
class BuildingClassification:
    """Classification data for a single building from PLUTO."""
    bbl: str
    borough: str
    block: str
    lot: str
    building_class: str
    building_type: str  # Derived: condo, coop, rental_elevator, rental_walkup, small_residential
    land_use: str
    units_res: int
    units_total: int
    year_built: Optional[int]
    address: Optional[str]


# Building class to type mapping
CONDO_CLASSES = {'R0', 'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7', 'R8', 'R9', 'RR'}
COOP_CLASSES = {'C6', 'C8', 'D0', 'D4'}  # Walk-up and elevator coops
ELEVATOR_RENTAL_CLASSES = {'D1', 'D2', 'D3', 'D5', 'D6', 'D7', 'D8', 'D9'}
WALKUP_RENTAL_CLASSES = {'C0', 'C1', 'C2', 'C3', 'C4', 'C5', 'C7', 'C9'}
SMALL_RESIDENTIAL_CLASSES = set(f'{letter}{num}' for letter in 'AB' for num in range(10))


def classify_building(building_class: str) -> str:
    """
    Classify a building based on its NYC building class code.
    
    Args:
        building_class: Two-character building class code (e.g., 'D4', 'R1')
        
    Returns:
        Building type: 'condo', 'coop', 'rental_elevator', 'rental_walkup', 
                      'small_residential', or 'other'
    """
    if not building_class:
        return 'unknown'
    
    bc = building_class.upper().strip()
    
    if bc in CONDO_CLASSES:
        return 'condo'
    elif bc in COOP_CLASSES:
        return 'coop'
    elif bc in ELEVATOR_RENTAL_CLASSES:
        return 'rental_elevator'
    elif bc in WALKUP_RENTAL_CLASSES:
        return 'rental_walkup'
    elif bc in SMALL_RESIDENTIAL_CLASSES:
        return 'small_residential'
    else:
        return 'other'


class PLUTOClient:
    """Client for fetching PLUTO building classification data."""
    
    def __init__(self, app_token: Optional[str] = None):
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/json"
        if app_token:
            self.session.headers["X-App-Token"] = app_token
        
        # Cache to avoid repeated lookups
        self._cache: Dict[str, BuildingClassification] = {}
        # Error tracking for audit logging
        self._batch_errors = 0
        self._single_errors = 0
    
    def make_bbl(self, boro_id: str, block: str, lot: str) -> str:
        """
        Construct BBL (Borough-Block-Lot) identifier.
        
        Args:
            boro_id: Borough ID (1=Manhattan, 2=Bronx, 3=Brooklyn, 4=Queens, 5=SI)
            block: Tax block number
            lot: Tax lot number
            
        Returns:
            10-digit BBL string
        """
        # BBL format: B BBBBB LLLL (1 digit boro, 5 digits block, 4 digits lot)
        boro = str(boro_id).zfill(1)
        blk = str(block).zfill(5)
        lt = str(lot).zfill(4)
        return f"{boro}{blk}{lt}"
    
    def get_classification(self, boro_id: str, block: str, lot: str) -> Optional[BuildingClassification]:
        """
        Get building classification for a single property.
        
        Args:
            boro_id: Borough ID
            block: Tax block
            lot: Tax lot
            
        Returns:
            BuildingClassification or None if not found
        """
        bbl = self.make_bbl(boro_id, block, lot)
        
        # Check cache
        if bbl in self._cache:
            return self._cache[bbl]
        
        for attempt in range(3):
            try:
                # Query by borough, block, lot for exact match
                params = {
                    "$where": f"borocode='{boro_id}' AND block='{block}' AND lot='{lot}'",
                    "$limit": 1
                }
                
                response = self.session.get(PLUTO_ENDPOINT, params=params, timeout=15)
                response.raise_for_status()
                results = response.json()
                
                if not results:
                    self._cache[bbl] = None
                    return None
                
                record = results[0]
                classification = self._parse_record(record)
                self._cache[bbl] = classification
                return classification
                
            except (requests.RequestException, ValueError) as e:
                if attempt < 2:
                    wait = 1.0 * (2 ** attempt)
                    logger.warning(f"PLUTO lookup attempt {attempt + 1} failed for BBL {bbl}: {e}, retrying in {wait}s")
                    time.sleep(wait)
                else:
                    self._single_errors += 1
                    logger.warning(f"PLUTO lookup failed for BBL {bbl} after 3 attempts: {e}")
                    return None
    
    def get_classifications_batch(
        self, 
        buildings: List[Dict], 
        boro_field: str = 'boroid',
        block_field: str = 'block',
        lot_field: str = 'lot'
    ) -> Dict[str, BuildingClassification]:
        """
        Get building classifications for a batch of buildings.
        
        Uses bulk fetch by borough to minimize API calls.
        
        Args:
            buildings: List of building dicts with boro/block/lot fields
            boro_field: Field name for borough ID
            block_field: Field name for block
            lot_field: Field name for lot
            
        Returns:
            Dict mapping BBL to BuildingClassification
        """
        results = {}
        
        # Build set of BBLs we need and group by borough
        needed_bbls: Dict[str, Set[str]] = defaultdict(set)  # boro -> set of BBLs
        bbl_to_info = {}
        
        for building in buildings:
            boro = building.get(boro_field, '')
            block = building.get(block_field, '')
            lot = building.get(lot_field, '')
            
            if boro and block and lot:
                bbl = self.make_bbl(boro, block, lot)
                
                # Check cache first
                if bbl in self._cache:
                    if self._cache[bbl]:
                        results[bbl] = self._cache[bbl]
                    continue
                
                needed_bbls[boro].add(bbl)
                bbl_to_info[bbl] = (boro, block, lot)
        
        if not needed_bbls:
            logger.info(f"PLUTO batch: all {len(buildings)} buildings already cached")
            return results
        
        # Fetch by borough - get all records, filter locally
        total_fetched = 0
        for boro, bbls_needed in needed_bbls.items():
            logger.info(f"PLUTO: Fetching data for borough {boro} ({len(bbls_needed)} BBLs needed)")
            
            # Get unique blocks in this borough
            blocks_needed = set()
            for bbl in bbls_needed:
                _, block, _ = bbl_to_info[bbl]
                blocks_needed.add(block)
            
            # Fetch in batches of blocks (max 50 blocks per request to keep URL short)
            block_list = sorted(list(blocks_needed))
            batch_size = 50
            
            for i in range(0, len(block_list), batch_size):
                batch_blocks = block_list[i:i+batch_size]
                
                for attempt in range(3):
                    try:
                        # Build IN clause for blocks
                        blocks_str = ",".join(f"'{b}'" for b in batch_blocks)
                        params = {
                            "$where": f"borocode='{boro}' AND block in ({blocks_str})",
                            "$limit": 50000  # Max per request
                        }
                        
                        response = self.session.get(PLUTO_ENDPOINT, params=params, timeout=60)
                        response.raise_for_status()
                        records = response.json()
                        
                        logger.info(f"PLUTO: Got {len(records)} records for boro {boro} blocks {batch_blocks[0]}-{batch_blocks[-1]}")
                        
                        # Process results
                        for record in records:
                            block = record.get('block', '')
                            lot = record.get('lot', '')
                            bbl = self.make_bbl(boro, block, lot)
                            
                            classification = self._parse_record(record)
                            self._cache[bbl] = classification
                            
                            if bbl in bbls_needed:
                                results[bbl] = classification
                                total_fetched += 1
                        
                        # Small delay between requests
                        time.sleep(0.2)
                        break  # Success, exit retry loop
                        
                    except (requests.RequestException, ValueError) as e:
                        if attempt < 2:
                            wait = 2.0 * (2 ** attempt)
                            logger.warning(f"PLUTO batch attempt {attempt + 1} failed for boro={boro}, blocks {batch_blocks[0]}-{batch_blocks[-1]}: {e}, retrying in {wait}s")
                            time.sleep(wait)
                        else:
                            logger.warning(f"PLUTO batch fetch failed after 3 attempts for boro={boro}, blocks {batch_blocks[0]}-{batch_blocks[-1]}: {e}")
                            self._batch_errors += 1
            
            # Mark BBLs not found as None in cache
            for bbl in bbls_needed:
                if bbl not in self._cache:
                    self._cache[bbl] = None
        
        total_needed = sum(len(bbls) for bbls in needed_bbls.values())
        logger.info(f"PLUTO batch fetch: {total_fetched}/{total_needed} classifications found for {len(buildings)} buildings ({self._batch_errors} batch errors)")
        return results
    
    def _parse_record(self, record: Dict) -> BuildingClassification:
        """Parse a PLUTO API record into BuildingClassification."""
        building_class = record.get('bldgclass', '')
        
        # Parse year built
        year_built = None
        if record.get('yearbuilt'):
            try:
                year_built = int(record['yearbuilt'])
                if year_built == 0:
                    year_built = None
            except (ValueError, TypeError):
                pass
        
        # Parse unit counts
        units_res = 0
        units_total = 0
        try:
            units_res = int(float(record.get('unitsres', 0)))
            units_total = int(float(record.get('unitstotal', 0)))
        except (ValueError, TypeError):
            pass
        
        return BuildingClassification(
            bbl=record.get('bbl', ''),
            borough=record.get('borough', ''),
            block=record.get('block', ''),
            lot=record.get('lot', ''),
            building_class=building_class,
            building_type=classify_building(building_class),
            land_use=record.get('landuse', ''),
            units_res=units_res,
            units_total=units_total,
            year_built=year_built,
            address=record.get('address', ''),
        )


# Convenience function
def get_building_type(boro_id: str, block: str, lot: str) -> Optional[str]:
    """
    Quick lookup for building type.
    
    Returns: 'condo', 'coop', 'rental_elevator', 'rental_walkup', 
             'small_residential', 'other', or None
    """
    client = PLUTOClient()
    classification = client.get_classification(boro_id, block, lot)
    if classification:
        return classification.building_type
    return None


# Quick test
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    client = PLUTOClient()
    
    # Test single lookup - a known Manhattan building
    print("Testing single lookup...")
    result = client.get_classification('1', '450', '31')
    if result:
        print(f"BBL: {result.bbl}")
        print(f"Building Class: {result.building_class}")
        print(f"Building Type: {result.building_type}")
        print(f"Address: {result.address}")
        print(f"Units: {result.units_res}")
    else:
        print("Not found")
    
    # Test batch lookup
    print("\nTesting batch lookup...")
    test_buildings = [
        {'boroid': '1', 'block': '450', 'lot': '31'},
        {'boroid': '1', 'block': '1561', 'lot': '45'},
        {'boroid': '1', 'block': '1553', 'lot': '2'},
    ]
    batch_results = client.get_classifications_batch(test_buildings)
    print(f"Got {len(batch_results)} results")
    for bbl, classification in batch_results.items():
        print(f"  {bbl}: {classification.building_type} ({classification.building_class})")
