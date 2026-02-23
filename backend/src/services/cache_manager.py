"""
Centralized cache and background-task state manager.

Encapsulates the in-memory leads cache, enrichment/refresh/PLUTO state dicts,
and their associated threading locks so that routers don't need to touch globals.
"""
import threading
import logging
from datetime import datetime
from typing import List, Optional, Dict

from src.transform.aggregate import Lead

logger = logging.getLogger(__name__)


class _BackgroundState:
    """Thread-safe wrapper around a mutable state dict."""

    def __init__(self, initial: Dict):
        self._state = dict(initial)
        self._lock = threading.Lock()

    def get(self, key: str, default=None):
        return self._state.get(key, default)

    def snapshot(self) -> Dict:
        """Return a shallow copy (safe for reading outside the lock)."""
        return dict(self._state)

    def update(self, **kwargs):
        with self._lock:
            self._state.update(kwargs)

    def reset(self, values: Dict):
        with self._lock:
            self._state.update(values)

    @property
    def lock(self) -> threading.Lock:
        return self._lock


class CacheManager:
    """Singleton that owns the in-memory leads cache and all background-task state."""

    def __init__(self):
        # Leads cache
        self._leads: List[Lead] = []
        self._leads_lock = threading.Lock()
        self._last_refresh: Optional[datetime] = None
        self._startup_complete = False
        self._startup_state = "starting"  # starting | ready | degraded
        self._startup_error: Optional[str] = None
        self._startup_lock = threading.Lock()

        # Enrichment state
        self.enrichment = _BackgroundState({
            "running": False, "phase": "", "progress": 0, "total": 0,
            "completed": 0, "failed": 0, "dos_completed": 0, "dos_found": 0,
            "web_completed": 0, "web_found": 0, "current_lead": None,
            "started_at": None, "finished_at": None, "error": None,
        })
        self.enrichment_stop_requested = False

        # Refresh state
        self.refresh = _BackgroundState({
            "running": False, "phase": None, "buildings_fetched": 0,
            "total_buildings": 0, "leads_created": 0, "pluto_lookups": 0,
            "started_at": None, "finished_at": None, "error": None,
        })

        # PLUTO enrichment state
        self.pluto = _BackgroundState({
            "running": False, "progress": 0, "total": 0, "found": 0,
            "started_at": None, "finished_at": None, "error": None,
        })

    # -- Leads cache helpers -----------------------------------------------

    @property
    def leads(self) -> List[Lead]:
        return self._leads

    @leads.setter
    def leads(self, value: List[Lead]):
        with self._leads_lock:
            self._leads = value

    @property
    def leads_lock(self) -> threading.Lock:
        return self._leads_lock

    @property
    def last_refresh(self) -> Optional[datetime]:
        return self._last_refresh

    @last_refresh.setter
    def last_refresh(self, value: Optional[datetime]):
        self._last_refresh = value

    @property
    def startup_complete(self) -> bool:
        with self._startup_lock:
            return self._startup_complete

    @startup_complete.setter
    def startup_complete(self, value: bool):
        with self._startup_lock:
            self._startup_complete = value

    @property
    def startup_state(self) -> str:
        with self._startup_lock:
            return self._startup_state

    @startup_state.setter
    def startup_state(self, value: str):
        with self._startup_lock:
            self._startup_state = value

    @property
    def startup_error(self) -> Optional[str]:
        with self._startup_lock:
            return self._startup_error

    @startup_error.setter
    def startup_error(self, value: Optional[str]):
        with self._startup_lock:
            self._startup_error = value

    def lead_count(self) -> int:
        with self._leads_lock:
            return len(self._leads)

    def get_lead_index(self) -> Dict[str, int]:
        """Return {lead_id: index} for fast lookup. Call inside leads_lock."""
        return {lead.lead_id: i for i, lead in enumerate(self._leads)}

    def update_lead_in_cache(self, lead: Lead):
        """Replace a single lead in the cache by lead_id."""
        with self._leads_lock:
            for i, existing in enumerate(self._leads):
                if existing.lead_id == lead.lead_id:
                    self._leads[i] = lead
                    return True
        return False


# Singleton
_instance: Optional[CacheManager] = None


def get_cache() -> CacheManager:
    global _instance
    if _instance is None:
        _instance = CacheManager()
    return _instance
