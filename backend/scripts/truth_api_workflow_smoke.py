"""Read-only API workflow smoke for Data Truth & Confidence surfaces.

This script verifies the representative API workflows that matter for the
truth program without mutating data:

- lead search and lead detail
- lead lineage, contacts, and truth summary
- building list/detail/lineage and building truth summary
- truth dashboard, health report, review queue, golden benchmark, activation
  packet, source audit, quality data-health, and dry-run preview gates
- representative mutation-capable job starts that must return approval or
  schema-gated previews without queueing work

It is intentionally HTTP-based so it exercises the same auth, routing, CORS-era
API contracts, and schema-gated responses used by the frontend.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "http://127.0.0.1:8000"


@dataclass
class Check:
    name: str
    status: str
    detail: str
    path: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


class ApiClient:
    def __init__(self, base_url: str, *, token: str | None = None, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        payload = None
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = Request(
            f"{self.base_url}{path}",
            data=payload,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(req, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {path} failed with HTTP {exc.code}: {raw[:500]}") from exc
        except URLError as exc:
            raise RuntimeError(f"{method} {path} failed: {exc}") from exc

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self.request("POST", path, body or {})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("API_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--email", default=os.environ.get("SMOKE_EMAIL") or os.environ.get("ADMIN_EMAIL"))
    parser.add_argument("--password", default=os.environ.get("SMOKE_PASSWORD") or os.environ.get("ADMIN_PASSWORD"))
    parser.add_argument("--token", default=os.environ.get("SMOKE_TOKEN") or os.environ.get("API_TOKEN"))
    parser.add_argument("--indent", type=int, default=2)
    parser.add_argument("--lead-id", default=os.environ.get("SMOKE_LEAD_ID"))
    parser.add_argument("--bbl", default=os.environ.get("SMOKE_BBL"))
    return parser.parse_args()


def ok(name: str, detail: str, *, path: str | None = None, evidence: dict[str, Any] | None = None) -> Check:
    return Check(name=name, status="ok", detail=detail, path=path, evidence=evidence or {})


def skipped(name: str, detail: str, *, path: str | None = None, evidence: dict[str, Any] | None = None) -> Check:
    return Check(name=name, status="skipped", detail=detail, path=path, evidence=evidence or {})


def failed(name: str, detail: str, *, path: str | None = None, evidence: dict[str, Any] | None = None) -> Check:
    return Check(name=name, status="failed", detail=detail, path=path, evidence=evidence or {})


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def first_bbl_from_lead_contacts(contacts_payload: dict[str, Any]) -> str | None:
    for building in contacts_payload.get("buildings") or []:
        bbl = building.get("bbl")
        if bbl:
            return str(bbl)
    return None


def first_bbl_from_building_list(buildings_payload: dict[str, Any]) -> str | None:
    for building in buildings_payload.get("buildings") or []:
        bbl = building.get("bbl")
        if bbl:
            return str(bbl)
    return None


def job_ids(payload: Any) -> list[int]:
    if not isinstance(payload, list):
        return []
    ids: list[int] = []
    for row in payload:
        if isinstance(row, dict) and row.get("id") is not None:
            try:
                ids.append(int(row["id"]))
            except (TypeError, ValueError):
                continue
    return ids


def validate_activation_packet(payload: dict[str, Any]) -> None:
    require(payload.get("dry_run") is True, "activation packet must be read-only")
    require(payload.get("mutations_planned") == 0, "activation packet should not plan mutations")
    require(payload.get("business_use_allowed") is False, "business use should remain blocked before activation gates clear")
    require(payload.get("approval_required") is True, "activation packet should expose approval-required gates")

    claim_readiness = payload.get("claim_readiness") if isinstance(payload.get("claim_readiness"), dict) else {}
    require(claim_readiness, "activation packet missing claim_readiness")
    for key in ("claim_count", "verified_claim_count", "critical_or_high_gap_count"):
        require(key in claim_readiness, f"activation packet claim_readiness missing {key}")
        require(isinstance(claim_readiness[key], int), f"activation packet claim_readiness {key} should be an integer")
    for key in ("has_materialized_claims", "has_verified_claims", "has_no_critical_or_high_gaps"):
        require(key in claim_readiness, f"activation packet claim_readiness missing {key}")
        require(isinstance(claim_readiness[key], bool), f"activation packet claim_readiness {key} should be a boolean")

    rollback = payload.get("rollback") if isinstance(payload.get("rollback"), dict) else {}
    require(
        isinstance(rollback.get("offline_rollback_command"), str) and rollback["offline_rollback_command"].strip(),
        "activation packet missing offline rollback command",
    )

    next_steps = payload.get("next_safe_steps")
    require(isinstance(next_steps, list) and next_steps, "activation packet missing next safe steps")
    require(
        any(step.get("mutates_data") is False for step in next_steps if isinstance(step, dict)),
        "activation packet should include non-mutating review steps",
    )
    require(
        any(
            step.get("mutates_data") is True and step.get("requires_explicit_approval") is True
            for step in next_steps
            if isinstance(step, dict)
        ),
        "activation packet should mark mutating steps as explicit-approval required",
    )

    source_refresh = payload.get("source_refresh") if isinstance(payload.get("source_refresh"), dict) else {}
    require(source_refresh.get("approval_required") is True, "source refresh should be approval-gated")
    planned_job_count = int(source_refresh.get("planned_job_count") or 0)
    refreshable_job_count = int(source_refresh.get("refreshable_job_count") or 0)
    blocked_job_count = int(source_refresh.get("blocked_job_count") or 0)
    require(planned_job_count > 0, "source refresh plan should expose planned jobs")
    require(
        refreshable_job_count + blocked_job_count == planned_job_count,
        "source refresh plan should split planned jobs into refreshable and blocked counts",
    )
    next_jobs = source_refresh.get("next_jobs")
    require(isinstance(next_jobs, list) and next_jobs, "source refresh plan missing next jobs")
    for job in next_jobs:
        if not isinstance(job, dict):
            continue
        if job.get("blocked"):
            require(not job.get("execute_endpoint"), "blocked source refresh job should not expose execute endpoint")
            continue
        require(job.get("approval_required") is True, "source refresh next job missing approval flag")
        require(
            "confirm_execute=true" in str(job.get("execute_endpoint") or ""),
            "source refresh execute endpoint should require confirm_execute=true",
        )


def validate_completion_audit(payload: dict[str, Any]) -> None:
    require(payload.get("dry_run") is True, "completion audit must be read-only")
    require(payload.get("mutations_planned") == 0, "completion audit should not plan mutations")
    require(payload.get("completion_status") in {"complete", "not_complete"}, "completion audit missing status")
    require(isinstance(payload.get("success_criteria"), list), "completion audit missing success_criteria")
    prompt_checklist = payload.get("prompt_to_artifact_checklist")
    require(isinstance(prompt_checklist, list) and prompt_checklist, "completion audit missing prompt checklist")
    for item in prompt_checklist:
        require(isinstance(item, dict), "completion audit checklist item should be an object")
        require(isinstance(item.get("requirement"), str) and item["requirement"], "checklist item missing requirement")
        require(
            item.get("status") in {"satisfied", "blocked", "missing", "requires_review", "runtime_not_checked"},
            "unknown checklist status",
        )
        require(isinstance(item.get("evidence"), dict), "checklist item missing evidence")
    artifact_summary = payload.get("artifact_summary") if isinstance(payload.get("artifact_summary"), dict) else {}
    for key in ("total", "satisfied", "missing"):
        require(isinstance(artifact_summary.get(key), int), f"completion artifact_summary missing integer {key}")
    blockers = payload.get("runtime_blockers")
    require(isinstance(blockers, list), "completion audit missing runtime blockers")
    if payload.get("completion_status") == "complete":
        require(not blockers, "complete audit should have no runtime blockers")
        require(
            all(item.get("status") == "satisfied" for item in prompt_checklist),
            "complete audit should satisfy every prompt checklist item",
        )
    else:
        require(blockers, "not-complete audit should expose runtime blockers")
        require(
            any(item.get("status") != "satisfied" for item in prompt_checklist),
            "not-complete audit should identify blocked/missing prompt checklist items",
        )
        source_overlap_blocker = next(
            (
                blocker
                for blocker in blockers
                if isinstance(blocker, dict) and blocker.get("gate") == "source_overlap_recording"
            ),
            None,
        )
        if source_overlap_blocker is not None:
            evidence = source_overlap_blocker.get("evidence")
            if isinstance(evidence, dict):
                new_relationship_count = int(evidence.get("new_relationship_candidate_count") or 0)
                if new_relationship_count > 0:
                    require(
                        evidence.get("new_relationship_counts_as_current_ledger_overlap") is False,
                        "completion audit new relationship candidates should not count as current-ledger overlap",
                    )
                    require(
                        evidence.get("new_relationship_approval_required_for_relationship_creation") is True,
                        "completion audit new relationship candidates should require relationship-creation approval",
                    )
                    require(
                        isinstance(evidence.get("new_relationship_source_family_counts"), dict)
                        and evidence["new_relationship_source_family_counts"],
                        "completion audit missing new relationship source-family counts",
                    )
                    samples = evidence.get("new_relationship_candidates_sample")
                    require(
                        isinstance(samples, list) and samples,
                        "completion audit missing new relationship candidate samples",
                    )
                    first_sample = samples[0]
                    require(
                        isinstance(first_sample, dict),
                        "completion audit new relationship sample should be an object",
                    )
                    require(
                        isinstance(first_sample.get("source_family"), str) and first_sample["source_family"].strip(),
                        "completion audit new relationship sample missing source family",
                    )
                    require(
                        isinstance(first_sample.get("local_building_match"), dict),
                        "completion audit new relationship sample missing local building match",
                    )
                    relationship_state = first_sample.get("current_relationship_state")
                    require(
                        isinstance(relationship_state, dict),
                        "completion audit new relationship sample missing current relationship state",
                    )
                    require(
                        relationship_state.get("counts_as_current_ledger_overlap") is False,
                        "completion audit new relationship sample should not count as current-ledger overlap",
                    )
                    require(
                        isinstance(relationship_state.get("current_truth_claim_count"), int),
                        "completion audit new relationship sample missing current truth-claim count",
                    )
                    require(
                        isinstance(relationship_state.get("current_building_management_relationship_count"), int),
                        "completion audit new relationship sample missing current building-management count",
                    )
                    require(
                        "not counted as current-ledger source overlap"
                        in str(evidence.get("new_relationship_policy") or ""),
                        "completion audit missing new relationship non-overlap policy",
                    )
                operator_confirmed = evidence.get("operator_confirmed")
                if isinstance(operator_confirmed, dict) and "strict_gap_summary" in operator_confirmed:
                    strict_gap_summary = operator_confirmed.get("strict_gap_summary")
                    require(isinstance(strict_gap_summary, dict), "completion audit operator strict_gap_summary should be an object")
                    for key in ("proposal_count", "strict_ready_proposal_count", "broad_source_ready_not_strict_count"):
                        require(
                            isinstance(strict_gap_summary.get(key), int),
                            f"completion audit operator strict_gap_summary missing integer {key}",
                        )
                    if int(strict_gap_summary.get("broad_source_ready_not_strict_count") or 0) > 0:
                        gap_candidates = strict_gap_summary.get("gap_candidates")
                        require(
                            isinstance(gap_candidates, list) and gap_candidates,
                            "completion audit operator strict_gap_summary missing gap candidates",
                        )
                        first_gap = gap_candidates[0]
                        require(isinstance(first_gap, dict), "completion audit operator gap candidate should be an object")
                        require(
                            isinstance(first_gap.get("address"), str) and first_gap["address"].strip(),
                            "completion audit operator gap candidate missing address",
                        )
                        require(
                            first_gap.get("strict_manager_gap_status") == "broad_source_ready_not_strict",
                            "completion audit operator gap candidate should preserve strict gap status",
                        )
                        require(
                            isinstance(first_gap.get("missing_manager_proof_source_family_count"), int),
                            "completion audit operator gap candidate missing manager-proof family count",
                        )


def validate_source_overlap_approval_packet(payload: dict[str, Any]) -> None:
    require(payload.get("dry_run") is True, "source-overlap approval packet must be read-only")
    require(payload.get("mutations_planned") == 0, "source-overlap approval packet should not plan mutations")
    require(payload.get("approval_required") is True, "source-overlap approval packet should require approval")

    ledger = payload.get("current_ledger") if isinstance(payload.get("current_ledger"), dict) else {}
    for key in (
        "total_fact_group_count",
        "single_source_fact_group_count",
        "multi_source_fact_group_count",
        "source_ready_fact_group_count",
        "verification_candidate_count",
    ):
        require(isinstance(ledger.get(key), int), f"source-overlap current_ledger missing integer {key}")

    recording_gate = (
        payload.get("source_overlap_recording_gate")
        if isinstance(payload.get("source_overlap_recording_gate"), dict)
        else {}
    )
    require(
        recording_gate.get("status") in {"approval_required", "satisfied"},
        "source-overlap packet missing recording-gate status",
    )
    for key in (
        "current_multi_source_fact_group_count",
        "current_source_ready_fact_group_count",
        "current_verification_candidate_count",
    ):
        require(isinstance(recording_gate.get(key), int), f"source-overlap recording gate missing integer {key}")
    require(
        isinstance(recording_gate.get("source_overlap_proof_satisfied"), bool),
        "source-overlap recording gate missing proof status",
    )
    require(
        recording_gate.get("additional_evidence_recording_requires_approval") is True,
        "source-overlap recording gate should keep additional evidence approval-gated",
    )
    if int(ledger.get("multi_source_fact_group_count") or 0) > 0 and int(ledger.get("source_ready_fact_group_count") or 0) > 0:
        require(
            recording_gate.get("status") == "satisfied",
            "source-overlap recording gate should be satisfied when current ledger has source overlap",
        )
        require(
            recording_gate.get("source_overlap_proof_satisfied") is True,
            "source-overlap recording gate should mark current-ledger overlap as proof satisfied",
        )

    previewed = (
        payload.get("previewed_overlap_if_approved")
        if isinstance(payload.get("previewed_overlap_if_approved"), dict)
        else {}
    )
    for key in (
        "manager_source_ready_if_recorded_count",
        "manager_strict_source_ready_if_recorded_count",
        "operator_source_ready_if_recorded_count",
        "operator_strict_source_ready_if_recorded_count",
        "safe_to_mark_verified_after_recording",
    ):
        require(isinstance(previewed.get(key), int), f"source-overlap preview missing integer {key}")
    require(
        previewed.get("safe_to_mark_verified_after_recording") == 0,
        "source-overlap packet should not mark previewed evidence verified-safe",
    )

    manager_packet = payload.get("recommended_first_packet") if isinstance(payload.get("recommended_first_packet"), dict) else {}
    require(
        manager_packet.get("approval_required") is True,
        "source-overlap manager packet should expose top-level approval_required",
    )
    require(
        manager_packet.get("allowed_execute") is False,
        "source-overlap manager packet should not allow execute",
    )
    require(int(manager_packet.get("template_count") or 0) > 0, "source-overlap packet missing manager templates")
    require(
        int(manager_packet.get("planned_upsert_count_if_approved") or 0) > 0,
        "source-overlap packet missing approval-gated manager upsert count",
    )
    manager_families = manager_packet.get("manager_proof_source_families")
    require(
        isinstance(manager_families, list) and len(manager_families) >= 2,
        "source-overlap packet missing manager-proof source families",
    )
    require(
        "--confirm-execute" in str(manager_packet.get("recommended_execute_command") or ""),
        "source-overlap manager execute command should require confirm_execute",
    )
    approval_summary = manager_packet.get("approval_decision_summary")
    require(
        isinstance(approval_summary, dict),
        "source-overlap manager packet missing approval decision summary",
    )
    require(
        approval_summary.get("approval_required") is True,
        "source-overlap approval decision summary should require approval",
    )
    require(
        "--confirm-execute" in str(approval_summary.get("recommended_execute_command") or ""),
        "source-overlap approval decision command should require confirm_execute",
    )
    require(
        approval_summary.get("would_record_template_count") == manager_packet.get("template_count"),
        "source-overlap approval decision summary should match template count",
    )
    require(
        approval_summary.get("would_plan_upsert_count") == manager_packet.get("planned_upsert_count_if_approved"),
        "source-overlap approval decision summary should match planned upsert count",
    )
    require(
        approval_summary.get("expected_safe_to_mark_verified_count") == 0,
        "source-overlap approval decision should not expect verified-safe facts",
    )
    require(
        approval_summary.get("single_source_claims_stay_unverified") is True,
        "source-overlap approval decision should keep single-source claims unverified",
    )
    for key in (
        "will_mark_verified",
        "will_create_or_refresh_source_data",
        "will_materialize_new_relationships",
    ):
        require(
            approval_summary.get(key) is False,
            f"source-overlap approval decision should set {key}=false",
        )

    manager_gap_summary = payload.get("manager_strict_gap_summary")
    if isinstance(manager_gap_summary, dict):
        for key in (
            "claim_group_count",
            "strict_ready_claim_group_count",
            "broad_source_ready_not_strict_count",
        ):
            require(
                isinstance(manager_gap_summary.get(key), int),
                f"source-overlap manager strict gap summary missing integer {key}",
            )
        if int(manager_gap_summary.get("broad_source_ready_not_strict_count") or 0) > 0:
            gap_candidates = manager_gap_summary.get("gap_candidates")
            require(
                isinstance(gap_candidates, list) and gap_candidates,
                "source-overlap manager strict gap summary missing gap candidates",
            )
            first_gap = gap_candidates[0]
            require(
                isinstance(first_gap, dict),
                "source-overlap manager strict gap candidate should be an object",
            )
            require(
                isinstance(first_gap.get("address"), str) and first_gap["address"].strip(),
                "source-overlap manager strict gap candidate missing address",
            )
            require(
                first_gap.get("strict_manager_gap_status") == "broad_source_ready_not_strict",
                "source-overlap manager strict gap candidate should preserve strict gap status",
            )
            require(
                isinstance(first_gap.get("missing_manager_proof_source_family_count"), int),
                "source-overlap manager strict gap candidate missing manager-proof family count",
            )
            require(
                isinstance(first_gap.get("suggested_source_families"), list)
                and first_gap["suggested_source_families"],
                "source-overlap manager strict gap candidate missing suggested source families",
            )
            require(
                isinstance(first_gap.get("first_search_query"), str)
                and first_gap["first_search_query"].strip(),
                "source-overlap manager strict gap candidate missing first search query",
            )

    manager_new_relationship_summary = payload.get("manager_new_relationship_candidate_summary")
    if isinstance(manager_new_relationship_summary, dict):
        require(
            isinstance(manager_new_relationship_summary.get("candidate_count"), int),
            "source-overlap manager new relationship summary missing candidate count",
        )
        require(
            manager_new_relationship_summary.get("counts_as_current_ledger_overlap") is False,
            "source-overlap manager new relationship candidates should not count as current-ledger overlap",
        )
        require(
            manager_new_relationship_summary.get("approval_required_for_relationship_creation") is True,
            "source-overlap manager new relationship candidates should require relationship-creation approval",
        )
        require(
            isinstance(manager_new_relationship_summary.get("source_family_counts"), dict),
            "source-overlap manager new relationship summary missing source family counts",
        )
        if int(manager_new_relationship_summary.get("candidate_count") or 0) > 0:
            candidates = manager_new_relationship_summary.get("candidates")
            require(
                isinstance(candidates, list) and candidates,
                "source-overlap manager new relationship summary missing candidate samples",
            )
            first_candidate = candidates[0]
            require(
                isinstance(first_candidate, dict),
                "source-overlap manager new relationship candidate should be an object",
            )
            require(
                isinstance(first_candidate.get("source_family"), str) and first_candidate["source_family"].strip(),
                "source-overlap manager new relationship candidate missing source family",
            )
            require(
                isinstance(first_candidate.get("bbl"), str) and first_candidate["bbl"].strip(),
                "source-overlap manager new relationship candidate missing local BBL",
            )
            require(
                isinstance(first_candidate.get("safe_action"), str) and first_candidate["safe_action"].strip(),
                "source-overlap manager new relationship candidate missing safe action",
            )
            relationship_state = first_candidate.get("current_relationship_state")
            require(
                isinstance(relationship_state, dict),
                "source-overlap manager new relationship candidate missing current relationship state",
            )
            require(
                relationship_state.get("counts_as_current_ledger_overlap") is False,
                "source-overlap manager new relationship candidate should not count as current-ledger overlap",
            )
            require(
                isinstance(relationship_state.get("current_truth_claim_count"), int),
                "source-overlap manager new relationship candidate missing current truth-claim count",
            )
            require(
                isinstance(relationship_state.get("current_building_management_relationship_count"), int),
                "source-overlap manager new relationship candidate missing current building-management count",
            )

    operator_packet = payload.get("operator_strict_packet")
    if isinstance(operator_packet, dict):
        require(
            operator_packet.get("approval_required") is True,
            "source-overlap operator packet should expose top-level approval_required",
        )
        require(
            operator_packet.get("allowed_execute") is False,
            "source-overlap operator packet should not allow execute",
        )
        require(
            int(operator_packet.get("template_count") or 0) > 0,
            "source-overlap packet missing operator templates",
        )
        require(
            int(operator_packet.get("claim_group_count") or 0) > 0,
            "source-overlap packet missing operator claim groups",
        )
        require(
            int(operator_packet.get("planned_upsert_count_if_approved") or 0) > 0,
            "source-overlap packet missing approval-gated operator upsert count",
        )
        operator_families = operator_packet.get("manager_proof_source_families")
        require(
            isinstance(operator_families, list) and "operator_confirmed" in operator_families,
            "source-overlap operator packet missing operator-confirmed source family",
        )
        require(
            "--confirm-execute" in str(operator_packet.get("recommended_execute_command") or ""),
            "source-overlap operator execute command should require confirm_execute",
        )
        approval_summary = operator_packet.get("approval_decision_summary")
        require(
            isinstance(approval_summary, dict),
            "source-overlap operator packet missing approval decision summary",
        )
        require(
            approval_summary.get("approval_required") is True,
            "source-overlap operator approval decision summary should require approval",
        )
        require(
            "--confirm-execute" in str(approval_summary.get("recommended_execute_command") or ""),
            "source-overlap operator approval decision command should require confirm_execute",
        )
        require(
            approval_summary.get("would_record_template_count") == operator_packet.get("template_count"),
            "source-overlap operator approval decision summary should match template count",
        )
        require(
            approval_summary.get("would_plan_upsert_count") == operator_packet.get("planned_upsert_count_if_approved"),
            "source-overlap operator approval decision summary should match planned upsert count",
        )
        require(
            approval_summary.get("expected_safe_to_mark_verified_count") == 0,
            "source-overlap operator approval decision should not expect verified-safe facts",
        )
        for key in (
            "will_mark_verified",
            "will_create_or_refresh_source_data",
            "will_materialize_new_relationships",
        ):
            require(
                approval_summary.get(key) is False,
                f"source-overlap operator approval decision should set {key}=false",
            )
        excluded_count = operator_packet.get("excluded_non_strict_candidate_count")
        require(
            isinstance(excluded_count, int),
            "source-overlap operator packet missing excluded non-strict candidate count",
        )
        if excluded_count > 0:
            excluded_candidates = operator_packet.get("excluded_non_strict_candidates")
            require(
                isinstance(excluded_candidates, list) and excluded_candidates,
                "source-overlap operator packet missing excluded non-strict candidates",
            )
            first_excluded = excluded_candidates[0]
            require(
                isinstance(first_excluded, dict),
                "source-overlap operator excluded candidate should be an object",
            )
            require(
                isinstance(first_excluded.get("address"), str) and first_excluded["address"].strip(),
                "source-overlap operator excluded candidate missing address",
            )
            require(
                first_excluded.get("strict_manager_gap_status") == "broad_source_ready_not_strict",
                "source-overlap operator excluded candidate should preserve strict gap status",
            )
            require(
                isinstance(first_excluded.get("missing_manager_proof_source_family_count"), int),
                "source-overlap operator excluded candidate missing manager-proof family count",
            )

    operator_gap_summary = payload.get("operator_strict_gap_summary")
    if isinstance(operator_gap_summary, dict):
        for key in (
            "candidate_count",
            "strict_ready_candidate_count",
            "broad_source_ready_not_strict_count",
        ):
            require(
                isinstance(operator_gap_summary.get(key), int),
                f"source-overlap operator strict gap summary missing integer {key}",
            )
        if int(operator_gap_summary.get("broad_source_ready_not_strict_count") or 0) > 0:
            gap_candidates = operator_gap_summary.get("gap_candidates")
            require(
                isinstance(gap_candidates, list) and gap_candidates,
                "source-overlap operator strict gap summary missing gap candidates",
            )
            first_gap = gap_candidates[0]
            require(
                isinstance(first_gap, dict),
                "source-overlap operator strict gap candidate should be an object",
            )
            require(
                isinstance(first_gap.get("address"), str) and first_gap["address"].strip(),
                "source-overlap operator strict gap candidate missing address",
            )
            require(
                first_gap.get("strict_manager_gap_status") == "broad_source_ready_not_strict",
                "source-overlap operator strict gap candidate should preserve strict gap status",
            )
            require(
                isinstance(first_gap.get("missing_manager_proof_source_family_count"), int),
                "source-overlap operator strict gap candidate missing manager-proof family count",
            )

    approval_policy = payload.get("approval_policy") if isinstance(payload.get("approval_policy"), dict) else {}
    require(
        approval_policy.get("single_source_claims_stay_unverified") is True,
        "source-overlap packet should keep single-source claims unverified",
    )
    require(
        isinstance(payload.get("post_execution_required_checks"), list) and payload["post_execution_required_checks"],
        "source-overlap packet missing post-execution checks",
    )
    require(
        isinstance(payload.get("blocked_business_use_reason"), str) and payload["blocked_business_use_reason"].strip(),
        "source-overlap packet missing business-use block reason",
    )


def validate_source_overlap_post_recording_check(payload: dict[str, Any]) -> None:
    require(payload.get("dry_run") is True, "post-recording source-overlap check must be read-only")
    require(payload.get("mutations_planned") == 0, "post-recording source-overlap check should not plan mutations")
    require(
        isinstance(payload.get("post_recording_success"), bool),
        "post-recording source-overlap check missing pass/fail status",
    )

    thresholds = payload.get("thresholds") if isinstance(payload.get("thresholds"), dict) else {}
    for key in (
        "min_multi_source_fact_groups",
        "min_source_ready_fact_groups",
        "max_verified_single_source_claims",
    ):
        require(isinstance(thresholds.get(key), int), f"post-recording check missing threshold {key}")

    ledger = payload.get("current_ledger") if isinstance(payload.get("current_ledger"), dict) else {}
    for key in (
        "multi_source_fact_group_count",
        "source_ready_fact_group_count",
    ):
        require(isinstance(ledger.get(key), int), f"post-recording check missing ledger integer {key}")
    for key in (
        "total_fact_group_count",
        "single_source_fact_group_count",
        "max_supporting_source_count",
        "max_supporting_evidence_count",
    ):
        if ledger.get(key) is not None:
            require(isinstance(ledger.get(key), int), f"post-recording check ledger {key} should be integer/null")

    verified_policy = (
        payload.get("verified_single_source_policy")
        if isinstance(payload.get("verified_single_source_policy"), dict)
        else {}
    )
    for key in ("verified_claim_count", "verified_single_source_claim_count", "sample_limit"):
        require(isinstance(verified_policy.get(key), int), f"post-recording check missing verified policy {key}")
    require(
        verified_policy.get("verified_single_source_claim_count") == 0,
        "post-recording check should not allow verified single-source claims",
    )
    require(isinstance(verified_policy.get("samples"), list), "post-recording check verified samples should be a list")

    checks = payload.get("checks")
    require(isinstance(checks, list) and checks, "post-recording check missing checks")
    check_by_name = {check.get("check"): check for check in checks if isinstance(check, dict)}
    for check_name in (
        "actual_current_ledger_multi_source",
        "actual_current_ledger_source_ready",
        "no_single_source_verified_claims",
    ):
        if check_name in check_by_name:
            check = check_by_name[check_name]
            require(check.get("status") in {"pass", "fail"}, f"post-recording {check_name} missing status")
            require(isinstance(check.get("observed"), int), f"post-recording {check_name} missing observed count")
            require(isinstance(check.get("reason"), str) and check["reason"].strip(), f"post-recording {check_name} missing reason")
    if payload.get("post_recording_success") is True:
        require(
            all(isinstance(check, dict) and check.get("status") == "pass" for check in checks),
            "passing post-recording check should have only passing checks",
        )
    else:
        require(
            any(isinstance(check, dict) and check.get("status") == "fail" for check in checks),
            "blocked post-recording check should expose a failed check",
        )
    require(
        isinstance(payload.get("safe_action"), str) and payload["safe_action"].strip(),
        "post-recording source-overlap check missing safe action",
    )


def validate_manager_source_acquisition_packet(payload: dict[str, Any]) -> None:
    require(payload.get("run_type") == "manager_source_acquisition_packet", "manager source packet missing run type")
    require(payload.get("dry_run") is True, "manager source packet must be read-only")
    require(payload.get("mutations_planned") == 0, "manager source packet should not plan mutations")
    require(int(payload.get("candidate_count") or 0) > 0, "manager source packet missing candidate count")
    require(int(payload.get("next_source_seed_count") or 0) > 0, "manager source packet missing next source seeds")
    new_relationship_count = int(payload.get("new_relationship_candidate_count") or 0)
    if new_relationship_count > 0:
        require(
            isinstance(payload.get("new_relationship_candidates"), list)
            and payload["new_relationship_candidates"],
            "manager source packet missing new relationship candidates",
        )
        first_candidate = payload["new_relationship_candidates"][0]
        require(
            isinstance(first_candidate.get("current_relationship_state"), dict),
            "manager source packet new relationship candidate missing current relationship state",
        )
        require(
            first_candidate["current_relationship_state"].get("counts_as_current_ledger_overlap") is False,
            "manager source packet new relationship candidate should not count as current-ledger overlap",
        )
        require(
            "not counted as current-ledger source overlap" in str(payload.get("new_relationship_policy") or ""),
            "manager source packet missing new relationship policy",
        )
    require(
        isinstance(payload.get("proposals"), list) and payload["proposals"],
        "manager source packet missing acquisition proposals",
    )
    for key in (
        "source_ready_if_recorded_count",
        "independent_source_ready_if_recorded_count",
        "strict_manager_source_ready_if_recorded_count",
        "verified_safe_if_recorded_count",
    ):
        require(isinstance(payload.get(key), int), f"manager source packet missing integer {key}")
    require(
        payload.get("verified_safe_if_recorded_count") == 0,
        "manager source packet should not claim verified-safe evidence",
    )
    first = payload["proposals"][0]
    require(isinstance(first.get("first_search_query"), str) and first["first_search_query"].strip(), "manager source proposal missing first search query")
    require(isinstance(first.get("source_targets"), list) and first["source_targets"], "manager source proposal missing source targets")
    require(
        isinstance(payload.get("reviewed_source_findings"), list) and payload["reviewed_source_findings"],
        "manager source packet missing reviewed-source findings",
    )
    require(
        isinstance(payload.get("source_boundary_notes"), list) and payload["source_boundary_notes"],
        "manager source packet missing source-boundary notes",
    )
    require(
        isinstance(payload.get("safe_action"), str) and "Read-only source-acquisition packet" in payload["safe_action"],
        "manager source packet missing read-only safe action",
    )


def validate_verification_frontier(payload: dict[str, Any]) -> None:
    require(payload.get("run_type") == "truth_verification_frontier", "verification frontier missing run type")
    require(payload.get("dry_run") is True, "verification frontier must be read-only")
    require(payload.get("mutations_planned") == 0, "verification frontier should not plan mutations")
    ledger = payload.get("current_ledger") if isinstance(payload.get("current_ledger"), dict) else {}
    for key in (
        "total_fact_group_count",
        "single_source_fact_group_count",
        "multi_source_fact_group_count",
        "source_ready_fact_group_count",
    ):
        require(key in ledger, f"verification frontier current_ledger missing {key}")
    ready_gaps = (
        payload.get("source_ready_below_verified")
        if isinstance(payload.get("source_ready_below_verified"), dict)
        else {}
    )
    require("proposal_count" in ready_gaps, "verification frontier missing source-ready gap count")
    require(
        isinstance(ready_gaps.get("proposals"), list),
        "verification frontier source-ready proposals should be a list",
    )
    if ready_gaps["proposals"]:
        first_ready_gap = ready_gaps["proposals"][0]
        require(
            isinstance(first_ready_gap.get("current_sources"), list),
            "verification frontier source-ready gap missing current sources",
        )
        require(
            isinstance(first_ready_gap.get("required_bundle_sources"), list),
            "verification frontier source-ready gap missing required bundle sources",
        )
        require(
            first_ready_gap.get("recording_ready") is False,
            "verification frontier should keep simulated bundles recording_ready=false",
        )
    single_source_gaps = (
        payload.get("single_source_gaps")
        if isinstance(payload.get("single_source_gaps"), dict)
        else {}
    )
    require("proposal_count" in single_source_gaps, "verification frontier missing single-source gap count")
    acquisition = (
        payload.get("source_acquisition_frontier")
        if isinstance(payload.get("source_acquisition_frontier"), dict)
        else {}
    )
    require(
        isinstance(acquisition.get("manager_proposals"), list),
        "verification frontier missing manager source-acquisition proposals",
    )
    require(
        isinstance(acquisition.get("operator_proposals"), list),
        "verification frontier missing operator source-acquisition proposals",
    )
    require(
        "does not mark claims verified" in str(payload.get("safe_action") or ""),
        "verification frontier safe action must state it does not verify claims",
    )


def validate_source_acquisition_worklist(payload: dict[str, Any]) -> None:
    require(payload.get("run_type") == "truth_source_acquisition_worklist", "source worklist missing run type")
    require(payload.get("dry_run") is True, "source worklist must be read-only")
    require(payload.get("mutations_planned") == 0, "source worklist should not plan mutations")
    require(isinstance(payload.get("request_count"), int), "source worklist missing request count")
    require(isinstance(payload.get("work_item_count"), int), "source worklist missing work item count")
    require(isinstance(payload.get("hpd_work_item_count"), int), "source worklist missing HPD work item count")
    require(int(payload.get("recording_ready_count") or 0) == 0, "source worklist should not be recording-ready")
    work_items = payload.get("work_items")
    require(isinstance(work_items, list), "source worklist items should be a list")
    if work_items:
        first = work_items[0]
        require(isinstance(first, dict), "source worklist item should be an object")
        require(isinstance(first.get("relationship"), dict), "source worklist item missing relationship")
        require(first["relationship"].get("bbl") or first["relationship"].get("address"), "source worklist item missing property identity")
        require(isinstance(first.get("source_family_needs"), list), "source worklist item missing source-family needs")
        require(isinstance(first.get("paste_back_template"), dict), "source worklist item missing paste-back template")
        require(isinstance(first.get("paste_back_fields"), list), "source worklist item missing paste-back fields")
        require(
            "Source acquisition only" in str(first.get("safe_action") or ""),
            "source worklist item missing source-acquisition safe action",
        )
    policy = payload.get("policy") if isinstance(payload.get("policy"), dict) else {}
    require(
        "Agent is not manager" in str(policy.get("role_policy") or ""),
        "source worklist policy should preserve role-specific boundary",
    )
    require(
        "not evidence" in str(payload.get("safe_action") or ""),
        "source worklist safe action must state it is not evidence",
    )


def validate_source_overlap_blocker_report(payload: dict[str, Any], *, candidate_preview_checked: bool = False) -> None:
    require(payload.get("run_type") == "truth_source_overlap_blocker_report", "blocker report missing run type")
    require(payload.get("dry_run") is True, "blocker report must be read-only")
    require(payload.get("mutations_planned") == 0, "blocker report should not plan mutations")
    require(
        payload.get("status") in {"blocked_evidence_acquisition_required", "ready_for_manual_recording_review"},
        "blocker report missing acquisition status",
    )
    assessment = payload.get("source_bridge_assessment") if isinstance(payload.get("source_bridge_assessment"), dict) else {}
    require(isinstance(assessment.get("can_record_evidence_now"), bool), "blocker report missing recording assessment")
    require(isinstance(assessment.get("can_mark_verified_now"), bool), "blocker report missing verification assessment")
    require(isinstance(assessment.get("blocking_reasons"), list), "blocker report missing blocking reasons")
    summary = (
        payload.get("source_evidence_candidate_summary")
        if isinstance(payload.get("source_evidence_candidate_summary"), dict)
        else {}
    )
    require(summary, "blocker report missing source-evidence candidate summary")
    require(summary.get("allowed_execute") is False, "blocker report candidate summary must not allow execute")
    require(isinstance(summary.get("recording_ready_count"), int), "blocker report missing candidate recording count")
    require(isinstance(summary.get("recommended_count"), int), "blocker report missing candidate recommended count")
    require(
        "not evidence" in str(payload.get("safe_action") or "").lower(),
        "blocker report safe action should state it is not evidence",
    )
    if candidate_preview_checked:
        require(summary.get("checked") is True, "candidate preview blocker report should mark candidate summary checked")
        require(
            summary.get("status") in {
                "preview_ready_approval_required",
                "preview_ready_non_recommended_scope",
                "no_recording_ready_candidates",
                "source_clue_only_primary_source_required",
            },
            "candidate preview blocker report missing candidate preview status",
        )
        if summary.get("status") == "source_clue_only_primary_source_required":
            require(
                int(summary.get("source_acquisition_clue_count") or 0) > 0,
                "clue-only blocker report should include source acquisition clue count",
            )
            require(
                summary.get("can_record_evidence_now") is False,
                "clue-only blocker report must not be recordable",
            )
        if int(summary.get("recommended_count") or 0) > 0:
            packet = summary.get("recording_approval_packet") if isinstance(summary.get("recording_approval_packet"), dict) else {}
            require(packet.get("approval_required") is True, "candidate preview packet should require approval")
            require(packet.get("allowed_execute") is False, "candidate preview packet should not allow execute")
            expected_overlap = (
                packet.get("expected_post_recording_source_overlap")
                if isinstance(packet.get("expected_post_recording_source_overlap"), dict)
                else {}
            )
            require(
                expected_overlap,
                "candidate preview packet missing expected post-recording source-overlap projection",
            )
            for key in (
                "first_source_only_after_recording_count",
                "multi_source_after_recording_count",
                "source_ready_after_recording_count",
            ):
                require(
                    isinstance(expected_overlap.get(key), int),
                    f"candidate preview packet expected-overlap missing integer {key}",
                )
            require(
                int(packet.get("manual_evidence_payload_count") or 0) == int(summary.get("recommended_count") or 0),
                "candidate preview packet payload count should match recommended count",
            )
            require(
                isinstance(packet.get("manual_evidence_payload_review"), list),
                "candidate preview packet should include manual evidence payload review rows",
            )
            require(
                "--confirm-batch-execute" in str(packet.get("execute_command_after_approval") or ""),
                "candidate preview packet missing batch confirm flag",
            )


def validate_materialization_preview(payload: dict[str, Any]) -> None:
    require(payload.get("dry_run") is True, "materialization preview should be dry-run")
    require(payload.get("mutations_planned") == 0, "materialization preview should plan no mutations")
    planned_claims_total = int(payload.get("planned_claims_total") or 0)
    sample_specs = payload.get("sample_materialized_claim_specs")
    require(isinstance(sample_specs, list), "materialization preview should include sample claim/evidence specs")
    if planned_claims_total <= 0:
        return

    require(sample_specs, "materialization preview with planned claims should include sample claim/evidence specs")
    sample = sample_specs[0]
    require(isinstance(sample, dict), "materialization preview sample spec should be an object")
    for key in (
        "claim_id",
        "evidence_id",
        "subject_type",
        "subject_id",
        "predicate",
        "object_type",
        "object_id",
        "claim_type",
        "source_name",
        "source_record_id",
        "support_status",
        "confidence_score",
        "freshness_days",
        "actionability_level",
    ):
        require(key in sample, f"materialization preview sample spec missing {key}")


def validate_manual_evidence_preview(payload: dict[str, Any]) -> None:
    require(payload.get("dry_run") is True, "manual evidence capture should default to dry-run")
    require(payload.get("allowed_execute") is False, "manual evidence preview should not be executable")
    if payload.get("schema_status") and payload.get("blocked_reason"):
        require(payload.get("mutations_planned") == 0, "schema-gated manual evidence should plan no mutations")
        return

    require(payload.get("run_type") == "manual_evidence_capture", "manual evidence preview missing run type")
    require(int(payload.get("mutations_planned") or 0) > 0, "manual evidence preview should show planned rows")
    claim_spec = payload.get("claim_spec") if isinstance(payload.get("claim_spec"), dict) else {}
    for key in (
        "claim_id",
        "evidence_id",
        "subject_type",
        "subject_id",
        "predicate",
        "object_type",
        "object_id",
        "claim_type",
        "source_name",
        "support_status",
        "confidence_score",
        "actionability_level",
    ):
        require(key in claim_spec, f"manual evidence preview claim_spec missing {key}")
    rollback_plan = payload.get("rollback_plan") if isinstance(payload.get("rollback_plan"), dict) else {}
    require(rollback_plan.get("rollback_strategy"), "manual evidence preview missing rollback strategy")
    required_execute = payload.get("required_execute_params") if isinstance(payload.get("required_execute_params"), dict) else {}
    require(required_execute.get("dry_run") is False, "manual evidence preview missing dry_run=false execute gate")
    require(required_execute.get("confirm_execute") is True, "manual evidence preview missing confirm_execute=true execute gate")


def login(client: ApiClient, *, email: str | None, password: str | None, token: str | None) -> tuple[ApiClient, Check]:
    if token:
        return ApiClient(client.base_url, token=token, timeout=client.timeout), ok(
            "auth",
            "using provided bearer token",
        )
    if not email or not password:
        raise RuntimeError("Set SMOKE_EMAIL/SMOKE_PASSWORD, ADMIN_EMAIL/ADMIN_PASSWORD, or SMOKE_TOKEN/API_TOKEN.")
    payload = client.post("/api/auth/login", {"email": email, "password": password})
    require(isinstance(payload.get("token"), str) and payload["token"], "login response missing token")
    user = payload.get("user") or {}
    return ApiClient(client.base_url, token=payload["token"], timeout=client.timeout), ok(
        "auth",
        "login succeeded",
        path="/api/auth/login",
        evidence={"role": user.get("role"), "email_present": bool(user.get("email"))},
    )


def run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    unauthenticated = ApiClient(args.base_url)
    checks: list[Check] = []
    failures: list[str] = []

    try:
        health = unauthenticated.get("/api/health")
        require(health.get("status") in {"ok", "degraded", "starting"}, "unexpected health status")
        checks.append(ok("health", f"backend health is {health.get('status')}", path="/api/health", evidence=health))
    except Exception as exc:
        checks.append(failed("health", str(exc), path="/api/health"))
        failures.append("health")

    try:
        client, auth_check = login(unauthenticated, email=args.email, password=args.password, token=args.token)
        checks.append(auth_check)
    except Exception as exc:
        checks.append(failed("auth", str(exc), path="/api/auth/login"))
        failures.append("auth")
        return 1, render_report(args, checks, failures)

    lead_id = args.lead_id
    lead_contacts: dict[str, Any] | None = None

    try:
        leads = client.get("/api/leads?" + urlencode({"limit": 1, "count_mode": "estimate"}))
        rows = leads.get("leads") or []
        require(rows, "lead search returned no leads")
        lead_id = lead_id or str(rows[0]["lead_id"])
        checks.append(ok(
            "lead_search",
            f"found {len(rows)} lead sample(s)",
            path="/api/leads",
            evidence={"sample_lead_id": lead_id, "total": leads.get("total")},
        ))
    except Exception as exc:
        checks.append(failed("lead_search", str(exc), path="/api/leads"))
        failures.append("lead_search")

    if lead_id:
        for name, path in {
            "lead_detail": f"/api/leads/{lead_id}",
            "lead_lineage": f"/api/leads/{lead_id}/lineage",
            "lead_truth_summary": f"/api/v1/truth/leads/{lead_id}/summary",
        }.items():
            try:
                payload = client.get(path)
                require(isinstance(payload, dict), f"{name} returned non-object payload")
                if name == "lead_detail":
                    require(str(payload.get("lead_id")) == lead_id, "lead detail returned mismatched lead_id")
                if name == "lead_lineage":
                    require("sources" in payload, "lead lineage missing sources")
                if name == "lead_truth_summary":
                    require("belief_summary" in payload, "lead truth summary missing belief_summary")
                checks.append(ok(name, "contract returned expected shape", path=path, evidence=summarize_payload(payload)))
            except Exception as exc:
                checks.append(failed(name, str(exc), path=path))
                failures.append(name)

        try:
            lead_contacts = client.get(f"/api/leads/{lead_id}/contacts")
            require("buildings" in lead_contacts, "lead contacts missing buildings")
            checks.append(ok(
                "lead_contacts",
                "lead contact roster returned expected shape",
                path=f"/api/leads/{lead_id}/contacts",
                evidence={"building_count": len(lead_contacts.get("buildings") or [])},
            ))
        except Exception as exc:
            checks.append(failed("lead_contacts", str(exc), path=f"/api/leads/{lead_id}/contacts"))
            failures.append("lead_contacts")

    bbl = args.bbl
    if not bbl and lead_contacts:
        bbl = first_bbl_from_lead_contacts(lead_contacts)
    if not bbl and lead_id:
        try:
            buildings = client.get("/api/v1/buildings?" + urlencode({"lead_id": lead_id, "limit": 1}))
            bbl = first_bbl_from_building_list(buildings)
            checks.append(ok(
                "building_lookup",
                "queried building sample by lead",
                path="/api/v1/buildings",
                evidence={"sample_bbl": bbl, "total": buildings.get("total")},
            ))
        except Exception as exc:
            checks.append(failed("building_lookup", str(exc), path="/api/v1/buildings"))
            failures.append("building_lookup")

    if bbl:
        for name, path in {
            "building_detail": f"/api/v1/buildings/{bbl}",
            "building_lineage": f"/api/v1/buildings/{bbl}/lineage",
            "building_truth_summary": f"/api/v1/truth/subjects/building/{bbl}/summary",
        }.items():
            try:
                payload = client.get(path)
                require(isinstance(payload, dict), f"{name} returned non-object payload")
                if name == "building_detail":
                    require(str(payload.get("bbl")) == str(bbl), "building detail returned mismatched bbl")
                if name == "building_lineage":
                    require("sources" in payload, "building lineage missing sources")
                if name == "building_truth_summary":
                    require("belief_summary" in payload, "building truth summary missing belief_summary")
                checks.append(ok(name, "contract returned expected shape", path=path, evidence=summarize_payload(payload)))
            except Exception as exc:
                checks.append(failed(name, str(exc), path=path))
                failures.append(name)
    else:
        checks.append(skipped("building_workflows", "no representative BBL found from lead contacts or building list"))

    recent_job_ids_before: list[int] = []
    review_decision_path: str | None = None

    for name, path in {
        "quality_data_health": "/api/v1/quality/data-health",
        "quality_coverage": "/api/v1/quality/coverage",
        "quality_source_audit": "/api/v1/quality/source-audit",
        "jobs_recent_before_previews": "/api/v1/jobs?limit=5",
        "truth_dashboard": "/api/v1/truth/dashboard",
        "truth_health_report": "/api/v1/truth/health-report",
        "truth_activation_packet": "/api/v1/truth/activation-packet",
        "truth_completion_audit": "/api/v1/truth/completion-audit",
        "truth_source_overlap_approval_packet": "/api/v1/truth/source-overlap-approval-packet",
        "truth_source_overlap_post_recording_check": "/api/v1/truth/source-overlap-post-recording-check",
        "truth_manager_source_acquisition_packet": "/api/v1/truth/manager-source-acquisition-packet",
        "truth_verification_frontier": "/api/v1/truth/verification-frontier?limit=5",
        "truth_source_acquisition_worklist": "/api/v1/truth/source-acquisition-worklist?max_items=5",
        "truth_source_overlap_blocker_report": "/api/v1/truth/source-overlap-blocker-report?max_items=5",
        "truth_adjudication_preview": "/api/v1/truth/adjudication-preview?limit=10",
        "truth_review_queue": "/api/v1/truth/review-queue?limit=5",
        "truth_golden_benchmark": "/api/v1/truth/golden-benchmark",
    }.items():
        try:
            payload = client.get(path)
            if name == "jobs_recent_before_previews":
                recent_job_ids_before = job_ids(payload)
                checks.append(ok(
                    name,
                    "captured recent job ids before preview calls",
                    path=path,
                    evidence={"recent_job_ids": recent_job_ids_before},
                ))
            else:
                require(isinstance(payload, dict), f"{name} returned non-object payload")
                if name == "truth_activation_packet":
                    validate_activation_packet(payload)
                if name == "truth_completion_audit":
                    validate_completion_audit(payload)
                if name == "truth_source_overlap_approval_packet":
                    validate_source_overlap_approval_packet(payload)
                if name == "truth_source_overlap_post_recording_check":
                    validate_source_overlap_post_recording_check(payload)
                if name == "truth_manager_source_acquisition_packet":
                    validate_manager_source_acquisition_packet(payload)
                if name == "truth_verification_frontier":
                    validate_verification_frontier(payload)
                if name == "truth_source_acquisition_worklist":
                    validate_source_acquisition_worklist(payload)
                if name == "truth_source_overlap_blocker_report":
                    validate_source_overlap_blocker_report(payload)
                if name == "truth_adjudication_preview":
                    require(payload.get("dry_run") is True, "adjudication preview is not marked dry_run")
                    require(payload.get("mutations_planned") == 0, "adjudication preview planned mutations")
                    require("verification_candidate_count" in payload, "adjudication preview missing verification candidate count")
                    source_coverage = payload.get("source_coverage")
                    require(isinstance(source_coverage, dict), "adjudication preview missing source coverage")
                    require("multi_source_fact_group_count" in source_coverage, "source coverage missing multi-source count")
                    require("single_source_fact_group_count" in source_coverage, "source coverage missing single-source count")
                    gap_plan = payload.get("verification_gap_plan")
                    require(isinstance(gap_plan, dict), "adjudication preview missing verification gap plan")
                    require(gap_plan.get("dry_run") is True, "verification gap plan should be dry-run")
                    require(gap_plan.get("mutations_planned") == 0, "verification gap plan planned mutations")
                    confidence_gap_plan = payload.get("verified_confidence_gap_plan")
                    require(
                        isinstance(confidence_gap_plan, dict),
                        "adjudication preview missing verified confidence gap plan",
                    )
                    require(confidence_gap_plan.get("dry_run") is True, "verified confidence gap plan should be dry-run")
                    require(
                        confidence_gap_plan.get("mutations_planned") == 0,
                        "verified confidence gap plan planned mutations",
                    )
                    if "single_source_upgrade_would_verify_count" in confidence_gap_plan:
                        require(
                            isinstance(confidence_gap_plan.get("single_source_upgrade_would_verify_count"), int),
                            "verified confidence gap plan missing one-source verification count",
                        )
                    if "best_single_source_upgrade_overall" in confidence_gap_plan:
                        best_overall = confidence_gap_plan.get("best_single_source_upgrade_overall")
                        require(
                            best_overall is None or isinstance(best_overall, dict),
                            "verified confidence gap plan best overall upgrade should be an object or null",
                        )
                    if "bundle_upgrade_would_verify_count" in confidence_gap_plan:
                        require(
                            isinstance(confidence_gap_plan.get("bundle_upgrade_would_verify_count"), int),
                            "verified confidence gap plan missing bundle verification count",
                        )
                    confidence_gap_proposals = confidence_gap_plan.get("proposals")
                    if isinstance(confidence_gap_proposals, list) and confidence_gap_proposals:
                        first_proposal = confidence_gap_proposals[0]
                        require(
                            isinstance(first_proposal.get("simulated_quality_upgrades"), list),
                            "verified confidence gap proposal missing one-source simulations",
                        )
                        require(
                            isinstance(first_proposal.get("single_source_upgrade_would_verify"), bool),
                            "verified confidence gap proposal missing single-source verification result",
                        )
                        best_upgrade = first_proposal.get("best_single_source_upgrade")
                        require(
                            isinstance(best_upgrade, dict),
                            "verified confidence gap proposal missing best one-source upgrade",
                        )
                        require(
                            isinstance(best_upgrade.get("would_reach_verified_threshold"), bool),
                            "verified confidence gap best upgrade missing threshold result",
                        )
                        bundle_upgrade = first_proposal.get("simulated_quality_bundle_upgrade")
                        require(
                            isinstance(bundle_upgrade, dict),
                            "verified confidence gap proposal missing suggested-bundle simulation",
                        )
                        require(
                            isinstance(bundle_upgrade.get("would_reach_verified_threshold"), bool),
                            "verified confidence gap bundle missing threshold result",
                        )
                if name == "truth_review_queue":
                    items = payload.get("items") if isinstance(payload.get("items"), list) else []
                    for item in items:
                        if isinstance(item, dict) and item.get("review_id"):
                            review_decision_path = f"/api/v1/truth/review-queue/{item['review_id']}/decision"
                            break
                checks.append(ok(name, "contract returned expected shape", path=path, evidence=summarize_payload(payload)))
        except Exception as exc:
            checks.append(failed(name, str(exc), path=path))
            failures.append(name)

    preview_posts = [
        ("truth_validation_preview", "/api/v1/truth/validate/preview?sample_limit=5", {}),
        ("truth_materialize_preview", "/api/v1/truth/materialize/preview?limit=5", {}),
        ("truth_manual_evidence_preview", "/api/v1/truth/manual-evidence", {
            "subject_type": "lead",
            "subject_id": lead_id or "manual-evidence-smoke-lead",
            "predicate": "manages_building",
            "object_type": "building",
            "object_id": bbl or "manual-evidence-smoke-building",
            "normalized_value": "manager",
            "claim_type": "building_management",
            "support_status": "supports",
            "source_name": "manual_evidence",
            "source_type": "operator_review",
            "source_record_id": "smoke-preview-only",
            "note": "Read-only API smoke preview; do not execute.",
            "dry_run": True,
            "confirm_execute": False,
        }),
        ("truth_adjudication_apply_preview", "/api/v1/truth/adjudication/apply", {
            "limit": 10,
            "dry_run": True,
            "confirm_execute": False,
        }),
        ("truth_source_overlap_blocker_report_preview", "/api/v1/truth/source-overlap-blocker-report/preview?max_items=5", {
            "candidates": [],
            "source_mode": "api_smoke_empty_candidate_list",
            "recommended_scope_only": True,
        }),
    ]
    if review_decision_path:
        preview_posts.append((
            "truth_review_decision_preview",
            review_decision_path,
            {"decision": "needs_more_evidence", "dry_run": True, "confirm_execute": False},
        ))
    else:
        checks.append(skipped(
            "truth_review_decision_preview",
            "no review item available for dry-run decision preview",
            path="/api/v1/truth/review-queue",
        ))

    for name, path, body in preview_posts:
        try:
            payload = client.post(path, body)
            require(isinstance(payload, dict), f"{name} returned non-object payload")
            require(
                payload.get("mutations_planned", 0) == 0 or payload.get("dry_run") is True,
                f"{name} did not present a read-only/dry-run contract",
            )
            if name == "truth_materialize_preview":
                validate_materialization_preview(payload)
            if name == "truth_manual_evidence_preview":
                validate_manual_evidence_preview(payload)
            if name == "truth_adjudication_apply_preview":
                require(payload.get("dry_run") is True, "adjudication apply preview should be dry-run")
                require(payload.get("allowed_execute") is False, "adjudication apply preview should not execute by default")
                require("candidate_summary" in payload or payload.get("schema_status"), "adjudication apply preview missing candidate summary")
            if name == "truth_source_overlap_blocker_report_preview":
                validate_source_overlap_blocker_report(payload, candidate_preview_checked=True)
            checks.append(ok(name, "dry-run/schema-gated contract returned expected shape", path=path, evidence=summarize_payload(payload)))
        except Exception as exc:
            checks.append(failed(name, str(exc), path=path))
            failures.append(name)

    for name, path in {
        "job_start_source_refresh_preview": "/api/v1/jobs/building_coordinates/start?limit=5",
        "job_start_scoring_preview": "/api/v1/jobs/scoring/start?limit=5",
        "job_start_truth_materialization_gate": "/api/v1/jobs/truth_materialization/start?limit=5",
    }.items():
        try:
            payload = client.post(path, {})
            require(isinstance(payload, dict), f"{name} returned non-object payload")
            require(payload.get("job_id") in (None, 0), f"{name} unexpectedly queued a job")
            require(
                payload.get("status") in {"approval_required", "schema_not_ready"},
                f"{name} did not return an approval/schema gate",
            )
            require(payload.get("dry_run") is True, f"{name} was not dry-run by default")
            checks.append(ok(name, "job start returned non-queueing approval/schema gate", path=path, evidence=summarize_payload(payload)))
        except Exception as exc:
            checks.append(failed(name, str(exc), path=path))
            failures.append(name)

    try:
        recent_jobs_after = client.get("/api/v1/jobs?limit=5")
        recent_job_ids_after = job_ids(recent_jobs_after)
        require(
            recent_job_ids_after == recent_job_ids_before,
            f"preview calls changed recent job ids: before={recent_job_ids_before}, after={recent_job_ids_after}",
        )
        checks.append(ok(
            "jobs_recent_after_previews",
            "recent job ids unchanged after preview calls",
            path="/api/v1/jobs?limit=5",
            evidence={"recent_job_ids": recent_job_ids_after},
        ))
    except Exception as exc:
        checks.append(failed("jobs_recent_after_previews", str(exc), path="/api/v1/jobs?limit=5"))
        failures.append("jobs_recent_after_previews")

    report = render_report(args, checks, failures, lead_id=lead_id, bbl=bbl)
    return (1 if failures else 0), report


def summarize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in (
        "lead_id",
        "bbl",
        "total_leads",
        "total_buildings_registered",
        "total_buildings",
        "coverage_percent",
        "data_age_days",
        "dry_run",
        "mutations_planned",
        "status",
        "job_type",
        "requested_job_type",
        "job_id",
        "safe_to_run_automatically",
        "review_bucket",
        "overall_confidence_score",
        "source",
        "verdict",
        "business_use_allowed",
        "approval_required",
        "trust_posture",
        "completion_status",
        "post_recording_success",
    ):
        if key in payload:
            summary[key] = payload.get(key)
    if "schema_status" in payload:
        schema = payload.get("schema_status") or {}
        summary["schema_status"] = {
            "ready": schema.get("ready"),
            "current_revision": schema.get("current_revision"),
            "expected_revision": schema.get("expected_revision"),
            "missing_tables": schema.get("missing_tables"),
        }
    if "schema" in payload:
        schema = payload.get("schema") or {}
        summary["schema"] = {
            "ready": schema.get("ready"),
            "current_revision": schema.get("current_revision"),
            "expected_revision": schema.get("expected_revision"),
            "missing_tables": schema.get("missing_tables"),
        }
    if "artifact_summary" in payload and isinstance(payload["artifact_summary"], dict):
        artifact_summary = payload["artifact_summary"]
        summary["artifact_summary"] = {
            k: artifact_summary.get(k)
            for k in ("total", "satisfied", "missing")
            if k in artifact_summary
        }
    if "runtime_blockers" in payload and isinstance(payload["runtime_blockers"], list):
        blockers = [blocker for blocker in payload["runtime_blockers"] if isinstance(blocker, dict)]
        summary["runtime_blockers"] = {
            "count": len(blockers),
            "gates": [str(blocker.get("gate")) for blocker in blockers if blocker.get("gate")],
        }
    if "prompt_to_artifact_checklist" in payload and isinstance(payload["prompt_to_artifact_checklist"], list):
        checklist = [item for item in payload["prompt_to_artifact_checklist"] if isinstance(item, dict)]
        status_counts: dict[str, int] = {}
        for item in checklist:
            status = str(item.get("status") or "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
        summary["prompt_to_artifact_checklist"] = {
            "count": len(checklist),
            "status_counts": dict(sorted(status_counts.items())),
        }
    if "summary" in payload and isinstance(payload["summary"], dict):
        summary["summary"] = {
            k: payload["summary"].get(k)
            for k in (
                "trust_posture",
                "claim_count",
                "critical_or_high_gap_count",
                "planned_claims_total",
                "total_sources",
                "operational",
                "schema_missing",
                "no_recent_ingest",
                "stale_ingest",
            )
            if k in payload["summary"]
        }
    if "blocked_reason" in payload:
        summary["blocked_reason"] = payload.get("blocked_reason")
    if "source_coverage" in payload and isinstance(payload["source_coverage"], dict):
        coverage = payload["source_coverage"]
        summary["source_coverage"] = {
            k: coverage.get(k)
            for k in (
                "sampled_fact_group_count",
                "single_source_fact_group_count",
                "multi_source_fact_group_count",
                "max_supporting_source_count",
                "verification_blocker",
            )
            if k in coverage
        }
    if "verification_gap_plan" in payload and isinstance(payload["verification_gap_plan"], dict):
        plan = payload["verification_gap_plan"]
        summary["verification_gap_plan"] = {
            k: plan.get(k)
            for k in ("dry_run", "mutations_planned", "proposal_count")
            if k in plan
        }
    if "current_ledger" in payload and isinstance(payload["current_ledger"], dict):
        ledger = payload["current_ledger"]
        summary["current_ledger"] = {
            k: ledger.get(k)
            for k in (
                "total_fact_group_count",
                "single_source_fact_group_count",
                "multi_source_fact_group_count",
                "source_ready_fact_group_count",
                "verification_candidate_count",
            )
            if k in ledger
        }
    if "source_overlap_recording_gate" in payload and isinstance(payload["source_overlap_recording_gate"], dict):
        recording_gate = payload["source_overlap_recording_gate"]
        summary["source_overlap_recording_gate"] = {
            k: recording_gate.get(k)
            for k in (
                "status",
                "current_multi_source_fact_group_count",
                "current_source_ready_fact_group_count",
                "current_verification_candidate_count",
                "source_overlap_proof_satisfied",
                "additional_evidence_recording_requires_approval",
            )
            if k in recording_gate
        }
    if (
        "source_evidence_candidate_summary" in payload
        and isinstance(payload["source_evidence_candidate_summary"], dict)
    ):
        candidate_summary = payload["source_evidence_candidate_summary"]
        compact_candidate_summary = {
            k: candidate_summary.get(k)
            for k in (
                "status",
                "checked",
                "source_mode",
                "candidate_count",
                "source_acquisition_clue_count",
                "recording_ready_count",
                "recommended_count",
                "allowed_execute",
                "can_record_evidence_now",
            )
            if k in candidate_summary
        }
        packet = (
            candidate_summary.get("recording_approval_packet")
            if isinstance(candidate_summary.get("recording_approval_packet"), dict)
            else {}
        )
        if packet:
            compact_candidate_summary["recording_approval_packet"] = {
                k: packet.get(k)
                for k in (
                    "status",
                    "approval_required",
                    "allowed_execute",
                    "recommended_count",
                    "manual_evidence_payload_count",
                    "approval_scope",
                    "expected_post_recording_source_overlap",
                )
                if k in packet
            }
        summary["source_evidence_candidate_summary"] = compact_candidate_summary
    if "previewed_overlap_if_approved" in payload and isinstance(payload["previewed_overlap_if_approved"], dict):
        previewed = payload["previewed_overlap_if_approved"]
        summary["previewed_overlap_if_approved"] = {
            k: previewed.get(k)
            for k in (
                "manager_strict_source_ready_if_recorded_count",
                "operator_strict_source_ready_if_recorded_count",
                "safe_to_mark_verified_after_recording",
            )
            if k in previewed
        }
    if "recommended_first_packet" in payload and isinstance(payload["recommended_first_packet"], dict):
        packet = payload["recommended_first_packet"]
        summary["recommended_first_packet"] = {
            k: packet.get(k)
            for k in (
                "batch_filter",
                "approval_required",
                "approval_required_before_recording",
                "allowed_execute",
                "template_count",
                "claim_group_count",
                "manager_proof_source_families",
                "planned_upsert_count_if_approved",
            )
            if k in packet
        }
        if isinstance(packet.get("approval_decision_summary"), dict):
            approval_summary = packet["approval_decision_summary"]
            summary["recommended_first_packet"]["approval_decision_summary"] = {
                k: approval_summary.get(k)
                for k in (
                    "would_record_template_count",
                    "would_record_claim_group_count",
                    "would_plan_upsert_count",
                    "expected_source_ready_fact_group_count",
                    "expected_safe_to_mark_verified_count",
                    "single_source_claims_stay_unverified",
                    "will_mark_verified",
                    "will_create_or_refresh_source_data",
                    "will_materialize_new_relationships",
                )
                if k in approval_summary
            }
    if "manager_strict_gap_summary" in payload and isinstance(payload["manager_strict_gap_summary"], dict):
        gap_summary = payload["manager_strict_gap_summary"]
        summary["manager_strict_gap_summary"] = {
            k: gap_summary.get(k)
            for k in (
                "claim_group_count",
                "strict_ready_claim_group_count",
                "broad_source_ready_not_strict_count",
            )
            if k in gap_summary
        }
    if (
        "manager_new_relationship_candidate_summary" in payload
        and isinstance(payload["manager_new_relationship_candidate_summary"], dict)
    ):
        relationship_summary = payload["manager_new_relationship_candidate_summary"]
        summary["manager_new_relationship_candidate_summary"] = {
            k: relationship_summary.get(k)
            for k in (
                "candidate_count",
                "counts_as_current_ledger_overlap",
                "approval_required_for_relationship_creation",
                "source_family_counts",
            )
            if k in relationship_summary
        }
    if "operator_strict_packet" in payload and isinstance(payload["operator_strict_packet"], dict):
        packet = payload["operator_strict_packet"]
        summary["operator_strict_packet"] = {
            k: packet.get(k)
            for k in (
                "batch_filter",
                "approval_required",
                "approval_required_before_recording",
                "allowed_execute",
                "template_count",
                "claim_group_count",
                "manager_proof_source_families",
                "planned_upsert_count_if_approved",
                "excluded_non_strict_candidate_count",
            )
            if k in packet
        }
        if isinstance(packet.get("approval_decision_summary"), dict):
            approval_summary = packet["approval_decision_summary"]
            summary["operator_strict_packet"]["approval_decision_summary"] = {
                k: approval_summary.get(k)
                for k in (
                    "would_record_template_count",
                    "would_record_claim_group_count",
                    "would_plan_upsert_count",
                    "expected_source_ready_fact_group_count",
                    "expected_safe_to_mark_verified_count",
                    "single_source_claims_stay_unverified",
                    "will_mark_verified",
                    "will_create_or_refresh_source_data",
                    "will_materialize_new_relationships",
                )
                if k in approval_summary
            }
    if "operator_strict_gap_summary" in payload and isinstance(payload["operator_strict_gap_summary"], dict):
        gap_summary = payload["operator_strict_gap_summary"]
        summary["operator_strict_gap_summary"] = {
            k: gap_summary.get(k)
            for k in (
                "candidate_count",
                "strict_ready_candidate_count",
                "broad_source_ready_not_strict_count",
            )
            if k in gap_summary
        }
    if "sample_materialized_claim_specs" in payload and isinstance(payload["sample_materialized_claim_specs"], list):
        summary["sample_materialized_claim_spec_count"] = len(payload["sample_materialized_claim_specs"])
    if "allowed_execute" in payload:
        summary["allowed_execute"] = payload.get("allowed_execute")
    if "preview" in payload and isinstance(payload["preview"], dict):
        preview = payload["preview"]
        summary["preview"] = {
            k: preview.get(k)
            for k in ("operation", "would_enqueue_job_type", "required_execute_query")
            if k in preview
        }
    if "truth_confidence" in payload and isinstance(payload["truth_confidence"], dict):
        truth_confidence = payload["truth_confidence"]
        summary["truth_confidence"] = {
            k: truth_confidence.get(k)
            for k in ("claim_count", "verified_claim_count", "conflicting_claim_count", "open_review_count")
            if k in truth_confidence
        }
        if isinstance(truth_confidence.get("schema_status"), dict):
            schema = truth_confidence["schema_status"]
            summary["truth_confidence"]["schema_status"] = {
                "ready": schema.get("ready"),
                "current_revision": schema.get("current_revision"),
                "expected_revision": schema.get("expected_revision"),
                "missing_tables": schema.get("missing_tables"),
            }
    if "source_refresh" in payload and isinstance(payload["source_refresh"], dict):
        source_refresh = payload["source_refresh"]
        next_jobs = source_refresh.get("next_jobs") if isinstance(source_refresh.get("next_jobs"), list) else []
        summary["source_refresh"] = {
            k: source_refresh.get(k)
            for k in (
                "approval_required",
                "planned_job_count",
                "refreshable_job_count",
                "blocked_job_count",
                "affected_source_count",
                "non_refreshable_gap_count",
            )
            if k in source_refresh
        }
        summary["source_refresh"]["next_job_count"] = len(next_jobs)
    if "claim_readiness" in payload and isinstance(payload["claim_readiness"], dict):
        claim_readiness = payload["claim_readiness"]
        summary["claim_readiness"] = {
            k: claim_readiness.get(k)
            for k in (
                "claim_count",
                "verified_claim_count",
                "critical_or_high_gap_count",
                "has_materialized_claims",
                "has_verified_claims",
                "has_no_critical_or_high_gaps",
            )
            if k in claim_readiness
        }
    if "next_safe_steps" in payload and isinstance(payload["next_safe_steps"], list):
        steps = [step for step in payload["next_safe_steps"] if isinstance(step, dict)]
        summary["next_safe_steps"] = {
            "count": len(steps),
            "mutating_approval_required_count": sum(
                1 for step in steps if step.get("mutates_data") is True and step.get("requires_explicit_approval") is True
            ),
            "non_mutating_count": sum(1 for step in steps if step.get("mutates_data") is False),
        }
    if "rollback" in payload and isinstance(payload["rollback"], dict):
        rollback = payload["rollback"]
        summary["rollback"] = {
            "offline_rollback_command_present": bool(rollback.get("offline_rollback_command")),
        }
    if "warnings" in payload and isinstance(payload["warnings"], list):
        summary["warning_count"] = len(payload["warnings"])
    if "items" in payload and isinstance(payload["items"], list):
        summary["item_count"] = len(payload["items"])
    if "sources" in payload and isinstance(payload["sources"], list):
        summary["source_count"] = len(payload["sources"])
    if "claims" in payload and isinstance(payload["claims"], list):
        summary["claim_count"] = len(payload["claims"])
    if "buildings" in payload and isinstance(payload["buildings"], list):
        summary["building_count"] = len(payload["buildings"])
    return summary


def render_report(
    args: argparse.Namespace,
    checks: list[Check],
    failures: list[str],
    *,
    lead_id: str | None = None,
    bbl: str | None = None,
) -> dict[str, Any]:
    return {
        "base_url": args.base_url.rstrip("/"),
        "dry_run": True,
        "mutations_planned": 0,
        "status": "failed" if failures else "passed",
        "sample": {"lead_id": lead_id, "bbl": bbl},
        "check_count": len(checks),
        "failures": failures,
        "checks": [
            {
                "name": check.name,
                "status": check.status,
                "detail": check.detail,
                "path": check.path,
                "evidence": check.evidence,
            }
            for check in checks
        ],
    }


def main() -> int:
    args = parse_args()
    exit_code, report = run(args)
    print(json.dumps(report, indent=args.indent, default=str))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
