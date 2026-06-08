"""Preview or execute truth claim materialization with explicit source filters."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db.session import get_session_factory, shutdown_engine  # noqa: E402
from src.services.truth_materialization import materialize_truth_claims  # noqa: E402
from src.tasks.truth_materialization import run_truth_materialization  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--source", action="append", default=None, help="Repeatable source filter.")
    parser.add_argument("--execute", action="store_true", help="Run with dry_run=false.")
    parser.add_argument("--confirm-execute", action="store_true", help="Required with --execute.")
    parser.add_argument("--indent", type=int, default=2)
    return parser.parse_args()


async def preview(args: argparse.Namespace) -> dict:
    factory = get_session_factory()
    async with factory() as session:
        result = await materialize_truth_claims(
            session,
            limit=args.limit,
            dry_run=True,
            confirm_execute=False,
            sources=args.source,
        )
        await session.rollback()
    return result


async def preview_and_shutdown(args: argparse.Namespace) -> dict:
    try:
        return await preview(args)
    finally:
        await shutdown_engine()


def main() -> int:
    args = parse_args()
    if args.execute and not args.confirm_execute:
        print(json.dumps({
            "dry_run": True,
            "mutations_planned": 0,
            "allowed_execute": False,
            "blocked_reason": "--execute requires --confirm-execute.",
            "required_execute_params": {"execute": True, "confirm_execute": True},
        }, indent=args.indent))
        return 2

    if args.execute:
        result = run_truth_materialization(
            limit=args.limit,
            dry_run=False,
            confirm_execute=True,
            sources=args.source,
        )
    else:
        result = asyncio.run(preview_and_shutdown(args))
    print(json.dumps(result, indent=args.indent, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
