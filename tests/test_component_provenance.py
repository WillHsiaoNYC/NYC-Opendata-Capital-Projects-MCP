import duckdb

from od_cpd import materialize
from od_cpd.tools.inspect import (get_project_budget_from, get_project_history_from,
                                  get_project_schedule_from)
from tests.test_inspect import _history_con
from tests.test_materialize_normalized import _raw


def test_schedule_components_reproduce_current_state_history_and_links():
    con = _history_con()
    history = get_project_history_from(con, pid="101")
    parts = history["provenance"]["components"]
    assert len(con.execute(parts["periods"]).fetchall()) == len(history["periods"])
    current = con.execute(parts["current_state"]).fetchone()
    assert current[:4] == ("202601", "Construction", "in_progress", 35)
    assert set(con.execute(parts["linked_budgets"]).fetchall()) == {("A", "DDC"), ("B", "DDC")}
    detail = get_project_schedule_from(con, "101")
    assert set(con.execute(detail["provenance"]["components"]["linked_budgets"]).fetchall()) == {
        (r["fms_id"], r["managing_agency"]) for r in detail["linked_budgets"]}


def test_budget_components_reproduce_each_holders_own_latest_links_and_adoption():
    con = duckdb.connect(":memory:")
    _raw(con)
    con.execute("INSERT INTO raw_project_detail (reporting_period, managing_agency, pid, fms_id, "
                "total_budget) VALUES ('202509', 'DPR', '601', 'A', '70')")
    con.executemany("INSERT INTO raw_budget_history (managing_agency, fms_id, year_month_reported, "
                    "total_budget, spend_to_date) VALUES ('DPR', 'A', ?, ?, ?)",
                    [["202509", "70", "7"], ["201803", "40", None]])
    materialize.materialize_all(con)
    history = get_project_history_from(con, fms_id="a")
    assert len(history["lines"]) == 2
    expected_links = {("101", "DDC"), ("601", "DPR")}
    parts = history["provenance"]["components"]
    assert set(con.execute(parts["linked_schedules"]).fetchall()) == expected_links
    assert len(con.execute(parts["periods"]).fetchall()) == 3
    assert con.execute(parts["original_budget"]).fetchone() == ("A", "DPR", "201803", 40)
    detail = get_project_budget_from(con, "A")
    assert {(r["pid"], r["managing_agency"]) for r in detail["linked_schedules"]} == expected_links
    for sql in detail["provenance"]["components"].values():
        con.execute(sql).fetchall()


def test_adoption_only_component_reproduces_headers_despite_empty_snapshot_query():
    con = duckdb.connect(":memory:")
    _raw(con)
    con.executemany("INSERT INTO raw_budget_history (managing_agency, fms_id, year_month_reported, "
                    "total_budget) VALUES (?, 'ZZ', '201803', ?)", [["DDC", "100"], ["DPR", "200"]])
    materialize.materialize_all(con)
    result = get_project_history_from(con, fms_id="ZZ")
    assert len(result["lines"]) == 2
    assert con.execute(result["provenance"]["reproduce_sql"]).fetchall() == []
    originals = con.execute(result["provenance"]["components"]["original_budget"]).fetchall()
    assert {(r[1], r[3]) for r in originals} == {("DDC", 100), ("DPR", 200)}


def test_history_rejects_budget_holder_for_pid_lens():
    result = get_project_history_from(None, pid="101", managing_agency="DDC")
    assert "applies only" in result["error"]
