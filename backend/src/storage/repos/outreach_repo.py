"""Outreach repository mixin — outreach attempts and events."""
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class OutreachRepo:
    """Mixin: outreach attempts + events + follow-ups."""

    # --- Legacy outreach attempts ---

    def add_outreach_attempt(self, lead_id: str, attempt: Dict):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO outreach_attempts (id, lead_id, method, outcome, notes, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                (attempt["id"], lead_id, attempt["method"], attempt["outcome"],
                 attempt.get("notes"), attempt.get("timestamp", datetime.now().isoformat())),
            )
            conn.commit()

    def get_outreach_attempts(self, lead_id: str) -> List[Dict]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT id, method, outcome, notes, timestamp FROM outreach_attempts WHERE lead_id = ? ORDER BY timestamp DESC",
                (lead_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_all_outreach_attempts(self) -> Dict[str, List[Dict]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT lead_id, id, method, outcome, notes, timestamp FROM outreach_attempts ORDER BY timestamp DESC"
            ).fetchall()
            result: Dict[str, List[Dict]] = {}
            for row in rows:
                lid = row["lead_id"]
                result.setdefault(lid, []).append({
                    "id": row["id"], "method": row["method"], "outcome": row["outcome"],
                    "notes": row["notes"], "timestamp": row["timestamp"],
                })
            return result

    # --- Phase 5.3: Outreach events ---

    def add_outreach_event(self, lead_id: str, event: Dict) -> int:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO outreach_events (lead_id, stage, method, outcome, notes, next_follow_up, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (lead_id, event["stage"], event.get("method"), event.get("outcome"),
                 event.get("notes"), event.get("next_follow_up"),
                 event.get("timestamp", datetime.now().isoformat())),
            )
            conn.commit()
            return cursor.lastrowid

    def get_outreach_events(self, lead_id: str) -> List[Dict]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT id, stage, method, outcome, notes, next_follow_up, timestamp FROM outreach_events WHERE lead_id = ? ORDER BY timestamp DESC",
                (lead_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_follow_ups_due(self, before_date: Optional[str] = None) -> List[Dict]:
        if before_date is None:
            before_date = datetime.now().strftime("%Y-%m-%d")
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT l.lead_id, l.agent_name, l.company_name, l.phone, l.email, l.pipeline_stage, l.next_follow_up, l.priority_rank, l.score, l.portfolio_size "
                "FROM leads l WHERE l.next_follow_up IS NOT NULL AND l.next_follow_up <= ? ORDER BY l.priority_rank DESC, l.next_follow_up ASC",
                (before_date,),
            ).fetchall()
            return [dict(row) for row in rows]
