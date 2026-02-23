"""Scoring configuration API router.

Controls the PM Operator's building churn scoring weights.
Does NOT affect the PE Searcher's lead scoring (V3).
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
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
async def list_configs(session: AsyncSession = Depends(get_session)):
    rows = await session.execute(
        text("SELECT id, name, is_active, is_preset, weights, created_by, created_at FROM scoring_configs ORDER BY id")
    )
    return [
        {"id": r[0], "name": r[1], "is_active": r[2], "is_preset": r[3],
         "weights": r[4], "created_by": r[5], "created_at": str(r[6]) if r[6] else None}
        for r in rows
    ]


@router.get("/configs/active")
async def get_active_config(session: AsyncSession = Depends(get_session)):
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
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    import threading
    result = await session.execute(
        text("""
            INSERT INTO ingestion_jobs (job_type, source, status, started_at, created_at, updated_at)
            VALUES ('scoring', 'churn', 'running', now(), now(), now())
            RETURNING id
        """)
    )
    job_id = result.scalar_one()

    def _run_scoring():
        try:
            from src.tasks.score import compute_churn_scores, _get_pg_session, SIGNAL_VIEWS_SQL, SUMMARY_VIEW_SQL, SIGNAL_NAMES, _compute_raw_signal
            from sqlalchemy import create_engine, text as sa_text
            from sqlalchemy.orm import Session as SyncSession
            from src.db.session import get_sync_url
            import json
            from datetime import datetime

            sync_session = SyncSession(create_engine(get_sync_url()))
            config_row = sync_session.execute(sa_text("SELECT id, weights FROM scoring_configs WHERE is_active = true LIMIT 1")).first()
            if not config_row:
                sync_session.execute(sa_text("UPDATE ingestion_jobs SET status='failed', error='No active config', finished_at=now() WHERE id=:id"), {"id": job_id})
                sync_session.commit()
                return

            cfg_id, weights = config_row[0], config_row[1] if isinstance(config_row[1], dict) else {}
            for stmt in SIGNAL_VIEWS_SQL.split(";"):
                stmt = stmt.strip()
                if stmt:
                    sync_session.execute(sa_text(stmt))
            sync_session.commit()
            sync_session.execute(sa_text("DROP MATERIALIZED VIEW IF EXISTS building_signal_summary"))
            sync_session.execute(sa_text(SUMMARY_VIEW_SQL))
            sync_session.commit()

            rows = sync_session.execute(sa_text("SELECT * FROM building_signal_summary")).fetchall()
            columns = list(sync_session.execute(sa_text("SELECT * FROM building_signal_summary LIMIT 0")).keys())
            scored = 0
            now = datetime.utcnow()

            for row in rows:
                row_dict = dict(zip(columns, row))
                bbl = row_dict["bbl"]
                raw_scores = {}
                available = 0
                for name in SIGNAL_NAMES:
                    val = _compute_raw_signal(name, row_dict)
                    raw_scores[name] = val
                    if val is not None:
                        available += 1
                null_weight_pool = sum(weights.get(n, 0) for n in SIGNAL_NAMES if raw_scores[n] is None)
                non_null_total = sum(weights.get(n, 0) for n in SIGNAL_NAMES if raw_scores[n] is not None)
                weighted_sum = 0.0
                breakdown = {}
                for name in SIGNAL_NAMES:
                    raw = raw_scores[name]
                    base_weight = weights.get(name, 0)
                    if raw is None:
                        breakdown[name] = {"raw": None, "weight": base_weight, "effective_weight": 0, "contribution": 0}
                        continue
                    effective_weight = base_weight
                    if non_null_total > 0 and null_weight_pool > 0:
                        effective_weight = base_weight + (null_weight_pool * base_weight / non_null_total)
                    contribution = raw * effective_weight / 100.0
                    weighted_sum += contribution
                    breakdown[name] = {"raw": round(raw, 1), "weight": base_weight, "effective_weight": round(effective_weight, 1), "contribution": round(contribution, 1)}

                churn_score = min(100.0, max(0.0, weighted_sum))
                category = "hot" if churn_score >= 70 else "warm" if churn_score >= 40 else "stable"
                key_signal = max(((n, d["contribution"]) for n, d in breakdown.items() if d["contribution"] > 0), key=lambda x: x[1], default=("none", 0))[0]
                sync_session.execute(sa_text("UPDATE buildings SET churn_score=:score, churn_category=:category, churn_breakdown=:breakdown, key_signal=:key, scoring_config_id=:cfg_id, signals_available=:available, coverage_ratio=:coverage, last_scored_at=:now, updated_at=:now WHERE bbl=:bbl"),
                    {"score": round(churn_score, 1), "category": category, "breakdown": json.dumps(breakdown), "key": key_signal, "cfg_id": cfg_id, "available": available, "coverage": round(available / len(SIGNAL_NAMES), 2), "now": now, "bbl": bbl})
                scored += 1
                if scored % 5000 == 0:
                    sync_session.commit()

            sync_session.commit()
            sync_session.execute(sa_text("UPDATE ingestion_jobs SET status='completed', succeeded=:scored, finished_at=now() WHERE id=:id"), {"id": job_id, "scored": scored})
            sync_session.commit()
            sync_session.close()
        except Exception as exc:
            logger.error(f"Scoring failed: {exc}")
            try:
                from sqlalchemy import create_engine, text as sa_text
                from sqlalchemy.orm import Session as SyncSession
                from src.db.session import get_sync_url
                s = SyncSession(create_engine(get_sync_url()))
                s.execute(sa_text("UPDATE ingestion_jobs SET status='failed', error=:err, finished_at=now() WHERE id=:id"), {"id": job_id, "err": str(exc)[:500]})
                s.commit()
                s.close()
            except Exception:
                pass

    threading.Thread(target=_run_scoring, daemon=True).start()
    return {"job_id": job_id, "status": "running"}
