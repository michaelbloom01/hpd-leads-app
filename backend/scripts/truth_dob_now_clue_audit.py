"""Read-only DOB NOW filing clue audit for truth source acquisition.

This script inspects official DOB NOW Build job-application exports for exact
property and party-name clues. DOB filings are role-ambiguous for property
management, so this script only emits source-acquisition clues. It never emits
manual-evidence candidates, records evidence, marks claims verified, refreshes
sources, or mutates local tables.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.source_evidence_intake import build_source_acquisition_clue_only_preview  # noqa: E402


DOB_NOW_DATASET_ID = "w9ak-ipjd"
DOB_NOW_API_URL = f"https://data.cityofnewyork.us/resource/{DOB_NOW_DATASET_ID}.json"
DOB_NOW_DOWNLOAD_URL = f"https://data.cityofnewyork.us/api/views/{DOB_NOW_DATASET_ID}/rows.csv?accessType=DOWNLOAD"
DOB_NOW_CATALOG_URL = f"https://data.cityofnewyork.us/d/{DOB_NOW_DATASET_ID}"

DEFAULT_TARGETS: tuple[dict[str, Any], ...] = (
    {
        "target_id": "md2-57-bond-dob-now-clue",
        "address": "57 BOND STREET",
        "bbl": "1005297507",
        "expected_party": "Md2 Property Group",
        "expected_manager": "MD Squared Property Group",
        "manager_lead_id": "56a71624c6c0",
        "aliases": ("57 BOND", "57B BOND", "57 BOND STREET", "57B BOND STREET"),
    },
)

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "borough": ("borough", "boro", "borough_name"),
    "block": ("block", "tax_block", "block_number"),
    "lot": ("lot", "tax_lot", "lot_number"),
    "bbl": ("bbl", "borough_block_lot", "bin_bbl"),
    "house_number": ("house_number", "house_no", "houseno", "house", "number"),
    "street_name": ("street_name", "streetname", "street", "street_name_1"),
    "job_number": ("job_number", "job_no", "job_filing_number", "filing_number"),
    "filing_date": ("filing_date", "pre_filing_date", "submitted_date", "latest_action_date"),
    "work_type": ("work_type", "job_type", "filing_type"),
    "description": ("description", "job_description", "work_description", "proposed_work"),
    "applicant_business_name": (
        "applicant_business_name",
        "applicant_licensee_business_name",
        "applicant_business",
        "applicant_s_business_name",
    ),
    "applicant_first_name": ("applicant_first_name", "applicant_first"),
    "applicant_last_name": ("applicant_last_name", "applicant_last"),
    "owner_business_name": ("owner_business_name", "owner_s_business_name", "owner_business"),
    "owner_first_name": ("owner_first_name", "owner_first"),
    "owner_last_name": ("owner_last_name", "owner_last"),
}


def _norm_key(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _norm_text(value: Any) -> str:
    text = str(value or "").upper().replace("&", " AND ")
    for before, after in {
        " STREET": " ST",
        " AVENUE": " AVE",
        " BOULEVARD": " BLVD",
        " WEST ": " W ",
        " EAST ": " E ",
    }.items():
        text = text.replace(before, after)
    return " ".join("".join(ch if ch.isalnum() else " " for ch in text).split())


def _get(row: dict[str, Any], field: str) -> Any:
    aliases = {_norm_key(name) for name in FIELD_ALIASES[field]}
    for key, value in row.items():
        if _norm_key(key) in aliases:
            return value
    return None


def _compact(value: Any) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _bbl_from_parts(row: dict[str, Any]) -> str | None:
    raw_bbl = str(_get(row, "bbl") or "").strip()
    if raw_bbl.endswith(".0"):
        raw_bbl = raw_bbl[:-2]
    compact_bbl = "".join(ch for ch in raw_bbl if ch.isdigit())
    if len(compact_bbl) == 10:
        return compact_bbl

    borough = str(_get(row, "borough") or "").strip().upper()
    borough_id = {
        "1": "1",
        "MANHATTAN": "1",
        "MN": "1",
        "2": "2",
        "BRONX": "2",
        "BX": "2",
        "3": "3",
        "BROOKLYN": "3",
        "BK": "3",
        "4": "4",
        "QUEENS": "4",
        "QN": "4",
        "5": "5",
        "STATEN ISLAND": "5",
        "SI": "5",
    }.get(borough)
    block = "".join(ch for ch in str(_get(row, "block") or "") if ch.isdigit())
    lot = "".join(ch for ch in str(_get(row, "lot") or "") if ch.isdigit())
    if borough_id and block and lot:
        return f"{borough_id}{int(block):05d}{int(lot):04d}"
    return None


def _address_text(row: dict[str, Any]) -> str:
    house = str(_get(row, "house_number") or "").strip()
    street = str(_get(row, "street_name") or "").strip()
    return " ".join(part for part in (house, street) if part)


def _party_text(row: dict[str, Any]) -> str:
    parts = [
        _get(row, "applicant_business_name"),
        _get(row, "applicant_first_name"),
        _get(row, "applicant_last_name"),
        _get(row, "owner_business_name"),
        _get(row, "owner_first_name"),
        _get(row, "owner_last_name"),
    ]
    return " ".join(str(part or "") for part in parts)


def _read_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
    raise ValueError(f"Unsupported DOB NOW extract format for {path}. Use CSV or JSON.")


def _build_target(
    *,
    target_id: str,
    address: str,
    bbl: str | None,
    expected_party: str | None,
    expected_manager: str | None,
    manager_lead_id: str | None,
) -> dict[str, Any]:
    aliases = {address, _norm_text(address)}
    if address.upper().startswith("57 BOND"):
        aliases.update({"57B BOND", "57B BOND STREET"})
    return {
        "target_id": target_id,
        "address": address.upper(),
        "bbl": bbl,
        "expected_party": expected_party,
        "expected_manager": expected_manager,
        "manager_lead_id": manager_lead_id,
        "aliases": tuple(sorted(alias for alias in aliases if alias)),
    }


def _target_matches(row: dict[str, Any], target: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    row_bbl = _bbl_from_parts(row)
    if target.get("bbl") and row_bbl == target["bbl"]:
        reasons.append("bbl")
    row_address = _norm_text(_address_text(row))
    alias_matches = [_norm_text(alias) for alias in target.get("aliases", ())]
    if row_address and row_address in alias_matches:
        reasons.append("address_alias")
    expected_party = str(target.get("expected_party") or "").strip()
    if expected_party and _compact(expected_party) in _compact(_party_text(row)):
        reasons.append("expected_party")
    if not target.get("bbl") and not expected_party:
        return bool(reasons), reasons
    if target.get("bbl") and expected_party:
        return ("bbl" in reasons or "address_alias" in reasons) and "expected_party" in reasons, reasons
    return bool(reasons), reasons


def _row_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_number": _get(row, "job_number"),
        "filing_date": _get(row, "filing_date"),
        "work_type": _get(row, "work_type"),
        "description": _get(row, "description"),
        "address": _address_text(row),
        "bbl": _bbl_from_parts(row),
        "applicant_business_name": _get(row, "applicant_business_name"),
        "owner_business_name": _get(row, "owner_business_name"),
    }


def _source_reference(summary: dict[str, Any], row_number: int) -> str:
    job = str(summary.get("job_number") or "").strip()
    return f"DOB NOW Build job {job}" if job else f"DOB NOW Build export row {row_number}"


def _build_clue(row: dict[str, Any], row_number: int, target: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
    summary = _row_summary(row)
    return {
        "target_id": target["target_id"],
        "address": target["address"],
        "bbl": target.get("bbl"),
        "expected_manager": target.get("expected_manager"),
        "manager_lead_id": target.get("manager_lead_id"),
        "expected_party": target.get("expected_party"),
        "source_family": "dob_now_build_job_filing",
        "source_name": "dob_now_build_job_application_filings",
        "source_dataset_id": DOB_NOW_DATASET_ID,
        "source_reference": _source_reference(summary, row_number),
        "source_document_row_number": row_number,
        "matched_reasons": reasons,
        "filing_summary": summary,
        "clue_status": "source_clue_only",
        "can_become_manual_evidence_template": False,
        "source_evidence_intake_candidate_ready": False,
        "requires_primary_source_review": True,
        "approval_required_before_recording": True,
        "safe_action": (
            "Use this DOB NOW filing only as a primary-source clue. Filing/applicant/owner context is "
            "role-ambiguous for property management and cannot support, contradict, verify, or activate "
            "a manages_building claim without another exact role-specific manager source."
        ),
    }


def _bbl_where_clause(bbl: str) -> str:
    compact_bbl = "".join(ch for ch in str(bbl or "") if ch.isdigit())
    clauses = [f"bbl='{compact_bbl}'"] if compact_bbl else []
    if len(compact_bbl) == 10:
        borough_id = compact_bbl[:1]
        block = str(int(compact_bbl[1:6]))
        lot = str(int(compact_bbl[6:]))
        borough_name = {
            "1": "MANHATTAN",
            "2": "BRONX",
            "3": "BROOKLYN",
            "4": "QUEENS",
            "5": "STATEN ISLAND",
        }.get(borough_id)
        if borough_name:
            clauses.append(f"(borough='{borough_name}' AND block='{block}' AND lot='{lot}')")
    return " OR ".join(clauses) or "1=0"


def _party_search_terms(target: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for value in (target.get("expected_party"), target.get("expected_manager")):
        normalized = " ".join(str(value or "").upper().split())
        if not normalized:
            continue
        terms.append(normalized)
        if "MD2" in normalized or "MD SQUARED" in normalized:
            terms.extend(["MD2", "MD SQUARED"])
    deduped: list[str] = []
    for term in terms:
        if term not in deduped:
            deduped.append(term)
    return deduped


def _party_where_clause(target: dict[str, Any]) -> str:
    clauses: list[str] = []
    for term in _party_search_terms(target):
        escaped = term.replace("'", "''")
        clauses.extend([
            f"upper(applicant_business_name) like '%{escaped}%'",
            f"upper(filing_representative_business_name) like '%{escaped}%'",
            f"upper(owner_s_business_name) like '%{escaped}%'",
        ])
    return " OR ".join(clauses) or "1=0"


def _target_party_where_clause(target: dict[str, Any]) -> str:
    return f"({_bbl_where_clause(str(target.get('bbl') or ''))}) AND ({_party_where_clause(target)})"


def registration_query_url_for_bbl(bbl: str) -> str:
    where = _bbl_where_clause(bbl)
    return f"{DOB_NOW_API_URL}?{urlencode({'$limit': 50, '$where': where})}"


def _api_url_for_where(where: str, *, limit: int = 50) -> str:
    return f"{DOB_NOW_API_URL}?{urlencode({'$limit': limit, '$where': where})}"


def build_query_packet(targets: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "dataset_id": DOB_NOW_DATASET_ID,
        "catalog_url": DOB_NOW_CATALOG_URL,
        "api_url": DOB_NOW_API_URL,
        "download_url": DOB_NOW_DOWNLOAD_URL,
        "source_boundary": (
            "DOB NOW filings are source-acquisition clues only and role-ambiguous for management. "
            "They may identify applicants, owners, job filers, or work descriptions, but they do not "
            "prove current property management."
        ),
        "target_query_urls": [
            {
                "target_id": target["target_id"],
                "address": target["address"],
                "bbl": target.get("bbl"),
                "api_url": registration_query_url_for_bbl(str(target["bbl"])) if target.get("bbl") else None,
                "target_party_api_url": (
                    _api_url_for_where(_target_party_where_clause(target))
                    if target.get("bbl") and _party_search_terms(target)
                    else None
                ),
                "party_api_url": (
                    _api_url_for_where(_party_where_clause(target))
                    if _party_search_terms(target)
                    else None
                ),
            }
            for target in targets
        ],
        "post_fetch_local_extract_command": (
            ".\\.venv-x64\\Scripts\\python.exe scripts\\truth_dob_now_clue_audit.py "
            "--file <path-to-w9ak-ipjd.csv-or-json> --target-preset md2-57-bond --indent 2"
        ),
    }


def _fetch_socrata_rows(where: str, *, limit: int = 50) -> list[dict[str, Any]]:
    url = _api_url_for_where(where, limit=limit)
    try:
        with urlopen(url, timeout=30) as response:
            payload = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"DOB NOW live query failed for {url}: {exc}") from exc
    data = json.loads(payload)
    if not isinstance(data, list):
        raise RuntimeError(f"DOB NOW live query returned non-list payload for {url}.")
    return [item for item in data if isinstance(item, dict)]


def _sample_rows(rows: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    return [_row_summary(row) for row in rows[:limit]]


def audit_live_dob_now_targets(
    targets: list[dict[str, Any]],
    *,
    fetch_rows: Any = _fetch_socrata_rows,
    limit: int = 50,
) -> dict[str, Any]:
    target_query_results: list[dict[str, Any]] = []
    target_party_rows: list[dict[str, Any]] = []
    for target in targets:
        property_where = _bbl_where_clause(str(target.get("bbl") or ""))
        party_where = _party_where_clause(target)
        target_party_where = _target_party_where_clause(target)
        property_rows = fetch_rows(property_where, limit=limit) if target.get("bbl") else []
        party_rows = fetch_rows(party_where, limit=limit) if _party_search_terms(target) else []
        exact_party_rows = fetch_rows(target_party_where, limit=limit) if target.get("bbl") else []
        target_party_rows.extend(exact_party_rows)
        target_query_results.append({
            "target_id": target["target_id"],
            "address": target["address"],
            "bbl": target.get("bbl"),
            "expected_party": target.get("expected_party"),
            "expected_manager": target.get("expected_manager"),
            "property_row_count": len(property_rows),
            "party_row_count": len(party_rows),
            "target_party_match_count": len(exact_party_rows),
            "property_query_url": _api_url_for_where(property_where, limit=limit),
            "party_query_url": _api_url_for_where(party_where, limit=limit),
            "target_party_query_url": _api_url_for_where(target_party_where, limit=limit),
            "sample_property_rows": _sample_rows(property_rows),
            "sample_party_only_rows": _sample_rows(party_rows),
            "sample_target_party_rows": _sample_rows(exact_party_rows),
            "safe_action": (
                "DOB NOW rows are source-acquisition context only. A target-party match can guide "
                "primary-source review, but DOB applicant/owner/filing context is role-ambiguous "
                "and still cannot prove current property management by itself."
            ),
        })

    report = audit_dob_now_rows(target_party_rows, targets=targets)
    report.update({
        "source_access_mode": "live_official_query",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_query_results": target_query_results,
        "query_packet": build_query_packet(targets),
        "safe_action": (
            "Source acquisition only. Live official DOB NOW queries can prove whether a DOB clue "
            "intersects the exact target property, but DOB filings remain role-ambiguous and cannot "
            "be recorded as manager proof without a separate exact role-specific manager source."
        ),
    })
    return report


def audit_dob_now_rows(rows: list[dict[str, Any]], *, targets: list[dict[str, Any]]) -> dict[str, Any]:
    clues: list[dict[str, Any]] = []
    matched_target_ids: set[str] = set()
    for index, row in enumerate(rows, start=2):
        for target in targets:
            matched, reasons = _target_matches(row, target)
            if not matched:
                continue
            matched_target_ids.add(str(target["target_id"]))
            clues.append(_build_clue(row, index, target, reasons))

    unmatched = [
        {
            "target_id": target["target_id"],
            "address": target["address"],
            "bbl": target.get("bbl"),
            "expected_party": target.get("expected_party"),
        }
        for target in targets
        if str(target["target_id"]) not in matched_target_ids
    ]
    return {
        "run_type": "truth_dob_now_clue_audit",
        "dry_run": True,
        "mutations_planned": 0,
        "source_access_mode": "local_extract",
        "source_dataset": {
            "dataset_id": DOB_NOW_DATASET_ID,
            "catalog_url": DOB_NOW_CATALOG_URL,
            "download_url": DOB_NOW_DOWNLOAD_URL,
        },
        "target_count": len(targets),
        "row_count": len(rows),
        "source_acquisition_clue_count": len(clues),
        "source_acquisition_clues": clues,
        "source_evidence_intake_candidates": [],
        "recording_ready_count": 0,
        "allowed_execute": False,
        "unmatched_targets": unmatched,
        "query_packet": build_query_packet(targets),
        "safe_action": (
            "Source acquisition only. Review matched DOB NOW rows as primary-source clues, then acquire "
            "role-specific manager evidence before source-evidence intake or recording approval."
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", help="Local official DOB NOW CSV/JSON export. The file is read only.")
    parser.add_argument("--query-packet-only", action="store_true")
    parser.add_argument("--live-query", action="store_true", help="Run read-only live NYC Open Data API queries.")
    parser.add_argument("--live-limit", type=int, default=50)
    parser.add_argument("--target-preset", choices=("md2-57-bond",), default="md2-57-bond")
    parser.add_argument("--target-id", default="custom-dob-now-target")
    parser.add_argument("--address")
    parser.add_argument("--bbl")
    parser.add_argument("--expected-party")
    parser.add_argument("--expected-manager")
    parser.add_argument("--manager-lead-id")
    parser.add_argument("--preview-source-evidence-intake", action="store_true")
    parser.add_argument("--indent", type=int, default=2)
    return parser.parse_args()


def _targets_from_args(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.address or args.bbl or args.expected_party:
        return [
            _build_target(
                target_id=args.target_id,
                address=args.address or "",
                bbl=args.bbl,
                expected_party=args.expected_party,
                expected_manager=args.expected_manager,
                manager_lead_id=args.manager_lead_id,
            )
        ]
    return [dict(target) for target in DEFAULT_TARGETS]


def main() -> int:
    args = parse_args()
    targets = _targets_from_args(args)
    if args.query_packet_only:
        result = {
            "run_type": "truth_dob_now_clue_audit_query_packet",
            "dry_run": True,
            "mutations_planned": 0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "query_packet": build_query_packet(targets),
            "source_evidence_intake_candidates": [],
            "source_acquisition_clues": [],
            "recording_ready_count": 0,
            "allowed_execute": False,
        }
    elif args.live_query:
        result = audit_live_dob_now_targets(targets, limit=args.live_limit)
        if args.preview_source_evidence_intake:
            result["source_evidence_intake_preview"] = build_source_acquisition_clue_only_preview(
                result["source_acquisition_clues"],
                source_mode="dob_now_live_query",
            )
    else:
        if not args.file:
            raise SystemExit("--file is required unless --query-packet-only or --live-query is set.")
        result = audit_dob_now_rows(_read_rows(Path(args.file)), targets=targets)
        if args.preview_source_evidence_intake:
            result["source_evidence_intake_preview"] = build_source_acquisition_clue_only_preview(
                result["source_acquisition_clues"],
                source_mode="dob_now_clue_audit",
            )
    print(json.dumps(result, indent=args.indent, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
