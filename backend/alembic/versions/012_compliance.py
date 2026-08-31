"""Add source-backed DOB compliance evidence and bounded source coverage.

Revision ID: 012_compliance
Revises: 011_building_identity
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "012_compliance"
down_revision = "011_building_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "compliance_records",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("source_system", sa.String(40), nullable=False),
        sa.Column("source_record_key", sa.String(160), nullable=False),
        sa.Column("record_type", sa.String(30), nullable=False),
        sa.Column("bin", sa.String(7)),
        sa.Column("bbl", sa.String(10)),
        sa.Column("address", sa.Text()),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("violation_type", sa.String(80)),
        sa.Column("device_type", sa.String(120)),
        sa.Column("status", sa.String(80)),
        sa.Column("issue_date", sa.Date()),
        sa.Column("description", sa.Text()),
        sa.Column("identity_status", sa.String(40), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(40), nullable=False),
        sa.Column("ingestion_run_id", sa.String(80), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint(
            "source_system", "source_record_key", name="uq_compliance_source_record"
        ),
    )
    op.create_index("ix_compliance_records_bin", "compliance_records", ["bin"])
    op.create_index("ix_compliance_records_bbl", "compliance_records", ["bbl"])
    op.create_table(
        "compliance_observations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "record_id",
            sa.String(32),
            sa.ForeignKey("compliance_records.id"),
            nullable=False,
        ),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingestion_run_id", sa.String(80), nullable=False),
        sa.Column("parser_version", sa.String(40), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint(
            "record_id", "ingestion_run_id", name="uq_compliance_observation_run"
        ),
    )
    op.create_table(
        "compliance_source_checks",
        sa.Column("source_system", sa.String(40), primary_key=True),
        sa.Column("bin", sa.String(7), primary_key=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("records_count", sa.Integer(), nullable=False),
        sa.Column("ingestion_run_id", sa.String(80), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
    )
    op.create_table(
        "compliance_balance_observations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("bin", sa.String(7), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("scope", sa.String(30), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        sa.Column("source_timestamp_raw", sa.String(200), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewer", sa.String(200), nullable=False),
        sa.Column("evidence_note", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False, unique=True),
        sa.CheckConstraint(
            "amount_cents >= 0 AND amount_cents <= 1000000000000",
            name="ck_compliance_balance_amount",
        ),
        sa.CheckConstraint(
            "category = 'LL152' AND scope = 'bin_category'",
            name="ck_compliance_balance_scope",
        ),
    )
    op.create_index(
        "ix_compliance_balances_scope",
        "compliance_balance_observations",
        ["bin", "category", "observed_at"],
    )


def downgrade() -> None:
    op.drop_table("compliance_balance_observations")
    op.drop_table("compliance_source_checks")
    op.drop_table("compliance_observations")
    op.drop_table("compliance_records")
