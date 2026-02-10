"""
AI-powered company description generator using Anthropic Claude.
"""
import os
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# R7: configurable model via env var
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")


def generate_company_description(
    company_name: str,
    portfolio_size: int,
    total_units: int,
    boroughs: list,
    building_types: Optional[Dict[str, int]] = None,
    owner_names: Optional[list] = None,
    year_established: Optional[str] = None,
    service_areas: Optional[list] = None,
    website_description: Optional[str] = None,
) -> Optional[str]:
    """
    Generate a succinct AI description of the property management company.
    
    Args:
        company_name: Name of the management company
        portfolio_size: Number of buildings managed
        total_units: Total residential units
        boroughs: List of NYC boroughs where they operate
        building_types: Dict of building type counts (condo, coop, rental, etc.)
        owner_names: Known owner/principal names
        year_established: Year the company was founded
        service_areas: Areas they service
        website_description: Description scraped from their website
        
    Returns:
        2-3 sentence description of the company, or None if API not configured
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.debug("ANTHROPIC_API_KEY not configured, skipping AI summary")
        return None
    
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic package not installed, skipping AI summary")
        return None
    
    # Build context about the company
    context_parts = [
        f"Company: {company_name}",
        f"Portfolio: {portfolio_size} buildings, {total_units:,} total units",
        f"Operating in: {', '.join(boroughs)}",
    ]
    
    if building_types:
        type_breakdown = []
        if building_types.get('condo', 0) > 0:
            type_breakdown.append(f"{building_types['condo']} condos")
        if building_types.get('coop', 0) > 0:
            type_breakdown.append(f"{building_types['coop']} coops")
        rental_count = building_types.get('rental_elevator', 0) + building_types.get('rental_walkup', 0)
        if rental_count > 0:
            type_breakdown.append(f"{rental_count} rental buildings")
        if type_breakdown:
            context_parts.append(f"Building mix: {', '.join(type_breakdown)}")
    
    if owner_names:
        context_parts.append(f"Principal(s): {', '.join(owner_names)}")
    
    if year_established:
        context_parts.append(f"Established: {year_established}")
    
    if service_areas:
        context_parts.append(f"Service areas: {', '.join(service_areas)}")
    
    if website_description:
        context_parts.append(f"From website: {website_description[:300]}")
    
    context = "\n".join(context_parts)
    
    prompt = f"""Based on this NYC property management company data, write a 2-3 sentence professional description suitable for a CRM lead record. Focus on their scale, specialization, and any notable characteristics. Be factual and concise.

{context}

Write only the description, no preamble or labels."""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=ANTHROPIC_MODEL,  # R7: configurable via ANTHROPIC_MODEL env var
            max_tokens=500,  # Allow longer descriptions
            messages=[{"role": "user", "content": prompt}]
        )
        
        description = response.content[0].text.strip()
        logger.info(f"Generated AI description for {company_name}")
        return description
        
    except Exception as e:
        logger.error(f"AI summary generation failed: {e}")
        return None
