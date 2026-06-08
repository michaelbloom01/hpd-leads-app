"""Read-only verification frontier for source-ready facts that still are not verified."""

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

from scripts.truth_manager_external_evidence_batch import build_manager_source_acquisition_packet  # noqa: E402
from scripts.truth_operator_confirmed_evidence_batch import build_operator_source_acquisition_packet  # noqa: E402
from scripts.truth_live_hpd_role_audit import (  # noqa: E402
    CONTACTS_DOWNLOAD_URL,
    REGISTRATIONS_DOWNLOAD_URL,
    official_query_urls_for_target,
)
from src.db.session import get_session_factory, shutdown_engine  # noqa: E402
from src.services.truth_adjudication import (  # noqa: E402
    load_claim_adjudication_preview,
    load_manager_external_source_acquisition_preview,
    load_operator_confirmed_management_preview,
)
from src.services.truth_health import build_schema_readiness_report, is_truth_schema_current, load_truth_schema_status  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lead-id", default="0ff794d3ba2d")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--indent", type=int, default=2)
    return parser.parse_args()


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _compact_reviewed_source_finding(finding: Any) -> dict[str, Any] | None:
    if not isinstance(finding, dict):
        return None
    compacted = {
        "source_family": finding.get("source_family"),
        "source_urls": _as_list(finding.get("source_urls"))[:5],
        "finding": finding.get("finding"),
        "qualification": finding.get("qualification"),
    }
    if not any(compacted.values()):
        return None
    return compacted


def _compact_reviewed_source_findings(findings: Any, *, limit: int = 4) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for finding in reversed(_as_list(findings)):
        compact = _compact_reviewed_source_finding(finding)
        if compact:
            compacted.append(compact)
        if len(compacted) >= limit:
            break
    return compacted


def _reviewed_finding_text(finding: dict[str, Any]) -> str:
    return " ".join(
        str(value or "")
        for value in (
            finding.get("source_family"),
            finding.get("finding"),
            finding.get("qualification"),
            " ".join(str(url) for url in _as_list(finding.get("source_urls"))),
        )
    ).lower()


def _reviewed_source_priority_keywords(gap: dict[str, Any]) -> list[str]:
    display = gap.get("display") if isinstance(gap.get("display"), dict) else {}
    fact_key = gap.get("fact_key") if isinstance(gap.get("fact_key"), dict) else {}
    building = display.get("building") if isinstance(display.get("building"), dict) else {}
    bundle_sources = gap.get("required_bundle_sources")
    if not isinstance(bundle_sources, list):
        bundle_sources = []
    sources = [
        display.get("relationship_label"),
        display.get("subject_label"),
        display.get("object_label"),
        building.get("address"),
        building.get("bbl"),
        fact_key.get("object_id"),
        fact_key.get("subject_id"),
        *bundle_sources,
    ]
    keywords = [str(source).lower().strip() for source in sources if str(source or "").strip()]
    best_single_source = gap.get("best_single_source_upgrade")
    suggested_source = ""
    if isinstance(best_single_source, dict):
        suggested_source = str(best_single_source.get("suggested_source") or "").lower()
    if suggested_source == "hpd_management_company" or "hpd_management_company" in keywords:
        keywords.extend([
            "hpd",
            "managementcompany",
            "management company",
            "threshold_candidate",
            "threshold candidate",
            "live_hpd",
            "official_hpd",
        ])
    return keywords


def _prioritized_reviewed_source_findings(
    findings: Any,
    *,
    gap: dict[str, Any],
    limit: int = 3,
) -> list[dict[str, Any]]:
    keywords = _reviewed_source_priority_keywords(gap)
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for index, finding in enumerate(_as_list(findings)):
        compact = _compact_reviewed_source_finding(finding)
        if not compact:
            continue
        finding_text = _reviewed_finding_text(compact)
        score = sum(1 for keyword in keywords if keyword and keyword in finding_text)
        source_family = str(compact.get("source_family") or "").lower()
        if "threshold_candidate" in source_family:
            score += 8
        if "live_hpd" in source_family or "official_hpd" in source_family:
            score += 4
        ranked.append((score, index, compact))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [compact for _, _, compact in ranked[:limit]]


def _reviewed_source_history_status(
    reviewed_source_findings: list[dict[str, Any]],
    *,
    recording_ready: bool,
) -> str | None:
    if not reviewed_source_findings:
        return None
    if recording_ready:
        return "reviewed_source_history_available"
    return "reviewed_dead_end_no_recording_ready_source"


def _compact_fact_key(fact_key: Any) -> dict[str, Any]:
    if not isinstance(fact_key, dict):
        return {}
    return {
        key: fact_key.get(key)
        for key in (
            "subject_type",
            "subject_id",
            "predicate",
            "object_type",
            "object_id",
            "claim_type",
            "normalized_value",
        )
        if fact_key.get(key) is not None
    }


def _display_name_from_lead_context(lead_context: dict[str, Any]) -> str | None:
    for key in ("company_name", "agent_name", "normalized_name", "lead_id"):
        value = str(lead_context.get(key) or "").strip()
        if value:
            return value
    return None


def _build_fact_display_context(
    fact_key: dict[str, Any],
    *,
    display_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    display_context = display_context if isinstance(display_context, dict) else {}
    buildings_by_bbl = display_context.get("buildings_by_bbl")
    if not isinstance(buildings_by_bbl, dict):
        buildings_by_bbl = {}
    leads_by_id = display_context.get("leads_by_id")
    if not isinstance(leads_by_id, dict):
        leads_by_id = {}

    subject_id = str(fact_key.get("subject_id") or "")
    object_id = str(fact_key.get("object_id") or "")
    building = buildings_by_bbl.get(object_id) if object_id else None
    if not isinstance(building, dict):
        building = {}
    lead = leads_by_id.get(subject_id) if subject_id else None
    if not isinstance(lead, dict):
        lead = {}

    object_label = str(building.get("address") or object_id or fact_key.get("normalized_value") or "").strip()
    subject_label = _display_name_from_lead_context(lead) or subject_id or str(fact_key.get("subject_type") or "")
    predicate_label = str(fact_key.get("predicate") or "").replace("_", " ").strip()
    relationship_label = " ".join(part for part in (subject_label, predicate_label, object_label) if part)
    return {
        "subject_label": subject_label,
        "predicate_label": predicate_label,
        "object_label": object_label,
        "relationship_label": relationship_label,
        "building": {
            key: building.get(key)
            for key in ("bbl", "address", "borough", "unit_count")
            if building.get(key) is not None
        },
    }


def _compact_source_ready_gap(
    proposal: dict[str, Any],
    *,
    display_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fact_key = _compact_fact_key(proposal.get("fact_key"))
    display = _build_fact_display_context(fact_key, display_context=display_context)
    best_single_source = proposal.get("best_single_source_upgrade")
    if not isinstance(best_single_source, dict):
        best_single_source = {}
    bundle = proposal.get("simulated_quality_bundle_upgrade")
    if not isinstance(bundle, dict):
        bundle = {}
    required_real_evidence = [
        _with_hpd_management_acquisition_guidance(
            evidence,
            bbl=fact_key.get("object_id") or (display.get("building") or {}).get("bbl"),
            expected_manager=display.get("subject_label") or fact_key.get("subject_id"),
        )
        for evidence in _as_list(bundle.get("required_real_evidence"))
        if isinstance(evidence, dict)
    ]
    evidence_acquisition_status = (
        "acquisition_required"
        if bundle.get("acquisition_required") is True or required_real_evidence
        else "review_required"
    )
    return {
        "fact_key": fact_key,
        "display": display,
        "current_sources": _as_list(proposal.get("current_sources")),
        "supporting_source_count": proposal.get("supporting_source_count"),
        "supporting_evidence_count": proposal.get("supporting_evidence_count"),
        "recomputed_confidence_score": proposal.get("recomputed_confidence_score"),
        "verified_confidence_threshold": proposal.get("verified_confidence_threshold"),
        "score_gap_to_verified": proposal.get("score_gap_to_verified"),
        "best_single_source_upgrade": {
            "suggested_source": best_single_source.get("suggested_source"),
            "simulated_confidence_score": best_single_source.get("simulated_confidence_score"),
            "would_reach_verified_threshold": best_single_source.get("would_reach_verified_threshold"),
        },
        "bundle_upgrade_would_verify": bundle.get("would_reach_verified_threshold") is True,
        "bundle_simulated_confidence_score": bundle.get("simulated_confidence_score"),
        "required_bundle_sources": _as_list(bundle.get("suggested_sources")),
        "required_real_evidence": required_real_evidence,
        "required_real_evidence_count": len(required_real_evidence),
        "evidence_acquisition_status": evidence_acquisition_status,
        "recording_ready": bundle.get("recording_ready") is True,
        "approval_required_before_recording": bundle.get("approval_required_before_recording") is True,
        "manual_evidence_template": proposal.get("manual_evidence_template"),
        "safe_action": proposal.get("safe_action"),
    }


def _compact_single_source_gap(
    proposal: dict[str, Any],
    *,
    display_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fact_key = _compact_fact_key(proposal.get("fact_key"))
    return {
        "fact_key": fact_key,
        "display": _build_fact_display_context(fact_key, display_context=display_context),
        "current_sources": _as_list(proposal.get("current_sources")),
        "supporting_source_count": proposal.get("supporting_source_count"),
        "supporting_evidence_count": proposal.get("supporting_evidence_count"),
        "recomputed_confidence_score": proposal.get("recomputed_confidence_score"),
        "missing_source_count": proposal.get("missing_source_count"),
        "missing_evidence_count": proposal.get("missing_evidence_count"),
        "suggested_sources": _as_list(proposal.get("suggested_sources"))
        or _as_list(proposal.get("suggested_quality_upgrade_sources")),
        "safe_action": proposal.get("safe_action"),
        "manual_evidence_template": proposal.get("manual_evidence_template"),
    }


def _compact_source_acquisition_proposal(
    proposal: dict[str, Any],
    *,
    manager_lead_id: str | None = None,
    manager_name: str | None = None,
) -> dict[str, Any]:
    return {
        "candidate_id": proposal.get("candidate_id"),
        "bbl": proposal.get("bbl"),
        "address": proposal.get("address"),
        "manager_lead_id": proposal.get("manager_lead_id") or manager_lead_id,
        "manager_name": proposal.get("manager_name") or manager_name,
        "existing_manager_proof_source_families": _as_list(proposal.get("existing_manager_proof_source_families")),
        "supporting_source_families_if_recorded": _as_list(
            proposal.get("supporting_source_families_if_recorded")
            or proposal.get("existing_source_families_if_recorded")
        ),
        "strict_manager_source_ready_if_recorded": proposal.get("strict_manager_source_ready_if_recorded") is True,
        "strict_manager_gap_status": proposal.get("strict_manager_gap_status"),
        "strict_manager_gap_reason": proposal.get("strict_manager_gap_reason"),
        "missing_manager_proof_source_family_count": proposal.get("missing_manager_proof_source_family_count"),
        "suggested_source_families": _as_list(proposal.get("suggested_source_families")),
        "first_search_query": proposal.get("first_search_query"),
        "search_queries": _as_list(proposal.get("search_queries"))[:5],
        "source_targets": _as_list(proposal.get("source_targets"))[:5],
        "current_relationship_state": proposal.get("current_relationship_state") or {},
        "next_required_manager_proof": proposal.get("next_required_manager_proof"),
        "safe_action": proposal.get("safe_action"),
    }


def _evidence_request_policy() -> dict[str, Any]:
    return {
        "single_source_policy": "No single-source claim may be marked verified.",
        "role_policy": (
            "HPD Agent, SiteManager, CorporateOwner, HeadOfficer, building_management, NY DOS, "
            "company website, and outreach-confirmed evidence stay role-specific. Agent is not manager."
        ),
        "property_match_policy": (
            "A source can support a manages_building claim only when it names the exact property and "
            "states a property-management or managing-agent relationship."
        ),
        "execution_policy": (
            "This packet is read-only. Manual evidence still needs source review, preview, explicit approval, "
            "recording, and adjudication before any status change."
        ),
    }


def _relationship_from_acquisition_request(proposal: dict[str, Any]) -> dict[str, Any]:
    manager_name = str(proposal.get("manager_name") or "").strip()
    address = str(proposal.get("address") or "").strip()
    bbl = str(proposal.get("bbl") or "").strip()
    relationship_label = " ".join(
        part
        for part in (
            manager_name,
            "manages building" if manager_name and (address or bbl) else "",
            address or bbl,
        )
        if part
    )
    return {
        "manager_name": manager_name or None,
        "manager_lead_id": proposal.get("manager_lead_id"),
        "address": address or None,
        "bbl": bbl or None,
        "relationship_label": relationship_label or None,
    }


def _powershell_quote(value: Any) -> str:
    return '"' + str(value or "").replace("`", "``").replace('"', '`"') + '"'


def _hpd_management_acquisition_guidance(
    *,
    bbl: Any,
    expected_manager: Any = None,
) -> dict[str, Any] | None:
    bbl_text = str(bbl or "").strip()
    if not bbl_text:
        return None
    manager_text = str(expected_manager or "Harlem Property Management").strip() or "Harlem Property Management"
    expected_agent = manager_text.upper()
    target = {"bbl": bbl_text, "expected_manager": manager_text, "expected_agent": expected_agent}
    base_command = [
        r".\.venv-x64\Scripts\python.exe",
        "scripts\\truth_live_hpd_role_audit.py",
        "--bbl",
        bbl_text,
        "--expected-agent",
        _powershell_quote(expected_agent),
        "--expected-manager",
        _powershell_quote(manager_text),
        "--no-include-operator-seeds",
        "--no-include-hpm-nonstrict",
    ]
    return {
        "acquisition_mode": "official_hpd_query_packet_only",
        "source_name": "nyc_open_data_hpd_registration_contacts",
        "source_dataset_ids": ["tesw-yqqr", "feu5-w2e2"],
        "official_query_urls": official_query_urls_for_target(target),
        "download_urls": [REGISTRATIONS_DOWNLOAD_URL, CONTACTS_DOWNLOAD_URL],
        "read_only_preview_command": " ".join([*base_command, "--query-packet-only", "--indent", "2"]),
        "post_fetch_local_extract_command": " ".join([
            *base_command,
            "--registrations-file",
            "<path-to-tesw-yqqr.csv-or-json>",
            "--contacts-file",
            "<path-to-feu5-w2e2.csv-or-json>",
            "--indent",
            "2",
        ]),
        "acquisition_note": (
            "Fetch the official HPD registration/contact slice outside restricted runtimes, then rerun "
            "local extract mode before previewing manual evidence. Only ManagementCompany rows support "
            "the hpd_management_company source family."
        ),
    }


def _with_hpd_management_acquisition_guidance(
    evidence: dict[str, Any],
    *,
    bbl: Any,
    expected_manager: Any = None,
) -> dict[str, Any]:
    source = evidence.get("suggested_source") or evidence.get("suggested_source_family")
    if source != "hpd_management_company":
        return evidence
    guidance = _hpd_management_acquisition_guidance(bbl=bbl, expected_manager=expected_manager)
    if not guidance:
        return evidence
    return {**evidence, **guidance}


def _source_ready_evidence_request(
    gap: dict[str, Any],
    *,
    reviewed_source_findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    best_single_source = gap.get("best_single_source_upgrade") or {}
    threshold_paths: list[dict[str, Any]] = []
    if best_single_source.get("suggested_source"):
        threshold_paths.append({
            "path_type": "single_source_upgrade",
            "source": best_single_source.get("suggested_source"),
            "simulated_confidence_score": best_single_source.get("simulated_confidence_score"),
            "would_reach_verified_threshold": (
                best_single_source.get("would_reach_verified_threshold") is True
            ),
        })
    if gap.get("required_bundle_sources"):
        threshold_paths.append({
            "path_type": "quality_bundle",
            "sources": _as_list(gap.get("required_bundle_sources")),
            "simulated_confidence_score": gap.get("bundle_simulated_confidence_score"),
            "would_reach_verified_threshold": gap.get("bundle_upgrade_would_verify") is True,
        })

    display = gap.get("display") if isinstance(gap.get("display"), dict) else {}
    reviewed_source_findings = reviewed_source_findings or []
    recording_ready = gap.get("recording_ready") is True
    fact_key = gap.get("fact_key") or {}
    hpd_bbl = fact_key.get("object_id") or (display.get("building") or {}).get("bbl")
    expected_manager = display.get("subject_label") or fact_key.get("subject_id")
    required_real_evidence = [
        _with_hpd_management_acquisition_guidance(
            evidence,
            bbl=hpd_bbl,
            expected_manager=expected_manager,
        )
        for evidence in _as_list(gap.get("required_real_evidence"))
        if isinstance(evidence, dict)
    ]
    return {
        "request_type": "source_ready_below_verified",
        "relationship_label": display.get("relationship_label"),
        "fact_key": fact_key,
        "display": display,
        "current_sources": _as_list(gap.get("current_sources")),
        "current_supporting_source_count": gap.get("supporting_source_count"),
        "current_supporting_evidence_count": gap.get("supporting_evidence_count"),
        "current_confidence_score": gap.get("recomputed_confidence_score"),
        "verified_confidence_threshold": gap.get("verified_confidence_threshold"),
        "score_gap_to_verified": gap.get("score_gap_to_verified"),
        "can_become": (
            "verified_candidate_after_real_evidence_preview_recording_and_adjudication"
            if gap.get("bundle_upgrade_would_verify") is True
            else "higher_confidence_only_under_current_simulation"
        ),
        "evidence_need": (
            "Acquire stronger exact-property, role-specific management evidence before a source-ready "
            "fact can become a verified-candidate candidate."
        ),
        "threshold_paths": threshold_paths,
        "required_sources": _as_list(gap.get("required_bundle_sources")),
        "required_real_evidence": required_real_evidence,
        "required_real_evidence_count": gap.get("required_real_evidence_count") or len(required_real_evidence),
        "manual_evidence_template": gap.get("manual_evidence_template"),
        "recording_ready": recording_ready,
        "approval_required_before_recording": gap.get("approval_required_before_recording") is True,
        "reviewed_source_findings": reviewed_source_findings,
        "reviewed_source_history_status": _reviewed_source_history_status(
            reviewed_source_findings,
            recording_ready=recording_ready,
        ),
        "safe_action": gap.get("safe_action"),
    }


def _single_source_evidence_request(gap: dict[str, Any]) -> dict[str, Any]:
    display = gap.get("display") if isinstance(gap.get("display"), dict) else {}
    suggested_sources = _as_list(gap.get("suggested_sources"))
    fact_key = gap.get("fact_key") or {}
    hpd_bbl = fact_key.get("object_id") or (display.get("building") or {}).get("bbl")
    expected_manager = display.get("subject_label") or fact_key.get("subject_id")
    return {
        "request_type": "single_source_gap",
        "relationship_label": display.get("relationship_label"),
        "fact_key": fact_key,
        "display": display,
        "current_sources": _as_list(gap.get("current_sources")),
        "current_supporting_source_count": gap.get("supporting_source_count"),
        "current_supporting_evidence_count": gap.get("supporting_evidence_count"),
        "current_confidence_score": gap.get("recomputed_confidence_score"),
        "missing_source_count": gap.get("missing_source_count"),
        "missing_evidence_count": gap.get("missing_evidence_count"),
        "can_become": "multi_source_source_ready_candidate_after_independent_support",
        "evidence_need": "Acquire at least one independent source before verification can be considered.",
        "suggested_sources": suggested_sources,
        "required_real_evidence": [
            _with_hpd_management_acquisition_guidance(
                {
                    "suggested_source": source,
                    "required_fields": [
                        "source_record_id",
                        "source_url_or_local_record_reference",
                        "observed_at",
                        "exact_property_match",
                        "role_specific_management_support",
                    ],
                },
                bbl=hpd_bbl,
                expected_manager=expected_manager,
            )
            for source in suggested_sources
        ],
        "manual_evidence_template": gap.get("manual_evidence_template"),
        "recording_ready": False,
        "approval_required_before_recording": True,
        "safe_action": gap.get("safe_action"),
    }


def _source_acquisition_evidence_request(
    proposal: dict[str, Any],
    *,
    origin: str,
    reviewed_source_findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    relationship = _relationship_from_acquisition_request(proposal)
    current_state = proposal.get("current_relationship_state")
    if not isinstance(current_state, dict):
        current_state = {}
    reviewed_source_findings = reviewed_source_findings or []
    required_real_evidence = [
        _with_hpd_management_acquisition_guidance(
            {
                "suggested_source_family": family,
                "required_fields": [
                    "source_record_id",
                    "source_url_or_local_record_reference",
                    "observed_at",
                    "exact_property_match",
                    "role_specific_management_support",
                ],
            },
            bbl=proposal.get("bbl"),
            expected_manager=relationship.get("manager_name"),
        )
        for family in _as_list(proposal.get("suggested_source_families"))
    ]
    return {
        "request_type": f"{origin}_source_acquisition",
        "candidate_id": proposal.get("candidate_id"),
        "relationship_label": relationship.get("relationship_label"),
        "relationship": relationship,
        "fact_key": {
            "subject_type": "lead",
            "subject_id": proposal.get("manager_lead_id"),
            "predicate": "manages_building",
            "object_type": "building",
            "object_id": proposal.get("bbl"),
            "claim_type": "building_management",
            "normalized_value": "manager",
        },
        "current_relationship_state": current_state,
        "current_sources": _as_list(current_state.get("current_source_names")),
        "existing_manager_proof_source_families": _as_list(proposal.get("existing_manager_proof_source_families")),
        "supporting_source_families_if_recorded": _as_list(proposal.get("supporting_source_families_if_recorded")),
        "strict_manager_source_ready_if_recorded": proposal.get("strict_manager_source_ready_if_recorded") is True,
        "strict_manager_gap_status": proposal.get("strict_manager_gap_status"),
        "strict_manager_gap_reason": proposal.get("strict_manager_gap_reason"),
        "missing_manager_proof_source_family_count": proposal.get("missing_manager_proof_source_family_count"),
        "can_become": (
            "strict_manager_source_ready_after_approved_recording"
            if proposal.get("strict_manager_source_ready_if_recorded") is True
            else "strict_manager_source_gap_after_operator_seed"
        ),
        "evidence_need": proposal.get("next_required_manager_proof")
        or "Acquire exact non-HPD manager-proof evidence before recording strict support.",
        "suggested_source_families": _as_list(proposal.get("suggested_source_families")),
        "search_queries": _as_list(proposal.get("search_queries")),
        "source_targets": _as_list(proposal.get("source_targets")),
        "required_real_evidence": required_real_evidence,
        "recording_ready": False,
        "approval_required_before_recording": True,
        "reviewed_source_findings": reviewed_source_findings,
        "reviewed_source_history_status": _reviewed_source_history_status(
            reviewed_source_findings,
            recording_ready=False,
        ),
        "safe_action": proposal.get("safe_action"),
    }


def _reviewed_findings_for_source_ready_gap(
    gap: dict[str, Any],
    *,
    manager_reviewed_source_findings: list[dict[str, Any]],
    operator_reviewed_source_findings: list[dict[str, Any]],
    raw_manager_reviewed_source_findings: list[dict[str, Any]],
    raw_operator_reviewed_source_findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    display = gap.get("display") if isinstance(gap.get("display"), dict) else {}
    label = str(display.get("relationship_label") or "").lower()
    current_sources = {str(source).lower() for source in _as_list(gap.get("current_sources"))}
    subject_id = str((gap.get("fact_key") or {}).get("subject_id") or "").lower()
    if (
        "harlem property management" in label
        or "hpm_revenue_by_property_summary" in current_sources
        or subject_id == "0ff794d3ba2d"
    ):
        return _prioritized_reviewed_source_findings(
            raw_manager_reviewed_source_findings or manager_reviewed_source_findings,
            gap=gap,
        )
    if "md squared" in label or "daisy" in label or "outreach_confirmed" in current_sources:
        return _prioritized_reviewed_source_findings(
            raw_operator_reviewed_source_findings or operator_reviewed_source_findings,
            gap=gap,
        )
    return []


def _build_evidence_request_packet(
    *,
    source_ready_gaps: list[dict[str, Any]],
    single_source_gaps: list[dict[str, Any]],
    manager_next_sources: list[dict[str, Any]],
    operator_next_sources: list[dict[str, Any]],
    manager_reviewed_source_findings: list[dict[str, Any]],
    operator_reviewed_source_findings: list[dict[str, Any]],
    raw_manager_reviewed_source_findings: list[dict[str, Any]],
    raw_operator_reviewed_source_findings: list[dict[str, Any]],
    reviewed_source_finding_count: int,
    limit: int,
) -> dict[str, Any]:
    source_ready_requests = [
        _source_ready_evidence_request(
            gap,
            reviewed_source_findings=_reviewed_findings_for_source_ready_gap(
                gap,
                manager_reviewed_source_findings=manager_reviewed_source_findings,
                operator_reviewed_source_findings=operator_reviewed_source_findings,
                raw_manager_reviewed_source_findings=raw_manager_reviewed_source_findings,
                raw_operator_reviewed_source_findings=raw_operator_reviewed_source_findings,
            ),
        )
        for gap in source_ready_gaps[:limit]
    ]
    single_source_requests = [_single_source_evidence_request(gap) for gap in single_source_gaps[:limit]]
    source_acquisition_requests = [
        *[
            _source_acquisition_evidence_request(
                proposal,
                origin="manager",
                reviewed_source_findings=manager_reviewed_source_findings,
            )
            for proposal in manager_next_sources[:limit]
        ],
        *[
            _source_acquisition_evidence_request(
                proposal,
                origin="operator",
                reviewed_source_findings=operator_reviewed_source_findings,
            )
            for proposal in operator_next_sources[:limit]
        ],
    ]
    requests = [*source_ready_requests, *single_source_requests, *source_acquisition_requests]
    record_ready_count = sum(1 for request in requests if request.get("recording_ready") is True)
    approval_required_count = sum(1 for request in requests if request.get("approval_required_before_recording") is True)
    reviewed_source_findings = [
        *manager_reviewed_source_findings,
        *operator_reviewed_source_findings,
    ][:8]
    return {
        "dry_run": True,
        "mutations_planned": 0,
        "shown_limit_per_section": limit,
        "request_count": (
            len(source_ready_gaps)
            + len(single_source_gaps)
            + len(manager_next_sources)
            + len(operator_next_sources)
        ),
        "displayed_request_count": len(requests),
        "source_ready_request_count": len(source_ready_gaps),
        "single_source_request_count": len(single_source_gaps),
        "manager_source_request_count": len(manager_next_sources),
        "operator_source_request_count": len(operator_next_sources),
        "source_acquisition_request_count": len(manager_next_sources) + len(operator_next_sources),
        "recording_ready_count": record_ready_count,
        "approval_required_count": approval_required_count,
        "reviewed_source_finding_count": reviewed_source_finding_count,
        "reviewed_source_findings": reviewed_source_findings,
        "reviewed_source_history_status": (
            "reviewed_dead_end_no_recording_ready_source"
            if reviewed_source_finding_count and record_ready_count == 0
            else "reviewed_source_history_available"
            if reviewed_source_finding_count
            else "no_reviewed_source_history"
        ),
        "source_ready_requests": source_ready_requests,
        "single_source_requests": single_source_requests,
        "source_acquisition_requests": source_acquisition_requests,
        "requests": requests,
        "policy": _evidence_request_policy(),
        "safe_action": (
            "Use these requests to acquire and review real evidence. Do not record evidence, adjudicate, "
            "mark verified, or use facts for business action from this packet alone."
        ),
    }


def _build_verification_readiness_gate(
    *,
    verification_candidate_count: Any,
    source_ready_gaps: list[dict[str, Any]],
    single_source_gaps: list[dict[str, Any]],
) -> dict[str, Any]:
    candidate_count = int(verification_candidate_count or 0)
    record_ready_count = sum(1 for gap in source_ready_gaps if gap.get("recording_ready") is True)
    acquisition_required_count = sum(
        1 for gap in source_ready_gaps if gap.get("evidence_acquisition_status") == "acquisition_required"
    )
    approval_required_count = sum(
        1 for gap in source_ready_gaps if gap.get("approval_required_before_recording") is True
    )
    bundle_threshold_clear_count = sum(1 for gap in source_ready_gaps if gap.get("bundle_upgrade_would_verify") is True)
    one_source_threshold_clear_count = sum(
        1
        for gap in source_ready_gaps
        if (gap.get("best_single_source_upgrade") or {}).get("would_reach_verified_threshold") is True
    )
    required_real_evidence_count = sum(int(gap.get("required_real_evidence_count") or 0) for gap in source_ready_gaps)
    if candidate_count > 0:
        status = "verification_candidates_available"
        reason = "Current ledger has facts that may be eligible for adjudication execution after review."
    elif record_ready_count > 0:
        status = "manual_evidence_preview_required"
        reason = "Some source-ready facts have record-ready evidence templates, but execution still needs preview and approval."
    elif acquisition_required_count > 0:
        status = "blocked_evidence_acquisition_required"
        reason = (
            "Source-ready facts remain below the verified threshold, and their threshold-clearing bundles still "
            "require real exact-property, role-specific evidence."
        )
    elif single_source_gaps:
        status = "blocked_independent_source_required"
        reason = "Single-source facts need independent support before verification can be considered."
    else:
        status = "blocked_no_frontier_candidates"
        reason = "No current frontier item is ready for verified-claim adjudication."
    return {
        "status": status,
        "verification_candidate_count": candidate_count,
        "source_ready_below_verified_count": len(source_ready_gaps),
        "record_ready_count": record_ready_count,
        "acquisition_required_count": acquisition_required_count,
        "approval_required_count": approval_required_count,
        "required_real_evidence_count": required_real_evidence_count,
        "one_source_threshold_clear_count": one_source_threshold_clear_count,
        "bundle_threshold_clear_count": bundle_threshold_clear_count,
        "single_source_gap_count": len(single_source_gaps),
        "reason": reason,
        "safe_action": (
            "Do not mark any claim verified from the frontier alone. Acquire and review the required real evidence, "
            "preview manual evidence, get explicit execution approval, then rerun adjudication."
        ),
    }


def build_truth_verification_frontier(
    *,
    adjudication_preview: dict[str, Any],
    manager_source_packet: dict[str, Any] | None = None,
    operator_source_packet: dict[str, Any] | None = None,
    display_context: dict[str, Any] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Build a concise no-mutation frontier of facts closest to verified status."""
    bounded_limit = max(1, min(int(limit or 10), 50))
    ledger = adjudication_preview.get("ledger_source_overlap")
    if not isinstance(ledger, dict):
        ledger = {}
    verified_gap_plan = adjudication_preview.get("verified_confidence_gap_plan")
    if not isinstance(verified_gap_plan, dict):
        verified_gap_plan = {}
    single_source_plan = adjudication_preview.get("verification_gap_plan")
    if not isinstance(single_source_plan, dict):
        single_source_plan = {}
    manager_source_packet = manager_source_packet if isinstance(manager_source_packet, dict) else {}
    operator_source_packet = operator_source_packet if isinstance(operator_source_packet, dict) else {}
    display_context = display_context if isinstance(display_context, dict) else {}
    leads_by_id = display_context.get("leads_by_id")
    if not isinstance(leads_by_id, dict):
        leads_by_id = {}
    manager_packet_lead_id = str(manager_source_packet.get("lead_id") or "").strip() or None
    manager_packet_name = None
    if manager_packet_lead_id:
        lead_context = leads_by_id.get(manager_packet_lead_id)
        if isinstance(lead_context, dict):
            manager_packet_name = _display_name_from_lead_context(lead_context)
    manager_reviewed_source_finding_count = len(_as_list(manager_source_packet.get("reviewed_source_findings")))
    operator_reviewed_source_finding_count = len(_as_list(operator_source_packet.get("reviewed_source_findings")))
    manager_reviewed_source_findings = _compact_reviewed_source_findings(
        manager_source_packet.get("reviewed_source_findings"),
        limit=4,
    )
    operator_reviewed_source_findings = _compact_reviewed_source_findings(
        operator_source_packet.get("reviewed_source_findings"),
        limit=4,
    )

    all_source_ready_gaps = [
        _compact_source_ready_gap(proposal, display_context=display_context)
        for proposal in _as_list(verified_gap_plan.get("proposals"))
        if isinstance(proposal, dict)
    ]
    all_single_source_gaps = [
        _compact_single_source_gap(proposal, display_context=display_context)
        for proposal in _as_list(single_source_plan.get("proposals"))
        if isinstance(proposal, dict)
    ]
    source_ready_gaps = all_source_ready_gaps[:bounded_limit]
    single_source_gaps = all_single_source_gaps[:bounded_limit]
    manager_next_sources = [
        _compact_source_acquisition_proposal(
            proposal,
            manager_lead_id=manager_packet_lead_id,
            manager_name=manager_packet_name,
        )
        for proposal in _as_list(manager_source_packet.get("proposals"))[:bounded_limit]
        if isinstance(proposal, dict)
    ]
    operator_next_sources = [
        _compact_source_acquisition_proposal(proposal)
        for proposal in _as_list(operator_source_packet.get("proposals"))[:bounded_limit]
        if isinstance(proposal, dict)
    ]
    verification_readiness_gate = _build_verification_readiness_gate(
        verification_candidate_count=adjudication_preview.get("verification_candidate_count"),
        source_ready_gaps=all_source_ready_gaps,
        single_source_gaps=all_single_source_gaps,
    )
    evidence_request_packet = _build_evidence_request_packet(
        source_ready_gaps=all_source_ready_gaps,
        single_source_gaps=all_single_source_gaps,
        manager_next_sources=manager_next_sources,
        operator_next_sources=operator_next_sources,
        manager_reviewed_source_findings=manager_reviewed_source_findings,
        operator_reviewed_source_findings=operator_reviewed_source_findings,
        raw_manager_reviewed_source_findings=_as_list(manager_source_packet.get("reviewed_source_findings")),
        raw_operator_reviewed_source_findings=_as_list(operator_source_packet.get("reviewed_source_findings")),
        reviewed_source_finding_count=(
            manager_reviewed_source_finding_count + operator_reviewed_source_finding_count
        ),
        limit=bounded_limit,
    )
    return {
        "run_type": "truth_verification_frontier",
        "dry_run": True,
        "mutations_planned": 0,
        "limit": bounded_limit,
        "current_ledger": {
            "total_fact_group_count": ledger.get("total_fact_group_count"),
            "single_source_fact_group_count": ledger.get("single_source_fact_group_count"),
            "multi_source_fact_group_count": ledger.get("multi_source_fact_group_count"),
            "source_ready_fact_group_count": ledger.get("source_ready_fact_group_count"),
        },
        "verification_candidate_count": adjudication_preview.get("verification_candidate_count"),
        "source_ready_below_verified": {
            "proposal_count": verified_gap_plan.get("proposal_count"),
            "single_source_upgrade_would_verify_count": verified_gap_plan.get(
                "single_source_upgrade_would_verify_count"
            ),
            "bundle_upgrade_would_verify_count": verified_gap_plan.get("bundle_upgrade_would_verify_count"),
            "proposals": source_ready_gaps,
        },
        "single_source_gaps": {
            "proposal_count": single_source_plan.get("proposal_count"),
            "proposals": single_source_gaps,
        },
        "source_acquisition_frontier": {
            "manager_next_source_seed_count": manager_source_packet.get("next_source_seed_count"),
            "operator_second_source_seed_count": operator_source_packet.get("second_source_seed_count"),
            "manager_proposals": manager_next_sources,
            "operator_proposals": operator_next_sources,
        },
        "verification_readiness_gate": verification_readiness_gate,
        "evidence_request_packet": evidence_request_packet,
        "safe_action": (
            "Use this frontier for evidence acquisition and review planning only. It is read-only, does not mark "
            "claims verified, does not record evidence, and does not permit business use."
        ),
        "next_required_action": (
            "Acquire exact-property, role-explicit evidence bundles for source-ready facts and exact non-HPD "
            "manager-proof sources for strict gaps; then preview manual evidence before any approved execution."
        ),
    }


def _extract_frontier_fact_context(adjudication_preview: dict[str, Any]) -> dict[str, set[str]]:
    building_ids: set[str] = set()
    lead_ids: set[str] = set()
    for plan_name in ("verified_confidence_gap_plan", "verification_gap_plan"):
        plan = adjudication_preview.get(plan_name)
        if not isinstance(plan, dict):
            continue
        for proposal in _as_list(plan.get("proposals")):
            if not isinstance(proposal, dict):
                continue
            fact_key = proposal.get("fact_key")
            if not isinstance(fact_key, dict):
                continue
            if str(fact_key.get("subject_type") or "") == "lead" and fact_key.get("subject_id"):
                lead_ids.add(str(fact_key["subject_id"]))
            if str(fact_key.get("object_type") or "") == "building" and fact_key.get("object_id"):
                building_ids.add(str(fact_key["object_id"]))
    return {"building_ids": building_ids, "lead_ids": lead_ids}


async def load_frontier_display_context(session: Any, adjudication_preview: dict[str, Any]) -> dict[str, Any]:
    """Load labels for BBL/lead IDs shown in the read-only verification frontier."""
    context_ids = _extract_frontier_fact_context(adjudication_preview)
    building_ids = sorted(context_ids["building_ids"])
    lead_ids = sorted(context_ids["lead_ids"])
    buildings_by_bbl: dict[str, dict[str, Any]] = {}
    leads_by_id: dict[str, dict[str, Any]] = {}

    if building_ids:
        rows = await session.execute(
            text("""
                SELECT bbl, address, borough, unit_count
                FROM buildings
                WHERE bbl = ANY(:building_ids)
            """),
            {"building_ids": building_ids},
        )
        buildings_by_bbl = {
            str(row._mapping["bbl"]): dict(row._mapping)
            for row in rows
        }

    if lead_ids:
        rows = await session.execute(
            text("""
                SELECT lead_id, company_name, agent_name, normalized_name
                FROM leads
                WHERE lead_id = ANY(:lead_ids)
            """),
            {"lead_ids": lead_ids},
        )
        leads_by_id = {
            str(row._mapping["lead_id"]): dict(row._mapping)
            for row in rows
        }

    return {
        "buildings_by_bbl": buildings_by_bbl,
        "leads_by_id": leads_by_id,
    }


async def build_frontier_for_local_db(*, lead_id: str, limit: int) -> dict[str, Any]:
    factory = get_session_factory()
    async with factory() as session:
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
            limit=max(int(limit or 10), 10),
            include_samples=False,
        )
        manager_preview = await load_manager_external_source_acquisition_preview(
            session,
            lead_id=lead_id,
            limit=50,
        )
        operator_preview = await load_operator_confirmed_management_preview(session, limit=50)
        display_context = await load_frontier_display_context(session, adjudication_preview)
        await session.rollback()

    return build_truth_verification_frontier(
        adjudication_preview=adjudication_preview,
        manager_source_packet=build_manager_source_acquisition_packet(
            manager_preview,
            lead_id=lead_id,
            run_id=f"truth-manager-source-acquisition-frontier-{lead_id}",
        ),
        operator_source_packet=build_operator_source_acquisition_packet(
            operator_preview,
            run_id="truth-operator-source-acquisition-frontier",
        ),
        display_context=display_context,
        limit=limit,
    )


async def async_main() -> int:
    args = parse_args()
    result = await build_frontier_for_local_db(lead_id=args.lead_id, limit=args.limit)
    print(json.dumps(result, indent=args.indent, default=str))
    await shutdown_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
