"""Preview or record the Harlem manager external-evidence pilot batch."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db.session import get_session_factory, shutdown_engine  # noqa: E402
from src.services.manual_evidence import preview_or_record_manual_evidence  # noqa: E402
from src.services.truth_adjudication import (  # noqa: E402
    load_manager_external_source_acquisition_preview,
    simulate_manager_external_evidence_post_recording,
)
from src.services.truth_health import build_schema_readiness_report, is_truth_schema_current, load_truth_schema_status  # noqa: E402


EXCLUDED_CANDIDATE_STATUSES = {"address_range_review_required"}


def _utc_run_id() -> str:
    return f"truth-manager-external-evidence-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"


def _template_candidate_status(template: dict[str, Any]) -> str:
    raw_payload = template.get("raw_payload") or {}
    return str(raw_payload.get("candidate_status") or "").strip()


def _preview_readiness_blockers(group: dict[str, Any], simulation: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if not simulation.get("source_ready_fact_group_count"):
        blockers.append("needs_two_supporting_sources")
    if not simulation.get("independent_source_ready_fact_group_count"):
        blockers.append("needs_two_independent_source_families")
    if not simulation.get("strict_manager_source_ready_fact_group_count"):
        blockers.append("needs_two_manager_proof_source_families")
    if not simulation.get("safe_to_mark_verified_count"):
        blockers.append("not_safe_to_mark_verified_after_recording")
    for blocker in (simulation.get("blocker_counts") or {}):
        if blocker not in blockers:
            blockers.append(str(blocker))
    return blockers


def _verification_score_summary(simulation: dict[str, Any]) -> dict[str, Any]:
    sample = next(iter(simulation.get("samples") or []), {})
    score = sample.get("recomputed_confidence_score")
    score_gap = None
    if isinstance(score, int | float):
        score_gap = round(max(0.0, 0.9 - float(score)), 3)
    return {
        "recomputed_confidence_score": score,
        "proposed_belief_status": sample.get("proposed_belief_status"),
        "proposed_actionability_level": sample.get("proposed_actionability_level"),
        "freshest_observed_freshness_days": sample.get("freshest_observed_freshness_days"),
        "score_gap_to_verified": score_gap,
        "verified_blockers": sample.get("blockers") or [],
    }


def _empty_rollback_aggregate() -> dict[str, Any]:
    return {
        "new_claim_count": 0,
        "updated_claim_count": 0,
        "new_evidence_count": 0,
        "updated_evidence_count": 0,
        "new_confidence_snapshot_count": 0,
        "updated_confidence_snapshot_count": 0,
        "manifest_entry_count": 0,
        "manifest_by_type": {},
    }


def _add_rollback_summary(aggregate: dict[str, Any], result: dict[str, Any]) -> None:
    rollback_plan = result.get("rollback_plan") or {}
    for key in (
        "new_claim_count",
        "updated_claim_count",
        "new_evidence_count",
        "updated_evidence_count",
        "new_confidence_snapshot_count",
        "updated_confidence_snapshot_count",
    ):
        aggregate[key] = int(aggregate.get(key) or 0) + int(rollback_plan.get(key) or 0)

    rollback_manifest = result.get("rollback_manifest") or {}
    aggregate["manifest_entry_count"] = int(aggregate.get("manifest_entry_count") or 0) + int(
        rollback_manifest.get("entry_count") or 0
    )
    by_type = aggregate.setdefault("manifest_by_type", {})
    for item_type, counts in (rollback_manifest.get("by_type") or {}).items():
        target = by_type.setdefault(item_type, {})
        for status, count in (counts or {}).items():
            target[status] = int(target.get(status) or 0) + int(count or 0)


def _build_manual_evidence_preview(template: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    raw_payload = template.get("raw_payload") or {}
    rollback_plan = result.get("rollback_plan") or {}
    rollback_manifest = result.get("rollback_manifest") or {}
    claim_spec = result.get("claim_spec") or {}
    return {
        "claim_id": claim_spec.get("claim_id"),
        "evidence_id": claim_spec.get("evidence_id"),
        "predicate": template.get("predicate"),
        "object_id": claim_spec.get("object_id"),
        "address": raw_payload.get("local_address") or raw_payload.get("external_address"),
        "support_status": template.get("support_status"),
        "source_name": claim_spec.get("source_name"),
        "source_type": claim_spec.get("source_type"),
        "source_record_id": template.get("source_record_id"),
        "source_url": template.get("source_url"),
        "candidate_status": raw_payload.get("candidate_status"),
        "freshness_days": claim_spec.get("freshness_days"),
        "actionability_level": claim_spec.get("actionability_level"),
        "mutations_planned": result.get("mutations_planned"),
        "allowed_execute": result.get("allowed_execute"),
        "mutation_scope": result.get("mutation_scope"),
        "rollback_plan": {
            "new_claim_count": rollback_plan.get("new_claim_count"),
            "updated_claim_count": rollback_plan.get("updated_claim_count"),
            "new_evidence_count": rollback_plan.get("new_evidence_count"),
            "updated_evidence_count": rollback_plan.get("updated_evidence_count"),
            "new_confidence_snapshot_count": rollback_plan.get("new_confidence_snapshot_count"),
            "updated_confidence_snapshot_count": rollback_plan.get("updated_confidence_snapshot_count"),
        },
        "rollback_manifest": {
            "entry_count": rollback_manifest.get("entry_count"),
            "by_type": rollback_manifest.get("by_type"),
        },
    }


def _build_approval_decision_summary(
    *,
    batch_filter: str,
    template_count: int,
    claim_group_count: int,
    included_addresses: list[str],
    post_recording_simulation: dict[str, Any],
    recommended_execute_command: str,
) -> dict[str, Any]:
    return {
        "approval_required": True,
        "batch_filter": batch_filter,
        "recommended_execute_command": recommended_execute_command,
        "would_record_template_count": template_count,
        "would_record_claim_group_count": claim_group_count,
        "would_plan_upsert_count": template_count * 3,
        "included_addresses": included_addresses,
        "expected_multi_source_fact_group_count": post_recording_simulation.get("multi_source_fact_group_count"),
        "expected_source_ready_fact_group_count": post_recording_simulation.get("source_ready_fact_group_count"),
        "expected_strict_manager_source_ready_fact_group_count": post_recording_simulation.get(
            "strict_manager_source_ready_fact_group_count"
        ),
        "expected_safe_to_mark_verified_count": post_recording_simulation.get("safe_to_mark_verified_count"),
        "single_source_claims_stay_unverified": True,
        "will_mark_verified": False,
        "will_create_or_refresh_source_data": False,
        "will_materialize_new_relationships": False,
        "post_execution_required_checks": [
            "python scripts/truth_adjudication_preview.py --limit 20 --no-samples --indent 2",
            "python scripts/truth_health_report.py --indent 2",
            "python scripts/truth_completion_audit.py --include-runtime --include-production --indent 2",
        ],
        "safe_action": (
            "Approve only if the listed exact-property sources have been inspected. "
            "This records evidence rows; it does not mark facts verified, create new relationships, "
            "refresh source tables, or allow business use by itself."
        ),
    }


def build_manager_external_evidence_batch(
    acquisition_preview: dict[str, Any],
    *,
    lead_id: str,
    run_id: str,
    recorded_by: str,
    strict_manager_proof_only: bool = False,
) -> dict[str, Any]:
    """Build the exact manual-evidence batch from source-ready preview groups."""
    templates: list[dict[str, Any]] = []
    excluded_templates: list[dict[str, Any]] = []
    included_bbls: list[str] = []
    included_addresses: list[str] = []

    for group in acquisition_preview.get("claim_groups") or []:
        if not group.get("source_ready_if_recorded"):
            continue
        if not group.get("independent_source_ready_if_recorded"):
            continue
        if strict_manager_proof_only and not group.get("strict_manager_source_ready_if_recorded"):
            continue
        included_bbls.append(str((group.get("fact_key") or {}).get("object_id") or ""))
        included_addresses.append(str(group.get("address") or ""))
        for template in group.get("manual_evidence_templates") or []:
            candidate_status = _template_candidate_status(template)
            if candidate_status in EXCLUDED_CANDIDATE_STATUSES:
                excluded_templates.append(template)
                continue
            templates.append(template)

    source_names = sorted({str(template.get("source_name") or "") for template in templates})
    source_families = sorted({
        str((template.get("raw_payload") or {}).get("source_family") or "").strip()
        for template in templates
        if str((template.get("raw_payload") or {}).get("source_family") or "").strip()
    })
    manager_proof_source_families = sorted({
        family for family in source_families if family != "hpd_registration_derived"
    })
    acquisition_groups_by_bbl = {
        str((group.get("fact_key") or {}).get("object_id") or ""): group
        for group in acquisition_preview.get("claim_groups") or []
        if str((group.get("fact_key") or {}).get("object_id") or "").strip()
    }
    next_source_batches = acquisition_preview.get("next_source_batches") or {}
    next_source_search_pack = [
        {
            "bbl": proposal.get("bbl"),
            "address": proposal.get("address"),
            "existing_manager_proof_source_families": proposal.get("existing_manager_proof_source_families") or [],
            "missing_manager_proof_source_family_count": proposal.get("missing_manager_proof_source_family_count") or 0,
            "suggested_source_families": proposal.get("suggested_source_families") or [],
            "search_queries": proposal.get("search_queries") or [],
            "source_targets": proposal.get("source_targets") or [],
            "safe_action": proposal.get("safe_action"),
        }
        for proposal in next_source_batches.get("proposals") or []
    ]
    candidate_statuses: dict[str, int] = {}
    for template in templates:
        status = _template_candidate_status(template) or "unknown"
        candidate_statuses[status] = candidate_statuses.get(status, 0) + 1
    claim_group_review_summary: dict[str, dict[str, Any]] = {}
    for template in templates:
        raw_payload = template.get("raw_payload") or {}
        bbl = str(template.get("object_id") or "")
        if not bbl:
            continue
        group = claim_group_review_summary.setdefault(
            bbl,
            {
                "bbl": bbl,
                "address": raw_payload.get("local_address") or raw_payload.get("external_address"),
                "template_count": 0,
                "source_names": set(),
                "source_families": set(),
                "manager_proof_source_families": set(),
                "source_record_ids": set(),
                "source_urls": set(),
                "candidate_status_counts": {},
                "_templates": [],
            },
        )
        group["template_count"] += 1
        group["_templates"].append(template)
        if template.get("source_name"):
            group["source_names"].add(str(template.get("source_name")))
        source_family = raw_payload.get("source_family")
        if source_family:
            group["source_families"].add(str(source_family))
            if source_family not in {"hpd_registration_derived"}:
                group["manager_proof_source_families"].add(str(source_family))
        if template.get("source_record_id"):
            group["source_record_ids"].add(str(template.get("source_record_id")))
        if template.get("source_url"):
            group["source_urls"].add(str(template.get("source_url")))
        status = _template_candidate_status(template) or "unknown"
        group["candidate_status_counts"][status] = group["candidate_status_counts"].get(status, 0) + 1
    review_groups = []
    for group in sorted(claim_group_review_summary.values(), key=lambda item: str(item.get("address") or "")):
        source_group = acquisition_groups_by_bbl.get(str(group.get("bbl") or "")) or {}
        simulation = simulate_manager_external_evidence_post_recording(group["_templates"])
        supporting_sources = sorted(set(source_group.get("supporting_sources_if_recorded") or group["source_names"]))
        supporting_families = sorted(set(
            source_group.get("supporting_source_families_if_recorded") or group["source_families"]
        ))
        manager_proof_families = sorted(set(
            source_group.get("manager_proof_source_families_if_recorded") or group["manager_proof_source_families"]
        ))
        batch_source_ready = bool(simulation.get("source_ready_fact_group_count"))
        batch_independent_ready = bool(simulation.get("independent_source_ready_fact_group_count"))
        batch_strict_manager_ready = bool(simulation.get("strict_manager_source_ready_fact_group_count"))
        score_summary = _verification_score_summary(simulation)
        review_groups.append({
            **group,
            "address": source_group.get("address") or group.get("address"),
            "acquisition_source_ready_if_recorded": bool(source_group.get("source_ready_if_recorded")),
            "acquisition_independent_source_ready_if_recorded": bool(
                source_group.get("independent_source_ready_if_recorded")
            ),
            "acquisition_strict_manager_source_ready_if_recorded": bool(
                source_group.get("strict_manager_source_ready_if_recorded")
            ),
            "supporting_sources_if_recorded": supporting_sources,
            "supporting_source_families_if_recorded": supporting_families,
            "manager_proof_source_families_if_recorded": manager_proof_families,
            "supporting_source_count_if_recorded": (
                source_group.get("supporting_source_count_if_recorded") or len(supporting_sources)
            ),
            "independent_source_family_count_if_recorded": (
                source_group.get("independent_source_family_count_if_recorded") or len(supporting_families)
            ),
            "manager_proof_source_family_count_if_recorded": (
                source_group.get("manager_proof_source_family_count_if_recorded") or len(manager_proof_families)
            ),
            "source_ready_if_recorded": batch_source_ready,
            "independent_source_ready_if_recorded": batch_independent_ready,
            "strict_manager_source_ready_if_recorded": batch_strict_manager_ready,
            "safe_to_mark_verified_if_recorded": bool(simulation.get("safe_to_mark_verified_count")),
            "verification_score_if_recorded": score_summary,
            "readiness_blockers": _preview_readiness_blockers(source_group, simulation),
            "source_names": sorted(group["source_names"]),
            "source_families": sorted(group["source_families"]),
            "manager_proof_source_families": sorted(group["manager_proof_source_families"]),
            "source_record_ids": sorted(group["source_record_ids"]),
            "source_urls": sorted(group["source_urls"]),
            "candidate_status_counts": dict(sorted(group["candidate_status_counts"].items())),
            "post_recording_simulation": {
                "multi_source_fact_group_count": simulation.get("multi_source_fact_group_count"),
                "source_ready_fact_group_count": simulation.get("source_ready_fact_group_count"),
                "independent_source_ready_fact_group_count": simulation.get(
                    "independent_source_ready_fact_group_count"
                ),
                "strict_manager_source_ready_fact_group_count": simulation.get(
                    "strict_manager_source_ready_fact_group_count"
                ),
                "safe_to_mark_verified_count": simulation.get("safe_to_mark_verified_count"),
                "blocker_counts": simulation.get("blocker_counts"),
            },
        })
        review_groups[-1].pop("_templates", None)

    batch_filter = "strict_manager_proof" if strict_manager_proof_only else "all_source_ready"
    post_recording_simulation = simulate_manager_external_evidence_post_recording(templates)
    recommended_execute_command = (
        "python scripts/truth_manager_external_evidence_batch.py "
        f"{'--strict-manager-proof-only ' if strict_manager_proof_only else ''}"
        "--execute --confirm-execute --indent 2"
    )
    filtered_included_addresses = [address for address in included_addresses if address]

    return {
        "run_type": "manager_external_evidence_batch",
        "run_id": run_id,
        "lead_id": lead_id,
        "recorded_by": recorded_by,
        "dry_run": True,
        "mutations_planned": 0,
        "allowed_execute": False,
        "approval_required": True,
        "approval_required_before_recording": True,
        "batch_filter": batch_filter,
        "template_count": len(templates),
        "claim_group_count": len({bbl for bbl in included_bbls if bbl}),
        "included_bbls": [bbl for bbl in included_bbls if bbl],
        "included_addresses": filtered_included_addresses,
        "source_names": source_names,
        "source_families": source_families,
        "manager_proof_source_families": manager_proof_source_families,
        "candidate_status_counts": dict(sorted(candidate_statuses.items())),
        "claim_group_review_summary": review_groups,
        "excluded_template_count": len(excluded_templates),
        "excluded_address_review_candidate_count": (
            (acquisition_preview.get("manual_evidence_batch_preview") or {}).get(
                "excluded_address_review_candidate_count"
            )
        ),
        "excluded_candidate_statuses": sorted(EXCLUDED_CANDIDATE_STATUSES),
        "new_relationship_candidate_count": acquisition_preview.get("new_relationship_candidate_count") or 0,
        "new_relationship_candidates": acquisition_preview.get("new_relationship_candidates") or [],
        "new_relationship_policy": (
            "Source-backed buildings without a current pilot relationship are not included in this evidence batch. "
            "Review and materialize the relationship claim separately before using that source as overlap evidence."
        ),
        "next_source_search_pack_count": len(next_source_search_pack),
        "next_source_search_pack": next_source_search_pack,
        "next_source_search_policy": (
            "Exact-property search guidance only. Do not count a proposed source unless it names the exact building "
            "and a property-management or managing-agent role; broad company evidence and HPD-derived registration "
            "context remain review-only for strict manager proof."
        ),
        "acquisition_preview_summary": {
            "candidate_source_count": acquisition_preview.get("candidate_source_count"),
            "matched_evidence_candidate_count": acquisition_preview.get("matched_evidence_candidate_count"),
            "unmatched_candidate_count": acquisition_preview.get("unmatched_candidate_count"),
            "new_relationship_candidate_count": acquisition_preview.get("new_relationship_candidate_count"),
            "claim_group_count": acquisition_preview.get("claim_group_count"),
            "clean_exact_claim_count": acquisition_preview.get("clean_exact_claim_count"),
            "source_ready_if_recorded_count": acquisition_preview.get("source_ready_if_recorded_count"),
            "independent_source_ready_if_recorded_count": acquisition_preview.get(
                "independent_source_ready_if_recorded_count"
            ),
            "strict_manager_source_ready_if_recorded_count": acquisition_preview.get(
                "strict_manager_source_ready_if_recorded_count"
            ),
            "review_required_count": acquisition_preview.get("review_required_count"),
        },
        "post_recording_simulation": post_recording_simulation,
        "manual_evidence_templates": templates,
        "approval_decision_summary": _build_approval_decision_summary(
            batch_filter=batch_filter,
            template_count=len(templates),
            claim_group_count=len({bbl for bbl in included_bbls if bbl}),
            included_addresses=filtered_included_addresses,
            post_recording_simulation=post_recording_simulation,
            recommended_execute_command=recommended_execute_command,
        ),
        "recommended_execute_command": recommended_execute_command,
        "safe_action": (
            "Preview only unless both --execute and --confirm-execute are provided. "
            "After recording, rerun adjudication, truth health, and completion audit."
        ),
    }


def build_manager_source_acquisition_packet(
    acquisition_preview: dict[str, Any],
    *,
    lead_id: str,
    run_id: str,
) -> dict[str, Any]:
    """Build a compact read-only packet for the next source-acquisition pass."""
    next_source_batches = acquisition_preview.get("next_source_batches") or {}
    proposals = next_source_batches.get("proposals") or []
    new_relationship_candidates = acquisition_preview.get("new_relationship_candidates") or []
    source_family_counts = next_source_batches.get("suggested_source_family_counts") or {}
    top_priority_order = {
        "ny_dps_order_entry": 0,
        "company_website": 1,
        "outreach_confirmed": 2,
        "ny_dos": 3,
    }
    sorted_proposals = sorted(
        proposals,
        key=lambda proposal: (
            min(
                top_priority_order.get(str(family), 99)
                for family in (proposal.get("suggested_source_families") or ["zzz"])
            ),
            str(proposal.get("address") or ""),
        ),
    )

    return {
        "run_type": "manager_source_acquisition_packet",
        "run_id": run_id,
        "lead_id": lead_id,
        "dry_run": True,
        "mutations_planned": 0,
        "candidate_count": next_source_batches.get("candidate_count") or len(proposals),
        "source_ready_if_recorded_count": acquisition_preview.get("source_ready_if_recorded_count"),
        "independent_source_ready_if_recorded_count": acquisition_preview.get(
            "independent_source_ready_if_recorded_count"
        ),
        "strict_manager_source_ready_if_recorded_count": acquisition_preview.get(
            "strict_manager_source_ready_if_recorded_count"
        ),
        "verified_safe_if_recorded_count": (
            acquisition_preview.get("post_recording_simulation") or {}
        ).get("safe_to_mark_verified_count"),
        "next_source_seed_count": next_source_batches.get("candidate_count") or len(proposals),
        "new_relationship_candidate_count": acquisition_preview.get("new_relationship_candidate_count") or 0,
        "new_relationship_candidates": [
            {
                "candidate_id": candidate.get("candidate_id"),
                "source_name": candidate.get("source_name"),
                "source_family": candidate.get("source_family"),
                "external_address": candidate.get("external_address"),
                "local_address": candidate.get("local_address"),
                "manager_name": candidate.get("manager_name"),
                "evidence_role": candidate.get("evidence_role"),
                "evidence_summary": candidate.get("evidence_summary"),
                "source_url": candidate.get("source_url"),
                "local_building_match": candidate.get("local_building_match") or {},
                "current_relationship_state": candidate.get("current_relationship_state"),
                "safe_action": candidate.get("safe_action"),
            }
            for candidate in new_relationship_candidates[:5]
        ],
        "new_relationship_policy": (
            "Source-backed new relationships are source-acquisition leads only. They are not counted as "
            "current-ledger source overlap and must go through relationship review before any evidence is recorded."
        ),
        "suggested_source_family_counts": source_family_counts,
        "priority_source_families": [
            "ny_dps_order_entry",
            "company_website",
            "outreach_confirmed",
            "ny_dos",
        ],
        "proposals": [
            {
                "bbl": proposal.get("bbl"),
                "address": proposal.get("address"),
                "existing_manager_proof_source_families": (
                    proposal.get("existing_manager_proof_source_families") or []
                ),
                "missing_manager_proof_source_family_count": (
                    proposal.get("missing_manager_proof_source_family_count") or 0
                ),
                "suggested_source_families": proposal.get("suggested_source_families") or [],
                "first_search_query": (proposal.get("search_queries") or [None])[0],
                "search_queries": proposal.get("search_queries") or [],
                "source_targets": proposal.get("source_targets") or [],
                "safe_action": proposal.get("safe_action"),
            }
            for proposal in sorted_proposals
        ],
        "reviewed_source_findings": next_source_batches.get("reviewed_source_findings") or [],
        "source_boundary_notes": next_source_batches.get("source_boundary_notes") or [],
        "current_preview_summary": {
            "claim_group_count": acquisition_preview.get("claim_group_count"),
            "clean_exact_claim_count": acquisition_preview.get("clean_exact_claim_count"),
            "source_ready_if_recorded_count": acquisition_preview.get("source_ready_if_recorded_count"),
            "strict_manager_source_ready_if_recorded_count": acquisition_preview.get(
                "strict_manager_source_ready_if_recorded_count"
            ),
            "new_relationship_candidate_count": acquisition_preview.get("new_relationship_candidate_count") or 0,
            "verified_safe_if_recorded_count": (
                acquisition_preview.get("post_recording_simulation") or {}
            ).get("safe_to_mark_verified_count"),
        },
        "safe_action": (
            "Read-only source-acquisition packet. Record nothing from this packet until a source is inspected, "
            "converted into a manual-evidence preview, and explicitly approved with confirm_execute=true."
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lead-id", default="0ff794d3ba2d")
    parser.add_argument("--recorded-by", default="operator")
    parser.add_argument("--run-id")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-execute", action="store_true")
    parser.add_argument("--strict-manager-proof-only", action="store_true")
    parser.add_argument("--source-acquisition-only", action="store_true")
    parser.add_argument("--include-templates", action="store_true")
    parser.add_argument("--include-all-previews", action="store_true")
    parser.add_argument("--indent", type=int, default=2)
    return parser.parse_args()


async def run(args: argparse.Namespace) -> dict[str, Any]:
    run_id = args.run_id or _utc_run_id()
    dry_run = not (args.execute and args.confirm_execute)
    factory = get_session_factory()
    async with factory() as session:
        schema_status = await load_truth_schema_status(session)
        if not is_truth_schema_current(schema_status):
            return build_schema_readiness_report(schema_status=schema_status)

        acquisition_preview = await load_manager_external_source_acquisition_preview(
            session,
            lead_id=args.lead_id,
            limit=50,
        )
        if args.source_acquisition_only:
            return build_manager_source_acquisition_packet(
                acquisition_preview,
                lead_id=args.lead_id,
                run_id=run_id,
            )

        batch = build_manager_external_evidence_batch(
            acquisition_preview,
            lead_id=args.lead_id,
            run_id=run_id,
            recorded_by=args.recorded_by,
            strict_manager_proof_only=args.strict_manager_proof_only,
        )
        batch["dry_run"] = dry_run
        batch["allowed_execute"] = not dry_run
        batch["approval_required"] = dry_run
        batch["approval_required_before_recording"] = True
        batch["required_execute_params"] = {"execute": True, "confirm_execute": True}

        previews: list[dict[str, Any]] = []
        mutations_planned = 0
        rollback_aggregate = _empty_rollback_aggregate()
        for template in batch["manual_evidence_templates"]:
            result = await preview_or_record_manual_evidence(
                session,
                payload=template,
                recorded_by=args.recorded_by,
                dry_run=dry_run,
                confirm_execute=args.confirm_execute,
                run_id=run_id,
            )
            mutations_planned += int(result.get("mutations_planned") or 0)
            _add_rollback_summary(rollback_aggregate, result)
            previews.append(_build_manual_evidence_preview(template, result))

        if dry_run:
            await session.rollback()
        batch["mutations_planned"] = mutations_planned if dry_run else 0
        batch["planned_upsert_count"] = mutations_planned if dry_run else 0
        batch["rollback_preview"] = rollback_aggregate
        batch["rollback_strategy"] = (
            "Execute-mode manifest entries are written under the batch run_id. "
            "Dry-run preview shows the claim/evidence/snapshot rows that a rollback would need to remove or restore."
        )
        templates = batch["manual_evidence_templates"]
        if args.include_templates:
            batch["manual_evidence_templates"] = templates
        else:
            batch.pop("manual_evidence_templates", None)
            batch["manual_evidence_template_sample"] = templates[:3]
            batch["template_output_note"] = "Pass --include-templates to print all manual evidence payloads."
        batch["manual_evidence_previews"] = previews if args.include_all_previews else previews[:5]
        if not args.include_all_previews:
            batch["preview_output_note"] = "Pass --include-all-previews to print all per-template preview IDs."
        if dry_run:
            batch["blocked_reason"] = (
                "Manager external evidence batch defaults to preview. Recording requires --execute --confirm-execute."
            )
        else:
            batch["claims_or_evidence_recorded"] = len(previews)
    return batch


async def async_main() -> int:
    args = parse_args()
    result = await run(args)
    print(json.dumps(result, indent=args.indent, default=str))
    await shutdown_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
