"""Read-only source registry and audit helpers for trust surfaces."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


RUNNABLE_JOB_TYPES = {
    "buildings", "hpd_complaints", "acris", "hpd_violations",
    "dob_permits", "hpd_litigation", "emergency_repairs", "aep",
    "evictions", "energy", "facades", "pad", "scoring", "enrichment",
    "building_coordinates", "board_chairs", "truth_validation", "outreach_feedback",
    "dob_safety",
}

SOURCE_REGISTRY = [
    {"source_name": "hpd_registrations", "dataset_id": "tesw-yqqr", "table_name": "buildings", "job_type": "buildings", "ui_surface": "leads+buildings", "quality_sources": ["hpd_registrations", "hpd_buildings"]},
    {"source_name": "hpd_contacts", "dataset_id": "feu5-w2e2", "table_name": "building_contacts", "job_type": "buildings", "ui_surface": "lead_detail_contacts", "quality_sources": ["hpd_contacts", "hpd_buildings"]},
    {"source_name": "pluto", "dataset_id": "64uk-42ks", "table_name": "buildings", "job_type": "buildings", "ui_surface": "lead_detail+building_detail", "quality_sources": ["pluto", "hpd_buildings"]},
    {"source_name": "building_coordinates", "dataset_id": "planninglabs|nominatim", "table_name": "buildings", "job_type": "building_coordinates", "ui_surface": "portfolio_map"},
    {"source_name": "hpd_complaints", "dataset_id": "ygpa-z7cr", "table_name": "hpd_complaints", "job_type": "hpd_complaints", "ui_surface": "building_timeline+churn"},
    {"source_name": "hpd_violations", "dataset_id": "wvxf-dwi5", "table_name": "hpd_violations", "job_type": "hpd_violations", "ui_surface": "lead_distress+timeline"},
    {"source_name": "acris_transactions", "dataset_id": "bnx9-e6tj|8h5j-fqxa|636b-3b5g", "table_name": "acris_transactions", "job_type": "acris", "ui_surface": "building_timeline+churn", "quality_sources": ["acris_transactions", "acris"]},
    {"source_name": "dob_permits", "dataset_id": "ipu4-2vj7|rbx6-tga4", "table_name": "dob_permits", "job_type": "dob_permits", "ui_surface": "building_timeline+churn"},
    {"source_name": "dob_safety", "dataset_id": "855j-jady", "table_name": "compliance_records", "job_type": "dob_safety", "ui_surface": "lead_compliance+building_compliance", "stale_after_days": 3, "coverage_scope": "explicit BIN pilot", "required_parameters": ["bin"]},
    {"source_name": "hpd_litigation", "dataset_id": "59kj-x8nc", "table_name": "hpd_litigation", "job_type": "hpd_litigation", "ui_surface": "building_timeline+churn"},
    {"source_name": "emergency_repairs", "dataset_id": "24cj-meh5", "table_name": "emergency_repairs", "job_type": "emergency_repairs", "ui_surface": "churn_only"},
    {"source_name": "aep_designations", "dataset_id": "hcir-3275", "table_name": "aep_designations", "job_type": "aep", "ui_surface": "churn_only"},
    {"source_name": "eviction_filings", "dataset_id": "6z8x-wfk4", "table_name": "eviction_filings", "job_type": "evictions", "ui_surface": "churn_only"},
    {"source_name": "energy_grades", "dataset_id": "355w-xvp2", "table_name": "energy_grades", "job_type": "energy", "ui_surface": "churn_only"},
    {"source_name": "facade_inspections", "dataset_id": "xubg-57si", "table_name": "facade_inspections", "job_type": "facades", "ui_surface": "churn_only"},
    {"source_name": "pad", "dataset_id": "bc8t-ecyu", "table_name": "pad_addresses", "job_type": "pad", "ui_surface": "join_crosswalk"},
    {"source_name": "ny_dos_cache", "dataset_id": "ny_dos_api", "table_name": "dos_cache", "job_type": "board_chairs", "ui_surface": "contact_roster+building_detail+data_health", "observed_at_column": "cached_at", "record_count_column": "cache_key", "stale_after_days": 30},
    {"source_name": "google_places", "dataset_id": "google_places_api", "table_name": "enrichment_results", "job_type": "enrichment", "ui_surface": "lead_detail_contacts", "observed_at_column": "fetched_at", "record_count_column": "id", "where_sql": "source = 'google_places'", "stale_after_days": 30},
    {"source_name": "hunter", "dataset_id": "hunter_api", "table_name": "enrichment_results", "job_type": "enrichment", "ui_surface": "lead_detail_contacts", "observed_at_column": "fetched_at", "record_count_column": "id", "where_sql": "source IN ('hunter', 'hunter_domain', 'hunter_person')", "stale_after_days": 30},
    {"source_name": "company_websites", "dataset_id": "web_crawl", "table_name": "enrichment_results", "job_type": "enrichment", "ui_surface": "lead_detail_contacts", "observed_at_column": "fetched_at", "record_count_column": "id", "where_sql": "source IN ('web_crawl', 'company_website')", "stale_after_days": 30},
    {"source_name": "outreach_feedback", "dataset_id": "operator_feedback", "table_name": "outreach_events", "job_type": "outreach_feedback", "ui_surface": "truth_subject_summary+review_queue", "observed_at_column": "event_timestamp", "record_count_column": "id", "refreshable": False, "stale_after_days": 90},
]
DEFAULT_STALE_AFTER_DAYS = 45
SOURCE_STATUS_PRIORITY = {
    "schema_missing": 10,
    "not_wired": 20,
    "no_recent_ingest": 30,
    "stale_ingest": 40,
}


def _coerce_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(value: Any, *, now: datetime | None = None) -> int | None:
    observed = _coerce_utc(value)
    if observed is None:
        return None
    current = _coerce_utc(now) or datetime.now(timezone.utc)
    return max(0, int((current - observed).total_seconds() // 86400))


def _source_row_status(
    table_exists: bool,
    has_quality_log: bool,
    runnable_job: bool,
    *,
    last_run: Any = None,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
    now: datetime | None = None,
) -> str:
    if not runnable_job:
        return "not_wired"
    if not table_exists:
        return "schema_missing"
    if not has_quality_log:
        return "no_recent_ingest"
    source_age_days = _age_days(last_run, now=now)
    if source_age_days is None:
        return "no_recent_ingest"
    if source_age_days > stale_after_days:
        return "stale_ingest"
    return "operational"


def _pick_latest_quality_row(source: dict, latest_quality: dict[str, dict]) -> Optional[dict]:
    quality_sources = source.get("quality_sources") or [source["source_name"]]
    matches = [latest_quality.get(name) for name in quality_sources if latest_quality.get(name)]
    if not matches:
        return None
    return max(matches, key=lambda row: row.get("run_timestamp") or datetime.min.replace(tzinfo=timezone.utc))


async def _load_table_observation(session: AsyncSession, source: dict, *, table_exists: bool) -> dict[str, Any] | None:
    observed_at_column = source.get("observed_at_column")
    if not table_exists or not observed_at_column:
        return None
    table_name = source["table_name"]
    record_count_column = source.get("record_count_column") or "*"
    where_sql = source.get("where_sql")
    where_clause = f"WHERE {where_sql}" if where_sql else ""
    row = (await session.execute(text(f"""
        SELECT
            COUNT({record_count_column})::int AS record_count,
            MAX({observed_at_column}) AS last_observed_at
        FROM {table_name}
        {where_clause}
    """))).first()
    if not row:
        return None
    payload = dict(row._mapping)
    if int(payload.get("record_count") or 0) <= 0:
        return None
    return payload


def build_source_refresh_plan(source_audit: dict[str, Any]) -> dict[str, Any]:
    """Build a no-mutation operator plan for stale or missing source evidence."""
    gaps = list(source_audit.get("critical_gaps") or [])
    grouped: dict[str, dict[str, Any]] = {}
    for gap in gaps:
        if gap.get("refreshable") is False:
            continue
        job_type = str(gap.get("job_type") or gap.get("source_name") or "unknown")
        current = grouped.setdefault(
            job_type,
            {
                "job_type": job_type,
                "sources": [],
                "statuses": set(),
                "priority": 999,
                "blocked": False,
                "approval_required": True,
                "safe_to_run_automatically": False,
                "reason": "",
                "required_parameters": list(gap.get("required_parameters") or []),
            },
        )
        status = str(gap.get("status") or "unknown")
        current["sources"].append({
            "source_name": gap.get("source_name"),
            "status": status,
            "table_name": gap.get("table_name"),
            "last_run": gap.get("last_run"),
            "source_age_days": gap.get("source_age_days"),
        })
        current["statuses"].add(status)
        current["priority"] = min(current["priority"], SOURCE_STATUS_PRIORITY.get(status, 90))

    items: list[dict[str, Any]] = []
    for item in grouped.values():
        statuses = sorted(item.pop("statuses"), key=lambda status: SOURCE_STATUS_PRIORITY.get(status, 90))
        blocked_statuses = {"schema_missing", "not_wired"}.intersection(statuses)
        item["statuses"] = statuses
        item["blocked"] = bool(blocked_statuses)
        item["approval_required"] = not item["blocked"]
        item["recommended_action"] = (
            "Fix schema/job wiring before refreshing this source."
            if item["blocked"]
            else "Refresh this source through the ingestion job after explicit approval."
        )
        item["blocked_reason"] = ", ".join(sorted(blocked_statuses)) if blocked_statuses else None
        item["reason"] = ", ".join(statuses)
        item["job_start_endpoint"] = None if item["blocked"] else f"/api/v1/jobs/{item['job_type']}/start"
        item["preview_endpoint"] = None if item["blocked"] else f"/api/v1/jobs/{item['job_type']}/start"
        item["execute_endpoint"] = None if item["blocked"] else f"/api/v1/jobs/{item['job_type']}/start?dry_run=false&confirm_execute=true"
        if item["job_type"] == "dob_safety" and not item["blocked"]:
            item["recommended_action"] = "Select 1-100 exact DOB BINs, run dob_safety_preview, then execute the reviewed pilot scope."
            item["source_preview_endpoint"] = "/api/v1/jobs/dob_safety_preview/start?dry_run=true"
            item["required_parameters"] = ["bin"]
        if item["job_type"] == "buildings" and not item["blocked"]:
            item["recommended_action"] = "Run buildings_preview, review the complete source diff and worker capacity, then execute using its expected_source_fingerprint."
            item["source_preview_endpoint"] = "/api/v1/jobs/buildings_preview/start?dry_run=true"
            item["required_parameters"] = ["expected_source_fingerprint"]
        items.append(item)

    items.sort(key=lambda item: (item["priority"], item["job_type"]))
    executable = [item for item in items if not item["blocked"]]
    blocked = [item for item in items if item["blocked"]]
    return {
        "dry_run": True,
        "mutations_planned": 0,
        "approval_required": bool(executable),
        "safe_to_run_automatically": False,
        "summary": {
            "planned_job_count": len(items),
            "refreshable_job_count": len(executable),
            "blocked_job_count": len(blocked),
            "affected_source_count": len(gaps),
            "non_refreshable_gap_count": sum(1 for gap in gaps if gap.get("refreshable") is False),
        },
        "items": items,
        "rollback_strategy": "Plan is read-only. Actual refresh jobs mutate source tables and data-quality logs, so run them only after explicit approval.",
    }


async def load_source_audit(session: AsyncSession) -> dict[str, Any]:
    """Return the configured source matrix without mutating data."""
    now = datetime.now(timezone.utc)
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
        table_name = source["table_name"]
        job_type = source["job_type"]
        quality = _pick_latest_quality_row(source, latest_quality)
        table_exists = table_name in existing_tables
        table_observation = await _load_table_observation(session, source, table_exists=table_exists)
        has_quality_log = quality is not None or table_observation is not None
        runnable_job = job_type in RUNNABLE_JOB_TYPES
        last_run = (
            quality.get("run_timestamp")
            if quality
            else (table_observation or {}).get("last_observed_at")
        )
        source_age_days = _age_days(last_run, now=now)
        status = _source_row_status(
            table_exists=table_exists,
            has_quality_log=has_quality_log,
            runnable_job=runnable_job,
            last_run=last_run,
            stale_after_days=int(source.get("stale_after_days") or DEFAULT_STALE_AFTER_DAYS),
            now=now,
        )
        rows.append({
            **source,
            "table_exists": table_exists,
            "runnable_job": runnable_job,
            "has_quality_log": has_quality_log,
            "last_run": last_run.isoformat() if isinstance(last_run, datetime) else None,
            "source_age_days": source_age_days,
            "stale_after_days": int(source.get("stale_after_days") or DEFAULT_STALE_AFTER_DAYS),
            "last_records_fetched": (
                int(quality.get("records_fetched") or 0)
                if quality
                else int((table_observation or {}).get("record_count") or 0)
            ),
            "last_records_inserted": (
                int(quality.get("records_inserted") or 0)
                if quality
                else int((table_observation or {}).get("record_count") or 0)
            ),
            "refreshable": source.get("refreshable", True),
            "status": status,
        })

    counts = {
        "total_sources": len(rows),
        "operational": sum(1 for r in rows if r["status"] == "operational"),
        "not_wired": sum(1 for r in rows if r["status"] == "not_wired"),
        "schema_missing": sum(1 for r in rows if r["status"] == "schema_missing"),
        "no_recent_ingest": sum(1 for r in rows if r["status"] == "no_recent_ingest"),
        "stale_ingest": sum(1 for r in rows if r["status"] == "stale_ingest"),
    }
    critical_gaps = [
        r for r in rows if r["status"] in {"not_wired", "schema_missing", "no_recent_ingest", "stale_ingest"}
    ]

    return {
        "dry_run": True,
        "mutations_planned": 0,
        "summary": counts,
        "critical_gaps": critical_gaps,
        "sources": rows,
        "refresh_plan": build_source_refresh_plan({"critical_gaps": critical_gaps}),
    }
