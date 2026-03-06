"""Runtime-safe lead generation service.

Shared lead-generation logic lives here so Celery/tasks can import it from the
application package without depending on the CLI-only `scripts/` directory.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.db.session import get_sync_url
from src.transform.normalize import normalize_name, normalize_name_for_grouping

logger = logging.getLogger(__name__)

SEED_CONTACT_TYPES = {
    "Agent",
    "ManagementCompany",
    "Owner",
    "CorporateOwner",
    "IndividualOwner",
}

OWNER_CONTACT_TYPES = {"Owner", "CorporateOwner", "IndividualOwner"}

PLACEHOLDER_NAMES = {
    "",
    "N A",
    "N/A",
    "NA",
    "NONE",
    "NULL",
    "UNKNOWN",
    "TBD",
    "-",
    "--",
}

JUNK_TOKENS = {
    "BOARD",
    "MEMBER",
    "OFFICER",
    "OWNER",
    "TENANT",
    "RESIDENT",
    "UNKNOWN",
    "NONE",
    "TRUSTEE",
}


def make_lead_id(name: str) -> str:
    """Stable lead ID from normalized name. Max 12 chars to match DB schema."""
    normalized = name.strip().upper()
    return hashlib.md5(normalized.encode()).hexdigest()[:12]


def _raw_name_from_contact(row: Any) -> str:
    corp = (row.corporation_name or "").strip()
    if corp:
        return corp
    first = (row.first_name or "").strip()
    last = (row.last_name or "").strip()
    return " ".join(part for part in [first, last] if part).strip()


def _is_seed_contact(row: Any) -> bool:
    return str(row.contact_type or "").strip() in SEED_CONTACT_TYPES


def _is_probably_junk_name(raw_name: str) -> bool:
    normalized = normalize_name(raw_name)
    if normalized in PLACEHOLDER_NAMES:
        return True
    if len(normalized) < 3:
        return True
    if re.fullmatch(r"\d+", normalized):
        return True

    tokens = normalized.split()
    if not tokens:
        return True
    if len(tokens) <= 2 and all(token in JUNK_TOKENS for token in tokens):
        return True
    return False


def _display_name_for_group(raw_name: str) -> str:
    return normalize_name(raw_name)


def _merge_lead_records(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    target["bbl_set"].update(incoming.get("bbl_set", set()))
    target["address_set"].update(incoming.get("address_set", set()))
    target["unit_counts_by_bbl"].update(incoming.get("unit_counts_by_bbl", {}))
    target["unit_count_total"] = sum(target["unit_counts_by_bbl"].values())
    target["building_classes"].extend(incoming.get("building_classes", []))

    for borough, count in (incoming.get("boroughs") or {}).items():
        target["boroughs"][borough] = target["boroughs"].get(borough, 0) + count

    for field in ("agent_name", "owner_name", "company_name", "address"):
        if not target.get(field) and incoming.get(field):
            target[field] = incoming[field]

    if target.get("entity_type") != "company" and incoming.get("entity_type") == "company":
        target["entity_type"] = "company"


def _collapse_duplicate_company_leads(leads_map: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    collapsed: dict[str, dict[str, Any]] = {}
    company_aliases: dict[str, str] = {}

    for key, lead in leads_map.items():
        company_name = normalize_name_for_grouping(lead.get("company_name") or "")
        if company_name:
            canonical_key = company_aliases.get(company_name)
            if canonical_key is None:
                company_aliases[company_name] = key
                collapsed[key] = lead
                continue
            _merge_lead_records(collapsed[canonical_key], lead)
            continue
        collapsed[key] = lead

    return collapsed


def compute_score(portfolio_size: int, total_units: int, violation_count: int = 0) -> float:
    """Simple heuristic score used during lead generation bootstrap."""
    score = 0.0
    if portfolio_size >= 50:
        score += 40
    elif portfolio_size >= 20:
        score += 30
    elif portfolio_size >= 10:
        score += 20
    elif portfolio_size >= 5:
        score += 10
    else:
        score += portfolio_size * 2

    if total_units >= 500:
        score += 30
    elif total_units >= 200:
        score += 20
    elif total_units >= 100:
        score += 15
    elif total_units >= 50:
        score += 10
    else:
        score += min(total_units / 5.0, 10)

    if portfolio_size > 0:
        violations_per_unit = violation_count / max(total_units, 1)
        score += min(violations_per_unit * 10, 30)

    return round(min(score, 100.0), 2)


def generate_leads(min_portfolio: int = 1) -> dict[str, int]:
    """Materialize leads and live building links into PostgreSQL."""
    engine = create_engine(get_sync_url())
    session = Session(engine)
    try:
        logger.info("Counting source data...")
        building_count = session.execute(text("SELECT COUNT(*) FROM buildings")).scalar()
        contact_count = session.execute(text("SELECT COUNT(*) FROM building_contacts")).scalar()
        logger.info("  %s buildings, %s contacts", f"{building_count:,}", f"{contact_count:,}")

        if building_count == 0:
            logger.error("No buildings found. Run ingestion first.")
            return {"upserted": 0, "building_management_backfilled": 0, "total_leads": 0}

        logger.info("Aggregating buildings by agent/owner...")
        rows = session.execute(text("""
            SELECT
                bc.bbl,
                bc.contact_type,
                bc.corporation_name,
                bc.first_name,
                bc.last_name,
                bc.business_address,
                bc.business_city,
                bc.business_state,
                bc.business_zip,
                b.address,
                b.borough,
                b.unit_count,
                b.building_class,
                b.year_built,
                b.assessed_value
            FROM building_contacts bc
            JOIN buildings b ON bc.bbl = b.bbl
            WHERE bc.contact_type IN ('Agent', 'ManagementCompany', 'Owner', 'CorporateOwner', 'IndividualOwner')
            ORDER BY bc.corporation_name NULLS LAST
        """)).fetchall()

        logger.info("Processing %s contact-building pairs...", f"{len(rows):,}")

        leads_map: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not _is_seed_contact(row):
                continue

            raw_name = _raw_name_from_contact(row)
            if _is_probably_junk_name(raw_name):
                continue

            norm_name = normalize_name_for_grouping(raw_name)
            display_name = _display_name_for_group(raw_name)
            if not norm_name:
                continue

            if norm_name not in leads_map:
                leads_map[norm_name] = {
                    "lead_id": make_lead_id(norm_name),
                    "normalized_name": norm_name,
                    "agent_name": display_name if row.contact_type in {"Agent", "ManagementCompany"} else None,
                    "owner_name": display_name if row.contact_type in OWNER_CONTACT_TYPES else None,
                    "company_name": (row.corporation_name or "").strip() or None,
                    "entity_type": "company" if (row.corporation_name or "").strip() else "individual_agent",
                    "address": row.business_address,
                    "boroughs": {},
                    "bbl_set": set(),
                    "address_set": set(),
                    "unit_counts_by_bbl": {},
                    "unit_count_total": 0,
                    "building_classes": [],
                }

            lead = leads_map[norm_name]
            bbl = row.bbl

            if bbl not in lead["bbl_set"]:
                lead["bbl_set"].add(bbl)
                lead["unit_counts_by_bbl"][bbl] = row.unit_count or 0
                lead["unit_count_total"] = sum(lead["unit_counts_by_bbl"].values())
                if row.address:
                    lead["address_set"].add(row.address)
                if row.borough:
                    lead["boroughs"][row.borough] = lead["boroughs"].get(row.borough, 0) + 1
                if row.building_class:
                    lead["building_classes"].append(row.building_class)

            if row.contact_type in {"Agent", "ManagementCompany"} and not lead.get("agent_name"):
                lead["agent_name"] = display_name
            if row.contact_type in OWNER_CONTACT_TYPES and not lead.get("owner_name"):
                lead["owner_name"] = display_name
            if (row.corporation_name or "").strip() and not lead.get("company_name"):
                lead["company_name"] = (row.corporation_name or "").strip()

        logger.info("Found %s unique PM companies before portfolio filter", f"{len(leads_map):,}")

        filtered = {k: v for k, v in leads_map.items() if len(v["bbl_set"]) >= min_portfolio}
        logger.info("After min_portfolio=%s filter: %s leads", min_portfolio, f"{len(filtered):,}")
        collapsed = _collapse_duplicate_company_leads(filtered)
        logger.info("After conservative company dedupe: %s leads", f"{len(collapsed):,}")

        if not collapsed:
            logger.warning("No leads generated. Check building_contacts data.")
            return {"upserted": 0, "building_management_backfilled": 0, "total_leads": 0}

        inserted = 0
        now = datetime.now(timezone.utc)
        batch_size = 1000

        import json as _json

        leads_list = list(collapsed.values())
        db_batch: list[dict[str, Any]] = []

        for lead in leads_list:
            portfolio_size = len(lead["bbl_set"])
            total_units = lead["unit_count_total"]
            primary_borough = max(lead["boroughs"], key=lead["boroughs"].get) if lead["boroughs"] else None
            score = compute_score(portfolio_size, total_units)

            db_batch.append({
                "lead_id": lead["lead_id"],
                "normalized_name": lead["normalized_name"],
                "agent_name": lead.get("agent_name"),
                "owner_name": lead.get("owner_name"),
                "company_name": lead.get("company_name"),
                "entity_type": lead["entity_type"],
                "address": lead.get("address"),
                "primary_borough": primary_borough,
                "portfolio_size": portfolio_size,
                "total_units": total_units,
                "score": score,
                "score_breakdown": _json.dumps({"components": {}}),
                "buildings": _json.dumps(sorted(lead["address_set"])),
                "boros": _json.dumps(sorted(lead["boroughs"].keys())),
                "enrichment_status": "none",
                "pipeline_stage": "research",
                "outreach_status": "new",
                "priority_rank": 0,
                "created_at": now,
                "updated_at": now,
            })

        insert_sql = text("""
            INSERT INTO leads (
                lead_id, normalized_name, agent_name, owner_name,
                company_name, entity_type, address, primary_borough,
                portfolio_size, total_units, score, score_breakdown, buildings,
                boros, enrichment_status, pipeline_stage,
                outreach_status, priority_rank,
                created_at, updated_at
            ) VALUES (
                :lead_id, :normalized_name, :agent_name, :owner_name,
                :company_name, :entity_type, :address, :primary_borough,
                :portfolio_size, :total_units, :score, CAST(:score_breakdown AS JSONB), CAST(:buildings AS JSONB),
                CAST(:boros AS JSONB), :enrichment_status, :pipeline_stage,
                :outreach_status, :priority_rank,
                :created_at, :updated_at
            ) ON CONFLICT (lead_id) DO UPDATE SET
                normalized_name = EXCLUDED.normalized_name,
                agent_name = EXCLUDED.agent_name,
                owner_name = EXCLUDED.owner_name,
                company_name = EXCLUDED.company_name,
                entity_type = EXCLUDED.entity_type,
                address = EXCLUDED.address,
                portfolio_size = EXCLUDED.portfolio_size,
                total_units = EXCLUDED.total_units,
                score = EXCLUDED.score,
                primary_borough = EXCLUDED.primary_borough,
                buildings = EXCLUDED.buildings,
                boros = EXCLUDED.boros,
                updated_at = EXCLUDED.updated_at
        """)

        for i in range(0, len(db_batch), batch_size):
            chunk = db_batch[i:i + batch_size]
            try:
                session.execute(insert_sql, chunk)
                session.commit()
            except Exception as exc:
                session.rollback()
                logger.error("Batch insert failed at offset %s: %s", i, exc)
                for row in chunk:
                    try:
                        session.execute(insert_sql, row)
                        session.commit()
                    except Exception as row_exc:
                        session.rollback()
                        logger.warning("Skipping lead %s: %s", row.get("lead_id"), row_exc)
            inserted += len(chunk)
            logger.info("  Progress: %s/%s", f"{min(i + batch_size, len(db_batch)):,}", f"{len(db_batch):,}")

        logger.info("Done: %s leads upserted into PostgreSQL", f"{inserted:,}")

        logger.info("Backfilling building_management from generated lead links...")
        bm_count = 0
        try:
            session.execute(text("CREATE TEMP TABLE tmp_lead_links (bbl TEXT, lead_id TEXT, role TEXT) ON COMMIT DROP"))
            link_rows: list[dict[str, str]] = []
            for lead in leads_list:
                role = "agent" if lead.get("agent_name") else "owner"
                for bbl in lead["bbl_set"]:
                    link_rows.append({"bbl": bbl, "lead_id": lead["lead_id"], "role": role})

            if link_rows:
                session.execute(
                    text("INSERT INTO tmp_lead_links (bbl, lead_id, role) VALUES (:bbl, :lead_id, :role)"),
                    link_rows,
                )

            bm_result = session.execute(text("""
                INSERT INTO building_management (bbl, lead_id, role, is_current, created_at, updated_at)
                SELECT DISTINCT ON (t.bbl, t.lead_id)
                    t.bbl, t.lead_id, t.role, true, now(), now()
                FROM tmp_lead_links t
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM building_management bm
                    WHERE bm.bbl = t.bbl
                      AND bm.lead_id = t.lead_id
                      AND bm.is_current = true
                )
            """))
            session.commit()
            bm_count = int(bm_result.rowcount or 0)
            logger.info("  building_management: %s rows backfilled", f"{bm_count:,}")
        except Exception as exc:
            session.rollback()
            logger.error("building_management backfill failed: %s", exc)

        logger.info("Refreshing lead portfolio snapshots from live building links...")
        snapshot_result = session.execute(text("""
            UPDATE leads l
            SET portfolio_size = COALESCE(sub.bldg_count, 0),
                total_units = COALESCE(sub.unit_sum, 0),
                updated_at = NOW()
            FROM (
                SELECT bm.lead_id,
                       COUNT(DISTINCT bm.bbl) AS bldg_count,
                       COALESCE(SUM(b.unit_count), 0) AS unit_sum
                FROM building_management bm
                JOIN buildings b ON b.bbl = bm.bbl
                WHERE bm.is_current = true
                GROUP BY bm.lead_id
            ) sub
            WHERE l.lead_id = sub.lead_id
              AND (
                    COALESCE(l.portfolio_size, 0) <> COALESCE(sub.bldg_count, 0)
                 OR COALESCE(l.total_units, 0) <> COALESCE(sub.unit_sum, 0)
              )
        """))
        session.commit()
        logger.info("  portfolio snapshots updated: %s", int(snapshot_result.rowcount or 0))

        final_count = session.execute(text("SELECT COUNT(*) FROM leads")).scalar()
        logger.info("Total leads in DB: %s", f"{int(final_count or 0):,}")
        return {
            "upserted": inserted,
            "building_management_backfilled": bm_count,
            "total_leads": int(final_count or 0),
        }
    finally:
        session.close()
        engine.dispose()

