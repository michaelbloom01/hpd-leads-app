import asyncio

import pytest
from fastapi import HTTPException

from src.auth.auth import AuthUser, get_current_admin
from src.routers.auth import _ensure_bootstrap_users


class _FakeResult:
    def __init__(self, row=None):
        self._row = row

    def first(self):
        return self._row


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self.executed = []
        self.commit_count = 0

    async def execute(self, statement, params=None):
        self.executed.append((str(statement), params or {}))
        if self._results:
            return self._results.pop(0)
        return _FakeResult()

    async def commit(self):
        self.commit_count += 1


def test_ensure_bootstrap_users_creates_shared_test_user(monkeypatch):
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("TEST_USER_EMAIL", "demo@example.com")
    monkeypatch.setenv("TEST_USER_PASSWORD", "demo-password-123")

    session = _FakeSession([_FakeResult(None), _FakeResult(None)])

    asyncio.run(_ensure_bootstrap_users(session))

    insert_calls = [params for statement, params in session.executed if "INSERT INTO users" in statement]
    assert len(insert_calls) == 1
    assert insert_calls[0]["email"] == "demo@example.com"
    assert insert_calls[0]["role"] == "user"
    assert session.commit_count == 1


def test_get_current_admin_rejects_non_admin_user():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_current_admin(AuthUser(user_id="u-1", email="demo@example.com", role="user")))

    assert exc.value.status_code == 403
