"""Read-only post-recording check for actual current-ledger source overlap."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db.session import get_session_factory, shutdown_engine  # noqa: E402
from src.services.truth_adjudication import load_ledger_source_overlap_summary  # noqa: E402
from src.services.truth_health import build_schema_readiness_report, is_truth_schema_current, load_truth_schema_status  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-multi-source", type=int, default=1)
    parser.add_argument("--min-source-ready", type=int, default=1)
    parser.add_argument("--sample-limit", type=int, default=5)
    parser.add_argument("--indent", type=int, default=2)
    return parser.parse_args()


async def load_verified_single_source_summary(
    session: Any,
    *,
    min_sources: int = 2,
    sample_limit: int = 5,
) -> dict[str, Any]:
    """Summarize verified claims that still lack independent support."""
    count_rows = await session.execute(
        text("""
            WITH verified_claim_sources AS (
                SELECT
                    c.claim_id,
                    COUNT(DISTINCT e.source_name) FILTER (WHERE e.support_status = 'supports')::int
                        AS supporting_source_count
                FROM truth_claims c
                LEFT JOIN truth_evidence e ON e.claim_id = c.claim_id
                WHERE c.current_flag = true
                  AND c.belief_status = 'verified'
                GROUP BY c.claim_id
            )
            SELECT
                COUNT(*)::int AS verified_claim_count,
                COUNT(*) FILTER (WHERE supporting_source_count < :min_sources)::int
                    AS verified_single_source_claim_count
            FROM verified_claim_sources
        """),
        {"min_sources": min_sources},
    )
    count_row = count_rows.first()
    counts = dict(count_row._mapping) if count_row is not None else {}
    sample_rows = await session.execute(
        text("""
            WITH verified_claim_sources AS (
                SELECT
                    c.claim_id,
                    c.subject_type,
                    c.subject_id,
                    c.predicate,
                    c.object_type,
                    c.object_id,
                    c.normalized_value,
                    c.claim_type,
                    c.confidence_score,
                    COUNT(DISTINCT e.source_name) FILTER (WHERE e.support_status = 'supports')::int
                        AS supporting_source_count,
                    ARRAY_REMOVE(ARRAY_AGG(DISTINCT e.source_name) FILTER (WHERE e.support_status = 'supports'), NULL)
                        AS supporting_sources
                FROM truth_claims c
                LEFT JOIN truth_evidence e ON e.claim_id = c.claim_id
                WHERE c.current_flag = true
                  AND c.belief_status = 'verified'
                GROUP BY
                    c.claim_id,
                    c.subject_type,
                    c.subject_id,
                    c.predicate,
                    c.object_type,
                    c.object_id,
                    c.normalized_value,
                    c.claim_type,
                    c.confidence_score
            )
            SELECT *
            FROM verified_claim_sources
            WHERE supporting_source_count < :min_sources
            ORDER BY supporting_source_count, claim_id
            LIMIT :sample_limit
        """),
        {"min_sources": min_sources, "sample_limit": max(int(sample_limit or 5), 1)},
    )
    return {
        "verified_claim_count": int(counts.get("verified_claim_count") or 0),
        "verified_single_source_claim_count": int(counts.get("verified_single_source_claim_count") or 0),
        "sample_limit": max(int(sample_limit or 5), 1),
        "samples": [dict(row._mapping) for row in sample_rows],
    }


def build_post_recording_check(
    *,
    ledger_source_overlap: dict[str, Any],
    verified_single_source_summary: dict[str, Any],
    min_multi_source: int = 1,
    min_source_ready: int = 1,
) -> dict[str, Any]:
    """Build the pass/fail payload for the post-recording source-overlap gate."""
    multi_source_count = int(ledger_source_overlap.get("multi_source_fact_group_count") or 0)
    source_ready_count = int(ledger_source_overlap.get("source_ready_fact_group_count") or 0)
    verified_single_source_count = int(
        verified_single_source_summary.get("verified_single_source_claim_count") or 0
    )
    checks = [
        {
            "check": "actual_current_ledger_multi_source",
            "status": "pass" if multi_source_count >= min_multi_source else "fail",
            "observed": multi_source_count,
            "minimum": min_multi_source,
            "reason": (
                "Current ledger has nonzero independent source overlap."
                if multi_source_count >= min_multi_source
                else "Current ledger still has no independent source overlap; preview counts do not satisfy this gate."
            ),
        },
        {
            "check": "actual_current_ledger_source_ready",
            "status": "pass" if source_ready_count >= min_source_ready else "fail",
            "observed": source_ready_count,
            "minimum": min_source_ready,
            "reason": (
                "Current ledger has source-ready fact groups under adjudication thresholds."
                if source_ready_count >= min_source_ready
                else "No current ledger fact group is source-ready under adjudication thresholds."
            ),
        },
        {
            "check": "no_single_source_verified_claims",
            "status": "pass" if verified_single_source_count == 0 else "fail",
            "observed": verified_single_source_count,
            "maximum": 0,
            "reason": (
                "No verified current claim has fewer than two supporting source names."
                if verified_single_source_count == 0
                else "At least one current verified claim still has fewer than two supporting source names."
            ),
        },
    ]
    success = all(check["status"] == "pass" for check in checks)
    return {
        "run_type": "truth_source_overlap_post_recording_check",
        "dry_run": True,
        "mutations_planned": 0,
        "post_recording_success": success,
        "thresholds": {
            "min_multi_source_fact_groups": min_multi_source,
            "min_source_ready_fact_groups": min_source_ready,
            "max_verified_single_source_claims": 0,
        },
        "current_ledger": {
            "total_fact_group_count": ledger_source_overlap.get("total_fact_group_count"),
            "single_source_fact_group_count": ledger_source_overlap.get("single_source_fact_group_count"),
            "multi_source_fact_group_count": multi_source_count,
            "source_ready_fact_group_count": source_ready_count,
            "max_supporting_source_count": ledger_source_overlap.get("max_supporting_source_count"),
            "max_supporting_evidence_count": ledger_source_overlap.get("max_supporting_evidence_count"),
        },
        "verified_single_source_policy": verified_single_source_summary,
        "checks": checks,
        "safe_action": (
            "Post-recording source-overlap gate passed; continue with truth health, completion audit, "
            "activation packet, and production truth-surface checks before business use."
            if success
            else "Do not treat previewed source overlap as actual ledger truth. Record approved evidence or fix "
            "single-source verification failures, then rerun this check."
        ),
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    min_multi_source = max(int(args.min_multi_source or 1), 1)
    min_source_ready = max(int(args.min_source_ready or 1), 1)
    factory = get_session_factory()
    async with factory() as session:
        schema_status = await load_truth_schema_status(session)
        if not is_truth_schema_current(schema_status):
            readiness = build_schema_readiness_report(schema_status=schema_status)
            readiness["post_recording_success"] = False
            return readiness
        ledger_source_overlap = await load_ledger_source_overlap_summary(session)
        verified_single_source_summary = await load_verified_single_source_summary(
            session,
            min_sources=2,
            sample_limit=args.sample_limit,
        )
        return build_post_recording_check(
            ledger_source_overlap=ledger_source_overlap,
            verified_single_source_summary=verified_single_source_summary,
            min_multi_source=min_multi_source,
            min_source_ready=min_source_ready,
        )


async def main() -> int:
    args = parse_args()
    try:
        result = await run(args)
        print(json.dumps(result, indent=args.indent, default=str))
        return 0 if result.get("post_recording_success") else 1
    finally:
        await shutdown_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
