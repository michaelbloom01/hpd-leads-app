"""BuildingAssessment model — NYC DOF property assessment roll.

One row per tax lot per tax year per roll period. Joins to `buildings` on BBL.

Source: NYC Open Data `8y4t-faws` (Property Valuation and Assessment Data Tax
Classes 1,2,3,4). Five near-identically named datasets on the portal are stale
and thinner — see `src/ingest/dof_client.py` for the full list before switching.

Roll periods: '1' tentative (published January), '3' final (published May).
Both are retained because the tentative-to-final delta on the ASSESSED line is
what shows a Tax Commission reduction.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Index, Integer, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class BuildingAssessment(Base):
    __tablename__ = "building_assessments"

    bbl: Mapped[str] = mapped_column(String(10), primary_key=True)
    tax_year: Mapped[str] = mapped_column(String(4), primary_key=True)
    period: Mapped[str] = mapped_column(String(1), primary_key=True)

    # Market value — DOF's estimate of what the property is worth.
    market_value: Mapped[Optional[float]] = mapped_column(Numeric(16, 2))
    market_value_final: Mapped[Optional[float]] = mapped_column(Numeric(16, 2))
    market_value_tentative: Mapped[Optional[float]] = mapped_column(Numeric(16, 2))
    market_value_prior_year: Mapped[Optional[float]] = mapped_column(Numeric(16, 2))

    # Assessed / taxable — Tax Commission reductions land here, not on market value.
    assessed_total: Mapped[Optional[float]] = mapped_column(Numeric(16, 2))
    assessed_total_tentative: Mapped[Optional[float]] = mapped_column(Numeric(16, 2))
    assessed_total_final: Mapped[Optional[float]] = mapped_column(Numeric(16, 2))
    taxable_total: Mapped[Optional[float]] = mapped_column(Numeric(16, 2))
    exemption_total: Mapped[Optional[float]] = mapped_column(Numeric(16, 2))

    tax_class: Mapped[Optional[str]] = mapped_column(String(2))
    building_class: Mapped[Optional[str]] = mapped_column(String(4))
    owner: Mapped[Optional[str]] = mapped_column(Text)
    zoning: Mapped[Optional[str]] = mapped_column(String(10))

    # Tax certiorari representation.
    protest_code: Mapped[Optional[str]] = mapped_column(String(3))
    protest_code_2: Mapped[Optional[str]] = mapped_column(String(3))
    attorney_group: Mapped[Optional[str]] = mapped_column(String(4))
    attorney_group_2: Mapped[Optional[str]] = mapped_column(String(4))

    units: Mapped[Optional[int]] = mapped_column(Integer)
    residential_units: Mapped[Optional[int]] = mapped_column(Integer)
    gross_sqft: Mapped[Optional[int]] = mapped_column(Integer)
    residential_sqft: Mapped[Optional[int]] = mapped_column(Integer)
    retail_sqft: Mapped[Optional[int]] = mapped_column(Integer)
    office_sqft: Mapped[Optional[int]] = mapped_column(Integer)
    garage_sqft: Mapped[Optional[int]] = mapped_column(Integer)
    stories: Mapped[Optional[float]] = mapped_column(Numeric(7, 2))
    land_area: Mapped[Optional[int]] = mapped_column(Integer)
    year_built: Mapped[Optional[int]] = mapped_column(Integer)
    year_altered_1: Mapped[Optional[int]] = mapped_column(Integer)
    year_altered_2: Mapped[Optional[int]] = mapped_column(Integer)

    # Parent development keys for condo/co-op unit lots.
    coop_number: Mapped[Optional[str]] = mapped_column(String(12))
    condo_number: Mapped[Optional[str]] = mapped_column(String(12))

    apportionment_date: Mapped[Optional[str]] = mapped_column(String(8))
    new_lot: Mapped[Optional[bool]] = mapped_column(Boolean, server_default="false")
    building_in_progress: Mapped[Optional[bool]] = mapped_column(
        Boolean, server_default="false"
    )

    source_dataset: Mapped[Optional[str]] = mapped_column(String(20))
    ingested_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )

    __table_args__ = (
        Index("idx_bassess_bbl", "bbl"),
        Index("idx_bassess_year_period", "tax_year", "period"),
    )

    @property
    def rollup_key(self) -> Optional[str]:
        """Parent development identifier for condo/co-op unit lots."""
        if self.coop_number:
            return f"coop:{self.coop_number}"
        if self.condo_number:
            return f"condo:{self.condo_number}"
        return None

    @property
    def filed_protest(self) -> bool:
        return bool(self.protest_code or self.protest_code_2)
