"""Tests for the DOF assessment roll client.

No network. Parsing and derived signals only — the live Socrata surface is not
this suite's job.
"""
import pytest

from src.ingest import dof_client as dof


@pytest.fixture
def client():
    return dof.DOFAssessmentClient()


# A row shaped like Socrata's output: every value is a string, and "0" is used
# for "not populated" rather than a real zero.
RAW = {
    "parid": "1007950001",
    "boro": "1", "block": "795", "lot": "1",
    "year": "2027", "period": "3",
    "pymkttot": "92000000", "tenmkttot": "99000000",
    "finmkttot": "98525000", "curmkttot": "98525000",
    "curacttot": "44336250", "tenacttot": "44550000", "finacttot": "44336250",
    "curtxbtot": "44336250", "curactextot": "1200000",
    "curtaxclass": "2", "bldg_class": "D4",
    "owner": "795 PARK AVENUE CORP", "zoning": "R8B",
    "protest_1": "1", "attorney_group1": "135",
    "units": "120", "coop_apts": "118", "gross_sqft": "210000",
    "residential_area_gross": "195000", "retail_area_gross": "15000",
    "bld_story": "15", "land_area": "18000",
    "yrbuilt": "1929", "yralt1": "1998", "yralt2": "0",
    "coop_num": "0103367", "condo_number": "0",
    "appt_date": "39078", "newdrop": "0", "noav": "",
}


class TestParsing:
    def test_core_fields_mapped(self, client):
        r = client._parse_record(RAW)
        assert r.bbl == "1007950001"
        assert r.tax_year == "2027"
        assert r.period == "3"
        assert r.market_value == 98_525_000
        assert r.tax_class == "2"
        assert r.building_class == "D4"
        assert r.owner == "795 PARK AVENUE CORP"
        assert r.units == 120
        assert r.gross_sqft == 210_000
        assert r.year_built == 1929

    def test_zero_string_becomes_none(self, client):
        """Socrata writes '0' for 'no value', which must not read as a real 0."""
        r = client._parse_record(RAW)
        assert r.year_altered_2 is None, "yralt2='0' means never altered again"
        assert r.condo_number is None, "condo_number='0' means not a condo"

    def test_empty_string_becomes_none(self, client):
        r = client._parse_record({**RAW, "owner": "", "zoning": "  "})
        assert r.owner is None
        assert r.zoning is None

    def test_leading_zeros_stripped_from_development_keys(self, client):
        r = client._parse_record(RAW)
        assert r.coop_number == "103367"

    def test_boolean_flags(self, client):
        r = client._parse_record({**RAW, "newdrop": "1", "noav": "Y"})
        assert r.new_lot is True
        assert r.building_in_progress is True
        base = client._parse_record(RAW)
        assert base.new_lot is False
        assert base.building_in_progress is False

    def test_missing_fields_do_not_raise(self, client):
        r = client._parse_record({"parid": "1000010001"})
        assert r.bbl == "1000010001"
        assert r.market_value is None
        assert r.units is None

    def test_raw_preserved(self, client):
        r = client._parse_record(RAW)
        assert r.raw["attorney_group1"] == "135"


class TestDerivedSignals:
    def test_filed_protest(self, client):
        assert client._parse_record(RAW).filed_protest is True
        assert client._parse_record({**RAW, "protest_1": ""}).filed_protest is False

    def test_reduction_measured_on_assessed_not_market(self, client):
        """
        Tax Commission reductions land on the ASSESSED line. Measuring on market
        value produced ~zero citywide and was the original bug.
        """
        r = client._parse_record(RAW)
        assert r.protest_reduction == pytest.approx(44_550_000 - 44_336_250)
        assert r.protest_reduction_pct == pytest.approx(0.48, abs=0.01)

    def test_reduction_none_when_snapshots_missing(self, client):
        r = client._parse_record({**RAW, "tenacttot": "0", "finacttot": "0"})
        assert r.protest_reduction is None
        assert r.protest_reduction_pct is None

    def test_value_change_pct(self, client):
        r = client._parse_record(RAW)
        assert r.value_change_pct == pytest.approx(
            100 * (98_525_000 - 92_000_000) / 92_000_000, rel=1e-3
        )

    def test_exemption_signals(self, client):
        r = client._parse_record(RAW)
        assert r.has_exemption is True
        assert r.exemption_share == pytest.approx(
            100 * 1_200_000 / 44_336_250, rel=1e-2
        )
        clean = client._parse_record({**RAW, "curactextot": "0", "finactextot": "0"})
        assert clean.has_exemption is False
        assert clean.exemption_share is None

    def test_per_unit_and_per_sqft(self, client):
        r = client._parse_record(RAW)
        assert r.market_value_per_unit == pytest.approx(98_525_000 / 120, rel=1e-4)
        assert r.market_value_per_sqft == pytest.approx(98_525_000 / 210_000, rel=1e-4)

    def test_rollup_key_prefers_coop(self, client):
        assert client._parse_record(RAW).rollup_key == "coop:103367"
        condo = client._parse_record(
            {**RAW, "coop_num": "0", "condo_number": "101695"}
        )
        assert condo.rollup_key == "condo:101695"
        standalone = client._parse_record(
            {**RAW, "coop_num": "0", "condo_number": "0"}
        )
        assert standalone.rollup_key is None

    def test_implied_noi_refuses_class_1(self, client):
        """Class 1 is valued by comparable sales, so inverting to income is invalid."""
        c1 = client._parse_record({**RAW, "curtaxclass": "1"})
        assert c1.implied_noi(0.05) is None

    def test_implied_noi_for_class_2(self, client):
        r = client._parse_record(RAW)
        assert r.implied_noi(0.05) == pytest.approx(98_525_000 * 0.05)
        assert r.implied_noi(0) is None


class TestBBLConstruction:
    @pytest.mark.parametrize("boro,block,lot,expected", [
        (1, 795, 1, "1007950001"),
        ("1", "795", "1", "1007950001"),
        (3, 1234, 56, "3012340056"),
        (5, 8050, 9999, "5080509999"),
    ])
    def test_zero_padding(self, boro, block, lot, expected):
        assert dof.DOFAssessmentClient.make_bbl(boro, block, lot) == expected

    def test_length_is_always_ten(self):
        assert len(dof.DOFAssessmentClient.make_bbl(2, 2260, 1)) == 10


class TestConfiguration:
    def test_uses_the_live_dataset_not_a_stale_one(self):
        """
        Five near-identically named assessment datasets on NYC Open Data are
        abandoned. Pointing at one silently yields 2020-era data.
        """
        stale = {"yjxr-fw8i", "cqds-77ys", "qpsp-bm9z", "kevu-8hby",
                 "m8p6-tp4b", "rgy2-tti8"}
        assert dof._DOF_DATASET == "8y4t-faws"
        assert dof._DOF_DATASET not in stale
        assert dof._DOF_DATASET in dof.DOF_ENDPOINT

    def test_period_constants(self):
        assert dof.PERIOD_TENTATIVE == "1"
        assert dof.PERIOD_FINAL == "3"

    @pytest.mark.parametrize("field", [
        "parid", "curmkttot", "tenacttot", "finacttot", "curtaxclass",
        "protest_1", "attorney_group1", "units", "gross_sqft",
        "coop_num", "condo_number", "curactextot",
    ])
    def test_select_includes_load_bearing_fields(self, field):
        assert field in dof.SELECT_FIELDS

    def test_batch_size_within_url_limits(self):
        assert dof._BBL_BATCH <= 500, "larger batches risk Socrata URL length limits"

    def test_page_limit_is_socrata_max(self):
        assert dof._PAGE_LIMIT == 50000


class TestCaching:
    def test_repeated_lookup_does_not_refetch(self, client, monkeypatch):
        calls = []

        def fake_get(params, timeout=60, retries=3):
            calls.append(params)
            return [RAW]

        monkeypatch.setattr(client, "_get", fake_get)
        first = client.get_assessment("1007950001")
        second = client.get_assessment("1007950001")
        assert first is second
        assert len(calls) == 1

    def test_batch_marks_misses_so_they_are_not_refetched(self, client, monkeypatch):
        calls = []

        def fake_get(params, timeout=60, retries=3):
            calls.append(params)
            return [RAW]

        monkeypatch.setattr(client, "_get", fake_get)
        out = client.get_assessments_batch(["1007950001", "9999999999"])
        assert "1007950001" in out
        assert "9999999999" not in out
        client.get_assessments_batch(["1007950001", "9999999999"])
        assert len(calls) == 1, "second call should be served entirely from cache"

    def test_attorney_graph_groups_by_firm_id(self, client, monkeypatch):
        rows = [
            {**RAW, "parid": "1007950001", "attorney_group1": "135"},
            {**RAW, "parid": "1007950002", "attorney_group1": "135"},
            {**RAW, "parid": "1007950003", "attorney_group1": "18"},
        ]
        monkeypatch.setattr(client, "_get", lambda *a, **k: rows)
        graph = client.attorney_groups_for_bbls(
            ["1007950001", "1007950002", "1007950003"]
        )
        assert sorted(graph["135"]) == ["1007950001", "1007950002"]
        assert graph["18"] == ["1007950003"]
