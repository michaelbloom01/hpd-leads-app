"""Source-backed compliance evidence. BIN and BBL remain separate namespaces."""

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

JSON_VALUE = JSON().with_variant(JSONB(), "postgresql")


class ComplianceRecord(Base):
    __tablename__ = "compliance_records"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_system: Mapped[str] = mapped_column(String(40), nullable=False)
    source_record_key: Mapped[str] = mapped_column(String(160), nullable=False)
    record_type: Mapped[str] = mapped_column(String(30), nullable=False)
    bin: Mapped[str | None] = mapped_column(String(7))
    bbl: Mapped[str | None] = mapped_column(String(10))
    address: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    violation_type: Mapped[str | None] = mapped_column(String(80))
    device_type: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str | None] = mapped_column(String(80))
    issue_date: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(Text)
    identity_status: Mapped[str] = mapped_column(String(40), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(40), nullable=False)
    ingestion_run_id: Mapped[str] = mapped_column(String(80), nullable=False)
    raw_payload: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "source_system", "source_record_key", name="uq_compliance_source_record"
        ),
        Index("ix_compliance_records_bin", "bin"),
        Index("ix_compliance_records_bbl", "bbl"),
    )


class ComplianceObservation(Base):
    __tablename__ = "compliance_observations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    record_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("compliance_records.id"), nullable=False
    )
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ingestion_run_id: Mapped[str] = mapped_column(String(80), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(40), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    raw_payload: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "record_id", "ingestion_run_id", name="uq_compliance_observation_run"
        ),
    )


class ComplianceSourceCheck(Base):
    """Last complete source check per BIN, including successful empty checks."""

    __tablename__ = "compliance_source_checks"

    source_system: Mapped[str] = mapped_column(String(40), primary_key=True)
    bin: Mapped[str] = mapped_column(String(7), primary_key=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    records_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ingestion_run_id: Mapped[str] = mapped_column(String(80), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)


class ComplianceBalanceObservation(Base):
    """Attributed BIN/category portal evidence. No statutory estimate is a balance."""

    __tablename__ = "compliance_balance_observations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    bin: Mapped[str] = mapped_column(String(7), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    scope: Mapped[str] = mapped_column(String(30), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_timestamp_raw: Mapped[str] = mapped_column(String(200), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reviewer: Mapped[str] = mapped_column(String(200), nullable=False)
    evidence_note: Mapped[str] = mapped_column(Text, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    __table_args__ = (
        Index("ix_compliance_balances_scope", "bin", "category", "observed_at"),
        CheckConstraint(
            "amount_cents >= 0 AND amount_cents <= 1000000000000",
            name="ck_compliance_balance_amount",
        ),
        CheckConstraint(
            "category = 'LL152' AND scope = 'bin_category'",
            name="ck_compliance_balance_scope",
        ),
    )
