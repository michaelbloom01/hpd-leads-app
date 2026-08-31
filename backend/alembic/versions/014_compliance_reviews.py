"""Append-only internal compliance review history.

Revision ID: 014_compliance_reviews
Revises: 013_contact_region_text
"""

import sqlalchemy as sa

from alembic import op

revision = "014_compliance_reviews"
down_revision = "013_contact_region_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "compliance_reviews",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("record_id", sa.String(32), sa.ForeignKey("compliance_records.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.String(80), nullable=False),
        sa.Column("actor_label", sa.String(320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("record_id", "version", name="uq_compliance_review_version"),
        sa.CheckConstraint("version > 0", name="ck_compliance_review_version"),
        sa.CheckConstraint("state IN ('new','in_review','verified_for_briefing','monitoring','closed_internally','dismissed','source_mismatch')", name="ck_compliance_review_state"),
    )


def downgrade() -> None:
    # Retained human review history requires a separate retention decision.
    op.execute(sa.text("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM compliance_reviews) THEN
            RAISE EXCEPTION 'Review history exists; review evidence retention before dropping compliance_reviews';
        END IF;
    END $$;"""))
    op.drop_table("compliance_reviews")
