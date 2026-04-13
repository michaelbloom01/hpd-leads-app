"""Read-only canonical entity and proposal APIs."""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.auth import AuthUser, get_current_user
from src.db.session import get_session

router = APIRouter(prefix="/api/v1/canonical", tags=["canonical"])


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


@router.get("/entities")
async def list_canonical_entities(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    del user
    wheres: list[str] = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if status:
        wheres.append("ce.status = :status")
        params["status"] = status
    where_sql = f"WHERE {' AND '.join(wheres)}" if wheres else ""

    result = await session.execute(
        text(f"""
            WITH lead_counts AS (
                SELECT canonical_entity_id, COUNT(*)::int AS lead_count
                FROM canonical_entity_leads
                GROUP BY canonical_entity_id
            ),
            building_counts AS (
                SELECT canonical_entity_id, COUNT(*)::int AS building_count
                FROM canonical_entity_buildings
                GROUP BY canonical_entity_id
            ),
            proposal_counts AS (
                SELECT
                    canonical_entity_id,
                    COUNT(*)::int AS proposal_count,
                    COUNT(*) FILTER (WHERE safe_to_execute = true)::int AS safe_proposal_count
                FROM canonical_entity_match_proposals
                GROUP BY canonical_entity_id
            )
            SELECT
                ce.canonical_entity_id,
                ce.normalized_name,
                ce.display_name,
                ce.entity_type,
                ce.status,
                ce.confidence_score,
                ce.updated_at,
                COALESCE(lc.lead_count, 0) AS lead_count,
                COALESCE(bc.building_count, 0) AS building_count,
                COALESCE(pc.proposal_count, 0) AS proposal_count,
                COALESCE(pc.safe_proposal_count, 0) AS safe_proposal_count
            FROM canonical_entities ce
            LEFT JOIN lead_counts lc ON lc.canonical_entity_id = ce.canonical_entity_id
            LEFT JOIN building_counts bc ON bc.canonical_entity_id = ce.canonical_entity_id
            LEFT JOIN proposal_counts pc ON pc.canonical_entity_id = ce.canonical_entity_id
            {where_sql}
            ORDER BY ce.updated_at DESC NULLS LAST, ce.canonical_entity_id ASC
            LIMIT :limit OFFSET :offset
        """),
        params,
    )
    rows = [dict(row._mapping) for row in result]

    count_result = await session.execute(
        text(f"SELECT COUNT(*) FROM canonical_entities ce {where_sql}"),
        {k: v for k, v in params.items() if k != "limit" and k != "offset"},
    )
    total = int(count_result.scalar() or 0)
    return {"entities": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/entities/{canonical_entity_id}")
async def get_canonical_entity(
    canonical_entity_id: str,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    del user
    entity_row = (await session.execute(
        text("""
            SELECT canonical_entity_id, normalized_name, display_name, entity_type, status, confidence_score, profile, updated_at
            FROM canonical_entities
            WHERE canonical_entity_id = :cid
        """),
        {"cid": canonical_entity_id},
    )).first()
    if not entity_row:
        raise HTTPException(status_code=404, detail="Canonical entity not found")

    entity = dict(entity_row._mapping)
    entity["profile"] = _parse_json_object(entity.get("profile"))

    alias_rows = await session.execute(
        text("""
            SELECT alias_name, normalized_alias, source, confidence_score
            FROM canonical_entity_aliases
            WHERE canonical_entity_id = :cid
            ORDER BY confidence_score DESC NULLS LAST, alias_name ASC
        """),
        {"cid": canonical_entity_id},
    )
    lead_rows = await session.execute(
        text("""
            SELECT lead_id, relationship_type, is_primary, confidence_score, evidence
            FROM canonical_entity_leads
            WHERE canonical_entity_id = :cid
            ORDER BY is_primary DESC, confidence_score DESC NULLS LAST, lead_id ASC
        """),
        {"cid": canonical_entity_id},
    )
    building_rows = await session.execute(
        text("""
            SELECT bbl, source, confidence_score, evidence
            FROM canonical_entity_buildings
            WHERE canonical_entity_id = :cid
            ORDER BY confidence_score DESC NULLS LAST, bbl ASC
        """),
        {"cid": canonical_entity_id},
    )
    proposal_rows = await session.execute(
        text("""
            SELECT id, proposal_key, lead_id, bucket, proposal_status, safe_to_execute, reasons, evidence, updated_at
            FROM canonical_entity_match_proposals
            WHERE canonical_entity_id = :cid
            ORDER BY updated_at DESC NULLS LAST, id DESC
        """),
        {"cid": canonical_entity_id},
    )

    leads = []
    for row in lead_rows:
        payload = dict(row._mapping)
        payload["is_primary"] = bool(payload.get("is_primary"))
        payload["evidence"] = _parse_json_object(payload.get("evidence"))
        leads.append(payload)

    buildings = []
    for row in building_rows:
        payload = dict(row._mapping)
        payload["evidence"] = _parse_json_object(payload.get("evidence"))
        buildings.append(payload)

    proposals = []
    for row in proposal_rows:
        payload = dict(row._mapping)
        payload["safe_to_execute"] = bool(payload.get("safe_to_execute"))
        payload["reasons"] = _parse_json_object(payload.get("reasons"))
        payload["evidence"] = _parse_json_object(payload.get("evidence"))
        payload["updated_at"] = payload["updated_at"].isoformat() if payload.get("updated_at") else None
        proposals.append(payload)

    return {
        "entity": entity,
        "aliases": [dict(row._mapping) for row in alias_rows],
        "leads": leads,
        "buildings": buildings,
        "proposals": proposals,
    }


@router.get("/proposals")
async def list_canonical_proposals(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    bucket: Optional[str] = Query(None),
    proposal_status: Optional[str] = Query(None),
    lead_id: Optional[str] = Query(None),
    safe_to_execute: Optional[bool] = Query(None),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    del user
    wheres: list[str] = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if bucket:
        wheres.append("bucket = :bucket")
        params["bucket"] = bucket
    if proposal_status:
        wheres.append("proposal_status = :proposal_status")
        params["proposal_status"] = proposal_status
    if lead_id:
        wheres.append("lead_id = :lead_id")
        params["lead_id"] = lead_id
    if safe_to_execute is not None:
        wheres.append("safe_to_execute = :safe_to_execute")
        params["safe_to_execute"] = safe_to_execute
    where_sql = f"WHERE {' AND '.join(wheres)}" if wheres else ""

    result = await session.execute(
        text(f"""
            SELECT
                id,
                proposal_key,
                canonical_entity_id,
                lead_id,
                bucket,
                proposal_status,
                safe_to_execute,
                reasons,
                evidence,
                updated_at
            FROM canonical_entity_match_proposals
            {where_sql}
            ORDER BY updated_at DESC NULLS LAST, id DESC
            LIMIT :limit OFFSET :offset
        """),
        params,
    )
    proposals = []
    for row in result:
        payload = dict(row._mapping)
        payload["safe_to_execute"] = bool(payload.get("safe_to_execute"))
        payload["reasons"] = _parse_json_object(payload.get("reasons"))
        payload["evidence"] = _parse_json_object(payload.get("evidence"))
        payload["updated_at"] = payload["updated_at"].isoformat() if payload.get("updated_at") else None
        proposals.append(payload)

    count_result = await session.execute(
        text(f"SELECT COUNT(*) FROM canonical_entity_match_proposals {where_sql}"),
        {k: v for k, v in params.items() if k not in {"limit", "offset"}},
    )
    total = int(count_result.scalar() or 0)
    return {"proposals": proposals, "total": total, "limit": limit, "offset": offset}
