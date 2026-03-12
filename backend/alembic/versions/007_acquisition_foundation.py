"""Add acquisition foundation schema, integrity constraints, and target workflow tables.

Revision ID: 007_acquisition_foundation
Revises: 006_building_coordinates
Create Date: 2026-03-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "007_acquisition_foundation"
down_revision: Union[str, None] = "006_building_coordinates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_bm_current_bbl_role
            ON building_management (bbl, role)
            WHERE is_current = true;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_bm_current_bbl_lead_role
            ON building_management (bbl, lead_id, role)
            WHERE is_current = true;
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_building_contacts_natural_key
            ON building_contacts (bbl, registration_id, registration_contact_id, contact_type);
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_pad_addresses_bin_bbl_address
            ON pad_addresses (bin, bbl, address);
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_energy_grades_bbl_year_grade
            ON energy_grades (bbl, year, grade);
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_aep_designations_bbl_designation_removal
            ON aep_designations (bbl, designation_date, removal_date);
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_facade_inspections_bbl_cycle_filing
            ON facade_inspections (bbl, cycle, filing_date);
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS canonical_entities (
                canonical_entity_id VARCHAR(36) PRIMARY KEY,
                normalized_name TEXT NOT NULL UNIQUE,
                display_name TEXT,
                entity_type VARCHAR(30) DEFAULT 'pm_company',
                status VARCHAR(20) DEFAULT 'proposed',
                confidence_score DOUBLE PRECISION,
                profile JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
    )
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_canonical_entities_status ON canonical_entities (status);"))

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS canonical_entity_aliases (
                id SERIAL PRIMARY KEY,
                canonical_entity_id VARCHAR(36) NOT NULL REFERENCES canonical_entities(canonical_entity_id) ON DELETE CASCADE,
                alias_name TEXT NOT NULL,
                normalized_alias TEXT NOT NULL,
                source VARCHAR(30),
                confidence_score DOUBLE PRECISION,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
    )
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_canonical_entity_aliases_entity ON canonical_entity_aliases (canonical_entity_id);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_canonical_entity_aliases_normalized ON canonical_entity_aliases (normalized_alias);"))
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_canonical_entity_alias
            ON canonical_entity_aliases (canonical_entity_id, normalized_alias);
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS canonical_entity_leads (
                id SERIAL PRIMARY KEY,
                canonical_entity_id VARCHAR(36) NOT NULL REFERENCES canonical_entities(canonical_entity_id) ON DELETE CASCADE,
                lead_id VARCHAR(12) NOT NULL REFERENCES leads(lead_id) ON DELETE CASCADE,
                relationship_type VARCHAR(30) DEFAULT 'candidate',
                is_primary BOOLEAN DEFAULT false,
                confidence_score DOUBLE PRECISION,
                evidence JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
    )
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_canonical_entity_leads_entity ON canonical_entity_leads (canonical_entity_id);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_canonical_entity_leads_lead ON canonical_entity_leads (lead_id);"))
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_canonical_entity_lead
            ON canonical_entity_leads (canonical_entity_id, lead_id);
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS canonical_entity_buildings (
                id SERIAL PRIMARY KEY,
                canonical_entity_id VARCHAR(36) NOT NULL REFERENCES canonical_entities(canonical_entity_id) ON DELETE CASCADE,
                bbl VARCHAR(10) NOT NULL REFERENCES buildings(bbl) ON DELETE CASCADE,
                source VARCHAR(30) DEFAULT 'proposal',
                confidence_score DOUBLE PRECISION,
                evidence JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
    )
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_canonical_entity_buildings_entity ON canonical_entity_buildings (canonical_entity_id);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_canonical_entity_buildings_bbl ON canonical_entity_buildings (bbl);"))
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_canonical_entity_building
            ON canonical_entity_buildings (canonical_entity_id, bbl);
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS canonical_entity_match_proposals (
                id SERIAL PRIMARY KEY,
                proposal_key VARCHAR(120) NOT NULL UNIQUE,
                canonical_entity_id VARCHAR(36) NOT NULL REFERENCES canonical_entities(canonical_entity_id) ON DELETE CASCADE,
                lead_id VARCHAR(12) REFERENCES leads(lead_id) ON DELETE SET NULL,
                bucket VARCHAR(30) NOT NULL,
                proposal_status VARCHAR(20) DEFAULT 'proposed',
                safe_to_execute BOOLEAN DEFAULT false,
                reasons JSONB,
                evidence JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
    )
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_canonical_entity_match_proposals_entity ON canonical_entity_match_proposals (canonical_entity_id);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_canonical_entity_match_proposals_lead ON canonical_entity_match_proposals (lead_id);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_canonical_entity_match_proposals_bucket ON canonical_entity_match_proposals (bucket);"))

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS acquisition_theses (
                thesis_id VARCHAR(36) PRIMARY KEY,
                name VARCHAR(120) NOT NULL UNIQUE,
                description TEXT,
                criteria JSONB,
                is_default BOOLEAN DEFAULT false,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS target_lists (
                target_list_id VARCHAR(36) PRIMARY KEY,
                user_id VARCHAR(255) NOT NULL,
                name VARCHAR(120) NOT NULL,
                description TEXT,
                targeting_mode VARCHAR(30) DEFAULT 'both',
                thesis_id VARCHAR(36) REFERENCES acquisition_theses(thesis_id) ON DELETE SET NULL,
                source_notes TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
    )
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_target_lists_user ON target_lists (user_id);"))

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS target_list_items (
                target_item_id VARCHAR(36) PRIMARY KEY,
                target_list_id VARCHAR(36) NOT NULL REFERENCES target_lists(target_list_id) ON DELETE CASCADE,
                canonical_entity_id VARCHAR(36) REFERENCES canonical_entities(canonical_entity_id) ON DELETE SET NULL,
                matched_lead_id VARCHAR(12) REFERENCES leads(lead_id) ON DELETE SET NULL,
                company_name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                established VARCHAR(40),
                portfolio_estimate VARCHAR(120),
                units_estimate VARCHAR(120),
                geography TEXT,
                ownership TEXT,
                key_principals TEXT,
                condo_focus TEXT,
                website TEXT,
                website_domain VARCHAR(255),
                phone VARCHAR(30),
                phone_normalized VARCHAR(20),
                address TEXT,
                tier VARCHAR(20),
                acquisition_fit_notes TEXT,
                risk_flag TEXT,
                notes TEXT,
                raw_profile JSONB,
                match_status VARCHAR(20) DEFAULT 'unprocessed',
                thesis_score DOUBLE PRECISION,
                thesis_summary TEXT,
                thesis_breakdown JSONB,
                pipeline_stage VARCHAR(30) DEFAULT 'research',
                outreach_status VARCHAR(20) DEFAULT 'new',
                priority_rank INTEGER DEFAULT 0,
                next_follow_up DATE,
                last_contacted_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
    )
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_target_list_items_list ON target_list_items (target_list_id);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_target_list_items_match_status ON target_list_items (match_status);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_target_list_items_matched_lead ON target_list_items (matched_lead_id);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_target_list_items_canonical_entity ON target_list_items (canonical_entity_id);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_target_list_items_next_follow_up ON target_list_items (next_follow_up);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_target_list_items_normalized_name ON target_list_items (normalized_name);"))

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS target_matches (
                id SERIAL PRIMARY KEY,
                target_item_id VARCHAR(36) NOT NULL REFERENCES target_list_items(target_item_id) ON DELETE CASCADE,
                lead_id VARCHAR(12) REFERENCES leads(lead_id) ON DELETE SET NULL,
                canonical_entity_id VARCHAR(36) REFERENCES canonical_entities(canonical_entity_id) ON DELETE SET NULL,
                match_type VARCHAR(30) NOT NULL,
                confidence_score DOUBLE PRECISION,
                selected BOOLEAN DEFAULT false,
                reasons JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
    )
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_target_matches_item ON target_matches (target_item_id);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_target_matches_lead ON target_matches (lead_id);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_target_matches_selected ON target_matches (selected);"))

    op.execute(sa.text("ALTER TABLE outreach_events ADD COLUMN IF NOT EXISTS canonical_entity_id VARCHAR(36);"))
    op.execute(sa.text("ALTER TABLE outreach_events ADD COLUMN IF NOT EXISTS target_item_id VARCHAR(36);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_outreach_events_target_item ON outreach_events (target_item_id);"))
    op.execute(sa.text("ALTER TABLE change_alerts ADD COLUMN IF NOT EXISTS canonical_entity_id VARCHAR(36);"))
    op.execute(sa.text("ALTER TABLE change_alerts ADD COLUMN IF NOT EXISTS target_item_id VARCHAR(36);"))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_outreach_events_target_item;"))
    op.execute(sa.text("ALTER TABLE outreach_events DROP COLUMN IF EXISTS target_item_id;"))
    op.execute(sa.text("ALTER TABLE outreach_events DROP COLUMN IF EXISTS canonical_entity_id;"))
    op.execute(sa.text("ALTER TABLE change_alerts DROP COLUMN IF EXISTS target_item_id;"))
    op.execute(sa.text("ALTER TABLE change_alerts DROP COLUMN IF EXISTS canonical_entity_id;"))

    op.execute(sa.text("DROP TABLE IF EXISTS target_matches;"))
    op.execute(sa.text("DROP TABLE IF EXISTS target_list_items;"))
    op.execute(sa.text("DROP TABLE IF EXISTS target_lists;"))
    op.execute(sa.text("DROP TABLE IF EXISTS acquisition_theses;"))
    op.execute(sa.text("DROP TABLE IF EXISTS canonical_entity_match_proposals;"))
    op.execute(sa.text("DROP TABLE IF EXISTS canonical_entity_buildings;"))
    op.execute(sa.text("DROP TABLE IF EXISTS canonical_entity_leads;"))
    op.execute(sa.text("DROP TABLE IF EXISTS canonical_entity_aliases;"))
    op.execute(sa.text("DROP TABLE IF EXISTS canonical_entities;"))

    op.execute(sa.text("DROP INDEX IF EXISTS uq_facade_inspections_bbl_cycle_filing;"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_aep_designations_bbl_designation_removal;"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_energy_grades_bbl_year_grade;"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_pad_addresses_bin_bbl_address;"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_building_contacts_natural_key;"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_bm_current_bbl_lead_role;"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_bm_current_bbl_role;"))
