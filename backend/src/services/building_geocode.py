"""Geocoding helpers for persisted building coordinates."""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import requests

logger = logging.getLogger(__name__)

PLANNING_LABS_URL = "https://geosearch.planninglabs.nyc/v2/search"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
REQUEST_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "double-edge-hpd-leads/1.0",
}
REQUEST_TIMEOUT_SECONDS = 20
REQUEST_MAX_ATTEMPTS = 3
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class BuildingGeocode:
    latitude: float
    longitude: float
    coordinate_source: str
    coordinate_precision: str


def _query_string(address: str, borough: Optional[str]) -> str:
    parts = [address.strip()]
    if borough and borough.strip():
        parts.append(borough.strip())
    parts.extend(["NY", "USA"])
    return ", ".join(part for part in parts if part)


def _parse_float(value) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _parse_latitude(value) -> Optional[float]:
    parsed = _parse_float(value)
    if parsed is None or not (-90 <= parsed <= 90):
        return None
    return parsed


def _parse_longitude(value) -> Optional[float]:
    parsed = _parse_float(value)
    if parsed is None or not (-180 <= parsed <= 180):
        return None
    return parsed


def _request_json(url: str, *, params: dict) -> Optional[object]:
    last_error: Exception | None = None
    for attempt in range(1, REQUEST_MAX_ATTEMPTS + 1):
        try:
            response = requests.get(
                url,
                params=params,
                headers=REQUEST_HEADERS,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code in RETRYABLE_STATUS_CODES:
                raise requests.HTTPError(f"HTTP {response.status_code}")
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
            if attempt == REQUEST_MAX_ATTEMPTS:
                break
            time.sleep(0.25 * attempt)
    if last_error:
        raise last_error
    return None


@lru_cache(maxsize=20000)
def geocode_building(address: str, borough: Optional[str] = None) -> Optional[BuildingGeocode]:
    query = _query_string(address, borough)
    if not address or not address.strip():
        return None

    try:
        payload = _request_json(
            PLANNING_LABS_URL,
            params={"text": query},
        )
        features = payload.get("features") if isinstance(payload, dict) else None
        coordinates = (
            features[0].get("geometry", {}).get("coordinates")
            if isinstance(features, list) and features
            else None
        )
        if isinstance(coordinates, list) and len(coordinates) >= 2:
            lon = _parse_longitude(coordinates[0])
            lat = _parse_latitude(coordinates[1])
            if lat is not None and lon is not None:
                return BuildingGeocode(
                    latitude=lat,
                    longitude=lon,
                    coordinate_source="planninglabs",
                    coordinate_precision="parcel",
                )
    except Exception as exc:
        logger.debug("Planning Labs geocode failed for %s: %s", query, exc)

    try:
        payload = _request_json(
            NOMINATIM_URL,
            params={
                "q": query,
                "format": "jsonv2",
                "limit": 1,
                "countrycodes": "us",
            },
        )
        first = payload[0] if isinstance(payload, list) and payload else None
        lat = _parse_latitude(first.get("lat") if isinstance(first, dict) else None)
        lon = _parse_longitude(first.get("lon") if isinstance(first, dict) else None)
        if lat is not None and lon is not None:
            return BuildingGeocode(
                latitude=lat,
                longitude=lon,
                coordinate_source="nominatim",
                coordinate_precision="address",
            )
    except Exception as exc:
        logger.debug("Nominatim geocode failed for %s: %s", query, exc)

    return None
