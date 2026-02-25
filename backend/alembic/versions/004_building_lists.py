"""Add building_lists and building_list_members tables.

Revision ID: 004_building_lists
Revises: 003_smart_lists_auto_eval_columns
Create Date: 2026-02-25

Saved collections of buildings for the PM Operator persona.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "004_building_lists"
down_revision: Union[str, None] = "003_smart_lists_auto_eval_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS building_lists (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_building_lists_user ON building_lists(user_id);
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS building_list_members (
            list_id TEXT NOT NULL REFERENCES building_lists(id) ON DELETE CASCADE,
            bbl TEXT NOT NULL,
            added_at TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (list_id, bbl)
        );
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_building_list_members_bbl ON building_list_members(bbl);
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS building_list_members;"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_building_lists_user;"))
    op.execute(sa.text("DROP TABLE IF EXISTS building_lists;"))
