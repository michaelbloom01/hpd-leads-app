"""Shared contact roster assembly for building, lead, and agent surfaces.

This module is the single source of truth for contact assembly and provenance.
It only reads from local data stores (building_contacts + dos_cache) and never
calls external APIs in request paths.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession


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


def _classify_officer_confidence(
    officer_address: Optional[str],
    building_address: Optional[str],
    pm_address: Optional[str],
) -> tuple[Optional[str], bool]:
    officer_upper = _normalize_addr(officer_address)
    building_upper = _normalize_addr(building_address)
    pm_upper = _normalize_addr(pm_address)

    if building_upper and officer_upper and building_upper[:20] in officer_upper:
        return "Likely board member (resident)", True
    if pm_upper and officer_upper and pm_upper[:20] in officer_upper:
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

    results = list(deduped.values())
    results.sort(
        key=lambda c: (
            0 if c.get("is_decision_maker") else 1,
            -_parse_date_for_sort(c.get("as_of_date")),
            str(c.get("name") or "").upper(),
        )
    )
    return results


def _get_dos_cache_payload_from_row(
    row: Any,
) -> tuple[Optional[dict[str, Any]], bool, Optional[str]]:
    if not row:
        return None, True, None

    raw = row.get("result")
    cached_at = row.get("cached_at")
    expires_at = row.get("expires_at")
    now = datetime.now(timezone.utc)

    is_stale = True
    parsed_expiry = _coerce_datetime(expires_at)
    if parsed_expiry is not None:
        is_stale = parsed_expiry <= now

    if not raw:
        return None, is_stale, _coerce_iso(cached_at)

    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(payload, dict):
            return None, is_stale, _coerce_iso(cached_at)
        return payload, is_stale, _coerce_iso(cached_at)
    except json.JSONDecodeError:
        return None, is_stale, _coerce_iso(cached_at)


async def get_building_contacts(
    session: AsyncSession,
    bbl: str,
    building_address: Optional[str] = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    contacts: list[dict[str, Any]] = []
    pm_address: Optional[str] = None
    management_company: Optional[str] = None
    corporate_owner: Optional[str] = None

    if not building_address:
        b_row = (
            await session.execute(
                text("SELECT address FROM buildings WHERE bbl = :bbl"),
                {"bbl": bbl},
            )
        ).first()
        building_address = b_row[0] if b_row else None

    contact_rows = await session.execute(
        text("""
            SELECT id, registration_contact_id, contact_type, corporation_name, first_name, last_name,
                   business_address, business_city, business_state, business_zip, updated_at
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
        address = _format_contact_address(
            [
                data.get("business_address"),
                data.get("business_city"),
                data.get("business_state"),
                data.get("business_zip"),
            ]
        )
        as_of_date = _coerce_iso(data.get("updated_at"))
        is_decision_maker = role in {"CorporateOwner", "Owner", "IndividualOwner"}

        contacts.append(
            {
                "name": name,
                "role": role,
                "source": "HPD Registration",
                "source_record_id": data.get("registration_contact_id"),
                "as_of_date": as_of_date,
                "address": address,
                "confidence_hint": None,
                "is_decision_maker": is_decision_maker,
            }
        )

        if role == "Agent" and not pm_address:
            pm_address = address
            management_company = name
        if role == "CorporateOwner" and not corporate_owner:
            corporate_owner = name

    try:
        cache_row = (
            await session.execute(
                text("""
                    SELECT result, cached_at, expires_at
                    FROM dos_cache
                    WHERE cache_key = :cache_key
                """),
                {"cache_key": f"officers:{bbl}"},
            )
        ).first()
    except Exception:
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

    payload, is_stale, last_refreshed_at = _get_dos_cache_payload_from_row(
        dict(cache_row._mapping) if cache_row else None
    )

    if payload:
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
                        "role": "DOS Officer",
                        "source": "NY DOS Filing",
                        "source_record_id": officer.get("filing_num"),
                        "as_of_date": _coerce_iso(officer.get("filing_date")),
                        "address": officer_address,
                        "confidence_hint": confidence_hint,
                        "is_decision_maker": resident_decision_maker,
                    }
                )

        ceo_name = payload.get("ceo_name")
        if ceo_name:
            contacts.append(
                {
                    "name": ceo_name,
                    "role": "DOS Chairman (Biennial)",
                    "source": "NY DOS Snapshot",
                    "source_record_id": payload.get("dos_id"),
                    "as_of_date": _coerce_iso(payload.get("snapshot_as_of")) or last_refreshed_at,
                    "address": payload.get("ceo_address"),
                    "confidence_hint": None,
                    "is_decision_maker": True,
                }
            )

    deduped = _dedupe_contacts(contacts)
    metadata = {
        "management_company": management_company,
        "corporate_owner": corporate_owner,
        "dos_contacts_is_stale": is_stale,
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

    if not building_address:
        row = conn.execute(
            text("SELECT address FROM buildings WHERE bbl = :bbl"),
            {"bbl": bbl},
        ).first()
        building_address = row[0] if row else None

    rows = conn.execute(
        text("""
            SELECT id, registration_contact_id, contact_type, corporation_name, first_name, last_name,
                   business_address, business_city, business_state, business_zip, updated_at
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
                "address": address,
                "confidence_hint": None,
                "is_decision_maker": role in {"CorporateOwner", "Owner", "IndividualOwner"},
            }
        )
        if role == "Agent" and not pm_address:
            pm_address = address
            management_company = name
        if role == "CorporateOwner" and not corporate_owner:
            corporate_owner = name

    try:
        cache_row = conn.execute(
            text("""
                SELECT result, cached_at, expires_at
                FROM dos_cache
                WHERE cache_key = :cache_key
            """),
            {"cache_key": f"officers:{bbl}"},
        ).first()
    except Exception:
        cache_row = conn.execute(
            text("""
                SELECT result, cached_at
                FROM dos_cache
                WHERE cache_key = :cache_key
            """),
            {"cache_key": f"officers:{bbl}"},
        ).first()
    payload, is_stale, last_refreshed_at = _get_dos_cache_payload_from_row(
        dict(cache_row._mapping) if cache_row else None
    )

    if payload:
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
                    "role": "DOS Officer",
                    "source": "NY DOS Filing",
                    "source_record_id": officer.get("filing_num"),
                    "as_of_date": _coerce_iso(officer.get("filing_date")),
                    "address": officer_address,
                    "confidence_hint": confidence_hint,
                    "is_decision_maker": resident_decision_maker,
                }
            )

        ceo_name = payload.get("ceo_name")
        if ceo_name:
            contacts.append(
                {
                    "name": ceo_name,
                    "role": "DOS Chairman (Biennial)",
                    "source": "NY DOS Snapshot",
                    "source_record_id": payload.get("dos_id"),
                    "as_of_date": _coerce_iso(payload.get("snapshot_as_of")) or last_refreshed_at,
                    "address": payload.get("ceo_address"),
                    "confidence_hint": None,
                    "is_decision_maker": True,
                }
            )

    return _dedupe_contacts(contacts), {
        "management_company": management_company,
        "corporate_owner": corporate_owner,
        "dos_contacts_is_stale": is_stale,
        "dos_contacts_last_refreshed_at": last_refreshed_at,
    }
