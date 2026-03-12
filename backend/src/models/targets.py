"""Target list, thesis, and matching models for acquisition workflows."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class AcquisitionThesis(TimestampMixin, Base):
    __tablename__ = "acquisition_theses"

    thesis_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    criteria: Mapped[Optional[dict]] = mapped_column(JSONB)
    is_default: Mapped[bool] = mapped_column(Boolean, server_default="false")


class TargetList(TimestampMixin, Base):
    __tablename__ = "target_lists"

    target_list_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    targeting_mode: Mapped[Optional[str]] = mapped_column(String(30), server_default="both")
    thesis_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("acquisition_theses.thesis_id", ondelete="SET NULL")
    )
    source_notes: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        Index("idx_target_lists_user", "user_id"),
    )


class TargetListItem(TimestampMixin, Base):
    __tablename__ = "target_list_items"

    target_item_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    target_list_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("target_lists.target_list_id", ondelete="CASCADE"), nullable=False
    )
    canonical_entity_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("canonical_entities.canonical_entity_id", ondelete="SET NULL")
    )
    matched_lead_id: Mapped[Optional[str]] = mapped_column(String(12), ForeignKey("leads.lead_id", ondelete="SET NULL"))
    company_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False)
    established: Mapped[Optional[str]] = mapped_column(String(40))
    portfolio_estimate: Mapped[Optional[str]] = mapped_column(String(120))
    units_estimate: Mapped[Optional[str]] = mapped_column(String(120))
    geography: Mapped[Optional[str]] = mapped_column(Text)
    ownership: Mapped[Optional[str]] = mapped_column(Text)
    key_principals: Mapped[Optional[str]] = mapped_column(Text)
    condo_focus: Mapped[Optional[str]] = mapped_column(Text)
    website: Mapped[Optional[str]] = mapped_column(Text)
    website_domain: Mapped[Optional[str]] = mapped_column(String(255))
    phone: Mapped[Optional[str]] = mapped_column(String(30))
    phone_normalized: Mapped[Optional[str]] = mapped_column(String(20))
    address: Mapped[Optional[str]] = mapped_column(Text)
    tier: Mapped[Optional[str]] = mapped_column(String(20))
    acquisition_fit_notes: Mapped[Optional[str]] = mapped_column(Text)
    risk_flag: Mapped[Optional[str]] = mapped_column(Text)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    raw_profile: Mapped[Optional[dict]] = mapped_column(JSONB)
    match_status: Mapped[Optional[str]] = mapped_column(String(20), server_default="unprocessed")
    thesis_score: Mapped[Optional[float]] = mapped_column(Float)
    thesis_summary: Mapped[Optional[str]] = mapped_column(Text)
    thesis_breakdown: Mapped[Optional[dict]] = mapped_column(JSONB)
    pipeline_stage: Mapped[Optional[str]] = mapped_column(String(30), server_default="research")
    outreach_status: Mapped[Optional[str]] = mapped_column(String(20), server_default="new")
    priority_rank: Mapped[Optional[int]] = mapped_column(Integer, server_default="0")
    next_follow_up: Mapped[Optional[date]] = mapped_column(Date)
    last_contacted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_target_list_items_list", "target_list_id"),
        Index("idx_target_list_items_match_status", "match_status"),
        Index("idx_target_list_items_matched_lead", "matched_lead_id"),
        Index("idx_target_list_items_canonical_entity", "canonical_entity_id"),
        Index("idx_target_list_items_next_follow_up", "next_follow_up"),
        Index("idx_target_list_items_normalized_name", "normalized_name"),
    )


class TargetMatch(TimestampMixin, Base):
    __tablename__ = "target_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target_item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("target_list_items.target_item_id", ondelete="CASCADE"), nullable=False
    )
    lead_id: Mapped[Optional[str]] = mapped_column(String(12), ForeignKey("leads.lead_id", ondelete="SET NULL"))
    canonical_entity_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("canonical_entities.canonical_entity_id", ondelete="SET NULL")
    )
    match_type: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence_score: Mapped[Optional[float]] = mapped_column(Float)
    selected: Mapped[bool] = mapped_column(Boolean, server_default="false")
    reasons: Mapped[Optional[dict]] = mapped_column(JSONB)

    __table_args__ = (
        Index("idx_target_matches_item", "target_item_id"),
        Index("idx_target_matches_lead", "lead_id"),
        Index("idx_target_matches_selected", "selected"),
    )
