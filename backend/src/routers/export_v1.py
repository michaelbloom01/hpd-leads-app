"""V1 CSV/JSON export for buildings and leads via PostgreSQL.

Handles large exports via streaming response to avoid memory issues.
Complements the legacy export router (export.py) which works against SQLite.
"""
import csv
import io
import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_session
from src.auth.auth import AuthUser, get_current_user
from src.services.portfolio_export import build_portfolio_export, build_portfolio_workbook

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/export", tags=["export-v1"])
limiter = Limiter(key_func=get_remote_address)


@router.get("/buildings/csv")
@limiter.limit("10/minute")
async def export_buildings_csv(
    request: Request,
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
@limiter.limit("10/minute")
async def export_buildings_json(
    request: Request,
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
@limiter.limit("10/minute")
async def export_leads_csv(
    request: Request,
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


@router.get("/portfolio-contacts/csv")
@limiter.limit("10/minute")
async def export_portfolio_contacts_csv(
    request: Request,
    company: str = Query(..., min_length=2, max_length=160),
    lead_id: Optional[str] = Query(default=None, max_length=64),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """Export all buildings and contact/source rows for a manager portfolio."""
    payload = await build_portfolio_export(
        session=session,
        company=company,
        lead_id=lead_id,
    )
    rows = payload.get("contact_rows") or []
    columns = list(rows[0].keys()) if rows else [
        "bbl",
        "address",
        "borough",
        "zip_code",
        "unit_count",
        "churn_score",
        "churn_category",
        "management_company",
        "corporate_owner",
        "dos_contacts_status",
        "contact_name",
        "contact_role",
        "contact_source",
        "contact_updated",
        "contact_address",
        "contact_confidence",
        "contact_source_record_id",
        "contact_source_url",
        "board_role",
        "is_decision_maker",
    ]

    def generate():
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)
        for row in rows:
            writer.writerow(row)
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

    filename_company = re.sub(r"[^A-Za-z0-9]+", "_", company).strip("_").lower() or "portfolio"
    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f"attachment; filename=double_edge_{filename_company}_portfolio_contacts.csv"
            )
        },
    )


@router.get("/portfolio-contacts/xlsx")
@limiter.limit("10/minute")
async def export_portfolio_contacts_xlsx(
    request: Request,
    company: str = Query(..., min_length=2, max_length=160),
    lead_id: Optional[str] = Query(default=None, max_length=64),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """Export a portfolio workbook with buildings, contacts, sourcing, and gaps."""
    payload = await build_portfolio_export(
        session=session,
        company=company,
        lead_id=lead_id,
    )
    filename_company = re.sub(r"[^A-Za-z0-9]+", "_", company).strip("_").lower() or "portfolio"
    return Response(
        content=build_portfolio_workbook(payload),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f"attachment; filename=double_edge_{filename_company}_portfolio_contacts.xlsx"
            )
        },
    )
