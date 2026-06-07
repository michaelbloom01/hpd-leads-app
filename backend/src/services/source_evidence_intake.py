"""Read-only intake validation for source-acquisition paste-back evidence."""

from __future__ import annotations

import re
from typing import Any

from src.services.manual_evidence import ALLOWED_MANUAL_SOURCE_NAMES


REQUIRED_PASTE_BACK_FIELDS = [
    "relationship_label",
    "bbl",
    "address",
    "manager_name",
    "source_family",
    "source_name",
    "source_url_or_local_record_reference",
    "source_record_id",
    "observed_at",
    "exact_property_match",
    "role_specific_management_support",
    "source_excerpt_or_row_summary",
    "contradicts_current_claim",
    "notes",
]

REQUIRED_BEFORE_MANUAL_PREVIEW = [
    "source_name",
    "source_url_or_local_record_reference",
    "source_record_id",
    "observed_at",
    "exact_property_match",
]

ROLE_SUPPORT_REQUIRED_FOR_SUPPORTING_EVIDENCE = "role_specific_management_support"
CONTRADICTION_REQUIRED_FOR_CONTRADICTING_EVIDENCE = "contradicts_current_claim"
MANUAL_EVIDENCE_BATCH_REQUIRED_EXECUTE_FLAGS = [
    "--execute",
    "--confirm-execute",
    "--confirm-batch-execute",
]
MANUAL_EVIDENCE_BATCH_PREVIEW_COMMAND = "truth_manual_evidence.py --payload-file <reviewed-preview.json>"
MANUAL_EVIDENCE_BATCH_EXECUTE_COMMAND = (
    "truth_manual_evidence.py --payload-file <reviewed-preview.json> "
    "--execute --confirm-execute --confirm-batch-execute"
)

POST_RECORDING_EXPECTATIONS = {
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
            "Operator seed evidence can add ledger support without proving a verified manager fact. It may still "
            "be first-source-only when no independent support already exists. Verification depends on adjudication "
            "thresholds and, where needed, additional exact-property role-specific independent sources."
        ),
    },
}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"true", "t", "yes", "y", "1"}:
        return True
    if text in {"false", "f", "no", "n", "0"}:
        return False
    return None


def _normalize_for_match(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def extract_source_evidence_intake_candidates(payload: Any) -> list[dict[str, Any]]:
    """Extract source-evidence intake candidates from an HPD audit style payload."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("source_evidence_intake_candidates"), list):
        return [item for item in payload["source_evidence_intake_candidates"] if isinstance(item, dict)]
    candidates: list[dict[str, Any]] = []
    for result in payload.get("results", []):
        if not isinstance(result, dict):
            continue
        for candidate in result.get("source_evidence_intake_candidates", []):
            if isinstance(candidate, dict):
                candidates.append(candidate)
    return candidates


def extract_source_acquisition_clues(payload: Any) -> list[dict[str, Any]]:
    """Extract source-acquisition clues from audit outputs that cannot become evidence yet."""
    if isinstance(payload, list):
        return [
            item
            for item in payload
            if isinstance(item, dict) and item.get("clue_status") == "source_clue_only"
        ]
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("source_acquisition_clues"), list):
        return [item for item in payload["source_acquisition_clues"] if isinstance(item, dict)]
    clues: list[dict[str, Any]] = []
    for result in payload.get("results", []):
        if not isinstance(result, dict):
            continue
        for clue in result.get("source_acquisition_clues", []):
            if isinstance(clue, dict):
                clues.append(clue)
    return clues


def build_source_acquisition_clue_only_preview(
    clues: list[dict[str, Any]],
    *,
    source_mode: str,
) -> dict[str, Any]:
    """Return an explicit no-evidence preview for derivative/source-clue-only packets."""
    return {
        "run_type": "truth_source_evidence_intake_clue_only_preview",
        "source_mode": source_mode,
        "dry_run": True,
        "mutations_planned": 0,
        "allowed_execute": False,
        "candidate_count": 0,
        "source_acquisition_clue_count": len(clues),
        "source_acquisition_clues": clues,
        "recording_ready_count": 0,
        "recording_ready_status": "source_clue_only_primary_source_required",
        "source_evidence_intake_candidates": [],
        "approval_required_before_recording": True,
        "can_record_evidence_now": False,
        "safe_action": (
            "This packet contains source-acquisition clues only. Inspect the cited primary source, create a real "
            "source-evidence candidate with exact property, observed date, source record, and role-specific "
            "management support, then rerun source-evidence intake preview. Do not record evidence or mark a "
            "claim verified from this clue-only packet."
        ),
    }


def build_source_evidence_intake_batch_preview(
    previews: list[dict[str, Any]],
    *,
    candidate_count: int,
    source_mode: str,
) -> dict[str, Any]:
    """Roll up per-candidate source-evidence intake previews without writes."""
    ready_previews = [
        preview for preview in previews if preview.get("validation_status") == "ready_for_manual_evidence_preview"
    ]
    recommended_new_source_previews = [
        preview
        for preview in ready_previews
        if preview.get("support_status") == "supports"
        and _as_dict(preview.get("source_overlap_effect")).get("adds_new_supporting_source") is True
    ]
    duplicate_or_freshness_previews = [
        preview
        for preview in ready_previews
        if preview.get("support_status") == "supports"
        and _as_dict(preview.get("source_overlap_effect")).get("source_already_present") is True
    ]
    contradiction_previews = [
        preview for preview in ready_previews if preview.get("support_status") == "contradicts"
    ]
    recommended_scope = _recommended_recording_scope(
        recommended_new_source_previews=recommended_new_source_previews,
        duplicate_or_freshness_previews=duplicate_or_freshness_previews,
        contradiction_previews=contradiction_previews,
    )
    return {
        "run_type": "truth_source_evidence_intake_batch_preview",
        "source_mode": source_mode,
        "dry_run": True,
        "mutations_planned": 0,
        "allowed_execute": False,
        "recording_ready_status": "preview_ready_approval_required",
        "required_execute_flags_for_batch": MANUAL_EVIDENCE_BATCH_REQUIRED_EXECUTE_FLAGS,
        "candidate_count": candidate_count,
        "ready_for_manual_evidence_preview_count": len(ready_previews),
        "recording_ready_count": sum(1 for preview in previews if preview.get("recording_ready") is True),
        "new_supporting_source_ready_count": sum(
            1 for preview in ready_previews if _as_dict(preview.get("source_overlap_effect")).get("adds_new_supporting_source") is True
        ),
        "supporting_source_already_present_count": sum(
            1 for preview in ready_previews if _as_dict(preview.get("source_overlap_effect")).get("source_already_present") is True
        ),
        "contradiction_candidate_count": sum(1 for preview in previews if preview.get("support_status") == "contradicts"),
        "blocked_count": sum(
            1 for preview in previews if preview.get("validation_status") != "ready_for_manual_evidence_preview"
        ),
        "recommended_recording_scope": recommended_scope,
        "recording_approval_packet": _recording_approval_packet(recommended_scope),
        "manual_evidence_replay_boundary": {
            "explicit_approval_required": True,
            "payload_file_replay_command": MANUAL_EVIDENCE_BATCH_PREVIEW_COMMAND,
            "payload_file_preview_command": MANUAL_EVIDENCE_BATCH_PREVIEW_COMMAND,
            "payload_file_execute_command_after_approval": MANUAL_EVIDENCE_BATCH_EXECUTE_COMMAND,
            "required_execute_flags_for_batch": MANUAL_EVIDENCE_BATCH_REQUIRED_EXECUTE_FLAGS,
            "will_mark_verified": False,
            "will_refresh_sources": False,
            "will_materialize_relationships": False,
            "will_start_jobs": False,
            "will_allow_business_use": False,
            "post_recording_expectations": POST_RECORDING_EXPECTATIONS,
        },
        "previews": previews,
        "safe_action": (
            "Batch is read-only. Review each manual_evidence_preview, mutation scope, and rollback plan; "
            "batch replay records nothing without explicit approval and the required "
            "--execute --confirm-execute --confirm-batch-execute flags."
        ),
    }


def filter_source_evidence_batch_to_recommended_scope(batch: dict[str, Any]) -> dict[str, Any]:
    """Keep only new-supporting-source previews while preserving the all-candidate summary."""
    scope = _as_dict(batch.get("recommended_recording_scope"))
    recommended_previews = [
        preview
        for preview in _as_list(batch.get("previews"))
        if preview.get("validation_status") == "ready_for_manual_evidence_preview"
        and preview.get("support_status") == "supports"
        and _as_dict(preview.get("source_overlap_effect")).get("adds_new_supporting_source") is True
    ]
    filtered = {
        **batch,
        "source_mode": f"{batch.get('source_mode')}_recommended_scope_only",
        "allowed_execute": False,
        "recording_ready_status": "preview_ready_approval_required",
        "required_execute_flags_for_batch": MANUAL_EVIDENCE_BATCH_REQUIRED_EXECUTE_FLAGS,
        "original_candidate_count": batch.get("candidate_count"),
        "candidate_count": len(recommended_previews),
        "ready_for_manual_evidence_preview_count": sum(
            1 for preview in recommended_previews if preview.get("validation_status") == "ready_for_manual_evidence_preview"
        ),
        "recording_ready_count": sum(1 for preview in recommended_previews if preview.get("recording_ready") is True),
        "new_supporting_source_ready_count": len(recommended_previews),
        "supporting_source_already_present_count": 0,
        "contradiction_candidate_count": 0,
        "blocked_count": sum(
            1
            for preview in recommended_previews
            if preview.get("validation_status") != "ready_for_manual_evidence_preview"
        ),
        "previews": recommended_previews,
        "filtered_out_candidate_count": max(0, int(batch.get("candidate_count") or 0) - len(recommended_previews)),
        "safe_action": (
            "Recommended-scope preview is read-only and narrowed to candidates that add a new supporting source. "
            "Batch replay still requires explicit approval plus --execute --confirm-execute "
            "--confirm-batch-execute."
        ),
    }
    filtered["recommended_recording_scope"] = {
        **scope,
        "filtered_view": True,
        "filtered_candidate_count": len(recommended_previews),
    }
    filtered["recording_approval_packet"] = _recording_approval_packet(
        filtered["recommended_recording_scope"],
        filtered_view=True,
    )
    return filtered


def _preview_relationship_summary(preview: dict[str, Any]) -> dict[str, Any]:
    match = _as_dict(preview.get("relationship_match"))
    relationship = _as_dict(match.get("relationship"))
    effect = _as_dict(preview.get("source_overlap_effect"))
    manual_preview = _as_dict(preview.get("manual_evidence_preview"))
    return {
        "work_item_id": match.get("work_item_id"),
        "request_type": match.get("request_type"),
        "relationship_label": relationship.get("relationship_label"),
        "bbl": relationship.get("bbl"),
        "address": relationship.get("address"),
        "manager_name": relationship.get("manager_name"),
        "source_name": effect.get("source_name"),
        "effect_status": effect.get("effect_status"),
        "claim_id": _as_dict(manual_preview.get("claim_spec")).get("claim_id"),
        "evidence_id": _as_dict(manual_preview.get("claim_spec")).get("evidence_id"),
    }


def _manual_evidence_payload_review_row(preview: dict[str, Any], index: int) -> dict[str, Any]:
    match = _as_dict(preview.get("relationship_match"))
    payload = _as_dict(preview.get("manual_evidence_payload"))
    raw_payload = _as_dict(payload.get("raw_payload"))
    relationship = {
        **_as_dict(match.get("relationship")),
        **_as_dict(raw_payload.get("relationship")),
    }
    return {
        "payload_index": index,
        "subject_type": payload.get("subject_type"),
        "subject_id": payload.get("subject_id"),
        "predicate": payload.get("predicate"),
        "object_type": payload.get("object_type"),
        "object_id": payload.get("object_id"),
        "claim_type": payload.get("claim_type"),
        "normalized_value": payload.get("normalized_value"),
        "extracted_value": payload.get("extracted_value"),
        "support_status": payload.get("support_status"),
        "source_name": payload.get("source_name"),
        "source_type": payload.get("source_type"),
        "source_record_id": payload.get("source_record_id"),
        "source_url_present": bool(payload.get("source_url")),
        "observed_at": payload.get("observed_at"),
        "relationship_label": relationship.get("relationship_label"),
        "bbl": relationship.get("bbl"),
        "address": relationship.get("address"),
        "manager_name": relationship.get("manager_name"),
        "exact_property_match": raw_payload.get("exact_property_match"),
        "role_specific_management_support": raw_payload.get("role_specific_management_support"),
        "contradicts_current_claim": raw_payload.get("contradicts_current_claim"),
    }


def _manual_evidence_replay_payloads(previews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _as_dict(preview.get("manual_evidence_payload"))
        for preview in previews
        if _as_dict(preview.get("manual_evidence_payload"))
    ]


def _manual_evidence_payload_review_rows(previews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _manual_evidence_payload_review_row(preview, index)
        for index, preview in enumerate(previews, start=1)
    ]


def _recommended_recording_scope(
    *,
    recommended_new_source_previews: list[dict[str, Any]],
    duplicate_or_freshness_previews: list[dict[str, Any]],
    contradiction_previews: list[dict[str, Any]],
) -> dict[str, Any]:
    recommended_relationships = [
        _preview_relationship_summary(preview)
        for preview in recommended_new_source_previews
    ]
    duplicate_or_freshness_relationships = [
        _preview_relationship_summary(preview)
        for preview in duplicate_or_freshness_previews
    ]
    contradiction_relationships = [
        _preview_relationship_summary(preview)
        for preview in contradiction_previews
    ]
    manual_evidence_payloads = _manual_evidence_replay_payloads(recommended_new_source_previews)
    manual_evidence_payload_review = _manual_evidence_payload_review_rows(recommended_new_source_previews)
    expected_post_recording_source_overlap = _expected_post_recording_source_overlap(
        recommended_new_source_previews
    )
    return {
        "scope": "new_supporting_sources_only",
        "dry_run": True,
        "mutations_planned": 0,
        "explicit_approval_required": True,
        "required_execute_flags_for_batch": MANUAL_EVIDENCE_BATCH_REQUIRED_EXECUTE_FLAGS,
        "recommended_count": len(recommended_relationships),
        "recommended_relationships": recommended_relationships,
        "manual_evidence_payload_count": len(manual_evidence_payloads),
        "manual_evidence_payloads": manual_evidence_payloads,
        "manual_evidence_payload_review": manual_evidence_payload_review,
        "expected_post_recording_source_overlap": expected_post_recording_source_overlap,
        "duplicate_or_freshness_only_count": len(duplicate_or_freshness_relationships),
        "duplicate_or_freshness_only_relationships": duplicate_or_freshness_relationships,
        "contradiction_review_count": len(contradiction_relationships),
        "contradiction_relationships": contradiction_relationships,
        "expected_effect": (
            "If later approved and recorded, only the recommended relationships add a supporting source name "
            "that is not already present on the matched work item. Duplicate/freshness-only and contradiction "
            "items should be reviewed as separate scopes."
        ),
        "non_effects": {
            "will_mark_verified": False,
            "will_refresh_sources": False,
            "will_materialize_relationships": False,
            "will_start_jobs": False,
            "will_allow_business_use": False,
        },
        "post_recording_expectations": POST_RECORDING_EXPECTATIONS,
        "safe_action": (
            "Use this as an approval-boundary summary only. Even the recommended scope still requires explicit "
            "approval plus --execute --confirm-execute --confirm-batch-execute for batch replay, recording, "
            "and a later adjudication preview."
        ),
    }


def _recording_approval_packet(
    recommended_scope: dict[str, Any],
    *,
    filtered_view: bool = False,
) -> dict[str, Any]:
    recommended_relationships = _as_list(recommended_scope.get("recommended_relationships"))
    manual_evidence_payloads = _as_list(recommended_scope.get("manual_evidence_payloads"))
    manual_evidence_payload_review = _as_list(recommended_scope.get("manual_evidence_payload_review"))
    expected_post_recording_source_overlap = _as_dict(
        recommended_scope.get("expected_post_recording_source_overlap")
    )
    duplicate_or_freshness_relationships = _as_list(
        recommended_scope.get("duplicate_or_freshness_only_relationships")
    )
    contradiction_relationships = _as_list(recommended_scope.get("contradiction_relationships"))
    recommended_count = int(recommended_scope.get("recommended_count") or 0)
    excluded_count = len(duplicate_or_freshness_relationships) + len(contradiction_relationships)
    status = "preview_ready_approval_required" if recommended_count else "no_recommended_recording_scope"
    return {
        "status": status,
        "approval_required": True,
        "allowed_execute": False,
        "approval_scope": "new_supporting_sources_only",
        "filtered_view": filtered_view,
        "recommended_count": recommended_count,
        "recommended_relationships": recommended_relationships,
        "manual_evidence_payload_count": len(manual_evidence_payloads),
        "manual_evidence_payloads": manual_evidence_payloads,
        "manual_evidence_payload_review": manual_evidence_payload_review,
        "expected_post_recording_source_overlap": expected_post_recording_source_overlap,
        "excluded_count": excluded_count,
        "excluded_relationships": {
            "duplicate_or_freshness_only": duplicate_or_freshness_relationships,
            "contradiction_review": contradiction_relationships,
        },
        "approval_question": (
            f"Approve recording {recommended_count} preview-clean new-supporting-source "
            "manual-evidence row(s) only?"
        ),
        "preview_command": MANUAL_EVIDENCE_BATCH_PREVIEW_COMMAND,
        "execute_command_after_approval": MANUAL_EVIDENCE_BATCH_EXECUTE_COMMAND,
        "required_execute_flags_for_batch": MANUAL_EVIDENCE_BATCH_REQUIRED_EXECUTE_FLAGS,
        "mutation_scope": {
            "allowed_tables": [
                "truth_materialization_manifest",
                "truth_claims",
                "truth_evidence",
                "confidence_snapshots",
            ],
            "forbidden_side_effects": {
                "will_mark_verified": False,
                "will_refresh_sources": False,
                "will_materialize_relationships": False,
                "will_start_jobs": False,
                "will_allow_business_use": False,
            },
        },
        "post_recording_expectations": POST_RECORDING_EXPECTATIONS,
        "safe_action": (
            "Use this packet as the human approval boundary. A later execute command may only record the listed "
            "new-supporting-source manual evidence rows, then must rerun adjudication, post-recording proof, "
            "truth health, and runtime audit before any verification or business-use decision."
        ),
    }


def _candidate_source_name(payload: dict[str, Any]) -> str:
    source_name = _clean_text(payload.get("source_name"))
    source_family = _clean_text(payload.get("source_family"))
    return source_name or source_family


def _source_overlap_effect(
    *,
    source_name: str,
    support_status: str,
    matched_item: dict[str, Any] | None,
) -> dict[str, Any]:
    current_sources = sorted({
        str(source).strip()
        for source in _as_list(_as_dict(matched_item).get("current_sources"))
        if str(source or "").strip()
    })
    current_supporting_source_count = len(current_sources)
    source_already_present = bool(source_name and source_name in current_sources)
    adds_new_supporting_source = bool(
        support_status == "supports"
        and source_name
        and not source_already_present
    )
    projected_supporting_source_count = current_supporting_source_count + (
        1 if adds_new_supporting_source else 0
    )
    would_be_multi_source_after_recording = (
        support_status == "supports" and projected_supporting_source_count >= 2
    )
    would_be_first_source_only_after_recording = (
        support_status == "supports"
        and adds_new_supporting_source
        and projected_supporting_source_count == 1
    )
    if support_status == "contradicts":
        effect_status = "contradiction_review_evidence"
        expected_overlap_effect_status = "contradiction_review_evidence"
        safe_action = (
            "This candidate is contradiction evidence. It may be useful for review, but it does not add a "
            "supporting source for verification."
        )
    elif adds_new_supporting_source:
        effect_status = "adds_new_supporting_source"
        if would_be_first_source_only_after_recording:
            expected_overlap_effect_status = "adds_first_supporting_source_only"
            safe_action = (
                "This candidate would create the first supporting source for this relationship, but it would not "
                "create independent source overlap by itself. A second independent exact-property source is still "
                "needed before the fact can be source-ready."
            )
        else:
            expected_overlap_effect_status = "adds_independent_source_overlap"
            safe_action = (
                "This candidate would add a supporting source name that is not already present on the matched work "
                "item, subject to explicit recording approval and later adjudication."
            )
    else:
        effect_status = "source_already_present"
        expected_overlap_effect_status = "duplicate_or_freshness_only"
        safe_action = (
            "This candidate may add a new evidence row or fresher observation, but it does not add a new independent "
            "supporting source name for source-overlap purposes."
        )
    return {
        "source_name": source_name or None,
        "current_sources": current_sources,
        "current_supporting_source_count": current_supporting_source_count,
        "projected_supporting_source_count": projected_supporting_source_count,
        "projected_new_supporting_source_count_delta": 1 if adds_new_supporting_source else 0,
        "source_already_present": source_already_present,
        "adds_new_supporting_source": adds_new_supporting_source,
        "effect_status": effect_status,
        "expected_overlap_effect_status": expected_overlap_effect_status,
        "would_be_first_source_only_after_recording": would_be_first_source_only_after_recording,
        "would_be_multi_source_after_recording": would_be_multi_source_after_recording,
        "would_be_source_ready_after_recording": would_be_multi_source_after_recording,
        "safe_action": safe_action,
    }


def _expected_post_recording_source_overlap(previews: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for preview in previews:
        effect = _as_dict(preview.get("source_overlap_effect"))
        relationship = _preview_relationship_summary(preview)
        current_count = int(effect.get("current_supporting_source_count") or 0)
        projected_count = int(effect.get("projected_supporting_source_count") or current_count)
        row = {
            "relationship_label": relationship.get("relationship_label"),
            "bbl": relationship.get("bbl"),
            "address": relationship.get("address"),
            "manager_name": relationship.get("manager_name"),
            "source_name": effect.get("source_name"),
            "current_supporting_source_count": current_count,
            "projected_supporting_source_count": projected_count,
            "projected_new_supporting_source_count_delta": int(
                effect.get("projected_new_supporting_source_count_delta") or 0
            ),
            "expected_overlap_effect_status": effect.get("expected_overlap_effect_status"),
            "would_be_first_source_only_after_recording": bool(
                effect.get("would_be_first_source_only_after_recording")
            ),
            "would_be_multi_source_after_recording": bool(
                effect.get("would_be_multi_source_after_recording")
            ),
            "would_be_source_ready_after_recording": bool(
                effect.get("would_be_source_ready_after_recording")
            ),
        }
        rows.append(row)

    first_source_only_count = sum(
        1 for row in rows if row["would_be_first_source_only_after_recording"]
    )
    multi_source_after_recording_count = sum(
        1 for row in rows if row["would_be_multi_source_after_recording"]
    )
    source_ready_after_recording_count = sum(
        1 for row in rows if row["would_be_source_ready_after_recording"]
    )
    new_source_delta = sum(
        int(row["projected_new_supporting_source_count_delta"]) for row in rows
    )
    if rows and source_ready_after_recording_count == 0:
        safe_action = (
            "The recommended recording scope would add evidence, but none of the listed rows would become "
            "multi-source/source-ready immediately after recording. Treat it as first-source capture unless a "
            "separate independent source is also acquired and previewed."
        )
    elif source_ready_after_recording_count:
        safe_action = (
            "Some recommended rows would become multi-source/source-ready after recording, but verification still "
            "requires adjudication, freshness checks, no contradictions, and the verified confidence threshold."
        )
    else:
        safe_action = "No recommended recording rows are present."

    return {
        "recommended_row_count": len(rows),
        "new_supporting_source_count_delta": new_source_delta,
        "first_source_only_after_recording_count": first_source_only_count,
        "multi_source_after_recording_count": multi_source_after_recording_count,
        "source_ready_after_recording_count": source_ready_after_recording_count,
        "rows": rows,
        "safe_action": safe_action,
    }


def _match_work_item_score(payload: dict[str, Any], item: dict[str, Any]) -> int:
    relationship = _as_dict(item.get("relationship"))
    score = 0
    if _normalize_for_match(payload.get("relationship_label")) and (
        _normalize_for_match(payload.get("relationship_label"))
        == _normalize_for_match(relationship.get("relationship_label"))
    ):
        score += 5
    if _clean_text(payload.get("bbl")) and _clean_text(payload.get("bbl")) == _clean_text(relationship.get("bbl")):
        score += 4
    if _normalize_for_match(payload.get("address")) and (
        _normalize_for_match(payload.get("address")) == _normalize_for_match(relationship.get("address"))
    ):
        score += 2
    if _normalize_for_match(payload.get("manager_name")) and (
        _normalize_for_match(payload.get("manager_name")) == _normalize_for_match(relationship.get("manager_name"))
    ):
        score += 2
    return score


def find_matching_source_work_item(
    paste_back: dict[str, Any],
    worklist: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Find the current source-acquisition work item that a paste-back belongs to."""
    best_item: dict[str, Any] | None = None
    best_score = 0
    for item in _as_list(_as_dict(worklist).get("work_items")):
        if not isinstance(item, dict):
            continue
        score = _match_work_item_score(paste_back, item)
        if score > best_score:
            best_item = item
            best_score = score
    return best_item if best_score >= 4 else None


def _relationship_context(
    paste_back: dict[str, Any],
    matched_item: dict[str, Any] | None,
) -> dict[str, Any]:
    matched_relationship = _as_dict(_as_dict(matched_item).get("relationship"))
    return {
        "relationship_label": _clean_text(
            paste_back.get("relationship_label") or matched_relationship.get("relationship_label")
        ),
        "bbl": _clean_text(paste_back.get("bbl") or matched_relationship.get("bbl")),
        "address": _clean_text(paste_back.get("address") or matched_relationship.get("address")),
        "manager_name": _clean_text(paste_back.get("manager_name") or matched_relationship.get("manager_name")),
        "manager_lead_id": _clean_text(
            paste_back.get("manager_lead_id") or matched_relationship.get("manager_lead_id")
        ),
    }


def _source_url(value: Any) -> str | None:
    text = _clean_text(value)
    if text.lower().startswith(("http://", "https://")):
        return text
    return None


def _missing_preview_fields(paste_back: dict[str, Any], *, support_status: str) -> list[str]:
    missing = [
        field
        for field in REQUIRED_BEFORE_MANUAL_PREVIEW
        if field != "source_name"
        and not _clean_text(paste_back.get(field))
        and _boolish(paste_back.get(field)) is None
    ]
    if not _candidate_source_name(paste_back):
        missing.append("source_name")
    if _boolish(paste_back.get("exact_property_match")) is not True and "exact_property_match" not in missing:
        missing.append("exact_property_match=true")
    if support_status == "supports":
        if _boolish(paste_back.get(ROLE_SUPPORT_REQUIRED_FOR_SUPPORTING_EVIDENCE)) is not True:
            missing.append(f"{ROLE_SUPPORT_REQUIRED_FOR_SUPPORTING_EVIDENCE}=true")
    else:
        if _boolish(paste_back.get(CONTRADICTION_REQUIRED_FOR_CONTRADICTING_EVIDENCE)) is not True:
            missing.append(f"{CONTRADICTION_REQUIRED_FOR_CONTRADICTING_EVIDENCE}=true")
    return missing


def build_manual_evidence_payload_from_source_intake(
    paste_back: dict[str, Any],
    *,
    matched_item: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert a validated source-acquisition paste-back into manual-evidence payload shape."""
    relationship = _relationship_context(paste_back, matched_item)
    source_reference = _clean_text(paste_back.get("source_url_or_local_record_reference"))
    source_name = _candidate_source_name(paste_back)
    contradicts = _boolish(paste_back.get("contradicts_current_claim")) is True
    support_status = "contradicts" if contradicts else "supports"
    source_excerpt = _clean_text(paste_back.get("source_excerpt_or_row_summary"))
    notes = _clean_text(paste_back.get("notes"))
    note_parts = [part for part in [source_excerpt, notes] if part]
    return {
        "subject_type": "lead",
        "subject_id": relationship["manager_lead_id"],
        "predicate": "manages_building",
        "object_type": "building",
        "object_id": relationship["bbl"],
        "claim_type": "building_management",
        "normalized_value": "manager",
        "extracted_value": relationship["relationship_label"] or None,
        "support_status": support_status,
        "source_name": source_name,
        "source_type": _clean_text(paste_back.get("source_family")) or source_name,
        "source_record_id": _clean_text(paste_back.get("source_record_id")),
        "source_url": _source_url(source_reference),
        "observed_at": _clean_text(paste_back.get("observed_at")) or None,
        "note": " | ".join(note_parts) or None,
        "raw_payload": {
            "source_acquisition_intake": True,
            "relationship": relationship,
            "source_family": _clean_text(paste_back.get("source_family")) or None,
            "source_url_or_local_record_reference": source_reference or None,
            "exact_property_match": _boolish(paste_back.get("exact_property_match")),
            "role_specific_management_support": _boolish(paste_back.get("role_specific_management_support")),
            "contradicts_current_claim": contradicts,
        },
    }


def build_source_evidence_intake_preview(
    paste_back: dict[str, Any],
    *,
    worklist: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a filled source-acquisition template before manual-evidence preview."""
    matched_item = find_matching_source_work_item(paste_back, worklist)
    relationship = _relationship_context(paste_back, matched_item)
    source_name = _candidate_source_name(paste_back)
    support_status = "contradicts" if _boolish(paste_back.get("contradicts_current_claim")) is True else "supports"
    source_overlap_effect = _source_overlap_effect(
        source_name=source_name,
        support_status=support_status,
        matched_item=matched_item,
    )

    blockers: list[str] = []
    if not matched_item and not relationship.get("manager_lead_id"):
        blockers.append("relationship_not_matched_to_current_worklist")
    if not relationship.get("bbl"):
        blockers.append("missing_bbl")
    if not relationship.get("manager_name"):
        blockers.append("missing_manager_name")
    if not source_name:
        blockers.append("missing_source_name")
    elif source_name not in ALLOWED_MANUAL_SOURCE_NAMES:
        blockers.append(f"unsupported_source_name:{source_name}")
    missing_fields = _missing_preview_fields(paste_back, support_status=support_status)
    blockers.extend(f"missing_or_invalid_{field}" for field in missing_fields)

    manual_payload = build_manual_evidence_payload_from_source_intake(
        paste_back,
        matched_item=matched_item,
    )
    validation_status = "ready_for_manual_evidence_preview" if not blockers else "blocked_before_manual_evidence_preview"
    return {
        "run_type": "truth_source_evidence_intake_preview",
        "dry_run": True,
        "mutations_planned": 0,
        "validation_status": validation_status,
        "recording_ready": False,
        "approval_required_before_recording": True,
        "relationship_match": {
            "status": "matched_current_work_item" if matched_item else "unmatched",
            "work_item_id": _as_dict(matched_item).get("work_item_id"),
            "request_type": _as_dict(matched_item).get("request_type"),
            "relationship": relationship,
        },
        "required_paste_back_fields": REQUIRED_PASTE_BACK_FIELDS,
        "required_before_manual_preview": REQUIRED_BEFORE_MANUAL_PREVIEW,
        "blocking_reasons": blockers,
        "support_status": support_status,
        "source_overlap_effect": source_overlap_effect,
        "manual_evidence_payload": manual_payload,
        "safe_action": (
            "This intake is read-only. If validation and manual-evidence preview are clean, "
            "recording still requires explicit approval and the matching manual-evidence execute flags; "
            "contradictions must route to review instead of overwriting current claims."
        ),
    }
