"""Add lead lineage columns for reconciliation.

Revision ID: 008_lead_lineage
Revises: 007_acquisition_foundation
Create Date: 2026-04-30
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "008_lead_lineage"
down_revision: Union[str, None] = "007_acquisition_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE leads ADD COLUMN IF NOT EXISTS superseded_by_lead_id VARCHAR(12);"))
    op.execute(sa.text("ALTER TABLE leads ADD COLUMN IF NOT EXISTS retired_at TIMESTAMPTZ;"))
    op.execute(sa.text("ALTER TABLE leads ADD COLUMN IF NOT EXISTS retirement_reason TEXT;"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_leads_retired_at ON leads (retired_at);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_leads_superseded_by ON leads (superseded_by_lead_id);"))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_leads_superseded_by;"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_leads_retired_at;"))
    op.execute(sa.text("ALTER TABLE leads DROP COLUMN IF EXISTS retirement_reason;"))
    op.execute(sa.text("ALTER TABLE leads DROP COLUMN IF EXISTS retired_at;"))
    op.execute(sa.text("ALTER TABLE leads DROP COLUMN IF EXISTS superseded_by_lead_id;"))
