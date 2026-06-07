"""Target intake, matching, scoring, discovery, and dossier helpers."""

from __future__ import annotations

import json
import re
import uuid
from statistics import mean
from typing import Any, Optional
from urllib.parse import urlparse

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.contact_roster import get_lead_contacts
from src.transform.normalize import normalize_name, normalize_name_for_grouping, normalize_phone


DEFAULT_THESIS_NAME = "Default PM Acquisition Thesis"
DEFAULT_THESIS_DESCRIPTION = (
    "Prioritize condo/co-op heavy PM firms with workable portfolio size, reachable contacts, "
    "and likely succession or timing signals."
)
DEFAULT_THESIS_CRITERIA = {
    "portfolio_size": {"ideal_min": 15, "ideal_max": 120},
    "units": {"ideal_min": 300, "ideal_max": 5000},
    "contactability_weight": 0.15,
    "focus_weight": 0.2,
    "succession_weight": 0.25,
    "timing_weight": 0.2,
    "reachability_weight": 0.2,
}


def normalize_target_name(name: str) -> str:
    return normalize_name_for_grouping(normalize_name(name or ""))


def normalize_domain(value: Optional[str]) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    candidate = raw if "://" in raw else f"https://{raw}"
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return None
    netloc = (parsed.netloc or parsed.path or "").strip().lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc or None


def average_numeric_estimate(raw: Optional[str]) -> Optional[float]:
    text_value = str(raw or "").strip()
    if not text_value:
        return None
    numbers = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", text_value)]
    if not numbers:
        return None
    return float(sum(numbers) / len(numbers))


def score_target_item_snapshot(item: dict[str, Any], lead: Optional[dict[str, Any]]) -> dict[str, Any]:
    portfolio_size = float((lead or {}).get("portfolio_size") or average_numeric_estimate(item.get("portfolio_estimate")) or 0)
    total_units = float((lead or {}).get("total_units") or average_numeric_estimate(item.get("units_estimate")) or 0)
    has_phone = bool((lead or {}).get("phone") or item.get("phone"))
    has_email = bool((lead or {}).get("email"))
    has_website = bool((lead or {}).get("website") or item.get("website"))

    focus_text = " ".join(
        str(item.get(key) or "")
        for key in ("condo_focus", "acquisition_fit_notes", "notes")
    ).upper()
    ownership_text = " ".join(
        str(item.get(key) or "")
        for key in ("ownership", "key_principals", "acquisition_fit_notes", "risk_flag", "established")
    ).upper()
    lead_building_types = json.dumps((lead or {}).get("building_types") or {}).upper()

    portfolio_component = 0.0
    if 15 <= portfolio_size <= 120:
        portfolio_component = 25.0
    elif 5 <= portfolio_size <= 180:
        portfolio_component = 16.0
    elif portfolio_size > 0:
        portfolio_component = 8.0

    units_component = 0.0
    if 300 <= total_units <= 5000:
        units_component = 20.0
    elif 100 <= total_units <= 8000:
        units_component = 12.0
    elif total_units > 0:
        units_component = 6.0

    contact_component = 0.0
    if has_phone:
        contact_component += 6.0
    if has_email:
        contact_component += 5.0
    if has_website:
        contact_component += 4.0

    focus_component = 0.0
    if any(token in focus_text for token in ("CO-OP", "COOP", "CONDO")) or any(
        token in lead_building_types for token in ("CONDO", "COOP", "CO-OP")
    ):
        focus_component = 15.0
    elif focus_text:
        focus_component = 7.0

    succession_component = 0.0
    if any(token in ownership_text for token in ("FOUNDER", "FAMILY", "2ND GEN", "3RD GEN", "4TH GEN", "SUCCESSION")):
        succession_component += 12.0
    established_year = average_numeric_estimate(item.get("established"))
    if established_year and established_year <= 2000:
        succession_component += 8.0
    elif established_year and established_year <= 2010:
        succession_component += 4.0

    timing_component = 0.0
    if any(token in ownership_text for token in ("BOUTIQUE", "LEAN", "OPAQUE", "RETENTION", "TEST DRIVE")):
        timing_component += 6.0
    if any(token in ownership_text for token in ("NOT SELL", "OWNER-OPERATOR", "EXPENSIVE", "TOO LARGE")):
        timing_component -= 6.0
    if float((lead or {}).get("estimated_annual_revenue") or 0) > 0:
        timing_component += 4.0

    total_score = max(
        0.0,
        min(
            100.0,
            round(
                portfolio_component
                + units_component
                + contact_component
                + focus_component
                + succession_component
                + timing_component,
                1,
            ),
        ),
    )
    summary_parts: list[str] = []
    if focus_component >= 15:
        summary_parts.append("strong condo/co-op fit")
    if succession_component >= 12:
        summary_parts.append("succession signal")
    if contact_component >= 10:
        summary_parts.append("contactable")
    if timing_component < 0:
        summary_parts.append("timing/risk penalty")
    if not summary_parts:
        summary_parts.append("needs more diligence")

    return {
        "score": total_score,
        "summary": ", ".join(summary_parts),
        "breakdown": {
            "portfolio_fit": portfolio_component,
            "units_fit": units_component,
            "contactability": contact_component,
            "focus_fit": focus_component,
            "succession_signal": succession_component,
            "timing_signal": timing_component,
        },
    }


def score_lead_candidate(item: dict[str, Any], lead: dict[str, Any]) -> tuple[float, list[str]]:
    reasons: list[str] = []
    confidence = 0.0
    item_norm = normalize_target_name(str(item.get("company_name") or ""))
    lead_norm = str(lead.get("normalized_name") or "").strip().upper()
    if item_norm and lead_norm and item_norm == lead_norm:
        confidence += 0.55
        reasons.append("normalized_name_exact")
    fuzzy_name = normalize_name(str(item.get("company_name") or ""))
    if fuzzy_name and any(
        fuzzy_name in str(lead.get(field) or "").upper()
        for field in ("company_name", "agent_name", "owner_name")
    ):
        confidence += 0.15
        reasons.append("name_contains")

    item_domain = normalize_domain(item.get("website"))
    lead_domain = normalize_domain(lead.get("website"))
    if item_domain and lead_domain and item_domain == lead_domain:
        confidence += 0.2
        reasons.append("website_domain")

    item_phone = normalize_phone(item.get("phone"))
    lead_phone = normalize_phone(lead.get("phone"))
    if item_phone and lead_phone and item_phone == lead_phone:
        confidence += 0.15
        reasons.append("phone_exact")

    geography = str(item.get("geography") or "").upper()
    borough = str(lead.get("primary_borough") or "").upper()
    if geography and borough and borough in geography:
        confidence += 0.05
        reasons.append("geography_overlap")

    return round(min(confidence, 0.99), 2), reasons


def score_adjacent_candidate(seed_summary: dict[str, Any], lead: dict[str, Any]) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 0.0
    avg_portfolio = float(seed_summary.get("avg_portfolio_size") or 0)
    avg_units = float(seed_summary.get("avg_units") or 0)
    lead_portfolio = float(lead.get("portfolio_size") or 0)
    lead_units = float(lead.get("total_units") or 0)

    if avg_portfolio > 0 and lead_portfolio > 0:
        distance = abs(lead_portfolio - avg_portfolio) / max(avg_portfolio, 1)
        score += max(0.0, 30.0 - min(distance, 2.0) * 15.0)
        if distance <= 0.35:
            reasons.append("portfolio_similarity")

    if avg_units > 0 and lead_units > 0:
        distance = abs(lead_units - avg_units) / max(avg_units, 1)
        score += max(0.0, 25.0 - min(distance, 2.0) * 12.5)
        if distance <= 0.4:
            reasons.append("unit_similarity")

    seed_boroughs = set(seed_summary.get("boroughs") or [])
    lead_borough = str(lead.get("primary_borough") or "").upper()
    if lead_borough and lead_borough in seed_boroughs:
        score += 15.0
        reasons.append("borough_overlap")

    building_types_text = json.dumps(lead.get("building_types") or {}).upper()
    if seed_summary.get("prefers_condo_focus") and any(token in building_types_text for token in ("CONDO", "COOP", "CO-OP")):
        score += 15.0
        reasons.append("condo_focus")

    if (lead.get("phone") or lead.get("email") or lead.get("website")):
        score += 10.0
        reasons.append("contactable")

    if float(lead.get("estimated_annual_revenue") or 0) > 0:
        score += 5.0
        reasons.append("revenue_estimate_available")

    return round(min(score, 100.0), 1), reasons


async def ensure_default_thesis(session: AsyncSession) -> str:
    row = (
        await session.execute(
            text(
                """
                SELECT thesis_id
                FROM acquisition_theses
                WHERE is_default = true
                ORDER BY created_at ASC
                LIMIT 1
                """
            )
        )
    ).first()
    if row:
        return str(row[0])

    thesis_id = str(uuid.uuid4())
    await session.execute(
        text(
            """
            INSERT INTO acquisition_theses (thesis_id, name, description, criteria, is_default, created_at, updated_at)
            VALUES (:thesis_id, :name, :description, CAST(:criteria AS JSONB), true, NOW(), NOW())
            """
        ),
        {
            "thesis_id": thesis_id,
            "name": DEFAULT_THESIS_NAME,
            "description": DEFAULT_THESIS_DESCRIPTION,
            "criteria": json.dumps(DEFAULT_THESIS_CRITERIA),
        },
    )
    return thesis_id


async def list_target_lists(session: AsyncSession, user_id: str) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                """
                SELECT
                    tl.*,
                    COUNT(tli.target_item_id)::int AS item_count,
                    COUNT(*) FILTER (WHERE tli.match_status = 'matched')::int AS matched_count
                FROM target_lists tl
                LEFT JOIN target_list_items tli ON tli.target_list_id = tl.target_list_id
                WHERE tl.user_id = :user_id
                GROUP BY tl.target_list_id
                ORDER BY tl.updated_at DESC
                """
            ),
            {"user_id": user_id},
        )
    ).mappings().all()
    return [dict(row) for row in rows]


async def get_target_list(session: AsyncSession, target_list_id: str) -> dict[str, Any] | None:
    list_row = (
        await session.execute(
            text("SELECT * FROM target_lists WHERE target_list_id = :target_list_id"),
            {"target_list_id": target_list_id},
        )
    ).mappings().first()
    if not list_row:
        return None
    items = (
        await session.execute(
            text(
                """
                SELECT *
                FROM target_list_items
                WHERE target_list_id = :target_list_id
                ORDER BY COALESCE(thesis_score, 0) DESC, priority_rank DESC, company_name ASC
                """
            ),
            {"target_list_id": target_list_id},
        )
    ).mappings().all()
    return {**dict(list_row), "items": [dict(item) for item in items]}


async def create_target_list(
    session: AsyncSession,
    *,
    user_id: str,
    name: str,
    description: str | None,
    targeting_mode: str,
    source_notes: str | None,
) -> dict[str, Any]:
    thesis_id = await ensure_default_thesis(session)
    target_list_id = str(uuid.uuid4())
    await session.execute(
        text(
            """
            INSERT INTO target_lists (
                target_list_id, user_id, name, description, targeting_mode, thesis_id, source_notes, created_at, updated_at
            )
            VALUES (
                :target_list_id, :user_id, :name, :description, :targeting_mode, :thesis_id, :source_notes, NOW(), NOW()
            )
            """
        ),
        {
            "target_list_id": target_list_id,
            "user_id": user_id,
            "name": name,
            "description": description,
            "targeting_mode": targeting_mode,
            "thesis_id": thesis_id,
            "source_notes": source_notes,
        },
    )
    return {"target_list_id": target_list_id, "name": name, "thesis_id": thesis_id}


async def _create_alert(
    session: AsyncSession,
    *,
    alert_type: str,
    description: str,
    details: dict[str, Any],
    target_item_id: str | None = None,
    lead_id: str | None = None,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO change_alerts (
                alert_type, lead_id, target_item_id, description, details, dismissed, created_at, updated_at
            )
            VALUES (
                :alert_type, :lead_id, :target_item_id, :description, CAST(:details AS JSONB), false, NOW(), NOW()
            )
            """
        ),
        {
            "alert_type": alert_type,
            "lead_id": lead_id,
            "target_item_id": target_item_id,
            "description": description,
            "details": json.dumps(details),
        },
    )


async def refresh_target_matches(session: AsyncSession, target_item_id: str) -> dict[str, Any]:
    item = (
        await session.execute(
            text("SELECT * FROM target_list_items WHERE target_item_id = :target_item_id"),
            {"target_item_id": target_item_id},
        )
    ).mappings().first()
    if not item:
        raise ValueError("Target item not found")

    domain = normalize_domain(item.get("website"))
    phone = normalize_phone(item.get("phone"))
    fuzzy = f"%{normalize_name(str(item.get('company_name') or ''))}%"
    leads = (
        await session.execute(
            text(
                """
                SELECT
                    lead_id, normalized_name, company_name, agent_name, owner_name,
                    primary_borough, phone, email, website, portfolio_size, total_units,
                    estimated_annual_revenue, building_types
                FROM leads
                WHERE normalized_name = :normalized_name
                   OR COALESCE(company_name, '') ILIKE :fuzzy
                   OR COALESCE(agent_name, '') ILIKE :fuzzy
                   OR COALESCE(owner_name, '') ILIKE :fuzzy
                   OR (:domain IS NOT NULL AND COALESCE(website, '') ILIKE :domain_like)
                   OR (:phone_norm IS NOT NULL AND regexp_replace(COALESCE(phone, ''), '\\D', '', 'g') = regexp_replace(:phone_norm, '\\D', '', 'g'))
                ORDER BY score DESC NULLS LAST, portfolio_size DESC NULLS LAST
                LIMIT 10
                """
            ),
            {
                "normalized_name": item["normalized_name"],
                "fuzzy": fuzzy,
                "domain": domain,
                "domain_like": f"%{domain}%" if domain else None,
                "phone_norm": phone,
            },
        )
    ).mappings().all()

    await session.execute(text("DELETE FROM target_matches WHERE target_item_id = :target_item_id"), {"target_item_id": target_item_id})

    candidate_rows: list[dict[str, Any]] = []
    for lead in leads:
        confidence, reasons = score_lead_candidate(dict(item), dict(lead))
        if confidence <= 0:
            continue
        candidate_rows.append({**dict(lead), "confidence": confidence, "reasons": reasons})

    candidate_rows.sort(key=lambda row: (row["confidence"], float(row.get("estimated_annual_revenue") or 0), float(row.get("portfolio_size") or 0)), reverse=True)
    best = candidate_rows[0] if candidate_rows else None
    second = candidate_rows[1] if len(candidate_rows) > 1 else None

    selected_lead_id = None
    selected_canonical_entity_id = None
    match_status = "unmatched"
    if best:
        if second and abs(best["confidence"] - second["confidence"]) < 0.1:
            match_status = "ambiguous"
        elif best["confidence"] >= 0.55:
            match_status = "matched"
            selected_lead_id = str(best["lead_id"])
    if selected_lead_id:
        entity_row = (
            await session.execute(
                text(
                    """
                    SELECT canonical_entity_id
                    FROM canonical_entity_leads
                    WHERE lead_id = :lead_id
                    ORDER BY is_primary DESC, confidence_score DESC NULLS LAST, updated_at DESC NULLS LAST
                    LIMIT 1
                    """
                ),
                {"lead_id": selected_lead_id},
            )
        ).first()
        selected_canonical_entity_id = str(entity_row[0]) if entity_row else None

    for idx, candidate in enumerate(candidate_rows):
        candidate_entity_row = (
            await session.execute(
                text(
                    """
                    SELECT canonical_entity_id
                    FROM canonical_entity_leads
                    WHERE lead_id = :lead_id
                    ORDER BY is_primary DESC, confidence_score DESC NULLS LAST, updated_at DESC NULLS LAST
                    LIMIT 1
                    """
                ),
                {"lead_id": candidate["lead_id"]},
            )
        ).first()
        await session.execute(
            text(
                """
                INSERT INTO target_matches (
                    target_item_id, lead_id, canonical_entity_id, match_type, confidence_score, selected, reasons, created_at, updated_at
                )
                VALUES (
                    :target_item_id, :lead_id, :canonical_entity_id, :match_type, :confidence_score, :selected,
                    CAST(:reasons AS JSONB), NOW(), NOW()
                )
                """
            ),
            {
                "target_item_id": target_item_id,
                "lead_id": candidate["lead_id"],
                "canonical_entity_id": candidate_entity_row[0] if candidate_entity_row else None,
                "match_type": "auto_match",
                "confidence_score": candidate["confidence"],
                "selected": idx == 0 and match_status == "matched",
                "reasons": json.dumps(candidate["reasons"]),
            },
        )

    thesis = score_target_item_snapshot(dict(item), best if selected_lead_id else None)
    await session.execute(
        text(
            """
            UPDATE target_list_items
            SET matched_lead_id = :matched_lead_id,
                canonical_entity_id = :canonical_entity_id,
                match_status = :match_status,
                thesis_score = :thesis_score,
                thesis_summary = :thesis_summary,
                thesis_breakdown = CAST(:thesis_breakdown AS JSONB),
                updated_at = NOW()
            WHERE target_item_id = :target_item_id
            """
        ),
        {
            "target_item_id": target_item_id,
            "matched_lead_id": selected_lead_id,
            "canonical_entity_id": selected_canonical_entity_id,
            "match_status": match_status,
            "thesis_score": thesis["score"],
            "thesis_summary": thesis["summary"],
            "thesis_breakdown": json.dumps(thesis["breakdown"]),
        },
    )

    if match_status in {"ambiguous", "unmatched"}:
        await _create_alert(
            session,
            alert_type="target_match_review",
            description=f"Target '{item['company_name']}' needs match review ({match_status})",
            details={
                "target_item_id": target_item_id,
                "company_name": item["company_name"],
                "match_status": match_status,
                "candidate_count": len(candidate_rows),
            },
            target_item_id=target_item_id,
            lead_id=selected_lead_id,
        )

    return {
        "target_item_id": target_item_id,
        "match_status": match_status,
        "matched_lead_id": selected_lead_id,
        "candidate_count": len(candidate_rows),
        "thesis_score": thesis["score"],
    }


async def import_target_items(session: AsyncSession, target_list_id: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for row in rows:
        company_name = str(row.get("company_name") or "").strip()
        if not company_name:
            continue
        normalized_name = normalize_target_name(company_name)
        website = str(row.get("website") or "").strip() or None
        phone = str(row.get("phone") or "").strip() or None
        existing = (
            await session.execute(
                text(
                    """
                    SELECT target_item_id
                    FROM target_list_items
                    WHERE target_list_id = :target_list_id
                      AND normalized_name = :normalized_name
                    ORDER BY updated_at DESC NULLS LAST
                    LIMIT 1
                    """
                ),
                {"target_list_id": target_list_id, "normalized_name": normalized_name},
            )
        ).first()
        target_item_id = str(existing[0]) if existing else str(uuid.uuid4())
        await session.execute(
            text(
                """
                INSERT INTO target_list_items (
                    target_item_id, target_list_id, company_name, normalized_name, established,
                    portfolio_estimate, units_estimate, geography, ownership, key_principals,
                    condo_focus, website, website_domain, phone, phone_normalized, address,
                    tier, acquisition_fit_notes, risk_flag, notes, raw_profile, created_at, updated_at
                )
                VALUES (
                    :target_item_id, :target_list_id, :company_name, :normalized_name, :established,
                    :portfolio_estimate, :units_estimate, :geography, :ownership, :key_principals,
                    :condo_focus, :website, :website_domain, :phone, :phone_normalized, :address,
                    :tier, :acquisition_fit_notes, :risk_flag, :notes, CAST(:raw_profile AS JSONB), NOW(), NOW()
                )
                ON CONFLICT (target_item_id)
                DO UPDATE SET
                    company_name = EXCLUDED.company_name,
                    normalized_name = EXCLUDED.normalized_name,
                    established = EXCLUDED.established,
                    portfolio_estimate = EXCLUDED.portfolio_estimate,
                    units_estimate = EXCLUDED.units_estimate,
                    geography = EXCLUDED.geography,
                    ownership = EXCLUDED.ownership,
                    key_principals = EXCLUDED.key_principals,
                    condo_focus = EXCLUDED.condo_focus,
                    website = EXCLUDED.website,
                    website_domain = EXCLUDED.website_domain,
                    phone = EXCLUDED.phone,
                    phone_normalized = EXCLUDED.phone_normalized,
                    address = EXCLUDED.address,
                    tier = EXCLUDED.tier,
                    acquisition_fit_notes = EXCLUDED.acquisition_fit_notes,
                    risk_flag = EXCLUDED.risk_flag,
                    notes = EXCLUDED.notes,
                    raw_profile = EXCLUDED.raw_profile,
                    updated_at = NOW()
                """
            ),
            {
                "target_item_id": target_item_id,
                "target_list_id": target_list_id,
                "company_name": company_name,
                "normalized_name": normalized_name,
                "established": row.get("established"),
                "portfolio_estimate": row.get("portfolio_estimate"),
                "units_estimate": row.get("units_estimate"),
                "geography": row.get("geography"),
                "ownership": row.get("ownership"),
                "key_principals": row.get("key_principals"),
                "condo_focus": row.get("condo_focus"),
                "website": website,
                "website_domain": normalize_domain(website),
                "phone": phone,
                "phone_normalized": normalize_phone(phone),
                "address": row.get("address"),
                "tier": row.get("tier"),
                "acquisition_fit_notes": row.get("acquisition_fit_notes"),
                "risk_flag": row.get("risk_flag"),
                "notes": row.get("notes"),
                "raw_profile": json.dumps(row),
            },
        )
        results.append(await refresh_target_matches(session, target_item_id))

    await session.execute(
        text("UPDATE target_lists SET updated_at = NOW() WHERE target_list_id = :target_list_id"),
        {"target_list_id": target_list_id},
    )
    return results


async def select_target_match(session: AsyncSession, target_item_id: str, lead_id: str) -> dict[str, Any]:
    await session.execute(
        text("UPDATE target_matches SET selected = false, updated_at = NOW() WHERE target_item_id = :target_item_id"),
        {"target_item_id": target_item_id},
    )
    row = (
        await session.execute(
            text(
                """
                SELECT canonical_entity_id
                FROM target_matches
                WHERE target_item_id = :target_item_id
                  AND lead_id = :lead_id
                ORDER BY confidence_score DESC NULLS LAST
                LIMIT 1
                """
            ),
            {"target_item_id": target_item_id, "lead_id": lead_id},
        )
    ).first()
    await session.execute(
        text(
            """
            UPDATE target_matches
            SET selected = true, updated_at = NOW()
            WHERE target_item_id = :target_item_id
              AND lead_id = :lead_id
            """
        ),
        {"target_item_id": target_item_id, "lead_id": lead_id},
    )
    lead_row = (
        await session.execute(text("SELECT * FROM leads WHERE lead_id = :lead_id"), {"lead_id": lead_id})
    ).mappings().first()
    item_row = (
        await session.execute(text("SELECT * FROM target_list_items WHERE target_item_id = :target_item_id"), {"target_item_id": target_item_id})
    ).mappings().first()
    thesis = score_target_item_snapshot(dict(item_row or {}), dict(lead_row or {}))
    await session.execute(
        text(
            """
            UPDATE target_list_items
            SET matched_lead_id = :lead_id,
                canonical_entity_id = :canonical_entity_id,
                match_status = 'matched',
                thesis_score = :thesis_score,
                thesis_summary = :thesis_summary,
                thesis_breakdown = CAST(:thesis_breakdown AS JSONB),
                updated_at = NOW()
            WHERE target_item_id = :target_item_id
            """
        ),
        {
            "target_item_id": target_item_id,
            "lead_id": lead_id,
            "canonical_entity_id": row[0] if row else None,
            "thesis_score": thesis["score"],
            "thesis_summary": thesis["summary"],
            "thesis_breakdown": json.dumps(thesis["breakdown"]),
        },
    )
    return {"target_item_id": target_item_id, "lead_id": lead_id, "status": "selected"}


async def discover_adjacent_targets(session: AsyncSession, target_list_id: str, limit: int = 25) -> list[dict[str, Any]]:
    seed_rows = (
        await session.execute(
            text(
                """
                SELECT l.*, tli.condo_focus, tli.geography
                FROM target_list_items tli
                JOIN leads l ON l.lead_id = tli.matched_lead_id
                WHERE tli.target_list_id = :target_list_id
                """
            ),
            {"target_list_id": target_list_id},
        )
    ).mappings().all()
    if not seed_rows:
        return []

    portfolio_values = [float(row.get("portfolio_size") or 0) for row in seed_rows if row.get("portfolio_size")]
    unit_values = [float(row.get("total_units") or 0) for row in seed_rows if row.get("total_units")]

    seed_summary = {
        "avg_portfolio_size": mean(portfolio_values) if portfolio_values else 0.0,
        "avg_units": mean(unit_values) if unit_values else 0.0,
        "boroughs": sorted({str(row.get("primary_borough") or "").upper() for row in seed_rows if str(row.get("primary_borough") or "").strip()}),
        "prefers_condo_focus": any("CONDO" in str(row.get("condo_focus") or "").upper() or "COOP" in str(row.get("condo_focus") or "").upper() for row in seed_rows),
    }
    seed_lead_ids = [str(row["lead_id"]) for row in seed_rows]
    candidates = (
        await session.execute(
            text(
                """
                SELECT *
                FROM leads
                WHERE lead_id NOT IN :seed_lead_ids
                ORDER BY score DESC NULLS LAST, portfolio_size DESC NULLS LAST
                LIMIT 200
                """
            ).bindparams(bindparam("seed_lead_ids", expanding=True)),
            {"seed_lead_ids": seed_lead_ids},
        )
    ).mappings().all()
    scored: list[dict[str, Any]] = []
    for candidate in candidates:
        discovery_score, reasons = score_adjacent_candidate(seed_summary, dict(candidate))
        if discovery_score <= 0:
            continue
        scored.append(
            {
                "lead_id": candidate["lead_id"],
                "company_name": candidate.get("company_name") or candidate.get("agent_name") or candidate.get("owner_name"),
                "primary_borough": candidate.get("primary_borough"),
                "portfolio_size": candidate.get("portfolio_size"),
                "total_units": candidate.get("total_units"),
                "estimated_annual_revenue": candidate.get("estimated_annual_revenue"),
                "discovery_score": discovery_score,
                "reasons": reasons,
            }
        )
    scored.sort(key=lambda row: (row["discovery_score"], float(row.get("estimated_annual_revenue") or 0)), reverse=True)
    return scored[:limit]


async def build_target_dossier(session: AsyncSession, target_item_id: str) -> dict[str, Any] | None:
    item = (
        await session.execute(
            text("SELECT * FROM target_list_items WHERE target_item_id = :target_item_id"),
            {"target_item_id": target_item_id},
        )
    ).mappings().first()
    if not item:
        return None

    matches = (
        await session.execute(
            text(
                """
                SELECT tm.*, l.company_name, l.agent_name, l.owner_name, l.primary_borough, l.portfolio_size, l.total_units
                FROM target_matches tm
                LEFT JOIN leads l ON l.lead_id = tm.lead_id
                WHERE tm.target_item_id = :target_item_id
                ORDER BY tm.selected DESC, tm.confidence_score DESC NULLS LAST
                """
            ),
            {"target_item_id": target_item_id},
        )
    ).mappings().all()

    lead = None
    building_contacts: list[dict[str, Any]] = []
    linked_buildings: list[dict[str, Any]] = []
    outreach_events: list[dict[str, Any]] = []
    people_graph: list[dict[str, Any]] = []

    if item.get("matched_lead_id"):
        lead = (
            await session.execute(
                text("SELECT * FROM leads WHERE lead_id = :lead_id"),
                {"lead_id": item["matched_lead_id"]},
            )
        ).mappings().first()
        building_contacts = await get_lead_contacts(session, str(item["matched_lead_id"]))
        linked_buildings = [
            dict(row)
            for row in (
                await session.execute(
                    text(
                        """
                        SELECT b.bbl, b.address, b.borough, b.unit_count, b.building_type, b.outreach_status
                        FROM building_management bm
                        JOIN buildings b ON b.bbl = bm.bbl
                        WHERE bm.lead_id = :lead_id
                          AND bm.is_current = true
                        ORDER BY b.address ASC
                        """
                    ),
                    {"lead_id": item["matched_lead_id"]},
                )
            ).mappings().all()
        ]
        outreach_events = [
            dict(row)
            for row in (
                await session.execute(
                    text(
                        """
                        SELECT *
                        FROM outreach_events
                        WHERE lead_id = :lead_id OR target_item_id = :target_item_id
                        ORDER BY event_timestamp DESC NULLS LAST, created_at DESC
                        """
                    ),
                    {"lead_id": item["matched_lead_id"], "target_item_id": target_item_id},
                )
            ).mappings().all()
        ]
    else:
        outreach_events = [
            dict(row)
            for row in (
                await session.execute(
                    text(
                        """
                        SELECT *
                        FROM outreach_events
                        WHERE target_item_id = :target_item_id
                        ORDER BY event_timestamp DESC NULLS LAST, created_at DESC
                        """
                    ),
                    {"target_item_id": target_item_id},
                )
            ).mappings().all()
        ]

    seen_people: set[tuple[str, str]] = set()
    if lead:
        for name, role, source in [
            (lead.get("owner_principal"), "Principal", "Lead enrichment"),
            (lead.get("primary_contact"), str(lead.get("primary_contact_title") or "Primary contact"), "Lead"),
            (lead.get("company_name") or lead.get("agent_name") or lead.get("owner_name"), "Target entity", "Lead"),
        ]:
            text_name = str(name or "").strip()
            if not text_name:
                continue
            key = (text_name.upper(), str(role))
            if key in seen_people:
                continue
            seen_people.add(key)
            people_graph.append(
                {
                    "name": text_name,
                    "role": role,
                    "source": source,
                    "is_decision_maker": role in {"Principal", "Primary contact"},
                }
            )
    for building in building_contacts:
        for contact in building.get("contacts") or []:
            text_name = str(contact.get("name") or "").strip()
            if not text_name:
                continue
            key = (text_name.upper(), str(contact.get("role") or ""))
            if key in seen_people:
                continue
            seen_people.add(key)
            people_graph.append(
                {
                    "name": text_name,
                    "role": contact.get("role"),
                    "source": contact.get("source"),
                    "is_decision_maker": bool(contact.get("is_decision_maker")),
                    "address": contact.get("address"),
                }
            )

    missing_diligence: list[str] = []
    if not item.get("matched_lead_id"):
        missing_diligence.append("No confirmed internal lead match")
    if len(matches) > 1 and item.get("match_status") == "ambiguous":
        missing_diligence.append("Multiple plausible internal matches require manual resolution")
    if not (lead or {}).get("phone") and not item.get("phone"):
        missing_diligence.append("No direct phone on file")
    if not (lead or {}).get("email"):
        missing_diligence.append("No direct email on file")
    if not ((lead or {}).get("owner_principal") or item.get("key_principals")):
        missing_diligence.append("Principal / leadership contact path is incomplete")
    if not linked_buildings:
        missing_diligence.append("No current building footprint attached to matched lead")
    if not item.get("acquisition_fit_notes"):
        missing_diligence.append("Acquisition fit note not yet confirmed")
    if not item.get("risk_flag"):
        missing_diligence.append("Key risk flag not yet reviewed")

    adjacent = await discover_adjacent_targets(session, str(item["target_list_id"]), limit=5)

    return {
        "target_item": dict(item),
        "matches": [dict(match) for match in matches],
        "matched_lead": dict(lead) if lead else None,
        "linked_buildings": linked_buildings,
        "building_contacts": building_contacts,
        "people_graph": people_graph,
        "outreach_events": outreach_events,
        "missing_diligence": missing_diligence,
        "adjacent_targets": adjacent,
    }
