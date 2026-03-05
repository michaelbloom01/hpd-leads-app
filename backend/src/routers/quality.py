"""Data quality API router.

Surfaces ingestion health for the Data Health Dashboard on the Settings page.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/quality", tags=["data-quality"])

RUNNABLE_JOB_TYPES = {
    "buildings", "hpd_complaints", "acris", "hpd_violations",
    "dob_permits", "hpd_litigation", "emergency_repairs", "aep",
    "evictions", "energy", "facades", "pad", "scoring", "enrichment",
}

SOURCE_REGISTRY = [
    {"source_name": "hpd_registrations", "dataset_id": "tesw-yqqr", "table_name": "buildings", "job_type": "buildings", "ui_surface": "leads+buildings"},
    {"source_name": "hpd_contacts", "dataset_id": "feu5-w2e2", "table_name": "building_contacts", "job_type": "buildings", "ui_surface": "lead_detail_contacts"},
    {"source_name": "pluto", "dataset_id": "64uk-42ks", "table_name": "buildings", "job_type": "buildings", "ui_surface": "lead_detail+building_detail"},
    {"source_name": "hpd_complaints", "dataset_id": "ygpa-z7cr", "table_name": "hpd_complaints", "job_type": "hpd_complaints", "ui_surface": "building_timeline+churn"},
    {"source_name": "hpd_violations", "dataset_id": "wvxf-dwi5", "table_name": "hpd_violations", "job_type": "hpd_violations", "ui_surface": "lead_distress+timeline"},
    {"source_name": "acris_transactions", "dataset_id": "bnx9-e6tj|8h5j-fqxa|636b-3b5g", "table_name": "acris_transactions", "job_type": "acris", "ui_surface": "building_timeline+churn"},
    {"source_name": "dob_permits", "dataset_id": "ipu4-2vj7|rbx6-tga4", "table_name": "dob_permits", "job_type": "dob_permits", "ui_surface": "building_timeline+churn"},
    {"source_name": "hpd_litigation", "dataset_id": "59kj-x8nc", "table_name": "hpd_litigation", "job_type": "hpd_litigation", "ui_surface": "building_timeline+churn"},
    {"source_name": "emergency_repairs", "dataset_id": "24cj-meh5", "table_name": "emergency_repairs", "job_type": "emergency_repairs", "ui_surface": "churn_only"},
    {"source_name": "aep_designations", "dataset_id": "hcir-3275", "table_name": "aep_designations", "job_type": "aep", "ui_surface": "churn_only"},
    {"source_name": "eviction_filings", "dataset_id": "6z8x-wfk4", "table_name": "eviction_filings", "job_type": "evictions", "ui_surface": "churn_only"},
    {"source_name": "energy_grades", "dataset_id": "355w-xvp2", "table_name": "energy_grades", "job_type": "energy", "ui_surface": "churn_only"},
    {"source_name": "facade_inspections", "dataset_id": "xubg-57si", "table_name": "facade_inspections", "job_type": "facades", "ui_surface": "churn_only"},
    {"source_name": "pad", "dataset_id": "bc8t-ecyu", "table_name": "pad_addresses", "job_type": "pad", "ui_surface": "join_crosswalk"},
]


def _source_row_status(table_exists: bool, has_quality_log: bool, runnable_job: bool) -> str:
    if not runnable_job:
        return "not_wired"
    if not table_exists:
        return "schema_missing"
    if not has_quality_log:
        return "no_recent_ingest"
    return "operational"


@router.get("/data-health")
async def data_health(session: AsyncSession = Depends(get_session)):
    """Aggregated data-health metrics for the dashboard badge."""
    counts = await session.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM leads) AS total_leads,
            (SELECT COUNT(*) FROM buildings) AS total_buildings_registered,
            (SELECT COUNT(DISTINCT source_name) FROM data_quality_log) AS hpd_source_count
    """))
    row = dict(counts.first()._mapping)

    # Buildings with at least one signal (complaint or violation data)
    buildings_with_signals = (await session.execute(
        text("""
            SELECT COUNT(DISTINCT b.bbl) FROM buildings b
            WHERE EXISTS (SELECT 1 FROM hpd_complaints c WHERE c.bbl = b.bbl)
               OR EXISTS (SELECT 1 FROM hpd_violations v WHERE v.bbl = b.bbl)
        """)
    )).scalar() or 0

    total_buildings = row["total_buildings_registered"] or 0

    coverage_pct = None
    if total_buildings > 0:
        coverage_pct = round(buildings_with_signals / total_buildings * 100, 1)

    refresh_row = await session.execute(text("""
        SELECT id, status, started_at, finished_at, succeeded, failed
        FROM ingestion_jobs
        WHERE source IN ('hpd_buildings', 'buildings') OR job_type IN ('buildings', 'ingest')
        ORDER BY started_at DESC NULLS LAST
        LIMIT 1
    """))
    refresh = refresh_row.first()
    last_refresh = None
    data_age_days = None
    if refresh:
        r = dict(refresh._mapping)
        finished = r.get("finished_at")
        last_refresh = {
            "started_at": r["started_at"].isoformat() if r.get("started_at") else None,
            "finished_at": finished.isoformat() if finished else None,
            "status": r["status"],
            "leads_net_change": (r.get("succeeded") or 0) - (r.get("failed") or 0),
            "buildings_fetched": r.get("succeeded") or 0,
        }
        if finished:
            data_age_days = (datetime.now(timezone.utc) - finished).days

    stale_result = await session.execute(text("""
        SELECT COUNT(*) FROM buildings
        WHERE updated_at < NOW() - INTERVAL '90 days'
    """))
    stale_count = stale_result.scalar() or 0

    entity_row = (await session.execute(text("""
        WITH source_entities AS (
            SELECT DISTINCT REGEXP_REPLACE(UPPER(TRIM(corporation_name)), '\\s+', ' ', 'g') AS normalized_name
            FROM building_contacts
            WHERE corporation_name IS NOT NULL
              AND TRIM(corporation_name) != ''
        ),
        lead_entities AS (
            SELECT DISTINCT normalized_name
            FROM leads
            WHERE normalized_name IS NOT NULL
              AND TRIM(normalized_name) != ''
        )
        SELECT
            (SELECT COUNT(*) FROM source_entities) AS distinct_entities_in_contacts,
            (SELECT COUNT(*) FROM lead_entities) AS distinct_entities_in_leads,
            (
                SELECT COUNT(*)
                FROM source_entities s
                JOIN lead_entities l ON l.normalized_name = s.normalized_name
            ) AS matched_entities
    """))).first()
    entity_data = dict(entity_row._mapping) if entity_row else {}
    distinct_entities_in_contacts = int(entity_data.get("distinct_entities_in_contacts") or 0)
    distinct_entities_in_leads = int(entity_data.get("distinct_entities_in_leads") or 0)
    matched_entities = int(entity_data.get("matched_entities") or 0)
    entity_coverage_ratio = round((matched_entities / distinct_entities_in_contacts) * 100, 1) if distinct_entities_in_contacts > 0 else None

    buildings_without_contacts = int((await session.execute(text("""
        SELECT COUNT(*)
        FROM buildings b
        WHERE NOT EXISTS (
            SELECT 1 FROM building_contacts bc WHERE bc.bbl = b.bbl
        )
    """))).scalar() or 0)

    lead_generation_row = (await session.execute(text("""
        SELECT started_at, finished_at, status
        FROM ingestion_jobs
        WHERE job_type = 'lead_generation'
        ORDER BY started_at DESC NULLS LAST
        LIMIT 1
    """))).first()
    last_lead_generation = None
    if lead_generation_row:
        lg = dict(lead_generation_row._mapping)
        last_lead_generation = {
            "started_at": lg.get("started_at").isoformat() if lg.get("started_at") else None,
            "finished_at": lg.get("finished_at").isoformat() if lg.get("finished_at") else None,
            "status": lg.get("status"),
        }

    staleness_result = await session.execute(text("""
        SELECT
            COALESCE(SUM(CASE WHEN l.updated_at >= NOW() - INTERVAL '7 days'  THEN 1 ELSE 0 END), 0) AS fresh,
            COALESCE(SUM(CASE WHEN l.updated_at >= NOW() - INTERVAL '30 days'
                              AND l.updated_at <  NOW() - INTERVAL '7 days'  THEN 1 ELSE 0 END), 0) AS recent,
            COALESCE(SUM(CASE WHEN l.updated_at <  NOW() - INTERVAL '30 days' THEN 1 ELSE 0 END), 0) AS stale
        FROM leads l
    """))
    st = dict(staleness_result.first()._mapping)

    enrich_result = await session.execute(text("""
        SELECT
            COALESCE(SUM(CASE WHEN phone IS NOT NULL AND phone != '' THEN 1 ELSE 0 END), 0) AS with_phone,
            COALESCE(SUM(CASE WHEN email IS NOT NULL AND email != '' THEN 1 ELSE 0 END), 0) AS with_email,
            COALESCE(SUM(CASE WHEN website IS NOT NULL AND website != '' THEN 1 ELSE 0 END), 0) AS with_website,
            COUNT(*) AS total
        FROM leads
    """))
    enr = dict(enrich_result.first()._mapping)
    total_l = enr["total"] or 1
    enrichment_coverage = {
        "phone": round(enr["with_phone"] / total_l * 100, 1),
        "email": round(enr["with_email"] / total_l * 100, 1),
        "website": round(enr["with_website"] / total_l * 100, 1),
    }

    warnings: list[str] = []
    if data_age_days is not None and data_age_days > 30:
        warnings.append(f"Building data is {data_age_days} days old")
    if stale_count > 1000:
        warnings.append(f"{stale_count:,} buildings not updated in 90+ days")
    if enrichment_coverage["phone"] < 20:
        warnings.append(f"Low phone coverage: {enrichment_coverage['phone']}%")
    if entity_coverage_ratio is not None and entity_coverage_ratio < 95:
        warnings.append(f"Entity coverage gap: only {entity_coverage_ratio}% of source entities materialized into leads")

    return {
        "total_leads": row["total_leads"],
        "total_buildings_registered": row["total_buildings_registered"],
        "hpd_source_count": row["hpd_source_count"],
        "coverage_percent": coverage_pct,
        "last_refresh": last_refresh,
        "stale_buildings_count": stale_count,
        "buildings_without_contacts": buildings_without_contacts,
        "lead_staleness": {"fresh": st["fresh"], "recent": st["recent"], "stale": st["stale"]},
        "data_age_days": data_age_days,
        "enrichment_coverage": enrichment_coverage,
        "distinct_entities_in_contacts": distinct_entities_in_contacts,
        "distinct_entities_in_leads": distinct_entities_in_leads,
        "matched_entities": matched_entities,
        "entity_coverage_ratio": entity_coverage_ratio,
        "coverage_ratio": entity_coverage_ratio,
        "last_lead_generation": last_lead_generation,
        "last_lead_generation_at": (
            last_lead_generation.get("finished_at")
            if isinstance(last_lead_generation, dict)
            else None
        ),
        "warnings": warnings,
    }


@router.get("/summary")
async def quality_summary(session: AsyncSession = Depends(get_session)):
    """Per-source latest stats for the data health dashboard."""
    result = await session.execute(text("""
        SELECT DISTINCT ON (source_name)
            source_name, records_fetched, records_matched, records_rejected,
            records_inserted, match_rate, volume_anomaly, notes, run_timestamp
        FROM data_quality_log
        ORDER BY source_name, run_timestamp DESC
    """))
    return [dict(r._mapping) for r in result]


@router.get("/history")
async def quality_history(
    source: Optional[str] = None,
    limit: int = Query(default=30, le=200),
    session: AsyncSession = Depends(get_session),
):
    if source:
        result = await session.execute(
            text("""
                SELECT * FROM data_quality_log
                WHERE source_name = :source
                ORDER BY run_timestamp DESC LIMIT :limit
            """),
            {"source": source, "limit": limit},
        )
    else:
        result = await session.execute(
            text("SELECT * FROM data_quality_log ORDER BY run_timestamp DESC LIMIT :limit"),
            {"limit": limit},
        )
    return [dict(r._mapping) for r in result]


@router.get("/coverage")
async def building_coverage(session: AsyncSession = Depends(get_session)):
    """Signal coverage: how many buildings have data for each signal."""
    signal_tables = {
        "with_complaints": "hpd_complaints",
        "with_violations": "hpd_violations",
        "with_transactions": "acris_transactions",
        "with_permits": "dob_permits",
        "with_litigation": "hpd_litigation",
        "with_erp": "emergency_repairs",
        "with_energy": "energy_grades",
        "with_evictions": "eviction_filings",
        "with_facades": "facade_inspections",
        "with_aep": "aep_designations",
    }
    total = (await session.execute(text("SELECT COUNT(*) FROM buildings"))).scalar() or 0
    coverage: dict = {"total_buildings": total}

    # Check which tables actually exist
    existing_tables = set()
    try:
        result = await session.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ))
        existing_tables = {r[0] for r in result}
    except Exception:
        pass

    allowed_tables = set(signal_tables.values())
    for key, table in signal_tables.items():
        if table not in existing_tables or table not in allowed_tables:
            coverage[key] = None
            continue
        try:
            val = (await session.execute(
                text(f"SELECT COUNT(DISTINCT bbl) FROM {table}")
            )).scalar()
            coverage[key] = val or 0
        except Exception:
            coverage[key] = None
            await session.rollback()
    return coverage


@router.get("/source-audit")
async def source_audit(session: AsyncSession = Depends(get_session)):
    """Canonical source integrity matrix: configured vs runnable vs surfaced."""
    tables_result = await session.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    )
    existing_tables = {str(r[0]) for r in tables_result}

    latest_quality_result = await session.execute(text("""
        SELECT DISTINCT ON (source_name)
            source_name, records_fetched, records_inserted, run_timestamp
        FROM data_quality_log
        ORDER BY source_name, run_timestamp DESC
    """))
    latest_quality = {
        str(r._mapping["source_name"]): dict(r._mapping)
        for r in latest_quality_result
    }

    rows = []
    for source in SOURCE_REGISTRY:
        source_name = source["source_name"]
        table_name = source["table_name"]
        job_type = source["job_type"]
        quality = latest_quality.get(source_name)
        table_exists = table_name in existing_tables
        has_quality_log = quality is not None
        runnable_job = job_type in RUNNABLE_JOB_TYPES
        status = _source_row_status(
            table_exists=table_exists,
            has_quality_log=has_quality_log,
            runnable_job=runnable_job,
        )
        rows.append({
            **source,
            "table_exists": table_exists,
            "runnable_job": runnable_job,
            "has_quality_log": has_quality_log,
            "last_run": quality.get("run_timestamp").isoformat() if quality and quality.get("run_timestamp") else None,
            "last_records_fetched": int(quality.get("records_fetched") or 0) if quality else 0,
            "last_records_inserted": int(quality.get("records_inserted") or 0) if quality else 0,
            "status": status,
        })

    counts = {
        "total_sources": len(rows),
        "operational": sum(1 for r in rows if r["status"] == "operational"),
        "not_wired": sum(1 for r in rows if r["status"] == "not_wired"),
        "schema_missing": sum(1 for r in rows if r["status"] == "schema_missing"),
        "no_recent_ingest": sum(1 for r in rows if r["status"] == "no_recent_ingest"),
    }

    critical_gaps = [
        r for r in rows if r["status"] in {"not_wired", "schema_missing"}
    ]

    return {
        "summary": counts,
        "critical_gaps": critical_gaps,
        "sources": rows,
    }
