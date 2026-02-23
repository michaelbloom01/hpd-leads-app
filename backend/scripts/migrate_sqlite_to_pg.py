"""Migrate data from SQLite to PostgreSQL.

Reads the existing SQLite database and transforms data into the new
PostgreSQL schema. Idempotent: safe to run multiple times.

Key transformations:
- leads table: JSON text fields -> JSONB, type coercion for nulls
- buildings: extracted from leads.buildings JSON + HPD building_ids
- building_management: temporal records linking buildings to leads
- users: preserved with existing password hashes
- enrichment/outreach/cache tables: direct copy with type fixes

Usage:
    python scripts/migrate_sqlite_to_pg.py [--sqlite-path PATH] [--pg-url URL]
"""
import argparse
import hashlib
import json
import logging
import os
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.models import Base

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_SQLITE = Path(__file__).parent.parent / "data" / "leads.db"
DEFAULT_PG_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/hpd_leads",
)


def normalize_pg_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://") and "+psycopg" not in url and "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    for async_driver in ("+asyncpg", "+aiopg"):
        url = url.replace(async_driver, "+psycopg")
    return url


def safe_str(val) -> str | None:
    """Convert SQLite value to string, handling 'None' literals and empty strings."""
    if val is None:
        return None
    s = str(val).strip()
    if s.lower() in ("none", "null", ""):
        return None
    return s


def safe_int(val) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def safe_json(val) -> dict | list | None:
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return None


def safe_datetime(val) -> datetime | None:
    if val is None:
        return None
    s = str(val).strip()
    if s.lower() in ("none", "null", ""):
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:26], fmt)
        except (ValueError, TypeError):
            continue
    return None


def safe_date(val) -> date | None:
    dt = safe_datetime(val)
    return dt.date() if dt else None


def compute_bbl(boro_id: str, block: str, lot: str) -> str | None:
    """Compute 10-digit BBL from borough ID, block, and lot."""
    if not boro_id or not block or not lot:
        return None
    try:
        b = str(int(boro_id))
        bl = str(int(block)).zfill(5)
        lt = str(int(lot)).zfill(4)
        return f"{b}{bl}{lt}"
    except (ValueError, TypeError):
        return None


def migrate(sqlite_path: Path, pg_url: str, dry_run: bool = False):
    if not sqlite_path.exists():
        logger.error(f"SQLite database not found: {sqlite_path}")
        sys.exit(1)

    pg_url = normalize_pg_url(pg_url)
    logger.info(f"Source: {sqlite_path}")
    logger.info(f"Target: {pg_url.split('@')[1] if '@' in pg_url else pg_url}")

    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row

    pg_engine = create_engine(pg_url, echo=False)

    with Session(pg_engine) as pg:
        # ── 1. Migrate leads ──
        logger.info("Migrating leads...")
        rows = sqlite_conn.execute("SELECT * FROM leads ORDER BY score DESC").fetchall()
        lead_count = 0
        for row in rows:
            d = dict(row)
            lead_id = d.get("lead_id")
            if not lead_id:
                continue

            pg.execute(
                text("""
                    INSERT INTO leads (
                        lead_id, normalized_name, agent_name, owner_name, owner_type,
                        company_name, entity_type, primary_contact, primary_contact_title,
                        address, primary_borough, portfolio_size, total_units,
                        score, score_breakdown, phone, email, website,
                        business_summary, owner_principal, enrichment_status,
                        enrichment_sources, last_enriched, enrichment_retries,
                        dos_id, dos_status, pipeline_stage, next_follow_up,
                        priority_rank, outreach_status, notes, tags, opportunity_note,
                        estimated_monthly_revenue, estimated_annual_revenue,
                        violation_count, violation_class_a, violation_class_b,
                        violation_class_c, violations_per_unit,
                        buildings, building_ids, contacts, boros,
                        building_types, building_classes,
                        created_at, updated_at
                    ) VALUES (
                        :lead_id, :normalized_name, :agent_name, :owner_name, :owner_type,
                        :company_name, :entity_type, :primary_contact, :primary_contact_title,
                        :address, :primary_borough, :portfolio_size, :total_units,
                        :score, :score_breakdown, :phone, :email, :website,
                        :business_summary, :owner_principal, :enrichment_status,
                        :enrichment_sources, :last_enriched, :enrichment_retries,
                        :dos_id, :dos_status, :pipeline_stage, :next_follow_up,
                        :priority_rank, :outreach_status, :notes, :tags, :opportunity_note,
                        :estimated_monthly_revenue, :estimated_annual_revenue,
                        :violation_count, :violation_class_a, :violation_class_b,
                        :violation_class_c, :violations_per_unit,
                        :buildings, :building_ids, :contacts, :boros,
                        :building_types, :building_classes,
                        :created_at, :updated_at
                    ) ON CONFLICT (lead_id) DO NOTHING
                """),
                {
                    "lead_id": lead_id,
                    "normalized_name": safe_str(d.get("normalized_name")),
                    "agent_name": safe_str(d.get("agent_name")),
                    "owner_name": safe_str(d.get("owner_name")),
                    "owner_type": safe_str(d.get("owner_type")),
                    "company_name": safe_str(d.get("company_name")),
                    "entity_type": safe_str(d.get("entity_type")) or "unknown",
                    "primary_contact": safe_str(d.get("primary_contact")),
                    "primary_contact_title": safe_str(d.get("primary_contact_title")),
                    "address": safe_str(d.get("address")),
                    "primary_borough": safe_str(d.get("boro")),
                    "portfolio_size": safe_int(d.get("portfolio_size")) or 0,
                    "total_units": safe_int(d.get("total_units")) or 0,
                    "score": safe_float(d.get("score")) or 0.0,
                    "score_breakdown": json.dumps(safe_json(d.get("score_breakdown")) or {}),
                    "phone": safe_str(d.get("phone")),
                    "email": safe_str(d.get("email")),
                    "website": safe_str(d.get("website")),
                    "business_summary": safe_str(d.get("business_summary")),
                    "owner_principal": safe_str(d.get("owner_principal")),
                    "enrichment_status": safe_str(d.get("enrichment_status")) or "none",
                    "enrichment_sources": json.dumps(safe_json(d.get("enrichment_sources")) or []),
                    "last_enriched": safe_datetime(d.get("last_enriched")),
                    "enrichment_retries": safe_int(d.get("enrichment_retries")) or 0,
                    "dos_id": safe_str(d.get("dos_id")),
                    "dos_status": safe_str(d.get("dos_status")),
                    "pipeline_stage": safe_str(d.get("pipeline_stage")) or "research",
                    "next_follow_up": safe_date(d.get("next_follow_up")),
                    "priority_rank": safe_int(d.get("priority_rank")) or 0,
                    "outreach_status": safe_str(d.get("outreach_status")) or "new",
                    "notes": safe_str(d.get("notes")),
                    "tags": json.dumps(safe_json(d.get("tags")) or []),
                    "opportunity_note": safe_str(d.get("opportunity_note")),
                    "estimated_monthly_revenue": safe_float(d.get("estimated_monthly_revenue")),
                    "estimated_annual_revenue": safe_float(d.get("estimated_annual_revenue")),
                    "violation_count": safe_int(d.get("violation_count")) or 0,
                    "violation_class_a": safe_int(d.get("violation_class_a")) or 0,
                    "violation_class_b": safe_int(d.get("violation_class_b")) or 0,
                    "violation_class_c": safe_int(d.get("violation_class_c")) or 0,
                    "violations_per_unit": safe_float(d.get("violations_per_unit")) or 0.0,
                    "buildings": json.dumps(safe_json(d.get("buildings")) or []),
                    "building_ids": json.dumps(safe_json(d.get("building_ids")) or []),
                    "contacts": json.dumps(safe_json(d.get("contacts")) or []),
                    "boros": json.dumps(safe_json(d.get("boros")) or []),
                    "building_types": json.dumps(safe_json(d.get("building_types")) or {}),
                    "building_classes": json.dumps(safe_json(d.get("building_classes")) or {}),
                    "created_at": safe_datetime(d.get("created_at")) or datetime.now(),
                    "updated_at": safe_datetime(d.get("updated_at")) or datetime.now(),
                },
            )
            lead_count += 1

        logger.info(f"  Migrated {lead_count} leads")

        # ── 2. Migrate users ──
        logger.info("Migrating users...")
        try:
            user_rows = sqlite_conn.execute("SELECT * FROM users").fetchall()
            user_count = 0
            for row in user_rows:
                d = dict(row)
                pg.execute(
                    text("""
                        INSERT INTO users (user_id, email, password_hash, role, last_login, created_at, updated_at)
                        VALUES (:user_id, :email, :password_hash, :role, :last_login, :created_at, now())
                        ON CONFLICT (user_id) DO NOTHING
                    """),
                    {
                        "user_id": d["user_id"],
                        "email": d["email"],
                        "password_hash": d["password_hash"],
                        "role": d.get("role", "user"),
                        "last_login": safe_datetime(d.get("last_login")),
                        "created_at": safe_datetime(d.get("created_at")) or datetime.now(),
                    },
                )
                user_count += 1
            logger.info(f"  Migrated {user_count} users")
        except sqlite3.OperationalError:
            logger.info("  No users table in SQLite, skipping")

        # ── 3. Migrate legacy tables (enrichment_cache, outreach, etc.) ──
        _migrate_simple_table(sqlite_conn, pg, "lead_user_data", [
            "lead_id", "outreach_status", "notes", "created_at", "updated_at",
        ])
        _migrate_simple_table(sqlite_conn, pg, "enrichment_cache", [
            "lead_id", "phone", "email", "website", "business_summary",
            "owner_principal", "enrichment_status", "enrichment_sources",
            "last_enriched",
        ])
        _migrate_simple_table(sqlite_conn, pg, "outreach_attempts", [
            "id", "lead_id", "method", "outcome", "notes", "timestamp",
        ])
        _migrate_simple_table(sqlite_conn, pg, "dos_cache", [
            "cache_key", "result", "cached_at",
        ])
        _migrate_simple_table(sqlite_conn, pg, "places_cache", [
            "cache_key", "result", "cached_at",
        ])
        _migrate_simple_table(sqlite_conn, pg, "ai_summary_cache", [
            "lead_id", "summary", "model", "cached_at",
        ])
        _migrate_simple_table(sqlite_conn, pg, "app_settings", [
            "key", "value", "updated_at",
        ])
        _migrate_simple_table(sqlite_conn, pg, "change_alerts", [
            "id", "alert_type", "lead_id", "description", "details",
            "created_at",
        ], json_fields=["details"])
        _migrate_simple_table(sqlite_conn, pg, "outreach_events", [
            "id", "lead_id", "stage", "method", "outcome", "notes",
            "next_follow_up", "timestamp",
        ], rename={"timestamp": "event_timestamp"})
        _migrate_simple_table(sqlite_conn, pg, "enrichment_jobs", [
            "id", "job_type", "status", "total_leads", "processed",
            "completed", "failed", "dos_found", "web_found", "current_lead",
            "lead_ids_remaining", "config", "error", "started_at",
            "finished_at", "updated_at",
        ])
        _migrate_simple_table(sqlite_conn, pg, "agent_conversations", [
            "conversation_id", "title", "created_at", "updated_at",
        ])
        _migrate_simple_table(sqlite_conn, pg, "agent_messages", [
            "message_id", "conversation_id", "role", "content",
            "structured_data", "claude_messages", "created_at",
        ])
        _migrate_simple_table(sqlite_conn, pg, "agent_pending_actions", [
            "action_id", "conversation_id", "tool_name", "tool_input",
            "description", "created_at",
        ])

        if not dry_run:
            pg.commit()
            logger.info("Migration committed.")
        else:
            pg.rollback()
            logger.info("DRY RUN -- rolled back.")

    # ── Verify ──
    _verify(sqlite_conn, pg_engine)
    sqlite_conn.close()


def _migrate_simple_table(
    sqlite_conn, pg: Session, table: str, columns: list[str],
    json_fields: list[str] | None = None,
    rename: dict[str, str] | None = None,
):
    """Copy a simple table from SQLite to PG. Idempotent via ON CONFLICT DO NOTHING."""
    json_fields = json_fields or []
    rename = rename or {}
    try:
        rows = sqlite_conn.execute(f"SELECT * FROM {table}").fetchall()
    except sqlite3.OperationalError:
        logger.info(f"  {table}: not found in SQLite, skipping")
        return

    if not rows:
        logger.info(f"  {table}: 0 rows")
        return

    pg_cols = [rename.get(c, c) for c in columns]
    placeholders = ", ".join(f":{c}" for c in columns)
    col_list = ", ".join(pg_cols)
    pk = pg_cols[0]

    count = 0
    for row in rows:
        d = dict(row)
        params = {}
        for c in columns:
            val = d.get(c)
            if c in json_fields and isinstance(val, str):
                params[c] = safe_json(val)
            else:
                params[c] = safe_str(val) if isinstance(val, str) else val
        try:
            pg.execute(
                text(f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) ON CONFLICT ({pk}) DO NOTHING"),
                params,
            )
            count += 1
        except Exception as e:
            logger.warning(f"  {table}: row {count} failed: {e}")
            continue

    logger.info(f"  {table}: {count} rows")


def _verify(sqlite_conn, pg_engine):
    """Compare row counts between SQLite and PostgreSQL."""
    logger.info("\n=== Verification ===")
    tables = ["leads", "users", "lead_user_data", "enrichment_cache",
              "outreach_attempts", "dos_cache", "places_cache",
              "app_settings", "change_alerts", "outreach_events"]

    with pg_engine.connect() as pg_conn:
        for table in tables:
            try:
                sqlite_count = sqlite_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.OperationalError:
                sqlite_count = "N/A"
            try:
                pg_count = pg_conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            except Exception:
                pg_count = "N/A"
            match = "OK" if sqlite_count == pg_count else "MISMATCH"
            logger.info(f"  {table}: SQLite={sqlite_count}, PG={pg_count} [{match}]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate SQLite to PostgreSQL")
    parser.add_argument("--sqlite-path", type=Path, default=DEFAULT_SQLITE)
    parser.add_argument("--pg-url", default=DEFAULT_PG_URL)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    migrate(args.sqlite_path, args.pg_url, args.dry_run)
