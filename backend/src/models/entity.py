"""Canonical entity and proposal models for acquisition-grade identity."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class CanonicalEntity(TimestampMixin, Base):
    __tablename__ = "canonical_entities"

    canonical_entity_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    normalized_name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(Text)
    entity_type: Mapped[Optional[str]] = mapped_column(String(30), server_default="pm_company")
    status: Mapped[Optional[str]] = mapped_column(String(20), server_default="proposed")
    confidence_score: Mapped[Optional[float]] = mapped_column(Float)
    profile: Mapped[Optional[dict]] = mapped_column(JSONB)

    __table_args__ = (
        Index("idx_canonical_entities_status", "status"),
    )


class CanonicalEntityAlias(TimestampMixin, Base):
    __tablename__ = "canonical_entity_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_entity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("canonical_entities.canonical_entity_id", ondelete="CASCADE"), nullable=False
    )
    alias_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_alias: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[Optional[str]] = mapped_column(String(30))
    confidence_score: Mapped[Optional[float]] = mapped_column(Float)

    __table_args__ = (
        Index("idx_canonical_entity_aliases_entity", "canonical_entity_id"),
        Index("idx_canonical_entity_aliases_normalized", "normalized_alias"),
        Index("uq_canonical_entity_alias", "canonical_entity_id", "normalized_alias", unique=True),
    )


class CanonicalEntityLead(TimestampMixin, Base):
    __tablename__ = "canonical_entity_leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_entity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("canonical_entities.canonical_entity_id", ondelete="CASCADE"), nullable=False
    )
    lead_id: Mapped[str] = mapped_column(String(12), ForeignKey("leads.lead_id", ondelete="CASCADE"), nullable=False)
    relationship_type: Mapped[Optional[str]] = mapped_column(String(30), server_default="candidate")
    is_primary: Mapped[bool] = mapped_column(Boolean, server_default="false")
    confidence_score: Mapped[Optional[float]] = mapped_column(Float)
    evidence: Mapped[Optional[dict]] = mapped_column(JSONB)

    __table_args__ = (
        Index("idx_canonical_entity_leads_entity", "canonical_entity_id"),
        Index("idx_canonical_entity_leads_lead", "lead_id"),
        Index("uq_canonical_entity_lead", "canonical_entity_id", "lead_id", unique=True),
    )


class CanonicalEntityBuilding(TimestampMixin, Base):
    __tablename__ = "canonical_entity_buildings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_entity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("canonical_entities.canonical_entity_id", ondelete="CASCADE"), nullable=False
    )
    bbl: Mapped[str] = mapped_column(String(10), ForeignKey("buildings.bbl", ondelete="CASCADE"), nullable=False)
    source: Mapped[Optional[str]] = mapped_column(String(30), server_default="proposal")
    confidence_score: Mapped[Optional[float]] = mapped_column(Float)
    evidence: Mapped[Optional[dict]] = mapped_column(JSONB)

    __table_args__ = (
        Index("idx_canonical_entity_buildings_entity", "canonical_entity_id"),
        Index("idx_canonical_entity_buildings_bbl", "bbl"),
        Index("uq_canonical_entity_building", "canonical_entity_id", "bbl", unique=True),
    )


class CanonicalEntityMatchProposal(TimestampMixin, Base):
    __tablename__ = "canonical_entity_match_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proposal_key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    canonical_entity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("canonical_entities.canonical_entity_id", ondelete="CASCADE"), nullable=False
    )
    lead_id: Mapped[Optional[str]] = mapped_column(String(12), ForeignKey("leads.lead_id", ondelete="SET NULL"))
    bucket: Mapped[str] = mapped_column(String(30), nullable=False)
    proposal_status: Mapped[Optional[str]] = mapped_column(String(20), server_default="proposed")
    safe_to_execute: Mapped[bool] = mapped_column(Boolean, server_default="false")
    reasons: Mapped[Optional[dict]] = mapped_column(JSONB)
    evidence: Mapped[Optional[dict]] = mapped_column(JSONB)

    __table_args__ = (
        Index("idx_canonical_entity_match_proposals_entity", "canonical_entity_id"),
        Index("idx_canonical_entity_match_proposals_lead", "lead_id"),
        Index("idx_canonical_entity_match_proposals_bucket", "bucket"),
    )
