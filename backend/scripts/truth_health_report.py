"""Generate a no-mutation Data Truth & Confidence health report."""

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
from src.services.truth_health import build_truth_health_report  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--materialization-limit", type=int, default=500)
    parser.add_argument("--validation-sample-limit", type=int, default=20)
    parser.add_argument("--indent", type=int, default=2)
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    factory = get_session_factory()
    async with factory() as session:
        report = await build_truth_health_report(
            session,
            materialization_limit=args.materialization_limit,
            validation_sample_limit=args.validation_sample_limit,
        )
        await session.rollback()
    print(json.dumps(report, indent=args.indent, default=str))
    await shutdown_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
