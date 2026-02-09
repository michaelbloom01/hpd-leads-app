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
                    building_types TEXT,  -- JSON object with condo, coop, rental counts
                    building_classes TEXT,  -- JSON object with raw class codes
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                -- Add building_types and building_classes columns if they don't exist (migration)
                -- SQLite doesn't support ADD COLUMN IF NOT EXISTS, so we catch errors
            """)
            
            # Migration: Add building_types column if it doesn't exist
            try:
                conn.execute("ALTER TABLE leads ADD COLUMN building_types TEXT")
                logger.info("Added building_types column to leads table")
            except sqlite3.OperationalError:
                pass  # Column already exists
            
            try:
                conn.execute("ALTER TABLE leads ADD COLUMN building_classes TEXT")
                logger.info("Added building_classes column to leads table")
            except sqlite3.OperationalError:
                pass  # Column already exists
            
            # Migration: Add entity classification columns (Phase 0)
            for col, col_type in [
                ("entity_type", "TEXT DEFAULT 'unknown'"),
                ("company_name", "TEXT"),
                ("primary_contact", "TEXT"),
                ("primary_contact_title", "TEXT"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE leads ADD COLUMN {col} {col_type}")
                    logger.info(f"Added {col} column to leads table")
                except sqlite3.OperationalError:
                    pass  # Column already exists
            
            conn.executescript("""
                
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
                
                -- Enrichment job state (persists across restarts)
                CREATE TABLE IF NOT EXISTS enrichment_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_type TEXT NOT NULL,  -- 'batch', 'single'
                    status TEXT NOT NULL,  -- 'running', 'completed', 'failed', 'paused'
                    total_leads INTEGER DEFAULT 0,
                    processed INTEGER DEFAULT 0,
                    completed INTEGER DEFAULT 0,
                    failed INTEGER DEFAULT 0,
                    dos_found INTEGER DEFAULT 0,
                    web_found INTEGER DEFAULT 0,
                    current_lead TEXT,
                    lead_ids_remaining TEXT,  -- JSON array of lead IDs not yet processed
                    config TEXT,  -- JSON config for the job
                    error TEXT,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    finished_at TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                -- Indexes for faster lookups
                CREATE INDEX IF NOT EXISTS idx_lead_status ON lead_user_data(outreach_status);
                CREATE INDEX IF NOT EXISTS idx_outreach_lead ON outreach_attempts(lead_id);
                CREATE INDEX IF NOT EXISTS idx_leads_score ON leads(score DESC);
                CREATE INDEX IF NOT EXISTS idx_leads_boro ON leads(boro);
                CREATE INDEX IF NOT EXISTS idx_leads_enrichment ON leads(enrichment_status);
                CREATE INDEX IF NOT EXISTS idx_enrichment_jobs_status ON enrichment_jobs(status);
                
                -- Performance indexes for filtering (Phase 1)
                CREATE INDEX IF NOT EXISTS idx_leads_portfolio ON leads(portfolio_size DESC);
                CREATE INDEX IF NOT EXISTS idx_leads_phone ON leads(phone);
                CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email);
                CREATE INDEX IF NOT EXISTS idx_leads_website ON leads(website);
                CREATE INDEX IF NOT EXISTS idx_leads_outreach ON leads(outreach_status);
                CREATE INDEX IF NOT EXISTS idx_leads_score_portfolio ON leads(score DESC, portfolio_size DESC);
                CREATE INDEX IF NOT EXISTS idx_leads_entity_type ON leads(entity_type);
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
    
    def update_lead(self, lead_id: str, updates: Dict) -> bool:
        """
        Update specific fields of a lead in the database.
        
        Args:
            lead_id: The lead ID to update
            updates: Dict of field names to new values
            
        Returns:
            True if the lead was found and updated, False otherwise.
        """
        if not updates:
            return False
        
        # Only allow updating certain fields to prevent SQL injection
        allowed_fields = {
            'business_summary', 'phone', 'email', 'website', 
            'enrichment_status', 'last_enriched', 'owner_principal',
            'dos_id', 'dos_status', 'notes', 'outreach_status',
            'entity_type', 'company_name', 'primary_contact', 'primary_contact_title',
            'enrichment_sources', 'score', 'score_breakdown',
        }
        
        # Filter to allowed fields
        safe_updates = {k: v for k, v in updates.items() if k in allowed_fields}
        if not safe_updates:
            logger.warning(f"No valid fields to update for lead {lead_id}")
            return False
        
        with self._get_connection() as conn:
            # Build the SET clause
            set_clause = ", ".join(f"{field} = ?" for field in safe_updates.keys())
            values = list(safe_updates.values()) + [lead_id]
            
            cursor = conn.execute(
                f"UPDATE leads SET {set_clause} WHERE lead_id = ?",
                values
            )
            conn.commit()
            
            if cursor.rowcount > 0:
                logger.info(f"Updated lead {lead_id}: {list(safe_updates.keys())}")
                return True
            else:
                logger.warning(f"Lead {lead_id} not found in database")
                return False
    
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
                # Serialize building_types to JSON
                building_types_json = None
                if lead.building_types:
                    building_types_json = json.dumps(lead.building_types.to_dict())
                
                conn.execute(
                    """INSERT OR REPLACE INTO leads (
                        lead_id, agent_name, owner_name, owner_type,
                        portfolio_size, total_units, buildings, building_ids,
                        contacts, address, boro, boros, reg_status,
                        last_registration, dos_id, dos_status, phone, email,
                        website, business_summary, owner_principal, enrichment_status,
                        enrichment_sources, last_enriched, score, score_breakdown,
                        tags, opportunity_note, outreach_status, notes, 
                        building_types, building_classes,
                        entity_type, company_name, primary_contact, primary_contact_title,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                        building_types_json,
                        json.dumps(lead.building_classes) if lead.building_classes else None,
                        getattr(lead, 'entity_type', 'unknown'),
                        getattr(lead, 'company_name', None),
                        getattr(lead, 'primary_contact', None),
                        getattr(lead, 'primary_contact_title', None),
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
        from src.transform.aggregate import Lead, BuildingTypeBreakdown
        
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
                
                # Parse building_types from JSON
                building_types = BuildingTypeBreakdown()
                if row_dict.get('building_types'):
                    try:
                        bt_data = json.loads(row_dict['building_types'])
                        building_types = BuildingTypeBreakdown(
                            condo=bt_data.get('condo', 0),
                            coop=bt_data.get('coop', 0),
                            rental_elevator=bt_data.get('rental_elevator', 0),
                            rental_walkup=bt_data.get('rental_walkup', 0),
                            small_residential=bt_data.get('small_residential', 0),
                            other=bt_data.get('other', 0),
                            unknown=bt_data.get('unknown', 0),
                        )
                    except (json.JSONDecodeError, TypeError):
                        pass
                
                # Parse building_classes from JSON
                building_classes = {}
                if row_dict.get('building_classes'):
                    try:
                        building_classes = json.loads(row_dict['building_classes'])
                    except (json.JSONDecodeError, TypeError):
                        pass
                
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
                    building_types=building_types,
                    building_classes=building_classes,
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
                    entity_type=row_dict.get('entity_type') or 'unknown',
                    company_name=row_dict.get('company_name'),
                    primary_contact=row_dict.get('primary_contact'),
                    primary_contact_title=row_dict.get('primary_contact_title'),
                    created_at=created_at,
                    updated_at=updated_at,
                )
                leads.append(lead)
            
            logger.info(f"Loaded {len(leads)} leads from database")
            return leads
    
    def get_leads_filtered(
        self,
        min_score: Optional[float] = None,
        min_portfolio: Optional[int] = None,
        boro: Optional[str] = None,
        has_phone: Optional[bool] = None,
        has_email: Optional[bool] = None,
        has_website: Optional[bool] = None,
        entity_type: Optional[str] = None,
        min_units: Optional[int] = None,
        enrichment_status: Optional[str] = None,
        outreach_status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple:
        """
        Get filtered leads using SQL WHERE clauses (fast, uses indexes).
        
        Returns:
            Tuple of (list of row dicts, total count matching filters)
        """
        where_clauses = []
        params = []
        
        if min_score is not None:
            where_clauses.append("score >= ?")
            params.append(min_score)
        if min_portfolio is not None:
            where_clauses.append("portfolio_size >= ?")
            params.append(min_portfolio)
        if boro is not None:
            # boro field is primary borough; also check boros JSON array
            where_clauses.append("(UPPER(boro) = UPPER(?) OR boros LIKE ?)")
            params.extend([boro, f'%"{boro.upper()}"%'])
        if has_phone is True:
            where_clauses.append("phone IS NOT NULL AND phone != ''")
        elif has_phone is False:
            where_clauses.append("(phone IS NULL OR phone = '')")
        if has_email is True:
            where_clauses.append("email IS NOT NULL AND email != ''")
        elif has_email is False:
            where_clauses.append("(email IS NULL OR email = '')")
        if has_website is True:
            where_clauses.append("website IS NOT NULL AND website != ''")
        elif has_website is False:
            where_clauses.append("(website IS NULL OR website = '')")
        if entity_type is not None:
            where_clauses.append("entity_type = ?")
            params.append(entity_type)
        if min_units is not None:
            where_clauses.append("total_units >= ?")
            params.append(min_units)
        if enrichment_status is not None:
            where_clauses.append("enrichment_status = ?")
            params.append(enrichment_status)
        if outreach_status is not None:
            where_clauses.append("outreach_status = ?")
            params.append(outreach_status)
        
        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        
        with self._get_connection() as conn:
            # Get total count
            count_row = conn.execute(
                f"SELECT COUNT(*) FROM leads WHERE {where_sql}", params
            ).fetchone()
            total = count_row[0]
            
            # Get paginated results
            rows = conn.execute(
                f"SELECT * FROM leads WHERE {where_sql} ORDER BY score DESC LIMIT ? OFFSET ?",
                params + [limit, offset]
            ).fetchall()
            
            return [dict(r) for r in rows], total
    
    def get_stats_sql(self) -> Dict:
        """
        Get comprehensive stats using SQL aggregation (fast, no Python iteration).
        """
        with self._get_connection() as conn:
            # Core counts
            core = conn.execute("""
                SELECT 
                    COUNT(*) as total_leads,
                    SUM(portfolio_size) as total_buildings,
                    SUM(total_units) as total_units,
                    COUNT(CASE WHEN phone IS NOT NULL AND phone != '' THEN 1 END) as with_phone,
                    COUNT(CASE WHEN email IS NOT NULL AND email != '' THEN 1 END) as with_email,
                    COUNT(CASE WHEN website IS NOT NULL AND website != '' THEN 1 END) as with_website,
                    MAX(score) as top_score,
                    AVG(score) as avg_score
                FROM leads
            """).fetchone()
            
            # Borough distribution
            by_borough = {}
            for row in conn.execute("SELECT boro, COUNT(*) as cnt FROM leads GROUP BY boro"):
                by_borough[row['boro'] or 'Unknown'] = row['cnt']
            
            # Enrichment status distribution
            by_enrichment = {}
            for row in conn.execute("SELECT enrichment_status, COUNT(*) as cnt FROM leads GROUP BY enrichment_status"):
                by_enrichment[row['enrichment_status'] or 'none'] = row['cnt']
            
            # Outreach status distribution
            by_outreach = {}
            for row in conn.execute("SELECT outreach_status, COUNT(*) as cnt FROM leads GROUP BY outreach_status"):
                by_outreach[row['outreach_status'] or 'new'] = row['cnt']
            
            # Entity type distribution
            by_entity = {}
            for row in conn.execute("SELECT entity_type, COUNT(*) as cnt FROM leads GROUP BY entity_type"):
                by_entity[row['entity_type'] or 'unknown'] = row['cnt']
            
            # Score distribution
            score_dist = {}
            for row in conn.execute("""
                SELECT 
                    CASE 
                        WHEN score < 20 THEN '0-20'
                        WHEN score < 40 THEN '20-40'
                        WHEN score < 60 THEN '40-60'
                        WHEN score < 80 THEN '60-80'
                        ELSE '80-100'
                    END as bucket,
                    COUNT(*) as cnt
                FROM leads GROUP BY bucket
            """):
                score_dist[row['bucket']] = row['cnt']
            
            # Portfolio distribution
            portfolio_dist = {}
            for row in conn.execute("""
                SELECT 
                    CASE 
                        WHEN portfolio_size <= 5 THEN '1-5'
                        WHEN portfolio_size <= 10 THEN '6-10'
                        WHEN portfolio_size <= 25 THEN '11-25'
                        WHEN portfolio_size <= 50 THEN '26-50'
                        WHEN portfolio_size <= 100 THEN '51-100'
                        ELSE '100+'
                    END as bucket,
                    COUNT(*) as cnt
                FROM leads GROUP BY bucket
            """):
                portfolio_dist[row['bucket']] = row['cnt']
            
            return {
                "total_leads": core['total_leads'] or 0,
                "total_buildings": core['total_buildings'] or 0,
                "total_units": core['total_units'] or 0,
                "with_phone": core['with_phone'] or 0,
                "with_email": core['with_email'] or 0,
                "with_website": core['with_website'] or 0,
                "top_score": core['top_score'] or 0.0,
                "avg_score": round(core['avg_score'] or 0.0, 1),
                "by_borough": by_borough,
                "by_enrichment_status": by_enrichment,
                "by_outreach_status": by_outreach,
                "by_entity_type": by_entity,
                "score_distribution": score_dist,
                "portfolio_distribution": portfolio_dist,
            }
    
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


    # === Enrichment Job Management ===
    
    def create_enrichment_job(
        self, 
        job_type: str, 
        lead_ids: List[str], 
        config: Optional[Dict] = None
    ) -> int:
        """Create a new enrichment job and return its ID."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO enrichment_jobs 
                   (job_type, status, total_leads, lead_ids_remaining, config)
                   VALUES (?, 'running', ?, ?, ?)""",
                (job_type, len(lead_ids), json.dumps(lead_ids), json.dumps(config or {}))
            )
            conn.commit()
            job_id = cursor.lastrowid
            logger.info(f"Created enrichment job {job_id} for {len(lead_ids)} leads")
            return job_id
    
    def update_enrichment_job(
        self,
        job_id: int,
        processed: Optional[int] = None,
        completed: Optional[int] = None,
        failed: Optional[int] = None,
        dos_found: Optional[int] = None,
        web_found: Optional[int] = None,
        current_lead: Optional[str] = None,
        lead_ids_remaining: Optional[List[str]] = None,
        status: Optional[str] = None,
        error: Optional[str] = None,
    ):
        """Update enrichment job progress."""
        updates = []
        params = []
        
        if processed is not None:
            updates.append("processed = ?")
            params.append(processed)
        if completed is not None:
            updates.append("completed = ?")
            params.append(completed)
        if failed is not None:
            updates.append("failed = ?")
            params.append(failed)
        if dos_found is not None:
            updates.append("dos_found = ?")
            params.append(dos_found)
        if web_found is not None:
            updates.append("web_found = ?")
            params.append(web_found)
        if current_lead is not None:
            updates.append("current_lead = ?")
            params.append(current_lead)
        if lead_ids_remaining is not None:
            updates.append("lead_ids_remaining = ?")
            params.append(json.dumps(lead_ids_remaining))
        if status is not None:
            updates.append("status = ?")
            params.append(status)
            if status in ('completed', 'failed'):
                updates.append("finished_at = ?")
                params.append(datetime.now().isoformat())
        if error is not None:
            updates.append("error = ?")
            params.append(error)
        
        updates.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.append(job_id)
        
        with self._get_connection() as conn:
            conn.execute(
                f"UPDATE enrichment_jobs SET {', '.join(updates)} WHERE id = ?",
                params
            )
            conn.commit()
    
    def get_active_enrichment_job(self) -> Optional[Dict]:
        """Get the currently running or paused enrichment job."""
        with self._get_connection() as conn:
            row = conn.execute(
                """SELECT * FROM enrichment_jobs 
                   WHERE status IN ('running', 'paused') 
                   ORDER BY id DESC LIMIT 1"""
            ).fetchone()
            if row:
                data = dict(row)
                data['lead_ids_remaining'] = json.loads(data.get('lead_ids_remaining') or '[]')
                data['config'] = json.loads(data.get('config') or '{}')
                return data
            return None
    
    def get_enrichment_job(self, job_id: int) -> Optional[Dict]:
        """Get a specific enrichment job by ID."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM enrichment_jobs WHERE id = ?",
                (job_id,)
            ).fetchone()
            if row:
                data = dict(row)
                data['lead_ids_remaining'] = json.loads(data.get('lead_ids_remaining') or '[]')
                data['config'] = json.loads(data.get('config') or '{}')
                return data
            return None
    
    def get_enrichment_stats(self) -> Dict:
        """Get enrichment statistics from leads table."""
        with self._get_connection() as conn:
            # Count by enrichment status
            status_counts = {}
            for row in conn.execute(
                "SELECT enrichment_status, COUNT(*) as count FROM leads GROUP BY enrichment_status"
            ):
                status_counts[row['enrichment_status'] or 'none'] = row['count']
            
            # Count with contact info
            with_phone = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE phone IS NOT NULL AND phone != ''"
            ).fetchone()[0]
            with_email = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE email IS NOT NULL AND email != ''"
            ).fetchone()[0]
            with_website = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE website IS NOT NULL AND website != ''"
            ).fetchone()[0]
            
            return {
                "by_status": status_counts,
                "with_phone": with_phone,
                "with_email": with_email,
                "with_website": with_website,
            }


# Singleton instance
_db_instance: Optional[LeadsDatabase] = None


def get_database() -> LeadsDatabase:
    """Get the database singleton."""
    global _db_instance
    if _db_instance is None:
        _db_instance = LeadsDatabase()
    return _db_instance
