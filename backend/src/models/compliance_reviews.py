"""Append-only internal review history, separate from agency record status."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ComplianceReview(Base):
    __tablename__ = "compliance_reviews"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    record_id: Mapped[str] = mapped_column(String(32), ForeignKey("compliance_records.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_label: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("record_id", "version", name="uq_compliance_review_version"),
        CheckConstraint("version > 0", name="ck_compliance_review_version"),
        CheckConstraint("state IN ('new','in_review','verified_for_briefing','monitoring','closed_internally','dismissed','source_mismatch')", name="ck_compliance_review_state"),
    )
