"""Add smart_lists table.

Revision ID: 002_smart_lists_table
Revises: 001_initial_schema
Create Date: 2026-02-25 06:35:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "002_smart_lists_table"
down_revision: Union[str, None] = "001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS smart_lists (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                filters JSONB NOT NULL DEFAULT '{}',
                pinned BOOLEAN DEFAULT false,
                last_evaluated_at TIMESTAMPTZ,
                last_lead_ids TEXT[] DEFAULT '{}',
                last_count INTEGER DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS idx_smart_lists_user ON smart_lists(user_id);"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_smart_lists_user;"))
    op.execute(sa.text("DROP TABLE IF EXISTS smart_lists;"))
