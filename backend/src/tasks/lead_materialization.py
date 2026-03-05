"""Lead materialization and coverage reconciliation tasks."""

import logging
from typing import Optional

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


def _compute_entity_coverage(session: Session) -> dict[str, int | float]:
    row = session.execute(text("""
        WITH source_entities AS (
            SELECT DISTINCT REGEXP_REPLACE(UPPER(TRIM(corporation_name)), '\\s+', ' ', 'g') AS normalized_name
            FROM building_contacts
            WHERE corporation_name IS NOT NULL
              AND TRIM(corporation_name) != ''
        ),
        lead_entities AS (
            SELECT DISTINCT normalized_name
            FROM leads
            WHERE normalized_name IS NOT NULL
              AND TRIM(normalized_name) != ''
        )
        SELECT
            (SELECT COUNT(*) FROM source_entities) AS source_count,
            (SELECT COUNT(*) FROM lead_entities) AS lead_count,
            (
                SELECT COUNT(*)
                FROM source_entities s
                JOIN lead_entities l ON l.normalized_name = s.normalized_name
            ) AS matched_count
    """)).first()
    source_count = int(row[0] or 0) if row else 0
    lead_count = int(row[1] or 0) if row else 0
    matched_count = int(row[2] or 0) if row else 0
    coverage_ratio = (matched_count / source_count) if source_count > 0 else 1.0
    return {
        "source_count": source_count,
        "lead_count": lead_count,
        "matched_count": matched_count,
        "coverage_ratio": coverage_ratio,
    }


@celery_app.task(bind=True, name="src.tasks.lead_materialization.generate_leads_job")
def generate_leads_job(self, job_id: Optional[int] = None, min_portfolio: int = 1):
    """Materialize leads from building_contacts and log coverage quality."""
    session = _get_pg_session()
    try:
        from src.tasks.ingest import _ensure_or_create_job, _finish_job, _log_quality

        job_id = _ensure_or_create_job(session, job_id, "lead_generation", "lead_generation")
        session.commit()

        from src.tasks.generate_leads import run_generate_leads
        run_generate_leads(min_portfolio=min_portfolio)

        coverage = _compute_entity_coverage(session)
        rejected = max(0, int(coverage["source_count"]) - int(coverage["matched_count"]))
        _log_quality(
            session=session,
            source="lead_generation",
            job_id=job_id,
            fetched=int(coverage["source_count"]),
            matched=int(coverage["matched_count"]),
            rejected=rejected,
            inserted=int(coverage["lead_count"]),
            notes=f"coverage_ratio={float(coverage['coverage_ratio']):.4f}",
        )
        _finish_job(
            session,
            job_id=job_id,
            status="completed",
            total=int(coverage["source_count"]),
            succeeded=int(coverage["matched_count"]),
            failed=rejected,
        )
        session.commit()
        logger.info(
            "Lead generation completed: source=%s matched=%s leads=%s ratio=%.2f%%",
            coverage["source_count"],
            coverage["matched_count"],
            coverage["lead_count"],
            float(coverage["coverage_ratio"]) * 100,
        )
        try:
            reconcile_lead_coverage.delay()
        except Exception as rec_exc:
            logger.warning("Failed to queue lead reconciliation after generation: %s", rec_exc)
        return {"job_id": job_id, **coverage}
    except Exception as exc:
        logger.exception("Lead generation job failed: %s", exc)
        try:
            from src.tasks.ingest import _finish_job
            if job_id is not None:
                _finish_job(session, job_id, "failed", 0, 0, 1, str(exc)[:500])
                session.commit()
        except Exception:
            session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(bind=True, name="src.tasks.lead_materialization.reconcile_lead_coverage")
def reconcile_lead_coverage(self, job_id: Optional[int] = None):
    """Log lead coverage integrity and emit alerts when materialization drifts."""
    session = _get_pg_session()
    try:
        from src.tasks.ingest import _ensure_or_create_job, _finish_job, _log_quality

        job_id = _ensure_or_create_job(session, job_id, "lead_reconciliation", "lead_coverage")
        session.commit()

        coverage = _compute_entity_coverage(session)
        rejected = max(0, int(coverage["source_count"]) - int(coverage["matched_count"]))
        _log_quality(
            session=session,
            source="lead_coverage",
            job_id=job_id,
            fetched=int(coverage["source_count"]),
            matched=int(coverage["matched_count"]),
            rejected=rejected,
            inserted=int(coverage["lead_count"]),
            notes=f"coverage_ratio={float(coverage['coverage_ratio']):.4f}",
        )

        if float(coverage["coverage_ratio"]) < 0.95:
            session.execute(
                text("""
                    INSERT INTO change_alerts (alert_type, description, details, created_at, updated_at)
                    VALUES (:alert_type, :description, :details, NOW(), NOW())
                """),
                {
                    "alert_type": "lead_coverage_gap",
                    "description": "Lead materialization coverage dropped below 95%",
                    "details": (
                        f'{{"source_count": {int(coverage["source_count"])}, '
                        f'"matched_count": {int(coverage["matched_count"])}, '
                        f'"lead_count": {int(coverage["lead_count"])}, '
                        f'"coverage_ratio": {float(coverage["coverage_ratio"]):.4f}}}'
                    ),
                },
            )

        _finish_job(
            session,
            job_id=job_id,
            status="completed",
            total=int(coverage["source_count"]),
            succeeded=int(coverage["matched_count"]),
            failed=rejected,
        )
        session.commit()
        return {"job_id": job_id, **coverage}
    except Exception as exc:
        logger.exception("Lead reconciliation failed: %s", exc)
        try:
            from src.tasks.ingest import _finish_job
            if job_id is not None:
                _finish_job(session, job_id, "failed", 0, 0, 1, str(exc)[:500])
                session.commit()
        except Exception:
            session.rollback()
        raise
    finally:
        session.close()

