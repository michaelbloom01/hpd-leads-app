"""
AI-powered company description generator using Anthropic Claude.
"""
import os
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)

# R7: configurable model via env var.
# Keep a fallback chain because model aliases can be retired by Anthropic.
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")
ANTHROPIC_MODEL_FALLBACKS = [
    "claude-3-5-sonnet-latest",
    "claude-3-5-haiku-latest",
    "claude-3-haiku-20240307",
]


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
        Tuple of (description, None) on success, or (None, reason_string) on failure.
        For backward compatibility, callers checking `result is None` should use `result[0] is None`.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not configured, skipping AI summary")
        return None, "Anthropic API key is not configured on the server. Set the ANTHROPIC_API_KEY environment variable."
    
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic package not installed, skipping AI summary")
        return None, "The 'anthropic' Python package is not installed on the server."
    
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
    
    prompt = f"""You are writing a brief company profile for a property management acquisition target database.

Data:
{context}

Write exactly 2-3 sentences in plain text (no markdown, no bullet points, no headers). Cover: (1) what they manage and where, (2) their scale and specialization, (3) one insight relevant to an acquirer (e.g., concentration in one borough, mix of building types, portfolio density). Be factual — do not invent information not in the data above. Do not start with the company name."""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        tried_models = []
        response = None
        model_candidates = [ANTHROPIC_MODEL] + [m for m in ANTHROPIC_MODEL_FALLBACKS if m != ANTHROPIC_MODEL]
        for model_name in model_candidates:
            tried_models.append(model_name)
            try:
                response = client.messages.create(
                    model=model_name,
                    max_tokens=500,
                    messages=[{"role": "user", "content": prompt}],
                )
                break
            except Exception as model_exc:
                msg = str(model_exc).lower()
                # Retry with fallback model when alias/model is retired.
                if "not_found_error" in msg or "model" in msg and "not found" in msg:
                    logger.warning("Anthropic model unavailable: %s", model_name)
                    continue
                raise

        if response is None:
            return None, f"No available Anthropic model from: {', '.join(tried_models)}"
        
        description = response.content[0].text.strip()
        logger.info(f"Generated AI description for {company_name}")
        return description, None
        
    except Exception as e:
        logger.error(f"AI summary generation failed: {e}")
        return None, f"AI API call failed: {str(e)}"
