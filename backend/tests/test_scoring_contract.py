from src.tasks.score import _compute_raw_signal


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
