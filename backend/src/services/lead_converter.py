"""
Helpers to convert between DB rows, Lead dataclasses, and API responses.

Centralizes the _row_to_lead and _lead_to_response logic that was previously
duplicated in api.py and database.py.
"""
import json
import logging
from typing import Dict, List, Optional

from src.transform.aggregate import Lead, BuildingTypeBreakdown
from src.schemas.responses import OutreachAttemptResponse

logger = logging.getLogger(__name__)


def row_to_lead(row_dict: Dict) -> Lead:
    """Convert a raw DB row dict to a Lead object."""
    buildings = json.loads(row_dict.get("buildings") or "[]")
    building_ids = json.loads(row_dict.get("building_ids") or "[]")
    contacts = json.loads(row_dict.get("contacts") or "[]")
    boros = json.loads(row_dict.get("boros") or "[]")
    enrichment_sources = json.loads(row_dict.get("enrichment_sources") or "[]")
    score_breakdown = json.loads(row_dict.get("score_breakdown") or "{}")
    tags = json.loads(row_dict.get("tags") or "[]")

    building_types = BuildingTypeBreakdown()
    if row_dict.get("building_types"):
        try:
            bt_data = json.loads(row_dict["building_types"])
            building_types = BuildingTypeBreakdown(
                condo=bt_data.get("condo", 0), coop=bt_data.get("coop", 0),
                rental_elevator=bt_data.get("rental_elevator", 0),
                rental_walkup=bt_data.get("rental_walkup", 0),
                small_residential=bt_data.get("small_residential", 0),
                other=bt_data.get("other", 0), unknown=bt_data.get("unknown", 0),
            )
        except (ValueError, TypeError):
            pass

    building_classes: dict = {}
    if row_dict.get("building_classes"):
        try:
            building_classes = json.loads(row_dict["building_classes"])
        except (ValueError, TypeError):
            pass

    return Lead(
        lead_id=row_dict["lead_id"],
        agent_name=row_dict.get("agent_name") or "",
        owner_name=row_dict.get("owner_name") or "",
        owner_type=row_dict.get("owner_type") or "",
        portfolio_size=row_dict.get("portfolio_size") or 0,
        total_units=row_dict.get("total_units") or 0,
        buildings=buildings, building_ids=building_ids, contacts=contacts,
        address=row_dict.get("address"), boro=row_dict.get("boro") or "", boros=boros,
        building_types=building_types, building_classes=building_classes,
        reg_status=row_dict.get("reg_status") or "",
        dos_id=row_dict.get("dos_id"), dos_status=row_dict.get("dos_status"),
        phone=row_dict.get("phone"), email=row_dict.get("email"),
        website=row_dict.get("website"),
        business_summary=row_dict.get("business_summary"),
        owner_principal=row_dict.get("owner_principal"),
        enrichment_status=row_dict.get("enrichment_status") or "none",
        enrichment_sources=enrichment_sources,
        score=row_dict.get("score") or 0.0, score_breakdown=score_breakdown,
        tags=tags, opportunity_note=row_dict.get("opportunity_note"),
        outreach_status=row_dict.get("outreach_status") or "new",
        notes=row_dict.get("notes"),
        entity_type=row_dict.get("entity_type") or "unknown",
        company_name=row_dict.get("company_name"),
        primary_contact=row_dict.get("primary_contact"),
        primary_contact_title=row_dict.get("primary_contact_title"),
        estimated_monthly_revenue=row_dict.get("estimated_monthly_revenue") or 0.0,
        estimated_annual_revenue=row_dict.get("estimated_annual_revenue") or 0.0,
        violation_count=row_dict.get("violation_count") or 0,
        violation_class_a=row_dict.get("violation_class_a") or 0,
        violation_class_b=row_dict.get("violation_class_b") or 0,
        violation_class_c=row_dict.get("violation_class_c") or 0,
        violations_per_unit=row_dict.get("violations_per_unit") or 0.0,
        pipeline_stage=row_dict.get("pipeline_stage") or "research",
        next_follow_up=row_dict.get("next_follow_up"),
        priority_rank=row_dict.get("priority_rank") or 0,
        enrichment_retries=row_dict.get("enrichment_retries") or 0,
    )


def get_outreach_attempts_for_lead(lead_id: str) -> List[OutreachAttemptResponse]:
    """Fetch outreach attempts from the DB for a single lead."""
    from src.storage.database import get_database
    db = get_database()
    attempts = db.get_outreach_attempts(lead_id)
    return [
        OutreachAttemptResponse(
            id=a.get("id", ""),
            method=a.get("method", ""),
            outcome=a.get("outcome", ""),
            notes=a.get("notes"),
            timestamp=a.get("timestamp", ""),
        )
        for a in attempts
    ]


def get_revenue_breakdown(lead) -> Optional[List[Dict]]:
    """Compute revenue breakdown for display (live, not cached)."""
    try:
        from src.score.revenue import estimate_revenue
        result = estimate_revenue(lead)
        bd = result.get("revenue_breakdown", [])
        if bd:
            return [{
                **item,
                "fee_rate": result.get("fee_rate", 0.05),
                "avg_rent_per_unit": result.get("avg_rent_per_unit", 0),
                "borough_used": result.get("borough_used", ""),
                "total_units_used": result.get("total_units_used", 0),
            } for item in bd]
    except Exception:
        pass
    return None
