"""Alert repository mixin — change alerts."""
import json
import logging
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


class AlertRepo:
    """Mixin: change alerts (Phase 5.4)."""

    def add_change_alert(self, alert_type: str, description: str,
                         lead_id: Optional[str] = None, details: Optional[Dict] = None):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO change_alerts (alert_type, lead_id, description, details) VALUES (?, ?, ?, ?)",
                (alert_type, lead_id, description, json.dumps(details) if details else None),
            )
            conn.commit()

    def get_change_alerts(self, limit: int = 50, include_dismissed: bool = False) -> List[Dict]:
        with self._get_connection() as conn:
            where = "" if include_dismissed else "WHERE dismissed = 0"
            rows = conn.execute(
                f"SELECT id, alert_type, lead_id, description, details, created_at, dismissed FROM change_alerts {where} ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            result = []
            for row in rows:
                d = dict(row)
                if d.get("details"):
                    d["details"] = json.loads(d["details"])
                result.append(d)
            return result

    def dismiss_alert(self, alert_id: int):
        with self._get_connection() as conn:
            conn.execute("UPDATE change_alerts SET dismissed = 1 WHERE id = ?", (alert_id,))
            conn.commit()
