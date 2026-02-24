"""
Fetch PLUTO data from NYC Open Data and write UPDATE SQL to a file.
No psycopg needed — pure requests + file output.
Then run the SQL via: psql -h localhost -U postgres -d hpd_leads -f pluto_updates.sql
"""
import os, sys, json, time, logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

import requests

PLUTO_URL = "https://data.cityofnewyork.us/resource/64uk-42ks.json"
APP_TOKEN = os.environ.get("NYC_OPEN_DATA_APP_TOKEN", "")
BATCH = 5000
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "pluto_updates.sql")


def normalize_bbl(v):
    """'1000050010.00000000' → '1000050010'"""
    try:
        return str(int(float(v)))
    except Exception:
        return str(v).strip()


def get_building_type(code):
    if not code:
        return None
    c = code.upper()
    if c.startswith("D"):
        return "elevator_apartment"
    if c.startswith("C"):
        return "walkup_apartment"
    if c.startswith("A") or c.startswith("B"):
        return "residential_small"
    if c.startswith("R"):
        return "condo"
    if c.startswith("S"):
        return "mixed_use"
    if c.startswith("E") or c.startswith("F") or c.startswith("G"):
        return "commercial"
    return "other"


def esc(v):
    """Escape a string value for SQL."""
    if v is None:
        return "NULL"
    return "'" + str(v).replace("'", "''") + "'"


def main():
    headers = {"X-App-Token": APP_TOKEN} if APP_TOKEN else {}
    all_records = []
    offset = 0
    start = time.time()
    logger.info("Fetching PLUTO data from NYC Open Data...")

    while True:
        params = {
            "$select": "bbl,unitsres,bldgclass,yearbuilt,assesstot,cd,council,ct2010",
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

    elapsed = time.time() - start
    logger.info(f"Total PLUTO records: {len(all_records):,} in {elapsed:.1f}s")

    # Build normalized lookup
    pluto_by_bbl = {}
    for p in all_records:
        raw = p.get("bbl")
        if raw:
            pluto_by_bbl[normalize_bbl(raw)] = p
    logger.info(f"PLUTO lookup: {len(pluto_by_bbl):,} unique BBLs")

    # Write SQL file
    matched = unmatched = 0
    logger.info(f"Writing SQL to {OUTPUT_FILE}...")
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("BEGIN;\n")
        count = 0

        for bbl, p in pluto_by_bbl.items():
            units = None
            if p.get("unitsres"):
                try:
                    units = int(float(p["unitsres"]))
                except Exception:
                    pass
            
            year_built = None
            if p.get("yearbuilt"):
                try:
                    year_built = int(float(p["yearbuilt"]))
                    if year_built < 1600 or year_built > 2030:
                        year_built = None
                except Exception:
                    pass

            assessed_value = None
            if p.get("assesstot"):
                try:
                    assessed_value = float(p["assesstot"])
                except Exception:
                    pass

            bldg_class = p.get("bldgclass")
            bldg_type = get_building_type(bldg_class)

            # Only update if we have at least units or bldg_class
            if units is None and bldg_class is None:
                unmatched += 1
                continue

            units_sql = str(units) if units is not None else "NULL"
            year_sql = str(year_built) if year_built is not None else "NULL"
            val_sql = str(assessed_value) if assessed_value is not None else "NULL"

            f.write(
                f"UPDATE buildings SET "
                f"unit_count={units_sql}, "
                f"building_class={esc(bldg_class)}, "
                f"building_type={esc(bldg_type)}, "
                f"year_built={year_sql}, "
                f"assessed_value={val_sql}, "
                f"council_district={esc(p.get('council'))}, "
                f"community_board={esc(p.get('cd'))}, "
                f"census_tract={esc(p.get('ct2010'))}, "
                f"updated_at=now() "
                f"WHERE bbl={esc(bbl)};\n"
            )
            matched += 1
            count += 1
            if count % 50000 == 0:
                f.write("COMMIT;\nBEGIN;\n")
                logger.info(f"  Written {count:,} updates...")

        f.write("COMMIT;\n")

    logger.info(f"SQL file written: {matched:,} updates, {unmatched:,} skipped (no data)")
    logger.info(f"Run via: psql -h localhost -U postgres -d hpd_leads -f {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
