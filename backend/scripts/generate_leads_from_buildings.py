"""
Generate leads from buildings + building_contacts tables.

A "lead" is a property management company (agent/owner) aggregated across
all their buildings. This script runs after buildings ingestion completes.

Usage:
    python scripts/generate_leads_from_buildings.py [--min-portfolio N]
"""
import os
import sys
import logging
import argparse
import hashlib
import re
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

if not os.environ.get("DATABASE_URL"):
    os.environ["DATABASE_URL"] = "postgresql+psycopg://postgres:postgres@localhost:5432/hpd_leads"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from src.db.session import get_sync_url
from src.transform.normalize import normalize_name, normalize_name_for_grouping


def make_lead_id(name: str) -> str:
    """Stable lead ID from normalized name. Max 12 chars to match DB schema."""
    normalized = name.strip().upper()
    return hashlib.md5(normalized.encode()).hexdigest()[:12]


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


def _raw_name_from_contact(row) -> str:
    corp = (row.corporation_name or "").strip()
    if corp:
        return corp
    first = (row.first_name or "").strip()
    last = (row.last_name or "").strip()
    return " ".join(part for part in [first, last] if part).strip()


def _is_seed_contact(row) -> bool:
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


def _merge_lead_records(target: dict, incoming: dict) -> None:
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


def _collapse_duplicate_company_leads(leads_map: dict[str, dict]) -> dict[str, dict]:
    collapsed: dict[str, dict] = {}
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
    """Simple heuristic score — will be replaced by full scoring run."""
    score = 0.0
    # Portfolio size signal (0-40 pts)
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

    # Units signal (0-30 pts)
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

    # Violation signal (churn indicator, 0-30 pts)
    if portfolio_size > 0:
        violations_per_unit = violation_count / max(total_units, 1)
        score += min(violations_per_unit * 10, 30)

    return round(min(score, 100.0), 2)


def main(min_portfolio: int = 1):
    engine = create_engine(get_sync_url())
    session = Session(engine)

    logger.info("Counting source data...")
    building_count = session.execute(text("SELECT COUNT(*) FROM buildings")).scalar()
    contact_count = session.execute(text("SELECT COUNT(*) FROM building_contacts")).scalar()
    logger.info(f"  {building_count:,} buildings, {contact_count:,} contacts")

    if building_count == 0:
        logger.error("No buildings found. Run ingestion first: python scripts/run_ingestion.py --tasks buildings")
        return

    logger.info("Aggregating buildings by agent/owner...")
    # Join buildings with their Agent contacts, group by agent
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

    logger.info(f"Processing {len(rows):,} contact-building pairs...")

    # Aggregate by normalized name
    leads_map = {}  # normalized_name -> lead dict
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

        # Prefer Agent name over other contact types
        if row.contact_type in {"Agent", "ManagementCompany"} and not lead.get("agent_name"):
            lead["agent_name"] = display_name
        if row.contact_type in OWNER_CONTACT_TYPES and not lead.get("owner_name"):
            lead["owner_name"] = display_name
        if (row.corporation_name or "").strip() and not lead.get("company_name"):
            lead["company_name"] = (row.corporation_name or "").strip()

    logger.info(f"Found {len(leads_map):,} unique PM companies before portfolio filter")

    # Filter by minimum portfolio size
    filtered = {k: v for k, v in leads_map.items() if len(v["bbl_set"]) >= min_portfolio}
    logger.info(f"After min_portfolio={min_portfolio} filter: {len(filtered):,} leads")
    collapsed = _collapse_duplicate_company_leads(filtered)
    logger.info(f"After conservative company dedupe: {len(collapsed):,} leads")

    if not collapsed:
        logger.warning("No leads generated. Check building_contacts data.")
        return

    # Insert leads
    inserted = 0
    now = datetime.utcnow()
    batch_size = 1000

    import json as _json

    leads_list = list(collapsed.values())
    db_batch = []

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

    INSERT_SQL = text("""
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
            session.execute(INSERT_SQL, chunk)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Batch insert failed at offset {i}: {e}")
            # Try row by row as fallback
            for row in chunk:
                try:
                    session.execute(INSERT_SQL, row)
                    session.commit()
                except Exception as e2:
                    session.rollback()
                    logger.warning(f"Skipping lead {row.get('lead_id')}: {e2}")
        inserted += len(chunk)
        logger.info(f"  Progress: {min(i + batch_size, len(db_batch)):,}/{len(db_batch):,}")

    logger.info(f"Done: {inserted:,} leads upserted into PostgreSQL")

    # Backfill building_management table
    logger.info("Backfilling building_management from generated lead links...")
    try:
        session.execute(text("CREATE TEMP TABLE tmp_lead_links (bbl TEXT, lead_id TEXT, role TEXT) ON COMMIT DROP"))
        link_rows = []
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
        bm_count = bm_result.rowcount
        logger.info(f"  building_management: {bm_count:,} rows backfilled")
    except Exception as e:
        session.rollback()
        logger.error(f"building_management backfill failed: {e}")

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

    # Final count
    final_count = session.execute(text("SELECT COUNT(*) FROM leads")).scalar()
    logger.info(f"Total leads in DB: {final_count:,}")
    session.close()
    return {
        "upserted": inserted,
        "building_management_backfilled": bm_count if "bm_count" in locals() else 0,
        "total_leads": int(final_count or 0),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-portfolio", type=int, default=1,
                        help="Minimum number of buildings for a lead to be included")
    args = parser.parse_args()
    main(min_portfolio=args.min_portfolio)
