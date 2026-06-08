from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from scripts import truth_live_hpd_role_audit as audit


class FakeResponse:
    def __init__(self, payload: list[dict[str, Any]], status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> list[dict[str, Any]]:
        return self.payload


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, *, params: dict[str, Any], timeout: float) -> FakeResponse:
        self.calls.append((url, params))
        if url == audit.REGISTRATIONS_URL:
            assert "boroid=1 AND block=874 AND lot=7504" in params["$where"]
            return FakeResponse([
                {
                    "registrationid": "113190",
                    "housenumber": "220",
                    "streetname": "3 AVENUE",
                    "lastregistrationdate": "2025-08-01T00:00:00.000",
                    "registrationenddate": "2026-09-01T00:00:00.000",
                }
            ])
        if url == audit.CONTACTS_URL:
            assert params["$where"] == "registrationid=113190"
            return FakeResponse([
                {
                    "type": "Agent",
                    "corporationname": "MD Squared Property Group",
                    "firstname": "Jordan",
                    "lastname": "Perlman",
                    "contactdescription": "CONDO",
                },
                {
                    "type": "SiteManager",
                    "firstname": "Jordan",
                    "lastname": "Perlman",
                    "contactdescription": "CONDO",
                },
            ])
        raise AssertionError(f"Unexpected URL {url}")


class FailingSession:
    def get(self, url: str, *, params: dict[str, Any], timeout: float) -> FakeResponse:
        raise requests.ConnectionError("socket blocked")


def test_bbl_parts_rejects_non_ten_digit_values() -> None:
    assert audit.bbl_parts("1008747504") == {"boroid": 1, "block": 874, "lot": 7504}
    with pytest.raises(ValueError, match="10-digit"):
        audit.bbl_parts("bad-bbl")


def test_normalize_name_strips_legal_suffixes_but_keeps_business_role_words() -> None:
    assert audit.normalize_name("HARLEM PROPERTY MANAGEMENT INC.") == "HARLEM PROPERTY MANAGEMENT"
    assert audit.normalize_name("Harlem Realty LLC") == "HARLEM REALTY"
    assert audit.normalize_name("Harlem Property Management") != "HARLEM"


def test_official_query_urls_are_exact_target_slices() -> None:
    registration_url = audit.registration_query_url("1008747504")
    registration_params = parse_qs(urlparse(registration_url).query)

    assert urlparse(registration_url).netloc == "data.cityofnewyork.us"
    assert registration_params["$where"] == ["boroid=1 AND block=874 AND lot=7504"]
    assert registration_params["$limit"] == ["20"]
    property_manager_url = audit.property_managers_first_step_query_url("1008747504")
    property_manager_params = parse_qs(urlparse(property_manager_url).query)
    assert urlparse(property_manager_url).path.endswith("/resource/v4vh-sni9.json")
    assert property_manager_params["$where"] == ["boroid=1 AND block=874 AND lot=7504"]

    contacts_url = audit.contacts_query_url("113190")
    contacts_params = parse_qs(urlparse(contacts_url).query)

    assert contacts_params["$where"] == ["registrationid=113190"]
    assert contacts_params["$limit"] == ["200"]
    assert "{registration_id}" in audit.contacts_query_template_url()


def test_build_official_query_packet_does_not_require_network() -> None:
    packet = audit.build_official_query_packet([
        {
            "group": "operator_confirmed",
            "bbl": "1008170057",
            "address": "4 WEST 16 STREET",
            "expected_manager": "MD Squared Property Group",
        }
    ])

    assert packet["dry_run"] is True
    assert packet["mutations_planned"] == 0
    assert packet["source_access_mode"] == "official_query_packet_only"
    assert packet["live_source_status"] == "not_queried"
    assert packet["target_count"] == 1
    target = packet["targets"][0]
    assert target["expected_address"] == "4 WEST 16 STREET"
    assert "block%3D817" in target["official_query_urls"]["registrations_api"]
    assert "v4vh-sni9" in target["official_query_urls"]["property_managers_first_step_api"]
    assert "{registration_id}" in target["official_query_urls"]["contacts_api_template"]
    assert packet["source_catalog"]["property_managers_first_step_view"]["dataset_id"] == "v4vh-sni9"
    assert "registration lookup context only" in packet["policy"]["property_managers_first_step_policy"]
    assert "Does not contact NYC Open Data" in packet["policy"]["execution_policy"]


def test_custom_bbl_targets_carry_expected_manager_label() -> None:
    args = audit.argparse.Namespace(
        include_operator_seeds=False,
        include_hpm_nonstrict=False,
        bbl=["1008170057"],
        expected_agent="MD SQUARED PROPERTY GROUP",
        expected_manager="MD Squared Property Group",
    )

    targets = audit.build_targets(args)

    assert targets == [{
        "group": "custom",
        "bbl": "1008170057",
        "address": None,
        "expected_agent": "MD SQUARED PROPERTY GROUP",
        "expected_manager": "MD Squared Property Group",
    }]


def test_audit_target_keeps_agent_role_out_of_manager_proof() -> None:
    result = audit.audit_target(
        FakeSession(),
        {
            "group": "operator_confirmed",
            "bbl": "1008747504",
            "address": "220 3 AVENUE",
            "expected_agent": "MD SQUARED PROPERTY GROUP",
            "expected_manager": "MD Squared Property Group",
        },
        as_of=date(2026, 5, 15),
    )

    assert result["registration_count"] == 1
    assert result["current_registration_count"] == 1
    assert result["agent_contact_count"] == 1
    assert result["management_company_contact_count"] == 0
    assert result["expected_agent_match"] is True
    assert result["role_specific_claim_preview_count"] == 2
    assert result["agent_role_claim_preview_count"] == 1
    assert result["agent_role_strict_identity_match_count"] == 1
    agent_preview = next(
        preview for preview in result["role_specific_claim_previews"] if preview["hpd_contact_type"] == "Agent"
    )
    assert agent_preview["predicate"] == "registered_agent_for_building"
    assert agent_preview["claim_type"] == "registered_agent"
    assert agent_preview["source_name"] == "hpd_contacts"
    assert agent_preview["strict_identity_matches_expected_agent"] is True
    assert agent_preview["strict_identity_matches_expected_manager"] is True
    assert agent_preview["can_support_management_claim"] is False
    assert agent_preview["can_become_management_evidence_candidate"] is False
    site_preview = next(
        preview for preview in result["role_specific_claim_previews"] if preview["hpd_contact_type"] == "SiteManager"
    )
    assert site_preview["predicate"] == "hpd_site_manager_for_building"
    assert site_preview["can_support_management_claim"] is False
    assert result["manager_proof_ready"] is False
    assert result["source_catalog"]["registration_contacts"]["dataset_id"] == "feu5-w2e2"
    assert result["source_catalog"]["registration_contacts"]["download_url"].endswith("feu5-w2e2/rows.csv?accessType=DOWNLOAD")
    assert result["official_query_urls"]["registrations_api"].startswith("https://data.cityofnewyork.us/resource/tesw-yqqr")
    assert result["registrations"][0]["contacts_query_url"].startswith("https://data.cityofnewyork.us/resource/feu5-w2e2")
    assert "registered-agent/legal-contact evidence" in result["safe_action"]


def test_audit_target_reports_live_source_unreachable_without_traceback() -> None:
    result = audit.audit_target(
        FailingSession(),
        {
            "group": "operator_confirmed",
            "bbl": "1008747504",
            "address": "220 3 AVENUE",
            "expected_agent": "MD SQUARED PROPERTY GROUP",
            "expected_manager": "MD Squared Property Group",
        },
        as_of=date(2026, 5, 15),
    )

    assert result["live_source_status"] == "unreachable"
    assert result["registration_count"] == 0
    assert result["management_company_contact_count"] == 0
    assert result["manager_proof_ready"] is False
    assert result["source_catalog"]["multiple_dwelling_registrations"]["dataset_id"] == "tesw-yqqr"
    assert result["official_query_urls"]["registrations_download_csv"].endswith(
        "tesw-yqqr/rows.csv?accessType=DOWNLOAD"
    )
    assert "socket blocked" in result["error"]
    assert "not as evidence" in result["safe_action"]


def test_audit_target_can_use_official_extracts_without_network(tmp_path) -> None:
    registrations_path = tmp_path / "tesw-yqqr.csv"
    contacts_path = tmp_path / "feu5-w2e2.csv"
    registrations_path.write_text(
        "\n".join([
            "RegistrationID,BoroID,HouseNumber,StreetName,Block,Lot,LastRegistrationDate,RegistrationEndDate",
            "113190,1,220,3 AVENUE,874,7504,2025-08-01T00:00:00.000,2026-09-01T00:00:00.000",
        ]),
        encoding="utf-8",
    )
    contacts_path.write_text(
        "\n".join([
            "RegistrationID,Type,ContactDescription,CorporationName,Title,FirstName,LastName",
            "113190,Agent,CONDO,MD Squared Property Group,,,",
            "113190,ManagementCompany,MANAGING AGENT,MD Squared Property Group,,,",
        ]),
        encoding="utf-8",
    )

    result = audit.audit_target(
        FailingSession(),
        {
            "group": "operator_confirmed",
            "bbl": "1008747504",
            "address": "220 3 AVENUE",
            "expected_agent": "MD SQUARED PROPERTY GROUP",
            "expected_manager": "MD Squared Property Group",
        },
        as_of=date(2026, 5, 15),
        registration_rows=audit.load_extract_rows(registrations_path),
        contact_rows=audit.load_extract_rows(contacts_path),
        source_access_mode="local_extract",
    )

    assert result["live_source_status"] == "queried_local_extract"
    assert result["source_access_mode"] == "local_extract"
    assert result["registration_count"] == 1
    assert result["current_registration_count"] == 1
    assert result["agent_contact_count"] == 1
    assert result["management_company_contact_count"] == 1
    assert result["expected_agent_match"] is True
    assert result["role_specific_claim_preview_count"] == 2
    assert result["agent_role_strict_identity_match_count"] == 1
    management_preview = next(
        preview for preview in result["role_specific_claim_previews"] if preview["hpd_contact_type"] == "ManagementCompany"
    )
    assert management_preview["predicate"] == "manages_building"
    assert management_preview["claim_type"] == "building_management"
    assert management_preview["can_support_management_claim"] is True
    assert management_preview["can_become_management_evidence_candidate"] is True
    assert result["manager_proof_ready"] is True
    assert result["management_company_expected_manager_match_count"] == 1
    assert result["management_company_contradiction_count"] == 0
    assert result["source_evidence_intake_candidate_count"] == 1
    candidate = result["source_evidence_intake_candidates"][0]
    assert candidate["relationship_label"] == "MD Squared Property Group manages building 220 3 AVENUE"
    assert candidate["source_family"] == "hpd_management_company"
    assert candidate["source_name"] == "hpd_management_company"
    assert candidate["source_record_id"] == "feu5-w2e2:registration:113190:managementcompany:mdsquaredpropertygroup"
    assert candidate["exact_property_match"] is True
    assert candidate["role_specific_management_support"] is True
    assert candidate["contradicts_current_claim"] is False
    assert candidate["support_status"] == "supports"
    assert "source-evidence intake preview" in candidate["notes"]
    assert "source-evidence intake preview" in result["safe_action"]


def test_audit_target_builds_contradiction_intake_candidate_for_different_management_company(tmp_path) -> None:
    registrations_path = tmp_path / "tesw-yqqr.csv"
    contacts_path = tmp_path / "feu5-w2e2.csv"
    registrations_path.write_text(
        "\n".join([
            "RegistrationID,BoroID,HouseNumber,StreetName,Block,Lot,LastRegistrationDate,RegistrationEndDate",
            "113190,1,220,3 AVENUE,874,7504,2025-08-01T00:00:00.000,2026-09-01T00:00:00.000",
        ]),
        encoding="utf-8",
    )
    contacts_path.write_text(
        "\n".join([
            "RegistrationID,Type,ContactDescription,CorporationName,Title,FirstName,LastName",
            "113190,ManagementCompany,MANAGING AGENT,Other Manager LLC,,,",
        ]),
        encoding="utf-8",
    )

    result = audit.audit_target(
        FailingSession(),
        {
            "group": "operator_confirmed",
            "bbl": "1008747504",
            "address": "220 3 AVENUE",
            "expected_agent": "MD SQUARED PROPERTY GROUP",
            "expected_manager": "MD Squared Property Group",
        },
        as_of=date(2026, 5, 15),
        registration_rows=audit.load_extract_rows(registrations_path),
        contact_rows=audit.load_extract_rows(contacts_path),
        source_access_mode="local_extract",
    )

    assert result["manager_proof_ready"] is False
    assert result["management_company_contact_count"] == 1
    assert result["management_company_expected_manager_match_count"] == 0
    assert result["management_company_contradiction_count"] == 1
    assert result["source_evidence_intake_candidate_count"] == 1
    candidate = result["source_evidence_intake_candidates"][0]
    assert candidate["support_status"] == "contradicts"
    assert candidate["role_specific_management_support"] is False
    assert candidate["contradicts_current_claim"] is True
    assert "Other Manager LLC" in candidate["source_excerpt_or_row_summary"]
    assert "route as contradiction/review" in result["safe_action"]


def test_build_summary_counts_management_company_ready_groups() -> None:
    summary = audit.build_summary([
        {
            "group": "operator_confirmed",
            "registration_count": 1,
            "current_registration_count": 1,
            "management_company_contact_count": 0,
            "management_company_expected_manager_match_count": 0,
            "management_company_contradiction_count": 0,
            "source_evidence_intake_candidate_count": 0,
            "expected_agent_match": True,
            "role_specific_claim_preview_count": 5,
            "agent_role_claim_preview_count": 1,
            "agent_role_strict_identity_match_count": 1,
        },
        {
            "group": "operator_confirmed",
            "registration_count": 1,
            "current_registration_count": 1,
            "management_company_contact_count": 1,
            "management_company_expected_manager_match_count": 1,
            "management_company_contradiction_count": 0,
            "source_evidence_intake_candidate_count": 1,
            "expected_agent_match": False,
            "role_specific_claim_preview_count": 4,
            "agent_role_claim_preview_count": 1,
            "agent_role_strict_identity_match_count": 0,
        },
    ])

    assert summary["target_count"] == 2
    assert summary["registration_matched_count"] == 2
    assert summary["current_registration_matched_count"] == 2
    assert summary["management_company_ready_count"] == 1
    assert summary["management_company_expected_manager_match_count"] == 1
    assert summary["management_company_contradiction_count"] == 0
    assert summary["role_specific_claim_preview_count"] == 9
    assert summary["agent_role_claim_preview_count"] == 2
    assert summary["agent_role_strict_identity_match_count"] == 1
    assert summary["source_evidence_intake_candidate_count"] == 1
    assert summary["agent_expected_match_count"] == 1
    assert summary["live_source_unreachable_count"] == 0
    assert summary["groups"]["operator_confirmed"]["management_company_ready_count"] == 1
    assert summary["groups"]["operator_confirmed"]["source_evidence_intake_candidate_count"] == 1
    assert summary["groups"]["operator_confirmed"]["role_specific_claim_preview_count"] == 9
    assert summary["groups"]["operator_confirmed"]["agent_role_strict_identity_match_count"] == 1
