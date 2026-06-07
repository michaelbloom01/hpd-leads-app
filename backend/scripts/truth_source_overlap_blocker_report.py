"""Explain why current source overlap still cannot produce verified claims.

This script is read-only. It rolls up the verification frontier and
source-acquisition worklist into a compact proof packet for the current source
bridge posture: what is already source-ready, what is still missing, which
source families have already been reviewed, and why nothing can be recorded or
verified without new evidence plus explicit approval.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.truth_source_acquisition_worklist import build_source_acquisition_worklist  # noqa: E402
from scripts.truth_verification_frontier import build_frontier_for_local_db  # noqa: E402
from src.db.session import shutdown_engine  # noqa: E402
from src.services.source_evidence_intake import (  # noqa: E402
    build_source_acquisition_clue_only_preview,
    extract_source_acquisition_clues,
    extract_source_evidence_intake_candidates,
    filter_source_evidence_batch_to_recommended_scope,
)
from scripts.truth_source_evidence_intake import _preview_payloads  # noqa: E402


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _compact_relationship(item: dict[str, Any]) -> dict[str, Any]:
    relationship = _as_dict(item.get("relationship"))
    return {
        "work_item_id": item.get("work_item_id"),
        "priority": item.get("priority"),
        "request_type": item.get("request_type"),
        "relationship_label": relationship.get("relationship_label"),
        "bbl": relationship.get("bbl"),
        "address": relationship.get("address"),
        "manager_name": relationship.get("manager_name"),
        "current_sources": _as_list(item.get("current_sources")),
        "source_family_needs": _as_list(item.get("source_family_needs")),
        "strict_manager_gap_status": item.get("strict_manager_gap_status"),
        "reviewed_source_history_status": item.get("reviewed_source_history_status"),
        "has_official_hpd_query_packet": bool(item.get("official_hpd_query")),
        "post_fetch_local_extract_command": item.get("post_fetch_local_extract_command"),
        "safe_action": item.get("safe_action"),
    }


def _compact_reviewed_source_findings(findings: Any, *, limit: int = 2) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for finding in _as_list(findings):
        if not isinstance(finding, dict):
            continue
        compact.append({
            "source_family": finding.get("source_family"),
            "finding": finding.get("finding"),
            "qualification": finding.get("qualification"),
        })
        if len(compact) >= limit:
            break
    return compact


def _relationship_label(item: dict[str, Any]) -> str:
    relationship = _as_dict(item.get("relationship"))
    return str(
        item.get("relationship_label")
        or relationship.get("relationship_label")
        or ""
    ).strip()


def _review_context_by_relationship(evidence_packet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    contexts: dict[str, dict[str, Any]] = {}
    for request in _as_list(evidence_packet.get("requests")):
        if not isinstance(request, dict):
            continue
        label = _relationship_label(request)
        if not label:
            continue
        existing = contexts.get(label)
        if existing and existing.get("request_type") == "source_ready_below_verified":
            continue
        contexts[label] = {
            "request_type": request.get("request_type"),
            "reviewed_source_history_status": request.get("reviewed_source_history_status"),
            "reviewed_source_findings": _compact_reviewed_source_findings(
                request.get("reviewed_source_findings"),
            ),
            "required_real_evidence_count": request.get("required_real_evidence_count"),
        }
    return contexts


def _threshold_sensitive_relationships(
    source_ready: dict[str, Any],
    *,
    evidence_packet: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return source-ready facts where one stronger source could clear verification."""
    relationships: list[dict[str, Any]] = []
    review_contexts = _review_context_by_relationship(evidence_packet)
    for proposal in _as_list(source_ready.get("proposals")):
        if not isinstance(proposal, dict):
            continue
        best_upgrade = _as_dict(proposal.get("best_single_source_upgrade"))
        if best_upgrade.get("would_reach_verified_threshold") is not True:
            continue
        display = _as_dict(proposal.get("display"))
        building = _as_dict(display.get("building"))
        relationship_label = display.get("relationship_label")
        review_context = review_contexts.get(str(relationship_label or "").strip(), {})
        relationships.append({
            "relationship_label": relationship_label,
            "bbl": building.get("bbl"),
            "address": building.get("address"),
            "manager_name": display.get("subject_label"),
            "current_sources": _as_list(proposal.get("current_sources")),
            "current_confidence_score": proposal.get("recomputed_confidence_score"),
            "verified_confidence_threshold": proposal.get("verified_confidence_threshold"),
            "score_gap_to_verified": proposal.get("score_gap_to_verified"),
            "best_single_source": best_upgrade.get("suggested_source"),
            "best_single_source_simulated_confidence": best_upgrade.get("simulated_confidence_score"),
            "required_bundle_sources": _as_list(proposal.get("required_bundle_sources")),
            "required_real_evidence_count": review_context.get("required_real_evidence_count"),
            "recording_ready": proposal.get("recording_ready") is True,
            "approval_required_before_recording": proposal.get("approval_required_before_recording") is True,
            "reviewed_source_history_status": review_context.get("reviewed_source_history_status"),
            "reviewed_source_findings": review_context.get("reviewed_source_findings", []),
            "safe_action": proposal.get("safe_action"),
        })
    return relationships


def _reviewed_source_summary(work_items: list[dict[str, Any]]) -> dict[str, Any]:
    family_counts: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []
    for item in work_items:
        for finding in _as_list(item.get("reviewed_source_findings")):
            if not isinstance(finding, dict):
                continue
            family = str(finding.get("source_family") or "unknown").strip() or "unknown"
            family_counts[family] += 1
            if len(examples) < 5:
                examples.append({
                    "source_family": family,
                    "finding": finding.get("finding"),
                    "qualification": finding.get("qualification"),
                })
    return {
        "reviewed_source_family_counts": dict(sorted(family_counts.items())),
        "sample_reviewed_source_findings": examples,
    }


def _source_evidence_candidate_summary(batch_preview: dict[str, Any] | None) -> dict[str, Any]:
    """Summarize an optional filled-candidate preview without treating it as approval."""
    if not batch_preview:
        return {
            "status": "not_checked",
            "checked": False,
            "candidate_count": 0,
            "recording_ready_count": 0,
            "recommended_count": 0,
            "allowed_execute": False,
            "safe_action": (
                "No filled source-evidence candidate batch was supplied to this blocker report. "
                "Run truth_source_evidence_intake.py with a filled candidate file or CSV to preview "
                "recording readiness before asking for execution approval."
            ),
        }

    if batch_preview.get("run_type") == "truth_source_evidence_intake_clue_only_preview":
        clue_count = int(batch_preview.get("source_acquisition_clue_count") or 0)
        return {
            "status": "source_clue_only_primary_source_required",
            "checked": True,
            "source_mode": batch_preview.get("source_mode"),
            "candidate_count": 0,
            "source_acquisition_clue_count": clue_count,
            "source_acquisition_clues": _as_list(batch_preview.get("source_acquisition_clues")),
            "recording_ready_count": 0,
            "recommended_count": 0,
            "allowed_execute": False,
            "approval_required_before_recording": True,
            "can_record_evidence_now": False,
            "safe_action": batch_preview.get("safe_action") or (
                "Source-acquisition clues require primary-source review before any evidence can be recorded."
            ),
        }

    recommended_scope = _as_dict(batch_preview.get("recommended_recording_scope"))
    approval_packet = _as_dict(batch_preview.get("recording_approval_packet"))
    recommended_relationships = _as_list(recommended_scope.get("recommended_relationships"))
    contradiction_relationships = _as_list(recommended_scope.get("contradiction_relationships"))
    duplicate_relationships = _as_list(recommended_scope.get("duplicate_or_freshness_only_relationships"))
    recommended_count = int(recommended_scope.get("recommended_count") or 0)
    recording_ready_count = int(batch_preview.get("recording_ready_count") or 0)
    if recommended_count > 0:
        status = "preview_ready_approval_required"
    elif recording_ready_count > 0:
        status = "preview_ready_non_recommended_scope"
    else:
        status = "no_recording_ready_candidates"
    return {
        "status": status,
        "checked": True,
        "source_mode": batch_preview.get("source_mode"),
        "candidate_count": batch_preview.get("candidate_count"),
        "source_acquisition_clue_count": batch_preview.get("source_acquisition_clue_count"),
        "source_acquisition_clues": _as_list(batch_preview.get("source_acquisition_clues")),
        "original_candidate_count": batch_preview.get("original_candidate_count"),
        "filtered_out_candidate_count": batch_preview.get("filtered_out_candidate_count"),
        "ready_for_manual_evidence_preview_count": batch_preview.get("ready_for_manual_evidence_preview_count"),
        "recording_ready_count": recording_ready_count,
        "new_supporting_source_ready_count": batch_preview.get("new_supporting_source_ready_count"),
        "supporting_source_already_present_count": batch_preview.get("supporting_source_already_present_count"),
        "contradiction_candidate_count": batch_preview.get("contradiction_candidate_count"),
        "blocked_count": batch_preview.get("blocked_count"),
        "recommended_count": recommended_count,
        "recommended_relationships": recommended_relationships,
        "duplicate_or_freshness_only_count": recommended_scope.get("duplicate_or_freshness_only_count"),
        "duplicate_or_freshness_only_relationships": duplicate_relationships,
        "contradiction_review_count": recommended_scope.get("contradiction_review_count"),
        "contradiction_relationships": contradiction_relationships,
        "allowed_execute": batch_preview.get("allowed_execute") is True,
        "required_execute_flags_for_batch": batch_preview.get("required_execute_flags_for_batch"),
        "recording_approval_packet": approval_packet or None,
        "post_recording_expectations": recommended_scope.get("post_recording_expectations"),
        "safe_action": (
            "Candidate preview is read-only. A preview-ready candidate still requires explicit execution "
            "approval and the required batch flags; it will not mark verified, refresh sources, materialize "
            "relationships, start jobs, or allow business use."
        ),
    }


def build_source_overlap_blocker_report(
    *,
    frontier: dict[str, Any],
    worklist: dict[str, Any],
    source_evidence_batch_preview: dict[str, Any] | None = None,
    max_relationships: int = 10,
) -> dict[str, Any]:
    """Build a compact read-only report from current frontier/worklist evidence."""
    current_ledger = _as_dict(frontier.get("current_ledger") or worklist.get("frontier_current_ledger"))
    evidence_packet = _as_dict(frontier.get("evidence_request_packet"))
    verification_gate = _as_dict(
        evidence_packet.get("verification_readiness_gate")
        or frontier.get("verification_readiness_gate")
    )
    source_ready = _as_dict(frontier.get("source_ready_below_verified"))
    work_items = [item for item in _as_list(worklist.get("work_items")) if isinstance(item, dict)]
    recording_ready_count = int(worklist.get("recording_ready_count") or evidence_packet.get("recording_ready_count") or 0)
    candidate_summary = _source_evidence_candidate_summary(source_evidence_batch_preview)
    candidate_recommended_count = int(candidate_summary.get("recommended_count") or 0)
    candidate_recording_ready_count = int(candidate_summary.get("recording_ready_count") or 0)
    can_request_recording_approval = (
        candidate_recommended_count > 0
        and candidate_recording_ready_count > 0
        and candidate_summary.get("allowed_execute") is False
    )
    verification_candidate_count = int(frontier.get("verification_candidate_count") or 0)
    source_ready_count = int(current_ledger.get("source_ready_fact_group_count") or 0)
    reviewed_summary = _reviewed_source_summary(work_items)
    threshold_sensitive_relationships = _threshold_sensitive_relationships(
        source_ready,
        evidence_packet=evidence_packet,
    )

    blockers: list[str] = []
    if verification_candidate_count == 0:
        blockers.append("verification_candidate_count=0")
    if recording_ready_count == 0:
        blockers.append("recording_ready_count=0")
    if int(worklist.get("request_count") or 0) > 0:
        blockers.append("source_acquisition_requests_remain_open")
    if verification_gate.get("status"):
        blockers.append(f"verification_readiness_gate={verification_gate.get('status')}")
    if candidate_summary.get("status") == "source_clue_only_primary_source_required":
        blockers.append("source_clue_only_primary_source_required")
    if can_request_recording_approval:
        blockers.append("execution_approval_required_for_preview_ready_candidates")

    status = (
        "ready_for_manual_recording_review"
        if recording_ready_count > 0
        else "blocked_evidence_acquisition_required"
    )
    return {
        "run_type": "truth_source_overlap_blocker_report",
        "dry_run": True,
        "mutations_planned": 0,
        "status": status,
        "current_ledger": current_ledger,
        "verification_candidate_count": verification_candidate_count,
        "source_ready_fact_group_count": source_ready_count,
        "source_ready_below_verified_count": source_ready.get("proposal_count"),
        "single_source_upgrade_would_verify_count": source_ready.get("single_source_upgrade_would_verify_count"),
        "bundle_upgrade_would_verify_count": source_ready.get("bundle_upgrade_would_verify_count"),
        "threshold_sensitive_relationships": threshold_sensitive_relationships,
        "evidence_request_summary": {
            "request_count": worklist.get("request_count"),
            "work_item_count": worklist.get("work_item_count"),
            "hpd_work_item_count": worklist.get("hpd_work_item_count"),
            "recording_ready_count": recording_ready_count,
            "approval_required_count": worklist.get("approval_required_count"),
            "reviewed_source_finding_count": evidence_packet.get("reviewed_source_finding_count"),
            "reviewed_source_history_status": evidence_packet.get("reviewed_source_history_status"),
            "verification_readiness_gate": verification_gate,
        },
        "source_bridge_assessment": {
            "can_record_evidence_now": recording_ready_count > 0,
            "can_request_recording_approval": can_request_recording_approval,
            "has_preview_ready_candidate_batch": bool(candidate_summary.get("recommended_count")),
            "candidate_preview_status": candidate_summary.get("status"),
            "candidate_recording_ready_count": candidate_recording_ready_count,
            "candidate_recommended_count": candidate_recommended_count,
            "candidate_allowed_execute": candidate_summary.get("allowed_execute") is True,
            "has_source_acquisition_clues": bool(candidate_summary.get("source_acquisition_clue_count")),
            "source_acquisition_clue_count": candidate_summary.get("source_acquisition_clue_count", 0),
            "can_mark_verified_now": verification_candidate_count > 0,
            "blocking_reasons": blockers,
            "approval_boundary": (
                "A preview-clean candidate batch can be used to ask for explicit recording approval, but it "
                "cannot execute, mark verified, refresh sources, materialize relationships, or allow business use."
                if can_request_recording_approval
                else (
                    "No preview-clean candidate batch is ready for an approval request."
                    if not candidate_recommended_count
                    else "Candidate preview still requires review before any approval request."
                )
            ),
            "why_current_overlap_is_not_enough": (
                "Current source-ready groups remain below the verified confidence threshold or still need "
                "fresh role-specific evidence. Already-reviewed public, Drive, and HPD-derived context does "
                "not supply a recording-ready exact-property manager-proof source."
            ),
        },
        "source_evidence_candidate_summary": candidate_summary,
        "top_blocked_relationships": [
            _compact_relationship(item) for item in work_items[: max(1, int(max_relationships or 10))]
        ],
        "reviewed_source_summary": reviewed_summary,
        "policy": {
            "single_source_policy": "No single-source claim may be marked verified.",
            "role_policy": "HPD Agent, SiteManager, CorporateOwner, HeadOfficer, owner, and legal-contact rows do not verify manages_building claims.",
            "recording_policy": "Manual evidence recording requires a clean preview plus explicit dry_run=false / confirm_execute=true approval.",
            "business_use_policy": "Business use remains blocked until verified-claim, review, freshness, activation, and production truth-surface gates pass.",
        },
        "next_required_action": (
            "Acquire a real exact-property, role-specific manager source or dated second operator/outreach "
            "confirmation, run source-evidence intake preview, then ask for explicit approval before recording."
        ),
        "safe_action": (
            "Use this report to explain the current blocker. It is not evidence, does not record evidence, "
            "does not adjudicate claims, and does not permit business use."
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lead-id", default="0ff794d3ba2d")
    parser.add_argument("--frontier-limit", type=int, default=10)
    parser.add_argument("--max-items", type=int, default=10)
    parser.add_argument("--max-relationships", type=int, default=10)
    candidate_source = parser.add_mutually_exclusive_group()
    candidate_source.add_argument(
        "--candidate-file",
        help="Optional JSON audit/candidate file containing source_evidence_intake_candidates to summarize.",
    )
    candidate_source.add_argument(
        "--candidate-csv",
        help="Optional filled source-evidence candidate CSV to summarize.",
    )
    parser.add_argument(
        "--recommended-scope-only",
        action="store_true",
        help="When candidate input is supplied, summarize only candidates that add a new supporting source.",
    )
    parser.add_argument("--indent", type=int, default=2)
    return parser.parse_args()


def _read_json(path: Path) -> Any:
    if not path.exists():
        raise SystemExit(f"Input JSON not found: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_candidate_file_payload(path: Path) -> Any:
    return _read_json(path)


def _read_candidate_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"Candidate CSV not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        candidates = [
            {str(key).strip(): value for key, value in row.items() if key and str(key).strip()}
            for row in reader
            if any(str(value or "").strip() for value in row.values())
        ]
    if not candidates:
        raise SystemExit("Candidate CSV contained no non-empty rows.")
    return candidates


async def _build_optional_candidate_preview(
    *,
    args: argparse.Namespace,
    worklist: dict[str, Any],
) -> dict[str, Any] | None:
    if not args.candidate_file and not args.candidate_csv:
        return None
    if args.candidate_file:
        candidate_payload = _read_candidate_file_payload(Path(args.candidate_file).expanduser().resolve())
        payloads = extract_source_evidence_intake_candidates(candidate_payload)
        clues = extract_source_acquisition_clues(candidate_payload)
        source_mode = "candidate_file"
        if not payloads and clues:
            return build_source_acquisition_clue_only_preview(clues, source_mode=source_mode)
        if not payloads:
            raise SystemExit("Candidate file contained no source_evidence_intake_candidates or source_acquisition_clues.")
    else:
        payloads = _read_candidate_csv(Path(args.candidate_csv).expanduser().resolve())
        clues = []
        source_mode = "candidate_csv"
    preview = await _preview_payloads(
        payloads=payloads,
        worklist=worklist,
        recorded_by="operator",
        run_id=None,
        source_mode=source_mode,
    )
    if args.recommended_scope_only:
        preview = filter_source_evidence_batch_to_recommended_scope(preview)
    if clues:
        preview["source_acquisition_clue_count"] = len(clues)
        preview["source_acquisition_clues"] = clues
        preview["source_clue_safe_action"] = (
            "Source-acquisition clues are not evidence candidates. Inspect the cited primary source, "
            "then rerun preview with exact-property role-specific source evidence."
        )
    return preview


async def async_main() -> int:
    args = parse_args()
    frontier = await build_frontier_for_local_db(lead_id=args.lead_id, limit=args.frontier_limit)
    worklist = build_source_acquisition_worklist(frontier, max_items=args.max_items)
    candidate_preview = await _build_optional_candidate_preview(args=args, worklist=worklist)
    report = build_source_overlap_blocker_report(
        frontier=frontier,
        worklist=worklist,
        source_evidence_batch_preview=candidate_preview,
        max_relationships=args.max_relationships,
    )
    print(json.dumps(report, indent=args.indent, default=str))
    await shutdown_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
