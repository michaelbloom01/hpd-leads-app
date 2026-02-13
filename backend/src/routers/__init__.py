"""API Routers for HPD Leads.

Agent router imported separately (requires anthropic SDK).
"""
from .auth import router as auth_router
from .leads import router as leads_router
from .enrichment import router as enrichment_router
from .pipeline import router as pipeline_router
from .export import router as export_router
from .admin import router as admin_router

# Agent router has heavy optional deps — import directly where needed
# from .agent import router as agent_router

__all__ = [
    "auth_router",
    "leads_router",
    "enrichment_router",
    "pipeline_router",
    "export_router",
    "admin_router",
]
