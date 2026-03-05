from pathlib import Path


def test_lead_contacts_endpoint_is_defined():
    leads_router = Path(__file__).resolve().parents[1] / "src" / "routers" / "leads.py"
    source = leads_router.read_text(encoding="utf-8")
    assert '@router.get("/leads/{lead_id}/contacts")' in source
    assert "get_lead_contacts(session=session, lead_id=lead_id)" in source


def test_buildings_router_uses_shared_contact_roster_service():
    buildings_router = Path(__file__).resolve().parents[1] / "src" / "routers" / "buildings.py"
    source = buildings_router.read_text(encoding="utf-8")
    assert "from src.services.contact_roster import get_building_contacts" in source
    assert "contacts, contact_meta = await get_building_contacts(" in source
