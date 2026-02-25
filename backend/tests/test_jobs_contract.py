"""Tests for jobs API contract helpers."""

from src.routers.jobs import JOB_TYPE_ALIASES, _normalize_status


def test_normalize_status_maps_legacy_completed():
    assert _normalize_status("completed") == "succeeded"


def test_normalize_status_passthrough_for_canonical_values():
    assert _normalize_status("queued") == "queued"
    assert _normalize_status("running") == "running"
    assert _normalize_status("succeeded") == "succeeded"
    assert _normalize_status("failed") == "failed"


def test_normalize_status_handles_none():
    assert _normalize_status(None) == "unknown"


def test_job_type_aliases_cover_source_style_names():
    assert JOB_TYPE_ALIASES["energy_grades"] == "energy"
    assert JOB_TYPE_ALIASES["aep_designations"] == "aep"
    assert JOB_TYPE_ALIASES["eviction_filings"] == "evictions"
    assert JOB_TYPE_ALIASES["facade_inspections"] == "facades"
    assert JOB_TYPE_ALIASES["acris_transactions"] == "acris"
