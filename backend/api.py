"""
FastAPI server for Double Edge Pipeline.

Thin entry point that registers routers and handles startup/shutdown.
All route logic lives in src/routers/.
"""
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import logging
import os
import time
import uuid

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from src.logging_config import configure_logging, set_request_id
from src.sentry_init import init_sentry
from src.db.session import get_pool_snapshot, get_session_factory, shutdown_engine
from src.routers.smart_lists import ensure_smart_lists_table
from src.routers.building_lists import ensure_building_lists_tables
from src.routers.admin import sync_lead_portfolio_snapshot

configure_logging()
init_sentry()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
STARTUP_SYNC_ENV = "DOUBLE_EDGE_RUN_STARTUP_SYNC"

# SQLAlchemy async session
from src.db.session import get_session  # noqa: F401

# Routers (PostgreSQL-backed)
from src.routers.auth import router as auth_router
from src.routers.leads import router as leads_router
from src.routers.admin import router as admin_router
from src.routers.scoring import router as scoring_router
from src.routers.buildings import router as buildings_router
from src.routers.jobs import router as jobs_router
from src.routers.quality import router as quality_router
from src.routers.canonical import router as canonical_router
from src.routers.alerts import router as alerts_router
from src.routers.outreach import router as outreach_router
from src.routers.targets import router as targets_router
from src.routers.export_v1 import router as export_v1_router
from src.routers.smart_lists import router as smart_lists_router
from src.routers.building_lists import router as building_lists_router
from src.routers.truth import router as truth_router
from src.routers.compliance import router as compliance_router

# Optional routers loaded conditionally.
_optional_routers = []

# Agent router uses PostgreSQL — always load
try:
    from src.routers.agent import router as agent_router
    _optional_routers.append(agent_router)
except ImportError:
    agent_router = None

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Double Edge API",
    description=(
        "Enterprise API for NYC property management lead generation and building churn analysis.\n\n"
        "**Dual-purpose platform:**\n"
        "- PE/Acquirer (Leads tab): Source and evaluate PM businesses for acquisition\n"
        "- PM Operator (Buildings tab): Find buildings ripe for high-value outreach"
    ),
    version="3.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
_env = os.environ.get("ENVIRONMENT", "development")
_cors_origins_env = os.environ.get("CORS_ORIGINS", "")
_allowed_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()] if _cors_origins_env else []

if _env != "production":
    _allowed_origins.extend([
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:3001", "http://127.0.0.1:3001",
        "http://localhost:3002", "http://127.0.0.1:3002",
    ])

_origin_regex = r"https://[-\w]+\.vercel\.app"

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_origin_regex=_origin_regex,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = request_id
    request.state.request_started_at = time.perf_counter()
    set_request_id(request_id)
    try:
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Request-ID"] = request_id
        if _env == "production":
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        elapsed_ms = int((time.perf_counter() - request.state.request_started_at) * 1000)
        logger.info(
            "http_request method=%s path=%s status=%s elapsed_ms=%s",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response
    finally:
        set_request_id(None)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    request_id = getattr(request.state, "request_id", None)
    headers = {"X-Request-ID": request_id} if request_id else {}
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": "http_error",
                "message": str(exc.detail),
                "request_id": request_id,
            }
        },
        headers=headers,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", None)
    headers = {"X-Request-ID": request_id} if request_id else {}
    logger.exception("unhandled_exception request_id=%s", request_id)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_server_error",
                "message": "Unexpected server error",
                "request_id": request_id,
            }
        },
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Register routers
# ---------------------------------------------------------------------------
app.include_router(auth_router)
app.include_router(leads_router)
app.include_router(admin_router)
app.include_router(scoring_router)
app.include_router(buildings_router)
app.include_router(jobs_router)
app.include_router(quality_router)
app.include_router(canonical_router)
app.include_router(alerts_router)
app.include_router(outreach_router)
app.include_router(targets_router)
app.include_router(export_v1_router)
app.include_router(smart_lists_router)
app.include_router(building_lists_router)
app.include_router(truth_router)
app.include_router(compliance_router)
for r in _optional_routers:
    app.include_router(r)


# ---------------------------------------------------------------------------
# Root + startup
# ---------------------------------------------------------------------------
@app.get("/")
async def root():
    return {"status": "ok", "service": "double-edge-api"}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "double-edge-api"}


@app.get("/api/health")
async def api_health():
    return {"status": "ok", "service": "double-edge-api"}


@app.get("/api/health/db-pool")
async def db_pool_health():
    snapshot = get_pool_snapshot()
    status = "ok" if snapshot.get("utilization_ratio", 0) < 0.9 else "degraded"
    return {"status": status, "pool": snapshot}


def _startup_sync_enabled() -> bool:
    return os.environ.get(STARTUP_SYNC_ENV, "").strip().lower() in {"1", "true", "yes"}


async def _run_legacy_startup_sync() -> dict[str, int]:
    factory = get_session_factory()
    async with factory() as session:
        await ensure_smart_lists_table(session)
        await ensure_building_lists_tables(session)
        snapshot = await sync_lead_portfolio_snapshot(session)
        await session.commit()
    return snapshot


@app.on_event("startup")
async def startup():
    _jwt_secret = os.environ.get("JWT_SECRET", "")
    if _env == "production" and (
        not _jwt_secret or _jwt_secret == "change-me-to-a-random-64-char-string"
    ):
        logger.critical("REFUSING TO START: JWT_SECRET is not set or is the default placeholder. Set a secure random value.")
        raise RuntimeError("JWT_SECRET must be set in production")
    logger.info(f"Double Edge API starting (env={_env})")

    if not _startup_sync_enabled():
        logger.info(
            "startup_sync skipped; set %s=1 to run legacy smart/building-list repair and lead portfolio snapshot sync explicitly",
            STARTUP_SYNC_ENV,
        )
        return

    try:
        snapshot = await _run_legacy_startup_sync()
        logger.info(
            "startup_sync complete: smart/building lists ensured; lead portfolio snapshot rows_updated=%s threshold_count=%s",
            snapshot.get("rows_updated"),
            snapshot.get("leads_50_units_5_buildings"),
        )
    except Exception as exc:
        logger.warning("Failed startup data/table sync: %s", exc)


@app.on_event("shutdown")
async def shutdown():
    await shutdown_engine()
    logger.info("Database engine disposed on shutdown")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
