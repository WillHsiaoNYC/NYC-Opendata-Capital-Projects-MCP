# tests/test_inspect.py
import duckdb
from od_cpd import schema, materialize
from od_cpd.tools.inspect import get_project_schedule_from, get_project_budget_from
from tests.test_materialize_normalized import _raw


def test_get_schedule_lists_linked_budgets_fanout():
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = get_project_schedule_from(con, "101")
    assert r["answer"]["lifecycle_status"] == "in_progress"
    assert r["anchor"] == {"type": "schedule", "id": "101"}
    assert len(r["linked_budgets"]) == 2
    assert "many-to-many" in r["caveat"] or "fans out" in r["caveat"]
    assert r["answer"]["period_variance_days"]["direction"] == "later"
    assert r["answer"]["sponsor_agency"] == "DDC"   # owner exposed alongside managing agency


def test_get_schedule_provenance_sql_matches_interpolated_form():
    # reproduce_sql now routes through interpolate_sql (quote-safe), matching the
    # parameterized query actually executed — not a raw f-string.
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = get_project_schedule_from(con, "101")
    assert r["provenance"]["reproduce_sql"] == \
        "SELECT * FROM latest_project_state WHERE pid = '101'"


def test_get_schedule_exposes_sponsor_when_managed_by_other():
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = get_project_schedule_from(con, "102")   # DDC-managed, DPR-sponsored
    assert r["answer"]["agency"] == "DDC"
    assert r["answer"]["sponsor_agency"] == "DPR"


def test_get_budget_lists_linked_schedules():
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = get_project_budget_from(con, "A", "DDC")
    assert r["anchor"]["type"] == "budget"
    assert any(s.get("pid") == "101" for s in r["linked_schedules"])


def test_schedule_answer_carries_borough_and_list():
    con = duckdb.connect(":memory:"); _raw(con)
    con.executemany(
        "INSERT INTO raw_project_detail (reporting_period, managing_agency, sponsor_agency,"
        " pid, fms_id, total_budget, current_phase, borough) VALUES (?,?,?,?,?,?,?,?)",
        [["202601", "DPR", "DPR", "401", "J1", "10", "Construction", "Brooklyn"],
         ["202601", "DPR", "DPR", "401", "J2", "10", "Construction", "Bronx"]])
    materialize.materialize_all(con)
    r = get_project_schedule_from(con, "401")
    assert r["answer"]["borough"] == "Multiple"
    assert r["answer"]["boroughs"] == ["Bronx", "Brooklyn"]


from od_cpd.tools.inspect import get_project_history_from


def _history_con():
    con = duckdb.connect(":memory:"); _raw(con)
    # a second, earlier period for PID 101 so history has a trajectory
    con.execute(
        "INSERT INTO raw_project_detail (reporting_period, managing_agency, sponsor_agency,"
        " pid, fms_id, total_budget, current_phase, borough, agency_project_name) "
        "VALUES ('202509','DDC','DDC','101','A','90','Design','K','Park A')")
    con.execute(
        "INSERT INTO raw_schedule_history (reporting_period, managing_agency, pid,"
        " current_phase, completion_date, completion_date_type, variance_day) "
        "VALUES ('202509','DDC','101','Design','2026-11-01','Forecast','-10')")
    materialize.materialize_all(con)
    return con


def test_history_pid_period_by_period():
    r = get_project_history_from(_history_con(), pid="101")
    assert [p["reporting_period"] for p in r["periods"]] == ["202509", "202601"]
    assert r["periods"][0]["variance_days"] == {"value": -10, "direction": "earlier"}
    assert r["periods"][1]["variance_days"] == {"value": 45, "direction": "later"}
    assert r["current_state"]["reporting_period"] == "202601"
    assert r["current_state"]["cumulative_variance_days"]["value"] == 35
    assert r["anchor"] == {"type": "schedule", "id": "101"}
    assert len(r["linked_budgets"]) == 2          # A and B at the latest link period


def test_history_pid_artifact_rows_kept_and_marked():
    con = duckdb.connect(":memory:"); _raw(con)
    con.execute(
        "INSERT INTO raw_project_detail (reporting_period, managing_agency, sponsor_agency,"
        " pid, fms_id, total_budget, current_phase, borough) "
        "VALUES ('202601','DDC','DDC','777','G','10','Design','K')")
    con.execute(
        "INSERT INTO raw_schedule_history (reporting_period, managing_agency, pid,"
        " current_phase, completion_date, completion_date_type, variance_day) "
        "VALUES ('202601','DDC','777','Design','2028-01-01','Forecast','-364938')")
    materialize.materialize_all(con)
    r = get_project_history_from(con, pid="777")
    assert len(r["periods"]) == 1                  # kept, not dropped
    assert r["periods"][0]["variance_artifact"] is True


def test_history_requires_exactly_one_anchor():
    con = _history_con()
    assert "error" in get_project_history_from(con)
    assert "error" in get_project_history_from(con, pid="101", fms_id="A")


def test_history_unknown_pid_errors():
    assert "error" in get_project_history_from(_history_con(), pid="999")


def test_history_fms_period_series_signed():
    r = get_project_history_from(_history_con(), fms_id="a")   # case-insensitive
    assert len(r["lines"]) == 1
    line = r["lines"][0]
    assert line["fms_id"] == "A" and line["managing_agency"] == "DDC"
    assert [p["reporting_period"] for p in line["periods"]] == ["202509", "202601"]
    assert line["periods"][1]["total_budget"] == 100.0
    assert line["periods"][1]["budget_variance"]["direction"] in (
        "increased", "decreased", "unchanged")
    assert r["anchor"]["type"] == "budget"


def test_history_fms_adoption_only_line_is_header_only():
    con = duckdb.connect(":memory:"); _raw(con)
    # adoption record only (NULL spend_to_date), no snapshots
    con.execute(
        "INSERT INTO raw_budget_history (managing_agency, fms_id, year_month_reported,"
        " total_budget, spend_to_date, budget_variance) "
        "VALUES ('DPR','ZZ','201903','500',NULL,NULL)")
    materialize.materialize_all(con)
    r = get_project_history_from(con, fms_id="ZZ")
    assert "error" not in r
    line = r["lines"][0]
    assert line["periods"] == []
    assert line["original_budget"]["recorded_period"] == "201903"
    assert "Adoption-only" in line["note"]


def test_history_fms_unknown_errors():
    assert "error" in get_project_history_from(_history_con(), fms_id="NOPE")


def test_schedule_and_history_surface_forecast_past_due():
    con = _history_con()
    assert get_project_schedule_from(con, "101")["answer"]["forecast_past_due"] is False
    r = get_project_history_from(con, pid="101")
    assert r["current_state"]["forecast_past_due"] is False
