"""Add persisted coordinate/provenance fields to buildings.

Revision ID: 006_building_coordinates
Revises: 005_dos_cache_expires_at
Create Date: 2026-03-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "006_building_coordinates"
down_revision: Union[str, None] = "005_dos_cache_expires_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            ALTER TABLE buildings
            ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS coordinate_source VARCHAR(30),
            ADD COLUMN IF NOT EXISTS coordinate_precision VARCHAR(30),
            ADD COLUMN IF NOT EXISTS coordinates_updated_at TIMESTAMPTZ;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS idx_buildings_coordinates
            ON buildings (latitude, longitude);
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_buildings_coordinates;"))
    op.execute(
        sa.text(
            """
            ALTER TABLE buildings
            DROP COLUMN IF EXISTS coordinates_updated_at,
            DROP COLUMN IF EXISTS coordinate_precision,
            DROP COLUMN IF EXISTS coordinate_source,
            DROP COLUMN IF EXISTS longitude,
            DROP COLUMN IF EXISTS latitude;
            """
        )
    )
