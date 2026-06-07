"""Scoring configuration API router.

Controls the PM Operator's building churn scoring weights.
Does NOT affect the PE Searcher's lead scoring (V3).
"""
import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_session
from src.auth.auth import AuthUser, get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/scoring", tags=["scoring"])


class ScoringWeights(BaseModel):
    ownership_change: int = Field(ge=0, le=100)
    complaint_spike: int = Field(ge=0, le=100)
    violation_trend: int = Field(ge=0, le=100)
    energy_grade_drop: int = Field(ge=0, le=100)
    dob_permits: int = Field(ge=0, le=100)
    hpd_litigation: int = Field(ge=0, le=100)
    emergency_repairs: int = Field(ge=0, le=100)
    building_size: int = Field(ge=0, le=100)
    eviction_activity: int = Field(ge=0, le=100)
    facade_status: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def weights_must_sum_to_100(self) -> "ScoringWeights":
        total = sum(self.model_dump().values())
        if total != 100:
            raise ValueError(f"Weights must sum to 100, got {total}")
        return self


class ScoringConfigCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    weights: ScoringWeights


class ScoringConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    is_active: bool
    is_preset: bool
    weights: dict
    created_by: Optional[str] = None
    created_at: Optional[str] = None


class RecalculateResponse(BaseModel):
    job_id: int


@router.get("/configs")
async def list_configs(session: AsyncSession = Depends(get_session), user: AuthUser = Depends(get_current_user)):
    rows = await session.execute(
        text("SELECT id, name, is_active, is_preset, weights, created_by, created_at FROM scoring_configs ORDER BY id")
    )
    return [
        {"id": r[0], "name": r[1], "is_active": r[2], "is_preset": r[3],
         "weights": r[4], "created_by": r[5], "created_at": str(r[6]) if r[6] else None}
        for r in rows
    ]


@router.get("/configs/active")
async def get_active_config(session: AsyncSession = Depends(get_session), user: AuthUser = Depends(get_current_user)):
    row = await session.execute(
        text("SELECT id, name, is_active, is_preset, weights, created_by FROM scoring_configs WHERE is_active = true LIMIT 1")
    )
    r = row.first()
    if not r:
        raise HTTPException(404, "No active scoring config")
    return {"id": r[0], "name": r[1], "is_active": r[2], "is_preset": r[3],
            "weights": r[4], "created_by": r[5]}


@router.post("/configs")
async def create_config(
    body: ScoringConfigCreate,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    result = await session.execute(
        text("""
            INSERT INTO scoring_configs (name, is_active, is_preset, weights, created_by, created_at, updated_at)
            VALUES (:name, false, false, :weights, :user, now(), now())
            RETURNING id
        """),
        {"name": body.name, "weights": json.dumps(body.weights.model_dump()), "user": user.email},
    )
    config_id = result.scalar_one()
    return {"id": config_id, "name": body.name}


@router.put("/configs/{config_id}")
async def update_config(
    config_id: int,
    body: ScoringConfigCreate,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    row = await session.execute(
        text("SELECT is_preset FROM scoring_configs WHERE id = :id"), {"id": config_id}
    )
    existing = row.first()
    if not existing:
        raise HTTPException(404, "Config not found")
    if existing[0]:
        raise HTTPException(400, "Cannot modify preset configurations")

    await session.execute(
        text("UPDATE scoring_configs SET name = :name, weights = :weights, updated_at = now() WHERE id = :id"),
        {"id": config_id, "name": body.name, "weights": json.dumps(body.weights.model_dump())},
    )
    return {"id": config_id, "status": "updated"}


@router.delete("/configs/{config_id}")
async def delete_config(
    config_id: int,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    row = await session.execute(
        text("SELECT is_preset, is_active FROM scoring_configs WHERE id = :id"), {"id": config_id}
    )
    existing = row.first()
    if not existing:
        raise HTTPException(404, "Config not found")
    if existing[0]:
        raise HTTPException(400, "Cannot delete preset configurations")
    if existing[1]:
        raise HTTPException(400, "Cannot delete active configuration")

    await session.execute(text("DELETE FROM scoring_configs WHERE id = :id"), {"id": config_id})
    return {"status": "deleted"}


@router.post("/configs/{config_id}/activate")
async def activate_config(
    config_id: int,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    row = await session.execute(
        text("SELECT 1 FROM scoring_configs WHERE id = :id"), {"id": config_id}
    )
    if not row.first():
        raise HTTPException(404, "Config not found")

    await session.execute(text("UPDATE scoring_configs SET is_active = false WHERE is_active = true"))
    await session.execute(text("UPDATE scoring_configs SET is_active = true WHERE id = :id"), {"id": config_id})
    return {"status": "activated", "config_id": config_id}


@router.post("/recalculate")
async def trigger_recalculate(
    dry_run: bool = Query(default=True),
    confirm_execute: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    dry_run = dry_run if isinstance(dry_run, bool) else True
    confirm_execute = confirm_execute if isinstance(confirm_execute, bool) else False
    if dry_run:
        return {
            "status": "approval_required",
            "job_id": None,
            "dry_run": True,
            "confirm_execute": confirm_execute,
            "approval_required": True,
            "safe_to_run_automatically": False,
            "mutations_planned": 0,
            "preview": {
                "operation": "scoring_recalculate",
                "would_enqueue_job_type": "scoring",
                "would_mutate": ["ingestion_jobs", "lead/building score fields"],
                "required_execute_query": "/api/v1/scoring/recalculate?dry_run=false&confirm_execute=true",
            },
            "rollback_strategy": "No scoring job was queued. Execute only after explicit approval.",
        }
    if not confirm_execute:
        raise HTTPException(400, "scoring recalculation execution requires confirm_execute=true")

    result = await session.execute(
        text("""
            INSERT INTO ingestion_jobs (job_type, source, status, started_at, created_at, updated_at)
            VALUES ('scoring', 'churn', 'queued', now(), now(), now())
            RETURNING id
        """)
    )
    job_id = result.scalar_one()
    await session.commit()

    dispatch_mode = "celery"
    try:
        from src.tasks.score import run_scoring_job
        run_scoring_job.delay(job_id=job_id)
    except Exception as exc:
        logger.warning(
            "Celery dispatch failed for scoring job %s, falling back to in-process execution: %s",
            job_id,
            exc,
        )
        from src.tasks.score import run_scoring_job
        asyncio.create_task(asyncio.to_thread(run_scoring_job.run, job_id=job_id))
        dispatch_mode = "in_process"

    return {"job_id": job_id, "status": "queued", "dispatch_mode": dispatch_mode}
