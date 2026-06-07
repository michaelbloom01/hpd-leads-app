"""Claim-ledger materialization job entrypoint."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Optional

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


def _get_pg_session() -> Session:
    from src.db.session import get_sync_url

    engine = create_engine(get_sync_url())
    return Session(engine)


def _materialization_run_metrics(result: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    return {
        "dry_run": dry_run,
        "planned_claims_total": result.get("planned_claims_total"),
        "claims_upserted": result.get("claims_upserted", 0),
        "evidence_upserted": result.get("evidence_upserted", 0),
        "confidence_snapshots_upserted": result.get("confidence_snapshots_upserted", 0),
        "skipped_claims": result.get("skipped_claims", 0),
        "conflict_count": len(result.get("conflicts") or []),
        "before_counts": result.get("before_counts") or {},
        "after_counts": result.get("after_counts") or {},
        "rollback_plan": result.get("rollback_plan") or {},
        "rollback_manifest": result.get("rollback_manifest") or {},
        "sources": result.get("planned_claims_by_source") or result.get("claims_upserted_by_source") or {},
        "selected_sources": result.get("selected_sources") or [],
        "source_filter_applied": bool(result.get("source_filter_applied")),
    }


def _materialization_sample_findings(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "sample_building_management_claims": result.get("sample_building_management_claims", []),
        "sample_hpd_management_link_claims": result.get("sample_hpd_management_link_claims", []),
        "sample_hpd_contact_claims": result.get("sample_hpd_contact_claims", []),
        "sample_enrichment_observation_claims": result.get("sample_enrichment_observation_claims", []),
        "sample_building_signal_claims": result.get("sample_building_signal_claims", []),
        "conflicts": result.get("conflicts", []),
    }


async def _run_materialization_async(
    *,
    limit: int,
    dry_run: bool,
    confirm_execute: bool,
    run_id: str,
    sources: Any = None,
) -> dict[str, Any]:
    from src.db.session import get_session_factory
    from src.services.truth_health import build_schema_readiness_report, is_truth_schema_current, load_truth_schema_status
    from src.services.truth_materialization import materialize_truth_claims

    factory = get_session_factory()
    async with factory() as session:
        schema_status = await load_truth_schema_status(session)
        if not is_truth_schema_current(schema_status):
            return build_schema_readiness_report(schema_status=schema_status)
        return await materialize_truth_claims(
            session,
            limit=limit,
            dry_run=dry_run,
            confirm_execute=confirm_execute,
            run_id=run_id,
            sources=sources,
        )


def run_truth_materialization(
    job_id: Optional[int] = None,
    limit: int = 500,
    dry_run: bool = True,
    confirm_execute: bool = False,
    sources: list[str] | tuple[str, ...] | str | None = None,
) -> dict[str, Any]:
    if not dry_run and not confirm_execute:
        raise ValueError("truth materialization execution requires confirm_execute=true")

    run_id = f"truth-materialization-{job_id or 'manual'}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
    started_at = datetime.now(timezone.utc)
    result = asyncio.run(_run_materialization_async(
        limit=limit,
        dry_run=dry_run,
        confirm_execute=confirm_execute,
        run_id=run_id,
        sources=sources,
    ))
    if result.get("schema_status") and not result["schema_status"].get("migration_current"):
        return {"run_id": run_id, "status": "schema_not_ready", **result}
    session = _get_pg_session()
    try:
        metrics = _materialization_run_metrics(result, dry_run=dry_run)
        session.execute(
            text("""
                INSERT INTO truth_validation_runs (
                    run_id, run_type, dry_run, status, started_at, finished_at,
                    scope, metrics, sample_findings, rollback_strategy, created_at, updated_at
                )
                VALUES (
                    :run_id, 'truth_claim_materialization', :dry_run, 'completed', :started_at, :finished_at,
                    CAST(:scope AS JSONB), CAST(:metrics AS JSONB), CAST(:sample_findings AS JSONB),
                    :rollback_strategy, NOW(), NOW()
                )
                ON CONFLICT (run_id)
                DO UPDATE SET
                    dry_run = EXCLUDED.dry_run,
                    status = EXCLUDED.status,
                    finished_at = EXCLUDED.finished_at,
                    scope = EXCLUDED.scope,
                    metrics = EXCLUDED.metrics,
                    sample_findings = EXCLUDED.sample_findings,
                    rollback_strategy = EXCLUDED.rollback_strategy,
                    updated_at = NOW()
            """),
            {
                "run_id": run_id,
                "dry_run": dry_run,
                "started_at": started_at,
                "finished_at": datetime.now(timezone.utc),
                "scope": json.dumps({
                    "limit": limit,
                    "dry_run": dry_run,
                    "confirm_execute": confirm_execute,
                    "sources": result.get("selected_sources") or sources,
                    "source_filter_applied": bool(result.get("source_filter_applied")),
                }),
                "metrics": json.dumps(metrics),
                "sample_findings": json.dumps(_materialization_sample_findings(result)),
                "rollback_strategy": result.get("rollback_strategy"),
            },
        )
        if job_id is not None:
            processed = int(result.get("claims_upserted") or result.get("planned_claims_total") or 0)
            session.execute(
                text("""
                    UPDATE ingestion_jobs
                    SET status = 'completed',
                        processed = :processed,
                        succeeded = :succeeded,
                        failed = 0,
                        finished_at = NOW(),
                        updated_at = NOW()
                    WHERE id = :job_id
                """),
                {"job_id": job_id, "processed": processed, "succeeded": processed},
            )
        session.commit()
        return {"run_id": run_id, **result}
    except Exception:
        session.rollback()
        if job_id is not None:
            session.execute(
                text("""
                    UPDATE ingestion_jobs
                    SET status = 'failed', failed = 1, finished_at = NOW(), updated_at = NOW()
                    WHERE id = :job_id
                """),
                {"job_id": job_id},
            )
            session.commit()
        raise
    finally:
        session.close()
        session.bind.dispose()


@celery_app.task(bind=True, name="src.tasks.truth_materialization.run_truth_materialization_task")
def run_truth_materialization_task(
    self,
    job_id: Optional[int] = None,
    limit: int = 500,
    dry_run: bool = True,
    confirm_execute: bool = False,
    sources: list[str] | tuple[str, ...] | str | None = None,
) -> dict[str, Any]:
    return run_truth_materialization(
        job_id=job_id,
        limit=limit,
        dry_run=dry_run,
        confirm_execute=confirm_execute,
        sources=sources,
    )
