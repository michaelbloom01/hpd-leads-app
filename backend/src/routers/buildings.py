"""Buildings API router.

The PM Operator's primary workspace: find buildings with high churn
probability, understand why, and do outreach.
"""
import logging
import re
import json
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_session
from src.auth.auth import AuthUser, get_current_user
from src.score.revenue import estimate_building_revenue
from src.services.contact_roster import get_building_contacts
from src.services.outreach_feedback import load_outreach_feedback_truth_write_status, record_outreach_feedback_claims

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/buildings", tags=["buildings"])
limiter = Limiter(key_func=get_remote_address)
DOS_REFRESH_COOLDOWN = timedelta(minutes=5)


def _normalize_bbl_digits(raw_value: Optional[str]) -> Optional[str]:
    if not raw_value:
        return None
    value = str(raw_value).strip()
    if not value:
        return None
    if re.fullmatch(r"\d{10}\.0+", value):
        value = value.split(".", 1)[0]
    digits = re.sub(r"\D", "", value)
    if len(digits) == 10:
        return digits
    if 0 < len(digits) < 10:
        return digits.zfill(10)
    return None


async def _resolve_canonical_bbl(session: AsyncSession, raw_bbl: str) -> Optional[str]:
    candidate = unquote(raw_bbl or "").strip()
    if not candidate:
        return None

    exact_row = (await session.execute(
        text("SELECT bbl FROM buildings WHERE bbl = :bbl LIMIT 1"),
        {"bbl": candidate},
    )).first()
    if exact_row:
        return str(exact_row[0])

    digits = _normalize_bbl_digits(candidate)
    if not digits:
        return None

    digits_row = (await session.execute(
        text("SELECT bbl FROM buildings WHERE bbl = :digits LIMIT 1"),
        {"digits": digits},
    )).first()
    if digits_row:
        return str(digits_row[0])

    normalized_row = (await session.execute(
        text("""
            SELECT bbl
            FROM buildings
            WHERE regexp_replace(bbl, '[^0-9]', '', 'g') = :digits
            ORDER BY updated_at DESC NULLS LAST, bbl ASC
            LIMIT 1
        """),
        {"digits": digits},
    )).first()
    if normalized_row:
        return str(normalized_row[0])

    logger.warning("Could not resolve BBL: raw=%r, normalized_digits=%s", raw_bbl, digits)
    return None


def _coerce_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


async def _mark_dos_refresh_requested(session: AsyncSession, bbl: str) -> None:
    cache_key = f"officers:{bbl}"
    row = (await session.execute(
        text("SELECT result FROM dos_cache WHERE cache_key = :cache_key"),
        {"cache_key": cache_key},
    )).first()
    payload: dict = {}
    if row and row[0]:
        try:
            parsed = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            if isinstance(parsed, dict):
                payload = parsed
        except Exception:
            payload = {}
    payload["refresh_requested_at"] = datetime.now(timezone.utc).isoformat()

    await session.execute(
        text("""
            INSERT INTO dos_cache (cache_key, result, cached_at)
            VALUES (:cache_key, :result, NOW())
            ON CONFLICT (cache_key)
            DO UPDATE SET result = EXCLUDED.result
        """),
        {"cache_key": cache_key, "result": json.dumps(payload)},
    )
    await session.commit()


async def _request_dos_refresh(
    session: AsyncSession,
    bbl: str,
    corporate_owner_name: Optional[str],
) -> dict[str, object]:
    owner_name = (corporate_owner_name or "").strip()
    if not owner_name:
        return {"status": "skipped", "reason": "missing_corporate_owner"}

    cache_key = f"officers:{bbl}"
    row = (
        await session.execute(
            text("SELECT result FROM dos_cache WHERE cache_key = :cache_key"),
            {"cache_key": cache_key},
        )
    ).first()
    payload: dict = {}
    if row and row[0]:
        try:
            parsed = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            if isinstance(parsed, dict):
                payload = parsed
        except Exception:
            payload = {}

    refresh_requested_at = _coerce_iso_datetime(payload.get("refresh_requested_at"))
    if refresh_requested_at and (datetime.now(timezone.utc) - refresh_requested_at) < DOS_REFRESH_COOLDOWN:
        return {
            "status": "refreshing",
            "bbl": bbl,
            "corporate_owner": owner_name,
            "refresh_requested_at": refresh_requested_at.isoformat(),
        }

    from src.tasks.enrich import refresh_dos_contacts

    refresh_dos_contacts.delay(bbl, owner_name)
    await _mark_dos_refresh_requested(session, bbl)
    return {
        "status": "refreshing",
        "bbl": bbl,
        "corporate_owner": owner_name,
    }


@router.get("")
@limiter.limit("60/minute")
async def list_buildings(
    request: Request,
    borough: Optional[str] = Query(None, max_length=100),
    building_type: Optional[str] = None,
    min_units: Optional[int] = None,
    max_units: Optional[int] = None,
    min_churn: Optional[float] = None,
    max_churn: Optional[float] = None,
    churn_category: Optional[str] = None,
    outreach_status: Optional[str] = None,
    lead_id: Optional[str] = None,
    search: Optional[str] = Query(None, max_length=200),
    sort_by: str = "churn_score",
    sort_dir: str = "desc",
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    wheres = []
    params: dict = {"limit": limit, "offset": offset, "lead_id": lead_id}

    if borough:
        wheres.append("b.borough = :borough")
        params["borough"] = borough
    if building_type:
        wheres.append("b.building_type = :btype")
        params["btype"] = building_type
    if min_units is not None:
        wheres.append("b.unit_count >= :min_units")
        params["min_units"] = min_units
    if max_units is not None:
        wheres.append("b.unit_count <= :max_units")
        params["max_units"] = max_units
    if min_churn is not None:
        wheres.append("b.churn_score >= :min_churn")
        params["min_churn"] = min_churn
    if max_churn is not None:
        wheres.append("b.churn_score <= :max_churn")
        params["max_churn"] = max_churn
    if churn_category:
        wheres.append("b.churn_category = :category")
        params["category"] = churn_category
    if lead_id:
        wheres.append("EXISTS (SELECT 1 FROM building_management bm WHERE bm.bbl = b.bbl AND bm.lead_id = :lead_id AND bm.is_current)")
        params["lead_id"] = lead_id
    if outreach_status:
        if outreach_status == "in_pipeline":
            wheres.append("b.outreach_status != 'none'")
        else:
            wheres.append("b.outreach_status = :ostatus")
            params["ostatus"] = outreach_status
    if search:
        wheres.append("""
            (
                b.address ILIKE :search
                OR b.bbl ILIKE :search
                OR EXISTS (
                    SELECT 1
                    FROM building_contacts bc_search
                    WHERE bc_search.bbl = b.bbl
                      AND bc_search.contact_type = 'Agent'
                      AND bc_search.corporation_name ILIKE :search
                )
            )
        """)
        params["search"] = f"%{search}%"

    where_sql = " AND ".join(wheres) if wheres else "1=1"

    ALLOWED_BUILDING_SORTS = {"churn_score", "unit_count", "borough", "address", "assessed_value"}
    sort_col = sort_by if sort_by in ALLOWED_BUILDING_SORTS else "churn_score"
    direction = "ASC" if sort_dir.lower() == "asc" else "DESC"

    count_result = await session.execute(
        text(f"SELECT COUNT(*) FROM buildings b WHERE {where_sql}"), params
    )
    total = count_result.scalar()

    result = await session.execute(
        text(f"""
            WITH page AS (
                SELECT b.bbl, b.address, b.borough, b.unit_count, b.building_type,
                       b.churn_score, b.churn_category, b.key_signal,
                       b.coverage_ratio, b.outreach_status, b.last_scored_at,
                       b.latitude, b.longitude, b.coordinate_source, b.coordinate_precision
                FROM buildings b
                WHERE {where_sql}
                ORDER BY b.{sort_col} {direction} NULLS LAST
                LIMIT :limit OFFSET :offset
            )
            SELECT page.bbl, page.address, page.borough, page.unit_count, page.building_type,
                   page.churn_score, page.churn_category, page.key_signal,
                   page.coverage_ratio, page.outreach_status, page.last_scored_at,
                   page.latitude, page.longitude, page.coordinate_source, page.coordinate_precision,
                   :lead_id AS current_lead_id,
                   NULL::int AS current_link_count,
                   ARRAY[]::text[] AS current_link_lead_ids,
                   false AS current_link_conflict,
                   NULL::text AS pm_company
            FROM page
        """),
        params,
    )
    buildings = [dict(r._mapping) for r in result]
    for b in buildings:
        b["estimated_annual_revenue"] = estimate_building_revenue(
            b.get("unit_count"),
            b.get("borough"),
            b.get("building_type"),
        )
    return {"buildings": buildings, "total": total, "limit": limit, "offset": offset}


@router.get("/stats")
@limiter.limit("60/minute")
async def building_stats(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    result = await session.execute(text("""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE churn_category = 'hot') AS hot,
            COUNT(*) FILTER (WHERE churn_category = 'warm') AS warm,
            COUNT(*) FILTER (WHERE churn_category = 'stable') AS stable,
            ROUND(AVG(churn_score)::numeric, 1) AS avg_score,
            COUNT(*) FILTER (WHERE churn_score IS NOT NULL) AS scored
        FROM buildings
    """))
    row = result.first()

    outreach_result = await session.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE outreach_status = 'pipeline') AS pipeline,
            COUNT(*) FILTER (WHERE outreach_status = 'contacted') AS contacted,
            COUNT(*) FILTER (WHERE outreach_status = 'meeting') AS meeting,
            COUNT(*) FILTER (WHERE outreach_status = 'won') AS won,
            COUNT(*) FILTER (WHERE outreach_status = 'lost') AS lost,
            COUNT(*) FILTER (WHERE outreach_status IS NOT NULL AND outreach_status != 'none') AS in_pipeline
        FROM buildings
    """))
    orow = outreach_result.first()

    return {
        "total": row[0], "hot": row[1], "warm": row[2], "stable": row[3],
        "avg_score": float(row[4]) if row[4] else 0, "scored": row[5],
        "outreach": {
            "pipeline": orow[0], "contacted": orow[1], "meeting": orow[2],
            "won": orow[3], "lost": orow[4], "in_pipeline": orow[5],
        },
    }


@router.get("/hot")
@limiter.limit("60/minute")
async def hot_buildings(
    request: Request,
    limit: int = Query(default=20, le=100),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    result = await session.execute(
        text("""
            SELECT b.bbl, b.address, b.borough, b.unit_count,
                   b.churn_score, b.churn_category, b.key_signal
            FROM buildings b
            WHERE b.churn_score IS NOT NULL
            ORDER BY b.churn_score DESC
            LIMIT :limit
        """),
        {"limit": limit},
    )
    return [dict(r._mapping) for r in result]


@router.get("/{bbl}")
@limiter.limit("60/minute")
async def get_building(
    request: Request,
    bbl: str,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    canonical_bbl = await _resolve_canonical_bbl(session, bbl)
    if not canonical_bbl:
        raise HTTPException(404, "Building not found")

    result = await session.execute(
        text("""
            SELECT
                b.*,
                m.current_lead_id,
                m.current_link_count,
                m.current_link_lead_ids,
                m.current_link_conflict
            FROM buildings b
            LEFT JOIN LATERAL (
                SELECT
                    CASE
                        WHEN COUNT(*) = 1 AND COUNT(DISTINCT bm.lead_id) = 1 THEN MAX(bm.lead_id)
                        ELSE NULL
                    END AS current_lead_id,
                    COUNT(*)::int AS current_link_count,
                    ARRAY_AGG(DISTINCT bm.lead_id ORDER BY bm.lead_id) AS current_link_lead_ids,
                    (COUNT(*) > 1 OR COUNT(DISTINCT bm.lead_id) > 1) AS current_link_conflict
                FROM building_management bm
                WHERE bm.bbl = b.bbl
                  AND bm.is_current = true
            ) m ON TRUE
            WHERE b.bbl = :bbl
        """),
        {"bbl": canonical_bbl},
    )
    row = result.first()
    if not row:
        raise HTTPException(404, "Building not found")
    building = dict(row._mapping)

    contacts, contact_meta = await get_building_contacts(
        session=session,
        bbl=canonical_bbl,
        building_address=building.get("address"),
    )

    corporate_owner_name = contact_meta.get("corporate_owner")
    dos_status = str(contact_meta.get("dos_contacts_status") or "not_loaded")

    building["all_contacts"] = contacts
    building["management_company"] = contact_meta.get("management_company")
    building["corporate_owner"] = corporate_owner_name
    building["dos_contacts_is_stale"] = bool(contact_meta.get("dos_contacts_is_stale"))
    building["dos_contacts_status"] = dos_status
    building["dos_refresh_requested_at"] = contact_meta.get("dos_refresh_requested_at")
    building["dos_contacts_last_refreshed_at"] = contact_meta.get("dos_contacts_last_refreshed_at")

    return building


@router.post("/{bbl}/dos-contacts/refresh")
@limiter.limit("20/minute")
async def refresh_building_dos_contacts(
    request: Request,
    bbl: str,
    dry_run: bool = Query(True),
    confirm_execute: bool = Query(False),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    if dry_run:
        return {
            "status": "approval_required",
            "bbl": bbl,
            "job_id": None,
            "dry_run": True,
            "confirm_execute": confirm_execute,
            "approval_required": True,
            "safe_to_run_automatically": False,
            "mutations_planned": 0,
            "preview": {
                "operation": "dos_contacts_refresh",
                "would_mutate": ["dos_cache", "external DOS refresh task"],
                "required_execute_query": (
                    f"/api/v1/buildings/{bbl}/dos-contacts/refresh?dry_run=false&confirm_execute=true"
                ),
            },
            "rollback_strategy": "No DOS refresh was queued. Execute only after reviewing the preview and passing confirm_execute=true.",
        }
    if not confirm_execute:
        raise HTTPException(400, "DOS contact refresh execution requires confirm_execute=true")

    canonical_bbl = await _resolve_canonical_bbl(session, bbl)
    if not canonical_bbl:
        raise HTTPException(404, "Building not found")

    result = await session.execute(
        text("SELECT address FROM buildings WHERE bbl = :bbl"),
        {"bbl": canonical_bbl},
    )
    row = result.first()
    if not row:
        raise HTTPException(404, "Building not found")

    _, contact_meta = await get_building_contacts(
        session=session,
        bbl=canonical_bbl,
        building_address=row[0],
    )

    try:
        return await _request_dos_refresh(
            session=session,
            bbl=canonical_bbl,
            corporate_owner_name=contact_meta.get("corporate_owner"),
        )
    except Exception as exc:
        logger.warning("Failed to queue DOS refresh for %s: %s", canonical_bbl, exc)
        raise HTTPException(503, "Failed to queue DOS refresh") from exc


@router.get("/{bbl}/timeline")
@limiter.limit("60/minute")
async def building_timeline(
    request: Request,
    bbl: str,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """Chronological event feed merging all signal sources."""
    canonical_bbl = await _resolve_canonical_bbl(session, bbl)
    if not canonical_bbl:
        raise HTTPException(404, "Building not found")

    events = []

    complaints = await session.execute(
        text("SELECT 'complaint' AS type, received_date AS date, major_category AS detail FROM hpd_complaints WHERE bbl = :bbl ORDER BY received_date DESC LIMIT 50"),
        {"bbl": canonical_bbl},
    )
    events.extend([dict(r._mapping) for r in complaints])

    violations = await session.execute(
        text("SELECT 'violation' AS type, inspection_date AS date, nov_description AS detail FROM hpd_violations WHERE bbl = :bbl ORDER BY inspection_date DESC LIMIT 50"),
        {"bbl": canonical_bbl},
    )
    events.extend([dict(r._mapping) for r in violations])

    transactions = await session.execute(
        text("SELECT 'transaction' AS type, recorded_date AS date, doc_type_description AS detail FROM acris_transactions WHERE bbl = :bbl ORDER BY recorded_date DESC LIMIT 20"),
        {"bbl": canonical_bbl},
    )
    events.extend([dict(r._mapping) for r in transactions])

    permits = await session.execute(
        text("SELECT 'permit' AS type, filing_date AS date, job_description AS detail FROM dob_permits WHERE bbl = :bbl ORDER BY filing_date DESC LIMIT 20"),
        {"bbl": canonical_bbl},
    )
    events.extend([dict(r._mapping) for r in permits])

    litigation = await session.execute(
        text("SELECT 'litigation' AS type, case_open_date AS date, case_type AS detail FROM hpd_litigation WHERE bbl = :bbl ORDER BY case_open_date DESC LIMIT 20"),
        {"bbl": canonical_bbl},
    )
    events.extend([dict(r._mapping) for r in litigation])

    emergency_repairs = await session.execute(
        text("""
            SELECT 'emergency_repair' AS type, order_date AS date,
                   CONCAT_WS(' - ', repair_type, status) AS detail
            FROM emergency_repairs
            WHERE bbl = :bbl
            ORDER BY order_date DESC
            LIMIT 20
        """),
        {"bbl": canonical_bbl},
    )
    events.extend([dict(r._mapping) for r in emergency_repairs])

    evictions = await session.execute(
        text("""
            SELECT 'eviction' AS type, executed_date AS date,
                   COALESCE(case_index_number, eviction_address, 'Eviction filing') AS detail
            FROM eviction_filings
            WHERE bbl = :bbl
            ORDER BY executed_date DESC
            LIMIT 20
        """),
        {"bbl": canonical_bbl},
    )
    events.extend([dict(r._mapping) for r in evictions])

    energy = await session.execute(
        text("""
            SELECT 'energy' AS type,
                   TO_DATE(CAST(year AS TEXT) || '-01-01', 'YYYY-MM-DD') AS date,
                   CONCAT_WS(' ', 'LL33 grade', grade, CASE WHEN score IS NOT NULL THEN CONCAT('(score ', score::TEXT, ')') ELSE NULL END) AS detail
            FROM energy_grades
            WHERE bbl = :bbl
            ORDER BY year DESC NULLS LAST
            LIMIT 10
        """),
        {"bbl": canonical_bbl},
    )
    events.extend([dict(r._mapping) for r in energy])

    facades = await session.execute(
        text("""
            SELECT 'facade' AS type, filing_date AS date,
                   CONCAT_WS(' - ', filing_status, CONCAT('Cycle ', cycle)) AS detail
            FROM facade_inspections
            WHERE bbl = :bbl
            ORDER BY filing_date DESC
            LIMIT 20
        """),
        {"bbl": canonical_bbl},
    )
    events.extend([dict(r._mapping) for r in facades])

    aep = await session.execute(
        text("""
            SELECT 'aep' AS type, designation_date AS date,
                   'AEP designation active' AS detail
            FROM aep_designations
            WHERE bbl = :bbl
            ORDER BY designation_date DESC
            LIMIT 10
        """),
        {"bbl": canonical_bbl},
    )
    events.extend([dict(r._mapping) for r in aep])

    events.sort(key=lambda e: str(e.get("date") or ""), reverse=True)
    return events


@router.get("/{bbl}/lineage")
@limiter.limit("60/minute")
async def get_building_lineage(
    request: Request,
    bbl: str,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    canonical_bbl = await _resolve_canonical_bbl(session, bbl)
    if not canonical_bbl:
        raise HTTPException(status_code=404, detail="Building not found")

    building_row = (await session.execute(
        text("SELECT bbl, address, updated_at FROM buildings WHERE bbl = :bbl"),
        {"bbl": canonical_bbl},
    )).first()
    if not building_row:
        raise HTTPException(status_code=404, detail="Building not found")

    building = dict(building_row._mapping)

    def build_source_row(source_name: str, count: int, last_updated, mapped_fields: list[str], missing_reason: str):
        return {
            "source_name": source_name,
            "record_count": int(count or 0),
            "last_updated": last_updated.isoformat() if last_updated else None,
            "status": "available" if int(count or 0) > 0 else "missing",
            "mapped_fields": mapped_fields,
            "missing_reason": None if int(count or 0) > 0 else missing_reason,
        }

    sources = [
        build_source_row("buildings", 1, building.get("updated_at"), ["address", "borough", "unit_count", "churn_score"], ""),
    ]

    source_specs = [
        ("hpd_complaints", "hpd_complaints", "received_date", ["major_category", "minor_category", "status"]),
        ("hpd_violations", "hpd_violations", "inspection_date", ["violation_class", "current_status"]),
        ("acris_transactions", "acris_transactions", "recorded_date", ["doc_type", "doc_amount"]),
        ("dob_permits", "dob_permits", "filing_date", ["permit_type", "job_description", "estimated_cost"]),
        ("hpd_litigation", "hpd_litigation", "case_open_date", ["case_type", "case_status", "penalty"]),
        ("emergency_repairs", "emergency_repairs", "order_date", ["repair_type", "amount", "status"]),
        ("eviction_filings", "eviction_filings", "executed_date", ["case_index_number", "borough"]),
        ("energy_grades", "energy_grades", "created_at", ["grade", "score", "year"]),
        ("facade_inspections", "facade_inspections", "filing_date", ["filing_status", "cycle"]),
        ("aep_designations", "aep_designations", "designation_date", ["is_active"]),
    ]
    for source_name, table_name, last_col, mapped_fields in source_specs:
        row = (await session.execute(
            text(f"""
                SELECT COUNT(*)::int AS cnt, MAX({last_col}) AS last_updated
                FROM {table_name}
                WHERE bbl = :bbl
            """),
            {"bbl": canonical_bbl},
        )).first()
        sources.append(
            build_source_row(
                source_name,
                int(row.cnt or 0) if row else 0,
                row.last_updated if row else None,
                mapped_fields,
                f"no {source_name} records for building",
            )
        )

    return {
        "bbl": canonical_bbl,
        "address": building.get("address"),
        "last_updated": building.get("updated_at").isoformat() if building.get("updated_at") else None,
        "sources": sources,
    }


@router.get("/{bbl}/score-history")
@limiter.limit("60/minute")
async def building_score_history(
    request: Request,
    bbl: str,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    canonical_bbl = await _resolve_canonical_bbl(session, bbl)
    if not canonical_bbl:
        raise HTTPException(404, "Building not found")

    result = await session.execute(
        text("""
            SELECT churn_score, churn_category, churn_breakdown, scored_at
            FROM building_score_history
            WHERE bbl = :bbl
            ORDER BY scored_at DESC
            LIMIT 52
        """),
        {"bbl": canonical_bbl},
    )
    return [dict(r._mapping) for r in result]


@router.post("/{bbl}/pipeline")
@limiter.limit("30/minute")
async def add_to_pipeline(
    request: Request,
    bbl: str,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    canonical_bbl = await _resolve_canonical_bbl(session, bbl)
    if not canonical_bbl:
        raise HTTPException(404, "Building not found")

    result = await session.execute(
        text("SELECT 1 FROM buildings WHERE bbl = :bbl"), {"bbl": canonical_bbl}
    )
    if not result.first():
        raise HTTPException(404, "Building not found")

    await session.execute(
        text("UPDATE buildings SET outreach_status = 'pipeline', updated_at = now() WHERE bbl = :bbl"),
        {"bbl": canonical_bbl},
    )
    await session.commit()
    return {"bbl": canonical_bbl, "status": "added_to_pipeline"}


@router.patch("/{bbl}/pipeline")
@limiter.limit("30/minute")
async def update_pipeline_status(
    request: Request,
    bbl: str,
    outreach_status: str,
    outreach_priority: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """Update a building's outreach status and priority."""
    valid = {"none", "pipeline", "contacted", "meeting", "won", "lost"}
    if outreach_status not in valid:
        raise HTTPException(400, f"Invalid outreach_status. Must be one of: {', '.join(valid)}")
    canonical_bbl = await _resolve_canonical_bbl(session, bbl)
    if not canonical_bbl:
        raise HTTPException(404, "Building not found")

    result = await session.execute(
        text("SELECT 1 FROM buildings WHERE bbl = :bbl"), {"bbl": canonical_bbl}
    )
    if not result.first():
        raise HTTPException(404, "Building not found")

    sets = ["outreach_status = :os", "updated_at = now()"]
    params: dict = {"bbl": canonical_bbl, "os": outreach_status}
    if outreach_priority is not None:
        sets.append("outreach_priority = :op")
        params["op"] = outreach_priority
    await session.execute(
        text(f"UPDATE buildings SET {', '.join(sets)} WHERE bbl = :bbl"), params
    )
    await session.commit()
    return {"bbl": canonical_bbl, "outreach_status": outreach_status}


@router.post("/{bbl}/outreach-event")
@limiter.limit("30/minute")
async def log_building_outreach_event(
    request: Request,
    bbl: str,
    stage: str,
    method: Optional[str] = None,
    outcome: Optional[str] = None,
    notes: Optional[str] = None,
    next_follow_up: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """Log an outreach event for a building."""
    canonical_bbl = await _resolve_canonical_bbl(session, bbl)
    if not canonical_bbl:
        raise HTTPException(404, "Building not found")

    from datetime import datetime, timezone
    exists = (await session.execute(
        text("SELECT 1 FROM buildings WHERE bbl = :bbl"), {"bbl": canonical_bbl}
    )).first()
    if not exists:
        raise HTTPException(404, "Building not found")

    truth_claim_status = await load_outreach_feedback_truth_write_status(session)
    insert_result = await session.execute(
        text("""
            INSERT INTO outreach_events (bbl, stage, method, outcome, notes, next_follow_up, event_timestamp, created_at, updated_at)
            VALUES (:bbl, :stage, :method, :outcome, :notes, :nfu, :ts, NOW(), NOW())
            RETURNING id
        """),
        {
            "bbl": canonical_bbl, "stage": stage, "method": method,
            "outcome": outcome, "notes": notes, "nfu": next_follow_up,
            "ts": datetime.now(timezone.utc),
        },
    )
    event_id = insert_result.scalar_one()
    truth_claim_ids = []
    if truth_claim_status["ready"]:
        truth_claim_ids = await record_outreach_feedback_claims(
            session,
            lead_id=None,
            event_id=int(event_id),
            method=method,
            outcome=outcome,
            notes=notes,
            bbl=canonical_bbl,
        )

    update_sql = "UPDATE buildings SET outreach_status = :os, updated_at = now()"
    update_params: dict = {"bbl": canonical_bbl, "os": stage}
    if next_follow_up:
        update_sql += ", next_outreach_date = :nod"
        update_params["nod"] = next_follow_up
    update_sql += " WHERE bbl = :bbl"
    await session.execute(text(update_sql), update_params)
    await session.commit()

    return {
        "status": "success",
        "bbl": canonical_bbl,
        "stage": stage,
        "event_id": event_id,
        "truth_claim_ids": truth_claim_ids,
        "truth_claim_status": truth_claim_status,
    }


@router.get("/{bbl}/outreach-events")
@limiter.limit("60/minute")
async def get_building_outreach_events(
    request: Request,
    bbl: str,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    """Get outreach event history for a building."""
    canonical_bbl = await _resolve_canonical_bbl(session, bbl)
    if not canonical_bbl:
        raise HTTPException(404, "Building not found")

    result = await session.execute(
        text("""
            SELECT id, bbl, stage, method, outcome, notes, next_follow_up,
                   event_timestamp, created_at
            FROM outreach_events
            WHERE bbl = :bbl
            ORDER BY event_timestamp DESC
        """),
        {"bbl": canonical_bbl},
    )
    events = [dict(r._mapping) for r in result]
    for e in events:
        for k in ("event_timestamp", "created_at", "next_follow_up"):
            if e.get(k) and hasattr(e[k], "isoformat"):
                e[k] = e[k].isoformat()
    return {"bbl": canonical_bbl, "events": events}
