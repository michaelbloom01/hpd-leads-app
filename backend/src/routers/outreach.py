"""Unified outreach timeline and follow-up APIs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.auth import AuthUser, get_current_user
from src.db.session import get_session
from src.services.outreach_feedback import load_outreach_feedback_truth_write_status, record_outreach_feedback_claims

router = APIRouter(prefix="/api/v1/outreach", tags=["outreach"])


class OutreachEventCreateRequest(BaseModel):
    lead_id: Optional[str] = None
    bbl: Optional[str] = None
    target_item_id: Optional[str] = None
    canonical_entity_id: Optional[str] = None
    stage: str = Field(min_length=1, max_length=30)
    method: Optional[str] = Field(default=None, max_length=20)
    outcome: Optional[str] = Field(default=None, max_length=30)
    notes: Optional[str] = None
    next_follow_up: Optional[str] = None

    @model_validator(mode="after")
    def require_context(self) -> "OutreachEventCreateRequest":
        if not any([self.lead_id, self.bbl, self.target_item_id, self.canonical_entity_id]):
            raise ValueError("At least one outreach context ID is required")
        return self


@router.get("/events")
async def list_outreach_events(
    lead_id: Optional[str] = None,
    bbl: Optional[str] = None,
    target_item_id: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    wheres = ["1=1"]
    params: dict[str, object] = {"limit": limit}
    if lead_id:
        wheres.append("lead_id = :lead_id")
        params["lead_id"] = lead_id
    if bbl:
        wheres.append("bbl = :bbl")
        params["bbl"] = bbl
    if target_item_id:
        wheres.append("target_item_id = :target_item_id")
        params["target_item_id"] = target_item_id
    rows = (
        await session.execute(
            text(
                f"""
                SELECT *
                FROM outreach_events
                WHERE {' AND '.join(wheres)}
                ORDER BY event_timestamp DESC NULLS LAST, created_at DESC
                LIMIT :limit
                """
            ),
            params,
        )
    ).mappings().all()
    return {"events": [dict(row) for row in rows]}


@router.post("/events")
async def create_outreach_event(
    body: OutreachEventCreateRequest,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    truth_claim_status = await load_outreach_feedback_truth_write_status(session)
    event_id = (
        await session.execute(
            text(
                """
                INSERT INTO outreach_events (
                    lead_id, bbl, target_item_id, canonical_entity_id, stage, method, outcome,
                    notes, next_follow_up, event_timestamp, created_at, updated_at
                )
                VALUES (
                    :lead_id, :bbl, :target_item_id, :canonical_entity_id, :stage, :method, :outcome,
                    :notes, :next_follow_up, :event_timestamp, NOW(), NOW()
                )
                RETURNING id
                """
            ),
            {
                "lead_id": body.lead_id,
                "bbl": body.bbl,
                "target_item_id": body.target_item_id,
                "canonical_entity_id": body.canonical_entity_id,
                "stage": body.stage,
                "method": body.method,
                "outcome": body.outcome,
                "notes": body.notes,
                "next_follow_up": body.next_follow_up,
                "event_timestamp": datetime.now(timezone.utc),
            },
        )
    ).scalar_one()

    truth_claim_ids: list[str] = []
    if truth_claim_status["ready"] and any([body.lead_id, body.bbl, body.canonical_entity_id, body.target_item_id]):
        truth_claim_ids = await record_outreach_feedback_claims(
            session,
            lead_id=body.lead_id,
            event_id=int(event_id),
            method=body.method,
            outcome=body.outcome,
            notes=body.notes,
            bbl=body.bbl,
            canonical_entity_id=body.canonical_entity_id,
            target_item_id=body.target_item_id,
        )
        await session.execute(
            text(
                """
                UPDATE leads
                SET pipeline_stage = :stage,
                    next_follow_up = COALESCE(:next_follow_up, next_follow_up),
                    updated_at = NOW()
                WHERE lead_id = :lead_id
                """
            ),
            {"lead_id": body.lead_id, "stage": body.stage, "next_follow_up": body.next_follow_up},
        )
    if body.bbl:
        await session.execute(
            text(
                """
                UPDATE buildings
                SET outreach_status = COALESCE(:outcome, outreach_status),
                    next_outreach_date = COALESCE(:next_follow_up, next_outreach_date),
                    updated_at = NOW()
                WHERE bbl = :bbl
                """
            ),
            {"bbl": body.bbl, "outcome": body.outcome, "next_follow_up": body.next_follow_up},
        )
    if body.target_item_id:
        await session.execute(
            text(
                """
                UPDATE target_list_items
                SET pipeline_stage = :stage,
                    outreach_status = COALESCE(:outcome, outreach_status),
                    next_follow_up = COALESCE(:next_follow_up, next_follow_up),
                    last_contacted_at = NOW(),
                    updated_at = NOW()
                WHERE target_item_id = :target_item_id
                """
            ),
            {
                "target_item_id": body.target_item_id,
                "stage": body.stage,
                "outcome": body.outcome,
                "next_follow_up": body.next_follow_up,
            },
        )

    await session.commit()
    return {
        "status": "success",
        "event_id": event_id,
        "truth_claim_ids": truth_claim_ids,
        "truth_claim_status": truth_claim_status,
    }


@router.get("/follow-ups")
async def list_follow_ups(
    before: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    before_sql = "CURRENT_DATE"
    params: dict[str, object] = {"limit": limit}
    if before:
        before_sql = "CAST(:before AS DATE)"
        params["before"] = before

    rows = (
        await session.execute(
            text(
                f"""
                WITH due_items AS (
                    SELECT
                        'lead' AS entity_type,
                        lead_id AS entity_id,
                        COALESCE(company_name, agent_name, owner_name, lead_id) AS display_name,
                        next_follow_up AS due_date,
                        pipeline_stage AS stage,
                        outreach_status AS status,
                        NULL::TEXT AS secondary_ref
                    FROM leads
                    WHERE next_follow_up IS NOT NULL
                      AND next_follow_up <= {before_sql}

                    UNION ALL

                    SELECT
                        'target' AS entity_type,
                        target_item_id AS entity_id,
                        company_name AS display_name,
                        next_follow_up AS due_date,
                        pipeline_stage AS stage,
                        outreach_status AS status,
                        target_list_id AS secondary_ref
                    FROM target_list_items
                    WHERE next_follow_up IS NOT NULL
                      AND next_follow_up <= {before_sql}

                    UNION ALL

                    SELECT
                        'building' AS entity_type,
                        bbl AS entity_id,
                        address AS display_name,
                        next_outreach_date AS due_date,
                        'building_outreach' AS stage,
                        outreach_status AS status,
                        borough AS secondary_ref
                    FROM buildings
                    WHERE next_outreach_date IS NOT NULL
                      AND next_outreach_date <= {before_sql}
                )
                SELECT *
                FROM due_items
                ORDER BY due_date ASC, display_name ASC
                LIMIT :limit
                """
            ),
            params,
        )
    ).mappings().all()
    counts = {}
    for row in rows:
        counts[row["entity_type"]] = counts.get(row["entity_type"], 0) + 1
    return {"count": len(rows), "counts": counts, "items": [dict(row) for row in rows]}
