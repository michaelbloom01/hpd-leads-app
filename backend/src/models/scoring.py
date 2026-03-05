"""Scoring configuration and score history models.

ScoringConfig stores user-configurable churn scoring weights (PM Operator persona).
BuildingScoreHistory provides full auditability: every scoring run is recorded
with the exact signal values and data vintages used.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class ScoringConfig(TimestampMixin, Base):
    __tablename__ = "scoring_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="false")
    is_preset: Mapped[bool] = mapped_column(Boolean, server_default="false")
    weights: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(String(100))

    buildings = relationship("Building", backref="scoring_config")
    score_history = relationship("BuildingScoreHistory", back_populates="scoring_config")

    __table_args__ = (
        Index("idx_scoring_configs_active", "is_active"),
        CheckConstraint(
            """
            weights ? 'ownership_change'
            AND weights ? 'complaint_spike'
            AND weights ? 'violation_trend'
            AND weights ? 'energy_grade_drop'
            AND weights ? 'dob_permits'
            AND weights ? 'hpd_litigation'
            AND weights ? 'emergency_repairs'
            AND weights ? 'building_size'
            AND weights ? 'eviction_activity'
            AND weights ? 'facade_status'
            """,
            name="ck_scoring_weights_keys",
        ),
    )


class BuildingScoreHistory(TimestampMixin, Base):
    """Append-only score history for audit trail and trend analysis."""

    __tablename__ = "building_score_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bbl: Mapped[str] = mapped_column(
        String(10), ForeignKey("buildings.bbl"), nullable=False
    )
    churn_score: Mapped[float] = mapped_column(Float, nullable=False)
    churn_category: Mapped[str] = mapped_column(String(10), nullable=False)
    churn_breakdown: Mapped[Optional[dict]] = mapped_column(JSONB)
    scoring_config_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("scoring_configs.id"), nullable=False
    )
    signal_snapshot: Mapped[Optional[dict]] = mapped_column(JSONB)
    data_vintages: Mapped[Optional[dict]] = mapped_column(JSONB)
    scored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    building = relationship("Building", back_populates="score_history")
    scoring_config = relationship("ScoringConfig", back_populates="score_history")

    __table_args__ = (
        Index("idx_score_history_bbl", "bbl"),
        Index("idx_score_history_scored_at", "scored_at"),
    )
