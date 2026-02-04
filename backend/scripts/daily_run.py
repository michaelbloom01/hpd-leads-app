#!/usr/bin/env python
"""
Daily run script - Incremental update.

Usage:
    python scripts/daily_run.py
"""
import json
import logging
import sys
from datetime import datetime, date
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logger import setup_logging
from src.ingest import HPDClient
from src.transform import normalize_building, aggregate_to_leads
from src.enrich import Enricher
from src.score import Scorer
from src.publish import SheetsWriter
from config.settings import settings

logger = logging.getLogger(__name__)

STATE_FILE = Path("data/run_state.json")


def load_last_run() -> date:
    """Load the last run date from state file."""
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            state = json.load(f)
            return date.fromisoformat(state.get("last_run", "2020-01-01"))
    return date(2020, 1, 1)


def save_run_state():
    """Save current run date to state file."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump({"last_run": date.today().isoformat()}, f)


def main():
    """Run daily incremental update."""
    setup_logging()
    start_time = datetime.now()
    logger.info("Starting daily run...")
    
    # Load last run date
    last_run = load_last_run()
    logger.info(f"Last run: {last_run}")
    
    # 1. Ingest (incremental)
    logger.info(f"Fetching HPD registrations since {last_run}...")
    client = HPDClient()
    raw_buildings = client.fetch_since(last_run)
    logger.info(f"Fetched {len(raw_buildings)} updated buildings")
    
    if not raw_buildings:
        logger.info("No updates, exiting")
        save_run_state()
        return
    
    # 2-6. Same as backfill (transform, aggregate, filter, enrich, score)
    logger.info("Processing...")
    buildings = [normalize_building(b) for b in raw_buildings]
    leads = aggregate_to_leads(buildings)
    leads = [l for l in leads if l.portfolio_size >= settings.min_portfolio_size]
    
    enricher = Enricher()
    leads = enricher.enrich_batch(leads, limit=settings.enrichment_batch_size)
    
    scorer = Scorer()
    leads = [scorer.score_lead(l) for l in leads]
    
    # 7. Incremental publish
    logger.info("Updating Google Sheet...")
    writer = SheetsWriter()
    updated, added = writer.incremental_update(leads)
    logger.info(f"Updated {updated}, added {added}")
    
    # Save state
    save_run_state()
    
    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"Daily run complete in {elapsed:.1f}s")


if __name__ == "__main__":
    try:
        main()
    except NotImplementedError as e:
        logger.error(f"Not yet implemented: {e}")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Daily run failed: {e}")
        sys.exit(1)
