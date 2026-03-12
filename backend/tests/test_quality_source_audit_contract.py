"""Contract tests for source audit status logic."""

from datetime import datetime, timezone

from src.routers.quality import (
    RUNNABLE_JOB_TYPES,
    SOURCE_REGISTRY,
    _pick_latest_quality_row,
    _source_row_status,
)


def test_source_registry_excludes_deprecated_dof_assessment():
    dof = next((s for s in SOURCE_REGISTRY if s["source_name"] == "dof_assessment"), None)
    assert dof is None


def test_source_status_reports_not_wired_before_other_conditions():
    assert _source_row_status(table_exists=True, has_quality_log=True, runnable_job=False) == "not_wired"


def test_source_status_reports_schema_missing():
    assert _source_row_status(table_exists=False, has_quality_log=True, runnable_job=True) == "schema_missing"


def test_source_status_reports_no_recent_ingest():
    assert _source_row_status(table_exists=True, has_quality_log=False, runnable_job=True) == "no_recent_ingest"


def test_source_status_reports_operational():
    assert _source_row_status(table_exists=True, has_quality_log=True, runnable_job=True) == "operational"


def test_coordinate_backfill_source_is_registered_and_runnable():
    source = next((s for s in SOURCE_REGISTRY if s["source_name"] == "building_coordinates"), None)
    assert source is not None
    assert source["job_type"] == "building_coordinates"
    assert "building_coordinates" in RUNNABLE_JOB_TYPES


def test_source_audit_accepts_quality_aliases_for_combined_jobs():
    source = next((s for s in SOURCE_REGISTRY if s["source_name"] == "hpd_registrations"), None)
    assert source is not None
    latest = {
        "hpd_buildings": {"run_timestamp": datetime(2026, 3, 12, tzinfo=timezone.utc)},
    }

    picked = _pick_latest_quality_row(source, latest)

    assert picked == latest["hpd_buildings"]
