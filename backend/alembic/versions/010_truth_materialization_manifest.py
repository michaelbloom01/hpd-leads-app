"""Add rollback manifest for truth materialization runs.

Revision ID: 010_truth_manifest
Revises: 009_truth_confidence_program
Create Date: 2026-05-14
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "010_truth_manifest"
down_revision: Union[str, None] = "009_truth_confidence_program"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS truth_materialization_manifest (
            id BIGSERIAL PRIMARY KEY,
            run_id VARCHAR(80) NOT NULL,
            item_type VARCHAR(30) NOT NULL,
            item_id VARCHAR(120) NOT NULL,
            was_existing BOOLEAN NOT NULL DEFAULT false,
            before_snapshot JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (run_id, item_type, item_id)
        );
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_truth_materialization_manifest_run ON truth_materialization_manifest (run_id);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_truth_materialization_manifest_type ON truth_materialization_manifest (item_type);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_truth_materialization_manifest_existing ON truth_materialization_manifest (was_existing);"))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS truth_materialization_manifest"))
