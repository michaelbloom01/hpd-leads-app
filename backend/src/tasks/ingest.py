"""Ingestion Celery tasks.

Each task follows the standard pattern from the plan:
1. Fetch from Socrata API with pagination, date filtering, app token
2. Schema validation: verify response field names match expected
3. Validate/normalize join key (BBL or BuildingID)
4. Bulk upsert, deduplicate by natural key
5. Update IngestionJob progress
6. Write summary to data_quality_log
7. Circuit breaker: 5 consecutive 5xx = mark source_unavailable
"""
import logging
import os
import threading
from typing import Optional

import requests
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

try:
    from src.worker import app as celery_app
except ImportError:
    class _FakeCelery:
        @staticmethod
        def task(*args, **kwargs):
            return lambda fn: fn
    celery_app = _FakeCelery()

logger = logging.getLogger(__name__)

APP_TOKEN = os.environ.get("NYC_OPEN_DATA_APP_TOKEN", "")
SOCRATA_BASE = "https://data.cityofnewyork.us/resource"

DATASETS = {
    "hpd_complaints": "ygpa-z7cr",
    "acris_master": "bnx9-e6tj",
    "acris_legals": "8h5j-fqxa",
    "acris_parties": "636b-3b5g",
    "dob_permits_bis": "ipu4-2vj7",
    "dob_permits_now": "rbx6-tga4",
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


def _get_pg_session() -> Session:
    from src.db.session import get_sync_url
    engine = create_engine(get_sync_url())
    return Session(engine)


def _socrata_fetch(dataset_id: str, params: dict, max_retries: int = 3) -> list[dict]:
    """Fetch from Socrata with pagination, retries, and circuit breaker logic."""
    url = f"{SOCRATA_BASE}/{dataset_id}.json"
    headers = {}
    if APP_TOKEN:
        headers["X-App-Token"] = APP_TOKEN

    all_records = []
    offset = params.get("$offset", 0)
    limit = params.get("$limit", 10000)
    consecutive_failures = 0

    while True:
        params["$offset"] = offset
        params["$limit"] = limit

        for attempt in range(max_retries):
            try:
                resp = requests.get(url, params=params, headers=headers, timeout=60)
                if resp.status_code >= 500:
                    consecutive_failures += 1
                    if consecutive_failures >= 5:
                        raise RuntimeError(
                            f"Circuit breaker: 5 consecutive 5xx from {dataset_id}"
                        )
                    continue
                resp.raise_for_status()
                consecutive_failures = 0
                break
            except requests.RequestException as e:
                if attempt == max_retries - 1:
                    raise
                logger.warning(f"Retry {attempt+1} for {dataset_id}: {e}")

        batch = resp.json()
        if not batch:
            break
        all_records.extend(batch)
        if len(batch) < limit:
            break
        offset += limit

    return all_records


def _log_quality(session: Session, source: str, job_id: int | None,
                 fetched: int, matched: int, rejected: int, inserted: int,
                 notes: str = ""):
    """Write a data quality log entry."""
    match_rate = matched / fetched if fetched > 0 else 0.0
    session.execute(
        text("""
            INSERT INTO data_quality_log
                (source_name, job_id, run_timestamp, records_fetched,
                 records_matched, records_rejected, records_inserted,
                 match_rate, volume_anomaly, notes, created_at, updated_at)
            VALUES
                (:source, :job_id, now(), :fetched, :matched, :rejected,
                 :inserted, :match_rate, false, :notes, now(), now())
        """),
        {
            "source": source, "job_id": job_id, "fetched": fetched,
            "matched": matched, "rejected": rejected, "inserted": inserted,
            "match_rate": match_rate, "notes": notes,
        },
    )


def _create_job(session: Session, job_type: str, source: str) -> int:
    result = session.execute(
        text("""
            INSERT INTO ingestion_jobs (job_type, source, status, started_at, created_at, updated_at)
            VALUES (:job_type, :source, 'running', now(), now(), now())
            RETURNING id
        """),
        {"job_type": job_type, "source": source},
    )
    session.flush()
    return result.scalar_one()


def _ensure_or_create_job(
    session: Session,
    job_id: Optional[int],
    job_type: str,
    source: str,
) -> int:
    """Adopt an existing queued job row, or create a new one."""
    if job_id is None:
        return _create_job(session, job_type, source)
    session.execute(
        text("""
            UPDATE ingestion_jobs
            SET job_type = :job_type,
                source = :source,
                status = 'running',
                started_at = COALESCE(started_at, now()),
                total = NULL,
                processed = 0,
                succeeded = 0,
                failed = 0,
                error = NULL,
                updated_at = now()
            WHERE id = :job_id
        """),
        {"job_id": job_id, "job_type": job_type, "source": source},
    )
    return job_id


def _finish_job(session: Session, job_id: int, status: str, total: int, succeeded: int,
                failed: int, error: str | None = None):
    normalized_status = "succeeded" if status == "completed" else status
    session.execute(
        text("""
            UPDATE ingestion_jobs
            SET status = :status, total = :total, succeeded = :succeeded,
                failed = :failed, error = :error, finished_at = now(), updated_at = now()
            WHERE id = :job_id
        """),
        {"job_id": job_id, "status": normalized_status, "total": total,
         "succeeded": succeeded, "failed": failed, "error": error},
    )


def _compute_bbl(boro_id, block, lot) -> str | None:
    try:
        b = str(int(str(boro_id).strip()))
        bl = str(int(str(block).strip())).zfill(5)
        lt = str(int(str(lot).strip())).zfill(4)
        return f"{b}{bl}{lt}"
    except (ValueError, TypeError):
        return None


@celery_app.task(bind=True, name="src.tasks.ingest.ingest_buildings_from_hpd")
def ingest_buildings_from_hpd(self, job_id: Optional[int] = None):
    """Phase 1A: Backfill buildings table from HPD registrations + contacts + PLUTO."""
    session = _get_pg_session()
    if job_id is None:
        job_id = _create_job(session, "buildings", "buildings")
    else:
        session.execute(
            text("""
                UPDATE ingestion_jobs
                SET source = 'buildings',
                    status = 'running',
                    started_at = COALESCE(started_at, now()),
                    total = NULL,
                    processed = 0,
                    succeeded = 0,
                    failed = 0,
                    error = NULL,
                    updated_at = now()
                WHERE id = :job_id
            """),
            {"job_id": job_id},
        )
    session.commit()

    try:
        logger.info("Fetching HPD registrations...")
        registrations = _socrata_fetch(DATASETS["hpd_registrations"], {
            "$select": "boroid,block,lot,buildingid,registrationid,housenumber,"
                       "streetname,zip,boro,lastregistrationdate,registrationenddate",
            "$order": "lastregistrationdate DESC",
        })
        logger.info(f"Fetched {len(registrations)} registrations")

        logger.info("Fetching HPD contacts...")
        contacts = _socrata_fetch(DATASETS["hpd_contacts"], {
            "$select": "registrationcontactid,registrationid,type,"
                       "contactdescription,corporationname,firstname,lastname,title,"
                       "businesshousenumber,businessstreetname,businessapartment,"
                       "businesscity,businessstate,businesszip",
        })
        contacts_by_reg = {}
        for c in contacts:
            rid = c.get("registrationid")
            if rid:
                contacts_by_reg.setdefault(rid, []).append(c)
        logger.info(f"Fetched {len(contacts)} contacts for {len(contacts_by_reg)} registrations")

        logger.info("Fetching PLUTO for geographic fields...")
        # _socrata_fetch paginates until exhaustion via $offset/$limit loop;
        # this retrieves the full PLUTO dataset, not just one 50k page.
        pluto_data = _socrata_fetch(DATASETS["pluto"], {
            "$select": "bbl,cd,council,ct2010,assesstot,unitsres,yearbuilt,bldgclass",
            "$limit": 50000,
        })
        pluto_by_bbl = {str(p.get("bbl", "")).strip(): p for p in pluto_data if p.get("bbl")}
        logger.info(f"Fetched {len(pluto_data)} PLUTO records")

        inserted = 0
        matched = 0
        rejected = 0
        buildings_seen = set()

        for reg in registrations:
            bbl = _compute_bbl(
                reg.get("boroid"), reg.get("block"), reg.get("lot")
            )
            if not bbl or bbl in buildings_seen:
                if not bbl:
                    rejected += 1
                continue
            buildings_seen.add(bbl)
            matched += 1

            pluto = pluto_by_bbl.get(bbl, {})
            address = f"{reg.get('housenumber', '')} {reg.get('streetname', '')}".strip()

            session.execute(
                text("""
                    INSERT INTO buildings (
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
                        assessed_value = EXCLUDED.assessed_value,
                        council_district = EXCLUDED.council_district,
                        community_board = EXCLUDED.community_board,
                        updated_at = now()
                """),
                {
                    "bbl": bbl,
                    "bin": reg.get("buildingid"),
                    "address": address,
                    "borough": reg.get("boro"),
                    "block": reg.get("block"),
                    "lot": reg.get("lot"),
                    "zip": reg.get("zip"),
                    "bldg_class": pluto.get("bldgclass"),
                    "units": int(pluto["unitsres"]) if pluto.get("unitsres") else None,
                    "year_built": int(pluto["yearbuilt"]) if pluto.get("yearbuilt") else None,
                    "assessed_value": float(pluto["assesstot"]) if pluto.get("assesstot") else None,
                    "council": pluto.get("council"),
                    "cd": pluto.get("cd"),
                    "census": pluto.get("ct2010"),
                    "nta": None,
                },
            )
            inserted += 1

            reg_id = reg.get("registrationid")
            reg_contacts = contacts_by_reg.get(reg_id, [])
            for c in reg_contacts:
                session.execute(
                    text("""
                        INSERT INTO building_contacts (
                            bbl, registration_contact_id, registration_id,
                            contact_type, description, corporation_name,
                            first_name, last_name, title,
                            business_address, business_city, business_state, business_zip,
                            created_at, updated_at
                        ) VALUES (
                            :bbl, :contact_id, :reg_id, :type, :desc, :corp,
                            :first, :last, :title,
                            :addr, :city, :state, :zip, now(), now()
                        ) ON CONFLICT DO NOTHING
                    """),
                    {
                        "bbl": bbl,
                        "contact_id": c.get("registrationcontactid"),
                        "reg_id": reg_id,
                        "type": c.get("type"),
                        "desc": c.get("contactdescription"),
                        "corp": c.get("corporationname"),
                        "first": c.get("firstname"),
                        "last": c.get("lastname"),
                        "title": c.get("title"),
                        "addr": f"{c.get('businesshousenumber', '')} {c.get('businessstreetname', '')}".strip(),
                        "city": c.get("businesscity"),
                        "state": c.get("businessstate"),
                        "zip": c.get("businesszip"),
                    },
                )

            if inserted % 5000 == 0:
                session.execute(
                    text("""
                        UPDATE ingestion_jobs
                        SET processed = :processed, succeeded = :succeeded, failed = :failed, updated_at = now()
                        WHERE id = :job_id
                    """),
                    {"job_id": job_id, "processed": inserted, "succeeded": inserted, "failed": rejected},
                )
                session.commit()
                logger.info(f"Progress: {inserted} buildings inserted")

        session.commit()
        _log_quality(session, "hpd_buildings", job_id, len(registrations),
                     matched, rejected, inserted)
        _finish_job(session, job_id, "completed", len(registrations), inserted, rejected)
        session.commit()
        try:
            scoring_job_id = _create_job(session, "scoring", "churn")
            session.commit()
            from src.tasks.score import run_scoring_job
            try:
                run_scoring_job.delay(job_id=scoring_job_id)
                logger.info("Queued follow-up scoring job %s after buildings ingestion", scoring_job_id)
            except Exception as dispatch_exc:
                logger.warning(
                    "Celery dispatch failed for follow-up scoring job %s, using in-process fallback: %s",
                    scoring_job_id,
                    dispatch_exc,
                )
                t = threading.Thread(
                    target=run_scoring_job.run,
                    kwargs={"job_id": scoring_job_id},
                    daemon=True,
                )
                t.start()
        except Exception as score_exc:
            logger.warning("Failed to queue follow-up scoring job after buildings ingestion: %s", score_exc)
        try:
            lead_job_id = _create_job(session, "lead_generation", "lead_generation")
            session.commit()
            from src.tasks.lead_materialization import generate_leads_job
            try:
                generate_leads_job.delay(job_id=lead_job_id, min_portfolio=1)
                logger.info("Queued follow-up lead generation job %s after buildings ingestion", lead_job_id)
            except Exception as dispatch_exc:
                logger.warning(
                    "Celery dispatch failed for follow-up lead generation job %s, using in-process fallback: %s",
                    lead_job_id,
                    dispatch_exc,
                )
                t = threading.Thread(
                    target=generate_leads_job.run,
                    kwargs={"job_id": lead_job_id, "min_portfolio": 1},
                    daemon=True,
                )
                t.start()
        except Exception as lead_exc:
            logger.warning("Failed to queue follow-up lead generation job after buildings ingestion: %s", lead_exc)
        logger.info(f"Buildings backfill complete: {inserted} inserted, {rejected} rejected")
        return {"inserted": inserted, "rejected": rejected}

    except Exception as e:
        _finish_job(session, job_id, "failed", 0, 0, 0, str(e))
        session.commit()
        session.close()
        raise


@celery_app.task(bind=True, name="src.tasks.ingest.ingest_hpd_complaints")
def ingest_hpd_complaints(self, job_id: Optional[int] = None):
    """Signal Batch 1: HPD Complaints (ygpa-z7cr)."""
    session = _get_pg_session()
    job_id = _ensure_or_create_job(session, job_id, "hpd_complaints", "hpd_complaints")
    session.commit()

    try:
        records = _socrata_fetch(DATASETS["hpd_complaints"], {
            "$select": "complaintid,buildingid,boroughid,block,lot,status,"
                       "statusdate,statusid,complaintid,majorcategory,"
                       "minorcategory,receiveddate",
            "$order": "receiveddate DESC",
            "$limit": 50000,
        })

        inserted = matched = rejected = 0
        for r in records:
            bbl = _compute_bbl(r.get("boroughid"), r.get("block"), r.get("lot"))
            if not bbl:
                rejected += 1
                continue

            exists = session.execute(
                text("SELECT 1 FROM buildings WHERE bbl = :bbl"), {"bbl": bbl}
            ).first()
            if not exists:
                rejected += 1
                continue
            matched += 1

            session.execute(
                text("""
                    INSERT INTO hpd_complaints (
                        complaint_id, bbl, building_id, status, status_date,
                        major_category, minor_category, received_date,
                        created_at, updated_at
                    ) VALUES (
                        :cid, :bbl, :bid, :status, :status_date,
                        :major, :minor, :received, now(), now()
                    ) ON CONFLICT (complaint_id) DO NOTHING
                """),
                {
                    "cid": r.get("complaintid"),
                    "bbl": bbl,
                    "bid": r.get("buildingid"),
                    "status": r.get("status"),
                    "status_date": r.get("statusdate", "")[:10] or None,
                    "major": r.get("majorcategory"),
                    "minor": r.get("minorcategory"),
                    "received": r.get("receiveddate", "")[:10] or None,
                },
            )
            inserted += 1
            if inserted % 5000 == 0:
                session.commit()

        session.commit()
        _log_quality(session, "hpd_complaints", job_id, len(records), matched, rejected, inserted)
        _finish_job(session, job_id, "completed", len(records), inserted, rejected)
        session.commit()
        logger.info(f"HPD Complaints: {inserted} inserted, {rejected} rejected")
        return {"inserted": inserted, "rejected": rejected}
    except Exception as e:
        _finish_job(session, job_id, "failed", 0, 0, 0, str(e))
        session.commit()
        session.close()
        raise


@celery_app.task(bind=True, name="src.tasks.ingest.ingest_acris_transactions")
def ingest_acris_transactions(self, job_id: Optional[int] = None):
    """Signal Batch 1: ACRIS Real Property transactions."""
    session = _get_pg_session()
    job_id = _ensure_or_create_job(session, job_id, "acris", "acris")
    session.commit()

    try:
        legals = _socrata_fetch(DATASETS["acris_legals"], {
            "$select": "document_id,borough,block,lot",
            "$where": "borough IS NOT NULL AND block IS NOT NULL AND lot IS NOT NULL",
            "$limit": 50000,
            "$order": "document_id DESC",
        })
        doc_to_bbl = {}
        for legal_row in legals:
            bbl = _compute_bbl(legal_row.get("borough"), legal_row.get("block"), legal_row.get("lot"))
            if bbl:
                doc_to_bbl[legal_row.get("document_id")] = bbl

        masters = _socrata_fetch(DATASETS["acris_master"], {
            "$select": "document_id,doc_type,doc_type_description,recorded_datetime,doc_amount",
            "$where": "doc_type IN('DEED','MTGE','AGMT','ASST')",
            "$order": "recorded_datetime DESC",
            "$limit": 50000,
        })

        inserted = matched = rejected = 0
        for m in masters:
            doc_id = m.get("document_id")
            bbl = doc_to_bbl.get(doc_id)
            if not bbl:
                rejected += 1
                continue

            exists = session.execute(
                text("SELECT 1 FROM buildings WHERE bbl = :bbl"), {"bbl": bbl}
            ).first()
            if not exists:
                rejected += 1
                continue
            matched += 1

            recorded = m.get("recorded_datetime", "")[:10] or None
            session.execute(
                text("""
                    INSERT INTO acris_transactions (
                        document_id, bbl, doc_type, doc_type_description,
                        recorded_date, doc_amount, created_at, updated_at
                    ) VALUES (
                        :doc_id, :bbl, :doc_type, :desc, :recorded, :amount, now(), now()
                    ) ON CONFLICT (document_id) DO NOTHING
                """),
                {
                    "doc_id": doc_id, "bbl": bbl,
                    "doc_type": m.get("doc_type"),
                    "desc": m.get("doc_type_description"),
                    "recorded": recorded,
                    "amount": float(m["doc_amount"]) if m.get("doc_amount") else None,
                },
            )
            inserted += 1
            if inserted % 5000 == 0:
                session.commit()

        session.commit()
        _log_quality(session, "acris", job_id, len(masters), matched, rejected, inserted)
        _finish_job(session, job_id, "completed", len(masters), inserted, rejected)
        session.commit()
        return {"inserted": inserted, "rejected": rejected}
    except Exception as e:
        _finish_job(session, job_id, "failed", 0, 0, 0, str(e))
        session.commit()
        session.close()
        raise


@celery_app.task(bind=True, name="src.tasks.ingest.ingest_hpd_violations")
def ingest_hpd_violations(self, job_id: Optional[int] = None):
    """Signal Batch 1: HPD Violations."""
    session = _get_pg_session()
    job_id = _ensure_or_create_job(session, job_id, "hpd_violations", "hpd_violations")
    session.commit()

    try:
        records = _socrata_fetch(DATASETS["hpd_violations"], {
            "$select": "violationid,boroid,block,lot,buildingid,class,"
                       "inspectiondate,approveddate,originalcertifybydate,"
                       "originalcorrectbydate,novdescription,currentstatus,currentstatusdate",
            "$order": "inspectiondate DESC",
            "$limit": 50000,
        })

        inserted = matched = rejected = 0
        for r in records:
            bbl = _compute_bbl(r.get("boroid"), r.get("block"), r.get("lot"))
            if not bbl:
                rejected += 1
                continue
            exists = session.execute(
                text("SELECT 1 FROM buildings WHERE bbl = :bbl"), {"bbl": bbl}
            ).first()
            if not exists:
                rejected += 1
                continue
            matched += 1

            session.execute(
                text("""
                    INSERT INTO hpd_violations (
                        violation_id, bbl, building_id, violation_class,
                        inspection_date, approved_date, original_certify_by_date,
                        original_correct_by_date, nov_description,
                        current_status, current_status_date, created_at, updated_at
                    ) VALUES (
                        :vid, :bbl, :bid, :cls, :insp, :approved, :certify,
                        :correct, :desc, :status, :status_date, now(), now()
                    ) ON CONFLICT (violation_id) DO NOTHING
                """),
                {
                    "vid": r.get("violationid"), "bbl": bbl,
                    "bid": r.get("buildingid"),
                    "cls": r.get("class"),
                    "insp": r.get("inspectiondate", "")[:10] or None,
                    "approved": r.get("approveddate", "")[:10] or None,
                    "certify": r.get("originalcertifybydate", "")[:10] or None,
                    "correct": r.get("originalcorrectbydate", "")[:10] or None,
                    "desc": r.get("novdescription"),
                    "status": r.get("currentstatus"),
                    "status_date": r.get("currentstatusdate", "")[:10] or None,
                },
            )
            inserted += 1
            if inserted % 5000 == 0:
                session.commit()

        session.commit()
        _log_quality(session, "hpd_violations", job_id, len(records), matched, rejected, inserted)
        _finish_job(session, job_id, "completed", len(records), inserted, rejected)
        session.commit()
        return {"inserted": inserted, "rejected": rejected}
    except Exception as e:
        _finish_job(session, job_id, "failed", 0, 0, 0, str(e))
        session.commit()
        session.close()
        raise


@celery_app.task(bind=True, name="src.tasks.ingest.ingest_dob_permits")
def ingest_dob_permits(self, job_id: Optional[int] = None):
    """Signal Batch 2: DOB Permits (BIS + NOW)."""
    session = _get_pg_session()
    job_id = _ensure_or_create_job(session, job_id, "dob_permits", "dob_permits")
    session.commit()

    try:
        pad_bins = dict(
            session.execute(text("SELECT bin, bbl FROM pad_addresses")).fetchall()
        )

        records = _socrata_fetch(DATASETS["dob_permits_bis"], {
            "$select": "job__,bin__,job_type,job_type_desc,filing_date,"
                       "issuance_date,expiration_date,job_description,estimated_job_cost__",
            "$order": "filing_date DESC",
            "$limit": 50000,
        })

        inserted = matched = rejected = 0
        for r in records:
            bin_val = r.get("bin__")
            bbl = pad_bins.get(bin_val)
            if not bbl:
                rejected += 1
                continue
            exists = session.execute(
                text("SELECT 1 FROM buildings WHERE bbl = :bbl"), {"bbl": bbl}
            ).first()
            if not exists:
                rejected += 1
                continue
            matched += 1

            session.execute(
                text("""
                    INSERT INTO dob_permits (
                        job_number, bbl, bin, permit_type, filing_date,
                        issuance_date, expiration_date, job_description,
                        estimated_cost, created_at, updated_at
                    ) VALUES (
                        :job, :bbl, :bin, :type, :filed, :issued, :expires,
                        :desc, :cost, now(), now()
                    ) ON CONFLICT (job_number) DO NOTHING
                """),
                {
                    "job": r.get("job__"), "bbl": bbl, "bin": bin_val,
                    "type": r.get("job_type"),
                    "filed": r.get("filing_date", "")[:10] or None,
                    "issued": r.get("issuance_date", "")[:10] or None,
                    "expires": r.get("expiration_date", "")[:10] or None,
                    "desc": r.get("job_description"),
                    "cost": float(r["estimated_job_cost__"]) if r.get("estimated_job_cost__") else None,
                },
            )
            inserted += 1
            if inserted % 5000 == 0:
                session.commit()

        session.commit()
        _log_quality(session, "dob_permits", job_id, len(records), matched, rejected, inserted)
        _finish_job(session, job_id, "completed", len(records), inserted, rejected)
        session.commit()
        return {"inserted": inserted, "rejected": rejected}
    except Exception as e:
        _finish_job(session, job_id, "failed", 0, 0, 0, str(e))
        session.commit()
        session.close()
        raise


@celery_app.task(bind=True, name="src.tasks.ingest.ingest_hpd_litigation")
def ingest_hpd_litigation(self, job_id: Optional[int] = None):
    """Signal Batch 2: HPD Litigation."""
    session = _get_pg_session()
    job_id = _ensure_or_create_job(session, job_id, "hpd_litigation", "hpd_litigation")
    session.commit()

    try:
        records = _socrata_fetch(DATASETS["hpd_litigation"], {
            "$select": "litigationid,boroid,block,lot,buildingid,casetype,"
                       "casestatus,caseopendate,caseclosedate,findingofharassment,penalty",
            "$order": "caseopendate DESC",
            "$limit": 50000,
        })

        inserted = matched = rejected = 0
        for r in records:
            bbl = _compute_bbl(r.get("boroid"), r.get("block"), r.get("lot"))
            if not bbl:
                rejected += 1
                continue
            exists = session.execute(
                text("SELECT 1 FROM buildings WHERE bbl = :bbl"), {"bbl": bbl}
            ).first()
            if not exists:
                rejected += 1
                continue
            matched += 1

            session.execute(
                text("""
                    INSERT INTO hpd_litigation (
                        litigation_id, bbl, building_id, case_type, case_status,
                        case_open_date, case_close_date, finding, penalty,
                        created_at, updated_at
                    ) VALUES (
                        :lid, :bbl, :bid, :type, :status, :opened, :closed,
                        :finding, :penalty, now(), now()
                    ) ON CONFLICT (litigation_id) DO NOTHING
                """),
                {
                    "lid": r.get("litigationid"), "bbl": bbl,
                    "bid": r.get("buildingid"),
                    "type": r.get("casetype"), "status": r.get("casestatus"),
                    "opened": r.get("caseopendate", "")[:10] or None,
                    "closed": r.get("caseclosedate", "")[:10] or None,
                    "finding": r.get("findingofharassment"),
                    "penalty": float(r["penalty"]) if r.get("penalty") else None,
                },
            )
            inserted += 1

        session.commit()
        _log_quality(session, "hpd_litigation", job_id, len(records), matched, rejected, inserted)
        _finish_job(session, job_id, "completed", len(records), inserted, rejected)
        session.commit()
        return {"inserted": inserted, "rejected": rejected}
    except Exception as e:
        _finish_job(session, job_id, "failed", 0, 0, 0, str(e))
        session.commit()
        session.close()
        raise


@celery_app.task(bind=True, name="src.tasks.ingest.ingest_emergency_repairs")
def ingest_emergency_repairs(self, job_id: Optional[int] = None):
    """Signal Batch 2: HPD Emergency Repair Program."""
    session = _get_pg_session()
    job_id = _ensure_or_create_job(session, job_id, "emergency_repairs", "emergency_repairs")
    session.commit()

    try:
        records = _socrata_fetch(DATASETS["emergency_repairs"], {
            "$select": "erpordernumber,boroid,block,lot,buildingid,"
                       "orderdate,repairtype,amount,status",
            "$order": "orderdate DESC",
            "$limit": 50000,
        })

        inserted = matched = rejected = 0
        for r in records:
            bbl = _compute_bbl(r.get("boroid"), r.get("block"), r.get("lot"))
            if not bbl:
                rejected += 1
                continue
            exists = session.execute(
                text("SELECT 1 FROM buildings WHERE bbl = :bbl"), {"bbl": bbl}
            ).first()
            if not exists:
                rejected += 1
                continue
            matched += 1

            session.execute(
                text("""
                    INSERT INTO emergency_repairs (
                        erp_order_number, bbl, building_id, order_date,
                        repair_type, amount, status, created_at, updated_at
                    ) VALUES (
                        :oid, :bbl, :bid, :odate, :type, :amount, :status, now(), now()
                    ) ON CONFLICT (erp_order_number) DO NOTHING
                """),
                {
                    "oid": r.get("erpordernumber"), "bbl": bbl,
                    "bid": r.get("buildingid"),
                    "odate": r.get("orderdate", "")[:10] or None,
                    "type": r.get("repairtype"),
                    "amount": float(r["amount"]) if r.get("amount") else None,
                    "status": r.get("status"),
                },
            )
            inserted += 1

        session.commit()
        _log_quality(session, "emergency_repairs", job_id, len(records), matched, rejected, inserted)
        _finish_job(session, job_id, "completed", len(records), inserted, rejected)
        session.commit()
        return {"inserted": inserted, "rejected": rejected}
    except Exception as e:
        _finish_job(session, job_id, "failed", 0, 0, 0, str(e))
        session.commit()
        session.close()
        raise


@celery_app.task(bind=True, name="src.tasks.ingest.ingest_aep_designations")
def ingest_aep_designations(self, job_id: Optional[int] = None):
    """Signal Batch 2: AEP Designations."""
    session = _get_pg_session()
    job_id = _ensure_or_create_job(session, job_id, "aep", "aep")
    session.commit()

    try:
        records = _socrata_fetch(DATASETS["aep_designations"], {
            "$limit": 50000,
        })

        inserted = matched = rejected = 0
        for r in records:
            bbl = _compute_bbl(r.get("boroid"), r.get("block"), r.get("lot"))
            if not bbl:
                rejected += 1
                continue
            exists = session.execute(
                text("SELECT 1 FROM buildings WHERE bbl = :bbl"), {"bbl": bbl}
            ).first()
            if not exists:
                rejected += 1
                continue
            matched += 1

            session.execute(
                text("""
                    INSERT INTO aep_designations (
                        bbl, building_id, designation_date, is_active,
                        created_at, updated_at
                    ) VALUES (:bbl, :bid, :ddate, true, now(), now())
                    ON CONFLICT DO NOTHING
                """),
                {
                    "bbl": bbl,
                    "bid": r.get("buildingid"),
                    "ddate": r.get("designationdate", "")[:10] or None,
                },
            )
            inserted += 1

        session.commit()
        _log_quality(session, "aep_designations", job_id, len(records), matched, rejected, inserted)
        _finish_job(session, job_id, "completed", len(records), inserted, rejected)
        session.commit()
        return {"inserted": inserted, "rejected": rejected}
    except Exception as e:
        _finish_job(session, job_id, "failed", 0, 0, 0, str(e))
        session.commit()
        session.close()
        raise


@celery_app.task(bind=True, name="src.tasks.ingest.ingest_eviction_filings")
def ingest_eviction_filings(self, job_id: Optional[int] = None):
    """Signal Batch 3: Eviction Filings."""
    session = _get_pg_session()
    job_id = _ensure_or_create_job(session, job_id, "evictions", "evictions")
    session.commit()

    try:
        pad_bins = dict(
            session.execute(text("SELECT bin, bbl FROM pad_addresses")).fetchall()
        )

        records = _socrata_fetch(DATASETS["eviction_filings"], {
            "$select": "court_index_number,executed_date,marshal_first_name,"
                       "marshal_last_name,eviction_address,borough,bin",
            "$order": "executed_date DESC",
            "$limit": 50000,
        })

        inserted = matched = rejected = 0
        for r in records:
            bin_val = r.get("bin")
            bbl = pad_bins.get(bin_val) if bin_val else None
            if not bbl:
                rejected += 1
                continue
            exists = session.execute(
                text("SELECT 1 FROM buildings WHERE bbl = :bbl"), {"bbl": bbl}
            ).first()
            if not exists:
                rejected += 1
                continue
            matched += 1

            session.execute(
                text("""
                    INSERT INTO eviction_filings (
                        case_index_number, bbl, executed_date,
                        marshal_first_name, marshal_last_name,
                        eviction_address, borough, created_at, updated_at
                    ) VALUES (
                        :case_num, :bbl, :executed, :first, :last,
                        :addr, :boro, now(), now()
                    ) ON CONFLICT (case_index_number) DO NOTHING
                """),
                {
                    "case_num": r.get("court_index_number"), "bbl": bbl,
                    "executed": r.get("executed_date", "")[:10] or None,
                    "first": r.get("marshal_first_name"),
                    "last": r.get("marshal_last_name"),
                    "addr": r.get("eviction_address"),
                    "boro": r.get("borough"),
                },
            )
            inserted += 1

        session.commit()
        _log_quality(session, "eviction_filings", job_id, len(records), matched, rejected, inserted)
        _finish_job(session, job_id, "completed", len(records), inserted, rejected)
        session.commit()
        return {"inserted": inserted, "rejected": rejected}
    except Exception as e:
        _finish_job(session, job_id, "failed", 0, 0, 0, str(e))
        session.commit()
        session.close()
        raise


@celery_app.task(bind=True, name="src.tasks.ingest.ingest_energy_grades")
def ingest_energy_grades(self, job_id: Optional[int] = None):
    """Signal Batch 3: LL33 Energy Grades."""
    session = _get_pg_session()
    job_id = _ensure_or_create_job(session, job_id, "energy", "energy")
    session.commit()

    try:
        records = _socrata_fetch(DATASETS["energy_grades"], {
            "$select": "bbl,lettergrade,score,year,propertyname,address",
            "$order": "year DESC",
            "$limit": 50000,
        })

        inserted = matched = rejected = 0
        for r in records:
            bbl = str(r.get("bbl", "")).strip()
            if not bbl or len(bbl) != 10:
                rejected += 1
                continue
            exists = session.execute(
                text("SELECT 1 FROM buildings WHERE bbl = :bbl"), {"bbl": bbl}
            ).first()
            if not exists:
                rejected += 1
                continue
            matched += 1

            session.execute(
                text("""
                    INSERT INTO energy_grades (
                        bbl, grade, score, year, property_name, address,
                        created_at, updated_at
                    ) VALUES (:bbl, :grade, :score, :year, :name, :addr, now(), now())
                    ON CONFLICT DO NOTHING
                """),
                {
                    "bbl": bbl, "grade": r.get("lettergrade"),
                    "score": float(r["score"]) if r.get("score") else None,
                    "year": int(r["year"]) if r.get("year") else None,
                    "name": r.get("propertyname"), "addr": r.get("address"),
                },
            )
            inserted += 1

        session.commit()
        _log_quality(session, "energy_grades", job_id, len(records), matched, rejected, inserted)
        _finish_job(session, job_id, "completed", len(records), inserted, rejected)
        session.commit()
        return {"inserted": inserted, "rejected": rejected}
    except Exception as e:
        _finish_job(session, job_id, "failed", 0, 0, 0, str(e))
        session.commit()
        session.close()
        raise


@celery_app.task(bind=True, name="src.tasks.ingest.ingest_facade_inspections")
def ingest_facade_inspections(self, job_id: Optional[int] = None):
    """Signal Batch 3: Facade Inspection / FISP / LL11."""
    session = _get_pg_session()
    job_id = _ensure_or_create_job(session, job_id, "facades", "facades")
    session.commit()

    try:
        pad_bins = dict(
            session.execute(text("SELECT bin, bbl FROM pad_addresses")).fetchall()
        )

        records = _socrata_fetch(DATASETS["facade_inspections"], {
            "$select": "bin,filing_date,filing_status,inspection_date,"
                       "report_filing_date,cycle_number",
            "$order": "filing_date DESC",
            "$limit": 50000,
        })

        inserted = matched = rejected = 0
        for r in records:
            bin_val = r.get("bin")
            bbl = pad_bins.get(bin_val) if bin_val else None
            if not bbl:
                rejected += 1
                continue
            exists = session.execute(
                text("SELECT 1 FROM buildings WHERE bbl = :bbl"), {"bbl": bbl}
            ).first()
            if not exists:
                rejected += 1
                continue
            matched += 1

            session.execute(
                text("""
                    INSERT INTO facade_inspections (
                        bbl, bin, filing_date, filing_status, inspection_date,
                        report_filing_date, cycle, created_at, updated_at
                    ) VALUES (
                        :bbl, :bin, :filed, :status, :inspected,
                        :reported, :cycle, now(), now()
                    ) ON CONFLICT DO NOTHING
                """),
                {
                    "bbl": bbl, "bin": bin_val,
                    "filed": r.get("filing_date", "")[:10] or None,
                    "status": r.get("filing_status"),
                    "inspected": r.get("inspection_date", "")[:10] or None,
                    "reported": r.get("report_filing_date", "")[:10] or None,
                    "cycle": r.get("cycle_number"),
                },
            )
            inserted += 1

        session.commit()
        _log_quality(session, "facade_inspections", job_id, len(records), matched, rejected, inserted)
        _finish_job(session, job_id, "completed", len(records), inserted, rejected)
        session.commit()
        return {"inserted": inserted, "rejected": rejected}
    except Exception as e:
        _finish_job(session, job_id, "failed", 0, 0, 0, str(e))
        session.commit()
        session.close()
        raise


@celery_app.task(bind=True, name="src.tasks.ingest.ingest_pad_addresses")
def ingest_pad_addresses(self, job_id: Optional[int] = None):
    """Reference data: PAD BIN-to-BBL crosswalk."""
    session = _get_pg_session()
    job_id = _ensure_or_create_job(session, job_id, "pad", "pad")
    session.commit()

    try:
        records = _socrata_fetch(DATASETS["pad"], {
            "$select": "bin,bbl,stname,boro",
            "$where": "bin IS NOT NULL AND bbl IS NOT NULL",
            "$limit": 50000,
        })

        inserted = 0
        for r in records:
            session.execute(
                text("""
                    INSERT INTO pad_addresses (bin, bbl, address, borough, created_at, updated_at)
                    VALUES (:bin, :bbl, :addr, :boro, now(), now())
                    ON CONFLICT DO NOTHING
                """),
                {
                    "bin": r.get("bin"), "bbl": str(r.get("bbl", "")),
                    "addr": r.get("stname"), "boro": r.get("boro"),
                },
            )
            inserted += 1
            if inserted % 10000 == 0:
                session.commit()

        session.commit()
        _log_quality(session, "pad", job_id, len(records), len(records), 0, inserted)
        _finish_job(session, job_id, "completed", len(records), inserted, 0)
        session.commit()
        return {"inserted": inserted}
    except Exception as e:
        _finish_job(session, job_id, "failed", 0, 0, 0, str(e))
        session.commit()
        session.close()
        raise
