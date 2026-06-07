"""Preview claim-ledger adjudication without mutating truth claims."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db.session import get_session_factory  # noqa: E402
from src.services.truth_adjudication import load_claim_adjudication_preview  # noqa: E402
from src.services.truth_health import build_schema_readiness_report, is_truth_schema_current, load_truth_schema_status  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--no-samples", action="store_true")
    parser.add_argument("--indent", type=int, default=2)
    return parser.parse_args()


async def run(args: argparse.Namespace) -> dict:
    factory = get_session_factory()
    async with factory() as session:
        schema_status = await load_truth_schema_status(session)
        if not is_truth_schema_current(schema_status):
            return build_schema_readiness_report(schema_status=schema_status)
        preview = await load_claim_adjudication_preview(
            session,
            limit=args.limit,
            include_samples=not args.no_samples,
        )
        preview["schema_status"] = schema_status
        return preview


def main() -> int:
    args = parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(result, indent=args.indent, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
