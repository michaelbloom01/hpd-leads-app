"""Read-only adjudication of materialized truth claims."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.confidence import (
    CONFIDENCE_POLICY_VERSION,
    VERIFIED_CONFIDENCE_THRESHOLD,
    ConfidenceInput,
    compute_confidence,
    source_quality,
)
from src.services.manual_evidence import build_manual_evidence_claim_spec
from src.services.truth_materialization import (
    _building_management_role_claim_shape,
    _contact_display_name,
    _hpd_contact_role_claim,
    _load_before_snapshots_by_id,
    _load_existing_ids,
    _load_materializable_claims,
    _manifest_summary,
    _name_group,
    _upsert_materialization_manifest_entries,
    _filter_verification_name_keys,
    _verification_name_key,
    build_materialization_manifest_entries,
)


VERIFICATION_MIN_SUPPORTING_SOURCES = 2
VERIFICATION_MIN_SUPPORTING_EVIDENCE = 2
VERIFICATION_MAX_FRESHNESS_DAYS = 120
NON_MANAGER_PROOF_SOURCE_FAMILIES = {"hpd_registration_derived", "nyc_dof_billing_record"}
HPM_REVENUE_BY_PROPERTY_SOURCE_NAME = "hpm_revenue_by_property_summary"
HPM_REVENUE_BY_PROPERTY_SOURCE_FAMILY = "first_party_operator_document"
HPM_REVENUE_BY_PROPERTY_DOCUMENT_TITLE = "Revenue by Property - Summary"
HPM_REVENUE_BY_PROPERTY_OBSERVED_AT = "2026-03-20T14:24:44+00:00"


def _hpm_revenue_by_property_candidate(
    *,
    slug: str,
    row_number: int,
    sheet_property_label: str,
    local_address: str,
) -> dict[str, Any]:
    return {
        "candidate_id": f"hpm-revenue-by-property-summary-{slug}",
        "candidate_status": "operator_document_review_required",
        "source_name": HPM_REVENUE_BY_PROPERTY_SOURCE_NAME,
        "source_type": "first_party_operator_revenue_summary",
        "source_family": HPM_REVENUE_BY_PROPERTY_SOURCE_FAMILY,
        "source_record_id": f"google-drive-revenue-by-property-summary-row-{row_number}",
        "source_url": None,
        "observed_at": HPM_REVENUE_BY_PROPERTY_OBSERVED_AT,
        "external_address": sheet_property_label,
        "local_address": local_address,
        "external_owner": sheet_property_label,
        "manager_name": "Harlem Property Management",
        "manager_contact_name": None,
        "evidence_role": "first_party_management_revenue_property",
        "source_document_title": HPM_REVENUE_BY_PROPERTY_DOCUMENT_TITLE,
        "source_document_row_number": row_number,
        "evidence_summary": (
            f"User-identified HPM revenue-by-property spreadsheet row {row_number} "
            f"lists {sheet_property_label} with management-fee and related operating-revenue columns. "
            "Treat as first-party operator-document evidence for the exact property; do not store "
            "row-level revenue amounts in the truth-ledger preview."
        ),
    }

MANAGER_EXTERNAL_SOURCE_CANDIDATES: list[dict[str, Any]] = [
    {
        "candidate_id": "ny-dps-verizon-324-e-112-petition",
        "candidate_status": "clean_exact_match",
        "source_name": "verizon_order_entry_petition",
        "source_type": "ny_dps_hosted_verizon_petition",
        "source_family": "ny_dps_order_entry",
        "source_record_id": "ny-dps-21-00289-324-e-112-petition",
        "source_url": "https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7B01D4BD59-5170-49BB-828F-34534144E502%7D",
        "observed_at": "2021-02-04T00:00:00+00:00",
        "external_address": "324 EAST 112 STREET",
        "local_address": "324 EAST 112 STREET",
        "external_owner": "Senneca Terrace/324 East 112th Street Condominium",
        "manager_name": "Harlem Property Management, Inc.",
        "manager_contact_name": "James Simari",
        "evidence_role": "managing_agent",
        "evidence_summary": (
            "Verizon order-of-entry petition states the owner and managing agent for "
            "324 East 112th Street."
        ),
    },
    {
        "candidate_id": "ny-dps-psc-324-e-112-notice",
        "candidate_status": "supporting_notice_exact_match",
        "source_name": "ny_dps_order_entry",
        "source_type": "ny_public_service_commission_notice",
        "source_family": "ny_dps_order_entry",
        "source_record_id": "ny-dps-21-00289-324-e-112-psc-notice",
        "source_url": "https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7BB682B3B6-F25B-47F5-B4EE-76E600EF99A0%7D",
        "observed_at": "2021-03-04T00:00:00+00:00",
        "external_address": "324 EAST 112 STREET",
        "local_address": "324 EAST 112 STREET",
        "external_owner": "Senneca Terrace/324 East 112th Street Condominium",
        "manager_name": "Harlem Property Management, Inc.",
        "manager_contact_name": "James Simari",
        "evidence_role": "notice_care_of_manager",
        "evidence_summary": (
            "PSC notice for the same matter is addressed to James Simari and the building "
            "condominium care of Harlem Property Management."
        ),
    },
    {
        "candidate_id": "ny-dps-exhibit-36-w-138",
        "candidate_status": "clean_exact_match",
        "source_name": "ny_dps_order_entry",
        "source_type": "ny_dps_order_entry_exhibit",
        "source_family": "ny_dps_order_entry",
        "source_record_id": "ny-dps-20-00427-36-w-138-exhibit",
        "source_url": "https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7BCF5C1F53-F557-45AB-9DA2-BA47E386AD90%7D",
        "observed_at": "2019-10-17T00:00:00+00:00",
        "external_address": "36 WEST 138 STREET",
        "local_address": "36 WEST 138 STREET",
        "external_owner": "36 West 138 Street HDFC",
        "manager_name": "Harlem Property Management, Inc.",
        "manager_contact_name": "Jim Simari",
        "evidence_role": "mdu_managing_agent_company",
        "evidence_summary": (
            "DPS/Verizon exhibit lists Harlem Property Management as the MDU managing "
            "agent company for 36 W 138 Street."
        ),
    },
    {
        "candidate_id": "ny-dps-psc-36-w-138-notice",
        "candidate_status": "supporting_notice_exact_match",
        "source_name": "ny_dps_order_entry",
        "source_type": "ny_public_service_commission_notice",
        "source_family": "ny_dps_order_entry",
        "source_record_id": "ny-dps-20-00427-36-w-138-psc-notice",
        "source_url": "https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7B2912DCD1-5F78-4A21-A710-7A1E2841E9BA%7D",
        "observed_at": "2020-07-15T00:00:00+00:00",
        "external_address": "36 WEST 138 STREET",
        "local_address": "36 WEST 138 STREET",
        "external_owner": "36 West 138 Street HDFC",
        "manager_name": "Harlem Property Management, Inc.",
        "manager_contact_name": "Jim Simari",
        "evidence_role": "notice_care_of_manager",
        "evidence_summary": (
            "PSC notice for Verizon order-of-entry matter 20-00427 is addressed to "
            "Jim Simari and 36 West 138 Street HDFC care of Harlem Property Management."
        ),
    },
    {
        "candidate_id": "ny-dps-verizon-402-w-153-petition",
        "candidate_status": "new_relationship_candidate",
        "source_name": "ny_dps_order_entry",
        "source_type": "ny_dps_hosted_verizon_petition",
        "source_family": "ny_dps_order_entry",
        "source_record_id": "ny-dps-22-402-w-153-petition",
        "source_url": "https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7B516B105C-8647-4BFE-AB6E-62263DA10207%7D",
        "observed_at": "2022-01-28T00:00:00+00:00",
        "external_address": "402 WEST 153 STREET",
        "local_address": "402 WEST 153 STREET",
        "external_owner": "402 West 153rd Street Co-operative Corp.",
        "manager_name": "Harlem Property Management, Inc.",
        "manager_contact_name": "James Simari",
        "evidence_role": "managing_agent",
        "evidence_summary": (
            "Verizon order-of-entry petition states the owner and managing agent for "
            "402 West 153rd Street. It matches a local building but not a current "
            "Harlem building-management relationship, so it is a new relationship "
            "candidate rather than overlap evidence for the current ledger."
        ),
    },
    {
        "candidate_id": "renthistory-36-324-registration-index",
        "candidate_status": "derived_source_review_required",
        "source_name": "renthistory",
        "source_type": "hpd_registration_index",
        "source_family": "hpd_registration_derived",
        "source_record_id": "renthistory-harlem-property-management-inc-properties",
        "source_url": "https://renthistory.org/corporation/corp_report.php?corporationname=HARLEM+PROPERTY+MANAGEMENT+INC.",
        "observed_at": "2026-05-14T00:00:00+00:00",
        "external_address": "HARLEM PROPERTY MANAGEMENT INC. corporation report property list",
        "local_address": None,
        "external_owner": None,
        "manager_name": "Harlem Property Management, Inc.",
        "manager_contact_name": "James Simari",
        "evidence_role": "registration_index_association",
        "evidence_summary": (
            "RentHistory lists Harlem Property Management Inc. on registration records "
            "covering multiple exact local pilot addresses; treat as HPD-derived, "
            "not a fresh independent manager source."
        ),
        "candidate_local_addresses": [
            "141 WEST 123 STREET",
            "204 WEST 140 STREET",
            "2257 ADAM C POWELL BOULEVARD",
            "306 WEST 115 STREET",
            "324 EAST 112 STREET",
            "330 WEST 145 STREET",
            "342 WEST 56 STREET",
            "345 LENOX AVENUE",
            "36 WEST 138 STREET",
            "42 WEST 120 STREET",
            "506 EAST 119 STREET",
            "555 LENOX AVENUE",
            "61 LENOX AVENUE",
        ],
    },
    {
        "candidate_id": "renthistory-hpm-registration-index-st-nicholas",
        "candidate_status": "derived_source_review_required",
        "source_name": "renthistory",
        "source_type": "hpd_registration_index",
        "source_family": "hpd_registration_derived",
        "source_record_id": "renthistory-harlem-property-management-properties",
        "source_url": "https://renthistory.org/corporation/corp_report.php?corporationname=HARLEM+PROPERTY+MANAGEMENT",
        "observed_at": "2026-05-14T00:00:00+00:00",
        "external_address": "HARLEM PROPERTY MANAGEMENT corporation report property list",
        "local_address": None,
        "external_owner": None,
        "manager_name": "Harlem Property Management",
        "manager_contact_name": None,
        "evidence_role": "registration_index_association",
        "evidence_summary": (
            "RentHistory's Harlem Property Management report lists 11-15 St Nicholas Avenue "
            "among associated registration properties. Treat as HPD-derived review context, "
            "not strict manager-proof evidence."
        ),
        "candidate_local_addresses": [
            "11 ST NICHOLAS AVENUE",
        ],
    },
    {
        "candidate_id": "mystatemls-ellison-2257-hoa",
        "candidate_status": "clean_exact_match",
        "source_name": "mystatemls",
        "source_type": "real_estate_listing",
        "source_family": "real_estate_listing",
        "source_record_id": "mystatemls-11146569-2257-adam-clayton-powell",
        "source_url": "https://www.mystatemls.com/property/2257-adam-clayton-powell-jr-blvd-3b-new-york-ny-10030/11146569/",
        "observed_at": "2023-01-12T00:00:00+00:00",
        "external_address": "2257 ADAM CLAYTON POWELL JR BLVD",
        "local_address": "2257 ADAM C POWELL BOULEVARD",
        "external_owner": "Ellison Condominium",
        "manager_name": "Harlem Property Management",
        "manager_contact_name": None,
        "evidence_role": "hoa_management_contact",
        "evidence_summary": (
            "MyStateMLS listing 11146569 for unit 3B at 2257 Adam Clayton Powell Jr Blvd "
            "lists the HOA as Harlem Property Management and gives the HPM phone number. "
            "The local Harlem ledger has a current relationship for 2257 Adam C Powell "
            "Boulevard / BBL 1019177501; treat the Adam Clayton Powell / Adam C Powell "
            "street-name variant as an exact street alias, not a broad name dedupe."
        ),
    },
    {
        "candidate_id": "renthop-342-w-56-application-processing-fee",
        "candidate_status": "clean_exact_match",
        "source_name": "renthop",
        "source_type": "real_estate_listing",
        "source_family": "real_estate_listing",
        "source_record_id": "renthop-74523503-342-w-56-2d",
        "source_url": "https://www.renthop.com/listings/342-w-56th-st/2d/74523503",
        "observed_at": "2026-05-15T00:00:00+00:00",
        "external_address": "342 W 56TH ST",
        "local_address": "342 WEST 56 STREET",
        "external_owner": "342 W 56th Street Owners Corp.",
        "manager_name": "Harlem Property Management, Inc.",
        "manager_contact_name": None,
        "evidence_role": "application_processing_management_contact",
        "evidence_summary": (
            "RentHop listing 74523503 for 342 W 56th St unit 2D states that a $350 "
            "processing fee is payable to Harlem Property Management, Inc. with the "
            "rental application. Treat this as exact-property application-processing "
            "management evidence for the current local 342 West 56 Street HPM relationship."
        ),
    },
    {
        "candidate_id": "zillow-342-w-56-application-processing-fee",
        "candidate_status": "clean_exact_match",
        "source_name": "zillow",
        "source_type": "real_estate_listing",
        "source_family": "real_estate_listing",
        "source_record_id": "zillow-122346580-342-w-56-2d",
        "source_url": "https://www.zillow.com/homedetails/342-W-56th-St-APT-2D-New-York-NY-10019/122346580_zpid/",
        "observed_at": "2026-05-15T00:00:00+00:00",
        "external_address": "342 W 56TH ST",
        "local_address": "342 WEST 56 STREET",
        "external_owner": "342 W 56th Street Owners Corp.",
        "manager_name": "Harlem Property Management, Inc.",
        "manager_contact_name": None,
        "evidence_role": "application_processing_management_contact",
        "evidence_summary": (
            "Zillow's off-market rental page for 342 W 56th St Apt 2D repeats the "
            "application-processing evidence: a $350 fee is payable to Harlem Property "
            "Management, Inc. with the rental application. Treat this as same-family "
            "real-estate-listing corroboration for the exact local 342 West 56 Street "
            "relationship, not as an additional independent source family."
        ),
    },
    {
        "candidate_id": "openigloo-harlem-property-management-buildings",
        "candidate_status": "external_web_review_required",
        "source_name": "openigloo",
        "source_type": "management_company_profile",
        "source_family": "external_web_profile",
        "source_record_id": "openigloo-harlem-property-management-buildings",
        "source_url": "https://www.openigloo.com/contact/nyc/66ec2a5f-3005-415d-9553-d5ee48f8aeec/harlem-property-management-inc/buildings",
        "observed_at": "2026-05-14T00:00:00+00:00",
        "external_address": "OpenIgloo Harlem Property Management Inc. buildings page",
        "local_address": None,
        "external_owner": None,
        "manager_name": "Harlem Property Management, Inc.",
        "manager_contact_name": None,
        "evidence_role": "management_company_profile_property",
        "evidence_summary": (
            "OpenIgloo presents Harlem Property Management Inc. as a management company "
            "and lists multiple exact local pilot properties among associated buildings."
        ),
        "candidate_local_addresses": [
            "141 WEST 123 STREET",
            "2257 ADAM C POWELL BOULEVARD",
            "306 WEST 115 STREET",
            "324 EAST 112 STREET",
            "342 WEST 56 STREET",
            "345 LENOX AVENUE",
            "36 WEST 138 STREET",
            "42 WEST 120 STREET",
            "506 EAST 119 STREET",
            "555 LENOX AVENUE",
            "61 LENOX AVENUE",
        ],
    },
    {
        "candidate_id": "ny-dps-psc-330-w-145-notice",
        "candidate_status": "supporting_notice_exact_match",
        "source_name": "ny_dps_order_entry",
        "source_type": "ny_public_service_commission_notice",
        "source_family": "ny_dps_order_entry",
        "source_record_id": "ny-dps-22-00079-330-w-145-psc-notice",
        "source_url": "https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7BAEA2D08D-05DF-46D4-B9F0-CB3774078968%7D",
        "observed_at": "2022-02-01T00:00:00+00:00",
        "external_address": "330 WEST 145 STREET",
        "local_address": "330 WEST 145 STREET",
        "external_owner": "The Hamilton Owners Corp.",
        "manager_name": "Harlem Property Management, Inc.",
        "manager_contact_name": "James Simari",
        "evidence_role": "notice_care_of_manager",
        "evidence_summary": (
            "PSC notice for Verizon order-of-entry matter 22-00079 is addressed to "
            "James Simari and The Hamilton Owners Corp. care of Harlem Property "
            "Management for 330 West 145th Street."
        ),
    },
    {
        "candidate_id": "ny-dps-exhibit-202-w-140-address-range-review",
        "candidate_status": "address_range_review_required",
        "source_name": "ny_dps_order_entry",
        "source_type": "ny_dps_order_entry_exhibit",
        "source_family": "ny_dps_order_entry",
        "source_record_id": "ny-dps-9365475-202-w-140-exhibit",
        "source_url": "https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7B32249633-00BF-46BE-9DBC-4918ECA197B1%7D&DocTitle=Exhibit+1",
        "observed_at": "2016-02-10T00:00:00+00:00",
        "external_address": "202 WEST 140 STREET",
        "local_address": "204 WEST 140 STREET",
        "external_owner": "Strivers North Condominium",
        "manager_name": "Harlem Property Management, Inc.",
        "manager_contact_name": "Jim Simari",
        "evidence_role": "mdu_managing_agent_company",
        "evidence_summary": (
            "DPS/Verizon exhibit lists 202 W 140 Street, but the local Harlem lead row "
            "uses 204 W 140 Street / condo tax lot; requires address-range confirmation."
        ),
    },
    {
        "candidate_id": "ny-dps-exhibit-204-w-140-exact-match",
        "candidate_status": "clean_exact_match",
        "source_name": "ny_dps_order_entry",
        "source_type": "ny_dps_order_entry_exhibit",
        "source_family": "ny_dps_order_entry",
        "source_record_id": "ny-dps-17-02449-204-w-140-exhibit",
        "source_url": "https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7B304B3F37-2994-4AE2-A199-4485C050FC42%7D",
        "observed_at": "2017-11-15T00:00:00+00:00",
        "external_address": "204 W 140 ST",
        "local_address": "204 WEST 140 STREET",
        "external_owner": "Strivers North Condominium",
        "manager_name": "Harlem Property Management, Inc.",
        "manager_contact_name": "Jim Simari",
        "evidence_role": "mdu_managing_agent_company",
        "evidence_summary": (
            "DPS/Verizon exhibit lists 204 W 140 ST, Strivers North Condominium, "
            "and Harlem Property Management, Inc. as Managing Company with Jim "
            "Simari as contact. Treat this as exact-property manager evidence for "
            "the local 204 West 140 Street / BBL 1020257501 relationship; the older "
            "202 W 140 exhibit remains address-range review context only."
        ),
    },
    {
        "candidate_id": "justia-mckew-11-st-nicholas-property-manager",
        "candidate_status": "clean_exact_match",
        "source_name": "justia",
        "source_type": "ny_court_decision",
        "source_family": "litigation_records",
        "source_record_id": "justia-2025-ny-slip-op-30666-u",
        "source_url": "https://law.justia.com/cases/new-york/other-courts/2025/2025-ny-slip-op-30666-u.html",
        "identity_evidence_urls": [
            "https://home4.nyc.gov/assets/finance/downloads/pdf/rolling_sales/annualized-sales/2024/2024_manhattan.pdf",
            "https://www.nyc.gov/assets/buildings/pdf/cbl_income.pdf",
        ],
        "observed_at": "2025-02-18T00:00:00+00:00",
        "external_address": "11-15 ST NICHOLAS AVENUE",
        "local_address": "11 ST NICHOLAS AVENUE",
        "external_owner": "11-15 St. Nicholas Avenue HDFC",
        "manager_name": "Harlem Property Management, Inc.",
        "manager_contact_name": "Robert D. Pair",
        "evidence_role": "receiver_property_manager",
        "evidence_summary": (
            "2025 New York County decision describes Robert D. Pair of Harlem Property "
            "Management as the plaintiff receiver's property manager for 11-15 St. "
            "Nicholas Avenue HDFC and its real property. The local building row is "
            "11 St Nicholas Avenue / BBL 1018210025; NYC rolling-sales and LL97 "
            "records tie the 11 and 11-15 address forms to the same tax lot."
        ),
    },
    {
        "candidate_id": "nyc-dof-275-greenwich-hpm-billing-record",
        "candidate_status": "new_relationship_candidate",
        "source_name": "nyc_dof_assessment",
        "source_type": "nyc_finance_tax_assessment",
        "source_family": "nyc_dof_billing_record",
        "source_record_id": "nyc-dof-1001327501-2026-2027-tentative-assessment",
        "source_url": (
            "https://a836-pts-access.nyc.gov/care/datalets/datalet.aspx?"
            "LMparent=20&UseSearch=no&jur=65&mode=asmt_tent_2027&pin=1001327501&taxyr=2025"
        ),
        "observed_at": "2026-05-15T00:00:00+00:00",
        "external_address": "275 GREENWICH STREET",
        "local_address": "269 GREENWICH STREET",
        "identity_evidence_urls": [
            "https://openstoop.com/building/275-greenwich-street-manhattan",
        ],
        "external_owner": "Greenwich Court Condominium Associates Corp.",
        "manager_name": "Harlem Property Management, Inc.",
        "manager_contact_name": None,
        "evidence_role": "tax_billing_contact",
        "evidence_summary": (
            "NYC Finance's 2026-2027 tentative assessment page for BBL 1001327501 "
            "lists property address 275 Greenwich Street and billing name/address as "
            "Harlem Property Management, Inc. The local building row for the same BBL "
            "is 269 Greenwich Street. Treat this as official billing-agent context for "
            "a possible new relationship, not operating-manager proof by itself."
        ),
    },
    {
        "candidate_id": "hpm-site-review-275-greenwich-management-takeover",
        "candidate_status": "new_relationship_candidate",
        "source_name": "company_website",
        "source_type": "company_website_customer_review",
        "source_family": "company_website",
        "source_record_id": "harlempm-homepage-review-275-greenwich",
        "source_url": "https://harlempm.com/",
        "observed_at": "2026-05-15T00:00:00+00:00",
        "external_address": "275 GREENWICH STREET",
        "local_address": "269 GREENWICH STREET",
        "identity_evidence_urls": [
            "https://openstoop.com/building/275-greenwich-street-manhattan",
        ],
        "external_owner": "Greenwich Court Condominium Associates Corp.",
        "manager_name": "Harlem Property Management, Inc.",
        "manager_contact_name": "Greg Poverelli",
        "evidence_role": "company_site_customer_review_management_context",
        "evidence_summary": (
            "Harlem Property Management's public homepage includes a customer review saying "
            "HPM took over 275 Greenwich Street and helped approve a buyer. Because this is "
            "review text hosted on the company site rather than a formal client list, treat it "
            "as review-gated company-site context for a possible new relationship."
        ),
    },
]

HPM_REVENUE_BY_PROPERTY_SOURCE_CANDIDATES: list[dict[str, Any]] = [
    _hpm_revenue_by_property_candidate(
        slug="11-st-nicholas-avenue",
        row_number=3,
        sheet_property_label="11-15 St. Nicholas Ave",
        local_address="11 ST NICHOLAS AVENUE",
    ),
    _hpm_revenue_by_property_candidate(
        slug="36-w-138-street",
        row_number=14,
        sheet_property_label="138th 36 W - HDFC",
        local_address="36 WEST 138 STREET",
    ),
    _hpm_revenue_by_property_candidate(
        slug="204-w-140-street",
        row_number=15,
        sheet_property_label="140th 202/204 W - Strivers North Condo",
        local_address="204 WEST 140 STREET",
    ),
    _hpm_revenue_by_property_candidate(
        slug="2257-adam-c-powell-boulevard",
        row_number=30,
        sheet_property_label="2257 ACPB - Ellison Condo",
        local_address="2257 ADAM C POWELL BOULEVARD",
    ),
    _hpm_revenue_by_property_candidate(
        slug="306-w-115-street",
        row_number=44,
        sheet_property_label="306W 115th - The IBO Condo",
        local_address="306 WEST 115 STREET",
    ),
    _hpm_revenue_by_property_candidate(
        slug="324-e-112-street",
        row_number=48,
        sheet_property_label="324 East 112th St",
        local_address="324 EAST 112 STREET",
    ),
    _hpm_revenue_by_property_candidate(
        slug="330-w-145-street",
        row_number=49,
        sheet_property_label="330 West 145th - The Hamilton Coop",
        local_address="330 WEST 145 STREET",
    ),
    _hpm_revenue_by_property_candidate(
        slug="345-lenox-avenue",
        row_number=50,
        sheet_property_label="345 Lenox Ave - Condo",
        local_address="345 LENOX AVENUE",
    ),
    _hpm_revenue_by_property_candidate(
        slug="42-w-120-street",
        row_number=55,
        sheet_property_label="42 W 120th - Park Place Condo",
        local_address="42 WEST 120 STREET",
    ),
    _hpm_revenue_by_property_candidate(
        slug="506-e-119-street",
        row_number=57,
        sheet_property_label="506 East 119th St",
        local_address="506 EAST 119 STREET",
    ),
    _hpm_revenue_by_property_candidate(
        slug="555-lenox-avenue",
        row_number=59,
        sheet_property_label="555 Malcolm X - Savoy West Condo",
        local_address="555 LENOX AVENUE",
    ),
    _hpm_revenue_by_property_candidate(
        slug="342-w-56-street",
        row_number=62,
        sheet_property_label="56th 342 W - Coop",
        local_address="342 WEST 56 STREET",
    ),
    _hpm_revenue_by_property_candidate(
        slug="61-lenox-avenue",
        row_number=63,
        sheet_property_label="61 Malcolm X Blvd - Parc Vues Condo",
        local_address="61 LENOX AVENUE",
    ),
]

MANAGER_EXTERNAL_SOURCE_CANDIDATES.extend(HPM_REVENUE_BY_PROPERTY_SOURCE_CANDIDATES)

OPERATOR_CONFIRMED_MANAGEMENT_CANDIDATES: list[dict[str, Any]] = [
    {
        "candidate_id": "operator-confirmed-md-squared-220-3-ave",
        "user_address": "220 Third Ave",
        "address_aliases": ["220 3 AVENUE", "220 THIRD AVENUE"],
        "manager_name": "MD Squared",
        "target_lead_names": ["MD SQUARED PROPERTY GROUP"],
        "source_record_id": "operator-confirmed-2026-05-15-md-squared-220-3-ave",
        "observed_at": "2026-05-15T00:00:00+00:00",
    },
    {
        "candidate_id": "operator-confirmed-md-squared-57-bond-st",
        "user_address": "57 Bond St",
        "address_aliases": ["57 BOND STREET", "57 BOND ST"],
        "manager_name": "MD Squared",
        "target_lead_names": ["MD SQUARED PROPERTY GROUP"],
        "source_record_id": "operator-confirmed-2026-05-15-md-squared-57-bond-st",
        "observed_at": "2026-05-15T00:00:00+00:00",
    },
    {
        "candidate_id": "operator-confirmed-md-squared-4-w-16-st",
        "user_address": "4 W 16th St",
        "address_aliases": ["4 WEST 16 STREET", "4 W 16 STREET", "4 WEST 16TH STREET", "4 W 16TH STREET"],
        "manager_name": "MD Squared",
        "target_lead_names": ["MD SQUARED PROPERTY GROUP"],
        "source_record_id": "operator-confirmed-2026-05-15-md-squared-4-w-16-st",
        "observed_at": "2026-05-15T00:00:00+00:00",
    },
    {
        "candidate_id": "operator-confirmed-daisy-9-prospect-park-w",
        "user_address": "9 Prospect Park W",
        "address_aliases": ["9 PROSPECT PARK WEST", "9 PROSPECT PARK W"],
        "manager_name": "Daisy",
        "target_lead_names": ["DAISY MANAGEMENT"],
        "source_record_id": "operator-confirmed-2026-05-15-daisy-9-prospect-park-w",
        "observed_at": "2026-05-15T00:00:00+00:00",
    },
]

OPERATOR_CONFIRMED_SECOND_SOURCE_CANDIDATES: list[dict[str, Any]] = [
    {
        "operator_candidate_id": "operator-confirmed-md-squared-220-3-ave",
        "candidate_id": "renthistory-md-squared-220-222-3-ave",
        "candidate_status": "hpd_registration_derived_review_required",
        "source_name": "renthistory",
        "source_type": "hpd_registration_index",
        "source_family": "hpd_registration_derived",
        "source_record_id": "renthistory-md-squared-property-group-220-222-3-ave",
        "source_url": "https://renthistory.org/corporation/corp_report.php?corporationname=MD+SQUARED+PROPERTY+GROUP",
        "observed_at": "2026-05-14T00:00:00+00:00",
        "external_address": "220-222 3 AVENUE",
        "evidence_role": "hpd_registration_associated_property",
        "evidence_summary": (
            "RentHistory's HPD-registration-derived corporation report for MD Squared Property Group "
            "lists 220-222 3 Avenue among associated properties. This is source-overlap context, "
            "but it remains HPD-derived and is excluded from strict manager-proof counts."
        ),
    },
    {
        "operator_candidate_id": "operator-confirmed-md-squared-57-bond-st",
        "candidate_id": "renthistory-md-squared-57-bond-st",
        "candidate_status": "hpd_registration_derived_review_required",
        "source_name": "renthistory",
        "source_type": "hpd_registration_index",
        "source_family": "hpd_registration_derived",
        "source_record_id": "renthistory-md-squared-property-group-57-bond-st",
        "source_url": "https://renthistory.org/corporation/corp_report.php?corporationname=MD+Squared+Property+Group",
        "observed_at": "2026-05-14T00:00:00+00:00",
        "external_address": "57 BOND STREET",
        "evidence_role": "hpd_registration_associated_property",
        "evidence_summary": (
            "RentHistory's HPD-registration-derived corporation report for MD Squared Property Group "
            "lists 57 Bond Street among associated properties. This can make the operator-confirmed "
            "claim broad source-ready if recorded, but it is not independent manager-proof evidence."
        ),
    },
    {
        "operator_candidate_id": "operator-confirmed-md-squared-4-w-16-st",
        "candidate_id": "renthistory-md-squared-4-west-16-st",
        "candidate_status": "hpd_registration_derived_review_required",
        "source_name": "renthistory",
        "source_type": "hpd_registration_index",
        "source_family": "hpd_registration_derived",
        "source_record_id": "renthistory-md-squared-property-group-4-west-16-st",
        "source_url": "https://renthistory.org/corporation/corp_report.php?corporationname=MD+SQUARED+PROPERTY+GROUP",
        "observed_at": "2026-05-14T00:00:00+00:00",
        "external_address": "4 WEST 16 STREET",
        "evidence_role": "hpd_registration_associated_property",
        "evidence_summary": (
            "RentHistory's HPD-registration-derived network/corporation pages associate "
            "4 West 16 Street with MD Squared Property Group. It is exact-property context, "
            "but not a non-HPD-derived manager-proof family."
        ),
    },
    {
        "operator_candidate_id": "operator-confirmed-md-squared-4-w-16-st",
        "candidate_id": "legal-case-md-squared-4-west-16-st-lapaglia",
        "candidate_status": "review_required_legal_role_inference",
        "source_name": "justia",
        "source_type": "court_opinion_and_docket_mirror",
        "source_family": "litigation_records",
        "source_record_id": "lapaglia-v-4-w-16-st-corp-index-503845-2020",
        "source_url": "https://law.justia.com/cases/new-york/other-courts/2023/2023-ny-slip-op-50516-u.html",
        "observed_at": "2026-05-15T00:00:00+00:00",
        "external_address": "4 WEST 16 STREET",
        "evidence_role": "legal_record_property_manager_context",
        "evidence_summary": (
            "The 2023 Marson v. 4 W. 16 St. Corp. court opinion states that, in the related "
            "LaPaglia premises-liability action, the landlord and its property manager were named "
            "as defendants for 4 West 16 Street. A public docket mirror for the LaPaglia case names "
            "MD Squared Property Group, LLC among the defendants alongside 4 W. 16th Street Corp. "
            "Treat this as exact-property legal-record manager context that requires operator review "
            "before recording because the court opinion supplies the property-manager role while the "
            "docket mirror supplies the MD Squared party name."
        ),
    },
    {
        "operator_candidate_id": "operator-confirmed-daisy-9-prospect-park-w",
        "candidate_id": "homes-daisy-9-prospect-park-w-unit-1a",
        "candidate_status": "clean_exact_match",
        "source_name": "homes",
        "source_type": "real_estate_listing_property_profile",
        "source_family": "real_estate_listing",
        "source_record_id": "homes-9-prospect-park-w-unit-1a-s54jg6l4hhxzj",
        "source_url": "https://www.homes.com/property/9-prospect-park-w-brooklyn-ny-unit-1a/s54jg6l4hhxzj/",
        "observed_at": "2026-05-14T00:00:00+00:00",
        "external_address": "9 PROSPECT PARK W",
        "evidence_role": "listing_management_company",
        "evidence_summary": (
            "Homes.com listing data for 9 Prospect Park W Unit 1A says the property is managed "
            "by Daisy Property Management and includes the manager phone. This is an exact-property "
            "real-estate-listing source family separate from the operator confirmation."
        ),
    },
    {
        "operator_candidate_id": "operator-confirmed-daisy-9-prospect-park-w",
        "candidate_id": "redfin-daisy-9-prospect-park-w-unit-1a",
        "candidate_status": "clean_exact_match_same_listing_family",
        "source_name": "redfin",
        "source_type": "real_estate_listing_property_profile",
        "source_family": "real_estate_listing",
        "source_record_id": "redfin-9-prospect-park-w-unit-1a-147153073",
        "source_url": "https://www.redfin.com/NY/Brooklyn/9-Prospect-Park-W-11215/unit-1A/home/147153073",
        "observed_at": "2026-05-15T00:00:00+00:00",
        "external_address": "9 PROSPECT PARK W",
        "evidence_role": "listing_management_company",
        "evidence_summary": (
            "Redfin listing data for 9 Prospect Park W Unit 1A names Daisy Property Management as "
            "the management company and includes the same manager phone. This corroborates the exact "
            "Daisy relationship, but it remains the same real-estate-listing source family as Homes.com "
            "rather than a third independent manager-proof family."
        ),
    },
]

OPERATOR_CONFIRMED_REVIEWED_SOURCE_FINDINGS: list[dict[str, Any]] = [
    {
        "source_family": "company_website",
        "source_urls": [
            "https://www.mdsquaredpropertygroup.com/property-management-services/",
            "https://www.mdsquaredpropertygroup.com/residential/",
        ],
        "finding": (
            "MD Squared's site proves the company is a NYC property manager and describes condo, co-op, "
            "multifamily, commercial, and building-management services."
        ),
        "qualification": (
            "It does not list 220 3 Avenue, 57 Bond Street, or 4 West 16 Street as exact managed "
            "properties, so it remains company-role context rather than building relationship proof."
        ),
    },
    {
        "source_family": "public_building_profile",
        "source_urls": ["https://openstoop.com/building/4-west-16-street-manhattan"],
        "finding": (
            "OpenStoop's 4 West 16 Street profile has exact-property records that mention MD2 Property "
            "Group in owner/boiler-record context."
        ),
        "qualification": (
            "Owner, boiler owner, applicant, or compliance-record context is role-adjacent but not a "
            "property-management claim. It is review context only unless a source explicitly says manager "
            "or managing agent."
        ),
    },
    {
        "source_family": "external_web_profile",
        "source_urls": [
            "https://www.openigloo.com/contact/nyc/c072472d-b88d-4f79-aaf2-c5f8df6327eb/md-squared-property-group"
        ],
        "finding": (
            "OpenIgloo's MD Squared Property Group profile proves a public external profile exists for "
            "the company, but the reviewed profile lists other associated properties rather than the "
            "operator-confirmed seed buildings."
        ),
        "qualification": (
            "It does not list 220 3 Avenue, 57 Bond Street, or 4 West 16 Street, so it is a negative "
            "source-acquisition finding and cannot be recorded as support for those exact manages_building "
            "claims."
        ),
    },
    {
        "source_family": "ny_dos_or_legal_mailing",
        "source_urls": ["https://www.bizprofile.net/ny/new-york/4-w-16-street"],
        "finding": (
            "A reviewed NY DOS mirror for 4 W. 16 Street Corp. lists Md Squared Property Group, LLC "
            "as the service-of-process mailing recipient for the corporation tied to 4 West 16th Street."
        ),
        "qualification": (
            "This is legal-mailing/service-of-process context, not a property-management statement. "
            "Do not record it as support for a manages_building claim unless an official record or "
            "building-specific source states a management or managing-agent role."
        ),
    },
    {
        "source_family": "stale_public_utility_notice",
        "source_urls": [
            "https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7BB9856159-EB11-4740-9335-3B8D4C4C7E10%7D&DocTitle=Exhibit+1",
            "https://device.report/m/c7d7baaaf59d4e3d7ba88af56226a7a3af8df7e354ca2750dda829a139ab1396",
        ],
        "finding": (
            "A reviewed 2015 NY DPS public-utility exhibit names 57 Bond St / Bond Street Lofts "
            "Condominium with Andrews Building Corp. as MDU Managing Agent Co.; a mirrored petition also "
            "lists Bond Street Lofts Condominium care of Andrews Building Corp. at 666 Broadway, Suite 1201."
        ),
        "qualification": (
            "This is stale 2015 managing-agent/legal-notice context for the 57 Bond Street building, not "
            "current MD Squared management proof. Treat Andrews as contradiction/review context only; if "
            "fresh evidence asserts a conflicting current manager, route it through contradiction review "
            "rather than overwriting."
        ),
    },
    {
        "source_family": "hpd_registration_derived",
        "source_urls": [
            "https://renthistory.org/corporation/corp_report.php?corporationname=MD+SQUARED+PROPERTY+GROUP",
            "https://www.renthistory.org/network-explorer/index.php?query=MD+SQUARED+PROPERTY+GROUP&search_type=corporation",
        ],
        "finding": (
            "RentHistory names the exact MD Squared seed properties, including 220-222 3 Avenue, "
            "57 Bond Street, and 4 West 16 Street."
        ),
        "qualification": (
            "This is useful broad source-overlap context, but it is HPD-registration-derived and stays "
            "excluded from strict manager-proof source-family counts."
        ),
    },
    {
        "source_family": "local_hpd_contact_role_audit",
        "source_urls": [],
        "finding": (
            "A read-only local HPD contact-role audit for the operator-confirmed buildings found "
            "Agent, SiteManager, CorporateOwner, HeadOfficer, and Officer contacts, but no "
            "ManagementCompany contact rows for 220 3 Avenue, 57 Bond Street, 4 West 16 Street, "
            "or 9 Prospect Park West."
        ),
        "qualification": (
            "The local HPD contact table cannot currently supply the role-specific HPD ManagementCompany "
            "second source for these operator-confirmed facts. HPD Agent, SiteManager, CorporateOwner, "
            "HeadOfficer, and Officer rows stay separate role claims and must not be promoted to manager proof."
        ),
    },
    {
        "source_family": "live_hpd_open_data_role_audit_2026_05_15",
        "source_urls": [
            "https://data.cityofnewyork.us/resource/tesw-yqqr.json",
            "https://data.cityofnewyork.us/resource/feu5-w2e2.json",
        ],
        "finding": (
            "A May 15, 2026 read-only NYC Open Data HPD registration/contact query found current "
            "or 2025-registered HPD rows for the four operator-confirmed buildings. 220 3 Avenue, "
            "57 Bond Street, and 4 West 16 Street list MD Squared Property Group as Agent; "
            "9 Prospect Park West lists Daisy Management as Agent. None of the four live HPD "
            "contact sets includes a ManagementCompany row."
        ),
        "qualification": (
            "This confirms the HPD role boundary on fresh public data. Live HPD Agent rows can "
            "support registered-agent/legal-contact claims, but they still cannot be recorded as "
            "manager-proof support for manages_building without a role-specific ManagementCompany "
            "row or another exact manager source."
        ),
    },
    {
        "source_family": "live_hpd_threshold_candidate_role_audit_2026_06_01",
        "source_urls": [
            "https://data.cityofnewyork.us/resource/tesw-yqqr.json",
            "https://data.cityofnewyork.us/resource/feu5-w2e2.json",
        ],
        "finding": (
            "A June 1, 2026 read-only NYC Open Data HPD registration/contact query rechecked the two "
            "source-ready facts where one stronger source would clear the verified confidence threshold: "
            "Daisy Management / 9 Prospect Park West and MD Squared Property Group / 4 West 16 Street. "
            "Both current registrations were reachable and current through 2026-09-01; both had strict "
            "HPD Agent identity matches for the expected manager name after legal-suffix stripping; neither "
            "registration contained a ManagementCompany contact row."
        ),
        "qualification": (
            "This is fresh official role-specific evidence and a negative manager-proof bridge result. "
            "It should keep the threshold-sensitive facts in acquisition_required state: HPD Agent can "
            "support registered-agent/legal-contact claims only, and no hpd_management_company source-evidence "
            "template is recording-ready for these two facts from the June 1 official HPD pass."
        ),
    },
    {
        "source_family": "live_hpd_property_managers_first_step_view_2026_06_01",
        "source_urls": [
            "https://data.cityofnewyork.us/d/v4vh-sni9",
            "https://data.cityofnewyork.us/resource/v4vh-sni9.json",
        ],
        "finding": (
            "A June 1, 2026 read-only check inspected NYC Open Data view `v4vh-sni9`, titled "
            "`Property Managers-1st Step`. The view is community-created and based on HPD "
            "Multiple Dwelling Registrations; its live API rows expose RegistrationID, BoroID, "
            "Boro, HouseNumber, StreetName, StreetCode, Block, Lot, LastRegistrationDate, and "
            "RegistrationEndDate. It does not expose manager, agent, contact type, corporation "
            "name, or role-specific management fields."
        ),
        "qualification": (
            "This is reviewed source-acquisition boundary context only. Despite the dataset title, "
            "`v4vh-sni9` can only help locate registration rows before querying Registration Contacts; "
            "it cannot support, contradict, verify, or activate a manages_building claim without a "
            "matching role-specific `feu5-w2e2` ManagementCompany row or another exact manager-proof source."
        ),
    },
    {
        "source_family": "live_hpd_open_data_catalog_unreachable_2026_05_16",
        "source_urls": [
            "https://data.cityofnewyork.us/d/tesw-yqqr",
            "https://data.cityofnewyork.us/d/feu5-w2e2",
            "https://data.cityofnewyork.us/resource/tesw-yqqr.json",
            "https://data.cityofnewyork.us/resource/feu5-w2e2.json",
        ],
        "finding": (
            "A May 16, 2026 follow-up identified the official NYC Open Data HPD source catalog: "
            "Multiple Dwelling Registrations (`tesw-yqqr`) for BBL-to-registration freshness and "
            "Registration Contacts (`feu5-w2e2`) for role-specific contacts. Direct read-only "
            "queries from this runtime still failed with an unable-to-connect socket error."
        ),
        "qualification": (
            "This is source-access state, not negative evidence. The current runtime cannot prove "
            "whether a fresh HPD ManagementCompany row exists for the operator-confirmed seeds; a "
            "successful live query, export, or operator-supplied official extract is still required "
            "before any HPD manager-proof evidence can be recorded."
        ),
    },
    {
        "source_family": "real_estate_listing",
        "source_urls": [
            "https://www.homes.com/property/9-prospect-park-w-brooklyn-ny-unit-1a/s54jg6l4hhxzj/",
            "https://www.redfin.com/NY/Brooklyn/9-Prospect-Park-W-11215/unit-1A/home/147153073",
        ],
        "finding": (
            "Homes.com and Redfin exact-property listing data for 9 Prospect Park W Unit 1A name Daisy "
            "Property Management as the management company."
        ),
        "qualification": (
            "These are current strict manager-proof listing sources for the Daisy operator-confirmed seed, "
            "but they share the same real-estate-listing family and do not create an additional source "
            "family beyond listing evidence."
        ),
    },
    {
        "source_family": "public_web_search_followup_md_squared_batch_2",
        "source_urls": [
            "https://renthistory.org/corporation/corp_report.php?corporationname=MD+Squared+Property+Group",
            "https://www.renthistory.org/network-explorer/index.php?query=MD+SQUARED+PROPERTY+GROUP&search_type=corporation",
            "https://www.crexi.com/lease/properties/NY/New_York/Warehouses?page=25&pageSize=60",
        ],
        "finding": (
            "Fresh exact-property web searches for the operator-confirmed MD Squared seeds again found "
            "RentHistory/HPD-derived exact-property context for 220-222 3 Avenue, 57 Bond Street, and "
            "4 West 16 Street; Crexi surfaced a 57 Bond listing result but did not expose a usable "
            "manager-specific source page in the reviewed public result."
        ),
        "qualification": (
            "Do not count these as a new strict manager-proof family. The MD Squared seeds still need "
            "an exact company-controlled page, external profile, public notice, HPD ManagementCompany "
            "role row, NY DOS record with explicit manager-role language, or outreach-confirmed evidence."
        ),
    },
    {
        "source_family": "public_web_search_live_refresh_2026_05_15",
        "source_urls": [
            "https://www.redfin.com/NY/Brooklyn/9-Prospect-Park-W-11215/unit-1A/home/147153073",
            "https://www.homes.com/property/9-prospect-park-w-brooklyn-ny-unit-1a/s54jg6l4hhxzj/",
            "https://renthistory.org/corporation/corp_report.php?corporationname=MD+SQUARED+PROPERTY+GROUP",
            "https://renthistory.org/network-explorer/index.php?query=4+WEST+16TH+STREET+CORPORATION&search_type=corporation",
        ],
        "finding": (
            "A May 15, 2026 live source refresh re-confirmed current Daisy listing evidence for "
            "9 Prospect Park West: Redfin shows the listing updated May 13, 2026, checked minutes "
            "before review, and names Daisy Property Management; Homes.com lists the same MLS "
            "number, May 2026 listing activity, Daisy as management company, and the same manager "
            "phone. Exact MD Squared searches still resolved only to RentHistory/HPD-derived "
            "property associations for 220-222 3 Avenue, 57 Bond Street, and 4 West 16 Street."
        ),
        "qualification": (
            "The Daisy facts remain strict source-ready-if-recorded because operator confirmation "
            "and listing evidence are separate families, but Homes.com and Redfin stay one "
            "real_estate_listing family. The MD Squared refresh does not add strict manager-proof "
            "overlap because RentHistory remains HPD-registration-derived."
        ),
    },
        {
            "source_family": "public_web_search_followup_md_squared_batch_3",
            "source_urls": [
                "https://www.mdsquaredpropertygroup.com/",
                "https://www.mdsquaredpropertygroup.com/residential/",
            "https://www.mdsquaredpropertygroup.com/available-listings/",
            "https://md2pg.appfolio.com/listings/listings",
            "https://renthistory.org/corporation/corp_report.php?corporationname=MD+Squared+Property+Group",
        ],
        "finding": (
            "A follow-up pass over the MD Squared website, residential/listings pages, AppFolio-linked "
            "listing surface, and exact-property web queries for 220-222 3 Avenue, 57 Bond Street, "
            "and 4 West 16 Street found company-role proof and HPD-derived RentHistory overlap but "
            "no company-controlled page or listing that names any of the three operator-confirmed "
            "seed buildings as managed by MD Squared. The linked AppFolio listing surface was live "
            "but showed unrelated Edinboro, PA rentals, not the NYC seed buildings."
        ),
            "qualification": (
                "Do not add a strict second-source template for the MD Squared seeds from this pass. "
                "They still need an exact property page, public notice, role-explicit legal record, "
                "HPD ManagementCompany row, non-HPD external profile, or dated outreach confirmation."
            ),
        },
        {
            "source_family": "public_web_search_followup_md_squared_batch_4_2026_05_15",
            "source_urls": [
                "https://renthistory.org/corporation/corp_report.php?corporationname=MD+SQUARED+PROPERTY+GROUP",
                "https://www.renthistory.org/network-explorer/index.php?query=MD+SQUARED+PROPERTY+GROUP&search_type=corporation",
                "https://www.bizprofile.net/ny/new-york/4-w-16-street",
            ],
            "finding": (
                "A later exact-property pass for 220 Third Avenue Condominium, Bond Street Lofts, "
                "and 4 W. 16 Street Corp. again found HPD-derived RentHistory overlap and NY DOS "
                "service-of-process context, including MD Squared tied to 220-222 3 Avenue, "
                "57 Bond Street, and 4 West 16 Street in RentHistory and Md Squared Property Group "
                "as service-of-process recipient for 4 W. 16 Street Corp."
            ),
            "qualification": (
                "This does not add strict manager-proof evidence. RentHistory remains HPD-derived, "
                "and NY DOS/service-of-process context is legal-mailing evidence unless the source "
                "explicitly states a property-management or managing-agent role for the exact building."
            ),
        },
        {
            "source_family": "public_web_search_followup_md_squared_batch_5_2026_05_15",
            "source_urls": [
                "https://www.mdsquaredpropertygroup.com/",
                "https://www.mdsquaredpropertygroup.com/residential/",
                "https://www.mdsquaredpropertygroup.com/available-listings/",
                "https://md2pg.appfolio.com/listings/listings",
                "https://renthistory.org/corporation/corp_report.php?corporationname=MD+Squared+Property+Group",
                "https://www.bizprofile.net/ny/new-york/4-w-16-street",
            ],
            "finding": (
                "A later exact-property search pass for 220 3 Avenue / 220 Third Avenue / "
                "220-222 3 Avenue, 57 Bond Street, and 4 West 16 Street repeated MD Squared "
                "company-role pages, the MD2-linked AppFolio surface, RentHistory HPD-derived "
                "property associations, and NY DOS service-of-process context, but found no "
                "company-controlled exact-property page, external profile, public notice, HPD "
                "ManagementCompany row, role-explicit NY DOS record, or outreach-confirmed "
                "second source for the MD Squared seed buildings."
            ),
            "qualification": (
                "This is a negative reviewed-source finding only. Do not add a strict "
                "manager-proof template for the MD Squared seeds from repeated company-role, "
                "AppFolio, HPD-derived, or legal-mailing context; they still need exact "
                "non-HPD manager proof or additional outreach-confirmed evidence."
            ),
        },
        {
            "source_family": "public_web_search_followup_md_squared_batch_6_2026_05_15",
            "source_urls": [
                "https://www.mdsquaredpropertygroup.com/",
                "https://www.mdsquaredpropertygroup.com/available-listings/",
                "https://renthistory.org/corporation/corp_report.php?corporationname=MD+SQUARED+PROPERTY+GROUP",
                "https://www.renthistory.org/network-explorer/index.php?query=MD+SQUARED+PROPERTY+GROUP&search_type=corporation",
            ],
            "finding": (
                "A continuation exact-property search pass for 220 Third Avenue / 220 3 Avenue / "
                "220-222 3 Avenue, 57 Bond Street / Bond Street Lofts, and 4 West 16 Street / "
                "4 W. 16 Street Corp. found only repeated RentHistory/HPD-derived exact-property "
                "associations, generic MD Squared company/listing surfaces, and unrelated MD Squared "
                "landlord or legal snippets for other properties."
            ),
            "qualification": (
                "This pass adds no strict manager-proof template and does not change strict source-ready "
                "counts. The MD Squared seeds still need exact non-HPD manager proof, such as a "
                "company-controlled exact-property page, public exact-property profile, HPD "
                "ManagementCompany row, role-explicit NY DOS record, or an additional dated "
                "outreach-confirmed source."
            ),
        },
        {
            "source_family": "public_web_search_followup_md_squared_batch_7_2026_05_15",
            "source_urls": [
                "https://renthistory.org/corporation/corp_report.php?corporationname=MD+SQUARED+PROPERTY+GROUP",
                "https://www.renthistory.org/network-explorer/index.php?query=MD+SQUARED+PROPERTY+GROUP&search_type=corporation",
                "https://renthistory.org/network-explorer/index.php?query=4+WEST+16TH+STREET+CORPORATION&search_type=corporation",
                "https://openstoop.com/building/4-west-16-street-manhattan",
            ],
            "finding": (
                "A final post-contract exact search for MD Squared plus 220-222 3 Avenue, "
                "57 Bond Street, 4 West 16 Street, and MD2 Property Group again found only "
                "RentHistory/HPD-derived associations for the seed properties and OpenStoop "
                "owner/boiler-style context for 4 West 16 Street. It did not find a company "
                "page, listing, public notice, HPD ManagementCompany row, role-explicit NY DOS "
                "record, or external exact-property profile stating that MD Squared manages "
                "the seed buildings."
            ),
            "qualification": (
                "Do not add a strict manager-proof template from this pass. RentHistory remains "
                "HPD-registration-derived and OpenStoop owner context is not a management-role "
                "claim; the MD Squared seeds still need exact non-HPD manager proof or another "
                "dated outreach-confirmed source."
            ),
        },
        {
            "source_family": "public_web_search_followup_md_squared_batch_8_2026_05_15",
            "source_urls": [
                "https://www.redfin.com/NY/Brooklyn/9-Prospect-Park-W-11215/unit-1A/home/147153073",
                "https://www.homes.com/property/9-prospect-park-w-brooklyn-ny-unit-1a/s54jg6l4hhxzj/",
                "https://renthistory.org/corporation/corp_report.php?corporationname=MD+SQUARED+PROPERTY+GROUP",
                "https://renthistory.org/network-explorer/index.php?query=4+WEST+16TH+STREET+CORPORATION&search_type=corporation",
            ],
            "finding": (
                "A post-boundary live exact search again found strict Daisy listing support on "
                "Redfin and Homes.com for 9 Prospect Park West, but MD Squared searches for "
                "220 Third / 220-222 3 Avenue, Bond Street Lofts / 57 Bond Street, and "
                "4 W. 16 Street Corp. / 4 West 16 Street still returned only HPD-derived "
                "RentHistory associations or unrelated/noisy results."
            ),
            "qualification": (
                "This adds no new strict MD Squared source template and does not change strict "
                "source-ready counts. Daisy remains strict source-ready-if-recorded through "
                "operator-confirmed plus real-estate-listing families; the Redfin and Homes.com "
                "items remain one listing family."
            ),
        },
        {
            "source_family": "public_web_search_followup_md_squared_batch_9_2026_05_15",
            "source_urls": [
                "https://www.redfin.com/NY/Brooklyn/9-Prospect-Park-W-11215/unit-1A/home/147153073",
                "https://www.homes.com/property/9-prospect-park-w-brooklyn-ny-unit-1a/s54jg6l4hhxzj/",
                "https://renthistory.org/corporation/corp_report.php?corporationname=MD+SQUARED+PROPERTY+GROUP",
                "https://renthistory.org/network-explorer/index.php?query=MD+SQUARED+PROPERTY+GROUP&search_type=corporation",
            ],
            "finding": (
                "A follow-up exact search using both address and condo/corporation names found "
                "Redfin and Homes.com still naming Daisy Property Management for 9 Prospect "
                "Park West, while MD Squared searches for 220 Third Avenue Condominium, "
                "Bond Street Lofts Condominium, and 4 West 16th Street Corporation again "
                "returned RentHistory/HPD-derived associations rather than an exact non-HPD "
                "manager source."
            ),
            "qualification": (
                "This confirms the current split: Daisy remains strict source-ready-if-recorded, "
                "but the MD Squared seeds remain broad source-ready only after approval because "
                "the second family is HPD-registration-derived and cannot count as strict "
                "manager-proof evidence."
            ),
        },
        {
            "source_family": "public_web_search_followup_md_squared_batch_10_2026_05_15",
            "source_urls": [
                "https://renthistory.org/corporation/corp_report.php?corporationname=MD+Squared+Property+Group",
                "https://renthistory.org/network-explorer/index.php?query=4+WEST+16TH+STREET+CORPORATION&search_type=corporation",
                "https://www.crexi.com/lease/properties?cities%5B%5D=New%20York,%20NY&page=7&states%5B%5D=NY&types%5B%5D=Retail",
                "https://www.nyc.gov/assets/finance/downloads/pdf/reports/issuers_allocation/2024/issuers-allocation-percentage-2024.pdf",
            ],
            "finding": (
                "A targeted alternate-name pass searched 220-222 Third Avenue / 220 Third Avenue "
                "Condominium, Bond Street Lofts / Bond Street Lofts Condominium, 57 Bond Street, "
                "4 West 16 Street / 4 West 16th Street Corporation, and property-manager language. "
                "The results again concentrated in RentHistory/HPD-derived association pages; Crexi "
                "surfaced unrelated MD Squared leasing context, and NYC Finance issuer-allocation "
                "records confirmed condo/corp names but did not name a manager."
            ),
            "qualification": (
                "This does not add a strict MD Squared manager-proof source. RentHistory remains "
                "HPD-registration-derived, Crexi did not tie MD Squared to the seed buildings, and "
                "issuer-allocation records establish entity names rather than property-management "
                "relationships."
            ),
        },
        {
            "source_family": "public_web_search_followup_md_squared_batch_11_2026_05_15",
            "source_urls": [
                "https://renthistory.org/corporation/corp_report.php?corporationname=MD+SQUARED+PROPERTY+GROUP",
                "https://renthistory.org/network-explorer/index.php?query=4+WEST+16TH+STREET+CORPORATION&search_type=corporation",
                "https://openstoop.com/building/4-west-16-street-manhattan",
                "https://www.linkedin.com/posts/jon-klaristenfeld-b16374203_propertymanagement-businessdevelopment-activity-7279873632586149888-1fE0",
            ],
            "finding": (
                "A fresh exact-property pass searched MD Squared / MD Squared Property Group with "
                "220 3 Avenue, 220 Third Avenue Condominium, 57 Bond Street, Bond Street Lofts, "
                "4 West 16 Street, 4 West 16th Street Corporation, and manager/property-manager "
                "language. Results again repeated RentHistory/HPD-derived exact-property "
                "associations; OpenStoop exposed owner/boiler-style context for 4 West 16 Street; "
                "and a public LinkedIn portfolio post named other MD Squared properties but not the "
                "three seed buildings."
            ),
            "qualification": (
                "This pass adds no strict MD Squared manager-proof source. RentHistory remains "
                "HPD-registration-derived, OpenStoop owner/boiler context is not a management-role "
                "claim, and the LinkedIn portfolio post does not tie MD Squared to 220 3 Avenue, "
                "57 Bond Street, or 4 West 16 Street."
            ),
        },
        {
            "source_family": "public_web_search_followup_md_squared_batch_12_2026_05_15",
            "source_urls": [
                "https://www.mdsquaredpropertygroup.com/nyc-apartment-management-company/",
                "https://www.mdsquaredpropertygroup.com/building-management-solutions/",
                "https://renthistory.org/corporation/corp_report.php?corporationname=MD+SQUARED+PROPERTY+GROUP",
                "https://www.renthistory.org/network-explorer/index.php?query=MD+SQUARED+PROPERTY+GROUP&search_type=corporation",
            ],
            "finding": (
                "A fresh post-smoke pass searched exact MD Squared address variants for 220 Third "
                "Avenue / 220 3 Avenue / 220-222 3 Avenue, 57 Bond Street / Bond Street Lofts, "
                "and 4 West 16th Street / 4 West 16 Street, plus MD Squared company-controlled "
                "service pages. Results again surfaced RentHistory/HPD-derived exact-property "
                "associations and generic MD Squared building-management service pages; the "
                "company pages describe portfolio-wide management services but do not name any "
                "of the three seed buildings."
            ),
            "qualification": (
                "This pass adds no strict manager-proof source. Generic company-service pages "
                "prove MD Squared offers property management, but not the exact building "
                "relationship; RentHistory remains HPD-registration-derived and excluded from "
                "strict manager-proof source-family counts."
            ),
        },
        {
            "source_family": "public_web_search_followup_md_squared_batch_13_2026_05_15",
            "source_urls": [
                "https://law.justia.com/cases/new-york/other-courts/2023/2023-ny-slip-op-50516-u.html",
                "https://aicasepredict.com/cases-for-court/5616/737834/New%2BYork%2BNew%2BYork%2BState%2BCourts%2BElectronic%2BFiling",
            ],
            "finding": (
                "A legal-record follow-up found a 2023 court opinion for Robert Marson Testamentary "
                "Trust v. 4 W. 16 St. Corp. stating that the landlord and its property manager were "
                "named as defendants in the related LaPaglia premises-liability action for 4 West "
                "16 Street. A public docket mirror for the LaPaglia case names MD Squared Property "
                "Group, LLC among the defendants with 4 W. 16th Street Corp."
            ),
            "qualification": (
                "This is the first exact non-HPD manager-role bridge for a MD Squared seed, but it is "
                "review-gated because the manager role and MD Squared party name come from two public "
                "legal surfaces. Count it only as preview-only litigation-record support for the "
                "4 West 16 Street seed; it does not help 220 3 Avenue or 57 Bond Street."
            ),
        },
        {
            "source_family": "public_web_search_followup_md_squared_batch_14_2026_05_15",
            "source_urls": [
                "https://www.mdsquaredpropertygroup.com/available-listings/",
                "https://www.mdsquaredpropertygroup.com/building-management-solutions/",
                "https://www.mdsquaredpropertygroup.com/nyc-apartment-management-company/",
                "https://renthistory.org/corporation/corp_report.php?corporationname=MD+SQUARED+PROPERTY+GROUP",
                "https://data.cityofnewyork.us/resource/tesw-yqqr.json",
                "https://data.cityofnewyork.us/resource/feu5-w2e2.json",
            ],
            "finding": (
                "A follow-up exact-source pass for the two remaining MD Squared gaps searched "
                "220 3 Avenue / 220 Third Avenue / 220-222 3 Avenue, 57 Bond Street / Bond "
                "Street Lofts, MD Squared company-site listing and building-management pages, "
                "AppFolio listing surfaces, and live NYC Open Data HPD role rows. Results again "
                "found only RentHistory/HPD-registration-derived associations plus company-role "
                "pages that do not name the seed buildings. Live HPD rows list MD Squared as "
                "Agent for both 220 3 Avenue and 57 Bond Street but still show no "
                "ManagementCompany rows."
            ),
            "qualification": (
                "This pass does not change source-ready counts. The company pages are not "
                "exact-property relationship proof, RentHistory remains HPD-derived, and live HPD "
                "Agent rows are registered-agent/legal-contact evidence rather than "
                "manager-proof support. 220 3 Avenue and 57 Bond Street still need one exact "
                "non-HPD manager-proof family or another dated outreach-confirmed source."
            ),
        },
        {
            "source_family": "public_web_search_followup_md_squared_batch_15_2026_05_15",
            "source_urls": [
                "https://renthistory.org/corporation/corp_report.php?corporationname=MD+SQUARED+PROPERTY+GROUP",
                "https://www.renthistory.org/network-explorer/index.php?query=MD+SQUARED+PROPERTY+GROUP&search_type=corporation",
            ],
            "finding": (
                "A further exact-property pass searched 220 Third Avenue Condominium, 220 3rd "
                "Avenue, 220-222 Third Avenue, Bond Street Lofts, Bond Street Lofts "
                "Condominium, 57 Bond Street, and managed-by/property-manager variants. "
                "Public results again resolved to RentHistory/HPD-derived MD Squared "
                "portfolio associations plus property/listing context that does not name an "
                "operating manager."
            ),
            "qualification": (
                "This adds no strict source template. RentHistory remains "
                "HPD-registration-derived, property-only pages do not state a management "
                "relationship, and the two remaining MD Squared seeds still require exact "
                "non-HPD manager proof or another dated outreach-confirmed source before they "
                "can enter the strict operator packet."
            ),
        },
        {
            "source_family": "public_web_search_followup_md_squared_batch_16_2026_05_15",
            "source_urls": [
                "https://renthistory.org/corporation/corp_report.php?corporationname=MD+SQUARED+PROPERTY+GROUP",
                "https://www.renthistory.org/network-explorer/index.php?query=MD+SQUARED+PROPERTY+GROUP&search_type=corporation",
            ],
            "finding": (
                "A latest exact web pass searched MD Squared / MD Squared Property Group with "
                "220 Third Avenue, 220 3 Avenue, 220 Third Avenue Condominium, 57 Bond Street, "
                "Bond Street Lofts, and property-manager variants. Public results again surfaced "
                "RentHistory/HPD-derived MD Squared association pages for 220-222 3 Avenue and "
                "57 Bond Street, plus repeated corporation/network pages, but no company-controlled "
                "exact-property page, external exact-property manager profile, public notice, "
                "role-explicit legal record, HPD ManagementCompany row, or second dated outreach "
                "confirmation."
            ),
            "qualification": (
                "This adds no strict manager-proof source and does not change source-ready counts. "
                "RentHistory remains HPD-registration-derived broad context, while 220 3 Avenue "
                "and 57 Bond Street stay excluded from the strict operator packet until one exact "
                "non-HPD manager-proof family or another dated outreach-confirmed source is found."
            ),
        },
        {
            "source_family": "site_native_search_md_squared_2026_05_15",
            "source_urls": [
                "https://www.mdsquaredpropertygroup.com/wp-json/wp/v2/search?search=220%20Third%20Avenue&per_page=10",
                "https://www.mdsquaredpropertygroup.com/wp-json/wp/v2/search?search=220-222%203%20Avenue&per_page=20",
                "https://www.mdsquaredpropertygroup.com/wp-json/wp/v2/search?search=57%20Bond&per_page=10",
                "https://www.mdsquaredpropertygroup.com/wp-json/wp/v2/search?search=Bond%20Street%20Lofts&per_page=20",
                "https://www.mdsquaredpropertygroup.com/wp-json/wp/v2/search?search=4%20West%2016&per_page=10",
                "https://www.mdsquaredpropertygroup.com/blog/building-energy-ratings-near-you/",
                "https://www.mdsquaredpropertygroup.com/property-management-nyc/",
            ],
            "finding": (
                "A site-native WordPress search checked MD Squared's own public search endpoint for "
                "220 Third Avenue, 220 3 Avenue, 220-222 3 Avenue, 57 Bond, Bond Street Lofts, "
                "Bond Street Lofts Condominium, 4 West 16, 4 W 16, and 4 West 16th. The 220, "
                "220-222, 57 Bond, Bond Street Lofts, and 4 West 16th searches returned no posts "
                "or pages. The 4 West 16 / 4 W 16 searches returned generic company, service, "
                "brokerage, and blog pages; direct page inspection found only metadata/CSS-style "
                "false positives, not a visible exact-property management statement."
            ),
            "qualification": (
                "This adds no strict manager-proof source and does not change source-ready counts. "
                "The operator-confirmed MD Squared gaps still require one exact non-HPD manager-proof "
                "family or another dated outreach-confirmed source."
            ),
        },
        {
            "source_family": "public_web_search_followup_md_squared_batch_17_2026_05_15",
            "source_urls": [
                "https://renthistory.org/corporation/corp_report.php?corporationname=MD+Squared+Property+Group",
                "https://www.renthistory.org/network-explorer/index.php?query=MD+SQUARED+PROPERTY+GROUP&search_type=corporation",
                "https://renthistory.org/network-explorer/index.php?query=MD+SQAURED+PROPERTY+GROUP&search_type=corporation",
            ],
            "finding": (
                "A post-approval-boundary exact search repeated MD Squared / MD Squared Property "
                "Group queries for 220 Third Avenue, 220 Third Avenue Condominium, 220-222 3 "
                "Avenue, 57 Bond Street, Bond Street Lofts, and property-manager variants. "
                "Public results again resolved to RentHistory/HPD-derived corporation and network "
                "pages that list 220 THIRD AVENUE CONDOMINIUM, BOND STREET LOFTS CONDOMINIUM, "
                "and 4 WEST 16TH STREET CORPORATION, plus noisy landlord-network litigation "
                "snippets for other MD Squared matters."
            ),
            "qualification": (
                "This adds no strict template and does not change source-ready counts. The newly "
                "reviewed hits are still HPD-registration-derived or wrong-property/no-role context; "
                "they do not supply a company-controlled exact-property page, external manager "
                "profile, public notice, role-explicit legal record, HPD ManagementCompany row, or "
                "dated second outreach confirmation for 220 3 Avenue or 57 Bond Street."
            ),
        },
        {
            "source_family": "public_web_search_followup_md_squared_batch_18_2026_05_16",
            "source_urls": [
                "https://www.redfin.com/NY/Brooklyn/9-Prospect-Park-W-11215/unit-1A/home/147153073",
                "https://www.homes.com/property/9-prospect-park-w-brooklyn-ny-unit-1a/s54jg6l4hhxzj/",
                "https://renthistory.org/corporation/corp_report.php?corporationname=MD+Squared+Property+Group",
                "https://renthistory.org/network-explorer/index.php?query=4+WEST+16TH+STREET+CORPORATION&search_type=corporation",
            ],
            "finding": (
                "A May 16, 2026 frontier refresh searched the two one-source threshold-clear "
                "simulations and the remaining broad-only MD Squared gaps. Daisy / "
                "9 Prospect Park West still has Redfin and Homes.com listing-family proof "
                "naming Daisy Property Management. MD Squared / 4 West 16 Street still resolves "
                "to the already-reviewed RentHistory/HPD-derived and litigation-record families. "
                "MD Squared / 220 3 Avenue and 57 Bond Street still resolve only to "
                "RentHistory/HPD-derived exact-property association pages and related corporation "
                "network context."
            ),
            "qualification": (
                "This adds no strict template and does not change source-ready or verified counts. "
                "The Daisy listing pages remain one real_estate_listing family, the MD Squared "
                "4 West 16 litigation bridge is already recorded, and the two broad-only MD "
                "Squared seeds still need one exact non-HPD manager-proof family or another "
                "dated outreach-confirmed source. No real HPD ManagementCompany row was found "
                "in this pass, so the one-source threshold-clear cases remain simulations only."
            ),
        },
        {
            "source_family": "operator_document_search_md_daisy_2026_05_16",
            "source_urls": [],
            "finding": (
                "A read-only Drive/local workbook pass checked the operator-provided HPM revenue sheet "
                "and local HPM workbook copies for MD Squared / Daisy seed strings and address variants "
                "including 220 3 Avenue, 57 Bond Street, 4 West 16 Street, and 9 Prospect Park West. "
                "No exact MD Squared or Daisy building-manager relationship row was found."
            ),
            "qualification": (
                "This adds no strict operator template and does not change source-ready or verified "
                "counts. The two broad-only MD Squared seeds still require one exact non-HPD "
                "manager-proof family or another dated outreach-confirmed source; Daisy and 4 West 16 "
                "remain source-ready but below the verified threshold."
            ),
        },
        {
            "source_family": "official_hpd_and_public_web_refresh_md_daisy_2026_05_18",
            "source_urls": [
                "https://data.cityofnewyork.us/d/tesw-yqqr",
                "https://data.cityofnewyork.us/d/feu5-w2e2",
                "https://www.homes.com/property/9-prospect-park-w-brooklyn-ny-unit-1a/s54jg6l4hhxzj/",
                "https://www.redfin.com/NY/Brooklyn/9-Prospect-Park-W-11215/unit-1A/home/147153073",
                "https://renthistory.org/corporation/corp_report.php?corporationname=MD+Squared+Property+Group",
            ],
            "finding": (
                "A May 18, 2026 Phase 2 acquisition pass generated exact official HPD query packets for "
                "MD Squared / 4 West 16 Street, Daisy / 9 Prospect Park West, MD Squared / 220 3 Avenue, "
                "and MD Squared / 57 Bond Street. Direct read-only `tesw-yqqr` registration-slice fetches "
                "from this runtime still failed with `Unable to connect to the remote server`. Fresh web "
                "search reconfirmed Daisy listing-family evidence on Homes.com and Redfin for 9 Prospect "
                "Park West, while MD Squared searches still resolved to HPD-derived RentHistory/corporation "
                "context rather than a new exact non-HPD manager-proof source."
            ),
            "qualification": (
                "This adds no strict evidence template and does not change source-ready or verified counts. "
                "The failed HPD fetch is source-access state, not proof that HPD ManagementCompany rows are "
                "absent. Daisy's Homes.com/Redfin support remains one real_estate_listing family, and the "
                "two broad-only MD Squared seeds still need one exact non-HPD manager-proof family, a real "
                "HPD ManagementCompany row from an official extract/query, or another dated outreach-confirmed source."
            ),
        },
        {
            "source_family": "operator_document_raw_xlsx_followup_md_daisy_2026_05_19",
            "source_urls": [],
            "finding": (
                "A May 19, 2026 read-only raw `.xlsx` fetch of the operator-provided HPM "
                "'Revenue by Property - Summary' workbook reconfirmed the known HPM property rows "
                "but did not contain exact MD Squared or Daisy seed rows for 220 3 Avenue, "
                "57 Bond Street, 4 West 16 Street, or 9 Prospect Park West. The native Google "
                "Sheet metadata/range path hit a Sheets API rate limit before returning any new "
                "operator-document evidence."
            ),
            "qualification": (
                "This is reviewed source-acquisition history only. It adds no evidence template, "
                "does not prove absence outside the reviewed workbook, does not expose private "
                "Drive URLs or row-level revenue data, and does not change source-ready or verified "
                "counts. The MD Squared broad gaps still need an exact non-HPD manager-proof source, "
                "a real HPD ManagementCompany row from an official extract/query, or a dated second "
                "outreach confirmation; Daisy and 4 West 16 remain source-ready below verified."
            ),
        },
        {
            "source_family": "operator_document_native_sheet_followup_md_daisy_2026_05_19",
            "source_urls": [],
            "finding": (
                "A May 19, 2026 follow-up searched Google Drive for a separate `Revenue by Building` "
                "style file and found no distinct operator source beyond the already-reviewed "
                "`Revenue by Property - Summary` materials. The native Google Sheet `Summary` tab "
                "was then searched for MD Squared/Daisy seed variants including 220, Bond, and "
                "Prospect; it did not contain exact rows for 220 3 Avenue, 57 Bond Street, or "
                "9 Prospect Park West."
            ),
            "qualification": (
                "This is reviewed source-acquisition history only. It adds no evidence template, "
                "does not prove absence outside the reviewed Drive materials, exposes no private "
                "Drive URL or row-level revenue data, and does not change source-ready or verified "
                "counts. The MD Squared broad gaps still need an exact non-HPD manager-proof source, "
                "a real HPD ManagementCompany row from an official extract/query, or a dated second "
                "outreach confirmation; Daisy remains source-ready below verified."
            ),
        },
        {
            "source_family": "official_hpd_and_public_web_refresh_md_daisy_2026_05_19_phase2",
            "source_urls": [
                "https://catalog.data.gov/dataset/multiple-dwelling-registrations",
                "https://catalog.data.gov/dataset/registration-contacts",
                "https://renthistory.org/corporation/corp_report.php?corporationname=MD+Squared+Property+Group",
                "https://www.homes.com/property/9-prospect-park-w-brooklyn-ny-unit-1a/s54jg6l4hhxzj/",
                "https://www.redfin.com/NY/Brooklyn/9-Prospect-Park-W-11215/unit-1A/home/147153073",
            ],
            "finding": (
                "A later May 19, 2026 read-only acquisition pass again attempted exact official HPD "
                "registration-slice queries for MD Squared / 220 3 Avenue, MD Squared / 57 Bond Street, "
                "MD Squared / 4 West 16 Street, and Daisy / 9 Prospect Park West. Shell access to the "
                "Socrata row APIs still failed with `Unable to connect to the remote server`, while the "
                "official Data.gov catalog pages confirmed the `tesw-yqqr` registrations and `feu5-w2e2` "
                "registration-contacts datasets are public and last updated May 1, 2026. Fresh exact web "
                "searches repeated already-reviewed evidence families: Daisy still has Homes.com/Redfin "
                "listing-family manager support, and MD Squared / 220 3 Avenue plus 57 Bond Street still "
                "resolve to RentHistory/HPD-derived corporation-network context such as 220 THIRD AVENUE "
                "CONDOMINIUM and BOND STREET LOFTS CONDOMINIUM."
            ),
            "qualification": (
                "This adds reviewed source-acquisition history only. It does not prove official HPD "
                "ManagementCompany absence, adds no source-evidence template, and does not change "
                "recording-ready, source-ready, verified, or business-use counts. RentHistory remains "
                "HPD-registration-derived context and cannot be used as the exact non-HPD manager-proof "
                "family needed for the two broad-only MD Squared seeds."
            ),
        },
        {
            "source_family": "public_web_and_drive_retry_md_daisy_2026_05_19_phase3",
            "source_urls": [
                "https://www.mdsquaredpropertygroup.com/michaelmintz/",
                "https://renthistory.org/corporation/corp_report.php?corporationname=MD+Squared+Property+Group",
                "https://www.renthistory.org/network-explorer/index.php?query=MD+SQUARED+PROPERTY+GROUP&search_type=corporation",
                "https://www.homes.com/property/9-prospect-park-w-brooklyn-ny-unit-1a/s54jg6l4hhxzj/",
            ],
            "finding": (
                "A later May 19, 2026 exact public-search and Drive retry again found no new "
                "recording-ready source for the two broad-only MD Squared seeds. Searches for "
                "220 Third Avenue / 220-222 3 Avenue / 220 THIRD AVENUE CONDOMINIUM and "
                "57 Bond Street / BOND STREET LOFTS CONDOMINIUM returned MD Squared company-role "
                "context and RentHistory/HPD-derived registration-network pages that list both "
                "properties, but no company-controlled exact-property page, public role-explicit "
                "notice, real HPD ManagementCompany extract row, or independent non-HPD manager "
                "source. Daisy / 9 Prospect Park West again surfaced Homes.com listing-family "
                "support that is already represented in the current source mix. A concise Drive "
                "search found the likely `Revenue by Property - Summary` workbook family but no "
                "separate `Revenue by Building` spreadsheet result, and Sheets row reads hit the "
                "per-minute API quota before returning new property evidence."
            ),
            "qualification": (
                "This is reviewed source-acquisition history only. It adds no source-evidence "
                "template, does not prove absence from official HPD or Drive materials, does not "
                "change recording-ready/source-ready/verified counts, and should not convert "
                "RentHistory/HPD-derived or generic company-role pages into manager-proof support. "
                "The two broad-only MD Squared seeds still need a real exact non-HPD manager-proof "
                "family, a real official HPD ManagementCompany row, or another dated independent "
                "operator/outreach confirmation before strict verification can advance."
            ),
        },
        {
            "source_family": "operator_document_raw_drive_fetch_md_daisy_2026_05_19_phase4",
            "source_urls": [],
            "finding": (
                "A later May 19, 2026 direct Drive raw `.xlsx` fetch avoided the native Sheets API "
                "quota path and returned text-extracted rows from the operator-provided "
                "`Revenue by Property - Summary.xlsx` workbook. The workbook is an HPM "
                "revenue-by-property source and includes known HPM rows such as 306W 115th, "
                "42 W 120th, 506 East 119th, 324 East 112th, 36 W 138th, 342 W 56th, "
                "555 Lenox/Malcolm X, 61 Malcolm X, and 11-15 St. Nicholas, but it did not "
                "contain exact MD Squared or Daisy seed rows for 220 3 Avenue, 57 Bond Street, "
                "4 West 16 Street, or 9 Prospect Park West."
            ),
            "qualification": (
                "This is reviewed source-acquisition history only. It adds no evidence template, "
                "exposes no private Drive URL or row-level revenue data, and does not change "
                "recording-ready/source-ready/verified counts. It is not a new exact-property "
                "manager-proof source for the MD Squared or Daisy seeds."
            ),
        },
        {
            "source_family": "operator_document_exact_drive_search_md_daisy_2026_05_19_phase5",
            "source_urls": [],
            "finding": (
                "A follow-on May 19, 2026 read-only Drive pass searched exact operator-seed strings "
                "against the known `Revenue by Property - Summary` native sheet and adjacent Drive "
                "results. Sheet row searches for Bond, 220, Prospect, West 16, and 141 returned no "
                "exact MD Squared, Daisy, or remaining HPM gap row; the only 220 hit was an unrelated "
                "property row. Drive searches for MD Squared and Daisy surfaced generic shortlist or "
                "portfolio/research materials, not exact building-management proof for 220 3 Avenue, "
                "57 Bond Street, 4 West 16 Street, or 9 Prospect Park West."
            ),
            "qualification": (
                "This is reviewed source-acquisition history only. It adds no evidence template, "
                "does not prove absence outside the reviewed Drive corpus, exposes no private Drive "
                "URL or row-level revenue data, and does not change recording-ready, source-ready, "
                "verified, or business-use counts. The two broad-only MD Squared seeds still need "
                "one exact non-HPD manager-proof source, a real HPD ManagementCompany row, or a "
                "dated independent operator/outreach confirmation."
            ),
        },
        {
            "source_family": "public_web_adjacent_md_squared_building_clue_2026_05_19",
            "source_urls": [
                "https://www.nyc.gov/assets/hpd/downloads/pdfs/services/AEP-city-council-report-round8.pdf",
                "https://reports.displacementalert.org/oct20/109.html",
                "https://renthistory.org/corporation/corp_report.php?corporationname=MD+Squared+Property+Group",
            ],
            "finding": (
                "A later May 19, 2026 exact public-search pass found additional MD Squared "
                "property-context clues in West Harlem: a public HPD AEP city-council report names "
                "MD SQUARED PROPERTY GROUP, LLC on 572 WEST 141 STREET, displacement-report "
                "snapshots name MD Squared on 572 WEST 141 STREET and 613 WEST 140 STREET, and "
                "RentHistory still lists the target 220-222 3 Avenue and 57 Bond Street properties "
                "under MD Squared's HPD-registration-derived corporation report."
            ),
            "qualification": (
                "This is reviewed source-acquisition history only. The adjacent Harlem rows are not "
                "current local MD Squared relationship claims, the public-report/owner fields are "
                "role-ambiguous, and RentHistory remains HPD-registration-derived. Do not convert "
                "these clues into source evidence, source-ready counts, verified candidates, or "
                "business-use permission without an exact-property role-specific manager source or "
                "a dated independent outreach/operator confirmation."
            ),
        },
        {
            "source_family": "public_web_exact_gap_retry_md_daisy_2026_05_19_phase6",
            "source_urls": [
                "https://renthistory.org/corporation/corp_report.php?corporationname=MD+Squared+Property+Group",
                "https://www.redfin.com/NY/Brooklyn/9-Prospect-Park-W-11215/unit-1A/home/147153073",
            ],
            "finding": (
                "A restart-continuation exact public-web retry on May 19, 2026 searched the top "
                "operator/source-acquisition gaps again. RentHistory's MD Squared corporation "
                "report lists the target 57 Bond Street, 4 West 16 Street, and 220-222 3 Avenue "
                "properties, but that remains HPD-registration-derived corporation-network "
                "context. Redfin's active 9 Prospect Park West Unit 1A listing names Daisy "
                "Property Management and a manager phone in the HOA section, but that Daisy "
                "real-estate-listing family is already represented in the existing strict "
                "operator packet."
            ),
            "qualification": (
                "This is reviewed source-acquisition history only. It adds no evidence template, "
                "does not create or approve the two broad-only MD Squared relationship claims, "
                "does not add a new independent Daisy source family, and does not change "
                "recording-ready, source-ready, verified, or business-use counts. The MD Squared "
                "220 3 Avenue and 57 Bond Street gaps still need one exact non-HPD manager-proof "
                "source, a real official HPD ManagementCompany row, or a dated independent "
                "operator/outreach confirmation."
            ),
        },
        {
            "source_family": "public_web_dob_now_md2_57_bond_clue_2026_05_19",
            "source_urls": [
                "https://www.pincusco.com/property/57b-bond-street-condominium/",
                "https://catalog.data.gov/dataset/dob-now-build-job-application-filings",
            ],
            "finding": (
                "A restart-continuation exact public-search pass surfaced a search-index clue that "
                "PincusCo had associated Md2 Property Group with an alteration-plan entry for 57B "
                "Bond Street / BBL 1005297507. The live PincusCo page available to this runtime "
                "confirms the exact property and BBL, but it no longer exposed the MD2 alteration "
                "line in the retrieved page text. The official DOB NOW Build job-application "
                "filings dataset was identified as the likely primary-source path for this clue, "
                "but direct shell access to NYC Open Data from this runtime still failed with an "
                "unable-to-connect error."
            ),
            "qualification": (
                "This is a primary-source acquisition clue only. An alteration filing is role-ambiguous "
                "and does not state current property management, the live page text did not provide a "
                "stable MD2 manager statement, and the official DOB NOW rows were not retrieved. Do not "
                "convert this clue into source evidence, a contradiction, source-ready overlap, a "
                "verified candidate, or business-use permission without the official filing row or "
                "another exact-property role-specific manager source."
            ),
        },
        {
            "source_family": "live_dob_now_md2_57_bond_official_query_2026_06_01",
            "source_urls": [
                "https://data.cityofnewyork.us/d/w9ak-ipjd",
                "https://data.cityofnewyork.us/resource/w9ak-ipjd.json",
            ],
            "finding": (
                "A June 1, 2026 read-only live official DOB NOW query resolved the prior 57 Bond "
                "Md2/PincusCo source-acquisition clue against dataset w9ak-ipjd. The exact target "
                "property query for 57 BOND STREET / BBL 1005297507 returned 11 DOB NOW rows, and "
                "the party query for MD2 / MD SQUARED terms returned 9 DOB NOW rows on other "
                "properties, but the combined exact target-plus-party query returned 0 rows. "
                "Sample target rows named other applicant/owner parties such as Guth DeConzo, "
                "PACS Architecture, HSL Architect, and RIP Construction Consultants; sample "
                "party-only rows were for 595 Madison Avenue and 205 West 95 Street."
            ),
            "qualification": (
                "This is official reviewed source-acquisition history only. It resolves this DOB "
                "NOW path as a non-bridge for the current MD Squared / 57 Bond manager claim: no "
                "source-evidence template, contradiction, source-ready overlap, verified candidate, "
                "or business-use permission can be created from the DOB NOW result. DOB applicant, "
                "owner, filing-representative, and work-description rows remain role-ambiguous for "
                "property management even if a future target-party match is found."
            ),
        },
        {
            "source_family": "operator_document_native_sheet_retry_md_daisy_2026_05_19_phase7",
            "source_urls": [],
            "finding": (
                "A same-day continuation used the Google Drive connector to re-ground the user-mentioned "
                "revenue workbook. Concise Drive searches again found the native `Revenue by Property - "
                "Summary` sheet and `.xlsx` copies, plus unrelated revenue/cash-flow files, but no distinct "
                "`Revenue by Building` spreadsheet. The native sheet has one `Summary` tab. Exact row searches "
                "for 220, Bond, West 16, Prospect, Third, and 141 returned no manager-proof rows for "
                "220 3 Avenue, 57 Bond Street, 4 West 16 Street, 9 Prospect Park West, or 141 West 123 Street; "
                "the 220 hit was an unrelated property row. A 56th search reconfirmed the known HPM "
                "`56th 342 W - Coop` row already represented in the approved strict-HPM packet."
            ),
            "qualification": (
                "This is reviewed source-acquisition history only. It exposes no private Drive URL or row-level "
                "revenue data, adds no source-evidence template, does not prove absence outside the reviewed "
                "Drive corpus, and does not change recording-ready, source-ready, verified, or business-use "
                "counts. The broad-only MD Squared rows still need exact non-HPD manager proof, a real official "
                "HPD ManagementCompany row, or a dated independent operator/outreach confirmation."
            ),
        },
    ]


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item or "").strip()]
    return [str(value)] if str(value or "").strip() else []


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _fact_key(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "subject_type": row.get("subject_type"),
        "subject_id": row.get("subject_id"),
        "predicate": row.get("predicate"),
        "object_type": row.get("object_type"),
        "object_id": row.get("object_id"),
        "normalized_value": row.get("normalized_value"),
        "claim_type": row.get("claim_type"),
    }


def adjudicate_fact_group(row: dict[str, Any]) -> dict[str, Any]:
    supporting_sources = _as_list(row.get("supporting_sources"))
    contradicting_sources = _as_list(row.get("contradicting_sources"))
    supporting_source_count = len(set(supporting_sources))
    supporting_evidence_count = _as_int(row.get("supporting_evidence_count"))
    contradicting_evidence_count = _as_int(row.get("contradicting_evidence_count"))
    freshest = row.get("freshest_observed_freshness_days")
    freshest_days = None if freshest is None else _as_int(freshest, default=9999)
    existing_statuses = _as_list(row.get("existing_belief_statuses"))
    claim_type = str(row.get("claim_type") or "unknown")

    recomputed = compute_confidence(ConfidenceInput(
        claim_type=claim_type,
        supporting_sources=supporting_sources,
        contradicting_sources=contradicting_sources,
        freshness_days=freshest_days,
        source_agreement_count=supporting_evidence_count,
        source_disagreement_count=contradicting_evidence_count,
    ))
    recomputed_score = _as_float(recomputed.get("confidence_score"))
    score_gap_to_verified = round(max(0.0, VERIFIED_CONFIDENCE_THRESHOLD - recomputed_score), 3)
    proposed_status = str(recomputed["belief_status"])
    proposed_actionability = str(recomputed["actionability_level"])

    blockers: list[str] = []
    if contradicting_evidence_count > 0:
        blockers.append("contradicting_evidence")
    if supporting_source_count < VERIFICATION_MIN_SUPPORTING_SOURCES:
        blockers.append("needs_independent_source")
    if supporting_evidence_count < VERIFICATION_MIN_SUPPORTING_EVIDENCE:
        blockers.append("needs_additional_evidence")
    if freshest_days is None:
        blockers.append("missing_observed_date")
    elif freshest_days > VERIFICATION_MAX_FRESHNESS_DAYS:
        blockers.append("stale_evidence")
    if proposed_status != "verified":
        blockers.append("confidence_below_verified_threshold")

    safe_to_mark_verified = not blockers
    recommended_queue = "safe_auto_accept" if safe_to_mark_verified else "needs_human_review"
    if "contradicting_evidence" in blockers:
        recommended_queue = "conflicting_evidence"
    elif "needs_independent_source" in blockers or "needs_additional_evidence" in blockers:
        recommended_queue = "insufficient_evidence"

    return {
        "fact_key": _fact_key(row),
        "claim_ids": _as_list(row.get("claim_ids")),
        "evidence_ids": _as_list(row.get("evidence_ids")),
        "supporting_sources": supporting_sources,
        "contradicting_sources": contradicting_sources,
        "supporting_source_count": supporting_source_count,
        "supporting_evidence_count": supporting_evidence_count,
        "contradicting_evidence_count": contradicting_evidence_count,
        "existing_belief_statuses": existing_statuses,
        "current_max_confidence_score": round(_as_float(row.get("max_confidence_score")), 3),
        "recomputed_confidence_score": recomputed["confidence_score"],
        "proposed_confidence": recomputed["confidence_score"],
        "verified_confidence_threshold": VERIFIED_CONFIDENCE_THRESHOLD,
        "score_gap_to_verified": score_gap_to_verified,
        "proposed_belief_status": proposed_status,
        "proposed_actionability_level": proposed_actionability,
        "freshest_observed_freshness_days": freshest_days,
        "oldest_observed_freshness_days": None if row.get("oldest_observed_freshness_days") is None else _as_int(row.get("oldest_observed_freshness_days")),
        "safe_to_mark_verified": safe_to_mark_verified,
        "recommended_queue": recommended_queue,
        "blockers": blockers,
        "confidence_rationale": recomputed.get("rationale") or {},
        "rationale": {
            "confidence_policy_version": CONFIDENCE_POLICY_VERSION,
            "verification_min_confidence_score": VERIFIED_CONFIDENCE_THRESHOLD,
            "score_gap_to_verified": score_gap_to_verified,
            "verification_min_supporting_sources": VERIFICATION_MIN_SUPPORTING_SOURCES,
            "verification_min_supporting_evidence": VERIFICATION_MIN_SUPPORTING_EVIDENCE,
            "verification_max_freshness_days": VERIFICATION_MAX_FRESHNESS_DAYS,
            "why": (
                "Independent, fresh, non-contradicted evidence meets the verified threshold."
                if safe_to_mark_verified
                else "Do not mark verified until blockers are resolved."
            ),
        },
    }


def summarize_adjudication_source_coverage(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Explain whether sampled fact groups have enough independent source overlap."""
    source_counts: dict[str, int] = {}
    source_count_distribution: dict[str, int] = {}
    max_supporting_source_count = 0
    max_supporting_evidence_count = 0
    single_source_fact_group_count = 0
    multi_source_fact_group_count = 0
    zero_source_fact_group_count = 0
    for candidate in candidates:
        supporting_sources = list(dict.fromkeys(candidate.get("supporting_sources") or []))
        supporting_source_count = int(candidate.get("supporting_source_count") or len(supporting_sources))
        supporting_evidence_count = int(candidate.get("supporting_evidence_count") or 0)
        source_count_distribution[str(supporting_source_count)] = source_count_distribution.get(str(supporting_source_count), 0) + 1
        max_supporting_source_count = max(max_supporting_source_count, supporting_source_count)
        max_supporting_evidence_count = max(max_supporting_evidence_count, supporting_evidence_count)
        if supporting_source_count == 0:
            zero_source_fact_group_count += 1
        elif supporting_source_count == 1:
            single_source_fact_group_count += 1
        else:
            multi_source_fact_group_count += 1
        for source in supporting_sources:
            source_counts[source] = source_counts.get(source, 0) + 1

    top_sources = [
        {"source_name": source, "fact_group_count": count}
        for source, count in sorted(source_counts.items(), key=lambda item: (-item[1], item[0]))[:10]
    ]
    sampled_count = len(candidates)
    return {
        "sampled_fact_group_count": sampled_count,
        "zero_source_fact_group_count": zero_source_fact_group_count,
        "single_source_fact_group_count": single_source_fact_group_count,
        "multi_source_fact_group_count": multi_source_fact_group_count,
        "max_supporting_source_count": max_supporting_source_count,
        "max_supporting_evidence_count": max_supporting_evidence_count,
        "source_count_distribution": source_count_distribution,
        "top_sources": top_sources,
        "verification_blocker": (
            "No sampled fact group has independent supporting sources."
            if sampled_count and multi_source_fact_group_count == 0
            else None
        ),
    }


def _suggested_sources_for_fact(candidate: dict[str, Any]) -> list[str]:
    fact_key = candidate.get("fact_key") or {}
    claim_type = str(fact_key.get("claim_type") or "")
    predicate = str(fact_key.get("predicate") or "")
    if claim_type == "building_management" or predicate == "manages_building":
        return ["hpd_management_company", "company_website", "outreach_confirmed", "ny_dos", "ny_dps_order_entry"]
    if claim_type == "building_ownership" or predicate == "owns_building":
        return ["acris", "hpd_contacts", "ny_dos", "outreach_confirmed"]
    if claim_type in {"person_contact", "contact_path"} or "contact" in predicate:
        return ["company_website", "hunter", "outreach_confirmed", "operator_review"]
    if claim_type == "mailing_address" or "address" in predicate:
        return ["ny_dos", "hpd_contacts", "operator_review"]
    return ["manual_evidence", "operator_review", "outreach_confirmed"]


def _manual_evidence_template(candidate: dict[str, Any], suggested_source: str) -> dict[str, Any]:
    fact_key = candidate.get("fact_key") or {}
    source_specific_names = {
        "company_website",
        "hpd_management_company",
        "ny_dos",
        "ny_dps_order_entry",
        "operator_review",
        "outreach_confirmed",
    }
    source_record_bits = [
        "verification-gap",
        fact_key.get("subject_type"),
        fact_key.get("subject_id"),
        fact_key.get("predicate"),
        fact_key.get("object_id") or fact_key.get("normalized_value"),
        suggested_source,
    ]
    source_record_id = ":".join(str(bit) for bit in source_record_bits if str(bit or "").strip())[:120]
    return {
        "subject_type": fact_key.get("subject_type"),
        "subject_id": fact_key.get("subject_id"),
        "predicate": fact_key.get("predicate"),
        "object_type": fact_key.get("object_type"),
        "object_id": fact_key.get("object_id"),
        "claim_type": fact_key.get("claim_type"),
        "normalized_value": fact_key.get("normalized_value"),
        "support_status": "supports",
        "source_name": suggested_source if suggested_source in source_specific_names else "manual_evidence",
        "source_type": suggested_source,
        "source_record_id": source_record_id,
        "recording_ready": False,
        "required_before_execution": [
            "reviewed_source_record_or_url",
            "operator_verification_of_exact_property_and_role",
            "explicit_dry_run_false_confirm_execute_approval",
        ],
        "note": "Preview template only. Attach source URL/record and execute only after operator review.",
    }


def _confidence_simulation_source_name(suggested_source: str) -> str:
    """Map source-family acquisition ideas onto the source_name used by scoring."""
    return suggested_source


def _simulate_quality_upgrade(candidate: dict[str, Any], suggested_source: str) -> dict[str, Any]:
    current_sources = list(dict.fromkeys(candidate.get("supporting_sources") or []))
    simulated_source_name = _confidence_simulation_source_name(suggested_source)
    simulated_sources = list(current_sources)
    simulated_source_already_present = simulated_source_name in simulated_sources
    if not simulated_source_already_present:
        simulated_sources.append(simulated_source_name)
    simulated_evidence_count = _as_int(
        candidate.get("supporting_evidence_count"),
        default=len(current_sources),
    ) + 1
    fact_key = candidate.get("fact_key") or {}
    simulated = compute_confidence(ConfidenceInput(
        claim_type=str(fact_key.get("claim_type") or "unknown"),
        supporting_sources=simulated_sources,
        contradicting_sources=_as_list(candidate.get("contradicting_sources")),
        freshness_days=candidate.get("freshest_observed_freshness_days"),
        source_agreement_count=simulated_evidence_count,
        source_disagreement_count=_as_int(candidate.get("contradicting_evidence_count")),
    ))
    simulated_score = _as_float(simulated.get("confidence_score"))
    score_gap = round(max(0.0, VERIFIED_CONFIDENCE_THRESHOLD - simulated_score), 3)
    would_reach_threshold = simulated_score >= VERIFIED_CONFIDENCE_THRESHOLD
    return {
        "suggested_source": suggested_source,
        "simulated_supporting_source_name": simulated_source_name,
        "simulated_source_already_present": simulated_source_already_present,
        "source_quality_score": source_quality(simulated_source_name),
        "simulated_supporting_source_count": len(set(simulated_sources)),
        "simulated_supporting_evidence_count": simulated_evidence_count,
        "simulated_confidence_score": simulated.get("confidence_score"),
        "simulated_belief_status": simulated.get("belief_status"),
        "simulated_actionability_level": simulated.get("actionability_level"),
        "score_gap_to_verified": score_gap,
        "would_reach_verified_threshold": would_reach_threshold,
        "safe_action": (
            "This one-source upgrade would clear the confidence threshold, but recording evidence "
            "and changing status still require explicit approval plus adjudication."
            if would_reach_threshold
            else "This one-source upgrade would improve confidence but still leave the fact below verified."
        ),
    }


def _simulate_quality_upgrade_bundle(candidate: dict[str, Any], suggested_sources: list[str]) -> dict[str, Any]:
    current_sources = list(dict.fromkeys(candidate.get("supporting_sources") or []))
    simulated_sources = list(current_sources)
    simulated_source_names: list[str] = []
    for source in suggested_sources:
        simulated_source_name = _confidence_simulation_source_name(source)
        simulated_source_names.append(simulated_source_name)
        if simulated_source_name not in simulated_sources:
            simulated_sources.append(simulated_source_name)
    simulated_evidence_count = _as_int(
        candidate.get("supporting_evidence_count"),
        default=len(current_sources),
    ) + len(suggested_sources)
    fact_key = candidate.get("fact_key") or {}
    simulated = compute_confidence(ConfidenceInput(
        claim_type=str(fact_key.get("claim_type") or "unknown"),
        supporting_sources=simulated_sources,
        contradicting_sources=_as_list(candidate.get("contradicting_sources")),
        freshness_days=candidate.get("freshest_observed_freshness_days"),
        source_agreement_count=simulated_evidence_count,
        source_disagreement_count=_as_int(candidate.get("contradicting_evidence_count")),
    ))
    simulated_score = _as_float(simulated.get("confidence_score"))
    score_gap = round(max(0.0, VERIFIED_CONFIDENCE_THRESHOLD - simulated_score), 3)
    would_reach_threshold = simulated_score >= VERIFIED_CONFIDENCE_THRESHOLD
    return {
        "suggested_sources": suggested_sources,
        "simulated_supporting_source_names": list(dict.fromkeys(simulated_source_names)),
        "simulated_supporting_source_count": len(set(simulated_sources)),
        "simulated_supporting_evidence_count": simulated_evidence_count,
        "simulated_confidence_score": simulated.get("confidence_score"),
        "simulated_belief_status": simulated.get("belief_status"),
        "simulated_actionability_level": simulated.get("actionability_level"),
        "score_gap_to_verified": score_gap,
        "would_reach_verified_threshold": would_reach_threshold,
        "acquisition_required": True,
        "recording_ready": False,
        "approval_required_before_recording": True,
        "required_real_evidence": [
            {
                "suggested_source": source,
                "simulated_supporting_source_name": _confidence_simulation_source_name(source),
                "required_fields": [
                    "source_record_id",
                    "source_url_or_local_record_reference",
                    "observed_at",
                    "exact_property_match",
                    "role_specific_management_support",
                ],
            }
            for source in suggested_sources
        ],
        "safe_action": (
            "This suggested bundle would clear the confidence threshold, but recording evidence "
            "and changing status still require explicit approval plus adjudication."
            if would_reach_threshold
            else "Even this suggested bundle would leave the fact below verified under the current policy."
        ),
    }


def build_verification_gap_plan(candidates: list[dict[str, Any]], *, limit: int = 10) -> dict[str, Any]:
    """Build read-only proposals for obtaining the missing independent evidence."""
    proposals: list[dict[str, Any]] = []
    for candidate in candidates:
        blockers = set(candidate.get("blockers") or [])
        if candidate.get("safe_to_mark_verified") or "needs_independent_source" not in blockers:
            continue
        current_sources = list(dict.fromkeys(candidate.get("supporting_sources") or []))
        suggested_sources = [source for source in _suggested_sources_for_fact(candidate) if source not in current_sources]
        if not suggested_sources:
            suggested_sources = ["manual_evidence"]
        missing_source_count = max(0, VERIFICATION_MIN_SUPPORTING_SOURCES - int(candidate.get("supporting_source_count") or 0))
        missing_evidence_count = max(0, VERIFICATION_MIN_SUPPORTING_EVIDENCE - int(candidate.get("supporting_evidence_count") or 0))
        proposals.append({
            "fact_key": candidate.get("fact_key"),
            "current_sources": current_sources,
            "current_supporting_evidence_count": candidate.get("supporting_evidence_count"),
            "missing_source_count": missing_source_count,
            "missing_evidence_count": missing_evidence_count,
            "suggested_sources": suggested_sources[:4],
            "recommended_queue": "needs_human_review",
            "safe_action": "Collect or record independent evidence before adjudication; do not mark verified from this proposal alone.",
            "manual_evidence_template": _manual_evidence_template(candidate, suggested_sources[0]),
        })
        if len(proposals) >= limit:
            break

    return {
        "dry_run": True,
        "mutations_planned": 0,
        "proposal_count": len(proposals),
        "policy": {
            "min_independent_supporting_sources": VERIFICATION_MIN_SUPPORTING_SOURCES,
            "min_supporting_evidence": VERIFICATION_MIN_SUPPORTING_EVIDENCE,
            "execution_policy": "Read-only acquisition plan. Manual evidence capture still requires preview plus confirm_execute=true.",
        },
        "proposals": proposals,
    }


def build_verified_confidence_gap_plan(candidates: list[dict[str, Any]], *, limit: int = 10) -> dict[str, Any]:
    """Explain source-ready fact groups that still miss the verified confidence threshold."""
    proposals: list[dict[str, Any]] = []
    for candidate in candidates:
        blockers = set(candidate.get("blockers") or [])
        if candidate.get("safe_to_mark_verified") or "confidence_below_verified_threshold" not in blockers:
            continue
        if blockers.intersection({
            "needs_independent_source",
            "needs_additional_evidence",
            "contradicting_evidence",
            "stale_evidence",
            "missing_observed_date",
        }):
            continue
        current_sources = list(dict.fromkeys(candidate.get("supporting_sources") or []))
        suggested_sources = [source for source in _suggested_sources_for_fact(candidate) if source not in current_sources]
        if not suggested_sources:
            suggested_sources = ["outreach_confirmed", "operator_review"]
        rationale = candidate.get("confidence_rationale") or {}
        simulated_quality_upgrades = sorted(
            (
                _simulate_quality_upgrade(candidate, source)
                for source in suggested_sources[:5]
            ),
            key=lambda item: (-_as_float(item.get("simulated_confidence_score")), item["suggested_source"]),
        )
        best_single_source_upgrade = simulated_quality_upgrades[0] if simulated_quality_upgrades else None
        simulated_quality_bundle_upgrade = _simulate_quality_upgrade_bundle(candidate, suggested_sources[:5])
        proposals.append({
            "fact_key": candidate.get("fact_key"),
            "current_sources": current_sources,
            "supporting_source_count": candidate.get("supporting_source_count"),
            "supporting_evidence_count": candidate.get("supporting_evidence_count"),
            "recomputed_confidence_score": candidate.get("recomputed_confidence_score"),
            "verified_confidence_threshold": candidate.get("verified_confidence_threshold"),
            "score_gap_to_verified": candidate.get("score_gap_to_verified"),
            "average_supporting_source_quality": rationale.get("average_supporting_source_quality"),
            "raw_confidence_before_smoothing": rationale.get("raw_confidence_before_smoothing"),
            "source_quality_scores": rationale.get("source_quality_scores") or [],
            "suggested_quality_upgrade_sources": suggested_sources[:4],
            "simulated_quality_upgrades": simulated_quality_upgrades,
            "best_single_source_upgrade": best_single_source_upgrade,
            "simulated_quality_bundle_upgrade": simulated_quality_bundle_upgrade,
            "single_source_upgrade_would_verify": any(
                upgrade.get("would_reach_verified_threshold") is True
                for upgrade in simulated_quality_upgrades
            ),
            "recommended_queue": "needs_human_review",
            "safe_action": (
                "This fact is source-ready but not verified. Acquire stronger role-explicit, fresh evidence "
                "such as outreach-confirmed, HPD ManagementCompany, company website, NY DOS manager-role, "
                "or NY DPS order-entry support before marking verified."
            ),
            "manual_evidence_template": _manual_evidence_template(candidate, suggested_sources[0]),
        })
        if len(proposals) >= limit:
            break

    best_upgrades = [
        proposal["best_single_source_upgrade"]
        for proposal in proposals
        if proposal.get("best_single_source_upgrade")
    ]
    best_single_source_upgrade_overall = (
        max(
            best_upgrades,
            key=lambda item: _as_float(item.get("simulated_confidence_score")),
        )
        if best_upgrades
        else None
    )
    bundle_upgrades = [
        proposal["simulated_quality_bundle_upgrade"]
        for proposal in proposals
        if proposal.get("simulated_quality_bundle_upgrade")
    ]
    best_bundle_upgrade_overall = (
        max(
            bundle_upgrades,
            key=lambda item: _as_float(item.get("simulated_confidence_score")),
        )
        if bundle_upgrades
        else None
    )
    return {
        "dry_run": True,
        "mutations_planned": 0,
        "proposal_count": len(proposals),
        "single_source_upgrade_would_verify_count": sum(
            1
            for proposal in proposals
            if proposal.get("single_source_upgrade_would_verify") is True
        ),
        "best_single_source_upgrade_overall": best_single_source_upgrade_overall,
        "bundle_upgrade_would_verify_count": sum(
            1
            for proposal in proposals
            if (
                proposal.get("simulated_quality_bundle_upgrade") or {}
            ).get("would_reach_verified_threshold") is True
        ),
        "best_bundle_upgrade_overall": best_bundle_upgrade_overall,
        "policy": {
            "verified_confidence_threshold": VERIFIED_CONFIDENCE_THRESHOLD,
            "single_source_upgrade_simulation": (
                "Each simulation adds one fresh supporting evidence row from the suggested source, "
                "keeps the current lower-quality sources in the fact group, and does not lower thresholds."
            ),
            "execution_policy": "Read-only confidence-gap plan. It does not mark facts verified or record evidence.",
        },
        "proposals": proposals,
    }


def _normalize_street_address(value: Any) -> str:
    cleaned = str(value or "").upper()
    replacements = {
        ".": " ",
        ",": " ",
    }
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    return " ".join(cleaned.split())


def _manager_evidence_template(candidate: dict[str, Any], match: dict[str, Any]) -> dict[str, Any]:
    return {
        "subject_type": "lead",
        "subject_id": match.get("lead_id"),
        "predicate": "manages_building",
        "object_type": "building",
        "object_id": match.get("bbl"),
        "claim_type": "building_management",
        "normalized_value": "manager",
        "extracted_value": candidate.get("manager_name"),
        "support_status": "supports",
        "source_name": candidate.get("source_name"),
        "source_type": candidate.get("source_type"),
        "source_record_id": candidate.get("source_record_id"),
        "source_url": candidate.get("source_url"),
        "observed_at": candidate.get("observed_at"),
        "note": (
            "Preview template only. Operator must inspect the cited document and confirm the "
            "address/role before recording manual evidence."
        ),
        "raw_payload": {
            "external_evidence_preview": True,
            "source_family": candidate.get("source_family"),
            "candidate_id": candidate.get("candidate_id"),
            "candidate_status": candidate.get("candidate_status"),
            "identity_evidence_urls": candidate.get("identity_evidence_urls") or [],
            "external_address": candidate.get("external_address"),
            "local_address": match.get("address"),
            "external_owner": candidate.get("external_owner"),
            "manager_contact_name": candidate.get("manager_contact_name"),
            "evidence_role": candidate.get("evidence_role"),
            "evidence_summary": candidate.get("evidence_summary"),
            "source_document_title": candidate.get("source_document_title"),
            "source_document_row_number": candidate.get("source_document_row_number"),
        },
    }


def _operator_confirmation_template(
    candidate: dict[str, Any],
    building: dict[str, Any],
    lead: dict[str, Any],
    *,
    support_status: str = "supports",
    contradicted_claim_id: str | None = None,
) -> dict[str, Any]:
    source_record_id = str(candidate.get("source_record_id") or candidate.get("candidate_id") or "")
    if support_status == "contradicts" and contradicted_claim_id:
        source_record_id = f"{source_record_id}:contradicts:{contradicted_claim_id}"
    return {
        "subject_type": "lead",
        "subject_id": lead.get("lead_id"),
        "predicate": "manages_building",
        "object_type": "building",
        "object_id": building.get("bbl"),
        "claim_type": "building_management",
        "normalized_value": "manager",
        "extracted_value": lead.get("company_name") or lead.get("normalized_name") or candidate.get("manager_name"),
        "support_status": support_status,
        "source_name": "outreach_confirmed",
        "source_type": "operator_first_hand_confirmation",
        "source_record_id": source_record_id,
        "source_url": None,
        "observed_at": candidate.get("observed_at"),
        "note": (
            "Preview template only. First-hand/operator-confirmed evidence supplied by the user; "
            "record only after explicit approval and then acquire a second independent source."
        ),
        "raw_payload": {
            "operator_confirmed_preview": True,
            "source_family": "operator_confirmed",
            "candidate_id": candidate.get("candidate_id"),
            "user_address": candidate.get("user_address"),
            "canonical_address": building.get("address"),
            "manager_name_supplied": candidate.get("manager_name"),
            "matched_lead_name": lead.get("company_name") or lead.get("normalized_name"),
            "contradicted_claim_id": contradicted_claim_id,
            "evidence_summary": (
                f"User supplied first-hand evidence that {candidate.get('user_address')} "
                f"is currently managed by {candidate.get('manager_name')}."
            ),
        },
    }


def _operator_second_source_template(
    candidate: dict[str, Any],
    source_candidate: dict[str, Any],
    building: dict[str, Any],
    lead: dict[str, Any],
) -> dict[str, Any]:
    return {
        "subject_type": "lead",
        "subject_id": lead.get("lead_id"),
        "predicate": "manages_building",
        "object_type": "building",
        "object_id": building.get("bbl"),
        "claim_type": "building_management",
        "normalized_value": "manager",
        "extracted_value": lead.get("company_name") or lead.get("normalized_name") or candidate.get("manager_name"),
        "support_status": "supports",
        "source_name": source_candidate.get("source_name"),
        "source_type": source_candidate.get("source_type"),
        "source_record_id": source_candidate.get("source_record_id"),
        "source_url": source_candidate.get("source_url"),
        "observed_at": source_candidate.get("observed_at"),
        "note": (
            "Preview template only. Operator must inspect the cited exact-property source before "
            "recording it as second-source support."
        ),
        "raw_payload": {
            "operator_second_source_preview": True,
            "source_family": source_candidate.get("source_family"),
            "candidate_id": source_candidate.get("candidate_id"),
            "operator_candidate_id": source_candidate.get("operator_candidate_id"),
            "candidate_status": source_candidate.get("candidate_status"),
            "user_address": candidate.get("user_address"),
            "external_address": source_candidate.get("external_address"),
            "canonical_address": building.get("address"),
            "manager_name_supplied": candidate.get("manager_name"),
            "matched_lead_name": lead.get("company_name") or lead.get("normalized_name"),
            "evidence_role": source_candidate.get("evidence_role"),
            "evidence_summary": source_candidate.get("evidence_summary"),
            "strict_manager_proof": source_candidate.get("source_family") not in NON_MANAGER_PROOF_SOURCE_FAMILIES,
        },
    }


def _operator_source_search_queries(manager_name: Any, address: Any) -> list[str]:
    normalized_address = _normalize_street_address(address)
    manager = str(manager_name or "").strip()
    if not normalized_address or not manager:
        return []
    title_address = normalized_address.title()
    return [
        f'"{manager}" "{title_address}"',
        f'"{manager}" "{title_address}" "property manager"',
        f'"{title_address}" "managed by" "{manager}"',
        f'site:openigloo.com "{manager}" "{title_address}"',
        f'site:streeteasy.com "{title_address}" "{manager}"',
    ]


def _operator_source_targets(manager_name: Any, address: Any) -> list[dict[str, str]]:
    normalized_address = _normalize_street_address(address)
    manager = str(manager_name or "").strip()
    return [
        {
            "source_family": "company_website",
            "evidence_needed": (
                f"Find a {manager}-controlled page, portal notice, client list, or building notice that "
                f"names {normalized_address} as a managed property."
            ),
        },
        {
            "source_family": "external_web_profile",
            "evidence_needed": (
                f"Find an exact-property profile, listing, or resident page that names {manager} as manager "
                f"for {normalized_address}; company-only profile evidence is not enough."
            ),
        },
        {
            "source_family": "hpd_management_company",
            "evidence_needed": (
                f"Find a role-specific HPD ManagementCompany row for {normalized_address}; HPD Agent, "
                "SiteManager, CorporateOwner, or HeadOfficer roles stay separate."
            ),
        },
        {
            "source_family": "ny_dos",
            "evidence_needed": (
                f"Use NY DOS only if the record explicitly supports a management or managing-agent "
                f"relationship for {normalized_address}; service-of-process alone is not enough."
            ),
        },
        {
            "source_family": "outreach_confirmed",
            "evidence_needed": (
                f"Record only a dated second operator/outreach confirmation that independently confirms "
                f"{manager} manages {normalized_address}; reuse of the same first-hand note is duplicate "
                "freshness context, not a new independent source."
            ),
        },
    ]


def _operator_manager_proof_gap(
    *,
    candidate_source_ready: bool,
    candidate_strict_ready: bool,
    supporting_source_families: list[str],
    manager_proof_source_families: list[str],
) -> dict[str, Any]:
    missing_count = max(0, VERIFICATION_MIN_SUPPORTING_SOURCES - len(manager_proof_source_families))
    if candidate_strict_ready:
        return {
            "strict_manager_gap_status": "strict_manager_proof_ready_if_recorded",
            "strict_manager_gap_reason": (
                "Strict manager-proof overlap exists if approved because the preview has at least "
                f"{VERIFICATION_MIN_SUPPORTING_SOURCES} manager-proof source families."
            ),
            "missing_manager_proof_source_family_count": missing_count,
            "next_required_manager_proof": (
                "Review templates, record only after explicit approval, then rerun adjudication."
            ),
        }
    if candidate_source_ready and any(family in NON_MANAGER_PROOF_SOURCE_FAMILIES for family in supporting_source_families):
        return {
            "strict_manager_gap_status": "broad_source_ready_not_strict",
            "strict_manager_gap_reason": (
                "Broad source-ready only: the preview has multiple source families, but at least one "
                "second source is HPD-registration-derived and excluded from strict manager-proof counts."
            ),
            "missing_manager_proof_source_family_count": missing_count,
            "next_required_manager_proof": (
                "Acquire one exact non-HPD manager-proof source family, such as a company-controlled "
                "exact-property page, external exact-property manager profile, HPD ManagementCompany row, "
                "role-explicit NY DOS/legal record, public notice, or dated second outreach confirmation."
            ),
        }
    return {
        "strict_manager_gap_status": "single_source_only",
        "strict_manager_gap_reason": (
            "Single-source manager proof only: operator confirmation is first-hand evidence, but it "
            "does not satisfy independent source-overlap policy by itself."
        ),
        "missing_manager_proof_source_family_count": missing_count,
        "next_required_manager_proof": (
            "Acquire an independent exact-property source before recording any verified or business-use "
            "relationship claim."
        ),
    }


def _recordable_manager_external_templates(grouped_claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        template
        for group in grouped_claims
        if group["source_ready_if_recorded"] and group["independent_source_ready_if_recorded"]
        for template in group["manual_evidence_templates"]
        if (template.get("raw_payload") or {}).get("candidate_status") != "address_range_review_required"
    ]


def _manager_source_search_queries(address: Any, existing_families: set[str]) -> list[str]:
    normalized_address = _normalize_street_address(address)
    if not normalized_address:
        return []
    queries = [
        f'"Harlem Property Management" "{normalized_address.title()}"',
        f'"James Simari" "{normalized_address.title()}"',
        f'site:documents.dps.ny.gov "Harlem Property Management" "{normalized_address.title()}"',
        f'site:harlempm.com "{normalized_address.title()}"',
    ]
    if "ny_dps_order_entry" in existing_families:
        queries = [query for query in queries if not query.startswith("site:documents.dps.ny.gov")]
    if "external_web_profile" in existing_families:
        queries.append(f'"{normalized_address.title()}" "managing agent" "Harlem Property"')
    return queries


def _manager_source_targets(suggested_source_families: list[str], address: Any) -> list[dict[str, str]]:
    normalized_address = _normalize_street_address(address)
    target_notes = {
        "ny_dps_order_entry": (
            "Find an exact NY DPS/PSC order-entry petition, exhibit, or notice naming Harlem "
            "Property Management as managing company, managing agent, or care-of manager for "
            f"{normalized_address}."
        ),
        "company_website": (
            "Find an HPM-owned page, portal record, client list, or building notice that names "
            f"{normalized_address}; a generic HPM service page is company-role context only."
        ),
        HPM_REVENUE_BY_PROPERTY_SOURCE_FAMILY: (
            "Use a dated first-party HPM operating document only if the row names the exact "
            f"{normalized_address} property and the operator confirms the document's provenance."
        ),
        "outreach_confirmed": (
            "Record only a dated operator-confirmed email/call outcome that confirms HPM manages "
            f"{normalized_address}."
        ),
        "ny_dos": (
            "Use NY DOS or legal-mailing records only if they explicitly state a managing-agent "
            f"or property-management role for {normalized_address}; service-of-process alone is not enough."
        ),
    }
    return [
        {"source_family": family, "evidence_needed": target_notes[family]}
        for family in suggested_source_families
        if family in target_notes
    ]


def build_manager_external_next_source_batches(grouped_claims: list[dict[str, Any]]) -> dict[str, Any]:
    """Describe the next read-only source acquisition batches for non-strict manager groups."""
    proposals: list[dict[str, Any]] = []
    source_family_counts: dict[str, int] = {}
    reviewed_source_findings = [
        {
            "source_family": "ny_dps_order_entry",
            "source_urls": [
                "https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7B01D4BD59-5170-49BB-828F-34534144E502%7D",
                "https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7BB682B3B6-F25B-47F5-B4EE-76E600EF99A0%7D",
                "https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7BCF5C1F53-F557-45AB-9DA2-BA47E386AD90%7D",
                "https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7B2912DCD1-5F78-4A21-A710-7A1E2841E9BA%7D",
                "https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7BAEA2D08D-05DF-46D4-B9F0-CB3774078968%7D",
                "https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7B304B3F37-2994-4AE2-A199-4485C050FC42%7D",
            ],
            "finding": (
                "Exact manager/care-of public-utility order evidence exists for 324 EAST 112 STREET, "
                "36 WEST 138 STREET, 204 WEST 140 STREET, and 330 WEST 145 STREET; 402 WEST 153 "
                "STREET is a source-backed new relationship candidate, not current ledger overlap."
            ),
            "qualification": (
                "Counts as manager-proof evidence when the order/notice names Harlem Property Management "
                "at the exact property, but it does not create a second independent family for a group that "
                "already has NY DPS order evidence. The older 202 WEST 140 STREET exhibit stays address-range "
                "review context because the local building row is 204 WEST 140 STREET."
            ),
        },
        {
            "source_family": "company_website",
            "source_urls": ["https://harlempm.com/"],
            "finding": (
                "The current Harlem Property Management website proves HPM is an NYC condo/co-op "
                "property-management company, but the reviewed public pages do not list the exact pilot buildings."
            ),
            "qualification": (
                "Useful for company-role context and outreach, but it cannot support a specific building "
                "relationship unless an exact property page, portal record, board notice, or client list is found."
            ),
        },
        {
            "source_family": "openigloo",
            "source_urls": [
                "https://www.openigloo.com/contact/nyc/66ec2a5f-3005-415d-9553-d5ee48f8aeec/harlem-property-management-inc/buildings"
            ],
            "finding": (
                "The Harlem Property Management profile lists exact current pilot buildings for the broad "
                "source-ready groups, including an Adam Clayton Powell/7 Avenue alias group, but no exact "
                "330 WEST 145 STREET match was found in the reviewed profile."
            ),
            "qualification": (
                "Counts as external web/profile evidence only when the building appears as an exact listed "
                "property; nearby or profile-only company evidence stays review context."
            ),
        },
        {
            "source_family": "real_estate_listing",
            "source_urls": [
                "https://www.mystatemls.com/property/2257-adam-clayton-powell-jr-blvd-3b-new-york-ny-10030/11146569/",
                "https://www.renthop.com/listings/342-w-56th-st/2d/74523503",
                "https://www.zillow.com/homedetails/342-W-56th-St-APT-2D-New-York-NY-10019/122346580_zpid/",
            ],
            "finding": (
                "MyStateMLS listing 11146569 for 2257 Adam Clayton Powell Jr Blvd #3B identifies "
                "The Ellison Condominium, lists HOA as Harlem Property Management, and gives HPM's "
                "phone number. RentHop listing 74523503 for 342 W 56th St states that a $350 "
                "application processing fee is payable to Harlem Property Management, Inc.; Zillow's "
                "off-market page for the same unit repeats that application-processing fee evidence."
            ),
            "qualification": (
                "Counts as a review-gated real-estate-listing source for the exact local 2257 Adam C "
                "Powell Boulevard relationship and the exact local 342 WEST 56 STREET relationship; "
                "RentHop and Zillow stay in the same real-estate-listing family and do not create "
                "another independent source family; do not use them to generalize HPM to nearby "
                "7 Avenue, Adam Clayton Powell, or Hell's Kitchen buildings."
            ),
        },
        {
            "source_family": "renthistory",
            "source_urls": [
                "https://renthistory.org/corporation/corp_report.php?corporationname=HARLEM+PROPERTY+MANAGEMENT+INC."
            ],
            "finding": (
                "RentHistory covers multiple exact pilot addresses, but the evidence is derived from HPD "
                "registration context."
            ),
            "qualification": (
                "Supports review and broad source overlap, but remains excluded from strict manager-proof "
                "source-family counts."
            ),
        },
        {
            "source_family": HPM_REVENUE_BY_PROPERTY_SOURCE_FAMILY,
            "source_urls": [],
            "finding": (
                "The operator-identified Google Drive sheet 'Revenue by Property - Summary' has exact "
                "property rows for 13 current HPM pilot buildings, including 11-15 St. Nicholas Avenue, "
                "36 W 138, 202/204 W 140, 2257 ACPB, 306 W 115, 324 E 112, 330 W 145, 345 Lenox, "
                "42 W 120, 506 E 119, 555 Malcolm X/Lenox, 342 W 56, and 61 Malcolm X/Lenox."
            ),
            "qualification": (
                "Counts in preview as first-party operator-document manager proof only for exact row-to-building "
                "matches. Row-level revenue amounts are intentionally not copied into truth templates, and the "
                "private Drive URL is not embedded in evidence payloads. Recording still requires explicit approval."
            ),
        },
        {
            "source_family": "third_party_company_profile",
            "source_urls": [
                "https://www.uhab.org/es/vendor/harlem-property-management/",
                "https://www.bbb.org/us/ny/new-york-/profile/property-management/harlem-property-management-inc-0121-87159510",
            ],
            "finding": (
                "UHAB and BBB profiles corroborate Harlem Property Management as a property-management "
                "company; UHAB describes HPM as managing condos, co-ops, and HDFCs in New York City "
                "and as an NYS Part 36 court-appointed property manager, while BBB lists HPM in the "
                "property-management category with Robert D. Pair as business management."
            ),
            "qualification": (
                "These profiles support company-role context and outreach targeting, but they do not "
                "name the exact pilot buildings. Do not count them as support for a specific "
                "manages_building claim unless paired with exact-property evidence."
            ),
        },
        {
            "source_family": "local_hpd_contact_role_audit",
            "source_urls": [],
            "finding": (
                "A read-only local HPD contact-role audit for the original 10 HPM non-strict audit-target groups "
                "found Agent/SiteManager/CorporateOwner/HeadOfficer-style rows but no ManagementCompany "
                "contact rows. Current local building_management rows for these groups are still role "
                "'agent'."
            ),
            "qualification": (
                "The local HPD contact table cannot currently supply the missing HPD ManagementCompany "
                "manager-proof family for these groups. Agent, SiteManager, CorporateOwner, HeadOfficer, "
                "Officer, and IndividualOwner rows remain role-specific review context."
            ),
        },
        {
            "source_family": "live_hpd_open_data_role_audit_2026_05_15",
            "source_urls": [
                "https://data.cityofnewyork.us/resource/tesw-yqqr.json",
                "https://data.cityofnewyork.us/resource/feu5-w2e2.json",
            ],
            "finding": (
                "A May 15, 2026 read-only NYC Open Data HPD registration/contact query for the "
                "original 10 HPM non-strict audit-target groups found Harlem Property Management or Harlem "
                "Property Management Inc. as Agent on each matched registration, including current "
                "2025/2026 registrations for 9 of 10 groups and a stale 2022/2023 registration for "
                "141 West 123 Street. None of the live HPD contact sets includes a ManagementCompany row."
            ),
        "qualification": (
            "This is fresh public-source confirmation that HPD can support registered-agent "
            "claims for these groups but still cannot supply the missing HPD ManagementCompany "
            "manager-proof family. Do not count HPD Agent rows as the missing second source for "
            "manages_building."
        ),
    },
    {
        "source_family": "live_hpd_open_data_catalog_unreachable_2026_05_16",
        "source_urls": [
            "https://data.cityofnewyork.us/d/tesw-yqqr",
            "https://data.cityofnewyork.us/d/feu5-w2e2",
            "https://data.cityofnewyork.us/resource/tesw-yqqr.json",
            "https://data.cityofnewyork.us/resource/feu5-w2e2.json",
        ],
        "finding": (
            "A May 16, 2026 follow-up identified the official NYC Open Data HPD source catalog: "
            "Multiple Dwelling Registrations (`tesw-yqqr`) for BBL-to-registration freshness and "
            "Registration Contacts (`feu5-w2e2`) for role-specific contacts. Direct read-only "
            "queries from this runtime still failed with an unable-to-connect socket error."
        ),
        "qualification": (
            "This is source-access state, not negative evidence. The current runtime cannot prove "
            "whether a fresh HPD ManagementCompany row exists for the remaining HPM gap groups; a "
            "successful live query, export, or operator-supplied official extract is still required "
            "before any HPD manager-proof evidence can be recorded."
        ),
    },
    {
        "source_family": "live_hpd_property_managers_first_step_view_2026_06_01",
        "source_urls": [
            "https://data.cityofnewyork.us/d/v4vh-sni9",
            "https://data.cityofnewyork.us/resource/v4vh-sni9.json",
        ],
        "finding": (
            "A June 1, 2026 read-only check inspected NYC Open Data view `v4vh-sni9`, titled "
            "`Property Managers-1st Step`. The view is community-created and based on HPD "
            "Multiple Dwelling Registrations; its live API rows expose RegistrationID, BoroID, "
            "Boro, HouseNumber, StreetName, StreetCode, Block, Lot, LastRegistrationDate, and "
            "RegistrationEndDate. It does not expose manager, agent, contact type, corporation "
            "name, or role-specific management fields."
        ),
        "qualification": (
            "This is reviewed source-acquisition boundary context only. Despite the dataset title, "
            "`v4vh-sni9` can only help locate registration rows before querying Registration Contacts; "
            "it cannot support, contradict, verify, or activate a manages_building claim without a "
            "matching role-specific `feu5-w2e2` ManagementCompany row or another exact manager-proof source."
        ),
    },
    {
        "source_family": "ny_dos_or_legal_mailing",
        "source_urls": [
            "https://www.bizprofile.net/ny/new-york/the-hamilton-owners-corporation"
        ],
            "finding": (
                "NY DOS service-of-process, c/o mailing, and litigation mailing references can tie entities "
                "to legal notice roles; a reviewed The Hamilton Owners Corp. DOS mirror lists Harlem "
                "Property Management Inc. for service-of-process mail and a 330 WEST 145 STREET officer "
                "address, but does not state HPM manages that building."
            ),
            "qualification": (
                "Do not count as property-management evidence unless the source explicitly states a "
                "property-management or managing-agent role at the exact building."
            ),
        },
        {
            "source_family": "litigation_records",
            "source_urls": [
                "https://law.justia.com/cases/new-york/other-courts/2025/2025-ny-slip-op-30666-u.html",
                "https://law.justia.com/cases/new-york/other-courts/2025/2025-ny-slip-op-33436-u.html",
            ],
            "finding": (
                "A 2025 11-15 ST NICHOLAS decision states Harlem Property Management was the "
                "receiver's property manager, and NYC/public real-estate records tie 11 and 11-15 "
                "ST NICHOLAS to local BBL 1018210025. Exact 345 LENOX litigation records name "
                "Harlem Property Management with property defendants, but do not independently "
                "state that HPM is the operating manager."
            ),
            "qualification": (
                "Counts as one manager-proof source family for 11 ST NICHOLAS, but still needs a "
                "second independent manager-specific family before it can become strict source-ready."
            ),
        },
        {
            "source_family": "address_range_or_alias_review",
            "source_urls": [],
            "finding": (
                "Address-range, alias, and nearby-property hits can point to new acquisition targets, but "
                "they are not clean exact current fact groups."
            ),
            "qualification": (
                "Keep these in operator review until the local building identity and external property "
                "identity can be reconciled without broad dedupe grouping."
            ),
        },
        {
            "source_family": "public_web_search_followup",
            "source_urls": [
                "https://law.justia.com/cases/new-york/other-courts/2025/2025-ny-slip-op-33436-u.html",
                "https://renthistory.org/network-explorer/index.php?firstname=JAMES&lastname=SIMARI&search_type=individual",
                "https://northgatereg.com/",
            ],
            "finding": (
                "Follow-up exact-property searches found non-qualifying context: 345 Lenox litigation "
                "names Harlem Property Management with condo defendants but does not state an operating-manager "
                "role; James Simari/RentHistory pages are still HPD-registration-derived; and nearby Lenox "
                "sale pages such as 339-341 Lenox are wrong-property context."
            ),
            "qualification": (
                "Do not promote these to strict manager-proof evidence. They explain why the current "
                "non-strict groups still need a new exact-property manager source or outreach-confirmed evidence."
            ),
        },
        {
            "source_family": "public_web_search_followup_hpm_batch_2",
            "source_urls": [
                "https://www.openigloo.com/contact/nyc/66ec2a5f-3005-415d-9553-d5ee48f8aeec/harlem-property-management-inc",
                "https://renthistory.org/corporation/corp_report.php?corporationname=HARLEM+PROPERTY+MANAGEMENT+INC.",
            ],
            "finding": (
                "Follow-up searches for the remaining non-strict HPM groups found OpenIgloo exact-property "
                "profile evidence for some buildings, including 306 WEST 115 STREET and 342 WEST 56 STREET, "
                "plus RentHistory HPD-derived exact-property context for 42 WEST 120 STREET, 506 EAST 119 "
                "STREET, and 61 LENOX AVENUE."
            ),
            "qualification": (
                "OpenIgloo is already the existing external_web_profile family for these non-strict groups, "
                "and RentHistory remains HPD-registration-derived. These searches did not add a second "
                "independent manager-proof family, so the next useful sources are NY DPS/PSC, exact company "
                "site/portal records, NY DOS records with explicit manager-role language, or outreach-confirmed evidence."
            ),
        },
        {
            "source_family": "public_web_search_followup_hpm_batch_3",
            "source_urls": [
                "https://www.openigloo.com/contact/nyc/66ec2a5f-3005-415d-9553-d5ee48f8aeec/harlem-property-management-inc",
                "https://renthistory.org/corporation/corp_report.php?corporationname=HARLEM+PROPERTY+MANAGEMENT+INC.",
                "https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7B01D4BD59-5170-49BB-828F-34534144E502%7D",
            ],
            "finding": (
                "A fresh search pass over 11 St Nicholas Avenue, 141 West 123 Street, 306 West 115 "
                "Street, 342 West 56 Street, 42 West 120 Street, 506 East 119 Street, 61 Lenox Avenue, "
                "and 330 West 145 Street found no new qualifying strict manager-proof source family. "
                "Results repeated OpenIgloo existing-family evidence, RentHistory/HPD-derived context, "
                "already-reviewed NY DPS/PSC order evidence, and unrelated/noisy pages."
            ),
            "qualification": (
                "This closes the current public-web pass without changing source-ready counts. The next "
                "useful acquisition path is a new exact-property NY DPS/PSC document, an HPM-controlled "
                "building/portal/client-list page, a role-explicit NY DOS/legal record, or dated "
                "outreach-confirmed evidence."
            ),
        },
        {
            "source_family": "public_web_search_live_refresh_hpm_2026_05_15",
            "source_urls": [
                "https://www.openigloo.com/contact/nyc/66ec2a5f-3005-415d-9553-d5ee48f8aeec/harlem-property-management-inc",
                "https://www.mystatemls.com/property/2257-adam-clayton-powell-jr-blvd-3b-new-york-ny-10030/11146569/",
                "https://www.renthop.com/listings/342-w-56th-st/2d/74523503",
                "https://renthistory.org/corporation/corp_report.php?corporationname=HARLEM+PROPERTY+MANAGEMENT+INC.",
            ],
            "finding": (
                "A May 15, 2026 live HPM refresh found OpenIgloo still listing exact HPM-associated "
                "buildings such as 306 West 115 Street, 342 West 56 Street, 36 West 138 Street, "
                "plus active associated listings at 342 West 56 Street and 42 West 120 Street, "
                "with violation data refreshed within the week. MyStateMLS still lists 2257 Adam "
                "Clayton Powell Jr Blvd #3B / The Ellison with HOA as Harlem Property Management. "
                "A follow-up RentHop exact-property listing for 342 W 56th St names a processing fee "
                "payable to Harlem Property Management, Inc.; Zillow repeats the same fee language "
                "for the same off-market unit. RentHistory again lists the remaining exact HPM "
                "addresses from HPD registration context."
            ),
            "qualification": (
                "This refresh confirms existing external_web_profile, real_estate_listing, and "
                "HPD-registration-derived families, and adds one new real_estate_listing bridge that "
                "turns 342 WEST 56 STREET into a strict manager-proof source-ready-if-recorded group. "
                "Zillow corroborates that bridge inside the same listing family; do not double-count "
                "repeated OpenIgloo/listing or RentHistory evidence as new independent overlap for "
                "other groups."
            ),
        },
        {
            "source_family": "public_web_search_followup_hpm_batch_4_2026_05_15",
            "source_urls": [
                "https://harlempm.com/",
                "https://harlempm.com/wp-content/uploads/2024/01/Sample_COI_Harlem.pdf",
                "https://www.apartments.com/555-lenox-ave-new-york-ny/p0x729s/",
                "https://www.apartments.com/555-lenox-ave-new-york-ny/mnlmv0k/",
            ],
            "finding": (
                "A further exact-property and association-name pass over the 9 remaining non-strict HPM "
                "groups found HPM company-role content, a generic HPM sample COI, repeated RentHistory/"
                "OpenIgloo/Justia context, and Apartments.com 555 Lenox listing pages that show the "
                "exact property but do not expose Harlem Property Management as the manager in text."
            ),
            "qualification": (
                "This pass does not change source-ready counts. The remaining non-strict groups still "
                "need a new exact-property manager source such as NY DPS/PSC, an HPM-controlled building "
                "or portal page, role-explicit NY DOS/legal evidence, or outreach-confirmed evidence."
            ),
        },
        {
            "source_family": "public_web_search_followup_hpm_batch_5_2026_05_15",
            "source_urls": [
                "https://www.propertyshark.com/mason/Property/123720979/306-W-115-St-New-York-NY-10026/",
                "https://www.openigloo.com/contact/nyc/66ec2a5f-3005-415d-9553-d5ee48f8aeec/harlem-property-management-inc",
                "https://renthistory.org/corporation/corp_report.php?corporationname=HARLEM+PROPERTY+MANAGEMENT+INC.",
                "https://renthistory.org/network-explorer/?firstname_explore=JAMES&lastname_explore=SIMARI",
            ],
            "finding": (
                "A follow-up live search over 141 West 123 Street, 306 West 115 Street, 42 West "
                "120 Street, 506 East 119 Street, 11 St Nicholas Avenue, and 330 West 145 Street "
                "again found RentHistory/James Simari HPD-derived associations and OpenIgloo "
                "existing-family evidence. A 306 West 115 Street PropertyShark page exposes the "
                "exact BBL/property page but keeps building-management contact names behind account "
                "access, so it does not provide a visible manager-supporting record."
            ),
            "qualification": (
                "Do not add a strict evidence template from this pass. Gated contact placeholders, "
                "generic HPM lead-list pages, repeated OpenIgloo, and RentHistory-derived records "
                "do not create a second independent manager-proof source family."
            ),
        },
        {
            "source_family": "public_web_search_followup_hpm_batch_6_2026_05_15",
            "source_urls": [
                "https://law.justia.com/cases/new-york/other-courts/2025/2025-ny-slip-op-33436-u.html",
                "https://renthistory.org/corporation/corp_report.php?corporationname=HARLEM+PROPERTY+MANAGEMENT+INC.",
                "https://www.apartments.com/555-lenox-ave-new-york-ny/p0x729s/",
                "https://www.apartments.com/555-lenox-ave-new-york-ny/mnlmv0k/",
            ],
            "finding": (
                "A later pass over 555 Lenox Avenue, 61 Lenox Avenue, 345 Lenox Avenue, and "
                "42 West 120 Street found repeated RentHistory/HPD-derived property associations, "
                "the already-reviewed 345 Lenox litigation context, and Apartments.com 555 Lenox "
                "listing pages that expose the exact property but still do not name Harlem Property "
                "Management in public text."
            ),
            "qualification": (
                "Do not add a strict evidence template from this pass. The results are either "
                "already-reviewed litigation context, HPD-derived registration context, or "
                "property-only listings without visible manager language."
            ),
        },
        {
            "source_family": "public_web_search_followup_hpm_batch_7_2026_05_15",
            "source_urls": [
                "https://www.openigloo.com/contact/nyc/66ec2a5f-3005-415d-9553-d5ee48f8aeec/harlem-property-management-inc",
                "https://renthistory.org/corporation/corp_report.php?corporationname=HARLEM+PROPERTY+MANAGEMENT+INC.",
                "https://www.zillow.com/homedetails/342-W-56th-St-APT-2D-New-York-NY-10019/122346580_zpid/",
                "https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7B01D4BD59-5170-49BB-828F-34534144E502%7D",
            ],
            "finding": (
                "The latest exact-property pass for 141 West 123 Street, 306 West 115 Street, "
                "42 West 120 Street, 506 East 119 Street, 61 Lenox Avenue, 555 Lenox Avenue, "
                "330 West 145 Street, and 11 St Nicholas Avenue found no new strict independent "
                "manager-proof source. Results repeated OpenIgloo existing-family context, "
                "RentHistory/HPD-derived associations, same-family 342 West 56 Street listing "
                "corroboration, already-reviewed NY DPS/PSC records, or unrelated/noisy pages."
            ),
            "qualification": (
                "This pass did not change source-ready counts or strict manager-proof counts. "
                "The remaining HPM groups still need a new exact-property NY DPS/PSC document, "
                "an HPM-controlled building or portal page, role-explicit NY DOS/legal evidence, "
                "or dated outreach-confirmed evidence."
            ),
        },
        {
            "source_family": "public_web_search_followup_hpm_batch_8_2026_05_15",
            "source_urls": [
                "https://law.justia.com/cases/new-york/other-courts/2018/2018-ny-slip-op-32550-u.html",
                "https://law.justia.com/cases/new-york/other-courts/2025/2025-ny-slip-op-30666-u.html",
                "https://renthistory.org/network-explorer/?firstname_explore=JAMES&lastname_explore=SIMARI",
                "https://www.openigloo.com/contact/nyc/66ec2a5f-3005-415d-9553-d5ee48f8aeec/harlem-property-management-inc",
            ],
            "finding": (
                "A continuation live search for 11 St Nicholas Avenue, 141 West 123 Street, "
                "306 West 115 Street, 42 West 120 Street, 506 East 119 Street, 61 Lenox Avenue, "
                "555 Lenox Avenue, and 330 West 145 Street found a 2018 11-15 St. Nicholas "
                "court order authorizing the receiver to retain a managing agent, but that order "
                "does not name Harlem Property Management. Other results repeated the 2025 "
                "11-15 St. Nicholas HPM property-manager decision, OpenIgloo existing-family "
                "profile evidence, and RentHistory/James Simari HPD-derived associations."
            ),
            "qualification": (
                "Do not add a strict evidence template from this pass. The 2018 order is useful "
                "legal/receivership context for the building but is not support for HPM because "
                "it does not identify HPM as the managing agent; repeated Justia, OpenIgloo, and "
                "RentHistory hits do not create a new independent manager-proof family."
            ),
        },
        {
            "source_family": "public_web_search_followup_hpm_batch_9_2026_05_15",
            "source_urls": [
                "https://www.openigloo.com/contact/nyc/66ec2a5f-3005-415d-9553-d5ee48f8aeec/harlem-property-management-inc",
                "https://renthistory.org/corporation/corp_report.php?corporationname=HARLEM+PROPERTY+MANAGEMENT+INC.",
                "https://law.justia.com/cases/new-york/other-courts/2025/2025-ny-slip-op-33436-u.html",
                "https://www.apartments.com/555-lenox-ave-new-york-ny/p0x729s/",
                "https://www.apartments.com/555-lenox-ave-new-york-ny/mnlmv0k/",
            ],
            "finding": (
                "A further exact-source pass checked NY DPS/PSC-targeted searches and broader web "
                "queries for 141 West 123 Street, 306 West 115 Street, 42 West 120 Street, "
                "506 East 119 Street, 555 Lenox Avenue, 61 Lenox Avenue, 345 Lenox Avenue, "
                "and 330 West 145 Street. It found no new qualifying NY DPS/PSC or HPM-controlled "
                "exact-property manager source. Results repeated RentHistory/HPD-derived and "
                "OpenIgloo existing-family context; 345 Lenox litigation names HPM among condo "
                "defendants but still does not state an operating-manager role; Apartments.com "
                "555 Lenox pages expose property/listing context without naming HPM as manager."
            ),
            "qualification": (
                "Do not add a strict evidence template from this pass. The remaining non-strict "
                "HPM groups still need a new exact-property manager-specific source family, "
                "such as a visible NY DPS/PSC document, HPM-controlled building page, "
                "role-explicit NY DOS/legal record, or dated outreach confirmation."
            ),
        },
        {
            "source_family": "public_web_search_followup_hpm_batch_10_2026_05_15",
            "source_urls": [
                "https://www.openigloo.com/contact/nyc/66ec2a5f-3005-415d-9553-d5ee48f8aeec/harlem-property-management-inc",
                "https://renthistory.org/corporation/corp_report.php?corporationname=HARLEM+PROPERTY+MANAGEMENT+INC.",
                "https://renthistory.org/network-explorer/?query=Harlem+Property+Management&search_type=corporation",
            ],
            "finding": (
                "A post-resume exact-property pass over 141 West 123 Street, 306 West 115 Street, "
                "42 West 120 Street, 506 East 119 Street, 555 Lenox Avenue, 61 Lenox Avenue, "
                "330 West 145 Street, and 11 St Nicholas Avenue again found no new strict "
                "manager-proof source. Results repeated OpenIgloo associated-property profile "
                "evidence and RentHistory/HPD-registration-derived portfolio context already "
                "classified in existing source families."
            ),
            "qualification": (
                "This pass adds no source template and does not change source-ready counts. "
                "OpenIgloo remains the existing external_web_profile family, RentHistory remains "
                "HPD-derived review context, and the remaining HPM groups still need a new "
                "exact-property NY DPS/PSC document, HPM-controlled property page, role-explicit "
                "NY DOS/legal record, or dated outreach confirmation."
            ),
        },
        {
            "source_family": "public_web_search_followup_hpm_batch_11_2026_05_15",
            "source_urls": [
                "https://renthistory.org/corporation/corp_report.php?corporationname=HARLEM+PROPERTY+MANAGEMENT+INC.",
                "https://www.openigloo.com/contact/nyc/66ec2a5f-3005-415d-9553-d5ee48f8aeec/harlem-property-management-inc",
                "https://renthistory.org/network-explorer/?query=Harlem+Property+Management&search_type=corporation",
            ],
            "finding": (
                "A further exact search for Harlem Property Management plus 306 West 115 Street, "
                "42 West 120 Street, 506 East 119 Street, 555 Lenox Avenue, and 141 West 123 "
                "Street again returned RentHistory/HPD-derived portfolio pages and the already "
                "classified OpenIgloo HPM profile. No new NY DPS/PSC, HPM-controlled exact-property "
                "page, role-explicit NY DOS/legal source, HPD ManagementCompany row, or dated "
                "outreach confirmation was found."
            ),
            "qualification": (
                "This pass adds no source template and does not change source-ready counts. "
                "OpenIgloo remains the existing external_web_profile family and RentHistory remains "
                "HPD-registration-derived review context; the remaining HPM groups still need a new "
                "exact-property manager-specific source family."
            ),
        },
        {
            "source_family": "public_web_search_followup_hpm_batch_12_2026_05_15",
            "source_urls": [
                "https://renthistory.org/corporation/corp_report.php?corporationname=HARLEM+PROPERTY+MANAGEMENT+INC.",
                "https://www.openigloo.com/contact/nyc/66ec2a5f-3005-415d-9553-d5ee48f8aeec/harlem-property-management-inc",
                "https://www.propertyshark.com/mason/Property/123720979/306-W-115-St-New-York-NY-10026/",
            ],
            "finding": (
                "A post-boundary live search over 141 West 123 Street, 306 West 115 Street, "
                "42-44 West 120 Street, 506 East 119 Street Condominium, and 555 Lenox Avenue "
                "again repeated RentHistory HPD-derived associations and the existing OpenIgloo "
                "management-company profile; the visible PropertyShark 306 West 115 page exposes "
                "property facts but not a visible Harlem Property Management contact."
            ),
            "qualification": (
                "This adds no source template and does not change source-ready counts. The remaining "
                "non-strict HPM groups still need a new exact-property NY DPS/PSC document, "
                "HPM-controlled exact-property page, role-explicit legal source, HPD "
                "ManagementCompany row, or dated outreach confirmation."
            ),
        },
        {
            "source_family": "public_web_search_followup_hpm_batch_13_2026_05_15",
            "source_urls": [
                "https://renthistory.org/corporation/corp_report.php?corporationname=HARLEM+PROPERTY+MANAGEMENT+INC.",
                "https://www.openigloo.com/contact/nyc/66ec2a5f-3005-415d-9553-d5ee48f8aeec/harlem-property-management-inc",
                "https://renthistory.org/network-explorer/?query=Harlem+Property+Management&search_type=corporation",
            ],
            "finding": (
                "A final exact/non-HPD pass searched the remaining HPM strict-gap addresses: "
                "141 West 123 Street, 306 West 115 Street, 42 West 120 Street, 506 East 119 "
                "Street, 555 Lenox Avenue, 61 Lenox Avenue, 345 Lenox Avenue, and "
                "330 West 145 Street, including targeted NY DPS/PSC document searches. Results "
                "again resolved to RentHistory/HPD-registration-derived associations, the "
                "already-classified OpenIgloo associated-property profile, property-only pages, "
                "or broad company-directory context; no exact non-HPD source stated a current "
                "HPM operating-manager role for the remaining gap buildings."
            ),
            "qualification": (
                "This adds no source template and does not change source-ready counts. "
                "The remaining non-strict HPM groups still need one new exact-property "
                "manager-proof source family, such as a visible NY DPS/PSC document, "
                "HPM-controlled building page, role-explicit legal/DOS record, HPD "
                "ManagementCompany row, or dated outreach confirmation."
            ),
        },
        {
            "source_family": "public_web_search_followup_hpm_batch_14_2026_05_15",
            "source_urls": [
                "https://law.justia.com/cases/new-york/other-courts/2025/2025-ny-slip-op-33436-u.html",
                "https://www.leagle.com/decision/innyco20250926463",
                "https://www.apartments.com/555-lenox-ave-new-york-ny/p0x729s/",
                "https://www.apartments.com/555-lenox-ave-new-york-ny/mnlmv0k/",
                "https://renthistory.org/corporation/corp_report.php?corporationname=HARLEM+PROPERTY+MANAGEMENT+INC.",
            ],
            "finding": (
                "A latest exact-source pass searched Harlem Property Management with 141 West "
                "123 Street, 330 West 145 Street, 306 West 115 Street, 42 West 120 Street, "
                "555 Lenox Avenue, 61 Lenox Avenue, 506 East 119 Street, and 345 Lenox "
                "Avenue manager/managing-agent variants. It found current 345 Lenox litigation "
                "surfaces that name Harlem Property Management among condo defendants, "
                "Apartments.com 555 Lenox listing pages that expose property/listing context "
                "without visible HPM manager text, and repeated RentHistory/HPD-derived HPM "
                "association pages."
            ),
            "qualification": (
                "This adds no strict manager-proof source and does not change source-ready "
                "counts. The 345 Lenox litigation still does not state that HPM is the "
                "operating manager, Apartments.com is property/listing context without visible "
                "HPM manager language, and RentHistory remains HPD-registration-derived."
            ),
        },
        {
            "source_family": "public_web_search_followup_hpm_batch_15_2026_05_15",
            "source_urls": [
                "https://law.justia.com/cases/new-york/other-courts/2025/2025-ny-slip-op-30666-u.html",
                "https://renthistory.org/corporation/corp_report.php?corporationname=HARLEM+PROPERTY+MANAGEMENT",
                "https://www.homes.com/manhattan-ny/central-harlem-neighborhood/newest/p6/",
                "https://theorg.com/org/harlem-property-management/org-chart/robert-pair",
                "https://about.me/harlempropertymanagement",
                "https://www.uhab.org/es/vendor/harlem-property-management/",
            ],
            "finding": (
                "A Robert Pair / HPM identity and 11-15 St. Nicholas refresh searched "
                "Robert D. Pair, Robb Pair, Harlem Property Management, 11-15 St. Nicholas "
                "Avenue, and Management Agreement variants. It reconfirmed the already-counted "
                "2025 McKew decision naming Robert D. Pair of Harlem Property Management as "
                "receiver property manager, repeated RentHistory/HPD-derived 11-15 St. Nicholas "
                "associations, found Homes.com listing context for 11-15 St. Nicholas units "
                "under Robert Pair / Harlem Lofts rather than an HPM building-management "
                "relationship, and found company/leadership profiles that describe HPM and "
                "Pair as property-management operators."
            ),
            "qualification": (
                "This adds no strict evidence template and does not change source-ready "
                "counts. The Justia hit is the same litigation-record source family already "
                "recorded for 11 St Nicholas, RentHistory remains HPD-registration-derived, "
                "Homes.com is brokerage/listing context that does not state HPM manages the "
                "building, and company/leadership profiles still do not name exact pilot "
                "buildings."
            ),
        },
        {
            "source_family": "public_web_search_followup_hpm_batch_16_2026_05_15",
            "source_urls": [
                "https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7B32249633-00BF-46BE-9DBC-4918ECA197B1%7D&DocTitle=Exhibit+1",
                "https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7B12CCCFAB-46DD-4276-B49C-CC8D7A9C20CC%7D",
                "https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7B3C0A2D6C-990E-448A-9BA4-E16BEC4BCB3D%7D",
                "https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7B122FC9F1-5BD8-49A4-BB4D-D1F659589C67%7D",
                "https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7B4D40F974-F4F8-4AA7-ABB7-C705EA3A582F%7D",
                "https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7B1DA4DEBB-5A67-4703-B2FF-F0488DD72C30%7D",
            ],
            "finding": (
                "A Strivers North / 202-204 West 140 exact-source refresh found additional "
                "NY DPS/PSC documents naming Strivers North Condominium and Harlem Property "
                "Management, Inc. for 202 W 140 ST or exact 204 W 140 ST, including request, "
                "dismissal, notice, and certified-mail surfaces with Jim Simari / HPM contact "
                "context."
            ),
            "qualification": (
                "This strengthens the reviewed NY DPS context for the 204 WEST 140 STREET "
                "broad-source-ready group but does not add a second manager-proof family. "
                "All of these records remain the same ny_dps_order_entry source family; "
                "strict manager-proof readiness still needs a non-DPS exact-property "
                "manager source such as a company-controlled building page, role-explicit "
                "legal/DOS record from another family, HPD ManagementCompany row, or dated "
                "outreach confirmation."
            ),
        },
        {
            "source_family": "public_web_search_followup_hpm_batch_17_2026_05_15",
            "source_urls": [
                "https://www.fsresidential.com/new-york/news-events/press-releases/firstservice-residential-adds-seven-new-properties/",
                "https://renthistory.org/corporation/corp_report.php?corporationname=HARLEM+PROPERTY+MANAGEMENT+INC.",
                "https://www.renthistory.org/network-explorer/index.php?query=THE+HAMILTON+OWNERS+CORP&search_type=corporation",
            ],
            "finding": (
                "A 330 West 145 / The Hamilton exact-source pass found a FirstService "
                "Residential press release dated August 26, 2015 saying FirstService was "
                "selected to serve as property manager for The Hamilton Owners Corporation "
                "at 330 West 145th Street. The same pass otherwise repeated RentHistory / "
                "HPD-registration-derived HPM association pages for The Hamilton."
            ),
            "qualification": (
                "Do not record this as HPM support. The FirstService item is stale "
                "conflicting-manager context for the exact building, while the HPM hits are "
                "HPD-registration-derived. If recorded later, route FirstService as "
                "contradiction/review evidence against a current HPM manages_building claim; "
                "it does not create strict HPM manager-proof overlap or change source-ready "
                "counts."
            ),
        },
        {
            "source_family": "public_web_search_followup_hpm_batch_18_2026_05_15",
            "source_urls": [
                "https://www.openigloo.com/contact/nyc/66ec2a5f-3005-415d-9553-d5ee48f8aeec/harlem-property-management-inc",
                "https://renthistory.org/corporation/corp_report.php?corporationname=HARLEM+PROPERTY+MANAGEMENT+INC.",
                "https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7B304B3F37-2994-4AE2-A199-4485C050FC42%7D",
                "https://www.apartments.com/555-lenox-ave-new-york-ny/p0x729s/",
                "https://www.apartments.com/555-lenox-ave-new-york-ny/mnlmv0k/",
            ],
            "finding": (
                "A building-profile / Building Team source pass searched 141 West 123, "
                "306 West 115, 42 West 120, 506 East 119, 555 Lenox, 61 Lenox, "
                "345 Lenox, 330 West 145, and 204 West 140 variants for visible "
                "manager fields. Results repeated the OpenIgloo management-company "
                "profile, RentHistory/HPD-derived associations, same-family NY DPS "
                "Strivers North records, and Apartments.com 555 Lenox pages that show "
                "property/listing context but do not visibly name Harlem Property "
                "Management as manager."
            ),
            "qualification": (
                "This adds no strict evidence template and does not change source-ready "
                "counts. Building-profile pages are usable only when they expose a "
                "visible exact-property manager field; the latest 555 Lenox pages show "
                "listing/property context without HPM manager text, OpenIgloo remains "
                "the existing external_web_profile family, RentHistory remains "
                "HPD-registration-derived, and the Strivers North DPS records remain "
                "one ny_dps_order_entry family."
            ),
        },
        {
            "source_family": "public_web_search_followup_hpm_batch_19_2026_05_15",
            "source_urls": [
                "https://renthistory.org/corporation/corp_report.php?corporationname=HARLEM+PROPERTY+MANAGEMENT+INC.",
                "https://www.openigloo.com/contact/nyc/66ec2a5f-3005-415d-9553-d5ee48f8aeec/harlem-property-management-inc",
                "https://renthistory.org/network-explorer/?query=HARLEM+PROPERTY+MANAGEMENT&search_type=corporation",
            ],
            "finding": (
                "A post-approval-boundary exact search repeated Harlem Property Management queries "
                "for 141 West 123, 306 West 115, 42 West 120, and 506 East 119. Results again "
                "surfaced RentHistory/HPD-derived corporation and network pages plus the already "
                "classified OpenIgloo management-company profile that lists exact associated "
                "properties including 306 West 115 Street."
            ),
            "qualification": (
                "This adds no strict evidence template and does not change source-ready counts. "
                "RentHistory remains HPD-registration-derived, OpenIgloo remains the existing "
                "external_web_profile family rather than a new manager-proof family, and no HPM "
                "company-controlled exact-property page, new NY DPS/PSC manager record, "
                "role-explicit NY DOS/legal record, HPD ManagementCompany row, or dated outreach "
                "confirmation was found for the searched strict-gap buildings."
            ),
        },
        {
            "source_family": "public_web_search_followup_hpm_batch_20_2026_05_16",
            "source_urls": [
                "https://renthistory.org/corporation/corp_report.php?corporationname=HARLEM+PROPERTY+MANAGEMENT+INC.",
                "https://renthistory.org/network-explorer/?firstname_explore=JAMES&lastname_explore=SIMARI",
            ],
            "finding": (
                "A May 16, 2026 exact-frontier search for 141 West 123 Street, 141 W 123, "
                "Harlem Property Management, and James Simari again surfaced RentHistory / "
                "HPD-registration-derived corporation and network pages. The results list "
                "141 WEST 123 STREET in the HPM and James Simari registration-derived networks "
                "but did not surface a company-controlled exact-property page, NY DPS/PSC "
                "manager document, role-explicit NY DOS/legal manager record, HPD "
                "ManagementCompany row, or dated outreach confirmation."
            ),
            "qualification": (
                "This adds no strict evidence template and does not change source-ready or "
                "verified counts. 141 WEST 123 STREET remains the HPM next-source seed: "
                "RentHistory can support review but stays HPD-registration-derived broad context, "
                "not a new non-HPD manager-proof family."
            ),
        },
        {
            "source_family": "public_web_search_followup_hpm_new_relationship_275_greenwich_2026_05_15",
            "source_urls": [
                "https://a836-pts-access.nyc.gov/care/datalets/datalet.aspx?LMparent=20&UseSearch=no&jur=65&mode=asmt_tent_2027&pin=1001327501&taxyr=2025",
                "https://openstoop.com/building/275-greenwich-street-manhattan",
            ],
            "finding": (
                "A follow-up source-acquisition pass on the 275 Greenwich / local 269 "
                "Greenwich new-relationship lead found official NYC Finance billing "
                "context naming Harlem Property Management, Inc., while the public "
                "OpenStoop building profile exposes current boiler/owner-management "
                "context for GREENWICH COURT CONDO C/O MILFORD MGMT."
            ),
            "qualification": (
                "This is review-only relationship-acquisition context, not current-ledger "
                "source overlap and not strict HPM manager-proof support. The NYC Finance "
                "record remains billing-agent context, the OpenStoop/Milford item is "
                "role-adjacent potential contradiction context, and any 275 Greenwich "
                "relationship must be reviewed separately before recording."
            ),
        },
        {
            "source_family": "live_hpd_role_audit_hpm_new_relationship_402_w_153_2026_05_15",
            "source_urls": [
                "https://data.cityofnewyork.us/resource/tesw-yqqr.json",
                "https://data.cityofnewyork.us/resource/feu5-w2e2.json",
                "https://renthistory.org/network-explorer/index.php?query=ROCKBRIDGE+PM&search_type=corporation",
            ],
            "finding": (
                "A live NYC Open Data HPD registration/contact lookup for BBL 1020670047 / "
                "402 WEST 153 STREET found current registration 115521 with last registration "
                "date 2025-11-06 and registration end date 2026-09-01. HPD contacts name "
                "Empire State Property Management as Agent, 402 West 153rd Street Corporation "
                "as CorporateOwner, and Matthew Maggipinto as HeadOfficer/SiteManager; no "
                "ManagementCompany row was returned. A separate RentHistory HPD-registration-derived "
                "network page associates 402 WEST 153 STREET and Matthew Maggipinto with ROCKBRIDGE PM."
            ),
            "qualification": (
                "Treat as current role-boundary and contradiction-review context for the HPM "
                "402 WEST 153 STREET new-relationship candidate. This does not support HPM, "
                "does not create strict HPM overlap, and should not be generalized into manager "
                "proof. The RentHistory/RockBridge signal is HPD-registration-derived context, "
                "not a new strict manager-proof family. If the 402 relationship is reviewed later, "
                "route the current Empire State HPD Agent/SiteManager context and RockBridge "
                "derived context as possible conflicting or stale-manager evidence rather than "
                "overwriting."
            ),
        },
        {
            "source_family": "site_native_search_hpm_2026_05_15",
            "source_urls": [
                "https://harlempm.com/wp-json/wp/v2/search?search=141%20West%20123&per_page=10",
                "https://harlempm.com/wp-json/wp/v2/search?search=141%20West%20123%20Street&per_page=20",
                "https://harlempm.com/wp-json/wp/v2/search?search=306%20West%20115&per_page=10",
                "https://harlempm.com/wp-json/wp/v2/search?search=306%20West%20115%20Street&per_page=20",
                "https://harlempm.com/wp-json/wp/v2/search?search=42%20West%20120&per_page=10",
                "https://harlempm.com/wp-json/wp/v2/search?search=42-44%20West%20120%20Street&per_page=20",
                "https://harlempm.com/wp-json/wp/v2/search?search=506%20East%20119%20Street&per_page=20",
                "https://harlempm.com/wp-json/wp/v2/search?search=204%20West%20140&per_page=10",
                "https://harlempm.com/wp-json/wp/v2/search?search=553-559%20Lenox%20Avenue&per_page=20",
                "https://harlempm.com/wp-json/wp/v2/search?search=320-338%20West%20145%20Street&per_page=20",
                "https://harlempm.com/local-law-97-experts/",
                "https://harlempm.com/expert-tribeca-property-management/",
                "https://harlempm.com/property-tax-abatement-nyc/",
            ],
            "finding": (
                "A site-native WordPress search checked HPM's own public search endpoint for the "
                "remaining HPM strict-gap addresses: 141 West 123, 306 West 115, 42 West 120, "
                "506 East 119, 555 Lenox / 553-559 Lenox, 61 Lenox, 204 West 140, and "
                "330 West 145 / 320-338 West 145 variants. Most exact long-form queries returned "
                "no posts or pages. Abbreviated 141 W 123 and 306 W 115 queries returned generic "
                "blog posts, and the 42 West 120 query returned generic HPM content pages; direct "
                "inspection found no visible exact address or building-management statement."
            ),
            "qualification": (
                "This adds no strict evidence template and does not change source-ready counts. "
                "HPM company-site content remains company-role context only unless it names the "
                "exact property and states a management or managing-agent relationship."
            ),
        },
        {
            "source_family": "operator_document_search_hpm_2026_05_16",
            "source_urls": [],
            "finding": (
                "A read-only Drive/local workbook pass rechecked the native HPM revenue-by-property "
                "Summary sheet and local HPM workbook copies for the remaining exact HPM acquisition "
                "target, including 141 West 123 / 141 W 123 / 123 variants. The sheet search returned "
                "no 141 WEST 123 STREET row; 123 matched only another 123rd Street HDFC row, and local "
                "HPM workbook copies had no target-address matches."
            ),
            "qualification": (
                "This adds no strict HPM evidence template and does not change source-ready or verified "
                "counts. 141 WEST 123 STREET remains the HPM next-source seed and still needs one exact "
                "non-HPD manager-proof source or dated outreach-confirmed evidence."
            ),
        },
        {
            "source_family": "official_hpd_and_public_web_refresh_hpm_2026_05_18",
            "source_urls": [
                "https://data.cityofnewyork.us/d/tesw-yqqr",
                "https://data.cityofnewyork.us/d/feu5-w2e2",
                "https://www.openigloo.com/contact/nyc/66ec2a5f-3005-415d-9553-d5ee48f8aeec/harlem-property-management-inc",
                "https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId=%7BCF5C1F53-F557-45AB-9DA2-BA47E386AD90%7D",
                "https://renthistory.org/corporation/corp_report.php?corporationname=HARLEM+PROPERTY+MANAGEMENT+INC.",
            ],
            "finding": (
                "A May 18, 2026 Phase 2 acquisition pass searched the current HPM evidence frontier, "
                "including 141 West 123 Street and the closest source-ready HPM rows. Results repeated "
                "already-classified families: OpenIgloo exact-property profile evidence for HPM-associated "
                "buildings such as 342 West 56 Street and 36 West 138 Street, NY DPS exact managing-agent "
                "context for 36 West 138 Street, and RentHistory/HPD-derived portfolio context that includes "
                "141 West 123 Street. No new HPM-controlled exact-property page, new NY DPS/PSC family, "
                "role-explicit NY DOS/legal source, HPD ManagementCompany row, or dated outreach confirmation "
                "was found for the 141 West 123 next-source seed."
            ),
            "qualification": (
                "This adds no strict HPM evidence template and does not change source-ready or verified counts. "
                "The 36 West 138 DPS material and OpenIgloo rows are already represented source families, "
                "RentHistory remains HPD-registration-derived broad context, and the current runtime still "
                "cannot fetch official HPD slices directly. 141 WEST 123 STREET still needs one exact "
                "non-HPD manager-proof source, a real HPD ManagementCompany row from an official extract/query, "
                "or dated outreach-confirmed evidence."
            ),
        },
        {
            "source_family": "operator_document_raw_xlsx_followup_hpm_2026_05_19",
            "source_urls": [],
            "finding": (
                "A May 19, 2026 read-only raw `.xlsx` fetch of the operator-provided HPM "
                "'Revenue by Property - Summary' workbook reconfirmed the known HPM rows already "
                "used for the approved strict-HPM source-overlap packet, including 36 W 138, "
                "202/204 W 140, 306 W 115, 324 E 112, 330 W 145, 342 W 56, 42 W 120, "
                "506 E 119, 555 Malcolm X/Lenox, 61 Malcolm X/Lenox, and 11-15 St. Nicholas Avenue. "
                "It still did not contain an exact 141 WEST 123 STREET row."
            ),
            "qualification": (
                "This reconfirms document coverage and the remaining HPM acquisition gap but adds no "
                "new strict evidence family, exposes no private Drive URL or row-level revenue data, "
                "and does not change source-ready or verified counts. 141 WEST 123 STREET still needs "
                "one exact non-HPD manager-proof source, a real HPD ManagementCompany row from an "
                "official extract/query, or dated outreach-confirmed evidence."
            ),
        },
        {
            "source_family": "operator_document_raw_drive_fetch_hpm_2026_05_19_phase4",
            "source_urls": [],
            "finding": (
                "A later May 19, 2026 direct Drive raw `.xlsx` fetch reconfirmed the same "
                "HPM first-party operator-document coverage already used for the approved "
                "strict-HPM packet. The text-extracted `Revenue by Property - Summary.xlsx` "
                "rows include 306W 115th, 42 W 120th, 506 East 119th, 324 E 112th, "
                "36 W 138th, 342 W 56th, 555 Lenox/Malcolm X, 61 Malcolm X, and "
                "11-15 St. Nicholas; the workbook still did not contain exact "
                "141 WEST 123 STREET support."
            ),
            "qualification": (
                "This tightens source-acquisition provenance but adds no new strict evidence "
                "family, exposes no private Drive URL or row-level revenue data, and does not "
                "change source-ready or verified counts. 141 WEST 123 STREET still needs one "
                "exact non-HPD manager-proof source, a real HPD ManagementCompany row from an "
                "official extract/query, or dated outreach-confirmed evidence."
            ),
        },
        {
            "source_family": "operator_document_native_sheet_followup_hpm_2026_05_19",
            "source_urls": [],
            "finding": (
                "A May 19, 2026 native Google Sheet follow-up searched the operator-provided "
                "`Revenue by Property - Summary` `Summary` tab after the prior Sheets API rate "
                "limit cleared. The search reconfirmed known HPM first-party operator-document "
                "rows such as 342 West 56 Street, 324 East 112 Street, and 36 West 138 Street, "
                "but still did not contain an exact 141 WEST 123 STREET row."
            ),
            "qualification": (
                "This tightens source-acquisition provenance but adds no new strict evidence family, "
                "exposes no private Drive URL or row-level revenue data, and does not change "
                "source-ready or verified counts. 141 WEST 123 STREET still needs one exact "
                "non-HPD manager-proof source, a real HPD ManagementCompany row from an official "
                "extract/query, or dated outreach-confirmed evidence."
            ),
        },
        {
            "source_family": "operator_document_exact_drive_search_hpm_2026_05_19_phase5",
            "source_urls": [],
            "finding": (
                "A follow-on May 19, 2026 read-only Drive pass searched the known `Revenue by "
                "Property - Summary` native sheet for the remaining HPM gap and nearby target "
                "strings. Exact row searches for 141 and related operator-seed strings still did "
                "not return 141 WEST 123 STREET; the same pass reconfirmed that MD Squared/Daisy "
                "seed searches are outside the HPM workbook's exact row coverage."
            ),
            "qualification": (
                "This is reviewed source-acquisition history only. It adds no new strict HPM "
                "evidence family, exposes no private Drive URL or row-level revenue data, and "
                "does not change source-ready or verified counts. 141 WEST 123 STREET still needs "
                "one exact non-HPD manager-proof source, a real HPD ManagementCompany row from an "
                "official extract/query, or dated outreach-confirmed evidence."
            ),
        },
    ]
    source_boundary_notes = [
        (
            "RentHistory and HPD-registration-derived context can support review, but it is not "
            "counted as manager-proof independent evidence."
        ),
        (
            "NY DOS service-of-process or c/o mailing records are role-specific legal/mailing "
            "evidence unless the source explicitly states a property-management or managing-agent role."
        ),
        (
            "Company profile pages can prove Harlem Property Management is a property manager, but "
            "they do not support a specific building relationship unless they list the exact property."
        ),
        (
            "First-party HPM operating documents can be manager-proof evidence only when the row names "
            "the exact property and the operator confirms document provenance; they remain unrecorded "
            "preview templates until explicit approval."
        ),
    ]
    for group in grouped_claims:
        if group.get("strict_manager_source_ready_if_recorded"):
            continue
        manager_proof_families = set(group.get("manager_proof_source_families_if_recorded") or [])
        missing_family_count = max(0, VERIFICATION_MIN_SUPPORTING_SOURCES - len(manager_proof_families))
        suggested_source_families = [
            family
            for family in (
                HPM_REVENUE_BY_PROPERTY_SOURCE_FAMILY,
                "ny_dps_order_entry",
                "company_website",
                "outreach_confirmed",
                "ny_dos",
            )
            if family not in manager_proof_families
        ]
        for family in suggested_source_families[:2]:
            source_family_counts[family] = source_family_counts.get(family, 0) + 1
        proposals.append({
            "bbl": (group.get("fact_key") or {}).get("object_id"),
            "address": group.get("address"),
            "existing_manager_proof_source_families": sorted(manager_proof_families),
            "missing_manager_proof_source_family_count": missing_family_count,
            "suggested_source_families": suggested_source_families,
            "search_queries": _manager_source_search_queries(group.get("address"), manager_proof_families),
            "source_targets": _manager_source_targets(suggested_source_families, group.get("address")),
            "source_boundary_notes": source_boundary_notes,
            "safe_action": (
                "Acquire one more non-HPD-derived manager-specific source before treating this group "
                "as strict manager-proof. Do not use HPD Agent or RentHistory-derived context as the second source."
            ),
        })

    return {
        "dry_run": True,
        "mutations_planned": 0,
        "candidate_count": len(proposals),
        "suggested_source_family_counts": dict(sorted(source_family_counts.items(), key=lambda item: (-item[1], item[0]))),
        "source_boundary_notes": source_boundary_notes,
        "reviewed_source_findings": reviewed_source_findings,
        "proposals": proposals,
        "safe_action": (
            "Use this plan for the next source-acquisition pass only. Recording remains approval-gated "
            "through manual evidence capture."
        ),
    }


def simulate_manager_external_evidence_post_recording(
    templates: list[dict[str, Any]],
    *,
    recorded_by: str = "operator",
) -> dict[str, Any]:
    """Simulate adjudication of the external-manager evidence batch without writes."""
    grouped: dict[tuple[str, str, str, str, str, str, str], dict[str, Any]] = {}
    for template in templates:
        spec = build_manual_evidence_claim_spec(template, recorded_by=recorded_by)
        claim = spec["claim"]
        evidence = spec["evidence"]
        key = (
            str(claim.get("subject_type") or ""),
            str(claim.get("subject_id") or ""),
            str(claim.get("predicate") or ""),
            str(claim.get("object_type") or ""),
            str(claim.get("object_id") or ""),
            str(claim.get("normalized_value") or ""),
            str(claim.get("claim_type") or ""),
        )
        group = grouped.setdefault(key, {
            "subject_type": claim.get("subject_type"),
            "subject_id": claim.get("subject_id"),
            "predicate": claim.get("predicate"),
            "object_type": claim.get("object_type"),
            "object_id": claim.get("object_id"),
            "normalized_value": claim.get("normalized_value"),
            "claim_type": claim.get("claim_type"),
            "claim_ids": [],
            "evidence_ids": [],
            "supporting_sources": [],
            "contradicting_sources": [],
            "supporting_evidence_count": 0,
            "contradicting_evidence_count": 0,
            "existing_belief_statuses": [],
            "max_confidence_score": 0.0,
            "min_confidence_score": 1.0,
            "freshest_observed_freshness_days": None,
            "oldest_observed_freshness_days": None,
            "source_families": [],
        })
        group["claim_ids"].append(claim.get("claim_id"))
        group["evidence_ids"].append(evidence.get("evidence_id"))
        support_status = str(evidence.get("support_status") or "supports")
        source_name = str(evidence.get("source_name") or "")
        if support_status == "contradicts":
            group["contradicting_sources"].append(source_name)
            group["contradicting_evidence_count"] += 1
        else:
            group["supporting_sources"].append(source_name)
            group["supporting_evidence_count"] += 1
        group["existing_belief_statuses"].append(claim.get("belief_status"))
        score = _as_float(claim.get("confidence_score"))
        group["max_confidence_score"] = max(_as_float(group.get("max_confidence_score")), score)
        group["min_confidence_score"] = min(_as_float(group.get("min_confidence_score"), 1.0), score)
        freshness = claim.get("freshness_days")
        if freshness is not None:
            freshness_int = _as_int(freshness)
            current_freshest = group.get("freshest_observed_freshness_days")
            current_oldest = group.get("oldest_observed_freshness_days")
            group["freshest_observed_freshness_days"] = (
                freshness_int if current_freshest is None else min(_as_int(current_freshest), freshness_int)
            )
            group["oldest_observed_freshness_days"] = (
                freshness_int if current_oldest is None else max(_as_int(current_oldest), freshness_int)
            )
        family = (evidence.get("raw_payload") or {}).get("source_family")
        if family:
            group["source_families"].append(str(family))

    samples: list[dict[str, Any]] = []
    for group in grouped.values():
        group["supporting_sources"] = list(dict.fromkeys(group["supporting_sources"]))
        group["contradicting_sources"] = list(dict.fromkeys(group["contradicting_sources"]))
        group["source_families"] = list(dict.fromkeys(group["source_families"]))
        manager_proof_families = [
            family for family in group["source_families"] if family not in NON_MANAGER_PROOF_SOURCE_FAMILIES
        ]
        adjudicated = adjudicate_fact_group(group)
        samples.append({
            **adjudicated,
            "supporting_source_families": group["source_families"],
            "supporting_source_family_count": len(group["source_families"]),
            "manager_proof_source_families": manager_proof_families,
            "manager_proof_source_family_count": len(manager_proof_families),
            "strict_manager_source_ready": (
                len(manager_proof_families) >= VERIFICATION_MIN_SUPPORTING_SOURCES
                and group["supporting_evidence_count"] >= VERIFICATION_MIN_SUPPORTING_EVIDENCE
            ),
        })

    multi_source_count = sum(1 for sample in samples if sample["supporting_source_count"] >= 2)
    source_ready_count = sum(
        1
        for sample in samples
        if sample["supporting_source_count"] >= VERIFICATION_MIN_SUPPORTING_SOURCES
        and sample["supporting_evidence_count"] >= VERIFICATION_MIN_SUPPORTING_EVIDENCE
    )
    independent_source_ready_count = sum(
        1
        for sample in samples
        if sample["supporting_source_family_count"] >= VERIFICATION_MIN_SUPPORTING_SOURCES
        and sample["supporting_evidence_count"] >= VERIFICATION_MIN_SUPPORTING_EVIDENCE
    )
    strict_manager_source_ready_count = sum(1 for sample in samples if sample["strict_manager_source_ready"])
    safe_count = sum(1 for sample in samples if sample["safe_to_mark_verified"])
    source_ready_by_predicate: dict[str, int] = {}
    safe_by_predicate: dict[str, int] = {}
    blocker_counts: dict[str, int] = {}
    for sample in samples:
        predicate = str((sample.get("fact_key") or {}).get("predicate") or "unknown")
        if (
            sample["supporting_source_count"] >= VERIFICATION_MIN_SUPPORTING_SOURCES
            and sample["supporting_evidence_count"] >= VERIFICATION_MIN_SUPPORTING_EVIDENCE
        ):
            source_ready_by_predicate[predicate] = source_ready_by_predicate.get(predicate, 0) + 1
        if sample["safe_to_mark_verified"]:
            safe_by_predicate[predicate] = safe_by_predicate.get(predicate, 0) + 1
        for blocker in sample.get("blockers") or []:
            blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1

    return {
        "dry_run": True,
        "mutations_planned": 0,
        "template_count": len(templates),
        "simulated_fact_group_count": len(samples),
        "multi_source_fact_group_count": multi_source_count,
        "source_ready_fact_group_count": source_ready_count,
        "independent_source_ready_fact_group_count": independent_source_ready_count,
        "strict_manager_source_ready_fact_group_count": strict_manager_source_ready_count,
        "excluded_manager_proof_source_families": sorted(NON_MANAGER_PROOF_SOURCE_FAMILIES),
        "safe_to_mark_verified_count": safe_count,
        "source_ready_count_by_predicate": dict(sorted(source_ready_by_predicate.items())),
        "safe_to_mark_verified_count_by_predicate": dict(sorted(safe_by_predicate.items())),
        "blocker_counts": dict(sorted(blocker_counts.items())),
        "samples": samples[:10],
        "safe_action": (
            "This is a no-write simulation. It proves source overlap if the batch is later recorded, "
            "but verification/business use still require actual recording and post-record adjudication."
        ),
    }


async def load_manager_external_source_acquisition_preview(
    session: AsyncSession,
    *,
    lead_id: str = "0ff794d3ba2d",
    limit: int = 20,
) -> dict[str, Any]:
    """Preview manager-specific external evidence that could be operator-reviewed.

    The candidates are curated from public source discovery. This function only
    matches those documents to local current relationships and prepares manual
    evidence payloads; it does not claim that stale web/public filings are fresh
    enough for verification or that HPD-derived indexes are independent.
    """
    bounded_limit = min(max(int(limit or 20), 1), 50)
    address_matches: dict[str, dict[str, Any]] = {}
    candidate_addresses = sorted({
        _normalize_street_address(address)
        for candidate in MANAGER_EXTERNAL_SOURCE_CANDIDATES
        for address in (
            [candidate.get("local_address")]
            if candidate.get("local_address")
            else candidate.get("candidate_local_addresses") or []
        )
        if str(address or "").strip()
    })

    local_building_matches: dict[str, dict[str, Any]] = {}
    for normalized_address in candidate_addresses:
        row = (await session.execute(
            text("""
                SELECT
                    b.bbl,
                    b.address,
                    b.borough,
                    b.zip_code,
                    b.unit_count,
                    bm.lead_id,
                    bm.role AS building_management_role,
                    (
                        SELECT count(*)
                        FROM truth_claims tc
                        WHERE tc.subject_type = 'lead'
                          AND tc.subject_id = :lead_id
                          AND tc.predicate = 'manages_building'
                          AND tc.object_type = 'building'
                          AND tc.object_id = b.bbl
                          AND tc.current_flag = true
                    ) AS current_truth_claim_count,
                    l.company_name,
                    l.normalized_name
                FROM buildings b
                LEFT JOIN building_management bm
                  ON bm.bbl = b.bbl
                 AND bm.is_current = true
                 AND bm.lead_id = :lead_id
                LEFT JOIN leads l ON l.lead_id = bm.lead_id
                WHERE upper(coalesce(b.address, '')) = :address
                ORDER BY CASE WHEN bm.lead_id IS NULL THEN 1 ELSE 0 END,
                         bm.updated_at DESC NULLS LAST,
                         bm.id DESC
                LIMIT 1
            """),
            {"lead_id": lead_id, "address": normalized_address},
        )).first()
        if row is not None:
            match = dict(row._mapping)
            local_building_matches[normalized_address] = match
            if match.get("lead_id"):
                address_matches[normalized_address] = match

    evidence_candidates: list[dict[str, Any]] = []
    unmatched_candidates: list[dict[str, Any]] = []
    new_relationship_candidates: list[dict[str, Any]] = []
    claim_groups: dict[str, dict[str, Any]] = {}
    clean_claim_ids: set[str] = set()

    for candidate in MANAGER_EXTERNAL_SOURCE_CANDIDATES:
        candidate_local_addresses = (
            candidate.get("candidate_local_addresses")
            or ([candidate.get("local_address")] if candidate.get("local_address") else [])
        )
        matches = [
            address_matches[_normalize_street_address(address)]
            for address in candidate_local_addresses
            if _normalize_street_address(address) in address_matches
        ]
        if not matches:
            local_building_matches_for_candidate = [
                local_building_matches[_normalize_street_address(address)]
                for address in candidate_local_addresses
                if _normalize_street_address(address) in local_building_matches
            ]
            unmatched_payload = {
                "candidate_id": candidate.get("candidate_id"),
                "candidate_status": candidate.get("candidate_status"),
                "external_address": candidate.get("external_address"),
                "local_address": candidate.get("local_address"),
                "source_name": candidate.get("source_name"),
                "source_type": candidate.get("source_type"),
                "source_family": candidate.get("source_family"),
                "source_record_id": candidate.get("source_record_id"),
                "source_url": candidate.get("source_url"),
                "evidence_role": candidate.get("evidence_role"),
                "evidence_summary": candidate.get("evidence_summary"),
                "source_document_title": candidate.get("source_document_title"),
                "source_document_row_number": candidate.get("source_document_row_number"),
                "manager_name": candidate.get("manager_name"),
                "manager_contact_name": candidate.get("manager_contact_name"),
                "local_building_matches": [
                    {
                        "bbl": match.get("bbl"),
                        "address": match.get("address"),
                        "borough": match.get("borough"),
                        "zip_code": match.get("zip_code"),
                        "unit_count": match.get("unit_count"),
                    }
                    for match in local_building_matches_for_candidate
                ],
                "reason": (
                    "Source matches a local building but no current building-management relationship for the pilot lead."
                    if local_building_matches_for_candidate
                    else "No exact current local building-management match for the pilot lead."
                ),
                "safe_action": (
                    "Review as a possible new relationship claim; do not count it as source overlap for an existing "
                    "ledger fact until the relationship is separately approved and materialized."
                    if local_building_matches_for_candidate
                    else None
                ),
            }
            if local_building_matches_for_candidate:
                for local_match in local_building_matches_for_candidate:
                    local_building_payload = {
                        "bbl": local_match.get("bbl"),
                        "address": local_match.get("address"),
                        "borough": local_match.get("borough"),
                        "zip_code": local_match.get("zip_code"),
                        "unit_count": local_match.get("unit_count"),
                    }
                    relationship_state = {
                        "current_building_management_relationship_count": 1 if local_match.get("lead_id") else 0,
                        "current_truth_claim_count": int(local_match.get("current_truth_claim_count") or 0),
                        "counts_as_current_ledger_overlap": bool(local_match.get("current_truth_claim_count")),
                        "relationship_review_required": not local_match.get("lead_id"),
                    }
                    relationship_template = _manager_evidence_template(candidate, {
                        **local_match,
                        "lead_id": lead_id,
                        "company_name": "Harlem Property Management",
                        "building_management_role": None,
                    })
                    new_relationship_candidates.append({
                        **unmatched_payload,
                        "local_building_match": local_building_payload,
                        "current_relationship_state": relationship_state,
                        "relationship_claim_preview": relationship_template,
                    })
            unmatched_candidates.append(unmatched_payload)
            continue

        for match in matches:
            is_clean = candidate.get("candidate_status") in {"clean_exact_match", "supporting_notice_exact_match"}
            is_reviewable = candidate.get("candidate_status") in {
                "derived_source_review_required",
                "external_web_review_required",
                "operator_document_review_required",
            }
            template = _manager_evidence_template(candidate, match)
            candidate_payload = {
                "candidate_id": candidate.get("candidate_id"),
                "candidate_status": candidate.get("candidate_status"),
                "source_name": candidate.get("source_name"),
                "source_type": candidate.get("source_type"),
                "source_family": candidate.get("source_family"),
                "source_record_id": candidate.get("source_record_id"),
                "source_url": candidate.get("source_url"),
                "observed_at": candidate.get("observed_at"),
                "external_address": candidate.get("external_address"),
                "local_match": {
                    "bbl": match.get("bbl"),
                    "address": match.get("address"),
                    "borough": match.get("borough"),
                    "zip_code": match.get("zip_code"),
                    "unit_count": match.get("unit_count"),
                    "lead_id": match.get("lead_id"),
                    "company_name": match.get("company_name"),
                    "building_management_role": match.get("building_management_role"),
                },
                "evidence_role": candidate.get("evidence_role"),
                "evidence_summary": candidate.get("evidence_summary"),
                "manager_name": candidate.get("manager_name"),
                "manager_contact_name": candidate.get("manager_contact_name"),
                "clean_for_operator_review": bool(is_clean or is_reviewable),
                "independence_warning": (
                    "Treat as role-specific context, not independent operating-manager proof."
                    if (
                        candidate.get("source_family") in NON_MANAGER_PROOF_SOURCE_FAMILIES
                        and candidate.get("source_family") != "hpd_registration_derived"
                    )
                    else "Treat as HPD-derived context, not independent manager proof."
                    if candidate.get("source_family") == "hpd_registration_derived"
                    else None
                ),
                "manual_evidence_template": template,
            }
            evidence_candidates.append(candidate_payload)

            if not (is_clean or is_reviewable):
                continue
            group_key = str(match.get("bbl") or "")
            if not group_key:
                continue
            group = claim_groups.setdefault(group_key, {
                "fact_key": {
                    "subject_type": "lead",
                    "subject_id": match.get("lead_id"),
                    "predicate": "manages_building",
                    "object_type": "building",
                    "object_id": match.get("bbl"),
                    "normalized_value": "manager",
                    "claim_type": "building_management",
                },
                "address": match.get("address"),
                "building_management_role": match.get("building_management_role"),
                "supporting_sources_if_recorded": [],
                "supporting_source_families_if_recorded": [],
                "evidence_candidate_ids": [],
                "manual_evidence_templates": [],
            })
            group["supporting_sources_if_recorded"].append(str(candidate.get("source_name") or ""))
            group["supporting_source_families_if_recorded"].append(str(candidate.get("source_family") or ""))
            group["evidence_candidate_ids"].append(str(candidate.get("candidate_id") or ""))
            group["manual_evidence_templates"].append(template)
            if is_clean:
                clean_claim_ids.add(group_key)

    grouped_claims: list[dict[str, Any]] = []
    for group in claim_groups.values():
        sources = list(dict.fromkeys(group["supporting_sources_if_recorded"]))
        families = list(dict.fromkeys(group["supporting_source_families_if_recorded"]))
        manager_proof_families = [
            family for family in families if family not in NON_MANAGER_PROOF_SOURCE_FAMILIES
        ]
        source_ready = len(sources) >= VERIFICATION_MIN_SUPPORTING_SOURCES
        independently_ready = len(families) >= VERIFICATION_MIN_SUPPORTING_SOURCES
        strict_manager_ready = len(manager_proof_families) >= VERIFICATION_MIN_SUPPORTING_SOURCES
        grouped_claims.append({
            **group,
            "supporting_sources_if_recorded": sources,
            "supporting_source_families_if_recorded": families,
            "manager_proof_source_families_if_recorded": manager_proof_families,
            "supporting_source_count_if_recorded": len(sources),
            "independent_source_family_count_if_recorded": len(families),
            "manager_proof_source_family_count_if_recorded": len(manager_proof_families),
            "source_ready_if_recorded": source_ready,
            "independent_source_ready_if_recorded": independently_ready,
            "strict_manager_source_ready_if_recorded": strict_manager_ready,
            "safe_action": (
                "Preview and record only after operator review; rerun adjudication afterward. "
                "Do not treat current local Agent rows as manager support."
            ),
        })

    source_ready_count = sum(1 for group in grouped_claims if group["source_ready_if_recorded"])
    independent_ready_count = sum(1 for group in grouped_claims if group["independent_source_ready_if_recorded"])
    strict_manager_ready_count = sum(1 for group in grouped_claims if group["strict_manager_source_ready_if_recorded"])
    recordable_templates = _recordable_manager_external_templates(grouped_claims)
    recordable_source_names = sorted({str(template.get("source_name") or "") for template in recordable_templates})
    strict_recordable_templates = _recordable_manager_external_templates([
        group for group in grouped_claims if group["strict_manager_source_ready_if_recorded"]
    ])
    strict_recordable_source_names = sorted({
        str(template.get("source_name") or "") for template in strict_recordable_templates
    })
    strict_recordable_source_families = sorted({
        str((template.get("raw_payload") or {}).get("source_family") or "").strip()
        for template in strict_recordable_templates
        if str((template.get("raw_payload") or {}).get("source_family") or "").strip()
    })
    strict_recordable_manager_proof_source_families = sorted({
        family for family in strict_recordable_source_families if family not in NON_MANAGER_PROOF_SOURCE_FAMILIES
    })
    address_review_candidate_count = sum(
        1 for item in evidence_candidates if item.get("candidate_status") == "address_range_review_required"
    )
    post_recording_simulation = simulate_manager_external_evidence_post_recording(recordable_templates)
    next_source_batches = build_manager_external_next_source_batches(grouped_claims)
    return {
        "dry_run": True,
        "mutations_planned": 0,
        "lead_id": lead_id,
        "candidate_source_count": len(MANAGER_EXTERNAL_SOURCE_CANDIDATES),
        "matched_evidence_candidate_count": len(evidence_candidates),
        "clean_exact_claim_count": len(clean_claim_ids),
        "claim_group_count": len(grouped_claims),
        "source_ready_if_recorded_count": source_ready_count,
        "independent_source_ready_if_recorded_count": independent_ready_count,
        "strict_manager_source_ready_if_recorded_count": strict_manager_ready_count,
        "excluded_manager_proof_source_families": sorted(NON_MANAGER_PROOF_SOURCE_FAMILIES),
        "review_required_count": sum(
            1 for item in evidence_candidates if item.get("candidate_status") != "clean_exact_match"
        ),
        "unmatched_candidate_count": len(unmatched_candidates),
        "new_relationship_candidate_count": len(new_relationship_candidates),
        "policy": {
            "execution_policy": "Read-only acquisition preview. Manual evidence capture still requires explicit approval.",
            "freshness_warning": (
                "Most public filings are older than the 120-day verified threshold; source overlap alone "
                "does not make the manager fact verified."
            ),
            "role_policy": "External manager evidence cannot make HPD Agent rows safe to use as manager evidence.",
        },
        "manual_evidence_batch_preview": {
            "dry_run": True,
            "allowed_execute": False,
            "template_count": len(recordable_templates),
            "claim_group_count": independent_ready_count,
            "planned_upsert_count": len(recordable_templates) * 3,
            "source_names": recordable_source_names,
            "recommended_strict_manager_proof_batch": {
                "dry_run": True,
                "allowed_execute": False,
                "template_count": len(strict_recordable_templates),
                "claim_group_count": strict_manager_ready_count,
                "planned_upsert_count": len(strict_recordable_templates) * 3,
                "rollback_preview": {
                    "estimated_claim_count": len(strict_recordable_templates),
                    "estimated_evidence_count": len(strict_recordable_templates),
                    "estimated_confidence_snapshot_count": len(strict_recordable_templates),
                    "estimated_manifest_entry_count": len(strict_recordable_templates) * 3,
                    "note": (
                        "Exact new-vs-existing rollback counts are produced by "
                        "truth_manager_external_evidence_batch.py after checking the local ledger."
                    ),
                },
                "source_names": strict_recordable_source_names,
                "source_families": strict_recordable_source_families,
                "manager_proof_source_families": strict_recordable_manager_proof_source_families,
                "command": (
                    "python scripts/truth_manager_external_evidence_batch.py "
                    "--strict-manager-proof-only --execute --confirm-execute --indent 2"
                ),
                "safe_action": (
                    "Recommended first approval packet: records only strict manager-proof groups, "
                    "then requires adjudication, health, and completion-audit reruns."
                ),
            },
            "excluded_address_review_candidate_count": address_review_candidate_count,
            "required_execute_params": {
                "execute": True,
                "confirm_execute": True,
            },
            "command": (
                "python scripts/truth_manager_external_evidence_batch.py "
                "--execute --confirm-execute --indent 2"
            ),
            "safe_action": (
                "Review the batch preview only. Recording requires explicit operator approval and "
                "must be followed by adjudication, health, and completion-audit reruns."
            ),
        },
        "post_recording_simulation": post_recording_simulation,
        "next_source_batches": next_source_batches,
        "claim_groups": grouped_claims[:bounded_limit],
        "evidence_candidates": evidence_candidates[:bounded_limit],
        "unmatched_candidates": unmatched_candidates[:bounded_limit],
        "new_relationship_candidates": new_relationship_candidates[:bounded_limit],
    }


async def load_operator_confirmed_management_preview(
    session: AsyncSession,
    *,
    limit: int = 20,
) -> dict[str, Any]:
    """Preview first-hand management confirmations without writing ledger rows."""
    bounded_limit = min(max(int(limit or 20), 1), 50)
    address_values = sorted({
        _normalize_street_address(address)
        for candidate in OPERATOR_CONFIRMED_MANAGEMENT_CANDIDATES
        for address in candidate.get("address_aliases") or []
        if str(address or "").strip()
    })
    lead_values = sorted({
        str(name or "").upper().strip()
        for candidate in OPERATOR_CONFIRMED_MANAGEMENT_CANDIDATES
        for name in candidate.get("target_lead_names") or []
        if str(name or "").strip()
    })

    building_rows = await session.execute(
        text("""
            SELECT bbl, address, borough, zip_code, unit_count, building_class
            FROM buildings
            WHERE UPPER(COALESCE(address, '')) = ANY(:addresses)
            ORDER BY address, bbl
        """),
        {"addresses": address_values},
    )
    buildings_by_address = {
        _normalize_street_address(row._mapping.get("address")): dict(row._mapping)
        for row in building_rows
    }

    lead_rows = await session.execute(
        text("""
            SELECT lead_id, company_name, normalized_name, entity_type, portfolio_size, total_units
            FROM leads
            WHERE UPPER(COALESCE(normalized_name, '')) = ANY(:lead_names)
               OR UPPER(COALESCE(company_name, '')) = ANY(:lead_names)
            ORDER BY normalized_name, lead_id
        """),
        {"lead_names": lead_values},
    )
    leads_by_name: dict[str, dict[str, Any]] = {}
    for row in lead_rows:
        lead = dict(row._mapping)
        for key in (lead.get("normalized_name"), lead.get("company_name")):
            normalized = str(key or "").upper().strip()
            if normalized in lead_values:
                leads_by_name.setdefault(normalized, lead)

    bbls = sorted({str(building.get("bbl") or "") for building in buildings_by_address.values() if building.get("bbl")})
    current_manager_rows = await session.execute(
        text("""
            SELECT
                bm.id AS building_management_id,
                bm.bbl,
                bm.lead_id,
                bm.role,
                bm.registration_start,
                bm.registration_end,
                l.company_name,
                l.normalized_name,
                l.entity_type
            FROM building_management bm
            LEFT JOIN leads l ON l.lead_id = bm.lead_id
            WHERE bm.is_current = true
              AND bm.bbl = ANY(:bbls)
            ORDER BY bm.bbl, bm.id
        """),
        {"bbls": bbls},
    )
    current_managers_by_bbl: dict[str, list[dict[str, Any]]] = {}
    for row in current_manager_rows:
        data = dict(row._mapping)
        current_managers_by_bbl.setdefault(str(data.get("bbl") or ""), []).append(data)

    truth_rows = await session.execute(
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
                c.actionability_level,
                c.observed_at,
                COUNT(e.evidence_id) AS evidence_count,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT e.source_name), NULL) AS source_names,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT e.support_status), NULL) AS support_statuses,
                l.company_name,
                l.normalized_name
            FROM truth_claims c
            LEFT JOIN truth_evidence e ON e.claim_id = c.claim_id
            LEFT JOIN leads l ON l.lead_id = c.subject_id AND c.subject_type = 'lead'
            WHERE c.current_flag = true
              AND c.predicate = 'manages_building'
              AND c.claim_type = 'building_management'
              AND c.object_id = ANY(:bbls)
            GROUP BY
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
                c.actionability_level,
                c.observed_at,
                l.company_name,
                l.normalized_name
            ORDER BY c.object_id, c.subject_id, c.claim_id
        """),
        {"bbls": bbls},
    )
    truth_claims_by_bbl: dict[str, list[dict[str, Any]]] = {}
    for row in truth_rows:
        data = dict(row._mapping)
        truth_claims_by_bbl.setdefault(str(data.get("object_id") or ""), []).append(data)

    candidates: list[dict[str, Any]] = []
    operator_support_templates: list[dict[str, Any]] = []
    second_source_templates: list[dict[str, Any]] = []
    contradiction_templates: list[dict[str, Any]] = []
    second_source_proposals: list[dict[str, Any]] = []
    unmatched_candidates: list[dict[str, Any]] = []
    conflict_count = 0
    second_sources_by_operator_candidate: dict[str, list[dict[str, Any]]] = {}
    for source_candidate in OPERATOR_CONFIRMED_SECOND_SOURCE_CANDIDATES:
        operator_candidate_id = str(source_candidate.get("operator_candidate_id") or "")
        second_sources_by_operator_candidate.setdefault(operator_candidate_id, []).append(source_candidate)

    for candidate in OPERATOR_CONFIRMED_MANAGEMENT_CANDIDATES:
        building = None
        for address in candidate.get("address_aliases") or []:
            building = buildings_by_address.get(_normalize_street_address(address))
            if building:
                break
        lead = None
        for lead_name in candidate.get("target_lead_names") or []:
            lead = leads_by_name.get(str(lead_name or "").upper().strip())
            if lead:
                break

        base = {
            "candidate_id": candidate.get("candidate_id"),
            "user_address": candidate.get("user_address"),
            "manager_name_supplied": candidate.get("manager_name"),
            "observed_at": candidate.get("observed_at"),
            "source_name": "outreach_confirmed",
            "source_type": "operator_first_hand_confirmation",
            "source_family": "operator_confirmed",
        }
        if not building or not lead:
            unmatched_candidates.append({
                **base,
                "matched_building": building,
                "matched_lead": lead,
                "reason": "Could not match both the canonical building and canonical manager lead.",
                "safe_action": "Resolve canonical identity before creating any claim/evidence record.",
            })
            continue

        bbl = str(building.get("bbl") or "")
        lead_id = str(lead.get("lead_id") or "")
        current_managers = current_managers_by_bbl.get(bbl, [])
        current_truth_claims = truth_claims_by_bbl.get(bbl, [])
        conflicting_current_managers = [
            item for item in current_managers if str(item.get("lead_id") or "") != lead_id
        ]
        conflicting_truth_claims = [
            item for item in current_truth_claims if str(item.get("subject_id") or "") != lead_id
        ]
        matching_truth_claims = [
            item for item in current_truth_claims if str(item.get("subject_id") or "") == lead_id
        ]
        current_source_names = sorted({
            str(source)
            for claim in matching_truth_claims
            for source in _as_list(claim.get("source_names"))
            if str(source or "").strip()
        })
        current_supporting_evidence_count = sum(
            _as_int(claim.get("evidence_count"))
            for claim in matching_truth_claims
            if "supports" in {str(status) for status in _as_list(claim.get("support_statuses"))}
        )
        current_relationship_state = {
            "current_building_management_relationship_count": len(current_managers),
            "current_matching_building_management_relationship_count": len(current_managers)
            - len(conflicting_current_managers),
            "current_truth_claim_count": len(current_truth_claims),
            "current_matching_truth_claim_count": len(matching_truth_claims),
            "conflicting_current_manager_count": len(conflicting_current_managers),
            "conflicting_truth_claim_count": len(conflicting_truth_claims),
            "current_source_names": current_source_names,
            "current_supporting_source_count": len(current_source_names),
            "current_supporting_evidence_count": current_supporting_evidence_count,
            "current_ledger_source_ready": (
                len(current_source_names) >= VERIFICATION_MIN_SUPPORTING_SOURCES
                and current_supporting_evidence_count >= VERIFICATION_MIN_SUPPORTING_EVIDENCE
            ),
            "has_operator_confirmed_evidence_recorded": "outreach_confirmed" in current_source_names,
            "safe_action": (
                "Already-recorded ledger evidence can be adjudicated, but it still cannot be marked verified "
                "unless confidence, freshness, contradiction, and source thresholds pass."
                if current_source_names
                else "No current ledger evidence is recorded for this operator seed; use it for source acquisition only."
            ),
        }
        support_template = _operator_confirmation_template(candidate, building, lead)
        conflict_templates = [
            _operator_confirmation_template(
                candidate,
                building,
                {
                    "lead_id": claim.get("subject_id"),
                    "company_name": claim.get("company_name"),
                    "normalized_name": claim.get("normalized_name"),
                },
                support_status="contradicts",
                contradicted_claim_id=str(claim.get("claim_id") or ""),
            )
            for claim in conflicting_truth_claims
            if claim.get("subject_id")
        ]
        contradiction_templates.extend(conflict_templates)
        has_conflict = bool(conflicting_current_managers or conflicting_truth_claims)
        if has_conflict:
            conflict_count += 1
        matching_second_sources = second_sources_by_operator_candidate.get(str(candidate.get("candidate_id") or ""), [])
        candidate_second_source_templates = [
            _operator_second_source_template(candidate, source_candidate, building, lead)
            for source_candidate in matching_second_sources
        ]
        operator_support_templates.append(support_template)
        second_source_templates.extend(candidate_second_source_templates)
        post_recording_candidate_templates = [support_template, *candidate_second_source_templates]
        candidate_simulation = simulate_manager_external_evidence_post_recording(post_recording_candidate_templates)
        candidate_source_ready = bool(candidate_simulation["source_ready_fact_group_count"])
        candidate_strict_ready = bool(candidate_simulation["strict_manager_source_ready_fact_group_count"])
        candidate_verified_safe = bool(candidate_simulation["safe_to_mark_verified_count"])
        supporting_sources_if_recorded = list(dict.fromkeys(
            str(template.get("source_name") or "")
            for template in post_recording_candidate_templates
            if str(template.get("source_name") or "").strip()
        ))
        supporting_source_families_if_recorded = list(dict.fromkeys(
            str((template.get("raw_payload") or {}).get("source_family") or "")
            for template in post_recording_candidate_templates
            if str((template.get("raw_payload") or {}).get("source_family") or "").strip()
        ))
        manager_proof_source_families_if_recorded = [
            family
            for family in supporting_source_families_if_recorded
            if family not in NON_MANAGER_PROOF_SOURCE_FAMILIES
        ]
        manager_proof_gap = _operator_manager_proof_gap(
            candidate_source_ready=candidate_source_ready,
            candidate_strict_ready=candidate_strict_ready,
            supporting_source_families=supporting_source_families_if_recorded,
            manager_proof_source_families=manager_proof_source_families_if_recorded,
        )

        second_source_proposals.append({
            "bbl": bbl,
            "address": building.get("address"),
            "manager_lead_id": lead_id,
            "manager_name": lead.get("company_name") or lead.get("normalized_name") or candidate.get("manager_name"),
            "current_relationship_state": current_relationship_state,
            "existing_manager_proof_source_families": manager_proof_source_families_if_recorded,
            "existing_source_families_if_recorded": supporting_source_families_if_recorded,
            "supporting_sources_if_recorded": supporting_sources_if_recorded,
            "source_ready_if_recorded": candidate_source_ready,
            "strict_manager_source_ready_if_recorded": candidate_strict_ready,
            "verified_safe_if_recorded": candidate_verified_safe,
            "second_source_templates": candidate_second_source_templates,
            "missing_manager_proof_source_family_count": max(
                0,
                VERIFICATION_MIN_SUPPORTING_SOURCES - len(manager_proof_source_families_if_recorded),
            ),
            "strict_manager_gap_status": manager_proof_gap["strict_manager_gap_status"],
            "strict_manager_gap_reason": manager_proof_gap["strict_manager_gap_reason"],
            "next_required_manager_proof": manager_proof_gap["next_required_manager_proof"],
            "suggested_source_families": [
                family
                for family in [
                    "company_website",
                    "external_web_profile",
                    "hpd_management_company",
                    "ny_dos",
                    "outreach_confirmed",
                ]
                if family not in manager_proof_source_families_if_recorded
            ],
            "search_queries": _operator_source_search_queries(candidate.get("manager_name"), building.get("address")),
            "source_targets": _operator_source_targets(candidate.get("manager_name"), building.get("address")),
            "safe_action": (
                "Review the attached second-source templates before recording; do not mark verified "
                "because previewed source overlap still needs approval, recording, and adjudication."
                if candidate_source_ready
                else "Use the operator-confirmed fact as a seed for second-source acquisition only; "
                "do not mark the single-source claim verified."
            ),
        })
        candidates.append({
            **base,
            "matched_building": building,
            "matched_lead": lead,
            "current_building_management": current_managers,
            "current_truth_claims": current_truth_claims,
            "current_relationship_state": current_relationship_state,
            "conflicting_current_manager_count": len(conflicting_current_managers),
            "conflicting_truth_claim_count": len(conflicting_truth_claims),
            "review_queue": "conflicting_evidence" if has_conflict else "new_relationship_review",
            "supporting_sources_if_recorded": supporting_sources_if_recorded,
            "supporting_source_families_if_recorded": supporting_source_families_if_recorded,
            "manager_proof_source_families_if_recorded": manager_proof_source_families_if_recorded,
            "source_ready_if_recorded": candidate_source_ready,
            "strict_manager_source_ready_if_recorded": candidate_strict_ready,
            "strict_manager_gap_status": manager_proof_gap["strict_manager_gap_status"],
            "strict_manager_gap_reason": manager_proof_gap["strict_manager_gap_reason"],
            "missing_manager_proof_source_family_count": manager_proof_gap[
                "missing_manager_proof_source_family_count"
            ],
            "next_required_manager_proof": manager_proof_gap["next_required_manager_proof"],
            "verified_safe_if_recorded": candidate_verified_safe,
            "manual_evidence_template": support_template,
            "second_source_templates": candidate_second_source_templates,
            "contradiction_templates": conflict_templates,
            "safe_action": (
                "Route conflict to review; record support/contradiction evidence only after explicit approval."
                if has_conflict
                else "Preview-only new manager relationship. Record only after explicit approval and adjudicate after recording."
            ),
        })

    support_templates = [*operator_support_templates, *second_source_templates]
    post_recording_simulation = simulate_manager_external_evidence_post_recording(support_templates)
    return {
        "dry_run": True,
        "mutations_planned": 0,
        "source_name": "outreach_confirmed",
        "source_type": "operator_first_hand_confirmation",
        "source_family": "operator_confirmed",
        "candidate_count": len(OPERATOR_CONFIRMED_MANAGEMENT_CANDIDATES),
        "matched_candidate_count": len(candidates),
        "unmatched_candidate_count": len(unmatched_candidates),
        "new_relationship_candidate_count": sum(
            1
            for candidate in candidates
            if not candidate.get("current_building_management") and not candidate.get("current_truth_claims")
        ),
        "conflict_candidate_count": conflict_count,
        "operator_confirmation_template_count": len(operator_support_templates),
        "second_source_template_count": len(second_source_templates),
        "manual_evidence_template_count": len(support_templates),
        "contradiction_template_count": len(contradiction_templates),
        "planned_upsert_count": (len(support_templates) + len(contradiction_templates)) * 3,
        "source_ready_if_recorded_count": post_recording_simulation["source_ready_fact_group_count"],
        "independent_source_ready_if_recorded_count": post_recording_simulation[
            "independent_source_ready_fact_group_count"
        ],
        "strict_manager_source_ready_if_recorded_count": post_recording_simulation[
            "strict_manager_source_ready_fact_group_count"
        ],
        "verified_safe_if_recorded_count": post_recording_simulation["safe_to_mark_verified_count"],
        "policy": {
            "execution_policy": "Read-only preview. Recording requires explicit approval and confirm_execute=true.",
            "single_source_policy": (
                "Operator-confirmed evidence is high-quality first-hand evidence, but a single source is not verified. "
                "Previewed second-source templates do not affect the ledger until explicitly recorded."
            ),
            "conflict_policy": (
                "If existing current manager claims conflict, record contradictions/review items rather than "
                "overwriting or silently replacing the existing claim."
            ),
            "manager_proof_policy": (
                "RentHistory/HPD-registration-derived context can create broad source overlap but is excluded "
                "from strict manager-proof source-family counts."
            ),
        },
        "post_recording_simulation": post_recording_simulation,
        "second_source_seed_batches": {
            "dry_run": True,
            "mutations_planned": 0,
            "candidate_count": len(second_source_proposals),
            "template_count": len(second_source_templates),
            "source_ready_if_recorded_count": post_recording_simulation["source_ready_fact_group_count"],
            "strict_manager_source_ready_if_recorded_count": post_recording_simulation[
                "strict_manager_source_ready_fact_group_count"
            ],
            "proposals": second_source_proposals,
            "source_boundary_notes": [
                (
                    "Operator-confirmed evidence is a first-hand source family, but it still needs an "
                    "independent second family before any claim is source-ready."
                ),
                (
                    "RentHistory/HPD-registration-derived pages can create broad source overlap, but they "
                    "are excluded from strict manager-proof counts because they derive from the same HPD "
                    "registration ecosystem."
                ),
                (
                    "Company websites, listing pages, NY DOS records, court records, and public profiles "
                    "only support a manages_building claim when they name the exact property and state a "
                    "management or managing-agent relationship."
                ),
                (
                    "Multiple public listing sites can add corroborating evidence rows, but when they derive "
                    "from listing/MLS-style data they stay in the same real_estate_listing family for strict "
                    "manager-proof counting."
                ),
            ],
            "reviewed_source_findings": OPERATOR_CONFIRMED_REVIEWED_SOURCE_FINDINGS,
            "safe_action": (
                "Inspect exact-property second-source templates before recording. Source-ready preview counts "
                "are not ledger truth until approved and written."
            ),
        },
        "manual_evidence_templates": support_templates[:bounded_limit],
        "contradiction_templates": contradiction_templates[:bounded_limit],
        "candidates": candidates[:bounded_limit],
        "unmatched_candidates": unmatched_candidates[:bounded_limit],
        "safe_action": (
            "Preview only. Do not execute/write these operator-confirmed facts without explicit post-boundary approval."
        ),
    }


async def load_manager_source_bridge_preview(
    session: AsyncSession,
    *,
    lead_id: str = "0ff794d3ba2d",
    sample_limit: int = 10,
) -> dict[str, Any]:
    """Explain whether local sources can prove operating-manager overlap.

    This is deliberately stricter than role overlap: registered-agent and
    site-manager evidence are useful, but they do not verify the company as the
    operating manager unless a manager-specific source also supports the fact.
    """
    bounded_sample_limit = min(max(int(sample_limit or 10), 1), 25)
    role_rows = await session.execute(
        text("""
            SELECT 'all_current' AS scope, COALESCE(NULLIF(role, ''), '(blank)') AS role, COUNT(*)::int AS count
            FROM building_management
            WHERE is_current = true
            GROUP BY COALESCE(NULLIF(role, ''), '(blank)')
            UNION ALL
            SELECT 'pilot_current' AS scope, COALESCE(NULLIF(role, ''), '(blank)') AS role, COUNT(*)::int AS count
            FROM building_management
            WHERE is_current = true
              AND lead_id = :lead_id
            GROUP BY COALESCE(NULLIF(role, ''), '(blank)')
            ORDER BY scope, count DESC, role
        """),
        {"lead_id": lead_id},
    )
    role_counts: dict[str, dict[str, int]] = {"all_current": {}, "pilot_current": {}}
    for row in role_rows:
        data = dict(row._mapping)
        scope = str(data.get("scope") or "unknown")
        role_counts.setdefault(scope, {})[str(data.get("role") or "(blank)")] = _as_int(data.get("count"))

    source_row = (await session.execute(
        text("""
            SELECT
                (SELECT COUNT(*) FROM dos_cache)::int AS dos_cache_records,
                (SELECT COUNT(*) FROM enrichment_results WHERE source IN ('web_crawl', 'company_website'))::int AS company_website_records,
                (SELECT COUNT(*) FROM outreach_events)::int AS outreach_event_records,
                (SELECT COUNT(*) FROM outreach_events
                 WHERE LOWER(COALESCE(outcome, '') || ' ' || COALESCE(notes, '')) LIKE '%confirmed_manager%'
                    OR LOWER(COALESCE(outcome, '') || ' ' || COALESCE(notes, '')) LIKE '%confirmed management%'
                    OR LOWER(COALESCE(outcome, '') || ' ' || COALESCE(notes, '')) LIKE '%we manage%'
                    OR LOWER(COALESCE(outcome, '') || ' ' || COALESCE(notes, '')) LIKE '%they manage%')::int
                    AS outreach_confirmed_manager_events
        """)
    )).first()
    source_counts = dict(source_row._mapping) if source_row is not None else {}

    contact_rows = await session.execute(
        text("""
            SELECT
                bm.id AS building_management_id,
                bm.lead_id,
                bm.bbl,
                COALESCE(NULLIF(bm.role, ''), 'manager') AS building_management_role,
                l.normalized_name AS lead_normalized_name,
                l.company_name AS lead_company_name,
                l.agent_name AS lead_agent_name,
                l.owner_name AS lead_owner_name,
                bc.id AS hpd_contact_id,
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
                bc.updated_at AS hpd_contact_observed_at
            FROM building_management bm
            JOIN leads l ON l.lead_id = bm.lead_id
            LEFT JOIN building_contacts bc
              ON bc.bbl = bm.bbl
             AND bc.contact_type IN ('Agent', 'ManagementCompany', 'SiteManager')
            WHERE bm.is_current = true
              AND bm.lead_id = :lead_id
            ORDER BY bm.updated_at DESC NULLS LAST, bm.id DESC, bc.contact_type, bc.id
        """),
        {"lead_id": lead_id},
    )

    relationship_count = 0
    current_manager_role_relationship_count = 0
    registered_agent_bridge_count = 0
    hpd_management_company_strict_match_count = 0
    hpd_site_manager_row_count = 0
    hpd_site_manager_strict_identity_match_count = 0
    manager_source_ready_count = 0
    samples: list[dict[str, Any]] = []
    seen_relationships: set[str] = set()
    manager_ready_relationships: set[str] = set()
    registered_agent_relationships: set[str] = set()
    management_company_relationships: set[str] = set()

    for row in contact_rows:
        data = dict(row._mapping)
        bm_id = str(data.get("building_management_id") or "")
        if not bm_id:
            continue
        if bm_id not in seen_relationships:
            seen_relationships.add(bm_id)
            relationship_count += 1
            bm_shape = _building_management_role_claim_shape(data.get("building_management_role")) or {}
            if bm_shape.get("predicate") == "manages_building":
                current_manager_role_relationship_count += 1
        if data.get("hpd_contact_id") is None:
            continue

        lead_keys = _filter_verification_name_keys({
            key
            for key in (
                _verification_name_key(data.get("lead_normalized_name")),
                _verification_name_key(data.get("lead_company_name")),
                _verification_name_key(data.get("lead_agent_name")),
                _verification_name_key(data.get("lead_owner_name")),
            )
            if key
        })
        display_name = _contact_display_name(data)
        contact_key = _verification_name_key(display_name)
        identity_matches = bool(contact_key and contact_key in lead_keys)
        contact_type = str(data.get("contact_type") or "")
        hpd_shape = _hpd_contact_role_claim(data) or {}
        bm_shape = _building_management_role_claim_shape(data.get("building_management_role")) or {}
        role_matches = bool(hpd_shape and bm_shape and hpd_shape.get("predicate") == bm_shape.get("predicate"))

        if contact_type == "SiteManager":
            hpd_site_manager_row_count += 1
            if identity_matches:
                hpd_site_manager_strict_identity_match_count += 1
        if contact_type == "Agent" and identity_matches and role_matches:
            registered_agent_relationships.add(bm_id)
        if contact_type == "ManagementCompany" and identity_matches:
            hpd_management_company_strict_match_count += 1
            management_company_relationships.add(bm_id)
            if role_matches and bm_shape.get("predicate") == "manages_building":
                manager_ready_relationships.add(bm_id)

        if len(samples) < bounded_sample_limit and contact_type in {"Agent", "ManagementCompany", "SiteManager"}:
            samples.append({
                "bbl": str(data.get("bbl") or ""),
                "building_management_role": data.get("building_management_role"),
                "contact_type": contact_type,
                "display_name": display_name,
                "verification_key": contact_key,
                "strict_identity_matches_lead": identity_matches,
                "role_matches_building_management": role_matches,
                "hpd_predicate": hpd_shape.get("predicate"),
                "safe_action": (
                    "Can support registered-agent overlap only."
                    if contact_type == "Agent"
                    else "SiteManager is person/site evidence; it does not verify the company as operating manager."
                    if contact_type == "SiteManager"
                    else "Can support manager overlap only when the building-management role is manager."
                ),
            })

    registered_agent_bridge_count = len(registered_agent_relationships)
    manager_source_ready_count = len(manager_ready_relationships)
    source_counts = {key: _as_int(value) for key, value in source_counts.items()}
    blocking_reasons: list[str] = []
    if current_manager_role_relationship_count == 0:
        blocking_reasons.append("current_building_management_rows_are_not_manager_role")
    if not management_company_relationships:
        blocking_reasons.append("no_strict_hpd_management_company_matches")
    if source_counts.get("company_website_records", 0) == 0:
        blocking_reasons.append("no_local_company_website_evidence")
    if source_counts.get("outreach_confirmed_manager_events", 0) == 0:
        blocking_reasons.append("no_outreach_confirmed_manager_evidence")
    if source_counts.get("dos_cache_records", 0) == 0:
        blocking_reasons.append("no_local_ny_dos_cache_evidence")

    return {
        "dry_run": True,
        "mutations_planned": 0,
        "lead_id": lead_id,
        "relationship_count": relationship_count,
        "role_counts": role_counts,
        "source_counts": source_counts,
        "registered_agent_bridge_count": registered_agent_bridge_count,
        "current_manager_role_relationship_count": current_manager_role_relationship_count,
        "hpd_management_company_strict_match_count": hpd_management_company_strict_match_count,
        "hpd_site_manager_row_count": hpd_site_manager_row_count,
        "hpd_site_manager_strict_identity_match_count": hpd_site_manager_strict_identity_match_count,
        "manager_source_ready_if_materialized_count": manager_source_ready_count,
        "blocking_reasons": blocking_reasons,
        "samples": samples,
        "business_readiness_note": (
            "Local evidence can support registered-agent overlap, but it cannot yet verify operating-manager facts."
            if manager_source_ready_count == 0
            else "Local evidence includes at least one manager-specific overlap candidate."
        ),
        "safe_action": (
            "Collect a manager-specific independent source such as company website, outreach-confirmed response, "
            "NY DOS officer/registered-agent context, or true HPD ManagementCompany evidence before marking manager facts verified."
        ),
    }


async def load_role_source_overlap_pilot(
    session: AsyncSession,
    *,
    lead_id: str = "0ff794d3ba2d",
    limit: int = 20,
) -> dict[str, Any]:
    """Preview role-specific source overlap for a narrow lead/building slice."""
    bounded_limit = min(max(int(limit or 20), 1), 50)
    rows = await session.execute(
        text("""
            WITH pilot_bm AS (
                SELECT
                    bm.id AS building_management_id,
                    bm.lead_id,
                    bm.bbl,
                    COALESCE(NULLIF(bm.role, ''), 'manager') AS building_management_role,
                    bm.updated_at AS building_management_observed_at,
                    l.normalized_name AS lead_normalized_name,
                    l.company_name AS lead_company_name,
                    l.agent_name AS lead_agent_name,
                    l.owner_name AS lead_owner_name
                FROM building_management bm
                JOIN leads l ON l.lead_id = bm.lead_id
                WHERE bm.is_current = true
                  AND bm.lead_id = :lead_id
                ORDER BY bm.updated_at DESC NULLS LAST, bm.id DESC
            )
            SELECT
                pbm.*,
                bc.id AS hpd_contact_id,
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
                bc.updated_at AS hpd_contact_observed_at
            FROM pilot_bm pbm
            LEFT JOIN building_contacts bc
              ON bc.bbl = pbm.bbl
             AND bc.contact_type IN (
                'Agent',
                'ManagementCompany',
                'Owner',
                'CorporateOwner',
                'IndividualOwner',
                'HeadOfficer',
                'Officer',
                'Shareholder',
                'SiteManager'
             )
            ORDER BY pbm.building_management_observed_at DESC NULLS LAST,
                     pbm.building_management_id DESC,
                     bc.contact_type,
                     bc.id
        """),
        {"lead_id": lead_id},
    )

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        data = dict(row._mapping)
        bm_id = str(data.get("building_management_id") or "")
        if not bm_id:
            continue
        item = grouped.setdefault(bm_id, {
            "lead_id": data.get("lead_id"),
            "bbl": str(data.get("bbl") or ""),
            "building_management_id": data.get("building_management_id"),
            "building_management_role": data.get("building_management_role"),
            "building_management_shape": _building_management_role_claim_shape(data.get("building_management_role")),
            "lead_verification_keys": sorted(_filter_verification_name_keys({
                key
                for key in (
                    _verification_name_key(data.get("lead_normalized_name")),
                    _verification_name_key(data.get("lead_company_name")),
                    _verification_name_key(data.get("lead_agent_name")),
                    _verification_name_key(data.get("lead_owner_name")),
                )
                if key
            })),
            "lead_broad_dedupe_keys": sorted({
                key
                for key in (
                    _name_group(data.get("lead_normalized_name")),
                    _name_group(data.get("lead_company_name")),
                    _name_group(data.get("lead_agent_name")),
                    _name_group(data.get("lead_owner_name")),
                )
                if key
            }),
            "matched_role_contacts": [],
            "adjacent_role_contacts": [],
            "blocked_contacts": [],
        })
        if data.get("hpd_contact_id") is None:
            continue
        display_name = _contact_display_name({
            "id": data.get("hpd_contact_id"),
            "corporation_name": data.get("corporation_name"),
            "first_name": data.get("first_name"),
            "last_name": data.get("last_name"),
            "description": data.get("description"),
        })
        hpd_shape = _hpd_contact_role_claim(data)
        contact_key = _verification_name_key(display_name)
        broad_key = _name_group(display_name)
        role_matches = (
            bool(hpd_shape)
            and bool(item["building_management_shape"])
            and hpd_shape["predicate"] == item["building_management_shape"]["predicate"]
        )
        identity_matches = bool(contact_key and contact_key in set(item["lead_verification_keys"]))
        contact_payload = {
            "hpd_contact_id": data.get("hpd_contact_id"),
            "contact_type": data.get("contact_type"),
            "display_name": display_name,
            "verification_key": contact_key,
            "broad_dedupe_key": broad_key,
            "predicate": hpd_shape.get("predicate") if hpd_shape else None,
            "claim_type": hpd_shape.get("claim_type") if hpd_shape else None,
            "role_matches_building_management": role_matches,
            "strict_identity_matches_lead": identity_matches,
        }
        if role_matches and identity_matches:
            item["matched_role_contacts"].append(contact_payload)
        elif identity_matches:
            item["adjacent_role_contacts"].append(contact_payload)
        else:
            item["blocked_contacts"].append(contact_payload)

    relationship_summaries: list[dict[str, Any]] = []
    source_ready_count = 0
    multi_source_count = 0
    management_source_ready_count = 0
    registered_agent_source_ready_count = 0
    exact_claim_count_by_predicate: dict[str, int] = {}
    for item in grouped.values():
        bm_shape = item.get("building_management_shape") or {}
        predicate = str(bm_shape.get("predicate") or "unknown")
        exact_claim_count_by_predicate[predicate] = exact_claim_count_by_predicate.get(predicate, 0) + 1
        supporting_sources = ["building_management"]
        if item["matched_role_contacts"]:
            supporting_sources.append("hpd_contacts")
        supporting_sources = list(dict.fromkeys(supporting_sources))
        source_ready = len(supporting_sources) >= VERIFICATION_MIN_SUPPORTING_SOURCES
        if len(supporting_sources) >= 2:
            multi_source_count += 1
        if source_ready:
            source_ready_count += 1
            if bm_shape.get("predicate") == "manages_building":
                management_source_ready_count += 1
            if bm_shape.get("predicate") == "registered_agent_for_building":
                registered_agent_source_ready_count += 1
        relationship_summaries.append({
            "fact_key": {
                "subject_type": "lead",
                "subject_id": item["lead_id"],
                "predicate": bm_shape.get("predicate"),
                "object_type": "building",
                "object_id": item["bbl"],
                "normalized_value": bm_shape.get("normalized_value"),
                "claim_type": bm_shape.get("claim_type"),
            },
            "building_management_role": item["building_management_role"],
            "lead_verification_keys": item["lead_verification_keys"],
            "lead_broad_dedupe_keys": item["lead_broad_dedupe_keys"],
            "supporting_sources_if_materialized": supporting_sources,
            "supporting_source_count_if_materialized": len(supporting_sources),
            "source_ready_if_materialized": source_ready,
            "safe_action": (
                "Use as registered-agent/legal verification only; do not treat as operating-manager proof."
                if bm_shape.get("predicate") == "registered_agent_for_building"
                else "May support management verification only when the matched role is ManagementCompany/manager and remains contradiction-free."
            ),
            "matched_role_contacts": item["matched_role_contacts"][:3],
            "adjacent_role_contacts": item["adjacent_role_contacts"][:3],
            "blocked_contact_count": len(item["blocked_contacts"]),
        })

    broad_key_warning = None
    harlem_broad = _name_group("HARLEM PROPERTY MANAGEMENT")
    harlem_strict = _verification_name_key("HARLEM PROPERTY MANAGEMENT")
    if harlem_broad != harlem_strict:
        broad_key_warning = (
            f"Broad dedupe key collapses HARLEM PROPERTY MANAGEMENT to {harlem_broad!r}; "
            f"verification uses {harlem_strict!r} so HARLEM-only matches cannot verify a role."
        )

    return {
        "dry_run": True,
        "mutations_planned": 0,
        "lead_id": lead_id,
        "limit": bounded_limit,
        "scope_relationship_count": len(relationship_summaries),
        "sampled_relationship_count": min(len(relationship_summaries), bounded_limit),
        "multi_source_if_materialized_count": multi_source_count,
        "source_ready_if_materialized_count": source_ready_count,
        "management_source_ready_if_materialized_count": management_source_ready_count,
        "registered_agent_source_ready_if_materialized_count": registered_agent_source_ready_count,
        "claim_count_by_predicate_if_materialized": exact_claim_count_by_predicate,
        "identity_policy": {
            "strict_key_example": harlem_strict,
            "broad_dedupe_key_example": harlem_broad,
            "warning": broad_key_warning,
        },
        "business_readiness_note": (
            "Pilot found role-aligned registered-agent overlap, but no management-company overlap."
            if registered_agent_source_ready_count and not management_source_ready_count
            else "Pilot did not find source-ready role overlap."
        ),
        "samples": relationship_summaries[:bounded_limit],
    }


async def load_scaled_role_source_overlap_preview(
    session: AsyncSession,
    *,
    relationship_limit: int = 1000,
    batch_limit: int = 10,
    sample_limit: int = 3,
) -> dict[str, Any]:
    """Rank strict role-specific source-overlap batches across current links."""
    bounded_relationship_limit = min(max(int(relationship_limit or 1000), 1), 5000)
    bounded_batch_limit = min(max(int(batch_limit or 10), 1), 50)
    bounded_sample_limit = min(max(int(sample_limit or 3), 1), 10)
    rows = await session.execute(
        text("""
            WITH scoped_bm AS (
                SELECT
                    bm.id AS building_management_id,
                    bm.lead_id,
                    bm.bbl,
                    COALESCE(NULLIF(bm.role, ''), 'manager') AS building_management_role,
                    bm.updated_at AS building_management_observed_at,
                    l.normalized_name AS lead_normalized_name,
                    l.company_name AS lead_company_name,
                    l.agent_name AS lead_agent_name,
                    l.owner_name AS lead_owner_name
                FROM building_management bm
                JOIN leads l ON l.lead_id = bm.lead_id
                WHERE bm.is_current = true
                ORDER BY bm.updated_at DESC NULLS LAST, bm.id DESC
                LIMIT :relationship_limit
            )
            SELECT
                sbm.*,
                bc.id AS hpd_contact_id,
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
                bc.updated_at AS hpd_contact_observed_at
            FROM scoped_bm sbm
            LEFT JOIN building_contacts bc
              ON bc.bbl = sbm.bbl
             AND bc.contact_type IN (
                'Agent',
                'ManagementCompany',
                'Owner',
                'CorporateOwner',
                'IndividualOwner',
                'HeadOfficer',
                'Officer',
                'Shareholder',
                'SiteManager'
             )
            ORDER BY sbm.building_management_observed_at DESC NULLS LAST,
                     sbm.building_management_id DESC,
                     bc.contact_type,
                     bc.id
        """),
        {"relationship_limit": bounded_relationship_limit},
    )

    relationships: dict[str, dict[str, Any]] = {}
    for row in rows:
        data = dict(row._mapping)
        bm_id = str(data.get("building_management_id") or "")
        if not bm_id:
            continue
        item = relationships.setdefault(bm_id, {
            "lead_id": data.get("lead_id"),
            "lead_name": data.get("lead_company_name") or data.get("lead_normalized_name") or data.get("lead_id"),
            "bbl": str(data.get("bbl") or ""),
            "building_management_role": data.get("building_management_role"),
            "building_management_shape": _building_management_role_claim_shape(data.get("building_management_role")),
            "lead_verification_keys": sorted(_filter_verification_name_keys({
                key
                for key in (
                    _verification_name_key(data.get("lead_normalized_name")),
                    _verification_name_key(data.get("lead_company_name")),
                    _verification_name_key(data.get("lead_agent_name")),
                    _verification_name_key(data.get("lead_owner_name")),
                )
                if key
            })),
            "lead_broad_dedupe_keys": sorted({
                key
                for key in (
                    _name_group(data.get("lead_normalized_name")),
                    _name_group(data.get("lead_company_name")),
                    _name_group(data.get("lead_agent_name")),
                    _name_group(data.get("lead_owner_name")),
                )
                if key
            }),
            "matched_role_contacts": [],
            "adjacent_role_contacts": [],
            "blocked_contact_count": 0,
        })
        if data.get("hpd_contact_id") is None:
            continue
        display_name = _contact_display_name({
            "id": data.get("hpd_contact_id"),
            "corporation_name": data.get("corporation_name"),
            "first_name": data.get("first_name"),
            "last_name": data.get("last_name"),
            "description": data.get("description"),
        })
        hpd_shape = _hpd_contact_role_claim(data)
        contact_key = _verification_name_key(display_name)
        role_matches = (
            bool(hpd_shape)
            and bool(item["building_management_shape"])
            and hpd_shape["predicate"] == item["building_management_shape"]["predicate"]
        )
        identity_matches = bool(contact_key and contact_key in set(item["lead_verification_keys"]))
        contact_payload = {
            "hpd_contact_id": data.get("hpd_contact_id"),
            "contact_type": data.get("contact_type"),
            "display_name": display_name,
            "verification_key": contact_key,
            "broad_dedupe_key": _name_group(display_name),
            "predicate": hpd_shape.get("predicate") if hpd_shape else None,
            "claim_type": hpd_shape.get("claim_type") if hpd_shape else None,
            "role_matches_building_management": role_matches,
            "strict_identity_matches_lead": identity_matches,
        }
        if role_matches and identity_matches:
            item["matched_role_contacts"].append(contact_payload)
        elif identity_matches:
            item["adjacent_role_contacts"].append(contact_payload)
        else:
            item["blocked_contact_count"] += 1

    batches: dict[str, dict[str, Any]] = {}
    totals = {
        "relationship_count": 0,
        "multi_source_if_materialized_count": 0,
        "source_ready_if_materialized_count": 0,
        "management_source_ready_if_materialized_count": 0,
        "registered_agent_source_ready_if_materialized_count": 0,
    }
    predicate_counts: dict[str, int] = {}
    for item in relationships.values():
        lead_id = str(item.get("lead_id") or "")
        if not lead_id:
            continue
        bm_shape = item.get("building_management_shape") or {}
        predicate = str(bm_shape.get("predicate") or "unknown")
        source_ready = bool(item["matched_role_contacts"])
        batch = batches.setdefault(lead_id, {
            "lead_id": lead_id,
            "lead_name": item.get("lead_name"),
            "scope_relationship_count": 0,
            "multi_source_if_materialized_count": 0,
            "source_ready_if_materialized_count": 0,
            "management_source_ready_if_materialized_count": 0,
            "registered_agent_source_ready_if_materialized_count": 0,
            "claim_count_by_predicate_if_materialized": {},
            "samples": [],
        })
        totals["relationship_count"] += 1
        batch["scope_relationship_count"] += 1
        batch["claim_count_by_predicate_if_materialized"][predicate] = (
            batch["claim_count_by_predicate_if_materialized"].get(predicate, 0) + 1
        )
        predicate_counts[predicate] = predicate_counts.get(predicate, 0) + 1
        if source_ready:
            totals["multi_source_if_materialized_count"] += 1
            totals["source_ready_if_materialized_count"] += 1
            batch["multi_source_if_materialized_count"] += 1
            batch["source_ready_if_materialized_count"] += 1
            if predicate == "manages_building":
                totals["management_source_ready_if_materialized_count"] += 1
                batch["management_source_ready_if_materialized_count"] += 1
            if predicate == "registered_agent_for_building":
                totals["registered_agent_source_ready_if_materialized_count"] += 1
                batch["registered_agent_source_ready_if_materialized_count"] += 1
        if len(batch["samples"]) < bounded_sample_limit:
            batch["samples"].append({
                "fact_key": {
                    "subject_type": "lead",
                    "subject_id": lead_id,
                    "predicate": bm_shape.get("predicate"),
                    "object_type": "building",
                    "object_id": item["bbl"],
                    "normalized_value": bm_shape.get("normalized_value"),
                    "claim_type": bm_shape.get("claim_type"),
                },
                "building_management_role": item["building_management_role"],
                "supporting_sources_if_materialized": (
                    ["building_management", "hpd_contacts"] if source_ready else ["building_management"]
                ),
                "source_ready_if_materialized": source_ready,
                "matched_role_contacts": item["matched_role_contacts"][:2],
                "adjacent_role_contacts": item["adjacent_role_contacts"][:2],
                "blocked_contact_count": item["blocked_contact_count"],
                "safe_action": (
                    "Use as registered-agent/legal verification only; do not treat as operating-manager proof."
                    if predicate == "registered_agent_for_building"
                    else "May support management verification only when the matched role is ManagementCompany/manager and remains contradiction-free."
                ),
            })

    ranked_batches = sorted(
        batches.values(),
        key=lambda batch: (
            batch["source_ready_if_materialized_count"],
            batch["management_source_ready_if_materialized_count"],
            batch["scope_relationship_count"],
            batch["lead_id"],
        ),
        reverse=True,
    )
    return {
        "dry_run": True,
        "mutations_planned": 0,
        "relationship_limit": bounded_relationship_limit,
        "batch_limit": bounded_batch_limit,
        "source_ready_batch_count": sum(1 for batch in batches.values() if batch["source_ready_if_materialized_count"] > 0),
        "scanned_relationship_count": totals["relationship_count"],
        "multi_source_if_materialized_count": totals["multi_source_if_materialized_count"],
        "source_ready_if_materialized_count": totals["source_ready_if_materialized_count"],
        "management_source_ready_if_materialized_count": totals["management_source_ready_if_materialized_count"],
        "registered_agent_source_ready_if_materialized_count": totals["registered_agent_source_ready_if_materialized_count"],
        "claim_count_by_predicate_if_materialized": predicate_counts,
        "business_readiness_note": (
            "Scaled preview found management-company overlap candidates."
            if totals["management_source_ready_if_materialized_count"]
            else "Scaled preview found no management-company overlap; current strict overlap is role-specific legal/registered-agent evidence."
        ),
        "batches": ranked_batches[:bounded_batch_limit],
    }


async def simulate_role_overlap_post_materialization(
    session: AsyncSession,
    *,
    limit: int = 500,
) -> dict[str, Any]:
    """Preview adjudication posture for strict role-overlap claims before upsert."""
    bounded_limit = min(max(int(limit or 500), 1), 5000)
    specs = await _load_materializable_claims(
        session,
        limit=bounded_limit,
        sources=["building_management", "hpd_contact_role_links"],
    )
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for spec in specs:
        claim = spec.get("claim") or {}
        evidence = spec.get("evidence") or {}
        key = (
            claim.get("subject_type"),
            claim.get("subject_id"),
            claim.get("predicate"),
            claim.get("object_type"),
            claim.get("object_id"),
            claim.get("normalized_value"),
            claim.get("claim_type"),
        )
        group = groups.setdefault(key, {
            "subject_type": claim.get("subject_type"),
            "subject_id": claim.get("subject_id"),
            "predicate": claim.get("predicate"),
            "object_type": claim.get("object_type"),
            "object_id": claim.get("object_id"),
            "normalized_value": claim.get("normalized_value"),
            "claim_type": claim.get("claim_type"),
            "claim_ids": [],
            "evidence_ids": [],
            "supporting_sources": [],
            "contradicting_sources": [],
            "supporting_evidence_count": 0,
            "contradicting_evidence_count": 0,
            "existing_belief_statuses": [],
            "max_confidence_score": None,
            "min_confidence_score": None,
            "freshest_observed_freshness_days": None,
            "oldest_observed_freshness_days": None,
        })
        claim_id = claim.get("claim_id")
        evidence_id = evidence.get("evidence_id")
        source_name = evidence.get("source_name")
        support_status = str(evidence.get("support_status") or "supports")
        if claim_id and claim_id not in group["claim_ids"]:
            group["claim_ids"].append(claim_id)
        if evidence_id and evidence_id not in group["evidence_ids"]:
            group["evidence_ids"].append(evidence_id)
        if support_status == "contradicts":
            if source_name and source_name not in group["contradicting_sources"]:
                group["contradicting_sources"].append(source_name)
            group["contradicting_evidence_count"] += 1
        else:
            if source_name and source_name not in group["supporting_sources"]:
                group["supporting_sources"].append(source_name)
            group["supporting_evidence_count"] += 1
        belief_status = claim.get("belief_status")
        if belief_status and belief_status not in group["existing_belief_statuses"]:
            group["existing_belief_statuses"].append(belief_status)
        confidence_score = claim.get("confidence_score")
        if confidence_score is not None:
            score = _as_float(confidence_score)
            group["max_confidence_score"] = score if group["max_confidence_score"] is None else max(group["max_confidence_score"], score)
            group["min_confidence_score"] = score if group["min_confidence_score"] is None else min(group["min_confidence_score"], score)
        freshness = claim.get("freshness_days")
        if freshness is not None:
            days = _as_int(freshness, default=9999)
            group["freshest_observed_freshness_days"] = (
                days if group["freshest_observed_freshness_days"] is None else min(group["freshest_observed_freshness_days"], days)
            )
            group["oldest_observed_freshness_days"] = (
                days if group["oldest_observed_freshness_days"] is None else max(group["oldest_observed_freshness_days"], days)
            )

    candidates = [adjudicate_fact_group(group) for group in groups.values()]
    source_ready = [
        item for item in candidates
        if item["supporting_source_count"] >= VERIFICATION_MIN_SUPPORTING_SOURCES
        and item["supporting_evidence_count"] >= VERIFICATION_MIN_SUPPORTING_EVIDENCE
        and item["contradicting_evidence_count"] == 0
    ]
    safe_candidates = [item for item in candidates if item["safe_to_mark_verified"]]
    predicate_counts: dict[str, int] = {}
    source_ready_by_predicate: dict[str, int] = {}
    safe_by_predicate: dict[str, int] = {}
    for item in candidates:
        predicate = str(item.get("fact_key", {}).get("predicate") or "unknown")
        predicate_counts[predicate] = predicate_counts.get(predicate, 0) + 1
    for item in source_ready:
        predicate = str(item.get("fact_key", {}).get("predicate") or "unknown")
        source_ready_by_predicate[predicate] = source_ready_by_predicate.get(predicate, 0) + 1
    for item in safe_candidates:
        predicate = str(item.get("fact_key", {}).get("predicate") or "unknown")
        safe_by_predicate[predicate] = safe_by_predicate.get(predicate, 0) + 1

    return {
        "dry_run": True,
        "mutations_planned": 0,
        "selected_sources": ["building_management", "hpd_contact_role_links"],
        "planned_claim_spec_count": len(specs),
        "simulated_fact_group_count": len(candidates),
        "multi_source_fact_group_count": sum(1 for item in candidates if item["supporting_source_count"] >= 2),
        "source_ready_fact_group_count": len(source_ready),
        "safe_to_mark_verified_count": len(safe_candidates),
        "fact_group_count_by_predicate": predicate_counts,
        "source_ready_count_by_predicate": source_ready_by_predicate,
        "safe_to_mark_verified_count_by_predicate": safe_by_predicate,
        "business_readiness_note": (
            "Simulation creates source-ready registered-agent overlap but does not mark facts verified unless confidence/freshness policy also passes."
            if source_ready and not safe_candidates
            else "Simulation produces verified-safe role facts under the current policy."
            if safe_candidates
            else "Simulation does not create source-ready role facts."
        ),
        "samples": candidates[:10],
    }


async def load_role_claim_correction_preview(
    session: AsyncSession,
    *,
    lead_id: str = "0ff794d3ba2d",
    limit: int = 20,
) -> dict[str, Any]:
    """Preview stale role-shape claims that should not survive source-overlap execution."""
    bounded_limit = min(max(int(limit or 20), 1), 50)
    rows = await session.execute(
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
                c.actionability_level,
                c.observed_at,
                c.updated_at,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT e.evidence_id), NULL) AS evidence_ids,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT e.source_name), NULL) AS source_names,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT e.source_record_id), NULL) AS source_record_ids,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT e.raw_payload ->> 'source_role'), NULL) AS source_roles
            FROM truth_claims c
            LEFT JOIN truth_evidence e ON e.claim_id = c.claim_id
            WHERE c.current_flag = true
              AND c.subject_type = 'lead'
              AND c.subject_id = :lead_id
              AND c.predicate = 'manages_building'
              AND c.claim_type = 'building_management'
              AND (
                    LOWER(COALESCE(c.normalized_value, '')) = 'agent'
                    OR LOWER(COALESCE(e.raw_payload ->> 'source_role', '')) = 'agent'
                  )
            GROUP BY
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
                c.actionability_level,
                c.observed_at,
                c.updated_at
            ORDER BY c.updated_at DESC NULLS LAST, c.claim_id
            LIMIT :limit
        """),
        {"lead_id": lead_id, "limit": bounded_limit},
    )
    samples: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row._mapping)
        samples.append({
            "claim_id": data.get("claim_id"),
            "fact_key": _fact_key(data),
            "belief_status": data.get("belief_status"),
            "confidence_score": data.get("confidence_score"),
            "actionability_level": data.get("actionability_level"),
            "evidence_ids": _as_list(data.get("evidence_ids")),
            "source_names": _as_list(data.get("source_names")),
            "source_record_ids": _as_list(data.get("source_record_ids")),
            "source_roles": _as_list(data.get("source_roles")),
            "recommended_change": {
                "operation": "set_current_flag_false",
                "replacement_predicate": "registered_agent_for_building",
                "replacement_claim_type": "registered_agent",
                "reason": "Existing claim encodes an HPD Agent relationship as a management claim.",
            },
        })

    return {
        "dry_run": True,
        "mutations_planned": 0,
        "lead_id": lead_id,
        "sampled_stale_claim_count": len(samples),
        "requires_operator_approval": bool(samples),
        "business_readiness_note": (
            "Stale Agent-as-manager claims must be deactivated or superseded before management facts are safe for use."
            if samples
            else "No sampled stale Agent-as-manager management claims found for this pilot lead."
        ),
        "safe_action": "Preview only. Do not execute correction without explicit dry_run=false and confirm_execute=true approval.",
        "samples": samples,
    }


def build_role_overlap_activation_plan(
    *,
    correction_preview: dict[str, Any],
    scaled_role_source_overlap: dict[str, Any],
) -> dict[str, Any]:
    """Build the read-only approval plan for turning strict role overlap into claims."""
    stale_claim_count = _as_int(correction_preview.get("sampled_stale_claim_count"))
    source_ready_count = _as_int(scaled_role_source_overlap.get("source_ready_if_materialized_count"))
    manager_ready_count = _as_int(scaled_role_source_overlap.get("management_source_ready_if_materialized_count"))
    registered_agent_ready_count = _as_int(
        scaled_role_source_overlap.get("registered_agent_source_ready_if_materialized_count")
    )
    return {
        "dry_run": True,
        "mutations_planned": 0,
        "approval_required": bool(stale_claim_count or source_ready_count),
        "current_ledger_verified_claims_added": 0,
        "predicted_if_approved": {
            "source_ready_fact_groups_added": source_ready_count,
            "management_source_ready_fact_groups_added": manager_ready_count,
            "registered_agent_source_ready_fact_groups_added": registered_agent_ready_count,
            "stale_role_claims_to_supersede": stale_claim_count,
        },
        "materialization_sources": ["building_management", "hpd_contact_role_links"],
        "ordered_steps": [
            {
                "step": "preview_role_claim_corrections",
                "status": "complete",
                "approval_required": False,
                "mutations_planned": 0,
                "evidence": {
                    "sampled_stale_claim_count": stale_claim_count,
                },
            },
            {
                "step": "execute_role_claim_corrections",
                "status": "approval_required" if stale_claim_count else "not_needed",
                "approval_required": bool(stale_claim_count),
                "required_execute_params": {"dry_run": False, "confirm_execute": True},
                "mutations_planned": stale_claim_count,
                "safe_action": (
                    "Supersede stale Agent-as-manager claims before treating role overlap as evidence."
                    if stale_claim_count
                    else "No stale Agent-as-manager correction is required in the sampled plan."
                ),
            },
            {
                "step": "execute_bounded_role_overlap_materialization",
                "status": "approval_required" if source_ready_count else "blocked_no_source_ready_overlap",
                "approval_required": bool(source_ready_count),
                "required_execute_params": {"dry_run": False, "confirm_execute": True},
                "sources": ["building_management", "hpd_contact_role_links"],
                "mutations_planned": source_ready_count * 2 if source_ready_count else 0,
                "safe_action": (
                    "Materialize only the strict role-overlap sources, then rerun adjudication before any business use."
                    if source_ready_count
                    else "Acquire real independent evidence before materialization; no strict overlap is source-ready."
                ),
            },
            {
                "step": "rerun_adjudication_and_health",
                "status": "required_after_execution",
                "approval_required": False,
                "mutations_planned": 0,
                "commands": [
                    "python scripts/truth_adjudication_preview.py --limit 20 --indent 2",
                    "python scripts/truth_health_report.py --materialization-limit 50 --validation-sample-limit 10 --indent 2",
                    "python scripts/truth_completion_audit.py --include-runtime --indent 2",
                ],
            },
        ],
        "business_readiness_note": (
            "The activation plan can create registered-agent source overlap, but it still cannot verify operating-manager facts."
            if registered_agent_ready_count and not manager_ready_count
            else "The activation plan includes management-source-ready candidates; inspect samples before execution."
            if manager_ready_count
            else "No source-ready role overlap is available yet."
        ),
        "safe_action": "Preview only. Do not execute any step without explicit operator approval and confirm_execute=true.",
    }


def build_role_claim_correction_plan(
    correction_preview: dict[str, Any],
    *,
    run_id: str,
) -> dict[str, Any]:
    samples = correction_preview.get("samples") if isinstance(correction_preview.get("samples"), list) else []
    claim_updates: list[dict[str, Any]] = []
    for sample in samples:
        claim_id = str(sample.get("claim_id") or "").strip()
        if not claim_id:
            continue
        claim_updates.append({
            "claim_id": claim_id,
            "current_flag": False,
            "belief_status": "superseded",
            "actionability_level": "do_not_act",
            "rationale": {
                "correction_run_id": run_id,
                "correction_type": "stale_agent_as_manager_role_shape",
                "previous_fact_key": sample.get("fact_key"),
                "replacement_predicate": "registered_agent_for_building",
                "replacement_claim_type": "registered_agent",
                "why": (
                    "This current claim encoded an HPD Agent relationship as manages_building. "
                    "Agent evidence can support registered-agent/legal-contact facts only."
                ),
            },
        })
    return {
        "run_id": run_id,
        "claim_update_count": len(claim_updates),
        "claim_updates": claim_updates,
        "rollback_strategy": (
            "Role correction only updates existing truth_claims.current_flag/status/actionability/rationale. "
            "Each touched claim is written to truth_materialization_manifest with a before_snapshot; rollback "
            "requires targeted restore from that snapshot or backup/PITR."
        ),
    }


async def preview_or_apply_role_claim_corrections(
    session: AsyncSession,
    *,
    lead_id: str = "0ff794d3ba2d",
    limit: int = 100,
    dry_run: bool = True,
    confirm_execute: bool = False,
    run_id: str | None = None,
) -> dict[str, Any]:
    run_id = run_id or f"truth-role-correction-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
    correction_preview = await load_role_claim_correction_preview(session, lead_id=lead_id, limit=limit)
    plan = build_role_claim_correction_plan(correction_preview, run_id=run_id)
    claim_ids = [update["claim_id"] for update in plan["claim_updates"]]
    existing_claim_ids = await _load_existing_ids(
        session,
        table_name="truth_claims",
        id_column="claim_id",
        ids=claim_ids,
    )
    before_claims = await _load_before_snapshots_by_id(
        session,
        table_name="truth_claims",
        id_column="claim_id",
        columns=[
            "claim_id",
            "belief_status",
            "confidence_score",
            "actionability_level",
            "current_flag",
            "rationale",
            "updated_at",
        ],
        ids=existing_claim_ids,
    )
    manifest_entries = build_materialization_manifest_entries(
        run_id=run_id,
        item_type="truth_claim",
        item_ids=claim_ids,
        existing_item_ids=existing_claim_ids,
        before_snapshots_by_id=before_claims,
    )
    base_result = {
        "run_type": "truth_role_claim_correction",
        "run_id": run_id,
        "dry_run": bool(dry_run or not confirm_execute),
        "mutations_planned": len(plan["claim_updates"]) if dry_run or not confirm_execute else 0,
        "allowed_execute": not dry_run and confirm_execute and bool(plan["claim_updates"]),
        "lead_id": lead_id,
        "limit": limit,
        "candidate_summary": {
            "sampled_stale_claim_count": correction_preview.get("sampled_stale_claim_count"),
            "claim_update_count": plan["claim_update_count"],
        },
        "proposed_database_changes": [
            {
                "table": "truth_claims",
                "operation": "update",
                "id": update["claim_id"],
                "current_flag": update["current_flag"],
                "belief_status": update["belief_status"],
                "actionability_level": update["actionability_level"],
            }
            for update in plan["claim_updates"]
        ],
        "rollback_manifest": _manifest_summary(manifest_entries),
        "rollback_strategy": plan["rollback_strategy"],
        "required_execute_params": {"dry_run": False, "confirm_execute": True},
        "correction_preview": correction_preview,
    }
    if dry_run or not confirm_execute:
        base_result["blocked_reason"] = "Role-claim correction defaults to preview; execute requires dry_run=false and confirm_execute=true."
        return base_result
    if not plan["claim_updates"]:
        return {
            **base_result,
            "dry_run": True,
            "allowed_execute": False,
            "blocked_reason": "No stale Agent-as-manager claims were available to correct.",
        }

    await _upsert_materialization_manifest_entries(session, entries=manifest_entries)
    for update in plan["claim_updates"]:
        await session.execute(
            text("""
                UPDATE truth_claims
                SET
                    current_flag = false,
                    belief_status = :belief_status,
                    actionability_level = :actionability_level,
                    rationale = COALESCE(rationale, '{}'::jsonb) || CAST(:rationale AS JSONB),
                    updated_at = NOW()
                WHERE claim_id = :claim_id
            """),
            {
                **update,
                "rationale": json.dumps(update.get("rationale") or {}),
            },
        )
    await session.commit()
    return {
        **base_result,
        "dry_run": False,
        "mutations_planned": 0,
        "claims_updated": len(plan["claim_updates"]),
    }


async def load_ledger_source_overlap_summary(session: AsyncSession) -> dict[str, Any]:
    """Summarize source independence across the whole current claim ledger."""
    summary_rows = await session.execute(
        text("""
            WITH fact_groups AS (
                SELECT
                    c.subject_type,
                    c.subject_id,
                    c.predicate,
                    COALESCE(c.object_type, '') AS object_type,
                    COALESCE(c.object_id, '') AS object_id,
                    COALESCE(c.normalized_value, '') AS normalized_value,
                    c.claim_type,
                    COUNT(DISTINCT e.source_name) FILTER (WHERE e.support_status = 'supports')::int AS supporting_source_count,
                    COUNT(DISTINCT e.evidence_id) FILTER (WHERE e.support_status = 'supports')::int AS supporting_evidence_count,
                    COUNT(DISTINCT e.evidence_id) FILTER (WHERE e.support_status = 'contradicts')::int AS contradicting_evidence_count
                FROM truth_claims c
                LEFT JOIN truth_evidence e ON e.claim_id = c.claim_id
                WHERE c.current_flag = true
                GROUP BY
                    c.subject_type,
                    c.subject_id,
                    c.predicate,
                    COALESCE(c.object_type, ''),
                    COALESCE(c.object_id, ''),
                    COALESCE(c.normalized_value, ''),
                    c.claim_type
            )
            SELECT
                COUNT(*)::int AS total_fact_group_count,
                COUNT(*) FILTER (WHERE supporting_source_count = 0)::int AS zero_source_fact_group_count,
                COUNT(*) FILTER (WHERE supporting_source_count = 1)::int AS single_source_fact_group_count,
                COUNT(*) FILTER (WHERE supporting_source_count >= 2)::int AS multi_source_fact_group_count,
                COUNT(*) FILTER (
                    WHERE supporting_source_count >= :min_sources
                      AND supporting_evidence_count >= :min_evidence
                      AND contradicting_evidence_count = 0
                )::int AS source_ready_fact_group_count,
                COALESCE(MAX(supporting_source_count), 0)::int AS max_supporting_source_count,
                COALESCE(MAX(supporting_evidence_count), 0)::int AS max_supporting_evidence_count
            FROM fact_groups
        """),
        {
            "min_sources": VERIFICATION_MIN_SUPPORTING_SOURCES,
            "min_evidence": VERIFICATION_MIN_SUPPORTING_EVIDENCE,
        },
    )
    summary_row = summary_rows.first()
    summary = dict(summary_row._mapping) if summary_row is not None else {}
    top_source_rows = await session.execute(
        text("""
            WITH fact_source_pairs AS (
                SELECT DISTINCT
                    c.subject_type,
                    c.subject_id,
                    c.predicate,
                    COALESCE(c.object_type, '') AS object_type,
                    COALESCE(c.object_id, '') AS object_id,
                    COALESCE(c.normalized_value, '') AS normalized_value,
                    c.claim_type,
                    e.source_name
                FROM truth_claims c
                JOIN truth_evidence e ON e.claim_id = c.claim_id
                WHERE c.current_flag = true
                  AND e.support_status = 'supports'
                  AND e.source_name IS NOT NULL
            )
            SELECT source_name, COUNT(*)::int AS fact_group_count
            FROM fact_source_pairs
            GROUP BY source_name
            ORDER BY fact_group_count DESC, source_name
            LIMIT 10
        """)
    )
    top_sources = [dict(row._mapping) for row in top_source_rows]
    total_fact_group_count = _as_int(summary.get("total_fact_group_count"))
    multi_source_fact_group_count = _as_int(summary.get("multi_source_fact_group_count"))
    source_ready_fact_group_count = _as_int(summary.get("source_ready_fact_group_count"))
    return {
        "dry_run": True,
        "mutations_planned": 0,
        "total_fact_group_count": total_fact_group_count,
        "zero_source_fact_group_count": _as_int(summary.get("zero_source_fact_group_count")),
        "single_source_fact_group_count": _as_int(summary.get("single_source_fact_group_count")),
        "multi_source_fact_group_count": multi_source_fact_group_count,
        "source_ready_fact_group_count": source_ready_fact_group_count,
        "max_supporting_source_count": _as_int(summary.get("max_supporting_source_count")),
        "max_supporting_evidence_count": _as_int(summary.get("max_supporting_evidence_count")),
        "top_sources": top_sources,
        "business_readiness_blocker": (
            "No current ledger fact groups have enough independent supporting sources and evidence for adjudication."
            if total_fact_group_count and source_ready_fact_group_count == 0
            else None
        ),
    }


async def load_claim_adjudication_preview(
    session: AsyncSession,
    *,
    limit: int = 100,
    include_samples: bool = True,
) -> dict[str, Any]:
    """Group equivalent claim facts and preview safe status adjudication.

    This is deliberately read-only. It does not update `truth_claims`; it tells
    operators why a fact can or cannot be treated as verified.
    """
    bounded_limit = min(max(int(limit or 100), 1), 1000)
    ledger_source_overlap = await load_ledger_source_overlap_summary(session)
    role_source_overlap_pilot = await load_role_source_overlap_pilot(
        session,
        lead_id="0ff794d3ba2d",
        limit=min(bounded_limit, 20),
    )
    scaled_role_source_overlap = await load_scaled_role_source_overlap_preview(
        session,
        relationship_limit=1000,
        batch_limit=10,
        sample_limit=3,
    )
    role_overlap_post_materialization_simulation = await simulate_role_overlap_post_materialization(
        session,
        limit=500,
    )
    role_claim_correction_preview = await load_role_claim_correction_preview(
        session,
        lead_id="0ff794d3ba2d",
        limit=100,
    )
    manager_source_bridge_preview = await load_manager_source_bridge_preview(
        session,
        lead_id="0ff794d3ba2d",
        sample_limit=10,
    )
    manager_external_source_acquisition_preview = await load_manager_external_source_acquisition_preview(
        session,
        lead_id="0ff794d3ba2d",
        limit=20,
    )
    operator_confirmed_management_preview = await load_operator_confirmed_management_preview(
        session,
        limit=20,
    )
    role_overlap_activation_plan = build_role_overlap_activation_plan(
        correction_preview=role_claim_correction_preview,
        scaled_role_source_overlap=scaled_role_source_overlap,
    )
    rows = await session.execute(
        text("""
            WITH fact_groups AS (
                SELECT
                    c.subject_type,
                    c.subject_id,
                    c.predicate,
                    COALESCE(c.object_type, '') AS object_type,
                    COALESCE(c.object_id, '') AS object_id,
                    COALESCE(c.normalized_value, '') AS normalized_value,
                    c.claim_type,
                    ARRAY_REMOVE(ARRAY_AGG(DISTINCT c.claim_id), NULL) AS claim_ids,
                    ARRAY_REMOVE(ARRAY_AGG(DISTINCT e.evidence_id), NULL) AS evidence_ids,
                    ARRAY_REMOVE(ARRAY_AGG(DISTINCT e.source_name) FILTER (WHERE e.support_status = 'supports'), NULL) AS supporting_sources,
                    ARRAY_REMOVE(ARRAY_AGG(DISTINCT e.source_name) FILTER (WHERE e.support_status = 'contradicts'), NULL) AS contradicting_sources,
                    COUNT(DISTINCT e.evidence_id) FILTER (WHERE e.support_status = 'supports')::int AS supporting_evidence_count,
                    COUNT(DISTINCT e.evidence_id) FILTER (WHERE e.support_status = 'contradicts')::int AS contradicting_evidence_count,
                    ARRAY_REMOVE(ARRAY_AGG(DISTINCT c.belief_status), NULL) AS existing_belief_statuses,
                    MAX(c.confidence_score) AS max_confidence_score,
                    MIN(c.confidence_score) AS min_confidence_score,
                    MIN(c.freshness_days) AS freshest_observed_freshness_days,
                    MAX(c.freshness_days) AS oldest_observed_freshness_days,
                    MAX(c.updated_at) AS last_claim_updated_at
                FROM truth_claims c
                LEFT JOIN truth_evidence e ON e.claim_id = c.claim_id
                WHERE c.current_flag = true
                GROUP BY
                    c.subject_type,
                    c.subject_id,
                    c.predicate,
                    COALESCE(c.object_type, ''),
                    COALESCE(c.object_id, ''),
                    COALESCE(c.normalized_value, ''),
                    c.claim_type
            )
            SELECT *
            FROM fact_groups
            ORDER BY
                cardinality(supporting_sources) DESC NULLS LAST,
                supporting_evidence_count DESC,
                max_confidence_score DESC NULLS LAST,
                last_claim_updated_at DESC NULLS LAST
            LIMIT :limit
        """),
        {"limit": bounded_limit},
    )
    candidates = [adjudicate_fact_group(dict(row._mapping)) for row in rows]
    status_counts: dict[str, int] = {}
    blocker_counts: dict[str, int] = {}
    queue_counts: dict[str, int] = {}
    for item in candidates:
        status_counts[item["proposed_belief_status"]] = status_counts.get(item["proposed_belief_status"], 0) + 1
        queue_counts[item["recommended_queue"]] = queue_counts.get(item["recommended_queue"], 0) + 1
        for blocker in item["blockers"]:
            blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1

    safe_candidates = [item for item in candidates if item["safe_to_mark_verified"]]
    source_coverage = summarize_adjudication_source_coverage(candidates)
    verification_gap_plan = build_verification_gap_plan(candidates, limit=min(bounded_limit, 10))
    verified_confidence_gap_plan = build_verified_confidence_gap_plan(candidates, limit=min(bounded_limit, 10))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": True,
        "mutations_planned": 0,
        "limit": bounded_limit,
        "fact_group_count": len(candidates),
        "verification_candidate_count": len(safe_candidates),
        "status_counts": status_counts,
        "recommended_queue_counts": queue_counts,
        "blocker_counts": blocker_counts,
        "source_coverage": source_coverage,
        "ledger_source_overlap": ledger_source_overlap,
        "role_source_overlap_pilot": role_source_overlap_pilot,
        "scaled_role_source_overlap": scaled_role_source_overlap,
        "role_overlap_post_materialization_simulation": role_overlap_post_materialization_simulation,
        "role_claim_correction_preview": role_claim_correction_preview,
        "manager_source_bridge_preview": manager_source_bridge_preview,
        "manager_external_source_acquisition_preview": manager_external_source_acquisition_preview,
        "operator_confirmed_management_preview": operator_confirmed_management_preview,
        "role_overlap_activation_plan": role_overlap_activation_plan,
        "verification_gap_plan": verification_gap_plan,
        "verified_confidence_gap_plan": verified_confidence_gap_plan,
        "policy": {
            "confidence_policy_version": CONFIDENCE_POLICY_VERSION,
            "verification_min_supporting_sources": VERIFICATION_MIN_SUPPORTING_SOURCES,
            "verification_min_supporting_evidence": VERIFICATION_MIN_SUPPORTING_EVIDENCE,
            "verification_max_freshness_days": VERIFICATION_MAX_FRESHNESS_DAYS,
            "execution_policy": "Read-only preview. Status changes require a separate explicit, rollbackable adjudication execution path.",
        },
        "samples": candidates[:bounded_limit] if include_samples else [],
        "next_safe_steps": [
            "Materialize more source-diverse claims before marking facts verified.",
            "Refresh or manually record stale public sources, then rerun adjudication preview.",
            "Route conflicting or insufficient-evidence groups to human review rather than changing claim status.",
        ],
    }


def build_adjudication_update_plan(
    candidates: list[dict[str, Any]],
    *,
    run_id: str,
) -> dict[str, Any]:
    safe_candidates = [candidate for candidate in candidates if candidate.get("safe_to_mark_verified")]
    claim_updates: list[dict[str, Any]] = []
    for candidate in safe_candidates:
        for claim_id in candidate.get("claim_ids") or []:
            claim_updates.append({
                "claim_id": str(claim_id),
                "belief_status": candidate.get("proposed_belief_status"),
                "confidence_score": candidate.get("recomputed_confidence_score"),
                "actionability_level": candidate.get("proposed_actionability_level"),
                "rationale": {
                    "confidence_policy_version": CONFIDENCE_POLICY_VERSION,
                    "adjudication_run_id": run_id,
                    "fact_key": candidate.get("fact_key"),
                    "supporting_sources": candidate.get("supporting_sources") or [],
                    "supporting_evidence_count": candidate.get("supporting_evidence_count"),
                    "contradicting_sources": candidate.get("contradicting_sources") or [],
                    "contradicting_evidence_count": candidate.get("contradicting_evidence_count"),
                    "freshest_observed_freshness_days": candidate.get("freshest_observed_freshness_days"),
                    "why": "Claim fact met the independent, fresh, non-contradicted verification threshold.",
                },
            })
    return {
        "run_id": run_id,
        "safe_candidate_count": len(safe_candidates),
        "claim_update_count": len(claim_updates),
        "claim_updates": claim_updates,
        "skipped_candidate_count": len(candidates) - len(safe_candidates),
        "rollback_strategy": (
            "Adjudication only updates existing truth_claims. Each touched claim is written to "
            "truth_materialization_manifest with a before_snapshot; rollback requires targeted restore "
            "from that snapshot or backup/PITR."
        ),
    }


async def preview_or_apply_claim_adjudication(
    session: AsyncSession,
    *,
    limit: int = 100,
    dry_run: bool = True,
    confirm_execute: bool = False,
    run_id: str | None = None,
) -> dict[str, Any]:
    run_id = run_id or f"truth-adjudication-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
    preview = await load_claim_adjudication_preview(session, limit=limit, include_samples=True)
    candidates = preview.get("samples") if isinstance(preview.get("samples"), list) else []
    plan = build_adjudication_update_plan(candidates, run_id=run_id)
    claim_ids = [update["claim_id"] for update in plan["claim_updates"]]
    existing_claim_ids = await _load_existing_ids(
        session,
        table_name="truth_claims",
        id_column="claim_id",
        ids=claim_ids,
    )
    before_claims = await _load_before_snapshots_by_id(
        session,
        table_name="truth_claims",
        id_column="claim_id",
        columns=[
            "claim_id",
            "belief_status",
            "confidence_score",
            "actionability_level",
            "rationale",
            "updated_at",
        ],
        ids=existing_claim_ids,
    )
    manifest_entries = build_materialization_manifest_entries(
        run_id=run_id,
        item_type="truth_claim",
        item_ids=claim_ids,
        existing_item_ids=existing_claim_ids,
        before_snapshots_by_id=before_claims,
    )
    base_result = {
        "run_type": "truth_claim_adjudication",
        "run_id": run_id,
        "dry_run": bool(dry_run or not confirm_execute),
        "mutations_planned": len(plan["claim_updates"]) if dry_run or not confirm_execute else 0,
        "allowed_execute": not dry_run and confirm_execute and bool(plan["claim_updates"]),
        "limit": limit,
        "candidate_summary": {
            "fact_group_count": preview.get("fact_group_count"),
            "verification_candidate_count": preview.get("verification_candidate_count"),
            "safe_candidate_count": plan["safe_candidate_count"],
            "claim_update_count": plan["claim_update_count"],
            "skipped_candidate_count": plan["skipped_candidate_count"],
        },
        "proposed_database_changes": [
            {
                "table": "truth_claims",
                "operation": "update",
                "id": update["claim_id"],
                "belief_status": update["belief_status"],
                "confidence_score": update["confidence_score"],
                "actionability_level": update["actionability_level"],
            }
            for update in plan["claim_updates"]
        ],
        "rollback_manifest": _manifest_summary(manifest_entries),
        "rollback_strategy": plan["rollback_strategy"],
        "required_execute_params": {"dry_run": False, "confirm_execute": True},
        "adjudication_preview": preview,
    }
    if dry_run or not confirm_execute:
        base_result["blocked_reason"] = "Claim adjudication defaults to preview; execute requires dry_run=false and confirm_execute=true."
        return base_result
    if not plan["claim_updates"]:
        return {
            **base_result,
            "dry_run": True,
            "allowed_execute": False,
            "blocked_reason": "No safe verification candidates were available to adjudicate.",
        }

    await _upsert_materialization_manifest_entries(session, entries=manifest_entries)
    for update in plan["claim_updates"]:
        await session.execute(
            text("""
                UPDATE truth_claims
                SET
                    belief_status = :belief_status,
                    confidence_score = :confidence_score,
                    actionability_level = :actionability_level,
                    rationale = COALESCE(rationale, '{}'::jsonb) || CAST(:rationale AS JSONB),
                    updated_at = NOW()
                WHERE claim_id = :claim_id
            """),
            {
                **update,
                "rationale": json.dumps(update.get("rationale") or {}),
            },
        )
    await session.commit()
    return {
        **base_result,
        "dry_run": False,
        "mutations_planned": 0,
        "claims_updated": len(plan["claim_updates"]),
    }
