"""
Violations computation service.

Centralizes the duplicated logic for fetching HPD violations data and
aggregating it per lead — used by both the startup background task
and the /api/violations/refresh endpoint.
"""
import json
import logging
from datetime import datetime
from typing import List, Optional

from src.storage.database import get_database

logger = logging.getLogger(__name__)


class ViolationsService:
    """Fetch and aggregate HPD violations for leads."""

    @staticmethod
    def compute_and_persist(
        min_portfolio: int = 10,
        max_leads: int = 5000,
        batch_size: int = 200,
        cache_leads: Optional[List] = None,
    ) -> dict:
        """
        Fetch violations for high-value leads and persist to DB.

        Args:
            min_portfolio: Minimum portfolio size for eligibility.
            max_leads:     Max leads to process.
            batch_size:    Batch size for HPD API calls.
            cache_leads:   If provided, also update these in-memory Lead objects.

        Returns:
            Summary dict with counts.
        """
        from src.ingest.hpd_violations import HPDViolationsClient, aggregate_violations_for_lead

        db = get_database()
        rows, total = db.get_leads_filtered(
            min_portfolio=min_portfolio, limit=max_leads, offset=0,
        )

        if not rows:
            db.set_setting("violations_computed_at", datetime.now().isoformat())
            return {"leads_checked": 0, "leads_with_violations": 0,
                    "buildings_checked": 0, "buildings_with_violations": 0}

        # Collect building IDs per lead
        all_building_ids: list = []
        lead_buildings: dict = {}
        lead_units: dict = {}

        for row in rows:
            lead_id = row["lead_id"]
            bids = json.loads(row.get("building_ids") or "[]")
            lead_buildings[lead_id] = bids
            lead_units[lead_id] = row.get("total_units", 0) or 0
            all_building_ids.extend(bids)

        logger.info(
            f"Violations: fetching for {len(all_building_ids)} buildings "
            f"across {len(rows)} leads"
        )

        client = HPDViolationsClient()
        violations_data = client.fetch_violations_for_buildings(
            all_building_ids, batch_size=batch_size,
        )
        logger.info(f"Violations: API returned data for {len(violations_data)} buildings")

        # Aggregate per lead and persist
        updated = 0
        for lead_id, bids in lead_buildings.items():
            agg = aggregate_violations_for_lead(
                bids, violations_data, lead_units.get(lead_id, 0),
            )
            if agg["violation_count"] > 0:
                db.update_lead(lead_id, agg)
                updated += 1

        # Optionally update the in-memory cache
        if cache_leads is not None:
            for lead in cache_leads:
                if lead.lead_id in lead_buildings:
                    agg = aggregate_violations_for_lead(
                        lead_buildings[lead.lead_id],
                        violations_data,
                        lead_units.get(lead.lead_id, 0),
                    )
                    lead.violation_count = agg["violation_count"]
                    lead.violation_class_a = agg["violation_class_a"]
                    lead.violation_class_b = agg["violation_class_b"]
                    lead.violation_class_c = agg["violation_class_c"]
                    lead.violations_per_unit = agg["violations_per_unit"]

        db.set_setting("violations_computed_at", datetime.now().isoformat())

        summary = {
            "leads_checked": len(rows),
            "leads_with_violations": updated,
            "buildings_checked": len(all_building_ids),
            "buildings_with_violations": len(violations_data),
        }
        logger.info(f"Violations: computed for {updated}/{len(rows)} leads")
        return summary
