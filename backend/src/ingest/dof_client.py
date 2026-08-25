"""
DOF (Department of Finance) Property Valuation & Assessment Roll API client.

Fetches the NYC assessment roll -- the annual per-lot valuation DOF produces to
calculate property tax bills. Joins to buildings on BBL.

Dataset: 8y4t-faws "Property Valuation and Assessment Data Tax Classes 1,2,3,4"

IMPORTANT -- dataset selection:
    Several near-identically named assessment datasets exist on NYC Open Data.
    Most are ABANDONED and carry a thinner column set:
        yjxr-fw8i  last refreshed 2020  (do not use)
        cqds-77ys / qpsp-bm9z / kevu-8hby / m8p6-tp4b  stale 2022  (do not use)
        rgy2-tti8  stale 2018  (do not use)
    Only 8y4t-faws carries the full 139-field Property Master layout and is
    still refreshed (FY2027 final roll loaded 2026-06-15).

Roll periods (the `period` field) -- the roll snapshots value five times:
    PY*   prior year final
    TEN*  tentative roll, published January
    CBN*  change-by-notice, end of May
    FIN*  final roll, published May      <- period='3'
    CUR*  most current value for the lot
    period='1' tentative published, period='3' final published.

Two representation signals, in order of usefulness:
    `protest_1` / `attorney_group1` -- dense. 271,065 lots filed a protest in
        FY2027 and 270,241 carry a cert-attorney ID. Use for portfolio-level
        owner sophistication and as an entity-resolution fingerprint.
    tentative -> final ASSESSED delta -- sparse. Only 6,686 lots moved in
        FY2027 ($2.49B removed). Most Tax Commission settlements conclude after
        the final roll publishes and never appear here. See
        `AssessmentRecord.protest_reduction`.

Coverage: ~1.18M lots per tax year, FY2023-FY2027 available.
"""
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

import requests

logger = logging.getLogger(__name__)

_DOF_DATASET = os.environ.get("DOF_ASSESSMENT_DATASET_ID", "8y4t-faws")
DOF_ENDPOINT = f"https://data.cityofnewyork.us/resource/{_DOF_DATASET}.json"

# Latest published roll. Bump after DOF publishes the next final roll (each May).
DEFAULT_TAX_YEAR = os.environ.get("DOF_TAX_YEAR", "2027")

PERIOD_TENTATIVE = "1"
PERIOD_FINAL = "3"

# Socrata caps page size at 50k rows.
_PAGE_LIMIT = 50000
# Keep the `in (...)` clause well under Socrata's URL length ceiling.
_BBL_BATCH = 400

# Fields we pull. The dataset has 139 columns; this is the subset with
# analytic value for lead scoring, entity resolution and revenue estimation.
SELECT_FIELDS = ",".join([
    "parid", "boro", "block", "lot", "year", "period",
    # value lifecycle
    "pymkttot", "tenmkttot", "finmkttot", "curmkttot",
    "curacttot", "curtxbtot", "tenacttot", "finacttot",
    # exemptions -- 421-a / J-51 burn-off signal
    "curactextot", "finactextot",
    # tax class + identity
    "curtaxclass", "bldg_class", "owner", "zoning",
    # protest / representation graph
    "protest_1", "protest_2", "attorney_group1", "attorney_group2",
    # physical
    "units", "coop_apts", "gross_sqft", "residential_area_gross",
    "retail_area_gross", "office_area_gross", "garage_area",
    "bld_story", "land_area", "yrbuilt", "yralt1", "yralt2",
    # rollup keys + lifecycle events
    "coop_num", "condo_number", "appt_date", "newdrop", "noav",
])


def _num(v) -> Optional[float]:
    """Socrata returns everything as strings; 0 is used as 'not populated'."""
    if v in (None, "", "0"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v) -> Optional[int]:
    n = _num(v)
    return int(n) if n is not None else None


@dataclass
class AssessmentRecord:
    """One tax lot's FY assessment roll record."""
    bbl: str
    tax_year: str
    period: str

    # Market value -- DOF's estimate of what the property is worth.
    market_value: Optional[float] = None          # current
    market_value_final: Optional[float] = None    # final roll
    market_value_tentative: Optional[float] = None
    market_value_prior_year: Optional[float] = None

    # Assessed / taxable -- the fraction that actually gets taxed.
    # Tax Commission reductions land HERE, not on market value.
    assessed_total: Optional[float] = None
    assessed_total_tentative: Optional[float] = None
    assessed_total_final: Optional[float] = None
    taxable_total: Optional[float] = None
    exemption_total: Optional[float] = None

    tax_class: Optional[str] = None
    building_class: Optional[str] = None
    owner: Optional[str] = None
    zoning: Optional[str] = None

    # Tax certiorari representation
    protest_code: Optional[str] = None
    protest_code_2: Optional[str] = None
    attorney_group: Optional[str] = None
    attorney_group_2: Optional[str] = None

    units: Optional[int] = None
    residential_units: Optional[int] = None
    gross_sqft: Optional[int] = None
    residential_sqft: Optional[int] = None
    retail_sqft: Optional[int] = None
    office_sqft: Optional[int] = None
    garage_sqft: Optional[int] = None
    stories: Optional[float] = None
    land_area: Optional[int] = None
    year_built: Optional[int] = None
    year_altered_1: Optional[int] = None
    year_altered_2: Optional[int] = None

    # Rollup keys -- collapse condo/coop unit lots into their parent development.
    coop_number: Optional[str] = None
    condo_number: Optional[str] = None

    # Lifecycle events
    apportionment_date: Optional[str] = None   # lot split/merge -> condo conversion
    new_lot: bool = False
    building_in_progress: bool = False

    raw: dict = field(default_factory=dict, repr=False)

    # ---- derived signals -------------------------------------------------

    @property
    def filed_protest(self) -> bool:
        """Owner challenged the assessment with the Tax Commission."""
        return bool(self.protest_code or self.protest_code_2)

    @property
    def protest_reduction(self) -> Optional[float]:
        """
        Dollars knocked off ASSESSED value between tentative and final roll.

        Measured on assessed, not market, value -- verified against FY2027:
        market value is essentially unchanged tentative->final citywide, while
        6,686 lots saw assessed value fall, totalling $2.49B removed. Tax
        Commission reductions land on the assessed line.

        Caveat: this captures only reductions granted BEFORE the final roll
        publishes. Much of the Tax Commission's settlement activity concludes
        later in the year and never appears in this snapshot -- so treat a zero
        here as "no reduction on the roll", not "no reduction won". For a
        portfolio-level sophistication read, `filed_protest` is the far denser
        signal (271k lots carry one in FY2027).
        """
        if self.assessed_total_tentative and self.assessed_total_final:
            return self.assessed_total_tentative - self.assessed_total_final
        return None

    @property
    def protest_reduction_pct(self) -> Optional[float]:
        """Percent assessed-value reduction won between tentative and final roll."""
        red = self.protest_reduction
        if red is None or not self.assessed_total_tentative:
            return None
        return round(100.0 * red / self.assessed_total_tentative, 2)

    @property
    def value_change_pct(self) -> Optional[float]:
        """Year-over-year market value change."""
        if self.market_value_prior_year and self.market_value:
            return round(
                100.0 * (self.market_value - self.market_value_prior_year)
                / self.market_value_prior_year, 2
            )
        return None

    @property
    def has_exemption(self) -> bool:
        """Carries a tax exemption (421-a, J-51, etc.). Burn-off = distress timing."""
        return bool(self.exemption_total)

    @property
    def exemption_share(self) -> Optional[float]:
        """
        Share of assessed value currently exempt. High share means the tax bill
        jumps hard when the benefit expires -- a forced-sale / refi trigger.
        """
        if self.exemption_total and self.assessed_total:
            return round(100.0 * self.exemption_total / self.assessed_total, 2)
        return None

    @property
    def market_value_per_unit(self) -> Optional[float]:
        if self.market_value and self.units:
            return round(self.market_value / self.units, 2)
        return None

    @property
    def market_value_per_sqft(self) -> Optional[float]:
        if self.market_value and self.gross_sqft:
            return round(self.market_value / self.gross_sqft, 2)
        return None

    def implied_noi(self, cap_rate: float) -> Optional[float]:
        """
        Back into implied net operating income from DOF's market value.

        DOF values tax class 2 and 4 property by the income approach --
        capitalizing net income derived from RPIE filings. Inverting that gives
        an income estimate grounded in a first-party government valuation
        instead of a unit-count heuristic.

        This is an INFERENCE, not a published field. DOF's cap rate assumptions
        vary by class, borough and building type and are published in its
        assessment procedures and guidelines -- pass the matching rate.
        """
        if not self.market_value or not cap_rate:
            return None
        if self.tax_class and self.tax_class[0] not in ("2", "4"):
            return None  # class 1 is valued by comparable sales, not income
        return round(self.market_value * cap_rate, 2)

    @property
    def rollup_key(self) -> Optional[str]:
        """
        Parent development identifier for condo/coop unit lots.

        Thousands of individually assessed unit lots belong to one building.
        Grouping on this collapses them so portfolio counts stop fragmenting.
        """
        if self.coop_number:
            return f"coop:{self.coop_number}"
        if self.condo_number:
            return f"condo:{self.condo_number}"
        return None


class DOFAssessmentClient:
    """Client for the NYC DOF property assessment roll."""

    def __init__(self, app_token: Optional[str] = None, tax_year: str = DEFAULT_TAX_YEAR):
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/json"
        token = app_token or os.environ.get("NYC_OPEN_DATA_APP_TOKEN")
        if token:
            # Raises the anonymous rate limit substantially.
            self.session.headers["X-App-Token"] = token
        self.tax_year = tax_year
        self._cache: Dict[str, Optional[AssessmentRecord]] = {}
        self._request_errors = 0

    # ---- low level -------------------------------------------------------

    def _get(self, params: dict, timeout: int = 60, retries: int = 3) -> List[dict]:
        for attempt in range(retries + 1):
            try:
                resp = self.session.get(DOF_ENDPOINT, params=params, timeout=timeout)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                if attempt == retries:
                    self._request_errors += 1
                    logger.warning(f"DOF request failed after {retries} retries: {e}")
                    return []
                time.sleep(2 ** attempt)
        return []

    @staticmethod
    def make_bbl(boro_id, block, lot) -> str:
        """BBL format: 1 digit boro, 5 digits block, 4 digits lot."""
        return f"{int(boro_id)}{int(block):05d}{int(lot):04d}"

    # ---- fetch -----------------------------------------------------------

    def get_assessment(self, bbl: str, period: str = PERIOD_FINAL) -> Optional[AssessmentRecord]:
        """Fetch the assessment record for a single BBL."""
        if bbl in self._cache:
            return self._cache[bbl]
        rows = self._get({
            "$select": SELECT_FIELDS,
            "$where": f"parid='{bbl}' AND year='{self.tax_year}' AND period='{period}'",
            "$limit": 1,
        }, timeout=20)
        rec = self._parse_record(rows[0]) if rows else None
        self._cache[bbl] = rec
        return rec

    def get_assessments_batch(
        self, bbls: Iterable[str], period: str = PERIOD_FINAL
    ) -> Dict[str, AssessmentRecord]:
        """
        Fetch assessment records for many BBLs.

        Batches into `in (...)` clauses to keep request count low -- ~400 lots
        per call, so a 180k-building portfolio is ~450 requests.
        """
        wanted = [b for b in dict.fromkeys(bbls) if b]
        results: Dict[str, AssessmentRecord] = {}

        pending = []
        for bbl in wanted:
            if bbl in self._cache:
                if self._cache[bbl]:
                    results[bbl] = self._cache[bbl]
            else:
                pending.append(bbl)

        if not pending:
            logger.info(f"DOF batch: all {len(wanted)} BBLs cached")
            return results

        logger.info(f"DOF: fetching {len(pending)} BBLs for FY{self.tax_year} period={period}")
        for i in range(0, len(pending), _BBL_BATCH):
            batch = pending[i:i + _BBL_BATCH]
            inlist = ",".join(f"'{b}'" for b in batch)
            rows = self._get({
                "$select": SELECT_FIELDS,
                "$where": f"year='{self.tax_year}' AND period='{period}' AND parid in ({inlist})",
                "$limit": _PAGE_LIMIT,
            })
            for row in rows:
                rec = self._parse_record(row)
                self._cache[rec.bbl] = rec
                results[rec.bbl] = rec
            time.sleep(0.2)

        for bbl in pending:
            self._cache.setdefault(bbl, None)

        logger.info(
            f"DOF batch: {len(results)}/{len(wanted)} matched "
            f"({self._request_errors} request errors)"
        )
        return results

    def iter_roll(
        self,
        period: str = PERIOD_FINAL,
        where: Optional[str] = None,
        page_size: int = _PAGE_LIMIT,
    ) -> Iterable[AssessmentRecord]:
        """
        Stream the full roll for the tax year, paging via offset.

        Use for a full refresh (~1.18M lots -> ~24 requests at 50k/page).
        Pass `where` to narrow, e.g. "curtaxclass like '2%'" for the
        residential rental / coop / condo stock.
        """
        clause = f"year='{self.tax_year}' AND period='{period}'"
        if where:
            clause += f" AND ({where})"
        offset = 0
        while True:
            rows = self._get({
                "$select": SELECT_FIELDS,
                "$where": clause,
                "$order": "parid",
                "$limit": page_size,
                "$offset": offset,
            }, timeout=120)
            if not rows:
                return
            for row in rows:
                yield self._parse_record(row)
            if len(rows) < page_size:
                return
            offset += page_size
            time.sleep(0.2)

    # ---- representation graph -------------------------------------------

    def attorney_groups_for_bbls(self, bbls: Iterable[str]) -> Dict[str, List[str]]:
        """
        Map tax certiorari attorney group -> the BBLs they represent.

        The attorney group ID is a stable fingerprint that survives LLC name
        obfuscation: shell entities with unrelated names that share a cert
        attorney across a portfolio are usually the same operator. Use as an
        entity-resolution signal where name matching fails.
        """
        recs = self.get_assessments_batch(bbls)
        graph: Dict[str, List[str]] = defaultdict(list)
        for bbl, rec in recs.items():
            for grp in (rec.attorney_group, rec.attorney_group_2):
                if grp:
                    graph[grp].append(bbl)
        return dict(graph)

    # ---- parse -----------------------------------------------------------

    def _parse_record(self, r: dict) -> AssessmentRecord:
        return AssessmentRecord(
            bbl=r.get("parid", ""),
            tax_year=r.get("year", ""),
            period=r.get("period", ""),
            market_value=_num(r.get("curmkttot")),
            market_value_final=_num(r.get("finmkttot")),
            market_value_tentative=_num(r.get("tenmkttot")),
            market_value_prior_year=_num(r.get("pymkttot")),
            assessed_total=_num(r.get("curacttot")),
            assessed_total_tentative=_num(r.get("tenacttot")),
            assessed_total_final=_num(r.get("finacttot")),
            taxable_total=_num(r.get("curtxbtot")),
            exemption_total=_num(r.get("curactextot")) or _num(r.get("finactextot")),
            tax_class=(r.get("curtaxclass") or "").strip() or None,
            building_class=(r.get("bldg_class") or "").strip() or None,
            owner=(r.get("owner") or "").strip() or None,
            zoning=(r.get("zoning") or "").strip() or None,
            protest_code=(r.get("protest_1") or "").strip() or None,
            protest_code_2=(r.get("protest_2") or "").strip() or None,
            attorney_group=(r.get("attorney_group1") or "").strip() or None,
            attorney_group_2=(r.get("attorney_group2") or "").strip() or None,
            units=_int(r.get("units")),
            residential_units=_int(r.get("coop_apts")),
            gross_sqft=_int(r.get("gross_sqft")),
            residential_sqft=_int(r.get("residential_area_gross")),
            retail_sqft=_int(r.get("retail_area_gross")),
            office_sqft=_int(r.get("office_area_gross")),
            garage_sqft=_int(r.get("garage_area")),
            stories=_num(r.get("bld_story")),
            land_area=_int(r.get("land_area")),
            year_built=_int(r.get("yrbuilt")),
            year_altered_1=_int(r.get("yralt1")),
            year_altered_2=_int(r.get("yralt2")),
            coop_number=(r.get("coop_num") or "").strip().lstrip("0") or None,
            condo_number=(r.get("condo_number") or "").strip().lstrip("0") or None,
            apportionment_date=(r.get("appt_date") or "").strip() or None,
            new_lot=str(r.get("newdrop", "")).strip() == "1",
            building_in_progress=str(r.get("noav", "")).strip().upper() == "Y",
            raw=r,
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    client = DOFAssessmentClient()

    print("Single lookup (1 Wall Street area lot)...")
    rec = client.get_assessment("1000160001")
    if rec:
        print(f"  BBL {rec.bbl}  class {rec.tax_class}  bldg {rec.building_class}")
        print(f"  owner: {rec.owner}")
        print(f"  market value: ${rec.market_value:,.0f}" if rec.market_value else "  no value")
        print(f"  units={rec.units} gross_sqft={rec.gross_sqft} built={rec.year_built}")
        print(f"  protest={rec.filed_protest} attorney_group={rec.attorney_group}")
        print(f"  reduction won: {rec.protest_reduction_pct}%")
        print(f"  exemption share: {rec.exemption_share}%")
    else:
        print("  not found")

    print("\nBatch lookup...")
    test = ["1000160001", "1007950001", "3012340001", "1013730001"]
    batch = client.get_assessments_batch(test)
    for bbl, r in batch.items():
        mv = f"${r.market_value:,.0f}" if r.market_value else "n/a"
        print(f"  {bbl}: {mv:>16}  class {r.tax_class}  atty {r.attorney_group or '-'}")

    print("\nAttorney representation graph...")
    for grp, lots in client.attorney_groups_for_bbls(test).items():
        print(f"  group {grp}: {len(lots)} lots")
