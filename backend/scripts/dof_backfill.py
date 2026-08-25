"""Backfill building_assessments from the NYC DOF property assessment roll.

Source: NYC Open Data 8y4t-faws. Five near-identically named datasets on the
portal are abandoned -- see src/ingest/dof_client.py before changing the id.

Two modes:

  --scope portfolio   only BBLs already in the buildings table (default).
                      Fast, and covers everything the app actually scores.
  --scope citywide    the entire roll, ~1.18M lots in ~24 paged requests.
                      Narrow with --tax-class 2 to load only class 2 stock.

Dry run by default:

    python scripts/dof_backfill.py --indent 2
    python scripts/dof_backfill.py --scope citywide --tax-class 2 --indent 2

Execute (requires both flags):

    python scripts/dof_backfill.py --execute --confirm-execute

Set NYC_OPEN_DATA_APP_TOKEN before a citywide run — the anonymous rate limit is
low for a job this size.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db.session import get_sync_url  # noqa: E402
from src.ingest.dof_client import (  # noqa: E402
    DEFAULT_TAX_YEAR,
    PERIOD_FINAL,
    DOFAssessmentClient,
    _DOF_DATASET,
)

UPSERT = text("""
    INSERT INTO building_assessments (
        bbl, tax_year, period,
        market_value, market_value_final, market_value_tentative,
        market_value_prior_year,
        assessed_total, assessed_total_tentative, assessed_total_final,
        taxable_total, exemption_total,
        tax_class, building_class, owner, zoning,
        protest_code, protest_code_2, attorney_group, attorney_group_2,
        units, residential_units, gross_sqft, residential_sqft, retail_sqft,
        office_sqft, garage_sqft, stories, land_area,
        year_built, year_altered_1, year_altered_2,
        coop_number, condo_number, apportionment_date,
        new_lot, building_in_progress, source_dataset, ingested_at
    ) VALUES (
        :bbl, :tax_year, :period,
        :market_value, :market_value_final, :market_value_tentative,
        :market_value_prior_year,
        :assessed_total, :assessed_total_tentative, :assessed_total_final,
        :taxable_total, :exemption_total,
        :tax_class, :building_class, :owner, :zoning,
        :protest_code, :protest_code_2, :attorney_group, :attorney_group_2,
        :units, :residential_units, :gross_sqft, :residential_sqft, :retail_sqft,
        :office_sqft, :garage_sqft, :stories, :land_area,
        :year_built, :year_altered_1, :year_altered_2,
        :coop_number, :condo_number, :apportionment_date,
        :new_lot, :building_in_progress, :source_dataset, NOW()
    )
    ON CONFLICT (bbl, tax_year, period) DO UPDATE SET
        market_value             = EXCLUDED.market_value,
        market_value_final       = EXCLUDED.market_value_final,
        market_value_tentative   = EXCLUDED.market_value_tentative,
        market_value_prior_year  = EXCLUDED.market_value_prior_year,
        assessed_total           = EXCLUDED.assessed_total,
        assessed_total_tentative = EXCLUDED.assessed_total_tentative,
        assessed_total_final     = EXCLUDED.assessed_total_final,
        taxable_total            = EXCLUDED.taxable_total,
        exemption_total          = EXCLUDED.exemption_total,
        tax_class                = EXCLUDED.tax_class,
        building_class           = EXCLUDED.building_class,
        owner                    = EXCLUDED.owner,
        zoning                   = EXCLUDED.zoning,
        protest_code             = EXCLUDED.protest_code,
        protest_code_2           = EXCLUDED.protest_code_2,
        attorney_group           = EXCLUDED.attorney_group,
        attorney_group_2         = EXCLUDED.attorney_group_2,
        units                    = EXCLUDED.units,
        residential_units        = EXCLUDED.residential_units,
        gross_sqft               = EXCLUDED.gross_sqft,
        residential_sqft         = EXCLUDED.residential_sqft,
        retail_sqft              = EXCLUDED.retail_sqft,
        office_sqft              = EXCLUDED.office_sqft,
        garage_sqft              = EXCLUDED.garage_sqft,
        stories                  = EXCLUDED.stories,
        land_area                = EXCLUDED.land_area,
        year_built               = EXCLUDED.year_built,
        year_altered_1           = EXCLUDED.year_altered_1,
        year_altered_2           = EXCLUDED.year_altered_2,
        coop_number              = EXCLUDED.coop_number,
        condo_number             = EXCLUDED.condo_number,
        apportionment_date       = EXCLUDED.apportionment_date,
        new_lot                  = EXCLUDED.new_lot,
        building_in_progress     = EXCLUDED.building_in_progress,
        source_dataset           = EXCLUDED.source_dataset,
        ingested_at              = NOW()
""")


def record_params(rec, tax_year: str, period: str) -> dict[str, Any]:
    return {
        "bbl": rec.bbl,
        "tax_year": rec.tax_year or tax_year,
        "period": rec.period or period,
        "market_value": rec.market_value,
        "market_value_final": rec.market_value_final,
        "market_value_tentative": rec.market_value_tentative,
        "market_value_prior_year": rec.market_value_prior_year,
        "assessed_total": rec.assessed_total,
        "assessed_total_tentative": rec.assessed_total_tentative,
        "assessed_total_final": rec.assessed_total_final,
        "taxable_total": rec.taxable_total,
        "exemption_total": rec.exemption_total,
        "tax_class": rec.tax_class,
        "building_class": rec.building_class,
        "owner": rec.owner,
        "zoning": rec.zoning,
        "protest_code": rec.protest_code,
        "protest_code_2": rec.protest_code_2,
        "attorney_group": rec.attorney_group,
        "attorney_group_2": rec.attorney_group_2,
        "units": rec.units,
        "residential_units": rec.residential_units,
        "gross_sqft": rec.gross_sqft,
        "residential_sqft": rec.residential_sqft,
        "retail_sqft": rec.retail_sqft,
        "office_sqft": rec.office_sqft,
        "garage_sqft": rec.garage_sqft,
        "stories": rec.stories,
        "land_area": rec.land_area,
        "year_built": rec.year_built,
        "year_altered_1": rec.year_altered_1,
        "year_altered_2": rec.year_altered_2,
        "coop_number": rec.coop_number,
        "condo_number": rec.condo_number,
        "apportionment_date": rec.apportionment_date,
        "new_lot": rec.new_lot,
        "building_in_progress": rec.building_in_progress,
        "source_dataset": _DOF_DATASET,
    }


def portfolio_bbls(session: Session) -> list[str]:
    rows = session.execute(text("SELECT bbl FROM buildings WHERE bbl IS NOT NULL")).fetchall()
    return [str(r.bbl) for r in rows]


def summarise(records: Iterable, sample_limit: int = 5) -> dict[str, Any]:
    recs = list(records)
    with_protest = sum(1 for r in recs if r.filed_protest)
    with_attorney = sum(1 for r in recs if r.attorney_group)
    with_value = sum(1 for r in recs if r.market_value)
    rollups = sum(1 for r in recs if r.rollup_key)
    return {
        "records": len(recs),
        "with_market_value": with_value,
        "with_protest": with_protest,
        "with_attorney_group": with_attorney,
        "with_rollup_key": rollups,
        "sample": [
            {
                "bbl": r.bbl,
                "tax_class": r.tax_class,
                "building_class": r.building_class,
                "market_value": r.market_value,
                "units": r.units,
                "attorney_group": r.attorney_group,
                "rollup_key": r.rollup_key,
            }
            for r in recs[:sample_limit]
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("portfolio", "citywide"), default="portfolio")
    parser.add_argument("--tax-year", default=DEFAULT_TAX_YEAR)
    parser.add_argument("--period", default=PERIOD_FINAL)
    parser.add_argument("--tax-class", default=None,
                        help="citywide only, e.g. 2 to load class 2 stock")
    parser.add_argument("--limit", type=int, default=None,
                        help="stop after N records (preview aid)")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-execute", action="store_true")
    parser.add_argument("--indent", type=int, default=None)
    args = parser.parse_args()

    execute = args.execute and args.confirm_execute
    client = DOFAssessmentClient(tax_year=args.tax_year)
    engine = create_engine(get_sync_url())
    report: dict[str, Any] = {
        "dataset": _DOF_DATASET,
        "tax_year": args.tax_year,
        "period": args.period,
        "scope": args.scope,
        "app_token": bool(os.environ.get("NYC_OPEN_DATA_APP_TOKEN")),
    }

    with Session(engine) as session:
        if args.scope == "portfolio":
            bbls = portfolio_bbls(session)
            report["bbls_requested"] = len(bbls)
            records = list(client.get_assessments_batch(bbls, period=args.period).values())
        else:
            where = f"curtaxclass like '{args.tax_class}%'" if args.tax_class else None
            records = []
            for rec in client.iter_roll(period=args.period, where=where):
                records.append(rec)
                if args.limit and len(records) >= args.limit:
                    break

        if args.limit:
            records = records[: args.limit]

        report.update(summarise(records))

        if execute:
            written = 0
            for rec in records:
                session.execute(UPSERT, record_params(rec, args.tax_year, args.period))
                written += 1
            session.commit()
            report["executed"] = True
            report["rows_written"] = written
        else:
            report["executed"] = False
            report["note"] = (
                "DRY RUN — nothing written. Re-run with --execute --confirm-execute."
            )

    print(json.dumps(report, indent=args.indent, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
