"""Repository mixins for LeadsDatabase.

Each mixin provides methods for a single domain:
    UserRepo      — user management + auth
    LeadRepo      — lead CRUD, filtering, scoring
    EnrichmentRepo — enrichment cache, jobs, AI summary
    OutreachRepo  — outreach events + attempts
    CacheRepo     — DOS / Google Places / AI caches
    AlertRepo     — change alerts
    SettingsRepo  — key-value app settings
"""
from .user_repo import UserRepo
from .lead_repo import LeadRepo
from .enrichment_repo import EnrichmentRepo
from .outreach_repo import OutreachRepo
from .cache_repo import CacheRepo
from .alert_repo import AlertRepo
from .settings_repo import SettingsRepo

__all__ = [
    "UserRepo", "LeadRepo", "EnrichmentRepo",
    "OutreachRepo", "CacheRepo", "AlertRepo", "SettingsRepo",
]
