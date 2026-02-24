"""
Fix unit_count and building_type for all buildings using PLUTO data.

Root cause: PLUTO API returns BBLs as "1000050010.00000000" (float string)
but buildings table stores them as "1000050010" (integer string).
The ingestion script never matched them, so unit_count stayed NULL.

This script:
1. Fetches PLUTO data (unitsres, bldgclass, yearbuilt, assesstot)
2. Strips the decimal from PLUTO BBLs
3. Updates buildings in batch

Run locally, then sync changes to Railway.
"""
import os, sys, time, logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

if not os.environ.get("DATABASE_URL"):
    os.environ["DATABASE_URL"] = "postgresql+psycopg://postgres:postgres@localhost:5432/hpd_leads"

import requests
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from src.db.session import get_sync_url

PLUTO_URL = "https://data.cityofnewyork.us/resource/64uk-42ks.json"
BATCH = 5000
APP_TOKEN = os.environ.get("NYC_OPEN_DATA_APP_TOKEN", "")


def fetch_pluto():
    """Fetch all PLUTO records with BBL, unitsres, bldgclass, yearbuilt, assesstot."""
    all_records = []
    offset = 0
    headers = {"X-App-Token": APP_TOKEN} if APP_TOKEN else {}
    logger.info("Fetching PLUTO data from NYC Open Data...")

    while True:
        params = {
            "$select": "bbl,unitsres,bldgclass,yearbuilt,assesstot,ct2010,cd,council",
            "$limit": BATCH,
            "$offset": offset,
        }
        resp = requests.get(PLUTO_URL, params=params, headers=headers, timeout=60)
        resp.raise_for_status()
        chunk = resp.json()
        if not chunk:
            break
        all_records.extend(chunk)
        offset += len(chunk)
        if offset % 50000 == 0:
            logger.info(f"  Fetched {offset:,} PLUTO records...")
        if len(chunk) < BATCH:
            break

    logger.info(f"  Total PLUTO records: {len(all_records):,}")
    return all_records


def normalize_bbl(bbl_str: str) -> str:
    """Convert '1000050010.00000000' → '1000050010'."""
    try:
        return str(int(float(bbl_str)))
    except (ValueError, TypeError):
        return bbl_str.strip() if bbl_str else ""


def classify_building_type(bldgclass: str) -> str:
    """Map PLUTO building class code to a human-readable type."""
    if not bldgclass:
        return None
    code = bldgclass.upper()
    if code.startswith("D"):
        return "elevator_apartment"
    if code.startswith("C"):
        return "walkup_apartment"
    if code.startswith("A") or code.startswith("B"):
        return "residential_small"
    if code.startswith("R"):
        return "condo"
    if code.startswith("K") or code.startswith("RR"):
        return "coop"
    if code.startswith("S"):
        return "mixed_use"
    if code.startswith("E") or code.startswith("F") or code.startswith("G"):
        return "commercial"
    return "other"


def main():
    pluto_raw = fetch_pluto()

    # Build lookup dict with normalized BBLs
    pluto_by_bbl = {}
    for p in pluto_raw:
        raw_bbl = p.get("bbl", "")
        if raw_bbl:
            normalized = normalize_bbl(raw_bbl)
            if normalized:
                pluto_by_bbl[normalized] = p

    logger.info(f"PLUTO lookup built: {len(pluto_by_bbl):,} unique BBLs")

    engine = create_engine(get_sync_url())
    session = Session(engine)

    # Fetch all BBLs from buildings
    bbls = [r[0] for r in session.execute(text("SELECT bbl FROM buildings ORDER BY bbl")).fetchall()]
    logger.info(f"Buildings to update: {len(bbls):,}")

    matched = 0
    unmatched = 0
    update_batch = []
    start = time.time()

    for bbl in bbls:
        pluto = pluto_by_bbl.get(bbl)
        if pluto:
            matched += 1
            units = None
            if pluto.get("unitsres"):
                try:
                    units = int(float(pluto["unitsres"]))
                except (ValueError, TypeError):
                    pass

            year_built = None
            if pluto.get("yearbuilt"):
                try:
                    year_built = int(float(pluto["yearbuilt"]))
                except (ValueError, TypeError):
                    pass

            assessed_value = None
            if pluto.get("assesstot"):
                try:
                    assessed_value = float(pluto["assesstot"])
                except (ValueError, TypeError):
                    pass

            bldg_class = pluto.get("bldgclass")
            bldg_type = classify_building_type(bldg_class)

            update_batch.append({
                "bbl": bbl,
                "units": units,
                "bldg_class": bldg_class,
                "bldg_type": bldg_type,
                "year_built": year_built,
                "assessed_value": assessed_value,
                "council": pluto.get("council"),
                "cd": pluto.get("cd"),
                "census": pluto.get("ct2010"),
            })
        else:
            unmatched += 1

        if len(update_batch) >= BATCH:
            session.execute(
                text("""UPDATE buildings SET
                    unit_count = :units,
                    building_class = :bldg_class,
                    building_type = :bldg_type,
                    year_built = :year_built,
                    assessed_value = :assessed_value,
                    council_district = :council,
                    community_board = :cd,
                    census_tract = :census,
                    updated_at = now()
                WHERE bbl = :bbl"""),
                update_batch,
            )
            session.commit()
            elapsed = time.time() - start
            logger.info(f"  Updated {matched:,} buildings ({elapsed:.0f}s) — {unmatched:,} unmatched so far")
            update_batch = []

    if update_batch:
        session.execute(
            text("""UPDATE buildings SET
                unit_count = :units,
                building_class = :bldg_class,
                building_type = :bldg_type,
                year_built = :year_built,
                assessed_value = :assessed_value,
                council_district = :council,
                community_board = :cd,
                census_tract = :census,
                updated_at = now()
            WHERE bbl = :bbl"""),
            update_batch,
        )
        session.commit()

    session.close()
    elapsed = time.time() - start
    logger.info(f"Done in {elapsed:.1f}s: {matched:,} matched, {unmatched:,} unmatched of {len(bbls):,} total")
    logger.info(f"Match rate: {100*matched/max(len(bbls),1):.1f}%")


if __name__ == "__main__":
    main()
