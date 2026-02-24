"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-02-23

Creates all tables from SQLAlchemy models. This migration uses
Base.metadata.create_all() for the upgrade and drop_all() for
the downgrade, ensuring the migration stays in sync with models.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Import and use the actual model metadata to create all tables.
    # This ensures the migration is always in sync with the models.
    from src.models import Base
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)

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
    Base.metadata.drop_all(bind=bind)
