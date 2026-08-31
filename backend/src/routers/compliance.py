"""Read-only compliance views and attributed admin-only portal evidence capture."""

from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.auth import AuthUser, get_current_admin, get_current_user
from src.db.session import get_session
from src.ingest.dob_safety import payload_hash
from src.models.compliance import ComplianceBalanceObservation
from src.services.compliance import (
    compliance_enabled,
    load_compliance,
    schema_ready,
    utc,
)

router = APIRouter(prefix="/api/v1/compliance", tags=["compliance"])
BIN_PATTERN = r"^[1-5]\d{6}$"
BBL_PATTERN = r"^[1-5]\d{9}$"
PORTAL_URL = "https://www.nyc.gov/assets/buildings/html/Unpaid_Violations_Search.html"
DbSession = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[AuthUser, Depends(get_current_user)]
CurrentAdmin = Annotated[AuthUser, Depends(get_current_admin)]


@router.get("/portfolio/{lead_id}")
async def portfolio_compliance(
    lead_id: Annotated[str, Path(min_length=1, max_length=80)],
    session: DbSession,
    user: CurrentUser,
):
    return await load_compliance(session, scope_type="portfolio", scope_id=lead_id)


@router.get("/parcels/{bbl}")
async def parcel_compliance(
    bbl: Annotated[str, Path(pattern=BBL_PATTERN)],
    session: DbSession,
    user: CurrentUser,
):
    return await load_compliance(session, scope_type="parcel", scope_id=bbl)


@router.get("/buildings/{bin}")
async def building_compliance(
    bin: Annotated[str, Path(pattern=BIN_PATTERN)],
    session: DbSession,
    user: CurrentUser,
):
    return await load_compliance(session, scope_type="building", scope_id=bin)


class BalanceEvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bin: str = Field(pattern=BIN_PATTERN)
    category: Literal["LL152"] = "LL152"
    scope: Literal["bin_category"] = "bin_category"
    amount_cents: int = Field(ge=0, le=1_000_000_000_000, strict=True)
    source_url: str = PORTAL_URL
    source_updated_at: datetime | None = None
    source_timestamp_raw: str = Field(min_length=1, max_length=200)
    observed_at: datetime
    evidence_note: str = Field(min_length=20, max_length=4000)

    @field_validator("source_url")
    @classmethod
    def official_portal_only(cls, value: str) -> str:
        url = urlparse(value)
        if (
            url.scheme != "https"
            or url.netloc != "www.nyc.gov"
            or url.path != "/assets/buildings/html/Unpaid_Violations_Search.html"
            or url.query
            or url.fragment
        ):
            raise ValueError("Provide the official DOB unpaid-violation portal URL.")
        return value

    @field_validator("observed_at", "source_updated_at")
    @classmethod
    def dated_evidence(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                "Use a timezone-aware timestamp; keep unknown source timezone in source_timestamp_raw."
            )
        if value > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("Evidence timestamps cannot be in the future.")
        return utc(value)

    @field_validator("source_timestamp_raw", "evidence_note")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Evidence text cannot be blank.")
        return value.strip()


def evidence_payload(evidence: BalanceEvidenceInput, user: AuthUser) -> dict:
    if evidence.source_updated_at and evidence.source_updated_at > evidence.observed_at:
        raise HTTPException(
            status_code=422, detail="Source update cannot follow the observation time."
        )
    payload = evidence.model_dump(mode="json")
    payload["reviewer"] = user.user_id
    digest = payload_hash(payload)
    return {
        **payload,
        "id": digest[:32],
        "payload_hash": digest,
        "amount_basis": "manual_portal_observation",
        "interest_status": "unverified",
        "lien_status": "unverified",
    }


@router.post("/balance-evidence/preview")
async def preview_balance_evidence(evidence: BalanceEvidenceInput, user: CurrentAdmin):
    return {
        "dry_run": True,
        "writes": 0,
        "evidence": evidence_payload(evidence, user),
        "warnings": [
            "This is a BIN/category observation. It does not allocate amounts to individual violations or establish interest or liens."
        ],
    }


@router.post("/balance-evidence")
async def capture_balance_evidence(
    evidence: BalanceEvidenceInput,
    session: DbSession,
    user: CurrentAdmin,
    confirm_execute: bool = Query(False),
):
    if not confirm_execute:
        raise HTTPException(
            status_code=400,
            detail="Preview evidence, then provide confirm_execute=true to capture it.",
        )
    if not compliance_enabled() or not await schema_ready(session):
        raise HTTPException(
            status_code=503, detail="Compliance evidence capture is awaiting rollout."
        )
    identity = (
        await session.execute(
            text("SELECT bin FROM physical_buildings WHERE bin = :bin"),
            {"bin": evidence.bin},
        )
    ).scalar_one_or_none()
    if not identity:
        raise HTTPException(
            status_code=422,
            detail="Resolve this BIN against the physical-building identity source first.",
        )
    payload = evidence_payload(evidence, user)
    # Serialize duplicate captures without exposing a transient uniqueness error.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(152, :bin)"), {"bin": int(evidence.bin)}
    )
    existing = (
        await session.execute(
            select(ComplianceBalanceObservation).where(
                ComplianceBalanceObservation.payload_hash == payload["payload_hash"]
            )
        )
    ).scalar_one_or_none()
    if existing:
        return {"created": False, "evidence": payload}
    values = evidence.model_dump()
    session.add(
        ComplianceBalanceObservation(
            **values,
            id=payload["id"],
            payload_hash=payload["payload_hash"],
            reviewer=user.user_id,
            captured_at=datetime.now(timezone.utc),
        )
    )
    await session.flush()
    return {"created": True, "evidence": payload}
