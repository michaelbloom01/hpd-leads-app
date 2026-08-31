"""Preserve full HPD business region text.

Revision ID: 013_contact_region_text
Revises: 012_compliance
"""

import sqlalchemy as sa

from alembic import op

revision = "013_contact_region_text"
down_revision = "012_compliance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "building_contacts", "business_state", existing_type=sa.String(5),
        type_=sa.Text(), existing_nullable=True,
    )


def downgrade() -> None:
    # Narrowing requires a reviewed data-retention decision once longer values exist.
    op.execute(sa.text("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM building_contacts WHERE length(business_state) > 5) THEN
                RAISE EXCEPTION 'Cannot narrow business_state while full source regions exceed five characters';
            END IF;
        END $$;
    """))
    op.alter_column(
        "building_contacts", "business_state", existing_type=sa.Text(),
        type_=sa.String(5), existing_nullable=True,
    )
