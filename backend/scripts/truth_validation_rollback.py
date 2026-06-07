"""Dry-run or execute rollback for truth-validation review queue runs."""

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-execute", action="store_true")
    parser.add_argument("--include-reviewed", action="store_true")
    parser.add_argument("--indent", type=int, default=2)
    return parser.parse_args()


def build_validation_rollback_summary(
    *,
    run_id: str,
    review_status_counts: dict[str, int],
    validation_run_exists: bool,
    execute: bool = False,
    confirm_execute: bool = False,
    include_reviewed: bool = False,
    deleted_review_items: int = 0,
    deleted_validation_runs: int = 0,
) -> dict[str, Any]:
    open_count = int(review_status_counts.get("open") or 0)
    reviewed_count = sum(
        int(count)
        for status, count in review_status_counts.items()
        if status != "open"
    )
    blocked_reason = None
    if execute and not confirm_execute:
        blocked_reason = "Rollback execution requires --confirm-execute."
    elif execute and reviewed_count > 0 and not include_reviewed:
        blocked_reason = "Run has reviewed/non-open items; rerun with --include-reviewed only after preserving review history."

    would_delete_review_items = open_count + (reviewed_count if include_reviewed else 0)
    return {
        "run_id": run_id,
        "dry_run": not execute,
        "confirm_execute": confirm_execute,
        "include_reviewed": include_reviewed,
        "validation_run_exists": validation_run_exists,
        "review_status_counts": review_status_counts,
        "would_delete_review_items": 0 if execute else would_delete_review_items,
        "would_leave_reviewed_items": 0 if include_reviewed else reviewed_count,
        "would_delete_validation_run": bool(validation_run_exists) and not execute,
        "mutations_planned": 0 if not execute else would_delete_review_items + int(validation_run_exists),
        "blocked_reason": blocked_reason,
        "deleted_review_items": deleted_review_items,
        "deleted_validation_runs": deleted_validation_runs,
        "rollback_scope": [
            "truth_review_items rows whose run_id matches this validation run",
            "truth_validation_runs row for the same run_id",
        ],
        "rollback_strategy": (
            "Default execution deletes only open review items for the run and the validation-run envelope. "
            "Reviewed/non-open items are preserved unless --include-reviewed is explicitly supplied."
        ),
    }


def _load_status_counts(session: Session, run_id: str) -> dict[str, int]:
    rows = session.execute(
        text("""
            SELECT status, COUNT(*)::int AS count
            FROM truth_review_items
            WHERE run_id = :run_id
            GROUP BY status
            ORDER BY status
        """),
        {"run_id": run_id},
    )
    return {str(row.status or "unknown"): int(row.count or 0) for row in rows}


def _validation_run_exists(session: Session, run_id: str) -> bool:
    row = session.execute(
        text("SELECT EXISTS (SELECT 1 FROM truth_validation_runs WHERE run_id = :run_id) AS exists"),
        {"run_id": run_id},
    ).first()
    return bool(row.exists) if row else False


def rollback_validation_run(
    session: Session,
    *,
    run_id: str,
    execute: bool = False,
    confirm_execute: bool = False,
    include_reviewed: bool = False,
) -> dict[str, Any]:
    status_counts = _load_status_counts(session, run_id)
    validation_run_exists = _validation_run_exists(session, run_id)
    summary = build_validation_rollback_summary(
        run_id=run_id,
        review_status_counts=status_counts,
        validation_run_exists=validation_run_exists,
        execute=execute,
        confirm_execute=confirm_execute,
        include_reviewed=include_reviewed,
    )
    if not execute:
        return summary
    if summary["blocked_reason"]:
        raise SystemExit(summary["blocked_reason"])

    where_status = "" if include_reviewed else "AND status = 'open'"
    deleted_items = session.execute(
        text(f"""
            DELETE FROM truth_review_items
            WHERE run_id = :run_id
            {where_status}
        """),
        {"run_id": run_id},
    ).rowcount or 0
    deleted_runs = session.execute(
        text("DELETE FROM truth_validation_runs WHERE run_id = :run_id"),
        {"run_id": run_id},
    ).rowcount or 0
    session.commit()
    return build_validation_rollback_summary(
        run_id=run_id,
        review_status_counts=status_counts,
        validation_run_exists=validation_run_exists,
        execute=True,
        confirm_execute=confirm_execute,
        include_reviewed=include_reviewed,
        deleted_review_items=int(deleted_items),
        deleted_validation_runs=int(deleted_runs),
    )


def main() -> int:
    args = parse_args()
    engine = create_engine(get_sync_url())
    with Session(engine) as session:
        result = rollback_validation_run(
            session,
            run_id=args.run_id,
            execute=args.execute,
            confirm_execute=args.confirm_execute,
            include_reviewed=args.include_reviewed,
        )
    engine.dispose()
    print(json.dumps(result, indent=args.indent, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
