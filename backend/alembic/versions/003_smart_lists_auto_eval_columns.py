"""Add smart_lists auto-evaluation scheduling columns.

Revision ID: 003_smart_lists_auto_eval
Revises: 002_smart_lists_table
Create Date: 2026-02-25 15:05:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "003_smart_lists_auto_eval"
down_revision: Union[str, None] = "002_smart_lists_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            ALTER TABLE smart_lists
            ADD COLUMN IF NOT EXISTS auto_evaluate BOOLEAN NOT NULL DEFAULT false;
            """
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE smart_lists
            ADD COLUMN IF NOT EXISTS evaluation_interval_hours INTEGER NOT NULL DEFAULT 24;
            """
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE smart_lists
            ADD COLUMN IF NOT EXISTS next_evaluation_at TIMESTAMPTZ;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS idx_smart_lists_next_eval
            ON smart_lists (next_evaluation_at)
            WHERE auto_evaluate = true;
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_smart_lists_next_eval;"))
    op.execute(sa.text("ALTER TABLE smart_lists DROP COLUMN IF EXISTS next_evaluation_at;"))
    op.execute(sa.text("ALTER TABLE smart_lists DROP COLUMN IF EXISTS evaluation_interval_hours;"))
    op.execute(sa.text("ALTER TABLE smart_lists DROP COLUMN IF EXISTS auto_evaluate;"))
