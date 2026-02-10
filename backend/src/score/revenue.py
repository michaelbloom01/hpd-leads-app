"""
Revenue estimation for property management leads.

Estimates management fee revenue based on:
- Total residential units (known accurately from HPD data)
- Building type mix (used to weight average rents, NOT as unit counts)
- Average rents by borough and building type (StreetEasy/Census ACS data)
- Standard property management fee rate (4-8%, midpoint 5%)

IMPORTANT: building_types fields (condo, rental_elevator, etc.) are BUILDING
COUNTS, not unit counts. We distribute total_units proportionally across
building types to estimate per-type unit counts.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Average monthly rent per unit by borough (2025-2026 estimates)
# Sources: StreetEasy, Census ACS, NYC Rent Guidelines Board
AVG_MONTHLY_RENT = {
    "MANHATTAN": {
        "rental_elevator": 4200,
        "rental_walkup": 3200,
        "condo": 2500,
        "coop": 2200,
        "small_residential": 2800,
        "other": 3000,
        "unknown": 3500,
    },
    "BROOKLYN": {
        "rental_elevator": 3200,
        "rental_walkup": 2600,
        "condo": 2000,
        "coop": 1800,
        "small_residential": 2200,
        "other": 2400,
        "unknown": 2800,
    },
    "QUEENS": {
        "rental_elevator": 2500,
        "rental_walkup": 2000,
        "condo": 1600,
        "coop": 1400,
        "small_residential": 1800,
        "other": 1900,
        "unknown": 2100,
    },
    "BRONX": {
        "rental_elevator": 2000,
        "rental_walkup": 1500,
        "condo": 1200,
        "coop": 1100,
        "small_residential": 1400,
        "other": 1500,
        "unknown": 1600,
    },
    "STATEN ISLAND": {
        "rental_elevator": 2000,
        "rental_walkup": 1700,
        "condo": 1400,
        "coop": 1300,
        "small_residential": 1600,
        "other": 1700,
        "unknown": 1800,
    },
}

# Default rents if borough unknown (weighted NYC average)
DEFAULT_RENTS = {
    "rental_elevator": 2800,
    "rental_walkup": 2200,
    "condo": 1700,
    "coop": 1500,
    "small_residential": 1900,
    "other": 2000,
    "unknown": 2200,
}

# Property management fee rate (% of gross rent)
# Standard range: 4-8%, using 5% midpoint
MGMT_FEE_RATE = 0.05

# Condo/coop management is typically just management (no rent collection)
# so the effective fee relative to market rent is lower
CONDO_COOP_ADJUSTMENT = 0.60  # 60% of rental rate

# Friendly display names for building types
BUILDING_TYPE_LABELS = {
    "rental_elevator": "Elevator Rental",
    "rental_walkup": "Walkup Rental",
    "condo": "Condo",
    "coop": "Co-op",
    "small_residential": "Small Residential",
    "other": "Other",
    "unknown": "Unknown",
}


def estimate_revenue(lead) -> dict:
    """
    Estimate monthly and annual property management revenue for a lead.
    
    CRITICAL: building_types.condo, .rental_elevator, etc. are BUILDING COUNTS.
    We use total_units (the accurate unit count) distributed proportionally
    by building type mix to estimate per-type unit counts.
    
    Args:
        lead: Lead object with building_types, total_units, and boro attributes
        
    Returns:
        dict with estimated revenues AND a detailed breakdown for display
    """
    total_units = getattr(lead, 'total_units', 0) or 0
    if total_units == 0:
        return {
            "estimated_monthly_revenue": 0.0,
            "estimated_annual_revenue": 0.0,
            "revenue_breakdown": [],
        }
    
    # Get the primary borough (or default)
    boro = (getattr(lead, 'boro', '') or '').upper()
    rent_table = AVG_MONTHLY_RENT.get(boro, DEFAULT_RENTS)
    
    building_types = getattr(lead, 'building_types', None)
    
    # Build building count map from building_types object
    bldg_count_map = {}
    if building_types:
        for btype in ["condo", "coop", "rental_elevator", "rental_walkup", "small_residential", "other", "unknown"]:
            count = getattr(building_types, btype, 0) or 0
            if count > 0:
                bldg_count_map[btype] = count
    
    total_buildings = sum(bldg_count_map.values())
    
    # If no building type breakdown, treat all as "unknown"
    if total_buildings == 0:
        bldg_count_map = {"unknown": 1}
        total_buildings = 1
    
    # Distribute total_units proportionally by building type share
    # Use largest-remainder method to ensure sum(estimated_units) == total_units
    raw_shares = {btype: (bldg_count / total_buildings) * total_units for btype, bldg_count in bldg_count_map.items()}
    floored = {btype: int(v) for btype, v in raw_shares.items()}
    remainder = total_units - sum(floored.values())
    # Give extra units to types with largest fractional remainders
    sorted_by_remainder = sorted(raw_shares.keys(), key=lambda b: raw_shares[b] - floored[b], reverse=True)
    unit_allocation = dict(floored)
    for i in range(remainder):
        unit_allocation[sorted_by_remainder[i]] += 1
    
    monthly_gross = 0.0
    breakdown = []
    
    for btype, estimated_units in unit_allocation.items():
        if estimated_units <= 0:
            continue
        
        rent = rent_table.get(btype, rent_table.get("unknown", 2200))
        effective_rent = rent
        
        # Condo/coop adjustment
        if btype in ("condo", "coop"):
            effective_rent = rent * CONDO_COOP_ADJUSTMENT
        
        type_gross = estimated_units * effective_rent
        monthly_gross += type_gross
        
        breakdown.append({
            "type": btype,
            "label": BUILDING_TYPE_LABELS.get(btype, btype),
            "buildings": bldg_count,
            "estimated_units": estimated_units,
            "rent_per_unit": effective_rent,
            "monthly_gross": round(type_gross, 2),
        })
    
    monthly_revenue = monthly_gross * MGMT_FEE_RATE
    avg_rent = monthly_gross / total_units if total_units > 0 else 0
    
    return {
        "estimated_monthly_revenue": round(monthly_revenue, 2),
        "estimated_annual_revenue": round(monthly_revenue * 12, 2),
        "revenue_breakdown": breakdown,
        "avg_rent_per_unit": round(avg_rent, 0),
        "fee_rate": MGMT_FEE_RATE,
        "total_units_used": total_units,
        "borough_used": boro or "NYC avg",
    }


def estimate_revenue_for_leads(leads: list) -> list:
    """
    Estimate revenue for a batch of leads.
    
    Args:
        leads: List of Lead objects
        
    Returns:
        Same list with estimated_monthly_revenue and estimated_annual_revenue set
    """
    for lead in leads:
        rev = estimate_revenue(lead)
        lead.estimated_monthly_revenue = rev["estimated_monthly_revenue"]
        lead.estimated_annual_revenue = rev["estimated_annual_revenue"]
    
    logger.info(f"Estimated revenue for {len(leads)} leads")
    return leads
