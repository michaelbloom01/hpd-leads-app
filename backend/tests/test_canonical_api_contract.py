from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.auth.auth import AuthUser
from src.routers import canonical as canonical_router
from src.routers import leads as leads_router
from src.routers import quality as quality_router


class FakeExecuteResult:
    def __init__(self, *, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def __iter__(self):
        for row in self._rows:
            yield SimpleNamespace(_mapping=row, **row)

    def first(self):
        if not self._rows:
            return None
        row = self._rows[0]
        return SimpleNamespace(_mapping=row, **row)

    def scalar(self):
        return self._scalar


class FakeAsyncSession:
    def __init__(self, results):
        self._results = list(results)
        self.calls: list[tuple[str, dict | None]] = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        if not self._results:
            raise AssertionError("Unexpected execute call")
        return self._results.pop(0)


@pytest.mark.anyio
async def test_list_canonical_entities_returns_materialized_counts():
    session = FakeAsyncSession(
        [
            FakeExecuteResult(
                rows=[
                    {
                        "canonical_entity_id": "entity-1",
                        "normalized_name": "ACME MANAGEMENT",
                        "display_name": "Acme Management",
                        "entity_type": "pm_company",
                        "status": "proposed",
                        "confidence_score": 0.91,
                        "updated_at": None,
                        "lead_count": 2,
                        "building_count": 5,
                        "proposal_count": 3,
                        "safe_proposal_count": 1,
                    }
                ]
            ),
            FakeExecuteResult(scalar=1),
        ]
    )

    response = await canonical_router.list_canonical_entities(
        limit=50,
        offset=0,
        status=None,
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["total"] == 1
    assert response["entities"][0]["canonical_entity_id"] == "entity-1"
    assert response["entities"][0]["lead_count"] == 2
    assert response["entities"][0]["safe_proposal_count"] == 1


@pytest.mark.anyio
async def test_get_canonical_entity_serializes_nested_json():
    session = FakeAsyncSession(
        [
            FakeExecuteResult(
                rows=[
                    {
                        "canonical_entity_id": "entity-1",
                        "normalized_name": "ACME MANAGEMENT",
                        "display_name": "Acme Management",
                        "entity_type": "pm_company",
                        "status": "proposed",
                        "confidence_score": 0.91,
                        "profile": json.dumps({"bucket": "safe_keep"}),
                        "updated_at": None,
                    }
                ]
            ),
            FakeExecuteResult(rows=[{"alias_name": "Acme Mgmt", "normalized_alias": "ACME MGMT", "source": "prep", "confidence_score": 0.75}]),
            FakeExecuteResult(rows=[{"lead_id": "lead-1", "relationship_type": "keeper", "is_primary": True, "confidence_score": 1.0, "evidence": json.dumps({"current_link_count": 3})}]),
            FakeExecuteResult(rows=[{"bbl": "1000000001", "source": "prep", "confidence_score": 0.7, "evidence": json.dumps({"bucket": "safe_keep"})}]),
            FakeExecuteResult(rows=[{"id": 1, "proposal_key": "entity-1:lead-1:safe_keep", "lead_id": "lead-1", "bucket": "safe_keep", "proposal_status": "proposed", "safe_to_execute": True, "reasons": json.dumps({"blocked_reasons": []}), "evidence": json.dumps({"keeper_lead_id": "lead-1"}), "updated_at": None}]),
        ]
    )

    response = await canonical_router.get_canonical_entity(
        canonical_entity_id="entity-1",
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["entity"]["profile"] == {"bucket": "safe_keep"}
    assert response["leads"][0]["is_primary"] is True
    assert response["leads"][0]["evidence"] == {"current_link_count": 3}
    assert response["buildings"][0]["evidence"] == {"bucket": "safe_keep"}
    assert response["proposals"][0]["safe_to_execute"] is True


@pytest.mark.anyio
async def test_list_canonical_proposals_parses_json_fields():
    session = FakeAsyncSession(
        [
            FakeExecuteResult(
                rows=[
                    {
                        "id": 1,
                        "proposal_key": "entity-1:lead-1:safe_keep",
                        "canonical_entity_id": "entity-1",
                        "lead_id": "lead-1",
                        "bucket": "safe_keep",
                        "proposal_status": "proposed",
                        "safe_to_execute": True,
                        "reasons": json.dumps({"canonical_reasons": ["same_name"]}),
                        "evidence": json.dumps({"candidate_lead_count": 2}),
                        "updated_at": None,
                    }
                ]
            ),
            FakeExecuteResult(scalar=1),
        ]
    )

    response = await canonical_router.list_canonical_proposals(
        limit=50,
        offset=0,
        bucket=None,
        proposal_status=None,
        lead_id=None,
        safe_to_execute=None,
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["total"] == 1
    assert response["proposals"][0]["reasons"] == {"canonical_reasons": ["same_name"]}
    assert response["proposals"][0]["evidence"] == {"candidate_lead_count": 2}


@pytest.mark.anyio
async def test_get_canonical_lineage_summary_includes_memberships_aliases_and_proposals():
    session = FakeAsyncSession(
        [
            FakeExecuteResult(
                rows=[
                    {
                        "canonical_entity_id": "entity-1",
                        "normalized_name": "ACME MANAGEMENT",
                        "display_name": "Acme Management",
                        "status": "proposed",
                        "confidence_score": 0.91,
                        "relationship_type": "keeper",
                        "is_primary": True,
                        "membership_confidence": 1.0,
                        "evidence": json.dumps({"current_link_count": 3}),
                    }
                ]
            ),
            FakeExecuteResult(rows=[{"alias_name": "Acme Mgmt", "normalized_alias": "ACME MGMT", "source": "prep", "confidence_score": 0.75}]),
            FakeExecuteResult(rows=[{"bucket": "safe_keep", "proposal_status": "proposed", "safe_to_execute": True, "reasons": json.dumps({"blocked_reasons": []}), "evidence": json.dumps({"keeper_lead_id": "lead-1"}), "updated_at": None}]),
        ]
    )

    summary = await leads_router._get_canonical_lineage_summary(session, "lead-1")

    assert summary["primary_entity"]["canonical_entity_id"] == "entity-1"
    assert summary["primary_entity"]["aliases"][0]["alias_name"] == "Acme Mgmt"
    assert summary["memberships"][0]["evidence"] == {"current_link_count": 3}
    assert summary["proposals"][0]["safe_to_execute"] is True


@pytest.mark.anyio
async def test_load_canonical_materialization_stats_reports_bucket_counts():
    session = FakeAsyncSession(
        [
            FakeExecuteResult(
                rows=[
                    {
                        "entity_count": 3,
                        "alias_count": 6,
                        "lead_membership_count": 4,
                        "building_membership_count": 10,
                        "proposal_count": 5,
                        "safe_proposal_count": 2,
                    }
                ]
            ),
            FakeExecuteResult(
                rows=[
                    {"bucket": "review_required", "cnt": 3},
                    {"bucket": "safe_keep", "cnt": 2},
                ]
            ),
        ]
    )

    stats = await quality_router._load_canonical_materialization_stats(session)

    assert stats == {
        "entity_count": 3,
        "alias_count": 6,
        "lead_membership_count": 4,
        "building_membership_count": 10,
        "proposal_count": 5,
        "safe_proposal_count": 2,
        "bucket_counts": {"review_required": 3, "safe_keep": 2},
    }


@pytest.mark.anyio
async def test_load_truth_confidence_stats_is_schema_gated(monkeypatch):
    schema_status = {
        "ready": False,
        "current_revision": "008_lead_lineage",
        "expected_revision": "010_truth_manifest",
        "missing_tables": ["truth_claims", "truth_evidence"],
    }

    async def fake_schema_status(session):
        return schema_status

    monkeypatch.setattr(quality_router, "load_truth_schema_status", fake_schema_status)
    monkeypatch.setattr(quality_router, "is_truth_schema_current", lambda status: False)
    session = FakeAsyncSession([])

    stats = await quality_router._load_truth_confidence_stats(session)

    assert stats == {
        "claim_count": 0,
        "verified_claim_count": 0,
        "conflicting_claim_count": 0,
        "open_review_count": 0,
        "active_golden_case_count": 0,
        "actionability_distribution": {},
        "schema_status": schema_status,
    }
    assert session.calls == []
