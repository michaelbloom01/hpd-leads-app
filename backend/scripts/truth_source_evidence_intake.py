"""Validate source-acquisition paste-back evidence and preview manual evidence.

This script is read-only. It accepts either a JSON object filled from
truth_source_acquisition_worklist.py, a CSV paste-back file, or any audit
output containing source_evidence_intake_candidates or source_acquisition_clues.
It resolves each evidence candidate against the current worklist, checks
exact-property and role-specific evidence fields, and runs the existing
manual-evidence preview only when the intake is clean. Clue-only packets return
an explicit primary-source-required preview. It never records evidence or
changes claim status.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.truth_verification_frontier import build_frontier_for_local_db  # noqa: E402
from scripts.truth_source_acquisition_worklist import build_source_acquisition_worklist  # noqa: E402
from src.db.session import get_session_factory, shutdown_engine  # noqa: E402
from src.services.manual_evidence import preview_or_record_manual_evidence  # noqa: E402
from src.services.source_evidence_intake import (  # noqa: E402
    build_source_acquisition_clue_only_preview,
    build_source_evidence_intake_batch_preview,
    build_source_evidence_intake_preview,
    extract_source_acquisition_clues,
    extract_source_evidence_intake_candidates,
    filter_source_evidence_batch_to_recommended_scope,
)
from src.services.truth_health import build_schema_readiness_report, is_truth_schema_current, load_truth_schema_status  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--payload-file", help="Path to a JSON object filled from the worklist paste-back template.")
    source.add_argument(
        "--candidate-file",
        "--hpd-audit-file",
        dest="candidate_file",
        help="Path to audit JSON output containing source_evidence_intake_candidates.",
    )
    source.add_argument(
        "--candidate-csv",
        help=(
            "Path to a CSV paste-back file with source-evidence intake headers. "
            "Rows are previewed only; no evidence is recorded."
        ),
    )
    parser.add_argument("--lead-id", default="0ff794d3ba2d")
    parser.add_argument("--frontier-limit", type=int, default=10)
    parser.add_argument("--max-items", type=int, default=25)
    parser.add_argument("--recorded-by", default="operator")
    parser.add_argument("--run-id")
    parser.add_argument(
        "--recommended-scope-only",
        action="store_true",
        help=(
            "For candidate-file or candidate-csv batch previews, return only candidates that add a new "
            "supporting source. This is still read-only and does not record evidence."
        ),
    )
    parser.add_argument("--indent", type=int, default=2)
    return parser.parse_args()


def _read_json(path: Path) -> Any:
    if not path.exists():
        raise SystemExit(f"Input JSON not found: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_payload(path: Path) -> dict[str, Any]:
    parsed = _read_json(path)
    if not isinstance(parsed, dict):
        raise SystemExit("Paste-back payload must be a JSON object.")
    return parsed


def _extract_hpd_audit_candidates(payload: Any) -> list[dict[str, Any]]:
    """Extract source-evidence intake candidates from audit output."""
    return extract_source_evidence_intake_candidates(payload)


def _extract_candidate_file_clues(payload: Any) -> list[dict[str, Any]]:
    """Extract source-acquisition clues from clue-only audit output."""
    return extract_source_acquisition_clues(payload)


def _read_candidate_file_payload(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    payload = _read_json(path)
    return _extract_hpd_audit_candidates(payload), _extract_candidate_file_clues(payload)


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


async def _preview_one_intake(
    *,
    session: Any,
    payload: dict[str, Any],
    worklist: dict[str, Any],
    schema_status: dict[str, Any],
    recorded_by: str,
    run_id: str | None,
) -> dict[str, Any]:
    intake = build_source_evidence_intake_preview(payload, worklist=worklist)
    intake["worklist_context"] = {
        "request_count": worklist.get("request_count"),
        "work_item_count": worklist.get("work_item_count"),
        "recording_ready_count": worklist.get("recording_ready_count"),
        "approval_required_count": worklist.get("approval_required_count"),
    }
    if intake["validation_status"] != "ready_for_manual_evidence_preview":
        return intake

    if not is_truth_schema_current(schema_status):
        readiness = build_schema_readiness_report(schema_status=schema_status)
        intake["schema_status"] = schema_status
        intake["manual_evidence_preview"] = readiness
        intake["blocking_reasons"] = [
            *intake.get("blocking_reasons", []),
            "truth_schema_not_current",
        ]
        intake["validation_status"] = "blocked_before_manual_evidence_preview"
        return intake
    preview = await preview_or_record_manual_evidence(
        session,
        payload=intake["manual_evidence_payload"],
        recorded_by=recorded_by,
        dry_run=True,
        confirm_execute=False,
        run_id=run_id,
    )
    intake["schema_status"] = schema_status
    intake["manual_evidence_preview"] = preview
    intake["recording_ready"] = preview.get("run_type") == "manual_evidence_capture" and preview.get("dry_run") is True
    intake["next_required_action"] = (
        "Review the manual_evidence_preview, expected mutation scope, and rollback plan. "
        "Record only after explicit dry_run=false / confirm_execute=true approval."
    )
    return intake


async def _preview_payloads(
    *,
    payloads: list[dict[str, Any]],
    worklist: dict[str, Any],
    recorded_by: str,
    run_id: str | None,
    source_mode: str,
) -> dict[str, Any]:
    factory = get_session_factory()
    async with factory() as session:
        schema_status = await load_truth_schema_status(session)
        previews = [
            await _preview_one_intake(
                session=session,
                payload=payload,
                worklist=worklist,
                schema_status=schema_status,
                recorded_by=recorded_by,
                run_id=run_id,
            )
            for payload in payloads
        ]
        await session.rollback()
    if len(previews) == 1 and source_mode == "payload_file":
        return previews[0]
    return build_source_evidence_intake_batch_preview(
        previews,
        candidate_count=len(payloads),
        source_mode=source_mode,
    )


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.payload_file:
        payloads = [_read_payload(Path(args.payload_file).expanduser().resolve())]
        source_mode = "payload_file"
    elif args.candidate_csv:
        payloads = _read_candidate_csv(Path(args.candidate_csv).expanduser().resolve())
        source_mode = "candidate_csv"
        source_clues: list[dict[str, Any]] = []
    else:
        payloads, source_clues = _read_candidate_file_payload(Path(args.candidate_file).expanduser().resolve())
        source_mode = "candidate_file"
        if not payloads:
            if source_clues:
                if args.recommended_scope_only:
                    raise SystemExit("--recommended-scope-only requires source_evidence_intake_candidates.")
                return build_source_acquisition_clue_only_preview(source_clues, source_mode=source_mode)
            raise SystemExit("Candidate file contained no source_evidence_intake_candidates or source_acquisition_clues.")
    if args.payload_file:
        source_clues = []
    frontier = await build_frontier_for_local_db(lead_id=args.lead_id, limit=args.frontier_limit)
    worklist = build_source_acquisition_worklist(frontier, max_items=args.max_items)
    result = await _preview_payloads(
        payloads=payloads,
        worklist=worklist,
        recorded_by=args.recorded_by,
        run_id=args.run_id,
        source_mode=source_mode,
    )
    if args.recommended_scope_only:
        if source_mode == "payload_file":
            raise SystemExit("--recommended-scope-only requires --candidate-file or --candidate-csv.")
        result = filter_source_evidence_batch_to_recommended_scope(result)
    if source_clues:
        result["source_acquisition_clue_count"] = len(source_clues)
        result["source_acquisition_clues"] = source_clues
        result["source_clue_safe_action"] = (
            "Clues in this packet were not previewed as evidence. Inspect the cited primary sources and create "
            "separate source-evidence candidates before recording anything."
        )
    return result


async def async_main() -> int:
    args = parse_args()
    result = await run(args)
    print(json.dumps(result, indent=args.indent, default=str))
    await shutdown_engine()
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
