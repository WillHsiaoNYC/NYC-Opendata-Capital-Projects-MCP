# tests/test_periods.py
from od_cpd.periods import is_cadence_period, resolve_current_period


def test_is_cadence_period():
    assert is_cadence_period("202601")
    assert is_cadence_period("202505")
    assert is_cadence_period("202509")
    assert not is_cadence_period("202602")   # Feb — off cadence
    assert not is_cadence_period("202310")   # Oct — off cadence
    assert not is_cadence_period("")
    assert not is_cadence_period("bogus")


def test_resolve_rejects_offcadence_and_undersized():
    # (period, row_count) — mirrors live qj5n
    counts = {
        "202409": 4799, "202501": 4937, "202505": 5097,
        "202509": 5372, "202601": 5529,
        "202602": 6,      # off-cadence + tiny
        "202310": 97,     # off-cadence
    }
    assert resolve_current_period(counts) == "202601"


def test_resolve_skips_partial_latest_cadence_period():
    # latest cadence period is present but under the 50%-of-median threshold
    counts = {"202501": 5000, "202505": 5100, "202509": 5200, "202601": 50}
    assert resolve_current_period(counts) == "202509"


def test_resolve_empty_returns_none():
    assert resolve_current_period({}) is None
