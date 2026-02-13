"""Enrichment routes — batch, single-lead, status, stop, reset, PLUTO, DOS."""
import logging
import threading
import time
import re
from datetime import datetime
from typing import Optional, Dict
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, Query, HTTPException, BackgroundTasks

from src.enrich.enricher import Enricher
from src.enrich.ny_dos import NYDOSClient
from src.storage.database import get_database
from src.services.cache_manager import get_cache
from src.services.lead_converter import row_to_lead
from src.schemas.requests import EnrichmentRequest
from src.schemas.responses import LeadResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["enrichment"])


# ---------------------------------------------------------------------------
# Inline enrichment (small batch, synchronous)
# ---------------------------------------------------------------------------

@router.post("/enrich")
async def enrich_leads(request: EnrichmentRequest):
    """Enrich specific leads by ID using web crawl."""
    cache = get_cache()
    if not request.lead_ids:
        raise HTTPException(status_code=400, detail="No lead IDs provided")

    lead_index = {l.lead_id: i for i, l in enumerate(cache.leads)}
    to_enrich = [cache.leads[lead_index[lid]] for lid in request.lead_ids if lid in lead_index]
    if not to_enrich:
        raise HTTPException(status_code=404, detail="No matching leads found")

    enricher = Enricher(use_cache=True)
    enriched = enricher.enrich_batch(to_enrich, limit=len(to_enrich), skip_enriched=False)

    db = get_database()
    updated_leads = []
    with cache.leads_lock:
        for lead in enriched:
            if lead.lead_id in lead_index:
                cache.leads[lead_index[lead.lead_id]] = lead
                updated_leads.append(lead)
            db.save_enrichment(
                lead_id=lead.lead_id, phone=lead.phone, email=lead.email,
                website=lead.website, business_summary=lead.business_summary,
                owner_principal=lead.owner_principal,
                enrichment_status=lead.enrichment_status, enrichment_sources=lead.enrichment_sources,
            )
    if updated_leads:
        db.save_leads(updated_leads)

    return {
        "status": "success", "enriched_count": len(enriched),
        "results": [{"lead_id": l.lead_id, "agent_name": l.agent_name, "website": l.website,
                      "phone": l.phone, "email": l.email, "status": l.enrichment_status} for l in enriched],
    }


# ---------------------------------------------------------------------------
# Background enrichment runners
# ---------------------------------------------------------------------------

def _run_background_enrichment(limit: int, min_score: Optional[float] = None):
    """Background task to enrich leads with persistent job tracking."""
    cache = get_cache()
    cache.enrichment_stop_requested = False
    db = get_database()

    candidates = [l for l in cache.leads if l.enrichment_status not in ("complete", "partial") and (min_score is None or l.score >= min_score)]
    candidates.sort(key=lambda l: l.score, reverse=True)
    batch = candidates[:limit]
    if not batch:
        return

    lead_ids = [l.lead_id for l in batch]
    job_id = db.create_enrichment_job(job_type="batch", lead_ids=lead_ids, config={"min_score": min_score, "limit": limit})
    _run_background_enrichment_with_job(job_id, set(lead_ids), min_score)


def _run_background_enrichment_with_job(job_id: int, lead_ids_remaining: set, min_score: Optional[float] = None):
    """Background enrichment with persistent job tracking (supports resume)."""
    cache = get_cache()
    cache.enrichment_stop_requested = False

    cache.enrichment.update(
        running=True, phase="sequential", progress=0, total=len(lead_ids_remaining),
        completed=0, failed=0, started_at=datetime.now().isoformat(), finished_at=None, error=None,
    )

    try:
        enricher = Enricher(use_cache=True)
        db = get_database()
        lead_index = cache.get_lead_index()
        batch = [l for l in cache.leads if l.lead_id in lead_ids_remaining]
        batch.sort(key=lambda l: l.score, reverse=True)
        cache.enrichment.update(total=len(batch))
        remaining = list(lead_ids_remaining)

        for i, lead in enumerate(batch):
            if cache.enrichment_stop_requested:
                cache.enrichment.update(error="Stopped by user")
                db.update_enrichment_job(job_id, status="paused", lead_ids_remaining=remaining,
                                         processed=i, completed=cache.enrichment.get("completed"),
                                         failed=cache.enrichment.get("failed"), error="Stopped by user")
                break

            cache.enrichment.update(progress=i + 1, current_lead=lead.agent_name or lead.owner_name)
            try:
                enriched_lead = enricher.enrich_lead(lead)
                with cache.leads_lock:
                    if lead.lead_id in lead_index:
                        cache.leads[lead_index[lead.lead_id]] = enriched_lead
                db.save_leads([enriched_lead])
                if lead.lead_id in remaining:
                    remaining.remove(lead.lead_id)

                key = "completed" if enriched_lead.enrichment_status in ("complete", "partial") else "failed"
                cache.enrichment.update(**{key: cache.enrichment.get(key, 0) + 1})

                if (i + 1) % 10 == 0:
                    db.update_enrichment_job(job_id, processed=i + 1, completed=cache.enrichment.get("completed"),
                                             failed=cache.enrichment.get("failed"), lead_ids_remaining=remaining)
            except Exception as e:
                logger.warning(f"Failed to enrich {lead.agent_name}: {e}")
                cache.enrichment.update(failed=cache.enrichment.get("failed", 0) + 1)
                if lead.lead_id in remaining:
                    remaining.remove(lead.lead_id)

        db.update_enrichment_job(job_id, status="completed", processed=len(batch),
                                 completed=cache.enrichment.get("completed"),
                                 failed=cache.enrichment.get("failed"), lead_ids_remaining=[])
        try:
            db.set_setting("enrichment_completed_at", datetime.now().isoformat())
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Background enrichment failed: {e}", exc_info=True)
        cache.enrichment.update(error=str(e))
        db.update_enrichment_job(job_id, status="failed", error=str(e))
    finally:
        cache.enrichment_stop_requested = False
        cache.enrichment.update(running=False, phase="complete", current_lead=None, finished_at=datetime.now().isoformat())


def _run_full_enrichment_batch(lead_ids: list):
    """Background: unified enrichment (contacts + research + AI) per lead."""
    cache = get_cache()
    cache.enrichment_stop_requested = False
    cache.enrichment.update(
        running=True, phase="full_enrichment", progress=0, total=len(lead_ids),
        completed=0, failed=0, started_at=datetime.now().isoformat(), finished_at=None, error=None,
    )

    try:
        from src.enrich.contact_sources import MultiSourceEnricher
        from src.enrich.web_crawl import WebCrawler
        from src.enrich.ai_summary import generate_company_description

        enricher_ms = MultiSourceEnricher()
        crawler = WebCrawler()
        db = get_database()
        lead_index = cache.get_lead_index()

        for idx, lead_id in enumerate(lead_ids):
            if cache.enrichment_stop_requested:
                break
            cache_idx = lead_index.get(lead_id)
            if cache_idx is None:
                continue
            lead = cache.leads[cache_idx]
            company_name = lead.agent_name or lead.owner_name
            cache.enrichment.update(progress=idx + 1, current_lead=company_name)

            try:
                # Contacts
                try:
                    result = enricher_ms.enrich(lead_id=lead_id, company_name=company_name, website=lead.website)
                    if result.phones and not lead.phone:
                        lead.phone = result.phones[0].value
                    if result.emails and not lead.email:
                        lead.email = result.emails[0].value
                    if result.website and not lead.website:
                        lead.website = result.website
                    if result.dos_registered_agent and not lead.owner_principal:
                        lead.owner_principal = result.dos_registered_agent
                except Exception as e:
                    logger.warning(f"Contact enrichment failed for {lead_id}: {e}")

                # Website + deep scrape
                try:
                    if not lead.website:
                        website = crawler.find_website(company_name + " property management NYC")
                        if website:
                            lead.website = website
                    if lead.website:
                        try:
                            scrape_data = crawler.deep_scrape(lead.website)
                            if scrape_data.get("phones") and not lead.phone:
                                lead.phone = scrape_data["phones"][0]
                            if scrape_data.get("emails") and not lead.email:
                                lead.email = scrape_data["emails"][0]
                        except Exception:
                            pass
                except Exception as e:
                    logger.warning(f"Research failed for {lead_id}: {e}")

                # AI summary
                try:
                    bt_dict = None
                    if lead.building_types:
                        bt_dict = {"condo": lead.building_types.condo, "coop": lead.building_types.coop,
                                   "rental_elevator": lead.building_types.rental_elevator,
                                   "rental_walkup": lead.building_types.rental_walkup,
                                   "small_residential": lead.building_types.small_residential}
                    description, _ = generate_company_description(
                        company_name=company_name, portfolio_size=lead.portfolio_size,
                        total_units=lead.total_units, boroughs=lead.boros, building_types=bt_dict,
                        owner_names=[lead.owner_principal] if lead.owner_principal else None,
                    )
                    if description:
                        lead.business_summary = description
                except Exception as e:
                    logger.warning(f"AI summary failed for {lead_id}: {e}")

                has_contact = bool(lead.phone or lead.email)
                has_website = bool(lead.website)
                lead.enrichment_status = "complete" if has_contact and has_website else "partial" if has_contact or has_website else "failed"
                cache.leads[cache_idx] = lead
                db.update_lead(lead_id, {
                    "phone": lead.phone, "email": lead.email, "website": lead.website,
                    "owner_principal": lead.owner_principal, "enrichment_status": lead.enrichment_status,
                    "business_summary": lead.business_summary,
                })
                cache.enrichment.update(completed=cache.enrichment.get("completed", 0) + 1)
            except Exception as e:
                logger.error(f"Full enrichment failed for {lead_id}: {e}")
                cache.enrichment.update(failed=cache.enrichment.get("failed", 0) + 1)
            time.sleep(2)

    except Exception as e:
        logger.error(f"Full enrichment batch error: {e}")
        cache.enrichment.update(error=str(e))
    finally:
        cache.enrichment.update(running=False, phase=None, current_lead=None, finished_at=datetime.now().isoformat())


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@router.post("/enrich/batch-full")
async def enrich_batch_full(
    background_tasks: BackgroundTasks,
    limit: int = Query(100, le=2000),
    min_units: int = Query(40),
    min_score: Optional[float] = Query(None),
):
    """Full unified enrichment for leads with >= min_units."""
    cache = get_cache()
    if cache.enrichment.get("running"):
        return {"status": "already_running", "progress": cache.enrichment.get("progress"), "total": cache.enrichment.get("total")}
    if not cache.leads:
        raise HTTPException(status_code=400, detail="No leads loaded.")

    candidates = [l for l in cache.leads if (l.total_units or 0) >= min_units and l.enrichment_status not in ("complete", "partial") and (min_score is None or (l.score or 0) >= min_score)]
    candidates.sort(key=lambda l: (l.total_units or 0), reverse=True)
    batch = candidates[:limit]
    if not batch:
        return {"status": "no_candidates", "message": f"No unenriched leads with >= {min_units} units found."}

    background_tasks.add_task(_run_full_enrichment_batch, [l.lead_id for l in batch])
    return {"status": "started", "message": f"Full enrichment started for {len(batch)} leads", "total": len(batch)}


@router.post("/enrich/batch")
async def enrich_batch(
    background_tasks: BackgroundTasks,
    limit: int = Query(50, le=500),
    min_score: Optional[float] = Query(None),
    background: bool = Query(True),
):
    """Enrich a batch of top leads."""
    cache = get_cache()
    if cache.enrichment.get("running"):
        return {"status": "already_running", "progress": cache.enrichment.get("progress"), "total": cache.enrichment.get("total")}
    if not cache.leads:
        raise HTTPException(status_code=400, detail="No leads loaded. Run /api/refresh first.")

    if background:
        background_tasks.add_task(_run_background_enrichment, limit, min_score)
        return {"status": "started", "message": f"Background enrichment started for up to {limit} leads", "check_progress": "/api/enrich/status"}
    else:
        enricher = Enricher(use_cache=True)
        enriched = enricher.enrich_batch(cache.leads, limit=limit, skip_enriched=True, min_score=min_score)
        lead_index = {l.lead_id: i for i, l in enumerate(cache.leads)}
        db = get_database()
        updated = []
        for lead in enriched:
            if lead.lead_id in lead_index:
                cache.leads[lead_index[lead.lead_id]] = lead
                updated.append(lead)
        if updated:
            db.save_leads(updated)
        return {"status": "success", "enriched_count": len(enriched)}


@router.post("/enrich/all")
async def enrich_all_leads(
    background_tasks: BackgroundTasks,
    dos_enabled: bool = Query(True), web_enabled: bool = Query(True),
    max_leads: Optional[int] = Query(None), max_web_crawl: int = Query(500),
    min_portfolio: int = Query(5), min_score: float = Query(0.0),
):
    """High-performance batch enrichment for all leads."""
    cache = get_cache()
    if cache.enrichment.get("running"):
        return {"status": "already_running", "phase": cache.enrichment.get("phase"), "check_progress": "/api/enrich/status"}
    if not cache.leads:
        raise HTTPException(status_code=400, detail="No leads loaded. Run /api/refresh first.")

    from src.enrich.batch_enricher import BatchEnricher, EnrichmentConfig

    def _run():
        cache.enrichment.update(
            running=True, phase="initializing", progress=0, total=len(cache.leads),
            completed=0, failed=0, dos_completed=0, dos_found=0, web_completed=0, web_found=0,
            started_at=datetime.now().isoformat(), finished_at=None, error=None,
        )
        try:
            db = get_database()
            lead_idx = cache.get_lead_index()
            config = EnrichmentConfig(
                dos_enabled=dos_enabled, dos_batch_size=100, dos_workers=5,
                web_enabled=web_enabled, web_batch_size=50, web_workers=3,
                min_portfolio_for_web=min_portfolio, min_score_for_web=min_score,
                skip_already_enriched=True, save_interval=50, max_leads=max_leads, max_web_crawl=max_web_crawl,
            )
            enricher = BatchEnricher(config)

            def on_progress(p):
                cache.enrichment.update(phase=p.phase, progress=p.processed, total=p.total_leads,
                                        dos_completed=p.dos_completed, dos_found=p.dos_found,
                                        web_completed=p.web_completed, web_found=p.web_found,
                                        failed=p.failed, current_lead=p.current_lead)

            def on_batch_complete(enriched_leads):
                with cache.leads_lock:
                    for lead in enriched_leads:
                        if lead.lead_id in lead_idx:
                            cache.leads[lead_idx[lead.lead_id]] = lead
                db.save_leads(enriched_leads)

            enricher.enrich_all(list(cache.leads), on_progress=on_progress, on_batch_complete=on_batch_complete)
            cache.enrichment.update(completed=cache.enrichment.get("dos_found", 0) + cache.enrichment.get("web_found", 0))
        except Exception as e:
            logger.error(f"Batch enrichment failed: {e}", exc_info=True)
            cache.enrichment.update(error=str(e))
        finally:
            cache.enrichment.update(running=False, phase="complete", current_lead=None, finished_at=datetime.now().isoformat())

    background_tasks.add_task(_run)
    return {"status": "started", "message": f"Batch enrichment started for {len(cache.leads)} leads", "check_progress": "/api/enrich/status"}


@router.get("/enrich/status")
async def get_enrichment_status():
    """Get the status of background enrichment."""
    cache = get_cache()
    state = cache.enrichment.snapshot()
    total = state.get("total", 0)
    progress = state.get("progress", 0)
    db = get_database()
    return {
        **state,
        "percent_complete": round(progress / total * 100, 1) if total > 0 else 0,
        "last_completed_at": db.get_setting("enrichment_completed_at"),
    }


@router.post("/enrich/stop")
async def stop_enrichment():
    """Stop the running enrichment job."""
    cache = get_cache()
    if not cache.enrichment.get("running"):
        return {"status": "not_running", "message": "No enrichment job is currently running"}
    cache.enrichment_stop_requested = True
    return {"status": "stopping", "message": "Stop requested.", "current_lead": cache.enrichment.get("current_lead")}


@router.post("/enrich/reset")
async def reset_enrichment():
    """Reset all stuck enrichment jobs and failed leads."""
    cache = get_cache()
    db = get_database()

    was_running = cache.enrichment.get("running")
    cache.enrichment.reset({
        "running": False, "phase": "", "progress": 0, "total": 0,
        "completed": 0, "failed": 0, "dos_completed": 0, "dos_found": 0,
        "web_completed": 0, "web_found": 0, "current_lead": None,
        "started_at": None, "finished_at": None, "error": None,
    })
    cache.enrichment_stop_requested = False

    with db._get_connection() as conn:
        stuck_jobs = conn.execute("SELECT id FROM enrichment_jobs WHERE status = 'running'").fetchall()
        for job in stuck_jobs:
            conn.execute("UPDATE enrichment_jobs SET status='failed', error='Force reset', finished_at=?, updated_at=? WHERE id=?",
                         (datetime.now().isoformat(), datetime.now().isoformat(), job["id"]))
        failed_count = conn.execute("SELECT COUNT(*) FROM leads WHERE enrichment_status = 'failed'").fetchone()[0]
        conn.execute("UPDATE leads SET enrichment_status = 'none' WHERE enrichment_status = 'failed'")
        conn.commit()

    for lead in cache.leads:
        if lead.enrichment_status == "failed":
            lead.enrichment_status = "none"

    return {"status": "reset_complete", "was_running": was_running, "stuck_jobs_cleared": len(stuck_jobs), "failed_leads_reset": failed_count}


@router.get("/enrichment/queue")
async def get_enrichment_queue():
    """Get info about leads waiting for enrichment."""
    cache = get_cache()
    with cache.leads_lock:
        snapshot = list(cache.leads)

    status_counts = {"none": 0, "partial": 0, "complete": 0, "failed": 0}
    high_value_unenriched = []
    for lead in snapshot:
        status = lead.enrichment_status or "none"
        if status in status_counts:
            status_counts[status] += 1
        if lead.portfolio_size >= 10 and status in ("none", ""):
            high_value_unenriched.append({"lead_id": lead.lead_id, "name": lead.agent_name or lead.owner_name, "portfolio_size": lead.portfolio_size, "score": lead.score})
    high_value_unenriched.sort(key=lambda x: x["score"], reverse=True)
    return {"total_leads": len(snapshot), "status_counts": status_counts, "high_value_unenriched_count": len(high_value_unenriched), "next_in_queue": high_value_unenriched[:10]}


@router.get("/enrichment-gaps")
async def get_enrichment_gaps():
    """Return counts of unenriched leads broken down by enrichment status."""
    db = get_database()
    with db._get_connection() as conn:
        rows = conn.execute("SELECT enrichment_status, COUNT(*) as cnt FROM leads GROUP BY enrichment_status").fetchall()
        breakdown = {r["enrichment_status"] or "none": r["cnt"] for r in rows}
        total = sum(breakdown.values())
        unenriched = breakdown.get("none", 0) + breakdown.get("pending", 0)
        return {"total_leads": total, "unenriched": unenriched, "breakdown": breakdown}


# ---------------------------------------------------------------------------
# Single-lead enrichment
# ---------------------------------------------------------------------------

@router.post("/leads/{lead_id}/enrich-contacts")
async def enrich_lead_contacts(lead_id: str):
    """Enrich a single lead's contact info using multiple sources."""
    cache = get_cache()
    lead = None
    lead_idx = None
    for i, l in enumerate(cache.leads):
        if l.lead_id == lead_id:
            lead = l
            lead_idx = i
            break
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    company_name = lead.agent_name or lead.owner_name
    try:
        from src.enrich.contact_sources import MultiSourceEnricher
        from src.schemas.responses import ContactWithSource as CSResponse

        enricher_ms = MultiSourceEnricher()
        location = f"{lead.boro}, New York" if lead.boro else "New York, NY"
        result = enricher_ms.enrich(lead_id=lead_id, company_name=company_name, website=lead.website, location=location, address=lead.address, contacts=lead.contacts)

        phones = [CSResponse(value=p.value, type="phone", source=p.source, source_url=p.source_url, confidence=p.confidence, verified=p.verified) for p in result.phones]
        emails = [CSResponse(value=e.value, type="email", source=e.source, source_url=e.source_url, confidence=e.confidence, verified=e.verified) for e in result.emails]

        if result.phones and not lead.phone:
            best = result.best_phone()
            if best:
                lead.phone = best.value
        if result.emails and not lead.email:
            best = result.best_email()
            if best:
                lead.email = best.value
        if result.website and not lead.website:
            lead.website = result.website

        has_contact = bool(lead.phone or lead.email)
        has_website = bool(lead.website)
        lead.enrichment_status = "complete" if has_contact and has_website else "partial" if has_contact or has_website else lead.enrichment_status
        lead.last_enriched = datetime.now()

        with cache.leads_lock:
            cache.leads[lead_idx] = lead
        db = get_database()
        db.save_leads([lead])

        return {"status": "success", "lead_id": lead_id, "phones": [p.model_dump() for p in phones], "emails": [e.model_dump() for e in emails], "website": result.website, "enrichment_status": lead.enrichment_status}
    except Exception as e:
        logger.error(f"Contact enrichment failed for {lead_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# PLUTO & DOS
# ---------------------------------------------------------------------------

@router.post("/enrich/pluto")
async def enrich_pluto(background_tasks: BackgroundTasks, limit: Optional[int] = Query(None)):
    """Enrich leads with PLUTO building type classifications."""
    cache = get_cache()
    if cache.pluto.get("running"):
        return {"status": "already_running", "progress": cache.pluto.get("progress")}
    if not cache.leads:
        raise HTTPException(status_code=400, detail="No leads loaded.")
    # Placeholder — full PLUTO integration is via /api/refresh?pluto=true
    return {"status": "started", "note": "For full PLUTO integration, run /api/refresh?full=true&pluto=true"}


@router.get("/enrich/pluto/status")
async def get_pluto_enrichment_status():
    """Get status of PLUTO enrichment."""
    return get_cache().pluto.snapshot()


@router.get("/pluto/lookup")
async def lookup_pluto(
    boro_id: str = Query(...), block: str = Query(...), lot: str = Query(...),
):
    """Look up a single property's PLUTO classification."""
    from src.ingest.pluto_client import PLUTOClient
    client = PLUTOClient()
    result = client.get_classification(boro_id, block, lot)
    if not result:
        raise HTTPException(status_code=404, detail=f"No PLUTO record found for BBL {boro_id}-{block}-{lot}")
    return {"bbl": result.bbl, "address": result.address, "building_class": result.building_class,
            "building_type": result.building_type, "land_use": result.land_use,
            "units_residential": result.units_res, "units_total": result.units_total, "year_built": result.year_built}


@router.get("/dos/lookup")
async def lookup_dos_entity(name: str = Query(...), include_inactive: bool = Query(False)):
    """Look up a business entity in the NY DOS registry."""
    client = NYDOSClient()
    entity = client.lookup_entity(name, include_inactive=include_inactive)
    if not entity:
        raise HTTPException(status_code=404, detail=f"No entity found matching '{name}'")
    return {"dos_id": entity.dos_id, "name": entity.name, "entity_type": entity.entity_type,
            "status": entity.status, "jurisdiction": entity.jurisdiction,
            "formation_date": entity.formation_date, "county": entity.county,
            "process_name": entity.process_name, "process_address": entity.process_address}


@router.get("/dos/search")
async def search_dos_entities(name: str = Query(...), limit: int = Query(10, le=50)):
    """Search for business entities in the NY DOS registry."""
    client = NYDOSClient()
    entities = client.search_entities(name, limit=limit)
    return {"count": len(entities), "results": [
        {"dos_id": e.dos_id, "name": e.name, "entity_type": e.entity_type,
         "status": e.status, "formation_date": e.formation_date, "process_name": e.process_name}
        for e in entities
    ]}


# ---------------------------------------------------------------------------
# API-only enrichment (Google Places + NY DOS)
# ---------------------------------------------------------------------------

@router.post("/enrich/api-only")
async def enrich_api_only(
    background_tasks: BackgroundTasks,
    limit: int = Query(500, le=2000),
    min_portfolio: int = Query(5),
    boro: Optional[str] = Query(None),
):
    """API-only batch enrichment using Google Places + NY DOS."""
    cache = get_cache()
    if cache.enrichment.get("running"):
        return {"status": "already_running", "check_progress": "/api/enrich/status"}
    if not cache.leads:
        raise HTTPException(status_code=400, detail="No leads loaded.")

    from src.enrich.contact_sources import GooglePlacesEnricher
    gp = GooglePlacesEnricher()
    background_tasks.add_task(_run_api_enrichment, limit, min_portfolio, boro)
    return {"status": "started", "config": {"limit": limit, "google_places_configured": gp.is_configured()}, "check_progress": "/api/enrich/status"}


def _run_api_enrichment(limit: int, min_portfolio: int = 5, boro: Optional[str] = None):
    """API-only batch enrichment using Google Places + NY DOS (background)."""
    cache = get_cache()
    from src.enrich.contact_sources import GooglePlacesEnricher
    cache.enrichment_stop_requested = False
    cache.enrichment.update(
        running=True, phase="api_enrichment", progress=0, total=limit,
        completed=0, failed=0, dos_completed=0, dos_found=0, web_completed=0, web_found=0,
        started_at=datetime.now().isoformat(), finished_at=None, error=None,
    )

    try:
        google_places = GooglePlacesEnricher()
        dos_client = NYDOSClient()
        db = get_database()

        candidates = [l for l in cache.leads if not l.phone and not l.email and l.portfolio_size >= min_portfolio and (not boro or l.boro == boro.upper())]
        candidates.sort(key=lambda l: (l.score, l.portfolio_size), reverse=True)
        batch = candidates[:limit]
        cache.enrichment.update(total=len(batch))
        lead_index = cache.get_lead_index()

        def enrich_single(lead):
            name = lead.agent_name or lead.owner_name
            if not name:
                return lead, False, False
            gp_found = dos_found = False

            if google_places.is_configured():
                try:
                    gp_result = google_places.find_business(name)
                    if gp_result:
                        if gp_result.get("phone") and not lead.phone:
                            digits = re.sub(r"\D", "", gp_result["phone"])
                            if len(digits) == 11 and digits.startswith("1"):
                                digits = digits[1:]
                            if len(digits) == 10:
                                lead.phone = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
                                gp_found = True
                        if gp_result.get("website") and not lead.website:
                            lead.website = gp_result["website"]
                            gp_found = True
                except Exception:
                    pass

            corp_indicators = ["LLC", "INC", "CORP", "LP", "L.P.", "LLP", "COMPANY"]
            if any(ind in name.upper() for ind in corp_indicators):
                try:
                    dos_entity = dos_client.lookup_entity(name)
                    if dos_entity:
                        if dos_entity.process_name and not lead.owner_principal:
                            lead.owner_principal = dos_entity.process_name
                        lead.dos_id = dos_entity.dos_id
                        lead.dos_status = dos_entity.status
                        dos_found = True
                except Exception:
                    pass

            lead.last_enriched = datetime.now()
            has_contact = bool(lead.phone or lead.email)
            has_website = bool(lead.website)
            lead.enrichment_status = "complete" if has_contact and has_website else "partial" if has_contact or has_website or dos_found else "failed"
            return lead, gp_found, dos_found

        completed_count = 0
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(enrich_single, l): l for l in batch}
            for future in as_completed(futures):
                if cache.enrichment_stop_requested:
                    break
                try:
                    enriched_lead, gp_f, dos_f = future.result()
                    with cache.leads_lock:
                        if enriched_lead.lead_id in lead_index:
                            cache.leads[lead_index[enriched_lead.lead_id]] = enriched_lead
                    db.save_leads([enriched_lead])
                    completed_count += 1
                    upd = {"progress": completed_count, "current_lead": enriched_lead.agent_name or enriched_lead.owner_name}
                    if gp_f:
                        upd["web_found"] = cache.enrichment.get("web_found", 0) + 1
                    if dos_f:
                        upd["dos_found"] = cache.enrichment.get("dos_found", 0) + 1
                    key = "completed" if enriched_lead.enrichment_status in ("complete", "partial") else "failed"
                    upd[key] = cache.enrichment.get(key, 0) + 1
                    cache.enrichment.update(**upd)
                except Exception as e:
                    cache.enrichment.update(failed=cache.enrichment.get("failed", 0) + 1)

    except Exception as e:
        logger.error(f"API enrichment failed: {e}", exc_info=True)
        cache.enrichment.update(error=str(e))
    finally:
        cache.enrichment_stop_requested = False
        cache.enrichment.update(running=False, phase="complete", current_lead=None, finished_at=datetime.now().isoformat())
