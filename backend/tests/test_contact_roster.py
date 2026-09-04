import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.services.contact_roster import (
    _classify_dos_chairman,
    _dedupe_contacts,
    _detect_board_role,
    _get_dos_cache_payload_from_row,
    _officer_address_hint,
    get_building_contacts,
    get_building_contacts_sync,
)


def test_exact_dos_chairman_is_board_head_for_coop():
    hint, is_decision_maker, board_role = _classify_dos_chairman(
        {
            "lookup_name": "PARK WEST TENANTS CORP.",
            "entity_name": "PARK WEST TENANTS CORP",
        },
        is_condo_coop=True,
    )
    assert hint == "NY DOS names chairman/CEO candidate (exact entity match; board title needs confirmation)"
    assert is_decision_maker is True
    assert board_role == "Board Head"


def test_possible_dos_entity_match_does_not_become_board_head():
    hint, is_decision_maker, board_role = _classify_dos_chairman(
        {
            "lookup_name": "PARK WEST TENANTS CORP",
            "entity_name": "WEST TENANTS CORP",
        },
        is_condo_coop=True,
    )
    assert "needs review" in hint
    assert is_decision_maker is False
    assert board_role is None


def test_building_address_match_is_only_an_approximate_street_observation():
    hint = _officer_address_hint(
        officer_address="9 Prospect Park West, Brooklyn, NY 11215",
        building_address="9 Prospect Park West",
        pm_address="575 5th Ave, New York, NY",
    )
    assert hint == "Approximate street match to building address"


def test_agent_address_match_does_not_assert_employment():
    hint = _officer_address_hint(
        officer_address="575 5th Ave, New York, NY 10017",
        building_address="9 Prospect Park West",
        pm_address="575 5th Ave",
    )
    assert hint == "Approximate street match to registered agent address"


@pytest.mark.parametrize("title", ["Vice President", "Board Vice President", "Vice-President", "VP"])
def test_vice_president_is_an_officer_candidate_not_board_head(title):
    assert _detect_board_role("DOS Officer", title) == "Board Officer"


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def first(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class _ReadOnlyConnection:
    def __init__(self, hpd_rows, payload=None):
        self.hpd_rows = hpd_rows
        self.payload = payload

    def execute(self, statement, parameters):
        sql = str(statement).strip()
        assert sql.startswith("SELECT")
        if "FROM building_contacts" in sql:
            return _Rows([SimpleNamespace(_mapping=row) for row in self.hpd_rows])
        if "FROM buildings" in sql:
            return _Rows([("9 Prospect Park West", "Co-op", "C6")])
        if "FROM dos_cache" in sql:
            return _Rows([SimpleNamespace(_mapping={
                "result": self.payload,
                "cached_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
            })] if self.payload else [])
        raise AssertionError(f"Unexpected query: {sql}")


class _ReadOnlyAsyncSession(_ReadOnlyConnection):
    async def execute(self, statement, parameters):
        return super().execute(statement, parameters)


def _both_rosters(hpd_rows, payload=None):
    sync = get_building_contacts_sync(_ReadOnlyConnection(hpd_rows, payload), "1007160055")
    async_result = asyncio.run(get_building_contacts(
        _ReadOnlyAsyncSession(hpd_rows, payload), "1007160055",
    ))
    assert async_result == sync
    return sync


def _hpd_row(**overrides):
    return {
        "id": 1,
        "registration_contact_id": "14050904",
        "contact_type": "Agent",
        "corporation_name": "LIVINGSTON MANAGEMENT SERVICES",
        "first_name": "TARA",
        "last_name": "DEXTER",
        "title": None,
        "business_address": "225 WEST 35TH STREET",
        "business_city": "New York",
        "business_state": "NY",
        "business_zip": "10001",
        "updated_at": datetime(2026, 8, 31, tzinfo=timezone.utc),
        **overrides,
    }


def test_async_sync_hpd_projection_preserves_company_person_and_observation_date():
    contacts, metadata = _both_rosters([_hpd_row()])
    agent = contacts[0]
    assert agent["name"] == agent["company_name"] == "LIVINGSTON MANAGEMENT SERVICES"
    assert agent["person_name"] == "TARA DEXTER"
    assert agent["source_title"] is None
    assert agent["source_record_id"] == "14050904"
    assert agent["source_url"].endswith("registrationcontactid=14050904")
    assert agent["source_observed_at"] == agent["as_of_date"] == "2026-08-31"
    assert agent["publication_date"] is None
    assert metadata["management_company"] == "LIVINGSTON MANAGEMENT SERVICES"


def test_person_only_agent_is_not_a_management_company_and_blank_names_are_skipped():
    person_only = _hpd_row(corporation_name=None)
    contacts, metadata = _both_rosters([
        person_only,
        _hpd_row(id=2, corporation_name=None, first_name=None, last_name=None),
    ])
    assert len(contacts) == 1
    assert contacts[0]["name"] == contacts[0]["person_name"] == "TARA DEXTER"
    assert contacts[0]["company_name"] is None
    assert metadata["management_company"] is None

    _, metadata = _both_rosters([person_only, _hpd_row(id=3)])
    assert metadata["management_company"] == "LIVINGSTON MANAGEMENT SERVICES"


def test_hpd_title_hypothesis_keeps_source_title_and_unverified_status():
    contacts, _ = _both_rosters([
        _hpd_row(contact_type="Officer", corporation_name=None, title="Vice President"),
        _hpd_row(id=2, contact_type="HeadOfficer", corporation_name=None, title="President"),
    ])
    by_role = {contact["role"]: contact for contact in contacts}
    assert by_role["Officer"]["source_title"] == "Vice President"
    assert by_role["Officer"]["board_role"] == "Board Officer"
    assert by_role["Officer"]["board_role_status"] == "unverified"
    assert by_role["HeadOfficer"]["board_role"] is None


def test_same_company_retains_each_person_and_uses_their_latest_source_record():
    contacts, _ = _both_rosters([
        _hpd_row(),
        _hpd_row(id=2, registration_contact_id="other-person", first_name="ALEX", last_name="SMITH"),
        _hpd_row(
            id=3, registration_contact_id="tara-newer", first_name="Tara", last_name="Dexter",
            updated_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        ),
    ])
    assert len(contacts) == 2
    by_person = {contact["person_name"].upper(): contact for contact in contacts}
    assert by_person["TARA DEXTER"]["source_record_id"] == "tara-newer"
    assert by_person["ALEX SMITH"]["source_record_id"] == "other-person"


def test_dos_candidates_survive_without_address_based_role_promotion():
    contacts, _ = _both_rosters([_hpd_row()], {
        "lookup_name": "PARK WEST TENANTS CORP.",
        "entity_name": "PARK WEST TENANTS CORP",
        "dos_id": "12345",
        "ceo_name": "CHAIR CANDIDATE",
        "snapshot_as_of": "2026-08-01",
        "officers": [
            {"name": "CHAIR CANDIDATE"},
            {"name": "VICE PRESIDENT CANDIDATE", "title": "Vice President"},
            {"name": "BUILDING ADDRESS CONTACT", "address": "9 Prospect Park West"},
            {"name": "AGENT ADDRESS CONTACT", "address": "225 WEST 35TH STREET"},
        ],
    })
    by_name = {contact["name"]: contact for contact in contacts}
    chair = by_name["CHAIR CANDIDATE"]
    assert sum(contact["name"] == "CHAIR CANDIDATE" for contact in contacts) == 1
    assert chair["source"] == "NY DOS Snapshot"
    assert chair["board_role"] == "Board Head"
    assert chair["board_role_status"] == "unverified"
    assert "candidate" in chair["confidence_hint"]
    assert chair["is_decision_maker"] is True
    vice_president = by_name["VICE PRESIDENT CANDIDATE"]
    assert vice_president["board_role"] == "Board Officer"
    assert vice_president["board_role_status"] == "unverified"
    for name in ("BUILDING ADDRESS CONTACT", "AGENT ADDRESS CONTACT"):
        contact = by_name[name]
        assert contact["confidence_hint"].startswith("Approximate street match")
        assert contact["board_role"] is None
        assert contact["is_decision_maker"] is False


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
