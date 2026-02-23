"""Sync leads table from local to Railway PostgreSQL."""
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

BATCH = 2000

UPSERT_SQL = text("""
    INSERT INTO leads (lead_id, normalized_name, agent_name, owner_name,
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
        boros = EXCLUDED.boros,
        updated_at = EXCLUDED.updated_at
""")


def main():
    local_engine = create_engine(LOCAL_URL)
    remote_engine = create_engine(REMOTE_URL, pool_pre_ping=True,
                                  connect_args={"connect_timeout": 60})

    with local_engine.connect() as local_conn, remote_engine.connect() as remote_conn:
        local_count = local_conn.execute(text("SELECT COUNT(*) FROM leads")).scalar()
        remote_count = remote_conn.execute(text("SELECT COUNT(*) FROM leads")).scalar()
        logger.info(f"leads: local={local_count:,}, remote={remote_count:,}")

        if local_count == 0:
            logger.error("No leads in local DB. Run generate_leads_from_buildings.py first.")
            return

        offset = 0
        total = 0
        start = time.time()

        while offset < local_count:
            rows = local_conn.execute(text(
                f"""SELECT lead_id, normalized_name, agent_name, owner_name,
                          company_name, entity_type, address, primary_borough,
                          portfolio_size, total_units, score,
                          score_breakdown::text AS score_breakdown_text,
                          boros::text AS boros_text,
                          enrichment_status, pipeline_stage, outreach_status,
                          priority_rank, created_at, updated_at
                   FROM leads ORDER BY lead_id LIMIT {BATCH} OFFSET {offset}"""
            )).fetchall()
            if not rows:
                break

            params = [{
                "lead_id": r.lead_id,
                "normalized_name": r.normalized_name,
                "agent_name": r.agent_name,
                "owner_name": r.owner_name,
                "company_name": r.company_name,
                "entity_type": r.entity_type or "unknown",
                "address": r.address,
                "primary_borough": r.primary_borough,
                "portfolio_size": r.portfolio_size or 0,
                "total_units": r.total_units or 0,
                "score": r.score or 0.0,
                "score_breakdown": r.score_breakdown_text or '{}',
                "boros": r.boros_text or '[]',
                "enrichment_status": (r.enrichment_status or "none")[:20],
                "pipeline_stage": (r.pipeline_stage or "new")[:30],
                "outreach_status": (r.outreach_status or "none")[:20],
                "priority_rank": r.priority_rank or 0,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
            } for r in rows]

            try:
                remote_conn.execute(UPSERT_SQL, params)
                remote_conn.commit()
                total += len(rows)
                elapsed = time.time() - start
                rate = total / elapsed
                logger.info(f"  {total:,}/{local_count:,} leads synced ({rate:.0f}/s)")
            except Exception as e:
                remote_conn.rollback()
                logger.error(f"Batch failed at offset {offset}: {e}")
                # Try one-by-one fallback
                for p in params:
                    try:
                        remote_conn.execute(UPSERT_SQL, p)
                        remote_conn.commit()
                        total += 1
                    except Exception as e2:
                        remote_conn.rollback()
                        logger.warning(f"Skipping lead {p.get('lead_id')}: {e2}")

            offset += BATCH

    final_count_remote = remote_engine.connect().execute(text("SELECT COUNT(*) FROM leads")).scalar()
    logger.info(f"Done. Railway leads: {final_count_remote:,}")


if __name__ == "__main__":
    main()
