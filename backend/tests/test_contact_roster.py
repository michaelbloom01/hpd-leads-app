from datetime import datetime, timezone

from src.services.contact_roster import (
    _classify_officer_confidence,
    _dedupe_contacts,
    _get_dos_cache_payload_from_row,
)


def test_classify_officer_marks_resident_board_member():
    hint, is_decision_maker = _classify_officer_confidence(
        officer_address="9 Prospect Park West, Brooklyn, NY 11215",
        building_address="9 Prospect Park West",
        pm_address="575 5th Ave, New York, NY",
    )
    assert hint == "Likely board member (resident)"
    assert is_decision_maker is True


def test_classify_officer_marks_pm_employee():
    hint, is_decision_maker = _classify_officer_confidence(
        officer_address="575 5th Ave, New York, NY 10017",
        building_address="9 Prospect Park West",
        pm_address="575 5th Ave",
    )
    assert hint == "PM company employee"
    assert is_decision_maker is False


def test_dedupe_contacts_keeps_latest_date():
    contacts = [
        {
            "name": "Jane Doe",
            "role": "DOS Officer",
            "source": "NY DOS Filing",
            "source_record_id": "old",
            "as_of_date": "2024-01-01",
            "address": None,
            "confidence_hint": None,
            "is_decision_maker": False,
        },
        {
            "name": "Jane Doe",
            "role": "DOS Officer",
            "source": "NY DOS Filing",
            "source_record_id": "new",
            "as_of_date": "2025-01-01",
            "address": None,
            "confidence_hint": "Likely board member (resident)",
            "is_decision_maker": True,
        },
    ]

    deduped = _dedupe_contacts(contacts)
    assert len(deduped) == 1
    assert deduped[0]["source_record_id"] == "new"
    assert deduped[0]["is_decision_maker"] is True


def test_dos_cache_completed_empty_lookup_is_terminal_no_match():
    payload, status, refreshed_at = _get_dos_cache_payload_from_row(
        {
            "result": {
                "lookup_name": "PARK WEST TENANTS CORP",
                "officers": [],
                "ceo_name": None,
                "ceo_address": None,
                "snapshot_as_of": "2026-03-01",
            },
            "cached_at": datetime.now(timezone.utc),
        }
    )

    assert payload is not None
    assert status == "no_match"
    assert refreshed_at is not None
