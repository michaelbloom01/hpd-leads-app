"""Bounded legacy DOB violation snapshots from official NYC Open Data.

Dataset ``3h2n-5cm9`` is the legacy BIS violation feed. It is useful for
historical status and disposition context. It contains no reliable monetary
balance and no complaint-to-violation relationship field.
"""

import math
import re
from datetime import date, datetime, timezone
from urllib.parse import urlencode

from src.ingest.dob_safety import DOBSafetyClient, normalize_identifier, payload_hash

DATASET_ID = "3h2n-5cm9"
SOURCE_SYSTEM = "dob_violations"
DATASET_URL = f"https://data.cityofnewyork.us/d/{DATASET_ID}"
RESOURCE_URL = f"https://data.cityofnewyork.us/resource/{DATASET_ID}.json"
METADATA_URL = f"https://data.cityofnewyork.us/api/views/{DATASET_ID}.json"
PARSER_VERSION = "dob-violations-v1"
MAX_PILOT_BINS = 25
MAX_DATA_PAGES = 30
REQUIRED_FIELDS = {
    "isn_dob_bis_viol",
    "boro",
    "bin",
    "block",
    "lot",
    "issue_date",
    "violation_type_code",
    "violation_number",
    "house_number",
    "street",
    "disposition_date",
    "disposition_comments",
    "description",
    "ecb_number",
    "number",
    "violation_category",
    "violation_type",
}


def validate_bins(bins: list[str] | None) -> list[str]:
    if not bins or len(bins) > MAX_PILOT_BINS:
        raise ValueError(
            f"Provide 1-{MAX_PILOT_BINS} explicit DOB BINs for the violation pilot."
        )
    if any(normalize_identifier(value, 7) != value for value in bins):
        raise ValueError("Every pilot BIN must be an exact seven-digit DOB BIN.")
    return sorted(set(bins))


def _digits(value: object, width: int) -> str | None:
    raw = str(value or "").strip()
    if not raw.isdigit():
        return None
    if len(raw) > width:
        prefix, raw = raw[:-width], raw[-width:]
        if set(prefix) - {"0"}:
            return None
    return raw.zfill(width)


def source_bbl(row: dict) -> str | None:
    boro = _digits(row.get("boro"), 1)
    block = _digits(row.get("block"), 5)
    lot = _digits(row.get("lot"), 4)
    value = f"{boro}{block}{lot}" if boro and block and lot else None
    return normalize_identifier(value, 10)


def source_date(value: object, field: str, key: str) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if not re.fullmatch(r"\d{8}", raw):
        raise ValueError(f"Invalid DOB violations {field} for {key}.")
    try:
        return datetime.strptime(raw, "%Y%m%d").date()  # noqa: DTZ007
    except ValueError as exc:
        raise ValueError(f"Invalid DOB violations {field} for {key}.") from exc


def normalized_status(value: object) -> str:
    raw = str(value or "").strip().upper()
    if "ACTIVE" in raw:
        return "active"
    if "DISMISSED" in raw or "RESOLVED" in raw:
        return "resolved"
    return "unknown"


def record_url(source_key: str) -> str:
    escaped = source_key.replace("'", "''")
    return RESOURCE_URL + "?" + urlencode(
        {"$where": f"isn_dob_bis_viol='{escaped}'"}
    )


def violation_details(row: dict) -> dict:
    key = str(row.get("isn_dob_bis_viol") or "").strip()
    disposition = source_date(row.get("disposition_date"), "disposition date", key)
    return {
        "dob_violation_isn": key or None,
        "dob_violation_number": str(row.get("violation_number") or "").strip()
        or None,
        "display_number": str(row.get("number") or "").strip() or None,
        "ecb_number": str(row.get("ecb_number") or "").strip() or None,
        "violation_category_raw": str(row.get("violation_category") or "").strip()
        or None,
        "disposition_date": disposition.isoformat() if disposition else None,
        "disposition_comments": str(row.get("disposition_comments") or "").strip()
        or None,
    }


def normalize_record(
    row: dict, *, observed_at: datetime, source_updated_at: datetime | None, run_id: str
) -> dict:
    key = str(row.get("isn_dob_bis_viol") or "").strip()
    if not key.isdigit() or len(key) > 160:
        raise ValueError("DOB violation record has no valid source ISN.")
    bin_value = normalize_identifier(row.get("bin"), 7)
    bbl = source_bbl(row)
    identity_status = "exact_source_bin" if bin_value else "unresolved"
    if bin_value and bbl and bin_value[0] != bbl[0]:
        identity_status = "conflicting_source_identifiers"
    issue_date = source_date(row.get("issue_date"), "issue date", key)
    source_date(row.get("disposition_date"), "disposition date", key)
    description = str(row.get("description") or "").strip() or None
    if not description:
        description = str(row.get("disposition_comments") or "").strip() or None
    return {
        "id": payload_hash([SOURCE_SYSTEM, key])[:32],
        "source_system": SOURCE_SYSTEM,
        "source_record_key": key,
        "record_type": "violation",
        "bin": bin_value,
        "bbl": bbl,
        "address": " ".join(
            str(row.get(field) or "").strip() for field in ("house_number", "street")
        ).strip()
        or None,
        "category": "DOB_LEGACY_VIOLATION",
        "violation_type": str(row.get("violation_type") or "").strip() or None,
        "device_type": str(row.get("violation_type_code") or "").strip() or None,
        "status": normalized_status(row.get("violation_category")),
        "issue_date": issue_date,
        "description": description,
        "identity_status": identity_status,
        "source_url": record_url(key),
        "source_updated_at": source_updated_at,
        "observed_at": observed_at,
        "payload_hash": payload_hash(row),
        "parser_version": PARSER_VERSION,
        "ingestion_run_id": run_id,
        "raw_payload": row,
    }


class DOBViolationsClient(DOBSafetyClient):
    """Complete exact-BIN snapshots under a fixed request ceiling."""

    def metadata(self) -> dict:
        metadata = self._get(METADATA_URL)
        fields = {column.get("fieldName") for column in metadata.get("columns", [])}
        missing = REQUIRED_FIELDS - fields
        if missing:
            raise ValueError(f"DOB violations schema drift: missing {sorted(missing)}")
        if not metadata.get("rowsUpdatedAt"):
            raise ValueError("DOB violations publication timestamp is unavailable.")
        return metadata

    def fetch_snapshot(self, bins: list[str], *, page_size: int = 1000) -> dict:
        bins = validate_bins(bins)
        if not 1 <= page_size <= 1000:
            raise ValueError("Violation page size must be between 1 and 1000.")
        before = self.metadata()
        where = "bin in (" + ",".join(f"'{value}'" for value in bins) + ")"
        count_params = {"$select": "count(*) as count", "$where": where}
        expected = int(self._get(RESOURCE_URL, count_params)[0]["count"])
        if expected < 0 or math.ceil(expected / page_size) > MAX_DATA_PAGES:
            raise ValueError(
                "DOB violation snapshot exceeds the bounded request limit; narrow the BIN scope."
            )
        rows = []
        for offset in range(0, expected, page_size):
            batch = self._get(
                RESOURCE_URL,
                {
                    "$where": where,
                    "$order": "isn_dob_bis_viol,bin",
                    "$limit": page_size,
                    "$offset": offset,
                },
            )
            if not isinstance(batch, list) or not batch:
                raise ValueError("DOB violation snapshot is incomplete.")
            rows.extend(batch)
        after_count = int(self._get(RESOURCE_URL, count_params)[0]["count"])
        after = self.metadata()
        if (
            before["rowsUpdatedAt"] != after["rowsUpdatedAt"]
            or len(rows) != expected
            or after_count != expected
        ):
            raise ValueError(
                "DOB violations changed during pagination or returned an incomplete snapshot."
            )
        keys = [str(row.get("isn_dob_bis_viol") or "").strip() for row in rows]
        if any(not key for key in keys) or len(keys) != len(set(keys)):
            raise ValueError(
                "Duplicate or missing DOB violation source keys require review before publication."
            )
        if any(normalize_identifier(row.get("bin"), 7) not in bins for row in rows):
            raise ValueError(
                "DOB violations returned a record outside the requested exact BIN scope."
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
