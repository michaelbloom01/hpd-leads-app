"""Build a concise read-only approval packet for the role-overlap pilot."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db.session import get_session_factory, shutdown_engine  # noqa: E402
from src.services.truth_adjudication import load_claim_adjudication_preview  # noqa: E402
from src.services.truth_health import is_truth_schema_current, load_truth_schema_status  # noqa: E402
from src.services.truth_materialization import materialize_truth_claims  # noqa: E402


ROLE_OVERLAP_SOURCES = ["building_management", "hpd_contact_role_links"]


def build_role_overlap_activation_packet(
    *,
    schema_status: dict[str, Any],
    adjudication_preview: dict[str, Any],
    materialization_preview: dict[str, Any],
    limit: int,
) -> dict[str, Any]:
    ledger_overlap = adjudication_preview.get("ledger_source_overlap") or {}
    simulation = adjudication_preview.get("role_overlap_post_materialization_simulation") or {}
    activation_plan = adjudication_preview.get("role_overlap_activation_plan") or {}
    manager_external = adjudication_preview.get("manager_external_source_acquisition_preview") or {}
    predicted = activation_plan.get("predicted_if_approved") or {}
    correction_step = next(
        (step for step in activation_plan.get("ordered_steps", []) if step.get("step") == "execute_role_claim_corrections"),
        {},
    )
    materialization_step = next(
        (step for step in activation_plan.get("ordered_steps", []) if step.get("step") == "execute_bounded_role_overlap_materialization"),
        {},
    )
    return {
        "dry_run": True,
        "mutations_planned": 0,
        "schema_status": schema_status,
        "readiness": {
            "current_ledger_fact_groups": ledger_overlap.get("total_fact_group_count"),
            "current_multi_source_fact_groups": ledger_overlap.get("multi_source_fact_group_count"),
            "current_source_ready_fact_groups": ledger_overlap.get("source_ready_fact_group_count"),
            "current_verification_candidates": adjudication_preview.get("verification_candidate_count"),
            "current_business_use_allowed": False,
        },
        "role_overlap_sources": ROLE_OVERLAP_SOURCES,
        "materialization_preview": {
            "planned_claims_total": materialization_preview.get("planned_claims_total"),
            "planned_claims_by_source": materialization_preview.get("planned_claims_by_source"),
            "strict_materializable_claims_by_source": materialization_preview.get("strict_materializable_claims_by_source"),
            "strict_materializable_claims_by_predicate": materialization_preview.get("strict_materializable_claims_by_predicate"),
            "candidate_claims_by_source": materialization_preview.get("candidate_claims_by_source"),
        },
        "post_materialization_simulation": {
            "planned_claim_spec_count": simulation.get("planned_claim_spec_count"),
            "simulated_fact_group_count": simulation.get("simulated_fact_group_count"),
            "multi_source_fact_group_count": simulation.get("multi_source_fact_group_count"),
            "source_ready_fact_group_count": simulation.get("source_ready_fact_group_count"),
            "safe_to_mark_verified_count": simulation.get("safe_to_mark_verified_count"),
            "source_ready_count_by_predicate": simulation.get("source_ready_count_by_predicate"),
            "safe_to_mark_verified_count_by_predicate": simulation.get("safe_to_mark_verified_count_by_predicate"),
        },
        "predicted_if_approved": predicted,
        "manager_external_source_acquisition_preview": {
            "candidate_source_count": manager_external.get("candidate_source_count"),
            "matched_evidence_candidate_count": manager_external.get("matched_evidence_candidate_count"),
            "clean_exact_claim_count": manager_external.get("clean_exact_claim_count"),
            "claim_group_count": manager_external.get("claim_group_count"),
            "source_ready_if_recorded_count": manager_external.get("source_ready_if_recorded_count"),
            "independent_source_ready_if_recorded_count": manager_external.get(
                "independent_source_ready_if_recorded_count"
            ),
            "review_required_count": manager_external.get("review_required_count"),
            "unmatched_candidate_count": manager_external.get("unmatched_candidate_count"),
            "claim_groups": [
                {
                    "fact_key": group.get("fact_key"),
                    "address": group.get("address"),
                    "building_management_role": group.get("building_management_role"),
                    "supporting_sources_if_recorded": group.get("supporting_sources_if_recorded"),
                    "supporting_source_families_if_recorded": group.get("supporting_source_families_if_recorded"),
                    "source_ready_if_recorded": group.get("source_ready_if_recorded"),
                    "independent_source_ready_if_recorded": group.get("independent_source_ready_if_recorded"),
                }
                for group in manager_external.get("claim_groups", [])[:5]
            ],
            "safe_action": (
                "Operator may preview manual evidence payloads for clean external manager sources, but recording them "
                "still requires explicit approval and post-record adjudication."
            ),
        },
        "approval_steps": [
            {
                "name": "supersede_stale_agent_as_manager_claims",
                "approval_required": correction_step.get("approval_required", True),
                "mutations_planned": correction_step.get("mutations_planned"),
                "command": (
                    "python scripts/truth_role_claim_correction.py "
                    "--lead-id 0ff794d3ba2d --limit 100 --execute --confirm-execute --indent 2"
                ),
            },
            {
                "name": "materialize_strict_role_overlap",
                "approval_required": materialization_step.get("approval_required", True),
                "mutations_planned": materialization_step.get("mutations_planned"),
                "command": (
                    "python scripts/truth_materialization_run.py "
                    f"--limit {limit} --source building_management --source hpd_contact_role_links "
                    "--execute --confirm-execute --indent 2"
                ),
            },
            {
                "name": "rerun_readiness_checks",
                "approval_required": False,
                "mutations_planned": 0,
                "commands": [
                    "python scripts/truth_adjudication_preview.py --limit 20 --no-samples --indent 2",
                    "python scripts/truth_health_report.py --materialization-limit 50 --validation-sample-limit 10 --indent 2",
                    "python scripts/truth_completion_audit.py --include-runtime --indent 2",
                ],
            },
        ],
        "blocked_business_use_reason": (
            "The preview proves registered-agent source overlap and identifies manager-specific external evidence, "
            "but the manager evidence is not recorded or adjudicated yet. No fact is safe to mark verified until "
            "post-execution adjudication passes the confidence/freshness policy."
        ),
        "safe_action": "Review packet only. Do not run approval commands without explicit operator approval.",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--indent", type=int, default=2)
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    factory = get_session_factory()
    async with factory() as session:
        schema_status = await load_truth_schema_status(session)
        if not is_truth_schema_current(schema_status):
            packet = {
                "dry_run": True,
                "mutations_planned": 0,
                "schema_status": schema_status,
                "blocked_reason": "Role-overlap activation requires the additive truth-confidence schema.",
            }
        else:
            adjudication_preview = await load_claim_adjudication_preview(
                session,
                limit=20,
                include_samples=False,
            )
            materialization_preview = await materialize_truth_claims(
                session,
                limit=args.limit,
                dry_run=True,
                confirm_execute=False,
                sources=ROLE_OVERLAP_SOURCES,
            )
            packet = build_role_overlap_activation_packet(
                schema_status=schema_status,
                adjudication_preview=adjudication_preview,
                materialization_preview=materialization_preview,
                limit=args.limit,
            )
        await session.rollback()
    print(json.dumps(packet, indent=args.indent, default=str))
    await shutdown_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
