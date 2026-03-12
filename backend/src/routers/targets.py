"""Target intake, matching, thesis scoring, and dossier APIs."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.auth import AuthUser, get_current_user
from src.db.session import get_session
from src.services.targets import (
    build_target_dossier,
    create_target_list,
    discover_adjacent_targets,
    get_target_list,
    import_target_items,
    list_target_lists,
    refresh_target_matches,
    select_target_match,
)

router = APIRouter(prefix="/api/v1/targets", tags=["targets"])


class TargetListCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = None
    targeting_mode: str = Field(default="both", max_length=30)
    source_notes: Optional[str] = None


class TargetImportRow(BaseModel):
    company_name: str
    established: Optional[str] = None
    portfolio_estimate: Optional[str] = None
    units_estimate: Optional[str] = None
    geography: Optional[str] = None
    ownership: Optional[str] = None
    key_principals: Optional[str] = None
    condo_focus: Optional[str] = None
    website: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    tier: Optional[str] = None
    acquisition_fit_notes: Optional[str] = None
    risk_flag: Optional[str] = None
    notes: Optional[str] = None


class TargetImportRequest(BaseModel):
    rows: list[TargetImportRow]


class TargetItemPatchRequest(BaseModel):
    notes: Optional[str] = None
    acquisition_fit_notes: Optional[str] = None
    risk_flag: Optional[str] = None
    pipeline_stage: Optional[str] = None
    outreach_status: Optional[str] = None
    priority_rank: Optional[int] = None
    next_follow_up: Optional[str] = None


class TargetMatchSelectRequest(BaseModel):
    lead_id: str


@router.get("/lists")
async def get_lists(
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    return {"target_lists": await list_target_lists(session, user.user_id)}


@router.post("/lists")
async def post_list(
    body: TargetListCreateRequest,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    created = await create_target_list(
        session,
        user_id=user.user_id,
        name=body.name,
        description=body.description,
        targeting_mode=body.targeting_mode,
        source_notes=body.source_notes,
    )
    await session.commit()
    return {"status": "created", **created}


@router.get("/lists/{target_list_id}")
async def get_list_detail(
    target_list_id: str,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    target_list = await get_target_list(session, target_list_id)
    if not target_list or target_list.get("user_id") != user.user_id:
        raise HTTPException(404, "Target list not found")
    return target_list


@router.post("/lists/{target_list_id}/items/import")
async def import_items(
    target_list_id: str,
    body: TargetImportRequest,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    target_list = await get_target_list(session, target_list_id)
    if not target_list or target_list.get("user_id") != user.user_id:
        raise HTTPException(404, "Target list not found")
    results = await import_target_items(session, target_list_id, [row.model_dump() for row in body.rows])
    await session.commit()
    return {"status": "imported", "imported_count": len(results), "results": results}


@router.post("/lists/{target_list_id}/rescore")
async def rescore_items(
    target_list_id: str,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    target_list = await get_target_list(session, target_list_id)
    if not target_list or target_list.get("user_id") != user.user_id:
        raise HTTPException(404, "Target list not found")
    results: list[dict[str, Any]] = []
    for item in target_list.get("items") or []:
        results.append(await refresh_target_matches(session, str(item["target_item_id"])))
    await session.commit()
    return {"status": "rescored", "count": len(results), "results": results}


@router.get("/lists/{target_list_id}/discover")
async def discover_targets(
    target_list_id: str,
    limit: int = Query(default=25, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    target_list = await get_target_list(session, target_list_id)
    if not target_list or target_list.get("user_id") != user.user_id:
        raise HTTPException(404, "Target list not found")
    discoveries = await discover_adjacent_targets(session, target_list_id, limit=limit)
    return {"target_list_id": target_list_id, "discoveries": discoveries}


@router.patch("/items/{target_item_id}")
async def patch_target_item(
    target_item_id: str,
    body: TargetItemPatchRequest,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    row = (
        await session.execute(
            text(
                """
                SELECT tli.target_item_id, tl.user_id
                FROM target_list_items tli
                JOIN target_lists tl ON tl.target_list_id = tli.target_list_id
                WHERE tli.target_item_id = :target_item_id
                """
            ),
            {"target_item_id": target_item_id},
        )
    ).first()
    if not row or str(row[1]) != user.user_id:
        raise HTTPException(404, "Target item not found")

    set_clauses: list[str] = []
    params: dict[str, Any] = {"target_item_id": target_item_id}
    for field, value in body.model_dump(exclude_unset=True).items():
        set_clauses.append(f"{field} = :{field}")
        params[field] = value
    if not set_clauses:
        return {"status": "noop", "target_item_id": target_item_id}

    set_clauses.append("updated_at = NOW()")
    await session.execute(
        text(f"UPDATE target_list_items SET {', '.join(set_clauses)} WHERE target_item_id = :target_item_id"),
        params,
    )
    await session.commit()
    return {"status": "updated", "target_item_id": target_item_id}


@router.post("/items/{target_item_id}/match/select")
async def select_match(
    target_item_id: str,
    body: TargetMatchSelectRequest,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    row = (
        await session.execute(
            text(
                """
                SELECT tl.user_id
                FROM target_list_items tli
                JOIN target_lists tl ON tl.target_list_id = tli.target_list_id
                WHERE tli.target_item_id = :target_item_id
                """
            ),
            {"target_item_id": target_item_id},
        )
    ).first()
    if not row or str(row[0]) != user.user_id:
        raise HTTPException(404, "Target item not found")
    result = await select_target_match(session, target_item_id, body.lead_id)
    await session.commit()
    return result


@router.get("/items/{target_item_id}/dossier")
async def get_dossier(
    target_item_id: str,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    dossier = await build_target_dossier(session, target_item_id)
    if not dossier:
        raise HTTPException(404, "Target item not found")
    ownership = (
        await session.execute(
            text(
                """
                SELECT tl.user_id
                FROM target_list_items tli
                JOIN target_lists tl ON tl.target_list_id = tli.target_list_id
                WHERE tli.target_item_id = :target_item_id
                """
            ),
            {"target_item_id": target_item_id},
        )
    ).first()
    if not ownership or str(ownership[0]) != user.user_id:
        raise HTTPException(404, "Target item not found")
    return dossier
