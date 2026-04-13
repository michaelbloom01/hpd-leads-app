"""Authentication routes — login, token refresh, password change.

Migrated to PostgreSQL (AsyncSession).
"""
import os
import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth import hash_password, verify_password, create_access_token, AuthUser, get_current_user
from src.db.session import get_session
from src.schemas.requests import LoginRequest, ChangePasswordRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

limiter = Limiter(key_func=get_remote_address)


async def _ensure_env_user(
    session: AsyncSession,
    *,
    email: str | None,
    password: str | None,
    role: str,
    label: str,
):
    """Create a bootstrap user from env vars if it doesn't exist yet."""
    if not email or not password:
        return
    existing = (
        await session.execute(
            text("SELECT user_id FROM users WHERE email = :email"),
            {"email": email},
        )
    ).first()
    if not existing:
        uid = str(_uuid.uuid4())
        await session.execute(
            text("""
                INSERT INTO users (user_id, email, password_hash, role, created_at, updated_at)
                VALUES (:uid, :email, :pw, :role, NOW(), NOW())
            """),
            {"uid": uid, "email": email, "pw": hash_password(password), "role": role},
        )
        await session.commit()
        logger.info("Auth: Created %s user %s", label, email)


async def _ensure_bootstrap_users(session: AsyncSession):
    """Ensure env-provisioned users exist before login attempts."""
    await _ensure_env_user(
        session,
        email=os.environ.get("ADMIN_EMAIL"),
        password=os.environ.get("ADMIN_PASSWORD"),
        role="admin",
        label="admin",
    )
    await _ensure_env_user(
        session,
        email=os.environ.get("TEST_USER_EMAIL"),
        password=os.environ.get("TEST_USER_PASSWORD"),
        role="user",
        label="test",
    )


@router.post("/login")
@limiter.limit("10/minute")
async def auth_login(
    request: Request,
    req: LoginRequest,
    session: AsyncSession = Depends(get_session),
):
    await _ensure_bootstrap_users(session)

    result = await session.execute(
        text("SELECT user_id, email, password_hash, role FROM users WHERE email = :email"),
        {"email": req.email},
    )
    user = result.first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    auth_user = AuthUser(user_id=user.user_id, email=user.email, role=user.role)
    token = create_access_token(auth_user)
    await session.execute(
        text("UPDATE users SET last_login = NOW() WHERE user_id = :uid"),
        {"uid": user.user_id},
    )
    await session.commit()

    return {
        "token": token,
        "user": {"user_id": user.user_id, "email": user.email, "role": user.role},
    }


@router.get("/me")
async def auth_me(current_user: AuthUser = Depends(get_current_user)):
    return {"user_id": current_user.user_id, "email": current_user.email, "role": current_user.role}


@router.post("/change-password")
async def auth_change_password(
    req: ChangePasswordRequest,
    current_user: AuthUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        text("SELECT password_hash FROM users WHERE user_id = :uid"),
        {"uid": current_user.user_id},
    )
    user = result.first()
    if not user or not verify_password(req.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    await session.execute(
        text("UPDATE users SET password_hash = :pw, updated_at = NOW() WHERE user_id = :uid"),
        {"uid": current_user.user_id, "pw": hash_password(req.new_password)},
    )
    await session.commit()
    return {"status": "ok", "message": "Password changed successfully"}
