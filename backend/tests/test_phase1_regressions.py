from pathlib import Path


def _read(path: str) -> str:
    return (Path(__file__).resolve().parents[1] / path).read_text(encoding="utf-8")


def test_entity_resolution_no_removed_building_management_columns():
    source = _read("src/tasks/entity_resolution.py")
    assert "source, effective_date" not in source
    assert "INSERT INTO building_management (bbl, lead_id, role, is_current" in source


def test_quality_checks_no_invalid_update_order_by_limit():
    source = _read("src/tasks/quality_checks.py")
    assert "UPDATE data_quality_log SET volume_anomaly = true" in source
    assert "WHERE source_name = :source\n                        ORDER BY run_timestamp DESC LIMIT 1" not in source
    assert "WHERE id = (" in source


def test_jobs_router_wires_entity_resolution_and_quality_checks():
    source = _read("src/routers/jobs.py")
    assert '"entity_resolution"' in source
    assert '"quality_checks"' in source
    assert "from src.tasks.entity_resolution import resolve_entities" in source
    assert "from src.tasks.quality_checks import run_quality_checks" in source


def test_enrich_batch_queues_individual_jobs_when_available():
    source = _read("src/tasks/enrich.py")
    assert "hasattr(enrich_lead, \"delay\")" in source
    assert "enrich_lead.delay(lid)" in source
