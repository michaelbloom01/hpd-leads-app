"""Build a concise no-mutation approval packet for truth activation.

This script is intentionally read-only. It combines the verbose migration
preflight and truth health report into the smaller decision packet an operator
needs before approving schema migration, materialization, or source refreshes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.truth_migration_preflight import _run_alembic, build_preflight_result  # noqa: E402
from src.db.session import get_session_factory, shutdown_engine  # noqa: E402
from src.services.truth_activation import build_activation_packet  # noqa: E402
from src.services.truth_health import (  # noqa: E402
    EXPECTED_TRUTH_ALEMBIC_REVISION,
    build_truth_health_report,
    load_truth_schema_status,
)


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
        schema_status = await load_truth_schema_status(session)
        health_report = await build_truth_health_report(
            session,
            materialization_limit=args.materialization_limit,
            validation_sample_limit=args.validation_sample_limit,
        )
        await session.rollback()

    preflight = build_preflight_result(
        schema_status=schema_status,
        current_result=_run_alembic("current"),
        heads_result=_run_alembic("heads"),
        sql_result=_run_alembic("upgrade", f"008_lead_lineage:{EXPECTED_TRUTH_ALEMBIC_REVISION}", "--sql"),
        rollback_sql_result=_run_alembic("downgrade", f"{EXPECTED_TRUTH_ALEMBIC_REVISION}:008_lead_lineage", "--sql"),
    )
    packet = build_activation_packet(preflight=preflight, health_report=health_report)
    print(json.dumps(packet, indent=args.indent, default=str))
    await shutdown_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
