"""Preview or record operator-reviewed evidence into the truth ledger."""

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

from src.db.session import get_session_factory  # noqa: E402
from src.services.manual_evidence import preview_or_record_manual_evidence  # noqa: E402
from src.services.truth_health import build_schema_readiness_report, is_truth_schema_current, load_truth_schema_status  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--payload-file",
        help=(
            "Path to a manual-evidence payload JSON, a source-evidence intake preview JSON, "
            "or a source-evidence batch preview JSON. Batch previews replay only ready candidates "
            "that add a new supporting source."
        ),
    )
    parser.add_argument("--subject-type")
    parser.add_argument("--subject-id")
    parser.add_argument("--predicate")
    parser.add_argument("--object-type")
    parser.add_argument("--object-id")
    parser.add_argument("--claim-type")
    parser.add_argument("--normalized-value")
    parser.add_argument("--extracted-value")
    parser.add_argument("--support-status", default="supports", choices=["supports", "contradicts"])
    parser.add_argument("--source-name", default="manual_evidence")
    parser.add_argument("--source-type", default="operator_review")
    parser.add_argument("--source-record-id")
    parser.add_argument("--source-url")
    parser.add_argument("--observed-at")
    parser.add_argument("--note")
    parser.add_argument("--recorded-by", default="operator")
    parser.add_argument("--run-id")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-execute", action="store_true")
    parser.add_argument(
        "--confirm-batch-execute",
        action="store_true",
        help=(
            "Required with --execute --confirm-execute when --payload-file extracts more than one "
            "manual-evidence payload."
        ),
    )
    parser.add_argument("--indent", type=int, default=2)
    return parser.parse_args()


MANUAL_EVIDENCE_REQUIRED_FIELDS = {
    "subject_type",
    "subject_id",
    "predicate",
    "object_type",
    "object_id",
    "claim_type",
}

POST_EXECUTION_EXPECTATIONS = {
    "must_run": [
        "truth_adjudication_preview.py",
        "truth_source_overlap_post_recording_check.py",
        "truth_health_report.py",
        "truth_completion_audit.py --include-runtime",
    ],
    "must_hold": {
        "no_single_source_claim_marked_verified": True,
        "no_automatic_verified_status_change": True,
        "no_source_refresh": True,
        "no_relationship_materialization": True,
        "no_business_use_activation": True,
    },
    "acceptable_after_operator_seed_recording": {
        "verification_candidate_count_may_remain_zero": True,
        "runtime_completion_audit_may_remain_not_complete": True,
        "truth_health_may_remain_not_ready": True,
        "reason": (
            "Operator seed evidence can add ledger support without proving a verified manager fact. "
            "Verification still depends on adjudication thresholds and, where needed, additional exact-property "
            "role-specific independent sources."
        ),
    },
}


def _utc_batch_run_id() -> str:
    return f"truth-manual-evidence-batch-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"


def _read_json(path: Path) -> Any:
    if not path.exists():
        raise SystemExit(f"Payload file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _is_manual_evidence_payload(payload: Any) -> bool:
    return isinstance(payload, dict) and MANUAL_EVIDENCE_REQUIRED_FIELDS.issubset(payload)


def _extract_manual_payloads(payload: Any) -> list[dict[str, Any]]:
    """Extract executable manual-evidence payloads from exact preview artifacts."""
    if isinstance(payload, list):
        payloads = [_extract_manual_payloads(item) for item in payload]
        return [item for group in payloads for item in group]
    if not isinstance(payload, dict):
        return []
    if _is_manual_evidence_payload(payload):
        return [payload]
    if isinstance(payload.get("manual_evidence_payload"), dict):
        manual_payload = payload["manual_evidence_payload"]
        return [manual_payload] if _is_manual_evidence_payload(manual_payload) else []
    if isinstance(payload.get("manual_evidence_payloads"), list):
        return [
            item
            for item in payload["manual_evidence_payloads"]
            if _is_manual_evidence_payload(item)
        ]
    extracted: list[dict[str, Any]] = []
    for preview in payload.get("previews", []):
        if not isinstance(preview, dict):
            continue
        effect = preview.get("source_overlap_effect") if isinstance(preview.get("source_overlap_effect"), dict) else {}
        if (
            preview.get("validation_status") != "ready_for_manual_evidence_preview"
            or preview.get("recording_ready") is not True
            or effect.get("adds_new_supporting_source") is not True
        ):
            continue
        manual_payload = preview.get("manual_evidence_payload")
        if _is_manual_evidence_payload(manual_payload):
            extracted.append(manual_payload)
    return extracted


def _payloads_from_args(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.payload_file:
        parsed = _read_json(Path(args.payload_file).expanduser().resolve())
        payloads = _extract_manual_payloads(parsed)
        if not payloads:
            raise SystemExit(
                "Payload file did not contain a direct manual-evidence payload, "
                "a source-evidence intake preview, or ready new-supporting-source batch previews."
            )
        return payloads
    required_args = [
        "subject_type",
        "subject_id",
        "predicate",
        "object_type",
        "object_id",
        "claim_type",
    ]
    missing = [field.replace("_", "-") for field in required_args if not getattr(args, field)]
    if missing:
        raise SystemExit(f"Missing required manual-evidence arguments: {', '.join(missing)}")
    return [{
        "subject_type": args.subject_type,
        "subject_id": args.subject_id,
        "predicate": args.predicate,
        "object_type": args.object_type,
        "object_id": args.object_id,
        "claim_type": args.claim_type,
        "normalized_value": args.normalized_value,
        "extracted_value": args.extracted_value,
        "support_status": args.support_status,
        "source_name": args.source_name,
        "source_type": args.source_type,
        "source_record_id": args.source_record_id,
        "source_url": args.source_url,
        "observed_at": args.observed_at,
        "note": args.note,
    }]


def _batch_result(
    *,
    results: list[dict[str, Any]],
    batch_run_id: str,
    payload_count: int,
    execute_requested: bool,
    confirm_execute: bool,
    confirm_batch_execute: bool,
) -> dict[str, Any]:
    allowed_execute = execute_requested and confirm_execute and confirm_batch_execute
    return {
        "run_type": "manual_evidence_batch_capture",
        "batch_run_id": batch_run_id,
        "dry_run": not allowed_execute,
        "allowed_execute": allowed_execute,
        "mutations_planned": sum(int(result.get("mutations_planned") or 0) for result in results),
        "payload_count": payload_count,
        "result_count": len(results),
        "result_run_ids": [result.get("run_id") for result in results],
        "claims_upserted": sum(int(result.get("claims_upserted") or 0) for result in results),
        "evidence_upserted": sum(int(result.get("evidence_upserted") or 0) for result in results),
        "confidence_snapshots_upserted": sum(
            int(result.get("confidence_snapshots_upserted") or 0)
            for result in results
        ),
        "approval_boundary": {
            "explicit_approval_required": True,
            "required_execute_flags": [
                "--execute",
                "--confirm-execute",
                "--confirm-batch-execute",
            ],
            "requested_execute": execute_requested,
            "confirm_execute": confirm_execute,
            "confirm_batch_execute": confirm_batch_execute,
            "will_mark_verified": False,
            "will_refresh_sources": False,
            "will_materialize_relationships": False,
            "will_start_jobs": False,
            "will_allow_business_use": False,
        },
        "required_execute_params": {
            "execute": True,
            "confirm_execute": True,
            "confirm_batch_execute": True,
        },
        "mutation_scope": results[0].get("mutation_scope") if results else None,
        "results": results,
        "safe_action": (
            "Batch uses exact manual-evidence payloads extracted from reviewed preview artifacts. "
            "Execution still requires --execute --confirm-execute --confirm-batch-execute and does not "
            "adjudicate, mark verified, refresh sources, materialize relationships, start jobs, or allow business use."
        ),
        "blocked_reason": None if allowed_execute else (
            "Batch manual evidence capture defaults to preview; execute requires --execute, "
            "--confirm-execute, and --confirm-batch-execute."
        ),
        "next_required_checks": [
            "Run truth_adjudication_preview.py after any approved execution.",
            "Run truth_source_overlap_post_recording_check.py after any approved execution.",
            "Run truth_health_report.py after any approved execution.",
            "Run truth_completion_audit.py --include-runtime after any approved execution.",
        ],
        "post_execution_expectations": POST_EXECUTION_EXPECTATIONS,
    }


async def run(args: argparse.Namespace) -> dict:
    payloads = _payloads_from_args(args)
    factory = get_session_factory()
    async with factory() as session:
        schema_status = await load_truth_schema_status(session)
        if not is_truth_schema_current(schema_status):
            return build_schema_readiness_report(schema_status=schema_status)
        if len(payloads) == 1:
            result = await preview_or_record_manual_evidence(
                session,
                payload=payloads[0],
                recorded_by=args.recorded_by,
                dry_run=not args.execute,
                confirm_execute=args.confirm_execute,
                run_id=args.run_id,
            )
            result["schema_status"] = schema_status
            result["payload_source"] = "payload_file" if args.payload_file else "cli_args"
            return result
        batch_run_id = args.run_id or _utc_batch_run_id()
        results = []
        batch_execute_allowed = args.execute and args.confirm_execute and args.confirm_batch_execute
        for index, payload in enumerate(payloads, start=1):
            result = await preview_or_record_manual_evidence(
                session,
                payload=payload,
                recorded_by=args.recorded_by,
                dry_run=not batch_execute_allowed,
                confirm_execute=batch_execute_allowed,
                run_id=f"{batch_run_id}-{index:03d}",
            )
            result["payload_index"] = index
            results.append(result)
        result = _batch_result(
            results=results,
            batch_run_id=batch_run_id,
            payload_count=len(payloads),
            execute_requested=args.execute,
            confirm_execute=args.confirm_execute,
            confirm_batch_execute=args.confirm_batch_execute,
        )
        result["schema_status"] = schema_status
        result["payload_source"] = "payload_file" if args.payload_file else "cli_args"
        return result


def main() -> int:
    args = parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(result, indent=args.indent, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
