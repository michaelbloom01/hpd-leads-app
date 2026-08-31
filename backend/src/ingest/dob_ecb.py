"""Bounded DOB ECB violation snapshots from official NYC Open Data.

Dataset ``6bgk-3dad`` provides ticket status, hearing and certification fields,
plus record-level monetary fields. Monetary values remain attached to the
exact ECB record in this slice. They do not enter portfolio rollups until
ECB/OATH mirror deduplication is implemented and reviewed.
"""

import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from src.ingest.dob_safety import DOBSafetyClient, normalize_identifier, payload_hash
from src.ingest.dob_violations import source_bbl, source_date

DATASET_ID = "6bgk-3dad"
SOURCE_SYSTEM = "dob_ecb"
DATASET_URL = f"https://data.cityofnewyork.us/d/{DATASET_ID}"
RESOURCE_URL = f"https://data.cityofnewyork.us/resource/{DATASET_ID}.json"
METADATA_URL = f"https://data.cityofnewyork.us/api/views/{DATASET_ID}.json"
PARSER_VERSION = "dob-ecb-v1"
MAX_PILOT_BINS = 25
MAX_DATA_PAGES = 30
MAX_MONEY_CENTS = 1_000_000_000_000
REQUIRED_FIELDS = {
    "isn_dob_bis_extract",
    "ecb_violation_number",
    "ecb_violation_status",
    "dob_violation_number",
    "bin",
    "boro",
    "block",
    "lot",
    "hearing_date",
    "served_date",
    "issue_date",
    "severity",
    "violation_type",
    "respondent_name",
    "violation_description",
    "penality_imposed",
    "amount_paid",
    "balance_due",
    "hearing_status",
    "certification_status",
}


def validate_bins(bins: list[str] | None) -> list[str]:
    if not bins or len(bins) > MAX_PILOT_BINS:
        raise ValueError(
            f"Provide 1-{MAX_PILOT_BINS} explicit DOB BINs for the ECB pilot."
        )
    if any(normalize_identifier(value, 7) != value for value in bins):
        raise ValueError("Every pilot BIN must be an exact seven-digit DOB BIN.")
    return sorted(set(bins))


def money_cents(value: object, field: str, key: str) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        amount = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid DOB ECB {field} for {key}.") from exc
    cents = amount * 100
    if cents != cents.to_integral_value() or cents < 0 or cents > MAX_MONEY_CENTS:
        raise ValueError(f"Invalid DOB ECB {field} for {key}.")
    return int(cents)


def normalized_status(value: object) -> str:
    raw = str(value or "").strip().upper()
    if raw == "ACTIVE":
        return "active"
    if raw == "RESOLVE":
        return "resolved"
    return "unknown"


def record_url(ticket: str) -> str:
    escaped = ticket.replace("'", "''")
    return RESOURCE_URL + "?" + urlencode(
        {"$where": f"ecb_violation_number='{escaped}'"}
    )


def ecb_details(row: dict) -> dict:
    key = str(row.get("ecb_violation_number") or "").strip()
    dates = {}
    for target, source in (
        ("served_date", "served_date"),
        ("hearing_date", "hearing_date"),
    ):
        parsed = source_date(row.get(source), source.replace("_", " "), key)
        dates[target] = parsed.isoformat() if parsed else None
    return {
        **dates,
        "ecb_violation_number": key or None,
        "dob_violation_number": str(row.get("dob_violation_number") or "").strip()
        or None,
        "hearing_status": str(row.get("hearing_status") or "").strip() or None,
        "certification_status": str(row.get("certification_status") or "").strip()
        or None,
        "severity": str(row.get("severity") or "").strip() or None,
        "respondent_name": str(row.get("respondent_name") or "").strip() or None,
        "penalty_imposed_cents": money_cents(
            row.get("penality_imposed"), "penalty imposed", key
        ),
        "amount_paid_cents": money_cents(row.get("amount_paid"), "amount paid", key),
        "balance_due_cents": money_cents(row.get("balance_due"), "balance due", key),
        "monetary_rollup_status": "record_only_pending_ecb_oath_deduplication",
    }


def normalize_record(
    row: dict, *, observed_at: datetime, source_updated_at: datetime | None, run_id: str
) -> dict:
    key = str(row.get("ecb_violation_number") or "").strip()
    if not key or len(key) > 160:
        raise ValueError("DOB ECB record has no valid violation number.")
    source_isn = str(row.get("isn_dob_bis_extract") or "").strip()
    if not source_isn.isdigit():
        raise ValueError(f"DOB ECB record {key} has no valid source ISN.")
    bin_value = normalize_identifier(row.get("bin"), 7)
    bbl = source_bbl(row)
    identity_status = "exact_source_bin" if bin_value else "unresolved"
    if bin_value and bbl and bin_value[0] != bbl[0]:
        identity_status = "conflicting_source_identifiers"
    issue_date = source_date(row.get("issue_date"), "issue date", key)
    ecb_details(row)
    return {
        "id": payload_hash([SOURCE_SYSTEM, key])[:32],
        "source_system": SOURCE_SYSTEM,
        "source_record_key": key,
        "record_type": "violation",
        "bin": bin_value,
        "bbl": bbl,
        "address": None,
        "category": "DOB_ECB_VIOLATION",
        "violation_type": str(row.get("violation_type") or "").strip() or None,
        "device_type": str(row.get("severity") or "").strip() or None,
        "status": normalized_status(row.get("ecb_violation_status")),
        "issue_date": issue_date,
        "description": str(row.get("violation_description") or "").strip() or None,
        "identity_status": identity_status,
        "source_url": record_url(key),
        "source_updated_at": source_updated_at,
        "observed_at": observed_at,
        "payload_hash": payload_hash(row),
        "parser_version": PARSER_VERSION,
        "ingestion_run_id": run_id,
        "raw_payload": row,
    }


class DOBECBClient(DOBSafetyClient):
    """Complete exact-BIN snapshots under a fixed request ceiling."""

    def metadata(self) -> dict:
        metadata = self._get(METADATA_URL)
        fields = {column.get("fieldName") for column in metadata.get("columns", [])}
        missing = REQUIRED_FIELDS - fields
        if missing:
            raise ValueError(f"DOB ECB schema drift: missing {sorted(missing)}")
        if not metadata.get("rowsUpdatedAt"):
            raise ValueError("DOB ECB publication timestamp is unavailable.")
        return metadata

    def fetch_snapshot(self, bins: list[str], *, page_size: int = 1000) -> dict:
        bins = validate_bins(bins)
        if not 1 <= page_size <= 1000:
            raise ValueError("ECB page size must be between 1 and 1000.")
        before = self.metadata()
        where = "bin in (" + ",".join(f"'{value}'" for value in bins) + ")"
        count_params = {"$select": "count(*) as count", "$where": where}
        expected = int(self._get(RESOURCE_URL, count_params)[0]["count"])
        if expected < 0 or math.ceil(expected / page_size) > MAX_DATA_PAGES:
            raise ValueError(
                "DOB ECB snapshot exceeds the bounded request limit; narrow the BIN scope."
            )
        rows = []
        for offset in range(0, expected, page_size):
            batch = self._get(
                RESOURCE_URL,
                {
                    "$where": where,
                    "$order": "ecb_violation_number,bin",
                    "$limit": page_size,
                    "$offset": offset,
                },
            )
            if not isinstance(batch, list) or not batch:
                raise ValueError("DOB ECB snapshot is incomplete.")
            rows.extend(batch)
        after_count = int(self._get(RESOURCE_URL, count_params)[0]["count"])
        after = self.metadata()
        if (
            before["rowsUpdatedAt"] != after["rowsUpdatedAt"]
            or len(rows) != expected
            or after_count != expected
        ):
            raise ValueError(
                "DOB ECB changed during pagination or returned an incomplete snapshot."
            )
        keys = [str(row.get("ecb_violation_number") or "").strip() for row in rows]
        if any(not key for key in keys) or len(keys) != len(set(keys)):
            raise ValueError(
                "Duplicate or missing DOB ECB violation numbers require review before publication."
            )
        if any(normalize_identifier(row.get("bin"), 7) not in bins for row in rows):
            raise ValueError("DOB ECB returned a record outside the requested exact BIN scope.")
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
