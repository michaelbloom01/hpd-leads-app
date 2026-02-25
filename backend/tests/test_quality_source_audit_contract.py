"""Contract tests for source audit status logic."""

from src.routers.quality import SOURCE_REGISTRY, _source_row_status


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
