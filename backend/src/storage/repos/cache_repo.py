"""External API cache repository mixin — DOS / Google Places caches."""
import json
import logging
from datetime import datetime
from typing import Optional, Dict

logger = logging.getLogger(__name__)


class CacheRepo:
    """Mixin: DOS and Google Places caches with TTL."""

    CACHE_TTL_DAYS = 90

    def _is_cache_fresh(self, cached_at_str: Optional[str]) -> bool:
        if not cached_at_str:
            return False
        try:
            cached_at = datetime.fromisoformat(cached_at_str)
            return (datetime.now() - cached_at).days <= self.CACHE_TTL_DAYS
        except (ValueError, TypeError):
            return False

    # --- DOS cache ---

    def get_dos_cache(self, cache_key: str) -> Optional[Dict]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT result, cached_at FROM dos_cache WHERE cache_key = ?", (cache_key,)).fetchone()
            if row:
                if not self._is_cache_fresh(row["cached_at"]):
                    return None
                return json.loads(row["result"]) if row["result"] else None
            return None

    def has_dos_cache(self, cache_key: str) -> bool:
        with self._get_connection() as conn:
            row = conn.execute("SELECT cached_at FROM dos_cache WHERE cache_key = ?", (cache_key,)).fetchone()
            return self._is_cache_fresh(row["cached_at"]) if row else False

    def set_dos_cache(self, cache_key: str, result: Optional[Dict]):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO dos_cache (cache_key, result, cached_at) VALUES (?, ?, ?)",
                (cache_key, json.dumps(result) if result else None, datetime.now().isoformat()),
            )
            conn.commit()

    # --- Google Places cache ---

    def get_places_cache(self, cache_key: str) -> Optional[Dict]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT result, cached_at FROM places_cache WHERE cache_key = ?", (cache_key,)).fetchone()
            if row:
                if not self._is_cache_fresh(row["cached_at"]):
                    return None
                return json.loads(row["result"]) if row["result"] else None
            return None

    def has_places_cache(self, cache_key: str) -> bool:
        with self._get_connection() as conn:
            row = conn.execute("SELECT cached_at FROM places_cache WHERE cache_key = ?", (cache_key,)).fetchone()
            return self._is_cache_fresh(row["cached_at"]) if row else False

    def set_places_cache(self, cache_key: str, result: Optional[Dict]):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO places_cache (cache_key, result, cached_at) VALUES (?, ?, ?)",
                (cache_key, json.dumps(result) if result else None, datetime.now().isoformat()),
            )
            conn.commit()
