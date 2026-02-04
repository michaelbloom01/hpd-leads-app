"""Transform module - Normalize and aggregate data."""
from .normalize import normalize_building, normalize_name, normalize_phone
from .aggregate import aggregate_to_leads

__all__ = ["normalize_building", "normalize_name", "normalize_phone", "aggregate_to_leads"]
