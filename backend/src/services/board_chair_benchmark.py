"""Public, address-resolved board-chair examples for regression checks.

Each case confirms a named board president or chairman at the source date. Old
articles remain useful identity evidence while their currentness decays.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from typing import Any

BOARD_CHAIR_GOLDEN_CASES: tuple[dict[str, str], ...] = (
    {
        "bbl": "3010680037",
        "address": "9 Prospect Park West, Brooklyn",
        "expected_name": "Louise Hainline",
        "expected_title": "Chairman",
        "source_date": "2026-08-28",
        "source_name": "NY Department of State Active Corporations",
        "source_url": "https://data.ny.gov/resource/n9v6-gdp6.json?dos_id=454503",
        "evidence_summary": "The exact Park West Tenants Corp. record names Louise Hainline as chairman.",
    },
    {
        "bbl": "1013310014",
        "address": "227 East 57th Street, Manhattan",
        "expected_name": "Alex Moir",
        "expected_title": "Board President",
        "source_date": "2025-04-01",
        "source_name": "Habitat Magazine",
        "source_url": "https://www.habitatmag.com/Archive2/431-April-2025/Co-op-Board-President-Tackles-Gas-Leaks-Facade-Repairs-and-Energy-Upgrades",
        "evidence_summary": "A profile identifies Alex Moir as the co-op board president at this address.",
    },
    {
        "bbl": "1015450130",
        "address": "340 East 83rd Street, Manhattan",
        "expected_name": "David Hales",
        "expected_title": "Board President",
        "source_date": "2024-06-01",
        "source_name": "Habitat Magazine",
        "source_url": "https://www.habitatmag.com/Archive2/422-June-2024/David-Hales-Finds-Sanctuary-in-Yorkville-Co-Op-Amidst-Challenges",
        "evidence_summary": "A building profile identifies David Hales as board president.",
    },
    {
        "bbl": "1013230036",
        "address": "230 East 50th Street, Manhattan",
        "expected_name": "Russell A. Raman",
        "expected_title": "Board President",
        "source_date": "2022-05-01",
        "source_name": "Habitat Magazine",
        "source_url": "https://www.habitatmag.com/Archive2/399-May-2022/Leading-the-Way-Board-President-Russell-Raman-230-E.-50th-St",
        "evidence_summary": "A profile identifies Russell A. Raman as board president at the building.",
    },
    {
        "bbl": "1011470056",
        "address": "166 West 76th Street, Manhattan",
        "expected_name": "Debra McEneaney",
        "expected_title": "Board President",
        "source_date": "2021-05-01",
        "source_name": "Habitat Magazine",
        "source_url": "https://www.habitatmag.com/Publication-Content/2021/2021-May/Feature-Articles/Q-A-With-a-Ford-Model-Turned-Co-op-Board-President",
        "evidence_summary": "A Q&A identifies Debra McEneaney as the building's co-op board president.",
    },
    {
        "bbl": "1014250029",
        "address": "230 East 71st Street, Manhattan",
        "expected_name": "Robert P.J. Booher",
        "expected_title": "Board President",
        "source_date": "2014-07-01",
        "source_name": "Habitat Magazine",
        "source_url": "https://www.habitatmag.com/Archive2/313-July-August-2014/Secrets-of-Powerful-Partnerships",
        "evidence_summary": "A management profile identifies Robert P.J. Booher as board president.",
    },
    {
        "bbl": "1014697501",
        "address": "401 East 74th Street, Manhattan",
        "expected_name": "Stephen Doherty",
        "expected_title": "Board President",
        "source_date": "2019-05-01",
        "source_name": "Habitat Magazine",
        "source_url": "https://www.habitatmag.com/Archive2/366-May-2019/The-Man-Who-Took-the-Fix-Out-of-Fixed-Costs",
        "evidence_summary": "A building profile identifies Stephen Doherty as board president.",
    },
    {
        "bbl": "1014600001",
        "address": "401 East 65th Street, Manhattan",
        "expected_name": "Gerry Maughan",
        "expected_title": "Board President",
        "source_date": "2018-07-01",
        "source_name": "Habitat Magazine",
        "source_url": "https://www.habitatmag.com/layout/set/print/layout/set/print/Publication-Content/Bricks-Bucks/Fairy-Tale",
        "evidence_summary": "A building story identifies Gerry Maughan as the board president.",
    },
    {
        "bbl": "1008960032",
        "address": "230 East 15th Street, Manhattan",
        "expected_name": "James Ramadei",
        "expected_title": "Board President",
        "source_date": "2017-02-01",
        "source_name": "Habitat Magazine",
        "source_url": "https://www.habitatmag.com/Archive2/341-February-2017/History-Has-Its-Eyes-on-You",
        "evidence_summary": "A building history profile identifies James Ramadei as board president.",
    },
    {
        "bbl": "1008820052",
        "address": "150 East 27th Street, Manhattan",
        "expected_name": "Josette Cerasuola",
        "expected_title": "Board President",
        "source_date": "2013-12-01",
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


def evaluate_board_chair_case(
    case: dict[str, str],
    cache_result: Any,
    cached_at: datetime | None,
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """Compare a public historical assertion with the current DOS chair cache."""
    today = today or datetime.now(timezone.utc).date()
    source_date = date.fromisoformat(case["source_date"])
    evidence_age_days = max(0, (today - source_date).days)
    payload = _payload(cache_result)
    observed_name = str(payload.get("ceo_name") or "").strip() or None
    cache_age_days = None
    if cached_at:
        normalized_cached_at = cached_at if cached_at.tzinfo else cached_at.replace(tzinfo=timezone.utc)
        cache_age_days = max(0, (datetime.now(timezone.utc) - normalized_cached_at).days)
    name_matches = bool(
        observed_name
        and _canonical_person_name(observed_name) == _canonical_person_name(case["expected_name"])
    )

    if name_matches and cache_age_days is not None and cache_age_days <= 30:
        status = "current_match"
    elif name_matches:
        status = "stale_match"
    elif observed_name:
        status = "different_current_name"
    else:
        status = "missing_current_evidence"
    return {
        **case,
        "evidence_age_days": evidence_age_days,
        "evidence_currentness": "current" if evidence_age_days <= 365 else "historical",
        "observed_name": observed_name,
        "cache_age_days": cache_age_days,
        "status": status,
        "identity_match": name_matches,
    }
