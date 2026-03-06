"""CLI wrapper for the runtime-safe lead generation service.

Usage:
    python scripts/generate_leads_from_buildings.py [--min-portfolio N]
"""

import argparse
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from src.services.lead_generation import (  # noqa: E402
    _collapse_duplicate_company_leads,
    _is_probably_junk_name,
    generate_leads,
)


def main(min_portfolio: int = 1):
    return generate_leads(min_portfolio=min_portfolio)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-portfolio", type=int, default=1,
                        help="Minimum number of buildings for a lead to be included")
    args = parser.parse_args()
    main(min_portfolio=args.min_portfolio)
