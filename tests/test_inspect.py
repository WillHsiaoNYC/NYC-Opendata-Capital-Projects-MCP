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
