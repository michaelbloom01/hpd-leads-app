"""Known additive descendants satisfy truth schema presence, never activation."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.script import ScriptDirectory

from scripts.truth_migration_preflight import build_preflight_result
from src.services.truth_health import (
    EXPECTED_TRUTH_ALEMBIC_REVISION,
    REQUIRED_TRUTH_TABLES,
    VERIFIED_TRUTH_ALEMBIC_DESCENDANTS,
    build_activation_checklist,
    is_truth_schema_current,
    load_truth_schema_status,
    truth_revision_includes_expected,
)


class Result:
    def __init__(self, rows):
        self.rows = [SimpleNamespace(**row) for row in rows]

    def first(self):
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)


class ReadOnlySchemaSession:
    def __init__(self, revisions, missing_tables=(), alembic_exists=True):
        self.revisions = revisions
        self.missing_tables = set(missing_tables)
        self.alembic_exists = alembic_exists

    async def execute(self, statement, params=None):
        sql = str(statement)
        assert sql.strip().startswith("SELECT")
        if params:
            return Result([{"exists": params["table_name"] not in self.missing_tables}])
        if "to_regclass('alembic_version')" in sql:
            return Result([{"exists": self.alembic_exists}])
        assert "FROM alembic_version" in sql
        assert "LIMIT 1" not in sql
        return Result([{"version_num": value} for value in self.revisions])


@pytest.mark.parametrize(
    "revision",
    [EXPECTED_TRUTH_ALEMBIC_REVISION, *sorted(VERIFIED_TRUTH_ALEMBIC_DESCENDANTS)],
)
def test_verified_lineage_is_current_only_when_required_tables_exist(revision):
    status = asyncio.run(load_truth_schema_status(ReadOnlySchemaSession([revision])))
    assert status["ready"] is True
    assert status["expected_revision_applied"] is True
    assert is_truth_schema_current(status) is True
    assert status["current_revision"] == revision
    assert status["current_revisions"] == [revision]
    assert status["missing_tables"] == []
    assert status["mutations_planned"] == 0
    assert status["revision_status"] == (
        "expected"
        if revision == EXPECTED_TRUTH_ALEMBIC_REVISION
        else "verified_descendant"
    )


@pytest.mark.parametrize("missing_table", REQUIRED_TRUTH_TABLES)
@pytest.mark.parametrize("revision", sorted(VERIFIED_TRUTH_ALEMBIC_DESCENDANTS))
def test_verified_descendant_never_bypasses_table_presence(missing_table, revision):
    status = asyncio.run(
        load_truth_schema_status(
            ReadOnlySchemaSession([revision], [missing_table])
        )
    )
    assert status["migration_current"] is True
    assert status["ready"] is False
    assert status["missing_tables"] == [missing_table]
    assert is_truth_schema_current(status) is False


@pytest.mark.parametrize(
    "revisions",
    [
        [],
        ["008_lead_lineage"],
        ["009_truth_confidence_program"],
        ["010_future_truth_followup"],
        ["013_unknown"],
        ["012_compliance", "unrelated_branch"],
        ["010_truth_manifest", "012_compliance"],
    ],
)
def test_unknown_older_or_multiple_heads_fail_closed(revisions):
    status = asyncio.run(load_truth_schema_status(ReadOnlySchemaSession(revisions)))
    assert status["ready"] is True
    assert status["migration_current"] is False
    assert status["expected_revision_applied"] is False
    assert is_truth_schema_current(status) is False
    if len(revisions) > 1:
        assert status["current_revision"] is None
        assert status["revision_status"] == "multiple_heads_require_review"


def test_missing_alembic_table_does_not_pass_migration_gate():
    status = asyncio.run(
        load_truth_schema_status(ReadOnlySchemaSession([], alembic_exists=False))
    )
    assert status["alembic_table_exists"] is False
    assert is_truth_schema_current(status) is False


def test_verified_descendants_are_actual_descendants_in_repository():
    scripts = ScriptDirectory(str(Path(__file__).resolve().parents[1] / "alembic"))
    for revision in VERIFIED_TRUTH_ALEMBIC_DESCENDANTS:
        node = scripts.get_revision(revision)
        visited = set()
        while node.revision != EXPECTED_TRUTH_ALEMBIC_REVISION:
            assert node.revision not in visited
            visited.add(node.revision)
            assert isinstance(node.down_revision, str)
            node = scripts.get_revision(node.down_revision)
            assert node is not None
    assert not truth_revision_includes_expected("999_future")


def test_region_widening_is_narrow_and_downgrade_preserves_long_values(monkeypatch):
    import sqlalchemy as sa

    scripts = ScriptDirectory(str(Path(__file__).resolve().parents[1] / "alembic"))
    migration = scripts.get_revision("013_contact_region_text").module
    assert migration.down_revision == "012_compliance"
    calls = []
    monkeypatch.setattr(migration.op, "alter_column", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(migration.op, "execute", lambda sql: calls.append(str(sql)))
    migration.upgrade()
    assert len(calls) == 1
    assert calls[0][0] == ("building_contacts", "business_state")
    assert isinstance(calls[0][1]["type_"], sa.Text)
    calls.clear()
    migration.downgrade()
    assert "length(business_state) > 5" in calls[0]
    assert "RAISE EXCEPTION" in calls[0]
    assert calls[1][0] == ("building_contacts", "business_state")
    assert calls[1][1]["type_"].length == 5


@pytest.mark.parametrize("revision", sorted(VERIFIED_TRUTH_ALEMBIC_DESCENDANTS))
def test_recognized_descendant_does_not_activate_truth_business_use(revision):
    schema = asyncio.run(
        load_truth_schema_status(ReadOnlySchemaSession([revision]))
    )
    checklist = build_activation_checklist(
        schema_status=schema,
        summary={
            "trust_posture": "not_ready",
            "claim_count": 0,
            "verified_claim_count": 0,
        },
    )
    steps = {step["step"]: step for step in checklist}
    assert steps["apply_truth_schema"]["status"] == "complete"
    assert steps["execute_truth_materialization"]["status"] == "blocked"
    assert steps["allow_business_use"]["status"] == "blocked"


def preflight_inputs(heads="012_compliance (head)\n"):
    command = {
        "ok": True,
        "stdout": "BEGIN; COMMIT;",
        "stderr": "",
        "command": "offline SQL preview",
    }
    return {
        "schema_status": {
            "ready": False,
            "current_revision": "008_lead_lineage",
            "missing_tables": REQUIRED_TRUTH_TABLES,
        },
        "current_result": {"ok": True, "stdout": "008_lead_lineage\n", "stderr": ""},
        "heads_result": {"ok": True, "stdout": heads, "stderr": ""},
        "sql_result": dict(command),
        "rollback_sql_result": dict(command),
    }


@pytest.mark.parametrize(
    "head",
    [EXPECTED_TRUTH_ALEMBIC_REVISION, *sorted(VERIFIED_TRUTH_ALEMBIC_DESCENDANTS)],
)
def test_preflight_accepts_verified_repo_descendant_without_expanding_target(head):
    result = build_preflight_result(**preflight_inputs(f"{head} (head)\n"))
    assert result["ready_to_apply_additive_truth_migration"] is True
    assert result["expected_revision"] == EXPECTED_TRUTH_ALEMBIC_REVISION
    assert result["approval_required"] is True
    assert result["mutations_planned"] == 0


@pytest.mark.parametrize(
    "heads",
    ["013_unknown (head)\n", "012_compliance (head)\nunrelated_branch (head)\n", ""],
)
def test_preflight_rejects_unverified_or_multiple_repo_heads(heads):
    assert (
        build_preflight_result(**preflight_inputs(heads))[
            "ready_to_apply_additive_truth_migration"
        ]
        is False
    )


@pytest.mark.parametrize(
    "command", ["current_result", "heads_result", "sql_result", "rollback_sql_result"]
)
def test_preflight_requires_successful_read_only_commands(command):
    inputs = preflight_inputs()
    inputs[command]["ok"] = False
    assert (
        build_preflight_result(**inputs)["ready_to_apply_additive_truth_migration"]
        is False
    )


def test_preflight_cannot_reapply_or_offer_truth_only_rollback_at_descendant():
    inputs = preflight_inputs()
    inputs["schema_status"] = asyncio.run(
        load_truth_schema_status(ReadOnlySchemaSession(["012_compliance"]))
    )
    inputs["current_result"]["stdout"] = "012_compliance (head)\n"
    result = build_preflight_result(**inputs)
    assert result["ready_to_apply_additive_truth_migration"] is False
    assert (
        "separate rollback and evidence-retention plans" in result["rollback_strategy"]
    )
