from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OperatorDocumentTarget:
    target_id: str
    address: str
    aliases: tuple[str, ...]
    expected_manager: str | None = None
    bbl: str | None = None
    manager_lead_id: str | None = None
    source_name: str = "operator_review"
    source_family: str = "first_party_operator_document"
    target_context: str | None = None


HPM_MANAGER_NAME = "Harlem Property Management"
HPM_LEAD_ID = "0ff794d3ba2d"
HPM_SOURCE_NAME = "hpm_revenue_by_property_summary"
MD_SQUARED_MANAGER_NAME = "MD Squared Property Group"
MD_SQUARED_LEAD_ID = "56a71624c6c0"
DAISY_MANAGER_NAME = "Daisy Management"
DAISY_LEAD_ID = "d11246cb2dae"


HPM_PILOT_TARGETS: tuple[OperatorDocumentTarget, ...] = (
    OperatorDocumentTarget("hpm-11-st-nicholas", "11 ST NICHOLAS AVENUE", ("11-15 ST. NICHOLAS AVE",), HPM_MANAGER_NAME, "1018210025", HPM_LEAD_ID, HPM_SOURCE_NAME),
    OperatorDocumentTarget("hpm-36-w-138", "36 WEST 138 STREET", ("138TH 36 W", "36 W 138"), HPM_MANAGER_NAME, "1017350053", HPM_LEAD_ID, HPM_SOURCE_NAME),
    OperatorDocumentTarget("hpm-204-w-140", "204 WEST 140 STREET", ("140TH 202/204 W", "204 W 140"), HPM_MANAGER_NAME, "1020257501", HPM_LEAD_ID, HPM_SOURCE_NAME),
    OperatorDocumentTarget("hpm-2257-acp", "2257 ADAM C POWELL BOULEVARD", ("2257 ACPB", "2257 ADAM"), HPM_MANAGER_NAME, "1019177501", HPM_LEAD_ID, HPM_SOURCE_NAME),
    OperatorDocumentTarget("hpm-306-w-115", "306 WEST 115 STREET", ("306W 115TH", "306 W 115"), HPM_MANAGER_NAME, "1018487504", HPM_LEAD_ID, HPM_SOURCE_NAME),
    OperatorDocumentTarget("hpm-324-e-112", "324 EAST 112 STREET", ("324 EAST 112TH", "324 E 112"), HPM_MANAGER_NAME, "1016837501", HPM_LEAD_ID, HPM_SOURCE_NAME),
    OperatorDocumentTarget("hpm-330-w-145", "330 WEST 145 STREET", ("330 WEST 145TH", "330 W 145"), HPM_MANAGER_NAME, "1020517501", HPM_LEAD_ID, HPM_SOURCE_NAME),
    OperatorDocumentTarget("hpm-345-lenox", "345 LENOX AVENUE", ("345 LENOX",), HPM_MANAGER_NAME, "1019127501", HPM_LEAD_ID, HPM_SOURCE_NAME),
    OperatorDocumentTarget("hpm-42-w-120", "42 WEST 120 STREET", ("42 W 120TH", "42 W 120"), HPM_MANAGER_NAME, "1017187501", HPM_LEAD_ID, HPM_SOURCE_NAME),
    OperatorDocumentTarget("hpm-506-e-119", "506 EAST 119 STREET", ("506 EAST 119TH", "506 E 119"), HPM_MANAGER_NAME, "1018157501", HPM_LEAD_ID, HPM_SOURCE_NAME),
    OperatorDocumentTarget("hpm-555-lenox", "555 LENOX AVENUE", ("555 MALCOLM X", "555 LENOX"), HPM_MANAGER_NAME, "1020077501", HPM_LEAD_ID, HPM_SOURCE_NAME),
    OperatorDocumentTarget("hpm-342-w-56", "342 WEST 56 STREET", ("56TH 342 W", "342 W 56"), HPM_MANAGER_NAME, "1010460054", HPM_LEAD_ID, HPM_SOURCE_NAME),
    OperatorDocumentTarget("hpm-61-lenox", "61 LENOX AVENUE", ("61 MALCOLM X", "61 LENOX"), HPM_MANAGER_NAME, "1018237502", HPM_LEAD_ID, HPM_SOURCE_NAME),
    OperatorDocumentTarget("hpm-141-w-123", "141 WEST 123 STREET", ("141 W 123", "141 WEST 123"), HPM_MANAGER_NAME, "1019080014", HPM_LEAD_ID, HPM_SOURCE_NAME),
)

OPERATOR_SEED_TARGETS: tuple[OperatorDocumentTarget, ...] = (
    OperatorDocumentTarget("md-squared-220-3-ave", "220 3 AVENUE", ("220 THIRD", "220 3"), MD_SQUARED_MANAGER_NAME, "1008747504", MD_SQUARED_LEAD_ID),
    OperatorDocumentTarget("md-squared-57-bond", "57 BOND STREET", ("57 BOND",), MD_SQUARED_MANAGER_NAME, "1005297507", MD_SQUARED_LEAD_ID),
    OperatorDocumentTarget("md-squared-4-w-16", "4 WEST 16 STREET", ("4 W 16", "4 WEST 16"), MD_SQUARED_MANAGER_NAME, "1008170057", MD_SQUARED_LEAD_ID),
    OperatorDocumentTarget("daisy-9-prospect", "9 PROSPECT PARK WEST", ("9 PROSPECT PARK W",), DAISY_MANAGER_NAME, "3010680037", DAISY_LEAD_ID),
)

TARGET_PRESETS: dict[str, tuple[OperatorDocumentTarget, ...]] = {
    "hpm-pilot": HPM_PILOT_TARGETS,
    "hpm-next": (HPM_PILOT_TARGETS[-1],),
    "operator-seeds": OPERATOR_SEED_TARGETS,
    "all": HPM_PILOT_TARGETS + OPERATOR_SEED_TARGETS,
}

OPERATOR_DOCUMENT_KIND = "operator_workbook"
SOURCE_CLUE_DOCUMENT_KINDS = {"derived_research", "public_source_clue"}
DOCUMENT_KINDS = (OPERATOR_DOCUMENT_KIND, *sorted(SOURCE_CLUE_DOCUMENT_KINDS))


def normalize_document_text(value: Any) -> str:
    text = str(value or "").upper()
    text = text.replace("&", " AND ")
    text = re.sub(r"\b(\d+)(ST|ND|RD|TH)\b", r"\1", text)
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    replacements = {
        " STREET ": " ST ",
        " AVENUE ": " AVE ",
        " BOULEVARD ": " BLVD ",
        " WEST ": " W ",
        " EAST ": " E ",
        " NORTH ": " N ",
        " SOUTH ": " S ",
        " SAINT ": " ST ",
    }
    text = f" {text} "
    for before, after in replacements.items():
        text = text.replace(before, after)
    return re.sub(r"\s+", " ", text).strip()


def build_custom_targets(addresses: list[str]) -> tuple[OperatorDocumentTarget, ...]:
    return tuple(
        OperatorDocumentTarget(
            target_id=f"custom-{index + 1}",
            address=address,
            aliases=(address,),
        )
        for index, address in enumerate(addresses)
        if str(address or "").strip()
    )


def build_address_aliases(address: str) -> tuple[str, ...]:
    normalized = normalize_document_text(address)
    aliases = {address, normalized}
    aliases.add(normalized.replace(" AVE", " AVENUE"))
    aliases.add(normalized.replace(" ST", " STREET"))
    aliases.add(normalized.replace(" W ", " WEST "))
    aliases.add(normalized.replace(" E ", " EAST "))
    aliases.add(re.sub(r"\s+(ST|AVE|BLVD)$", "", normalized))
    return tuple(alias for alias in aliases if alias)


def build_relationship_targets_from_rows(
    rows: list[dict[str, Any]],
    *,
    expected_manager: str,
    manager_lead_id: str,
    source_name: str,
    source_family: str = "first_party_operator_document",
    target_prefix: str = "current-relationship",
) -> tuple[OperatorDocumentTarget, ...]:
    """Create audit targets from current relationship rows without treating those rows as proof."""
    targets: list[OperatorDocumentTarget] = []
    for index, row in enumerate(rows, start=1):
        address = str(row.get("address") or "").strip()
        bbl = str(row.get("bbl") or "").strip()
        if not address:
            continue
        role = str(row.get("role") or "").strip() or "unknown"
        target_id = f"{target_prefix}-{bbl or index}"
        targets.append(
            OperatorDocumentTarget(
                target_id=target_id,
                address=address.upper(),
                aliases=build_address_aliases(address),
                expected_manager=expected_manager,
                bbl=bbl or None,
                manager_lead_id=manager_lead_id,
                source_name=source_name,
                source_family=source_family,
                target_context=(
                    f"Generated from current building_management role={role}; "
                    "use for exact-property matching only, not as manager-proof evidence."
                ),
            )
        )
    return tuple(targets)


def _source_reference(*, row: dict[str, Any], source_column: str, document_title: str | None, row_number: int) -> str:
    public_reference = str(row.get(source_column) or "").strip()
    if public_reference:
        return public_reference
    return f"{document_title or 'operator document'} row {row_number}"


def _source_evidence_intake_candidate(
    *,
    target: OperatorDocumentTarget,
    row: dict[str, Any],
    row_number: int,
    property_label: str,
    document_title: str | None,
    source_column: str,
    observed_at: str | None,
    operator_confirmed_document_provenance: bool,
) -> dict[str, Any] | None:
    if not (target.bbl and target.expected_manager and target.manager_lead_id):
        return None
    return {
        "relationship_label": f"{target.expected_manager} manages building {target.address}",
        "bbl": target.bbl,
        "address": target.address,
        "manager_name": target.expected_manager,
        "manager_lead_id": target.manager_lead_id,
        "source_family": target.source_family,
        "source_name": target.source_name,
        "source_url_or_local_record_reference": _source_reference(
            row=row,
            source_column=source_column,
            document_title=document_title,
            row_number=row_number,
        ),
        "source_record_id": f"operator-document:{target.target_id}:row-{row_number}",
        "observed_at": observed_at,
        "exact_property_match": True,
        "role_specific_management_support": True if operator_confirmed_document_provenance else None,
        "source_excerpt_or_row_summary": (
            f"{document_title or 'Operator document'} row {row_number} lists exact property "
            f"{property_label}. Financial values are intentionally excluded."
        ),
        "contradicts_current_claim": False,
        "notes": (
            "Generated by operator_document_audit. Treat as preview-only source-acquisition intake; "
            "record only after provenance, exact property, and manager-role support are reviewed."
            + (f" Target context: {target.target_context}" if target.target_context else "")
        ),
    }


def _source_acquisition_clue(
    *,
    target: OperatorDocumentTarget,
    row: dict[str, Any],
    row_number: int,
    property_label: str,
    matched_alias: str,
    document_title: str | None,
    document_kind: str,
    source_column: str,
) -> dict[str, Any]:
    return {
        "target_id": target.target_id,
        "address": target.address,
        "bbl": target.bbl,
        "expected_manager": target.expected_manager,
        "manager_lead_id": target.manager_lead_id,
        "matched_property_label": property_label,
        "matched_alias": matched_alias,
        "source_document_title": document_title,
        "source_document_kind": document_kind,
        "source_document_row_number": row_number,
        "source_reference": str(row.get(source_column) or "").strip() or None,
        "clue_status": "source_clue_only",
        "can_become_manual_evidence_template": False,
        "source_evidence_intake_candidate_ready": False,
        "requires_primary_source_review": True,
        "approval_required_before_recording": True,
        "safe_action": (
            "Use this row only to find and inspect the cited primary source. Do not record evidence, "
            "mark a claim verified, or treat the document as independent support from this clue alone."
        ),
    }


def audit_operator_document_rows(
    rows: list[dict[str, Any]],
    *,
    targets: tuple[OperatorDocumentTarget, ...],
    document_title: str | None = None,
    property_column: str = "Property",
    source_column: str = "Source",
    observed_at: str | None = None,
    operator_confirmed_document_provenance: bool = False,
    document_kind: str = OPERATOR_DOCUMENT_KIND,
) -> dict[str, Any]:
    """Match source-document property rows to exact evidence targets without returning private financial values."""
    if document_kind not in DOCUMENT_KINDS:
        allowed = ", ".join(DOCUMENT_KINDS)
        raise ValueError(f"Unsupported operator document kind: {document_kind}. Expected one of: {allowed}")

    row_matches: list[dict[str, Any]] = []
    unmatched_targets: list[dict[str, Any]] = []
    intake_candidates: list[dict[str, Any]] = []
    source_acquisition_clues: list[dict[str, Any]] = []
    emits_evidence_candidates = document_kind == OPERATOR_DOCUMENT_KIND
    matchable_rows = [
        (index + 2, row, str(row.get(property_column) or "").strip())
        for index, row in enumerate(rows)
        if str(row.get(property_column) or "").strip()
    ]

    for target in targets:
        normalized_aliases = {
            normalize_document_text(target.address),
            *(normalize_document_text(alias) for alias in target.aliases),
        }
        matched_row: tuple[int, dict[str, Any], str, str] | None = None
        for row_number, row, property_label in matchable_rows:
            normalized_property = normalize_document_text(property_label)
            matched_alias = next(
                (
                    alias
                    for alias in normalized_aliases
                    if alias and (alias in normalized_property or normalized_property in alias)
                ),
                "",
            )
            if matched_alias:
                matched_row = (row_number, row, property_label, matched_alias)
                break

        if not matched_row:
            unmatched_targets.append({
                "target_id": target.target_id,
                "address": target.address,
            "expected_manager": target.expected_manager,
            "target_context": target.target_context,
            "match_status": "not_found",
            "safe_action": "Find another exact-property manager-proof source before previewing evidence.",
        })
            continue

        row_number, row, property_label, matched_alias = matched_row
        intake_candidate = None
        source_clue = None
        if emits_evidence_candidates:
            intake_candidate = _source_evidence_intake_candidate(
                target=target,
                row=row,
                row_number=row_number,
                property_label=property_label,
                document_title=document_title,
                source_column=source_column,
                observed_at=observed_at,
                operator_confirmed_document_provenance=operator_confirmed_document_provenance,
            )
        else:
            source_clue = _source_acquisition_clue(
                target=target,
                row=row,
                row_number=row_number,
                property_label=property_label,
                matched_alias=matched_alias,
                document_title=document_title,
                document_kind=document_kind,
                source_column=source_column,
            )
        if intake_candidate:
            intake_candidates.append(intake_candidate)
        if source_clue:
            source_acquisition_clues.append(source_clue)
        row_matches.append({
            "target_id": target.target_id,
            "address": target.address,
            "bbl": target.bbl,
            "expected_manager": target.expected_manager,
            "target_context": target.target_context,
            "match_status": "exact_property_row_found",
            "source_document_title": document_title,
            "source_document_row_number": row_number,
            "matched_property_label": property_label,
            "matched_alias": matched_alias,
            "source_reference": str(row.get(source_column) or "").strip() or None,
            "source_document_kind": document_kind,
            "clue_status": "source_clue_only" if source_clue else None,
            "can_become_manual_evidence_template": intake_candidate is not None,
            "source_evidence_intake_candidate_ready": intake_candidate is not None,
            "requires_primary_source_review": bool(source_clue),
            "approval_required_before_recording": True,
            "safe_action": source_clue["safe_action"] if source_clue else (
                "Inspect provenance and role context, then use manual-evidence preview before any recording. "
                "Do not copy row-level revenue amounts into claim evidence."
            ),
        })

    return {
        "run_type": "operator_document_audit",
        "dry_run": True,
        "mutations_planned": 0,
        "document_title": document_title,
        "document_kind": document_kind,
        "property_column": property_column,
        "source_column": source_column,
        "target_count": len(targets),
        "matched_target_count": len(row_matches),
        "unmatched_target_count": len(unmatched_targets),
        "source_evidence_intake_candidate_count": len(intake_candidates),
        "source_evidence_intake_candidates": intake_candidates,
        "source_acquisition_clue_count": len(source_acquisition_clues),
        "source_acquisition_clues": source_acquisition_clues,
        "recording_ready_count": 0,
        "match_rows": row_matches,
        "unmatched_targets": unmatched_targets,
        "redaction_policy": (
            "Output excludes management-fee, rent, revenue, and private Drive URL values. "
            "A property-row match is source-acquisition evidence only until reviewed and previewed. "
            "Derived research documents emit source-acquisition clues only, never evidence candidates."
        ),
        "safe_action": (
            "Use this report to decide whether a local operator document can support preview-only manual evidence. "
            "Recording still requires an explicit approval-gated manual evidence run. "
            "For derived research, inspect the cited primary source before creating any source-evidence candidate."
        ),
    }
