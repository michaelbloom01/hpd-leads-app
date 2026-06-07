"""Read-only portfolio building/contact export.

Exports the same HPD/DOS contact roster used by Building Detail's
"People & Companies" section for every building in a PM portfolio.
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

from src.db.session import get_session_factory, shutdown_engine  # noqa: E402
from src.services.portfolio_export import (  # noqa: E402
    build_portfolio_export,
    json_default,
    write_csv,
    write_xlsx,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company", default="VENTURE NY PROPERTY MANAGEMENT LLC")
    parser.add_argument("--lead-id", default=None)
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--output-csv", type=Path, default=None)
    parser.add_argument("--output-xlsx", type=Path, default=None)
    parser.add_argument("--indent", type=int, default=2)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    factory = get_session_factory()
    async with factory() as session:
        payload = await build_portfolio_export(
            session=session,
            company=args.company,
            lead_id=args.lead_id,
        )
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(payload, indent=args.indent, default=json_default),
            encoding="utf-8",
        )
    if args.output_csv:
        write_csv(args.output_csv, payload["contact_rows"])
    if args.output_xlsx:
        write_xlsx(args.output_xlsx, payload)
    print(json.dumps(
        {key: value for key, value in payload.items() if key not in {"records", "contact_rows"}},
        indent=args.indent,
        default=json_default,
    ))
    await shutdown_engine()


if __name__ == "__main__":
    asyncio.run(main())
