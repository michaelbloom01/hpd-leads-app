"""No-mutation health reporting for the Data Truth & Confidence program."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.confidence import ACTION_THRESHOLDS, CONFIDENCE_POLICY_VERSION
from src.services.golden_benchmark import evaluate_golden_cases, load_golden_benchmark
from src.services.source_audit import load_source_audit
from src.services.truth_adjudication import load_claim_adjudication_preview
from src.services.truth_materialization import materialize_truth_claims
from src.services.truth_program import GOLDEN_CASE_SEEDS, preview_adversarial_validation

DEFAULT_TRUST_THRESHOLDS: dict[str, int | float] = {
    "minimum_claim_count": 1,
    "maximum_conflicting_claim_ratio": 0.05,
    "maximum_open_review_ratio": 0.25,
    "minimum_configured_golden_cases": 4,
    "minimum_evaluable_golden_cases": 1,
}
EXPECTED_TRUTH_ALEMBIC_REVISION = "010_truth_manifest"
# These additive descendants retain the required truth schema. Extend only after
# reviewing the migration lineage and asserting it in test_truth_schema_lineage.
VERIFIED_TRUTH_ALEMBIC_DESCENDANTS = frozenset({
    "011_building_identity",
    "012_compliance",
    "013_contact_region_text",
    "014_compliance_reviews",
})
REQUIRED_TRUTH_TABLES = [
    "truth_claims",
    "truth_evidence",
    "truth_review_items",
    "confidence_snapshots",
    "golden_verification_cases",
    "truth_validation_runs",
    "truth_materialization_manifest",
]


def is_truth_schema_current(schema_status: dict[str, Any] | None) -> bool:
    return bool(schema_status and schema_status.get("ready") and schema_status.get("migration_current"))


def truth_revision_includes_expected(revision: str | None) -> bool:
    """Recognize the required migration and explicitly verified descendants."""
    return revision == EXPECTED_TRUTH_ALEMBIC_REVISION or revision in VERIFIED_TRUTH_ALEMBIC_DESCENDANTS


ACTIONABILITY_MEANINGS = {
    "broad_discovery": "Visible for exploratory search only; do not rely on it for outreach decisions.",
    "ranked_sourcing": "Can rank and compare opportunities, but should still be checked before outreach.",
    "automated_enrichment": "Safe enough for non-destructive automated enrichment or source refresh, but not for outreach.",
    "recommended_outreach": "Safe enough to use in a human-reviewed outreach queue.",
    "acquisition_quality_diligence": "Strong enough to ground diligence or investment-style judgment.",
}
ACTIONABILITY_DISPLAY_ORDER = [
    "broad_discovery",
    "ranked_sourcing",
    "automated_enrichment",
    "recommended_outreach",
    "acquisition_quality_diligence",
]


def _actionability_minimum_text(rule) -> str:
    contradiction_text = (
        "no contradictions"
        if rule.max_contradictions == 0
        else f"at most {rule.max_contradictions} contradiction{'s' if rule.max_contradictions != 1 else ''}"
    )
    return (
        f"confidence >= {rule.minimum_score:.2f}, "
        f"at least {rule.min_supporting_sources} supporting source{'s' if rule.min_supporting_sources != 1 else ''} "
        f"and {rule.min_supporting_evidence} evidence item{'s' if rule.min_supporting_evidence != 1 else ''}, "
        f"{contradiction_text}, evidence fresher than {rule.max_freshness_days} days"
    )


def build_actionability_rules() -> list[dict[str, Any]]:
    rules_by_level = {rule.level: rule for rule in ACTION_THRESHOLDS}
    rules: list[dict[str, Any]] = []
    for level in ACTIONABILITY_DISPLAY_ORDER:
        rule = rules_by_level[level]
        rules.append({
            "level": rule.level,
            "meaning": ACTIONABILITY_MEANINGS[rule.level],
            "minimum": _actionability_minimum_text(rule),
            "confidence_policy_version": CONFIDENCE_POLICY_VERSION,
            "minimum_score": rule.minimum_score,
            "max_contradictions": rule.max_contradictions,
            "max_freshness_days": rule.max_freshness_days,
            "min_supporting_sources": rule.min_supporting_sources,
            "min_supporting_evidence": rule.min_supporting_evidence,
        })
    rules.append({
        "level": "do_not_act",
        "meaning": "Too stale, weak, or contradicted for automated action.",
        "minimum": "below the minimum confidence/freshness/evidence thresholds or blocked by contradictions",
        "confidence_policy_version": CONFIDENCE_POLICY_VERSION,
        "minimum_score": None,
        "max_contradictions": None,
        "max_freshness_days": None,
        "min_supporting_sources": None,
        "min_supporting_evidence": None,
    })
    return rules


ACTIONABILITY_RULES: list[dict[str, Any]] = build_actionability_rules()


def _activation_step(
    step: str,
    status: str,
    *,
    reason: str,
    approval_required: bool = False,
    mutations_planned: int = 0,
) -> dict[str, Any]:
    return {
        "step": step,
        "status": status,
        "reason": reason,
        "approval_required": approval_required,
        "mutations_planned": mutations_planned,
    }


def build_activation_checklist(
    *,
    schema_status: dict[str, Any] | None,
    summary: dict[str, Any],
    source_refresh_plan: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    schema_current = is_truth_schema_current(schema_status)
    missing_truth_tables = list((schema_status or {}).get("missing_tables") or REQUIRED_TRUTH_TABLES)
    truth_tables_ready = bool((schema_status or {}).get("truth_tables_ready") or ((schema_status or {}).get("ready") and not missing_truth_tables))
    schema_step_reason = "Truth-confidence tables are present at the expected migration."
    schema_mutations_planned = 0
    if not schema_current and truth_tables_ready:
        schema_step_reason = "Truth-confidence tables exist, but Alembic revision differs from the expected truth-confidence migration; inspect migration lineage before ledger previews or execution."
    elif not schema_current:
        schema_step_reason = "Apply additive migration 009_truth_confidence_program before claim-ledger materialization or review execution."
        schema_mutations_planned = len(missing_truth_tables)
    trust_posture = str(summary.get("trust_posture") or "not_ready")
    claim_count = int(summary.get("claim_count") or 0)
    verified_claim_count = int(summary.get("verified_claim_count") or 0)
    planned_claims = int(summary.get("planned_claims_total") or 0)
    validation_checks = int(summary.get("validation_check_count") or 0)
    critical_or_high = int(summary.get("critical_or_high_gap_count") or 0)
    refresh_summary = (source_refresh_plan or {}).get("summary") or {}
    refreshable_jobs = int(refresh_summary.get("refreshable_job_count") or 0)
    blocked_jobs = int(refresh_summary.get("blocked_job_count") or 0)
    non_refreshable_gaps = int(refresh_summary.get("non_refreshable_gap_count") or 0)
    refreshable_label = "job" if refreshable_jobs == 1 else "jobs"
    manual_label = "evidence stream" if non_refreshable_gaps == 1 else "evidence streams"
    manual_verb = "is" if non_refreshable_gaps == 1 else "are"
    source_gaps_clear = refreshable_jobs == 0 and blocked_jobs == 0 and non_refreshable_gaps == 0
    claim_readiness_clear = claim_count > 0 and verified_claim_count > 0
    allow_business_use_complete = (
        trust_posture != "not_ready"
        and critical_or_high == 0
        and claim_readiness_clear
        and source_gaps_clear
    )
    if allow_business_use_complete:
        allow_business_use_reason = "Truth posture, claim readiness, source freshness, and review gates are acceptable for monitored use."
    elif not claim_readiness_clear:
        allow_business_use_reason = "Do not use for sourcing, diligence, or outreach decisions until the ledger has materialized and verified claims."
    elif not source_gaps_clear:
        allow_business_use_reason = "Do not use for sourcing, diligence, or outreach decisions until refreshable, blocked, and manually tracked source gaps are cleared."
    else:
        allow_business_use_reason = "Do not use for sourcing, diligence, or outreach decisions until schema, claims, sources, reviews, and benchmarks clear the trust gates."

    checklist = [
        _activation_step(
            "apply_truth_schema",
            "complete" if schema_current else "approval_required",
            reason=schema_step_reason,
            approval_required=not schema_current,
            mutations_planned=schema_mutations_planned,
        ),
        _activation_step(
            "run_materialization_dry_run",
            "complete" if schema_current and claim_count > 0 and planned_claims == 0 else "ready" if schema_current else "blocked",
            reason=(
                "No pending claim materialization is reported; current ledger claims are available for review."
                if schema_current and claim_count > 0 and planned_claims == 0
                else "Preview the stable claim/evidence/snapshot IDs and samples before executing any upsert."
                if schema_current
                else "Blocked until the truth-confidence schema exists at the expected migration."
            ),
            approval_required=False,
            mutations_planned=0,
        ),
        _activation_step(
            "review_truth_outputs",
            "needs_review" if schema_current and (planned_claims > 0 or validation_checks > 0 or critical_or_high > 0) else "blocked" if not schema_current else "complete",
            reason=(
                "Inspect claim samples, contradictions, validation checks, review queues, and golden benchmark output before execution."
                if schema_current
                else "Blocked until materialization and validation previews can run against the expected truth schema."
            ),
            approval_required=False,
            mutations_planned=0,
        ),
        _activation_step(
            "execute_truth_materialization",
            "complete" if schema_current and claim_count > 0 and planned_claims == 0 else "approval_required" if schema_current and planned_claims > 0 else "blocked",
            reason=(
                "No pending claim materialization is reported; ledger execution is not currently required."
                if schema_current and claim_count > 0 and planned_claims == 0
                else "Execute only after dry-run review; stable-ID upserts must keep the rollback plan with new vs updated rows."
                if schema_current and planned_claims > 0
                else "Blocked until materialization preview identifies claims to execute or the existing ledger has reviewable claims."
                if schema_current
                else "Blocked until schema migration is current and dry-run review is complete."
            ),
            approval_required=schema_current and planned_claims > 0,
            mutations_planned=planned_claims,
        ),
        _activation_step(
            "refresh_or_record_sources",
            "approval_required" if refreshable_jobs > 0 else "manual_review" if non_refreshable_gaps > 0 else "complete",
            reason=(
                f"{refreshable_jobs} refreshable source {refreshable_label} require explicit approval; {non_refreshable_gaps} {manual_label} {manual_verb} tracked manually."
                if refreshable_jobs > 0 or non_refreshable_gaps > 0
                else "No refreshable or manually tracked source gaps are currently reported."
            ),
            approval_required=refreshable_jobs > 0,
            mutations_planned=refreshable_jobs,
        ),
        _activation_step(
            "allow_business_use",
            "complete" if allow_business_use_complete else "blocked",
            reason=allow_business_use_reason,
            approval_required=False,
            mutations_planned=0,
        ),
    ]
    return checklist


async def load_truth_schema_status(session: AsyncSession) -> dict[str, Any]:
    table_status: dict[str, bool] = {}
    for table_name in REQUIRED_TRUTH_TABLES:
        row = (await session.execute(
            text("SELECT to_regclass(:table_name) IS NOT NULL AS exists"),
            {"table_name": table_name},
        )).first()
        table_status[table_name] = bool(row.exists) if row else False

    alembic_table_row = (await session.execute(
        text("SELECT to_regclass('alembic_version') IS NOT NULL AS exists")
    )).first()
    alembic_table_exists = bool(alembic_table_row.exists) if alembic_table_row else False
    current_revisions: list[str] = []
    if alembic_table_exists:
        revision_rows = await session.execute(
            text("SELECT version_num FROM alembic_version ORDER BY version_num")
        )
        current_revisions = [str(row.version_num) for row in revision_rows if row.version_num]
    # A branch/multiple-head state needs lineage review instead of selecting one
    # lexicographically and silently ignoring an unverified migration branch.
    current_revision = current_revisions[0] if len(current_revisions) == 1 else None

    missing_tables = [table_name for table_name, exists in table_status.items() if not exists]
    truth_tables_ready = not missing_tables
    expected_revision_applied = truth_revision_includes_expected(current_revision)
    if current_revision == EXPECTED_TRUTH_ALEMBIC_REVISION:
        revision_status = "expected"
    elif expected_revision_applied:
        revision_status = "verified_descendant"
    elif len(current_revisions) > 1:
        revision_status = "multiple_heads_require_review"
    elif truth_tables_ready:
        revision_status = "schema_present_revision_differs"
    else:
        revision_status = "schema_missing"
    return {
        "ready": truth_tables_ready,
        "expected_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
        "current_revision": current_revision,
        "current_revisions": current_revisions,
        "migration_current": expected_revision_applied,
        "expected_revision_applied": expected_revision_applied,
        "truth_tables_ready": truth_tables_ready,
        "revision_status": revision_status,
        "alembic_table_exists": alembic_table_exists,
        "required_tables": table_status,
        "missing_tables": missing_tables,
        "mutations_planned": 0,
    }


async def load_truth_dashboard(session: AsyncSession) -> dict[str, Any]:
    counts_row = (await session.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM truth_claims) AS claim_count,
            (SELECT COUNT(*) FROM truth_claims WHERE belief_status = 'verified') AS verified_claim_count,
            (SELECT COUNT(*) FROM truth_claims WHERE belief_status = 'conflicting') AS conflicting_claim_count,
            (SELECT COUNT(*) FROM truth_claims WHERE actionability_level = 'recommended_outreach') AS recommended_outreach_claim_count,
            (SELECT COUNT(*) FROM truth_review_items WHERE status = 'open') AS open_review_count,
            (SELECT COUNT(*) FROM golden_verification_cases WHERE active = true) AS active_golden_case_count,
            (SELECT COUNT(*) FROM confidence_snapshots) AS confidence_snapshot_count
    """))).first()
    counts = dict(counts_row._mapping) if counts_row else {}

    confidence_rows = await session.execute(text("""
        SELECT actionability_level, COUNT(*)::int AS cnt
        FROM truth_claims
        GROUP BY actionability_level
        ORDER BY actionability_level
    """))
    review_rows = await session.execute(text("""
        SELECT queue_name, COUNT(*)::int AS cnt
        FROM truth_review_items
        WHERE status = 'open'
        GROUP BY queue_name
        ORDER BY queue_name
    """))
    claim_type_rows = await session.execute(text("""
        SELECT claim_type, COUNT(*)::int AS cnt
        FROM truth_claims
        GROUP BY claim_type
        ORDER BY cnt DESC, claim_type ASC
        LIMIT 20
    """))
    return {
        "claim_count": int(counts.get("claim_count") or 0),
        "verified_claim_count": int(counts.get("verified_claim_count") or 0),
        "conflicting_claim_count": int(counts.get("conflicting_claim_count") or 0),
        "recommended_outreach_claim_count": int(counts.get("recommended_outreach_claim_count") or 0),
        "open_review_count": int(counts.get("open_review_count") or 0),
        "active_golden_case_count": int(counts.get("active_golden_case_count") or 0),
        "confidence_snapshot_count": int(counts.get("confidence_snapshot_count") or 0),
        "actionability_distribution": {str(row.actionability_level or "none"): int(row.cnt or 0) for row in confidence_rows},
        "review_queue_distribution": {str(row.queue_name): int(row.cnt or 0) for row in review_rows},
        "claim_type_distribution": {str(row.claim_type): int(row.cnt or 0) for row in claim_type_rows},
        "actionability_rules": ACTIONABILITY_RULES,
    }


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _gap(severity: str, area: str, message: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "severity": severity,
        "area": area,
        "message": message,
        "evidence": evidence,
    }


def _source_audit_gap(source_audit: dict[str, Any] | None) -> dict[str, Any] | None:
    source_summary = (source_audit or {}).get("summary") or {}
    source_critical_gaps = list((source_audit or {}).get("critical_gaps") or [])
    if not source_critical_gaps:
        return None
    return _gap(
        "high",
        "source_audit",
        "Some configured public-data sources are missing schema, stale, have no recent ingest, or are not runnable from the job system.",
        {
            "summary": source_summary,
            "sample_gaps": source_critical_gaps[:5],
        },
    )


def evaluate_truth_health_outputs(
    *,
    dashboard: dict[str, Any],
    materialization_preview: dict[str, Any],
    validation_preview: dict[str, Any],
    golden_benchmark: dict[str, Any],
    source_audit: dict[str, Any] | None = None,
    adjudication_preview: dict[str, Any] | None = None,
    schema_status: dict[str, Any] | None = None,
    thresholds: dict[str, int | float] | None = None,
) -> dict[str, Any]:
    limits = {**DEFAULT_TRUST_THRESHOLDS, **(thresholds or {})}
    claim_count = int(dashboard.get("claim_count") or 0)
    conflicting_claims = int(dashboard.get("conflicting_claim_count") or 0)
    open_reviews = int(dashboard.get("open_review_count") or 0)
    planned_claims = int(materialization_preview.get("planned_claims_total") or 0)
    configured_golden_cases = int(golden_benchmark.get("configured_cases") or 0)
    evaluable_golden_cases = int(golden_benchmark.get("evaluable_cases") or 0)

    gaps: list[dict[str, Any]] = []
    if schema_status is not None and not bool(schema_status.get("migration_current")):
        gaps.append(_gap(
            "high",
            "schema_revision",
            "Truth-confidence tables are present, but the Alembic revision does not match the expected truth-confidence migration.",
            {
                "expected_revision": schema_status.get("expected_revision"),
                "current_revision": schema_status.get("current_revision"),
                "revision_status": schema_status.get("revision_status"),
            },
        ))

    if claim_count < int(limits["minimum_claim_count"]):
        gaps.append(_gap(
            "critical",
            "claim_ledger",
            "Truth ledger has too few materialized claims to support business decisions.",
            {"claim_count": claim_count, "minimum": limits["minimum_claim_count"]},
        ))

    if planned_claims > 0:
        gaps.append(_gap(
            "high" if claim_count == 0 else "medium",
            "claim_materialization",
            "Dry-run materialization found operational evidence that is not yet represented as reviewable truth claims.",
            {
                "planned_claims_total": planned_claims,
                "planned_claims_by_source": materialization_preview.get("planned_claims_by_source", {}),
            },
        ))

    conflict_ratio = _ratio(conflicting_claims, claim_count)
    if conflict_ratio > float(limits["maximum_conflicting_claim_ratio"]):
        gaps.append(_gap(
            "high",
            "claim_conflicts",
            "Conflicting claims exceed the trust threshold for confident outreach or diligence.",
            {
                "conflicting_claim_count": conflicting_claims,
                "claim_count": claim_count,
                "ratio": conflict_ratio,
                "maximum": limits["maximum_conflicting_claim_ratio"],
            },
        ))

    review_ratio = _ratio(open_reviews, claim_count)
    if open_reviews > 0 and review_ratio > float(limits["maximum_open_review_ratio"]):
        gaps.append(_gap(
            "medium",
            "human_review",
            "Open review queue is large relative to the materialized claim base.",
            {
                "open_review_count": open_reviews,
                "claim_count": claim_count,
                "ratio": review_ratio,
                "maximum": limits["maximum_open_review_ratio"],
            },
        ))

    verified_claims = int(dashboard.get("verified_claim_count") or 0)
    adjudication_candidate_count = int((adjudication_preview or {}).get("verification_candidate_count") or 0)
    if claim_count > 0 and verified_claims == 0 and adjudication_preview is not None:
        gaps.append(_gap(
            "medium",
            "claim_adjudication",
            "Materialized claims exist, but no claim has yet been verified through the adjudication policy.",
            {
                "claim_count": claim_count,
                "verified_claim_count": verified_claims,
                "verification_candidate_count": adjudication_candidate_count,
                "top_blockers": (adjudication_preview.get("blocker_counts") or {}),
                "source_coverage": (adjudication_preview.get("source_coverage") or {}),
            },
        ))

    validation_checks = list(validation_preview.get("checks") or [])
    for check in validation_checks:
        severity = str(check.get("severity") or "medium")
        gaps.append(_gap(
            "critical" if severity == "critical" else "high" if severity == "high" else "medium",
            f"adversarial_validation:{check.get('check')}",
            str(check.get("why_it_matters") or "Validation preview found records that need review."),
            {
                "count_sampled": check.get("count_sampled", 0),
                "recommended_queue": check.get("recommended_queue"),
                "severity": severity,
            },
        ))

    if configured_golden_cases < int(limits["minimum_configured_golden_cases"]):
        gaps.append(_gap(
            "high",
            "golden_set",
            "Golden verification cases are not configured deeply enough to benchmark false merges, contact accuracy, and freshness.",
            {
                "configured_cases": configured_golden_cases,
                "minimum": limits["minimum_configured_golden_cases"],
                "seeded": golden_benchmark.get("seeded"),
            },
        ))

    if evaluable_golden_cases < int(limits["minimum_evaluable_golden_cases"]):
        gaps.append(_gap(
            "high",
            "golden_evaluation",
            "Golden verification cases are not yet tied to live subjects and claims, so benchmark metrics cannot validate real data quality.",
            {
                "evaluable_cases": evaluable_golden_cases,
                "minimum": limits["minimum_evaluable_golden_cases"],
                "configured_cases": configured_golden_cases,
            },
        ))

    feature_coverage = golden_benchmark.get("feature_coverage") or {}
    missing_features = list(feature_coverage.get("missing_required_features") or [])
    if missing_features:
        gaps.append(_gap(
            "medium",
            "golden_feature_coverage",
            "Golden verification cases do not yet cover every required hard-case feature.",
            {
                "missing_required_features": missing_features,
                "coverage": feature_coverage.get("coverage"),
            },
        ))

    null_metrics = sorted(key for key, value in (golden_benchmark.get("metrics") or {}).items() if value is None)
    if null_metrics:
        gaps.append(_gap(
            "medium",
            "golden_metrics",
            "Some golden-set metrics are not exercised by configured cases yet.",
            {"null_metrics": null_metrics},
        ))

    source_gap = _source_audit_gap(source_audit)
    if source_gap:
        gaps.append(source_gap)

    critical_or_high = sum(1 for gap in gaps if gap["severity"] in {"critical", "high"})

    source_refresh_plan = (source_audit or {}).get("refresh_plan")
    summary = {
        "claim_count": claim_count,
        "verified_claim_count": verified_claims,
        "verification_candidate_count": adjudication_candidate_count,
        "conflicting_claim_count": conflicting_claims,
        "conflicting_claim_ratio": conflict_ratio,
        "open_review_count": open_reviews,
        "open_review_ratio": review_ratio,
        "planned_claims_total": planned_claims,
        "validation_check_count": len(validation_checks),
        "configured_golden_cases": configured_golden_cases,
        "evaluable_golden_cases": evaluable_golden_cases,
        "critical_or_high_gap_count": critical_or_high,
        "trust_posture": "not_ready" if critical_or_high else "monitor",
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": True,
        "mutations_planned": 0,
        "thresholds": limits,
        "summary": summary,
        "trust_gaps": gaps,
        "activation_checklist": build_activation_checklist(
            schema_status=schema_status or {"ready": True, "migration_current": True},
            summary=summary,
            source_refresh_plan=source_refresh_plan,
        ),
        "schema_status": schema_status,
        "dashboard": dashboard,
        "materialization_preview": materialization_preview,
        "validation_preview": validation_preview,
        "golden_benchmark": golden_benchmark,
        "adjudication_preview": adjudication_preview,
        "source_audit": source_audit,
        "source_refresh_plan": source_refresh_plan,
        "rollback_strategy": "Report is read-only and calls preview services only; no database changes are planned.",
}


def build_schema_readiness_report(
    *,
    schema_status: dict[str, Any] | None,
    thresholds: dict[str, int | float] | None = None,
    error: SQLAlchemyError | None = None,
    source_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "expected_tables": REQUIRED_TRUTH_TABLES,
        "expected_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
    }
    if schema_status is not None:
        evidence.update({
            "current_revision": schema_status.get("current_revision"),
            "missing_tables": schema_status.get("missing_tables", []),
            "migration_current": schema_status.get("migration_current"),
            "expected_revision_applied": schema_status.get("expected_revision_applied"),
            "truth_tables_ready": schema_status.get("truth_tables_ready"),
            "revision_status": schema_status.get("revision_status"),
            "alembic_table_exists": schema_status.get("alembic_table_exists"),
        })
    if error is not None:
        evidence.update({
            "error_type": error.__class__.__name__,
            "error": str(error).splitlines()[0],
        })

    revision_drift = bool(schema_status and schema_status.get("truth_tables_ready") and not schema_status.get("migration_current"))
    gaps = [_gap(
        "critical" if not revision_drift else "high",
        "schema_revision" if revision_drift else "schema_readiness",
        "Truth-confidence tables are present, but the Alembic revision does not match the expected truth-confidence migration."
        if revision_drift
        else "Truth health report could not run because the database schema is missing required truth-confidence tables or columns.",
        evidence,
    )]
    source_gap = _source_audit_gap(source_audit)
    if source_gap:
        gaps.append(source_gap)
    critical_or_high = sum(1 for gap in gaps if gap["severity"] in {"critical", "high"})
    seed_golden_benchmark = evaluate_golden_cases(GOLDEN_CASE_SEEDS, {}, seeded=False)

    source_refresh_plan = (source_audit or {}).get("refresh_plan")
    summary = {
        "claim_count": 0,
        "verified_claim_count": 0,
        "conflicting_claim_count": 0,
        "conflicting_claim_ratio": 0.0,
        "open_review_count": 0,
        "open_review_ratio": 0.0,
        "planned_claims_total": 0,
        "validation_check_count": 0,
        "configured_golden_cases": int(seed_golden_benchmark.get("configured_cases") or 0),
        "evaluable_golden_cases": int(seed_golden_benchmark.get("evaluable_cases") or 0),
        "critical_or_high_gap_count": critical_or_high,
        "trust_posture": "not_ready",
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": True,
        "mutations_planned": 0,
        "thresholds": {**DEFAULT_TRUST_THRESHOLDS, **(thresholds or {})},
        "summary": summary,
        "trust_gaps": gaps,
        "activation_checklist": build_activation_checklist(
            schema_status=schema_status,
            summary=summary,
            source_refresh_plan=source_refresh_plan,
        ),
        "schema_status": schema_status,
        "dashboard": None,
        "materialization_preview": None,
        "validation_preview": None,
        "golden_benchmark": seed_golden_benchmark,
        "source_audit": source_audit,
        "source_refresh_plan": source_refresh_plan,
        "rollback_strategy": "Report is read-only. Apply the additive truth-confidence migration only after explicit approval.",
    }


async def build_truth_health_report(
    session: AsyncSession,
    *,
    materialization_limit: int = 500,
    validation_sample_limit: int = 20,
    thresholds: dict[str, int | float] | None = None,
) -> dict[str, Any]:
    try:
        source_audit = await load_source_audit(session)
        schema_status = await load_truth_schema_status(session)
        if not is_truth_schema_current(schema_status):
            return build_schema_readiness_report(schema_status=schema_status, thresholds=thresholds, source_audit=source_audit)
        dashboard = await load_truth_dashboard(session)
        materialization_preview = await materialize_truth_claims(
            session,
            limit=materialization_limit,
            dry_run=True,
            confirm_execute=False,
        )
        validation_preview = await preview_adversarial_validation(session, sample_limit=validation_sample_limit)
        golden_benchmark = await load_golden_benchmark(session)
        adjudication_preview = await load_claim_adjudication_preview(session, limit=200, include_samples=False)
    except SQLAlchemyError as exc:
        await session.rollback()
        return build_schema_readiness_report(schema_status=None, thresholds=thresholds, error=exc)
    report = evaluate_truth_health_outputs(
        dashboard=dashboard,
        materialization_preview=materialization_preview,
        validation_preview=validation_preview,
        golden_benchmark=golden_benchmark,
        source_audit=source_audit,
        adjudication_preview=adjudication_preview,
        schema_status=schema_status,
        thresholds=thresholds,
    )
    return report
