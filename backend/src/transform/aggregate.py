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
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    business_summary: Optional[str] = None
    owner_principal: Optional[str] = None
    contacts: List[Dict] = field(default_factory=list)
    address: Optional[str] = None
    boro: str = ""
    boros: List[str] = field(default_factory=list)  # All boroughs
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
    
    return leads


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
    
    # Generate stable lead ID
    lead_id = _generate_lead_id(grouping_key)
    
    return Lead(
        lead_id=lead_id,
        agent_name=agent_name,
        owner_name=owner_name,
        owner_type=owner_type,
        portfolio_size=len(buildings),
        total_units=0,  # HPD data doesn't include unit count directly
        buildings=building_addresses,
        building_ids=building_ids,
        contacts=contacts,
        address=address,
        boro=primary_boro,
        boros=unique_boros,
        last_registration=last_reg,
    )


def _generate_lead_id(name: str) -> str:
    """Generate a stable lead ID from the normalized name."""
    return hashlib.md5(name.encode()).hexdigest()[:12]


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
