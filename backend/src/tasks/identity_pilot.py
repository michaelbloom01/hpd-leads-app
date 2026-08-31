"""Approval-gated HPD identity bootstrap without citywide projections or freshness."""

import json
import re

from sqlalchemy import text

from src.ingest.hpd_identity_pilot import (
    HPDIdentityPilotClient,
    IdentityPilotError,
    validate_bins,
)
from src.tasks.ingest import _ensure_or_create_job, _finish_job, _get_pg_session
from src.worker import app as celery_app

MAX_BEFORE_IMAGE_BYTES = 25_000_000
MAX_AFFECTED_ROWS = 5000
TABLES = ("physical_buildings", "building_parcel_links", "hpd_registration_snapshots")


def _params(snapshot: dict) -> dict:
    return {
        "bins": snapshot["bins"], "hpd_ids": snapshot["hpd_building_ids"],
        "links": json.dumps([{key: row[key] for key in ("bin", "bbl", "source_record_key")} for row in snapshot["parcel_links"]]),
        "versions": json.dumps([{key: row[key] for key in ("registration_id", "payload_hash", "bin", "hpd_building_id")} for row in snapshot["registration_snapshots"]]),
    }


PREDICATES = {
    "physical_buildings": "t.bin = ANY(CAST(:bins AS text[]))",
    "building_parcel_links": """
        t.bin = ANY(CAST(:bins AS text[])) AND t.source_system='hpd_registrations'
        AND (t.is_current OR EXISTS (
            SELECT 1 FROM jsonb_to_recordset(CAST(:links AS jsonb)) AS k(bin text,bbl text,source_record_key text)
            WHERE (t.bin,t.bbl,t.source_record_key)=(k.bin,k.bbl,k.source_record_key)
        ))
    """,
    "hpd_registration_snapshots": """
        t.bin = ANY(CAST(:bins AS text[])) AND t.hpd_building_id = ANY(CAST(:hpd_ids AS text[]))
        AND (t.is_current OR EXISTS (
            SELECT 1 FROM jsonb_to_recordset(CAST(:versions AS jsonb)) AS k(registration_id text,payload_hash text)
            WHERE (t.registration_id,t.payload_hash)=(k.registration_id,k.payload_hash)
        ))
    """,
}


def preview_identity_pilot(session, snapshot: dict) -> dict:
    """Read only. Counts are scoped to existing rows that publication can affect."""
    params = _params(snapshot)
    ready = bool(session.execute(text("""
        SELECT bool_and(to_regclass('public.' || name) IS NOT NULL)
        FROM unnest(ARRAY['physical_buildings','building_parcel_links','hpd_registration_snapshots',
                         'hpd_refresh_rollback_rows']) AS t(name)
    """)).scalar())
    errors, before_images = [], {}
    by_bin = {row["bin"]: row["source_record_key"] for row in snapshot["physical_buildings"]}
    by_hpd = {hpd: bin_value for bin_value, hpd in by_bin.items()}
    if not ready:
        errors.append("building_identity_schema_required")
    else:
        physical = session.execute(text("""
            SELECT bin,source_system,source_record_key FROM physical_buildings
            WHERE bin=ANY(CAST(:bins AS text[])) OR (source_system='hpd_registrations' AND source_record_key=ANY(CAST(:hpd_ids AS text[])))
        """), params).mappings()
        for row in physical:
            if row["source_system"] != "hpd_registrations" or by_bin.get(row["bin"]) != row["source_record_key"]:
                errors.append("persisted_physical_identity_conflict")
        current = session.execute(text("""
            SELECT bin,hpd_building_id FROM hpd_registration_snapshots
            WHERE is_current AND identity_status='official_hpd'
                AND (bin=ANY(CAST(:bins AS text[])) OR hpd_building_id=ANY(CAST(:hpd_ids AS text[])))
        """), params).mappings()
        for row in current:
            if by_bin.get(row["bin"]) != row["hpd_building_id"] or by_hpd.get(row["hpd_building_id"]) != row["bin"]:
                errors.append("persisted_registration_identity_conflict")
        version_conflicts = session.execute(text("""
            SELECT count(*) FROM hpd_registration_snapshots t
            JOIN jsonb_to_recordset(CAST(:versions AS jsonb)) AS k(registration_id text,payload_hash text,bin text,hpd_building_id text)
                ON (t.registration_id,t.payload_hash)=(k.registration_id,k.payload_hash)
            WHERE t.bin IS DISTINCT FROM k.bin OR t.hpd_building_id IS DISTINCT FROM k.hpd_building_id
        """), params).scalar()
        if version_conflicts:
            errors.append("persisted_version_identity_conflict")
        for table in TABLES:
            result = session.execute(text(
                f"SELECT count(*) AS rows,COALESCE(sum(pg_column_size(to_jsonb(t))),0) AS bytes FROM {table} t WHERE " + PREDICATES[table]
            ), params).mappings().one()
            before_images[table] = {"rows": int(result["rows"]), "bytes": int(result["bytes"])}
        if sum(row["rows"] for row in before_images.values()) > MAX_AFFECTED_ROWS:
            errors.append("existing_identity_scope_exceeds_row_limit")
        if sum(row["bytes"] for row in before_images.values()) > MAX_BEFORE_IMAGE_BYTES:
            errors.append("existing_identity_before_images_exceed_storage_limit")
    return {
        "dry_run": True, "business_rows_written": 0, "bins": snapshot["bins"],
        "hpd_building_ids": snapshot["hpd_building_ids"], "source_fingerprint": snapshot["source_fingerprint"],
        "source_stamps": snapshot["source_stamps"], "source_checks": snapshot["source_checks"],
        "observed_at": snapshot["observed_at"], "evidence_bytes": snapshot["evidence_bytes"],
        "planned": {"physical_buildings": len(snapshot["physical_buildings"]), "parcel_links": len(snapshot["parcel_links"]), "registration_versions": len(snapshot["registration_snapshots"])},
        "before_images": before_images, "validation_errors": sorted(set(errors)), "ready_to_execute": not errors,
        "global_freshness_updated": False, "legacy_buildings_updated": 0, "legacy_contacts_updated": 0,
        "rollback_manifest": "hpd_refresh_rollback_rows", "automatic_rollback": False,
    }


def publish_identity_pilot(session, snapshot: dict, *, job_id: int) -> dict:
    """Caller owns the atomic transaction. Every write stays in the reviewed BIN set."""
    if not session.execute(text("SELECT pg_try_advisory_xact_lock(7342186031)")).scalar():
        raise IdentityPilotError("another_hpd_identity_refresh_is_publishing")
    session.execute(text("SET LOCAL lock_timeout = '10s'"))
    preview = preview_identity_pilot(session, snapshot)
    if not preview["ready_to_execute"]:
        raise IdentityPilotError("identity_pilot_preflight_failed", validation_errors=preview["validation_errors"])
    params = {**_params(snapshot), "job_id": job_id}
    for table in TABLES:
        key = "t.bin" if table == "physical_buildings" else "t.id::text"
        session.execute(text(f"""
            INSERT INTO hpd_refresh_rollback_rows (ingestion_job_id,table_name,row_key,was_existing,before_payload)
            SELECT :job_id,:table_name,{key},true,to_jsonb(t) FROM {table} t WHERE {PREDICATES[table]}
            ON CONFLICT (ingestion_job_id,table_name,row_key) DO NOTHING
        """), {**params, "table_name": table})
    session.execute(text("""
        UPDATE hpd_registration_snapshots SET is_current=false,updated_at=now()
        WHERE bin=ANY(CAST(:bins AS text[])) AND hpd_building_id=ANY(CAST(:hpd_ids AS text[])) AND is_current
    """), params)
    session.execute(text("""
        UPDATE building_parcel_links SET is_current=false,updated_at=now()
        WHERE bin=ANY(CAST(:bins AS text[])) AND source_system='hpd_registrations' AND is_current
    """), params)
    session.execute(text("""
        INSERT INTO physical_buildings (bin,address,borough,zip_code,source_system,source_record_key,first_seen_at,last_seen_at)
        VALUES (:bin,:address,:borough,:zip,'hpd_registrations',:source_record_key,now(),now())
        ON CONFLICT (bin) DO UPDATE SET address=EXCLUDED.address,borough=EXCLUDED.borough,zip_code=EXCLUDED.zip_code,
            last_seen_at=now(),updated_at=now()
    """), snapshot["physical_buildings"])
    session.execute(text("""
        INSERT INTO building_parcel_links (bin,bbl,relationship_type,source_system,source_record_key,source_url,effective_from,effective_to,is_current,first_seen_at,last_seen_at)
        VALUES (:bin,:bbl,'hpd_registration','hpd_registrations',:source_record_key,:source_url,:effective_from,:effective_to,:is_current,now(),now())
        ON CONFLICT (bin,bbl,source_system,source_record_key) DO UPDATE SET is_current=EXCLUDED.is_current,
            effective_from=EXCLUDED.effective_from,effective_to=EXCLUDED.effective_to,
            source_url=EXCLUDED.source_url,last_seen_at=now(),updated_at=now()
    """), snapshot["parcel_links"])
    session.execute(text("""
        INSERT INTO hpd_registration_snapshots (registration_id,payload_hash,hpd_building_id,bin,bbl,last_registration_date,registration_end_date,is_current,
            identity_status,source_url,source_updated_at,raw_payload,first_seen_at,last_seen_at,ingestion_job_id)
        VALUES (:registration_id,:payload_hash,:hpd_building_id,:bin,:bbl,:last_registration_date,:registration_end_date,:is_current,
            :identity_status,:source_url,:source_updated_at,CAST(:raw_payload AS jsonb),now(),now(),:job_id)
        ON CONFLICT (registration_id,payload_hash) DO UPDATE SET is_current=EXCLUDED.is_current,identity_status=EXCLUDED.identity_status,
            source_updated_at=EXCLUDED.source_updated_at,last_seen_at=now(),updated_at=now(),ingestion_job_id=EXCLUDED.ingestion_job_id
    """), [{**row, "raw_payload": json.dumps(row["raw_payload"]), "job_id": job_id} for row in snapshot["registration_snapshots"]])
    # Scope predicates include all newly upserted natural keys. Existing rows
    # already have immutable before-images, so only new rows receive markers.
    for table in TABLES:
        key = "t.bin" if table == "physical_buildings" else "t.id::text"
        session.execute(text(f"""
            INSERT INTO hpd_refresh_rollback_rows (ingestion_job_id,table_name,row_key,was_existing,before_payload)
            SELECT :job_id,:table_name,{key},false,NULL FROM {table} t WHERE {PREDICATES[table]}
            ON CONFLICT (ingestion_job_id,table_name,row_key) DO NOTHING
        """), {**params, "table_name": table})
    return {**preview, "dry_run": False, "business_rows_written": sum(preview["planned"].values()), "published": True}


@celery_app.task(bind=True, name="src.tasks.identity_pilot.ingest_hpd_identity_pilot")
def ingest_hpd_identity_pilot(
    self, job_id: int | None = None, bins: list[str] | None = None, dry_run: bool = True,
    confirm_execute: bool = False, expected_source_fingerprint: str | None = None,
):
    bins = validate_bins(bins)
    if not dry_run and (not confirm_execute or not re.fullmatch(r"[0-9a-f]{64}", expected_source_fingerprint or "")):
        raise IdentityPilotError("execution_requires_confirmation_and_reviewed_source_fingerprint")
    session = _get_pg_session()
    job_type = "hpd_identity_pilot_preview" if dry_run else "hpd_identity_pilot"
    try:
        job_id = _ensure_or_create_job(session, job_id, job_type, job_type)
        session.commit()
        snapshot = HPDIdentityPilotClient().fetch_snapshot(bins)
        summary = preview_identity_pilot(session, snapshot)
        session.execute(text("""
            UPDATE ingestion_jobs SET config=COALESCE(config,'{}'::jsonb) || CAST(:result AS jsonb),total=:total WHERE id=:job_id
        """), {"job_id": job_id, "total": len(bins), "result": json.dumps({"result": summary, "bins": bins, "dry_run": dry_run})})
        session.commit()
        if not dry_run:
            if expected_source_fingerprint != snapshot["source_fingerprint"]:
                raise IdentityPilotError("source_changed_since_reviewed_identity_preview")
            summary = publish_identity_pilot(session, snapshot, job_id=job_id)
        summary["job_id"] = job_id
        session.execute(text("""
            UPDATE ingestion_jobs SET config=COALESCE(config,'{}'::jsonb) || CAST(:result AS jsonb) WHERE id=:job_id
        """), {"job_id": job_id, "result": json.dumps({"result": summary})})
        _finish_job(session, job_id, "completed", len(bins), len(bins), 0)
        session.commit()
        return summary
    except Exception as exc:
        session.rollback()
        if job_id is not None:
            if isinstance(exc, IdentityPilotError):
                session.execute(text("""
                    UPDATE ingestion_jobs SET config=COALESCE(config,'{}'::jsonb) || CAST(:result AS jsonb) WHERE id=:job_id
                """), {"job_id": job_id, "result": json.dumps({"identity_pilot_error": exc.details})})
            _finish_job(session, job_id, "failed", 0, 0, 1, str(exc)[:500])
            session.commit()
        raise
    finally:
        session.close()
