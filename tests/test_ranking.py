# tests/test_ranking.py
import duckdb
from od_cpd import schema, materialize
from od_cpd.tools.ranking import rank_projects_from
from tests.test_materialize_normalized import _raw, _orig_budget_fixture
from tests.test_agency_scope import _scope_db


def test_rank_schedule_by_period_variance_excludes_null():
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = rank_projects_from(con, entity="schedule", rank_by="period_variance_days", n=10)
    assert r["ranked_entity"] == "schedule"
    assert r["rows"][0]["pid"] == "101"           # only PID with non-null variance (45)
    assert r["rows"][0]["period_variance_days"]["direction"] == "later"


def test_rank_requires_native_metric():
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = rank_projects_from(con, entity="schedule", rank_by="total_budget", n=5)
    assert "error" in r   # budget metric not native to schedule entity


def test_rank_budget_sponsor_scope_finds_builder_held_line():
    con = _scope_db(duckdb.connect(":memory:"))
    r = rank_projects_from(con, entity="budget", rank_by="total_budget", agency="DOC")
    fms = [row["fms_id"] for row in r["rows"]]
    assert fms[0] == "J2"                       # $9000 DDC-held but DOC-owned, ranks first
    assert set(fms) == {"J1", "J2"}
    assert r["agency_scope"]["role"] == "sponsor"
    assert r["rows"][0]["sponsor_agencies"] == ["DOC"]   # full owner set carried per row


def test_rank_budget_managing_scope_excludes_delegated():
    con = _scope_db(duckdb.connect(":memory:"))
    r = rank_projects_from(con, entity="budget", rank_by="total_budget",
                           agency="DOC", agency_role="managing")
    assert {row["fms_id"] for row in r["rows"]} == {"J1"}   # only DOC-held


def test_rank_schedule_agency_scope():
    con = _scope_db(duckdb.connect(":memory:"))
    r = rank_projects_from(con, entity="schedule", rank_by="period_variance_days",
                           agency="DDC", n=50)
    assert r["agency_scope"]["role"] == "managing"


def test_rank_budgets_by_cumulative_change_uses_original():
    con = duckdb.connect(":memory:"); _raw(con); _orig_budget_fixture(con)
    materialize.materialize_all(con)
    r = rank_projects_from(con, entity="budget", rank_by="cumulative_budget_change", n=3)
    assert r["ranked_entity"] == "budget"
    top = r["rows"][0]
    # F: latest 1.3M - adopted 1.0M = +300k, the biggest lifetime growth in the fixture
    assert top["fms_id"] == "F"
    assert top["cumulative_budget_change"]["value"] == 300000.0
    assert top["cumulative_budget_change"]["direction"] == "increased"
