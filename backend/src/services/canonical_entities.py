"""Helpers for canonical entity proposal materialization."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def canonical_entity_id_for_name(normalized_name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"hpd-leads-app::{normalized_name}"))


def ensure_canonical_entity(
    session: Session,
    *,
    normalized_name: str,
    display_name: str | None,
    entity_type: str = "pm_company",
    profile: dict[str, Any] | None = None,
) -> str:
    canonical_entity_id = canonical_entity_id_for_name(normalized_name)
    session.execute(
        text(
            """
            INSERT INTO canonical_entities (
                canonical_entity_id, normalized_name, display_name, entity_type, status,
                confidence_score, profile, created_at, updated_at
            )
            VALUES (
                :canonical_entity_id, :normalized_name, :display_name, :entity_type, 'proposed',
                NULL, CAST(:profile AS JSONB), NOW(), NOW()
            )
            ON CONFLICT (normalized_name)
            DO UPDATE SET
                display_name = COALESCE(EXCLUDED.display_name, canonical_entities.display_name),
                entity_type = COALESCE(EXCLUDED.entity_type, canonical_entities.entity_type),
                profile = COALESCE(EXCLUDED.profile, canonical_entities.profile),
                updated_at = NOW()
            """
        ),
        {
            "canonical_entity_id": canonical_entity_id,
            "normalized_name": normalized_name,
            "display_name": display_name,
            "entity_type": entity_type,
            "profile": json.dumps(profile or {}),
        },
    )
    row = session.execute(
        text("SELECT canonical_entity_id FROM canonical_entities WHERE normalized_name = :normalized_name"),
        {"normalized_name": normalized_name},
    ).first()
    return str(row[0]) if row else canonical_entity_id


def sync_canonical_proposals(
    session: Session,
    *,
    prep_rows: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
) -> dict[str, int]:
    clusters_by_name = {
        str(cluster.get("canonical_name") or "").strip(): cluster
        for cluster in clusters
    }
    entities_synced = 0
    aliases_synced = 0
    lead_links_synced = 0
    building_links_synced = 0
    proposals_synced = 0

    for row in prep_rows:
        canonical_name = str(row.get("canonical_name") or "").strip()
        if not canonical_name:
            continue
        normalized_name = canonical_name.upper()
        canonical_entity_id = ensure_canonical_entity(
            session,
            normalized_name=normalized_name,
            display_name=canonical_name,
            profile={
                "bucket": row.get("bucket"),
                "signals": row.get("signals"),
                "candidate_lead_count": row.get("candidate_lead_count"),
            },
        )
        entities_synced += 1

        cluster = clusters_by_name.get(canonical_name) or {}
        alias_names = {canonical_name, *[str(name).strip() for name in cluster.get("all_names") or [] if str(name).strip()]}
        for alias_name in alias_names:
            normalized_alias = alias_name.upper()
            session.execute(
                text(
                    """
                    INSERT INTO canonical_entity_aliases (
                        canonical_entity_id, alias_name, normalized_alias, source, confidence_score, created_at, updated_at
                    )
                    VALUES (:canonical_entity_id, :alias_name, :normalized_alias, :source, :confidence_score, NOW(), NOW())
                    ON CONFLICT (canonical_entity_id, normalized_alias)
                    DO UPDATE SET
                        alias_name = EXCLUDED.alias_name,
                        source = EXCLUDED.source,
                        confidence_score = EXCLUDED.confidence_score,
                        updated_at = NOW()
                    """
                ),
                {
                    "canonical_entity_id": canonical_entity_id,
                    "alias_name": alias_name,
                    "normalized_alias": normalized_alias,
                    "source": "entity_resolution_prep",
                    "confidence_score": 1.0 if normalized_alias == normalized_name else 0.75,
                },
            )
            aliases_synced += 1

        for candidate in row.get("candidate_leads") or []:
            lead_id = str(candidate.get("lead_id") or "").strip()
            if not lead_id:
                continue
            session.execute(
                text(
                    """
                    INSERT INTO canonical_entity_leads (
                        canonical_entity_id, lead_id, relationship_type, is_primary,
                        confidence_score, evidence, created_at, updated_at
                    )
                    VALUES (
                        :canonical_entity_id, :lead_id, :relationship_type, :is_primary,
                        :confidence_score, CAST(:evidence AS JSONB), NOW(), NOW()
                    )
                    ON CONFLICT (canonical_entity_id, lead_id)
                    DO UPDATE SET
                        relationship_type = EXCLUDED.relationship_type,
                        is_primary = EXCLUDED.is_primary,
                        confidence_score = EXCLUDED.confidence_score,
                        evidence = EXCLUDED.evidence,
                        updated_at = NOW()
                    """
                ),
                {
                    "canonical_entity_id": canonical_entity_id,
                    "lead_id": lead_id,
                    "relationship_type": "keeper" if lead_id == str(row.get("keeper_lead_id") or "") else "candidate",
                    "is_primary": lead_id == str(row.get("keeper_lead_id") or ""),
                    "confidence_score": 1.0 if lead_id == str(row.get("keeper_lead_id") or "") else 0.6,
                    "evidence": json.dumps(
                        {
                            "display_name": candidate.get("display_name"),
                            "current_link_count": candidate.get("current_link_count"),
                            "has_user_state": candidate.get("has_user_state"),
                            "blank_display_name": candidate.get("blank_display_name"),
                        }
                    ),
                },
            )
            lead_links_synced += 1

        for bbl in cluster.get("bbls") or []:
            bbl_value = str(bbl or "").strip()
            if not bbl_value:
                continue
            session.execute(
                text(
                    """
                    INSERT INTO canonical_entity_buildings (
                        canonical_entity_id, bbl, source, confidence_score, evidence, created_at, updated_at
                    )
                    VALUES (
                        :canonical_entity_id, :bbl, 'entity_resolution_prep', :confidence_score,
                        CAST(:evidence AS JSONB), NOW(), NOW()
                    )
                    ON CONFLICT (canonical_entity_id, bbl)
                    DO UPDATE SET
                        confidence_score = EXCLUDED.confidence_score,
                        evidence = EXCLUDED.evidence,
                        updated_at = NOW()
                    """
                ),
                {
                    "canonical_entity_id": canonical_entity_id,
                    "bbl": bbl_value,
                    "confidence_score": 0.75,
                    "evidence": json.dumps(
                        {
                            "bucket": row.get("bucket"),
                            "canonical_name": canonical_name,
                        }
                    ),
                },
            )
            building_links_synced += 1

        proposal_key = f"{canonical_entity_id}:{row.get('keeper_lead_id') or 'none'}:{row.get('bucket') or 'unknown'}"
        session.execute(
            text(
                """
                INSERT INTO canonical_entity_match_proposals (
                    proposal_key, canonical_entity_id, lead_id, bucket, proposal_status,
                    safe_to_execute, reasons, evidence, created_at, updated_at
                )
                VALUES (
                    :proposal_key, :canonical_entity_id, :lead_id, :bucket, 'proposed',
                    :safe_to_execute, CAST(:reasons AS JSONB), CAST(:evidence AS JSONB), NOW(), NOW()
                )
                ON CONFLICT (proposal_key)
                DO UPDATE SET
                    proposal_status = 'proposed',
                    safe_to_execute = EXCLUDED.safe_to_execute,
                    reasons = EXCLUDED.reasons,
                    evidence = EXCLUDED.evidence,
                    updated_at = NOW()
                """
            ),
            {
                "proposal_key": proposal_key,
                "canonical_entity_id": canonical_entity_id,
                "lead_id": row.get("keeper_lead_id"),
                "bucket": row.get("bucket") or "unresolved",
                "safe_to_execute": bool((row.get("signals") or {}).get("safe_to_execute")),
                "reasons": json.dumps(
                    {
                        "blocked_reasons": row.get("blocked_reasons") or [],
                        "review_reasons": row.get("review_reasons") or [],
                        "canonical_reasons": row.get("canonical_reasons") or [],
                    }
                ),
                "evidence": json.dumps(
                    {
                        "candidate_lead_count": row.get("candidate_lead_count"),
                        "keeper_lead_id": row.get("keeper_lead_id"),
                        "signals": row.get("signals"),
                    }
                ),
            },
        )
        proposals_synced += 1

    return {
        "entities_synced": entities_synced,
        "aliases_synced": aliases_synced,
        "lead_links_synced": lead_links_synced,
        "building_links_synced": building_links_synced,
        "proposals_synced": proposals_synced,
    }
