"""V1 CSV/JSON export for buildings and leads via PostgreSQL.

Handles large exports via streaming response to avoid memory issues.
Complements the legacy export router (export.py) which works against SQLite.
"""
import csv
import io
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_session
from src.auth.auth import AuthUser, get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/export", tags=["export-v1"])


@router.get("/buildings/csv")
async def export_buildings_csv(
    churn_category: Optional[str] = None,
    min_churn: Optional[float] = None,
    borough: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    wheres = []
    params: dict = {}
    if churn_category:
        wheres.append("b.churn_category = :cat")
        params["cat"] = churn_category
    if min_churn is not None:
        wheres.append("b.churn_score >= :min_churn")
        params["min_churn"] = min_churn
    if borough:
        wheres.append("b.borough = :boro")
        params["boro"] = borough
    where_sql = " AND ".join(wheres) if wheres else "1=1"

    result = await session.execute(
        text(f"""
            SELECT b.bbl, b.address, b.borough, b.unit_count, b.building_class,
                   b.year_built, b.assessed_value, b.churn_score, b.churn_category,
                   b.key_signal, b.outreach_status
            FROM buildings b WHERE {where_sql}
            ORDER BY b.churn_score DESC NULLS LAST
            LIMIT 10000
        """),
        params,
    )
    rows = result.fetchall()
    columns = ["bbl", "address", "borough", "unit_count", "building_class",
               "year_built", "assessed_value", "churn_score", "churn_category",
               "key_signal", "outreach_status"]

    def generate():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(columns)
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)
        for row in rows:
            writer.writerow(list(row))
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=double_edge_buildings_export.csv"},
    )


@router.get("/buildings/json")
async def export_buildings_json(
    churn_category: Optional[str] = None,
    min_churn: Optional[float] = None,
    limit: int = Query(default=1000, le=10000),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    wheres = []
    params: dict = {"limit": limit}
    if churn_category:
        wheres.append("b.churn_category = :cat")
        params["cat"] = churn_category
    if min_churn is not None:
        wheres.append("b.churn_score >= :min_churn")
        params["min_churn"] = min_churn
    where_sql = " AND ".join(wheres) if wheres else "1=1"

    result = await session.execute(
        text(f"""
            SELECT b.bbl, b.address, b.borough, b.unit_count, b.churn_score,
                   b.churn_category, b.churn_breakdown, b.key_signal
            FROM buildings b WHERE {where_sql}
            ORDER BY b.churn_score DESC NULLS LAST LIMIT :limit
        """),
        params,
    )
    return [dict(r._mapping) for r in result]


@router.get("/leads/csv")
async def export_leads_csv(
    min_score: Optional[int] = None,
    pipeline_stage: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    wheres = []
    params: dict = {}
    if min_score is not None:
        wheres.append("l.score >= :min_score")
        params["min_score"] = min_score
    if pipeline_stage:
        wheres.append("l.pipeline_stage = :stage")
        params["stage"] = pipeline_stage
    where_sql = " AND ".join(wheres) if wheres else "1=1"

    result = await session.execute(
        text(f"""
            SELECT l.lead_id, l.owner_name, l.company_name, l.portfolio_size,
                   l.total_units, l.score, l.pipeline_stage, l.outreach_status,
                   l.phone, l.email, l.website
            FROM leads l WHERE {where_sql}
            ORDER BY l.score DESC NULLS LAST LIMIT 10000
        """),
        params,
    )
    rows = result.fetchall()
    columns = ["lead_id", "owner_name", "company_name", "portfolio_size",
               "total_units", "score", "pipeline_stage", "outreach_status",
               "phone", "email", "company_website"]

    def generate():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(columns)
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)
        for row in rows:
            writer.writerow(list(row))
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=double_edge_leads_export.csv"},
    )
