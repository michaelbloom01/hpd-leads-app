"""Read-only live HPD Registration Contacts role audit for truth source acquisition.

This script queries NYC Open Data directly. It does not write local source tables,
truth claims, evidence rows, confidence snapshots, or manifests.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


REGISTRATIONS_URL = "https://data.cityofnewyork.us/resource/tesw-yqqr.json"
CONTACTS_URL = "https://data.cityofnewyork.us/resource/feu5-w2e2.json"
PROPERTY_MANAGERS_FIRST_STEP_URL = "https://data.cityofnewyork.us/resource/v4vh-sni9.json"
REGISTRATIONS_DOWNLOAD_URL = "https://data.cityofnewyork.us/api/views/tesw-yqqr/rows.csv?accessType=DOWNLOAD"
CONTACTS_DOWNLOAD_URL = "https://data.cityofnewyork.us/api/views/feu5-w2e2/rows.csv?accessType=DOWNLOAD"
PROPERTY_MANAGERS_FIRST_STEP_DOWNLOAD_URL = (
    "https://data.cityofnewyork.us/api/views/v4vh-sni9/rows.csv?accessType=DOWNLOAD"
)
SOURCE_CATALOG: dict[str, dict[str, str]] = {
    "multiple_dwelling_registrations": {
        "dataset_id": "tesw-yqqr",
        "source_url": REGISTRATIONS_URL,
        "download_url": REGISTRATIONS_DOWNLOAD_URL,
        "catalog_url": "https://data.cityofnewyork.us/d/tesw-yqqr",
        "role": "Maps BBLs to HPD registration IDs and registration freshness.",
    },
    "registration_contacts": {
        "dataset_id": "feu5-w2e2",
        "source_url": CONTACTS_URL,
        "download_url": CONTACTS_DOWNLOAD_URL,
        "catalog_url": "https://data.cityofnewyork.us/d/feu5-w2e2",
        "role": "Lists role-specific HPD registration contacts for each registration ID.",
    },
    "property_managers_first_step_view": {
        "dataset_id": "v4vh-sni9",
        "source_url": PROPERTY_MANAGERS_FIRST_STEP_URL,
        "download_url": PROPERTY_MANAGERS_FIRST_STEP_DOWNLOAD_URL,
        "catalog_url": "https://data.cityofnewyork.us/d/v4vh-sni9",
        "role": (
            "Community-created view based on HPD registrations. Despite its title, it exposes "
            "registration/address/block/lot/date fields only and is not manager/contact evidence."
        ),
    },
}

LEGAL_SUFFIX_TOKENS = {
    "CO",
    "COMPANY",
    "CORP",
    "CORPORATION",
    "INC",
    "INCORPORATED",
    "LLC",
    "LTD",
    "LP",
    "PLLC",
}
ROLE_CLAIM_POLICIES: dict[str, dict[str, Any]] = {
    "Agent": {
        "predicate": "registered_agent_for_building",
        "claim_type": "registered_agent",
        "normalized_value": "registered_agent",
        "can_support_management_claim": False,
        "safe_action": "Use as HPD Agent / legal-contact evidence only; do not treat as operating-manager proof.",
    },
    "SiteManager": {
        "predicate": "hpd_site_manager_for_building",
        "claim_type": "person_contact",
        "normalized_value": "site_manager",
        "can_support_management_claim": False,
        "safe_action": "Use as HPD site-manager contact evidence only; do not treat as property-management company proof.",
    },
    "CorporateOwner": {
        "predicate": "owns_building",
        "claim_type": "building_ownership",
        "normalized_value": "corporate_owner",
        "can_support_management_claim": False,
        "safe_action": "Use as HPD corporate-owner evidence only; do not treat as manager proof.",
    },
    "HeadOfficer": {
        "predicate": "hpd_head_officer_for_building",
        "claim_type": "person_contact",
        "normalized_value": "head_officer",
        "can_support_management_claim": False,
        "safe_action": "Use as HPD head-officer evidence only; do not treat as manager proof.",
    },
    "Officer": {
        "predicate": "hpd_officer_for_building",
        "claim_type": "person_contact",
        "normalized_value": "officer",
        "can_support_management_claim": False,
        "safe_action": "Use as HPD officer evidence only; do not treat as manager proof.",
    },
    "IndividualOwner": {
        "predicate": "owns_building",
        "claim_type": "building_ownership",
        "normalized_value": "individual_owner",
        "can_support_management_claim": False,
        "safe_action": "Use as HPD individual-owner evidence only; do not treat as manager proof.",
    },
    "ManagementCompany": {
        "predicate": "manages_building",
        "claim_type": "building_management",
        "normalized_value": "manager",
        "can_support_management_claim": True,
        "safe_action": "May support management only when exact-property and strict identity checks pass.",
    },
}


OPERATOR_CONFIRMED_TARGETS: list[dict[str, Any]] = [
    {
        "group": "operator_confirmed",
        "bbl": "1008747504",
        "address": "220 3 AVENUE",
        "expected_agent": "MD SQUARED PROPERTY GROUP",
        "expected_manager": "MD Squared Property Group",
    },
    {
        "group": "operator_confirmed",
        "bbl": "1005297507",
        "address": "57 BOND STREET",
        "expected_agent": "MD SQUARED PROPERTY GROUP",
        "expected_manager": "MD Squared Property Group",
    },
    {
        "group": "operator_confirmed",
        "bbl": "1008170057",
        "address": "4 WEST 16 STREET",
        "expected_agent": "MD SQUARED PROPERTY GROUP",
        "expected_manager": "MD Squared Property Group",
    },
    {
        "group": "operator_confirmed",
        "bbl": "3010680037",
        "address": "9 PROSPECT PARK WEST",
        "expected_agent": "DAISY MANAGEMENT",
        "expected_manager": "Daisy Management",
    },
]


HPM_NON_STRICT_TARGETS: list[dict[str, Any]] = [
    {"group": "hpm_remaining_non_strict", "bbl": "1018210025", "address": "11 ST NICHOLAS AVENUE"},
    {"group": "hpm_remaining_non_strict", "bbl": "1019080014", "address": "141 WEST 123 STREET"},
    {"group": "hpm_remaining_non_strict", "bbl": "1018487504", "address": "306 WEST 115 STREET"},
    {"group": "hpm_remaining_non_strict", "bbl": "1010460054", "address": "342 WEST 56 STREET"},
    {"group": "hpm_remaining_non_strict", "bbl": "1019127501", "address": "345 LENOX AVENUE"},
    {"group": "hpm_remaining_non_strict", "bbl": "1017187501", "address": "42 WEST 120 STREET"},
    {"group": "hpm_remaining_non_strict", "bbl": "1018157501", "address": "506 EAST 119 STREET"},
    {"group": "hpm_remaining_non_strict", "bbl": "1020077501", "address": "555 LENOX AVENUE"},
    {"group": "hpm_remaining_non_strict", "bbl": "1018237502", "address": "61 LENOX AVENUE"},
    {"group": "hpm_remaining_non_strict", "bbl": "1020517501", "address": "330 WEST 145 STREET"},
]


def normalize_name(value: Any) -> str:
    """Strict verification key: strip legal suffixes but preserve business-role words."""
    tokens = [
        token
        for token in str(value or "").upper().replace(".", " ").replace(",", " ").split()
        if token and token not in LEGAL_SUFFIX_TOKENS
    ]
    return " ".join(tokens)


def _normal_key(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _extract_value(row: dict[str, Any], *names: str) -> Any:
    normal_names = {_normal_key(name) for name in names}
    for key, value in row.items():
        if _normal_key(key) in normal_names:
            return value
    return None


def _id_text(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def _intish(value: Any) -> int | None:
    text = _id_text(value)
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _canonical_registration_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "registrationid": _extract_value(row, "registrationid", "RegistrationID"),
        "boroid": _extract_value(row, "boroid", "boro_id", "BoroID"),
        "boro": _extract_value(row, "boro", "Boro"),
        "housenumber": _extract_value(row, "housenumber", "house_number", "HouseNumber"),
        "streetname": _extract_value(row, "streetname", "street_name", "StreetName"),
        "block": _extract_value(row, "block", "Block"),
        "lot": _extract_value(row, "lot", "Lot"),
        "lastregistrationdate": _extract_value(row, "lastregistrationdate", "LastRegistrationDate"),
        "registrationenddate": _extract_value(row, "registrationenddate", "RegistrationEndDate"),
    }


def _canonical_contact_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "registrationid": _extract_value(row, "registrationid", "RegistrationID"),
        "type": _extract_value(row, "type", "Type"),
        "contactdescription": _extract_value(row, "contactdescription", "ContactDescription"),
        "corporationname": _extract_value(row, "corporationname", "CorporationName"),
        "title": _extract_value(row, "title", "Title"),
        "firstname": _extract_value(row, "firstname", "FirstName"),
        "lastname": _extract_value(row, "lastname", "LastName"),
        "businesshousenumber": _extract_value(row, "businesshousenumber", "BusinessHouseNumber"),
        "businessstreetname": _extract_value(row, "businessstreetname", "BusinessStreetName"),
        "businesscity": _extract_value(row, "businesscity", "BusinessCity"),
        "businessstate": _extract_value(row, "businessstate", "BusinessState"),
        "businesszip": _extract_value(row, "businesszip", "BusinessZip"),
    }


def load_extract_rows(path: Path) -> list[dict[str, Any]]:
    """Load Socrata CSV/JSON downloads without mutating local source tables."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            columns = (((payload.get("meta") or {}).get("view") or {}).get("columns") or [])
            names = [
                str(column.get("fieldName") or column.get("name") or f"column_{index}")
                for index, column in enumerate(columns)
                if isinstance(column, dict)
            ]
            rows: list[dict[str, Any]] = []
            for raw_row in payload["data"]:
                if isinstance(raw_row, list):
                    rows.append({name: raw_row[index] if index < len(raw_row) else None for index, name in enumerate(names)})
            return rows
    raise ValueError(f"Unsupported HPD extract format for {path}. Use official CSV or JSON downloads.")


def find_registrations_in_extract(rows: list[dict[str, Any]], bbl: str) -> list[dict[str, Any]]:
    parts = bbl_parts(bbl)
    matches: list[dict[str, Any]] = []
    for row in rows:
        canonical = _canonical_registration_row(row)
        if (
            _intish(canonical.get("boroid")) == parts["boroid"]
            and _intish(canonical.get("block")) == parts["block"]
            and _intish(canonical.get("lot")) == parts["lot"]
        ):
            matches.append(canonical)
    return sorted(
        matches,
        key=lambda item: (
            str(item.get("registrationenddate") or ""),
            str(item.get("lastregistrationdate") or ""),
        ),
        reverse=True,
    )[:20]


def find_contacts_in_extract(rows: list[dict[str, Any]], registration_id: Any) -> list[dict[str, Any]]:
    expected = _id_text(registration_id)
    contacts: list[dict[str, Any]] = []
    for row in rows:
        canonical = _canonical_contact_row(row)
        if _id_text(canonical.get("registrationid")) == expected:
            contacts.append(canonical)
    return sorted(
        contacts,
        key=lambda item: (
            str(item.get("type") or ""),
            str(item.get("corporationname") or ""),
            str(item.get("lastname") or ""),
            str(item.get("firstname") or ""),
        ),
    )


def bbl_parts(bbl: str) -> dict[str, int]:
    text = str(bbl or "").strip()
    if len(text) != 10 or not text.isdigit():
        raise ValueError(f"BBL must be a 10-digit numeric string: {bbl!r}")
    return {
        "boroid": int(text[0]),
        "block": int(text[1:6]),
        "lot": int(text[6:10]),
    }


def _api_url(url: str, params: dict[str, Any]) -> str:
    return f"{url}?{urlencode(params)}"


def registration_query_params(bbl: str) -> dict[str, Any]:
    parts = bbl_parts(bbl)
    return {
        "$select": (
            "registrationid,boroid,boro,housenumber,streetname,block,lot,"
            "lastregistrationdate,registrationenddate"
        ),
        "$where": f"boroid={parts['boroid']} AND block={parts['block']} AND lot={parts['lot']}",
        "$limit": 20,
        "$order": "registrationenddate DESC, lastregistrationdate DESC",
    }


def contacts_query_params(registration_id: Any) -> dict[str, Any]:
    return {
        "$select": (
            "type,contactdescription,corporationname,title,firstname,lastname,"
            "businesshousenumber,businessstreetname,businesscity,businessstate,businesszip"
        ),
        "$where": f"registrationid={registration_id}",
        "$limit": 200,
        "$order": "type,corporationname,lastname,firstname",
    }


def registration_query_url(bbl: str) -> str:
    return _api_url(REGISTRATIONS_URL, registration_query_params(bbl))


def property_managers_first_step_query_url(bbl: str) -> str:
    return _api_url(PROPERTY_MANAGERS_FIRST_STEP_URL, registration_query_params(bbl))


def contacts_query_url(registration_id: Any) -> str:
    return _api_url(CONTACTS_URL, contacts_query_params(registration_id))


def contacts_query_template_url() -> str:
    return contacts_query_url("{registration_id}").replace("%7Bregistration_id%7D", "{registration_id}")


def official_query_urls_for_target(target: dict[str, Any]) -> dict[str, Any]:
    return {
        "registrations_api": registration_query_url(str(target["bbl"])),
        "property_managers_first_step_api": property_managers_first_step_query_url(str(target["bbl"])),
        "contacts_api_template": contacts_query_template_url(),
        "registrations_download_csv": REGISTRATIONS_DOWNLOAD_URL,
        "contacts_download_csv": CONTACTS_DOWNLOAD_URL,
        "property_managers_first_step_download_csv": PROPERTY_MANAGERS_FIRST_STEP_DOWNLOAD_URL,
        "note": (
            "Use the registrations_api URL to find RegistrationID values for this BBL, then query "
            "registration_contacts with each RegistrationID. Full official CSV extracts can also be "
            "downloaded and passed to --registrations-file / --contacts-file. The "
            "property_managers_first_step_api is a registration lookup view only; it does not contain "
            "manager/contact fields and cannot support manages_building evidence."
        ),
    }


def build_official_query_packet(targets: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "run_type": "truth_live_hpd_role_audit_query_packet",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": True,
        "mutations_planned": 0,
        "source_access_mode": "official_query_packet_only",
        "live_source_status": "not_queried",
        "source_name": "nyc_open_data_hpd_registration_contacts",
        "source_urls": [REGISTRATIONS_URL, CONTACTS_URL, PROPERTY_MANAGERS_FIRST_STEP_URL],
        "download_urls": [
            REGISTRATIONS_DOWNLOAD_URL,
            CONTACTS_DOWNLOAD_URL,
            PROPERTY_MANAGERS_FIRST_STEP_DOWNLOAD_URL,
        ],
        "source_catalog": SOURCE_CATALOG,
        "target_count": len(targets),
        "targets": [
            {
                "bbl": target["bbl"],
                "group": target.get("group"),
                "expected_address": target.get("address"),
                "expected_manager": target.get("expected_manager") or "Harlem Property Management",
                "official_query_urls": official_query_urls_for_target(target),
            }
            for target in targets
        ],
        "policy": {
            "execution_policy": "Read-only official query packet. Does not contact NYC Open Data or local tables.",
            "manager_proof_policy": (
                "Only HPD ManagementCompany rows may support the HPD manager-proof family. "
                "Agent, SiteManager, CorporateOwner, HeadOfficer, Officer, and IndividualOwner rows "
                "remain role-specific legal/contact evidence."
            ),
            "property_managers_first_step_policy": (
                "The v4vh-sni9 Property Managers-1st Step view is registration lookup context only. "
                "It cannot support or contradict a management claim without matching role-specific "
                "Registration Contacts evidence from feu5-w2e2 or another exact manager-proof source."
            ),
        },
        "safe_action": (
            "Use these URLs to acquire official HPD registration/contact rows outside a restricted runtime. "
            "Then rerun this script with --registrations-file and --contacts-file before previewing any evidence."
        ),
    }


def parse_socrata_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def contact_display_name(contact: dict[str, Any]) -> str:
    corporation = str(contact.get("corporationname") or "").strip()
    if corporation:
        return corporation
    return " ".join(
        item
        for item in [
            str(contact.get("firstname") or "").strip(),
            str(contact.get("lastname") or "").strip(),
        ]
        if item
    ).strip()


def _source_record_id(registration_id: Any, contact: dict[str, Any]) -> str:
    name_key = _normal_key(contact_display_name(contact)) or "unnamed"
    role_key = _normal_key(contact.get("type")) or "unknownrole"
    return f"feu5-w2e2:registration:{_id_text(registration_id)}:{role_key}:{name_key}"


def _role_specific_claim_preview(
    *,
    target: dict[str, Any],
    registration: dict[str, Any],
    contact: dict[str, Any],
    is_current: bool,
    expected_agent_normalized: str,
    expected_manager_normalized: str,
) -> dict[str, Any]:
    role = str(contact.get("type") or "").strip()
    policy = ROLE_CLAIM_POLICIES.get(role, {
        "predicate": "hpd_contact_role_for_building",
        "claim_type": "person_contact",
        "normalized_value": role.lower() or "unknown_role",
        "can_support_management_claim": False,
        "safe_action": "Use as role-specific HPD contact evidence only; do not treat as manager proof.",
    })
    display_name = contact_display_name(contact)
    normalized_name = normalize_name(display_name)
    expected_address = target.get("address") or str(target.get("bbl") or "").strip()
    return {
        "bbl": str(target.get("bbl") or "").strip(),
        "address": expected_address,
        "registration_id": _id_text(registration.get("registrationid")),
        "registration_end_date": registration.get("registrationenddate"),
        "is_current_as_of": is_current,
        "hpd_contact_type": role,
        "display_name": display_name,
        "verification_identity_key": normalized_name,
        "expected_agent_identity_key": expected_agent_normalized,
        "expected_manager_identity_key": expected_manager_normalized,
        "strict_identity_matches_expected_agent": normalized_name == expected_agent_normalized,
        "strict_identity_matches_expected_manager": normalized_name == expected_manager_normalized,
        "source_name": "hpd_contacts",
        "source_record_id": _source_record_id(registration.get("registrationid"), contact),
        "predicate": policy["predicate"],
        "claim_type": policy["claim_type"],
        "normalized_value": policy["normalized_value"],
        "can_support_management_claim": policy["can_support_management_claim"],
        "can_become_management_evidence_candidate": (
            bool(policy["can_support_management_claim"])
            and is_current
            and normalized_name == expected_manager_normalized
        ),
        "safe_action": policy["safe_action"],
    }


def _best_observed_at(registration: dict[str, Any]) -> str | None:
    return (
        str(registration.get("lastregistrationdate") or "").strip()
        or str(registration.get("registrationenddate") or "").strip()
        or None
    )


def _management_contact_intake_candidate(
    *,
    target: dict[str, Any],
    registration: dict[str, Any],
    contact: dict[str, Any],
    is_current: bool,
    expected_manager_normalized: str,
) -> dict[str, Any] | None:
    """Build a paste-back candidate from a current official HPD ManagementCompany row."""
    if not is_current:
        return None
    display_name = contact_display_name(contact)
    normalized_name = normalize_name(display_name)
    matches_expected_manager = normalized_name == expected_manager_normalized
    expected_manager = target.get("expected_manager") or "Harlem Property Management"
    expected_address = target.get("address") or str(target.get("bbl") or "").strip()
    contacts_url = contacts_query_url(registration.get("registrationid"))
    support_status = "supports" if matches_expected_manager else "contradicts"
    excerpt = (
        "Official HPD Registration Contacts row lists "
        f"{display_name or 'unnamed contact'} as ManagementCompany for "
        f"registration {_id_text(registration.get('registrationid'))} / BBL {target.get('bbl')}."
    )
    if not matches_expected_manager:
        excerpt += f" Expected manager was {expected_manager}."
    return {
        "relationship_label": f"{expected_manager} manages building {expected_address}",
        "bbl": str(target.get("bbl") or "").strip(),
        "address": expected_address,
        "manager_name": expected_manager,
        "manager_lead_id": None,
        "source_family": "hpd_management_company",
        "source_name": "hpd_management_company",
        "source_url_or_local_record_reference": contacts_url,
        "source_record_id": _source_record_id(registration.get("registrationid"), contact),
        "observed_at": _best_observed_at(registration),
        "exact_property_match": True,
        "role_specific_management_support": matches_expected_manager,
        "source_excerpt_or_row_summary": excerpt,
        "contradicts_current_claim": not matches_expected_manager,
        "notes": (
            "Generated by read-only truth_live_hpd_role_audit.py from official HPD "
            "Registration Contacts; run source-evidence intake preview before any recording."
        ),
        "support_status": support_status,
        "matched_expected_manager": matches_expected_manager,
        "official_dataset_ids": ["tesw-yqqr", "feu5-w2e2"],
        "source_access_policy": "preview_only_requires_source_evidence_intake_then_explicit_approval",
    }


def fetch_json(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any],
    timeout: float,
) -> list[dict[str, Any]]:
    response = session.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError(f"Expected Socrata list response from {url}, got {type(payload).__name__}")
    return [item for item in payload if isinstance(item, dict)]


def fetch_registrations(
    session: requests.Session,
    bbl: str,
    *,
    timeout: float = 20.0,
) -> list[dict[str, Any]]:
    return fetch_json(
        session,
        REGISTRATIONS_URL,
        params=registration_query_params(bbl),
        timeout=timeout,
    )


def fetch_contacts(
    session: requests.Session,
    registration_id: Any,
    *,
    timeout: float = 20.0,
) -> list[dict[str, Any]]:
    return fetch_json(
        session,
        CONTACTS_URL,
        params=contacts_query_params(registration_id),
        timeout=timeout,
    )


def build_unreachable_result(target: dict[str, Any], exc: requests.RequestException) -> dict[str, Any]:
    return {
        "bbl": target["bbl"],
        "group": target.get("group"),
        "expected_address": target.get("address"),
        "expected_manager": target.get("expected_manager") or "Harlem Property Management",
        "registration_count": 0,
        "current_registration_count": 0,
        "contact_types": [],
        "agent_contact_count": 0,
        "management_company_contact_count": 0,
        "expected_agent_match": False,
        "manager_proof_ready": False,
        "registrations": [],
        "live_source_status": "unreachable",
        "source_catalog": SOURCE_CATALOG,
        "official_query_urls": official_query_urls_for_target(target),
        "error": str(exc),
        "safe_action": (
            "Live HPD Open Data was unreachable from this runtime. Treat this as an acquisition-tooling "
            "blocker, not as evidence that ManagementCompany rows do or do not exist."
        ),
    }


def audit_target(
    session: requests.Session,
    target: dict[str, Any],
    *,
    as_of: date,
    timeout: float = 20.0,
    registration_rows: list[dict[str, Any]] | None = None,
    contact_rows: list[dict[str, Any]] | None = None,
    source_access_mode: str = "live_api",
) -> dict[str, Any]:
    if registration_rows is not None:
        registrations = find_registrations_in_extract(registration_rows, str(target["bbl"]))
    else:
        try:
            registrations = fetch_registrations(session, str(target["bbl"]), timeout=timeout)
        except requests.RequestException as exc:
            return build_unreachable_result(target, exc)

    audited_registrations: list[dict[str, Any]] = []
    all_contact_types: set[str] = set()
    management_company_contacts: list[dict[str, Any]] = []
    agent_contacts: list[dict[str, Any]] = []
    expected_agent = normalize_name(target.get("expected_agent") or "HARLEM PROPERTY MANAGEMENT")
    expected_manager = normalize_name(target.get("expected_manager") or "Harlem Property Management")
    role_specific_claim_previews: list[dict[str, Any]] = []
    source_evidence_intake_candidates: list[dict[str, Any]] = []

    for registration in registrations:
        if contact_rows is not None:
            contacts = find_contacts_in_extract(contact_rows, registration.get("registrationid"))
        else:
            try:
                contacts = fetch_contacts(session, registration.get("registrationid"), timeout=timeout)
            except requests.RequestException as exc:
                return build_unreachable_result(target, exc)
        contact_types = sorted({str(contact.get("type") or "").strip() for contact in contacts if contact.get("type")})
        all_contact_types.update(contact_types)
        registration_end = parse_socrata_date(registration.get("registrationenddate"))
        is_current = bool(registration_end and registration_end >= as_of)
        role_specific_claim_previews.extend(
            _role_specific_claim_preview(
                target=target,
                registration=registration,
                contact=contact,
                is_current=is_current,
                expected_agent_normalized=expected_agent,
                expected_manager_normalized=expected_manager,
            )
            for contact in contacts
            if str(contact.get("type") or "").strip()
        )
        registration_management_contacts = [
            contact for contact in contacts if str(contact.get("type") or "").strip() == "ManagementCompany"
        ]
        registration_agent_contacts = [
            contact for contact in contacts if str(contact.get("type") or "").strip() == "Agent"
        ]
        management_company_contacts.extend(registration_management_contacts)
        agent_contacts.extend(registration_agent_contacts)
        for contact in registration_management_contacts:
            candidate = _management_contact_intake_candidate(
                target=target,
                registration=registration,
                contact=contact,
                is_current=is_current,
                expected_manager_normalized=expected_manager,
            )
            if candidate:
                source_evidence_intake_candidates.append(candidate)
        audited_registrations.append({
            "registration_id": registration.get("registrationid"),
            "contacts_query_url": contacts_query_url(registration.get("registrationid")),
            "address": " ".join(
                item
                for item in [
                    str(registration.get("housenumber") or "").strip(),
                    str(registration.get("streetname") or "").strip(),
                ]
                if item
            ),
            "last_registration_date": registration.get("lastregistrationdate"),
            "registration_end_date": registration.get("registrationenddate"),
            "is_current_as_of": is_current,
            "contact_types": contact_types,
            "agent_contacts": [
                {
                    "display_name": contact_display_name(contact),
                    "normalized_name": normalize_name(contact_display_name(contact)),
                    "title": contact.get("title"),
                    "contact_description": contact.get("contactdescription"),
                }
                for contact in registration_agent_contacts
            ],
            "management_company_contacts": [
                {
                    "display_name": contact_display_name(contact),
                    "normalized_name": normalize_name(contact_display_name(contact)),
                    "title": contact.get("title"),
                    "contact_description": contact.get("contactdescription"),
                }
                for contact in registration_management_contacts
            ],
        })

    normalized_agents = {normalize_name(contact_display_name(contact)) for contact in agent_contacts}
    current_role_specific_claim_previews = [
        preview for preview in role_specific_claim_previews if preview["is_current_as_of"]
    ]
    agent_role_previews = [
        preview for preview in current_role_specific_claim_previews if preview["hpd_contact_type"] == "Agent"
    ]
    supporting_intake_candidates = [
        candidate for candidate in source_evidence_intake_candidates if candidate["support_status"] == "supports"
    ]
    contradicting_intake_candidates = [
        candidate for candidate in source_evidence_intake_candidates if candidate["support_status"] == "contradicts"
    ]
    return {
        "bbl": target["bbl"],
        "group": target.get("group"),
        "expected_address": target.get("address"),
        "expected_manager": target.get("expected_manager") or "Harlem Property Management",
        "registration_count": len(registrations),
        "current_registration_count": sum(1 for item in audited_registrations if item["is_current_as_of"]),
        "contact_types": sorted(all_contact_types),
        "agent_contact_count": len(agent_contacts),
        "management_company_contact_count": len(management_company_contacts),
        "expected_agent_match": expected_agent in normalized_agents,
        "manager_proof_ready": bool(supporting_intake_candidates),
        "management_company_expected_manager_match_count": len(supporting_intake_candidates),
        "management_company_contradiction_count": len(contradicting_intake_candidates),
        "role_specific_claim_preview_count": len(current_role_specific_claim_previews),
        "agent_role_claim_preview_count": len(agent_role_previews),
        "agent_role_strict_identity_match_count": sum(
            1 for preview in agent_role_previews if preview["strict_identity_matches_expected_agent"]
        ),
        "role_specific_claim_previews": current_role_specific_claim_previews,
        "source_evidence_intake_candidate_count": len(source_evidence_intake_candidates),
        "source_evidence_intake_candidates": source_evidence_intake_candidates,
        "live_source_status": "queried_local_extract" if source_access_mode == "local_extract" else "queried",
        "source_access_mode": source_access_mode,
        "source_catalog": SOURCE_CATALOG,
        "official_query_urls": official_query_urls_for_target(target),
        "error": None,
        "registrations": audited_registrations,
        "safe_action": (
            "HPD ManagementCompany row matched expected manager; run source-evidence intake preview before recording."
            if supporting_intake_candidates
            else (
                "HPD ManagementCompany row names a different manager; route as contradiction/review before any recording."
                if contradicting_intake_candidates
                else "Use HPD Agent rows only as registered-agent/legal-contact evidence; do not count as manages_building proof."
            )
        ),
    }


def build_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "target_count": len(results),
        "registration_matched_count": sum(1 for item in results if item["registration_count"]),
        "current_registration_matched_count": sum(1 for item in results if item["current_registration_count"]),
        "management_company_ready_count": sum(1 for item in results if item["management_company_contact_count"]),
        "management_company_expected_manager_match_count": sum(
            int(item.get("management_company_expected_manager_match_count") or 0) for item in results
        ),
        "management_company_contradiction_count": sum(
            int(item.get("management_company_contradiction_count") or 0) for item in results
        ),
        "role_specific_claim_preview_count": sum(
            int(item.get("role_specific_claim_preview_count") or 0) for item in results
        ),
        "agent_role_claim_preview_count": sum(
            int(item.get("agent_role_claim_preview_count") or 0) for item in results
        ),
        "agent_role_strict_identity_match_count": sum(
            int(item.get("agent_role_strict_identity_match_count") or 0) for item in results
        ),
        "source_evidence_intake_candidate_count": sum(
            int(item.get("source_evidence_intake_candidate_count") or 0) for item in results
        ),
        "agent_expected_match_count": sum(1 for item in results if item["expected_agent_match"]),
        "live_source_unreachable_count": sum(1 for item in results if item.get("live_source_status") == "unreachable"),
        "groups": {
            group: {
                "target_count": sum(1 for item in results if item.get("group") == group),
                "management_company_ready_count": sum(
                    1 for item in results if item.get("group") == group and item["management_company_contact_count"]
                ),
                "source_evidence_intake_candidate_count": sum(
                    int(item.get("source_evidence_intake_candidate_count") or 0)
                    for item in results
                    if item.get("group") == group
                ),
                "role_specific_claim_preview_count": sum(
                    int(item.get("role_specific_claim_preview_count") or 0)
                    for item in results
                    if item.get("group") == group
                ),
                "agent_role_strict_identity_match_count": sum(
                    int(item.get("agent_role_strict_identity_match_count") or 0)
                    for item in results
                    if item.get("group") == group
                ),
                "live_source_unreachable_count": sum(
                    1 for item in results if item.get("group") == group and item.get("live_source_status") == "unreachable"
                ),
            }
            for group in sorted({str(item.get("group") or "") for item in results})
        },
    }


def build_targets(args: argparse.Namespace) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    if args.include_operator_seeds:
        targets.extend(OPERATOR_CONFIRMED_TARGETS)
    if args.include_hpm_nonstrict:
        targets.extend(HPM_NON_STRICT_TARGETS)
    for bbl in args.bbl or []:
        targets.append({
            "group": "custom",
            "bbl": bbl,
            "address": None,
            "expected_agent": args.expected_agent,
            "expected_manager": args.expected_manager,
        })
    deduped: dict[str, dict[str, Any]] = {}
    for target in targets:
        deduped[str(target["bbl"])] = target
    return list(deduped.values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bbl", action="append", help="10-digit BBL to audit. Can be passed multiple times.")
    parser.add_argument("--expected-agent", default="HARLEM PROPERTY MANAGEMENT", help="Expected agent name for custom BBLs.")
    parser.add_argument(
        "--expected-manager",
        default="Harlem Property Management",
        help="Expected manager display name for custom BBLs.",
    )
    parser.add_argument("--include-operator-seeds", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-hpm-nonstrict", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--registrations-file", type=Path, help="Official tesw-yqqr CSV/JSON extract to query locally.")
    parser.add_argument("--contacts-file", type=Path, help="Official feu5-w2e2 CSV/JSON extract to query locally.")
    parser.add_argument(
        "--query-packet-only",
        action="store_true",
        help="Emit exact official HPD query/download URLs for selected targets without network calls.",
    )
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--indent", type=int, default=2)
    args = parser.parse_args(argv)
    if bool(args.registrations_file) != bool(args.contacts_file):
        parser.error("--registrations-file and --contacts-file must be supplied together.")

    targets = build_targets(args)
    if not targets:
        parser.error("No BBLs selected. Use --bbl or enable one of the default target sets.")
    if args.query_packet_only:
        print(json.dumps(build_official_query_packet(targets), indent=args.indent, sort_keys=False))
        return 0

    today = datetime.now(timezone.utc).date()
    session = requests.Session()
    registration_rows = load_extract_rows(args.registrations_file) if args.registrations_file else None
    contact_rows = load_extract_rows(args.contacts_file) if args.contacts_file else None
    source_access_mode = "local_extract" if registration_rows is not None and contact_rows is not None else "live_api"
    results = [
        audit_target(
            session,
            target,
            as_of=today,
            timeout=args.timeout,
            registration_rows=registration_rows,
            contact_rows=contact_rows,
            source_access_mode=source_access_mode,
        )
        for target in targets
    ]
    queried_status = "queried_local_extract" if source_access_mode == "local_extract" else "queried"
    payload = {
        "run_type": "truth_live_hpd_role_audit",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": True,
        "mutations_planned": 0,
        "live_source_status": (
            "unreachable"
            if any(item.get("live_source_status") == "unreachable" for item in results)
            else queried_status
        ),
        "source_access_mode": source_access_mode,
        "source_name": "nyc_open_data_hpd_registration_contacts",
        "source_urls": [REGISTRATIONS_URL, CONTACTS_URL, PROPERTY_MANAGERS_FIRST_STEP_URL],
        "source_catalog": SOURCE_CATALOG,
        "download_urls": [
            REGISTRATIONS_DOWNLOAD_URL,
            CONTACTS_DOWNLOAD_URL,
            PROPERTY_MANAGERS_FIRST_STEP_DOWNLOAD_URL,
        ],
        "source_extracts": {
            "registration_row_count": len(registration_rows or []),
            "contact_row_count": len(contact_rows or []),
            "registrations_file": str(args.registrations_file) if args.registrations_file else None,
            "contacts_file": str(args.contacts_file) if args.contacts_file else None,
        },
        "policy": {
            "execution_policy": "Read-only live public-data query. Does not refresh local source tables.",
            "manager_proof_policy": (
                "Only HPD ManagementCompany rows may support the HPD manager-proof family. "
                "Agent, SiteManager, CorporateOwner, HeadOfficer, Officer, and IndividualOwner rows "
                "remain role-specific legal/contact evidence."
            ),
            "property_managers_first_step_policy": (
                "The v4vh-sni9 Property Managers-1st Step view is registration lookup context only. "
                "It cannot support or contradict a management claim without matching role-specific "
                "Registration Contacts evidence from feu5-w2e2 or another exact manager-proof source."
            ),
        },
        "summary": build_summary(results),
        "results": results,
        "safe_action": (
            "Use this packet for source acquisition and role-boundary review only. Record no truth "
            "evidence without a separate preview plus explicit --execute/--confirm-execute approval."
        ),
    }
    print(json.dumps(payload, indent=args.indent, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
