"""Test the data flow to ensure management companies are correctly extracted."""
import logging
logging.basicConfig(level=logging.INFO)

from src.ingest.hpd_client import HPDClient
from src.transform.normalize import normalize_building
from src.transform.aggregate import aggregate_to_leads
from src.score.scorer import score_leads

# Fetch a small sample
print("Fetching 100 buildings...")
client = HPDClient()
raw = client.get_combined_data(building_limit=100)
print(f"Got {len(raw)} buildings")

# Check contacts
print("\n=== Sample building with contacts ===")
for b in raw[:5]:
    contacts = b.get('contacts', [])
    agents = [c for c in contacts if c.get('type') == 'Agent']
    owners = [c for c in contacts if 'Owner' in c.get('type', '')]
    
    print(f"\nBuilding: {b.get('housenumber')} {b.get('streetname')}, {b.get('boro')}")
    print(f"  Total contacts: {len(contacts)}")
    if agents:
        agent = agents[0]
        name = agent.get('corporationname') or f"{agent.get('firstname', '')} {agent.get('lastname', '')}".strip()
        print(f"  AGENT (Mgmt Co): {name}")
    if owners:
        owner = owners[0]
        name = owner.get('corporationname') or f"{owner.get('firstname', '')} {owner.get('lastname', '')}".strip()
        print(f"  Owner: {name}")

# Normalize
print("\n=== Normalizing ===")
buildings = [normalize_building(b) for b in raw]
print(f"Normalized {len(buildings)} buildings")

# Check agent extraction
with_agent = len([b for b in buildings if b.agent])
with_owner = len([b for b in buildings if b.owner])
print(f"Buildings with Agent (Mgmt Co): {with_agent}")
print(f"Buildings with Owner: {with_owner}")

# Aggregate
print("\n=== Aggregating to Leads ===")
leads = aggregate_to_leads(buildings)
print(f"Created {len(leads)} leads")

# Score
leads = score_leads(leads)

# Show top leads
print("\n=== TOP 10 MANAGEMENT COMPANIES BY PORTFOLIO ===")
for i, lead in enumerate(leads[:10], 1):
    print(f"{i:2}. {lead.agent_name or lead.owner_name}")
    print(f"    Portfolio: {lead.portfolio_size} buildings")
    print(f"    Borough: {lead.boro}")
    print(f"    Score: {lead.score:.1f}")
    print()
