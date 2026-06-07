"""Data Truth & Confidence APIs."""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from scripts.truth_completion_audit import build_artifact_checklist, build_completion_audit
from scripts.truth_manager_external_evidence_batch import build_manager_source_acquisition_packet
from scripts.truth_operator_confirmed_evidence_batch import build_operator_source_acquisition_packet
from scripts.truth_source_acquisition_worklist import (
    build_source_acquisition_csv_template,
    build_source_acquisition_hpd_fetch_packet,
    build_source_acquisition_operator_confirmation_packet,
    build_source_acquisition_worklist,
)
from scripts.truth_source_overlap_blocker_report import build_source_overlap_blocker_report
from scripts.truth_source_overlap_approval_packet import build_source_overlap_approval_packet_for_session
from scripts.truth_source_overlap_post_recording_check import build_post_recording_check, load_verified_single_source_summary
from scripts.truth_verification_frontier import build_truth_verification_frontier, load_frontier_display_context
from src.auth.auth import AuthUser, get_current_user
from src.db.session import get_session
from src.services.golden_benchmark import evaluate_golden_cases, load_golden_benchmark
from src.services.manual_evidence import preview_or_record_manual_evidence
from src.services.source_evidence_intake import (
    build_source_acquisition_clue_only_preview,
    build_source_evidence_intake_batch_preview,
    build_source_evidence_intake_preview,
    extract_source_acquisition_clues,
    extract_source_evidence_intake_candidates,
    filter_source_evidence_batch_to_recommended_scope,
)
from src.services.truth_adjudication import (
    load_claim_adjudication_preview,
    load_ledger_source_overlap_summary,
    load_manager_external_source_acquisition_preview,
    load_operator_confirmed_management_preview,
    preview_or_apply_claim_adjudication,
    preview_or_apply_role_claim_corrections,
)
from src.services.truth_activation import build_activation_packet, build_runtime_preflight_summary
from src.services.truth_materialization import materialize_truth_claims
from src.services.truth_health import ACTIONABILITY_RULES, build_schema_readiness_report, build_truth_health_report, is_truth_schema_current, load_truth_dashboard, load_truth_schema_status
from src.services.truth_program import GOLDEN_CASE_SEEDS, build_lead_truth_summary, build_subject_truth_summary, preview_adversarial_validation
from src.services.truth_review import apply_review_decision

router = APIRouter(prefix="/api/v1/truth", tags=["truth-confidence"])
limiter = Limiter(key_func=get_remote_address)
ALLOWED_SUBJECT_TYPES = {"lead", "canonical_entity", "entity", "building", "contact", "hpd_contact", "person"}


class ReviewDecisionRequest(BaseModel):
    decision: str = Field(..., description="approve, reject, needs_more_evidence, or do_not_merge")
    note: str | None = Field(None, max_length=2000)
    dry_run: bool = True
    confirm_execute: bool = False


class ManualEvidenceRequest(BaseModel):
    subject_type: str = Field(..., max_length=30)
    subject_id: str = Field(..., max_length=80)
    predicate: str = Field(..., max_length=80)
    object_type: str = Field(..., max_length=30)
    object_id: str = Field(..., max_length=80)
    claim_type: str = Field(..., max_length=50)
    normalized_value: str | None = Field(None, max_length=2000)
    extracted_value: str | None = Field(None, max_length=2000)
    support_status: str = Field("supports", description="supports or contradicts")
    source_name: str = Field("manual_evidence", description="manual_evidence, operator_review, company_website, google_places, ny_dos, or outreach_confirmed")
    source_type: str = Field("operator_review", max_length=40)
    source_record_id: str | None = Field(None, max_length=120)
    source_url: str | None = Field(None, max_length=4000)
    observed_at: str | None = None
    note: str | None = Field(None, max_length=2000)
    raw_payload: dict[str, Any] | None = None
    dry_run: bool = True
    confirm_execute: bool = False
    run_id: str | None = Field(None, max_length=80)


class SourceEvidenceIntakeRequest(BaseModel):
    relationship_label: str | None = Field(None, max_length=300)
    bbl: str | None = Field(None, max_length=20)
    address: str | None = Field(None, max_length=300)
    manager_name: str | None = Field(None, max_length=300)
    manager_lead_id: str | None = Field(None, max_length=80)
    source_family: str | None = Field(None, max_length=80)
    source_name: str | None = Field(None, max_length=80)
    source_url_or_local_record_reference: str | None = Field(None, max_length=4000)
    source_record_id: str | None = Field(None, max_length=300)
    observed_at: str | None = Field(None, max_length=80)
    exact_property_match: bool | str | None = None
    role_specific_management_support: bool | str | None = None
    source_excerpt_or_row_summary: str | None = Field(None, max_length=4000)
    contradicts_current_claim: bool | str | None = None
    notes: str | None = Field(None, max_length=4000)


class SourceEvidenceIntakeBatchRequest(BaseModel):
    candidates: list[SourceEvidenceIntakeRequest] | None = Field(
        None,
        max_length=100,
        description="Filled paste-back candidates, including HPD audit source_evidence_intake_candidates.",
    )
    hpd_audit_output: dict[str, Any] | list[dict[str, Any]] | None = Field(
        None,
        description="Optional truth_live_hpd_role_audit.py output containing source_evidence_intake_candidates.",
    )
    source_mode: str | None = Field(None, max_length=80)
    recommended_scope_only: bool = Field(
        False,
        description="When true, return only candidates that add a new supporting source. Still read-only.",
    )


class SourceOverlapBlockerReportPreviewRequest(SourceEvidenceIntakeBatchRequest):
    """Optional filled candidates to include in the read-only blocker report."""


class ClaimAdjudicationApplyRequest(BaseModel):
    limit: int = Field(100, ge=1, le=1000)
    dry_run: bool = True
    confirm_execute: bool = False
    run_id: str | None = Field(None, max_length=80)


class RoleClaimCorrectionRequest(BaseModel):
    lead_id: str = Field("0ff794d3ba2d", max_length=80)
    limit: int = Field(100, ge=1, le=1000)
    dry_run: bool = True
    confirm_execute: bool = False
    run_id: str | None = Field(None, max_length=80)


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _schema_not_ready_review_queue(schema_status: dict[str, Any], *, limit: int, offset: int) -> dict[str, Any]:
    return {
        "items": [],
        "limit": limit,
        "offset": offset,
        "source": "schema_not_ready",
        "schema_status": schema_status,
    }


def _schema_not_ready_dashboard(schema_status: dict[str, Any]) -> dict[str, Any]:
    return {
        "claim_count": 0,
        "verified_claim_count": 0,
        "conflicting_claim_count": 0,
        "recommended_outreach_claim_count": 0,
        "open_review_count": 0,
        "active_golden_case_count": 0,
        "confidence_snapshot_count": 0,
        "actionability_distribution": {},
        "review_queue_distribution": {},
        "claim_type_distribution": {},
        "actionability_rules": ACTIONABILITY_RULES,
        "schema_status": schema_status,
    }


def _schema_not_ready_adjudication(schema_status: dict[str, Any], *, limit: int) -> dict[str, Any]:
    return {
        "generated_at": None,
        "dry_run": True,
        "mutations_planned": 0,
        "limit": limit,
        "fact_group_count": 0,
        "verification_candidate_count": 0,
        "status_counts": {},
        "recommended_queue_counts": {},
        "blocker_counts": {},
        "samples": [],
        "schema_status": schema_status,
        "blocked_reason": "Truth claim adjudication requires the additive truth-confidence schema at the expected migration.",
    }


def _schema_not_ready_decision(review_id: str, decision: str, schema_status: dict[str, Any]) -> dict[str, Any]:
    return {
        "dry_run": True,
        "review_id": review_id,
        "decision": decision,
        "current_status": None,
        "target_status": None,
        "queue_name": "schema_not_ready",
        "subject_type": None,
        "subject_id": None,
        "allowed_execute": False,
        "blocked_reason": "Truth review schema is not ready; apply the additive truth-confidence migration before previewing or executing review decisions.",
        "proposed_database_changes": [],
        "rollback_strategy": "No mutation was attempted because the truth review schema is not ready.",
        "schema_status": schema_status,
    }


def _schema_not_ready_manual_evidence(schema_status: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_type": "manual_evidence_capture",
        "dry_run": True,
        "mutations_planned": 0,
        "allowed_execute": False,
        "blocked_reason": "Manual evidence capture requires the additive truth-confidence schema at the expected migration.",
        "schema_status": schema_status,
    }


def _schema_not_ready_adjudication_apply(schema_status: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_type": "truth_claim_adjudication",
        "dry_run": True,
        "mutations_planned": 0,
        "allowed_execute": False,
        "blocked_reason": "Claim adjudication execution requires the additive truth-confidence schema at the expected migration.",
        "schema_status": schema_status,
    }


def _schema_not_ready_role_correction(schema_status: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_type": "truth_role_claim_correction",
        "dry_run": True,
        "mutations_planned": 0,
        "allowed_execute": False,
        "blocked_reason": "Role-claim correction requires the additive truth-confidence schema at the expected migration.",
        "schema_status": schema_status,
    }


def _attach_source_acquisition_csv_handoff(
    worklist: dict[str, Any],
    *,
    lead_id: str,
    frontier_limit: int,
    max_items: int,
) -> dict[str, Any]:
    worklist["csv_template"] = build_source_acquisition_csv_template(worklist)
    worklist["hpd_fetch_packet"] = build_source_acquisition_hpd_fetch_packet(worklist)
    worklist["operator_confirmation_packet"] = build_source_acquisition_operator_confirmation_packet(worklist)
    worklist["csv_template_command"] = (
        f"truth_source_acquisition_worklist.py --lead-id {lead_id} "
        f"--frontier-limit {frontier_limit} --max-items {max_items} --csv-template"
    )
    worklist["hpd_fetch_packet_command"] = (
        f"truth_source_acquisition_worklist.py --lead-id {lead_id} "
        f"--frontier-limit {frontier_limit} --max-items {max_items} --hpd-fetch-packet"
    )
    worklist["operator_confirmation_packet_command"] = (
        f"truth_source_acquisition_worklist.py --lead-id {lead_id} "
        f"--frontier-limit {frontier_limit} --max-items {max_items} --operator-confirmation-packet"
    )
    worklist["candidate_csv_preview_command"] = (
        "truth_source_evidence_intake.py --candidate-csv <filled-worklist.csv> "
        "--recommended-scope-only --indent 2"
    )
    return worklist


async def _build_source_intake_worklist(
    session: AsyncSession,
    *,
    lead_id: str,
    frontier_limit: int,
    max_items: int,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    schema_status = await load_truth_schema_status(session)
    if not is_truth_schema_current(schema_status):
        return schema_status, None
    adjudication_preview = await load_claim_adjudication_preview(
        session,
        limit=max(frontier_limit, 10),
        include_samples=False,
    )
    manager_preview = await load_manager_external_source_acquisition_preview(
        session,
        lead_id=lead_id,
        limit=50,
    )
    operator_preview = await load_operator_confirmed_management_preview(session, limit=50)
    display_context = await load_frontier_display_context(session, adjudication_preview)
    frontier = build_truth_verification_frontier(
        adjudication_preview=adjudication_preview,
        manager_source_packet=build_manager_source_acquisition_packet(
            manager_preview,
            lead_id=lead_id,
            run_id=f"truth-manager-source-acquisition-api-{lead_id}",
        ),
        operator_source_packet=build_operator_source_acquisition_packet(
            operator_preview,
            run_id="truth-operator-source-acquisition-api",
        ),
        display_context=display_context,
        limit=frontier_limit,
    )
    worklist = build_source_acquisition_worklist(frontier, max_items=max_items)
    return schema_status, _attach_source_acquisition_csv_handoff(
        worklist,
        lead_id=lead_id,
        frontier_limit=frontier_limit,
        max_items=max_items,
    )


async def _preview_source_evidence_intake_batch_request(
    body: SourceEvidenceIntakeBatchRequest,
    *,
    worklist: dict[str, Any],
    schema_status: dict[str, Any],
    session: AsyncSession,
    recorded_by: str,
) -> dict[str, Any]:
    payloads = [candidate.model_dump() for candidate in body.candidates or []]
    extracted_hpd_candidates = extract_source_evidence_intake_candidates(body.hpd_audit_output)
    extracted_clues = extract_source_acquisition_clues(body.hpd_audit_output)
    payloads.extend(extracted_hpd_candidates)
    source_mode = body.source_mode or (
        "mixed" if body.candidates and extracted_hpd_candidates
        else "hpd_audit_output" if extracted_hpd_candidates
        else "source_acquisition_clues" if extracted_clues
        else "candidate_list"
    )

    if not payloads and extracted_clues:
        preview = build_source_acquisition_clue_only_preview(
            extracted_clues,
            source_mode=source_mode,
        )
        preview["schema_status"] = schema_status
        preview["worklist_context"] = {
            "request_count": worklist.get("request_count"),
            "work_item_count": worklist.get("work_item_count"),
            "recording_ready_count": worklist.get("recording_ready_count"),
            "approval_required_count": worklist.get("approval_required_count"),
        }
        return preview

    previews: list[dict[str, Any]] = []
    for payload in payloads:
        intake = build_source_evidence_intake_preview(payload, worklist=worklist)
        intake["schema_status"] = schema_status
        intake["worklist_context"] = {
            "request_count": worklist.get("request_count"),
            "work_item_count": worklist.get("work_item_count"),
            "recording_ready_count": worklist.get("recording_ready_count"),
            "approval_required_count": worklist.get("approval_required_count"),
        }
        if intake["validation_status"] == "ready_for_manual_evidence_preview":
            preview = await preview_or_record_manual_evidence(
                session,
                payload=intake["manual_evidence_payload"],
                recorded_by=recorded_by,
                dry_run=True,
                confirm_execute=False,
            )
            intake["manual_evidence_preview"] = preview
            intake["recording_ready"] = (
                preview.get("run_type") == "manual_evidence_capture" and preview.get("dry_run") is True
            )
            intake["next_required_action"] = (
                "Review the manual_evidence_preview, mutation scope, and rollback plan. "
                "Record only after explicit dry_run=false / confirm_execute=true approval."
            )
        previews.append(intake)

    batch = build_source_evidence_intake_batch_preview(
        previews,
        candidate_count=len(payloads),
        source_mode=source_mode,
    )
    if body.recommended_scope_only:
        batch = filter_source_evidence_batch_to_recommended_scope(batch)
    if extracted_clues:
        batch["source_acquisition_clue_count"] = len(extracted_clues)
        batch["source_acquisition_clues"] = extracted_clues
        batch["source_clue_safe_action"] = (
            "Source-acquisition clues are not evidence candidates. Inspect the cited primary source, "
            "then rerun preview with exact-property role-specific source evidence."
        )
    batch["schema_status"] = schema_status
    batch["worklist_context"] = {
        "request_count": worklist.get("request_count"),
        "work_item_count": worklist.get("work_item_count"),
        "recording_ready_count": worklist.get("recording_ready_count"),
        "approval_required_count": worklist.get("approval_required_count"),
    }
    if not payloads:
        batch["blocked_count"] = 0
        batch["blocking_reasons"] = ["no_candidates_supplied"]
        batch["safe_action"] = (
            "Batch preview is read-only. Supply candidates or HPD audit output before manual-evidence preview; "
            "record nothing without explicit dry_run=false / confirm_execute=true approval."
        )
    return batch


@router.get("/dashboard")
async def truth_dashboard(
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    del user
    schema_status = await load_truth_schema_status(session)
    if not is_truth_schema_current(schema_status):
        return _schema_not_ready_dashboard(schema_status)
    return await load_truth_dashboard(session)


@router.get("/health-report")
async def truth_health_report(
    materialization_limit: int = Query(500, ge=1, le=5000),
    validation_sample_limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    del user
    return await build_truth_health_report(
        session,
        materialization_limit=materialization_limit,
        validation_sample_limit=validation_sample_limit,
    )


@router.get("/schema-status")
async def truth_schema_status(
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    del user
    return await load_truth_schema_status(session)


@router.get("/activation-packet")
async def truth_activation_packet(
    materialization_limit: int = Query(500, ge=1, le=5000),
    validation_sample_limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    del user
    schema_status = await load_truth_schema_status(session)
    health_report = await build_truth_health_report(
        session,
        materialization_limit=materialization_limit,
        validation_sample_limit=validation_sample_limit,
    )
    return build_activation_packet(
        preflight=build_runtime_preflight_summary(schema_status),
        health_report=health_report,
    )


@router.get("/completion-audit")
async def truth_completion_audit(
    materialization_limit: int = Query(500, ge=1, le=5000),
    validation_sample_limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    del user
    schema_status = await load_truth_schema_status(session)
    health_report = await build_truth_health_report(
        session,
        materialization_limit=materialization_limit,
        validation_sample_limit=validation_sample_limit,
    )
    activation_packet = build_activation_packet(
        preflight=build_runtime_preflight_summary(schema_status),
        health_report=health_report,
    )
    audit = build_completion_audit(
        artifact_checklist=build_artifact_checklist(),
        health_report=health_report,
        activation_packet=activation_packet,
        production_probe=None,
    )
    audit["production_probe_included"] = False
    audit["production_probe_note"] = (
        "API completion-audit preview does not probe production; use "
        "scripts/truth_completion_audit.py --include-runtime --include-production for the production gate."
    )
    return audit


@router.get("/source-overlap-approval-packet")
async def truth_source_overlap_approval_packet(
    lead_id: str = Query("0ff794d3ba2d", max_length=80),
    recorded_by: str = Query("operator", max_length=80),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    del user
    return await build_source_overlap_approval_packet_for_session(
        session,
        lead_id=lead_id,
        recorded_by=recorded_by,
    )


@router.get("/source-overlap-post-recording-check")
async def truth_source_overlap_post_recording_check(
    min_multi_source: int = Query(1, ge=1, le=1000),
    min_source_ready: int = Query(1, ge=1, le=1000),
    sample_limit: int = Query(5, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    del user
    schema_status = await load_truth_schema_status(session)
    if not is_truth_schema_current(schema_status):
        readiness = build_schema_readiness_report(schema_status=schema_status)
        readiness["run_type"] = "truth_source_overlap_post_recording_check"
        readiness["dry_run"] = True
        readiness["mutations_planned"] = 0
        readiness["post_recording_success"] = False
        readiness["thresholds"] = {
            "min_multi_source_fact_groups": min_multi_source,
            "min_source_ready_fact_groups": min_source_ready,
            "max_verified_single_source_claims": 0,
        }
        readiness["current_ledger"] = {
            "total_fact_group_count": 0,
            "single_source_fact_group_count": 0,
            "multi_source_fact_group_count": 0,
            "source_ready_fact_group_count": 0,
            "max_supporting_source_count": 0,
            "max_supporting_evidence_count": 0,
        }
        readiness["verified_single_source_policy"] = {
            "verified_claim_count": 0,
            "verified_single_source_claim_count": 0,
            "sample_limit": sample_limit,
            "samples": [],
        }
        readiness["checks"] = [{
            "check": "truth_schema_current",
            "status": "fail",
            "observed": 0,
            "minimum": 1,
            "reason": "Post-recording source-overlap proof requires the additive truth-confidence schema at the expected migration.",
        }]
        readiness["safe_action"] = "Apply or repair the additive truth-confidence schema before checking ledger source overlap."
        return readiness
    ledger_source_overlap = await load_ledger_source_overlap_summary(session)
    verified_single_source_summary = await load_verified_single_source_summary(
        session,
        min_sources=2,
        sample_limit=sample_limit,
    )
    result = build_post_recording_check(
        ledger_source_overlap=ledger_source_overlap,
        verified_single_source_summary=verified_single_source_summary,
        min_multi_source=min_multi_source,
        min_source_ready=min_source_ready,
    )
    result["schema_status"] = schema_status
    await session.rollback()
    return result


@router.get("/manager-source-acquisition-packet")
async def truth_manager_source_acquisition_packet(
    lead_id: str = Query("0ff794d3ba2d", max_length=80),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    del user
    schema_status = await load_truth_schema_status(session)
    if not is_truth_schema_current(schema_status):
        return build_schema_readiness_report(schema_status=schema_status)
    acquisition_preview = await load_manager_external_source_acquisition_preview(
        session,
        lead_id=lead_id,
        limit=50,
    )
    packet = build_manager_source_acquisition_packet(
        acquisition_preview,
        lead_id=lead_id,
        run_id=f"truth-manager-source-acquisition-api-{lead_id}",
    )
    await session.rollback()
    return packet


@router.get("/verification-frontier")
async def truth_verification_frontier(
    lead_id: str = Query("0ff794d3ba2d", max_length=80),
    limit: int = Query(10, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    del user
    schema_status = await load_truth_schema_status(session)
    if not is_truth_schema_current(schema_status):
        readiness = build_schema_readiness_report(schema_status=schema_status)
        readiness["run_type"] = "truth_verification_frontier"
        readiness["dry_run"] = True
        readiness["mutations_planned"] = 0
        readiness["blocked_reason"] = "Verification frontier requires the truth-confidence schema."
        return readiness
    adjudication_preview = await load_claim_adjudication_preview(
        session,
        limit=max(limit, 10),
        include_samples=False,
    )
    manager_preview = await load_manager_external_source_acquisition_preview(
        session,
        lead_id=lead_id,
        limit=50,
    )
    operator_preview = await load_operator_confirmed_management_preview(session, limit=50)
    display_context = await load_frontier_display_context(session, adjudication_preview)
    frontier = build_truth_verification_frontier(
        adjudication_preview=adjudication_preview,
        manager_source_packet=build_manager_source_acquisition_packet(
            manager_preview,
            lead_id=lead_id,
            run_id=f"truth-manager-source-acquisition-api-{lead_id}",
        ),
        operator_source_packet=build_operator_source_acquisition_packet(
            operator_preview,
            run_id="truth-operator-source-acquisition-api",
        ),
        display_context=display_context,
        limit=limit,
    )
    await session.rollback()
    return frontier


@router.get("/source-acquisition-worklist")
async def truth_source_acquisition_worklist(
    lead_id: str = Query("0ff794d3ba2d", max_length=80),
    frontier_limit: int = Query(10, ge=1, le=50),
    max_items: int = Query(10, ge=1, le=25),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    del user
    schema_status = await load_truth_schema_status(session)
    if not is_truth_schema_current(schema_status):
        readiness = build_schema_readiness_report(schema_status=schema_status)
        readiness["run_type"] = "truth_source_acquisition_worklist"
        readiness["dry_run"] = True
        readiness["mutations_planned"] = 0
        readiness["blocked_reason"] = "Source-acquisition worklist requires the truth-confidence schema."
        return readiness
    adjudication_preview = await load_claim_adjudication_preview(
        session,
        limit=max(frontier_limit, 10),
        include_samples=False,
    )
    manager_preview = await load_manager_external_source_acquisition_preview(
        session,
        lead_id=lead_id,
        limit=50,
    )
    operator_preview = await load_operator_confirmed_management_preview(session, limit=50)
    display_context = await load_frontier_display_context(session, adjudication_preview)
    frontier = build_truth_verification_frontier(
        adjudication_preview=adjudication_preview,
        manager_source_packet=build_manager_source_acquisition_packet(
            manager_preview,
            lead_id=lead_id,
            run_id=f"truth-manager-source-acquisition-api-{lead_id}",
        ),
        operator_source_packet=build_operator_source_acquisition_packet(
            operator_preview,
            run_id="truth-operator-source-acquisition-api",
        ),
        display_context=display_context,
        limit=frontier_limit,
    )
    worklist = build_source_acquisition_worklist(frontier, max_items=max_items)
    _attach_source_acquisition_csv_handoff(
        worklist,
        lead_id=lead_id,
        frontier_limit=frontier_limit,
        max_items=max_items,
    )
    worklist["schema_status"] = schema_status
    await session.rollback()
    return worklist


@router.get("/source-overlap-blocker-report")
async def truth_source_overlap_blocker_report(
    lead_id: str = Query("0ff794d3ba2d", max_length=80),
    frontier_limit: int = Query(10, ge=1, le=50),
    max_items: int = Query(10, ge=1, le=25),
    max_relationships: int = Query(10, ge=1, le=25),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    del user
    schema_status = await load_truth_schema_status(session)
    if not is_truth_schema_current(schema_status):
        readiness = build_schema_readiness_report(schema_status=schema_status)
        readiness["run_type"] = "truth_source_overlap_blocker_report"
        readiness["dry_run"] = True
        readiness["mutations_planned"] = 0
        readiness["blocked_reason"] = "Source-overlap blocker report requires the truth-confidence schema."
        return readiness
    adjudication_preview = await load_claim_adjudication_preview(
        session,
        limit=max(frontier_limit, 10),
        include_samples=False,
    )
    manager_preview = await load_manager_external_source_acquisition_preview(
        session,
        lead_id=lead_id,
        limit=50,
    )
    operator_preview = await load_operator_confirmed_management_preview(session, limit=50)
    display_context = await load_frontier_display_context(session, adjudication_preview)
    frontier = build_truth_verification_frontier(
        adjudication_preview=adjudication_preview,
        manager_source_packet=build_manager_source_acquisition_packet(
            manager_preview,
            lead_id=lead_id,
            run_id=f"truth-manager-source-acquisition-api-{lead_id}",
        ),
        operator_source_packet=build_operator_source_acquisition_packet(
            operator_preview,
            run_id="truth-operator-source-acquisition-api",
        ),
        display_context=display_context,
        limit=frontier_limit,
    )
    worklist = build_source_acquisition_worklist(frontier, max_items=max_items)
    report = build_source_overlap_blocker_report(
        frontier=frontier,
        worklist=worklist,
        max_relationships=max_relationships,
    )
    report["schema_status"] = schema_status
    await session.rollback()
    return report


@router.post("/source-overlap-blocker-report/preview")
async def truth_source_overlap_blocker_report_preview(
    body: SourceOverlapBlockerReportPreviewRequest,
    lead_id: str = Query("0ff794d3ba2d", max_length=80),
    frontier_limit: int = Query(10, ge=1, le=50),
    max_items: int = Query(10, ge=1, le=25),
    max_relationships: int = Query(10, ge=1, le=25),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    schema_status = await load_truth_schema_status(session)
    if not is_truth_schema_current(schema_status):
        readiness = build_schema_readiness_report(schema_status=schema_status)
        readiness["run_type"] = "truth_source_overlap_blocker_report"
        readiness["dry_run"] = True
        readiness["mutations_planned"] = 0
        readiness["blocked_reason"] = "Source-overlap blocker report requires the truth-confidence schema."
        return readiness
    adjudication_preview = await load_claim_adjudication_preview(
        session,
        limit=max(frontier_limit, 10),
        include_samples=False,
    )
    manager_preview = await load_manager_external_source_acquisition_preview(
        session,
        lead_id=lead_id,
        limit=50,
    )
    operator_preview = await load_operator_confirmed_management_preview(session, limit=50)
    display_context = await load_frontier_display_context(session, adjudication_preview)
    frontier = build_truth_verification_frontier(
        adjudication_preview=adjudication_preview,
        manager_source_packet=build_manager_source_acquisition_packet(
            manager_preview,
            lead_id=lead_id,
            run_id=f"truth-manager-source-acquisition-api-{lead_id}",
        ),
        operator_source_packet=build_operator_source_acquisition_packet(
            operator_preview,
            run_id="truth-operator-source-acquisition-api",
        ),
        display_context=display_context,
        limit=frontier_limit,
    )
    worklist = build_source_acquisition_worklist(frontier, max_items=max_items)
    candidate_preview = await _preview_source_evidence_intake_batch_request(
        body,
        worklist=worklist,
        schema_status=schema_status,
        session=session,
        recorded_by=user.email,
    )
    report = build_source_overlap_blocker_report(
        frontier=frontier,
        worklist=worklist,
        source_evidence_batch_preview=candidate_preview,
        max_relationships=max_relationships,
    )
    report["schema_status"] = schema_status
    await session.rollback()
    return report


@router.post("/source-evidence-intake/preview")
async def truth_source_evidence_intake_preview(
    body: SourceEvidenceIntakeRequest,
    lead_id: str = Query("0ff794d3ba2d", max_length=80),
    frontier_limit: int = Query(10, ge=1, le=50),
    max_items: int = Query(25, ge=1, le=25),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    schema_status, worklist = await _build_source_intake_worklist(
        session,
        lead_id=lead_id,
        frontier_limit=frontier_limit,
        max_items=max_items,
    )
    if worklist is None:
        readiness = build_schema_readiness_report(schema_status=schema_status)
        readiness["run_type"] = "truth_source_evidence_intake_preview"
        readiness["dry_run"] = True
        readiness["mutations_planned"] = 0
        readiness["blocked_reason"] = "Source-evidence intake requires the truth-confidence schema."
        return readiness
    intake = build_source_evidence_intake_preview(body.model_dump(), worklist=worklist)
    intake["schema_status"] = schema_status
    intake["worklist_context"] = {
        "request_count": worklist.get("request_count"),
        "work_item_count": worklist.get("work_item_count"),
        "recording_ready_count": worklist.get("recording_ready_count"),
        "approval_required_count": worklist.get("approval_required_count"),
    }
    if intake["validation_status"] != "ready_for_manual_evidence_preview":
        await session.rollback()
        return intake
    preview = await preview_or_record_manual_evidence(
        session,
        payload=intake["manual_evidence_payload"],
        recorded_by=user.email,
        dry_run=True,
        confirm_execute=False,
    )
    intake["manual_evidence_preview"] = preview
    intake["recording_ready"] = preview.get("run_type") == "manual_evidence_capture" and preview.get("dry_run") is True
    intake["next_required_action"] = (
        "Review the manual_evidence_preview, mutation scope, and rollback plan. "
        "Record only after explicit dry_run=false / confirm_execute=true approval."
    )
    await session.rollback()
    return intake


@router.post("/source-evidence-intake/batch-preview")
async def truth_source_evidence_intake_batch_preview(
    body: SourceEvidenceIntakeBatchRequest,
    lead_id: str = Query("0ff794d3ba2d", max_length=80),
    frontier_limit: int = Query(10, ge=1, le=50),
    max_items: int = Query(25, ge=1, le=25),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    schema_status, worklist = await _build_source_intake_worklist(
        session,
        lead_id=lead_id,
        frontier_limit=frontier_limit,
        max_items=max_items,
    )
    if worklist is None:
        readiness = build_schema_readiness_report(schema_status=schema_status)
        readiness["run_type"] = "truth_source_evidence_intake_batch_preview"
        readiness["dry_run"] = True
        readiness["mutations_planned"] = 0
        readiness["blocked_reason"] = "Source-evidence intake requires the truth-confidence schema."
        return readiness

    batch = await _preview_source_evidence_intake_batch_request(
        body,
        worklist=worklist,
        schema_status=schema_status,
        session=session,
        recorded_by=user.email,
    )
    await session.rollback()
    return batch


@router.get("/leads/{lead_id}/summary")
async def lead_truth_summary(
    lead_id: str,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    del user
    schema_status = await load_truth_schema_status(session)
    truth_schema_current = is_truth_schema_current(schema_status)
    summary = await build_lead_truth_summary(session, lead_id, include_persisted_claims=truth_schema_current)
    if summary is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    if not truth_schema_current:
        summary["schema_status"] = schema_status
    return summary


@router.get("/subjects/{subject_type}/{subject_id}/summary")
async def subject_truth_summary(
    subject_type: str,
    subject_id: str,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    del user
    normalized_subject_type = subject_type.strip().lower()
    if normalized_subject_type not in ALLOWED_SUBJECT_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported truth subject type: {subject_type}")
    schema_status = await load_truth_schema_status(session)
    truth_schema_current = is_truth_schema_current(schema_status)
    summary = await build_subject_truth_summary(
        session,
        subject_type=normalized_subject_type,
        subject_id=subject_id,
        include_persisted_claims=truth_schema_current,
    )
    if summary is None:
        raise HTTPException(status_code=404, detail="Truth subject not found")
    if not truth_schema_current:
        summary["schema_status"] = schema_status
    return summary


@router.get("/review-queue")
async def review_queue(
    queue: Optional[str] = Query(None),
    status: str = Query("open"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    del user
    schema_status = await load_truth_schema_status(session)
    if not is_truth_schema_current(schema_status):
        return _schema_not_ready_review_queue(schema_status, limit=limit, offset=offset)
    wheres = ["status = :status"]
    params: dict[str, Any] = {"status": status, "limit": limit, "offset": offset}
    if queue:
        wheres.append("queue_name = :queue")
        params["queue"] = queue
    where_sql = " AND ".join(wheres)

    persisted_rows = await session.execute(
        text(f"""
            SELECT
                review_id,
                queue_name,
                subject_type,
                subject_id,
                status,
                priority,
                confidence_score,
                actionability_level,
                proposed_change,
                supporting_evidence,
                contradicting_evidence,
                rationale,
                run_id,
                updated_at
            FROM truth_review_items
            WHERE {where_sql}
            ORDER BY priority DESC, updated_at DESC NULLS LAST
            LIMIT :limit OFFSET :offset
        """),
        params,
    )
    items = []
    for row in persisted_rows:
        payload = dict(row._mapping)
        for field in ("proposed_change", "supporting_evidence", "contradicting_evidence", "rationale"):
            payload[field] = _parse_json_object(payload.get(field))
        payload["updated_at"] = payload["updated_at"].isoformat() if payload.get("updated_at") else None
        items.append(payload)

    if status == "open" and not queue:
        proposal_rows = await session.execute(
            text("""
                SELECT
                    CONCAT('canonical-proposal-', id) AS review_id,
                    CASE
                        WHEN bucket = 'review_required' THEN 'needs_human_review'
                        WHEN safe_to_execute = true THEN 'safe_auto_accept'
                        ELSE bucket
                    END AS queue_name,
                    'canonical_entity' AS subject_type,
                    canonical_entity_id AS subject_id,
                    'open' AS status,
                    CASE WHEN bucket = 'review_required' THEN 90 WHEN safe_to_execute = true THEN 60 ELSE 50 END AS priority,
                    NULL::DOUBLE PRECISION AS confidence_score,
                    CASE WHEN safe_to_execute = true THEN 'ranked_sourcing' ELSE 'do_not_act' END AS actionability_level,
                    evidence AS proposed_change,
                    evidence AS supporting_evidence,
                    reasons AS contradicting_evidence,
                    reasons AS rationale,
                    NULL::VARCHAR AS run_id,
                    updated_at
                FROM canonical_entity_match_proposals
                WHERE proposal_status = 'proposed'
                ORDER BY updated_at DESC NULLS LAST, id DESC
                LIMIT :limit
            """),
            {"limit": max(0, limit - len(items))},
        )
        for row in proposal_rows:
            payload = dict(row._mapping)
            for field in ("proposed_change", "supporting_evidence", "contradicting_evidence", "rationale"):
                payload[field] = _parse_json_object(payload.get(field))
            payload["updated_at"] = payload["updated_at"].isoformat() if payload.get("updated_at") else None
            items.append(payload)

    return {
        "items": items[:limit],
        "limit": limit,
        "offset": offset,
        "source": "truth_review_items_plus_canonical_proposals",
    }


@router.post("/review-queue/{review_id}/decision")
async def review_queue_decision(
    review_id: str,
    body: ReviewDecisionRequest,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    schema_status = await load_truth_schema_status(session)
    if not is_truth_schema_current(schema_status):
        return _schema_not_ready_decision(review_id, body.decision, schema_status)
    return await apply_review_decision(
        session,
        review_id=review_id,
        decision=body.decision,
        reviewer_email=user.email,
        note=body.note,
        dry_run=body.dry_run,
        confirm_execute=body.confirm_execute,
    )


@router.post("/manual-evidence")
async def manual_evidence_capture(
    request: Request,
    body: ManualEvidenceRequest,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    del request
    schema_status = await load_truth_schema_status(session)
    if not is_truth_schema_current(schema_status):
        return _schema_not_ready_manual_evidence(schema_status)
    try:
        return await preview_or_record_manual_evidence(
            session,
            payload=body.model_dump(),
            recorded_by=user.email,
            dry_run=body.dry_run,
            confirm_execute=body.confirm_execute,
            run_id=body.run_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/validate/preview")
@limiter.limit("10/minute")
async def validate_preview(
    request: Request,
    sample_limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    del request, user
    schema_status = await load_truth_schema_status(session)
    if not is_truth_schema_current(schema_status):
        return build_schema_readiness_report(schema_status=schema_status)
    return await preview_adversarial_validation(session, sample_limit=sample_limit)


@router.get("/adjudication-preview")
async def adjudication_preview(
    limit: int = Query(100, ge=1, le=1000),
    include_samples: bool = Query(True),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    del user
    schema_status = await load_truth_schema_status(session)
    if not is_truth_schema_current(schema_status):
        return _schema_not_ready_adjudication(schema_status, limit=limit)
    preview = await load_claim_adjudication_preview(
        session,
        limit=limit,
        include_samples=include_samples,
    )
    preview["schema_status"] = schema_status
    return preview


@router.post("/adjudication/apply")
async def adjudication_apply(
    body: ClaimAdjudicationApplyRequest,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    del user
    schema_status = await load_truth_schema_status(session)
    if not is_truth_schema_current(schema_status):
        return _schema_not_ready_adjudication_apply(schema_status)
    result = await preview_or_apply_claim_adjudication(
        session,
        limit=body.limit,
        dry_run=body.dry_run,
        confirm_execute=body.confirm_execute,
        run_id=body.run_id,
    )
    result["schema_status"] = schema_status
    return result


@router.post("/role-claim-corrections/apply")
async def role_claim_correction_apply(
    body: RoleClaimCorrectionRequest,
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    del user
    schema_status = await load_truth_schema_status(session)
    if not is_truth_schema_current(schema_status):
        return _schema_not_ready_role_correction(schema_status)
    result = await preview_or_apply_role_claim_corrections(
        session,
        lead_id=body.lead_id,
        limit=body.limit,
        dry_run=body.dry_run,
        confirm_execute=body.confirm_execute,
        run_id=body.run_id,
    )
    result["schema_status"] = schema_status
    return result


@router.post("/materialize/preview")
@limiter.limit("10/minute")
async def materialize_preview(
    request: Request,
    limit: int = Query(500, ge=1, le=5000),
    source: list[str] | None = Query(default=None, description="Optional repeatable source filter, e.g. ?source=building_management&source=outreach_feedback"),
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    del request, user
    schema_status = await load_truth_schema_status(session)
    if not is_truth_schema_current(schema_status):
        return build_schema_readiness_report(schema_status=schema_status)
    try:
        return await materialize_truth_claims(
            session,
            limit=limit,
            dry_run=True,
            confirm_execute=False,
            sources=source,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/golden-cases")
async def golden_cases(
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    del user
    schema_status = await load_truth_schema_status(session)
    if not is_truth_schema_current(schema_status):
        return {"cases": GOLDEN_CASE_SEEDS, "seeded": False, "schema_status": schema_status}
    rows = await session.execute(text("""
        SELECT case_id, name, case_type, subject_type, subject_id, expected_outcome, expected_claims, tricky_features, source_notes, active
        FROM golden_verification_cases
        WHERE active = true
        ORDER BY case_type, name
    """))
    cases = []
    for row in rows:
        payload = dict(row._mapping)
        payload["expected_claims"] = payload.get("expected_claims") or {}
        payload["tricky_features"] = payload.get("tricky_features") or []
        cases.append(payload)
    return {"cases": cases or GOLDEN_CASE_SEEDS, "seeded": bool(cases)}


@router.get("/golden-benchmark")
async def golden_benchmark(
    session: AsyncSession = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
):
    del user
    schema_status = await load_truth_schema_status(session)
    if not is_truth_schema_current(schema_status):
        benchmark = evaluate_golden_cases(GOLDEN_CASE_SEEDS, {}, seeded=False)
        benchmark["schema_status"] = schema_status
        return benchmark
    return await load_golden_benchmark(session)
