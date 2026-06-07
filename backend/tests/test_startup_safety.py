import asyncio

import api


def test_startup_skips_legacy_sync_by_default(monkeypatch):
    called = False

    async def fail_if_called():
        nonlocal called
        called = True
        raise AssertionError("startup sync should be opt-in")

    monkeypatch.delenv(api.STARTUP_SYNC_ENV, raising=False)
    monkeypatch.setattr(api, "_run_legacy_startup_sync", fail_if_called)

    asyncio.run(api.startup())

    assert called is False


def test_startup_runs_legacy_sync_only_when_explicitly_enabled(monkeypatch):
    called = False

    async def fake_startup_sync():
        nonlocal called
        called = True
        return {"rows_updated": 0, "leads_50_units_5_buildings": 12}

    monkeypatch.setenv(api.STARTUP_SYNC_ENV, "1")
    monkeypatch.setattr(api, "_run_legacy_startup_sync", fake_startup_sync)

    asyncio.run(api.startup())

    assert called is True
