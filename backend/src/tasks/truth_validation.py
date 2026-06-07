"""Truth validation job entrypoint.

The default implementation is preview-only: it records the run envelope and
sample findings without changing leads, links, claims, or review queues.
"""

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


SEVERITY_PRIORITY = {
    "critical": 95,
    "high": 85,
    "medium": 65,
    "low": 45,
}


def _review_subject(sample: dict[str, Any], check_name: str) -> tuple[str, str]:
    if sample.get("lead_id"):
        return "lead", str(sample["lead_id"])
    if sample.get("canonical_entity_id"):
        return "canonical_entity", str(sample["canonical_entity_id"])
    if sample.get("bbl"):
        return "building", str(sample["bbl"])
    source_name = str(sample.get("source_name") or check_name)
    source_record = str(sample.get("source_record_id") or sample.get("id") or sample.get("name") or "sample")
    return "validation_sample", f"{source_name}:{source_record}"[:80]


def build_review_items_from_validation_preview(preview: dict[str, Any], *, run_id: str) -> list[dict[str, Any]]:
    from src.services.confidence import review_bucket
    from src.services.truth_program import stable_claim_id

    items: list[dict[str, Any]] = []
    for check in preview.get("checks") or []:
        check_name = str(check.get("check") or "validation_check")
        severity = str(check.get("severity") or "medium")
        queue_name = str(check.get("recommended_queue") or review_bucket(confidence_score=0.5, contradictions=1))
        for index, sample in enumerate(check.get("sample") or []):
            if not isinstance(sample, dict):
                continue
            subject_type, subject_id = _review_subject(sample, check_name)
            review_id = stable_claim_id("truth_review", run_id, check_name, subject_type, subject_id, index)
            items.append({
                "review_id": review_id,
                "queue_name": queue_name,
                "subject_type": subject_type,
                "subject_id": subject_id,
                "status": "open",
                "priority": SEVERITY_PRIORITY.get(severity, 55),
                "confidence_score": None,
                "actionability_level": "do_not_act" if severity in {"critical", "high"} else "broad_discovery",
                "proposed_change": {
                    "operation": "review_validation_finding",
                    "check": check_name,
                    "sample_index": index,
                    "source": "adversarial_truth_validation",
                },
                "supporting_evidence": {
                    "validation_check": check_name,
                    "sample": sample,
                },
                "contradicting_evidence": {
                    "severity": severity,
                    "why_it_matters": check.get("why_it_matters"),
                },
                "rationale": {
                    "generated_from_run_id": run_id,
                    "check": check_name,
                    "severity": severity,
                    "recommended_queue": queue_name,
                    "why_it_matters": check.get("why_it_matters"),
                    "count_sampled": check.get("count_sampled"),
                },
                "run_id": run_id,
            })
    return items


async def _run_preview_async(sample_limit: int) -> dict[str, Any]:
    from src.db.session import get_session_factory
    from src.services.golden_benchmark import load_golden_benchmark
    from src.services.truth_health import build_schema_readiness_report, is_truth_schema_current, load_truth_schema_status
    from src.services.truth_program import preview_adversarial_validation

    factory = get_session_factory()
    async with factory() as session:
        schema_status = await load_truth_schema_status(session)
        if not is_truth_schema_current(schema_status):
            return build_schema_readiness_report(schema_status=schema_status)
        preview = await preview_adversarial_validation(session, sample_limit=sample_limit)
        benchmark = await load_golden_benchmark(session)
        return {**preview, "golden_benchmark": benchmark}


def _upsert_review_items(session: Session, items: list[dict[str, Any]]) -> int:
    for item in items:
        session.execute(
            text("""
                INSERT INTO truth_review_items (
                    review_id, queue_name, subject_type, subject_id, status, priority,
                    confidence_score, actionability_level, proposed_change,
                    supporting_evidence, contradicting_evidence, rationale, run_id,
                    created_at, updated_at
                )
                VALUES (
                    :review_id, :queue_name, :subject_type, :subject_id, :status, :priority,
                    :confidence_score, :actionability_level, CAST(:proposed_change AS JSONB),
                    CAST(:supporting_evidence AS JSONB), CAST(:contradicting_evidence AS JSONB),
                    CAST(:rationale AS JSONB), :run_id, NOW(), NOW()
                )
                ON CONFLICT (review_id)
                DO UPDATE SET
                    queue_name = EXCLUDED.queue_name,
                    subject_type = EXCLUDED.subject_type,
                    subject_id = EXCLUDED.subject_id,
                    priority = EXCLUDED.priority,
                    confidence_score = EXCLUDED.confidence_score,
                    actionability_level = EXCLUDED.actionability_level,
                    proposed_change = EXCLUDED.proposed_change,
                    supporting_evidence = EXCLUDED.supporting_evidence,
                    contradicting_evidence = EXCLUDED.contradicting_evidence,
                    rationale = EXCLUDED.rationale,
                    run_id = EXCLUDED.run_id,
                    updated_at = NOW()
            """),
            {
                **item,
                "proposed_change": json.dumps(item.get("proposed_change") or {}),
                "supporting_evidence": json.dumps(item.get("supporting_evidence") or {}),
                "contradicting_evidence": json.dumps(item.get("contradicting_evidence") or {}),
                "rationale": json.dumps(item.get("rationale") or {}),
            },
        )
    return len(items)


def _rollback_command(run_id: str) -> str:
    return f"python scripts/truth_validation_rollback.py --run-id {run_id} --execute --confirm-execute"


def run_truth_validation(
    job_id: Optional[int] = None,
    sample_limit: int = 20,
    dry_run: bool = True,
    confirm_execute: bool = False,
) -> dict[str, Any]:
    if not dry_run and not confirm_execute:
        raise ValueError("truth validation review-item execution requires confirm_execute=true")

    run_id = f"truth-preview-{job_id or 'manual'}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
    preview = asyncio.run(_run_preview_async(sample_limit))
    if preview.get("schema_status") and not preview["schema_status"].get("migration_current"):
        return {"run_id": run_id, "status": "schema_not_ready", **preview}
    proposed_review_items = build_review_items_from_validation_preview(preview, run_id=run_id)
    if dry_run:
        return {
            "run_id": run_id,
            **preview,
            "dry_run": True,
            "proposed_review_items": proposed_review_items,
            "review_items_planned": len(proposed_review_items),
            "review_items_upserted": 0,
            "mutations_planned": 0,
            "required_execute_command": (
                f"python scripts/truth_validation_run.py --sample-limit {sample_limit} --execute --confirm-execute"
                if job_id is None
                else f"/api/v1/jobs/truth_validation/start?dry_run=false&confirm_execute=true&limit={sample_limit}"
            ),
            "rollback_strategy": "No mutation in dry-run mode; execute mode can be rolled back by run_id.",
        }
    session = _get_pg_session()
    try:
        review_items_upserted = 0
        review_items_upserted = _upsert_review_items(session, proposed_review_items)
        session.execute(
            text("""
                INSERT INTO truth_validation_runs (
                    run_id, run_type, dry_run, status, started_at, finished_at,
                    scope, metrics, sample_findings, rollback_strategy, created_at, updated_at
                )
                VALUES (
                    :run_id, 'adversarial_truth_validation', :dry_run, 'completed', :started_at, :finished_at,
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
                "started_at": preview["generated_at"],
                "finished_at": datetime.now(timezone.utc),
                "scope": json.dumps({"sample_limit": sample_limit, "dry_run": dry_run, "confirm_execute": confirm_execute}),
                "metrics": json.dumps({
                    "checks_run": len(preview.get("checks") or []),
                    "review_items_planned": len(proposed_review_items),
                    "review_items_upserted": review_items_upserted,
                    "mutations_planned": review_items_upserted,
                    "rollback_command": _rollback_command(run_id),
                    "golden_benchmark": (preview.get("golden_benchmark") or {}).get("metrics", {}),
                    "golden_benchmark_coverage": (preview.get("golden_benchmark") or {}).get("benchmark_coverage"),
                }),
                "sample_findings": json.dumps({
                    "checks": preview.get("checks") or [],
                    "proposed_review_items": proposed_review_items[:50],
                    "golden_benchmark_cases": (preview.get("golden_benchmark") or {}).get("cases", []),
                }),
                "rollback_strategy": "Delete truth_review_items whose run_id matches this validation run_id; no leads, buildings, claims, or canonical links are changed.",
            },
        )
        if job_id is not None:
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
                {"job_id": job_id, "processed": len(preview.get("checks") or []), "succeeded": len(preview.get("checks") or [])},
            )
        session.commit()
        return {
            "run_id": run_id,
            **preview,
            "dry_run": dry_run,
            "proposed_review_items": proposed_review_items,
            "review_items_planned": len(proposed_review_items),
            "review_items_upserted": review_items_upserted,
            "mutations_planned": review_items_upserted,
            "rollback_command": _rollback_command(run_id),
            "rollback_strategy": "Delete truth_review_items whose run_id matches this validation run_id; no leads, buildings, claims, or canonical links are changed.",
        }
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


@celery_app.task(bind=True, name="src.tasks.truth_validation.run_truth_validation_task")
def run_truth_validation_task(
    self,
    job_id: Optional[int] = None,
    sample_limit: int = 20,
    dry_run: bool = True,
    confirm_execute: bool = False,
) -> dict[str, Any]:
    return run_truth_validation(
        job_id=job_id,
        sample_limit=sample_limit,
        dry_run=dry_run,
        confirm_execute=confirm_execute,
    )
