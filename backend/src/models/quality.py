"""Data quality tracking model."""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class DataQualityLog(TimestampMixin, Base):
    """Per-source ingestion run summary for data quality monitoring."""

    __tablename__ = "data_quality_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_name: Mapped[str] = mapped_column(String(30), nullable=False)
    job_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("ingestion_jobs.id")
    )
    run_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    records_fetched: Mapped[int] = mapped_column(Integer, nullable=False)
    records_matched: Mapped[int] = mapped_column(Integer, nullable=False)
    records_rejected: Mapped[Optional[int]] = mapped_column(Integer, server_default="0")
    records_inserted: Mapped[Optional[int]] = mapped_column(Integer, server_default="0")
    match_rate: Mapped[float] = mapped_column(Float, nullable=False)
    volume_anomaly: Mapped[bool] = mapped_column(Boolean, server_default="false")
    notes: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        Index("idx_dql_source", "source_name"),
        Index("idx_dql_timestamp", "run_timestamp"),
    )
