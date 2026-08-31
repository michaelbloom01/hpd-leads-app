"""Approval-gated bounded DOB source refreshes using the existing worker runtime."""

from uuid import uuid4

from sqlalchemy import text

from src.ingest import dob_complaints, dob_ecb, dob_violations, oath_ecb
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
    return _ingest_source(
        source_system=SOURCE_SYSTEM,
        client=DOBSafetyClient(),
        normalizer=normalize_record,
        job_id=job_id,
        bins=bins,
        dry_run=dry_run,
        confirm_execute=confirm_execute,
    )


@celery_app.task(bind=True, name="src.tasks.compliance.ingest_dob_complaints")
def ingest_dob_complaints(
    self,
    job_id: int | None = None,
    bins: list[str] | None = None,
    dry_run: bool = True,
    confirm_execute: bool = False,
):
    bins = dob_complaints.validate_bins(bins)
    return _ingest_source(
        source_system=dob_complaints.SOURCE_SYSTEM,
        client=dob_complaints.DOBComplaintsClient(),
        normalizer=dob_complaints.normalize_record,
        job_id=job_id,
        bins=bins,
        dry_run=dry_run,
        confirm_execute=confirm_execute,
    )


@celery_app.task(bind=True, name="src.tasks.compliance.ingest_dob_violations")
def ingest_dob_violations(
    self,
    job_id: int | None = None,
    bins: list[str] | None = None,
    dry_run: bool = True,
    confirm_execute: bool = False,
):
    bins = dob_violations.validate_bins(bins)
    return _ingest_source(
        source_system=dob_violations.SOURCE_SYSTEM,
        client=dob_violations.DOBViolationsClient(),
        normalizer=dob_violations.normalize_record,
        job_id=job_id,
        bins=bins,
        dry_run=dry_run,
        confirm_execute=confirm_execute,
    )


@celery_app.task(bind=True, name="src.tasks.compliance.ingest_dob_ecb")
def ingest_dob_ecb(
    self,
    job_id: int | None = None,
    bins: list[str] | None = None,
    dry_run: bool = True,
    confirm_execute: bool = False,
):
    bins = dob_ecb.validate_bins(bins)
    return _ingest_source(
        source_system=dob_ecb.SOURCE_SYSTEM,
        client=dob_ecb.DOBECBClient(),
        normalizer=dob_ecb.normalize_record,
        job_id=job_id,
        bins=bins,
        dry_run=dry_run,
        confirm_execute=confirm_execute,
    )


@celery_app.task(bind=True, name="src.tasks.compliance.ingest_oath_ecb")
def ingest_oath_ecb(
    self,
    job_id: int | None = None,
    bins: list[str] | None = None,
    dry_run: bool = True,
    confirm_execute: bool = False,
):
    bins = oath_ecb.validate_bins(bins)
    return _ingest_source(
        source_system=oath_ecb.SOURCE_SYSTEM,
        client=oath_ecb.OATHFromDOBECBClient(),
        normalizer=oath_ecb.normalize_record,
        job_id=job_id,
        bins=bins,
        dry_run=dry_run,
        confirm_execute=confirm_execute,
    )


def _ingest_source(
    *, source_system, client, normalizer, job_id, bins, dry_run, confirm_execute
):
    if not dry_run and not confirm_execute:
        raise ValueError(
            "Executing DOB source publication requires confirm_execute=true."
        )
    session = _get_pg_session()
    job_type = f"{source_system}_preview" if dry_run else source_system
    run_id = f"{source_system.replace('_', '-')}-{uuid4().hex}"
    try:
        job_id = _ensure_or_create_job(session, job_id, job_type, job_type)
        session.commit()
        snapshot = client.fetch_snapshot(bins)
        if snapshot.get("source_system", source_system) != source_system:
            raise ValueError("Fetched snapshot belongs to a different source.")
        snapshot["source_system"] = source_system
        if (
            not snapshot.get("complete")
            or len(snapshot["rows"]) != snapshot["expected_count"]
            or sorted(snapshot["bins"]) != bins
        ):
            raise ValueError(
                "A complete count-verified snapshot of the requested BIN scope is required."
            )
        # Normalize every row before publishing or calling the preview successful.
        normalized = [
            normalizer(
                row,
                observed_at=snapshot["observed_at"],
                source_updated_at=snapshot["source_updated_at"],
                run_id=run_id,
            )
            for row in snapshot["rows"]
        ]
        if any(row["bin"] not in bins for row in normalized) or len(
            {row["id"] for row in normalized}
        ) != len(normalized):
            raise ValueError(
                "Snapshot source records must be unique and inside the requested BIN scope."
            )
        summary = {
            "job_id": job_id,
            "source_system": source_system,
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
                source_system,
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
