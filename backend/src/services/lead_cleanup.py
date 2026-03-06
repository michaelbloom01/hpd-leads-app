"""Conservative orphan-lead cleanup helpers."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.orm import Session

from src.db.session import get_sync_url

logger = logging.getLogger(__name__)

DEFAULT_SAMPLE_LIMIT = 10
DEFAULT_EXECUTION_BATCH_SIZE = 500


ORPHAN_CANDIDATE_SQL = text("""
    WITH current_link_counts AS (
        SELECT lead_id, COUNT(*) AS current_link_count
        FROM building_management
        WHERE is_current = true
        GROUP BY lead_id
    ),
    zero_link_leads AS (
        SELECT
            l.lead_id,
            l.normalized_name,
            l.company_name,
            l.agent_name,
            l.owner_name,
            l.primary_contact,
            l.pipeline_stage,
            l.outreach_status,
            l.next_follow_up,
            COALESCE(l.priority_rank, 0) AS priority_rank,
            l.notes,
            l.phone,
            l.email,
            l.website,
            l.business_summary,
            l.owner_principal,
            l.enrichment_status,
            l.last_enriched,
            COALESCE(l.enrichment_retries, 0) AS enrichment_retries,
            COALESCE(cl.current_link_count, 0) AS current_link_count
        FROM leads l
        LEFT JOIN current_link_counts cl ON cl.lead_id = l.lead_id
        WHERE COALESCE(cl.current_link_count, 0) = 0
    ),
    keeper_matches AS (
        SELECT
            zl.lead_id AS orphan_lead_id,
            k.lead_id AS keeper_lead_id
        FROM zero_link_leads zl
        JOIN leads k
          ON k.lead_id <> zl.lead_id
         AND NULLIF(BTRIM(k.normalized_name), '') = NULLIF(BTRIM(zl.normalized_name), '')
        JOIN current_link_counts kcl
          ON kcl.lead_id = k.lead_id
         AND kcl.current_link_count > 0
    ),
    keeper_rollup AS (
        SELECT
            orphan_lead_id,
            COUNT(*) AS keeper_count,
            MIN(keeper_lead_id) AS keeper_lead_id
        FROM keeper_matches
        GROUP BY orphan_lead_id
    ),
    orphan_events AS (
        SELECT lead_id, COUNT(*) AS outreach_event_count
        FROM outreach_events
        WHERE lead_id IS NOT NULL
        GROUP BY lead_id
    ),
    orphan_enrichment AS (
        SELECT lead_id, COUNT(*) AS enrichment_result_count
        FROM enrichment_results
        GROUP BY lead_id
    ),
    orphan_links AS (
        SELECT lead_id, COUNT(*) AS historical_link_count
        FROM building_management
        GROUP BY lead_id
    )
    SELECT
        zl.*,
        COALESCE(kr.keeper_count, 0) AS keeper_count,
        kr.keeper_lead_id,
        COALESCE(oe.outreach_event_count, 0) AS outreach_event_count,
        COALESCE(er.enrichment_result_count, 0) AS enrichment_result_count,
        COALESCE(ol.historical_link_count, 0) AS historical_link_count
    FROM zero_link_leads zl
    LEFT JOIN keeper_rollup kr ON kr.orphan_lead_id = zl.lead_id
    LEFT JOIN orphan_events oe ON oe.lead_id = zl.lead_id
    LEFT JOIN orphan_enrichment er ON er.lead_id = zl.lead_id
    LEFT JOIN orphan_links ol ON ol.lead_id = zl.lead_id
    ORDER BY zl.lead_id
""")

EXPANDING_LEAD_IDS = bindparam("lead_ids", expanding=True)


def _get_session() -> tuple[Session, Any]:
    engine = create_engine(get_sync_url())
    return Session(engine), engine


def _has_text(value: Any) -> bool:
    return bool(str(value or "").strip())


def _row_has_user_state(row: dict[str, Any]) -> bool:
    if str(row.get("pipeline_stage") or "research").strip().lower() != "research":
        return True
    if str(row.get("outreach_status") or "new").strip().lower() != "new":
        return True
    if row.get("next_follow_up") is not None:
        return True
    if int(row.get("priority_rank") or 0) > 0:
        return True
    if _has_text(row.get("notes")):
        return True
    if int(row.get("outreach_event_count") or 0) > 0:
        return True
    if int(row.get("enrichment_result_count") or 0) > 0:
        return True
    if any(
        _has_text(row.get(field))
        for field in ("phone", "email", "website", "business_summary", "owner_principal")
    ):
        return True
    if row.get("last_enriched") is not None:
        return True
    return False


def _row_has_blank_display(row: dict[str, Any]) -> bool:
    return not any(
        _has_text(row.get(field))
        for field in ("company_name", "agent_name", "owner_name", "primary_contact")
    )


def _row_is_retire_only_candidate(row: dict[str, Any]) -> bool:
    if int(row.get("keeper_count") or 0) != 0:
        return False
    if not _row_has_blank_display(row):
        return False
    if _row_has_user_state(row):
        return False
    if int(row.get("historical_link_count") or 0) > 0:
        return False
    return True


def _classify_orphan_row(row: dict[str, Any]) -> str:
    keeper_count = int(row.get("keeper_count") or 0)
    if _row_is_retire_only_candidate(row):
        return "safe_orphan_retire_only"
    if keeper_count != 1:
        return "ambiguous_orphan"
    if _row_has_user_state(row):
        return "orphan_with_user_state"
    return "safe_orphan_with_clear_keeper"


def _load_orphan_candidates(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(ORPHAN_CANDIDATE_SQL).mappings().all()
    candidates: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        row["classification"] = _classify_orphan_row(row)
        candidates.append(row)
    return candidates


def preview_orphan_lead_cleanup(sample_limit: int = DEFAULT_SAMPLE_LIMIT) -> dict[str, Any]:
    session, engine = _get_session()
    try:
        candidates = _load_orphan_candidates(session)
        counts = defaultdict(int)
        samples: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for row in candidates:
            classification = str(row["classification"])
            counts["total_zero_link_leads"] += 1
            counts[classification] += 1
            if len(samples[classification]) < sample_limit:
                samples[classification].append({
                    "lead_id": row["lead_id"],
                    "keeper_lead_id": row.get("keeper_lead_id"),
                    "normalized_name": row.get("normalized_name"),
                    "display_name": (
                        row.get("company_name")
                        or row.get("agent_name")
                        or row.get("owner_name")
                        or row.get("lead_id")
                    ),
                    "outreach_event_count": int(row.get("outreach_event_count") or 0),
                    "enrichment_result_count": int(row.get("enrichment_result_count") or 0),
                    "historical_link_count": int(row.get("historical_link_count") or 0),
                })

        actionable = counts["safe_orphan_with_clear_keeper"] + counts["orphan_with_user_state"]
        return {
            "counts": {
                "total_zero_link_leads": counts["total_zero_link_leads"],
                "safe_orphan_with_clear_keeper": counts["safe_orphan_with_clear_keeper"],
                "safe_orphan_retire_only": counts["safe_orphan_retire_only"],
                "orphan_with_user_state": counts["orphan_with_user_state"],
                "ambiguous_orphan": counts["ambiguous_orphan"],
                "actionable_orphans": actionable + counts["safe_orphan_retire_only"],
            },
            "samples": dict(samples),
        }
    finally:
        session.close()
        engine.dispose()


MERGE_KEEPER_SQL = text("""
    UPDATE leads keeper
    SET
        pipeline_stage = CASE
            WHEN COALESCE(NULLIF(BTRIM(keeper.pipeline_stage), ''), 'research') <> 'research'
                THEN keeper.pipeline_stage
            WHEN COALESCE(NULLIF(BTRIM(orphan.pipeline_stage), ''), 'research') <> 'research'
                THEN orphan.pipeline_stage
            ELSE keeper.pipeline_stage
        END,
        outreach_status = CASE
            WHEN COALESCE(NULLIF(BTRIM(keeper.outreach_status), ''), 'new') <> 'new'
                THEN keeper.outreach_status
            WHEN COALESCE(NULLIF(BTRIM(orphan.outreach_status), ''), 'new') <> 'new'
                THEN orphan.outreach_status
            ELSE keeper.outreach_status
        END,
        next_follow_up = CASE
            WHEN keeper.next_follow_up IS NULL THEN orphan.next_follow_up
            WHEN orphan.next_follow_up IS NULL THEN keeper.next_follow_up
            ELSE LEAST(keeper.next_follow_up, orphan.next_follow_up)
        END,
        priority_rank = GREATEST(COALESCE(keeper.priority_rank, 0), COALESCE(orphan.priority_rank, 0)),
        notes = CASE
            WHEN NULLIF(BTRIM(keeper.notes), '') IS NULL THEN orphan.notes
            WHEN NULLIF(BTRIM(orphan.notes), '') IS NULL THEN keeper.notes
            WHEN POSITION(orphan.notes IN keeper.notes) > 0 THEN keeper.notes
            ELSE keeper.notes || E'\n\nMerged from retired lead ' || orphan.lead_id || E':\n' || orphan.notes
        END,
        phone = COALESCE(NULLIF(BTRIM(keeper.phone), ''), NULLIF(BTRIM(orphan.phone), ''), keeper.phone),
        email = COALESCE(NULLIF(BTRIM(keeper.email), ''), NULLIF(BTRIM(orphan.email), ''), keeper.email),
        website = COALESCE(NULLIF(BTRIM(keeper.website), ''), NULLIF(BTRIM(orphan.website), ''), keeper.website),
        business_summary = COALESCE(
            NULLIF(BTRIM(keeper.business_summary), ''),
            NULLIF(BTRIM(orphan.business_summary), ''),
            keeper.business_summary
        ),
        owner_principal = COALESCE(
            NULLIF(BTRIM(keeper.owner_principal), ''),
            NULLIF(BTRIM(orphan.owner_principal), ''),
            keeper.owner_principal
        ),
        enrichment_status = CASE
            WHEN COALESCE(NULLIF(BTRIM(keeper.enrichment_status), ''), 'none') <> 'none'
                THEN keeper.enrichment_status
            WHEN COALESCE(NULLIF(BTRIM(orphan.enrichment_status), ''), 'none') <> 'none'
                THEN orphan.enrichment_status
            ELSE keeper.enrichment_status
        END,
        last_enriched = COALESCE(keeper.last_enriched, orphan.last_enriched),
        enrichment_retries = GREATEST(COALESCE(keeper.enrichment_retries, 0), COALESCE(orphan.enrichment_retries, 0)),
        updated_at = NOW()
    FROM leads orphan
    WHERE keeper.lead_id = :keeper_lead_id
      AND orphan.lead_id = :orphan_lead_id
""")


def _chunks(items: list[dict[str, Any]], chunk_size: int) -> list[list[dict[str, Any]]]:
    return [items[idx: idx + chunk_size] for idx in range(0, len(items), chunk_size)]


def execute_orphan_lead_cleanup(batch_size: int = DEFAULT_EXECUTION_BATCH_SIZE) -> dict[str, Any]:
    session, engine = _get_session()
    try:
        preview = preview_orphan_lead_cleanup(sample_limit=DEFAULT_SAMPLE_LIMIT)
        candidates = _load_orphan_candidates(session)
        actionable = [
            row
            for row in candidates
            if row["classification"] in {"safe_orphan_with_clear_keeper", "orphan_with_user_state"}
            and row.get("keeper_lead_id")
        ]
        retire_only = [
            row
            for row in candidates
            if row["classification"] == "safe_orphan_retire_only"
        ]

        if not actionable and not retire_only:
            return {
                "preview": preview,
                "migrated_orphans": 0,
                "retired_without_keeper": 0,
                "deleted_orphans": 0,
                "updated_outreach_events": 0,
                "updated_enrichment_results": 0,
                "updated_change_alerts": 0,
                "updated_historical_links": 0,
            }

        updated_outreach_events = 0
        updated_enrichment_results = 0
        updated_change_alerts = 0
        updated_historical_links = 0
        retired_without_keeper = 0
        deleted_orphans = 0

        for chunk in _chunks(actionable, batch_size):
            orphan_ids = [str(row["lead_id"]) for row in chunk]
            mapping_rows = [
                {"orphan_lead_id": str(row["lead_id"]), "keeper_lead_id": str(row["keeper_lead_id"])}
                for row in chunk
            ]

            for row in mapping_rows:
                session.execute(MERGE_KEEPER_SQL, row)

            session.execute(text("DROP TABLE IF EXISTS tmp_orphan_cleanup_map"))
            session.execute(text("""
                CREATE TEMP TABLE tmp_orphan_cleanup_map (
                    orphan_lead_id TEXT PRIMARY KEY,
                    keeper_lead_id TEXT NOT NULL
                ) ON COMMIT DROP
            """))
            session.execute(
                text("""
                    INSERT INTO tmp_orphan_cleanup_map (orphan_lead_id, keeper_lead_id)
                    VALUES (:orphan_lead_id, :keeper_lead_id)
                """),
                mapping_rows,
            )

            updated_outreach_events += int(session.execute(text("""
                UPDATE outreach_events oe
                SET lead_id = map.keeper_lead_id,
                    updated_at = NOW()
                FROM tmp_orphan_cleanup_map map
                WHERE oe.lead_id = map.orphan_lead_id
            """)).rowcount or 0)

            updated_enrichment_results += int(session.execute(text("""
                UPDATE enrichment_results er
                SET lead_id = map.keeper_lead_id,
                    updated_at = NOW()
                FROM tmp_orphan_cleanup_map map
                WHERE er.lead_id = map.orphan_lead_id
            """)).rowcount or 0)

            updated_change_alerts += int(session.execute(text("""
                UPDATE change_alerts ca
                SET lead_id = map.keeper_lead_id,
                    updated_at = NOW()
                FROM tmp_orphan_cleanup_map map
                WHERE ca.lead_id = map.orphan_lead_id
            """)).rowcount or 0)

            updated_historical_links += int(session.execute(text("""
                UPDATE building_management bm
                SET lead_id = map.keeper_lead_id,
                    updated_at = NOW()
                FROM tmp_orphan_cleanup_map map
                WHERE bm.lead_id = map.orphan_lead_id
            """)).rowcount or 0)

            deleted_orphans += int(session.execute(
                text("DELETE FROM leads WHERE lead_id IN :lead_ids").bindparams(EXPANDING_LEAD_IDS),
                {"lead_ids": orphan_ids},
            ).rowcount or 0)

            session.commit()
            logger.info(
                "Orphan cleanup chunk committed: retired=%s of %s",
                deleted_orphans,
                len(actionable) + len(retire_only),
            )

        for chunk in _chunks(retire_only, batch_size):
            orphan_ids = [str(row["lead_id"]) for row in chunk]
            deleted = int(session.execute(
                text("DELETE FROM leads WHERE lead_id IN :lead_ids").bindparams(EXPANDING_LEAD_IDS),
                {"lead_ids": orphan_ids},
            ).rowcount or 0)
            deleted_orphans += deleted
            retired_without_keeper += deleted
            session.commit()
            logger.info(
                "Retire-only orphan cleanup chunk committed: retired=%s of %s",
                deleted_orphans,
                len(actionable) + len(retire_only),
            )

        return {
            "preview": preview,
            "migrated_orphans": len(actionable),
            "retired_without_keeper": retired_without_keeper,
            "deleted_orphans": deleted_orphans,
            "updated_outreach_events": updated_outreach_events,
            "updated_enrichment_results": updated_enrichment_results,
            "updated_change_alerts": updated_change_alerts,
            "updated_historical_links": updated_historical_links,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        engine.dispose()

