"""Human review decision helpers for truth/confidence queues."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


DECISION_STATUS = {
    "approve": "approved",
    "reject": "rejected",
    "needs_more_evidence": "needs_more_evidence",
    "do_not_merge": "do_not_merge",
}
APPROVAL_BLOCKED_QUEUES = {"conflicting_evidence", "insufficient_evidence", "do_not_merge"}


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _json_safe(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def build_review_decision_preview(
    *,
    item: dict[str, Any],
    decision: str,
    reviewer_email: str | None,
    note: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    if decision not in DECISION_STATUS:
        raise ValueError(f"Unsupported review decision: {decision}")

    source = item.get("source") or "truth_review_items"
    target_status = DECISION_STATUS[decision]
    blocked_reason = None
    allowed_execute = source == "truth_review_items"
    if source == "canonical_entity_match_proposals":
        allowed_execute = False
        blocked_reason = "Canonical merge proposals are preview-only here; merge/reject execution must use the dedicated canonical review workflow."
    elif decision == "approve":
        queue_name = str(item.get("queue_name") or "").strip()
        actionability_level = str(item.get("actionability_level") or "").strip()
        if queue_name in APPROVAL_BLOCKED_QUEUES:
            allowed_execute = False
            blocked_reason = f"Review item is in {queue_name}; choose reject, needs_more_evidence, or do_not_merge instead of approve."
        elif actionability_level == "do_not_act":
            allowed_execute = False
            blocked_reason = "Review item actionability is do_not_act; approval would overstate the safe next action."

    proposed_database_changes = []
    previous_state = {
        "status": item.get("status"),
        "reviewed_by": item.get("reviewed_by"),
        "reviewed_at": item.get("reviewed_at"),
        "updated_at": item.get("updated_at"),
    }
    resulting_state = {
        "status": target_status,
        "reviewed_by": reviewer_email,
        "reviewed_at": "now()",
    }
    decision_payload = {
        "last_review_decision": decision,
        "last_review_note": note,
        "last_reviewed_by": reviewer_email,
        "previous_state": _json_safe(previous_state),
        "resulting_state": _json_safe(resulting_state),
    }
    if source == "truth_review_items":
        proposed_database_changes.append({
            "table": "truth_review_items",
            "operation": "update",
            "where": {"review_id": item.get("review_id")},
            "set": {
                "status": target_status,
                "reviewed_by": reviewer_email,
                "reviewed_at": "now()",
            },
            "jsonb_append": {"rationale": decision_payload},
        })
    else:
        proposed_database_changes.append({
            "table": "canonical_entity_match_proposals",
            "operation": "blocked_preview_only",
            "where": {"proposal_review_id": item.get("review_id")},
            "reason": blocked_reason,
        })

    return {
        "dry_run": dry_run,
        "review_id": item.get("review_id"),
        "decision": decision,
        "current_status": item.get("status"),
        "target_status": target_status,
        "queue_name": item.get("queue_name"),
        "subject_type": item.get("subject_type"),
        "subject_id": item.get("subject_id"),
        "allowed_execute": allowed_execute,
        "blocked_reason": blocked_reason,
        "reviewer_email": reviewer_email,
        "note": note,
        "previous_state": previous_state,
        "resulting_state": resulting_state,
        "decision_payload": decision_payload,
        "proposed_database_changes": proposed_database_changes,
        "supporting_evidence": item.get("supporting_evidence") or {},
        "contradicting_evidence": item.get("contradicting_evidence") or {},
        "rationale": item.get("rationale") or {},
        "rollback_strategy": "For truth_review_items decisions, restore the prior status/review metadata from audit logs or database backup. This endpoint does not mutate leads, buildings, claims, or canonical links.",
    }


async def load_review_decision_item(session: AsyncSession, review_id: str) -> dict[str, Any] | None:
    row = (await session.execute(
        text("""
            SELECT
                review_id,
                queue_name,
                subject_type,
                subject_id,
                status,
                priority,
                confidence_score,
                actionability_level,
                proposed_change,
                supporting_evidence,
                contradicting_evidence,
                rationale,
                run_id,
                reviewed_by,
                reviewed_at,
                updated_at
            FROM truth_review_items
            WHERE review_id = :review_id
        """),
        {"review_id": review_id},
    )).first()
    if row:
        payload = dict(row._mapping)
        payload["source"] = "truth_review_items"
        for field in ("proposed_change", "supporting_evidence", "contradicting_evidence", "rationale"):
            payload[field] = _parse_json_object(payload.get(field))
        return payload

    match = re.fullmatch(r"canonical-proposal-(\d+)", review_id)
    if not match:
        return None
    proposal_id = int(match.group(1))
    proposal = (await session.execute(
        text("""
            SELECT
                CONCAT('canonical-proposal-', id) AS review_id,
                CASE
                    WHEN bucket = 'review_required' THEN 'needs_human_review'
                    WHEN safe_to_execute = true THEN 'safe_auto_accept'
                    ELSE bucket
                END AS queue_name,
                'canonical_entity' AS subject_type,
                canonical_entity_id AS subject_id,
                proposal_status AS status,
                CASE WHEN bucket = 'review_required' THEN 90 WHEN safe_to_execute = true THEN 60 ELSE 50 END AS priority,
                NULL::DOUBLE PRECISION AS confidence_score,
                CASE WHEN safe_to_execute = true THEN 'ranked_sourcing' ELSE 'do_not_act' END AS actionability_level,
                evidence AS proposed_change,
                evidence AS supporting_evidence,
                reasons AS contradicting_evidence,
                reasons AS rationale,
                NULL::VARCHAR AS run_id,
                updated_at
            FROM canonical_entity_match_proposals
            WHERE id = :proposal_id
        """),
        {"proposal_id": proposal_id},
    )).first()
    if not proposal:
        return None
    payload = dict(proposal._mapping)
    payload["source"] = "canonical_entity_match_proposals"
    for field in ("proposed_change", "supporting_evidence", "contradicting_evidence", "rationale"):
        payload[field] = _parse_json_object(payload.get(field))
    return payload


async def apply_review_decision(
    session: AsyncSession,
    *,
    review_id: str,
    decision: str,
    reviewer_email: str | None,
    note: str | None = None,
    dry_run: bool = True,
    confirm_execute: bool = False,
) -> dict[str, Any]:
    item = await load_review_decision_item(session, review_id)
    if not item:
        raise HTTPException(status_code=404, detail="Review item not found")

    try:
        preview = build_review_decision_preview(
            item=item,
            decision=decision,
            reviewer_email=reviewer_email,
            note=note,
            dry_run=dry_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if dry_run:
        return preview
    if not confirm_execute:
        raise HTTPException(status_code=400, detail="Review decision execution requires confirm_execute=true")
    if not preview["allowed_execute"]:
        raise HTTPException(status_code=409, detail=preview["blocked_reason"])

    await session.execute(
        text("""
            UPDATE truth_review_items
            SET status = :status,
                reviewed_by = :reviewed_by,
                reviewed_at = :reviewed_at,
                rationale = COALESCE(rationale, '{}'::jsonb) || CAST(:decision_payload AS JSONB),
                updated_at = NOW()
            WHERE review_id = :review_id
        """),
        {
            "review_id": review_id,
            "status": preview["target_status"],
            "reviewed_by": reviewer_email,
            "reviewed_at": datetime.now(timezone.utc),
            "decision_payload": json.dumps({
                **preview["decision_payload"],
                "executed_at": datetime.now(timezone.utc).isoformat(),
            }),
        },
    )
    await session.commit()
    return {**preview, "dry_run": False, "executed": True}
