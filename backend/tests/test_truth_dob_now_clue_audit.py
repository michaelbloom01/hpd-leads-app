from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from scripts import truth_dob_now_clue_audit as audit


def test_dob_now_query_packet_is_read_only_and_exact_targeted() -> None:
    target = {
        "target_id": "md2-57-bond-dob-now-clue",
        "address": "57 BOND STREET",
        "bbl": "1005297507",
        "expected_party": "Md2 Property Group",
        "expected_manager": "MD Squared Property Group",
        "manager_lead_id": "56a71624c6c0",
        "aliases": ("57 BOND", "57B BOND"),
    }

    packet = audit.build_query_packet([target])

    assert packet["dataset_id"] == "w9ak-ipjd"
    assert packet["catalog_url"].endswith("/d/w9ak-ipjd")
    assert packet["download_url"].endswith("w9ak-ipjd/rows.csv?accessType=DOWNLOAD")
    query_url = packet["target_query_urls"][0]["api_url"]
    assert query_url is not None
    params = parse_qs(urlparse(query_url).query)
    assert "1005297507" in params["$where"][0]
    assert "borough='MANHATTAN'" in params["$where"][0]
    target_party_url = packet["target_query_urls"][0]["target_party_api_url"]
    assert target_party_url is not None
    target_party_params = parse_qs(urlparse(target_party_url).query)
    assert "1005297507" in target_party_params["$where"][0]
    assert "MD2" in target_party_params["$where"][0]
    assert "role-ambiguous" in packet["source_boundary"]
    assert "truth_dob_now_clue_audit.py" in packet["post_fetch_local_extract_command"]


def test_dob_now_extract_match_emits_clue_only_not_evidence() -> None:
    rows = [
        {
            "borough_block_lot": "1005297507",
            "house_no": "57B",
            "street_name": "Bond Street",
            "job_filing_number": "M01012345-I1",
            "filing_date": "2025-07-25T00:00:00",
            "work_type": "Alteration",
            "job_description": "Interior alteration",
            "applicant_business_name": "Md2 Property Group",
            "owner_business_name": "57B Bond Street Condominium",
        }
    ]

    report = audit.audit_dob_now_rows(rows, targets=[dict(audit.DEFAULT_TARGETS[0])])

    assert report["run_type"] == "truth_dob_now_clue_audit"
    assert report["dry_run"] is True
    assert report["mutations_planned"] == 0
    assert report["source_access_mode"] == "local_extract"
    assert report["source_acquisition_clue_count"] == 1
    assert report["source_evidence_intake_candidates"] == []
    assert report["recording_ready_count"] == 0
    assert report["allowed_execute"] is False
    clue = report["source_acquisition_clues"][0]
    assert clue["clue_status"] == "source_clue_only"
    assert clue["source_family"] == "dob_now_build_job_filing"
    assert clue["source_name"] == "dob_now_build_job_application_filings"
    assert clue["source_dataset_id"] == "w9ak-ipjd"
    assert clue["bbl"] == "1005297507"
    assert "bbl" in clue["matched_reasons"]
    assert "expected_party" in clue["matched_reasons"]
    assert clue["filing_summary"]["job_number"] == "M01012345-I1"
    assert clue["filing_summary"]["applicant_business_name"] == "Md2 Property Group"
    assert clue["can_become_manual_evidence_template"] is False
    assert clue["source_evidence_intake_candidate_ready"] is False
    assert "role-ambiguous for property management" in clue["safe_action"]


def test_dob_now_extract_can_match_by_address_and_party_without_bbl() -> None:
    rows = [
        {
            "house_number": "57B",
            "street_name": "Bond St",
            "job_number": "M01999999-I1",
            "applicant_business_name": "MD2 Property Group LLC",
        }
    ]

    report = audit.audit_dob_now_rows(rows, targets=[dict(audit.DEFAULT_TARGETS[0])])

    assert report["source_acquisition_clue_count"] == 1
    clue = report["source_acquisition_clues"][0]
    assert "address_alias" in clue["matched_reasons"]
    assert "expected_party" in clue["matched_reasons"]


def test_dob_now_clue_preview_stays_primary_source_required() -> None:
    rows = [
        {
            "borough_block_lot": "1005297507",
            "job_number": "M01012345-I1",
            "applicant_business_name": "Md2 Property Group",
        }
    ]
    report = audit.audit_dob_now_rows(rows, targets=[dict(audit.DEFAULT_TARGETS[0])])

    preview = audit.build_source_acquisition_clue_only_preview(
        report["source_acquisition_clues"],
        source_mode="dob_now_clue_audit",
    )

    assert preview["run_type"] == "truth_source_evidence_intake_clue_only_preview"
    assert preview["allowed_execute"] is False
    assert preview["candidate_count"] == 0
    assert preview["source_acquisition_clue_count"] == 1
    assert preview["recording_ready_count"] == 0
    assert preview["recording_ready_status"] == "source_clue_only_primary_source_required"


def test_dob_now_live_query_reports_exact_target_without_evidence_when_party_does_not_match() -> None:
    target = dict(audit.DEFAULT_TARGETS[0])

    def fake_fetch(where: str, *, limit: int = 50):
        if "bbl='1005297507'" in where and "MD2" not in where:
            return [{
                "bbl": "1005297507",
                "house_no": "57",
                "street_name": "BOND STREET",
                "job_filing_number": "M00934594-I1",
                "applicant_business_name": "GUTH DECONZO CONSULTING ENGINEERS",
            }]
        if "MD2" in where and "bbl='1005297507'" not in where:
            return [{
                "bbl": "1012930026",
                "house_no": "595",
                "street_name": "MADISON AVENUE",
                "job_filing_number": "M01232585-P1",
                "owner_s_business_name": "MD2",
            }]
        return []

    report = audit.audit_live_dob_now_targets([target], fetch_rows=fake_fetch)

    assert report["source_access_mode"] == "live_official_query"
    assert report["dry_run"] is True
    assert report["mutations_planned"] == 0
    assert report["source_acquisition_clue_count"] == 0
    assert report["source_evidence_intake_candidates"] == []
    assert report["recording_ready_count"] == 0
    assert report["allowed_execute"] is False
    result = report["target_query_results"][0]
    assert result["property_row_count"] == 1
    assert result["party_row_count"] == 1
    assert result["target_party_match_count"] == 0
    assert result["sample_property_rows"][0]["job_number"] == "M00934594-I1"
    assert result["sample_party_only_rows"][0]["bbl"] == "1012930026"
    assert "role-ambiguous" in result["safe_action"]


def test_dob_now_live_query_target_party_match_stays_clue_only() -> None:
    target = dict(audit.DEFAULT_TARGETS[0])

    def fake_fetch(where: str, *, limit: int = 50):
        row = {
            "bbl": "1005297507",
            "house_no": "57B",
            "street_name": "BOND STREET",
            "job_filing_number": "M01012345-I1",
            "applicant_business_name": "Md2 Property Group",
        }
        if "bbl='1005297507'" in where and "MD2" in where:
            return [row]
        if "bbl='1005297507'" in where:
            return [row]
        if "MD2" in where:
            return [row]
        return []

    report = audit.audit_live_dob_now_targets([target], fetch_rows=fake_fetch)

    assert report["source_access_mode"] == "live_official_query"
    assert report["source_acquisition_clue_count"] == 1
    assert report["source_evidence_intake_candidates"] == []
    assert report["recording_ready_count"] == 0
    clue = report["source_acquisition_clues"][0]
    assert clue["clue_status"] == "source_clue_only"
    assert clue["source_family"] == "dob_now_build_job_filing"
    assert clue["source_evidence_intake_candidate_ready"] is False
    assert report["target_query_results"][0]["target_party_match_count"] == 1
