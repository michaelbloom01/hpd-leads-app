"""Settings repository mixin — key/value store."""
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class SettingsRepo:
    """Mixin: persistent key-value settings."""

    def get_setting(self, key: str) -> Optional[str]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None

    def set_setting(self, key: str, value: str):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, datetime.now().isoformat()),
            )
            conn.commit()
