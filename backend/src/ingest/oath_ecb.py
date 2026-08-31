"""Bounded OATH case evidence linked from exact DOB ECB ticket numbers.

The official OATH Hearings Division Case Status source uses a ten-character
ticket representation for many nine-character DOB ECB tickets. This adapter
keeps both identifiers, links only by an exact reviewed normalization rule,
and preserves signed balances because the source can report credits.
"""

import math
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from src.ingest import dob_ecb
from src.ingest.dob_safety import DOBSafetyClient, normalize_identifier, payload_hash

DATASET_ID = "jz4z-kudi"
SOURCE_SYSTEM = "oath_ecb"
DATASET_URL = f"https://data.cityofnewyork.us/d/{DATASET_ID}"
RESOURCE_URL = f"https://data.cityofnewyork.us/resource/{DATASET_ID}.json"
METADATA_URL = f"https://data.cityofnewyork.us/api/views/{DATASET_ID}.json"
PARSER_VERSION = "oath-ecb-v1"
MAX_PILOT_BINS = 25
MAX_PILOT_TICKETS = 250
MAX_DATA_PAGES = 30
MAX_MONEY_CENTS = 1_000_000_000_000
REQUIRED_FIELDS = {
    "ticket_number",
    "issuing_agency",
    "balance_due",
    "violation_location_borough",
    "violation_location_block_no",
    "violation_location_lot_no",
    "violation_location_house",
    "violation_location_street_name",
    "hearing_status",
    "hearing_result",
    "hearing_date",
    "decision_date",
    "date_judgment_docketed",
    "penalty_imposed",
    "paid_amount",
    "additional_penalties_or_late_fees",
    "total_violation_amount",
    "compliance_status",
    "violation_description",
    "violation_details",
}
BOROUGH_CODES = {
    "MANHATTAN": "1",
    "NEW YORK": "1",
    "BRONX": "2",
    "BROOKLYN": "3",
    "QUEENS": "4",
    "STATEN ISLAND": "5",
}


def validate_bins(bins: list[str] | None) -> list[str]:
    return dob_ecb.validate_bins(bins)


def canonical_ticket(value: object) -> str | None:
    raw = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{1,20}", raw):
        return None
    if len(raw) == 10 and raw.startswith("0"):
        return raw[1:]
    return raw


def oath_ticket_candidates(dob_ticket: str) -> tuple[str, ...]:
    ticket = canonical_ticket(dob_ticket)
    if not ticket:
        raise ValueError("DOB ECB seed has no valid ticket number.")
    candidates = [ticket]
    if len(ticket) == 9:
        candidates.append(f"0{ticket}")
    return tuple(candidates)


def _digits(value: object, width: int) -> str | None:
    raw = str(value or "").strip()
    if not raw.isdigit() or len(raw) > width:
        return None
    return raw.zfill(width)


def source_bbl(row: dict) -> str | None:
    borough_raw = str(row.get("violation_location_borough") or "").strip().upper()
    borough = BOROUGH_CODES.get(borough_raw)
    block = _digits(row.get("violation_location_block_no"), 5)
    lot = _digits(row.get("violation_location_lot_no"), 4)
    value = f"{borough}{block}{lot}" if borough and block and lot else None
    return normalize_identifier(value, 10)


def source_date(value: object, field: str, key: str) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid OATH {field} for {key}.") from exc
    return parsed.date()


def money_cents(
    value: object, field: str, key: str, *, signed: bool = False
) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        amount = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid OATH {field} for {key}.") from exc
    cents = amount * 100
    lower = -MAX_MONEY_CENTS if signed else 0
    if cents != cents.to_integral_value() or cents < lower or cents > MAX_MONEY_CENTS:
        raise ValueError(f"Invalid OATH {field} for {key}.")
    return int(cents)


def normalized_status(row: dict) -> str:
    key = str(row.get("ticket_number") or "").strip()
    balance = money_cents(row.get("balance_due"), "balance due", key, signed=True)
    if balance is not None and balance > 0:
        return "active"
    hearing = str(row.get("hearing_status") or "").strip().upper()
    compliance = str(row.get("compliance_status") or "").strip().upper()
    if balance is not None and balance <= 0 and (
        hearing in {"PAID IN FULL", "WRITTEN OFF", "DISMISSED"}
        or compliance in {"ALL TERMS MET", "DISMISSED", "WRITTEN OFF"}
    ):
        return "resolved"
    return "unknown"


def record_url(ticket: str) -> str:
    escaped = ticket.replace("'", "''")
    return RESOURCE_URL + "?" + urlencode({"$where": f"ticket_number='{escaped}'"})


def oath_details(row: dict) -> dict:
    key = str(row.get("ticket_number") or "").strip()
    balance = money_cents(row.get("balance_due"), "balance due", key, signed=True)
    dates = {
        target: source_date(row.get(source), source.replace("_", " "), key)
        for target, source in (
            ("hearing_date", "hearing_date"),
            ("decision_date", "decision_date"),
            ("judgment_docketed_date", "date_judgment_docketed"),
        )
    }
    return {
        **{target: value.isoformat() if value else None for target, value in dates.items()},
        "oath_ticket_number": key or None,
        "linked_dob_ecb_violation_number": str(
            row.get("_linked_dob_ecb_violation_number") or ""
        ).strip()
        or None,
        "issuing_agency": str(row.get("issuing_agency") or "").strip() or None,
        "hearing_status": str(row.get("hearing_status") or "").strip() or None,
        "hearing_result": str(row.get("hearing_result") or "").strip() or None,
        "compliance_status": str(row.get("compliance_status") or "").strip()
        or None,
        "penalty_imposed_cents": money_cents(
            row.get("penalty_imposed"), "penalty imposed", key
        ),
        "amount_paid_cents": money_cents(row.get("paid_amount"), "paid amount", key),
        "additional_penalties_cents": money_cents(
            row.get("additional_penalties_or_late_fees"),
            "additional penalties or late fees",
            key,
        ),
        "total_violation_amount_cents": money_cents(
            row.get("total_violation_amount"), "total violation amount", key
        ),
        "oath_balance_due_cents": balance,
        "oath_balance_character": (
            "unknown"
            if balance is None
            else "amount_due"
            if balance > 0
            else "credit_or_adjustment"
            if balance < 0
            else "zero"
        ),
        "monetary_rollup_status": "record_only_exact_oath_ticket_evidence",
    }


def normalize_record(
    row: dict, *, observed_at: datetime, source_updated_at: datetime | None, run_id: str
) -> dict:
    key = str(row.get("ticket_number") or "").strip().upper()
    canonical = canonical_ticket(key)
    linked = canonical_ticket(row.get("_linked_dob_ecb_violation_number"))
    if not canonical or not linked or canonical != linked:
        raise ValueError("OATH record is not linked to the exact DOB ECB ticket.")
    raw_bin = str(row.get("_source_bin") or "").strip()
    bin_value = normalize_identifier(raw_bin, 7)
    if not bin_value or bin_value != raw_bin:
        raise ValueError(f"OATH record {key} has no reviewed source BIN.")
    bbl = source_bbl(row)
    issue_date = source_date(row.get("violation_date"), "violation date", key)
    oath_details(row)
    address = " ".join(
        str(row.get(field) or "").strip()
        for field in ("violation_location_house", "violation_location_street_name")
    ).strip()
    description = str(row.get("violation_details") or "").strip() or None
    if not description:
        description = str(row.get("violation_description") or "").strip() or None
    return {
        "id": payload_hash([SOURCE_SYSTEM, key])[:32],
        "source_system": SOURCE_SYSTEM,
        "source_record_key": key,
        "record_type": "case_evidence",
        "bin": bin_value,
        "bbl": bbl,
        "address": address or None,
        "category": "OATH_ECB_CASE",
        "violation_type": str(row.get("issuing_agency") or "").strip() or None,
        "device_type": "OATH case status",
        "status": normalized_status(row),
        "issue_date": issue_date,
        "description": description,
        "identity_status": "linked_via_exact_ticket",
        "source_url": record_url(key),
        "source_updated_at": source_updated_at,
        "observed_at": observed_at,
        "payload_hash": payload_hash(row),
        "parser_version": PARSER_VERSION,
        "ingestion_run_id": run_id,
        "raw_payload": row,
    }


def _validated_seed_map(ecb_rows: list[dict], bins: list[str]) -> dict[str, dict]:
    seeds: dict[str, dict] = {}
    for row in ecb_rows:
        bin_value = normalize_identifier(row.get("bin"), 7)
        ticket = canonical_ticket(row.get("ecb_violation_number"))
        if bin_value not in bins or not ticket:
            raise ValueError("DOB ECB seed is outside the exact OATH pilot scope.")
        previous = seeds.get(ticket)
        if previous and previous["bin"] != bin_value:
            raise ValueError("One DOB ECB ticket maps to multiple reviewed BINs.")
        seeds[ticket] = {"bin": bin_value, "ticket": ticket}
    if len(seeds) > MAX_PILOT_TICKETS:
        raise ValueError(
            f"OATH pilot exceeds {MAX_PILOT_TICKETS} exact DOB ECB tickets; narrow the BIN scope."
        )
    return seeds


class OATHECBClient(DOBSafetyClient):
    """Complete exact-ticket OATH snapshots under a fixed request ceiling."""

    def metadata(self) -> dict:
        metadata = self._get(METADATA_URL)
        fields = {column.get("fieldName") for column in metadata.get("columns", [])}
        missing = REQUIRED_FIELDS - fields
        if missing:
            raise ValueError(f"OATH schema drift: missing {sorted(missing)}")
        if not metadata.get("rowsUpdatedAt"):
            raise ValueError("OATH publication timestamp is unavailable.")
        return metadata

    def fetch_seeded_snapshot(
        self, bins: list[str], ecb_rows: list[dict], *, page_size: int = 1000
    ) -> dict:
        bins = validate_bins(bins)
        if not 1 <= page_size <= 1000:
            raise ValueError("OATH page size must be between 1 and 1000.")
        seeds = _validated_seed_map(ecb_rows, bins)
        before = self.metadata()
        if not seeds:
            return {
                "source_system": SOURCE_SYSTEM,
                "bins": bins,
                "rows": [],
                "expected_count": 0,
                "source_updated_at": datetime.fromtimestamp(
                    before["rowsUpdatedAt"], timezone.utc
                ),
                "observed_at": datetime.now(timezone.utc),
                "snapshot_hash": payload_hash([]),
                "complete": True,
            }
        candidates = sorted(
            {candidate for ticket in seeds for candidate in oath_ticket_candidates(ticket)}
        )
        where = "ticket_number in (" + ",".join(
            f"'{ticket.replace(chr(39), chr(39) * 2)}'" for ticket in candidates
        ) + ")"
        count_params = {"$select": "count(*) as count", "$where": where}
        expected = int(self._get(RESOURCE_URL, count_params)[0]["count"])
        if expected < 0 or math.ceil(expected / page_size) > MAX_DATA_PAGES:
            raise ValueError(
                "OATH snapshot exceeds the bounded request limit; narrow the BIN scope."
            )
        rows = []
        for offset in range(0, expected, page_size):
            batch = self._get(
                RESOURCE_URL,
                {
                    "$where": where,
                    "$order": "ticket_number",
                    "$limit": page_size,
                    "$offset": offset,
                },
            )
            if not isinstance(batch, list) or not batch:
                raise ValueError("OATH snapshot is incomplete.")
            rows.extend(batch)
        after_count = int(self._get(RESOURCE_URL, count_params)[0]["count"])
        after = self.metadata()
        if (
            before["rowsUpdatedAt"] != after["rowsUpdatedAt"]
            or len(rows) != expected
            or after_count != expected
        ):
            raise ValueError(
                "OATH changed during pagination or returned an incomplete snapshot."
            )
        seen = set()
        enriched = []
        for row in rows:
            exact = str(row.get("ticket_number") or "").strip().upper()
            canonical = canonical_ticket(exact)
            seed = seeds.get(canonical or "")
            if not exact or not seed or exact in seen:
                raise ValueError(
                    "Duplicate, missing, or out-of-scope OATH ticket requires review."
                )
            seen.add(exact)
            enriched.append(
                {
                    **row,
                    "_source_bin": seed["bin"],
                    "_linked_dob_ecb_violation_number": seed["ticket"],
                }
            )
        return {
            "source_system": SOURCE_SYSTEM,
            "bins": bins,
            "rows": enriched,
            "expected_count": expected,
            "source_updated_at": datetime.fromtimestamp(
                before["rowsUpdatedAt"], timezone.utc
            ),
            "observed_at": datetime.now(timezone.utc),
            "snapshot_hash": payload_hash(enriched),
            "complete": True,
        }


class OATHFromDOBECBClient(OATHECBClient):
    """Resolve exact OATH case seeds from the same bounded DOB ECB BIN scope."""

    def fetch_snapshot(self, bins: list[str], *, page_size: int = 1000) -> dict:
        bins = validate_bins(bins)
        ecb_snapshot = dob_ecb.DOBECBClient(session=self.session).fetch_snapshot(bins)
        return self.fetch_seeded_snapshot(bins, ecb_snapshot["rows"], page_size=page_size)
