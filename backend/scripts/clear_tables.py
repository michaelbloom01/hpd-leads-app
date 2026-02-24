"""Delete rows from tables to prepare for fresh data load."""
import os, sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

from sqlalchemy import create_engine, text

url = os.environ.get("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/hpd_leads")
engine = create_engine(url, connect_args={"connect_timeout": 30})

tables = ["outreach_events", "outreach_attempts", "lead_user_data",
          "hpd_violations", "hpd_complaints", "hpd_litigation",
          "building_contacts", "building_score_history", "building_management",
          "leads", "buildings", "ingestion_jobs", "data_quality_log"]

with engine.connect() as conn:
    for t in tables:
        try:
            count = conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
            if count > 0:
                conn.execute(text(f"DELETE FROM {t}"))
                conn.commit()
                logger.info(f"Deleted {count:,} rows from {t}")
            else:
                logger.info(f"{t}: already empty")
        except Exception as e:
            conn.rollback()
            logger.warning(f"Could not clear {t}: {e}")
logger.info("Done")
