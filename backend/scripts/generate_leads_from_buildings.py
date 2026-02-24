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


def make_lead_id(name: str) -> str:
    """Stable lead ID from normalized name. Max 12 chars to match DB schema."""
    normalized = name.strip().upper()
    return hashlib.md5(normalized.encode()).hexdigest()[:12]


def normalize_name(name: str) -> str:
    if not name:
        return ""
    return " ".join(name.strip().upper().split())


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
            b.borough,
            b.unit_count,
            b.building_class,
            b.year_built,
            b.assessed_value
        FROM building_contacts bc
        JOIN buildings b ON bc.bbl = b.bbl
        WHERE bc.contact_type IN ('Agent', 'Owner', 'CorporateOwner', 'IndividualOwner',
                                   'HeadOfficer', 'Officer', 'Shareholder')
        ORDER BY bc.corporation_name NULLS LAST
    """)).fetchall()

    logger.info(f"Processing {len(rows):,} contact-building pairs...")

    # Aggregate by normalized name
    leads_map = {}  # normalized_name -> lead dict
    for row in rows:
        corp = row.corporation_name
        first = row.first_name or ""
        last = row.last_name or ""

        if corp and corp.strip():
            raw_name = corp.strip()
        elif first or last:
            raw_name = f"{first} {last}".strip()
        else:
            continue

        norm_name = normalize_name(raw_name)
        if not norm_name or norm_name in ("", "N/A", "NONE", "-", "--"):
            continue
        if len(norm_name) < 2:
            continue

        if norm_name not in leads_map:
            leads_map[norm_name] = {
                "lead_id": make_lead_id(norm_name),
                "normalized_name": norm_name,
                "agent_name": raw_name if row.contact_type in ("Agent",) else None,
                "owner_name": raw_name if row.contact_type in ("Owner", "IndividualOwner", "CorporateOwner") else None,
                "company_name": corp.strip() if corp else None,
                "entity_type": "corporation" if corp else "individual",
                "address": row.business_address,
                "boroughs": {},
                "bbl_set": set(),
                "unit_count_total": 0,
                "building_classes": [],
            }

        lead = leads_map[norm_name]
        bbl = row.bbl

        if bbl not in lead["bbl_set"]:
            lead["bbl_set"].add(bbl)
            lead["unit_count_total"] += row.unit_count or 0
            if row.borough:
                lead["boroughs"][row.borough] = lead["boroughs"].get(row.borough, 0) + 1
            if row.building_class:
                lead["building_classes"].append(row.building_class)

        # Prefer Agent name over other contact types
        if row.contact_type == "Agent" and not lead.get("agent_name"):
            lead["agent_name"] = raw_name

    logger.info(f"Found {len(leads_map):,} unique PM companies before portfolio filter")

    # Filter by minimum portfolio size
    filtered = {k: v for k, v in leads_map.items() if len(v["bbl_set"]) >= min_portfolio}
    logger.info(f"After min_portfolio={min_portfolio} filter: {len(filtered):,} leads")

    if not filtered:
        logger.warning("No leads generated. Check building_contacts data.")
        return

    # Insert leads
    inserted = updated = 0
    now = datetime.utcnow()
    batch_size = 1000

    import json as _json

    leads_list = list(filtered.values())
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
            "boros": _json.dumps([primary_borough] if primary_borough else []),
            "enrichment_status": "none",
            "pipeline_stage": "new",
            "outreach_status": "none",
            "priority_rank": 0,
            "created_at": now,
            "updated_at": now,
        })

    INSERT_SQL = text("""
        INSERT INTO leads (
            lead_id, normalized_name, agent_name, owner_name,
            company_name, entity_type, address, primary_borough,
            portfolio_size, total_units, score, score_breakdown,
            boros, enrichment_status, pipeline_stage,
            outreach_status, priority_rank,
            created_at, updated_at
        ) VALUES (
            :lead_id, :normalized_name, :agent_name, :owner_name,
            :company_name, :entity_type, :address, :primary_borough,
            :portfolio_size, :total_units, :score, CAST(:score_breakdown AS JSONB),
            CAST(:boros AS JSONB), :enrichment_status, :pipeline_stage,
            :outreach_status, :priority_rank,
            :created_at, :updated_at
        ) ON CONFLICT (lead_id) DO UPDATE SET
            portfolio_size = EXCLUDED.portfolio_size,
            total_units = EXCLUDED.total_units,
            score = EXCLUDED.score,
            primary_borough = EXCLUDED.primary_borough,
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

    # Final count
    final_count = session.execute(text("SELECT COUNT(*) FROM leads")).scalar()
    logger.info(f"Total leads in DB: {final_count:,}")
    session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-portfolio", type=int, default=1,
                        help="Minimum number of buildings for a lead to be included")
    args = parser.parse_args()
    main(min_portfolio=args.min_portfolio)
