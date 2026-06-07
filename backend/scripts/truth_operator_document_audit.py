"""Audit an exported operator document for exact property-management evidence rows.

This script is read-only. It accepts a local CSV/XLSX export, matches exact
target address aliases, emits preview-only source-evidence intake candidates
for direct operator workbooks when possible, emits source-acquisition clues
for derivative research, and intentionally omits financial columns from output.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.operator_document_audit import (  # noqa: E402
    DOCUMENT_KINDS,
    HPM_LEAD_ID,
    HPM_MANAGER_NAME,
    HPM_SOURCE_NAME,
    TARGET_PRESETS,
    audit_operator_document_rows,
    build_custom_targets,
    build_relationship_targets_from_rows,
)
from scripts.truth_source_acquisition_worklist import build_source_acquisition_worklist  # noqa: E402
from scripts.truth_source_evidence_intake import _preview_payloads  # noqa: E402
from scripts.truth_verification_frontier import build_frontier_for_local_db  # noqa: E402
from src.db.session import get_session_factory, shutdown_engine  # noqa: E402
from src.services.source_evidence_intake import (  # noqa: E402
    build_source_acquisition_clue_only_preview,
    filter_source_evidence_batch_to_recommended_scope,
)


CURRENT_RELATIONSHIP_TARGET_PRESET = "current-lead-relationships"


def _read_rows(path: Path, *, sheet_name: str | None) -> list[dict[str, Any]]:
    import pandas as pd

    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls", ".xlsm"}:
        frame = pd.read_excel(path, sheet_name=sheet_name or 0)
    elif suffix in {".csv", ".tsv"}:
        frame = pd.read_csv(path, sep="\t" if suffix == ".tsv" else ",")
    else:
        raise ValueError(f"Unsupported operator document extension: {suffix}")
    frame = frame.where(pd.notna(frame), None)
    return [dict(row) for row in frame.to_dict(orient="records")]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="Local CSV/XLSX export path. The file is read only.")
    parser.add_argument("--sheet-name", default="Summary")
    parser.add_argument("--document-title")
    parser.add_argument(
        "--document-kind",
        choices=DOCUMENT_KINDS,
        default="operator_workbook",
        help=(
            "Use operator_workbook for direct operator-provenance exports that may emit preview candidates. "
            "Use derived_research or public_source_clue for research/AI/public-note material; those emit "
            "source-acquisition clues only."
        ),
    )
    parser.add_argument("--observed-at", help="Observation/provenance timestamp for emitted intake candidates.")
    parser.add_argument(
        "--operator-confirmed-document-provenance",
        action="store_true",
        help=(
            "Mark emitted candidates as role-specific management support. Use only when the operator has "
            "confirmed the document provenance and manager-role meaning."
        ),
    )
    parser.add_argument("--property-column", default="Property")
    parser.add_argument("--source-column", default="Source")
    parser.add_argument(
        "--target-preset",
        choices=sorted((*TARGET_PRESETS, CURRENT_RELATIONSHIP_TARGET_PRESET)),
        default="hpm-pilot",
        help=(
            "Built-in target set to check. current-lead-relationships loads exact-property targets "
            "from the local building_management table for --relationship-lead-id."
        ),
    )
    parser.add_argument(
        "--relationship-lead-id",
        default=HPM_LEAD_ID,
        help="Lead ID used by --target-preset current-lead-relationships.",
    )
    parser.add_argument(
        "--relationship-manager-name",
        default=HPM_MANAGER_NAME,
        help="Expected manager label used by --target-preset current-lead-relationships.",
    )
    parser.add_argument(
        "--relationship-source-name",
        default=HPM_SOURCE_NAME,
        help="Recordable source name for candidates emitted from current relationship target audits.",
    )
    parser.add_argument(
        "--relationship-target-limit",
        type=int,
        help="Optional max current relationship targets to audit.",
    )
    parser.add_argument(
        "--target",
        action="append",
        default=[],
        help="Additional exact target address to check. Can be repeated.",
    )
    parser.add_argument(
        "--preview-source-evidence-intake",
        action="store_true",
        help=(
            "After the audit, run emitted evidence candidates or clue-only rows through the read-only "
            "source-evidence intake preview path. This records nothing."
        ),
    )
    parser.add_argument(
        "--preview-recommended-scope-only",
        action="store_true",
        help=(
            "With --preview-source-evidence-intake, show only candidates that add a new supporting source. "
            "Still read-only and never an execution approval."
        ),
    )
    parser.add_argument("--intake-lead-id", default=HPM_LEAD_ID)
    parser.add_argument("--intake-frontier-limit", type=int, default=10)
    parser.add_argument("--intake-max-items", type=int, default=25)
    parser.add_argument("--recorded-by", default="operator")
    parser.add_argument("--run-id")
    parser.add_argument("--indent", type=int, default=2)
    return parser.parse_args()


async def _load_current_relationship_targets(args: argparse.Namespace):
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            text(
                """
                SELECT bm.bbl, b.address, bm.role
                FROM building_management bm
                JOIN buildings b ON b.bbl = bm.bbl
                WHERE bm.lead_id = :lead_id
                  AND bm.is_current = true
                  AND b.address IS NOT NULL
                ORDER BY b.address ASC, bm.bbl ASC
                LIMIT :limit
                """
            ),
            {
                "lead_id": args.relationship_lead_id,
                "limit": args.relationship_target_limit if args.relationship_target_limit else 100000,
            },
        )
        rows = [dict(row) for row in result.mappings().all()]
    return build_relationship_targets_from_rows(
        rows,
        expected_manager=args.relationship_manager_name,
        manager_lead_id=args.relationship_lead_id,
        source_name=args.relationship_source_name,
    )


async def _preview_source_evidence_intake_for_audit(
    audit_result: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    candidates = [
        candidate
        for candidate in audit_result.get("source_evidence_intake_candidates", [])
        if isinstance(candidate, dict)
    ]
    clues = [
        clue
        for clue in audit_result.get("source_acquisition_clues", [])
        if isinstance(clue, dict)
    ]
    if not candidates:
        if clues:
            return build_source_acquisition_clue_only_preview(
                clues,
                source_mode="operator_document_audit",
            )
        return {
            "run_type": "operator_document_source_evidence_intake_preview",
            "dry_run": True,
            "mutations_planned": 0,
            "allowed_execute": False,
            "candidate_count": 0,
            "source_acquisition_clue_count": 0,
            "recording_ready_count": 0,
            "recording_ready_status": "no_evidence_candidates",
            "approval_required_before_recording": True,
            "safe_action": (
                "The operator-document audit emitted no evidence candidates or source-acquisition clues. "
                "Acquire a matching exact-property source row before previewing manual evidence."
            ),
        }

    frontier = await build_frontier_for_local_db(
        lead_id=args.intake_lead_id,
        limit=args.intake_frontier_limit,
    )
    worklist = build_source_acquisition_worklist(
        frontier,
        max_items=args.intake_max_items,
    )
    preview = await _preview_payloads(
        payloads=candidates,
        worklist=worklist,
        recorded_by=args.recorded_by,
        run_id=args.run_id,
        source_mode="operator_document_audit",
    )
    if args.preview_recommended_scope_only:
        preview = filter_source_evidence_batch_to_recommended_scope(preview)
    if clues:
        preview["source_acquisition_clue_count"] = len(clues)
        preview["source_acquisition_clues"] = clues
        preview["source_clue_safe_action"] = (
            "These clues were not previewed as evidence. Inspect cited primary sources before creating "
            "separate evidence candidates."
        )
    return preview


async def async_main() -> int:
    args = parse_args()
    path = Path(args.file).expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"Operator document export not found: {path}")
    rows = _read_rows(path, sheet_name=args.sheet_name)
    engine_used = False
    if args.target_preset == CURRENT_RELATIONSHIP_TARGET_PRESET:
        preset_targets = await _load_current_relationship_targets(args)
        engine_used = True
        target_generation = {
            "mode": CURRENT_RELATIONSHIP_TARGET_PRESET,
            "relationship_lead_id": args.relationship_lead_id,
            "relationship_manager_name": args.relationship_manager_name,
            "target_count": len(preset_targets),
            "role_boundary_note": (
                "Current building_management rows are used only to generate exact-property matching targets. "
                "They do not prove a manages_building claim; the reviewed operator document row must independently "
                "support the management role before evidence can be recorded."
            ),
        }
    else:
        preset_targets = TARGET_PRESETS[args.target_preset]
        target_generation = {"mode": args.target_preset, "target_count": len(preset_targets)}
    targets = preset_targets + build_custom_targets(args.target)
    result = audit_operator_document_rows(
        rows,
        targets=targets,
        document_title=args.document_title or path.stem,
        property_column=args.property_column,
        source_column=args.source_column,
        observed_at=args.observed_at,
        operator_confirmed_document_provenance=args.operator_confirmed_document_provenance,
        document_kind=args.document_kind,
    )
    result["target_generation"] = target_generation
    result["source_file_name"] = path.name
    result["source_file_path_recorded"] = False
    if args.preview_source_evidence_intake:
        result["source_evidence_intake_preview"] = await _preview_source_evidence_intake_for_audit(
            result,
            args,
        )
        result["source_evidence_intake_preview_boundary"] = {
            "dry_run": True,
            "mutations_planned": 0,
            "allowed_execute": False,
            "approval_required_before_recording": True,
            "safe_action": (
                "Review this nested preview before any manual-evidence recording. A clean nested preview is "
                "still not execution approval and cannot mark claims verified or allow business use."
            ),
        }
        engine_used = True
    print(json.dumps(result, indent=args.indent, default=str))
    if engine_used:
        await shutdown_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
