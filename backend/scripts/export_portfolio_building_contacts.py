"""Read-only portfolio building/contact export.

Exports the same HPD/DOS contact roster used by Building Detail's
"People & Companies" section for every building in a PM portfolio.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db.session import get_session_factory, shutdown_engine  # noqa: E402
from src.services.contact_roster import get_building_contacts  # noqa: E402


VENTURE_KEYS = {
    "VENTURENYPROPERTYMANAGEMENT",
    "VENTURENYPROPERTYMANAGEMENTLLC",
}


def normalize_company_key(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def json_default(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _flatten_contact_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for building in records:
        contacts = building.get("contacts") or []
        if not contacts:
            rows.append({
                "bbl": building.get("bbl"),
                "address": building.get("address"),
                "borough": building.get("borough"),
                "zip_code": building.get("zip_code"),
                "unit_count": building.get("unit_count"),
                "churn_score": building.get("churn_score"),
                "churn_category": building.get("churn_category"),
                "management_company": building.get("management_company"),
                "corporate_owner": building.get("corporate_owner"),
                "dos_contacts_status": building.get("dos_contacts_status"),
                "contact_name": None,
                "contact_role": None,
                "contact_source": None,
                "contact_updated": None,
                "contact_address": None,
                "contact_confidence": None,
                "contact_source_record_id": None,
                "contact_source_url": None,
                "board_role": None,
                "is_decision_maker": None,
            })
            continue
        for contact in contacts:
            rows.append({
                "bbl": building.get("bbl"),
                "address": building.get("address"),
                "borough": building.get("borough"),
                "zip_code": building.get("zip_code"),
                "unit_count": building.get("unit_count"),
                "churn_score": building.get("churn_score"),
                "churn_category": building.get("churn_category"),
                "management_company": building.get("management_company"),
                "corporate_owner": building.get("corporate_owner"),
                "dos_contacts_status": building.get("dos_contacts_status"),
                "contact_name": contact.get("name"),
                "contact_role": contact.get("role"),
                "contact_source": contact.get("source"),
                "contact_updated": contact.get("as_of_date"),
                "contact_address": contact.get("address"),
                "contact_confidence": contact.get("confidence_hint") or "--",
                "contact_source_record_id": contact.get("source_record_id"),
                "contact_source_url": contact.get("source_url"),
                "board_role": contact.get("board_role"),
                "is_decision_maker": contact.get("is_decision_maker"),
            })
    return rows


async def build_portfolio_export(company: str, lead_id: str | None = None) -> dict[str, Any]:
    factory = get_session_factory()
    company_key = normalize_company_key(company)
    keys = sorted(VENTURE_KEYS if company_key in VENTURE_KEYS else {company_key})

    async with factory() as session:
        params: dict[str, Any] = {"keys": keys}
        lead_filter = ""
        if lead_id:
            params["lead_id"] = lead_id
            lead_filter = """
                OR EXISTS (
                    SELECT 1
                    FROM building_management bm
                    WHERE bm.bbl = b.bbl
                      AND bm.lead_id = :lead_id
                      AND bm.is_current = true
                )
            """

        result = await session.execute(
            text(f"""
                WITH agent_portfolio AS (
                    SELECT DISTINCT bc.bbl
                    FROM building_contacts bc
                    WHERE bc.contact_type = 'Agent'
                      AND regexp_replace(upper(coalesce(bc.corporation_name, '')), '[^A-Z0-9]', '', 'g')
                          = ANY(:keys)
                )
                SELECT
                    b.bbl, b.bin, b.address, b.borough, b.block, b.lot, b.zip_code,
                    b.building_class, b.building_type, b.unit_count, b.year_built,
                    b.assessed_value, b.churn_score, b.churn_category,
                    b.churn_breakdown, b.key_signal, b.outreach_status,
                    b.last_scored_at, b.updated_at
                FROM buildings b
                WHERE b.bbl IN (SELECT bbl FROM agent_portfolio)
                {lead_filter}
                ORDER BY b.address ASC, b.bbl ASC
            """),
            params,
        )
        building_rows = [dict(row._mapping) for row in result]

        records: list[dict[str, Any]] = []
        source_counts: dict[str, int] = {}
        total_contacts = 0
        dos_status_counts: dict[str, int] = {}
        for building in building_rows:
            contacts, meta = await get_building_contacts(
                session=session,
                bbl=str(building["bbl"]),
                building_address=building.get("address"),
            )
            for contact in contacts:
                source = str(contact.get("source") or "Unknown")
                source_counts[source] = source_counts.get(source, 0) + 1
            total_contacts += len(contacts)
            dos_status = str(meta.get("dos_contacts_status") or "unknown")
            dos_status_counts[dos_status] = dos_status_counts.get(dos_status, 0) + 1
            records.append({
                **building,
                "management_company": meta.get("management_company"),
                "corporate_owner": meta.get("corporate_owner"),
                "dos_contacts_status": dos_status,
                "dos_contacts_is_stale": bool(meta.get("dos_contacts_is_stale")),
                "dos_contacts_last_refreshed_at": meta.get("dos_contacts_last_refreshed_at"),
                "contacts": contacts,
                "contact_count": len(contacts),
                "contact_source_count": len({c.get("source") for c in contacts if c.get("source")}),
            })

        return {
            "run_type": "portfolio_building_contacts_export",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dry_run": True,
            "mutations_planned": 0,
            "company": company,
            "company_keys": keys,
            "portfolio_definition": (
                "Buildings whose HPD Registration Agent corporation name normalizes to the company key"
                + (" plus explicit lead_id current links." if lead_id else ".")
            ),
            "lead_id": lead_id,
            "building_count": len(records),
            "unit_count": sum(int(b.get("unit_count") or 0) for b in records),
            "contact_count": total_contacts,
            "contact_source_counts": dict(sorted(source_counts.items())),
            "dos_status_counts": dict(sorted(dos_status_counts.items())),
            "records": records,
            "contact_rows": _flatten_contact_rows(records),
        }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company", default="VENTURE NY PROPERTY MANAGEMENT LLC")
    parser.add_argument("--lead-id", default=None)
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--output-csv", type=Path, default=None)
    parser.add_argument("--indent", type=int, default=2)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    payload = await build_portfolio_export(company=args.company, lead_id=args.lead_id)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(payload, indent=args.indent, default=json_default),
            encoding="utf-8",
        )
    if args.output_csv:
        write_csv(args.output_csv, payload["contact_rows"])
    print(json.dumps(
        {key: value for key, value in payload.items() if key not in {"records", "contact_rows"}},
        indent=args.indent,
        default=json_default,
    ))
    await shutdown_engine()


if __name__ == "__main__":
    asyncio.run(main())
