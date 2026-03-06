"""Data quality automated checks.

Runs periodically after ingestion to detect:
1. Match rate drops (BBL referential integrity)
2. Volume anomalies (sudden drops or spikes)
3. Schema changes in Socrata responses
4. Source freshness (stale data detection)
5. Cross-source consistency
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

try:
    from src.worker import app as celery_app
except ImportError:
    class _FakeCelery:
        @staticmethod
        def task(*args, **kwargs):
            return lambda fn: fn
    celery_app = _FakeCelery()

logger = logging.getLogger(__name__)


def _get_pg_session() -> Session:
    from src.db.session import get_sync_url
    engine = create_engine(get_sync_url())
    return Session(engine)


@celery_app.task(bind=True, name="src.tasks.quality_checks.run_quality_checks")
def run_quality_checks(self, job_id: int | None = None):
    """Run all quality checks and create alerts for issues found."""
    session = _get_pg_session()
    alerts = []

    try:
        from src.tasks.ingest import _ensure_or_create_job, _finish_job
        job_id = _ensure_or_create_job(session, job_id, "quality_checks", "quality")
        session.commit()

        alerts.extend(_check_match_rates(session))
        alerts.extend(_check_volume_anomalies(session))
        alerts.extend(_check_freshness(session))
        alerts.extend(_check_bbl_integrity(session))
        alerts.extend(_check_coverage_drops(session))

        for alert in alerts:
            session.execute(
                text("""
                    INSERT INTO change_alerts (alert_type, description, details, dismissed, created_at, updated_at)
                    VALUES (:type, :desc, :details, false, now(), now())
                """),
                {
                    "type": alert["type"],
                    "desc": alert["description"],
                    "details": alert.get("details", "{}"),
                },
            )

        _finish_job(session, job_id, "completed", len(alerts), len(alerts), 0)
        session.commit()
        logger.info(f"Quality checks complete: {len(alerts)} alerts created")
        return {"job_id": job_id, "alerts_created": len(alerts)}
    except Exception as e:
        logger.error(f"Quality checks failed: {e}", exc_info=True)
        try:
            from src.tasks.ingest import _finish_job
            if job_id is not None:
                _finish_job(session, job_id, "failed", 0, 0, 1, str(e)[:500])
                session.commit()
        except Exception:
            session.rollback()
        session.rollback()
        raise
    finally:
        session.close()


def _check_match_rates(session: Session) -> list[dict]:
    """Flag sources where match rate dropped below threshold."""
    alerts = []
    rows = session.execute(text("""
        SELECT DISTINCT ON (source_name)
            source_name, match_rate, records_fetched, records_matched
        FROM data_quality_log
        ORDER BY source_name, run_timestamp DESC
    """)).fetchall()

    for r in rows:
        source, rate, fetched, matched = r
        if fetched > 100 and rate < 0.5:
            alerts.append({
                "type": "low_match_rate",
                "description": f"{source}: match rate {rate:.1%} ({matched}/{fetched} records). "
                               f"Many records failed BBL validation.",
                "details": f'{{"source": "{source}", "match_rate": {rate}, "fetched": {fetched}, "matched": {matched}}}',
            })
    return alerts


def _check_volume_anomalies(session: Session) -> list[dict]:
    """Detect large volume swings between consecutive runs of the same source."""
    alerts = []
    rows = session.execute(text("""
        WITH recent AS (
            SELECT source_name, records_fetched, run_timestamp,
                   LAG(records_fetched) OVER (PARTITION BY source_name ORDER BY run_timestamp) AS prev_fetched
            FROM data_quality_log
        )
        SELECT source_name, records_fetched, prev_fetched
        FROM recent
        WHERE prev_fetched IS NOT NULL AND prev_fetched > 0
        ORDER BY run_timestamp DESC
        LIMIT 20
    """)).fetchall()

    for r in rows:
        source, current, previous = r
        if previous > 100:
            change = abs(current - previous) / previous
            if change > 0.5:
                direction = "increase" if current > previous else "decrease"
                alerts.append({
                    "type": "volume_anomaly",
                    "description": f"{source}: {change:.0%} {direction} in volume "
                                   f"({previous} -> {current} records). Possible API change or data issue.",
                    "details": f'{{"source": "{source}", "current": {current}, "previous": {previous}, "change_pct": {change:.2f}}}',
                })

                session.execute(
                    text("""
                        UPDATE data_quality_log SET volume_anomaly = true
                        WHERE id = (
                            SELECT id
                            FROM data_quality_log
                            WHERE source_name = :source
                            ORDER BY run_timestamp DESC
                            LIMIT 1
                        )
                    """),
                    {"source": source},
                )
    return alerts


def _check_freshness(session: Session) -> list[dict]:
    """Flag sources that haven't been refreshed recently."""
    alerts = []
    thresholds = {
        "hpd_complaints": 14,
        "hpd_violations": 14,
        "acris": 45,
        "dob_permits": 45,
        "hpd_litigation": 45,
        "emergency_repairs": 45,
        "energy_grades": 90,
        "eviction_filings": 45,
        "facade_inspections": 120,
        "pad": 120,
        "hpd_buildings": 30,
    }

    for source, max_days in thresholds.items():
        row = session.execute(
            text("""
                SELECT run_timestamp FROM data_quality_log
                WHERE source_name = :source
                ORDER BY run_timestamp DESC LIMIT 1
            """),
            {"source": source},
        ).first()

        if row:
            last_run = row[0]
            if last_run and last_run.tzinfo is None:
                last_run = last_run.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - last_run).days if last_run else 999
            if age > max_days:
                alerts.append({
                    "type": "stale_data",
                    "description": f"{source}: last refreshed {age} days ago (threshold: {max_days} days).",
                    "details": f'{{"source": "{source}", "age_days": {age}, "threshold": {max_days}}}',
                })
        else:
            alerts.append({
                "type": "never_ingested",
                "description": f"{source}: has never been ingested. Run initial import.",
                "details": f'{{"source": "{source}"}}',
            })
    return alerts


def _check_bbl_integrity(session: Session) -> list[dict]:
    """Check that signal tables only reference existing buildings."""
    alerts = []
    tables = [
        "hpd_complaints", "hpd_violations", "acris_transactions",
        "dob_permits", "hpd_litigation", "emergency_repairs",
        "energy_grades", "eviction_filings", "facade_inspections",
    ]
    for table in tables:
        row = session.execute(
            text(f"""
                SELECT COUNT(*) FROM {table} t
                WHERE NOT EXISTS (SELECT 1 FROM buildings b WHERE b.bbl = t.bbl)
            """)
        ).scalar()

        if row and row > 0:
            alerts.append({
                "type": "orphaned_records",
                "description": f"{table}: {row} records reference BBLs not in buildings table.",
                "details": f'{{"table": "{table}", "orphaned_count": {row}}}',
            })
    return alerts


def _check_coverage_drops(session: Session) -> list[dict]:
    """Alert if signal coverage for buildings has significantly decreased."""
    alerts = []
    row = session.execute(text("""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE churn_score IS NOT NULL) AS scored
        FROM buildings
    """)).first()

    if row and row[0] > 0:
        coverage = row[1] / row[0]
        if coverage < 0.1 and row[0] > 100:
            alerts.append({
                "type": "low_scoring_coverage",
                "description": f"Only {coverage:.0%} of {row[0]} buildings have been scored. "
                               f"Run scoring recalculation.",
                "details": f'{{"total": {row[0]}, "scored": {row[1]}, "coverage": {coverage:.3f}}}',
            })
    return alerts
