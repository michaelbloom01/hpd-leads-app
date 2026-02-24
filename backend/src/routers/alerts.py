"""Churn alerts API router.

Surfaces management change detection and churn alerts for both personas.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_session
from src.auth.auth import AuthUser, get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


@router.get("")
async def list_alerts(
    alert_type: Optional[str] = None,
    dismissed: bool = False,
    limit: int = Query(default=50, le=200),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    wheres = ["dismissed = :dismissed"]
    params: dict = {"dismissed": dismissed, "limit": limit}
    if alert_type:
        wheres.append("alert_type = :atype")
        params["atype"] = alert_type
    where_sql = " AND ".join(wheres)

    result = await session.execute(
        text(f"""
            SELECT id, alert_type, lead_id, bbl, description, details, created_at, dismissed
            FROM change_alerts
            WHERE {where_sql}
            ORDER BY created_at DESC LIMIT :limit
        """),
        params,
    )
    return [dict(r._mapping) for r in result]


@router.get("/count")
async def alert_count(session: AsyncSession = Depends(get_session), user: AuthUser = Depends(get_current_user)):
    result = await session.execute(
        text("SELECT COUNT(*) FROM change_alerts WHERE dismissed = false")
    )
    return {"count": result.scalar()}


@router.post("/{alert_id}/dismiss")
async def dismiss_alert(
    alert_id: int,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    result = await session.execute(
        text("SELECT 1 FROM change_alerts WHERE id = :id"), {"id": alert_id}
    )
    if not result.first():
        raise HTTPException(404, "Alert not found")
    await session.execute(
        text("UPDATE change_alerts SET dismissed = true, updated_at = now() WHERE id = :id"),
        {"id": alert_id},
    )
    return {"status": "dismissed"}


@router.post("/{alert_id}/snooze")
async def snooze_alert(
    alert_id: int,
    days: int = 7,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    await session.execute(
        text("""
            UPDATE change_alerts
            SET dismissed = true, updated_at = now(),
                details = jsonb_set(COALESCE(details, '{}'), '{snoozed_until}',
                    to_jsonb((now() + (:days || ' days')::interval)::text))
            WHERE id = :id
        """),
        {"id": alert_id, "days": str(days)},
    )
    return {"status": "snoozed", "days": days}
