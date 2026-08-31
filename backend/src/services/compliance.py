"""Compliance publication and read contracts with explicit coverage and evidence."""

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from src.ingest.dob_safety import (
    DATASET_URL,
    SOURCE_SYSTEM,
    normalize_record,
)
from src.models.compliance import (
    ComplianceBalanceObservation,
    ComplianceObservation,
    ComplianceRecord,
    ComplianceSourceCheck,
)

FRESHNESS_HOURS = 72
SCHEMA_TABLES = (
    "compliance_records",
    "compliance_observations",
    "compliance_source_checks",
    "compliance_balance_observations",
    "physical_buildings",
    "building_parcel_links",
)


def compliance_enabled() -> bool:
    return os.environ.get("COMPLIANCE_INTELLIGENCE_ENABLED", "false").lower() in {
        "true",
        "1",
        "yes",
    }


def utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def is_stale(
    source_updated_at: datetime | None, observed_at: datetime | None, now: datetime
) -> bool:
    timestamps = [utc(value) for value in (source_updated_at, observed_at) if value]
    return not timestamps or any(
        now - value > timedelta(hours=FRESHNESS_HOURS) for value in timestamps
    )


def publish_snapshot(session: Session, snapshot: dict, *, run_id: str) -> dict:
    """Publish only after complete fetch and normalization; caller owns transaction."""
    if (
        not snapshot.get("complete")
        or len(snapshot["rows"]) != snapshot["expected_count"]
    ):
        raise ValueError("A complete, count-verified snapshot is required.")
    values = [
        normalize_record(
            row,
            observed_at=snapshot["observed_at"],
            source_updated_at=snapshot["source_updated_at"],
            run_id=run_id,
        )
        for row in snapshot["rows"]
    ]
    if len({row["id"] for row in values}) != len(values):
        raise ValueError("Source record identity must be unique in a snapshot.")
    if any(row["bin"] not in snapshot["bins"] for row in values):
        raise ValueError("Records must stay inside the checked BIN scope.")
    # Lock overlapping scopes before changing records. The advisory lock lasts
    # through commit and makes concurrent pilot runs deterministic in PostgreSQL.
    if session.get_bind().dialect.name == "postgresql":
        for bin_value in sorted(snapshot["bins"]):
            session.execute(
                text("SELECT pg_advisory_xact_lock(152, :bin)"), {"bin": int(bin_value)}
            )
    inserted = changed = unchanged = 0
    observations = []
    for value in values:
        existing = session.get(ComplianceRecord, value["id"])
        if existing and utc(existing.observed_at) > utc(snapshot["observed_at"]):
            raise ValueError("A newer snapshot is already published for this scope.")
        is_changed = existing is None or existing.payload_hash != value["payload_hash"]
        if existing is None:
            existing = ComplianceRecord(**value, first_seen_at=snapshot["observed_at"])
            session.add(existing)
            inserted += 1
        else:
            for key, item in value.items():
                setattr(existing, key, item)
            changed += int(is_changed)
            unchanged += int(not is_changed)
        if is_changed:
            observations.append(
                ComplianceObservation(
                    id=uuid4().hex,
                    record_id=value["id"],
                    payload_hash=value["payload_hash"],
                    source_updated_at=value["source_updated_at"],
                    observed_at=value["observed_at"],
                    ingestion_run_id=run_id,
                    parser_version=value["parser_version"],
                    source_url=value["source_url"],
                    raw_payload=value["raw_payload"],
                )
            )
    session.flush()
    session.add_all(observations)
    for bin_value in snapshot["bins"]:
        check = session.get(ComplianceSourceCheck, (SOURCE_SYSTEM, bin_value))
        if check and utc(check.observed_at) > utc(snapshot["observed_at"]):
            raise ValueError("A newer complete BIN check is already published.")
        if check is None:
            check = ComplianceSourceCheck(source_system=SOURCE_SYSTEM, bin=bin_value)
            session.add(check)
        check.source_updated_at = snapshot["source_updated_at"]
        check.observed_at = snapshot["observed_at"]
        check.records_count = sum(row["bin"] == bin_value for row in values)
        check.ingestion_run_id = run_id
        check.snapshot_hash = snapshot["snapshot_hash"]
        check.source_url = DATASET_URL
    session.flush()
    return {
        "inserted": inserted,
        "changed": changed,
        "unchanged": unchanged,
        "checked_bins": len(snapshot["bins"]),
    }


def _as_dict(row) -> dict:
    if isinstance(row, dict):
        return row
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def build_response(
    *,
    scope: dict,
    buildings: list[dict],
    records: list,
    checks: list,
    balances: list,
    now: datetime | None = None,
    enabled: bool = True,
    identity_ready: bool = True,
    status_override: str | None = None,
    warnings: list[str] | None = None,
) -> dict:
    """Pure response construction shared by all views and regression fixtures."""
    now = utc(now) or datetime.now(timezone.utc)
    warnings = list(warnings or [])
    check_map = {row["bin"]: row for row in map(_as_dict, checks)}
    record_rows = list(map(_as_dict, records))
    balance_rows = list(map(_as_dict, balances))
    # The source-authoritative BIN is the monetary and physical-building grain.
    unique_buildings = {row["bin"]: row for row in buildings}
    output = []
    reported_total = 0
    known_balance_bins = 0
    total_records = active_records = checked = 0
    for bin_value, building in sorted(
        unique_buildings.items(),
        key=lambda item: (item[1].get("address") or "", item[0]),
    ):
        check = check_map.get(bin_value)
        checked += int(check is not None)
        stale = is_stale(
            check.get("source_updated_at") if check else None,
            check.get("observed_at") if check else None,
            now,
        )
        bin_records = []
        for value in record_rows:
            if value["bin"] != bin_value:
                continue
            value = dict(value)
            value.pop("raw_payload", None)
            value["stale"] = is_stale(
                value.get("source_updated_at"), value.get("observed_at"), now
            )
            value["present_in_latest_check"] = bool(
                check and value["ingestion_run_id"] == check["ingestion_run_id"]
            )
            if not value["present_in_latest_check"]:
                value["stale"] = True
                warnings.append(
                    f"BIN {bin_value}: retained historical record absent from the last complete check; closure is unverified."
                )
            if (
                building.get("bbl")
                and value.get("bbl")
                and building["bbl"] != value["bbl"]
            ):
                value["identity_status"] = "conflicting_source_identifiers"
                warnings.append(
                    f"BIN {bin_value}: DOB and HPD parcel references differ; review the source identities."
                )
            bin_records.append(value)
        stale = stale or any(row["stale"] for row in bin_records)
        total_records += len(bin_records)
        active_records += sum(
            str(row.get("status") or "").lower() == "active" for row in bin_records
        )
        # This release accepts only one disjoint LL152 BIN/category scope. It
        # never allocates a category balance to individual violations.
        bin_balances = sorted(
            (
                dict(row)
                for row in balance_rows
                if row["bin"] == bin_value
                and row["category"] == "LL152"
                and row["scope"] == "bin_category"
            ),
            key=lambda row: (utc(row["observed_at"]), row["id"]),
            reverse=True,
        )
        chosen = bin_balances[:1]
        conflict = bool(
            chosen
            and any(
                utc(row["observed_at"]) == utc(chosen[0]["observed_at"])
                and row["amount_cents"] != chosen[0]["amount_cents"]
                for row in bin_balances[1:]
            )
        )
        amount = None if not chosen or conflict else int(chosen[0]["amount_cents"])
        if conflict:
            warnings.append(
                f"BIN {bin_value}: conflicting balance observations require review; balance excluded from subtotal."
            )
        if amount is not None:
            reported_total += amount
            known_balance_bins += 1
        for value in bin_balances:
            value["stale"] = is_stale(
                value.get("source_updated_at"), value.get("observed_at"), now
            )
            value["amount_basis"] = "manual_portal_observation"
            value.pop("payload_hash", None)
        output.append(
            {
                **building,
                "records": sorted(
                    bin_records,
                    key=lambda row: str(row.get("issue_date") or ""),
                    reverse=True,
                ),
                "stale": stale,
                "source_check_status": "checked" if check else "not_checked",
                "source_updated_at": check.get("source_updated_at") if check else None,
                "observed_at": check.get("observed_at") if check else None,
                "reported_balance_cents": amount,
                "balance_observations": bin_balances,
                "interest_status": "unverified",
                "lien_status": "unverified",
            }
        )
    count = len(output)
    status = status_override or (
        "not_checked" if checked == 0 else "complete" if checked == count else "partial"
    )
    source_times = [
        utc(row.get("source_updated_at"))
        for row in check_map.values()
        if row.get("source_updated_at")
    ]
    observed_times = [
        utc(row.get("observed_at"))
        for row in check_map.values()
        if row.get("observed_at")
    ]
    if count and known_balance_bins < count:
        warnings.append(
            "DOB Safety provides no balance amounts. Reported balances require separate dated evidence; missing amounts remain unknown."
        )
    if any(row["stale"] for row in output):
        warnings.append(
            "Source coverage is stale or incomplete. Review dates before relying on agency status."
        )
    if scope["type"] == "portfolio":
        warnings.append(
            "Portfolio scope uses saved management links marked current. Verify company membership separately after an HPD source refresh. Management at the violation date remains unverified."
        )
    if reported_total > 9_007_199_254_740_991:
        warnings.append(
            "Reported subtotal exceeds the exact API integer range; inspect individual balances."
        )
        reported_total = None
    return {
        "scope": scope,
        "enabled": enabled,
        "identity_ready": identity_ready,
        "as_of": max(observed_times) if observed_times else None,
        "source_updated_at": min(source_times) if source_times else None,
        "stale": not output or any(row["stale"] for row in output),
        "coverage": {
            "status": status,
            "physical_building_count": count,
            "checked_building_count": checked,
            "records_count": total_records,
            "active_records_count": active_records,
            "balance_known_building_count": known_balance_bins,
            "missing_balance_bin_count": count - known_balance_bins,
        },
        "reported_balance_cents": reported_total if known_balance_bins else None,
        "estimated_penalty_cents": None,
        "warnings": list(dict.fromkeys(warnings)),
        "buildings": output,
        "provenance": [
            {
                "source_system": SOURCE_SYSTEM,
                "source_url": DATASET_URL,
                "source_updated_at": min(source_times) if source_times else None,
                "observed_at": max(observed_times) if observed_times else None,
                "status": status,
            }
        ],
    }


async def schema_ready(session: AsyncSession) -> bool:
    result = await session.execute(
        text(
            "SELECT "
            + ", ".join(
                f"to_regclass('public.{name}') IS NOT NULL AS {name}"
                for name in SCHEMA_TABLES
            )
        )
    )
    return all(result.one())


async def load_compliance(
    session: AsyncSession, *, scope_type: str, scope_id: str
) -> dict:
    scope = {"type": scope_type, "id": scope_id}
    empty = {
        "scope": scope,
        "buildings": [],
        "records": [],
        "checks": [],
        "balances": [],
    }
    if not compliance_enabled():
        return build_response(
            **empty,
            enabled=False,
            identity_ready=False,
            status_override="disabled",
            warnings=["Compliance intelligence is awaiting rollout."],
        )
    if not await schema_ready(session):
        return build_response(
            **empty,
            identity_ready=False,
            status_override="schema_unavailable",
            warnings=["Compliance and building-identity migrations are required."],
        )
    if scope_type == "portfolio":
        where = "p.bbl IN (SELECT DISTINCT bbl FROM building_management WHERE lead_id = :scope_id AND is_current = true)"
    elif scope_type == "parcel":
        where = "p.bbl = :scope_id"
    else:
        where = "b.bin = :scope_id"
    rows = await session.execute(
        text(f"""
        SELECT DISTINCT b.bin, p.bbl, b.address
        FROM physical_buildings b JOIN building_parcel_links p ON p.bin = b.bin
        WHERE p.is_current = true AND {where}
        ORDER BY b.bin, p.bbl
    """),
        {"scope_id": scope_id},
    )
    identities = [dict(row._mapping) for row in rows]
    if not identities:
        return build_response(
            **empty,
            identity_ready=False,
            status_override="identity_unavailable",
            warnings=[
                "No refreshed physical-building identity is available for this scope. No clean compliance conclusion can be drawn."
            ],
        )
    # Multiple current parcel links remain visible as an identity warning. The
    # building and its BIN-level balances enter a portfolio rollup only once.
    by_bin = {}
    parcel_sets = {}
    warnings = []
    for row in identities:
        by_bin.setdefault(row["bin"], row)
        parcel_sets.setdefault(row["bin"], set()).add(row["bbl"])
    for bin_value, parcels in parcel_sets.items():
        if len(parcels) > 1:
            by_bin[bin_value]["bbl"] = None
            warnings.append(
                f"BIN {bin_value}: multiple current parcel references require review."
            )
    bins = list(by_bin)
    records = (
        (
            await session.execute(
                select(ComplianceRecord).where(ComplianceRecord.bin.in_(bins))
            )
        )
        .scalars()
        .all()
    )
    checks = (
        (
            await session.execute(
                select(ComplianceSourceCheck).where(
                    ComplianceSourceCheck.bin.in_(bins),
                    ComplianceSourceCheck.source_system == SOURCE_SYSTEM,
                )
            )
        )
        .scalars()
        .all()
    )
    balances = (
        (
            await session.execute(
                select(ComplianceBalanceObservation).where(
                    ComplianceBalanceObservation.bin.in_(bins)
                )
            )
        )
        .scalars()
        .all()
    )
    return build_response(
        scope=scope,
        buildings=list(by_bin.values()),
        records=records,
        checks=checks,
        balances=balances,
        warnings=warnings,
    )
