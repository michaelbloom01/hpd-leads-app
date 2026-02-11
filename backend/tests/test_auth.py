"""
Tests for the authentication module.
Covers: password hashing, JWT creation/verification, user CRUD.
"""
import uuid
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


class TestUserCRUD:
    def test_create_user(self, db):
        uid = str(uuid.uuid4())
        assert db.create_user(uid, "new@test.com", hash_password("pass1234")) is True
        user = db.get_user_by_email("new@test.com")
        assert user is not None
        assert user["email"] == "new@test.com"
        assert user["role"] == "user"

    def test_duplicate_email_fails(self, db):
        uid1 = str(uuid.uuid4())
        uid2 = str(uuid.uuid4())
        db.create_user(uid1, "dup@test.com", hash_password("pass1234"))
        assert db.create_user(uid2, "dup@test.com", hash_password("pass5678")) is False

    def test_get_user_by_id(self, db):
        uid = str(uuid.uuid4())
        db.create_user(uid, "byid@test.com", hash_password("pass1234"))
        user = db.get_user_by_id(uid)
        assert user is not None
        assert user["email"] == "byid@test.com"

    def test_update_password(self, db):
        uid = str(uuid.uuid4())
        db.create_user(uid, "pwd@test.com", hash_password("oldpass123"))
        new_hash = hash_password("newpass456")
        assert db.update_user_password(uid, new_hash) is True
        user = db.get_user_by_id(uid)
        assert verify_password("newpass456", user["password_hash"])

    def test_list_users(self, db):
        uid1 = str(uuid.uuid4())
        uid2 = str(uuid.uuid4())
        db.create_user(uid1, "a@test.com", hash_password("pass1234"))
        db.create_user(uid2, "b@test.com", hash_password("pass5678"))
        users = db.list_users()
        assert len(users) >= 2
        emails = [u["email"] for u in users]
        assert "a@test.com" in emails
        assert "b@test.com" in emails
        # Should not include password_hash
        for u in users:
            assert "password_hash" not in u
