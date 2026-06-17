"""Address alias helpers for NYC building search.

NYC source systems often describe one building with many valid addresses:
range records, corner addresses, vanity addresses, and source-specific
abbreviations. This module keeps the normalization/range logic shared across
ingestion, backfills, and API search.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy import text


MAX_GENERATED_RANGE_ALIASES = 60


@dataclass(frozen=True)
class AddressAlias:
    display_address: str
    normalized_address: str
    source: str
    confidence_score: float
    is_primary: bool = False
    source_record_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def normalize_address_for_match(value: str | None) -> str:
    """Normalize an address for exact alias lookup while preserving ranges."""
    if not value:
        return ""
    normalized = str(value).upper()
    normalized = normalized.replace(",", " ")
    normalized = re.sub(r"[.#]", " ", normalized)
    normalized = re.sub(r"\s*-\s*", "-", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def address_search_patterns(query: str) -> list[str]:
    """Return ILIKE patterns for common NYC address spelling variants."""
    q = normalize_address_for_match(query)
    ordinal_stripped = re.sub(r"\b(\d+)(?:ST|ND|RD|TH)\b", r"\1", q)
    direction_map = {"E": "EAST", "W": "WEST", "N": "NORTH", "S": "SOUTH"}
    direction_reverse = {v: k for k, v in direction_map.items()}
    type_map = {
        "ST": "STREET",
        "AVE": "AVENUE",
        "AV": "AVENUE",
        "BLVD": "BOULEVARD",
        "DR": "DRIVE",
        "PL": "PLACE",
        "CT": "COURT",
        "LN": "LANE",
        "RD": "ROAD",
        "PKWY": "PARKWAY",
        "HWY": "HIGHWAY",
        "SQ": "SQUARE",
        "TER": "TERRACE",
        "CIR": "CIRCLE",
    }
    type_reverse = {v: k for k, v in type_map.items()}

    def expand_once(text_value: str) -> set[str]:
        variants = {text_value}
        words = text_value.split()
        for index, word in enumerate(words):
            swaps: list[str] = []
            if word in direction_map:
                swaps.append(direction_map[word])
            if word in direction_reverse:
                swaps.append(direction_reverse[word])
            if word in type_map:
                swaps.append(type_map[word])
            if word in type_reverse:
                swaps.append(type_reverse[word])
            for swap in swaps:
                variants.add(" ".join(words[:index] + [swap] + words[index + 1 :]))
        return variants

    patterns: set[str] = set()
    for base in {q, ordinal_stripped}:
        if base:
            patterns.update(f"%{variant}%" for variant in expand_once(base))
    return sorted(patterns)


def address_alias_search_sql(alias: str, query: str, param_prefix: str) -> tuple[str, dict[str, str]]:
    """Build a parameterized SQL predicate for alias address search."""
    normalized = normalize_address_for_match(query)
    patterns = address_search_patterns(query)
    params: dict[str, str] = {f"{param_prefix}_normalized": normalized}
    clauses = [f"{alias}.normalized_address = :{param_prefix}_normalized"]
    for index, pattern in enumerate(patterns):
        key = f"{param_prefix}_p{index}"
        params[key] = pattern
        clauses.append(f"{alias}.normalized_address ILIKE :{key}")
        clauses.append(f"{alias}.display_address ILIKE :{key}")
    return "(" + " OR ".join(clauses) + ")", params


async def address_alias_table_exists(session) -> bool:
    result = await session.execute(
        text("SELECT to_regclass('building_address_aliases') IS NOT NULL AS alias_table_exists")
    )
    row = result.first()
    if not row:
        return False
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return bool(mapping.get("alias_table_exists"))
    if hasattr(row, "alias_table_exists"):
        return bool(row.alias_table_exists)
    try:
        return bool(row[0])
    except Exception:
        return False


def address_alias_table_exists_sync(session) -> bool:
    result = session.execute(
        text("SELECT to_regclass('building_address_aliases') IS NOT NULL AS alias_table_exists")
    )
    row = result.first()
    if not row:
        return False
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return bool(mapping.get("alias_table_exists"))
    if hasattr(row, "alias_table_exists"):
        return bool(row.alias_table_exists)
    try:
        return bool(row[0])
    except Exception:
        return False


def build_hpd_registration_aliases(
    *,
    house_number: str | None,
    low_house_number: str | None,
    high_house_number: str | None,
    street_name: str | None,
    registration_id: str | None = None,
    hpd_building_id: str | None = None,
) -> list[AddressAlias]:
    """Generate aliases from one HPD registration row.

    Numeric low/high ranges generate bounded same-parity house-number aliases,
    so 4-12 HANOVER SQUARE includes 10 HANOVER SQUARE without creating
    runaway aliases for very large or non-numeric ranges.
    """
    street = normalize_address_for_match(street_name)
    if not street:
        return []

    aliases: dict[tuple[str, str], AddressAlias] = {}

    def add(
        display_address: str,
        *,
        source: str,
        confidence_score: float,
        is_primary: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        display = normalize_address_for_match(display_address)
        if not display:
            return
        key = (display, source)
        aliases[key] = AddressAlias(
            display_address=display,
            normalized_address=normalize_address_for_match(display),
            source=source,
            confidence_score=confidence_score,
            is_primary=is_primary,
            source_record_id=registration_id,
            metadata={
                "registration_id": registration_id,
                "hpd_building_id": hpd_building_id,
                **(metadata or {}),
            },
        )

    house = normalize_address_for_match(house_number)
    low = normalize_address_for_match(low_house_number)
    high = normalize_address_for_match(high_house_number)

    if house:
        add(
            f"{house} {street}",
            source="hpd_registration_house_number",
            confidence_score=0.95,
            is_primary=True,
        )

    if low and high and low != high:
        add(
            f"{low}-{high} {street}",
            source="hpd_registration_range",
            confidence_score=0.9,
            metadata={"low_house_number": low, "high_house_number": high},
        )
        for value in _generated_house_numbers(low, high):
            add(
                f"{value} {street}",
                source="hpd_registration_range_member",
                confidence_score=0.85,
                metadata={"low_house_number": low, "high_house_number": high},
            )
    elif low:
        add(
            f"{low} {street}",
            source="hpd_registration_low_house_number",
            confidence_score=0.9,
        )
    elif high:
        add(
            f"{high} {street}",
            source="hpd_registration_high_house_number",
            confidence_score=0.85,
        )

    return sorted(aliases.values(), key=lambda alias: (not alias.is_primary, alias.source, alias.display_address))


def _generated_house_numbers(low: str, high: str) -> Iterable[str]:
    low_num = _parse_plain_house_number(low)
    high_num = _parse_plain_house_number(high)
    if low_num is None or high_num is None or high_num < low_num:
        return []
    step = 2 if low_num % 2 == high_num % 2 else 1
    count = ((high_num - low_num) // step) + 1
    if count > MAX_GENERATED_RANGE_ALIASES:
        return []
    return [str(value) for value in range(low_num, high_num + 1, step)]


def _parse_plain_house_number(value: str) -> int | None:
    normalized = normalize_address_for_match(value)
    if not re.fullmatch(r"\d+", normalized):
        return None
    return int(normalized)


def upsert_building_address_aliases_sync(
    session,
    *,
    bbl: str,
    bin_value: str | None,
    aliases: Iterable[AddressAlias],
) -> int:
    """Upsert aliases in a synchronous SQLAlchemy Session."""
    rows = []
    for alias in aliases:
        rows.append(
            {
                "bbl": bbl,
                "bin": bin_value,
                "display_address": alias.display_address,
                "normalized_address": alias.normalized_address,
                "source": alias.source,
                "source_record_id": alias.source_record_id,
                "confidence_score": alias.confidence_score,
                "is_primary": alias.is_primary,
                "metadata": json.dumps(alias.metadata or {}),
            }
        )
    if not rows:
        return 0
    session.execute(
        text(
            """
            INSERT INTO building_address_aliases (
                bbl, bin, display_address, normalized_address, source,
                source_record_id, confidence_score, is_primary, metadata,
                created_at, updated_at
            ) VALUES (
                :bbl, :bin, :display_address, :normalized_address, :source,
                :source_record_id, :confidence_score, :is_primary,
                CAST(:metadata AS JSONB), NOW(), NOW()
            )
            ON CONFLICT (bbl, normalized_address, source)
            DO UPDATE SET
                bin = COALESCE(EXCLUDED.bin, building_address_aliases.bin),
                display_address = EXCLUDED.display_address,
                source_record_id = COALESCE(EXCLUDED.source_record_id, building_address_aliases.source_record_id),
                confidence_score = GREATEST(
                    building_address_aliases.confidence_score,
                    EXCLUDED.confidence_score
                ),
                is_primary = building_address_aliases.is_primary OR EXCLUDED.is_primary,
                metadata = COALESCE(building_address_aliases.metadata, '{}'::jsonb) || EXCLUDED.metadata,
                updated_at = NOW()
            """
        ),
        rows,
    )
    return len(rows)
