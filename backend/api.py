"""
FastAPI server for HPD Leads Pipeline.

Thin entry point that registers routers and handles startup/shutdown.
All route logic lives in src/routers/.
"""
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import logging
import os

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from src.logging_config import configure_logging
from src.sentry_init import init_sentry

configure_logging()
init_sentry()

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
from src.routers.alerts import router as alerts_router
from src.routers.export_v1 import router as export_v1_router

# Legacy SQLite routers: only load when DATABASE_URL is absent (local dev with SQLite).
# On Railway (PostgreSQL), these routers call get_database() which hits a nonexistent
# SQLite file and can hang or error, breaking enrichment and pipeline endpoints.
_legacy_routers = []
_has_pg = bool(os.environ.get("DATABASE_URL"))
if not _has_pg:
    try:
        from src.routers.enrichment import router as enrichment_router
        _legacy_routers.append(enrichment_router)
    except Exception:
        enrichment_router = None
    try:
        from src.routers.pipeline import router as pipeline_router
        _legacy_routers.append(pipeline_router)
    except Exception:
        pipeline_router = None

# Agent router uses PostgreSQL — always load
try:
    from src.routers.agent import router as agent_router
    _legacy_routers.append(agent_router)
except ImportError:
    agent_router = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="HPD Leads API",
    description=(
        "Enterprise API for NYC property management lead generation and building churn analysis.\n\n"
        "**Personas:**\n"
        "- PE Searcher (Leads tab): Evaluate PM businesses for acquisition\n"
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
        "http://localhost:5173", "http://localhost:3000",
        "http://localhost:3001", "http://localhost:3002",
    ])

_origin_regex = None
if _env == "production" and _cors_origins_env:
    _origin_regex = None
else:
    _origin_regex = r"https://hpd-leads.*\.vercel\.app"

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
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    if _env == "production":
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response


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
app.include_router(alerts_router)
app.include_router(export_v1_router)
for r in _legacy_routers:
    app.include_router(r)


# ---------------------------------------------------------------------------
# Root + startup
# ---------------------------------------------------------------------------
@app.get("/")
async def root():
    return {"status": "ok", "service": "hpd-leads-api"}


@app.on_event("startup")
async def startup():
    _jwt_secret = os.environ.get("JWT_SECRET", "")
    if _env == "production" and (
        not _jwt_secret or _jwt_secret == "change-me-to-a-random-64-char-string"
    ):
        logger.critical("REFUSING TO START: JWT_SECRET is not set or is the default placeholder. Set a secure random value.")
        raise RuntimeError("JWT_SECRET must be set in production")
    logger.info(f"HPD Leads API starting (env={_env})")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
