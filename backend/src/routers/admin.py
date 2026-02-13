"""Admin routes — health checks, alerts, backups, building search."""
import time
import logging
from typing import Optional

from fastapi import APIRouter, Query, HTTPException

from src.storage.database import get_database
from src.services.cache_manager import get_cache
from src.services.lead_converter import row_to_lead

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["admin"])


@router.get("/health")
async def health_check():
    """Health check endpoint for monitoring."""
    cache = get_cache()
    if not cache.startup_complete:
        return {"status": "starting", "message": "Backend is loading data. This usually takes 1-2 minutes."}

    db = get_database()
    lead_count = db.get_leads_count()
    with cache.leads_lock:
        cache_size = len(cache.leads)
        unenriched_count = sum(1 for l in cache.leads if l.portfolio_size >= 10 and l.enrichment_status in ("none", None, ""))

    return {
        "status": "ok", "leads_in_cache": cache_size, "leads_in_db": lead_count,
        "enrichment_running": cache.enrichment.get("running", False),
        "unenriched_high_value": unenriched_count,
        "last_refresh": cache.last_refresh.isoformat() if cache.last_refresh else None,
    }


@router.get("/health/detailed")
async def health_detailed():
    """Comprehensive health and diagnostics endpoint."""
    start = time.time()
    cache = get_cache()
    db = get_database()
    lead_count = db.get_leads_count()
    enrichment_stats = db.get_enrichment_stats()
    db_size_bytes = 0
    try:
        db_size_bytes = db.db_path.stat().st_size if db.db_path.exists() else 0
    except Exception:
        pass
    with cache.leads_lock:
        cache_size = len(cache.leads)
    active_job = db.get_active_enrichment_job()
    follow_ups = db.get_follow_ups_due()
    alerts = db.get_change_alerts(limit=10)

    return {
        "status": "healthy", "uptime_check_ms": round((time.time() - start) * 1000, 1),
        "database": {"path": str(db.db_path), "size_mb": round(db_size_bytes / (1024 * 1024), 2), "total_leads": lead_count},
        "cache": {"leads_in_cache": cache_size},
        "enrichment": {
            "running": cache.enrichment.get("running", False),
            "by_status": enrichment_stats.get("by_status", {}),
            "with_phone": enrichment_stats.get("with_phone", 0),
            "with_email": enrichment_stats.get("with_email", 0),
            "with_website": enrichment_stats.get("with_website", 0),
            "active_job": {
                "id": active_job["id"], "status": active_job["status"],
                "processed": active_job.get("processed", 0), "total": active_job.get("total_leads", 0),
                "remaining": len(active_job.get("lead_ids_remaining", [])),
            } if active_job else None,
        },
        "pipeline": {"follow_ups_due": len(follow_ups), "recent_alerts": len(alerts)},
        "last_refresh": cache.last_refresh.isoformat() if cache.last_refresh else None,
    }


@router.post("/backup")
async def trigger_backup():
    """Trigger a manual database backup."""
    try:
        from scripts.backup_db import backup_database, DB_PATH
        if not DB_PATH.exists():
            raise HTTPException(status_code=404, detail="No database file found")
        backup_path = backup_database(max_backups=7)
        return {"status": "ok", "backup_path": str(backup_path)}
    except Exception as e:
        logger.error(f"Backup failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/alerts")
async def get_alerts(limit: int = Query(50, le=200)):
    """Get recent change alerts."""
    db = get_database()
    alerts = db.get_change_alerts(limit=limit)
    return {"count": len(alerts), "alerts": alerts}


@router.post("/alerts/{alert_id}/dismiss")
async def dismiss_alert(alert_id: int):
    """Dismiss a change alert."""
    db = get_database()
    db.dismiss_alert(alert_id)
    return {"status": "success", "alert_id": alert_id}


def _normalize_address_for_search(query: str) -> list[str]:
    """
    Generate multiple LIKE patterns from a user's address query.

    HPD stores addresses like "140 EAST 28 STREET" while users type
    "140 E 28th St".  This expands common abbreviations and strips
    ordinal suffixes so we can match flexibly.

    Returns a list of search patterns (any match counts).
    """
    import re
    q = query.strip().upper()

    # Strip ordinal suffixes: 28TH -> 28, 1ST -> 1, 2ND -> 2, 3RD -> 3
    q = re.sub(r'\b(\d+)(?:ST|ND|RD|TH)\b', r'\1', q)

    # Direction abbreviations <-> full words
    _dir_map = {
        "E": "EAST", "W": "WEST", "N": "NORTH", "S": "SOUTH",
    }
    _dir_reverse = {v: k for k, v in _dir_map.items()}

    # Street-type abbreviations <-> full words
    _type_map = {
        "ST": "STREET", "AVE": "AVENUE", "AV": "AVENUE",
        "BLVD": "BOULEVARD", "DR": "DRIVE", "PL": "PLACE",
        "CT": "COURT", "LN": "LANE", "RD": "ROAD",
        "PKWY": "PARKWAY", "HWY": "HIGHWAY", "SQ": "SQUARE",
        "TER": "TERRACE", "CIR": "CIRCLE",
    }
    _type_reverse = {v: k for k, v in _type_map.items()}

    def _expand(text: str) -> set[str]:
        """Generate all plausible HPD spellings of *text*."""
        variants = {text}
        words = text.split()
        # Try expanding/contracting every word
        for i, w in enumerate(words):
            swaps: list[str] = []
            if w in _dir_map:
                swaps.append(_dir_map[w])
            if w in _dir_reverse:
                swaps.append(_dir_reverse[w])
            if w in _type_map:
                swaps.append(_type_map[w])
            if w in _type_reverse:
                swaps.append(_type_reverse[w])
            for s in swaps:
                new_words = words[:i] + [s] + words[i + 1:]
                variants.add(" ".join(new_words))
        return variants

    patterns = set()
    for v in _expand(q):
        patterns.add(f"%{v}%")
    return list(patterns)


@router.get("/buildings/search")
async def search_buildings(
    address: str = Query(..., min_length=3),
    limit: int = Query(20, le=100),
):
    """Search for buildings by address across all leads.

    Returns the response shape the frontend expects:
        { query, buildings: [...], total }
    with address normalization so "140 E 28th St" matches
    "140 EAST 28 STREET".
    """
    import json

    patterns = _normalize_address_for_search(address)

    db = get_database()
    with db._get_connection() as conn:
        # Build OR clause for each pattern variant
        like_clauses = " OR ".join(["buildings LIKE ? COLLATE NOCASE"] * len(patterns))
        params: list = list(patterns) + [limit * 10]
        rows = conn.execute(
            f"SELECT lead_id, agent_name, owner_name, buildings, building_ids, "
            f"boro, boros, total_units, portfolio_size, score, building_types "
            f"FROM leads WHERE ({like_clauses}) LIMIT ?",
            params,
        ).fetchall()

    # Also do a normalized match inside the JSON arrays
    search_upper = address.strip().upper()
    import re
    search_normalized = re.sub(r'\b(\d+)(?:ST|ND|RD|TH)\b', r'\1', search_upper)

    results = []
    seen = set()
    for row in rows:
        buildings = json.loads(row["buildings"] or "[]")
        building_ids = json.loads(row["building_ids"] or "[]")
        boros = json.loads(row["boros"] or "[]")
        building_types_obj = json.loads(row["building_types"] or "{}")

        for idx, b_addr in enumerate(buildings):
            b_upper = b_addr.upper()
            # Normalize the stored address the same way
            b_normalized = re.sub(r'\b(\d+)(?:ST|ND|RD|TH)\b', r'\1', b_upper)
            # Check if any pattern variant matches
            matched = False
            for pat in patterns:
                pat_inner = pat.strip('%')
                if pat_inner in b_upper or pat_inner in b_normalized:
                    matched = True
                    break
            if not matched and search_normalized in b_normalized:
                matched = True
            if not matched:
                continue

            b_id = building_ids[idx] if idx < len(building_ids) else ""
            dedup_key = f"{b_addr}|{row['lead_id']}"
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            boro = boros[0] if boros else (row["boro"] or "")

            results.append({
                "building_id": b_id,
                "address": b_addr,
                "house_number": b_addr.split(" ")[0] if " " in b_addr else "",
                "street_name": " ".join(b_addr.split(" ")[1:]) if " " in b_addr else b_addr,
                "boro": boro,
                "units_res": row["total_units"] or 0,
                "building_class": "",
                "building_type": ", ".join(k for k, v in building_types_obj.items() if v) if building_types_obj else "",
                "lead_id": row["lead_id"],
                "agent_name": row["agent_name"] or "",
                "score": row["score"] or 0,
            })
            if len(results) >= limit:
                break
        if len(results) >= limit:
            break

    return {"query": address, "buildings": results, "total": len(results)}
