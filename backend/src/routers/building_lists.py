"""Building Lists — saved collections of buildings for the PM Operator persona."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_session
from src.auth.auth import AuthUser, get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/building-lists", tags=["building-lists"])
limiter = Limiter(key_func=get_remote_address)

REQUIRED_BUILDING_LIST_COLUMNS = {
    "building_lists": {"id", "user_id", "name", "created_at", "updated_at"},
    "building_list_members": {"list_id", "bbl", "added_at"},
}


def _auth_user_id(user: AuthUser) -> str:
    """Return the JWT subject id for ownership checks."""
    return user.user_id


async def ensure_building_lists_tables(session: AsyncSession) -> None:
    """Create building-list tables if explicitly requested by an operator."""
    await session.execute(text("""
        CREATE TABLE IF NOT EXISTS building_lists (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
    """))
    await session.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_building_lists_user ON building_lists(user_id);
    """))
    await session.execute(text("""
        CREATE TABLE IF NOT EXISTS building_list_members (
            list_id TEXT NOT NULL REFERENCES building_lists(id) ON DELETE CASCADE,
            bbl TEXT NOT NULL,
            added_at TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (list_id, bbl)
        );
    """))
    await session.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_building_list_members_bbl ON building_list_members(bbl);
    """))


async def load_building_lists_schema_status(session: AsyncSession) -> dict:
    """Inspect building-list schema readiness without mutating the database."""
    table_rows = await session.execute(
        text("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN ('building_lists', 'building_list_members')
        """)
    )
    existing_tables = {row[0] for row in table_rows}
    missing_tables = sorted(set(REQUIRED_BUILDING_LIST_COLUMNS) - existing_tables)

    column_rows = await session.execute(
        text("""
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name IN ('building_lists', 'building_list_members')
        """)
    )
    observed_columns: dict[str, set[str]] = {table: set() for table in REQUIRED_BUILDING_LIST_COLUMNS}
    for table_name, column_name in column_rows:
        observed_columns.setdefault(table_name, set()).add(column_name)

    missing_columns = {
        table: sorted(required - observed_columns.get(table, set()))
        for table, required in REQUIRED_BUILDING_LIST_COLUMNS.items()
        if table not in missing_tables and required - observed_columns.get(table, set())
    }

    return {
        "ready": not missing_tables and not missing_columns,
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
    }


async def require_building_lists_schema(session: AsyncSession) -> dict:
    status = await load_building_lists_schema_status(session)
    if not status["ready"]:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "schema_not_ready",
                "message": "Building Lists schema is not ready; run Alembic migrations or explicit startup repair before using Building Lists.",
                "schema_status": status,
            },
        )
    return status


class BuildingListCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class BuildingListUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


@router.get("")
@limiter.limit("60/minute")
async def list_building_lists(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """List all building lists for the current user."""
    await require_building_lists_schema(session)
    result = await session.execute(
        text("""
            SELECT id, name, created_at, updated_at,
                   (SELECT COUNT(*) FROM building_list_members m WHERE m.list_id = bl.id) AS member_count
            FROM building_lists bl
            WHERE user_id = :uid
            ORDER BY updated_at DESC
        """),
        {"uid": _auth_user_id(user)},
    )
    rows = [dict(r._mapping) for r in result]
    return {"building_lists": rows}


@router.post("")
@limiter.limit("30/minute")
async def create_building_list(
    request: Request,
    body: BuildingListCreate,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """Create a new building list."""
    await require_building_lists_schema(session)
    list_id = str(uuid.uuid4())
    await session.execute(
        text("""
            INSERT INTO building_lists (id, user_id, name)
            VALUES (:id, :uid, :name)
        """),
        {"id": list_id, "uid": _auth_user_id(user), "name": body.name.strip()},
    )
    await session.commit()
    return {"id": list_id, "name": body.name.strip()}


@router.patch("/{list_id}")
@limiter.limit("30/minute")
async def update_building_list(
    request: Request,
    list_id: str,
    body: BuildingListUpdate,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """Rename a building list."""
    await require_building_lists_schema(session)
    result = await session.execute(
        text(
            """
            UPDATE building_lists
            SET name = :name, updated_at = now()
            WHERE id = :id AND user_id = :uid
            RETURNING id, name
            """
        ),
        {"id": list_id, "uid": _auth_user_id(user), "name": body.name.strip()},
    )
    row = result.fetchone()
    await session.commit()
    if not row:
        raise HTTPException(status_code=404, detail="Building list not found")
    return {"id": row[0], "name": row[1], "status": "updated"}


@router.delete("/{list_id}")
@limiter.limit("30/minute")
async def delete_building_list(
    request: Request,
    list_id: str,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """Delete a building list."""
    await require_building_lists_schema(session)
    r = await session.execute(
        text("DELETE FROM building_lists WHERE id = :id AND user_id = :uid RETURNING id"),
        {"id": list_id, "uid": _auth_user_id(user)},
    )
    row = r.fetchone()
    await session.commit()
    if not row:
        raise HTTPException(status_code=404, detail="Building list not found")
    return {"id": list_id, "status": "deleted"}


@router.post("/{list_id}/buildings/{bbl}")
@limiter.limit("60/minute")
async def add_building_to_list(
    request: Request,
    list_id: str,
    bbl: str,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """Add a building (by BBL) to a building list."""
    await require_building_lists_schema(session)
    # Verify list ownership
    r = await session.execute(
        text("SELECT 1 FROM building_lists WHERE id = :id AND user_id = :uid"),
        {"id": list_id, "uid": _auth_user_id(user)},
    )
    if not r.fetchone():
        raise HTTPException(status_code=404, detail="Building list not found")
    # Verify building exists
    b = await session.execute(text("SELECT 1 FROM buildings WHERE bbl = :bbl"), {"bbl": bbl})
    if not b.fetchone():
        raise HTTPException(status_code=404, detail="Building not found")
    await session.execute(
        text("""
            INSERT INTO building_list_members (list_id, bbl)
            VALUES (:list_id, :bbl)
            ON CONFLICT (list_id, bbl) DO NOTHING
        """),
        {"list_id": list_id, "bbl": bbl},
    )
    await session.execute(
        text("UPDATE building_lists SET updated_at = now() WHERE id = :id"),
        {"id": list_id},
    )
    await session.commit()
    return {"list_id": list_id, "bbl": bbl, "status": "added"}


@router.delete("/{list_id}/buildings/{bbl}")
@limiter.limit("60/minute")
async def remove_building_from_list(
    request: Request,
    list_id: str,
    bbl: str,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """Remove a building from a building list."""
    await require_building_lists_schema(session)
    r = await session.execute(
        text("""
            DELETE FROM building_list_members
            WHERE list_id = :list_id AND bbl = :bbl
            AND EXISTS (SELECT 1 FROM building_lists WHERE id = :list_id AND user_id = :uid)
            RETURNING list_id
        """),
        {"list_id": list_id, "bbl": bbl, "uid": _auth_user_id(user)},
    )
    row = r.fetchone()
    await session.commit()
    if not row:
        raise HTTPException(status_code=404, detail="Building list or member not found")
    return {"list_id": list_id, "bbl": bbl, "status": "removed"}


@router.get("/{list_id}/buildings")
@limiter.limit("60/minute")
async def list_buildings_in_list(
    request: Request,
    list_id: str,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """List buildings in a building list."""
    await require_building_lists_schema(session)
    r = await session.execute(
        text("SELECT 1 FROM building_lists WHERE id = :id AND user_id = :uid"),
        {"id": list_id, "uid": _auth_user_id(user)},
    )
    if not r.fetchone():
        raise HTTPException(status_code=404, detail="Building list not found")
    result = await session.execute(
        text("""
            SELECT b.bbl, b.address, b.borough, b.unit_count, b.building_type,
                   b.churn_score, b.churn_category,
                   NULL::double precision AS latitude,
                   NULL::double precision AS longitude,
                   NULL::varchar AS coordinate_source,
                   NULL::varchar AS coordinate_precision,
                   m.added_at
            FROM building_list_members m
            JOIN buildings b ON b.bbl = m.bbl
            WHERE m.list_id = :list_id
            ORDER BY m.added_at DESC
        """),
        {"list_id": list_id},
    )
    return {"buildings": [dict(r._mapping) for r in result]}
