from __future__ import annotations

import pytest

from src.tasks import ingest as ingest_tasks


class _RetryableResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code

    def raise_for_status(self):
        raise AssertionError("raise_for_status should not be called for retryable 5xx responses")

    def json(self):
        return []


def test_socrata_fetch_raises_after_exhausting_retryable_5xx(monkeypatch):
    calls = {"count": 0}

    def fake_get(*_args, **_kwargs):
        calls["count"] += 1
        return _RetryableResponse(503)

    monkeypatch.setattr(ingest_tasks.requests, "get", fake_get)

    with pytest.raises(RuntimeError, match="returned HTTP 503 after 2 attempts"):
        ingest_tasks._socrata_fetch("fake-dataset", {"$limit": 1}, max_retries=2)

    assert calls["count"] == 2
