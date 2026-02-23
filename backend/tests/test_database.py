"""
Tests for the database module.
Covers: table creation, lead CRUD, enrichment cache, backups.
"""
from pathlib import Path


class TestDatabaseInit:
    def test_tables_created(self, db):
        """All expected tables should exist after init."""
        with db._get_connection() as conn:
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ]
        expected = [
            "leads", "lead_user_data", "enrichment_cache", "outreach_attempts",
            "enrichment_jobs", "dos_cache", "places_cache", "change_alerts",
            "outreach_events", "app_settings", "ai_summary_cache",
            "agent_conversations", "agent_messages", "agent_pending_actions",
            "users", "schema_version",
        ]
        for table in expected:
            assert table in tables, f"Missing table: {table}"


class TestLeadCRUD:
    def test_save_and_load_lead(self, db):
        """Save a lead via raw insert and read it back."""
        with db._get_connection() as conn:
            conn.execute(
                "INSERT INTO leads (lead_id, agent_name, owner_name, portfolio_size, total_units, score, boro) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("test-lead-1", "Test Agent", "Test Owner", 5, 100, 85.5, "MANHATTAN"),
            )
            conn.commit()
        
        lead = db.get_lead_by_id("test-lead-1")
        assert lead is not None
        assert lead["agent_name"] == "Test Agent"
        assert lead["portfolio_size"] == 5
        assert lead["score"] == 85.5

    def test_update_lead(self, db):
        """Update lead fields via db.update_lead."""
        with db._get_connection() as conn:
            conn.execute(
                "INSERT INTO leads (lead_id, agent_name, score, boro) VALUES (?, ?, ?, ?)",
                ("upd-1", "Agent", 50.0, "QUEENS"),
            )
            conn.commit()
        
        db.update_lead("upd-1", {
            "score": 95.0,
            "pipeline_stage": "first_contact",
            "notes": "Very promising lead",
        })
        
        lead = db.get_lead_by_id("upd-1")
        assert lead["score"] == 95.0
        assert lead["pipeline_stage"] == "first_contact"
        assert lead["notes"] == "Very promising lead"

    def test_get_leads_count(self, db):
        with db._get_connection() as conn:
            for i in range(5):
                conn.execute(
                    "INSERT INTO leads (lead_id, boro) VALUES (?, ?)",
                    (f"cnt-{i}", "BRONX"),
                )
            conn.commit()
        assert db.get_leads_count() == 5


class TestEnrichmentCache:
    def test_save_and_get_enrichment(self, db):
        db.save_enrichment(
            lead_id="enr-1",
            phone="212-555-1234",
            email="test@company.com",
            website="https://company.com",
            business_summary="A property management firm",
            owner_principal="John Doe",
            enrichment_status="complete",
            enrichment_sources=["google_places", "web_crawl"],
        )
        
        cached = db.get_enrichment("enr-1")
        assert cached is not None
        assert cached["phone"] == "212-555-1234"
        assert cached["email"] == "test@company.com"
        assert cached["enrichment_status"] == "complete"


class TestAppSettings:
    def test_set_and_get_setting(self, db):
        db.set_setting("test_key", "test_value")
        assert db.get_setting("test_key") == "test_value"

    def test_missing_setting_returns_none(self, db):
        assert db.get_setting("nonexistent") is None


class TestDatabaseBackup:
    def test_backup_creates_file(self, db):
        # Insert some data first
        with db._get_connection() as conn:
            conn.execute(
                "INSERT INTO leads (lead_id, boro) VALUES (?, ?)",
                ("bak-1", "BROOKLYN"),
            )
            conn.commit()
        
        backup_path = db.backup()
        assert Path(backup_path).exists()
        assert Path(backup_path).stat().st_size > 0
        # Cleanup
        Path(backup_path).unlink(missing_ok=True)
