"""
Portfolio-signature deduplication and condo/co-op rollup.

TWO DEFECTS THIS ADDRESSES
--------------------------
1. DUPLICATE LEADS. Distinct `lead_id` rows can carry byte-identical portfolios.
   Measured on the top 60 leads by portfolio size (July 2026): 10 of 60 were
   exact duplicates -- Douglas Elliman (307 lots), Andrews Organization (234),
   C&C Apartment Management (212), Guardian (195), Nieuw Amsterdam (193),
   Choice NY (190), GPG (148), Bronstein (139), PHH Mortgage (137), Rose (128).
   Not fuzzy name variants -- identical BBL sets. Name-based matching misses
   them because the corporation names genuinely differ in HPD.

   Hashing the BBL set catches every one, exactly, with no threshold to tune.

2. CONDO/CO-OP FRAGMENTATION. DOF assigns each condo and co-op UNIT its own tax
   lot. A 200-unit condo can appear as 200 BBLs. Any portfolio count built on
   raw BBLs therefore overstates buildings for condo/co-op-heavy managers and
   understates them for walk-up rental managers -- which silently biases every
   portfolio-size filter and score in the app.

   DOF publishes the parent development key: `coop_num` and `condo_number`.
   Rolling up on those collapses unit lots into one building.

Both functions are PURE -- they compute and return findings. Nothing here
mutates the database. Merging leads is destructive and belongs behind the same
dry-run/confirm gate as the truth-layer materialisation.
"""
import hashlib
import logging
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Sequence

logger = logging.getLogger(__name__)


def portfolio_signature(bbls: Iterable[str]) -> Optional[str]:
    """
    Stable hash of a set of BBLs.

    Order-independent and duplicate-independent, so two leads holding the same
    buildings hash identically regardless of ingest order. Returns None for an
    empty portfolio -- zero-link leads must never collide with each other.
    """
    unique = {str(b).strip() for b in (bbls or []) if b and str(b).strip()}
    if not unique:
        return None
    joined = ",".join(sorted(unique))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def find_duplicate_leads(lead_bbls: Dict[str, Sequence[str]]) -> List[dict]:
    """
    Group leads by portfolio signature.

    `lead_bbls` maps lead_id -> that lead's current BBLs.

    Returns one entry per collision group, largest portfolio first. Every group
    is a set of lead rows that manage exactly the same buildings.
    """
    by_sig: Dict[str, List[str]] = defaultdict(list)
    sizes: Dict[str, int] = {}

    for lead_id, bbls in (lead_bbls or {}).items():
        sig = portfolio_signature(bbls)
        if sig is None:
            continue
        by_sig[sig].append(lead_id)
        sizes[sig] = len({str(b).strip() for b in bbls if b})

    groups = [
        {
            "signature": sig,
            "lead_ids": sorted(ids),
            "portfolio_size": sizes[sig],
            "redundant_rows": len(ids) - 1,
        }
        for sig, ids in by_sig.items()
        if len(ids) > 1
    ]
    groups.sort(key=lambda g: (-g["portfolio_size"], g["signature"]))

    total_redundant = sum(g["redundant_rows"] for g in groups)
    logger.info(
        "portfolio dedup: %d collision groups covering %d redundant lead rows "
        "across %d leads examined",
        len(groups), total_redundant, len(lead_bbls or {}),
    )
    return groups


def choose_survivor(lead_ids: Sequence[str], leads_by_id: Optional[dict] = None) -> str:
    """
    Pick which lead row should survive a merge.

    Prefers the most enriched row -- contact detail first, then pipeline
    progress, then lexical order so the choice is deterministic and a rerun
    produces the same answer. Falls back to the first sorted id when no lead
    metadata is supplied.
    """
    ids = sorted(lead_ids)
    if not leads_by_id:
        return ids[0]

    def rank(lead_id):
        lead = leads_by_id.get(lead_id)
        if lead is None:
            return (0, 0, lead_id)
        def g(name):
            return lead.get(name) if isinstance(lead, dict) else getattr(lead, name, None)
        contact = sum(1 for f in ("phone", "email", "company_website") if g(f))
        staged = 0 if (g("pipeline_stage") or "research") in ("research", "new") else 1
        return (contact, staged, lead_id)

    return max(ids, key=rank)


def rollup_key(record) -> Optional[str]:
    """
    Parent-development key for a condo/co-op unit lot, else None.

    Accepts an `AssessmentRecord` from `ingest.dof_client` or a raw DOF dict.
    """
    def g(*names):
        for n in names:
            v = record.get(n) if isinstance(record, dict) else getattr(record, n, None)
            if v not in (None, "", "0"):
                return str(v).strip().lstrip("0") or None
        return None

    coop = g("coop_number", "coop_num")
    if coop:
        return f"coop:{coop}"
    condo = g("condo_number")
    if condo:
        return f"condo:{condo}"
    return None


def collapse_portfolio(assessment_records) -> dict:
    """
    Collapse condo/co-op unit lots into parent developments.

    Returns both the raw lot count and the true building count, plus the
    per-development detail. The gap between the two IS the fragmentation --
    report it rather than silently replacing one number with the other, because
    downstream filters and saved Smart Lists were built against the raw count.
    """
    developments: Dict[str, List[str]] = defaultdict(list)
    standalone: List[str] = []

    for rec in assessment_records or []:
        bbl = rec.get("bbl") if isinstance(rec, dict) else getattr(rec, "bbl", None)
        if not bbl:
            continue
        key = rollup_key(rec)
        if key:
            developments[key].append(bbl)
        else:
            standalone.append(bbl)

    raw_lots = sum(len(v) for v in developments.values()) + len(standalone)
    true_buildings = len(developments) + len(standalone)

    return {
        "raw_lot_count": raw_lots,
        "true_building_count": true_buildings,
        "fragmentation_ratio": round(raw_lots / true_buildings, 2) if true_buildings else 1.0,
        "developments": {k: sorted(v) for k, v in developments.items()},
        "standalone_bbls": sorted(standalone),
        "multi_lot_developments": sum(1 for v in developments.values() if len(v) > 1),
    }


def merge_plan(groups: List[dict], leads_by_id: Optional[dict] = None) -> List[dict]:
    """
    Build a dry-run merge plan from duplicate groups.

    Returns the proposed survivor and the rows that would be retired for each
    collision. Describes intent only -- apply it behind an explicit confirm gate.
    """
    plan = []
    for g in groups:
        survivor = choose_survivor(g["lead_ids"], leads_by_id)
        plan.append({
            "signature": g["signature"],
            "portfolio_size": g["portfolio_size"],
            "survivor_lead_id": survivor,
            "retire_lead_ids": [i for i in g["lead_ids"] if i != survivor],
        })
    return plan


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    demo = {
        "aaa": ["1000160001", "1000160002", "1000160003"],
        "bbb": ["1000160003", "1000160001", "1000160002"],   # same set, different order
        "ccc": ["3012340001"],
        "ddd": [],
    }
    groups = find_duplicate_leads(demo)
    print("duplicate groups:", groups)
    print("merge plan:", merge_plan(groups, {
        "aaa": {"phone": None, "email": None, "pipeline_stage": "research"},
        "bbb": {"phone": "212-555-0100", "email": "x@y.com", "pipeline_stage": "meeting"},
    }))

    recs = (
        [{"bbl": f"100795{i:04d}", "coop_number": "103367"} for i in range(200)]
        + [{"bbl": "3012340001", "coop_number": None, "condo_number": None}]
    )
    c = collapse_portfolio(recs)
    print(f"\nraw lots={c['raw_lot_count']} -> true buildings={c['true_building_count']} "
          f"(fragmentation {c['fragmentation_ratio']}x)")
