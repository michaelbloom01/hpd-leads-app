"""Dry-run or execute rollback of a truth materialization run.

The rollback uses truth_materialization_manifest. New rows are deleted by run_id
in dependency order. Rows that existed before the run are never auto-deleted;
their before_snapshot payloads are reported for targeted repair or backup/PITR.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db.session import get_sync_url  # noqa: E402


DELETE_ORDER = (
    ("confidence_snapshot", "confidence_snapshots", "snapshot_id"),
    ("truth_evidence", "truth_evidence", "evidence_id"),
    ("truth_claim", "truth_claims", "claim_id"),
)


def _count_manifest(session: Session, run_id: str) -> dict[str, Any]:
    rows = session.execute(
        text("""
            SELECT item_type, was_existing, COUNT(*)::int AS count
            FROM truth_materialization_manifest
            WHERE run_id = :run_id
            GROUP BY item_type, was_existing
            ORDER BY item_type, was_existing
        """),
        {"run_id": run_id},
    ).fetchall()
    by_type: dict[str, dict[str, int]] = {}
    for row in rows:
        item_type = str(row.item_type)
        bucket = by_type.setdefault(item_type, {"new": 0, "existing": 0, "total": 0})
        key = "existing" if row.was_existing else "new"
        bucket[key] = int(row.count)
        bucket["total"] += int(row.count)
    return {
        "run_id": run_id,
        "entry_count": sum(bucket["total"] for bucket in by_type.values()),
        "by_type": by_type,
    }


def _existing_samples(session: Session, run_id: str, limit: int) -> list[dict[str, Any]]:
    rows = session.execute(
        text("""
            SELECT item_type, item_id, before_snapshot
            FROM truth_materialization_manifest
            WHERE run_id = :run_id
              AND was_existing = true
            ORDER BY item_type, item_id
            LIMIT :limit
        """),
        {"run_id": run_id, "limit": limit},
    ).fetchall()
    return [
        {
            "item_type": row.item_type,
            "item_id": row.item_id,
            "before_snapshot": row.before_snapshot,
        }
        for row in rows
    ]


def _delete_new_rows(session: Session, run_id: str) -> dict[str, int]:
    deleted: dict[str, int] = {}
    for item_type, table_name, id_column in DELETE_ORDER:
        result = session.execute(
            text(f"""
                DELETE FROM {table_name} target
                USING truth_materialization_manifest manifest
                WHERE manifest.run_id = :run_id
                  AND manifest.item_type = :item_type
                  AND manifest.was_existing = false
                  AND target.{id_column} = manifest.item_id
            """),
            {"run_id": run_id, "item_type": item_type},
        )
        deleted[item_type] = int(result.rowcount or 0)
    return deleted


def rollback_materialization_run(
    *,
    run_id: str,
    dry_run: bool = True,
    confirm_execute: bool = False,
    sample_limit: int = 10,
) -> dict[str, Any]:
    engine = create_engine(get_sync_url())
    with Session(engine) as session:
        manifest = _count_manifest(session, run_id)
        existing_samples = _existing_samples(session, run_id, sample_limit)
        new_row_count = sum(bucket.get("new", 0) for bucket in manifest["by_type"].values())
        existing_row_count = sum(bucket.get("existing", 0) for bucket in manifest["by_type"].values())
        if dry_run or not confirm_execute:
            return {
                "dry_run": True,
                "confirm_execute": confirm_execute,
                "mutations_planned": 0,
                "would_delete_new_rows": new_row_count,
                "would_leave_existing_rows_for_manual_restore": existing_row_count,
                "manifest": manifest,
                "existing_before_snapshot_samples": existing_samples,
                "required_execute_command": (
                    f"python scripts/truth_materialization_rollback.py --run-id {run_id} "
                    "--execute --confirm-execute"
                ),
                "rollback_order": [item_type for item_type, _, _ in DELETE_ORDER],
            }
        deleted = _delete_new_rows(session, run_id)
        session.commit()
        return {
            "dry_run": False,
            "confirm_execute": True,
            "mutations_planned": new_row_count,
            "deleted": deleted,
            "left_existing_rows_for_manual_restore": existing_row_count,
            "manifest": manifest,
            "existing_before_snapshot_samples": existing_samples,
            "rollback_order": [item_type for item_type, _, _ in DELETE_ORDER],
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-execute", action="store_true")
    parser.add_argument("--sample-limit", type=int, default=10)
    parser.add_argument("--indent", type=int, default=2)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.execute and not args.confirm_execute:
        raise SystemExit("--execute requires --confirm-execute")
    result = rollback_materialization_run(
        run_id=args.run_id,
        dry_run=not args.execute,
        confirm_execute=args.confirm_execute,
        sample_limit=args.sample_limit,
    )
    print(json.dumps(result, indent=args.indent, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
