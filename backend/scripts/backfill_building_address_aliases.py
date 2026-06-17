"""Backfill building_address_aliases from HPD registration ranges.

Dry-run by default. Execute only after reviewing the preview:

    python scripts/backfill_building_address_aliases.py
    python scripts/backfill_building_address_aliases.py --execute --confirm-execute
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from src.db.session import get_sync_url  # noqa: E402
from src.tasks.ingest import DATASETS, SOCRATA_BASE, _compute_bbl  # noqa: E402
from src.services.address_aliases import (  # noqa: E402
    address_alias_table_exists_sync,
    build_hpd_registration_aliases,
    upsert_building_address_aliases_sync,
)


def fetch_registrations(limit: int | None = None) -> list[dict]:
    params = {
        "$select": (
            "boroid,block,lot,buildingid,bin,registrationid,housenumber,"
            "lowhousenumber,highhousenumber,streetname,lastregistrationdate"
        ),
        "$order": "lastregistrationdate DESC",
        "$limit": 50000,
        "$offset": 0,
    }
    token = os.environ.get("NYC_OPEN_DATA_APP_TOKEN", "")
    headers = {"X-App-Token": token} if token else {}
    url = f"{SOCRATA_BASE}/{DATASETS['hpd_registrations']}.json"
    records: list[dict] = []
    while True:
        response = requests.get(url, params=params, headers=headers, timeout=(30, 120))
        response.raise_for_status()
        page = response.json()
        if not page:
            break
        records.extend(page)
        if limit and len(records) >= limit:
            return records[:limit]
        params["$offset"] += params["$limit"]
    return records


def preview_or_backfill(*, execute: bool, limit: int | None) -> dict[str, object]:
    engine = create_engine(get_sync_url())
    with Session(engine) as session:
        if not address_alias_table_exists_sync(session):
            raise RuntimeError("building_address_aliases table is missing; run Alembic migration first")

        existing_bbls = {
            str(row[0])
            for row in session.execute(text("SELECT bbl FROM buildings")).fetchall()
        }
        registrations = fetch_registrations(limit=limit)
        seen_bbls: set[str] = set()
        generated_aliases = 0
        matched_buildings = 0
        skipped_missing_building = 0
        samples: list[dict[str, object]] = []

        for reg in registrations:
            bbl = _compute_bbl(reg.get("boroid"), reg.get("block"), reg.get("lot"))
            if not bbl or bbl in seen_bbls:
                continue
            seen_bbls.add(bbl)
            if bbl not in existing_bbls:
                skipped_missing_building += 1
                continue
            aliases = build_hpd_registration_aliases(
                house_number=reg.get("housenumber"),
                low_house_number=reg.get("lowhousenumber"),
                high_house_number=reg.get("highhousenumber"),
                street_name=reg.get("streetname"),
                registration_id=reg.get("registrationid"),
                hpd_building_id=reg.get("buildingid"),
            )
            if not aliases:
                continue
            matched_buildings += 1
            generated_aliases += len(aliases)
            if len(samples) < 10:
                samples.append({
                    "bbl": bbl,
                    "aliases": [alias.display_address for alias in aliases[:12]],
                })
            if execute:
                upsert_building_address_aliases_sync(
                    session,
                    bbl=bbl,
                    bin_value=reg.get("bin") or reg.get("buildingid"),
                    aliases=aliases,
                )

        if execute:
            session.commit()

        return {
            "execute": execute,
            "registrations_reviewed": len(registrations),
            "matched_buildings": matched_buildings,
            "generated_aliases": generated_aliases,
            "skipped_missing_building": skipped_missing_building,
            "sample": samples,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-execute", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    if args.execute and not args.confirm_execute:
        raise SystemExit("Execution requires --execute --confirm-execute")

    result = preview_or_backfill(execute=args.execute, limit=args.limit)
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
