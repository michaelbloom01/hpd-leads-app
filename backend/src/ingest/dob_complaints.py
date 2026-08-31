"""Bounded DOB complaint snapshots and source-code labels.

Dataset eabe-havv and the DOB-linked codebooks were checked 2026-08-31.
The source contains complaint codes and dates, with no complaint narrative,
BBL, downstream violation identifier, or monetary fields.
"""

import math
from datetime import datetime, timezone
from urllib.parse import urlencode

from src.ingest.dob_safety import DOBSafetyClient, normalize_identifier, payload_hash

DATASET_ID = "eabe-havv"
SOURCE_SYSTEM = "dob_complaints"
DATASET_URL = f"https://data.cityofnewyork.us/d/{DATASET_ID}"
RESOURCE_URL = f"https://data.cityofnewyork.us/resource/{DATASET_ID}.json"
METADATA_URL = f"https://data.cityofnewyork.us/api/views/{DATASET_ID}.json"
PARSER_VERSION = "dob-complaints-v1"
MAX_PILOT_BINS = 25
MAX_DATA_PAGES = 20
CATEGORY_CODEBOOK_URL = (
    "https://www.nyc.gov/assets/buildings/pdf/complaint_category.pdf"
)
DISPOSITION_CODEBOOK_URL = (
    "https://www.nyc.gov/assets/buildings/pdf/bis_complaint_disposition_codes.pdf"
)
CODEBOOK_REVISION = "2021-09"
CODEBOOK_CHECKED_DATE = "2026-08-31"
REQUIRED_FIELDS = {
    "complaint_number",
    "status",
    "date_entered",
    "house_number",
    "house_street",
    "zip_code",
    "bin",
    "community_board",
    "special_district",
    "complaint_category",
    "unit",
    "disposition_date",
    "disposition_code",
    "inspection_date",
    "dobrundate",
}

# DOB's currently linked September 2021 dictionary. Punctuation is normalized
# to ASCII hyphens; source codes remain available alongside every label.
CATEGORY_LABELS = {
    "1": "ACCIDENT - Construction/Plumbing",
    "2": "ACCIDENT - To Public",
    "3": "Adjacent Buildings - Not Protected",
    "4": "After Hours Work - Illegal",
    "5": "Permit - None (Building/PA/Demo etc.)",
    "6": "CONSTRUCTION - Change Grade/Change Watercourse",
    "7": "CONSTRUCTION - Change Watercourse",
    "8": "Contractor's Sign - NONE",
    "9": "Debris - Excessive",
    "10": "Debris/Building - Falling or In Danger of Falling",
    "11": "DEMOLITION - No Permit",
    "12": "DEMOLITION - Unsafe/Illegal/Mechanical Demo",
    "13": "Elevator In (FDNY) Readiness - NONE",
    "14": "Excavation - Undermining Adjacent Building",
    "15": "Fence - NONE/Inadequate/Illegal",
    "16": "Inadequate Support/Shoring",
    "17": "Material/Personnel Hoist - No Permit",
    "18": "Material Storage - Unsafe",
    "19": "Mechanical Demolition - Illegal",
    "1A": "Illegal Conversion Commercial Building/Space to Dwelling Units",
    "1B": "Illegal Tree Removal/Topo. Change in SNAD",
    "1C": "Damage Assessment Request or Report (Disaster)",
    "1D": "Con Edison Referral",
    "1E": "Suspended (Hanging) Scaffolds - No Permit/License/Dangerous/Accident",
    "1F": "Failure to Comply with Annual Crane Inspection",
    "1G": "Stalled Construction Site",
    "1H": "Emergency Asbestos Response Inspection",
    "1J": "Jewelry/Dentistry Torch: Gas Piping Removed w/o Permit",
    "1K": "Bowstring Truss Tracking Complaint",
    "1L": "Gas Utility Referral",
    "1U": "Special Operations Compliance Inspection",
    "1V": "Electrical Enforcement Work Order (DOB)",
    "1W": "Plumbing Enforcement Work Order (DOB)",
    "1X": "Construction Enforcement Work Order (DOB)",
    "1Y": "Enforcement Work Order (DOB)",
    "1Z": "Enforcement Work Order (DOB)",
    "20": "Landmark Building - ILLEGAL WORK",
    "21": "Safety Net/Guard Rail - Damaged/Inadequate/NONE (over 6-stories/75FT)",
    "22": "Safety Netting - NONE",
    "23": "Sidewalk Shed/Supported Scaffold/Inadequate/Defect/NONE/NO PMT/NO CERT",
    "24": "Sidewalk Shed - NONE",
    "25": "Warning Signs/Lights - NONE",
    "26": "Watchman - NONE",
    "27": "Auto Repair - ILLEGAL",
    "28": "BUILDING - In Danger of Collapse",
    "29": "BUILDING - Vacant, Open and Unguarded",
    "2A": "Posted Notice or Order Removed/Tampered With",
    "2B": "Failure to Comply with Vacate Order",
    "2C": "Smoking Ban - Smoking on Construction Site",
    "2D": "Smoking Signs - NO SMOKING SIGNS Not Observed on Construction Site",
    "2E": "Tracking Complaint for Full Demolition Notification",
    "2F": "Building Under Structural Monitoring",
    "2G": "Advertising Sign/Billboard/Posters/Flexible Fabric - ILLEGAL",
    "2H": "Second Avenue Subway Construction",
    "2J": "SANDY: Building Destroyed",
    "2K": "Structurally Compromised Building (LL33/08)",
    "2L": "Façade (LL11/98) - Unsafe Notification",
    "2M": "Monopole Tracking Complaint",
    "2N": "COVID-19 Executive Order",
    "2P": "Façades Unit Compliance Inspection",
    "30": "Building Shaking/Vibrating/Struct Stability Affected",
    "31": "Certificate of Occupancy - None/ILLEGAL/Contrary to CO",
    "32": "C of O - Not Being Complied With",
    "33": "Commercial Use - ILLEGAL",
    "34": "Compactor Room/Refuse Chute - ILLEGAL",
    "35": "Curb Cut/Driveway/Carport - ILLEGAL",
    "36": "Driveway/Carport - ILLEGAL",
    "37": "Egress: - Locked/Blocked/Improper/No Secondary Means",
    "38": "Egress: Exit Door Not Proper",
    "39": "Egress: No Secondary Means",
    "3A": "Unlicensed/ILLEGAL/Improper Electrical Work in Progress",
    "3B": "Routine Inspection",
    "3C": "Plan Compliance Inspection",
    "3D": "Bicycle Access Waiver Request - Elevator Safety",
    "3E": "Bicycle Access Waiver Request - Alternate Parking",
    "3G": "Restroom Non-Compliance with Local Law 79/16",
    "3H": "DCP/BSA Compliance Inspection",
    "40": "Falling - Part of Building",
    "41": "Falling - Part of Building in Danger of",
    "42": "Fence - ILLEGAL",
    "43": "Structural Stability Affected",
    "44": "Fireplace/Wood Stove - ILLEGAL",
    "45": "Illegal Conversion +",
    "46": "PA Permit - None",
    "47": "PA Permit - Not Being Complied With",
    "48": "Residential Use - ILLEGAL",
    "49": "Storefront or Business Sign/Awning/Marquee/Canopy - ILLEGAL",
    "4A": "Illegal Hotel Rooms in Residential Buildings",
    "4B": "SEP - Professional Certification Compliance Audit",
    "4E": "Stalled Sites Tracking Complaint",
    "4G": "Illegal Conversion No Access Follow-Up",
    "4H": "V.E.S.T. Program (DOB & NYPD)",
    "4J": "M.A.R.C.H. Program (INTERAGENCY)",
    "4K": "CSC: DM Tracking Complaint",
    "4L": "CSC: High-Rise Tracking Complaint",
    "4M": "CSC: Low-Rise Tracking Complaint",
    "4N": "Retaining Wall Tracking Complaint",
    "4P": "Legal/Padlock Tracking Complaint",
    "4S": "Sustainability Enforcement Work Order",
    "4W": "Woodside Settlement Project",
    "4X": "After Hours Work - With an AHV Permit",
    "50": "Sign Falling: Danger/Sign Erection or Display In-Progress (ILLEGAL)",
    "51": "Illegal Social Club",
    "52": "Sprinkler System - Inadequate",
    "53": "Vent/Exhaust - Illegal/Improper",
    "54": "Wall/Retaining Wall - Bulging/Cracked",
    "55": "Zoning: Non-Conforming",
    "56": "Boiler: Fumes/Smoke/Carbon Monoxide",
    "57": "Boiler: Illegal",
    "58": "Boiler: Defective/Inoperative/No Permit",
    "59": "Electrical Wiring: Defective/Exposed - In Progress",
    "5A": "Request for Joint FDNY/DOB Inspection",
    "5B": "Non-Compliance: with Lightweight Materials",
    "5C": "Structural Stability Impacted - New Building Under Construction",
    "5D": "Non-Compliance: with TPPN 1/00 - Vertical Enlargements",
    "5E": "Amusement Ride Accident/Incident",
    "5F": "Compliance Inspection",
    "5G": "Unlicensed/Illegal/Improper Work In-Progress",
    "5H": "Illegal Activity",
    "5J": "Multi Agency Joint Inspection",
    "60": "Electrical Work: Improper",
    "61": "Electrical Work: Unlicensed, In-Progress",
    "62": "Elevator: Danger Condition/Shaft Open/Unguarded",
    "63": "Elevator: Defective/Inoperative",
    "64": "Elevator Shaft: Open and Unguarded",
    "65": "Gas Hook-Up/Piping - Illegal or Defective",
    "66": "Plumbing Work - Illegal/No Permit (also Sprinkler/Standpipe)",
    "67": "Crane: No Permit/License/Cert/Unsafe/Illegal",
    "68": "Crane/Scaffold: Unsafe/Illegal Operations",
    "69": "Crane/Scaffold: Unsafe Installation/Equipment",
    "6A": "Vesting Inspection",
    "6B": "Semi-Annual Homeless Shelter Inspection: Plumbing",
    "6C": "Semi-Annual Homeless Shelter Inspection: Construction",
    "6D": "Semi-Annual Homeless Shelter Inspection: Electrical",
    "6M": "Elevator: Multiple Devices on Property",
    "6S": "Elevator: Single Device on Property/No Alternate Service",
    "6V": "Tenant Safety Inspection",
    "6W": "Tenant Safety - Failure to Post/Distribute",
    "6X": "Work Without Permits Watch List Compliance",
    "6Y": "Local Law Audits",
    "6Z": "Training Compliance",
    "70": "Suspension Scaffold Hanging - No Work In-Progress",
    "71": "SRO: Illegal Work/No Permit/Change in Occupancy Use +",
    "72": "SRO: Change in Occupancy/Use",
    "73": "Failure to Maintain",
    "74": "Illegal Commercial/Manufacturing Use in Residential Zone",
    "75": "Adult Establishment",
    "76": "Unlicensed/Illegal/Improper Plumbing Work In-Progress",
    "77": "Contrary to LL58/87 (Handicap Access)",
    "78": "Privately Owned Public Space/Non-Compliance",
    "79": "Lights from Parking Lot Shining on Building",
    "7A": "Integrity Complaint Referral",
    "7B": "Illegal Commercial or Manufacturing Use in a C1 or C2 Zone",
    "7F": "CSE: Tracking Compliance",
    "7G": "CSE: Sweep",
    "7J": "Work Without a Permit - Occupied Multiple Dwelling",
    "7K": "Local Law 188/17 Compliance Inspections - Active Jobs",
    "7L": "DOHMH Referral - Tenant Protection Non-Compliance",
    "7N": "Privately Owned Public Space/Compliance Inspection",
    "80": "Elevator Not Inspected/Illegal/No Permit",
    "81": "Elevator: Accident",
    "82": "Boiler: Accident/Explosion",
    "83": "Construction: Contrary/Beyond Approved Plans/Permits +",
    "84": "Façade: Defective/Cracking",
    "85": "Failure to Retain Water/Improper Drainage (LL103/89)",
    "86": "Work Contrary to Stop Work Order",
    "87": "Request for Deck Safety Inspection",
    "88": "Safety Net/Guard Rail - Damaged/Inadequate/None (6-stories 75FT or Less)",
    "89": "Accident - Cranes/Derricks/Suspension",
    "8A": "Construction Safety Compliance (CSC) Action",
    "90": "Unlicensed/Illegal Activity",
    "91": "Site Conditions Endangering Workers",
    "92": "Illegal Conversion of Manufacturing/Industrial Space",
    "93": "Request for Retaining Wall Safety Inspection",
    "94": "Plumbing: Defective/Leaking/Not Maintained",
    "95": "Bronx 2nd Offense Pilot Project",
    "96": "Unlicensed Boiler, Electrical, Plumbing or Sign Work Completed",
    "97": "Other Agency Jurisdiction",
    "98": "Refer to Operations for Determination",
    "99": "Other",
}

# Deliberately bounded to reviewed common dispositions. All other source codes
# remain visible and link to the full published dictionary, with a null label.
DISPOSITION_LABELS = {
    "A1": "Buildings Violation(s) Served",
    "A2": "Criminal Court Summons Served",
    "A3": "Full Stop Work Order Served",
    "A4": "Buildings Violation(s) and Criminal Court Summons Served",
    "A6": "Vacant/Open/Unguarded Structure - Violation(s) Issued",
    "A7": "Complaint Accepted by Padlock Unit",
    "A8": "OATH Violation Served",
    "A9": "OATH and DOB Violations Served",
    "C1": "Inspector Unable to Gain Access - 1st Attempt Made",
    "C2": "Inspector Unable to Gain Access - 2nd Final Attempt",
    "C3": "Access Denied - 1st Attempt",
    "C4": "Access Denied - 2nd Attempt",
    "H1": "Please See Complaint Number",
    "H3": "Building Violation Issued for Failure to Obey Stop Work Order",
    "I1": "Complaint Unsubstantiated Based on Department Records",
    "I2": "No Violation Warranted for Complaint at Time of Inspection",
    "I3": "Compliance Inspection Performed",
    "J1": "Follow-Up Inspection to be Scheduled Upon Further Research",
    "J2": "Complaint Resolved by Periodic Inspection",
    "J3": "Reviewed - Inspection to Be Scheduled",
    "J4": "Follow-Up Inspection Scheduled for Hazardous Condition",
    "L1": "Partial Stop Work Order",
    "L2": "Stop Work Order Fully Rescinded",
    "L3": "Stop Work Order Partially Rescinded",
    "XX": "Administrative Closure",
    "Y1": "Full Vacate Order Served",
    "Y2": "Vacate Order Fully Rescinded",
    "Y3": "Partial Vacate Order Served",
    "Y4": "Vacate Order Partially Rescinded",
}


def validate_bins(bins: list[str] | None) -> list[str]:
    if not bins or len(bins) > MAX_PILOT_BINS:
        raise ValueError(
            f"Provide 1-{MAX_PILOT_BINS} explicit DOB BINs for the complaint pilot."
        )
    if any(normalize_identifier(value, 7) != value for value in bins):
        raise ValueError("Every pilot BIN must be an exact seven-digit DOB BIN.")
    return sorted(set(bins))


def record_url(complaint_number: str) -> str:
    escaped = complaint_number.replace("'", "''")
    return RESOURCE_URL + "?" + urlencode({"$where": f"complaint_number='{escaped}'"})


def complaint_details(row: dict) -> dict:
    """Interpret explicit source fields without inventing case narratives."""
    values = {}
    warnings = []
    for target, source, fmt in (
        ("received_date", "date_entered", "%m/%d/%Y"),
        ("inspection_date", "inspection_date", "%m/%d/%Y"),
        ("disposition_date", "disposition_date", "%m/%d/%Y"),
        ("source_run_date", "dobrundate", "%Y%m%d%H%M%S"),
    ):
        raw = str(row.get(source) or "").strip()
        values[target] = None
        if raw:
            try:
                # The contract retains the source calendar date in every format.
                parsed_date = datetime.strptime(raw, fmt).date()  # noqa: DTZ007
                values[target] = parsed_date.isoformat()
            except ValueError:
                warnings.append(
                    f"Unrecognized {source} date; inspect the original source field."
                )
    category = str(row.get("complaint_category") or "").strip() or None
    lookup = (
        str(int(category))
        if category and category.isascii() and category.isdigit()
        else category
    )
    disposition = str(row.get("disposition_code") or "").strip() or None
    return {
        **values,
        "complaint_number": row.get("complaint_number"),
        "complaint_category": category,
        "complaint_category_label": CATEGORY_LABELS.get(lookup),
        "disposition_code": disposition,
        "disposition_code_label": DISPOSITION_LABELS.get(disposition),
        "category_codebook_url": CATEGORY_CODEBOOK_URL,
        "disposition_codebook_url": DISPOSITION_CODEBOOK_URL,
        "category_codebook_revision": CODEBOOK_REVISION,
        "disposition_codebook_revision": CODEBOOK_REVISION,
        "codebook_checked_date": CODEBOOK_CHECKED_DATE,
        "date_parse_warnings": warnings,
    }


def normalize_record(
    row: dict, *, observed_at: datetime, source_updated_at: datetime | None, run_id: str
) -> dict:
    key = str(row.get("complaint_number") or "").strip()
    if not key or len(key) > 160:
        raise ValueError("DOB complaint record has no valid source complaint number.")
    bin_value = normalize_identifier(row.get("bin"), 7)
    return {
        "id": payload_hash([SOURCE_SYSTEM, key])[:32],
        "source_system": SOURCE_SYSTEM,
        "source_record_key": key,
        "record_type": "complaint",
        "bin": bin_value,
        "bbl": None,
        "address": " ".join(
            str(row.get(field) or "").strip()
            for field in ("house_number", "house_street")
        ).strip()
        or None,
        "category": "DOB_COMPLAINT",
        "violation_type": None,
        "device_type": None,
        "status": row.get("status"),
        "issue_date": None,
        "description": None,
        "identity_status": "exact_source_bin" if bin_value else "unresolved",
        "source_url": record_url(key),
        "source_updated_at": source_updated_at,
        "observed_at": observed_at,
        "payload_hash": payload_hash(row),
        "parser_version": PARSER_VERSION,
        "ingestion_run_id": run_id,
        "raw_payload": row,
    }


class DOBComplaintsClient(DOBSafetyClient):
    """A complete exact-BIN snapshot, limited to 24 logical GET requests."""

    def metadata(self) -> dict:
        metadata = self._get(METADATA_URL)
        fields = {column.get("fieldName") for column in metadata.get("columns", [])}
        missing = REQUIRED_FIELDS - fields
        if missing:
            raise ValueError(f"DOB complaints schema drift: missing {sorted(missing)}")
        if not metadata.get("rowsUpdatedAt"):
            raise ValueError("DOB complaints publication timestamp is unavailable.")
        return metadata

    def fetch_snapshot(self, bins: list[str], *, page_size: int = 1000) -> dict:
        bins = validate_bins(bins)
        if not 1 <= page_size <= 1000:
            raise ValueError("Complaint page size must be between 1 and 1000.")
        before = self.metadata()
        where = "bin in (" + ",".join(f"'{value}'" for value in bins) + ")"
        count_params = {"$select": "count(*) as count", "$where": where}
        expected = int(self._get(RESOURCE_URL, count_params)[0]["count"])
        if expected < 0 or math.ceil(expected / page_size) > MAX_DATA_PAGES:
            raise ValueError(
                "Complaint snapshot exceeds the bounded request limit; narrow the BIN scope."
            )
        rows = []
        for offset in range(0, expected, page_size):
            batch = self._get(
                RESOURCE_URL,
                {
                    "$where": where,
                    "$order": "complaint_number,bin",
                    "$limit": page_size,
                    "$offset": offset,
                },
            )
            if not isinstance(batch, list) or not batch:
                raise ValueError("DOB complaint snapshot is incomplete.")
            rows.extend(batch)
        after_count = int(self._get(RESOURCE_URL, count_params)[0]["count"])
        after = self.metadata()
        if (
            before["rowsUpdatedAt"] != after["rowsUpdatedAt"]
            or len(rows) != expected
            or after_count != expected
        ):
            raise ValueError(
                "DOB complaints changed during pagination or returned an incomplete snapshot."
            )
        keys = [str(row.get("complaint_number") or "").strip() for row in rows]
        if any(not key for key in keys) or len(keys) != len(set(keys)):
            raise ValueError(
                "Duplicate or missing complaint source keys require review before publication."
            )
        if any(normalize_identifier(row.get("bin"), 7) not in bins for row in rows):
            raise ValueError(
                "DOB complaints returned a record outside the requested exact BIN scope."
            )
        return {
            "source_system": SOURCE_SYSTEM,
            "bins": bins,
            "rows": rows,
            "expected_count": expected,
            "source_updated_at": datetime.fromtimestamp(
                before["rowsUpdatedAt"], timezone.utc
            ),
            "observed_at": datetime.now(timezone.utc),
            "snapshot_hash": payload_hash(rows),
            "complete": True,
        }
