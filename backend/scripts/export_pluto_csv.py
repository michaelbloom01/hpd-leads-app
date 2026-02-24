"""
Export PLUTO data for buildings we have in the DB as a CSV for bulk COPY upload.
Only exports fields we need: bbl, unit_count, building_class, building_type, year_built, assessed_value.
Pure requests, no psycopg needed.
"""
import os, sys, time, csv, logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

import requests

PLUTO_URL = "https://data.cityofnewyork.us/resource/64uk-42ks.json"
APP_TOKEN = os.environ.get("NYC_OPEN_DATA_APP_TOKEN", "")
BATCH = 5000
OUTPUT_CSV = str(Path(__file__).parent / "pluto_data.csv")


def normalize_bbl(v):
    try:
        return str(int(float(v)))
    except Exception:
        return str(v).strip()


def get_building_type(code):
    if not code:
        return ""
    c = code.upper()
    if c.startswith("D"): return "elevator_apartment"
    if c.startswith("C"): return "walkup_apartment"
    if c.startswith("A") or c.startswith("B"): return "residential_small"
    if c.startswith("R"): return "condo"
    if c.startswith("S"): return "mixed_use"
    if c.startswith("E") or c.startswith("F") or c.startswith("G"): return "commercial"
    return "other"


def main():
    headers = {"X-App-Token": APP_TOKEN} if APP_TOKEN else {}
    all_records = []
    offset = 0
    start = time.time()
    logger.info("Fetching PLUTO data...")

    while True:
        resp = requests.get(PLUTO_URL, params={
            "$select": "bbl,unitsres,bldgclass,yearbuilt,assesstot",
            "$limit": BATCH,
            "$offset": offset,
        }, headers=headers, timeout=60)
        resp.raise_for_status()
        chunk = resp.json()
        if not chunk:
            break
        all_records.extend(chunk)
        offset += len(chunk)
        if offset % 100000 == 0:
            logger.info(f"  {offset:,} records...")
        if len(chunk) < BATCH:
            break

    logger.info(f"Fetched {len(all_records):,} PLUTO records in {time.time()-start:.1f}s")

    written = 0
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["bbl", "unit_count", "building_class", "building_type", "year_built", "assessed_value"])
        
        for p in all_records:
            bbl = normalize_bbl(p.get("bbl", ""))
            if not bbl:
                continue

            units = ""
            if p.get("unitsres"):
                try:
                    units = str(int(float(p["unitsres"])))
                except Exception:
                    pass

            year = ""
            if p.get("yearbuilt"):
                try:
                    y = int(float(p["yearbuilt"]))
                    if 1600 <= y <= 2030:
                        year = str(y)
                except Exception:
                    pass

            val = ""
            if p.get("assesstot"):
                try:
                    val = str(float(p["assesstot"]))
                except Exception:
                    pass

            bldg_class = p.get("bldgclass", "")
            bldg_type = get_building_type(bldg_class)

            writer.writerow([bbl, units, bldg_class, bldg_type, year, val])
            written += 1

    size_mb = os.path.getsize(OUTPUT_CSV) / 1024 / 1024
    logger.info(f"Written {written:,} rows to {OUTPUT_CSV} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
