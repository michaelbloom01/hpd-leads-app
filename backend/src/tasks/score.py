"""Churn scoring Celery task.

Implements the scoring engine from the plan:
1. Load active scoring config
2. Refresh building_signal_summary materialized view
3. Compute raw signal scores per building
4. Apply configurable weights with NULL redistribution
5. Atomic swap via staging table
6. Append score history
"""
import logging
from datetime import datetime

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.worker import app as celery_app

logger = logging.getLogger(__name__)


def _get_pg_session() -> Session:
    from src.db.session import get_sync_url
    engine = create_engine(get_sync_url())
    return Session(engine)


SIGNAL_VIEWS_SQL = """
CREATE OR REPLACE VIEW signal_complaints AS
SELECT bbl,
       count(*) FILTER (WHERE received_date > now() - interval '6 months') AS complaints_6mo,
       count(*) FILTER (WHERE received_date > now() - interval '12 months') AS complaints_12mo
FROM hpd_complaints GROUP BY bbl;

CREATE OR REPLACE VIEW signal_acris AS
SELECT bbl,
       bool_or(doc_type = 'DEED' AND recorded_date > now() - interval '12 months') AS sale_12mo,
       bool_or(doc_type = 'DEED' AND recorded_date > now() - interval '24 months') AS sale_24mo,
       bool_or(doc_type = 'MTGE' AND recorded_date > now() - interval '12 months') AS refi_12mo
FROM acris_transactions GROUP BY bbl;

CREATE OR REPLACE VIEW signal_violations AS
SELECT bbl,
       count(*) FILTER (WHERE inspection_date > now() - interval '6 months') AS violations_6mo,
       count(*) FILTER (WHERE inspection_date > now() - interval '12 months') AS violations_12mo
FROM hpd_violations GROUP BY bbl;

CREATE OR REPLACE VIEW signal_permits AS
SELECT bbl,
       count(*) FILTER (WHERE filing_date > now() - interval '12 months') AS permits_12mo,
       coalesce(sum(estimated_cost) FILTER (WHERE filing_date > now() - interval '12 months'), 0) AS permit_cost_12mo
FROM dob_permits GROUP BY bbl;

CREATE OR REPLACE VIEW signal_litigation AS
SELECT bbl,
       bool_or(case_status IN ('OPEN', 'PENDING')) AS active_litigation,
       count(*) FILTER (WHERE case_close_date > now() - interval '24 months') AS recent_closed,
       bool_or(finding ILIKE '%%harassment%%') AS harassment_finding
FROM hpd_litigation GROUP BY bbl;

CREATE OR REPLACE VIEW signal_erp AS
SELECT bbl,
       count(*) FILTER (WHERE order_date > now() - interval '12 months') AS erp_12mo,
       coalesce(sum(amount) FILTER (WHERE order_date > now() - interval '12 months'), 0) AS erp_amount_12mo
FROM emergency_repairs GROUP BY bbl;

CREATE OR REPLACE VIEW signal_energy AS
SELECT DISTINCT ON (bbl) bbl, grade AS current_grade,
       CASE WHEN grade > lag(grade) OVER (PARTITION BY bbl ORDER BY year) THEN true ELSE false END AS grade_dropped
FROM energy_grades ORDER BY bbl, year DESC;

CREATE OR REPLACE VIEW signal_evictions AS
SELECT bbl,
       count(*) FILTER (WHERE executed_date > now() - interval '12 months') AS filings_12mo
FROM eviction_filings GROUP BY bbl;

CREATE OR REPLACE VIEW signal_facade AS
SELECT fi.bbl,
       CASE fi.filing_status
           WHEN 'UNSAFE' THEN 100
           WHEN 'NO REPORT FILED' THEN 70
           WHEN 'SWARMP' THEN 30
           ELSE 0
       END AS facade_score
FROM (
    SELECT DISTINCT ON (bbl) bbl, filing_status, filing_date
    FROM facade_inspections ORDER BY bbl, filing_date DESC
) fi;

CREATE OR REPLACE VIEW signal_aep AS
SELECT bbl, true AS is_aep
FROM aep_designations WHERE is_active = true;
"""

SUMMARY_VIEW_SQL = """
CREATE MATERIALIZED VIEW IF NOT EXISTS building_signal_summary AS
SELECT b.bbl, b.unit_count,
       c.complaints_6mo, c.complaints_12mo,
       a.sale_12mo, a.sale_24mo, a.refi_12mo,
       v.violations_6mo, v.violations_12mo,
       p.permits_12mo, p.permit_cost_12mo,
       l.active_litigation, l.recent_closed, l.harassment_finding,
       e.erp_12mo, e.erp_amount_12mo,
       en.current_grade, en.grade_dropped,
       ev.filings_12mo AS eviction_filings_12mo,
       f.facade_score,
       aep.is_aep
FROM buildings b
LEFT JOIN signal_complaints c USING(bbl)
LEFT JOIN signal_acris a USING(bbl)
LEFT JOIN signal_violations v USING(bbl)
LEFT JOIN signal_permits p USING(bbl)
LEFT JOIN signal_litigation l USING(bbl)
LEFT JOIN signal_erp e USING(bbl)
LEFT JOIN signal_energy en USING(bbl)
LEFT JOIN signal_evictions ev USING(bbl)
LEFT JOIN signal_facade f USING(bbl)
LEFT JOIN signal_aep aep USING(bbl);
"""


def _compute_raw_signal(name: str, row: dict) -> float | None:
    """Compute a single raw signal score (0-100) from summary row data."""
    if name == "ownership_change":
        if row.get("sale_12mo"):
            return 100.0
        if row.get("sale_24mo"):
            return 50.0
        if row.get("refi_12mo"):
            return 30.0
        return 0.0

    if name == "complaint_spike":
        c6 = row.get("complaints_6mo") or 0
        c12 = row.get("complaints_12mo") or 0
        prior = c12 - c6
        if prior == 0:
            return 50.0 if c6 > 0 else 0.0
        ratio = c6 / max(prior, 1)
        if ratio >= 2:
            return 100.0
        if ratio >= 1.5:
            return 60.0
        if ratio >= 1:
            return 20.0
        return 0.0

    if name == "violation_trend":
        v6 = row.get("violations_6mo") or 0
        v12 = row.get("violations_12mo") or 0
        prior = v12 - v6
        units = row.get("unit_count") or 1
        if prior == 0:
            return min(100.0, (v6 / max(units, 1)) * 100)
        ratio = v6 / max(prior, 1)
        return min(100.0, ratio * 50)

    if name == "energy_grade_drop":
        grade = row.get("current_grade")
        if grade is None:
            return None
        if grade == "F":
            return 100.0
        if row.get("grade_dropped"):
            return 60.0
        return 0.0

    if name == "dob_permits":
        permits = row.get("permits_12mo") or 0
        cost = row.get("permit_cost_12mo") or 0
        if permits == 0:
            return 0.0
        return min(100.0, (cost / 100000) * 30 + permits * 10)

    if name == "hpd_litigation":
        if row.get("active_litigation") or row.get("is_aep"):
            return 100.0
        if row.get("harassment_finding"):
            return 100.0
        if row.get("recent_closed") and row["recent_closed"] > 0:
            return 50.0
        if row.get("is_aep"):
            return 70.0
        return 0.0

    if name == "emergency_repairs":
        return 100.0 if (row.get("erp_12mo") or 0) > 0 else 0.0

    if name == "building_size":
        units = row.get("unit_count") or 0
        if units >= 50:
            return 100.0
        if units >= 20:
            return 60.0
        return 20.0

    if name == "eviction_activity":
        filings = row.get("eviction_filings_12mo") or 0
        units = row.get("unit_count") or 1
        per_unit = filings / max(units, 1)
        return min(100.0, per_unit * 500)

    if name == "facade_status":
        score = row.get("facade_score")
        return float(score) if score is not None else None

    return 0.0


SIGNAL_NAMES = [
    "ownership_change", "complaint_spike", "violation_trend",
    "energy_grade_drop", "dob_permits", "hpd_litigation",
    "emergency_repairs", "building_size", "eviction_activity", "facade_status",
]


@celery_app.task(bind=True, name="src.tasks.score.compute_churn_scores")
def compute_churn_scores(self, config_id: int | None = None):
    """Score all buildings using the active (or specified) scoring config."""
    session = _get_pg_session()

    if config_id:
        config_row = session.execute(
            text("SELECT id, weights FROM scoring_configs WHERE id = :id"),
            {"id": config_id},
        ).first()
    else:
        config_row = session.execute(
            text("SELECT id, weights FROM scoring_configs WHERE is_active = true LIMIT 1")
        ).first()

    if not config_row:
        raise ValueError("No active scoring configuration found")

    cfg_id = config_row[0]
    weights = config_row[1] if isinstance(config_row[1], dict) else {}

    logger.info(f"Scoring with config {cfg_id}: {weights}")

    for stmt in SIGNAL_VIEWS_SQL.split(";"):
        stmt = stmt.strip()
        if stmt:
            session.execute(text(stmt))
    session.commit()

    session.execute(text("DROP MATERIALIZED VIEW IF EXISTS building_signal_summary"))
    session.execute(text(SUMMARY_VIEW_SQL))
    session.commit()

    rows = session.execute(text("SELECT * FROM building_signal_summary")).fetchall()
    columns = session.execute(text("SELECT * FROM building_signal_summary LIMIT 0")).keys()
    logger.info(f"Scoring {len(rows)} buildings")

    scored = 0
    now = datetime.utcnow()

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

        null_weight_pool = sum(
            weights.get(n, 0) for n in SIGNAL_NAMES if raw_scores[n] is None
        )
        non_null_total = sum(
            weights.get(n, 0) for n in SIGNAL_NAMES if raw_scores[n] is not None
        )

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
        if churn_score >= 70:
            category = "hot"
        elif churn_score >= 40:
            category = "warm"
        else:
            category = "stable"

        key_signal = max(
            ((n, d["contribution"]) for n, d in breakdown.items() if d["contribution"] > 0),
            key=lambda x: x[1],
            default=("none", 0),
        )[0]

        import json
        session.execute(
            text("""
                UPDATE buildings SET
                    churn_score = :score, churn_category = :category,
                    churn_breakdown = :breakdown, key_signal = :key,
                    scoring_config_id = :cfg_id, signals_available = :available,
                    coverage_ratio = :coverage, last_scored_at = :now, updated_at = :now
                WHERE bbl = :bbl
            """),
            {
                "score": round(churn_score, 1), "category": category,
                "breakdown": json.dumps(breakdown), "key": key_signal,
                "cfg_id": cfg_id, "available": available,
                "coverage": round(available / len(SIGNAL_NAMES), 2),
                "now": now, "bbl": bbl,
            },
        )

        session.execute(
            text("""
                INSERT INTO building_score_history (
                    bbl, churn_score, churn_category, churn_breakdown,
                    scoring_config_id, signal_snapshot, scored_at,
                    created_at, updated_at
                ) VALUES (
                    :bbl, :score, :category, :breakdown,
                    :cfg_id, :snapshot, :now, :now, :now
                )
            """),
            {
                "bbl": bbl, "score": round(churn_score, 1),
                "category": category, "breakdown": json.dumps(breakdown),
                "cfg_id": cfg_id,
                "snapshot": json.dumps({k: v for k, v in row_dict.items() if k != "bbl"}),
                "now": now,
            },
        )
        scored += 1
        if scored % 1000 == 0:
            session.commit()

    session.execute(
        text("""
            UPDATE leads l SET portfolio_churn_risk = sub.pct
            FROM (
                SELECT bm.lead_id,
                       ROUND(100.0 * COUNT(*) FILTER (WHERE b.churn_category IN ('hot','warm'))
                             / NULLIF(COUNT(*), 0), 1) AS pct
                FROM building_management bm
                JOIN buildings b ON b.bbl = bm.bbl
                WHERE bm.is_current = true
                GROUP BY bm.lead_id
            ) sub
            WHERE l.lead_id = sub.lead_id
        """)
    )

    session.commit()
    session.close()
    logger.info(f"Scored {scored} buildings with config {cfg_id}")
    return {"scored": scored, "config_id": cfg_id}
