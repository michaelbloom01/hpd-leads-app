from src.services.address_aliases import (
    address_search_patterns,
    build_hpd_registration_aliases,
)


def test_hpd_range_aliases_include_same_parity_addresses():
    aliases = build_hpd_registration_aliases(
        house_number="4",
        low_house_number="4",
        high_house_number="12",
        street_name="Hanover Square",
        registration_id="142641",
        hpd_building_id="22599",
    )

    displays = {alias.display_address for alias in aliases}

    assert "4 HANOVER SQUARE" in displays
    assert "4-12 HANOVER SQUARE" in displays
    assert "10 HANOVER SQUARE" in displays
    assert "11 HANOVER SQUARE" not in displays


def test_address_search_patterns_expand_square_abbreviation():
    patterns = set(address_search_patterns("10 Hanover Sq"))

    assert "%10 HANOVER SQ%" in patterns
    assert "%10 HANOVER SQUARE%" in patterns
