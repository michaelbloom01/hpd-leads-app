"""Outreach pipeline and change alert models.

OutreachEvent is polymorphic: it can reference either a lead_id (PE Searcher
deal pipeline) or a bbl (PM Operator building outreach pipeline).
"""
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Date, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class OutreachEvent(TimestampMixin, Base):
    """Polymorphic outreach event: links to lead_id OR bbl."""

    __tablename__ = "outreach_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[Optional[str]] = mapped_column(String(12))
    bbl: Mapped[Optional[str]] = mapped_column(String(10))
    canonical_entity_id: Mapped[Optional[str]] = mapped_column(String(36))
    target_item_id: Mapped[Optional[str]] = mapped_column(String(36))
    stage: Mapped[str] = mapped_column(String(30), nullable=False)
    method: Mapped[Optional[str]] = mapped_column(String(20))
    outcome: Mapped[Optional[str]] = mapped_column(String(30))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    next_follow_up: Mapped[Optional[date]] = mapped_column(Date)
    event_timestamp: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )

    lead = relationship(
        "Lead",
        primaryjoin="OutreachEvent.lead_id == Lead.lead_id",
        foreign_keys="OutreachEvent.lead_id",
        back_populates="outreach_events",
    )

    __table_args__ = (
        Index("idx_outreach_events_lead", "lead_id"),
        Index("idx_outreach_events_bbl", "bbl"),
        Index("idx_outreach_events_target_item", "target_item_id"),
        Index("idx_outreach_events_stage", "stage"),
    )


class ChangeAlert(TimestampMixin, Base):
    """Alerts for data changes: new companies, portfolio shifts, management changes."""

    __tablename__ = "change_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_type: Mapped[str] = mapped_column(String(30), nullable=False)
    lead_id: Mapped[Optional[str]] = mapped_column(String(12))
    bbl: Mapped[Optional[str]] = mapped_column(String(10))
    canonical_entity_id: Mapped[Optional[str]] = mapped_column(String(36))
    target_item_id: Mapped[Optional[str]] = mapped_column(String(36))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[Optional[dict]] = mapped_column(JSONB)
    dismissed: Mapped[bool] = mapped_column(default=False)

    __table_args__ = (
        Index("idx_change_alerts_created", "created_at"),
        Index("idx_change_alerts_dismissed", "dismissed"),
    )
