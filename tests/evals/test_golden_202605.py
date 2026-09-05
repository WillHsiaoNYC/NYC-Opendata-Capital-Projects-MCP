"""May 2026 snapshot headlines, checked against source rows and explicit scopes."""
import pytest

from od_cpd.coverage import schedule_coverage
from od_cpd.tools.budget import budget_breakdown_from
from od_cpd.tools.lifecycle import project_duration_stats_from
from od_cpd.tools.lookup import dataset_info_from
from od_cpd.tools.portfolio import project_portfolio_from
from od_cpd.tools.ranking import rank_projects_from
from tests.evals import snapshot_database


@pytest.fixture(scope="module")
def con():
    with snapshot_database("202605") as c:
        yield c


def test_current_budget_inventory_and_source_total(con):
    info = dataset_info_from(con)
    assert all(d["current_snapshot_period"] == "202605" for d in info["datasets"])
    result = budget_breakdown_from(con)
    assert result["period"] == "202605"
    assert sum(g["n"] for g in result["groups"]) == 5647
    total = sum(g["value"] for g in result["groups"])
    assert total == pytest.approx(160_336_058_990.19, abs=0.01, rel=0)
    source_total = con.execute("SELECT sum(CAST(total_budget AS DOUBLE)) FROM raw_budget_history "
                              "WHERE year_month_reported='202605' AND spend_to_date IS NOT NULL").fetchone()[0]
    assert total == pytest.approx(source_total, abs=0.01, rel=0)


def test_current_and_latest_known_schedule_populations(con):
    current = project_portfolio_from(con, population_scope="current", n=1)
    assert current["summary"]["n_projects"] == 3063
    assert current["summary"]["n_delayed_this_period"] == 369
    assert current["rows"][0]["reporting_period"] == "202605"
    assert current["rows"][0]["present_in_current_snapshot"] is True
    assert project_portfolio_from(con, n=1)["summary"]["n_projects"] == 3638
    assert con.execute("SELECT count(DISTINCT pid) FROM raw_project_detail "
                       "WHERE reporting_period='202605'").fetchone()[0] == 3063


def test_source_coverage_and_invalid_durations_remain_visible(con):
    coverage = schedule_coverage(con)
    assert (coverage["source_rows"], coverage["dashboard_rows"], coverage["matched_rows"]) == (22464, 26598, 21917)
    assert (coverage["source_only_rows"], coverage["pids_with_omitted_source_rows"], coverage["source_only_pids"]) == (547, 196, 30)
    duration = project_duration_stats_from(con)
    assert (duration["population_total"], duration["n_projects"], duration["excluded_missing_dates"],
            duration["excluded_invalid_order"]) == (3638, 1172, 2465, 1)
    assert duration["invalid_intervals"][0]["pid"] == "4612"
    assert duration["invalid_intervals"][0]["days"] == -48
    assert duration["stats"]["min_days"] == 67


def test_owner_and_category_rules_replay_at_current_snapshot(con):
    doc = rank_projects_from(con, "budget", "total_budget", n=1,
                             agency="DOC", population_scope="current")["rows"][0]
    assert doc["fms_id"] == "BBJ-Q"
    assert doc["sponsor_agencies"] == ["DEP", "DOC"]
    assert doc["total_budget"] == pytest.approx(4_474_638_690.85, abs=0.01, rel=0)
    assert con.execute("SELECT category FROM category_dim WHERE fms_id='EO26-0079' "
                       "AND managing_agency='DCAS'").fetchone() == ("Homeless Shelters",)
    library = project_portfolio_from(con, category="Library", lifecycle_status="in_progress",
                                     population_scope="current", n=3)
    assert library["summary"]["n_projects"] == 54
    assert [r["pid"] for r in library["rows"]] == ["3986", "5477", "4084"]
    assert library["summary"]["line_budget_total"] == pytest.approx(804_180_868.81, abs=0.01, rel=0)
