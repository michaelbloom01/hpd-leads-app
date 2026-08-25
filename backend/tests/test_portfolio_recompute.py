"""Tests for the portfolio recompute script's pure logic.

No database. The SQL paths are exercised only through their text, to assert the
two guarantees that matter operationally:
  - duplicates are FLAGGED, never merged or retired
  - the raw lot count is preserved so the portfolio_size switch is reversible
"""
import importlib.util
import re
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parent.parent / "scripts"
SCRIPT = SCRIPTS / "portfolio_recompute.py"


def _load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pr = _load(SCRIPT)


@pytest.fixture(scope="module")
def source():
    return SCRIPT.read_text()


class TestTrueBuildingCount:
    def test_unit_lots_collapse(self):
        bbls = [f"100795{i:04d}" for i in range(200)]
        rollup = {b: "coop:103367" for b in bbls}
        assert pr.true_building_count(bbls, rollup) == 1

    def test_standalone_lots_counted_individually(self):
        bbls = ["3012340001", "3012340002", "3012340003"]
        assert pr.true_building_count(bbls, {}) == 3

    def test_mixed(self):
        coop = [f"100795{i:04d}" for i in range(10)]
        condo = [f"100895{i:04d}" for i in range(5)]
        rollup = {b: "coop:1" for b in coop} | {b: "condo:2" for b in condo}
        assert pr.true_building_count(coop + condo + ["3012340001"], rollup) == 3

    def test_duplicate_bbls_do_not_double_count(self):
        assert pr.true_building_count(["1", "1", "2"], {}) == 2

    def test_empty(self):
        assert pr.true_building_count([], {}) == 0

    def test_two_developments_stay_distinct(self):
        rollup = {"a": "coop:1", "b": "coop:2"}
        assert pr.true_building_count(["a", "b"], rollup) == 2


class TestBuildPlan:
    def test_records_both_counts(self):
        rollup = {"a": "coop:1", "b": "coop:1"}
        plan = pr.build_plan({"lead1": ["a", "b", "c"]}, rollup)
        entry = plan[0]
        assert entry["portfolio_size_raw"] == 3
        assert entry["true_building_count"] == 2
        assert entry["collapsed_by"] == 1

    def test_signature_present(self):
        plan = pr.build_plan({"lead1": ["a", "b"]}, {})
        assert plan[0]["portfolio_signature"] is not None
        assert len(plan[0]["portfolio_signature"]) <= 16

    def test_zero_link_lead_has_no_signature(self):
        plan = pr.build_plan({"lead1": []}, {})
        assert plan[0]["portfolio_signature"] is None
        assert plan[0]["portfolio_size_raw"] == 0

    def test_sorted_by_raw_size_descending(self):
        plan = pr.build_plan({"a": ["1"], "b": ["1", "2", "3"], "c": ["1", "2"]}, {})
        assert [p["portfolio_size_raw"] for p in plan] == [3, 2, 1]

    def test_no_rollup_data_is_a_noop_not_a_crash(self):
        """Before the DOF backfill runs, true count should equal raw count."""
        plan = pr.build_plan({"lead1": ["a", "b", "c"]}, {})
        assert plan[0]["true_building_count"] == plan[0]["portfolio_size_raw"]
        assert plan[0]["collapsed_by"] == 0


class TestSummarise:
    def test_totals_and_duplicate_counts(self):
        rollup = {"a": "coop:1", "b": "coop:1"}
        lead_bbls = {"x": ["a", "b", "c"], "y": ["c", "b", "a"], "z": ["d"]}
        plan = pr.build_plan(lead_bbls, rollup)
        from src.transform.portfolio_dedup import find_duplicate_leads
        dups = find_duplicate_leads(lead_bbls)
        s = pr.summarise(plan, dups)
        assert s["leads_examined"] == 3
        assert s["duplicate_groups"] == 1
        assert s["redundant_lead_rows"] == 1
        assert s["raw_lot_total"] == 7          # 3 + 3 + 1
        assert s["true_building_total"] == 5    # 2 + 2 + 1
        assert s["lots_collapsed"] == 2

    def test_largest_collapses_excludes_unaffected_leads(self):
        plan = pr.build_plan({"a": ["1", "2"]}, {})
        s = pr.summarise(plan, [])
        assert s["largest_collapses"] == []


class TestNeverMerges:
    """The decision was flag-only. These assert the script cannot violate it."""

    def test_no_destructive_sql(self, source):
        upper = source.upper()
        for forbidden in ("DELETE FROM", "DROP ", "TRUNCATE"):
            assert forbidden not in upper, f"script contains {forbidden}"

    def test_does_not_retire_or_supersede_leads(self, source):
        for col in ("retired_at", "superseded_by_lead_id", "retirement_reason"):
            assert not re.search(rf"SET[^;]*{col}", source, re.S | re.I), (
                f"script writes to {col} — that is a merge, not a flag"
            )

    def test_only_writes_expected_columns(self, source):
        update = source[source.index("UPDATE leads"): source.index("WHERE lead_id")]
        written = set(re.findall(r"(\w+)\s*=\s*:", update)) | \
            set(re.findall(r"(\w+)\s*=\s*NOW\(\)", update)) | \
            set(re.findall(r"(\w+)\s*=\s*COALESCE", update))
        assert written == {
            "portfolio_signature", "portfolio_signature_at",
            "portfolio_size_raw", "true_building_count", "portfolio_size",
        }, f"unexpected columns written: {written}"


class TestReversibility:
    def test_raw_count_written_only_when_null(self, source):
        """
        A rerun must not overwrite the original raw count with an
        already-collapsed value — that would destroy the ability to revert.
        """
        assert "portfolio_size_raw     = COALESCE(portfolio_size_raw, :raw)" in source

    def test_migration_downgrade_restores_portfolio_size(self):
        mig = (Path(__file__).parent.parent / "alembic" / "versions"
               / "011_dof_assessment.py").read_text()
        down = mig[mig.index("def downgrade"):]
        assert "UPDATE leads SET portfolio_size = portfolio_size_raw" in down
        assert down.index("UPDATE leads SET portfolio_size") < \
            down.index("DROP COLUMN IF EXISTS portfolio_size_raw"), \
            "must restore the value before dropping the column holding it"


class TestDryRunDefault:
    def test_execute_requires_both_flags(self, source):
        assert "args.execute and args.confirm_execute" in source

    def test_commit_only_inside_execute_branch(self, source):
        body = source[source.index("if args.execute and args.confirm_execute"):]
        assert "session.commit()" in body
        before = source[: source.index("if args.execute and args.confirm_execute")]
        assert "session.commit()" not in before, "commit outside the execute gate"

    def test_warns_when_rollup_data_missing(self, source):
        assert "WARNING" in source and "backfill" in source


class TestBackfillScript:
    @staticmethod
    @pytest.fixture(scope="class")
    def backfill_source():
        return (SCRIPTS / "dof_backfill.py").read_text()

    def test_upsert_is_idempotent(self, backfill_source):
        assert "ON CONFLICT (bbl, tax_year, period) DO UPDATE" in backfill_source

    def test_no_destructive_sql(self, backfill_source):
        upper = backfill_source.upper()
        for forbidden in ("DELETE FROM", "DROP ", "TRUNCATE"):
            assert forbidden not in upper

    def test_execute_requires_both_flags(self, backfill_source):
        assert "args.execute and args.confirm_execute" in backfill_source

    def test_records_source_dataset_for_provenance(self, backfill_source):
        assert "source_dataset" in backfill_source
        assert "_DOF_DATASET" in backfill_source
