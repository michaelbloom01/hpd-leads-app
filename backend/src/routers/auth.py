"""Authentication routes — login, token refresh, password change."""
import os
import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException

from src.auth import hash_password, verify_password, create_access_token, AuthUser, get_current_user
from src.storage.database import get_database
from src.schemas.requests import LoginRequest, ChangePasswordRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


def _ensure_admin_user():
    """Create the admin user from env vars if it doesn't exist yet."""
    admin_email = os.environ.get("ADMIN_EMAIL")
    admin_password = os.environ.get("ADMIN_PASSWORD")
    if not admin_email or not admin_password:
        return
    db = get_database()
    existing = db.get_user_by_email(admin_email)
    if not existing:
        uid = str(_uuid.uuid4())
        db.create_user(uid, admin_email, hash_password(admin_password), role="admin")
        logger.info(f"Auth: Created admin user {admin_email}")


@router.post("/login")
async def auth_login(req: LoginRequest):
    """Authenticate and return a JWT token."""
    _ensure_admin_user()

    db = get_database()
    user = db.get_user_by_email(req.email)
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    auth_user = AuthUser(user_id=user["user_id"], email=user["email"], role=user["role"])
    token = create_access_token(auth_user)
    db.update_user_last_login(user["user_id"])

    return {
        "token": token,
        "user": {"user_id": user["user_id"], "email": user["email"], "role": user["role"]},
    }


@router.get("/me")
async def auth_me(current_user: AuthUser = Depends(get_current_user)):
    """Return the current authenticated user's info."""
    return {"user_id": current_user.user_id, "email": current_user.email, "role": current_user.role}


@router.post("/change-password")
async def auth_change_password(req: ChangePasswordRequest, current_user: AuthUser = Depends(get_current_user)):
    """Change the current user's password."""
    db = get_database()
    user = db.get_user_by_id(current_user.user_id)
    if not user or not verify_password(req.current_password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    db.update_user_password(current_user.user_id, hash_password(req.new_password))
    return {"status": "ok", "message": "Password changed successfully"}
