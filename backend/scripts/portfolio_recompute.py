"""Recompute lead portfolio signatures, true building counts, and duplicate flags.

Three things, all derived from each lead's current BBL set:

  1. portfolio_signature -- order-independent hash of the BBL set. Two leads
     sharing one manage exactly the same buildings.
  2. true_building_count -- condo/co-op unit lots collapsed into their parent
     development via DOF coop_num / condo_number. DOF gives each unit its own
     tax lot, so a 200-unit condo can appear as 200 buildings.
  3. portfolio_size -- switched to the true building count, with the raw lot
     count preserved in portfolio_size_raw.

DUPLICATES ARE FLAGGED, NEVER MERGED. This script does not delete, retire or
supersede any lead. Merging is a separate decision and is deliberately not
implemented here.

THE portfolio_size SWITCH IS USER-VISIBLE. Every saved Smart List and bookmarked
filter URL was built against the raw lot count. After this runs, condo/co-op-heavy
managers will show sharply lower portfolio sizes -- a manager showing 200
buildings may show 3. portfolio_size_raw makes it reversible; the migration's
downgrade restores it.

Dry run by default:

    python scripts/portfolio_recompute.py
    python scripts/portfolio_recompute.py --limit 50 --indent 2

Execute (requires both flags):

    python scripts/portfolio_recompute.py --execute --confirm-execute
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db.session import get_sync_url  # noqa: E402
from src.transform.portfolio_dedup import (  # noqa: E402
    find_duplicate_leads,
    portfolio_signature,
)

DEFAULT_TAX_YEAR = "2027"
DEFAULT_PERIOD = "3"


def load_lead_bbls(session: Session, limit: int | None = None) -> dict[str, list[str]]:
    """Current BBLs per lead. Retired leads are excluded."""
    sql = """
        SELECT bm.lead_id, bm.bbl
        FROM building_management bm
        JOIN leads l ON l.lead_id = bm.lead_id
        WHERE bm.is_current = true
          AND l.retired_at IS NULL
    """
    rows = session.execute(text(sql)).fetchall()
    out: dict[str, list[str]] = {}
    for row in rows:
        out.setdefault(str(row.lead_id), []).append(str(row.bbl))
    if limit is not None:
        keys = sorted(out, key=lambda k: -len(out[k]))[:limit]
        out = {k: out[k] for k in keys}
    return out


def load_rollup_keys(
    session: Session, tax_year: str, period: str
) -> dict[str, str | None]:
    """BBL -> parent development key, for BBLs that have one."""
    rows = session.execute(
        text("""
            SELECT bbl, coop_number, condo_number
            FROM building_assessments
            WHERE tax_year = :ty AND period = :pd
              AND (coop_number IS NOT NULL OR condo_number IS NOT NULL)
        """),
        {"ty": tax_year, "pd": period},
    ).fetchall()
    keys: dict[str, str | None] = {}
    for row in rows:
        if row.coop_number:
            keys[str(row.bbl)] = f"coop:{row.coop_number}"
        elif row.condo_number:
            keys[str(row.bbl)] = f"condo:{row.condo_number}"
    return keys


def true_building_count(bbls: list[str], rollup: dict[str, str | None]) -> int:
    """Distinct developments plus standalone lots."""
    developments = set()
    standalone = 0
    for bbl in set(bbls):
        key = rollup.get(bbl)
        if key:
            developments.add(key)
        else:
            standalone += 1
    return len(developments) + standalone


def build_plan(
    lead_bbls: dict[str, list[str]], rollup: dict[str, str | None]
) -> list[dict[str, Any]]:
    plan = []
    for lead_id, bbls in lead_bbls.items():
        raw = len(set(bbls))
        true_count = true_building_count(bbls, rollup)
        plan.append({
            "lead_id": lead_id,
            "portfolio_signature": portfolio_signature(bbls),
            "portfolio_size_raw": raw,
            "true_building_count": true_count,
            "collapsed_by": raw - true_count,
        })
    plan.sort(key=lambda p: -p["portfolio_size_raw"])
    return plan


def summarise(plan: list[dict[str, Any]], duplicates: list[dict]) -> dict[str, Any]:
    collapsed = [p for p in plan if p["collapsed_by"] > 0]
    raw_total = sum(p["portfolio_size_raw"] for p in plan)
    true_total = sum(p["true_building_count"] for p in plan)
    return {
        "leads_examined": len(plan),
        "leads_with_signature": sum(1 for p in plan if p["portfolio_signature"]),
        "leads_affected_by_rollup": len(collapsed),
        "raw_lot_total": raw_total,
        "true_building_total": true_total,
        "lots_collapsed": raw_total - true_total,
        "largest_collapses": [
            {k: p[k] for k in ("lead_id", "portfolio_size_raw", "true_building_count")}
            for p in sorted(plan, key=lambda x: -x["collapsed_by"])[:10]
            if p["collapsed_by"] > 0
        ],
        "duplicate_groups": len(duplicates),
        "redundant_lead_rows": sum(g["redundant_rows"] for g in duplicates),
        "duplicates": [
            {"lead_ids": g["lead_ids"], "portfolio_size": g["portfolio_size"]}
            for g in duplicates[:25]
        ],
    }


def apply_plan(session: Session, plan: list[dict[str, Any]]) -> int:
    """
    Write signatures and counts.

    portfolio_size_raw is only ever written when NULL, so a rerun cannot
    overwrite the original raw count with an already-collapsed value.
    """
    updated = 0
    for entry in plan:
        session.execute(
            text("""
                UPDATE leads
                SET portfolio_signature    = :sig,
                    portfolio_signature_at = NOW(),
                    portfolio_size_raw     = COALESCE(portfolio_size_raw, :raw),
                    true_building_count    = :true_count,
                    portfolio_size         = :true_count
                WHERE lead_id = :lead_id
            """),
            {
                "sig": entry["portfolio_signature"],
                "raw": entry["portfolio_size_raw"],
                "true_count": entry["true_building_count"],
                "lead_id": entry["lead_id"],
            },
        )
        updated += 1
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tax-year", default=DEFAULT_TAX_YEAR)
    parser.add_argument("--period", default=DEFAULT_PERIOD)
    parser.add_argument("--limit", type=int, default=None,
                        help="only the N largest portfolios (preview aid)")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-execute", action="store_true")
    parser.add_argument("--indent", type=int, default=None)
    args = parser.parse_args()

    engine = create_engine(get_sync_url())
    with Session(engine) as session:
        lead_bbls = load_lead_bbls(session, args.limit)
        rollup = load_rollup_keys(session, args.tax_year, args.period)

        if not rollup:
            print(
                "WARNING: building_assessments has no rollup keys for "
                f"tax_year={args.tax_year} period={args.period}. Run the DOF "
                "backfill first, or true_building_count will equal the raw lot "
                "count and the rollup will look like a no-op.",
                file=sys.stderr,
            )

        plan = build_plan(lead_bbls, rollup)
        duplicates = find_duplicate_leads(lead_bbls)
        report = summarise(plan, duplicates)
        report["tax_year"] = args.tax_year
        report["period"] = args.period
        report["rollup_keys_loaded"] = len(rollup)

        if args.execute and args.confirm_execute:
            updated = apply_plan(session, plan)
            session.commit()
            report["executed"] = True
            report["leads_updated"] = updated
            report["note"] = (
                "portfolio_size now holds the true building count; the raw lot "
                "count is preserved in portfolio_size_raw. No lead was merged, "
                "retired or deleted."
            )
        else:
            report["executed"] = False
            report["note"] = (
                "DRY RUN — nothing written. Re-run with --execute "
                "--confirm-execute to apply."
            )

    print(json.dumps(report, indent=args.indent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
