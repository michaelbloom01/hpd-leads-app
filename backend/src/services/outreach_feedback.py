"""Convert outreach outcomes into claim-ledger evidence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_DNS, uuid5

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.confidence import ConfidenceInput, compute_confidence


NEGATIVE_CONTACT_OUTCOMES = {"bounced", "wrong_number", "bad_email", "disconnected"}
NEGATIVE_MANAGER_OUTCOMES = {"does_not_manage", "wrong_manager", "not_the_manager"}
POSITIVE_CONTACT_OUTCOMES = {"spoke_with_contact", "meeting_scheduled", "confirmed_decision_maker"}
POSITIVE_MANAGER_OUTCOMES = {"confirmed_manager", "confirmed_management"}
REFERRAL_OUTCOMES = {"referred", "referral"}
EXPECTED_TRUTH_ALEMBIC_REVISION = "010_truth_manifest"
OUTREACH_FEEDBACK_TRUTH_TABLES = ["truth_claims", "truth_evidence"]


def _stable_id(*parts: object) -> str:
    key = "::".join(str(part or "") for part in parts)
    return str(uuid5(NAMESPACE_DNS, f"double-edge-outreach-feedback::{key}"))


def stable_outreach_feedback_id(*parts: object) -> str:
    """Stable public ID helper for live writes and batch materialization."""
    return _stable_id(*parts)


async def load_outreach_feedback_truth_write_status(session: AsyncSession) -> dict[str, Any]:
    """Return whether live outreach feedback may write claim-ledger rows.

    Outreach logging itself must keep working while the additive truth schema is
    approval-gated. This narrow check protects only the optional truth-ledger
    side effect used by live feedback capture.
    """
    try:
        table_status: dict[str, bool] = {}
        for table_name in OUTREACH_FEEDBACK_TRUTH_TABLES:
            row = (await session.execute(
                text("SELECT to_regclass(:table_name) IS NOT NULL AS exists"),
                {"table_name": table_name},
            )).first()
            table_status[table_name] = bool(row.exists) if row else False

        alembic_row = (await session.execute(
            text("SELECT to_regclass('alembic_version') IS NOT NULL AS exists")
        )).first()
        alembic_table_exists = bool(alembic_row.exists) if alembic_row else False
        current_revision = None
        if alembic_table_exists:
            revision_row = (await session.execute(
                text("SELECT version_num FROM alembic_version ORDER BY version_num DESC LIMIT 1")
            )).first()
            current_revision = str(revision_row.version_num) if revision_row and revision_row.version_num else None
    except SQLAlchemyError as exc:
        return {
            "ready": False,
            "reason": "schema_status_unavailable",
            "error": str(exc).splitlines()[0],
            "missing_tables": OUTREACH_FEEDBACK_TRUTH_TABLES,
            "expected_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
            "current_revision": None,
        }

    missing_tables = [table_name for table_name, exists in table_status.items() if not exists]
    migration_current = current_revision == EXPECTED_TRUTH_ALEMBIC_REVISION
    ready = not missing_tables and migration_current
    if ready:
        reason = "truth_feedback_claims_recorded"
    elif missing_tables:
        reason = "truth_schema_missing"
    else:
        reason = "truth_schema_revision_not_current"
    return {
        "ready": ready,
        "reason": reason,
        "expected_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
        "current_revision": current_revision,
        "missing_tables": missing_tables,
        "required_tables": table_status,
        "migration_current": migration_current,
    }


def _normalized_outcome(outcome: str | None, notes: str | None) -> str:
    raw = f"{outcome or ''} {notes or ''}".lower()
    raw = raw.replace("-", "_").replace(" ", "_")
    if "wrong_number" in raw or "bad_number" in raw or "disconnected" in raw:
        return "wrong_number"
    if "bounce" in raw or "bad_email" in raw or "undeliverable" in raw:
        return "bounced"
    if "do_not_manage" in raw or "does_not_manage" in raw or "don't_manage" in raw or "not_the_manager" in raw:
        return "does_not_manage"
    if "confirmed_manager" in raw or "we_manage" in raw or "they_manage" in raw:
        return "confirmed_manager"
    if "decision_maker" in raw or "confirmed_contact" in raw:
        return "confirmed_decision_maker"
    if "referr" in raw:
        return "referred"
    return (outcome or "").strip().lower()


def classify_outreach_feedback(
    *,
    lead_id: str | None = None,
    event_id: int,
    method: str | None,
    outcome: str | None,
    notes: str | None,
    bbl: str | None = None,
    canonical_entity_id: str | None = None,
    target_item_id: str | None = None,
) -> list[dict[str, Any]]:
    normalized = _normalized_outcome(outcome, notes)
    source_record_id = f"outreach_events:{event_id}"
    subject_type = "lead"
    subject_id = str(lead_id or "").strip()
    if not subject_id and bbl:
        subject_type = "building"
        subject_id = str(bbl).strip()
    if not subject_id and canonical_entity_id:
        subject_type = "canonical_entity"
        subject_id = str(canonical_entity_id).strip()
    if not subject_id and target_item_id:
        subject_type = "target_item"
        subject_id = str(target_item_id).strip()
    if not subject_id:
        return []

    base = {
        "subject_type": subject_type,
        "subject_id": subject_id,
        "source_record_id": source_record_id,
        "source_name": "outreach_confirmed",
        "source_type": "human_response",
        "method": method,
        "outcome": outcome,
        "normalized_outcome": normalized,
        "notes": notes,
        "canonical_entity_id": canonical_entity_id,
        "target_item_id": target_item_id,
    }

    claims: list[dict[str, Any]] = []
    contact_path_id = f"{subject_type}:{subject_id}:{method or 'unknown'}"
    if normalized in NEGATIVE_CONTACT_OUTCOMES:
        claims.append({
            **base,
            "predicate": "has_valid_contact_path",
            "object_type": "contact_path",
            "object_id": contact_path_id,
            "claim_type": "person_contact",
            "support_status": "contradicts",
            "normalized_value": normalized,
        })
    elif normalized in POSITIVE_CONTACT_OUTCOMES:
        claims.append({
            **base,
            "predicate": "has_valid_contact_path",
            "object_type": "contact_path",
            "object_id": contact_path_id,
            "claim_type": "person_contact",
            "support_status": "supports",
            "normalized_value": normalized,
        })

    manager_object_type = "building" if bbl and subject_type != "building" else "management_relationship"
    manager_object_id = str(bbl).strip() if bbl and subject_type != "building" else f"{subject_type}:{subject_id}:manager_feedback"
    if normalized in NEGATIVE_MANAGER_OUTCOMES:
        claims.append({
            **base,
            "predicate": "has_valid_management_relationship",
            "object_type": manager_object_type if bbl else "building_set",
            "object_id": manager_object_id if bbl else f"{subject_type}:{subject_id}:current_buildings",
            "claim_type": "building_management",
            "support_status": "contradicts",
            "normalized_value": normalized,
        })
    elif normalized in POSITIVE_MANAGER_OUTCOMES:
        claims.append({
            **base,
            "predicate": "has_valid_management_relationship",
            "object_type": manager_object_type if bbl else "building_set",
            "object_id": manager_object_id if bbl else f"{subject_type}:{subject_id}:current_buildings",
            "claim_type": "building_management",
            "support_status": "supports",
            "normalized_value": normalized,
        })

    if normalized in REFERRAL_OUTCOMES:
        claims.append({
            **base,
            "predicate": "has_referral_path",
            "object_type": "contact_path",
            "object_id": f"{subject_type}:{subject_id}:referral",
            "claim_type": "person_contact",
            "support_status": "supports",
            "normalized_value": normalized,
        })

    return claims


async def record_outreach_feedback_claims(
    session: AsyncSession,
    *,
    lead_id: str | None = None,
    event_id: int,
    method: str | None,
    outcome: str | None,
    notes: str | None,
    bbl: str | None = None,
    canonical_entity_id: str | None = None,
    target_item_id: str | None = None,
) -> list[str]:
    claims = classify_outreach_feedback(
        lead_id=lead_id,
        event_id=event_id,
        method=method,
        outcome=outcome,
        notes=notes,
        bbl=bbl,
        canonical_entity_id=canonical_entity_id,
        target_item_id=target_item_id,
    )
    inserted_ids: list[str] = []
    now = datetime.now(timezone.utc)
    for claim in claims:
        support_status = claim["support_status"]
        confidence = compute_confidence(ConfidenceInput(
            claim_type=claim["claim_type"],
            supporting_sources=["outreach_confirmed"] if support_status == "supports" else [],
            contradicting_sources=["outreach_confirmed"] if support_status == "contradicts" else [],
            freshness_days=0,
            source_agreement_count=1 if support_status == "supports" else 0,
            source_disagreement_count=1 if support_status == "contradicts" else 0,
        ))
        if support_status == "contradicts":
            confidence["belief_status"] = "conflicting"
            confidence["actionability_level"] = "do_not_act"

        claim_id = stable_outreach_feedback_id(claim["subject_type"], claim["subject_id"], claim["predicate"], claim["object_type"], claim["object_id"], event_id)
        evidence_id = stable_outreach_feedback_id("evidence", claim_id, event_id, support_status)
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
                    0, :observed_at, true, :actionability_level, CAST(:rationale AS JSONB),
                    NOW(), NOW()
                )
                ON CONFLICT (claim_id)
                DO UPDATE SET
                    belief_status = EXCLUDED.belief_status,
                    confidence_score = EXCLUDED.confidence_score,
                    freshness_days = 0,
                    observed_at = EXCLUDED.observed_at,
                    actionability_level = EXCLUDED.actionability_level,
                    rationale = EXCLUDED.rationale,
                    updated_at = NOW()
            """),
            {
                "claim_id": claim_id,
                "subject_type": claim["subject_type"],
                "subject_id": claim["subject_id"],
                "predicate": claim["predicate"],
                "object_type": claim["object_type"],
                "object_id": claim["object_id"],
                "extracted_value": claim.get("notes") or claim.get("outcome"),
                "normalized_value": claim["normalized_value"],
                "claim_type": claim["claim_type"],
                "belief_status": confidence["belief_status"],
                "confidence_score": confidence["confidence_score"],
                "observed_at": now,
                "actionability_level": confidence["actionability_level"],
                "rationale": json.dumps({
                    **confidence["rationale"],
                    "outreach_event_id": event_id,
                    "outreach_method": method,
                    "outreach_outcome": outcome,
                    "normalized_outcome": claim["normalized_outcome"],
                }),
            },
        )
        await session.execute(
            text("""
                INSERT INTO truth_evidence (
                    evidence_id, claim_id, source_name, source_type, source_record_id,
                    observed_at, extracted_value, normalized_value, support_status,
                    source_quality_score, evidence_weight, raw_payload, created_at, updated_at
                )
                VALUES (
                    :evidence_id, :claim_id, :source_name, :source_type, :source_record_id,
                    :observed_at, :extracted_value, :normalized_value, :support_status,
                    :source_quality_score, :evidence_weight, CAST(:raw_payload AS JSONB), NOW(), NOW()
                )
                ON CONFLICT (evidence_id)
                DO UPDATE SET
                    observed_at = EXCLUDED.observed_at,
                    extracted_value = EXCLUDED.extracted_value,
                    normalized_value = EXCLUDED.normalized_value,
                    support_status = EXCLUDED.support_status,
                    raw_payload = EXCLUDED.raw_payload,
                    updated_at = NOW()
            """),
            {
                "evidence_id": evidence_id,
                "claim_id": claim_id,
                "source_name": claim["source_name"],
                "source_type": claim["source_type"],
                "source_record_id": claim["source_record_id"],
                "observed_at": now,
                "extracted_value": claim.get("notes") or claim.get("outcome"),
                "normalized_value": claim["normalized_value"],
                "support_status": support_status,
                "source_quality_score": 0.98,
                "evidence_weight": 1.0 if support_status == "supports" else -1.0,
                "raw_payload": json.dumps({
                    "lead_id": lead_id,
                    "bbl": bbl,
                    "canonical_entity_id": canonical_entity_id,
                    "target_item_id": target_item_id,
                    "method": method,
                    "outcome": outcome,
                    "notes": notes,
                }),
            },
        )
        inserted_ids.append(claim_id)
    return inserted_ids
