from pathlib import Path

from src.tasks.generate_leads import run_generate_leads


def test_run_generate_leads_uses_runtime_service(monkeypatch):
    captured: dict[str, int] = {}

    def fake_generate_leads(min_portfolio: int = 1):
        captured["min_portfolio"] = min_portfolio
        return {"upserted": 7}

    monkeypatch.setattr("src.services.lead_generation.generate_leads", fake_generate_leads)

    result = run_generate_leads(min_portfolio=3)

    assert captured["min_portfolio"] == 3
    assert result == {"upserted": 7}


def test_generate_leads_task_source_does_not_import_scripts():
    source = (
        Path(__file__).resolve().parents[1] / "src" / "tasks" / "generate_leads.py"
    ).read_text(encoding="utf-8")

    assert "from scripts.generate_leads_from_buildings" not in source
    assert "from src.services.lead_generation import generate_leads" in source
