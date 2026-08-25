"""Tests for DOF-calibrated management company revenue estimation.

The central guarantee: this estimates the MANAGEMENT COMPANY's fee revenue,
driven by doors, not the building's profitability driven by value.
"""
import pytest

from src.score import revenue_dof as rd


# Borough medians from the calibration set. A building priced at exactly the
# median value per unit must reproduce that borough's median rent per unit.
MEDIAN_RENT_PER_UNIT = {
    "MANHATTAN": 3699,
    "BROOKLYN": 2400,
    "QUEENS": 1826,
    "BRONX": 1488,
}


class TestCalibrationRoundTrip:
    """The fitted ratios must reproduce the rents they were fitted from."""

    @pytest.mark.parametrize("boro,expected", sorted(MEDIAN_RENT_PER_UNIT.items()))
    def test_median_value_per_unit_reproduces_median_rent(self, boro, expected):
        units = 50
        mv = units * rd.MEDIAN_MARKET_VALUE_PER_UNIT[boro]
        rent = rd.implied_monthly_rent_per_unit(mv, units, boro, "D1")
        assert rent == pytest.approx(expected, rel=0.05), (
            f"{boro}: calibration drifted — got {rent:.0f}, expected ~{expected}"
        )

    def test_gross_income_ratio_applies_class_adjustment(self):
        base = rd.BOROUGH_GI_RATIO["MANHATTAN"]
        assert rd.gross_income_ratio("MANHATTAN", "D4") == pytest.approx(
            base * rd.CLASS_GI_ADJUSTMENT["D4"]
        )
        # Unknown class falls back to the borough base, not to zero.
        assert rd.gross_income_ratio("MANHATTAN", "ZZ") == pytest.approx(base)

    def test_unknown_borough_uses_citywide_ratio(self):
        assert rd.gross_income_ratio("ATLANTIS", None) == pytest.approx(
            rd.CITYWIDE_GI_RATIO
        )


class TestNotLinearInBuildingValue:
    """Regression guard. Fees are per door; they must not scale with value."""

    def test_trophy_building_does_not_scale_linearly(self):
        units = 40
        normal = rd.estimate_building_revenue(
            units, units * rd.MEDIAN_MARKET_VALUE_PER_UNIT["MANHATTAN"],
            "MANHATTAN", "D4", "2",
        )
        trophy = rd.estimate_building_revenue(
            units, units * rd.MEDIAN_MARKET_VALUE_PER_UNIT["MANHATTAN"] * 10,
            "MANHATTAN", "D4", "2",
        )
        ratio = trophy["estimated_annual_revenue"] / normal["estimated_annual_revenue"]
        assert ratio < 2.5, (
            f"10x the building value produced {ratio:.1f}x the fee — the estimate "
            "has gone linear in value again"
        )
        assert ratio > 1.0, "a more valuable building should still bill somewhat more"

    def test_condo_coop_fee_per_door_stays_in_plausible_band(self):
        for boro in rd.CONDO_COOP_FEE_PER_DOOR:
            for mult in (0.25, 1, 4, 20):
                mv = 100 * rd.MEDIAN_MARKET_VALUE_PER_UNIT[boro] * mult
                est = rd.estimate_building_revenue(100, mv, boro, "D4", "2")
                assert not est["out_of_band"], (
                    f"{boro} at {mult}x median: ${est['fee_per_unit_month']}/door "
                    "is outside the plausible band"
                )

    def test_quality_modifier_is_bounded(self):
        tiny = rd.quality_modifier(1, 1000, "MANHATTAN")
        huge = rd.quality_modifier(10**12, 1, "MANHATTAN")
        assert rd.QUALITY_MIN <= tiny <= rd.QUALITY_MAX
        assert rd.QUALITY_MIN <= huge <= rd.QUALITY_MAX

    def test_doors_drive_revenue(self):
        """Twice the doors at the same per-unit value is twice the revenue."""
        per_unit = rd.MEDIAN_MARKET_VALUE_PER_UNIT["BROOKLYN"]
        small = rd.estimate_building_revenue(20, 20 * per_unit, "BROOKLYN", "D1", "2")
        big = rd.estimate_building_revenue(40, 40 * per_unit, "BROOKLYN", "D1", "2")
        assert big["estimated_annual_revenue"] == pytest.approx(
            2 * small["estimated_annual_revenue"], rel=0.01
        )


class TestTwoFeeModels:
    def test_condo_coop_uses_per_door_basis(self):
        est = rd.estimate_building_revenue(
            120, 120 * rd.MEDIAN_MARKET_VALUE_PER_UNIT["MANHATTAN"],
            "MANHATTAN", "D4", "2",
        )
        assert est["basis"] == "per_door_condo_coop"
        # At the borough median the modifier is ~1, so the base rate applies.
        assert est["fee_per_unit_month"] == pytest.approx(
            rd.CONDO_COOP_FEE_PER_DOOR["MANHATTAN"], rel=0.05
        )
        assert est["implied_rent_per_unit_month"] is None

    def test_rental_uses_percentage_of_rent(self):
        units = 50
        mv = units * rd.MEDIAN_MARKET_VALUE_PER_UNIT["MANHATTAN"]
        est = rd.estimate_building_revenue(units, mv, "MANHATTAN", "D1", "2")
        assert est["basis"] == "pct_of_rent_rental"
        assert est["fee_per_unit_month"] == pytest.approx(
            est["implied_rent_per_unit_month"] * rd.RENTAL_FEE_RATE, rel=1e-3
        )

    def test_building_type_recognised_when_class_missing(self):
        est = rd.estimate_building_revenue(
            60, 60 * 145_615, "BROOKLYN", None, "2", building_type="coop"
        )
        assert est["basis"] == "per_door_condo_coop"

    @pytest.mark.parametrize("cls", ["R4", "D4", "C6", "R1", "D0"])
    def test_condo_coop_classes_detected(self, cls):
        assert rd.is_condo_or_coop(cls)

    @pytest.mark.parametrize("cls", ["D1", "C1", "C4", "D5"])
    def test_rental_classes_not_treated_as_condo(self, cls):
        assert not rd.is_condo_or_coop(cls)


class TestRefusesUnsupportableEstimates:
    """None means 'cannot say', which must not be confused with zero revenue."""

    def test_tax_class_1_returns_none(self):
        assert rd.estimate_building_revenue(2, 950_000, "QUEENS", "B1", "1") is None

    def test_zero_units_returns_none(self):
        assert rd.estimate_building_revenue(0, 5_000_000, "BROOKLYN", "D1", "2") is None
        assert rd.estimate_building_revenue(None, 5_000_000, "BROOKLYN", "D1", "2") is None

    def test_rental_without_market_value_returns_none(self):
        assert rd.estimate_building_revenue(30, 0, "BROOKLYN", "D1", "2") is None
        assert rd.estimate_building_revenue(30, None, "BROOKLYN", "D1", "2") is None

    def test_condo_coop_without_market_value_still_estimates(self):
        """Per-door billing does not need a value — only a quality modifier does."""
        est = rd.estimate_building_revenue(30, None, "BROOKLYN", "D4", "2")
        assert est is not None
        assert est["quality_modifier"] == 1.0
        assert est["fee_per_unit_month"] == pytest.approx(
            rd.CONDO_COOP_FEE_PER_DOOR["BROOKLYN"]
        )

    def test_implied_rent_none_when_underdetermined(self):
        assert rd.implied_monthly_rent_per_unit(0, 10, "BROOKLYN") is None
        assert rd.implied_monthly_rent_per_unit(1_000_000, 0, "BROOKLYN") is None


class TestConfidence:
    def test_staten_island_is_low_confidence(self):
        est = rd.estimate_building_revenue(40, 40 * 76_677, "STATEN ISLAND", "D4", "2")
        assert est["confidence"] == "low", "n=37 in the calibration set"

    def test_unknown_borough_is_low_confidence(self):
        est = rd.estimate_building_revenue(40, 8_000_000, "ATLANTIS", "D1", "2")
        assert est["confidence"] == "low"

    def test_no_estimate_ever_claims_high_confidence(self):
        """
        Both paths rest on something uncalibrated: condo/co-op on an assumed
        per-door schedule, rentals on a ratio fitted only on condo/co-op stock.
        If a "high" tier ever appears, the calibration story changed and this
        test should be the thing that notices.
        """
        for boro in ("MANHATTAN", "BROOKLYN", "QUEENS", "BRONX"):
            for cls in ("D4", "R4", "D1", "C1"):
                mv = 40 * rd.MEDIAN_MARKET_VALUE_PER_UNIT[boro]
                est = rd.estimate_building_revenue(40, mv, boro, cls, "2")
                assert est["confidence"] in ("low", "medium"), (
                    f"{boro}/{cls} claimed {est['confidence']} confidence"
                )

    def test_calibration_set_contains_no_rental_classes(self):
        """
        Documents WHY there is no high-confidence tier. Every class with a
        fitted adjustment is a condo/co-op class, because the calibration files
        are DOF's condo and co-op comparable rental income datasets.
        """
        assert all(rd.is_condo_or_coop(c) for c in rd.CLASS_GI_ADJUSTMENT)


class TestPortfolioAggregation:
    def _portfolio(self):
        return [
            {"unit_count": 50, "market_value": 50 * 232_118, "borough": "MANHATTAN",
             "building_class": "D4", "tax_class": "2"},
            {"unit_count": 30, "market_value": 30 * 145_615, "borough": "BROOKLYN",
             "building_class": "D1", "tax_class": "2"},
            # Class 1 — not supportable.
            {"unit_count": 2, "market_value": 900_000, "borough": "QUEENS",
             "building_class": "B1", "tax_class": "1"},
            # Rental with no value — not supportable.
            {"unit_count": 20, "market_value": None, "borough": "BRONX",
             "building_class": "D1", "tax_class": "2"},
        ]

    def test_uncovered_buildings_are_counted_not_dropped(self):
        r = rd.estimate_portfolio(self._portfolio())
        assert r["buildings_covered"] == 2
        assert r["buildings_uncovered"] == 2
        assert r["uncovered_units"] == 22
        assert r["coverage_ratio"] == pytest.approx(0.5)

    def test_total_is_sum_of_covered_buildings(self):
        p = self._portfolio()
        r = rd.estimate_portfolio(p)
        expected = sum(
            rd.estimate_building_revenue(
                b["unit_count"], b["market_value"], b["borough"],
                b["building_class"], b["tax_class"],
            )["estimated_annual_revenue"]
            for b in p[:2]
        )
        assert r["estimated_annual_revenue"] == pytest.approx(expected)

    def test_basis_mix_reported(self):
        r = rd.estimate_portfolio(self._portfolio())
        assert r["basis_mix"] == {"per_door_condo_coop": 1, "pct_of_rent_rental": 1}

    def test_empty_portfolio_is_zero_not_error(self):
        r = rd.estimate_portfolio([])
        assert r["estimated_annual_revenue"] == 0
        assert r["coverage_ratio"] == 0.0

    def test_accepts_objects_as_well_as_dicts(self):
        class B:
            unit_count, market_value, borough = 50, 50 * 232_118, "MANHATTAN"
            building_class, tax_class, building_type = "D4", "2", None
        r = rd.estimate_portfolio([B()])
        assert r["buildings_covered"] == 1
        assert r["estimated_annual_revenue"] > 0

    def test_monthly_is_annual_over_twelve(self):
        r = rd.estimate_portfolio(self._portfolio())
        assert r["estimated_monthly_revenue"] == pytest.approx(
            r["estimated_annual_revenue"] / 12, rel=0.01
        )
