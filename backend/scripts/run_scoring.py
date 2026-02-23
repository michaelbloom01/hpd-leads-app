"""Standalone churn scoring runner - calls scoring logic directly without Celery."""
import os, sys, json, logging, time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

if not os.environ.get("DATABASE_URL"):
    logger.warning("DATABASE_URL not set; defaulting to localhost for development")
    os.environ["DATABASE_URL"] = "postgresql+psycopg://postgres:postgres@localhost:5432/hpd_leads"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from src.db.session import get_sync_url
from src.tasks.score import SIGNAL_VIEWS_SQL, SUMMARY_VIEW_SQL, SIGNAL_NAMES, _compute_raw_signal


def main():
    engine = create_engine(get_sync_url())
    session = Session(engine)

    config_row = session.execute(
        text("SELECT id, weights FROM scoring_configs WHERE is_active = true LIMIT 1")
    ).first()
    if not config_row:
        logger.error("No active scoring config!")
        return

    cfg_id = config_row[0]
    weights = config_row[1] if isinstance(config_row[1], dict) else {}
    logger.info(f"Using scoring config {cfg_id}: {weights}")

    logger.info("Creating signal views...")
    for stmt in SIGNAL_VIEWS_SQL.split(";"):
        stmt = stmt.strip()
        if stmt:
            session.execute(text(stmt))
    session.commit()

    logger.info("Creating materialized view...")
    session.execute(text("DROP MATERIALIZED VIEW IF EXISTS building_signal_summary"))
    session.execute(text(SUMMARY_VIEW_SQL))
    session.commit()

    rows = session.execute(text("SELECT * FROM building_signal_summary")).fetchall()
    columns = list(session.execute(text("SELECT * FROM building_signal_summary LIMIT 0")).keys())
    logger.info(f"Scoring {len(rows)} buildings...")

    scored = 0
    now = datetime.utcnow()
    start = time.time()

    for row in rows:
        row_dict = dict(zip(columns, row))
        bbl = row_dict["bbl"]

        raw_scores = {}
        available = 0
        for name in SIGNAL_NAMES:
            val = _compute_raw_signal(name, row_dict)
            raw_scores[name] = val
            if val is not None:
                available += 1

        null_weight_pool = sum(weights.get(n, 0) for n in SIGNAL_NAMES if raw_scores[n] is None)
        non_null_total = sum(weights.get(n, 0) for n in SIGNAL_NAMES if raw_scores[n] is not None)

        weighted_sum = 0.0
        breakdown = {}
        for name in SIGNAL_NAMES:
            raw = raw_scores[name]
            base_weight = weights.get(name, 0)
            if raw is None:
                breakdown[name] = {"raw": None, "weight": base_weight, "effective_weight": 0, "contribution": 0}
                continue
            effective_weight = base_weight
            if non_null_total > 0 and null_weight_pool > 0:
                effective_weight = base_weight + (null_weight_pool * base_weight / non_null_total)
            contribution = raw * effective_weight / 100.0
            weighted_sum += contribution
            breakdown[name] = {
                "raw": round(raw, 1),
                "weight": base_weight,
                "effective_weight": round(effective_weight, 1),
                "contribution": round(contribution, 1),
            }

        churn_score = min(100.0, max(0.0, weighted_sum))
        category = "hot" if churn_score >= 70 else "warm" if churn_score >= 40 else "stable"

        key_signal = max(
            ((n, d["contribution"]) for n, d in breakdown.items() if d["contribution"] > 0),
            key=lambda x: x[1],
            default=("none", 0),
        )[0]

        session.execute(
            text("""UPDATE buildings SET
                    churn_score=:score, churn_category=:category,
                    churn_breakdown=:breakdown, key_signal=:key,
                    scoring_config_id=:cfg_id, signals_available=:available,
                    coverage_ratio=:coverage, last_scored_at=:now, updated_at=:now
                WHERE bbl=:bbl"""),
            {"score": round(churn_score, 1), "category": category,
             "breakdown": json.dumps(breakdown), "key": key_signal,
             "cfg_id": cfg_id, "available": available,
             "coverage": round(available / len(SIGNAL_NAMES), 2),
             "now": now, "bbl": bbl},
        )

        session.execute(
            text("""INSERT INTO building_score_history (
                    bbl, churn_score, churn_category, churn_breakdown,
                    scoring_config_id, signal_snapshot, scored_at, created_at, updated_at)
                VALUES (:bbl, :score, :category, :breakdown, :cfg_id, :snapshot, :now, :now, :now)"""),
            {"bbl": bbl, "score": round(churn_score, 1), "category": category,
             "breakdown": json.dumps(breakdown), "cfg_id": cfg_id,
             "snapshot": json.dumps({k: str(v) for k, v in row_dict.items() if k != "bbl"}),
             "now": now},
        )
        scored += 1
        if scored % 5000 == 0:
            session.commit()
            elapsed = time.time() - start
            logger.info(f"  Scored {scored} buildings ({elapsed:.0f}s)")

    session.commit()
    elapsed = time.time() - start
    logger.info(f"Scoring complete: {scored} buildings in {elapsed:.1f}s")

    stats = session.execute(text("""
        SELECT churn_category, count(*) FROM buildings
        WHERE churn_score IS NOT NULL GROUP BY churn_category ORDER BY 1
    """)).fetchall()
    for cat, cnt in stats:
        logger.info(f"  {cat}: {cnt}")

    session.close()


if __name__ == "__main__":
    main()
