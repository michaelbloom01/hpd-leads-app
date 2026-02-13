"""Pydantic response models for the HPD Leads API."""
from typing import List, Optional, Dict
from pydantic import BaseModel

from src.transform.aggregate import Lead


class OutreachAttemptResponse(BaseModel):
    """Outreach attempt data for API response."""
    id: str
    method: str
    outcome: str
    notes: Optional[str]
    timestamp: str


class ScoreBreakdown(BaseModel):
    """Score breakdown showing component scores."""
    portfolio: float = 0.0
    units: float = 0.0
    professional: float = 0.0
    contact: float = 0.0
    concentration: float = 0.0


class BuildingTypeBreakdownResponse(BaseModel):
    """Building type composition for a lead's portfolio."""
    condo: int = 0
    coop: int = 0
    rental_elevator: int = 0
    rental_walkup: int = 0
    small_residential: int = 0
    other: int = 0
    unknown: int = 0
    total: int = 0
    total_rental: int = 0


class ContactWithSource(BaseModel):
    """Contact info with source attribution."""
    value: str
    type: str
    source: str
    source_url: Optional[str] = None
    confidence: int = 50
    verified: bool = False


class LeadResponse(BaseModel):
    """Lead data for API response."""
    lead_id: str
    agent_name: str
    owner_name: str
    owner_type: str
    portfolio_size: int
    total_units: int
    buildings: List[str]
    phone: Optional[str]
    email: Optional[str]
    phones: List[ContactWithSource] = []
    emails: List[ContactWithSource] = []
    website: Optional[str]
    website_source: Optional[str] = None
    business_summary: Optional[str]
    linkedin_url: Optional[str] = None
    linkedin_people: List[str] = []
    address: Optional[str]
    boro: str
    boros: List[str]
    building_types: Optional[BuildingTypeBreakdownResponse] = None
    building_classes: Dict[str, int] = {}
    score: float
    score_breakdown: Optional[ScoreBreakdown] = None
    tags: List[str]
    enrichment_status: str
    outreach_status: str
    notes: Optional[str]
    outreach_attempts: List[OutreachAttemptResponse] = []
    contacts: List[Dict] = []
    entity_type: str = "unknown"
    company_name: Optional[str] = None
    primary_contact: Optional[str] = None
    primary_contact_title: Optional[str] = None
    estimated_monthly_revenue: float = 0.0
    estimated_annual_revenue: float = 0.0
    revenue_breakdown: Optional[List[Dict]] = None
    violation_count: int = 0
    violation_class_a: int = 0
    violation_class_b: int = 0
    violation_class_c: int = 0
    violations_per_unit: float = 0.0
    pipeline_stage: str = "research"
    next_follow_up: Optional[str] = None
    priority_rank: int = 0

    @staticmethod
    def from_lead(lead: Lead, outreach_attempts: Optional[List["OutreachAttemptResponse"]] = None, revenue_breakdown: Optional[List[Dict]] = None) -> "LeadResponse":
        """Convert a Lead dataclass to an API response."""
        # Convert score breakdown
        breakdown = None
        if lead.score_breakdown:
            breakdown = ScoreBreakdown(
                portfolio=lead.score_breakdown.get("portfolio", 0.0),
                units=lead.score_breakdown.get("units", 0.0),
                professional=lead.score_breakdown.get("professional", 0.0),
                contact=lead.score_breakdown.get("contact", 0.0),
                concentration=lead.score_breakdown.get("concentration", 0.0),
            )

        # Convert phones with sources
        phones_with_source = []
        if hasattr(lead, 'phones') and lead.phones:
            for p in lead.phones:
                if hasattr(p, 'value'):
                    phones_with_source.append(ContactWithSource(
                        value=p.value, type=p.type, source=p.source,
                        source_url=p.source_url, confidence=p.confidence,
                        verified=p.verified,
                    ))

        # Convert emails with sources
        emails_with_source = []
        if hasattr(lead, 'emails') and lead.emails:
            for e in lead.emails:
                if hasattr(e, 'value'):
                    emails_with_source.append(ContactWithSource(
                        value=e.value, type=e.type, source=e.source,
                        source_url=e.source_url, confidence=e.confidence,
                        verified=e.verified,
                    ))

        # Build building types response
        building_types_response = None
        if lead.building_types:
            building_types_response = BuildingTypeBreakdownResponse(
                condo=lead.building_types.condo, coop=lead.building_types.coop,
                rental_elevator=lead.building_types.rental_elevator,
                rental_walkup=lead.building_types.rental_walkup,
                small_residential=lead.building_types.small_residential,
                other=lead.building_types.other, unknown=lead.building_types.unknown,
                total=lead.building_types.total, total_rental=lead.building_types.total_rental,
            )

        return LeadResponse(
            lead_id=lead.lead_id,
            agent_name=lead.agent_name,
            owner_name=lead.owner_name,
            owner_type=lead.owner_type,
            portfolio_size=lead.portfolio_size,
            total_units=lead.total_units,
            buildings=lead.buildings,
            phone=lead.phone,
            email=lead.email,
            phones=phones_with_source,
            emails=emails_with_source,
            website=lead.website,
            website_source=getattr(lead, 'website_source', None),
            business_summary=lead.business_summary[:200] if isinstance(lead.business_summary, str) else None,
            linkedin_url=getattr(lead, 'linkedin_url', None),
            linkedin_people=getattr(lead, 'linkedin_people', []) or [],
            address=lead.address,
            boro=lead.boro,
            boros=lead.boros,
            building_types=building_types_response,
            building_classes=getattr(lead, 'building_classes', {}) or {},
            score=lead.score,
            score_breakdown=breakdown,
            tags=lead.tags,
            enrichment_status=lead.enrichment_status,
            outreach_status=lead.outreach_status,
            notes=lead.notes,
            outreach_attempts=outreach_attempts or [],
            contacts=lead.contacts if lead.contacts else [],
            entity_type=getattr(lead, 'entity_type', 'unknown'),
            company_name=getattr(lead, 'company_name', None),
            primary_contact=getattr(lead, 'primary_contact', None),
            primary_contact_title=getattr(lead, 'primary_contact_title', None),
            estimated_monthly_revenue=getattr(lead, 'estimated_monthly_revenue', 0.0) or 0.0,
            estimated_annual_revenue=getattr(lead, 'estimated_annual_revenue', 0.0) or 0.0,
            revenue_breakdown=revenue_breakdown,
            violation_count=getattr(lead, 'violation_count', 0) or 0,
            violation_class_a=getattr(lead, 'violation_class_a', 0) or 0,
            violation_class_b=getattr(lead, 'violation_class_b', 0) or 0,
            violation_class_c=getattr(lead, 'violation_class_c', 0) or 0,
            violations_per_unit=getattr(lead, 'violations_per_unit', 0.0) or 0.0,
            pipeline_stage=getattr(lead, 'pipeline_stage', 'research') or 'research',
            next_follow_up=getattr(lead, 'next_follow_up', None),
            priority_rank=getattr(lead, 'priority_rank', 0) or 0,
        )


class LeadsListResponse(BaseModel):
    """Paginated leads response with total count."""
    leads: List[LeadResponse]
    total: int
    offset: int
    limit: int


class PipelineStatus(BaseModel):
    """Pipeline status response."""
    total_leads: int
    last_refresh: Optional[str]
    enriched_count: int
    top_score: float
