from src.services.contact_roster import (
    _classify_officer_confidence,
    _dedupe_contacts,
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
