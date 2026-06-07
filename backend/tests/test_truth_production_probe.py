from scripts.truth_production_probe import (
    build_probe_report,
    classify_truth_surface,
    evaluate_activation_packet_readiness,
    evaluate_production_data_health,
)


READY_CLAIM_READINESS = {
    "claim_count": 25,
    "verified_claim_count": 12,
    "critical_or_high_gap_count": 0,
    "has_materialized_claims": True,
    "has_verified_claims": True,
    "has_no_critical_or_high_gaps": True,
}


def test_truth_production_probe_marks_missing_truth_routes_not_deployed():
    endpoint_reports = {
        "health": {
            "status_code": 200,
            "ok": True,
            "payload": {"status": "ok", "leads_in_db": 314723, "buildings_in_db": 181307},
            "error": None,
        },
        "data_health": {
            "status_code": 200,
            "ok": True,
            "payload": {
                "total_leads": 314723,
                "total_buildings_registered": 181307,
                "coverage_percent": 51.6,
                "entity_coverage_ratio": 96.9,
                "integrity": {
                    "leads_with_zero_active_links": 55804,
                    "buildings_with_multiple_current_pm_links": 106759,
                },
                "lead_staleness": {"fresh": 2, "recent": 2, "stale": 314719},
                "enrichment_coverage": {"phone": 0.0, "email": 0.0, "website": 0.0},
                "last_refresh": {
                    "status": "running",
                    "started_at": "2026-04-13T20:47:40.956449+00:00",
                    "finished_at": None,
                },
            },
            "error": None,
        },
        "truth_schema_status": {"status_code": 404, "ok": False, "payload": None, "error": None},
        "truth_activation_packet": {"status_code": 404, "ok": False, "payload": None, "error": None},
    }

    report = build_probe_report(endpoint_reports, base_url="https://example.test")

    assert report["dry_run"] is True
    assert report["mutations_planned"] == 0
    assert report["truth_surface_status"] == "not_deployed"
    assert report["data_health_thresholds"]["max_zero_active_links"] == 0
    assert report["data_health_thresholds"]["max_stale_lead_ratio"] == 0.5
    assert report["production_data_health_ready"] is False
    assert report["production_business_use_allowed"] is False
    assert report["activation_gaps"] == [{
        "severity": "critical",
        "area": "activation_packet",
        "message": "Production activation packet did not return a usable payload.",
        "evidence": {"status_code": 404, "error": None},
    }]
    assert report["health"] == {"status": "ok", "leads_in_db": 314723, "buildings_in_db": 181307}
    assert report["data_health"]["zero_active_links"] == 55804
    assert report["data_health"]["buildings_with_multiple_current_pm_links"] == 106759
    assert report["data_health"]["enrichment_coverage"] == {"phone": 0.0, "email": 0.0, "website": 0.0}
    assert {gap["area"] for gap in report["data_health_gaps"]} >= {
        "source_refresh",
        "building_links",
        "freshness",
        "contact_coverage",
    }
    assert "not deployed" in report["trust_gap"]


def test_truth_production_probe_requires_live_data_health_even_when_truth_routes_are_ready():
    endpoint_reports = {
        "health": {
            "status_code": 200,
            "ok": True,
            "payload": {"status": "ok", "leads_in_db": 100, "buildings_in_db": 20},
            "error": None,
        },
        "data_health": {
            "status_code": 200,
            "ok": True,
            "payload": {
                "total_leads": 100,
                "total_buildings_registered": 20,
                "coverage_percent": 50.0,
                "integrity": {
                    "leads_with_zero_active_links": 10,
                    "buildings_with_multiple_current_pm_links": 2,
                },
                "lead_staleness": {"fresh": 1, "recent": 1, "stale": 98},
                "enrichment_coverage": {"phone": 0.0, "email": 5.0, "website": 1.0},
                "last_refresh": {"status": "complete", "started_at": "2026-05-14T00:00:00Z", "finished_at": "2026-05-14T00:10:00Z"},
            },
            "error": None,
        },
        "truth_schema_status": {"status_code": 200, "ok": True, "payload": {"ready": True}, "error": None},
        "truth_activation_packet": {
            "status_code": 200,
            "ok": True,
            "payload": {
                "business_use_allowed": True,
                "verdict": "ready_for_business_use",
                "claim_readiness": READY_CLAIM_READINESS,
            },
            "error": None,
        },
    }

    report = build_probe_report(endpoint_reports, base_url="https://example.test")

    assert report["truth_surface_status"] == "deployed"
    assert report["truth_activation_packet"]["business_use_allowed"] is True
    assert report["production_data_health_ready"] is False
    assert report["production_business_use_allowed"] is False
    assert "Production still has leads with zero active building links" in report["trust_gap"]


def test_truth_production_probe_blocks_partial_or_auth_gated_truth_surface_even_with_ready_data_health():
    endpoint_reports = {
        "health": {
            "status_code": 200,
            "ok": True,
            "payload": {"status": "ok", "leads_in_db": 100, "buildings_in_db": 20},
            "error": None,
        },
        "data_health": {
            "status_code": 200,
            "ok": True,
            "payload": {
                "total_leads": 100,
                "total_buildings_registered": 20,
                "coverage_percent": 99.0,
                "integrity": {
                    "leads_with_zero_active_links": 0,
                    "buildings_with_multiple_current_pm_links": 0,
                },
                "lead_staleness": {"fresh": 80, "recent": 15, "stale": 5},
                "enrichment_coverage": {"phone": 1.0, "email": 1.0, "website": 1.0},
                "last_refresh": {"status": "complete", "started_at": "2026-05-14T00:00:00Z", "finished_at": "2026-05-14T00:10:00Z"},
            },
            "error": None,
        },
        "truth_schema_status": {"status_code": 200, "ok": True, "payload": {"ready": True}, "error": None},
        "truth_activation_packet": {"status_code": 401, "ok": False, "payload": None, "error": "unauthorized"},
    }

    report = build_probe_report(endpoint_reports, base_url="https://example.test")

    assert report["truth_surface_status"] == "partial_or_auth_gated"
    assert report["production_data_health_ready"] is True
    assert report["production_business_use_allowed"] is False
    assert report["activation_gaps"] == [{
        "severity": "critical",
        "area": "activation_packet",
        "message": "Production activation packet did not return a usable payload.",
        "evidence": {"status_code": 401, "error": "unauthorized"},
    }]
    assert report["trust_gaps"][0] == {
        "severity": "critical",
        "area": "truth_surface",
        "message": "Production truth-confidence routes are not deployed, not reachable, or not usable.",
        "evidence": {"truth_surface_status": "partial_or_auth_gated"},
    }


def test_truth_production_probe_allows_business_use_only_when_truth_and_data_health_are_ready():
    endpoint_reports = {
        "health": {
            "status_code": 200,
            "ok": True,
            "payload": {"status": "ok", "leads_in_db": 100, "buildings_in_db": 20},
            "error": None,
        },
        "data_health": {
            "status_code": 200,
            "ok": True,
            "payload": {
                "total_leads": 100,
                "total_buildings_registered": 20,
                "coverage_percent": 99.0,
                "integrity": {
                    "leads_with_zero_active_links": 0,
                    "buildings_with_multiple_current_pm_links": 0,
                },
                "lead_staleness": {"fresh": 80, "recent": 15, "stale": 5},
                "enrichment_coverage": {"phone": 1.0, "email": 1.0, "website": 1.0},
                "last_refresh": {"status": "complete", "started_at": "2026-05-14T00:00:00Z", "finished_at": "2026-05-14T00:10:00Z"},
            },
            "error": None,
        },
        "truth_schema_status": {"status_code": 200, "ok": True, "payload": {"ready": True}, "error": None},
        "truth_activation_packet": {
            "status_code": 200,
            "ok": True,
            "payload": {
                "business_use_allowed": True,
                "verdict": "ready_for_business_use",
                "claim_readiness": READY_CLAIM_READINESS,
            },
            "error": None,
        },
    }

    report = build_probe_report(endpoint_reports, base_url="https://example.test")

    assert report["production_data_health_ready"] is True
    assert report["production_business_use_allowed"] is True
    assert report["activation_gaps"] == []
    assert report["trust_gaps"] == []


def test_truth_production_probe_requires_activation_claim_readiness_even_when_packet_allows_business_use():
    endpoint_reports = {
        "health": {
            "status_code": 200,
            "ok": True,
            "payload": {"status": "ok", "leads_in_db": 100, "buildings_in_db": 20},
            "error": None,
        },
        "data_health": {
            "status_code": 200,
            "ok": True,
            "payload": {
                "total_leads": 100,
                "total_buildings_registered": 20,
                "coverage_percent": 99.0,
                "integrity": {
                    "leads_with_zero_active_links": 0,
                    "buildings_with_multiple_current_pm_links": 0,
                },
                "lead_staleness": {"fresh": 80, "recent": 15, "stale": 5},
                "enrichment_coverage": {"phone": 1.0, "email": 1.0, "website": 1.0},
                "last_refresh": {"status": "complete", "started_at": "2026-05-14T00:00:00Z", "finished_at": "2026-05-14T00:10:00Z"},
            },
            "error": None,
        },
        "truth_schema_status": {"status_code": 200, "ok": True, "payload": {"ready": True}, "error": None},
        "truth_activation_packet": {
            "status_code": 200,
            "ok": True,
            "payload": {"business_use_allowed": True, "verdict": "ready_for_business_use"},
            "error": None,
        },
    }

    report = build_probe_report(endpoint_reports, base_url="https://example.test")

    assert report["production_data_health_ready"] is True
    assert report["production_business_use_allowed"] is False
    assert report["activation_gaps"] == [{
        "severity": "critical",
        "area": "activation_claim_readiness",
        "message": "Production activation packet is missing claim-readiness evidence.",
        "evidence": {},
    }]


def test_evaluate_activation_packet_readiness_blocks_unverified_claim_evidence():
    gaps = evaluate_activation_packet_readiness({
        "business_use_allowed": True,
        "verdict": "ready_for_business_use",
        "claim_readiness": {
            "claim_count": 10,
            "verified_claim_count": 0,
            "critical_or_high_gap_count": 0,
            "has_materialized_claims": True,
            "has_verified_claims": False,
            "has_no_critical_or_high_gaps": True,
        },
    })

    assert gaps == [{
        "severity": "critical",
        "area": "activation_claim_readiness",
        "message": "Production activation packet does not prove materialized, verified, gap-free claim readiness.",
        "evidence": {
            "claim_count": 10,
            "verified_claim_count": 0,
            "critical_or_high_gap_count": 0,
            "has_materialized_claims": True,
            "has_verified_claims": False,
            "has_no_critical_or_high_gaps": True,
        },
    }]


def test_evaluate_production_data_health_blocks_missing_payload():
    gaps = evaluate_production_data_health({})

    assert gaps == [{
        "severity": "critical",
        "area": "data_health",
        "message": "Production data-health endpoint did not return a usable payload.",
        "evidence": {},
    }]


def test_evaluate_production_data_health_can_use_explicit_thresholds():
    gaps = evaluate_production_data_health(
        {
            "total_leads": 100,
            "zero_active_links": 1,
            "buildings_with_multiple_current_pm_links": 1,
            "lead_staleness": {"stale": 80},
            "enrichment_coverage": {"phone": 0.0, "email": 0.0, "website": 0.0},
            "last_refresh": {"status": "running", "finished_at": None},
        },
        thresholds={
            "max_zero_active_links": 10,
            "max_buildings_with_multiple_current_pm_links": 10,
            "max_stale_lead_ratio": 0.9,
            "min_phone_coverage": 0,
            "min_email_coverage": 0,
            "min_website_coverage": 0,
            "allow_running_refresh_without_finished_at": True,
        },
    )

    assert gaps == []


def test_truth_production_probe_classifies_deployed_partial_and_unreachable():
    assert classify_truth_surface({
        "truth_schema_status": {"status_code": 200},
        "truth_activation_packet": {"status_code": 200},
    }) == "deployed"
    assert classify_truth_surface({
        "truth_schema_status": {"status_code": 404},
        "truth_activation_packet": {"status_code": 404},
    }) == "not_deployed"
    assert classify_truth_surface({
        "truth_schema_status": {"status_code": 200},
        "truth_activation_packet": {"status_code": 401},
    }) == "partial_or_auth_gated"
    assert classify_truth_surface({
        "truth_schema_status": {"status_code": None},
        "truth_activation_packet": {"status_code": None},
    }) == "unreachable"
