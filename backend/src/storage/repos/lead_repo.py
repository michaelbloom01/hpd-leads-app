"""Lead repository mixin — CRUD, filtering, scoring, persistence."""
import json
import logging
from datetime import datetime, date
from typing import Optional, Dict, List, TYPE_CHECKING

if TYPE_CHECKING:
    from src.transform.aggregate import Lead

logger = logging.getLogger(__name__)


class LeadRepo:
    """Mixin: lead CRUD, filtering, stats, and persistence."""

    def get_lead_by_id(self, lead_id: str) -> Optional[Dict]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM leads WHERE lead_id = ?", (lead_id,)).fetchone()
            return dict(row) if row else None

    def update_lead(self, lead_id: str, updates: Dict) -> bool:
        if not updates:
            return False
        allowed_fields = {
            "business_summary", "phone", "email", "website",
            "enrichment_status", "last_enriched", "owner_principal",
            "dos_id", "dos_status", "notes", "outreach_status",
            "entity_type", "company_name", "primary_contact", "primary_contact_title",
            "enrichment_sources", "score", "score_breakdown",
            "enrichment_retries", "estimated_monthly_revenue", "estimated_annual_revenue",
            "violation_count", "violation_class_a", "violation_class_b", "violation_class_c",
            "violations_per_unit", "pipeline_stage", "next_follow_up", "priority_rank",
        }
        safe = {k: v for k, v in updates.items() if k in allowed_fields}
        if not safe:
            return False
        with self._get_connection() as conn:
            set_clause = ", ".join(f"{f} = ?" for f in safe)
            values = list(safe.values()) + [lead_id]
            cursor = conn.execute(f"UPDATE leads SET {set_clause} WHERE lead_id = ?", values)
            conn.commit()
            return cursor.rowcount > 0

    def batch_update_revenue(self, updates: List[tuple]) -> int:
        if not updates:
            return 0
        with self._get_connection() as conn:
            conn.executemany(
                "UPDATE leads SET estimated_monthly_revenue = ?, estimated_annual_revenue = ? WHERE lead_id = ?",
                [(m, a, lid) for lid, m, a in updates],
            )
            conn.commit()
            return len(updates)

    def increment_enrichment_retries(self, lead_id: str) -> int:
        with self._get_connection() as conn:
            conn.execute("UPDATE leads SET enrichment_retries = COALESCE(enrichment_retries, 0) + 1 WHERE lead_id = ?", (lead_id,))
            conn.commit()
            row = conn.execute("SELECT enrichment_retries FROM leads WHERE lead_id = ?", (lead_id,)).fetchone()
            return row[0] if row else 0

    def get_leads_count(self) -> int:
        with self._get_connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]

    def get_last_refresh_time(self) -> Optional[datetime]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT MAX(updated_at) as last_update FROM leads").fetchone()
            if row and row["last_update"]:
                try:
                    return datetime.fromisoformat(row["last_update"])
                except (ValueError, TypeError):
                    pass
            return None

    # --- Lead user data ---

    def get_lead_user_data(self, lead_id: str) -> Optional[Dict]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM lead_user_data WHERE lead_id = ?", (lead_id,)).fetchone()
            return dict(row) if row else None

    def get_all_user_data(self) -> Dict[str, Dict]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM lead_user_data").fetchall()
            return {row["lead_id"]: dict(row) for row in rows}

    def save_lead_user_data(self, lead_id: str, outreach_status: Optional[str] = None, notes: Optional[str] = None):
        with self._get_connection() as conn:
            existing = conn.execute("SELECT lead_id FROM lead_user_data WHERE lead_id = ?", (lead_id,)).fetchone()
            if existing:
                updates, params = [], []
                if outreach_status is not None:
                    updates.append("outreach_status = ?"); params.append(outreach_status)
                if notes is not None:
                    updates.append("notes = ?"); params.append(notes)
                updates.append("updated_at = ?"); params.append(datetime.now())
                params.append(lead_id)
                conn.execute(f"UPDATE lead_user_data SET {', '.join(updates)} WHERE lead_id = ?", params)
            else:
                conn.execute("INSERT INTO lead_user_data (lead_id, outreach_status, notes) VALUES (?, ?, ?)",
                             (lead_id, outreach_status or "new", notes))
            conn.commit()

    # --- Stats ---

    def get_stats(self) -> Dict:
        with self._get_connection() as conn:
            user_count = conn.execute("SELECT COUNT(*) FROM lead_user_data").fetchone()[0]
            enriched_count = conn.execute("SELECT COUNT(*) FROM enrichment_cache").fetchone()[0]
            status_counts = {}
            for row in conn.execute("SELECT outreach_status, COUNT(*) as count FROM lead_user_data GROUP BY outreach_status"):
                status_counts[row["outreach_status"]] = row["count"]
            return {"total_user_records": user_count, "total_enriched": enriched_count, "by_status": status_counts}

    def get_stats_sql(self) -> Dict:
        with self._get_connection() as conn:
            core = conn.execute("""
                SELECT COUNT(*) as total_leads, SUM(portfolio_size) as total_buildings,
                       SUM(total_units) as total_units,
                       COUNT(CASE WHEN phone IS NOT NULL AND phone != '' THEN 1 END) as with_phone,
                       COUNT(CASE WHEN email IS NOT NULL AND email != '' THEN 1 END) as with_email,
                       COUNT(CASE WHEN website IS NOT NULL AND website != '' THEN 1 END) as with_website,
                       MAX(score) as top_score, AVG(score) as avg_score FROM leads
            """).fetchone()
            by_borough = {r["boro"] or "Unknown": r["cnt"] for r in conn.execute("SELECT boro, COUNT(*) as cnt FROM leads GROUP BY boro")}
            by_enrichment = {r["enrichment_status"] or "none": r["cnt"] for r in conn.execute("SELECT enrichment_status, COUNT(*) as cnt FROM leads GROUP BY enrichment_status")}
            by_outreach = {r["outreach_status"] or "new": r["cnt"] for r in conn.execute("SELECT outreach_status, COUNT(*) as cnt FROM leads GROUP BY outreach_status")}
            by_entity = {r["entity_type"] or "unknown": r["cnt"] for r in conn.execute("SELECT entity_type, COUNT(*) as cnt FROM leads GROUP BY entity_type")}
            score_dist = {r["bucket"]: r["cnt"] for r in conn.execute("""
                SELECT CASE WHEN score < 20 THEN '0-20' WHEN score < 40 THEN '20-40'
                            WHEN score < 60 THEN '40-60' WHEN score < 80 THEN '60-80'
                            ELSE '80-100' END as bucket, COUNT(*) as cnt FROM leads GROUP BY bucket""")}
            portfolio_dist = {r["bucket"]: r["cnt"] for r in conn.execute("""
                SELECT CASE WHEN portfolio_size <= 5 THEN '1-5' WHEN portfolio_size <= 10 THEN '6-10'
                            WHEN portfolio_size <= 25 THEN '11-25' WHEN portfolio_size <= 50 THEN '26-50'
                            WHEN portfolio_size <= 100 THEN '51-100' ELSE '100+' END as bucket,
                       COUNT(*) as cnt FROM leads GROUP BY bucket""")}
            return {
                "total_leads": core["total_leads"] or 0, "total_buildings": core["total_buildings"] or 0,
                "total_units": core["total_units"] or 0, "with_phone": core["with_phone"] or 0,
                "with_email": core["with_email"] or 0, "with_website": core["with_website"] or 0,
                "top_score": core["top_score"] or 0.0, "avg_score": round(core["avg_score"] or 0.0, 1),
                "by_borough": by_borough, "by_enrichment_status": by_enrichment,
                "by_outreach_status": by_outreach, "by_entity_type": by_entity,
                "score_distribution": score_dist, "portfolio_distribution": portfolio_dist,
            }
