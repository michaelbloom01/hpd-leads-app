"""
SQLite database for persisting leads data.

Stores:
- Lead metadata (notes, outreach_status)
- Enrichment results (phone, email, website, etc.)
- User-added data that shouldn't be lost on refresh
"""
import json
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Default database path
DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "data" / "leads.db"


class LeadsDatabase:
    """SQLite database for lead persistence."""
    
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    @contextmanager
    def _get_connection(self):
        """Get a database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def _init_db(self):
        """Initialize database tables."""
        with self._get_connection() as conn:
            conn.executescript("""
                -- Lead user data (notes, status, etc.)
                CREATE TABLE IF NOT EXISTS lead_user_data (
                    lead_id TEXT PRIMARY KEY,
                    outreach_status TEXT DEFAULT 'new',
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                -- Enrichment cache (expensive to fetch, save it)
                CREATE TABLE IF NOT EXISTS enrichment_cache (
                    lead_id TEXT PRIMARY KEY,
                    phone TEXT,
                    email TEXT,
                    website TEXT,
                    business_summary TEXT,
                    owner_principal TEXT,
                    enrichment_status TEXT,
                    enrichment_sources TEXT,  -- JSON array
                    last_enriched TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                -- Index for faster lookups
                CREATE INDEX IF NOT EXISTS idx_lead_status ON lead_user_data(outreach_status);
            """)
            conn.commit()
            logger.info(f"Database initialized at {self.db_path}")
    
    # === Lead User Data ===
    
    def get_lead_user_data(self, lead_id: str) -> Optional[Dict]:
        """Get user data (notes, status) for a lead."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM lead_user_data WHERE lead_id = ?",
                (lead_id,)
            ).fetchone()
            return dict(row) if row else None
    
    def get_all_user_data(self) -> Dict[str, Dict]:
        """Get all user data as a dict keyed by lead_id."""
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM lead_user_data").fetchall()
            return {row['lead_id']: dict(row) for row in rows}
    
    def save_lead_user_data(
        self, 
        lead_id: str, 
        outreach_status: Optional[str] = None,
        notes: Optional[str] = None
    ):
        """Save or update user data for a lead."""
        with self._get_connection() as conn:
            # Check if exists
            existing = conn.execute(
                "SELECT lead_id FROM lead_user_data WHERE lead_id = ?",
                (lead_id,)
            ).fetchone()
            
            if existing:
                # Update
                updates = []
                params = []
                if outreach_status is not None:
                    updates.append("outreach_status = ?")
                    params.append(outreach_status)
                if notes is not None:
                    updates.append("notes = ?")
                    params.append(notes)
                updates.append("updated_at = ?")
                params.append(datetime.now())
                params.append(lead_id)
                
                conn.execute(
                    f"UPDATE lead_user_data SET {', '.join(updates)} WHERE lead_id = ?",
                    params
                )
            else:
                # Insert
                conn.execute(
                    """INSERT INTO lead_user_data (lead_id, outreach_status, notes)
                       VALUES (?, ?, ?)""",
                    (lead_id, outreach_status or 'new', notes)
                )
            conn.commit()
    
    # === Enrichment Cache ===
    
    def get_enrichment(self, lead_id: str) -> Optional[Dict]:
        """Get cached enrichment data for a lead."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM enrichment_cache WHERE lead_id = ?",
                (lead_id,)
            ).fetchone()
            if row:
                data = dict(row)
                # Parse JSON fields
                if data.get('enrichment_sources'):
                    data['enrichment_sources'] = json.loads(data['enrichment_sources'])
                return data
            return None
    
    def get_all_enrichments(self) -> Dict[str, Dict]:
        """Get all enrichment data as a dict keyed by lead_id."""
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM enrichment_cache").fetchall()
            result = {}
            for row in rows:
                data = dict(row)
                if data.get('enrichment_sources'):
                    data['enrichment_sources'] = json.loads(data['enrichment_sources'])
                result[data['lead_id']] = data
            return result
    
    def save_enrichment(
        self,
        lead_id: str,
        phone: Optional[str] = None,
        email: Optional[str] = None,
        website: Optional[str] = None,
        business_summary: Optional[str] = None,
        owner_principal: Optional[str] = None,
        enrichment_status: str = "none",
        enrichment_sources: Optional[List[str]] = None,
    ):
        """Save enrichment data for a lead."""
        with self._get_connection() as conn:
            sources_json = json.dumps(enrichment_sources or [])
            
            conn.execute(
                """INSERT OR REPLACE INTO enrichment_cache 
                   (lead_id, phone, email, website, business_summary, owner_principal,
                    enrichment_status, enrichment_sources, last_enriched)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (lead_id, phone, email, website, business_summary, owner_principal,
                 enrichment_status, sources_json, datetime.now())
            )
            conn.commit()
    
    # === Bulk Operations ===
    
    def apply_persisted_data_to_leads(self, leads: list) -> list:
        """
        Apply persisted user data and enrichment to leads.
        
        Call this after loading leads from HPD to restore saved data.
        """
        user_data = self.get_all_user_data()
        enrichments = self.get_all_enrichments()
        
        for lead in leads:
            # Apply user data
            if lead.lead_id in user_data:
                data = user_data[lead.lead_id]
                lead.outreach_status = data.get('outreach_status', 'new')
                lead.notes = data.get('notes')
            
            # Apply enrichment
            if lead.lead_id in enrichments:
                enr = enrichments[lead.lead_id]
                if enr.get('phone') and not lead.phone:
                    lead.phone = enr['phone']
                if enr.get('email') and not lead.email:
                    lead.email = enr['email']
                if enr.get('website') and not lead.website:
                    lead.website = enr['website']
                if enr.get('business_summary') and not lead.business_summary:
                    lead.business_summary = enr['business_summary']
                if enr.get('owner_principal') and not lead.owner_principal:
                    lead.owner_principal = enr['owner_principal']
                if enr.get('enrichment_status'):
                    lead.enrichment_status = enr['enrichment_status']
                if enr.get('enrichment_sources'):
                    lead.enrichment_sources = enr['enrichment_sources']
        
        return leads
    
    def get_stats(self) -> Dict:
        """Get database statistics."""
        with self._get_connection() as conn:
            user_count = conn.execute("SELECT COUNT(*) FROM lead_user_data").fetchone()[0]
            enriched_count = conn.execute("SELECT COUNT(*) FROM enrichment_cache").fetchone()[0]
            status_counts = {}
            for row in conn.execute(
                "SELECT outreach_status, COUNT(*) as count FROM lead_user_data GROUP BY outreach_status"
            ):
                status_counts[row['outreach_status']] = row['count']
            
            return {
                "total_user_records": user_count,
                "total_enriched": enriched_count,
                "by_status": status_counts,
            }


# Singleton instance
_db_instance: Optional[LeadsDatabase] = None


def get_database() -> LeadsDatabase:
    """Get the database singleton."""
    global _db_instance
    if _db_instance is None:
        _db_instance = LeadsDatabase()
    return _db_instance
