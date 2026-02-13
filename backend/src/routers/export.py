"""Export routes — CSV export, due diligence reports."""
import csv
import io
import logging
from datetime import datetime
from typing import Optional, List, Dict

from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import StreamingResponse

from src.storage.database import get_database
from src.services.lead_converter import row_to_lead

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["export"])


@router.get("/export/csv")
async def export_leads_csv(
    min_score: Optional[float] = None,
    min_portfolio: Optional[int] = None,
    boro: Optional[str] = None,
    limit: int = Query(500, le=5000),
):
    """Export leads to CSV format."""
    db = get_database()
    rows, total = db.get_leads_filtered(
        min_score=min_score, min_portfolio=min_portfolio, boro=boro, limit=limit, offset=0,
    )
    filtered = [row_to_lead(r) for r in rows]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Score", "Tier", "Agent Name", "Owner Name", "Portfolio Size",
        "Phone", "Email", "Website", "LinkedIn Company", "LinkedIn People",
        "Business Summary", "Address", "Borough", "Tags",
        "Enrichment Status", "Lead ID",
    ])
    for lead in filtered:
        tier = "A" if lead.score >= 80 else "B" if lead.score >= 60 else "C" if lead.score >= 40 else "D"
        linkedin_people = "; ".join(getattr(lead, "linkedin_people", []) or [])
        writer.writerow([
            round(lead.score, 1), tier, lead.agent_name or "", lead.owner_name or "",
            lead.portfolio_size, lead.phone or "", lead.email or "",
            lead.website or "", getattr(lead, "linkedin_url", "") or "",
            linkedin_people, (lead.business_summary or "")[:200],
            lead.address or "", lead.boro,
            ", ".join(lead.tags) if lead.tags else "",
            lead.enrichment_status, lead.lead_id,
        ])
    output.seek(0)
    filename = f"hpd_leads_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename={filename}"})


@router.get("/leads/{lead_id}/due-diligence")
async def generate_due_diligence(lead_id: str):
    """Generate a structured due diligence snapshot for a lead."""
    db = get_database()
    row = db.get_lead_by_id(lead_id)
    if not row:
        raise HTTPException(status_code=404, detail="Lead not found")

    lead = row_to_lead(row)
    outreach_events = db.get_outreach_events(lead_id)
    outreach_attempts = db.get_outreach_attempts(lead_id)

    comparable_rows, _ = db.get_leads_filtered(
        boro=lead.boro if lead.boro else None,
        min_portfolio=max(1, lead.portfolio_size - 20), limit=6, offset=0,
    )
    comparables = [
        {"lead_id": r["lead_id"], "name": r.get("company_name") or r.get("agent_name") or r.get("owner_name"),
         "portfolio_size": r.get("portfolio_size", 0), "total_units": r.get("total_units", 0),
         "score": r.get("score", 0), "entity_type": r.get("entity_type", "unknown")}
        for r in comparable_rows if r["lead_id"] != lead_id
    ][:5]

    company_display = lead.company_name or lead.agent_name or lead.owner_name
    md_lines = _build_dd_markdown(lead, company_display, outreach_events, outreach_attempts, comparables)

    return {
        "lead_id": lead_id, "company_name": company_display,
        "report_markdown": "\n".join(md_lines),
        "data": {
            "entity_type": lead.entity_type, "portfolio_size": lead.portfolio_size,
            "total_units": lead.total_units, "score": lead.score,
            "estimated_annual_revenue": lead.estimated_annual_revenue,
            "violation_count": lead.violation_count, "violations_per_unit": lead.violations_per_unit,
            "phone": lead.phone, "email": lead.email, "website": lead.website,
            "pipeline_stage": lead.pipeline_stage, "priority_rank": lead.priority_rank,
            "contacts_count": len(lead.contacts), "outreach_events_count": len(outreach_events),
            "comparables_count": len(comparables),
        },
        "comparables": comparables,
    }


def _build_dd_markdown(lead, company_display, outreach_events, outreach_attempts, comparables) -> List[str]:
    """Build the due diligence markdown report."""
    md = [
        f"# Due Diligence: {company_display}", "",
        "## Company Overview",
        f"- **Entity Type:** {lead.entity_type}",
        f"- **Company Name:** {lead.company_name or 'N/A'}",
        f"- **Agent/Owner:** {lead.agent_name}",
        f"- **Owner:** {lead.owner_name}",
        f"- **Primary Contact:** {lead.primary_contact or 'N/A'} ({lead.primary_contact_title or 'N/A'})",
        f"- **Borough:** {lead.boro}",
        f"- **Score:** {lead.score}/100",
        f"- **Pipeline Stage:** {lead.pipeline_stage}",
    ]
    if lead.dos_id:
        md += ["", "### NY DOS Corporation Info", f"- **DOS ID:** {lead.dos_id}", f"- **Status:** {lead.dos_status or 'N/A'}"]
    if lead.business_summary:
        md += ["", "### Business Summary", lead.business_summary]
    md += ["", "## Portfolio", f"- **Buildings:** {lead.portfolio_size}", f"- **Total Units:** {lead.total_units}",
           f"- **Boroughs:** {', '.join(lead.boros) if lead.boros else lead.boro}"]
    if lead.building_types:
        bt = lead.building_types
        bt_lines = [f"- Condo: {bt.condo}" if bt.condo else "", f"- Coop: {bt.coop}" if bt.coop else "",
                     f"- Rental Elevator: {bt.rental_elevator}" if bt.rental_elevator else "",
                     f"- Rental Walkup: {bt.rental_walkup}" if bt.rental_walkup else ""]
        md += ["", "### Building Type Breakdown"] + [l for l in bt_lines if l]
    md += ["", "## Financial Estimate"]
    md.append(f"- **Estimated Annual Revenue:** ${lead.estimated_annual_revenue:,.0f}" if lead.estimated_annual_revenue else "- **Estimated Annual Revenue:** N/A")
    if lead.violation_count > 0:
        md += ["", "## Operational Risk: Violations", f"- **Total:** {lead.violation_count}",
               f"- **Class C (immediately hazardous):** {lead.violation_class_c}", f"- **Per Unit:** {lead.violations_per_unit}"]
    md += ["", "## Contact Information", f"- **Phone:** {lead.phone or 'N/A'}", f"- **Email:** {lead.email or 'N/A'}",
           f"- **Website:** {lead.website or 'N/A'}"]
    if outreach_events or outreach_attempts:
        md += ["", "## Outreach History"]
        for e in outreach_events[:10]:
            md.append(f"- [{e.get('timestamp', 'N/A')[:10]}] {e.get('stage', '')} - {e.get('method', '')} - {e.get('outcome', '')}")
    if comparables:
        md += ["", "## Comparable Companies"]
        for c in comparables:
            md.append(f"- {c['name']} - {c['portfolio_size']} buildings, {c['total_units']} units, score: {c['score']}")
    md += ["", "### Building Addresses"]
    for addr in lead.buildings[:50]:
        md.append(f"- {addr}")
    if len(lead.buildings) > 50:
        md.append(f"- ... and {len(lead.buildings) - 50} more")
    return [l for l in md if l is not None]
