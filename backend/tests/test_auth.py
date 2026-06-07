"""
Tests for the authentication module.
Covers: password hashing, JWT creation/verification, user CRUD.
"""
import pytest
from src.auth.auth import (
    hash_password,
    verify_password,
    create_access_token,
    decode_token,
    AuthUser,
)


class TestPasswordHashing:
    def test_hash_and_verify(self):
        plain = "my-secure-password-123"
        hashed = hash_password(plain)
        assert hashed != plain
        assert verify_password(plain, hashed)

    def test_wrong_password_fails(self):
        hashed = hash_password("correct-password")
        assert not verify_password("wrong-password", hashed)

    def test_different_hashes_for_same_password(self):
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2  # bcrypt uses random salt


class TestJWT:
    def test_create_and_decode_token(self):
        user = AuthUser(user_id="u-123", email="test@example.com", role="admin")
        token = create_access_token(user)
        decoded = decode_token(token)
        assert decoded.user_id == "u-123"
        assert decoded.email == "test@example.com"
        assert decoded.role == "admin"

    def test_invalid_token_raises(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            decode_token("not-a-valid-token")

    def test_expired_token_raises(self):
        from fastapi import HTTPException
        from datetime import timedelta
        user = AuthUser(user_id="u-123", email="test@example.com", role="user")
        token = create_access_token(user, expires_delta=timedelta(seconds=-10))
        with pytest.raises(HTTPException):
            decode_token(token)

