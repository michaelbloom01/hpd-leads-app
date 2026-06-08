"""Build a concise no-mutation approval packet for source-overlap evidence recording."""

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

from scripts.truth_manager_external_evidence_batch import (  # noqa: E402
    build_manager_external_evidence_batch,
)
from scripts.truth_operator_confirmed_evidence_batch import (  # noqa: E402
    build_operator_confirmed_evidence_batch,
)
from src.db.session import get_session_factory, shutdown_engine  # noqa: E402
from src.services.manual_evidence import preview_or_record_manual_evidence  # noqa: E402
from src.services.truth_adjudication import (  # noqa: E402
    load_claim_adjudication_preview,
    load_manager_external_source_acquisition_preview,
    load_operator_confirmed_management_preview,
)
from src.services.truth_health import build_schema_readiness_report, is_truth_schema_current, load_truth_schema_status  # noqa: E402


def _utc_run_id() -> str:
    return f"truth-source-overlap-approval-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"


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


def _review_summary(batch: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "bbl": group.get("bbl"),
            "address": group.get("address"),
            "template_count": group.get("template_count"),
            "source_names": group.get("source_names"),
            "source_families": group.get("source_families"),
            "manager_proof_source_families": group.get("manager_proof_source_families")
            or group.get("manager_proof_source_families_if_recorded"),
            "source_ready_if_recorded": group.get("source_ready_if_recorded"),
            "strict_manager_source_ready_if_recorded": group.get("strict_manager_source_ready_if_recorded"),
            "safe_to_mark_verified_if_recorded": group.get("safe_to_mark_verified_if_recorded")
            or group.get("verified_safe_if_recorded"),
            "verification_score_if_recorded": group.get("verification_score_if_recorded"),
            "readiness_blockers": group.get("readiness_blockers"),
        }
        for group in batch.get("claim_group_review_summary") or []
    ]


def _aggregate_review_values(batch: dict[str, Any], *keys: str) -> list[str]:
    values: set[str] = set()
    for group in batch.get("claim_group_review_summary") or []:
        for key in keys:
            for value in group.get(key) or []:
                if str(value).strip():
                    values.add(str(value))
    return sorted(values)


def _summarize_batch(batch: dict[str, Any]) -> dict[str, Any]:
    simulation = batch.get("post_recording_simulation") or {}
    source_families = batch.get("source_families") or _aggregate_review_values(batch, "source_families")
    manager_proof_source_families = batch.get("manager_proof_source_families") or _aggregate_review_values(
        batch,
        "manager_proof_source_families",
        "manager_proof_source_families_if_recorded",
    )
    return {
        "run_type": batch.get("run_type"),
        "batch_filter": batch.get("batch_filter"),
        "template_count": batch.get("template_count"),
        "claim_group_count": batch.get("claim_group_count"),
        "included_bbls": batch.get("included_bbls"),
        "included_addresses": batch.get("included_addresses"),
        "included_candidate_count": batch.get("included_candidate_count"),
        "approval_required": batch.get("approval_required"),
        "approval_required_before_recording": batch.get("approval_required_before_recording"),
        "allowed_execute": batch.get("allowed_execute"),
        "excluded_non_strict_candidate_count": batch.get("excluded_non_strict_candidate_count"),
        "excluded_non_strict_candidates": batch.get("excluded_non_strict_candidates"),
        "excluded_conflict_candidate_count": batch.get("excluded_conflict_candidate_count"),
        "excluded_conflict_candidates": batch.get("excluded_conflict_candidates"),
        "source_names": batch.get("source_names"),
        "source_families": source_families,
        "manager_proof_source_families": manager_proof_source_families,
        "planned_upsert_count_if_approved": batch.get("planned_upsert_count"),
        "rollback_preview_if_approved": batch.get("rollback_preview"),
        "sample_manual_evidence_previews": batch.get("sample_manual_evidence_previews") or [],
        "post_recording_simulation": {
            "multi_source_fact_group_count": simulation.get("multi_source_fact_group_count"),
            "source_ready_fact_group_count": simulation.get("source_ready_fact_group_count"),
            "strict_manager_source_ready_fact_group_count": simulation.get(
                "strict_manager_source_ready_fact_group_count"
            ),
            "safe_to_mark_verified_count": simulation.get("safe_to_mark_verified_count"),
            "blocker_counts": simulation.get("blocker_counts"),
        },
        "approval_decision_summary": batch.get("approval_decision_summary"),
        "claim_group_review_summary": _review_summary(batch),
        "recommended_execute_command": batch.get("recommended_execute_command"),
        "safe_action": batch.get("safe_action"),
    }


def _mark_existing_truth_ledger_packet(summary: dict[str, Any], *, packet_label: str) -> None:
    rollback_preview = summary.get("rollback_preview_if_approved") or {}
    template_count = int(summary.get("template_count") or 0)
    new_claim_count = int(rollback_preview.get("new_claim_count") or 0)
    new_evidence_count = int(rollback_preview.get("new_evidence_count") or 0)
    updated_claim_count = int(rollback_preview.get("updated_claim_count") or 0)
    updated_evidence_count = int(rollback_preview.get("updated_evidence_count") or 0)
    already_recorded = (
        template_count > 0
        and new_claim_count == 0
        and new_evidence_count == 0
        and updated_claim_count >= template_count
        and updated_evidence_count >= template_count
    )
    if not already_recorded:
        return

    summary["current_recording_status"] = "truth_ledger_evidence_already_recorded"
    summary["recording_effect_if_rerun"] = {
        "would_create_new_claim_count": new_claim_count,
        "would_create_new_evidence_count": new_evidence_count,
        "would_update_existing_claim_count": updated_claim_count,
        "would_update_existing_evidence_count": updated_evidence_count,
        "would_create_confidence_snapshot_count": int(rollback_preview.get("new_confidence_snapshot_count") or 0),
    }
    summary["safe_action"] = (
        f"The {packet_label} evidence is already represented in truth_claims/truth_evidence. "
        "Treat this packet as an idempotent repair/review packet only; rerunning it would touch existing "
        "claim/evidence rows and create fresh confidence snapshots/rollback manifest entries, but it still "
        "would not mark facts verified, refresh sources, materialize relationships, or allow business use."
    )


async def _preview_batch_rollup(
    session: Any,
    *,
    batch: dict[str, Any],
    recorded_by: str,
    run_id: str,
) -> dict[str, Any]:
    aggregate = _empty_rollback_aggregate()
    planned_upserts = 0
    samples: list[dict[str, Any]] = []
    for template in batch.get("manual_evidence_templates") or []:
        result = await preview_or_record_manual_evidence(
            session,
            payload=template,
            recorded_by=recorded_by,
            dry_run=True,
            confirm_execute=False,
            run_id=run_id,
        )
        planned_upserts += int(result.get("mutations_planned") or 0)
        _add_rollback_summary(aggregate, result)
        if len(samples) < 3:
            claim_spec = result.get("claim_spec") or {}
            samples.append({
                "claim_id": claim_spec.get("claim_id"),
                "evidence_id": claim_spec.get("evidence_id"),
                "predicate": claim_spec.get("predicate"),
                "object_id": claim_spec.get("object_id"),
                "source_name": claim_spec.get("source_name"),
                "source_record_id": claim_spec.get("source_record_id"),
                "actionability_level": claim_spec.get("actionability_level"),
                "mutations_planned": result.get("mutations_planned"),
                "allowed_execute": result.get("allowed_execute"),
                "mutation_scope": result.get("mutation_scope"),
            })
    return {
        "planned_upsert_count": planned_upserts,
        "rollback_preview": aggregate,
        "sample_manual_evidence_previews": samples,
    }


def _summarize_operator_strict_gaps(operator_preview: dict[str, Any]) -> dict[str, Any]:
    candidates = list(operator_preview.get("candidates") or [])
    gap_candidates = [
        candidate
        for candidate in candidates
        if candidate.get("strict_manager_gap_status") != "strict_manager_proof_ready_if_recorded"
    ]
    status_counts: dict[str, int] = {}
    for candidate in candidates:
        status = str(candidate.get("strict_manager_gap_status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1

    return {
        "candidate_count": len(candidates),
        "strict_ready_candidate_count": status_counts.get("strict_manager_proof_ready_if_recorded", 0),
        "broad_source_ready_not_strict_count": status_counts.get("broad_source_ready_not_strict", 0),
        "single_source_only_count": status_counts.get("single_source_only", 0),
        "status_counts": dict(sorted(status_counts.items())),
        "gap_candidates": [
            {
                "candidate_id": candidate.get("candidate_id"),
                "address": (candidate.get("matched_building") or {}).get("address") or candidate.get("user_address"),
                "manager_name": (candidate.get("matched_lead") or {}).get("company_name")
                or candidate.get("manager_name_supplied"),
                "strict_manager_gap_status": candidate.get("strict_manager_gap_status"),
                "missing_manager_proof_source_family_count": candidate.get(
                    "missing_manager_proof_source_family_count"
                ),
                "strict_manager_gap_reason": candidate.get("strict_manager_gap_reason"),
                "next_required_manager_proof": candidate.get("next_required_manager_proof"),
            }
            for candidate in gap_candidates[:5]
        ],
        "safe_action": (
            "Treat broad operator overlap as source-acquisition context only. Record strict operator evidence "
            "only for proposals with strict_manager_proof_ready_if_recorded after explicit approval."
        ),
    }


def _summarize_manager_strict_gaps(manager_preview: dict[str, Any]) -> dict[str, Any]:
    groups = list(manager_preview.get("claim_groups") or [])
    next_source_batches = manager_preview.get("next_source_batches") or {}
    proposals_by_bbl = {
        str(proposal.get("bbl") or ""): proposal
        for proposal in next_source_batches.get("proposals") or []
    }
    status_counts: dict[str, int] = {}
    gap_groups: list[dict[str, Any]] = []
    for group in groups:
        if group.get("strict_manager_source_ready_if_recorded"):
            status = "strict_manager_proof_ready_if_recorded"
        elif group.get("source_ready_if_recorded") or group.get("independent_source_ready_if_recorded"):
            status = "broad_source_ready_not_strict"
        else:
            status = "single_source_only"
        status_counts[status] = status_counts.get(status, 0) + 1
        if status == "strict_manager_proof_ready_if_recorded":
            continue
        bbl = str((group.get("fact_key") or {}).get("object_id") or "")
        proposal = proposals_by_bbl.get(bbl) or {}
        search_queries = proposal.get("search_queries") or []
        manager_proof_families = group.get("manager_proof_source_families_if_recorded") or []
        missing_family_count = max(0, 2 - len(manager_proof_families))
        gap_groups.append({
            "bbl": bbl,
            "address": group.get("address"),
            "strict_manager_gap_status": status,
            "existing_manager_proof_source_families": manager_proof_families,
            "supporting_source_families_if_recorded": group.get("supporting_source_families_if_recorded") or [],
            "missing_manager_proof_source_family_count": missing_family_count,
            "suggested_source_families": proposal.get("suggested_source_families") or [],
            "first_search_query": proposal.get("first_search_query") or (search_queries[0] if search_queries else None),
            "next_required_manager_proof": (
                "Acquire one exact non-HPD manager-proof source family before recording this group "
                "in the strict HPM packet."
            ),
        })

    return {
        "claim_group_count": len(groups),
        "strict_ready_claim_group_count": status_counts.get("strict_manager_proof_ready_if_recorded", 0),
        "broad_source_ready_not_strict_count": status_counts.get("broad_source_ready_not_strict", 0),
        "single_source_only_count": status_counts.get("single_source_only", 0),
        "status_counts": dict(sorted(status_counts.items())),
        "gap_candidates": gap_groups[:8],
        "safe_action": (
            "Treat non-strict HPM groups as source-acquisition targets only. Record only the strict "
            "HPM packet unless an exact non-HPD manager-proof source is added and reviewed."
        ),
    }


def _summarize_manager_new_relationship_candidates(manager_preview: dict[str, Any]) -> dict[str, Any]:
    candidates = list(manager_preview.get("new_relationship_candidates") or [])
    source_family_counts: dict[str, int] = {}
    for candidate in candidates:
        source_family = str(candidate.get("source_family") or "unknown")
        source_family_counts[source_family] = source_family_counts.get(source_family, 0) + 1

    candidate_count = int(manager_preview.get("new_relationship_candidate_count") or len(candidates))
    return {
        "candidate_count": candidate_count,
        "counts_as_current_ledger_overlap": False,
        "approval_required_for_relationship_creation": True,
        "source_family_counts": dict(sorted(source_family_counts.items())),
        "candidates": [
            {
                "candidate_id": candidate.get("candidate_id"),
                "source_name": candidate.get("source_name"),
                "source_family": candidate.get("source_family"),
                "external_address": candidate.get("external_address"),
                "local_address": candidate.get("local_address")
                or (candidate.get("local_building_match") or {}).get("address"),
                "bbl": (candidate.get("local_building_match") or {}).get("bbl"),
                "manager_name": candidate.get("manager_name"),
                "evidence_role": candidate.get("evidence_role"),
                "source_url": candidate.get("source_url"),
                **(
                    {"current_relationship_state": candidate.get("current_relationship_state")}
                    if candidate.get("current_relationship_state") is not None
                    else {}
                ),
                "safe_action": candidate.get("safe_action"),
            }
            for candidate in candidates[:5]
        ],
        "safe_action": (
            "Source-backed new relationship candidates are acquisition leads only. They do not count as "
            "current-ledger source overlap and require explicit review/approval before relationship creation."
        ),
    }


def _blocked_business_use_reason(
    *,
    ledger: dict[str, Any],
    manager_batch: dict[str, Any],
    operator_batch: dict[str, Any],
) -> str:
    multi_source_count = int(ledger.get("multi_source_fact_group_count") or 0)
    source_ready_count = int(ledger.get("source_ready_fact_group_count") or 0)
    manager_simulation = manager_batch.get("post_recording_simulation") or {}
    operator_simulation = operator_batch.get("post_recording_simulation") or {}
    verified_safe_if_recorded = int(manager_simulation.get("safe_to_mark_verified_count") or 0) + int(
        operator_simulation.get("safe_to_mark_verified_count") or 0
    )
    if multi_source_count <= 0 or source_ready_count <= 0:
        return (
            "Actual ledger source overlap is still zero. The packet proves source overlap only if an operator "
            "approves recording the cited evidence, and even the simulated post-recording result has zero "
            "verified-safe facts under the current confidence policy."
        )
    return (
        f"Actual ledger source overlap is now {multi_source_count} multi-source / "
        f"{source_ready_count} source-ready fact groups, but business use remains blocked until local truth "
        "health, activation packet, review, freshness, verified-claim, and production truth-surface gates pass. "
        f"Additional approval-packet simulations still produce {verified_safe_if_recorded} verified-safe facts "
        "under the current confidence policy and require explicit approval before recording."
    )


def _source_overlap_recording_gate(*, ledger: dict[str, Any]) -> dict[str, Any]:
    multi_source_count = int(ledger.get("multi_source_fact_group_count") or 0)
    source_ready_count = int(ledger.get("source_ready_fact_group_count") or 0)
    verification_candidate_count = int(ledger.get("verification_candidate_count") or 0)
    satisfied = multi_source_count > 0 and source_ready_count > 0
    return {
        "status": "satisfied" if satisfied else "approval_required",
        "current_multi_source_fact_group_count": multi_source_count,
        "current_source_ready_fact_group_count": source_ready_count,
        "current_verification_candidate_count": verification_candidate_count,
        "source_overlap_proof_satisfied": satisfied,
        "additional_evidence_recording_requires_approval": True,
        "safe_action": (
            "The current ledger already proves source overlap; do not rerun an evidence batch just to satisfy "
            "the source-overlap gate. Additional evidence recording remains approval-gated and still cannot "
            "mark facts verified by itself."
            if satisfied
            else "Source-overlap proof is not yet recorded in the current ledger. Review a clean packet, then "
            "execute only with explicit --execute --confirm-execute approval."
        ),
    }


def build_source_overlap_approval_packet(
    *,
    run_id: str,
    schema_status: dict[str, Any],
    adjudication_preview: dict[str, Any],
    manager_batch: dict[str, Any],
    operator_batch: dict[str, Any],
) -> dict[str, Any]:
    ledger = adjudication_preview.get("ledger_source_overlap") or {}
    manager_preview = adjudication_preview.get("manager_external_source_acquisition_preview") or {}
    operator_preview = adjudication_preview.get("operator_confirmed_management_preview") or {}
    current_ledger = {
        "total_fact_group_count": ledger.get("total_fact_group_count"),
        "single_source_fact_group_count": ledger.get("single_source_fact_group_count"),
        "multi_source_fact_group_count": ledger.get("multi_source_fact_group_count"),
        "source_ready_fact_group_count": ledger.get("source_ready_fact_group_count"),
        "verification_candidate_count": adjudication_preview.get("verification_candidate_count"),
    }
    recording_gate = _source_overlap_recording_gate(ledger=current_ledger)
    recommended_first_packet = _summarize_batch(manager_batch)
    _mark_existing_truth_ledger_packet(recommended_first_packet, packet_label="strict HPM")
    if recording_gate["source_overlap_proof_satisfied"]:
        recommended_first_packet.setdefault("current_recording_status", "source_overlap_gate_already_satisfied")
        if recommended_first_packet["current_recording_status"] == "source_overlap_gate_already_satisfied":
            recommended_first_packet["safe_action"] = (
                "The strict HPM source-overlap proof is already present in the current ledger. Treat this packet "
                "as an idempotent repair/review packet only; do not rerun it unless deliberately repairing the "
                "ledger after inspecting the rollback scope."
            )
    operator_strict_packet = _summarize_batch(operator_batch)
    _mark_existing_truth_ledger_packet(operator_strict_packet, packet_label="strict operator")
    return {
        "run_type": "truth_source_overlap_approval_packet",
        "run_id": run_id,
        "dry_run": True,
        "mutations_planned": 0,
        "schema_status": {
            "ready": schema_status.get("ready"),
            "current_revision": schema_status.get("current_revision"),
            "expected_revision": schema_status.get("expected_revision"),
            "migration_current": schema_status.get("migration_current"),
        },
        "current_ledger": current_ledger,
        "source_overlap_recording_gate": recording_gate,
        "previewed_overlap_if_approved": {
            "manager_source_ready_if_recorded_count": manager_preview.get("source_ready_if_recorded_count"),
            "manager_strict_source_ready_if_recorded_count": manager_preview.get(
                "strict_manager_source_ready_if_recorded_count"
            ),
            "operator_source_ready_if_recorded_count": operator_preview.get("source_ready_if_recorded_count"),
            "operator_strict_source_ready_if_recorded_count": operator_preview.get(
                "strict_manager_source_ready_if_recorded_count"
            ),
            "safe_to_mark_verified_after_recording": 0,
        },
        "recommended_first_packet": recommended_first_packet,
        "manager_strict_gap_summary": _summarize_manager_strict_gaps(manager_preview),
        "manager_new_relationship_candidate_summary": _summarize_manager_new_relationship_candidates(manager_preview),
        "operator_strict_packet": operator_strict_packet,
        "operator_strict_gap_summary": _summarize_operator_strict_gaps(operator_preview),
        "approval_required": True,
        "approval_policy": {
            "requires_explicit_operator_approval": True,
            "requires_execute_flag": True,
            "requires_confirm_execute_flag": True,
            "single_source_claims_stay_unverified": True,
            "post_record_adjudication_required": True,
        },
        "post_execution_required_checks": [
            "python scripts/truth_adjudication_preview.py --limit 20 --no-samples --indent 2",
            "python scripts/truth_source_overlap_post_recording_check.py --indent 2",
            "python scripts/truth_health_report.py --indent 2",
            "python scripts/truth_completion_audit.py --include-runtime --include-production --indent 2",
        ],
        "blocked_business_use_reason": _blocked_business_use_reason(
            ledger=ledger,
            manager_batch=manager_batch,
            operator_batch=operator_batch,
        ),
        "safe_action": "Review only. Do not execute any command from this packet without explicit approval.",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lead-id", default="0ff794d3ba2d")
    parser.add_argument("--recorded-by", default="operator")
    parser.add_argument("--run-id")
    parser.add_argument("--indent", type=int, default=2)
    return parser.parse_args()


async def run(args: argparse.Namespace) -> dict[str, Any]:
    run_id = args.run_id or _utc_run_id()
    factory = get_session_factory()
    async with factory() as session:
        return await build_source_overlap_approval_packet_for_session(
            session,
            lead_id=args.lead_id,
            recorded_by=args.recorded_by,
            run_id=run_id,
        )


async def build_source_overlap_approval_packet_for_session(
    session: Any,
    *,
    lead_id: str = "0ff794d3ba2d",
    recorded_by: str = "operator",
    run_id: str | None = None,
) -> dict[str, Any]:
    """Build the no-mutation approval packet against an existing DB session."""
    packet_run_id = run_id or _utc_run_id()
    schema_status = await load_truth_schema_status(session)
    if not is_truth_schema_current(schema_status):
        return build_schema_readiness_report(schema_status=schema_status)

    adjudication_preview = await load_claim_adjudication_preview(
        session,
        limit=20,
        include_samples=False,
    )
    manager_preview = await load_manager_external_source_acquisition_preview(
        session,
        lead_id=lead_id,
        limit=50,
    )
    operator_preview = await load_operator_confirmed_management_preview(session, limit=50)
    manager_batch = build_manager_external_evidence_batch(
        manager_preview,
        lead_id=lead_id,
        run_id=f"{packet_run_id}-manager-strict",
        recorded_by=recorded_by,
        strict_manager_proof_only=True,
    )
    operator_batch = build_operator_confirmed_evidence_batch(
        operator_preview,
        run_id=f"{packet_run_id}-operator-strict",
        recorded_by=recorded_by,
        strict_manager_proof_only=True,
    )
    manager_rollup = await _preview_batch_rollup(
        session,
        batch=manager_batch,
        recorded_by=recorded_by,
        run_id=f"{packet_run_id}-manager-strict",
    )
    operator_rollup = await _preview_batch_rollup(
        session,
        batch=operator_batch,
        recorded_by=recorded_by,
        run_id=f"{packet_run_id}-operator-strict",
    )
    manager_batch.update(manager_rollup)
    operator_batch.update(operator_rollup)
    packet_adjudication_preview = {
        **adjudication_preview,
        "manager_external_source_acquisition_preview": manager_preview,
        "operator_confirmed_management_preview": operator_preview,
    }
    packet = build_source_overlap_approval_packet(
        run_id=packet_run_id,
        schema_status=schema_status,
        adjudication_preview=packet_adjudication_preview,
        manager_batch=manager_batch,
        operator_batch=operator_batch,
    )
    await session.rollback()
    return packet


async def main() -> int:
    args = parse_args()
    try:
        packet = await run(args)
        print(json.dumps(packet, indent=args.indent, default=str))
        return 0
    finally:
        await shutdown_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
