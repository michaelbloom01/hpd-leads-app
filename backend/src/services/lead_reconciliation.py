"""Lead materialization reconciliation previews and guarded execution."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from src.services.building_links import guarded_insert_current_links
from src.services.lead_generation import (
    OWNER_CONTACT_TYPES,
    compute_score,
    make_lead_id,
    summarize_portfolio_building_types,
    _display_name_for_group,
    _is_probably_junk_name,
    _is_seed_contact,
    _raw_name_from_contact,
)
from src.transform.normalize import normalize_name, normalize_name_for_grouping

logger = logging.getLogger(__name__)


BASELINE_PIPELINE_STAGES = {"", "new", "research"}
BASELINE_OUTREACH_STATUSES = {"", "none", "new"}
LEGAL_SUFFIXES = (
    " LLC",
    " L L C",
    " INC",
    " INCORPORATED",
    " CORP",
    " CORPORATION",
    " CO",
    " COMPANY",
    " LP",
    " LLP",
    " PLLC",
    " PC",
    " PA",
)
SOURCE_CONTACTS_SQL = text("""
    SELECT
        bc.bbl,
        bc.contact_type,
        bc.corporation_name,
        bc.first_name,
        bc.last_name,
        bc.business_address,
        b.address,
        b.borough,
        b.unit_count,
        b.building_class,
        b.building_type
    FROM building_contacts bc
    JOIN buildings b ON bc.bbl = b.bbl
    WHERE bc.contact_type IN ('Agent', 'ManagementCompany', 'Owner', 'CorporateOwner', 'IndividualOwner')
    ORDER BY bc.corporation_name NULLS LAST, bc.bbl
""")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _display_name(row: Mapping[str, Any]) -> str:
    return next(
        (
            _text(row.get(field))
            for field in ("company_name", "agent_name", "owner_name", "primary_contact", "normalized_name", "lead_id")
            if _text(row.get(field))
        ),
        "",
    )


def _canonical_grouping_key(row: Mapping[str, Any]) -> str:
    return normalize_name_for_grouping(_display_name(row))


def _legal_suffix_key(value: str) -> str:
    name = normalize_name(value)
    changed = True
    while changed:
        changed = False
        for suffix in LEGAL_SUFFIXES:
            if name.endswith(suffix):
                name = name[:-len(suffix)].strip()
                changed = True
    return name


def _has_user_state(row: Mapping[str, Any]) -> bool:
    pipeline_stage = _text(row.get("pipeline_stage")).lower()
    outreach_status = _text(row.get("outreach_status")).lower()
    if pipeline_stage not in BASELINE_PIPELINE_STAGES:
        return True
    if outreach_status not in BASELINE_OUTREACH_STATUSES:
        return True
    if _int(row.get("priority_rank")) > 0:
        return True
    if any(
        _text(row.get(field))
        for field in ("notes", "phone", "email", "website", "business_summary", "owner_principal")
    ):
        return True
    if row.get("last_enriched") is not None:
        return True
    return False


def _candidate_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "lead_id": _text(row.get("lead_id")),
        "display_name": _display_name(row),
        "normalized_name": _text(row.get("normalized_name")),
        "portfolio_size": _int(row.get("portfolio_size")),
        "total_units": _int(row.get("total_units")),
        "pipeline_stage": _text(row.get("pipeline_stage")),
        "outreach_status": _text(row.get("outreach_status")),
        "has_user_state": _has_user_state(row),
    }


def _empty_source_group(grouping_key: str) -> dict[str, Any]:
    return {
        "lead_id": make_lead_id(grouping_key),
        "normalized_name": grouping_key,
        "agent_name": None,
        "owner_name": None,
        "company_name": None,
        "entity_type": "unknown",
        "address": None,
        "boroughs": {},
        "bbl_set": set(),
        "address_set": set(),
        "unit_counts_by_bbl": {},
        "building_type_by_bbl": {},
        "building_classes": [],
    }


def _add_source_row_to_group(group: dict[str, Any], row: Any, display_name: str) -> None:
    bbl = _text(row.bbl)
    if not bbl:
        return

    if row.contact_type in {"Agent", "ManagementCompany"} and not group.get("agent_name"):
        group["agent_name"] = display_name
    if row.contact_type in OWNER_CONTACT_TYPES and not group.get("owner_name"):
        group["owner_name"] = display_name
    if _text(row.corporation_name) and not group.get("company_name"):
        group["company_name"] = _text(row.corporation_name)
    if _text(row.corporation_name):
        group["entity_type"] = "company"
    elif group.get("entity_type") != "company":
        group["entity_type"] = "individual_agent"
    if not group.get("address") and _text(row.business_address):
        group["address"] = _text(row.business_address)

    if bbl in group["bbl_set"]:
        return
    group["bbl_set"].add(bbl)
    group["unit_counts_by_bbl"][bbl] = _int(row.unit_count)
    group["building_type_by_bbl"][bbl] = _text(row.building_type)
    if _text(row.address):
        group["address_set"].add(_text(row.address))
    if _text(row.borough):
        group["boroughs"][_text(row.borough)] = group["boroughs"].get(_text(row.borough), 0) + 1
    if _text(row.building_class):
        group["building_classes"].append(_text(row.building_class))


def _load_source_groups(
    session: Session,
    legal_keys_by_grouping_key: dict[str, set[str]],
) -> dict[str, dict[str, Any]]:
    if not legal_keys_by_grouping_key:
        return {}

    groups: dict[str, dict[str, Any]] = {}
    for row in session.execute(SOURCE_CONTACTS_SQL).fetchall():
        if not _is_seed_contact(row):
            continue
        raw_name = _raw_name_from_contact(row)
        if _is_probably_junk_name(raw_name):
            continue
        grouping_key = normalize_name_for_grouping(raw_name)
        allowed_legal_keys = legal_keys_by_grouping_key.get(grouping_key)
        if not allowed_legal_keys:
            continue
        if _legal_suffix_key(raw_name) not in allowed_legal_keys:
            continue
        group = groups.setdefault(grouping_key, _empty_source_group(grouping_key))
        _add_source_row_to_group(group, row, _display_name_for_group(raw_name))
    return groups


def _source_group_to_upsert_payload(group: dict[str, Any]) -> dict[str, Any]:
    portfolio_size = len(group["bbl_set"])
    total_units = sum(_int(value) for value in group["unit_counts_by_bbl"].values())
    primary_borough = max(group["boroughs"], key=group["boroughs"].get) if group["boroughs"] else None
    building_types, _ = summarize_portfolio_building_types(
        [
            {
                "building_type": raw_type,
                "unit_count": group["unit_counts_by_bbl"].get(bbl, 0),
            }
            for bbl, raw_type in group.get("building_type_by_bbl", {}).items()
        ],
        total_buildings=portfolio_size,
    )
    return {
        "lead_id": group["lead_id"],
        "normalized_name": group["normalized_name"],
        "agent_name": group.get("agent_name"),
        "owner_name": group.get("owner_name"),
        "company_name": group.get("company_name"),
        "entity_type": group.get("entity_type") or "unknown",
        "address": group.get("address"),
        "primary_borough": primary_borough,
        "portfolio_size": portfolio_size,
        "total_units": total_units,
        "score": compute_score(portfolio_size, total_units),
        "score_breakdown": json.dumps({"components": {}}),
        "buildings": json.dumps(sorted(group["address_set"])),
        "boros": json.dumps(sorted(group["boroughs"].keys())),
        "building_types": json.dumps(building_types),
        "now": datetime.now(timezone.utc),
    }


def _upsert_canonical_lead(session: Session, group: dict[str, Any]) -> None:
    payload = _source_group_to_upsert_payload(group)
    session.execute(
        text("""
            INSERT INTO leads (
                lead_id, normalized_name, agent_name, owner_name,
                company_name, entity_type, address, primary_borough,
                portfolio_size, total_units, score, score_breakdown, buildings,
                boros, building_types, enrichment_status, pipeline_stage,
                outreach_status, priority_rank, created_at, updated_at,
                retired_at, superseded_by_lead_id, retirement_reason
            ) VALUES (
                :lead_id, :normalized_name, :agent_name, :owner_name,
                :company_name, :entity_type, :address, :primary_borough,
                :portfolio_size, :total_units, :score, CAST(:score_breakdown AS JSONB), CAST(:buildings AS JSONB),
                CAST(:boros AS JSONB), CAST(:building_types AS JSONB), 'none', 'research',
                'new', 0, :now, :now,
                NULL, NULL, NULL
            ) ON CONFLICT (lead_id) DO UPDATE SET
                normalized_name = EXCLUDED.normalized_name,
                agent_name = EXCLUDED.agent_name,
                owner_name = EXCLUDED.owner_name,
                company_name = EXCLUDED.company_name,
                entity_type = EXCLUDED.entity_type,
                address = EXCLUDED.address,
                primary_borough = EXCLUDED.primary_borough,
                portfolio_size = EXCLUDED.portfolio_size,
                total_units = EXCLUDED.total_units,
                score = EXCLUDED.score,
                score_breakdown = EXCLUDED.score_breakdown,
                buildings = EXCLUDED.buildings,
                boros = EXCLUDED.boros,
                building_types = EXCLUDED.building_types,
                pipeline_stage = CASE
                    WHEN COALESCE(NULLIF(BTRIM(leads.pipeline_stage), ''), 'research') = 'new'
                        THEN 'research'
                    ELSE COALESCE(NULLIF(BTRIM(leads.pipeline_stage), ''), 'research')
                END,
                outreach_status = CASE
                    WHEN COALESCE(NULLIF(BTRIM(leads.outreach_status), ''), 'new') = 'none'
                        THEN 'new'
                    ELSE COALESCE(NULLIF(BTRIM(leads.outreach_status), ''), 'new')
                END,
                retired_at = NULL,
                superseded_by_lead_id = NULL,
                retirement_reason = NULL,
                updated_at = EXCLUDED.updated_at
        """),
        payload,
    )


def _source_group_link_rows(group: dict[str, Any]) -> list[dict[str, str]]:
    role = "agent" if group.get("agent_name") else "owner"
    return [
        {"bbl": bbl, "lead_id": group["lead_id"], "role": role}
        for bbl in sorted(group["bbl_set"])
    ]


def _retire_superseded_leads(
    session: Session,
    *,
    lead_ids: list[str],
    canonical_lead_id: str,
    reason: str,
) -> int:
    retiring = [lead_id for lead_id in lead_ids if lead_id and lead_id != canonical_lead_id]
    if not retiring:
        return 0
    result = session.execute(
        text("""
            UPDATE leads
            SET retired_at = COALESCE(retired_at, NOW()),
                superseded_by_lead_id = :canonical_lead_id,
                retirement_reason = :reason,
                updated_at = NOW()
            WHERE lead_id IN :lead_ids
              AND lead_id <> :canonical_lead_id
        """).bindparams(bindparam("lead_ids", expanding=True)),
        {
            "lead_ids": retiring,
            "canonical_lead_id": canonical_lead_id,
            "reason": reason,
        },
    )
    return int(result.rowcount or 0)


def build_stale_lead_reconciliation_report(
    rows: Iterable[Mapping[str, Any]],
    *,
    current_link_count: int = 0,
    sample_limit: int = 25,
    search: str | None = None,
    grouping_key: str | None = None,
) -> dict[str, Any]:
    """Preview generated-lead dedupe drift without mutating the database."""
    materialized_rows = [
        dict(row)
        for row in rows
        if row.get("retired_at") is None
    ]
    search_value = _text(search).lower()
    grouping_key_filter = _text(grouping_key).upper()
    groups: dict[str, list[dict[str, Any]]] = {}
    status_drift = {
        "pipeline_new": 0,
        "outreach_none": 0,
        "non_baseline_user_state": 0,
    }

    for row in materialized_rows:
        pipeline_stage = _text(row.get("pipeline_stage")).lower()
        outreach_status = _text(row.get("outreach_status")).lower()
        if pipeline_stage == "new":
            status_drift["pipeline_new"] += 1
        if outreach_status == "none":
            status_drift["outreach_none"] += 1
        if _has_user_state(row):
            status_drift["non_baseline_user_state"] += 1

        grouping_key = _canonical_grouping_key(row)
        if grouping_key:
            groups.setdefault(grouping_key, []).append(row)

    duplicate_groups: list[dict[str, Any]] = []
    canonical_id_mismatches = 0
    for grouping_key, group_rows in groups.items():
        canonical_lead_id = make_lead_id(grouping_key)
        mismatched_rows = [row for row in group_rows if _text(row.get("lead_id")) != canonical_lead_id]
        if mismatched_rows:
            canonical_id_mismatches += len(mismatched_rows)

        should_report = len(group_rows) > 1 or bool(mismatched_rows)
        if grouping_key_filter:
            should_report = should_report and grouping_key == grouping_key_filter
        if search_value:
            should_report = should_report and any(search_value in _display_name(row).lower() for row in group_rows)
        if not should_report:
            continue

        sorted_rows = sorted(
            group_rows,
            key=lambda row: (_int(row.get("portfolio_size")), _int(row.get("total_units"))),
            reverse=True,
        )
        user_state_rows = [row for row in sorted_rows if _has_user_state(row)]
        legal_keys = sorted({
            key
            for key in (_legal_suffix_key(_display_name(row)) for row in sorted_rows)
            if key
        })
        bucket = "review_required"
        review_reasons: list[str] = []
        if user_state_rows:
            review_reasons.append("candidate_has_user_state")
        if len(legal_keys) > 1:
            review_reasons.append("broad_grouping_key_matches_multiple_legal_name_bases")
        if not review_reasons:
            bucket = "low_risk_legal_suffix_variant"

        duplicate_groups.append({
            "grouping_key": grouping_key,
            "canonical_lead_id": canonical_lead_id,
            "bucket": bucket,
            "review_reasons": review_reasons,
            "legal_name_keys": legal_keys,
            "candidate_count": len(sorted_rows),
            "candidate_leads": [_candidate_payload(row) for row in sorted_rows],
            "would_retire_lead_ids": [
                _text(row.get("lead_id"))
                for row in sorted_rows
                if _text(row.get("lead_id")) != canonical_lead_id
            ],
            "would_preserve_user_state": bool(user_state_rows),
            "combined_portfolio_size": sum(_int(row.get("portfolio_size")) for row in sorted_rows),
            "combined_total_units": sum(_int(row.get("total_units")) for row in sorted_rows),
        })

    duplicate_groups.sort(
        key=lambda group: (
            group["bucket"] != "low_risk_legal_suffix_variant",
            -group["candidate_count"],
            -group["combined_portfolio_size"],
            group["grouping_key"],
        )
    )

    return {
        "mode": "dry_run",
        "total_leads": len(materialized_rows),
        "current_building_management_links": int(current_link_count or 0),
        "leads_without_current_links": len(materialized_rows) if int(current_link_count or 0) == 0 else None,
        "canonical_group_count": len(groups),
        "canonical_id_mismatches": canonical_id_mismatches,
        "duplicate_group_count": len(duplicate_groups),
        "low_risk_legal_suffix_variant_count": sum(
            1 for group in duplicate_groups if group["bucket"] == "low_risk_legal_suffix_variant"
        ),
        "review_required_count": sum(1 for group in duplicate_groups if group["bucket"] == "review_required"),
        "status_drift": status_drift,
        "samples": duplicate_groups[:sample_limit],
    }


def preview_stale_lead_reconciliation(
    session: Session,
    *,
    sample_limit: int = 25,
    search: str | None = None,
    grouping_key: str | None = None,
) -> dict[str, Any]:
    """Load lead snapshots and return a read-only stale materialization report."""
    rows = session.execute(text("""
        SELECT
            lead_id,
            normalized_name,
            company_name,
            agent_name,
            owner_name,
            primary_contact,
            portfolio_size,
            total_units,
            pipeline_stage,
            outreach_status,
            priority_rank,
            notes,
            phone,
            email,
            website,
            business_summary,
            owner_principal,
            last_enriched,
            retired_at
        FROM leads
        WHERE retired_at IS NULL
    """)).mappings().all()
    current_link_count = session.execute(text("""
        SELECT COUNT(*)
        FROM building_management
        WHERE is_current = true
    """)).scalar() or 0
    return build_stale_lead_reconciliation_report(
        rows,
        current_link_count=int(current_link_count),
        sample_limit=sample_limit,
        search=search,
        grouping_key=grouping_key,
    )


def execute_stale_lead_reconciliation(
    session: Session,
    *,
    confirm_execute: bool = False,
    search: str | None = None,
    grouping_key: str | None = None,
    batch_size: int = 500,
    dry_run_sample_limit: int = 25,
) -> dict[str, Any]:
    """Repair low-risk generated-lead drift from source data.

    This intentionally refuses to run unless ``confirm_execute`` is true. The
    executor only handles groups already classified as low-risk legal-suffix
    variants, then leaves broad-name/user-state groups for manual review.
    """
    if not confirm_execute:
        raise ValueError("execute_stale_lead_reconciliation requires confirm_execute=True")

    report = preview_stale_lead_reconciliation(
        session,
        sample_limit=1_000_000,
        search=search,
        grouping_key=grouping_key,
    )
    executable_groups = [
        group
        for group in report["samples"]
        if group.get("bucket") == "low_risk_legal_suffix_variant"
        and group.get("would_retire_lead_ids")
    ]
    legal_keys_by_grouping_key = {
        _text(group.get("grouping_key")): {
            _text(legal_key)
            for legal_key in group.get("legal_name_keys") or []
            if _text(legal_key)
        }
        for group in executable_groups
        if _text(group.get("grouping_key"))
    }
    source_groups = _load_source_groups(session, legal_keys_by_grouping_key)

    processed_groups = 0
    canonical_leads_upserted = 0
    superseded_leads_retired = 0
    current_links_inserted = 0
    current_links_skipped_existing = 0
    current_link_conflicts = 0
    skipped_no_source = 0
    samples: list[dict[str, Any]] = []

    for idx, group in enumerate(executable_groups, start=1):
        grouping_key = _text(group.get("grouping_key"))
        source_group = source_groups.get(grouping_key)
        if not source_group or not source_group.get("bbl_set"):
            skipped_no_source += 1
            continue

        canonical_lead_id = _text(group.get("canonical_lead_id"))
        if source_group["lead_id"] != canonical_lead_id:
            logger.warning(
                "Skipping reconciliation group %s: report canonical %s != source canonical %s",
                grouping_key,
                canonical_lead_id,
                source_group["lead_id"],
            )
            skipped_no_source += 1
            continue

        _upsert_canonical_lead(session, source_group)
        canonical_leads_upserted += 1

        link_result = guarded_insert_current_links(session, _source_group_link_rows(source_group))
        current_links_inserted += int(link_result.get("inserted") or 0)
        current_links_skipped_existing += int(link_result.get("skipped_existing") or 0)
        current_link_conflicts += len(link_result.get("conflicts") or [])

        retired = _retire_superseded_leads(
            session,
            lead_ids=[_text(lead_id) for lead_id in group.get("would_retire_lead_ids") or []],
            canonical_lead_id=canonical_lead_id,
            reason="stale_materialization_legal_suffix_variant",
        )
        superseded_leads_retired += retired
        processed_groups += 1

        if len(samples) < dry_run_sample_limit:
            samples.append({
                "grouping_key": grouping_key,
                "canonical_lead_id": canonical_lead_id,
                "source_building_count": len(source_group["bbl_set"]),
                "source_total_units": sum(_int(value) for value in source_group["unit_counts_by_bbl"].values()),
                "retired_lead_count": retired,
                "links_inserted": int(link_result.get("inserted") or 0),
                "link_conflict_count": len(link_result.get("conflicts") or []),
            })

        if batch_size > 0 and idx % batch_size == 0:
            session.flush()

    return {
        "mode": "executed",
        "input": {
            "search": search,
            "grouping_key": grouping_key,
            "candidate_low_risk_groups": len(executable_groups),
            "review_required_groups": report["review_required_count"],
        },
        "processed_groups": processed_groups,
        "canonical_leads_upserted": canonical_leads_upserted,
        "superseded_leads_retired": superseded_leads_retired,
        "current_links_inserted": current_links_inserted,
        "current_links_skipped_existing": current_links_skipped_existing,
        "current_link_conflicts": current_link_conflicts,
        "skipped_no_source": skipped_no_source,
        "samples": samples,
    }
