"""Print a read-only stale lead materialization reconciliation report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.db.session import get_sync_url
from src.services.lead_reconciliation import (
    execute_stale_lead_reconciliation,
    preview_stale_lead_reconciliation,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-limit", type=int, default=25)
    parser.add_argument("--search", default=None, help="Limit duplicate samples to display names containing this text")
    parser.add_argument("--grouping-key", default=None, help="Limit to one exact generated grouping key, e.g. 'LEMLE AND WOLFF'")
    parser.add_argument("--execute", action="store_true", help="Repair low-risk legal-suffix groups")
    parser.add_argument(
        "--confirm-execute",
        action="store_true",
        help="Required with --execute; prevents accidental data mutation",
    )
    args = parser.parse_args()

    engine = create_engine(get_sync_url())
    try:
        with Session(engine) as session:
            if args.execute:
                report = execute_stale_lead_reconciliation(
                    session,
                    confirm_execute=args.confirm_execute,
                    search=args.search,
                    grouping_key=args.grouping_key,
                    dry_run_sample_limit=args.sample_limit,
                )
                session.commit()
            else:
                report = preview_stale_lead_reconciliation(
                    session,
                    sample_limit=args.sample_limit,
                    search=args.search,
                    grouping_key=args.grouping_key,
                )
        print(json.dumps(report, indent=2, default=str))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
