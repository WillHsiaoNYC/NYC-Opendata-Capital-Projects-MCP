# tests/test_lifecycle.py
import duckdb
import pytest
from od_cpd import schema, materialize
from od_cpd.tools.lifecycle import project_duration_stats_from


def test_duration_stats_requires_both_actuals():
    con = duckdb.connect(":memory:"); schema.apply_schema(con)
    con.executemany(
        "INSERT INTO raw_project_detail (reporting_period, managing_agency, pid, fms_id,"
        " total_budget, current_phase, actual_design_start, actual_construction_end)"
        " VALUES (?,?,?,?,?,?,?,?)",
        [["202601","DDC","201","Z","10","Close-out","2020-01-01","2023-01-01"],
         ["202601","DDC","202","Y","10","Construction","2021-01-01",None]],
    )
    materialize.materialize_all(con)
    r = project_duration_stats_from(con, from_milestone="actual_design_start",
                                    to_milestone="actual_construction_end")
    assert r["n_projects"] == 1                 # only PID 201 has both dates
    assert r["excluded_missing_dates"] == 1
    assert r["stats"]["mean_days"] > 1000


@pytest.mark.parametrize("group_by", [None, "managing_agency"])
def test_duration_quality_counts_reconcile_and_preserve_invalid_source_dates(group_by):
    con = duckdb.connect(":memory:")
    schema.apply_schema(con)
    con.executemany(
        "INSERT INTO raw_project_detail (reporting_period, managing_agency, pid, fms_id, "
        "actual_design_start, actual_construction_end) VALUES (?,?,?,?,?,?)",
        [
            ["202509", "DDC", "3", "C", "2023-01-01", "2023-08-15"],
            ["202601", "DDC", "1", "A", "2023-01-01", "2023-02-01"],
            ["202601", "DPR", "2", "B", "2023-01-01", "2023-01-01"],
            ["202601", "DDC", "3", "C", "2023-10-02", "2023-08-15"],
            ["202601", "DDC", "4", "D", "2023-01-01", None],
            ["202601", "DPR", "5", "E", None, None],
        ])
    materialize.materialize_all(con)
    result = project_duration_stats_from(con, group_by=group_by)
    assert result["population_total"] == 5
    assert result["n_projects"] == 2
    assert result["excluded_missing_dates"] == 2
    assert result["excluded_invalid_order"] == 1
    invalid = result["invalid_intervals"]
    assert len(invalid) == 1 and invalid[0]["pid"] == "3"
    assert invalid[0]["days"] == -48
    assert str(invalid[0]["from_date"]) == "2023-10-02"
    assert invalid[0]["quality_flag"] == "negative_forward_interval"
    assert "actual_construction_end is never suppressed" in result["note"]
    if group_by:
        for group in result["groups"]:
            assert group["population_total"] == (group["n_projects"] +
                group["excluded_missing_dates"] + group["excluded_invalid_order"])
        assert sum(g["excluded_invalid_order"] for g in result["groups"]) == 1
    else:
        assert result["stats"] == {"mean_days": 15.5, "median_days": 15.5,
                                  "min_days": 0, "max_days": 31}
    for sql in result["provenance"]["components"].values():
        con.execute(sql).fetchall()


@pytest.mark.parametrize("start, end", [
    ("actual_construction_end", "actual_design_start"),
    ("actual_design_start", "actual_design_start"),
])
def test_duration_rejects_reversed_and_same_milestones(start, end):
    assert "must precede" in project_duration_stats_from(None, start, end)["error"]
