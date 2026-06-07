"""Golden-set benchmark evaluation for the truth/confidence program."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.truth_program import GOLDEN_CASE_SEEDS, serialize_dt


METRIC_KEYS = (
    "precision",
    "recall",
    "false_merge_rate",
    "false_split_rate",
    "building_link_accuracy",
    "contact_accuracy",
    "freshness_accuracy",
)
REQUIRED_TRICKY_FEATURES = (
    "co-op board",
    "owner corporation",
    "LLC suffix variants",
    "shared legal address",
    "sponsor ambiguity",
    "law firm address",
    "stale DOS record",
    "HPD contact conflict",
    "shared address",
    "false merge risk",
    "false split risk",
    "wrong contact",
    "outreach contradiction",
)


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _normalize_expected_claims(value: Any) -> dict[str, list[dict[str, Any]]]:
    if isinstance(value, list):
        return {"required_claims": _as_list(value), "forbidden_claims": []}
    if not isinstance(value, dict):
        return {"required_claims": [], "forbidden_claims": []}
    return {
        "required_claims": _as_list(value.get("required_claims") or value.get("required") or []),
        "forbidden_claims": _as_list(value.get("forbidden_claims") or value.get("forbidden") or []),
    }


def _text_eq(left: Any, right: Any) -> bool:
    return str(left or "").strip().lower() == str(right or "").strip().lower()


def _claim_matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    exact_fields = (
        "subject_type",
        "subject_id",
        "predicate",
        "object_type",
        "object_id",
        "claim_type",
        "belief_status",
        "actionability_level",
    )
    for field in exact_fields:
        if field in expected and expected[field] is not None and not _text_eq(actual.get(field), expected.get(field)):
            return False

    if expected.get("normalized_value") is not None and not _text_eq(
        actual.get("normalized_value"), expected.get("normalized_value")
    ):
        return False

    min_confidence = expected.get("min_confidence")
    if min_confidence is not None and (actual.get("confidence_score") is None or float(actual["confidence_score"]) < float(min_confidence)):
        return False

    max_freshness_days = expected.get("max_freshness_days")
    if max_freshness_days is not None:
        freshness = actual.get("freshness_days")
        if freshness is None or int(freshness) > int(max_freshness_days):
            return False

    return True


def _metric_category(expected: dict[str, Any]) -> str | None:
    explicit = expected.get("metric")
    if explicit:
        return str(explicit)
    claim_type = str(expected.get("claim_type") or "").lower()
    predicate = str(expected.get("predicate") or "").lower()
    if "building" in claim_type or predicate in {"manages_buildings", "owns_building", "linked_to_building"}:
        return "building_link_accuracy"
    if "contact" in claim_type or "contact" in predicate:
        return "contact_accuracy"
    return None


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def evaluate_golden_cases(
    cases: list[dict[str, Any]],
    actual_claims_by_case: dict[str, list[dict[str, Any]]],
    *,
    seeded: bool,
) -> dict[str, Any]:
    """Evaluate golden cases against actual truth claims.

    Expected claims schema is intentionally small:
    {
      "required_claims": [{"predicate": "...", "object_id": "...", "min_confidence": 0.8}],
      "forbidden_claims": [{"predicate": "...", "object_id": "...", "metric": "false_merge"}]
    }
    """

    total_cases = len(cases)
    configured_cases = 0
    evaluable_cases = 0
    passed_cases = 0
    case_results: list[dict[str, Any]] = []
    observed_features = sorted({
        str(feature)
        for case in cases
        for feature in (case.get("tricky_features") or [])
        if str(feature).strip()
    })
    missing_required_features = [
        feature for feature in REQUIRED_TRICKY_FEATURES if feature not in observed_features
    ]

    true_positive = 0
    false_negative = 0
    false_positive = 0

    category_totals = {
        "building_link_accuracy": [0, 0],
        "contact_accuracy": [0, 0],
        "freshness_accuracy": [0, 0],
        "false_merge_rate": [0, 0],
        "false_split_rate": [0, 0],
    }

    for case in cases:
        expected = _normalize_expected_claims(case.get("expected_claims"))
        required = expected["required_claims"]
        forbidden = expected["forbidden_claims"]
        actual_claims = actual_claims_by_case.get(str(case.get("case_id")), [])

        if required or forbidden:
            configured_cases += 1

        if not case.get("subject_id"):
            status = "not_seeded"
        elif not (required or forbidden):
            status = "not_configured"
        else:
            evaluable_cases += 1
            status = "pass"

        matched_required: list[dict[str, Any]] = []
        missing_required: list[dict[str, Any]] = []
        violated_forbidden: list[dict[str, Any]] = []

        for expected_claim in required:
            match = next((claim for claim in actual_claims if _claim_matches(claim, expected_claim)), None)
            category = _metric_category(expected_claim)
            if match:
                true_positive += 1
                matched_required.append({"expected": expected_claim, "actual_claim_id": match.get("claim_id")})
                if category in ("building_link_accuracy", "contact_accuracy"):
                    category_totals[category][0] += 1
                    category_totals[category][1] += 1
                if expected_claim.get("max_freshness_days") is not None:
                    category_totals["freshness_accuracy"][0] += 1
                    category_totals["freshness_accuracy"][1] += 1
            else:
                false_negative += 1
                missing_required.append(expected_claim)
                status = "fail" if status == "pass" else status
                if category in ("building_link_accuracy", "contact_accuracy"):
                    category_totals[category][1] += 1
                if expected_claim.get("max_freshness_days") is not None:
                    category_totals["freshness_accuracy"][1] += 1

                metric = str(expected_claim.get("metric") or "").lower()
                if metric == "false_split":
                    category_totals["false_split_rate"][0] += 1
                    category_totals["false_split_rate"][1] += 1

        for expected_claim in forbidden:
            violation = next((claim for claim in actual_claims if _claim_matches(claim, expected_claim)), None)
            metric = str(expected_claim.get("metric") or "").lower()
            if metric == "false_merge" or expected_claim.get("predicate") in {"maps_to_canonical_entity", "same_entity_as"}:
                category_totals["false_merge_rate"][1] += 1
            if violation:
                false_positive += 1
                violated_forbidden.append({"expected": expected_claim, "actual_claim_id": violation.get("claim_id")})
                status = "fail" if status == "pass" else status
                if metric == "false_merge" or expected_claim.get("predicate") in {"maps_to_canonical_entity", "same_entity_as"}:
                    category_totals["false_merge_rate"][0] += 1

        if status == "pass":
            passed_cases += 1

        case_results.append({
            "case_id": case.get("case_id"),
            "name": case.get("name"),
            "case_type": case.get("case_type"),
            "subject_type": case.get("subject_type"),
            "subject_id": case.get("subject_id"),
            "expected_outcome": case.get("expected_outcome"),
            "status": status,
            "required_claim_count": len(required),
            "matched_required_count": len(matched_required),
            "missing_required": missing_required,
            "forbidden_claim_count": len(forbidden),
            "violated_forbidden": violated_forbidden,
            "actual_claim_count": len(actual_claims),
            "tricky_features": case.get("tricky_features") or [],
        })

    precision_denominator = true_positive + false_positive
    metrics = {
        "precision": _ratio(true_positive, precision_denominator),
        "recall": _ratio(true_positive, true_positive + false_negative),
        "false_merge_rate": _ratio(category_totals["false_merge_rate"][0], category_totals["false_merge_rate"][1]),
        "false_split_rate": _ratio(category_totals["false_split_rate"][0], category_totals["false_split_rate"][1]),
        "building_link_accuracy": _ratio(category_totals["building_link_accuracy"][0], category_totals["building_link_accuracy"][1]),
        "contact_accuracy": _ratio(category_totals["contact_accuracy"][0], category_totals["contact_accuracy"][1]),
        "freshness_accuracy": _ratio(category_totals["freshness_accuracy"][0], category_totals["freshness_accuracy"][1]),
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seeded": seeded,
        "total_cases": total_cases,
        "configured_cases": configured_cases,
        "evaluable_cases": evaluable_cases,
        "passed_cases": passed_cases,
        "failed_cases": sum(1 for case in case_results if case["status"] == "fail"),
        "benchmark_coverage": _ratio(configured_cases, total_cases),
        "feature_coverage": {
            "required_features": list(REQUIRED_TRICKY_FEATURES),
            "observed_features": observed_features,
            "missing_required_features": missing_required_features,
            "coverage": _ratio(len(REQUIRED_TRICKY_FEATURES) - len(missing_required_features), len(REQUIRED_TRICKY_FEATURES)),
        },
        "metrics": metrics,
        "metric_counts": {
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "category_denominators": {key: value[1] for key, value in category_totals.items()},
        },
        "cases": case_results,
        "notes": [
            "Null metric values mean no configured golden claims currently exercise that measurement.",
            "Cases without a subject_id or expected_claims are descriptive only and do not affect benchmark metrics.",
        ],
    }


async def load_golden_benchmark(session: AsyncSession) -> dict[str, Any]:
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

    seeded = bool(cases)
    if not cases:
        cases = GOLDEN_CASE_SEEDS

    subject_cases = [case for case in cases if case.get("subject_id")]
    actual_claims_by_case: dict[str, list[dict[str, Any]]] = {str(case.get("case_id")): [] for case in cases}
    if subject_cases:
        claim_rows = await session.execute(
            text("""
                SELECT
                    c.claim_id,
                    c.subject_type,
                    c.subject_id,
                    c.predicate,
                    c.object_type,
                    c.object_id,
                    c.normalized_value,
                    c.claim_type,
                    c.belief_status,
                    c.confidence_score,
                    c.freshness_days,
                    c.actionability_level,
                    c.observed_at,
                    g.case_id
                FROM golden_verification_cases g
                JOIN truth_claims c ON c.subject_type = g.subject_type AND c.subject_id = g.subject_id
                WHERE g.active = true
            """)
        )
        for row in claim_rows:
            payload = dict(row._mapping)
            case_id = str(payload.pop("case_id"))
            payload["observed_at"] = serialize_dt(payload.get("observed_at"))
            actual_claims_by_case.setdefault(case_id, []).append(payload)

    return evaluate_golden_cases(cases, actual_claims_by_case, seeded=seeded)
