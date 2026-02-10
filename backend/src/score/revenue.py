"""
Revenue estimation for property management leads.

Estimates management fee revenue based on:
- Number of units per building type per borough
- Average rents by borough and building type (StreetEasy/Census ACS data)
- Standard property management fee rate (4-8%, midpoint 5%)

This gives a rough proxy for whether a company is in the PE target range
(e.g., $500K-$1.5M EBITDA).
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
        "condo": 2500,     # Management fee only (no rent collection), lower effective rate
        "coop": 2200,      # Similar to condo
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
# Condos/coops often pay flat fees but we approximate as % of imputed rent
MGMT_FEE_RATE = 0.05

# Condo/coop management is typically just management (no rent collection)
# so the effective fee relative to market rent is lower
CONDO_COOP_ADJUSTMENT = 0.60  # 60% of rental rate


def estimate_revenue(lead) -> dict:
    """
    Estimate monthly and annual property management revenue for a lead.
    
    Uses building type breakdown and borough distribution.
    
    Args:
        lead: Lead object with building_types and boro/boros attributes
        
    Returns:
        dict with estimated_monthly_revenue and estimated_annual_revenue
    """
    building_types = getattr(lead, 'building_types', None)
    if not building_types:
        # Fallback: use total_units with default rent
        total_units = getattr(lead, 'total_units', 0) or 0
        if total_units == 0:
            return {"estimated_monthly_revenue": 0.0, "estimated_annual_revenue": 0.0}
        
        avg_rent = DEFAULT_RENTS["unknown"]
        monthly_gross = total_units * avg_rent
        monthly_revenue = monthly_gross * MGMT_FEE_RATE
        return {
            "estimated_monthly_revenue": round(monthly_revenue, 2),
            "estimated_annual_revenue": round(monthly_revenue * 12, 2),
        }
    
    # Get the primary borough (or default)
    boro = (getattr(lead, 'boro', '') or '').upper()
    rent_table = AVG_MONTHLY_RENT.get(boro, DEFAULT_RENTS)
    
    # Calculate revenue by building type
    monthly_gross = 0.0
    
    type_map = {
        "condo": getattr(building_types, 'condo', 0) or 0,
        "coop": getattr(building_types, 'coop', 0) or 0,
        "rental_elevator": getattr(building_types, 'rental_elevator', 0) or 0,
        "rental_walkup": getattr(building_types, 'rental_walkup', 0) or 0,
        "small_residential": getattr(building_types, 'small_residential', 0) or 0,
        "other": getattr(building_types, 'other', 0) or 0,
        "unknown": getattr(building_types, 'unknown', 0) or 0,
    }
    
    for btype, units in type_map.items():
        if units <= 0:
            continue
        
        rent = rent_table.get(btype, rent_table.get("unknown", 2200))
        
        # Condo/coop adjustment
        if btype in ("condo", "coop"):
            rent = rent * CONDO_COOP_ADJUSTMENT
        
        monthly_gross += units * rent
    
    monthly_revenue = monthly_gross * MGMT_FEE_RATE
    
    return {
        "estimated_monthly_revenue": round(monthly_revenue, 2),
        "estimated_annual_revenue": round(monthly_revenue * 12, 2),
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
