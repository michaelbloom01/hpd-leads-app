"""Preflight the additive truth-confidence migration without mutating data."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db.session import get_session_factory, shutdown_engine  # noqa: E402
from src.services.truth_health import EXPECTED_TRUTH_ALEMBIC_REVISION, load_truth_schema_status  # noqa: E402


def _run_alembic(*args: str) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "command": " ".join(["python", "-m", "alembic", *args]),
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "ok": completed.returncode == 0,
    }


def _extract_revisions(output: str) -> list[str]:
    revisions: list[str] = []
    for line in output.splitlines():
        value = line.strip()
        if not value or value.startswith("INFO "):
            continue
        revisions.append(value.split()[0])
    return revisions


def build_preflight_result(
    *,
    schema_status: dict[str, Any],
    current_result: dict[str, Any],
    heads_result: dict[str, Any],
    sql_result: dict[str, Any],
    rollback_sql_result: dict[str, Any],
) -> dict[str, Any]:
    current_revisions = _extract_revisions(current_result.get("stdout") or "")
    head_revisions = _extract_revisions(heads_result.get("stdout") or "")
    sql_preview = (sql_result.get("stdout") or "").splitlines()[:20]
    rollback_sql_preview = (rollback_sql_result.get("stdout") or "").splitlines()[:20]
    ready_to_apply = (
        not schema_status.get("ready")
        and schema_status.get("current_revision") == "008_lead_lineage"
        and EXPECTED_TRUTH_ALEMBIC_REVISION in head_revisions
        and sql_result.get("ok") is True
        and rollback_sql_result.get("ok") is True
    )
    return {
        "dry_run": True,
        "mutations_planned": 0,
        "ready_to_apply_additive_truth_migration": ready_to_apply,
        "expected_revision": EXPECTED_TRUTH_ALEMBIC_REVISION,
        "schema_status": schema_status,
        "alembic_current": {
            "ok": current_result["ok"],
            "revisions": current_revisions,
            "stderr": current_result["stderr"],
        },
        "alembic_heads": {
            "ok": heads_result["ok"],
            "revisions": head_revisions,
            "stderr": heads_result["stderr"],
        },
        "offline_sql": {
            "ok": sql_result["ok"],
            "command": sql_result["command"],
            "preview_first_20_lines": sql_preview,
            "stderr": sql_result["stderr"],
        },
        "offline_rollback_sql": {
            "ok": rollback_sql_result["ok"],
            "command": rollback_sql_result["command"],
            "preview_first_20_lines": rollback_sql_preview,
            "stderr": rollback_sql_result["stderr"],
        },
        "rollback_strategy": (
            "If the additive truth-confidence migration must be backed out before production use, "
            f"run the generated Alembic downgrade from {EXPECTED_TRUTH_ALEMBIC_REVISION} to 008_lead_lineage. "
            "The downgrade drops only the additive truth program tables in dependency order."
        ),
        "approval_required": True,
        "approval_reason": f"Applying Alembic upgrade through {EXPECTED_TRUTH_ALEMBIC_REVISION} creates truth-confidence tables in the configured database.",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--indent", type=int, default=2)
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    factory = get_session_factory()
    async with factory() as session:
        schema_status = await load_truth_schema_status(session)
        await session.rollback()

    current_result = _run_alembic("current")
    heads_result = _run_alembic("heads")
    sql_result = _run_alembic("upgrade", f"008_lead_lineage:{EXPECTED_TRUTH_ALEMBIC_REVISION}", "--sql")
    rollback_sql_result = _run_alembic("downgrade", f"{EXPECTED_TRUTH_ALEMBIC_REVISION}:008_lead_lineage", "--sql")
    result = build_preflight_result(
        schema_status=schema_status,
        current_result=current_result,
        heads_result=heads_result,
        sql_result=sql_result,
        rollback_sql_result=rollback_sql_result,
    )
    print(json.dumps(result, indent=args.indent))
    await shutdown_engine()
    return 0 if result["ready_to_apply_additive_truth_migration"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
