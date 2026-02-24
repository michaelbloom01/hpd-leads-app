"""Ingestion and enrichment job tracking models."""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class IngestionJob(TimestampMixin, Base):
    """Tracks background job state for ingestion, scoring, and enrichment tasks."""

    __tablename__ = "ingestion_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source: Mapped[Optional[str]] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    total: Mapped[Optional[int]] = mapped_column(Integer)
    processed: Mapped[Optional[int]] = mapped_column(Integer, server_default="0")
    succeeded: Mapped[Optional[int]] = mapped_column(Integer, server_default="0")
    failed: Mapped[Optional[int]] = mapped_column(Integer, server_default="0")
    error: Mapped[Optional[str]] = mapped_column(Text)
    config: Mapped[Optional[dict]] = mapped_column(JSONB)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_ingestion_jobs_status", "status"),
        Index("idx_ingestion_jobs_type", "job_type"),
    )
