"""Bounded, complete DOB Safety snapshots from official NYC Open Data.

Metadata verified 2026-08-31. Dataset 855j-jady has no monetary fields.
Issue dates are not incremental cursors because historical statuses change.
"""

import hashlib
import json
import os
import re
from datetime import date, datetime, timezone
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DATASET_ID = "855j-jady"
SOURCE_SYSTEM = "dob_safety"
DATASET_URL = f"https://data.cityofnewyork.us/d/{DATASET_ID}"
RESOURCE_URL = f"https://data.cityofnewyork.us/resource/{DATASET_ID}.json"
METADATA_URL = f"https://data.cityofnewyork.us/api/views/{DATASET_ID}.json"
PARSER_VERSION = "dob-safety-v1"
MAX_PILOT_BINS = 100
REQUIRED_FIELDS = {
    "bin",
    "bbl",
    "violation_number",
    "violation_issue_date",
    "violation_type",
    "violation_status",
    "violation_remarks",
    "device_type",
    "house_number",
    "street",
}


def payload_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def normalize_identifier(value: object, length: int) -> str | None:
    raw = str(value or "").strip()
    raw = raw.removesuffix(".0")
    return raw if re.fullmatch(rf"[1-5]\d{{{length - 1}}}", raw) else None


def validate_bins(bins: list[str] | None) -> list[str]:
    if not bins or len(bins) > MAX_PILOT_BINS:
        raise ValueError(f"Provide 1-{MAX_PILOT_BINS} explicit DOB BINs for the pilot.")
    if any(normalize_identifier(value, 7) != value for value in bins):
        raise ValueError("Every pilot BIN must be an exact seven-digit DOB BIN.")
    return sorted(set(bins))


def record_url(violation_number: str) -> str:
    escaped = violation_number.replace("'", "''")
    return RESOURCE_URL + "?" + urlencode({"$where": f"violation_number='{escaped}'"})


def normalize_record(
    row: dict, *, observed_at: datetime, source_updated_at: datetime | None, run_id: str
) -> dict:
    key = str(row.get("violation_number") or "").strip()
    if not key or len(key) > 160:
        raise ValueError("DOB Safety record has no valid source violation number.")
    bin_value = normalize_identifier(row.get("bin"), 7)
    bbl = normalize_identifier(row.get("bbl"), 10)
    identity_status = "exact_source_bin" if bin_value else "unresolved"
    if bin_value and bbl and bin_value[0] != bbl[0]:
        identity_status = "conflicting_source_identifiers"
    issue_date = None
    if row.get("violation_issue_date"):
        try:
            issue_date = date.fromisoformat(str(row["violation_issue_date"])[:10])
        except ValueError as exc:
            raise ValueError(f"Invalid DOB Safety issue date for {key}") from exc
    device = str(row.get("device_type") or "")
    violation_type = str(row.get("violation_type") or "")
    category = (
        "LL152"
        if "LL152" in device.upper() or violation_type == "FTF-PL-PER"
        else "DOB_SAFETY_OTHER"
    )
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
        "category": category,
        "violation_type": violation_type or None,
        "device_type": device or None,
        "status": row.get("violation_status"),
        "issue_date": issue_date,
        "description": row.get("violation_remarks"),
        "identity_status": identity_status,
        "source_url": record_url(key),
        "source_updated_at": source_updated_at,
        "observed_at": observed_at,
        "payload_hash": payload_hash(row),
        "parser_version": PARSER_VERSION,
        "ingestion_run_id": run_id,
        "raw_payload": row,
    }


class DOBSafetyClient:
    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        if session is None:
            retry = Retry(
                total=3,
                backoff_factor=0.5,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["GET"],
            )
            self.session.mount("https://", HTTPAdapter(max_retries=retry))
            token = os.environ.get("NYC_OPEN_DATA_APP_TOKEN")
            if token:
                self.session.headers["X-App-Token"] = token

    def _get(self, url: str, params: dict | None = None):
        response = self.session.get(url, params=params, timeout=(15, 60))
        response.raise_for_status()
        return response.json()

    def metadata(self) -> dict:
        metadata = self._get(METADATA_URL)
        fields = {column.get("fieldName") for column in metadata.get("columns", [])}
        missing = REQUIRED_FIELDS - fields
        if missing:
            raise ValueError(f"DOB Safety schema drift: missing {sorted(missing)}")
        if not metadata.get("rowsUpdatedAt"):
            raise ValueError("DOB Safety publication timestamp is unavailable.")
        return metadata

    def fetch_snapshot(self, bins: list[str], *, page_size: int = 1000) -> dict:
        bins = validate_bins(bins)
        before = self.metadata()
        where = "bin in (" + ",".join(f"'{value}'" for value in bins) + ")"
        counts = self._get(
            RESOURCE_URL, {"$select": "count(*) as count", "$where": where}
        )
        expected = int(counts[0]["count"])
        if expected > 100000:
            raise ValueError("Pilot source volume exceeds the bounded safety limit.")
        rows = []
        for offset in range(0, expected, page_size):
            batch = self._get(
                RESOURCE_URL,
                {
                    "$where": where,
                    "$order": "violation_number,bin",
                    "$limit": page_size,
                    "$offset": offset,
                },
            )
            if not isinstance(batch, list) or not batch:
                raise ValueError("DOB Safety snapshot is incomplete.")
            rows.extend(batch)
        after = self.metadata()
        if before["rowsUpdatedAt"] != after["rowsUpdatedAt"] or len(rows) != expected:
            raise ValueError(
                "DOB Safety changed during pagination or returned an incomplete snapshot."
            )
        keys = [str(row.get("violation_number") or "") for row in rows]
        if len(keys) != len(set(keys)):
            raise ValueError(
                "Duplicate DOB Safety source keys require review before publication."
            )
        if any(normalize_identifier(row.get("bin"), 7) not in bins for row in rows):
            raise ValueError(
                "DOB Safety returned a record outside the requested exact BIN scope."
            )
        return {
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
