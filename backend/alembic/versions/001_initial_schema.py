"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-02-23

Creates a frozen snapshot of initial tables from SQLAlchemy models.
Unlike metadata-wide create_all/drop_all, this revision only operates
on an explicit table list to avoid future model drift side effects.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Import model metadata, but apply only a frozen table snapshot so
    # future model changes do not mutate this historical revision.
    from src.models import Base
    bind = op.get_bind()
    table_order = [
        "users",
        "leads",
        "scoring_configs",
        "buildings",
        "building_management",
        "building_contacts",
        "enrichment_results",
        "outreach_events",
        "change_alerts",
        "ingestion_jobs",
        "building_score_history",
        "hpd_complaints",
        "acris_transactions",
        "dob_permits",
        "hpd_violations",
        "energy_grades",
        "hpd_litigation",
        "emergency_repairs",
        "aep_designations",
        "eviction_filings",
        "facade_inspections",
        "data_quality_log",
        "pad_addresses",
        "lead_user_data",
        "enrichment_cache",
        "outreach_attempts",
        "enrichment_jobs",
        "dos_cache",
        "places_cache",
        "ai_summary_cache",
        "app_settings",
        "agent_conversations",
        "agent_messages",
        "agent_pending_actions",
    ]
    for table_name in table_order:
        table = Base.metadata.tables.get(table_name)
        if table is not None:
            table.create(bind=bind, checkfirst=True)

    # Seed scoring config presets
    op.execute(
        sa.text("""
        INSERT INTO scoring_configs (name, is_active, is_preset, weights, created_by, created_at, updated_at)
        VALUES
        (
            'Default', true, true,
            '{"ownership_change": 20, "complaint_spike": 17, "violation_trend": 12, "energy_grade_drop": 8, "dob_permits": 8, "hpd_litigation": 9, "emergency_repairs": 5, "building_size": 5, "eviction_activity": 9, "facade_status": 7}',
            'system', now(), now()
        ),
        (
            'Violation-Heavy', false, true,
            '{"ownership_change": 8, "complaint_spike": 25, "violation_trend": 20, "energy_grade_drop": 5, "dob_permits": 5, "hpd_litigation": 15, "emergency_repairs": 7, "building_size": 0, "eviction_activity": 10, "facade_status": 5}',
            'system', now(), now()
        ),
        (
            'Transaction-Focused', false, true,
            '{"ownership_change": 35, "complaint_spike": 8, "violation_trend": 8, "energy_grade_drop": 5, "dob_permits": 15, "hpd_litigation": 5, "emergency_repairs": 4, "building_size": 8, "eviction_activity": 7, "facade_status": 5}',
            'system', now(), now()
        )
        """)
    )


def downgrade() -> None:
    from src.models import Base
    bind = op.get_bind()
    table_order = [
        "agent_pending_actions",
        "agent_messages",
        "agent_conversations",
        "app_settings",
        "ai_summary_cache",
        "places_cache",
        "dos_cache",
        "enrichment_jobs",
        "outreach_attempts",
        "enrichment_cache",
        "lead_user_data",
        "pad_addresses",
        "data_quality_log",
        "facade_inspections",
        "eviction_filings",
        "aep_designations",
        "emergency_repairs",
        "hpd_litigation",
        "energy_grades",
        "hpd_violations",
        "dob_permits",
        "acris_transactions",
        "hpd_complaints",
        "building_score_history",
        "ingestion_jobs",
        "change_alerts",
        "outreach_events",
        "enrichment_results",
        "building_contacts",
        "building_management",
        "buildings",
        "scoring_configs",
        "leads",
        "users",
    ]
    for table_name in table_order:
        table = Base.metadata.tables.get(table_name)
        if table is not None:
            table.drop(bind=bind, checkfirst=True)
