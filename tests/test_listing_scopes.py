import duckdb
import pytest

from od_cpd import materialize, schema
from od_cpd.tools.inspect import get_project_schedule_from
from od_cpd.tools.portfolio import project_portfolio_from
from od_cpd.tools.ranking import rank_projects_from
from tests.test_portfolio import _portfolio_db


def _listing_db():
    con = duckdb.connect(":memory:")
    schema.apply_schema(con)
    detail, schedule, budgets = [], [], []
    for period in ("202501", "202505", "202509", "202601"):
        for i in range(1, 11):
            fid = ("RD-CURRENT" if period == "202601" else "LB-OLD") if i == 1 else f"Z{i}"
            owner = "DOT" if i == 1 else "DEP"
            detail.append([period, "DDC", owner, str(i), fid, "100", "Construction"])
            schedule.append([period, "DDC", str(i), "5" if period == "202601" else "1"])
            budgets.append(["DDC", fid, period, "100", "0"])
    # The old project remains in the latest-known inventory, absent from current.
    detail.append(["202509", "DPR", "DPR", "99", "LEGACY", "90", "Construction"])
    schedule.append(["202509", "DPR", "99", "30"])
    budgets.append(["DPR", "LEGACY", "202509", "90", "0"])
    # A small newest publication must not replace the complete 202601 snapshot.
    detail.append(["202605", "DDC", "FDNY", "1", "NEW-FUTURE", "999", "Construction"])
    schedule.append(["202605", "DDC", "1", "50"])
    budgets += [["DDC", "NEW-FUTURE", "202605", "999", "0"],
                ["DDC", "Z2", "202605", "888", "0"]]
    con.executemany("INSERT INTO raw_project_detail (reporting_period, managing_agency, "
                    "sponsor_agency, pid, fms_id, total_budget, current_phase) VALUES (?,?,?,?,?,?,?)",
                    detail)
    con.executemany("INSERT INTO raw_schedule_history (reporting_period, managing_agency, pid, "
                    "variance_day) VALUES (?,?,?,?)", schedule)
    con.executemany("INSERT INTO raw_budget_history (managing_agency, fms_id, year_month_reported, "
                    "total_budget, spend_to_date) VALUES (?,?,?,?,?)", budgets)
    materialize.materialize_all(con)
    return con


@pytest.mark.parametrize("tool", [project_portfolio_from,
    lambda con, **kw: rank_projects_from(con, "schedule", "period_variance_days", **kw)])
def test_population_scope_reports_old_states_and_uses_the_complete_snapshot(tool):
    con = _listing_db()
    latest = tool(con, n=50)
    assert latest["current_period"] == "202601"
    rows = {r["pid"]: r for r in latest["rows"]}
    assert rows["99"]["reporting_period"] == "202509"
    assert rows["99"]["present_in_current_snapshot"] is False
    assert rows["1"]["reporting_period"] == "202605"
    assert rows["1"]["present_in_current_snapshot"] is True
    current = tool(con, n=50, population_scope="current")
    assert {r["pid"] for r in current["rows"]} == {str(i) for i in range(1, 11)}
    assert all(r["reporting_period"] == "202601" for r in current["rows"])
    assert all(r["present_in_current_snapshot"] for r in current["rows"])
    first = next(r for r in current["rows"] if r["pid"] == "1")
    assert first["period_variance_days"]["value"] == 5
    assert first["attributed_budget"] == 100


def test_budget_scope_uses_line_snapshot_values_and_presence():
    con = _listing_db()
    latest = rank_projects_from(con, "budget", "total_budget", n=50)
    known = {r["fms_id"]: r for r in latest["rows"]}
    assert known["Z2"]["reporting_period"] == "202605"
    assert known["Z2"]["present_in_current_snapshot"] is True
    assert known["Z2"]["total_budget"] == 888
    assert known["LEGACY"]["present_in_current_snapshot"] is False
    current = rank_projects_from(con, "budget", "total_budget", n=50,
                                 population_scope="current")
    assert current["current_period"] == "202601"
    assert len(current["rows"]) == 10
    assert all(r["reporting_period"] == "202601" and r["total_budget"] == 100
               for r in current["rows"])


def test_current_budget_display_name_does_not_leak_from_a_newer_partial_snapshot():
    con = _listing_db()
    con.execute("UPDATE raw_project_detail SET fms_project_name = 'Current name' WHERE fms_id='Z2'")
    con.execute("INSERT INTO raw_project_detail (reporting_period, managing_agency, fms_id, "
                "fms_project_name) VALUES ('202605', 'DDC', 'Z2', 'Future name')")
    materialize.materialize_all(con)
    for scope, expected in (("latest_known", "Future name"), ("current", "Current name")):
        result = rank_projects_from(con, "budget", "total_budget", n=50, population_scope=scope)
        assert next(r for r in result["rows"] if r["fms_id"] == "Z2")["fms_project_name"] == expected


def test_category_scope_defaults_to_selected_states_current_links():
    con = _listing_db()
    for scope in ("latest_known", "current"):
        for tool in (project_portfolio_from,
                     lambda con, **kw: rank_projects_from(con, "schedule", "period_variance_days", **kw)):
            assert tool(con, category="Library", population_scope=scope)["rows"] == []
            historical = tool(con, category="Library", category_scope="all_history",
                              population_scope=scope)
            assert {r["pid"] for r in historical["rows"]} == {"1"}
    assert {r["fms_id"] for r in get_project_schedule_from(con, "1")["linked_budgets"]} == {"NEW-FUTURE"}
    current = project_portfolio_from(con, category="Streets & Highways", population_scope="current")
    assert {r["pid"] for r in current["rows"]} == {"1"}
    assert project_portfolio_from(con, category="Streets & Highways")["rows"] == []


def test_current_cumulative_ranking_stops_at_selected_period_and_labels_the_basis():
    con = _listing_db()
    result = rank_projects_from(con, "schedule", "cumulative_variance_days", n=50,
                                population_scope="current")
    assert next(r for r in result["rows"] if r["pid"] == "1")["cumulative_variance_days"]["value"] == 8
    assert "cumulative" in result["label"].lower()
    assert "most-recent period" not in result["label"]


def test_portfolio_component_sql_reproduces_truncated_shared_line_summary():
    con = _portfolio_db()
    result = project_portfolio_from(con, category="Library", n=1)
    assert result["truncated"]
    parts = result["provenance"]["components"]
    assert len(con.execute(parts["rows"]).fetchall()) == 1
    summary = con.execute(parts["summary"]).fetchone()
    assert summary[:2] == (3, 550)
    assert con.execute(parts["line_budget_total"]).fetchone() == (350,)


@pytest.mark.parametrize("kwargs", [
    {"n": 0}, {"n": 501}, {"n": 1.5}, {"n": True},
    {"population_scope": "bad"}, {"category_scope": "bad"}, {"agency_role": "bad"},
])
def test_listing_validation_is_shared_by_direct_calls(kwargs):
    assert "error" in project_portfolio_from(None, **kwargs)
    assert "error" in rank_projects_from(None, "schedule", "period_variance_days", **kwargs)


def test_ranking_rejects_invalid_direction_without_touching_database():
    assert "error" in rank_projects_from(None, "budget", "total_budget", direction="sideways")


@pytest.mark.parametrize("entity, metric", [("budget", "total_budget"), ("schedule", "period_variance_days")])
def test_ranking_rejects_inverted_and_nonfinite_budget_bounds(entity, metric):
    for kwargs in ({"min_total_budget": 100, "max_total_budget": 10},
                   {"min_total_budget": float("nan")}, {"max_total_budget": "invalid"}):
        assert "error" in rank_projects_from(None, entity, metric, **kwargs)


def test_budget_ranking_rejects_schedule_link_category_scope():
    result = rank_projects_from(None, "budget", "total_budget", category_scope="all_history")
    assert "only to schedule" in result["error"]


def _changed_owner_db():
    con = _listing_db()
    con.executemany("INSERT INTO agency_dim (slug, aliases, cpdw_acronym, role_default) "
                    "VALUES (?, ?, ?, 'sponsor')", [[a.lower(), [a], a] for a in ("DOT", "DEP", "FDNY")])
    con.execute("UPDATE raw_project_detail SET fms_id='RD-CURRENT' "
                "WHERE reporting_period='202605' AND pid='1'")
    con.execute("UPDATE raw_budget_history SET fms_id='RD-CURRENT' "
                "WHERE year_month_reported='202605' AND fms_id='NEW-FUTURE'")
    con.executemany("INSERT INTO raw_project_detail (reporting_period, managing_agency, "
                    "sponsor_agency, pid, fms_id, total_budget) VALUES ('202601', ?, ?, ?, 'RD-CURRENT', ?)",
                    [["DDC", "DEP", "2", "100"], ["DPR", None, None, "77"]])
    con.execute("INSERT INTO raw_budget_history (managing_agency, fms_id, year_month_reported, "
                "total_budget, spend_to_date) VALUES ('DPR', 'RD-CURRENT', '202601', '77', '0')")
    materialize.materialize_all(con)
    return con


def test_current_budget_ownership_uses_selected_schedule_state_and_full_line_keys():
    con = _changed_owner_db()
    assert {r["pid"] for r in project_portfolio_from(con, agency="DOT", population_scope="current")["rows"]} == {"1"}
    current = rank_projects_from(con, "budget", "total_budget", agency="DOT", population_scope="current")
    assert [(r["managing_agency"], r["fms_id"], r["total_budget"]) for r in current["rows"]] == [
        ("DDC", "RD-CURRENT", 100)]
    assert current["rows"][0]["sponsor_agencies"] == ["DEP", "DOT"]
    assert current["ownership_reporting_period"] == "202601"
    assert rank_projects_from(con, "budget", "total_budget", agency="FDNY", population_scope="current")["rows"] == []
    # Latest-known ownership continues to follow the newer observed PID state.
    known = rank_projects_from(con, "budget", "total_budget", agency="FDNY")
    assert next(r for r in known["rows"] if r["managing_agency"] == "DDC")["sponsor_agencies"] == ["DEP", "FDNY"]
    owners = con.execute(current["provenance"]["components"]["sponsor_agencies"]).fetchall()
    assert owners == [("RD-CURRENT", "DDC", ["DEP", "DOT"])]


@pytest.mark.parametrize("newer_complete_source", ["budget", "schedule"])
def test_current_budget_ownership_reports_independently_selected_source_period(newer_complete_source):
    con = _changed_owner_db()
    if newer_complete_source == "budget":
        con.executemany("INSERT INTO raw_budget_history (managing_agency, fms_id, year_month_reported, "
                        "total_budget, spend_to_date) VALUES ('DDC', ?, '202605', '100', '0')",
                        [[f"Z{i}"] for i in range(3, 11)])
        agency, budget_period, ownership_period, amount = "DOT", "202605", "202601", 999
    else:
        con.executemany("INSERT INTO raw_project_detail (reporting_period, managing_agency, "
                        "sponsor_agency, pid, fms_id, total_budget) VALUES ('202605', 'DDC', 'DEP', ?, ?, '100')",
                        [[str(i), f"Z{i}"] for i in range(2, 11)])
        con.executemany("INSERT INTO raw_schedule_history (reporting_period, managing_agency, "
                        "pid, variance_day) VALUES ('202605', 'DDC', ?, '5')",
                        [[str(i)] for i in range(2, 11)])
        agency, budget_period, ownership_period, amount = "FDNY", "202601", "202605", 100
    con.execute("UPDATE raw_schedule_history SET variance_day='0' WHERE reporting_period='202605' AND pid='1'")
    materialize.materialize_all(con)
    result = rank_projects_from(con, "budget", "total_budget", agency=agency, population_scope="current")
    assert result["current_period"] == budget_period
    assert result["ownership_reporting_period"] == ownership_period
    assert result["agency_scope"]["ownership_reporting_period"] == ownership_period
    row = next(r for r in result["rows"] if r["fms_id"] == "RD-CURRENT")
    assert row["reporting_period"] == budget_period and row["total_budget"] == amount
    assert ownership_period in result["agency_scope"]["note"]
    delayed = rank_projects_from(con, "budget", "total_budget", agency=agency,
                                 population_scope="current", delayed_only=True)
    assert delayed["schedule_filter_reporting_period"] == ownership_period
    assert bool(delayed["rows"]) is (newer_complete_source == "budget")
