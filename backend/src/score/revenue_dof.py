"""
DOF-calibrated management company revenue estimation.

WHAT THIS ESTIMATES
-------------------
The annual REVENUE OF THE MANAGEMENT COMPANY -- the fees the managing agent
bills. NOT the profitability of the building. The building's economics only
enter as an input to the fee, never as the answer.

    management company revenue = doors x fee per door x 12

Doors are the primary driver. Building value and type are MODIFIERS on the
per-door rate, because a Park Avenue co-op bills more per door than a Bronx
walk-up. This is the correct shape: an estimate linear in building value
overstates trophy assets badly, since no agent bills a fee proportional to a
building being worth $200M.

TWO FEE MODELS, BECAUSE THERE ARE TWO BUSINESSES
------------------------------------------------
- RENTAL: the agent collects rent and bills a percentage of collections
  (4-8%, 5% midpoint). Fee per door therefore scales with rent, which is why
  a Manhattan rental door is worth far more than a Bronx one.
- CONDO / CO-OP: the agent administers a building it collects no rent for.
  Fees are negotiated PER DOOR, typically $50-150/unit/month in NYC, nudged by
  building quality. Applying a percentage-of-rent model here overstates by
  roughly 2-3x -- a mistake this module previously made.

WHERE THE RENT NUMBER COMES FROM
--------------------------------
Not a guess. DOF publishes actual income for a subset of buildings:

    9ck6-2jew  DOF Condominium Comparable Rental Income  (latest FY2023)
    myei-c3fa  DOF Cooperative Comparable Rental Income  (latest FY2022)

Both carry `estimated_gross_income` and `full_market_value` for the SAME
building -- 7,367 usable rows. Fitting one against the other gives a tight,
stable ratio (citywide median 20.0%, p10 17.0%, p90 28.1%; Manhattan IQR
17.6-20.6%). Market value is known for all 1.18M lots from the assessment roll,
so the calibration extends a real measurement to the whole city.

Sanity check on the fit: the implied median rents are $3,699/month in Manhattan,
$2,400 Brooklyn, $1,826 Queens, $1,488 Bronx. Those are plausible, and close to
the hand-built table in `revenue.py` -- but derived per building rather than
assumed per borough-and-type.

WHAT THIS IS STILL NOT
----------------------
- DOF's *view* of income, not an actual rent roll. For co-ops and condos RPTL
  581 forces DOF to value the building as if it were a rental, so that income is
  hypothetical -- which is exactly why the condo/co-op branch ignores it and uses
  a per-door schedule instead.
- Tax class 1 (1-3 family) is valued by comparable SALES, not income. The
  function returns None rather than a number.
- The per-door schedule below is an INDUSTRY ESTIMATE, not calibrated -- no
  public dataset of actual management agreement fees exists. It is flagged
  separately from the DOF-derived constants for that reason.

Every result carries `basis` and `confidence`. Never mix a calibrated estimate
with a fallback one silently.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# --- DOF-CALIBRATED constants ---------------------------------------------
# Fitted on the 7,367 buildings where DOF publishes both income and market
# value. Regenerate when DOF refreshes the comparable-rental-income files.
CALIBRATION_SOURCE = "DOF 9ck6-2jew (FY2023) + myei-c3fa (FY2022), n=7367"

BOROUGH_GI_RATIO = {
    "MANHATTAN": 0.1883,       # n=3417
    "BRONX": 0.2885,           # n=546
    "BROOKLYN": 0.2010,        # n=1909
    "QUEENS": 0.2336,          # n=1458
    "STATEN ISLAND": 0.2598,   # n=37 -- thin, low confidence
}
CITYWIDE_GI_RATIO = 0.2003

CLASS_GI_ADJUSTMENT = {
    "D4": 1.077,   # n=2751, elevator co-op
    "R4": 0.933,   # n=1947, condominium
    "C6": 1.093,   # n=1399, walk-up co-op
    "RR": 0.950,   # n=519
    "R2": 0.986,   # n=396
    "R9": 0.972,   # n=206
    "D0": 0.908,   # n=143
}

# Median DOF market value per unit, used to scale the per-door rate against
# building quality. Same calibration set.
MEDIAN_MARKET_VALUE_PER_UNIT = {
    "MANHATTAN": 232_118,
    "BRONX": 62_109,
    "BROOKLYN": 145_615,
    "QUEENS": 92_877,
    "STATEN ISLAND": 76_677,
}
CITYWIDE_MEDIAN_MV_PER_UNIT = 145_000

# --- ASSUMED constants (NOT calibrated) ------------------------------------
# No public dataset of actual management agreement fees exists. These are
# industry estimates and are the weakest link in the model -- if real fee data
# ever becomes available, replace these first.
RENTAL_FEE_RATE = 0.05          # 4-8% of collected rent, 5% midpoint

CONDO_COOP_FEE_PER_DOOR = {     # $/unit/month, administration only
    "MANHATTAN": 95.0,
    "BRONX": 55.0,
    "BROOKLYN": 75.0,
    "QUEENS": 65.0,
    "STATEN ISLAND": 55.0,
}
CITYWIDE_FEE_PER_DOOR = 70.0

# Quality modifier bounds on the per-door rate. Dampened with a fractional
# exponent so a building worth 10x the borough median pays more per door, but
# nowhere near 10x more.
QUALITY_EXPONENT = 0.30
QUALITY_MIN, QUALITY_MAX = 0.70, 1.80

# Plausibility band on fee per door per month. Anything outside is reported via
# `out_of_band` rather than silently clamped -- a wild number is information.
FEE_PER_DOOR_MIN, FEE_PER_DOOR_MAX = 25.0, 400.0

CONDO_COOP_CLASSES = {
    "R0", "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "RR",
    "C6", "C8", "D0", "D4",
}


def _norm_boro(borough: Optional[str]) -> Optional[str]:
    b = (str(borough or "")).upper().strip()
    return b if b in BOROUGH_GI_RATIO else None


def _cls(building_class: Optional[str]) -> str:
    return (str(building_class or "")).upper().strip()[:2]


def gross_income_ratio(borough: Optional[str], building_class: Optional[str]) -> float:
    """Calibrated gross-income-to-market-value ratio for a borough/class."""
    base = BOROUGH_GI_RATIO.get(_norm_boro(borough), CITYWIDE_GI_RATIO)
    return base * CLASS_GI_ADJUSTMENT.get(_cls(building_class), 1.0)


def implied_monthly_rent_per_unit(
    market_value: Optional[float],
    units: Optional[int],
    borough: Optional[str] = None,
    building_class: Optional[str] = None,
) -> Optional[float]:
    """Monthly rent per unit implied by DOF market value. None if underdetermined."""
    mv = float(market_value or 0)
    u = int(units or 0)
    if mv <= 0 or u <= 0:
        return None
    return (mv * gross_income_ratio(borough, building_class)) / u / 12.0


def quality_modifier(
    market_value: Optional[float], units: Optional[int], borough: Optional[str] = None
) -> float:
    """
    Per-door rate modifier from building value relative to the borough median.

    Dampened and bounded: a building worth 10x the median lands near 2x, not 10x.
    """
    mv = float(market_value or 0)
    u = int(units or 0)
    if mv <= 0 or u <= 0:
        return 1.0
    median = MEDIAN_MARKET_VALUE_PER_UNIT.get(
        _norm_boro(borough), CITYWIDE_MEDIAN_MV_PER_UNIT
    )
    if median <= 0:
        return 1.0
    ratio = (mv / u) / median
    if ratio <= 0:
        return 1.0
    return max(QUALITY_MIN, min(QUALITY_MAX, ratio ** QUALITY_EXPONENT))


def is_condo_or_coop(building_class: Optional[str], building_type: Optional[str] = None) -> bool:
    if _cls(building_class) in CONDO_COOP_CLASSES:
        return True
    return (str(building_type or "")).lower().strip() in ("condo", "coop")


def estimate_building_revenue(
    units: Optional[int],
    market_value: Optional[float] = None,
    borough: Optional[str] = None,
    building_class: Optional[str] = None,
    tax_class: Optional[str] = None,
    building_type: Optional[str] = None,
) -> Optional[dict]:
    """
    Annual management fee revenue for one building.

    Returns None when the estimate is not supportable -- no doors, or tax class 1
    where DOF uses comparable sales rather than income. Returning None rather than
    zero is deliberate: the caller must decide whether to fall back, and an
    uncovered building must not read as a building worth nothing.
    """
    u = int(units or 0)
    if u <= 0:
        return None

    tc = (str(tax_class or "")).strip()
    if tc and tc[0] == "1":
        return None

    boro = _norm_boro(borough)
    condo_coop = is_condo_or_coop(building_class, building_type)
    mv = float(market_value or 0)

    if condo_coop:
        # Administration only -- no rent collected, so fee is per door.
        base = CONDO_COOP_FEE_PER_DOOR.get(boro, CITYWIDE_FEE_PER_DOOR)
        modifier = quality_modifier(mv, u, borough)
        fee_per_door = base * modifier
        rent_per_unit = None
        basis = "per_door_condo_coop"
    else:
        # Rental -- percentage of collected rent.
        rent_per_unit = implied_monthly_rent_per_unit(mv, u, borough, building_class)
        if rent_per_unit is None:
            return None
        fee_per_door = rent_per_unit * RENTAL_FEE_RATE
        modifier = 1.0
        basis = "pct_of_rent_rental"

    annual = u * fee_per_door * 12.0

    # No "high" tier exists, deliberately. Both paths rest on something
    # uncalibrated: condo/co-op uses the ASSUMED per-door schedule, and rentals
    # extrapolate a ratio fitted only on condo/co-op buildings -- DOF publishes
    # comparable rental income for condos and co-ops, not for rental buildings,
    # so no rental class has its own fitted adjustment. Claiming high confidence
    # anywhere would overstate what the data supports.
    if boro is None:
        confidence = "low"                     # no borough-level calibration
    elif boro == "STATEN ISLAND":
        confidence = "low"                     # n=37 in the calibration set
    else:
        confidence = "medium"

    return {
        "estimated_annual_revenue": round(annual, 0),
        "estimated_monthly_revenue": round(annual / 12.0, 0),
        "fee_per_unit_month": round(fee_per_door, 2),
        "out_of_band": not (FEE_PER_DOOR_MIN <= fee_per_door <= FEE_PER_DOOR_MAX),
        "implied_rent_per_unit_month": round(rent_per_unit, 2) if rent_per_unit else None,
        "quality_modifier": round(modifier, 3),
        "units": u,
        "market_value": mv or None,
        "basis": basis,
        "confidence": confidence,
        "calibration": CALIBRATION_SOURCE,
    }


def estimate_portfolio(buildings) -> dict:
    """
    Aggregate a management company's portfolio into an annual revenue estimate.

    Buildings the model cannot cover are counted, not dropped -- so low coverage
    reads as low coverage rather than as low revenue.
    """
    def get(b, *names):
        for n in names:
            v = b.get(n) if isinstance(b, dict) else getattr(b, n, None)
            if v not in (None, ""):
                return v
        return None

    total = 0.0
    covered = uncovered = out_of_band = 0
    covered_units = uncovered_units = 0
    by_basis: dict = {}

    for b in buildings or []:
        units = get(b, "unit_count", "units")
        est = estimate_building_revenue(
            units=units,
            market_value=get(b, "market_value", "assessed_value"),
            borough=get(b, "borough", "boro"),
            building_class=get(b, "building_class", "bldg_class"),
            tax_class=get(b, "tax_class", "curtaxclass"),
            building_type=get(b, "building_type"),
        )
        if est is None:
            uncovered += 1
            uncovered_units += int(units or 0)
            continue
        covered += 1
        covered_units += est["units"]
        total += est["estimated_annual_revenue"]
        out_of_band += 1 if est["out_of_band"] else 0
        by_basis[est["basis"]] = by_basis.get(est["basis"], 0) + 1

    n = covered + uncovered
    return {
        "estimated_annual_revenue": round(total, 0),
        "estimated_monthly_revenue": round(total / 12.0, 0),
        "buildings_covered": covered,
        "buildings_uncovered": uncovered,
        "covered_units": covered_units,
        "uncovered_units": uncovered_units,
        "coverage_ratio": round(covered / n, 3) if n else 0.0,
        "out_of_band_buildings": out_of_band,
        "basis_mix": by_basis,
        "calibration": CALIBRATION_SOURCE,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Market values sit at each borough's MEDIAN value per unit, so the output
    # should reproduce that borough's median rent. Anything else means the
    # calibration has drifted.
    cases = [
        ("Manhattan co-op (median)",  120, 120 * 232_118, "MANHATTAN", "D4", "2"),
        ("Manhattan condo (median)",   90,  90 * 232_118, "MANHATTAN", "R4", "2"),
        ("Manhattan trophy co-op",     40,  200_000_000,  "MANHATTAN", "D4", "2"),
        ("Brooklyn walk-up (median)",  12,  12 * 145_615, "BROOKLYN",  "C1", "2"),
        ("Bronx elevator (median)",    60,  60 *  62_109, "BRONX",     "D1", "2"),
        ("Manhattan rental (median)",  50,  50 * 232_118, "MANHATTAN", "D1", "2"),
        ("Queens 2-family (class 1)",   2,      950_000,  "QUEENS",    "B1", "1"),
        ("No market value",            30,            0,  "BROOKLYN",  "D1", "2"),
    ]
    for label, u, mv, boro, cls, tc in cases:
        e = estimate_building_revenue(u, mv, boro, cls, tc)
        if e is None:
            print(f"{label:<28} -> not applicable")
            continue
        rent = f"${e['implied_rent_per_unit_month']:,.0f}" if e["implied_rent_per_unit_month"] else "n/a"
        flag = "  <-- OUT OF BAND" if e["out_of_band"] else ""
        print(f"{label:<28} {u:>4}u  rent/u {rent:>8}  "
              f"fee ${e['fee_per_unit_month']:>6,.0f}/door/mo  "
              f"${e['estimated_annual_revenue']:>9,.0f}/yr  [{e['confidence']}]{flag}")
