"""Approval-gated bounded DOB Safety refresh using the existing worker runtime."""

from uuid import uuid4

from sqlalchemy import text

from src.ingest.dob_safety import (
    SOURCE_SYSTEM,
    DOBSafetyClient,
    normalize_record,
    validate_bins,
)
from src.services.compliance import publish_snapshot
from src.tasks.ingest import (
    _ensure_or_create_job,
    _finish_job,
    _get_pg_session,
    _log_quality,
)
from src.worker import app as celery_app


@celery_app.task(bind=True, name="src.tasks.compliance.ingest_dob_safety")
def ingest_dob_safety(
    self,
    job_id: int | None = None,
    bins: list[str] | None = None,
    dry_run: bool = True,
    confirm_execute: bool = False,
):
    bins = validate_bins(bins)
    if not dry_run and not confirm_execute:
        raise ValueError(
            "Executing DOB Safety publication requires confirm_execute=true."
        )
    session = _get_pg_session()
    job_type = "dob_safety_preview" if dry_run else SOURCE_SYSTEM
    run_id = f"dob-safety-{uuid4().hex}"
    try:
        job_id = _ensure_or_create_job(session, job_id, job_type, job_type)
        session.commit()
        snapshot = DOBSafetyClient().fetch_snapshot(bins)
        # Normalize every row before publishing or calling the preview successful.
        normalized = [
            normalize_record(
                row,
                observed_at=snapshot["observed_at"],
                source_updated_at=snapshot["source_updated_at"],
                run_id=run_id,
            )
            for row in snapshot["rows"]
        ]
        summary = {
            "job_id": job_id,
            "run_id": run_id,
            "dry_run": dry_run,
            "bins": bins,
            "fetched": len(normalized),
            "checked_bins": len(bins),
            "source_updated_at": snapshot["source_updated_at"].isoformat(),
            "observed_at": snapshot["observed_at"].isoformat(),
            "snapshot_hash": snapshot["snapshot_hash"],
            "identity_conflicts": sum(
                row["identity_status"] != "exact_source_bin" for row in normalized
            ),
            "reported_balance_available": False,
        }
        if not dry_run:
            summary.update(publish_snapshot(session, snapshot, run_id=run_id))
            _log_quality(
                session,
                SOURCE_SYSTEM,
                job_id,
                len(normalized),
                len(normalized),
                0,
                summary["inserted"],
                notes=f"Complete bounded snapshot; {len(bins)} exact BINs; source_updated_at={summary['source_updated_at']}; run={run_id}; no monetary fields.",
            )
        # Store the preview evidence in job config; preview does not create source
        # checks, compliance rows, observations, or successful-refresh audit rows.
        import json

        session.execute(
            text("""
            UPDATE ingestion_jobs SET config = COALESCE(config, '{}'::jsonb) || CAST(:result AS jsonb),
                processed = :processed WHERE id = :job_id
        """),
            {
                "result": json.dumps({"result": summary}),
                "processed": len(normalized),
                "job_id": job_id,
            },
        )
        _finish_job(session, job_id, "completed", len(normalized), len(normalized), 0)
        session.commit()
        return summary
    except Exception as exc:
        session.rollback()
        if job_id is not None:
            _finish_job(session, job_id, "failed", 0, 0, 1, str(exc)[:500])
            session.commit()
        raise
    finally:
        session.close()
