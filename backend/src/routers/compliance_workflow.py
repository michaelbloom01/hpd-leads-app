"""Authenticated, versioned internal review actions. Agency status is read-only."""

from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.auth import AuthUser, get_current_user
from src.db.session import get_session
from src.models.compliance import ComplianceRecord
from src.models.compliance_reviews import ComplianceReview
from src.services.compliance import compliance_enabled

router = APIRouter(prefix="/api/v1/compliance/records", tags=["compliance"])
DbSession = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[AuthUser, Depends(get_current_user)]
RecordId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
ReviewState = Literal["new", "in_review", "verified_for_briefing", "monitoring", "closed_internally", "dismissed", "source_mismatch"]


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: ReviewState
    reason: str = Field(min_length=5, max_length=2000)
    expected_version: int = Field(ge=0, strict=True)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Review reasons cannot contain NUL characters.")
        value = value.strip()
        if len(value) < 5:
            raise ValueError("Provide a short reason for the internal review.")
        return value


async def review_schema_ready(session: AsyncSession) -> bool:
    return bool((await session.execute(text("SELECT to_regclass('public.compliance_reviews') IS NOT NULL"))).scalar())


async def require_record(session: AsyncSession, record_id: str, *, lock: bool = False) -> ComplianceRecord:
    if not compliance_enabled() or not await review_schema_ready(session):
        raise HTTPException(status_code=503, detail="Internal compliance review is awaiting rollout.")
    statement = select(ComplianceRecord).where(ComplianceRecord.id == record_id)
    if lock:
        statement = statement.with_for_update()
    record = (await session.execute(statement)).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Compliance record not found.")
    return record


async def review_history(session: AsyncSession, record: ComplianceRecord) -> dict:
    rows = list((await session.execute(select(ComplianceReview).where(
        ComplianceReview.record_id == record.id,
    ).order_by(ComplianceReview.version.desc()).limit(50))).scalars())
    latest = rows[0] if rows else None
    return {
        "record_id": record.id,
        "source_record_key": record.source_record_key,
        "agency_status": record.status,
        "state": latest.state if latest else "new",
        "version": latest.version if latest else 0,
        "history_limit": 50,
        "history": [{
            "id": row.id, "version": row.version, "state": row.state,
            "reason": row.reason, "actor": row.actor_label, "created_at": row.created_at,
        } for row in rows],
        "notice": "Internal review decisions leave agency status, payment and legal conclusions unchanged.",
    }


@router.get("/{record_id}/reviews")
async def get_reviews(record_id: RecordId, session: DbSession, user: CurrentUser):
    return await review_history(session, await require_record(session, record_id))


@router.post("/{record_id}/reviews")
async def save_review(record_id: RecordId, review: ReviewInput, session: DbSession, user: CurrentUser):
    record = await require_record(session, record_id, lock=True)
    current = await review_history(session, record)
    if current["version"] != review.expected_version:
        raise HTTPException(status_code=409, detail="Another reviewer updated this record. Reload its review history before saving.")
    session.add(ComplianceReview(
        id=uuid4().hex, record_id=record.id, version=current["version"] + 1,
        state=review.state, reason=review.reason, actor_id=user.user_id,
        actor_label=user.email, created_at=datetime.now(timezone.utc),
    ))
    await session.commit()
    return await review_history(session, record)
