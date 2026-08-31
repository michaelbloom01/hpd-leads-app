"""Complete, explicitly bounded HPD identity evidence for the compliance pilot."""

import hashlib
import json
import os
import re
from datetime import datetime, timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

REGISTRATIONS = "tesw-yqqr"
CONTACTS = "feu5-w2e2"
SOURCE_SYSTEM = "hpd_identity_pilot"
PARSER_VERSION = "hpd-identity-pilot-v1"
MAX_BINS = 25
MAX_REGISTRATION_ROWS = 5000
MAX_CONTACT_ROWS = 25000
MAX_EVIDENCE_BYTES = 5_000_000
ROW_ID = "__pilot_row_id"
FIELDS = {
    REGISTRATIONS: (
        "boroid", "block", "lot", "bin", "buildingid", "registrationid",
        "housenumber", "streetname", "zip", "boro", "lastregistrationdate", "registrationenddate",
    ),
    CONTACTS: (
        "registrationcontactid", "registrationid", "type", "contactdescription",
        "corporationname", "firstname", "lastname", "title", "businesshousenumber",
        "businessstreetname", "businessapartment", "businesscity", "businessstate", "businesszip",
    ),
}


class IdentityPilotError(ValueError):
    def __init__(self, code: str, **details):
        self.code = code
        self.details = {"code": code, **details}
        super().__init__(code + (": " + json.dumps(details, sort_keys=True, default=str) if details else ""))


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def validate_bins(bins: list[str] | None) -> list[str]:
    if not isinstance(bins, list) or not 1 <= len(bins) <= MAX_BINS:
        raise IdentityPilotError("provide_1_to_25_explicit_bins")
    if any(not isinstance(value, str) or not re.fullmatch(r"[1-5][0-9]{6}", value) for value in bins):
        raise IdentityPilotError("exact_seven_digit_bins_required")
    return sorted(set(bins))


def _source_id(value: object, field: str) -> str:
    value = str(value or "").strip()
    if not value.isdigit() or not 0 < len(value) <= 20 or int(value) <= 0:
        raise IdentityPilotError("invalid_source_identifier", field=field)
    return value


def _in(field: str, values: list[str]) -> str:
    # Every value is validated as digits before becoming a SoQL predicate.
    return field + " in (" + ",".join(f"'{value}'" for value in sorted(set(values))) + ")"


def _identity_evidence(rows: list[dict]) -> list[dict]:
    return [
        {field: row.get(field) for field in ("bin", "buildingid", "registrationid", "boroid", "block", "lot")}
        for row in rows[:100]
    ]


class HPDIdentityPilotClient:
    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        if session is None:
            retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"])
            self.session.mount("https://", HTTPAdapter(max_retries=retry))
            token = os.environ.get("NYC_OPEN_DATA_APP_TOKEN")
            if token:
                self.session.headers["X-App-Token"] = token

    def _get(self, dataset: str, params: dict | None = None, *, metadata: bool = False):
        url = (
            f"https://data.cityofnewyork.us/api/views/{dataset}.json" if metadata
            else f"https://data.cityofnewyork.us/resource/{dataset}.json"
        )
        response = self.session.get(url, params=params, timeout=(15, 60))
        response.raise_for_status()
        return response.json()

    def metadata(self, dataset: str) -> dict:
        data = self._get(dataset, metadata=True)
        fields = {column.get("fieldName") for column in data.get("columns", [])}
        missing = set(FIELDS[dataset]) - fields
        if missing or not data.get("rowsUpdatedAt"):
            raise IdentityPilotError("hpd_schema_or_publication_marker_unavailable", dataset=dataset, missing_fields=sorted(missing))
        return {"rows_updated_at": data["rowsUpdatedAt"]}

    def _fetch_complete(self, dataset: str, where: str, *, limit: int, page_size: int) -> tuple[list[dict], dict]:
        def count() -> int:
            rows = self._get(dataset, {"$where": where, "$select": "count(*) as count"})
            if not isinstance(rows, list) or len(rows) != 1 or "count" not in rows[0]:
                raise IdentityPilotError("source_count_unavailable", dataset=dataset)
            return int(rows[0]["count"])

        expected = count()
        if expected < 0 or expected > limit:
            raise IdentityPilotError("bounded_source_volume_exceeded", dataset=dataset, expected=expected, limit=limit)
        rows, seen = [], set()
        for offset in range(0, expected, page_size):
            batch = self._get(dataset, {
                "$where": where, "$select": ":id as " + ROW_ID + "," + ",".join(FIELDS[dataset]),
                "$order": ":id", "$limit": page_size, "$offset": offset,
            })
            if not isinstance(batch, list) or len(batch) != min(page_size, expected - offset):
                raise IdentityPilotError("incomplete_source_page", dataset=dataset, offset=offset)
            for row in batch:
                key = row.get(ROW_ID)
                if not key or key in seen:
                    raise IdentityPilotError("missing_or_duplicate_source_row_id", dataset=dataset)
                seen.add(key)
                rows.append(row)
        if count() != expected:
            raise IdentityPilotError("source_count_changed", dataset=dataset)
        return rows, {
            "dataset": dataset, "where": where, "count": expected,
            "content_digest": fingerprint(sorted(fingerprint(row) for row in rows)),
        }

    def fetch_snapshot(self, bins: list[str], *, page_size: int = 1000) -> dict:
        bins = validate_bins(bins)
        if not isinstance(page_size, int) or not 1 <= page_size <= 1000:
            raise IdentityPilotError("invalid_page_size")
        before = {dataset: self.metadata(dataset) for dataset in FIELDS}
        seed, seed_check = self._fetch_complete(REGISTRATIONS, _in("bin", bins), limit=MAX_REGISTRATION_ROWS, page_size=page_size)
        if {str(row.get("bin") or "") for row in seed} != set(bins):
            raise IdentityPilotError("requested_bins_missing_or_source_out_of_scope", requested_bins=bins, evidence=_identity_evidence(seed))
        hpd_ids = sorted({_source_id(row.get("buildingid"), "buildingid") for row in seed})
        reg_ids = sorted({_source_id(row.get("registrationid"), "registrationid") for row in seed})
        parcel_clauses = set()
        for row in seed:
            components = [_source_id(row.get(field), field) for field in ("boroid", "block", "lot")]
            parcel_clauses.add("(" + " AND ".join(f"{field}='{value}'" for field, value in zip(("boroid", "block", "lot"), components)) + ")")
        where = " OR ".join([
            _in("bin", bins), _in("buildingid", hpd_ids), _in("registrationid", reg_ids), *sorted(parcel_clauses),
        ])
        registrations, related_check = self._fetch_complete(REGISTRATIONS, where, limit=MAX_REGISTRATION_ROWS, page_size=page_size)
        related_by_id = {row[ROW_ID]: row for row in registrations}
        if any(related_by_id.get(row[ROW_ID]) != row for row in seed):
            raise IdentityPilotError("seed_rows_changed_or_omitted_from_sibling_query")
        discovered_bins = {str(row.get("bin") or "") for row in registrations}
        if discovered_bins - set(bins):
            raise IdentityPilotError(
                "omitted_or_conflicting_sibling_bins", requested_bins=bins,
                additional_bins=sorted(discovered_bins - set(bins)),
                evidence=_identity_evidence(registrations), source_stamps=before,
            )
        reg_ids = sorted({_source_id(row.get("registrationid"), "registrationid") for row in registrations})
        contacts, contact_check = self._fetch_complete(CONTACTS, _in("registrationid", reg_ids), limit=MAX_CONTACT_ROWS, page_size=page_size)
        if any(str(row.get("registrationid") or "") not in reg_ids for row in contacts):
            raise IdentityPilotError("contacts_outside_complete_registration_scope")
        after = {dataset: self.metadata(dataset) for dataset in FIELDS}
        if before != after:
            raise IdentityPilotError("source_publication_changed_during_fetch")
        # Transient pagination identifiers never enter durable HPD raw payloads.
        registrations = [{key: value for key, value in row.items() if key != ROW_ID} for row in registrations]
        contacts = [{key: value for key, value in row.items() if key != ROW_ID} for row in contacts]
        from src.tasks.ingest import _prepare_building_refresh_snapshot

        prepared = _prepare_building_refresh_snapshot(
            registrations, contacts, [],
            source_updated_at=datetime.fromtimestamp(before[REGISTRATIONS]["rows_updated_at"], timezone.utc),
        )
        if prepared["quarantine"] or prepared["stats"]["rejected_contacts"]:
            raise IdentityPilotError("source_identity_or_contact_conflicts", evidence=[
                {"source_record_key": row["source_record_key"], "reason": row["reason"]}
                for row in prepared["quarantine"][:100]
            ])
        if {row["bin"] for row in prepared["physical_buildings"]} != set(bins):
            raise IdentityPilotError("current_identity_does_not_cover_every_requested_bin")
        for row in prepared["physical_buildings"]:
            if len(str(row.get("borough") or "")) > 20 or len(str(row.get("zip") or "")) > 10:
                raise IdentityPilotError("physical_identity_text_exceeds_schema")
        evidence_bytes = sum(len(json.dumps(row["raw_payload"]).encode()) for row in prepared["registration_snapshots"])
        if evidence_bytes > MAX_EVIDENCE_BYTES:
            raise IdentityPilotError("pilot_evidence_storage_limit_exceeded", bytes=evidence_bytes, limit=MAX_EVIDENCE_BYTES)
        checks = [seed_check, related_check, contact_check]
        prepared.update({
            "bins": bins, "hpd_building_ids": sorted({row["source_record_key"] for row in prepared["physical_buildings"]}),
            "source_stamps": before, "source_checks": checks, "observed_at": datetime.now(timezone.utc).isoformat(),
            "source_fingerprint": fingerprint({"parser": PARSER_VERSION, "bins": bins, "stamps": before, "checks": checks}),
            "evidence_bytes": evidence_bytes, "parser_version": PARSER_VERSION,
        })
        return prepared
