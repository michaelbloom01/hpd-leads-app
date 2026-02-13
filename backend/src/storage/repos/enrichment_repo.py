"""Enrichment repository mixin — enrichment cache, jobs, and bulk operations."""
import json
import logging
from datetime import datetime
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


class EnrichmentRepo:
    """Mixin: enrichment cache, enrichment jobs, and AI summary cache."""

    # --- Enrichment cache ---

    def get_enrichment(self, lead_id: str) -> Optional[Dict]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM enrichment_cache WHERE lead_id = ?", (lead_id,)).fetchone()
            if row:
                data = dict(row)
                if data.get("enrichment_sources"):
                    data["enrichment_sources"] = json.loads(data["enrichment_sources"])
                return data
            return None

    def get_all_enrichments(self) -> Dict[str, Dict]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM enrichment_cache").fetchall()
            result = {}
            for row in rows:
                data = dict(row)
                if data.get("enrichment_sources"):
                    data["enrichment_sources"] = json.loads(data["enrichment_sources"])
                result[data["lead_id"]] = data
            return result

    def save_enrichment(self, lead_id: str, phone=None, email=None, website=None,
                        business_summary=None, owner_principal=None,
                        enrichment_status="none", enrichment_sources=None):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO enrichment_cache (lead_id, phone, email, website, business_summary, owner_principal, enrichment_status, enrichment_sources, last_enriched) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (lead_id, phone, email, website, business_summary, owner_principal,
                 enrichment_status, json.dumps(enrichment_sources or []), datetime.now()),
            )
            conn.commit()

    def get_all_enrichment_from_leads(self) -> Dict[str, Dict]:
        enrichment_fields = [
            "lead_id", "phone", "email", "website", "business_summary",
            "owner_principal", "enrichment_status", "enrichment_sources",
            "last_enriched", "pipeline_stage", "next_follow_up", "priority_rank",
            "notes", "outreach_status",
        ]
        cols = ", ".join(enrichment_fields)
        with self._get_connection() as conn:
            rows = conn.execute(
                f"SELECT {cols} FROM leads WHERE enrichment_status IS NOT NULL AND enrichment_status != 'none'"
            ).fetchall()
            result: Dict[str, Dict] = {}
            for row in rows:
                data = dict(row)
                if data.get("enrichment_sources") and isinstance(data["enrichment_sources"], str):
                    try:
                        data["enrichment_sources"] = json.loads(data["enrichment_sources"])
                    except (json.JSONDecodeError, TypeError):
                        data["enrichment_sources"] = []
                result[data["lead_id"]] = data
            return result

    def get_enrichment_stats(self) -> Dict:
        with self._get_connection() as conn:
            status_counts = {}
            for row in conn.execute("SELECT enrichment_status, COUNT(*) as count FROM leads GROUP BY enrichment_status"):
                status_counts[row["enrichment_status"] or "none"] = row["count"]
            with_phone = conn.execute("SELECT COUNT(*) FROM leads WHERE phone IS NOT NULL AND phone != ''").fetchone()[0]
            with_email = conn.execute("SELECT COUNT(*) FROM leads WHERE email IS NOT NULL AND email != ''").fetchone()[0]
            with_website = conn.execute("SELECT COUNT(*) FROM leads WHERE website IS NOT NULL AND website != ''").fetchone()[0]
            return {"by_status": status_counts, "with_phone": with_phone, "with_email": with_email, "with_website": with_website}

    # --- Enrichment jobs ---

    def create_enrichment_job(self, job_type: str, lead_ids: List[str], config: Optional[Dict] = None) -> int:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO enrichment_jobs (job_type, status, total_leads, lead_ids_remaining, config) VALUES (?, 'running', ?, ?, ?)",
                (job_type, len(lead_ids), json.dumps(lead_ids), json.dumps(config or {})),
            )
            conn.commit()
            return cursor.lastrowid

    def update_enrichment_job(self, job_id: int, processed=None, completed=None, failed=None,
                               dos_found=None, web_found=None, current_lead=None,
                               lead_ids_remaining=None, status=None, error=None):
        updates, params = [], []
        for field, value in [("processed", processed), ("completed", completed), ("failed", failed),
                             ("dos_found", dos_found), ("web_found", web_found), ("current_lead", current_lead)]:
            if value is not None:
                updates.append(f"{field} = ?")
                params.append(value)
        if lead_ids_remaining is not None:
            updates.append("lead_ids_remaining = ?")
            params.append(json.dumps(lead_ids_remaining))
        if status is not None:
            updates.append("status = ?")
            params.append(status)
            if status in ("completed", "failed"):
                updates.append("finished_at = ?")
                params.append(datetime.now().isoformat())
        if error is not None:
            updates.append("error = ?")
            params.append(error)
        updates.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.append(job_id)
        with self._get_connection() as conn:
            conn.execute(f"UPDATE enrichment_jobs SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()

    def get_active_enrichment_job(self) -> Optional[Dict]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM enrichment_jobs WHERE status IN ('running','paused') ORDER BY id DESC LIMIT 1").fetchone()
            if row:
                data = dict(row)
                data["lead_ids_remaining"] = json.loads(data.get("lead_ids_remaining") or "[]")
                data["config"] = json.loads(data.get("config") or "{}")
                return data
            return None

    def get_enrichment_job(self, job_id: int) -> Optional[Dict]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM enrichment_jobs WHERE id = ?", (job_id,)).fetchone()
            if row:
                data = dict(row)
                data["lead_ids_remaining"] = json.loads(data.get("lead_ids_remaining") or "[]")
                data["config"] = json.loads(data.get("config") or "{}")
                return data
            return None

    # --- AI summary cache ---

    def get_ai_summary_cache(self, lead_id: str) -> Optional[str]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT summary, cached_at FROM ai_summary_cache WHERE lead_id = ?", (lead_id,)).fetchone()
            if row and self._is_cache_fresh(row["cached_at"]):
                return row["summary"]
            return None

    def set_ai_summary_cache(self, lead_id: str, summary: str, model: str = ""):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ai_summary_cache (lead_id, summary, model, cached_at) VALUES (?, ?, ?, ?)",
                (lead_id, summary, model, datetime.now().isoformat()),
            )
            conn.commit()
