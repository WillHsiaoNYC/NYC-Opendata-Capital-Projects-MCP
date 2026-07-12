# tests/test_common.py
import duckdb

from od_cpd import materialize
from od_cpd.tools._common import current_period, direction_of, signed_metric, mm_envelope
from od_cpd.tools.schedule import schedule_breakdown_from
from tests.test_materialize_normalized import _raw


def _fragment_period_db():
    """202601 full (via _raw) + a 202605 fragment: one stray row of a mid-publish period."""
    con = duckdb.connect(":memory:"); _raw(con)
    con.execute(
        "INSERT INTO raw_schedule_history (reporting_period, managing_agency, pid,"
        " current_phase, completion_date, completion_date_type, variance_day) "
        "VALUES ('202605','DDC','999','Design','2028-01-01','Forecast','0')")
    # pad 202601 so its count clearly dominates the median-based fullness threshold
    con.executemany(
        "INSERT INTO raw_schedule_history (reporting_period, managing_agency, pid,"
        " current_phase, completion_date, completion_date_type, variance_day) VALUES (?,?,?,?,?,?,?)",
        [["202601", "DDC", str(900 + i), "Design", "2028-01-01", "Forecast", "0"]
         for i in range(4)])
    materialize.materialize_all(con)
    return con


def test_current_period_skips_partial_latest_period():
    con = _fragment_period_db()
    # bare max() would say 202605; the fullness guard (the same rule that stamps
    # meta.latest_reporting_period) must fall back to the last full period.
    assert current_period(con, "schedule_history") == "202601"


def test_schedule_breakdown_current_uses_full_period():
    r = schedule_breakdown_from(_fragment_period_db(), group_by="managing_agency")
    assert r["period"] == "202601"


def test_direction_of():
    assert direction_of(45) == "later"
    assert direction_of(-3) == "earlier"
    assert direction_of(0) == "unchanged"
    assert direction_of(None) is None


def test_signed_metric():
    assert signed_metric(45) == {"value": 45, "direction": "later"}
    assert signed_metric(-2, kind="budget") == {"value": -2, "direction": "decreased"}
    assert signed_metric(None) == {"value": None, "direction": None}


def test_mm_envelope_count_one_light_caveat():
    env = mm_envelope(anchor_type="schedule", anchor_id="101",
                      linked=[{"fms_id": "ABC", "managing_agency": "DDC"}])
    assert env["anchor"] == {"type": "schedule", "id": "101"}
    assert env["linked_budgets"] == [{"fms_id": "ABC", "managing_agency": "DDC"}]
    assert "1:1" in env["caveat"]


def test_mm_envelope_fanout_lists_all():
    env = mm_envelope(anchor_type="budget", anchor_id="ABC",
                      linked=[{"pid": "1"}, {"pid": "2"}, {"pid": "3"}])
    assert env["linked_schedules"] == [{"pid": "1"}, {"pid": "2"}, {"pid": "3"}]
    assert "3" in env["caveat"]
