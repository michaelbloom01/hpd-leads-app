"""Build a concise read-only worklist for truth evidence acquisition.

The verification frontier is intentionally rich, but it is too verbose for the
human loop of "fetch this source, inspect this row, paste back this proof." This
script converts that frontier into a small operator-facing worklist. It does not
record evidence, adjudicate claims, refresh sources, or mutate local/production
data.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.truth_verification_frontier import build_frontier_for_local_db  # noqa: E402
from src.db.session import shutdown_engine  # noqa: E402
from src.services.manual_evidence import ALLOWED_MANUAL_SOURCE_NAMES  # noqa: E402


PASTE_BACK_FIELDS = [
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

HPD_FETCH_PACKET_FIELDS = [
    "work_item_id",
    "relationship_label",
    "bbl",
    "address",
    "manager_name",
    "registrations_api",
    "property_managers_first_step_api",
    "contacts_api_template",
    "registrations_download_csv",
    "contacts_download_csv",
    "property_managers_first_step_download_csv",
    "post_fetch_local_extract_command",
    "acceptance_note",
]

OPERATOR_CONFIRMATION_FIELDS = [
    "work_item_id",
    "question_prompt",
    "non_duplicate_boundary",
    "confirmation_channel",
    "confirmed_by_name_or_role",
    "confirmed_at",
    "relationship_label",
    "bbl",
    "address",
    "manager_name",
    "source_record_id",
    "source_excerpt_or_row_summary",
    "contradiction_handling",
    "candidate_csv_preview_command",
]

BASE_ACCEPTANCE_CRITERIA = [
    "The source names the exact property or official BBL/registration row.",
    "The source states a property-management or managing-agent relationship for that exact property.",
    "The source family is independent from already-counted support for this claim.",
    "The observed date or source freshness is captured.",
    "Contradictions are routed to review instead of overwriting current claims.",
]

CONCRETE_SOURCE_NAME_NOTES = {
    "external_web_profile": (
        "Fill source_name with the concrete reviewed source, such as openigloo, homes, redfin, "
        "renthop, or zillow; external_web_profile is a family label, not a recordable source name."
    ),
}

HPD_ACCEPTANCE_CRITERIA = [
    "Use official HPD `tesw-yqqr` registrations to identify RegistrationID values for the BBL.",
    "Use official HPD `feu5-w2e2` contacts for each RegistrationID.",
    "Only `ManagementCompany` contact rows can support the HPD manager-proof family.",
    "`Agent`, `SiteManager`, `CorporateOwner`, `HeadOfficer`, `Officer`, and owner rows remain role-specific non-manager evidence.",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lead-id", default="0ff794d3ba2d")
    parser.add_argument("--frontier-limit", type=int, default=10)
    parser.add_argument("--max-items", type=int, default=10)
    parser.add_argument(
        "--csv-template",
        action="store_true",
        help=(
            "Emit a CSV paste-back template for the displayed work items instead of JSON. "
            "The CSV is read-only and can be reviewed with truth_source_evidence_intake.py --candidate-csv."
        ),
    )
    parser.add_argument(
        "--hpd-fetch-packet",
        action="store_true",
        help=(
            "Emit a compact CSV of official HPD query/download URLs for the displayed work items. "
            "This is a read-only acquisition packet for fetching official extracts outside this runtime."
        ),
    )
    parser.add_argument(
        "--operator-confirmation-packet",
        action="store_true",
        help=(
            "Emit a compact CSV of dated operator/outreach confirmation requests for displayed work items. "
            "This is read-only and feeds the existing candidate CSV preview path after a human fills it."
        ),
    )
    parser.add_argument("--indent", type=int, default=2)
    return parser.parse_args()


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_hpd_requirement(request: dict[str, Any]) -> dict[str, Any] | None:
    for evidence in _as_list(request.get("required_real_evidence")):
        if not isinstance(evidence, dict):
            continue
        source = evidence.get("suggested_source") or evidence.get("suggested_source_family")
        if source == "hpd_management_company":
            return evidence
    return None


def _relationship_context(request: dict[str, Any]) -> dict[str, Any]:
    relationship = _as_dict(request.get("relationship"))
    display = _as_dict(request.get("display"))
    building = _as_dict(display.get("building"))
    fact_key = _as_dict(request.get("fact_key"))
    return {
        "relationship_label": (
            request.get("relationship_label")
            or relationship.get("relationship_label")
            or display.get("relationship_label")
        ),
        "bbl": relationship.get("bbl") or building.get("bbl") or fact_key.get("object_id"),
        "address": relationship.get("address") or building.get("address") or display.get("object_label"),
        "manager_name": relationship.get("manager_name") or display.get("subject_label"),
        "manager_lead_id": relationship.get("manager_lead_id") or fact_key.get("subject_id"),
    }


def _task_priority(request: dict[str, Any]) -> int:
    request_type = str(request.get("request_type") or "")
    hpd_requirement = _first_hpd_requirement(request)
    if request_type == "operator_source_acquisition" and request.get("strict_manager_gap_status") == "broad_source_ready_not_strict":
        return 10
    if request_type == "source_ready_below_verified" and hpd_requirement:
        return 20
    if request_type == "operator_source_acquisition":
        return 30
    if request_type == "manager_source_acquisition":
        return 40
    if request_type == "single_source_gap":
        return 50
    return 90


def _source_family_needs(request: dict[str, Any]) -> list[str]:
    families: list[str] = []
    for key in ("suggested_source_families", "required_sources", "suggested_sources"):
        for item in _as_list(request.get(key)):
            text = str(item or "").strip()
            if text and text not in families:
                families.append(text)
    for evidence in _as_list(request.get("required_real_evidence")):
        if not isinstance(evidence, dict):
            continue
        text = str(evidence.get("suggested_source") or evidence.get("suggested_source_family") or "").strip()
        if text and text not in families:
            families.append(text)
    return families


def _build_paste_back_template(context: dict[str, Any], *, source_family: str | None) -> dict[str, Any]:
    source = str(source_family or "").strip()
    default_source_name = source if source in ALLOWED_MANUAL_SOURCE_NAMES else None
    return {
        "relationship_label": context.get("relationship_label"),
        "bbl": context.get("bbl"),
        "address": context.get("address"),
        "manager_name": context.get("manager_name"),
        "source_family": source_family,
        "source_name": default_source_name,
        "source_url_or_local_record_reference": None,
        "source_record_id": None,
        "observed_at": None,
        "exact_property_match": None,
        "role_specific_management_support": None,
        "source_excerpt_or_row_summary": None,
        "contradicts_current_claim": None,
        "notes": CONCRETE_SOURCE_NAME_NOTES.get(source),
    }


def _build_operator_confirmation_request(
    context: dict[str, Any],
    *,
    paste_back_templates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    outreach_template = next(
        (
            template
            for template in paste_back_templates
            if str(template.get("source_family") or "").strip() == "outreach_confirmed"
        ),
        None,
    )
    if not outreach_template:
        return None
    relationship_label = context.get("relationship_label")
    address = context.get("address")
    manager_name = context.get("manager_name")
    return {
        "status": "needs_dated_independent_confirmation",
        "source_family": "outreach_confirmed",
        "question_prompt": (
            f"Can you independently confirm that {manager_name} currently manages {address}?"
            if manager_name and address
            else "Can you independently confirm the current manager for this exact property?"
        ),
        "non_duplicate_boundary": (
            "Do not reuse the same first-hand note already in the ledger. This must be a separately "
            "dated operator/outreach confirmation with enough context to identify the source."
        ),
        "required_fields": OPERATOR_CONFIRMATION_FIELDS,
        "paste_back_template": {
            **outreach_template,
            "observed_at": "<YYYY-MM-DD or ISO timestamp>",
            "exact_property_match": True,
            "role_specific_management_support": True,
            "contradicts_current_claim": False,
            "source_record_id": "<operator-confirmation-id>",
            "source_excerpt_or_row_summary": (
                f"Independent operator/outreach confirmation that {relationship_label}."
                if relationship_label
                else "Independent operator/outreach confirmation for this exact property manager."
            ),
            "notes": (
                "Record confirmer name/role, channel, and any confidence limits. If the confirmation "
                "names a different manager, set contradicts_current_claim=true and preview as review."
            ),
        },
        "contradiction_paste_back_template": {
            **outreach_template,
            "observed_at": "<YYYY-MM-DD or ISO timestamp>",
            "exact_property_match": True,
            "role_specific_management_support": False,
            "contradicts_current_claim": True,
            "source_record_id": "<operator-contradiction-confirmation-id>",
            "source_excerpt_or_row_summary": (
                f"Independent operator/outreach confirmation names <different manager> as current manager "
                f"for {address}, contradicting {relationship_label}."
                if address and relationship_label
                else (
                    "Independent operator/outreach confirmation names a different current manager for this "
                    "exact property."
                )
            ),
            "notes": (
                "Use this template when the source says someone else manages the building. Capture the "
                "different manager name, confirmer name/role, channel, and confidence limits; preview "
                "as contradiction/review instead of overwriting the existing claim."
            ),
        },
        "contradiction_handling": (
            "If the source names a different manager as current manager, use contradiction_paste_back_template. "
            "It should preview as contradicting evidence and route to review, not overwrite the claim."
        ),
        "preview_command": (
            "truth_source_evidence_intake.py --candidate-csv <filled-worklist.csv> "
            "--recommended-scope-only --indent 2"
        ),
        "safe_action": (
            "Preview only. A clean operator-confirmation preview still requires explicit execution approval "
            "before evidence can be recorded."
        ),
    }


def _ordered_paste_back_source_families(
    source_family_needs: list[str],
    *,
    primary_source_family: str | None,
) -> list[str]:
    ordered: list[str] = []
    for source_family in [primary_source_family, *source_family_needs]:
        source = str(source_family or "").strip()
        if source and source not in ordered:
            ordered.append(source)
    return ordered


def _build_work_item(index: int, request: dict[str, Any]) -> dict[str, Any]:
    context = _relationship_context(request)
    hpd_requirement = _first_hpd_requirement(request)
    source_family_needs = _source_family_needs(request)
    first_source_family = "hpd_management_company" if hpd_requirement else (source_family_needs[0] if source_family_needs else None)
    paste_back_source_families = _ordered_paste_back_source_families(
        source_family_needs,
        primary_source_family=first_source_family,
    )
    paste_back_templates = [
        _build_paste_back_template(context, source_family=source_family)
        for source_family in paste_back_source_families
    ]
    if not paste_back_templates:
        paste_back_templates = [_build_paste_back_template(context, source_family=None)]
    reviewed_findings = [
        {
            "source_family": finding.get("source_family"),
            "finding": finding.get("finding"),
            "qualification": finding.get("qualification"),
            "source_urls": _as_list(finding.get("source_urls"))[:3],
        }
        for finding in _as_list(request.get("reviewed_source_findings"))[:3]
        if isinstance(finding, dict)
    ]
    return {
        "work_item_id": f"source-acquisition-{index:03d}",
        "priority": _task_priority(request),
        "request_type": request.get("request_type"),
        "relationship": context,
        "current_sources": _as_list(request.get("current_sources")),
        "current_confidence_score": request.get("current_confidence_score"),
        "score_gap_to_verified": request.get("score_gap_to_verified"),
        "strict_manager_gap_status": request.get("strict_manager_gap_status"),
        "can_become": request.get("can_become"),
        "evidence_need": request.get("evidence_need"),
        "source_family_needs": source_family_needs,
        "search_queries": _as_list(request.get("search_queries"))[:5],
        "source_targets": _as_list(request.get("source_targets"))[:5],
        "official_hpd_query": _as_dict(hpd_requirement.get("official_query_urls")) if hpd_requirement else None,
        "official_hpd_download_urls": _as_list(hpd_requirement.get("download_urls")) if hpd_requirement else [],
        "read_only_hpd_preview_command": hpd_requirement.get("read_only_preview_command") if hpd_requirement else None,
        "post_fetch_local_extract_command": hpd_requirement.get("post_fetch_local_extract_command") if hpd_requirement else None,
        "acceptance_criteria": [
            *(HPD_ACCEPTANCE_CRITERIA if hpd_requirement else []),
            *BASE_ACCEPTANCE_CRITERIA,
        ],
        "paste_back_template": paste_back_templates[0],
        "paste_back_templates": paste_back_templates,
        "operator_confirmation_request": _build_operator_confirmation_request(
            context,
            paste_back_templates=paste_back_templates,
        ),
        "paste_back_fields": PASTE_BACK_FIELDS,
        "reviewed_source_history_status": request.get("reviewed_source_history_status"),
        "reviewed_source_findings": reviewed_findings,
        "safe_action": (
            "Source acquisition only. Do not record evidence or mark verified until this source is reviewed, "
            "manual evidence preview is clean, and explicit execution approval is given."
        ),
    }


def build_source_acquisition_worklist(frontier: dict[str, Any], *, max_items: int = 10) -> dict[str, Any]:
    """Compress a verification frontier into source-acquisition work items."""
    evidence_packet = _as_dict(frontier.get("evidence_request_packet"))
    requests = [request for request in _as_list(evidence_packet.get("requests")) if isinstance(request, dict)]
    sorted_requests = sorted(requests, key=_task_priority)
    bounded_items = sorted_requests[: max(1, int(max_items or 10))]
    work_items = [_build_work_item(index, request) for index, request in enumerate(bounded_items, start=1)]
    hpd_items = [item for item in work_items if item.get("official_hpd_query")]
    return {
        "run_type": "truth_source_acquisition_worklist",
        "dry_run": True,
        "mutations_planned": 0,
        "source": "truth_verification_frontier.evidence_request_packet",
        "frontier_current_ledger": frontier.get("current_ledger"),
        "verification_candidate_count": frontier.get("verification_candidate_count"),
        "request_count": len(requests),
        "work_item_count": len(work_items),
        "hpd_work_item_count": len(hpd_items),
        "recording_ready_count": evidence_packet.get("recording_ready_count", 0),
        "approval_required_count": evidence_packet.get("approval_required_count", 0),
        "work_items": work_items,
        "policy": {
            "single_source_policy": "No single-source claim may be marked verified.",
            "role_policy": "Agent is not manager; role-specific evidence stays role-specific.",
            "execution_policy": "This worklist is read-only and cannot record evidence or change claim status.",
        },
        "next_step_after_source_found": (
            "Paste back the filled template or run the official/local extract audit. Then preview manual evidence; "
            "execute only after explicit dry_run=false / confirm_execute=true approval."
        ),
        "safe_action": (
            "Use this as a human/source-acquisition checklist. It is not evidence and does not permit business use."
        ),
    }


def build_source_acquisition_csv_template(worklist: dict[str, Any]) -> str:
    """Return a CSV paste-back template for the current work items."""
    handle = io.StringIO()
    writer = csv.DictWriter(handle, fieldnames=PASTE_BACK_FIELDS, lineterminator="\n")
    writer.writeheader()
    for item in _as_list(worklist.get("work_items")):
        if not isinstance(item, dict):
            continue
        templates = _as_list(item.get("paste_back_templates")) or [item.get("paste_back_template")]
        for template_value in templates:
            template = _as_dict(template_value)
            if template:
                writer.writerow({field: template.get(field) or "" for field in PASTE_BACK_FIELDS})
    return handle.getvalue()


def build_source_acquisition_hpd_fetch_packet(worklist: dict[str, Any]) -> str:
    """Return a compact CSV of official HPD fetch/replay instructions."""
    handle = io.StringIO()
    writer = csv.DictWriter(handle, fieldnames=HPD_FETCH_PACKET_FIELDS, lineterminator="\n")
    writer.writeheader()
    for item in _as_list(worklist.get("work_items")):
        if not isinstance(item, dict):
            continue
        query = _as_dict(item.get("official_hpd_query"))
        if not query:
            continue
        relationship = _as_dict(item.get("relationship"))
        writer.writerow(
            {
                "work_item_id": item.get("work_item_id"),
                "relationship_label": relationship.get("relationship_label"),
                "bbl": relationship.get("bbl"),
                "address": relationship.get("address"),
                "manager_name": relationship.get("manager_name"),
                "registrations_api": query.get("registrations_api"),
                "property_managers_first_step_api": query.get("property_managers_first_step_api"),
                "contacts_api_template": query.get("contacts_api_template"),
                "registrations_download_csv": query.get("registrations_download_csv"),
                "contacts_download_csv": query.get("contacts_download_csv"),
                "property_managers_first_step_download_csv": query.get("property_managers_first_step_download_csv"),
                "post_fetch_local_extract_command": item.get("post_fetch_local_extract_command"),
                "acceptance_note": (
                    "Fetch official registration rows, then contacts for each RegistrationID. "
                    "Only ManagementCompany rows can support manages_building evidence; the "
                    "Property Managers-1st Step view is registration lookup context only."
                ),
            }
        )
    return handle.getvalue()


def build_source_acquisition_operator_confirmation_packet(worklist: dict[str, Any]) -> str:
    """Return a compact CSV for requesting and paste-backing operator confirmations."""
    handle = io.StringIO()
    writer = csv.DictWriter(handle, fieldnames=OPERATOR_CONFIRMATION_FIELDS, lineterminator="\n")
    writer.writeheader()
    for item in _as_list(worklist.get("work_items")):
        if not isinstance(item, dict):
            continue
        request = _as_dict(item.get("operator_confirmation_request"))
        template = _as_dict(request.get("paste_back_template"))
        if not request or not template:
            continue
        relationship = _as_dict(item.get("relationship"))
        writer.writerow(
            {
                "work_item_id": item.get("work_item_id"),
                "question_prompt": request.get("question_prompt"),
                "non_duplicate_boundary": request.get("non_duplicate_boundary"),
                "confirmation_channel": "",
                "confirmed_by_name_or_role": "",
                "confirmed_at": "",
                "relationship_label": relationship.get("relationship_label"),
                "bbl": relationship.get("bbl"),
                "address": relationship.get("address"),
                "manager_name": relationship.get("manager_name"),
                "source_record_id": template.get("source_record_id"),
                "source_excerpt_or_row_summary": template.get("source_excerpt_or_row_summary"),
                "contradiction_handling": request.get("contradiction_handling"),
                "candidate_csv_preview_command": request.get("preview_command"),
            }
        )
    return handle.getvalue()


async def async_main() -> int:
    args = parse_args()
    frontier = await build_frontier_for_local_db(lead_id=args.lead_id, limit=args.frontier_limit)
    worklist = build_source_acquisition_worklist(frontier, max_items=args.max_items)
    if args.hpd_fetch_packet:
        print(build_source_acquisition_hpd_fetch_packet(worklist), end="")
    elif args.operator_confirmation_packet:
        print(build_source_acquisition_operator_confirmation_packet(worklist), end="")
    elif args.csv_template:
        print(build_source_acquisition_csv_template(worklist), end="")
    else:
        print(json.dumps(worklist, indent=args.indent, default=str))
    await shutdown_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
