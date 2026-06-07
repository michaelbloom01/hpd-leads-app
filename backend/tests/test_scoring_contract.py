import pytest

from src.auth.auth import AuthUser
from src.routers import scoring as scoring_router
from src.tasks.score import _compute_raw_signal


class NoExecuteSession:
    def __init__(self):
        self.calls = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        raise AssertionError("scoring recalculation preview should not touch the database")

    async def commit(self):
        raise AssertionError("scoring recalculation preview should not commit")


def test_unknown_churn_signals_return_none_instead_of_zero():
    empty_row = {
        "sale_12mo": None,
        "sale_24mo": None,
        "refi_12mo": None,
        "complaints_6mo": None,
        "complaints_12mo": None,
        "violations_6mo": None,
        "violations_12mo": None,
        "permits_12mo": None,
        "permit_cost_12mo": None,
        "active_litigation": None,
        "recent_closed": None,
        "harassment_finding": None,
        "is_aep": None,
        "erp_12mo": None,
        "erp_amount_12mo": None,
        "eviction_filings_12mo": None,
    }

    assert _compute_raw_signal("ownership_change", empty_row) is None
    assert _compute_raw_signal("complaint_spike", empty_row) is None
    assert _compute_raw_signal("violation_trend", empty_row) is None
    assert _compute_raw_signal("dob_permits", empty_row) is None
    assert _compute_raw_signal("hpd_litigation", empty_row) is None
    assert _compute_raw_signal("emergency_repairs", empty_row) is None
    assert _compute_raw_signal("eviction_activity", empty_row) is None


@pytest.mark.anyio
async def test_scoring_recalculate_defaults_to_approval_preview_without_queueing():
    session = NoExecuteSession()

    response = await scoring_router.trigger_recalculate(
        session=session,
        user=AuthUser(user_id="u1", email="test@example.com"),
    )

    assert response["status"] == "approval_required"
    assert response["job_id"] is None
    assert response["approval_required"] is True
    assert response["safe_to_run_automatically"] is False
    assert response["mutations_planned"] == 0
    assert response["preview"]["operation"] == "scoring_recalculate"
    assert "confirm_execute=true" in response["preview"]["required_execute_query"]
    assert session.calls == []
