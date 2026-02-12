"""
JWT authentication for HPD Leads API.

Provides:
- Password hashing (bcrypt via passlib)
- JWT token creation / verification
- FastAPI dependency `get_current_user` to protect routes
"""
import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import bcrypt
from jose import JWTError, jwt
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_jwt_secret_raw = os.environ.get("JWT_SECRET")
if not _jwt_secret_raw:
    raise RuntimeError(
        "JWT_SECRET environment variable is not set. "
        "Set a strong random string (64+ chars) before starting the server."
    )
JWT_SECRET = _jwt_secret_raw
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.environ.get("JWT_EXPIRE_MINUTES", "1440"))  # 24 hours default

# ---------------------------------------------------------------------------
# Password hashing (bcrypt directly — avoids passlib compatibility issues)
# ---------------------------------------------------------------------------


def hash_password(plain: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

class AuthUser(BaseModel):
    """Payload stored inside the JWT and returned by get_current_user."""
    user_id: str
    email: str
    role: str = "user"


def create_access_token(user: AuthUser, expires_delta: Optional[timedelta] = None) -> str:
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=JWT_EXPIRE_MINUTES))
    payload = {
        "sub": user.user_id,
        "email": user.email,
        "role": user.role,
        "exp": expire,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> AuthUser:
    """Decode and validate a JWT token. Raises HTTPException on failure."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("sub")
        email = payload.get("email")
        role = payload.get("role", "user")
        if not user_id or not email:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
        return AuthUser(user_id=user_id, email=email, role=role)
    except JWTError as e:
        logger.warning(f"JWT decode failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired or invalid",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> AuthUser:
    """
    FastAPI dependency that extracts and validates the JWT from the Authorization header.
    Use as: current_user: AuthUser = Depends(get_current_user)
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_token(credentials.credentials)


async def get_current_admin(
    user: AuthUser = Depends(get_current_user),
) -> AuthUser:
    """Requires the user to have admin role."""
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user
