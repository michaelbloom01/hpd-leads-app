"""Preview-first operator evidence capture for the truth claim ledger."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.confidence import CONFIDENCE_POLICY_VERSION, ConfidenceInput, compute_confidence, source_quality
from src.services.truth_materialization import (
    _load_before_snapshots_by_id,
    _load_existing_ids,
    _load_truth_table_counts,
    _manifest_summary,
    _upsert_confidence_snapshot,
    _upsert_materialization_manifest_entries,
    build_confidence_snapshots_from_specs,
    build_materialization_manifest_entries,
    build_materialization_rollback_plan,
    freshness_days,
)
from src.services.truth_program import stable_claim_id


ALLOWED_SUPPORT_STATUSES = {"supports", "contradicts"}
ALLOWED_MANUAL_SOURCE_NAMES = {
    "manual_evidence",
    "operator_review",
    "company_website",
    "google_places",
    "hpd_management_company",
    "ny_dos",
    "ny_dps_order_entry",
    "verizon_order_entry_petition",
    "justia",
    "mystatemls",
    "openigloo",
    "renthistory",
    "redfin",
    "homes",
    "renthop",
    "zillow",
    "outreach_confirmed",
    "hpm_revenue_by_property_summary",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_observed_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return _utc_now()


def _clean_required(value: Any, *, field: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise ValueError(f"{field} is required")
    return cleaned


def _default_source_record_id(payload: dict[str, Any], observed_at: datetime) -> str:
    return stable_claim_id(
        "manual_evidence_record",
        payload.get("subject_type"),
        payload.get("subject_id"),
        payload.get("predicate"),
        payload.get("object_type"),
        payload.get("object_id"),
        payload.get("normalized_value"),
        payload.get("support_status"),
        payload.get("source_url") or payload.get("note") or observed_at.isoformat(),
    )


def build_manual_evidence_claim_spec(payload: dict[str, Any], *, recorded_by: str | None = None) -> dict[str, Any]:
    """Build a stable claim/evidence spec from reviewed operator evidence."""
    subject_type = _clean_required(payload.get("subject_type"), field="subject_type")
    subject_id = _clean_required(payload.get("subject_id"), field="subject_id")
    predicate = _clean_required(payload.get("predicate"), field="predicate")
    object_type = _clean_required(payload.get("object_type"), field="object_type")
    object_id = _clean_required(payload.get("object_id"), field="object_id")
    claim_type = _clean_required(payload.get("claim_type"), field="claim_type")
    normalized_value = str(payload.get("normalized_value") or "").strip() or None
    extracted_value = str(payload.get("extracted_value") or "").strip() or normalized_value
    support_status = str(payload.get("support_status") or "supports").strip().lower()
    if support_status not in ALLOWED_SUPPORT_STATUSES:
        raise ValueError(f"support_status must be one of: {', '.join(sorted(ALLOWED_SUPPORT_STATUSES))}")
    source_name = str(payload.get("source_name") or "manual_evidence").strip()
    if source_name not in ALLOWED_MANUAL_SOURCE_NAMES:
        raise ValueError(f"source_name must be one of: {', '.join(sorted(ALLOWED_MANUAL_SOURCE_NAMES))}")
    source_type = str(payload.get("source_type") or "operator_review").strip()
    observed_at = _coerce_observed_at(payload.get("observed_at"))
    source_record_id = str(payload.get("source_record_id") or "").strip() or _default_source_record_id(
        {
            **payload,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "predicate": predicate,
            "object_type": object_type,
            "object_id": object_id,
            "normalized_value": normalized_value,
            "support_status": support_status,
        },
        observed_at,
    )
    age = freshness_days(observed_at)
    confidence = compute_confidence(ConfidenceInput(
        claim_type=claim_type,
        supporting_sources=[source_name] if support_status == "supports" else [],
        contradicting_sources=[source_name] if support_status == "contradicts" else [],
        freshness_days=age,
        source_agreement_count=1 if support_status == "supports" else 0,
        source_disagreement_count=1 if support_status == "contradicts" else 0,
    ))
    claim_id = stable_claim_id(
        "manual_evidence_claim",
        subject_type,
        subject_id,
        predicate,
        object_type,
        object_id,
        normalized_value,
        source_name,
        source_record_id,
        support_status,
    )
    evidence_id = stable_claim_id("manual_evidence", claim_id, source_name, source_record_id, support_status)
    note = str(payload.get("note") or "").strip() or None
    raw_payload = {
        "recorded_by": recorded_by,
        "note": note,
        "confidence_policy_version": CONFIDENCE_POLICY_VERSION,
        "manual_evidence": True,
    }
    raw_payload.update(payload.get("raw_payload") or {})
    return {
        "claim": {
            "claim_id": claim_id,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "predicate": predicate,
            "object_type": object_type,
            "object_id": object_id,
            "extracted_value": extracted_value,
            "normalized_value": normalized_value,
            "claim_type": claim_type,
            "freshness_days": age,
            "observed_at": observed_at,
            **confidence,
            "rationale": {
                **(confidence.get("rationale") or {}),
                "manual_evidence": True,
                "support_status": support_status,
                "source_record_id": source_record_id,
                "recorded_by": recorded_by,
                "note": note,
            },
        },
        "evidence": {
            "evidence_id": evidence_id,
            "claim_id": claim_id,
            "source_name": source_name,
            "source_type": source_type,
            "source_record_id": source_record_id,
            "source_url": str(payload.get("source_url") or "").strip() or None,
            "observed_at": observed_at,
            "extracted_value": extracted_value,
            "normalized_value": normalized_value,
            "support_status": support_status,
            "source_quality_score": source_quality(source_name),
            "evidence_weight": 1.0,
            "raw_payload": raw_payload,
        },
    }


async def _upsert_manual_evidence_claim(
    session: AsyncSession,
    *,
    spec: dict[str, Any],
    run_id: str,
) -> None:
    claim = spec["claim"]
    evidence = spec["evidence"]
    rationale = {
        **(claim.get("rationale") or {}),
        "manual_evidence_run_id": run_id,
    }
    raw_payload = {
        **(evidence.get("raw_payload") or {}),
        "manual_evidence_run_id": run_id,
    }
    await session.execute(
        text("""
            INSERT INTO truth_claims (
                claim_id, subject_type, subject_id, predicate, object_type, object_id,
                extracted_value, normalized_value, claim_type, belief_status, confidence_score,
                freshness_days, observed_at, current_flag, actionability_level, rationale,
                created_at, updated_at
            )
            VALUES (
                :claim_id, :subject_type, :subject_id, :predicate, :object_type, :object_id,
                :extracted_value, :normalized_value, :claim_type, :belief_status, :confidence_score,
                :freshness_days, :observed_at, true, :actionability_level, CAST(:rationale AS JSONB),
                NOW(), NOW()
            )
            ON CONFLICT (claim_id)
            DO UPDATE SET
                extracted_value = EXCLUDED.extracted_value,
                normalized_value = EXCLUDED.normalized_value,
                belief_status = EXCLUDED.belief_status,
                confidence_score = EXCLUDED.confidence_score,
                freshness_days = EXCLUDED.freshness_days,
                observed_at = EXCLUDED.observed_at,
                actionability_level = EXCLUDED.actionability_level,
                rationale = EXCLUDED.rationale,
                updated_at = NOW()
        """),
        {
            **claim,
            "rationale": json.dumps(rationale),
        },
    )
    await session.execute(
        text("""
            INSERT INTO truth_evidence (
                evidence_id, claim_id, source_name, source_type, source_record_id, source_url,
                observed_at, extracted_value, normalized_value, support_status,
                source_quality_score, evidence_weight, raw_payload, created_at, updated_at
            )
            VALUES (
                :evidence_id, :claim_id, :source_name, :source_type, :source_record_id, :source_url,
                :observed_at, :extracted_value, :normalized_value, :support_status,
                :source_quality_score, :evidence_weight, CAST(:raw_payload AS JSONB), NOW(), NOW()
            )
            ON CONFLICT (evidence_id)
            DO UPDATE SET
                source_url = EXCLUDED.source_url,
                observed_at = EXCLUDED.observed_at,
                extracted_value = EXCLUDED.extracted_value,
                normalized_value = EXCLUDED.normalized_value,
                support_status = EXCLUDED.support_status,
                source_quality_score = EXCLUDED.source_quality_score,
                evidence_weight = EXCLUDED.evidence_weight,
                raw_payload = EXCLUDED.raw_payload,
                updated_at = NOW()
        """),
        {
            **evidence,
            "raw_payload": json.dumps(raw_payload),
        },
    )


async def preview_or_record_manual_evidence(
    session: AsyncSession,
    *,
    payload: dict[str, Any],
    recorded_by: str | None,
    dry_run: bool = True,
    confirm_execute: bool = False,
    run_id: str | None = None,
) -> dict[str, Any]:
    spec = build_manual_evidence_claim_spec(payload, recorded_by=recorded_by)
    run_id = run_id or f"truth-manual-evidence-{_utc_now():%Y%m%d%H%M%S}"
    snapshots = build_confidence_snapshots_from_specs([spec], run_id=run_id)
    claim_ids = [spec["claim"]["claim_id"]]
    evidence_ids = [spec["evidence"]["evidence_id"]]
    snapshot_ids = [snapshot["snapshot_id"] for snapshot in snapshots]

    existing_claim_ids = await _load_existing_ids(
        session,
        table_name="truth_claims",
        id_column="claim_id",
        ids=claim_ids,
    )
    existing_evidence_ids = await _load_existing_ids(
        session,
        table_name="truth_evidence",
        id_column="evidence_id",
        ids=evidence_ids,
    )
    existing_snapshot_ids = await _load_existing_ids(
        session,
        table_name="confidence_snapshots",
        id_column="snapshot_id",
        ids=snapshot_ids,
    )
    claim_columns = [
        "claim_id",
        "extracted_value",
        "normalized_value",
        "belief_status",
        "confidence_score",
        "freshness_days",
        "observed_at",
        "actionability_level",
        "rationale",
        "updated_at",
    ]
    evidence_columns = [
        "evidence_id",
        "claim_id",
        "source_url",
        "observed_at",
        "extracted_value",
        "normalized_value",
        "support_status",
        "source_quality_score",
        "evidence_weight",
        "raw_payload",
        "updated_at",
    ]
    snapshot_columns = [
        "snapshot_id",
        "confidence_score",
        "actionability_level",
        "supporting_claim_count",
        "contradicting_claim_count",
        "stale_claim_count",
        "computed_at",
        "rationale",
        "updated_at",
    ]
    before_claims = await _load_before_snapshots_by_id(
        session,
        table_name="truth_claims",
        id_column="claim_id",
        columns=claim_columns,
        ids=existing_claim_ids,
    )
    before_evidence = await _load_before_snapshots_by_id(
        session,
        table_name="truth_evidence",
        id_column="evidence_id",
        columns=evidence_columns,
        ids=existing_evidence_ids,
    )
    before_snapshots = await _load_before_snapshots_by_id(
        session,
        table_name="confidence_snapshots",
        id_column="snapshot_id",
        columns=snapshot_columns,
        ids=existing_snapshot_ids,
    )
    rollback_plan = build_materialization_rollback_plan(
        run_id=run_id,
        claim_ids=claim_ids,
        evidence_ids=evidence_ids,
        snapshot_ids=snapshot_ids,
        existing_claim_ids=existing_claim_ids,
        existing_evidence_ids=existing_evidence_ids,
        existing_snapshot_ids=existing_snapshot_ids,
        before_snapshot_samples={
            "truth_claims": list(before_claims.values())[:25],
            "truth_evidence": list(before_evidence.values())[:25],
            "confidence_snapshots": list(before_snapshots.values())[:25],
        },
    )
    manifest_entries = [
        *build_materialization_manifest_entries(
            run_id=run_id,
            item_type="truth_claim",
            item_ids=claim_ids,
            existing_item_ids=existing_claim_ids,
            before_snapshots_by_id=before_claims,
        ),
        *build_materialization_manifest_entries(
            run_id=run_id,
            item_type="truth_evidence",
            item_ids=evidence_ids,
            existing_item_ids=existing_evidence_ids,
            before_snapshots_by_id=before_evidence,
        ),
        *build_materialization_manifest_entries(
            run_id=run_id,
            item_type="confidence_snapshot",
            item_ids=snapshot_ids,
            existing_item_ids=existing_snapshot_ids,
            before_snapshots_by_id=before_snapshots,
        ),
    ]
    planned_changes = [
        {
            "table": "truth_claims",
            "operation": "upsert",
            "id": claim_ids[0],
            "was_existing": claim_ids[0] in existing_claim_ids,
        },
        {
            "table": "truth_evidence",
            "operation": "upsert",
            "id": evidence_ids[0],
            "was_existing": evidence_ids[0] in existing_evidence_ids,
        },
        *[
            {
                "table": "confidence_snapshots",
                "operation": "upsert",
                "id": snapshot_id,
                "was_existing": snapshot_id in existing_snapshot_ids,
            }
            for snapshot_id in snapshot_ids
        ],
    ]
    base_result = {
        "run_type": "manual_evidence_capture",
        "run_id": run_id,
        "dry_run": bool(dry_run or not confirm_execute),
        "mutations_planned": len(planned_changes) if dry_run or not confirm_execute else 0,
        "allowed_execute": not dry_run and confirm_execute,
        "mutation_scope": {
            "allowed_tables": [
                "truth_materialization_manifest",
                "truth_claims",
                "truth_evidence",
                "confidence_snapshots",
            ],
            "forbidden_side_effects": {
                "will_mark_verified": False,
                "will_create_or_refresh_source_data": False,
                "will_materialize_building_management_relationships": False,
                "will_start_jobs": False,
                "will_allow_business_use": False,
            },
            "safe_action": (
                "Manual evidence capture may only upsert truth-ledger rows, confidence snapshots, "
                "and rollback manifest entries; relationship creation, source refresh, adjudication "
                "status changes, and business-use activation require separate approval-gated paths."
            ),
        },
        "claim_spec": {
            "claim_id": claim_ids[0],
            "evidence_id": evidence_ids[0],
            "subject_type": spec["claim"]["subject_type"],
            "subject_id": spec["claim"]["subject_id"],
            "predicate": spec["claim"]["predicate"],
            "object_type": spec["claim"]["object_type"],
            "object_id": spec["claim"]["object_id"],
            "normalized_value": spec["claim"]["normalized_value"],
            "claim_type": spec["claim"]["claim_type"],
            "belief_status": spec["claim"]["belief_status"],
            "confidence_score": spec["claim"]["confidence_score"],
            "freshness_days": spec["claim"]["freshness_days"],
            "actionability_level": spec["claim"]["actionability_level"],
            "source_name": spec["evidence"]["source_name"],
            "source_type": spec["evidence"]["source_type"],
            "support_status": spec["evidence"]["support_status"],
            "source_url": spec["evidence"].get("source_url"),
        },
        "proposed_database_changes": planned_changes,
        "rollback_plan": rollback_plan,
        "rollback_manifest": _manifest_summary(manifest_entries),
        "rollback_strategy": rollback_plan["rollback_strategy"],
        "required_execute_params": {
            "dry_run": False,
            "confirm_execute": True,
        },
    }
    if dry_run or not confirm_execute:
        base_result["blocked_reason"] = "Manual evidence capture defaults to preview; execute requires dry_run=false and confirm_execute=true."
        return base_result

    before_counts = await _load_truth_table_counts(session)
    await _upsert_materialization_manifest_entries(session, entries=manifest_entries)
    await _upsert_manual_evidence_claim(session, spec=spec, run_id=run_id)
    for snapshot in snapshots:
        await _upsert_confidence_snapshot(session, snapshot=snapshot)
    await session.commit()
    after_counts = await _load_truth_table_counts(session)
    return {
        **base_result,
        "dry_run": False,
        "mutations_planned": 0,
        "before_counts": before_counts,
        "after_counts": after_counts,
        "claims_upserted": 1,
        "evidence_upserted": 1,
        "confidence_snapshots_upserted": len(snapshots),
    }
