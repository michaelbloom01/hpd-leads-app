"""Read-only production probe for Data Truth & Confidence activation status.

This script does not authenticate, enqueue jobs, run migrations, or mutate data.
It checks public production health/data-health endpoints and reports whether the
truth-confidence API surface is deployed and reachable.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import requests


DEFAULT_BASE_URL = "https://hpd-leads-app-production.up.railway.app"
ENDPOINTS = {
    "health": "/api/health",
    "data_health": "/api/v1/quality/data-health",
    "truth_schema_status": "/api/v1/truth/schema-status",
    "truth_activation_packet": "/api/v1/truth/activation-packet",
}
PRODUCTION_DATA_HEALTH_THRESHOLDS = {
    "max_zero_active_links": 0,
    "max_buildings_with_multiple_current_pm_links": 0,
    "max_stale_lead_ratio": 0.5,
    "min_phone_coverage": 0.0001,
    "min_email_coverage": 0.0001,
    "min_website_coverage": 0.0001,
    "allow_running_refresh_without_finished_at": False,
}


def summarize_health(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    return {
        "status": payload.get("status"),
        "leads_in_db": payload.get("leads_in_db"),
        "buildings_in_db": payload.get("buildings_in_db"),
    }


def summarize_data_health(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    integrity = payload.get("integrity") or {}
    lead_staleness = payload.get("lead_staleness") or {}
    enrichment = payload.get("enrichment_coverage") or {}
    last_refresh = payload.get("last_refresh") or {}
    return {
        "total_leads": payload.get("total_leads"),
        "total_buildings_registered": payload.get("total_buildings_registered"),
        "coverage_percent": payload.get("coverage_percent"),
        "entity_coverage_ratio": payload.get("entity_coverage_ratio"),
        "zero_active_links": integrity.get("leads_with_zero_active_links"),
        "buildings_with_multiple_current_pm_links": integrity.get("buildings_with_multiple_current_pm_links"),
        "lead_staleness": {
            "fresh": lead_staleness.get("fresh"),
            "recent": lead_staleness.get("recent"),
            "stale": lead_staleness.get("stale"),
        },
        "enrichment_coverage": {
            "phone": enrichment.get("phone"),
            "email": enrichment.get("email"),
            "website": enrichment.get("website"),
        },
        "last_refresh": {
            "status": last_refresh.get("status"),
            "started_at": last_refresh.get("started_at"),
            "finished_at": last_refresh.get("finished_at"),
        },
    }


def summarize_truth_endpoint(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    summary = {
        "dry_run": payload.get("dry_run"),
        "mutations_planned": payload.get("mutations_planned"),
        "ready": payload.get("ready"),
        "current_revision": payload.get("current_revision"),
        "expected_revision": payload.get("expected_revision"),
        "verdict": payload.get("verdict"),
        "business_use_allowed": payload.get("business_use_allowed"),
        "approval_required": payload.get("approval_required"),
    }
    claim_readiness = payload.get("claim_readiness")
    if isinstance(claim_readiness, dict):
        summary["claim_readiness"] = {
            key: claim_readiness.get(key)
            for key in (
                "claim_count",
                "verified_claim_count",
                "critical_or_high_gap_count",
                "has_materialized_claims",
                "has_verified_claims",
                "has_no_critical_or_high_gaps",
            )
            if key in claim_readiness
        }
    return summary


def evaluate_activation_packet_readiness(activation_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return activation-packet gaps that should block production business use."""
    if not isinstance(activation_payload, dict):
        return [{
            "severity": "critical",
            "area": "activation_packet",
            "message": "Production activation packet did not return a usable payload.",
            "evidence": {},
        }]

    gaps: list[dict[str, Any]] = []
    if activation_payload.get("business_use_allowed") is not True:
        gaps.append({
            "severity": "critical",
            "area": "activation_packet",
            "message": "Production activation packet does not allow business use.",
            "evidence": {
                "verdict": activation_payload.get("verdict"),
                "business_use_allowed": activation_payload.get("business_use_allowed"),
            },
        })

    claim_readiness = activation_payload.get("claim_readiness")
    if not isinstance(claim_readiness, dict):
        gaps.append({
            "severity": "critical",
            "area": "activation_claim_readiness",
            "message": "Production activation packet is missing claim-readiness evidence.",
            "evidence": {},
        })
        return gaps

    has_materialized_claims = claim_readiness.get("has_materialized_claims") is True
    has_verified_claims = claim_readiness.get("has_verified_claims") is True
    has_no_critical_or_high_gaps = claim_readiness.get("has_no_critical_or_high_gaps") is True
    if not (has_materialized_claims and has_verified_claims and has_no_critical_or_high_gaps):
        gaps.append({
            "severity": "critical",
            "area": "activation_claim_readiness",
            "message": "Production activation packet does not prove materialized, verified, gap-free claim readiness.",
            "evidence": {
                "claim_count": claim_readiness.get("claim_count"),
                "verified_claim_count": claim_readiness.get("verified_claim_count"),
                "critical_or_high_gap_count": claim_readiness.get("critical_or_high_gap_count"),
                "has_materialized_claims": claim_readiness.get("has_materialized_claims"),
                "has_verified_claims": claim_readiness.get("has_verified_claims"),
                "has_no_critical_or_high_gaps": claim_readiness.get("has_no_critical_or_high_gaps"),
            },
        })
    return gaps


def evaluate_production_data_health(
    data_health: dict[str, Any],
    thresholds: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return production data-health gaps that should block business use."""
    thresholds = thresholds or PRODUCTION_DATA_HEALTH_THRESHOLDS
    gaps: list[dict[str, Any]] = []
    if not data_health:
        return [{
            "severity": "critical",
            "area": "data_health",
            "message": "Production data-health endpoint did not return a usable payload.",
            "evidence": {},
        }]

    total_leads = int(data_health.get("total_leads") or 0)
    zero_active_links = int(data_health.get("zero_active_links") or 0)
    duplicate_current_pm_links = int(data_health.get("buildings_with_multiple_current_pm_links") or 0)
    lead_staleness = data_health.get("lead_staleness") or {}
    stale_leads = int(lead_staleness.get("stale") or 0)
    stale_ratio = (stale_leads / total_leads) if total_leads else 1.0
    enrichment = data_health.get("enrichment_coverage") or {}
    contact_channels = ("phone", "email", "website")
    zero_contact_channels = []
    for channel in contact_channels:
        minimum = float(thresholds.get(f"min_{channel}_coverage") or 0)
        if float(enrichment.get(channel) or 0) < minimum:
            zero_contact_channels.append(channel)
    last_refresh = data_health.get("last_refresh") or {}

    if total_leads <= 0:
        gaps.append({
            "severity": "critical",
            "area": "data_health",
            "message": "Production data-health reports no leads.",
            "evidence": {"total_leads": total_leads},
        })
    if (
        not thresholds.get("allow_running_refresh_without_finished_at")
        and str(last_refresh.get("status") or "").lower() == "running"
        and not last_refresh.get("finished_at")
    ):
        gaps.append({
            "severity": "high",
            "area": "source_refresh",
            "message": "Production data refresh is still marked running and has no finished timestamp.",
            "evidence": last_refresh,
        })
    if zero_active_links > int(thresholds.get("max_zero_active_links") or 0):
        gaps.append({
            "severity": "high",
            "area": "building_links",
            "message": "Production still has leads with zero active building links.",
            "evidence": {
                "zero_active_links": zero_active_links,
                "total_leads": total_leads,
                "maximum": thresholds.get("max_zero_active_links"),
            },
        })
    if duplicate_current_pm_links > int(thresholds.get("max_buildings_with_multiple_current_pm_links") or 0):
        gaps.append({
            "severity": "high",
            "area": "building_links",
            "message": "Production still has buildings with multiple current PM links.",
            "evidence": {
                "buildings_with_multiple_current_pm_links": duplicate_current_pm_links,
                "maximum": thresholds.get("max_buildings_with_multiple_current_pm_links"),
            },
        })
    if stale_ratio > float(thresholds.get("max_stale_lead_ratio") or 0):
        gaps.append({
            "severity": "high",
            "area": "freshness",
            "message": "More than half of production leads are stale.",
            "evidence": {
                "stale_leads": stale_leads,
                "total_leads": total_leads,
                "stale_ratio": round(stale_ratio, 4),
                "maximum": thresholds.get("max_stale_lead_ratio"),
            },
        })
    if zero_contact_channels:
        gaps.append({
            "severity": "medium",
            "area": "contact_coverage",
            "message": "Production contact enrichment coverage is zero for one or more channels.",
            "evidence": {
                "zero_contact_channels": zero_contact_channels,
                "enrichment_coverage": enrichment,
                "minimums": {
                    channel: thresholds.get(f"min_{channel}_coverage")
                    for channel in contact_channels
                },
            },
        })
    return gaps


def classify_truth_surface(endpoint_reports: dict[str, dict[str, Any]]) -> str:
    schema_status = endpoint_reports.get("truth_schema_status", {}).get("status_code")
    activation_status = endpoint_reports.get("truth_activation_packet", {}).get("status_code")
    statuses = {schema_status, activation_status}
    if statuses == {200}:
        return "deployed"
    if statuses == {404}:
        return "not_deployed"
    if all(status is None for status in statuses):
        return "unreachable"
    return "partial_or_auth_gated"


def _request_json(url: str, *, timeout: float) -> dict[str, Any]:
    try:
        response = requests.get(url, timeout=timeout)
        payload = None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        return {
            "status_code": response.status_code,
            "ok": response.ok,
            "payload": payload,
            "error": None,
        }
    except requests.RequestException as exc:
        return {
            "status_code": None,
            "ok": False,
            "payload": None,
            "error": str(exc),
        }


def build_probe_report(
    endpoint_reports: dict[str, dict[str, Any]],
    *,
    base_url: str,
) -> dict[str, Any]:
    truth_surface_status = classify_truth_surface(endpoint_reports)
    activation_report = endpoint_reports.get("truth_activation_packet", {})
    activation_payload = activation_report.get("payload")
    data_health = summarize_data_health(endpoint_reports.get("data_health", {}).get("payload"))
    data_health_gaps = evaluate_production_data_health(data_health)
    if activation_report.get("status_code") == 200:
        activation_gaps = evaluate_activation_packet_readiness(activation_payload)
    else:
        activation_gaps = [{
            "severity": "critical",
            "area": "activation_packet",
            "message": "Production activation packet did not return a usable payload.",
            "evidence": {
                "status_code": activation_report.get("status_code"),
                "error": activation_report.get("error"),
            },
        }]
    production_data_health_ready = not any(
        gap["severity"] in {"critical", "high"}
        for gap in data_health_gaps
    )
    production_business_use_allowed = bool(
        truth_surface_status == "deployed"
        and isinstance(activation_payload, dict)
        and activation_payload.get("business_use_allowed") is True
        and not activation_gaps
        and production_data_health_ready
    )
    trust_gaps = []
    if truth_surface_status != "deployed":
        trust_gaps.append({
            "severity": "critical",
            "area": "truth_surface",
            "message": "Production truth-confidence routes are not deployed, not reachable, or not usable.",
            "evidence": {"truth_surface_status": truth_surface_status},
        })
    trust_gaps.extend(activation_gaps)
    trust_gaps.extend(data_health_gaps)
    return {
        "dry_run": True,
        "mutations_planned": 0,
        "base_url": base_url,
        "truth_surface_status": truth_surface_status,
        "data_health_thresholds": PRODUCTION_DATA_HEALTH_THRESHOLDS,
        "production_data_health_ready": production_data_health_ready,
        "production_business_use_allowed": production_business_use_allowed,
        "endpoints": {
            name: {
                "path": ENDPOINTS[name],
                "status_code": report.get("status_code"),
                "ok": report.get("ok"),
                "error": report.get("error"),
            }
            for name, report in endpoint_reports.items()
        },
        "health": summarize_health(endpoint_reports.get("health", {}).get("payload")),
        "data_health": data_health,
        "data_health_gaps": data_health_gaps,
        "activation_gaps": activation_gaps,
        "truth_schema_status": summarize_truth_endpoint(endpoint_reports.get("truth_schema_status", {}).get("payload")),
        "truth_activation_packet": summarize_truth_endpoint(endpoint_reports.get("truth_activation_packet", {}).get("payload")),
        "trust_gaps": trust_gaps,
        "trust_gap": (
            "; ".join(gap["message"] for gap in trust_gaps[:5])
            if trust_gaps
            else "Production truth-confidence routes and data-health gates are business-use ready."
        ),
    }


def run_probe(*, base_url: str, timeout: float) -> dict[str, Any]:
    normalized_base = base_url.rstrip("/")
    endpoint_reports = {
        name: _request_json(f"{normalized_base}{path}", timeout=timeout)
        for name, path in ENDPOINTS.items()
    }
    return build_probe_report(endpoint_reports, base_url=normalized_base)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--indent", type=int, default=2)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_probe(base_url=args.base_url, timeout=args.timeout)
    print(json.dumps(report, indent=args.indent, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
