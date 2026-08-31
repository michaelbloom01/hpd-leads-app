"""Source-attributed physical buildings alongside the legacy BBL parcel surface."""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class PhysicalBuilding(TimestampMixin, Base):
    __tablename__ = "physical_buildings"

    bin: Mapped[str] = mapped_column(String(7), primary_key=True)
    address: Mapped[str | None] = mapped_column(Text)
    borough: Mapped[str | None] = mapped_column(String(20))
    zip_code: Mapped[str | None] = mapped_column(String(10))
    source_system: Mapped[str] = mapped_column(String(40), nullable=False)
    source_record_key: Mapped[str] = mapped_column(String(80), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BuildingParcelLink(TimestampMixin, Base):
    __tablename__ = "building_parcel_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bin: Mapped[str] = mapped_column(String(7), ForeignKey("physical_buildings.bin"))
    # Historical parcel links can predate the legacy current-parcel projection.
    bbl: Mapped[str] = mapped_column(String(10), nullable=False)
    relationship_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_system: Mapped[str] = mapped_column(String(40), nullable=False)
    source_record_key: Mapped[str] = mapped_column(String(80), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("uq_building_parcel_source", "bin", "bbl", "source_system", "source_record_key", unique=True),
        Index("idx_building_parcel_current", "bbl", "is_current"),
    )


class HPDRegistrationSnapshot(TimestampMixin, Base):
    __tablename__ = "hpd_registration_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    registration_id: Mapped[str] = mapped_column(String(20), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hpd_building_id: Mapped[str | None] = mapped_column(String(20))
    bin: Mapped[str | None] = mapped_column(String(7))
    bbl: Mapped[str | None] = mapped_column(String(10))
    last_registration_date: Mapped[date | None] = mapped_column(Date)
    registration_end_date: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False)
    identity_status: Mapped[str] = mapped_column(String(40), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingestion_job_id: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        Index("uq_hpd_registration_version", "registration_id", "payload_hash", unique=True),
        Index("idx_hpd_registration_building", "hpd_building_id", "is_current"),
        Index("idx_hpd_registration_bin", "bin", "is_current"),
    )


class BuildingIdentityQuarantine(TimestampMixin, Base):
    __tablename__ = "building_identity_quarantine"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_record_key: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    ingestion_job_id: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        Index("uq_building_identity_quarantine", "source_record_key", "payload_hash", "reason", unique=True),
    )


class HPDRefreshRollbackRow(TimestampMixin, Base):
    """Before-images for a reviewed, run-scoped rollback. Never automatic deletion."""

    __tablename__ = "hpd_refresh_rollback_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ingestion_job_id: Mapped[int] = mapped_column(Integer, nullable=False)
    table_name: Mapped[str] = mapped_column(String(50), nullable=False)
    row_key: Mapped[str] = mapped_column(String(160), nullable=False)
    was_existing: Mapped[bool] = mapped_column(Boolean, nullable=False)
    before_payload: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("uq_hpd_refresh_rollback", "ingestion_job_id", "table_name", "row_key", unique=True),
    )
