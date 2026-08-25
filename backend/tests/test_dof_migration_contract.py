"""Contract tests binding migration 011 to the BuildingAssessment model.

A migration and its ORM model drifting apart fails at runtime in production,
not at import time. These tests catch that in CI. No database required — the
migration source is parsed as text and compared against the model metadata.
"""
import re
from pathlib import Path

import pytest

from src.models import BuildingAssessment

VERSIONS = Path(__file__).parent.parent / "alembic" / "versions"
MIGRATION = VERSIONS / "011_dof_assessment.py"


@pytest.fixture(scope="module")
def migration_sql():
    return MIGRATION.read_text()


class TestMigrationChain:
    def test_migration_exists(self):
        assert MIGRATION.exists()

    def test_revision_identifiers(self, migration_sql):
        assert 'revision: str = "011_dof_assessment"' in migration_sql
        assert 'down_revision: Union[str, None] = "010_truth_manifest"' in migration_sql

    def test_single_head(self):
        """Two heads make `alembic upgrade head` ambiguous and it stops working."""
        revisions, downs = set(), set()
        for f in VERSIONS.glob("*.py"):
            src = f.read_text()
            m = re.search(r'^revision: str = ["\']([^"\']+)["\']', src, re.M)
            d = re.search(r'^down_revision: Union\[str, None\] = ["\']([^"\']+)["\']', src, re.M)
            if m:
                revisions.add(m.group(1))
            if d:
                downs.add(d.group(1))
        heads = revisions - downs
        assert heads == {"011_dof_assessment"}, f"expected one head, found {heads}"

    def test_every_down_revision_resolves(self):
        revisions = set()
        edges = {}
        for f in VERSIONS.glob("*.py"):
            src = f.read_text()
            m = re.search(r'^revision: str = ["\']([^"\']+)["\']', src, re.M)
            d = re.search(r'^down_revision: Union\[str, None\] = (["\']([^"\']+)["\']|None)', src, re.M)
            if m:
                revisions.add(m.group(1))
                edges[m.group(1)] = d.group(2) if d and d.group(2) else None
        for rev, parent in edges.items():
            assert parent is None or parent in revisions, (
                f"{rev} points at missing revision {parent}"
            )

    def test_exactly_one_root(self):
        roots = []
        for f in VERSIONS.glob("*.py"):
            src = f.read_text()
            if re.search(r'^down_revision: Union\[str, None\] = None', src, re.M):
                roots.append(f.name)
        assert len(roots) == 1, f"expected one root migration, found {roots}"


class TestModelMatchesMigration:
    def test_table_name_matches(self, migration_sql):
        assert BuildingAssessment.__tablename__ == "building_assessments"
        assert "CREATE TABLE IF NOT EXISTS building_assessments" in migration_sql

    def test_every_model_column_exists_in_migration(self, migration_sql):
        ddl = migration_sql[
            migration_sql.index("CREATE TABLE IF NOT EXISTS building_assessments"):
        ]
        ddl = ddl[: ddl.index('"""')] if '"""' in ddl else ddl
        missing = [
            c.name for c in BuildingAssessment.__table__.columns
            if not re.search(rf"\b{re.escape(c.name)}\b", ddl)
        ]
        assert not missing, f"model columns absent from migration DDL: {missing}"

    def test_composite_primary_key(self, migration_sql):
        pk = [c.name for c in BuildingAssessment.__table__.primary_key]
        assert sorted(pk) == ["bbl", "period", "tax_year"]
        assert "PRIMARY KEY (bbl, tax_year, period)" in migration_sql

    def test_bbl_width_matches_bbl_format(self):
        """BBL is 1 boro + 5 block + 4 lot = exactly 10 characters."""
        assert BuildingAssessment.__table__.columns["bbl"].type.length == 10

    def test_signature_column_width_matches_hash_length(self, migration_sql):
        from src.transform.portfolio_dedup import portfolio_signature
        assert "portfolio_signature VARCHAR(16)" in migration_sql
        assert len(portfolio_signature(["1000160001"])) <= 16

    @pytest.mark.parametrize("col", [
        "portfolio_signature", "portfolio_signature_at",
        "portfolio_size_raw", "true_building_count",
    ])
    def test_lead_columns_exist_in_model_and_migration(self, migration_sql, col):
        from src.models import Lead
        assert col in Lead.__table__.columns, f"{col} missing from Lead model"
        assert f"ADD COLUMN IF NOT EXISTS {col}" in migration_sql


class TestMigrationSafety:
    def test_is_additive_only(self, migration_sql):
        """
        The app is live. Upgrade must not drop or retype anything — only the
        downgrade path may drop.
        """
        upgrade = migration_sql[
            migration_sql.index("def upgrade"): migration_sql.index("def downgrade")
        ]
        for forbidden in ("DROP TABLE", "DROP COLUMN", "ALTER COLUMN", "TRUNCATE", "DELETE FROM"):
            assert forbidden not in upgrade.upper(), (
                f"upgrade() contains destructive statement: {forbidden}"
            )

    def test_upgrade_is_idempotent(self, migration_sql):
        upgrade = migration_sql[
            migration_sql.index("def upgrade"): migration_sql.index("def downgrade")
        ]
        creates = re.findall(r"CREATE (TABLE|INDEX)(?! IF NOT EXISTS)", upgrade)
        assert not creates, "every CREATE must use IF NOT EXISTS to stay re-runnable"
        adds = re.findall(r"ADD COLUMN(?! IF NOT EXISTS)", upgrade)
        assert not adds, "every ADD COLUMN must use IF NOT EXISTS"

    def test_downgrade_reverses_upgrade(self, migration_sql):
        down = migration_sql[migration_sql.index("def downgrade"):]
        assert "DROP TABLE IF EXISTS building_assessments" in down
        assert "DROP COLUMN IF EXISTS portfolio_signature" in down

    def test_portfolio_signature_index_is_not_unique(self, migration_sql):
        """
        A collision is the signal we want to detect, not an error to reject at
        write time. A unique index would make duplicate leads unwritable.
        """
        m = re.search(r"CREATE (UNIQUE )?INDEX[^;]*idx_leads_portfolio_signature", migration_sql)
        assert m is not None
        assert m.group(1) is None, "portfolio_signature index must not be UNIQUE"
