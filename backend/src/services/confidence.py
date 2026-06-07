"""Deterministic confidence and actionability rules for truth claims."""

from __future__ import annotations

from dataclasses import dataclass
from math import exp
from typing import Any


SOURCE_QUALITY: dict[str, float] = {
    "outreach_confirmed": 0.98,
    "hpd_management_company": 0.86,
    "hpm_revenue_by_property_summary": 0.78,
    "operator_review": 0.86,
    "manual_evidence": 0.78,
    "hpd_contacts": 0.82,
    "hpd_registrations": 0.80,
    "building_management": 0.78,
    "ny_dos": 0.74,
    "company_website": 0.72,
    "ny_dps_order_entry": 0.76,
    "verizon_order_entry_petition": 0.73,
    "openigloo": 0.58,
    "renthistory": 0.56,
    "redfin": 0.60,
    "homes": 0.60,
    "renthop": 0.60,
    "google_places": 0.65,
    "hunter": 0.61,
    "acris": 0.62,
    "dob_permits": 0.60,
    "hpd_violations": 0.64,
    "hpd_complaints": 0.57,
    "hpd_litigation": 0.66,
    "emergency_repairs": 0.63,
    "aep_designations": 0.68,
    "eviction_filings": 0.58,
    "energy_grades": 0.56,
    "facade_inspections": 0.55,
    "pad": 0.54,
    "pluto": 0.58,
    "web_crawl": 0.55,
    "enrichment": 0.50,
    "legacy_leads": 0.45,
}
CONFIDENCE_POLICY_VERSION = "truth-confidence-v2"
VERIFIED_CONFIDENCE_THRESHOLD = 0.90
QUALITY_BASIS_SOURCE_COUNT = 2

CLAIM_TYPE_RISK: dict[str, float] = {
    "entity_identity": 0.10,
    "building_management": 0.18,
    "building_ownership": 0.22,
    "registered_agent": 0.28,
    "person_contact": 0.24,
    "website": 0.16,
    "phone": 0.14,
    "email": 0.14,
    "mailing_address": 0.20,
    "property_transaction": 0.18,
    "permit_activity": 0.20,
    "building_condition_signal": 0.18,
    "building_litigation_signal": 0.22,
    "building_distress_signal": 0.22,
    "building_reference": 0.12,
}

@dataclass(frozen=True)
class ConfidenceInput:
    claim_type: str
    supporting_sources: list[str]
    contradicting_sources: list[str]
    freshness_days: int | None
    source_agreement_count: int = 0
    source_disagreement_count: int = 0


@dataclass(frozen=True)
class ActionabilityRule:
    level: str
    minimum_score: float
    max_contradictions: int
    max_freshness_days: int
    min_supporting_sources: int
    min_supporting_evidence: int


ACTION_THRESHOLDS = [
    ActionabilityRule("acquisition_quality_diligence", 0.90, 0, 60, 2, 2),
    ActionabilityRule("recommended_outreach", 0.78, 1, 120, 1, 1),
    ActionabilityRule("automated_enrichment", 0.70, 1, 240, 1, 1),
    ActionabilityRule("ranked_sourcing", 0.64, 2, 180, 1, 1),
    ActionabilityRule("broad_discovery", 0.45, 99, 365, 1, 1),
]


def source_quality(source_name: str | None) -> float:
    if not source_name:
        return 0.4
    return SOURCE_QUALITY.get(source_name, 0.5)


def freshness_factor(freshness_days: int | None) -> float:
    if freshness_days is None:
        return 0.65
    if freshness_days <= 7:
        return 1.0
    if freshness_days <= 30:
        return 0.93
    if freshness_days <= 90:
        return 0.82
    if freshness_days <= 180:
        return 0.68
    if freshness_days <= 365:
        return 0.50
    return 0.35


def _agreement_bonus(count: int) -> float:
    if count <= 1:
        return 0.0
    return min(0.16, 0.05 * (count - 1))


def _contradiction_penalty(count: int, sources: list[str]) -> float:
    if count <= 0 and not sources:
        return 0.0
    weighted = sum(source_quality(source) for source in sources) or (0.55 * count)
    return min(0.45, 0.12 * max(count, len(sources)) + 0.12 * weighted / max(1, len(sources) or count))


def _distinct_sources(sources: list[str]) -> list[str]:
    return list(dict.fromkeys(source for source in sources if str(source or "").strip()))


def compute_confidence(input_data: ConfidenceInput) -> dict[str, Any]:
    support_sources = _distinct_sources(input_data.supporting_sources or [])
    contradiction_sources = _distinct_sources(input_data.contradicting_sources or [])
    source_quality_scores = [
        {"source_name": source, "quality": source_quality(source)}
        for source in support_sources
    ]
    if support_sources:
        all_source_average = sum(item["quality"] for item in source_quality_scores) / len(support_sources)
        quality_basis_scores = sorted(
            source_quality_scores,
            key=lambda item: (-item["quality"], item["source_name"]),
        )[:QUALITY_BASIS_SOURCE_COUNT]
        base = sum(item["quality"] for item in quality_basis_scores) / len(quality_basis_scores)
    else:
        all_source_average = 0.25
        quality_basis_scores = []
        base = 0.25

    risk_penalty = CLAIM_TYPE_RISK.get(input_data.claim_type, 0.20)
    agreement = max(input_data.source_agreement_count, len(set(support_sources)))
    disagreement = max(input_data.source_disagreement_count, len(contradiction_sources))
    freshness = freshness_factor(input_data.freshness_days)

    raw = (
        base * 0.72
        + freshness * 0.22
        + _agreement_bonus(agreement)
        - _contradiction_penalty(disagreement, contradiction_sources)
        - (risk_penalty * 0.50)
    )
    score = max(0.0, min(1.0, raw))
    # Smooth the mid-range so a single okay source does not look falsely binary.
    score = max(0.0, min(1.0, 1 / (1 + exp(-5 * (score - 0.5)))))
    status = belief_status(score=score, contradictions=disagreement)
    actionability = actionability_level(
        score=score,
        contradictions=disagreement,
        freshness_days=input_data.freshness_days,
        supporting_source_count=len(set(support_sources)),
        supporting_evidence_count=agreement,
    )
    return {
        "confidence_score": round(score, 3),
        "belief_status": status,
        "actionability_level": actionability,
        "rationale": {
            "confidence_policy_version": CONFIDENCE_POLICY_VERSION,
            "claim_type": input_data.claim_type,
            "supporting_sources": support_sources,
            "contradicting_sources": contradiction_sources,
            "source_quality_scores": source_quality_scores,
            "average_supporting_source_quality": round(all_source_average, 3),
            "confidence_source_quality_basis": "strongest_distinct_supporting_sources",
            "confidence_source_quality_basis_count": QUALITY_BASIS_SOURCE_COUNT,
            "confidence_source_quality_basis_average": round(base, 3),
            "confidence_source_quality_basis_scores": quality_basis_scores,
            "raw_confidence_before_smoothing": round(raw, 3),
            "verified_confidence_threshold": VERIFIED_CONFIDENCE_THRESHOLD,
            "freshness_days": input_data.freshness_days,
            "freshness_factor": round(freshness, 3),
            "source_agreement_count": agreement,
            "source_disagreement_count": disagreement,
            "supporting_source_count": len(set(support_sources)),
            "supporting_evidence_count": agreement,
            "claim_type_risk_penalty": risk_penalty,
        },
    }


def belief_status(*, score: float, contradictions: int) -> str:
    if contradictions >= 2 and score < 0.82:
        return "conflicting"
    if score >= VERIFIED_CONFIDENCE_THRESHOLD:
        return "verified"
    if score >= 0.70:
        return "likely"
    if score >= 0.45:
        return "proposed"
    return "insufficient_evidence"


def actionability_level(
    *,
    score: float,
    contradictions: int,
    freshness_days: int | None,
    supporting_source_count: int = 0,
    supporting_evidence_count: int = 0,
) -> str:
    age = 9999 if freshness_days is None else freshness_days
    for rule in ACTION_THRESHOLDS:
        if (
            score >= rule.minimum_score
            and contradictions <= rule.max_contradictions
            and age <= rule.max_freshness_days
            and supporting_source_count >= rule.min_supporting_sources
            and supporting_evidence_count >= rule.min_supporting_evidence
        ):
            return rule.level
    return "do_not_act"


def review_bucket(*, confidence_score: float, contradictions: int, safe_to_execute: bool = False) -> str:
    if safe_to_execute and confidence_score >= VERIFIED_CONFIDENCE_THRESHOLD and contradictions == 0:
        return "safe_auto_accept"
    if contradictions > 0:
        return "conflicting_evidence"
    if confidence_score >= 0.78:
        return "suggested_merge"
    if confidence_score >= 0.45:
        return "needs_human_review"
    return "insufficient_evidence"
