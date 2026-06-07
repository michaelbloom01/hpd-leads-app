"""Read-only activation packet helpers for the truth-confidence program."""

from __future__ import annotations

from typing import Any

from src.services.truth_health import EXPECTED_TRUTH_ALEMBIC_REVISION

BUSINESS_USE_REQUIRED_STEPS = {
    "apply_truth_schema",
    "run_materialization_dry_run",
    "review_truth_outputs",
    "execute_truth_materialization",
    "refresh_or_record_sources",
    "allow_business_use",
}


def _claim_readiness(summary: dict[str, Any]) -> dict[str, Any]:
    claim_count = int(summary.get("claim_count") or 0)
    verified_claim_count = int(summary.get("verified_claim_count") or 0)
    critical_or_high_gap_count = int(summary.get("critical_or_high_gap_count") or 0)
    return {
        "claim_count": claim_count,
        "verified_claim_count": verified_claim_count,
        "critical_or_high_gap_count": critical_or_high_gap_count,
        "has_materialized_claims": claim_count > 0,
        "has_verified_claims": verified_claim_count > 0,
        "has_no_critical_or_high_gaps": critical_or_high_gap_count == 0,
    }


def _gate_statuses(health_report: dict[str, Any]) -> dict[str, str]:
    return {
        str(item.get("step")): str(item.get("status"))
        for item in health_report.get("activation_checklist") or []
        if item.get("step")
    }


def _approval_steps(health_report: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for item in health_report.get("activation_checklist") or []:
        if not item.get("approval_required"):
            continue
        steps.append({
            "step": item.get("step"),
            "status": item.get("status"),
            "reason": item.get("reason"),
            "mutations_planned": item.get("mutations_planned", 0),
        })
    return steps


def _verification_frontier_summary(health_report: dict[str, Any]) -> dict[str, Any]:
    adjudication = health_report.get("adjudication_preview")
    if not isinstance(adjudication, dict):
        adjudication = {}
    ledger = adjudication.get("ledger_source_overlap")
    if not isinstance(ledger, dict):
        ledger = {}
    verified_gap_plan = adjudication.get("verified_confidence_gap_plan")
    if not isinstance(verified_gap_plan, dict):
        verified_gap_plan = {}
    verification_gap_plan = adjudication.get("verification_gap_plan")
    if not isinstance(verification_gap_plan, dict):
        verification_gap_plan = {}
    manager_preview = adjudication.get("manager_external_source_acquisition_preview")
    if not isinstance(manager_preview, dict):
        manager_preview = {}
    manager_next_batches = manager_preview.get("next_source_batches")
    if not isinstance(manager_next_batches, dict):
        manager_next_batches = {}
    operator_preview = adjudication.get("operator_confirmed_management_preview")
    if not isinstance(operator_preview, dict):
        operator_preview = {}
    operator_next_batches = operator_preview.get("second_source_seed_batches")
    if not isinstance(operator_next_batches, dict):
        operator_next_batches = {}
    verification_candidate_count = int(adjudication.get("verification_candidate_count") or 0)
    source_ready_count = int(ledger.get("source_ready_fact_group_count") or 0)
    source_ready_below_verified_count = int(verified_gap_plan.get("proposal_count") or 0)
    single_source_gap_count = int(verification_gap_plan.get("proposal_count") or 0)
    evidence_acquisition_required = (
        verification_candidate_count == 0
        and (source_ready_below_verified_count > 0 or single_source_gap_count > 0)
    )
    return {
        "dry_run": True,
        "mutations_planned": 0,
        "verification_candidate_count": verification_candidate_count,
        "current_ledger": {
            "total_fact_group_count": ledger.get("total_fact_group_count"),
            "single_source_fact_group_count": ledger.get("single_source_fact_group_count"),
            "multi_source_fact_group_count": ledger.get("multi_source_fact_group_count"),
            "source_ready_fact_group_count": source_ready_count,
        },
        "source_ready_below_verified_count": source_ready_below_verified_count,
        "single_source_gap_count": single_source_gap_count,
        "single_source_upgrade_would_verify_count": verified_gap_plan.get(
            "single_source_upgrade_would_verify_count"
        ),
        "bundle_upgrade_would_verify_count": verified_gap_plan.get("bundle_upgrade_would_verify_count"),
        "manager_next_source_seed_count": manager_next_batches.get("candidate_count"),
        "operator_second_source_seed_count": operator_next_batches.get("candidate_count"),
        "evidence_acquisition_required": evidence_acquisition_required,
        "business_use_blocker": (
            "No facts are verified or eligible for verified adjudication yet. Source-ready facts still need "
            "stronger exact-property, role-specific evidence before business use can be activated."
            if evidence_acquisition_required or source_ready_count == 0
            else None
        ),
        "next_preview_command": "python scripts/truth_verification_frontier.py --limit 10 --indent 2",
        "safe_action": (
            "Review the verification frontier and evidence request packet. Do not activate business use until "
            "verified candidates exist, adjudication passes, and source freshness/production gates are clear."
        ),
    }


def _business_use_allowed(health_report: dict[str, Any]) -> bool:
    summary = health_report.get("summary") or {}
    claim_readiness = _claim_readiness(summary)
    gates = _gate_statuses(health_report)
    source_plan = health_report.get("source_refresh_plan") or {}
    source_summary = source_plan.get("summary") or {}
    required_gates_complete = all(gates.get(step) == "complete" for step in BUSINESS_USE_REQUIRED_STEPS)
    return (
        summary.get("trust_posture") != "not_ready"
        and claim_readiness["has_materialized_claims"]
        and claim_readiness["has_verified_claims"]
        and claim_readiness["has_no_critical_or_high_gaps"]
        and required_gates_complete
        and not health_report.get("trust_gaps")
        and not source_plan.get("approval_required")
        and int(source_summary.get("blocked_job_count") or 0) == 0
        and int(source_summary.get("non_refreshable_gap_count") or 0) == 0
    )


def _source_refresh_jobs(source_plan: dict[str, Any], *, limit: int = 5) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for item in source_plan.get("items") or []:
        sources = [
            {
                "source_name": source.get("source_name"),
                "status": source.get("status"),
                "source_age_days": source.get("source_age_days"),
            }
            for source in (item.get("sources") or [])[:3]
        ]
        jobs.append({
            "job_type": item.get("job_type"),
            "reason": item.get("reason"),
            "priority": item.get("priority"),
            "blocked": bool(item.get("blocked")),
            "approval_required": bool(item.get("approval_required")),
            "preview_endpoint": item.get("preview_endpoint"),
            "execute_endpoint": item.get("execute_endpoint"),
            "source_count": len(item.get("sources") or []),
            "sources": sources,
        })
        if len(jobs) >= limit:
            break
    return jobs


def _next_safe_steps(
    *,
    schema_ready: bool,
    ready_to_apply_schema: bool,
    source_plan: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    preview_step = "preview_materialization"
    review_step = "review_materialization_evidence"
    source_plan = source_plan or {}
    source_summary = source_plan.get("summary") or {}
    steps: list[dict[str, Any]] = [
        {
            "step": "review_health_report",
            "command": "python scripts/truth_health_report.py --indent 2",
            "mutates_data": False,
        },
    ]

    if not schema_ready:
        preview_step = "preview_materialization_after_schema"
        steps.insert(0, {
            "step": "review_preflight_sql",
            "command": "python scripts/truth_migration_preflight.py --indent 2",
            "mutates_data": False,
        })
        if ready_to_apply_schema:
            steps.append({
                "step": "apply_truth_schema",
                "command": "python -m alembic upgrade 009_truth_confidence_program",
                "mutates_data": True,
                "requires_explicit_approval": True,
            })
        steps.append({
            "step": "preview_materialization_after_schema",
            "command": "POST /api/v1/truth/materialize/preview",
            "mutates_data": False,
            "blocked_until": "apply_truth_schema",
        })
        review_step = "review_materialization_evidence_after_schema"
    else:
        steps.append({
            "step": "preview_materialization",
            "command": "POST /api/v1/truth/materialize/preview",
            "mutates_data": False,
        })

    steps.append({
        "step": review_step,
        "command": "Inspect sample_materialized_claim_specs, validation checks, review queue, golden benchmark, and rollback plan",
        "mutates_data": False,
        "blocked_until": preview_step,
    })
    steps.append({
        "step": "preview_validation_review_queue",
        "command": "python scripts/truth_validation_run.py --sample-limit 20 --indent 2",
        "mutates_data": False,
        "blocked_until": "review_health_report",
    })
    steps.append({
        "step": "review_verification_frontier",
        "command": "python scripts/truth_verification_frontier.py --limit 10 --indent 2",
        "mutates_data": False,
        "blocked_until": "review_health_report",
    })
    steps.append({
        "step": "execute_validation_review_queue_after_review",
        "command": "python scripts/truth_validation_run.py --sample-limit 20 --execute --confirm-execute",
        "mutates_data": True,
        "requires_explicit_approval": True,
        "blocked_until": "preview_validation_review_queue",
    })
    steps.append({
        "step": "execute_materialization_after_review",
        "command": "POST /api/v1/jobs/truth_materialization/start?dry_run=false&confirm_execute=true",
        "mutates_data": True,
        "requires_explicit_approval": True,
        "blocked_until": review_step,
    })
    if source_plan.get("approval_required") or int(source_summary.get("blocked_job_count") or 0) > 0:
        steps.append({
            "step": "review_source_refresh_plan",
            "command": "Inspect source_refresh.next_jobs, blocked_job_count, non_refreshable_gap_count, and source ages in the activation packet",
            "mutates_data": False,
            "blocked_until": "review_health_report",
        })
        steps.append({
            "step": "execute_approved_source_refresh_jobs",
            "command": "POST each approved /api/v1/jobs/{job_type}/start?dry_run=false&confirm_execute=true endpoint from source_refresh.next_jobs",
            "mutates_data": True,
            "requires_explicit_approval": True,
            "blocked_until": "review_source_refresh_plan",
        })
    return steps


def build_runtime_preflight_summary(schema_status: dict[str, Any]) -> dict[str, Any]:
    """Build an API-safe preflight summary without shelling out to Alembic."""
    ready_to_apply = (
        not schema_status.get("ready")
        and schema_status.get("current_revision") == "008_lead_lineage"
        and schema_status.get("expected_revision") == EXPECTED_TRUTH_ALEMBIC_REVISION
    )
    return {
        "dry_run": True,
        "mutations_planned": 0,
        "ready_to_apply_additive_truth_migration": ready_to_apply,
        "expected_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
        "schema_status": schema_status,
        "rollback_strategy": (
            "If the additive truth-confidence migration must be backed out before production use, "
            "run the generated Alembic downgrade from 009_truth_confidence_program to 008_lead_lineage. "
            "The downgrade drops only the additive truth program tables in dependency order."
        ),
        "offline_rollback_sql": {
            "command": "python -m alembic downgrade 009_truth_confidence_program:008_lead_lineage --sql",
        },
    }


def build_activation_packet(
    *,
    preflight: dict[str, Any],
    health_report: dict[str, Any],
) -> dict[str, Any]:
    """Return a compact activation decision packet from read-only inputs."""
    schema_status = preflight.get("schema_status") or {}
    summary = health_report.get("summary") or {}
    source_plan = health_report.get("source_refresh_plan") or {}
    source_plan_summary = source_plan.get("summary") or {}
    claim_readiness = _claim_readiness(summary)
    missing_tables = list(schema_status.get("missing_tables") or [])
    approval_steps = _approval_steps(health_report)
    business_use_allowed = _business_use_allowed(health_report)

    if business_use_allowed:
        verdict = "ready_for_business_use"
    elif preflight.get("ready_to_apply_additive_truth_migration"):
        verdict = "schema_approval_required"
    elif schema_status.get("ready"):
        verdict = "materialization_or_review_required"
    else:
        verdict = "blocked"

    return {
        "dry_run": True,
        "mutations_planned": 0,
        "verdict": verdict,
        "business_use_allowed": business_use_allowed,
        "trust_posture": summary.get("trust_posture", "not_ready"),
        "schema": {
            "ready": schema_status.get("ready", False),
            "current_revision": schema_status.get("current_revision"),
            "expected_revision": schema_status.get("expected_revision"),
            "missing_tables": missing_tables,
            "ready_to_apply_additive_truth_migration": preflight.get("ready_to_apply_additive_truth_migration", False),
        },
        "approval_required": bool(approval_steps),
        "approval_steps": approval_steps,
        "source_refresh": {
            "approval_required": source_plan.get("approval_required", False),
            "planned_job_count": source_plan_summary.get("planned_job_count", 0),
            "refreshable_job_count": source_plan_summary.get("refreshable_job_count", 0),
            "blocked_job_count": source_plan_summary.get("blocked_job_count", 0),
            "affected_source_count": source_plan_summary.get("affected_source_count", 0),
            "non_refreshable_gap_count": source_plan_summary.get("non_refreshable_gap_count", 0),
            "next_jobs": _source_refresh_jobs(source_plan),
        },
        "golden_benchmark": {
            "configured_cases": summary.get("configured_golden_cases", 0),
            "evaluable_cases": summary.get("evaluable_golden_cases", 0),
        },
        "claim_readiness": claim_readiness,
        "verification_frontier": _verification_frontier_summary(health_report),
        "trust_gap_summary": [
            {
                "severity": gap.get("severity"),
                "area": gap.get("area"),
                "message": gap.get("message"),
            }
            for gap in health_report.get("trust_gaps", [])[:10]
        ],
        "next_safe_steps": _next_safe_steps(
            schema_ready=bool(schema_status.get("ready")),
            ready_to_apply_schema=bool(preflight.get("ready_to_apply_additive_truth_migration")),
            source_plan=source_plan,
        ),
        "rollback": {
            "strategy": preflight.get("rollback_strategy"),
            "offline_rollback_command": (preflight.get("offline_rollback_sql") or {}).get("command"),
        },
    }
