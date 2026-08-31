"""Compliance publication and read contracts with explicit coverage and evidence."""

import os
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from src.ingest import dob_complaints, dob_ecb, dob_violations
from src.ingest.dob_safety import (
    DATASET_URL,
    SOURCE_SYSTEM,
    normalize_record,
    validate_bins,
)
from src.models.compliance import (
    ComplianceBalanceObservation,
    ComplianceObservation,
    ComplianceRecord,
    ComplianceSourceCheck,
)

FRESHNESS_HOURS = 72
SOURCE_CONFIG = {
    SOURCE_SYSTEM: {
        "url": DATASET_URL,
        "normalize": normalize_record,
        "validate_bins": validate_bins,
    },
    dob_complaints.SOURCE_SYSTEM: {
        "url": dob_complaints.DATASET_URL,
        "normalize": dob_complaints.normalize_record,
        "validate_bins": dob_complaints.validate_bins,
    },
    dob_violations.SOURCE_SYSTEM: {
        "url": dob_violations.DATASET_URL,
        "normalize": dob_violations.normalize_record,
        "validate_bins": dob_violations.validate_bins,
    },
    dob_ecb.SOURCE_SYSTEM: {
        "url": dob_ecb.DATASET_URL,
        "normalize": dob_ecb.normalize_record,
        "validate_bins": dob_ecb.validate_bins,
    },
}
SCHEMA_TABLES = (
    "compliance_records",
    "compliance_observations",
    "compliance_source_checks",
    "compliance_balance_observations",
    "physical_buildings",
    "building_parcel_links",
    "hpd_registration_snapshots",
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
    source_system = snapshot.get("source_system", SOURCE_SYSTEM)
    config = SOURCE_CONFIG.get(source_system)
    if config is None:
        raise ValueError("Unsupported compliance source.")
    bins = config["validate_bins"](snapshot.get("bins"))
    if (
        not snapshot.get("complete")
        or len(snapshot["rows"]) != snapshot["expected_count"]
    ):
        raise ValueError("A complete, count-verified snapshot is required.")
    values = [
        config["normalize"](
            row,
            observed_at=snapshot["observed_at"],
            source_updated_at=snapshot["source_updated_at"],
            run_id=run_id,
        )
        for row in snapshot["rows"]
    ]
    if len({row["id"] for row in values}) != len(values):
        raise ValueError("Source record identity must be unique in a snapshot.")
    if any(row["bin"] not in bins for row in values):
        raise ValueError("Records must stay inside the checked BIN scope.")
    # Lock overlapping scopes before changing records. The advisory lock lasts
    # through commit and makes concurrent pilot runs deterministic in PostgreSQL.
    if session.get_bind().dialect.name == "postgresql":
        for bin_value in bins:
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
    for bin_value in bins:
        check = session.get(ComplianceSourceCheck, (source_system, bin_value))
        if check and utc(check.observed_at) > utc(snapshot["observed_at"]):
            raise ValueError("A newer complete BIN check is already published.")
        if check is None:
            check = ComplianceSourceCheck(source_system=source_system, bin=bin_value)
            session.add(check)
        check.source_updated_at = snapshot["source_updated_at"]
        check.observed_at = snapshot["observed_at"]
        check.records_count = sum(row["bin"] == bin_value for row in values)
        check.ingestion_run_id = run_id
        check.snapshot_hash = snapshot["snapshot_hash"]
        check.source_url = config["url"]
    session.flush()
    return {
        "inserted": inserted,
        "changed": changed,
        "unchanged": unchanged,
        "checked_bins": len(bins),
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
    scope_parcel_count: int | None = None,
    mapped_parcel_count: int | None = None,
) -> dict:
    """Pure response construction shared by all views and regression fixtures."""
    now = utc(now) or datetime.now(timezone.utc)
    warnings = list(warnings or [])
    check_map = {
        (row.get("source_system", SOURCE_SYSTEM), row["bin"]): row
        for row in map(_as_dict, checks)
    }
    record_rows = list(map(_as_dict, records))
    balance_rows = list(map(_as_dict, balances))
    # The source-authoritative BIN is the monetary and physical-building grain.
    unique_buildings = {row["bin"]: row for row in buildings}
    known_parcels = {row["bbl"] for row in buildings if row.get("bbl")}
    mapped_parcel_count = (
        len(known_parcels) if mapped_parcel_count is None else mapped_parcel_count
    )
    scope_parcel_count = (
        mapped_parcel_count if scope_parcel_count is None else scope_parcel_count
    )
    if not 0 <= mapped_parcel_count <= scope_parcel_count:
        raise ValueError(
            "Mapped parcel count must stay inside the requested parcel scope."
        )
    unmapped_parcel_count = scope_parcel_count - mapped_parcel_count
    identity_coverage_status = (
        "unavailable"
        if not identity_ready or not unique_buildings
        else "partial" if unmapped_parcel_count else "complete"
    )
    output = []
    reported_total = 0
    known_balance_bins = 0
    total_records = active_records = checked = complaints = open_complaints = 0
    for bin_value, building in sorted(
        unique_buildings.items(),
        key=lambda item: (item[1].get("address") or "", item[0]),
    ):
        check = check_map.get((SOURCE_SYSTEM, bin_value))
        registration = building.get("hpd_registration")
        if registration:
            registration = dict(registration)
            end_date = registration.get("registration_end_date")
            registration["status"] = "unknown"
            if end_date:
                end_date = date.fromisoformat(str(end_date)[:10])
                expired = end_date < now.astimezone(ZoneInfo("America/New_York")).date()
                registration["status"] = "expired" if expired else "unexpired"
                if expired:
                    warnings.append(
                        f"BIN {bin_value}: the latest saved HPD registration ended {end_date.isoformat()}. Verify current registration and company membership separately."
                    )
            if registration.get("current_record_count", 1) > 1:
                registration["status"] = "conflicting_current_records"
                warnings.append(
                    f"BIN {bin_value}: multiple current official HPD registration records require review. Displayed dates come from the latest saved record."
                )
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
            raw_payload = value.pop("raw_payload", {})
            if value.get("source_system") == dob_complaints.SOURCE_SYSTEM:
                value.update(dob_complaints.complaint_details(raw_payload))
            elif value.get("source_system") == dob_violations.SOURCE_SYSTEM:
                value.update(dob_violations.violation_details(raw_payload))
            elif value.get("source_system") == dob_ecb.SOURCE_SYSTEM:
                value.update(dob_ecb.ecb_details(raw_payload))
            record_check = check_map.get(
                (value.get("source_system", SOURCE_SYSTEM), bin_value)
            )
            value["stale"] = is_stale(
                value.get("source_updated_at"), value.get("observed_at"), now
            )
            value["present_in_latest_check"] = bool(
                record_check
                and value["ingestion_run_id"] == record_check["ingestion_run_id"]
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
            row.get("record_type") == "violation"
            and str(row.get("status") or "").lower() == "active"
            for row in bin_records
        )
        complaints += sum(row.get("record_type") == "complaint" for row in bin_records)
        open_complaints += sum(
            row.get("record_type") == "complaint"
            and str(row.get("status") or "").upper() == "ACTIVE"
            for row in bin_records
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
                "hpd_registration": registration,
                "records": sorted(
                    bin_records,
                    key=lambda row: str(
                        row.get("issue_date") or row.get("received_date") or ""
                    ),
                    reverse=True,
                ),
                "stale": stale,
                "source_check_status": "checked" if check else "not_checked",
                "source_updated_at": check.get("source_updated_at") if check else None,
                "observed_at": check.get("observed_at") if check else None,
                "source_checks": [
                    {
                        "source_system": source,
                        "status": "checked" if source_check else "not_checked",
                        "source_updated_at": (
                            source_check.get("source_updated_at")
                            if source_check
                            else None
                        ),
                        "observed_at": (
                            source_check.get("observed_at") if source_check else None
                        ),
                        "records_count": (
                            source_check.get("records_count", 0)
                            if source_check
                            else None
                        ),
                        "source_url": config["url"],
                        "stale": is_stale(
                            (
                                source_check.get("source_updated_at")
                                if source_check
                                else None
                            ),
                            source_check.get("observed_at") if source_check else None,
                            now,
                        ),
                    }
                    for source, config in SOURCE_CONFIG.items()
                    for source_check in [check_map.get((source, bin_value))]
                ],
                "reported_balance_cents": amount,
                "balance_observations": bin_balances,
                "interest_status": "unverified",
                "lien_status": "unverified",
            }
        )
    count = len(output)
    status = status_override or (
        "not_checked"
        if checked == 0
        else "complete" if checked == count and not unmapped_parcel_count else "partial"
    )
    source_times = [
        utc(row.get("source_updated_at"))
        for (source, bin_value), row in check_map.items()
        if source == SOURCE_SYSTEM
        and bin_value in unique_buildings
        and row.get("source_updated_at")
    ]
    observed_times = [
        utc(row.get("observed_at"))
        for (source, bin_value), row in check_map.items()
        if source == SOURCE_SYSTEM
        and bin_value in unique_buildings
        and row.get("observed_at")
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
    if unmapped_parcel_count:
        warnings.append(
            f"Physical-building identity covers {mapped_parcel_count} of {scope_parcel_count} scope parcels. {unmapped_parcel_count} parcels remain unmapped; building and compliance counts cover only mapped BINs."
        )
    if complaints:
        warnings.append(
            "DOB complaint categories and dispositions are source-coded investigation clues. The open dataset provides no complaint narrative, linked violation identifier, or balance; present conditions require separate verification."
        )
    if reported_total > 9_007_199_254_740_991:
        warnings.append(
            "Reported subtotal exceeds the exact API integer range; inspect individual balances."
        )
        reported_total = None
    source_coverage = []
    for source, config in SOURCE_CONFIG.items():
        source_checks = [
            row
            for (key, bin_value), row in check_map.items()
            if key == source and bin_value in unique_buildings
        ]
        source_records = [
            row
            for row in record_rows
            if row.get("source_system", SOURCE_SYSTEM) == source
            and row["bin"] in unique_buildings
        ]
        source_checked = len(source_checks)
        source_status = status_override or (
            "not_checked"
            if not source_checked
            else (
                "complete"
                if source_checked == count and not unmapped_parcel_count
                else "partial"
            )
        )
        publications = [
            utc(row["source_updated_at"])
            for row in source_checks
            if row.get("source_updated_at")
        ]
        observations = [
            utc(row["observed_at"]) for row in source_checks if row.get("observed_at")
        ]
        source_coverage.append(
            {
                "source_system": source,
                "source_url": config["url"],
                "status": source_status,
                "checked_building_count": source_checked,
                "physical_building_count": count,
                "records_count": len(source_records),
                "active_records_count": sum(
                    row.get("record_type") == "violation"
                    and str(row.get("status") or "").lower() == "active"
                    for row in source_records
                ),
                "open_complaints_count": sum(
                    row.get("record_type") == "complaint"
                    and str(row.get("status") or "").upper() == "ACTIVE"
                    for row in source_records
                ),
                "source_updated_at": min(publications) if publications else None,
                "observed_at": max(observations) if observations else None,
                "stale": source_checked < count
                or not source_checks
                or bool(unmapped_parcel_count)
                or any(
                    is_stale(row.get("source_updated_at"), row.get("observed_at"), now)
                    for row in source_checks
                )
                or any(
                    is_stale(row.get("source_updated_at"), row.get("observed_at"), now)
                    or row["ingestion_run_id"]
                    != check_map.get((source, row["bin"]), {}).get("ingestion_run_id")
                    for row in source_records
                ),
            }
        )
    return {
        "scope": scope,
        "enabled": enabled,
        "identity_ready": identity_ready,
        "as_of": max(observed_times) if observed_times else None,
        "source_updated_at": min(source_times) if source_times else None,
        "stale": not output
        or bool(unmapped_parcel_count)
        or any(row["stale"] for row in output),
        "coverage": {
            "status": status,
            "physical_building_count": count,
            "checked_building_count": checked,
            "records_count": total_records,
            "active_records_count": active_records,
            "complaints_count": complaints,
            "open_complaints_count": open_complaints,
            "scope_parcel_count": scope_parcel_count,
            "mapped_parcel_count": mapped_parcel_count,
            "unmapped_parcel_count": unmapped_parcel_count,
            "identity_coverage_status": identity_coverage_status,
            "balance_known_building_count": known_balance_bins,
            "missing_balance_bin_count": count - known_balance_bins,
        },
        "reported_balance_cents": reported_total if known_balance_bins else None,
        "estimated_penalty_cents": None,
        "warnings": list(dict.fromkeys(warnings)),
        "buildings": output,
        "source_coverage": source_coverage,
        "provenance": [
            {
                key: row[key]
                for key in (
                    "source_system",
                    "source_url",
                    "source_updated_at",
                    "observed_at",
                    "status",
                )
            }
            for row in source_coverage
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
        scope_parcel_count = int(
            (
                await session.execute(
                    text(
                        "SELECT COUNT(DISTINCT bbl) FROM building_management WHERE lead_id = :scope_id AND is_current = true"
                    ),
                    {"scope_id": scope_id},
                )
            ).scalar_one()
        )
    elif scope_type == "parcel":
        where = "p.bbl = :scope_id"
        scope_parcel_count = 1
    else:
        where = "b.bin = :scope_id"
        scope_parcel_count = None
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
    mapped_parcel_count = len({row["bbl"] for row in identities})
    if scope_parcel_count is None:
        scope_parcel_count = mapped_parcel_count
    if not identities:
        return build_response(
            **empty,
            identity_ready=False,
            status_override="identity_unavailable",
            scope_parcel_count=scope_parcel_count,
            mapped_parcel_count=0,
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
    registrations = await session.execute(
        text("""
        SELECT DISTINCT ON (bin) bin, registration_id, hpd_building_id,
            last_registration_date, registration_end_date, source_url,
            source_updated_at, last_seen_at AS observed_at,
            COUNT(*) OVER (PARTITION BY bin) AS current_record_count
        FROM hpd_registration_snapshots
        WHERE is_current = true AND identity_status = 'official_hpd' AND bin = ANY(:bins)
        ORDER BY bin, last_registration_date DESC NULLS LAST,
            registration_end_date DESC NULLS LAST, last_seen_at DESC, id DESC
        """),
        {"bins": bins},
    )
    for registration_row in registrations:
        registration = dict(registration_row._mapping)
        bin_value = registration.pop("bin")
        by_bin[bin_value]["hpd_registration"] = registration
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
                    ComplianceSourceCheck.source_system.in_(SOURCE_CONFIG),
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
        scope_parcel_count=scope_parcel_count,
        mapped_parcel_count=mapped_parcel_count,
    )
