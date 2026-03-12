"""Async SQLAlchemy session factory with connection pooling.

Usage in FastAPI:
    from src.db.session import get_session

    @router.get("/buildings")
    async def list_buildings(session: AsyncSession = Depends(get_session)):
        ...

The engine is created lazily on first use. Connection pooling defaults
are tuned for Railway's PG (max 25 connections).
"""
import logging
import os
from functools import lru_cache
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

_RAW_DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not _RAW_DATABASE_URL:
    _env = os.environ.get("ENVIRONMENT", "development")
    if _env == "production":
        raise RuntimeError("DATABASE_URL must be set in production")
    _RAW_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/hpd_leads"
    logger.info("DATABASE_URL not set; using localhost default for development")


def _resolve_async_url(raw: str) -> str:
    """Normalize a DATABASE_URL into an asyncpg-compatible URL."""
    url = raw
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://") and "+" not in url.split("://")[0]:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    url = url.replace("+psycopg", "+asyncpg")
    return url


@lru_cache(maxsize=1)
def _get_engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    return create_async_engine(
        _resolve_async_url(_RAW_DATABASE_URL),
        pool_size=10,
        max_overflow=15,
        pool_pre_ping=True,
        echo=False,
    )


@lru_cache(maxsize=1)
def _get_session_factory():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    return async_sessionmaker(
        _get_engine(), class_=AsyncSession, expire_on_commit=False
    )


def get_session_factory():
    """Public async session factory for non-FastAPI contexts (e.g., workers)."""
    return _get_session_factory()


def get_pool_snapshot() -> dict:
    """Pool snapshot for lightweight health monitoring."""
    engine = _get_engine()
    pool = engine.pool
    size = int(pool.size())
    checked_out = int(pool.checkedout())
    overflow = int(pool.overflow())
    total_capacity = max(1, size + max(0, overflow))
    utilization_ratio = checked_out / total_capacity
    return {
        "size": size,
        "checked_out": checked_out,
        "overflow": overflow,
        "total_capacity": total_capacity,
        "utilization_ratio": round(utilization_ratio, 3),
    }


async def shutdown_engine():
    """Dispose engine on application shutdown."""
    await _get_engine().dispose()


async def get_session() -> AsyncGenerator:
    """FastAPI dependency that yields an async session and auto-commits/rolls back."""
    factory = _get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_sync_url() -> str:
    """Return a psycopg-compatible sync URL for Alembic and migration scripts."""
    url = _RAW_DATABASE_URL
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://") and "+" not in url.split("://")[0]:
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    url = url.replace("+asyncpg", "+psycopg")
    return url


def get_compatible_sync_url() -> str:
    """Return a sync URL with a best-effort driver for the current runtime."""
    url = get_sync_url()
    try:
        import psycopg  # noqa: F401

        return url
    except Exception:
        return url.replace("+psycopg", "+pg8000")
