"""Shared contact roster assembly for building, lead, and agent surfaces.

This module is the single source of truth for contact assembly and provenance.
It only reads from local data stores (building_contacts + dos_cache) and never
calls external APIs in request paths.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession


DOS_CACHE_MAX_AGE_DAYS = 30
DOS_REFRESH_COOLDOWN_MINUTES = 5


def _normalize_addr(value: Optional[str]) -> str:
    return (value or "").strip().upper()


def _coerce_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    text_value = str(value).strip()
    if not text_value:
        return None
    # Keep YYYY-MM-DD as is, otherwise return raw value.
    if len(text_value) >= 10 and text_value[4] == "-" and text_value[7] == "-":
        return text_value[:10]
    return text_value


def _coerce_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text_value = str(value).strip()
    if not text_value:
        return None
    try:
        parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _parse_date_for_sort(value: Optional[str]) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _format_contact_address(parts: list[Optional[str]]) -> Optional[str]:
    cleaned = [str(p).strip() for p in parts if p and str(p).strip()]
    if not cleaned:
        return None
    return ", ".join(cleaned)


def _hpd_source_url(registration_contact_id: Optional[str]) -> Optional[str]:
    if not registration_contact_id:
        return None
    return (
        "https://data.cityofnewyork.us/resource/feu5-w2e2.json"
        f"?registrationcontactid={registration_contact_id}"
    )


def _dos_entity_url(dos_id: Optional[str]) -> Optional[str]:
    if not dos_id:
        return None
    return f"https://data.ny.gov/resource/n9v6-gdp6.json?dos_id={dos_id}"


def _dos_filing_url(filing_num: Optional[str], dos_id: Optional[str]) -> Optional[str]:
    if filing_num:
        return f"https://data.ny.gov/resource/2tms-hftb.json?film_num={filing_num}"
    return _dos_entity_url(dos_id)


def _detect_board_role(role: Optional[str], title: Optional[str]) -> Optional[str]:
    role_upper = (role or "").strip().upper()
    title_upper = (title or "").strip().upper()
    text_value = f"{role_upper} {title_upper}".strip()
    if not text_value:
        return None
    if "HEADOFFICER" in text_value:
        return "Board Head"
    if re.search(r"\b(PRESIDENT|CHAIR|CHAIRMAN|BOARD|TREASURER|SECRETARY|VICE PRESIDENT|VP)\b", text_value):
        return "Board Officer"
    return None


def _is_condo_or_coop(building_type: Optional[str], building_class: Optional[str]) -> bool:
    bt = (building_type or "").strip().upper()
    bc = (building_class or "").strip().upper()
    if "CONDO" in bt or "COOP" in bt or "CO-OP" in bt:
        return True
    if bc.startswith("R"):  # PLUTO condo classes often R1-R9 / RR
        return True
    if bc in {"C6", "C8", "D0", "D4"}:  # common coop classes
        return True
    return False


def _extract_street_key(value: Optional[str]) -> str:
    text_value = _normalize_addr(value)
    if not text_value:
        return ""
    parts = [p.strip(".,") for p in text_value.split() if p.strip(".,")]
    if not parts:
        return ""
    # Drop common care-of prefixes to get the physical street key.
    while parts and parts[0] in {"C/O", "ATTN", "ATTN:"}:
        parts.pop(0)
    if not parts:
        return ""
    key: list[str] = []
    for token in parts:
        if token in {"APT", "UNIT", "STE", "SUITE", "FL", "FLOOR", "RM", "ROOM"} or token.startswith("#"):
            break
        key.append(token)
        if len(key) >= 3:
            break
    return " ".join(key)


def _classify_officer_confidence(
    officer_address: Optional[str],
    building_address: Optional[str],
    pm_address: Optional[str],
) -> tuple[Optional[str], bool]:
    officer_key = _extract_street_key(officer_address)
    building_key = _extract_street_key(building_address)
    pm_key = _extract_street_key(pm_address)

    if building_key and officer_key and building_key == officer_key:
        return "Likely board member (resident)", True
    if pm_key and officer_key and pm_key == officer_key:
        return "PM company employee", False
    return None, False


def _dedupe_contacts(contacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for contact in contacts:
        name = str(contact.get("name") or "").strip()
        role = str(contact.get("role") or "").strip()
        if not name or not role:
            continue
        key = (name.upper(), role.upper())
        existing = deduped.get(key)
        if not existing:
            deduped[key] = contact
            continue

        existing_ts = _parse_date_for_sort(existing.get("as_of_date"))
        incoming_ts = _parse_date_for_sort(contact.get("as_of_date"))
        if incoming_ts > existing_ts:
            deduped[key] = contact
        elif incoming_ts == existing_ts and contact.get("is_decision_maker") and not existing.get("is_decision_maker"):
            deduped[key] = contact

    # Prefer the semantic chairman row over generic DOS officer rows for same person.
    chairman_names = {
        name for (name, role) in deduped.keys()
        if "CHAIRMAN" in role
    }
    for key in list(deduped.keys()):
        name, role = key
        if name in chairman_names and "CHAIRMAN" not in role and "DOS" in role:
            deduped.pop(key, None)

    def _sort_priority(contact: dict[str, Any]) -> int:
        hint = str(contact.get("confidence_hint") or "").upper()
        role = str(contact.get("role") or "").upper()
        source = str(contact.get("source") or "").upper()
        if "RESIDENT" in hint:
            return 0
        if "CHAIRMAN" in role:
            return 1
        if contact.get("is_decision_maker"):
            return 2
        if "DOS" in source:
            return 3
        return 4

    results = list(deduped.values())
    results.sort(
        key=lambda c: (
            _sort_priority(c),
            -_parse_date_for_sort(c.get("as_of_date")),
            str(c.get("name") or "").upper(),
        )
    )
    return results


def _get_dos_cache_payload_from_row(
    row: Any,
) -> tuple[Optional[dict[str, Any]], str, Optional[str]]:
    if not row:
        return None, "not_loaded", None

    raw = row.get("result")
    cached_at = row.get("cached_at")
    now = datetime.now(timezone.utc)
    cached_at_iso = _coerce_iso(cached_at)

    payload: Optional[dict[str, Any]] = None

    if not raw:
        parsed_cached = _coerce_datetime(cached_at)
        if parsed_cached and now - parsed_cached <= timedelta(days=DOS_CACHE_MAX_AGE_DAYS):
            return None, "loaded", cached_at_iso
        return None, "stale", cached_at_iso

    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(parsed, dict):
            payload = parsed
    except json.JSONDecodeError:
        payload = None

    has_officers = bool((payload or {}).get("officers"))
    has_ceo = bool(str((payload or {}).get("ceo_name") or "").strip())
    has_dos_id = bool(str((payload or {}).get("dos_id") or "").strip())
    has_lookup_attempt = payload is not None and any(
        key in (payload or {})
        for key in ("lookup_name", "snapshot_as_of", "entity_name", "entity_type", "officers")
    )
    parsed_cached = _coerce_datetime(cached_at)
    is_fresh_cache = bool(parsed_cached and now - parsed_cached <= timedelta(days=DOS_CACHE_MAX_AGE_DAYS))

    refresh_requested_at = _coerce_datetime((payload or {}).get("refresh_requested_at"))
    refresh_in_cooldown = bool(
        refresh_requested_at and now - refresh_requested_at <= timedelta(minutes=DOS_REFRESH_COOLDOWN_MINUTES)
    )

    if payload is not None:
        if has_officers or has_ceo or has_dos_id:
            if is_fresh_cache:
                return payload, "loaded", cached_at_iso
            if refresh_in_cooldown:
                return payload, "refreshing", cached_at_iso
            return payload, "stale", cached_at_iso
        if has_lookup_attempt:
            if is_fresh_cache:
                return payload, "no_match", cached_at_iso
            if refresh_in_cooldown:
                return payload, "refreshing", cached_at_iso
            return payload, "stale", cached_at_iso

    if refresh_in_cooldown:
        return payload, "refreshing", cached_at_iso

    if parsed_cached and now - parsed_cached <= timedelta(days=DOS_CACHE_MAX_AGE_DAYS):
        return payload, "loaded", cached_at_iso
    return payload, "stale", cached_at_iso


async def get_building_contacts(
    session: AsyncSession,
    bbl: str,
    building_address: Optional[str] = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    contacts: list[dict[str, Any]] = []
    pm_address: Optional[str] = None
    management_company: Optional[str] = None
    corporate_owner: Optional[str] = None
    building_type: Optional[str] = None
    building_class: Optional[str] = None

    b_row = (
        await session.execute(
            text("SELECT address, building_type, building_class FROM buildings WHERE bbl = :bbl"),
            {"bbl": bbl},
        )
    ).first()
    if b_row:
        if not building_address:
            building_address = b_row[0]
        building_type = b_row[1]
        building_class = b_row[2]
    is_condo_coop = _is_condo_or_coop(building_type, building_class)

    contact_rows = await session.execute(
        text("""
            SELECT id, registration_contact_id, contact_type, corporation_name, first_name, last_name,
                   title, business_address, business_city, business_state, business_zip, updated_at
            FROM building_contacts
            WHERE bbl = :bbl
            ORDER BY id ASC
        """),
        {"bbl": bbl},
    )

    for row in contact_rows:
        data = dict(row._mapping)
        name = data.get("corporation_name") or f"{data.get('first_name', '')} {data.get('last_name', '')}".strip()
        if not name:
            continue

        role = data.get("contact_type") or "Unknown"
        board_role = _detect_board_role(role, data.get("title"))
        address = _format_contact_address(
            [
                data.get("business_address"),
                data.get("business_city"),
                data.get("business_state"),
                data.get("business_zip"),
            ]
        )
        as_of_date = _coerce_iso(data.get("updated_at"))
        is_decision_maker = role in {"CorporateOwner", "Owner", "IndividualOwner", "HeadOfficer"}
        if board_role and is_condo_coop:
            is_decision_maker = True

        contacts.append(
            {
                "name": name,
                "role": role,
                "source": "HPD Registration",
                "source_record_id": data.get("registration_contact_id"),
                "as_of_date": as_of_date,
                "publication_date": as_of_date,
                "address": address,
                "confidence_hint": None,
                "is_decision_maker": is_decision_maker,
                "source_url": _hpd_source_url(data.get("registration_contact_id")),
                "board_role": board_role,
            }
        )

        if role == "Agent" and not pm_address:
            pm_address = address
            management_company = name
        if role == "CorporateOwner" and not corporate_owner:
            corporate_owner = name

    cache_row = (
        await session.execute(
            text("""
                SELECT result, cached_at
                FROM dos_cache
                WHERE cache_key = :cache_key
            """),
            {"cache_key": f"officers:{bbl}"},
        )
    ).first()

    payload, dos_status, last_refreshed_at = _get_dos_cache_payload_from_row(
        dict(cache_row._mapping) if cache_row else None
    )

    if payload:
        dos_id = str(payload.get("dos_id") or "").strip() or None
        officers = payload.get("officers") or []
        if isinstance(officers, list):
            for officer in officers:
                if not isinstance(officer, dict):
                    continue
                officer_name = str(officer.get("name") or "").strip()
                if not officer_name:
                    continue
                officer_address = _format_contact_address(
                    [
                        officer.get("address"),
                        officer.get("city"),
                        officer.get("state"),
                        officer.get("zip"),
                    ]
                )
                confidence_hint, resident_decision_maker = _classify_officer_confidence(
                    officer_address=officer_address,
                    building_address=building_address,
                    pm_address=pm_address,
                )
                contacts.append(
                    {
                        "name": officer_name,
                        "role": str(officer.get("title") or "").strip() or "DOS Officer",
                        "source": "NY DOS Filing",
                        "source_record_id": officer.get("filing_num"),
                        "as_of_date": _coerce_iso(officer.get("filing_date")),
                        "publication_date": _coerce_iso(officer.get("filing_date")),
                        "filing_date": _coerce_iso(officer.get("filing_date")),
                        "address": officer_address,
                        "confidence_hint": confidence_hint,
                        "is_decision_maker": resident_decision_maker or (
                            bool(_detect_board_role("DOS Officer", officer.get("title"))) and is_condo_coop
                        ),
                        "source_url": _dos_filing_url(officer.get("filing_num"), dos_id),
                        "board_role": _detect_board_role("DOS Officer", officer.get("title")),
                    }
                )

        ceo_name = payload.get("ceo_name")
        if ceo_name:
            snapshot_as_of = _coerce_iso(payload.get("snapshot_as_of")) or last_refreshed_at
            contacts.append(
                {
                    "name": ceo_name,
                    "role": "DOS Chairman (Biennial)",
                    "source": "NY DOS Snapshot",
                    "source_record_id": payload.get("dos_id"),
                    "as_of_date": snapshot_as_of,
                    "publication_date": snapshot_as_of,
                    "snapshot_as_of": snapshot_as_of,
                    "address": payload.get("ceo_address"),
                    "confidence_hint": None,
                    "is_decision_maker": True,
                    "source_url": _dos_entity_url(payload.get("dos_id")),
                    "board_role": _detect_board_role("DOS Chairman", "Chairman"),
                }
            )

    deduped = _dedupe_contacts(contacts)
    metadata = {
        "management_company": management_company,
        "corporate_owner": corporate_owner,
        "dos_contacts_is_stale": dos_status in {"stale", "not_loaded", "refreshing"},
        "dos_contacts_status": dos_status,
        "dos_refresh_requested_at": _coerce_iso((payload or {}).get("refresh_requested_at")),
        "dos_contacts_last_refreshed_at": last_refreshed_at,
    }
    return deduped, metadata


async def get_lead_contacts(
    session: AsyncSession,
    lead_id: str,
) -> list[dict[str, Any]]:
    rows = await session.execute(
        text("""
            SELECT b.bbl, b.address, b.outreach_status
            FROM building_management bm
            JOIN buildings b ON b.bbl = bm.bbl
            WHERE bm.lead_id = :lead_id
              AND bm.is_current = true
            ORDER BY b.address ASC
        """),
        {"lead_id": lead_id},
    )

    results: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row._mapping)
        bbl = str(record.get("bbl"))
        address = record.get("address")
        contacts, _ = await get_building_contacts(
            session=session,
            bbl=bbl,
            building_address=address,
        )
        results.append(
            {
                "bbl": bbl,
                "address": address,
                "outreach_status": record.get("outreach_status"),
                "contacts": contacts,
            }
        )
    return results


def get_building_contacts_sync(
    conn: Connection,
    bbl: str,
    building_address: Optional[str] = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    contacts: list[dict[str, Any]] = []
    pm_address: Optional[str] = None
    management_company: Optional[str] = None
    corporate_owner: Optional[str] = None
    building_type: Optional[str] = None
    building_class: Optional[str] = None
    row = conn.execute(
        text("SELECT address, building_type, building_class FROM buildings WHERE bbl = :bbl"),
        {"bbl": bbl},
    ).first()
    if row:
        if not building_address:
            building_address = row[0]
        building_type = row[1]
        building_class = row[2]
    is_condo_coop = _is_condo_or_coop(building_type, building_class)

    rows = conn.execute(
        text("""
            SELECT id, registration_contact_id, contact_type, corporation_name, first_name, last_name,
                   title, business_address, business_city, business_state, business_zip, updated_at
            FROM building_contacts
            WHERE bbl = :bbl
            ORDER BY id ASC
        """),
        {"bbl": bbl},
    ).fetchall()

    for row in rows:
        data = dict(row._mapping)
        name = data.get("corporation_name") or f"{data.get('first_name', '')} {data.get('last_name', '')}".strip()
        if not name:
            continue
        role = data.get("contact_type") or "Unknown"
        board_role = _detect_board_role(role, data.get("title"))
        address = _format_contact_address(
            [
                data.get("business_address"),
                data.get("business_city"),
                data.get("business_state"),
                data.get("business_zip"),
            ]
        )
        contacts.append(
            {
                "name": name,
                "role": role,
                "source": "HPD Registration",
                "source_record_id": data.get("registration_contact_id"),
                "as_of_date": _coerce_iso(data.get("updated_at")),
                "publication_date": _coerce_iso(data.get("updated_at")),
                "address": address,
                "confidence_hint": None,
                "is_decision_maker": (
                    role in {"CorporateOwner", "Owner", "IndividualOwner", "HeadOfficer"}
                    or (bool(board_role) and is_condo_coop)
                ),
                "source_url": _hpd_source_url(data.get("registration_contact_id")),
                "board_role": board_role,
            }
        )
        if role == "Agent" and not pm_address:
            pm_address = address
            management_company = name
        if role == "CorporateOwner" and not corporate_owner:
            corporate_owner = name

    cache_row = conn.execute(
        text("""
            SELECT result, cached_at
            FROM dos_cache
            WHERE cache_key = :cache_key
        """),
        {"cache_key": f"officers:{bbl}"},
    ).first()
    payload, dos_status, last_refreshed_at = _get_dos_cache_payload_from_row(
        dict(cache_row._mapping) if cache_row else None
    )

    if payload:
        dos_id = str(payload.get("dos_id") or "").strip() or None
        for officer in payload.get("officers") or []:
            if not isinstance(officer, dict):
                continue
            officer_name = str(officer.get("name") or "").strip()
            if not officer_name:
                continue
            officer_address = _format_contact_address(
                [
                    officer.get("address"),
                    officer.get("city"),
                    officer.get("state"),
                    officer.get("zip"),
                ]
            )
            confidence_hint, resident_decision_maker = _classify_officer_confidence(
                officer_address=officer_address,
                building_address=building_address,
                pm_address=pm_address,
            )
            contacts.append(
                {
                    "name": officer_name,
                    "role": str(officer.get("title") or "").strip() or "DOS Officer",
                    "source": "NY DOS Filing",
                    "source_record_id": officer.get("filing_num"),
                    "as_of_date": _coerce_iso(officer.get("filing_date")),
                    "publication_date": _coerce_iso(officer.get("filing_date")),
                    "filing_date": _coerce_iso(officer.get("filing_date")),
                    "address": officer_address,
                    "confidence_hint": confidence_hint,
                    "is_decision_maker": resident_decision_maker or (
                        bool(_detect_board_role("DOS Officer", officer.get("title"))) and is_condo_coop
                    ),
                    "source_url": _dos_filing_url(officer.get("filing_num"), dos_id),
                    "board_role": _detect_board_role("DOS Officer", officer.get("title")),
                }
            )

        ceo_name = payload.get("ceo_name")
        if ceo_name:
            snapshot_as_of = _coerce_iso(payload.get("snapshot_as_of")) or last_refreshed_at
            contacts.append(
                {
                    "name": ceo_name,
                    "role": "DOS Chairman (Biennial)",
                    "source": "NY DOS Snapshot",
                    "source_record_id": payload.get("dos_id"),
                    "as_of_date": snapshot_as_of,
                    "publication_date": snapshot_as_of,
                    "snapshot_as_of": snapshot_as_of,
                    "address": payload.get("ceo_address"),
                    "confidence_hint": None,
                    "is_decision_maker": True,
                    "source_url": _dos_entity_url(payload.get("dos_id")),
                    "board_role": _detect_board_role("DOS Chairman", "Chairman"),
                }
            )

    return _dedupe_contacts(contacts), {
        "management_company": management_company,
        "corporate_owner": corporate_owner,
        "dos_contacts_is_stale": dos_status in {"stale", "not_loaded", "refreshing"},
        "dos_contacts_status": dos_status,
        "dos_refresh_requested_at": _coerce_iso((payload or {}).get("refresh_requested_at")),
        "dos_contacts_last_refreshed_at": last_refreshed_at,
    }
