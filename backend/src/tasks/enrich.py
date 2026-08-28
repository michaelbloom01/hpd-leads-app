"""Enrichment Celery tasks.

Wraps existing enrichment logic to run in background workers
instead of blocking the API server.
"""
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    from src.worker import app as celery_app
except ImportError:
    class _FakeCelery:
        @staticmethod
        def task(*args, **kwargs):
            return lambda fn: fn
    celery_app = _FakeCelery()

logger = logging.getLogger(__name__)


def _canonical_entity_name(value: str | None) -> str:
    """Normalize case, punctuation, and spacing for a strict entity-name comparison."""
    normalized = re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9]+", " ", (value or "").upper())).strip()
    return re.sub(r"\bOWNER S\b", "OWNERS", normalized)


def _classify_dos_entity_match(lookup_name: str | None, entity_name: str | None) -> str:
    """Classify the selected DOS record without treating substring matches as confirmed."""
    lookup_key = _canonical_entity_name(lookup_name)
    entity_key = _canonical_entity_name(entity_name)
    if lookup_key and lookup_key == entity_key:
        return "exact"
    return "possible" if lookup_key and entity_key else "unknown"


def _job_status_from_counts(succeeded: int, failed: int) -> str:
    if failed > 0 and succeeded == 0:
        return "failed"
    return "succeeded"


async def _run_enrichment_job_async(job_id: int, limit: int, lead_ids: list[str] | None = None) -> dict[str, Any]:
    from sqlalchemy import text
    from src.db.session import get_session_factory
    from src.routers.leads import enrich_lead_all_core

    session_factory = get_session_factory()
    safe_limit = max(1, int(limit or 1))
    requested_lead_ids = [str(lead_id).strip() for lead_id in (lead_ids or []) if str(lead_id).strip()]

    async with session_factory() as session:
        await session.execute(
            text("""
                UPDATE ingestion_jobs
                SET status = 'running',
                    started_at = COALESCE(started_at, now()),
                    processed = 0,
                    succeeded = 0,
                    failed = 0,
                    error = NULL,
                    updated_at = now()
                WHERE id = :job_id
            """),
            {"job_id": job_id},
        )
        if requested_lead_ids:
            requested_unique_ids = list(dict.fromkeys(requested_lead_ids))
            placeholders = ", ".join(f":lid_{i}" for i in range(len(requested_unique_ids)))
            params = {f"lid_{i}": lead_id for i, lead_id in enumerate(requested_unique_ids)}
            result = await session.execute(
                text(f"""
                    SELECT lead_id
                    FROM leads
                    WHERE lead_id IN ({placeholders})
                    ORDER BY score DESC NULLS LAST, updated_at ASC NULLS FIRST
                """),
                params,
            )
            target_lead_ids = [r[0] for r in result.fetchall()]
        else:
            result = await session.execute(
                text("""
                    SELECT lead_id
                    FROM leads
                    WHERE COALESCE(enrichment_status, 'none') IN ('none', 'pending', 'failed')
                    ORDER BY score DESC NULLS LAST, updated_at ASC NULLS FIRST
                    LIMIT :limit
                """),
                {"limit": safe_limit},
            )
            target_lead_ids = [r[0] for r in result.fetchall()]
        total = len(target_lead_ids)
        await session.execute(
            text("""
                UPDATE ingestion_jobs
                SET total = :total, updated_at = now()
                WHERE id = :job_id
            """),
            {"job_id": job_id, "total": total},
        )
        await session.commit()

    processed = 0
    succeeded = 0
    failed = 0
    last_error: str | None = None

    for lead_id in target_lead_ids:
        async with session_factory() as session:
            try:
                await enrich_lead_all_core(lead_id=lead_id, session=session)
                succeeded += 1
            except Exception as exc:  # pragma: no cover - defensive task isolation
                failed += 1
                last_error = str(exc)[:500]
                logger.warning("Enrichment failed for lead %s: %s", lead_id, exc)
            finally:
                processed += 1
                await session.execute(
                    text("""
                        UPDATE ingestion_jobs
                        SET processed = :processed,
                            succeeded = :succeeded,
                            failed = :failed,
                            status = 'running',
                            error = :error,
                            updated_at = now()
                        WHERE id = :job_id
                    """),
                    {
                        "job_id": job_id,
                        "processed": processed,
                        "succeeded": succeeded,
                        "failed": failed,
                        "error": last_error,
                    },
                )
                await session.commit()

    async with session_factory() as session:
        final_status = _job_status_from_counts(succeeded=succeeded, failed=failed)
        await session.execute(
            text("""
                UPDATE ingestion_jobs
                SET status = :status,
                    processed = :processed,
                    succeeded = :succeeded,
                    failed = :failed,
                    error = :error,
                    finished_at = now(),
                    updated_at = now()
                WHERE id = :job_id
            """),
            {
                "job_id": job_id,
                "status": final_status,
                "processed": processed,
                "succeeded": succeeded,
                "failed": failed,
                "error": last_error,
            },
        )
        await session.commit()

    return {
        "job_id": job_id,
        "status": _job_status_from_counts(succeeded=succeeded, failed=failed),
        "total": len(target_lead_ids),
        "processed": processed,
        "succeeded": succeeded,
        "failed": failed,
    }


@celery_app.task(bind=True, name="src.tasks.enrich.run_enrichment_job")
def run_enrichment_job(self, job_id: int, limit: int = 500, lead_ids: list[str] | None = None):
    """Run batch enrichment for top leads and update ingestion_jobs lifecycle."""
    return asyncio.run(_run_enrichment_job_async(job_id=job_id, limit=limit, lead_ids=lead_ids))


def _build_dos_cache_payload(corporate_owner_name: str, dos_entity: Any, officers: list[dict[str, Any]]) -> dict[str, Any]:
    entity_name = getattr(dos_entity, "name", None)
    return {
        "lookup_name": corporate_owner_name,
        "dos_id": getattr(dos_entity, "dos_id", None),
        "entity_name": entity_name,
        "entity_match_status": _classify_dos_entity_match(corporate_owner_name, entity_name),
        "entity_type": getattr(dos_entity, "entity_type", None),
        "officers": officers or [],
        "ceo_name": getattr(dos_entity, "ceo_name", None),
        "ceo_address": getattr(dos_entity, "ceo_address", None),
        "snapshot_as_of": datetime.now(timezone.utc).date().isoformat(),
    }


def _build_board_chair_cache_payload(
    corporate_owner_name: str,
    entities: list[Any],
    *,
    snapshot_as_of: str,
) -> dict[str, Any]:
    """Build an auditable chair result without promoting ambiguous DOS matches."""
    distinct_entities = {
        str(getattr(entity, "dos_id", "") or ""): entity
        for entity in entities
        if str(getattr(entity, "dos_id", "") or "").strip()
    }
    matches = list(distinct_entities.values())
    base: dict[str, Any] = {
        "lookup_name": corporate_owner_name,
        "officers": [],
        "snapshot_as_of": snapshot_as_of,
        "source_name": "NY Department of State Active Corporations",
        "source_dataset": "n9v6-gdp6",
        "source_field": "chairman_name",
        "source_role_label": "Chairman or CEO",
    }
    if not matches:
        return {
            **base,
            "entity_match_status": "unknown",
            "chair_status": "no_named_chair_match",
            "ceo_name": None,
            "ceo_address": None,
        }
    if len(matches) > 1:
        return {
            **base,
            "entity_match_status": "ambiguous",
            "chair_status": "ambiguous_entity",
            "candidate_dos_ids": sorted(distinct_entities),
            "ceo_name": None,
            "ceo_address": None,
        }

    entity = matches[0]
    chairman_name = str(getattr(entity, "ceo_name", "") or "").strip() or None
    dos_id = str(getattr(entity, "dos_id", "") or "").strip() or None
    return {
        **base,
        "dos_id": dos_id,
        "entity_name": getattr(entity, "name", None),
        "entity_match_status": "exact",
        "entity_type": getattr(entity, "entity_type", None),
        "chair_status": "named_chair" if chairman_name else "exact_no_chair",
        "ceo_name": chairman_name,
        "ceo_address": getattr(entity, "ceo_address", None),
        "source_url": f"https://data.ny.gov/resource/n9v6-gdp6.json?dos_id={dos_id}" if dos_id else None,
        "role_confidence": "medium",
    }


@celery_app.task(bind=True, name="src.tasks.enrich.refresh_board_chairs_from_dos")
def refresh_board_chairs_from_dos(self, job_id: int | None = None, limit: int = 50000):
    """Refresh exact-match DOS chair evidence for current co-op/condo owner entities."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from src.db.session import get_sync_url
    from src.enrich.ny_dos import NYDOSClient
    from src.tasks.ingest import _ensure_or_create_job, _finish_job, _log_quality

    safe_limit = max(1, min(int(limit or 1), 50000))
    engine = create_engine(get_sync_url())
    session = Session(engine)
    job_id = _ensure_or_create_job(session, job_id, "board_chairs", "ny_dos_cache")
    session.commit()
    processed = succeeded = failed = 0

    try:
        rows = session.execute(
            text("""
                SELECT DISTINCT ON (bc.bbl)
                    bc.bbl,
                    TRIM(bc.corporation_name) AS corporate_owner_name
                FROM building_contacts bc
                JOIN buildings b ON b.bbl = bc.bbl
                WHERE UPPER(COALESCE(bc.contact_type, '')) = 'CORPORATEOWNER'
                  AND TRIM(COALESCE(bc.corporation_name, '')) <> ''
                  AND (
                      UPPER(COALESCE(b.building_type, '')) LIKE '%CONDO%'
                      OR UPPER(COALESCE(b.building_type, '')) LIKE '%COOP%'
                      OR UPPER(COALESCE(b.building_type, '')) LIKE '%CO-OP%'
                      OR UPPER(COALESCE(b.building_class, '')) LIKE 'R%'
                      OR UPPER(COALESCE(b.building_class, '')) IN ('C6', 'C8', 'D0', 'D4')
                  )
                ORDER BY bc.bbl, bc.updated_at DESC NULLS LAST, bc.id DESC
                LIMIT :limit
            """),
            {"limit": safe_limit},
        ).fetchall()
        targets = [
            {"bbl": str(row.bbl), "owner_name": str(row.corporate_owner_name).strip()}
            for row in rows
        ]
        total = len(targets)
        session.execute(
            text("UPDATE ingestion_jobs SET total = :total, updated_at = now() WHERE id = :job_id"),
            {"job_id": job_id, "total": total},
        )
        session.commit()

        client = NYDOSClient(app_token=os.environ.get("NY_DATA_APP_TOKEN"))
        owner_names = list(dict.fromkeys(target["owner_name"] for target in targets))
        exact_matches = client.lookup_chairmen_for_exact_names(owner_names)
        snapshot_as_of = datetime.now(timezone.utc).date().isoformat()
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=30)
        canonicalize = client._canonical_exact_name
        status_counts = {
            "named_chair": 0,
            "exact_no_chair": 0,
            "ambiguous_entity": 0,
            "no_named_chair_match": 0,
        }

        for offset in range(0, total, 1000):
            target_batch = targets[offset:offset + 1000]
            cache_rows = []
            for target in target_batch:
                payload = _build_board_chair_cache_payload(
                    target["owner_name"],
                    exact_matches.get(canonicalize(target["owner_name"]), []),
                    snapshot_as_of=snapshot_as_of,
                )
                status_counts[payload["chair_status"]] += 1
                cache_rows.append({
                    "cache_key": f"officers:{target['bbl']}",
                    "result": json.dumps(payload),
                    "cached_at": now,
                    "expires_at": expires_at,
                })

            if cache_rows:
                session.execute(
                    text("""
                        INSERT INTO dos_cache (cache_key, result, cached_at, expires_at)
                        VALUES (:cache_key, :result, :cached_at, :expires_at)
                        ON CONFLICT (cache_key) DO UPDATE SET
                            result = EXCLUDED.result,
                            cached_at = EXCLUDED.cached_at,
                            expires_at = EXCLUDED.expires_at
                    """),
                    cache_rows,
                )
            processed += len(target_batch)
            succeeded += len(target_batch)
            session.execute(
                text("""
                    UPDATE ingestion_jobs
                    SET processed = :processed, succeeded = :succeeded,
                        failed = :failed, updated_at = now()
                    WHERE id = :job_id
                """),
                {
                    "job_id": job_id,
                    "processed": processed,
                    "succeeded": succeeded,
                    "failed": failed,
                },
            )
            session.commit()

        _log_quality(
            session,
            "ny_dos_cache",
            job_id,
            total,
            status_counts["named_chair"],
            status_counts["ambiguous_entity"] + status_counts["no_named_chair_match"],
            succeeded,
            notes=json.dumps({
                "mode": "exact_entity_name_batch",
                "eligible_buildings": total,
                **status_counts,
            }),
        )
        _finish_job(session, job_id, "completed", total, succeeded, failed)
        session.commit()
        return {
            "job_id": job_id,
            "processed": processed,
            "succeeded": succeeded,
            "failed": failed,
            **status_counts,
        }
    except Exception as exc:
        session.rollback()
        _finish_job(session, job_id, "failed", processed, succeeded, failed, str(exc))
        session.commit()
        raise
    finally:
        session.close()
        engine.dispose()


@celery_app.task(bind=True, name="src.tasks.enrich.refresh_dos_contacts")
def refresh_dos_contacts(self, bbl: str, corporate_owner_name: str):
    """Refresh DOS officers into dos_cache for a building."""
    from sqlalchemy import create_engine, text
    from src.db.session import get_sync_url
    from src.enrich.ny_dos import NYDOSClient

    owner_name = (corporate_owner_name or "").strip()
    if not bbl or not owner_name:
        return {"status": "skipped", "reason": "missing_bbl_or_owner"}

    client = NYDOSClient(app_token=os.environ.get("NY_DATA_APP_TOKEN"))
    dos_entity = client.lookup_entity(owner_name)
    if not dos_entity:
        payload = {
            "lookup_name": owner_name,
            "officers": [],
            "ceo_name": None,
            "ceo_address": None,
            "snapshot_as_of": datetime.now(timezone.utc).date().isoformat(),
        }
    else:
        officers = client.get_filing_officers(dos_entity.dos_id)
        payload = _build_dos_cache_payload(
            corporate_owner_name=owner_name,
            dos_entity=dos_entity,
            officers=officers,
        )

    now = datetime.now(timezone.utc)
    cache_key = f"officers:{bbl}"

    engine = create_engine(get_sync_url())
    try:
        with engine.begin() as conn:
            params = {
                "cache_key": cache_key,
                "result": json.dumps(payload),
                "cached_at": now,
            }
            conn.execute(
                text(
                    """
                    INSERT INTO dos_cache (cache_key, result, cached_at)
                    VALUES (:cache_key, :result, :cached_at)
                    ON CONFLICT (cache_key)
                    DO UPDATE SET
                        result = EXCLUDED.result,
                        cached_at = EXCLUDED.cached_at
                    """
                ),
                params,
            )
    finally:
        engine.dispose()

    return {
        "status": "success",
        "bbl": bbl,
        "cache_key": cache_key,
        "officers_count": len(payload.get("officers") or []),
    }


@celery_app.task(bind=True, name="src.tasks.enrich.enrich_lead")
def enrich_lead(self, lead_id: str):
    """Enrich a single lead in worker-safe async DB context."""
    from src.db.session import get_session_factory
    from src.routers.leads import enrich_lead_all_core

    async def _run() -> dict[str, Any]:
        session_factory = get_session_factory()
        async with session_factory() as session:
            try:
                await enrich_lead_all_core(lead_id=lead_id, session=session)
                return {"status": "completed", "lead_id": lead_id}
            except Exception as exc:
                logger.exception("Enrichment failed for lead %s: %s", lead_id, exc)
                return {"status": "failed", "lead_id": lead_id, "error": str(exc)[:500]}

    return asyncio.run(_run())


@celery_app.task(bind=True, name="src.tasks.enrich.enrich_batch")
def enrich_batch(self, lead_ids: list[str]):
    """Enrich a batch of leads; queue via Celery when available."""
    results = {"queued": 0, "completed": 0, "failed": 0}
    can_queue = hasattr(enrich_lead, "delay")
    for lid in lead_ids:
        try:
            if can_queue:
                enrich_lead.delay(lid)
                results["queued"] += 1
            else:
                outcome = enrich_lead(None, lid)
                if outcome.get("status") == "completed":
                    results["completed"] += 1
                else:
                    results["failed"] += 1
        except Exception as exc:
            logger.error("Enrichment failed for %s: %s", lid, exc)
            results["failed"] += 1
    if can_queue:
        results["status"] = "queued"
    else:
        results["status"] = "completed"
    return results
