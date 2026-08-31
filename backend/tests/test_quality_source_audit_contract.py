"""Contract tests for source audit status logic."""

from datetime import datetime, timezone

from src.routers.quality import (
    RUNNABLE_JOB_TYPES,
    SOURCE_REGISTRY,
    _pick_latest_quality_row,
    _source_row_status,
)
from src.services.source_audit import build_source_refresh_plan


def test_source_registry_excludes_deprecated_dof_assessment():
    dof = next((s for s in SOURCE_REGISTRY if s["source_name"] == "dof_assessment"), None)
    assert dof is None


def test_compliance_sources_are_registered_as_bounded_refreshes():
    by_name = {source["source_name"]: source for source in SOURCE_REGISTRY}
    assert {
        name: (by_name[name]["dataset_id"], by_name[name]["required_parameters"])
        for name in (
            "dob_safety",
            "dob_complaints",
            "dob_violations",
            "dob_ecb",
            "oath_ecb",
        )
    } == {
        "dob_safety": ("855j-jady", ["bin"]),
        "dob_complaints": ("eabe-havv", ["bin"]),
        "dob_violations": ("3h2n-5cm9", ["bin"]),
        "dob_ecb": ("6bgk-3dad", ["bin"]),
        "oath_ecb": ("jz4z-kudi", ["bin"]),
    }


def test_source_status_reports_not_wired_before_other_conditions():
    assert _source_row_status(table_exists=True, has_quality_log=True, runnable_job=False) == "not_wired"


def test_source_status_reports_schema_missing():
    assert _source_row_status(table_exists=False, has_quality_log=True, runnable_job=True) == "schema_missing"


def test_source_status_reports_no_recent_ingest():
    assert _source_row_status(table_exists=True, has_quality_log=False, runnable_job=True) == "no_recent_ingest"


def test_source_status_reports_stale_ingest():
    assert _source_row_status(
        table_exists=True,
        has_quality_log=True,
        runnable_job=True,
        last_run=datetime(2026, 1, 1, tzinfo=timezone.utc),
        now=datetime(2026, 3, 1, tzinfo=timezone.utc),
        stale_after_days=45,
    ) == "stale_ingest"


def test_source_status_reports_operational():
    assert _source_row_status(
        table_exists=True,
        has_quality_log=True,
        runnable_job=True,
        last_run=datetime(2026, 2, 20, tzinfo=timezone.utc),
        now=datetime(2026, 3, 1, tzinfo=timezone.utc),
    ) == "operational"


def test_coordinate_backfill_source_is_registered_and_runnable():
    source = next((s for s in SOURCE_REGISTRY if s["source_name"] == "building_coordinates"), None)
    assert source is not None
    assert source["job_type"] == "building_coordinates"
    assert "building_coordinates" in RUNNABLE_JOB_TYPES


def test_external_verification_sources_are_explicitly_audited():
    source_names = {source["source_name"] for source in SOURCE_REGISTRY}

    assert {
        "ny_dos_cache",
        "google_places",
        "hunter",
        "company_websites",
        "outreach_feedback",
    }.issubset(source_names)
    outreach = next(source for source in SOURCE_REGISTRY if source["source_name"] == "outreach_feedback")
    assert outreach["refreshable"] is False
    assert outreach["job_type"] == "outreach_feedback"
    assert "outreach_feedback" in RUNNABLE_JOB_TYPES
    dos = next(source for source in SOURCE_REGISTRY if source["source_name"] == "ny_dos_cache")
    assert dos["job_type"] == "board_chairs"
    assert "board_chairs" in RUNNABLE_JOB_TYPES


def test_source_audit_accepts_quality_aliases_for_combined_jobs():
    source = next((s for s in SOURCE_REGISTRY if s["source_name"] == "hpd_registrations"), None)
    assert source is not None
    latest = {
        "hpd_buildings": {"run_timestamp": datetime(2026, 3, 12, tzinfo=timezone.utc)},
    }

    picked = _pick_latest_quality_row(source, latest)

    assert picked == latest["hpd_buildings"]


def test_source_refresh_plan_groups_gaps_by_job_and_preserves_approval_gate():
    plan = build_source_refresh_plan({
        "critical_gaps": [
            {
                "source_name": "hpd_registrations",
                "job_type": "buildings",
                "status": "stale_ingest",
                "table_name": "buildings",
                "source_age_days": 60,
            },
            {
                "source_name": "hpd_contacts",
                "job_type": "buildings",
                "status": "no_recent_ingest",
                "table_name": "building_contacts",
            },
            {
                "source_name": "dob_permits",
                "job_type": "dob_permits",
                "status": "schema_missing",
                "table_name": "dob_permits",
            },
        ],
    })

    assert plan["dry_run"] is True
    assert plan["mutations_planned"] == 0
    assert plan["approval_required"] is True
    assert plan["safe_to_run_automatically"] is False
    assert plan["summary"] == {
        "planned_job_count": 2,
        "refreshable_job_count": 1,
        "blocked_job_count": 1,
        "affected_source_count": 3,
        "non_refreshable_gap_count": 0,
    }
    assert plan["items"][0]["job_type"] == "dob_permits"
    assert plan["items"][0]["blocked"] is True
    assert plan["items"][0]["approval_required"] is False
    assert plan["items"][0]["preview_endpoint"] is None
    assert plan["items"][0]["execute_endpoint"] is None
    assert plan["items"][0]["blocked_reason"] == "schema_missing"
    assert plan["items"][1]["job_type"] == "buildings"
    assert plan["items"][1]["approval_required"] is True
    assert len(plan["items"][1]["sources"]) == 2
    assert plan["items"][1]["preview_endpoint"] == "/api/v1/jobs/buildings/start"
    assert plan["items"][1]["execute_endpoint"] == "/api/v1/jobs/buildings/start?dry_run=false&confirm_execute=true"
    assert plan["items"][1]["required_parameters"] == ["expected_source_fingerprint"]
    assert plan["items"][1]["source_preview_endpoint"] == "/api/v1/jobs/buildings_preview/start?dry_run=true"


def test_compliance_refresh_plan_requires_explicit_pilot_scope():
    plan = build_source_refresh_plan({"critical_gaps": [{
        "source_name": "dob_safety", "job_type": "dob_safety", "status": "no_recent_ingest",
    }]})
    assert plan["items"][0]["required_parameters"] == ["bin"]
    assert plan["items"][0]["source_preview_endpoint"] == "/api/v1/jobs/dob_safety_preview/start?dry_run=true"
    source = next(item for item in SOURCE_REGISTRY if item["source_name"] == "dob_safety")
    assert source["coverage_scope"] == "explicit BIN pilot"


def test_source_refresh_plan_excludes_non_refreshable_feedback_streams():
    plan = build_source_refresh_plan({
        "critical_gaps": [
            {
                "source_name": "outreach_feedback",
                "job_type": "outreach_feedback",
                "status": "no_recent_ingest",
                "table_name": "outreach_events",
                "refreshable": False,
            }
        ],
    })

    assert plan["summary"] == {
        "planned_job_count": 0,
        "refreshable_job_count": 0,
        "blocked_job_count": 0,
        "affected_source_count": 1,
        "non_refreshable_gap_count": 1,
    }
    assert plan["items"] == []


def test_source_refresh_plan_with_only_blocked_jobs_does_not_request_execution_approval():
    plan = build_source_refresh_plan({
        "critical_gaps": [
            {
                "source_name": "dob_permits",
                "job_type": "dob_permits",
                "status": "schema_missing",
                "table_name": "dob_permits",
            },
            {
                "source_name": "legacy_source",
                "job_type": "legacy_source",
                "status": "not_wired",
                "table_name": "legacy_source",
            },
        ],
    })

    assert plan["approval_required"] is False
    assert plan["safe_to_run_automatically"] is False
    assert plan["summary"] == {
        "planned_job_count": 2,
        "refreshable_job_count": 0,
        "blocked_job_count": 2,
        "affected_source_count": 2,
        "non_refreshable_gap_count": 0,
    }
    assert all(item["blocked"] for item in plan["items"])
    assert all(item["execute_endpoint"] is None for item in plan["items"])
