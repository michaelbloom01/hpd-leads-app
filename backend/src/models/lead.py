"""Lead model.

Leads represent PM companies (aggregations of buildings). The PE Searcher
persona evaluates these as acquisition targets. Lead scoring (V3) uses
portfolio size, revenue, and enrichment quality -- distinct from the PM
Operator's building churn scoring.
"""
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Date, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class Lead(TimestampMixin, Base):
    __tablename__ = "leads"

    lead_id: Mapped[str] = mapped_column(String(12), primary_key=True)
    normalized_name: Mapped[Optional[str]] = mapped_column(Text)
    agent_name: Mapped[Optional[str]] = mapped_column(Text)
    owner_name: Mapped[Optional[str]] = mapped_column(Text)
    owner_type: Mapped[Optional[str]] = mapped_column(String(30))
    company_name: Mapped[Optional[str]] = mapped_column(Text)
    entity_type: Mapped[Optional[str]] = mapped_column(
        String(20), server_default="unknown"
    )
    primary_contact: Mapped[Optional[str]] = mapped_column(Text)
    primary_contact_title: Mapped[Optional[str]] = mapped_column(Text)
    address: Mapped[Optional[str]] = mapped_column(Text)
    primary_borough: Mapped[Optional[str]] = mapped_column(String(20))

    # Portfolio metrics (computed from building_management)
    portfolio_size: Mapped[Optional[int]] = mapped_column(Integer, server_default="0")
    total_units: Mapped[Optional[int]] = mapped_column(Integer, server_default="0")
    portfolio_churn_risk: Mapped[Optional[float]] = mapped_column(Float)

    # Lead scoring (V3, PE Searcher persona)
    score: Mapped[Optional[float]] = mapped_column(Float, server_default="0.0")
    score_breakdown: Mapped[Optional[dict]] = mapped_column(JSONB)

    # Enrichment
    phone: Mapped[Optional[str]] = mapped_column(String(20))
    email: Mapped[Optional[str]] = mapped_column(Text)
    website: Mapped[Optional[str]] = mapped_column(Text)
    business_summary: Mapped[Optional[str]] = mapped_column(Text)
    owner_principal: Mapped[Optional[str]] = mapped_column(Text)
    enrichment_status: Mapped[Optional[str]] = mapped_column(
        String(20), server_default="none"
    )
    enrichment_sources: Mapped[Optional[list]] = mapped_column(JSONB)
    last_enriched: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    enrichment_retries: Mapped[Optional[int]] = mapped_column(
        Integer, server_default="0"
    )

    # DOS
    dos_id: Mapped[Optional[str]] = mapped_column(String(30))
    dos_status: Mapped[Optional[str]] = mapped_column(String(20))

    # PE Searcher pipeline
    pipeline_stage: Mapped[Optional[str]] = mapped_column(
        String(30), server_default="research"
    )
    next_follow_up: Mapped[Optional[date]] = mapped_column(Date)
    priority_rank: Mapped[Optional[int]] = mapped_column(Integer, server_default="0")
    outreach_status: Mapped[Optional[str]] = mapped_column(
        String(20), server_default="new"
    )
    notes: Mapped[Optional[str]] = mapped_column(Text)
    tags: Mapped[Optional[list]] = mapped_column(JSONB)
    opportunity_note: Mapped[Optional[str]] = mapped_column(Text)

    # Revenue estimation
    estimated_monthly_revenue: Mapped[Optional[float]] = mapped_column(Float)
    estimated_annual_revenue: Mapped[Optional[float]] = mapped_column(Float)

    # Violations (aggregate)
    violation_count: Mapped[Optional[int]] = mapped_column(Integer, server_default="0")
    violation_class_a: Mapped[Optional[int]] = mapped_column(
        Integer, server_default="0"
    )
    violation_class_b: Mapped[Optional[int]] = mapped_column(
        Integer, server_default="0"
    )
    violation_class_c: Mapped[Optional[int]] = mapped_column(
        Integer, server_default="0"
    )
    violations_per_unit: Mapped[Optional[float]] = mapped_column(
        Float, server_default="0.0"
    )

    # Legacy fields preserved from SQLite
    buildings_json: Mapped[Optional[list]] = mapped_column(
        JSONB, name="buildings"
    )
    building_ids_json: Mapped[Optional[list]] = mapped_column(
        JSONB, name="building_ids"
    )
    contacts_json: Mapped[Optional[list]] = mapped_column(
        JSONB, name="contacts"
    )
    boros_json: Mapped[Optional[list]] = mapped_column(JSONB, name="boros")
    building_types_json: Mapped[Optional[dict]] = mapped_column(
        JSONB, name="building_types"
    )
    building_classes_json: Mapped[Optional[dict]] = mapped_column(
        JSONB, name="building_classes"
    )

    # Relationships
    managed_buildings = relationship("BuildingManagement", back_populates="lead")
    enrichment_results = relationship("EnrichmentResult", back_populates="lead")
    outreach_events = relationship(
        "OutreachEvent",
        primaryjoin="Lead.lead_id == foreign(OutreachEvent.lead_id)",
        back_populates="lead",
    )

    __table_args__ = (
        Index("idx_leads_score", "score"),
        Index("idx_leads_enrichment_status", "enrichment_status"),
        Index("idx_leads_pipeline_stage", "pipeline_stage"),
        Index("idx_leads_normalized_name", "normalized_name"),
        Index("idx_leads_priority_rank", "priority_rank"),
        Index("idx_leads_borough", "primary_borough"),
    )
