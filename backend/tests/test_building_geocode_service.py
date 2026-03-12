from __future__ import annotations

import requests

from src.services import building_geocode


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_geocode_building_prefers_planninglabs(monkeypatch):
    building_geocode.geocode_building.cache_clear()
    monkeypatch.setattr(building_geocode.time, "sleep", lambda *_args, **_kwargs: None)

    calls: list[str] = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _FakeResponse(
            {
                "features": [
                    {"geometry": {"coordinates": [-73.9857, 40.7484]}},
                ]
            }
        )

    monkeypatch.setattr(building_geocode.requests, "get", fake_get)

    result = building_geocode.geocode_building("350 5th Ave", "Manhattan")

    assert result is not None
    assert result.latitude == 40.7484
    assert result.longitude == -73.9857
    assert result.coordinate_source == "planninglabs"
    assert result.coordinate_precision == "parcel"
    assert calls == [building_geocode.PLANNING_LABS_URL]


def test_geocode_building_falls_back_to_nominatim(monkeypatch):
    building_geocode.geocode_building.cache_clear()
    monkeypatch.setattr(building_geocode.time, "sleep", lambda *_args, **_kwargs: None)

    calls: list[str] = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if url == building_geocode.PLANNING_LABS_URL:
            return _FakeResponse({"features": []})
        if url == building_geocode.NOMINATIM_URL:
            return _FakeResponse([{"lat": "40.7614", "lon": "-73.9776"}])
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(building_geocode.requests, "get", fake_get)

    result = building_geocode.geocode_building("135 W 50th St", "Manhattan")

    assert result is not None
    assert result.latitude == 40.7614
    assert result.longitude == -73.9776
    assert result.coordinate_source == "nominatim"
    assert result.coordinate_precision == "address"
    assert calls == [
        building_geocode.PLANNING_LABS_URL,
        building_geocode.NOMINATIM_URL,
    ]


def test_geocode_building_retries_retryable_planninglabs_failure(monkeypatch):
    building_geocode.geocode_building.cache_clear()
    monkeypatch.setattr(building_geocode.time, "sleep", lambda *_args, **_kwargs: None)

    attempts = {"planninglabs": 0}

    def fake_get(url, **kwargs):
        if url == building_geocode.PLANNING_LABS_URL:
            attempts["planninglabs"] += 1
            if attempts["planninglabs"] < 3:
                return _FakeResponse({}, status_code=503)
            return _FakeResponse(
                {
                    "features": [
                        {"geometry": {"coordinates": [-73.9851, 40.758]}},
                    ]
                }
            )
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(building_geocode.requests, "get", fake_get)

    result = building_geocode.geocode_building("200 W 45th St", "Manhattan")

    assert result is not None
    assert result.coordinate_source == "planninglabs"
    assert attempts["planninglabs"] == 3
