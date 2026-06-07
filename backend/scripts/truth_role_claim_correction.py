"""Preview or apply stale role-shape claim corrections."""

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
from src.services.truth_adjudication import preview_or_apply_role_claim_corrections  # noqa: E402
from src.services.truth_health import build_schema_readiness_report, is_truth_schema_current, load_truth_schema_status  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lead-id", default="0ff794d3ba2d")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--run-id")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-execute", action="store_true")
    parser.add_argument("--indent", type=int, default=2)
    return parser.parse_args()


async def run(args: argparse.Namespace) -> dict:
    factory = get_session_factory()
    async with factory() as session:
        schema_status = await load_truth_schema_status(session)
        if not is_truth_schema_current(schema_status):
            return build_schema_readiness_report(schema_status=schema_status)
        result = await preview_or_apply_role_claim_corrections(
            session,
            lead_id=args.lead_id,
            limit=args.limit,
            dry_run=not args.execute,
            confirm_execute=args.confirm_execute,
            run_id=args.run_id,
        )
        result["schema_status"] = schema_status
        return result


def main() -> int:
    args = parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(result, indent=args.indent, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
