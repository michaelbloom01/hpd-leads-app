"""Add expires_at to dos_cache for stale-while-refresh behavior.

Revision ID: 005_dos_cache_expires_at
Revises: 004_building_lists
Create Date: 2026-03-05
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "005_dos_cache_expires_at"
down_revision: Union[str, None] = "004_building_lists"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            ALTER TABLE dos_cache
            ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE dos_cache
            SET expires_at = COALESCE(expires_at, cached_at + INTERVAL '30 days')
            WHERE cached_at IS NOT NULL;
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            ALTER TABLE dos_cache
            DROP COLUMN IF EXISTS expires_at;
            """
        )
    )
