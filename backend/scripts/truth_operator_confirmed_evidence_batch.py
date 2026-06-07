"""Preview or record operator-confirmed management evidence templates."""

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
    load_operator_confirmed_management_preview,
    simulate_manager_external_evidence_post_recording,
)
from src.services.truth_health import build_schema_readiness_report, is_truth_schema_current, load_truth_schema_status  # noqa: E402


def _utc_run_id() -> str:
    return f"truth-operator-confirmed-evidence-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"


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
    claim_spec = result.get("claim_spec") or {}
    raw_payload = template.get("raw_payload") or {}
    return {
        "claim_id": claim_spec.get("claim_id"),
        "evidence_id": claim_spec.get("evidence_id"),
        "operator_candidate_id": _template_candidate_id(template),
        "predicate": template.get("predicate"),
        "object_id": claim_spec.get("object_id"),
        "address": raw_payload.get("canonical_address") or raw_payload.get("external_address"),
        "support_status": template.get("support_status"),
        "source_name": claim_spec.get("source_name"),
        "source_type": claim_spec.get("source_type"),
        "source_family": raw_payload.get("source_family"),
        "source_record_id": template.get("source_record_id"),
        "source_url": template.get("source_url"),
        "candidate_status": raw_payload.get("candidate_status"),
        "freshness_days": claim_spec.get("freshness_days"),
        "actionability_level": claim_spec.get("actionability_level"),
        "mutations_planned": result.get("mutations_planned"),
        "allowed_execute": result.get("allowed_execute"),
        "mutation_scope": result.get("mutation_scope"),
    }


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
            "Approve only if the first-hand operator confirmation and listed exact-property second sources "
            "have been inspected. This records truth claim/evidence rows; it does not mark facts verified, "
            "refresh source tables, create current relationship rows, or allow business use by itself."
        ),
    }


def _template_source_family(template: dict[str, Any]) -> str:
    return str((template.get("raw_payload") or {}).get("source_family") or "").strip()


def _template_candidate_id(template: dict[str, Any]) -> str:
    raw_payload = template.get("raw_payload") or {}
    return str(raw_payload.get("operator_candidate_id") or raw_payload.get("candidate_id") or "").strip()


def _candidate_exclusion_summary(
    candidate: dict[str, Any],
    reason: str,
    proposal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    proposal = proposal or {}
    building = candidate.get("matched_building") or {}
    lead = candidate.get("matched_lead") or {}
    return {
        "candidate_id": candidate.get("candidate_id"),
        "bbl": building.get("bbl"),
        "address": building.get("address") or candidate.get("user_address"),
        "manager_lead_id": lead.get("lead_id"),
        "manager_name": lead.get("company_name") or candidate.get("manager_name_supplied"),
        "reason": reason,
        "review_queue": candidate.get("review_queue"),
        "source_ready_if_recorded": bool(candidate.get("source_ready_if_recorded")),
        "strict_manager_source_ready_if_recorded": bool(candidate.get("strict_manager_source_ready_if_recorded")),
        "strict_manager_gap_status": candidate.get("strict_manager_gap_status")
        or proposal.get("strict_manager_gap_status"),
        "missing_manager_proof_source_family_count": candidate.get("missing_manager_proof_source_family_count")
        if candidate.get("missing_manager_proof_source_family_count") is not None
        else proposal.get("missing_manager_proof_source_family_count"),
        "strict_manager_gap_reason": candidate.get("strict_manager_gap_reason")
        or proposal.get("strict_manager_gap_reason"),
        "next_required_manager_proof": candidate.get("next_required_manager_proof")
        or proposal.get("next_required_manager_proof"),
        "current_relationship_state": candidate.get("current_relationship_state")
        or proposal.get("current_relationship_state")
        or {},
    }


def build_operator_confirmed_evidence_batch(
    operator_preview: dict[str, Any],
    *,
    run_id: str,
    recorded_by: str,
    strict_manager_proof_only: bool = False,
    operator_only: bool = False,
) -> dict[str, Any]:
    """Build an approval packet from the operator-confirmed preview."""
    templates: list[dict[str, Any]] = []
    excluded_conflict_candidates: list[dict[str, Any]] = []
    excluded_non_strict_candidates: list[dict[str, Any]] = []
    included_candidates: list[dict[str, Any]] = []
    source_acquisition_proposals = {
        str(proposal.get("candidate_id") or ""): proposal
        for proposal in (operator_preview.get("second_source_seed_batches") or {}).get("proposals") or []
        if str(proposal.get("candidate_id") or "").strip()
    }

    for candidate in operator_preview.get("candidates") or []:
        if candidate.get("review_queue") == "conflicting_evidence":
            excluded_conflict_candidates.append(candidate)
            continue
        if strict_manager_proof_only and not candidate.get("strict_manager_source_ready_if_recorded"):
            excluded_non_strict_candidates.append(candidate)
            continue
        if not operator_only and not candidate.get("source_ready_if_recorded"):
            continue

        candidate_templates = [candidate.get("manual_evidence_template")]
        if not operator_only:
            candidate_templates.extend(candidate.get("second_source_templates") or [])
        selected_templates = [template for template in candidate_templates if isinstance(template, dict)]
        if not selected_templates:
            continue
        templates.extend(selected_templates)
        included_candidates.append(candidate)

    source_names = sorted({str(template.get("source_name") or "") for template in templates})
    source_families = sorted({_template_source_family(template) for template in templates if _template_source_family(template)})
    manager_proof_source_families = sorted({
        family for family in source_families if family != "hpd_registration_derived"
    })
    candidate_status_counts: dict[str, int] = {}
    for template in templates:
        status = str((template.get("raw_payload") or {}).get("candidate_status") or "operator_confirmed").strip()
        candidate_status_counts[status] = candidate_status_counts.get(status, 0) + 1

    review_summary = []
    for candidate in included_candidates:
        candidate_templates = [candidate.get("manual_evidence_template")]
        if not operator_only:
            candidate_templates.extend(candidate.get("second_source_templates") or [])
        candidate_templates = [template for template in candidate_templates if isinstance(template, dict)]
        candidate_simulation = simulate_manager_external_evidence_post_recording(candidate_templates)
        building = candidate.get("matched_building") or {}
        lead = candidate.get("matched_lead") or {}
        review_summary.append({
            "candidate_id": candidate.get("candidate_id"),
            "bbl": building.get("bbl"),
            "address": building.get("address") or candidate.get("user_address"),
            "manager_lead_id": lead.get("lead_id"),
            "manager_name": lead.get("company_name") or candidate.get("manager_name_supplied"),
            "review_queue": candidate.get("review_queue"),
            "current_relationship_state": candidate.get("current_relationship_state") or {},
            "supporting_sources_if_recorded": candidate.get("supporting_sources_if_recorded") or [],
            "supporting_source_families_if_recorded": candidate.get("supporting_source_families_if_recorded") or [],
            "manager_proof_source_families_if_recorded": (
                candidate.get("manager_proof_source_families_if_recorded") or []
            ),
            "source_ready_if_recorded": bool(candidate.get("source_ready_if_recorded")),
            "strict_manager_source_ready_if_recorded": bool(
                candidate.get("strict_manager_source_ready_if_recorded")
            ),
            "verified_safe_if_recorded": bool(candidate.get("verified_safe_if_recorded")),
            "verification_score_if_recorded": _verification_score_summary(candidate_simulation),
        })

    batch_filter = (
        "operator_only"
        if operator_only
        else "strict_manager_proof"
        if strict_manager_proof_only
        else "all_source_ready"
    )
    claim_group_count = len({
        str(template.get("object_id") or "")
        for template in templates
        if str(template.get("object_id") or "").strip()
    })
    post_recording_simulation = simulate_manager_external_evidence_post_recording(templates)
    recommended_execute_command = (
        "python scripts/truth_operator_confirmed_evidence_batch.py "
        f"{'--strict-manager-proof-only ' if strict_manager_proof_only else ''}"
        f"{'--operator-only ' if operator_only else ''}"
        "--execute --confirm-execute --indent 2"
    )
    included_addresses = [
        str(group.get("address"))
        for group in review_summary
        if str(group.get("address") or "").strip()
    ]

    return {
        "run_type": "operator_confirmed_management_evidence_batch",
        "run_id": run_id,
        "recorded_by": recorded_by,
        "dry_run": True,
        "mutations_planned": 0,
        "allowed_execute": False,
        "approval_required": True,
        "approval_required_before_recording": True,
        "batch_filter": batch_filter,
        "template_count": len(templates),
        "claim_group_count": claim_group_count,
        "included_candidate_count": len(included_candidates),
        "excluded_conflict_candidate_count": len(excluded_conflict_candidates),
        "excluded_non_strict_candidate_count": len(excluded_non_strict_candidates),
        "excluded_non_strict_candidates": [
            _candidate_exclusion_summary(
                candidate,
                "strict_manager_proof_filter",
                source_acquisition_proposals.get(str(candidate.get("candidate_id") or "")),
            )
            for candidate in excluded_non_strict_candidates
        ],
        "excluded_conflict_candidates": [
            _candidate_exclusion_summary(
                candidate,
                "conflicting_evidence",
                source_acquisition_proposals.get(str(candidate.get("candidate_id") or "")),
            )
            for candidate in excluded_conflict_candidates
        ],
        "source_names": source_names,
        "source_families": source_families,
        "manager_proof_source_families": manager_proof_source_families,
        "candidate_status_counts": dict(sorted(candidate_status_counts.items())),
        "claim_group_review_summary": review_summary,
        "operator_preview_summary": {
            "candidate_count": operator_preview.get("candidate_count"),
            "matched_candidate_count": operator_preview.get("matched_candidate_count"),
            "new_relationship_candidate_count": operator_preview.get("new_relationship_candidate_count"),
            "conflict_candidate_count": operator_preview.get("conflict_candidate_count"),
            "manual_evidence_template_count": operator_preview.get("manual_evidence_template_count"),
            "source_ready_if_recorded_count": operator_preview.get("source_ready_if_recorded_count"),
            "strict_manager_source_ready_if_recorded_count": operator_preview.get(
                "strict_manager_source_ready_if_recorded_count"
            ),
            "verified_safe_if_recorded_count": operator_preview.get("verified_safe_if_recorded_count"),
        },
        "reviewed_source_findings": (
            (operator_preview.get("second_source_seed_batches") or {}).get("reviewed_source_findings") or []
        ),
        "source_boundary_notes": (
            (operator_preview.get("second_source_seed_batches") or {}).get("source_boundary_notes") or []
        ),
        "post_recording_simulation": post_recording_simulation,
        "approval_decision_summary": _build_approval_decision_summary(
            batch_filter=batch_filter,
            template_count=len(templates),
            claim_group_count=claim_group_count,
            included_addresses=included_addresses,
            post_recording_simulation=post_recording_simulation,
            recommended_execute_command=recommended_execute_command,
        ),
        "manual_evidence_templates": templates,
        "recommended_execute_command": recommended_execute_command,
        "safe_action": (
            "Preview only unless both --execute and --confirm-execute are provided. "
            "After recording, rerun adjudication, truth health, and completion audit."
        ),
        "source_boundary_note": (
            "RentHistory/HPD-registration-derived templates can create broad source overlap, but strict "
            "manager-proof counts require non-HPD source families such as operator_confirmed plus real_estate_listing."
        ),
    }


def build_operator_source_acquisition_packet(
    operator_preview: dict[str, Any],
    *,
    run_id: str,
) -> dict[str, Any]:
    """Build a compact no-write packet for second-source acquisition."""
    seed_batches = operator_preview.get("second_source_seed_batches") or {}
    proposals = list(seed_batches.get("proposals") or [])
    candidates = list(operator_preview.get("candidates") or [])
    candidate_by_bbl = {
        str((candidate.get("matched_building") or {}).get("bbl") or ""): candidate
        for candidate in candidates
        if str((candidate.get("matched_building") or {}).get("bbl") or "").strip()
    }

    source_family_counts: dict[str, int] = {}
    packet_proposals: list[dict[str, Any]] = []
    for proposal in proposals:
        suggested_families = list(proposal.get("suggested_source_families") or [])
        for family in suggested_families:
            source_family_counts[str(family)] = source_family_counts.get(str(family), 0) + 1
        candidate = candidate_by_bbl.get(str(proposal.get("bbl") or ""))
        packet_proposals.append({
            "candidate_id": proposal.get("candidate_id") or (candidate or {}).get("candidate_id"),
            "bbl": proposal.get("bbl"),
            "address": proposal.get("address"),
            "manager_lead_id": proposal.get("manager_lead_id") or (
                (candidate or {}).get("matched_lead") or {}
            ).get("lead_id"),
            "manager_name": proposal.get("manager_name") or (
                (candidate or {}).get("matched_lead") or {}
            ).get("company_name"),
            "existing_manager_proof_source_families": (
                proposal.get("existing_manager_proof_source_families") or []
            ),
            "supporting_source_families_if_recorded": (
                proposal.get("supporting_source_families_if_recorded")
                or proposal.get("existing_source_families_if_recorded")
                or []
            ),
            "strict_manager_source_ready_if_recorded": bool(
                proposal.get("strict_manager_source_ready_if_recorded")
            ),
            "strict_manager_gap_status": proposal.get("strict_manager_gap_status")
            or (candidate or {}).get("strict_manager_gap_status"),
            "strict_manager_gap_reason": proposal.get("strict_manager_gap_reason")
            or (candidate or {}).get("strict_manager_gap_reason"),
            "missing_manager_proof_source_family_count": proposal.get(
                "missing_manager_proof_source_family_count"
            )
            or (candidate or {}).get("missing_manager_proof_source_family_count"),
            "next_required_manager_proof": proposal.get("next_required_manager_proof")
            or (candidate or {}).get("next_required_manager_proof"),
            "current_relationship_state": proposal.get("current_relationship_state")
            or (candidate or {}).get("current_relationship_state")
            or {},
            "suggested_source_families": suggested_families,
            "first_search_query": (proposal.get("search_queries") or [None])[0],
            "search_queries": proposal.get("search_queries") or [],
            "source_targets": proposal.get("source_targets") or [],
            "safe_action": proposal.get("safe_action") or (
                "Find one exact-property, non-HPD-derived manager source before recording strict support."
            ),
        })

    return {
        "run_type": "operator_source_acquisition_packet",
        "run_id": run_id,
        "dry_run": True,
        "mutations_planned": 0,
        "candidate_count": operator_preview.get("candidate_count") or len(candidates),
        "matched_candidate_count": operator_preview.get("matched_candidate_count"),
        "source_ready_if_recorded_count": operator_preview.get("source_ready_if_recorded_count"),
        "strict_manager_source_ready_if_recorded_count": operator_preview.get(
            "strict_manager_source_ready_if_recorded_count"
        ),
        "verified_safe_if_recorded_count": operator_preview.get("verified_safe_if_recorded_count"),
        "second_source_seed_count": seed_batches.get("candidate_count") or len(proposals),
        "suggested_source_family_counts": dict(sorted(source_family_counts.items())),
        "priority_source_families": [
            "company_website",
            "external_web_profile",
            "real_estate_listing",
            "ny_dps_order_entry",
            "outreach_confirmed",
            "ny_dos",
        ],
        "proposals": packet_proposals,
        "reviewed_source_findings": seed_batches.get("reviewed_source_findings") or [],
        "source_boundary_notes": seed_batches.get("source_boundary_notes") or [],
        "safe_action": (
            "Read-only operator source-acquisition packet. Treat first-hand confirmations as evidence seeds, "
            "not verified facts; record nothing until exact-property evidence is inspected and explicitly approved."
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recorded-by", default="operator")
    parser.add_argument("--run-id")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-execute", action="store_true")
    parser.add_argument("--strict-manager-proof-only", action="store_true")
    parser.add_argument("--operator-only", action="store_true")
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

        operator_preview = await load_operator_confirmed_management_preview(session, limit=50)
        if args.source_acquisition_only:
            return build_operator_source_acquisition_packet(operator_preview, run_id=run_id)

        batch = build_operator_confirmed_evidence_batch(
            operator_preview,
            run_id=run_id,
            recorded_by=args.recorded_by,
            strict_manager_proof_only=args.strict_manager_proof_only,
            operator_only=args.operator_only,
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
                "Operator-confirmed evidence batch defaults to preview. Recording requires --execute --confirm-execute."
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
