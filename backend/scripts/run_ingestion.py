"""Standalone ingestion runner — calls task logic directly without Celery/Redis.

Usage:
    python scripts/run_ingestion.py [--tasks buildings,complaints,violations,...]

If no --tasks argument, runs all ingestion tasks in dependency order.
"""
import os
import sys
import time
import logging
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

if not os.environ.get("DATABASE_URL"):
    logger.warning("DATABASE_URL not set; defaulting to localhost for development")
    os.environ["DATABASE_URL"] = "postgresql+psycopg://postgres:postgres@localhost:5432/hpd_leads"

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from src.db.session import get_sync_url
from src.services.address_aliases import (
    address_alias_table_exists_sync,
    build_hpd_registration_aliases,
    upsert_building_address_aliases_sync,
)

SOCRATA_BASE = "https://data.cityofnewyork.us/resource"
APP_TOKEN = os.environ.get("NYC_OPEN_DATA_APP_TOKEN", "")
import requests

DATASETS = {
    "hpd_complaints": "ygpa-z7cr",
    "acris_legals": "8h5j-fqxa",
    "acris_master": "bnx9-e6tj",
    "dob_permits_bis": "ipu4-2vj7",
    "hpd_litigation": "59kj-x8nc",
    "emergency_repairs": "24cj-meh5",
    "aep_designations": "hcir-3275",
    "eviction_filings": "6z8x-wfk4",
    "energy_grades": "355w-xvp2",
    "facade_inspections": "xubg-57si",
    "pad": "bc8t-ecyu",
    "hpd_violations": "wvxf-dwi5",
    "hpd_registrations": "tesw-yqqr",
    "hpd_contacts": "feu5-w2e2",
    "pluto": "64uk-42ks",
}


def get_session():
    engine = create_engine(get_sync_url())
    return Session(engine)


def socrata_fetch(dataset_id, params, label="", max_records=200000):
    url = f"{SOCRATA_BASE}/{dataset_id}.json"
    headers = {"X-App-Token": APP_TOKEN} if APP_TOKEN else {}
    all_records = []
    offset = params.get("$offset", 0)
    limit = min(params.get("$limit", 50000), 50000)

    while True:
        params["$offset"] = offset
        params["$limit"] = limit
        for attempt in range(3):
            try:
                resp = requests.get(url, params=params, headers=headers, timeout=120)
                resp.raise_for_status()
                break
            except Exception as e:
                if attempt == 2:
                    raise
                logger.warning(f"Retry {attempt+1} for {label}: {e}")
                time.sleep(2)

        batch = resp.json()
        if not batch:
            break
        all_records.extend(batch)
        logger.info(f"  {label}: fetched {len(all_records)} records so far...")
        if len(batch) < limit:
            break
        if len(all_records) >= max_records:
            logger.info(f"  {label}: hit max_records cap of {max_records}")
            break
        offset += limit

    return all_records[:max_records]


def compute_bbl(boro_id, block, lot):
    try:
        b = str(int(str(boro_id).strip()))
        bl = str(int(str(block).strip())).zfill(5)
        lt = str(int(str(lot).strip())).zfill(4)
        return f"{b}{bl}{lt}"
    except (ValueError, TypeError):
        return None


def log_quality(session, source, fetched, matched, rejected, inserted):
    match_rate = matched / fetched if fetched > 0 else 0.0
    session.execute(
        text("""
            INSERT INTO data_quality_log
                (source_name, run_timestamp, records_fetched, records_matched,
                 records_rejected, records_inserted, match_rate, volume_anomaly,
                 notes, created_at, updated_at)
            VALUES (:source, now(), :fetched, :matched, :rejected, :inserted,
                    :match_rate, false, '', now(), now())
        """),
        {"source": source, "fetched": fetched, "matched": matched,
         "rejected": rejected, "inserted": inserted, "match_rate": match_rate},
    )


def create_job(session, source):
    result = session.execute(
        text("""
            INSERT INTO ingestion_jobs (job_type, source, status, started_at, created_at, updated_at)
            VALUES ('ingest', :source, 'running', now(), now(), now()) RETURNING id
        """),
        {"source": source},
    )
    session.flush()
    return result.scalar_one()


def finish_job(session, job_id, status, total, succeeded, failed, error=None):
    session.execute(
        text("""
            UPDATE ingestion_jobs
            SET status=:status, total=:total, succeeded=:succeeded,
                failed=:failed, error=:error, finished_at=now(), updated_at=now()
            WHERE id=:job_id
        """),
        {"job_id": job_id, "status": status, "total": total,
         "succeeded": succeeded, "failed": failed, "error": error},
    )


def run_pad(session):
    logger.info("=== PAD Addresses (BIN-to-BBL crosswalk) ===")
    job_id = create_job(session, "pad")
    session.commit()
    records = socrata_fetch(DATASETS["pad"], {
        "$select": "bin,bbl,stname,boro",
        "$where": "bin IS NOT NULL AND bbl IS NOT NULL",
    }, "PAD")
    inserted = 0
    for r in records:
        session.execute(
            text("""INSERT INTO pad_addresses (bin, bbl, address, borough, created_at, updated_at)
                    VALUES (:bin, :bbl, :addr, :boro, now(), now()) ON CONFLICT DO NOTHING"""),
            {"bin": r.get("bin"), "bbl": str(r.get("bbl", "")),
             "addr": r.get("stname"), "boro": r.get("boro")},
        )
        inserted += 1
        if inserted % 10000 == 0:
            session.commit()
    session.commit()
    log_quality(session, "pad", len(records), len(records), 0, inserted)
    finish_job(session, job_id, "completed", len(records), inserted, 0)
    session.commit()
    logger.info(f"PAD: {inserted} inserted")
    return inserted


def run_buildings(session):
    logger.info("=== Buildings (HPD registrations + contacts + PLUTO) ===")
    job_id = create_job(session, "hpd_buildings")
    session.commit()

    registrations = socrata_fetch(DATASETS["hpd_registrations"], {
        "$select": "boroid,block,lot,buildingid,bin,registrationid,housenumber,"
                   "lowhousenumber,highhousenumber,streetname,zip,boro,"
                   "lastregistrationdate,registrationenddate",
        "$order": "lastregistrationdate DESC",
    }, "HPD Registrations")

    contacts = socrata_fetch(DATASETS["hpd_contacts"], {
        "$select": "registrationcontactid,registrationid,type,"
                   "contactdescription,corporationname,firstname,lastname,title,"
                   "businesshousenumber,businessstreetname,businessapartment,"
                   "businesscity,businessstate,businesszip",
    }, "HPD Contacts")
    contacts_by_reg = {}
    for c in contacts:
        rid = c.get("registrationid")
        if rid:
            contacts_by_reg.setdefault(rid, []).append(c)

    pluto_data = socrata_fetch(DATASETS["pluto"], {
        "$select": "bbl,cd,council,ct2010,assesstot,unitsres,yearbuilt,bldgclass",
    }, "PLUTO")
    # PLUTO returns BBLs as floats e.g. "1000050010.00000000" — normalize to integer string
    def _norm_bbl(v):
        try:
            return str(int(float(v)))
        except (ValueError, TypeError):
            return str(v).strip()
    pluto_by_bbl = {_norm_bbl(p.get("bbl", "")): p for p in pluto_data if p.get("bbl")}

    inserted = matched = rejected = 0
    buildings_seen = set()
    building_batch = []
    contact_batch = []
    alias_batch = []
    write_address_aliases = address_alias_table_exists_sync(session)
    BATCH_SIZE = 5000

    def flush_buildings(batch):
        if not batch:
            return
        session.execute(
            text("""INSERT INTO buildings (
                        bbl, bin, address, borough, block, lot, zip_code,
                        building_class, unit_count, year_built, assessed_value,
                        council_district, community_board, census_tract, nta,
                        created_at, updated_at
                    ) VALUES (
                        :bbl, :bin, :address, :borough, :block, :lot, :zip,
                        :bldg_class, :units, :year_built, :assessed_value,
                        :council, :cd, :census, :nta, now(), now()
                    ) ON CONFLICT (bbl) DO UPDATE SET
                        address = EXCLUDED.address,
                        unit_count = COALESCE(EXCLUDED.unit_count, buildings.unit_count),
                        building_class = COALESCE(EXCLUDED.building_class, buildings.building_class),
                        year_built = COALESCE(EXCLUDED.year_built, buildings.year_built),
                        assessed_value = COALESCE(EXCLUDED.assessed_value, buildings.assessed_value),
                        council_district = COALESCE(EXCLUDED.council_district, buildings.council_district),
                        community_board = COALESCE(EXCLUDED.community_board, buildings.community_board),
                        updated_at = now()"""),
            batch,
        )

    def flush_contacts(batch):
        if not batch:
            return
        try:
            session.execute(
                text("""INSERT INTO building_contacts (
                        bbl, registration_contact_id, registration_id,
                        contact_type, description, corporation_name,
                        first_name, last_name, title,
                        business_address, business_city, business_state, business_zip,
                        created_at, updated_at
                    ) VALUES (
                        :bbl, :contact_id, :reg_id, :type, :desc, :corp,
                        :first, :last, :title, :addr, :city, :state, :zip, now(), now()
                    ) ON CONFLICT DO NOTHING"""),
                batch,
            )
        except Exception as e:
            session.rollback()
            logger.warning(f"Contact batch insert failed, skipping {len(batch)} rows: {e}")

    def flush_aliases(batch):
        if not batch or not write_address_aliases:
            return
        for item in batch:
            upsert_building_address_aliases_sync(
                session,
                bbl=item["bbl"],
                bin_value=item["bin"],
                aliases=item["aliases"],
            )

    for reg in registrations:
        bbl = compute_bbl(reg.get("boroid"), reg.get("block"), reg.get("lot"))
        if not bbl or bbl in buildings_seen:
            if not bbl:
                rejected += 1
            continue
        buildings_seen.add(bbl)
        matched += 1
        pluto = pluto_by_bbl.get(bbl, {})
        address = f"{reg.get('housenumber', '')} {reg.get('streetname', '')}".strip()
        bin_value = reg.get("bin") or reg.get("buildingid")

        building_batch.append({
            "bbl": bbl, "bin": bin_value,
            "address": address, "borough": reg.get("boro"),
            "block": reg.get("block"), "lot": reg.get("lot"), "zip": reg.get("zip"),
            "bldg_class": pluto.get("bldgclass"),
            "units": int(pluto["unitsres"]) if pluto.get("unitsres") else None,
            "year_built": int(pluto["yearbuilt"]) if pluto.get("yearbuilt") else None,
            "assessed_value": float(pluto["assesstot"]) if pluto.get("assesstot") else None,
            "council": pluto.get("council"), "cd": pluto.get("cd"),
            "census": pluto.get("ct2010"), "nta": None,
        })
        if write_address_aliases:
            alias_batch.append({
                "bbl": bbl,
                "bin": bin_value,
                "aliases": build_hpd_registration_aliases(
                    house_number=reg.get("housenumber"),
                    low_house_number=reg.get("lowhousenumber"),
                    high_house_number=reg.get("highhousenumber"),
                    street_name=reg.get("streetname"),
                    registration_id=reg.get("registrationid"),
                    hpd_building_id=reg.get("buildingid"),
                ),
            })
        inserted += 1

        reg_id = reg.get("registrationid")
        for c in contacts_by_reg.get(reg_id, []):
            contact_batch.append({
                "bbl": bbl, "contact_id": c.get("registrationcontactid"),
                "reg_id": reg_id, "type": c.get("type"),
                "desc": c.get("contactdescription"), "corp": c.get("corporationname"),
                "first": c.get("firstname"), "last": c.get("lastname"),
                "title": c.get("title"),
                "addr": f"{c.get('businesshousenumber', '')} {c.get('businessstreetname', '')}".strip(),
                "city": c.get("businesscity"), "state": c.get("businessstate"),
                "zip": c.get("businesszip"),
            })

        if len(building_batch) >= BATCH_SIZE:
            flush_buildings(building_batch)
            flush_contacts(contact_batch)
            flush_aliases(alias_batch)
            session.commit()
            logger.info(f"  Buildings progress: {inserted}")
            building_batch = []
            contact_batch = []
            alias_batch = []

    # Flush remaining
    flush_buildings(building_batch)
    flush_contacts(contact_batch)
    flush_aliases(alias_batch)

    session.commit()
    log_quality(session, "hpd_buildings", len(registrations), matched, rejected, inserted)
    finish_job(session, job_id, "completed", len(registrations), inserted, rejected)
    session.commit()
    logger.info(f"Buildings: {inserted} inserted, {rejected} rejected")
    return inserted


def load_known_bbls(session) -> set:
    """Load all known BBLs into memory to avoid per-row DB lookups."""
    rows = session.execute(text("SELECT bbl FROM buildings")).fetchall()
    return {r[0] for r in rows}


def run_signal_task(session, source, dataset_key, select, insert_sql, row_mapper,
                    order="", use_bbl=True, needs_pad=False, known_bbls=None):
    logger.info(f"=== {source} ===")
    job_id = create_job(session, source)
    session.commit()

    if known_bbls is None:
        logger.info(f"  Loading known BBLs for {source}...")
        known_bbls = load_known_bbls(session)
    logger.info(f"  {len(known_bbls):,} known BBLs")

    pad_bins = {}
    if needs_pad:
        pad_bins = dict(session.execute(text("SELECT bin, bbl FROM pad_addresses")).fetchall())

    params = {"$select": select, "$limit": 50000}
    if order:
        params["$order"] = order
    records = socrata_fetch(DATASETS[dataset_key], params, source)

    inserted = matched = rejected = 0
    batch = []
    BATCH_SIZE = 5000

    for r in records:
        if use_bbl:
            bbl = compute_bbl(r.get("boroughid", r.get("boroid")), r.get("block"), r.get("lot"))
        elif needs_pad:
            bin_val = r.get("bin")
            bbl = pad_bins.get(bin_val) if bin_val else None
        else:
            bbl = str(r.get("bbl", "")).strip()
            if len(bbl) != 10:
                bbl = None

        if not bbl:
            rejected += 1
            continue
        if bbl not in known_bbls:
            rejected += 1
            continue
        matched += 1

        batch.append(row_mapper(r, bbl))
        if len(batch) >= BATCH_SIZE:
            try:
                session.execute(text(insert_sql), batch)
                session.commit()
            except Exception as e:
                session.rollback()
                logger.warning(f"{source} batch insert failed: {e}")
            inserted += len(batch)
            logger.info(f"  {source} progress: {inserted}")
            batch = []

    if batch:
        try:
            session.execute(text(insert_sql), batch)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.warning(f"{source} final batch failed: {e}")
        inserted += len(batch)

    log_quality(session, source, len(records), matched, rejected, inserted)
    finish_job(session, job_id, "completed", len(records), inserted, rejected)
    session.commit()
    logger.info(f"{source}: {inserted} inserted, {rejected} rejected")
    return inserted


def run_complaints(session, known_bbls=None):
    logger.info("=== HPD Complaints ===")
    job_id = create_job(session, "hpd_complaints")
    session.commit()

    if known_bbls is None:
        known_bbls = load_known_bbls(session)

    records = socrata_fetch(DATASETS["hpd_complaints"], {
        "$select": "complaint_id,building_id,borough,block,lot,complaint_status,"
                   "complaint_status_date,major_category,minor_category,received_date",
        "$where": "received_date > '2024-01-01'",
        "$order": "received_date DESC",
        "$limit": 50000,
    }, "hpd_complaints")

    boro_map = {"MANHATTAN": "1", "BRONX": "2", "BROOKLYN": "3", "QUEENS": "4", "STATEN ISLAND": "5"}
    inserted = matched = rejected = 0
    batch = []
    BATCH_SIZE = 5000

    for r in records:
        boro = r.get("borough", "")
        boro_id = boro_map.get(boro.upper(), r.get("borough"))
        bbl = compute_bbl(boro_id, r.get("block"), r.get("lot"))
        if not bbl or bbl not in known_bbls:
            rejected += 1
            continue
        matched += 1
        batch.append({
            "cid": r.get("complaint_id"), "bbl": bbl, "bid": r.get("building_id"),
            "status": r.get("complaint_status"),
            "status_date": (r.get("complaint_status_date") or "")[:10] or None,
            "major": r.get("major_category"), "minor": r.get("minor_category"),
            "received": (r.get("received_date") or "")[:10] or None,
        })
        if len(batch) >= BATCH_SIZE:
            try:
                session.execute(
                    text("""INSERT INTO hpd_complaints (complaint_id,bbl,building_id,status,status_date,
                       major_category,minor_category,received_date,created_at,updated_at)
                       VALUES(:cid,:bbl,:bid,:status,:status_date,:major,:minor,:received,now(),now())
                       ON CONFLICT(complaint_id) DO NOTHING"""), batch)
                session.commit()
            except Exception as e:
                session.rollback()
                logger.warning(f"Complaints batch failed: {e}")
            inserted += len(batch)
            logger.info(f"  Complaints progress: {inserted}")
            batch = []

    if batch:
        try:
            session.execute(
                text("""INSERT INTO hpd_complaints (complaint_id,bbl,building_id,status,status_date,
                   major_category,minor_category,received_date,created_at,updated_at)
                   VALUES(:cid,:bbl,:bid,:status,:status_date,:major,:minor,:received,now(),now())
                   ON CONFLICT(complaint_id) DO NOTHING"""), batch)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.warning(f"Complaints final batch failed: {e}")
        inserted += len(batch)

    log_quality(session, "hpd_complaints", len(records), matched, rejected, inserted)
    finish_job(session, job_id, "completed", len(records), inserted, rejected)
    session.commit()
    logger.info(f"HPD Complaints: {inserted} inserted, {rejected} rejected")
    return inserted


def run_violations(session, known_bbls=None):
    logger.info("=== HPD Violations ===")
    job_id = create_job(session, "hpd_violations")
    session.commit()

    if known_bbls is None:
        known_bbls = load_known_bbls(session)

    records = socrata_fetch(DATASETS["hpd_violations"], {
        "$select": "violationid,boroid,block,lot,buildingid,class,"
                   "inspectiondate,approveddate,originalcertifybydate,"
                   "originalcorrectbydate,novdescription,currentstatus,currentstatusdate",
        "$where": "inspectiondate > '2024-01-01'",
        "$order": "inspectiondate DESC",
        "$limit": 50000,
    }, "hpd_violations")

    inserted = matched = rejected = 0
    batch = []
    BATCH_SIZE = 5000

    for r in records:
        bbl = compute_bbl(r.get("boroid"), r.get("block"), r.get("lot"))
        if not bbl or bbl not in known_bbls:
            rejected += 1
            continue
        matched += 1
        batch.append({
            "vid": r.get("violationid"), "bbl": bbl, "bid": r.get("buildingid"),
            "cls": r.get("class"), "insp": (r.get("inspectiondate") or "")[:10] or None,
            "approved": (r.get("approveddate") or "")[:10] or None,
            "certify": (r.get("originalcertifybydate") or "")[:10] or None,
            "correct": (r.get("originalcorrectbydate") or "")[:10] or None,
            "desc": r.get("novdescription"), "status": r.get("currentstatus"),
            "status_date": (r.get("currentstatusdate") or "")[:10] or None,
        })
        if len(batch) >= BATCH_SIZE:
            try:
                session.execute(
                    text("""INSERT INTO hpd_violations (violation_id,bbl,building_id,violation_class,
                       inspection_date,approved_date,original_certify_by_date,original_correct_by_date,
                       nov_description,current_status,current_status_date,created_at,updated_at)
                       VALUES(:vid,:bbl,:bid,:cls,:insp,:approved,:certify,:correct,:desc,:status,:status_date,now(),now())
                       ON CONFLICT(violation_id) DO NOTHING"""), batch)
                session.commit()
            except Exception as e:
                session.rollback()
                logger.warning(f"Violations batch failed: {e}")
            inserted += len(batch)
            logger.info(f"  Violations progress: {inserted}")
            batch = []

    if batch:
        try:
            session.execute(
                text("""INSERT INTO hpd_violations (violation_id,bbl,building_id,violation_class,
                   inspection_date,approved_date,original_certify_by_date,original_correct_by_date,
                   nov_description,current_status,current_status_date,created_at,updated_at)
                   VALUES(:vid,:bbl,:bid,:cls,:insp,:approved,:certify,:correct,:desc,:status,:status_date,now(),now())
                   ON CONFLICT(violation_id) DO NOTHING"""), batch)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.warning(f"Violations final batch failed: {e}")
        inserted += len(batch)

    log_quality(session, "hpd_violations", len(records), matched, rejected, inserted)
    finish_job(session, job_id, "completed", len(records), inserted, rejected)
    session.commit()
    logger.info(f"HPD Violations: {inserted} inserted, {rejected} rejected")
    return inserted


def run_litigation(session, known_bbls=None):
    return run_signal_task(
        session, "hpd_litigation", "hpd_litigation",
        "litigationid,boroid,block,lot,buildingid,casetype,casestatus,caseopendate,caseclosedate,"
        "findingofharassment,penalty",
        """INSERT INTO hpd_litigation (litigation_id,bbl,building_id,case_type,case_status,
           case_open_date,case_close_date,finding,penalty,created_at,updated_at)
           VALUES(:lid,:bbl,:bid,:type,:status,:opened,:closed,:finding,:penalty,now(),now())
           ON CONFLICT(litigation_id) DO NOTHING""",
        lambda r, bbl: {"lid": r.get("litigationid"), "bbl": bbl, "bid": r.get("buildingid"),
                        "type": r.get("casetype"), "status": r.get("casestatus"),
                        "opened": (r.get("caseopendate") or "")[:10] or None,
                        "closed": (r.get("caseclosedate") or "")[:10] or None,
                        "finding": r.get("findingofharassment"),
                        "penalty": float(r["penalty"]) if r.get("penalty") else None},
        order="caseopendate DESC",
        known_bbls=known_bbls,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", help="Comma-separated list of tasks", default="all")
    args = parser.parse_args()

    all_tasks = ["pad", "buildings", "complaints", "violations", "litigation"]
    tasks = all_tasks if args.tasks == "all" else [t.strip() for t in args.tasks.split(",")]

    session = get_session()
    results = {}

    task_funcs = {
        "pad": run_pad,
        "buildings": run_buildings,
        "complaints": run_complaints,
        "violations": run_violations,
        "litigation": run_litigation,
    }

    # Load known BBLs once after buildings are available, reuse for signal tasks
    known_bbls = None

    for task_name in tasks:
        func = task_funcs.get(task_name)
        if not func:
            logger.warning(f"Unknown task: {task_name}")
            continue
        start = time.time()
        try:
            # After buildings are populated, load BBLs once for all signal tasks
            if task_name in ("complaints", "violations", "litigation") and known_bbls is None:
                logger.info("Loading known BBLs for signal tasks...")
                known_bbls = load_known_bbls(session)
                logger.info(f"  {len(known_bbls):,} BBLs loaded")

            if task_name in ("complaints", "violations", "litigation"):
                count = func(session, known_bbls=known_bbls)
            else:
                count = func(session)

            elapsed = time.time() - start
            results[task_name] = {"status": "ok", "inserted": count, "elapsed_s": round(elapsed, 1)}
            logger.info(f"{task_name}: completed in {elapsed:.1f}s")
        except Exception as e:
            elapsed = time.time() - start
            results[task_name] = {"status": "error", "error": str(e), "elapsed_s": round(elapsed, 1)}
            logger.error(f"{task_name} failed: {e}")

    session.close()
    logger.info("=" * 60)
    logger.info("INGESTION SUMMARY:")
    for task, result in results.items():
        logger.info(f"  {task}: {result}")
    return results


if __name__ == "__main__":
    main()
