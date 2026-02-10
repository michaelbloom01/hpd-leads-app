"""
Aggregate buildings into leads.

See docs/02-data-model.md for grouping logic.
"""
import hashlib
from collections import defaultdict, Counter
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import List, Optional, Dict

from .normalize import Building, Contact, normalize_name, normalize_name_for_grouping


@dataclass
class ContactWithSource:
    """Contact info with source attribution."""
    value: str
    type: str  # "phone" or "email"
    source: str  # "google_places", "hunter", "web_crawl", etc.
    source_url: Optional[str] = None  # Clickable link to verify
    confidence: int = 50  # 0-100
    verified: bool = False
    found_at: Optional[str] = None


@dataclass
class BuildingTypeBreakdown:
    """Breakdown of building types in a portfolio."""
    condo: int = 0
    coop: int = 0
    rental_elevator: int = 0
    rental_walkup: int = 0
    small_residential: int = 0  # 1-2 family homes
    other: int = 0
    unknown: int = 0
    
    @property
    def total(self) -> int:
        return self.condo + self.coop + self.rental_elevator + self.rental_walkup + self.small_residential + self.other + self.unknown
    
    @property
    def total_rental(self) -> int:
        """Total rental buildings (elevator + walkup)."""
        return self.rental_elevator + self.rental_walkup
    
    def to_dict(self) -> Dict:
        return {
            'condo': self.condo,
            'coop': self.coop,
            'rental_elevator': self.rental_elevator,
            'rental_walkup': self.rental_walkup,
            'small_residential': self.small_residential,
            'other': self.other,
            'unknown': self.unknown,
            'total': self.total,
            'total_rental': self.total_rental,
        }


@dataclass
class Lead:
    """A lead represents an agent/owner managing multiple buildings."""
    lead_id: str
    agent_name: str
    owner_name: str
    owner_type: str
    portfolio_size: int
    total_units: int
    buildings: List[str]  # List of addresses
    building_ids: List[str]  # List of building IDs
    phone: Optional[str] = None  # Best phone (backwards compat)
    email: Optional[str] = None  # Best email (backwards compat)
    phones: List[ContactWithSource] = field(default_factory=list)  # All phones with sources
    emails: List[ContactWithSource] = field(default_factory=list)  # All emails with sources
    website: Optional[str] = None
    website_source: Optional[str] = None  # Where we found the website
    business_summary: Optional[str] = None
    owner_principal: Optional[str] = None
    linkedin_url: Optional[str] = None  # Company LinkedIn page
    linkedin_people: List[str] = field(default_factory=list)  # Key people's LinkedIn profiles
    contacts: List[Dict] = field(default_factory=list)
    address: Optional[str] = None
    boro: str = ""
    boros: List[str] = field(default_factory=list)  # All boroughs
    # Building type composition from PLUTO
    building_types: BuildingTypeBreakdown = field(default_factory=BuildingTypeBreakdown)
    building_classes: Dict[str, int] = field(default_factory=dict)  # Raw class codes with counts
    reg_status: str = ""
    last_registration: Optional[date] = None
    dos_id: Optional[str] = None
    dos_status: Optional[str] = None
    enrichment_status: str = "none"
    enrichment_sources: List[str] = field(default_factory=list)
    last_enriched: Optional[datetime] = None
    score: float = 0.0
    score_breakdown: Dict = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    opportunity_note: Optional[str] = None
    outreach_status: str = "new"  # new, contacted, interested, not_interested, closed
    notes: Optional[str] = None
    # Entity classification (Phase 0)
    entity_type: str = "unknown"  # company, individual_agent, owner_operator, unknown
    company_name: Optional[str] = None  # Resolved company name (esp. for person-named leads)
    primary_contact: Optional[str] = None  # Best person name to contact
    primary_contact_title: Optional[str] = None  # Their role/title
    # Revenue estimation (Phase 5.1)
    estimated_monthly_revenue: float = 0.0
    estimated_annual_revenue: float = 0.0
    # Violations (Phase 5.2)
    violation_count: int = 0
    violation_class_a: int = 0
    violation_class_b: int = 0
    violation_class_c: int = 0
    violations_per_unit: float = 0.0
    # Pipeline (Phase 5.3)
    pipeline_stage: str = "research"  # research, first_contact, follow_up, meeting_scheduled, meeting_done, loi, due_diligence, closed
    next_follow_up: Optional[str] = None  # ISO date string
    priority_rank: int = 0  # 1-5 stars, 0 = unranked
    # Enrichment retry tracking (Phase 2.7)
    enrichment_retries: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


def aggregate_to_leads(buildings: List[Building]) -> List[Lead]:
    """
    Group buildings into leads by agent/owner.
    
    Primary grouping key: normalized agent name
    Fallback: normalized owner name
    
    Args:
        buildings: List of normalized buildings
        
    Returns:
        List of leads (one per unique agent/owner)
    """
    # Group by normalized agent name (preferred) or owner name
    groups: Dict[str, List[Building]] = defaultdict(list)
    
    for building in buildings:
        # Primary key: agent name (if present and substantial)
        key = building.agent_name
        if not key or len(key) < 3:
            # Fallback to owner name
            key = building.owner_name
        
        if key and len(key) >= 3:
            # Use grouping normalization for the key
            grouping_key = normalize_name_for_grouping(key)
            groups[grouping_key].append(building)
    
    # Convert groups to leads
    leads = []
    for grouping_key, group_buildings in groups.items():
        lead = _create_lead_from_buildings(grouping_key, group_buildings)
        leads.append(lead)
    
    # Sort by portfolio size descending
    leads.sort(key=lambda l: l.portfolio_size, reverse=True)
    
    # Deduplicate leads with same business address
    leads = deduplicate_leads(leads)
    
    return leads


def deduplicate_leads(leads: List[Lead]) -> List[Lead]:
    """
    Deduplicate leads that likely represent the same business.
    
    Deduplication criteria:
    1. Same business address (exact match, ignoring case)
    2. One lead's name is contained in another's (e.g., "ABC LLC" and "ABC MANAGEMENT LLC")
    
    When merging, keep the lead with larger portfolio and combine buildings.
    
    Args:
        leads: List of leads to deduplicate
        
    Returns:
        Deduplicated list of leads
    """
    if not leads:
        return leads
    
    # Index leads by normalized address
    address_index: Dict[str, List[int]] = defaultdict(list)
    for i, lead in enumerate(leads):
        if lead.address:
            # Normalize address for comparison
            normalized_addr = lead.address.upper().strip()
            # Remove common variations
            normalized_addr = normalized_addr.replace(".", "").replace(",", " ").replace("  ", " ")
            address_index[normalized_addr].append(i)
    
    # Find leads to merge (same address)
    merged_indices: set = set()
    merge_targets: Dict[int, int] = {}  # index -> target index to merge into
    
    for addr, indices in address_index.items():
        if len(indices) > 1:
            # Multiple leads at same address - merge into the one with largest portfolio
            sorted_indices = sorted(indices, key=lambda i: leads[i].portfolio_size, reverse=True)
            target_idx = sorted_indices[0]
            for idx in sorted_indices[1:]:
                merge_targets[idx] = target_idx
                merged_indices.add(idx)
    
    # Also check for name containment (e.g., "ABC" and "ABC MANAGEMENT")
    name_groups: Dict[str, List[int]] = defaultdict(list)
    for i, lead in enumerate(leads):
        if i in merged_indices:
            continue
        name = (lead.agent_name or lead.owner_name).upper().strip()
        # Use first 10 significant chars as key
        name_key = ''.join(c for c in name if c.isalnum())[:10]
        if len(name_key) >= 5:
            name_groups[name_key].append(i)
    
    for name_key, indices in name_groups.items():
        if len(indices) > 1:
            # Check if names are similar enough to merge
            sorted_indices = sorted(indices, key=lambda i: leads[i].portfolio_size, reverse=True)
            target_idx = sorted_indices[0]
            target_name = (leads[target_idx].agent_name or leads[target_idx].owner_name).upper()
            
            for idx in sorted_indices[1:]:
                if idx in merged_indices:
                    continue
                other_name = (leads[idx].agent_name or leads[idx].owner_name).upper()
                # Merge if one name contains the other (minus common suffixes)
                target_base = target_name.replace("LLC", "").replace("INC", "").replace("CORP", "").strip()
                other_base = other_name.replace("LLC", "").replace("INC", "").replace("CORP", "").strip()
                if target_base in other_base or other_base in target_base:
                    merge_targets[idx] = target_idx
                    merged_indices.add(idx)
    
    # Perform merges
    for source_idx, target_idx in merge_targets.items():
        source = leads[source_idx]
        target = leads[target_idx]
        
        # Combine buildings (avoiding duplicates)
        existing_addresses = set(target.buildings)
        for addr in source.buildings:
            if addr not in existing_addresses:
                target.buildings.append(addr)
                existing_addresses.add(addr)
        
        existing_ids = set(target.building_ids)
        for bid in source.building_ids:
            if bid not in existing_ids:
                target.building_ids.append(bid)
                existing_ids.add(bid)
        
        # Update portfolio size
        target.portfolio_size = len(target.buildings)
        
        # Merge boroughs
        for boro in source.boros:
            if boro not in target.boros:
                target.boros.append(boro)
        
        # Keep better contact info
        if not target.phone and source.phone:
            target.phone = source.phone
        if not target.email and source.email:
            target.email = source.email
        if not target.website and source.website:
            target.website = source.website
    
    # Return non-merged leads
    result = [lead for i, lead in enumerate(leads) if i not in merged_indices]
    
    # Re-sort by portfolio size
    result.sort(key=lambda l: l.portfolio_size, reverse=True)
    
    return result


def _create_lead_from_buildings(grouping_key: str, buildings: List[Building]) -> Lead:
    """
    Create a Lead from a group of buildings.
    
    Args:
        grouping_key: The grouping key (normalized name without suffixes)
        buildings: Buildings belonging to this lead
        
    Returns:
        Lead object
    """
    # Find the most recent building for "best" contact info
    buildings_sorted = sorted(
        buildings, 
        key=lambda b: b.last_registration or date.min, 
        reverse=True
    )
    most_recent = buildings_sorted[0]
    
    # Determine agent and owner names (use original, not grouping key)
    agent_name = ""
    owner_name = ""
    owner_type = ""
    
    # Collect all unique agent/owner names
    agent_names = Counter()
    owner_names = Counter()
    owner_types = Counter()
    
    for b in buildings:
        if b.agent_name:
            agent_names[b.agent_name] += 1
        if b.owner_name:
            owner_names[b.owner_name] += 1
        if b.owner:
            owner_types[b.owner.contact_type] += 1
    
    # Use most common names
    if agent_names:
        agent_name = agent_names.most_common(1)[0][0]
    if owner_names:
        owner_name = owner_names.most_common(1)[0][0]
    if owner_types:
        owner_type = owner_types.most_common(1)[0][0]
    
    # Aggregate portfolio metrics
    building_addresses = [b.address for b in buildings if b.address]
    building_ids = [b.building_id for b in buildings if b.building_id]
    
    # Determine boroughs
    boros = [b.boro for b in buildings if b.boro]
    boro_counts = Counter(boros)
    primary_boro = boro_counts.most_common(1)[0][0] if boro_counts else ""
    unique_boros = list(boro_counts.keys())
    
    # Get business address from most recent agent
    address = None
    if most_recent.agent:
        agent = most_recent.agent
        addr_parts = [agent.business_address, agent.business_city, agent.business_state, agent.business_zip]
        address = ", ".join(p for p in addr_parts if p)
    
    # Find most recent registration date
    last_reg = None
    for b in buildings_sorted:
        if b.last_registration:
            last_reg = b.last_registration
            break
    
    # Collect unique contacts
    contacts_seen = set()
    contacts = []
    for b in buildings:
        for c in b.contacts:
            if c.contact_id not in contacts_seen:
                contacts_seen.add(c.contact_id)
                contacts.append({
                    "type": c.contact_type,
                    "name": c.full_name,
                    "title": c.title,
                    "address": c.business_address,
                    "city": c.business_city,
                    "state": c.business_state,
                    "zip": c.business_zip,
                })
    
    # Aggregate building types from PLUTO data
    building_type_counts = Counter()
    building_class_counts = Counter()
    total_units = 0
    
    for b in buildings:
        if b.building_type:
            building_type_counts[b.building_type] += 1
        else:
            building_type_counts['unknown'] += 1
        
        if b.building_class:
            building_class_counts[b.building_class] += 1
        
        total_units += b.units_res
    
    building_types = BuildingTypeBreakdown(
        condo=building_type_counts.get('condo', 0),
        coop=building_type_counts.get('coop', 0),
        rental_elevator=building_type_counts.get('rental_elevator', 0),
        rental_walkup=building_type_counts.get('rental_walkup', 0),
        small_residential=building_type_counts.get('small_residential', 0),
        other=building_type_counts.get('other', 0),
        unknown=building_type_counts.get('unknown', 0),
    )
    
    # Generate stable lead ID
    lead_id = _generate_lead_id(grouping_key)
    
    # Entity classification
    entity_info = _classify_entity(agent_name, owner_name, owner_type, contacts)
    
    return Lead(
        lead_id=lead_id,
        agent_name=agent_name,
        owner_name=owner_name,
        owner_type=owner_type,
        portfolio_size=len(buildings),
        total_units=total_units,
        buildings=building_addresses,
        building_ids=building_ids,
        contacts=contacts,
        address=address,
        boro=primary_boro,
        boros=unique_boros,
        building_types=building_types,
        building_classes=dict(building_class_counts),
        last_registration=last_reg,
        entity_type=entity_info['entity_type'],
        company_name=entity_info['company_name'],
        primary_contact=entity_info['primary_contact'],
        primary_contact_title=entity_info['primary_contact_title'],
    )


def _classify_entity(agent_name: str, owner_name: str, owner_type: str, contacts: List[Dict]) -> Dict:
    """
    Classify a lead as company, individual_agent, or owner_operator.
    
    Returns dict with: entity_type, company_name, primary_contact, primary_contact_title
    """
    COMPANY_INDICATORS = [
        'LLC', 'INC', 'CORP', 'CO.', 'CO ', 'LTD', 'LP', 'L.P.', 'L.L.C.',
        'MANAGEMENT', 'MGMT', 'PROPERTIES', 'PROPERTY', 'REALTY', 'REAL ESTATE',
        'ASSOCIATES', 'GROUP', 'HOLDINGS', 'SERVICES', 'ENTERPRISES',
        'HOUSING', 'DEVELOPMENT', 'DEVELOPERS', 'INVESTMENTS', 'CAPITAL',
        'PARTNERS', 'PARTNERSHIP', 'EQUITIES', 'ADVISORS', 'CONSULTING',
        'VENTURES', 'TRUST', 'FUND', 'AGENCY',
    ]
    
    name_upper = (agent_name or '').upper().strip()
    owner_upper = (owner_name or '').upper().strip()
    
    is_company_name = any(ind in name_upper for ind in COMPANY_INDICATORS)
    is_owner_company = any(ind in owner_upper for ind in COMPANY_INDICATORS)
    
    # Check if name looks like a person (First Last pattern, no company indicators)
    def looks_like_person(name: str) -> bool:
        if not name or len(name) < 3:
            return False
        # If it has company indicators, it's not a person
        upper = name.upper()
        if any(ind in upper for ind in COMPANY_INDICATORS):
            return False
        # Simple heuristic: 2-3 words, all alpha
        parts = name.split()
        if 2 <= len(parts) <= 4 and all(p.replace("'", "").replace("-", "").isalpha() for p in parts):
            return True
        return False
    
    # Find the best company name from contacts
    corporate_owner = None
    agent_contact = None
    for c in contacts:
        ct = (c.get('type') or '').lower()
        cn = c.get('name') or ''
        if ct == 'corporateowner' and cn:
            if any(ind in cn.upper() for ind in COMPANY_INDICATORS):
                corporate_owner = c
        if ct == 'agent' and cn:
            agent_contact = c
    
    result = {
        'entity_type': 'unknown',
        'company_name': None,
        'primary_contact': None,
        'primary_contact_title': None,
    }
    
    if is_company_name:
        # Lead name itself is a company
        result['entity_type'] = 'company'
        result['company_name'] = agent_name or owner_name
        # Find a person to contact from contacts
        for c in contacts:
            ct = (c.get('type') or '').lower()
            cn = c.get('name') or ''
            if ct in ('agent', 'officer', 'sitemanager') and looks_like_person(cn):
                result['primary_contact'] = cn
                result['primary_contact_title'] = c.get('title') or ct.title()
                break
    elif looks_like_person(agent_name):
        # Lead is a person name acting as agent
        result['entity_type'] = 'individual_agent'
        result['primary_contact'] = agent_name
        result['primary_contact_title'] = 'Managing Agent'
        # Try to resolve their company from CorporateOwner contacts
        if corporate_owner:
            result['company_name'] = corporate_owner.get('name')
        elif is_owner_company:
            result['company_name'] = owner_name
    elif looks_like_person(owner_name) and not agent_name:
        # Owner-operator (no agent, just an owner name)
        result['entity_type'] = 'owner_operator'
        result['primary_contact'] = owner_name
        result['primary_contact_title'] = owner_type or 'Owner'
    else:
        # Fallback - try to determine from owner
        if is_owner_company:
            result['entity_type'] = 'company'
            result['company_name'] = owner_name
        elif owner_name:
            result['entity_type'] = 'owner_operator'
            result['primary_contact'] = owner_name if looks_like_person(owner_name) else None
    
    return result


def _generate_lead_id(name: str) -> str:
    """Generate a stable lead ID from the normalized name."""
    return hashlib.md5(name.encode()).hexdigest()[:12]


class StreamingLeadAggregator:
    """
    Memory-efficient lead aggregator that processes buildings in chunks.
    
    Instead of holding all buildings in memory, this aggregates incrementally
    by maintaining only the lead-level data structures.
    """
    
    def __init__(self):
        # Store partial lead data keyed by grouping_key
        # Each value is a dict with aggregated info (not full Building objects)
        self._lead_data: Dict[str, Dict] = {}
    
    def process_chunk(self, buildings: List[Building]) -> int:
        """
        Process a chunk of buildings and merge into aggregated lead data.
        
        Args:
            buildings: List of normalized Building objects
            
        Returns:
            Number of unique leads after this chunk
        """
        for building in buildings:
            # Determine grouping key
            key = building.agent_name
            if not key or len(key) < 3:
                key = building.owner_name
            
            if not key or len(key) < 3:
                continue
            
            grouping_key = normalize_name_for_grouping(key)
            
            if grouping_key not in self._lead_data:
                # Initialize new lead data
                self._lead_data[grouping_key] = {
                    "lead_id": _generate_lead_id(grouping_key),
                    "agent_names": Counter(),
                    "owner_names": Counter(),
                    "owner_types": Counter(),
                    "building_addresses": [],
                    "building_ids": [],
                    "boros": [],
                    "contacts": {},  # keyed by contact_id to dedupe
                    "last_registration": None,
                    "business_address": None,
                    # PLUTO building type tracking
                    "building_types": Counter(),
                    "building_classes": Counter(),
                    "total_units": 0,
                }
            
            data = self._lead_data[grouping_key]
            
            # Accumulate names
            if building.agent_name:
                data["agent_names"][building.agent_name] += 1
            if building.owner_name:
                data["owner_names"][building.owner_name] += 1
            if building.owner:
                data["owner_types"][building.owner.contact_type] += 1
            
            # Accumulate building info
            if building.address:
                data["building_addresses"].append(building.address)
            if building.building_id:
                data["building_ids"].append(building.building_id)
            if building.boro:
                data["boros"].append(building.boro)
            
            # Track most recent registration
            if building.last_registration:
                if data["last_registration"] is None or building.last_registration > data["last_registration"]:
                    data["last_registration"] = building.last_registration
                    # Update business address from most recent
                    if building.agent:
                        agent = building.agent
                        addr_parts = [agent.business_address, agent.business_city, agent.business_state, agent.business_zip]
                        data["business_address"] = ", ".join(p for p in addr_parts if p)
            
            # Accumulate unique contacts
            for c in building.contacts:
                if c.contact_id not in data["contacts"]:
                    data["contacts"][c.contact_id] = {
                        "type": c.contact_type,
                        "name": c.full_name,
                        "title": c.title,
                        "address": c.business_address,
                        "city": c.business_city,
                        "state": c.business_state,
                        "zip": c.business_zip,
                    }
            
            # Track PLUTO building types
            if building.building_type:
                data["building_types"][building.building_type] += 1
            else:
                data["building_types"]["unknown"] += 1
            
            if building.building_class:
                data["building_classes"][building.building_class] += 1
            
            data["total_units"] += building.units_res
        
        return len(self._lead_data)
    
    def get_leads(self) -> List[Lead]:
        """
        Finalize and return all aggregated leads.
        
        Returns:
            List of Lead objects sorted by portfolio size
        """
        leads = []
        
        for grouping_key, data in self._lead_data.items():
            # Determine best names from counters
            agent_name = data["agent_names"].most_common(1)[0][0] if data["agent_names"] else ""
            owner_name = data["owner_names"].most_common(1)[0][0] if data["owner_names"] else ""
            owner_type = data["owner_types"].most_common(1)[0][0] if data["owner_types"] else ""
            
            # Determine primary borough
            boro_counts = Counter(data["boros"])
            primary_boro = boro_counts.most_common(1)[0][0] if boro_counts else ""
            unique_boros = list(boro_counts.keys())
            
            # Build building type breakdown
            type_counts = data["building_types"]
            building_types = BuildingTypeBreakdown(
                condo=type_counts.get('condo', 0),
                coop=type_counts.get('coop', 0),
                rental_elevator=type_counts.get('rental_elevator', 0),
                rental_walkup=type_counts.get('rental_walkup', 0),
                small_residential=type_counts.get('small_residential', 0),
                other=type_counts.get('other', 0),
                unknown=type_counts.get('unknown', 0),
            )
            
            contacts_list = list(data["contacts"].values())
            entity_info = _classify_entity(agent_name, owner_name, owner_type, contacts_list)
            
            lead = Lead(
                lead_id=data["lead_id"],
                agent_name=agent_name,
                owner_name=owner_name,
                owner_type=owner_type,
                portfolio_size=len(data["building_addresses"]),
                total_units=data["total_units"],
                buildings=data["building_addresses"],
                building_ids=data["building_ids"],
                contacts=contacts_list,
                address=data["business_address"],
                boro=primary_boro,
                boros=unique_boros,
                building_types=building_types,
                building_classes=dict(data["building_classes"]),
                last_registration=data["last_registration"],
                entity_type=entity_info['entity_type'],
                company_name=entity_info['company_name'],
                primary_contact=entity_info['primary_contact'],
                primary_contact_title=entity_info['primary_contact_title'],
            )
            leads.append(lead)
        
        # Sort by portfolio size descending
        leads.sort(key=lambda l: l.portfolio_size, reverse=True)
        
        return leads
    
    def get_stats(self) -> Dict:
        """Get current aggregation statistics."""
        total_buildings = sum(len(d["building_addresses"]) for d in self._lead_data.values())
        return {
            "unique_leads": len(self._lead_data),
            "total_buildings": total_buildings,
        }


# Quick test
if __name__ == "__main__":
    from .normalize import normalize_building
    
    # Create some test buildings
    raw_buildings = [
        {
            "buildingid": "1",
            "registrationid": "100",
            "housenumber": "100",
            "streetname": "MAIN ST",
            "boro": "MANHATTAN",
            "zip": "10001",
            "block": "1",
            "lot": "1",
            "bin": "1000001",
            "lastregistrationdate": "2025-01-15T00:00:00.000",
            "contacts": [
                {"registrationcontactid": "1", "registrationid": "100", "type": "Agent", "corporationname": "ABC MGMT LLC"},
                {"registrationcontactid": "2", "registrationid": "100", "type": "CorporateOwner", "corporationname": "123 MAIN LLC"},
            ]
        },
        {
            "buildingid": "2",
            "registrationid": "101",
            "housenumber": "200",
            "streetname": "BROADWAY",
            "boro": "MANHATTAN",
            "zip": "10002",
            "block": "2",
            "lot": "1",
            "bin": "1000002",
            "lastregistrationdate": "2025-02-01T00:00:00.000",
            "contacts": [
                {"registrationcontactid": "3", "registrationid": "101", "type": "Agent", "corporationname": "ABC MGMT LLC"},
            ]
        },
        {
            "buildingid": "3",
            "registrationid": "102",
            "housenumber": "300",
            "streetname": "PARK AVE",
            "boro": "BROOKLYN",
            "zip": "11201",
            "block": "3",
            "lot": "1",
            "bin": "3000001",
            "lastregistrationdate": "2024-12-01T00:00:00.000",
            "contacts": [
                {"registrationcontactid": "4", "registrationid": "102", "type": "Agent", "corporationname": "XYZ PROPERTIES INC"},
            ]
        },
    ]
    
    # Normalize
    buildings = [normalize_building(b) for b in raw_buildings]
    
    # Aggregate
    leads = aggregate_to_leads(buildings)
    
    print(f"Created {len(leads)} leads from {len(buildings)} buildings:\n")
    for lead in leads:
        print(f"Lead: {lead.agent_name or lead.owner_name}")
        print(f"  ID: {lead.lead_id}")
        print(f"  Portfolio: {lead.portfolio_size} buildings")
        print(f"  Boroughs: {lead.boros}")
        print(f"  Address: {lead.address}")
        print(f"  Contacts: {len(lead.contacts)}")
        print()
