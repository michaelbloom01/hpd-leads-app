"""Data quality API router.

Surfaces ingestion health for the Data Health Dashboard on the Settings page.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_session
from src.services.board_chair_benchmark import (
    BOARD_CHAIR_GOLDEN_CASES,
    evaluate_board_chair_case,
)
from src.services.source_audit import (
    RUNNABLE_JOB_TYPES,
    SOURCE_REGISTRY,
    _pick_latest_quality_row,
    _source_row_status,
    load_source_audit,
)
from src.services.truth_health import is_truth_schema_current, load_truth_schema_status

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/quality", tags=["data-quality"])

__all__ = [
    "RUNNABLE_JOB_TYPES",
    "SOURCE_REGISTRY",
    "_pick_latest_quality_row",
    "_source_row_status",
]

def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


async def _load_canonical_materialization_stats(session: AsyncSession) -> dict[str, Any]:
    counts_row = (await session.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM canonical_entities) AS entity_count,
            (SELECT COUNT(*) FROM canonical_entity_aliases) AS alias_count,
            (SELECT COUNT(*) FROM canonical_entity_leads) AS lead_membership_count,
            (SELECT COUNT(*) FROM canonical_entity_buildings) AS building_membership_count,
            (SELECT COUNT(*) FROM canonical_entity_match_proposals) AS proposal_count,
            (SELECT COUNT(*) FROM canonical_entity_match_proposals WHERE safe_to_execute = true) AS safe_proposal_count
    """))).first()
    bucket_rows = await session.execute(text("""
        SELECT bucket, COUNT(*) AS cnt
        FROM canonical_entity_match_proposals
        GROUP BY bucket
        ORDER BY bucket
    """))
    counts = dict(counts_row._mapping) if counts_row else {}
    return {
        "entity_count": int(counts.get("entity_count") or 0),
        "alias_count": int(counts.get("alias_count") or 0),
        "lead_membership_count": int(counts.get("lead_membership_count") or 0),
        "building_membership_count": int(counts.get("building_membership_count") or 0),
        "proposal_count": int(counts.get("proposal_count") or 0),
        "safe_proposal_count": int(counts.get("safe_proposal_count") or 0),
        "bucket_counts": {
            str(row.bucket): int(row.cnt or 0)
            for row in bucket_rows
        },
    }


async def _load_truth_confidence_stats(session: AsyncSession) -> dict[str, Any]:
    schema_status = await load_truth_schema_status(session)
    if not is_truth_schema_current(schema_status):
        return {
            "claim_count": 0,
            "verified_claim_count": 0,
            "conflicting_claim_count": 0,
            "open_review_count": 0,
            "active_golden_case_count": 0,
            "actionability_distribution": {},
            "schema_status": schema_status,
        }

    counts_row = (await session.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM truth_claims) AS claim_count,
            (SELECT COUNT(*) FROM truth_claims WHERE belief_status = 'verified') AS verified_claim_count,
            (SELECT COUNT(*) FROM truth_claims WHERE belief_status = 'conflicting') AS conflicting_claim_count,
            (SELECT COUNT(*) FROM truth_review_items WHERE status = 'open') AS open_review_count,
            (SELECT COUNT(*) FROM golden_verification_cases WHERE active = true) AS active_golden_case_count
    """))).first()
    counts = dict(counts_row._mapping) if counts_row else {}
    actionability_rows = await session.execute(text("""
        SELECT actionability_level, COUNT(*) AS cnt
        FROM truth_claims
        GROUP BY actionability_level
        ORDER BY actionability_level
    """))
    return {
        "claim_count": int(counts.get("claim_count") or 0),
        "verified_claim_count": int(counts.get("verified_claim_count") or 0),
        "conflicting_claim_count": int(counts.get("conflicting_claim_count") or 0),
        "open_review_count": int(counts.get("open_review_count") or 0),
        "active_golden_case_count": int(counts.get("active_golden_case_count") or 0),
        "actionability_distribution": {
            str(row.actionability_level or "none"): int(row.cnt or 0)
            for row in actionability_rows
        },
    }


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

    leads_with_zero_active_links = int((await session.execute(text("""
        SELECT COUNT(*)
        FROM leads l
        WHERE NOT EXISTS (
            SELECT 1
            FROM building_management bm
            WHERE bm.lead_id = l.lead_id
              AND bm.is_current = true
        )
    """))).scalar() or 0)

    multilink_row = (await session.execute(text("""
        WITH current_links AS (
            SELECT bm.bbl, bm.role, bm.lead_id, COALESCE(NULLIF(TRIM(l.normalized_name), ''), bm.lead_id) AS normalized_entity
            FROM building_management bm
            LEFT JOIN leads l ON l.lead_id = bm.lead_id
            WHERE bm.is_current = true
        )
        SELECT
            COALESCE((
                SELECT COUNT(*)
                FROM (
                    SELECT bbl
                    FROM current_links
                    GROUP BY bbl
                    HAVING COUNT(*) > 1
                ) any_role
            ), 0) AS any_role_count,
            COALESCE((
                SELECT COUNT(*)
                FROM (
                    SELECT bbl
                    FROM current_links
                    GROUP BY bbl, COALESCE(role, '')
                    HAVING COUNT(*) > 1
                ) same_role
            ), 0) AS same_role_count,
            COALESCE((
                SELECT COUNT(*)
                FROM (
                    SELECT bbl
                    FROM current_links
                    GROUP BY bbl, normalized_entity
                    HAVING COUNT(*) > 1
                ) same_entity
            ), 0) AS same_entity_count
    """))).first()
    multilink_counts = dict(multilink_row._mapping) if multilink_row else {}
    buildings_with_multiple_current_pm_links = int(multilink_counts.get("any_role_count") or 0)
    buildings_with_multiple_current_same_role_links = int(multilink_counts.get("same_role_count") or 0)
    buildings_with_multiple_current_same_entity_links = int(multilink_counts.get("same_entity_count") or 0)

    blank_display_name_leads = int((await session.execute(text("""
        SELECT COUNT(*)
        FROM leads l
        WHERE COALESCE(NULLIF(TRIM(l.company_name), ''), NULLIF(TRIM(l.agent_name), ''), NULLIF(TRIM(l.owner_name), ''), NULLIF(TRIM(l.primary_contact), '')) IS NULL
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

    canonical_prep_row = (await session.execute(text("""
        SELECT id, status, started_at, finished_at, config
        FROM ingestion_jobs
        WHERE job_type = 'entity_resolution'
        ORDER BY started_at DESC NULLS LAST, id DESC
        LIMIT 1
    """))).first()
    canonical_prep = None
    if canonical_prep_row:
        cp = dict(canonical_prep_row._mapping)
        config = _parse_json_object(cp.get("config"))
        preview = _parse_json_object(config.get("preview"))
        canonical_prep = {
            "job_id": int(cp.get("id")),
            "status": cp.get("status"),
            "started_at": cp.get("started_at").isoformat() if cp.get("started_at") else None,
            "finished_at": cp.get("finished_at").isoformat() if cp.get("finished_at") else None,
            "mode": config.get("mode"),
            "dry_run": bool(config.get("dry_run", False)),
            "confirm_execute": bool(config.get("confirm_execute", False)),
            "cohort_filter": config.get("cohort_filter"),
            "write_permitted": bool(config.get("write_permitted", False)),
            "preview_counts": preview.get("counts"),
            "guardrails": preview.get("guardrails"),
        }
    canonical_materialized = await _load_canonical_materialization_stats(session)
    truth_confidence = await _load_truth_confidence_stats(session)
    if canonical_prep is None and canonical_materialized["proposal_count"] > 0:
        canonical_prep = {
            "job_id": None,
            "status": "materialized",
            "started_at": None,
            "finished_at": None,
            "mode": "materialized_only",
            "dry_run": False,
            "confirm_execute": False,
            "cohort_filter": None,
            "write_permitted": False,
            "preview_counts": None,
            "guardrails": None,
        }
    if canonical_prep is not None:
        canonical_prep["materialized"] = canonical_materialized

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
    if leads_with_zero_active_links > 0:
        warnings.append(f"{leads_with_zero_active_links:,} leads have no active building links")
    if blank_display_name_leads > 0:
        warnings.append(f"{blank_display_name_leads:,} leads are missing a display name")
    if buildings_with_multiple_current_same_entity_links > 0:
        warnings.append(
            f"{buildings_with_multiple_current_same_entity_links:,} buildings have duplicate current links to the same entity"
        )
    if canonical_prep and canonical_prep.get("preview_counts"):
        preview_counts = canonical_prep["preview_counts"] or {}
        if int(preview_counts.get("review_required") or 0) > 0:
            warnings.append(
                f"{int(preview_counts.get('review_required') or 0):,} canonical clusters still require review"
            )
        if int(preview_counts.get("unresolved") or 0) > 0:
            warnings.append(
                f"{int(preview_counts.get('unresolved') or 0):,} canonical clusters remain unresolved"
            )
    if canonical_materialized["proposal_count"] > 0:
        warnings.append(
            f"{canonical_materialized['proposal_count']:,} canonical proposals are materialized for audit"
        )
        review_required = int(canonical_materialized["bucket_counts"].get("review_required") or 0)
        if review_required > 0:
            warnings.append(f"{review_required:,} materialized canonical proposals still require review")
    if truth_confidence["conflicting_claim_count"] > 0:
        warnings.append(f"{truth_confidence['conflicting_claim_count']:,} truth claims have conflicting evidence")
    if truth_confidence["open_review_count"] > 0:
        warnings.append(f"{truth_confidence['open_review_count']:,} truth review items are open")

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
        "integrity": {
            "leads_with_zero_active_links": leads_with_zero_active_links,
            "buildings_with_multiple_current_pm_links": buildings_with_multiple_current_pm_links,
            "buildings_with_multiple_current_same_role_links": buildings_with_multiple_current_same_role_links,
            "buildings_with_multiple_current_same_entity_links": buildings_with_multiple_current_same_entity_links,
            "blank_display_name_leads": blank_display_name_leads,
        },
        "last_lead_generation": last_lead_generation,
        "last_lead_generation_at": (
            last_lead_generation.get("finished_at")
            if isinstance(last_lead_generation, dict)
            else None
        ),
        "canonical_prep": canonical_prep,
        "canonical_materialized": canonical_materialized,
        "truth_confidence": truth_confidence,
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


@router.get("/board-chair-coverage")
async def board_chair_coverage(session: AsyncSession = Depends(get_session)):  # noqa: B008
    """Coverage and reliability tiers for actual board-chair evidence."""
    row = (await session.execute(text("""
        WITH eligible AS (
            SELECT bbl
            FROM buildings
            WHERE UPPER(COALESCE(building_type, '')) LIKE '%CONDO%'
               OR UPPER(COALESCE(building_type, '')) LIKE '%COOP%'
               OR UPPER(COALESCE(building_type, '')) LIKE '%CO-OP%'
               OR UPPER(COALESCE(building_class, '')) LIKE 'R%'
               OR UPPER(COALESCE(building_class, '')) IN ('C6', 'C8', 'D0', 'D4')
        ), cache AS (
            SELECT
                REPLACE(cache_key, 'officers:', '') AS bbl,
                CAST(result AS jsonb) AS payload,
                cached_at,
                expires_at
            FROM dos_cache
            WHERE cache_key LIKE 'officers:%'
              AND result IS NOT NULL
        )
        SELECT
            (SELECT COUNT(*) FROM buildings)::int AS total_buildings,
            COUNT(*)::int AS eligible_buildings,
            COUNT(*) FILTER (
                WHERE cache.payload->>'entity_match_status' = 'exact'
                  AND NULLIF(TRIM(cache.payload->>'ceo_name'), '') IS NOT NULL
                  AND COALESCE(cache.expires_at, cache.cached_at + INTERVAL '30 days') > NOW()
            )::int AS current_exact_chair,
            COUNT(*) FILTER (
                WHERE cache.payload->>'entity_match_status' = 'exact'
                  AND NULLIF(TRIM(cache.payload->>'ceo_name'), '') IS NOT NULL
                  AND COALESCE(cache.expires_at, cache.cached_at + INTERVAL '30 days') <= NOW()
            )::int AS stale_exact_chair,
            COUNT(*) FILTER (
                WHERE cache.payload->>'entity_match_status' IN ('possible', 'ambiguous')
                   OR cache.payload->>'chair_status' = 'ambiguous_entity'
            )::int AS ambiguous_or_possible,
            COUNT(*) FILTER (
                WHERE cache.payload->>'chair_status' = 'exact_no_chair'
            )::int AS exact_entity_without_chair,
            COUNT(*) FILTER (
                WHERE cache.payload->>'chair_status' IN ('no_named_chair_match', 'no_match')
            )::int AS no_named_chair_match,
            COUNT(*) FILTER (WHERE cache.bbl IS NULL)::int AS not_loaded
        FROM eligible
        LEFT JOIN cache USING (bbl)
    """))).first()
    counts = dict(row._mapping) if row else {}
    head_officer_count = int((await session.execute(text("""
        SELECT COUNT(DISTINCT bc.bbl)
        FROM building_contacts bc
        JOIN buildings b ON b.bbl = bc.bbl
        WHERE UPPER(COALESCE(bc.contact_type, '')) = 'HEADOFFICER'
          AND (
              UPPER(COALESCE(b.building_type, '')) LIKE '%CONDO%'
              OR UPPER(COALESCE(b.building_type, '')) LIKE '%COOP%'
              OR UPPER(COALESCE(b.building_type, '')) LIKE '%CO-OP%'
              OR UPPER(COALESCE(b.building_class, '')) LIKE 'R%'
              OR UPPER(COALESCE(b.building_class, '')) IN ('C6', 'C8', 'D0', 'D4')
          )
    """))).scalar() or 0)
    eligible = int(counts.get("eligible_buildings") or 0)
    total_buildings = int(counts.get("total_buildings") or 0)
    current = int(counts.get("current_exact_chair") or 0)
    sourced = current + int(counts.get("stale_exact_chair") or 0)
    return {
        **{key: int(value or 0) for key, value in counts.items()},
        "hpd_head_officer_proxy": head_officer_count,
        "hpd_head_officer_included_in_chair_coverage": False,
        "current_exact_coverage": round(current / eligible, 4) if eligible else 0.0,
        "current_exact_all_buildings_coverage": round(current / total_buildings, 4) if total_buildings else 0.0,
        "any_sourced_chair_coverage": round(sourced / eligible, 4) if eligible else 0.0,
        "reliability_policy": {
            "current_exact_chair": "High entity-identity confidence and medium board-role confidence. DOS reports a Chairman or CEO and the cache is no older than 30 days.",
            "stale_exact_chair": "High entity-identity confidence and medium board-role confidence at the source date. Currentness requires refresh.",
            "ambiguous_or_possible": "Review required. Never presented as Board Head.",
            "hpd_head_officer_proxy": "Separate HPD registration role. Excluded from board-chair coverage.",
        },
    }


@router.get("/board-chair-benchmark")
async def board_chair_benchmark(session: AsyncSession = Depends(get_session)):  # noqa: B008
    """Compare ten public board-leadership examples with the current DOS cache."""
    bbls = [case["bbl"] for case in BOARD_CHAIR_GOLDEN_CASES]
    rows = (await session.execute(
        text("""
            SELECT REPLACE(cache_key, 'officers:', '') AS bbl, result, cached_at
            FROM dos_cache
            WHERE cache_key = ANY(CAST(:cache_keys AS text[]))
        """),
        {"cache_keys": [f"officers:{bbl}" for bbl in bbls]},
    )).fetchall()
    cache_by_bbl = {
        str(row.bbl): {"result": row.result, "cached_at": row.cached_at}
        for row in rows
    }
    cases = []
    for case in BOARD_CHAIR_GOLDEN_CASES:
        cached = cache_by_bbl.get(case["bbl"], {})
        cases.append(evaluate_board_chair_case(
            dict(case),
            cached.get("result"),
            cached.get("cached_at"),
        ))
    status_counts: dict[str, int] = {}
    for case in cases:
        status = str(case["status"])
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "total_cases": len(cases),
        "identity_matches": sum(1 for case in cases if case["identity_match"]),
        "status_counts": status_counts,
        "cases": cases,
        "interpretation": "The source proves the named role at its publication date. Evidence older than one year is historical and does not establish current leadership by itself.",
    }


@router.get("/source-audit")
async def source_audit(session: AsyncSession = Depends(get_session)):
    """Canonical source integrity matrix: configured vs runnable vs surfaced."""
    return await load_source_audit(session)
