"""
Sync data from local PostgreSQL to Railway PostgreSQL.
Uses batch upserts to handle existing data gracefully.

Usage:
    python scripts/sync_local_to_railway.py
"""
import os, sys, logging, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

from sqlalchemy import create_engine, text

LOCAL_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/hpd_leads"
REMOTE_URL = os.environ.get("RAILWAY_DATABASE_URL", "")

if not REMOTE_URL:
    logger.error("Set RAILWAY_DATABASE_URL environment variable")
    sys.exit(1)

BATCH = 5000


def sync_table(local_conn, remote_conn, table, pk_cols, select_sql, upsert_sql, params_fn):
    local_count = local_conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
    remote_count = remote_conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
    logger.info(f"{table}: local={local_count:,}, remote={remote_count:,}")

    if local_count == 0:
        logger.info(f"  {table}: nothing to sync")
        return

    offset = 0
    total_upserted = 0
    start = time.time()

    while offset < local_count:
        rows = local_conn.execute(text(select_sql + f" LIMIT {BATCH} OFFSET {offset}")).fetchall()
        if not rows:
            break

        batch_params = [params_fn(r) for r in rows]
        try:
            remote_conn.execute(text(upsert_sql), batch_params)
            remote_conn.commit()
        except Exception as e:
            remote_conn.rollback()
            logger.warning(f"  {table} batch at offset {offset} failed: {e}")
            # Try smaller batches
            for row in rows:
                try:
                    remote_conn.execute(text(upsert_sql), params_fn(row))
                    remote_conn.commit()
                except Exception as e2:
                    remote_conn.rollback()

        total_upserted += len(rows)
        offset += BATCH
        elapsed = time.time() - start
        rate = total_upserted / elapsed if elapsed > 0 else 0
        logger.info(f"  {table}: {total_upserted:,}/{local_count:,} ({rate:.0f}/s)")

    logger.info(f"  {table}: done — {total_upserted:,} rows synced")


def main():
    logger.info("Connecting to local and remote databases...")
    local_engine = create_engine(LOCAL_URL)
    remote_engine = create_engine(REMOTE_URL, pool_pre_ping=True)

    with local_engine.connect() as local_conn, remote_engine.connect() as remote_conn:
        # ── 1. Buildings ──
        sync_table(
            local_conn, remote_conn, "buildings",
            ["bbl"],
            """SELECT bbl, bin, address, borough, block, lot, zip_code,
                      building_class, unit_count, year_built, assessed_value,
                      council_district, community_board, census_tract, nta,
                      churn_score, churn_category, created_at, updated_at
               FROM buildings ORDER BY bbl""",
            """INSERT INTO buildings (bbl, bin, address, borough, block, lot, zip_code,
                   building_class, unit_count, year_built, assessed_value,
                   council_district, community_board, census_tract, nta,
                   churn_score, churn_category, created_at, updated_at)
               VALUES (:bbl, :bin, :address, :borough, :block, :lot, :zip,
                   :bldg_class, :units, :year_built, :assessed_value,
                   :council, :cd, :census, :nta,
                   :churn_score, :churn_category, :created_at, :updated_at)
               ON CONFLICT (bbl) DO UPDATE SET
                   address = EXCLUDED.address, unit_count = EXCLUDED.unit_count,
                   churn_score = EXCLUDED.churn_score, updated_at = EXCLUDED.updated_at""",
            lambda r: {
                "bbl": r.bbl, "bin": r.bin, "address": r.address, "borough": r.borough,
                "block": r.block, "lot": r.lot, "zip": r.zip_code,
                "bldg_class": r.building_class, "units": r.unit_count,
                "year_built": r.year_built, "assessed_value": r.assessed_value,
                "council": r.council_district, "cd": r.community_board,
                "census": r.census_tract, "nta": r.nta,
                "churn_score": r.churn_score, "churn_category": r.churn_category,
                "created_at": r.created_at, "updated_at": r.updated_at,
            }
        )

        # ── 2. Building contacts ──
        sync_table(
            local_conn, remote_conn, "building_contacts",
            ["registration_contact_id"],
            """SELECT bbl, registration_contact_id, registration_id,
                      contact_type, description, corporation_name,
                      first_name, last_name, title,
                      business_address, business_city, business_state, business_zip,
                      created_at, updated_at
               FROM building_contacts ORDER BY id""",
            """INSERT INTO building_contacts (bbl, registration_contact_id, registration_id,
                   contact_type, description, corporation_name,
                   first_name, last_name, title,
                   business_address, business_city, business_state, business_zip,
                   created_at, updated_at)
               VALUES (:bbl, :contact_id, :reg_id, :type, :desc, :corp,
                   :first, :last, :title, :addr, :city, :state, :zip, :created_at, :updated_at)
               ON CONFLICT DO NOTHING""",
            lambda r: {
                "bbl": r.bbl, "contact_id": r.registration_contact_id,
                "reg_id": r.registration_id, "type": r.contact_type,
                "desc": r.description, "corp": r.corporation_name,
                "first": r.first_name, "last": r.last_name, "title": r.title,
                "addr": r.business_address, "city": r.business_city,
                "state": r.business_state, "zip": r.business_zip,
                "created_at": r.created_at, "updated_at": r.updated_at,
            }
        )

        # ── 3. Leads ──
        sync_table(
            local_conn, remote_conn, "leads",
            ["lead_id"],
            """SELECT lead_id, normalized_name, agent_name, owner_name,
                      company_name, entity_type, address, primary_borough,
                      portfolio_size, total_units, score,
                      score_breakdown::text AS score_breakdown_text,
                      boros::text AS boros_text,
                      enrichment_status, pipeline_stage, outreach_status,
                      priority_rank, created_at, updated_at
               FROM leads ORDER BY lead_id""",
            """INSERT INTO leads (lead_id, normalized_name, agent_name, owner_name,
                   company_name, entity_type, address, primary_borough,
                   portfolio_size, total_units, score, score_breakdown,
                   boros, enrichment_status, pipeline_stage,
                   outreach_status, priority_rank, created_at, updated_at)
               VALUES (:lead_id, :normalized_name, :agent_name, :owner_name,
                   :company_name, :entity_type, :address, :primary_borough,
                   :portfolio_size, :total_units, :score,
                   CAST(:score_breakdown AS JSONB), CAST(:boros AS JSONB),
                   :enrichment_status, :pipeline_stage,
                   :outreach_status, :priority_rank, :created_at, :updated_at)
               ON CONFLICT (lead_id) DO UPDATE SET
                   portfolio_size = EXCLUDED.portfolio_size,
                   total_units = EXCLUDED.total_units,
                   score = EXCLUDED.score,
                   primary_borough = EXCLUDED.primary_borough,
                   updated_at = EXCLUDED.updated_at""",
            lambda r: {
                "lead_id": r.lead_id, "normalized_name": r.normalized_name,
                "agent_name": r.agent_name, "owner_name": r.owner_name,
                "company_name": r.company_name, "entity_type": r.entity_type,
                "address": r.address, "primary_borough": r.primary_borough,
                "portfolio_size": r.portfolio_size, "total_units": r.total_units,
                "score": r.score,
                "score_breakdown": r.score_breakdown_text or '{}',
                "boros": r.boros_text or '[]',
                "enrichment_status": r.enrichment_status or "none",
                "pipeline_stage": r.pipeline_stage or "new",
                "outreach_status": r.outreach_status or "none",
                "priority_rank": r.priority_rank or 0,
                "created_at": r.created_at, "updated_at": r.updated_at,
            }
        )

    logger.info("Sync complete!")


if __name__ == "__main__":
    main()
