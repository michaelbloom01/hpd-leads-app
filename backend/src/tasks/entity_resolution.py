"""Graph-based entity resolution with prep-first safety guardrails."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.orm import Session

from src.services.canonical_entities import sync_canonical_proposals
from src.services.building_links import detect_current_link_conflicts, guarded_insert_current_links

try:
    import networkx as nx
except ImportError:  # pragma: no cover - exercised only in minimal envs
    nx = None

try:
    from src.worker import app as celery_app
except ImportError:
    class _FakeCelery:
        @staticmethod
        def task(*args, **kwargs):
            return lambda fn: fn

    celery_app = _FakeCelery()

logger = logging.getLogger(__name__)

DEFAULT_SAMPLE_LIMIT = 10
EXECUTABLE_COHORTS = {"safe_keep"}
EXPANDING_BBLS = bindparam("bbls", expanding=True)
EXPANDING_NAMES = bindparam("names", expanding=True)
ENTITY_RESOLUTION_ROLLBACK_STRATEGY = (
    "Entity-resolution execute mode only writes approved safe_keep clusters. "
    "Use the run/job config rollback samples to restore prior portfolio_size values and remove newly inserted "
    "current building links if the run must be backed out; review_required, unresolved, and safe_retire buckets "
    "are never auto-written."
)


def _get_pg_session() -> Session:
    from src.db.session import get_compatible_sync_url

    engine = create_engine(get_compatible_sync_url())
    return Session(engine)


def _normalize(s: Optional[str]) -> str:
    if not s:
        return ""
    return " ".join(s.upper().strip().split())


def _has_text(value: Any) -> bool:
    return bool(str(value or "").strip())


def _display_name_from_row(row: dict[str, Any]) -> str:
    return str(
        row.get("company_name")
        or row.get("agent_name")
        or row.get("owner_name")
        or row.get("primary_contact")
        or row.get("lead_id")
        or ""
    ).strip()


def _row_has_blank_display_name(row: dict[str, Any]) -> bool:
    return not any(
        _has_text(row.get(field))
        for field in ("company_name", "agent_name", "owner_name", "primary_contact")
    )


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
    if any(
        _has_text(row.get(field))
        for field in ("phone", "email", "website", "business_summary", "owner_principal")
    ):
        return True
    if row.get("last_enriched") is not None:
        return True
    return False


def _build_entity_graph(session: Session) -> nx.Graph:
    """Build entity resolution graph from building contacts."""
    if nx is None:
        raise RuntimeError("entity_resolution requires networkx to build canonical clusters")
    G = nx.Graph()

    contacts = session.execute(text("""
        SELECT bc.bbl, bc.registration_id, bc.contact_type, bc.description,
               bc.corporation_name, bc.first_name, bc.last_name,
               bc.business_address, bc.business_city, bc.business_state, bc.business_zip
        FROM building_contacts bc
        WHERE bc.contact_type IN ('CorporateOwner', 'IndividualOwner', 'Agent', 'HeadOfficer', 'Owner')
    """)).fetchall()

    logger.info("Building entity graph from %s contacts", len(contacts))

    address_to_nodes = defaultdict(set)

    for c in contacts:
        bbl, _reg_id, _ctype, _desc, corp, first, last, addr, city, state, _zip_code = c

        corp_name = _normalize(corp)
        person_name = _normalize(f"{first or ''} {last or ''}".strip())
        full_addr = _normalize(f"{addr or ''} {city or ''} {state or ''}")

        if corp_name:
            node_id = f"corp:{corp_name}"
            G.add_node(node_id, type="corporation", name=corp_name, bbls=set())
            G.nodes[node_id]["bbls"].add(bbl)

            if full_addr and len(full_addr) > 5:
                address_to_nodes[full_addr].add(node_id)

        if person_name and len(person_name) > 3:
            node_id = f"person:{person_name}"
            if node_id not in G:
                G.add_node(node_id, type="person", name=person_name, bbls=set())
            G.nodes[node_id]["bbls"].add(bbl)

            if corp_name:
                G.add_edge(f"corp:{corp_name}", node_id, relation="officer_of")

            if full_addr and len(full_addr) > 5:
                address_to_nodes[full_addr].add(node_id)

    for _addr, nodes in address_to_nodes.items():
        node_list = list(nodes)
        for i in range(len(node_list)):
            for j in range(i + 1, len(node_list)):
                G.add_edge(node_list[i], node_list[j], relation="shared_address")

    logger.info("Entity graph: %s nodes, %s edges", G.number_of_nodes(), G.number_of_edges())
    return G


def _resolve_clusters(G: nx.Graph) -> list[dict[str, Any]]:
    """Extract connected components and create canonical lead representations."""
    if nx is None:
        raise RuntimeError("entity_resolution requires networkx to resolve canonical clusters")
    components = list(nx.connected_components(G))
    logger.info("Found %s entity clusters", len(components))

    leads: list[dict[str, Any]] = []
    for component in components:
        bbls = set()
        names = []
        corp_names = []

        for node in component:
            data = G.nodes[node]
            bbls.update(data.get("bbls", set()))
            if data.get("type") == "corporation":
                corp_names.append(data["name"])
            elif data.get("type") == "person":
                names.append(data["name"])

        canonical_name = corp_names[0] if corp_names else names[0] if names else "Unknown"
        all_names = list(set(corp_names + names))

        leads.append({
            "canonical_name": canonical_name,
            "all_names": all_names,
            "bbls": list(bbls),
            "portfolio_size": len(bbls),
            "node_count": len(component),
            "graph_json": {
                "nodes": [{"id": n, **{k: v for k, v in G.nodes[n].items() if k != "bbls"}} for n in component],
                "edges": [{"source": u, "target": v, **d} for u, v, d in G.edges(component, data=True)],
            },
        })

    leads.sort(key=lambda x: x["portfolio_size"], reverse=True)
    return leads


def _cluster_names(cluster: dict[str, Any]) -> list[str]:
    names = {_normalize(cluster.get("canonical_name"))}
    for name in cluster.get("all_names") or []:
        normalized = _normalize(name)
        if normalized:
            names.add(normalized)
    return sorted(name for name in names if name)


def _load_cluster_candidate_rows(session: Session, cluster: dict[str, Any]) -> list[dict[str, Any]]:
    rows_by_lead_id: dict[str, dict[str, Any]] = {}
    cluster_bbls = [str(bbl) for bbl in cluster.get("bbls") or [] if str(bbl).strip()]
    cluster_names = _cluster_names(cluster)

    if cluster_bbls:
        overlap_rows = session.execute(
            text("""
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
                    l.last_enriched,
                    COUNT(DISTINCT bm.bbl) AS overlapping_current_links
                FROM building_management bm
                JOIN leads l ON l.lead_id = bm.lead_id
                WHERE bm.is_current = true
                  AND bm.bbl IN :bbls
                GROUP BY
                    l.lead_id,
                    l.normalized_name,
                    l.company_name,
                    l.agent_name,
                    l.owner_name,
                    l.primary_contact,
                    l.pipeline_stage,
                    l.outreach_status,
                    l.next_follow_up,
                    l.priority_rank,
                    l.notes,
                    l.phone,
                    l.email,
                    l.website,
                    l.business_summary,
                    l.owner_principal,
                    l.last_enriched
            """).bindparams(EXPANDING_BBLS),
            {"bbls": cluster_bbls},
        ).mappings().all()
        for raw in overlap_rows:
            row = dict(raw)
            row["overlapping_current_links"] = int(row.get("overlapping_current_links") or 0)
            rows_by_lead_id[str(row["lead_id"])] = row

    if cluster_names:
        candidate_rows = session.execute(
            text("""
                WITH current_link_counts AS (
                    SELECT lead_id, COUNT(*) AS current_link_count
                    FROM building_management
                    WHERE is_current = true
                    GROUP BY lead_id
                )
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
                    l.last_enriched,
                    COALESCE(cl.current_link_count, 0) AS current_link_count
                FROM leads l
                LEFT JOIN current_link_counts cl ON cl.lead_id = l.lead_id
                WHERE NULLIF(BTRIM(l.normalized_name), '') IN :names
                   OR UPPER(TRIM(COALESCE(
                        NULLIF(l.company_name, ''),
                        NULLIF(l.agent_name, ''),
                        NULLIF(l.owner_name, ''),
                        NULLIF(l.primary_contact, ''),
                        l.lead_id
                   ))) IN :names
            """).bindparams(EXPANDING_NAMES),
            {"names": cluster_names},
        ).mappings().all()
        for raw in candidate_rows:
            lead_id = str(raw["lead_id"])
            existing = rows_by_lead_id.get(lead_id, {})
            merged = {
                **dict(raw),
                **existing,
            }
            merged["current_link_count"] = int(raw.get("current_link_count") or merged.get("current_link_count") or 0)
            merged["overlapping_current_links"] = int(existing.get("overlapping_current_links") or 0)
            rows_by_lead_id[lead_id] = merged

    normalized_rows: list[dict[str, Any]] = []
    for row in rows_by_lead_id.values():
        row["current_link_count"] = int(row.get("current_link_count") or 0)
        row["overlapping_current_links"] = int(row.get("overlapping_current_links") or 0)
        row["has_active_links"] = row["current_link_count"] > 0
        row["blank_display_name"] = _row_has_blank_display_name(row)
        row["has_user_state"] = _row_has_user_state(row)
        row["display_name"] = _display_name_from_row(row)
        normalized_rows.append(row)

    normalized_rows.sort(
        key=lambda row: (
            -int(row.get("overlapping_current_links") or 0),
            -int(row.get("current_link_count") or 0),
            str(row.get("lead_id") or ""),
        )
    )
    return normalized_rows


def _classify_cluster_candidates(
    cluster: dict[str, Any],
    candidate_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    active_overlap_rows = [row for row in candidate_rows if int(row.get("overlapping_current_links") or 0) > 0]
    active_candidate_rows = [row for row in candidate_rows if bool(row.get("has_active_links"))]

    keeper_row: dict[str, Any] | None = None
    if len(active_overlap_rows) == 1:
        keeper_row = active_overlap_rows[0]
    elif not active_overlap_rows and len(active_candidate_rows) == 1:
        keeper_row = active_candidate_rows[0]

    keeper_lead_id = str(keeper_row["lead_id"]) if keeper_row else None
    non_keeper_rows = [row for row in candidate_rows if str(row.get("lead_id")) != keeper_lead_id]
    zero_link_siblings = [row for row in non_keeper_rows if not row.get("has_active_links")]
    blank_display_siblings = [row for row in non_keeper_rows if row.get("blank_display_name")]
    user_state_siblings = [row for row in non_keeper_rows if row.get("has_user_state")]
    active_non_keeper_rows = [row for row in non_keeper_rows if row.get("has_active_links")]

    blocked_reasons: list[str] = []
    if int(cluster.get("portfolio_size") or 0) <= 0:
        blocked_reasons.append("no_linked_buildings")
    if keeper_row is None:
        if len(active_overlap_rows) > 1:
            blocked_reasons.append("multiple_current_linked_leads")
        elif len(active_candidate_rows) > 1:
            blocked_reasons.append("multiple_active_keeper_candidates")
        else:
            blocked_reasons.append("no_clear_keeper")
    if active_non_keeper_rows:
        blocked_reasons.append("non_keeper_has_active_links")
    if user_state_siblings:
        blocked_reasons.append("non_keeper_has_user_state")
    if blank_display_siblings:
        blocked_reasons.append("blank_display_tail_present")
    if zero_link_siblings:
        blocked_reasons.append("zero_link_tail_present")

    has_corporation_evidence = any(
        str(node.get("type") or "") == "corporation"
        for node in (cluster.get("graph_json") or {}).get("nodes", [])
    )
    has_person_evidence = any(
        str(node.get("type") or "") == "person"
        for node in (cluster.get("graph_json") or {}).get("nodes", [])
    )

    all_non_keeper_retire_only = bool(non_keeper_rows) and all(
        (not row.get("has_active_links"))
        and bool(row.get("blank_display_name"))
        and (not row.get("has_user_state"))
        for row in non_keeper_rows
    )

    if keeper_row and not non_keeper_rows:
        bucket = "safe_keep"
    elif keeper_row and all_non_keeper_retire_only:
        bucket = "safe_retire"
    elif keeper_row:
        bucket = "review_required"
    else:
        bucket = "unresolved"

    return {
        "canonical_name": cluster.get("canonical_name"),
        "bucket": bucket,
        "keeper_lead_id": keeper_lead_id,
        "portfolio_size": int(cluster.get("portfolio_size") or 0),
        "node_count": int(cluster.get("node_count") or 0),
        "candidate_lead_count": len(candidate_rows),
        "active_overlap_count": len(active_overlap_rows),
        "active_candidate_count": len(active_candidate_rows),
        "zero_link_sibling_count": len(zero_link_siblings),
        "blank_display_name_sibling_count": len(blank_display_siblings),
        "user_state_sibling_count": len(user_state_siblings),
        "blocked_reasons": blocked_reasons,
        "signals": {
            "cluster_names": _cluster_names(cluster),
            "has_corporation_evidence": has_corporation_evidence,
            "has_person_evidence": has_person_evidence,
            "safe_to_execute": bucket in EXECUTABLE_COHORTS and keeper_row is not None,
        },
        "candidate_leads": [
            {
                "lead_id": str(row.get("lead_id")),
                "display_name": row.get("display_name") or row.get("lead_id"),
                "normalized_name": row.get("normalized_name"),
                "current_link_count": int(row.get("current_link_count") or 0),
                "overlapping_current_links": int(row.get("overlapping_current_links") or 0),
                "blank_display_name": bool(row.get("blank_display_name")),
                "has_user_state": bool(row.get("has_user_state")),
                "is_keeper_candidate": str(row.get("lead_id")) == keeper_lead_id,
            }
            for row in candidate_rows
        ],
    }


def _classify_cluster_for_prep(session: Session, cluster: dict[str, Any]) -> dict[str, Any]:
    return _classify_cluster_candidates(cluster, _load_cluster_candidate_rows(session, cluster))


def _cluster_signal_summary(cluster: dict[str, Any]) -> dict[str, Any]:
    graph_nodes = (cluster.get("graph_json") or {}).get("nodes", [])
    return {
        "cluster_names": _cluster_names(cluster),
        "has_corporation_evidence": any(str(node.get("type") or "") == "corporation" for node in graph_nodes),
        "has_person_evidence": any(str(node.get("type") or "") == "person" for node in graph_nodes),
        "safe_to_execute": False,
    }


def _conservative_prep_row(
    cluster: dict[str, Any],
    *,
    blocked_reason: str = "candidate_scan_skipped_for_materialization",
) -> dict[str, Any]:
    return {
        "canonical_name": cluster.get("canonical_name"),
        "bucket": "review_required",
        "keeper_lead_id": None,
        "portfolio_size": int(cluster.get("portfolio_size") or 0),
        "node_count": int(cluster.get("node_count") or 0),
        "candidate_lead_count": 0,
        "active_overlap_count": 0,
        "active_candidate_count": 0,
        "zero_link_sibling_count": 0,
        "blank_display_name_sibling_count": 0,
        "user_state_sibling_count": 0,
        "blocked_reasons": [blocked_reason],
        "review_reasons": [blocked_reason],
        "canonical_reasons": [blocked_reason],
        "signals": {
            **_cluster_signal_summary(cluster),
            "conservative_mode": True,
        },
        "candidate_leads": [],
    }


def _empty_counts() -> dict[str, int]:
    return {
        "total_clusters": 0,
        "safe_keep": 0,
        "safe_retire": 0,
        "review_required": 0,
        "unresolved": 0,
        "safe_execution_candidates": 0,
    }


def _accumulate_counts(counts: dict[str, int], row: dict[str, Any]) -> None:
    bucket = str(row.get("bucket") or "unresolved")
    counts["total_clusters"] += 1
    counts[bucket] = int(counts.get(bucket) or 0) + 1
    if bucket == "safe_keep":
        counts["safe_execution_candidates"] += 1


def _preview_from_clusters(clusters: list[dict[str, Any]], session: Session, sample_limit: int) -> dict[str, Any]:
    prep_rows = [_classify_cluster_for_prep(session, cluster) for cluster in clusters]
    counts = defaultdict(int)
    samples: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in prep_rows:
        bucket = str(row["bucket"])
        counts["total_clusters"] += 1
        counts[bucket] += 1
        if len(samples[bucket]) < sample_limit:
            samples[bucket].append({
                "canonical_name": row.get("canonical_name"),
                "keeper_lead_id": row.get("keeper_lead_id"),
                "portfolio_size": row.get("portfolio_size"),
                "candidate_lead_count": row.get("candidate_lead_count"),
                "blocked_reasons": row.get("blocked_reasons"),
                "candidate_leads": row.get("candidate_leads"),
            })

    counts_dict = {
        "total_clusters": counts["total_clusters"],
        "safe_keep": counts["safe_keep"],
        "safe_retire": counts["safe_retire"],
        "review_required": counts["review_required"],
        "unresolved": counts["unresolved"],
        "safe_execution_candidates": counts["safe_keep"],
    }
    return {
        "counts": counts_dict,
        "samples": dict(samples),
        "guardrails": {
            "default_mode": "dry_run",
            "requires_confirm_execute": True,
            "allowed_execution_cohorts": sorted(EXECUTABLE_COHORTS),
            "blocked_buckets": ["review_required", "unresolved", "safe_retire"],
        },
        "clusters": prep_rows,
    }


def preview_entity_resolution(sample_limit: int = DEFAULT_SAMPLE_LIMIT) -> dict[str, Any]:
    """Read-only canonical-prep preview for entity resolution clusters."""
    session = _get_pg_session()
    engine = session.get_bind()
    try:
        clusters = _resolve_clusters(_build_entity_graph(session))
        preview = _preview_from_clusters(clusters, session, sample_limit=sample_limit)
        return {
            "counts": preview["counts"],
            "samples": preview["samples"],
            "guardrails": preview["guardrails"],
        }
    finally:
        session.close()
        if engine is not None:
            engine.dispose()


def materialize_canonical_proposals(
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    conservative_mode: bool = False,
    commit_batch_size: int = 500,
) -> dict[str, Any]:
    """Persist canonical entity proposals without mutating live lead/building links."""
    session = _get_pg_session()
    engine = session.get_bind()
    try:
        clusters = _resolve_clusters(_build_entity_graph(session))
        if conservative_mode:
            clusters = sorted(
                clusters,
                key=lambda cluster: (
                    int(cluster.get("node_count") or 0),
                    int(cluster.get("portfolio_size") or 0),
                    str(cluster.get("canonical_name") or ""),
                ),
            )
        counts = _empty_counts()
        proposal_sync = {
            "entities_synced": 0,
            "aliases_synced": 0,
            "lead_links_synced": 0,
            "building_links_synced": 0,
            "proposals_synced": 0,
        }

        batch_rows: list[dict[str, Any]] = []
        batch_clusters: list[dict[str, Any]] = []

        for index, cluster in enumerate(clusters, start=1):
            if conservative_mode:
                row = _conservative_prep_row(cluster)
            else:
                try:
                    row = _classify_cluster_for_prep(session, cluster)
                except Exception as exc:
                    logger.warning(
                        "Canonical materialization fell back to conservative mode for cluster=%s error=%s",
                        cluster.get("canonical_name"),
                        exc,
                    )
                    row = _conservative_prep_row(cluster, blocked_reason="candidate_scan_failed_during_materialization")

            _accumulate_counts(counts, row)
            batch_rows.append(row)
            batch_clusters.append(cluster)

            if len(batch_rows) >= max(1, commit_batch_size):
                batch_sync = sync_canonical_proposals(session, prep_rows=batch_rows, clusters=batch_clusters)
                for key, value in batch_sync.items():
                    proposal_sync[key] += int(value or 0)
                session.commit()
                if index % 1000 == 0 or index == len(clusters):
                    logger.info(
                        "Canonical materialization progress: %s/%s clusters persisted (conservative=%s)",
                        index,
                        len(clusters),
                        conservative_mode,
                    )
                batch_rows = []
                batch_clusters = []

        if batch_rows:
            batch_sync = sync_canonical_proposals(session, prep_rows=batch_rows, clusters=batch_clusters)
            for key, value in batch_sync.items():
                proposal_sync[key] += int(value or 0)
            session.commit()

        return {
            "counts": counts,
            "guardrails": {
                "default_mode": "dry_run",
                "requires_confirm_execute": True,
                "allowed_execution_cohorts": sorted(EXECUTABLE_COHORTS),
                "blocked_buckets": ["review_required", "unresolved", "safe_retire"],
                "materialization_mode": "conservative" if conservative_mode else "full",
                "sample_limit_used_for_preview_only": sample_limit,
            },
            "proposal_sync": proposal_sync,
        }
    finally:
        session.close()
        if engine is not None:
            engine.dispose()


def _store_job_config(session: Session, job_id: int, config: dict[str, Any]) -> None:
    session.execute(
        text("""
            UPDATE ingestion_jobs
            SET config = CAST(:config AS JSONB),
                updated_at = NOW()
            WHERE id = :job_id
        """),
        {"job_id": job_id, "config": json.dumps(config)},
    )


def _count_keeper_links(session: Session, lead_id: str) -> int:
    return int(
        session.execute(
            text("""
                SELECT COUNT(*)
                FROM building_management
                WHERE lead_id = :lead_id
                  AND is_current = true
            """),
            {"lead_id": lead_id},
        ).scalar()
        or 0
    )


@celery_app.task(bind=True, name="src.tasks.entity_resolution.resolve_entities")
def resolve_entities(
    self,
    *args,
    job_id: Optional[int] = None,
    dry_run: bool = True,
    confirm_execute: bool = False,
    cohort_filter: Optional[str] = None,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
):
    """Run entity resolution in dry-run mode by default with cohort-limited execution."""
    if args:
        if len(args) > 5:
            raise TypeError("resolve_entities accepts at most job_id, dry_run, confirm_execute, cohort_filter, and sample_limit positional args")
        positional = list(args)
        if positional[0] is None:
            positional = positional[1:]
        fields = ["job_id", "dry_run", "confirm_execute", "cohort_filter", "sample_limit"]
        values = {
            "job_id": job_id,
            "dry_run": dry_run,
            "confirm_execute": confirm_execute,
            "cohort_filter": cohort_filter,
            "sample_limit": sample_limit,
        }
        for field, value in zip(fields, positional):
            values[field] = value
        job_id = values["job_id"]
        dry_run = values["dry_run"]
        confirm_execute = values["confirm_execute"]
        cohort_filter = values["cohort_filter"]
        sample_limit = values["sample_limit"]

    session = _get_pg_session()
    engine = session.get_bind()

    try:
        from src.tasks.ingest import _ensure_or_create_job, _finish_job

        job_id = _ensure_or_create_job(session, job_id, "entity_resolution", "leads")
        run_id = f"entity-resolution-{job_id}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
        session.commit()

        clusters = _resolve_clusters(_build_entity_graph(session))
        preview = _preview_from_clusters(clusters, session, sample_limit=sample_limit)
        preview_counts = preview["counts"]
        prep_rows = preview["clusters"]

        base_config = {
            "mode": "dry_run" if dry_run or not confirm_execute else "execute",
            "dry_run": bool(dry_run),
            "confirm_execute": bool(confirm_execute),
            "cohort_filter": cohort_filter,
            "write_permitted": bool((not dry_run) and confirm_execute and cohort_filter in EXECUTABLE_COHORTS),
            "preview": {
                "counts": preview_counts,
                "guardrails": preview["guardrails"],
                "samples": preview["samples"],
            },
            "run_id": run_id,
            "rollback_strategy": ENTITY_RESOLUTION_ROLLBACK_STRATEGY,
        }

        base_config["proposal_sync"] = None

        if dry_run or not confirm_execute:
            _store_job_config(session, job_id, base_config)
            session.commit()
            _finish_job(session, job_id, "completed", len(prep_rows), 0, 0)
            session.commit()
            logger.info("Entity resolution dry-run captured %s cluster previews", len(prep_rows))
            return {
                "run_id": run_id,
                "job_id": job_id,
                "clusters": len(prep_rows),
                "updated": 0,
                "mode": "dry_run",
                "preview_counts": preview_counts,
                "samples": preview["samples"],
                "guardrails": preview["guardrails"],
                "rollback_strategy": "Dry-run mode made no changes.",
            }

        if cohort_filter not in EXECUTABLE_COHORTS:
            raise ValueError(
                f"Unsafe cohort_filter={cohort_filter!r}. Allowed execution cohorts: {sorted(EXECUTABLE_COHORTS)}"
            )

        eligible_rows = [
            row for row in prep_rows if row.get("bucket") == cohort_filter and row.get("keeper_lead_id")
        ]

        execution_conflicts: list[dict[str, Any]] = []
        for row in eligible_rows:
            cluster = next(
                (candidate for candidate in clusters if candidate.get("canonical_name") == row.get("canonical_name")),
                None,
            )
            cluster_conflicts = detect_current_link_conflicts(
                session,
                (cluster or {}).get("bbls", []),
                str(row["keeper_lead_id"]),
            )
            if cluster_conflicts:
                execution_conflicts.append(
                    {
                        "canonical_name": row.get("canonical_name"),
                        "keeper_lead_id": str(row["keeper_lead_id"]),
                        "conflicts": cluster_conflicts,
                    }
                )
        if execution_conflicts:
            raise ValueError(
                "entity_resolution execution blocked by current building-management conflicts: "
                + json.dumps(execution_conflicts[:sample_limit])
            )

        proposal_sync_summary = sync_canonical_proposals(
            session,
            prep_rows=prep_rows,
            clusters=clusters,
        )
        base_config["proposal_sync"] = proposal_sync_summary

        updated = 0
        rollback_samples: list[dict[str, Any]] = []
        for row in eligible_rows:
            lead_id = str(row["keeper_lead_id"])
            old_link_count = _count_keeper_links(session, lead_id)
            session.execute(
                text("""
                    UPDATE leads SET
                        portfolio_size = :size,
                        updated_at = now()
                    WHERE lead_id = :lid
                """),
                {"lid": lead_id, "size": int(row.get("portfolio_size") or 0)},
            )

            cluster = next(
                (candidate for candidate in clusters if candidate.get("canonical_name") == row.get("canonical_name")),
                None,
            )
            insert_result = guarded_insert_current_links(
                session,
                [
                    {"bbl": bbl, "lead_id": lead_id, "role": "agent"}
                    for bbl in (cluster or {}).get("bbls", [])
                ],
            )
            if insert_result["conflicts"]:
                raise RuntimeError(
                    f"entity_resolution detected current-link conflicts mid-execution for {row.get('canonical_name')!r}"
                )

            new_link_count = _count_keeper_links(session, lead_id)
            if len(rollback_samples) < sample_limit:
                rollback_samples.append({
                    "canonical_name": row.get("canonical_name"),
                    "keeper_lead_id": lead_id,
                    "old_current_link_count": old_link_count,
                    "new_current_link_count": new_link_count,
                    "portfolio_size": int(row.get("portfolio_size") or 0),
                })
            updated += 1
            if updated % 100 == 0:
                session.commit()

        execution_config = {
            **base_config,
            "mode": "execute",
            "write_permitted": True,
            "execution": {
                "executed_bucket": cohort_filter,
                "eligible_clusters": len(eligible_rows),
                "updated_clusters": updated,
                "rollback_samples": rollback_samples,
                "rollback_strategy": ENTITY_RESOLUTION_ROLLBACK_STRATEGY,
            },
        }
        _store_job_config(session, job_id, execution_config)
        session.commit()
        _finish_job(session, job_id, "completed", len(eligible_rows), updated, 0)
        session.commit()
        logger.info("Entity resolution executed %s safe clusters", updated)
        return {
            "run_id": run_id,
            "job_id": job_id,
            "clusters": len(prep_rows),
            "updated": updated,
            "mode": "execute",
            "cohort_filter": cohort_filter,
            "rollback_samples": rollback_samples,
            "rollback_strategy": ENTITY_RESOLUTION_ROLLBACK_STRATEGY,
        }

    except Exception as e:
        logger.error("Entity resolution failed: %s", e, exc_info=True)
        try:
            from src.tasks.ingest import _finish_job

            if job_id is not None:
                _finish_job(session, job_id, "failed", 0, 0, 1, str(e)[:500])
                session.commit()
        except Exception:
            session.rollback()
        session.rollback()
        raise
    finally:
        session.close()
        if engine is not None:
            engine.dispose()
