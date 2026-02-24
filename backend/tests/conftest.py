"""
Shared test fixtures for the Double Edge backend test suite.
"""
import os
import sys
import pytest
from pathlib import Path

# Ensure backend root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Use an in-memory or temp database for tests
os.environ.setdefault("DATABASE_PATH", ":memory:")
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-testing-only")
os.environ.setdefault("ADMIN_EMAIL", "admin@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "testpassword123")


@pytest.fixture
def db():
    """Provide a fresh database instance for each test."""
    from src.storage.database import LeadsDatabase
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    database = LeadsDatabase(db_path=db_path)
    yield database
    # Cleanup
    try:
        db_path.unlink(missing_ok=True)
    except Exception:
        pass


@pytest.fixture
def auth_token(db):
    """Provide a valid JWT token for authenticated test requests."""
    from src.auth.auth import create_access_token, hash_password, AuthUser
    import uuid
    
    uid = str(uuid.uuid4())
    db.create_user(uid, "testuser@test.com", hash_password("testpassword123"), role="admin")
    user = AuthUser(user_id=uid, email="testuser@test.com", role="admin")
    token = create_access_token(user)
    return token


@pytest.fixture
def auth_headers(auth_token):
    """Provide Authorization headers for authenticated requests."""
    return {"Authorization": f"Bearer {auth_token}"}
