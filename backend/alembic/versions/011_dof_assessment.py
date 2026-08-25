"""Add DOF assessment roll table and lead portfolio signature.

Revision ID: 011_dof_assessment
Revises: 010_truth_manifest
Create Date: 2026-07-26

Adds:
  - building_assessments: per-lot DOF assessment roll records, keyed on
    (bbl, tax_year, period). Sourced from NYC Open Data 8y4t-faws.
  - leads.portfolio_signature: order-independent hash of a lead's BBL set,
    used to detect leads whose portfolios are byte-identical. Measured on the
    top 60 leads by portfolio size, 10 were exact duplicates.
  - leads.portfolio_size_raw: preserves the pre-rollup raw TAX LOT count.
    portfolio_size is being switched to the true BUILDING count, which collapses
    condo/co-op unit lots into their parent development. Keeping the raw number
    means the change is reversible and the two can be compared -- saved Smart
    Lists were all built against the raw count.

Purely additive. No existing column is altered or dropped, and no data is
rewritten, so this is safe to apply ahead of any backfill. The backfill and any
lead merge are separate, explicitly gated operations.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "011_dof_assessment"
down_revision: Union[str, None] = "010_truth_manifest"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS building_assessments (
            bbl                       VARCHAR(10)  NOT NULL,
            tax_year                  VARCHAR(4)   NOT NULL,
            period                    VARCHAR(1)   NOT NULL,

            -- Market value: DOF's estimate of what the property is worth.
            market_value              NUMERIC(16,2),
            market_value_final        NUMERIC(16,2),
            market_value_tentative    NUMERIC(16,2),
            market_value_prior_year   NUMERIC(16,2),

            -- Assessed / taxable. Tax Commission reductions land HERE, not on
            -- market value.
            assessed_total            NUMERIC(16,2),
            assessed_total_tentative  NUMERIC(16,2),
            assessed_total_final      NUMERIC(16,2),
            taxable_total             NUMERIC(16,2),
            exemption_total           NUMERIC(16,2),

            tax_class                 VARCHAR(2),
            building_class            VARCHAR(4),
            owner                      TEXT,
            zoning                    VARCHAR(10),

            -- Tax certiorari representation. Dense: 271,065 lots filed a
            -- protest in FY2027 and 270,241 carry an attorney id. The id is
            -- stable across years and resolves to a firm name via the Open
            -- Article 7 Petitions dataset (aht6-vxai).
            protest_code              VARCHAR(3),
            protest_code_2            VARCHAR(3),
            attorney_group            VARCHAR(4),
            attorney_group_2          VARCHAR(4),

            units                     INTEGER,
            residential_units         INTEGER,
            gross_sqft                INTEGER,
            residential_sqft          INTEGER,
            retail_sqft               INTEGER,
            office_sqft               INTEGER,
            garage_sqft               INTEGER,
            stories                   NUMERIC(7,2),
            land_area                 INTEGER,
            year_built                INTEGER,
            year_altered_1            INTEGER,
            year_altered_2            INTEGER,

            -- Parent development keys. DOF assigns each condo/co-op UNIT its
            -- own tax lot, so these collapse unit lots into one building.
            coop_number               VARCHAR(12),
            condo_number              VARCHAR(12),

            apportionment_date        VARCHAR(8),
            new_lot                   BOOLEAN DEFAULT FALSE,
            building_in_progress      BOOLEAN DEFAULT FALSE,

            source_dataset            VARCHAR(20),
            ingested_at               TIMESTAMPTZ DEFAULT NOW(),

            CONSTRAINT pk_building_assessments PRIMARY KEY (bbl, tax_year, period)
        );
    """))

    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS idx_bassess_bbl ON building_assessments (bbl);"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS idx_bassess_attorney_group "
        "ON building_assessments (attorney_group) WHERE attorney_group IS NOT NULL;"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS idx_bassess_coop_num "
        "ON building_assessments (coop_number) WHERE coop_number IS NOT NULL;"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS idx_bassess_condo_num "
        "ON building_assessments (condo_number) WHERE condo_number IS NOT NULL;"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS idx_bassess_year_period "
        "ON building_assessments (tax_year, period);"
    ))

    op.execute(sa.text(
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS portfolio_signature VARCHAR(16);"
    ))
    op.execute(sa.text(
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS portfolio_signature_at TIMESTAMPTZ;"
    ))
    op.execute(sa.text(
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS portfolio_size_raw INTEGER;"
    ))
    op.execute(sa.text(
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS true_building_count INTEGER;"
    ))
    # Deliberately NOT unique -- a collision is the signal we want to detect,
    # not an error to reject at write time.
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS idx_leads_portfolio_signature "
        "ON leads (portfolio_signature) WHERE portfolio_signature IS NOT NULL;"
    ))


def downgrade() -> None:
    # Restore the raw lot count before dropping the column that holds it.
    op.execute(sa.text(
        "UPDATE leads SET portfolio_size = portfolio_size_raw "
        "WHERE portfolio_size_raw IS NOT NULL;"
    ))
    op.execute(sa.text("ALTER TABLE leads DROP COLUMN IF EXISTS true_building_count;"))
    op.execute(sa.text("ALTER TABLE leads DROP COLUMN IF EXISTS portfolio_size_raw;"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_leads_portfolio_signature;"))
    op.execute(sa.text("ALTER TABLE leads DROP COLUMN IF EXISTS portfolio_signature_at;"))
    op.execute(sa.text("ALTER TABLE leads DROP COLUMN IF EXISTS portfolio_signature;"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_bassess_year_period;"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_bassess_condo_num;"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_bassess_coop_num;"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_bassess_attorney_group;"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_bassess_bbl;"))
    op.execute(sa.text("DROP TABLE IF EXISTS building_assessments;"))
