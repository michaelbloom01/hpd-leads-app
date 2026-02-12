#!/usr/bin/env python
"""
Backfill script - Full load of all HPD data.

Usage:
    python scripts/backfill.py [--limit N] [--csv-only] [--no-enrich]
    
Examples:
    # Test with 100 buildings, output to CSV only
    python scripts/backfill.py --limit 100 --csv-only
    
    # Full run to Google Sheets (requires credentials)
    python scripts/backfill.py
"""
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logger import setup_logging
from src.ingest import HPDClient
from src.transform.normalize import normalize_building
from src.transform.aggregate import aggregate_to_leads
from src.score.scorer import Scorer
from src.publish.sheets_writer import SheetsWriter
from config.settings import settings

logger = logging.getLogger(__name__)


def main(limit: int = None, csv_only: bool = False, no_enrich: bool = False):
    """
    Run full backfill.
    
    Args:
        limit: Maximum buildings to fetch (for testing)
        csv_only: Export to CSV instead of Google Sheets
        no_enrich: Skip enrichment step
    """
    setup_logging()
    start_time = datetime.now()
    logger.info("Starting backfill...")
    
    # 1. Ingest
    logger.info("Fetching HPD data...")
    client = HPDClient()
    raw_buildings = client.get_combined_data(building_limit=limit)
    logger.info(f"Fetched {len(raw_buildings)} buildings with contacts")
    
    # 2. Transform
    logger.info("Normalizing...")
    buildings = []
    for raw in raw_buildings:
        try:
            buildings.append(normalize_building(raw))
        except Exception as e:
            logger.warning(f"Failed to normalize building {raw.get('buildingid')}: {e}")
    logger.info(f"Normalized {len(buildings)} buildings")
    
    # 3. Aggregate
    logger.info("Aggregating to leads...")
    leads = aggregate_to_leads(buildings)
    logger.info(f"Created {len(leads)} leads")
    
    # NOTE: No portfolio_size filter — all leads are kept in the database.
    # Low-value leads are ranked low by scoring, not deleted.
    logger.info(f"Proceeding with all {len(leads)} leads (no portfolio filter)")
    
    # 5. Enrich (optional)
    if not no_enrich:
        try:
            from src.enrich import Enricher
            logger.info("Enriching top leads...")
            enricher = Enricher()
            leads = enricher.enrich_batch(leads, limit=settings.enrichment_batch_size)
        except NotImplementedError:
            logger.info("Enrichment not yet implemented, skipping...")
        except Exception as e:
            logger.warning(f"Enrichment failed, continuing without: {e}")
    else:
        logger.info("Skipping enrichment (--no-enrich)")
    
    # 6. Score
    logger.info("Scoring leads...")
    scorer = Scorer()
    leads = [scorer.score_lead(l) for l in leads]
    
    # Sort by score
    leads.sort(key=lambda l: l.score, reverse=True)
    
    # 7. Publish
    writer = SheetsWriter()
    
    if csv_only:
        # Export to CSV
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"data/leads_{timestamp}.csv"
        writer.export_to_csv(leads, output_path)
        logger.info(f"Exported {len(leads)} leads to {output_path}")
    else:
        # Write to Google Sheets
        if not settings.google_sheet_id:
            logger.error("GOOGLE_SHEET_ID not configured. Use --csv-only or set env var.")
            # Fall back to CSV
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = f"data/leads_{timestamp}.csv"
            writer.export_to_csv(leads, output_path)
            logger.info(f"Exported {len(leads)} leads to {output_path} (fallback)")
        else:
            count = writer.full_refresh(leads)
            logger.info(f"Wrote {count} rows to Google Sheet")
    
    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"Backfill complete in {elapsed:.1f}s")
    
    # Print summary
    print("\n" + "="*50)
    print("BACKFILL SUMMARY")
    print("="*50)
    print(f"Buildings fetched: {len(raw_buildings)}")
    print(f"Buildings normalized: {len(buildings)}")
    print(f"Leads created: {len(leads)}")
    print(f"Time elapsed: {elapsed:.1f}s")
    if leads:
        top_5 = leads[:5]
        print(f"\nTop 5 leads by score:")
        for i, lead in enumerate(top_5, 1):
            print(f"  {i}. {lead.agent_name or lead.owner_name} - {lead.portfolio_size} buildings (score: {lead.score})")
    print("="*50)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill HPD leads")
    parser.add_argument("--limit", type=int, help="Limit buildings to fetch (for testing)")
    parser.add_argument("--csv-only", action="store_true", help="Export to CSV instead of Google Sheets")
    parser.add_argument("--no-enrich", action="store_true", help="Skip enrichment step")
    args = parser.parse_args()
    
    try:
        main(limit=args.limit, csv_only=args.csv_only, no_enrich=args.no_enrich)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.exception(f"Backfill failed: {e}")
        sys.exit(1)
