"""Materialize existing operational facts into the truth claim ledger."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.transform.normalize import normalize_name, normalize_name_for_grouping
from src.services.confidence import CONFIDENCE_POLICY_VERSION, ConfidenceInput, actionability_level, compute_confidence, source_quality
from src.services.outreach_feedback import classify_outreach_feedback, stable_outreach_feedback_id
from src.services.truth_program import stable_claim_id, serialize_dt


SUPPORTED_SOURCES = (
    "building_management",
    "hpd_contact_role_links",
    "hpd_contact_management_links",
    "hpd_contact_roles",
    "hpd_contact_addresses",
    "enrichment_observations",
    "lead_contact_paths",
    "canonical_entity_memberships",
    "acris_transactions",
    "dob_permits",
    "hpd_complaints",
    "hpd_violations",
    "hpd_litigation",
    "emergency_repairs",
    "aep_designations",
    "eviction_filings",
    "energy_grades",
    "facade_inspections",
    "pad_addresses",
    "outreach_feedback",
)
SourceSelection = tuple[str, ...] | None
OWNER_CONTACT_TYPES = {"Owner", "CorporateOwner", "IndividualOwner"}
MANAGER_CONTACT_TYPES = {"ManagementCompany"}
AGENT_CONTACT_TYPES = {"Agent"}
SITE_MANAGER_CONTACT_TYPES = {"SiteManager"}
PERSON_CONTACT_TYPES = {"HeadOfficer", "Officer", "Shareholder"}
LEAD_ROLE_LINK_CONTACT_TYPES = (
    OWNER_CONTACT_TYPES
    | MANAGER_CONTACT_TYPES
    | AGENT_CONTACT_TYPES
    | SITE_MANAGER_CONTACT_TYPES
    | PERSON_CONTACT_TYPES
)

VERIFICATION_LEGAL_SUFFIXES = (
    " LLC",
    " L L C",
    " INC",
    " INCORPORATED",
    " CORP",
    " CORPORATION",
    " LP",
    " LLP",
    " PLLC",
    " PC",
    " PA",
)


def normalize_materialization_sources(sources: Any = None) -> SourceSelection:
    if sources is None:
        return None
    raw_values = sources if isinstance(sources, (list, tuple, set)) else [sources]
    selected: list[str] = []
    for value in raw_values:
        for item in str(value).split(","):
            source = item.strip()
            if source and source not in selected:
                selected.append(source)
    if not selected:
        return None

    unsupported = sorted(set(selected) - set(SUPPORTED_SOURCES))
    if unsupported:
        raise ValueError(f"Unsupported truth materialization source(s): {', '.join(unsupported)}")
    return tuple(source for source in SUPPORTED_SOURCES if source in selected)


def _source_enabled(selected_sources: SourceSelection, source: str) -> bool:
    return selected_sources is None or source in selected_sources


def freshness_days(value: Any) -> int | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, time.min, tzinfo=timezone.utc)
    dt = value if getattr(value, "tzinfo", None) else value.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - dt).days)


def _evidence_id(claim_id: str, source_name: str, source_record_id: str | None) -> str:
    return stable_claim_id("evidence", claim_id, source_name, source_record_id or "")


def _snapshot_id(entity_type: str, entity_id: str, scope: str, run_id: str) -> str:
    return stable_claim_id("confidence_snapshot", entity_type, entity_id, scope, run_id)


def build_materialized_claim(
    *,
    subject_type: str,
    subject_id: str,
    predicate: str,
    object_type: str,
    object_id: str,
    normalized_value: str | None,
    claim_type: str,
    source_name: str,
    source_type: str,
    source_record_id: str,
    observed_at: Any,
    extracted_value: str | None = None,
    raw_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    age = freshness_days(observed_at)
    confidence = compute_confidence(ConfidenceInput(
        claim_type=claim_type,
        supporting_sources=[source_name],
        contradicting_sources=[],
        freshness_days=age,
        source_agreement_count=1,
    ))
    claim_id = stable_claim_id(subject_type, subject_id, predicate, object_type, object_id, source_name)
    return {
        "claim": {
            "claim_id": claim_id,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "predicate": predicate,
            "object_type": object_type,
            "object_id": object_id,
            "extracted_value": extracted_value or normalized_value,
            "normalized_value": normalized_value,
            "claim_type": claim_type,
            "freshness_days": age,
            "observed_at": observed_at,
            **confidence,
        },
        "evidence": {
            "evidence_id": _evidence_id(claim_id, source_name, source_record_id),
            "claim_id": claim_id,
            "source_name": source_name,
            "source_type": source_type,
            "source_record_id": source_record_id,
            "observed_at": observed_at,
            "extracted_value": extracted_value or normalized_value,
            "normalized_value": normalized_value,
            "support_status": "supports",
            "source_quality_score": source_quality(source_name),
            "evidence_weight": 1.0,
            "raw_payload": raw_payload or {},
        },
    }


def _preview_claim_spec(spec: dict[str, Any]) -> dict[str, Any]:
    claim = spec.get("claim") or {}
    evidence = spec.get("evidence") or {}
    return {
        "claim_id": claim.get("claim_id"),
        "evidence_id": evidence.get("evidence_id"),
        "subject_type": claim.get("subject_type"),
        "subject_id": claim.get("subject_id"),
        "predicate": claim.get("predicate"),
        "object_type": claim.get("object_type"),
        "object_id": claim.get("object_id"),
        "normalized_value": claim.get("normalized_value"),
        "claim_type": claim.get("claim_type"),
        "belief_status": claim.get("belief_status"),
        "confidence_score": claim.get("confidence_score"),
        "freshness_days": claim.get("freshness_days"),
        "actionability_level": claim.get("actionability_level"),
        "source_name": evidence.get("source_name"),
        "source_type": evidence.get("source_type"),
        "source_record_id": evidence.get("source_record_id"),
        "observed_at": serialize_dt(evidence.get("observed_at") or claim.get("observed_at")),
        "support_status": evidence.get("support_status"),
        "source_quality_score": evidence.get("source_quality_score"),
    }


def _preview_sample_limit(limit: int) -> int:
    return min(max(limit, 10), 25)


def _preview_spec_source(spec: dict[str, Any]) -> str:
    evidence = spec.get("evidence") or {}
    return str(evidence.get("source_name") or "unknown")


def _source_diverse_preview_specs(specs: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    source_order: list[str] = []
    for spec in specs:
        source_name = _preview_spec_source(spec)
        if source_name not in grouped:
            grouped[source_name] = []
            source_order.append(source_name)
        grouped[source_name].append(_preview_claim_spec(spec))

    selected: list[dict[str, Any]] = []
    max_count = _preview_sample_limit(limit)
    while len(selected) < max_count and any(grouped.values()):
        for source_name in source_order:
            if grouped[source_name]:
                selected.append(grouped[source_name].pop(0))
                if len(selected) >= max_count:
                    break
    return selected


async def _load_strict_hpd_role_link_preview_counts(
    session: AsyncSession,
    *,
    selected_sources: SourceSelection,
) -> dict[str, Any]:
    if not (
        _source_enabled(selected_sources, "hpd_contact_role_links")
        or _source_enabled(selected_sources, "hpd_contact_management_links")
    ):
        return {
            "strict_counts": {},
            "strict_counts_by_predicate": {},
            "strict_sample_specs": [],
        }

    rows = await session.execute(
        text("""
            SELECT
                bc.id,
                bc.bbl,
                bc.registration_contact_id,
                bc.registration_id,
                bc.contact_type,
                bc.description,
                bc.corporation_name,
                bc.first_name,
                bc.last_name,
                bc.title,
                bc.business_address,
                bc.business_city,
                bc.business_state,
                bc.business_zip,
                bc.updated_at AS observed_at,
                bm.id AS building_management_id,
                COALESCE(NULLIF(bm.role, ''), 'manager') AS building_management_role,
                bm.lead_id,
                l.normalized_name AS lead_normalized_name,
                l.company_name AS lead_company_name,
                l.agent_name AS lead_agent_name,
                l.owner_name AS lead_owner_name
            FROM building_contacts bc
            JOIN building_management bm ON bm.bbl = bc.bbl AND bm.is_current = true
            JOIN leads l ON l.lead_id = bm.lead_id
            WHERE bc.contact_type IN ('Agent', 'ManagementCompany', 'Owner', 'CorporateOwner', 'IndividualOwner', 'HeadOfficer', 'Officer', 'Shareholder', 'SiteManager')
            ORDER BY bc.updated_at DESC NULLS LAST, bc.id DESC
        """)
    )
    strict_counts = {
        "hpd_contact_role_links": 0,
        "hpd_contact_management_links": 0,
    }
    strict_counts_by_predicate: dict[str, int] = {}
    strict_sample_specs: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row._mapping)
        spec = build_hpd_role_link_claim_spec(data)
        if spec and _source_enabled(selected_sources, "hpd_contact_role_links"):
            strict_counts["hpd_contact_role_links"] += 1
            predicate = str(spec.get("claim", {}).get("predicate") or "unknown")
            strict_counts_by_predicate[predicate] = strict_counts_by_predicate.get(predicate, 0) + 1
            if len(strict_sample_specs) < 10:
                strict_sample_specs.append(spec)
        if data.get("contact_type") == "ManagementCompany" and _source_enabled(selected_sources, "hpd_contact_management_links"):
            management_spec = build_hpd_management_link_claim_spec(data)
            if management_spec:
                strict_counts["hpd_contact_management_links"] += 1
                predicate = str(management_spec.get("claim", {}).get("predicate") or "unknown")
                strict_counts_by_predicate[predicate] = strict_counts_by_predicate.get(predicate, 0) + 1
                if len(strict_sample_specs) < 10:
                    strict_sample_specs.append(management_spec)

    if selected_sources is not None:
        strict_counts = {source: count for source, count in strict_counts.items() if source in selected_sources}
    return {
        "strict_counts": strict_counts,
        "strict_counts_by_predicate": strict_counts_by_predicate,
        "strict_sample_specs": strict_sample_specs,
    }


def build_confidence_snapshots_from_specs(specs: list[dict[str, Any]], *, run_id: str) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for spec in specs:
        claim = spec.get("claim") or {}
        subject_type = str(claim.get("subject_type") or "").strip()
        subject_id = str(claim.get("subject_id") or "").strip()
        if not subject_type or not subject_id:
            continue
        grouped.setdefault((subject_type, subject_id), []).append(claim)

    snapshots: list[dict[str, Any]] = []
    computed_at = datetime.now(timezone.utc)
    for (entity_type, entity_id), claims in grouped.items():
        scores = [float(claim["confidence_score"]) for claim in claims if claim.get("confidence_score") is not None]
        if not scores:
            continue
        stale_count = sum(
            1
            for claim in claims
            if claim.get("freshness_days") is None or int(claim.get("freshness_days") or 0) > 180
        )
        contradiction_count = sum(
            int((claim.get("rationale") or {}).get("source_disagreement_count") or 0)
            for claim in claims
        )
        max_freshness = max(
            int(claim.get("freshness_days") or 9999)
            for claim in claims
        )
        score = round(sum(scores) / len(scores), 3)
        scope = "materialized_claims"
        snapshots.append({
            "snapshot_id": _snapshot_id(entity_type, entity_id, scope, run_id),
            "entity_type": entity_type,
            "entity_id": entity_id,
            "confidence_scope": scope,
            "confidence_score": score,
            "actionability_level": actionability_level(
                score=score,
                contradictions=contradiction_count,
                freshness_days=max_freshness,
                supporting_source_count=len({
                    source
                    for claim in claims
                    for source in ((claim.get("rationale") or {}).get("supporting_sources") or [])
                }),
                supporting_evidence_count=len(claims),
            ),
            "supporting_claim_count": len(claims),
            "contradicting_claim_count": contradiction_count,
            "stale_claim_count": stale_count,
            "computed_at": computed_at,
            "rationale": {
                "confidence_policy_version": CONFIDENCE_POLICY_VERSION,
                "materialization_run_id": run_id,
                "claim_ids": [claim.get("claim_id") for claim in claims if claim.get("claim_id")],
                "claim_types": sorted({str(claim.get("claim_type")) for claim in claims if claim.get("claim_type")}),
                "max_freshness_days": max_freshness,
            },
            "run_id": run_id,
        })
    return snapshots


def build_materialization_rollback_plan(
    *,
    run_id: str,
    claim_ids: list[str],
    evidence_ids: list[str],
    snapshot_ids: list[str],
    existing_claim_ids: set[str],
    existing_evidence_ids: set[str],
    existing_snapshot_ids: set[str],
    before_snapshot_samples: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    new_claim_ids = sorted(set(claim_ids) - existing_claim_ids)
    updated_claim_ids = sorted(set(claim_ids).intersection(existing_claim_ids))
    new_evidence_ids = sorted(set(evidence_ids) - existing_evidence_ids)
    updated_evidence_ids = sorted(set(evidence_ids).intersection(existing_evidence_ids))
    new_snapshot_ids = sorted(set(snapshot_ids) - existing_snapshot_ids)
    updated_snapshot_ids = sorted(set(snapshot_ids).intersection(existing_snapshot_ids))
    return {
        "run_id": run_id,
        "new_claim_count": len(new_claim_ids),
        "updated_claim_count": len(updated_claim_ids),
        "new_evidence_count": len(new_evidence_ids),
        "updated_evidence_count": len(updated_evidence_ids),
        "new_confidence_snapshot_count": len(new_snapshot_ids),
        "updated_confidence_snapshot_count": len(updated_snapshot_ids),
        "new_claim_ids_sample": new_claim_ids[:25],
        "updated_claim_ids_sample": updated_claim_ids[:25],
        "new_evidence_ids_sample": new_evidence_ids[:25],
        "updated_evidence_ids_sample": updated_evidence_ids[:25],
        "new_confidence_snapshot_ids_sample": new_snapshot_ids[:25],
        "updated_confidence_snapshot_ids_sample": updated_snapshot_ids[:25],
        "before_snapshot_samples": before_snapshot_samples or {
            "truth_claims": [],
            "truth_evidence": [],
            "confidence_snapshots": [],
        },
        "rollback_order": [
            "Delete confidence_snapshots listed as new for this run.",
            "Delete truth_evidence rows listed as new for this run.",
            "Delete truth_claims rows listed as new for this run.",
            "For updated rows, restore previous values from backup/PITR. Bounded before-snapshot samples are included for audit and spot repair, but broad rollback of updated rows requires a full backup or captured full before snapshot.",
        ],
        "rollback_strategy": (
            "This execute path uses upserts. New rows are safe to delete by ID in dependency order; "
            "preexisting rows are audit-updated and require restore from backup/PITR or a separately captured full before snapshot."
        ),
    }


def build_materialization_manifest_entries(
    *,
    run_id: str,
    item_type: str,
    item_ids: list[str],
    existing_item_ids: set[str],
    before_snapshots_by_id: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    snapshots = before_snapshots_by_id or {}
    entries: list[dict[str, Any]] = []
    for item_id in sorted({str(value) for value in item_ids if value}):
        was_existing = item_id in existing_item_ids
        entries.append({
            "run_id": run_id,
            "item_type": item_type,
            "item_id": item_id,
            "was_existing": was_existing,
            "before_snapshot": snapshots.get(item_id) if was_existing else None,
        })
    return entries


def _manifest_summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, dict[str, int]] = {}
    for entry in entries:
        item_type = str(entry.get("item_type") or "unknown")
        bucket = by_type.setdefault(item_type, {"total": 0, "new": 0, "existing": 0})
        bucket["total"] += 1
        if entry.get("was_existing"):
            bucket["existing"] += 1
        else:
            bucket["new"] += 1
    return {
        "entry_count": len(entries),
        "by_type": by_type,
        "rollback_strategy": (
            "Rows listed as new in truth_materialization_manifest can be deleted by run_id in dependency order. "
            "Rows listed as existing include before_snapshot JSON for targeted repair, but broad restore should still use backup/PITR."
        ),
    }


def _contact_display_name(row: dict[str, Any]) -> str:
    corporation = str(row.get("corporation_name") or "").strip()
    person = " ".join(
        part for part in [str(row.get("first_name") or "").strip(), str(row.get("last_name") or "").strip()]
        if part
    ).strip()
    return corporation or person or str(row.get("description") or "").strip() or f"building_contact:{row.get('id')}"


def _contact_address(row: dict[str, Any]) -> str | None:
    parts = [
        str(row.get("business_address") or "").strip(),
        str(row.get("business_city") or "").strip(),
        str(row.get("business_state") or "").strip(),
        str(row.get("business_zip") or "").strip(),
    ]
    value = ", ".join(part for part in parts if part)
    return value or None


def _role_claim_shape(*, role: Any = None, contact_type: Any = None) -> dict[str, str] | None:
    raw_value = str(contact_type if contact_type is not None else role or "").strip()
    normalized = raw_value.lower().replace(" ", "").replace("_", "")
    if raw_value in OWNER_CONTACT_TYPES or normalized in {"owner", "corporateowner", "individualowner"}:
        return {
            "predicate": "owns_building",
            "claim_type": "building_ownership",
            "normalized_value": "owner",
            "role_family": "owner",
        }
    if raw_value in MANAGER_CONTACT_TYPES or normalized in {"manager", "management", "managementcompany", "managingagent"}:
        return {
            "predicate": "manages_building",
            "claim_type": "building_management",
            "normalized_value": "manager",
            "role_family": "manager",
        }
    if raw_value in AGENT_CONTACT_TYPES or normalized in {"agent", "registeredagent"}:
        return {
            "predicate": "registered_agent_for_building",
            "claim_type": "registered_agent",
            "normalized_value": "registered_agent",
            "role_family": "registered_agent",
        }
    if raw_value in SITE_MANAGER_CONTACT_TYPES or normalized in {"sitemanager", "site_mgr"}:
        return {
            "predicate": "site_manager_for_building",
            "claim_type": "site_management_contact",
            "normalized_value": "site_manager",
            "role_family": "site_manager",
        }
    if raw_value in PERSON_CONTACT_TYPES or normalized in {"headofficer", "officer", "shareholder"}:
        return {
            "predicate": "person_associated_with_building_owner",
            "claim_type": "person_contact",
            "normalized_value": "person_contact",
            "role_family": "person",
        }
    return None


def _hpd_contact_role_claim(row: dict[str, Any]) -> dict[str, str] | None:
    contact_type = str(row.get("contact_type") or "").strip()
    return _role_claim_shape(contact_type=contact_type)


def _building_management_role_claim_shape(role: Any) -> dict[str, str] | None:
    return _role_claim_shape(role=role)


def build_hpd_contact_claim_specs(row: dict[str, Any]) -> list[dict[str, Any]]:
    contact_id = str(row.get("id"))
    bbl = str(row.get("bbl") or "").strip()
    if not contact_id or not bbl:
        return []

    specs: list[dict[str, Any]] = []
    display_name = _contact_display_name(row)
    role_shape = _hpd_contact_role_claim(row)
    if role_shape:
        specs.append(build_materialized_claim(
            subject_type="hpd_contact",
            subject_id=contact_id,
            predicate=role_shape["predicate"],
            object_type="building",
            object_id=bbl,
            normalized_value=display_name,
            claim_type=role_shape["claim_type"],
            source_name="hpd_contacts",
            source_type="hpd_registration_contact",
            source_record_id=f"building_contacts:{contact_id}:role",
            observed_at=row.get("observed_at"),
            extracted_value=display_name,
            raw_payload={
                "contact_type": row.get("contact_type"),
                "registration_id": row.get("registration_id"),
                "registration_contact_id": row.get("registration_contact_id"),
                "title": row.get("title"),
                "description": row.get("description"),
            },
        ))

    address = _contact_address(row)
    if address:
        specs.append(build_materialized_claim(
            subject_type="hpd_contact",
            subject_id=contact_id,
            predicate="has_mailing_address",
            object_type="mailing_address",
            object_id=stable_claim_id("hpd_contact_address", contact_id, address),
            normalized_value=address,
            claim_type="mailing_address",
            source_name="hpd_contacts",
            source_type="hpd_registration_contact",
            source_record_id=f"building_contacts:{contact_id}:address",
            observed_at=row.get("observed_at"),
            extracted_value=address,
            raw_payload={
                "bbl": bbl,
                "contact_type": row.get("contact_type"),
                "display_name": display_name,
            },
        ))
    return specs


def _name_group(value: Any) -> str:
    return normalize_name_for_grouping(normalize_name(str(value or "")))


def _verification_name_key(value: Any) -> str:
    """Strict key for verification; unlike dedupe, it preserves business-role words."""
    name = normalize_name(str(value or ""))
    if not name:
        return ""
    changed = True
    while changed:
        changed = False
        for suffix in VERIFICATION_LEGAL_SUFFIXES:
            if name.endswith(suffix):
                name = name[:-len(suffix)].strip()
                changed = True
    return " ".join(name.split())


def _filter_verification_name_keys(keys: set[str]) -> set[str]:
    specific_keys = {key for key in keys if len(key.split()) >= 2}
    return specific_keys or keys


def _lead_verification_name_keys(row: dict[str, Any]) -> set[str]:
    keys = {
        key
        for key in (
            _verification_name_key(row.get("lead_normalized_name")),
            _verification_name_key(row.get("lead_company_name")),
            _verification_name_key(row.get("lead_agent_name")),
            _verification_name_key(row.get("lead_owner_name")),
        )
        if key and len(key) >= 4
    }
    return _filter_verification_name_keys(keys)


def _lead_name_groups(row: dict[str, Any]) -> set[str]:
    return {
        group
        for group in (
            _name_group(row.get("lead_normalized_name")),
            _name_group(row.get("lead_company_name")),
            _name_group(row.get("lead_agent_name")),
            _name_group(row.get("lead_owner_name")),
        )
        if group and len(group) >= 4
    }


def build_hpd_role_link_claim_spec(row: dict[str, Any]) -> dict[str, Any] | None:
    """Build a lead/building role claim from HPD contacts only when role and identity are strict matches."""
    lead_id = str(row.get("lead_id") or "").strip()
    bbl = str(row.get("bbl") or "").strip()
    contact_id = str(row.get("id") or "").strip()
    if not lead_id or not bbl or not contact_id:
        return None

    hpd_shape = _hpd_contact_role_claim(row)
    bm_shape = _building_management_role_claim_shape(row.get("building_management_role"))
    if not hpd_shape or not bm_shape or hpd_shape["predicate"] != bm_shape["predicate"]:
        return None

    display_name = _contact_display_name(row)
    contact_key = _verification_name_key(display_name)
    lead_keys = _lead_verification_name_keys(row)
    if not contact_key or contact_key not in lead_keys:
        return None

    role_family = hpd_shape["role_family"]
    return build_materialized_claim(
        subject_type="lead",
        subject_id=lead_id,
        predicate=hpd_shape["predicate"],
        object_type="building",
        object_id=bbl,
        normalized_value=hpd_shape["normalized_value"],
        claim_type=hpd_shape["claim_type"],
        source_name="hpd_contacts",
        source_type=f"hpd_registration_{role_family}_match",
        source_record_id=f"building_contacts:{contact_id}:role_link:{lead_id}:{bbl}:{role_family}",
        observed_at=row.get("observed_at"),
        extracted_value=display_name,
        raw_payload={
            "contact_type": row.get("contact_type"),
            "building_management_role": row.get("building_management_role"),
            "registration_id": row.get("registration_id"),
            "registration_contact_id": row.get("registration_contact_id"),
            "building_management_id": row.get("building_management_id"),
            "hpd_contact_display_name": display_name,
            "hpd_contact_verification_key": contact_key,
            "matched_lead_verification_keys": sorted(lead_keys),
            "role_family": role_family,
            "match_rule": "same_bbl_role_aligned_strict_name_key",
            "safe_action_note": (
                "Registered-agent overlap is evidence for legal/registration contact only, not proof of operating management."
                if role_family == "registered_agent"
                else "Role-aligned HPD contact evidence; still require independent sources for higher-risk actions."
            ),
        },
    )


def build_hpd_management_link_claim_spec(row: dict[str, Any]) -> dict[str, Any] | None:
    contact_type = str(row.get("contact_type") or "").strip()
    lead_id = str(row.get("lead_id") or "").strip()
    bbl = str(row.get("bbl") or "").strip()
    contact_id = str(row.get("id") or "").strip()
    if contact_type not in MANAGER_CONTACT_TYPES or not lead_id or not bbl or not contact_id:
        return None

    role_aligned_row = {**row, "building_management_role": row.get("building_management_role") or "manager"}
    spec = build_hpd_role_link_claim_spec(role_aligned_row)
    if spec:
        spec["evidence"]["source_type"] = "hpd_registration_management_company_match"
        spec["evidence"]["source_record_id"] = f"building_contacts:{contact_id}:management_link:{lead_id}:{bbl}"
        spec["evidence"]["evidence_id"] = _evidence_id(
            spec["claim"]["claim_id"],
            spec["evidence"]["source_name"],
            spec["evidence"]["source_record_id"],
        )
        spec["evidence"]["raw_payload"]["match_rule"] = "same_bbl_management_company_strict_name_key"
    return spec


def _normalized_enrichment_source(source: Any) -> str:
    value = str(source or "enrichment").strip().lower()
    if value in {"google", "google_place", "places"}:
        return "google_places"
    if value in {"hunter_domain", "hunter_person"}:
        return "hunter"
    if value in {"nydos", "dos"}:
        return "ny_dos"
    return value or "enrichment"


def build_enrichment_result_claim_specs(row: dict[str, Any]) -> list[dict[str, Any]]:
    enrichment_id = str(row.get("id"))
    lead_id = str(row.get("lead_id") or "").strip()
    if not enrichment_id or not lead_id:
        return []

    source_name = _normalized_enrichment_source(row.get("source"))
    observed_at = row.get("fetched_at") or row.get("observed_at")
    raw_payload = {
        "source": row.get("source"),
        "raw_data": row.get("raw_data") or {},
    }
    fields = [
        ("phone", "has_phone", "phone", "phone"),
        ("email", "has_email", "email", "email"),
        ("website", "has_website", "website", "website"),
        ("owner_principal", "has_owner_principal", "person", "person_contact"),
    ]

    specs: list[dict[str, Any]] = []
    for field, predicate, object_type, claim_type in fields:
        value = str(row.get(field) or "").strip()
        if not value:
            continue
        specs.append(build_materialized_claim(
            subject_type="lead",
            subject_id=lead_id,
            predicate=predicate,
            object_type=object_type,
            object_id=stable_claim_id("enrichment_observation", field, value),
            normalized_value=value,
            claim_type=claim_type,
            source_name=source_name,
            source_type="enrichment_result",
            source_record_id=f"enrichment_results:{enrichment_id}:{field}",
            observed_at=observed_at,
            extracted_value=value,
            raw_payload={**raw_payload, "field": field},
        ))
    return specs


def build_outreach_feedback_claim_specs(row: dict[str, Any]) -> list[dict[str, Any]]:
    lead_id = str(row.get("lead_id") or "").strip()
    bbl = str(row.get("bbl") or "").strip()
    canonical_entity_id = str(row.get("canonical_entity_id") or "").strip()
    target_item_id = str(row.get("target_item_id") or "").strip()
    event_id = row.get("id")
    if event_id is None or not any([lead_id, bbl, canonical_entity_id, target_item_id]):
        return []

    observed_at = row.get("event_timestamp") or row.get("created_at") or row.get("updated_at")
    claims = classify_outreach_feedback(
        lead_id=lead_id or None,
        event_id=int(event_id),
        method=row.get("method"),
        outcome=row.get("outcome"),
        notes=row.get("notes"),
        bbl=bbl or None,
        canonical_entity_id=canonical_entity_id or None,
        target_item_id=target_item_id or None,
    )
    specs: list[dict[str, Any]] = []
    for claim in claims:
        support_status = str(claim.get("support_status") or "supports")
        age = freshness_days(observed_at)
        confidence = compute_confidence(ConfidenceInput(
            claim_type=str(claim["claim_type"]),
            supporting_sources=["outreach_confirmed"] if support_status == "supports" else [],
            contradicting_sources=["outreach_confirmed"] if support_status == "contradicts" else [],
            freshness_days=age,
            source_agreement_count=1 if support_status == "supports" else 0,
            source_disagreement_count=1 if support_status == "contradicts" else 0,
        ))
        if support_status == "contradicts":
            confidence["belief_status"] = "conflicting"
            confidence["actionability_level"] = "do_not_act"

        claim_id = stable_outreach_feedback_id(
            claim["subject_type"],
            claim["subject_id"],
            claim["predicate"],
            claim["object_type"],
            claim["object_id"],
            event_id,
        )
        evidence_id = stable_outreach_feedback_id("evidence", claim_id, event_id, support_status)
        raw_payload = {
            "lead_id": lead_id or None,
            "bbl": bbl or None,
            "canonical_entity_id": canonical_entity_id or None,
            "target_item_id": target_item_id or None,
            "stage": row.get("stage"),
            "method": row.get("method"),
            "outcome": row.get("outcome"),
            "notes": row.get("notes"),
            "normalized_outcome": claim.get("normalized_outcome"),
        }
        specs.append({
            "claim": {
                "claim_id": claim_id,
                "subject_type": claim["subject_type"],
                "subject_id": claim["subject_id"],
                "predicate": claim["predicate"],
                "object_type": claim["object_type"],
                "object_id": claim["object_id"],
                "extracted_value": claim.get("notes") or claim.get("outcome"),
                "normalized_value": claim["normalized_value"],
                "claim_type": claim["claim_type"],
                "freshness_days": age,
                "observed_at": observed_at,
                **confidence,
            },
            "evidence": {
                "evidence_id": evidence_id,
                "claim_id": claim_id,
                "source_name": "outreach_confirmed",
                "source_type": "operator_feedback",
                "source_record_id": f"outreach_events:{event_id}",
                "observed_at": observed_at,
                "extracted_value": claim.get("notes") or claim.get("outcome"),
                "normalized_value": claim["normalized_value"],
                "support_status": support_status,
                "source_quality_score": source_quality("outreach_confirmed"),
                "evidence_weight": 1.0 if support_status == "supports" else -1.0,
                "raw_payload": raw_payload,
            },
        })
    return specs


SIGNAL_SOURCE_SHAPES: dict[str, dict[str, Any]] = {
    "acris_transactions": {
        "source_name": "acris",
        "id_field": "document_id",
        "observed_field": "recorded_date",
        "predicate": "has_recorded_property_transaction",
        "claim_type": "property_transaction",
        "value_fields": ["doc_type_description", "party_type", "party_name", "doc_amount"],
    },
    "dob_permits": {
        "source_name": "dob_permits",
        "id_field": "job_number",
        "observed_field": "issuance_date",
        "fallback_observed_field": "filing_date",
        "predicate": "has_dob_permit_activity",
        "claim_type": "permit_activity",
        "value_fields": ["permit_type", "permit_subtype", "estimated_cost"],
    },
    "hpd_complaints": {
        "source_name": "hpd_complaints",
        "id_field": "complaint_id",
        "observed_field": "received_date",
        "predicate": "has_hpd_complaint",
        "claim_type": "building_condition_signal",
        "value_fields": ["complaint_type", "major_category", "minor_category", "status"],
    },
    "hpd_violations": {
        "source_name": "hpd_violations",
        "id_field": "violation_id",
        "observed_field": "inspection_date",
        "fallback_observed_field": "current_status_date",
        "predicate": "has_hpd_violation",
        "claim_type": "building_condition_signal",
        "value_fields": ["violation_class", "current_status", "nov_description"],
    },
    "hpd_litigation": {
        "source_name": "hpd_litigation",
        "id_field": "litigation_id",
        "observed_field": "case_open_date",
        "predicate": "has_hpd_litigation",
        "claim_type": "building_litigation_signal",
        "value_fields": ["case_type", "case_status", "finding", "penalty"],
    },
    "emergency_repairs": {
        "source_name": "emergency_repairs",
        "id_field": "erp_order_number",
        "observed_field": "order_date",
        "predicate": "has_emergency_repair",
        "claim_type": "building_distress_signal",
        "value_fields": ["repair_type", "amount", "status"],
    },
    "aep_designations": {
        "source_name": "aep_designations",
        "id_field": "id",
        "observed_field": "designation_date",
        "predicate": "has_aep_designation",
        "claim_type": "building_distress_signal",
        "value_fields": ["is_active", "removal_date"],
    },
    "eviction_filings": {
        "source_name": "eviction_filings",
        "id_field": "case_index_number",
        "observed_field": "executed_date",
        "predicate": "has_eviction_filing",
        "claim_type": "building_distress_signal",
        "value_fields": ["borough", "eviction_address"],
    },
    "energy_grades": {
        "source_name": "energy_grades",
        "id_field": "id",
        "observed_field": "year",
        "predicate": "has_energy_grade",
        "claim_type": "building_condition_signal",
        "value_fields": ["grade", "score", "property_name"],
    },
    "facade_inspections": {
        "source_name": "facade_inspections",
        "id_field": "id",
        "observed_field": "filing_date",
        "fallback_observed_field": "inspection_date",
        "predicate": "has_facade_inspection",
        "claim_type": "building_condition_signal",
        "value_fields": ["filing_status", "cycle", "bin"],
    },
    "pad_addresses": {
        "source_name": "pad",
        "id_field": "id",
        "observed_field": "updated_at",
        "predicate": "has_pad_address_reference",
        "claim_type": "building_reference",
        "value_fields": ["bin", "address", "borough"],
    },
}


def _normalize_observed_at(value: Any) -> Any:
    if isinstance(value, int):
        return datetime(value, 12, 31, tzinfo=timezone.utc)
    return value


def build_building_signal_claim_spec(table_name: str, row: dict[str, Any]) -> dict[str, Any] | None:
    shape = SIGNAL_SOURCE_SHAPES.get(table_name)
    bbl = str(row.get("bbl") or "").strip()
    if not shape or not bbl:
        return None

    source_record_key = str(row.get(shape["id_field"]) or row.get("id") or "").strip()
    if not source_record_key:
        return None

    observed_at = _normalize_observed_at(row.get(shape["observed_field"]) or row.get(shape.get("fallback_observed_field")))
    values = [
        f"{field}={row.get(field)}"
        for field in shape["value_fields"]
        if row.get(field) not in (None, "")
    ]
    normalized_value = "; ".join(values) or source_record_key
    raw_payload = {
        field: serialize_dt(value)
        for field, value in row.items()
        if field not in {"created_at", "updated_at"}
    }
    return build_materialized_claim(
        subject_type="building",
        subject_id=bbl,
        predicate=str(shape["predicate"]),
        object_type="source_record",
        object_id=f"{table_name}:{source_record_key}",
        normalized_value=normalized_value,
        claim_type=str(shape["claim_type"]),
        source_name=str(shape["source_name"]),
        source_type=table_name,
        source_record_id=f"{table_name}:{source_record_key}",
        observed_at=observed_at,
        raw_payload=raw_payload,
    )


def _build_reduced_building_signal_preview_spec(row: dict[str, Any]) -> dict[str, Any] | None:
    table_name = str(row.get("source_name") or "").strip()
    shape = SIGNAL_SOURCE_SHAPES.get(table_name)
    bbl = str(row.get("bbl") or "").strip()
    source_record_key = str(row.get("source_record_id") or "").strip()
    if not shape or not bbl or not source_record_key:
        return None

    normalized_value = str(row.get("value") or source_record_key)
    return build_materialized_claim(
        subject_type="building",
        subject_id=bbl,
        predicate=str(shape["predicate"]),
        object_type="source_record",
        object_id=f"{table_name}:{source_record_key}",
        normalized_value=normalized_value,
        claim_type=str(shape["claim_type"]),
        source_name=str(shape["source_name"]),
        source_type=table_name,
        source_record_id=f"{table_name}:{source_record_key}",
        observed_at=_normalize_observed_at(row.get("observed_at")),
        raw_payload={
            "preview_source_table": table_name,
            "preview_value": normalized_value,
        },
    )


async def _materialization_preview(
    session: AsyncSession,
    *,
    limit: int,
    sources: Any = None,
) -> dict[str, Any]:
    selected_sources = normalize_materialization_sources(sources)
    counts_row = (await session.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM building_management WHERE is_current = true) AS building_management_claims,
            (SELECT COUNT(*)
             FROM building_contacts bc
             JOIN building_management bm ON bm.bbl = bc.bbl AND bm.is_current = true
             WHERE bc.contact_type IN ('Agent', 'ManagementCompany', 'Owner', 'CorporateOwner', 'IndividualOwner', 'HeadOfficer', 'Officer', 'Shareholder', 'SiteManager')) AS hpd_contact_role_link_candidates,
            (SELECT COUNT(*)
             FROM building_contacts bc
             JOIN building_management bm ON bm.bbl = bc.bbl AND bm.is_current = true
             WHERE bc.contact_type = 'ManagementCompany') AS hpd_contact_management_link_candidates,
            (SELECT COUNT(*) FROM building_contacts WHERE contact_type IN ('Agent', 'ManagementCompany', 'Owner', 'CorporateOwner', 'IndividualOwner', 'HeadOfficer', 'Officer', 'Shareholder', 'SiteManager')) AS hpd_contact_role_claims,
            (SELECT COUNT(*) FROM building_contacts WHERE contact_type IN ('Agent', 'ManagementCompany', 'Owner', 'CorporateOwner', 'IndividualOwner', 'HeadOfficer', 'Officer', 'Shareholder', 'SiteManager') AND NULLIF(TRIM(COALESCE(business_address, '')), '') IS NOT NULL) AS hpd_contact_address_claims,
            (SELECT COALESCE(SUM(
                CASE WHEN NULLIF(TRIM(COALESCE(phone, '')), '') IS NOT NULL THEN 1 ELSE 0 END +
                CASE WHEN NULLIF(TRIM(COALESCE(email, '')), '') IS NOT NULL THEN 1 ELSE 0 END +
                CASE WHEN NULLIF(TRIM(COALESCE(website, '')), '') IS NOT NULL THEN 1 ELSE 0 END +
                CASE WHEN NULLIF(TRIM(COALESCE(owner_principal, '')), '') IS NOT NULL THEN 1 ELSE 0 END
            ), 0) FROM enrichment_results) AS enrichment_observation_claims,
            (SELECT COUNT(*) FROM leads WHERE retired_at IS NULL AND (NULLIF(phone, '') IS NOT NULL OR NULLIF(email, '') IS NOT NULL OR NULLIF(website, '') IS NOT NULL)) AS lead_contact_claims,
            (SELECT COUNT(*) FROM canonical_entity_leads) AS canonical_membership_claims,
            (SELECT COUNT(*) FROM acris_transactions) AS acris_transaction_claims,
            (SELECT COUNT(*) FROM dob_permits) AS dob_permit_claims,
            (SELECT COUNT(*) FROM hpd_complaints) AS hpd_complaint_claims,
            (SELECT COUNT(*) FROM hpd_violations) AS hpd_violation_claims,
            (SELECT COUNT(*) FROM hpd_litigation) AS hpd_litigation_claims,
            (SELECT COUNT(*) FROM emergency_repairs) AS emergency_repair_claims,
            (SELECT COUNT(*) FROM aep_designations) AS aep_designation_claims,
            (SELECT COUNT(*) FROM eviction_filings) AS eviction_filing_claims,
            (SELECT COUNT(*) FROM energy_grades) AS energy_grade_claims,
            (SELECT COUNT(*) FROM facade_inspections) AS facade_inspection_claims,
            (SELECT COUNT(*) FROM pad_addresses) AS pad_address_claims,
            (SELECT COUNT(*) FROM outreach_events
                WHERE (lead_id IS NOT NULL OR bbl IS NOT NULL OR canonical_entity_id IS NOT NULL OR target_item_id IS NOT NULL)
                  AND (
                    LOWER(COALESCE(outcome, '') || ' ' || COALESCE(notes, '')) LIKE '%wrong_number%'
                    OR LOWER(COALESCE(outcome, '') || ' ' || COALESCE(notes, '')) LIKE '%bad_number%'
                    OR LOWER(COALESCE(outcome, '') || ' ' || COALESCE(notes, '')) LIKE '%disconnected%'
                    OR LOWER(COALESCE(outcome, '') || ' ' || COALESCE(notes, '')) LIKE '%bounce%'
                    OR LOWER(COALESCE(outcome, '') || ' ' || COALESCE(notes, '')) LIKE '%bad_email%'
                    OR LOWER(COALESCE(outcome, '') || ' ' || COALESCE(notes, '')) LIKE '%undeliverable%'
                    OR LOWER(COALESCE(outcome, '') || ' ' || COALESCE(notes, '')) LIKE '%does_not_manage%'
                    OR LOWER(COALESCE(outcome, '') || ' ' || COALESCE(notes, '')) LIKE '%do_not_manage%'
                    OR LOWER(COALESCE(outcome, '') || ' ' || COALESCE(notes, '')) LIKE '%not_the_manager%'
                    OR LOWER(COALESCE(outcome, '') || ' ' || COALESCE(notes, '')) LIKE '%confirmed_manager%'
                    OR LOWER(COALESCE(outcome, '') || ' ' || COALESCE(notes, '')) LIKE '%we_manage%'
                    OR LOWER(COALESCE(outcome, '') || ' ' || COALESCE(notes, '')) LIKE '%they_manage%'
                    OR LOWER(COALESCE(outcome, '') || ' ' || COALESCE(notes, '')) LIKE '%decision_maker%'
                    OR LOWER(COALESCE(outcome, '') || ' ' || COALESCE(notes, '')) LIKE '%confirmed_contact%'
                    OR LOWER(COALESCE(outcome, '') || ' ' || COALESCE(notes, '')) LIKE '%referr%'
                  )
            ) AS outreach_feedback_claims,
            (SELECT COUNT(*) FROM truth_claims) AS existing_claim_count,
            (SELECT COUNT(*) FROM truth_evidence) AS existing_evidence_count
    """))).first()
    counts = dict(counts_row._mapping) if counts_row else {}
    strict_role_link_preview = await _load_strict_hpd_role_link_preview_counts(
        session,
        selected_sources=selected_sources,
    )
    strict_role_link_counts = strict_role_link_preview["strict_counts"]
    sample_materialized_claim_spec_sources: list[dict[str, Any]] = []

    samples = []
    if _source_enabled(selected_sources, "building_management"):
        sample_rows = await session.execute(
            text("""
            SELECT
                bm.id,
                bm.lead_id,
                bm.bbl,
                COALESCE(NULLIF(bm.role, ''), 'manager') AS role,
                bm.registration_start,
                bm.registration_end,
                COALESCE(bm.updated_at, l.updated_at) AS observed_at,
                bm.updated_at
            FROM building_management bm
            JOIN leads l ON l.lead_id = bm.lead_id
            WHERE bm.is_current = true
            ORDER BY bm.updated_at DESC NULLS LAST, bm.id DESC
            LIMIT :limit
        """),
            {"limit": min(limit, 10)},
        )
        for row in sample_rows:
            data = dict(row._mapping)
            role_shape = _building_management_role_claim_shape(data.get("role"))
            if not role_shape:
                continue
            spec = build_materialized_claim(
                subject_type="lead",
                subject_id=str(data["lead_id"]),
                predicate=role_shape["predicate"],
                object_type="building",
                object_id=str(data["bbl"]),
                normalized_value=role_shape["normalized_value"],
                claim_type=role_shape["claim_type"],
                source_name="building_management",
                source_type="derived_hpd_registration_link",
                source_record_id=f"building_management:{data['id']}",
                observed_at=data.get("observed_at"),
                raw_payload={
                    "source_role": data.get("role"),
                    "role_family": role_shape["role_family"],
                    "registration_start": serialize_dt(data.get("registration_start")),
                    "registration_end": serialize_dt(data.get("registration_end")),
                    "safe_action_note": (
                        "This source row carries an Agent role, so it supports registered-agent contact only, not manager verification."
                        if role_shape["role_family"] == "registered_agent"
                        else None
                    ),
                },
            )
            sample_materialized_claim_spec_sources.append(spec)
            payload = dict(data)
            payload["updated_at"] = serialize_dt(payload.get("updated_at"))
            payload["observed_at"] = serialize_dt(payload.get("observed_at"))
            payload["registration_start"] = serialize_dt(payload.get("registration_start"))
            payload["registration_end"] = serialize_dt(payload.get("registration_end"))
            samples.append(payload)

    hpd_samples = []
    if _source_enabled(selected_sources, "hpd_contact_roles") or _source_enabled(selected_sources, "hpd_contact_addresses"):
        hpd_contact_sample_rows = await session.execute(
            text("""
            SELECT
                id,
                bbl,
                registration_contact_id,
                registration_id,
                contact_type,
                description,
                corporation_name,
                first_name,
                last_name,
                title,
                business_address,
                business_city,
                business_state,
                business_zip,
                updated_at,
                updated_at AS observed_at
            FROM building_contacts
            WHERE contact_type IN ('Agent', 'ManagementCompany', 'Owner', 'CorporateOwner', 'IndividualOwner', 'HeadOfficer', 'Officer', 'Shareholder', 'SiteManager')
            ORDER BY updated_at DESC NULLS LAST, id DESC
            LIMIT :limit
        """),
            {"limit": min(limit, 10)},
        )
        for row in hpd_contact_sample_rows:
            data = dict(row._mapping)
            for spec in build_hpd_contact_claim_specs(data):
                claim_type = str(spec.get("claim", {}).get("claim_type") or "")
                source_key = "hpd_contact_addresses" if claim_type == "registered_address" else "hpd_contact_roles"
                if _source_enabled(selected_sources, source_key):
                    sample_materialized_claim_spec_sources.append(spec)
            payload = dict(data)
            payload["updated_at"] = serialize_dt(payload.get("updated_at"))
            payload["observed_at"] = serialize_dt(payload.get("observed_at"))
            hpd_samples.append(payload)

    hpd_management_link_samples = []
    hpd_role_link_samples = []
    if _source_enabled(selected_sources, "hpd_contact_role_links") or _source_enabled(selected_sources, "hpd_contact_management_links"):
        hpd_management_link_rows = await session.execute(
            text("""
            SELECT
                bc.id,
                bc.bbl,
                bc.registration_contact_id,
                bc.registration_id,
                bc.contact_type,
                bc.description,
                bc.corporation_name,
                bc.first_name,
                bc.last_name,
                bc.title,
                bc.business_address,
                bc.business_city,
                bc.business_state,
                bc.business_zip,
                bc.updated_at AS observed_at,
                bm.id AS building_management_id,
                COALESCE(NULLIF(bm.role, ''), 'manager') AS building_management_role,
                bm.lead_id,
                l.normalized_name AS lead_normalized_name,
                l.company_name AS lead_company_name,
                l.agent_name AS lead_agent_name,
                l.owner_name AS lead_owner_name
            FROM building_contacts bc
            JOIN building_management bm ON bm.bbl = bc.bbl AND bm.is_current = true
            JOIN leads l ON l.lead_id = bm.lead_id
            WHERE bc.contact_type IN ('Agent', 'ManagementCompany', 'Owner', 'CorporateOwner', 'IndividualOwner', 'HeadOfficer', 'Officer', 'Shareholder', 'SiteManager')
            ORDER BY bc.updated_at DESC NULLS LAST, bc.id DESC
            LIMIT :limit
        """),
            {"limit": min(limit, 10)},
        )
        for row in hpd_management_link_rows:
            data = dict(row._mapping)
            spec = build_hpd_role_link_claim_spec(data)
            if not spec:
                continue
            payload = dict(data)
            payload["observed_at"] = serialize_dt(payload.get("observed_at"))
            payload["hpd_contact_display_name"] = _contact_display_name(data)
            payload["predicate"] = spec["claim"]["predicate"]
            payload["claim_type"] = spec["claim"]["claim_type"]
            payload["role_family"] = (spec["evidence"].get("raw_payload") or {}).get("role_family")
            if _source_enabled(selected_sources, "hpd_contact_role_links"):
                sample_materialized_claim_spec_sources.append(spec)
                hpd_role_link_samples.append(payload)
            if data.get("contact_type") == "ManagementCompany" and _source_enabled(selected_sources, "hpd_contact_management_links"):
                management_spec = build_hpd_management_link_claim_spec(data)
                if management_spec:
                    sample_materialized_claim_spec_sources.append(management_spec)
                    hpd_management_link_samples.append(payload)

    enrichment_samples = []
    if _source_enabled(selected_sources, "enrichment_observations"):
        enrichment_sample_rows = await session.execute(
            text("""
            SELECT id, lead_id, source, phone, email, website, owner_principal, raw_data, fetched_at
            FROM enrichment_results
            WHERE NULLIF(TRIM(COALESCE(phone, '')), '') IS NOT NULL
               OR NULLIF(TRIM(COALESCE(email, '')), '') IS NOT NULL
               OR NULLIF(TRIM(COALESCE(website, '')), '') IS NOT NULL
               OR NULLIF(TRIM(COALESCE(owner_principal, '')), '') IS NOT NULL
            ORDER BY fetched_at DESC NULLS LAST, id DESC
            LIMIT :limit
        """),
            {"limit": min(limit, 10)},
        )
        for row in enrichment_sample_rows:
            data = dict(row._mapping)
            for spec in build_enrichment_result_claim_specs(data):
                sample_materialized_claim_spec_sources.append(spec)
            payload = dict(data)
            payload["fetched_at"] = serialize_dt(payload.get("fetched_at"))
            enrichment_samples.append(payload)

    signal_samples = []
    signal_preview_sources = {
        "acris_transactions",
        "dob_permits",
        "hpd_violations",
        "hpd_litigation",
    }
    if selected_sources is None or bool(set(selected_sources).intersection(signal_preview_sources)):
        signal_sample_rows = await session.execute(
            text("""
            SELECT 'acris_transactions' AS source_name, document_id AS source_record_id, bbl, recorded_date AS observed_at, doc_type_description AS value
            FROM acris_transactions
            UNION ALL
            SELECT 'dob_permits' AS source_name, job_number AS source_record_id, bbl, COALESCE(issuance_date, filing_date) AS observed_at, permit_type AS value
            FROM dob_permits
            UNION ALL
            SELECT 'hpd_violations' AS source_name, violation_id AS source_record_id, bbl, COALESCE(inspection_date, current_status_date) AS observed_at, violation_class AS value
            FROM hpd_violations
            UNION ALL
            SELECT 'hpd_litigation' AS source_name, litigation_id AS source_record_id, bbl, case_open_date AS observed_at, case_status AS value
            FROM hpd_litigation
            ORDER BY observed_at DESC NULLS LAST
            LIMIT :limit
        """),
            {"limit": min(limit, 10)},
        )
        for row in signal_sample_rows:
            payload = dict(row._mapping)
            source_key = str(payload.get("source_name") or "")
            if not _source_enabled(selected_sources, source_key):
                continue
            spec = _build_reduced_building_signal_preview_spec(payload)
            if spec:
                sample_materialized_claim_spec_sources.append(spec)
            payload["observed_at"] = serialize_dt(payload.get("observed_at"))
            signal_samples.append(payload)

    outreach_samples = []
    if _source_enabled(selected_sources, "outreach_feedback"):
        outreach_sample_rows = await session.execute(
            text("""
            SELECT id, lead_id, bbl, canonical_entity_id, target_item_id, stage, method, outcome, notes, event_timestamp
            FROM outreach_events
            WHERE lead_id IS NOT NULL OR bbl IS NOT NULL OR canonical_entity_id IS NOT NULL OR target_item_id IS NOT NULL
            ORDER BY event_timestamp DESC NULLS LAST, id DESC
            LIMIT :limit
        """),
            {"limit": min(limit, 10)},
        )
        for row in outreach_sample_rows:
            payload = dict(row._mapping)
            payload["event_timestamp"] = serialize_dt(payload.get("event_timestamp"))
            generated_claims = build_outreach_feedback_claim_specs(dict(row._mapping))
            if generated_claims:
                for spec in generated_claims:
                    sample_materialized_claim_spec_sources.append(spec)
                payload["generated_claim_count"] = len(generated_claims)
                payload["predicates"] = [spec["claim"]["predicate"] for spec in generated_claims]
                outreach_samples.append(payload)

    planned_claims = {
        "building_management": int(counts.get("building_management_claims") or 0),
        "hpd_contact_role_links": int(
            strict_role_link_counts.get(
                "hpd_contact_role_links",
                int(counts.get("hpd_contact_role_link_candidates") or 0),
            )
        ),
        "hpd_contact_management_links": int(
            strict_role_link_counts.get(
                "hpd_contact_management_links",
                int(counts.get("hpd_contact_management_link_candidates") or 0),
            )
        ),
        "hpd_contact_roles": int(counts.get("hpd_contact_role_claims") or 0),
        "hpd_contact_addresses": int(counts.get("hpd_contact_address_claims") or 0),
        "enrichment_observations": int(counts.get("enrichment_observation_claims") or 0),
        "lead_contact_paths": int(counts.get("lead_contact_claims") or 0),
        "canonical_entity_memberships": int(counts.get("canonical_membership_claims") or 0),
        "acris_transactions": int(counts.get("acris_transaction_claims") or 0),
        "dob_permits": int(counts.get("dob_permit_claims") or 0),
        "hpd_complaints": int(counts.get("hpd_complaint_claims") or 0),
        "hpd_violations": int(counts.get("hpd_violation_claims") or 0),
        "hpd_litigation": int(counts.get("hpd_litigation_claims") or 0),
        "emergency_repairs": int(counts.get("emergency_repair_claims") or 0),
        "aep_designations": int(counts.get("aep_designation_claims") or 0),
        "eviction_filings": int(counts.get("eviction_filing_claims") or 0),
        "energy_grades": int(counts.get("energy_grade_claims") or 0),
        "facade_inspections": int(counts.get("facade_inspection_claims") or 0),
        "pad_addresses": int(counts.get("pad_address_claims") or 0),
        "outreach_feedback": int(counts.get("outreach_feedback_claims") or 0),
    }
    if selected_sources is not None:
        planned_claims = {source: count for source, count in planned_claims.items() if source in selected_sources}

    return {
        "dry_run": True,
        "run_type": "truth_claim_materialization",
        "supported_sources": list(SUPPORTED_SOURCES),
        "selected_sources": list(selected_sources or SUPPORTED_SOURCES),
        "source_filter_applied": selected_sources is not None,
        "limit": limit,
        "planned_claims_by_source": planned_claims,
        "planned_claims_total": sum(planned_claims.values()),
        "candidate_claims_by_source": {
            "hpd_contact_role_links": int(counts.get("hpd_contact_role_link_candidates") or 0),
            "hpd_contact_management_links": int(counts.get("hpd_contact_management_link_candidates") or 0),
        },
        "strict_materializable_claims_by_source": strict_role_link_counts,
        "strict_materializable_claims_by_predicate": strict_role_link_preview["strict_counts_by_predicate"],
        "existing_claim_count": int(counts.get("existing_claim_count") or 0),
        "existing_evidence_count": int(counts.get("existing_evidence_count") or 0),
        "sample_building_management_claims": samples,
        "sample_hpd_role_link_claims": hpd_role_link_samples,
        "sample_hpd_management_link_claims": hpd_management_link_samples,
        "sample_strict_hpd_role_link_claim_specs": _source_diverse_preview_specs(
            strict_role_link_preview["strict_sample_specs"],
            limit=limit,
        ),
        "sample_hpd_contact_claims": hpd_samples,
        "sample_enrichment_observation_claims": enrichment_samples,
        "sample_building_signal_claims": signal_samples,
        "sample_outreach_feedback_claims": outreach_samples,
        "sample_materialized_claim_specs": _source_diverse_preview_specs(
            sample_materialized_claim_spec_sources,
            limit=limit,
        ),
        "mutations_planned": 0,
        "rollback_strategy": "Preview mode makes no changes. Execute mode only upserts truth_claims/truth_evidence by stable IDs; rollback can delete claims/evidence by run_id recorded in rationale/raw_payload.",
    }


async def _load_materializable_claims(
    session: AsyncSession,
    *,
    limit: int,
    sources: Any = None,
) -> list[dict[str, Any]]:
    selected_sources = normalize_materialization_sources(sources)
    specs: list[dict[str, Any]] = []

    if _source_enabled(selected_sources, "building_management"):
        bm_rows = await session.execute(
            text("""
            SELECT
                bm.id,
                bm.lead_id,
                bm.bbl,
                COALESCE(NULLIF(bm.role, ''), 'manager') AS role,
                bm.registration_start,
                bm.registration_end,
                COALESCE(bm.updated_at, l.updated_at) AS observed_at
            FROM building_management bm
            JOIN leads l ON l.lead_id = bm.lead_id
            WHERE bm.is_current = true
            ORDER BY bm.updated_at DESC NULLS LAST, bm.id DESC
            LIMIT :limit
        """),
            {"limit": limit},
        )
        for row in bm_rows:
            data = dict(row._mapping)
            role_shape = _building_management_role_claim_shape(data.get("role"))
            if not role_shape:
                continue
            specs.append(build_materialized_claim(
                subject_type="lead",
                subject_id=str(data["lead_id"]),
                predicate=role_shape["predicate"],
                object_type="building",
                object_id=str(data["bbl"]),
                normalized_value=role_shape["normalized_value"],
                claim_type=role_shape["claim_type"],
                source_name="building_management",
                source_type="derived_hpd_registration_link",
                source_record_id=f"building_management:{data['id']}",
                observed_at=data.get("observed_at"),
                raw_payload={
                    "source_role": data.get("role"),
                    "role_family": role_shape["role_family"],
                    "registration_start": serialize_dt(data.get("registration_start")),
                    "registration_end": serialize_dt(data.get("registration_end")),
                    "safe_action_note": (
                        "This source row carries an Agent role, so it supports registered-agent contact only, not manager verification."
                        if role_shape["role_family"] == "registered_agent"
                        else None
                    ),
                },
            ))

    if _source_enabled(selected_sources, "hpd_contact_roles") or _source_enabled(selected_sources, "hpd_contact_addresses"):
        hpd_contact_rows = await session.execute(
            text("""
            SELECT
                id,
                bbl,
                registration_contact_id,
                registration_id,
                contact_type,
                description,
                corporation_name,
                first_name,
                last_name,
                title,
                business_address,
                business_city,
                business_state,
                business_zip,
                updated_at AS observed_at
            FROM building_contacts
            WHERE contact_type IN ('Agent', 'ManagementCompany', 'Owner', 'CorporateOwner', 'IndividualOwner', 'HeadOfficer', 'Officer', 'Shareholder', 'SiteManager')
            ORDER BY updated_at DESC NULLS LAST, id DESC
            LIMIT :limit
        """),
            {"limit": limit},
        )
        for row in hpd_contact_rows:
            for spec in build_hpd_contact_claim_specs(dict(row._mapping)):
                claim_type = str(spec.get("claim", {}).get("claim_type") or "")
                source_key = "hpd_contact_addresses" if claim_type == "registered_address" else "hpd_contact_roles"
                if _source_enabled(selected_sources, source_key):
                    specs.append(spec)

    if _source_enabled(selected_sources, "hpd_contact_role_links") or _source_enabled(selected_sources, "hpd_contact_management_links"):
        hpd_management_link_rows = await session.execute(
            text("""
            SELECT
                bc.id,
                bc.bbl,
                bc.registration_contact_id,
                bc.registration_id,
                bc.contact_type,
                bc.description,
                bc.corporation_name,
                bc.first_name,
                bc.last_name,
                bc.title,
                bc.business_address,
                bc.business_city,
                bc.business_state,
                bc.business_zip,
                bc.updated_at AS observed_at,
                bm.id AS building_management_id,
                COALESCE(NULLIF(bm.role, ''), 'manager') AS building_management_role,
                bm.lead_id,
                l.normalized_name AS lead_normalized_name,
                l.company_name AS lead_company_name,
                l.agent_name AS lead_agent_name,
                l.owner_name AS lead_owner_name
            FROM building_contacts bc
            JOIN building_management bm ON bm.bbl = bc.bbl AND bm.is_current = true
            JOIN leads l ON l.lead_id = bm.lead_id
            WHERE bc.contact_type IN ('Agent', 'ManagementCompany', 'Owner', 'CorporateOwner', 'IndividualOwner', 'HeadOfficer', 'Officer', 'Shareholder', 'SiteManager')
            ORDER BY bc.updated_at DESC NULLS LAST, bc.id DESC
            LIMIT :limit
        """),
            {"limit": limit},
        )
        for row in hpd_management_link_rows:
            data = dict(row._mapping)
            spec = build_hpd_role_link_claim_spec(data)
            if spec and _source_enabled(selected_sources, "hpd_contact_role_links"):
                specs.append(spec)
            if data.get("contact_type") == "ManagementCompany" and _source_enabled(selected_sources, "hpd_contact_management_links"):
                management_spec = build_hpd_management_link_claim_spec(data)
                if management_spec:
                    specs.append(management_spec)

    if _source_enabled(selected_sources, "enrichment_observations"):
        enrichment_rows = await session.execute(
            text("""
            SELECT id, lead_id, source, phone, email, website, owner_principal, raw_data, fetched_at
            FROM enrichment_results
            WHERE NULLIF(TRIM(COALESCE(phone, '')), '') IS NOT NULL
               OR NULLIF(TRIM(COALESCE(email, '')), '') IS NOT NULL
               OR NULLIF(TRIM(COALESCE(website, '')), '') IS NOT NULL
               OR NULLIF(TRIM(COALESCE(owner_principal, '')), '') IS NOT NULL
            ORDER BY fetched_at DESC NULLS LAST, id DESC
            LIMIT :limit
        """),
            {"limit": limit},
        )
        for row in enrichment_rows:
            specs.extend(build_enrichment_result_claim_specs(dict(row._mapping)))

    if _source_enabled(selected_sources, "lead_contact_paths"):
        contact_rows = await session.execute(
            text("""
            SELECT lead_id, phone, email, website, COALESCE(last_enriched, updated_at) AS observed_at
            FROM leads
            WHERE retired_at IS NULL
              AND (NULLIF(phone, '') IS NOT NULL OR NULLIF(email, '') IS NOT NULL OR NULLIF(website, '') IS NOT NULL)
            ORDER BY COALESCE(last_enriched, updated_at) DESC NULLS LAST, lead_id
            LIMIT :limit
        """),
            {"limit": limit},
        )
        for row in contact_rows:
            data = dict(row._mapping)
            channels = [name for name in ("phone", "email", "website") if data.get(name)]
            specs.append(build_materialized_claim(
                subject_type="lead",
                subject_id=str(data["lead_id"]),
                predicate="has_valid_contact_path",
                object_type="contact_set",
                object_id=f"lead:{data['lead_id']}:contacts",
                normalized_value=", ".join(channels),
                claim_type="person_contact",
                source_name="legacy_leads",
                source_type="lead_profile_contact_fields",
                source_record_id=f"leads:{data['lead_id']}:contacts",
                observed_at=data.get("observed_at"),
                raw_payload={channel: data.get(channel) for channel in channels},
            ))

    if _source_enabled(selected_sources, "canonical_entity_memberships"):
        canonical_rows = await session.execute(
            text("""
            SELECT
                cel.lead_id,
                cel.canonical_entity_id,
                COALESCE(cel.relationship_type, 'member') AS relationship_type,
                cel.confidence_score AS membership_confidence,
                COALESCE(cel.updated_at, ce.updated_at) AS observed_at
            FROM canonical_entity_leads cel
            JOIN canonical_entities ce ON ce.canonical_entity_id = cel.canonical_entity_id
            ORDER BY cel.updated_at DESC NULLS LAST, cel.lead_id
            LIMIT :limit
        """),
            {"limit": limit},
        )
        for row in canonical_rows:
            data = dict(row._mapping)
            specs.append(build_materialized_claim(
                subject_type="lead",
                subject_id=str(data["lead_id"]),
                predicate="maps_to_canonical_entity",
                object_type="canonical_entity",
                object_id=str(data["canonical_entity_id"]),
                normalized_value=str(data["relationship_type"] or "member"),
                claim_type="entity_identity",
                source_name="canonical_entity_leads",
                source_type="canonical_entity_graph",
                source_record_id=f"canonical_entity_leads:{data['lead_id']}:{data['canonical_entity_id']}",
                observed_at=data.get("observed_at"),
                raw_payload={"membership_confidence": data.get("membership_confidence")},
            ))

    if _source_enabled(selected_sources, "outreach_feedback"):
        outreach_rows = await session.execute(
            text("""
            SELECT id, lead_id, bbl, canonical_entity_id, target_item_id, stage, method, outcome, notes, event_timestamp, created_at, updated_at
            FROM outreach_events
            WHERE lead_id IS NOT NULL OR bbl IS NOT NULL OR canonical_entity_id IS NOT NULL OR target_item_id IS NOT NULL
            ORDER BY event_timestamp DESC NULLS LAST, id DESC
            LIMIT :limit
        """),
            {"limit": limit},
        )
        for row in outreach_rows:
            specs.extend(build_outreach_feedback_claim_specs(dict(row._mapping)))

    signal_queries = {
        "acris_transactions": """
            SELECT id, document_id, bbl, doc_type, doc_type_description, recorded_date, doc_amount, party_type, party_name
            FROM acris_transactions
            ORDER BY recorded_date DESC NULLS LAST, id DESC
            LIMIT :limit
        """,
        "dob_permits": """
            SELECT id, job_number, bbl, bin, permit_type, permit_subtype, filing_date, issuance_date, expiration_date, estimated_cost
            FROM dob_permits
            ORDER BY COALESCE(issuance_date, filing_date) DESC NULLS LAST, id DESC
            LIMIT :limit
        """,
        "hpd_complaints": """
            SELECT id, complaint_id, bbl, building_id, status, status_date, complaint_type, major_category, minor_category, received_date
            FROM hpd_complaints
            ORDER BY received_date DESC NULLS LAST, id DESC
            LIMIT :limit
        """,
        "hpd_violations": """
            SELECT id, violation_id, bbl, building_id, violation_class, inspection_date, current_status, current_status_date, nov_description
            FROM hpd_violations
            ORDER BY COALESCE(inspection_date, current_status_date) DESC NULLS LAST, id DESC
            LIMIT :limit
        """,
        "hpd_litigation": """
            SELECT id, litigation_id, bbl, building_id, case_type, case_status, case_open_date, case_close_date, finding, penalty
            FROM hpd_litigation
            ORDER BY case_open_date DESC NULLS LAST, id DESC
            LIMIT :limit
        """,
        "emergency_repairs": """
            SELECT id, erp_order_number, bbl, building_id, order_date, repair_type, amount, status
            FROM emergency_repairs
            ORDER BY order_date DESC NULLS LAST, id DESC
            LIMIT :limit
        """,
        "aep_designations": """
            SELECT id, bbl, building_id, designation_date, removal_date, is_active
            FROM aep_designations
            ORDER BY designation_date DESC NULLS LAST, id DESC
            LIMIT :limit
        """,
        "eviction_filings": """
            SELECT id, case_index_number, bbl, executed_date, eviction_address, borough
            FROM eviction_filings
            ORDER BY executed_date DESC NULLS LAST, id DESC
            LIMIT :limit
        """,
        "energy_grades": """
            SELECT id, bbl, grade, score, year, property_name, address
            FROM energy_grades
            ORDER BY year DESC NULLS LAST, id DESC
            LIMIT :limit
        """,
        "facade_inspections": """
            SELECT id, bbl, bin, filing_date, filing_status, inspection_date, report_filing_date, cycle
            FROM facade_inspections
            ORDER BY COALESCE(filing_date, inspection_date) DESC NULLS LAST, id DESC
            LIMIT :limit
        """,
        "pad_addresses": """
            SELECT id, bin, bbl, address, borough, updated_at
            FROM pad_addresses
            ORDER BY updated_at DESC NULLS LAST, id DESC
            LIMIT :limit
        """,
    }
    for table_name, query in signal_queries.items():
        if not _source_enabled(selected_sources, table_name):
            continue
        signal_rows = await session.execute(text(query), {"limit": limit})
        for row in signal_rows:
            spec = build_building_signal_claim_spec(table_name, dict(row._mapping))
            if spec:
                specs.append(spec)

    return specs


async def _upsert_materialized_claim(
    session: AsyncSession,
    *,
    spec: dict[str, Any],
    run_id: str,
) -> None:
    claim = spec["claim"]
    evidence = spec["evidence"]
    rationale = {
        **(claim.get("rationale") or {}),
        "materialization_run_id": run_id,
        "materialized_from": evidence["source_name"],
    }
    raw_payload = {
        **(evidence.get("raw_payload") or {}),
        "materialization_run_id": run_id,
    }
    await session.execute(
        text("""
            INSERT INTO truth_claims (
                claim_id, subject_type, subject_id, predicate, object_type, object_id,
                extracted_value, normalized_value, claim_type, belief_status, confidence_score,
                freshness_days, observed_at, current_flag, actionability_level, rationale,
                created_at, updated_at
            )
            VALUES (
                :claim_id, :subject_type, :subject_id, :predicate, :object_type, :object_id,
                :extracted_value, :normalized_value, :claim_type, :belief_status, :confidence_score,
                :freshness_days, :observed_at, true, :actionability_level, CAST(:rationale AS JSONB),
                NOW(), NOW()
            )
            ON CONFLICT (claim_id)
            DO UPDATE SET
                extracted_value = EXCLUDED.extracted_value,
                normalized_value = EXCLUDED.normalized_value,
                belief_status = EXCLUDED.belief_status,
                confidence_score = EXCLUDED.confidence_score,
                freshness_days = EXCLUDED.freshness_days,
                observed_at = EXCLUDED.observed_at,
                actionability_level = EXCLUDED.actionability_level,
                rationale = EXCLUDED.rationale,
                updated_at = NOW()
        """),
        {
            **claim,
            "rationale": json.dumps(rationale),
        },
    )
    await session.execute(
        text("""
            INSERT INTO truth_evidence (
                evidence_id, claim_id, source_name, source_type, source_record_id,
                observed_at, extracted_value, normalized_value, support_status,
                source_quality_score, evidence_weight, raw_payload, created_at, updated_at
            )
            VALUES (
                :evidence_id, :claim_id, :source_name, :source_type, :source_record_id,
                :observed_at, :extracted_value, :normalized_value, :support_status,
                :source_quality_score, :evidence_weight, CAST(:raw_payload AS JSONB), NOW(), NOW()
            )
            ON CONFLICT (evidence_id)
            DO UPDATE SET
                observed_at = EXCLUDED.observed_at,
                extracted_value = EXCLUDED.extracted_value,
                normalized_value = EXCLUDED.normalized_value,
                support_status = EXCLUDED.support_status,
                source_quality_score = EXCLUDED.source_quality_score,
                evidence_weight = EXCLUDED.evidence_weight,
                raw_payload = EXCLUDED.raw_payload,
                updated_at = NOW()
        """),
        {
            **evidence,
            "raw_payload": json.dumps(raw_payload),
        },
    )


async def _upsert_confidence_snapshot(
    session: AsyncSession,
    *,
    snapshot: dict[str, Any],
) -> None:
    await session.execute(
        text("""
            INSERT INTO confidence_snapshots (
                snapshot_id, entity_type, entity_id, confidence_scope, confidence_score,
                actionability_level, supporting_claim_count, contradicting_claim_count,
                stale_claim_count, computed_at, rationale, run_id, created_at, updated_at
            )
            VALUES (
                :snapshot_id, :entity_type, :entity_id, :confidence_scope, :confidence_score,
                :actionability_level, :supporting_claim_count, :contradicting_claim_count,
                :stale_claim_count, :computed_at, CAST(:rationale AS JSONB), :run_id, NOW(), NOW()
            )
            ON CONFLICT (snapshot_id)
            DO UPDATE SET
                confidence_score = EXCLUDED.confidence_score,
                actionability_level = EXCLUDED.actionability_level,
                supporting_claim_count = EXCLUDED.supporting_claim_count,
                contradicting_claim_count = EXCLUDED.contradicting_claim_count,
                stale_claim_count = EXCLUDED.stale_claim_count,
                computed_at = EXCLUDED.computed_at,
                rationale = EXCLUDED.rationale,
                updated_at = NOW()
        """),
        {
            **snapshot,
            "rationale": json.dumps(snapshot.get("rationale") or {}),
        },
    )


async def _load_truth_table_counts(session: AsyncSession) -> dict[str, int]:
    row = (await session.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM truth_claims)::int AS truth_claim_count,
            (SELECT COUNT(*) FROM truth_evidence)::int AS truth_evidence_count,
            (SELECT COUNT(*) FROM confidence_snapshots)::int AS confidence_snapshot_count,
            (SELECT COUNT(*) FROM truth_review_items WHERE status = 'open')::int AS open_review_count
    """))).first()
    counts = dict(row._mapping) if row else {}
    return {
        "truth_claim_count": int(counts.get("truth_claim_count") or 0),
        "truth_evidence_count": int(counts.get("truth_evidence_count") or 0),
        "confidence_snapshot_count": int(counts.get("confidence_snapshot_count") or 0),
        "open_review_count": int(counts.get("open_review_count") or 0),
    }


async def _load_existing_ids(
    session: AsyncSession,
    *,
    table_name: str,
    id_column: str,
    ids: list[str],
) -> set[str]:
    unique_ids = sorted({str(value) for value in ids if value})
    if not unique_ids:
        return set()
    rows = await session.execute(
        text(f"""
            SELECT {id_column} AS existing_id
            FROM {table_name}
            WHERE {id_column} = ANY(:ids)
        """),
        {"ids": unique_ids},
    )
    return {str(row._mapping["existing_id"]) for row in rows}


async def _load_before_snapshot_samples(
    session: AsyncSession,
    *,
    table_name: str,
    id_column: str,
    columns: list[str],
    ids: set[str],
    limit: int = 25,
) -> list[dict[str, Any]]:
    unique_ids = sorted({str(value) for value in ids if value})[:limit]
    if not unique_ids:
        return []
    selected_columns = ", ".join(columns)
    rows = await session.execute(
        text(f"""
            SELECT {selected_columns}
            FROM {table_name}
            WHERE {id_column} = ANY(:ids)
            ORDER BY {id_column}
            LIMIT :limit
        """),
        {"ids": unique_ids, "limit": limit},
    )
    samples: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row._mapping)
        samples.append({key: serialize_dt(value) for key, value in payload.items()})
    return samples


async def _load_before_snapshots_by_id(
    session: AsyncSession,
    *,
    table_name: str,
    id_column: str,
    columns: list[str],
    ids: set[str],
) -> dict[str, dict[str, Any]]:
    unique_ids = sorted({str(value) for value in ids if value})
    if not unique_ids:
        return {}
    selected_columns = ", ".join(columns)
    rows = await session.execute(
        text(f"""
            SELECT {selected_columns}
            FROM {table_name}
            WHERE {id_column} = ANY(:ids)
        """),
        {"ids": unique_ids},
    )
    snapshots: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = dict(row._mapping)
        item_id = str(payload.get(id_column))
        snapshots[item_id] = {key: serialize_dt(value) for key, value in payload.items()}
    return snapshots


async def _upsert_materialization_manifest_entries(
    session: AsyncSession,
    *,
    entries: list[dict[str, Any]],
) -> None:
    for entry in entries:
        await session.execute(
            text("""
                INSERT INTO truth_materialization_manifest (
                    run_id, item_type, item_id, was_existing, before_snapshot, created_at, updated_at
                )
                VALUES (
                    :run_id, :item_type, :item_id, :was_existing, CAST(:before_snapshot AS JSONB), NOW(), NOW()
                )
                ON CONFLICT (run_id, item_type, item_id)
                DO UPDATE SET
                    was_existing = EXCLUDED.was_existing,
                    before_snapshot = EXCLUDED.before_snapshot,
                    updated_at = NOW()
            """),
            {
                **entry,
                "before_snapshot": json.dumps(entry.get("before_snapshot")) if entry.get("before_snapshot") else None,
            },
        )


async def materialize_truth_claims(
    session: AsyncSession,
    *,
    limit: int = 500,
    dry_run: bool = True,
    confirm_execute: bool = False,
    run_id: str | None = None,
    sources: Any = None,
) -> dict[str, Any]:
    selected_sources = normalize_materialization_sources(sources)
    if dry_run or not confirm_execute:
        return await _materialization_preview(session, limit=limit, sources=selected_sources)

    run_id = run_id or f"truth-materialization-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
    before_counts = await _load_truth_table_counts(session)
    specs = await _load_materializable_claims(session, limit=limit, sources=selected_sources)
    snapshots = build_confidence_snapshots_from_specs(specs, run_id=run_id)
    claim_ids = [str(spec["claim"]["claim_id"]) for spec in specs if spec.get("claim", {}).get("claim_id")]
    evidence_ids = [str(spec["evidence"]["evidence_id"]) for spec in specs if spec.get("evidence", {}).get("evidence_id")]
    snapshot_ids = [str(snapshot["snapshot_id"]) for snapshot in snapshots if snapshot.get("snapshot_id")]
    existing_claim_ids = await _load_existing_ids(
        session,
        table_name="truth_claims",
        id_column="claim_id",
        ids=claim_ids,
    )
    existing_evidence_ids = await _load_existing_ids(
        session,
        table_name="truth_evidence",
        id_column="evidence_id",
        ids=evidence_ids,
    )
    existing_snapshot_ids = await _load_existing_ids(
        session,
        table_name="confidence_snapshots",
        id_column="snapshot_id",
        ids=snapshot_ids,
    )
    claim_snapshot_columns = [
        "claim_id",
        "extracted_value",
        "normalized_value",
        "belief_status",
        "confidence_score",
        "freshness_days",
        "observed_at",
        "actionability_level",
        "rationale",
        "updated_at",
    ]
    evidence_snapshot_columns = [
        "evidence_id",
        "claim_id",
        "observed_at",
        "extracted_value",
        "normalized_value",
        "support_status",
        "source_quality_score",
        "evidence_weight",
        "raw_payload",
        "updated_at",
    ]
    confidence_snapshot_columns = [
        "snapshot_id",
        "confidence_score",
        "actionability_level",
        "supporting_claim_count",
        "contradicting_claim_count",
        "stale_claim_count",
        "computed_at",
        "rationale",
        "updated_at",
    ]
    before_claim_snapshots_by_id = await _load_before_snapshots_by_id(
        session,
        table_name="truth_claims",
        id_column="claim_id",
        columns=claim_snapshot_columns,
        ids=existing_claim_ids,
    )
    before_evidence_snapshots_by_id = await _load_before_snapshots_by_id(
        session,
        table_name="truth_evidence",
        id_column="evidence_id",
        columns=evidence_snapshot_columns,
        ids=existing_evidence_ids,
    )
    before_confidence_snapshots_by_id = await _load_before_snapshots_by_id(
        session,
        table_name="confidence_snapshots",
        id_column="snapshot_id",
        columns=confidence_snapshot_columns,
        ids=existing_snapshot_ids,
    )
    before_snapshot_samples = {
        "truth_claims": list(before_claim_snapshots_by_id.values())[:25],
        "truth_evidence": list(before_evidence_snapshots_by_id.values())[:25],
        "confidence_snapshots": list(before_confidence_snapshots_by_id.values())[:25],
    }
    rollback_plan = build_materialization_rollback_plan(
        run_id=run_id,
        claim_ids=claim_ids,
        evidence_ids=evidence_ids,
        snapshot_ids=snapshot_ids,
        existing_claim_ids=existing_claim_ids,
        existing_evidence_ids=existing_evidence_ids,
        existing_snapshot_ids=existing_snapshot_ids,
        before_snapshot_samples=before_snapshot_samples,
    )
    manifest_entries = [
        *build_materialization_manifest_entries(
            run_id=run_id,
            item_type="truth_claim",
            item_ids=claim_ids,
            existing_item_ids=existing_claim_ids,
            before_snapshots_by_id=before_claim_snapshots_by_id,
        ),
        *build_materialization_manifest_entries(
            run_id=run_id,
            item_type="truth_evidence",
            item_ids=evidence_ids,
            existing_item_ids=existing_evidence_ids,
            before_snapshots_by_id=before_evidence_snapshots_by_id,
        ),
        *build_materialization_manifest_entries(
            run_id=run_id,
            item_type="confidence_snapshot",
            item_ids=snapshot_ids,
            existing_item_ids=existing_snapshot_ids,
            before_snapshots_by_id=before_confidence_snapshots_by_id,
        ),
    ]
    await _upsert_materialization_manifest_entries(session, entries=manifest_entries)
    inserted_by_source: dict[str, int] = {}
    for spec in specs:
        source = str(spec["evidence"]["source_name"])
        await _upsert_materialized_claim(session, spec=spec, run_id=run_id)
        inserted_by_source[source] = inserted_by_source.get(source, 0) + 1
    for snapshot in snapshots:
        await _upsert_confidence_snapshot(session, snapshot=snapshot)
    await session.commit()
    after_counts = await _load_truth_table_counts(session)

    return {
        "dry_run": False,
        "run_type": "truth_claim_materialization",
        "run_id": run_id,
        "limit": limit,
        "selected_sources": list(selected_sources or SUPPORTED_SOURCES),
        "source_filter_applied": selected_sources is not None,
        "before_counts": before_counts,
        "after_counts": after_counts,
        "claims_upserted": len(specs),
        "evidence_upserted": len(specs),
        "confidence_snapshots_upserted": len(snapshots),
        "claims_upserted_by_source": inserted_by_source,
        "skipped_claims": 0,
        "conflicts": [],
        "rollback_plan": rollback_plan,
        "rollback_manifest": _manifest_summary(manifest_entries),
        "rollback_strategy": rollback_plan["rollback_strategy"],
    }
