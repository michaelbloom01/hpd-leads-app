"""
Agent tool implementations.

Eight tools, each a plain Python function that returns a dict.
No raw SQL anywhere — all queries go through database.py's validated methods.
"""

import json
import logging
import os
import threading
import time
from typing import Optional

from src.storage.database import get_database
from src.agent.types import AgentLeadRow

logger = logging.getLogger(__name__)

# Valid pipeline stages (used for validation)
VALID_PIPELINE_STAGES = {
    "research", "first_contact", "follow_up",
    "meeting_scheduled", "meeting_done",
    "loi", "due_diligence", "closed",
}


def _row_to_agent_lead(row: dict) -> dict:
    """Convert a raw DB row dict to an AgentLeadRow-compatible dict."""
    boros = []
    if row.get("boros"):
        try:
            boros = json.loads(row["boros"]) if isinstance(row["boros"], str) else row["boros"]
        except (json.JSONDecodeError, TypeError):
            pass
    if not boros and row.get("boro"):
        boros = [row["boro"]]

    return {
        "lead_id": row.get("lead_id", ""),
        "company_name": row.get("company_name") or row.get("agent_name") or "Unknown",
        "portfolio_size": row.get("portfolio_size") or 0,
        "total_units": row.get("total_units") or 0,
        "score": row.get("score") or 0.0,
        "estimated_annual_revenue": row.get("estimated_annual_revenue") or 0.0,
        "enrichment_status": row.get("enrichment_status") or "none",
        "phone": row.get("phone") or None,
        "email": row.get("email") or None,
        "website": row.get("website") or None,
        "boros": boros,
        "violations_per_unit": row.get("violations_per_unit") or 0.0,
        "pipeline_stage": row.get("pipeline_stage") or "research",
    }


# ---------------------------------------------------------------------------
# Tool 1: query_leads
# ---------------------------------------------------------------------------

def query_leads(
    min_units: Optional[int] = None,
    max_units: Optional[int] = None,
    min_score: Optional[float] = None,
    max_score: Optional[float] = None,
    boro: Optional[str] = None,
    building_type_has: Optional[str] = None,
    entity_type: Optional[str] = None,
    enrichment_status: Optional[str] = None,
    pipeline_stage: Optional[str] = None,
    has_phone: Optional[bool] = None,
    has_email: Optional[bool] = None,
    has_website: Optional[bool] = None,
    min_violations_per_unit: Optional[float] = None,
    max_violations_per_unit: Optional[float] = None,
    min_revenue: Optional[float] = None,
    max_revenue: Optional[float] = None,
    search: Optional[str] = None,
    sort_by: str = "score",
    sort_dir: str = "desc",
    limit: int = 20,
) -> dict:
    """Search and filter leads. Returns structured lead rows + total count."""
    db = get_database()

    # Hard cap at 50
    limit = min(max(limit, 1), 50)

    rows, total_count = db.get_leads_filtered(
        min_score=min_score,
        max_score=max_score,
        min_units=min_units,
        max_units=max_units,
        boro=boro,
        building_type_has=building_type_has,
        entity_type=entity_type,
        enrichment_status=enrichment_status,
        pipeline_stage=pipeline_stage,
        has_phone=has_phone,
        has_email=has_email,
        has_website=has_website,
        min_violations_per_unit=min_violations_per_unit,
        max_violations_per_unit=max_violations_per_unit,
        min_revenue=min_revenue,
        max_revenue=max_revenue,
        search=search,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=limit,
        offset=0,
    )

    leads = [_row_to_agent_lead(r) for r in rows]

    # Build filters_applied for the agent to reference
    filters_applied = {}
    if min_units is not None:
        filters_applied["min_units"] = min_units
    if max_units is not None:
        filters_applied["max_units"] = max_units
    if min_score is not None:
        filters_applied["min_score"] = min_score
    if max_score is not None:
        filters_applied["max_score"] = max_score
    if boro:
        filters_applied["boro"] = boro
    if building_type_has:
        filters_applied["building_type_has"] = building_type_has
    if entity_type:
        filters_applied["entity_type"] = entity_type
    if enrichment_status:
        filters_applied["enrichment_status"] = enrichment_status
    if pipeline_stage:
        filters_applied["pipeline_stage"] = pipeline_stage
    if has_phone is not None:
        filters_applied["has_phone"] = has_phone
    if has_email is not None:
        filters_applied["has_email"] = has_email
    if has_website is not None:
        filters_applied["has_website"] = has_website
    if min_violations_per_unit is not None:
        filters_applied["min_violations_per_unit"] = min_violations_per_unit
    if max_violations_per_unit is not None:
        filters_applied["max_violations_per_unit"] = max_violations_per_unit
    if min_revenue is not None:
        filters_applied["min_revenue"] = min_revenue
    if max_revenue is not None:
        filters_applied["max_revenue"] = max_revenue
    if search:
        filters_applied["search"] = search
    if sort_by != "score":
        filters_applied["sort_by"] = sort_by
    if sort_dir != "desc":
        filters_applied["sort_dir"] = sort_dir

    return {
        "leads": leads,
        "total_count": total_count,
        "showing": len(leads),
        "filters_applied": filters_applied,
    }


# ---------------------------------------------------------------------------
# Tool 2: get_lead_details
# ---------------------------------------------------------------------------

def get_lead_details(lead_id: str) -> dict:
    """Get full details for a single lead."""
    db = get_database()
    row = db.get_lead_by_id(lead_id)
    if not row:
        return {"error": f"Lead {lead_id} not found"}

    # Return the full row data
    result = dict(row)
    # Parse JSON fields
    for json_field in ["buildings", "boros", "contacts", "building_types",
                       "building_classes", "enrichment_sources", "score_breakdown", "tags"]:
        if result.get(json_field) and isinstance(result[json_field], str):
            try:
                result[json_field] = json.loads(result[json_field])
            except (json.JSONDecodeError, TypeError):
                pass
    return result


# ---------------------------------------------------------------------------
# Tool 3: update_leads_batch
# ---------------------------------------------------------------------------

def update_leads_batch(
    lead_ids: list[str],
    pipeline_stage: Optional[str] = None,
    priority_rank: Optional[int] = None,
    next_follow_up: Optional[str] = None,
) -> dict:
    """Update pipeline stage, priority, or follow-up for a batch of leads."""
    db = get_database()

    # Validate inputs
    if pipeline_stage and pipeline_stage not in VALID_PIPELINE_STAGES:
        return {"error": f"Invalid pipeline_stage: {pipeline_stage}. Must be one of: {', '.join(VALID_PIPELINE_STAGES)}"}
    if priority_rank is not None and not (0 <= priority_rank <= 5):
        return {"error": f"priority_rank must be 0-5, got {priority_rank}"}

    updates = {}
    if pipeline_stage:
        updates["pipeline_stage"] = pipeline_stage
    if priority_rank is not None:
        updates["priority_rank"] = priority_rank
    if next_follow_up:
        updates["next_follow_up"] = next_follow_up

    if not updates:
        return {"error": "No update fields provided"}

    updated = 0
    failed = []
    for lid in lead_ids:
        try:
            success = db.update_lead(lid, updates)
            if success:
                updated += 1
            else:
                failed.append(lid)
        except Exception as e:
            logger.error(f"Failed to update lead {lid}: {e}")
            failed.append(lid)

    # Also update the in-memory cache if it exists
    try:
        import api as _api
        with _api._leads_lock:
            for i, lead in enumerate(_api._leads_cache):
                if lead.lead_id in lead_ids:
                    if pipeline_stage:
                        lead.pipeline_stage = pipeline_stage
                    if priority_rank is not None:
                        lead.priority_rank = priority_rank
                    if next_follow_up:
                        lead.next_follow_up = next_follow_up
                    _api._leads_cache[i] = lead
    except Exception as e:
        logger.warning(f"Could not update in-memory cache: {e}")

    actions = []
    if pipeline_stage:
        actions.append(f"Set pipeline stage to '{pipeline_stage}' for {updated} leads")
    if priority_rank is not None:
        actions.append(f"Set priority rank to {priority_rank} for {updated} leads")
    if next_follow_up:
        actions.append(f"Set next follow-up to {next_follow_up} for {updated} leads")

    return {
        "updated": updated,
        "failed": failed,
        "actions": actions,
    }


# ---------------------------------------------------------------------------
# Tool 4: enrich_leads_batch
# ---------------------------------------------------------------------------

def enrich_leads_batch(lead_ids: list[str]) -> dict:
    """Start full-pipeline background enrichment for up to 20 leads.
    
    Full pipeline: multi-source contacts → deep website scrape → AI summary → dual-write.
    """
    if len(lead_ids) > 20:
        return {"error": "Maximum 20 leads per enrichment batch. Please narrow your selection."}

    # Validate leads exist
    db = get_database()
    valid_ids = []
    for lid in lead_ids:
        row = db.get_lead_by_id(lid)
        if row:
            valid_ids.append(lid)

    if not valid_ids:
        return {"error": "No valid lead IDs found"}

    def _run_enrichment():
        try:
            from src.enrich.contact_sources import MultiSourceEnricher
            from src.enrich.ai_summary import generate_company_description
            from src.enrich.web_crawl import WebCrawler
            from datetime import datetime as _dt

            enricher = MultiSourceEnricher()
            crawler = WebCrawler()

            for lid in valid_ids:
                try:
                    row = db.get_lead_by_id(lid)
                    if not row:
                        continue
                    company_name = row.get("company_name") or row.get("agent_name") or row.get("owner_name") or ""
                    
                    # Parse contacts and boros from JSON if needed
                    contacts_raw = row.get("contacts")
                    contacts = []
                    if contacts_raw:
                        try:
                            contacts = json.loads(contacts_raw) if isinstance(contacts_raw, str) else contacts_raw
                        except (json.JSONDecodeError, TypeError):
                            pass
                    
                    boros_raw = row.get("boros")
                    boros = []
                    if boros_raw:
                        try:
                            boros = json.loads(boros_raw) if isinstance(boros_raw, str) else boros_raw
                        except (json.JSONDecodeError, TypeError):
                            pass
                    
                    boro = row.get("boro") or (boros[0] if boros else "")
                    location = f"{boro}, New York, NY" if boro else "New York, NY"
                    
                    existing_sources = []
                    if row.get("enrichment_sources"):
                        try:
                            existing_sources = json.loads(row["enrichment_sources"]) if isinstance(row["enrichment_sources"], str) else row["enrichment_sources"]
                        except (json.JSONDecodeError, TypeError):
                            pass

                    # === Step 1: Multi-source contact enrichment ===
                    enrich_result = enricher.enrich(
                        lead_id=lid,
                        company_name=company_name,
                        website=row.get("website"),
                        location=location,
                        address=row.get("address"),
                        contacts=contacts,
                    )

                    updates = {}
                    if enrich_result.phones and not row.get("phone"):
                        updates["phone"] = enrich_result.phones[0].value
                    if enrich_result.emails and not row.get("email"):
                        updates["email"] = enrich_result.emails[0].value
                    if enrich_result.website and not row.get("website"):
                        updates["website"] = enrich_result.website
                    if enrich_result.dos_registered_agent and not row.get("owner_principal"):
                        updates["owner_principal"] = enrich_result.dos_registered_agent
                    
                    # === Step 2: Deep website scrape ===
                    current_website = updates.get("website") or row.get("website")
                    owner_names = []
                    if not current_website:
                        try:
                            found = crawler.find_website(f"{company_name} property management NYC")
                            if found:
                                updates["website"] = found
                                current_website = found
                        except Exception:
                            pass
                    
                    if current_website:
                        try:
                            scrape_data = crawler.deep_scrape(current_website)
                            if scrape_data.get("owner_names"):
                                owner_names = scrape_data["owner_names"]
                            if scrape_data.get("phones"):
                                for p in scrape_data["phones"]:
                                    if not (updates.get("phone") or row.get("phone")):
                                        updates["phone"] = p
                            if scrape_data.get("emails"):
                                for e_val in scrape_data["emails"]:
                                    if not (updates.get("email") or row.get("email")):
                                        updates["email"] = e_val
                        except Exception as scrape_err:
                            logger.warning(f"Deep scrape failed for {lid}: {scrape_err}")

                    # === Step 3: AI summary ===
                    if not row.get("business_summary"):
                        try:
                            building_types = None
                            bt_raw = row.get("building_types")
                            if bt_raw:
                                try:
                                    bt = json.loads(bt_raw) if isinstance(bt_raw, str) else bt_raw
                                    building_types = {
                                        "condo": bt.get("condo", 0),
                                        "coop": bt.get("coop", 0),
                                        "rental_elevator": bt.get("rental_elevator", 0),
                                        "rental_walkup": bt.get("rental_walkup", 0),
                                        "small_residential": bt.get("small_residential", 0),
                                    }
                                except (json.JSONDecodeError, TypeError):
                                    pass
                            
                            description, _ = generate_company_description(
                                company_name=company_name,
                                portfolio_size=row.get("portfolio_size") or 0,
                                total_units=row.get("total_units") or 0,
                                boroughs=boros,
                                building_types=building_types,
                                owner_names=owner_names or ([row.get("owner_principal")] if row.get("owner_principal") else None),
                            )
                            if description:
                                updates["business_summary"] = description
                        except Exception as ai_err:
                            logger.warning(f"AI summary failed for {lid}: {ai_err}")

                    # === Compute enrichment status ===
                    has_contact = bool(updates.get("phone") or row.get("phone") or updates.get("email") or row.get("email"))
                    has_website = bool(updates.get("website") or row.get("website"))
                    has_summary = bool(updates.get("business_summary") or row.get("business_summary"))
                    if has_contact and has_website and has_summary:
                        updates["enrichment_status"] = "complete"
                    elif has_contact or has_website:
                        updates["enrichment_status"] = "partial"
                    else:
                        updates["enrichment_status"] = "failed"
                    
                    # Track sources
                    all_sources = list(set(existing_sources + enrich_result.sources_succeeded))
                    updates["enrichment_sources"] = json.dumps(all_sources)
                    updates["last_enriched"] = _dt.now().isoformat()
                    
                    # Auto-advance pipeline
                    current_stage = row.get("pipeline_stage") or "research"
                    if current_stage == "research" and has_contact:
                        updates["pipeline_stage"] = "first_contact"

                    # === Persist: update leads table ===
                    if updates:
                        db.update_lead(lid, updates)

                    # === Persist: dual-write to enrichment_cache ===
                    db.save_enrichment(
                        lead_id=lid,
                        phone=updates.get("phone") or row.get("phone"),
                        email=updates.get("email") or row.get("email"),
                        website=updates.get("website") or row.get("website"),
                        business_summary=updates.get("business_summary") or row.get("business_summary"),
                        owner_principal=updates.get("owner_principal") or row.get("owner_principal"),
                        enrichment_status=updates.get("enrichment_status", "none"),
                        enrichment_sources=all_sources,
                    )

                    # Update in-memory cache
                    try:
                        import api as _api
                        with _api._leads_lock:
                            for i, lead in enumerate(_api._leads_cache):
                                if lead.lead_id == lid:
                                    for k, v in updates.items():
                                        if k == "enrichment_sources":
                                            setattr(lead, k, all_sources)
                                        elif k == "last_enriched":
                                            setattr(lead, k, _dt.now())
                                        else:
                                            setattr(lead, k, v)
                                    _api._leads_cache[i] = lead
                                    break
                    except Exception:
                        pass

                    time.sleep(2)  # Rate limit between leads
                except Exception as e:
                    logger.error(f"Enrichment failed for lead {lid}: {e}")
                    continue

            logger.info(f"Background enrichment complete for {len(valid_ids)} leads")
        except Exception as e:
            logger.error(f"Background enrichment thread failed: {e}")

    thread = threading.Thread(target=_run_enrichment, daemon=True)
    thread.start()

    return {
        "started": len(valid_ids),
        "message": f"Full enrichment started for {len(valid_ids)} leads in background (contacts + deep scrape + AI summary). Results will appear within a few minutes.",
        "actions": [f"Started full enrichment for {len(valid_ids)} leads"],
    }


# ---------------------------------------------------------------------------
# Tool 5: generate_cold_call_scripts
# ---------------------------------------------------------------------------

def generate_cold_call_scripts(lead_ids: list[str]) -> dict:
    """Generate personalized cold call scripts for up to 10 leads."""
    if len(lead_ids) > 10:
        return {"error": "Maximum 10 leads per script generation batch."}

    db = get_database()
    leads_data = []
    for lid in lead_ids:
        row = db.get_lead_by_id(lid)
        if row:
            # Parse JSON fields
            boros = []
            if row.get("boros"):
                try:
                    boros = json.loads(row["boros"]) if isinstance(row["boros"], str) else row["boros"]
                except (json.JSONDecodeError, TypeError):
                    pass
            building_types = {}
            if row.get("building_types"):
                try:
                    building_types = json.loads(row["building_types"]) if isinstance(row["building_types"], str) else row["building_types"]
                except (json.JSONDecodeError, TypeError):
                    pass

            leads_data.append({
                "lead_id": lid,
                "company_name": row.get("company_name") or row.get("agent_name") or "Unknown",
                "owner_name": row.get("owner_name") or "the owner",
                "phone": row.get("phone"),
                "portfolio_size": row.get("portfolio_size") or 0,
                "total_units": row.get("total_units") or 0,
                "boros": boros,
                "building_types": building_types,
                "violations_per_unit": row.get("violations_per_unit") or 0.0,
                "business_summary": row.get("business_summary") or "",
                "estimated_annual_revenue": row.get("estimated_annual_revenue") or 0.0,
            })

    if not leads_data:
        return {"error": "No valid leads found for script generation."}

    # Build prompt for batch script generation
    script_model = os.environ.get("AGENT_SCRIPT_MODEL", "claude-3-5-haiku-latest")

    lead_descriptions = []
    for i, ld in enumerate(leads_data, 1):
        desc = (
            f"Lead {i}: {ld['company_name']} (ID: {ld['lead_id']})\n"
            f"  Owner: {ld['owner_name']}\n"
            f"  Portfolio: {ld['portfolio_size']} buildings, {ld['total_units']} units\n"
            f"  Boroughs: {', '.join(ld['boros']) if ld['boros'] else 'Unknown'}\n"
            f"  Building types: {', '.join(f'{k}: {v}' for k, v in ld['building_types'].items() if v > 0) if ld['building_types'] else 'Unknown'}\n"
            f"  Violations/unit: {ld['violations_per_unit']:.2f}\n"
            f"  Est. annual revenue: ${ld['estimated_annual_revenue']:,.0f}\n"
            f"  Summary: {ld['business_summary'][:200] if ld['business_summary'] else 'No summary available'}\n"
        )
        lead_descriptions.append(desc)

    prompt = f"""Generate a personalized cold call script for each of the following property management leads.
You are calling on behalf of a PE firm looking to acquire property management companies in NYC.

Key talking points to weave in:
- Reference their specific portfolio (buildings, units, boroughs)
- If they have high violations, frame it as a challenge we can help with ("operational support")
- Reference their revenue potential without naming exact numbers
- Goal: get a 15-minute meeting to discuss partnership opportunities

For each lead, provide a complete script including:
1. Opening line (with their name)
2. Value proposition (personalized to their portfolio)
3. Handle common objections
4. Close (ask for a meeting)

Leads:
{chr(10).join(lead_descriptions)}

Output format: For each lead, start with "=== LEAD_ID: <lead_id> ===" on its own line, then the script. No other formatting."""

    try:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return {"error": "Anthropic API key not configured. Cannot generate scripts."}

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=script_model,
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )

        raw_text = response.content[0].text

        # Parse scripts by lead ID
        scripts = []
        current_lead_id = None
        current_lines = []

        for line in raw_text.split("\n"):
            if line.startswith("=== LEAD_ID:") and "===" in line[12:]:
                # Save previous script
                if current_lead_id and current_lines:
                    script_text = "\n".join(current_lines).strip()
                    # Find matching lead data
                    lead_info = next((ld for ld in leads_data if ld["lead_id"] == current_lead_id), None)
                    if lead_info:
                        scripts.append({
                            "lead_id": current_lead_id,
                            "company_name": lead_info["company_name"],
                            "phone": lead_info.get("phone"),
                            "owner_name": lead_info.get("owner_name"),
                            "script": script_text,
                        })
                # Start new script
                current_lead_id = line.split("LEAD_ID:")[1].split("===")[0].strip()
                current_lines = []
            else:
                current_lines.append(line)

        # Don't forget the last one
        if current_lead_id and current_lines:
            script_text = "\n".join(current_lines).strip()
            lead_info = next((ld for ld in leads_data if ld["lead_id"] == current_lead_id), None)
            if lead_info:
                scripts.append({
                    "lead_id": current_lead_id,
                    "company_name": lead_info["company_name"],
                    "phone": lead_info.get("phone"),
                    "owner_name": lead_info.get("owner_name"),
                    "script": script_text,
                })

        # If parsing failed, create a single entry with the whole text
        if not scripts and leads_data:
            scripts = [{
                "lead_id": leads_data[0]["lead_id"],
                "company_name": leads_data[0]["company_name"],
                "phone": leads_data[0].get("phone"),
                "owner_name": leads_data[0].get("owner_name"),
                "script": raw_text.strip(),
            }]

        return {"scripts": scripts, "count": len(scripts)}

    except Exception as e:
        logger.error(f"Script generation failed: {e}")
        return {"error": f"Script generation failed: {str(e)}"}


# ---------------------------------------------------------------------------
# Tool 6: compile_email_briefing
# ---------------------------------------------------------------------------

def compile_email_briefing(
    lead_ids: list[str],
    subject: Optional[str] = None,
    recipient_email: Optional[str] = None,
) -> dict:
    """Compile an HTML email briefing for the specified leads."""
    db = get_database()
    leads_data = []
    for lid in lead_ids:
        row = db.get_lead_by_id(lid)
        if row:
            leads_data.append(_row_to_agent_lead(row))

    if not leads_data:
        return {"error": "No valid leads found for briefing."}

    if not subject:
        subject = f"HPD Leads Briefing — {len(leads_data)} Leads"

    # Build HTML briefing
    from src.agent.email_service import build_briefing_html, send_briefing_email, save_briefing
    html = build_briefing_html(subject, leads_data)
    briefing_id = save_briefing(html)

    # Try to send email
    if recipient_email or os.environ.get("EMAIL_TO_DEFAULT"):
        to = recipient_email or os.environ.get("EMAIL_TO_DEFAULT", "")
        result = send_briefing_email(subject, html, to)
        if result.get("sent"):
            return {
                "briefing_id": briefing_id,
                "html": html,
                "lead_count": len(leads_data),
                "sent": True,
                "recipient": to,
            }

    return {
        "briefing_id": briefing_id,
        "html": html,
        "lead_count": len(leads_data),
        "sent": False,
        "message": "Email not configured — briefing saved for download.",
    }


# ---------------------------------------------------------------------------
# Tool 7: refine_rent_estimates
# ---------------------------------------------------------------------------

def refine_rent_estimates(lead_ids: list[str]) -> dict:
    """Refine rent estimates using public data sources. Max 15 leads."""
    if len(lead_ids) > 15:
        return {"error": "Maximum 15 leads per rent refinement batch."}

    from src.agent.rent_research import research_rents_for_leads
    return research_rents_for_leads(lead_ids)


# ---------------------------------------------------------------------------
# Tool 8: get_stats
# ---------------------------------------------------------------------------

def get_stats() -> dict:
    """Get aggregate statistics about the leads database."""
    db = get_database()
    stats = db.get_stats_sql()
    return stats


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------

TOOL_FUNCTIONS = {
    "query_leads": query_leads,
    "get_lead_details": get_lead_details,
    "update_leads_batch": update_leads_batch,
    "enrich_leads_batch": enrich_leads_batch,
    "generate_cold_call_scripts": generate_cold_call_scripts,
    "compile_email_briefing": compile_email_briefing,
    "refine_rent_estimates": refine_rent_estimates,
    "get_stats": get_stats,
}


def execute_tool(tool_name: str, tool_input: dict) -> dict:
    """Execute a tool by name with the given input. Returns the result dict."""
    func = TOOL_FUNCTIONS.get(tool_name)
    if not func:
        return {"error": f"Unknown tool: {tool_name}"}

    logger.info(f"Agent tool: {tool_name}, input: {json.dumps(tool_input)[:500]}")
    start = time.time()
    try:
        result = func(**tool_input)
    except TypeError as e:
        logger.error(f"Tool {tool_name} type error: {e}")
        result = {"error": f"Invalid parameters for {tool_name}: {str(e)}"}
    except Exception as e:
        logger.error(f"Tool {tool_name} failed: {e}", exc_info=True)
        result = {"error": f"Tool {tool_name} failed: {str(e)}"}

    duration_ms = int((time.time() - start) * 1000)
    logger.info(f"Agent tool: {tool_name} completed in {duration_ms}ms")
    return result
