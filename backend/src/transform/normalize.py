"""
Data normalization functions.

See docs/02-data-model.md for normalization rules.
"""
import re
from typing import Optional, List, Dict
from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class Contact:
    """Normalized contact record (owner, agent, or officer)."""
    contact_id: str
    registration_id: str
    contact_type: str  # CorporateOwner, IndividualOwner, Agent, HeadOfficer, etc.
    description: str
    corporation_name: str
    first_name: str
    last_name: str
    title: str
    business_address: str
    business_city: str
    business_state: str
    business_zip: str
    
    @property
    def full_name(self) -> str:
        """Get full name (person or corporation)."""
        if self.corporation_name:
            return self.corporation_name
        parts = [self.first_name, self.last_name]
        return " ".join(p for p in parts if p)
    
    @property
    def is_agent(self) -> bool:
        return self.contact_type.upper() == "AGENT"
    
    @property
    def is_owner(self) -> bool:
        return "OWNER" in self.contact_type.upper()


@dataclass
class Building:
    """Normalized building record with contacts."""
    building_id: str
    registration_id: str
    address: str
    boro: str
    boro_id: str  # 1=Manhattan, 2=Bronx, 3=Brooklyn, 4=Queens, 5=SI
    zip_code: str
    block: str
    lot: str
    bin: str
    last_registration: Optional[date]
    registration_end: Optional[date]
    contacts: List[Contact] = field(default_factory=list)
    # PLUTO data
    building_class: str = ""  # e.g., "D4", "R1", "C0"
    building_type: str = ""  # e.g., "condo", "coop", "rental_elevator"
    units_res: int = 0
    year_built: Optional[int] = None
    
    @property
    def agent(self) -> Optional[Contact]:
        """Get the agent contact if present."""
        for c in self.contacts:
            if c.is_agent:
                return c
        return None
    
    @property
    def owner(self) -> Optional[Contact]:
        """Get the first owner contact."""
        for c in self.contacts:
            if c.is_owner:
                return c
        return None
    
    @property
    def agent_name(self) -> str:
        """Get agent name (normalized)."""
        agent = self.agent
        if agent:
            return normalize_name(agent.full_name)
        return ""
    
    @property
    def owner_name(self) -> str:
        """Get owner name (normalized)."""
        owner = self.owner
        if owner:
            return normalize_name(owner.full_name)
        return ""


def normalize_building(raw: dict) -> Building:
    """
    Normalize a raw HPD building record with contacts.
    
    Args:
        raw: Raw record from HPD API (with contacts attached)
        
    Returns:
        Normalized Building object
    """
    # Parse dates
    last_reg = None
    if raw.get("lastregistrationdate"):
        try:
            last_reg = datetime.fromisoformat(raw["lastregistrationdate"].replace("T", " ").split(".")[0]).date()
        except (ValueError, AttributeError):
            pass
    
    reg_end = None
    if raw.get("registrationenddate"):
        try:
            reg_end = datetime.fromisoformat(raw["registrationenddate"].replace("T", " ").split(".")[0]).date()
        except (ValueError, AttributeError):
            pass
    
    # Build address
    house_num = raw.get("housenumber", "")
    street = raw.get("streetname", "")
    address = f"{house_num} {street}".strip()
    
    # Normalize contacts
    contacts = []
    for raw_contact in raw.get("contacts", []):
        contacts.append(normalize_contact(raw_contact))
    
    return Building(
        building_id=raw.get("buildingid", ""),
        registration_id=raw.get("registrationid", ""),
        address=address,
        boro=raw.get("boro", ""),
        boro_id=raw.get("boroid", ""),
        zip_code=raw.get("zip", ""),
        block=raw.get("block", ""),
        lot=raw.get("lot", ""),
        bin=raw.get("bin", ""),
        last_registration=last_reg,
        registration_end=reg_end,
        contacts=contacts,
        # PLUTO fields (populated during refresh if pluto=true)
        building_class=raw.get("building_class", ""),
        building_type=raw.get("building_type", ""),
        units_res=int(raw.get("units_res", 0)) if raw.get("units_res") else 0,
        year_built=int(raw.get("year_built")) if raw.get("year_built") else None,
    )


def normalize_contact(raw: dict) -> Contact:
    """
    Normalize a raw contact record.
    
    Args:
        raw: Raw contact from HPD API
        
    Returns:
        Normalized Contact object
    """
    # Build business address
    parts = [
        raw.get("businesshousenumber", ""),
        raw.get("businessstreetname", ""),
    ]
    apt = raw.get("businessapartment", "")
    if apt:
        parts.append(f"Apt {apt}")
    business_address = " ".join(p for p in parts if p)
    
    return Contact(
        contact_id=raw.get("registrationcontactid", ""),
        registration_id=raw.get("registrationid", ""),
        contact_type=raw.get("type", ""),
        description=raw.get("contactdescription", ""),
        corporation_name=raw.get("corporationname", ""),
        first_name=raw.get("firstname", ""),
        last_name=raw.get("lastname", ""),
        title=raw.get("title", ""),
        business_address=business_address,
        business_city=raw.get("businesscity", ""),
        business_state=raw.get("businessstate", ""),
        business_zip=raw.get("businesszip", ""),
    )


def normalize_name(name: Optional[str]) -> str:
    """
    Normalize a company or person name.
    
    Rules:
    1. Uppercase
    2. Remove punctuation (LLC → LLC, L.L.C. → LLC)
    3. Trim whitespace
    4. Replace multiple spaces with single space
    
    Args:
        name: Raw name string
        
    Returns:
        Normalized name
    """
    if not name:
        return ""
    
    # Uppercase
    name = name.upper()
    
    # Remove periods (L.L.C. → LLC)
    name = name.replace(".", "")
    
    # Remove commas
    name = name.replace(",", "")
    
    # Normalize whitespace
    name = " ".join(name.split())
    
    return name.strip()


def normalize_name_for_grouping(name: str) -> str:
    """
    Normalize a name for grouping/deduplication.
    
    Additional rules beyond normalize_name:
    - Iteratively remove common suffixes (LLC, INC, CORP, etc.) until stable
    - Strip leading noise words (THE, A)
    
    Args:
        name: Already normalized name
        
    Returns:
        Name suitable for grouping
    """
    if not name:
        return ""
    
    # Already normalized, but ensure uppercase
    name = name.upper()
    
    # Remove common suffixes for grouping — ITERATIVE until stable
    # This fixes the bug where "ABC LLC INC" only stripped "INC" in a single pass
    suffixes = [
        " LLC", " L L C", " INC", " INCORPORATED", " CORP", " CORPORATION",
        " CO", " COMPANY", " LP", " LLP", " PLLC", " PC", " PA",
        " ASSOCIATES", " ASSOC", " GROUP", " HOLDINGS", " PARTNERS",
        " MANAGEMENT", " MGMT", " MGT", " PROPERTIES", " PROPERTY",
        " REALTY", " REAL ESTATE", " ENTERPRISES", " SERVICES",
    ]
    changed = True
    while changed:
        changed = False
        for suffix in suffixes:
            if name.endswith(suffix):
                name = name[:-len(suffix)].strip()
                changed = True
    
    # Strip leading noise words
    for prefix in ["THE ", "A "]:
        if name.startswith(prefix):
            name = name[len(prefix):]
    
    return name.strip()


def normalize_phone(phone: Optional[str]) -> Optional[str]:
    """
    Normalize a phone number to (XXX) XXX-XXXX format.
    
    Args:
        phone: Raw phone string
        
    Returns:
        Formatted phone or None if invalid
    """
    if not phone:
        return None
    
    # Extract digits only
    digits = re.sub(r"\D", "", phone)
    
    # Remove leading 1 (country code)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    
    # Must be 10 digits
    if len(digits) != 10:
        return None
    
    # Format as (XXX) XXX-XXXX
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def normalize_email(email: Optional[str]) -> Optional[str]:
    """
    Normalize an email address.
    
    Args:
        email: Raw email string
        
    Returns:
        Lowercase trimmed email or None if invalid
    """
    if not email:
        return None
    
    email = email.lower().strip()
    
    # Basic validation
    if "@" not in email or "." not in email:
        return None
    
    return email


# Quick test
if __name__ == "__main__":
    print("Name normalization:")
    print(f"  'ABC Management, L.L.C.' -> '{normalize_name('ABC Management, L.L.C.')}'")
    print(f"  'XYZ Holdings LLC' (for grouping) -> '{normalize_name_for_grouping(normalize_name('XYZ Holdings LLC'))}'")
    
    print("\nPhone normalization:")
    print(f"  '1-212-555-1234' -> '{normalize_phone('1-212-555-1234')}'")
    print(f"  '212.555.1234' -> '{normalize_phone('212.555.1234')}'")
    
    print("\nEmail normalization:")
    print(f"  'Test@Example.COM ' -> '{normalize_email('Test@Example.COM ')}'")
    
    # Test building normalization
    print("\nBuilding normalization:")
    raw_building = {
        "buildingid": "123",
        "registrationid": "456",
        "housenumber": "100",
        "streetname": "MAIN ST",
        "boro": "MANHATTAN",
        "zip": "10001",
        "block": "1",
        "lot": "1",
        "bin": "1000001",
        "lastregistrationdate": "2025-01-15T00:00:00.000",
        "contacts": [
            {
                "registrationcontactid": "1",
                "registrationid": "456",
                "type": "Agent",
                "corporationname": "ABC MGMT LLC",
                "businesshousenumber": "200",
                "businessstreetname": "BROADWAY",
                "businesscity": "NEW YORK",
                "businessstate": "NY",
                "businesszip": "10001",
            }
        ]
    }
    building = normalize_building(raw_building)
    print(f"  Address: {building.address}")
    print(f"  Agent: {building.agent_name}")
