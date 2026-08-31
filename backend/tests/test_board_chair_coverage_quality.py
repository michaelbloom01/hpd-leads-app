"""Coverage buckets reconcile while registry-name availability stays separate."""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

from src.db.session import get_compatible_sync_url
from src.routers.quality import board_chair_coverage

OUTCOMES = (
    "current_exact_chair",
    "stale_exact_chair",
    "ambiguous_or_possible",
    "exact_entity_without_chair",
    "no_named_chair_match",
    "not_loaded",
    "unclassified_cached",
)


def coverage_from_counts(**overrides):
    counts = {
        "total_buildings": 181132,
        "eligible_buildings": 15733,
        **dict.fromkeys(OUTCOMES, 0),
        "not_loaded": 15728,
        "unclassified_cached": 5,
        "cached_eligible_buildings": 5,
        "cached_with_ceo_name": 5,
        "unclassified_cached_with_ceo_name": 5,
        **overrides,
    }

    class Session:
        calls = 0

        async def execute(self, _statement):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(first=lambda: SimpleNamespace(_mapping=counts))
            return SimpleNamespace(scalar=lambda: 100)

    return asyncio.run(board_chair_coverage(session=Session()))


def test_five_legacy_caches_reconcile_and_expose_names_without_title_claim():
    response = coverage_from_counts()
    assert response["outcome_total"] == response["eligible_buildings"] == 15733
    assert response["outcomes_reconcile"]
    assert response["unclassified_cached"] == 5
    assert response["classified_cached_buildings"] == 0
    assert response["cache_classification_coverage"] == 0.0
    assert response["candidate_availability"]["cached_with_ceo_name"] == 5
    assert response["candidate_availability"]["unclassified_cached_with_ceo_name"] == 5
    assert response["current_exact_chair"] == 0
    assert response["explicit_current_board_role_coverage"] is None
    assert response["explicit_current_board_role_status"] == "not_measured"
    assert "unmeasured" in response["reliability_policy"]["candidate_availability"]
    assert not response["hpd_head_officer_included_in_chair_coverage"]


def test_one_classified_refresh_moves_one_bucket_without_changing_name_availability():
    response = coverage_from_counts(
        current_exact_chair=1,
        unclassified_cached=4,
        unclassified_cached_with_ceo_name=4,
    )
    assert response["outcomes_reconcile"]
    assert response["classified_cached_buildings"] == 1
    assert response["cache_classification_coverage"] == 0.2
    assert response["candidate_availability"]["cached_with_ceo_name"] == 5
    assert response["explicit_current_board_role_coverage"] is None


def test_denominator_mismatch_is_exposed():
    response = coverage_from_counts(unclassified_cached=4)
    assert response["outcome_total"] == 15732
    assert response["outcomes_reconcile"] is False


def test_no_cached_candidates_have_unknown_classification_rate():
    response = coverage_from_counts(
        not_loaded=15733,
        unclassified_cached=0,
        cached_eligible_buildings=0,
        cached_with_ceo_name=0,
        unclassified_cached_with_ceo_name=0,
    )
    assert response["outcomes_reconcile"]
    assert response["cache_classification_coverage"] is None
    assert response["candidate_availability"]["cached_with_ceo_name"] == 0
    assert response["explicit_current_board_role_coverage"] is None


@pytest.fixture
def postgres_connection():
    if os.environ.get("RUN_POSTGRES_INTEGRATION") != "1":
        pytest.skip(
            "Requires the explicit disposable PostgreSQL integration environment"
        )
    engine = create_engine(get_compatible_sync_url())
    assert engine.url.host in {"localhost", "127.0.0.1"}
    assert engine.url.database == "hpd_leads_test"
    connection = engine.connect()
    transaction = connection.begin()
    # Connection-local tables shadow the application tables. Fixture data never
    # touches persisted application rows or another test connection.
    connection.execute(text("""
        CREATE TEMP TABLE buildings (bbl text PRIMARY KEY, building_type text, building_class text) ON COMMIT DROP;
        CREATE TEMP TABLE dos_cache (cache_key text PRIMARY KEY, result text, cached_at timestamptz) ON COMMIT DROP;
        CREATE TEMP TABLE building_contacts (bbl text, contact_type text) ON COMMIT DROP;
    """))
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()


def read_postgres(connection):
    class Session:
        async def execute(self, statement):
            return connection.execute(statement)

    return asyncio.run(board_chair_coverage(session=Session()))


def test_postgres_legacy_cache_denominator_and_one_scoped_refresh(postgres_connection):
    connection = postgres_connection
    connection.execute(text("""
        INSERT INTO buildings (bbl, building_type)
        SELECT (1000000000 + i)::text, 'COOP' FROM generate_series(1, 15733) AS i;
        INSERT INTO dos_cache (cache_key, result, cached_at)
        SELECT 'officers:' || (1000000000 + i), jsonb_build_object('ceo_name', 'Synthetic person ' || i)::text, NOW()
        FROM generate_series(1, 5) AS i;
    """))
    response = read_postgres(connection)
    assert response["eligible_buildings"] == 15733
    assert response["not_loaded"] == 15728
    assert response["unclassified_cached"] == response["cached_with_ceo_name"] == 5
    assert response["outcomes_reconcile"]
    assert sum(response[key] for key in OUTCOMES) == 15733
    connection.execute(text("""
        UPDATE dos_cache SET result = (result::jsonb || '{"entity_match_status":"exact","chair_status":"named_chair"}'::jsonb)::text
        WHERE cache_key='officers:1000000001'
    """))
    response = read_postgres(connection)
    assert response["current_exact_chair"] == 1
    assert response["unclassified_cached"] == 4
    assert response["cached_with_ceo_name"] == 5
    assert response["outcomes_reconcile"]


def test_postgres_outcomes_are_exclusive_and_malformed_names_stay_unclassified(
    postgres_connection,
):
    connection = postgres_connection
    fixtures = [
        ({"entity_match_status": "exact", "ceo_name": "Current Candidate"}, 1),
        ({"entity_match_status": "exact", "ceo_name": "Stale Candidate"}, 31),
        ({"entity_match_status": "possible", "ceo_name": "Possible Candidate"}, 1),
        ({"entity_match_status": "ambiguous", "chair_status": "exact_no_chair"}, 1),
        ({"entity_match_status": "exact", "chair_status": "exact_no_chair"}, 1),
        ({"chair_status": "no_match"}, 1),
        ({"ceo_name": "Legacy Candidate"}, 1),
        ({}, 1),
        ({"entity_match_status": "exact", "ceo_name": "Undated Candidate"}, None),
        ({"entity_match_status": "exact", "ceo_name": {"name": "Wrong Type"}}, 1),
        (
            {
                "entity_match_status": "exact",
                "ceo_name": "Conflicting Candidate",
                "chair_status": "no_match",
            },
            1,
        ),
        ({"ceo_name": "   "}, 1),
    ]
    connection.execute(text("""
        INSERT INTO buildings (bbl, building_class)
        SELECT (1000000000 + i)::text, 'R4' FROM generate_series(1, 14) AS i;
        INSERT INTO buildings (bbl, building_class) VALUES ('2000000001', 'F1');
        INSERT INTO dos_cache (cache_key, result, cached_at)
        VALUES ('officers:1000000013', NULL, NOW()),
               ('officers:officers:1000000001', '{"ceo_name":"Duplicate-looking key"}', NOW()),
               ('officers:2000000001', '{"ceo_name":"Outside eligible scope"}', NOW());
        INSERT INTO building_contacts (bbl, contact_type)
        VALUES ('1000000001', 'HeadOfficer'), ('1000000001', 'HeadOfficer'), ('2000000001', 'HeadOfficer');
    """))
    for index, (payload, age) in enumerate(fixtures, 1):
        connection.execute(
            text(
                "INSERT INTO dos_cache (cache_key, result, cached_at) VALUES (:key, :result, :cached_at)"
            ),
            {
                "key": f"officers:{1000000000 + index}",
                "result": json.dumps(payload),
                "cached_at": (
                    None
                    if age is None
                    else datetime.now(timezone.utc) - timedelta(days=age)
                ),
            },
        )
    response = read_postgres(connection)
    assert response["total_buildings"] == 15
    assert response["eligible_buildings"] == 14
    assert response["current_exact_chair"] == response["stale_exact_chair"] == 1
    assert response["ambiguous_or_possible"] == 2
    assert (
        response["exact_entity_without_chair"] == response["no_named_chair_match"] == 1
    )
    assert response["not_loaded"] == 2
    assert response["unclassified_cached"] == 6
    assert response["cached_eligible_buildings"] == 12
    assert response["cached_with_ceo_name"] == 6
    assert response["unclassified_cached_with_ceo_name"] == 3
    assert response["outcomes_reconcile"]
    assert response["hpd_head_officer_proxy"] == 1
    assert response["explicit_current_board_role_coverage"] is None
