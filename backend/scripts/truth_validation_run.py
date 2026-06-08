"""Run truth validation and optional review-queue materialization."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tasks.truth_validation import run_truth_validation  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-limit", type=int, default=20)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-execute", action="store_true")
    parser.add_argument("--indent", type=int, default=2)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_truth_validation(
        job_id=None,
        sample_limit=args.sample_limit,
        dry_run=not args.execute,
        confirm_execute=args.confirm_execute,
    )
    print(json.dumps(result, indent=args.indent, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
