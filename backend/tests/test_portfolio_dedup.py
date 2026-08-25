"""Tests for portfolio-signature deduplication and condo/co-op rollup.

Two defects these guard:
  1. Distinct lead_id rows carrying byte-identical portfolios (10 of the top 60
     leads by portfolio size, July 2026).
  2. Condo/co-op unit lots inflating portfolio counts, because DOF gives each
     unit its own tax lot.
"""
import pytest

from src.transform import portfolio_dedup as pd


class TestPortfolioSignature:
    def test_order_independent(self):
        a = pd.portfolio_signature(["1000160001", "1000160002", "1000160003"])
        b = pd.portfolio_signature(["1000160003", "1000160001", "1000160002"])
        assert a == b

    def test_duplicate_independent(self):
        a = pd.portfolio_signature(["1000160001", "1000160002"])
        b = pd.portfolio_signature(["1000160001", "1000160001", "1000160002"])
        assert a == b

    def test_whitespace_normalised(self):
        a = pd.portfolio_signature(["1000160001", "1000160002"])
        b = pd.portfolio_signature([" 1000160001 ", "1000160002\n"])
        assert a == b

    def test_different_portfolios_differ(self):
        a = pd.portfolio_signature(["1000160001"])
        b = pd.portfolio_signature(["1000160002"])
        assert a != b

    def test_subset_is_not_a_match(self):
        """A partial overlap must NOT collide — only exact sets are duplicates."""
        a = pd.portfolio_signature(["1000160001", "1000160002", "1000160003"])
        b = pd.portfolio_signature(["1000160001", "1000160002"])
        assert a != b

    def test_empty_portfolio_returns_none(self):
        """
        Zero-link leads must never collide with each other. Production carries
        55,804 of them; hashing the empty set would merge them all.
        """
        assert pd.portfolio_signature([]) is None
        assert pd.portfolio_signature(None) is None
        assert pd.portfolio_signature(["", "  ", None]) is None

    def test_signature_is_stable_across_calls(self):
        bbls = ["3012340001", "1000160001"]
        assert pd.portfolio_signature(bbls) == pd.portfolio_signature(bbls)

    def test_signature_fits_the_column(self):
        sig = pd.portfolio_signature(["1000160001"])
        assert len(sig) <= 16, "migration declares VARCHAR(16)"


class TestFindDuplicateLeads:
    def test_finds_exact_duplicates(self):
        groups = pd.find_duplicate_leads({
            "aaa": ["1", "2", "3"],
            "bbb": ["3", "2", "1"],
            "ccc": ["9"],
        })
        assert len(groups) == 1
        assert groups[0]["lead_ids"] == ["aaa", "bbb"]
        assert groups[0]["portfolio_size"] == 3
        assert groups[0]["redundant_rows"] == 1

    def test_ignores_non_duplicates(self):
        groups = pd.find_duplicate_leads({"a": ["1"], "b": ["2"], "c": ["3"]})
        assert groups == []

    def test_zero_link_leads_never_group(self):
        groups = pd.find_duplicate_leads({"a": [], "b": [], "c": []})
        assert groups == []

    def test_three_way_collision(self):
        groups = pd.find_duplicate_leads({
            "a": ["1", "2"], "b": ["2", "1"], "c": ["1", "2"],
        })
        assert len(groups) == 1
        assert groups[0]["redundant_rows"] == 2
        assert groups[0]["lead_ids"] == ["a", "b", "c"]

    def test_sorted_by_portfolio_size_descending(self):
        groups = pd.find_duplicate_leads({
            "a": ["1"], "b": ["1"],
            "c": ["1", "2", "3"], "d": ["3", "2", "1"],
        })
        assert [g["portfolio_size"] for g in groups] == [3, 1]

    def test_handles_empty_input(self):
        assert pd.find_duplicate_leads({}) == []
        assert pd.find_duplicate_leads(None) == []


class TestChooseSurvivor:
    def test_prefers_more_contact_detail(self):
        winner = pd.choose_survivor(["aaa", "bbb"], {
            "aaa": {"phone": None, "email": None, "pipeline_stage": "research"},
            "bbb": {"phone": "212-555-0100", "email": "x@y.com",
                    "pipeline_stage": "research"},
        })
        assert winner == "bbb"

    def test_prefers_pipeline_progress_when_contact_equal(self):
        winner = pd.choose_survivor(["aaa", "bbb"], {
            "aaa": {"phone": "1", "pipeline_stage": "research"},
            "bbb": {"phone": "1", "pipeline_stage": "meeting"},
        })
        assert winner == "bbb"

    def test_deterministic_without_metadata(self):
        assert pd.choose_survivor(["zzz", "aaa"]) == "aaa"
        assert pd.choose_survivor(["aaa", "zzz"]) == "aaa"

    def test_deterministic_when_fully_tied(self):
        meta = {"a": {"phone": "1", "pipeline_stage": "research"},
                "b": {"phone": "1", "pipeline_stage": "research"}}
        assert pd.choose_survivor(["a", "b"], meta) == pd.choose_survivor(["b", "a"], meta)

    def test_missing_metadata_entry_does_not_crash(self):
        winner = pd.choose_survivor(["aaa", "bbb"], {"aaa": {"phone": "1"}})
        assert winner == "aaa"

    def test_accepts_objects(self):
        class L:
            def __init__(self, phone):
                self.phone = phone
                self.email = None
                self.company_website = None
                self.pipeline_stage = "research"
        assert pd.choose_survivor(["a", "b"], {"a": L(None), "b": L("212")}) == "b"


class TestMergePlan:
    def test_survivor_excluded_from_retire_list(self):
        groups = pd.find_duplicate_leads({"a": ["1", "2"], "b": ["2", "1"]})
        plan = pd.merge_plan(groups, {
            "a": {"phone": None, "pipeline_stage": "research"},
            "b": {"phone": "212", "pipeline_stage": "meeting"},
        })
        assert len(plan) == 1
        assert plan[0]["survivor_lead_id"] == "b"
        assert plan[0]["retire_lead_ids"] == ["a"]

    def test_plan_never_retires_everything(self):
        groups = pd.find_duplicate_leads({"a": ["1"], "b": ["1"], "c": ["1"]})
        plan = pd.merge_plan(groups)
        entry = plan[0]
        assert entry["survivor_lead_id"] not in entry["retire_lead_ids"]
        assert len(entry["retire_lead_ids"]) == 2

    def test_empty_groups_produce_empty_plan(self):
        assert pd.merge_plan([]) == []


class TestRollupKey:
    def test_coop_takes_precedence_over_condo(self):
        assert pd.rollup_key({"coop_number": "103367", "condo_number": "100735"}) \
            == "coop:103367"

    def test_condo_used_when_no_coop(self):
        assert pd.rollup_key({"condo_number": "101695"}) == "condo:101695"

    def test_none_for_standalone_lot(self):
        assert pd.rollup_key({"coop_number": None, "condo_number": None}) is None
        assert pd.rollup_key({}) is None

    def test_zero_string_is_not_an_identifier(self):
        """DOF writes '0' for 'no co-op', which must not become 'coop:0'."""
        assert pd.rollup_key({"coop_number": "0", "condo_number": "0"}) is None

    def test_leading_zeros_stripped(self):
        assert pd.rollup_key({"coop_number": "000123"}) == "coop:123"

    def test_accepts_alternate_field_name(self):
        assert pd.rollup_key({"coop_num": "555"}) == "coop:555"

    def test_accepts_objects(self):
        class R:
            coop_number = "999"
            condo_number = None
        assert pd.rollup_key(R()) == "coop:999"


class TestCollapsePortfolio:
    def test_unit_lots_collapse_to_one_building(self):
        recs = [{"bbl": f"100795{i:04d}", "coop_number": "103367"} for i in range(200)]
        out = pd.collapse_portfolio(recs)
        assert out["raw_lot_count"] == 200
        assert out["true_building_count"] == 1
        assert out["fragmentation_ratio"] == 200.0

    def test_standalone_lots_counted_individually(self):
        recs = [{"bbl": "3012340001"}, {"bbl": "3012340002"}]
        out = pd.collapse_portfolio(recs)
        assert out["raw_lot_count"] == 2
        assert out["true_building_count"] == 2
        assert out["fragmentation_ratio"] == 1.0

    def test_mixed_portfolio(self):
        recs = (
            [{"bbl": f"1007950{i:03d}", "coop_number": "103367"} for i in range(10)]
            + [{"bbl": f"1008950{i:03d}", "condo_number": "100735"} for i in range(5)]
            + [{"bbl": "3012340001"}]
        )
        out = pd.collapse_portfolio(recs)
        assert out["raw_lot_count"] == 16
        assert out["true_building_count"] == 3
        assert out["multi_lot_developments"] == 2

    def test_reports_both_counts(self):
        """
        Saved Smart Lists were built against the raw count, so the collapsed
        count must be additive information, never a silent replacement.
        """
        out = pd.collapse_portfolio([{"bbl": "1", "coop_number": "5"}])
        assert "raw_lot_count" in out and "true_building_count" in out

    def test_records_without_bbl_are_skipped(self):
        out = pd.collapse_portfolio([{"coop_number": "5"}, {"bbl": "1"}])
        assert out["raw_lot_count"] == 1

    def test_empty_input_does_not_divide_by_zero(self):
        out = pd.collapse_portfolio([])
        assert out["raw_lot_count"] == 0
        assert out["true_building_count"] == 0
        assert out["fragmentation_ratio"] == 1.0

    def test_bbls_deduplicated_within_a_development(self):
        out = pd.collapse_portfolio([
            {"bbl": "1007950001", "coop_number": "103367"},
            {"bbl": "1007950002", "coop_number": "103367"},
        ])
        assert sorted(out["developments"]["coop:103367"]) == ["1007950001", "1007950002"]


class TestRegressionAgainstMeasuredData:
    """Reproduces the July 2026 finding on the top 60 leads by portfolio size."""

    MEASURED = [
        ("Douglas Elliman", 307), ("Andrews Organization", 234),
        ("C&C Apartment Management", 212), ("Guardian Asset Management", 195),
        ("Nieuw Amsterdam", 193), ("Choice NY Management", 190),
        ("GPG Management", 148), ("Bronstein Properties", 139),
        ("PHH Mortgage", 137), ("Rose Property Mgmt", 128),
    ]

    def test_all_ten_known_duplicate_pairs_detected(self):
        lead_bbls = {}
        for i, (name, size) in enumerate(self.MEASURED):
            bbls = [f"{i}{n:09d}" for n in range(size)]
            lead_bbls[f"{name}-a"] = bbls
            lead_bbls[f"{name}-b"] = list(reversed(bbls))
        # A genuinely distinct lead that must not be swept in.
        lead_bbls["Unique Co"] = ["9999999999"]

        groups = pd.find_duplicate_leads(lead_bbls)
        assert len(groups) == 10
        assert sum(g["redundant_rows"] for g in groups) == 10
        assert [g["portfolio_size"] for g in groups] == \
            sorted([s for _, s in self.MEASURED], reverse=True)
