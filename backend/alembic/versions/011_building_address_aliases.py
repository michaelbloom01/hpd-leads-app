"""Add building address aliases.

Revision ID: 011_building_address_aliases
Revises: 010_truth_manifest
Create Date: 2026-06-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "011_building_address_aliases"
down_revision: Union[str, None] = "010_truth_manifest"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS building_address_aliases (
            id BIGSERIAL PRIMARY KEY,
            bbl VARCHAR(10) NOT NULL REFERENCES buildings(bbl) ON DELETE CASCADE,
            bin VARCHAR(20),
            display_address TEXT NOT NULL,
            normalized_address TEXT NOT NULL,
            source VARCHAR(50) NOT NULL,
            source_record_id VARCHAR(80),
            confidence_score DOUBLE PRECISION NOT NULL DEFAULT 0.8,
            is_primary BOOLEAN NOT NULL DEFAULT false,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """))
    op.execute(sa.text("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_building_address_aliases_bbl_normalized_source
        ON building_address_aliases (bbl, normalized_address, source);
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_building_address_aliases_normalized
        ON building_address_aliases (normalized_address);
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_building_address_aliases_bbl
        ON building_address_aliases (bbl);
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_building_address_aliases_source
        ON building_address_aliases (source);
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_building_address_aliases_primary
        ON building_address_aliases (bbl, is_primary);
    """))

    op.execute(sa.text("""
        INSERT INTO building_address_aliases (
            bbl, bin, display_address, normalized_address, source,
            confidence_score, is_primary, metadata, created_at, updated_at
        )
        SELECT
            bbl,
            bin,
            UPPER(TRIM(address)) AS display_address,
            UPPER(TRIM(address)) AS normalized_address,
            'building_record_address' AS source,
            0.75 AS confidence_score,
            true AS is_primary,
            '{"backfill":"existing_buildings"}'::jsonb AS metadata,
            NOW(),
            NOW()
        FROM buildings
        WHERE address IS NOT NULL
          AND TRIM(address) <> ''
        ON CONFLICT (bbl, normalized_address, source) DO NOTHING;
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS building_address_aliases;"))
