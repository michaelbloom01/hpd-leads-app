"""Source-dated Board Head evidence and registry-identity regression cases.

Nine firsthand sources support historical board roles. The 9 Prospect case
supports a DOS candidate only. Registry freshness, name matching, entity identity,
and exact board-role evidence are evaluated independently.
"""

from __future__ import annotations

import json
import re
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from typing import Any

BOARD_CHAIR_GOLDEN_CASES: tuple[dict[str, Any], ...] = (
    {
        "bbl": "3010680037",
        "address": "9 Prospect Park West, Brooklyn",
        "expected_name": "Louise Hainline",
        "expected_title": "Board Head candidate (exact title unverified)",
        "dos_id": "454503",
        "dos_entity_name": "PARK WEST TENANTS CORP.",
        "evidence_kind": "registry_candidate",
        "expected_role_is_explicit": False,
        "source_date": None,
        "source_date_precision": "unknown",
        "source_observed_on": "2026-08-31",
        "source_published_at": "2026-08-31T11:38:57Z",
        "source_field": "chairman_name",
        "source_field_display_name": "CEO Name",
        "source_name": "NY Department of State Active Corporations",
        "source_url": "https://data.ny.gov/resource/n9v6-gdp6.json?dos_id=454503",
        "evidence_summary": "The exact Park West Tenants Corp. DOS record names Louise Hainline in the CEO Name field. Her exact current board title is unverified. Dataset publication and observation dates do not establish role tenure.",
    },
    {
        "bbl": "1013310014",
        "address": "227 East 57th Street, Manhattan",
        "expected_name": "Alex Moir",
        "expected_title": "Board President",
        "dos_id": "866804",
        "dos_entity_name": "227 EAST 57TH STREET, INC.",
        "evidence_kind": "firsthand_role_statement",
        "expected_role_is_explicit": True,
        "source_date": "2025-04",
        "source_date_precision": "month",
        "source_name": "Habitat Magazine",
        "source_url": "https://www.habitatmag.com/Archive2/431-April-2025/Co-op-Board-President-Tackles-Gas-Leaks-Facade-Repairs-and-Energy-Upgrades",
        "evidence_summary": "A profile identifies Alex Moir as the co-op board president at this address.",
    },
    {
        "bbl": "1015450130",
        "address": "340 East 83rd Street, Manhattan",
        "expected_name": "David Hales",
        "expected_title": "Board President",
        "source_role_label": "President",
        "dos_id": "974822",
        "dos_entity_name": "340 E. 83RD ST. APARTMENT CORP.",
        "evidence_kind": "firsthand_role_statement",
        "expected_role_is_explicit": True,
        "source_date": "2024-06",
        "source_date_precision": "month",
        "identity_notes": "The DOS entity uses E. where the HPD owner name uses EAST. Retain the reviewed entity ID.",
        "source_name": "Habitat Magazine",
        "source_url": "https://www.habitatmag.com/Archive2/422-June-2024/David-Hales-Finds-Sanctuary-in-Yorkville-Co-Op-Amidst-Challenges",
        "evidence_summary": "A building profile identifies David Hales as board president.",
    },
    {
        "bbl": "1013230036",
        "address": "230 East 50th Street, Manhattan",
        "expected_name": "Russell A. Raman",
        "expected_title": "Board President",
        "dos_id": "123062",
        "dos_entity_name": "230 TENANTS CORPORATION",
        "evidence_kind": "firsthand_role_statement",
        "expected_role_is_explicit": True,
        "source_date": "2022-05",
        "source_date_precision": "month",
        "source_name": "Habitat Magazine",
        "source_url": "https://www.habitatmag.com/Archive2/399-May-2022/Leading-the-Way-Board-President-Russell-Raman-230-E.-50th-St",
        "evidence_summary": "A profile identifies Russell A. Raman as board president at the building.",
    },
    {
        "bbl": "1011470056",
        "address": "166 West 76th Street, Manhattan",
        "expected_name": "Debra McEneaney",
        "expected_title": "Board President",
        "dos_id": "611390",
        "dos_entity_name": "166 WEST 76TH APARTMENT CORP.",
        "evidence_kind": "firsthand_role_statement",
        "expected_role_is_explicit": True,
        "source_date": "2021-05-17",
        "source_date_precision": "day",
        "source_name": "Habitat Magazine",
        "source_url": "https://www.habitatmag.com/Publication-Content/2021/2021-May/Feature-Articles/Q-A-With-a-Ford-Model-Turned-Co-op-Board-President",
        "evidence_summary": "A Q&A identifies Debra McEneaney as the building's co-op board president.",
    },
    {
        "bbl": "1014250029",
        "address": "230 East 71st Street, Manhattan",
        "expected_name": "Robert P.J. Booher",
        "expected_title": "Board President",
        "dos_id": "538921",
        "dos_entity_name": "230 OWNERS CORP.",
        "evidence_kind": "firsthand_role_statement",
        "expected_role_is_explicit": True,
        "source_date": "2014-07/2014-08",
        "source_date_precision": "issue_range",
        "identity_notes": "The article uses 230 East 71st Street; the targeted HPD registration uses 238 East 71st Street. The address alias requires review.",
        "source_name": "Habitat Magazine",
        "source_url": "https://www.habitatmag.com/Archive2/313-July-August-2014/Secrets-of-Powerful-Partnerships",
        "evidence_summary": "A management profile identifies Robert P.J. Booher as board president.",
    },
    {
        "bbl": "1014697501",
        "address": "401 East 74th Street, Manhattan",
        "expected_name": "Stephen Doherty",
        "expected_title": "Board President",
        "dos_id": "1088921",
        "dos_entity_name": "401 E. 74 OWNERS CORP.",
        "evidence_kind": "firsthand_role_statement",
        "expected_role_is_explicit": True,
        "source_date": "2021-02-26",
        "source_date_precision": "day",
        "source_name": "Habitat Magazine",
        "source_url": "https://www.habitatmag.com/Publication-Content/Green-Ideas/2021/2021-February/Boards-Should-Focus-on-Building-Emissions-Not-Letter-Grades",
        "evidence_summary": "The February 26, 2021 article interviews Stephen Doherty as the Amherst board president. A fresh registry name match leaves his current tenure unverified.",
        "role_conflicts": [{
            "kind": "role_end_lead",
            "source_url": "https://www.linkedin.com/in/stephenpdoherty",
            "source_observed_on": "2026-08-31",
            "source_date": None,
            "role_start_month": "2012-06",
            "role_end_month": "2021-02",
            "confidence": "medium",
            "requires_direct_capture": True,
            "summary": "The search-indexed self-profile dates this presidency through February 2021. Direct retrieval returned HTTP 999; verify the profile before treating the end date as fully confirmed.",
        }],
    },
    {
        "bbl": "1014600001",
        "address": "401 East 65th Street, Manhattan",
        "expected_name": "Gerry Maughan",
        "expected_title": "Board President",
        "dos_id": "614822",
        "dos_entity_name": "401/65 OWNERS CORP.",
        "evidence_kind": "firsthand_role_statement",
        "expected_role_is_explicit": True,
        "source_date": "2019-06-17",
        "source_date_precision": "day",
        "identity_notes": "The article uses 401 East 65th Street; HPD uses the corner address 1206 First Avenue. The address alias requires review.",
        "source_name": "Habitat Magazine",
        "source_url": "https://www.habitatmag.com/Publication-Content/Board-Operations/2019/2019-June/Gerry-Maughan",
        "evidence_summary": "The June 17, 2019 interview identifies Gerry Maughan as board president at 401 East 65th Street.",
        "additional_role_evidence": [{
            "person": "Michael J Grand",
            "exact_roles": ["Director", "Coop Board Member", "officer"],
            "source_url": "https://reports.adviserinfo.sec.gov/reports/individual/individual_5044980.pdf",
            "source_date": "2025-12-17",
            "source_date_basis": "report_last_updated",
            "source_observed_on": "2026-08-31",
            "chair_or_president_proof": False,
            "summary": "The self-reported regulatory record identifies these board roles at 401/65 Owners Corp. Its report-update date is not a field-level tenure certification, and none of the roles establishes the presidency.",
        }],
    },
    {
        "bbl": "1008960032",
        "address": "230 East 15th Street, Manhattan",
        "expected_name": "James Ramadei",
        "expected_title": "Board President",
        "dos_id": "672936",
        "dos_entity_name": "RUTHERFORD TENANTS CORP.",
        "evidence_kind": "firsthand_role_statement",
        "expected_role_is_explicit": True,
        "source_date": "2017-02",
        "source_date_precision": "month",
        "possible_person_aliases": ["Jim Ramadei"],
        "identity_notes": "HPD includes a leading THE in the entity name. Jim/James is a possible person alias requiring review, not an automatic equivalence.",
        "source_name": "Habitat Magazine",
        "source_url": "https://www.habitatmag.com/Archive2/341-February-2017/History-Has-Its-Eyes-on-You",
        "evidence_summary": "A building history profile identifies James Ramadei as board president.",
    },
    {
        "bbl": "1008820052",
        "address": "150 East 27th Street, Manhattan",
        "expected_name": "Josette Cerasuola",
        "expected_title": "Board President",
        "dos_id": "858280",
        "dos_entity_name": "GOTHAM HOUSE OWNER'S CORP.",
        "evidence_kind": "firsthand_role_statement",
        "expected_role_is_explicit": True,
        "source_date": "2013-12-12",
        "source_date_precision": "day",
        "source_name": "Habitat Magazine",
        "source_url": "https://www.habitatmag.com/Publication-Content/Board-Operations/2013/2013-December/Q-A-Co-op-Board-President-Josette-Cerasuol",
        "evidence_summary": "A Q&A identifies Josette Cerasuola as the co-op board president.",
    },
)


def _canonical_person_name(value: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9]+", " ", (value or "").upper())).strip()


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _date_bounds(value: Any) -> tuple[date, date] | None:
    """Return explicit bounds without inventing publication-day precision."""
    if not isinstance(value, str):
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}", value):
            year, month = (int(part) for part in value.split("-"))
            return date(year, month, 1), date(year, month, monthrange(year, month)[1])
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            exact = date.fromisoformat(value)
            return exact, exact
        if value.count("/") == 1:
            start, end = (_date_bounds(part) for part in value.split("/"))
            if start and end and start[0] <= end[1]:
                return start[0], end[1]
    except (ValueError, TypeError):
        return None
    return None


def evaluate_board_chair_case(
    case: dict[str, Any],
    cache_result: Any,
    cached_at: datetime | None,
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """Compare registry identity while separately qualifying exact-role evidence.

    ``status`` retains legacy keys for existing summary consumers. A
    ``current_match`` requires recent explicit role evidence, a fresh registry
    name match, and the expected DOS entity ID. ``benchmark_status`` carries the
    more precise meaning.
    The legacy ``identity_match`` remains a person-name comparison only.
    """
    today = today or datetime.now(timezone.utc).date()
    bounds = _date_bounds(case.get("source_date"))
    explicit_role = (
        case.get("evidence_kind") == "firsthand_role_statement"
        and case.get("expected_role_is_explicit") is True
    )
    evidence_date_status = "unknown"
    if bounds:
        cutoff = today - timedelta(days=365)
        if bounds[0] > today:
            evidence_date_status = "future"
        elif bounds[1] < cutoff:
            evidence_date_status = "historical"
        elif bounds[0] >= cutoff and bounds[1] <= today:
            evidence_date_status = "recent"
        else:
            evidence_date_status = "uncertain_range"
    evidence_currentness = (
        "current" if explicit_role and evidence_date_status == "recent"
        else "historical" if explicit_role and evidence_date_status == "historical"
        else "unverified"
    )
    role_conflicts = case.get("role_conflicts") or []
    current_role_supported = evidence_currentness == "current" and not role_conflicts
    payload = _payload(cache_result)
    raw_name = payload.get("ceo_name")
    observed_name = (raw_name.strip() or None) if isinstance(raw_name, str) else None
    cache_age_days = None
    registry_freshness = "unknown"
    if cached_at:
        normalized_cached_at = cached_at if cached_at.tzinfo else cached_at.replace(tzinfo=timezone.utc)
        age = (today - normalized_cached_at.astimezone(timezone.utc).date()).days
        if age < 0:
            registry_freshness = "future_timestamp"
        else:
            cache_age_days = age
            registry_freshness = "fresh" if age <= 30 else "stale"
    name_matches = bool(
        observed_name
        and _canonical_person_name(observed_name) == _canonical_person_name(case["expected_name"])
    )
    possible_alias = bool(
        observed_name and not name_matches
        and _canonical_person_name(observed_name) in {
            _canonical_person_name(alias) for alias in case.get("possible_person_aliases", [])
        }
    )
    observed_dos_id = str(payload.get("dos_id") or "").strip() or None
    entity_identity_match = (
        observed_dos_id == case.get("dos_id") if observed_dos_id else None
    )
    if payload.get("entity_match_status") == "ambiguous":
        entity_identity_match = False

    if entity_identity_match is False:
        benchmark_status = "entity_identity_conflict"
    elif not observed_name:
        benchmark_status = "missing_registry_candidate"
    elif possible_alias:
        benchmark_status = "possible_person_alias"
    elif not name_matches:
        benchmark_status = "different_registry_name"
    elif not explicit_role:
        benchmark_status = "registry_candidate_only"
    elif role_conflicts:
        benchmark_status = "role_conflict_requires_review"
    elif current_role_supported:
        benchmark_status = (
            "registry_identity_unverified" if entity_identity_match is not True
            else "recent_role_and_registry_match" if registry_freshness == "fresh"
            else "recent_role_registry_refresh_needed"
        )
    elif evidence_currentness == "historical":
        benchmark_status = "historical_role_match"
    else:
        benchmark_status = "role_date_unverified"

    if benchmark_status == "recent_role_and_registry_match":
        status = "current_match"
    elif benchmark_status == "registry_identity_unverified":
        status = "missing_current_evidence"
    elif name_matches and explicit_role and entity_identity_match is not False:
        status = "stale_match"
    elif observed_name and not name_matches:
        status = "different_current_name"
    else:
        status = "missing_current_evidence"
    return {
        **case,
        "evidence_age_days": (today - bounds[0]).days if bounds and bounds[0] == bounds[1] else None,
        "evidence_age_days_min": (today - bounds[1]).days if bounds else None,
        "evidence_age_days_max": (today - bounds[0]).days if bounds else None,
        "source_date_earliest": bounds[0].isoformat() if bounds else None,
        "source_date_latest": bounds[1].isoformat() if bounds else None,
        "evidence_date_status": evidence_date_status,
        "evidence_currentness": evidence_currentness,
        "exact_board_role_supported": explicit_role,
        "current_board_role_supported": current_role_supported,
        "current_title_status": "supported_by_recent_source" if current_role_supported else "unverified",
        "has_unresolved_role_conflict": bool(role_conflicts),
        "observed_name": observed_name,
        "observed_dos_id": observed_dos_id,
        "entity_identity_match": entity_identity_match,
        "cache_age_days": cache_age_days,
        "registry_freshness": registry_freshness,
        "registry_name_match_status": (
            "exact" if name_matches else "possible_alias" if possible_alias
            else "different" if observed_name else "missing"
        ),
        "status": status,
        "benchmark_status": benchmark_status,
        "identity_match": name_matches,
        "identity_match_basis": "normalized_person_name_only",
    }
