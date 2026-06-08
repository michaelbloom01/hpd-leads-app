"""Smart Lists — saved filter segments with change detection.

Users save a set of lead filters as a "Smart List". The evaluate endpoint
re-runs the filters and detects which leads entered/exited since last run,
creating change_alerts for meaningful movements.
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_session
from src.auth.auth import AuthUser, get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/smart-lists", tags=["smart-lists"])
limiter = Limiter(key_func=get_remote_address)

ENSURE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS smart_lists (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    filters JSONB NOT NULL DEFAULT '{}',
    pinned BOOLEAN DEFAULT false,
    auto_evaluate BOOLEAN NOT NULL DEFAULT false,
    evaluation_interval_hours INTEGER NOT NULL DEFAULT 24,
    next_evaluation_at TIMESTAMPTZ,
    last_evaluated_at TIMESTAMPTZ,
    last_lead_ids TEXT[] DEFAULT '{}',
    last_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
"""
ENSURE_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_smart_lists_user ON smart_lists(user_id);"
ENSURE_AUTO_EVAL_COLUMNS_SQL = """
ALTER TABLE smart_lists
ADD COLUMN IF NOT EXISTS auto_evaluate BOOLEAN NOT NULL DEFAULT false;
"""
ENSURE_AUTO_EVAL_INTERVAL_SQL = """
ALTER TABLE smart_lists
ADD COLUMN IF NOT EXISTS evaluation_interval_hours INTEGER NOT NULL DEFAULT 24;
"""
ENSURE_AUTO_EVAL_NEXT_SQL = """
ALTER TABLE smart_lists
ADD COLUMN IF NOT EXISTS next_evaluation_at TIMESTAMPTZ;
"""
ENSURE_AUTO_EVAL_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_smart_lists_next_eval
ON smart_lists (next_evaluation_at)
WHERE auto_evaluate = true;
"""

REQUIRED_SMART_LIST_COLUMNS = {
    "id",
    "user_id",
    "name",
    "description",
    "filters",
    "pinned",
    "auto_evaluate",
    "evaluation_interval_hours",
    "next_evaluation_at",
    "last_evaluated_at",
    "last_lead_ids",
    "last_count",
    "created_at",
    "updated_at",
}


class SmartListCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = ""
    filters: dict = Field(default_factory=dict)

    @field_validator("filters")
    @classmethod
    def filters_object_only(cls, v: dict) -> dict:
        # Allow empty filter objects so users can create a scaffold list first,
        # then populate filters from the Leads workflow.
        return v

    pinned: bool = False
    auto_evaluate: bool = False
    evaluation_interval_hours: int = Field(default=24, ge=1, le=168)


class SmartListUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    filters: Optional[dict] = None
    pinned: Optional[bool] = None
    auto_evaluate: Optional[bool] = None
    evaluation_interval_hours: Optional[int] = Field(default=None, ge=1, le=168)


def _compute_next_evaluation_at(
    interval_hours: int,
    now: datetime | None = None,
) -> str:
    base = now or datetime.now(timezone.utc)
    next_dt = base.timestamp() + (int(interval_hours) * 3600)
    return datetime.fromtimestamp(next_dt, tz=timezone.utc).isoformat()


async def _evaluate_smart_list_data(
    session: AsyncSession,
    list_id: str,
    data: dict,
    *,
    triggered_by: str = "manual",
) -> dict:
    filters = data["filters"]
    if isinstance(filters, str):
        filters = json.loads(filters)

    where_sql, params = _build_where(filters)
    result = await session.execute(
        text(f"SELECT lead_id FROM leads WHERE {where_sql}"),
        params,
    )
    current_ids = [r[0] for r in result]
    current_set = set(current_ids)

    prev_ids = data.get("last_lead_ids") or []
    prev_set = set(prev_ids)

    entered = current_set - prev_set
    exited = prev_set - current_set

    # Keep scheduler state and list membership in sync after each evaluation.
    next_eval = None
    if data.get("auto_evaluate"):
        interval_hours = int(data.get("evaluation_interval_hours") or 24)
        next_eval = _compute_next_evaluation_at(interval_hours)

    await session.execute(
        text("""
            UPDATE smart_lists SET
                last_evaluated_at = NOW(),
                last_lead_ids = :ids,
                last_count = :cnt,
                next_evaluation_at = COALESCE(CAST(:next_eval AS TIMESTAMPTZ), next_evaluation_at),
                updated_at = NOW()
            WHERE id = :id
        """),
        {"ids": list(current_ids), "cnt": len(current_ids), "id": list_id, "next_eval": next_eval},
    )

    # Create change alerts for significant movements
    if entered and prev_ids:
        await session.execute(
            text("""
                INSERT INTO change_alerts (alert_type, description, details, created_at)
                VALUES ('smart_list_change', :desc, CAST(:details AS JSONB), NOW())
            """),
            {
                "desc": f"Smart List '{data['name']}': {len(entered)} new lead(s) entered",
                "details": json.dumps({
                    "smart_list_id": list_id,
                    "smart_list_name": data["name"],
                    "entered_count": len(entered),
                    "exited_count": len(exited),
                    "entered_ids": list(entered)[:20],
                    "triggered_by": triggered_by,
                }),
            },
        )

    return {
        "list_id": list_id,
        "name": data["name"],
        "total": len(current_ids),
        "previous_total": len(prev_ids),
        "entered": len(entered),
        "exited": len(exited),
        "entered_ids": list(entered)[:50],
        "exited_ids": list(exited)[:50],
    }


async def evaluate_due_auto_lists(
    session: AsyncSession,
    *,
    user_id: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Evaluate due auto-enabled Smart Lists (optionally scoped to one user)."""
    sql = """
        SELECT *
        FROM smart_lists
        WHERE auto_evaluate = true
          AND (
            next_evaluation_at IS NULL
            OR next_evaluation_at <= NOW()
          )
    """
    params: dict = {"limit": limit}
    if user_id:
        sql += " AND user_id = :uid"
        params["uid"] = user_id
    sql += " ORDER BY COALESCE(next_evaluation_at, created_at) ASC LIMIT :limit"
    due_rows = (await session.execute(text(sql), params)).fetchall()
    results: list[dict] = []
    for row in due_rows:
        data = dict(row._mapping)
        evaluated = await _evaluate_smart_list_data(
            session,
            data["id"],
            data,
            triggered_by="auto",
        )
        results.append(evaluated)
    return results


def _build_where(filters: dict) -> tuple[str, dict]:
    """Build WHERE clause from Smart List filter dict (mirrors leads.py logic)."""
    wheres: list[str] = []
    params: dict = {}

    def _num(key: str, val):
        n = float(val) if val else None
        return n if n and n > 0 else None

    if v := _num("min_score", filters.get("min_score")):
        wheres.append("score >= :sl_min_score")
        params["sl_min_score"] = v
    if v := _num("max_score", filters.get("max_score")):
        wheres.append("score <= :sl_max_score")
        params["sl_max_score"] = v
    if v := _num("min_portfolio", filters.get("min_portfolio")):
        wheres.append("portfolio_size >= :sl_min_port")
        params["sl_min_port"] = v
    if v := _num("max_portfolio", filters.get("max_portfolio")):
        wheres.append("portfolio_size <= :sl_max_port")
        params["sl_max_port"] = v
    if v := _num("min_units", filters.get("min_units")):
        wheres.append("total_units >= :sl_min_units")
        params["sl_min_units"] = v
    if v := _num("max_units", filters.get("max_units")):
        wheres.append("total_units <= :sl_max_units")
        params["sl_max_units"] = v

    if boros := filters.get("boroughs"):
        if isinstance(boros, str):
            boros = [b.strip() for b in boros.split(",") if b.strip()]
        if boros:
            phs = ", ".join(f":sl_boro_{i}" for i in range(len(boros)))
            wheres.append(f"primary_borough IN ({phs})")
            for i, b in enumerate(boros):
                params[f"sl_boro_{i}"] = b

    if entity_types := filters.get("entity_types"):
        if isinstance(entity_types, str):
            entity_types = [e.strip() for e in entity_types.split(",") if e.strip()]
        if entity_types:
            phs = ", ".join(f":sl_et_{i}" for i in range(len(entity_types)))
            wheres.append(f"entity_type IN ({phs})")
            for i, et in enumerate(entity_types):
                params[f"sl_et_{i}"] = et

    if ps := filters.get("pipeline_stages"):
        if isinstance(ps, str):
            ps = [s.strip() for s in ps.split(",") if s.strip()]
        if ps:
            phs = ", ".join(f":sl_ps_{i}" for i in range(len(ps)))
            wheres.append(f"pipeline_stage IN ({phs})")
            for i, s in enumerate(ps):
                params[f"sl_ps_{i}"] = s

    if filters.get("has_phone"):
        wheres.append("phone IS NOT NULL AND phone != ''")
    if filters.get("has_email"):
        wheres.append("email IS NOT NULL AND email != ''")

    if search := filters.get("search"):
        wheres.append("(owner_name ILIKE :sl_search OR company_name ILIKE :sl_search)")
        params["sl_search"] = f"%{search}%"

    where_sql = " AND ".join(wheres) if wheres else "1=1"
    return where_sql, params


async def ensure_smart_lists_table(session: AsyncSession):
    """Create the smart_lists table if explicitly requested by an operator."""
    # Prevent concurrent worker startup races on CREATE TABLE IF NOT EXISTS.
    await session.execute(text("SELECT pg_advisory_xact_lock(987654321012345678)"))
    await session.execute(text(ENSURE_TABLE_SQL))
    await session.execute(text(ENSURE_INDEX_SQL))
    await session.execute(text(ENSURE_AUTO_EVAL_COLUMNS_SQL))
    await session.execute(text(ENSURE_AUTO_EVAL_INTERVAL_SQL))
    await session.execute(text(ENSURE_AUTO_EVAL_NEXT_SQL))
    await session.execute(text(ENSURE_AUTO_EVAL_INDEX_SQL))


async def load_smart_lists_schema_status(session: AsyncSession) -> dict:
    """Inspect smart-list schema readiness without mutating the database."""
    table_exists = bool(
        (
            await session.execute(
                text("SELECT to_regclass('public.smart_lists') IS NOT NULL")
            )
        ).scalar()
    )
    if not table_exists:
        return {
            "ready": False,
            "table_exists": False,
            "missing_columns": sorted(REQUIRED_SMART_LIST_COLUMNS),
        }

    result = await session.execute(
        text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'smart_lists'
        """)
    )
    columns = {row[0] for row in result}
    missing_columns = sorted(REQUIRED_SMART_LIST_COLUMNS - columns)
    return {
        "ready": not missing_columns,
        "table_exists": True,
        "missing_columns": missing_columns,
    }


async def require_smart_lists_schema(session: AsyncSession) -> dict:
    status = await load_smart_lists_schema_status(session)
    if not status["ready"]:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "schema_not_ready",
                "message": "Smart Lists schema is not ready; run Alembic migrations or explicit startup repair before using Smart Lists.",
                "schema_status": status,
            },
        )
    return status


@router.get("")
@limiter.limit("60/minute")
async def list_smart_lists(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    await require_smart_lists_schema(session)
    result = await session.execute(
        text("""
            SELECT id, name, description, filters, pinned,
                   auto_evaluate, evaluation_interval_hours, next_evaluation_at,
                   last_evaluated_at, last_count, created_at, updated_at
            FROM smart_lists
            WHERE user_id = :uid
            ORDER BY pinned DESC, updated_at DESC
        """),
        {"uid": user.user_id},
    )
    rows = [dict(r._mapping) for r in result]
    for row in rows:
        if isinstance(row.get("filters"), str):
            row["filters"] = json.loads(row["filters"])
    return {"smart_lists": rows}


@router.post("")
@limiter.limit("30/minute")
async def create_smart_list(
    request: Request,
    body: SmartListCreate,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    await require_smart_lists_schema(session)
    list_id = str(uuid.uuid4())[:12]
    await session.execute(
        text("""
            INSERT INTO smart_lists (id, user_id, name, description, filters, pinned)
            VALUES (:id, :uid, :name, :desc, CAST(:filters AS JSONB), :pinned)
        """),
        {
            "id": list_id,
            "uid": user.user_id,
            "name": body.name,
            "desc": body.description,
            "filters": json.dumps(body.filters),
            "pinned": body.pinned,
        },
    )
    if body.auto_evaluate:
        next_eval = _compute_next_evaluation_at(body.evaluation_interval_hours)
        await session.execute(
            text("""
                UPDATE smart_lists
                SET auto_evaluate = true,
                    evaluation_interval_hours = :hrs,
                    next_evaluation_at = CAST(:next_eval AS TIMESTAMPTZ)
                WHERE id = :id
            """),
            {"id": list_id, "hrs": body.evaluation_interval_hours, "next_eval": next_eval},
        )
    return {"id": list_id, "name": body.name, "status": "created"}


@router.get("/{list_id}")
@limiter.limit("60/minute")
async def get_smart_list(
    request: Request,
    list_id: str,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    await require_smart_lists_schema(session)
    result = await session.execute(
        text("SELECT * FROM smart_lists WHERE id = :id AND user_id = :uid"),
        {"id": list_id, "uid": user.user_id},
    )
    row = result.first()
    if not row:
        raise HTTPException(404, "Smart list not found")
    data = dict(row._mapping)
    if isinstance(data.get("filters"), str):
        data["filters"] = json.loads(data["filters"])
    return data


@router.patch("/{list_id}")
@limiter.limit("30/minute")
async def update_smart_list(
    request: Request,
    list_id: str,
    body: SmartListUpdate,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    await require_smart_lists_schema(session)
    exists = (await session.execute(
        text("SELECT 1 FROM smart_lists WHERE id = :id AND user_id = :uid"),
        {"id": list_id, "uid": user.user_id},
    )).first()
    if not exists:
        raise HTTPException(404, "Smart list not found")

    sets = ["updated_at = NOW()"]
    params: dict = {"id": list_id, "uid": user.user_id}
    if body.name is not None:
        sets.append("name = :name")
        params["name"] = body.name
    if body.description is not None:
        sets.append("description = :desc")
        params["desc"] = body.description
    if body.filters is not None:
        sets.append("filters = CAST(:filters AS JSONB)")
        params["filters"] = json.dumps(body.filters)
    if body.pinned is not None:
        sets.append("pinned = :pinned")
        params["pinned"] = body.pinned
    if body.auto_evaluate is not None:
        sets.append("auto_evaluate = :auto_evaluate")
        params["auto_evaluate"] = body.auto_evaluate
        if body.auto_evaluate is False:
            sets.append("next_evaluation_at = NULL")
    if body.evaluation_interval_hours is not None:
        sets.append("evaluation_interval_hours = :evaluation_interval_hours")
        params["evaluation_interval_hours"] = body.evaluation_interval_hours

    await session.execute(
        text(f"UPDATE smart_lists SET {', '.join(sets)} WHERE id = :id AND user_id = :uid"),
        params,
    )
    if body.auto_evaluate is True:
        # If enabling auto-evaluate, ensure a due time exists.
        interval = body.evaluation_interval_hours or 24
        next_eval = _compute_next_evaluation_at(interval)
        await session.execute(
            text("""
                UPDATE smart_lists
                SET next_evaluation_at = COALESCE(next_evaluation_at, CAST(:next_eval AS TIMESTAMPTZ))
                WHERE id = :id AND user_id = :uid
            """),
            {"id": list_id, "uid": user.user_id, "next_eval": next_eval},
        )
    return {"id": list_id, "status": "updated"}


@router.delete("/{list_id}")
@limiter.limit("30/minute")
async def delete_smart_list(
    request: Request,
    list_id: str,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    await require_smart_lists_schema(session)
    result = await session.execute(
        text("DELETE FROM smart_lists WHERE id = :id AND user_id = :uid"),
        {"id": list_id, "uid": user.user_id},
    )
    if result.rowcount == 0:
        raise HTTPException(404, "Smart list not found")
    return {"id": list_id, "status": "deleted"}


@router.post("/{list_id}/evaluate")
@limiter.limit("20/minute")
async def evaluate_smart_list(
    request: Request,
    list_id: str,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """Re-run the saved filters and detect changes since last evaluation."""
    await require_smart_lists_schema(session)
    row = (await session.execute(
        text("SELECT * FROM smart_lists WHERE id = :id AND user_id = :uid"),
        {"id": list_id, "uid": user.user_id},
    )).first()
    if not row:
        raise HTTPException(404, "Smart list not found")

    data = dict(row._mapping)
    return await _evaluate_smart_list_data(session, list_id, data, triggered_by="manual")


@router.post("/auto-evaluate/run")
@limiter.limit("10/minute")
async def run_due_auto_evaluations(
    request: Request,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """Evaluate due auto-enabled Smart Lists for current user."""
    await require_smart_lists_schema(session)
    results = await evaluate_due_auto_lists(session, user_id=user.user_id, limit=limit)

    return {
        "status": "ok",
        "evaluated_count": len(results),
        "results": results,
    }
