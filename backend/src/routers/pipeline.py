"""Pipeline routes — refresh, violations, rescore, revenue, data status."""
import gc
import logging
import threading
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query, HTTPException, BackgroundTasks

from src.ingest.hpd_client import HPDClient
from src.transform.normalize import normalize_building
from src.transform.aggregate import aggregate_to_leads, StreamingLeadAggregator
from src.score.scorer import score_leads
from src.storage.database import get_database
from src.services.cache_manager import get_cache
from src.services.violations_service import ViolationsService
from src.schemas.responses import PipelineStatus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["pipeline"])


# ---------------------------------------------------------------------------
# Background refresh
# ---------------------------------------------------------------------------

def _run_background_refresh(limit: Optional[int], include_pluto: bool = True):
    """Background task to refresh pipeline data using streaming/chunked processing."""
    cache = get_cache()
    CHUNK_SIZE = 10000

    cache.refresh.update(
        running=True, phase="initializing", buildings_fetched=0,
        total_buildings=limit or 0, leads_created=0, pluto_lookups=0,
        started_at=datetime.now().isoformat(), finished_at=None, error=None,
    )

    try:
        logger.info(f"Background refresh started with limit={limit or 'ALL'}, pluto={include_pluto}")
        client = HPDClient()
        db = get_database()

        pluto_client = None
        if include_pluto:
            from src.ingest.pluto_client import PLUTOClient
            pluto_client = PLUTOClient()
            logger.info("PLUTO client initialized for building classification")

        aggregator = StreamingLeadAggregator()
        chunk_num = 0
        total_pluto_lookups = 0

        def update_progress(fetched, total):
            cache.refresh.update(buildings_fetched=fetched)
            if total:
                cache.refresh.update(total_buildings=total)

        for chunk_buildings in client.stream_buildings_with_contacts(
            chunk_size=CHUNK_SIZE, total_limit=limit, progress_callback=update_progress,
        ):
            chunk_num += 1
            cache.refresh.update(phase=f"processing chunk {chunk_num}")
            logger.info(f"Processing chunk {chunk_num}: {len(chunk_buildings)} buildings")

            if pluto_client:
                cache.refresh.update(phase=f"PLUTO lookup chunk {chunk_num}")
                logger.info(f"Fetching PLUTO data for chunk {chunk_num}...")
                pluto_data = pluto_client.get_classifications_batch(
                    chunk_buildings, boro_field="boroid", block_field="block", lot_field="lot",
                )
                total_pluto_lookups += len(pluto_data)
                cache.refresh.update(pluto_lookups=total_pluto_lookups)
                for building in chunk_buildings:
                    boro_id = building.get("boroid", "")
                    block = building.get("block", "")
                    lot = building.get("lot", "")
                    if boro_id and block and lot:
                        bbl = pluto_client.make_bbl(boro_id, block, lot)
                        if bbl in pluto_data:
                            pluto = pluto_data[bbl]
                            building["building_class"] = pluto.building_class
                            building["building_type"] = pluto.building_type
                            building["units_res"] = pluto.units_res
                            building["year_built"] = pluto.year_built

            cache.refresh.update(phase=f"normalizing chunk {chunk_num}")
            normalized = [normalize_building(b) for b in chunk_buildings]
            del chunk_buildings
            if pluto_client:
                del pluto_data

            aggregator.process_chunk(normalized)
            del normalized
            gc.collect()

            stats = aggregator.get_stats()
            logger.info(f"After chunk {chunk_num}: {stats['unique_leads']} leads, {stats['total_buildings']} buildings")
            cache.refresh.update(leads_created=stats["unique_leads"])

        cache.refresh.update(phase="finalizing")
        leads = aggregator.get_leads()
        logger.info(f"Created {len(leads)} leads from streaming aggregation")

        cache.refresh.update(phase="scoring")
        leads = score_leads(leads)

        cache.refresh.update(phase="persisting")
        leads = db.apply_persisted_data_to_leads(leads)
        db.save_leads(leads)
        logger.info(f"Persisted {len(leads)} leads to database")

        cache.leads = leads
        cache.last_refresh = datetime.now()
        cache.refresh.update(leads_created=len(leads))
        logger.info(f"Background refresh complete: {cache.refresh.get('buildings_fetched')} buildings -> {len(leads)} leads")

    except Exception as e:
        logger.error(f"Background refresh failed: {e}", exc_info=True)
        cache.refresh.update(error=str(e))
    finally:
        cache.refresh.update(running=False, finished_at=datetime.now().isoformat())


@router.post("/refresh")
async def refresh_pipeline(
    background_tasks: BackgroundTasks,
    limit: Optional[int] = Query(None),
    full: bool = Query(False),
    background: bool = Query(True),
    pluto: bool = Query(True),
):
    """Refresh the pipeline: fetch from HPD, normalize, aggregate, score."""
    cache = get_cache()
    if full:
        limit = None
    elif limit is None:
        limit = 10000

    if cache.refresh.get("running"):
        return {
            "status": "already_running", "message": "Background refresh is already in progress",
            "phase": cache.refresh.get("phase"), "buildings_fetched": cache.refresh.get("buildings_fetched"),
            "check_progress": "/api/refresh/status",
        }

    if (limit is None or limit > 15000) and not background:
        return {"status": "error", "message": "For datasets > 15,000 buildings, background=true is required"}

    if background:
        background_tasks.add_task(_run_background_refresh, limit, pluto)
        return {
            "status": "started",
            "message": f"Background refresh started for {limit or 'ALL'} buildings" + (" with PLUTO data" if pluto else ""),
            "check_progress": "/api/refresh/status",
        }
    else:
        client = HPDClient()
        raw_buildings = client.get_combined_data(building_limit=limit)
        buildings = [normalize_building(b) for b in raw_buildings]
        leads = aggregate_to_leads(buildings)
        leads = score_leads(leads)
        db = get_database()
        leads = db.apply_persisted_data_to_leads(leads)
        db.save_leads(leads)
        cache.leads = leads
        cache.last_refresh = datetime.now()
        return {"status": "success", "buildings_fetched": len(raw_buildings), "leads_created": len(leads)}


@router.get("/refresh/status")
async def get_refresh_status():
    """Get the status of background refresh."""
    return get_cache().refresh.snapshot()


@router.get("/status", response_model=PipelineStatus)
async def get_status():
    """Get pipeline status."""
    cache = get_cache()
    with cache.leads_lock:
        enriched = len([l for l in cache.leads if l.enrichment_status in ("complete", "partial")])
        top = max((l.score for l in cache.leads), default=0)
        total = len(cache.leads)
    return PipelineStatus(
        total_leads=total, last_refresh=cache.last_refresh.isoformat() if cache.last_refresh else None,
        enriched_count=enriched, top_score=top,
    )


@router.get("/stats")
async def get_stats():
    """Get detailed statistics using SQL aggregation."""
    db = get_database()
    stats = db.get_stats_sql()
    cache = get_cache()
    stats["last_refresh"] = cache.last_refresh.isoformat() if cache.last_refresh else None
    return stats


@router.get("/data-status")
async def get_data_status():
    """Get data freshness timestamps."""
    db = get_database()
    cache = get_cache()
    return {
        "revenue_computed_at": db.get_setting("revenue_computed_at"),
        "revenue_formula_version": db.get_setting("revenue_formula_version"),
        "violations_computed_at": db.get_setting("violations_computed_at"),
        "enrichment_completed_at": db.get_setting("enrichment_completed_at"),
        "last_refresh": cache.last_refresh.isoformat() if cache.last_refresh else None,
    }


@router.post("/violations/refresh")
async def refresh_violations():
    """Fetch HPD violations data and aggregate per lead."""
    cache = get_cache()
    with cache.leads_lock:
        leads_snapshot = list(cache.leads)
    summary = ViolationsService.compute_and_persist(cache_leads=leads_snapshot)
    # Write changes back
    with cache.leads_lock:
        lead_map = {l.lead_id: l for l in leads_snapshot}
        for i, lead in enumerate(cache.leads):
            if lead.lead_id in lead_map:
                cache.leads[i] = lead_map[lead.lead_id]
    return {"status": "success", **summary}


@router.post("/rescore")
async def rescore_all_leads():
    """Re-score all leads using Scoring V2."""
    from src.score.scorer import Scorer
    import json as _j

    db = get_database()
    leads = db.load_all_leads()
    scorer = Scorer()
    updated = 0
    for lead in leads:
        old_score = lead.score
        scorer.score_lead(lead)
        if lead.score != old_score:
            db.update_lead(lead.lead_id, {"score": lead.score, "score_breakdown": _j.dumps(lead.score_breakdown)})
            updated += 1

    cache = get_cache()
    with cache.leads_lock:
        for lead in cache.leads:
            scorer.score_lead(lead)
    return {"status": "success", "leads_rescored": len(leads), "leads_changed": updated}


@router.post("/estimate-revenue")
async def estimate_all_revenue():
    """Run revenue estimation on all leads."""
    from src.score.revenue import estimate_revenue

    db = get_database()
    leads = db.load_all_leads()
    updated = 0
    for lead in leads:
        rev = estimate_revenue(lead)
        monthly = rev["estimated_monthly_revenue"]
        annual = rev["estimated_annual_revenue"]
        if monthly > 0:
            db.update_lead(lead.lead_id, {"estimated_monthly_revenue": monthly, "estimated_annual_revenue": annual})
            updated += 1

    cache = get_cache()
    with cache.leads_lock:
        for lead in cache.leads:
            rev = estimate_revenue(lead)
            lead.estimated_monthly_revenue = rev["estimated_monthly_revenue"]
            lead.estimated_annual_revenue = rev["estimated_annual_revenue"]
    return {"status": "success", "leads_updated": updated, "total_leads": len(leads)}


@router.post("/classify-entities")
async def classify_entities():
    """Run entity classification on all existing leads."""
    import json as _json
    from src.transform.aggregate import _classify_entity

    db = get_database()
    classified = 0
    with db._get_connection() as conn:
        rows = conn.execute(
            "SELECT lead_id, agent_name, owner_name, owner_type, contacts FROM leads WHERE entity_type IS NULL OR entity_type = 'unknown'"
        ).fetchall()
        for row in rows:
            contacts = _json.loads(row["contacts"] or "[]")
            entity_info = _classify_entity(row["agent_name"] or "", row["owner_name"] or "", row["owner_type"] or "", contacts)
            conn.execute(
                "UPDATE leads SET entity_type=?, company_name=?, primary_contact=?, primary_contact_title=? WHERE lead_id=?",
                (entity_info["entity_type"], entity_info["company_name"], entity_info["primary_contact"], entity_info["primary_contact_title"], row["lead_id"]),
            )
            classified += 1
        conn.commit()

    cache = get_cache()
    for lead in cache.leads:
        if lead.entity_type in (None, "unknown"):
            entity_info = _classify_entity(lead.agent_name, lead.owner_name, lead.owner_type, lead.contacts)
            lead.entity_type = entity_info["entity_type"]
            lead.company_name = entity_info["company_name"]
            lead.primary_contact = entity_info["primary_contact"]
            lead.primary_contact_title = entity_info["primary_contact_title"]
    return {"status": "complete", "classified": classified}


@router.post("/refresh/check-updates")
async def check_for_updates():
    """Compare current HPD data against DB and detect changes."""
    from datetime import timedelta
    import requests as _req

    db = get_database()
    rows, total = db.get_leads_filtered(min_portfolio=10, limit=5000, offset=0)
    alerts_created = 0

    try:
        resp = _req.get("https://data.cityofnewyork.us/resource/tesw-yqqr.json", params={"$select": "COUNT(*) as cnt"}, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            new_count = int(data[0].get("cnt", 0)) if data else 0
            if new_count > total * 1.01:
                db.add_change_alert("data_growth", f"HPD building count increased: {new_count} total (our DB has {total} high-value leads)", details={"hpd_count": new_count, "our_count": total})
                alerts_created += 1
    except Exception as e:
        logger.warning(f"Failed to check HPD building count: {e}")

    stale_cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    with db._get_connection() as conn:
        stale_count = conn.execute("SELECT COUNT(*) FROM leads WHERE updated_at < ? AND portfolio_size >= 10", (stale_cutoff,)).fetchone()[0]
    if stale_count > 100:
        db.add_change_alert("stale_data", f"{stale_count} high-value leads haven't been refreshed in 7+ days", details={"stale_count": stale_count})
        alerts_created += 1

    return {"status": "success", "alerts_created": alerts_created, "high_value_leads": len(rows)}
