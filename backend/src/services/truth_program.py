"""Read-model helpers for the Data Truth & Confidence program."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid5, NAMESPACE_DNS

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.confidence import ConfidenceInput, compute_confidence, review_bucket
from src.services.outreach_feedback import (
    NEGATIVE_CONTACT_OUTCOMES,
    NEGATIVE_MANAGER_OUTCOMES,
    POSITIVE_CONTACT_OUTCOMES,
    POSITIVE_MANAGER_OUTCOMES,
    REFERRAL_OUTCOMES,
)


GOLDEN_CASE_SEEDS: list[dict[str, Any]] = [
    {
        "case_id": "golden-coop-board-owner-agent",
        "name": "Co-op board with owner corp and managing agent",
        "case_type": "coop_board",
        "subject_type": "building",
        "subject_id": "golden-bbl-coop-board",
        "expected_outcome": "distinguish_board_owner_from_manager",
        "expected_claims": {
            "required_claims": [
                {
                    "predicate": "has_owner",
                    "object_type": "entity",
                    "object_id": "golden-coop-owner-corp",
                    "claim_type": "building_ownership",
                    "min_confidence": 0.7,
                    "max_freshness_days": 180,
                },
                {
                    "predicate": "has_registered_agent",
                    "claim_type": "person_contact",
                    "actionability_level": "automated_enrichment",
                },
            ],
            "forbidden_claims": [
                {
                    "predicate": "manages_building",
                    "object_id": "golden-coop-owner-corp",
                    "metric": "false_merge",
                },
            ],
        },
        "tricky_features": ["co-op board", "owner corporation", "agent may be site manager"],
    },
    {
        "case_id": "golden-llc-shell-sponsor-manager",
        "name": "Owner LLC shell vs sponsor vs third-party manager",
        "case_type": "owner_shell",
        "subject_type": "entity",
        "subject_id": "golden-owner-llc-shell",
        "expected_outcome": "avoid_false_manager_merge",
        "expected_claims": {
            "required_claims": [
                {
                    "predicate": "has_owner_role",
                    "claim_type": "entity_identity",
                    "min_confidence": 0.64,
                },
            ],
            "forbidden_claims": [
                {
                    "predicate": "same_entity_as",
                    "object_id": "golden-sponsor-operating-company",
                    "metric": "false_merge",
                },
                {
                    "predicate": "manages_buildings",
                    "object_id": "golden-owner-llc-shell",
                    "metric": "false_merge",
                },
            ],
        },
        "tricky_features": ["LLC suffix variants", "shared legal address", "sponsor ambiguity"],
    },
    {
        "case_id": "golden-stale-registered-agent",
        "name": "Stale registered agent with current HPD contact disagreement",
        "case_type": "stale_agent",
        "subject_type": "entity",
        "subject_id": "golden-stale-agent-entity",
        "expected_outcome": "downgrade_stale_agent_contact",
        "expected_claims": {
            "required_claims": [
                {
                    "predicate": "has_registered_agent",
                    "claim_type": "person_contact",
                    "actionability_level": "do_not_act",
                },
                {
                    "predicate": "has_current_hpd_contact_conflict",
                    "claim_type": "person_contact",
                    "min_confidence": 0.45,
                    "metric": "contact_accuracy",
                },
            ],
            "forbidden_claims": [
                {
                    "predicate": "has_valid_contact_path",
                    "actionability_level": "recommended_outreach",
                    "metric": "contact_accuracy",
                },
            ],
        },
        "tricky_features": ["law firm address", "stale DOS record", "HPD contact conflict"],
    },
    {
        "case_id": "golden-pm-variant-duplicate",
        "name": "Known duplicate PM company suffix variants",
        "case_type": "pm_duplicate",
        "subject_type": "entity",
        "subject_id": "golden-pm-variant-primary",
        "expected_outcome": "merge_only_low_risk_suffix_variants",
        "expected_claims": {
            "required_claims": [
                {
                    "predicate": "same_entity_as",
                    "object_type": "entity",
                    "object_id": "golden-pm-variant-secondary",
                    "claim_type": "entity_identity",
                    "metric": "false_split",
                },
                {
                    "predicate": "manages_buildings",
                    "object_type": "building",
                    "object_id": "golden-pm-variant-building",
                    "claim_type": "building_management",
                    "min_confidence": 0.78,
                    "max_freshness_days": 120,
                },
            ],
            "forbidden_claims": [
                {
                    "predicate": "same_entity_as",
                    "object_id": "golden-broad-name-unrelated-company",
                    "metric": "false_merge",
                },
            ],
        },
        "tricky_features": ["INC vs LLC", "MGMT abbreviation", "shared named contact"],
    },
    {
        "case_id": "golden-shared-address-unrelated-entities",
        "name": "Shared office address across unrelated property companies",
        "case_type": "shared_address_false_merge",
        "subject_type": "entity",
        "subject_id": "golden-shared-address-company-a",
        "expected_outcome": "do_not_merge_on_address_only",
        "expected_claims": {
            "required_claims": [
                {
                    "predicate": "has_mailing_address",
                    "claim_type": "entity_address",
                    "min_confidence": 0.64,
                },
            ],
            "forbidden_claims": [
                {
                    "predicate": "same_entity_as",
                    "object_id": "golden-shared-address-company-b",
                    "metric": "false_merge",
                },
                {
                    "predicate": "maps_to_canonical_entity",
                    "object_id": "golden-shared-address-canonical-b",
                    "metric": "false_merge",
                },
            ],
        },
        "tricky_features": ["shared address", "mailbox address", "false merge risk"],
    },
    {
        "case_id": "golden-false-split-same-manager",
        "name": "Same manager split across legal suffix and abbreviation variants",
        "case_type": "false_split",
        "subject_type": "canonical_entity",
        "subject_id": "golden-same-manager-canonical-a",
        "expected_outcome": "detect_false_split_without_broad_auto_merge",
        "expected_claims": {
            "required_claims": [
                {
                    "predicate": "same_entity_as",
                    "object_type": "canonical_entity",
                    "object_id": "golden-same-manager-canonical-b",
                    "claim_type": "entity_identity",
                    "metric": "false_split",
                },
                {
                    "predicate": "manages_buildings",
                    "object_type": "building",
                    "object_id": "golden-same-manager-building",
                    "claim_type": "building_management",
                    "metric": "building_link_accuracy",
                },
            ],
            "forbidden_claims": [
                {
                    "predicate": "same_entity_as",
                    "object_id": "golden-same-manager-broad-name-only",
                    "metric": "false_merge",
                },
            ],
        },
        "tricky_features": ["legal suffix variants", "abbreviation variants", "false split risk"],
    },
    {
        "case_id": "golden-outreach-wrong-contact",
        "name": "Outreach confirms contact does not manage building",
        "case_type": "outreach_contradiction",
        "subject_type": "lead",
        "subject_id": "golden-outreach-wrong-contact-lead",
        "expected_outcome": "downgrade_contact_and_manager_claim",
        "expected_claims": {
            "required_claims": [
                {
                    "predicate": "has_valid_contact_path",
                    "claim_type": "person_contact",
                    "actionability_level": "do_not_act",
                    "metric": "contact_accuracy",
                },
                {
                    "predicate": "has_valid_management_relationship",
                    "claim_type": "building_management",
                    "actionability_level": "do_not_act",
                    "metric": "building_link_accuracy",
                },
            ],
            "forbidden_claims": [
                {
                    "predicate": "has_valid_contact_path",
                    "actionability_level": "recommended_outreach",
                    "metric": "contact_accuracy",
                },
            ],
        },
        "tricky_features": ["wrong contact", "outreach contradiction", "stale HPD manager"],
    },
]


def stable_claim_id(*parts: object) -> str:
    key = "::".join(str(part or "") for part in parts)
    return str(uuid5(NAMESPACE_DNS, f"double-edge-truth::{key}"))


def serialize_dt(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _claim_rows_to_payloads(result) -> list[dict[str, Any]]:
    claims = []
    for row in result:
        payload = dict(row._mapping)
        payload["observed_at"] = serialize_dt(payload.get("observed_at"))
        payload["rationale"] = payload.get("rationale") or {}
        payload["supporting_sources"] = list(payload.get("supporting_sources") or [])
        payload["contradicting_sources"] = list(payload.get("contradicting_sources") or [])
        claims.append(payload)
    return claims


async def load_persisted_claims(session: AsyncSession, *, lead_id: str, canonical_entity_id: str | None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"lead_id": lead_id, "canonical_entity_id": canonical_entity_id or ""}
    result = await session.execute(
        text("""
            WITH claim_evidence AS (
                SELECT
                    claim_id,
                    COUNT(*) FILTER (WHERE support_status = 'supports')::int AS supporting_evidence_count,
                    COUNT(*) FILTER (WHERE support_status = 'contradicts')::int AS contradicting_evidence_count,
                    ARRAY_REMOVE(ARRAY_AGG(DISTINCT source_name) FILTER (WHERE support_status = 'supports'), NULL) AS supporting_sources,
                    ARRAY_REMOVE(ARRAY_AGG(DISTINCT source_name) FILTER (WHERE support_status = 'contradicts'), NULL) AS contradicting_sources
                FROM truth_evidence
                GROUP BY claim_id
            )
            SELECT
                c.claim_id,
                c.subject_type,
                c.subject_id,
                c.predicate,
                c.object_type,
                c.object_id,
                c.normalized_value,
                c.claim_type,
                c.belief_status,
                c.confidence_score,
                c.freshness_days,
                c.observed_at,
                c.actionability_level,
                c.rationale,
                COALESCE(ce.supporting_evidence_count, 0) AS supporting_evidence_count,
                COALESCE(ce.contradicting_evidence_count, 0) AS contradicting_evidence_count,
                COALESCE(ce.supporting_sources, ARRAY[]::TEXT[]) AS supporting_sources,
                COALESCE(ce.contradicting_sources, ARRAY[]::TEXT[]) AS contradicting_sources
            FROM truth_claims c
            LEFT JOIN claim_evidence ce ON ce.claim_id = c.claim_id
            WHERE (c.subject_type = 'lead' AND c.subject_id = :lead_id)
               OR (c.object_type = 'lead' AND c.object_id = :lead_id)
               OR (:canonical_entity_id != '' AND c.subject_type = 'canonical_entity' AND c.subject_id = :canonical_entity_id)
               OR (:canonical_entity_id != '' AND c.object_type = 'canonical_entity' AND c.object_id = :canonical_entity_id)
            ORDER BY c.confidence_score DESC NULLS LAST, c.updated_at DESC NULLS LAST
            LIMIT 50
        """),
        params,
    )
    return _claim_rows_to_payloads(result)


async def load_subject_claims(session: AsyncSession, *, subject_type: str, subject_id: str, limit: int = 50) -> list[dict[str, Any]]:
    result = await session.execute(
        text("""
            WITH claim_evidence AS (
                SELECT
                    claim_id,
                    COUNT(*) FILTER (WHERE support_status = 'supports')::int AS supporting_evidence_count,
                    COUNT(*) FILTER (WHERE support_status = 'contradicts')::int AS contradicting_evidence_count,
                    ARRAY_REMOVE(ARRAY_AGG(DISTINCT source_name) FILTER (WHERE support_status = 'supports'), NULL) AS supporting_sources,
                    ARRAY_REMOVE(ARRAY_AGG(DISTINCT source_name) FILTER (WHERE support_status = 'contradicts'), NULL) AS contradicting_sources
                FROM truth_evidence
                GROUP BY claim_id
            )
            SELECT
                c.claim_id,
                c.subject_type,
                c.subject_id,
                c.predicate,
                c.object_type,
                c.object_id,
                c.normalized_value,
                c.claim_type,
                c.belief_status,
                c.confidence_score,
                c.freshness_days,
                c.observed_at,
                c.actionability_level,
                c.rationale,
                COALESCE(ce.supporting_evidence_count, 0) AS supporting_evidence_count,
                COALESCE(ce.contradicting_evidence_count, 0) AS contradicting_evidence_count,
                COALESCE(ce.supporting_sources, ARRAY[]::TEXT[]) AS supporting_sources,
                COALESCE(ce.contradicting_sources, ARRAY[]::TEXT[]) AS contradicting_sources
            FROM truth_claims c
            LEFT JOIN claim_evidence ce ON ce.claim_id = c.claim_id
            WHERE (c.subject_type = :subject_type AND c.subject_id = :subject_id)
               OR (c.object_type = :subject_type AND c.object_id = :subject_id)
            ORDER BY c.confidence_score DESC NULLS LAST, c.updated_at DESC NULLS LAST
            LIMIT :limit
        """),
        {"subject_type": subject_type, "subject_id": subject_id, "limit": limit},
    )
    return _claim_rows_to_payloads(result)


def _claim_belief_sentence(claim: dict[str, Any]) -> str:
    subject = f"{claim.get('subject_type')} {claim.get('subject_id')}"
    predicate = str(claim.get("predicate") or "has_claim").replace("_", " ")
    value = claim.get("normalized_value") or claim.get("object_id") or claim.get("object_type") or "observed value"
    status = str(claim.get("belief_status") or "proposed").replace("_", " ")
    return f"{subject} {predicate}: {value} ({status})."


def _claim_sentences_for_claims(claims: list[dict[str, Any]]) -> list[str]:
    return [_claim_belief_sentence(claim) for claim in claims]


def _unique_sources(claims: list[dict[str, Any]], field: str) -> list[str]:
    seen: set[str] = set()
    sources: list[str] = []
    for claim in claims:
        for source in claim.get(field) or []:
            value = str(source or "").strip()
            if value and value not in seen:
                seen.add(value)
                sources.append(value)
    return sources


def _why_we_believe_sentences(claims: list[dict[str, Any]], *, limit: int = 5) -> list[str]:
    sentences: list[str] = []
    for claim in claims:
        predicate = str(claim.get("predicate") or "claim").replace("_", " ")
        supporting_sources = [str(source) for source in claim.get("supporting_sources") or [] if source]
        contradicting_sources = [str(source) for source in claim.get("contradicting_sources") or [] if source]
        supporting_count = int(claim.get("supporting_evidence_count") or 0)
        contradicting_count = int(claim.get("contradicting_evidence_count") or 0)
        confidence = claim.get("confidence_score")
        confidence_text = f"{round(float(confidence) * 100)}% confidence" if confidence is not None else "unscored confidence"

        parts: list[str] = []
        if supporting_sources or supporting_count:
            source_text = ", ".join(supporting_sources) if supporting_sources else "recorded evidence"
            parts.append(f"{supporting_count} support from {source_text}")
        if contradicting_sources or contradicting_count:
            source_text = ", ".join(contradicting_sources) if contradicting_sources else "recorded evidence"
            parts.append(f"{contradicting_count} contradiction from {source_text}")
        if not parts:
            parts.append("no supporting evidence has been materialized yet")

        sentences.append(f"{predicate}: {'; '.join(parts)}; {confidence_text}.")
        if len(sentences) >= limit:
            break
    return sentences


def _belief_summary_from_claims(
    claims: list[dict[str, Any]],
    *,
    what_we_believe: list[str],
    contradictions: int,
    freshness_days: int | None,
    safe_actions: list[str],
) -> dict[str, Any]:
    return {
        "what_we_believe": what_we_believe,
        "why_we_believe": _why_we_believe_sentences(claims),
        "supporting_sources": _unique_sources(claims, "supporting_sources"),
        "contradicting_sources": _unique_sources(claims, "contradicting_sources"),
        "contradiction_count": contradictions,
        "freshness_days": freshness_days,
        "safe_actions": _summary_safe_actions(claims, safe_actions),
    }


def _summary_safe_actions(claims: list[dict[str, Any]], safe_actions: list[str]) -> list[str]:
    explicit_safe_actions = []
    seen: set[str] = set()
    for action in safe_actions:
        normalized = str(action or "").strip()
        if normalized and normalized != "do_not_act" and normalized not in seen:
            seen.add(normalized)
            explicit_safe_actions.append(normalized)
    if explicit_safe_actions:
        return explicit_safe_actions
    if claims:
        return ["do_not_act"]
    return ["not_evaluated"]


def _freshness_days(value: Any) -> int | None:
    if not value:
        return None
    dt = value if getattr(value, "tzinfo", None) else value.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - dt).days)


def _contact_display_name(row: dict[str, Any]) -> str:
    corporation = str(row.get("corporation_name") or "").strip()
    person = " ".join(
        part for part in [str(row.get("first_name") or "").strip(), str(row.get("last_name") or "").strip()]
        if part
    ).strip()
    return corporation or person or str(row.get("description") or "").strip() or f"building_contact:{row.get('id')}"


def _contact_mailing_address(row: dict[str, Any]) -> str | None:
    parts = [
        str(row.get("business_address") or "").strip(),
        str(row.get("business_city") or "").strip(),
        str(row.get("business_state") or "").strip(),
        str(row.get("business_zip") or "").strip(),
    ]
    return ", ".join(part for part in parts if part) or None


def _person_key(value: str | None) -> str:
    normalized = str(value or "").strip()
    if normalized.lower().startswith("person:"):
        normalized = normalized.split(":", 1)[1]
    return " ".join(normalized.lower().split())


def _normalized_outreach_outcome(outcome: str | None, notes: str | None) -> str:
    raw = f"{outcome or ''} {notes or ''}".lower()
    raw = raw.replace("-", "_").replace(" ", "_")
    if "wrong_number" in raw or "bad_number" in raw or "disconnected" in raw:
        return "wrong_number"
    if "bounce" in raw or "bad_email" in raw or "undeliverable" in raw:
        return "bounced"
    if "do_not_manage" in raw or "does_not_manage" in raw or "don't_manage" in raw or "not_the_manager" in raw or "wrong_manager" in raw:
        return "does_not_manage"
    if "confirmed_manager" in raw or "we_manage" in raw or "they_manage" in raw:
        return "confirmed_manager"
    if "decision_maker" in raw or "confirmed_contact" in raw:
        return "confirmed_decision_maker"
    if "referr" in raw:
        return "referred"
    return str(outcome or "").strip().lower()


def _building_contact_claim_shape(contact_type: str | None) -> dict[str, str] | None:
    value = str(contact_type or "").strip()
    if value in {"Owner", "CorporateOwner", "IndividualOwner"}:
        return {"predicate": "has_owner_contact", "claim_type": "building_ownership"}
    if value == "ManagementCompany":
        return {"predicate": "has_management_contact", "claim_type": "building_management"}
    if value == "Agent":
        return {"predicate": "has_registered_agent", "claim_type": "registered_agent"}
    if value in {"HeadOfficer", "Officer", "Shareholder"}:
        return {"predicate": "has_person_contact", "claim_type": "person_contact"}
    return None


def _synthetic_claim(
    *,
    subject_type: str,
    subject_id: str,
    predicate: str,
    object_type: str,
    object_id: str,
    normalized_value: str | None,
    claim_type: str,
    supporting_sources: list[str],
    contradicting_sources: list[str] | None = None,
    observed_at: Any = None,
    supporting_evidence_count: int = 1,
    contradicting_evidence_count: int = 0,
    rationale: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contradiction_sources = contradicting_sources or []
    age = _freshness_days(observed_at)
    confidence = compute_confidence(ConfidenceInput(
        claim_type=claim_type,
        supporting_sources=supporting_sources,
        contradicting_sources=contradiction_sources,
        freshness_days=age,
        source_agreement_count=max(supporting_evidence_count, len(set(supporting_sources))),
        source_disagreement_count=contradicting_evidence_count,
    ))
    return {
        "claim_id": stable_claim_id(subject_type, subject_id, predicate, object_type, object_id, *supporting_sources),
        "subject_type": subject_type,
        "subject_id": subject_id,
        "predicate": predicate,
        "object_type": object_type,
        "object_id": object_id,
        "normalized_value": normalized_value,
        "claim_type": claim_type,
        "freshness_days": age,
        "observed_at": serialize_dt(observed_at),
        "supporting_evidence_count": supporting_evidence_count,
        "contradicting_evidence_count": contradicting_evidence_count,
        "supporting_sources": supporting_sources,
        "contradicting_sources": contradiction_sources,
        **confidence,
        "rationale": {
            **confidence.get("rationale", {}),
            "source": "synthetic_subject_summary",
            **(rationale or {}),
        },
    }


def _outreach_feedback_claim(
    *,
    row: dict[str, Any],
    subject_type: str,
    subject_id: str,
    predicate: str,
    claim_type: str,
    support_status: str,
    normalized_outcome: str,
) -> dict[str, Any]:
    event_id = str(row.get("id") or "")
    observed_at = row.get("event_timestamp") or row.get("updated_at") or row.get("created_at")
    object_type = "outreach_event"
    object_id = f"outreach_events:{event_id}"
    supporting_sources = ["outreach_confirmed"] if support_status == "supports" else []
    contradicting_sources = ["outreach_confirmed"] if support_status == "contradicts" else []
    claim = _synthetic_claim(
        subject_type=subject_type,
        subject_id=subject_id,
        predicate=predicate,
        object_type=object_type,
        object_id=object_id,
        normalized_value=normalized_outcome,
        claim_type=claim_type,
        supporting_sources=supporting_sources,
        contradicting_sources=contradicting_sources,
        observed_at=observed_at,
        supporting_evidence_count=1 if support_status == "supports" else 0,
        contradicting_evidence_count=1 if support_status == "contradicts" else 0,
        rationale={
            "outreach_event_id": row.get("id"),
            "outreach_method": row.get("method"),
            "outreach_outcome": row.get("outcome"),
            "normalized_outcome": normalized_outcome,
            "lead_id": row.get("lead_id"),
            "bbl": row.get("bbl"),
            "canonical_entity_id": row.get("canonical_entity_id"),
            "target_item_id": row.get("target_item_id"),
            "notes": row.get("notes"),
        },
    )
    if support_status == "contradicts":
        claim["belief_status"] = "conflicting"
        claim["actionability_level"] = "do_not_act"
        claim["confidence_score"] = min(float(claim.get("confidence_score") or 0), 0.44)
    return claim


async def load_synthetic_outreach_feedback_claims(
    session: AsyncSession,
    *,
    subject_type: str,
    subject_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    normalized_subject_type = subject_type.strip().lower()
    params: dict[str, Any] = {"subject_id": subject_id, "person_key": _person_key(subject_id), "limit": limit}
    if normalized_subject_type == "lead":
        where_sql = "oe.lead_id = :subject_id"
    elif normalized_subject_type == "building":
        where_sql = "oe.bbl = :subject_id"
    elif normalized_subject_type in {"canonical_entity", "entity"}:
        where_sql = "oe.canonical_entity_id = :subject_id"
    elif normalized_subject_type in {"contact", "hpd_contact"}:
        where_sql = "(oe.target_item_id = :subject_id OR oe.notes ILIKE CONCAT('%', :subject_id, '%'))"
    elif normalized_subject_type == "person":
        where_sql = "(LOWER(COALESCE(oe.notes, '')) LIKE CONCAT('%', :person_key, '%'))"
    else:
        return []

    rows = await session.execute(
        text(f"""
            SELECT
                oe.id,
                oe.lead_id,
                oe.bbl,
                oe.canonical_entity_id,
                oe.target_item_id,
                oe.method,
                oe.outcome,
                oe.notes,
                oe.event_timestamp,
                oe.created_at,
                oe.updated_at
            FROM outreach_events oe
            WHERE {where_sql}
              AND (
                LOWER(COALESCE(oe.outcome, '')) IN (
                    'wrong_number',
                    'bounced',
                    'bad_email',
                    'disconnected',
                    'does_not_manage',
                    'wrong_manager',
                    'not_the_manager',
                    'spoke_with_contact',
                    'meeting_scheduled',
                    'confirmed_decision_maker',
                    'confirmed_manager',
                    'confirmed_management',
                    'referred',
                    'referral'
                )
                OR COALESCE(oe.notes, '') ILIKE ANY (ARRAY[
                    '%wrong number%',
                    '%bad email%',
                    '%bounce%',
                    '%does not manage%',
                    '%do not manage%',
                    '%not the manager%',
                    '%wrong manager%',
                    '%confirmed manager%',
                    '%decision maker%',
                    '%referr%'
                ])
              )
            ORDER BY oe.event_timestamp DESC NULLS LAST, oe.updated_at DESC NULLS LAST, oe.id DESC
            LIMIT :limit
        """),
        params,
    )

    claims: list[dict[str, Any]] = []
    for row in rows:
        event = dict(row._mapping)
        normalized = _normalized_outreach_outcome(event.get("outcome"), event.get("notes"))
        if normalized in NEGATIVE_CONTACT_OUTCOMES:
            claims.append(_outreach_feedback_claim(
                row=event,
                subject_type=normalized_subject_type,
                subject_id=subject_id,
                predicate="has_valid_contact_path",
                claim_type="person_contact",
                support_status="contradicts",
                normalized_outcome=normalized,
            ))
        elif normalized in POSITIVE_CONTACT_OUTCOMES:
            claims.append(_outreach_feedback_claim(
                row=event,
                subject_type=normalized_subject_type,
                subject_id=subject_id,
                predicate="has_valid_contact_path",
                claim_type="person_contact",
                support_status="supports",
                normalized_outcome=normalized,
            ))

        if normalized in NEGATIVE_MANAGER_OUTCOMES:
            claims.append(_outreach_feedback_claim(
                row=event,
                subject_type=normalized_subject_type,
                subject_id=subject_id,
                predicate="has_valid_management_relationship",
                claim_type="building_management",
                support_status="contradicts",
                normalized_outcome=normalized,
            ))
        elif normalized in POSITIVE_MANAGER_OUTCOMES:
            claims.append(_outreach_feedback_claim(
                row=event,
                subject_type=normalized_subject_type,
                subject_id=subject_id,
                predicate="has_valid_management_relationship",
                claim_type="building_management",
                support_status="supports",
                normalized_outcome=normalized,
            ))

        if normalized in REFERRAL_OUTCOMES:
            claims.append(_outreach_feedback_claim(
                row=event,
                subject_type=normalized_subject_type,
                subject_id=subject_id,
                predicate="has_referral_path",
                claim_type="person_contact",
                support_status="supports",
                normalized_outcome=normalized,
            ))
    return claims[:limit]


async def load_synthetic_building_claims(session: AsyncSession, *, bbl: str, limit: int = 50) -> list[dict[str, Any]] | None:
    building_row = (await session.execute(
        text("""
            SELECT bbl, address, borough, updated_at
            FROM buildings
            WHERE bbl = :bbl
        """),
        {"bbl": bbl},
    )).first()
    if not building_row:
        return None

    building = dict(building_row._mapping)
    claims: list[dict[str, Any]] = [
        _synthetic_claim(
            subject_type="building",
            subject_id=bbl,
            predicate="exists_in_building_table",
            object_type="building",
            object_id=bbl,
            normalized_value=building.get("address") or bbl,
            claim_type="building_reference",
            supporting_sources=["hpd_registrations"],
            observed_at=building.get("updated_at"),
            rationale={"address": building.get("address"), "borough": building.get("borough")},
        )
    ]

    management_row = (await session.execute(
        text("""
            SELECT
                COUNT(DISTINCT bm.lead_id)::int AS current_manager_count,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT bm.lead_id), NULL) AS lead_ids,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT COALESCE(NULLIF(l.company_name, ''), NULLIF(l.agent_name, ''), NULLIF(l.owner_name, ''), bm.lead_id)), NULL) AS lead_names,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT COALESCE(NULLIF(bm.role, ''), 'manager')), NULL) AS roles,
                MAX(COALESCE(bm.updated_at, l.updated_at)) AS observed_at
            FROM building_management bm
            LEFT JOIN leads l ON l.lead_id = bm.lead_id
            WHERE bm.bbl = :bbl
              AND bm.is_current = true
        """),
        {"bbl": bbl},
    )).first()
    if management_row:
        management = dict(management_row._mapping)
        manager_count = int(management.get("current_manager_count") or 0)
        if manager_count:
            manager_names = [str(name) for name in (management.get("lead_names") or []) if name]
            claims.append(_synthetic_claim(
                subject_type="building",
                subject_id=bbl,
                predicate="has_current_management_link",
                object_type="lead_set",
                object_id=f"building:{bbl}:current_managers",
                normalized_value=", ".join(manager_names) or f"{manager_count} current manager link(s)",
                claim_type="building_management",
                supporting_sources=["building_management"],
                contradicting_sources=["building_management"] if manager_count > 1 else [],
                observed_at=management.get("observed_at"),
                supporting_evidence_count=manager_count,
                contradicting_evidence_count=max(0, manager_count - 1),
                rationale={
                    "lead_ids": management.get("lead_ids") or [],
                    "roles": management.get("roles") or [],
                    "why_contradicting": "More than one current manager link is present for the same building." if manager_count > 1 else None,
                },
            ))

    contact_rows = await session.execute(
        text("""
            SELECT
                id,
                bbl,
                registration_contact_id,
                registration_id,
                contact_type,
                description,
                corporation_name,
                first_name,
                last_name,
                title,
                business_address,
                business_city,
                business_state,
                business_zip,
                updated_at AS observed_at
            FROM building_contacts
            WHERE bbl = :bbl
              AND contact_type IN ('Agent', 'ManagementCompany', 'Owner', 'CorporateOwner', 'IndividualOwner', 'HeadOfficer', 'Officer', 'Shareholder')
            ORDER BY updated_at DESC NULLS LAST, id DESC
            LIMIT :limit
        """),
        {"bbl": bbl, "limit": limit},
    )
    for row in contact_rows:
        contact = dict(row._mapping)
        shape = _building_contact_claim_shape(contact.get("contact_type"))
        if not shape:
            continue
        display_name = _contact_display_name(contact)
        claims.append(_synthetic_claim(
            subject_type="building",
            subject_id=bbl,
            predicate=shape["predicate"],
            object_type="hpd_contact",
            object_id=str(contact.get("id")),
            normalized_value=display_name,
            claim_type=shape["claim_type"],
            supporting_sources=["hpd_contacts"],
            observed_at=contact.get("observed_at"),
            rationale={
                "contact_type": contact.get("contact_type"),
                "registration_id": contact.get("registration_id"),
                "registration_contact_id": contact.get("registration_contact_id"),
                "title": contact.get("title"),
            },
        ))

    return claims[:limit]


async def load_synthetic_hpd_contact_claims(session: AsyncSession, *, contact_id: str, public_subject_type: str = "contact") -> list[dict[str, Any]] | None:
    contact_row = (await session.execute(
        text("""
            SELECT
                id,
                bbl,
                registration_contact_id,
                registration_id,
                contact_type,
                description,
                corporation_name,
                first_name,
                last_name,
                title,
                business_address,
                business_city,
                business_state,
                business_zip,
                updated_at AS observed_at
            FROM building_contacts
            WHERE id::text = :contact_id
        """),
        {"contact_id": contact_id},
    )).first()
    if not contact_row:
        return None

    contact = dict(contact_row._mapping)
    display_name = _contact_display_name(contact)
    bbl = str(contact.get("bbl") or "").strip()
    claims: list[dict[str, Any]] = [
        _synthetic_claim(
            subject_type=public_subject_type,
            subject_id=contact_id,
            predicate="identified_in_hpd_contacts",
            object_type="building",
            object_id=bbl,
            normalized_value=display_name,
            claim_type="person_contact",
            supporting_sources=["hpd_contacts"],
            observed_at=contact.get("observed_at"),
            rationale={
                "contact_type": contact.get("contact_type"),
                "registration_id": contact.get("registration_id"),
                "registration_contact_id": contact.get("registration_contact_id"),
                "title": contact.get("title"),
            },
        )
    ]

    shape = _building_contact_claim_shape(contact.get("contact_type"))
    if shape and bbl:
        claims.append(_synthetic_claim(
            subject_type=public_subject_type,
            subject_id=contact_id,
            predicate=shape["predicate"],
            object_type="building",
            object_id=bbl,
            normalized_value=display_name,
            claim_type=shape["claim_type"],
            supporting_sources=["hpd_contacts"],
            observed_at=contact.get("observed_at"),
            rationale={
                "contact_type": contact.get("contact_type"),
                "registration_id": contact.get("registration_id"),
                "registration_contact_id": contact.get("registration_contact_id"),
                "title": contact.get("title"),
            },
        ))

    mailing_address = _contact_mailing_address(contact)
    if mailing_address:
        claims.append(_synthetic_claim(
            subject_type=public_subject_type,
            subject_id=contact_id,
            predicate="has_mailing_address",
            object_type="mailing_address",
            object_id=stable_claim_id("hpd_contact_address", contact_id, mailing_address),
            normalized_value=mailing_address,
            claim_type="mailing_address",
            supporting_sources=["hpd_contacts"],
            observed_at=contact.get("observed_at"),
            rationale={
                "bbl": bbl,
                "contact_type": contact.get("contact_type"),
                "display_name": display_name,
            },
        ))

    return claims


async def load_synthetic_person_claims(session: AsyncSession, *, person_id: str, limit: int = 50) -> list[dict[str, Any]] | None:
    person_key = _person_key(person_id)
    if not person_key:
        return None

    contact_rows = await session.execute(
        text("""
            SELECT
                id,
                bbl,
                registration_contact_id,
                registration_id,
                contact_type,
                description,
                corporation_name,
                first_name,
                last_name,
                title,
                business_address,
                business_city,
                business_state,
                business_zip,
                updated_at AS observed_at
            FROM building_contacts
            WHERE LOWER(REGEXP_REPLACE(TRIM(CONCAT_WS(' ', first_name, last_name)), '\\s+', ' ', 'g')) = :person_key
              AND NULLIF(TRIM(CONCAT_WS(' ', first_name, last_name)), '') IS NOT NULL
            ORDER BY updated_at DESC NULLS LAST, id DESC
            LIMIT :limit
        """),
        {"person_key": person_key, "limit": limit},
    )
    contacts = [dict(row._mapping) for row in contact_rows]

    enrichment_rows = await session.execute(
        text("""
            SELECT id, lead_id, source, owner_principal, fetched_at AS observed_at
            FROM enrichment_results
            WHERE LOWER(REGEXP_REPLACE(TRIM(COALESCE(owner_principal, '')), '\\s+', ' ', 'g')) = :person_key
            ORDER BY fetched_at DESC NULLS LAST, id DESC
            LIMIT :limit
        """),
        {"person_key": person_key, "limit": limit},
    )
    enrichment_observations = [dict(row._mapping) for row in enrichment_rows]

    if not contacts and not enrichment_observations:
        return None

    display_name = contacts and _contact_display_name(contacts[0]) or str(enrichment_observations[0].get("owner_principal") or person_id)
    latest_observed_at = next(
        (row.get("observed_at") for row in [*contacts, *enrichment_observations] if row.get("observed_at")),
        None,
    )
    addresses = sorted({
        address
        for address in (_contact_mailing_address(contact) for contact in contacts)
        if address
    })
    bbls = sorted({str(contact.get("bbl")) for contact in contacts if contact.get("bbl")})
    source_names = sorted({
        str(row.get("source") or "enrichment").strip().lower()
        for row in enrichment_observations
        if row.get("source")
    })
    supporting_sources = ["hpd_contacts"] if contacts else []
    supporting_sources.extend(source_names or (["enrichment"] if enrichment_observations else []))
    ambiguity_count = 1 if len(addresses) > 1 or len({str(contact.get("id")) for contact in contacts}) > 1 else 0

    claims: list[dict[str, Any]] = [
        _synthetic_claim(
            subject_type="person",
            subject_id=person_id,
            predicate="identified_by_person_name",
            object_type="person_name",
            object_id=f"person:{person_key}",
            normalized_value=display_name,
            claim_type="person_contact",
            supporting_sources=supporting_sources or ["hpd_contacts"],
            contradicting_sources=["hpd_contacts"] if ambiguity_count else [],
            observed_at=latest_observed_at,
            supporting_evidence_count=len(contacts) + len(enrichment_observations),
            contradicting_evidence_count=ambiguity_count,
            rationale={
                "person_key": person_key,
                "hpd_contact_count": len(contacts),
                "enrichment_observation_count": len(enrichment_observations),
                "distinct_address_count": len(addresses),
                "bbls": bbls[:50],
                "why_contradicting": "The same person name appears on multiple HPD contact records or mailing addresses; treat identity as ambiguous." if ambiguity_count else None,
            },
        )
    ]

    for contact in contacts[: max(0, limit - len(claims))]:
        contact_name = _contact_display_name(contact)
        claims.append(_synthetic_claim(
            subject_type="person",
            subject_id=person_id,
            predicate="associated_with_building",
            object_type="building",
            object_id=str(contact.get("bbl")),
            normalized_value=f"{contact_name} ({contact.get('contact_type') or 'contact'})",
            claim_type="person_contact",
            supporting_sources=["hpd_contacts"],
            observed_at=contact.get("observed_at"),
            rationale={
                "hpd_contact_id": contact.get("id"),
                "contact_type": contact.get("contact_type"),
                "registration_id": contact.get("registration_id"),
                "registration_contact_id": contact.get("registration_contact_id"),
                "title": contact.get("title"),
                "mailing_address": _contact_mailing_address(contact),
            },
        ))

    remaining = max(0, limit - len(claims))
    for row in enrichment_observations[:remaining]:
        source_name = str(row.get("source") or "enrichment").strip().lower() or "enrichment"
        claims.append(_synthetic_claim(
            subject_type="person",
            subject_id=person_id,
            predicate="observed_as_owner_principal",
            object_type="lead",
            object_id=str(row.get("lead_id")),
            normalized_value=str(row.get("owner_principal") or display_name),
            claim_type="person_contact",
            supporting_sources=[source_name],
            observed_at=row.get("observed_at"),
            rationale={"enrichment_result_id": row.get("id"), "source": source_name},
        ))

    return claims[:limit]


async def load_synthetic_canonical_entity_claims(
    session: AsyncSession,
    *,
    canonical_entity_id: str,
    public_subject_type: str = "canonical_entity",
    limit: int = 50,
) -> list[dict[str, Any]] | None:
    entity_row = (await session.execute(
        text("""
            SELECT canonical_entity_id, normalized_name, display_name, entity_type, status, confidence_score, updated_at
            FROM canonical_entities
            WHERE canonical_entity_id = :cid
        """),
        {"cid": canonical_entity_id},
    )).first()
    if not entity_row:
        return None

    entity = dict(entity_row._mapping)
    display_name = entity.get("display_name") or entity.get("normalized_name") or canonical_entity_id
    claims: list[dict[str, Any]] = [
        _synthetic_claim(
            subject_type=public_subject_type,
            subject_id=canonical_entity_id,
            predicate="has_canonical_identity",
            object_type="canonical_entity",
            object_id=canonical_entity_id,
            normalized_value=str(display_name),
            claim_type="entity_identity",
            supporting_sources=["canonical_entity_leads"],
            observed_at=entity.get("updated_at"),
            rationale={
                "entity_type": entity.get("entity_type"),
                "status": entity.get("status"),
                "stored_confidence_score": entity.get("confidence_score"),
            },
        )
    ]

    alias_row = (await session.execute(
        text("""
            SELECT
                COUNT(*)::int AS alias_count,
                ARRAY_REMOVE(ARRAY_AGG(alias_name ORDER BY confidence_score DESC NULLS LAST, alias_name ASC), NULL) AS alias_names,
                MAX(updated_at) AS observed_at
            FROM canonical_entity_aliases
            WHERE canonical_entity_id = :cid
        """),
        {"cid": canonical_entity_id},
    )).first()
    if alias_row:
        aliases = dict(alias_row._mapping)
        alias_count = int(aliases.get("alias_count") or 0)
        if alias_count:
            alias_names = [str(name) for name in (aliases.get("alias_names") or []) if name]
            claims.append(_synthetic_claim(
                subject_type=public_subject_type,
                subject_id=canonical_entity_id,
                predicate="has_aliases",
                object_type="alias_set",
                object_id=f"canonical_entity:{canonical_entity_id}:aliases",
                normalized_value=", ".join(alias_names[:5]),
                claim_type="entity_identity",
                supporting_sources=["canonical_entity_aliases"],
                observed_at=aliases.get("observed_at"),
                supporting_evidence_count=alias_count,
                rationale={"alias_count": alias_count, "alias_names": alias_names[:20]},
            ))

    lead_row = (await session.execute(
        text("""
            SELECT
                COUNT(*)::int AS lead_count,
                COUNT(*) FILTER (WHERE COALESCE(is_primary, false) = true)::int AS primary_count,
                COUNT(*) FILTER (WHERE COALESCE(relationship_type, '') <> 'keeper')::int AS non_keeper_count,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT cel.lead_id), NULL) AS lead_ids,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT COALESCE(NULLIF(l.company_name, ''), NULLIF(l.agent_name, ''), NULLIF(l.owner_name, ''), cel.lead_id)), NULL) AS lead_names,
                MIN(COALESCE(cel.confidence_score, 0)) AS min_confidence,
                MAX(COALESCE(cel.updated_at, l.updated_at)) AS observed_at
            FROM canonical_entity_leads cel
            LEFT JOIN leads l ON l.lead_id = cel.lead_id
            WHERE cel.canonical_entity_id = :cid
        """),
        {"cid": canonical_entity_id},
    )).first()
    if lead_row:
        membership = dict(lead_row._mapping)
        lead_count = int(membership.get("lead_count") or 0)
        non_keeper_count = int(membership.get("non_keeper_count") or 0)
        min_confidence = float(membership.get("min_confidence") or 0)
        contradiction_count = 1 if lead_count > 1 and (non_keeper_count > 0 or min_confidence < 0.75) else 0
        if lead_count:
            lead_names = [str(name) for name in (membership.get("lead_names") or []) if name]
            claims.append(_synthetic_claim(
                subject_type=public_subject_type,
                subject_id=canonical_entity_id,
                predicate="has_lead_memberships",
                object_type="lead_set",
                object_id=f"canonical_entity:{canonical_entity_id}:leads",
                normalized_value=", ".join(lead_names[:5]) or f"{lead_count} lead membership(s)",
                claim_type="entity_identity",
                supporting_sources=["canonical_entity_leads"],
                contradicting_sources=["canonical_entity_leads"] if contradiction_count else [],
                observed_at=membership.get("observed_at"),
                supporting_evidence_count=lead_count,
                contradicting_evidence_count=contradiction_count,
                rationale={
                    "lead_count": lead_count,
                    "primary_count": int(membership.get("primary_count") or 0),
                    "non_keeper_count": non_keeper_count,
                    "lead_ids": membership.get("lead_ids") or [],
                    "lead_names": lead_names[:20],
                    "min_membership_confidence": min_confidence,
                    "why_contradicting": "Multiple weak or candidate lead memberships require review." if contradiction_count else None,
                },
            ))

    building_row = (await session.execute(
        text("""
            SELECT
                COUNT(*)::int AS building_count,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT bbl), NULL) AS bbls,
                MIN(COALESCE(confidence_score, 0)) AS min_confidence,
                MAX(updated_at) AS observed_at
            FROM canonical_entity_buildings
            WHERE canonical_entity_id = :cid
        """),
        {"cid": canonical_entity_id},
    )).first()
    if building_row:
        buildings = dict(building_row._mapping)
        building_count = int(buildings.get("building_count") or 0)
        if building_count:
            bbls = [str(bbl) for bbl in (buildings.get("bbls") or []) if bbl]
            claims.append(_synthetic_claim(
                subject_type=public_subject_type,
                subject_id=canonical_entity_id,
                predicate="has_building_memberships",
                object_type="building_set",
                object_id=f"canonical_entity:{canonical_entity_id}:buildings",
                normalized_value=f"{building_count} building link(s)",
                claim_type="building_management",
                supporting_sources=["canonical_entity_buildings"],
                observed_at=buildings.get("observed_at"),
                supporting_evidence_count=building_count,
                rationale={
                    "building_count": building_count,
                    "bbls": bbls[:50],
                    "min_membership_confidence": float(buildings.get("min_confidence") or 0),
                },
            ))

    proposal_row = (await session.execute(
        text("""
            SELECT
                COUNT(*)::int AS proposal_count,
                COUNT(*) FILTER (WHERE COALESCE(safe_to_execute, false) = false)::int AS unsafe_count,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT bucket), NULL) AS buckets,
                MAX(updated_at) AS observed_at
            FROM canonical_entity_match_proposals
            WHERE canonical_entity_id = :cid
              AND COALESCE(proposal_status, 'proposed') = 'proposed'
        """),
        {"cid": canonical_entity_id},
    )).first()
    if proposal_row:
        proposals = dict(proposal_row._mapping)
        proposal_count = int(proposals.get("proposal_count") or 0)
        unsafe_count = int(proposals.get("unsafe_count") or 0)
        if proposal_count:
            claims.append(_synthetic_claim(
                subject_type=public_subject_type,
                subject_id=canonical_entity_id,
                predicate="has_open_match_proposals",
                object_type="review_queue",
                object_id=f"canonical_entity:{canonical_entity_id}:proposals",
                normalized_value=f"{proposal_count} open canonical proposal(s)",
                claim_type="entity_identity",
                supporting_sources=["canonical_entity_match_proposals"],
                contradicting_sources=["canonical_entity_match_proposals"] if unsafe_count else [],
                observed_at=proposals.get("observed_at"),
                supporting_evidence_count=proposal_count,
                contradicting_evidence_count=unsafe_count,
                rationale={
                    "proposal_count": proposal_count,
                    "unsafe_count": unsafe_count,
                    "buckets": proposals.get("buckets") or [],
                    "why_contradicting": "One or more open canonical proposals are not safe to execute automatically." if unsafe_count else None,
                },
            ))

    return claims[:limit]


async def build_subject_truth_summary(
    session: AsyncSession,
    *,
    subject_type: str,
    subject_id: str,
    include_persisted_claims: bool = True,
    limit: int = 50,
) -> dict[str, Any] | None:
    normalized_subject_type = subject_type.strip().lower()
    if normalized_subject_type == "lead":
        summary = await build_lead_truth_summary(
            session,
            subject_id,
            include_persisted_claims=include_persisted_claims,
        )
        if summary is None:
            return None
        outreach_claims = await load_synthetic_outreach_feedback_claims(
            session,
            subject_type="lead",
            subject_id=subject_id,
            limit=limit,
        )
        if outreach_claims:
            claims = [*outreach_claims, *summary["claims"]][:limit]
            confidence_scores = [float(c["confidence_score"]) for c in claims if c.get("confidence_score") is not None]
            contradictions = sum(int(c.get("contradicting_evidence_count") or 0) for c in claims)
            freshness_values = [int(c["freshness_days"]) for c in claims if c.get("freshness_days") is not None]
            safe_actions = sorted({
                str(c.get("actionability_level"))
                for c in claims
                if c.get("actionability_level") and c.get("actionability_level") != "do_not_act"
            })
            summary["claims"] = claims
            summary["overall_confidence_score"] = round(sum(confidence_scores) / len(confidence_scores), 3) if confidence_scores else 0.0
            summary["belief_summary"] = _belief_summary_from_claims(
                claims,
                what_we_believe=[
                    *_claim_sentences_for_claims(outreach_claims),
                    *summary["belief_summary"]["what_we_believe"],
                ][:10],
                contradictions=contradictions,
                freshness_days=min(freshness_values) if freshness_values else None,
                safe_actions=safe_actions,
            )
            summary["review_bucket"] = review_bucket(
                confidence_score=summary["overall_confidence_score"],
                contradictions=contradictions,
                safe_to_execute=False,
            )
        return summary

    claims = []
    synthetic_claims: list[dict[str, Any]] = []
    if normalized_subject_type == "building":
        synthetic_claims_result = await load_synthetic_building_claims(session, bbl=subject_id, limit=limit)
        if synthetic_claims_result is None:
            return None
        synthetic_claims = synthetic_claims_result
    elif normalized_subject_type in {"contact", "hpd_contact"}:
        public_subject_type = "contact" if normalized_subject_type == "contact" else "hpd_contact"
        synthetic_claims_result = await load_synthetic_hpd_contact_claims(
            session,
            contact_id=subject_id,
            public_subject_type=public_subject_type,
        )
        if synthetic_claims_result is None:
            return None
        synthetic_claims = synthetic_claims_result
    elif normalized_subject_type in {"canonical_entity", "entity"}:
        synthetic_claims_result = await load_synthetic_canonical_entity_claims(
            session,
            canonical_entity_id=subject_id,
            public_subject_type=normalized_subject_type,
            limit=limit,
        )
        if synthetic_claims_result is None:
            return None
        synthetic_claims = synthetic_claims_result
    elif normalized_subject_type == "person":
        synthetic_claims_result = await load_synthetic_person_claims(session, person_id=subject_id, limit=limit)
        if synthetic_claims_result is None:
            return None
        synthetic_claims = synthetic_claims_result

    synthetic_claims = [
        *await load_synthetic_outreach_feedback_claims(
            session,
            subject_type=normalized_subject_type,
            subject_id=subject_id,
            limit=limit,
        ),
        *synthetic_claims,
    ][:limit]

    if include_persisted_claims:
        persisted_subject_type = "hpd_contact" if normalized_subject_type == "contact" else normalized_subject_type
        claims = await load_subject_claims(
            session,
            subject_type=persisted_subject_type,
            subject_id=subject_id,
            limit=limit,
        )
    claims = [*synthetic_claims, *claims][:limit]

    confidence_scores = [float(c["confidence_score"]) for c in claims if c.get("confidence_score") is not None]
    overall_score = round(sum(confidence_scores) / len(confidence_scores), 3) if confidence_scores else 0.0
    contradictions = sum(int(c.get("contradicting_evidence_count") or 0) for c in claims)
    freshness_values = [int(c["freshness_days"]) for c in claims if c.get("freshness_days") is not None]
    freshness_days = min(freshness_values) if freshness_values else None
    safe_actions = sorted({
        str(c.get("actionability_level"))
        for c in claims
        if c.get("actionability_level") and c.get("actionability_level") != "do_not_act"
    })

    return {
        "subject_type": normalized_subject_type,
        "subject_id": subject_id,
        "overall_confidence_score": overall_score,
        "belief_summary": _belief_summary_from_claims(
            claims,
            what_we_believe=[_claim_belief_sentence(claim) for claim in claims[:10]]
            or [f"No verified claim ledger entries are materialized for this {normalized_subject_type} yet."],
            contradictions=contradictions,
            freshness_days=freshness_days,
            safe_actions=safe_actions,
        ),
        "claims": claims,
        "review_bucket": review_bucket(
            confidence_score=overall_score,
            contradictions=contradictions,
            safe_to_execute=bool(claims and contradictions == 0),
        ),
    }


async def build_lead_truth_summary(session: AsyncSession, lead_id: str, *, include_persisted_claims: bool = True) -> dict[str, Any] | None:
    lead_row = (await session.execute(
        text("""
            SELECT
                lead_id,
                COALESCE(NULLIF(company_name, ''), NULLIF(agent_name, ''), NULLIF(owner_name, ''), NULLIF(primary_contact, ''), lead_id) AS display_name,
                phone,
                email,
                website,
                enrichment_status,
                last_enriched,
                updated_at
            FROM leads
            WHERE lead_id = :lead_id
        """),
        {"lead_id": lead_id},
    )).first()
    if not lead_row:
        return None
    lead = dict(lead_row._mapping)

    canonical_row = (await session.execute(
        text("""
            SELECT
                ce.canonical_entity_id,
                ce.display_name,
                ce.normalized_name,
                ce.confidence_score,
                cel.relationship_type,
                cel.confidence_score AS membership_confidence
            FROM canonical_entity_leads cel
            JOIN canonical_entities ce ON ce.canonical_entity_id = cel.canonical_entity_id
            WHERE cel.lead_id = :lead_id
            ORDER BY cel.is_primary DESC, cel.confidence_score DESC NULLS LAST, ce.updated_at DESC NULLS LAST
            LIMIT 1
        """),
        {"lead_id": lead_id},
    )).first()
    canonical = dict(canonical_row._mapping) if canonical_row else None

    link_row = (await session.execute(
        text("""
            SELECT COUNT(DISTINCT bm.bbl)::int AS linked_buildings, MAX(bm.updated_at) AS last_linked_at
            FROM building_management bm
            WHERE bm.lead_id = :lead_id AND bm.is_current = true
        """),
        {"lead_id": lead_id},
    )).first()
    linked_buildings = int(link_row.linked_buildings or 0) if link_row else 0
    last_linked_at = link_row.last_linked_at if link_row else None

    conflict_row = (await session.execute(
        text("""
            WITH lead_links AS (
                SELECT DISTINCT bbl
                FROM building_management
                WHERE lead_id = :lead_id AND is_current = true
            )
            SELECT COUNT(*)::int AS conflicting_buildings
            FROM (
                SELECT bm.bbl
                FROM building_management bm
                JOIN lead_links ll ON ll.bbl = bm.bbl
                WHERE bm.is_current = true
                  AND bm.lead_id <> :lead_id
                GROUP BY bm.bbl
            ) conflicts
        """),
        {"lead_id": lead_id},
    )).first()
    conflicting_buildings = int(conflict_row.conflicting_buildings or 0) if conflict_row else 0

    updated_at = lead.get("last_enriched") or lead.get("updated_at")
    freshness_days = None
    if updated_at:
        dt = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=timezone.utc)
        freshness_days = (datetime.now(timezone.utc) - dt).days

    synthetic_claims = []
    management_eval = compute_confidence(ConfidenceInput(
        claim_type="building_management",
        supporting_sources=["building_management", "hpd_contacts"] if linked_buildings else [],
        contradicting_sources=["building_management"] * min(conflicting_buildings, 3),
        freshness_days=freshness_days,
        source_agreement_count=2 if linked_buildings else 0,
        source_disagreement_count=conflicting_buildings,
    ))
    synthetic_claims.append({
        "claim_id": stable_claim_id("lead", lead_id, "manages_buildings"),
        "subject_type": "lead",
        "subject_id": lead_id,
        "predicate": "manages_buildings",
        "object_type": "building_set",
        "object_id": f"lead:{lead_id}:current_buildings",
        "normalized_value": str(linked_buildings),
        "claim_type": "building_management",
        "supporting_evidence_count": linked_buildings,
        "contradicting_evidence_count": conflicting_buildings,
        "supporting_sources": ["building_management", "hpd_contacts"] if linked_buildings else [],
        "contradicting_sources": ["building_management"] if conflicting_buildings else [],
        "observed_at": serialize_dt(last_linked_at),
        **management_eval,
    })

    contact_sources = []
    if lead.get("phone"):
        contact_sources.append("hpd_contacts")
    if lead.get("email"):
        contact_sources.append("enrichment")
    if lead.get("website"):
        contact_sources.append("company_website")
    contact_eval = compute_confidence(ConfidenceInput(
        claim_type="person_contact",
        supporting_sources=contact_sources,
        contradicting_sources=[],
        freshness_days=freshness_days,
        source_agreement_count=len(contact_sources),
    ))
    synthetic_claims.append({
        "claim_id": stable_claim_id("lead", lead_id, "has_contact_path"),
        "subject_type": "lead",
        "subject_id": lead_id,
        "predicate": "has_contact_path",
        "object_type": "contact_set",
        "object_id": f"lead:{lead_id}:contacts",
        "normalized_value": ", ".join([key for key in ["phone", "email", "website"] if lead.get(key)]) or "none",
        "claim_type": "person_contact",
        "supporting_evidence_count": len(contact_sources),
        "contradicting_evidence_count": 0,
        "supporting_sources": contact_sources,
        "contradicting_sources": [],
        "observed_at": serialize_dt(updated_at),
        **contact_eval,
    })

    if canonical:
        entity_sources = ["canonical_entity_leads"]
        entity_eval = compute_confidence(ConfidenceInput(
            claim_type="entity_identity",
            supporting_sources=entity_sources,
            contradicting_sources=[],
            freshness_days=freshness_days,
            source_agreement_count=1,
        ))
        synthetic_claims.append({
            "claim_id": stable_claim_id("lead", lead_id, "maps_to_canonical_entity", canonical["canonical_entity_id"]),
            "subject_type": "lead",
            "subject_id": lead_id,
            "predicate": "maps_to_canonical_entity",
            "object_type": "canonical_entity",
            "object_id": canonical["canonical_entity_id"],
            "normalized_value": canonical.get("display_name") or canonical.get("normalized_name"),
            "claim_type": "entity_identity",
            "supporting_evidence_count": 1,
            "contradicting_evidence_count": 0,
            "supporting_sources": entity_sources,
            "contradicting_sources": [],
            "observed_at": serialize_dt(lead.get("updated_at")),
            **entity_eval,
        })

    persisted_claims = []
    if include_persisted_claims:
        persisted_claims = await load_persisted_claims(
            session,
            lead_id=lead_id,
            canonical_entity_id=canonical["canonical_entity_id"] if canonical else None,
        )
    claims = [*synthetic_claims, *persisted_claims]
    confidence_scores = [float(c["confidence_score"]) for c in claims if c.get("confidence_score") is not None]
    overall_score = round(sum(confidence_scores) / len(confidence_scores), 3) if confidence_scores else 0.0
    contradictions = sum(int(c.get("contradicting_evidence_count") or 0) for c in claims)
    safe_actions = sorted({str(c.get("actionability_level")) for c in claims if c.get("actionability_level") and c.get("actionability_level") != "do_not_act"})

    return {
        "lead_id": lead_id,
        "entity_name": lead.get("display_name"),
        "canonical_entity": canonical,
        "overall_confidence_score": overall_score,
        "belief_summary": _belief_summary_from_claims(
            claims,
            what_we_believe=[
                f"{lead.get('display_name')} has {linked_buildings} current linked building(s)." if linked_buildings else "No current building-management link is verified yet.",
                "A direct contact path exists." if contact_sources else "No direct contact path is verified yet.",
                "A canonical entity membership exists." if canonical else "No canonical entity membership is materialized yet.",
            ],
            contradictions=contradictions,
            freshness_days=freshness_days,
            safe_actions=safe_actions,
        ),
        "claims": claims,
        "review_bucket": review_bucket(
            confidence_score=overall_score,
            contradictions=contradictions,
            safe_to_execute=bool(canonical and linked_buildings and contradictions == 0),
        ),
    }


async def preview_adversarial_validation(session: AsyncSession, sample_limit: int = 20) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    zero_link_rows = await session.execute(
        text("""
            SELECT l.lead_id, COALESCE(NULLIF(l.company_name, ''), NULLIF(l.agent_name, ''), l.lead_id) AS name
            FROM leads l
            WHERE l.retired_at IS NULL
              AND NOT EXISTS (
                SELECT 1 FROM building_management bm
                WHERE bm.lead_id = l.lead_id AND bm.is_current = true
              )
            ORDER BY l.updated_at DESC NULLS LAST
            LIMIT :limit
        """),
        {"limit": sample_limit},
    )
    zero_link = [dict(row._mapping) for row in zero_link_rows]
    if zero_link:
        checks.append({
            "check": "zero_current_building_links",
            "severity": "high",
            "count_sampled": len(zero_link),
            "why_it_matters": "Lead rows without active building links cannot support sourcing or diligence claims.",
            "sample": zero_link,
            "recommended_queue": "insufficient_evidence",
        })

    conflict_rows = await session.execute(
        text("""
            SELECT bm.bbl, COUNT(DISTINCT bm.lead_id)::int AS lead_count, ARRAY_AGG(DISTINCT bm.lead_id) AS lead_ids
            FROM building_management bm
            WHERE bm.is_current = true
            GROUP BY bm.bbl, COALESCE(bm.role, '')
            HAVING COUNT(DISTINCT bm.lead_id) > 1
            ORDER BY lead_count DESC, bm.bbl ASC
            LIMIT :limit
        """),
        {"limit": sample_limit},
    )
    conflicts = [dict(row._mapping) for row in conflict_rows]
    if conflicts:
        checks.append({
            "check": "duplicate_current_role_links",
            "severity": "critical",
            "count_sampled": len(conflicts),
            "why_it_matters": "A building should not have multiple current managers for the same role without explicit evidence.",
            "sample": conflicts,
            "recommended_queue": "conflicting_evidence",
        })

    stale_rows = await session.execute(
        text("""
            SELECT lead_id, COALESCE(NULLIF(company_name, ''), NULLIF(agent_name, ''), lead_id) AS name, updated_at
            FROM leads
            WHERE retired_at IS NULL
              AND updated_at < NOW() - INTERVAL '180 days'
            ORDER BY updated_at ASC NULLS FIRST
            LIMIT :limit
        """),
        {"limit": sample_limit},
    )
    stale = [dict(row._mapping) for row in stale_rows]
    if stale:
        checks.append({
            "check": "stale_high_level_records",
            "severity": "medium",
            "count_sampled": len(stale),
            "why_it_matters": "Old lead snapshots should be downgraded before recommended outreach or diligence.",
            "sample": [{**row, "updated_at": serialize_dt(row.get("updated_at"))} for row in stale],
            "recommended_queue": "needs_human_review",
        })

    dissolved_rows = await session.execute(
        text("""
            SELECT
                lead_id,
                COALESCE(NULLIF(company_name, ''), NULLIF(agent_name, ''), NULLIF(owner_name, ''), lead_id) AS name,
                dos_status,
                updated_at
            FROM leads
            WHERE retired_at IS NULL
              AND NULLIF(dos_status, '') IS NOT NULL
              AND LOWER(dos_status) NOT IN ('active', 'current', 'good standing')
            ORDER BY updated_at DESC NULLS LAST, lead_id
            LIMIT :limit
        """),
        {"limit": sample_limit},
    )
    dissolved = [dict(row._mapping) for row in dissolved_rows]
    if dissolved:
        checks.append({
            "check": "non_active_dos_entities",
            "severity": "high",
            "count_sampled": len(dissolved),
            "why_it_matters": "A dissolved, inactive, or suspended entity should not be treated as an acquisition-quality operating company without newer contradictory evidence.",
            "sample": [{**row, "updated_at": serialize_dt(row.get("updated_at"))} for row in dissolved],
            "recommended_queue": "conflicting_evidence",
        })

    mailbox_rows = await session.execute(
        text("""
            SELECT
                bc.bbl,
                bc.contact_type,
                bc.corporation_name,
                bc.business_address,
                bc.business_city,
                bc.business_state,
                COUNT(DISTINCT bm.lead_id)::int AS linked_lead_count,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT bm.lead_id), NULL) AS lead_ids
            FROM building_contacts bc
            LEFT JOIN building_management bm ON bm.bbl = bc.bbl AND bm.is_current = true
            WHERE bc.contact_type IN ('Agent', 'ManagementCompany', 'CorporateOwner', 'Owner')
              AND (
                COALESCE(bc.business_address, '') ILIKE '%P.O.%'
                OR COALESCE(bc.business_address, '') ILIKE '%PO BOX%'
                OR COALESCE(bc.business_address, '') ILIKE '%C/O%'
                OR COALESCE(bc.business_address, '') ILIKE '%CARE OF%'
                OR COALESCE(bc.corporation_name, '') ILIKE '% LAW %'
                OR COALESCE(bc.corporation_name, '') ILIKE '%ATTORNEY%'
                OR COALESCE(bc.corporation_name, '') ILIKE '%LEGAL%'
              )
            GROUP BY bc.bbl, bc.contact_type, bc.corporation_name, bc.business_address, bc.business_city, bc.business_state
            ORDER BY linked_lead_count DESC, bc.bbl
            LIMIT :limit
        """),
        {"limit": sample_limit},
    )
    mailboxes = [dict(row._mapping) for row in mailbox_rows]
    if mailboxes:
        checks.append({
            "check": "legal_mailbox_or_agent_addresses",
            "severity": "medium",
            "count_sampled": len(mailboxes),
            "why_it_matters": "Law-firm, care-of, and mailbox addresses often indicate a registered agent or legal mailing address, not the operating manager.",
            "sample": mailboxes,
            "recommended_queue": "needs_human_review",
        })

    role_rows = await session.execute(
        text("""
            SELECT
                bm.bbl,
                COUNT(DISTINCT bm.lead_id)::int AS lead_count,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT bm.lead_id), NULL) AS lead_ids,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT COALESCE(bm.role, 'unknown')), NULL) AS roles
            FROM building_management bm
            WHERE bm.is_current = true
            GROUP BY bm.bbl
            HAVING BOOL_OR(LOWER(COALESCE(bm.role, '')) IN ('owner', 'corporateowner', 'individualowner'))
               AND BOOL_OR(LOWER(COALESCE(bm.role, '')) IN ('agent', 'manager', 'managementcompany', 'management'))
            ORDER BY lead_count DESC, bm.bbl
            LIMIT :limit
        """),
        {"limit": sample_limit},
    )
    role_ambiguity = [dict(row._mapping) for row in role_rows]
    if role_ambiguity:
        checks.append({
            "check": "owner_manager_role_ambiguity",
            "severity": "high",
            "count_sampled": len(role_ambiguity),
            "why_it_matters": "Owner, sponsor, registered agent, and operating manager roles need separate claims before outreach or diligence.",
            "sample": role_ambiguity,
            "recommended_queue": "conflicting_evidence",
        })

    stale_enrichment_rows = await session.execute(
        text("""
            SELECT
                lead_id,
                COALESCE(NULLIF(company_name, ''), NULLIF(agent_name, ''), lead_id) AS name,
                phone,
                email,
                website,
                last_enriched
            FROM leads
            WHERE retired_at IS NULL
              AND (NULLIF(phone, '') IS NOT NULL OR NULLIF(email, '') IS NOT NULL OR NULLIF(website, '') IS NOT NULL)
              AND (last_enriched IS NULL OR last_enriched < NOW() - INTERVAL '180 days')
            ORDER BY last_enriched ASC NULLS FIRST, lead_id
            LIMIT :limit
        """),
        {"limit": sample_limit},
    )
    stale_enrichment = [dict(row._mapping) for row in stale_enrichment_rows]
    if stale_enrichment:
        checks.append({
            "check": "stale_contact_or_website_evidence",
            "severity": "medium",
            "count_sampled": len(stale_enrichment),
            "why_it_matters": "Old phone, email, and website evidence should be downgraded before recommended outreach.",
            "sample": [{**row, "last_enriched": serialize_dt(row.get("last_enriched"))} for row in stale_enrichment],
            "recommended_queue": "needs_human_review",
        })

    conflicting_enrichment_rows = await session.execute(
        text("""
            WITH observations AS (
                SELECT
                    lead_id,
                    'phone' AS field,
                    REGEXP_REPLACE(COALESCE(phone, ''), '\\D', '', 'g') AS normalized_value,
                    COALESCE(NULLIF(source, ''), 'enrichment') AS source,
                    fetched_at
                FROM enrichment_results
                WHERE NULLIF(TRIM(COALESCE(phone, '')), '') IS NOT NULL

                UNION ALL

                SELECT
                    lead_id,
                    'email' AS field,
                    LOWER(TRIM(COALESCE(email, ''))) AS normalized_value,
                    COALESCE(NULLIF(source, ''), 'enrichment') AS source,
                    fetched_at
                FROM enrichment_results
                WHERE NULLIF(TRIM(COALESCE(email, '')), '') IS NOT NULL

                UNION ALL

                SELECT
                    lead_id,
                    'website' AS field,
                    LOWER(REGEXP_REPLACE(REGEXP_REPLACE(TRIM(COALESCE(website, '')), '^https?://(www\\.)?', '', 'i'), '/+$', '')) AS normalized_value,
                    COALESCE(NULLIF(source, ''), 'enrichment') AS source,
                    fetched_at
                FROM enrichment_results
                WHERE NULLIF(TRIM(COALESCE(website, '')), '') IS NOT NULL
            )
            SELECT
                lead_id,
                field,
                COUNT(DISTINCT normalized_value)::int AS distinct_value_count,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT normalized_value), NULL) AS values,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT source), NULL) AS sources,
                MAX(fetched_at) AS last_observed_at
            FROM observations
            WHERE NULLIF(normalized_value, '') IS NOT NULL
            GROUP BY lead_id, field
            HAVING COUNT(DISTINCT normalized_value) > 1
            ORDER BY distinct_value_count DESC, lead_id, field
            LIMIT :limit
        """),
        {"limit": sample_limit},
    )
    conflicting_enrichment = [dict(row._mapping) for row in conflicting_enrichment_rows]
    if conflicting_enrichment:
        checks.append({
            "check": "conflicting_enrichment_observations",
            "severity": "high",
            "count_sampled": len(conflicting_enrichment),
            "why_it_matters": "Different enrichment sources disagreeing on phone, email, or website should block recommended outreach until a current contact path is reviewed.",
            "sample": [{**row, "last_observed_at": serialize_dt(row.get("last_observed_at"))} for row in conflicting_enrichment],
            "recommended_queue": "conflicting_evidence",
        })

    outreach_contradiction_rows = await session.execute(
        text("""
            SELECT
                oe.lead_id,
                COALESCE(NULLIF(l.company_name, ''), NULLIF(l.agent_name, ''), NULLIF(l.owner_name, ''), oe.lead_id) AS name,
                oe.bbl,
                oe.canonical_entity_id,
                oe.method,
                oe.stage,
                oe.outcome,
                oe.notes,
                oe.event_timestamp
            FROM outreach_events oe
            LEFT JOIN leads l ON l.lead_id = oe.lead_id
            WHERE LOWER(COALESCE(oe.outcome, '')) IN (
                'wrong_number',
                'bounced',
                'bad_email',
                'disconnected',
                'does_not_manage',
                'wrong_manager',
                'not_the_manager'
            )
               OR COALESCE(oe.notes, '') ILIKE '%wrong manager%'
               OR COALESCE(oe.notes, '') ILIKE '%does not manage%'
               OR COALESCE(oe.notes, '') ILIKE '%do not manage%'
               OR COALESCE(oe.notes, '') ILIKE '%not the manager%'
               OR COALESCE(oe.notes, '') ILIKE '%wrong number%'
            ORDER BY oe.event_timestamp DESC NULLS LAST, oe.updated_at DESC NULLS LAST
            LIMIT :limit
        """),
        {"limit": sample_limit},
    )
    outreach_contradictions = [dict(row._mapping) for row in outreach_contradiction_rows]
    if outreach_contradictions:
        checks.append({
            "check": "outreach_contradicted_relationships",
            "severity": "critical",
            "count_sampled": len(outreach_contradictions),
            "why_it_matters": "Direct outreach saying a contact path is wrong or an entity does not manage a building should override stale source assumptions before any next action.",
            "sample": [{**row, "event_timestamp": serialize_dt(row.get("event_timestamp"))} for row in outreach_contradictions],
            "recommended_queue": "conflicting_evidence",
        })

    possible_false_merge_rows = await session.execute(
        text("""
            SELECT
                ce.canonical_entity_id,
                ce.display_name AS canonical_name,
                COUNT(DISTINCT cel.lead_id)::int AS lead_count,
                COUNT(DISTINCT COALESCE(NULLIF(l.normalized_name, ''), NULLIF(l.company_name, ''), NULLIF(l.agent_name, ''), l.lead_id))::int AS distinct_name_count,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT cel.lead_id), NULL) AS lead_ids,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT COALESCE(NULLIF(l.company_name, ''), NULLIF(l.agent_name, ''), NULLIF(l.owner_name, ''), l.lead_id)), NULL) AS lead_names,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT COALESCE(cel.relationship_type, 'unknown')), NULL) AS relationship_types,
                MIN(COALESCE(cel.confidence_score, 0)) AS min_link_confidence,
                MAX(COALESCE(cel.confidence_score, 0)) AS max_link_confidence
            FROM canonical_entities ce
            JOIN canonical_entity_leads cel ON cel.canonical_entity_id = ce.canonical_entity_id
            JOIN leads l ON l.lead_id = cel.lead_id
            WHERE COALESCE(ce.status, 'proposed') <> 'retired'
              AND l.retired_at IS NULL
            GROUP BY ce.canonical_entity_id, ce.display_name
            HAVING COUNT(DISTINCT cel.lead_id) > 1
               AND COUNT(DISTINCT COALESCE(NULLIF(l.normalized_name, ''), NULLIF(l.company_name, ''), NULLIF(l.agent_name, ''), l.lead_id)) > 1
               AND (
                    MIN(COALESCE(cel.confidence_score, 0)) < 0.75
                 OR BOOL_OR(COALESCE(cel.relationship_type, 'candidate') <> 'primary')
               )
            ORDER BY lead_count DESC, min_link_confidence ASC, ce.display_name
            LIMIT :limit
        """),
        {"limit": sample_limit},
    )
    possible_false_merges = [dict(row._mapping) for row in possible_false_merge_rows]
    if possible_false_merges:
        checks.append({
            "check": "possible_false_canonical_merges",
            "severity": "high",
            "count_sampled": len(possible_false_merges),
            "why_it_matters": "A canonical entity containing multiple active lead names with weak or candidate-only memberships may be a false merge and should not become diligence-grade truth automatically.",
            "sample": possible_false_merges,
            "recommended_queue": "do_not_merge",
        })

    return {
        "dry_run": True,
        "run_type": "adversarial_truth_validation",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sample_limit": sample_limit,
        "checks": checks,
        "mutations_planned": 0,
        "rollback_strategy": "No mutation in preview mode. Execute mode must persist run_id, before/after counts, samples, and reversible review items.",
    }
