# tests/test_lifecycle.py
import duckdb
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
