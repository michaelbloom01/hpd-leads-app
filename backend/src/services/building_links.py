"""Guarded helpers for current building-management links."""

from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

EXPANDING_BBLS = bindparam("bbls", expanding=True)


def _normalize_bbls(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for value in values:
        bbl = str(value or "").strip()
        if not bbl or bbl in seen:
            continue
        seen.add(bbl)
        normalized.append(bbl)
    return normalized


def summarize_current_links(session: Session, bbls: Iterable[Any]) -> dict[str, dict[str, Any]]:
    normalized_bbls = _normalize_bbls(bbls)
    if not normalized_bbls:
        return {}

    rows = session.execute(
        text(
            """
            SELECT
                bm.bbl,
                ARRAY_AGG(bm.lead_id ORDER BY bm.lead_id, bm.id) AS current_lead_ids,
                COUNT(*)::int AS active_row_count
            FROM building_management bm
            WHERE bm.is_current = true
              AND bm.bbl IN :bbls
            GROUP BY bm.bbl
            """
        ).bindparams(EXPANDING_BBLS),
        {"bbls": normalized_bbls},
    ).fetchall()

    summary: dict[str, dict[str, Any]] = {}
    for row in rows:
        mapping = row._mapping
        current_lead_ids = [str(lead_id) for lead_id in (mapping.get("current_lead_ids") or []) if str(lead_id).strip()]
        distinct_lead_ids = sorted(set(current_lead_ids))
        active_row_count = int(mapping.get("active_row_count") or 0)
        summary[str(mapping["bbl"])] = {
            "bbl": str(mapping["bbl"]),
            "current_lead_ids": distinct_lead_ids,
            "active_row_count": active_row_count,
            "has_conflict": active_row_count > 1 or len(distinct_lead_ids) > 1,
        }
    return summary


def detect_current_link_conflicts(
    session: Session,
    bbls: Iterable[Any],
    keeper_lead_id: str,
) -> list[dict[str, Any]]:
    keeper = str(keeper_lead_id or "").strip()
    conflicts: list[dict[str, Any]] = []
    for summary in summarize_current_links(session, bbls).values():
        current_lead_ids = summary["current_lead_ids"]
        active_row_count = int(summary["active_row_count"])
        if active_row_count == 1 and current_lead_ids == [keeper]:
            continue
        if not summary["has_conflict"] and current_lead_ids == [keeper]:
            continue
        if current_lead_ids and current_lead_ids != [keeper]:
            conflicts.append(summary)
            continue
        if summary["has_conflict"]:
            conflicts.append(summary)
    return conflicts


def guarded_insert_current_links(
    session: Session,
    links: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    deduped_links: list[dict[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for link in links:
        bbl = str(link.get("bbl") or "").strip()
        lead_id = str(link.get("lead_id") or "").strip()
        role = str(link.get("role") or "agent").strip() or "agent"
        if not bbl or not lead_id:
            continue
        key = (bbl, lead_id)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        deduped_links.append({"bbl": bbl, "lead_id": lead_id, "role": role})

    summary = summarize_current_links(session, [link["bbl"] for link in deduped_links])
    safe_links: list[dict[str, str]] = []
    skipped_existing = 0
    conflicts: list[dict[str, Any]] = []

    for link in deduped_links:
        bbl = link["bbl"]
        lead_id = link["lead_id"]
        link_summary = summary.get(bbl)
        if not link_summary:
            safe_links.append(link)
            continue
        if int(link_summary["active_row_count"]) == 1 and link_summary["current_lead_ids"] == [lead_id]:
            skipped_existing += 1
            continue
        conflicts.append(link_summary)

    inserted = 0
    if safe_links:
        result = session.execute(
            text(
                """
                INSERT INTO building_management (bbl, lead_id, role, is_current, created_at, updated_at)
                SELECT :bbl, :lead_id, :role, true, now(), now()
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM building_management bm
                    WHERE bm.bbl = :bbl
                      AND bm.lead_id = :lead_id
                      AND bm.is_current = true
                )
                """
            ),
            safe_links,
        )
        inserted = int(result.rowcount or 0)

    deduped_conflicts: dict[str, dict[str, Any]] = {}
    for conflict in conflicts:
        deduped_conflicts[str(conflict["bbl"])] = conflict

    return {
        "inserted": inserted,
        "skipped_existing": skipped_existing,
        "conflicts": list(deduped_conflicts.values()),
    }


def preview_current_link_conflicts(
    session: Session,
    *,
    sample_limit: int = 10,
) -> dict[str, Any]:
    duplicate_rows = session.execute(
        text(
            """
            SELECT COALESCE(SUM(duplicate_count - 1), 0)::int AS rows_to_delete
            FROM (
                SELECT COUNT(*) AS duplicate_count
                FROM building_management
                WHERE is_current = true
                GROUP BY bbl, lead_id, role
                HAVING COUNT(*) > 1
            ) duplicate_groups
            """
        )
    ).scalar() or 0

    duplicate_groups = session.execute(
        text(
            """
            SELECT COUNT(*)::int
            FROM (
                SELECT 1
                FROM building_management
                WHERE is_current = true
                GROUP BY bbl, lead_id, role
                HAVING COUNT(*) > 1
            ) duplicate_groups
            """
        )
    ).scalar() or 0

    conflict_rows = session.execute(
        text(
            """
            SELECT
                bbl,
                role,
                COUNT(*)::int AS active_row_count,
                COUNT(DISTINCT lead_id)::int AS distinct_lead_count,
                ARRAY_AGG(DISTINCT lead_id ORDER BY lead_id) AS current_lead_ids
            FROM building_management
            WHERE is_current = true
            GROUP BY bbl, role
            HAVING COUNT(DISTINCT lead_id) > 1
            ORDER BY active_row_count DESC, bbl ASC, role ASC
            LIMIT :sample_limit
            """
        ),
        {"sample_limit": sample_limit},
    ).mappings().all()

    conflict_count = session.execute(
        text(
            """
            SELECT COUNT(*)::int
            FROM (
                SELECT 1
                FROM building_management
                WHERE is_current = true
                GROUP BY bbl, role
                HAVING COUNT(DISTINCT lead_id) > 1
            ) conflict_groups
            """
        )
    ).scalar() or 0

    conflict_building_count = session.execute(
        text(
            """
            SELECT COUNT(DISTINCT bbl)::int
            FROM (
                SELECT bbl, role
                FROM building_management
                WHERE is_current = true
                GROUP BY bbl, role
                HAVING COUNT(DISTINCT lead_id) > 1
            ) conflict_groups
            """
        )
    ).scalar() or 0

    return {
        "duplicate_groups": int(duplicate_groups),
        "duplicate_rows_to_delete": int(duplicate_rows),
        "multi_lead_conflict_groups": int(conflict_count),
        "multi_lead_conflict_buildings": int(conflict_building_count),
        "samples": [
            {
                "bbl": str(row["bbl"]),
                "role": str(row.get("role") or ""),
                "active_row_count": int(row.get("active_row_count") or 0),
                "distinct_lead_count": int(row.get("distinct_lead_count") or 0),
                "current_lead_ids": [str(value) for value in (row.get("current_lead_ids") or []) if str(value).strip()],
            }
            for row in conflict_rows
        ],
    }


def cleanup_current_link_duplicates(session: Session) -> dict[str, Any]:
    deleted = session.execute(
        text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY bbl, lead_id, role
                        ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST, id DESC
                    ) AS rn
                FROM building_management
                WHERE is_current = true
            )
            DELETE FROM building_management bm
            USING ranked
            WHERE bm.id = ranked.id
              AND ranked.rn > 1
            """
        )
    )

    session.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_bm_current_bbl_lead_role
            ON building_management (bbl, lead_id, role)
            WHERE is_current = true
            """
        )
    )
    session.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_bm_current_bbl_role
            ON building_management (bbl, role)
            WHERE is_current = true
            """
        )
    )

    remaining = preview_current_link_conflicts(session)
    return {
        "deleted_duplicate_rows": int(deleted.rowcount or 0),
        "exact_link_unique_index": "uq_bm_current_bbl_lead_role",
        "remaining_conflicts": remaining,
    }
