"""Contact and enrichment result models."""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class BuildingContact(TimestampMixin, Base):
    """HPD registered contacts for a building (agents, owners, officers)."""

    __tablename__ = "building_contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bbl: Mapped[str] = mapped_column(
        String(10), ForeignKey("buildings.bbl"), nullable=False
    )
    registration_contact_id: Mapped[Optional[str]] = mapped_column(String(20))
    registration_id: Mapped[Optional[str]] = mapped_column(String(20))
    contact_type: Mapped[Optional[str]] = mapped_column(String(30))
    description: Mapped[Optional[str]] = mapped_column(Text)
    corporation_name: Mapped[Optional[str]] = mapped_column(Text)
    first_name: Mapped[Optional[str]] = mapped_column(String(100))
    last_name: Mapped[Optional[str]] = mapped_column(String(100))
    title: Mapped[Optional[str]] = mapped_column(String(100))
    business_address: Mapped[Optional[str]] = mapped_column(Text)
    business_city: Mapped[Optional[str]] = mapped_column(String(100))
    business_state: Mapped[Optional[str]] = mapped_column(String(5))
    business_zip: Mapped[Optional[str]] = mapped_column(String(10))

    building = relationship("Building", back_populates="contacts")

    __table_args__ = (
        Index("idx_building_contacts_bbl", "bbl"),
        Index("idx_building_contacts_type", "contact_type"),
    )


class EnrichmentResult(TimestampMixin, Base):
    """Cached enrichment results from external sources (DOS, web crawl, Google Places)."""

    __tablename__ = "enrichment_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("leads.lead_id"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(20))
    email: Mapped[Optional[str]] = mapped_column(Text)
    website: Mapped[Optional[str]] = mapped_column(Text)
    business_summary: Mapped[Optional[str]] = mapped_column(Text)
    owner_principal: Mapped[Optional[str]] = mapped_column(Text)
    raw_data: Mapped[Optional[dict]] = mapped_column(JSONB)
    fetched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    lead = relationship("Lead", back_populates="enrichment_results")

    __table_args__ = (
        Index("idx_enrichment_results_lead", "lead_id"),
        Index("idx_enrichment_results_source", "source"),
    )
