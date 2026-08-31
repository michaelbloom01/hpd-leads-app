"""Preserve source physical buildings and HPD registration history.

Revision ID: 011_building_identity
Revises: 010_truth_manifest
"""

import sqlalchemy as sa

from alembic import op

revision = "011_building_identity"
down_revision = "010_truth_manifest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE physical_buildings (
            bin VARCHAR(7) PRIMARY KEY CHECK (bin ~ '^[1-5][0-9]{6}$'),
            address TEXT, borough VARCHAR(20), zip_code VARCHAR(10),
            source_system VARCHAR(40) NOT NULL, source_record_key VARCHAR(80) NOT NULL,
            first_seen_at TIMESTAMPTZ NOT NULL, last_seen_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE TABLE building_parcel_links (
            id SERIAL PRIMARY KEY, bin VARCHAR(7) NOT NULL REFERENCES physical_buildings(bin),
            bbl VARCHAR(10) NOT NULL,
            relationship_type VARCHAR(40) NOT NULL, source_system VARCHAR(40) NOT NULL,
            source_record_key VARCHAR(80) NOT NULL, source_url TEXT NOT NULL,
            effective_from DATE, effective_to DATE, is_current BOOLEAN NOT NULL,
            first_seen_at TIMESTAMPTZ NOT NULL, last_seen_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_building_parcel_source UNIQUE (bin,bbl,source_system,source_record_key)
        );
        CREATE INDEX idx_building_parcel_current ON building_parcel_links(bbl,is_current);
        CREATE TABLE hpd_registration_snapshots (
            id SERIAL PRIMARY KEY, registration_id VARCHAR(20) NOT NULL,
            payload_hash VARCHAR(64) NOT NULL, hpd_building_id VARCHAR(20), bin VARCHAR(7), bbl VARCHAR(10),
            last_registration_date DATE, registration_end_date DATE, is_current BOOLEAN NOT NULL,
            identity_status VARCHAR(40) NOT NULL, source_url TEXT NOT NULL, source_updated_at TIMESTAMPTZ,
            raw_payload JSONB NOT NULL, first_seen_at TIMESTAMPTZ NOT NULL, last_seen_at TIMESTAMPTZ NOT NULL,
            ingestion_job_id INTEGER, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_hpd_registration_version UNIQUE (registration_id,payload_hash)
        );
        CREATE INDEX idx_hpd_registration_building ON hpd_registration_snapshots(hpd_building_id,is_current);
        CREATE INDEX idx_hpd_registration_bin ON hpd_registration_snapshots(bin,is_current);
        CREATE TABLE building_identity_quarantine (
            id SERIAL PRIMARY KEY, source_record_key VARCHAR(80) NOT NULL, payload_hash VARCHAR(64) NOT NULL,
            reason TEXT NOT NULL, raw_payload JSONB NOT NULL, ingestion_job_id INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_building_identity_quarantine UNIQUE (source_record_key,payload_hash,reason)
        );
        CREATE TABLE hpd_refresh_rollback_rows (
            id SERIAL PRIMARY KEY, ingestion_job_id INTEGER NOT NULL, table_name VARCHAR(50) NOT NULL,
            row_key VARCHAR(160) NOT NULL, was_existing BOOLEAN NOT NULL, before_payload JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_hpd_refresh_rollback UNIQUE (ingestion_job_id,table_name,row_key)
        );
    """))


def downgrade() -> None:
    # Run only after separately approved evidence retention and rollback review.
    for table in ("hpd_refresh_rollback_rows", "building_identity_quarantine",
                  "hpd_registration_snapshots", "building_parcel_links", "physical_buildings"):
        op.drop_table(table)
