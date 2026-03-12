"""Building and BuildingManagement models.

Buildings are the atomic unit of the platform. BBL is the universal join key
across all NYC datasets. BuildingManagement is a temporal join table that
tracks which PM company manages each building over time -- management
transitions ARE churn.
"""
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class Building(TimestampMixin, Base):
    __tablename__ = "buildings"

    bbl: Mapped[str] = mapped_column(String(10), primary_key=True)
    bin: Mapped[Optional[str]] = mapped_column(String(7))
    address: Mapped[Optional[str]] = mapped_column(Text)
    borough: Mapped[Optional[str]] = mapped_column(String(20))
    block: Mapped[Optional[str]] = mapped_column(String(5))
    lot: Mapped[Optional[str]] = mapped_column(String(4))
    zip_code: Mapped[Optional[str]] = mapped_column(String(5))
    building_class: Mapped[Optional[str]] = mapped_column(String(4))
    building_type: Mapped[Optional[str]] = mapped_column(String(30))
    unit_count: Mapped[Optional[int]] = mapped_column(Integer)
    year_built: Mapped[Optional[int]] = mapped_column(Integer)
    assessed_value: Mapped[Optional[float]] = mapped_column(Float)

    # PLUTO geographic enrichment
    council_district: Mapped[Optional[str]] = mapped_column(String(10))
    community_board: Mapped[Optional[str]] = mapped_column(String(10))
    census_tract: Mapped[Optional[str]] = mapped_column(String(20))
    nta: Mapped[Optional[str]] = mapped_column(String(10))
    latitude: Mapped[Optional[float]] = mapped_column(Float)
    longitude: Mapped[Optional[float]] = mapped_column(Float)
    coordinate_source: Mapped[Optional[str]] = mapped_column(String(30))
    coordinate_precision: Mapped[Optional[str]] = mapped_column(String(30))
    coordinates_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Churn scoring
    churn_score: Mapped[Optional[float]] = mapped_column(Float)
    churn_category: Mapped[Optional[str]] = mapped_column(String(10))
    churn_breakdown: Mapped[Optional[dict]] = mapped_column(JSONB)
    key_signal: Mapped[Optional[str]] = mapped_column(String(30))
    scoring_config_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("scoring_configs.id")
    )
    signals_available: Mapped[Optional[int]] = mapped_column(Integer)
    coverage_ratio: Mapped[Optional[float]] = mapped_column(Float)
    last_scored_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # PM Operator outreach pipeline
    outreach_status: Mapped[Optional[str]] = mapped_column(
        String(20), server_default="none"
    )
    outreach_priority: Mapped[Optional[int]] = mapped_column(Integer)
    next_outreach_date: Mapped[Optional[date]] = mapped_column(Date)

    # Relationships
    management_records = relationship(
        "BuildingManagement", back_populates="building", lazy="selectin"
    )
    score_history = relationship("BuildingScoreHistory", back_populates="building")
    contacts = relationship("BuildingContact", back_populates="building")

    __table_args__ = (
        Index("idx_buildings_churn_score", "churn_score"),
        Index("idx_buildings_scoring_config", "scoring_config_id"),
        Index("idx_buildings_borough", "borough"),
        Index("idx_buildings_outreach_status", "outreach_status"),
        Index("idx_buildings_coordinates", "latitude", "longitude"),
    )


class BuildingManagement(TimestampMixin, Base):
    """Temporal link between buildings and the PM companies (leads) that manage them."""

    __tablename__ = "building_management"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bbl: Mapped[str] = mapped_column(
        String(10), ForeignKey("buildings.bbl"), nullable=False
    )
    lead_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("leads.lead_id"), nullable=False
    )
    role: Mapped[Optional[str]] = mapped_column(String(30))
    registration_start: Mapped[Optional[date]] = mapped_column(Date)
    registration_end: Mapped[Optional[date]] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, server_default="true")

    building = relationship("Building", back_populates="management_records")
    lead = relationship("Lead", back_populates="managed_buildings")

    __table_args__ = (
        Index("idx_bm_bbl", "bbl"),
        Index("idx_bm_lead_id", "lead_id"),
        Index("idx_bm_is_current", "is_current"),
    )
