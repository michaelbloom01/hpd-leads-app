"""CLI wrapper for conservative orphan-lead cleanup."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from src.services.lead_cleanup import execute_orphan_lead_cleanup, preview_orphan_lead_cleanup  # noqa: E402


def main(preview_only: bool = False, batch_size: int = 500) -> dict:
    if preview_only:
        return preview_orphan_lead_cleanup()
    return execute_orphan_lead_cleanup(batch_size=batch_size)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", action="store_true", help="Only print the orphan cleanup classification report")
    parser.add_argument("--batch-size", type=int, default=500, help="How many orphan mappings to process per transaction")
    args = parser.parse_args()
    print(json.dumps(main(preview_only=args.preview, batch_size=args.batch_size), default=str, indent=2))
