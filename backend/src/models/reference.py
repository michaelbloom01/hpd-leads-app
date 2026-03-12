"""Reference data models.

PAD (Property Address Directory) provides the authoritative BIN-to-BBL
crosswalk needed to join DOB, eviction, and facade data to buildings.
"""
from typing import Optional

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class PADAddress(TimestampMixin, Base):
    """DCP Property Address Directory. BIN-to-BBL crosswalk. Refreshed quarterly."""

    __tablename__ = "pad_addresses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bin: Mapped[str] = mapped_column(String(7), nullable=False)
    bbl: Mapped[str] = mapped_column(String(10), nullable=False)
    address: Mapped[Optional[str]] = mapped_column(Text)
    borough: Mapped[Optional[str]] = mapped_column(String(20))

    __table_args__ = (
        Index("idx_pad_bin", "bin"),
        Index("idx_pad_bbl", "bbl"),
        Index("uq_pad_addresses_bin_bbl_address", "bin", "bbl", "address", unique=True),
    )
