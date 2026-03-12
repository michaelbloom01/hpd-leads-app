"""Signal source models -- one table per NYC Open Data source.

Each table stores raw records from a data source with a BBL (or BIN) foreign
key back to buildings. Individual signal views aggregate these into the
building_signal_summary materialized view consumed by the scoring engine.
"""
from datetime import date
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class HPDComplaint(TimestampMixin, Base):
    """HPD Complaints (ygpa-z7cr). BuildingID join, 100% match rate."""

    __tablename__ = "hpd_complaints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    complaint_id: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    bbl: Mapped[str] = mapped_column(
        String(10), ForeignKey("buildings.bbl"), nullable=False
    )
    building_id: Mapped[Optional[str]] = mapped_column(String(20))
    status: Mapped[Optional[str]] = mapped_column(String(30))
    status_date: Mapped[Optional[date]] = mapped_column(Date)
    complaint_type: Mapped[Optional[str]] = mapped_column(Text)
    major_category: Mapped[Optional[str]] = mapped_column(Text)
    minor_category: Mapped[Optional[str]] = mapped_column(Text)
    received_date: Mapped[Optional[date]] = mapped_column(Date)

    __table_args__ = (
        Index("idx_hpd_complaints_bbl", "bbl"),
        Index("idx_hpd_complaints_received", "received_date"),
    )


class ACRISTransaction(TimestampMixin, Base):
    """ACRIS Real Property (bnx9-e6tj + 8h5j-fqxa + 636b-3b5g). BBL via Legals."""

    __tablename__ = "acris_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    bbl: Mapped[str] = mapped_column(
        String(10), ForeignKey("buildings.bbl"), nullable=False
    )
    doc_type: Mapped[Optional[str]] = mapped_column(String(10))
    doc_type_description: Mapped[Optional[str]] = mapped_column(Text)
    recorded_date: Mapped[Optional[date]] = mapped_column(Date)
    doc_amount: Mapped[Optional[float]] = mapped_column(Float)
    party_type: Mapped[Optional[str]] = mapped_column(String(10))
    party_name: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        Index("idx_acris_bbl", "bbl"),
        Index("idx_acris_recorded", "recorded_date"),
        Index("idx_acris_doc_type", "doc_type"),
    )


class DOBPermit(TimestampMixin, Base):
    """DOB Permits (BIS ipu4-2vj7 + DOB NOW rbx6-tga4). BIN join via PAD."""

    __tablename__ = "dob_permits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    bbl: Mapped[str] = mapped_column(
        String(10), ForeignKey("buildings.bbl"), nullable=False
    )
    bin: Mapped[Optional[str]] = mapped_column(String(7))
    permit_type: Mapped[Optional[str]] = mapped_column(String(10))
    permit_subtype: Mapped[Optional[str]] = mapped_column(String(10))
    filing_date: Mapped[Optional[date]] = mapped_column(Date)
    issuance_date: Mapped[Optional[date]] = mapped_column(Date)
    expiration_date: Mapped[Optional[date]] = mapped_column(Date)
    job_description: Mapped[Optional[str]] = mapped_column(Text)
    estimated_cost: Mapped[Optional[float]] = mapped_column(Float)

    __table_args__ = (
        Index("idx_dob_permits_bbl", "bbl"),
        Index("idx_dob_permits_filing", "filing_date"),
    )


class HPDViolation(TimestampMixin, Base):
    """HPD Violations (existing data). Already ingested; new table for PG."""

    __tablename__ = "hpd_violations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    violation_id: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    bbl: Mapped[str] = mapped_column(
        String(10), ForeignKey("buildings.bbl"), nullable=False
    )
    building_id: Mapped[Optional[str]] = mapped_column(String(20))
    violation_class: Mapped[Optional[str]] = mapped_column(String(5))
    inspection_date: Mapped[Optional[date]] = mapped_column(Date)
    approved_date: Mapped[Optional[date]] = mapped_column(Date)
    original_certify_by_date: Mapped[Optional[date]] = mapped_column(Date)
    original_correct_by_date: Mapped[Optional[date]] = mapped_column(Date)
    nov_description: Mapped[Optional[str]] = mapped_column(Text)
    current_status: Mapped[Optional[str]] = mapped_column(String(30))
    current_status_date: Mapped[Optional[date]] = mapped_column(Date)

    __table_args__ = (
        Index("idx_hpd_violations_bbl", "bbl"),
        Index("idx_hpd_violations_inspection", "inspection_date"),
        Index("idx_hpd_violations_class", "violation_class"),
    )


class EnergyGrade(TimestampMixin, Base):
    """LL33 Energy Grades (355w-xvp2). BBL join. ~20% coverage (>=25K sqft)."""

    __tablename__ = "energy_grades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bbl: Mapped[str] = mapped_column(
        String(10), ForeignKey("buildings.bbl"), nullable=False
    )
    grade: Mapped[Optional[str]] = mapped_column(String(2))
    score: Mapped[Optional[float]] = mapped_column(Float)
    year: Mapped[Optional[int]] = mapped_column(Integer)
    property_name: Mapped[Optional[str]] = mapped_column(Text)
    address: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        Index("idx_energy_grades_bbl", "bbl"),
        Index("idx_energy_grades_year", "year"),
        Index("uq_energy_grades_bbl_year_grade", "bbl", "year", "grade", unique=True),
    )


class HPDLitigation(TimestampMixin, Base):
    """HPD Litigation (59kj-x8nc). BBL + BuildingID."""

    __tablename__ = "hpd_litigation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    litigation_id: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False
    )
    bbl: Mapped[str] = mapped_column(
        String(10), ForeignKey("buildings.bbl"), nullable=False
    )
    building_id: Mapped[Optional[str]] = mapped_column(String(20))
    case_type: Mapped[Optional[str]] = mapped_column(String(30))
    case_status: Mapped[Optional[str]] = mapped_column(String(30))
    case_open_date: Mapped[Optional[date]] = mapped_column(Date)
    case_close_date: Mapped[Optional[date]] = mapped_column(Date)
    finding: Mapped[Optional[str]] = mapped_column(Text)
    penalty: Mapped[Optional[float]] = mapped_column(Float)

    __table_args__ = (
        Index("idx_hpd_litigation_bbl", "bbl"),
        Index("idx_hpd_litigation_status", "case_status"),
    )


class EmergencyRepair(TimestampMixin, Base):
    """HPD Emergency Repair Program (24cj-meh5). BuildingID join."""

    __tablename__ = "emergency_repairs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    erp_order_number: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False
    )
    bbl: Mapped[str] = mapped_column(
        String(10), ForeignKey("buildings.bbl"), nullable=False
    )
    building_id: Mapped[Optional[str]] = mapped_column(String(20))
    order_date: Mapped[Optional[date]] = mapped_column(Date)
    repair_type: Mapped[Optional[str]] = mapped_column(Text)
    amount: Mapped[Optional[float]] = mapped_column(Float)
    status: Mapped[Optional[str]] = mapped_column(String(30))

    __table_args__ = (
        Index("idx_emergency_repairs_bbl", "bbl"),
        Index("idx_emergency_repairs_date", "order_date"),
    )


class AEPDesignation(TimestampMixin, Base):
    """AEP Designation (hcir-3275). BuildingID join. Multiplier, not standalone signal."""

    __tablename__ = "aep_designations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bbl: Mapped[str] = mapped_column(
        String(10), ForeignKey("buildings.bbl"), nullable=False
    )
    building_id: Mapped[Optional[str]] = mapped_column(String(20))
    designation_date: Mapped[Optional[date]] = mapped_column(Date)
    removal_date: Mapped[Optional[date]] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")

    __table_args__ = (
        Index("idx_aep_bbl", "bbl"),
        Index("idx_aep_active", "is_active"),
        Index(
            "uq_aep_designations_bbl_designation_removal",
            "bbl",
            "designation_date",
            "removal_date",
            unique=True,
        ),
    )


class EvictionFiling(TimestampMixin, Base):
    """Eviction Filings (6z8x-wfk4 + OCA). BBL via PAD crosswalk."""

    __tablename__ = "eviction_filings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_index_number: Mapped[str] = mapped_column(
        String(30), unique=True, nullable=False
    )
    bbl: Mapped[str] = mapped_column(
        String(10), ForeignKey("buildings.bbl"), nullable=False
    )
    executed_date: Mapped[Optional[date]] = mapped_column(Date)
    marshal_first_name: Mapped[Optional[str]] = mapped_column(String(50))
    marshal_last_name: Mapped[Optional[str]] = mapped_column(String(50))
    eviction_address: Mapped[Optional[str]] = mapped_column(Text)
    borough: Mapped[Optional[str]] = mapped_column(String(20))

    __table_args__ = (
        Index("idx_eviction_filings_bbl", "bbl"),
        Index("idx_eviction_filings_date", "executed_date"),
    )


class FacadeInspection(TimestampMixin, Base):
    """Facade Inspection / FISP / LL11 (xubg-57si). BIN via PAD. ~8% coverage (>6 stories)."""

    __tablename__ = "facade_inspections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bbl: Mapped[str] = mapped_column(
        String(10), ForeignKey("buildings.bbl"), nullable=False
    )
    bin: Mapped[Optional[str]] = mapped_column(String(7))
    filing_date: Mapped[Optional[date]] = mapped_column(Date)
    filing_status: Mapped[Optional[str]] = mapped_column(String(30))
    inspection_date: Mapped[Optional[date]] = mapped_column(Date)
    report_filing_date: Mapped[Optional[date]] = mapped_column(Date)
    cycle: Mapped[Optional[str]] = mapped_column(String(10))

    __table_args__ = (
        Index("idx_facade_inspections_bbl", "bbl"),
        Index("idx_facade_inspections_status", "filing_status"),
        Index(
            "uq_facade_inspections_bbl_cycle_filing",
            "bbl",
            "cycle",
            "filing_date",
            unique=True,
        ),
    )
