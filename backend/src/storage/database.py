"""
SQLite database for persisting leads data.

Stores:
- Full lead data (survives server restarts)
- Lead metadata (notes, outreach_status)
- Enrichment results (phone, email, website, etc.)
- User-added data that shouldn't be lost on refresh
"""
import json
import os
import sqlite3
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Optional, Dict, List, TYPE_CHECKING
from contextlib import contextmanager

if TYPE_CHECKING:
    from src.transform.aggregate import Lead

logger = logging.getLogger(__name__)

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
        """Get a database connection with WAL mode for crash-safety and concurrent reads."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")  # R6: crash-safe, allows concurrent reads during writes
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
            
            # Migration: Add enrichment retry column (Phase 2.7)
            try:
                conn.execute("ALTER TABLE leads ADD COLUMN enrichment_retries INTEGER DEFAULT 0")
                logger.info("Added enrichment_retries column to leads table")
            except sqlite3.OperationalError:
                pass  # Column already exists
            
            # Migration: Add revenue estimation columns (Phase 5.1)
            for col, col_type in [
                ("estimated_monthly_revenue", "REAL"),
                ("estimated_annual_revenue", "REAL"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE leads ADD COLUMN {col} {col_type}")
                    logger.info(f"Added {col} column to leads table")
                except sqlite3.OperationalError:
                    pass  # Column already exists
            
            # Migration: Add violations columns (Phase 5.2)
            for col, col_type in [
                ("violation_count", "INTEGER DEFAULT 0"),
                ("violation_class_a", "INTEGER DEFAULT 0"),
                ("violation_class_b", "INTEGER DEFAULT 0"),
                ("violation_class_c", "INTEGER DEFAULT 0"),
                ("violations_per_unit", "REAL DEFAULT 0.0"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE leads ADD COLUMN {col} {col_type}")
                    logger.info(f"Added {col} column to leads table")
                except sqlite3.OperationalError:
                    pass  # Column already exists
            
            # Migration: Add pipeline columns (Phase 5.3)
            for col, col_type in [
                ("pipeline_stage", "TEXT DEFAULT 'research'"),
                ("next_follow_up", "TEXT"),
                ("priority_rank", "INTEGER DEFAULT 0"),
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
                CREATE INDEX IF NOT EXISTS idx_leads_pipeline_stage ON leads(pipeline_stage);
                CREATE INDEX IF NOT EXISTS idx_leads_next_follow_up ON leads(next_follow_up);
                CREATE INDEX IF NOT EXISTS idx_leads_priority_rank ON leads(priority_rank DESC);
                
                -- NY DOS cache (Phase 2.7c)
                CREATE TABLE IF NOT EXISTS dos_cache (
                    cache_key TEXT PRIMARY KEY,
                    result TEXT,  -- JSON serialized DOSEntity or null
                    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                -- Google Places cache (Phase 2.7d)
                CREATE TABLE IF NOT EXISTS places_cache (
                    cache_key TEXT PRIMARY KEY,
                    result TEXT,  -- JSON serialized result
                    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                -- Change alerts (Phase 5.4)
                CREATE TABLE IF NOT EXISTS change_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_type TEXT NOT NULL,  -- new_company, portfolio_change, contact_change, buildings_added, buildings_removed
                    lead_id TEXT,
                    description TEXT NOT NULL,
                    details TEXT,  -- JSON
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    dismissed INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_change_alerts_created ON change_alerts(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_change_alerts_dismissed ON change_alerts(dismissed);
                
                -- Outreach events (Phase 5.3) - richer than outreach_attempts
                CREATE TABLE IF NOT EXISTS outreach_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lead_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    method TEXT,
                    outcome TEXT,
                    notes TEXT,
                    next_follow_up TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (lead_id) REFERENCES leads(lead_id)
                );
                CREATE INDEX IF NOT EXISTS idx_outreach_events_lead ON outreach_events(lead_id);
                CREATE INDEX IF NOT EXISTS idx_outreach_events_stage ON outreach_events(stage);
                
                -- App settings (key/value store for persistent state)
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                -- R11: AI summary cache (same TTL pattern as dos_cache/places_cache)
                CREATE TABLE IF NOT EXISTS ai_summary_cache (
                    lead_id TEXT PRIMARY KEY,
                    summary TEXT,
                    model TEXT,
                    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                -- Agent conversation memory (AI Agent feature)
                CREATE TABLE IF NOT EXISTS agent_conversations (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE TABLE IF NOT EXISTS agent_messages (
                    message_id TEXT PRIMARY KEY,
                    conversation_id TEXT REFERENCES agent_conversations(conversation_id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    structured_data TEXT,
                    claude_messages TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_agent_messages_conv ON agent_messages(conversation_id, created_at);
                
                -- Pending agent actions (for confirmation gate)
                CREATE TABLE IF NOT EXISTS agent_pending_actions (
                    action_id TEXT PRIMARY KEY,
                    conversation_id TEXT REFERENCES agent_conversations(conversation_id) ON DELETE CASCADE,
                    tool_name TEXT NOT NULL,
                    tool_input TEXT NOT NULL,
                    description TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                -- Users table (JWT auth)
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
                
                -- Schema version tracking
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    description TEXT
                );
                
                -- === Data Robustness Tables (v2) ===
                
                -- Building registry: one row per physical building for address-level lookup
                CREATE TABLE IF NOT EXISTS building_registry (
                    building_id TEXT PRIMARY KEY,
                    registration_id TEXT,
                    lead_id TEXT,
                    address TEXT NOT NULL DEFAULT '',
                    house_number TEXT,
                    street_name TEXT,
                    boro TEXT,
                    zip_code TEXT,
                    block TEXT,
                    lot TEXT,
                    bbl TEXT,
                    units_res INTEGER DEFAULT 0,
                    building_class TEXT,
                    building_type TEXT,
                    last_registration TEXT,
                    status TEXT DEFAULT 'active',
                    last_seen_in_hpd TIMESTAMP,
                    source_refresh_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_building_address ON building_registry(street_name, house_number);
                CREATE INDEX IF NOT EXISTS idx_building_lead ON building_registry(lead_id);
                CREATE INDEX IF NOT EXISTS idx_building_bbl ON building_registry(bbl);
                CREATE INDEX IF NOT EXISTS idx_building_full_address ON building_registry(address);
                CREATE INDEX IF NOT EXISTS idx_building_boro ON building_registry(boro);
                
                -- Refresh audit log: one row per pipeline run with counts at every stage
                CREATE TABLE IF NOT EXISTS refresh_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    refresh_id TEXT NOT NULL,
                    started_at TIMESTAMP,
                    finished_at TIMESTAMP,
                    status TEXT DEFAULT 'running',
                    hpd_total_available INTEGER,
                    hpd_buildings_fetched INTEGER,
                    hpd_contacts_fetched INTEGER,
                    buildings_normalized INTEGER,
                    buildings_dropped INTEGER,
                    leads_aggregated INTEGER,
                    leads_saved INTEGER,
                    leads_before_refresh INTEGER,
                    leads_after_refresh INTEGER,
                    leads_net_change INTEGER,
                    buildings_registered INTEGER,
                    pluto_lookups_success INTEGER,
                    pluto_lookups_failed INTEGER,
                    coverage_percent REAL,
                    error_message TEXT,
                    config_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_refresh_audit_started ON refresh_audit_log(started_at DESC);
                
                -- Stats cache: pre-computed counts to avoid GROUP BY on every request
                CREATE TABLE IF NOT EXISTS stats_cache (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                -- Composite indexes for common filter+sort queries (performance)
                CREATE INDEX IF NOT EXISTS idx_leads_score_pipeline ON leads(pipeline_stage, score DESC);
                CREATE INDEX IF NOT EXISTS idx_leads_score_outreach ON leads(outreach_status, score DESC);
                CREATE INDEX IF NOT EXISTS idx_leads_boro_score ON leads(boro, score DESC);
            """)
            
            # Migration: Add data_staleness column to leads (v2)
            try:
                conn.execute("ALTER TABLE leads ADD COLUMN data_staleness TEXT DEFAULT 'current'")
                logger.info("Added data_staleness column to leads table")
            except sqlite3.OperationalError:
                pass  # Column already exists
            
            # Record initial schema version if not present
            existing_version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            if existing_version is None:
                conn.execute(
                    "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                    (1, "Initial schema with leads, enrichment, agent, users tables"),
                )
            
            # Record v2 schema if not present
            v2_exists = conn.execute("SELECT 1 FROM schema_version WHERE version = 2").fetchone()
            if not v2_exists:
                conn.execute(
                    "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                    (2, "Data robustness: building_registry, refresh_audit_log, stats_cache, data_staleness"),
                )
            
            conn.commit()
            logger.info(f"Database initialized at {self.db_path} (schema v{existing_version or 1})")
    
    # === App Settings ===
    
    def get_setting(self, key: str) -> Optional[str]:
        """Get a persistent setting value."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
            return row['value'] if row else None
    
    def set_setting(self, key: str, value: str):
        """Set a persistent setting value."""
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, datetime.now().isoformat())
            )
            conn.commit()
    
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
    
    def get_all_enrichment_from_leads(self) -> Dict[str, Dict]:
        """Load enrichment fields from the LEADS table itself.

        This is the authoritative source for enrichment data because most
        enrichment code paths write to the leads table (via update_lead or
        save_leads) but not all of them write to enrichment_cache.
        """
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

    def apply_persisted_data_to_leads(self, leads: list) -> list:
        """
        Apply persisted user data and enrichment to leads.
        
        Call this after loading leads from HPD to restore saved data.
        
        Reads from THREE sources (in priority order):
        1. The leads table itself (authoritative — most enrichment writes here)
        2. The enrichment_cache table (backup — older enrichment code writes here)
        3. The lead_user_data table (pipeline stage, notes, outreach status)
        """
        user_data = self.get_all_user_data()
        enrichment_cache = self.get_all_enrichments()
        leads_enrichment = self.get_all_enrichment_from_leads()
        
        # Merge: leads table takes priority, enrichment_cache fills gaps
        # IMPORTANT: Use `is not None` checks, NOT truthiness checks.
        # Falsy values like "", 0, False are valid data and must not be overwritten.
        merged_enrichment: Dict[str, Dict] = {}
        all_ids = set(enrichment_cache.keys()) | set(leads_enrichment.keys())
        for lid in all_ids:
            le = leads_enrichment.get(lid, {})
            ec = enrichment_cache.get(lid, {})
            merged = {}
            for field in ["phone", "email", "website", "business_summary",
                          "owner_principal", "enrichment_status", "enrichment_sources",
                          "pipeline_stage", "next_follow_up", "priority_rank"]:
                # Prefer leads table value if it exists (even if falsy), fall back to enrichment_cache
                val = le.get(field) if le.get(field) is not None else ec.get(field)
                if val is not None:
                    merged[field] = val
            merged_enrichment[lid] = merged
        
        for lead in leads:
            # Apply user data (outreach status, notes)
            if lead.lead_id in user_data:
                data = user_data[lead.lead_id]
                lead.outreach_status = data.get('outreach_status', 'new')
                lead.notes = data.get('notes')
            
            # Apply enrichment from merged sources
            if lead.lead_id in merged_enrichment:
                enr = merged_enrichment[lead.lead_id]
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
                if enr.get('enrichment_status') and enr['enrichment_status'] != 'none':
                    lead.enrichment_status = enr['enrichment_status']
                if enr.get('enrichment_sources'):
                    lead.enrichment_sources = enr['enrichment_sources']
                # Preserve pipeline/outreach state
                if enr.get('pipeline_stage') and enr['pipeline_stage'] != 'research':
                    lead.pipeline_stage = enr['pipeline_stage']
                if enr.get('next_follow_up'):
                    lead.next_follow_up = enr['next_follow_up']
                if enr.get('priority_rank') and enr['priority_rank'] > 0:
                    lead.priority_rank = enr['priority_rank']
        
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
            'enrichment_retries', 'estimated_monthly_revenue', 'estimated_annual_revenue',
            'violation_count', 'violation_class_a', 'violation_class_b', 'violation_class_c',
            'violations_per_unit', 'pipeline_stage', 'next_follow_up', 'priority_rank',
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
    
    def batch_update_revenue(self, updates: List[tuple]) -> int:
        """
        Batch update revenue for many leads in a single transaction.
        
        Args:
            updates: List of (lead_id, monthly_revenue, annual_revenue) tuples
            
        Returns:
            Number of leads updated.
        """
        if not updates:
            return 0
        
        with self._get_connection() as conn:
            conn.executemany(
                "UPDATE leads SET estimated_monthly_revenue = ?, estimated_annual_revenue = ? WHERE lead_id = ?",
                [(monthly, annual, lead_id) for lead_id, monthly, annual in updates]
            )
            conn.commit()
            logger.info(f"Batch updated revenue for {len(updates)} leads")
            return len(updates)
    
    def save_leads(self, leads: List["Lead"]) -> int:
        """
        Save leads to the database using a non-destructive merge upsert.
        
        Uses INSERT ... ON CONFLICT(lead_id) DO UPDATE SET with field-level rules:
        - HPD source fields: always overwrite from source
        - User fields (notes, outreach, pipeline): NEVER overwrite if already set by user
        - Enrichment fields: keep whichever is richer (COALESCE)
        
        Uses batch executemany for performance.
        Returns the number of leads saved.
        """
        if not leads:
            return 0
        
        UPSERT_SQL = """INSERT INTO leads (
                lead_id, agent_name, owner_name, owner_type,
                portfolio_size, total_units, buildings, building_ids,
                contacts, address, boro, boros, reg_status,
                last_registration, dos_id, dos_status, phone, email,
                website, business_summary, owner_principal, enrichment_status,
                enrichment_sources, last_enriched, score, score_breakdown,
                tags, opportunity_note, outreach_status, notes,
                building_types, building_classes,
                entity_type, company_name, primary_contact, primary_contact_title,
                estimated_monthly_revenue, estimated_annual_revenue,
                violation_count, violation_class_a, violation_class_b, violation_class_c,
                violations_per_unit,
                pipeline_stage, next_follow_up, priority_rank,
                enrichment_retries,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(lead_id) DO UPDATE SET
                -- HPD source fields: always update from source
                agent_name = excluded.agent_name,
                owner_name = excluded.owner_name,
                owner_type = excluded.owner_type,
                portfolio_size = excluded.portfolio_size,
                total_units = excluded.total_units,
                buildings = excluded.buildings,
                building_ids = excluded.building_ids,
                contacts = excluded.contacts,
                address = excluded.address,
                boro = excluded.boro,
                boros = excluded.boros,
                reg_status = excluded.reg_status,
                last_registration = excluded.last_registration,
                score = excluded.score,
                score_breakdown = excluded.score_breakdown,
                tags = excluded.tags,
                building_types = excluded.building_types,
                building_classes = excluded.building_classes,
                entity_type = excluded.entity_type,
                -- Enrichment fields: keep existing if richer (COALESCE)
                phone = COALESCE(leads.phone, excluded.phone),
                email = COALESCE(leads.email, excluded.email),
                website = COALESCE(leads.website, excluded.website),
                business_summary = COALESCE(leads.business_summary, excluded.business_summary),
                owner_principal = COALESCE(leads.owner_principal, excluded.owner_principal),
                dos_id = COALESCE(leads.dos_id, excluded.dos_id),
                dos_status = COALESCE(leads.dos_status, excluded.dos_status),
                company_name = COALESCE(leads.company_name, excluded.company_name),
                primary_contact = COALESCE(leads.primary_contact, excluded.primary_contact),
                primary_contact_title = COALESCE(leads.primary_contact_title, excluded.primary_contact_title),
                enrichment_status = CASE
                    WHEN leads.enrichment_status IN ('complete', 'partial') THEN leads.enrichment_status
                    ELSE COALESCE(excluded.enrichment_status, leads.enrichment_status)
                END,
                enrichment_sources = COALESCE(leads.enrichment_sources, excluded.enrichment_sources),
                last_enriched = COALESCE(leads.last_enriched, excluded.last_enriched),
                enrichment_retries = COALESCE(leads.enrichment_retries, excluded.enrichment_retries),
                -- User fields: NEVER overwrite if already set by user
                outreach_status = CASE
                    WHEN leads.outreach_status IS NOT NULL AND leads.outreach_status != 'new'
                    THEN leads.outreach_status
                    ELSE excluded.outreach_status
                END,
                notes = COALESCE(leads.notes, excluded.notes),
                opportunity_note = COALESCE(leads.opportunity_note, excluded.opportunity_note),
                pipeline_stage = CASE
                    WHEN leads.pipeline_stage IS NOT NULL AND leads.pipeline_stage != 'research'
                    THEN leads.pipeline_stage
                    ELSE excluded.pipeline_stage
                END,
                next_follow_up = COALESCE(leads.next_follow_up, excluded.next_follow_up),
                priority_rank = CASE
                    WHEN leads.priority_rank IS NOT NULL AND leads.priority_rank > 0
                    THEN leads.priority_rank
                    ELSE excluded.priority_rank
                END,
                -- Revenue/violation fields: keep existing if non-zero
                estimated_monthly_revenue = CASE
                    WHEN leads.estimated_monthly_revenue > 0 THEN leads.estimated_monthly_revenue
                    ELSE excluded.estimated_monthly_revenue
                END,
                estimated_annual_revenue = CASE
                    WHEN leads.estimated_annual_revenue > 0 THEN leads.estimated_annual_revenue
                    ELSE excluded.estimated_annual_revenue
                END,
                violation_count = CASE
                    WHEN leads.violation_count > 0 THEN leads.violation_count
                    ELSE excluded.violation_count
                END,
                violation_class_a = CASE WHEN leads.violation_class_a > 0 THEN leads.violation_class_a ELSE excluded.violation_class_a END,
                violation_class_b = CASE WHEN leads.violation_class_b > 0 THEN leads.violation_class_b ELSE excluded.violation_class_b END,
                violation_class_c = CASE WHEN leads.violation_class_c > 0 THEN leads.violation_class_c ELSE excluded.violation_class_c END,
                violations_per_unit = CASE WHEN leads.violations_per_unit > 0 THEN leads.violations_per_unit ELSE excluded.violations_per_unit END,
                -- Always update timestamp
                updated_at = excluded.updated_at
        """
        
        def _lead_to_tuple(lead):
            building_types_json = None
            if lead.building_types:
                building_types_json = json.dumps(lead.building_types.to_dict())
            return (
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
                getattr(lead, 'estimated_monthly_revenue', 0.0),
                getattr(lead, 'estimated_annual_revenue', 0.0),
                getattr(lead, 'violation_count', 0),
                getattr(lead, 'violation_class_a', 0),
                getattr(lead, 'violation_class_b', 0),
                getattr(lead, 'violation_class_c', 0),
                getattr(lead, 'violations_per_unit', 0.0),
                getattr(lead, 'pipeline_stage', 'research'),
                getattr(lead, 'next_follow_up', None),
                getattr(lead, 'priority_rank', 0),
                getattr(lead, 'enrichment_retries', 0),
                datetime.now().isoformat(),
            )
        
        BATCH_SIZE = 500
        with self._get_connection() as conn:
            for i in range(0, len(leads), BATCH_SIZE):
                batch = leads[i:i + BATCH_SIZE]
                conn.executemany(UPSERT_SQL, [_lead_to_tuple(l) for l in batch])
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
                    estimated_monthly_revenue=row_dict.get('estimated_monthly_revenue') or 0.0,
                    estimated_annual_revenue=row_dict.get('estimated_annual_revenue') or 0.0,
                    violation_count=row_dict.get('violation_count') or 0,
                    violation_class_a=row_dict.get('violation_class_a') or 0,
                    violation_class_b=row_dict.get('violation_class_b') or 0,
                    violation_class_c=row_dict.get('violation_class_c') or 0,
                    violations_per_unit=row_dict.get('violations_per_unit') or 0.0,
                    pipeline_stage=row_dict.get('pipeline_stage') or 'research',
                    next_follow_up=row_dict.get('next_follow_up'),
                    priority_rank=row_dict.get('priority_rank') or 0,
                    enrichment_retries=row_dict.get('enrichment_retries') or 0,
                    created_at=created_at,
                    updated_at=updated_at,
                )
                leads.append(lead)
            
            logger.info(f"Loaded {len(leads)} leads from database")
            return leads
    
    def get_leads_filtered(
        self,
        min_score: Optional[float] = None,
        max_score: Optional[float] = None,
        min_portfolio: Optional[int] = None,
        max_portfolio: Optional[int] = None,
        boro: Optional[str] = None,
        has_phone: Optional[bool] = None,
        has_email: Optional[bool] = None,
        has_website: Optional[bool] = None,
        entity_type: Optional[str] = None,
        min_units: Optional[int] = None,
        max_units: Optional[int] = None,
        min_units_per_bldg: Optional[float] = None,
        max_units_per_bldg: Optional[float] = None,
        building_type_has: Optional[str] = None,
        enrichment_status: Optional[str] = None,
        outreach_status: Optional[str] = None,
        pipeline_stage: Optional[str] = None,
        search: Optional[str] = None,
        sort_by: str = 'score',
        sort_dir: str = 'desc',
        limit: int = 50,
        offset: int = 0,
        # Agent-specific filters (Phase 1B)
        min_violations_per_unit: Optional[float] = None,
        max_violations_per_unit: Optional[float] = None,
        min_revenue: Optional[float] = None,
        max_revenue: Optional[float] = None,
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
        if max_score is not None:
            where_clauses.append("score <= ?")
            params.append(max_score)
        if min_portfolio is not None:
            where_clauses.append("portfolio_size >= ?")
            params.append(min_portfolio)
        if max_portfolio is not None:
            where_clauses.append("portfolio_size <= ?")
            params.append(max_portfolio)
        if boro is not None:
            # Support comma-separated boroughs for multi-select (e.g., "MANHATTAN,BROOKLYN")
            boro_list = [b.strip().upper() for b in boro.split(',') if b.strip()]
            if len(boro_list) == 1:
                where_clauses.append("(UPPER(boro) = UPPER(?) OR boros LIKE ?)")
                params.extend([boro_list[0], f'%"{boro_list[0]}"%'])
            elif len(boro_list) > 1:
                placeholders = ','.join(['?' for _ in boro_list])
                like_clauses = ' OR '.join(['boros LIKE ?' for _ in boro_list])
                where_clauses.append(f"(UPPER(boro) IN ({placeholders}) OR ({like_clauses}))")
                params.extend(boro_list)  # for IN clause
                params.extend([f'%"{b}"%' for b in boro_list])  # for LIKE clauses
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
            types = [t.strip() for t in entity_type.split(',') if t.strip()]
            if len(types) == 1:
                where_clauses.append("entity_type = ?")
                params.append(types[0])
            elif len(types) > 1:
                placeholders = ','.join('?' * len(types))
                where_clauses.append(f"entity_type IN ({placeholders})")
                params.extend(types)
        if min_units is not None:
            where_clauses.append("total_units >= ?")
            params.append(min_units)
        if max_units is not None:
            where_clauses.append("total_units <= ?")
            params.append(max_units)
        if min_units_per_bldg is not None:
            where_clauses.append("CASE WHEN portfolio_size > 0 THEN total_units * 1.0 / portfolio_size ELSE 0 END >= ?")
            params.append(min_units_per_bldg)
        if max_units_per_bldg is not None:
            where_clauses.append("CASE WHEN portfolio_size > 0 THEN total_units * 1.0 / portfolio_size ELSE 0 END <= ?")
            params.append(max_units_per_bldg)
        if building_type_has is not None:
            # Support comma-separated building types (e.g., "condo,coop")
            VALID_TYPES = {'condo', 'coop', 'rental_elevator', 'rental_walkup', 'small_residential'}
            types = [t.strip().lower() for t in building_type_has.split(',') if t.strip().lower() in VALID_TYPES]
            if types:
                type_clauses = [f"CAST(json_extract(building_types, '$.{t}') AS INTEGER) > 0" for t in types]
                where_clauses.append(f"({' OR '.join(type_clauses)})")
        if enrichment_status is not None:
            # Support comma-separated multi-select (e.g., "complete,partial")
            statuses = [s.strip() for s in enrichment_status.split(',') if s.strip()]
            if len(statuses) == 1:
                where_clauses.append("enrichment_status = ?")
                params.append(statuses[0])
            elif len(statuses) > 1:
                placeholders = ','.join('?' * len(statuses))
                where_clauses.append(f"enrichment_status IN ({placeholders})")
                params.extend(statuses)
        if outreach_status is not None:
            statuses = [s.strip() for s in outreach_status.split(',') if s.strip()]
            if len(statuses) == 1:
                where_clauses.append("outreach_status = ?")
                params.append(statuses[0])
            elif len(statuses) > 1:
                placeholders = ','.join('?' * len(statuses))
                where_clauses.append(f"outreach_status IN ({placeholders})")
                params.extend(statuses)
        if pipeline_stage is not None:
            stages = [s.strip() for s in pipeline_stage.split(',') if s.strip()]
            if len(stages) == 1:
                where_clauses.append("(pipeline_stage = ? OR (? = 'research' AND (pipeline_stage IS NULL OR pipeline_stage = '')))")
                params.extend([stages[0], stages[0]])
            elif len(stages) > 1:
                stage_clauses = []
                for s in stages:
                    if s == 'research':
                        stage_clauses.append("(pipeline_stage = 'research' OR pipeline_stage IS NULL OR pipeline_stage = '')")
                    else:
                        stage_clauses.append(f"pipeline_stage = ?")
                        params.append(s)
                where_clauses.append(f"({' OR '.join(stage_clauses)})")
        
        # Agent-specific filters
        if min_violations_per_unit is not None:
            where_clauses.append("violations_per_unit >= ?")
            params.append(min_violations_per_unit)
        if max_violations_per_unit is not None:
            where_clauses.append("violations_per_unit <= ?")
            params.append(max_violations_per_unit)
        if min_revenue is not None:
            where_clauses.append("estimated_annual_revenue >= ?")
            params.append(min_revenue)
        if max_revenue is not None:
            where_clauses.append("estimated_annual_revenue <= ?")
            params.append(max_revenue)
        
        # 2B: Full-text search across name/company/address fields
        if search is not None and search.strip():
            search_term = f"%{search.strip()}%"
            where_clauses.append(
                "(agent_name LIKE ? COLLATE NOCASE OR owner_name LIKE ? COLLATE NOCASE "
                "OR company_name LIKE ? COLLATE NOCASE OR primary_contact LIKE ? COLLATE NOCASE "
                "OR address LIKE ? COLLATE NOCASE)"
            )
            params.extend([search_term] * 5)
        
        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        
        # 2A: Server-side sorting with validated column names
        ALLOWED_SORT_COLS = {
            'score', 'portfolio_size', 'total_units', 'estimated_annual_revenue',
            'violations_per_unit', 'agent_name', 'boro', 'enrichment_status',
            'outreach_status', 'pipeline_stage', 'priority_rank', 'company_name',
            'units_per_bldg',
        }
        sort_col = sort_by if sort_by in ALLOWED_SORT_COLS else 'score'
        # Handle derived sort columns
        if sort_col == 'units_per_bldg':
            sort_col_sql = "CASE WHEN portfolio_size > 0 THEN total_units * 1.0 / portfolio_size ELSE 0 END"
        else:
            sort_col_sql = sort_col
        sort_direction = 'ASC' if sort_dir.lower() == 'asc' else 'DESC'
        order_sql = f"{sort_col_sql} {sort_direction}"
        # Secondary sort for stability
        if sort_col != 'score':
            order_sql += ", score DESC"
        
        with self._get_connection() as conn:
            # Get total count
            count_row = conn.execute(
                f"SELECT COUNT(*) FROM leads WHERE {where_sql}", params
            ).fetchone()
            total = count_row[0]
            
            # Get paginated results
            rows = conn.execute(
                f"SELECT * FROM leads WHERE {where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?",
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
            
            # Pipeline stage distribution
            by_pipeline_stage = {}
            for row in conn.execute(
                "SELECT COALESCE(pipeline_stage, 'research') as stage, COUNT(*) as cnt FROM leads GROUP BY stage"
            ):
                by_pipeline_stage[row['stage']] = row['cnt']
            
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
                "by_pipeline_stage": by_pipeline_stage,
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
    
    # get_stats() removed — superseded by get_stats_sql() which queries the leads table directly
    
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
    
    # get_all_outreach_attempts() removed — never called from any endpoint or internal code


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
    
    # === DOS Cache (Phase 2.7c) ===
    
    # R11: Cache TTL — entries older than this are treated as misses
    CACHE_TTL_DAYS = 90
    
    def _is_cache_fresh(self, cached_at_str: Optional[str]) -> bool:
        """Check if a cached_at timestamp is within TTL."""
        if not cached_at_str:
            return False
        try:
            cached_at = datetime.fromisoformat(cached_at_str)
            age_days = (datetime.now() - cached_at).days
            return age_days <= self.CACHE_TTL_DAYS
        except (ValueError, TypeError):
            return False
    
    def get_dos_cache(self, cache_key: str) -> Optional[Dict]:
        """Get cached NY DOS lookup result (respects 90-day TTL)."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT result, cached_at FROM dos_cache WHERE cache_key = ?",
                (cache_key,)
            ).fetchone()
            if row:
                if not self._is_cache_fresh(row['cached_at']):
                    logger.debug(f"DOS cache expired for {cache_key}")
                    return None  # Treat as cache miss
                return json.loads(row['result']) if row['result'] else None
            return None  # Cache miss (returns None - differentiate from cached "not found")
    
    def has_dos_cache(self, cache_key: str) -> bool:
        """Check if a fresh DOS cache entry exists (even if result is null = not found)."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT cached_at FROM dos_cache WHERE cache_key = ?",
                (cache_key,)
            ).fetchone()
            if row:
                return self._is_cache_fresh(row['cached_at'])
            return False
    
    def set_dos_cache(self, cache_key: str, result: Optional[Dict]):
        """Cache a NY DOS lookup result (can be None for 'not found')."""
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO dos_cache (cache_key, result, cached_at) VALUES (?, ?, ?)",
                (cache_key, json.dumps(result) if result else None, datetime.now().isoformat())
            )
            conn.commit()
    
    # === Google Places Cache (Phase 2.7d) ===
    
    def get_places_cache(self, cache_key: str) -> Optional[Dict]:
        """Get cached Google Places result (respects 90-day TTL)."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT result, cached_at FROM places_cache WHERE cache_key = ?",
                (cache_key,)
            ).fetchone()
            if row:
                if not self._is_cache_fresh(row['cached_at']):
                    logger.debug(f"Places cache expired for {cache_key}")
                    return None
                return json.loads(row['result']) if row['result'] else None
            return None
    
    def has_places_cache(self, cache_key: str) -> bool:
        """Check if a fresh Places cache entry exists."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT cached_at FROM places_cache WHERE cache_key = ?",
                (cache_key,)
            ).fetchone()
            if row:
                return self._is_cache_fresh(row['cached_at'])
            return False
    
    def set_places_cache(self, cache_key: str, result: Optional[Dict]):
        """Cache a Google Places result."""
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO places_cache (cache_key, result, cached_at) VALUES (?, ?, ?)",
                (cache_key, json.dumps(result) if result else None, datetime.now().isoformat())
            )
            conn.commit()
    
    # === Outreach Events (Phase 5.3) ===
    
    def add_outreach_event(self, lead_id: str, event: Dict) -> int:
        """Add an outreach event and return its ID."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO outreach_events (lead_id, stage, method, outcome, notes, next_follow_up, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (lead_id, event['stage'], event.get('method'), event.get('outcome'),
                 event.get('notes'), event.get('next_follow_up'),
                 event.get('timestamp', datetime.now().isoformat()))
            )
            conn.commit()
            return cursor.lastrowid
    
    def get_outreach_events(self, lead_id: str) -> List[Dict]:
        """Get all outreach events for a lead, most recent first."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """SELECT id, stage, method, outcome, notes, next_follow_up, timestamp
                   FROM outreach_events WHERE lead_id = ? ORDER BY timestamp DESC""",
                (lead_id,)
            ).fetchall()
            return [dict(row) for row in rows]
    
    def get_follow_ups_due(self, before_date: Optional[str] = None) -> List[Dict]:
        """Get leads with follow-ups due on or before the given date."""
        if before_date is None:
            before_date = datetime.now().strftime("%Y-%m-%d")
        with self._get_connection() as conn:
            rows = conn.execute(
                """SELECT l.lead_id, l.agent_name, l.company_name, l.phone, l.email,
                          l.pipeline_stage, l.next_follow_up, l.priority_rank, l.score, l.portfolio_size
                   FROM leads l
                   WHERE l.next_follow_up IS NOT NULL AND l.next_follow_up <= ?
                   ORDER BY l.priority_rank DESC, l.next_follow_up ASC""",
                (before_date,)
            ).fetchall()
            return [dict(row) for row in rows]
    
    # === Change Alerts (Phase 5.4) ===
    
    def add_change_alert(self, alert_type: str, description: str, lead_id: Optional[str] = None, details: Optional[Dict] = None):
        """Add a change alert."""
        with self._get_connection() as conn:
            conn.execute(
                """INSERT INTO change_alerts (alert_type, lead_id, description, details)
                   VALUES (?, ?, ?, ?)""",
                (alert_type, lead_id, description, json.dumps(details) if details else None)
            )
            conn.commit()
    
    def get_change_alerts(self, limit: int = 50, include_dismissed: bool = False) -> List[Dict]:
        """Get recent change alerts."""
        with self._get_connection() as conn:
            where = "" if include_dismissed else "WHERE dismissed = 0"
            rows = conn.execute(
                f"""SELECT id, alert_type, lead_id, description, details, created_at, dismissed
                    FROM change_alerts {where}
                    ORDER BY created_at DESC LIMIT ?""",
                (limit,)
            ).fetchall()
            result = []
            for row in rows:
                d = dict(row)
                if d.get('details'):
                    d['details'] = json.loads(d['details'])
                result.append(d)
            return result
    
    def dismiss_alert(self, alert_id: int):
        """Dismiss a change alert."""
        with self._get_connection() as conn:
            conn.execute("UPDATE change_alerts SET dismissed = 1 WHERE id = ?", (alert_id,))
            conn.commit()
    
    # === Lead by ID from DB (Phase 4.2) ===
    
    def get_lead_by_id(self, lead_id: str) -> Optional[Dict]:
        """Get a single lead by ID directly from DB."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM leads WHERE lead_id = ?", (lead_id,)).fetchone()
            return dict(row) if row else None
    
    def increment_enrichment_retries(self, lead_id: str) -> int:
        """Increment enrichment retry count and return new value."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE leads SET enrichment_retries = COALESCE(enrichment_retries, 0) + 1 WHERE lead_id = ?",
                (lead_id,)
            )
            conn.commit()
            row = conn.execute(
                "SELECT enrichment_retries FROM leads WHERE lead_id = ?", (lead_id,)
            ).fetchone()
            return row[0] if row else 0
    
    # === AI Summary Cache (R11) ===
    
    def get_ai_summary_cache(self, lead_id: str) -> Optional[str]:
        """Get cached AI summary for a lead (respects 90-day TTL)."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT summary, cached_at FROM ai_summary_cache WHERE lead_id = ?",
                (lead_id,)
            ).fetchone()
            if row and self._is_cache_fresh(row['cached_at']):
                return row['summary']
            return None
    
    def set_ai_summary_cache(self, lead_id: str, summary: str, model: str = ""):
        """Cache an AI summary for a lead."""
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ai_summary_cache (lead_id, summary, model, cached_at) VALUES (?, ?, ?, ?)",
                (lead_id, summary, model, datetime.now().isoformat())
            )
            conn.commit()
    
    # === User Management (Auth) ===
    
    def create_user(self, user_id: str, email: str, password_hash: str, role: str = "user") -> bool:
        """Create a new user. Returns False if email already exists."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT INTO users (user_id, email, password_hash, role) VALUES (?, ?, ?, ?)",
                    (user_id, email.lower().strip(), password_hash, role),
                )
                conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
    
    def get_user_by_email(self, email: str) -> Optional[Dict]:
        """Get user by email address."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
            ).fetchone()
            return dict(row) if row else None
    
    def get_user_by_id(self, user_id: str) -> Optional[Dict]:
        """Get user by ID."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            return dict(row) if row else None
    
    def update_user_password(self, user_id: str, password_hash: str) -> bool:
        """Update a user's password hash."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE users SET password_hash = ? WHERE user_id = ?",
                (password_hash, user_id),
            )
            conn.commit()
            return cursor.rowcount > 0
    
    def update_user_last_login(self, user_id: str):
        """Record the user's last login timestamp."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE users SET last_login = ? WHERE user_id = ?",
                (datetime.now().isoformat(), user_id),
            )
            conn.commit()
    
    def list_users(self) -> List[Dict]:
        """List all users (without password hashes)."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT user_id, email, role, created_at, last_login FROM users ORDER BY created_at"
            ).fetchall()
            return [dict(r) for r in rows]
    
    # === Building Registry (Data Robustness v2) ===
    
    def save_buildings(self, buildings: List[Dict], refresh_id: str = None) -> int:
        """
        Bulk upsert buildings into the building_registry table.
        
        Args:
            buildings: List of dicts with building_id, lead_id, address, etc.
            refresh_id: Optional refresh ID for provenance tracking
            
        Returns:
            Number of buildings saved
        """
        if not buildings:
            return 0
        
        now = datetime.now().isoformat()
        BATCH_SIZE = 500
        total = 0
        
        with self._get_connection() as conn:
            for i in range(0, len(buildings), BATCH_SIZE):
                batch = buildings[i:i + BATCH_SIZE]
                conn.executemany(
                    """INSERT INTO building_registry (
                        building_id, registration_id, lead_id, address,
                        house_number, street_name, boro, zip_code,
                        block, lot, bbl, units_res,
                        building_class, building_type, last_registration,
                        status, last_seen_in_hpd, source_refresh_id, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                    ON CONFLICT(building_id) DO UPDATE SET
                        registration_id = excluded.registration_id,
                        lead_id = excluded.lead_id,
                        address = excluded.address,
                        house_number = excluded.house_number,
                        street_name = excluded.street_name,
                        boro = excluded.boro,
                        zip_code = excluded.zip_code,
                        block = excluded.block,
                        lot = excluded.lot,
                        bbl = excluded.bbl,
                        units_res = excluded.units_res,
                        building_class = excluded.building_class,
                        building_type = excluded.building_type,
                        last_registration = excluded.last_registration,
                        status = 'active',
                        last_seen_in_hpd = excluded.last_seen_in_hpd,
                        source_refresh_id = excluded.source_refresh_id,
                        updated_at = excluded.updated_at
                    """,
                    [
                        (
                            b.get('building_id', ''),
                            b.get('registration_id', ''),
                            b.get('lead_id', ''),
                            b.get('address', ''),
                            b.get('house_number', ''),
                            b.get('street_name', ''),
                            b.get('boro', ''),
                            b.get('zip_code', ''),
                            b.get('block', ''),
                            b.get('lot', ''),
                            b.get('bbl', ''),
                            b.get('units_res', 0),
                            b.get('building_class', ''),
                            b.get('building_type', ''),
                            b.get('last_registration', ''),
                            now,
                            refresh_id or '',
                            now,
                        )
                        for b in batch
                    ]
                )
                total += len(batch)
            conn.commit()
        
        logger.info(f"Saved {total} buildings to building_registry")
        return total
    
    def search_buildings_by_address(self, address: str, limit: int = 20) -> List[Dict]:
        """
        Search building_registry by address with fuzzy matching.
        
        Parses input into house_number + street_name components.
        Returns buildings with their linked lead info.
        """
        if not address or not address.strip():
            return []
        
        address = address.strip().upper()
        
        # Try to split into house number and street name
        parts = address.split(None, 1)
        house_number = None
        street_name = address
        
        if len(parts) >= 2 and parts[0].isdigit():
            house_number = parts[0]
            street_name = parts[1]
        
        with self._get_connection() as conn:
            if house_number:
                # Exact house number + fuzzy street name
                rows = conn.execute(
                    """SELECT br.*, l.agent_name, l.owner_name, l.score, l.portfolio_size, l.total_units
                       FROM building_registry br
                       LEFT JOIN leads l ON br.lead_id = l.lead_id
                       WHERE br.house_number = ? AND br.street_name LIKE ?
                       ORDER BY l.score DESC
                       LIMIT ?""",
                    (house_number, f"%{street_name}%", limit)
                ).fetchall()
            else:
                # Just street name search
                rows = conn.execute(
                    """SELECT br.*, l.agent_name, l.owner_name, l.score, l.portfolio_size, l.total_units
                       FROM building_registry br
                       LEFT JOIN leads l ON br.lead_id = l.lead_id
                       WHERE br.street_name LIKE ? OR br.address LIKE ?
                       ORDER BY l.score DESC
                       LIMIT ?""",
                    (f"%{street_name}%", f"%{address}%", limit)
                ).fetchall()
            
            return [dict(row) for row in rows]
    
    def mark_stale_buildings(self, active_building_ids: set, refresh_id: str = None) -> int:
        """
        Mark buildings not in the active set as 'stale'.
        Called after a full refresh to detect buildings that disappeared from HPD.
        
        Returns number of buildings marked stale.
        """
        if not active_building_ids:
            return 0
        
        with self._get_connection() as conn:
            # Mark all buildings NOT in the active set as stale
            placeholders = ','.join('?' * min(len(active_building_ids), 999))
            # Process in chunks due to SQLite variable limit
            stale_count = 0
            active_list = list(active_building_ids)
            
            # First, mark everything stale
            conn.execute("UPDATE building_registry SET status = 'stale' WHERE status = 'active'")
            
            # Then mark active ones back
            CHUNK = 999
            for i in range(0, len(active_list), CHUNK):
                chunk = active_list[i:i + CHUNK]
                placeholders = ','.join('?' * len(chunk))
                conn.execute(
                    f"UPDATE building_registry SET status = 'active' WHERE building_id IN ({placeholders})",
                    chunk
                )
            
            stale_count = conn.execute("SELECT COUNT(*) FROM building_registry WHERE status = 'stale'").fetchone()[0]
            conn.commit()
            
            if stale_count > 0:
                logger.warning(f"Marked {stale_count} buildings as stale (not found in latest HPD pull)")
            
            return stale_count
    
    def update_lead_staleness(self) -> Dict[str, int]:
        """
        Update data_staleness on leads based on their buildings' status in building_registry.
        Returns counts by staleness category.
        """
        with self._get_connection() as conn:
            # Leads where ALL buildings are stale = 'expired'
            conn.execute("""
                UPDATE leads SET data_staleness = 'expired'
                WHERE lead_id IN (
                    SELECT br.lead_id FROM building_registry br
                    GROUP BY br.lead_id
                    HAVING COUNT(*) = SUM(CASE WHEN br.status = 'stale' THEN 1 ELSE 0 END)
                    AND COUNT(*) > 0
                )
            """)
            
            # Leads where SOME buildings are stale = 'partially_stale'
            conn.execute("""
                UPDATE leads SET data_staleness = 'partially_stale'
                WHERE lead_id IN (
                    SELECT br.lead_id FROM building_registry br
                    GROUP BY br.lead_id
                    HAVING SUM(CASE WHEN br.status = 'stale' THEN 1 ELSE 0 END) > 0
                    AND SUM(CASE WHEN br.status = 'stale' THEN 1 ELSE 0 END) < COUNT(*)
                )
            """)
            
            # All others = 'current'
            conn.execute("""
                UPDATE leads SET data_staleness = 'current'
                WHERE data_staleness IS NULL OR data_staleness NOT IN ('expired', 'partially_stale')
                OR lead_id IN (
                    SELECT br.lead_id FROM building_registry br
                    GROUP BY br.lead_id
                    HAVING SUM(CASE WHEN br.status = 'stale' THEN 1 ELSE 0 END) = 0
                )
            """)
            
            conn.commit()
            
            counts = {}
            for row in conn.execute("SELECT data_staleness, COUNT(*) FROM leads GROUP BY data_staleness"):
                counts[row[0] or 'current'] = row[1]
            
            return counts
    
    def count_buildings_in_registry(self) -> int:
        """Count total buildings in the registry."""
        with self._get_connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM building_registry").fetchone()[0]
    
    def count_stale_buildings(self) -> int:
        """Count stale buildings in the registry."""
        with self._get_connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM building_registry WHERE status = 'stale'").fetchone()[0]
    
    # === Refresh Audit Log (Data Robustness v2) ===
    
    def create_audit_log(self, refresh_id: str, config: dict = None) -> int:
        """Start a new audit log entry. Returns the row ID."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO refresh_audit_log (refresh_id, started_at, status, config_json, leads_before_refresh)
                   VALUES (?, ?, 'running', ?, ?)""",
                (
                    refresh_id,
                    datetime.now().isoformat(),
                    json.dumps(config) if config else None,
                    conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0],
                )
            )
            conn.commit()
            return cursor.lastrowid
    
    def update_audit_log(self, audit_id: int, updates: Dict):
        """Update an audit log entry with pipeline stage counts."""
        if not updates:
            return
        
        set_clauses = []
        params = []
        for key, val in updates.items():
            set_clauses.append(f"{key} = ?")
            params.append(val)
        params.append(audit_id)
        
        with self._get_connection() as conn:
            conn.execute(
                f"UPDATE refresh_audit_log SET {', '.join(set_clauses)} WHERE id = ?",
                params
            )
            conn.commit()
    
    def finish_audit_log(self, audit_id: int, status: str = 'success', error: str = None):
        """Finalize an audit log entry with post-refresh counts."""
        with self._get_connection() as conn:
            leads_after = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
            leads_before = conn.execute(
                "SELECT leads_before_refresh FROM refresh_audit_log WHERE id = ?", (audit_id,)
            ).fetchone()[0] or 0
            
            conn.execute(
                """UPDATE refresh_audit_log SET
                    finished_at = ?,
                    status = ?,
                    leads_after_refresh = ?,
                    leads_net_change = ?,
                    error_message = ?
                   WHERE id = ?""",
                (
                    datetime.now().isoformat(),
                    status,
                    leads_after,
                    leads_after - leads_before,
                    error,
                    audit_id,
                )
            )
            conn.commit()
            
            net_change = leads_after - leads_before
            if net_change < 0 and abs(net_change) > leads_before * 0.05:
                logger.warning(
                    f"DATA REGRESSION: Refresh caused net loss of {abs(net_change)} leads "
                    f"({leads_before} -> {leads_after}). This may indicate a problem."
                )
    
    def get_last_audit_log(self) -> Optional[Dict]:
        """Get the most recent audit log entry."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM refresh_audit_log ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None
    
    # === Stats Cache (Data Robustness v2) ===
    
    def get_cached_stats(self, key: str, max_age_seconds: int = 300) -> Optional[Dict]:
        """Get cached stats if fresh enough."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT value, updated_at FROM stats_cache WHERE key = ?", (key,)
            ).fetchone()
            if row:
                try:
                    updated_at = datetime.fromisoformat(row['updated_at'])
                    age = (datetime.now() - updated_at).total_seconds()
                    if age < max_age_seconds:
                        return json.loads(row['value'])
                except (ValueError, TypeError):
                    pass
            return None
    
    def set_cached_stats(self, key: str, value: Dict):
        """Set/update cached stats."""
        with self._get_connection() as conn:
            conn.execute(
                """INSERT INTO stats_cache (key, value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                (key, json.dumps(value), datetime.now().isoformat())
            )
            conn.commit()
    
    def invalidate_stats_cache(self):
        """Invalidate all cached stats (call after data changes)."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM stats_cache")
            conn.commit()
    
    # === Database Backup ===
    
    def backup(self, backup_path: Optional[str] = None) -> str:
        """Create a backup of the database file. Returns the backup file path."""
        import shutil
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = self.db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        dest = Path(backup_path) if backup_path else backup_dir / f"leads_{ts}.db"
        shutil.copy2(self.db_path, dest)
        logger.info(f"Database backed up to {dest}")
        return str(dest)


# Singleton instance
_db_instance: Optional[LeadsDatabase] = None


def get_database() -> LeadsDatabase:
    """Get the database singleton."""
    global _db_instance
    if _db_instance is None:
        _db_instance = LeadsDatabase()
    return _db_instance
