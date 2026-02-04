"""
SQLite database for persisting leads data.

Stores:
- Full lead data (survives server restarts)
- Lead metadata (notes, outreach_status)
- Enrichment results (phone, email, website, etc.)
- User-added data that shouldn't be lost on refresh
"""
import json
import sqlite3
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Optional, Dict, List, TYPE_CHECKING
from contextlib import contextmanager

if TYPE_CHECKING:
    from src.transform.aggregate import Lead

logger = logging.getLogger(__name__)

import os

# Database path - use environment variable if set (for Railway volume), otherwise local
_db_path_env = os.environ.get("DATABASE_PATH")
if _db_path_env:
    DEFAULT_DB_PATH = Path(_db_path_env)
else:
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
                -- Full lead data (survives server restarts)
                CREATE TABLE IF NOT EXISTS leads (
                    lead_id TEXT PRIMARY KEY,
                    agent_name TEXT,
                    owner_name TEXT,
                    owner_type TEXT,
                    portfolio_size INTEGER,
                    total_units INTEGER,
                    buildings TEXT,  -- JSON array of addresses
                    building_ids TEXT,  -- JSON array of building IDs
                    contacts TEXT,  -- JSON array of contact objects
                    address TEXT,
                    boro TEXT,
                    boros TEXT,  -- JSON array
                    reg_status TEXT,
                    last_registration TEXT,
                    dos_id TEXT,
                    dos_status TEXT,
                    phone TEXT,
                    email TEXT,
                    website TEXT,
                    business_summary TEXT,
                    owner_principal TEXT,
                    enrichment_status TEXT DEFAULT 'none',
                    enrichment_sources TEXT,  -- JSON array
                    last_enriched TIMESTAMP,
                    score REAL DEFAULT 0.0,
                    score_breakdown TEXT,  -- JSON object
                    tags TEXT,  -- JSON array
                    opportunity_note TEXT,
                    outreach_status TEXT DEFAULT 'new',
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                -- Lead user data (notes, status, etc.) - kept for backward compatibility
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
                
                -- Outreach attempts log
                CREATE TABLE IF NOT EXISTS outreach_attempts (
                    id TEXT PRIMARY KEY,
                    lead_id TEXT NOT NULL,
                    method TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    notes TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (lead_id) REFERENCES lead_user_data(lead_id)
                );
                
                -- Indexes for faster lookups
                CREATE INDEX IF NOT EXISTS idx_lead_status ON lead_user_data(outreach_status);
                CREATE INDEX IF NOT EXISTS idx_outreach_lead ON outreach_attempts(lead_id);
                CREATE INDEX IF NOT EXISTS idx_leads_score ON leads(score DESC);
                CREATE INDEX IF NOT EXISTS idx_leads_boro ON leads(boro);
                CREATE INDEX IF NOT EXISTS idx_leads_enrichment ON leads(enrichment_status);
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
    
    # === Full Lead Persistence ===
    
    def save_leads(self, leads: List["Lead"]) -> int:
        """
        Save all leads to the database (bulk upsert).
        
        This persists the complete lead data so it survives server restarts.
        Returns the number of leads saved.
        """
        if not leads:
            return 0
        
        with self._get_connection() as conn:
            # Use INSERT OR REPLACE for upsert behavior
            for lead in leads:
                conn.execute(
                    """INSERT OR REPLACE INTO leads (
                        lead_id, agent_name, owner_name, owner_type,
                        portfolio_size, total_units, buildings, building_ids,
                        contacts, address, boro, boros, reg_status,
                        last_registration, dos_id, dos_status, phone, email,
                        website, business_summary, owner_principal, enrichment_status,
                        enrichment_sources, last_enriched, score, score_breakdown,
                        tags, opportunity_note, outreach_status, notes, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        lead.lead_id,
                        lead.agent_name,
                        lead.owner_name,
                        lead.owner_type,
                        lead.portfolio_size,
                        lead.total_units,
                        json.dumps(lead.buildings),
                        json.dumps(lead.building_ids),
                        json.dumps(lead.contacts),
                        lead.address,
                        lead.boro,
                        json.dumps(lead.boros),
                        lead.reg_status,
                        lead.last_registration.isoformat() if lead.last_registration else None,
                        lead.dos_id,
                        lead.dos_status,
                        lead.phone,
                        lead.email,
                        lead.website,
                        lead.business_summary,
                        lead.owner_principal,
                        lead.enrichment_status,
                        json.dumps(lead.enrichment_sources),
                        lead.last_enriched.isoformat() if lead.last_enriched else None,
                        lead.score,
                        json.dumps(lead.score_breakdown),
                        json.dumps(lead.tags),
                        lead.opportunity_note,
                        lead.outreach_status,
                        lead.notes,
                        datetime.now().isoformat(),
                    )
                )
            conn.commit()
            logger.info(f"Saved {len(leads)} leads to database")
            return len(leads)
    
    def load_all_leads(self) -> List["Lead"]:
        """
        Load all leads from the database.
        
        Returns an empty list if no leads are stored.
        """
        # Import here to avoid circular imports
        from src.transform.aggregate import Lead
        
        with self._get_connection() as conn:
            rows = conn.execute(
                """SELECT * FROM leads ORDER BY score DESC"""
            ).fetchall()
            
            if not rows:
                logger.info("No leads found in database")
                return []
            
            leads = []
            for row in rows:
                row_dict = dict(row)
                
                # Parse JSON fields
                buildings = json.loads(row_dict.get('buildings') or '[]')
                building_ids = json.loads(row_dict.get('building_ids') or '[]')
                contacts = json.loads(row_dict.get('contacts') or '[]')
                boros = json.loads(row_dict.get('boros') or '[]')
                enrichment_sources = json.loads(row_dict.get('enrichment_sources') or '[]')
                score_breakdown = json.loads(row_dict.get('score_breakdown') or '{}')
                tags = json.loads(row_dict.get('tags') or '[]')
                
                # Parse dates
                last_registration = None
                if row_dict.get('last_registration'):
                    try:
                        last_registration = date.fromisoformat(row_dict['last_registration'][:10])
                    except (ValueError, TypeError):
                        pass
                
                last_enriched = None
                if row_dict.get('last_enriched'):
                    try:
                        last_enriched = datetime.fromisoformat(row_dict['last_enriched'])
                    except (ValueError, TypeError):
                        pass
                
                created_at = datetime.now()
                if row_dict.get('created_at'):
                    try:
                        created_at = datetime.fromisoformat(row_dict['created_at'])
                    except (ValueError, TypeError):
                        pass
                
                updated_at = datetime.now()
                if row_dict.get('updated_at'):
                    try:
                        updated_at = datetime.fromisoformat(row_dict['updated_at'])
                    except (ValueError, TypeError):
                        pass
                
                lead = Lead(
                    lead_id=row_dict['lead_id'],
                    agent_name=row_dict.get('agent_name') or '',
                    owner_name=row_dict.get('owner_name') or '',
                    owner_type=row_dict.get('owner_type') or '',
                    portfolio_size=row_dict.get('portfolio_size') or 0,
                    total_units=row_dict.get('total_units') or 0,
                    buildings=buildings,
                    building_ids=building_ids,
                    contacts=contacts,
                    address=row_dict.get('address'),
                    boro=row_dict.get('boro') or '',
                    boros=boros,
                    reg_status=row_dict.get('reg_status') or '',
                    last_registration=last_registration,
                    dos_id=row_dict.get('dos_id'),
                    dos_status=row_dict.get('dos_status'),
                    phone=row_dict.get('phone'),
                    email=row_dict.get('email'),
                    website=row_dict.get('website'),
                    business_summary=row_dict.get('business_summary'),
                    owner_principal=row_dict.get('owner_principal'),
                    enrichment_status=row_dict.get('enrichment_status') or 'none',
                    enrichment_sources=enrichment_sources,
                    last_enriched=last_enriched,
                    score=row_dict.get('score') or 0.0,
                    score_breakdown=score_breakdown,
                    tags=tags,
                    opportunity_note=row_dict.get('opportunity_note'),
                    outreach_status=row_dict.get('outreach_status') or 'new',
                    notes=row_dict.get('notes'),
                    created_at=created_at,
                    updated_at=updated_at,
                )
                leads.append(lead)
            
            logger.info(f"Loaded {len(leads)} leads from database")
            return leads
    
    def get_leads_count(self) -> int:
        """Get the number of leads stored in the database."""
        with self._get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
            return count
    
    def get_last_refresh_time(self) -> Optional[datetime]:
        """Get the most recent updated_at timestamp from leads."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT MAX(updated_at) as last_update FROM leads"
            ).fetchone()
            if row and row['last_update']:
                try:
                    return datetime.fromisoformat(row['last_update'])
                except (ValueError, TypeError):
                    pass
            return None
    
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
    
    # === Outreach Attempts ===
    
    def add_outreach_attempt(self, lead_id: str, attempt: Dict):
        """Add an outreach attempt to the log."""
        with self._get_connection() as conn:
            conn.execute(
                """INSERT INTO outreach_attempts (id, lead_id, method, outcome, notes, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (attempt['id'], lead_id, attempt['method'], attempt['outcome'], 
                 attempt.get('notes'), attempt.get('timestamp', datetime.now().isoformat()))
            )
            conn.commit()
    
    def get_outreach_attempts(self, lead_id: str) -> List[Dict]:
        """Get all outreach attempts for a lead, most recent first."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """SELECT id, method, outcome, notes, timestamp 
                   FROM outreach_attempts 
                   WHERE lead_id = ? 
                   ORDER BY timestamp DESC""",
                (lead_id,)
            ).fetchall()
            return [dict(row) for row in rows]
    
    def get_all_outreach_attempts(self) -> Dict[str, List[Dict]]:
        """Get all outreach attempts grouped by lead_id."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """SELECT lead_id, id, method, outcome, notes, timestamp 
                   FROM outreach_attempts 
                   ORDER BY timestamp DESC"""
            ).fetchall()
            result = {}
            for row in rows:
                lead_id = row['lead_id']
                if lead_id not in result:
                    result[lead_id] = []
                result[lead_id].append({
                    'id': row['id'],
                    'method': row['method'],
                    'outcome': row['outcome'],
                    'notes': row['notes'],
                    'timestamp': row['timestamp'],
                })
            return result


# Singleton instance
_db_instance: Optional[LeadsDatabase] = None


def get_database() -> LeadsDatabase:
    """Get the database singleton."""
    global _db_instance
    if _db_instance is None:
        _db_instance = LeadsDatabase()
    return _db_instance
